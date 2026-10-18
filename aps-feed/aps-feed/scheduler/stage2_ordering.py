"""stage2_ordering.py: Stage2 组件化排序 —— 决定「线内怎么排」。

按混合机连通分量分组；分量内联合 CP-SAT（逐线 Circuit + 时间变量），
产出精确线内顺序与实际时刻。目标: 蛋白降序 + 粒径升序 + 换型 + 时间窗 + 谷电，
跨线 PR0014 析取/隔离。

无共享混合机的线退化为逐线独立求解。
"""
import constant
from ortools.sat.python import cp_model
from scheduler.context import ApsContext
from scheduler.zero_groups import postprocess_zero_groups


def _build_mixer_components(ctx: ApsContext, lines):
    """按共享混合机把产线分组为连通分量。分量内需联合求解以正确建模跨线隔离。"""
    parent = {l: l for l in lines}

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for a, b in getattr(ctx, 'shared_mixer_line_pairs', []) or []:
        if a in parent and b in parent:
            ra, rb = find(a), find(b)
            if ra != rb:
                parent[rb] = ra

    comps = {}
    for l in lines:
        comps.setdefault(find(l), []).append(l)
    return list(comps.values())


def _add_line_circuit(ctx, model, lid, real_rids, wait_rids, max_time, pre_rid, pre_end,
                      PB, K, protein_w, particle_w, changeover_w, tw_w, elec_w,
                      objective_terms):
    """在共享 model 中添加一条产线的 Circuit + 时间变量 + 目标项。

    返回 (rid_to_nid, nid_to_rid, arc_lit, start_v, end_v), 供跨线约束和解抽取使用。
    """
    all_nodes = real_rids + wait_rids
    N = len(all_nodes)
    A = max(N, 1)
    end_node = N + 1

    norm_protein = protein_w * K // max(A, 1) if protein_w > 0 else 0
    norm_particle = particle_w * K // max(A, 1) if particle_w > 0 else 0
    norm_changeover = changeover_w * K // max(A * PB, 1) if changeover_w > 0 else 0

    rid_to_nid = {rid: i + 1 for i, rid in enumerate(all_nodes)}
    nid_to_rid = {v: k for k, v in rid_to_nid.items()}
    arcs = []
    arc_lit = {}
    start_v = {}
    end_v = {}

    start_v[0] = model.NewIntVar(0, 0, f's2_{lid}_start_0')
    end_v[0] = model.NewIntVar(pre_end, pre_end, f's2_{lid}_end_0')

    wait_stage = {}
    for rid in wait_rids:
        wait_stage[rid_to_nid[rid]] = model.NewIntVar(1, 4, f's2_{lid}_wstage_{rid}')

    for rid in all_nodes:
        u = rid_to_nid[rid]
        start_v[u] = model.NewIntVar(0, max_time, f's2_{lid}_start_{u}')
        end_v[u] = model.NewIntVar(0, max_time, f's2_{lid}_end_{u}')
        placement = ctx.product_attrs.get(rid, {}).get('demoPlacement', {})
        if 'start_minute' in placement:
            model.Add(start_v[u] == placement['start_minute'])
        if 'earliest' in placement:
            model.Add(start_v[u] >= placement['earliest'])
        if 'latest' in placement:
            model.Add(end_v[u] <= placement['latest'])
    start_v[end_node] = model.NewIntVar(0, max_time, f's2_{lid}_start_E')
    end_v[end_node] = model.NewIntVar(0, max_time, f's2_{lid}_end_E')

    # 前置 → 产品
    for rid in all_nodes:
        u = rid_to_nid[rid]
        if rid in ctx.wait_nodes:
            st = 0
        else:
            st = ctx.switch_time_matrix.get((pre_rid, rid), constant.IS_UnScheduled)
            if st == constant.IS_UnScheduled:
                st = PB  # 无换型数据按最大换型计, 保证弧存在(circuit 必须连通)
        lit = model.NewBoolVar(f's2_{lid}_arc_0_{u}')
        arcs.append([0, u, lit])
        arc_lit[(0, u)] = lit
        model.Add(start_v[u] >= end_v[0] + st).OnlyEnforceIf(lit)

    # 产品 → 产品
    for rid_u in all_nodes:
        u = rid_to_nid[rid_u]
        u_is_wait = rid_u in ctx.wait_nodes
        for rid_v in all_nodes:
            v = rid_to_nid[rid_v]
            if u == v:
                continue
            v_is_wait = rid_v in ctx.wait_nodes
            if u_is_wait and v_is_wait:
                continue
            if u_is_wait or v_is_wait:
                st = 0
            else:
                st = ctx.switch_time_matrix.get((rid_u, rid_v), constant.IS_UnScheduled)
                if st == constant.IS_UnScheduled:
                    st = PB  # 无换型数据按最大换型计, 保证弧存在
            lit = model.NewBoolVar(f's2_{lid}_arc_{u}_{v}')
            arcs.append([u, v, lit])
            arc_lit[(u, v)] = lit
            model.Add(start_v[v] >= end_v[u] + st).OnlyEnforceIf(lit)

            # stage 排序: 加色/加药阶段不可回退
            if u_is_wait and v_is_wait:
                model.Add(wait_stage[u] <= wait_stage[v]).OnlyEnforceIf(lit)
            elif u_is_wait:
                stage_v = ctx.product_attrs[rid_v].get('stage', 1)
                model.Add(wait_stage[u] <= stage_v).OnlyEnforceIf(lit)
            elif v_is_wait:
                stage_u = ctx.product_attrs[rid_u].get('stage', 1)
                model.Add(stage_u <= wait_stage[v]).OnlyEnforceIf(lit)
            else:
                stage_u = ctx.product_attrs[rid_u].get('stage', 1)
                stage_v = ctx.product_attrs[rid_v].get('stage', 1)
                model.Add(stage_u <= stage_v).OnlyEnforceIf(lit)

    # 产品 → END
    for rid in all_nodes:
        u = rid_to_nid[rid]
        lit = model.NewBoolVar(f's2_{lid}_arc_{u}_E')
        arcs.append([u, end_node, lit])
        arc_lit[(u, end_node)] = lit
        model.Add(start_v[end_node] >= end_v[u]).OnlyEnforceIf(lit)

    arcs.append([end_node, 0, model.NewConstant(1)])
    model.AddCircuit(arcs)

    # 生产时间跨度
    for rid in all_nodes:
        u = rid_to_nid[rid]
        if rid in ctx.wait_nodes:
            pt = ctx.wait_nodes[rid]['production_time']
        else:
            pt, _ = ctx.prod_time_matrix.get((rid, lid), (0, 10))
        model.Add(end_v[u] == start_v[u] + pt)

    # ---- 线内目标: 蛋白降序 + 粒径升序 + 换型 + 时间窗 + 谷电 ----
    for rid_u in all_nodes:
        u = rid_to_nid[rid_u]
        if rid_u in ctx.wait_nodes:
            continue
        u_pgl = float(ctx.product_attrs[rid_u].get('proteinGradeLevel', 0) or 0)
        u_dia = float(ctx.product_attrs[rid_u].get('dia', 0) or 0)

        if (0, u) in arc_lit:
            lit = arc_lit[(0, u)]
            pre_pgl = float(ctx.product_attrs.get(pre_rid, {}).get('proteinGradeLevel', 0) or 0)
            pre_dia = float(ctx.product_attrs.get(pre_rid, {}).get('dia', 0) or 0)
            st_pre = ctx.switch_time_matrix.get((pre_rid, rid_u), 0)
            if norm_protein and pre_pgl >= u_pgl > 0:
                objective_terms.append(lit * norm_protein)
            if norm_particle and pre_dia <= u_dia > 0:
                objective_terms.append(lit * norm_particle)
            if norm_changeover:
                objective_terms.append(lit * (PB - st_pre) * norm_changeover)

        for rid_v in all_nodes:
            v = rid_to_nid[rid_v]
            if u == v or rid_v in ctx.wait_nodes:
                continue
            if (u, v) not in arc_lit:
                continue
            v_pgl = float(ctx.product_attrs[rid_v].get('proteinGradeLevel', 0) or 0)
            v_dia = float(ctx.product_attrs[rid_v].get('dia', 0) or 0)
            st = ctx.switch_time_matrix.get((rid_u, rid_v), 0)
            lit = arc_lit[(u, v)]
            if norm_protein and u_pgl >= v_pgl > 0:
                objective_terms.append(lit * norm_protein)
            if norm_particle and u_dia <= v_dia > 0:
                objective_terms.append(lit * norm_particle)
            if norm_changeover:
                objective_terms.append(lit * (PB - st) * norm_changeover)

    if tw_w > 0:
        for rid in real_rids:
            u = rid_to_nid[rid]
            tw = ctx.product_attrs[rid].get('time_window')
            if not tw:
                continue
            earliest = int(tw['earliest'] * 60)
            latest = int(tw['latest'] * 60)
            in_tw = model.NewBoolVar(f's2_{lid}_intw_{u}')
            model.Add(start_v[u] >= earliest).OnlyEnforceIf(in_tw)
            model.Add(end_v[u] <= latest).OnlyEnforceIf(in_tw)
            objective_terms.append(in_tw * (tw_w * K // max(A, 1)))

    if elec_w > 0:
        segments = ctx.line_electricity_prices.get(lid, [])
        if segments:
            min_price = min(p for _, _, p in segments)
            valley = [(s, e) for s, e, p in segments if p == min_price]
            if valley:
                v_start = min(s for s, _ in valley)
                v_end = max(e for _, e in valley)
                if v_end > v_start:
                    for rid in real_rids:
                        u = rid_to_nid[rid]
                        pt, _ = ctx.prod_time_matrix.get((rid, lid), (0, 10))
                        max_ov = min(pt, v_end - v_start)
                        if max_ov <= 0:
                            continue
                        ov = model.NewIntVar(0, max_ov, f's2_{lid}_valley_{u}')
                        model.Add(ov <= start_v[u] - v_start + pt)
                        model.Add(ov <= v_end - start_v[u] + pt)
                        qty = float(ctx.product_attrs[rid].get('plannedProductionQuantity', 0))
                        uec = float(ctx.product_attrs[rid].get('unitElectricityConsumption', 0))
                        if qty > 0 and uec > 0:
                            cb = ctx._norm.get('cost_bound', 1) if constant.OBJECTIVE_NORMALIZE else 1
                            bonus = int(uec * qty * elec_w) * K // max(cb * max_ov, 1)
                            if bonus > 0:
                                objective_terms.append(ov * bonus)

    return rid_to_nid, nid_to_rid, arc_lit, start_v, end_v


def _add_cross_line_pr0014(ctx: ApsContext, model, comp_lines, line_assignment, per_line, gap_min, objective_terms=None):
    """跨线 PR0014: 共享混合机的产线不能同时生产不同类型的特殊物料。

    - 析取约束: end(i)+gap ≤ start(j) OR end(j)+gap ≤ start(i)
    - 前置隔离: src 线前置订单占用混合机时, dst 线异类型产品须等 pre_end+gap
    gap_min = mixerWaitTime(分钟)。语义: 特殊品种/加药物料生产时, 同混合机其他产线的等待时间。
    """
    from scheduler import preprocessing as _pp
    if objective_terms is None:
        objective_terms = []
    PR0014_SOFT_PENALTY = 500000  # 前置隔离软约束惩罚(高,但不致命)
    pruned = 0
    pairs = getattr(ctx, 'shared_mixer_line_pairs', []) or []
    in_comp = set(comp_lines)

    for line_a, line_b in pairs:
        if line_a not in in_comp or line_b not in in_comp:
            continue
        a_info = per_line[line_a]
        b_info = per_line[line_b]

        # --- 产品-产品析取 ---
        for rid_i in line_assignment.get(line_a, []):
            if rid_i in ctx.wait_nodes:
                continue
            type_i = _pp.get_effective_special_type(ctx.product_attrs[rid_i])
            if not type_i:
                continue
            u = a_info['rid_to_nid'].get(rid_i)
            if u is None:
                continue
            for rid_j in line_assignment.get(line_b, []):
                if rid_j in ctx.wait_nodes:
                    continue
                type_j = _pp.get_effective_special_type(ctx.product_attrs[rid_j])
                if not type_j or type_j == type_i:
                    continue
                v = b_info['rid_to_nid'].get(rid_j)
                if v is None:
                    continue
                i_before_j = model.NewBoolVar(f'p14_{line_a}_{rid_i}_{rid_j}')
                model.Add(a_info['end_v'][u] + gap_min <= b_info['start_v'][v]).OnlyEnforceIf(i_before_j)
                model.Add(b_info['end_v'][v] + gap_min <= a_info['start_v'][u]).OnlyEnforceIf(i_before_j.Not())
                pruned += 1
                # 析取硬约束会因时间冲突导致 INFEASIBLE; 记录到 objective 以最大化满足数
                # (即使模型可行, 析取也只有0/1, 此处额外加分鼓励选中的方向与目标一致)

        # --- 前置订单隔离 (双向) ---
        for src_line, dst_line in [(line_a, line_b), (line_b, line_a)]:
            pre_type = ctx.line_attrs[src_line].get('specialProcessType', '') or ''
            if not pre_type:
                continue
            src_info = per_line[src_line]
            pre_end_val = src_info['pre_end'] + gap_min
            dst_info = per_line[dst_line]
            for rid_j in line_assignment.get(dst_line, []):
                if rid_j in ctx.wait_nodes:
                    continue
                dst_type = _pp.get_effective_special_type(ctx.product_attrs[rid_j])
                if not dst_type or dst_type == pre_type:
                    continue
                v = dst_info['rid_to_nid'].get(rid_j)
                if v is None:
                    continue
                # 软约束: 满足前置隔离则加分, 避免硬约束导致 INFEASIBLE
                iso_ok = model.NewBoolVar(f'p14iso_{src_line}_{dst_line}_{rid_j}')
                model.Add(dst_info['start_v'][v] >= pre_end_val).OnlyEnforceIf(iso_ok)
                model.Add(dst_info['start_v'][v] < pre_end_val).OnlyEnforceIf(iso_ok.Not())
                objective_terms.append(iso_ok * PR0014_SOFT_PENALTY)
                pruned += 1

    return pruned


def refine_line_ordering(ctx: ApsContext, line_assignment, line_remaining_time, changeover_time_penalty, scheduled_set):
    """Stage2: 逐混合机分量求解最优线内排序 + 跨线隔离, 构建完整结果。

    分量 = 共享混合机的连通产线组。分量内联合求解以正确建模跨线 PR0014 隔离;
    分量间无耦合, 独立求解。无共享混合机时退化为逐线求解。
    """
    from scheduler import result_builder

    PB = max(changeover_time_penalty, 1)
    K = constant.OBJECTIVE_SCALE_K
    protein_w = ctx.constraint_weights.get("protein_grade", 0)
    particle_w = ctx.constraint_weights.get("particle_size", 0)
    changeover_w = ctx.constraint_weights.get("changeover_time", 0)
    tw_w = ctx.constraint_weights.get("time_window", 0)
    elec_w = ctx.constraint_weights.get("electricity_cost", 0)

    comp_lines = _build_mixer_components(ctx, list(line_assignment.keys()))
    # mixerWaitTime: 特殊品种/加药物料生产时, 同混合机其他产线的等待时间(小时→分钟)
    gap_min = 0
    for l in line_assignment:
        mwt = float(ctx.line_attrs[l].get('mixerWaitTime', 0) or 0)
        gap_min = max(gap_min, int(mwt * 60))

    line_sequences = {}
    line_times = {}
    refined_count = 0

    for comp in comp_lines:
        comp_rids = {l: line_assignment[l] for l in comp if line_assignment.get(l)}
        if not comp_rids:
            continue

        # 单产品/无产品的线直接跳过求解
        solvable = {}
        for lid, rids in comp_rids.items():
            real = [r for r in rids if r not in ctx.wait_nodes]
            waits = [r for r in ctx.wait_nodes if ctx.wait_nodes[r]['line_id'] == lid]
            if real:
                solvable[lid] = (real, waits)
        if not solvable:
            continue

        max_time_map = {}
        pre_end_map = {}
        pre_rid_map = {}
        for lid in solvable:
            max_time_map[lid] = line_remaining_time[lid]
            pre_rid = ctx.line_attrs[lid].get('recordId', '')
            pre_rid_map[lid] = pre_rid
            pre_order_h = float(ctx.line_attrs[lid].get('preOrderProduceTime', 0) or 0)
            if pre_order_h > 0:
                pre_end_map[lid] = int(pre_order_h * 60)
            else:
                pre_end_map[lid], _ = ctx.prod_time_matrix.get((pre_rid, lid), (0, 10))

        model = cp_model.CpModel()
        objective_terms = []
        per_line = {}

        for lid, (real_rids, wait_rids) in solvable.items():
            r2n, n2r, arc_lit, start_v, end_v = _add_line_circuit(
                ctx, model, lid, real_rids, wait_rids, max_time_map[lid],
                pre_rid_map[lid], pre_end_map[lid],
                PB, K, protein_w, particle_w, changeover_w, tw_w, elec_w,
                objective_terms)
            per_line[lid] = {'rid_to_nid': r2n, 'nid_to_rid': n2r, 'arc_lit': arc_lit,
                             'start_v': start_v, 'end_v': end_v, 'pre_end': pre_end_map[lid]}

        # 跨线隔离 (仅多产线分量有意义)
        if len(solvable) > 1:
            pruned = _add_cross_line_pr0014(ctx, model, list(solvable.keys()),
                                            {l: solvable[l][0] for l in solvable},
                                            per_line, gap_min, objective_terms)
            if pruned:
                ctx.logger.info(f"Stage2 分量 {[l for l in solvable]}: 跨线 PR0014 隔离 {pruned} 条约束")

        if objective_terms:
            model.Maximize(sum(objective_terms))

        solver = cp_model.CpSolver()
        solver.parameters.max_time_in_seconds = 8.0 if len(solvable) > 1 else 5.0
        solver.parameters.num_workers = 1
        solver.parameters.log_search_progress = False
        status = solver.Solve(model)

        if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
            ctx.logger.warning(f"Stage2 分量 {[l for l in solvable]}: 求解失败, 用默认顺序")
            for lid, (real_rids, _) in solvable.items():
                line_sequences[lid] = real_rids
            continue

        for lid, (real_rids, wait_rids) in solvable.items():
            info = per_line[lid]
            rid_to_nid, nid_to_rid = info['rid_to_nid'], info['nid_to_rid']
            arc_lit, start_v, end_v = info['arc_lit'], info['start_v'], info['end_v']
            all_nodes = real_rids + wait_rids
            N = len(all_nodes)
            end_node = N + 1

            new_seq = []
            new_times = {}
            current = 0
            for _ in range(N + 1):
                found = False
                for (a, b), lit in arc_lit.items():
                    if a == current and b != 0 and b != current and solver.Value(lit):
                        if b != end_node:
                            rid_b = nid_to_rid[b]
                            new_seq.append(rid_b)
                            new_times[rid_b] = (solver.Value(start_v[b]), solver.Value(end_v[b]))
                        current = b
                        found = True
                        break
                if not found or current == end_node:
                    break

            seq_real = [r for r in new_seq if r not in ctx.wait_nodes]
            if seq_real != real_rids:
                refined_count += 1
                ctx.logger.info(f"Stage2 {lid}: 排序优化 {[ctx.product_attrs[r].get('productId','') for r in seq_real]}")
            line_sequences[lid] = seq_real if seq_real else real_rids
            line_times[lid] = new_times

    if refined_count > 0:
        ctx.logger.info(f"Stage2 完成: {refined_count} 条产线排序优化")

    # Compaction can move solved timestamps; preserve explicit user placements.
    if not any(p.get('demoPlacement') for p in ctx.product_attrs.values()):
        postprocess_zero_groups(ctx, line_sequences, line_times)

    return result_builder.build_result_from_assignment(
        ctx, line_sequences, line_remaining_time, changeover_time_penalty,
        scheduled_set, line_times=line_times)
