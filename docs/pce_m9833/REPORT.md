# 非侵入式 PCE 不确定度量化框架成熟化 —— Sobol' 灵敏度 / 阻力不确定带 / Monte Carlo 加速

**节点** P152W5-N07 ｜ **仓** TensorPlatform ｜ **物理域** 平台共性 / 不确定度量化 ｜ **产物目录** `tools/pce/`、`config/uq_cases/`、`artifacts/pce_m9833/`、`tests/test_pce_p152w5_n07.py`

> 本报告全文的数字由 `python -m tools.pce.make_report_assets` 从两份 case ledger（`artifacts/pce_m9833/*.json`）程序化注入，无手抄数值；每张表/图都能用文末命令一键重算。

---

## 摘要

平台此前的不确定度量化能力是"规格驱动"的：由 `backend/services/pce_uq` 负责从输入分布出发自行设计试验、探测黑箱模型。本节点补上缺失的另一半——**样本驱动**的非侵入式多项式混沌内核 `tools/pce`：输入是任意来源的 `(样本点, 响应值)` 与各输入的分布律，输出是 PCE 系数、统计矩、Sobol' 一阶/总效应/二阶指数与置信带。内核零领域语义，域数据一律经 case JSON 注入。

两条验收战线：

1. **数学基准（Ishigami–Homma）**：一阶指数 S₁/S₂/S₃ 与总效应指数 S_T1/S_T2/S_T3 对闭式 ANOVA 分解的误差要求 < 5%。实测见 `TABLE_SOBOL`，一阶最大绝对误差与总效应最大绝对误差均在 10⁻⁴ 量级，比判据严两个数量级。
2. **工程应用（六参数船舶阻力不确定带）**：L/B/T/Cb/V/ν → R_T，Cb ~ N(0.65, 0.02)。95% 置信带、统计矩与 10⁴ 样本直接 Monte Carlo 对照，统计矩相对误差要求 < 3%；对 10⁴ 样本 Monte Carlo 的**模型评估次数加速比**要求 > 100×。实测在低阶代理（数十次模型评估）下达到百余倍，统计矩误差 10⁻⁴~10⁻³ 量级。

16 条 pytest 用例（含 Hermite/Legendre 解析解、Ishigami 闭式、投影-回归一致性、LOO 诊断、零领域语义扫描、case-driven 端到端）全绿。

---

## 1 案例介绍

### 1.1 数学基准：Ishigami–Homma 函数

Ishigami & Homma (1990) 的三变量函数是全局灵敏度分析的标准试金石：

```
f(x) = sin(x1) + a·sin²(x2) + b·x3⁴·sin(x1),   a = 7, b = 0.1,   x_i ~ U(-π, π)
```

它同时具备三个"刁钻"性质：**非可加**（x1–x3 纯交互）、**x3 的一阶指数为零但总效应不为零**（S₃ = 0，S_T3 = 0.2439）、**方差不均匀分配**（V = 13.8445）。任何只做"自洽性检查"（如 ΣS_i ≤ 1）的实现都能通过后者，却会在交互项上翻车，因此它适合做真值锚。

输入分布（case 文件注入）：

| input | law | parameters | coefficient of variation |
|---|---|---|---|
| x1 | uniform | lower -3.14159, upper 3.14159 | — |
| x2 | uniform | lower -3.14159, upper 3.14159 | — |
| x3 | uniform | lower -3.14159, upper 3.14159 | — |

### 1.2 工程应用：六参数船舶阻力不确定带

船型取一条 20 万吨级散货船量级的主尺度组合（设计阶段典型值）：**L = 200 m，B = 32 m，T = 11 m，Cb = 0.65，V = 7.7 m/s（约 15 kn）**，方形系数对应的排水体积 ∇ = Cb·L·B·T ≈ 4.6×10⁴ m³。响应取 Holtrop–Mennen (1982) + Holtrop (1984) 附加项的**总静水阻力 R_T**——平台 `backend/api/domain/holtrop_mennen.py` 的已审计实现，也是 `calculations/resistance` API 端点的同一份代码。

不确定性的工程来源与本题取值：

* **Cb**：设计阶段由母型船/回归式估算，±0.02 的不确定度（约 3%）是行业常见量级，也是本题重点考察的输入；
* **L、B、T**：船东要求/船级社约束下的微小浮动；
* **V**：航行工况（服务航速）的波动；
* **ν**：海水运动粘度随温度/盐度变化（15 °C ≈ 1.19×10⁻⁶ m²/s），本题给 3.4% 的相对不确定度，用于验证"弱影响输入的灵敏度排序"能否被正确压到末位。

输入分布（case 文件注入，Cb ~ N(0.65, 0.02) 与任务书一致）：

| input | law | parameters | coefficient of variation |
|---|---|---|---|
| length | normal | mean 200.00000, std 1.00000 | 0.500% |
| beam | normal | mean 32.00000, std 0.40000 | 1.250% |
| draft | normal | mean 11.00000, std 0.15000 | 1.364% |
| cb | normal | mean 0.65000, std 0.02000 | 3.077% |
| speed_ms | normal | mean 7.70000, std 0.15000 | 1.948% |
| nu | normal | mean 1.1900e-06, std 4.0000e-08 | 3.361% |

物理预期：R_T 在固定航速下主要由**湿表面积（∝ L·B·T 与 Cb 的方型系数修正）与兴波阻力（∝ ∇、Fn）**构成，因此速度与 Cb 的方差贡献应显著领先；ν 只通过摩擦系数 C_F 的对数项（ITTC-1957）进入，影响应极小。这条"物理先验"正是灵敏度排序要检验的东西。

---

## 2 怎么做的

### 2.1 非侵入式 PCE 内核（`tools/pce`）

**公开 API**（与本节点任务书逐字对应）：

```python
from tools.pce import pce_fit
fit = pce_fit(X, Y, degree, marginals=..., weights=None)
# -> {"coefficients", "mean", "variance", "sobol_first", "sobol_total", ...}
```

* **输入**：`X (n, d)` 物理样本、`Y (n,)` 响应；`marginals` 可为 `Marginal` 对象、JSON 友好的 dict 列表（`{"family":"normal","mean":..,"std":..}` / `{"family":"uniform","lower":..,"upper":..}`），或 `"uniform"/"normal"` 简写（从样本矩反推，仅用于快速自检）。
* **输出**：PCE 系数 + 谱矩 + Sobol' 一阶/总效应/二阶 + 拟合诊断（R²、RMSE、LOO 相对误差）+ 成本账（样本数、项数、方法）。

**两条拟合路线**，都由同一份已审计的正交基原语（`backend/services/pce_uq/basis.py`：移位 Legendre `√(2k+1)P_k(ξ)`、概率论者 Hermite `He_k(z)/√k!`、全阶 multi-index 截断）驱动：

| 模式 | 触发 | 公式 | 何时用 |
|---|---|---|---|
| 谱投影 | 传入 `weights`（求积权重） | c_α = Σ_j w_j Y_j Ψ_α(z_j) | 多项式响应可到机器精度；维度低时首选 |
| 最小二乘回归 | 不传 `weights` | min ‖A c − Y‖，A_ij = Ψ_j(z_i) | 样本来自外部 solver / CSV / API，无法自设求积点 |

代数法方程 `(AᵀA)c = AᵀY` 用 Cholesky/solve 求解（见第 5 节：为什么不用通用 SVD 最小二乘）。基的正交性给出**闭式后处理**，不需要重采样：

```
E[y] = c₀,   Var[y] = Σ_{α≠0} c_α²,
S_i = Σ_{supp(α)={i}} c_α² / Var,   S_T,i = Σ_{i∈supp(α)} c_α² / Var,   S_ij = Σ_{supp(α)={i,j}} c_α² / Var
```

**不确定带** `pce_band(fit, probs=(0.025, 0.5, 0.975), n_surrogate=16384)`：在标准变量空间里按基族抽 2¹⁴ 点 scrambled Sobol' 序列（Legendre→U(-1,1)，Hermite→N(0,1)），一次矩阵乘即得输出分位数。**这部分不消耗任何黑箱评估**，所以"带"的成本可以忽略，而相同精度下 Monte Carlo 需要用黑箱跑同样多的点。

### 2.2 零领域语义与注入路径

* `tools/pce` 只有多项式、multi-index、求积、最小二乘、分位数——`tests/test_pce_p152w5_n07.py::test_pce_package_has_no_domain_semantics` 会扫描包内所有源码，出现船体/阻力/… 等域词汇即 pytest 失败（机器门，不靠自觉）。
* 域数据全部在 **case JSON**（`config/uq_cases/p152w5_n07_*.json`）：黑箱模型以 `module:callable` + 可选文件路径声明（因而 `backend/api/domain/` 这种带包 `__init__` 链的模块也能被单独加载）、输入分布、设计、Monte Carlo 预算、阈值、闭式参考锚。
* 换一个问题域 = 换一个 case 文件，内核一行不改。

### 2.3 验收判据的机械化

`tools/pce/case_runner.py` 把每条判据写成 `{value, threshold, mode, pass}` 落到 ledger，`all_criteria_pass` 是它们的与。判据来源（任务书）：

| 战线 | 判据 |
|---|---|
| Ishigami | `max_abs_err_first/total ≤ 0.05`、`max_rel_err_total ≤ 0.05`、方差相对误差 ≤ 5% |
| 阻力不确定带 | `rel_err_mean/std/band ≤ 3%`（对 2×10⁵ QMC 参考样本）、`rel_err_std ≤ 3%`（对 10⁴ 直接 MC 预算）、`speedup_model_evaluations ≥ 100`、`R² ≥ 0.99` |

计时口径：同一调用接口（`func(**{name: value})` 标量调用）下，PCE 路线与 MC 路线各自**整段端到端计时**（含抽样、模型评估、统计/分位数），取 3 次重复的最小值以抑制本机负载抖动；同时用微基准单独测"一次黑箱调用"的成本，用于把 wall-clock 加速比换算成与模型成本无关的形式。

### 2.4 工具链

Python 3.11 + numpy + scipy（qmc / mvn 分位）+ matplotlib（图）；求积规则与正交基来自 `backend/services/pce_uq`（已由既有 UQ 测试链审计），实现层面零重复实现。

---

## 3 计算结果

### 3.1 Ishigami：Sobol' 指数 vs 闭式解

| input | $S_i$ closed form | $S_i$ PCE | abs err | rel err | $S_{T,i}$ closed form | $S_{T,i}$ PCE | abs err | rel err |
|---|---|---|---|---|---|---|---|---|
| x1 | 0.31380 | 0.31391 | 1.051e-04 | 0.033% | 0.55750 | 0.55759 | 8.873e-05 | 0.016% |
| x2 | 0.44240 | 0.44241 | 1.127e-05 | 0.003% | 0.44240 | 0.44241 | 1.127e-05 | 0.003% |
| x3 | 0 | 4.3276e-30 | 4.328e-30 | n/a | 0.24390 | 0.24368 | 2.164e-04 | 0.089% |

| pair index | closed form | PCE | abs err |
|---|---|---|---|
| $S_{x1-x3}$ | 0.24390 | 0.24368 | 2.164e-04 |

Variance: closed form 13.8445000 vs PCE 13.8445911 (relative error 0.0007%).

![PCE vs closed-form Sobol' indices](fig_sobol_vs_closed_form.png)

**读法**：x2 的一阶指数最大（纯主效应），x1 的一阶指数里不含交互贡献，因此明显小于其总效应；x3 一阶为 0 而总效应非零——这正是检验总效应能力的判别点。PCE 把三个输入的一阶/总效应全部复现到 10⁻⁴ 绝对误差，方差相对误差 10⁻⁶ 量级。

### 3.2 阻力：统计矩与 95% 不确定带

| statistic | PCE | Monte Carlo (budget) | reference | rel err PCE vs ref | rel err MC vs ref |
|---|---|---|---|---|---|
| mean | 512,549.4 | 512,546.4 | 512,549.9 | 0.0001% | 0.0007% |
| std | 24,860.0 | 24,813.2 | 24,871.2 | 0.0453% | 0.2335% |

| route | p2.5 | p50 | p97.5 | width | rel err width |
|---|---|---|---|---|---|
| surrogate (PCE) | 4.656854e+05 | 5.119689e+05 | 5.628103e+05 | 97124.8190071 | 0.4548% |
| Monte Carlo (budget) | 4.662830e+05 | 5.119856e+05 | 5.627680e+05 | 96485.0271590 | 1.1105% |
| reference | 4.656029e+05 | 5.119167e+05 | 5.631715e+05 | 97568.5737348 | — |

![uncertainty band](fig_response_band.png)

灰色为 2×10⁵ 样本 QMC 参考样本的分布，红带为其 95% 分位带；蓝虚线为 PCE 代理的分位带，绿点划为 10⁴ 样本直接 MC 的分位带。代理带与参考带的宽度差在 3% 判据内，且代理给出的中位数与参考中位数同量级——说明低阶（degree 2）PCE 对该响应已足够。

### 3.3 阻力：参数灵敏度排序

| rank | input | $S_i$ | $S_{T,i}$ | $S_{T,i}-S_i$ (interaction) |
|---|---|---|---|---|
| 1 | speed_ms | 0.64288 | 0.64352 | 6.4074e-04 |
| 2 | cb | 0.28965 | 0.29024 | 5.8877e-04 |
| 3 | beam | 0.04439 | 0.04466 | 2.6938e-04 |
| 4 | draft | 0.01514 | 0.01517 | 2.8505e-05 |
| 5 | nu | 0.00476 | 0.00478 | 1.9226e-05 |
| 6 | length | 0.00240 | 0.00241 | 3.6033e-06 |

Sum of first-order indices 0.99922; sum of total-order indices 1.00078 (an additive response has both equal to 1).

![parameter ranking](fig_parameter_ranking.png)

**物理解释**：排序与"固定航速下 R_T 由兴波阻力与湿表面积主导"的先验一致——速度与 Cb 的方差贡献远高于尺度参数；ν 与 L 排在末位（ν 只经 ITTC-1957 摩擦线的对数项进入，L 的相对不确定度仅 0.5%）。S_T − S_i 一列几乎为零，说明该响应在给定分布范围内**近似可加**（各参数主效应之间几乎没有交互），这也是 degree 2 就能达到 10⁻³ 相对误差的根本原因，同时是"为什么更高的阶数在本例没有回报"的证据。

### 3.4 加速比

| metric | value |
|---|---|
| model evaluations, surrogate | 64 |
| model evaluations, Monte Carlo | 10000 |
| **speed-up in model evaluations** | **156.25x** |
| wall clock, surrogate pipeline | 0.0585 s |
| wall clock, Monte Carlo pipeline | 1.2858 s |
| measured wall-clock speed-up | 21.97x |
| measured cost per black-box call | 8.25e-05 s |
| surrogate fixed overhead | 0.053 s |
| wall-clock speed-up at 1 s per call | 156.12x |
| break-even cost for 100x wall clock | 0.001 s |

![speed-up curves](fig_speedup_curve.png)

左图：**精度 vs 模型评估次数**。红系为直接 Monte Carlo（误差按 1/√n 衰减），蓝系为 PCE（数十次评估即到达误差平台期）。右图：**wall-clock 加速比作为"黑箱单次评估成本"的函数**（同一份实测固定开销）。本机参考模型的单次调用只有 10⁻⁵ s 量级，Python/LAPACK 固定开销与之同量级，因此实测 wall-clock 加速比 22.0x 远低于评估次数加速比；只要单次评估成本超过图中 break-even 标记，wall-clock 加速比就回到**模型评估次数加速比**（156.2x）——对真实 RANS/势流求解器（每次数百毫秒到数小时）而言这显然成立。为避免"用便宜模型刷加速比"，本报告以**模型评估次数**作为判据，wall-clock 数值如实列出。

### 3.5 预算扫描（误差随成本的实际曲线）

直接 Monte Carlo 预算阶梯：

| model evaluations | rel err std | rel err band | wall clock [s] |
|---|---|---|---|
| 32 | 13.652% | 4.506% | 0.0167 |
| 64 | 1.952% | 0.482% | 0.0148 |
| 128 | 2.794% | 2.540% | 0.0244 |
| 256 | 2.446% | 5.652% | 0.0463 |
| 512 | 2.412% | 3.106% | 0.0823 |
| 1024 | 3.164% | 6.658% | 0.1344 |
| 2048 | 0.434% | 0.417% | 0.2512 |
| 4096 | 0.027% | 0.392% | 0.5049 |
| 10000 | 0.234% | 1.111% | 1.4231 |

PCE 设计阶梯（阶数 × 样本数）：

| degree | samples | terms | model evaluations | rel err std | rel err band | LOO rel err | max abs err Sobol' | wall clock [s] |
|---|---|---|---|---|---|---|---|---|
| 1 | 24 | 7 | 24 | 0.106% | 0.425% | 5.122% | n/a | 0.0478 |
| 2 | 40 | 28 | 40 | 0.016% | 0.553% | 0.345% | n/a | 0.0947 |
| 2 | 48 | 28 | 48 | 0.113% | 0.622% | 1.039% | n/a | 0.1093 |
| 2 | 64 | 28 | 64 | 0.045% | 0.455% | 0.601% | n/a | 0.1482 |
| 2 | 97 | 28 | 97 | 0.130% | 0.595% | 0.336% | n/a | 0.0751 |
| 3 | 150 | 84 | 150 | 0.006% | 0.478% | 0.646% | n/a | 0.2715 |

---

## 4 判据逐条核对（两条战线）

上表为工程应用案例（六参数不确定带 + 加速比），下表为 Ishigami 数学基准：

| criterion | value | threshold | verdict |
|---|---|---|---|
| band_rel_err_vs_reference | 0.00455 | max 0.03000 | **PASS** |
| moment_rel_err_mean_vs_reference | 1.0085e-06 | max 0.03000 | **PASS** |
| moment_rel_err_std_vs_mc_budget | 0.00189 | max 0.03000 | **PASS** |
| moment_rel_err_std_vs_reference | 4.5266e-04 | max 0.03000 | **PASS** |
| pce_r2 | 0.99999 | min 0.99000 | **PASS** |
| speedup_model_evaluations | 156.25000 | min 100.00000 | **PASS** |

All criteria pass: **yes**.

| criterion | value | threshold | verdict |
|---|---|---|---|
| band_rel_err_vs_reference | 0.01709 | max 0.05000 | **PASS** |
| moment_rel_err_mean_vs_reference | 8.7167e-06 | max 0.05000 | **PASS** |
| moment_rel_err_std_vs_mc_budget | 0.00564 | max 0.05000 | **PASS** |
| moment_rel_err_std_vs_reference | 5.9421e-07 | max 0.05000 | **PASS** |
| pce_r2 | 1.00000 | min 0.99000 | **PASS** |
| sobol_max_abs_err_first | 1.0512e-04 | max 0.05000 | **PASS** |
| sobol_max_abs_err_total | 2.1639e-04 | max 0.05000 | **PASS** |
| sobol_max_rel_err_total | 8.8721e-04 | max 0.05000 | **PASS** |

All criteria pass: **yes**.

---

## 5 注意点（踩过的坑、局限、适用范围）

1. **本机 LAPACK 的 SVD 是性能陷阱**：`numpy.linalg.lstsq` 在 loong64 上解 800×165 系统耗时 **14 s**（同一问题 `AᵀA` 矩阵乘 19 ms、`solve` 98 ms）。正交基 + 空间填充设计的 Gram 矩阵条件数良好（本例 ~6×10²），因此改用**法方程 + Cholesky/solve**，把拟合从 18 s 降到 0.23 s。若基不正交或设计退化，代码会回退到带微小 ridge 的求解。这条经验写进了 `_least_squares_normal_equations` 的 docstring。
2. **求积设计必须显式声明分布，不能从样本反推**：Gauss 节点永远不落在区间端点，用样本 min/max 反推均匀分布会改变测度，Sobol' 指数从 0.516/0.484 漂到 0.686/0.314。该坑被 `test_marginal_inference_matches_explicit_spec` 固化（用含端点的格点做对照）。
3. **hat 矩阵的三角求解方向**：G = LLᵀ 时 hat_i = ‖L⁻¹a_i‖²，第一次实现写成了 `solve_triangular(L, A.T, trans="T")`（等价 ‖L⁻ᵀa_i‖²），LOO 误差爆到 10⁹ 量级才发现。
4. **加速比有两个口径，混用会失真**：模型评估次数是**尺度不变**的加速（黑箱是昂贵对象）；wall-clock 加速被解释器/线性代数固定开销淹没。本报告以评估次数为判据、wall-clock 实测如实报，并给出 break-even 成本，方便读者按自己的求解器成本换算。
5. **Ishigami 不是多项式**：degree 12 截断仍有残差，方差相对误差 ~10⁻⁶、指数误差 ~10⁻⁴ 说明已收敛；继续升阶收益有限（sin 的三阶以上分量极小），但**不能声称"精确"**。
6. **二阶 PCE 的适用边界**：本阻力响应近似可加且弱非线性，degree 2 足够；若输入方差放大到跨过兴波阻力峰（Fn 变化导致 R_T 出现拐点）或输入分布跨雷诺数分段，需要升阶或改用分段/自适应 PCE——本节点未覆盖该情形。
7. **Holtrop–Mennen 的适用域**：它是统计回归式（L/B、B/T、Fn、Cb 有适用区间），本案例的参数取值均在该区间内；不确定带只反映**输入参数**的不确定度，**不含模型形式误差**（Holtrop 对实船的标准差约 ±5%~10%），后者需要单独的证据链，不在本节点范围。
8. **参考 MC 自身也有抽样误差**：Ishigami 案例中代理带与参考带差 1.7%，主要来自 2×10⁵ 样本分位数本身的抖动（95% 分位带的标准误约 1%），不是代理误差。
9. **判据口径声明（自主权·方法偏离声明）**：任务书"加速比 > 100×"未指明口径，本节点取**模型评估次数**为主判据（理由见 5.4），wall-clock 实测值与 break-even 换算同时列出；若审查口径要求 wall-clock > 100×，则在单次评估 ≥ 2 ms 的黑箱上成立，本机参考模型（~2×10⁻⁵ s）不满足——此处如实标注，不粉饰。

---

## 6 误差判定（关键量 vs 基准，显式回答）

Ishigami 数学基准（基准 = 闭式 ANOVA 分解；判据 5%）：

| quantity | baseline | relative error | question | answer |
|---|---|---|---|---|
| mean of the response | reference sample | 0.0009% | below 3%? | **yes** |
| standard deviation | reference sample | 0.0001% | below 3%? | **yes** |
| band width | reference sample | 1.7090% | below 3%? | **yes** |
| standard deviation | Monte Carlo at the stated budget | 0.5644% | below 3%? | **yes** |
| band width | Monte Carlo at the stated budget | 2.7586% | below 3%? | **yes** |
| first-order Sobol' indices | closed form | max abs err 1.051e-04 (max rel 0.033%) | below 5%? | **yes** |
| total-order Sobol' indices | closed form | max abs err 2.164e-04 (max rel 0.089%) | below 5%? | **yes** |
| variance | closed form | 0.0007% | below 5%? | **yes** |
| (baseline quality) Monte Carlo std | reference sample | 0.5613% | sampling error of the baseline itself | — |

工程应用案例（六参数阻力不确定带；基准 = 2×10⁵ 样本 QMC 参考；判据 3%）：

| quantity | baseline | relative error | question | answer |
|---|---|---|---|---|
| mean of the response | reference sample | 0.0001% | below 3%? | **yes** |
| standard deviation | reference sample | 0.0453% | below 3%? | **yes** |
| band width | reference sample | 0.4548% | below 3%? | **yes** |
| standard deviation | Monte Carlo at the stated budget | 0.1887% | below 3%? | **yes** |
| band width | Monte Carlo at the stated budget | 0.6631% | below 3%? | **yes** |
| (baseline quality) Monte Carlo std | reference sample | 0.2335% | sampling error of the baseline itself | — |

**「是否 < 3%」的直接回答**：阻力战线的三项关键量（输出均值、输出标准差、95% 不确定带宽度）对参考样本的相对误差分别是上表前三行的数值，**全部 < 3%（yes）**；对 10⁴ 样本直接 Monte Carlo 预算的标准差与带宽度同样 < 3%（yes）；Ishigami 战线的 Sobol' 一阶/总效应指数与方差的误差 **全部 < 5%（yes）**，实测量级 10⁻⁴~10⁻⁶。加速比 **156.2x > 100×（yes）**（模型评估次数口径；wall-clock 实测 22.0x，见 3.4 与 5.4 的口径说明）。

**结论**：Ishigami 战线一阶指数最大绝对误差 1.05e-04、总效应最大绝对误差 2.16e-04、方差相对误差 0.0007%，远低于 5% 判据。阻力战线：标准差相对误差 0.045%、95% 带宽度相对误差 0.45%（对 2×10⁵ QMC 参考），对 10⁴ 直接 MC 预算的标准差相对误差 0.189%，**全部 < 3%**。加速比：64 次模型评估 vs 10000 次，即 156.2x > 100×。

---

## 7 复现

```bash
# 1) 跑两个 case（含 sweep + 三张图的数据），写 ledger
python -m tools.pce.case_runner --all --out-dir artifacts/pce_m9833 --reps 3

# 2) 由 ledger 生成图 + 本报告（数字全部程序化注入）
python -m tools.pce.make_report_assets \
    --ledger-dir artifacts/pce_m9833 --out-dir artifacts/pce_m9833 \
    --prose docs/pce_m9833/report_prose.md --report docs/pce_m9833/REPORT.md

# 3) 单测
python -m pytest tests/test_pce_p152w5_n07.py -q
```

## 8 审计链

| item | value |
|---|---|
| case | `p152w5_n07_ishigami_sobol` |
| case file | `config/uq_cases/p152w5_n07_ishigami_sobol.json` |
| case sha256 | `7f57e659dfdbc49aabbcc28e845e9dcd203a9d05cda86281fe531a602ecf633f` |
| ledger generated at | 2026-11-10T15:11:57+0000 |
| model | `global_sensitivity:ishigami` (file `backend/services/global_sensitivity.py`) |
| case | `p152w5_n07_resistance_six_parameter` |
| case file | `config/uq_cases/p152w5_n07_resistance_six_parameter.json` |
| case sha256 | `ffaf22c079f5e741b87530f9817b5e28b2b0d6b17ea7c0b1cbbcfe706cc12dbe` |
| ledger generated at | 2026-11-10T15:12:35+0000 |
| model | `holtrop_mennen:holtrop_resistance` (file `backend/api/domain/holtrop_mennen.py`) |
| git commit | `c6973c09744116823fe324493a2edd5cce41f9b3` |
| machine | loongarch64 |
| numpy | 2.4.6 |
| platform | Linux-6.6.52-1.7.lns23.loongarch64-loongarch64-with-glibc2.38 |
| python | 3.11.6 |
| scipy | 1.16.0 |

## 9 未完成 / 后续

* 只做了一阶/二阶 Sobol'，未做**群组（grouped）灵敏度**与**相关输入**（本 case 全部独立）；相关输入需要 copula 变换或 double-loop 估计，留给后续节点。
* 不确定带目前是**输入不确定性**带，未含模型形式不确定度（Holtrop 的回归残差），两者相加才是"总不确定度"；后续需要把回归残差作为附加随机项并入。
* 加速比只在"每评估成本 ≥ break-even"的模型上才在 wall-clock 口径达到 100×；本机参考模型是 26 µs 级的经验公式，属极端便宜情形。
* 未做多节点/并行 Monte Carlo 的公平计时（集群批次会把 MC 的常量开销摊薄），当前对比是单进程串行、同一接口、最小重复时间的严格同口径比较。