"""测试1335数据的不同均衡权重"""
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
print("📊 1335数据 - 均衡权重敏感性分析")
print("="*90)

weights = [50, 55, 60, 65, 70, 75]
results = []

with app.app_context():
    from aps import Aps
    
    for weight in weights:
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
            results.append({'weight': weight, 'status': 'FAIL'})
            continue
        
        # 统计
        line_stats = {}
        total_scheduled = 0
        
        for lid, details in result['schedule_details'].items():
            products = [p for p in details.get('scheduled_products', []) 
                       if not p.get('is_wait')]
            
            if products:
                total_time = 0
                for p in products:
                    rid = p['rid']
                    prod_time, _ = aps.prod_time_matrix.get((rid, lid), (0, 10))
                    total_time += prod_time
                    
                    idx = products.index(p)
                    if idx > 0:
                        prev_rid = products[idx-1]['rid']
                        switch_time = aps.switch_time_matrix.get((prev_rid, rid), 0)
                        total_time += switch_time
                
                line_stats[lid] = {
                    'count': len(products),
                    'time': total_time
                }
                total_scheduled += len(products)
        
        # 计算指标
        if line_stats:
            times = [s['time'] for s in line_stats.values()]
            counts = [s['count'] for s in line_stats.values()]
            avg_time = sum(times) / len(times)
            time_std = (sum((t - avg_time)**2 for t in times) / len(times))**0.5
            
            results.append({
                'weight': weight,
                'status': 'OK',
                'scheduled': total_scheduled,
                'rate': total_scheduled / 14 * 100,
                'min_time': min(times),
                'max_time': max(times),
                'time_diff': max(times) - min(times),
                'time_std': time_std,
                'min_count': min(counts),
                'max_count': max(counts),
                'count_diff': max(counts) - min(counts)
            })

# 打印结果表格
print("\n" + "="*90)
print("权重敏感性分析结果")
print("="*90)
print(f"{'权重':<6} {'排产率':<8} {'时间差':<8} {'标准差':<8} {'数量差':<8} "
      f"{'最小时间':<10} {'最大时间':<10}")
print("-"*90)

for r in results:
    if r['status'] == 'OK':
        print(f"{r['weight']:<6} {r['rate']:>6.0f}%  "
              f"{r['time_diff']:>6.0f}分  {r['time_std']:>6.0f}分  "
              f"{r['count_diff']:>6}个   "
              f"{r['min_time']:>8.0f}分  {r['max_time']:>8.0f}分")

print("="*90)

# 找最佳权重
best = None
for r in results:
    if r['status'] == 'OK' and r['rate'] >= 95:
        if best is None or r['time_diff'] < best['time_diff']:
            best = r

if best:
    print(f"\n✅ 推荐权重: {best['weight']}")
    print(f"   排产率: {best['rate']:.0f}%")
    print(f"   时间差: {best['time_diff']:.0f}分钟")
    print(f"   标准差: {best['time_std']:.0f}分钟")
else:
    print("\n⚠️  未找到排产率≥95%的权重")

print("\n" + "="*90)
