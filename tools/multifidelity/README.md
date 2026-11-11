# `tools/multifidelity` —— 多保真度建模框架（共性机制，零领域语义）

给定同一物理量的若干模型（档位越高越贵越准），**用固定预算把采样放在哪一档**？
本包把这问题拆成四个可测原语，域数据（档位是什么、成本多少、设计空间在哪）全部由调用方注入。

| 模块 | 职责 |
|---|---|
| `levels.py` | `Level(name, rank, cost, evaluate)` + `Ladder`（秩连续/成本单调校验）+ `EvaluationLedger` 成本账本 |
| `gpr.py` | 各向异性平方指数 GP：输入/输出标准化、仿射趋势（universal kriging 边际）、小 nugget、超参在子样本上拟合；`variance_reduction_many` = 批量方差缩减采集量 |
| `cokriging.py` | 自回归（Kennedy–O'Hagan）链 `z_k = ρ_k z_{k−1} + δ_k`；`ρ` 由共点实测样品估计并向 1 收缩；任意档数递归 |
| `pareto.py` | 成本-精度非支配集；"≥N 档、成本单调升、误差单调降、全部非支配"机器判据 `check_monotone_front` |
| `allocator.py` | 静态规划（误差律 `a·n^-b` + 预算离散搜索）与自适应分配（单位成本期望方差缩减、批量、确定性） |
| `selection.py` | 用顶层样本做 k 折交叉验证选链路子集（防止强偏置低档把融合拖坏） |

## 用法

```python
from tools.multifidelity import Level, Ladder, MultiFidelitySurrogate, adaptive_allocation

ladder = Ladder([
    Level("coarse", 0, cost=1.0,   evaluate=coarse_fn),
    Level("medium", 1, cost=12.0,  evaluate=medium_fn),
    Level("fine",   2, cost=400.0, evaluate=fine_fn),
])
surrogate = MultiFidelitySurrogate().fit({0: (X0, y0), 1: (X1, y1), 2: (X2, y2)})
mean, std = surrogate.predict(X_test)

out = adaptive_allocation(ladder, budget=100 * 400.0, pool=X_pool,
                          box_lo=lo, box_hi=hi, batch=4)
```

## 数值陷阱与回归保护（本包在开发中真实踩过的坑）

| 坑 | 症状 | 保护用例 |
|---|---|---|
| 二次标准化 | 超参在子样本上拟合时把已标准化的 `Xz` 再标准化一次 → 长度尺度系统性偏短，6 维插值误差 0.2% → 23% | `test_gpr_interpolates_and_extrapolates_smoothly`、`test_gpr_predictive_variance_shrinks_with_more_data`（合成函数） |
| 常数残差的自信心 | 残差近似常数时 `y_std` 兜底 1.0 → 方差缩放在绝对单位下被放大，采集量把预算全投到无信息档 | `test_cokriging_survives_a_constant_residual` |
| 只做下三角解 | `variance_reduction` 漏了 `L^{-T}`，与批量版本差两个数量级 | `test_gpr_variance_reduction_is_positive_and_batched_consistent` |
| 小样本 `ρ` 噪声 | 共点样品少时 `ρ` 抖动，链把低档偏置按 `ρ` 放大 | `test_cokriging_rho_is_positive_and_scaled`、`test_cross_validated_choice_returns_a_valid_chain` |
| BLAS 小矩阵 | `np.linalg.solve` 在小矩阵上 39 ms（多线程开销），采集循环不可用；改 `scipy.linalg.solve_triangular` 后 0.05 ms | 端到端运行时长（见 `docs/m12638/evidence` 的 `wall_seconds`） |

## 领域算例（不在本包内）

`docs/m12638/`：薄船保真度阶梯（经验/势流筛选用/势流验证）、Forrester 解析基准、
Wigley 阻力扫掠、6 维船型战役与 honest-FAIL 归因。