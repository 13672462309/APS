"""preprocessing.py: 字段预处理、混合机分析、等待节点、生产/切换时间/cluster 预计算。"""
import math
from collections import defaultdict
import constant
from scheduler.context import ApsContext
from scheduler import capacity


def get_effective_special_type(product: dict) -> str:
    """计算产品的有效特殊类型，用于混合机隔离判断。"""
    special = product.get('specialProcessType', '') or ''
    if product.get('isDosing', 0) == 1 and not special:
        return constant.SPECIAL_TYPE_DOSING
    if product.get('isColoring', 0) == 1 and not special:
        return constant.SPECIAL_TYPE_COLORING
    return special


def preprocess_new_fields(ctx: ApsContext) -> None:
    """预处理新字段，设置默认值和阶段信息。"""
    for rid, product in ctx.product_attrs.items():
        product['isDosing'] = int(product.get('isDosing', 0))
        product['isColoring'] = int(product.get('isColoring', 0))
        product['specialProcessType'] = product.get('specialProcessType', '') or ''

        if product['isDosing'] == 1 and product['isColoring'] == 1:
            product['stage'] = 4
        elif product['isDosing'] == 1:
            product['stage'] = 3
        elif product['isColoring'] == 1:
            product['stage'] = 2
        else:
            product['stage'] = 1

    for line_id, line in ctx.line_attrs.items():
        line['assignedMixers'] = line.get('assignedMixers', []) or []
        line['mixerWaitTime'] = float(line.get('mixerWaitTime', 0) or 0)
        line['preOrderProduceTime'] = float(line.get('preOrderProduceTime', 0) or 0)
        line['specialProcessType'] = line.get('specialProcessType', '') or ''


def analyze_mixer_sharing(ctx: ApsContext, active_lines: list) -> None:
    """分析共享混合机的产线对，仅 assignedMixers 长度=1 的产线参与。"""
    mixer_to_lines = {}
    for line_id in active_lines:
        line = ctx.line_attrs[line_id]
        mixers = line.get('assignedMixers', [])
        if len(mixers) == 1 and mixers[0]:
            mixer_id = mixers[0]
            mixer_to_lines.setdefault(mixer_id, []).append(line_id)

    ctx.shared_mixer_line_pairs = []
    for mixer_id, lines in mixer_to_lines.items():
        if len(lines) >= 2:
            for i in range(len(lines)):
                for j in range(i + 1, len(lines)):
                    ctx.shared_mixer_line_pairs.append((lines[i], lines[j]))

    if ctx.shared_mixer_line_pairs:
        ctx.logger.info(f"检测到 {len(ctx.shared_mixer_line_pairs)} 对共享混合机的产线")


def create_wait_nodes(ctx: ApsContext, active_lines: list, line_feasible_rids: dict) -> None:
    """为共享混合机的产线创建等待节点。"""
    ctx.wait_nodes = {}
    for line_id in active_lines:
        line = ctx.line_attrs[line_id]
        mixers = line.get('assignedMixers', [])
        mixer_wait_time = line.get('mixerWaitTime', 0)

        if len(mixers) != 1 or mixer_wait_time <= 0:
            continue

        special_types = set()
        for rid in line_feasible_rids.get(line_id, []):
            t = get_effective_special_type(ctx.product_attrs[rid])
            if t:
                special_types.add(t)

        num_wait_nodes = min(len(special_types) + 1, constant.MAX_WAIT_NODES_PER_LINE)
        if num_wait_nodes <= 0:
            continue

        for idx in range(num_wait_nodes):
            wait_rid = f"{constant.WAIT_NODE_PREFIX}{line_id}_{idx}"
            wait_minutes = int(mixer_wait_time * 60)
            ctx.wait_nodes[wait_rid] = {
                'recordId': wait_rid,
                'productId': f'{constant.WAIT_NODE_PREFIX}PRODUCT_{line_id}_{idx}',
                'productName': f'{constant.WAIT_NODE_NAME_PREFIX}#{idx + 1}',
                'isDosing': 0,
                'isColoring': 0,
                'specialProcessType': '',
                'stage': None,
                'is_wait_node': True,
                'production_time': wait_minutes,
                'materialPriority': constant.WAIT_NODE_MATERIAL_PRIORITY,
                'line_id': line_id,
                'wait_index': idx,
            }


def precompute_production_times(ctx: ApsContext, all_rids, lines) -> None:
    """一次性预计算所有产品和前序产品的生产时间，并缓存。"""
    ctx.logger.info("开始预计算所有产品的生产时间...")
    for rid in all_rids:
        product = ctx.product_attrs[rid]
        for l in lines:
            time, priority = cal_production_time_from_ctx(ctx, product["productId"], l, product['plannedProductionQuantity'])
            ctx.prod_time_matrix[(rid, l)] = (time, priority)

    for l in lines:
        pre_info = ctx.line_attrs[l]
        pre_rid = pre_info['recordId']
        if (pre_rid, l) not in ctx.prod_time_matrix:
            pre_pid = pre_info['productId']
            pre_qty = pre_info.get('plannedProductionQuantity', 0)
            time, priority = cal_production_time_from_ctx(ctx, pre_pid, l, pre_qty)
            ctx.prod_time_matrix[(pre_rid, l)] = (time, priority)
    ctx.logger.info("生产时间预计算完成。")


def cal_production_time_from_ctx(ctx: ApsContext, product_id, line, order_quantity):
    """基于 ctx 的产能矩阵计算生产时间（纯函数语义）。"""
    if order_quantity == 0:
        return 0, 10
    capacity_related = ctx.product_product_line_capacity_matrix.get(
        product_id + '-' + line, {'capacity': 0})
    if capacity_related['capacity'] == 0:
        return constant.IS_UnScheduled, 10
    return math.ceil(order_quantity * 60 / capacity_related['capacity']), capacity_related['priority']


def precompute_switch_times(ctx: ApsContext, scheduled_rids, lines) -> None:
    """一次性预计算所有相关产品对的切换时间，并缓存。"""
    ctx.logger.info("开始预计算所有产品的切换时间...")
    all_nodes = set(scheduled_rids)
    pre_rids = {ctx.line_attrs[l]['recordId'] for l in lines}
    all_nodes.update(pre_rids)

    for r1 in all_nodes:
        for r2 in all_nodes:
            if r1 == r2:
                ctx.switch_time_matrix[(r1, r2)] = 0
            else:
                ctx.switch_time_matrix[(r1, r2)] = capacity.get_switch_time(ctx, r1, r2)
    ctx.logger.info("切换时间预计算完成。")


def precompute_switch_clusters(ctx: ApsContext, scheduled_rids) -> None:
    """计算零切换产品聚类：互切=0 的产品归为同一 cluster。

    直接用 switch_time_matrix 做并查集, 不按 (semi, dia) 预分桶。
    原分桶逻辑会把同半成品但 dia 不同的产品(如 5029297 dia=2.0 / 5070346 dia=2.5)
    分到不同桶, 即使互切=0 也无法合并, 导致应该同线的产品被拆分。
    """
    parent = {}

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a, b):
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[rb] = ra

    for rid in scheduled_rids:
        parent[rid] = rid

    # O(n²) 对所有产品对检查互切=0, n≤40 不构成瓶颈
    for i in range(len(scheduled_rids)):
        for j in range(i + 1, len(scheduled_rids)):
            st = ctx.switch_time_matrix.get(
                (scheduled_rids[i], scheduled_rids[j]), constant.IS_UnScheduled)
            if st == 0:
                union(scheduled_rids[i], scheduled_rids[j])

    root_members = defaultdict(list)
    for rid in scheduled_rids:
        root_members[find(rid)].append(rid)

    ctx.switch_clusters = {}
    cluster_count = 0
    for root, members in root_members.items():
        if len(members) >= 2:
            for rid in members:
                ctx.switch_clusters[rid] = cluster_count
            cluster_count += 1

    if cluster_count > 0:
        ctx.logger.info(f"检测到 {cluster_count} 个 switch_cluster, "
                        f"共 {len(ctx.switch_clusters)} 个产品")


def precompute_zero_groups(ctx: ApsContext, scheduled_rids) -> None:
    """零切换物料组: 双向互切=0 的连通分量 (union-find)。

    业务语义: 组内物料"不用切换", 必须同线连续生产, 组内按蛋白降序/粒径升序
    (d263ee2 后处理 + 2026-09-29 业务问题清单)。与 switch_clusters 的区别:
    只认双向 0, 避免单向 0 链把实际要换型的对拉进组。

    ctx.zero_groups = {gid: [rid, ...]} (仅 ≥2 的组)
    """
    parent = {}

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a, b):
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[rb] = ra

    for rid in scheduled_rids:
        parent[rid] = rid

    for i in range(len(scheduled_rids)):
        for j in range(i + 1, len(scheduled_rids)):
            a, b = scheduled_rids[i], scheduled_rids[j]
            st1 = ctx.switch_time_matrix.get((a, b), constant.IS_UnScheduled)
            st2 = ctx.switch_time_matrix.get((b, a), constant.IS_UnScheduled)
            if st1 == 0 and st2 == 0:
                union(a, b)

    root_members = defaultdict(list)
    for rid in scheduled_rids:
        root_members[find(rid)].append(rid)

    ctx.zero_groups = {}
    gid = 0
    for root, members in root_members.items():
        if len(members) >= 2:
            ctx.zero_groups[gid] = list(members)
            gid += 1

    if gid > 0:
        ctx.logger.info(f"零切换物料组: {gid} 个, 共 {sum(len(v) for v in ctx.zero_groups.values())} 个产品")
