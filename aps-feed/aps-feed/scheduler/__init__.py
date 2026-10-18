"""scheduler — APS 排程核心模块包。

按职责拆分（解耦 + 可维护 + 可复用）:
  - context.py         ApsContext: 共享只读状态（产品/产线/矩阵/权重/电价...）
  - preprocessing.py   字段预处理、混合机分析、等待节点、生产/切换时间/cluster 预计算
  - capacity.py        DB 产能查询、生产/切换时间计算、优先级得分
  - callbacks.py       CP-SAT 求解过程回调（SolutionLogger）
  - model_build.py     求解入口编排 + 共享预处理 + 解抽取（兼容再导出 Stage1/Stage2）
  - stage1_assign.py   Stage1 纯分配: is_scheduled + 分配级近似目标 + 成对共置项
  - stage2_ordering.py Stage2 组件化排序: 逐线 Circuit + 跨线 PR0014
  - zero_groups.py     零切换物料组后处理: 同线聚拢 + 蛋白降序
  - result_builder.py  排程结果转换（HPAAS 格式）、日志打印、可行性分析
  - scoring.py         后评估分计算 + 分段电价积分
  - estimate.py        轻量级贪心负载预估（不走 CP-SAT）

aps.py 作为 facade 仅做组装：Aps.__init__ 构造 ApsContext，方法委托到上述模块。
"""
