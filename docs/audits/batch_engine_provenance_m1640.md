# 批量执行器推进模型接线审计与修复（mission 1640）

*生成时间 (UTC)*: 2026-11-01
*仓库*: `TensorPlatform` · *分支*: `agent/m1640`
*改动文件*: `tools/batch_scan/batch_executor.py`、`tests/test_batch_engine_provenance.py`、
`tools/verify/batch_engine_provenance_report.py`
*机器可读产物*: `artifacts/m1640/provenance_summary.json`（本文件全部数字的注入源）

## 1. 案例与问题

`tools/batch_scan`（PC-1 千工况批量执行器）的 `ShipCalculationEngine.calc_propeller`
是一条 **MOCK 级硬编码代理**：

```
KT = max(0.05, 0.5 - 0.3*J)
KQ = max(0.01, 0.05 + 0.02*J)
eta0 = clip(J*KT/(2*pi*KQ), 0.3, 0.85)
```

而平台能力清单把 `propeller.open_water` 标为 **REAL**：m1393 已经交付了真实模型
`backend/services/propeller_open_water.py`（叶片元 + 动量耦合 BEM），并配套了验证套件
`tests/test_propeller_open_water.py`。**接线（wiring）从未完成**，因此该验证套件里
`TestBatchWrapperWiring::test_batch_executor_uses_bem_model` 长期是红的
（`KeyError: 'model'`）：平台一边宣称 REAL，一边在批量工况路径上继续用代理出数。

几何/工况（与验证套件同源）：D = 5.0 m，P/D = 1.0，
Ae/A0 = 0.55，Z = 4，n = 120 rpm，
ρ = 1025 kg/m³。

## 2. 方法

1. **接线**：`calc_propeller` 改为构造 `PropellerGeometry` 并调用
   `open_water_point(geom, J, rho=1025, n_rps=n)`，单位口径 `rpm→n [rps]`、`kn→m/s`；
   返回值保留旧键名（`advance_coefficient / thrust_coefficient / torque_coefficient /
   efficiency / thrust_kn`）并新增 `torque_knm / eta_ideal / induction_converged / model`。
2. **溯源标注**：`calc_resistance / calc_stability / calc_geometry` 仍为解析代理，但统一加
   `model` 字段（`surrogate_ittc57_friction_v0` / `surrogate_box_hydrostatics_v0` /
   `surrogate_parametric_geometry_v0`），类文档如实写明 REAL / SURROGATE 分级，
   杜绝"代理冒充 REAL"。
3. **验证**（`tests/test_batch_engine_provenance.py`，13 项）：
   逐位一致性、相似律不变性、零推力推进比存在性、坏几何拒绝、旧代理反例证伪
   （敏感性证据）、溯源标签、`case_generator → CaseExecutor` 端到端冒烟。

## 3. 计算结果

### 3.1 接线残差（批量执行器 vs 服务直调，逐位）

| J | engine $K_T$ | service $K_T$ | \|ΔK_T\| | engine $K_Q$ | service $K_Q$ | \|ΔK_Q\| |
|---|---|---|---|---|---|---|
| 0.2 | 0.4062 | 0.406165 | 3.46e-05 | 0.0517 | 0.051749 | 4.88e-05 |
| 0.4 | 0.3274 | 0.327442 | 4.20e-05 | 0.0452 | 0.045160 | 4.03e-05 |
| 0.6 | 0.2284 | 0.228364 | 3.65e-05 | 0.0347 | 0.034741 | 4.09e-05 |
| 0.8 | 0.1183 | 0.118329 | 2.94e-05 | 0.0209 | 0.020886 | 1.44e-05 |

最大绝对差：$K_T$ 4.20e-05，$K_Q$ 4.88e-05
—— 恰为 4 位小数舍入下限 5e-5，即**同一模型、零物理偏差**。

### 3.2 相似律（同 J = 0.6，换 D 与 n）

| D (m) | rpm | J | $K_T$ | $K_Q$ |
|---|---|---|---|---|
| 3 | 200 | 0.600000 | 0.2284 | 0.0347 |
| 5 | 120 | 0.600000 | 0.2284 | 0.0347 |
| 7.5 | 80 | 0.600000 | 0.2284 | 0.0347 |

跨尺度极差：$K_T$ 0.0e+00，$K_Q$ 0.0e+00。

### 3.3 REAL（BEM）与旧代理（MOCK）对照

| J | $K_T$ (BEM) | $K_Q$ (BEM) | $\eta_0$ (BEM) | 致动盘上限 |
|---|---|---|---|---|
| 0.0 | 0.4692 | 0.0556 | 0.0000 | 1.0000 |
| 0.1 | 0.4391 | 0.0539 | 0.1296 | 0.1721 |
| 0.2 | 0.4062 | 0.0517 | 0.2498 | 0.3235 |
| 0.3 | 0.3696 | 0.0489 | 0.3608 | 0.4561 |
| 0.4 | 0.3274 | 0.0452 | 0.4616 | 0.5727 |
| 0.5 | 0.2796 | 0.0404 | 0.5509 | 0.6753 |
| 0.6 | 0.2284 | 0.0347 | 0.6277 | 0.7642 |
| 0.7 | 0.1748 | 0.0283 | 0.6885 | 0.8398 |
| 0.8 | 0.1183 | 0.0209 | 0.7214 | 0.9038 |
| 0.9 | 0.0587 | 0.0124 | 0.6762 | 0.9577 |
| 1.0 | -0.0082 | 0.0022 | 0.0000 | 1.0000 |
| 1.1 | -0.1703 | -0.0257 | 0.0000 | 1.0000 |
| 1.2 | -0.3293 | -0.0574 | 0.0000 | 1.0000 |
| 1.3 | -0.4825 | -0.0920 | 0.0000 | 1.0000 |

| J | $K_T$ (legacy) | $K_Q$ (legacy) | $\eta_0$ (legacy) |
|---|---|---|---|
| 0.0 | 0.5000 | 0.0500 | 0.3000 |
| 0.1 | 0.4700 | 0.0520 | 0.3000 |
| 0.2 | 0.4400 | 0.0540 | 0.3000 |
| 0.3 | 0.4100 | 0.0560 | 0.3496 |
| 0.4 | 0.3800 | 0.0580 | 0.4171 |
| 0.5 | 0.3500 | 0.0600 | 0.4642 |
| 0.6 | 0.3200 | 0.0620 | 0.4929 |
| 0.7 | 0.2900 | 0.0640 | 0.5048 |
| 0.8 | 0.2600 | 0.0660 | 0.5016 |
| 0.9 | 0.2300 | 0.0680 | 0.4845 |
| 1.0 | 0.2000 | 0.0700 | 0.4547 |
| 1.1 | 0.1700 | 0.0720 | 0.4134 |
| 1.2 | 0.1400 | 0.0740 | 0.3613 |
| 1.3 | 0.1100 | 0.0760 | 0.3000 |

关键差异：

* 零推力推进比 $J_0$ = **0.993**（BEM）；旧代理把 $K_T$ 夹在
  0.05，在 J = 1.3 时 BEM 的 $K_T$ = -0.483（负推力），
  代理却仍报正推力 —— 高 J 区符号错误。
* 旧代理 $K_Q$ 在整段网格上**上升** 0.0260（物理上应下降）；
  效率被 clip 成 [0.3, 0.349572, 0.361325, 0.413361, 0.417096, 0.454728, 0.464202, 0.484486, 0.492867, 0.501579, 0.50482] 的平台值，无物理内容。

图 1：`docs/audits/figs/m1640_kt_kq_eta_vs_J.png`（$K_T/K_Q/\eta_0$-$J$ 双模型对照，
含致动盘效率上限与 $J_0$ 标注）；图 2：`docs/audits/figs/m1640_scale_invariance.png`
（相似律柱状图 + 接线残差半对数图）。

## 4. 注意点与局限

* 旧代理的**绝对精度**从未被文献图表校验过（无 Wageningen 点值可离线取得），因此本次只
  关闭"REAL 被 MOCK 顶替"这一溯源缺陷；`propeller.open_water` 的绝对精度仍为
  **WITHHELD**（沿 m1393 口径），本次未改变该结论。
* `calc_resistance / calc_stability / calc_geometry` **仍是代理**（SURROGATE），本次只加
  溯源标签，未替换为 `ship_resistance` / `ship_hydrostatics` / `stability_reference`；
  批量报告若引用这三项数字，不得作为对实验精度的证据。已在其返回值中显式标注。
* 批量生成器的高 J 工况（$J \gtrsim 1.0$）会拿到负 $K_T$，其判据 `thrust_coefficient ≥ 0`
  会把该工况判 FAILED —— 这是**物理真值越界**而非缺陷；生成器参数域是否需要收窄到
  工作区（$J \in [0.2, 0.95]$）留作后续项。
* 已知悬挂证据指针（顺带发现，未修）：`backend/api/domain/ship_seakeeping.py` 第 98 行引用
  `docs/audits/seakeeping_verification_m1433.md`，该文件在树内不存在（`artifacts/m1433/` 为空）。

## 5. 误差判定

| 关键量 | 基准 | 相对/绝对误差 | <3% ? |
|---|---|---|---|
| 批量执行器 $K_T$ vs BEM 服务 | 同一模型直调 | 4.20e-05（绝对，舍入下限） | 是（判据为 <5e-5 绝对） |
| 批量执行器 $K_Q$ vs BEM 服务 | 同一模型直调 | 4.88e-05（绝对） | 是 |
| 相似律 $K_T$/$K_Q$ 跨 (D, n) 极差 | 相同 J 解 | 0.0e+00 / 0.0e+00 | 是（精确不变） |
| BEM 绝对精度 vs 文献图表 | Wageningen B 系列 | 未取得点值 | **无基准，未判定（WITHHELD）** |

结论：本次修复的判据是"接线一致性"，已机器证明为舍入级一致；**绝对物理精度未被本单验证**，
如实标注为 WITHHELD，不作粉饰。
