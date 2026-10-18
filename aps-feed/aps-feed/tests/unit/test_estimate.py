"""estimate.estimate_production_load 单测（/estimate-task 的唯一实现）。

只 patch DB 产能加载（`capacity.get_product_capacity_list`），其余走真实路径：
生产时间预计算、贪心分配、加班/汇总都按实际逻辑跑。
"""
import logging
import os
import sys
import unittest
from unittest.mock import patch

_REP = os.path.dirname(os.path.abspath(__file__))
while not os.path.exists(os.path.join(_REP, 'constant.py')):
    _REP = os.path.dirname(_REP)
REPO_ROOT = _REP
sys.path.insert(0, REPO_ROOT)

import constant
constant.IS_PROD = False

from scheduler.context import ApsContext
from scheduler.estimate import estimate_production_load


def make_ctx(products, lines, matrix, switch=None):
    """products: {rid: {productId, materialPriority, plannedProductionQuantity, ...}}"""
    pattrs = {}
    for rid, meta in products.items():
        pattrs[rid] = {'recordId': rid, 'materialPriority': '1',
                       'plannedProductionQuantity': 10, **meta}
    lattrs = {}
    for lid, meta in lines.items():
        lattrs[lid] = {'recordId': f'PRE-{lid}', 'productId': f'PRE-{lid}',
                       'plannedProductionQuantity': 0, 'productionLineName': lid,
                       'lineRemainingTime': 24.0, **meta}
    # get_switch_time 读**原始** switch_matrix（不是预计算的 switch_time_matrix）。
    # 贪心每次分配后会把 last_rid 换成该产品，再查 产品→产品 的切换时间；
    # 查不到就把整条线跳过。真实数据里 switch_matrix 是全的，这里也补全，
    # 默认 0（免切换），个别用例再覆盖。
    ids = list(pattrs) + [m['recordId'] for m in lattrs.values()]
    sw = {a: {b: 0 for b in ids if b != a} for a in ids}
    for r1, row in (switch or {}).items():
        sw.setdefault(r1, {}).update(row)

    return ApsContext(
        product_attrs=pattrs, line_attrs=lattrs,
        switch_matrix=sw,
        version_id='T', trace_id='T', org_id='T',
        scheduling_strategy=[], mysql_config={},
        logger=logging.getLogger('test_estimate'),
        product_product_line_capacity_matrix=matrix,
    )


def run(ctx):
    with patch('scheduler.capacity.get_product_capacity_list', return_value=True):
        return estimate_production_load(ctx)


class TestEstimateProductionLoad(unittest.TestCase):

    def test_returns_none_when_capacity_lookup_fails(self):
        ctx = make_ctx({'r1': {'productId': 'p1'}}, {'L1': {}}, {})
        with patch('scheduler.capacity.get_product_capacity_list', return_value=False):
            self.assertIsNone(estimate_production_load(ctx))

    def test_summary_has_required_fields(self):
        ctx = make_ctx({'r1': {'productId': 'p1'}}, {'L1': {}},
                       {'p1-L1': {'capacity': 60, 'min_quantity': 1, 'priority': 1}})
        res = run(ctx)
        for k in ('lineEstimates', 'unassignedCount', 'unassignedProductIds',
                  'maxOvertimeMinutes', 'isAnyOvertime', 'totalOvertimeMinutes'):
            self.assertIn(k, res)
        line = res['lineEstimates'][0]
        for k in ('productionLineId', 'productionLineName', 'estimatedDurationMinutes',
                  'limitDurationMinutes', 'minFeasibleDurationMinutes',
                  'maxFeasibleDurationMinutes', 'isOvertime', 'overtimeMinutes', 'taskCount'):
            self.assertIn(k, line)

    def test_low_priority_4_excluded_from_estimate(self):
        """materialPriority="4" 是低优先级，不参与预估。"""
        ctx = make_ctx(
            {'r1': {'productId': 'p1', 'materialPriority': '4'},
             'r2': {'productId': 'p2', 'materialPriority': '1'}},
            {'L1': {}},
            {'p1-L1': {'capacity': 60, 'min_quantity': 1, 'priority': 1},
             'p2-L1': {'capacity': 60, 'min_quantity': 1, 'priority': 1}})
        res = run(ctx)
        self.assertEqual(res['lineEstimates'][0]['taskCount'], 1)
        self.assertEqual(res['unassignedCount'], 0)

    def test_all_assigned_when_capacity_sufficient(self):
        ctx = make_ctx(
            {f'r{i}': {'productId': f'p{i}'} for i in range(3)},
            {'L1': {}, 'L2': {}},
            {f'p{i}-L{j}': {'capacity': 60, 'min_quantity': 1, 'priority': 1}
             for i in range(3) for j in (1, 2)})
        res = run(ctx)
        self.assertEqual(res['unassignedCount'], 0)
        self.assertEqual(sum(l['taskCount'] for l in res['lineEstimates']), 3)

    def test_unassigned_when_no_capable_line(self):
        """无任何产线有产能 → 进 unassigned，不静默丢。"""
        ctx = make_ctx(
            {'r1': {'productId': 'p1'}, 'r2': {'productId': 'p2'}},
            {'L1': {}},
            {'p2-L1': {'capacity': 60, 'min_quantity': 1, 'priority': 1}})
        res = run(ctx)
        self.assertEqual(res['unassignedCount'], 1)
        self.assertEqual(res['unassignedProductIds'], ['r1'])

    def test_load_reflects_production_time(self):
        """负载 = 生产量×60/产能 的向上取整之和。"""
        ctx = make_ctx(
            {'r1': {'productId': 'p1', 'plannedProductionQuantity': 30}},
            {'L1': {}},
            {'p1-L1': {'capacity': 60, 'min_quantity': 1, 'priority': 1}})
        res = run(ctx)
        self.assertEqual(res['lineEstimates'][0]['estimatedDurationMinutes'], 30.0)

    def test_overtime_flagged_when_load_exceeds_remaining_time(self):
        """lineRemainingTime 是**小时**；超出则 isOvertime 且累计加班分钟。"""
        ctx = make_ctx(
            {'r1': {'productId': 'p1', 'plannedProductionQuantity': 60}},  # 60 分钟
            {'L1': {'lineRemainingTime': 0.5}},                            # 30 分钟
            {'p1-L1': {'capacity': 60, 'min_quantity': 1, 'priority': 1}})
        res = run(ctx)
        line = res['lineEstimates'][0]
        self.assertTrue(line['isOvertime'])
        self.assertEqual(line['overtimeMinutes'], 30.0)
        self.assertTrue(res['isAnyOvertime'])
        self.assertEqual(res['totalOvertimeMinutes'], 30.0)
        self.assertEqual(res['maxOvertimeMinutes'], 30.0)

    def test_no_overtime_when_within_limit(self):
        ctx = make_ctx(
            {'r1': {'productId': 'p1', 'plannedProductionQuantity': 10}},
            {'L1': {'lineRemainingTime': 24.0}},
            {'p1-L1': {'capacity': 60, 'min_quantity': 1, 'priority': 1}})
        res = run(ctx)
        self.assertFalse(res['lineEstimates'][0]['isOvertime'])
        self.assertFalse(res['isAnyOvertime'])
        self.assertEqual(res['maxOvertimeMinutes'], 0)

    def test_feasible_band_is_relative_to_estimate(self):
        """min/max 可行区间按估算负载 ±10% / +30 分钟给出。"""
        ctx = make_ctx(
            {'r1': {'productId': 'p1', 'plannedProductionQuantity': 60}},
            {'L1': {}},
            {'p1-L1': {'capacity': 60, 'min_quantity': 1, 'priority': 1}})
        line = run(ctx)['lineEstimates'][0]
        self.assertEqual(line['estimatedDurationMinutes'], 60.0)
        self.assertEqual(line['minFeasibleDurationMinutes'], 54.0)   # 60*0.9
        self.assertEqual(line['maxFeasibleDurationMinutes'], 96.0)   # 60*1.1+30

    def test_empty_line_has_zero_band(self):
        """空线不给假的可行区间（否则会误导业务以为有 30 分钟弹性）。"""
        ctx = make_ctx({}, {'L1': {}}, {})
        line = run(ctx)['lineEstimates'][0]
        self.assertEqual(line['estimatedDurationMinutes'], 0.0)
        self.assertEqual(line['minFeasibleDurationMinutes'], 0.0)
        self.assertEqual(line['maxFeasibleDurationMinutes'], 0.0)

    def test_longer_products_assigned_first(self):
        """贪心按平均耗时降序分配——长单先占资源，避免长单最后无处可去。"""
        ctx = make_ctx(
            {'r_long': {'productId': 'p_long', 'plannedProductionQuantity': 120},
             'r_short': {'productId': 'p_short', 'plannedProductionQuantity': 10}},
            {'L1': {}},
            {'p_long-L1': {'capacity': 60, 'min_quantity': 1, 'priority': 1},
             'p_short-L1': {'capacity': 60, 'min_quantity': 1, 'priority': 1}})
        res = run(ctx)
        self.assertEqual(res['unassignedCount'], 0)
        self.assertEqual(res['lineEstimates'][0]['taskCount'], 2)

    def test_line_ids_and_names_reported(self):
        ctx = make_ctx({'r1': {'productId': 'p1'}},
                       {'L1': {'productionLineName': '1500CX01'}},
                       {'p1-L1': {'capacity': 60, 'min_quantity': 1, 'priority': 1}})
        line = run(ctx)['lineEstimates'][0]
        self.assertEqual(line['productionLineId'], 'L1')
        self.assertEqual(line['productionLineName'], '1500CX01')


if __name__ == '__main__':
    unittest.main(verbosity=2)
