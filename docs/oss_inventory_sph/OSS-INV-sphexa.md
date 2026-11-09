# OSS 深盘报告 · sphexa-org/sphexa（SPH-EXA）

> **scenario_key**: `osssph-w2`
> **mission**: m8657（OSS-SPH 深盘波2·集团军，目标仓 tier1）
> **repo**: `sphexa-org/sphexa` · **commit 钉定**: `28aa0d0d4104524d4e4d47e5ec1780ee8027ed8b`（`develop` HEAD，2026-11-04，commit message `Move HDF5 parallel mode definition to env variable (#658)`）
> **license**: MIT（GitHub API `/repos` → `license.spdx_id = "MIT"`，`LICENSE` 文件在根目录）
> **盘点日期**: 2026-11-09 · **盘点人**: agent/m8657 · **平台报告 id**: 9964
> **产物（本单全部由脚本生成，零手抄）**
> - `artifacts/oss-sph-sphexa/sphexa_evidence.json` —— 37 条能力断言 → `file:line` 证据表（`tools/oss_inventory/sphexa_evidence.py`）
> - `artifacts/oss-sph-sphexa/kernel_check.json` —— 上游 header-only 数值部件**编译+运行**复算 vs 解析基准（`tools/oss_inventory/sphexa_kernel_check.py`）
> - `artifacts/oss-sph-sphexa/tensorphysics_sph_anchors.json` —— 下游 TensorPhysics SPH 线现状锚点（供可迁移方案对表）
> - `artifacts/oss-sph-sphexa/figures/fig1_kernels_and_recompute.png`、`fig2_leapfrog_error.png`

---

## 一、案例介绍（这是什么系统 / 物理与工程背景）

SPH-EXA 是 CSCS（ETH Zurich）+ University of Basel 的 **Exascale 级 SPH 流体仿真码**，C++20 编写，
"MPI + OpenMP + CUDA + HIP" 四路并行，血统上继承 **SPHYNX / ChaNGa / SPH-flow** 三个 PASC SPH-EXA 项目的
父代代码（`README.md:23`）。它的物理战场是**天体物理**：自引力气体动力学、星盘、湍流、冲击波、星系盘，
不是海岸/船舶水动力。

### 1.1 仓元数据（GitHub API 实查，非记忆）

| 字段 | 值 | 来源 |
|---|---|---|
| full_name | `sphexa-org/sphexa` | `/repos` |
| 主语言 | C++ | 同上 |
| stars / forks / open issues | 111 / 37 / 21 | 同上 |
| license | **MIT**（`spdx_id: MIT`） | 同上 |
| default branch | `develop` | 同上 |
| created_at / 最近 commit | 2018-12-19 / 2026-11-04（`28aa0d0d41`） | `/repos`, `/commits/develop` |
| topics | computational-fluid-dynamics, exascale, n-body, sph, sph-simulations | `/repos` |
| 归档状态 | 活跃（`archived: false`，`pushed_at` 2026-11-09） | `/repos` |
| 规模（剔除 `extern/` 第三方子模块） | 429 个 C++/CUDA 文件 / 67,790 行；其中 GPU 源 95 个 / 15,158 行 | `sphexa_evidence.json:stats` |
| 模块行数 | `sph/` 66 文件 7,813 行；`domain/`(Cornerstone/cstone) 209 文件 37,160 行；`ryoanji/`(重力) 32 文件 5,893 行 | 同上 |

### 1.2 目录结构与执行路径

```
sphexa/
├── main/src/          # 应用前端：init(初始条件) / io(HDF5,ASCII) / propagator(时间推进) / observables / viz(Ascent,Catalyst)
├── sph/include/sph/   # SPH 内核：hydro_std(WCSPH) / hydro_ve(体积元) / hydro_turb / kernels / positions / ts_*(时间步)
├── domain/include/cstone/  # Cornerstone：SFC 八叉树、域分解、halo 交换、邻居搜索、ijloop(CPU/GPU 双实现)
├── ryoanji/           # 自引力 N-body：八叉树笛卡尔多极矩 + Ewald(周期) + LET 分布式
├── physics/           # 扩展物理：cooling(GRACKLE 化学/冷却) / Disk(吸积盘) / polytrope(Lane-Emden)
├── ci/                # GitLab CI（CSCS）定义 + 8 组初始条件的守恒量回归参考文件
└── docs/              # Sphinx/readthedocs
```

执行路径：`main/src/sphexa/sphexa.cpp` → `simInit`（按 `--init` 造 IC）→ `Domain`（cstone 建树/分配）
→ `propagator`（`std_hydro` / `ve_hydro` / `ve_hydro_bdt` / `nbody`）→ 每步 `observables` 守恒量写盘
（`main/src/sphexa/sphexa.cpp:124,174`）。

### 1.3 关键参数（从各 IC 的 settings 表提取，给出处）

| 参数 | 典型值 | 含义 | 出处 |
|---|---|---|---|
| `Kcour` | 0.2 | CFL 系数，`dt = Kcour·h/max(v_signal, c)` | `main/src/init/sedov_constants.hpp:17`、`sph/include/sph/kernels.hpp:16-22` |
| `ng0` / `ngmax` | 100 / 150 | 目标邻居数 / 邻表上限 | `main/src/init/sedov_constants.hpp:17` |
| `etaAcc` | 默认 **0.2**（可 CLI/HDF5 覆盖） | 加速度限制步长系数 | `sph/include/sph/particles_data.hpp:119,198`、`sph/include/sph/ts_global.hpp:49-70` |
| `alpha`（人工黏性） | 1.0（std）/ 逐粒子 0.05–1.0（VE 开关） | Monaghan 型 α-β 黏性，`β=2` | `sph/include/sph/kernels.hpp:147-160`、`sph/include/sph/hydro_ve/av_switches_kern.hpp:43` |
| 核支撑域 | `kernelSupport = 2.0`（即 `2h`） | 所有核函数在 `x = r/h ≥ 2` 截断 | `sph/include/sph/sph_kernels.hpp:81,85` |
| `maxNumRungs` | 4 | 块时间步最多 4 级（2 的幂） | `sph/include/sph/timestep.h:19` |

---

## 二、怎么做的（方法 / 工具链 / 关键设置）

四步，全部可复现，命令如下（脚本内不含任何人工转录数字）：

1. **GitHub API 实查元数据**（禁凭记忆）：`/repos/sphexa-org/sphexa`、`/commits/develop`、`/git/trees/develop?recursive=1`。
   未走 `raw.githubusercontent.com`（本机出口被重置），改用 `codeload` tarball（928,708 B）解包到本地检出，
   所有 `file:line` 在本机检出上实时 `grep` 生成。
2. **能力断言 → 指针证据表**：`python tools/oss_inventory/sphexa_evidence.py --checkout <检出> --commit 28aa0d0d41...`
   → 37 条断言（31 条肯定 + 6 条否定），每条带 grep 命中数与 `file:line`。
   **否定声明与肯定声明同等举证**：`grep -rnE <pattern>` 命中 0 即否定证据，命中数一并落盘。
3. **独立复算（不读报告，直接编译上游代码）**：
   `python tools/oss_inventory/sphexa_kernel_check.py --checkout <检出> --figdir artifacts/oss-sph-sphexa/figures`
   - 探针程序 `#include "sph/sph_kernels.hpp"` 与 `"sph/positions.hpp"`（两文件 header-only），
     `g++ 12.3.0 -std=c++20 -O2` 编译并运行；
   - 调上游自己的 `sph::kernel_3D_k()`（Simpson 2000 区间积分）复算 6 个核的归一化常数，
     与 (a) 源码里硬编码的 `K1/K2`、(b) **Wendland 1995 / Dehnen & Aly 2012 解析归一化**比对；
   - 调上游自己的 `sph::positionUpdate()` 跑常加速度场，与解析解 `x = x₀+v₀t+½at²` 比对；
   - 调 `sph::tsKCourant()`、`sph::artificial_viscosity()` 与手推解析式比对。
4. **下游对表**：拉 `cxs503/TensorPhysics`（main）树，定位 SPH 线现状（见 §7.0），据此给可迁移方案分级。

**未做（诚实申报）**：没有构建整码 / 没有跑 Sedov、Evrard 等算例（需 MPI + HDF5 + 玻璃 IC 文件，且本机为 x86，
上游性能数据主要在 GH200/LUMI 上取得）。因此本报告的量化结论**只覆盖** §3.4 的 10 项复算，不覆盖水动力精度。

---

## 三、计算结果（程序化注入，零手抄）

### 3.1 SPH 变体清单（源码级）

| # | 能力 | 判定 | 证据（`file:line`） |
|---|---|---|---|
| M1 | 标准 WCSPH：**密度求和**（`densityIjLoop`，非连续性方程）+ 动量/能量核，Monaghan 型人工黏性（α-β，β=2） | **REAL** | `sph/include/sph/hydro_std/density.hpp:43-46`、`sph/include/sph/hydro_std/momentum_energy_kern.hpp:64`、`sph/include/sph/kernels.hpp:147-160` |
| M2 | 体积元（Volume Element）公式：`xmass`/`gradh`/`dV`/三重对称矩阵 `c11..c33`（Hopkins 2015 血统） | **REAL** | `sph/include/sph/hydro_ve/divv_curlv_kern.hpp:113,121,125`、`sph/include/sph/hydro_ve/xmass_kern.hpp:86` |
| M3 | 逐粒子人工黏性开关（div(v) 驱动，α∈[αmin,αmax]，decay_constant 衰减；SPHYNX 血统） | **REAL** | `sph/include/sph/hydro_ve/av_switches_kern.hpp:43,52,93` |
| M4 | IAD（积分近似导数）正则化：矩阵质量判据 + ridge 修正（粒子分布退化时梯度仍可解）；`hydro_std` 与 `hydro_ve` 两条线都有 | **REAL** | `sph/include/sph/iad_regularization.hpp:24,30,35`、`sph/include/sph/hydro_std/iad.hpp` |
| M5 | ISPH / PCISPH / DFSPH / IISPH / PBD（任何压力投影/不可压缩/位置动力学变体） | **NOT_SUPPORTED** | 全仓 `grep -rnE "\b(ISPH\|PCISPH\|DFSPH\|IISPH\|PBD)\b"` → **命中 0**（`sphexa_evidence.json:M5`） |
| M6 | δ-SPH（密度扩散 / delta 项） | **NOT_SUPPORTED** | `grep -rniE "delta.?sph\|deltasph\|density diffusion"` → **命中 0**（`M6`） |
| K1 | 核函数族：`sinc^n`、`0.9·sinc⁴+0.1·sinc⁹` 混合核、Wendland C2/C4/C6（`SPHEXA_WITH_WENDLAND` 开关） | **REAL** | `sph/include/sph/sph_kernels.hpp:94,117,162,185,209,237` |
| K2 | 混合核硬编码归一化常数 `K1=0.4589175166876355`、`K2=1.391322148921789`（注释标来源 `kernel_3D_k`） | **REAL** | `sph/include/sph/sph_kernels.hpp:157,158` |
| K3 | 核归一化现算：`kernel_3D_k` = 1/∫₀²4πx²W(x)dx（Simpson，2000 区间） | **REAL** | `sph/include/sph/sph_kernels.hpp:81,85-92` |

**结论**：SPH-EXA 是**纯 WCSPH 谱系 + 体积元（VE）改进**的天体 SPH，**没有**任何不可压缩/投影/PBD 分支，
也**没有** δ-SPH 耗散项。对船舶/海岸方向的"不可压缩自由面 SPH"而言，它提供的是**精度与并行工程**，
不是压力求解范式。

### 3.2 边界处理 / 时间步 / 多相

| # | 能力 | 判定 | 证据 |
|---|---|---|---|
| B1 | 边界类型：周期 / 开放 / 固定（`cstone::BoundaryType`），盒级设置 | **REAL** | `domain/include/cstone/findneighbors.hpp:44`、`main/src/init/noh_init.hpp:145`（open）、`main/src/init/gresho_chan.hpp:148`（periodic） |
| B2 | 固定边界修正 FBC：近壁粒子把加速度/速度按 `fbcAdjustFactors` 因子衰减，**不是壁面粒子** | **REAL（但为权宜法）** | `sph/include/sph/positions.hpp:48,57-59,116,155` |
| B3 | 幽灵/镜像/动态边界粒子（DualSPHysics 型 wall particle） | **NOT_SUPPORTED** | `grep -rniE "ghost particle\|mirror particle\|dynamic boundary\|wall particle"` → **命中 0**（`B3`） |
| P1 | 多相 / 表面张力 / 自由面检测 | **NOT_SUPPORTED** | `grep -rniE "multiphase\|multi-phase\|surface tension\|free.?surface"` → **命中 0**（`P1`） |
| T1 | 逐粒子 CFL：`dt = Kcour·h/max(v_signal, c)`，`v_signal = c_i+c_j-3w_ij` | **REAL** | `sph/include/sph/kernels.hpp:16-22`（定义）、`sph/include/sph/hydro_std/momentum_energy_kern.hpp:120`（调用） |
| T2 | 块时间步（分级 rung）：`numRungs = min(⌊log₂(dt_max/dt_min)⌋+1, 4)`，rung 边界=`2^r·dt_min` | **REAL** | `sph/include/sph/ts_rungs.hpp:147`、`sph/include/sph/timestep.h:19` |
| T3 | 分级驱动：逐 rung 循环 + drift-back 判定 + butterfly 子步（Springel 型 individual timestep） | **REAL** | `main/src/propagator/ve_hydro_bdt.hpp:352,354,360,364` |
| T4 | leapfrog/KDK 位置-能量更新（`positionUpdate` / `energyUpdate`，`dX/dt_m1` 反解半步速度） | **REAL** | `sph/include/sph/positions.hpp:78,98,181-200` |
| T5 | 重力/加速度限制步长 `etaAcc·(min h²/a²)^{1/4}`；发散量限制步长 `rhoTimestep` | **REAL** | `sph/include/sph/ts_global.hpp:49,58,105` |
| E1 | EOS 三种：理想气体 `idealGasEOS_u(u,ρ,γ)` / `idealGasEOS(T,ρ,mui,γ)`（平均分子量逐粒子）、等温 `p=ρc²`、多方（polytrope） | **REAL** | `sph/include/sph/hydro_ve/eos.hpp:87,116`、`sph/include/sph/eos.hpp:59-64,66-` |

**时间步亮点**：`dtCourant` 是**逐粒子**场（`sph/include/sph/particles_data.hpp:259`），先在 GPU 上按 group
算最小值、radix sort 后按 `fastFraction=0.4` 取"最快 40% 粒子的最大步长"来定 `numRungs`
（`sph/include/sph/ts_rungs.hpp:93-152`）——这是**上限受控的个体时间步**，避免少数快粒子把全局 dt 拖死。

### 3.3 并行策略与验证面

| # | 能力 | 判定 | 证据 |
|---|---|---|---|
| G1 | MPI 域分解：SFC（Morton 键）八叉树 + 全局粒子交换 | **REAL** | `domain/include/cstone/domain/assignment.hpp:184,189`、`domain/include/cstone/domain/domaindecomp_mpi.hpp:70` |
| G2 | OpenMP：ijloop 的 `omp parallel for simd` 向量化路径 | **REAL** | `domain/include/cstone/traversal/ijloop/cpu_fullnblist.hpp:50,73`、`cpu_alwaystraverse.hpp:47` |
| G3 | CUDA **与** HIP 双后端（同一 `.cu`，`GPU_SYMBOL` 宏切换；二者互斥） | **REAL** | `CMakeLists.txt:22,70-85`、`domain/include/cstone/cuda/cuda_stubs.h` |
| G4 | GPU-aware halo 交换（`cudaMemcpyAsync` + `P2pTags::haloExchange`） | **REAL** | `domain/include/cstone/halos/exchange_halos_gpu.cuh:49,68` |
| G5 | GPU 邻居搜索（Octree ± 超级簇邻表 `gpu_superclusternblist`） | **REAL** | `domain/include/cstone/traversal/ijloop/gpu_superclusternblist.cuh`、`atomic_update_ptr.cuh:19` |
| G6 | NVIDIA MPS 包装脚本（多 rank 共享单卡，NUMA 绑定） | **REAL** | `mps_wrapper.sh:9-20` |
| R1 | 自引力：八叉树笛卡尔四极矩 + 分布式 LET（local essential tree） | **REAL** | `ryoanji/src/ryoanji/interface/global_multipole.hpp:11,26-37` |
| R2 | 周期重力：Ewald 求和 | **REAL** | `ryoanji/src/ryoanji/interface/ewald.cu:11,18` |
| I1 | 原地可视化：Ascent / Catalyst 适配器 | **REAL** | `main/src/viz/ascent_adaptor.h:8,20`、`catalyst_adaptor.h` |
| I2 | 重启续算：HDF5 step 属性 `loadOrStore`（存 `dt_m1`/`numRungs`） | **REAL** | `sph/include/sph/timestep.h:29` |
| V1 | 解析解比对：Sedov / Noh / Evrard / Gresho-Chan 的 L1 误差脚本 | **REAL** | `main/src/analytical_solutions/compare_solutions.py:106,114,125`、`compare_evrard.py:326,357` |
| V2 | Sedov 自相似解求解器（ODE 积分，C++ 独立程序） | **REAL** | `main/src/analytical_solutions/sedov_solution/sedov_solution.cpp` |
| V3 | CI 守恒量回归：8 组 IC × CPU/CUDA = 16 个参考文件，`rtol=1e-2`/`atol=1e-6` 门限 | **REAL** | `ci/scripts/compare_constants.py:5-7`、`ci/reference/*.txt`（16 个文件）、`ci/scripts/run_constants_comparison.sh` |
| V4 | 单测：gtest + ctest（`sph/main/physics/ryoanji` 共 115 个 `TEST()`），含 MPI 多 rank 集成测试 | **REAL** | `sphexa_evidence.json:X1`（29 命中/`sph/test`）、`main/test/CMakeLists.txt:19`、`ryoanji/test/CMakeLists.txt:56` |
| V5 | **dam-break / Poiseuille / Taylor-Green 水动力基准** | **NOT_SUPPORTED** | `grep -rniE "dam.?break\|Poiseuille\|Taylor.?Green"` → **命中 0**（`V4`） |
| V6 | Sod 激波管标准测试 | **NOT_SUPPORTED** | `grep -rniE "\bsod\b"` → **命中 0**（`V5`） |
| V7 | GitHub Actions 上的构建/测试 CI | **无**（仅 `clang-format` / `docs` / `repo-stats` 三个 workflow，构建测试在 GitLab CI@CSCS） | `.github/workflows/`（3 文件）、`ci/cscs.yml` |

**验证面判读**：SPH-EXA 的验证是**天体向 + 回归向**：
① 解析解（Sedov/Noh/Evrard/Gresho-Chan）有 L1 误差脚本但**无门限**（人工看）；
② 真正的机器门是 **CI 守恒量对比**（能量/线动量/角动量 vs 8 组参考文件，rtol 1e-2），
   即"**不许比上次差**"的回归护栏，而**不是**"与实验/解析解误差 <X%"的精度断言；
③ 完全**没有**自由面/内流/黏性通道类基准（dam-break、Poiseuille、Taylor-Green 全部 0 命中）。
对 `GAP-SPH-WCSPH-VALIDATION` 来说，它**不能**当对标数据源，但**能**当验证**工装**样本。

### 3.4 独立复算结果（本单跑出来的数字）

环境：`g++ (GCC) 12.3.0`，`-std=c++20 -O2`，直接 `#include` 上游 header-only 部件。
数据源：`artifacts/oss-sph-sphexa/kernel_check.json`。

| 复算量 | 上游代码结果 | 参考值（解析） | 相对误差 | <3%？ |
|---|---|---|---|---|
| Wendland C2 归一化 `K` | 0.417781725616 | 21/(16π) = 0.417781725616 | 3.99e-16 | ✅ |
| Wendland C4 归一化 `K` | 0.615482006488 | 495/(256π) = 0.615482006488 | 5.41e-16 | ✅ |
| Wendland C6 归一化 `K` | 0.848619130158 | 1365/(512π) = 0.848619130158 | 2.62e-16 | ✅ |
| 混合核归一化 `∫4πx²W dx` | 1.0000000000000007 | 1（构造归一无量纲） | 6.66e-16 | ✅ |
| `K1`（sinc⁴，硬编码 vs 现算） | 0.458917516688 | 0.458917516688（`kernel_3D_k`） | **0**（逐位一致） | ✅ |
| `K2`（sinc⁹，硬编码 vs 现算） | 1.39132214892 | 1.39132214892（`kernel_3D_k`） | **0**（逐位一致） | ✅ |
| `tsKCourant(0, h=1, c=10, Kcour=0.2)` | 0.020000000298 | 0.02 | 1.49e-08 | ✅ |
| `tsKCourant(v=30, h=1, c=10, Kcour=0.2)` | 0.00666666676601 | 0.00666666666667 | 1.49e-08 | ✅ |
| `artificial_viscosity(1,1,10,10,w=-5)` | 100 | 100（`(0.25·2·20 + 2·5)·5`） | 0 | ✅ |
| `artificial_viscosity(1,1,10,10,w=+5)`（分离流） | 0 | 0 | 0 | ✅ |

**leapfrog（`positionUpdate`）常加速度场精确性**：6 个 dt 下位置误差 5.0e-16 … 7.1e-14（机器精度量级），
即对常加速度**严格精确**——证实它是标准 KDK leapfrog，`dX/dt_m1` 反解的是**半步速度** `v_{n-1/2}`。
（本单踩到的坑：初值若把 `v₀` 当 `v_{-1/2}` 喂进去，会看到**一阶**假误差 `|a|·t·dt/2`，见 §5 坑 K2。）

`tsKCourant` 的 1.49e-8 偏差**不是 bug**：`Kcour` 在函数签名里是 `float`（`sph/include/sph/kernels.hpp:16`），
`0.2f = 0.20000000298…`，是单精度量化结果，量级远小于任何物理判据。

---

## 四、结果图

**图 1（左）** 6 个核函数形状（已乘 `K`，`x=r/h∈[0,2]`）：`sinc⁴`、`sinc⁹`、SPHYNX 混合核、Wendland C2/C4/C6。
**图 1（右）** 10 项独立复算的相对误差（对数轴，红虚线=3% 验收线）：最大 1.49e-8，全部过线。

**图 2** `positionUpdate()` 在常加速度场下的位置误差 vs dt：6 个 dt 全部落在 1e-13 以下，
证明上游积分器与解析解一致（无系统性偏移）。

![fig1](figures/fig1_kernels_and_recompute.png)
![fig2](figures/fig2_leapfrog_error.png)

> 图由 `tools/oss_inventory/sphexa_kernel_check.py` 直接从 `kernel_check.json` 生成（matplotlib Agg），
> 无任何手录数据点。报告发布版本以 base64 内嵌（见平台报告 figures 字段）。

---

## 五、注意点（踩过的坑 / 局限 / 适用范围）

- **K1 天体 SPH ≠ 船舶水动力 SPH。** 全仓无多相/自由面/表面张力（`P1` 命中 0）、无 wall particle（`B3` 命中 0）。
  SPH-EXA 的"自由面"是**真空边界**（密度按求和自然跌到 0），这对天体正确，对液舱晃荡/入水
  **缺少压强-密度解耦与壁面不可穿透约束**，直接搬会把 spurious pressure 一起搬过来。
- **K2 半步速度约定是移植第一大坑。** `positions.hpp:100-110` 用 `dX/dt_m1` 反解 `v_{n-1/2}`，再 `+½dt_m1·a` 得 `v_n`；
  若把整数步速度当半步速度初始化，会看到**一阶**误差 `|a|·t·dt/2`（本单首次探针即复现该假象，见 §3.4）。
- **K3 "FBC"不是壁面边界条件。** 固定边界只是把近壁粒子的加速度/速度乘一个 `<1` 因子
  （`positions.hpp:48-70`），本质是**阻尼近似**；用它算壁面剪切/阻力会系统性偏低。
- **K4 没有精度门。** 解析解比对脚本只打印 L1 误差，CI 门是"与上次一致"（rtol 1e-2），
  所以上游**不能**为 `GAP-SPH-WCSPH-VALIDATION` 提供任何"误差 <3%"的可用数据，只能提供工装套路。
- **K5 license 合规**：MIT（宽松）——允许改码/再分发，但**必须保留版权声明与许可文本**。
  本报告所有可迁移条目一律定级 **adapt / reference**：思路照搬 + 在 TensorPhysics 侧重写，
  不整文件复制，避免把 MIT 声明混进我方许可体系（合规判定见 §7 每条 `license_compliance`）。
- **K6 混合精度：坐标 double、流体量 float。** `types.hpp:42-43` 里 `CoordinateType=double`、`HydroType=float`，
  逐粒子 `dtCourant` 随之是 float（`particles_data.hpp:259`）；另外 `tsKCourant()` 的 `Kcour` 形参本身是 `float`
  （`kernels.hpp:16`），§3.4 里的 1.49e-8 即由此单精度量化而来。移植时必须显式决定精度档。
- **K7 未验证项**：本单**没有**构建整码、**没有**跑任何算例，故上游声称的 Exascale 弱/强扩展曲线
  （GH200/LUMI）**未被我们复现**，属 `WITHHELD`；引用其扩展性数字须注明来自上游自述。

---

## 六、误差判定（逐项，显式回答"是否 <3%"）

| 判定对象 | 基准 | 误差 | <3%？ |
|---|---|---|---|
| Wendland C2/C4/C6 归一化常数 | 解析式 21/(16π)、495/(256π)、1365/(512π) | ≤5.41e-16 | **是** |
| 混合核归一化 | 构造值 1 | 6.66e-16 | **是** |
| 硬编码 `K1/K2` vs 运行期 `kernel_3D_k` | 上游自身现算值 | 0（逐位） | **是** |
| CFL 时间步公式 | 解析 `Kcour·h/max(v,c)` | ≤1.49e-8（float32 系数） | **是** |
| 人工黏性公式（压缩/分离两支） | 解析 `−(¼(αᵢ+αⱼ)(cᵢ+cⱼ)−βw)w` | 0 | **是** |
| leapfrog 位置积分（常加速度） | 解析 `x₀+v₀t+½at²` | ≤7.1e-14（绝对，6 个 dt） | **是** |
| SPH 水动力精度（dam-break / Poiseuille / Sedov vs 文献） | — | **未测** | **不适用**：①上游无此类基准（`V4/V5` 命中 0）；②本单未构建整码（§2 未做项）。**不粉饰**：这一层仍为 `WITHHELD`。 |
| 上游 GPU 弱/强扩展数字 | — | **未测** | **不适用**：无 GPU 节点，且数字属上游自述。 |

---

## 七、对 TensorPhysics SPH 线的可迁移方案（≥3，含 license 合规判定）

### 7.0 下游现状对表（实读，非推测）

TensorPhysics（`cxs503/TensorPhysics`, `main`）当前的 SPH 线很薄：
树内 SPH 相关文件 **4 个**（`api /git/trees/main?recursive=1`，1401 文件中共 4 个含 "sph"）：

| 下游位置 | 是什么 | 证据 |
|---|---|---|
| `src/tensorphysics/cross_solver_attribution/sph.py`（199 行） | 1D **Poiseuille** 黏性通道 + 浅水波速两支，用**三次样条核**、两层固定 wall particle、Monaghan 黏性 Laplacian | `sph.py:38`（`kernel_1d`）、`sph.py:52`（`sph_poiseuille`）、`sph.py:120`（`sph_shallow_wave`）、`sph.py:23`（自注 "plug-flow" 倾向） |
| `scenarios/droplet_entry_pilot/run_sph.py`（101 行） | 液滴入水先导，外挂 `TensorSPH`（`/nfs/wangxi/tensor_eco/TensorSPH`），WCSPH 配置 `dt=9e-5`（注释"满足 Courant 条件"）、`sound_speed=20`、`gamma=7` | `run_sph.py:18,31,35` |
| `scenarios/droplet_entry_pilot/outputs/observables_sph*.json` | 先导观测量 | — |

即：**我方 SPH 是"固定 dt + 三次样条核 + 手工 wall particle"的最小实现**，缺三样东西——
核函数高阶化与归一化自检、自适应时间步（个体/块时间步）、可机器裁决的守恒量回归护栏。
SPH-EXA 恰好在**这三样**上是成熟的，且与"没有 ISPH/多相"的短板正好互补地**错位**：
（它的长处是我方短板；它的短板——不可压缩/自由面——我方已有 Poiseuille/浅水腿）。

### 7.1 方案（3 条以上，每条挂 GAP 锚）

**方案卡 1（机器块见 §8）**
```json
{"repo":"sphexa-org/sphexa","id":"SPHSOL-sphexa-01","kind":"adapt",
 "summary":"把逐粒子 CFL + 2^n 块时间步（rung）搬进 TensorSPH：dtCourant=max 归约(组内 sort + fastFraction=0.4 取上限) + numRungs=min(floor(log2(dtmax/dtmin))+1,4) + drift-back 驱动；替代当前 run_sph.py 的固定 dt=9e-5",
 "gap":"GAP-SPH-GPU-SCALING",
 "evidence":["sph/include/sph/kernels.hpp:16-22","sph/include/sph/ts_rungs.hpp:93-152","sph/include/sph/timestep.h:19","main/src/propagator/ve_hydro_bdt.hpp:352-378"],
 "license_compliance":"MIT 宽松：可 adapt；在 TensorPhysics 侧用 Python/Torch 重写实现（不复制 C++ 源文件），注释注明思路出处 sphexa-org/sphexa@28aa0d0d + MIT"}
```
> 理由：我方 `dt=9.0e-5` 是为 Courant 硬降的**全局**步长（`run_sph.py:35`），液滴入水这种
> "局部高速区 + 全局慢区"的算例正是个体时间步的主场；且块时间步是**可度量**的（同精度下步数/墙钟）。

**方案卡 2（机器块见 §8）**
```json
{"repo":"sphexa-org/sphexa","id":"SPHSOL-sphexa-02","kind":"adapt",
 "summary":"核函数归一化自检模块：以 kernel_3D_k（1/∫4πx²W dx，Simpson）现算常数，对 sinc^n / 0.9sinc^4+0.1sinc^9 / Wendland C2,C4,C6 六核在启动时断言 ∫≡1 并比对解析常数；把我方三次样条核升级为可插拔核 + 自带归一化校验",
 "gap":"GAP-SPH-WCSPH-VALIDATION",
 "evidence":["sph/include/sph/sph_kernels.hpp:81-92","sph/include/sph/sph_kernels.hpp:157-158","sph/include/sph/sph_kernels.hpp:237"],
 "license_compliance":"MIT：公式与判据照搬（数学不受版权约束），代码在 TensorPhysics 侧重写；保留出处注释"}
```
> 理由：`cross_solver_attribution/sph.py:38` 的 `kernel_1d` 是硬编码三次样条，**没有任何归一化自检**；
> 本单已验证该套路可把归一化误差压到 1e-16 量级（§3.4），是高性价比的"验证面"增量。

**方案卡 3（机器块见 §8）**
```json
{"repo":"sphexa-org/sphexa","id":"SPHSOL-sphexa-03","kind":"adapt",
 "summary":"体积元(VE)+IAD 正则化：用 dV 矩阵(c11..c33 对称三阶)取代 gradh 标量做梯度/散度，粒子分布退化时按矩阵行列式质量判据触发 ridge 修正；用于提升 TensorSPH 在无序粒子（入水飞溅）下的拉格朗日一致性",
 "gap":"GAP-SPH-ISPH-CONVERGENCE",
 "evidence":["sph/include/sph/hydro_ve/divv_curlv_kern.hpp:113-155","sph/include/sph/iad_regularization.hpp:24-55","sph/include/sph/hydro_ve/xmass_kern.hpp:86"],
 "license_compliance":"MIT：adapt（在 TensorPhysics 侧重写并保留出处）；注：VE 源自 Hopkins 2015 论文，方法本身不受版权约束"}
```
> 理由：我方液滴入水在飞溅/破碎区粒子分布劣化，正是 IAD 的靶场；且收敛性可机器裁决（粒子加密序列 → 梯度误差阶）。

**方案卡 4（机器块见 §8）**
```json
{"repo":"sphexa-org/sphexa","id":"SPHSOL-sphexa-04","kind":"reference",
 "summary":"守恒量回归护栏套路：8 组 IC × CPU/GPU 各存一份参考输出（16 文件），CI 用 rtol=1e-2/atol=1e-6 比对能量/线动量/角动量，超限即 fail；把该模式落到 TensorPhysics 的 SPH 算例上（参考值随 commit 固化）",
 "gap":"GAP-SPH-WCSPH-VALIDATION",
 "evidence":["ci/scripts/compare_constants.py:5-7","ci/scripts/run_constants_comparison.sh:44-100","ci/reference/ (16 files)"],
 "license_compliance":"MIT：reference（只借机制，不搬脚本；我方用 pytest+JSON 基准重写）"}
```
> 理由：我方 SPH 目前**没有**任何回归护栏；这套"守恒量 + 参考文件 + 容差门"是最便宜的结构性防退化装置。

**方案卡 5（机器块见 §8）**
```json
{"repo":"sphexa-org/sphexa","id":"SPHSOL-sphexa-05","kind":"reference",
 "summary":"解析解比对工装：Sedov 自相似解由独立 ODE 求解器生成，配合 compare_solutions.py 的 L1 误差(密度/压力/速度)分列输出——注意上游只打印不判决，我方接入时必须补门限",
 "gap":"GAP-SPH-ASTRO-SEDOV",
 "evidence":["main/src/analytical_solutions/sedov_solution/sedov_solution.cpp","main/src/analytical_solutions/compare_solutions.py:106-127","main/src/analytical_solutions/compare_evrard.py:326,357"],
 "license_compliance":"MIT：reference（L1 判据是通用数学；我方重写并给门限）"}
```
> 理由：Sedov 是 SPH 收敛阶的经典利器；本单已核实其**只有打印无门限**（§3.3 V1 判读），
> 故只作 reference，绝不写成"上游已验证 <3%"。

**方案卡 6（机器块见 §8）**
```json
{"repo":"sphexa-org/sphexa","id":"SPHSOL-sphexa-06","kind":"adapt",
 "summary":"逐粒子人工黏性开关：α 由 div(v) 驱动在 [αmin,αmax] 间升降并按 decay_constant 衰减，替代全局常数 α；可直接量化'黏性耗散对自由面/飞溅结构的侵蚀'",
 "gap":"GAP-SPH-DELTA-SPH",
 "evidence":["sph/include/sph/hydro_ve/av_switches_kern.hpp:43-101","sph/include/sph/kernels.hpp:147-160"],
 "license_compliance":"MIT：adapt（重写 + 出处注释）"}
```
> 理由：我方 `sph.py` 用固定 Monaghan 黏性，入水算例的"数值耗散 vs 物理黏性"无法分离；
> 开关化后可用 α 扫描给出**耗散量化曲线**，直接喂 `GAP-SPH-DELTA-SPH`（δ-SPH 耗散量化）。

**明确不可迁移（诚实负结论）**：
`GAP-SPH-MULTIPHASE`、`GAP-SPH-FSI-BENCHMARK`、`GAP-SPH-DIFFERENTIABLE` **无法**由 SPH-EXA 供料——
它无多相/表面张力（`P1` 命中 0）、无壁面粒子（`B3` 命中 0）、C++/CUDA 无自动微分。
FSI 与可微仍须走 SPHinXsys / jax-sph 线。**它为 `GAP-SPH-GPU-SCALING` 提供的是"方法论"而非"数据"**：
上游扩展性数字本单未复现（`WITHHELD`）。

---

## 八、机器块

[OSS-INV]{"campaign":"osssph-w2","repo":"sphexa-org/sphexa","tier":1,"commit":"28aa0d0d4104524d4e4d47e5ec1780ee8027ed8b","lang":"C++20","license":"MIT","stars":111,"forks":37,"methods":["WCSPH-density-sum","volume-element-VE","IAD-regularization","per-particle-AV-switch","sinc4-sinc9-mixture-kernel","wendland-C2-C4-C6","leapfrog-KDK","block-timesteps-rungs","ideal-gas-EOS","self-gravity-multipole-LET","ewald-periodic-gravity"],"absent_methods":["ISPH","PCISPH","DFSPH","IISPH","PBD","delta-SPH","multiphase","surface-tension","wall-particles","AD-differentiable"],"boundary":"BoundaryType{periodic,open,fixed} per axis + FBC damping factor for near-wall particles; NO ghost/mirror/dynamic boundary particles (grep 0 hits)","gpu":"MPI+OpenMP+CUDA or HIP (GPU_SYMBOL macro, mutually exclusive); GPU-aware halo exchange via cudaMemcpyAsync+P2pTags; GPU octree neighbor search with supercluster nblist; NVIDIA MPS wrapper for multi-rank-per-GPU","validation":"8 analytic-solution L1 scripts (Sedov/Noh/Evrard/Gresho-Chan) WITHOUT tolerance gate; CI machine gate = conservation-constant regression vs 16 reference files (rtol 1e-2); 115 gtest TEST() incl. MPI multi-rank; NO dam-break/Poiseuille/Taylor-Green/Sod (grep 0 hits)","transferable":["SPHSOL-sphexa-01","SPHSOL-sphexa-02","SPHSOL-sphexa-03","SPHSOL-sphexa-04","SPHSOL-sphexa-05","SPHSOL-sphexa-06"],"evidence_artifact":"artifacts/oss-sph-sphexa/sphexa_evidence.json","recompute_artifact":"artifacts/oss-sph-sphexa/kernel_check.json","claims":{"total":37,"positive":31,"negative":6,"failed":0}}[/OSS-INV]

[OSS-SOL]{"repo":"sphexa-org/sphexa","id":"SPHSOL-sphexa-01","kind":"adapt","summary":"per-particle CFL + power-of-2 block timestep rungs (fastFraction=0.4 cap, max 4 rungs) with drift-back driver; replaces fixed dt=9e-5 in TensorSPH droplet pilot","gap":"GAP-SPH-GPU-SCALING","license":"MIT","compliance":"adapt: reimplement in Torch, cite commit"}[/OSS-SOL]

[OSS-SOL]{"repo":"sphexa-org/sphexa","id":"SPHSOL-sphexa-02","kind":"adapt","summary":"kernel-normalization self-check: kernel_3D_k Simpson quadrature + assert integral=1 for 6 kernels, upgrade TensorSPH cubic-spline-only kernel to pluggable family","gap":"GAP-SPH-WCSPH-VALIDATION","license":"MIT","compliance":"adapt: math + code rewritten, attribution note"}[/OSS-SOL]

[OSS-SOL]{"repo":"sphexa-org/sphexa","id":"SPHSOL-sphexa-03","kind":"adapt","summary":"volume-element (dV matrix) gradients + IAD ridge regularization for Lagrangian consistency on disordered (splash) particle distributions","gap":"GAP-SPH-ISPH-CONVERGENCE","license":"MIT","compliance":"adapt: rewrite, cite Hopkins2015 origin"}[/OSS-SOL]

[OSS-SOL]{"repo":"sphexa-org/sphexa","id":"SPHSOL-sphexa-04","kind":"reference","summary":"conservation-constant regression harness: per-IC reference files + rtol=1e-2/atol=1e-6 gate wired into CI, prevents silent physical regression","gap":"GAP-SPH-WCSPH-VALIDATION","license":"MIT","compliance":"reference: mechanism only, pytest+JSON rewrite"}[/OSS-SOL]

[OSS-SOL]{"repo":"sphexa-org/sphexa","id":"SPHSOL-sphexa-05","kind":"reference","summary":"Sedov self-similar ODE solver + L1-error comparison scripts (print-only, NO tolerance) -> adopt as farm work but add a gate on our side","gap":"GAP-SPH-ASTRO-SEDOV","license":"MIT","compliance":"reference: reimplement L1 metric with threshold"}[/OSS-SOL]

[OSS-SOL]{"repo":"sphexa-org/sphexa","id":"SPHSOL-sphexa-06","kind":"adapt","summary":"per-particle artificial-viscosity switch (alpha driven by div(v), decay_constant) to quantify numerical dissipation on free-surface splash structures","gap":"GAP-SPH-DELTA-SPH","license":"MIT","compliance":"adapt: rewrite + attribution"}[/OSS-SOL]

---

### 附：复现命令（本单实际执行）

```bash
# 1) 能力断言 -> file:line 证据表（37 条，0 失败）
python tools/oss_inventory/sphexa_evidence.py \
  --checkout /tmp/sphexa/sphexa-develop \
  --commit 28aa0d0d4104524d4e4d47e5ec1780ee8027ed8b \
  --out artifacts/oss-sph-sphexa/sphexa_evidence.json

# 2) 编译+运行上游 header-only 部件，与解析基准比对（10 项，全部 <3%）
python tools/oss_inventory/sphexa_kernel_check.py \
  --checkout /tmp/sphexa/sphexa-develop \
  --commit 28aa0d0d4104524d4e4d47e5ec1780ee8027ed8b \
  --out artifacts/oss-sph-sphexa/kernel_check.json \
  --figdir artifacts/oss-sph-sphexa/figures
```

检出获取（commit 钉定）：`codeload.github.com/sphexa-org/sphexa/tar.gz/refs/heads/develop` → 解包后
`git` 未初始化，故以 API `/commits/develop` 返回的 sha `28aa0d0d41…` 作为版本锚；tarball 为 develop HEAD
（2026-11-04 `#658` 之后无新 commit，`pushed_at` 2026-11-09 对应其它分支活动）。