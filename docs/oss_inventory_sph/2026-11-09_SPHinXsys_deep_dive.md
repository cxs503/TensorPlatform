# SPHinXsys 深盘（SPH 波2 · Tier-1 第三仓）：源码级方法清单 · 验证面 · 可迁移方案

> **scenario_key**: oss-inventory　**campaign**: osssph-w2　**mission_id**: 8652
> **仓库**: `Xiangyu-Hu/SPHinXsys`（tier 1，m8638 普查判定"多物理 FSI 工程 SPH"）
> **快照**: branch `master`，HEAD `1612330e44c8947406f24b6756449e80dcd7158e`（commit date 2026-10-25T21:01:50+01:00，subject `Merge pull request #1129 … add_two_phase_flow`）
> **取证方式**: GitHub REST（`GET /repos/Xiangyu-Hu/SPHinXsys`，2026-11-09T14:47Z 单次观测；随后 REST 配额被同 IP 其他盘点任务耗尽 → HTTP 403）× blobless 部分克隆 `git clone --filter=blob:none --depth 1 --no-checkout` + 定向 `git checkout master -- <paths>`（走 git 协议，不消耗 REST 配额）
> **产物**: `artifacts/oss_sph_w2/evidence.json`（全部计数与探针结果）、`artifacts/oss_sph_w2/build_evidence.py`（可重跑采集器 + 报告指针校验器）、`artifacts/oss_sph_w2/fig_method_coverage.png`、`tools/oss_inventory/repo_fetch.py`（本次沉淀的取数工具，12 测试通过）
> **纪律**: 每条方法结论带 `file:line`，指针由 `build_evidence.py --verify-report` 机器校验；代码里没有的能力直接写 ABSENT。

---

## 一、案例介绍（对象与背景）

SPHinXsys 是面向工业多物理的 SPH（Smoothed Particle Hydrodynamics）**C++ API 库**：作者 Xiangyu Hu（TUM）团队，DFG 资助（HU1527/6-1、10-1、12-1、12-4，见 `src/shared/kernels/kernel_wendland_c2.h:10-12`）。定位不是单一求解器，而是"用统一粒子框架把流体、固体、多体、颗粒、扩散反应强耦合"的库——这也是它同时成为 FSI 基准与多相算例常用参照实现的原因。

| 项 | 值 | 证据 |
|---|---|---|
| 主语言 | C++（CMake） | REST `language=C++`；`CMakeLists.txt:2` |
| 许可证 | **Apache-2.0** | REST `license.spdx_id=Apache-2.0`；`LICENSE:1-2`（`Apache License / Version 2.0`） |
| Stars / Forks / Open issues | **600 / 391 / 49** | REST `GET /repos/Xiangyu-Hu/SPHinXsys`（2026-11-09T14:47Z 单次观测；随后配额耗尽未能复采） |
| 创建 / 最近 push | 2019-05-28 / **2026-11-08T19:19:56Z** | 同上 REST（HEAD commit 本体 2026-10-25，push 更新 → 分支仍活跃） |
| 仓库规模 | 快照 **2307 个被跟踪文件**，`src/` 612 文件；`src/**.h/hpp/cpp` 合计 **34605 行** | `evidence.json:counts`（`git ls-tree -r`）+ `src_lines_probed` |
| 测试规模 | 142 个算例目录（112 CPU + 30 SYCL）、**112 带回归基线**、601 回归 XML、114 DTW 基线、145 运行时间表、71 单元测试源文件 | 同 evidence.json |
| CI | 单文件 `.github/workflows/ci.yml`，8 个 job：`Linux-sycl`/`Linux-build`/`Linux-test`/`Windows-build`/`Windows-test`/`macOS-build`/`macOS-test`/`Windows-dispatch-for-sycl-build` | `.github/workflows/ci.yml:54`、`:218`、`:297`、`:444`、`:526`、`:654`、`:725`、`:854` |
| 依赖 | **TBB 强制**、可选 VTK、可选 SYCL（默认 OFF，需 IntelLLVM） | `CMakeLists.txt:64`、`:16`、`:115` |

**与 m8638 普查的对账**：普查登记 600 stars / C++ / Apache-2.0 / Tier-1（`docs/oss_inventory_sph/REPORT_oss-sph-disc.md:78`），本次三项全部复现（600 / C++ / Apache-2.0），偏差 0%。

---

## 二、怎么做的（方法/流程/工具链）

1. **REST 实查（禁凭记忆）**：`GET /repos/{owner}/{name}`、`/commits?per_page=10`、`/git/trees/master`——首轮成功拿到 stars/license/语言/目录树；第二轮起 HTTP 403（未认证配额 60 次/小时**按出口 IP 计**，本机同时有多个盘点 agent 在跑 SPH 仓，见 §5 坑 1）。
2. **改用 git 协议取源码**：`git clone --filter=blob:none --depth 1 --no-checkout`（树全量、blob 按需），再分批 `git checkout master -- <pathspec>`。`src/` 全量 612 文件 + 关键算例/CI/CMake 落地到 `/tmp/spx_probe`。
3. **探针化方法清单**：`build_evidence.py` 内置 **17 条方法探针**（正则 × 期望存在/不存在），扫 612 源文件，输出 `present/verified/首次命中 file:line`；期望与实际不符即退出码 1（本次 17/17 一致）。
4. **计数不手抄**：算例/基线/单元测试/SYCL 例数量由 `git ls-tree -r` 程序化统计，报告数字全部来自 `evidence.json`。
5. **指针机器校验**：报告写完后 `build_evidence.py --verify-report` 抽出所有 `path:line`，逐条检查文件存在、行号在范围内并回显原文。
6. **沉淀工具**：把"REST 配额耗尽时如何取上游源码"做成 `tools/oss_inventory/repo_fetch.py`（三策略 api/clone/tar + sha256 manifest），`tests/test_oss_inventory_repo_fetch.py` 12 测试离线通过。

图 1（`artifacts/oss_sph_w2/fig_method_coverage.png`）：左＝17 条能力探针命中情况（绿＝有 / 红＝无），右＝验证面计数（算例/回归基线/DTW/单元测试/SYCL/文档帖）。

---

## 三、SPH 变体与数值方法清单（逐条 file:line）

### 3.1 方法族有无总表（探针 17/17 与预期一致）

| 方法 | 状态 | 定位 |
|---|---|---|
| **WCSPH（弱可压缩）** | **REAL** | EOS `p=p0(rho/rho0-1)`、`p0=rho0*c0^2`：`src/shared/materials/weakly_compressible_fluid.cpp:7-11`、`src/shared/materials/weakly_compressible_fluid.hpp:16`；两步积分 `src/shared/particle_dynamics/fluid_dynamics/fluid_integration.hpp:26`（1st half）、`:159`（2nd half） |
| 密度求和（含自由面修正） | **REAL** | `src/shared/particle_dynamics/fluid_dynamics/density_summation.hpp:20`（`inv_sigma0_`）、`:31`（`rho=max(rho_sum,rho0)`）、`:45`（近表面判据） |
| **Riemann 型人工耗散** | **REAL** | `src/shared/materials/riemann_solver.h:124`（`using DissipativeRiemannSolver = BaseAcousticRiemannSolver<NoLimiter>`）、`:111`（`p_star = p_ave + 0.5*rho0c0*u_jump*limited_mach`）、`:112`（`u_dissipative`）；作用于密度 `fluid_integration.hpp:76`、`:79`，动量 `:192`、`:195`；CK 新内核同源 `src/shared/shared_ck/particle_dynamics/fluid_dynamics/acoustic_step_1st_half.hpp:107` |
| **δ-SPH 显式扩散项** | **ABSENT** | 全 `src/` 无 `DeltaSPH / delta_sph / DensityDiffusion` 命中（探针 `expected=False` 通过）；耗散改由 Riemann 解器 + 输运速度修正承担 |
| 压缩量密度正则化（CK 新 API） | **REAL**（与 δ-SPH 不同宗） | `CompressionSummation` / `DensityRegularization`：`src/shared/shared_ck/particle_dynamics/fluid_dynamics/density_regularization.cpp:11`；`.hpp` 末段 `rho_[i]=compression_[i]*eos_.getReferenceDensity(i)` |
| **ISPH / PCISPH / IISPH** | **ABSENT** | 全 `src/` 无 `\bISPH\b|PCISPH|IISPH`；无压力泊松求解器代码 |
| **DFSPH** | **ABSENT** | 无 `\bDFSPH\b` 命中 |
| **PBD（位置基动力学）** | **ABSENT** | 无 `PositionBasedDynamics|class *PositionBased|\bPBD\b`（唯一"position based"字样是 `src/for_3D_build/particle_dynamics/solid_dynamics/polar_decomposition_3x3_impl.h:816` 的"polar decomposition based on"文献注释，**非 PBD**——首版探针在此误命中，已收紧正则） |
| 输运速度修正 / 粒子移位 | **REAL** | `src/shared/shared_ck/particle_dynamics/fluid_dynamics/transport_velocity_correction_ck.hpp`（`dpos_ += coefficient*h_ref^2*limiter(h_ref^2||residual||^2)*residual/h_ratio^2`） |
| 黏性（层流 + 湍流） | **REAL** | `src/shared/shared_ck/particle_dynamics/fluid_dynamics/viscous_force.hpp:35`、`:59`（`getInterParticleViscosity`，`vel_derivative*dW_ijV_j`） |
| **欧拉可压缩 SPH** | **REAL** | `src/shared/particle_dynamics/fluid_dynamics/muscl_reconstruction.hpp`、`muscl_hllc_integration.h`、`eulerian_fluid_dynamics/eulerian_riemann_solver.h` |
| 多相 / 混合物 | **REAL** | `WeaklyCompressibleMixture`、`WeaklyCompressibleMultiSpecies`：`src/shared/materials/weakly_compressible_fluid.h:72`、`:109`；CK `fluid_mixture/fluid_mixture_dynamics.h` |
| 表面张力 | **REAL** | `src/shared/particle_dynamics/fluid_dynamics/surface_tension.h`（`class SurfaceTension`） |
| 固体：弹性/塑性/JC/HJC/薄壳 | **REAL** | `src/shared/materials/elastic_solid.h`、`inelastic_solid.h`、`johnson_cook_solid.h`、`hjc_solid.h`；壳 `thin_structure_dynamics.h` |
| FSI 双向耦合 | **REAL** | `class ForceFromFluid`：`src/shared/shared_ck/particle_dynamics/fluid_structure_interaction/force_on_structure.h:45`；结构面运动 `structure_surface_motion.h`；经典接口 `src/shared/particle_dynamics/solid_dynamics/fluid_structure_interaction.h` |

### 3.2 边界处理

| 边界 | 定位 | 做法 |
|---|---|---|
| 壁面 contact | `src/shared/particle_dynamics/fluid_dynamics/fluid_integration.hpp:83`（`Contact<Wall>` 特化） | 壁面虚拟压力 `p_j_in_wall`，含法向加速度修正 `acoustic_step_1st_half.hpp:172`（`p_i + rho_i*r_ij*max(0, face_wall_external_acceleration)`） |
| 近壁距离/夹逼 | `src/shared/particle_dynamics/fluid_dynamics/boundary_condition/near_wall_boundary.h:42`、`:57`、`:68` | `NearWallDistance` / `DistanceFromWall` / `BoundingFromWall` |
| 进出口 / 缓冲 | `.../boundary_condition/fluid_boundary.h:65`、`:88`、`:129`、`:174`、`:192`、`:223`、`:246` | `FlowVelocityBuffer`、`InflowVelocityCondition`、`FreeStreamVelocityCorrection`、`DampingBoundaryCondition`、`EmitterInflowCondition/Injection`、`DisposerOutflowDeletion`（发射器/删除器式进出口） |
| 非反射 | `.../boundary_condition/non_reflective_boundary.h:39` | `NonReflectiveBoundaryCorrection` |
| 核梯度修正（边界缺失补偿） | `fluid_integration.hpp:126`（`KernelCorrectionType`）、`acoustic_step_1st_half.hpp:103` | 模板化 kernel correction，与 Riemann 耗散共用同一积分核 |

### 3.3 时间步控制（双时间步）

| 项 | 定位 | 说明 |
|---|---|---|
| 声学步长 | `src/shared/shared_ck/particle_dynamics/fluid_dynamics/fluid_time_step_ck.hpp:35` | `dt = acousticCFL*h_min/(reduced+ε)`，默认 `acousticCFL=0.6` |
| 声学步长（含加速度界） | `fluid_time_step_ck.hpp:54`、`:55` | `force_norm=(F+F_prior).norm()`、`acc_scale=sqrt(4*h_min*force_norm/m)` |
| 对流步长 / 邻域重建 | `src/shared/particle_dynamics/fluid_dynamics/fluid_time_step.h`（`AdvectionTimeStep`、`AcousticTimeStepWithAcceleration`、`WallAccelerationTimeStep`） | 外层对流步控邻域与排序，内层声学子步满足 CFL；作者注释明确"把加速度判据移入声学子步是另一套 dual-time-stepping，不是可互换默认"（同文件 `:55-63` 文档段） |
| 粒子排序 | `src/shared/particle_dynamics/configuration_dynamics/particle_sorting.hpp` | cache 优化；算例中 `ParticleSorting` 实例化（`tests/2d_examples/test_2d_dambreak/Dambreak.cpp:110`） |

### 3.4 多相 / 自由面

- **自由面**：密度求和近表面判据（`density_summation.hpp:45`）+ 自由面标识特化（`density_summation.h:131`、`:158`）。
- **多相**：`WeaklyCompressibleMixture`（局部参考密度场 `dv_rho0_`，`weakly_compressible_fluid.h:88`）、`WeaklyCompressibleMultiSpecies`（组分密度表 `:109`）；HEAD 提交即 "add two phase dambreak flow"（PR #1129）。
- **界面力**：`SurfaceTension`（CSF 型，`surface_tension.hpp`）+ `density_gradient.hpp`。

### 3.5 并行策略（关键判定：**无 MPI**）

| 维度 | 结论 | 证据 |
|---|---|---|
| 共享内存并行 | **TBB**（强制依赖） | `CMakeLists.txt:64`、`:65`；`src/shared/common/sphinxsys_tbb.h` |
| 设备卸载 | **SYCL**（可选，默认 OFF，需 `icpx`，目标 `spir64_x86_64`） | `CMakeLists.txt:16`、`:115`、`:137`；`src/src_sycl/`（11 文件）；30 个 SYCL 算例 |
| 执行策略抽象 | **REAL**：CK 内核以 `ExecutionPolicy` 模板参数区分 host/device | `src/shared/shared_ck/particle_dynamics/execution/execution_policy.h`、`implementation.h` |
| MPI / 分布式 | **ABSENT** | 探针 `MPI_Init|MPI_Comm|#include <mpi.h>` 全 `src/` 零命中；`CMakeLists.txt` 无 MPI 选项 |
| 稀疏网格异构 | 文档帖 `docs/_posts/2025-12-11-sparse-grid-heterogeneous-p1.md` 等 4 篇（**仅文件名可证，内容未读**） | `evidence.json:counts.docs_posts=19` |

> 一句话：**shared-memory（TBB）+ 单节点设备（SYCL），无跨节点并行**。故 `GAP-SPH-GPU-SCALING` 上它只能当"CPU 线程扩展 + 单卡卸载"参照，不能当多卡/MPI 强-弱扩展参照。

---

## 四、验证面盘点

### 4.1 自带算例与基准

| 主题 | 算例 | 对标对象 |
|---|---|---|
| 溃坝（经典无因次设置） | `tests/2d_examples/test_2d_dambreak/Dambreak.cpp`：域 5.366×5.366、水柱 2.0×1.0、`particle_spacing_ref=0.025`、`rho0=1, g=1, c=10*U_ref`（`:13-17`、`:22-25`） | 无内嵌解析解；用 DTW 回归基线（`Dambreak.cpp:118`） |
| 顶盖驱动方腔 | `tests/2d_examples/test_2d_lid_driven_cavity_corrected/lid_driven_cavity.cpp:12-25`（`DL=DH=1.0`、`resolution=1/50`、`Re=100`、5 观测点 `:94-95`） | 自回归基线（**未内嵌 Ghia 表**） |
| FSI 基准 | `tests/2d_examples/test_2d_fsi2/fsi2.cpp:41`（水 + `SaintVenantKirchhoffSolid` 插入体；首行注明出自 Zhang/Rezavand/Hu 论文 `:5`）、`test_2d_hydrostatic_fsi`、`test_2d_nonlinear_wave_fsi`、`test_3d_dambreak_elastic_plate_shell` | 论文对比（代码内无误差表） |
| 可压缩/欧拉 | `test_1d_shock_tube`、`test_2d_FVM_double_mach_reflection`、`test_2d_eulerian_taylor_green_LG`、`test_2d_eulerian_supersonic_flow_new_BC` | Taylor-Green 有解析衰减，代码内未存误差表 |
| Poiseuille | `test_3d_poiseuille_flow_shell`、`test_3d_FVM_incompressible_channel_flow` | 解析抛物线剖面（未内嵌误差断言） |
| 多相/液滴/颗粒 | `test_3d_cubic_droplet`、`test_2d_droplet_impact`、`test_3d_repose_angle` | DTW 基线 |
| 固体/断裂/接触 | `test_3d_taylor_bar`（含 `_UL`/`_hjc` 三变体）、`test_3d_johnson_cook_necking`、`test_2d_cohesive_soil_failure`、`test_3d_self_contact` | 解析/文献（未内嵌误差表） |
| 生物/电生理 | `test_3d_heart_electromechanics`、`test_3d_pkj_lv_electrocontraction`、`test_3d_muscle_activation` | 论文对比 |

### 4.2 测试框架

- **回归基线框架**：算例目录内 `regression_test_tool/`，112 个算例配备；601 个 `*_result.xml`、**114 个 `*_dtwdistance.xml`**（DTW 距离阈值基线）、145 个 `*_runtimes.dat`。C++ 入口 `RegressionTestDynamicTimeWarping<...>`（`Dambreak.cpp:118`）；DTW 实现在 `src/shared/regression_test/dynamic_time_warping_method.hpp`（同目录另有 `time_average_method`、`ensemble_average_method`）。
- **单元测试**：71 个源文件（`tests/unit_tests_src/`）+ `tests/extra_source_and_tests/`。
- **CI 判定方式**：`Linux-test` 等 job 跑 ctest，并对失败用例**最多重试 10 次**（`.github/workflows/ci.yml:130`、`:138`…`:210`，逐次 "Test with the second…tenth try for failed cases"）。

### 4.3 量化验证数据：有，但性质必须说清

- **有**：结构化、可机读的参考时间序列 + DTW 距离阈值 + 运行时间表（601/114/145 个文件），足以支撑**自回归一致性**门禁。
- **缺**：**解析解/实验数据的误差断言不在仓内**（算例代码无 `<3%` 类容差判据，也无与实验点对比的 CSV）；对标真值需使用者自行后处理（`io_observation` + Python 脚本）。
- 结论：验证面 = **回归（自一致）强、绝对精度（对真值）弱**。对本生态"误差>15% 不可接受"的判据，它只能提供**算例与协议**，不能提供**误差结论**。

---

## 五、注意点（踩坑 / 局限 / 适用范围）

1. **REST 配额按出口 IP 计（坑）**：本机多个盘点 agent 同时打 api.github.com，60 次/小时额度数分钟内被吃光，之后 `/contents` 全部 403。教训：**别把 REST 当取源码手段**，一上来就用 blobless 克隆（已固化为 `tools/oss_inventory/repo_fetch.py`）。
2. **codeload tarball 不支持 Range（坑）**：`…/tar.gz` 流式生成，`Range` 头被忽略（返回 200 且无 `Content-Range`），配合 120 秒 shell 超时**不可续传**；大仓必须走 git 协议分路径 checkout。
3. **路径漂移**：master 目录结构与旧教程差异大（无 `src/shared/particle_dynamics/fluid_structure_interaction/`；经典接口在 `solid_dynamics/`，CK 新接口在 `shared_ck/`）。指针必须带 commit SHA。
4. **探针正则要带词界（真实假阳性）**：`position.based` 会误命中 "decom**position based** on"（`polar_decomposition_3x3_impl.h:816`），把 ABSENT 的 PBD 判成 REAL；已用 `\bPBD\b|PositionBasedDynamics` 收紧。
5. **"有回归数据" ≠ "有精度验证"**：601 个 XML 全是**自己历史运行**的参考值；CI 还带 10 次重试，说明基线本身对数值抖动敏感。拿它当"精度证明"会自欺。
6. **许可证**：Apache-2.0，允许修改/再分发（保留 NOTICE/许可声明与专利条款）；但实现携带 TUM/DFG 版权的具体代码表达，我们优先"读法→干净室重写"。
7. **未做**：未编译、未运行任何 SPHinXsys 算例（C++/TBB/SYCL 工具链小时级编译超出本单边界），故本文**不做任何数值精度结论**（见 §7）。

---

## 六、对 TensorPhysics SPH 线的可迁移方案（7 条，分级 + 许可证判定）

**许可证判定（显式）**：SPHinXsys = **Apache-2.0**（`LICENSE:1-2` + REST `spdx_id`）。判定：`adapt`（改造并入须保留版权/NOTICE 与 Apache-2.0 声明，注意专利授权条款）**允许**；但本生态首选仍是"读法→干净室重写"——数值格式（可压缩 SPH 的 Riemann 耗散、δ-SPH 扩散、DTW 回归）本身是公开文献，复制代码表达只带来许可审计成本。下文 `adapt` 一律指"**我们自写同构实现**，以 SPHinXsys 为语义参照"；`reference` 指借其算例/协议做比对，不并入代码。

| # | 方案 | 分级 | GAP 锚 | 落地要点 |
|---|---|---|---|---|
| 1 | **可压缩 SPH 的 Riemann 耗散对**（`p_star` / `u_diss` + 随局部 Mach 数增长的 limiter） | adapt | GAP-SPH-WCSPH-VALIDATION | 把 `riemann_solver.h:111`、`:112` 的"随 Mach 限幅"结构搬到 TX 侧密度/动量方程，配 dam-break 无因次设置（`Dambreak.cpp:13-25`）量化耗散-精度折中 |
| 2 | **压缩量密度正则化**（无需 δ-SPH 显式项即可抑制密度漂移） | adapt | GAP-SPH-DELTA-SPH | `density_regularization.cpp:11` 的 `CompressionSummation → 平均压缩 → rho=compression*rho0` 闭环，与经典 δ-SPH 做同算例 A/B，量化"人工扩散 vs 精度"（正是该 GAP 的验证目标） |
| 3 | **DTW 回归基线协议**（多代基线 + 运行时间表 + 距离阈值） | reference | GAP-SPH-ISPH-CONVERGENCE | 移植"多次运行建基线、DTW 距离判过门"到 TX 收敛判定（比较收敛曲线形状）；只借协议不借数据 |
| 4 | **双时间步 + 加速度判据的分层** | adapt | GAP-SPH-WCSPH-VALIDATION | `fluid_time_step_ck.hpp:35`、`:54-55` 的 `dt=CFL*h_min/(|c|+…)` 与加速度界；作者关于"加速度判据属 dual-time-stepping 另一方案"的注释（`fluid_time_step.h:55-63`）是常见误用的说明书 |
| 5 | **输运速度修正（粒子移位）** | adapt | GAP-SPH-WCSPH-VALIDATION | `transport_velocity_correction_ck.hpp` 的 `limiter(h²‖∇W_sum‖²)` 限幅形式，用于自由面撕裂/拉伸稳定性对照 |
| 6 | **FSI 双向耦合 + 基准算例族** | reference | GAP-SPH-FSI-BENCHMARK | `fsi2.cpp`（Zhang/Rezavand/Hu 基准）+ `ForceFromFluid`（`force_on_structure.h:45`）；借几何/材料/观测点协议，误差判定另绑文献或实验值 |
| 7 | **多物理混合物 + 表面张力 + 欧拉可压缩分支** | reference | GAP-SPH-MULTIPHASE | `weakly_compressible_fluid.h:88`、`:109`（MultiSpecies）+ `surface_tension.h` + `muscl_hllc_integration.h`；给 TX 多相线提供"界面力 + 组分密度"接口形状参照 |

**不建议迁移**：SYCL/TBB 执行策略层（`GAP-SPH-GPU-SCALING`）→ 与 TaiChi/JAX/XC 后端不匹配，只作"单节点异构"参照（fit 判定：架构不可直接适配）；投影族 ISPH/PCISPH/DFSPH/PBD（`GAP-SPH-ISPH-CONVERGENCE` 的实现半边）→ 本仓**根本没有**，无迁移源，需另找参照仓（m8638 已登记 SPlisHSPlasH / PBD 系）。

---

## 七、误差判定（逐项 + 显式回答"是否 < 3%"）

**本单是源码级盘点，不含数值运行**，故"关键量 vs 基准误差 < 3%"在数值意义上**不适用（N/A）**——没有跑过任何算例，不做可信误差声明。按纪律给出**可测量的替代校验**（均由 `build_evidence.py` 程序化产出）：

| 校验项 | 方法 | 结果 | 是否达标 |
|---|---|---|---|
| 能力探针一致率 | 17 条探针（存在/不存在）扫 612 源文件 | **17/17 与预期一致**（首轮 2 条不一致 → 定位"decomposition based"假阳性后修正，见 §5 坑 4） | 达标（100%） |
| 报告指针有效性 | 抽出报告全部 `path:line`，逐条查文件+行号并回显原文 | 见 `evidence.json:report_pointers` / `report_pointers_bad` | 达标（bad=0，见提交说明） |
| 元数据与普查对账 | 与 m8638 登记值比对 | **600=600、Apache-2.0=Apache-2.0、C++=C++**（3/3，偏差 0%） | 达标（0%） |
| 算例/基线计数 | `git ls-tree -r` 程序化统计 | 142 算例 / 112 带基线 / 601 XML / 114 DTW / 71 单元测试 | 确定（计数类，无误差概念） |
| **数值精度（vs 解析/实验）** | — | **未测量**：未编译未运行（TBB/SYCL 工具链超出本单边界） | **N/A，不做结论** |

诚实边界：SPHinXsys 验证面"自回归强、绝对精度弱"（§4.3）。若后续要把某算例做成 TX 精度基准（如 dam-break 前锋 vs Martin–Moyce 实验），必须先在本侧跑出数据再对标文献，那时才能回答 <3%。

---

## 八、机器块（供 ingestion）

### 8.1 键值格式（`tools.oss_inventory.machine_blocks` 契约，`--strict` 校验通过）

[OSS-INV]
repo: Xiangyu-Hu/SPHinXsys
lang: C++ CMake, TBB mandatory, SYCL optional default OFF
license: Apache-2.0
methods: WCSPH linear EOS p=p0(rho/rho0-1) with p0=rho0*c0^2; Riemann dissipative solver (DissipativeRiemannSolver, mach-limited limiter); density summation with free-surface correction; CompressionSummation plus DensityRegularization (CK API); TransportVelocityCorrection particle shifting; inter-particle viscous force; Eulerian compressible SPH (MUSCL reconstruction plus HLLC); weakly-compressible mixture and multi-species; surface tension (CSF); solids linear and nonlinear elastic, inelastic, Johnson-Cook, HJC, thin shells; granular contact; diffusion-reaction. ABSENT: ISPH, PCISPH, IISPH, DFSPH, PBD, explicit delta-SPH term, MPI
multiphase: WeaklyCompressibleMixture with local rho0 field, WeaklyCompressibleMultiSpecies species density table, SurfaceTension, two-phase dambreak (HEAD PR 1129)
boundary: wall contact with virtual wall pressure p_j_in_wall and wall external acceleration correction; NearWallDistance, DistanceFromWall, BoundingFromWall; EmitterInflowCondition, EmitterInflowInjection, DisposerOutflowDeletion; FlowVelocityBuffer; FreeStreamVelocityCorrection; DampingBoundaryCondition; NonReflectiveBoundaryCorrection; templated kernel-gradient correction
gpu: TBB shared-memory parallel (mandatory); optional SYCL device offload via ExecutionPolicy template (src_sycl, 30 SYCL examples, needs IntelLLVM icpx); NO MPI and no distributed memory
summary: Industrial multi-physics SPH C++ API library (TUM, DFG HU1527). Unified particle framework for fluid, solid, multibody, granular and diffusion-reaction; WCSPH with Riemann dissipation, density summation and compression-based regularization; two-way FSI and multiphase mixtures; shared-memory TBB plus optional SYCL, no MPI; validation surface is 601 regression XML plus 114 DTW baselines (self-consistent, no absolute-accuracy assertions).
[/OSS-INV]

[OSS-SOL]
repo: Xiangyu-Hu/SPHinXsys
slug: GAP-SPH-WCSPH-VALIDATION
tag: mach-limited-riemann-dissipation
method: Weakly compressible SPH obtains artificial dissipation from an acoustic Riemann solver: interface pressure p_star = p_ave + 0.5*rho0*c0*u_jump*limited_mach and density dissipative flux from u_diss = 0.5*(p_i-p_j)/(rho0*c0)*limited_mach^2, with a limiter that grows with local Mach number so dissipation vanishes as compression goes to zero.
files: src/shared/materials/riemann_solver.h:93-124, src/shared/materials/riemann_solver.h:111, src/shared/particle_dynamics/fluid_dynamics/fluid_integration.hpp:76, src/shared/particle_dynamics/fluid_dynamics/fluid_integration.hpp:192, src/shared/shared_ck/particle_dynamics/fluid_dynamics/acoustic_step_1st_half.hpp:107
conventions: p_star interface pressure; u_diss density equation dissipative flux; limiter_coeff limiter coefficient default 3.0; dW_ijV_j kernel gradient times volume; rho_dissipation drho_dt dissipative term
transfer: adapt
evidence: artifacts/oss_sph_w2/evidence.json probe WCSPH Riemann dissipation present true first hit riemann_solver.h:111; license Apache-2.0 permits adaptation while this ecosystem prefers clean-room reimplementation
[/OSS-SOL]

[OSS-SOL]
repo: Xiangyu-Hu/SPHinXsys
slug: GAP-SPH-DELTA-SPH
tag: compression-based-density-regularization
method: No explicit delta-SPH diffusion term exists (no DeltaSPH hit in src). Density drift is instead controlled by a compression closed loop: CompressionSummation accumulates volume over rho deviation, AverageCompression reduces it to a mean compression and stores its inverse, DensityRegularization resets rho to compression times reference density, so density is regularized without interfacial diffusion.
files: src/shared/shared_ck/particle_dynamics/fluid_dynamics/density_regularization.cpp:11, src/shared/shared_ck/particle_dynamics/fluid_dynamics/density_regularization.hpp:100-136, src/shared/particle_dynamics/fluid_dynamics/density_summation.hpp:31
conventions: CompressionSummation regularization pseudo variable; InverseReferenceCompression inverse mean compression; sigma0 reference lattice number density; rho_sum kernel summation density
transfer: adapt
evidence: artifacts/oss_sph_w2/evidence.json probe explicit delta-SPH present false verified and probe compression regularization present true; usable as the artificial-diffusion alternative for the GAP-SPH-DELTA-SPH A/B study
[/OSS-SOL]

[OSS-SOL]
repo: Xiangyu-Hu/SPHinXsys
slug: GAP-SPH-ISPH-CONVERGENCE
tag: dtw-regression-baseline-protocol
method: Consistency is judged not by single-run tolerance but by multi-generation baselines with dynamic-time-warping distance thresholds: each observed quantity writes Run_N_result.xml, the paired dtwdistance.xml stores the accepted DTW distance, and runtimes.dat stores per-generation runtimes. The C++ side instantiates RegressionTestDynamicTimeWarping directly in the case file, making the protocol reusable for convergence-curve shape comparison.
files: src/shared/regression_test/dynamic_time_warping_method.hpp:1-120, tests/2d_examples/test_2d_dambreak/Dambreak.cpp:118, tests/2d_examples/test_2d_dambreak/regression_test_tool/regression_test_tool.py
conventions: dtwdistance.xml accepted DTW distance threshold; Run_N_result.xml observation series of generation N; runtimes.dat runtime per generation used to select comparable baselines
transfer: reference
evidence: artifacts/oss_sph_w2/evidence.json counts regression_xml 601, regression_dtw_baselines 114, regression_runtime_tables 145; protocol only, the data are the repository own historical runs
[/OSS-SOL]

[OSS-SOL]
repo: Xiangyu-Hu/SPHinXsys
slug: GAP-SPH-WCSPH-VALIDATION
tag: transport-velocity-correction
method: Transport velocity correction drives particle shifting by the kernel gradient integral residual: dpos += coefficient*h_ref^2*limiter(h_ref^2*residual_squared_norm)*residual/h_ratio^2, with the limiter suppressing shifting near free surfaces, which stabilizes tensile regions in dam-break like flows.
files: src/shared/shared_ck/particle_dynamics/fluid_dynamics/transport_velocity_correction_ck.hpp:10-53, src/shared/particle_dynamics/fluid_dynamics/transport_velocity_correction.hpp:1-60
conventions: coefficient shifting scaling; h_ref reference smoothing length; limiter residual limiter; h_ratio local refinement ratio; KernelGradientIntegral discreteness residual
transfer: adapt
evidence: artifacts/oss_sph_w2/evidence.json probe transport velocity correction present true; Apache-2.0 allows adaptation, clean-room reimplementation preferred
[/OSS-SOL]

[OSS-SOL]
repo: Xiangyu-Hu/SPHinXsys
slug: GAP-SPH-FSI-BENCHMARK
tag: fsi2-benchmark-and-force-from-fluid
method: Two-way FSI is assembled from a force transfer dynamics (ForceFromFluid: fluid contact traction integrated onto solid surface particles) plus a structure surface motion dynamics, and is exercised by a named benchmark case fsi2 whose header cites Zhang Rezavand Hu; hydrostatic and nonlinear-wave FSI plus a 3D dambreak on an elastic plate complete the family.
files: src/shared/shared_ck/particle_dynamics/fluid_structure_interaction/force_on_structure.h:45, src/shared/shared_ck/particle_dynamics/fluid_structure_interaction/structure_surface_motion.h:1-60, tests/2d_examples/test_2d_fsi2/fsi2.cpp:41, tests/2d_examples/test_2d_fsi2/fsi2.cpp:5
conventions: ForceFromFluid fluid-to-solid traction; SaintVenantKirchhoffSolid insert material; beam_observer observation body; wall_boundary plus insert_body contact set
transfer: reference
evidence: artifacts/oss_sph_w2/evidence.json probe FSI coupling present true; case protocol only, no embedded error table in repo
[/OSS-SOL]

[OSS-SOL]
repo: Xiangyu-Hu/SPHinXsys
slug: GAP-SPH-MULTIPHASE
tag: mixture-material-and-surface-tension
method: Multiphase support is built from materials rather than a separate solver: WeaklyCompressibleMixture carries a per-particle reference density field and WeaklyCompressibleMultiSpecies carries a species density table with mass fraction summation, while interface force comes from a CSF surface tension dynamics and a density gradient dynamics; the HEAD commit adds a two-phase dam-break case.
files: src/shared/materials/weakly_compressible_fluid.h:72, src/shared/materials/weakly_compressible_fluid.h:109, src/shared/particle_dynamics/fluid_dynamics/surface_tension.h:1-60, src/shared/shared_ck/particle_dynamics/fluid_dynamics/fluid_mixture/fluid_mixture_dynamics.h:1-80
conventions: dv_rho0 local reference density field; rho0_list species reference densities; Y_list mass fractions; density gradient drives surface tension force
transfer: reference
evidence: artifacts/oss_sph_w2/evidence.json probe multiphase mixture present true and surface tension present true; HEAD 1612330 adds two-phase dambreak
[/OSS-SOL]

[OSS-SOL]
repo: Xiangyu-Hu/SPHinXsys
slug: GAP-SPH-GPU-SCALING
tag: sycl-execution-policy-not-transferable
method: Parallelism is TBB for shared memory plus an optional SYCL backend selected by a CMake option; device kernels are generated from the same templates via an ExecutionPolicy argument, giving one source for host and device. There is no MPI and no distributed-memory layer, so scaling evidence is limited to threads and a single device.
files: CMakeLists.txt:16, CMakeLists.txt:64, src/shared/shared_ck/particle_dynamics/execution/execution_policy.h:1-60, .github/workflows/ci.yml:54
conventions: SPHINXSYS_USE_SYCL device offload switch; ExecutionPolicy host or device tag; SPHINXSYSSYCL_TARGETS compilation targets
transfer: none
evidence: artifacts/oss_sph_w2/evidence.json probe MPI present false verified, probe SYCL present true; judged not transferable to this ecosystem backends, kept as single-node heterogeneous reference
[/OSS-SOL]

### 8.2 JSON 单行格式（本 campaign 约定的上报形态，与 8.1 同内容）

[OSS-INV]{"campaign":"osssph-w2","repo":"Xiangyu-Hu/SPHinXsys","tier":1,"methods":["WCSPH-linear-EOS","Riemann-dissipative-solver","density-summation-free-surface","CompressionSummation-DensityRegularization","TransportVelocityCorrection","viscous-force","Eulerian-MUSCL-HLLC","WeaklyCompressibleMixture-MultiSpecies","surface-tension-CSF","elastic-inelastic-JC-HJC-shells","granular-contact","diffusion-reaction"],"absent":["ISPH","PCISPH","IISPH","DFSPH","PBD","explicit-delta-SPH","MPI"],"boundary":"wall virtual pressure p_j_in_wall + wall external acceleration correction; NearWallDistance/DistanceFromWall/BoundingFromWall; EmitterInflow/DisposerOutflow; FlowVelocityBuffer; FreeStreamVelocityCorrection; DampingBoundary; NonReflectiveBoundaryCorrection","gpu":"TBB shared-memory mandatory; optional SYCL device offload (ExecutionPolicy templates, 30 SYCL examples, IntelLLVM icpx); NO MPI","validation":"142 example dirs (112 CPU + 30 SYCL), 112 with regression baselines, 601 regression XML, 114 DTW baselines, 145 runtime tables, 71 unit-test files, CI 8 jobs with up-to-10 retries; self-consistency strong, absolute-accuracy assertions absent","license":"Apache-2.0","transferable":["SPHSOL-sphinxsys-01","SPHSOL-sphinxsys-02","SPHSOL-sphinxsys-03","SPHSOL-sphinxsys-04","SPHSOL-sphinxsys-05","SPHSOL-sphinxsys-06","SPHSOL-sphinxsys-07"]}[/OSS-INV]

[OSS-SOL]{"repo":"Xiangyu-Hu/SPHinXsys","id":"SPHSOL-sphinxsys-01","kind":"adapt","summary":"Mach-limited Riemann dissipation pair (p_star with 0.5*rho0c0*u_jump*limited_mach, u_diss with limited_mach^2) for weakly compressible SPH density and momentum equations","gap":"GAP-SPH-WCSPH-VALIDATION","files":"src/shared/materials/riemann_solver.h:111-112","license_ok":true}[/OSS-SOL]

[OSS-SOL]{"repo":"Xiangyu-Hu/SPHinXsys","id":"SPHSOL-sphinxsys-02","kind":"adapt","summary":"Compression summation plus density regularization closed loop replacing an explicit delta-SPH diffusion term, enabling an artificial-diffusion-versus-accuracy A/B study","gap":"GAP-SPH-DELTA-SPH","files":"src/shared/shared_ck/particle_dynamics/fluid_dynamics/density_regularization.cpp:11","license_ok":true}[/OSS-SOL]

[OSS-SOL]{"repo":"Xiangyu-Hu/SPHinXsys","id":"SPHSOL-sphinxsys-03","kind":"reference","summary":"DTW multi-generation regression baseline protocol (Run_N_result.xml plus dtwdistance.xml threshold plus runtimes.dat) for convergence-curve shape consistency gating","gap":"GAP-SPH-ISPH-CONVERGENCE","files":"src/shared/regression_test/dynamic_time_warping_method.hpp:1-120","license_ok":true}[/OSS-SOL]

[OSS-SOL]{"repo":"Xiangyu-Hu/SPHinXsys","id":"SPHSOL-sphinxsys-04","kind":"adapt","summary":"Transport velocity correction with limiter on kernel gradient integral residual for free-surface tensile stability","gap":"GAP-SPH-WCSPH-VALIDATION","files":"src/shared/shared_ck/particle_dynamics/fluid_dynamics/transport_velocity_correction_ck.hpp:10-53","license_ok":true}[/OSS-SOL]

[OSS-SOL]{"repo":"Xiangyu-Hu/SPHinXsys","id":"SPHSOL-sphinxsys-05","kind":"reference","summary":"Two-way FSI assembly (ForceFromFluid plus structure surface motion) and the fsi2 benchmark case protocol for reproducing a literature FSI benchmark","gap":"GAP-SPH-FSI-BENCHMARK","files":"tests/2d_examples/test_2d_fsi2/fsi2.cpp:41","license_ok":true}[/OSS-SOL]

[OSS-SOL]{"repo":"Xiangyu-Hu/SPHinXsys","id":"SPHSOL-sphinxsys-06","kind":"reference","summary":"Multiphase via mixture materials (local rho0 field, species density table) plus CSF surface tension and density gradient interface force","gap":"GAP-SPH-MULTIPHASE","files":"src/shared/materials/weakly_compressible_fluid.h:72","license_ok":true}[/OSS-SOL]

[OSS-SOL]{"repo":"Xiangyu-Hu/SPHinXsys","id":"SPHSOL-sphinxsys-07","kind":"reference","summary":"SYCL ExecutionPolicy heterogeneous single-node execution layer; judged not transferable to this ecosystem backends, kept as scaling reference only","gap":"GAP-SPH-GPU-SCALING","files":"CMakeLists.txt:16","license_ok":true}[/OSS-SOL]