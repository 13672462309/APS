"""PR0014 混线隔离回归测试 — 1580 案例。

背景（2026-09 业务反馈）:
  物料 5066128（特殊物料组「组合1」）需排入 1580 三条产线之一。
  三线共用混合机 M090，且:
    - 1580CX01 前置物料 5066127 = 组合1，preOrderProduceTime = 4.0h（占用）
    - 1580CX02 前置物料 5066129 = 组合1，preOrderProduceTime = 2.6h（占用）
    - 1580CX03 前置物料 5005008 = 组合2，preOrderProduceTime = 0（不占用）

期望: 5066128 不得排到 1580CX02（该线前置同为组合1 且占用），应排 1580CX03。

历史缺陷（已修）:
  A. end_times[(l,0)] 用 prod_time_matrix(=0, 前置数量为0) 而非 preOrderProduceTime
     → PR0014 前置隔离约束恒真，混线隔离失效
  B. 前置隔离约束缺 OnlyEnforceIf(is_scheduled) → pre_end>0 时 INFEASIBLE
  C. result_builder 紧凑回放不读 solver start_times → 等待间隙被吞
  R5. 前置占用线禁止再排同类型新产品（语义待业务确认）

运行: python tests/test_mixer_isolation.py
数据: data/v2/1580-0920-mix.txt（已 gitignore，需自备）
"""
import json
import os
import sys

# 仓库根：向上查找 constant.py，避免脚本挪目录后失效
_REP = os.path.dirname(os.path.abspath(__file__))
while not os.path.exists(os.path.join(_REP, 'constant.py')):
    _REP = os.path.dirname(_REP)
REPO_ROOT = _REP
sys.path.insert(0, REPO_ROOT)

import constant
constant.IS_PROD = False  # UAT 环境

from logging_config import setup_logging
from flask import Flask

import aps_feed

DATA_FILE = os.path.join(REPO_ROOT, 'data', 'v2', '1580-0920-mix.txt')
TARGET_PID = '5066128'          # 嘟康，组合1
FORBIDDEN_LINE = '1580CX02'     # 前置同为组合1 且占用 → 不得排
EXPECTED_LINE = '1580CX03'      # 前置不占用 → 应排


def _run(rounds=3):
    """跑多轮（CP-SAT 非确定性），返回每轮 5066128 的落点。"""
    from aps import Aps

    with open(DATA_FILE) as f:
        data = json.load(f)

    app = Flask(__name__)
    app.config['JSON_AS_ASCII'] = False
    setup_logging(app)

    weights = aps_feed._calculate_weights_v2(data['schedulingStrategy'])
    lands = []
    with app.app_context():
        for _ in range(rounds):
            aps = Aps(data['productionPlan'], data['productionLine'], data['switchMatrix'],
                      data['versionId'], data['traceId'], data['orgId'],
                      data['schedulingStrategy'])
            aps.set_constraint_weights(weights)
            result = aps.production_scheduling()
            if not result or not result.get('schedule_details'):
                lands.append(None)
                continue
            land = None
            for lid, det in result['schedule_details'].items():
                for p in det.get('scheduled_products', []):
                    if aps.product_attrs.get(p['rid'], {}).get('productId') == TARGET_PID:
                        land = lid
            lands.append(land)
    return lands


def main():
    if not os.path.exists(DATA_FILE):
        print(f"SKIP: 测试数据不存在 {DATA_FILE}（data/ 已 gitignore，需自备）")
        return 0

    lands = _run(rounds=3)
    print(f"{TARGET_PID} 各轮落点: {lands}")

    failures = []
    for i, land in enumerate(lands, 1):
        if land != EXPECTED_LINE:
            failures.append(f"  第{i}轮: 落 {land}, 期望 {EXPECTED_LINE}")
        if land == FORBIDDEN_LINE:
            failures.append(f"  第{i}轮: 落在禁止线 {FORBIDDEN_LINE}（前置同为组合1 且占用）")

    if failures:
        print("FAIL:")
        print("\n".join(failures))
        return 1
    print(f"PASS: {TARGET_PID} 稳定落 {EXPECTED_LINE}")
    return 0


if __name__ == '__main__':
    sys.exit(main())
