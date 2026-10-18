# tests/ 目录说明

三层结构，按「谁会跑它、结果怎么用」划分：

| 目录 | 内容 | 何时跑 |
|------|------|--------|
| `unit/` | unittest 套件，纯逻辑断言 + 权重→排产结构 | 每次改动随手跑 |
| `regression/` | 算法回归与业务断言，**准入门槛** | 算法改动必跑 |
| `scratch/` | 历史调参/诊断一次性脚本 | 基本不再跑，留作查证 |
| `baselines/` | 回归基线 JSON（入库） | 与新采集结果比对 |

## 准入门槛（算法改动必跑）

```bash
python -m unittest discover -s tests/unit -v               # unit/ 全部 95 用例
python tests/regression/test_all_data.py                   # data/v1 全量冒烟
python tests/regression/check_business_issues.py           # 业务断言 14 项
python tests/regression/test_mixer_isolation.py            # PR0014 / R5

# 或逐个跑
python tests/unit/test_totalstrategy.py                    # 24  v2 权重公式
python tests/unit/test_balance.py                           #  4  PR0010 权重→排产结构
python tests/unit/test_zero_groups.py                      # 11  零切换组后处理语义
python tests/unit/test_capacity.py                         # 17  生产/切换时间、优先级、产能兜底
python tests/unit/test_estimate.py                         # 12  /estimate-task
python tests/unit/test_scoring.py                          #  9  分段电价积分
python tests/unit/test_result_builder_helpers.py           #  8  漏排解释文案
python tests/unit/test_new_rules.py                        #  7  PR0012/13/14
python tests/unit/test_facade_compatibility.py             #  3  Aps 门面委托

# 17 case 回归 + 与基线比对
python tests/regression/collect_regression.py <代码根> /tmp/new.json 3
python tests/regression/compare_regression.py tests/baselines/v3.4.2.json /tmp/new.json
```

`tests/unit/test_balance.py` 是唯一断言「权重 → 求解结果」的用例（`test_totalstrategy`
只验公式不验结果）。production_balance 曾静默失效（v2 非核心打折压到 20、base
50→80→200 反复调），没有它就只能靠翻日志发现。

`compare_regression.py` 退出码非 0 表示有 FAIL，可直接做脚本门槛。

## 回归口径（三个坑，务必遵守）

1. **换型用链式口径**：沿最终序列 Σ `switch_matrix(prev→rid)`，首产品 `prev` = 产线 recordId。
   不要用结果里的 `switch_time` 汇总——旧版首产品恒记 0，会系统性低估 ~30%。
2. **均衡 CV 用含空线口径**：空线负载 0 计入分母。剔除空线会把「留空一条线」伪算成均衡好。
   ⚠ 分母必须取 `len(data["productionLine"])`，**不能**取 `len(schedule_details)`——
   `schedule_details` 会整条省略空产线（实测 1500-0810、zhanjiang-0921-9digit 输入 7 线只返回 6 条），
   用它当分母会让空线凭空消失、`cv_all` 退化成 `cv_used`。
   **2026-10-08 前的基线就是这个 bug 的产物**：`v3.4.1.json` 的 cv_all 未真含空线，
   受影响 `1500-0810` 34.4→55.2、`zhanjiang-0921-9digit` 43.5→62.3。以 `v3.4.2.json` 为准。
3. **CP-SAT 多线程不确定**：同文件轮间 CV 可摆动 17~41，单轮对比不可信。
   每 case 跑多轮取均值/范围；`collect_regression.py` 的 `ROOT` 只放目标代码根到
   `sys.path`，勿混入工作区，否则对比两侧会导入同一份代码。

## 数据

case 文件在 `data/`（gitignored，需自备）：
- `data/v1/` — 历史数据集，`test_all_data.py` 用
- `data/v2/` — 17 个回归 case，`collect_regression.py` / `check_business_issues.py` 用

`data/v2/` 可用 `APS_CASE_DIR` 环境变量覆盖。

## scratch/ 里是什么

2026-07~08 调产线均衡权重时留下的一次性脚本（`test_1335_*`、`test_balance*`、
`test_comprehensive`、`test_local`、`uat_test_new_rules`）。它们不是测试，是当时的诊断记录——
（`uat_test_new_rules.py` 原先混在 gitignored 的 `data/v1/` 里，2026-10-08 归位到这里；
`data/` 下的 UAT 全量回归快照移到了 `tests/baselines/uat-db-full-2026-08-04.json`。）
保留是为了「当时为什么把 base 调到 80」这类问题可以回查。**不要**把它们接进 CI，
也别把它们的输出当成回归结果。

### `test_balance_unit.py` 已删除，由 `tests/unit/test_balance.py` 取代

旧用例断言「`ruleWeight`=100 的 `time_diff` ≤ 权重0」，FAIL（1086 vs 567）。核对结论：
**v3.4.1 拆分前代码同样失败、数值一致**，不是拆分引入的回归。旧断言本身是错的：

1. **口径错**：`time_diff` 只统计 `used_lines`（剔除空线）——正是坑 #2。
2. **单轮比对**：违反坑 #3。
3. **`test_balance_score_calculation` 是恒真式**：断言 `balance_score == min_load - max_load`，
   而 `balance_score` 就是照这个公式算的，零信息量。

实测（正确含空线口径、3 轮均值）**PR0010 权重确实有效**，旧口径把结论说反了：

| case | w=0 | w=200 | w=500 | w=1000 |
|------|-----|-------|-------|--------|
| request (6线30产品) | 32.47 | 28.06 | 20.27 | 15.68 |
| 1130 (7线22产品) | 91.34 | 72.22 | 72.26 | 72.26 |

旧口径下 1130 看起来是「w=100 更差」（time_diff 567→1086），因为 w=0 只用 4/7 条线、
3 条空线被剔除后伪算成均衡好。含空线后 w>0 反而明显更好。

新用例断言：权重 0→200 含空线 CV 必须下降（且排产数不变）、权重递增趋势不反弹、
校准区间内排产率 100%、以及口径自检（有空线时 cv_all 必须严格 > cv_used）。

### ⚠ 已知边界：极端权重会压过排产

显式 `ruleWeight` 大到 ~2000 时，均衡项压过 `base_scheduling_bonus`，开始丢产品
（实测 `request` w=2000 → 22/30，`1130` w=2000 → 20/22）。v2 接口最多产出
`production_balance`=200，不在此区间，故未作为不变量；若要收紧，需同时保证
排产优先于均衡（提高 `base_scheduling_bonus` 或给均衡项加上界）。
