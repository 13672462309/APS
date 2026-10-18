"""aps.py: Aps facade — 保持对外接口不变，内部委托到 scheduler 包各模块。

历史: 本文件曾是 1634 行的巨型实现，混杂了预处理/产能/建模/求解/抽取/评分/日志。
现拆为 scheduler/ 下的若干模块（context/preprocessing/capacity/
callbacks/model_build/result_builder/scoring/estimate）。本文件仅做组装与转发，
Aps 的所有方法签名与行为与重构前等价。
"""
import constant
from constant import mysql_online_config, mysql_offline_config
from logging_config import get_logger
from scheduler.context import ApsContext
from scheduler import preprocessing, model_build, result_builder, estimate


class Aps:
    """排产服务门面。方法委托到 scheduler 包，保留原公共接口。"""

    def __init__(self, production_plan_list, production_line_list, switch_matrix,
                 version_id, trace_id, org_id, scheduling_strategy):
        # 基础属性初始化
        line_attrs = {line['productionLineId']: line for line in production_line_list}
        mysql_config = mysql_online_config if constant.IS_PROD else mysql_offline_config

        # 产品属性 + 时间窗口
        product_attrs = {}
        for plan in production_plan_list:
            rid = plan['recordId']
            product_attrs[rid] = plan
            earliest = float(plan['earliestStartTime'])
            latest = float(plan['latestCompletionTime'])
            product_attrs[rid]['time_window'] = {'earliest': earliest, 'latest': latest}

        self.ctx = ApsContext(
            product_attrs=product_attrs,
            line_attrs=line_attrs,
            switch_matrix=switch_matrix,
            version_id=version_id,
            trace_id=trace_id,
            org_id=org_id,
            scheduling_strategy=scheduling_strategy,
            mysql_config=mysql_config,
            logger=get_logger(),
        )

        # PR0012/13/14 新字段预处理（stage/assignedMixers 默认值）
        preprocessing.preprocess_new_fields(self.ctx)
        # 约束权重初始化（按 scheduling_strategy 的 ruleWeight 覆盖默认）
        self.ctx.constraint_weights = self._init_constraint_weights()

        # 兼容旧调用方：暴露部分属性到 self
        # 注意: 这些 self.<attr> 只是 ctx.<attr> 的初始引用别名。
        # 若外部代码做 `aps.constraint_weights = X`(如 /submit-task-v2 注入权重),
        # 只会替换 self 的引用, ctx 不会同步 — 生产调度走 ctx, 会静默用旧权重。
        # 因此权重必须用 `aps.constraint_weights = X` 之外的方式同步, 见
        # `set_constraint_weights()` 方法。
        self.line_attrs = self.ctx.line_attrs
        self.product_attrs = self.ctx.product_attrs
        self.switch_matrix = self.ctx.switch_matrix
        self.version_id = self.ctx.version_id
        self.trace_id = self.ctx.trace_id
        self.org_id = self.ctx.org_id
        self.scheduling_strategy = self.ctx.scheduling_strategy
        self.logger = self.ctx.logger
        self.constraint_weights = self.ctx.constraint_weights
        self.product_product_line_capacity_matrix = self.ctx.product_product_line_capacity_matrix
        self.prod_time_matrix = self.ctx.prod_time_matrix
        self.switch_time_matrix = self.ctx.switch_time_matrix
        self.shared_mixer_line_pairs = self.ctx.shared_mixer_line_pairs
        self.wait_nodes = self.ctx.wait_nodes

    def set_constraint_weights(self, weights: dict):
        """设置约束权重, 同步 self 与 ctx。

        /submit-task-v2 注入 v2 权重时应调用此方法,
        而非 `aps.constraint_weights = v2_weights`(后者只改 self 引用, ctx 不同步)。
        """
        self.ctx.constraint_weights = weights
        self.constraint_weights = weights

    # ---- 权重 ----
    def _init_constraint_weights(self):
        """根据策略规则初始化约束权重（CP-SAT 要求所有系数为整数）。"""
        weights = dict(constant.constraint_weights)
        if self.ctx.scheduling_strategy:
            for strategy in self.ctx.scheduling_strategy:
                if 'ruleCode' in strategy and 'ruleWeight' in strategy:
                    rule_code = strategy['ruleCode']
                    try:
                        constraint_type = constant.rule_constraint_mapping.get(rule_code)
                        if constraint_type and constraint_type in weights:
                            weights[constraint_type] = int(float(strategy['ruleWeight']))
                    except (ValueError, TypeError):
                        self.ctx.logger.warning(f"无效的规则权重设置: {rule_code}: {strategy['ruleWeight']}")

        for k in weights:
            weights[k] = int(weights[k])

        self.ctx.logger.info(f"约束权重: {weights}")
        return weights

    # ---- 兼容旧辅助方法 ----
    # 保留旧 Aps 调用方/测试的薄委托，真正实现仍在 scheduler 模块。
    def _get_effective_special_type(self, product):
        return preprocessing.get_effective_special_type(product)

    def _preprocess_new_fields(self):
        return preprocessing.preprocess_new_fields(self.ctx)

    def _analyze_mixer_sharing(self, active_lines):
        preprocessing.analyze_mixer_sharing(self.ctx, active_lines)
        self.shared_mixer_line_pairs = self.ctx.shared_mixer_line_pairs

    def _create_wait_nodes(self, active_lines, line_feasible_rids):
        preprocessing.create_wait_nodes(self.ctx, active_lines, line_feasible_rids)
        self.wait_nodes = self.ctx.wait_nodes

    def _precompute_production_times(self, all_rids, lines):
        return preprocessing.precompute_production_times(self.ctx, all_rids, lines)

    def _precompute_switch_times(self, scheduled_rids, lines):
        return preprocessing.precompute_switch_times(self.ctx, scheduled_rids, lines)

    def _precompute_switch_clusters(self, scheduled_rids):
        return preprocessing.precompute_switch_clusters(self.ctx, scheduled_rids)

    def get_product_capacity_list(self):
        return __import__('scheduler.capacity', fromlist=['get_product_capacity_list']).get_product_capacity_list(self.ctx)

    def cal_production_time(self, product_id, line, order_quantity, show_log=False):
        return __import__('scheduler.capacity', fromlist=['cal_production_time']).cal_production_time(
            self.ctx, product_id, line, order_quantity, show_log)

    def get_switch_time(self, rid1, rid2):
        return __import__('scheduler.capacity', fromlist=['get_switch_time']).get_switch_time(self.ctx, rid1, rid2)

    def get_product_priority(self, rid, line):
        return __import__('scheduler.capacity', fromlist=['get_product_priority']).get_product_priority(self.ctx, rid, line)

    def get_feasible_lines_for_product(self, rid):
        return result_builder.get_feasible_lines_for_product(self.ctx, rid)

    def analyze_infeasibility_reason(self, rid):
        return result_builder.analyze_infeasibility_reason(self.ctx, rid)

    def print_solution_log(self, scheduled_result, all_rids=None, M_values=None):
        return result_builder.print_solution_log(self.ctx, scheduled_result, all_rids, M_values)


    def calculate_solution_scores(self, scheduled_result, detailed_log_enabled=False):
        from scheduler.scoring import calculate_solution_scores
        return calculate_solution_scores(self.ctx, scheduled_result, detailed_log_enabled)

    # ---- 主流程入口 ----
    def production_scheduling(self):
        """生产调度主函数：委托到 model_build.schedule。"""
        # 每次排产都是独立求解事务；失败时不能让下游复用上次成功的 penalty。
        self.ctx.changeover_time_penalty = 0
        return model_build.schedule(self.ctx)

    def estimate_production_load(self):
        """排产负载预估：委托到 estimate.estimate_production_load。"""
        return estimate.estimate_production_load(self.ctx)

    def build_schedule_results(self, scheduled_result):
        """转换为 HPAAS scheduleResults 数组：委托到 result_builder。"""
        return result_builder.build_schedule_results(self.ctx, scheduled_result)
