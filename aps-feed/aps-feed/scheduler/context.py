"""ApsContext: 排程过程中各模块共享的只读/缓存状态。

设计意图:
  - 替代 Aps 实例上散落的 10+ 个 self.<attr>，让各模块函数签名统一为 fn(ctx, ...)。
  - 预计算缓存（prod_time_matrix 等）在 preprocessing 阶段填充后即只读。
  - mutable 字段（wait_nodes/shared_mixer_line_pairs/电价/_changeover_time_penalty）
    由对应阶段写入后即"冻结"，下游只读——避免隐式顺序耦合。
"""
from dataclasses import dataclass, field
from typing import Any


@dataclass
class ApsContext:
    """排程共享状态。各模块通过 ctx.<field> 只读访问。"""

    # ---- 入参（构造时给定，后续只读）----
    product_attrs: dict                      # {rid -> 产品属性 dict}
    line_attrs: dict                        # {line_id -> 产线属性 dict}
    switch_matrix: dict                     # 原始切换矩阵 {rid1 -> {rid2 -> minutes}}
    version_id: str
    trace_id: str
    org_id: str
    scheduling_strategy: list               # 规则策略入参
    mysql_config: dict                      # DB 连接配置
    logger: Any                             # 日志器

    # ---- 权重（_init_constraint_weights 填充）----
    constraint_weights: dict = field(default_factory=dict)

    # ---- 预计算缓存（preprocessing 阶段填充）----
    prod_time_matrix: dict = field(default_factory=dict)        # {(rid, line) -> (time, priority)}
    switch_time_matrix: dict = field(default_factory=dict)      # {(r1, r2) -> minutes}
    switch_clusters: dict = field(default_factory=dict)         # {rid -> cluster_id}
    zero_groups: dict = field(default_factory=dict)             # {gid: [rid, ...]} 零切换物料组(双向互切=0)
    product_product_line_capacity_matrix: dict = field(default_factory=dict)  # DB 产能查询结果

    # ---- PR0012/13/14 预处理结果（preprocessing 阶段填充）----
    shared_mixer_line_pairs: list = field(default_factory=list)
    wait_nodes: dict = field(default_factory=dict)

    # ---- 电价预处理结果（model_build 预处理阶段填充）----
    line_electricity_prices: dict = field(default_factory=dict)        # {line_id -> [(start, end, price)]}
    line_avg_electricity_price: dict = field(default_factory=dict)     # {line_id -> 加权均价}

    # ---- 求解阶段写入，下游只读 ----
    # changeover_time_penalty 由 model_build 计算，由 result_builder/scoring 读取。
    # 改为显式字段（不再用 self._changeover_time_penalty 隐式副作用）。
    changeover_time_penalty: int = 0
