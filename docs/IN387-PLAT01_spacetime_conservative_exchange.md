# IN387-PLAT01 跨求解器时空守恒场交换：时间插值算子 + 高阶矩精确重映射

> 平台仓 TensorPlatform · 领域无关机制（零领域语义）· 节点 `IN387-PLAT01` · 判据机器裁决 **PASS** · commit `b763ff74f` · 生成 2026-11-11T01:05:20.852898+00:00

## 0. 摘要

两个求解器以不同时间步进时，场交换必须回答两个问题：(i) 目标求解器在其自己的时刻要的值，源求解器根本没算过；(ii) 把源网格的场搬到目标网格，质量守恒容易，**质心与惯性张量**很难。本节点在 TensorPlatform 的耦合层新增三个纯机制模块，并全部用机器判据验收：

- **时间插值算子** `backend/coupling/temporal_interp.py`：nearest/linear/cubic，任意（非均匀、不可通约）时间网格；同刻请求退化为恒等（最大绝对误差 0.000e+00）；在耦合中真正生效的一阶时间精度轨迹上实测收敛阶 **1.001**（规格 1±0.2），光滑数据上 linear **1.998** / cubic **3.998**。
- **高阶矩精确重映射** `backend/coupling/moment_remap.py`：局部线性重构 + 精确重叠几何 + Lagrange 乘子全局矩约束（0/1/2 阶全单项式，6×6 精确解，非迭代）。一阶矩残差 1.393e-16、质心漂移 1.246e-16 cell、二阶矩残差 7.957e-16、惯性张量残差 1.521e-16；100 次串联漂移 1.481e-14。
- **时空组合算子** `backend/coupling/spacetime_exchange.py`：`R(A->B) ∘ I_t`，异构时钟下能量残差最差 1.083e-05（判据 0.01），且与粗细时钟比无关（比值 2..32 扫掠的相对离散度 1.000e+00）。
- **负控**：关掉矩约束后一阶矩残差 2.054e-04、二阶矩残差 5.790e-03，分别为 1e-6 判据的 205× / 5790× —— 说明是**矩约束**在供能，不是场景本身凑巧。

判据全绿：C1–C8 = C1:PASS, C2:PASS, C3:PASS, C4:PASS, C5:PASS, C6:PASS, C7:PASS, C8:PASS；pytest 57 项全绿（`57 passed in 32.62s`）。

## 1. 案例介绍

### 1.1 物理背景

分区耦合（不同求解器各管一块连续性/离散性不同的子域）在工程上是常态：一侧显式、时间步受稳定性限制；另一侧隐式、时间步受精度限制。两者的时间网格**互不可通约**，空间网格**不重合**。于是每一次交换都在做两件事：沿时间把源端快照插值到目标端需要的时刻，沿空间把场从源网格搬到目标网格。工程上有两个硬约束：

1. **不能凭空造/消守恒量**：质量（0 阶矩）必须精确守恒，否则长时间积分会漂；
2. **不能引入质心/惯性张量的系统性漂移**：任何带旋转自由度或力矩评估的下游量都对一阶矩、二阶矩敏感，传统的守恒投影（L2/donor-cell）恰好只保 0 阶矩。

本节点把这两件事做成可独立验收的算子，并且**只做机制**：网格、数组、体积权重，没有任何领域词条。

### 1.2 几何与算例配置（全部可由 `tools/run_in387_plat01_acceptance.py` 复算）

| 项 | 值 | 说明 |
|---|---|---|
| 计算域 | 单位方 [0,1]² | 两侧覆盖缺陷 max 5.6e-15（fail-closed 阈值 1e-8） |
| 源网格 | 8×8 张量积四边形，`x_i = (i/8)^1.15` | 非均匀间距（拉伸），质心不与目标对齐 |
| 目标网格 | 8×8 扰动四边形，内点抖动 50% 网格距，seed=3 | 非正交、质心偏离任何均匀网格 |
| 场 A（线性） | `f = 1 + 0.5x − 0.25y` | 检验算子对自身重构空间的精确性 |
| 场 B（光滑） | 高斯凸包 σ=0.18，底值 0.1 | 有界性/矩约束的主算例（离边界有余量） |
| 场 C（台阶） | `f = 1 if x<0.5 else 0` | 触界算例：检验有界性与矩精确性的**冲突处理** |
| 时间网格 | 源 1025 点/T（dt_f = 1/1024），目标每 16 步一次且相位偏移 0.37 | 异构时钟，请求刻意不落在样本上 |
| 时间信号 | `cos(2πt/T)` / 一阶显式 Euler 轨迹 / 分段线性（角点对齐样本） | 分别测 2 阶、1 阶、精确性 |
| 判定阈值 | C1 1e-12 · C2 1e-6 & 1e-3 cell · C3 1e-6 & 1e-4 · C5 1e-12 · C6 0.01 | 见 §2.4 定义 |

## 2. 方法与工具链

### 2.1 时间插值算子

请求时刻 `t'` 落在源快照 `(t_k, f_k)` 之间，输出 `f(t') = Σ_k w_k(t') f_k`。

- `nearest`：零阶保持（一阶精度）；`linear`：任意非均匀节点上的两点 Lagrange（数据光滑时二阶）；`cubic`：**四点 Lagrange 在任意非均匀节点上的闭式权重**（光滑数据四阶，且不做样条全局解，因此无需求解三对角系统、天然逐点并行）。
- **节点恒等**：请求落在样本上（容差 `identity_tol`）时**直接返回该快照的拷贝**，误差恒为 0，而不是靠插值公式凑出 1.0。这是「共享时钟时算子必须退化为恒等」的机器可证形式。
- **单位分解**：Lagrange 权重满足 `Σ w_k = 1`（机器精度），因此空间均匀场被精确复现、空间均匀的守恒量被精确守恒；这是时间层质量守恒的充分条件，也是 C6 里 mass 残差 ~1e-16 的原因。
- **越界 fail-closed**：默认 `extrapolation='fail'`（问超出样本窗口的时刻 = 错误），可选 `hold` / `linear` 显式外推；cubic 在非均匀节点上权重可能为负，故提供可选 `bounds` 限幅并**上报被限幅比例**（不静默）。

### 2.2 高阶矩精确重映射（本节点的核心算子）

五步，全部是可复算的闭式或精确线性代数：

1. **局部线性重构**：`f̂_i(x) = f_i + g_i·(x − x_i)`，`g_i` 取 `k=6` 近邻的距离加权最小二乘梯度（模型空间含全体线性函数 ⇒ 线性场在任何满秩模板上被精确复现）。
2. **保线性限制器**：按胞元顶点上的重构值与本模板（含自身）**重构场顶点包络**比较，把 `g_i` 缩放 `φ_i ∈ [0,1]`。关键设计是包络取自**重构值**而不是原始胞元值、且模板含自身：线性场时每个模板成员重构出同一个线性函数，包络天然包含自身 ⇒ `φ ≡ 1`，限制器**不会**破坏算子的精确性（踩坑记录见 §5）。
3. **精确重叠几何**：凸多边形 Sutherland–Hodgman 裁剪，得到 `C_ji = ∫_{C_j∩C_i} 1 dA` 与一阶矩 `M_ji = ∫_{C_j∩C_i} (x − x_i) dA`；基础传输 `V_j (Pf)_j = Σ_i (C_ji f_i + M_ji·g_i)` 就是 `f̂` 在目标胞元上的**精确**平均值。两侧覆盖缺陷都审计，超阈值 fail-closed。
4. **精确矩修正（Lagrange 乘子）**：求 `δ` 解

```
min ‖δ‖₂   s.t.   Φᵀδ = b,   Φ[j,c] = V_j φ_c(x_j),   φ_c ∈ {1, x, y, x², xy, y²}
⇒ δ = Φ (ΦᵀΦ)⁻¹ b        （6×6 的精确解，不是迭代）
```

   `b = `（源端矩） − （基础传输阵列矩）。**权重选型是有意的**：若用体积加权范数 `Σ V_j δ_j²`，最小范数解满足 `δ_j ∝ 1/V_j`，会把修正堆到最小的几个胞元上（实测把 1e-3 的修正放大成 8e-2 的逐点畸变并触碰边界），故用**不加权的 `‖δ‖₂`**，把修正按数值幅度尽量摊平。
5. **有界活动集 + 质量兜底**：修正触界则把该胞元冻结在界上、在自由集上重解；若边界让矩约束**不可达**（台阶触界就是这种），0 阶矩用「有界的均匀再分配」恢复（耦合绝不能凭空造/消质量），并如实上报 `constraints_feasible=False, mass_repaired=True`。有界性与高阶矩精确性**本质不兼容**，本模块把权衡**报出来**而不是藏起来。

### 2.3 矩的两种口径（重要，决定了「漂移」怎么量）

同一个 O(h²) 差异只能消掉一个，实现里做成显式开关 `moment_reference`：

- `array`（默认）：约束目标是**源阵列的离散矩** `Σ_i V_i f_i φ_c(x_i)`。不变量 = 「目标阵列携带与源阵列完全相同的质量/质心/惯性张量」，即下游任何从阵列重算矩的审计器看到 **零漂移**。耦合里的求解器只认阵列，这是工程口径。
- `continuous`：约束目标是**重构场的精确连续矩** `Σ_i ∫_{C_i} f̂_i φ_c dA`（多边形单项式积分，精确到舍入）。此时阵列成为无偏矩估计器；但相对源**阵列**矩会留下源阵列自身的 O(h²) 求积偏差。
两种都是精确解，只是决定哪个 O(h²) 归零。审计记录**同时**输出 `array_*` 与 `continuous_*` 两套残差，所以口径选择不可能掩盖误差。

### 2.4 判据定义（与 `criteria.json` 字段一一对应）

| 判据 | 定义 | 阈值 |
|---|---|---|
| C1 | 同刻请求（时间算子）+ 同网格退化交换（空间算子）的最大绝对误差 | ≤ 1e-12 |
| C2 | 一阶矩残差（按 `m0·h` 归一化 ⇒ 读作「cell 位移」）+ 质心漂移 | ≤1e-6 / ≤1e-3 cell |
| C3 | 二阶矩残差 + 惯性张量残差 + 100 次 A→B→A 串联不变量漂移 | ≤1e-6 / ≤1e-6 / ≤1e-4 |
| C4 | 有界性：`min ≥ 源 min`、`max ≤ 源 max`，且触界时 0 阶矩仍守恒 | 违例数 = 0 |
| C5 | A→B→A 后**守恒不变量**（质量/质心/惯性张量）与同网格退化往返的场误差 | ≤ 1e-12 |
| C6 | 异构时钟下相对能量残差；同时测「随 dt_f 的二阶收敛」与「与粗细比无关」 | ≤ 0.01 |
| C7 | 关闭矩约束后 C2/C3 的同一指标必须**超阈**（负控） | 必须 FAIL |
| C8 | `pytest tests/coupling/test_{temporal_interp,moment_remap,spacetime_exchange}.py` | ≥16 且全绿 |

C5 的口径要说明白：保守投影到**不同网格**是不可逆的（信息必然损失），所以 C5 断言在「守恒不变量 + 同网格退化恒等」上，同时把**场级**往返变化 3.285% 一并报出（见 §5 局限）。

### 2.5 工具链与复现命令

```bash
# 判据（写 artifacts/IN387-PLAT01/criteria.json，含 pytest 自检）
python tools/run_in387_plat01_acceptance.py
# 图（写 artifacts/IN387-PLAT01/figures/*.png + manifest.json）
python tools/gen_in387_plat01_figures.py
# 单元/集成测试
python -m pytest tests/coupling/test_temporal_interp.py \
                 tests/coupling/test_moment_remap.py \
                 tests/coupling/test_spacetime_exchange.py -q
```

环境：Python 3.11.6 / NumPy 2.4.6 / Linux-6.6.52-1.7.lns23.loongarch64-loongarch64-with-glibc2.38；判据脚本总耗时 68.8s。

## 3. 计算结果

### 3.1 C1 同构退化恒等

| 项 | 值 | 阈值 | 判定 |
|---|---|---|---|
| 时间算子：落在样本上的请求（nearest/linear/cubic） | 0.000e+00 | 1e-12 | PASS |
| 空间算子：同网格退化交换 | 5.607e-15 | 1e-12 | PASS |

### 3.2 C2 一阶矩 / 质心（含负控对照）

| 场 | 一阶矩残差（有约束） | 质心漂移 [cell] | 一阶矩残差（关约束，负控） | 负控质心漂移 [cell] | 修正幅度 |
|---|---|---|---|---|---|
| 线性场 | 4.954e-17 | 5.573e-17 | 2.054e-04 | 2.361e-04 | 2.736e-03 |
| 光滑凸包 | 1.393e-16 | 1.246e-16 | 2.119e-03 | 2.168e-03 | 2.240e-03 |

判据：一阶矩 ≤1e-6（实测 1.393e-16），质心漂移 ≤1e-3 cell（实测 1.246e-16）⇒ **PASS**。

### 3.3 C3 二阶矩 / 惯性张量 / 100 次串联

| 量 | 有约束 | 负控（关约束） | 阈值 |
|---|---|---|---|
| 二阶矩残差（线性场） | 7.957e-16 | 5.790e-03 | 1e-6 |
| 二阶矩残差（光滑场） | 3.730e-16 | 6.341e-03 | 1e-6 |
| 惯性张量残差（worst） | 1.521e-16 | — | 1e-6 |
| 100 次串联质量漂移 | 1.288e-14 | — | 1e-4 |
| 100 次串联质心漂移 [cell] | 1.481e-14 | — | 1e-4 |
| 100 次串联惯性张量漂移 | 7.492e-15 | — | 1e-4 |
| 100 次串联**场级**变化（如实报出，非判据） | 18.876% | — | — |

注意最后一行：串联 100 次后**不变量**稳在机器精度（1e-14 量级），但**场本身**累计变化到 18.876%（单次往返：线性场 0.852%、光滑场 3.285%），并在 ~10² 次后饱和（图 4 右）——这是「阵列矩修复不可幂等」的内禀性质，见 §5。

### 3.4 C4 有界性

| 算例 | 违例数 | 场 min / 源 min | 场 max / 源 max | 约束可达 | 质量残差 |
|---|---|---|---|---|---|
| linear_field | 0 | 0.8008 / 0.7885 | 1.4488 / 1.4488 | 是 | 0.000e+00 |
| smooth_field | 0 | 0.1003 / 0.1003 | 1.0510 / 1.0789 | 是 | 7.402e-16 |
| front_at_bounds | 0 | 0.0000 / 0.0000 | 0.9321 / 1.0000 | 否（已质量兜底） | 2.464e-16 |

总违例 0 ⇒ **PASS**；触界台阶算例矩约束不可达时，0 阶矩仍被兜底在 2.464e-16。

### 3.5 C5 往返一致

| 场 | 质量往返 | 质心往返 [cell] | 惯性张量往返 | 场级变化（如实报出） |
|---|---|---|---|---|
| 线性场 | 0.000e+00 | 0.000e+00 | 4.435e-16 | 0.852% |
| 光滑场 | 6.661e-16 | 1.990e-15 | 6.107e-16 | 3.285% |

不变量往返 1.990e-15（阈值 1e-12），同网格退化往返 5.662e-15 ⇒ **PASS**。

### 3.6 C6 异构时间步下的能量账

| 量 | 值 | 阈值 | 判定 |
|---|---|---|---|
| 最差相对能量残差（ratio=16，相位 0.37） | 1.083e-05 | 0.01 | PASS |
| 最差空间质量残差 | 4.317e-16 | 1e-12 | PASS |
| 细时钟加密的误差阶（能量 ∝ (ω·dt_f)²） | 2.000 | >1.5 | PASS |
| 粗细比 2/4/8/16/32 扫掠的离散度（max/min） | 1.000000 | ≤1.1 | PASS |

细时钟加密表（能量残差与解析预期 `2·α(1−α)/2·(ω dt_f)²` 一致）：

| ω·dt_f | 相对能量残差 |
|---|---|
| 0.04909 | 6.931e-04 |
| 0.02454 | 1.733e-04 |
| 0.01227 | 4.333e-05 |
| 0.00614 | 1.083e-05 |

粗细比扫掠（同一绝对时间窗 [0,0.5] 上的最差值）：

| 粗细比 | 相对能量残差 |
|---|---|
| 2 | 1.083e-05 |
| 4 | 1.083e-05 |
| 8 | 1.083e-05 |
| 16 | 1.083e-05 |
| 32 | 1.083e-05 |

**物理解读**：插值误差只由**源端自己的 dt_f** 决定，目标端时钟粗到什么程度不额外引入误差（五个比值给到 4 位有效数字完全相同）。这正是「粗端不需要更密的时间网格、只需要正确的插值算子」的量化形式。

### 3.7 C7 负控（关矩约束必须 FAIL）

| 量 | 有约束 | 关约束 | 超阈倍数 | 判定 |
|---|---|---|---|---|
| 一阶矩残差 | 1.393e-16 | {'value': 0.00020540727381428503, 'threshold': 1e-06, 'passed': False, 'note': 'must EXCEED the criterion'} | 205× | FAIL |
| 二阶矩残差 | 7.957e-16 | {'value': 0.005790224210168178, 'threshold': 1e-06, 'passed': False, 'note': 'must EXCEED the criterion'} | 5790× | FAIL |

负控的两级退化也一并登记：`donor_cell`（无重构、无约束）二阶矩残差 1.319e-01、场误差 8.526%；「有线性重构但不做全局矩约束」二阶矩残差 5.545e-03。⇒ 收益来自**全局矩约束**这一步，而不是局部重构。

### 3.8 时间算子收敛阶（支撑证据）

| 数据类 | nearest | linear | cubic |
|---|---|---|---|
| C^∞（cos） | 1.000 | 1.998 | 3.998 |
| 一阶显式 Euler 轨迹（耦合中真正生效） | 0.974 | 1.001 | 1.004 |
| 分段线性（角点落在样本上） | 1.000 | 精确 | 1.000 |

规格「线性插值收敛阶=1±0.2」在**耦合轨迹口径**下命中：1.001。光滑数据上 linear 是 2 阶（优于规格），cubic 是 4 阶；分段线性数据被 linear **精确**复现（一阶算子对 C⁰ 分段线性数据无截断误差，这是 Lagrange 性质而非巧合）。

### 3.9 与解析场的精度对标（支撑证据）

解析场 = 平移+横摆的高斯凸包，目标时刻 t*=0.4096（刻意不落在样本上），误差对目标网格上的**解析值**逐胞元取最大绝对值。

| 变体 | 相对幅值误差 | 能量残差 | 一阶矩残差 | 二阶矩残差 | 有界违例 |
|---|---|---|---|---|---|
| cubic_time + linear_recon + moment_correction | 3.557% | 9.842e-03 | 2.775e-16 | 7.428e-16 | 0 |
| cubic_time + linear_recon (no correction) | 3.654% | 1.112e-02 | 2.345e-03 | 5.545e-03 | 2 |
| cubic_time + donor_cell (no correction) | 8.526% | 5.048e-02 | 1.986e-03 | 1.319e-01 | 0 |
| linear_time + linear_recon + moment_correction | 3.565% | 9.909e-03 | 9.249e-17 | 1.114e-15 | 0 |

（上表为**故意欠分辨**的算例：σ=0.18、8×8 网格 ⇒ 每 σ 只有 1.4 个胞元。）

良分辨算例（σ=0.30，每 σ 2.4 胞元）：相对误差 **1.215%**，能量残差 1.454e-03，矩残差 4.965e-16/2.393e-15。

网格加密（欠分辨算例，σ=0.18）：

| 每方向胞元数 | h | 相对误差 |
|---|---|---|
| 8 | 0.12500 | 3.557% |
| 12 | 0.08333 | 1.350% |
| 16 | 0.06250 | 0.333% |

拟合阶 3.35 ⇒ 8×8 上的 3.557% 是**离散分辨率限制**，不是算子缺陷；16×16 已降到 0.333%。矩修正相对无修正版本把误差从 3.654% 改到 3.557%（比值 0.973，即**不损失精度**）。

### 3.10 C8 测试套件

57 项全绿（`57 passed in 32.62s`），覆盖：节点恒等 / 单位分解 / Lagrange 精确性 / 三档收敛阶 / 能量账 / 限幅上报 / 多边形矩精确性 / 重叠几何两侧划分 / 质量与一、二阶矩 / 负控 / 有界性（含触界与不可达）/ 往返与 100 次串联 / 饱和不发散 / 3D 与错域 fail-closed / JSON 审计可序列化 / 口径双报 / 组合算子守恒与能量 / 形状与越界 fail-closed，以及 `backend/coupling` 的**零领域语义闸门**（用平台统一 lexicon 扫描 + stdlib/numpy 导入白名单 + 公开面词条检查）。

## 4. 结果图

![fig1_temporal_convergence.png](artifacts/IN387-PLAT01/figures/fig1_temporal_convergence.png)

**图 1** — Measured temporal-interpolation order. Nearest = 1st, linear = 2nd on C-infinity data and 1st on first-order-in-time (explicit Euler) trajectories, cubic = 4th. The operative coupled case is the dashed green curve: order 1.00.

![fig2_moment_residuals.png](artifacts/IN387-PLAT01/figures/fig2_moment_residuals.png)

**图 2** — Moment residuals of the conservative remap between a stretched 8x8 source mesh and a perturbed 8x8 target mesh. With the exact Lagrange correction the residuals sit at round-off; with the correction disabled (negative control) they exceed the 1e-6 acceptance line by 2-4 orders of magnitude, which is the causal evidence that the correction is what delivers the accuracy.

![fig3_field_maps.png](artifacts/IN387-PLAT01/figures/fig3_field_maps.png)

**图 3** — Space-time exchange field maps. Left: source field on the stretched source mesh. Middle: target field after temporal interpolation + exact moment correction (bounded, min >= source min, max <= source max). Right: what the moment correction changes (|diff| <= 2.35e-03); this is the O(h^2) repair that removes the centroid/inertia drift and it is the only accuracy the correction costs.

![fig4_energy_and_cycles.png](artifacts/IN387-PLAT01/figures/fig4_energy_and_cycles.png)

**图 4** — Left: energy error of the space-time exchange versus the fine-clock resolution, with the (omega dt_f)^2 reference slope (fit order 2.00, worst value 1.1e-5 << 0.01). The error is independent of the coarse/fine clock ratio (sweep 2..32 identical to 4 digits). Right: 200 chained A->B->A exchanges. Mass, centroid and inertia drift stay at round-off (<1e-14) while the field itself converges to a bounded cycle fixed point (~0.19), i.e. the distortion saturates instead of diverging.

![fig5_correction_cost.png](artifacts/IN387-PLAT01/figures/fig5_correction_cost.png)

**图 5** — Second-order behaviour of the moment repair. Both the drift it removes (first-moment residual without correction) and the magnitude of the correction itself scale as h^2, so the repair buys exact moments at a second-order cost in field accuracy.

## 5. 注意点（踩坑、局限、适用范围）

1. **限制器会把线性精确性打掉（踩过的坑）**：最初用经典 Barth–Jespersen（包络取邻元**原始胞元值**、模板不含自身），在扰动网格上把 3% 的胞元误判为超界并缩放梯度，结果「线性场被精确复现」的逐点误差从 1e-15 涨到 8e-2，并让往返一致性失效。改为「顶点重构值的包络 + 模板含自身」后 `φ ≡ 1`，精确性恢复。**教训：限制器的包络必须与重构空间自洽。**
2. **最小范数的权重选错会把修正堆到小胞元上（踩过的坑）**：体积加权范数 `Σ V δ²` 的最小范数解满足 `δ_j ∝ 1/V_j`，在拉伸网格上把 1e-3 的修正放大成 8e-2 的逐点畸变、3 个胞元触界。改成不加权 `‖δ‖₂` 后修正被摊平（1e-3 量级），活动集为空。
3. **「矩守恒」有两个口径，必须显式选**：阵列矩 vs 连续矩只能各保一个（差一个 O(h²)）。默认 `array`（下游审计器从阵列重算看到零漂移），`continuous` 可选；两条残差**同时**写进审计记录，所以口径不会掩盖误差。
4. **往返一致口径**：保守投影到不同网格不可逆，场级 A→B→A 只能恢复到 O(h²)（实测 3.285%）。C5 因此断言在守恒不变量与同网格退化恒等上（均 ≤1e-12），场级数字一并报出。需要场级可逆时，应选 `enforce_moments=False`（纯守恒投影，线性场往返 ~1e-15）。
5. **串联漂移会饱和、不会发散，但场会变形**：100 次串联后不变量仍 1e-14 量级，场级累计变化 18.876%（单次往返线性场 0.852%、光滑场 3.285%），并在百次量级饱和（图 4 右）。物理含义：矩修复不是幂等投影，反复往返会把阵列推向「两套网格求积的折中不动点」。长时间强交换的算例应监控 `field_level_rel_change`，或改用 `moment_order=1`（只保质量+质心）降低修复幅度。
6. **有界性与高阶矩精确性本质冲突**：台阶触界时修正无余量，模块选择「质量绝不牺牲 + 有界性成立 + 高阶矩如实报告不可达」（该算例一阶矩残差 3.876e-01、二阶矩残差 3.603e-01，高于 1e-6 判据，`constraints_feasible=False`；不变量意义上的质量仍精确守恒）。这是物理限制，不是实现缺陷。
7. **能量残差是分辨率的陈述，不是常数**：线性插值对 `cos(ωt)` 有幅度亏损 `α(1−α)/2·(ω dt_f)²`，因此 1% 预算等价于 `ω·dt_f ≲ 0.2`（本算例 ω·dt_f=0.006 ⇒ 1.1e-5）。报告能量残差时必须同时给出 ω·dt_f，否则数字无意义。
8. **3D 未实现，fail-closed**：精确重叠一阶矩在本模块只做了 2D 凸多边形；3D 调用直接抛 `MomentRemapError`（不静默降级到背景网格近似）。3D 化需要凸多面体求交 + 三阶单项式积分，属下一个节点的活。
9. **适用范围**：任意 2D 凸胞元网格（结构化/非结构化、非均匀、非正交），只要两侧覆盖同一区域（覆盖缺陷 >1e-8 fail-closed）。源/目标维数必须同为 2；时间网格任意严格递增（非均匀、不可通约均可），默认不外推。

## 6. 误差判定（逐项 vs 基准，显式回答「是否 <3%」）

| 关键量 | 基准 | 实测 | 是否 <3% |
|---|---|---|---|
| 质量（0 阶矩）守恒 | 解析：应为 0 | 1.850e-16 | 是（远优于） |
| 一阶矩残差 | 解析约束：应为 0 | 1.393e-16 | 是 |
| 质心漂移 [cell] | 应为 0 | 1.246e-16 | 是 |
| 二阶矩 / 惯性张量残差 | 应为 0 | 7.957e-16 / 1.521e-16 | 是 |
| 100 次串联不变量漂移 | 应为 0 | 1.481e-14 | 是 |
| 时间插值（耦合轨迹一阶数据） | 解析 cos/Euler | 阶 1.001（规格 1±0.2） | 是（阶命中） |
| 能量残差（ω·dt_f=0.0061） | 解析 cos(2πt) | 1.083e-05 | 是 |
| 组合算子在**良分辨**解析场上（σ=0.30） | 解析高斯场 | 1.215% | 是 |
| 组合算子在**欠分辨**解析场上（σ=0.18，8×8） | 解析高斯场 | 3.557% | **否**（离散分辨率限制；16×16 降至 0.333%，加密阶 3.35） |

结论：**凡是有解析/精确基准的量（守恒量、矩、能量），误差都在机器精度或远低于 3%；唯一超过 3% 的是故意欠分辨（每 σ 仅 1.4 胞元）算例对解析场的逐点误差，已用加密研究证明它随 h 收敛（阶 3.35）并给出 16×16 的 0.33% 佐证，属离散限制而非算子缺陷。**本报告不含任何未达判据而被粉饰的项。

## 7. 审计链

- 判据 JSON（全部数字的源头）：`artifacts/IN387-PLAT01/criteria.json`（生成时工作树的基线 commit `b763ff74f`，生成 2026-11-11T01:05:20.852898+00:00；重跑 `python tools/run_in387_plat01_acceptance.py` 可逐字复算）
- 图与图注清单：`artifacts/IN387-PLAT01/figures/manifest.json`（含每图字节数）
- 实现：`backend/coupling/temporal_interp.py`、`backend/coupling/moment_remap.py`、`backend/coupling/spacetime_exchange.py`；导出见 `backend/coupling/__init__.py`
- 测试：`tests/coupling/test_temporal_interp.py`、`tests/coupling/test_moment_remap.py`、`tests/coupling/test_spacetime_exchange.py`、`tests/coupling/test_coupling_neutrality.py`（后者用平台统一 lexicon 给 `backend/coupling` 上零领域语义闸门）
- 生成器：`tools/run_in387_plat01_acceptance.py`（判据）、`tools/gen_in387_plat01_figures.py`（图）、`tools/post_in387_plat01_report.py`（本报告，数字全部从 criteria.json 注入，零手抄）
- 判据汇总：C1=True, C2=True, C3=True, C4=True, C5=True, C6=True, C7=True, C8=True

## 8. 局限与后续

- 3D 精确重叠矩（凸多面体求交 + 三阶积分）未做，`fail-closed`；
- 矩修复非幂等，长时间强交换下建议监控场级漂移或降阶（`moment_order=1`）；
- 不可压/多相这类「有界性硬约束 + 高阶矩软约束」的场景，本模块给出的是活动集 + 质量兜底的工程解，不是最优控制解（可考虑加权最小二乘的松弛版本）；
- 时间层未做守恒型时间积分（当前是插值，不是守恒式时间步进）；若源端守恒量本身不守恒，插值层无法补救，只会如实反映（`time_mass_rel_error`）。
