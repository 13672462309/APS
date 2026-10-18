"""result_builder 排查辅助函数单测（`get_feasible_lines_for_product` / `analyze_infeasibility_reason`）。

这两个函数只在「产品没排上」时用来生成给业务看的解释文字。它们不参与求解，
但漏排查因全靠它们——文案错就是错诊断。这里钉住分支与截断行为。
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
from scheduler.result_builder import get_feasible_lines_for_product


def make_ctx(products=None, lines=None, prod_time=None, switch=None):
    pattrs = {}
    for rid, meta in (products or {}).items():
        pattrs[rid] = {'productId': rid, 'recordId': rid,
                       'materialPriority': '', 'time_window': {}, **meta}
    lattrs = {}
    for lid, meta in (lines or {}).items():
        lattrs[lid] = {'recordId': 'PRE', 'productionLineName': lid, **meta}
    return ApsContext(
        product_attrs=pattrs, line_attrs=lattrs,
        switch_matrix=switch or {},
        version_id='T', trace_id='T', org_id='T',
        scheduling_strategy=[], mysql_config={},
        logger=logging.getLogger('test_result_builder'),
        prod_time_matrix=prod_time or {},
        switch_time_matrix=switch or {},
    )


class TestFeasibleLines(unittest.TestCase):

    def test_unknown_product(self):
        ctx = make_ctx()
        self.assertEqual(get_feasible_lines_for_product(ctx, 'nope'), "产品信息不存在")

    def test_no_feasible_line(self):
        """所有线都 IS_UnScheduled → 明确说无可排产产线。"""
        ctx = make_ctx(
            products={'r1': {}},
            lines={'L1': {}},
            prod_time={('r1', 'L1'): (constant.IS_UnScheduled, 10)},
            switch={('PRE', 'r1'): 10})
        self.assertEqual(get_feasible_lines_for_product(ctx, 'r1'), "无可排产产线")

    def test_unfeasible_when_pre_switch_missing(self):
        """生产时间可行但与产线前序的切换时间缺失 → 也不算可行。"""
        ctx = make_ctx(
            products={'r1': {}},
            lines={'L1': {}},
            prod_time={('r1', 'L1'): (30, 1)},
            switch={('PRE', 'r1'): constant.IS_UnScheduled})
        self.assertEqual(get_feasible_lines_for_product(ctx, 'r1'), "无可排产产线")

    def test_reports_time_plus_switch(self):
        ctx = make_ctx(
            products={'r1': {}},
            lines={'L1': {'productionLineName': '产线一'}},
            prod_time={('r1', 'L1'): (30, 1)},
            switch={('PRE', 'r1'): 10})
        self.assertEqual(get_feasible_lines_for_product(ctx, 'r1'), "产线一(40分)")

    def test_line_name_truncated_to_4_chars(self):
        ctx = make_ctx(
            products={'r1': {}},
            lines={'L1': {'productionLineName': '1500CX01生产线'}},
            prod_time={('r1', 'L1'): (30, 1)},
            switch={('PRE', 'r1'): 10})
        self.assertEqual(get_feasible_lines_for_product(ctx, 'r1'), "1500(40分)")

    def test_more_than_3_lines_truncated_with_count(self):
        lines = {f'L{i}': {'productionLineName': f'线{i}'} for i in range(5)}
        prod_time = {('r1', f'L{i}'): (10, 1) for i in range(5)}
        switch = {('PRE', 'r1'): 5}
        ctx = make_ctx(products={'r1': {}}, lines=lines,
                       prod_time=prod_time, switch=switch)
        got = get_feasible_lines_for_product(ctx, 'r1')
        self.assertIn("等5条", got, got)
        self.assertEqual(got.count('('), 3, f"只应列出 3 条: {got}")

    def test_three_lines_not_truncated(self):
        lines = {f'L{i}': {'productionLineName': f'线{i}'} for i in range(3)}
        prod_time = {('r1', f'L{i}'): (10, 1) for i in range(3)}
        ctx = make_ctx(products={'r1': {}}, lines=lines,
                       prod_time=prod_time, switch={('PRE', 'r1'): 5})
        got = get_feasible_lines_for_product(ctx, 'r1')
        self.assertNotIn("等", got)
        self.assertEqual(got.count('('), 3)

    def test_uses_precomputed_switch_time_matrix(self):
        """读的是预计算的 switch_time_matrix，不是原始 switch_matrix。"""
        ctx = make_ctx(
            products={'r1': {}},
            lines={'L1': {}},
            prod_time={('r1', 'L1'): (30, 1)})
        ctx.switch_time_matrix = {('PRE', 'r1'): 7}
        self.assertEqual(get_feasible_lines_for_product(ctx, 'r1'), "L1(37分)")


if __name__ == '__main__':
    unittest.main(verbosity=2)
