# Phantom SPH 深盘报告（OSS-SPH Wave-2·集团军）

> **scenario_key**: oss-sph-phantom-w2  
> **mission_id**: 8655  
> **盘点日期**: 2026-11-09  
> **盘点人**: agent/m8655  
> **目标仓**: danieljprice/phantom (tier1)  
> **产物**: `artifacts/oss-sph/phantom_inventory.json`（机器可解析）  
> **方法**: GitHub API (`/repos/` + `/git/trees/main?recursive=1` + `/git/blobs/`) 实查；禁凭记忆

---

## 一、案例介绍（物理背景 / 仓库概况）

### 1.1 物理背景

Phantom 是一款面向天体物理的 3D 光滑粒子流体动力学（SPH）与磁流体动力学（MHD）生产级代码，由 Daniel Price（Monash University）主导开发，2007 年起始，2018 年发表于 PASA（Price et al. 2018, PASA, 35, 31）。定位是**生产运行代码**而非算法测试平台，核心应用场景包括：

- 恒星形成（分子云坍缩、吸积盘、原恒星喷流）
- 行星系统形成（原行星盘、行星-盘相互作用）
- 双星/多星系统（质量转移、公共包层、引力波源）
- 星际介质（超新星遗迹、湍流、尘埃-气体耦合）
- 广义相对论流体动力学（Kerr/Schwarzschild 度规）

### 1.2 仓库元数据（GitHub API 实查）

| 字段 | 值 |
|---|---|
| **full_name** | danieljprice/phantom |
| **description** | Phantom Smoothed Particle Hydrodynamics and Magnetohydrodynamics code |
| **language** | Fortran |
| **license** | GPL-3.0（源码头声明 GPLv3 + 第 7c 条补充条件：重命名须保留 "Phantom" 字样） |
| **stargazers_count** | 167 |
| **forks_count** | 355 |
| **pushed_at** | 2026-11-09T14:03:32Z |
| **created_at** | 2020-05-31T23:30:43Z |
| **default_branch** | main |
| **size** | 63,253 KB (~62 MB) |
| **open_issues_count** | 67 |
| **topics** | astronomy, astrophysical-simulation, astrophysics, dust-gas, fluid-dynamics, fluid-simulation-engine, fluid-solver, fortran, gas-dynamics, general-relativity, hydrodynamic-modeling, magnetohydrodynamics, particles, smoothed-particle-hydrodynamics, smoothed-particle-magnetohydrodynamics |
| **archived** | false |

### 1.3 代码规模与语言分布

| 维度 | 数据 |
|---|---|
| **总文件数** | 881（blob）+ 57（tree） |
| **Fortran (.f90)** | 466 文件 |
| **Fortran (.F90)** | 41 文件（预处理） |
| **文档 (.rst)** | 194 文件（Sphinx/ReadTheDocs） |
| **Python (.py)** | 27 文件（分析脚本 + pyphantom 库） |
| **Shell (.sh)** | 24 文件 |
| **YAML (.yml)** | 14 文件（CI/CD） |
| **源码目录** | src/main (195), src/setup (64+), src/tests (45), src/utils, src/lib/NICIL |

---

## 二、源码级方法清单

### 2.1 SPH 变体与方法族

| 方法/能力 | 状态 | 定位（文件:行） | 说明 |
|---|---|---|---|
| **WCSPH（标准 SPH）** | ✅ REAL | `src/main/force.F90` (~148KB), `src/main/dens.F90` (~75KB) | 核心密度-力循环；标准 SPH 公式 |
| **δ-SPH** | ⚠️ INSUFFICIENT | 未在文件名中显式出现；可能在 `shock_capturing.f90` 或 `viscosity.f90` 中实现 | 需读源码确认 |
| **ISPH/PCISPH/DFSPH/IISPH** | ❌ NOT_SUPPORTED | — | Phantom 为可压缩 SPH 代码，不支持不可压投影法 |
| **PBD** | ❌ NOT_SUPPORTED | — | 天体物理 SPH 代码，无 PBD |
| **MHD（SPMHD）** | ✅ REAL | `src/main/force.F90`, `src/main/divb_projection.f90` | 光滑粒子磁流体动力学；divB 投影清洗 |
| **尘埃-气体多相** | ✅ REAL | `src/main/dust.f90`, `src/main/dust_formation.f90` | 多组分尘埃-气体混合物（dust-as-mixture / dust-as-particles） |
| **辐射流体** | ✅ REAL | `src/main/radiation_implicit.f90` | 隐式通量限制扩散 + Monte Carlo 辐射转移 |
| **广义相对论 SPH** | ✅ REAL | `src/main/extern_gr.f90`, `src/main/metric_*.f90` | Kerr/Schwarzschild/Minkowski 度规 |
| **自引力** | ✅ REAL | `src/main/ptmass.F90`, `src/main/ptmass_tree.f90` | 动量/角动量守恒树代码 + 自适应引力软化 |
| **自适应粒子细化 (APR)** | ✅ REAL | `src/main/apr.f90`, `src/main/apr_region.f90` | 粒子分裂/合并细化 |

### 2.2 核函数（Kernel）

| 核函数 | 文件 | 说明 |
|---|---|---|
| Cubic spline | `src/main/kernel_cubic.f90` | 标准三次样条核 |
| Quartic | `src/main/kernel_quartic.f90` | 四次核 |
| Quintic | `src/main/kernel_quintic.f90` | 五次核 |
| Wendland C2 | `src/main/kernel_WendlandC2.f90` | Wendland C2 紧支核 |
| Wendland C4 | `src/main/kernel_WendlandC4.f90` | Wendland C4 紧支核 |
| Wendland C6 | `src/main/kernel_WendlandC6.f90` | Wendland C6 紧支核 |

### 2.3 边界处理

| 能力 | 文件 | 说明 |
|---|---|---|
| **边界条件** | `src/main/boundary.f90` (6,295 B) | 标准 SPH 边界处理 |
| **动态边界** | `src/main/boundary_dynamic.f90` | 动态边界粒子 |
| **注入边界** | `src/main/inject_*.f90` (14 文件) | Bondi 注入、盘注入、风注入、双星注入等 |

> **注意**：Phantom 为天体物理代码，**无工程 SPH 的刚性壁面/SA 边界/动态边界粒子**等海岸工程常用边界。边界主要通过注入（inject_*）和外部势（extern_*）实现。

### 2.4 时间步控制

| 能力 | 文件 | 说明 |
|---|---|---|
| **时间步计算** | `src/main/timestep.f90` (7,129 B) | CFL + 力/加速度限制 |
| **独立时间步** | `src/main/utils_indtimesteps.f90` | 粒子级独立时间步 |
| **Leapfrog 积分** | `src/main/step_leapfrog.f90` (35,495 B) | 标准 Leapfrog 时间积分 |
| **子步/子群** | `src/main/substepping.F90` (68,187 B), `src/main/subgroup.f90` | 子步积分 + 子群（sink 粒子高阶积分） |
| **演化循环** | `src/main/evolve.f90` (17,308 B) | 主演化循环 |

### 2.5 GPU/MPI 并行策略

| 能力 | 状态 | 文件 | 说明 |
|---|---|---|---|
| **MPI 并行** | ✅ REAL | `src/main/mpi_*.F90/f90` (14 文件) | 域分解 + 树并行 + 力/密度 MPI |
| **OpenMP** | ✅ REAL | `src/main/utils_omp.f90` | OpenMP 共享内存并行 |
| **CUDA/GPU** | ❌ NOT_SUPPORTED | — | 无 GPU 加速 |
| **KD-Tree 邻居搜索** | ✅ REAL | `src/main/kdtree.F90`, `src/main/neigh_kdtree.F90` | O(N log N) 邻居搜索 |
| **负载均衡** | ✅ REAL | `src/main/mpi_balance.F90` | MPI 域负载均衡 |

### 2.6 状态方程（EOS）

| EOS | 文件 |
|---|---|
| 理想气体 | `src/main/eos.f90` |
| 多方 | `src/main/eos.f90` |
| 条形 | `src/main/eos_barotropic.f90` |
| Helmholtz | `src/main/eos_helmholtz.f90` |
| MESA | `src/main/eos_mesa.f90` |
| Shen（中子星） | `src/main/eos_shen.f90` |
| Tillotson（撞击） | `src/main/eos_tillotson.f90` |
| 分段 | `src/main/eos_piecewise.f90` |
| 零温 | `src/main/eos_zerotemp.f90` |
| 气体+辐射 | `src/main/eos_gasradrec.f90`, `src/main/eos_idealplusrad.f90` |

### 2.7 激波捕捉/人工粘度

| 能力 | 文件 | 说明 |
|---|---|---|
| **人工粘度** | `src/main/viscosity.f90` (6,147 B) | Monaghan 型人工粘度 |
| **激波捕捉** | `src/main/shock_capturing.f90` (7,148 B) | 高级激波捕捉方法 |

---

## 三、验证面盘点

### 3.1 自带算例/基准（src/setup/）

| 算例 | 文件 | 物理 |
|---|---|---|
| **Sedov 爆炸波** | `src/setup/setup_sedov.f90` (6,720 B) | 点源爆炸，对标自相似解 |
| **Kelvin-Helmholtz** | `src/setup/setup_kh.f90` (7,294 B) | 剪切层不稳定性 |
| **Taylor-Green 涡** | `src/setup/setup_taylorgreen.f90` (2,454 B) | 涡衰减基准 |
| **Blob 测试** | `src/setup/setup_blob.f90` (4,739 B) | 云-风相互作用 |
| **激波管** | `src/setup/setup_shock.f90` (32,574 B) | Sod/Riemann 问题 |
| **MHD 转子** | `src/setup/setup_mhdrotor.f90` (4,491 B) | MHD 转子问题 |
| **MHD 波** | `src/setup/setup_mhdwave.f90` (3,192 B) | Alfvén/快/慢波 |
| **MHD 涡旋** | `src/setup/setup_mhdvortex.f90` (3,352 B) | MHD 涡旋 |
| **MHD 爆炸** | `src/setup/setup_mhdblast.f90` (4,486 B) | MHD 爆炸波 |
| **尘埃箱** | `src/setup/setup_dustybox.f90` (10,781 B) | 尘埃-气体耦合解析解 |
| **尘埃沉降** | `src/setup/setup_dustsettle.f90` (13,737 B) | 尘埃沉降 |
| **波传播** | `src/setup/setup_wave.f90` (11,275 B) | 线性波传播 |
| **均匀分布** | `src/setup/setup_unifdis.f90` (10,422 B) | 粒子玻璃化/均匀化 |
| **吸积盘** | `src/setup/setup_disc.f90` (167,466 B) | Keplerian 盘 |
| **双星** | `src/setup/setup_binary.f90` (10,495 B) | 双星系统 |
| **湍流** | `src/setup/setup_turb.f90` (8,850 B) | 湍流驱动 |

### 3.2 测试框架（src/tests/）

| 测试 | 文件 | 覆盖 |
|---|---|---|
| **Phantom 测试套件** | `src/tests/phantomtest.f90`, `src/tests/testsuite.f90` | 主测试驱动 |
| **核函数测试** | `src/tests/test_kernel.f90` | 核函数归一化/导数 |
| **Sedov 测试** | `src/tests/test_sedov.f90` | Sedov 自相似解对标 |
| **尘埃测试** | `src/tests/test_dust.f90` | 尘埃-气体耦合 |
| **重力测试** | `src/tests/test_gravity.f90` | 自引力/树代码 |
| **MPI 测试** | `src/tests/test_mpi.f90` | MPI 并行正确性 |
| **EOS 测试** | `src/tests/test_eos.f90` | 状态方程 |
| **导数测试** | `src/tests/test_derivs.f90` | SPH 导数精度 |
| **邻居测试** | `src/tests/test_neigh.f90` | KD-Tree 邻居搜索 |
| **时间步测试** | `src/tests/test_indtstep.f90` | 独立时间步 |
| **直接求和** | `src/tests/directsum.f90` | O(N²) 直接求和验证 |

### 3.3 CI/CD 流水线

| 工作流 | 文件 | 说明 |
|---|---|---|
| **test.yml** | `.github/workflows/test.yml` | 4 编译器 × 2 调试模式 × 10 测试输入 = 80 矩阵 |
| **build.yml** | `.github/workflows/build.yml` | 编译验证 |
| **mpi.yml** | `.github/workflows/mpi.yml` | MPI 并行测试 |
| **on_push.yml** | `.github/workflows/on_push.yml` | Push 触发 |
| **on_pr.yml** | `.github/workflows/on_pr.yml` | PR 触发 |

**测试矩阵**（test.yml 实查）：
- 编译器：gfortran, ifort, ifx, nvfortran
- 调试：yes/no
- 测试输入：test, testkd, testdust, testgr, testgrav, testgrowth, testnimhd, test2, testcyl, testapr

### 3.4 量化验证数据

| 数据类型 | 位置 | 说明 |
|---|---|---|
| **EOS 数据表** | `data/eos/helmholtz/`, `data/eos/mesa/`, `data/eos/shen/` | 真实物理 EOS 表 |
| **冷却数据** | `data/cooling/` | 辐射冷却表 |
| **Krome 化学反应** | `data/Krome/` | 化学网络 |
| **双星黑洞** | `data/binarybh/` | 双星黑洞初始条件 |

> **注意**：Phantom 的验证主要依赖**解析解对标**（Sedov 自相似解、尘埃箱解析解、线性波色散关系）和**文献对比**，而非实验数据。这是天体物理 SPH 的典型特征。

---

## 四、对 TensorPhysics SPH 线的可迁移方案

### 4.1 License 合规判定

| 项目 | 判定 |
|---|---|
| **Phantom License** | **GPL-3.0**（含第 7c 条补充条件：重命名须保留 "Phantom" 字样） |
| **对 TensorSPH 的影响** | ⚠️ **reference-only**：GPL-3.0 具有传染性，直接适配/移植需开源衍生作品。建议仅作为**参考实现**（reference），不直接复制代码。 |
| **NICIL 子库** | `src/lib/NICIL/` 有独立 LICENSE 文件，需单独确认 |

### 4.2 可迁移方案（≥3 条）

#### 方案 1：SPH 核函数与导数验证面 → reference

| 字段 | 值 |
|---|---|
| **id** | SPHSOL-phantom-01 |
| **kind** | reference |
| **summary** | Phantom 提供 6 种核函数（cubic/quartic/quintic/Wendland C2/C4/C6）的完整实现与测试（`test_kernel.f90`），可作为 TensorSPH 核函数验证面的参考基准。核函数归一化、导数精度、紧支半径等均可对标。 |
| **gap** | GAP-SPH-WCSPH-VALIDATION |
| **license_ok** | ✅ reference 用途合规（仅参考算法，不复制代码） |
| **定位** | `src/main/kernel_*.f90` (6 files), `src/tests/test_kernel.f90` |

#### 方案 2：Sedov 爆炸波基准 → reference

| 字段 | 值 |
|---|---|
| **id** | SPHSOL-phantom-02 |
| **kind** | reference |
| **summary** | Phantom 的 Sedov 测试（`setup_sedov.f90` + `test_sedov.f90`）提供标准点源爆炸问题的 SPH 实现，对标自相似解析解。可作为 TensorSPH 激波捕捉能力的验证基准。 |
| **gap** | GAP-SPH-ASTRO-SEDOV |
| **license_ok** | ✅ reference 用途合规 |
| **定位** | `src/setup/setup_sedov.f90` (6,720 B), `src/tests/test_sedov.f90` |

#### 方案 3：尘埃-气体多相耦合 → reference

| 字段 | 值 |
|---|---|
| **id** | SPHSOL-phantom-03 |
| **kind** | reference |
| **summary** | Phantom 的多组分尘埃-气体混合物实现（`dust.f90`, `setup_dustybox.f90`）提供 dust-as-mixture 和 dust-as-particles 两种方法，且有解析解对标（尘埃箱测试）。可作为 TensorSPH 多相 SPH 能力的参考。 |
| **gap** | GAP-SPH-MULTIPHASE |
| **license_ok** | ✅ reference 用途合规 |
| **定位** | `src/main/dust.f90`, `src/setup/setup_dustybox.f90` (10,781 B), `src/tests/test_dust.f90` |

#### 方案 4：MPI 域分解与 KD-Tree 邻居搜索 → adapt

| 字段 | 值 |
|---|---|
| **id** | SPHSOL-phantom-04 |
| **kind** | adapt |
| **summary** | Phantom 的 MPI 域分解（`mpi_domain.F90`, `mpi_balance.F90`）+ KD-Tree 邻居搜索（`kdtree.F90`, `neigh_kdtree.F90`）架构可适配到 TensorSPH 的并行后端。算法思想（域分解 + 树搜索 + 负载均衡）可移植，但需重写为 C++/Python。 |
| **gap** | GAP-SPH-GPU-SCALING |
| **license_ok** | ⚠️ 需重写实现（GPL 不保护算法思想，仅保护代码表达） |
| **定位** | `src/main/mpi_*.F90/f90` (14 files), `src/main/kdtree.F90`, `src/main/neigh_kdtree.F90` |

#### 方案 5：时间步控制与 Leapfrog 积分 → fit

| 字段 | 值 |
|---|---|
| **id** | SPHSOL-phantom-05 |
| **kind** | fit |
| **summary** | Phantom 的时间步控制（`timestep.f90`: CFL + 力限制）+ Leapfrog 积分（`step_leapfrog.f90`）+ 子步/子群（`substepping.F90`）架构与 TensorSPH 的时间积分需求高度匹配。可作为 fit 级参考，直接映射到 TensorSPH 的时间步进模块。 |
| **gap** | GAP-SPH-WCSPH-VALIDATION |
| **license_ok** | ✅ reference/fit 用途合规（算法思想可移植） |
| **定位** | `src/main/timestep.f90` (7,129 B), `src/main/step_leapfrog.f90` (35,495 B), `src/main/substepping.F90` (68,187 B) |

---

## 五、注意点（踩坑 / 局限 / 适用范围）

### 5.1 代码定位局限

1. **天体物理专用**：Phantom 是天体物理 SPH 代码，**不支持工程 SPH 场景**（溃坝、波浪、FSI、刚性壁面）。边界处理通过注入（inject_*）和外部势（extern_*）实现，而非 SA 边界/动态边界粒子。
2. **可压缩 SPH**：Phantom 仅支持可压缩 SPH（WCSPH），**不支持 ISPH/PCISPH/DFSPH/IISPH** 等不可压投影法。
3. **无 GPU 加速**：仅支持 MPI + OpenMP，无 CUDA/GPU 实现。

### 5.2 License 风险

- **GPL-3.0 传染性**：直接复制/修改 Phantom 代码需开源衍生作品。
- **第 7c 条补充条件**：重命名须保留 "Phantom" 字样，限制 fork 独立性。
- **建议**：仅作为 reference（参考算法思想），不直接复制代码。

### 5.3 验证面局限

- **无实验数据对标**：Phantom 的验证主要依赖解析解和文献对比，无实验数据。
- **无自由面/多相工业基准**：无 dam-break、Poiseuille 等工程 SPH 基准。
- **天体物理基准为主**：Sedov、KH、尘埃箱、MHD 转子等。

### 5.4 数据获取限制

- **GitHub API 速率限制**：未认证请求 60 req/hour，部分 blob 内容未能获取。
- **大文件未下载**：force.F90 (148KB)、dens.F90 (75KB)、substepping.F90 (68KB) 等核心文件仅通过树 API 获取元数据，未读取完整内容。
- **行号定位**：部分定位基于文件名推断，精确行号需 clone 后 grep 确认。

---

## 六、误差判定

本单为源码级普查（非精度验证），无计算结果需对标基准。误差判定不适用。

**诚实声明**：
- ✅ 完成：GitHub API 元数据 + 目录树全量扫描 + 方法清单 + 验证面盘点 + 5 条可迁移方案 + 机器块
- ⚠️ 未完成：核心源文件（force.F90/dens.F90/substepping.F90）完整内容未读取（API 速率限制）；精确行号定位未确认
- ⚠️ 未覆盖：NICIL 子库 license 未单独确认；δ-SPH 实现状态未确认（需读源码）

---

## 七、机器块

[OSS-INV]{"campaign":"osssph-w2","repo":"danieljprice/phantom","tier":1,"methods":["WCSPH","SPMHD","dust-gas multiphase","radiation hydro","GR hydro","APR","self-gravity tree"],"boundary":"inject_* + extern_* (no engineering walls)","gpu":"none (MPI+OpenMP only)","validation":"sedov/kh/taylorgreen/blob/dustybox/mhd rotor (analytic solutions)","license":"GPL-3.0 + 7c name clause","transferable":["SPHSOL-phantom-01:kernel reference","SPHSOL-phantom-02:sedov reference","SPHSOL-phantom-03:multiphase reference","SPHSOL-phantom-04:MPI/KD-tree adapt","SPHSOL-phantom-05:timestepping fit"]}[/OSS-INV]

[OSS-SOL]{"repo":"danieljprice/phantom","id":"SPHSOL-phantom-01","kind":"reference","summary":"6 kernel functions (cubic/quartic/quintic/Wendland C2/C4/C6) with test_kernel.f90 validation; reference for TensorSPH kernel verification","gap":"GAP-SPH-WCSPH-VALIDATION"}[/OSS-SOL]

[OSS-SOL]{"repo":"danieljprice/phantom","id":"SPHSOL-phantom-02","kind":"reference","summary":"Sedov blast wave test (setup_sedov.f90 + test_sedov.f90) with self-similar analytic solution; reference for shock capturing validation","gap":"GAP-SPH-ASTRO-SEDOV"}[/OSS-SOL]

[OSS-SOL]{"repo":"danieljprice/phantom","id":"SPHSOL-phantom-03","kind":"reference","summary":"Dust-gas multiphase (dust.f90 + setup_dustybox.f90) with analytic solution benchmark; reference for multiphase SPH","gap":"GAP-SPH-MULTIPHASE"}[/OSS-SOL]

[OSS-SOL]{"repo":"danieljprice/phantom","id":"SPHSOL-phantom-04","kind":"adapt","summary":"MPI domain decomposition + KD-tree neighbor search architecture adaptable to TensorSPH parallel backend (requires rewrite)","gap":"GAP-SPH-GPU-SCALING"}[/OSS-SOL]

[OSS-SOL]{"repo":"danieljprice/phantom","id":"SPHSOL-phantom-05","kind":"fit","summary":"Timestep control (CFL+force limit) + Leapfrog integration + substepping architecture fits TensorSPH time integration needs","gap":"GAP-SPH-WCSPH-VALIDATION"}[/OSS-SOL]

---

**产物清单**：
- `docs/oss_inventory_sph/REPORT_phantom_deep.md` — 本报告
- `artifacts/oss-sph/phantom_inventory.json` — 机器可解析清单（见下方）
