# CLAUDE.md

本文件为 Claude Code (claude.ai/code) 提供本仓库的工作指引。

## 命令

```bash
# 安装依赖
pip install -r requirements.txt

# 本地开发运行（Flask debug，端口 8123）
python aps_feed.py

# 生产运行（gunicorn，读 gunicorn.conf.py）
gunicorn -c gunicorn.conf.py aps_feed:app

# Docker 构建/运行
docker build -t aps-feed .
docker run -p 8123:8123 aps-feed
```

仓库内有一个 unittest 套件 `tests/unit/test_totalstrategy.py`（24 用例：v2 权重计算 + schema 校验 + 端到端聚合）。`tests/` 分三层：`unit/`（unittest：`test_totalstrategy` 24 用例验 v2 权重公式、
`test_balance` 4 用例验 PR0010 权重→排产结构）、`regression/`（**准入门槛**，含 17 case 回归
与业务断言）、`scratch/`（历史调参脚本，不接 CI）；说明见 `tests/README.md`。数据放在 `data/`（gitignored，需自备）。

## 架构

这是一个 **APS（高级排程）feed 服务**，以 Flask HTTP 服务形式对外提供排产接口：接收排产请求 → 用 OR-Tools CP-SAT 求解 → 把排程结果回写到 HPAAS 开放接口。**入口/基础设施在仓库根目录，求解核心在 `scheduler/` 包**。

### 两段式求解（v3.4.0）

```
Stage1 纯分配 (scheduler/stage1_assign.build_model)
  变量: is_scheduled[(line, rid)] 布尔
  约束: 容量(含估算换型)、R5 混线隔离、每产品≤1线
  目标: base_bonus + priority + electricity + balance(SCALE)
        + 蛋白/粒径 beat_count 近似 + 换型 avg 近似 + 成对共置项
  —— 不建模线内排序，只决定"产品上哪条线"

Stage2 组件化排序 (scheduler/stage2_ordering.refine_line_ordering)
  按混合机连通分量分组；分量内联合 CP-SAT（stage2_ordering._add_line_circuit 逐线 Circuit + 时间变量）
  目标: 蛋白降序 + 粒径升序 + 换型 + 时间窗 + 谷电; 跨线 PR0014 析取/隔离
  —— 精确线内排序 + 实际时刻，构建结果
```

⚠ 设计要点：Stage1 的换型近似必须能感知"真实同线伙伴"（成对共置项 `is_sched(l,i) AND is_sched(l,j)`），只用可行集平均会拆散零切换物料组；共置项需校准（当前 0.25x），全量会压过 balance。

### 数据流

```
HTTP 请求
  └─> aps_feed.py        — Flask 入口，校验 JSON schema，路由分发、v2 权重计算
        └─> aps.py        — Aps 类：门面，委托 scheduler/ 各模块
              └─> scheduler/
                    ├─ preprocessing.py  — 预处理(换型矩阵、零切换聚类、混合机共享分析)
                    ├─ model_build.py    — 求解入口编排 + 共享预处理 + 解抽取
                    ├─ stage1_assign.py  — Stage1 纯分配（is_scheduled + 分配级近似目标）
                    ├─ stage2_ordering.py— Stage2 组件化排序（逐线 Circuit + 跨线 PR0014）
                    ├─ zero_groups.py    — 零切换物料组后处理（聚拢 + 蛋白降序）
                    ├─ result_builder.py — 从分配+排序构建排程结果、转 HPAAS 格式
                    ├─ capacity.py / scoring.py / estimate.py / callbacks.py / context.py
              └─> http_util.py   — HpaasApiClient：OAuth2 鉴权 + 回写排程结果
        └─> constant.py   — schema、DB/HPAAS 配置、规则→约束权重映射、base_weights
        └─> mysql_util.py / logging_config.py  — DB 与日志基础设施
```

### HTTP 接口（aps_feed.py）

| 路由 | 说明 |
|------|------|
| `POST /submit-task` | 提交排产任务，权重由入参 `schedulingStrategy[].ruleWeight` 显式给定 |
| `POST /submit-task-v2` | 提交排产任务，权重由 `_calculate_weights_v2()` 按 `isPrimaryGoal`/`rulePriority` 自动计算（推荐） |
| `POST /estimate-task` | 排产负载预估（不回写） |
| `GET /test` | 健康检查 |

### 关键模块

| 文件 | 职责 |
|------|------|
| `constant.py` | 中心配置：`schema`（JSON Schema）、`mysql_online/offline_config`、`hpaas_config[uat\|prod]`、`IS_PROD`、`rule_constraint_mapping`（规则码→约束类型）、`base_weights`/`constraint_weights`（权重）、`OBJECTIVE_NORMALIZE`/`OBJECTIVE_SCALE_K`（量纲归一化） |
| `aps_feed.py` | Flask 入口；`_calculate_weights_v2()` 实现 v2 权重自动计算 |
| `aps.py` | `Aps` 门面类：委托 scheduler/ 完成建模+求解+结果构建 |
| `scheduler/model_build.py` | **求解入口**：`schedule()` / `solve_and_extract()`、电价与换型 penalty 预处理、兼容再导出 |
| `scheduler/stage1_assign.py` | **Stage1 纯分配**：`is_scheduled`、容量/均衡/R5 约束、成对共置项、前置延迟成本 |
| `scheduler/stage2_ordering.py` | **Stage2 排序**：混合机连通分量分组、逐线 Circuit、跨线 PR0014、`refine_line_ordering()` |
| `scheduler/zero_groups.py` | 零切换组后处理：同线聚拢 + 组内蛋白降序/粒径升序（换型不劣才采纳） |
| `scheduler/result_builder.py` | 从分配+排序构建排程结果、转 HPAAS 格式、可行性分析 |
| `scheduler/preprocessing.py` | 换型矩阵、零切换聚类(`switch_clusters`)、混合机共享分析、wait 节点 |
| `scheduler/capacity.py` / `scoring.py` / `estimate.py` | 产能/优先级、解评分、负载预估 |
| `scheduler/context.py` | `ApsContext` 数据类（矩阵/属性/权重容器） |
| `http_util.py` | `HpaasApiClient`：OAuth2 client_credentials，token 自动刷新，POST 回写 |
| `mysql_util.py` | PyMySQL 基础设施（连接/查询，产能表只读） |
| `logging_config.py` | 统一日志配置（含 gunicorn 接入） |
| `gunicorn.conf.py` | 生产 WSGI 配置：3 worker、6GB/worker 内存上限、600s 超时 |

### 环境配置

所有环境敏感值都在 `constant.py`：
- `IS_PROD = True/False` — 切换生产/UAT：影响 MySQL 数据库、HPAAS 接口地址。**部署前先改这个 flag。**
- `mysql_online_config`（prod）/ `mysql_offline_config`（uat）
- `hpaas_config["prod"]` / `hpaas_config["uat"]` — token_url、result_url、client_id/secret

### 规则与权重系统

- 规则码→约束类型映射见 `constant.rule_constraint_mapping`（PR0003~PR0011）。
- v2 权重公式：`weight = base_weights[type] × goal_multiplier × priority_factor`，其中 `priority_factor = WEIGHT_PRIORITY_BASE ** (-priority)`（指数衰减，每级 ÷2）。详见 `constant.py` 顶部注释与 `aps_feed._calculate_weights_v2()`。
- 求解器目标函数包含：换型时长、产线均衡、电耗成本、蛋白档次、粒径、时间窗等软项；外加 `base_scheduling_bonus`（确保排产优于不排）和 `SWITCH_CLUSTER_BONUS`（同 cluster 软聚合）。

## 待办（业务确认）

| # | 事项 | 状态 | 影响 |
|---|------|------|------|
| T1 | **R5 语义确认**：前置占用线禁止再排同类型新产品 | ⏳ 待业务答复 | 见下 |

**T1 详情**（2026-09 提出，2026-10-08 暂按当前实现保留，不改代码）：

- 业务原文：「只要有特殊物料占用时间，这段时间内，**其他**共用混合机的线只能安排生产同类型的特殊物料」
- 实现的 R5 更强：额外禁止「**占用线自身**再排同类型新产品」
- 字面语义推不出业务期望的落点（1580 案例 5066128 应落 CX03，字面允许落 CX02），
  两种字面实现均已实测落 CX02；R5 让它稳定落 CX03
- **待问业务**：「CX02 前置已经是组合1 且仍在占用中，再排一个组合1 新品，允许吗？」
- 若业务确认按字面 → 收窄/删除 `_add_r5_mixer_isolation`（`scheduler/model_build.py`），
  `tests/regression/test_mixer_isolation.py` 期望需同步改；若确认 R5 正确 → 关闭本项并删掉这里的 ⏳

## 开发约定

- 新增规则：先在 `constant.rule_constraint_mapping` 加映射，再实现约束项——**影响"产品上哪条线"的进 `scheduler/stage1_assign.py`（分配级近似/约束），影响"线内怎么排"的进 `scheduler/stage2_ordering.py`（`_add_line_circuit` 目标）**，必要时更新 `constant.schema`。
- 改权重配比：调 `constant.base_weights`（系统量级），不要改用户语义的 `rulePriority`。
- **约束违反的优先级语义**（用户 2026-09-29 明确）：只有「高优先级约束已满足、低优先级未满足」才算违反；为保高优先级（如换型）而存在的低优先级逆序（蛋白/粒径）是正确取舍，不算违反。修这类问题只做不损害高优先级的优化（候选按字典序：先高优先级代价、后低优先级指标）。零切换物料组（双向互切=0）须同线连续 + 组内蛋白降序/粒径升序——见 `precompute_zero_groups` / `zero_groups.postprocess_zero_groups` / Stage1 组同线约束。
- **算法改动必须跑 17 case 回归对比**（`data/v2/` 全量 × 3 轮平均），对比口径注意三个坑：
  1. 换型用**链式口径**：沿最终序列 Σ `switch_matrix(prev→rid)`，首产品 prev=产线 recordId（旧版结果构建对首产品恒记 0，会系统性低估 ~30%）；
  2. 均衡 CV 用**含空线口径**（空线负载 0 计入分母）——剔除空线会把"留空一条线"伪算成均衡好。
   分母取 `len(data["productionLine"])`，**不能**取 `len(schedule_details)`：后者会整条省略
   空产线，空线会凭空消失（2026-10-08 修过此 bug，`tests/baselines/v3.4.1.json` 是错口径产物）；
  3. 基线 CP-SAT 多线程**不确定**（同文件轮间 CV 可摆动 17~41），单轮对比不可信，且对比脚本的 sys.path 必须只指目标代码根。
- **回归基线**：`tests/baselines/<版本>.json`（含 meta + 逐 case 结果，入库）。流程：
  `python tests/regression/collect_regression.py <代码根> /tmp/new.json 3` →
  `python tests/regression/compare_regression.py tests/baselines/v3.4.1.json /tmp/new.json`
  （退出码非 0 = 有 FAIL）。改算法前先有一份基线，否则「8770→8068」这类结论无法复验。
  当前基线 `tests/baselines/v3.4.2.json`：17 case，链式换型合计 **8426.7**，cv_all 均值 **29.22**，
  排产 **465**。（`v3.4.1.json` 的 cv_all 是错口径，已标注，仅作历史对照。）
- 算法演变与决策记录见 `docs/`（`APS算法计分策略文档.md`、`APS调度系统算法演变史.md`、`docs/requirements/`、`docs/superpowers/specs/`）；一次性修复报告归档在 `docs/archive/`。
