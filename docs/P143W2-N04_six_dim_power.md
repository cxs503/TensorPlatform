# P143-W2-N04 六维统计功效门（Six-Dimension Statistical Power Gate）

> 节点：P143W2-N04
> 实现：`tools/abm/six_dim_power.py`（673 行，纯函数，零领域语义）
> 验收：`tests/abm/test_six_dim_power.py`（31 条，全部通过）
> 配套：`tools/abm/acceptance.py`（验收聚合，141 行）
> 首次实裁：mission 8596 / PR#747 重开

## 1. 问题的形状

在 ABM（Agent-Based Modeling）攻关编排协议中，六维评价表（正确性/精度/稳定性/效率/可复现性/适用范围）
长期存在一个**结构性缺陷**：「优于对照」的结论靠叙事而非统计判决。

| 旧做法 | 风险 |
|---|---|
| 肉眼比较两组均值 | 无第一类错误控制，样本量小时假阳性率可远超 5% |
| 单维 t 检验 | 六维同时检验 → 族系错误率（FWER）膨胀至 ~26%（1-0.95^6） |
| 无功效分析 | 不知道当前样本量能否检测到有意义的效应量 |
| 无 A/A 验证 | 无法确认检验程序本身的误报率是否受控 |

本节点把「优于对照」从叙事升级为**带第一类错误控制的统计判决**，同时保持零领域语义——
维度名/效应量定义/阈值全部由调用方注入，本模块只做纯数学运算。

## 2. 功能清单

### 2.1 效应量计算

| 函数 | 定义 | 适用场景 |
|---|---|---|
| `cohens_d(control, treatment)` | 配对设计 Cohen's d = mean(diff) / std(diff) | 参数化效应量，可解释性强 |
| `paired_median_diff(control, treatment)` | 配对差中位数 | 非参数、对异常值鲁棒 |

### 2.2 置换检验 + 多重比较校正

`permutation_test_six_dim(control_data, treatment_data, ...)` 对六维逐一执行配对置换检验（默认 10^4 次），
并提供三种多重比较校正方法：

| 校正方法 | 特点 |
|---|---|
| `bonferroni_holm`（默认） | 逐步校正，比 Bonferroni 更强，FWER ≤ α |
| `bonferroni` | 保守但简单，adjusted_p = min(raw_p × m, 1) |
| `none` | 不校正，仅报告原始 p 值 |

置换检验的优势：**不依赖正态性假设**，对任意分布有效；配对设计利用实验组-对照组的相关性，提高检验功效。

### 2.3 MDE(n) 曲线与样本量反解

| 函数 | 功能 |
|---|---|
| `mde_curve(...)` | 对一系列样本量 n，计算最小可检测效应量（MDE） |
| `solve_n_for_mde(target_mde=...)` | 反解：给定目标 MDE，求所需最小样本量 |

MDE 曲线帮助实验设计者回答：「我需要多少样本才能以 80% 功效检测到 d=0.5 的效应？」

### 2.4 A/A 空对照误报率验证

`aa_false_positive_rate(...)` 用模拟数据（control=treatment）运行检验程序，
实测误报率是否 ≤ 名义 α 水平（默认 5%）。这是**检验程序自身的校准**——
如果 A/A 误报率超标，说明检验程序有系统性偏差，所有后续结论都不可信。

### 2.5 回溯性功效计算

`retrospective_power(control_data, treatment_data, ...)` 对已有实验数据，
通过模拟估计在当前样本量和观测效应量下的统计功效。帮助回答：
「这个实验的结果可靠吗？还是因为样本量太小而功效不足？」

### 2.6 六维功效门集成判决

`six_dim_power_gate(control_data, treatment_data, ...)` 是顶层入口，
可选择性地运行上述全部子功能，并给出综合判决：

| verdict | 条件 |
|---|---|
| `pass` | ≥1 维在 FWER 控制下显著，且回溯功效 ≥ min_power，效应量 ≥ min_effect_size |
| `fail` | 无维度在 FWER 控制下显著 |
| `inconclusive` | 有显著维度，但功效或效应量不足 |

## 3. 设计原则

1. **纯函数优先**：所有对外结果都是可确定性序列化的 `dict`，便于落库与逐字节复算。
2. **零领域语义**：维度名/效应量定义/阈值由调用方注入，本模块不出现任何物理域词条。
3. **可复现**：固定随机种子，所有参数可追溯；相同输入+相同种子 → 相同输出。
4. **渐进式**：顶层 gate 函数可选择性开启子功能（`run_aa_check`/`run_mde`/`run_retrospective`），
   避免不必要的计算开销。

## 4. 使用示例

```python
from tools.abm.six_dim_power import six_dim_power_gate, DIMENSIONS
import random

# 构造模拟数据（配对设计）
rng = random.Random(42)
n = 20
control = {d: [rng.gauss(0, 1) for _ in range(n)] for d in DIMENSIONS}
treatment = {d: [c + 1.0 + rng.gauss(0, 0.3) for c in control[d]] for d in DIMENSIONS}

# 运行六维功效门
result = six_dim_power_gate(
    control, treatment,
    alpha=0.05, n_perm=1000, seed=42,
    min_power=0.8, min_effect_size=0.5,
    run_aa_check=True, run_mde=True, run_retrospective=True,
)

print(result["gate_verdict"])   # "pass" / "fail" / "inconclusive"
print(result["gate_reason"])    # 人类可读的判决理由
print(result["permutation_test"]["n_significant"])  # 显著维度数
```

## 5. 验收测试结果

| 测试类 | 条数 | 状态 |
|---|---|---|
| `TestCohensD` | 6 | ✅ 全部通过 |
| `TestPairedMedianDiff` | 3 | ✅ 全部通过 |
| `TestPermutationTest` | 5 | ✅ 全部通过 |
| `TestMdeCurve` | 2 | ✅ 全部通过 |
| `TestSolveNForMde` | 1 | ✅ 通过 |
| `TestAANullControl` | 2 | ✅ 全部通过 |
| `TestRetrospectivePower` | 3 | ✅ 全部通过 |
| `TestSixDimPowerGate` | 6 | ✅ 全部通过 |
| `TestEdgeCases` | 3 | ✅ 全部通过 |
| **合计** | **31** | **✅ 31/31 通过** |

关键验证点：
- Cohen's d 已知值验证：diff=[1,2,3] → d=2.0（误差 < 1e-10）
- 大效应量 → p < 0.01；无效应 → p > 0.05
- Bonferroni-Holm 校正后 adjusted_p ≥ raw_p
- MDE 随样本量增加单调递减
- A/A 误报率 ≤ 5%（名义水平）
- 相同种子 → 相同结果（确定性）

## 6. 与既有机制的关系

| 机制 | 管什么 | 与本模块边界 |
|---|---|---|
| `dimensions.py` | 六维评价表结构 + 多臂对照门 | 本模块**消费**六维数值，做统计推断 |
| `acceptance.py` | 验收聚合（留痕/预测盲/多臂/三腿） | 本模块提供统计功效维度，可被 acceptance 调用 |
| `blind.py` | 预测盲登记器 | 独立机制，无直接依赖 |
| `gates.py` | 七环有序过门状态机 | 本模块是统计门，可嵌入七环中的任一环 |

## 7. 局限与适用范围

1. **配对设计要求**：control 与 treatment 必须等长且一一对应（同一实验单元的前后测量）。
   独立双样本需改用独立置换检验（本模块暂不支持）。
2. **计算开销**：置换检验 10^4 次 × 6 维 × 100 次功效模拟 ≈ 6×10^6 次效应量计算。
   生产环境建议用 `run_cluster` 分片并行。
3. **MDE 曲线精度**：默认用 100 次功效模拟估计，存在 Monte Carlo 噪声（±0.03）。
   高精度需求可增大 `n_rep` 参数。
4. **效应量解释**：Cohen's d 的「小/中/大」阈值（0.2/0.5/0.8）是经验规则，
   具体领域应使用领域特定的 MDE 阈值。
5. **不替代物理判据**：统计显著 ≠ 物理重要。本模块只回答「差异是否可靠」，
   不回答「差异是否有物理意义」——后者由领域判据决定。

## 8. 文件清单

| 文件 | 行数 | 说明 |
|---|---|---|
| `tools/abm/six_dim_power.py` | 673 | 核心实现（纯函数，零依赖） |
| `tools/abm/acceptance.py` | 141 | 验收聚合（调用本模块） |
| `tests/abm/test_six_dim_power.py` | 378 | 31 条测试 |
| `docs/P143W2-N04_six_dim_power.md` | — | 本文档 |
