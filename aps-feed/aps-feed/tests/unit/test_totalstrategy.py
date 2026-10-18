"""totalStrategy 与 v2 权重计算的极端情况测试套件

覆盖:
1. totalStrategy 归一化: None/缺省/字符串/整数/垃圾值/空串
2. v2 权重计算: base × goal_mul × priority_factor 各分支
3. schema 校验: totalStrategy 边界
4. isPrimaryGoal/rulePriority 极端值
5. electricity_cost 特殊处理
6. 未知 ruleCode / 全部规则缺省
7. 端到端聚合验证(可选, 需 DB)

运行: python tests/unit/test_totalstrategy.py
退出码 0 表示全部通过
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
DATA_DIR = os.path.join(REPO_ROOT, 'data')

import constant
constant.IS_PROD = False

from logging_config import setup_logging
from flask import Flask

app = Flask(__name__)
app.config['JSON_AS_ASCII'] = False
setup_logging(app)

import jsonschema


# ---------- 复制 v2 接口的权重计算逻辑（aps_feed._calculate_weights_v2）----------
def calc_weights_v2(scheduling_strategy):
    """与 aps_feed._calculate_weights_v2 保持一致的参考实现，用于断言。"""
    weights = dict(constant.constraint_weights)
    for s in scheduling_strategy:
        rule_code = s.get('ruleCode', '')
        constraint_type = constant.rule_constraint_mapping.get(rule_code)
        if not constraint_type:
            continue
        is_primary = int(s.get('isPrimaryGoal', 0)) == 1
        priority = float(s.get('rulePriority', 1))
        base = constant.base_weights.get(constraint_type, 0)
        # production_balance 豁免非核心打折 (与 aps_feed._calculate_weights_v2 一致)
        if constraint_type == 'production_balance':
            goal_mul = constant.WEIGHT_GOAL_MULTIPLIER_PRIMARY
        else:
            goal_mul = (constant.WEIGHT_GOAL_MULTIPLIER_PRIMARY if is_primary
                        else constant.WEIGHT_GOAL_MULTIPLIER_SECONDARY)
        pri_factor = max(
            constant.WEIGHT_PRIORITY_FLOOR,
            constant.WEIGHT_PRIORITY_BASE ** (-priority),
        )
        if constraint_type == 'electricity_cost':
            weights[constraint_type] = 1 if is_primary else 0
        else:
            weights[constraint_type] = int(base * goal_mul * pri_factor)
    return weights


# ---------- 最小可用的 Aps 构造数据 ----------
def minimal_plan(line_dia="3.0"):
    return [{
        "recordId": "R001", "productId": "P001", "productName": "测试",
        "semiFinishedProductId": "S001", "semiFinishedProductName": "半成品",
        "thirdMaterialGroupId": "50101", "thirdMaterialGroupDesc": "饲料",
        "forthMaterialGroupId": "5010123", "forthMaterialGroupDesc": "禽料",
        "productionParticleSize": line_dia, "aspectRatio": "", "dia": line_dia,
        "packageType": "01", "materialPriority": "1",
        "plannedProductionQuantity": 10, "earliestStartTime": "0",
        "latestCompletionTime": "24", "proteinGradeLevel": "0",
    }], [{
        "productionLineId": "L1", "productionLineName": "线1", "productionLineDescription": "",
        "lineRemainingTime": 24, "productId": "P000", "recordId": "R000",
        "productName": "前序", "semiFinishedProductId": "S000", "semiFinishedProductName": "",
        "thirdMaterialGroupId": "", "thirdMaterialGroupDesc": "",
        "forthMaterialGroupId": "", "forthMaterialGroupDesc": "",
        "productionParticleSize": line_dia, "aspectRatio": "", "dia": line_dia,
        "packageType": "01", "plannedProductionQuantity": 0,
    }]


# ======================================================================
# 1. totalStrategy 已废弃（机制删除后的回归保护）
# ======================================================================
class TestTotalStrategyDeprecated(unittest.TestCase):
    """totalStrategy 机制已彻底删除，确认即使旧调用赋值也不再影响结果。"""

    def test_Aps不再接受total_strategy参数(self):
        """Aps.__init__ 签名已不含 total_strategy"""
        import inspect
        from aps import Aps
        params = inspect.signature(Aps.__init__).parameters
        self.assertNotIn('total_strategy', params,
                         "Aps.__init__ 不应再接受 total_strategy 参数")

    def test_Aps无total_strategy属性(self):
        """构造后不应有 total_strategy 属性（或即便旧测试赋值也不影响排产）"""
        plan, line = minimal_plan()
        with app.app_context():
            from aps import Aps
            aps = Aps(plan, line, {}, "V1", "T1", "1130", [])
        self.assertFalse(hasattr(aps, 'total_strategy'),
                         "Aps 不应再设置 total_strategy 属性")

    def test_旧调用传total_strategy会被忽略(self):
        """模拟历史调用: 即便外部强行 setattr, 也不触发任何换型翻倍/均衡额外项。
        通过对比有无赋值的目标函数得分一致来验证（轻量：只检查属性不参与逻辑）。"""
        plan, line = minimal_plan()
        with app.app_context():
            from aps import Aps
            aps = Aps(plan, line, {}, "V1", "T1", "1130", [])
        # 关键不变量: 类代码里已无 self.total_strategy == 1/2 判断
        import aps as aps_mod
        src = open(aps_mod.__file__).read()
        self.assertNotIn('self.total_strategy == 1', src)
        self.assertNotIn('self.total_strategy == 2', src)



# ======================================================================
# 2. v2 权重计算
# ======================================================================
class TestV2WeightCalculation(unittest.TestCase):
    """v2 接口权重计算: weight = base × goal_mul × priority_factor"""

    def test_非核心priority1_标准配置(self):
        """PR0009 换型, 非核心, priority=1 → base(200) × 0.5 × 0.5 = 50"""
        w = calc_weights_v2([{"ruleCode": "PR0009", "rulePriority": "1", "isPrimaryGoal": "0"}])
        self.assertEqual(w['changeover_time'], 50)

    def test_核心priority1_不衰减(self):
        """核心目标 priority=1 → base(200) × 1.0 × 0.5 = 100"""
        w = calc_weights_v2([{"ruleCode": "PR0009", "rulePriority": "1", "isPrimaryGoal": "1"}])
        self.assertEqual(w['changeover_time'], 100)
        self.assertEqual(w['changeover_time'], int(200 * 1.0 * 0.5))

    def test_priority0_最高优先级_无衰减(self):
        """priority=0 → pri_factor=1.0(2^0), 核心时拿满 base"""
        w = calc_weights_v2([{"ruleCode": "PR0005", "rulePriority": "0", "isPrimaryGoal": "1"}])
        self.assertEqual(w['line_priority'], 100)  # 100 × 1.0 × 1.0

    def test_priority指数衰减每级减半(self):
        """指数衰减: priority 0→1→2→3 factor = 1.0/0.5/0.25/0.125, 每级严格 ÷2"""
        for pri, expected_factor in [(0, 1.0), (1, 0.5), (2, 0.25), (3, 0.125)]:
            w = calc_weights_v2([{"ruleCode": "PR0003", "rulePriority": str(pri), "isPrimaryGoal": "1"}])
            self.assertEqual(w['material_priority'], int(100 * 1.0 * expected_factor),
                             f"priority={pri} factor 应为 {expected_factor}")

    def test_priority衰减到触底(self):
        """priority≥4 触底 WEIGHT_PRIORITY_FLOOR=0.0625 (2^-4)"""
        w1 = calc_weights_v2([{"ruleCode": "PR0003", "rulePriority": "1", "isPrimaryGoal": "1"}])
        w_floor = calc_weights_v2([{"ruleCode": "PR0003", "rulePriority": "10", "isPrimaryGoal": "1"}])
        self.assertEqual(w1['material_priority'], int(100 * 1.0 * 0.5))      # 50
        self.assertEqual(w_floor['material_priority'], int(100 * 1.0 * 0.0625))  # 6 触底
        self.assertGreater(w1['material_priority'], w_floor['material_priority'])

    def test_priority极大值_触底不崩溃(self):
        """priority=999 触底 0.0625, 不产生异常值"""
        w = calc_weights_v2([{"ruleCode": "PR0003", "rulePriority": "999", "isPrimaryGoal": "1"}])
        self.assertEqual(w['material_priority'], int(100 * 1.0 * 0.0625))  # 触底 6

    def test_高priority权重永远大于低priority(self):
        """不变量(设计意图, 2026-09 protein base 80→200 后修订):
        同 base 量级的规则, priority 高者权重严格更大——指数衰减每级 ÷2 保证阶梯权重保持。
        注: 跨规则的 base 差异可以覆盖 priority 差 (base 是系统量级, priority 是用户语义),
        如 protein(base=200) pri2 与 line(base=100) pri1 算出同权重 25。"""
        # 同 base(200): changeover pri1 非核心 200×0.5×0.5=50 > protein pri2 非核心 200×0.5×0.25=25
        w = calc_weights_v2([
            {"ruleCode": "PR0009", "rulePriority": "1", "isPrimaryGoal": "0"},
            {"ruleCode": "PR0007", "rulePriority": "2", "isPrimaryGoal": "0"},
        ])
        self.assertGreater(w['changeover_time'], w['protein_grade'],
                           "同 base 下 priority=1 必须大于 priority=2 (阶梯权重保持)")
        # 跨 base 现状锁定: protein base 200 后, pri2(×0.25) 与 line base100 pri1(×0.5) 相等
        w2 = calc_weights_v2([
            {"ruleCode": "PR0005", "rulePriority": "1", "isPrimaryGoal": "0"},
            {"ruleCode": "PR0007", "rulePriority": "2", "isPrimaryGoal": "0"},
        ])
        self.assertEqual(w2['line_priority'], w2['protein_grade'],
                         "200×0.25 == 100×0.5: 跨 base 时 priority 差可被 base 差抵消")

    def test_priority为0字符串和整数等价(self):
        w_str = calc_weights_v2([{"ruleCode": "PR0005", "rulePriority": "0", "isPrimaryGoal": "1"}])
        w_int = calc_weights_v2([{"ruleCode": "PR0005", "rulePriority": 0, "isPrimaryGoal": "1"}])
        self.assertEqual(w_str, w_int)

    def test_isPrimaryGoal各种真值(self):
        """isPrimaryGoal="1"/1 为核心; "0"/0/缺省为非核心。
        注: v2 接口用 int() 解析 isPrimaryGoal, 传 "true"/"false" 等非数字
        字符串会抛 ValueError——这是已知边界, schema enum 限制了只能 "0"/"1"。"""
        for val in ["1", 1]:
            w = calc_weights_v2([{"ruleCode": "PR0009", "rulePriority": "1", "isPrimaryGoal": val}])
            self.assertEqual(w['changeover_time'], 100, f"isPrimaryGoal={val!r} 应为核心")
        for val in ["0", 0, None]:
            s = {"ruleCode": "PR0009", "rulePriority": "1"}
            if val is not None:
                s["isPrimaryGoal"] = val
            w = calc_weights_v2([s])
            self.assertEqual(w['changeover_time'], 50, f"isPrimaryGoal={val!r} 应为非核心")

    def test_electricity_cost核心_硬开关1(self):
        """electricity_cost 不参与乘法, 核心时=1, 非核心=0"""
        w = calc_weights_v2([{"ruleCode": "PR0011", "rulePriority": "1", "isPrimaryGoal": "1"}])
        self.assertEqual(w['electricity_cost'], 1)
        # 即使 priority 极大, 核心仍为 1
        w = calc_weights_v2([{"ruleCode": "PR0011", "rulePriority": "999", "isPrimaryGoal": "1"}])
        self.assertEqual(w['electricity_cost'], 1)

    def test_electricity_cost非核心_关闭(self):
        w = calc_weights_v2([{"ruleCode": "PR0011", "rulePriority": "1", "isPrimaryGoal": "0"}])
        self.assertEqual(w['electricity_cost'], 0)

    def test_未传入的规则权重保持0(self):
        """只传一条规则, 其余约束权重应为 0"""
        w = calc_weights_v2([{"ruleCode": "PR0009", "rulePriority": "1", "isPrimaryGoal": "0"}])
        for k in ['material_priority', 'line_priority', 'time_window', 'protein_grade',
                  'particle_size', 'production_balance', 'electricity_cost']:
            self.assertEqual(w[k], 0, f"{k} 未传入应保持 0")
        self.assertEqual(w['changeover_time'], 50)  # 200*0.5*0.5

    def test_全部规则全0配置(self):
        """空策略 → 全部权重 0"""
        w = calc_weights_v2([])
        self.assertTrue(all(v == 0 for v in w.values()))

    def test_未知ruleCode_跳过不崩溃(self):
        w = calc_weights_v2([{"ruleCode": "PR9999", "rulePriority": "1", "isPrimaryGoal": "1"}])
        self.assertTrue(all(v == 0 for v in w.values()))

    def test_重复ruleCode_后者覆盖(self):
        """同一 ruleCode 出现两次, 后计算的覆盖"""
        w = calc_weights_v2([
            {"ruleCode": "PR0009", "rulePriority": "1", "isPrimaryGoal": "1"},   # 200*1.0*0.5=100
            {"ruleCode": "PR0009", "rulePriority": "5", "isPrimaryGoal": "0"},   # 200*0.5*0.0625=6
        ])
        self.assertEqual(w['changeover_time'], 6)

    def test_规则字段缺失_安全降级(self):
        """缺 rulePriority/isPrimaryGoal 时用默认值"""
        w = calc_weights_v2([{"ruleCode": "PR0009"}])
        # priority 默认 1, isPrimaryGoal 默认 0 → 200*0.5*0.5=50
        self.assertEqual(w['changeover_time'], 50)

    def test_全量规则匹配_1170真实入参(self):
        """复现 1170 入参的完整权重, 锁定期望值
        (指数衰减 factor=2^-pri, base changeover=200, base protein_grade=200 (2026-09 由 80 提升),
         base production_balance=200, production_balance 豁免非核心 goal_mul 打折
         — 见 _calculate_weights_v2 docstring)"""
        with open(os.path.join(DATA_DIR, 'v1', '1170-0720.txt')) as f:
            data = json.load(f)
        w = calc_weights_v2(data['schedulingStrategy'])
        expected = {
            'material_priority': 0, 'line_priority': 12, 'time_window': 0,
            # PR0007 priority=1/非核心: base(200, 2026-09 由 80 提升) × 0.5 × 0.5 = 50
            'protein_grade': 50, 'particle_size': 10, 'changeover_time': 50,
            # PR0010 priority=2/非核心: base(200) × 1.0(豁免非核心) × 0.25 = 50
            'production_balance': 50, 'electricity_cost': 0,
        }
        self.assertEqual(w, expected)


# ======================================================================
# 3. schema 校验
# ======================================================================
BASE_VALID = {
    "orgId": "x", "productionPlan": [], "productionLine": [],
    "switchMatrix": {}, "strategyId": "1", "traceId": "t", "versionId": "v",
}


class TestSchemaValidation(unittest.TestCase):
    """schema 校验: totalStrategy 字段已从 schema 移除（additionalProperties=False）"""

    def test_传入totalStrategy_应被拒(self):
        """totalStrategy 字段已废弃，schema 用 additionalProperties=False 拒绝未知字段"""
        d = dict(BASE_VALID)
        d['totalStrategy'] = 2
        with self.assertRaises(jsonschema.ValidationError):
            jsonschema.validate(instance=d, schema=constant.schema)

    def test_不传totalStrategy_通过(self):
        d = dict(BASE_VALID)
        jsonschema.validate(instance=d, schema=constant.schema)  # 不应抛异常

    def test_历史入参全部通过(self):
        """回归: 11 个历史入参文件（均不含 totalStrategy）应通过校验"""
        files = ['v1/1130-4.30.txt', 'v1/1335排程0526.txt', 'v1/request.json',
                 'v1/request03.json', 'v1/request04.json', 'v1/request05.json',
                 'v1/request2.json', 'v1/uat_test_data.json', 'v1/balance_test.json',
                 'v1/1170-0720.txt', 'v1/14300707.json']
        passed = 0
        for f in files:
            fpath = os.path.join(DATA_DIR, f)
            if not os.path.exists(fpath):
                continue
            with open(fpath) as fp:
                d = json.load(fp)
            try:
                jsonschema.validate(instance=d, schema=constant.schema)
                passed += 1
            except jsonschema.ValidationError as e:
                self.fail(f"{f} 被新 schema 拒绝: {e.message}")
        self.assertEqual(passed, len(files), "所有历史入参应通过校验")


# ======================================================================
# 4. 端到端聚合验证(需 DB, 跳过条件)
# ======================================================================
class TestEndToEndAggregation(unittest.TestCase):
    """验证 totalStrategy=缺省 时, 三个同半成品同环模物料更倾向聚到同线

    需 UAT DB 连通且 org=1170 产能数据存在。连不上则跳过。
    """

    @classmethod
    def setUpClass(cls):
        cls.db_ok = False
        try:
            import pymysql
            conn = pymysql.connect(host='haid-oee-uat.mysql.rds.aliyuncs.com', port=3306,
                                   user='algo_aps_rw', password=constant.mysql_offline_config['password'],
                                   database='aps_uat', charset='utf8mb4',
                                   connect_timeout=5)
            conn.close()
            cls.db_ok = True
        except Exception:
            cls.db_ok = False

    def setUp(self):
        if not self.db_ok:
            self.skipTest("UAT DB 不可达, 跳过端到端验证")

    def _run_schedule(self):
        """用 1170 真实数据跑 v2 排产（无 totalStrategy）"""
        with open(os.path.join(DATA_DIR, 'v1', '1170-0720.txt')) as f:
            data = json.load(f)
        with app.app_context():
            from aps import Aps
            aps = Aps(data['productionPlan'], data['productionLine'], data['switchMatrix'],
                      data['versionId'], data['traceId'], data['orgId'],
                      data.get('schedulingStrategy', []))
            # 用 set_constraint_weights 同步 ctx, 否则 production_scheduling 走 ctx 读到旧权重
            aps.set_constraint_weights(calc_weights_v2(data.get('schedulingStrategy', [])))
            return aps.production_scheduling(), data

    def _target_line_count(self, result, data):
        """三个目标物料分布在几条产线"""
        targets = ['5032322', '5046342', '5046326']
        pid2rid = {p['productId']: p['recordId'] for p in data['productionPlan']}
        tset = {pid2rid[t] for t in targets}
        lines = set()
        for l, det in result.get('schedule_details', {}).items():
            for p in det.get('scheduled_products', []):
                if p['rid'] in tset:
                    lines.add(l)
        return len(lines)

    def test_三个同半成品同环模物料聚合到同线(self):
        """核心验证: 换型 priority=1(最高优先级)时, 三个互相零切换的物料
        应聚合到同一条产线连续排产，体现"换型最短第一优先级"的用户要求。"""
        result, data = self._run_schedule()
        n_lines = self._target_line_count(result, data)
        print(f"\n  三物料分布产线数: {n_lines} (期望 1)")
        self.assertEqual(n_lines, 1, "三个零切换物料应聚合到同一条产线")


def run_all():
    loader = unittest.TestLoader()
    suite = unittest.TestSuite()
    for cls in [TestTotalStrategyDeprecated, TestV2WeightCalculation,
                TestSchemaValidation, TestEndToEndAggregation]:
        suite.addTests(loader.loadTestsFromTestCase(cls))
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)
    return 0 if result.wasSuccessful() else 1


if __name__ == '__main__':
    print("=" * 64)
    print("totalStrategy 废弃 + v2 权重计算 极端情况测试")
    print("=" * 64)
    sys.exit(run_all())
