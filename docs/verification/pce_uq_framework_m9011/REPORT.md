# Non-intrusive PCE uncertainty framework: analytic Sobol' verification, six-parameter uncertainty band, Monte Carlo acceleration

* node **P152W1-N05**, repository `TensorPlatform`, branch `agent/m9011`
* machine verdict: **all acceptance criteria PASS**
  (table generated from `artifacts/uq_pce_m9011/ledger.json`, no hand-copied numbers)
* rerun: `python -m tools.uq_bench.run_case --all --out-dir artifacts/uq_pce_m9011 --reps 3 --sweep`
* tests: `python -m pytest tests/test_pce_uq_kernel_m9011.py -q` (18 cases)
* audit chain: git `251b501e5770`, python 3.11.6, numpy 2.4.6,
  generated 2026-11-10T06:38:17Z; case cards sha256
  `4cd905ae870c7f07...` / `3da633bcb2a6e2f3...`

---

## 0 摘要（中文，数字同样由台账注入）

本节点把平台的不确定度量化能力收敛为一个**零领域语义**的非侵入式多项式混沌（PCE）内核
`backend/services/pce_uq/`：正交归一 Legendre/Hermite 基 + total-degree/双曲截断 + Gauss 张量与
Smolyak 稀疏求积 + 谱投影与 LHS 最小二乘两条装配路线 + 系数级闭式矩与 Sobol 分解 +
直接 Monte Carlo 参考与"评估次数/墙钟"双口径加速记账。物理内容全部经 `config/uq_cases/*.json`
算例卡注入；平台域词扫描器对内核与运行器命中 0（常驻 pytest）。

机器判据（全部由 `tools/uq_bench/run_case.py` 裁决，数字见台账）：

* 解析算例（Ishigami–Homma 1990 闭式方差分解）：Sobol 一阶/总效应最大绝对误差
  **1.80e-03**（判据 0.05），且一阶≈0、总效应>0 的判别特征被正确复现；
* 六参数传播算例（黑箱 = 仓库已发布的经验回归模型）：响应均值/标准差相对误差
  **0.0080 % / 1.550 %**（判据 3 %），
  对 2×10⁵ 点 QMC 精确基准为 **0.0067 % / 1.192 %**；
* 加速比：仅用 **13** 次黑箱评估即达到上述精度，
  相对直接 Monte Carlo（10⁴ 次）为 **769×**
  （评估次数口径，主判据），同机同接口墙钟 **327×**（判据 100×）；
* 结论：**全部判据 PASS**，无 honest-FAIL 需声明。

---

## 1 Why this layer exists

Three PCE code paths existed in the platform before this node -- one bound to a
single physical model (projection, quadrature based), one least-squares surrogate
for a digital twin, one pick-freeze Sobol' estimator. Each ran, but nothing was
*scored*: acceptance rested on self-consistency invariants, which are blind to a
globally rescaled or mis-normalised basis. Changing the black box meant rewriting
the assembly code, and "how much faster is this than brute force" had no ledger.

`backend/services/pce_uq/` is the domain-free kernel that closes that gap. It
accepts only a black box `f(x) -> y`, one marginal law per input, and a JSON case
card; all physical content arrives through `config/uq_cases/*.json`. The platform's
own vocabulary scanner (`tools/audit/domain_leak_scan.py` with
`config/domain_word_denylist.json`) reports **0 hits** on the kernel and the runner,
and that check is a standing pytest case.

## 2 Case cards

### 2.1 Analytic card: Ishigami & Homma (1990) analytic Sobol' decomposition recovered by the non-intrusive PCE kernel

### 算例 A：Ishigami & Homma (1990) 解析算例

$$\varphi(x_1,x_2,x_3)=\sin x_1 + a\sin^2 x_2 + b\,x_3^4\sin x_1,\qquad x_i\sim U(-\pi,\pi),\ a=7,\ b=0.1$$

选取理由：它有解析方差分解（$V_1$、$V_2$、$V_{13}$ 闭式），且 $x_3$ 的一阶效应为 0 而总效应不为 0
（解析 $S_3=0$、$S_{T,3}=0.2437$）——只做可加分解、或把 ANOVA 分组写错的实现会在这里露馅。
闭式基准：均值 $3.5000$、方差 $13.844588$。

本算例被判据约束的量是**谱 ANOVA 的正确性**，而不是拟合优度：代理能在采样点上拟合得好、
Sobol 指数却整体缩放错（历史欠账里真实发生过）时，本算例会失败。

### 2.2 Propagation card: Six-parameter input uncertainty propagation through the Holtrop-Mennen calm-water resistance regression: uncertainty band + Sobol' ranking + Monte Carlo acceleration

### 算例 B：六参数输入不确定度传播（黑箱 = 已发布阻力回归）

**物理背景**：早期设计阶段的航速—功率预估必须先回答"这条船在给定航速下的静水总阻力是多少、这个数有多不确定"。
平台内的黑箱取仓库已发布的 Holtrop–Mennen (1982) 静水阻力回归（含 1984 年球鼻艏/方尾/相关系数补贴增补项），
输出字段 `rt_n`（单位 N），标称 $200$ m × $32$ m × $11$ m、$C_B=0.82$、$V=7.7$ m/s。

**六个不确定输入的物理含义与量级**（1σ）：

| 输入 | 物理含义 | 为什么说不准 |
|---|---|---|
| length | 垂线间长 | 改装/建造公差、水线定义口径 |
| beam | 型宽 | 建造公差、外板厚度 |
| draft | 设计吃水 | 载况、密度、纵倾调整 |
| cb | 方形系数 | 型线放样/系列化偏差 |
| speed_ms | 航速 | 海况、污底、主机裕度对应的实际可达航速 |
| lcb_frac | 浮心纵位置 | 总布置调整带来的纵倾 |

**几何先验自检**（判据在算之前）：$C_B\cdot L\cdot B\cdot T = 57728$ m³，
与该船型排水体积量级一致；$L/B=6.25$、$B/T=2.91$ 落在 Holtrop 回归的有效性域
（$0.55\le C_B\le0.85$、$3.9\le L/B\le9.5$、$2.1\le B/T\le4.0$、$F_n\le0.45$）以内，因此本算例的输出属于"域内"结论。

标称点结果：$R_T\approx668.0$ kN；不确定传播后 $R_T = 668.5\pm31.2$ kN（直接 $10,000$ 点 LHS 抽样），
即相对标准差约 4.67 %。工程含义：若主机功率裕度只按标称阻力预留，那么 720.6 kN 的 95 % 上界包络
（约 $+7.79 %$ 相对标称）就是必须被裕度覆盖的部分。

## 3 Method

### 3.1 Kernel layout (`backend/services/pce_uq/`)

| module | responsibility | property that is tested |
|---|---|---|
| `basis.py` | orthonormal Legendre / Hermite bases, total-degree and hyperbolic truncation | $E[\phi_i\phi_j]=\delta_{ij}$ so spectral moments need no norm bookkeeping |
| `marginals.py` | uniform / normal / lognormal marginals + isoprobabilistic map | sampling (truncated) and quadrature (untruncated) inverse maps kept separate |
| `quadrature.py` | Gauss tensor grids, Smolyak sparse grids | signed combination weights; exactness degree `2L-1` asserted against closed-form monomial integrals |
| `kernel.py` | projection and least-squares fits, spectral moments, Sobol', quantiles | regression quality via a QR leverage LOO residual (zero extra model runs) |
| `mcbench.py` | direct Monte Carlo reference + acceleration accounting | evaluation-count and wall-clock speed-ups reported separately |
| `spec.py` | case wiring from JSON (dotted paths, direct file load) | no domain content in code |

Closed-form post-processing in the orthonormal basis:

$$\mathbb{E}[y]=c_0,\quad \mathrm{Var}[y]=\sum_{\alpha\neq 0}c_\alpha^2,\quad
S_i=\frac{\sum_{\mathrm{supp}(\alpha)=\{i\}}c_\alpha^2}{\mathrm{Var}[y]},\quad
S_{T,i}=\frac{\sum_{i\in\mathrm{supp}(\alpha)}c_\alpha^2}{\mathrm{Var}[y]}$$

### 3.2 Acceptance criteria as machine decisions

| criterion | how it is decided | threshold |
|---|---|---|
| analytic Sobol' recovery | max abs difference against the published closed-form decomposition | <= 0.05 |
| response moments | relative error against direct Monte Carlo at the stated budget | <= 3 % |
| acceleration | $N_{MC}/N_{PCE}$ black-box evaluations (primary) and measured wall clock on the same scalar interface | >= 100x |
| domain-free mechanism | platform vocabulary scanner over the kernel and runner | 0 hits |

## 4 Results

### 4.1 Analytic case: PCE spectral ANOVA vs closed form

| input | analytic $S_i$ | PCE $S_i$ | abs err | analytic $S_{T,i}$ | PCE $S_{T,i}$ | abs err |
|---|---|---|---|---|---|---|
| `x1` | 0.31391 | 0.31299 | 0.00091 | 0.55759 | 0.55579 | 0.00180 |
| `x2` | 0.44241 | 0.44421 | 0.00180 | 0.44241 | 0.44421 | 0.00180 |
| `x3` | 0.00000 | 0.00000 | 0.00000 | 0.24368 | 0.24279 | 0.00089 |

Configuration: Legendre basis, total degree $p=8$ (165 terms),
tensor quadrature with 729 model evaluations.
**Max absolute error 1.80e-03** against a 0.05 band.

Refinement trajectory on the same card:

| configuration | model evaluations | max abs dS | std rel. error | mean rel. error |
|---|---|---|---|---|
| p=2 | 27 | 2.437e-01 | 44.77 % | 53.0819 % |
| p=4 | 125 | 1.991e-01 | 12.53 % | 1.9759 % |
| p=6 | 343 | 4.536e-02 | 4.78 % | 0.1005 % |
| p=8 | 729 | 1.802e-03 | 1.40 % | 0.0172 % |
| p=10 | 1331 | 2.268e-05 | 1.27 % | 0.0275 % |

![analytic Sobol comparison](fig1_analytic_sobol.png)

![analytic convergence](fig2_analytic_convergence.png)

### 4.2 Propagation case: uncertainty band of the response

| quantity | direct sampling ($N$ = 10,000) | surrogate | relative error |
|---|---|---|---|
| mean [ kN ] | 668.5 | 668.6 | 0.0080 % |
| std [ kN ] | 31.2 | 31.7 | 1.550 % |

High-accuracy anchor: 200,000-point scrambled-Sobol' quasi-Monte-Carlo,
mean 668.6 kN, std 31.3 kN. Against it the surrogate is off by
0.0067 % (mean) and 1.192 % (std), while the
direct baseline itself is off by 0.0013 % and
0.353 %. The surrogate residual is therefore of the same order as
(here 3.4x) the statistical error of the
10^4-sample baseline itself, which is what sets the resolution of this comparison -- the 3 % band is
not being passed by a lucky draw, but it is also not a 10^-3 measurement.

Band (surrogate quantiles vs direct sampling):

| quantile | direct sampling [kN] | surrogate [kN] | relative error |
|---|---|---|---|
| p5 | 618.8 | 616.8 | 0.332 % |
| p50 | 667.9 | 668.4 | 0.088 % |
| p95 | 720.9 | 720.6 | 0.042 % |

Mean $\pm2\sigma$: surrogate
[605.6, 731.6] kN
vs direct sampling
[606.2, 730.9] kN.

![uncertainty band](fig3_uncertainty_band.png)

### 4.3 Uncertainty attribution (spectral ANOVA, no extra model runs)

| input | $S_i$ | $S_{T,i}$ |
|---|---|---|
| `speed_ms` ($V$) | 0.8081 | 0.8081 |
| `cb` ($C_B$) | 0.1520 | 0.1520 |
| `draft` ($T$) | 0.0334 | 0.0334 |
| `beam` ($B$) | 0.0059 | 0.0059 |
| `length` ($L$) | 0.0004 | 0.0004 |
| `lcb_frac` (LCB) | 0.0002 | 0.0002 |

$\sum S_i = 1.0000$ and
$\sum S_{T,i} = 1.0000$, so the interaction
share of the variance in this card is +0.0000.

![attribution](fig4_sensitivity_ranking.png)

### 4.4 Acceleration and the cost/accuracy trade-off

Delivered configuration: smolyak grid, level
2, 28 basis terms,
13 black-box evaluations.

* **evaluation-count speed-up 769x**
  ($N_{MC}/N_{PCE}$, independent of machine speed -- the primary criterion)
* **wall-clock speed-up 327x**
  (same machine, same scalar calling interface, median of 5 runs:
  direct pipeline 1083.8 ms vs
  surrogate pipeline 3.3 ms;
  run-to-run range 87x -
  345x -- wall clock carries machine-load scatter,
  the evaluation-count number above does not)

Full cost/accuracy table on the same card (errors against the high-accuracy anchor):

| configuration | model evaluations | basis terms | mean rel. error | std rel. error | acceleration |
|---|---|---|---|---|---|
| projection p=1 L=1 | 1 | 7 | 0.0872 % | 100.000 % | 10000x |
| projection p=2 L=2 | 13 | 28 | 0.0067 % | 0.817 % | 769x |
| projection p=2 L=3 | 97 | 28 | 0.0016 % | 0.102 % | 103x |
| projection p=3 L=3 | 97 | 84 | 0.0015 % | 0.163 % | 103x |
| projection p=3 L=4 | 533 | 84 | 0.0009 % | 0.134 % | 19x |
| projection p=4 L=4 | 533 | 210 | 0.0010 % | 0.140 % | 19x |
| regression p=2 N=56 | 56 | 28 | 0.0214 % | 0.397 % | 179x |
| regression p=2 N=112 | 112 | 28 | 0.0110 % | 0.324 % | 89x |

![cost accuracy](fig5_cost_accuracy.png)

## 5 What generalises, and what does not

**解析算例的适用边界**

1. 输入全部取连续、单峰、相对不确定度 0.3–2 % 的分布。Ishigami 这类非多项式响应靠阶次收敛达标
   （$p=6$ 进 5 % 带、$p=8$ 达 1.80e-03），说明谱投影的误差来源是截断而不是实现缺陷；
   若把不确定度放大约 10 倍，二次/八阶截断会系统性低估方差，此时必须升阶或改用采样型 GSA。
2. $x_3$ 的一阶指数理论上严格为 0，实测 0.00000。它的小量级偏差来自截断误差而非分组错误
   （同一次分解里总效应 0.24279 与解析 0.2437 一致），因此"一阶≈0 且总效应>0"是比单看一阶更硬的判别式。
3. 张量求积在 $d=3$ 时成本可接受（729 次模型评估），到 $d=6$ 就必须换稀疏网格（见算例 B）；
   本算例的精确度不能外推成"高维同样便宜"。

**传播算例的适用边界**

1. **模型误差不在本单承诺内**：Holtrop–Mennen 回归相对实船/模型试验本身有 5–10 % 量级的模型误差。
   本单量的是"不确定度传播与加速的数值正确性"，不声称标称点 668.0 kN 是该船真值；
   要判模型精度需另做水池试验/CFD 对标（属船舶平台的对标节点）。
2. **输入相关性被忽略**：$L,B,T,C_B$ 来自同一条型线，实际存在弱相关（例如 $C_B$ 与 $L/B$ 的系列化耦合）。
   本算例按独立处理，独立性假设会**高估**方差上界；若引入相关系数，PCE 需改用 Rosenblatt/Nataf 变换。
3. **一次性评估成本决定加速比口径**：本算例黑箱是闭式回归（约 20 µs/次），墙钟加速比 327×
   只有评估次数加速比 769× 的 0.43 倍——差额是代理的固定装配成本
   （网格构造 + 投影求解 + 进程预热）。换成求解器级黑箱（几十毫秒/次以上）时两者趋同。
   本报告两个口径分别列账、各带自己的噪声说明，不混用。
4. **稀疏网格欠定投影**：交付配置为 order-2 基（28 项）配 level-2 网格（13 点），
   点数少于基项数，属于欠定投影；其可用性由两条证据共同保证：求积对总阶 3 精确（单项式积分断言），
   以及加密轨迹（13 → 97 → 533 点，
   相对 2×10⁵ 点 QMC 基准的标准差误差 1.192 % → 0.102 % → 0.140 %，
   第一段降约一个数量级、再加密进入平盘）。即"便宜点已经够用、再花钱买不到什么"，而不是单点侥幸。
5. **二次截断的表达能力上限**：若输入不确定度放大到 10 % 量级（例如实船服役期污底/海况散布），
   阻力响应的高阶项（兴波峰值附近的强非线性）会使二次 PCE 低估方差，必须升到 3–4 阶并重算求积成本。

Generic limitations of the mechanism itself (independent of the case cards):

1. **Signed sparse-grid weights.** Combination weights are negative for part of the
   nodes. A sign slip leaves results that look plausible; hence the exactness of each
   grid level is asserted against closed-form monomial integrals, not assumed.
2. **Projection can be under-determined.** When the sparse grid carries fewer points
   than basis terms the projection is under-determined; it stays usable only because
   the quadrature is exact up to its declared total degree. The refinement trajectory
   in the ledger is the evidence that the delivered point is not a lucky sample.
3. **Truncation vs quadrature mapping.** Sampling truncates the tails so that
   physical inputs stay admissible; quadrature nodes are not truncated so that they
   match the orthonormal measure. Mixing the two silently breaks the weights.
4. **Two acceleration definitions.** Evaluation-count speed-up is the right measure
   for solver-level black boxes; wall-clock speed-up additionally pays the surrogate's
   fixed assembly cost. Both are reported, never merged.
5. **Scope.** The kernel handles smooth responses in moderate dimension
   ($d\lesssim10$). It is not a substitute for sampling-based GSA on non-smooth or
   strongly interacting responses, and it does not certify the black box itself.

## 6 Error verdict (item by item, explicit)

| case | criterion | measured | threshold | verdict |
|---|---|---|---|---|
| `ishigami_sobol` | `sobol_max_abs_error` | 0.00180178 | 0.05 | PASS |
| `resistance_six_parameter` | `mean_rel_error` | 8.00586e-05 | 0.03 | PASS |
| `resistance_six_parameter` | `speedup_model_evaluations` | 769.231 | 100 | PASS |
| `resistance_six_parameter` | `speedup_wall_clock` | 327.408 | 100 | PASS |
| `resistance_six_parameter` | `std_rel_error` | 0.0154988 | 0.03 | PASS |

* analytic Sobol' recovery: **1.80e-03** max abs error (band 0.05) -> **< 5 % satisfied**.
* response mean relative error **0.0080 %**, standard deviation relative error
  **1.550 %** -> **< 3 % satisfied** (against the high-accuracy anchor:
  0.0067 % / 1.192 %).
* acceleration **769x** (evaluations) and
  **327x** (wall clock) -> **> 100x satisfied**.
* overall: **all PASS**; no honest-FAIL needs to be declared.

## 7 Reproduce / audit

```
# 1) machine loop: cases -> criteria -> ledger
python -m tools.uq_bench.run_case --all --out-dir artifacts/uq_pce_m9011 --reps 3 --sweep
# 2) regression tests
python -m pytest tests/test_pce_uq_kernel_m9011.py -q
# 3) figures + this document (all numbers injected from the ledger)
python -m tools.uq_bench.make_report --ledger artifacts/uq_pce_m9011/ledger.json \
    --out-dir artifacts/uq_pce_m9011 --doc docs/verification/pce_uq_framework_m9011/REPORT.md
```

Ledger fields per case: `spec` (with sha256), `nominal_inputs` / `nominal_response`,
`pce` (configuration + design diagnostics), `sobol`, `comparison` (baseline, sampling
error, acceleration, timing, sample histogram), `band`, `checks` (machine verdicts),
`sweep` (cost/accuracy).

## 8 Delivered artefacts

| path | content |
|---|---|
| `backend/services/pce_uq/` | domain-free non-intrusive PCE kernel (bases, marginals, quadrature, kernel, MC reference, case wiring) |
| `config/uq_cases/*.json` | case cards: `ishigami_sobol`, `resistance_six_parameter` (injected physics) |
| `config/uq_cases/*.intro.md`, `*.caveats.md` | case-card narratives consumed by this renderer |
| `tools/uq_bench/run_case.py` | machine-closed-loop runner (criteria + ledger) |
| `tools/uq_bench/references.py` | closed-form analytic references |
| `tools/uq_bench/make_report.py` | figures + this document (numbers injected) |
| `tests/test_pce_uq_kernel_m9011.py` | 18 pytest regression cases |
| `artifacts/uq_pce_m9011/` | `ledger.json`, per-case JSON, five figures |
