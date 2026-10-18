"""callbacks.py: CP-SAT 求解过程回调。"""
from ortools.sat.python import cp_model


class SolutionLogger(cp_model.CpSolverSolutionCallback):
    """求解过程回调：记录每个可行解的发现过程，类似 SCIP 的搜索日志。

    提前终止策略（按优先级）:
    1. 排产数 stall: 连续 stall_threshold 个解未增加排产产品数 → 停止
       (排产数是业务核心指标, 分数优化是锦上添花)
    2. Gap 精度达标: gap < 0.5% 且已有足够解 → 停止
    """

    def __init__(self, logger, num_products, stall_threshold=10):
        super().__init__()
        self._logger = logger
        self._num_products = num_products
        self._stall_threshold = stall_threshold
        self.solution_count = 0
        self._start_time = None
        self._last_obj = None
        self._stall_count = 0
        self._last_product_count = -1
        self._product_stall_count = 0
        self._best_obj = 0

    def on_solution_callback(self):
        if self._start_time is None:
            self._start_time = self.WallTime()

        self.solution_count += 1
        obj = self.ObjectiveValue()
        bound = self.BestObjectiveBound()
        gap = abs(bound - obj) / max(abs(obj), 1) * 100
        elapsed = self.WallTime()

        improved = ''
        if self._last_obj is not None:
            delta = obj - self._last_obj
            if delta > 0:
                improved = f' (+{delta:.0f})'
                self._stall_count = 0
            else:
                self._stall_count += 1
        self._last_obj = obj

        self._logger.info(
            f"  #{self.solution_count:<4d} {elapsed:>6.1f}s  "
            f"目标={obj:>12.0f}{improved}  上界={bound:>12.0f}  "
            f"Gap={gap:>5.2f}%"
        )

        # 提前终止1: Gap 精度达标
        if gap < 0.5 and self.solution_count >= 5:
            self._logger.info(f"  Gap {gap:.2f}% < 0.5% 且已有 {self.solution_count} 个解，提前终止搜索")
            self.StopSearch()
            return

        # 提前终止2: 排产数 stall 检测
        # 通过 base_bonus 估算排产数: obj ≈ product_count × base_bonus + 其他项
        # 用 obj // 1_000_000 粗估排产数变化（精度足够判断 stall）
        # 注意: 排产数可能在后续解中增加(如 #1=28产品, #5=29产品), 所以阈值要宽松
        est_product_count = int(obj // 1_000_000)
        if obj > self._best_obj:
            self._best_obj = obj
            self._product_stall_count = 0
        elif est_product_count == self._last_product_count:
            self._product_stall_count += 1
            if self._product_stall_count >= self._stall_threshold:
                self._logger.info(
                    f"  排产数连续 {self._stall_threshold} 个解未变化，提前终止搜索")
                self.StopSearch()
                return
        else:
            self._product_stall_count = 0
        self._last_product_count = est_product_count
