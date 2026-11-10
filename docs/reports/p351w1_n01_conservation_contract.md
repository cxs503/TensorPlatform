# 跨求解器守恒契约与耦合循环收敛证书（节点 p351w1_n01）

**平台机制，零领域语义**：本节点不引入任何域词条——交换量的含义只通过
`contracts/conservation_law.schema.json` 的实例进入，`quantity_kind` 在平台代码里不透明。

---

## ① 案例介绍（物理背景 / 几何 / 参数）

耦合系统的“守恒”与“收敛”被拆成两件可机器裁决的事：

1. **界面守恒**：两个求解器在共享面上各自给出法向通量 $F_A$、$F_B$，并用界面算子
   $\mathcal{T}$ 把 $F_A$ 传到对方离散面上。门禁量是 $\|F_A - F_B\|/\|F_A\|$，并对照闭式解。
2. **耦合循环**：定点问题 $x = G(x)$（线性化即 $x = Ax+b$）必须输出证书
   $\mathrm{Certificate}\{\mathrm{converged},\ \hat\rho,\ \mathrm{iterations}\}$，发散须在上限前判定。

**验证夹具（制造解）**：二维稳态扩散，两个子域在 $x=0.5$ 处以平面界面相接，$k_L=1$、$k_R=4$：

$$\varphi_L=\sin(2x)\cos(\pi y),\qquad
\varphi_R=\bigl(\alpha\sin(2x)+\beta\bigr)\cos(\pi y),\qquad
\alpha=\frac{k_L}{k_R},\ \ \beta=\sin(1)(1-\alpha)$$

该构造使界面**值连续**且**法向通量连续**，闭式界面通量为
$q^*(y)=-2k_L\cos(1)\cos(\pi y)$，因此“两侧一致性”与“对解析解的精度”都可测，无需人工容差。

| 参数 | 值 | 说明 |
|---|---|---|
| 域 | $[0,1]^2$，界面 $x=0.5$ | 两子域共享一个平面界面 |
| 导热系数 | $k_L=1.0$，$k_R=4.0$ | 界面谐均值 $k_h=2k_Lk_R/(k_L+k_R)=1.6$ |
| 离散 | 单元中心有限体积，界面面单值谐均导度 | 全局一张矩阵，即真正的耦合解 |
| 壁面 | Dirichlet（制造解真值），三点壁面导数 | 半格形式只有一阶通量，会把阶封在 1 |
| 网格 | 每侧 $n\times n$，$n\in\{48,96,192,256\}$ | 界面通量是 $n$ 段上的分布 |
| 判据 | $\|F_A-F_B\|/\|F_A\|\le10^{-4}$，$\|F_A-q^*\|/\|q^*\|\le10^{-4}$ | 负控：抖动通量必 FAIL |

## ② 怎么做的（方法 / 流程 / 关键设置 / 工具链）

| 路径 | 作用 |
|---|---|
| `contracts/conservation_law.schema.json` | 守恒契约 schema（draft-07，`additionalProperties:false` 全封闭） |
| `contracts/conservation_law.example.v1.json` | 示例契约实例（`quantity_kind` 对平台不透明） |
| `tools/coupling/conservation_check.py` | 残差计算器 + 三种守恒传输算子 + 阶数证书 + 制造解夹具 + 灵敏度 + 契约校验 |
| `tools/coupled_loop.py` | 通用定点迭代器（Jacobi / Gauss-Seidel / Aitken），输出 `Certificate` |
| `tests/test_conservation_check.py`、`tests/test_coupled_loop.py` | 62 项 pytest，逐条判据配负控 |
| `tools/conservation_contract_evidence.py` | 证据生成器（本页数字全部由它程序化注入） |

**界面算子**（一维切向，重叠积分解析求积 → 零阶矩机器精度守恒）：
重构 $p_i(\xi)=f_i+s_i\xi+c_i\left(\xi^2-h_i^2/12\right)$，$\xi=x-x_i$，目标单元取
$\frac{1}{|D|}\int_D p\,dx$。

| 算子 | $s_i$ | $c_i$ | 设计阶 |
|---|---|---|---|
| `donor_cell` | 0 | 0 | 1 |
| `linear_reconstruction` | 中心差分 | 0 | 2 |
| `quadratic_projection` | 中心差分 | 二阶差分/2 | 3 |

端部单元用三点公式（$s_0=(4f_1-3f_0-f_2)/(2h)$、$c_0=(f_2-2f_1+f_0)/(2h^2)$）：两点差分会在端部留
$O(h^2)$ 局部误差，把高阶算子的可测阶拖到约 2.55（实测），见注意点。

**阶数测量**：源网格 $n$、目标网格 $\mathrm{round}(1.5n)+1$（永不整除），两侧取精确单元平均
（8 点 Gauss-Legendre），残差即传输误差，观测阶取 $\log R$–$\log h$ 最小二乘斜率。

**耦合循环**：Jacobi $x_{k+1}=x_k+\omega_k(G(x_k)-x_k)$；Gauss-Seidel 按块顺序用最新值扫一遍再松弛；
Aitken（Irons-Tuck）$\omega_k=-\omega_{k-1}\frac{r_{k-1}\cdot\Delta r}{\|\Delta r\|^2}$，
$\Delta r=r_k-r_{k-1}$ —— 对线性映射该式一步给出最优 $\omega=1/(1-\rho)$（实现夹在 $[10^{-3},5]$）。
发散判据用**绝对**残差增长拟合 $\hat\rho$，$\hat\rho>\rho_{\max}$ 连续 2 次即判 DIVERGED。

## ③ 计算结果（数字表，程序化注入）

**表 1 · 界面算子观测阶**（目标网格 $\mathrm{round}(1.5n)+1$）

| 算子 | 设计阶 | 残差序列 | 观测阶 | 判定 |
|---|---|---|---|---|
| `donor_cell` | 1 | 0.013, 0.006, 0.003, 0.002 | 1.034 | PASS |
| `linear_reconstruction` | 2 | 2.646e-04, 6.559e-05, 1.622e-05, 4.026e-06 | 2.036 | PASS |
| `quadratic_projection` | 3 | 4.494e-05, 5.316e-06, 6.529e-07, 8.100e-08 | 3.072 | PASS |

负控（固定幅度抖动算子）：残差 0.008 → 0.008（不随网格下降），观测阶 **-0.013** —— 阶数门禁可失败。

**表 2 · 制造解扩散夹具**（每侧 $n\times n$ 单元）

| n | $\Delta x$ | 两侧残差 | 解析误差(左) | 解析误差(右) | 界面值跳变 | 抖动负控 |
|---|---|---|---|---|---|---|
| 48 | 0.01042 | 2.811e-05 | 8.082e-04 | 7.815e-04 | 4.259e-05 | 0.245 |
| 96 | 0.00521 | 3.519e-06 | 2.279e-04 | 2.247e-04 | 1.041e-05 | 0.257 |
| 192 | 0.00260 | 4.400e-07 | 6.740e-05 | 6.705e-05 | 2.572e-06 | 0.256 |
| 256 | 0.00195 | 1.856e-07 | 4.126e-05 | 4.112e-05 | 1.443e-06 | 0.256 |

细化观测阶（48→256）：两侧残差 **2.999**，解析误差 **1.777**。

**表 3 · 耦合循环证书**（$\rho(A)=0.95$；发散系统 $\rho=1.2$）

| 方案 | 状态 | 迭代数 | $\hat\rho$ | 末残差 | 末 $\omega$ |
|---|---|---|---|---|---|
| jacobi | CONVERGED | 414 | 0.9500 | 9.944e-11 | 1.000 |
| gauss_seidel | CONVERGED | 203 | 0.9025 | 9.220e-11 | 1.000 |
| aitken | CONVERGED | 114 | 0.7973 | 7.756e-11 | 5.000 |
| divergent | DIVERGED | 3 | 1.2000 | 0.875 | 1.000 |

Aitken 相对 Jacobi 的迭代下降 **72.5%**；GS 的 $\hat\rho=0.9025\approx\rho_J^2=0.9025$（两分块相容次序）；
解与直接解偏差：Jacobi 4.250e-09、GS 6.052e-09、Aitken 8.095e-09。

**表 4 · 验收判据**

| 判据 | 实测 | 阈值 | 判定 |
|---|---|---|---|
| a_interface_residual | 4.4002e-07 | 0.0001 | PASS |
| a_analytic_error | 6.7400e-05 | 0.0001 | PASS |
| a_negative_control_jitter | 0.2563 | 0.01 | PASS |
| b_order_linear | 2.0362 | 0.95 | PASS |
| b_order_quadratic | 3.0721 | 1.95 | PASS |
| b_negative_control_noisy | -0.0125 | 1.95 | PASS |
| c_divergence_iterations | 3.0000 | 10 | PASS |
| c_divergence_rho | 1.2000 | 1.0 | PASS |
| d_aitken_iteration_reduction | 0.7246 | 0.4 | PASS |
| e_sensitivity_relative_error | 6.6021e-13 | 0.02 | PASS |
| f_pytest_items | 62.0000 | 20 | PASS |
| g_domain_scan_hits | 0.0000 | 0 | PASS |

**表 5 · 契约负控**（严格校验命中错误数）

| 篡改用例 | 命中错误数 | 首个错误 |
|---|---|---|
| unknown_key | 1 | unknown keys: ['tolerance_hack'] |
| unknown_operator | 1 | unknown interface operator kind: 'magic_transfer' |
| zero_tolerance | 1 | residual.relative_tolerance must be in (0, 1] |
| bad_levels | 1 | refinement_gate.levels must list 3..8 integers |
| bad_scheme | 1 | loop_gate.scheme must be jacobi or gauss_seidel |

## ④ 结果图

* **fig1_observed_order** —— 三种界面算子的残差–网格宽双对数曲线与 $h^1/h^2/h^3$ 参考斜率；红色点线为
  固定幅度抖动的负控（残差停滞，观测阶≈0）。
* **fig2_interface_flux** —— 左：两侧重建通量与闭式解 $q^*(y)$ 对比（192×192）；右：逐点通量误差
  （48 与 192 两套网格）与 $10^{-4}$ 门禁线，可见二阶下降。
* **fig3_coupled_loop** —— 左：$\rho=0.95$ 系统 Jacobi/GS/Aitken 的相对残差历史；右：$\rho=1.2$
  系统在第 3 次迭代被判 DIVERGED（远在 10 次预算内）。

## ⑤ 注意点（踩过的坑 / 局限 / 适用范围）

**实现期抓到并修掉的四个真实缺陷（均有测试锁死）**

1. **界面面导度必须单值**：只有左单元用谐均值、右单元用自身 $k$ 时，界面缝上出现“通量汇”，
   精确解在该行的相对截断残差达 108%，解只有一阶精度。
2. **Dirichlet 半格形式只有一阶通量**：壁面用 $(\varphi_0-\varphi_w)/(h/2)$ 时解析误差卡在 $10^{-2}$
   量级不降；改三点壁面导数后回到二阶（表 2）。
3. **稀疏装配错位**：`rows/cols/vals` 长度不一致时 `coo_matrix` 静默错位，解爆到 $10^{25}$。
4. **相对残差不能当收缩因子**：发散系统的 $\|r\|/\|G(x)\|$ 有界（$\to|1-1/\rho|$），会把
   $\rho=1.2$ 读成 $\approx0.17$ 的“收敛”；改用绝对残差增长后第 3 次即判 DIVERGED。

**适用边界与局限（不粉饰）**

* 界面算子只实现了一维（切向）版本，非匹配构造是“网格数按比例错位”；二维/三维面算子需扩展成
  张量积或真实面网格交叠。
* 夹具的端壁取**解析 Dirichlet 值**，目的是隔离算子传输误差与求解器误差；它验证“耦合离散 + 侧向
  重建通量”的一致性，不是完整外边界问题。
* Aitken 的 $\omega$ 夹在 $[10^{-3},5]$，对 $\rho\to1$ 不会给极端值，故 72% 是夹逼后的下界收益。
* 契约的 `order_class` 是声明值；平台只裁决“观测阶 ≥ 门限”，不反推声明值最优。

## ⑥ 误差判定（逐项 vs 基准，显式回答“是否 < 3%”）

| 关键量 | 基准 | 误差 | 是否 < 3% |
|---|---|---|---|
| 界面通量（左/右） | 闭式 $q^*(y)$ | 6.7400e-05 / 6.7054e-05（= 0.007%） | **是** |
| 两侧通量一致性 | 两侧独立重建 | 4.4002e-07（0.0000%） | **是** |
| 界面值连续性 | 制造解 | 2.5724e-06 | **是** |
| 增益灵敏度 | 解析 $dR/ds$ | 相对误差 6.602e-13（容差 2%） | **是** |
| 定点解 | 直接解 $(I-A)^{-1}b$ | 偏差 8.095e-09（绝对） | **是** |
| 算子阶数 | 设计阶 1/2/3（非误差量） | 1.03/2.04/3.07 | 判据 (b) 全达标 |

负控逐项：抖动通量残差 0.256（>1e-2 → FAIL 成立）；抖动算子观测阶 -0.013（<1.95 → FAIL 成立）；
$\rho=1.2$ 系统第 3 次迭代判 DIVERGED（≤10 成立）。

**诚实声明**：本节点判据全部实测通过（`artifacts/conservation_contract_p351w1_n01/acceptance.json`
中 `all_passed=true`）。上述四项局限未在本节点解决，属后续节点范围。

---

**审计链**：`acceptance.json` ← `order_study.json` / `diffusion_fixture.json` /
`loop_certificates.json` / `contract_validation.json` ← `tools/conservation_contract_evidence.py`
（重跑即复现，零手抄）。测试：`python -m pytest tests/test_conservation_check.py tests/test_coupled_loop.py -q`
→ 62 passed。
