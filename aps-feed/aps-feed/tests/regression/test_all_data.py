"""Comprehensive test of all data files in data/ directory"""
import json, os, sys, time, traceback
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

# Test files (v1 历史数据集)
DATA_DIR = os.path.join(REPO_ROOT, 'data', 'v1')
TEST_FILES = [f for f in os.listdir(DATA_DIR)
              if f.endswith(('.json', '.txt'))
              and not f.startswith('balance_test')
              and not f.startswith('uat_test_new')]

# Sort: new field data last
TEST_FILES.sort(key=lambda x: ('uat' in x, x))

results = []

with app.app_context():
    from aps import Aps

    for filename in TEST_FILES:
        filepath = os.path.join(DATA_DIR, filename)
        print(f"\n{'='*90}")
        print(f"📂 {filename} ({os.path.getsize(filepath)/1024:.0f}KB)")
        print('='*90)

        try:
            with open(filepath, 'r') as f:
                data = json.load(f)
        except Exception as e:
            print(f"  ❌ JSON加载失败: {e}")
            continue

        n_products = len(data.get('productionPlan', []))
        n_lines = len(data.get('productionLine', []))
        print(f"  产品: {n_products}, 产线: {n_lines}")

        # Check for new fields
        has_new = any(p.get('isDosing') or p.get('isColoring') or p.get('specialProcessType')
                      for p in data.get('productionPlan', []))
        n_single_mixer = sum(1 for l in data.get('productionLine', [])
                           if len(l.get('assignedMixers', [])) == 1)
        n_multi_mixer = sum(1 for l in data.get('productionLine', [])
                          if len(l.get('assignedMixers', [])) > 1)
        n_has_wait_time = sum(1 for l in data.get('productionLine', [])
                            if float(l.get('mixerWaitTime', 0) or 0) > 0)

        print(f"  新字段: {'✅ 有' if has_new else '⬜ 无(向后兼容)'}")
        print(f"  混合机: 单={n_single_mixer}, 多={n_multi_mixer}, 等待时间={n_has_wait_time}条产线")

        # Stage distribution (only if new fields)
        if has_new:
            stages = {1:0, 2:0, 3:0, 4:0}
            for p in data.get('productionPlan', []):
                d = int(p.get('isDosing', 0))
                c = int(p.get('isColoring', 0))
                s = 4 if d and c else 3 if d else 2 if c else 1
                stages[s] += 1
            print(f"  Stage分布: 普通={stages[1]}, 加色={stages[2]}, 加药={stages[3]}, 加色+加药={stages[4]}")

        # Run scheduling
        t0 = time.time()
        try:
            aps = Aps(
                data.get('productionPlan', []),
                data.get('productionLine', []),
                data.get('switchMatrix', {}),
                data.get('versionId', 'TEST'),
                data.get('traceId', 'TEST'),
                data.get('orgId', '1130'),
                data.get('schedulingStrategy', []),
            )
            result = aps.production_scheduling()
            elapsed = time.time() - t0
        except Exception as e:
            print(f"  ❌ 排产异常: {e}")
            traceback.print_exc()
            continue

        if not result or not result.get('schedule_details'):
            print(f"  ❌ 排产失败或无结果 (耗时{elapsed:.1f}s)")
            results.append({'file': filename, 'status': 'FAIL', 'error': 'no_result'})
            continue

        # Analyze results
        used_lines = []
        total_real = 0
        total_wait = 0
        stage_ok = True
        stage_details = []

        for lid, details in result['schedule_details'].items():
            products = details.get('scheduled_products', [])
            real_products = [p for p in products if not p.get('is_wait')]
            wait_products = [p for p in products if p.get('is_wait')]

            if real_products or wait_products:
                used_lines.append(lid)
                total_real += len(real_products)
                total_wait += len(wait_products)

                # Check stage order
                stages_seq = []
                for p in real_products:
                    rid = p['rid']
                    if rid in aps.product_attrs:
                        stages_seq.append(aps.product_attrs[rid].get('stage', 1))
                if len(stages_seq) > 1:
                    ok = all(stages_seq[i] <= stages_seq[i+1] for i in range(len(stages_seq)-1))
                    if not ok:
                        stage_ok = False
                        stage_details.append(f"{lid}: {stages_seq}")

        total_products = n_products
        schedule_rate = total_real / total_products * 100 if total_products > 0 else 0
        utilization = result.get('overall_utilization', 0)

        # Check mixer sharing
        single_mixer_lines = [lid for lid in aps.line_attrs
                             if len(aps.line_attrs[lid].get('assignedMixers', [])) == 1]
        shared_pairs = aps.shared_mixer_line_pairs
        wait_nodes_created = len(aps.wait_nodes)
        wait_nodes_used = total_wait

        # Print summary line
        status = '✅' if (schedule_rate > 90 and stage_ok) else '⚠️'
        print(f"  {status} 排产: {total_real}/{total_products} ({schedule_rate:.0f}%), "
              f"产线: {len(used_lines)}/{n_lines}, "
              f"利用率: {utilization:.1f}%, "
              f"耗时: {elapsed:.1f}s")

        if has_new:
            print(f"  混合机: 共享对={shared_pairs}, "
                  f"等待节点={wait_nodes_created}个(创建)/{wait_nodes_used}个(使用)")
            print(f"  Stage顺序: {'✅ 正确' if stage_ok else '❌ ' + str(stage_details)}")

        if not stage_ok:
            for sd in stage_details:
                print(f"    ⚠️ {sd}")

        # Check unscheduled
        unscheduled = result.get('unscheduled', [])
        if unscheduled:
            unsched_info = []
            for rid in unscheduled[:3]:
                p = aps.product_attrs.get(rid, {})
                unsched_info.append(f"{p.get('productName', rid)[:30]}"
                                  f"(stage={p.get('stage','?')})")
            print(f"  未排产: {len(unscheduled)}个 - {', '.join(unsched_info)}")

        results.append({
            'file': filename, 'status': 'OK', 'stage_ok': stage_ok,
            'schedule_rate': schedule_rate, 'utilization': utilization,
            'elapsed': elapsed, 'products': n_products, 'lines': n_lines,
            'has_new': has_new, 'wait_created': wait_nodes_created,
            'wait_used': wait_nodes_used, 'shared_pairs': shared_pairs,
            'unscheduled': len(unscheduled)
        })

# Summary table
print(f"\n{'='*130}")
print("📊 汇总表")
print('='*130)
print(f"{'文件':<22} {'状态':<6} {'产品':>5} {'产线':>4} {'排产率':>7} {'利用率':>7} "
      f"{'耗时':>7} {'新字段':>6} {'Stage':>6} {'等待':>6}")
print('-'*130)
for r in results:
    if r['status'] == 'OK':
        stage_str = '✅' if r['stage_ok'] else '❌'
        new_str = '✅' if r['has_new'] else 'N/A'
        wait_str = f"{r['wait_used']}/{r['wait_created']}" if r['has_new'] else 'N/A'
        print(f"{r['file']:<22} ✅     {r['products']:>5} {r['lines']:>4} "
              f"{r['schedule_rate']:>6.0f}% {r['utilization']:>6.1f}% "
              f"{r['elapsed']:>6.1f}s {new_str:>6} {stage_str:>6} {wait_str:>6}")
    else:
        print(f"{r['file']:<22} ❌ {r.get('error',''):<20}")

print('='*130)
print(f"总计: {len(results)} 个文件, "
      f"{sum(1 for r in results if r['status']=='OK')} 通过, "
      f"{sum(1 for r in results if r['status']!='OK')} 失败")
print('='*130)
