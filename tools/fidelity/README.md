# `tools/fidelity` —— 保真度可靠性层（误差传递 / 可信域 / 切换门）

`tools/multifidelity`（m12638）回答「预算怎么分」；本包回答另一半问题：

> 便宜模型给出的这个数，**错多少**、**在这个工况点上能不能用**、**什么时候必须升档**。

两个包共用同一套「档位」概念但互不 import：可靠性层不知道也不关心档位是怎么造出来的，
它只要标定数据（每档在同一批点上的数值 + 参考值）。

## 原语

| 模块 | 职责 |
|---|---|
| `TrustDomain` | 标定箱与**归一化外部距离** `d`（`d = 0.2` = 超出标定域 20%，与门控判据同一坐标） |
| `TierModel` / `calibrate_tier` | 单档标定：系统偏差 `b`、split-conformal 分位（报告档 95% / 报警档 99%）、Lipschitz 包络、外推风险律 |
| `FidelityChain` | 传递算术：单档上界、`Σ|w_k| L_k eps_k(x)` 总上界、偏差修正可信区间、组合预测 |
| `FidelitySwitchGate` | 逐点取「最低可用档」，原因码 `domain_exceeded` / `tolerance_exceeded` / `interval_disjoint` / `extrapolation_study_exceeded` |
| `ExtrapolationLaw` / `fit_extrapolation_law` | `eps(d) = C d^p`，硬约束 `p ≥ 1`，记录 `study_max_distance` |
| `interval_coverage` / `check_conservativeness` | 覆盖率与「上界 ≥ 实际」的机器审计（判据 C1/C2 直接调它们） |

## 用法

```python
from tools.fidelity import (CalibrationSet, ChainLink, FidelityChain, calibrate_tier)

cal = CalibrationSet(X=X_cal, reference=y_ref,
                     tier_values={0: y_coarse, 1: y_medium, 2: y_fine})

chain = FidelityChain(
    {r: calibrate_tier(r, f"tier{r}", fn, cal, study=(d_study, err_study))
     for r, fn in enumerate((f0, f1, f2))},
    links={0: ChainLink(weight=0.5, sensitivity=2.0),   # 下游泛函的 Lipschitz 常数自己给
           1: ChainLink(0.25, 2.0), 2: ChainLink(0.25, 2.0)},
)

bound = chain.composed_bound(X_new, mode="guard")        # 传递后的总误差上界
lo, hi = chain.credible_interval(0, X_new)               # 低档的 95% 可信区间
gate = chain.gate(max_distance=0.0, rel_tol=None)        # 超域即升档
decision = gate.advice(X_new)[0]
decision.escalate, decision.target_rank, decision.reasons
```

## 数值陷阱与回归保护（本包真实踩过的坑）

| 坑 | 症状 | 保护用例 |
|---|---|---|
| 用报告分位当报警阈值 | 域内误报天然 ~5%，直接吃掉 5% 预算 | `test_gate_false_alarm_rate_inside_domain`（实测 0.0000） |
| 「经验分位 = 上界」 | 2 维稀疏标定下 2/300 点实际误差超上界（conformal 只保边际覆盖） | `test_two_dimensional_chain_coverage_and_bound`（默认 `lipschitz_margin=1.0, guard_margin=0.25` 时 0 违例） |
| 幂律指数不设下界 | 误差随距离「衰减」的拟合也能通过，风险律变成免责声明 | `test_extrapolation_law_enforces_power_law_minimum`（`p ≥ 1` 硬约束） |
| 标定栅格混叠 | 误差结构比标定网格更细 → 节点残差≈0，可信度被系统性高估 | `test_gate_credible_domain_guard_fires_on_aliased_error`（`interval_disjoint` 报警） |
| 下游灵敏度低估 | 传递上界不再保守，且没有任何报错 | `test_understated_sensitivity_is_detectable`（故意 `L = 0.05`，违例率 > 50%） |
| 上界对 `actual → 0` 的点比值发散 | 单看 max(bound/actual) 会得出「上界荒谬」的错误结论 | 报告同时给 median / p95（`check_conservativeness`） |

## 适用范围与已知局限

* 包络界假设残差场 Lipschitz 常数不超过观测割线最大值的 `1 + margin` 倍；
  **高维稀疏标定会破这条假设**（包络估低）→ 用 `check_conservativeness` 探针 + 加密标定。
* 外推幂律只在 `study_max_distance` 内被验证；超出该距离时门控必须升档
  （见 `extrapolation_study_exceeded`）。
* 门控的 `max_distance` 默认 0（任何超域都升档）：这是「宁可多花一次高档计算」的策略选择，
  不是数学结论，调用方可按预算放宽。

## 领域算例（不在本包内）

`docs/p470w2_n10/`：截断展开三档基准（C1–C6 判据数字、4 张图、证据 JSON、复现脚本）。