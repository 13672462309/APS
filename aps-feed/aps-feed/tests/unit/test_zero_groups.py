"""零切换物料组后处理（`scheduler.zero_groups.postprocess_zero_groups`）单测。

这段逻辑是 3.4.1 业务修复的核心，此前只有 `check_business_issues` 的 4 个数据集
端到端兜着——纯语义（0链≠0团、换型不劣才采纳、字典序择优）没有直接断言，改坏了
不容易定位到是哪条规则。本文件逐条钉住。

被测语义（见模块 docstring）:
  1. 同线组员聚拢成**连续块**
  2. 仅当组内**两两互切=0**（clique）才在块内按 蛋白降序 / 粒径升序 重排
  3. 单向 0 链 ≠ 0 团，此时**保持块内原有相对顺序**
  4. **换型成本变差的排列一律不采纳**（换型 pri=1 > 蛋白 pri=2）
  5. 换型成本不变时，仍按 (换型, -连续组内产品数, 蛋白逆序数) 字典序择优
  6. 重排过的线丢弃 solver 时刻（紧凑回放），未动的线保留
"""
import logging
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

from scheduler.context import ApsContext
from scheduler.zero_groups import postprocess_zero_groups

BG = 5          # 背景换型成本（组外默认）
FREE = 0        # 组内互切


def make_ctx(switch_time_matrix, zero_groups, products, lines):
    """switch_time_matrix: {(r1,r2)->min}; products: {rid: {proteinGradeLevel, dia}}"""
    pattrs = {}
    for rid, meta in products.items():
        pattrs[rid] = {'productId': rid, 'recordId': rid, **meta}
    return ApsContext(
        product_attrs=pattrs,
        line_attrs=lines,
        switch_matrix={},
        version_id='T', trace_id='T', org_id='T',
        scheduling_strategy=[], mysql_config={},
        logger=logging.getLogger('test_zero_groups'),
        zero_groups=zero_groups,
        switch_time_matrix=switch_time_matrix,
    )


def sym(*pairs):
    """把 (a,b,m) 列表展开成双向 switch_time_matrix, 未给的对默认 BG。"""
    m = {}
    for a, b, v in pairs:
        m[(a, b)] = v
        m[(b, a)] = v
    return m


def fill_default(m, rids, default=BG):
    """补齐所有有序对, 缺省 default。"""
    for a in rids:
        for b in rids:
            if a != b:
                m.setdefault((a, b), default)
    return m


class TestZeroGroupPostprocess(unittest.TestCase):

    def test_scattered_group_becomes_contiguous(self):
        """组员被打散在组外产品之间时，必须聚拢成连续块。"""
        rids = ['A', 'X', 'B']
        sw = fill_default(sym(('A', 'B', FREE)), rids)
        sw[('PRE', 'A')] = sw[('A', 'PRE')] = 0
        sw[('PRE', 'X')] = sw[('X', 'PRE')] = 0
        sw[('PRE', 'B')] = sw[('B', 'PRE')] = 0

        ctx = make_ctx(sw, {'g1': ['A', 'B']},
                       {'A': {'proteinGradeLevel': 100}, 'X': {}, 'B': {'proteinGradeLevel': 200}},
                       {'L1': {'recordId': 'PRE'}})
        seq = {'L1': ['A', 'X', 'B']}
        times = {'L1': 123}
        postprocess_zero_groups(ctx, seq, times)

        got = seq['L1']
        idxs = sorted(got.index(r) for r in ('A', 'B'))
        self.assertEqual(idxs[1] - idxs[0], 1,
                         f"组员应连续, 实际序列 {got}")
        self.assertNotEqual(got, ['A', 'X', 'B'], "仍被打散")

    def test_clique_sorted_by_protein_desc(self):
        """组内两两互切=0（clique）时，块内按蛋白降序。"""
        rids = ['A', 'B', 'C', 'X']
        sw = fill_default(sym(('A', 'B', FREE), ('A', 'C', FREE), ('B', 'C', FREE)), rids)
        for r in rids:
            sw[('PRE', r)] = sw[(r, 'PRE')] = 0

        # 蛋白: A=100, B=300, C=200  →  期望块内顺序 B, C, A
        ctx = make_ctx(sw, {'g1': ['A', 'B', 'C']},
                       {'A': {'proteinGradeLevel': 100}, 'B': {'proteinGradeLevel': 300},
                        'C': {'proteinGradeLevel': 200}, 'X': {}},
                       {'L1': {'recordId': 'PRE'}})
        seq = {'L1': ['A', 'X', 'B', 'C']}
        postprocess_zero_groups(ctx, seq, {})

        got = seq['L1']
        block = [r for r in got if r in ('A', 'B', 'C')]
        self.assertEqual(block, ['B', 'C', 'A'],
                         f"clique 应按蛋白降序, 实际 {got}")

    def test_clique_tie_broken_by_dia_ascending(self):
        """蛋白相同时，块内按粒径升序。"""
        rids = ['A', 'B']
        sw = fill_default(sym(('A', 'B', FREE)), rids)
        for r in rids:
            sw[('PRE', r)] = sw[(r, 'PRE')] = 0

        ctx = make_ctx(sw, {'g1': ['A', 'B']},
                       {'A': {'proteinGradeLevel': 100, 'dia': 8}, 'B': {'proteinGradeLevel': 100, 'dia': 3}},
                       {'L1': {'recordId': 'PRE'}})
        seq = {'L1': ['A', 'B']}
        postprocess_zero_groups(ctx, seq, {})
        self.assertEqual(seq['L1'], ['B', 'A'], "蛋白相同应按粒径升序")

    def test_zero_chain_not_clique_keeps_inner_order(self):
        """0 链 ≠ 0 团：单向 0 时块内**不得**自由重排，保持原有相对顺序。"""
        rids = ['X', 'B', 'A']
        # A→B=0 但 B→A=5 → 非 clique
        sw = fill_default({}, rids)
        sw[('A', 'B')] = FREE
        sw[('B', 'A')] = 5
        for r in rids:
            sw[('PRE', r)] = sw[(r, 'PRE')] = 0

        # 蛋白 A=300 > B=100, 若误按 clique 排会变成 [A, B]
        ctx = make_ctx(sw, {'g1': ['A', 'B']},
                       {'A': {'proteinGradeLevel': 300}, 'B': {'proteinGradeLevel': 100}, 'X': {}},
                       {'L1': {'recordId': 'PRE'}})
        seq = {'L1': ['X', 'B', 'A']}
        postprocess_zero_groups(ctx, seq, {})

        got = seq['L1']
        block = [r for r in got if r in ('A', 'B')]
        self.assertEqual(block, ['B', 'A'],
                         f"非 clique 不得重排块内顺序, 实际 {got}")

    def test_never_accept_when_switch_cost_worsens(self):
        """换型成本变差的排列一律不采纳（换型 pri=1 > 蛋白 pri=2）。"""
        rids = ['A', 'X', 'B']
        sw = fill_default(sym(('A', 'B', FREE)), rids, default=0)   # 全 0, 当前成本 0
        # 当前 [A,X,B] 成本 = PRE→A + A→X + X→B = 0。
        # 聚拢候选 (块 [A,B], rest=[X]) 两个位置:
        #   pos0 [A,B,X] = PRE→A + A→B + B→X = B→X
        #   pos1 [X,A,B] = PRE→X + X→A + A→B = PRE→X + X→A
        # 抬高这三条让两个候选都严格贵于当前。
        sw[('B', 'X')] = 10
        sw[('PRE', 'X')] = 10
        sw[('X', 'A')] = 10

        ctx = make_ctx(sw, {'g1': ['A', 'B']},
                       {'A': {'proteinGradeLevel': 300}, 'X': {}, 'B': {'proteinGradeLevel': 100}},
                       {'L1': {'recordId': 'PRE'}})
        seq = {'L1': ['A', 'X', 'B']}
        before = list(seq['L1'])
        postprocess_zero_groups(ctx, seq, {})

        self.assertEqual(seq['L1'], before,
                         f"聚拢会增加换型成本, 不应改动 (期望 {before}, 实际 {seq['L1']})")

    def test_gather_at_equal_cost_still_applies(self):
        """换型成本不变时仍应聚拢——连续性是业务要求，靠 -连续组内产品数 择优。"""
        rids = ['A', 'X', 'B']
        sw = fill_default(sym(('A', 'B', FREE)), rids, default=0)
        for r in rids:
            sw[('PRE', r)] = sw[(r, 'PRE')] = 0
        sw[('B', 'X')] = 0          # 两个候选成本都 = 0

        ctx = make_ctx(sw, {'g1': ['A', 'B']},
                       {'A': {'proteinGradeLevel': 100}, 'X': {}, 'B': {'proteinGradeLevel': 300}},
                       {'L1': {'recordId': 'PRE'}})
        seq = {'L1': ['A', 'X', 'B']}
        postprocess_zero_groups(ctx, seq, {})

        got = seq['L1']
        idxs = sorted(got.index(r) for r in ('A', 'B'))
        self.assertEqual(idxs[1] - idxs[0], 1, f"等成本也应聚拢, 实际 {got}")
        self.assertEqual([r for r in got if r in ('A', 'B')], ['B', 'A'], "clique 内应蛋白降序")

    def test_protein_inversion_reduced_only_when_cost_not_worse(self):
        """换型不变时，字典序第三项（蛋白逆序数）参与择优。"""
        rids = ['A', 'B']
        sw = fill_default(sym(('A', 'B', FREE)), rids, default=0)
        for r in rids:
            sw[('PRE', r)] = sw[(r, 'PRE')] = 0

        ctx = make_ctx(sw, {'g1': ['A', 'B']},
                       {'A': {'proteinGradeLevel': 100}, 'B': {'proteinGradeLevel': 300}},
                       {'L1': {'recordId': 'PRE'}})
        seq = {'L1': ['A', 'B']}   # 蛋白升序 = 逆序
        postprocess_zero_groups(ctx, seq, {})
        self.assertEqual(seq['L1'], ['B', 'A'], "应消除蛋白逆序")

    def test_no_zero_groups_is_noop(self):
        """无零切换组时不改动任何序列，也不清 line_times。"""
        ctx = make_ctx({}, {}, {'A': {}, 'B': {}}, {'L1': {'recordId': 'PRE'}})
        seq = {'L1': ['A', 'B']}
        times = {'L1': 42}
        postprocess_zero_groups(ctx, seq, times)
        self.assertEqual(seq['L1'], ['A', 'B'])
        self.assertEqual(times, {'L1': 42})

    def test_single_member_group_ignored(self):
        """单成员「组」不是组（len>=2 才处理）。"""
        rids = ['A', 'X', 'B']
        sw = fill_default({}, rids, default=0)
        ctx = make_ctx(sw, {'g1': ['A'], 'g2': ['B']},
                       {'A': {}, 'X': {}, 'B': {}}, {'L1': {'recordId': 'PRE'}})
        seq = {'L1': ['A', 'X', 'B']}
        postprocess_zero_groups(ctx, seq, {})
        self.assertEqual(seq['L1'], ['A', 'X', 'B'])

    def test_line_times_cleared_only_for_changed_lines(self):
        """重排过的线丢弃 solver 时刻（紧凑回放），未动的线保留。"""
        rids = ['A', 'X', 'B', 'C', 'D']
        sw = fill_default(sym(('A', 'B', FREE), ('C', 'D', FREE)), rids)
        for r in rids:
            sw[('PRE', r)] = sw[(r, 'PRE')] = 0
        # L1: 抬高 PRE→B / PRE→X, 让 L1 的聚拢候选都比当前贵 → L1 不动
        sw[('PRE', 'B')] = sw[('B', 'PRE')] = 10
        sw[('PRE', 'X')] = sw[('X', 'PRE')] = 10

        ctx = make_ctx(sw, {'g1': ['A', 'B'], 'g2': ['C', 'D']},
                       {'A': {'proteinGradeLevel': 100}, 'X': {},
                        'B': {'proteinGradeLevel': 300}, 'C': {}, 'D': {}},
                       {'L1': {'recordId': 'PRE'}, 'L2': {'recordId': 'PRE'}})
        seq = {'L1': ['A', 'X', 'B'], 'L2': ['C', 'X', 'D']}
        times = {'L1': 111, 'L2': 222}
        postprocess_zero_groups(ctx, seq, times)

        self.assertEqual(seq['L1'], ['A', 'X', 'B'], "L1 换型变差不应改动")
        self.assertIn('L1', times, "未改动的线应保留 solver 时刻")
        self.assertEqual(times['L1'], 111)
        idxs = sorted(seq['L2'].index(r) for r in ('C', 'D'))
        self.assertEqual(idxs[1] - idxs[0], 1, f"L2 应聚拢, 实际 {seq['L2']}")
        self.assertNotIn('L2', times, "重排过的线应丢弃 solver 时刻")

    def test_is_unscheduled_treated_as_zero(self):
        """IS_UnScheduled 的切换时间按 0 处理（与求解侧口径一致）。"""
        rids = ['A', 'X', 'B']
        sw = fill_default({}, rids, default=BG)
        sw[('A', 'B')] = constant.IS_UnScheduled
        sw[('B', 'A')] = constant.IS_UnScheduled
        for r in rids:
            sw[('PRE', r)] = sw[(r, 'PRE')] = 0

        ctx = make_ctx(sw, {'g1': ['A', 'B']},
                       {'A': {'proteinGradeLevel': 300}, 'X': {}, 'B': {'proteinGradeLevel': 100}},
                       {'L1': {'recordId': 'PRE'}})
        seq = {'L1': ['A', 'X', 'B']}
        postprocess_zero_groups(ctx, seq, {})
        got = seq['L1']
        idxs = sorted(got.index(r) for r in ('A', 'B'))
        self.assertEqual(idxs[1] - idxs[0], 1, f"IS_UnScheduled 应视作互切 0, 实际 {got}")


if __name__ == '__main__':
    unittest.main(verbosity=2)
