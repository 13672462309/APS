"""Aps facade 兼容性测试：旧的辅助方法仍委托到 scheduler 模块。"""
import json
import os
import sys
import unittest
from unittest.mock import patch

# 仓库根：向上查找 constant.py，避免脚本挪目录后失效
_REP = os.path.dirname(os.path.abspath(__file__))
while not os.path.exists(os.path.join(_REP, 'constant.py')):
    _REP = os.path.dirname(_REP)
REPO_ROOT = _REP
sys.path.insert(0, REPO_ROOT)

import constant
from aps import Aps


class TestApsFacadeCompatibility(unittest.TestCase):
    def setUp(self):
        plan = [{
            'recordId': 'R1', 'productId': 'P1', 'productName': 'P1',
            'plannedProductionQuantity': 10, 'materialPriority': '1',
            'earliestStartTime': '0', 'latestCompletionTime': '24',
            'proteinGradeLevel': '0', 'productionParticleSize': '3.8',
            'packageType': '01', 'semiFinishedProductId': 'S1',
            'semiFinishedProductName': 'S1', 'thirdMaterialGroupId': 'G1',
            'thirdMaterialGroupDesc': 'G1', 'forthMaterialGroupId': 'G2',
            'forthMaterialGroupDesc': 'G2', 'aspectRatio': '', 'dia': '3.8',
            'unitElectricityConsumption': 1,
        }]
        lines = [{
            'productionLineId': 'L1', 'productionLineName': 'L1',
            'lineRemainingTime': 24, 'recordId': 'PRE1', 'productId': 'PRE1',
            'productName': 'PRE1', 'allowOverTime': '0',
            'packageType': '01', 'semiFinishedProductId': 'SP',
            'semiFinishedProductName': 'SP', 'thirdMaterialGroupId': 'G1',
            'thirdMaterialGroupDesc': 'G1', 'forthMaterialGroupId': 'G2',
            'forthMaterialGroupDesc': 'G2', 'productionParticleSize': '3.8',
            'aspectRatio': '', 'dia': '3.8', 'plannedProductionQuantity': 0,
            'assignedMixers': ['M1'], 'mixerWaitTime': 0.5,
            'electricityPrice': [],
        }]
        self.aps = Aps(plan, lines, {}, 'V', 'T', '1130', [])

    def test_old_preprocessing_helpers_delegate(self):
        """旧测试调用的预处理辅助方法仍能工作并同步 facade 状态。"""
        self.assertEqual(
            self.aps._get_effective_special_type(self.aps.product_attrs['R1']), '')
        self.aps._analyze_mixer_sharing(['L1'])
        self.assertEqual(self.aps.shared_mixer_line_pairs, [])
        self.aps._create_wait_nodes(['L1'], {'L1': ['R1']})
        self.assertTrue(self.aps.wait_nodes)

    def test_old_capacity_helpers_delegate(self):
        """旧调用方的产能/预计算辅助方法仍能工作。"""
        matrix = {'P1-L1': {'capacity': 10, 'min_quantity': 0, 'priority': 0}}
        self.aps.ctx.product_product_line_capacity_matrix = matrix
        self.aps.product_product_line_capacity_matrix = matrix
        with patch('scheduler.capacity.get_product_capacity_list', return_value=True):
            self.assertTrue(self.aps.get_product_capacity_list())
        self.aps._precompute_production_times(['R1'], ['L1'])
        self.aps._precompute_switch_times(['R1'], ['L1'])
        self.assertEqual(self.aps.cal_production_time('P1', 'L1', 10)[0], 60)
        self.assertEqual(self.aps.get_switch_time('R1', 'R1'), 0)

    def test_changeover_penalty_resets_before_each_schedule(self):
        """同一 Aps 复用时，本次失败不会沿用上次成功的 penalty。"""
        self.aps.ctx.changeover_time_penalty = 77
        with patch('scheduler.model_build.schedule', return_value={}):
            self.aps.production_scheduling()
        self.assertEqual(self.aps.ctx.changeover_time_penalty, 0)


if __name__ == '__main__':
    unittest.main()
