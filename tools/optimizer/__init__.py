"""
Optimizer Tool — 船舶总体性能平台第5类工具（优化库接入）

提供:
- OptimizerTool: 统一优化接口，支持 SLSQP / differential_evolution / 加权多目标
- LatinHypercubeDOE: 拉丁超立方采样
- BayesianSurrogate: 基于 RBF 插值的贝叶斯代理模型

后端: scipy.optimize (SLSQP, differential_evolution)
注: pymoo (NSGA-II) 在当前环境不可用 (No matching distribution)，
    多目标通过加权和方法实现，未来可替换为 pymoo 的 NSGA-II。
"""

from .optimizer_tool import (
    OptimizerTool,
    OptimizerResult,
    LatinHypercubeDOE,
    BayesianSurrogate,
    DesignVariable,
)

__all__ = [
    "OptimizerTool",
    "OptimizerResult",
    "LatinHypercubeDOE",
    "BayesianSurrogate",
    "DesignVariable",
]
