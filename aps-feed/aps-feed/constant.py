# Runtime credentials are supplied by environment variables or an ignored local file.
# The Excel-based Demo does not require database or HPAAS credentials.
import json as _json
import os as _os
from pathlib import Path as _Path

_credentials_path = _Path(__file__).with_name("credentials.local.json")
_local_credentials = _json.loads(_credentials_path.read_text(encoding="utf-8")) if _credentials_path.is_file() else {}


def _credential(name):
    return _os.environ.get(name, _local_credentials.get(name, ""))


schema = {
    "$schema": "http://json-schema.org/draft-07/schema#",
    "type": "object",
    "properties": {
        "productionPlan": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "recordId": {"type": "string"},
                    "productId": {"type": "string"},
                    "productName": {"type": "string"},
                    "semiFinishedProductId": {"type": "string"},
                    "semiFinishedProductName": {"type": "string"},
                    "thirdMaterialGroupId": {"type": "string"},
                    "thirdMaterialGroupDesc": {"type": "string"},
                    "forthMaterialGroupId": {"type": "string"},
                    "forthMaterialGroupDesc": {"type": "string"},
                    "productionParticleSize": {"type": "string"},
                    "aspectRatio": {"type": "string"},
                    "packageType": {"type": "string", "enum": ["01", "02"]},
                    "dia": {"type": "string"},
                    "materialPriority": {"type": "string", "enum": ["0", "1", "2", "3", "4", "5", "6", "7", "8", "9", "10"]},
                    "plannedProductionQuantity": {"type": "number"},
                    "earliestStartTime": {"type": "string"},
                    "latestCompletionTime": {"type": "string"},
                    "proteinGradeLevel": {"type": "string"},
                    "unitElectricityConsumption": {"type": "number"},
                    "isDosing": {"type": "integer", "enum": [0, 1]},
                    "isColoring": {"type": "integer", "enum": [0, 1]},
                    "specialProcessType": {"type": "string"},
                },
                "required": [
                    "productId", "productName", "semiFinishedProductId", "recordId",
                    "semiFinishedProductName", "thirdMaterialGroupId", "thirdMaterialGroupDesc",
                    "forthMaterialGroupId", "forthMaterialGroupDesc", "productionParticleSize",
                    "aspectRatio", "dia", "plannedProductionQuantity", "materialPriority", "packageType",
                    "earliestStartTime", "latestCompletionTime", "proteinGradeLevel"
                ],
                "additionalProperties": False
            }
        },
        "productionLine": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "productionLineId": {"type": "string"},
                    "productionLineName": {"type": "string"},
                    "productionLineDescription": {"type": "string"},
                    "lineRemainingTime": {"type": "number"},
                    "productId": {"type": "string"},
                    "recordId": {"type": "string"},
                    "productName": {"type": "string"},
                    "semiFinishedProductId": {"type": "string"},
                    "semiFinishedProductName": {"type": "string"},
                    "thirdMaterialGroupId": {"type": "string"},
                    "thirdMaterialGroupDesc": {"type": "string"},
                    "forthMaterialGroupId": {"type": "string"},
                    "forthMaterialGroupDesc": {"type": "string"},
                    "productionParticleSize": {"type": "string"},
                    "aspectRatio": {"type": "string"},
                    "packageType": {"type": "string", "enum": ["01", "02"]},
                    "dia": {"type": "string"},
                    "plannedProductionQuantity": {"type": "number"},
                    "allowOverTime": {"type": "string", "enum": ["0", "1", "2", "3", "4", "5", "6", "7", "8", "9", "10", "11", "12"]},
                    "assignedMixers": {"type": "array", "items": {"type": "string"}},
                    "mixerWaitTime": {"type": "number"},
                    "preOrderProduceTime": {"type": "number"},
                    "specialProcessType": {"type": "string"},
                    "electricityPrice": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "electricityStartTime": {"type": "string"},
                                "electricityEndTime": {"type": "string"},
                                "ePrice": {"type": "number"}
                            },
                            "required": ["electricityStartTime", "electricityEndTime", "ePrice"]
                        }
                    }
                },
                "required": [
                    "productionLineId", "productionLineName", "productionLineDescription",
                    "lineRemainingTime", "productId", "productName", "semiFinishedProductId",
                    "semiFinishedProductName", "thirdMaterialGroupId", "thirdMaterialGroupDesc",
                    "forthMaterialGroupId", "forthMaterialGroupDesc", "productionParticleSize",
                    "aspectRatio", "dia", "plannedProductionQuantity", "packageType", "recordId"
                ],
                "additionalProperties": False
            }
        },
        "switchMatrix": {
            "type": "object",
            "patternProperties": {
                "^[0-9]+$": {
                    "type": "object",
                    "patternProperties": {
                        "^[0-9]+$": {"type": "string"}
                    },
                    "additionalProperties": False
                }
            },
            "additionalProperties": False
        },
        "strategyId": {"type": "string"},
        "traceId": {"type": "string"},
        "versionId": {"type": "string"},
        "orgId": {"type": "string"},
        "writeBack": {"type": "boolean", "default": True},
        "constraint_weights": {
            "type": "object",
            "properties": {
                "material_priority": {"type": "number"},
                "line_priority": {"type": "number"},
                "time_window": {"type": "number"},
                "protein_grade": {"type": "number"},
                "particle_size": {"type": "number"},
                "base_product": {"type": "number"},
                "utilization": {"type": "number"},
                "high_utilization": {"type": "number"},
                "small_batch": {"type": "number"},
                "diversity": {"type": "number"},
                "production_balance": {"type": "number"}
            },
            "additionalProperties": False
        },
        "schedulingStrategy": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "ruleCode": {"type": "string"},
                    "rulePriority": {"type": "string"},
                    "ruleWeight": {"type": "string"},
                    "isPrimaryGoal": {"type": "string", "enum": ["0", "1"]}
                },
                "required": ["ruleCode", "rulePriority"],
                "additionalProperties": False
            }
        }
    },
    "required": ["orgId", "productionPlan", "productionLine", "switchMatrix", "strategyId", "traceId", "versionId"],
    "additionalProperties": False
}

mysql_online_config = {
    "host": "haid-aps-prod.mysql.rds.aliyuncs.com",
    "port": 3306,
    "username": "algo_aps_rw",
    "password": _credential('APS_MYSQL_ONLINE_PASSWORD'),
    "database": "aps_prod"
}

mysql_offline_config = {
    "host": "haid-oee-uat.mysql.rds.aliyuncs.com",
    "port": 3306,
    "username": "algo_aps_rw",
    "password": _credential('APS_MYSQL_OFFLINE_PASSWORD'),
    "database": "aps_uat"
}

http_base_url_uat = "https://aps-uat.haid.com.cn/api/aps-server/open/algorithmResult"
http_base_url_prod = "https://aps.haid.com.cn/api/aps-server/open/algorithmResult"

# HPAAS 开放接口鉴权配置
hpaas_config = {
    "uat": {
        "token_url": "https://hzero-api-uat.haid.com.cn/oauth/oauth/token",
        "result_url": "https://hzero-api-uat.haid.com.cn/hitf/v2p/rest/invoke/metricsResult",
        "client_id": "algorithm_uat",
        "client_secret": _credential('APS_HPAAS_UAT_CLIENT_SECRET'),
    },
    "prod": {
        "token_url": "https://hpaas-api.haid.com.cn/oauth/oauth/token",
        "result_url": "https://hpaas-api.haid.com.cn/hitf/v2p/rest/invoke/metricsResult",
        "client_id": "algorithm_prod",
        "client_secret": _credential('APS_HPAAS_PROD_CLIENT_SECRET'),
    },
}
IS_PROD = False
IS_UnScheduled = 20000
IS_print_detailed_log = True

# 优先级相关参数（简化设计）
# 统一优先级权重
priority_weights = {
    # 产品优先级权重
    'product': {
        0: 100,  # 最高
        1: 90,
        2: 80,
        3: 70,
        4: 60,
        5: 50,
        6: 40,
        7: 30,
        8: 20,
        9: 10,
        10: 5    # 最低
    },
    # 产线优先级权重
    'line': {
        0: 100,  # 默认最高
        1: 90,
        2: 80,
        3: 70,
        4: 60,
        5: 50,
        6: 40,
        7: 30,
        8: 20,
        9: 10,
        10: 5    # 最低
    }
}

changeover_time_penalty = 60

# 基础排产奖励：确保排产本身永远优于不排产，防止纯惩罚目标时 solver 选择全部不排
# 电耗惩罚 = int(qty * uec * avg_price) * elec_weight，去掉×100后量级约 ~1000 分
# 设为 10_000 确保远大于单产品最大电耗惩罚
base_scheduling_bonus = 10_000

# 策略规则与内部约束的映射关系
rule_constraint_mapping = {
    "PR0003": "material_priority",  # 产品优先级高的优先
    "PR0004": "particle_size",      # 更换环模，按环模从小到大顺序排
    "PR0005": "line_priority",      # 产线优先级高的优先
    "PR0006": "time_window",        # 建议生产时间窗口
    "PR0007": "protein_grade",      # 更换品种，高中低档次排程规则
    "PR0009": "changeover_time",      # 换型时长最短物料优先级
    "PR0010": "production_balance",   # 产线均衡权重系数
    "PR0011": "electricity_cost",     # 整体电耗成本最低
}

# 约束权重配置 - 包含所有权重配置（包括目标函数中的权重）
constraint_weights = {
    "material_priority": 0,     # 产品优先级权重
    "line_priority": 0,         # 产线优先级权重
    "time_window": 0,           # 时间窗口约束权重
    "protein_grade": 0,         # 蛋白档次约束权重
    "particle_size": 0,          # 生产粒径约束权重
    "changeover_time": 0,        # 换型时长约束权重
    "production_balance": 0,     # 产线均衡权重系数
    "electricity_cost": 0,       # 电耗成本权重
}

# v2 权重自动计算参数
WEIGHT_GOAL_MULTIPLIER_PRIMARY = 1.0     # 核心目标乘数
WEIGHT_GOAL_MULTIPLIER_SECONDARY = 0.5   # 非核心目标乘数
# priority 指数衰减基数: priority_factor = WEIGHT_PRIORITY_BASE ^ (-priority)
#   priority 0→1→2→3 的 factor = 1.0 → 0.5 → 0.25 → 0.125 (每级严格 ÷2)
# 设计意图: priority 高的规则权重必须明显大于 priority 低的，不能被 base 差异
# 掩盖。例如 base=100/pri1(×0.5)=50 永远 > base=80/pri2(×0.25)=20。
# 早期线性衰减 1-pri×0.15 时 pri1/pri2 仅 1.17x，导致蛋白(pri1)≈产线(pri2)，
# 违反"充分拉开"要求；改为指数衰减后每级固定 2x，区分度有保证。
WEIGHT_PRIORITY_BASE = 2.0
WEIGHT_PRIORITY_FLOOR = 0.0625           # priority 衰减下限 (2^-4, priority≥4 触底)

# —————— v2 权重自动计算 ——————
# 各规则的基准权重（经过推荐配置验证的量级），由 /submit-task-v2 使用
# changeover_time=200: 换型设为 priority=1(最高)时，权重=200×0.5×0.5=50，
# 实测此量级能让零切换物料聚合同线连续排产，体现"换型最短第一优先级"。
#
# 权重调试指引（当排产方向不符合预期时）:
# 1. 看日志「v2 权重计算结果」确认各规则 weight
# 2. 检查 priority 顺序: factor=2^-pri, pri0>pri1>pri2 每级 ÷2
# 3. 若某 priority=1 规则没主导排产, 是因 weight 不足 → 提高其 base_weights
#    (而非调 priority；priority 是用户语义，base 是系统量级)
# 4. changeover 聚合阈值经验值: priority=1 非核心时 weight 需 ≥50 才稳定聚合
base_weights = {
    "material_priority": 100,     # PR0003
    "line_priority": 100,         # PR0005
    "time_window": 200,           # PR0006
    "protein_grade": 200,         # PR0007 (提高: base 80→200, 与 changeover/balance 同量级; weight=200×2×0.25=100, 蛋白:换型=1:2, 符合 pri2 应有 pri1 一半影响力的设计; 解决零切换组内蛋白排序被 solver 忽略的问题)
    "particle_size": 80,          # PR0004
    "changeover_time": 200,       # PR0009 (提高: 让高优先级换型能主导聚合)
    "production_balance": 200,    # PR0010 (v2 豁免非核心打折: 产线均衡是全局目标, 不因 isPrimaryGoal 打 0.5; 提至 200 因 load-balance 权重扫描证明 w≥200 才压住完工时刻极差, 旧值 80 经非核心打折后 v2 仅得 20 失效)
    "electricity_cost": 1,        # PR0011 (特殊: 不做乘法)
}

# —————————— 目标函数量纲归一化 (202608) ——————————
# 问题: 原目标函数七个项有五种不同量纲, 声明 weight 与实际影响力无稳定映射。
#   changeover  : Σ(penalty_base - st) × w   每弧累加, 随产品数无界增长
#   balance     : diff(分钟) × w × scale     负载差为分钟量级, 又额外乘 scale
#   protein/particle : w × 10 // 可行产品数   被除小一个量级
#   time_window : w                          每产品常量
#   priority    : score(5~100) × w           又一套标尺
# 实测 1500-0807: 换型(核心,pri1,w=100) 实际贡献 94500,
#   蛋白(核心,pri2,w=20) 仅 563 → 声明比 5:1, 实际比 168:1, 偏离 34 倍。
#   均衡(非核心,w=12) 以 18072 反压换型单弧收益 6000, 与优先级语义相反。
#
# 归一化方案: 每项先折算为"该规则被满足的程度"(0~1), 再乘 weight, 使
#   每项理论最大贡献 == OBJECTIVE_SCALE_K × weight
# 于是 weight 成为唯一决定相对影响力的量, 契约
#   weight = base × goal_multiplier × priority_factor
# 才真正成立: weight 翻倍则影响力翻倍, weight 相等则影响力相等,
# 且不再随产品数/产线数/负载差漂移。
#
# 开关: True 启用归一化目标函数。切换会改变所有用例的绝对分数(量纲不同,
# 分数不可比), 回归须比排产结构(排产数/每线分布/cluster 聚合)而非分数。
OBJECTIVE_NORMALIZE = True

# 归一化统一标尺: 每个规则项满分贡献 = OBJECTIVE_SCALE_K × weight
# 取 10000 使最小 weight(=1) 也有足够整数分辨率, 避免整除归零。
OBJECTIVE_SCALE_K = 10_000

# priority_weights 表中的最大分值(product/line 均为 100), 用于归一化分母
OBJECTIVE_PRIORITY_MAX_SCORE = 100

# cluster 软聚合 bonus 占 changeover 归一化预算的比例分母。
# 10 表示 cluster bonus 总量为 changeover 满分的 1/10 —— 保持其
# "tiebreaker"定位(引导同 cluster 聚合), 但不足以压过换型本身或容量约束。
OBJECTIVE_CLUSTER_BONUS_DIVISOR = 5

# switch_cluster 软聚合 bonus: 同 cluster（同半成品、同 dia、互切=0）的
# 产品被排到同一条产线且相邻时，每条相邻弧加此 bonus。
# 设计意图: 当全局得分差异很小时，引导 solver 优先聚合同 cluster 产品。
# 数值不宜过大，否则会"压过"容量约束让 solver 选择排不下产线的方案。
# 1000 ≈ PR0007 蛋白(核心pri1)单产品总分(200×2=400) 的 2~3 倍，足以作为
# tiebreaker 但不足以覆盖"少排一个产品"的 10000 分 base_scheduling_bonus。
SWITCH_CLUSTER_BONUS = 1000

# —————————— 智能排程算法改造（202605） ——————————
# 有效特殊类型标记（用于 PR0014 混合机隔离判断）
SPECIAL_TYPE_DOSING = "DOSING"        # isDosing=1 且无 specialProcessType
SPECIAL_TYPE_COLORING = "COLORING"    # isColoring=1 且无 specialProcessType

# 等待节点常量
WAIT_NODE_PREFIX = "WAIT_"            # 等待节点 recordId 前缀
MAX_WAIT_NODES_PER_LINE = 20          # 每条产线最多等待节点数
WAIT_NODE_MATERIAL_PRIORITY = "98"    # 等待节点优先级标记
WAIT_NODE_NAME_PREFIX = "混合机等待"   # 等待节点产品名称前缀
