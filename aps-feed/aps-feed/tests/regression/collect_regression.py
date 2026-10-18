"""17 case 回归对比采集器（算法改动必跑）。

用法:
    python tests/regression/collect_regression.py <代码根> <输出.json> <轮数>
例:
    python tests/regression/collect_regression.py /opt/haid/code/aps-feed /tmp/new.json 3
    # 对比基线: git worktree/另存一份旧代码根, 同样跑一次后比对两份 json

口径（三个坑, 见 CLAUDE.md 开发约定）:
1. sw (chain): 沿最终序列 Σ switch_matrix(prev→rid), 首产品 prev=产线 recordId
   —— 版本无关; 不要用结果里的 switch_time 汇总(旧版首产品恒记 0, 系统性低估)
2. cv_all: 均衡 CV, 含空线(负载 0 计入分母); cvA 为剔除空线的旧口径, 仅作参考
3. 每文件跑 ROUNDS 轮取均值/范围: CP-SAT 多线程不确定, 单轮对比不可信
4. ROOT 只放目标代码根到 sys.path(勿混入工作区), 避免对比两侧导入同一份代码

数据: data/v2/ 全量 17 个历史 case; 蛋白违反 = 相邻产品 pgl 升序次数
"""
import json, sys, os, time

# 脚本所在仓库根：向上查找 constant.py，避免脚本挪目录后失效
_REP = os.path.dirname(os.path.abspath(__file__))
while not os.path.exists(os.path.join(_REP, 'constant.py')):
    _REP = os.path.dirname(_REP)
REPO_ROOT = _REP

# ROOT = 被对比的目标代码根(可指向 worktree/另一份检出); DATA_DIR 总是取本工作区 case
ROOT = os.path.abspath(sys.argv[1]); OUTPATH = sys.argv[2]; ROUNDS = int(sys.argv[3])
sys.path.insert(0, ROOT)
import constant
constant.IS_PROD = False
from logging_config import setup_logging
from flask import Flask
import aps_feed
from aps import Aps

DATA_DIR = os.environ.get(
    "APS_CASE_DIR",
    os.path.join(REPO_ROOT, "data", "v2"))
FILES = sorted(f for f in os.listdir(DATA_DIR) if f.endswith((".txt", ".json")))


def chain_switch(aps, lid, seq):
    SWM = aps.switch_time_matrix
    pre = aps.line_attrs[lid].get("recordId", "")
    tot = 0
    for rid in seq:
        sw = SWM.get((pre, rid), 0)
        if sw == constant.IS_UnScheduled:
            sw = 0
        tot += sw
        pre = rid
    return tot


res = {}
for fname in FILES:
    fp = os.path.join(DATA_DIR, fname)
    try:
        with open(fp) as f:
            data = json.load(f)
    except Exception:
        continue
    if "productionPlan" not in data or "productionLine" not in data:
        continue
    agg = {"pgl_v": [], "sw": [], "sw_rep": [], "cv": [], "cv_all": [], "sched": [], "time": []}
    ok = True
    for _ in range(ROUNDS):
        app = Flask(__name__); app.config["JSON_AS_ASCII"] = False; setup_logging(app)
        w = aps_feed._calculate_weights_v2(data["schedulingStrategy"])
        t0 = time.time()
        try:
            with app.app_context():
                aps = Aps(data["productionPlan"], data["productionLine"], data["switchMatrix"],
                          data["versionId"], data["traceId"], data["orgId"], data["schedulingStrategy"])
                aps.set_constraint_weights(w)
                r = aps.production_scheduling()
        except Exception as e:
            agg = {"error": "%s:%s" % (type(e).__name__, str(e)[:60])}
            ok = False
            break
        agg["time"].append(time.time() - t0)
        if not r:
            agg = {"error": "FAIL"}
            ok = False
            break
        pv = 0
        sw_chain_total = 0
        sw_rep_total = 0
        loads = []
        for lid, det in r["schedule_details"].items():
            ps = det.get("scheduled_products", [])
            seq = [p["rid"] for p in ps]
            sw_rep_total += sum(p.get("switch_time", 0) for p in ps)
            swc = chain_switch(aps, lid, seq) if seq else 0
            sw_chain_total += swc
            prod = sum(p.get("production_time", 0) for p in ps)
            if seq:
                loads.append(prod + swc)
            for i in range(1, len(ps)):
                pp = aps.product_attrs.get(ps[i - 1]["rid"], {}).get("proteinGradeLevel", "")
                cp = aps.product_attrs.get(ps[i]["rid"], {}).get("proteinGradeLevel", "")
                if not pp or not cp:
                    continue
                try:
                    if float(cp) > float(pp):
                        pv += 1
                except Exception:
                    pass
        act = [t for t in loads if t > 0]
        avg = sum(act) / len(act) if act else 1
        cv = (sum((t - avg) ** 2 for t in act) / len(act)) ** 0.5 / avg * 100 if avg else 0
        # 含空线口径: 分母是**输入的全部产线**, 不是 schedule_details 的条目数。
        # schedule_details 会整条省略空产线 (实测 1500-0810 / zhanjiang-0921-9digit
        # 输入 7 线只有 6 条), 用 len(schedule_details) 会让空线凭空消失,
        # cv_all 退化成 cv_used —— 2026-10-08 前的基线就是这个 bug 的产物。
        n_lines_total = len(data["productionLine"])
        loads_all = loads + [0.0] * max(n_lines_total - len(loads), 0)
        avg_a = sum(loads_all) / len(loads_all) if loads_all else 1
        cv_all = (sum((t - avg_a) ** 2 for t in loads_all) / len(loads_all)) ** 0.5 / avg_a * 100 if avg_a else 0
        agg["pgl_v"].append(pv); agg["sw"].append(sw_chain_total); agg["sw_rep"].append(sw_rep_total)
        agg["cv"].append(round(cv, 1)); agg["cv_all"].append(round(cv_all, 1))
        agg["sched"].append(len(r.get("scheduled", set())))
    if not ok:
        res[fname] = agg
        continue
    res[fname] = {
        "pgl_v": sum(agg["pgl_v"]) / len(agg["pgl_v"]),
        "sw": sum(agg["sw"]) / len(agg["sw"]),
        "sw_min": min(agg["sw"]), "sw_max": max(agg["sw"]),
        "sw_rep": sum(agg["sw_rep"]) / len(agg["sw_rep"]),
        "cv": sum(agg["cv"]) / len(agg["cv"]),
        "cv_min": min(agg["cv"]), "cv_max": max(agg["cv"]),
        "cv_all": sum(agg["cv_all"]) / len(agg["cv_all"]),
        "sched": agg["sched"][0], "total": len(data["productionPlan"]),
        "time": sum(agg["time"]) / len(agg["time"]),
    }
    print("done %s sw=%s(%s-%s) cvA=%s cvAll=%s" % (
        fname, res[fname]["sw"], res[fname]["sw_min"], res[fname]["sw_max"],
        res[fname]["cv"], res[fname]["cv_all"]), flush=True)
with open(OUTPATH, "w") as f:
    json.dump(res, f, indent=2, ensure_ascii=False)
print("collected %d files x%d rounds -> %s" % (len(res), ROUNDS, OUTPATH))
