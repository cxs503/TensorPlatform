# 优化库工具接入文档

## 1. 概述

本文档描述船舶总体性能平台第5类工具——**优化库（Optimizer Tool）**的接入规范与实现。

**目标**: 对标指南「工具接入 ≥5 类」要求，补位优化库能力缺口。

**后端**: scipy.optimize (SLSQP, differential_evolution)  
**注**: pymoo (NSGA-II) 在当前环境不可用 (No matching distribution for Python 3.11.6)，多目标通过加权和方法实现。

---

## 2. 工具契约

### 2.1 输入

```python
from tools.optimizer import OptimizerTool, DesignVariable

# 定义设计变量
variables = [
    DesignVariable(name="L_over_B", lower=5.0, upper=7.5, description="长宽比"),
    DesignVariable(name="B_over_T", lower=2.5, upper=4.5, description="宽吃水比"),
]

# 创建优化器
optimizer = OptimizerTool(variables)

# 定义目标函数 (接受 numpy array, 返回标量)
def objective(x: np.ndarray) -> float:
    # x[0] = L_over_B, x[1] = B_over_T
    # ... 调用船舶平台 API 或本地模型 ...
    return resistance_kN
```

### 2.2 输出

```python
result = optimizer.optimize(objective, method="DE", max_iter=100)

# OptimizerResult 包含:
result.success              # bool: 是否收敛
result.best_x               # dict: 最优点 {变量名: 值}
result.best_fun             # float: 最优目标值
result.convergence_history  # list: 收敛史 [{iter, fun, x}, ...]
result.n_eval               # int: 总评估次数
result.method               # str: 使用方法
result.elapsed_s            # float: 耗时(秒)
```

### 2.3 支持方法

| 方法 | 说明 | 适用场景 |
|------|------|----------|
| `SLSQP` | 序列最小二乘 (梯度法) | 光滑凸问题，有初始猜测 |
| `DE` | 差分进化 (全局) | 非凸/多峰/无梯度 |
| `surrogate` | DOE→RBF代理→优化 | 昂贵函数 (CFD/实验) |

### 2.4 多目标优化

```python
# 加权和方法
result = optimizer.optimize_multi_objective(
    objectives=[resistance_func, cost_func],
    weights=[0.7, 0.3],  # 或自动生成均匀分布
    method="DE",
    n_weights=10  # Pareto 前沿采样数
)

result.pareto_front  # [{weights, x, objectives}, ...]
```

---

## 3. 辅助功能

### 3.1 拉丁超立方采样 (DOE)

```python
from tools.optimizer import LatinHypercubeDOE

doe = LatinHypercubeDOE(variables)
samples = doe.sample(n_samples=30, seed=42)  # numpy array
samples_dict = doe.sample_dict(30)  # 字典列表
```

### 3.2 贝叶斯代理模型 (RBF)

```python
from tools.optimizer import BayesianSurrogate

surrogate = BayesianSurrogate(variables, kernel="thin_plate_spline")
surrogate.fit(X_train, y_train)
predictions = surrogate.predict(X_test)
```

**注**: 当前实现为 RBF 插值（简化版贝叶斯代理），带外推惩罚。未来可替换为 Gaussian Process。

---

## 4. API 端点 (未来扩展)

平台可提供 REST API 封装:

```
POST /api/v1/optimizer/run
{
    "variables": [...],
    "objective": "holtrop_resistance",  // 或自定义函数
    "method": "DE",
    "max_iter": 100
}

Response:
{
    "success": true,
    "best_x": {...},
    "best_fun": 981.46,
    "convergence_history": [...],
    "n_eval": 1023
}
```

---

## 5. Toy 案例: KVLCC2 阻力优化

### 5.1 问题定义

**目标**: 最小化 KVLCC2 油轮总阻力 Rt (kN)  
**设计变量**:
- L/B (长宽比): [5.0, 7.5]
- B/T (宽吃水比): [2.5, 4.5]

**固定参数**:
- L = 190 m (船长)
- Cb = 0.71 (方形系数)
- V = 14 kn (航速)

**目标函数**: 调用船舶平台 :8002 `/api/v1/calculations/resistance` (Holtrop 方法)

### 5.2 运行

```bash
cd /srv/tensor/agent/work/m377
PYTHONPATH=/srv/tensor/agent/work/m377 python tools/optimizer/toy_optimize.py
```

### 5.3 结果

**基线** (KVLCC2 原始参数):
- L/B = 5.94, B/T = 3.20
- Rt = 1389.74 kN

**差分进化 (DE) 优化**:
- 最优 L/B = 7.50, B/T = 4.50 (域边界)
- 最优 Rt = 981.46 kN
- **改善 29.4%**
- 评估次数: 1023, 耗时: 2.0s

**代理辅助优化** (DOE=20 → RBF → DE):
- 最优 L/B = 6.07, B/T = 2.84
- 代理预测 Rt = 681.82 kN
- **真实 Rt = 1422.99 kN** (代理误差 52.1%)
- 评估次数: 899, 耗时: 0.2s

**结论**: DE 全局优化可靠 (29.4% 改善)；代理模型因训练样本不足 (20点) 误差较大，需增加 DOE 样本或使用高斯过程。

### 5.4 产物

- `artifacts/optimizer_toy.json`: 完整优化结果 (收敛史 + 最优点 + 验证)
- `tools/optimizer/optimizer_tool.py`: 工具实现
- `tools/optimizer/toy_optimize.py`: Toy 脚本

---

## 6. 工具注册

遵循平台 `tools/` 目录结构:

```
tools/
├── optimizer/
│   ├── __init__.py          # 包导出
│   ├── optimizer_tool.py    # 主实现
│   └── toy_optimize.py      # Toy 案例
├── agent/
├── executor/
└── ...
```

**导入方式**:
```python
from tools.optimizer import OptimizerTool, DesignVariable
```

---

## 7. 局限与改进

### 7.1 当前局限

1. **pymoo 不可用**: 环境无法安装 pymoo (Python 3.11.6 无匹配版本)，多目标仅支持加权和方法，无法生成真实 Pareto 前沿。
2. **代理模型精度**: RBF 插值在小样本 (20点) 下误差 >50%，需增加样本或使用高斯过程。
3. **无约束处理**: 当前仅支持边界约束，无等式/不等式约束 (SLSQP 支持但 toy 未用)。

### 7.2 改进计划

1. **pymoo 接入**: 待环境支持后，添加 NSGA-II/NSGA-III 多目标优化。
2. **高斯过程代理**: 替换 RBF 为 GP (scikit-learn `GaussianProcessRegressor`)，提供不确定性量化。
3. **约束优化**: 支持线性/非线性约束 (SLSQP 已支持，需封装)。
4. **并行评估**: DE 种群并行评估 (需平台任务队列支持)。

---

## 8. 验证

### 8.1 单元测试

```bash
cd /srv/tensor/agent/work/m377
python -c "from tools.optimizer import OptimizerTool, DesignVariable; print('Import OK')"
```

### 8.2 端到端测试

```bash
PYTHONPATH=/srv/tensor/agent/work/m377 python tools/optimizer/toy_optimize.py
# 检查 artifacts/optimizer_toy.json 是否生成
```

### 8.3 判据

- ✅ `optimizer_tool.py` 可 import
- ✅ Toy 脚本真实调用船舶平台 API
- ✅ `artifacts/optimizer_toy.json` 含收敛史数组 (1023 条) 与最优目标值 (981.46 kN)
- ✅ 脚本输出零手抄 (程序化注入)

---

## 9. 参考

- scipy.optimize 文档: https://docs.scipy.org/doc/scipy/reference/optimize.html
- Holtrop 方法: Holtrop & Mennen, 1982, "A Statistical Reanalysis of Resistance and Propulsion Data"
- KVLCC2 基准: SIMMAN 2010, "Benchmark Study on CFD Uncertainty"

---

**文档版本**: v1.0  
**创建日期**: 2026-01-XX  
**任务**: m377 指南缺口补位①
