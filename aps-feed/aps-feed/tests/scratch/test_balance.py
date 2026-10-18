"""分析每个产品在5号线和7号线的产能差异"""
import json, sys, os, math
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

with app.app_context():
    from aps import Aps

    with open(os.path.join(DATA_DIR, 'v1', '1130-4.30.txt'), 'r') as f:
        data = json.load(f)

    strategy = data.get('schedulingStrategy', [])
    aps = Aps(data['productionPlan'], data['productionLine'], data['switchMatrix'],
              data['versionId'], data['traceId'], data['orgId'], strategy)

    # 获取产能数据
    aps.get_product_capacity_list()

    line5 = '1130CX05'
    line7 = '1130CX07'

    print(f"{'产品ID':<12} {'recordId':<25} {'吨位':>6} {'5号线产能':>10} {'5号线时间':>10} {'7号线产能':>10} {'7号线时间':>10} {'时间差':>8}")
    print("-" * 110)

    for plan in data['productionPlan']:
        rid = plan['recordId']
        pid = plan['productId']
        qty = plan['plannedProductionQuantity']

        cap5 = aps.product_product_line_capacity_matrix.get(f'{pid}-{line5}', {})
        cap7 = aps.product_product_line_capacity_matrix.get(f'{pid}-{line7}', {})

        t5 = math.ceil(qty * 60 / cap5['capacity']) if cap5.get('capacity', 0) > 0 else '-'
        t7 = math.ceil(qty * 60 / cap7['capacity']) if cap7.get('capacity', 0) > 0 else '-'

        c5 = cap5.get('capacity', 0)
        c7 = cap7.get('capacity', 0)

        t5_str = f"{t5}min" if t5 != '-' else '不可行'
        t7_str = f"{t7}min" if t7 != '-' else '不可行'

        diff = ''
        if isinstance(t5, int) and isinstance(t7, int):
            diff = f"{t7-t5:+d}min"

        print(f"{pid:<12} {rid:<25} {qty:>6} {c5:>10} {t5_str:>10} {c7:>10} {t7_str:>10} {diff:>8}")
