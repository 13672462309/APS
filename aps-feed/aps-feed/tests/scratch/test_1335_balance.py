"""测试1335数据在不同均衡权重下的排产结果"""
import json, os, sys
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

app = Flask(__name__)
app.config['JSON_AS_ASCII'] = False
setup_logging(app)

# 加载数据
with open(os.path.join(DATA_DIR, 'v1', '1335排程0526.txt'), 'r') as f:
    data = json.load(f)

print("\n" + "="*90)
print("📊 1335数据 - 产线均衡权重对比测试")
print("="*90)

# 测试两个权重
weights = [50, 75]

with app.app_context():
    from aps import Aps
    
    for weight in weights:
        print(f"\n{'='*90}")
        print(f"⚖️  均衡权重 = {weight}")
        print('='*90)
        
        # 修改均衡权重
        strategy = data.get('schedulingStrategy', [])
        for rule in strategy:
            if rule.get('ruleCode') == 'PR0010':
                rule['ruleWeight'] = weight
        
        # 运行排产
        aps = Aps(
            data.get('productionPlan', []),
            data.get('productionLine', []),
            data.get('switchMatrix', {}),
            data.get('versionId', 'TEST'),
            data.get('traceId', 'TEST'),
            data.get('orgId', '1335'),
            strategy,
        )
        result = aps.production_scheduling()
        
        if not result or not result.get('schedule_details'):
            print("  ❌ 排产失败")
            continue
        
        # 分析结果
        line_stats = {}
        total_scheduled = 0
        
        for lid, details in result['schedule_details'].items():
            products = [p for p in details.get('scheduled_products', []) 
                       if not p.get('is_wait')]
            
            if products:
                # 计算该产线的总时间
                total_time = 0
                for p in products:
                    rid = p['rid']
                    prod_time = aps.product_attrs[rid].get('production_time', 0)
                    # 找换型时间
                    switch_time = 0
                    if len(products) > 1:
                        idx = products.index(p)
                        if idx > 0:
                            prev_rid = products[idx-1]['rid']
                            prev_semi = aps.product_attrs[prev_rid].get('semiFinishedProductId', '')
                            curr_semi = aps.product_attrs[rid].get('semiFinishedProductId', '')
                            switch_time = aps.switch_matrix.get(prev_semi, {}).get(curr_semi, 0)
                    total_time += prod_time + switch_time
                
                line_stats[lid] = {
                    'count': len(products),
                    'time': total_time,
                    'products': [aps.product_attrs[p['rid']].get('productName', p['rid'])[:30] 
                               for p in products]
                }
                total_scheduled += len(products)
        
        # 打印统计
        print(f"\n  排产率: {total_scheduled}/14 ({total_scheduled/14*100:.0f}%)")
        print(f"  使用产线: {len(line_stats)}/5")
        print()
        
        # 按产线打印
        for lid in sorted(line_stats.keys()):
            stat = line_stats[lid]
            print(f"  {lid}: {stat['count']}个产品, {stat['time']:.0f}分钟")
            for pname in stat['products']:
                print(f"    - {pname}")
        
        # 计算均衡度
        if line_stats:
            times = [s['time'] for s in line_stats.values()]
            counts = [s['count'] for s in line_stats.values()]
            print(f"\n  时间分布: min={min(times):.0f}, max={max(times):.0f}, "
                  f"差值={max(times)-min(times):.0f}")
            print(f"  数量分布: min={min(counts)}, max={max(counts)}, "
                  f"差值={max(counts)-min(counts)}")
        
        # 未排产
        unscheduled = result.get('unscheduled', [])
        if unscheduled:
            print(f"\n  ❌ 未排产 ({len(unscheduled)}个):")
            for rid in unscheduled:
                pname = aps.product_attrs[rid].get('productName', rid)
                ptime = aps.product_attrs[rid].get('production_time', 0)
                print(f"    - {pname[:40]} ({ptime}分钟)")

print("\n" + "="*90)
