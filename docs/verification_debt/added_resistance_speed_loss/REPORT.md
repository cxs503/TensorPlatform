# 波浪增阻 / 失速验证欠账：Hs-only 经验律的硬判据判决与「Gerritsma 归属」纠错（m1859）

**任务** m1859（verifier 席自找活 tp-ver-7）｜**仓** TensorPlatform｜**分支** `agent/m1859`
**被测件** `backend/services/digital_twin/models/seakeeping.py`（`added_resistance` / `irregular_seastate`）
与 `backend/services/digital_twin/surrogate_registry.py`（seakeeping 代理的 `reference_model` 声明）
**判据工具** `tools/verifier/raw_added_resistance_gate.py`｜**测试** `tests/test_raw_added_resistance_gate.py`（5 pass + 4 strict xfail）
**工件** `artifacts/verification_debt/added_resistance_speed_loss/{results.json, criteria_table.md, band_table.md, loss_table.md, fig1..fig3.png}`
**上游欠账来源** `docs/verification/seakeeping_crosspath_adjudication.md`（第 120-121、151 行：R_AW 无周期依赖，未做）

## ① 案例介绍（物理背景 / 对象 / 参数）

波浪增阻 $R_{AW}$ 是耐波与绿色航行的接合量：它决定船舶在给定海况下为保持航速所需的
功率增量，进而决定波浪失速、EEXI/CII 的天气修正与数字孪生的实时能耗预测。平台内该量
只有一条通路：数字孪生 `models/seakeeping.py` 的 `added_resistance()`（被
`online_predictor`、`surrogate_registry` 与 5 个 PCE 代理引用）。

被测公式（代码实际形态，逐字对应 `RAW_LAW["formula"]`）：

$$R_{AW} = c_{aw}\cdot \cos^2\!\left(\frac{180-|\beta|}{2}\right)\cdot\rho g H_s^2 \frac{B^2}{L}\cdot\left(0.5+0.5\min\left(\frac{Fn}{0.20},1.5\right)\right)$$

$c_{aw}=0.055$（`C_AW`）。**注意**：修改前该核在代理注册表里被标注为
`2dof_modal_strip + gerritsma_added_resistance`，即声称实现了 Gerritsma–Beukelman
谱方法（需要对入射谱 $S(\omega)$、相对运动 RAO 作谱积分）；上面这条式子不含 $T_p$、
不含谱形、不含相对运动项。**这是本单要裁决的核心事实。**

参数表（判据用的 5 条船，均为 `HullForm` 闭式几何 + 设计 Fn）：

| 船 | L [m] | B [m] | T [m] | Cb | 设计 Fn |
|---|---|---|---|---|---|
| KVLCC2 tanker | 320.0 | 58.0 | 20.8 | 0.82 | 0.137 |
| KCS container | 230.0 | 32.2 | 10.8 | 0.651 | 0.190 |
| Handysize bulker | 180.0 | 30.0 | 10.5 | 0.83 | 0.160 |
| Ferry | 120.0 | 20.0 | 5.0 | 0.60 | 0.220 |
| Trawler | 45.0 | 11.0 | 3.2 | 0.55 | 0.280 |

## ② 怎么做的（方法 / 流程 / 关键设置）

纯解析求值 + 仓内核，无 CFD/DEM 重算（本机 load15 ≈ 122 ≥ 100，按容量纪律只做轻量活）。
六条判据全部落在**活模块**上，判据先于动手固定（`tools/verifier/raw_added_resistance_gate.py`）：

- **W1 周期敏感性**：$H_s=4$ m 迎浪、$T_p\in\{3,5,7,9,11,14\}$ s，要求 $R_{AW}$ 极差 > 15 %
  （实船增阻在 $\lambda\approx L$ 附近有峰，谱方法必然对 $T_p$ 敏感）。
- **W2 二阶律**：$\log R_{AW}$ vs $\log H_s$ 斜率必须为 2，误差 < 1 %。
- **W3 声明/代码漂移门**：用 `RAW_LAW` 声明字段独立重算公式，对 5 船 × 4 航向 × 5 Fn × 3 $H_s$
  = 300 点与代码比对，相对残差必须 < 1e-12。
- **W4 零速伪影**：$R_{AW}(Fn\to0)/R_{AW}(Fn=0.20)$ 必须落在 [0.8, 1.2]
  （二阶平均漂移力不会因为船停下来就减半）。
- **W5 声明谱带**：模块自称 "$c_{aw}=0.055$ 使 $H_s=4$ m 迎浪下增阻为静水阻力的 5–15 %"，
  用仓内 Holtrop 核逐船核对。
- **W6 下游传播**：由 $\Delta V = R_{AW}/(dR_T/dV)$（一阶推力平衡，$dR_T/dV$ 用 Holtrop
  中心差分）算隐含失速，要求随 $T_p$ 变化 > 15 %。

## ③ 计算结果（数字表，全部由工具写出）

裁决：**2 PASS / 4 FAIL**，失败项 = W1, W4, W5, W6，
证据级 **MOCK**。

| criterion | measured | threshold | verdict |
|---|---|---|---|
| W1 period sensitivity (>15% over Tp in [3,14] s) | 0 | 0.15 | FAIL |
| W2 quadratic law d(log R_AW)/d(log Hs) = 2 (err<1%) | 2 | 2 | PASS |
| W3 RAW_LAW declaration reproduces code (<1e-12 rel) | 0 | 1e-12 | PASS |
| W4 R_AW(Fn->0)/R_AW(Fn=0.20) in [0.8,1.2] | 0.5 | - | FAIL |
| W5 R_AW/R_calm at Hs=4 m head seas within declared 5-15% | 4.6, 5.2, 8.2, 17.6, 75.7 | - | FAIL |
| W6 speed loss varies with Tp by >15% | 0 | 0.15 | FAIL |

**W5 逐船明细（$H_s=4$ m，迎浪，设计航速）**

| ship | Fn | V [m/s] | R_calm [kN] | R_AW [kN] | share [%] |
|---|---|---|---|---|---|
| KVLCC2 tanker | 0.137 | 7.67 | 1707.1 | 78.3 | 4.59 |
| KCS container | 0.190 | 9.02 | 744.0 | 38.9 | 5.23 |
| Handysize bulker | 0.160 | 6.72 | 484.1 | 39.8 | 8.22 |
| Ferry | 0.220 | 7.55 | 175.5 | 31.0 | 17.64 |
| Trawler | 0.280 | 5.88 | 37.7 | 28.5 | 75.72 |

带内 2/5 船；带外：KVLCC2 tanker 4.6%, Ferry 17.6%, Trawler 75.7%。

**W6 逐 $T_p$ 隐含失速（KVLCC2，$H_s=4$ m，$dR_T/dV$ = 495918.2 N/(m/s)）**

| Tp [s] | R_AW [kN] | implied dV [kn] | dV/V [%] |
|---|---|---|---|
| 3 | 78.344 | 0.3071 | 2.058 |
| 5 | 78.344 | 0.3071 | 2.058 |
| 7 | 78.344 | 0.3071 | 2.058 |
| 9 | 78.344 | 0.3071 | 2.058 |
| 11 | 78.344 | 0.3071 | 2.058 |
| 14 | 78.344 | 0.3071 | 2.058 |

## ④ 结果图

- `fig1_tp_blindness.png`：五船的 $R_{AW}(T_p)$ 是**严格水平线**（KVLCC2 78.3435 kN，
  逐 $T_p$ 逐位相同）；同一图上垂荡 RMS 随 $T_p$ 变 0.00207→1.3675 m，
  证明"周期不起作用"是**该核的结构缺失**，而不是整个耐波模型对 $T_p$ 无感。
- `fig2_declared_band.png`：声明 5–15 % 带 vs 实测占比，两条小船（Ferry 17.6 %、Trawler 75.7 %）
  冲出带外。
- `fig3_speed_loss.png`：隐含失速 vs $T_p$，恒为 0.3071 kn。

## ⑤ 注意点（踩过的坑 / 局限 / 适用范围）

1. **归属纠错是本单最硬的结论**：`reference_model` 曾被写成 `gerritsma_added_resistance`，
   而 Gerritsma–Beukelman 的定义特征（谱积分、相对运动、$T_p$ 依赖）一条都不在代码里。
   W1/W6 全零极差即"该方法是未实现"的机器证据。已改为 `raw_empirical_hs_only_v1`
   （代码 + 文档 + 审计工具 4 处），并由 `test_provenance_label_is_not_gerritsma` 锁死。
2. **W5 只在小船上崩**：$B^2/L$ 标度对小船（Trawler $B^2/L=2.69$、$R_{calm}$ 仅 37.7 kN）给出
   与静水阻力同量级的增阻。这意味着 $c_{aw}=0.055$ 的标定只在肥大型商船族上成立，
   跨船型使用时 5–15 % 的自述不成立——**不要**把该核用于小船/高速船的失速或 EEXI 天气修正。
3. **未做（如实登记）**：没有引入周期依赖。要做对必须实现谱积分（Gerritsma–Beukelman 或
   STAWAVE 类），需要相对运动 RAO 与入射谱；本单是验证单，在无模型试验基准的条件下
   自行发明一条增阻公式会引入比现状更危险的"看似 REAL 的假物理"，故只做判决与声明，
   把公式替换留给后续功能单（建议判据：以本条 W1/W4/W5/W6 转 PASS 为验收）。
4. **$T_p$ 不敏感的下游后果**：数字孪生的 seakeeping PCE 代理把 $T_p$ 当输入（3–14 s），
   但 $R_{aw}$ 输出对 $T_p$ 的偏导恒为 0；代理的 $R^2$ 仍然 0.99999（
   `docs/verification/digital_twin_surrogate_accuracy.md`）——这恰好说明
   **代理精度高 ≠ 物理对**，二者必须分开记账。
5. **未改变任何数值行为**：修改前后 $R_{AW}$ 逐位相同（KVLCC2/$H_s=4$/迎浪 78.4489 kN，
   $Fn=0$ 时 46494.7 N），本单是"把错的声明改成对的 + 加机器门"，不是调参。

## ⑥ 误差判定（逐项，显式回答是否 < 3 %）

| 关键量 | 基准 | 实测误差 | < 3 % ? |
|---|---|---|---|
| W2 二阶律指数 | 2（二阶理论严格值） | 0.00 % | **是** |
| W3 声明 vs 代码（300 点） | `RAW_LAW` 声明式 | 0.0e+00（相对） | **是** |
| W1 $R_{AW}$ 周期敏感性 | 需 > 15 % 极差（谱方法的必要条件） | 实测极差 0.00 % → 缺口 **100 %** | **否**（结构性缺失） |
| W4 零速漂移力比 | [0.8, 1.2] | 0.500（−50 %） | **否** |
| W5 增阻占静水阻力 | 声明带 5–15 % | 带内 3/5；Ferry +17.6 %、Trawler +75.7 % 越界 | **否**（小船族） |
| W6 隐含失速的 $T_p$ 敏感性 | 需 > 15 % | 0.00 % | **否** |
| 绝对精度 vs 模型试验 | — | **WITHHELD** | 无法判：仓内无 $R_{AW}$ 水池数据，留待外部基准入账 |

结论：**"是否 < 3 %"的答案是分层的**——声明/代码一致性与二阶律两条为 0 % 误差（机器裁决通过）；
周期依赖、零速伪影、声明的 5–15 % 带、下游失速敏感度四条**不达标，缺口是结构性的（100 % /
50 %）而不是数值误差**；对模型试验的绝对精度本仓无法裁决，记 **WITHHELD**，不粉饰。
