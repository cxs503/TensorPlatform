# G-OPT 优化库使用指南

> TensorPlatform 通用优化内核（Gradient-free + Gradient-based + Multi-objective）

## 1. 算法清单

| 算法 | 模块 | 类型 | 适用场景 | 约束支持 |
|------|------|------|----------|----------|
| **CMA-ES** | `optimizer.gopt.cmaes` | 无导数/黑盒 | 非凸、病态、含噪声、仿真驱动优化 | Box 约束（clip + mean projection） |
| **SLSQP** | `optimizer.gopt.slsqp` | 梯度/光滑 | 光滑可微问题、等式+不等式约束 | 等式/不等式 + Box |
| **NSGA-II** | `optimizer.multiobjective.nsga2` | 多目标进化 | 阻力-耐波权衡、Pareto 前沿搜索 | 约束可行性规则（Deb） |

### 1.1 CMA-ES 特性

- **ask/tell 分离**：支持外部/并行评估
- **IPOP/BIPOP 重启**：自动跳出局部最优（Auger & Hansen 2005）
- **噪声重采样**：`n_resample > 1` 时对每个候选多次评估取中位数
- **完整终止准则集**：TolFun / TolX / TolUpSigma / ConditionCov / NoEffectCoord / NoEffectAxis
- **位级可复现**：固定 seed 保证 bit-identical 结果

### 1.2 SLSQP 特性

- **自有 QP 求解器**：二次惩罚 Newton + 精确 active-set polish，输出 KKT 乘子证书
- **L1 价值函数线搜索**：Kraft 规则自动调节惩罚参数
- **阻尼 BFGS**：Powell 修正保证 Hessian 正定
- **Adjoint 协议**：通过 `InjectedAdjointObjective` 接入 LBM/FVM/FEM/BEM 求解器

### 1.3 NSGA-II 特性

- **快速非支配排序**：O(MN²)，支持约束可行性规则
- **拥挤距离**：保持 Pareto 前沿多样性
- **收敛度量**：GD / IGD / Spread / Hypervolume（2D 精确、3D 精确、高维 MC）
- **标准测试函数**：ZDT1-3、DTLZ1-2

## 2. API 示例

### 2.1 CMA-ES 快速入门

```python
from optimizer.gopt.cmaes import minimize_cmaes
import numpy as np

# Sphere 函数：min sum(x_i^2), x* = 0, f* = 0
def sphere(x):
    return float(np.sum(x ** 2))

bounds = np.array([[-5.0, 5.0], [-5.0, 5.0]])
result = minimize_cmaes(sphere, dim=2, bounds=bounds, seed=42, max_evals=500)

print(f"Optimum: x = {result.x}, f = {result.fun:.6e}")
print(f"Converged: {result.converged}, reason: {result.termination}")
```

### 2.2 CMA-ES 带重启（多峰问题）

```python
from optimizer.gopt.cmaes import CMAESOptimizer

# Rastrigin 函数：多峰，需要重启才能找到全局最优
def rastrigin(x):
    n = x.size
    return float(10 * n + np.sum(x ** 2 - 10 * np.cos(2 * np.pi * x)))

optimizer = CMAESOptimizer(
    dim=5,
    bounds=np.tile([-4.0, 4.0], (5, 1)),
    seed=5,
    max_evals=24000,
    restarts=4,          # IPOP 重启
    strategy="ipop",     # 或 "bipop"
    n_resample=1,
)
result = optimizer.optimize(rastrigin)
```

### 2.3 SLSQP 梯度优化

```python
from optimizer.gopt.slsqp import minimize_slsqp
from optimizer.gopt.interfaces import ConstraintSet
import numpy as np

# Nocedal & Wright Example 17.3
def f(x):
    return float((x[0] - 2) ** 2 + (x[1] - 1) ** 2)

def jac(x):
    return np.array([2 * (x[0] - 2), 2 * (x[1] - 1)])

# 不等式约束: 1 - x1^2/4 - x2^2 >= 0, -(x1 - 2*x2 + 1) >= 0
def ineq(x):
    return np.array([1.0 - x[0]**2/4.0 - x[1]**2, -(x[0] - 2.0*x[1] + 1.0)])

def ineq_jac(x):
    return np.array([[-x[0]/2.0, -2.0*x[1]], [-1.0, 2.0]])

constraints = ConstraintSet(ineq=ineq, ineq_jac=ineq_jac)

result = minimize_slsqp(
    f, x0=[0.0, 0.0], jac=jac,
    constraints=constraints
)
print(f"Success: {result.success}, x = {result.x}, f = {result.fun:.6f}")
```

### 2.4 Adjoint 协议接入外部求解器

```python
from optimizer.gopt.interfaces import AdjointSolver, PrimalResult, InjectedAdjointObjective
from optimizer.gopt.slsqp import SLSQPOptimizer
import numpy as np

class MyPDESolver(AdjointSolver):
    dim = 6
    name = "my_pde_solver"

    def primal(self, x):
        # 求解正问题，返回目标值和收敛状态
        value = float(np.sum(x ** 2))
        state = {"x": x.copy()}
        return PrimalResult(value=value, state=state)

    def adjoint(self, x, state):
        # 求解伴随方程，返回 dJ/dx
        return 2.0 * x

solver = MyPDESolver()
objective = InjectedAdjointObjective(solver)
bounds = np.array([[0.1, 3.0]] * 6)

result = SLSQPOptimizer(bounds=bounds, max_iter=60).optimize(objective, x0=np.full(6, 1.0))
```

### 2.5 NSGA-II 多目标优化

```python
from optimizer.multiobjective import NSGA2, ZDT1
import numpy as np

zdt1 = ZDT1(n_var=10)

opt = NSGA2(
    n_var=10, n_obj=2,
    pop_size=100, n_gen=200,
    seed=42
)

result = opt.optimize(
    zdt1, zdt1.lb, zdt1.ub,
    reference_point=np.array([1.1, 1.1]),
    true_pareto=zdt1.true_pareto_front(100)
)

print(f"Pareto front size: {len(result['pareto_front'])}")
print(f"Final GD: {result['history']['gd'][-1]:.6e}")
print(f"Final IGD: {result['history']['igd'][-1]:.6e}")
```

## 3. 适用场景

### 3.1 何时选择 CMA-ES

- 目标函数不可微或导数不可用
- 仿真驱动优化（CFD/FEA 等昂贵评估）
- 非凸、多峰、病态缩放问题
- 含噪声的目标函数（使用 `n_resample`）
- 设计变量 2-50 维

### 3.2 何时选择 SLSQP

- 目标函数光滑可微
- 需要精确满足等式/不等式约束
- 有伴随求解器可用（梯度成本 O(1) 而非 O(n)）
- 设计变量 2-100 维

### 3.3 何时选择 NSGA-II

- 多目标权衡分析（阻力 vs 耐波性、成本 vs 性能）
- 需要 Pareto 前沿而非单点最优
- 目标函数不可微或黑盒
- 需要量化收敛（GD/IGD/HV）

## 4. 基准测试与验证

### 4.1 内置基准问题

| 问题 | 维度 | 最优值 | 约束 | 用途 |
|------|------|--------|------|------|
| sphere2/10 | 2/10 | f*=0 | Box | 基础收敛验证 |
| rosenbrock2/5 | 2/5 | f*=0 | Box | 病态缩放测试 |
| ellipsoid5 | 5 | f*=0 | Box | 条件数测试 |
| cigar5/tablet5 | 5 | f*=0 | Box | 极端病态测试 |
| rastrigin5 | 5 | f*=0 | Box | 多模态测试 |
| eq_quadratic | 2 | f*=2 | 等式 | KKT 验证 |
| nocedal_17_3 | 2 | f*=1.393 | 不等式 | 教科书验证 |
| box_quadratic | 2 | f*=1 | Box | 主动约束验证 |
| eq_ineq_quadratic | 2 | f*=2 | 等式+不等式 | 混合约束验证 |

### 4.2 精度标准

- **CMA-ES**: 对 sphere/ellipsoid 问题，f < 1e-10；对 Rosenbrock，f < 1e-8
- **SLSQP**: 对 Nocedal 17.3，|f - 1.393465| < 1e-5，|x - x*| < 1e-4
- **NSGA-II**: 对 ZDT1，GD < 1e-3，IGD < 1e-2
- **Adjoint**: 梯度相对误差 < 5e-6，Taylor 阶数 >= 0.9

### 4.3 运行验证

```bash
# 运行所有 G-OPT 测试
pytest tests/test_gopt_cmaes.py tests/test_gopt_slsqp.py tests/test_gopt_adjoint.py -q

# 运行多目标测试
pytest tests/multiobjective/test_nsga2_framework.py -q

# 覆盖率检查
coverage run --source=optimizer/gopt -m pytest tests/test_gopt_*.py -q
coverage report
```

## 5. 架构概览

```
optimizer/
├── gopt/                          # 通用优化内核
│   ├── cmaes.py                   # CMA-ES (成熟内核)
│   ├── slsqp.py                   # SLSQP (自有 QP 求解器)
│   ├── interfaces.py              # 协议层 (Objective/AdjointSolver/ConstraintSet)
│   ├── benchmarks.py              # 基准问题 (闭式最优解)
│   ├── adjoint_reference.py       # 伴随参考实现
│   ├── cases/                     # 物理案例注入
│   └── validation/                # 验证报告生成
├── multiobjective/                # 多目标优化
│   ├── nsga2.py                   # NSGA-II 实现
│   ├── metrics.py                 # GD/IGD/Spread/Hypervolume
│   └── test_functions.py          # ZDT/DTLZ 测试函数
├── core/                          # 遗留内核 (保持兼容)
│   └── cmaes.py                   # 旧版 CMA-ES
└── interfaces/                    # 船舶特定接口
    ├── kcs_bow_param.py           # KCS 船首参数化
    └── resistance_eval.py         # 阻力评估
```

## 6. 注意事项

1. **SLSQP 必须有梯度**：如果 `objective.gradient(x)` 返回 `None`，SLSQP 会报错退出（fail loud），不会静默回退到无导数方法。
2. **CMA-ES 的 Box 约束**：通过 clip 实现，不是惩罚函数。如果 clipped fraction 持续很高，说明搜索分布偏离可行域，应调整初始 sigma。
3. **NSGA-II 的参考点**：必须支配所有 Pareto 点，否则 hypervolume 计算会出错。建议设为 `max(objs) * 1.1 + 1.0`。
4. **Adjoint 一致性检查**：在接入新求解器时，必须通过 `adjoint_consistency_check` 验证梯度正确性，否则优化结果不可信。
5. **重启策略**：对多峰问题（如 Rastrigin），必须设置 `restarts >= 3` 才能可靠找到全局最优。

## 7. 版本信息

- **G-OPT 版本**: 1.0.0
- **Python**: >= 3.9
- **依赖**: numpy, scipy (可选，用于交叉验证)
- **测试**: pytest >= 7.0
