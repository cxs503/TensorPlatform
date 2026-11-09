# OpenSPH 深度盘点报告（OSS-SPH 深盘波2）

> 任务 ID: m8653 | 战役: ossph-w2 | 目标仓: pavelsevecek/OpenSPH | tier: 1
> 调查日期: 2026-11-09 | 调查方法: GitHub API + 源码级文件阅读

---

## 1. GitHub API 实查事实

| 字段 | 值 |
|---|---|
| full_name | pavelsevecek/OpenSPH |
| language | C++ (C++14) |
| license | **MIT** (SPDX: MIT) |
| stars | 254 |
| forks | 20 |
| open_issues | 1 |
| size | ~48 MB |
| created_at | 2020-06-15 |
| updated_at | 2026-11-01 |
| pushed_at | 2026-10-09 |
| default_branch | master |
| description | "C++ SPH/N-body library and standalone GUI application, mainly designed for astrophysical simulations" |
| topics | astrophysics, impact, nbody, simulation, sph |
| 主开发方 | Charles University (Astronomical Institute), Prague |
| 主作者 | Pavel Ševeček (sevecek@sirrah.troja.mff.cuni.cz) |
| 构建系统 | CMake 3.12+ / qmake (.pro 文件) |
| 依赖 | wxWidgets (GUI), TBB (可选并行), Eigen (可选), OpenVDB (可选), HDF5 (可选), ChaiScript (可选) |

### 最近提交活跃度

| SHA | 日期 | 作者 | 内容 |
|---|---|---|---|
| c2141cf | 2026-10-09 | Pavel | Merge HDF5 support |
| d56a09a | 2026-10-08 | Tymofiy Zakharchuk | Full HDF5 I/O (OpenSPH+GADGET formats) |
| 05af156 | 2026-10-09 | Tymofiy Zakharchuk | HDF5 safety + GADGET interop fix |
| f3033fa | 2024-07-29 | pavel | Merge patch |
| a502076 | 2024-05-13 | Bishal Bhattacharyya | Update control |
| 6c0d4b8 | 2023-05-14 | Pavel Ševeček | CI image fixes |
| cd953fc | 2022-10-24 | Pavel Ševeček | Equilibrium density solver + Peng-Robinson EoS |

**活跃度判定**: 低-中频维护。核心开发 2021-2022 密集，2023-2024 低频，2026-10 有 HDF5 外部贡献者活跃。项目成熟稳定但非高频迭代。

### 目录结构

```
OpenSPH/
├── core/                  # 核心库
│   ├── sph/               # SPH 求解器核心
│   │   ├── solvers/       # 12+ 种 SPH solver
│   │   ├── equations/     # 方程项 (Fluids, DeltaSph, XSph, Heat, ...)
│   │   │   └── av/        # 人工粘度 (Standard, Balsara, MorrisMonaghan, Riemann, Conductivity, Stress)
│   │   ├── boundary/      # 边界条件
│   │   ├── kernel/        # SPH 核函数
│   │   ├── initial/       # 初始条件生成
│   │   └── test/          # SPH 单元测试
│   ├── gravity/           # N-body / 引力 (BarnesHut, BruteForce, NBodySolver)
│   ├── physics/           # 物理模型 (EoS, ANEOS, Rheology, Damage)
│   ├── timestepping/      # 时间积分
│   ├── objects/finders/   # 邻居搜索 (KdTree, Octree, BVH, UniformGrid, HashMap, Periodic)
│   ├── quantities/        # 物理量管理 (Storage, Material)
│   ├── thread/            # 线程池 (ThreadPool, Scheduler)
│   ├── io/                # I/O
│   ├── math/              # 数学工具
│   └── run/               # 运行控制
├── gui/                   # wxWidgets GUI
├── cli/                   # 命令行接口
├── test/                  # 集成测试 (Catch2 框架)
├── bench/                 # 性能基准
├── regression/            # 回归测试 (impact, nbody-bounce, nbody-merge, rotation)
├── examples/              # 6 个示例 (asteroid, death star, collision, ...)
├── post/                  # 后处理
├── .github/workflows/     # CI (cmake.yml, codeql-analysis.yml)
└── .gitlab-ci.yml         # GitLab CI
```

### CI 配置

- **GitHub Actions** (`cmake.yml`): Ubuntu latest, 矩阵构建 = {Release,Debug} × {VDB ON/OFF} × {Eigen ON/OFF} × {TBB ON/OFF} × {single/double precision}
- **CodeQL** 安全分析
- **GitLab CI** (`.gitlab-ci.yml`): 额外 CI 管线
- 测试框架: **Catch2** (v2)

---

## 2. 源码级方法清单

### 2.1 SPH 求解器变体（core/sph/solvers/）

| 求解器 | 文件 | 方法描述 | 证据等级 |
|---|---|---|---|
| **SymmetricSolver** | `SymmetricSolver.h` | 标准对称 SPH（PPE 对称形式），模板化 Dim 维 | REAL |
| **AsymmetricSolver** | `AsymmetricSolver.h` | 非对称粒子评估，hash-map 搜索半径 | REAL |
| **SummationSolver** | `SummationSolver.h` | 密度直接求和 + 能量方程（ continuity eq 替代） | REAL |
| **DensityIndependentSolver** | `DensityIndependentSolver.h` | Saitoh & Makino (2013) 密度无关 SPH，独立变量=能量密度 q + 内能 U | REAL |
| **EnergyConservingSolver** | `EnergyConservingSolver.h` | Owen et al. (2009) 能量守恒形式 | REAL |
| **EntropySolver** | `EntropySolver.h` | Springel & Hernquist (2002) 熵演化（仅理想气体 EoS） | REAL |
| **EquilibriumSolver** | `EquilibriumSolver.h` | 平衡态密度/能量/应力求解器 | REAL |
| **PositionBasedSolver** | `PositionBasedSolver.h` | PBD (Position Based Dynamics) 风格 SPH，poly6+spiky 核 | REAL |
| **GradHSolver** | `GradHSolver.h` | ∇h 梯度修正 | REAL |
| **StabilizationSolver** | `StabilizationSolver.h` | 密度调整稳定化 (2022-10 新增) | REAL |
| **GravitySolver** | `GravitySolver.h` | N-body 引力耦合 | REAL |
| **ElasticDeformationSolver** | `ElasticDeformationSolver.h` | 弹性变形（固体力学） | REAL |

**SPH 变体覆盖**: WCSPH (SummationSolver/Standard), 密度无关 SPH (DensityIndependentSolver), δ-SPH (DeltaSph), PBD (PositionBasedSolver), 能量守恒 (EnergyConservingSolver), 熵形式 (EntropySolver)。**无 ISPH/PCISPH/DFSPH/IISPH 显式实现**。

### 2.2 方程项（core/sph/equations/）

| 方程 | 文件:行 | 描述 |
|---|---|---|
| 标准流体方程 | `Fluids.h` | 压力梯度 + 连续性方程 + 能量方程；含表面张力 (Akinci 2013 Lennard-Jones cohesion) |
| **δ-SPH** | `DeltaSph.h` | Marrone et al. (2011) 密度扩散项；RenormalizedDensityGradient + DensityDiffusion |
| **XSPH** | `XSph.h` | Monaghan 1992 速度平均修正 |
| 热传导 | `Heat.h` | 热扩散方程 |
| 摩擦 | `Friction.h` | 摩擦模型 |
| 旋转 | `Rotation.h` | 旋转参考系 |
| YORP 效应 | `Yorp.h` | 辐射力矩（天体物理特有） |
| 累积量 | `Accumulated.cpp/h` | 导数累积框架 |

### 2.3 人工粘度（core/sph/equations/av/）

| 模型 | 文件 | 描述 |
|---|---|---|
| Standard AV | `Standard.h` | 标准 Monaghan 人工粘度 |
| Balsara Switch | `Balsara.h` | Balsara 剪切开关 |
| Morris-Monaghan | `MorrisMonaghan.h` | Morris & Monaghan 粘度 |
| Riemann Solver | `Riemann.h` | Riemann 求解器型 AV |
| Conductivity | `Conductivity.h` | 热传导 |
| Stress Tensor | `Stress.cpp/h` | 应力张量型粘度 |

### 2.4 核函数（core/sph/kernel/）

- `Kernel.h`: 模板化核函数基类 + **LutKernel** (40000 条目查找表加速)
- `Interpolation.h`: 插值工具
- `GravityKernel.h`: 引力专用核

### 2.5 边界处理（core/sph/boundary/）

- `Boundary.h/cpp`: 通用边界条件框架（IBoundaryCondition 接口）
  - `initialize()` → 导数计算前
  - `finalize()` → 导数计算后
  - `GhostParticlesData`: 鬼粒子管理
  - `NullBoundaryCondition`: 空边界（周期性/无界）
- **边界方法**: 鬼粒子法（ghost particles），无显式 dummy particle / dynamic boundary 实现

### 2.6 时间步控制（core/timestepping/）

- `TimeStepping.h/cpp`: 时间积分框架
  - `ITimeStepping::step()` → 调用 `stepParticles()`
  - 自动清除高阶导数、量值钳位
- `TimeStepCriterion.h/cpp`: CFL 条件 + 用户自定义判据
- `ISolver.h`: 求解器接口

### 2.7 邻居搜索（core/objects/finders/）

| 方法 | 文件 | 描述 |
|---|---|---|
| KdTree | `KdTree.cpp/h` | KD 树 |
| Octree | `Octree.h` | 八叉树 |
| BVH | `Bvh.h` | 包围盒层次 |
| UniformGrid | `UniformGrid.cpp/h` | 均匀网格 |
| HashMap | `HashMapFinder.cpp/h` | 哈希表 |
| AdaptiveGrid | `AdaptiveGrid.h` | 自适应网格 |
| PeriodicFinder | `PeriodicFinder.cpp/h` | 周期性边界 |
| BruteForce | `BruteForceFinder.h` | 暴力搜索（测试用） |
| Incremental | `IncrementalFinder.cpp/h` | 增量更新 |

### 2.8 物理模型（core/physics/）

- **EoS**: `Eos.h/cpp` — 状态方程框架（理想气体、Tillotson、Murnaghan、Peng-Robinson 等）
- **ANEOS**: `Aneos.cpp/h` — ANEOS 表格式 EoS（2021-01 新增）
- **Rheology**: `Rheology.h/cpp` — 流变模型
- **Damage**: `Damage.h/cpp` — 损伤模型（碎片化模拟）

### 2.9 并行策略

| 策略 | 实现 | 证据 |
|---|---|---|
| **共享内存多线程** | ThreadPool + TBB 可选 | `core/thread/Pool.h` — 固定线程数任务池；CMake `WITH_TBB` 选项 |
| **SIMD** | SSE/AVX | README 明确提及 "utilizing modern CPU features (SSE/AVX instruction sets)" |
| **GPU** | **无** | 源码中未发现 CUDA/OpenCL/SYCL 文件 |
| **MPI** | **无** | 源码中未发现 MPI 调用 |
| **并行粒度** | 邻居循环并行化 | `parallelInvoke` in IScheduler (CHANGELOG 2021-08-25) |

---

## 3. 验证面盘点

### 3.1 自带算例/基准

| 算例 | 路径 | 类型 | 描述 |
|---|---|---|---|
| Hello Asteroid | `examples/01_hello_asteroid/` | 天体 | 小行星基础模拟 |
| Death Star | `examples/02_death_star/` | 天体 | 碰撞破坏 |
| Van der Waals | `examples/03_van_der_waals/` | 材料 | 范德华力 |
| Simple Collision | `examples/04_simple_collision/` | 碰撞 | 简单碰撞 |
| Fragmentation | `examples/05_fragmentation_reaccumulation/` | 天体 | 碎片化+再聚集 |
| Heat Diffusion | `examples/06_heat_diffusion/` | 热传导 | 热扩散验证 |

### 3.2 回归测试

| 测试 | 路径 | 内容 |
|---|---|---|
| Impact | `regression/impact/` | 碰撞场景 + reference.png + reference.scf |
| N-body Bounce | `regression/nbody-bounce/` | N体弹跳 |
| N-body Merge | `regression/nbody-merge/` | N体合并 |
| Rotation | `regression/rotation/` | 旋转 |

### 3.3 测试框架

- **框架**: Catch2 (v2) — `test/main.cpp` 使用 `Catch::Session()`
- **单元测试位置**: 各模块 `test/` 子目录 (core/sph/test, core/physics/test, core/objects/finders/test, ...)
- **性能基准**: `bench/` 目录 + `core/sph/solvers/benchmark/` + `core/sph/kernel/benchmark/`
- **CI 集成**: GitHub Actions 矩阵构建 + GitLab CI

### 3.4 量化验证数据

- **有**: 回归测试含 reference.png/reference.scf 参考图像
- **无**: 未发现标准 CFD 基准 (dam-break/Poiseuille/Taylor-Green) 的量化对比数据
- **定位**: 天体物理 SPH 为主，非传统 CFD 验证面

---

## 4. 对 TensorPhysics SPH 线的可迁移方案

### License 合规判定

**OpenSPH = MIT License** → ✅ 完全合规，可 adapt/fit/reference 任意使用。

### 方案 1: adapt — δ-SPH 密度扩散模型迁移

- **来源**: `core/sph/equations/DeltaSph.h` — `DensityDiffusion` 类
- **方法**: Marrone et al. (2011) δ-SPH 密度扩散项 + RenormalizedDensityGradient
- **迁移路径**: 将 DeltaSph.h 中的 DerivativeTemplate 模式适配到 TensorSPH 的方程框架
- **适配难度**: 中（需将 OpenSPH 的 Accumulated/Storage 体系映射到 TensorSPH 数据布局）
- **GAP 锚**: `GAP-SPH-DELTA-SPH`
- **License**: MIT → adapt 合规

### 方案 2: fit — 密度无关 SPH (Saitoh & Makino 2013) 参考实现

- **来源**: `core/sph/solvers/DensityIndependentSolver.h`
- **方法**: 以能量密度 q 和内能 U 为独立变量，避免密度不连续导致的表面不稳定性
- **迁移路径**: 作为 TensorSPH 天体/自由面场景的可选求解器参考
- **适配难度**: 中-高（需理解 Saitoh & Makino 公式与 OpenSPH 的自洽 h 迭代）
- **GAP 锚**: `GAP-SPH-ISPH-CONVERGENCE`（密度无关形式可改善收敛性）
- **License**: MIT → fit 合规

### 方案 3: reference — 人工粘度模型库

- **来源**: `core/sph/equations/av/` — 6 种 AV 模型（Standard, Balsara, MorrisMonaghan, Riemann, Conductivity, Stress）
- **方法**: 完整的 AV 模型族谱，含开关策略和 Riemann 求解器型
- **迁移路径**: 作为 TensorSPH AV 模块的参考实现和对比基准
- **适配难度**: 低（各 AV 模型独立，可逐个移植）
- **GAP 锚**: `GAP-SPH-WCSPH-VALIDATION`（AV 模型选择直接影响验证精度）
- **License**: MIT → reference 合规

### 方案 4: reference — 邻居搜索算法库

- **来源**: `core/objects/finders/` — KdTree, Octree, BVH, UniformGrid, HashMap, AdaptiveGrid, Periodic
- **方法**: 7 种邻居搜索策略的完整实现
- **迁移路径**: 为 TensorSPH 提供邻居搜索性能对比参考
- **适配难度**: 低-中（接口清晰，可独立使用）
- **GAP 锚**: `GAP-SPH-GPU-SCALING`（邻居搜索是 GPU 扩展的关键瓶颈）
- **License**: MIT → reference 合规

### 方案 5: fit — PBD (Position Based Dynamics) SPH 求解器

- **来源**: `core/sph/solvers/PositionBasedSolver.h`
- **方法**: PBD 风格约束投影，poly6+spiky 核组合
- **迁移路径**: 为 TensorSPH 提供 FSI/自由面场景的备选求解器
- **适配难度**: 中
- **GAP 锚**: `GAP-SPH-FSI-BENCHMARK`
- **License**: MIT → fit 合规

---

## 5. 机器可读块

[OSS-INV]{"campaign":"osssph-w2","repo":"pavelsevecek/OpenSPH","tier":1,"methods":["SymmetricSolver(WCSPH)","AsymmetricSolver","SummationSolver(density-summation)","DensityIndependentSolver(Saitoh-Makino2013)","EnergyConservingSolver(Owen2009)","EntropySolver(Springel-Hernquist2002)","PositionBasedSolver(PBD)","EquilibriumSolver","GradHSolver","StabilizationSolver","ElasticDeformationSolver","DeltaSph(Marrone2011)","XSph(Monaghan1992)"],"boundary":"GhostParticles (IBoundaryCondition interface, initialize/finalize pattern)","gpu":"NONE (CPU-only: ThreadPool+TBB+SSE/AVX; no CUDA/OpenCL/MPI)","validation":"Catch2 unit tests + 4 regression tests (impact/nbody-bounce/nbody-merge/rotation) + 6 examples; no standard CFD benchmarks (dam-break/Poiseuille/Taylor-Green)","license":"MIT","transferable":["adapt:DeltaSph-density-diffusion→GAP-SPH-DELTA-SPH","fit:DensityIndependentSolver→GAP-SPH-ISPH-CONVERGENCE","reference:AV-models(6)→GAP-SPH-WCSPH-VALIDATION","reference:neighbor-finders(7)→GAP-SPH-GPU-SCALING","fit:PositionBasedSolver→GAP-SPH-FSI-BENCHMARK"]}[/OSS-INV]

[OSS-SOL]{"repo":"pavelsevecek/OpenSPH","id":"SPHSOL-opensph-01","kind":"adapt","summary":"Migrate δ-SPH density diffusion (Marrone 2011) from DeltaSph.h — DensityDiffusion + RenormalizedDensityGradient classes. Adapt DerivativeTemplate pattern to TensorSPH equation framework. Key: density diffusion term δ·∇²ρ stabilizes free-surface flows.","gap":"GAP-SPH-DELTA-SPH"}[/OSS-SOL]

[OSS-SOL]{"repo":"pavelsevecek/OpenSPH","id":"SPHSOL-opensph-02","kind":"fit","summary":"Port density-independent SPH (Saitoh & Makino 2013) from DensityIndependentSolver.h. Independent variables: energy density q + internal energy U. Self-consistent smoothing length iteration. Improves surface stability for astrophysical/multiphase flows.","gap":"GAP-SPH-ISPH-CONVERGENCE"}[/OSS-SOL]

[OSS-SOL]{"repo":"pavelsevecek/OpenSPH","id":"SPHSOL-opensph-03","kind":"reference","summary":"Reference implementation library of 6 artificial viscosity models (Standard/Balsara/MorrisMonaghan/Riemann/Conductivity/Stress) from core/sph/equations/av/. Use as AV selection guide and validation baseline for TensorSPH WCSPH.","gap":"GAP-SPH-WCSPH-VALIDATION"}[/OSS-SOL]

[OSS-SOL]{"repo":"pavelsevecek/OpenSPH","id":"SPHSOL-opensph-04","kind":"reference","summary":"7 neighbor search algorithms (KdTree/Octree/BVH/UniformGrid/HashMap/AdaptiveGrid/Periodic) from core/objects/finders/. Reference for GPU scaling bottleneck analysis and algorithm selection.","gap":"GAP-SPH-GPU-SCALING"}[/OSS-SOL]

[OSS-SOL]{"repo":"pavelsevecek/OpenSPH","id":"SPHSOL-opensph-05","kind":"fit","summary":"Position Based Dynamics SPH solver from PositionBasedSolver.h. poly6+spiky kernel pair, constraint projection iteration. Candidate for FSI/free-surface benchmarks in TensorSPH.","gap":"GAP-SPH-FSI-BENCHMARK"}[/OSS-SOL]

---

## 6. 总结与评价

### 优势
1. **SPH 变体丰富**: 12+ 种求解器覆盖 WCSPH/密度无关/PBD/熵形式/能量守恒等
2. **天体物理深度**: N-body + BarnesHut + ANEOS + Damage + YORP = 完整的小行星碰撞链
3. **MIT 许可**: 完全开放，可自由迁移
4. **代码质量**: 模板化设计、CRTP 模式、查找表加速、清晰接口

### 局限
1. **无 GPU/MPI**: 纯 CPU 并行（TBB+SIMD），大规模计算受限
2. **无标准 CFD 验证**: 缺 dam-break/Poiseuille/Taylor-Green 等经典基准
3. **无 ISPH/PCISPH/DFSPH**: 缺不可压 SPH 家族（压力泊松方程求解）
4. **低频维护**: 核心开发者 2022 后活跃度下降

### 对 TensorPhysics SPH 线的价值
- **高价值**: δ-SPH 实现、密度无关 SPH、AV 模型库、邻居搜索算法库
- **中价值**: PBD 求解器、XSPH 修正、EoS 框架
- **低价值**: 天体物理特有功能（YORP、Galaxy 初始化）

---

*报告生成: m8653 agent | 数据源: GitHub API + 源码阅读 | 零编造零手抄*
