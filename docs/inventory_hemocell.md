# 【盘点】UvaCsl/HemoCell — 开源 LBM 技术深盘

> 任务 ID: m8302 | 盘点对象: UvaCsl/HemoCell (tier 3, LBM+细胞 IBM 悬浮流耦合 HPC)
> 代码版本: v2.8 (2026-08-01 tag) | clone SHA: 5a41084
> 盘点日期: 2026-11-09

---

## 一、概况

| 项 | 值 |
|---|---|
| 语言 | C++（主）+ CMake 构建；依赖 Palabos v2.3+（LBM 内核全部来自 Palabos） |
| 许可证 | **AGPL-3.0**（GNU Affero General Public License v3 or later） |
| 许可证可用性判断 | ⚠️ **AGPL 对 TensorLBM 有传染性风险**：AGPL 要求网络交互场景下也必须开源全部衍生代码。若 TensorLBM 链接 HemoCell 代码（哪怕只复用 IBM kernel），整个 TensorLBM 须 AGPL 开源。**建议：仅参考算法思路与架构设计，不直接 copy 代码**；若需复用 IBM kernel 实现，须自行重写（clean-room）。 |
| 活跃度 | 中低：最新 tag v2.8 (2026-08)，CHANGELOG 最新记录 v2.6 (2022-07)；维护方为 UvA CSL (info@hemocell.eu)；Zenodo DOI 活跃 |
| 定位 | **血液/细胞悬浮流专用模拟器**：LBM 流体 + IBM 耦合 + 三角网格细胞力学模型（RBC/WBC/PLT），面向微血管/血栓/支架等生物医学场景 |

**一句话**：HemoCell 不是通用 LBM 求解器，而是基于 Palabos 的**生物细胞悬浮流应用框架**——LBM 内核 100% 委托 Palabos，自身贡献在 IBM 耦合、细胞力学模型、负载平衡和生物医学案例。

---

## 二、方法谱系

### 2.1 格子（Lattice）

| 格子 | 实现位置 | 说明 |
|---|---|---|
| **D3Q19**（强制） | `config/constant_defaults.h:60` → `plb::descriptors::ForcedD3Q19Descriptor` | 唯一流体格子，编译期硬编码 |
| **D3Q19 AD** | `config/constant_defaults.h:65` → `plb::descriptors::AdvectionDiffusionD3Q19Descriptor` | CEPAC 药物输运模块用 |

**无 D2Q9/D3Q15/D3Q27**。格子完全由 Palabos `nearestNeighborLattices3D.h` 提供，HemoCell 自身不定义格子。

### 2.2 碰撞（Collision）

| 碰撞模型 | 实现位置 | 说明 |
|---|---|---|
| **BGK** | 所有 cases 如 `cases/cellCollision/cellCollision.cpp:67` → `GuoExternalForceBGKdynamics<T, DESCRIPTOR>(1.0/param::tau)` | 唯一碰撞模型，来自 Palabos |

**无 TRT/MRT/KBC/RR**。整个代码库 grep 仅出现 BGK。碰撞完全委托 Palabos 的 `GuoExternalForceBGKdynamics`。

### 2.3 力项（Forcing）

| 力项方案 | 实现位置 | 说明 |
|---|---|---|
| **Guo 外力项** | `cases/*/` 全局使用 `GuoExternalForceBGKdynamics`；力重置 `core/hemoCell.cpp:368-370` | 通过 Palabos 的 `ExternalField` 机制注入体力，IBM 粒子力 → 流体体力 via `spreadParticleForce()` |

**无 RK/shifted-velocity/Luo 等**。Guo forcing 是唯一方案。

### 2.4 时间积分（粒子力学）

| 方案 | 位置 | 说明 |
|---|---|---|
| Euler (1) / Adams-Bashforth (2) | `config/constant_defaults.h:44-48` `HEMOCELL_MATERIAL_INTEGRATION` | 编译期选择，默认 Euler |

---

## 三、边界处理

### 3.1 Bounce-back

| 型式 | 位置 | 说明 |
|---|---|---|
| 标准满格 bounce-back | `cases/atherosclerosis/atherosclerosis.cpp:128,135-138` → `BounceBack<T, DESCRIPTOR>` | 来自 Palabos，用于壁面 |
| 带 flag 矩阵 bounce-back | `cases/atherosclerosis/atherosclerosis.cpp:125` → `defineDynamics(... flagMatrix ... BounceBack)` | 基于体素化几何（STL）设置固体节点 |

### 3.2 Zou-He

| 型式 | 位置 | 说明 |
|---|---|---|
| 压力边界（Zou-He 包装） | `cases/AR2/AR2.cpp:125-128` → `WrappedZouHeBoundaryManager3D<T,DESCRIPTOR>` | 出口压力边界，配合 `setBoundaryDensity` |
| 速度边界 | `cases/cellCollision/cellCollision.cpp:72-73` → `createLocalBoundaryCondition3D` | Palabos 局部边界条件 |

### 3.3 Lees-Edwards（剪切周期）

| 型式 | 位置 | 说明 |
|---|---|---|
| 自定义 LE BC | `helper/leesEdwardsBC.h` 全文（~200 行） | **HemoCell 原创**：基于 Palabos `BoxProcessingFunctional3D_L` 的 data processor，在 z 方向实现剪切周期边界；通过 population 读写实现偏移；仅支持单方向 |

### 3.4 IBM（浸没边界法）——核心贡献

| 组件 | 位置 | 说明 |
|---|---|---|
| IBM kernel (phi2) | `core/immersedBoundaryMethod.h:35-38` → `phi2(x) = max(1-|x|, 0)` | 3-point Peskin 核，张量积 3D → 8 点插值 |
| IBM kernel (phi3/phi4) | `core/immersedBoundaryMethod.h:40-44` | 4-point 和 3-point 备选核（声明在 .h，实现在 .cpp） |
| 插值系数计算 | `core/immersedBoundaryMethod.h:46-140` `interpolationCoefficientsPhi2()` | 8 点 stencil，归一化权重，跳过边界节点 |
| 力扩散修正 | `core/immersedBoundaryMethod.h:155-160` `curateInterpolationCoefficients()` | 当插值邻域含边界节点时重新分配权重 |
| 力扩散（particle→fluid） | `core/hemoCell.cpp:313` → `cellfields->spreadParticleForce()` | IBM 力从 Lagrangian 点扩散到 Euler 格子 |
| 速度插值（fluid→particle） | 同上文件，`interpolationCoefficients` 系列 | 流体速度插值到 Lagrangian 点 |

### 3.5 PreInlet（入口发展段）

| 组件 | 位置 | 说明 |
|---|---|---|
| PreInlet 模块 | `helper/preInlet.h` + `helper/preInlet.cpp` | 附加入口段用于发展充分流动，独立 lattice + 周期性驱动 |

---

## 四、多相/多组元

### **结论：无**

HemoCell **没有**任何传统意义上的多相/多组元 LBM 实现：

- ❌ **无 Shan-Chen (SCMP)**：代码库 grep `Shan.Chen`/`pseudopotential`/`psi`/`interaction.potential` 零命中
- ❌ **无 MCMP（多组元）**：无第二分布函数、无颜色梯度
- ❌ **无 Free-energy / Phase-field / Allen-Cahn**：零命中
- ❌ **无 Free-surface**：零命中
- ❌ **无颜色梯度模型**：零命中

**HemoCell 的"多相"概念是 IBM 粒子（细胞）+ 单相流体**，不是 LBM 多相流。细胞通过 IBM 耦合到流体，细胞间有排斥力（`applyRepulsionForce()`）和粘附力（`bindingField`），但这些都是 Lagrangian 粒子间作用力，不是 LBM 介观多相模型。

**与"多相"最接近的功能**：
- 细胞内粘度（interior viscosity）：`helper/interiorViscosity.h` + `helper/interiorViscosity.cpp`——通过 `MultiScalarField3D<T>` 在粒子占据的格子节点上修改局部 tau，实现细胞内外粘度差异。这是**单流体变粘度**方案，不是真正的两相。
- 固化力学（solidify）：`config/constant_defaults.h:38-39` `SOLIDIFY_MECHANICS`——血小板粘附后"固化"到壁面，本质是 IBM 粒子锁定，不是相变。

---

## 五、GPU/并行

### 5.1 GPU

**无 GPU 实现**。代码库 grep `CUDA`/`GPU`/`OpenCL`/`HIP`/`SYCL` 零命中（仅一处 `auto_ptr` 注释误匹配）。HemoCell 是纯 CPU 代码。

### 5.2 数据布局

由 Palabos 管理。Palabos 使用 **AoS（Array of Structs）** 风格的 `BlockLattice3D`，每个 Cell 存储 q 个分布函数 + 外部场。HemoCell 自身不控制流体数据布局。

粒子数据：`core/hemoCellParticle.h` 定义 `HemoCellParticle` 结构体（含 position/velocity/force 等 `hemo::Array<T,3>` 成员），以 `vector<HemoCellParticle*>` 组织——**AoS per particle**。

### 5.3 MPI 并行

| 组件 | 位置 | 说明 |
|---|---|---|
| MPI 并行 | `config/config.cpp:31` → `#include "parallelism/mpiManager.h"` | 通过 Palabos MPI 管理器 |
| MultiBlockLattice3D | 所有 cases 如 `cases/cellCollision/cellCollision.cpp:62` | Palabos 多块格子，自动域分解 |
| 编译宏 | `CMakeLists.txt:56` → `-DPLB_MPI_PARALLEL` | 强制 MPI 并行 |
| SMP 并行 | `CMakeLists.txt:58` → `-DPLB_SMP_PARALLEL` | OpenMP 线程并行 |

### 5.4 负载平衡

| 组件 | 位置 | 说明 |
|---|---|---|
| ParMETIS 负载平衡 | `helper/loadBalancer.h` + `helper/loadBalancer.cpp` | **可选**（`HEMO_PARMETIS` 编译宏）；基于粒子数/计算时间的动态负载重分配 |
| 时间采集 | `helper/loadBalancer.cpp:55-70` `GatherTimeOfAtomicBlocks` | 收集每个 atomic block 的流体/粒子计算时间和粒子数 |
| 块重组 | `helper/loadBalancer.h:30` `restructureBlocks()` | 基于 ParMETIS 的块重映射 |

### 5.5 通信隐藏

由 Palabos 的 `BlockCommunicator` 管理 envelope 同步。HemoCell 的 `cellfields->syncEnvelopes()` 触发通信。无显式通信隐藏/重叠计算设计。

---

## 六、可微/代码生成

**无可微/代码生成支持**：

- ❌ 无 autograd/PyTorch/JAX 接口
- ❌ 无符号推导/代码生成
- ❌ 无 kernel 自动生成
- 所有 kernel 均为手写 C++ 模板，编译期实例化

---

## 七、与我们功能点的映射

| feature_ledger slug | HemoCell 覆盖情况 | 说明 |
|---|---|---|
| **GAP-LBM-MCMP-POISEUILLE**（两组分分层 Poiseuille） | ❌ 无 | HemoCell 无多相 LBM，无法直接映射 |
| **MCMP 高密度比** | ❌ 无 | 无 Shan-Chen / MCMP |
| **自由面** | ❌ 无 | 无 free-surface |
| **相场 Allen-Cahn** | ❌ 无 | 无 phase-field |
| **GPU 融合 kernel** | ❌ 无 | 纯 CPU，无 GPU |
| **分布式多卡** | ✅ 部分 | MPI+OpenMP 并行 + ParMETIS 负载平衡（`helper/loadBalancer.cpp`），但仅限 CPU |
| **稀疏/多孔** | ✅ 部分 | 体素化几何（`helper/voxelizeDomain.h`）+ flag 矩阵定义固体域，可处理复杂几何（如支架），但非 LBM 多孔介质模型 |
| **案例组织** | ✅ 可参考 | `cases/` + `examples/` 目录结构清晰，XML 配置驱动，案例模板化 |
| **测试与验证面** | ✅ 可参考 | `tests/` 目录含 gtest 框架 + validation 子目录（pipe flow / stretch cell 验证） |
| **CI** | ✅ 可参考 | CHANGELOG 提及 4 阶段 CI pipeline（build/test/validation/documentation） |

**NEW 提案**：
- `NEW:IBM-耦合框架`——HemoCell 的 IBM 耦合架构（phi2 kernel + force spreading + velocity interpolation + interior viscosity）可作为 TensorLBM IBM 模块的参考实现
- `NEW:粒子负载平衡`——ParMETIS 动态负载平衡策略（基于粒子数+计算时间）可复用于 TensorLBM 的颗粒流场景

---

## 八、可吸收 Top3 + 风险

### Top 1：IBM 耦合架构设计（参考级）

**内容**：HemoCell 的 IBM 实现（`core/immersedBoundaryMethod.h`）展示了完整的 IBM 耦合架构：
- Peskin kernel 选择（phi2/phi3/phi4 多阶）
- 插值系数归一化 + 边界节点修正
- 力扩散 + 速度插值的双向耦合
- Interior viscosity 的变粘度处理

**对我们的价值**：TensorLBM 若需 IBM 耦合（如颗粒流/流固耦合），可参考此架构设计。

**风险**：AGPL 许可证——必须 clean-room 重写，不可 copy 代码。

**transfer**: reference

### Top 2：ParMETIS 动态负载平衡策略（参考级）

**内容**：`helper/loadBalancer.cpp` 实现了基于计算时间+粒子数的动态负载平衡：
- 采集每个 atomic block 的流体/粒子计算时间
- 基于 ParMETIS 进行块重映射
- 支持 checkpoint 重载

**对我们的价值**：TensorLBM 的颗粒流/IBM 场景存在负载不均问题，此策略可参考。

**风险**：ParMETIS 依赖较重；需评估是否值得引入。

**transfer**: reference

### Top 3：案例组织与验证面架构（可适配）

**内容**：HemoCell 的案例组织（`cases/` + `examples/` + XML 配置 + gtest 验证）展示了良好的工程实践：
- 案例与库分离
- XML 配置驱动参数
- 验证测试独立于功能测试
- CI 4 阶段流水线

**对我们的价值**：TensorLBM/TensorPlatform 的案例组织和验证面可参考此架构。

**风险**：低。架构设计不受许可证限制。

**transfer**: adapt

---

## 守护线解析

[OSS-INV]
repo: UvaCsl/HemoCell
lang: C++
license: AGPL-3.0
methods: D3Q19×BGK(Guo forcing)
multiphase: 无（仅 IBM 粒子+单相流体；interior viscosity 为变粘度方案 helper/interiorViscosity.cpp:1-80）
boundary: BounceBack(Palabos); ZouHe(WrappedZouHeBoundaryManager3D); Lees-Edwards(helper/leesEdwardsBC.h); IBM-phi2/phi3/phi4(core/immersedBoundaryMethod.h:35-160)
gpu: 无（纯 CPU MPI+OpenMP）
summary: HemoCell 是基于 Palabos 的生物细胞悬浮流专用框架，LBM 内核 100% 委托 Palabos（D3Q19+BGK+Guo forcing），自身贡献在 IBM 耦合、细胞力学模型和负载平衡。无多相 LBM、无 GPU、无可微支持。AGPL 许可证对 TensorLBM 有传染性风险，仅可参考架构。
[/OSS-INV]

[OSS-SOL]
repo: UvaCsl/HemoCell
slug: NEW:IBM-耦合框架
tag: ibm-peskin-kernel-coupling
method: 基于 Peskin kernel (phi2/phi3/phi4) 的 IBM 耦合：流体速度插值到 Lagrangian 点，粒子力扩散到 Euler 格子，支持 interior viscosity 变粘度
files: core/immersedBoundaryMethod.h:35-160, helper/interiorViscosity.cpp:1-80
conventions: phi2 = 3-point Peskin kernel; 8-point stencil; 权重归一化; 边界节点跳过
transfer: reference
[/OSS-SOL]

[OSS-SOL]
repo: UvaCsl/HemoCell
slug: NEW:粒子负载平衡
tag: parmetis-dynamic-load-balancing
method: 基于 ParMETIS 的动态负载平衡：采集每个 atomic block 的流体/粒子计算时间和粒子数，进行块重映射
files: helper/loadBalancer.h:1-80, helper/loadBalancer.cpp:1-80
conventions: 编译宏 HEMO_PARMETIS 启用; 支持 checkpoint 重载
transfer: reference
[/OSS-SOL]

[OSS-SOL]
repo: UvaCsl/HemoCell
slug: NEW:案例组织与验证面
tag: case-organization-validation-pipeline
method: 案例与库分离（cases/ + examples/），XML 配置驱动，gtest 验证框架，CI 4 阶段流水线（build/test/validation/documentation）
files: cases/, examples/, tests/, CHANGELOG.md:50-60
conventions: 案例通过 configuration.xml 输入; 验证测试独立于功能测试
transfer: adapt
[/OSS-SOL]

---

## 附：代码指针汇总

| 功能 | 文件:行 |
|---|---|
| D3Q19 格子定义 | `config/constant_defaults.h:60` |
| BGK + Guo forcing | `cases/cellCollision/cellCollision.cpp:67` |
| BounceBack 边界 | `cases/atherosclerosis/atherosclerosis.cpp:128,135-138` |
| ZouHe 边界 | `cases/AR2/AR2.cpp:125-128` |
| Lees-Edwards BC | `helper/leesEdwardsBC.h:1-200` |
| IBM kernel (phi2) | `core/immersedBoundaryMethod.h:35-38` |
| IBM 插值系数 | `core/immersedBoundaryMethod.h:46-140` |
| 力扩散 | `core/hemoCell.cpp:313` |
| Interior viscosity | `helper/interiorViscosity.h:1-60`, `helper/interiorViscosity.cpp:1-80` |
| ParMETIS 负载平衡 | `helper/loadBalancer.h:1-80`, `helper/loadBalancer.cpp:1-80` |
| RBC 力学模型 | `mechanics/rbcHighOrderModel.h:1-80`, `mechanics/rbcHighOrderModel.cpp:1-100` |
| 体素化几何 | `helper/voxelizeDomain.h:1-40` |
| PreInlet 模块 | `helper/preInlet.h:1-60` |
| 固化力学 | `config/constant_defaults.h:38-39`, `core/hemoCell.cpp:334-338` |
| MPI 并行 | `config/config.cpp:31`, `CMakeLists.txt:56` |

---

**盘点结论**：HemoCell 是生物医学领域的专业工具，对 TensorLBM 的通用 LBM 开发**直接价值有限**（无多相、无 GPU、无可微）。但其 IBM 耦合架构、负载平衡策略和案例组织可作为**参考级**输入。AGPL 许可证要求 clean-room 重写，不可直接复用代码。
