"""业务问题回归校验 —— 零切换组拆开 / 组内蛋白降序。

用法:
    python tests/check_business_issues.py [ROOT] [ROUNDS]
    # ROOT 默认当前仓库; 每 case 跑 ROUNDS 轮(默认 3), 逐轮校验

业务问题清单 (2026-09-29, 湛江海大, 来自下载的 7 个文件 = 4 个唯一数据集):
- 零切换物料组被拆开生产 (跨线/同线不连续)
- 连续生产的物料组未按蛋白档次从高到低

校验口径:
- zero_group: 组内产品须 1) 同一条产线 2) 连续占据一段(中间无组外产品)。
  另报组内是否按蛋白降序(业务对 0923 组也提了此要求)。
- protein_run: 这些物料连续生产时须按蛋白降序。若被拆散(非连续)记 SPLIT
  (无连续违规序); 若连续则必须 pgl 降序。
"""
import json, sys, os

# 脚本所在仓库根：向上查找 constant.py，避免脚本挪目录后失效
_REP = os.path.dirname(os.path.abspath(__file__))
while not os.path.exists(os.path.join(_REP, 'constant.py')):
    _REP = os.path.dirname(_REP)
REPO_ROOT = _REP

ROOT = os.path.abspath(sys.argv[1]) if len(sys.argv) > 1 else REPO_ROOT
ROUNDS = int(sys.argv[2]) if len(sys.argv) > 2 else 3
DATA_DIR = os.environ.get(
    "APS_CASE_DIR",
    os.path.join(REPO_ROOT, "data", "v2"))

sys.path.insert(0, ROOT)
import constant
constant.IS_PROD = False
from logging_config import setup_logging
from flask import Flask
import aps_feed
from aps import Aps

ISSUES = {
    "zhanjiang-0920.txt": {
        "zero_groups": [[5070349, 5029300], [5091371, 5097903]],
        "protein_runs": [],
    },
    "zhanjiang-0921-9digit.txt": {
        "zero_groups": [[5100177, 5093334], [5068282, 5044574, 5067650]],
        "protein_runs": [[5044576, 5100179, 5103186, 5071334], [5001812, 5057438]],
    },
    "zhanjiang-0923-9digit.txt": {
        "zero_groups": [[5029300, 5102870, 5070349]],
        "protein_runs": [[5029300, 5102870, 5070349], [5007927, 5013388]],
    },
    "zhanjiang-0920-9digit.txt": {
        "zero_groups": [[5007928, 5013389, 5013390], [5070349, 5029300], [5091371, 5097903]],
        "protein_runs": [[5065067, 5093335, 5065520], [5007927, 5013388]],
    },
}


def run_case(fname):
    fp = os.path.join(DATA_DIR, fname)
    with open(fp) as f:
        data = json.load(f)
    app = Flask(__name__); app.config["JSON_AS_ASCII"] = False; setup_logging(app)
    w = aps_feed._calculate_weights_v2(data["schedulingStrategy"])
    with app.app_context():
        aps = Aps(data["productionPlan"], data["productionLine"], data["switchMatrix"],
                  data["versionId"], data["traceId"], data["orgId"], data["schedulingStrategy"])
        aps.set_constraint_weights(w)
        r = aps.production_scheduling()
    return aps, r


def index_result(aps, r):
    """返回 {productId: (lid, pos, pgl, dia)} 与 {lid: [productId,...]}"""
    pos_of = {}
    seqs = {}
    for lid, det in r["schedule_details"].items():
        seq = []
        for i, p in enumerate(det.get("scheduled_products", [])):
            a = aps.product_attrs.get(p["rid"], {})
            pid = str(a.get("productId"))
            pgl = float(a.get("proteinGradeLevel", 0) or 0)
            dia = float(a.get("dia", 0) or 0)
            pos_of[pid] = (lid, i, pgl, dia)
            seq.append(pid)
        seqs[lid] = seq
    return pos_of, seqs


def check_group(pos_of, seqs, group, kind):
    """kind: 'zero' | 'protein'。返回 (状态字符串, 详情)"""
    group = [str(pid) for pid in group]
    missing = [pid for pid in group if pid not in pos_of]
    if missing:
        return "MISSING", f"未排产: {missing}"
    lids = {pos_of[pid][0] for pid in group}
    if len(lids) > 1:
        detail = ", ".join(f"{pid}@{pos_of[pid][0]}#{pos_of[pid][1]}" for pid in group)
        return ("SPLIT-LINE" if kind == "zero" else "SPLIT"), f"跨线: {detail}"
    lid = lids.pop()
    seq = seqs[lid]
    idxs = sorted(seq.index(pid) for pid in group)
    contiguous = (idxs[-1] - idxs[0] + 1) == len(idxs)
    order = [seq[i] for i in idxs]
    pgls = [pos_of[pid][2] for pid in order]
    desc = all(pgls[i] >= pgls[i + 1] for i in range(len(pgls) - 1))
    span = f"{lid} [{idxs[0]}..{idxs[-1]}] {order} pgl={pgls}"
    if kind == "zero":
        if not contiguous:
            return "SPLIT-INTRA", f"同线不连续: {span}"
        return ("OK" if desc else "ORDER"), span
    # protein_run: 拆散本身不再是连续违规序问题; 连续则必须降序
    if not contiguous:
        return "SPLIT", f"未连续(无连续违规序): {span}"
    return ("OK" if desc else "ORDER"), span


def main():
    print("== 业务问题回归校验 | ROOT=%s | ROUNDS=%d ==" % (ROOT, ROUNDS))
    overall = {}
    for fname, spec in ISSUES.items():
        for rnd in range(ROUNDS):
            aps, r = run_case(fname)
            pos_of, seqs = index_result(aps, r)
            for group in spec["zero_groups"]:
                st, detail = check_group(pos_of, seqs, group, "zero")
                key = (fname, "zero", tuple(group))
                overall.setdefault(key, []).append((st, detail))
            for group in spec["protein_runs"]:
                st, detail = check_group(pos_of, seqs, group, "protein")
                key = (fname, "protein", tuple(group))
                overall.setdefault(key, []).append((st, detail))
        print("done %s" % fname, flush=True)
    print()
    n_ok = n_bad = 0
    for (fname, kind, group), results in sorted(overall.items()):
        statuses = [st for st, _ in results]
        unique = sorted(set(statuses))
        detail_last = results[-1][1]
        stable = "稳定" if len(unique) == 1 else "波动"
        ok = all(st == "OK" for st in statuses)
        if ok:
            n_ok += 1
        else:
            n_bad += 1
        g = "-".join(str(x) for x in group)
        print("%-28s %-7s [%s]  %s  %s  %s" % (
            fname, kind, g, "/".join(unique), stable, detail_last))
    print()
    print("结论: %d 项通过, %d 项仍有问题" % (n_ok, n_bad))


if __name__ == "__main__":
    main()
