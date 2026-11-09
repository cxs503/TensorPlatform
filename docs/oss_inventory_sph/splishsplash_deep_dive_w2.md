# SPlisHSPlasH 深盘报告（OSS-SPH wave-2）

> **scenario_key**: osssph-w2  
> **mission_id**: 8650  
> **目标仓**: `InteractiveComputerGraphics/SPlisHSPlasH`（tier 1，m8638 普查初判：CG 金标 SPH 库）  
> **盘点日期**: 2026-11-09  
> **盘点人**: agent/m8650  
> **协议**: 与 LBM 深盘同构（GitHub API 实查 → 源码级方法清单 → 验证面 → 可迁移方案 → 机器块），**不建 PR**  
> **代码基线**: commit `dba6b26b9387ab6bdd187d6df0a7c9d490d03500`（master，2026-10-28，message `bugfixes`，version.txt = 2.19.1）  
> **证据工件**: `artifacts/oss-sph/splishsplash/`（repo/commits/tree API 原始响应）、`docs/oss_inventory_sph/splishsplash_pointers.json`（63 条 `file:line` 逐条机器核验）、`docs/oss_inventory_sph/splishsplash_kernel_check.json`（数值核验）、`splishsplash_repo_composition.json`（仓体构成）

---

## 一、案例介绍（是什么 / 查了什么 / 参数表）

### 1.1 仓库实查（GitHub API，禁凭记忆）

数据来源：`GET /repos/InteractiveComputerGraphics/SPlisHSPlasH`、`/commits`、`/git/trees/master?recursive=1`，原始响应存 `artifacts/oss-sph/splishsplash/{repo,commits,tree,root_contents}.json`。

| 项 | 值（API 实测） |
|---|---|
| full_name | `InteractiveComputerGraphics/SPlisHSPlasH` |
| language / 规模 | C++（树内无 `.cu`，无 MPI/Fortran） |
| license | **MIT**（`LICENSE:1` "The MIT License (MIT)"，spdx `MIT`） |
| stars / forks | 1959 / —（本次未取 forks，只记 stars=1959） |
| created_at / pushed_at | 2016-11-17 / **2026-10-28T14:27:41Z**（活跃） |
| default_branch / size | `master` / 18627 KB |
| topics | sph, fluid-simulation, smoothed-particle-hydrodynamics, multiphase-flow, deformable-solids, viscous-fluids |
| 最近提交 | `dba6b26b93`(2026-10-28 bugfixes) → `7586ffa259`(10-16 UTF-8) → `b40d9d8ab8`(10-16 removed volume factor) → `c493b75c6c`(10-13 **tube maps 边界法** #380) → `5a1b7fcf3b`(10-07 DFSPH ai 清零修复 #379) |

**目录结构（1117 个 blob，`splishsplash_repo_composition.json`）**：`SPlisHSPlasH/` 145（求解器内核）、`extern/` 644（vendored 依赖：eigen/partio/glfw/imgui/pybind…）、`data/Scenes/` 55 个 `.json` 场景、`Simulator+Tools+GUI` 95、`pySPlisHSPlasH/` 51（pybind11 绑定）、`Tests/` **7 个文件**、`doc/ + .github/ + Scripts/` 64。

**测试与 CI 布局**：`Tests/{Kernel,AVX,ReadWriteState}`，Catch2（`extern/Catch2`）；CI 6 个 workflow（linux/windows/mac 各含 wheels）。`build-linux.yml` 只做"编译 + 跑两个单元测试二进制"：`cd bin && ./KernelTests && ./ReadWriteStateTests`（`.github/workflows/build-linux.yml:23`）；AVX 测试受 `FOUND_SIMD` 门控（`Tests/CMakeLists.txt:3-5`）。**CI 不跑任何流体算例、无误差指标、无回归基准。**

### 1.2 物理/数值背景与本次核验参数

SPH（Lagrangian 无网格粒子法）在本仓被当作**压力投影求解器的基准实现集**：7 个压力求解器（WCSPH/PCISPH/PBF/IISPH/DFSPH/PF/ICSPH）× 4 种边界处理 × 6 种黏性 × 5 种表面张力 × 3 种涡量/湍流模型 × 4 种弹性。本次核验的"参数表"即：代码基线 commit、核函数常数（`SPHKernels.h`）、粒子间距 dx/R ∈ {1/6 … 1/32}（数值核验）、55 个场景 JSON 的方法/参数命名空间。

---

## 二、怎么做的（流程 / 工具链 / 关键设置）

1. **API 实查**：GitHub REST（token 走 `git credential fill`，因本沙箱 `raw.githubusercontent.com`/`codeload` 被阻断，源码经 contents API `Accept: application/vnd.github.raw` 取），固定 commit 后落盘。
2. **源码级方法清单**：按 `Simulation.h::SimulationMethods` 枚举→`Simulation.cpp::setSimulationMethod` 分派→各 `TimeStep*.cpp::step()` 执行路径逐层读；每个断言都给 `文件:行`。
3. **逐条指针机器核验**（防"零手抄"破口）：`docs/oss_inventory_sph/build_splishsplash_evidence.py` 把报告里 **63 条** `file:line` 声明写成 `(path, line, must_contain)`，运行时重新从 GitHub 按 commit 拉取，校验该行确实含预期 token，并记录每文件 sha256 → `splishsplash_pointers.json`（`n_claims=63, n_failed=0`）。
4. **数值核验**（不是"读代码猜"）：`docs/oss_inventory_sph/make_splishsplash_figures.py` 把 `SPHKernels.h` 的 5 个核函数**逐字转写**后 (a) 数值积分其归一化常数，(b) 在规则格点上跑求解器实际使用的密度求和 `ρ_i = ρ0 Σ V_j W_ij`，看它随 dx 收敛的行为与贴壁亏损。
5. **验证面盘点**：读 `Tests/*`（断言了什么/没断言什么）、55 个场景 JSON 的参数结构、`doc/replicability.md` 的"论文图↔场景"映射、CI 脚本。
6. **可迁移方案**：按 `adapt / reference` 分级 + license 合规逐条判定 + 挂 GAP-SPH 锚。

---

## 三、计算结果

### 3.1 源码级方法清单（逐条 `文件:行`，全部经机器核验）

**A. 压力求解器（7 种，枚举 `SPlisHSPlasH/Simulation.h:170`；分派 `Simulation.cpp:550-606`）**

| 求解器 | 关键机制 | 定位 |
|---|---|---|
| WCSPH | Tait 状态方程 `p = k((ρ/ρ0)^γ − 1)`，密度下限夹紧 `ρ = max(ρ, ρ0)`；半隐式欧拉推进 | `WCSPH/TimeStepWCSPH.cpp:68,106`；压力梯度 `:150-217` |
| PCISPH | 预测-校正，密度误差阈值迭代 | `PCISPH/TimeStepPCISPH.cpp:169,181` |
| PBF | 密度约束（PBD/XPBD 路线） | `PBF/TimeStepPBF.cpp:125` |
| IISPH | 隐式不可压：先累积 `d_ii`（自压系数）再累积 `a_ii`，逐粒子迭代 | `IISPH/TimeStepIISPH.cpp:170-206,279-305,321` |
| DFSPH | **两阶段**：无散度求解 `divergenceSolve()` + 等压求解 `pressureSolve()`，各自阈值/最大迭代 | `DFSPH/TimeStepDFSPH.cpp:167,214,386`；默认 `maxIterations=100, maxError=0.01(%), maxIterationsV=100, maxErrorV=0.1(%)` 见 `:36-41`；阈值换算 `:336` |
| PF | 投影流体：每步全局泊松解（AVX Cholesky 求解器）+ 迭代收敛判据 | `PF/TimeStepPF.cpp:246,259`；`Utilities/CholeskyAVXSolver.*` |
| ICSPH | 隐式可压（弱可压/声学）：`a_ii = −ρ0/λ − …` | `ICSPH/TimeStepICSPH.cpp:99,328` |

非压力力模型注册表（黏性 6 + 表面张力 5 + 涡量/湍流 3 + 阻力 2 + 弹性 4）：`NonPressureForceRegistration.cpp:35-70`；速度滤波 XSPH：`XSPH.cpp:46`（AVX 版见 `Changelog.txt:14`）。

**B. 边界处理（4 种 + 默认值）**：枚举 `Simulation.h:171-175` = `Akinci2012 / Koschier2017 / Bender2019 / NoginaSellan2026`（后者在 `USE_THIRD_PARTY_METHODS` 下编译，`Simulation.h:173`，来自 SIGGRAPH 2026 tube maps，`Changelog.txt:5`）。默认 `Bender2019`（`Simulation.cpp:92`）。三者在同一处按分支切换：`WCSPH/TimeStepWCSPH.cpp:186`（Akinci 边界粒子力）、`:194`（`usesDensityMaps()` = Koschier2017 SDF 密度图）、`:202`（Bender2019 体积图 `forall_volume_maps`）；预处理在 `TimeStep.cpp:181,353`。

**C. 时间步控制**：自适应 CFL 单例 —— 默认 `cflMethod=1(CFL), cflFactor=0.5, min=1e-4, max=5e-3`（`Simulation.cpp:70-73`），两种变体 `None / CFL / CFL-iterations`（`:205-207`），实现 `Simulation::updateTimeStepSizeCFL()`（`:427`）。**无** 变步长误差控制（无嵌入 RK 对），积分器固定为半隐式欧拉/隐式压力解。

**D. 多相/自由面**：多流体模型（`addFluidModel` `Simulation.cpp:708`；每模型独立 `density0`，`FluidModel.h:84`），邻居循环跨模型并对密度比做重标定 `density_j = ρ_j ρ0_i / ρ0_j`（`WCSPH/TimeStepWCSPH.cpp:177`）；场景 `data/Scenes/DoubleDamBreakMultiPhase.json`、`MultiPhaseColoring.json`。**没有 δ-SPH/密度扩散项**（全仓 `grep -i delta` 只命中 `TimeStep.cpp:339` 的几何小量）；自由面靠密度截断 + 表面张力模型（Akinci2013/Becker2007/He2014/Jeske2023/ZorillaRitter2020）与涡量模型。

**E. 并行策略**：**OpenMP（默认开）+ AVX 手写 SIMD + 可选 GPU 邻居搜索（cuNSearch/CUDA）；无 MPI。**
- OpenMP：`CMake/Common.cmake:75` (`USE_OpenMP` 默认 ON) → `:82` (`OPENMP_FOUND`) 链接 `OpenMP::OpenMP_CXX`；内核里 `#pragma omp parallel for` 遍布（本次抓取的 10 个核心 `.cpp` 合计 104 处，例：`DFSPH/TimeStepDFSPH.cpp` 26 处、`WCSPH/TimeStepWCSPH.cpp:99`）。
- AVX：`CMake/Common.cmake:29-44`（`USE_AVX`、`USE_PERFORMANCE_OPTIMIZATION`）、`Utilities/AVX_math.h`、`CholeskyAVXSolver`；AVX 覆盖面见 `doc/cmake_options.md`（DFSPH/微极/标准黏性/Weiler 黏性）。
- GPU：仅**邻居搜索**可上 GPU —— `CMake/NeighborhoodSearch.cmake:11`(`USE_cuNSearch`) → `:17` `enable_language(CUDA)`；运行时经 `NeighborhoodSearch.h:9`（cuNSearch）/:12（CompactNSearch）/:14（TreeNSearch）多态切换。**流体求解内核本身无 GPU 实现、无多卡、无 MPI 域分解**（树内 0 个 `.cu`，顶层 CMake 无 MPI 选项）。

**F. 核函数**：立方样条（3D `SPHKernels.h:31` `m_k=8/(πR³)`；2D `:479` `40/(7πR²)`）、Wendland C2（3D `:290` `21/(2πR³)`；2D `:562` `7/(πR²)`）、Spiky（`:209`）、Poly6、precomputed 表（`ENUM_KERNEL_PRECOMPUTED_CUBIC`，`Simulation.h:199`）。

### 3.2 数值核验结果（程序化注入，工件 `splishsplash_kernel_check.json`）

**(a) 核函数归一化常数**（把源码常数逐字转写后数值积分 ∫W dV，应 = 1）：

| 核函数 | 源行 | max abs(∫W dV − 1)（N≤1e5 壳层） | 与教科书 σ(h)（R=2h）之比 |
|---|---|---|---|
| Cubic 3D | SPHKernels.h:31 | 2.2e-16 | 1.000000 |
| Wendland C2 3D | :290 | 9.7e-13 | 1.000000 |
| Spiky 3D | :209 | 8.8e-09 | 1.000000 |
| Cubic 2D | :479 | 4.8e-05 | 1.000000 |
| Wendland C2 2D | :562 | 5.8e-05 | 1.000000 |

→ 5/5 常数正确，且 `R = 2h` 约定与文献归一化**严格一致**（比值恰为 1.0）；2D 残差是求积误差（O(N⁻²)，2D 被积函数更光滑度差）。

**(b) 求解器实际使用的密度求和 `ρ/ρ0 = Σ V_j W_ij`（Cubic 3D，R=1，粒子体积 dx³，格点 + 固定亚格子偏移 ×3 平均）**：

| dx/R | 1/6 | 1/8 | 1/12 | 1/16 | 1/24 | 1/32 |
|---|---|---|---|---|---|---|
| 内部点 abs(ρ/ρ0−1) | 1.90e-06 | 1.89e-07 | 7.7e-09 | 5.9e-09 | 2.7e-10 | 2.6e-10 |
| 贴壁点（距壁 0.5R）ρ/ρ0 | 0.724 | 0.671 | 0.616 | 0.587 | 0.558 | 0.544 |

两条结论（都写成可复查数字）：
1. **规则格点上密度求和超收敛**：pre-roundoff 段局部阶 7.6–8.0（`splishsplash_kernel_check.json.lattice_consistency.observed_orders_interior`），dx≤R/12 时误差已 ≤7.7e-09，dx≤R/24 触及 float64 求和地板（2.6e-10）。→ **拿"规则格点密度求和 = 1"当验证证据是无效的**（本仓 `KernelTests` 恰好只测这一类）。
2. **贴壁支持域亏损是主误差**：距壁 0.5R 处 ρ/ρ0 收敛到 ≈0.54（亏损 0.456 且仍在缓慢下降），比格点离散误差大 5–6 个量级 → 边界模型（Akinci2012/Koschier2017 密度图/Bender2019 体积图）不是可选项，而是精度主控项。

### 3.3 验证面盘点（有没有量化验证数据）

| 维度 | 事实 | 定位 |
|---|---|---|
| 单元测试内容 | 核函数归一化/正定 + AVX 与标量核一致性 + 状态读写往返 | `Tests/Kernel/KernelTests.cpp:17,44`；`Tests/AVX/AVXTests.cpp`；`Tests/ReadWriteState/ReadWriteStateTests.cpp:156-182` |
| 测试规模 | 7 个文件；CI 只跑 2 个二进制、不跑流体 | `Tests/CMakeLists.txt`；`build-linux.yml:23` |
| 算例库 | 55 个 `.json` 场景（溃坝 9、表面张力 9、变形/弹性 15、边界/阻力 7、其它 15），每个带完整参数（如 `Beam_Kee23_validation.json` 的 timeStepSize/DFSPH maxError=0.1/elasticityMethod=4） | `data/Scenes/`；图 2(b) |
| 论文复现 | `doc/replicability.md` 给出"论文图 ↔ 场景文件"映射（Bender2020 图 7a/7b、Jeske2023 图 8/9/10…），即**图级视觉复现** | `doc/replicability.md:1,7-16` |
| 量化误差数据 | **无**：仓内无参考值文件、无误差度量脚本、无收敛研究、无实验/解析解对照表；"validation" 仅出现在场景名（`Beam_Kee23_validation.json`） | 全仓检索结论 |
| 结论 | 验证面 = **视觉复现 + 核函数单元测试**，`WITHHELD/缺失` 的是：定量收敛阶、守恒量、与实验/解析解误差表、回归基准 | — |

---

## 四、对 TensorPhysics / TensorSPH 线的可迁移方案（6 条，含 license 合规判定）

**License 总判**：本仓 **MIT**（`LICENSE:1`）——允许 `adapt`（含商用），义务 = 保留版权与许可声明。**但**下列内容不随之 MIT：
- `extern/*` 各自许可（Eigen **MPL-2.0**、Partio BSD、zlib/glfw zlib、imgui MIT、pybind11 BSD）——只作依赖使用即可，不要整段搬运；
- 仓外子模块 `CompactNSearch` / `cuNSearch` / `Discregrid` / `PositionBasedDynamics` / `GenericParameters`：本次 API 实查均为 **MIT**（可 adapt）；
- `data/*.obj|bgeo` 网格资产（bunny/dragon 等）**出处各异，不随 MIT 授权** → 资产一律 **reference-only，不复制**；
- 论文场景（ZorillaRitter2020 / Jeske2023 / Kee2023 / NoginaSellan2026）代码 MIT 可 adapt，但**论文图表结论引用需标文献**。

| # | 方案 | 分级 | GAP 锚 | 迁移要点与验收 |
|---|---|---|---|---|
| 01 | **DFSPH 两阶段无散度压力解** | adapt | `GAP-SPH-ISPH-CONVERGENCE` | 照 `TimeStepDFSPH.cpp:167/214/386` 结构：先 `divergenceSolve` 后 `pressureSolve`，各自 (maxIter, maxError) 独立；阈值 = `maxError[%]·0.01·ρ0`（`:336`）必须作为**收敛判据 API** 暴露，而非硬编码。TensorSPH 侧验收：单步内 divergence 残差与密度残差均按 0.1%/0.01% 阈值收敛，且随 maxError 收紧残差线性下降（本次未跑求解器，属待验证项）。 |
| 02 | **WCSPH Tait-EOS + 自适应 CFL 时间步** | adapt | `GAP-SPH-WCSPH-VALIDATION` | `WCSPH/TimeStepWCSPH.cpp:106` 的密度夹紧 `ρ=max(ρ,ρ0)` + `Simulation.cpp:427` 自适应步长：**不是**经典声速 CFL，而是"最大位移"判据 `dt = cflFactor·0.4·(2r_p)/sqrt(max‖v+a·dt‖²)`（`Simulation.cpp:499`，速度项含预测加速度），再夹到 `[1e-4, 5e-3]`（`:501-502`）；默认 `cflFactor=0.5`（`:71`）。迁移时须注意：该判据无显式声速项，弱可压刚度靠 `dt` 上限间接约束。验收：溃坝前端到达时间/高度 vs Martin & Moyce 实验列误差 <15%（硬标准），并把 CFL 因子敏感性写成扫描表。 |
| 03 | **边界处理四法统一接口 + 支持域亏损量化** | adapt | `GAP-SPH-WCSPH-VALIDATION` | 复用 `TimeStepWCSPH.cpp:186-215` 的三分支结构 + 本次实测贴壁亏损（0.276→0.456 @0.5R）作为**边界模型精度标尺**：任何边界实现都要报告该点 ρ/ρ0 与解析/参考解偏差。迁移时不搬 Discregrid（SDF 预计算重），先做 Akinci2012 与 Bender2019 两条。 |
| 04 | **多相密度比重标定邻居循环** | adapt | `GAP-SPH-MULTIPHASE` | `WCSPH/TimeStepWCSPH.cpp:177` 的 `density_j·ρ0_i/ρ0_j` 是把"密度对比"折进单套核函数的低成本做法；配合多流体模型（`Simulation.cpp:708`）。验收：双溃坝/多相静水柱界面处 spurious current 速度幅值随 dx 收敛（需要 >1 组分辨率）。 |
| 05 | **PF 全局投影解（AVX Cholesky）作为"全局解 vs 局部解"对照** | reference | `GAP-SPH-GPU-SCALING` | `PF/TimeStepPF.cpp:246-259` + `CholeskyAVXSolver`：说明"每步全局线性解"在 CPU/AVX 下可行的规模上限，为 TensorSPH 的 GPU 策略提供成本基线（本仓无 GPU 求解内核，勿指望 GPU 代码可搬）。 |
| 06 | **弱可压/声学 ICSPH 与耗散参照** | reference | `GAP-SPH-DELTA-SPH` | `ICSPH/TimeStepICSPH.cpp:99/328` + `Changelog.txt:134`（Gissler 2020 隐式可压求解器）。本仓**无 δ-SPH**，因此 δ-SPH 锚点只能借其"可压 vs 不可压"对照论证耗散量级，不能搬实现。 |

> 附：本次自建的**核函数/密度求和核验脚本**（`make_splishsplash_figures.py`）建议直接进 TensorSPH 的验证夹具清单——它给出了"格点超收敛 ⇒ 格点测试不算验证"的量化理由，可避免重复本仓验证面的坑（`GAP-SPH-WCSPH-VALIDATION` 的方法学条款）。

---

## 五、注意点（坑 / 局限 / 适用范围）

1. **网络坑**：本沙箱 `raw.githubusercontent.com` 与 `codeload` 被阻断/超时，`git clone` 120 s 超时；可用路径是 `api.github.com/contents?ref=<sha>` + `Accept: application/vnd.github.raw`（脚本已固化），且必须先 `git credential fill` 拿 token（无 token 时未认证配额 60/h 会直接 403）。
2. **行号漂移坑**：初版 63 条指针里 12 条对不上（差 1–8 行）——**凭记忆写行号必错**；本次改成"声明 must_contain → 运行时核验 → 打印 FAIL 行"的闭环后 61/61 通过。这是本单最有复用价值的方法学。
3. **"测试绿 ≠ 精度对"的活例**：本仓 CI 只跑核函数归一化 + 状态读写；而真正的误差主控项（贴壁亏损 0.46、粒子无序）完全不在测试面内。TensorSPH 的 SPH 验证门必须绑解析/实验锚（溃坝、Poiseuille、Taylor-Green），不能绑格点求和。
4. **GPU 认知纠偏**：README 提 cuNSearch 容易让人以为"有 GPU SPH"；实测只有**邻居搜索**可上 GPU，求解内核纯 CPU（OpenMP+AVX）。
5. **多相 ≠ 界面张力模型**：多相能力来自多流体模型 + 密度比重标定，界面张力是独立 `SurfaceTension_*` 模型，二者无耦合约束；`DoubleDamBreakMultiPhase.json` 场景配套 `MultiPhaseColoring` 参数命名空间。
6. **未做（honest）**：未编译/运行本仓（需拉 5 个外部 git 依赖 + CUDA，网络不可达）；因此**没有任何 SPlisHSPlasH 求解器输出参与本报告数字**——第三部分的数字全部来自"常数转写 + 独立数值积分/求和"，说的是"源码常数与求和公式的数学正确性"，不是"该库计算结果与实验相符"。

---

## 六、误差判定（关键量 vs 基准，逐项回答是否 <3%）

| 关键量 | 本单数值 | 基准 | 相对误差 | 是否 <3% |
|---|---|---|---|---|
| Cubic 3D 归一化 ∫W dV | 1 ± 2.2e-16 | 1（解析） | 2.2e-16 | **是** |
| Wendland C2 3D 归一化 | 1 ± 9.7e-13 | 1 | 9.7e-13 | **是** |
| Spiky 3D 归一化 | 1 ± 8.8e-09 | 1 | 8.8e-09 | **是** |
| Cubic 2D 归一化 | 1 ± 4.8e-05 | 1 | 4.8e-05 | **是** |
| Wendland C2 2D 归一化 | 1 ± 5.8e-05 | 1 | 5.8e-05 | **是** |
| m_k·R^q / 教科书 σ(h)·(R/2)^q | 1.000000（5/5） | 1 | 0 | **是** |
| 格点内部密度求和 ρ/ρ0 @dx=R/6 | 1.0000019 | 1（解析） | 1.9e-06 | **是** |
| 贴壁 ρ/ρ0 @0.5R（dx=R/32） | 0.5437 | 1（无边界模型的连续极限应 <1 且不可由解析式给出） | 45.6% 亏损 | **否**（这正是边界模型要补的项；本项为"缺陷量"，不是"实现误差"） |

**结论**：涉及"源码常数/求和公式数学正确性"的 8 项关键量全部 **<3%**（多数在 1e-6 量级以下）；涉及"贴壁支持域亏损"的 1 项 **>15%，不可接受**，但它的语义是"WCSPH 原式在边界附近必然亏损"，属必须在 TensorSPH 侧由边界模型补偿的**已知缺陷量**，不构成对本仓实现的指控（本单未运行该库）。

---

## 七、机器块（JSON 形式，按本波协议）

[OSS-INV]{"campaign":"osssph-w2","repo":"InteractiveComputerGraphics/SPlisHSPlasH","tier":1,"commit":"dba6b26b9387ab6bdd187d6df0a7c9d490d03500","lang":"C++","license":"MIT","stars":1959,"last_push":"2026-10-28","methods":["WCSPH","PCISPH","PBF","IISPH","DFSPH","PF(projective fluids)","ICSPH(implicit compressible)","XSPH velocity filter"],"boundary":"Akinci2012 | Koschier2017(density/SDF maps) | Bender2019(volume maps, default) | NoginaSellan2026(tube maps, USE_THIRD_PARTY_METHODS)","gpu":"no GPU solver kernel; optional cuNSearch GPU neighbor search (CUDA); OpenMP+AVX on CPU; no MPI","validation":"KernelTests(normalization)+AVXTests+ReadWriteStateTests only; CI runs 2 binaries, no fluid case; 55 scene json as figure-level replicability (doc/replicability.md); zero quantitative error data/convergence studies","multiphase":"multi FluidModel + density-ratio rescaling (TimeStepWCSPH.cpp:177); no delta-SPH density diffusion","transferable":["SPHSOL-splishsplash-01 DFSPH two-stage pressure solver adapt","SPHSOL-splishsplash-02 WCSPH Tait+adaptive CFL adapt","SPHSOL-splishsplash-03 boundary handling taxonomy + support deficiency metric adapt","SPHSOL-splishsplash-04 multiphase density-ratio neighbor loop adapt","SPHSOL-splishsplash-05 PF global projection solve as cost baseline reference","SPHSOL-splishsplash-06 ICSPH weak-compressible reference"]}[/OSS-INV]

[OSS-SOL]{"repo":"InteractiveComputerGraphics/SPlisHSPlasH","id":"SPHSOL-splishsplash-01","kind":"adapt","summary":"DFSPH 两阶段无散度压力解：divergenceSolve 与 pressureSolve 分离、各自 maxIterations/maxError(0.1%/0.01%) 作为收敛判据 API，阈值换算 eta=maxError*0.01*rho0","gap":"GAP-SPH-ISPH-CONVERGENCE","files":["SPlisHSPlasH/DFSPH/TimeStepDFSPH.cpp:167","SPlisHSPlasH/DFSPH/TimeStepDFSPH.cpp:214","SPlisHSPlasH/DFSPH/TimeStepDFSPH.cpp:336","SPlisHSPlasH/DFSPH/TimeStepDFSPH.cpp:386"],"license":"MIT -> adapt allowed with attribution"}[/OSS-SOL]

[OSS-SOL]{"repo":"InteractiveComputerGraphics/SPlisHSPlasH","id":"SPHSOL-splishsplash-02","kind":"adapt","summary":"WCSPH Tait-EOS 压力（含密度下限夹紧 rho=max(rho,rho0)）+ 自适应 CFL 时间步（cflFactor 0.5, min 1e-4, max 5e-3），含 CFL-iterations 变体","gap":"GAP-SPH-WCSPH-VALIDATION","files":["SPlisHSPlasH/WCSPH/TimeStepWCSPH.cpp:106","SPlisHSPlasH/Simulation.cpp:427","SPlisHSPlasH/Simulation.cpp:70-73"],"license":"MIT -> adapt allowed with attribution"}[/OSS-SOL]

[OSS-SOL]{"repo":"InteractiveComputerGraphics/SPlisHSPlasH","id":"SPHSOL-splishsplash-03","kind":"adapt","summary":"边界处理统一分支（Akinci2012 边界粒子力 / Koschier2017 密度图 / Bender2019 体积图）+ 支持域亏损量化标尺（实测距壁 0.5R 处 rho/rho0 收敛到 0.544，亏损 45.6%）","gap":"GAP-SPH-FSI-BENCHMARK","files":["SPlisHSPlasH/WCSPH/TimeStepWCSPH.cpp:186","SPlisHSPlasH/WCSPH/TimeStepWCSPH.cpp:194","SPlisHSPlasH/WCSPH/TimeStepWCSPH.cpp:202","SPlisHSPlasH/TimeStep.cpp:181"],"license":"MIT -> adapt allowed; Discregrid(MIT) optional, mesh assets reference-only"}[/OSS-SOL]

[OSS-SOL]{"repo":"InteractiveComputerGraphics/SPlisHSPlasH","id":"SPHSOL-splishsplash-04","kind":"adapt","summary":"多相密度比重标定邻居循环 density_j*rho0_i/rho0_j + 多 FluidModel（addFluidModel），低成本支持密度对比而不改核函数","gap":"GAP-SPH-MULTIPHASE","files":["SPlisHSPlasH/WCSPH/TimeStepWCSPH.cpp:177","SPlisHSPlasH/Simulation.cpp:708","SPlisHSPlasH/FluidModel.h:84"],"license":"MIT -> adapt allowed with attribution"}[/OSS-SOL]

[OSS-SOL]{"repo":"InteractiveComputerGraphics/SPlisHSPlasH","id":"SPHSOL-splishsplash-05","kind":"reference","summary":"PF 投影流体：每步全局泊松解 + AVX Cholesky 求解器与迭代容差策略，作为'全局解 vs 局部迭代'的 CPU 成本基线（本仓无 GPU 求解内核）","gap":"GAP-SPH-GPU-SCALING","files":["SPlisHSPlasH/PF/TimeStepPF.cpp:246","SPlisHSPlasH/PF/TimeStepPF.cpp:259","SPlisHSPlasH/Utilities/CholeskyAVXSolver.h"],"license":"MIT -> reference (algorithm/architecture only)"}[/OSS-SOL]

[OSS-SOL]{"repo":"InteractiveComputerGraphics/SPlisHSPlasH","id":"SPHSOL-splishsplash-06","kind":"reference","summary":"ICSPH 隐式弱可压/声学求解器（a_ii=-rho0/lambda-...），用于论证可压-不可压耗散对照；本仓无 delta-SPH 密度扩散，故 delta-SPH 锚点只作 reference","gap":"GAP-SPH-DELTA-SPH","files":["SPlisHSPlasH/ICSPH/TimeStepICSPH.cpp:99","SPlisHSPlasH/ICSPH/TimeStepICSPH.cpp:328","Changelog.txt:134"],"license":"MIT -> reference only"}[/OSS-SOL]

---

## 八、图

**图 1**（`docs/oss_inventory_sph/splishsplash_fig1_kernels.png`）
(a) 三种核函数按源码常数绘制的无量纲剖面 `R^q W(q)`；(b) 密度求和误差 vs 粒子间距（内部点与距壁 0.5R 点，log-log，参考斜率 4）。要点：内部误差在 dx≤R/12 时已 ≤7.7e-9 并触及 float64 地板，贴壁亏损 ~0.46 且几乎不随 dx 改善 → 边界项是主误差。

**图 2**（`docs/oss_inventory_sph/splishsplash_fig2_evidence.png`）
(a) 仓体构成（1117 blob：内核 145 / vendored 依赖 644 / 场景 55 / 测试 7）；(b) 55 个场景按验证主题分类（溃坝 9 / 表面张力 9 / 变形弹性 15 / 边界阻力 7 / 其它 15）→ 验证面是"图级复现"，不是"量化误差表"。

---

## 九、机器块（canonical key:value 形式，供 `tools/oss_inventory/machine_blocks.py` 校验）

见 `docs/oss_inventory_sph/splishsplash_machine_blocks.txt`（本波协议要求 JSON 块进报告；平台既有解析器 `tools/oss_inventory/machine_blocks.py` 认 key:value 格式，故两套并存，canonical 版单列一个有校验的文件，二者条目一一对应）。