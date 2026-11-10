# 数字孪生贝叶斯模型更新机制（p148w2-n02）

> 传感器数据 → 似然函数 → 贝叶斯修正 → 模型参数后验分布 → 预测不确定性量化
> 代码位置：`backend/services/digital_twin/bayesian_updater.py`
> 验收工件：`artifacts/digital_twin_bayesian/results.json`、`artifacts/digital_twin_bayesian/figures/`

---

## 1. 概述

本模块实现数字孪生的**完整贝叶斯模型更新闭环**，填补平台在参数后验分布与不确定性量化方面的空白。

与已有的 `ExtendedKalmanFilter`（`state_estimator.py`）互补：
- **EKF**：在线点估计，适合实时状态跟踪（3 状态：GM、阻力偏置、损伤标度）
- **贝叶斯更新器**：离线/批量全后验分布，适合参数标定、不确定性传播、决策支持

## 2. 架构

```
ModelWrapper (θ → y)          SensorData (t, z, σ)
        │                            │
        └────────┬───────────────────┘
                 ▼
         BayesianUpdater
           │         │
     ┌─────┘         └─────┐
     ▼                     ▼
  MCMC (MH)             UKF
  全后验样本         高斯近似后验
     │                     │
     └────────┬────────────┘
              ▼
    Predictive Distribution
    (均值 + 95% 置信区间)
```

### 核心组件

| 组件 | 职责 |
|---|---|
| `ModelWrapper` | 抽象参数化模型接口（θ → y），零领域语义 |
| `SensorData` | 抽象传感器观测接口（时间戳 + 观测值 + 噪声标准差） |
| `GaussianPrior` | 独立高斯先验 |
| `metropolis_hastings()` | MCMC 后验采样（Metropolis-Hastings） |
| `UnscentedKalmanFilter` | UKF 高斯近似后验 |
| `BayesianUpdater` | 编排器：先验 → 似然 → 后验 → 预测不确定性 |
| `SurrogateModelWrapper` | 将代理模型注册表包装为 ModelWrapper |
| `LinearModel` | 线性模型（测试/验证用） |

## 3. 接口定义

### ModelWrapper（零领域语义）

```python
class ModelWrapper(ABC):
    def parameter_names(self) -> List[str]: ...   # 不确定参数 θ 的名称
    def output_names(self) -> List[str]: ...      # 模型输出 y 的名称
    def evaluate(self, theta: np.ndarray) -> np.ndarray: ...  # θ → y
```

### SensorData

```python
class SensorData:
    timestamp: float          # 观测时间
    values: np.ndarray        # 观测值 z
    noise_std: np.ndarray     # 噪声标准差 σ
    output_indices: np.ndarray  # 对应模型输出的哪些通道（None=全部）
```

### BayesianUpdater

```python
updater = BayesianUpdater(model, prior)
updater.add_observation(obs)
result = updater.full_update(n_mcmc_samples=5000, burn_in=1000)

# result.mcmc: MCMCResult (samples, acceptance_rate, credible_interval)
# result.ukf_result: UKFResult (state_mean, state_cov)
# result.predictive_mean/std/ci_lower/ci_upper: 预测分布统计量
```

## 4. 验收判据

| 编号 | 判据 | 实测 | 结论 |
|---|---|---|---|
| (a) | MCMC 接受率在 [0.1, 0.9] | 0.188 | ✅ |
| (b) | 后验均值在真值 3σ 内 | z-score < 1.1 | ✅ |
| (c) | 后验标准差 < 先验标准差 | 0.035 < 0.5, 0.028 < 0.3 | ✅ |
| (d) | 95% 可信区间包含真值 | [1.13, 1.26] ∋ 1.20 | ✅ |
| (e) | 预测 95% CI 包含真输出 | 全部 3 通道 | ✅ |
| (f) | 负控：零观测 → 后验 = 先验 | normalized shift < 0.3 | ✅ |
| (g) | 负控：完美观测 → 后验在真值 | relative error < 10% | ✅ |
| (h) | UKF 收敛（状态距真值 < 1.0） | | ✅ |

## 5. 负控验证

### NC1: 零观测 → 后验 = 先验

无传感器数据时，贝叶斯修正不应改变先验信念。MCMC 采样结果的后验均值/标准差应与先验一致（在蒙特卡洛噪声范围内）。

### NC2: 完美观测 → 后验集中在真值

无噪声观测时，似然函数在真值处达到峰值，后验应集中在真值附近（后验标准差远小于先验标准差）。

## 6. 与 EKF 的关系

| 特性 | EKF (`state_estimator.py`) | 贝叶斯更新器 (本模块) |
|---|---|---|
| 后验形式 | 高斯近似（均值+协方差） | 全后验样本 / 高斯近似 |
| 运行模式 | 在线递推 | 离线批量 / 在线递推 |
| 适用场景 | 实时状态跟踪 | 参数标定、不确定性量化 |
| 非线性处理 | 一阶线性化（Jacobian） | 无近似（MCMC）/ 二阶（UKF） |
| 计算成本 | 低（ms 级） | 中-高（秒级，取决于 MCMC 样本数） |

## 7. 使用示例

```python
from services.digital_twin.bayesian_updater import (
    BayesianUpdater, GaussianPrior, LinearModel, SensorData
)
import numpy as np

# 定义参数化模型
A = np.array([[1.0, 0.5], [0.3, 1.0]])
model = LinearModel(A, param_names=["alpha", "beta"])

# 定义先验
prior = GaussianPrior(mean=np.array([1.0, 2.0]), std=np.array([0.5, 0.3]))

# 创建贝叶斯更新器
updater = BayesianUpdater(model, prior)

# 添加传感器观测
obs = SensorData(timestamp=0.0, values=np.array([2.1, 2.8]),
                 noise_std=np.array([0.1, 0.15]))
updater.add_observation(obs)

# 执行贝叶斯更新
result = updater.full_update(n_mcmc_samples=5000, burn_in=1000)

# 查看后验
print(f"Posterior mean: {result.mcmc.posterior_mean}")
print(f"95% CI: {result.mcmc.credible_interval(0.95)}")
print(f"Predictive mean: {result.predictive_mean}")
print(f"Predictive 95% CI: [{result.predictive_ci_lower}, {result.predictive_ci_upper}]")
```

## 8. 测试

```bash
cd backend
python3 -m pytest tests/test_bayesian_updater.py -v --noconftest
# 29 passed
```

## 9. 复现基准

```bash
python3 scripts/run_bayesian_benchmark.py   # ~3 s, 8/8 acceptance criteria pass
```

## 10. 局限与适用范围

1. **MCMC 收敛**：Metropolis-Hastings 采样器在参数维度高（>10）或后验强相关时可能收敛缓慢。建议检查接受率（目标 0.2-0.5）和自相关。
2. **UKF 高斯近似**：UKF 假设后验近似高斯，对强非高斯后验（多峰、偏态）精度有限。此时应使用 MCMC。
3. **先验敏感性**：数据量少时后验受先验影响大。应进行先验敏感性分析。
4. **计算成本**：MCMC 需要多次模型评估。对于计算密集型模型（CFD/FEM），应使用代理模型或变分推断。
5. **零领域语义**：所有物理语义通过 `ModelWrapper` 注入，本模块不含任何船舶/冰区特定逻辑。

## 11. 参考文献

- Gelman et al., "Bayesian Data Analysis", 3rd ed., CRC 2013.
- Julier & Uhlmann, "Unscented filtering and nonlinear estimation", PIEEE 2004.
- Brooks et al., "Handbook of Markov Chain Monte Carlo", CRC 2011.
