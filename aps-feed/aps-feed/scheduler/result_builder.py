"""result_builder.py: 从 solver 解抽取排程、转 HPAAS 格式、日志打印、可行性分析。"""
import constant
from scheduler.context import ApsContext
from scheduler import scoring


def build_result_from_assignment(ctx, line_sequences, line_remaining_time,
                                  changeover_time_penalty, scheduled_set, line_times=None):
    """从 Stage1 分配 + Stage2 排序构建完整排产结果。

    line_sequences: {line_id: [rid, ...]} Stage2 优化后的线内排序 (不含 wait 节点)
    line_times: {line_id: {rid: (start, end)}} Stage2 求解器给出的实际时刻。
                为空时回退"紧凑回放"(仅按顺序+换型推算)。
    """
    from scheduler import scoring

    line_times = line_times or {}
    lines_scheduled = {}
    line_schedule_details = {}
    total_available_time = 0
    total_used_time = 0

    for l, seq_list in line_sequences.items():
        if not seq_list:
            continue

        sequence = {}
        for idx, rid in enumerate(seq_list, 1):
            sequence[idx] = rid
        lines_scheduled[l] = sequence

        line_schedule_details[l] = {
            "name": ctx.line_attrs[l]['productionLineName'],
            "description": ctx.line_attrs[l].get('productionLineDescription', ''),
            "available_time": line_remaining_time[l],
            "used_time": 0, "utilization": 0, "scheduled_products": [], "time_slots": []
        }
        total_available_time += line_remaining_time[l]

        pre_order_h = float(ctx.line_attrs[l].get('preOrderProduceTime', 0) or 0)
        if pre_order_h > 0:
            current_time = int(pre_order_h * 60)
        else:
            pre_rid0 = ctx.line_attrs[l]['recordId']
            pre_prod_time, _ = ctx.prod_time_matrix.get((pre_rid0, l), (0, 10))
            current_time = pre_prod_time

        solver_times = line_times.get(l, {})
        pre_rid = ctx.line_attrs[l].get('recordId', '')
        total_line_time = 0
        last_end = current_time

        for rid in seq_list:
            product = ctx.product_attrs[rid]
            need_time, _ = ctx.prod_time_matrix.get((rid, l), (0, 10))
            if pre_rid and pre_rid not in ctx.wait_nodes:
                switch_time = ctx.switch_time_matrix.get((pre_rid, rid), 0)
            else:
                switch_time = 0

            if rid in solver_times:
                # Stage2 求解器给出的真实时刻 (含时间窗/谷电/等待间隙)
                start, end = solver_times[rid]
                # 换型时长用矩阵值; start 与 last_end 的差额中超出换型的部分记为 idle
                gap_start = last_end + switch_time
                if start > gap_start:
                    line_schedule_details[l]["time_slots"].append(
                        {"type": "idle", "start_time": gap_start, "end_time": start,
                         "duration": start - gap_start, "reason": "混合机隔离/时间窗等待"})
            else:
                start = current_time + switch_time
                end = start + need_time

            if switch_time > 0:
                line_schedule_details[l]["time_slots"].append(
                    {"type": "switch", "start_time": last_end,
                     "end_time": last_end + switch_time, "duration": switch_time,
                     "reason": "换型"})

            qty = float(product.get('plannedProductionQuantity', 0))
            uec = float(product.get('unitElectricityConsumption', 0))
            elec_cost = scoring.calc_electricity_cost_for_slot(ctx, l, start, end, qty, uec)

            product_info = {"rid": rid, "name": product['productName'], "production_time": need_time,
                            "switch_time": switch_time, "total_time": switch_time + need_time,
                            "start_time": start, "end_time": end, "electricity_cost": elec_cost}
            line_schedule_details[l]["scheduled_products"].append(product_info)
            line_schedule_details[l]["time_slots"].append(
                {"type": "production", "start_time": start, "end_time": end,
                 "duration": need_time, "product": product['productName']})

            last_end = end
            current_time = end
            total_line_time += switch_time + need_time
            pre_rid = rid

        line_schedule_details[l]["used_time"] = total_line_time
        line_schedule_details[l]["utilization"] = round(total_line_time / line_remaining_time[l] * 100, 2) if line_remaining_time[l] > 0 else 0

        if last_end < line_remaining_time[l]:
            line_schedule_details[l]["time_slots"].append(
                {"type": "idle", "start_time": last_end, "end_time": line_remaining_time[l],
                 "duration": line_remaining_time[l] - last_end})

        total_used_time += total_line_time

    overall_utilization = round(total_used_time / total_available_time * 100, 2) if total_available_time > 0 else 0

    all_rids = set(ctx.product_attrs.keys())
    result = lines_scheduled.copy()
    result["scheduled"] = scheduled_set
    result["time"] = total_used_time
    result["schedule_details"] = line_schedule_details
    result["overall_utilization"] = overall_utilization
    result["unscheduled"] = list(all_rids - scheduled_set)

    if ctx.changeover_time_penalty == 0 and changeover_time_penalty:
        ctx.changeover_time_penalty = changeover_time_penalty
    scores = scoring.calculate_solution_scores(ctx, result, detailed_log_enabled=False)
    result["scores"] = scores

    elec_info = f", 电耗成本={scores.get('electricity_cost', 0):.2f}" if scores.get('electricity_cost', 0) != 0 else ""
    ctx.logger.info(f"求解结果: 优先级={scores['priority_score']:.2f}, 时间窗={scores['time_window_bonus']}, "
                    f"换型={scores['changeover_score']:.2f}{elec_info}, 业务综合分={scores['total_score']:.2f}, 耗时={total_used_time}")
    print_solution_log(ctx, result, list(ctx.product_attrs.keys()), {})

    return result


def build_schedule_results(ctx: ApsContext, scheduled_result: dict) -> list:
    """将排产结果转换为 HPAAS 回调接口所需的 scheduleResults 数组。"""
    from scheduler import capacity

    placeholder = " "
    schedule_results = []
    schedule_details = scheduled_result.get("schedule_details", {})

    for line_id, details in schedule_details.items():
        products = details.get("scheduled_products", [])
        prev_rid = None
        for seq_idx, product_info in enumerate(products):
            rid = product_info["rid"]
            if product_info.get("is_wait", False) or rid in ctx.wait_nodes:
                continue
            product = ctx.product_attrs[rid]
            product_id = product.get('productId', '')

            capacity_info = ctx.product_product_line_capacity_matrix.get(
                product_id + '-' + line_id, {'priority': 0, 'capacity': 0})
            line_priority = capacity_info.get('priority', 0)

            fit_time = 0
            if 'time_window' in product:
                earliest_min = product['time_window']['earliest'] * 60
                latest_min = product['time_window']['latest'] * 60
                if product_info["start_time"] >= earliest_min and product_info["end_time"] <= latest_min:
                    fit_time = 1

            fit_dia = 0
            curr_size = float(product.get('productionParticleSize', 0) or 0)
            if seq_idx == 0:
                line_size = float(ctx.line_attrs[line_id].get('productionParticleSize', 0) or 0)
                if line_size <= curr_size:
                    fit_dia = 1
            elif prev_rid:
                prev_size = float(ctx.product_attrs[prev_rid].get('productionParticleSize', 0) or 0)
                if prev_size <= curr_size:
                    fit_dia = 1

            fit_grade = 1
            if seq_idx > 0 and prev_rid:
                prev_protein = float(ctx.product_attrs[prev_rid].get('proteinGradeLevel', 0) or 0)
                curr_protein = float(product.get('proteinGradeLevel', 0) or 0)
                fit_grade = 1 if prev_protein >= curr_protein else 0

            planned_quantity = float(product.get('plannedProductionQuantity', 0) or 0)
            # 注意: 局部变量不能命名为 capacity, 否则遮蔽顶部 `from scheduler import capacity`,
            # 导致下方 capacity.get_product_priority() 抛 AttributeError (重构 dc751d2 引入)。
            capacity_value = float(capacity_info.get('capacity', 0) or 0)
            if capacity_value > 0:
                exact_hours = planned_quantity / capacity_value
                production_time_hours = f"{exact_hours:.6f}".rstrip('0').rstrip('.')
            else:
                production_time_hours = str(round(product_info["production_time"] / 60.0, 2))
            elec_cost = product_info.get("electricity_cost", 0)

            priority_score = capacity.get_product_priority(ctx, rid, line_id)
            switch_time = product_info.get("switch_time", 0)
            ctp = ctx.changeover_time_penalty or constant.changeover_time_penalty
            changeover_score = (ctp - switch_time) * ctx.constraint_weights.get('changeover_time', 0)
            elec_penalty = int(elec_cost) * ctx.constraint_weights.get('electricity_cost', 0)
            product_score = (
                constant.base_scheduling_bonus
                + priority_score
                + fit_time * ctx.constraint_weights.get('time_window', 0)
                + fit_dia * ctx.constraint_weights.get('particle_size', 0)
                + fit_grade * ctx.constraint_weights.get('protein_grade', 0)
                + changeover_score
                - elec_penalty
            )

            entry = {
                "versionId": ctx.version_id,
                "traceId": ctx.trace_id,
                "recordId": rid,
                "productId": product_id,
                "productionLineId": line_id,
                "productSequence": seq_idx + 1,
                "productSchedulingScore": str(round(product_score, 2)),
                "productQuantity": str(planned_quantity),
                "productionTime": production_time_hours,
                "productionChangeTime": str(product_info.get("switch_time", 0)),
                "powerCost": str(round(elec_cost, 2)),
                "priority": line_priority,
                "fitProductionTime": fit_time,
                "fitDia": fit_dia,
                "fitGrade": fit_grade,
            }
            schedule_results.append(entry)
            prev_rid = rid

    unscheduled = scheduled_result.get("unscheduled", [])
    for rid in unscheduled:
        product = ctx.product_attrs.get(rid, {})
        schedule_results.append({
            "versionId": ctx.version_id,
            "traceId": ctx.trace_id,
            "recordId": rid,
            "productId": product.get('productId', ''),
            "productionLineId": placeholder,
            "productSequence": 0,
            "productSchedulingScore": placeholder,
            "productQuantity": str(float(product.get('plannedProductionQuantity', 0) or 0)),
            "productionTime": placeholder,
            "productionChangeTime": placeholder,
            "powerCost": placeholder,
            "priority": placeholder,
            "fitProductionTime": 0,
            "fitDia": 0,
            "fitGrade": 0,
        })

    ctx.logger.info(f"构建 scheduleResults 完成: {len(schedule_results)} 条记录")
    return schedule_results


def get_feasible_lines_for_product(ctx: ApsContext, rid) -> str:
    if rid not in ctx.product_attrs:
        return "产品信息不存在"

    feasible_lines = []
    for l in ctx.line_attrs.keys():
        prod_time, _ = ctx.prod_time_matrix.get((rid, l), (constant.IS_UnScheduled, 10))
        if prod_time != constant.IS_UnScheduled:
            pre_rid = ctx.line_attrs[l]['recordId']
            switch_time = ctx.switch_time_matrix.get((pre_rid, rid))
            if switch_time != constant.IS_UnScheduled:
                line_name = ctx.line_attrs[l].get('productionLineName', f'产线{l}')
                short_name = line_name[:4] if len(line_name) > 4 else line_name
                feasible_lines.append(f"{short_name}({prod_time+switch_time}分)")

    if not feasible_lines:
        return "无可排产产线"

    if len(feasible_lines) > 3:
        remaining = len(feasible_lines) - 3
        return ", ".join(feasible_lines[:3]) + f"等{remaining+3}条"
    return ", ".join(feasible_lines)


def analyze_infeasibility_reason(ctx: ApsContext, rid) -> str:
    if rid not in ctx.product_attrs:
        return "产品信息不存在"

    product_info = ctx.product_attrs[rid]
    reasons = []

    priority = product_info.get('materialPriority', '')
    if priority == "4":
        reasons.append("低优先级产品(4级)")

    time_window = product_info.get('time_window', {})
    earliest = time_window.get('earliest', 0) * 60
    latest = time_window.get('latest', 24) * 60
    window_duration = latest - earliest

    feasible_lines = []
    min_prod_time = float('inf')

    for l in ctx.line_attrs.keys():
        prod_time, _ = ctx.prod_time_matrix.get((rid, l), (constant.IS_UnScheduled, 10))
        if prod_time != constant.IS_UnScheduled:
            min_prod_time = min(min_prod_time, prod_time)
            feasible_lines.append(l)

    if not feasible_lines:
        reasons.append("没有兼容的生产线")
    elif min_prod_time == float('inf'):
        reasons.append("无法计算生产时间")
    elif min_prod_time > window_duration:
        reasons.append(f"时间窗口过短({window_duration/60:.1f}h < 所需{min_prod_time/60:.1f}h)")

    if not reasons:
        reasons.append("可能受产能限制、换型约束或其他产品排产顺序影响")

    return "，".join(reasons)


def print_solution_log(ctx: ApsContext, scheduled_result: dict, all_rids=None, M_values=None):
    """打印排产计划详情 + 未排产产品 + 得分计算过程。"""
    if all_rids is None:
        all_rids = list(ctx.product_attrs.keys())
    if M_values is None:
        M_values = {}

    scheduled_products_set = scheduled_result.get("scheduled", set())
    schedule_details = scheduled_result.get("schedule_details", {})
    overall_utilization = scheduled_result.get("overall_utilization", 0)

    scheduled_count = len(scheduled_products_set)

    log_lines = []
    log_lines.append("\n" + "=" * 100)
    log_lines.append(f"排产计划详情 (版本ID: {ctx.version_id}, 跟踪ID: {ctx.trace_id})")
    log_lines.append("=" * 100)

    for l in ctx.line_attrs.keys():
        line_name = ctx.line_attrs[l].get('productionLineName', f'生产线 {l}')
        line_details = schedule_details.get(l, {})
        products = line_details.get("scheduled_products", [])

        log_lines.append(f"\n生产线: {line_name} (ID: {l})")
        log_lines.append("-" * 80)

        if not products:
            log_lines.append("  该生产线没有安排产品")
            continue

        available_time = line_details.get("available_time", 0)
        used_time = line_details.get("used_time", 0)
        utilization = line_details.get("utilization", 0)

        log_lines.append(f"  产品数量: {len(products)}")
        log_lines.append(f"  可用时间: {available_time} 分钟")
        log_lines.append(f"  使用时间: {used_time} 分钟")
        log_lines.append(f"  产线利用率: {utilization:.2f}%")

        log_lines.append("\n  产品排产详情:")
        headers = ["#", "记录ID", "开始时间", "结束时间", "生产时间", "切换时间", "优先级", "时间窗口", "窗口内"]
        col_widths = [4, 26, 12, 12, 12, 12, 8, 14, 6]

        header_row = "  "
        for i, header in enumerate(headers):
            if i in [3, 4, 5, 6]:
                header += "(分)"
            header_row += header.ljust(col_widths[i])
        log_lines.append(header_row)
        log_lines.append("  " + "-" * (sum(col_widths)))

        for i, product in enumerate(products):
            rid = product["rid"]
            if rid not in ctx.product_attrs:
                continue

            product_info = ctx.product_attrs[rid]

            start_time = product.get("start_time", 0)
            end_time = product.get("end_time", 0)
            production_time = product.get("production_time", 0)
            switch_time = product.get("switch_time", 0)
            priority = product_info.get("materialPriority", "未知")

            earliest = product_info.get('time_window', {}).get('earliest', 0) * 60
            latest = product_info.get('time_window', {}).get('latest', 24) * 60
            in_window = "是" if (start_time >= earliest and end_time <= latest) else "否"

            time_window = f"[{earliest/60:.1f}-{latest/60:.1f}]h"

            row = f"  {str(i+1).ljust(col_widths[0])}"
            row += f"{rid.ljust(col_widths[1])}"
            row += f"{f'{start_time:.1f}'.ljust(col_widths[2])}"
            row += f"{f'{end_time:.1f}'.ljust(col_widths[3])}"
            row += f"{f'{production_time:.1f}'.ljust(col_widths[4])}"
            row += f"{f'{switch_time:.1f}'.ljust(col_widths[5])}"
            row += f"{priority.ljust(col_widths[6])}"
            row += f"{time_window.ljust(col_widths[7])}"
            row += f"{in_window.ljust(col_widths[8])}"
            log_lines.append(row)

        log_lines.append("\n  换型详情:")
        prev_product = None
        for i, product in enumerate(products):
            rid = product["rid"]
            if i == 0:
                pre_rid = ctx.line_attrs[l]['recordId']
                switch_time = product.get("switch_time", 0)
                log_lines.append(f"  前序产品 {pre_rid} -> 产品 {rid}: {switch_time} 分钟")
                prev_product = product
                continue

            if prev_product:
                prev_rid = prev_product["rid"]
                switch_time = product.get("switch_time", 0)
                log_lines.append(f"  产品 {prev_rid} -> 产品 {rid}: {switch_time} 分钟")
                prev_product = product

    log_lines.append("\n" + "=" * 100)
    log_lines.append("未排产产品详情")
    log_lines.append("=" * 100)

    unscheduled_products = set(all_rids) - scheduled_products_set
    if not unscheduled_products:
        log_lines.append("所有产品均已排产")
    else:
        log_lines.append(f"未排产产品数: {len(unscheduled_products)}")
        headers = ["#", "记录ID", "优先级", "时间窗口", "可排产产线", "不可行原因"]
        col_widths = [4, 26, 8, 12, 30, 38]

        header_row = ""
        for i, header in enumerate(headers):
            header_row += header.ljust(col_widths[i])
        log_lines.append(header_row)
        log_lines.append("-" * (sum(col_widths)))

        for i, rid in enumerate(sorted(unscheduled_products)):
            if rid not in ctx.product_attrs:
                continue

            product_info = ctx.product_attrs[rid]
            priority = product_info.get("materialPriority", "未知")
            earliest = product_info.get('time_window', {}).get('earliest', '-')
            latest = product_info.get('time_window', {}).get('latest', '-')
            time_window = f"[{earliest}-{latest}]h"

            feasible_lines_info = get_feasible_lines_for_product(ctx, rid)
            infeasibility_reason = analyze_infeasibility_reason(ctx, rid)
            if len(infeasibility_reason) > col_widths[5]-2:
                infeasibility_reason = infeasibility_reason[:col_widths[5]-3] + "…"

            row = f"{str(i+1).ljust(col_widths[0])}"
            row += f"{rid.ljust(col_widths[1])}"
            row += f"{priority.ljust(col_widths[2])}"
            row += f"{time_window.ljust(col_widths[3])}"
            row += f"{feasible_lines_info.ljust(col_widths[4])}"
            row += f"{infeasibility_reason.ljust(col_widths[5])}"
            log_lines.append(row)

    log_lines.append("\n" + "=" * 100)
    log_lines.append(f"排产计划总结: 已排产 {scheduled_count}/{len(all_rids)} 个产品, 总体利用率: {overall_utilization:.2f}%")
    log_lines.append("=" * 100)

    if scheduled_result.get("scores"):
        score_logs = scoring.calculate_solution_scores(ctx, scheduled_result, detailed_log_enabled=True)
        log_lines.extend(score_logs)

    ctx.logger.info("\n".join(log_lines))
