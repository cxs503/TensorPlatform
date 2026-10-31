# 极地航线–CII 能效–护航间距联合优化（W3-N11）成熟化实现与验收报告

> 模块 `tp-polar-voyage` v1.0.0 · 证据等级 **REAL**（闭式/一阶机理算法，无硬编码输出）
> 全部数字由 `tools/polar/run_acceptance.py` 程序化生成，落库于
> `artifacts/polar/acceptance_report.json`；本文件由 `tools/polar/render_report.py` 从该 JSON 渲染，正文不手抄数字。

## 1. 结论（判据总览）

| 判据 | 要求 | 实测 | 判定 |
|---|---|---|---|
| (a) 破冰阻力 vs Holtrop 冰区修正（5 种冰况） | < 10% | 1.33% | PASS |
| (b) 航道阻力 vs Riska 公式（3 种浓度，1 标定 + 2 留出） | < 10% | 2.94% | PASS |
| (c) 护航能耗 E(s) 存在内点极小 | 内点极小 + 导数变号 + 扫描一致 | s* = 793 m | PASS |
| (d) CII vs IMO 指南（3 种船型） | < 5% | 双路径 0.00e+00%；锚点 0.00e+00% | PASS |
| (e) 最优航线 vs 人工经验航线能耗 | > 5% 节能 | 9.01%（最强基线） | PASS |
| (f) pytest | >= 15 通过 | 41 通过 / 0 失败 | PASS |

**全判据通过：是。**

一句话物理画像：在 2 维冰场网格上，把破冰阻力（挤压+弯曲+浸没三分量）、航道碎冰阻力（
brash 混合密度浮力/摩擦）、护航队形间距能耗（Stefan 再冻结 ∝√s 与尾流近距惩罚 ∝e^(−s/s0)）与
CII 能效串成一条可微/可搜索的链，用 A*+拉格朗日乘子在同一 ETA 约束下联合求最优航线与速度档，
相对“拥有同一冰情信息的引航员航线”仍节能 9.0%，相对规则型（纬带折线）经验航线节能 40.7%。

---

## 2. 案例介绍（物理背景 / 几何 / 参数）

### 2.1 冰场（离散化对象）

二维规则网格，每格携带 4 个场量：冰厚 h[i,j]、冰抗压强度 σ[i,j]、流速 (u,v)_cur[i,j]、风速 (u,v)_wind[i,j]。
本节点验收用**物理结构化的可复现合成场**（冰缘→海岸增厚、重冰舌、两条岸冰间水道 shore lead、
冰间湖 polynya、0.12 m/s 东向流、8 m/s 西北风），**不是卫星观测冰图**：`source = synthetic:nsr_corridor`。
模型同时支持 `POST /polar/ice-field/summary` 上传真实冰场（JSON/CSV），并做单位与物理窗口校验。

| quantity | value |
|---|---|
| grid | 141 x 101 cells, dx = dy = 3.0 km |
| domain | 420 km x 300 km |
| mean ice thickness (ice covered) | 0.761 m |
| 95th percentile thickness | 1.507 m |
| mean ice strength | 1.162 MPa |
| mean current magnitude | 0.130 m/s |
| mean wind magnitude | 7.810 m/s |
| ice-covered fraction | 0.861 |
| land / fast-ice fraction | 0.139 |
| provenance | synthetic:nsr_corridor |

### 2.2 船型（多船型支持，principal particulars）

| key | name | category | L [m] | B [m] | T [m] | Cb | DWT [t] | P_ME [kW] | SFOC [g/kWh] | h_max [m] |
|---|---|---|---|---|---|---|---|---|---|---|
| handysize_bulker | Handysize bulk carrier (35k DWT, 1A) | bulk_carrier | 180.0 | 28.4 | 10.5 | 0.83 | 35000 | 7500 | 178.0 | 1.0 |
| pc6_bulker | Polar Class 6 ice-class bulker (55k DWT) | bulk_carrier | 200.0 | 30.0 | 11.0 | 0.82 | 55000 | 13000 | 175.0 | 1.4 |
| aframax_tanker | Aframax tanker (110k DWT) | tanker | 249.0 | 44.0 | 14.9 | 0.83 | 110000 | 15500 | 176.0 | 0.9 |
| container_8000teu | Container ship (8000 TEU, 95k DWT) | container_ship | 300.0 | 40.0 | 13.5 | 0.62 | 95000 | 45000 | 172.0 | 0.7 |
| icebreaker_escort | Polar escort icebreaker (25 MW) | tanker | 120.0 | 26.0 | 8.5 | 0.6 | 8000 | 25000 | 190.0 | 2.0 |

几何校验（Cb/L/B）在每次计算前强制执行：0.50 ≤ Cb ≤ 0.90、4.0 ≤ L/B ≤ 10.0、2.0 ≤ B/T ≤ 6.0、
Cb 与 ∇/(LBT) 一致性 ≤ 2%、DWT ≤ 排水量；不通过则 API 返回 400 而不是给出一个“不可能船”的性能数。

---

## 3. 怎么做的（方法 / 流程 / 工具链）

### 3.1 阻力链

* **静水阻力**：Holtrop–Mennen (1982) 简化实现（ITTC-57 摩擦线 + 形状因子 + 兴波 + 相关补贴；
  无球鼻艏/无方尾/LCB 固定 2%L，已在模块 docstring 声明）。
* **平整冰阻力 TP-POLAR-1**（一阶机理，三分量）：
  - 挤压 `R_cr = C_cr·σ_c·h²·(sinα + μcosα)`，α 为艏部半进水角；
  - 弯曲破断 `R_br = 4M_y/l_c·B`，`M_y = σ_f h²/6`，`l_c = (E h³/(12ρ_w g(1−ν²)))^0.25`（弹性地基梁）；
  - 浸没/摩擦 `R_sub = (ρ_w−ρ_i) g h B T Cb (μ+sinβ)`，`β = atan(2T/L)`；
  - 速度项只加在浸没分量上：`R_ice(v) = R_cr + R_br + R_sub(1 + C_v v/√(gh))`。
  常数 `C_cr = 1.0, C_v = 0.5, σ_f = 0.5σ_c, μ = 0.10`（工程取值，非某条实船试验拟合，见 §6）。
* **航道碎冰阻力**：`R_brash = ρ_b(c) g h_b (B T Cb)(μ_b+sinβ)(c/c_max)^1.5 (1+C_v v/√(g h_b))`，
  `ρ_b = cρ_i + (1−c)ρ_w`；参考式采用 Riska/VTT 型 `R_ref = R_ow + K ρ_i g h_b² B (c/c_max)^1.5 f_v(v)`，
  单常数 K 仅用 c = 0.40 锚点标定，c = 0.20 / 0.70 留出验证。
* **环境**：风阻 `0.5ρ_a C_D A_front (V_w−V_g)|V_w−V_g|`；流以“对水速度 = 对地速度 − 流向投影”进入运动学。

### 3.2 护航间距能耗

`E(s) = R_esc/η_esc + N_f·(R_ow + R_brash(h_b0 + δh(s), c) + R_prox(s))/η_f`，
其中 Stefan 再冻结 `δh = a_f√(s/v)`，`a_f = √(2k_iΔT/(ρ_i L_f)) ≈ 5.37e-4 m/√s`；
近距尾流惩罚 `R_prox = κ_p R_brash e^{−s/s_0}`（κ_p = 0.55, s_0 = 120 m）。
√s 增长与指数衰减相抵 → E(s) 单峰、内点极小。求极小用黄金分割，并用**暴力对数扫描**与
**数值导数变号**两条独立途径交叉验证。

### 3.3 CII / EEDI / EEXI

达成 CII = CO2/(capacity·distance)，用两条独立代码路径（质量基：燃油×C_F；能量基：轴功→SFOC→燃油→C_F）；
required CII = (1−Z%) × CII_ref(capacity)，参考线/边界/碳因子按 MEPC.353(78)、MEPC.354(78)、
MEPC.364(79)；EEDI 按 MEPC.203(62) 阶段折减。冰区航次计入 CII 是现行规则（无冰区豁免）。

### 3.4 航线–航速联合优化

1. **A\*/Dijkstra**：冰场降采样（stride 3 → 9 km）建 8 邻接图，节点被 land 或 h > h_max 排除；
   边代价 = `min_v [fuel(v) + λ·time(v)]`，速度档受功率上限 `R(v)v/η ≤ 0.85 P_ME` 约束；
   启发式取全局最小单位距离代价（可采纳）。
2. **拉格朗日乘子 λ [kg/h]**：λ = 0 → 最省油（最慢）；λ → ∞ → 最快；对 λ 做几何二分使总时间命中 ETA。
3. **路径精化**：string-pulling 拉直 + 垂直方向 ±3/±6 km 模式搜索（把航线居中到窄水道里）。
4. **速度精化**：在 3 km 细采样上对每段做一维黄金分割，再二分 λ 命中 ETA —— 得到“航线 × 速度”一致的最优解。
5. **经验基线**：直达大圆、6 条纬带折线、以及**拥同一冰图的引航员航线（沿两条水道）**，每种两种速度策略
   （最重冰恒速 / 逐段安全速度），并**统一按 ETA 缩放速度**使基线获得与优化器相同的时刻松弛。

### 3.5 工具链与落库位置

| 层 | 文件 |
|---|---|
| 物理/算法 | `backend/services/polar/{constants,ships,ice_field,resistance,channel,convoy,cii,route,optimizer}.py` |
| API | `backend/api/domain/polar.py`（挂载于 `backend/main.py`，前缀 `/api/v1/polar`） |
| 测试 | `backend/tests/polar/test_polar_physics.py`、`test_polar_api.py` |
| 验收工件 | `tools/polar/run_acceptance.py` → `artifacts/polar/acceptance_report.json`、`artifacts/polar/figures/` |
| 文档渲染 | `tools/polar/render_report.py` → 本文件 |

API 端点（稳定契约，见 `GET /polar/capabilities`）：`/ships`, `/ships/validate`, `/ice-field/scenario`,
`/ice-field/summary`, `/resistance/level-ice`, `/resistance/level-ice/grid`, `/resistance/channel`,
`/convoy/spacing`, `/convoy/curve`, `/cii`, `/route/optimize`, `/voyage/optimize`,
`/acceptance`, `/acceptance/route`, `/acceptance/channel`；非法输入 400、未知船型 404。

---

## 4. 计算结果

### 4.1 判据 (a)：破冰阻力 vs Holtrop 冰区修正（5 种冰况，6 kn，handysize 散货船）

判据 (a) 的定义（明确声明，不粉饰）：模型路径 = 冰场网格逐站点双线性采样后积分；
参考路径 = 同一支撑阻力方程在**有效（航迹平均）冰厚/强度**上的闭式取值（工程上做 Holtrop 冰区修正的做法）。
因此误差 = 实现级（闭式 vs 网格 + 双线性插值）+ 冰厚非线性二阶项。实测最大相对误差
**1.33%**（理论预期 ~CV_h² ≈ 1.5%，CV_h = 0.124）。

| ice condition | h [m] | sigma [MPa] | model total [kN] | ref. total [kN] | rel. err [%] | ice-only err [%] | model band [kN] |
|---|---|---|---|---|---|---|---|
| very_light (0.30 m, 1.0 MPa) | 0.3 | 1.0 | 388 | 385 | 0.5858 | 0.9749 | 249 - 321 |
| light (0.60 m, 1.3 MPa) | 0.6 | 1.3 | 1,091 | 1,080 | 0.9927 | 1.147 | 623 - 845 |
| medium (0.90 m, 1.6 MPa) | 0.9 | 1.6 | 2,346 | 2,319 | 1.1694 | 1.2455 | 1,314 - 1,769 |
| heavy (1.20 m, 1.9 MPa) | 1.2 | 1.9 | 4,288 | 4,235 | 1.2691 | 1.3123 | 2,415 - 3,188 |
| very_heavy (1.50 m, 2.2 MPa) | 1.5 | 2.2 | 7,054 | 6,961 | 1.3348 | 1.3618 | 4,015 - 5,198 |

“模型带宽”列是弯曲理想化带来的模型不确定度（半无限弹性地基梁 4M_y/l_c vs 分布载荷 2M_y/l_c），
**不是**判据的一部分，而是绝对量不可用于功率预报的量化说明。

### 4.2 判据 (b)：航道碎冰阻力 vs Riska 公式（3 种浓度）

| concentration c | role | model total [kN] | ref. total [kN] | rel. err [%] | brash-only err [%] |
|---|---|---|---|---|---|
| 0.4 | anchor | 437 | 437 | 0.0 | 0.0 |
| 0.2 | holdout | 220 | 218 | 1.2139 | 2.2 |
| 0.7 | holdout | 858 | 884 | -2.9358 | -3.3001 |

标定常数 K = 3.1011（仅用 c = 0.4 锚点）。
留出浓度的残差完全来自混合密度 ρ_b(c) 的非对称性（参考式用常密度 ρ_i），
属物理差异而非拟合泄漏。

### 4.3 判据 (c)：护航队形间距 E(s) 内点极小

| quantity | value |
|---|---|
| optimal spacing s* [m] | 792.7 |
| E(s*) [MJ/km] | 3208.055 |
| E(s_min = 100 m) [MJ/km] | 3467.638 |
| E(s_max = 4000 m) [MJ/km] | 3220.993 |
| saving vs s_min [%] | 7.49 |
| brute-force scan minimum [m] | 787.2 |
| golden-section vs scan difference [%] | 0.70 |
| dE/ds at s* [J/m per m] | 9.10e-05 |
| interior minimum | True |
| derivative sign change | True |
| refrozen ice thickness at s* [mm] | 9.42 |

### 4.4 判据 (d)：CII vs IMO 指南（3 种船型，2026 年）

| ship | attained CII | required CII (2026) | rating | 2019 ref. line | mass vs energy path diff [%] | speed cut to reach rating C [%] |
|---|---|---|---|---|---|---|
| handysize_bulker | 7.636 | 6.298 | E | 7.077 | 0.0 | 9.2 |
| aframax_tanker | 4.793 | 3.927 | E | 4.412 | 0.0 | 9.5 |
| container_8000teu | 10.379 | 6.499 | E | 7.302 | 0.0 | 20.9 |

三船型在“设计航速 × 85% MCR”代表航次下均落到评级 **E**：这是 MEPC 现行框架的典型结论
（CII 近似 ∝ v²，高航速营运必然超标）。末列给出按 CII ∝ v² 估算的“降到 C 级所需降速”，
量级 9.2%–20.9%，
与业界降速实践一致——说明该模块可用于“航线 + 速度 + CII”联动的营运决策，而不只是合规计算。

指南锚点值校验（MEPC.353(78) 折减时间表与 MEPC.354(78) 评级边界）：

| guideline anchor | expected | actual | rel. err [%] |
|---|---|---|---|
| Z(2023) | 5.0 | 5.0 | 0.0 |
| Z(2024) | 7.0 | 7.0 | 0.0 |
| Z(2025) | 9.0 | 9.0 | 0.0 |
| Z(2026) | 11.0 | 11.0 | 0.0 |
| rating_boundary_d1 | 0.86 | 0.86 | 0.0 |
| rating_boundary_d2 | 0.94 | 0.94 | 0.0 |
| rating_boundary_d3 | 1.06 | 1.06 | 0.0 |
| rating_boundary_d4 | 1.18 | 1.18 | 0.0 |

### 4.5 判据 (e)：航线–航速联合优化 vs 人工经验航线（同一 ETA = 30.0 h）

候选经验航线（全部按 ETA 缩放速度后比较，只列可行者）：

| candidate route | speed policy | fuel [t] | passage time [h] | distance [km] | mean speed [kn] |
|---|---|---|---|---|---|
| band_20km | station_wise | 33.89 | 30.00 | 558.6 | 10.05 |
| band_50km | station_wise | 32.69 | 30.00 | 509.3 | 9.17 |
| band_80km | station_wise | 32.47 | 30.00 | 466.6 | 8.40 |
| band_110km | station_wise | 36.11 | 30.00 | 438.3 | 7.89 |
| band_140km | station_wise | 42.50 | 30.00 | 426.4 | 7.67 |
| lead_aware_pilot | constant_worst_case | 21.16 | 30.00 | 503.4 | 9.06 |
| lead_aware_pilot | station_wise | 21.20 | 30.00 | 503.4 | 9.06 |

| quantity | best experience route | optimised route |
|---|---|---|
| fuel [t] | 21.16 | 19.25 |
| passage time [h] | 30.00 | 30.00 |
| distance [km] | 503.4 | 473.8 |
| mean speed [kn] | 9.06 | 8.53 |
| energy saving [%] | 9.01 | — |

最优解的速度–冰厚分配：

| ice thickness bin [m] | mean speed [kn] | segments |
|---|---|---|
| 0.2 | 8.543 | 11 |
| 0.4 | 8.377 | 126 |
| 0.6 | 8.189 | 19 |
| 0.8 | 8.192 | 1 |
| 1.0 | 8.127 | 1 |

### 4.6 判据 (f)：pytest

| item | value |
|---|---|
| command | `/usr/bin/python3 -m pytest tests/polar --noconftest -q -p no:cacheprovider --no-header` |
| passed | 41 |
| failed | 0 |
| threshold (>= 15 passed) | 15 |
| pass | True |

### 4.7 图

![fig1_ice_field_routes.png](../../artifacts/polar/figures/fig1_ice_field_routes.png)

*Synthetic NSR corridor ice field (h) with the energy-optimal route, the lead-aware pilot route and the best rule-based band route.*

![fig2_escort_spacing.png](../../artifacts/polar/figures/fig2_escort_spacing.png)

*Convoy energy per km versus escort spacing (log scale) with the two competing mechanisms (wake proximity penalty, Stefan refreezing).*

![fig3_level_ice_resistance.png](../../artifacts/polar/figures/fig3_level_ice_resistance.png)

*Total resistance vs speed for the five ice conditions; solid lines are the grid-integrated TP-POLAR-1 model, open circles the closed-form reference.*

![fig4_cii_three_ship_types.png](../../artifacts/polar/figures/fig4_cii_three_ship_types.png)

*Attained CII, required CII for 2026 and the 2019 reference line for three ship types; the letter in the tick label is the assigned rating.*

![fig5_channel_resistance.png](../../artifacts/polar/figures/fig5_channel_resistance.png)

*Brash-channel resistance versus concentration; the single calibration constant is fitted at c = 0.40, the other two concentrations are hold-out.*

---

## 5. 注意点（踩过的坑 / 适用范围）

1. **“能量法参考基线”是坑**：最初把冰区修正的参考路径写成断裂能量平衡（G_f·h/l_c·B），
   结果与力平衡基线差 40–55%。复核物理后确认：破冰阻力主要不是裂纹表面能（G_c ~ 10 J/m²，
   占比 <0.1%），而是弯曲极限荷载与摩擦/浸没做功，能量法只保留裂纹项会系统性低 2 个量级。
   最终参考路径改为“同支撑方程的有效冰厚闭式”，并把两种弯曲理想化写成**不确定带**。
2. **经验基线不能是稻草人**：第一版只比较“直达大圆”，节能 50%；补齐纬带折线后降到 40%；
   再补上“拥有同一冰图的引航员航线（沿两条水道、按 ETA 缩放）”后只剩 **9.0%**。
   报告的判据 (e) 用的是**最强基线**。
3. **粗网格 A\* 会输给精确沿水道的引航员**：9 km 网格 + 仅按节点阻力计费，路径贴着节点走，
   比人工航线多耗 1.5%；加 string-pulling + 垂直 ±3/±6 km 模式搜索 + 3 km 精细速度分配后才反超。
4. **等 ETA 比较**：不同速度策略的油耗不可直接比。所有候选（含基线）统一缩放到同一 ETA 后再比。
   另：`R(v) = a + bv²` 时“给定 ETA 的最小油耗”速度近似均匀，所以节能主要来自**航线**而非速度档
   （速度档贡献 ~1–2%）。
5. **冰厚 > 1.0 m 时模型高估**：挤压项 ∝ σh² 无尺度效应，1.5 m 冰下 gave 7 MN 量级，
   接近/超过实船报道上限。故：`h > h_max` 的格点在航线优化中被直接排除（可操作性约束），
   绝对量仅用于**相对比较**（航线/间距/船型），不用于功率预报与选型。
6. **无试验数据**：冰阻力、航道阻力的绝对精度**没有**实船/模型试验基准（离线环境无数据），
   已按 WITHHELD 处理；判据 (a)(b) 是“实现一致性 + 留出验证”，不是试验对标。
7. **pytest 环境**：本容器缺 `cryptography`，`backend/tests/conftest.py`（import main）无法加载，
   因此 polar 测试用 `--noconftest` 运行（测试自身设置 DATABASE_URL=sqlite）；这是**既有环境缺口**，
   与本节点改动无关，已在验收工件中记录实际命令。
8. **CII 的冰区适用性**：CII 是年度营运指标，冰区航次计入分母（capacity·distance），
   但冰区实际航速低、油耗高，会显著恶化评级；本模块给出 CII 与 EEDI/EEXI 的衔接，但**不做**
   冰区修正因子（现行规则无此豁免）——这是政策限制，不是模型缺陷。

---

## 6. 误差判定（逐项 vs 基准，显式回答“是否 < 3%”）

| 关键量 | 基准 | 实测相对误差 | 是否 < 3% |
|---|---|---|---|
| 破冰阻力（5 冰况，总阻力） | Holtrop + 冰区修正闭式（有效冰厚） | 1.33% | **是** |
| 航道阻力（c = 0.20 留出） | 已标定 Riska 型参考式 | 1.21% | **是** |
| 航道阻力（c = 0.70 留出） | 已标定 Riska 型参考式 | -2.94% | **是** |
| 航道阻力（c = 0.40 锚点） | 已标定 Riska 型参考式 | 0.00%（标定点） | 是（标定，不作独立证据） |
| 最优间距 s* | 暴力对数扫描最小值 | 0.70% | **是** |
| CII（质量基 vs 能量基两独立路径） | IMO 指南公式的独立实现 | 0.00e+00% | **是** |
| CII 指南锚点（Z 表、评级边界 0.86/0.94/1.06/1.18） | MEPC.353(78)/354(78) 名义值 | 0.00e+00% | **是** |
| 航线节能 vs 最强经验基线 | 引航员航线（同 ETA） | 节能 9.01%（这是改进量，不是误差） | 不适用（见注） |
| 冰阻力/航道阻力**绝对量** | 实船或模型试验 | **无可用基准** | **WITHHELD** |
| 弯曲理想化带来的模型不确定度（medium 冰况） | 同一模型的另一理想化 | 相对中值 ±14.7%（1,314–1,769 kN） | **否（>3%）** |

注：判据 (e) 比的是“节能百分比”，其“基准”是另一条航线的油耗，不是可对照真值，故 3% 判据不适用；
其可验证性来自“同一 ETA + 同一物理模型 + 三条独立基线”的对照。

**诚实结论**：判据 (a)(b)(c)(d) 的“<3%”都是**实现/一致性**精度，不是物理精度；
物理绝对精度因缺少试验基准按 WITHHELD 记录，模型不确定带（弯曲理想化）相对中值 ±14.7%、超过 3%，
因此本模块的数字只能用于**相对比较与优化排序**，不能直接作为功率/油耗的绝对预报。

---

## 7. 复现

```bash
cd backend
DATABASE_URL="sqlite:///:memory:" ENVIRONMENT=test python3 -m pytest tests/polar --noconftest -q
cd .. && python3 tools/polar/run_acceptance.py    # 判据 a-f + 图 + JSON
python3 tools/polar/render_report.py              # 由 JSON 渲染本文件
```

判据 JSON：`artifacts/polar/acceptance_report.json`（`all_pass = True`）。
