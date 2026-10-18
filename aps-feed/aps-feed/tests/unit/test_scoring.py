"""scoring.calc_electricity_cost_for_slot 单测（分段电价积分）。

电耗是目标函数里唯一的惩罚项（PR0011），积分算错会系统性偏移产线选择。
这里钉住分段重叠、跨段、退化输入三种情形。
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
from scheduler.scoring import calc_electricity_cost_for_slot


def make_ctx(prices):
    return ApsContext(
        product_attrs={}, line_attrs={},
        switch_matrix={}, version_id='T', trace_id='T', org_id='T',
        scheduling_strategy=[], mysql_config={},
        logger=logging.getLogger('test_scoring'),
        line_electricity_prices=prices,
    )


class TestElectricityCostForSlot(unittest.TestCase):

    def test_single_segment(self):
        """单段电价: 成本 = 量 × 单耗 × 电价。"""
        ctx = make_ctx({'L1': [(0, 1440, 2.0)]})
        # 120 分钟 = 2 小时, 10 件, 单耗 3 → 10*3*2.0 = 60
        self.assertAlmostEqual(
            calc_electricity_cost_for_slot(ctx, 'L1', 0, 120, 10, 3), 60.0)

    def test_two_segments_weighted_by_overlap(self):
        """跨两段时按重叠分钟加权平均电价。"""
        ctx = make_ctx({'L1': [(0, 60, 1.0), (60, 120, 3.0)]})
        # 0~120 各占 60 分 → 均价 (60*1 + 60*3)/120 = 2.0 → 10*3*2 = 60
        self.assertAlmostEqual(
            calc_electricity_cost_for_slot(ctx, 'L1', 0, 120, 10, 3), 60.0)

    def test_partial_overlap_only_counts_covered_minutes(self):
        """时段只覆盖部分区间时，只计重叠分钟。"""
        ctx = make_ctx({'L1': [(0, 30, 4.0)]})          # 只覆盖 0~30
        # 0~120 共 120 分钟, 加权 = 30*4 = 120, 均价 = 120/120 = 1.0 → 10*3*1 = 30
        self.assertAlmostEqual(
            calc_electricity_cost_for_slot(ctx, 'L1', 0, 120, 10, 3), 30.0)

    def test_segment_entirely_outside_contributes_nothing(self):
        ctx = make_ctx({'L1': [(200, 300, 9.0)]})
        self.assertAlmostEqual(
            calc_electricity_cost_for_slot(ctx, 'L1', 0, 120, 10, 3), 0.0)

    def test_no_segments_returns_zero(self):
        ctx = make_ctx({})
        self.assertEqual(calc_electricity_cost_for_slot(ctx, 'L1', 0, 120, 10, 3), 0.0)

    def test_zero_or_negative_qty_uec_returns_zero(self):
        ctx = make_ctx({'L1': [(0, 1440, 2.0)]})
        self.assertEqual(calc_electricity_cost_for_slot(ctx, 'L1', 0, 120, 0, 3), 0.0)
        self.assertEqual(calc_electricity_cost_for_slot(ctx, 'L1', 0, 120, -5, 3), 0.0)
        self.assertEqual(calc_electricity_cost_for_slot(ctx, 'L1', 0, 120, 10, 0), 0.0)

    def test_non_positive_duration_returns_zero(self):
        ctx = make_ctx({'L1': [(0, 1440, 2.0)]})
        self.assertEqual(calc_electricity_cost_for_slot(ctx, 'L1', 120, 120, 10, 3), 0.0)
        self.assertEqual(calc_electricity_cost_for_slot(ctx, 'L1', 120, 60, 10, 3), 0.0)

    def test_open_ended_slot_uses_segment_end(self):
        """时段边界用 min(end_min, seg_end) 截断，不外推。"""
        ctx = make_ctx({'L1': [(0, 30, 2.0)]})
        # start=0 end=60, 重叠仅 0~30 → 均价 (30*2)/60 = 1.0
        self.assertAlmostEqual(
            calc_electricity_cost_for_slot(ctx, 'L1', 0, 60, 10, 3), 30.0)

    def test_valley_price_cheaper_than_peak_for_same_slot_shape(self):
        """谷电时段同样产量应更便宜——这是谷电调度能生效的前提。"""
        peak = make_ctx({'L1': [(0, 1440, 5.0)]})
        valley = make_ctx({'L1': [(0, 1440, 1.0)]})
        self.assertLess(
            calc_electricity_cost_for_slot(valley, 'L1', 0, 120, 10, 3),
            calc_electricity_cost_for_slot(peak, 'L1', 0, 120, 10, 3))


if __name__ == '__main__':
    unittest.main(verbosity=2)
