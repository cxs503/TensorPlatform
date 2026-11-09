# 开源 SPH 深盘报告 · zhai-xiao/CPP-Fluid-Particles

> **campaign**: osssph-w2  ·  **mission**: m8661  ·  **tier**: 2  ·  **盘点日期**: 2026-11-09
> **仓**: `zhai-xiao/CPP-Fluid-Particles` @ `master` / `f88e6e16ffa4`（最近代码提交 2019-07-11）
> **产物**: `artifacts/oss-sph-w2/`（`repo_meta.json` API 事实 · `metrics.json` 全部量化指标 · `src/` 源码快照+sha256 ·
> `figures/` 3 图 · `machine_blocks.json` 机器块）· 本报告
> **协议**: 与 LBM 深盘同构（GitHub API 实查 → 源码级方法清单 → 验证面盘点 → 可迁移方案 → 六要素报告）；不建 PR。

---

## 一、案例介绍（物理背景 / 仓库画像 / 参数表）

### 1.1 这是什么

一个**教学/图形学导向的个人 SPH 求解器**：C++ + CUDA（无显式标准设定，`src/CMakeLists.txt`），OpenGL 粒子渲染，单可执行程序演示
**三种粒子流体求解器并列**（按 1/2/3 键切换，`src/main.cpp:69-71`）：

* **WCSPH**：Tait 状态方程（γ=7）+ 对称压力力 + Laplacian 粘性（Becker & Teschner 2007）；
* **DFSPH**：divergence-free → density 两段压力求解 + warm start 刚度累加（Bender & Koschier 2015）；
* **PBD/PBF**：密度约束 λ + 松弛因子 + XSPH 粘性（Macklin & Müller 2013）。

三者在同一 `SPHSystem` 框架下共享邻域搜索/边界粒子/表面张力模块（`src/BaseSolver.h:20-31` 抽象基类），
这正是它对 TensorPhysics SPH 线的价值：**同一套数据/邻域结构下三种压力求解策略的可对照实现**。

### 1.2 GitHub API 实查（2026-11-09，认证 REST v3，非记忆）

| 项 | 值 | 来源 |
|---|---|---|
| 语言 / 体量 | `Cuda`，4061 KB（其中 example.gif 4.0 MB） | `/repos` |
| ★ / fork / open issues | 250 / 35 / 1 | `/repos` |
| License | **GPL-3.0**（GNU General Public License v3.0，LICENSE 35149 B） | `/repos` |
| 建仓 / 最近代码提交 | 2019-05-04 / **2019-07-11**（pushed_at 2019-07-11） | `/repos`, `/commits` |
| 提交数 / 分支 / tag / release | 21 / ['experiment', 'master'] / 0 / 0 | `/commits`,`/branches`,`/tags`,`/releases` |
| 目录结构 | 27 条 tree 项，`src/` 共 20 个文件（2256 行 .cu/.h/.cuh/.cpp） | `/git/trees?recursive=1` |
| **测试布局** | **无**：无 `tests/`、无 assert 断言、无基准脚本 | tree + 本地 grep |
| **CI** | **无**：`/actions/workflows` 返回 0 个 workflow，无 `.github/` | `/actions/workflows` |

**判定：2019-07 后冻结的归档型项目**（无 release、无 CI、无测试），tier2 定位成立；
价值在**方法实现本身与三个求解器的同框架对照**，不在工程化。

### 1.3 算例与参数表（全部由 `metrics.json: parsed_constants_main_cpp` 注入）

几何 = 单位立方箱 `spaceSize = (1,1,1)`，初始流体块 = 24×36×24 = **20736 粒子**的立方柱
（`x∈[0.27,0.73], y∈[0.10,0.80], z∈[0.27,0.73]`），六面体刚性边界粒子包裹（`src/main.cpp:76-116`）。
自由落体溃坝（dam-break）拓扑，无 inflow/outflow。

| 参数 | 值 | 位置 |
|---|---|---|
| 粒子间距 dx | 0.02 | main.cpp:55 |
| 光滑半径 R（核支撑域） | 0.04 = 2·dx | main.cpp:56 |
| 邻域单元边长 | 0.040400 = 1.01·R | main.cpp:57 |
| 时间步 dt | **0.002 s（全求解器固定）** | main.cpp:58 |
| 参考密度 ρ0 / 边界密度 ρB | 1.0 / 1.4（=1.4ρ0） | main.cpp:59-60 |
| 粒子质量 m0（**硬编码**） | 7.659675076208e-05 | main.cpp:61 |
| 刚度 stiff（Tait） | 10.0 | main.cpp:62 |
| 重力 | (0, -9.8, 0) | main.cpp:63 |
| 粘性 ν（WCSPH）/ XSPH c（PBD） | 0.0005 / 0.05 | main.cpp:64 / PBDSolver.h:29 |
| 表面张力强度 / 气压 | 0.0001 / 0.0001 | main.cpp:65-66 |
| GPU / 平台 | GTX 1070, CUDA 8.0, Windows（作者自述，另一台 980 Ti/CUDA 10.1） | README.md:6-18 |

---

## 二、怎么做的（方法 / 流程 / 工具链）

1. **API 实查**（认证 REST：`/repos`、`/git/trees?recursive=1`、`/commits?per_page=100`、`/branches`、
   `/actions/workflows`、`/tags`、`/releases`）+ **代码搜索**（`/search/code`，关键词 ISPH / PCISPH / IISPH /
   delta-SPH / multiphase / two-phase / damBreak / dam-break / Poiseuille → `total_count` 全 0）。
2. **源码全量快照**：20 个源文件（132567 B）落 `artifacts/oss-sph-w2/src/`，
   逐文件 sha256 入 `metrics.json: source_snapshot`（结构性防造假：报告引用的每一行都能对回哈希）。
3. **方法清单（file:line）**：25 条能力/方法项，逐条给 `文件:行`，present/absent 双栏（表 3.1/3.2）。
4. **独立数值复核（CPU-only，numpy 重算代码里的核代数）**：C1 核体积归一化、C2 质量标定与初始密度、
   C3 初始柱自由面邻域截断、C4 表面张力核结点连续性、C5 README 吞吐数自一致性、C6 声学 CFL。
   脚本 `artifacts/oss-sph-w2/tools/analyze_m8661.py`，输出 `metrics.json` + 3 张图（本报告所有数字均程序化注入）。
5. **可迁移方案**：5 条（adapt×2 / reference×3），逐条显式 license 判定并挂 GAP-SPH 锚。

**能力边界（诚实声明）**：本机 `loongarch64`、无 `nvcc`/`nvidia-smi`、无 CUDA/OpenGL/CUDA-Samples
（`CUDASAMPLESROOT` 为 Windows 路径，`src/CMakeLists.txt:11,29` 硬编码 `bin/win64` 拷贝），**无法编译运行**。
因此本报告**不做现场复算流场**（无 dam-break 云图/无量纲精度对标），只做**源码级能力清单 + 可复核的核代数量化核验**；
凡需运行才能得到的量，一律标注 NOT_SUPPORTED/insufficient，不以推测充数。

---

## 三、计算结果

### 3.1 方法能力清单（present，14 项，全部 file:line 定位）

| # | 能力/方法 | 状态 | 证据 (file:line) | 说明 |
|---|---|---|---|---|
| 1 | WCSPH / Tait EOS (gamma=7, p clamped >=0) | `present` | `src/BasicSPHSolver.cu:103-110` | p = stiff*((rho/rho0)^7-1), negative pressures clamped to 0 |
| 2 | WCSPH density with boundary contribution | `present` | `src/BasicSPHSolver.cu:42-53 (kernels), :74-83 (driver)` | rho_i = sum_j m_j W_ij over fluid + boundary neighbours |
| 3 | WCSPH symmetric pressure force | `present` | `src/BasicSPHSolver.cu:113-138 (kernels), :140-165 (driver)` | (p_i/rho_i^2 + p_j/rho_j^2) gradW; velocity clamp 'dirty trick' against blow-ups |
| 4 | Laplacian viscosity kernel | `present` | `src/BasicSPHSolver.cu:183-207 ; src/CUDAFunctions.cuh:52-54` | viscosity_kernel_laplacian = 45(R-r)/(pi R^6) |
| 5 | DFSPH (Bender & Koschier 2015) | `present` | `src/DFSPHSolver.cu:33-72 (step), :160-210 (density solve), :331-365 (divergence solve)` | divergence-free solve, then density solve, warm-started stiffness accumulation |
| 6 | DFSPH convergence control (thresholds/maxIter) | `present` | `src/DFSPHSolver.h:28-30 ; src/DFSPHSolver.cu:187-197, :347` | density & divergence thresholds 1e-3, maxIter 20, stop when totalErr > thr*num*rho0 |
| 7 | PBD / Position-Based Fluids (Macklin & Muller 2013) | `present` | `src/PBDSolver.cu:34-73 (step), :141-168 (lambda), :190-210 (deltaPos), :225-258 (iter loop)` | density constraint lambda, relaxation 0.75, no s_corr artificial pressure term |
| 8 | XSPH viscosity (PBD path) | `present` | `src/PBDSolver.cu:89-115 ; src/PBDSolver.h:29` | v += c * sum_j m (v_j-v_i) W / rho0, c = 0.05 |
| 9 | Rigid boundary particles with Akinci-style mass | `present` | `src/SPHSystem.cu:79-91 (kernel sum), :92-112 (computeBoundaryMass)` | m_b = rhoB / sum_j W_bj, rhoB = 1.4 rho0 (main.cpp:60) |
| 10 | Wall clamp boundary (position + velocity zeroing) | `present` | `src/BasicSPHSolver.cu:85-96 ; src/PBDSolver.cu:212-224` | hard clamp to [0, 0.99]*spaceSize; no friction/contact/impulse model |
| 11 | Colour-gradient free surface + surface tension + air pressure (He 2014 / Akinci 2013) | `present` | `src/BasicSPHSolver.cu:262-279 (handleSurface), :320-370 (surfaceDetection), :372-430 (applySurfaceEffects) ; src/CUDAFunctions.cuh:82-98` | normalised colour gradient, dedicated tension kernel (constant 136.0241, knot 0.0156) |
| 12 | Spatial-hash neighbour search (counting sort + 27-cell stencil) | `present` | `src/SPHSystem.cu:114-127 ; src/CUDAFunctions.cuh:56-76` | mapParticles2Cells -> thrust::sort_by_key -> countingInCell -> exclusive_scan |
| 13 | Single-GPU CUDA back end (Thrust device ops) | `present` | `src/SPHSystem.cu:114-125 ; src/CMakeLists.txt:23-38` | one stream, cudaDeviceSynchronize per step, --expt-extended-lambda |
| 14 | Per-step timing instrumentation | `present` | `src/SPHSystem.cu:129-156` | cudaEvent pair, returns ms |

### 3.2 显式缺失（absent，10 项；代码搜索 + 本地 grep 双证）

| # | 能力/方法 | 状态 | 证据 (file:line) | 说明 |
|---|---|---|---|---|
| 1 | ISPH (classic projection) | `absent` | `code search total_count=0 ; local grep 0` | - |
| 2 | PCISPH | `absent` | `code search total_count=0 ; local grep 0` | - |
| 3 | IISPH | `absent` | `code search total_count=0 ; local grep 0` | - |
| 4 | delta-SPH (density diffusion / delta term) | `absent` | `local grep 'delta' -> only local variable deltaV` | no dissipation term |
| 5 | Multiphase / per-phase density or interface | `absent` | `local grep multiphase\|phase = 0` | single fluid phase |
| 6 | FSI / deformable solid coupling | `absent` | `no solid solver, boundary particles are static` | - |
| 7 | MPI / multi-GPU / domain decomposition | `absent` | `local grep MPI\|OpenMP\|omp = 0` | single device only |
| 8 | CFL / adaptive time step | `absent` | `local grep CFL\|adaptive = 0 ; src/main.cpp:58 const dt` | fixed dt from host |
| 9 | Differentiable / autodiff | `absent` | `no AD in tree` | - |
| 10 | Test suite / CI / benchmark harness | `absent` | `tree: 27 entries, no tests/, no .github/ ; actions/workflows=0` | no assertions, no regression gate |

### 3.3 独立数值复核（C1–C6，numpy 重算源码代数）

| ID | 检验 | 代码/实测值 | 基准/期望值 | 偏差 | 判定 |
|---|---|---|---|---|---|
| C1 | 核体积积分 ∫W dV（code kernel, R=0.04） | 0.125000 | 1.0（单位体积核） | **-87.5%** | 非单位体积核（被 m0 吸收） |
| C2 | 硬编码 m0 vs ρ0/ΣW（内部点阵 ΣW=15624.57, 33 邻居） | 7.659675e-05 | 6.400176e-05 | **+19.68%** | 超出 3% → 见 5.1 陷阱 |
| C2' | 隐含初始体相密度（用硬编码 m0） | 1.1968 ρ0 | 1.0 ρ0 | +19.68% | p_Tait=25.17（stiff=10.0） |
| C2'' | m0 / (ρ0·dx³) | 9.5746 | 1.0（若核单位归一） | — | 说明核缩放因子=1/8 未显式声明 |
| C3 | 初始柱平均 ΣW 相对内部点阵 | 15109.65 | 15624.57 | −3.30% | 自由面邻域截断亏损（物理正常，需登记） |
| C3' | 初始密度分布（硬编码 m0） | 均值 1.1573 / 最低 0.7259 ρ0 | 1.0 | 1.54% 粒子 <ρ0 | 自由面层密度亏损 |
| C4 | 表面张力核 q=1 结点连续性（常数 0.0156） | 0.015650 vs 0.015625 | 1/64 = 0.015625 | **0.16%** | 三位有效数字截断，可用 |
| C5 | README FPS vs 1000/ms（三求解器） | 见表 3.4 | — | ≤0.18% | README 内部自洽 |
| C5' | README dt vs 代码 dt | README 0.001/0.004/0.004 s | 代码 0.002 s | −50% / +100% / +100% | **不可复现**（见 5.2） |
| C6 | 声学 CFL c_s·dt/R（c_s=√(7·stiff/ρ0)=8.367） | 0.4183 | ≤0.4 | +4.6% | 已越过常用上限（h=R/2 口径 0.837） |
| C6' | 同上，但按体积标定隐含的真实体相声速 14.34 | 0.717 | ≤0.4 | **+79%** | 固定 dt 已远超稳定性界 |
| C_id | 源码粒子数 vs README 20,736 | 20736 | 20,736 | 0.0000% | 一致 |

### 3.4 README 吞吐数字（作者自报，GTX 1070 / CUDA 8.0 / 20,736 粒子）

| 求解器 | ms/帧 | FPS | FPS vs 1000/ms | 粒子步/s（派生） | README dt [s] | dt vs 代码 dt |
|---|---:|---:|---:|---:|---:|---:|
| WCSPH | 4.4 | 227.0 | 0.12% | 4.7127e+06 | 0.001 | -50% |
| DFSPH | 23.0 | 43.4 | 0.18% | 9.0157e+05 | 0.004 | +100% |
| PBD | 11.3 | 88.5 | 0.00% | 1.8350e+06 | 0.004 | +100% |

派生关系：WCSPH 比 DFSPH 快 5.23×，PBD 比 DFSPH 快 2.04×
（与「DFSPH 需要压力迭代」的预期一致）。**这些是唯一存在的量化数据，且无任何精度/守恒量对标。**

---

## 四、结果图

图 1 `figures/fig1_capability_matrix.png`：方法能力矩阵（源码确认），红/绿=present/absent。**读法**：三种压力求解齐全、
表面张力/边界模块齐全，但**验证面（tests/CI）与并行扩展（MPI/multi-GPU）、可调时间步全空**——即"算法多、证据少"。

图 2 `figures/fig2_quantitative_checks.png`：三面板量化复核。(a) 代码核形状与数值积分 ∫W dV=0.1250；
(b) 三种质量标定基准对比，硬编码 m0 比 ρ0/ΣW 高 19.68%，隐含体相密度 1.1968ρ0；
(c) README FPS 与 1000/ms 自洽（≤0.18%），但 README dt 与代码 dt=0.002 不一致。

图 3 `figures/fig3_transfer_plan.png`：5 条可迁移方案 × GAP-SPH 锚点 × 杠杆等级（全部 adapt/reference，许可证判定 GPL-3.0 → 禁代码搬运）。

**为何无流场云图 / 无解析解比较曲线**：本机无 CUDA 设备与 nvcc（loongarch64），且该项目构建硬依赖
Windows 路径的 CUDA-Samples（`src/CMakeLists.txt:11,29`）、GLUT 窗口与 OpenGL 上下文，**在本执行环境不可运行**；
上游本身也不提供任何基准算例或参照数据。为保证"零编造"，此处不生成伪流场图，改以可复核的核代数核验图替代，
并把"无 dam-break/Poiseuille/Taylor-Green 量化验证"作为**上游缺陷**登记（见 6.2）。

---

## 五、注意点（踩坑 / 局限 / 适用范围）

### 5.1 陷阱 A：核未体积归一 + 质量硬编码的"双重静默补偿"

* 代码核 ∫W dV = 0.1250（差 8×），且 m0 硬编码 7.659675e-05，比"核自洽"值高
  19.68%，比"单位归一核 + ρ0dx³"值高 9.57×。
* 后果：密度/压力力/粘性力对"质量×核缩放"的组合大多相互抵消（m∇W 不变），**但绝对量纲不安全**——
  ρ0 不再等于体相密度（初始体相 1.1968ρ0）、
  p_Tait 初值 25.17、以 ρ0 为参考的声速 c_s=√(7·stiff/ρ0)=8.367
  **并非真实静止态声速**（按 ρ=1.1968ρ0 重算 c_s=14.34，高 1.71×）。任何以此码标定的
  刚度/时间步/表面张力强度都**不可跨配置复用**。→ 迁移时必须显式声明核归一约定，m0 由几何程序化推导（方案 02）。

### 5.2 陷阱 B：README 吞吐数字与仓库代码不同步

README.md:7-9 声明 WCSPH dt=0.001、DFSPH/PBD dt=0.004；代码只有 `const float dt = 0.002f`（main.cpp:58）三求解器共用。
→ README 的 ms/帧 **不能由当前提交复现**（每帧推进的物理时间差 2–4 倍）。引用其吞吐必须注明此不确定性。

### 5.3 陷阱 C：固定 dt，无 CFL/自适应

`local grep CFL|adaptive = 0`；dt 由 host 常量注入。以 code 核的声速关系算，dt=0.002 的声学 CFL 已到
0.418（R 口径）/ 0.837（h=R/2 口径）；若按真实体相声速
（14.34）则为 0.717 / 1.434——**超出常用 0.4 上限 1.8–3.6 倍**；
代码用"dirty trick"（`src/BasicSPHSolver.cu:159-163` 速度钳制）代替 CFL 控制——**不可移植到验证级求解器**。

### 5.4 陷阱 D：边界是"钳制墙"，不是物理边界

位置钳制 + 速度清零（`src/BasicSPHSolver.cu:85-96`）近似 free-slip 墙，无摩擦、无接触冲量、无壁面压力守恒；
DFSPH/PBD 的边界粒子质量用 Akinci 核和给出（ρB=1.4ρ0），但**边界粒子不参与运动**，不能做 FSI（缺 GAP-SPH-FSI-BENCHMARK 的静水压/梁冲击验证能力）。

### 5.5 陷阱 E：PBF 缺 s_corr 人工压力

`src/PBDSolver.cu:141-168` 只有 λ+松弛（0.75），无 PBF 论文中的 s_corr= −k(ρ/ρ0)ⁿ 张量项 → 拉伸区粒子聚集/空洞
（拉伸不稳定）不会被抑制；用它当"PBF 参考实现"会低估 PBF 在生产中的稳定性差异。

### 5.6 局限与适用范围

* **适用**：图形学级自由面动画；三求解器同框架对照学习；核/边界/表面张力算子的形态参考。
* **不适用**：船舶/海洋工程定量验证（无表征实验对标、无静水压、无波浪边界）、FSI、多相、HPC 扩展性研究。
* **审计边界**：静态审计 + 核代数复核；**未运行**（无 GPU/nvcc），故"数值上是否稳定/精度多少"= insufficient（无法判定，不作为结论）。

---

## 六、误差判定（逐项显式回答"是否 <3%"）

### 6.1 可判定的误差项

| 关键量 | 参照基准 | 本仓值/偏差 | <3% ? |
|---|---|---|---|
| 粒子数（源码 36×24×24） | README 声明的 20,736 | 20736，偏差 0.0000% | **是** |
| README FPS 自一致性 | 1000/ms 派生 | 最大偏差 0.18% | **是** |
| 表面张力核 q=1 结点连续性 | 精确常数 1/64 | 偏差 0.16% | **是** |
| 核体积归一 ∫W dV | 1.0 | 0.125000，偏差 **-87.5%** | **否**（被 m0 吸收，但属未声明约定） |
| 质量标定 m0 vs ρ0/ΣW | ρ0/ΣW（核自洽） | **+19.68%** | **否** → 初始体相密度 1.1968ρ0 |
| README dt vs 代码 dt | 代码 main.cpp:58 | −50% / +100% / +100% | **否**（不可复现） |
| 声学 CFL | 常用量 0.4 | 0.418（R 口径） | **否**（超 4.6%） |

### 6.2 无基准、无法判定的项（NOT_SUPPORTED，不含糊）

* **dam-break 前沿位置/浪高、Poiseuille 速度剖面、Taylor-Green 能量衰减**：上游**无任何**算例数据或测试，
  本机也不能运行 → **无基准可对标，误差不可判定（NOT_SUPPORTED）**。
* **密度误差/压力振荡（DFSPH 收敛性）**：代码有阈值 1e-3 与 maxIter=20 的机制，但无实测收敛曲线 → insufficient。
* **GPU 强/弱扩展性**：仅单卡单点吞吐，无规模扫描、无多卡 → insufficient。

**结论口径**：本仓的"精度证据等级 = WITHHELD（无验证面）"；可用的只有算法形态与经验数值，
**不能作为 TensorPhysics 的精度基线**，只能作为方法与"反面经验"的参考源。

---

## 七、可迁移方案（5 条，license 合规显式判定）

上游 **GPL-3.0**（copyleft）⇒ **一律禁止复制任何 `.cu/.h/.cpp` 代码行**到 TensorPhysics（非 GPL 仓）。
下表的 adapt/reference 均指"移植算法约定 / 作为概念与经验参照"，实现须按论文（SCA'07/SCA'15/TOG'13/TOG'14）独立重写。

| ID | 类型 | 方案 | GAP-SPH 锚 | License 判定 |
|---|---|---|---|---|
| `…-01` | **adapt** | DFSPH 收敛停机判据（thr·num·ρ0 / maxIter / warm start） | `GAP-SPH-ISPH-CONVERGENCE` | 上游 GPL-3.0：不得复制任何 .cu/.h 代码行 |
| `…-02` | **adapt** | 核-质量归一化卫生：∫W dV=1 与 m0=ρ0dx³ 的 CI 断言 | `GAP-SPH-WCSPH-VALIDATION` | 纯数值方法论，无代码复制 |
| `…-03` | **reference** | Akinci 边界粒子质量 + 自由面截断亏损的负基线（位置钳制不可用于 FSI） | `GAP-SPH-FSI-BENCHMARK` | GPL-3.0 → 仅概念与经验数值参考，禁止代码搬运。 |
| `…-04` | **reference** | 颜色场自由面 → 张力/气压算子骨架（结点常数 0.0156=1/64 可用） | `GAP-SPH-MULTIPHASE` | GPL-3.0 → reference-only |
| `…-05` | **reference** | 单卡吞吐下界参照（4.71e6 粒子步/s @GTX1070，无多卡数据） | `GAP-SPH-GPU-SCALING` | 吞吐数字为 README 声明值，仅作参照 |

### 7.1 机器块（各出现一次/多次，供台账解析）

[OSS-INV]{"campaign": "osssph-w2", "repo": "zhai-xiao/CPP-Fluid-Particles", "tier": 2, "methods": ["WCSPH (Tait gamma=7)", "DFSPH (Bender&Koschier 2015)", "PBD/PBF (Macklin&Muller 2013)", "XSPH viscosity", "colour-gradient free surface + surface tension/air pressure (He 2014/Akinci 2013)", "Akinci-style rigid boundary particle mass"], "boundary": "rigid boundary particles with kernel-summed mass (rhoB=1.4*rho0, src/SPHSystem.cu:92-112) + hard position clamp & velocity zeroing (src/BasicSPHSolver.cu:85-96); no friction/contact/impulse, no dynamic boundary, no open/far-field boundary", "gpu": "single-GPU CUDA + Thrust (sort_by_key/countingInCell/exclusive_scan), 256-thread blocks, per-step cudaDeviceSynchronize, timing via cudaEvent; no MPI, no multi-GPU, no OpenMP, no adaptive dt; fixed dt=0.002 s (src/main.cpp:58)", "validation": "NONE: no test suite, no CI workflow (actions/workflows=0), no assert-based checks, no dam-break/Poiseuille/Taylor-Green case, no quantitative error data; only a qualitative README GIF + self-reported ms/frame throughput", "license": "GPL-3.0 (LICENSE 35149 B, SPDX via GitHub API)", "transferable": ["SPHSOL-cppfluidparticles-01", "SPHSOL-cppfluidparticles-02", "SPHSOL-cppfluidparticles-03", "SPHSOL-cppfluidparticles-04", "SPHSOL-cppfluidparticles-05"]}[/OSS-INV]

[OSS-SOL]{"repo": "zhai-xiao/CPP-Fluid-Particles", "id": "SPHSOL-cppfluidparticles-01", "kind": "adapt", "summary": "DFSPH 收敛停止准则移植：以总误差 totalError > thr*num*rho0 为停机判据（thr=1e-3, maxIter=20, 至少 2 轮），并把上一时间步刚度 denWarmStiff 按 particle2cell 排序做 warm start 累加。TensorPhysics SPH 线目前无量化收敛门，可据此把 ISPH/DFSPH 压力求解的收敛判据写成可测门限，由 C6 类时间步敏感性扫描给出 thr–iter–密度误差曲线。", "gap": "GAP-SPH-ISPH-CONVERGENCE"}[/OSS-SOL]

[OSS-SOL-LICENSE](上游 GPL-3.0：不得复制任何 .cu/.h 代码行；本项只移植算法约定（阈值语义/停机判据/warm start 结构），完全按 Bender & Koschier 2015 论文（SCA'15）独立实现 = 合规。)[/OSS-SOL-LICENSE]

证据: `src/DFSPHSolver.cu:33-72, :160-210, :187-197, :331-365 ; src/DFSPHSolver.h:28-30`

[OSS-SOL]{"repo": "zhai-xiao/CPP-Fluid-Particles", "id": "SPHSOL-cppfluidparticles-02", "kind": "adapt", "summary": "核/质量归一化卫生检查：本仓 kernel 体积积分仅 0.1250（≠1，偏差 -87.5%），偏差被硬编码 m0 吸收，导致 m0 比 ρ0/ΣW 大 19.68%，初始体相密度 1.1968ρ0、p_Tait=25.17。TensorPhysics 应把「∫W dV=1」与「m0=ρ0·dx³」做成 CI 断言（pytest 数值积分 + 点阵求和双检），任何几何/间距改动都重算 m0，禁止硬编码。", "gap": "GAP-SPH-WCSPH-VALIDATION"}[/OSS-SOL]

[OSS-SOL-LICENSE](纯数值方法论，无代码复制；GPL-3.0 无关。)[/OSS-SOL-LICENSE]

证据: `src/CUDAFunctions.cuh:23-35 ; src/main.cpp:55-62 ; 本仓 metrics.json C1/C2`

[OSS-SOL]{"repo": "zhai-xiao/CPP-Fluid-Particles", "id": "SPHSOL-cppfluidparticles-03", "kind": "reference", "summary": "Akinci 式刚体边界粒子质量（m_b=ρ_B/ΣW_b, ρ_B=1.4ρ0）与位置钳制边界的对照基线：本仓可量化给出初始柱自由面邻域截断亏损 3.30%、1.54% 粒子密度低于 ρ0（最低 0.726ρ0）——即为「边界/自由面粒子密度亏损」的定量参考，可作 TensorPhysics 边界处理（含流固耦合）的负基线对照：位置钳制无摩擦/接触/压力守恒，不能用于 FSI 基准，只能做 graphics 级参考。", "gap": "GAP-SPH-FSI-BENCHMARK"}[/OSS-SOL]

[OSS-SOL-LICENSE](GPL-3.0 → 仅概念与经验数值参考，禁止代码搬运。)[/OSS-SOL-LICENSE]

证据: `src/SPHSystem.cu:79-112 ; src/BasicSPHSolver.cu:85-96 ; src/PBDSolver.cu:212-224`

[OSS-SOL]{"repo": "zhai-xiao/CPP-Fluid-Particles", "id": "SPHSOL-cppfluidparticles-04", "kind": "reference", "summary": "颜色场自由面检测 + 表面张力/气压合并算子（He 2014 / Akinci 2013，张力核归一常数 136.0241，q=1 结点常数 0.0156：本仓复算结点连续性误差仅 0.16%，即该常数是 1/64 的三位有效数字截断，可用）。可作多相/界面张力算子的参考实现形态：归一化颜色梯度 → 自由面判定 → 张力+气压项，接口形状适合作为 TensorPhysics 多相 GAP 的算子骨架。", "gap": "GAP-SPH-MULTIPHASE"}[/OSS-SOL]

[OSS-SOL-LICENSE](GPL-3.0 → reference-only；常数与公式出自论文（TOG 2014/2013），按论文重推即可合规。)[/OSS-SOL-LICENSE]

证据: `src/BasicSPHSolver.cu:262-279, :320-370, :372-430 ; src/CUDAFunctions.cuh:82-98`

[OSS-SOL]{"repo": "zhai-xiao/CPP-Fluid-Particles", "id": "SPHSOL-cppfluidparticles-05", "kind": "reference", "summary": "单 GPU 吞吐基线（GTX 1070 / CUDA 8.0 / 20,736 粒子，README 实测）：WCSPH 4.4 ms/帧 = 4.713e+06 粒子步/s，DFSPH 23.0 ms/帧 = 9.016e+05 粒子步/s，PBD 11.3 ms/帧 = 1.835e+06 粒子步/s。可作 TensorSPH GPU 扩展性（GAP-SPH-GPU-SCALING）的低端参照点；注意本仓无 MPI/多卡/域分解，强/弱扩展性完全无数据（single-device only）。", "gap": "GAP-SPH-GPU-SCALING"}[/OSS-SOL]

[OSS-SOL-LICENSE](吞吐数字为 README 声明值，仅作参照；GPL-3.0 代码不可复用。)[/OSS-SOL-LICENSE]

证据: `README.md:6-9 ; src/SPHSystem.cu:129-156`

### 7.2 三档建议（对 TensorPhysics SPH 线的排期含义）

1. **立刻可做（adapt, 低成本高收益）**：方案 02 归一化卫生断言（本次审计已把判据写死为可测：∫W dV=1、m0=ρ0dx³、
   ΣW 点阵核对），直接进 GAP-SPH-WCSPH-VALIDATION 的验收门；方案 01 的收敛停机判据进 GAP-SPH-ISPH-CONVERGENCE。
2. **中期（reference 形态借鉴）**：方案 04 的颜色场→张力/气压算子骨架（GAP-SPH-MULTIPHASE）；方案 03 的边界质量核和
   只在"边界粒子参与守恒"的前提下才可用（当前实现不可 → 作为负基线）。
3. **仅作对照点**：方案 05 的单卡吞吐（4.7e6 粒子步/s @ GTX1070）纳入 GAP-SPH-GPU-SCALING 的下界参照，勿当目标。

---

## 八、结论与后续波次

* 本仓 = **"三求解器同框架"的优秀教学实现 + 零验证面 + GPL-3.0 冻结仓**；对 TensorPhysics 的价值集中在
  **算法约定（收敛判据/边界质量/张力算子形态）与两条硬教训（核-质量双重静默补偿、README-代码漂移）**。
* 证据等级：方法面 REAL（源码确认）；**精度面 WITHHELD**（无验证数据且本机不可运行）。
* 建议波次：wave-3 起在 TensorSPH 侧补 (a) 归一化/标定 CI 断言（方案 02）、(b) 收敛判据门（方案 01）、
  (c) 与 SPlisHSPlasH（MIT）/ DualSPHysics 的同算例 dam-break 对标（真正回答 <3% 的那一步）。

**产物清单**
* `artifacts/oss-sph-w2/repo_meta.json` — GitHub API 事实（含 tree/commits/branches/workflows）
* `artifacts/oss-sph-w2/metrics.json` — 全部量化指标 + 25 条方法清单 + 源码 sha256
* `artifacts/oss-sph-w2/src/` — 20 文件源码快照（132567 B）
* `artifacts/oss-sph-w2/figures/` — 3 图 · `artifacts/oss-sph-w2/machine_blocks.json / machine_blocks.txt`
* `artifacts/oss-sph-w2/tools/` — `analyze_m8661.py`（量化复核）、`ghm8661.py`（API helper）、`emit_report.py`（本报告生成）
