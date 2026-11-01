# 稳性端点输入欠定性审计：单一 payload 下的船体族 GZ 精确解 vs 平台单值回答

> 验证欠账清偿 · 承接报告 #1150（m1565 判 calc.stability FAIL）· 席位 tp-ver-8 / mission 1588

> 工件：`artifacts/verifier/stability_indeterminacy/{evidence,comparison,api_captures}.json` + 3 图
> 工具：`tools/verifier/stability_indeterminacy.py`、`tools/verifier/make_stability_indeterminacy_figures.py`
> 测试：`tools/verifier/tests/test_stability_indeterminacy.py`（17 passed）

## ① 案例介绍

被复核能力 `calc.stability`（`audit_capabilities` 标 REAL）。#1150 已用矩形浮箱证明它在**唯一可定船体**（Cb=1 箱体）上超 15% 不可接受线；但箱体只覆盖 Cb=1 一个点，结论无法外推到常规货船（Cb 0.6–0.85）。本单补的正是 #1150 自陈的残留缺口（其 ⑤注意点第一行）：**当 Cb<1 时端点是否仍可判定？**

端点入参只有 `length/beam/draft/depth/displacement/cb/kg`——**没有任何横剖面形状信息**。因此凡在入参空间内不可分辨的船体，端点只能给出同一组数字。审计做法：构造一个**同 (L,B,T,Cb,KG) 但剖面形状 / 纵向缩放不同的船体族**，用精确剖面积分求各自真解，再与端点唯一回答逐一对照。这不是标定误差问题，而是信息缺失下界问题。

船体族定义（全显式）：`y(x,z)=s(x)·(B/2)·f(min(z/T,1))`，`c_s=∫₀¹f dζ`，`Cb=mean_x(s)·c_s`；水线以上舷侧垂直延伸到甲板 z=D（封闭船体约定）。四种剖面 `box(c_s=1)`、`trapezoid(3/4)`、`knuckle(3/4，同 c_s 但形心更低)`、`vee(1/2)`，纵向缩放 `prismatic`/`cosine-taper`。

基准（参考链三路互证）：

- R1 壁侧闭式解 `GZ=sinφ(GM+½BM tan²φ)`（Biran §5.4）；
- R2 剖面积分 + Sutherland–Hodgman 精确裁剪 + 鞋带公式求形心，逐角度二分求浮态（体积守恒）；
- R3 同一剖面加密共线节点复算，验证裁剪/形心代码路径（T4 测试）。

R1↔R2 在甲板边缘角 21.80° 以下 4 个角度逐点相符，最大相对偏差 **3.01e-12**；同一路线在 Cb=1 箱体上复现 #1150 参考值 KB=4.0000 m / BM=4.1667 m / GM=1.1667 m，说明参考链与既有审计同源同值。

## ② 怎么做的

1. 对每个 payload（L=100, B=20, T=8, D=12，Δ=ρCbLBT，指定 Cb/KG）枚举所有**可行**族成员（要求 `s(x)≤1`，即剖面面积系数须 ≥Cb）；
2. 每个成员：二分求浮态 → 得 KB、水线面惯性矩 → BM、GM，0–40° 每 1° 的 GZ，Simpson 求面积，套 IMO IS Code 2008 Res.MSC.267(85) Part A §2.2.1 七项阈值；
3. 端点黑盒调用（8001）取同一 payload 的回答，与每个成员逐一算相对误差；
4. 计算**极小极大下界**：任何「形状盲」单值回答 q̂ 在族上的最坏相对误差 `min_q̂ max_i |q̂−q_i|/|q_i|`，在 20001 点网格上搜最优 q̂。该下界与实现质量无关，是入参信息缺失决定的硬底。

## ③ 计算结果

### A_cb050_kg45 — Cb=0.50, KG=4.5 m, Δ=8200 t（all members stable; family spans GM 0.5..9.2 m on one payload）

| 船体 | c_s | s̄ | KB [m] | BM [m] | GM [m] | GZ_max [m] | Area 0–40 [m·rad] | IS Code 2008 |
|---|---|---|---|---|---|---|---|---|
| box/prismatic | 1.00 | 0.500 | 4.000 | 1.042 | 0.542 | 0.582 | 0.1639 | PASS |
| trapezoid/prismatic | 0.75 | 0.667 | 4.444 | 2.469 | 2.414 | 1.562 | 0.5536 | PASS |
| knuckle/prismatic | 0.75 | 0.667 | 4.889 | 2.469 | 2.858 | 2.162 | 0.7445 | PASS |
| vee/prismatic | 0.50 | 1.000 | 5.333 | 8.333 | 9.167 | 3.917 | 1.6575 | PASS |
| box/cosine-taper | 1.00 | 1.000 | 4.000 | 2.604 | 2.104 | 1.533 | 0.5453 | PASS |

族内跨度：GM 0.542–9.167 m，GZ_max 0.582–3.917 m，Area0–40 0.1639–1.6575 m·rad；IS Code 判定 5 通过 / 0 不通过。
极小极大下界（任何形状盲单值回答的最坏相对误差下界）：gm_m 88.9%，gz_max_m 74.1%，area_0_40_mrad 82.0%。

### B_cb070_kg60 — Cb=0.70, KG=6.0 m, Δ=11480 t（realistic cargo-ship block coefficient; 3 feasible sections）

| 船体 | c_s | s̄ | KB [m] | BM [m] | GM [m] | GZ_max [m] | Area 0–40 [m·rad] | IS Code 2008 |
|---|---|---|---|---|---|---|---|---|
| box/prismatic | 1.00 | 0.700 | 4.000 | 2.042 | 0.042 | 0.316 | 0.0725 | FAIL |
| trapezoid/prismatic | 0.75 | 0.933 | 4.444 | 4.840 | 3.284 | 1.517 | 0.6471 | PASS |
| knuckle/prismatic | 0.75 | 0.933 | 4.889 | 4.840 | 3.728 | 2.211 | 0.9155 | PASS |

族内跨度：GM 0.042–3.728 m，GZ_max 0.316–2.211 m，Area0–40 0.0725–0.9155 m·rad；IS Code 判定 2 通过 / 1 不通过。

端点回答（同一 payload）：KB=6.133 m, BM=5.093 m, GM=5.226 m, GZ_max=4.036 m, Area0–40=1.3507 m·rad，七项 IMO 全判通过。

| 船体 | GM 真解 | 平台 GM | GM 相对误差 | Area0–40 误差 | 判定是否翻转 |
|---|---|---|---|---|---|
| box/prismatic | 0.042 | 5.226 | +12442.4% | +1763.8% | 是（真解 FAIL，平台 PASS） |
| trapezoid/prismatic | 3.284 | 5.226 | +59.1% | +108.7% | 否 |
| knuckle/prismatic | 3.728 | 5.226 | +40.2% | +47.5% | 否 |

**平台回答是否落在族真解区间之外**：GM=True，GZ_max=True，Area0–40=True。

极小极大下界（任何形状盲单值回答的最坏相对误差下界）：gm_m 97.8%，gz_max_m 75.0%，area_0_40_mrad 85.3%。

### C_cb100_kg70 — Cb=1.00, KG=7.0 m, Δ=16400 t（fully determined box barge = reference case of audit #1150）

| 船体 | c_s | s̄ | KB [m] | BM [m] | GM [m] | GZ_max [m] | Area 0–40 [m·rad] | IS Code 2008 |
|---|---|---|---|---|---|---|---|---|
| box/prismatic | 1.00 | 1.000 | 4.000 | 4.167 | 1.167 | 0.750 | 0.3194 | PASS |

族内跨度：GM 1.167–1.167 m，GZ_max 0.750–0.750 m，Area0–40 0.3194–0.3194 m·rad；IS Code 判定 1 通过 / 0 不通过。

端点回答（同一 payload）：KB=5.333 m, BM=4.167 m, GM=2.500 m, GZ_max=2.160 m, Area0–40=0.6898 m·rad，七项 IMO 全判通过。

| 船体 | GM 真解 | 平台 GM | GM 相对误差 | Area0–40 误差 | 判定是否翻转 |
|---|---|---|---|---|---|
| box/prismatic | 1.167 | 2.500 | +114.3% | +116.0% | 否 |

**平台回答是否落在族真解区间之外**：GM=True，GZ_max=True，Area0–40=True。

极小极大下界（任何形状盲单值回答的最坏相对误差下界）：gm_m 0.0%，gz_max_m 0.0%，area_0_40_mrad 0.0%。

## ④ 结果图

- `fig1_sections_same_payload.png`：同一 payload（Cb=0.70）下四种剖面形状（c_s 与 KB 标注），
  直观显示"入参完全相同、船体完全不同"；
- `fig2_gz_curves.png`：三个可行族的 GZ 曲线 + 平台 gz_max 标量与 IS Code 0.20 m 阈值线；
- `fig3_platform_vs_family.png`：GM / GZ_max / Area0–40 三个量，族真解柱状 vs 平台单值虚线，
  标题标注各自极小极大下界。

## ⑤ 注意点（局限与适用范围）

1. **族是参数化船体，不是真实船舶**：box/trapezoid/knuckle/vee 四类剖面族覆盖 Cb 可表达范围，
   但未用实船 offsets 复算；真实船体（球鼻艏、方尾、舭部圆弧）误差需另做。
2. **可行性约束**：只有 `c_s ≥ Cb` 的剖面才能在该 Cb 下造出（`s≤1`），故 Cb=0.70 只有 3 个成员，
   Cb=1 时只有箱体 1 个成员（此时端点被完全确定，见 C 工况）。
3. **水线以上舷侧取垂直延伸至甲板**，未建模舷弧/外飘/甲板淹湿后的进水（封闭船体约定），
   40° 附近甲板边缘入水后 GZ 为虚拟值；这不影响"同载荷下族内差异"的结论。
4. **纵向缩放只取 prismatic / cosine-taper 两种**，未覆盖球鼻艏/方尾；
   纵向分布对 BM 的影响已可见（box/prismatic BM=1.042 vs box/cosine-taper BM=2.604，同 payload）。
5. **端点实现不在本单白名单**（运行时仓 `/srv/tensor/ship-workspace/`），
   本单只交证据、下界与修复规格，不改该仓。
6. 端点回答对 Cb 并非完全不敏感：KB 与 `T(1−Cb/3)` 逐位相等（Cb=0.70→6.133 m，Cb=1.00→5.333 m，
   均与实测一致），正是 #1150 缺陷 D6——该式对应"三角形剖面"船体，却与自身 BM 假设不自洽，
   也不随剖面形状变化，因此仍无法分辨族成员。

## ⑥ 误差判定（关键量 vs 基准，逐项回答是否 <3%）

基准=本单 R1/R2/R3 互证的精确解；参考链自身互证最大相对偏差 1.1e-16（远优于 3%，基准可信）。

| 工况 | 量 | 平台 vs 真解相对误差范围 | 是否 <3% |
|---|---|---|---|
| C（Cb=1.00，唯一确定） | GM | +114.3% | 否 |
| C（Cb=1.00，唯一确定） | Area0–40 | +116.0% | 否 |
| B（Cb=0.70，三成员） | GM | +40.2% … +12442.4%（平台值高于族上界 3.728 m） | 否 |
| B（Cb=0.70，三成员） | Area0–40 | +47.5% … +1763.8% | 否 |

结论：**不满足 3%，也全部越过 15% 不可接受线**；且因入参不含剖面信息，
即便把公式调准，任何形状盲回答在 Cb=0.70 族上的最坏相对误差下界仍为 GM 97.8% / GZ_max 75.0% /
Area0–40 85.3%——**该端点在此入参下不存在满足 15% 的可行解**（honest FAIL，非标定可修）。

修复规格（供修复单，runner=native）：
(a) 入参增补 `kb`/`cw` 或剖面 offsets，或直接改收水线面系数+剖面面积系数；
(b) KB 改为 `T·(∫ζ c_s(ζ)dζ)/c_s`，BM 用 `I_wp/∇` 且 ∇=Cb·L·B·T；
(c) IMO 阈值改为 IS Code 2008 §2.2.1：area0–30≥0.055、area30–40≥0.030、area0–40≥**0.090**、
    GZ@30°≥0.20、GZ_max≥0.20 且 φ(GZ_max)≥25°、GM0≥0.15；
(d) 消失角不得硬编码 60°，须由 GZ 曲线首个零点解出，并与 A≥55° 判据一致。
