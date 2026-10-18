"""capacity.py 纯计算函数单测（不含 DB）。

`get_product_capacity_list` 走 MySQL，不在这里测（有 DB 时用 tests/scratch/）；
本文件覆盖其余纯函数：生产时间、切换时间、优先级得分、产能兜底。
"""
import logging
import os
import sys
import unittest

_REP = os.path.dirname(os.path.abspath(__file__))
while not os.path.exists(os.path.join(_REP, 'constant.py')):
    _REP = os.path.dirname(_REP)
REPO_ROOT = _REP
sys.path.insert(0, REPO_ROOT)

import constant
constant.IS_PROD = False

from scheduler.context import ApsContext
from scheduler.capacity import (
    cal_production_time, get_switch_time, get_product_priority,
    fallback_capacity_from_semifinished,
)


def make_ctx(products=None, lines=None, switch=None, matrix=None, weights=None):
    pattrs = {}
    for rid, meta in (products or {}).items():
        pattrs[rid] = {'productId': rid, 'recordId': rid,
                       'materialPriority': 0, 'semiFinishedProductId': '',
                       'forthMaterialGroupId': '', 'dia': '', **meta}
    return ApsContext(
        product_attrs=pattrs,
        line_attrs=lines or {},
        switch_matrix=switch or {},
        version_id='T', trace_id='T', org_id='T',
        scheduling_strategy=[], mysql_config={},
        logger=logging.getLogger('test_capacity'),
        constraint_weights=weights or {k: 0 for k in constant.constraint_weights},
        product_product_line_capacity_matrix=matrix or {},
    )


class TestCalProductionTime(unittest.TestCase):

    def test_zero_quantity_returns_zero_time(self):
        """订单量 0 → 时间 0（不报 IS_UnScheduled）。

        ⚠ 优先级是**硬编码 10**，不读产能记录（见 cal_production_time 首行）。
        priority_weights['line'][10]=5，即零量产品拿到最低档产线分，
        而不是该产线的真实优先级。此处按现状钉住，改它需要同步回归。
        """
        ctx = make_ctx(matrix={'p1-L1': {'capacity': 60, 'min_quantity': 1, 'priority': 3}})
        t, pri = cal_production_time(ctx, 'p1', 'L1', 0)
        self.assertEqual(t, 0)
        self.assertEqual(pri, 10)
        self.assertEqual(constant.priority_weights['line'].get(10), 5,
                         "硬编码 10 应落在最低档, 否则零量产品会虚拿高产线分")

    def test_time_is_ceiled_hours_to_minutes(self):
        """时间 = ceil(量 × 60 / 产能)，向上取整。"""
        ctx = make_ctx(matrix={'p1-L1': {'capacity': 60, 'min_quantity': 1, 'priority': 1}})
        self.assertEqual(cal_production_time(ctx, 'p1', 'L1', 30)[0], 30)   # 30*60/60=30
        self.assertEqual(cal_production_time(ctx, 'p1', 'L1', 31)[0], 31)   # 31 整除
        ctx2 = make_ctx(matrix={'p1-L1': {'capacity': 100, 'min_quantity': 1, 'priority': 1}})
        self.assertEqual(cal_production_time(ctx2, 'p1', 'L1', 1)[0], 1)    # ceil(0.6)=1

    def test_missing_capacity_returns_is_unscheduled(self):
        """无产能记录 → IS_UnScheduled（=20000），这是漏排的根因信号。"""
        ctx = make_ctx()
        t, pri = cal_production_time(ctx, 'p1', 'L1', 10)
        self.assertEqual(t, constant.IS_UnScheduled)

    def test_zero_capacity_returns_is_unscheduled(self):
        """产能为 0 视作不可排（与缺记录同语义）。"""
        ctx = make_ctx(matrix={'p1-L1': {'capacity': 0, 'min_quantity': 0, 'priority': 1}})
        t, _ = cal_production_time(ctx, 'p1', 'L1', 10)
        self.assertEqual(t, constant.IS_UnScheduled)


class TestGetSwitchTime(unittest.TestCase):

    def test_same_rid_is_zero(self):
        ctx = make_ctx()
        self.assertEqual(get_switch_time(ctx, 'a', 'a'), 0)

    def test_reads_matrix(self):
        ctx = make_ctx(switch={'a': {'b': 45}})
        self.assertEqual(get_switch_time(ctx, 'a', 'b'), 45)

    def test_missing_returns_is_unscheduled(self):
        ctx = make_ctx(switch={'a': {'b': 45}})
        self.assertEqual(get_switch_time(ctx, 'a', 'z'), constant.IS_UnScheduled)

    def test_returned_value_is_int(self):
        ctx = make_ctx(switch={'a': {'b': 45.0}})
        self.assertIsInstance(get_switch_time(ctx, 'a', 'b'), int)


class TestGetProductPriority(unittest.TestCase):

    def test_weighted_sum_of_product_and_line_scores(self):
        """得分 = 产品优先级分×W_material + 产线优先级分×W_line。"""
        ctx = make_ctx(
            products={'r1': {'productId': 'p1', 'materialPriority': 1}},
            matrix={'p1-L1': {'capacity': 60, 'min_quantity': 1, 'priority': 2}},
            weights={'material_priority': 10, 'line_priority': 20})
        got = get_product_priority(ctx, 'r1', 'L1')
        expect = (constant.priority_weights['product'].get(1, 0) * 10
                  + constant.priority_weights['line'].get(2, 0) * 20)
        self.assertEqual(got, expect)

    def test_missing_line_capacity_defaults_to_priority_zero(self):
        """缺产能记录时 line_priority 取 0 —— 而 0 在 priority_weights['line'] 是**最高档**(100)。

        ⚠ 「缺数据 = 最高优先级」这个默认值偏宽松。实际影响有限：缺产能的产品在
        _build_feasible_rids 里已被 IS_UnScheduled 过滤，走不到打分。此处钉住现状。
        """
        ctx = make_ctx(
            products={'r1': {'productId': 'p1', 'materialPriority': 1}},
            weights={'material_priority': 10, 'line_priority': 20})
        self.assertEqual(constant.priority_weights['line'].get(0), 100)
        got = get_product_priority(ctx, 'r1', 'L1')
        self.assertEqual(
            got,
            constant.priority_weights['product'].get(1, 0) * 10
            + constant.priority_weights['line'].get(0, 0) * 20)

    def test_zero_weights_give_zero(self):
        ctx = make_ctx(
            products={'r1': {'productId': 'p1', 'materialPriority': 1}},
            matrix={'p1-L1': {'capacity': 60, 'min_quantity': 1, 'priority': 2}})
        self.assertEqual(get_product_priority(ctx, 'r1', 'L1'), 0)


class TestFallbackCapacity(unittest.TestCase):
    """产能兜底：无记录的产品继承同半成品 / 同小类+粒径 的产能。"""

    def _ctx(self):
        return make_ctx(
            products={
                'r_src': {'productId': 'p_src', 'semiFinishedProductId': 'S1',
                          'forthMaterialGroupId': 'G1', 'dia': '5'},
                'r_semi': {'productId': 'p_semi', 'semiFinishedProductId': 'S1',
                           'forthMaterialGroupId': 'G9', 'dia': '9'},
                'r_group': {'productId': 'p_group', 'semiFinishedProductId': 'S9',
                            'forthMaterialGroupId': 'G1', 'dia': '5'},
                'r_none': {'productId': 'p_none', 'semiFinishedProductId': 'S9',
                           'forthMaterialGroupId': 'G8', 'dia': '1'},
            },
            lines={'L1': {'recordId': 'PRE'}, 'L2': {'recordId': 'PRE'}},
            matrix={
                'p_src-L1': {'capacity': 60, 'min_quantity': 5, 'priority': 1},
                'p_src-L2': {'capacity': 30, 'min_quantity': 2, 'priority': 3},
            })

    def test_inherits_by_semifinished(self):
        ctx = self._ctx()
        fallback_capacity_from_semifinished(ctx)
        self.assertIn('p_semi-L1', ctx.product_product_line_capacity_matrix)
        self.assertEqual(ctx.product_product_line_capacity_matrix['p_semi-L1']['capacity'], 60)
        self.assertEqual(ctx.product_product_line_capacity_matrix['p_semi-L2']['priority'], 3)

    def test_inherits_by_group_and_dia_when_no_semifinished_match(self):
        ctx = self._ctx()
        fallback_capacity_from_semifinished(ctx)
        self.assertIn('p_group-L1', ctx.product_product_line_capacity_matrix)
        self.assertEqual(ctx.product_product_line_capacity_matrix['p_group-L1']['capacity'], 60)

    def test_no_match_leaves_product_without_capacity(self):
        ctx = self._ctx()
        fallback_capacity_from_semifinished(ctx)
        self.assertNotIn('p_none-L1', ctx.product_product_line_capacity_matrix)

    def test_existing_capacity_not_overwritten(self):
        ctx = self._ctx()
        ctx.product_product_line_capacity_matrix['p_semi-L1'] = {
            'capacity': 999, 'min_quantity': 1, 'priority': 9}
        fallback_capacity_from_semifinished(ctx)
        self.assertEqual(ctx.product_product_line_capacity_matrix['p_semi-L1']['capacity'], 999)

    def test_inherited_capacity_is_a_copy_not_alias(self):
        """继承的是拷贝；改继承结果不应污染源产品。"""
        ctx = self._ctx()
        fallback_capacity_from_semifinished(ctx)
        ctx.product_product_line_capacity_matrix['p_semi-L1']['capacity'] = -1
        self.assertEqual(ctx.product_product_line_capacity_matrix['p_src-L1']['capacity'], 60)

    def test_after_fallback_production_time_becomes_feasible(self):
        """兜底的最终目的：原本漏排的产品能算出生长时间。"""
        ctx = self._ctx()
        self.assertEqual(cal_production_time(ctx, 'p_semi', 'L1', 10)[0],
                         constant.IS_UnScheduled)
        fallback_capacity_from_semifinished(ctx)
        self.assertEqual(cal_production_time(ctx, 'p_semi', 'L1', 30)[0], 30)


if __name__ == '__main__':
    unittest.main(verbosity=2)
