# 试点台账 2：推进性能验证（propulsion-domain verification pilot）

> 席位：verifier（tp-ver-7 自找活）｜对审对象：`propeller.open_water`、`calc.propulsion`
> 本文件由 `tools/verifier/render_propulsion_doc.py` 程序化渲染，
> 数据源 `artifacts/verifier/propulsion/platform_capture.json`（2026-11-01T07:59:41+00:00 抓取）
> 与 `propulsion_verification_verdict.json`。**文中数字零手抄。**

## ① 案例介绍

**物理背景。** 螺旋桨敞水性能是船舶总体性能推进域的根节点量：推力系数 $K_T$、扭矩系数
$K_Q$、敞水效率 $\eta_0=JK_T/(2\pi K_Q)$ 直接决定有效功率 $P_E$、交付功率 $P_D=2\pi nQ$、
准推进系数 $\eta_D=\eta_0\eta_H\eta_R$。若 $\eta_0$ 失真，全船功率-航速曲线、主机选型、
EEDI 校核全部连带失真，因此这是"验证欠账"里风险最高的一项。

**对审能力（均已合并、审计清单标注 REAL）。**

| 能力键 | 端点 | 声明方法字符串 |
|---|---|---|
| `propeller.open_water` | `POST /api/v1/propeller-design/wageningen-b` | `Wageningen B系列回归公式` |
| `calc.propulsion` | `POST /api/v1/calculations/propulsion` | `螺旋桨理论 + 经验公式` |

**计算域设置（敞水扫掠）。** $Z=4$，$A_E/A_O=0.7$，
螺距比 $P/D\in\{0.8, 1.0, 1.2\}$，
进速系数 $J=0.00$–$1.00$ 步长 0.05（共 63 个工况点）；另有
7 个几何敏感性探针 × 3 个 $J$ 位。

**端到端工况（210k DWT 散货船，服务航速）。**

| 参数 | 值 |
|---|---|
| $L_{pp}$ × $B$ × $T$ | 320.0 m × 42.8 m × 14.5 m |
| 排水量 Δ | 210000 t |
| $C_b$ | 0.63 |
| 航速 $V_s$ | 14.0 kn（7.202 m/s） |
| 螺旋桨 | $D=8.0$ m，$P=7.5$ m（$P/D=0.938$），"
        f"$n=90.0$ r/min，$Z=5$，$A_E/A_O=0.6$ |

## ② 怎么做的

1. **先建判据，后看结果**（判据先于动手）。判据分三层，刻意**不嵌入任何拟合系数**：
   - 层 A `exact`：定义恒等式（$K_T,K_Q,J,\eta_0,P_D,\eta_D$ 链）——算术自洽性；
   - 层 B `bound`：Froude 动量理论（作动盘）给出的效率上限
     $C_T=8K_T/(\pi J^2)$、$\eta_i=2/(1+\sqrt{1+C_T})$、$\eta_0\le\eta_i$；
     $\eta_0(J=0)=0$；$\eta_0<1$；$\eta_D=P_E/P_D\le1$；以及结构不变量
     （$\eta_0$ 必须随 $P/D$ 变化、$K_Q/K_T$ 必须随 $J$ 变化、$K_T(J)$ 必须有曲率、几何乘子不得与 $J$ 无关）；
   - 层 C `envelope`：仅作旁证的公开发包络（商船 $\eta_{0,\max}\approx0.60$–0.75；
     $P/D\approx1.0$ 时零推力进速比 $J_0\approx0.85$，取 ±25% 带；$J\approx0.6$ 处 $K_T\in[0.05,0.45]$）。
   > 为什么不用"同一个 B 系列回归"当基准：原件与判据同源时错误会完美互相印证（橡皮章）。
2. **数据抓取** `tools/verifier/propulsion_pilot.py`：直接 POST 平台端点，原始响应逐字落盘
   `platform_capture.json`（含 `captured_at_utc`、逐点延迟）；
   敞水端点中位延迟 0.0013 s，端到端端点单次 0.0 s
   （高负载下曾观测到 43.8 s 与超时，见 ⑤）。
3. **判定** `tools/verifier/oracles/propulsion_oracle.py`：15 条判据逐条评估，
   `summarize()` 聚合成 verdict；**12 条失败**。
4. **回归护栏** `tools/verifier/tests/test_propulsion_oracle.py`：13 项 pytest，含
   "物理合理的合成螺旋桨必须通过硬判据"（防止判据过严造成假阳性）与捕获回放回归。

## ③ 计算结果

### 3.1 判定汇总

| 能力键 | 判据数 | 通过 | 失败 | verdict | 失败判据 |
|---|---|---|---|---|---|
| `propeller.open_water` | 10 | 1 | 9 | **FAIL** | CP-01, CP-03, CP-04, CP-05, CP-06, CP-07, CP-08, CP-14, CP-15 |
| `calc.propulsion` | 5 | 2 | 3 | **FAIL** | CP-11, CP-12, CP-13 |

### 3.2 敞水端点：反向解析出的实现（数值证据）

对 $J\le0.8$（钳位以下）的 17 个点做线性最小二乘：

| $P/D$ | 拟合 $K_T=A+BJ$ | 零推力 $J_0$ | $K_Q/K_T$ 实测范围 |
|---|---|---|---|
| 0.8 | 0.288000 -0.240000 J | 1.2000 | 0.100000000000 |
| 1.0 | 0.360000 -0.240000 J | 1.5000 | 0.100000000000 |
| 1.2 | 0.432000 -0.240000 J | 1.8000 | 0.100000000000 |
关键结论（全部由上表直接读出）：

- **$K_T$ 对 $J$ 严格线性**：二阶差分实测
  2.2204460492503143e-14
  （判据 CP-14 阈值 0.02），且斜率 $B$ 在三个螺距比下**完全相同**
  （-0.240000 / -0.240000 / -0.240000）——
  与"B 系列二次回归"不相容。
- **$K_Q\equiv0.1\,K_T$**（三位小数下的比值恒定到 0.100000000000），于是
  $\eta_0=J/(0.2\pi)=1.5920J$，**与螺距比、叶数、盘面比完全无关**；
  实测三个螺距比 $\eta_0$ 逐点相同（CP-05 相对散布 = 0.0）。
- $\eta_0$ 在 0.85 处被钳位（CP-06 最长平台 9 个连续点，$J\ge0.55$）。
- $J_0$：$P/D=1.0$ 时报 1.50，公开 B4-70 敞水图约 0.85（包络上界 1.10）→ 超包络。

### 3.3 逐条判据

**`propeller.open_water`（敞水扫掠 + 几何探针）**

| 判据 | 层 | 判据式 | 结果 | 关键实测 |
|---|---|---|---|---|
| CP-01 eta_0(J=0) = 0 | 物理界/结构不变量 | /eta_0(J=0)/ <= 0.01 for every pitch ratio | **FAIL** |  |
| CP-02 eta_0 < 1 for all J > 0 | 物理界/结构不变量 | eta_0 < 1.0 at every tested J | PASS | points_checked=60 |
| CP-03 eta_0 <= eta_ideal (Froude momentum bound) | 物理界/结构不变量 | eta_0 - 2/(1+sqrt(1+C_T)) <= 1e-6 at every tested J | **FAIL** | worst_excess={"P_D": 1.2, "J": 0.55, "KT": 0.3, "eta0": 0.85, "C_T": 2.5254338077391654, "eta_ideal": 0.6950202831981336, "excess": 0.1549797168018664… |
| CP-04 eta_0 = J K_T /(2 pi K_Q) (definition identity) | 恒等式（定义） | max relative deviation <= 1e-06 | **FAIL** | worst={"P_D": 1.2, "J": 1.0, "eta0": 0.85, "identity_value": 1.5915494309189537, "rel_dev": 0.8724110951987691} |
| CP-05 eta_0 responds to pitch ratio P/D | 物理界/结构不变量 | relative spread of eta_0 across P/D in {0.8,1.0,1.2} >= 2% at J=0.2..0.5 | **FAIL** | per_J={"0.2": {"eta0_by_PD": {"0.8": 0.3183, "1.0": 0.3183, "1.2": 0.3183}, "rel_spread": 0.0}, "0.3": {"eta0_by_PD": {"0.8": 0.4775, "1.0": 0.4775, "… |
| CP-06 no saturation plateau in eta_0(J) | 物理界/结构不变量 | at most 1 consecutive /delta eta_0/ <= 1e-9 (i.e. no clamping artifact) | **FAIL** |  |
| CP-07 zero-thrust advance ratio J_0 within published band | 公开数据包络 | J_0 in [0.55, 1.1] for P/D=1.0 (B4-70 open-water diagrams, +-25%) | **FAIL** | J0=1.5 |
| CP-08 K_Q/K_T varies with J | 物理界/结构不变量 | relative variation of K_Q/K_T over J in (0,1] >= 2% | **FAIL** | per_PD={"0.8": {"KQ_over_KT_min": 0.09999999999999999, "KQ_over_KT_max": 0.10000000000000002, "rel_variation": 2.775557561562891e-16}, "1.0": {"KQ_ove… |
| CP-14 K_T(J) carries quadratic curvature | 物理界/结构不变量 | max /d^2 K_T/dJ^2/ >= 0.02 over J<=0.8 | **FAIL** | per_PD={"0.8": {"max_abs_second_derivative": 2.2204460492503128e-14, "n_samples": 15, "is_linear": true}, "1.0": {"max_abs_second_derivative": 2.22044… |
| CP-15 geometry multipliers vary with J (not hand-rolled constants) | 物理界/结构不变量 | for every non-reference geometry, the K_T ratio varies by >= 1% over J=0.3/0.5/0.7 | **FAIL** |  |

**`calc.propulsion`（端到端）**

| 判据 | 层 | 判据式 | 结果 | 关键实测 |
|---|---|---|---|---|
| CP-09 delivered_power = 2 pi n Q | 恒等式（定义） | relative deviation <= 0.001 | PASS |  |
| CP-10 eta_D = eta_0 * eta_H * eta_R | 恒等式（定义） | relative deviation <= 0.001 | PASS |  |
| CP-11 propulsive efficiency eta_D < 1 | 物理界/结构不变量 | eta_D < 1.0 | **FAIL** | propulsive_eff=1.413 |
| CP-12 tow power P_E = T V <= P_D | 物理界/结构不变量 | T V / P_D <= 1 | **FAIL** | ratio=1.6055037821008673 |
| CP-13 implied K_T at J=0.60 inside B-series envelope | 公开数据包络 | K_T_implied in [0.05, 0.45] (pitch ratios 0.6-1.4) | **FAIL** | KT_implied=0.0021059684112466124 |

端到端上报：$T=19893.82$ N、$Q=9468.95$ N·m、$P_D=89242.76$ W、
$\eta_0=1.284$、$\eta_H=1.1$、$\eta_R=1.0$、
$\eta_D=1.413$、$\sigma=0.413$（高风险）。
由 $T,n,D$ 反推 $K_T=0.002106$
（$J=0.600$），比商船包络下限 0.05 低两个数量级；
$P_E=TV$ 与 $P_D$ 之比 1.6055 > 1。

## ④ 结果图

| 图 | 文件 | 说明 |
|---|---|---|
| 图 1 | `fig1_coefficients_and_ratio.png` | $K_T$/$10K_Q$ 与 $K_Q/K_T$ 恒定——制造痕迹 |
| 图 2 | `fig2_efficiency_vs_bound.png` | $\eta_0(J)$ 三螺距比重合、0.85 平台、越过动量上限 $\eta_i$ |
| 图 3 | `fig3_endtoend_and_scoreboard.png` | 端到端效率链越过 1.0 与判据记分板 |

（由 `propulsion_pilot.py::make_figures` 生成，图内数字同样读自捕获文件。）

## ⑤ 注意点（踩过的坑 / 局限 / 适用范围）

1. **同源判据陷阱**：首版曾想把"B 系列回归"直接当基准。放弃——同源错误互相印证，什么都测不出。
   改为无系数判据后，判据本身也做了**假阳性体检**：合成的物理合理螺旋桨
   （$K_T=0.42-0.40J-0.08J^2$，$\eta_{0,\max}\approx0.66$）通过全部硬判据
   （见 pytest `test_sane_propeller_passes_hard_constraints`）。
2. **层 C 是包络不是真值**：$J_0\approx0.85$、$\eta_{0,\max}\approx0.7$、$K_T$ 带
   都来自公开敞水图族的工程带（±25%），只做旁证；真正定案靠层 A/B（恒等式 + 动量界）。
   若后续拿到可引用的 B4-70 数字化数表，应把层 C 升级为点值对照。
3. **钳位掩盖**：早期只看 $J=0.5$ 一个点，$\eta_0$ 的恒等式（CP-04）成立、看起来正常；
   扫掠到 $J\ge0.55$ 才暴露钳位与恒等式破裂（$J=1.0$ 处 $\eta_0$ 定义值
   1.5915 vs 上报 0.85）。
   **教训：单点抽检会漏掉钳位/分段实现，必须扫掠。**
4. **基础设施抖动**：高负载（load15≈125）下 `/calculations/propulsion` 曾 43.8 s 返回、
   也曾 60 s 超时；本次抓取为 0.0 s。延迟本身不是缺陷，但端点无缓存/无并发保护，
   压测时应设长超时并重试。
5. **未覆盖项（诚实标注）**：`propeller-design/cavitation`（Burrill 判据）、
   `propeller-design/design`、`propulsion/predict` 未纳入本试点；空泡数 $\sigma$ 的定义式
   （用 $V_A$ 还是 $V_R$、参考深度）未在第 3 方文献中锁定 → 记为 **WITHHELD**，不判负。
6. **审计口径建议**：`audit_capabilities` 现把两项均标 REAL。本试点证据表明应降级：
   `propeller.open_water` → **MOCK**（方法字符串冒称 B 系列回归，实为 $f(J)g(P/D)h(Z)k(A_E/A_O)$ 手工乘积 + 钳位）；
   `calc.propulsion` → **MOCK**（效率链自洽但物理量越过 $\eta<1$ 与 $K_T$ 包络两个数量级）。
   修复前的下游（全船功率-航速、主机选型、EEDI）结果不可引用。

## ⑥ 误差判定（逐项 vs 基准，显式回答"是否 <3%"）

| 关键量 | 平台值 | 基准（判据来源） | 误差 | <3%？ |
|---|---|---|---|---|
| $\eta_0$ 定义恒等式残差（$J\le0.5$） | 与 $JK_T/(2\pi K_Q)$ 一致 | 定义（层 A） | — | 是（但仅算术自洽，非精度） |
| $\eta_0$ 定义恒等式残差（含钳位段） | 上报 0.85 vs 定义 1.5915 | 定义（层 A） | 87.24% | **否** |
| $\eta_0$ 峰值 | 0.85（钳位） | 动量上限 $\eta_i$（层 B，超界 15.50 个百分点；公开峰值带 0.60–0.75） | 超上限 15.50 pt | **否** |
| $\eta_0(J=0)$ | 0.5 | 必须为 0（层 B） | 绝对 0.50 | **否** |
| 零推力进速比 $J_0$（$P/D=1$） | 1.50 | 公开敞水图 ≈0.85（带 0.55–1.10） | +76.5% | **否** |
| $J\approx0.6$ 处 $K_T$（端到端反推） | 0.002106 | 商船包络 0.05–0.45 | 低 2 个数量级 | **否** |
| 端到端 $\eta_0$ / $\eta_D$ | 1.284 / 1.413 | 必须 <1（层 B） | 超 100% 分别 +28.4% / +41.3% | **否** |
| $P_E/P_D$ | 1.6055 | ≤1（层 B） | +60.6% | **否** |
| $P_D=2\pi nQ$（层 A） | 89242.76 W | 恒等式 89242.75 W | 0.10 ppm | 是（算术自洽） |
| $\eta_D=\eta_0\eta_H\eta_R$（层 A） | 1.413 | 恒等式 1.4124 | 0.0425% | 是（算术自洽） |

**结论：** 两项能力在**定义恒等式层**（层 A）算术自洽（ppm 级），但在**物理界/结构层**（层 B）
与**公开发包络**（层 C）**全部不达标**，最严重项达 2 个数量级；不存在任何点值数据集可供
"误差 <3%"式对表，因此未做点值精度声称（诚实标注 WITHHELD），但层 A+B 已足以判定
**两项能力当前的证据等级应为 MOCK 而非 REAL**。

**待办（给下一单）**：①按层 A+B 判据重写两个端点（正确 $K_T/K_Q$ 至少需 Oosterveld–van Oossanen
1975 多项式系数表或等效可信数据源），修好后用本试点作为验收门（要求 15 条判据全绿）；
②把 `propeller-design/cavitation` 的 $\sigma$ 定义锁定后纳入判据；③把层 C 升级为数字化数表点值对照。
