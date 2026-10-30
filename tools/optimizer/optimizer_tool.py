"""
optimizer_tool.py — 优化库工具实现

契约:
  输入: 目标函数(可调用) + 设计变量域(DesignVariable列表)
  输出: OptimizerResult(Pareto/最优点 + 收敛史)
  支持: DOE(拉丁超立方) + 贝叶斯代理(RBF)两层优化

工具注册范式: 遵循平台 tools/ 目录结构，可被 import 并直接调用。
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field, asdict
from typing import Callable, List, Optional, Tuple, Any, Dict

import numpy as np
from scipy.optimize import minimize, differential_evolution
from scipy.stats import qmc
from scipy.interpolate import RBFInterpolator


# ─────────────────────────────────────────────
# 数据结构
# ─────────────────────────────────────────────

@dataclass
class DesignVariable:
    """设计变量定义"""
    name: str
    lower: float
    upper: float
    description: str = ""

    def to_dict(self) -> dict:
        return {"name": self.name, "lower": self.lower,
                "upper": self.upper, "description": self.description}


@dataclass
class OptimizerResult:
    """优化结果"""
    success: bool
    best_x: Dict[str, float]          # 最优点 {变量名: 值}
    best_fun: float                    # 最优目标值
    convergence_history: List[Dict]    # 收敛史 [{iter, best_fun, x}]
    n_eval: int                        # 总评估次数
    method: str                        # 使用的方法
    elapsed_s: float                   # 耗时(秒)
    multi_objective: bool = False
    pareto_front: Optional[List[Dict]] = None  # 多目标时的 Pareto 前沿

    def to_dict(self) -> dict:
        d = asdict(self)
        return d

    def to_json(self, path: str):
        with open(path, "w") as f:
            json.dump(self.to_dict(), f, indent=2, default=str)


# ─────────────────────────────────────────────
# DOE: 拉丁超立方采样
# ─────────────────────────────────────────────

class LatinHypercubeDOE:
    """拉丁超立方采样器"""

    def __init__(self, variables: List[DesignVariable]):
        self.variables = variables
        self.ndim = len(variables)

    def sample(self, n_samples: int, seed: int = 42) -> np.ndarray:
        """生成 n_samples 个样本，返回 (n_samples, ndim) 数组"""
        sampler = qmc.LatinHypercube(d=self.ndim, seed=seed)
        unit_samples = sampler.random(n=n_samples)  # [0,1]^d
        # 映射到实际域
        lower = np.array([v.lower for v in self.variables])
        upper = np.array([v.upper for v in self.variables])
        return qmc.scale(unit_samples, lower, upper)

    def sample_dict(self, n_samples: int, seed: int = 42) -> List[Dict[str, float]]:
        """返回字典列表"""
        arr = self.sample(n_samples, seed)
        return [
            {v.name: float(arr[i, j]) for j, v in enumerate(self.variables)}
            for i in range(n_samples)
        ]


# ─────────────────────────────────────────────
# 贝叶斯代理模型 (RBF 插值)
# ─────────────────────────────────────────────

class BayesianSurrogate:
    """
    基于 RBF 插值的代理模型（简化版贝叶斯代理）。
    用于减少昂贵函数评估次数：先在 DOE 上建代理，再在代理上优化。
    """

    def __init__(self, variables: List[DesignVariable],
                 kernel: str = "thin_plate_spline"):
        self.variables = variables
        self.kernel = kernel
        self.X_train: Optional[np.ndarray] = None
        self.y_train: Optional[np.ndarray] = None
        self.model: Optional[RBFInterpolator] = None

    def fit(self, X: np.ndarray, y: np.ndarray):
        """用样本数据拟合代理模型"""
        self.X_train = X.copy()
        self.y_train = y.copy()
        # 归一化到 [0,1]
        lower = np.array([v.lower for v in self.variables])
        upper = np.array([v.upper for v in self.variables])
        X_norm = (X - lower) / (upper - lower + 1e-12)
        self.model = RBFInterpolator(X_norm, y, kernel=self.kernel)
        self._lower = lower
        self._upper = upper

    def predict(self, X: np.ndarray) -> np.ndarray:
        """预测（带外推惩罚）"""
        if self.model is None:
            raise RuntimeError("Surrogate not fitted. Call fit() first.")
        X_norm = (X - self._lower) / (self._upper - self._lower + 1e-12)
        pred = self.model(X_norm)
        # 惩罚超出训练域范围的预测
        y_min, y_max = self.y_train.min(), self.y_train.max()
        y_range = y_max - y_min + 1e-12
        for i in range(len(pred)):
            if pred[i] < y_min - 0.5 * y_range:
                pred[i] = y_max + 0.5 * y_range  # 不可信低值 → 惩罚
            elif pred[i] > y_max + 0.5 * y_range:
                pred[i] = y_max + 0.5 * y_range  # 不可信高值 → 惩罚
        return pred

    def predict_single(self, x_dict: Dict[str, float]) -> float:
        """单点预测"""
        x = np.array([[x_dict[v.name] for v in self.variables]])
        return float(self.predict(x)[0])


# ─────────────────────────────────────────────
# 主优化器
# ─────────────────────────────────────────────

class OptimizerTool:
    """
    统一优化器工具。

    支持方法:
    - 'SLSQP': 序列最小二乘 (梯度法，适合光滑凸问题)
    - 'DE': 差分进化 (全局优化，不需要梯度)
    - 'surrogate': 代理辅助优化 (DOE → RBF代理 → SLSQP)

    多目标: 通过加权和方法将多目标转为单目标。
    """

    def __init__(self, variables: List[DesignVariable]):
        self.variables = variables
        self.bounds = [(v.lower, v.upper) for v in self.variables]
        self.n_eval = 0
        self.history: List[Dict] = []

    def _wrap_objective(self, func: Callable) -> Callable:
        """包装目标函数，记录收敛史"""
        def wrapped(x):
            self.n_eval += 1
            val = func(x)
            x_dict = {v.name: float(x[i]) for i, v in enumerate(self.variables)}
            self.history.append({
                "iter": self.n_eval,
                "fun": float(val) if np.isscalar(val) else float(val[0]),
                "x": x_dict
            })
            return val
        return wrapped

    def optimize(
        self,
        objective: Callable,
        method: str = "DE",
        max_iter: int = 100,
        pop_size: int = 15,
        x0: Optional[np.ndarray] = None,
        surrogate_doe_size: int = 30,
    ) -> OptimizerResult:
        """
        执行优化。

        Parameters
        ----------
        objective : callable
            目标函数 f(x) -> float，x 为 numpy array
        method : str
            'SLSQP' | 'DE' | 'surrogate'
        max_iter : int
            最大迭代次数
        pop_size : int
            DE 种群大小
        x0 : ndarray, optional
            初始点 (SLSQP 用)
        surrogate_doe_size : int
            代理模型 DOE 采样数

        Returns
        -------
        OptimizerResult
        """
        self.n_eval = 0
        self.history = []
        t0 = time.time()

        wrapped = self._wrap_objective(objective)

        if method == "SLSQP":
            if x0 is None:
                x0 = np.array([(v.lower + v.upper) / 2 for v in self.variables])
            result = minimize(
                wrapped, x0, method="SLSQP",
                bounds=self.bounds,
                options={"maxiter": max_iter, "ftol": 1e-8}
            )
            best_x = result.x
            best_fun = float(result.fun)
            success = result.success

        elif method == "DE":
            result = differential_evolution(
                wrapped, self.bounds,
                maxiter=max_iter, popsize=pop_size,
                seed=42, tol=1e-8, atol=1e-8
            )
            best_x = result.x
            best_fun = float(result.fun)
            success = result.success

        elif method == "surrogate":
            # 两层优化: DOE → 代理 → SLSQP
            doe = LatinHypercubeDOE(self.variables)
            X_doe = doe.sample(surrogate_doe_size)
            y_doe = np.array([objective(x) for x in X_doe])
            self.n_eval += surrogate_doe_size
            for i, x in enumerate(X_doe):
                x_dict = {v.name: float(x[j]) for j, v in enumerate(self.variables)}
                self.history.append({
                    "iter": i + 1, "fun": float(y_doe[i]), "x": x_dict,
                    "phase": "DOE"
                })

            surrogate = BayesianSurrogate(self.variables)
            surrogate.fit(X_doe, y_doe)

            # 在代理上优化
            def surrogate_obj(x):
                self.n_eval += 1
                val = surrogate.predict(x.reshape(1, -1))[0]
                x_dict = {v.name: float(x[j]) for j, v in enumerate(self.variables)}
                self.history.append({
                    "iter": self.n_eval, "fun": float(val), "x": x_dict,
                    "phase": "surrogate_opt"
                })
                return val

            # Use DE on surrogate for global robustness
            res_sur = differential_evolution(
                surrogate_obj, self.bounds,
                maxiter=max_iter, popsize=8,
                seed=42, tol=1e-8
            )
            best_x = res_sur.x
            best_fun = float(res_sur.fun)
            success = res_sur.success

        else:
            raise ValueError(f"Unknown method: {method}. Use 'SLSQP', 'DE', or 'surrogate'.")

        elapsed = time.time() - t0
        best_x_dict = {v.name: float(best_x[i]) for i, v in enumerate(self.variables)}

        return OptimizerResult(
            success=success,
            best_x=best_x_dict,
            best_fun=best_fun,
            convergence_history=self.history,
            n_eval=self.n_eval,
            method=method,
            elapsed_s=elapsed,
        )

    def optimize_multi_objective(
        self,
        objectives: List[Callable],
        weights: Optional[List[float]] = None,
        method: str = "DE",
        max_iter: int = 100,
        n_weights: int = 10,
    ) -> OptimizerResult:
        """
        多目标优化（加权和方法）。

        Parameters
        ----------
        objectives : list of callable
            多个目标函数 [f1(x), f2(x), ...]
        weights : list of float, optional
            权重向量（归一化）。若为 None 则自动生成均匀分布。
        method : str
            优化方法
        max_iter : int
            每个权重组合的最大迭代
        n_weights : int
            权重采样数（2目标时自动生成）

        Returns
        -------
        OptimizerResult (含 pareto_front)
        """
        if len(objectives) < 2:
            raise ValueError("Need at least 2 objectives for multi-objective")

        t0 = time.time()
        self.n_eval = 0
        self.history = []

        # 生成权重组合
        if weights is not None:
            weight_list = [weights]
        elif len(objectives) == 2:
            w1 = np.linspace(0, 1, n_weights)
            weight_list = [[w, 1 - w] for w in w1]
        else:
            # 简单均匀采样
            weight_list = [[1.0 / len(objectives)] * len(objectives)]

        pareto_points = []
        best_overall_fun = float("inf")
        best_overall_x = None

        for w in weight_list:
            def weighted_obj(x, _w=w):
                vals = [obj(x) for obj in objectives]
                return sum(wi * vi for wi, vi in zip(_w, vals))

            # 重置 history for each weight but keep n_eval
            sub_tool = OptimizerTool(self.variables)
            sub_tool.n_eval = self.n_eval
            result = sub_tool.optimize(weighted_obj, method=method, max_iter=max_iter)

            self.n_eval = sub_tool.n_eval
            self.history.extend(sub_tool.history)

            # 记录 Pareto 点
            obj_vals = {f"f{i+1}": float(obj(result.best_x_arr()))
                       for i, obj in enumerate(objectives)}
            pareto_points.append({
                "weights": w,
                "x": result.best_x,
                "objectives": obj_vals,
                "weighted_sum": result.best_fun
            })

            if result.best_fun < best_overall_fun:
                best_overall_fun = result.best_fun
                best_overall_x = result.best_x

        elapsed = time.time() - t0

        return OptimizerResult(
            success=True,
            best_x=best_overall_x,
            best_fun=best_overall_fun,
            convergence_history=self.history,
            n_eval=self.n_eval,
            method=f"multi_objective_{method}",
            elapsed_s=elapsed,
            multi_objective=True,
            pareto_front=pareto_points,
        )


# ─────────────────────────────────────────────
# 便捷工厂
# ─────────────────────────────────────────────

def create_optimizer(var_specs: List[Dict]) -> OptimizerTool:
    """从字典列表创建优化器"""
    variables = [DesignVariable(**spec) for spec in var_specs]
    return OptimizerTool(variables)
