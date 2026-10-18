"""zero_groups.py: 零切换物料组后处理。

双向互切 = 0 的产品构成零切换组，业务要求 1) 同线 2) 连续 3) 组内蛋白降序/粒径升序。
Stage1 已保证同线（`y[组,线]`）；本模块把同线组员聚拢成连续块并重排。

优先级语义（不得违反）: 换型 pri=1 > 蛋白 pri=2 —— 换型成本变差的排列一律不采纳；
为保换型而存在的蛋白逆序是正确取舍，不算违反。
"""
import constant
from scheduler.context import ApsContext


def postprocess_zero_groups(ctx: ApsContext, line_sequences, line_times):
    """零切换物料组后处理: 同线组员聚拢成连续块, 块内按蛋白降序/粒径升序。

    优先级语义 (2026-09-29): 只做**不增加换型成本**的重排——换型(pri=1) 是高优先级,
    蛋白/粒径(pri=2) 只在高优先级不受损的前提下修。候选排列按字典序择优:
    (换型成本, -成组连续产品数, 蛋白逆序数, 块内乱序数)。组内两两互切=0 时块内可自由排序;
    仅单向 0 链不视为可自由排序 (0链≠0团)。重排过的线丢弃 solver 时刻, 紧凑回放。
    """
    zero_groups = getattr(ctx, 'zero_groups', {}) or {}
    if not zero_groups or not line_sequences:
        return
    SWM = ctx.switch_time_matrix

    def _sw(a, b):
        v = SWM.get((a, b), 0)
        return 0 if v == constant.IS_UnScheduled else v

    def _cost(pre_rid, seq):
        total, prev = 0, pre_rid
        for rid in seq:
            total += _sw(prev, rid)
            prev = rid
        return total

    def _sort_key(rid):
        a = ctx.product_attrs.get(rid, {})
        pgl = float(a.get('proteinGradeLevel', 0) or 0)
        dia = float(a.get('dia', 0) or 0)
        return (-pgl, dia)

    def _inversions(seq, mem):
        vals = [float(ctx.product_attrs.get(r, {}).get('proteinGradeLevel', 0) or 0)
                for r in seq if r in mem]
        return sum(1 for i in range(len(vals) - 1) if vals[i] < vals[i + 1])

    def _disorder(seq, mem):
        """块内相对顺序相对 _sort_key 的逆序数 (0 = 已按 蛋白降序/粒径升序)。

        没有这一项时, 「蛋白相同只差粒径」的组在换型/连续/蛋白逆序三项上与已有序排列
        打平 (字典序相等不采纳), 粒径升序永远落不下来 —— 业务对 pgl 相同的零切换组
        (如 zhanjiang-0920 的 [5070349, 5029300] 两个 pgl 都是 80) 正是这种情形。
        """
        keys = [_sort_key(r) for r in seq if r in mem]
        return sum(1 for i in range(len(keys) - 1) if keys[i] > keys[i + 1])

    def _contig_count(seq, groups_on_line):
        n = 0
        for mem in groups_on_line:
            idxs = [i for i, r in enumerate(seq) if r in mem]
            if len(idxs) >= 2 and (idxs[-1] - idxs[0] + 1) == len(idxs):
                n += len(idxs)
        return n

    groups_list = [set(m) for m in zero_groups.values() if len(m) >= 2]
    changed = set()
    for _pass in range(3):
        progressed = False
        for lid, seq in list(line_sequences.items()):
            pre_rid = ctx.line_attrs[lid].get('recordId', '')
            cur = list(seq)
            # 该线上出现的组
            on_line = [mem for mem in groups_list if sum(1 for r in cur if r in mem) >= 2]
            if not on_line:
                continue
            # 逐组聚拢 + 排序 (在当前序列上贪心择优)
            for mem in on_line:
                idxs = [i for i, r in enumerate(cur) if r in mem]
                if len(idxs) < 2:
                    continue
                block = [cur[i] for i in idxs]
                clique = all(_sw(a, b) == 0 and _sw(b, a) == 0
                             for i, a in enumerate(block) for b in block[i + 1:])
                sorted_block = sorted(block, key=_sort_key) if clique else list(block)
                rest = [r for i, r in enumerate(cur) if r not in mem]
                cur_cost = _cost(pre_rid, cur)
                best = None
                for pos in range(len(rest) + 1):
                    cand = rest[:pos] + sorted_block + rest[pos:]
                    key = (_cost(pre_rid, cand),
                           -_contig_count(cand, on_line),
                           _inversions(cand, mem),
                           _disorder(cand, mem))
                    if best is None or key < best[0]:
                        best = (key, cand)
                if best and (best[1] != cur) and best[0][0] <= cur_cost:
                    if best[0] < (cur_cost, -_contig_count(cur, on_line),
                                  _inversions(cur, mem), _disorder(cur, mem)):
                        cur = best[1]
                        progressed = True
            if cur != list(seq):
                line_sequences[lid] = cur
                changed.add(lid)
        if not progressed:
            break

    if changed:
        for lid in changed:
            line_times.pop(lid, None)
        ctx.logger.info(f"零切换组后处理: 重排产线 {sorted(changed)}")
