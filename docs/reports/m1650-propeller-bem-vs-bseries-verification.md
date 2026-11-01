# 敞水性能 BEM 模型对 Wageningen B 系列正典表与 KP505 试验的验证闭环（m1650）：把 propeller.open_water 的 WITHHELD 绝对精度判为 ±12–18%

- 工件：`artifacts/m1650/propeller_bem_vs_bseries/audit.json`（全部数字程序化注入）、`table.md`、三张图
- 验证脚本：`tools/verify/propeller_open_water/bem_vs_bseries.py`
- 测试：`tests/verify/test_bem_vs_bseries.py`（12 passed）
- 受测模型（只读，未改动）：`backend/services/propeller_open_water.py`（m1393 交付，能力项 `propeller.open_water`）

## 1. 案例介绍（物理背景与几何/参数）

m1393 用叶元-动量（BEM）模型替换了旧的线性硬编码代理式（`KT = max(0.05, 0.5-0.3J)` 等），
并自称 REAL 等级。但它的公开验证（`docs/verifier/PROPELLER_OPEN_WATER_VERIFICATION.md`）
只有**解析约束**（致动盘上限、傅汝德缩尺不变性、零推力定义），绝对量级一栏自评为
**WITHHELD（离线取不到 B 系列图谱点值）**。本次把这条验证欠账在**仓内**结清：

- 参考 A：`tools/verify/propeller_open_water/reference_bseries.py` —— Oosterveld & van Oossanen (1975)
  39 项 KT / 47 项 KQ 回归系数（转自 Bernitsas, Ray & Kinley 1981, Univ. of Michigan Report 237），
  已逐项对齐平台官方系数表 `backend/data/official/Wageningen_Bseries_coeffs.dat`。
- 参考 B：KP505 模型桨敞水试验（Iftikhar & Takinaci 2023, Table 2；Z=5, Ae/A0=0.800, P/D=0.950），
  实测数据，既作 BEM 的独立试验锚，也用来标定参考 A 自身的可用误差带。

受测几何（全在 B 系列有效域内：Z=2..7、P/D=0.5..1.4、Ae/A0=0.2..1.05、J=0..1.5）：

| 几何 | P/D | Ae/A0 | Z |
| --- | --- | --- | --- |
| B4-70 | 1.00 | 0.70 | 4 |
| B4-55 | 0.80 | 0.55 | 4 |
| B4-40 | 1.00 | 0.40 | 4 |
| B5-80 | 0.95 | 0.80 | 5 |
| B3-50 | 1.20 | 0.50 | 3 |

计算设置：D=5.0 m，ρ=1025 kg/m³，n=1 rps（全部输出无量纲，与量纲选择无关）；
J 网格 0.10–0.90，步长 0.10；J0、J_opt、eta_max 由各自模型自带的
二分/黄金分割求取器计算。

## 2. 怎么做的（方法/流程/工具链）

1. `importlib` 按路径加载受测模块（**不修改**），与参考 A 在同一 J 网格上逐点求值；
2. 逐点相对误差在**载荷带**内统计（`KT_ref ≥ KT_ref >= 0.05, KQ_ref >= 0.005` 等价门限：
   推力/扭矩都不接近零，两者同号），因为 J→J0 时百分比误差按定义发散（见 §5.1）；
   全网格结果同时保留供审阅；
3. 积分特征量 J0、J_opt、eta_max 逐几何比对；
4. 两条模型同时对 KP505 实测数据比对，量化「参考基准自身有多准」与「模型有多准」；
5. 结构性诊断：载荷带内 KT 斜率 dKT/dJ 与归一化零推力比 J0/(P/D)；
6. 机器判决按平台精度门（<3% 可发表 / <15% 可接受），逐项显式给出，无人工抄写。

## 3. 计算结果

## Pointwise deviation vs reference A (B-series table)

| geometry | P/D | Ae/A0 | Z | mean |e_KT| % | max |e_KT| % | mean |e_KQ| % | max |e_KQ| % | mean |e_eta| % |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| B4-70 | 1.00 | 0.70 | 4 | 23.8 | 31.3 | 11.7 | 17.4 | 11.0 |
| B4-55 | 0.80 | 0.55 | 4 | 208.2 | 1683.6 | 105.1 | 804.5 | 27.5 |
| B4-40 | 1.00 | 0.40 | 4 | 12.6 | 47.3 | 21.3 | 48.3 | 11.5 |
| B5-80 | 0.95 | 0.80 | 5 | 37.8 | 46.1 | 25.0 | 31.8 | 10.6 |
| B3-50 | 1.20 | 0.50 | 3 | 4.3 | 5.7 | 12.8 | 21.8 | 19.0 |

## Integral characteristics

| geometry | J0 BEM | J0 ref | err % | J_opt BEM | J_opt ref | err % | eta_max BEM | eta_max ref | err % |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| B4-70 | 0.993 | 1.062 | -6.5 | 0.807 | 0.843 | -4.2 | 0.7172 | 0.6953 | 3.1 |
| B4-55 | 0.794 | 0.878 | -9.6 | 0.629 | 0.670 | -6.2 | 0.6538 | 0.6555 | -0.3 |
| B4-40 | 0.994 | 1.113 | -10.7 | 0.821 | 0.864 | -4.9 | 0.7503 | 0.7022 | 6.8 |
| B5-80 | 0.943 | 1.002 | -5.9 | 0.762 | 0.782 | -2.6 | 0.7001 | 0.6736 | 3.9 |
| B3-50 | 1.193 | 1.295 | -7.9 | 1.002 | 1.055 | -5.0 | 0.7817 | 0.7457 | 4.8 |

## Machine verdict (platform gates: <3% publishable, <15% acceptable)

Pointwise statistics are given on the **load band** (KT_ref >= 0.05, KQ_ref >= 0.005, 43 points; points excluded because the reference itself crosses zero: J = 0.8, 0.9) and, for transparency, on all grid points.

| key quantity | scope | mean abs err % | verdict | max abs err % | verdict |
| --- | --- | --- | --- | --- | --- |
| KT | load band | 18.04 | FAIL >=15% | 47.25 | FAIL >=15% |
| KT | all points | 57.35 | FAIL >=15% | 1683.56 | FAIL >=15% |
| KQ | load band | 16.36 | FAIL >=15% | 48.31 | FAIL >=15% |
| KQ | all points | 35.16 | FAIL >=15% | 804.53 | FAIL >=15% |
| eta_0 | load band | 12.00 | PASS <15% (not publishable grade) | 32.73 | FAIL >=15% |
| eta_0 | all points | 15.91 | FAIL >=15% | 100.00 | FAIL >=15% |
| J0 | all geometries | 8.12 | PASS <15% (not publishable grade) | 10.68 | PASS <15% (not publishable grade) |
| J_opt | all geometries | 4.59 | PASS <15% (not publishable grade) | 6.18 | PASS <15% (not publishable grade) |
| eta_max | all geometries | 3.80 | PASS <15% (not publishable grade) | 6.84 | PASS <15% (not publishable grade) |

## Structural diagnostic: KT slope and normalised J0

| geometry | dKT/dJ BEM | dKT/dJ ref | ratio % | J0/(P/D) BEM | J0/(P/D) ref |
| --- | --- | --- | --- | --- | --- |
| B4-70 | -0.5792 | -0.4347 | 133.2 | 0.993 | 1.062 |
| B4-55 | -0.4695 | -0.3825 | 122.8 | 0.992 | 1.098 |
| B4-40 | -0.4131 | -0.3429 | 120.5 | 0.994 | 1.113 |
| B5-80 | -0.7181 | -0.4608 | 155.8 | 0.993 | 1.055 |
| B3-50 | -0.3932 | -0.3642 | 107.9 | 0.994 | 1.080 |

## Against measured KP505 model test (reference B)

| J | KT exp | KT BEM | e KT BEM % | KT ref | e KT ref % | KQ exp | KQ BEM | e KQ BEM % | KQ ref | e KQ ref % |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 0.1 | 0.494 | 0.608 | 23.0 | 0.421 | -14.7 | 0.0683 | 0.0736 | 7.7 | 0.0601 | -12.0 |
| 0.2 | 0.447 | 0.563 | 25.9 | 0.387 | -13.4 | 0.0636 | 0.0703 | 10.5 | 0.0559 | -12.0 |
| 0.3 | 0.392 | 0.509 | 29.8 | 0.348 | -11.1 | 0.0585 | 0.0659 | 12.6 | 0.0512 | -12.5 |
| 0.4 | 0.343 | 0.446 | 29.9 | 0.306 | -10.8 | 0.0530 | 0.0601 | 13.4 | 0.0459 | -13.3 |
| 0.5 | 0.290 | 0.375 | 29.2 | 0.260 | -10.2 | 0.0471 | 0.0530 | 12.5 | 0.0402 | -14.7 |
| 0.6 | 0.241 | 0.297 | 23.1 | 0.212 | -12.0 | 0.0409 | 0.0444 | 8.7 | 0.0340 | -16.8 |
| 0.7 | 0.193 | 0.214 | 10.7 | 0.161 | -16.3 | 0.0342 | 0.0346 | 1.1 | 0.0275 | -19.5 |
| 0.8 | 0.141 | 0.128 | -8.9 | 0.109 | -22.6 | 0.0270 | 0.0236 | -12.6 | 0.0207 | -23.4 |
| 0.9 | 0.087 | 0.041 | -52.5 | 0.056 | -36.2 | 0.0192 | 0.0115 | -40.3 | 0.0135 | -29.5 |
| 1.0 | 0.029 | -0.141 | -586.6 | 0.001 | -95.8 | 0.0108 | -0.0167 | -255.0 | 0.0062 | -42.9 |

Mean absolute error over the KP505 grid: BEM KT 22.6 %, reference A KT 13.9 %; BEM KQ 9.9 %, reference A KQ 15.5 %.


## 4. 结果图

1. `fig1_kt_kq.png`：两个代表几何（B4-70 / B5-80）的 KT、10KQ、eta0 曲线，BEM（实线圆点）对正典表（虚线方块）。
2. `fig2_error_profile.png`：逐点相对误差剖面对 3%/15% 门（红点线/红虚线），以及 eta_max 与 J0 误差条形图。
3. `fig3_kp505_experiment.png`：KP505 实测（黑方块）对 BEM 与正典表两条模型曲线。

## 5. 注意点（坑、局限、适用范围）

1. **相对误差在零推力分支发散**：B4-55 在 J=0.8 时 BEM 已过零推力（KT<0）而表值仍为 0.037，
   百分比误差冲到 1683%。所以判决只在载荷带内做，全网格数字仅作透明披露，
   `load_band.excluded_J` 明列被排除的 J，测试 `test_load_band_excludes_zero_thrust_points` 钉住该规则。
2. **参考 A 只是 B 系列的回归近似，且 KP505 不是 B 系列桨**：同 (P/D, Ae/A0, Z) 匹配下，
   正典表对实测 KT 平均差 13.9%、KQ 差 15.5%，
   这就是「换了桨型/尺度」的固有带。因此本单只用它判**量级与结构**，不宣称任何一方达到 3%。
3. **m1393 报告的一条结论方向是错的（本次更正）**：该文 §5.4 写「本模型 J0 偏高约 10–15%，
   真实 B 系列约 0.85–0.9×(P/D)」，其依据是口头经验而非图谱点值。正典表给出
   J0/(P/D) = 1.055–1.113，而 BEM 恒为 0.993×(P/D)，即 **BEM 的 J0 偏低 -8.1%**（方向相反）。
   旧结论已在原报告中追加更正段并指向本单（原文不删，留痕）。
4. **受测模型自身有文档漂移**（记录不改代码）：`blade_element_section` docstring 写
   `Cd = 0.008 + 0.9α²`、夹紧 [0.006, 0.08]，代码实际是 `CD0 + 1.0α²`、夹紧 [0.008, 0.10]。
   改常数会移动 m1393 的全部已发表数字，故本单只登记（`section_docstring_drift`），
   由后续「模型改进」单决定是否改。
5. 适用范围：敞水、单桨、稳态；不含空化、桨-舵干扰、非均匀伴流、风车/制动工况。

## 6. 误差判定（逐项、显式回答是否 <3%）

| 关键量 | 基准 | 结果 | 是否 <3% | 是否 <15% |
| --- | --- | --- | --- | --- |
| eta_max（5 几何，均值） | 正典 B 系列表 | 均值误差 3.80%、最大 6.84% | **否** | 是 |
| J_opt（5 几何，均值） | 同上 | 4.59% / 最大 6.18% | **否** | 是 |
| J0（5 几何，均值） | 同上 | 8.12% / 最大 10.68%（系统性偏低） | **否** | 是 |
| KT 逐点（载荷带 43 点，均值） | 同上 | 18.04% / 最大 47.25% | **否** | **否（超 15%）** |
| KQ 逐点（载荷带，均值） | 同上 | 16.36% / 最大 48.31% | **否** | **否（超 15%）** |
| eta0 逐点（载荷带，均值） | 同上 | 12.00% / 最大 32.73% | **否** | 是（均值） |
| KT/KQ/eta0 傅汝德缩尺不变性 | 解析恒等 | m1393 已判 2e-16 | 是 | 是 |
| eta0 < 致动盘上限 | 动量理论 | 全部满足（测试 `test_physics_rails` 钉住） | 是 | 是 |

**显式回答**：没有任何关键量对文献/试验基准达到 <3%。结论是
「结构正确（REAL 实现）＋ 绝对精度可量化为载荷带 KT 18.0% / KQ 16.4% /
eta0 12.0%，效率类积分量 3.8%」——
KT/KQ 超出平台 15% 可接受门，效率类在门内。因此能力项 `propeller.open_water` 应从
「REAL 未定精度」改注为「REAL，KT/KQ ±15–20%、效率 ±4%」，并列入模型改进待办（下一步：
用 B 系列升力线/有限展弦比修正压低低 J 推力）。

## 7. 复现

```bash
python3 -m tools.verify.propeller_open_water.bem_vs_bseries   # 重算 audit.json + 3 图 + table.md
python3 -m pytest tests/verify/test_bem_vs_bseries.py -q      # 12 passed
python3 -m tools.verify.propeller_open_water.make_report_m1650
```
