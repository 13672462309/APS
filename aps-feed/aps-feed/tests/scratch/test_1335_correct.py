"""正确读取生产时间的1335测试"""
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

with open(os.path.join(DATA_DIR, 'v1', '1335排程0526.txt'), 'r') as f:
    data = json.load(f)

print("\n" + "="*90)
print("📊 1335数据 - 产线均衡测试（正确读取生产时间）")
print("="*90)

weights = [50, 75]

with app.app_context():
    from aps import Aps
    
    for weight in weights:
        print(f"\n{'='*90}")
        print(f"⚖️  均衡权重 = {weight}")
        print('='*90)
        
        strategy = data.get('schedulingStrategy', [])
        for rule in strategy:
            if rule.get('ruleCode') == 'PR0010':
                rule['ruleWeight'] = weight
        
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
                total_time = 0
                product_list = []
                
                for p in products:
                    rid = p['rid']
                    pname = aps.product_attrs[rid].get('productName', '')[:35]
                    
                    # 从 prod_time_matrix 读取生产时间
                    prod_time, _ = aps.prod_time_matrix.get((rid, lid), (0, 10))
                    
                    # 换型时间
                    switch_time = 0
                    idx = products.index(p)
                    if idx > 0:
                        prev_rid = products[idx-1]['rid']
                        switch_time = aps.switch_time_matrix.get((prev_rid, rid), 0)
                    
                    total_time += prod_time + switch_time
                    product_list.append((pname, prod_time, switch_time))
                
                line_stats[lid] = {
                    'count': len(products),
                    'time': total_time,
                    'products': product_list
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
            for pname, ptime, stime in stat['products']:
                if stime > 0:
                    print(f"    - {pname} ({ptime}分钟 + 换型{stime}分钟)")
                else:
                    print(f"    - {pname} ({ptime}分钟)")
        
        # 计算均衡度
        if line_stats:
            times = [s['time'] for s in line_stats.values()]
            counts = [s['count'] for s in line_stats.values()]
            avg_time = sum(times) / len(times)
            time_std = (sum((t - avg_time)**2 for t in times) / len(times))**0.5
            
            print(f"\n  时间分布: min={min(times):.0f}, max={max(times):.0f}, "
                  f"差值={max(times)-min(times):.0f}, 平均={avg_time:.0f}, 标准差={time_std:.0f}")
            print(f"  数量分布: min={min(counts)}, max={max(counts)}, "
                  f"差值={max(counts)-min(counts)}")
        
        # 未排产
        unscheduled = result.get('unscheduled', [])
        if unscheduled:
            print(f"\n  ❌ 未排产 ({len(unscheduled)}个):")
            for rid in unscheduled:
                pname = aps.product_attrs[rid].get('productName', rid)
                # 找一条可行产线的时间
                for lid in ['1335CX01', '1335CX02', '1335CX03', '1335CX05', '1335CX06']:
                    ptime, _ = aps.prod_time_matrix.get((rid, lid), (0, 10))
                    if ptime > 0 and ptime < 99999:
                        print(f"    - {pname[:40]} (需要{ptime}分钟)")
                        break
                else:
                    print(f"    - {pname[:40]} (无可行产线)")

print("\n" + "="*90)
