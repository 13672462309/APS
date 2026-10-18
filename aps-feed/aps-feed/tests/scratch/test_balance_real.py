"""实际数据测试 - 简化版本（5个产品，3条产线）"""
import json, sys, os
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

# 简化的测试数据：5个产品，3条产线
test_data = {
    "schedulingStrategy": [{
        "ruleCode": "PR0010",
        "rulePriority": "3",
        "ruleWeight": "100"
    },
    {
        "ruleCode": "PR0009",
        "rulePriority": "2",
        "ruleWeight": "29.17"
    },
    {
        "ruleCode": "PR0005",
        "rulePriority": "1",
        "ruleWeight": "31.5"
    }],
    "productionPlan": [
        {
            "recordId": "P001",
            "productId": "5000658",
            "productName": "产品A",
            "packageType": "01",
            "semiFinishedProductId": "4000693",
            "semiFinishedProductName": "半成品A",
            "thirdMaterialGroupId": "50101",
            "thirdMaterialGroupDesc": "成品-饲料-禽料",
            "forthMaterialGroupId": "5010123",
            "forthMaterialGroupDesc": "成品-饲料-禽料-番鸭",
            "productionParticleSize": "3.8",
            "aspectRatio": "",
            "dia": "3.8",
            "materialPriority": "1",
            "plannedProductionQuantity": 100.0,
            "earliestStartTime": "0.0",
            "latestCompletionTime": "24.0",
            "proteinGradeLevel": "0",
            "unitElectricityConsumption": 15.0
        },
        {
            "recordId": "P002",
            "productId": "5100036",
            "productName": "产品B",
            "packageType": "02",
            "semiFinishedProductId": "4072605",
            "semiFinishedProductName": "半成品B",
            "thirdMaterialGroupId": "50103",
            "thirdMaterialGroupDesc": "成品-饲料-猪料",
            "forthMaterialGroupId": "5010302",
            "forthMaterialGroupDesc": "成品-饲料-猪料-肉猪料",
            "productionParticleSize": "3.8",
            "aspectRatio": "",
            "dia": "3.8",
            "materialPriority": "1",
            "plannedProductionQuantity": 80.0,
            "earliestStartTime": "0.0",
            "latestCompletionTime": "24.0",
            "proteinGradeLevel": "0",
            "unitElectricityConsumption": 12.0
        },
        {
            "recordId": "P003",
            "productId": "5115272",
            "productName": "产品C",
            "packageType": "02",
            "semiFinishedProductId": "4070634",
            "semiFinishedProductName": "半成品C",
            "thirdMaterialGroupId": "50103",
            "thirdMaterialGroupDesc": "成品-饲料-猪料",
            "forthMaterialGroupId": "5010302",
            "forthMaterialGroupDesc": "成品-饲料-猪料-肉猪料",
            "productionParticleSize": "3.8",
            "aspectRatio": "",
            "dia": "3.8",
            "materialPriority": "1",
            "plannedProductionQuantity": 60.0,
            "earliestStartTime": "0.0",
            "latestCompletionTime": "24.0",
            "proteinGradeLevel": "0",
            "unitElectricityConsumption": 14.0
        },
        {
            "recordId": "P004",
            "productId": "5116218",
            "productName": "产品D",
            "packageType": "02",
            "semiFinishedProductId": "4088374",
            "semiFinishedProductName": "半成品D",
            "thirdMaterialGroupId": "50103",
            "thirdMaterialGroupDesc": "成品-饲料-猪料",
            "forthMaterialGroupId": "5010302",
            "forthMaterialGroupDesc": "成品-饲料-猪料-肉猪料",
            "productionParticleSize": "3.8",
            "aspectRatio": "",
            "dia": "3.8",
            "materialPriority": "1",
            "plannedProductionQuantity": 50.0,
            "earliestStartTime": "0.0",
            "latestCompletionTime": "24.0",
            "proteinGradeLevel": "0",
            "unitElectricityConsumption": 11.0
        },
        {
            "recordId": "P005",
            "productId": "5116214",
            "productName": "产品E",
            "packageType": "02",
            "semiFinishedProductId": "4088410",
            "semiFinishedProductName": "半成品E",
            "thirdMaterialGroupId": "50103",
            "thirdMaterialGroupDesc": "成品-饲料-猪料",
            "forthMaterialGroupId": "5010303",
            "forthMaterialGroupDesc": "成品-饲料-猪料-种猪料",
            "productionParticleSize": "3.8",
            "aspectRatio": "",
            "dia": "3.8",
            "materialPriority": "1",
            "plannedProductionQuantity": 40.0,
            "earliestStartTime": "0.0",
            "latestCompletionTime": "24.0",
            "proteinGradeLevel": "0",
            "unitElectricityConsumption": 13.0
        }
    ],
    "productionLine": [
        {
            "allowOverTime": "0",
            "productionLineId": "L001",
            "productionLineName": "1号线",
            "productionLineDescription": "一号线",
            "lineRemainingTime": 1440.0,
            "plannedProductionQuantity": 0.0,
            "productId": "5037473",
            "productName": "前序产品1",
            "packageType": "01",
            "semiFinishedProductId": "4031438",
            "semiFinishedProductName": "前序半成品1",
            "thirdMaterialGroupId": "50101",
            "thirdMaterialGroupDesc": "成品-饲料-禽料",
            "forthMaterialGroupId": "5010121",
            "forthMaterialGroupDesc": "成品-饲料-禽料-白羽肉鸭",
            "productionParticleSize": "3.8",
            "aspectRatio": "",
            "dia": "3.8",
            "recordId": "PRE001",
            "electricityPrice": [{
                "electricityStartTime": "0",
                "electricityEndTime": "24",
                "ePrice": 0.5
            }]
        },
        {
            "allowOverTime": "0",
            "productionLineId": "L002",
            "productionLineName": "2号线",
            "productionLineDescription": "二号线",
            "lineRemainingTime": 1440.0,
            "plannedProductionQuantity": 0.0,
            "productId": "5037904",
            "productName": "前序产品2",
            "packageType": "01",
            "semiFinishedProductId": "4030902",
            "semiFinishedProductName": "前序半成品2",
            "thirdMaterialGroupId": "50101",
            "thirdMaterialGroupDesc": "成品-饲料-禽料",
            "forthMaterialGroupId": "5010124",
            "forthMaterialGroupDesc": "成品-饲料-禽料-蛋鸭",
            "productionParticleSize": "3.8",
            "aspectRatio": "",
            "dia": "3.8",
            "recordId": "PRE002",
            "electricityPrice": [{
                "electricityStartTime": "0",
                "electricityEndTime": "24",
                "ePrice": 0.5
            }]
        },
        {
            "allowOverTime": "0",
            "productionLineId": "L003",
            "productionLineName": "3号线",
            "productionLineDescription": "三号线",
            "lineRemainingTime": 1440.0,
            "plannedProductionQuantity": 0.0,
            "productId": "5037904",
            "productName": "前序产品3",
            "packageType": "01",
            "semiFinishedProductId": "4030902",
            "semiFinishedProductName": "前序半成品3",
            "thirdMaterialGroupId": "50101",
            "thirdMaterialGroupDesc": "成品-饲料-禽料",
            "forthMaterialGroupId": "5010124",
            "forthMaterialGroupDesc": "成品-饲料-禽料-蛋鸭",
            "productionParticleSize": "3.8",
            "aspectRatio": "",
            "dia": "3.8",
            "recordId": "PRE003",
            "electricityPrice": [{
                "electricityStartTime": "0",
                "electricityEndTime": "24",
                "ePrice": 0.5
            }]
        }
    ],
    "switchMatrix": {
        "P001": {"P002": "30", "P003": "30", "P004": "30", "P005": "30", "PRE001": "15", "PRE002": "30", "PRE003": "30"},
        "P002": {"P001": "30", "P003": "15", "P004": "15", "P005": "30", "PRE001": "30", "PRE002": "15", "PRE003": "15"},
        "P003": {"P001": "30", "P002": "15", "P004": "15", "P005": "30", "PRE001": "30", "PRE002": "15", "PRE003": "15"},
        "P004": {"P001": "30", "P002": "15", "P003": "15", "P005": "30", "PRE001": "30", "PRE002": "15", "PRE003": "15"},
        "P005": {"P001": "30", "P002": "30", "P003": "30", "P004": "30", "PRE001": "30", "PRE002": "30", "PRE003": "30"},
        "PRE001": {"P001": "15", "P002": "30", "P003": "30", "P004": "30", "P005": "30"},
        "PRE002": {"P001": "30", "P002": "15", "P003": "15", "P004": "15", "P005": "30"},
        "PRE003": {"P001": "30", "P002": "15", "P003": "15", "P004": "15", "P005": "30"}
    },
    "strategyId": "",
    "versionId": "TEST_VERSION",
    "traceId": "TEST_TRACE",
    "orgId": "1130"
}

def run_test(weight, description):
    """运行单次测试"""
    print(f"\n{'='*100}")
    print(f"【{description}】PR0010 权重={weight}")
    print('='*100)

    test_data_copy = json.loads(json.dumps(test_data))
    test_data_copy["schedulingStrategy"][0]["ruleWeight"] = str(weight)

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
            used_lines = [(l, d) for l, d in result['schedule_details'].items()
                         if d.get('scheduled_products')]

            print(f"\n✓ 使用产线数: {len(used_lines)}")
            total_scheduled = sum(len(d.get('scheduled_products', [])) for _, d in used_lines)
            print(f"✓ 已排产品数: {total_scheduled}/{len(test_data['productionPlan'])}")

            print(f"\n产线分配详情:")
            print(f"{'产线':<10} {'产品数':>8} {'生产时间':>12} {'换型时间':>12} {'总用时':>12}")
            print('-'*60)

            for line_id, details in sorted(used_lines, key=lambda x: x[0]):
                products = details.get('scheduled_products', [])
                used_time = details.get('used_time', 0)

                # 计算生产时间和换型时间
                prod_time = sum(p.get('production_time', 0) for p in products)
                switch_time = used_time - prod_time

                print(f"{line_id:<10} {len(products):>8} {prod_time:>12.1f} {switch_time:>12.1f} {used_time:>12.1f}")

            # 计算均衡度
            used_times = [d.get('used_time', 0) for _, d in used_lines]
            if used_times:
                balance_score = min(used_times) - max(used_times)
                time_diff = max(used_times) - min(used_times)
                print(f"\n均衡指标:")
                print(f"  最小用时: {min(used_times):.1f} 分钟")
                print(f"  最大用时: {max(used_times):.1f} 分钟")
                print(f"  时间差异: {time_diff:.1f} 分钟")
                print(f"  均衡得分: {balance_score:.1f} (越接近0越均衡)")

            return {
                'lines_used': len(used_lines),
                'products_scheduled': total_scheduled,
                'balance_score': balance_score if used_times else 0,
                'time_diff': time_diff if used_times else 0
            }
        else:
            print("✗ 排产失败或无结果")
            return None

    except Exception as e:
        print(f"✗ 错误: {e}")
        import traceback
        traceback.print_exc()
        return None

with app.app_context():
    print("="*100)
    print("产线均衡修复 - 实际数据测试")
    print("="*100)
    print(f"测试数据: {len(test_data['productionPlan'])} 个产品, {len(test_data['productionLine'])} 条产线")

    # 测试1: 权重=0
    result_0 = run_test(0, "测试1：不考虑均衡")

    # 测试2: 权重=50
    result_50 = run_test(50, "测试2：适度均衡")

    # 测试3: 权重=100
    result_100 = run_test(100, "测试3：强制均衡")

    # 对比分析
    print(f"\n{'='*100}")
    print("对比分析")
    print('='*100)
    print(f"\n{'权重':<10} {'使用产线':>12} {'已排产品':>12} {'时间差异':>12} {'均衡得分':>12}")
    print('-'*60)

    if result_0:
        print(f"{'0':<10} {result_0['lines_used']:>12} {result_0['products_scheduled']:>12} "
              f"{result_0['time_diff']:>12.1f} {result_0['balance_score']:>12.1f}")
    if result_50:
        print(f"{'50':<10} {result_50['lines_used']:>12} {result_50['products_scheduled']:>12} "
              f"{result_50['time_diff']:>12.1f} {result_50['balance_score']:>12.1f}")
    if result_100:
        print(f"{'100':<10} {result_100['lines_used']:>12} {result_100['products_scheduled']:>12} "
              f"{result_100['time_diff']:>12.1f} {result_100['balance_score']:>12.1f}")

    print(f"\n{'='*100}")
    print("预期结果验证:")
    print("  ✓ 权重越大，使用产线数应该越多（或相同）")
    print("  ✓ 权重越大，时间差异应该越小")
    print("  ✓ 权重越大，均衡得分应该越接近0")
    print('='*100)
