# W3-N08 风-流-冰三场耦合船舶操纵性与漂移力修正

*任务*: W3-N08 · *生成时间*: 2026-10-31T13:04:28.435495+00:00 · *参考船*: VLCC-320 reference hull (KVLCC2 particulars)

## 1 案例介绍

**物理背景**。冰区/恶劣海况下的船舶操纵性由三类环境载荷叠加决定：水上受风面积上的风压、水下湿表面上的流压、以及船-冰相互作用产生的破冰/碎冰挤靠阻力。三者的量级随环境强度变化，且都随相对方位角非线性变化，必须叠加进 MMG 三自由度操纵运动方程才能得到可信的回转/Z 形性能。

**几何与主尺度**（`backend/physics/ship_env_maneuvering/data.py`，KVLCC2 族 VLCC 参考船）：

| 参数 | 数值 | 参数 | 数值 |
|---|---|---|---|
| $L_{pp}$ | 320.0 m | $B$ | 58.0 m |
| $T$ | 20.8 m | $C_b$ | 0.8098 |
| 排水体积 | 312622 m³ | 质量 | 320.4 kt |
| 湿表面积 | 26627 m² | 水线以上侧面积 $A_L$ | 1500 m² |
| 水线以上正面积 $A_F$ | 500 m² | 舵面积 $A_R$ | 77.9 m² |
| $I_z$ | 2.051e+12 kg·m² | 横流投影面积 $A_{LS}$ | 5658 m² |

## 2 方法与工具链

新增领域包 `backend/physics/ship_env_maneuvering/`（23 个 pytest 用例位于 `tests/test_ship_env_maneuvering.py`）：

| 模块 | 内容 |
|---|---|
| `data.py` | 主尺度/面积、OCIMF 型风/流系数锚点、ITTC-57 摩擦线、Riska 与 Lindqvist 型冰阻力系数集（每条常数带 provenance） |
| `loads.py` | 风载荷 $\frac12\rho_{air}V_R^2 C_X(\psi)A_L$ 等、流载荷、冰阻力与冰漂移力、独立参考模型（投影面积法 / 横流+ITTC-57 法）、相对风/流场变换 |
| `mmg.py` | 三自由度 MMG 方程 + 螺旋桨 $K_T(J)$ + 舵力 $F_N$、RK4 积分、回转/ Z 形试验、独立稳态回转平衡求解器、两套水动力系数集 |
| `validation.py` | 判据 (a)(b)(c) 与冰阻力不确定性带、耦合工况矩阵 |

**三场耦合实现**：MMG 方程右端叠加环境力与力矩

```
(m+m_x)u̇ − (m+m_y)vr = X_H + X_P + X_R + X_wind + X_ice + X_cur
(m+m_y)v̇ + (m+m_x)ur = Y_H + Y_R      + Y_wind + Y_ice + Y_cur
(I_z+J_z)ṙ              = N_H + N_R      + N_wind + N_ice + N_cur
```

流场默认按**运动学**方式进入（船体/桨/舵力用对水速度计算，物理上自洽），并保留显式流漂移力通道用于静态校核；风场与冰场按相对速度/漂角实时计算。冰阻力作用方向与瞬时速度矢量相反，另加航道约束偏航阻尼矩。

## 3 计算结果

### 3.1 判据 (a) 风载荷 vs 独立参考（20 m/s, 4 个风向角）

| 相对风向 | 模型合力 (kN) | 参考合力 (kN) | 合力相对误差 | 模型 $N$ (MN·m) | 参考 $N$ (MN·m) | 力矩相对误差 |
|---|---|---|---|---|---|---|
| 0° | 96.34 | 98.00 | 1.69 % | 0.00 | 0.00 | — |
| 45° | 172.92 | 163.69 | 5.63 % | 6.32 | 6.00 | 5.44 % |
| 90° | 291.07 | 312.38 | 6.82 % | 11.21 | 12.00 | 6.53 % |
| 135° | 171.71 | 163.69 | 4.90 % | 6.02 | 6.00 | 0.38 % |

最大相对误差 **6.82 %**（阈值 10 %）→ **通过**。IMO IS Code 2008 横风压强锚点校核：模型 194.05 Pa vs 指南 201.60 Pa，相对误差 3.75 %。

### 3.2 判据 (b) 流载荷 vs 独立参考（$\psi_c$ = 60°, 3 种流速）

| 流速 (m/s) | 模型合力 (kN) | 参考合力 (kN) | 合力相对误差 | 模型 $N$ (MN·m) | 参考 $N$ (MN·m) | 力矩相对误差 |
|---|---|---|---|---|---|---|
| 0.5 | 508.0 | 543.7 | 6.55 % | 16.26 | 17.40 | 6.52 % |
| 1.0 | 2032.1 | 2174.6 | 6.55 % | 65.05 | 69.59 | 6.52 % |
| 2.0 | 8128.6 | 8698.6 | 6.55 % | 260.20 | 278.35 | 6.52 % |

最大相对误差 **6.55 %**（阈值 10 %）→ **通过**。

### 3.3 判据 (c) 三场耦合回转性

**验证检查**（同一水动力系数集，35° 满舵）：

| 检查项 | 值 A | 值 B | 相对误差 | 阈值 | 结果 |
|---|---|---|---|---|---|
| time_domain_vs_steady_state_equilibrium | 920.20 m | 985.97 m | 6.67 % | 10.0 % | 通过 |
| time_step_convergence_dt_0.5_vs_0.25 | 920.20 m | 920.20 m | 0.00 % | 1.0 % | 通过 |
| coefficient_set_sensitivity (敏感性) | 920.20 m | 636.55 m | 30.82 % | 10.0 % | 未通过 |

**耦合工况矩阵**（时域 180° 战术直径 + 独立稳态平衡解）：

| 工况 | $D_T/L$ (时域) | $D_T/L$ (稳态) | 相对平静水变化 | 进距/L | 正横距/L | 稳态漂角 (°) | 稳态速度比 |
|---|---|---|---|---|---|---|---|
| calm_water | 2.88 | 3.08 | 0.00 % | 3.31 | 1.44 | 13.83 | 0.70 |
| wind_20ms_beam | 2.82 | 2.96 | 3.93 % | 3.26 | 1.40 | 14.29 | 0.69 |
| current_1ms_beam | 2.67 | 3.27 | 6.00 % | 4.31 | 1.46 | 23.64 | 0.74 |
| ice_1m | 3.14 | 3.54 | 14.86 % | 3.37 | 1.50 | 12.53 | 0.57 |
| three_field_coupled | 2.99 | 3.68 | 19.51 % | 4.32 | 1.50 | 24.72 | 0.61 |

**物理趋势检查**：`ice_reduces_steady_speed`=✓；`ice_increases_steady_turning_diameter`=✓；`beam_current_increases_drift_angle`=✓；`beam_wind_increases_drift_angle`=✓；`coupled_case_exceeds_calm_turning_diameter`=✓

### 3.4 Z 形试验（10°/10°）

| 工况 | 首超调角 (°) | 第二超调角 (°) | 至首舵角时间 (s) | 完成 |
|---|---|---|---|---|
| 平静水 | 13.55 | 17.41 | 88.5 | True |
| 三场耦合 | 15.70 | 15.90 | 236.5 | True |

### 3.5 冰阻力（支持性证据，任务未设显式阈值）

| 冰厚 (m) | 流速 (m/s) | Riska 型 (kN) | Lindqvist 型 (kN) | 偏差 |
|---|---|---|---|---|
| 0.5 | 0.0 | 96.1 | 98.5 | 2.5 % |
| 0.5 | 1.0 | 134.5 | 99.6 | 35.1 % |
| 0.5 | 2.0 | 172.9 | 100.6 | 71.8 % |
| 1.0 | 0.0 | 384.5 | 394.1 | 2.5 % |
| 1.0 | 1.0 | 538.0 | 398.3 | 35.1 % |
| 1.0 | 2.0 | 691.6 | 402.6 | 71.8 % |
| 1.5 | 0.0 | 865.0 | 886.8 | 2.5 % |
| 1.5 | 1.0 | 1210.6 | 896.3 | 35.1 % |
| 1.5 | 2.0 | 1556.1 | 905.8 | 71.8 % |

两模型平均偏差 **36.4 %**，最大 71.8 % —— 作为**模型不确定带**报告，不作为系数级校验通过声明。

### 3.6 单元测试（判据 d）

`/usr/bin/python3 -m pytest tests/test_ship_env_maneuvering.py -q --no-header` → **23 passed, 0 failed**（阈值 ≥ 12）→ **通过**。

## 4 结果图

**fig1_wind_coefficients.png** — 风压系数曲线（模型 vs 独立投影面积参考）与偏航力矩系数

**fig2_load_validation.png** — 判据 (a)(b)：风载荷 4 风向角、流载荷 3 流速的模型 vs 参考与相对误差

**fig3_turning_trajectories.png** — 五工况 35° 回转轨迹（水坐标系）与回转过程中的速度损失

**fig4_coupled_turning_diameter.png** — 判据 (c)：各环境工况战术直径（时域 vs 稳态）与 IMO A.751 限值

**fig5_ice_resistance.png** — 冰阻力两独立分解 vs 冰厚，以及风载荷随风速的包络（含参考模型）

## 5 注意点 / 局限

1. **无外部试验数据可用**：worktree 内没有冰水池操纵试验、EFD 或 OCIMF 1994 指南表格的可机读数据，且通用外网访问被封锁（仅 PyPI 镜像可用），因此「vs 冰水池试验 < 10 %」与「vs OCIMF 表格 < 10 %」两条判据按原样**无法诚实主张**，均以 WITHHELD 记录并给出替代证据。
2. **系数敏感性是主导误差源**：同一物理方程、两套独立推演的水动力系数集给出战术直径相差 30.8 %。这说明在没有 EFD 标定的条件下，回转直径的绝对精度不可能靠实现正确性达到 10 % 级。
3. **螺旋桨尾流放大因子的正则化**：舵来流 $u_R=u(1-w_R)\sqrt{1+8K_T/(\pi J^2)}$ 在 $J\to0$（重载/低速）时发散，本实现把放大因子上限设为 2.0（代码内注释说明）。未加此限制时，低速段舵力相对船体阻尼被高估，仿真的回转会持续收紧并导致速度崩溃（已实测，误差量级 10²）。
4. **冰阻力系数级 provenance 不足**：Riska/Lindqvist 原始论文离线不可得，系数取自文献工程区间并保证 $R\propto h^2$、$\propto\sigma_f$、线性于 $B$、随 $V$ 与 $\mu$ 单调等标度律（有单元测试）。冰-航道偏航阻尼为工程闭合项，敏感性已在线性范围验证。
5. **流场的处理方式**：运动学相对速度法为主（避免与显式流漂移力重复计数），回转指标在**水坐标系**下统计（扣除流速平流），否则地面轨迹直径在流速场中无意义。
6. **适用域**：单桨单舵、舵角 ≤ 35°、弗劳德数 $Fn\approx0.14$ 的肥胖型（$C_b\approx0.81$）船舶；冰厚 0.2–1.8 m；风速至 25 m/s。水温 15 ℃、海水密度 1025 kg/m³。

## 6 误差判定（逐项，含是否 < 3 %）

| 关键量 | 参考基准 | 相对误差 | < 3 % ? |
|---|---|---|---|
| 风载荷 4 风向角最大误差 | 独立投影面积模型 | 6.82 % | 6.82 % — >= 3 % |
| 横风压强 (90°) | IMO IS Code 2008 | 3.75 % | 3.75 % — >= 3 % |
| 流载荷 3 流速最大误差 | 横流阻力 + ITTC-57 | 6.55 % | 6.55 % — >= 3 % |
| 回转直径（时域 vs 稳态解） | 独立平衡求解器 | 6.67 % | 6.67 % — >= 3 % |
| 回转直径（时间步加密） | dt 0.5 s → 0.25 s | 0.00 % | 0.00 % — < 3 % |
| 回转直径（系数集敏感性） | 独立推演系数集 | 30.82 % | 30.82 % — >= 3 % |
| 冰阻力两模型偏差（均值） | Lindqvist 型分解 | 36.44 % | 36.44 % — >= 3 % |

**结论**：(a)(b) 两条环境载荷判据在独立公式基准下均 < 10 %（6.82 % / 6.55 %），但**均未达到 < 3 %**，不应据此宣称高保真；(c) 的实现正确性检查中，时间步收敛 < 3 %（0.00 %），而时域 vs 独立平衡解为 6.67 %（< 10 %，未达 < 3 %）；但**回转直径的绝对精度（vs 试验）不可主张** —— 系数敏感性 30.8 % 远大于 10 %，「vs 冰水池试验 < 10 %」判据 honest WITHHELD，不以实现正确性冒充试验符合性。(d) pytest 全部通过。

## 7 审计链

- 判据数据：`artifacts/m795/validation.json`（本报告全部数字由该 JSON 程序化注入）
- 代码：`backend/physics/ship_env_maneuvering/`（data/loads/mmg/validation）
- 测试：`tests/test_ship_env_maneuvering.py`（23 passed）
- 复现：`python3 scripts/m795/run_validation.py`

- checks_passed.a_wind_load = True
- checks_passed.b_current_load = True
- checks_passed.c_verification = True
- checks_passed.c_trends = True

**Withheld 清单**：

- 'wind load vs OCIMF 1994 guide tables' - guide tables not machine-readable offline; substituted by an independent projected-area formulation + the published IMO IS Code 2008 beam anchor
- 'current load vs OCIMF current curves' - same reason; substituted by strip cross-flow + ITTC-57 reference
- 'turning diameter vs ice-basin manoeuvring trials < 10%' - no ice-basin trial data available offline; substituted by code-to-code verification (equilibrium solve, dt refinement) plus an explicit coefficient-set sensitivity band
- 'ice resistance vs original Riska/Lindqvist coefficients' - papers unavailable offline; reported as a two-model uncertainty band