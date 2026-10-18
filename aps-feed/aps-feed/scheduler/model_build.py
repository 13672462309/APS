"""model_build.py: 求解入口编排 + 共享预处理 + 解抽取。

两段式求解的总入口：
  preprocess_electricity()   电价预处理（分时电价 + 加权均价）
  compute_changeover_penalty()  动态 changeover penalty
  build_model()              → scheduler.stage1_assign    Stage1 纯分配
  solve_and_extract()        Stage1 求解 → Stage2 排序 → 构建结果
  schedule()                 入口: 串联上述步骤

从原 Aps.production_scheduling (471 行) 拆出；模型本体见 stage1_assign /
stage2_ordering / zero_groups。兼容起见，历史符号在此再导出。
"""
import os
import constant
from ortools.sat.python import cp_model
from scheduler.context import ApsContext
from scheduler import preprocessing, capacity
from scheduler.stage1_assign import (          # noqa: F401  兼容再导出
    build_model,
    _build_feasible_rids,
    _add_assignment_approximations,
    _add_r5_mixer_isolation,
    _add_balance_constraints,
    _add_preorder_delay_cost,
)
from scheduler.stage2_ordering import (        # noqa: F401  兼容再导出
    refine_line_ordering as _refine_line_ordering,
    _build_mixer_components,
    _add_line_circuit,
    _add_cross_line_pr0014,
)
from scheduler.zero_groups import (            # noqa: F401  兼容再导出
    postprocess_zero_groups as _postprocess_zero_groups,
)


def preprocess_electricity(ctx: ApsContext, lines: list) -> None:
    """电价数据预处理：每条产线的分时段电价 + 加权平均电价。"""
    ctx.line_electricity_prices = {}
    ctx.line_avg_electricity_price = {}
    has_electricity_data = False
    for l_id, line in ctx.line_attrs.items():
        ep_list = line.get('electricityPrice', [])
        if ep_list:
            has_electricity_data = True
            segments = []
            for seg in ep_list:
                # 时段转 int 分钟: CP-SAT 线性约束/IntVar 域只接受整数,
                # 电价时段以小时字符串给出(如 "0.5"),*60 后必须 int() 否则
                # 下游 valley 约束会带 float 常数触发
                # "Linear constraints only accept integer values"。
                seg_start = int(float(seg['electricityStartTime']) * 60)
                seg_end = int(float(seg['electricityEndTime']) * 60)
                price = float(seg['ePrice'])  # 价格不进约束, 留 float 用于成本积分
                segments.append((seg_start, seg_end, price))
            segments.sort(key=lambda x: x[0])
            ctx.line_electricity_prices[l_id] = segments
            total_weighted = sum((e - s) * p for s, e, p in segments)
            total_duration = sum(e - s for s, e, p in segments)
            ctx.line_avg_electricity_price[l_id] = total_weighted / total_duration if total_duration > 0 else 0
            ctx.logger.info(f"产线 {l_id} 电价: {len(segments)} 个时段, 加权平均 {ctx.line_avg_electricity_price[l_id]:.4f} 元/度")
    if has_electricity_data:
        ctx.logger.info(f"电耗数据已加载, electricity_cost 权重 = {ctx.constraint_weights.get('electricity_cost', 0)}")


def compute_changeover_penalty(ctx: ApsContext) -> int:
    """动态 changeover_time_penalty：取 switch_matrix 中的实际最大值。"""
    max_switch = 0
    for (r1, r2), st in ctx.switch_time_matrix.items():
        if st != constant.IS_UnScheduled and st > max_switch:
            max_switch = st
    penalty = max(max_switch, 1)
    ctx.logger.info(f"动态 changeover_time_penalty = {penalty} (来自 switch_matrix 最大值)")
    return penalty


def solve_and_extract(ctx: ApsContext, build_result, scheduled_rids, lines,
                      line_remaining_time, changeover_time_penalty):
    """Stage1 求解 + Stage2 排序 + 构建完整结果。"""
    from scheduler import result_builder, scoring

    model = build_result['model']
    active_lines = build_result['active_lines']
    line_feasible_rids = build_result['line_feasible_rids']
    is_scheduled = build_result['is_scheduled']

    solver = cp_model.CpSolver()
    _env_max_time = os.environ.get('CP_SAT_MAX_TIME')
    if _env_max_time:
        try:
            solver.parameters.max_time_in_seconds = float(_env_max_time)
        except ValueError:
            solver.parameters.max_time_in_seconds = 30.0
    else:
        solver.parameters.max_time_in_seconds = 30.0
    solver.parameters.linearization_level = 1
    _env_workers = os.environ.get('CP_SAT_WORKERS')
    if _env_workers and _env_workers.isdigit():
        num_workers = int(_env_workers)
    else:
        num_workers = min(max((os.cpu_count() or 4) // 3, 4), 16)
    solver.parameters.num_workers = num_workers
    solver.parameters.log_search_progress = constant.IS_print_detailed_log

    ctx.logger.info("Stage1 CP-SAT 求解器开始运行...")
    status = solver.Solve(model)

    status_name = {cp_model.OPTIMAL: '最优解', cp_model.FEASIBLE: '可行解',
                   cp_model.INFEASIBLE: '无可行解', cp_model.MODEL_INVALID: '模型无效',
                   cp_model.UNKNOWN: '未知'}.get(status, str(status))
    wall_time = solver.WallTime()

    if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        ctx.logger.warning(f"Stage1 求解失败: 状态={status_name}, 耗时={wall_time:.1f}s")
        return {}

    obj_val = solver.ObjectiveValue()
    best_bound = solver.BestObjectiveBound()
    gap = abs(best_bound - obj_val) / max(abs(obj_val), 1) * 100
    ctx.logger.info(f"Stage1 完成: 状态={status_name}, 耗时={wall_time:.1f}s, "
                    f"目标值={obj_val:.0f}, Gap={gap:.2f}%")

    # 抽取 Stage1 分配结果
    line_assignment = {}
    for l in active_lines:
        assigned = []
        for rid in line_feasible_rids[l]:
            if (l, rid) in is_scheduled and solver.Value(is_scheduled[(l, rid)]):
                assigned.append(rid)
        if assigned:
            line_assignment[l] = assigned

    scheduled_set = set()
    for l, rids in line_assignment.items():
        for rid in rids:
            if rid not in ctx.wait_nodes:
                scheduled_set.add(rid)

    ctx.changeover_time_penalty = changeover_time_penalty

    # Stage2: 逐线排序 + 构建完整结果
    result = _refine_line_ordering(ctx, line_assignment, line_remaining_time,
                                    changeover_time_penalty, scheduled_set)
    return result


def schedule(ctx: ApsContext) -> dict:
    """排产主入口（CP-SAT）。"""
    ctx.logger.info('开始生产调度 (基于 OR-Tools CP-SAT)')
    if not capacity.get_product_capacity_list(ctx):
        return {}

    all_rids = list(ctx.product_attrs.keys())
    scheduled_rids = [r_id for r_id, product in ctx.product_attrs.items() if product['materialPriority'] != "4"]
    lines = list(ctx.line_attrs.keys())

    line_remaining_time = {}
    for l_id, line in ctx.line_attrs.items():
        base_time = line['lineRemainingTime']
        allow_over_time = float(line.get('allowOverTime', 0))
        pre_order_time = float(line.get('preOrderProduceTime', 0) or 0)
        line_remaining_time[l_id] = int((base_time + allow_over_time + pre_order_time) * 60)
        if allow_over_time > 0 or pre_order_time > 0:
            ctx.logger.info(f"产线 {l_id} 可用时间: 基础{base_time}h + 加班{allow_over_time}h + 前置{pre_order_time}h = {line_remaining_time[l_id]} 分钟")

    preprocessing.precompute_production_times(ctx, all_rids, lines)
    preprocessing.precompute_switch_times(ctx, scheduled_rids, lines)
    preprocessing.precompute_switch_clusters(ctx, scheduled_rids)
    preprocessing.precompute_zero_groups(ctx, scheduled_rids)

    preprocess_electricity(ctx, lines)
    changeover_time_penalty = compute_changeover_penalty(ctx)

    build_result = build_model(ctx, scheduled_rids, lines, line_remaining_time, changeover_time_penalty)
    if build_result is None:
        return {}

    return solve_and_extract(ctx, build_result, scheduled_rids, lines,
                             line_remaining_time, changeover_time_penalty)
