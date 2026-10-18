"""测试产线均衡修复效果 - 使用用户提供的完整数据"""
import json, sys, os
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

# 用户提供的完整测试数据
test_data_file = os.path.join(DATA_DIR, 'v1', 'balance_test.json')

if not os.path.exists(test_data_file):
    print(f"错误: 测试数据文件不存在: {test_data_file}")
    print("请先将测试数据保存到该文件")
    sys.exit(1)

with open(test_data_file, 'r', encoding='utf-8') as f:
    test_data = json.load(f)

with app.app_context():
    print("=" * 100)
    print("测试产线均衡修复效果 - 完整数据测试")
    print("=" * 100)
    print(f"产品数量: {len(test_data['productionPlan'])}")
    print(f"产线数量: {len(test_data['productionLine'])}")
    print("=" * 100)

    # 测试1: 权重=0（不考虑均衡）
    print("\n【测试1】PR0010 权重=0（不考虑均衡）")
    print("-" * 100)
    test_data_copy = json.loads(json.dumps(test_data))
    test_data_copy["schedulingStrategy"][0]["ruleWeight"] = "0"

    try:
        aps = Aps(
            test_data_copy['productionPlan'],
            test_data_copy['productionLine'],
            test_data_copy['switchMatrix'],
            test_data_copy['versionId'],
            test_data_copy['traceId'],
            test_data_copy['orgId'],
            test_data_copy['schedulingStrategy'],
        )
        result = aps.production_scheduling()

        if result and result.get('schedule_details'):
            used_lines = [(l, d) for l, d in result['schedule_details'].items() if d.get('scheduled_products')]
            print(f"✓ 使用产线数: {len(used_lines)}")

            total_scheduled = sum(len(d.get('scheduled_products', [])) for _, d in used_lines)
            print(f"✓ 已排产品数: {total_scheduled}/{len(test_data['productionPlan'])}")

            print("\n产线分配详情:")
            for line_id, details in sorted(used_lines, key=lambda x: x[0]):
                products = details.get('scheduled_products', [])
                used_time = details.get('used_time', 0)
                print(f"  {line_id}: {len(products):2d} 个产品, 用时 {used_time:6.1f} 分钟")

            used_times = [d.get('used_time', 0) for _, d in used_lines]
            if used_times:
                balance_score = min(used_times) - max(used_times)
                print(f"\n  均衡得分: {balance_score:.1f} (min={min(used_times):.1f}, max={max(used_times):.1f})")
                print(f"  时间差异: {max(used_times) - min(used_times):.1f} 分钟")
    except Exception as e:
        print(f"✗ 错误: {e}")
        import traceback
        traceback.print_exc()

    # 测试2: 权重=100（强制均衡）
    print("\n" + "=" * 100)
    print("【测试2】PR0010 权重=100（强制均衡）")
    print("-" * 100)
    test_data_copy = json.loads(json.dumps(test_data))
    test_data_copy["schedulingStrategy"][0]["ruleWeight"] = "100"

    try:
        aps = Aps(
            test_data_copy['productionPlan'],
            test_data_copy['productionLine'],
            test_data_copy['switchMatrix'],
            test_data_copy['versionId'],
            test_data_copy['traceId'],
            test_data_copy['orgId'],
            test_data_copy['schedulingStrategy'],
        )
        result = aps.production_scheduling()

        if result and result.get('schedule_details'):
            used_lines = [(l, d) for l, d in result['schedule_details'].items() if d.get('scheduled_products')]
            print(f"✓ 使用产线数: {len(used_lines)}")

            total_scheduled = sum(len(d.get('scheduled_products', [])) for _, d in used_lines)
            print(f"✓ 已排产品数: {total_scheduled}/{len(test_data['productionPlan'])}")

            print("\n产线分配详情:")
            for line_id, details in sorted(used_lines, key=lambda x: x[0]):
                products = details.get('scheduled_products', [])
                used_time = details.get('used_time', 0)
                print(f"  {line_id}: {len(products):2d} 个产品, 用时 {used_time:6.1f} 分钟")

            used_times = [d.get('used_time', 0) for _, d in used_lines]
            if used_times:
                balance_score = min(used_times) - max(used_times)
                print(f"\n  均衡得分: {balance_score:.1f} (min={min(used_times):.1f}, max={max(used_times):.1f})")
                print(f"  时间差异: {max(used_times) - min(used_times):.1f} 分钟")
    except Exception as e:
        print(f"✗ 错误: {e}")
        import traceback
        traceback.print_exc()

    print("\n" + "=" * 100)
    print("预期结果:")
    print("  ✓ 权重=100 时应该使用更多产线（≥3条），而非集中到2条")
    print("  ✓ 权重=100 时各产线的 used_time 应该更接近（时间差异更小）")
    print("  ✓ 权重越大，产线分散度越高（符合直觉）")
    print("=" * 100)
