"""
本地测试脚本：跳过 DB 和 HTTP 回调，直接用 request.json 测试排产核心逻辑。
用法:  python tests/test_local.py [数据文件]
"""
import json
import os
import sys
import time
from unittest.mock import patch, MagicMock

# 仓库根：向上查找 constant.py，避免脚本挪目录后失效
_REP = os.path.dirname(os.path.abspath(__file__))
while not os.path.exists(os.path.join(_REP, 'constant.py')):
    _REP = os.path.dirname(_REP)
REPO_ROOT = _REP
sys.path.insert(0, REPO_ROOT)
DATA_DIR = os.path.join(REPO_ROOT, 'data')

import logging

mock_app = MagicMock()
mock_app.logger = MagicMock()

import constant
constant.IS_print_detailed_log = False

import logging_config
logging_config._app_logger = logging.getLogger('test')
logging_config._app_logger.setLevel(logging.INFO)
handler = logging.StreamHandler()
handler.setFormatter(logging.Formatter('[%(asctime)s] %(levelname)s %(filename)s:%(lineno)d - %(message)s'))
logging_config._app_logger.addHandler(handler)

data_file = sys.argv[1] if len(sys.argv) > 1 else os.path.join(DATA_DIR, 'v1', 'request.json')
print(f"使用数据文件: {data_file}")
with open(data_file, 'r', encoding='utf-8') as f:
    post_data = json.load(f)

from aps import Aps

scheduling_strategy = post_data.get('schedulingStrategy', [])

aps = Aps(
    post_data['productionPlan'],
    post_data['productionLine'],
    post_data['switchMatrix'],
    post_data['versionId'],
    post_data['traceId'],
    post_data['orgId'],
    scheduling_strategy,
)

MOCK_CAPACITY = {}
for plan in post_data['productionPlan']:
    pid = plan['productId']
    for line in post_data['productionLine']:
        lid = line['productionLineId']
        key = pid + '-' + lid
        if key not in MOCK_CAPACITY:
            MOCK_CAPACITY[key] = {
                'capacity': 8.0,
                'min_quantity': 0,
                'priority': 0
            }

aps.product_product_line_capacity_matrix = MOCK_CAPACITY
aps.ctx.product_product_line_capacity_matrix = MOCK_CAPACITY

print(f"\n产品数量: {len(post_data['productionPlan'])}")
print(f"产线数量: {len(post_data['productionLine'])}")
print(f"排产策略: {scheduling_strategy}")
print(f"约束权重: {aps.constraint_weights}")

print("\n开始排产求解...")
start_time = time.time()

with patch('scheduler.capacity.get_product_capacity_list', return_value=True):
    result = aps.production_scheduling()

elapsed = time.time() - start_time
print(f"\n求解耗时: {elapsed:.2f} 秒")

if not result:
    print("排产失败: 没有返回结果")
    sys.exit(1)

scores = result.get('scores', {})
scheduled = result.get('scheduled', set())
unscheduled = result.get('unscheduled', [])

print(f"\n====== 排产结果摘要 ======")
print(f"已排产产品: {len(scheduled)}")
print(f"未排产产品: {len(unscheduled)}")
print(f"总体利用率: {result.get('overall_utilization', 0):.2f}%")
print(f"总分: {scores.get('total_score', 0):.2f}")
print(f"  优先级得分: {scores.get('priority_score', 0):.2f}")
print(f"  时间窗口奖励: {scores.get('time_window_bonus', 0)}")
print(f"  蛋白档次奖励: {scores.get('protein_grade_bonus', 0)}")
print(f"  粒径排序奖励: {scores.get('particle_size_bonus', 0)}")
print(f"  换型得分: {scores.get('changeover_score', 0):.2f}")
print(f"  电耗成本: {scores.get('electricity_cost', 0):.2f}")
print(f"  产线均衡: {scores.get('production_balance_score', 0)}")

non_line_keys = {"unscheduled", "time", "schedule_details", "overall_utilization", "scores", "scheduled"}
for line_id in sorted(k for k in result.keys() if k not in non_line_keys):
    seq = result[line_id]
    if isinstance(seq, dict):
        rids = [seq[k] for k in sorted(seq.keys())]
        print(f"\n产线 {line_id}: {len(rids)} 个产品")
        for i, rid in enumerate(rids, 1):
            print(f"  {i}. {rid}")

if unscheduled:
    print(f"\n未排产产品列表:")
    for rid in unscheduled:
        print(f"  - {rid}")

print("\n测试完成!")
