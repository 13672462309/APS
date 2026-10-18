"""回归对比器：比对两次 collect_regression.py 产出的 JSON。

用法:
    python tests/compare_regression.py <旧.json> <新.json> [--tol-sw 0.05] [--tol-cv 2.0]

判定（对照 CLAUDE.md 开发约定的口径）:
  sw    链式换型, 越小越好。|Δ|/旧 ≤ tol-sw 视为持平。
  cv_all 均衡 CV(含空线), 越小越好。Δ ≤ tol-cv 视为持平。
  sched 排产数, 越大越好。变少记 FAIL（漏排是硬伤）。
  pgl_v 蛋白违反, 越小越好。

退出码: 0 = 无 FAIL; 1 = 存在 FAIL。可直接用作脚本门槛。
"""
import json
import sys

argv = [a for a in sys.argv[1:] if not a.startswith('--')]
flags = dict(a[2:].split('=', 1) for a in sys.argv[1:] if a.startswith('--') and '=' in a)
TOL_SW = float(flags.get('tol-sw', 0.05))
TOL_CV = float(flags.get('tol-cv', 2.0))

if len(argv) < 2:
    print(__doc__)
    sys.exit(2)

def load(p):
    d = json.load(open(p))
    return d.get('results', d), d.get('meta', {})

old, om = load(argv[0])
new, nm = load(argv[1])
if om:
    print(f"旧: {om.get('version','?')} @{om.get('git','?')}  cases={om.get('cases')}")
if nm:
    print(f"新: {nm.get('version','?')} @{nm.get('git','?')}  cases={nm.get('cases')}")

cases = sorted(set(old) | set(new))
hdr = f"{'case':<32}{'sw 旧→新':>22}{'cv 旧→新':>18}{'sched':>12}{'pgl_v':>12}  判定"
print(hdr)
print('-' * len(hdr))

fails = []
sum_old_sw = sum_new_sw = 0.0
sum_old_cv = sum_new_cv = 0.0
n_ok = 0

for c in cases:
    o, n = old.get(c), new.get(c)
    if not o or not n:
        print(f"{c:<32}{'缺失于一侧':>22}")
        fails.append((c, 'MISSING'))
        continue
    sum_old_sw += o['sw']; sum_new_sw += n['sw']
    sum_old_cv += o['cv_all']; sum_new_cv += n['cv_all']
    dsw = n['sw'] - o['sw']
    dcv = n['cv_all'] - o['cv_all']
    dsch = n['sched'] - o['sched']
    dpgl = n.get('pgl_v', 0) - o.get('pgl_v', 0)
    verdict = 'OK'
    if abs(dsw) > max(abs(o['sw']) * TOL_SW, 1e-9):
        verdict = 'SW↑' if dsw > 0 else 'SW↓'
    if dcv > TOL_CV:
        verdict = 'CV↑' if verdict == 'OK' else verdict + '+CV↑'
    if dsch < 0:
        verdict = 'SCHED↓'
        fails.append((c, '排产数减少'))
    if dpgl > 0:
        verdict = verdict if verdict != 'OK' else 'PGL↑'
    if verdict in ('SW↑', 'CV↑', 'PGL↑', 'SW↑+CV↑'):
        fails.append((c, verdict))
    n_ok += verdict == 'OK'
    print(f"{c:<32}{o['sw']:8.1f}→{n['sw']:<7.1f}{dsw:+7.1f}"
          f"{o['cv_all']:7.2f}→{n['cv_all']:<6.2f}{dcv:+6.2f}"
          f"{o['sched']:5d}→{n['sched']:<3d}{dsch:+3d}"
          f"{o.get('pgl_v',0):5.1f}→{n.get('pgl_v',0):<4.1f}{dpgl:+3.1f}  {verdict}")

print('-' * len(hdr))
print(f"{'合计':<32}{sum_old_sw:8.1f}→{sum_new_sw:<7.1f}{sum_new_sw-sum_old_sw:+7.1f}"
      f"{sum_old_cv/len(cases):7.2f}→{sum_new_cv/len(cases):<6.2f}"
      f"{(sum_new_cv-sum_old_cv)/len(cases):+6.2f}")
print(f"\n{n_ok}/{len(cases)} 持平或改善, {len(fails)} 项需关注")
for c, v in fails:
    print(f"  ⚠ {c}: {v}")
sys.exit(1 if fails else 0)
