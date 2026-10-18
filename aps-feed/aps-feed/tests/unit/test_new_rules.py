"""智能排程算法改造 - 单元测试

测试 PR0012/PR0013/PR0014 规则的逻辑正确性
注意：集成测试（含排产验证）需要数据库连接，使用 data/ 中的真实测试数据
"""
import json
import os
import sys
import unittest

# 仓库根：向上查找 constant.py，避免脚本挪目录后失效
_REP = os.path.dirname(os.path.abspath(__file__))
while not os.path.exists(os.path.join(_REP, 'constant.py')):
    _REP = os.path.dirname(_REP)
REPO_ROOT = _REP
sys.path.insert(0, REPO_ROOT)

import constant
constant.IS_PROD = False

from logging_config import setup_logging
from flask import Flask

app = Flask(__name__)
app.config['JSON_AS_ASCII'] = False
setup_logging(app)


class TestPreprocessing(unittest.TestCase):
    """测试预处理逻辑（无需数据库）"""

    def setUp(self):
        self.plan_data = [
            {"recordId": "R001", "productId": "P001", "productName": "普通A",
             "plannedProductionQuantity": 100, "materialPriority": "1",
             "earliestStartTime": "0", "latestCompletionTime": "24",
             "proteinGradeLevel": "0", "productionParticleSize": "3.8",
             "packageType": "01", "semiFinishedProductId": "S001",
             "semiFinishedProductName": "半成品A", "thirdMaterialGroupId": "50101",
             "thirdMaterialGroupDesc": "饲料", "forthMaterialGroupId": "5010123",
             "forthMaterialGroupDesc": "禽料", "aspectRatio": "", "dia": "3.8",
             "unitElectricityConsumption": 10},
            {"recordId": "R002", "productId": "P002", "productName": "加色B",
             "plannedProductionQuantity": 80, "materialPriority": "1",
             "earliestStartTime": "0", "latestCompletionTime": "24",
             "proteinGradeLevel": "0", "productionParticleSize": "3.8",
             "packageType": "01", "semiFinishedProductId": "S002",
             "semiFinishedProductName": "半成品B", "thirdMaterialGroupId": "50101",
             "thirdMaterialGroupDesc": "饲料", "forthMaterialGroupId": "5010123",
             "forthMaterialGroupDesc": "禽料", "aspectRatio": "", "dia": "3.8",
             "unitElectricityConsumption": 10,
             "isDosing": 0, "isColoring": 1, "specialProcessType": ""},
            {"recordId": "R003", "productId": "P003", "productName": "加药C",
             "plannedProductionQuantity": 60, "materialPriority": "1",
             "earliestStartTime": "0", "latestCompletionTime": "24",
             "proteinGradeLevel": "0", "productionParticleSize": "3.8",
             "packageType": "01", "semiFinishedProductId": "S003",
             "semiFinishedProductName": "半成品C", "thirdMaterialGroupId": "50101",
             "thirdMaterialGroupDesc": "饲料", "forthMaterialGroupId": "5010123",
             "forthMaterialGroupDesc": "禽料", "aspectRatio": "", "dia": "3.8",
             "unitElectricityConsumption": 10,
             "isDosing": 1, "isColoring": 0, "specialProcessType": ""},
            {"recordId": "R004", "productId": "P004", "productName": "加色加药D",
             "plannedProductionQuantity": 40, "materialPriority": "1",
             "earliestStartTime": "0", "latestCompletionTime": "24",
             "proteinGradeLevel": "0", "productionParticleSize": "3.8",
             "packageType": "01", "semiFinishedProductId": "S004",
             "semiFinishedProductName": "半成品D", "thirdMaterialGroupId": "50101",
             "thirdMaterialGroupDesc": "饲料", "forthMaterialGroupId": "5010123",
             "forthMaterialGroupDesc": "禽料", "aspectRatio": "", "dia": "3.8",
             "unitElectricityConsumption": 10,
             "isDosing": 1, "isColoring": 1, "specialProcessType": ""},
            {"recordId": "R005", "productId": "P005", "productName": "种猪料E",
             "plannedProductionQuantity": 50, "materialPriority": "1",
             "earliestStartTime": "0", "latestCompletionTime": "24",
             "proteinGradeLevel": "0", "productionParticleSize": "3.8",
             "packageType": "01", "semiFinishedProductId": "S005",
             "semiFinishedProductName": "半成品E", "thirdMaterialGroupId": "50101",
             "thirdMaterialGroupDesc": "饲料", "forthMaterialGroupId": "5010303",
             "forthMaterialGroupDesc": "种猪料", "aspectRatio": "", "dia": "3.8",
             "unitElectricityConsumption": 10,
             "isDosing": 0, "isColoring": 0, "specialProcessType": "种猪料"},
        ]
        self.line_data = [
            {"productionLineId": "L1", "productionLineName": "一号线",
             "lineRemainingTime": 24, "recordId": "PRE001", "productId": "PRE001",
             "productName": "前序1", "allowOverTime": "0",
             "packageType": "01", "semiFinishedProductId": "S_PRE",
             "semiFinishedProductName": "前序半成品", "thirdMaterialGroupId": "50101",
             "thirdMaterialGroupDesc": "饲料", "forthMaterialGroupId": "5010123",
             "forthMaterialGroupDesc": "禽料", "productionParticleSize": "3.8",
             "aspectRatio": "", "dia": "3.8", "plannedProductionQuantity": 0,
             "assignedMixers": ["M1"], "mixerWaitTime": 0.5,
             "electricityPrice": [{"electricityStartTime": "0", "electricityEndTime": "24", "ePrice": 0.5}]},
            {"productionLineId": "L2", "productionLineName": "二号线",
             "lineRemainingTime": 24, "recordId": "PRE002", "productId": "PRE002",
             "productName": "前序2", "allowOverTime": "0",
             "packageType": "01", "semiFinishedProductId": "S_PRE2",
             "semiFinishedProductName": "前序半成品2", "thirdMaterialGroupId": "50101",
             "thirdMaterialGroupDesc": "饲料", "forthMaterialGroupId": "5010123",
             "forthMaterialGroupDesc": "禽料", "productionParticleSize": "3.8",
             "aspectRatio": "", "dia": "3.8", "plannedProductionQuantity": 0,
             "assignedMixers": ["M1"], "mixerWaitTime": 0.5,
             "electricityPrice": [{"electricityStartTime": "0", "electricityEndTime": "24", "ePrice": 0.5}]},
            {"productionLineId": "L3", "productionLineName": "三号线",
             "lineRemainingTime": 24, "recordId": "PRE003", "productId": "PRE003",
             "productName": "前序3", "allowOverTime": "0",
             "packageType": "01", "semiFinishedProductId": "S_PRE3",
             "semiFinishedProductName": "前序半成品3", "thirdMaterialGroupId": "50101",
             "thirdMaterialGroupDesc": "饲料", "forthMaterialGroupId": "5010123",
             "forthMaterialGroupDesc": "禽料", "productionParticleSize": "3.8",
             "aspectRatio": "", "dia": "3.8", "plannedProductionQuantity": 0,
             "assignedMixers": [], "mixerWaitTime": 0,
             "electricityPrice": [{"electricityStartTime": "0", "electricityEndTime": "24", "ePrice": 0.5}]},
        ]

    def build_aps(self):
        from aps import Aps
        with app.app_context():
            return Aps(self.plan_data, self.line_data, {}, "V1", "T1", "1130", [])

    def test_stage_assignment(self):
        """测试1: Stage 分配"""
        aps = self.build_aps()
        self.assertEqual(aps.product_attrs['R001']['stage'], 1)
        self.assertEqual(aps.product_attrs['R002']['stage'], 2)
        self.assertEqual(aps.product_attrs['R003']['stage'], 3)
        self.assertEqual(aps.product_attrs['R004']['stage'], 4)
        self.assertEqual(aps.product_attrs['R005']['stage'], 1)
        print("  ✓ Stage 分配正确")

    def test_default_values(self):
        """测试2: 字段缺失时默认值"""
        plans_no_new = [
            {"recordId": "R001", "productId": "P001", "productName": "X",
             "plannedProductionQuantity": 100, "materialPriority": "1",
             "earliestStartTime": "0", "latestCompletionTime": "24",
             "proteinGradeLevel": "0", "productionParticleSize": "3.8",
             "packageType": "01", "semiFinishedProductId": "S001",
             "semiFinishedProductName": "半成品", "thirdMaterialGroupId": "50101",
             "thirdMaterialGroupDesc": "饲料", "forthMaterialGroupId": "5010123",
             "forthMaterialGroupDesc": "禽料", "aspectRatio": "", "dia": "3.8",
             "unitElectricityConsumption": 10}]
        from aps import Aps
        with app.app_context():
            aps = Aps(plans_no_new, self.line_data[:1], {}, "V1", "T1", "1130", [])
        p = aps.product_attrs['R001']
        self.assertEqual(p['isDosing'], 0)
        self.assertEqual(p['isColoring'], 0)
        self.assertEqual(p['specialProcessType'], '')
        self.assertEqual(p['stage'], 1)
        print("  ✓ 默认值正确")

    def test_effective_special_type(self):
        """测试3: 有效特殊类型计算"""
        aps = self.build_aps()
        self.assertEqual(aps._get_effective_special_type(aps.product_attrs['R001']), '')
        self.assertEqual(aps._get_effective_special_type(aps.product_attrs['R002']), constant.SPECIAL_TYPE_COLORING)
        self.assertEqual(aps._get_effective_special_type(aps.product_attrs['R003']), constant.SPECIAL_TYPE_DOSING)
        self.assertEqual(aps._get_effective_special_type(aps.product_attrs['R005']), '种猪料')
        print("  ✓ 有效特殊类型正确")

    def test_mixer_sharing(self):
        """测试4: 混合机共享检测"""
        aps = self.build_aps()
        aps._analyze_mixer_sharing(['L1', 'L2', 'L3'])
        self.assertEqual(len(aps.shared_mixer_line_pairs), 1)
        self.assertIn(('L1', 'L2'), aps.shared_mixer_line_pairs)
        print(f"  ✓ 共享产线对: {aps.shared_mixer_line_pairs}")

    def test_wait_node_creation(self):
        """测试5: 等待节点创建"""
        aps = self.build_aps()
        aps._analyze_mixer_sharing(['L1', 'L2', 'L3'])
        line_feasible = {'L1': ['R005'], 'L2': [], 'L3': []}
        aps._create_wait_nodes(['L1', 'L2', 'L3'], line_feasible)

        self.assertGreater(len(aps.wait_nodes), 0)
        for rid, attrs in aps.wait_nodes.items():
            self.assertTrue(attrs['is_wait_node'])
            self.assertIsNone(attrs['stage'])
            self.assertGreater(attrs['production_time'], 0)
            self.assertIn('WAIT_', rid)
        print(f"  ✓ 等待节点数: {len(aps.wait_nodes)}")

    def test_no_wait_for_no_mixer(self):
        """测试6: 无混合机的产线不创建等待节点"""
        aps = self.build_aps()
        # L3 has empty assignedMixers, should not create wait nodes
        aps._analyze_mixer_sharing(['L3'])
        line_feasible = {'L3': ['R001', 'R002']}
        aps._create_wait_nodes(['L3'], line_feasible)
        self.assertEqual(len(aps.wait_nodes), 0)
        print("  ✓ 无混合机产线无等待节点")

    def test_no_wait_for_multi_mixer(self):
        """测试7: 多混合机产线不创建等待节点"""
        plans = json.loads(json.dumps(self.plan_data[:1]))
        lines = [{
            "productionLineId": "L4", "productionLineName": "四号线",
            "lineRemainingTime": 24, "recordId": "PRE004", "productId": "PRE004",
            "productName": "前序4", "allowOverTime": "0",
            "packageType": "01", "semiFinishedProductId": "S_PRE4",
            "semiFinishedProductName": "前序半成品4", "thirdMaterialGroupId": "50101",
            "thirdMaterialGroupDesc": "饲料", "forthMaterialGroupId": "5010123",
            "forthMaterialGroupDesc": "禽料", "productionParticleSize": "3.8",
            "aspectRatio": "", "dia": "3.8", "plannedProductionQuantity": 0,
            "assignedMixers": ["M1", "M2"], "mixerWaitTime": 0.5,
            "electricityPrice": [{"electricityStartTime": "0", "electricityEndTime": "24", "ePrice": 0.5}]}]
        from aps import Aps
        with app.app_context():
            aps = Aps(plans, lines, {}, "V1", "T1", "1130", [])
        aps._analyze_mixer_sharing(['L4'])
        self.assertEqual(len(aps.shared_mixer_line_pairs), 0)
        aps._create_wait_nodes(['L4'], {'L4': ['R001']})
        self.assertEqual(len(aps.wait_nodes), 0)
        print("  ✓ 多混合机产线无等待节点")


if __name__ == '__main__':
    unittest.main(verbosity=2)
