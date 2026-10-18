"""stage1_assign.py: Stage1 纯分配 —— 只决定「产品上哪条线」。

变量: `is_scheduled[(line, rid)]` 布尔。不建模线内排序（那是 Stage2 的事）。
约束: 容量(含估算换型)、R5 混线隔离、每产品≤1线、零切换组同线、产线均衡。
目标: base_bonus + priority + electricity + balance(SCALE)
      + 蛋白/粒径 beat_count 近似 + 换型 avg 近似 + 成对共置项 + 前置占用延迟。

⚠ 设计要点: 换型近似必须能感知「真实同线伙伴」（成对共置项
`is_sched(l,i) AND is_sched(l,j)`），只用可行集平均会拆散零切换物料组；
共置项需校准（当前 0.25x），全量会压过 balance。详见 CLAUDE.md。
"""
import constant
from ortools.sat.python import cp_model
from scheduler.context import ApsContext
from scheduler import capacity, preprocessing


def _build_feasible_rids(ctx: ApsContext, scheduled_rids, lines, line_remaining_time):
    line_feasible_rids = {}
    for l in lines:
        line_feasible_rids[l] = []
        for rid in scheduled_rids:
            t, _ = ctx.prod_time_matrix.get((rid, l), (constant.IS_UnScheduled, 10))
            if t != constant.IS_UnScheduled and t <= line_remaining_time[l]:
                line_feasible_rids[l].append(rid)
    return line_feasible_rids


def build_model(ctx: ApsContext, scheduled_rids, lines, line_remaining_time, changeover_time_penalty):
    """构建 Stage1 纯分配 CP-SAT 模型 (is_scheduled 布尔变量 + 分配级近似目标)。

    Stage1 只做产品-产线分配, 不建模线内排序:
    - 变量: is_scheduled[(l, rid)] 每产品每线一个布尔
    - 约束: 容量(Σprod_time ≤ available), R5混线隔离, 每产品≤1线
    - 目标: base_bonus + priority + electricity + balance
           + 蛋白/粒径/换型/cluster 的分配级近似值
    线内精确排序由 Stage2 (_refine_line_ordering) 负责。
    """
    model = cp_model.CpModel()

    line_feasible_rids = _build_feasible_rids(ctx, scheduled_rids, lines, line_remaining_time)
    active_lines = [l for l in lines if line_feasible_rids[l]]

    if not active_lines:
        ctx.logger.warning("没有产线有可行产品，无法进行排产")
        return None

    preprocessing.analyze_mixer_sharing(ctx, active_lines)

    objective_terms = []

    # ---- 归一化预计算 ----
    norm = None
    if constant.OBJECTIVE_NORMALIZE:
        real_rids = [r for r in scheduled_rids if r not in ctx.wait_nodes]
        N_norm = max(len(real_rids), 1)
        L_norm = len(active_lines)
        A_norm = max(N_norm - L_norm, 1)
        K_norm = constant.OBJECTIVE_SCALE_K
        PB_norm = max(changeover_time_penalty, 1)
        max_cap_norm = max(line_remaining_time[l] for l in active_lines)
        max_single_cost = 0
        for rid in real_rids:
            product = ctx.product_attrs.get(rid, {})
            qty = float(product.get('plannedProductionQuantity', 0))
            uec = float(product.get('unitElectricityConsumption', 0))
            if qty > 0 and uec > 0:
                for l in active_lines:
                    avg_price = ctx.line_avg_electricity_price.get(l, 0)
                    c = qty * uec * avg_price
                    if c > max_single_cost:
                        max_single_cost = c
        cost_bound_norm = max(int(max_single_cost * N_norm), 1)

        active_weights = sum(w for w in ctx.constraint_weights.values() if w > 0)
        min_base = K_norm * active_weights + 1
        if constant.base_scheduling_bonus < min_base:
            ctx.logger.warning(
                f"归一化模式: base_scheduling_bonus({constant.base_scheduling_bonus}) "
                f"< K×Σw({min_base-1}), 自动提升到 {min_base} 以保证排产正收益")
            norm_base_bonus = min_base
        else:
            norm_base_bonus = constant.base_scheduling_bonus
        norm = {
            'N': N_norm, 'A': A_norm, 'L': L_norm, 'K': K_norm,
            'PB': PB_norm, 'max_cap': max_cap_norm, 'cost_bound': cost_bound_norm,
            'base_bonus': norm_base_bonus,
        }
        ctx._norm = norm
        ctx.logger.info(f"Stage1 归一化: N={N_norm}, L={L_norm}, A={A_norm}, K={K_norm}, "
                        f"PB={PB_norm}, max_cap={max_cap_norm}, cost_bound={cost_bound_norm}, "
                        f"base_bonus={norm_base_bonus}")

    # ---- is_scheduled 布尔变量 ----
    is_scheduled = {}
    is_scheduled_anywhere = {}
    for rid in scheduled_rids:
        is_scheduled_anywhere[rid] = model.NewBoolVar(f'sched_{rid}')

    for l in active_lines:
        for rid in line_feasible_rids[l]:
            is_scheduled[(l, rid)] = model.NewBoolVar(f'sched_{l}_{rid}')

    # 每产品最多分配到1条线
    for rid in scheduled_rids:
        sched_vars = [is_scheduled[(l, rid)] for l in active_lines if (l, rid) in is_scheduled]
        if sched_vars:
            model.Add(sum(sched_vars) <= 1)
            model.Add(is_scheduled_anywhere[rid] == sum(sched_vars))
        if ctx.product_attrs.get(rid, {}).get('demoRequired'):
            model.Add(sum(sched_vars) == 1)

    # ---- 零切换物料组: 组同线 (拆开 = 多付边界换型; 换型 pri=1 不应为均衡让路) ----
    # y[g,l] = 组 g 用到线 l, 惩罚 Σy(用线数)。产能/包装放不下时自然拆成多线(最少拆分)。
    # 优先级语义: 该惩罚代表真实边界换型开销, 量级压过 balance 拉散的收益 (2026-09-29
    # 业务问题: balance=100 会把零切换对拆散; balance=0 时全部同线连续, 证明纯目标权衡)。
    _zg = getattr(ctx, 'zero_groups', {}) or {}
    if _zg and changeover_time_penalty > 0:
        _K = ctx._norm['K'] if constant.OBJECTIVE_NORMALIZE else 10000
        _split_penalty = 2 * max(
            changeover_time_penalty,
            ctx.constraint_weights.get('production_balance', 0)) * _K
        for _gid, _members in _zg.items():
            _feas = {rid: [l for l in active_lines if (l, rid) in is_scheduled]
                     for rid in _members}
            _used = sorted({l for fl in _feas.values() for l in fl})
            if len(_used) <= 1 or all(len(fl) <= 1 for fl in _feas.values()):
                continue
            _y = {}
            for l in _used:
                _y[l] = model.NewBoolVar(f'zg{_gid}_line_{l}')
            for _rid, _fl in _feas.items():
                for l in _fl:
                    model.AddImplication(is_scheduled[(l, _rid)], _y[l])
            objective_terms.append(sum(_y.values()) * (-_split_penalty))

    # ---- 容量约束 (含估算换型) ----
    # 每个产品除生产时间外, 还需一次切换开销(前置→首产品 或 产品→产品)。
    # 用该产品到该线其他可行产品的平均切换时间作为估算, 防止 Stage1 超配导致 Stage2 不可行。
    _avg_sw = {}
    for rid in scheduled_rids:
        sw_times = [ctx.switch_time_matrix.get((rid, o))
                    for o in scheduled_rids if o != rid]
        sw_times = [s for s in sw_times if s is not None and s != constant.IS_UnScheduled]
        _avg_sw[rid] = int(sum(sw_times) / len(sw_times)) if sw_times else 0

    for l in active_lines:
        pre_order_h = float(ctx.line_attrs[l].get('preOrderProduceTime', 0) or 0)
        pre_offset = int(pre_order_h * 60) if pre_order_h > 0 else 0
        max_time = line_remaining_time[l]
        load_terms = []
        for rid in line_feasible_rids[l]:
            pt, _ = ctx.prod_time_matrix.get((rid, l), (0, 10))
            load_terms.append(is_scheduled[(l, rid)] * (pt + _avg_sw.get(rid, 0)))
        if load_terms:
            model.Add(pre_offset + sum(load_terms) <= max_time)

    # ---- 目标函数: 分配级项 ----
    elec_weight = ctx.constraint_weights.get('electricity_cost', 0)
    base_bonus = ctx._norm['base_bonus'] if constant.OBJECTIVE_NORMALIZE else constant.base_scheduling_bonus

    for l in active_lines:
        avg_price = ctx.line_avg_electricity_price.get(l, 0)
        for rid in line_feasible_rids[l]:
            is_sched = is_scheduled[(l, rid)]

            # base_bonus
            objective_terms.append(is_sched * base_bonus)

            # priority_score
            priority_score = capacity.get_product_priority(ctx, rid, l)
            if constant.OBJECTIVE_NORMALIZE:
                priority_score_n = ctx._norm['K'] * priority_score // max(constant.OBJECTIVE_PRIORITY_MAX_SCORE * ctx._norm['L'], 1)
            else:
                priority_score_n = priority_score // max(len(line_feasible_rids[l]), 1)
            objective_terms.append(is_sched * priority_score_n)

            # electricity_cost
            product = ctx.product_attrs.get(rid, {})
            qty = float(product.get('plannedProductionQuantity', 0))
            uec = float(product.get('unitElectricityConsumption', 0))
            if elec_weight > 0 and qty > 0 and uec > 0 and avg_price > 0:
                cost_int = int(qty * uec * avg_price)
                if constant.OBJECTIVE_NORMALIZE:
                    elec_penalty = cost_int * elec_weight * ctx._norm['K'] // max(ctx._norm['cost_bound'], 1)
                else:
                    elec_penalty = cost_int * elec_weight
                objective_terms.append(is_sched * (-elec_penalty))

    # ---- 分配级近似: 蛋白/粒径/换型/cluster ----
    _add_assignment_approximations(ctx, model, active_lines, line_feasible_rids, is_scheduled,
                                   objective_terms, changeover_time_penalty)

    # ---- 均衡约束 ----
    _add_balance_constraints(ctx, model, active_lines, line_feasible_rids, is_scheduled,
                            scheduled_rids, line_remaining_time, objective_terms)

    # ---- R5 混线隔离 (纯 is_scheduled 约束) ----
    _add_r5_mixer_isolation(ctx, model, active_lines, line_feasible_rids, is_scheduled)

    # ---- 方案C: 前置占用延迟成本 (跨线 PR0014 对分配的影响) ----
    _add_preorder_delay_cost(ctx, active_lines, line_feasible_rids, is_scheduled,
                             objective_terms)

    # ---- DecisionStrategy ----
    real_rids = [r for r in scheduled_rids if r not in ctx.wait_nodes]
    def _priority_sort_key(rid):
        attrs = ctx.product_attrs.get(rid, {})
        mp = attrs.get('materialPriority', '99')
        try:
            return int(mp)
        except (ValueError, TypeError):
            return 99
    real_rids.sort(key=_priority_sort_key)

    ds_vars = [is_scheduled_anywhere[rid] for rid in real_rids]
    if ds_vars:
        model.AddDecisionStrategy(
            ds_vars,
            cp_model.CHOOSE_FIRST,
            cp_model.SELECT_MAX_VALUE)
        ctx.logger.info(f"Stage1 DecisionStrategy: {len(ds_vars)} 个产品")

    model.Maximize(sum(objective_terms))

    proto = model.Proto()
    ctx.logger.info(f"Stage1 模型: {len(proto.variables)} 变量, {len(proto.constraints)} 约束, "
                    f"{len(active_lines)} 活跃产线, {len(scheduled_rids)} 待排产品")
    for l in active_lines:
        ctx.logger.info(f"  产线 {l} ({ctx.line_attrs[l]['productionLineName']}): "
                        f"{len(line_feasible_rids[l])} 可行产品, 可用 {line_remaining_time[l]} 分钟")

    return {
        'model': model,
        'active_lines': active_lines,
        'line_feasible_rids': line_feasible_rids,
        'rid_to_id': {rid: i + 1 for i, rid in enumerate(scheduled_rids)},
        'id_to_rid': {i + 1: rid for i, rid in enumerate(scheduled_rids)},
        'is_scheduled': is_scheduled,
    }


def _add_assignment_approximations(ctx, model, active_lines, line_feasible_rids, is_scheduled,
                                   objective_terms, changeover_time_penalty):
    """分配级近似目标: 蛋白/粒径/换型/共置 (全部 line-aware)。

    关键: 近似值必须依赖产线 l, 否则对同一产品在所有产线上是常数,
    对分配决策贡献为 0 (Stage1 会完全忽略该规则)。

    - 蛋白/粒径: beat_count = 该产品在该线能"打败"多少个同线产品
    - 换型: avg_switch_to_line = 该产品到该线其他可行产品的平均换型时间
    - 共置: 成对项 (i,j 同排线 l 才得分)。单品级近似只看可行集平均,
      与真实同线伙伴无关, 会把零切换物料组拆散 (1500-0729: 小猪851 组被拆到
      CX05/CX06、A6 组被拆到 CX03/CX04, 换型 +35%)。共置项让"整组同线"的
      收益随 C(k,2) 增长, 是 Stage1 唯一能感知真实组内换型的信号。
    """
    K = ctx._norm['K'] if constant.OBJECTIVE_NORMALIZE else 10000
    A = ctx._norm['A'] if constant.OBJECTIVE_NORMALIZE else 1
    PB = max(changeover_time_penalty, 1)

    protein_w = ctx.constraint_weights.get('protein_grade', 0)
    particle_w = ctx.constraint_weights.get('particle_size', 0)
    changeover_w = ctx.constraint_weights.get('changeover_time', 0)

    for l in active_lines:
        feasible = line_feasible_rids[l]
        N_line = len(feasible)
        if N_line < 2:
            continue

        # line-aware 平均换型: 该产品 <-> 该线其他可行产品
        avg_switch_to_line = {}
        for rid in feasible:
            sw_times = []
            for other in feasible:
                if other == rid:
                    continue
                sw = ctx.switch_time_matrix.get((rid, other))
                if sw is not None and sw != constant.IS_UnScheduled:
                    sw_times.append(sw)
            # 缺失换型数据的按 PB(最差) 计, 避免"无数据=零成本"的假象
            avg_switch_to_line[rid] = int(sum(sw_times) / len(sw_times)) if sw_times else PB

        for rid in feasible:
            is_sched = is_scheduled[(l, rid)]
            u_pgl = float(ctx.product_attrs[rid].get('proteinGradeLevel', 0) or 0)
            u_dia = float(ctx.product_attrs[rid].get('dia', 0) or 0)

            # 蛋白: beat_count = 该产品在该线能"打败"多少个同线产品
            if protein_w > 0 and u_pgl > 0:
                beat_count = sum(1 for j in feasible if j != rid
                                and u_pgl >= float(ctx.product_attrs[j].get('proteinGradeLevel', 0) or 0))
                est_protein = beat_count * protein_w * K // max(A * (N_line - 1), 1)
                objective_terms.append(is_sched * est_protein)

            # 粒径: beat_count = 该产品在该线能"打败"多少个同线产品 (升序)
            if particle_w > 0 and u_dia > 0:
                beat_count = sum(1 for j in feasible if j != rid
                                and u_dia <= float(ctx.product_attrs[j].get('dia', 0) or 0))
                est_particle = beat_count * particle_w * K // max(A * (N_line - 1), 1)
                objective_terms.append(is_sched * est_particle)

            # 换型: line-aware 平均换型 (排到该线大约产生多少换型开销)
            if changeover_w > 0:
                avg_sw = avg_switch_to_line.get(rid, PB)
                est_changeover = (PB - avg_sw) * changeover_w * K // max(A * PB, 1)
                objective_terms.append(is_sched * est_changeover)

    # 共置奖励: 成对项 both = is_sched(l,i) AND is_sched(l,j), 只有真同线才得分。
    # 对互切低的对给高奖励 (零切换物料组整组同线收益 ~ C(k,2)),
    # 互切高的对奖励趋 0 (不罚: 可行性逼迫同线时不制造噪声)。
    if changeover_w > 0:
        feasible_sets = {l: set(line_feasible_rids[l]) for l in active_lines}
        rid_list = sorted({r for l in active_lines for r in line_feasible_rids[l]})
        pair_scale = changeover_w * K // max(A, 1)
        for i_idx in range(len(rid_list)):
            rid_i = rid_list[i_idx]
            for j_idx in range(i_idx + 1, len(rid_list)):
                rid_j = rid_list[j_idx]
                shared = [l for l in active_lines
                          if rid_i in feasible_sets[l] and rid_j in feasible_sets[l]]
                if not shared:
                    continue
                s = PB
                for v in (ctx.switch_time_matrix.get((rid_i, rid_j)),
                          ctx.switch_time_matrix.get((rid_j, rid_i))):
                    if v is not None and v != constant.IS_UnScheduled:
                        s = min(s, v)
                # 0.25x 校准: 全量共置收益会压过 balance (1170-1 CV 21->59);
                # 0.25x 下换型/均衡/蛋白全面持平或更优 (16 case 3 轮平均)。
                bonus = (PB - s) * pair_scale * 25 // max(PB * 100, 1)
                if bonus <= 0:
                    continue
                for l in shared:
                    both = model.NewBoolVar(f'pair_both_{l}_{rid_i}_{rid_j}')
                    model.AddMultiplicationEquality(
                        both, [is_scheduled[(l, rid_i)], is_scheduled[(l, rid_j)]])
                    objective_terms.append(both * bonus)


def _add_r5_mixer_isolation(ctx, model, active_lines, line_feasible_rids, is_scheduled):
    """R5: 有前置占用的产线, 其自身类型 T 的新产品不得再排到该线。"""
    if not ctx.shared_mixer_line_pairs:
        return
    for l in active_lines:
        pre_h = float(ctx.line_attrs[l].get('preOrderProduceTime', 0) or 0)
        pre_type = ctx.line_attrs[l].get('specialProcessType', '') or ''
        if pre_h <= 0 or not pre_type:
            continue
        for rid in line_feasible_rids.get(l, []):
            if preprocessing.get_effective_special_type(ctx.product_attrs[rid]) == pre_type:
                model.Add(is_scheduled[(l, rid)] == 0)
                ctx.logger.info(f"  R5: line {l} (pre {pre_type} occupied) forbids same-type product {rid}")


def _add_balance_constraints(ctx, model, active_lines, line_feasible_rids, is_scheduled,
                             scheduled_rids, line_remaining_time, objective_terms):
    """产线均衡：min/max-load 软项，load = 前置时长 + Σ(生产时间 + 估算换型时间)。

    改进: load 不再只看 prod_time, 还包含:
    1. preOrderProduceTime — 前置订单已占用的生产时长(分钟), 每线一个常量偏移
    2. 每产品的估算换型时间 — 取 switch_time_matrix 中该产品与所有其他已排产品
       的平均切换时间, 作为"排到该线后大约会产生的换型开销"的代理值
    这样均衡目标更贴近业务感知的 total_time = 前置 + 生产 + 换型。
    """
    if not (ctx.constraint_weights.get('production_balance', 0) > 0 and len(active_lines) > 1):
        return

    balance_weight = ctx.constraint_weights['production_balance']
    N_products = len(scheduled_rids)
    L_lines = len(active_lines)
    pr0010_priority = 1
    for s in (ctx.scheduling_strategy or []):
        if s.get('ruleCode') == 'PR0010':
            pr0010_priority = int(s.get('rulePriority', 1))
            break
    balance_scale = max(1, N_products // max(L_lines * pr0010_priority, 1))

    # 预计算每个产品的平均换型时间(跨所有其他已排产品)
    avg_switch_per_product = {}
    for rid in scheduled_rids:
        sw_times = []
        for other in scheduled_rids:
            if other == rid:
                continue
            sw = ctx.switch_time_matrix.get((rid, other))
            if sw is not None and sw != constant.IS_UnScheduled:
                sw_times.append(sw)
        avg_switch_per_product[rid] = int(sum(sw_times) / len(sw_times)) if sw_times else 0

    max_line_cap = max(line_remaining_time[l] for l in active_lines)
    min_load = model.NewIntVar(0, max_line_cap, 'min_line_load')
    max_load = model.NewIntVar(0, max_line_cap, 'max_line_load')
    line_load_vars = []
    for l in active_lines:
        # 前置订单已占用时长(分钟)
        pre_order_h = float(ctx.line_attrs[l].get('preOrderProduceTime', 0) or 0)
        pre_offset = int(pre_order_h * 60)

        load_terms = []
        for rid in line_feasible_rids[l]:
            pt, _ = ctx.prod_time_matrix.get((rid, l), (0, 10))
            avg_sw = avg_switch_per_product.get(rid, 0)
            load_terms.append(is_scheduled[(l, rid)] * (pt + avg_sw))

        ll = model.NewIntVar(0, max_line_cap, f'line_load_{l}')
        if load_terms:
            model.Add(ll == pre_offset + sum(load_terms))
        else:
            model.Add(ll == pre_offset)
        line_load_vars.append(ll)
        model.Add(min_load <= ll)
        model.Add(max_load >= ll)
    model.AddMinEquality(min_load, line_load_vars)
    model.AddMaxEquality(max_load, line_load_vars)
    balance_diff = model.NewIntVar(-max_line_cap, 0, 'balance_diff')
    model.Add(balance_diff == min_load - max_load)

    if pre_offset > 0:
        pre_items = []
        for l2 in active_lines:
            h = float(ctx.line_attrs[l2].get('preOrderProduceTime', 0) or 0)
            if h > 0:
                pre_items.append(f'{l2}={int(h*60)}min')
        ctx.logger.info(f"均衡 load 含前置时长: {pre_items}")
    ctx.logger.info(f"均衡 SCALE={balance_scale} (N={N_products}, L={L_lines}, PR0010 priority={pr0010_priority}), "
                    f"load = 前置 + Σ(prod_time + avg_switch_time)")

    if constant.OBJECTIVE_NORMALIZE:
        balance_coeff = balance_weight * ctx._norm['K'] // max(max_line_cap, 1)
        objective_terms.append(balance_diff * balance_coeff)
    else:
        objective_terms.append(balance_diff * balance_weight * balance_scale)


def _add_preorder_delay_cost(ctx, active_lines, line_feasible_rids, is_scheduled,
                             objective_terms):
    """方案C: 前置占用延迟成本反馈给 Stage1 分配。

    跨线前置隔离使 dst 线上的异类型产品被迫等 pre_end 才能开工。Stage1 是纯分配
    模型无时间概念, 不知道这个延迟, 可能做出次优分配。这里把延迟成本折算成
    分配级惩罚, 让 Stage1 感知"排到被前置占用的产线要付等待代价"。

    成本 ∝ min(pre_end, 产品预计生产时长) —— 延迟不可能超过一个产品自身时长,
    用产品时长做上界避免大 pre_end 把整条线的分配全压垮。
    """
    pairs = getattr(ctx, 'shared_mixer_line_pairs', []) or []
    if not pairs:
        return
    K = ctx._norm['K'] if constant.OBJECTIVE_NORMALIZE else 10000
    max_cap = ctx._norm.get('max_cap', 1500) if constant.OBJECTIVE_NORMALIZE else 1500
    base_bonus = ctx._norm['base_bonus'] if constant.OBJECTIVE_NORMALIZE else constant.base_scheduling_bonus

    # 延迟成本上界: base_bonus 的 1/4 —— 足以影响同质产品间的分配选择,
    # 但不压过"多排一个产品"的正收益
    max_penalty = base_bonus // 4

    added = 0
    for line_a, line_b in pairs:
        for src_line, dst_line in [(line_a, line_b), (line_b, line_a)]:
            pre_type = ctx.line_attrs[src_line].get('specialProcessType', '') or ''
            if not pre_type:
                continue
            pre_order_h = float(ctx.line_attrs[src_line].get('preOrderProduceTime', 0) or 0)
            if pre_order_h <= 0:
                continue
            pre_end = int(pre_order_h * 60)
            if pre_end <= 0:
                continue
            mwt = int(float(ctx.line_attrs[src_line].get('mixerWaitTime', 0) or 0) * 60)
            gap = pre_end + mwt

            for rid in line_feasible_rids.get(dst_line, []):
                dst_type = ctx.product_attrs[rid].get('specialProcessType', '') or ''
                if not dst_type or dst_type == pre_type:
                    continue
                pt, _ = ctx.prod_time_matrix.get((rid, dst_line), (0, 10))
                if pt <= 0:
                    continue
                delay = min(gap, pt)
                penalty = min(max_penalty, delay * max_penalty // max(gap, 1))
                if penalty > 0 and (dst_line, rid) in is_scheduled:
                    objective_terms.append(is_scheduled[(dst_line, rid)] * (-penalty))
                    added += 1
    if added:
        ctx.logger.info(f"Stage1 前置延迟成本: {added} 条分配惩罚 (max={max_penalty})")
