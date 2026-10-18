"""产线均衡 (PR0010) 对求解结果的影响 —— 唯一断言「权重→排产结构」的用例。

为什么留着:
  test_totalstrategy 只验 v2 权重**公式**, 不验求解器**结果**。production_balance
  曾经静默失效 (非核心打折把 v2 权重压到 20; base 50→80→200 反复调), 没有这类用例
  就只能靠人工看日志发现。本文件守的是「权重加大 ⇒ 均衡真的变好」和
  「权重过大 ⇒ 不能饿死排产」两条不变量。

口径 (与 tests/regression/collect_regression.py 一致, 见 tests/README.md 三个坑):
  1. 均衡用 **含空线 CV** —— 分母是 len(productionLine), 空线负载计 0。
     schedule_details 会整条省略空产线, 不能用它的条目数当分母, 否则空线凭空消失。
  2. 每个权重跑多轮取均值 —— CP-SAT 多线程不确定, 单轮对比不可信。

数据: data/v1/request.json (6线30产品, 效应单调且幅度大),
      data/v1/1130-4.30.txt (7线22产品, 效应稳健)。
"""
import unittest
import json
import os
import sys
import statistics

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
from aps import Aps

app = Flask(__name__)
app.config['JSON_AS_ASCII'] = False
setup_logging(app)

ROUNDS = 3


def _cv(loads):
    """变异系数(%). 空序列/全 0 返回 0。"""
    if not loads:
        return 0.0
    m = sum(loads) / len(loads)
    if not m:
        return 0.0
    return (sum((x - m) ** 2 for x in loads) / len(loads)) ** 0.5 / m * 100


def _run(data, balance_weight, rounds=ROUNDS):
    """跑 rounds 轮, 返回 {cv_all, cv_used, sched, total, n_lines} 的均值/清单。"""
    n_lines_in = len(data['productionLine'])
    total = len(data['productionPlan'])
    cv_all_s, cv_used_s, sched_s, n_used_s = [], [], [], []

    for _ in range(rounds):
        d = json.loads(json.dumps(data))
        st = d.setdefault('schedulingStrategy', [])
        found = False
        for r in st:
            if r.get('ruleCode') == 'PR0010':
                r['ruleWeight'] = str(balance_weight)
                found = True
                break
        if not found:
            st.append({'ruleCode': 'PR0010', 'rulePriority': '10',
                       'ruleWeight': str(balance_weight)})

        with app.app_context():
            aps = Aps(d['productionPlan'], d['productionLine'], d['switchMatrix'],
                      d.get('versionId', 'T'), d.get('traceId', 'T'),
                      d.get('orgId', 'T'), st)
            res = aps.production_scheduling()
        assert res, f"权重 {balance_weight} 排产失败"

        sd = res.get('schedule_details') or {}
        used = [(x.get('used_time') or 0) for x in sd.values() if x.get('scheduled_products')]
        # 含空线: 分母 = 输入产线总数, 空线计 0
        all_loads = used + [0.0] * max(n_lines_in - len(used), 0)

        cv_all_s.append(_cv(all_loads))
        cv_used_s.append(_cv(used))
        n_used_s.append(len(used))
        sched_s.append(sum(len(x.get('scheduled_products') or []) for x in sd.values()))

    return {
        'cv_all': statistics.mean(cv_all_s),
        'cv_all_rounds': cv_all_s,
        'cv_used': statistics.mean(cv_used_s),
        'sched': sched_s[0],
        'total': total,
        'n_lines': n_lines_in,
        'n_used': n_used_s[0],
    }


class TestProductionBalance(unittest.TestCase):
    """production_balance 权重 → 排产结构"""

    @classmethod
    def setUpClass(cls):
        cls.cases = {}
        for name, rel in [('request', ('v1', 'request.json')),
                          ('1130', ('v1', '1130-4.30.txt'))]:
            p = os.path.join(DATA_DIR, *rel)
            if os.path.exists(p):
                with open(p, encoding='utf-8') as f:
                    cls.cases[name] = json.load(f)
        if not cls.cases:
            raise unittest.SkipTest("data/v1 测试数据不存在")

    def test_balance_weight_reduces_cv(self):
        """权重 0 → 200，含空线 CV 必须下降（每个 case 单独断言）。"""
        for name, data in self.cases.items():
            with self.subTest(case=name):
                r0 = _run(data, 0)
                r2 = _run(data, 200)
                self.assertLess(
                    r2['cv_all'], r0['cv_all'],
                    f"[{name}] 权重200 的含空线 CV({r2['cv_all']:.2f}) 应 < "
                    f"权重0({r0['cv_all']:.2f}); 轮次 {r0['cv_all_rounds']} / {r2['cv_all_rounds']}"
                )
                # 同等排产量: 均衡改善不能靠少排产品换
                self.assertEqual(
                    r2['sched'], r0['sched'],
                    f"[{name}] 均衡对比须排产数相同 (0→{r0['sched']}, 200→{r2['sched']})")

    def test_balance_weight_monotone_trend(self):
        """权重递增，均衡应整体向好（request 上效应最大，做趋势哨兵）。"""
        if 'request' not in self.cases:
            self.skipTest("request 数据不存在")
        data = self.cases['request']
        sweep = [(w, _run(data, w)['cv_all']) for w in (0, 100, 200, 500)]
        vals = [v for _, v in sweep]
        # 允许小抖动, 但首尾必须显著拉开且不得反弹超过起点
        self.assertLess(vals[-1], vals[0],
                        f"权重递增应改善均衡: {sweep}")
        self.assertLess(vals[-1], vals[0] * 0.85,
                        f"权重 500 应至少改善 15%: {sweep}")
        for i in range(1, len(vals)):
            self.assertLessEqual(vals[i], vals[i - 1] * 1.05,
                                 f"趋势不应明显反弹: {sweep}")

    def test_scheduling_never_starved_in_calibrated_range(self):
        """校准区间内（v2 权重最多产出 production_balance=200）排产率必须 100%。

        ⚠ 已知边界（暂不作为不变量）: 显式 ruleWeight 大到 ~2000 时，均衡项会压过
        base_scheduling_bonus，开始丢产品（实测 request w=2000 → 22/30，
        1130 w=2000 → 20/22）。那属于「极端入参」而非当前运行区间，见 tests/README.md。
        """
        for name, data in self.cases.items():
            for w in (0, 200, 500):
                with self.subTest(case=name, w=w):
                    r = _run(data, w, rounds=1)
                    self.assertEqual(
                        r['sched'], r['total'],
                        f"[{name}] w={w} 排产 {r['sched']}/{r['total']} 不足")

    def test_empty_lines_counted_in_metric(self):
        """口径自检: 有空线时含空线 CV 必须**严格**大于剔除空线 CV。

        这条守的是 collect_regression 曾犯的 bug：用 len(schedule_details) 当分母
        （schedule_details 会整条省略空线），导致 cv_all 恒等于 cv_used，空线凭空消失。
        """
        for name, data in self.cases.items():
            with self.subTest(case=name):
                r = _run(data, 0)   # w=0 最容易留下空线
                self.assertGreaterEqual(
                    r['cv_all'], r['cv_used'] - 1e-6,
                    f"[{name}] cv_all({r['cv_all']:.2f}) 应 ≥ cv_used({r['cv_used']:.2f})")
                if r['n_used'] < r['n_lines']:
                    self.assertGreater(
                        r['cv_all'], r['cv_used'] + 1e-6,
                        f"[{name}] 有 {r['n_lines']-r['n_used']} 条空线时 cv_all"
                        f"({r['cv_all']:.2f}) 应严格 > cv_used({r['cv_used']:.2f})，"
                        f"否则空线未计入分母")


if __name__ == '__main__':
    unittest.main(verbosity=2)
