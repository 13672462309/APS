"""capacity.py: DB 产能查询与生产/切换时间、优先级得分计算。

纯计算函数（基于 ctx），不持有副作用状态。
"""
import math
import constant
from scheduler.context import ApsContext


def get_product_capacity_list(ctx: ApsContext) -> bool:
    """从 DB 加载产品-产线产能矩阵到 ctx。成功返回 True。"""
    import mysql_util
    with mysql_util.MySQLDB(
        ctx.mysql_config["host"],
        ctx.mysql_config["username"],
        ctx.mysql_config['password'],
        ctx.mysql_config['database'],
        ctx.mysql_config["port"]
    ) as mysql_conn:
        if not mysql_conn.connection:
            ctx.logger.error("DB connect failed in get_product_capacity_list.")
            return False

        succ, product_capacity_list = mysql_conn.select_data(
            'product_capacity_maintenance', 'org_id = ' + ctx.org_id)
        if not succ:
            ctx.logger.error("get_product_capacity_list SQL failed: %s", product_capacity_list)
            return False
        if product_capacity_list is None:
            return True

        try:
            for product_capacity in product_capacity_list:
                ctx.product_product_line_capacity_matrix[
                    product_capacity['product_id'] + '-' + product_capacity['production_line_id']] = {
                    'capacity': float(product_capacity['production_capacity']),
                    'min_quantity': product_capacity['minimum_order_quantity'],
                    # priority 必须 int 归一: DB 可能返回 str/Decimal,
                    # str 会让 priority_weights['line'].get(line_priority) 静默得 0,
                    # estimate 里 line_priority*5 会变成 "2"*5="22222" 污染评分。
                    'priority': int(product_capacity['production_line_priority'])
                }
        except Exception:
            import traceback
            ctx.logger.error("Error: %s", traceback.format_exc())
            return False

        fallback_capacity_from_semifinished(ctx)
        return True


def fallback_capacity_from_semifinished(ctx: ApsContext) -> None:
    """产能数据兜底: 无专属产能记录的产品继承同半成品/同小类产品的产能。

    product_capacity_maintenance 表可能漏维护某些新产品/散装产品, 导致
    cal_production_time 返回 IS_UnScheduled 而无法排产。这些产品通常与同半成品
    (semiFinishedProductId) 或同小类+粒径 (forthMaterialGroupId + dia) 的产品
    产能相同(同产线、同速率), 故按此继承, 避免"数据漏维护"直接造成漏排。
    """
    from collections import defaultdict

    line_ids = set(ctx.line_attrs.keys())

    # 两层兜底索引: 半成品 -> 产能; (小类, 粒径) -> 产能
    semi_to_cap = {}
    group_dia_to_cap = {}
    for product in ctx.product_attrs.values():
        pid = product['productId']
        caps = {}
        for line_id in line_ids:
            cap = ctx.product_product_line_capacity_matrix.get(pid + '-' + line_id)
            if cap is not None:
                caps[line_id] = cap
        if not caps:
            continue
        semi_to_cap.setdefault(product['semiFinishedProductId'], caps)
        group_dia_to_cap.setdefault(
            (product['forthMaterialGroupId'], str(product.get('dia', ''))), caps)

    # 对无任何产能记录的产品做兜底
    for product in ctx.product_attrs.values():
        pid = product['productId']
        has_capacity = any(
            (pid + '-' + line_id) in ctx.product_product_line_capacity_matrix
            for line_id in line_ids
        )
        if has_capacity:
            continue
        src = semi_to_cap.get(product['semiFinishedProductId'])
        source_label = f"同半成品 {product['semiFinishedProductId']}"
        if not src:
            src = group_dia_to_cap.get(
                (product['forthMaterialGroupId'], str(product.get('dia', ''))))
            source_label = f"同小类+粒径 {product['forthMaterialGroupId']}/{product.get('dia', '')}"
        if not src:
            continue
        for line_id, cap in src.items():
            ctx.product_product_line_capacity_matrix[pid + '-' + line_id] = dict(cap)
            ctx.logger.info(
                f"产能兜底: 产品 {pid}({product.get('productName', '')}) 无产能记录, "
                f"继承{source_label} 的 {line_id} 产能 cap={cap['capacity']}")



def cal_production_time(ctx: ApsContext, product_id, line, order_quantity, show_log=False):
    """计算单产品在指定产线的生产时间（分钟）。无产能返回 IS_UnScheduled。"""
    if order_quantity == 0:
        return 0, 10

    capacity_related = ctx.product_product_line_capacity_matrix.get(
        product_id + '-' + line, {'capacity': 0})
    if capacity_related['capacity'] == 0:
        if show_log:
            ctx.logger.error("No capacity : product " + product_id + ' ,line ' + line)
        return constant.IS_UnScheduled, 10
    return math.ceil(order_quantity * 60 / capacity_related['capacity']), capacity_related['priority']


def get_switch_time(ctx: ApsContext, rid1, rid2) -> int:
    """从原始 switch_matrix 读取切换时间。"""
    if rid1 == rid2:
        return 0
    switch_time = ctx.switch_matrix.get(rid1, {}).get(rid2, constant.IS_UnScheduled)
    if switch_time == constant.IS_UnScheduled:
        ctx.logger.error(f"product:{rid1} - product:{rid2}, switch_matrix not found.")
        return constant.IS_UnScheduled
    return int(switch_time)


def get_product_priority(ctx: ApsContext, rid, line):
    """产品+产线优先级加权得分（按 constraint_weights 缩放）。"""
    product_id = ctx.product_attrs[rid]['productId']
    capacity = ctx.product_product_line_capacity_matrix.get(product_id + '-' + line, {'priority': 0})
    product_priority = int(ctx.product_attrs[rid]['materialPriority'])
    line_priority = capacity['priority']

    product_score = constant.priority_weights['product'].get(product_priority, 0)
    line_score = constant.priority_weights['line'].get(line_priority, 0)
    final_score = (product_score * ctx.constraint_weights['material_priority']
                   + line_score * ctx.constraint_weights['line_priority'])
    return final_score
