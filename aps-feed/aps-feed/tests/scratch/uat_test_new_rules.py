"""UAT测试：使用用户提供的完整数据测试新规则"""
import json, os, sys
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
from aps import Aps

app = Flask(__name__)
app.config['JSON_AS_ASCII'] = False
setup_logging(app)

# Load the test data from the file saved from user
test_file = os.path.join(REPO_ROOT, 'data', 'v1', 'uat_test_data.json')

if not os.path.exists(test_file):
    print("ERROR: data/v1/uat_test_data.json 不存在 (gitignored, 需自备)")
    sys.exit(1)

with open(test_file, 'r') as f:
    data = json.load(f)

with app.app_context():
    print("=" * 80)
    print("UAT测试：新规则验证（24产品，7产线）")
    print("=" * 80)

    aps = Aps(
        data['productionPlan'], data['productionLine'], data['switchMatrix'],
        data['versionId'], data['traceId'], data['orgId'],
        data['schedulingStrategy']
    )

    # --- Preprocessing checks ---
    print("\n--- 1. Stage分配检查 ---")
    stage_counts = {1: 0, 2: 0, 3: 0, 4: 0}
    for rid, p in aps.product_attrs.items():
        s = p.get('stage', 1)
        stage_counts[s] = stage_counts.get(s, 0) + 1
        label = {1: '普通', 2: '加色', 3: '加药', 4: '加色+加药'}[s]
        if s > 1:
            print(f"  {rid}: stage={s} ({label}) - dosing={p['isDosing']}, coloring={p['isColoring']}, special={p['specialProcessType']}")

    print(f"  Stage分布: stage1={stage_counts[1]}, stage2={stage_counts[2]}, stage3={stage_counts[3]}, stage4={stage_counts[4]}")

    # --- Mixer sharing ---
    print("\n--- 2. 混合机共享检测 ---")
    # Need to precompute and check
    aps.get_product_capacity_list()
    lines = list(aps.line_attrs.keys())
    scheduled_rids = [r_id for r_id, p in aps.product_attrs.items() if p.get('materialPriority', '0') != '4']
    aps._precompute_production_times(scheduled_rids, lines)
    aps._precompute_switch_times(scheduled_rids, lines)

    line_feasible = {}
    line_remaining = {}
    for l_id in lines:
        line_remaining[l_id] = int(aps.line_attrs[l_id]['lineRemainingTime'] * 60)
        line_feasible[l_id] = [r_id for r_id in scheduled_rids
                               if aps.prod_time_matrix.get((r_id, l_id), (constant.IS_UnScheduled, 10))[0] != constant.IS_UnScheduled]
    active = [l for l in lines if line_feasible[l]]
    aps._analyze_mixer_sharing(active)

    print(f"  共享混合机产线对: {aps.shared_mixer_line_pairs}")
    print(f"  活跃产线: {active}")

    for l_id in active:
        mixers = aps.line_attrs[l_id].get('assignedMixers', [])
        wait = aps.line_attrs[l_id].get('mixerWaitTime', 0)
        print(f"  {l_id}: mixers={mixers}, waitTime={wait}h")

    # --- Wait nodes ---
    aps._create_wait_nodes(active, line_feasible)
    print(f"\n  等待节点: {len(aps.wait_nodes)} 个")
    for wid, wattr in aps.wait_nodes.items():
        print(f"    {wid}: time={wattr['production_time']}min, line={wattr['line_id']}")

    # --- Full scheduling ---
    print("\n--- 3. 完整排产 ---")
    result = aps.production_scheduling()

    if not result or not result.get('schedule_details'):
        print("  ERROR: 排产失败或没有结果")
        sys.exit(1)

    # Analyze results
    total_scheduled = 0
    wait_count = 0
    for l_id, details in result['schedule_details'].items():
        products = details.get('scheduled_products', [])
        wait_nodes = [p for p in products if p.get('is_wait')]
        real_products = [p for p in products if not p.get('is_wait')]

        if real_products or wait_nodes:
            print(f"\n  {l_id} ({details['name']}):")
            print(f"    真实产品: {len(real_products)}个, 等待: {len(wait_nodes)}个, 总用时: {details['used_time']}min")

            # Check stage ordering
            stages = []
            for p in real_products:
                rid = p['rid']
                if rid in aps.product_attrs:
                    stages.append(aps.product_attrs[rid].get('stage', 1))

            stage_ok = all(stages[i] <= stages[i+1] for i in range(len(stages)-1)) if len(stages) > 1 else True
            print(f"    Stage顺序: {stages} {'✅' if stage_ok else '❌'}")

            for p in products:
                is_wait = p.get('is_wait', False)
                marker = "⏸ WAIT" if is_wait else "  PROD"
                start_h = p.get('start_time', 0) / 60.0
                end_h = p.get('end_time', 0) / 60.0
                print(f"    {marker} [{start_h:5.1f}h - {end_h:5.1f}h] {p.get('name', p['rid'])[:40]}")

            total_scheduled += len(real_products)
            wait_count += len(wait_nodes)
        else:
            print(f"  {l_id}: 未排产")

    print(f"\n  总排产: {total_scheduled}/{len(scheduled_rids)} 产品")
    print(f"  等待节点: {wait_count} 个")

    # --- Validation ---
    print("\n--- 4. 规则验证 ---")
    all_stage_ok = True
    for l_id, details in result['schedule_details'].items():
        products = details.get('scheduled_products', [])
        real_products = [p for p in products if not p.get('is_wait')]
        stages = []
        for p in real_products:
            rid = p['rid']
            if rid in aps.product_attrs:
                stages.append((rid, aps.product_attrs[rid].get('stage', 1)))

        for i in range(len(stages)-1):
            if stages[i][1] > stages[i+1][1]:
                print(f"  ❌ Stage错误在 {l_id}: {stages[i]} -> {stages[i+1]}")
                all_stage_ok = False

    if all_stage_ok:
        print("  ✅ 所有产线Stage顺序正确")

    # Check wait nodes are NOT in used_time
    for l_id, details in result['schedule_details'].items():
        products = details.get('scheduled_products', [])
        wait_total = sum(p.get('total_time', 0) for p in products if p.get('is_wait'))
        prod_total = sum(p.get('total_time', 0) for p in products if not p.get('is_wait'))
        reported_used = details.get('used_time', 0)
        if wait_total > 0 and abs(reported_used - prod_total) > 1:
            print(f"  ⚠️ {l_id}: used_time={reported_used} 应该={prod_total}（不含等待{wait_total}）")
        elif wait_total > 0:
            print(f"  ✅ {l_id}: used_time={reported_used}（不含等待{wait_total}min）")

    print("\n" + "=" * 80)
    print("测试完成")
    print("=" * 80)
