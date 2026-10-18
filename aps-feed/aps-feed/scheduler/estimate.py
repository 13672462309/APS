"""estimate.py: 轻量级贪心负载预估（不走 CP-SAT）。

供 /estimate-task 接口用：快速估算各产线负载/加班，不做精确排产。
"""
import constant
from scheduler.context import ApsContext
from scheduler import capacity, preprocessing


def estimate_production_load(ctx: ApsContext) -> dict:
    """快速预估排产负载，返回各产线负载/加班/任务数与汇总。"""
    ctx.logger.info('开始快速预估排产负载')
    if not capacity.get_product_capacity_list(ctx):
        ctx.logger.error("获取产能数据失败")
        return None

    scheduled_rids = [r_id for r_id, product in ctx.product_attrs.items() if product['materialPriority'] != "4"]
    lines = list(ctx.line_attrs.keys())
    preprocessing.precompute_production_times(ctx, scheduled_rids, lines)

    line_status = {l: {'load': 0, 'count': 0, 'last_rid': ctx.line_attrs[l]['recordId']} for l in lines}

    unassigned = []
    order_metrics = []
    for rid in scheduled_rids:
        capable_lines = []
        total_time = 0
        for l in lines:
            t, priority = ctx.prod_time_matrix.get((rid, l), (constant.IS_UnScheduled, 0))
            if t != constant.IS_UnScheduled:
                capable_lines.append({'line': l, 'time': t, 'priority': priority})
                total_time += t

        if not capable_lines:
            # 无任何可行产线的产品必须计入 unassigned —— 否则 /estimate-task 的
            # unassignedCount 只统计"有可行线但没抢到"的，把"根本排不了"的漏掉，
            # 业务看到的未分配数会系统性偏小。
            unassigned.append(rid)
            continue

        avg_time = total_time / len(capable_lines)
        order_metrics.append({'rid': rid, 'avg_time': avg_time, 'capable': capable_lines})

    order_metrics.sort(key=lambda x: x['avg_time'], reverse=True)

    for item in order_metrics:
        rid = item['rid']
        best_line = None
        min_score = float('inf')
        min_new_load = float('inf')

        for cap in item['capable']:
            l = cap['line']
            prod_time = cap['time']
            line_priority = cap['priority']

            last_rid = line_status[l]['last_rid']
            switch_time = 0
            if last_rid:
                st = capacity.get_switch_time(ctx, last_rid, rid)
                if st != constant.IS_UnScheduled:
                    switch_time = st
                else:
                    continue

            current_load = line_status[l]['load']
            new_load = current_load + prod_time + switch_time

            score = new_load * 100 + switch_time + line_priority * 5

            if score < min_score:
                min_score = score
                best_line = l
                min_new_load = new_load

        if best_line:
            line_status[best_line]['load'] = min_new_load
            line_status[best_line]['count'] += 1
            line_status[best_line]['last_rid'] = rid
        else:
            unassigned.append(rid)

    result_lines = []
    total_overtime_amount = 0
    max_overtime_val = 0

    for l in lines:
        limit_hours = float(ctx.line_attrs[l].get('lineRemainingTime', 0))
        limit_min = limit_hours * 60

        estimated_min = line_status[l]['load']

        overtime = max(0, estimated_min - limit_min)
        if overtime > 0:
            total_overtime_amount += overtime
            if overtime > max_overtime_val:
                max_overtime_val = overtime

        max_feasible = estimated_min * 1.1 + 30 if estimated_min > 0 else 0
        min_feasible = estimated_min * 0.9 if estimated_min > 0 else 0

        result_lines.append({
            'productionLineId': l,
            'productionLineName': ctx.line_attrs[l].get('productionLineName', l),
            'estimatedDurationMinutes': round(estimated_min, 1),
            'limitDurationMinutes': round(limit_min, 1),
            'minFeasibleDurationMinutes': round(min_feasible, 1),
            'maxFeasibleDurationMinutes': round(max_feasible, 1),
            'isOvertime': estimated_min > limit_min,
            'overtimeMinutes': round(overtime, 1),
            'taskCount': line_status[l]['count']
        })

    summary = {
        'lineEstimates': result_lines,
        'unassignedCount': len(unassigned),
        'unassignedProductIds': unassigned,
        'maxOvertimeMinutes': round(max_overtime_val, 1),
        'isAnyOvertime': max_overtime_val > 0,
        'totalOvertimeMinutes': round(total_overtime_amount, 1)
    }

    ctx.logger.info(f"快速预估完成: MaxOvertime={max_overtime_val}, Unassigned={len(unassigned)}")
    return summary
