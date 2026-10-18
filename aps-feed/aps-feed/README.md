# aps-feed

APS（Advanced Planning & Scheduling）排产 feed 服务。接收排产请求，用 OR-Tools CP-SAT 求解，把排程结果回写到 HPAAS 开放接口。

## 环境

- Python 3.10
- 建议用 conda 建一个干净环境，避免 `ortools` 等包的版本冲突：

```bash
conda create -n aps python=3.10
conda activate aps
pip install -r requirements.txt
```

## 架构

**两段式求解**（v3.4.0+）：

```
Stage1 纯分配        决定「产品上哪条线」  —— is_scheduled 布尔 + 分配级近似目标
Stage2 组件化排序    决定「线内怎么排」    —— 逐线 Circuit + 时间变量，精确时刻
```

- 入口/基础设施在仓库根目录，求解核心在 `scheduler/` 包
- 数据流：`HTTP → aps_feed.py(校验/权重) → aps.py(门面) → scheduler/(preprocessing → model_build → result_builder) → http_util.py(HPAAS 回写)`
- 关键模块：`scheduler/model_build.py`（入口编排）、`scheduler/stage1_assign.py`（Stage1 分配）、`scheduler/stage2_ordering.py`（Stage2 排序）、`scheduler/zero_groups.py`（零切换组）、`scheduler/preprocessing.py`（换型矩阵/零切换聚类/混合机共享）、`constant.py`（schema/权重/归一化）

详见 `CLAUDE.md`（工作指引）、`docs/APS算法计分策略文档.md`（计分）、`docs/APS调度系统算法演变史.md`（演进）。

## 运行

### 本地开发

```bash
python aps_feed.py          # Flask debug，监听 0.0.0.0:8123
```

### 生产（gunicorn）

```bash
gunicorn -c gunicorn.conf.py aps_feed:app
```

### Docker

```bash
docker build -t aps-feed .
docker run -p 8123:8123 aps-feed
```

## 接口

| 方法 路径 | 说明 |
|-----------|------|
| `POST /submit-task` | 提交排产任务，权重由 `schedulingStrategy[].ruleWeight` 显式给定 |
| `POST /submit-task-v2` | 提交排产任务，权重按 `isPrimaryGoal`/`rulePriority` 自动计算（推荐） |
| `POST /estimate-task` | 排产负载预估 |
| `GET /test` | 健康检查 |

请求体 schema 见 `constant.py` 的 `schema`。

### 排程结果直接返回与 Agent 试算

`/submit-task` 与 `/submit-task-v2` 均同步返回计算结果。请求体可加
`"writeBack": false`，用于 Agent 循环试算；省略时默认为 `true`，继续回写 HPAAS。
试算仍需读取数据库产能，不会回写排程结果。

成功响应 `code="000"` 的 `data` 包含：

- `traceId`、`versionId`：本轮任务标识。
- `scheduleResults`：原有 HPAAS 格式结果，包含产线、顺序、数量等。
- `solverResult`：完整内部结果。`scheduled`、`unscheduled` 是任务 ID 数组；
  `schedule_details` 包含每条产线的 `scheduled_products` 与 `time_slots`，
  包括 `start_time`、`end_time`、生产／换型／等待时段；另有 `time`、
  `overall_utilization`、`scores`。内部集合已转换为 JSON 数组。
- `writeBack.status`：`skipped`（仅试算）、`success`（回写成功）、
  `failed`（回写失败，但计算结果仍返回）。调用方应独立检查此状态。

`solverResult` 中时刻和时长以分钟表示，相对于输入时间原点；输入时间窗口和
产线时长按小时传入。旧格式 `scheduleResults.productionTime` 是小时，
`productionChangeTime` 是分钟。JSON 序号键会变为字符串。

`overall_utilization` 是现有的生产加换型用时占比，不等于理论 OEE；`scores`
是已有业务评分，并不是截图六项指标。`code="000"` 仅表示结果生成成功，
不保证全部需求已排入或所有业务约束通过，loop 仍需评价和校验结果。
计算失败／无结果返回业务码 `500`，参数校验失败返回 `203`，沿用 HTTP 200 的
业务码约定。达到 loop 停止条件后应选择历史最佳有效方案，而非直接使用最后一轮。

## 配置

所有环境敏感配置在 `constant.py`：

- `IS_PROD` — `True` 走生产 MySQL + 生产 HPAAS；`False` 走 UAT。**部署前确认此 flag。**
- `mysql_online_config` / `mysql_offline_config` — 数据库连接
- `hpaas_config["prod" | "uat"]` — HPAAS OAuth2 + 回写地址
- `base_weights` — 系统量级（调排产方向改这里，不要改用户语义的 `rulePriority`）
- `OBJECTIVE_NORMALIZE` / `OBJECTIVE_SCALE_K` — 目标函数量纲归一化开关与标尺

## 测试

`tests/` 分三层（详见 `tests/README.md`）：`unit/` unittest 套件、`regression/` 准入门槛、`scratch/` 历史调参脚本。

```bash
python tests/unit/test_submit_task_api.py           # 返回契约 + 实际结果构建器（不连接业务库）
python tests/unit/test_totalstrategy.py           # 24 用例：v2 权重 + schema + 端到端聚合
python tests/regression/test_all_data.py          # data/v1 全量冒烟（10 文件）
python tests/regression/check_business_issues.py  # 业务断言：零切换组同线连续 + 蛋白降序
python tests/regression/collect_regression.py     # 17 case 回归采集（算法改动必跑）
python tests/regression/test_mixer_isolation.py   # PR0014 混线隔离 / R5
```

**算法改动必跑 17 case 回归**（`data/v2/` × 3 轮平均），对比口径见 `CLAUDE.md`
（链式换型、含空线 CV、多轮取均值）。基线存 `tests/baselines/`，用
`tests/regression/compare_regression.py <旧> <新>` 逐 case 比对。

测试数据放在 `data/`（已 gitignore，需自备）。

## 文档

- `CHANGELOG.md` — 变更记录
- `docs/APS算法计分策略文档.md`、`docs/APS调度系统算法演变史.md` — 算法设计
- `docs/requirements/` — 需求与决策记录
- `docs/superpowers/specs/` — 规则设计规格
- `docs/archive/` — 历史修复报告归档
