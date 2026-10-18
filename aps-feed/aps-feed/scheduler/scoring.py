"""scoring.py: 后评估分计算 + 分段电价积分。

这些是排产完成后的"评估"逻辑，与模型构建期的目标函数项对应但独立。

注意: 本模块产出的是"业务展示口径"的综合分（未归一化、量纲与 model_build 的
CP-SAT 归一化目标 OBJECTIVE_NORMALIZE 不同）。该分数只用于日志与 HPAAS 展示,
不参与 solver 优化, 二者数值不可比。排产结构与优化方向以 model_build 目标函数为准。
"""
import constant
from scheduler.context import ApsContext


def calc_electricity_cost_for_slot(ctx: ApsContext, line_id, start_min, end_min, qty, uec) -> float:
    """根据实际生产时间段精确计算电耗成本（分段电价积分）。"""
    segments = ctx.line_electricity_prices.get(line_id, [])
    if not segments or qty <= 0 or uec <= 0:
        return 0.0
    total_hours = (end_min - start_min) / 60.0
    if total_hours <= 0:
        return 0.0
    weighted_price = 0.0
    for seg_start, seg_end, price in segments:
        overlap_start = max(start_min, seg_start)
        overlap_end = min(end_min, seg_end)
        if overlap_start < overlap_end:
            weighted_price += (overlap_end - overlap_start) * price
    avg_price = weighted_price / (end_min - start_min) if (end_min - start_min) > 0 else 0
    return qty * uec * avg_price


def calculate_solution_scores(ctx: ApsContext, scheduled_result: dict, detailed_log_enabled: bool = False):
    """后评估分：优先级/时间窗/蛋白/粒径/换型/均衡/电耗。"""
    total_priority_score = 0
    total_time_window_bonus = 0
    total_protein_grade_bonus = 0
    total_particle_size_bonus = 0
    total_changeover_score = 0
    total_electricity_cost = 0.0

    line_scores = {}
    detailed_log = []

    detailed_log.append("")
    detailed_log.append("=" * 80)
    detailed_log.append("【最优解得分详细计算过程】")
    detailed_log.append("=" * 80)

    for l, details in scheduled_result.get("schedule_details", {}).items():
        products = details.get("scheduled_products", [])
        if not products:
            detailed_log.append(f"\n产线 {l} ({ctx.line_attrs[l]['productionLineName']}): 无排产产品，跳过")
            continue

        real_products = [p for p in products if not p.get("is_wait", False)]

        line_priority_score = 0
        line_time_window_bonus = 0
        line_protein_grade_bonus = 0
        line_particle_size_bonus = 0
        line_changeover_score = 0
        line_electricity_cost = 0.0

        detailed_log.append(f"\n产线 {l} ({ctx.line_attrs[l]['productionLineName']}):")
        pre_rid = ctx.line_attrs[l]['recordId']
        pre_time, _ = ctx.prod_time_matrix.get((pre_rid, l), (0, 10))
        detailed_log.append(f"  前序产品: {pre_rid} (耗时: {pre_time}分钟)")
        detailed_log.append("-" * 60)

        prev_rid = pre_rid

        for i, product_info in enumerate(real_products):
            rid = product_info["rid"]
            detailed_log.append(f"\n  产品 {i+1}: {rid}")

            product_id = ctx.product_attrs[rid]['productId']
            capacity_info = ctx.product_product_line_capacity_matrix.get(product_id + '-' + l, {'priority': 0})
            material_priority = int(ctx.product_attrs[rid].get('materialPriority', 4))
            product_priority_score = constant.priority_weights['product'].get(material_priority, 0)
            line_priority = capacity_info['priority']
            line_priority_score_part = constant.priority_weights['line'].get(line_priority, 0)

            prod_priority = (product_priority_score * ctx.constraint_weights['material_priority'] +
                             line_priority_score_part * ctx.constraint_weights['line_priority'])
            detailed_log.append(f"     ├─ 优先级得分: = {prod_priority:.2f}")
            line_priority_score += prod_priority

            switch_time = product_info.get("switch_time", 0)
            ctp = ctx.changeover_time_penalty or constant.changeover_time_penalty
            if switch_time >= 0:
                pair_changeover_score = ctp - switch_time
                detailed_log.append(f"     ├─ 换型得分: {ctp} - {switch_time} = {pair_changeover_score:.2f}")
                line_changeover_score += pair_changeover_score

            elec_cost = product_info.get("electricity_cost", 0)
            if elec_cost > 0:
                detailed_log.append(f"     ├─ 电耗成本: {elec_cost:.2f} 元")
                line_electricity_cost += elec_cost

            start_time = product_info["start_time"]
            end_time = product_info["end_time"]
            if 'time_window' in ctx.product_attrs[rid]:
                earliest = ctx.product_attrs[rid]['time_window']['earliest'] * 60
                latest = ctx.product_attrs[rid]['time_window']['latest'] * 60
                in_window = start_time >= earliest and end_time <= latest
                detailed_log.append(f"     ├─ 时间窗口奖励: {'在窗口内' if in_window else '超出窗口'} = {1 if in_window else 0}")
                if in_window:
                    line_time_window_bonus += 1

            if i == 0:
                line_particle_size = float(ctx.line_attrs[l].get('productionParticleSize', 0) or 0)
                product_particle_size = float(ctx.product_attrs[rid].get('productionParticleSize', 0) or 0)
                particle_bonus = 1 if line_particle_size <= product_particle_size else 0
                detailed_log.append(f"     ├─ 粒径排序奖励: {'升序' if particle_bonus else '非升序'} = {particle_bonus}")
                if particle_bonus:
                    line_particle_size_bonus += 1
                prev_rid = rid
                continue

            prev_protein = float(ctx.product_attrs[prev_rid].get('proteinGradeLevel', 0) or 0)
            curr_protein = float(ctx.product_attrs[rid].get('proteinGradeLevel', 0) or 0)
            protein_bonus = 1 if prev_protein >= curr_protein else 0
            detailed_log.append(f"     ├─ 蛋白档次奖励: {'降序' if protein_bonus else '非降序'} = {protein_bonus}")
            if protein_bonus:
                line_protein_grade_bonus += 1

            prev_size = float(ctx.product_attrs[prev_rid].get('productionParticleSize', 0) or 0)
            curr_size = float(ctx.product_attrs[rid].get('productionParticleSize', 0) or 0)
            size_bonus = 1 if prev_size <= curr_size else 0
            detailed_log.append(f"     ├─ 粒径排序奖励: {'升序' if size_bonus else '非升序'} = {size_bonus}")
            if size_bonus:
                line_particle_size_bonus += 1

            prev_rid = rid

        line_scores[l] = {
            "priority_score": line_priority_score,
            "time_window_bonus": line_time_window_bonus,
            "protein_grade_bonus": line_protein_grade_bonus,
            "particle_size_bonus": line_particle_size_bonus,
            "changeover_score": line_changeover_score,
            "electricity_cost": line_electricity_cost
        }

        total_priority_score += line_priority_score
        total_time_window_bonus += line_time_window_bonus
        total_protein_grade_bonus += line_protein_grade_bonus
        total_particle_size_bonus += line_particle_size_bonus
        total_changeover_score += line_changeover_score
        total_electricity_cost += line_electricity_cost

    production_balance_score = 0
    line_loads = []
    if ctx.constraint_weights.get('production_balance', 0) > 0:
        for l, details in scheduled_result.get("schedule_details", {}).items():
            load = sum(p.get("production_time", 0) for p in details.get("scheduled_products", []))
            line_loads.append(load)
        if line_loads:
            production_balance_score = min(line_loads) - max(line_loads)

    elec_weight = ctx.constraint_weights.get('electricity_cost', 0)
    electricity_penalty = int(total_electricity_cost * 100) * elec_weight if elec_weight > 0 else 0

    # 业务展示口径的综合分（未归一化），仅用于日志/HPAAS 展示，非 solver 优化目标。
    total_score = (
        total_priority_score +
        total_time_window_bonus * ctx.constraint_weights['time_window'] +
        total_protein_grade_bonus * ctx.constraint_weights['protein_grade'] +
        total_particle_size_bonus * ctx.constraint_weights['particle_size'] +
        total_changeover_score * ctx.constraint_weights['changeover_time'] +
        production_balance_score * ctx.constraint_weights.get('production_balance', 0) -
        electricity_penalty
    )

    if detailed_log_enabled:
        return detailed_log

    return {
        "priority_score": total_priority_score,
        "time_window_bonus": total_time_window_bonus,
        "protein_grade_bonus": total_protein_grade_bonus,
        "particle_size_bonus": total_particle_size_bonus,
        "changeover_score": total_changeover_score,
        "production_balance_score": production_balance_score,
        "electricity_cost": round(total_electricity_cost, 2),
        "total_score": total_score,
        "line_scores": line_scores
    }
