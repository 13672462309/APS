# Changelog

All notable changes to the APS Feed project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

---

## [Unreleased]

## [3.4.4] - 2026-10-08

### Fixed - 零切换组粒径升序落不下来 🔧

`postprocess_zero_groups` 的择优 key 是 `(换型, -连续组内产品数, 蛋白逆序数)`，
**不含「块内是否已有序」**。当蛋白相同、只差粒径时，已有序排列与待排排列在三项上
**字典序完全相等**，而采纳条件是严格小于 —— **粒径升序一次都落不下来**。

业务对 pgl 相同的零切换组正是这种情形（`zhanjiang-0920` 的 `[5070349, 5029300]`
两个 pgl 都是 80）。补第 4 项 `_disorder(seq, mem)` = 块内相对 `_sort_key` 的逆序数：

```
择优 key → (换型成本, -连续组内产品数, 蛋白逆序数, 块内乱序数)
```

优先级语义不变：换型（pri=1）仍排在蛋白/粒径（pri=2）之前，块内乱序只在前三项打平时起作用。

**验证**：业务 14/14 全绿；17 case 对比 `v3.4.2` 基线 **17/17**，sw 8426.7 持平，
cv_all 29.22→29.24（波动），pgl_v 全部不变。

### Fixed - `/estimate-task` 漏计「根本排不了」的产品 🔧

`estimate_production_load` 对 `capable_lines` 为空的产品直接 `continue`，既不进
`order_metrics` 也不进 `unassigned`。于是 `unassignedCount` **只统计「有可行线但没抢到」的**，
把「根本排不了」的漏掉 —— 业务看到的未分配数系统性偏小。改为计入 `unassigned`
（顺带把 `unassigned = []` 提到循环之前，否则该路径下 `UnboundLocalError`）。

### Fixed - Docker 镜像把本地数据与日志烤进去 ⚠️

`Dockerfile` 用 `COPY . /opt/haid` 且此前**没有 `.dockerignore`**，构建上下文约 45MB 全部进镜像：

| 带进去的 | 体积 | 风险 |
|---|---|---|
| `aps-log/` | 35M | 排产明细日志（产品 ID、得分、时刻） |
| `.git/` | 7.7M | 全部提交历史 |
| `data/` | 2.5M | **真实客户排产请求** |
| `.pytest_cache/` | 40K | — |

`data/` 与 `aps-log/` 都在 `.gitignore` 里——本地不进版本库，**却会进生产镜像**。
新增 `.dockerignore` 后构建上下文 **25 个文件 / 211 KB**。清单同时排除 `docs/`、`tests/`。
若线上排障需要 case 数据，请显式挂载卷。

### Changed - 死代码与构建残留清理

- 删 `scheduler/db_access.py` 整模块：`save_schedule_to_db` **零调用者**
  （`f67bca6` 已移除结果落库只留 HPAAS 回传，调用方删了模块留着）
- 删 `mysql_util.insert_data` / `batch_insert` / `update_data` / `delete_data`
  （后两个是模板桩，注释还写着「你可以继续添加」）。`MySQLDB` 现只保留实际使用的
  `__init__`/`connect`/`check_and_reconnect`/`close`/`execute_query`/`select_data`
- `README` 去掉 `pyscipopt`（SCIP 早已迁到 CP-SAT）
- `Dockerfile`：去掉 `apt-get install gunicorn`（`pip` 已装，避免两套版本打架）；
  启用 `HEALTHCHECK`（`/test` 是常驻健康检查路由）

### Added - 纯函数单测补齐（unit/ 9 文件 95 用例）

此前断言集中在 v2 权重公式 / PR0010 权重→结构 / 业务端到端，`scheduler/` 纯函数基本裸奔：

| 文件 | 用例 | 覆盖 |
|---|---|---|
| `test_zero_groups.py` | 11 | 零切换组后处理逐条语义（聚拢、clique 才排序、0链≠0团、换型变差不采纳、`IS_UnScheduled` 当 0、`line_times` 清理） |
| `test_capacity.py` | 17 | 生产时间、切换时间、优先级得分、产能兜底 |
| `test_estimate.py` | 12 | `/estimate-task` 唯一实现（只 patch DB 产能加载） |
| `test_scoring.py` | 9 | 分段电价积分 |
| `test_result_builder_helpers.py` | 8 | 漏排解释文案 |

写这些测试时断出了上面两处缺陷。另把两处「现状与直觉不符」的行为用断言钉住：
`cal_production_time` 订单量 0 时优先级硬编码 10（最低档 5 分）；缺产能记录时
`line_priority` 取 0，而 0 在 `priority_weights['line']` 是**最高档** 100
（实际影响有限：缺产能产品在 `_build_feasible_rids` 已被过滤）。

## [3.4.3] - 2026-10-08

### Fixed - 回归口径：含空线 CV 的空线曾凭空消失 🔧

`collect_regression.py` 计算 `cv_all` 时用 `len(r["schedule_details"])` 当产线总数，
但 **`schedule_details` 会整条省略空产线**（实测 `1500-0810`、`zhanjiang-0921-9digit`
输入 7 线只返回 6 条）。于是 `loads_all == loads`，`cv_all` 恒等于 `cv_used`——
CLAUDE.md 记的「坑 #2 含空线口径」写在文档里却**从未真正生效**。

改为分母取 `len(data["productionLine"])`。

| case | cv_all（错口径） | cv_all（修正） |
|------|----------------|---------------|
| 1500-0810 | 34.40 | **55.20** |
| zhanjiang-0921-9digit | 43.50 | **62.30** |

其余 15 个 case 无空线，数值不变；`sw` / `sched` / `pgl_v` 不受影响。

- `tests/baselines/v3.4.2.json` — 新基线（修正口径）：17 case，sw **8426.7**，
  cv_all 均值 **29.22**，排产 **465**
- `tests/baselines/v3.4.1.json` — 保留并在 meta 标注 bug，仅作历史对照
- ⚠ **历史结论需重读**：CHANGELOG 3.4.0 的「均衡 CV 16/16 不劣」是用错口径测的；
  3.4.1 的「1170-1 CV 13→28.7」「1500-0729 CV 49.6→57.8」同理。趋势方向多半仍成立，
  但绝对值不可与本版基线比。

### Changed - PR0010 均衡用例重写

`tests/scratch/test_balance_unit.py` 删除，由 `tests/unit/test_balance.py` 取代。

旧用例断言「`ruleWeight`=100 的 `time_diff` ≤ 权重0」且 **FAIL**（1086 vs 567）。
已核对 **v3.4.2 拆分前代码（`cc218b2`）同样失败、数值一致**，非拆分引入回归。
旧断言本身错了三处：口径剔除空线（坑 #2）、单轮比对（坑 #3）、
`test_balance_score_calculation` 是恒真式（断言 `balance_score == min_load - max_load`，
而该值就是照这个公式算的）。

实测（含空线 CV、3 轮均值）**PR0010 权重确实有效**，旧口径把结论说反了：

| case | w=0 | w=200 | w=500 | w=1000 |
|------|-----|-------|-------|--------|
| request (6线30产品) | 32.47 | 28.06 | 20.27 | 15.68 |
| 1130 (7线22产品) | 91.34 | 72.22 | 72.26 | 72.26 |

旧口径下 1130 看似「w=100 更差」，因为 w=0 只用 4/7 条线，3 条空线被剔除后伪算成均衡好。

新用例 4 条：权重 0→200 含空线 CV 必须下降且排产数不变、权重递增趋势不反弹、
校准区间（0/200/500）排产率 100%、口径自检（有空线时 `cv_all` 严格 > `cv_used`）。
它是**唯一断言「权重 → 求解结果」**的用例（`test_totalstrategy` 只验公式不验结果）；
`production_balance` 曾静默失效（v2 非核心打折压到 20、base 50→80→200 反复调），
没有它就只能靠翻日志。

### Changed - 数据目录归位

- `data/v1/uat_test_new_rules.py`（测试脚本混在 gitignored 的 `data/` 里）→ `tests/scratch/`
- `data/uat_full_regression.json`（2026-08-04 UAT DB 全量回归快照）→ `tests/baselines/uat-db-full-2026-08-04.json`

### ⚠ 已知边界

显式 `ruleWeight` 大到 ~2000 时，均衡项压过 `base_scheduling_bonus`，开始丢产品
（实测 `request` w=2000 → 22/30、`1130` w=2000 → 20/22）。v2 接口最多产出
`production_balance`=200，不在该区间，故未作为不变量。若要收紧，需同时保证
排产优先于均衡（提高 `base_scheduling_bonus` 或给均衡项加上界）。

## [3.4.2] - 2026-10-08

### Changed - 求解核心模块拆分 + 测试分层与回归基线 🧹

无算法行为改动（回归已验证持平），但导入路径与测试脚本路径有变。

#### `scheduler/model_build.py` 1132 行 → 4 个模块

| 文件 | 行数 | 职责 |
|------|------|------|
| `stage1_assign.py` | 484 | Stage1 纯分配：`is_scheduled`、容量/均衡/R5 约束、成对共置项、前置延迟成本 |
| `stage2_ordering.py` | 426 | Stage2 组件化排序：混合机连通分量、逐线 Circuit、跨线 PR0014 |
| `zero_groups.py` | 99 | 零切换组后处理：同线聚拢 + 组内蛋白降序 |
| `model_build.py` | 179 | 入口编排 `schedule()`/`solve_and_extract()` + 兼容再导出 |

拆分依据是**决策类型**而非代码模块：Stage1 管「上哪条线」、Stage2 管「怎么排」，
边界与目标函数天然对应。外部引用面 `model_build.schedule`/`build_model`/
`_refine_line_ordering` 保持可用。

**验证**：17 case 回归对比 `v3.4.1` 基线 **17/17 持平或改善**，0 项 FAIL
（sw 8433.3→8426.7，cv_all 26.90→26.87）；test_totalstrategy 24/24、
test_facade_compatibility 3/3、test_new_rules 7/7、test_all_data 10/10、
check_business_issues 14/14、test_mixer_isolation PASS。

#### `tests/` 三层分层

- `tests/unit/` — unittest 套件
- `tests/regression/` — **准入门槛**（17 case 回归、业务断言、混线隔离、全量冒烟）
- `tests/scratch/` — 2026-08 均衡调权期一次性诊断脚本，不接 CI
- `tests/baselines/` — 回归基线 JSON（入库）
- `tests/README.md` — 目录说明 + 回归三坑口径

脚本路径引导改为向上查找 `constant.py`，不再依赖「脚本在 `tests/` 一层」。

#### 回归基线固化

- 新增 `tests/baselines/v3.4.1.json`：**17 case**，sw 合计 **8433.3**、
  cv_all 均值 **26.90**、排产 **465**、墙钟 ~13s
- 新增 `tests/regression/compare_regression.py`：逐 case 比对 + 退出码门槛
- 此前「换型 8770→8068」这类结论只存在于 CHANGELOG 文字里，无法复验

#### 口径修正

- 回归 case 实为 **17** 不是 16（`zhanjiang-0923-9digit.txt` 后补）；
  `data/v2/1130-1.txt` 是 0 字节空文件（无法恢复，采集器本就跳过），已清理。
  文档中历史数字（3.4.1 的 8068、8770 等）保持原样不改写。
- **R5 语义**（前置占用线禁排同类型新产品）暂按当前实现保留、不改代码，
  已在 `CLAUDE.md` 记为待办 T1 待业务确认（详见该文件）。

#### ⚠ 已知问题（非本次引入）

`tests/scratch/test_balance_unit.py` 当前 FAIL（1130 case：权重100 的时间差异
1086 > 权重0 的 567）。已核对 **v3.4.1 拆分前代码同样失败且数值一致**。
该用例断言在当前架构下已不成立：口径剔除空线（违反回归口径 #2）、单轮比对
（违反 #3）、给 `production_balance`=100 而同文件 `changeover_time`=500 未校准。
处理建议记在 `tests/README.md`，在重写前不要把它当门槛。

## [3.4.1] - 2026-10-08

### Fixed - 零切换物料组拆开/乱序（2026-09-29 业务 7 文件问题）🔧

- **业务问题**：零切换物料组被拆开生产（跨线/同线不连续）、组内未按蛋白降序——
  4 个数据集 14 项断言（`tests/check_business_issues.py`），旧算法 3/14 且波动。
- **根因 1（拆开）**：均衡项(w=100) 压过分组激励——对照实验 balance=0 时全部同线连续；
  产能仅占 44~59%，非容量问题。属目标权衡的结构性问题，非调权重可根治。
- **根因 2（乱序）**：`_postprocess_zero_switch_ordering`（d263ee2）在 Stage1 简化重构
  重写 result_builder 时丢失。
- **修复**（严格遵循优先级语义：只在不损害高优先级的前提下满足低优先级）：
  - Stage1 零切换组（双向互切=0 连通组）同线约束：`y[组,线]` + 用线数惩罚
    （2×max(换型,均衡)×K，代表真实边界换型开销），产能不足时自动最少拆分
  - 后处理：同线组员聚拢成连续块（枚举插入位置取换型最小，仅采纳 ≤ 现状），
    块内两两互切=0 时按蛋白降序+粒径升序重排（0链≠0团不自由排）；
    换型成本变差的排列一律不采纳（换型 pri=1 > 蛋白 pri=2）
- **验证**：业务 14/14 全绿（3 轮稳定）；16 case 回归换型 8770→8068（-8.0%，
  较旧基线 8536 亦 -5.5%），均衡 CV 多数改善；test_all_data 10/10、
  test_totalstrategy 24/24、test_mixer_isolation PASS。
- 已知对价：1170-1 CV 13→28.7、1500-0729 CV 49.6→57.8（分组让出的均衡，换型再降 7~9%）。

### Changed - 文档与工程收口（2026-10-08）📚

- **文档对齐实现**（此前多处仍停在旧算法/旧权重，会误导调参）：
  - `docs/APS算法计分策略文档.md` → v2.1：权重表与推荐配置同步 `base_weights`
    （protein 80→**200**、changeover 150→**200**、balance 50→**200**）；新增 §十三
    「v2 权重计算与两段式求解」（v2 公式、量纲归一化、Stage1/Stage2 目标分工、零切换组）；
    §6.1 影响量级改为归一化口径（满分贡献恒 = `OBJECTIVE_SCALE_K × weight`）；
    §11 求解器参数同步实际值（超时 150s→**30s**、worker 自适应 `min(max(cpu//3,4),16)`）
  - `docs/APS调度系统算法演变史.md` → v1.3：新增**第十一阶段**（两段式求解、成对共置项、
    零切换组聚合、PR0014/前置占用、回归口径教训）；「系统架构与代码组织」从 MTZ/SCIP
    更新为 `scheduler/` 包 + 两段式数据流
  - `README.md`：新增「架构」章节（两段式 + 数据流 + 关键模块）；测试章节改为
    unittest 套件 + 回归脚本的真实清单
- **纳入版本管理**：`CLAUDE.md`（工作指引）与 `docs/APS调度系统算法演变史.md` 原在
  `.gitignore` 中，换机器/他人 clone 即丢失——已移出忽略列表并入库
- **chore**：删除根目录 `gen-requirement.py`（`pip freeze` 全量导出会把开发环境无关包
  灌进 `requirements.txt`）；`requirements.txt` 加维护说明，改为手工维护


## [3.4.0] - 2026-09-29

### Changed - 两段式求解重构: Stage1 纯分配简化 + 组件化 Stage2 🎯

#### Stage1 弧模型 → 纯分配
- **动机**：两段式拆分后 Stage2 已负责线内精确排序，Stage1 的弧/序列变量冗余，
  拖慢求解、扩大 gap。简化为 `is_scheduled` 布尔分配 + 分配级近似目标。
- **目标项**：base_bonus + priority + electricity + balance(保持 SCALE 机制)
  + 蛋白/粒径 beat_count 近似 + 换型 avg 近似 + **成对共置项**。
- **成对共置项（关键修复）**：单品级换型近似只看"可行集平均"，与真实同线伙伴无关，
  会把零切换物料组拆散（1500-0729: 小猪851 组拆到 CX05/CX06、A6 组拆到 CX03/CX04）。
  新增 `both = is_sched(l,i) AND is_sched(l,j)` 成对项，互切越低的对同线得分越高
  （整组同线收益 ~C(k,2)），按 0.25x 校准避免压过 balance（全量会把 1170-1 CV 推到 59%）。

#### Stage2 组件化 + 跨线 PR0014
- 按混合机连通分量分组求解（`_build_mixer_components`），无共享混合机时退化逐线求解。
- 分量内联合建模跨线 PR0014：产品-产品析取硬约束 + 前置隔离软约束，gap = mixerWaitTime×60。
- 方案C：Stage1 分配目标加入前置占用延迟成本，让分配感知"排到被占用线要付等待代价"。

#### result_builder 修复
- 新 `build_result_from_assignment`：首产品换型按 `switch_matrix[产线recordId → rid]` 计入
  —— 旧版 `apply_refined_ordering` 对首产品恒记 0（产线 recordId 不在 product_attrs），
  系统性漏算每线首次换型，导致对比基线失真。
- 死代码清理：删 `_build_per_line_arcs` / `_add_valley_electricity_bonus` / `_log_model_stats` 等。

#### 回归（16 case × 3 轮平均，真实口径：链式换型 + 含空线 CV）
- 换型 8536→8770（+2.7%）；均衡 CV **16/16 不劣**；蛋白违反 6.7→5.0；1580 排产 7/8→8/8。
- ⚠ 已知对价：1500-0729-bp1 / 0810 / 0819 换型 +14~22%，换均衡 CV 改善（这些 case
  production_balance w=100 核心，符合权重语义）；1580-mix 换型 +14% 为 PR0014
  跨线隔离串行化的正确安全代价。
- 测试：`test_all_data` 10/10、`test_totalstrategy` 24/24、`test_mixer_isolation` PASS。

#### 权重测试期望适配（d263ee2 protein base 80→200 遗留）
- `test_高priority权重永远大于低priority` 不变量修订为「同 base 量级下阶梯权重保持」
  （跨规则 base 差异可覆盖 priority 差）；1170 锁定期望 protein_grade 20→50。

### Fixed - PR0014 混线隔离失效 + v2 产线均衡权重（2026-09）🔧

#### 前置订单占用时长未建模（根因）
- **问题**：`_build_per_line_arcs` 用 `prod_time_matrix[(pre_rid, l)]` 算前置订单结束时刻，
  该值由 `cal_production_time(pre_pid, l, qty)` 得出，而前置 `plannedProductionQuantity=0`
  → 返回 0。实际占用时长在 `line.preOrderProduceTime`(小时)，未进模型。
- **后果**：`end_times[(l,0)]=0`，PR0014 前置隔离约束 `start >= pre_end` 恒真 → **混线隔离完全失效**
  （1580 案例：5066128 应排 CX03 却排到 CX02）。
- **修复**：`end_times[(l,0)]` 优先取 `preOrderProduceTime × 60`，字段缺失/为 0 时回退 `prod_time_matrix`。

#### 前置隔离约束缺 is_scheduled 保护
- **问题**：`Add(start_times[dst] >= pre_end)` 无 `OnlyEnforceIf`。未排产品 `start_time` 被钉死为 0，
  `pre_end > 0` 时即 `0 >= pre_end` → INFEASIBLE。修上一条后立即暴露。
- **修复**：补 `.OnlyEnforceIf(is_scheduled[(dst_line, rid_j)])`；
  `_add_mixer_isolation_constraints` 增加 `is_scheduled` 形参。

#### 结果抽取与模型不一致（等待间隙被吞）
- **问题**：`extract_solution_cp` 用「紧凑回放」重建时间轴，不读 solver 的 `start_times`，
  模型强制的等待间隙（混线隔离/时间窗）在返回结果里丢失 → **报出的时刻可违反模型实际约束**。
- **修复**：`build_model` 导出 `start_times`；抽取改读 solver 实际取值，间隙显式落成 `idle` 时间槽；
  保留无 `start_times` 时的紧凑回放回退。

#### R5 — 前置占用线禁止再排同类型新产品（⚠️ 待业务确认）
- **规则**：有前置占用（`preOrderProduceTime > 0`）且类型为 T 的产线，其自身类型 T 的新产品不得再排该线。
- **效果**：1580 案例 5066128（组合1）从 CX02 改为 CX03。
- **⚠️ 语义待确认**：业务原始表述为「其他共用混合机的线只能排同类型」；按字面，CX02（前置同为组合1）
  排 5066128 合法，推不出 CX03（已实测 V1/V2 两版实现均落 CX02）。R5 额外禁止「占用线自身再排同类型」，
  与字面表述有差异，**已提交业务确认**。

#### v2 权重：production_balance 被非核心打折压低
- **问题**：`_calculate_weights_v2` 对 production_balance 套「非核心×0.5 × priority 衰减」，
  1500 系列入参算出 20（生效门槛的 1/10）。权重扫描证明 load-balance 需 w≥200 才压住完工时刻极差。
- **修复**：production_balance 豁免非核心打折（`goal_mul` 恒 1.0）；`base_weights['production_balance']` 80 → 200。
- **验证**：1500-0915 完工极差中位 940→615 分，CX02 不再饿死；1170 期望值同步更新。

#### 验证
- `test_all_data.py` **10/10 通过**；`test_totalstrategy.py` **24/24 通过**；1500-0915 **27/27 排产**。
- 1580 案例 5066128 稳定落 CX03（3/3 轮），CX03 组合2 产品正确等待至 240 分开工。
- 已知副作用：1580 排产率 8/8 → 7/8（前置占用容量被正确计入，属修正后的正确行为）。
- 详细记录见 `docs/混线隔离与前置占用修复_2026-09.md`。


### Changed - v2 权重体系重构 🎯

#### 产线均衡优先级感知 SCALE
- **问题**：balance 目标函数是固定范围（0~1440min），不随产品数 N 增长；而 changeover 每产品一条 arc，总分∝N。N 越大 balance 越无力，priority 机制失效。
- **公式**：`SCALE = max(1, N // (L × priority))`，N=总产品数，L=活跃产线数，priority=PR0010 的优先级。
- **效果**：
  - priority=1 核心（SCALE=N/L）：balance 与 changeover 量级对齐，均衡真正主导
  - priority=4 非核心（SCALE=1）：balance 远低于 changeover，不干扰换型优先
  - 完全自动，无硬编码系数
- **验证**：1500-0729：priority=4 时 CX06=15产品(36.8%均衡度)；priority=1 时 CX05=7/CX06=8(71.0%均衡度)。其他 v2 文件排产率100%，聚合稳定。

#### 产线均衡硬约束保障
- **问题**：产能过剩场景下，产品全部堆到 pre-product 切换成本低的产线（如1500-0729：CX05=0产品，CX06=15产品），导致有可行产品的产线完全空置。
- **改动**：当 PR0010（production_balance）权重 > 0 时，添加硬约束：每条有可行产品的产线至少排 1 个产品。剩余产品的分布仍由均衡软项（balance_diff × weight）引导。
- **验证**：1500-0729：CX05 从 0 产品 → 1 产品（硬约束生效）。全部 v2 文件排产率 100%。

#### 谷电利用率优化
- **问题**：算法目标函数用全天加权均价 (`qty×uec×avg_price`)，无法区分谷电/峰电时段。当产品 UEC 不同且有时间窗约束时，算法可能选不到文档评分最高的排法。
- **改动**：新增谷电时段显式 bonus：检测每条产线的最低电价时段，在该时段内排产的产品获得 `valley_minutes × uec × qty × elec_weight / N_products` 的额外加分。
- **安全设计**：bonus 按产线产品数归一化（÷N），避免谷电项过大导致少排产品。
- **验证**：全部 v2 文件排产率 100%，不影响现有排产。

#### 均衡度归一化（部分修复）
- **问题**：算法用绝对时间差 `(min_load - max_load)`，文档用归一化 CV `1 - std(Ri)/mean(Ri)`。当产线容量不同时两者优化方向可能相反。
- **现状**：CP-SAT 精确实现 CV 需要整数除法归一化（`load/capacity`），但 `AddMultiplicationEquality` 会产生整除性约束导致大量产品无法排产。已验证此问题，暂回退到简单 min-max。
- **缓解**：实际产线容量多为1440min（24h），差异不大时 min-max≈CV。方向一致（都鼓励均匀分配），不会出现算法排得差但文档评分高的情况。
- **后续**：如需精确 CV 优化，需改用支持浮点目标的求解器（如 SCIP），或在 post-processing 层做局部搜索微调。

#### 放宽 solver 提前终止阈值
- **问题**：原阈值 `gap<0.5%` 且 `≥3个解` 时提前终止，导致 solver 在找到次优可行解后过早停止，偶发地跳过"零切换物料聚合同线"的更优解（CP-SAT 多线程搜索的非确定性）。
- **改动**：`gap` 阈值 0.5% → **0.01%**，最少解数 3 → **10**。让 solver 多探索几秒，显著提高找到最优解的概率，同时不无限跑。
- **验证**：1170-0723 连续 5 次运行全部聚合到同线 ✓，耗时 0.7~1.6s。

#### soft switch_cluster 聚合 bonus
- **问题**：同半成品、同 dia、互切=0 的产品（天然应排一起）偶尔被 solver 拆到不同产线——并非容量不够，而是搜索终止时机碰上次优可行解。
- **改动**：
  - 新增 `_precompute_switch_clusters()` 方法：按 `(semiFinishedProduct, dia)` 分组，再用 union-find 按 `switch_time==0` 合并连通分量，每个分量为一个 cluster。
  - 目标函数新增 cluster bonus：同 cluster 的产品排到同线相邻时，每条弧加 `SWITCH_CLUSTER_BONUS=1000` 分（约等于 PR0007 蛋白核心目标总分的 2.5 倍）。
- **安全设计**：这是 soft bonus，不是硬约束——容量放不下时 solver 会自动放弃 bonus 选择分线，不会报 INFEASIBLE。
- **验证**：1130-3（容量放不下的 case）仍 23/23 100% 排产，不崩溃；1170-0723 连续 5 次全聚合。

#### 废弃 totalStrategy 机制
- **背景**：`totalStrategy`（1=换型翻倍 / 2=均衡额外项）与 PR0009/PR0010 规则系统功能重叠，用固定硬编码系数（×2/×10）绕过权重配比，导致前端设的优先级被稀释。
- **改动**：
  - `aps.py`：`Aps.__init__` 移除 `total_strategy` 参数；删除 4 处 `==1`/`==2` 历史分支（换型 ×2、`min_load×10` 均衡额外项、评分函数 2 处）。
  - `aps_feed.py`：`submit-task`/`submit-task-v2`/`estimate` 三接口不再读取 `totalStrategy`。
  - `constant.py`：schema 移除 `totalStrategy` 字段（`additionalProperties: False` 会拒掉误传）。
- **影响**：入参本就不传 `totalStrategy`，对前端零影响。

#### priority 衰减改为指数曲线，确保优先级充分拉开
- **问题**：原线性衰减 `1 - priority × 0.15` 下，priority 1→2 比值仅 1.17x，导致 priority=1 的蛋白（base 80）权重 ≈ priority=2 的产线（base 100），违反"priority 高的应明显大于低的"设计意图。
- **改动**：`priority_factor = WEIGHT_PRIORITY_BASE ^ (-priority)`，`WEIGHT_PRIORITY_BASE=2.0`，即 priority 0→1→2→3 的 factor = 1.0 / 0.5 / 0.25 / 0.125，**每级严格 ÷2**。
- **保证**：priority 高的规则权重永远压过低 priority，不被 base 差异掩盖（base 100/pri1=50 永远 > base 80/pri2=20）。
- `WEIGHT_PRIORITY_FLOOR` 由 0.1 改为 0.0625（2^-4，priority≥4 触底）。

#### changeover 基准权重提高
- `base_weights[changeover_time]`：150 → **200**。
- 实测 priority=1、非核心时权重需达 ~50（=200×0.5×0.5）才能让零切换物料聚合同线连续排产，真正体现"换型最短第一优先级"用户要求，无需历史 totalStrategy 翻倍。

#### 权重调试指引
当排产方向不符合预期时：
1. 看日志 `v2 权重计算结果` 确认各规则 weight
2. 检查 priority 顺序：factor=2^-pri，pri0>pri1>pri2 每级 ÷2
3. 某 priority=1 规则没主导排产 → 提高其 `base_weights`（priority 是用户语义，base 是系统量级）
4. changeover 聚合阈值经验值：priority=1 非核心时 weight ≥50 才稳定聚合

### Fixed - totalStrategy 类型归一化（已在废弃前修复）
- v2 接口默认值 `'2'`（字符串）与 `aps.py` `== 2`（整数）比较失效，导致均衡约束静默跳过。该路径随 totalStrategy 机制废弃一并消除。

---

## [3.3.0] - 2026-05-28

### Added - 智能排程算法改造 🚀

#### PR0012 - Stage 分配与顺序约束
- 根据 `isDosing`、`isColoring`、`specialProcessType` 自动计算产品 Stage（1-4）
  - Stage 1: 普通产品
  - Stage 2: 加色产品（`isColoring=1` 或 `specialProcessType` 包含 COLORING）
  - Stage 3: 加药产品（`isDosing=1` 或 `specialProcessType` 包含 DOSING）
  - Stage 4: 加色+加药产品
- 同一产线内强制 Stage 递增顺序，防止特殊物料污染普通产品
- 新增字段：`isDosing` (0/1), `isColoring` (0/1), `specialProcessType` (字符串)

#### PR0013 - 混合机隔离约束
- 检测共享混合机的产线对（通过 `assignedMixers` 字段）
- 仅单混合机产线参与隔离约束
- 多混合机/无混合机产线不受影响
- 共享混合机的两条产线不能同时生产
- 新增字段：`assignedMixers` (数组), `mixerWaitTime` (数值)

#### PR0014 - 等待节点机制
- 为每条产线创建虚拟等待节点（零时长、零成本）
- 等待节点继承产线的 Stage 和混合机属性
- 当产线需要等待共享混合机释放时，自动插入等待节点
- 等待节点不计入排产数量，不影响利用率统计
- 等待节点标记：`is_wait=True`

### Fixed
- 修复等待节点绕过 Stage 约束的问题
- 等待节点现在正确遵守 Stage 顺序约束
- 产线均衡计算逻辑错误：修复产线负载只取单个产品最大时间而非累加所有产品时间的问题

### Testing
- 新增 7 个新规则单元测试（`test_new_rules.py`）
- 新增 5 个回归测试（`test_balance_unit.py`）
- 全部 12 个测试通过
- 7 个历史数据文件回归测试通过
- UAT 真实数据验证通过（23 产品/7 产线，96% 排产率）

### Documentation
- 完整设计规格说明书（`docs/superpowers/specs/2026-05-21-aps-scheduling-rules-design.md`）
- 实现方案决策记录（`docs/requirements/DECISION_RECORD_20260520.md`）
- 部署报告（`docs/archive/DEPLOYMENT_REPORT.md`）
- 测试报告（`docs/archive/FINAL_TEST_REPORT.md`, `docs/archive/TEST_REPORT.md`, `docs/archive/TEST_SUMMARY.md`）
- 工作总结（`docs/archive/WORK_SUMMARY.md`）

### Compatibility
- **向后兼容**：新字段缺失时使用默认值
  - `isDosing` 默认 0
  - `isColoring` 默认 0
  - `assignedMixers` 默认 []
  - `mixerWaitTime` 默认 0
- 旧数据文件无需修改即可正常运行
- 无性能退化

---

## [3.2.0] - 2026-05-08

### Changed
- **产线均衡策略优化**：从产品数量均衡改为时间均衡（基于 `used_time`）
- 均衡得分计算使用 `min(used_time) - max(used_time)` 替代产品数量差
- 切换到 UAT 环境配置

### Improved
- 更符合实际生产场景，避免"排很多小产品"与"排少数大产品"的不公平比较

---

## [3.1.1] - 2026-04-03

### Fixed
- **关键修复**：修复电耗权重导致负分和不排产问题 ⚠️
  - 当 PR0011（电耗成本）的 `ruleWeight` 设置过大（如 80）时，电耗惩罚可达 -60万，导致求解器选择"不排产任何产品"（0 分 > 负分）
  - **解决方案**：PR0011 权重必须保持为 1，若需更强省电倾向，建议在 5～10 范围内，不宜超过 50
- 修复 PR0006 时间窗口逻辑
- 修复 `fitGrade` 和 `productSchedulingScore` 计算
- 确保电耗作为纯惩罚项不会压制排产激励

### Documentation
- 在 `APS算法计分策略文档.md` 中明确说明电耗权重陷阱

---

## [3.1.0] - 2026-03-23

### Added
- 增强排产结果构建逻辑
- 新增未排产产品的处理机制
- 产能缺失时的容错机制

### Improved
- 优化生产时间计算
- 提升排产系统的健壮性与准确性

### Changed
- 恢复生产环境配置 `IS_PROD=True`

---

## [3.0.0] - 2026-03-10

### 🎯 Major Release - CP-SAT 重构版

#### Changed - 求解器迁移
- **重大架构变更**：求解器从 SCIP MIP 迁移到 Google OR-Tools CP-SAT
  - **原因**：SCIP 在部分实例上收敛慢、Gap 难以收窄
  - **新建模方式**：Circuit 约束（Hamilton 回路）替代 MTZ + Big-M
  - **性能提升**：求解时间从数分钟降至数秒～2.5分钟

#### Added - HPAAS 集成
- 集成 HPAAS 开放接口
  - 结果回写方式变更：不再写数据库，改为调用 HPAAS API
  - 鉴权机制：OAuth2 client_credentials
  - 接口：POST `/metricsResult`
  - 实现：新增 `HpaasApiClient` 类管理 token 刷新与重试

#### Added - 电耗成本规则
- 新增 PR0011 电耗成本规则
  - 目标：整体电耗成本最低
  - 计算公式：`电耗成本 = 排产量 × 单吨电耗 × 电价`
  - 目标函数：`- int(电耗成本 × 100) × W_electricity_cost`
  - **推荐权重**：1（关键！）

#### Added - 时间窗口得分规则
- 新增 PR0006 时间窗口得分规则
  - 生效条件：`earliestStartTime` 或 `latestCompletionTime` 任一端在 (0, 24) 小时内
  - 计分规则：满足窗口得 1 分，否则 0 分
  - 加权得分：`in_time_window × W_time_window`（推荐权重 200）

#### Fixed - 总分一致性
- 修复 totalStrategy 附加项未纳入对外展示的 `total_score` 问题
  - `totalStrategy=1`：总分再加一次换型加权得分
  - `totalStrategy=2`：总分再加 `min_load × 10`

#### Technical Details
- 弧剪枝 (Arc Pruning)：预先剪除不可行的弧，减少变量规模
- 系数要求：CP-SAT 要求目标函数系数为整数，所有权重转为 `int`
- 时间约束：用 `OnlyEnforceIf(弧选中)` 传递，无需 Big-M

#### Documentation
- 更新 `APS算法计分策略文档.md` 至 v2.0
- 更新 `APS调度系统算法演变史.md` 至 v1.2
- 新增第十阶段：CP-SAT 迁移与电耗规则

---

## [2.5.0] - 2026-02-10

### Added
- 增强排产功能，添加得分信息回调
- 支持变量稀疏化以提高效率

### Improved
- 优化决策变量创建逻辑

---

## [2.4.0] - 2025-12-16

### Added
- **新增产线均衡度约束功能 (PR0010)**
  - 计分规则：`产线均衡得分 = min(各产线排产数量) - max(各产线排产数量)`
  - 得分始终 ≤ 0（完全均衡时为 0）
  - 推荐权重：50
- 支持动态权重配置
- 计算并记录均衡得分

### Improved
- 优化生产调度逻辑

---

## [2.3.0] - 2025-12-02

### Added
- 添加排产预估功能
- 支持整体排程策略选择 (`totalStrategy`)
  - `totalStrategy=1`：时间最短（换型权重翻倍）
  - `totalStrategy=2`：均衡生产（最大化最轻负载产线）
- 允许产线加班设置

### Improved
- 优化生产调度逻辑

---

## [2.2.0] - 2025-11-16

### Changed
- 更新 `materialPriority` 枚举值（0-10）
- 调整优先级权重逻辑以支持更细致的优先级划分
- 优先级分数映射表更新

---

## [2.1.0] - 2025-07-09

### Fixed
- 修复约束参数顺序
- 确保时间窗口逻辑正确实现

---

## [2.0.0] - 2025-06-18

### 🎯 Major Release - MTZ 模型优化版

#### Changed - Big-M 精确化革命
- **问题**：全局 Big-M 过大（≈ 百万），导致 LP 松弛过于宽松
- **解决**：按产线计算精确 Big-M
  ```python
  M[l] = line_remaining_time[l] + max_switch_time[l] + 1
  ```
- **效果**：LP Gap 从 5-10% 收紧到 0.12%，求解时间从数十分钟降至数分钟

#### Added - Lifted Subtour Elimination 割平面
- 约束：`y[l][(i,j)] + y[l][(j,i)] <= z[l][i]`
- 效果：将序列变量 `y` 和分配变量 `z` 紧密联系，增强 LP 松弛

#### Improved
- 启发式解质量提升
- 贪心算法优化

---

## [1.0.0] - 2024-2025

### Initial Release - MTZ 模型初版

#### Core Features
- **建模方式**：MTZ (Miller-Tucker-Zemlin) 序列模型
- **求解器**：SCIP MIP Solver
- **决策变量**：
  - `y[l][(i,j)]`：产品 j 是否紧跟在产品 i 之后
  - `z[l][i]`：产品 i 是否分配到产线 l
  - `u[l][i]`：产品 i 在产线 l 上的位置序号（MTZ 变量）
  - `start_time[l][i]`：产品 i 的开始时间

#### Constraints
- 唯一分配约束
- MTZ 子环路消除约束
- 时间传递约束（Big-M 方法）
- 产能约束

#### Objective Function
- 优先级得分（物料优先级 + 产线优先级）
- 换型时长最小化
- 蛋白档次降序排列
- 粒径升序排列

---

## 版本说明

### 版本号规则
- **主版本号**：重大架构变更（如求解器迁移）
- **次版本号**：新功能添加或重要优化
- **修订号**：Bug 修复和小改进

### 分支管理
- **master**：主开发分支
- **pro**：生产环境分支（`IS_PROD=True`）
- **uat**：UAT 测试环境分支

### 环境配置
通过 `constant.py` 中的 `IS_PROD` 标志控制：
- UAT 环境：`IS_PROD=False`
- 生产环境：`IS_PROD=True`

---

## 性能指标

### 当前性能（v3.2.0）
- **求解时间**：数秒～2.5 分钟
- **解质量**：Gap < 1%（通常能找到最优解）
- **规模处理能力**：
  - 产品数量：50-200 个
  - 生产线数量：5-20 条

### 业务价值
- 生产效率提升：15-25%
- 换型时间减少：30-40%
- 时间窗口满足率：>95%
- 人工排产时间节省：>90%

---

## 链接

- [项目文档](./README.md)
- [算法演变史](./APS调度系统算法演变史.md)
- [计分策略文档](./APS算法计分策略文档.md)
- [版本历史](./VERSION_HISTORY.md)
- [问题反馈](https://gitlab.haid.com.cn/aigroup/aps-feed/issues)

---

*维护者：APS 算法团队*  
*最后更新：2026-05-14*
