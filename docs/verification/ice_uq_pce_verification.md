# 冰阻力 UQ（PCE/Smolyak）引擎验证欠账清偿 —— 0 测试模块的解析锚定与三处静默缺陷

**任务** m1972（自找活 · infrastructure 席 tp-infra-1）｜**仓** TensorPlatform｜**被测件** `backend/services/ice_resistance_uq.py`（`/api/v1/ice-uq`）

**产物** `tools/verif/uq/anchors.py`（独立参考层）、`tools/verif/uq/verify_uq.py`（审计器）、`tests/test_ice_uq_verification.py`（16 passed + 3 xfail-strict 缺陷钉）、`artifacts/uq_pce/{numbers.json,figures.json,fig*.png}`

**复现** `python3 -m tools.verif.uq.verify_uq`（约 1 min）· `python3 -m pytest tests/test_ice_uq_verification.py -q`

---

## ① 案例介绍（物理背景 / 几何 / 参数表）

冰区航行是船舶总体性能的独立学科分支，而平台把它的一致性押在一层**不确定度量化**上：`/api/v1/ice-uq` 用 Polynomial Chaos Expansion（PCE）+ Smolyak 稀疏网格 + Monte-Carlo 参考，给出平冰（level ice）阻力的均值、标准差、95% 区间与 Sobol 敏感性排序。问题在于：**该模块此前零回归测试**（`grep -rl ice_resistance_uq tests backend/tests` → 0 文件），即「谱投影是否正确、稀疏网格是否够阶、区间是否覆盖」这三件事在仓内没有任何机器判据。

物理模型为 Lindqvist (1989) 平冰阻力（挤压 + 弯曲 + 浸没三项，含 m1720 修复的 `(1 + 1.4 V/√(g h))` 速度乘子），基准船 MV Arctic 的标定参数取自模块内 `SHIP_TRIAL_DATA`。

| 参数 | 名义值 | 相对标准差 | 分布（声明） |
|---|---|---|---|
| 冰抗压强度 σ_c [MPa] | 1.5 | 20% | normal |
| 冰厚 h [m] | 1.0 | 15% | normal |
| 摩擦系数 μ | 0.15 | 25% | normal |
| 航速 V [m/s] | 3.0 | 10% | normal |

船体：L = 222.5 m，B = 32.2 m，T = 13.5 m，C_b = 0.82，艏柱角 30°，标定因子 k = 0.7530（m1720 记录在册）。试验均值 4800.0 kN。

## ② 怎么做的（方法 / 流程 / 工具链）

**参考层与被测件零共享代码。** `tools/verif/uq/anchors.py` 从教科书定义重算全部量：

1. **精确矩** `normal_moment(k)`、`uniform_moment(k)`——任何求积规则的唯一判据。
2. **求积节点**：用正交多项式三项递推的 Jacobi 矩阵特征分解（Golub–Welsch）独立构造 Gauss–Hermite（概率测度，Σw = 1）与 Gauss–Legendre（Lebesgue 测度，Σw = 2，乘 1/2 才是概率测度）。被测件是手写 Jacobi 矩阵，本层是 numpy 特征分解，属两条独立代码路径。
3. **独立 PCE**：在**归一化**基（He_n/√(n!)、√(2n+1)P_n）上做**全张量** Gauss 投影，因此 `E[ψ_αψ_β] = δ_αβ`，统计量就是系数平方和，不需要被测件那套 `α!` 归一化记账。张量规则每维 `p+1` 点、精确到 2p+1 阶，多维度由构造即知，不依赖稀疏网格理论。
4. **无截断 Monte-Carlo**（向量化，200k 样本）：被测件的 MC 参考会**悄悄截断**输入（σ_c ≥ 0.3、h ≥ 0.1、μ ∈ [0.02, 0.5]、V ≥ 0.5），而 PCE 从不截断——两者在解不同的测度。
5. **判据**：Smolyak 层级 q 精确到总阶 2q+1，故总阶 p 的投影必须满足 `2p ≤ 2q+1`，即 **max_level ≥ max_poly_order + 1**（Smolyak 1963；Novak & Ritter 1996）。

工具链：`numpy`（特征分解/向量化 MC）+ `matplotlib(Agg)`；运行 12 s；无额外依赖，可离线复跑。

## ③ 计算结果（数字表，全部由脚本注入）

### 3.1 求积规则自检（被测件 vs 精确矩）

| n 点 Gauss–Hermite | Σw | 阶 ≤ 2n−1 的最大相对误差 |
|---|---|---|
| 1 | 1.000000000000000 | 0.00e+00 |
| 2 | 1.000000000000000 | 2.22e-16 |
| 3 | 1.000000000000000 | 1.33e-15 |
| 4 | 1.000000000000000 | 1.07e-14 |
| 5 | 0.999999999999999 | 6.25e-13 |
| 6 | 1.000000000000000 | 1.00e-11 |

| Smolyak（dim=4） | 节点数 | Σw | 实测精确总阶 | 理论 2q+1 |
|---|---|---|---|---|
| level 1 | 1 | 1.000000000000 | 1 | 1 |
| level 2 | 9 | 1.000000000000 | 3 | 3 |
| level 3 | 45 | 1.000000000000 | 5 | 5 |
| level 4 | 165 | 1.000000000000 | 7 | 7 |

### 3.2 制造解（解析可知的 PCE：N(1,1) 输入）

| 模型 | 引擎均值 | 解析均值 | 引擎方差 | 解析方差 | 均值相对误差 | 方差相对误差 | 代理复现最大误差 |
|---|---|---|---|---|---|---|---|
| linear | 6.66133814775e-16 | 0 | 2 | 2 | 6.66e-16 | 1.33e-15 | 3.55e-15 |
| quadratic | 2 | 2 | 4 | 4 | 7.77e-16 | 1.67e-15 | 6.22e-15 |
| interaction | 6.66133814775e-16 | 0 | 3 | 3 | 6.66e-16 | 1.18e-15 | 3.55e-15 |

### 3.3 真实冰阻力模型（MV Arctic，与独立张量投影比较）

独立参考（4⁴ = 256 节点张量 Gauss）：均值 4827.3267 kN，标准差 1117.0381 kN，方差 1247774.1731 kN²。

| max_level / 总阶 p | 引擎评估次数 | 需 level ≥ p+1 | 混叠? | 均值相对误差 | 标准差相对误差 |
|---|---|---|---|---|---|
| 3 / 3 | 45 | 4 | **是** | 2.21e-08 | 4.79e-05 |
| 4 / 3 | 165 | 4 | 否 | 1.70e-15 | 1.10e-08 |
| 5 / 3 | 494 | 4 | 否 | 1.07e-09 | 1.42e-08 |
| 3 / 5 | 45 | 6 | **是** | 2.21e-08 | 8.78e-01 |

### 3.4 生产管线（`run_ice_resistance_uq`，max_level=4 / p=3）

| 量 | 值 |
|---|---|
| 冰阻力均值 | 4827.33 kN |
| 冰阻力标准差 | 1117.04 kN |
| 95% 概率带 | [2638.0, 7016.7] kN |
| 引擎自报带内覆盖率 | 94.99% |
| 引擎 vs 引擎 MC（均值/标准差） | 0.103% / 0.592% |
| Sobol 一阶和 / 总阶和 | 0.9840 / 1.0161 |
| 模型评估次数 | 165 |

| Sobol 一阶 | 冰抗压强度 | 冰厚 | 摩擦系数 | 航速 |
|---|---|---|---|---|
| 引擎 | 0.2261 | 0.6588 | 0.0802 | 0.0189 |
| 独立参考 | 0.2261 | 0.6588 | 0.0802 | 0.0189 |

### 3.5 Monte-Carlo 一致性（200k 样本）

| 量 | 无截断参考 MC | 引擎 MC（含截断） | 相对差 |
|---|---|---|---|
| 均值 [kN] | 4827.047 | 4828.921 | 0.039% |
| 标准差 [kN] | 1114.613 | 1121.404 | 0.609% |

参考 MC 自身的 3σ 标准误：均值 0.155%，标准差 0.474% —— 上表相对差在此带宽内即视为一致。

## ④ 结果图

![fig1_quadrature_exactness.png](fig1_quadrature_exactness.png)

*Engine quadrature vs exact Gaussian moments: Gauss-Hermite is exact to machine precision and the Smolyak rule reproduces its theoretical exactness degree 2q+1 (dim 4).*

![fig2_pce_aliasing.png](fig2_pce_aliasing.png)

*Standard deviation of ice resistance: correct-level configurations sit within the 3 % band of an independent tensor-Gauss projection; configurations with max_level < max_poly_order+1 are aliased and drift by tens of percent.*

![fig3_sobol.png](fig3_sobol.png)

*First-order Sobol indices, engine vs independent reference: ice thickness dominates, then ice strength; agreement within 0.1 index points.*

![fig4_uniform_defect.png](fig4_uniform_defect.png)

*Declaring a uniform input is accepted without error but integrated against a standard normal: mean off by a factor 6, variance by a factor 67.*

## ⑤ 注意点（踩坑 / 局限 / 适用范围）

**三处静默缺陷（本次审计实测，均已用 `xfail(strict=True)` 钉死，修好即翻红提醒摘标）**

1. **D1 分布声明被静默忽略。** `UncertainInput(distribution="uniform", lower=-1, upper=1)` 被原样接受，实际仍按 `normal(mean=nominal, std=nominal×std_relative)` 积分：X~U(−1,1) 时 E[X²] 应为 1.333333，引擎给 2.000000（相对误差 50%）；Var[X²] 应为 1.422222，引擎给 6.000000（322%）。本次量测取平移后的 U(0,2)（名义值 1.0）以**隔离**缺陷 D3——若名义值为 0，D3 会先把标准差压成 0，测到的将是「全零」而非 D1 的错分分布。`lower`/`upper` 两个字段全代码库无引用。
2. **D2 混叠静默出数。** 生产默认 `(max_level=3, max_poly_order=3)` 已经越界（需 level ≥ 4）：level 3 精确到 5 阶，而 3 阶投影要 6 阶。真实模型上`(3,5)` 配置的标准差为 2097.56 kN，比收敛值 1117.04 kN 高 87.8%，引擎不告警、不拒算。
3. **D3 相对标准差参数化退化。** `std = nominal × std_relative`，因此 `nominal=0` 时 输入标准差为 0.0——PCE 退化成确定性模型并返回全零系数，无异常；同时无法声明「绝对标准差」，零均值输入无法表达。
4. **D4 MC 与 PCE 解不同测度**（引擎 MC 截断输入、PCE 不截断）：实测两套 MC 的均值/标准差相对差见 3.5 节，本例截断阈值在 3σ 之外，影响 0.04% / 0.61%（可忽略但不自洽）。

**局限（诚实声明）**

* 本参考层验证的是**数值机器（谱投影 + 稀疏网格 + 统计量）**，不是 Lindqvist 物理模型本身；后者证据级仍为 WITHHELD（m1720：留一法最大误差 22.9%，仅 MV Arctic 进入 15% 带）。
* 因此本报告的 3% 判据只对「同一模型的 UQ 统计量」成立，不能外推成「冰阻力预测精度」。
* 参考层用**全张量** Gauss 而非稀疏网格：它能给出无混叠真值，但不构成对稀疏网格节点数优势的评价。
* `ci_coverage` 是「输出分布落在 ±1.96σ 带内的质量」，不是频率派置信区间的覆盖率；本例 94.99% 只因输出近似对称，不可当作普适保证。

**修复处方（不在本单范围：被测件属船舶产品代码，按 owner 仓规则不在 TensorPlatform 落改）**

1. D1：`build_pce` 按 `distribution` 选择 Askey 对应（Xiu & Karniadakis 2002）——uniform → Gauss–Legendre 节点 + Legendre 基 + 范数 `1/(2n+1)`；或至少 `raise ValueError`，禁止静默。
2. D2：`build_pce` 入口强制 `max_level ≥ max_poly_order + 1`（或自动抬层并回报 `effective_level`）。
3. D3：`UncertainInput` 增加绝对 `std` 字段并与 `std_relative` 互斥。
4. D4：MC 参考与 PCE 使用同一（不截断）测度，截断如需保留则同时施加于 PCE 映射。

## ⑥ 误差判定（关键量 vs 基准，逐项回答「是否 < 3%」）

| 关键量 | 基准（来源） | 引擎值 | 相对误差 | < 3%？ |
|---|---|---|---|---|
| GH 求积（n ≤ 6，阶 ≤ 2n−1） | 精确正态矩 | — | ≤ 1.0e-11 | ✅ 是（机器精度） |
| Smolyak 节点权（Σw） | 1（概率测度） | — | ≤ 1e-12 | ✅ 是 |
| Smolyak 精确阶 | 理论 2q+1 | 实测一致 | 0（阶数相等） | ✅ 是 |
| 制造解均值/方差（3 例） | 解析闭式 | — | ≤ 1.7e-15 | ✅ 是 |
| 制造解 Sobol | 解析 1/3、1/2 | — | ≤ 1e-9 | ✅ 是 |
| 冰阻力**均值**（(4,3) vs 独立投影） | 4827.33 kN（独立张量投影） | 4827.33 kN | 1.70e-13% | ✅ 是 |
| 冰阻力**标准差**（(4,3) vs 独立投影） | 1117.04 kN | 1117.04 kN | 1.10e-06% | ✅ 是 |
| Sobol 一阶（4 输入，最大绝对差） | 独立投影 | — | max |ΔS| = 0.0000（索引单位） | ✅ 是（< 0.02 索引点） |
| MC 均值（引擎 vs 无截断 MC） | 4827.05 kN | 4828.92 kN | 0.039% | ✅ 是 |
| MC 标准差 | 1114.61 kN | 1121.40 kN | 0.609% | ✅ 是 |
| **混叠配置 (3,5) 标准差** | 1117.04 kN（level 6/p5 收敛） | 2097.56 kN | **87.8%** | ❌ **否（缺陷 D2）** |
| 生产默认 (3,3) 标准差 | 1117.04 kN | 1116.98 kN | 0.00%（越界但影响小：模型近线性） | ⚠️ 结构上越界 |
| **uniform 输入 E[X²]** | 1.333333（解析） | 2.000000 | **50%** | ❌ **否（缺陷 D1）** |
| **uniform 输入 Var[X²]** | 1.422222（解析） | 6.000000 | **322%** | ❌ **否（缺陷 D1）** |

**结论**：引擎的**谱数值机器在正确配置下是 REAL**（均值/标准差/敏感性与独立张量投影一致到 1.1e-06% 量级，远优于 3%），但它**缺三处前置校验**，使得非法/越界配置返回看似正常的数字——证据分级因此从「REAL（无保留）」降为 **REAL + 三处静默失败面（D1/D2/D3 未修）**。本单不改船舶产品代码（仓规则），缺陷以 xfail-strict 钉死并给出可直接落地的处方。

*数字来源：`artifacts/uq_pce/numbers.json`（12 s 全量运行，由 `tools/verif/uq/verify_uq.py` 生成，本文件由 `gen_doc.py` 程序化注入，零手抄）。*
