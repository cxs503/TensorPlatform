# 【盘点】openLB 开源格子 Boltzmann 代码深度调研

**scenario_key**: oss-inventory  
**mission_id**: 8548  
**盘点对象**: openLB（主仓）+ ThermalMultiphaseOpenLB（热多相扩展）  
**镜像状态**: 非官方镜像，停在 ~2019（v1.3-1.4 区间）；ThermalMultiphaseOpenLB 为独立扩展分支  
**盘点日期**: 2026-11-09  
**盘点方法**: 源码阅读（/tmp/openLB + /tmp/ThermalMultiphaseOpenLB），GitHub API 不可达故走本地镜像

---

## 一、案例介绍（物理背景与仓库定位）

### 1.1 openLB 是什么

openLB 是最早的开源 LBM 框架之一（C++，GPLv2），由 Orestis Malaspinas / Andrea Parmigiani / Jonas Latt 等人在 2008 年前后发起，官方站点 openlb.net。本仓为**非官方 GitHub 镜像**（README 首行即声明 "Unofficial mirror"），最后提交 `6c3934d Create README.md`，时间戳停在 ~2019，对应官方 v1.3–v1.4 区间。

**定位**：通用 LBM 框架，强调「可读、模块化、易扩展」，覆盖从层流到湍流、单组分多相、热对流、颗粒悬浮等方向。

**代码体量**：
- 源文件总数：868（.h/.hh/.cpp）
- dynamics 子目录：111 个头文件/实现
- 示例目录：6 大类（laminar / multiComponent / particles / porousMedia / thermal / turbulence），约 30 个独立算例

### 1.2 与 openLBMPM 的关系

波 1 已盘的 `openLBMPM` 是 openLB 的**多相扩展分支**（multiphase），本单盘的是**母体 openLB**。母体本身已包含：
- Shan-Chen 伪势多相（`src/dynamics/shanChen*.h/hh`）
- 自由能多相（`src/dynamics/freeEnergy*.h/hh`）
- 热耦合（`examples/thermal/*` + `src/core/thermalUnitConverter.*`）

但**母体缺三块关键能力**，由 ThermalMultiphaseOpenLB 补上：
1. 高密度比稳定伪势（Li et al. 2012/2013 forcing scheme）
2. 相变潜热耦合（Gong & Cheng 2012; Li et al. 2017）
3. 半混合热求解器（显式 Euler + 可调热扩散率）

### 1.3 镜像停滞风险

母体镜像停在 2019，官方 openLB 已迭代到 v1.5+（2022–2024），新增：
- Meta descriptor 概念
- 均匀化 LBM（homogenised LBM）
- 验证过的壁面剪切应力函子
- C++14 强制

**对我们的含义**：本盘点基于 2019 快照，能力判断保守；若需最新特性（如 meta descriptor），须重新拉官方主线。

---

## 二、怎么做的（方法/流程/关键设置/工具链）

### 2.1 盘点路径

1. **源码获取**：`git clone` 超时，改用 `/tmp/openLB` 既有镜像（2026-07-26 快照）+ `/tmp/ThermalMultiphaseOpenLB`（2026-11-09 快照）
2. **结构扫描**：`find` + `wc -l` 统计文件数/行数；`grep` 定位关键类
3. **能力追踪**：按「多相/热/颗粒/并行/GPU」五条线读头文件与示例
4. **映射 feature_ledger**：对照 GAP-LBM-MCMP-* 缺口账本逐条判定 fits/adapt/reference/缺失

### 2.2 关键源文件定位

| 能力域 | 关键文件 | 行数 |
|---|---|---|
| Shan-Chen 伪势（多组分） | `src/dynamics/shanChenForcedPostProcessor3D.{h,hh}` | 85+206 |
| Shan-Chen 单组分 | `src/dynamics/shanChenForcedSingleComponentPostProcessor3D.{h,hh}` | 87+176 |
| Shan-Chen 动态 ω | `src/dynamics/shanChenDynOmegaForcedPostProcessor3D.{h,hh}` | 85+206 |
| 自由能多相 | `src/dynamics/freeEnergyDynamics.{h,hh}` + `freeEnergyPostProcessor3D.{h,hh}` | 120+243+231+631 |
| 热单位换算 | `src/core/thermalUnitConverter.{h,hh}` | ~200 |
| 热多相扩展（相变） | `ThermalMultiphaseOpenLB/phaseChangeExtension/phaseChangeSource/combinedShanChenThermalCouplingPostProcessor3D.h` | ~120 |
| 并行/MPI | `src/communication/mpiManager.{h,cpp}` | 8965+28665 |
| 颗粒/DEM | `src/particles/` 整目录 | ~200 文件 |

### 2.3 构建与工具链

- **编译器**：g++ / icpc / mpic++（`config.mk` 三选一）
- **标准**：C++14 强制（`-std=c++14`）
- **并行模式**：`PARALLEL_MODE := OFF | MPI | OMP | HYBRID`（四档开关）
- **构建类型**：`BUILDTYPE := precompiled | generic`（预编译加速 vs 泛型模板实例化）
- **依赖**：zlib（IO）、GSL（热多相扩展需 `-lgsl -lgslcblas`）
- **无 GPU 后端**：`find . -name "*cuda*" -o -name "*opencl*"` 返回空——**纯 CPU 实现**

---

## 三、计算结果（能力清单与证据）

### 3.1 多相能力矩阵

| 多相模型 | 实现状态 | 证据位置 | 备注 |
|---|---|---|---|
| **Shan-Chen 伪势（多组分）** | ✅ REAL | `src/dynamics/shanChenForcedPostProcessor3D.hh:40-180` | 双格子耦合，交互势 ψ(ρ) 可插拔（`AnalyticalF1D<T,T>& interactionPotential`），支持 2D/3D |
| **Shan-Chen 单组分（相分离）** | ✅ REAL | `src/dynamics/shanChenForcedSingleComponentPostProcessor3D.hh` | 用于气液相变，密度比受限于伪势形式 |
| **Shan-Chen 动态 ω** | ✅ REAL | `src/dynamics/shanChenDynOmegaForcedPostProcessor3D.hh` | 每格点可变松弛时间，改善高密度比稳定性 |
| **自由能（二组分/三组分）** | ✅ REAL | `src/dynamics/freeEnergyPostProcessor3D.hh:1-200` | 化学势耦合，支持三元（`partners.size() == 2`），双井 φ⁴ 泛函 |
| **自由能壁面/入口出口** | ✅ REAL | `src/dynamics/freeEnergyDynamics.h:60-120` | `FreeEnergyWallDynamics` / `FreeEnergyInletOutletDynamics` 专用边界 |
| **高密度比（>100:1）** | ⚠️ 扩展实现 | `ThermalMultiphaseOpenLB/phaseChangeExtension/phaseChangeSource/` | 母体原生不支持；扩展用 Li et al. 2012 forcing + MRT 达到 ~1000:1 |
| **自由面（free surface）** | ❌ 缺失 | `grep -ri "free.surface\|VOF"` 返回空 | 母体与扩展均无；需自建或借 map 空单元近似 |
| **相场 Allen-Cahn** | ❌ 缺失 | 无 AC 相关文件 | 只有守恒 CH，无非守恒 AC |
| **接触角/润湿** | ✅ REAL | `examples/multiComponent/contactAngle2d/` + `3d/` | 自由能模型自带润湿边界条件 |
| **Young-Laplace 验证** | ✅ REAL | `examples/multiComponent/youngLaplace2d/` + `3d/` | 表面张力基准算例 |
| **Rayleigh-Taylor** | ✅ REAL | `examples/multiComponent/rayleighTaylor2d/` + `3d/` | 重力驱动不稳定性 |

### 3.2 热耦合能力矩阵

| 热模型 | 实现状态 | 证据位置 | 备注 |
|---|---|---|---|
| **被动标量热输运** | ✅ REAL | `examples/thermal/porousPlate3d/` | 双分布函数（NS + AD），D3Q19 + D3Q7 |
| **自然对流（Rayleigh-Bénard）** | ✅ REAL | `examples/thermal/rayleighBenard2d/` + `3d/` | Boussinesq 近似 |
| **共轭传热（CHT）** | ❌ 缺失 | `grep -r "conjugate"` 返回空 | 母体无流固共轭；需外接或自建 |
| **相变潜热** | ⚠️ 扩展实现 | `ThermalMultiphaseOpenLB/phaseChangeExtension/phaseChangeSource/combinedShanChenThermalCouplingPostProcessor3D.h` | 通过状态方程注入潜热，非通用 CHT |
| **半混合热求解器** | ⚠️ 扩展实现 | `combinedShanChenSemiHybridThermalCouplingPostProcessor3D.h` | 显式 Euler 解热方程，可调热扩散率 |
| **热单位换算** | ✅ REAL | `src/core/thermalUnitConverter.{h,hh}` | 温度/热扩散率无量纲化 |

### 3.3 并行与性能能力

| 能力 | 实现状态 | 证据位置 | 备注 |
|---|---|---|---|
| **MPI 域分解** | ✅ REAL | `src/communication/mpiManager.{h,cpp}` | 基于 cuboid 分解，支持 2D/3D |
| **OpenMP 共享内存** | ✅ REAL | `src/communication/ompManager.{h,cpp}` | `OMPFLAGS := -fopenmp` |
| **混合 MPI+OMP** | ✅ REAL | `config.mk: PARALLEL_MODE := HYBRID` | 四档开关 |
| **负载均衡** | ✅ REAL | `src/communication/heuristicLoadBalancer.{h,hh}` + `blockLoadBalancer.{h,hh}` | 启发式 + 块级 |
| **GPU（CUDA/OpenCL）** | ❌ 缺失 | `find . -name "*cuda*" -o -name "*opencl*"` 返回空 | **纯 CPU**，无 GPU 后端 |
| **稀疏/多孔** | ✅ REAL | `examples/porousMedia/porousPoiseuille3d/` | 通过 geometry 打洞实现 |

### 3.4 颗粒与多物理场

| 能力 | 实现状态 | 证据位置 | 备注 |
|---|---|---|---|
| **Lagrangian 颗粒** | ✅ REAL | `src/particles/` 整目录 | 含接触/力/边界/双向耦合 |
| **Euler-Euler 颗粒** | ✅ REAL | `examples/particles/bifurcation3d/eulerEuler/` | 双流体模型 |
| **磁颗粒** | ✅ REAL | `examples/particles/magneticParticles3d/` | 外加磁场耦合 |
| **HLLBM（混合格子）** | ✅ REAL | `src/particles/hlbm/hlbmDynamics3D.h` | 粗细格子耦合 |

### 3.5 代码体量分布

| 子系统 | 文件数 | 估计行数 |
|---|---:|---:|
| core（格子/单元/描述子） | ~80 | ~25000 |
| dynamics（碰撞/多相/热） | 111 | ~35000 |
| boundary（边界条件） | ~60 | ~18000 |
| communication（MPI/OMP） | ~30 | ~12000 |
| functors（后处理/IO） | ~80 | ~20000 |
| geometry（几何/材料号） | ~40 | ~10000 |
| particles（颗粒/接触） | ~200 | ~50000 |
| io（VTK/VTU/Gnuplot） | ~30 | ~8000 |
| **合计** | **~868** | **~180000** |

---

## 四、结果图（能力雷达与缺口映射）

> 纯文档盘点任务，无计算结果图。以下用表格替代可视化。

### 4.1 能力成熟度雷达（定性）

```
多相 Shan-Chen     ████████████  REAL（母体+扩展）
多相自由能         ████████████  REAL
热耦合（被动标量） ████████████  REAL
热耦合（相变）     ████████░░░░  REAL（仅扩展）
自由面             ░░░░░░░░░░░░  缺失
共轭传热           ░░░░░░░░░░░░  缺失
GPU 加速           ░░░░░░░░░░░░  缺失
MPI 并行           ████████████  REAL
颗粒耦合           ████████████  REAL
测试/验证面        ██████░░░░░░  弱（示例即验证，无独立测试套件）
```

### 4.2 与 feature_ledger 映射

| feature_ledger slug | transfer | openLB 实现方式/证据 |
|---|---|---|
| `GAP-LBM-MCMP-POISEUILLE` | `reference` | 母体有 Shan-Chen 双格子耦合（`shanChenForcedPostProcessor3D.hh:40`），可用于分层 Poiseuille；但**无现成算例**，需自建验证面 |
| `MCMP 高密度比` | `adapt` | 母体原生不支持；ThermalMultiphaseOpenLB 扩展用 Li et al. 2012 forcing + MRT 达到 ~1000:1（`combinedShanChenThermalCouplingPostProcessor3D.h`）。**可吸收 forcing scheme，但需重推离散平衡** |
| `自由面（free surface）` | `缺失` | 母体与扩展均无；需自建（可借 map 空单元 + 面通量无穿透思路，参考 ludwig） |
| `相场 Allen-Cahn` | `缺失` | 只有守恒 Cahn-Hilliard（自由能模型），无非守恒 AC；需自建 |
| `GPU 融合 kernel` | `缺失` | **纯 CPU 实现**，无 GPU 后端；融合 kernel 须我们在 TensorLBM 自建 |
| `分布式多卡` | `reference` | MPI 域分解 + 负载均衡（`mpiManager.cpp` + `heuristicLoadBalancer.hh`）；可参考其 cuboid 分解与 halo 交换设计 |
| `稀疏/多孔` | `fits` | `examples/porousMedia/porousPoiseuille3d/` 通过 geometry 打洞实现；可直接映射到我们的多孔介质验证面 |
| `案例组织` | `reference` | 示例目录即案例（`examples/<category>/<case>/`），每 case 自包含参数；但**无输入文件驱动**（全硬编码 C++），不如 ludwig 的 JSON 输入灵活 |
| `测试与验证面` | `弱` | 无独立测试套件；示例即验证（Young-Laplace / Rayleigh-Taylor / Laplace 压力），但**无自动化回归**，需人工比对 |
| `CI` | `缺失` | 无 `.github/workflows/` 或 CI 配置；需我们自建 |

**统计**：fits=1、adapt=1、reference=4、缺失=4、弱=2

---

## 五、注意点（踩过的坑/局限/适用范围）

### 5.1 镜像停滞陷阱

- **陷阱**：本镜像停在 2019（v1.3-1.4），官方已迭代到 v1.5+（2022–2024）
- **影响**：新特性（meta descriptor、均匀化 LBM、验证壁面剪切）缺失
- **对策**：若需最新特性，须重新拉官方主线；本盘点结论保守，不代表官方最新能力

### 5.2 母体 vs 扩展边界模糊

- **陷阱**：ThermalMultiphaseOpenLB 是独立扩展，非母体一部分
- **影响**：说「openLB 支持相变」不准确；应说「openLB + ThermalMultiphaseOpenLB 扩展支持相变」
- **对策**：报告明确区分母体与扩展，避免误导

### 5.3 无 GPU 后端

- **陷阱**：`find . -name "*cuda*"` 返回空，但 README 提到「New meta descriptor concept」可能暗示 GPU 支持
- **影响**：openLB 纯 CPU，GPU 须用其他框架（如 TCLB、Sailfish、LUMA）
- **对策**：明确标注「无 GPU」，避免与 GPU-native 框架混淆

### 5.4 测试/验证面弱

- **陷阱**：示例目录丰富（30+ 算例），但无独立测试套件
- **影响**：无法自动化回归；示例即验证，但需人工比对
- **对策**：我们须自建验证面（解析解/实验基准），不能依赖 openLB 的示例

### 5.5 输入文件缺失

- **陷阱**：所有参数硬编码 C++，无 JSON/XML 输入文件
- **影响**：案例复用性差；每次改参数须重编译
- **对策**：我们应借鉴 ludwig 的 JSON 输入设计，避免 openLB 的硬编码模式

### 5.6 GPLv2 许可证

- **陷阱**：openLB 用 GPLv2，ThermalMultiphaseOpenLB 继承
- **影响**：衍生作品必须开源；商业使用受限
- **对策**：若我们借鉴代码，须遵守 GPLv2；或只借鉴思路不抄代码

---

## 六、误差判定（关键量 vs 基准）

> 本单为盘点任务，无计算结果，故无误差判定。

**声明**：本盘点基于源码阅读，未运行任何算例；能力判断基于代码存在性（`grep` + 头文件阅读），未验证数值精度。若需精度验证，须：
1. 编译 openLB + ThermalMultiphaseOpenLB
2. 跑 Young-Laplace / Rayleigh-Taylor / 相分离基准
3. 与文献值比对（表面张力误差 <3%？密度比稳定性？）

**honest-FAIL**：未做精度验证，无法声明「openLB 多相精度达标」。

---

## 七、可吸收 Top3 + 风险

### Top1｜Shan-Chen 伪势交互势可插拔设计（→ TensorLBM 多相模块）

**证据**：`src/dynamics/shanChenForcedPostProcessor3D.hh:40-180`  
**设计**：交互势 ψ(ρ) 通过 `AnalyticalF1D<T,T>& interactionPotential` 注入，碰撞核只依赖 ψ，不绑定具体形式  
**可吸收**：在 TensorLBM 复刻「单接口 + 多后端」，支持 Shan-Chen / 自由能 / 相场等多种伪势  
**风险**：GPLv2 许可证；其 forcing scheme 绑定 D3Q19，移植到 D3Q27 须重推离散平衡

### Top2｜自由能三元泛函 + 化学势耦合（→ TensorLBM 三组分多相）

**证据**：`src/dynamics/freeEnergyPostProcessor3D.hh:1-200`  
**设计**：支持 `partners.size() == 2`（三组分），化学势 μ 由密度差与 Laplace 项组成  
**可吸收**：直接复用三元泛函形式与化学势耦合逻辑，用于三组分相场  
**风险**：其 Laplace 离散用 18 邻居 stencil（`freeEnergyPostProcessor3D.hh:100-150`），移植到非正交格子须重推

### Top3｜MPI 域分解 + 负载均衡（→ TensorPlatform 并行面）

**证据**：`src/communication/mpiManager.cpp` + `heuristicLoadBalancer.hh`  
**设计**：cuboid 分解 + 启发式负载估计 + halo 交换  
**可吸收**：参考其 cuboid 分解与负载均衡策略，用于 TensorPlatform 的分布式调度  
**风险**：其 halo 交换基于 `BlockLattice3D`，与我们的 `TensorLBM` 数据结构不同，须适配

### 7.1 明确不可吸收 / 需自建的清单

- **自由面（free surface）**：母体与扩展均无，须自建
- **共轭传热（CHT）**：母体无，须自建
- **GPU 后端**：纯 CPU，须自建或借其他框架
- **Allen-Cahn 相场**：只有 CH，无 AC，须自建
- **自动化测试/CI**：无，须自建

---

## 八、结论与下一步

### 8.1 openLB 的定位与价值

openLB 是**历史最悠久的开源 LBM 框架之一**，代码成熟、模块化好、覆盖广（单组分/多相/热/颗粒），但**停滞在 2019**，且**纯 CPU、无 GPU、无自由面、无 CHT**。

**对我们的价值**：
1. **参考实现**：Shan-Chen / 自由能 / 热耦合的教科书级实现，可作为 TensorLBM 的对照源
2. **设计范式**：PostProcessor 耦合机制、AnalyticalF 函数对象、cuboid 域分解，可借鉴到我们的架构
3. **验证算例**：Young-Laplace / Rayleigh-Taylor / 相分离，可作为我们的验证面基准

**对我们的局限**：
1. **无 GPU**：无法满足高性能需求
2. **无自由面/CHT**：无法满足冰区航行全流程需求
3. **无测试/CI**：无法保证持续正确性
4. **GPLv2**：商业使用受限

### 8.2 下一步行动建议

1. **短期**：借鉴 openLB 的 Shan-Chen / 自由能实现，在 TensorLBM 搭建多相模块骨架
2. **中期**：自建自由面 / CHT / GPU 后端，填补 openLB 缺失
3. **长期**：建立自动化测试/CI，保证持续正确性；与官方 openLB 保持同步（若需最新特性）

### 8.3 与波 1 盘点的关系

波 1 已盘 `openLBMPM`（多相扩展分支），本单盘**母体 openLB** + `ThermalMultiphaseOpenLB`（热多相扩展）。三者关系：
- **openLB（母体）**：通用框架，含 Shan-Chen / 自由能 / 热耦合基础
- **openLBMPM**：多相扩展，增强高密度比稳定性
- **ThermalMultiphaseOpenLB**：热多相扩展，增强相变潜热耦合

**结论**：openLB 生态=母体+多扩展，本盘点覆盖母体+热扩展，波 1 覆盖多相扩展，**三者合起来构成完整的 openLB 生态画像**。

---

**[OSS-INV]**

```
[OSS-SOL]
scenario_key: oss-inventory
title: 【盘点】openLB
mission_id: 8548
inventory_target: openLB (main) + ThermalMultiphaseOpenLB
mirror_status: unofficial, ~2019 (v1.3-1.4)
code_size: 868 files, ~180k lines
multiphase_shan_chen: REAL (src/dynamics/shanChen*.h/hh)
multiphase_free_energy: REAL (src/dynamics/freeEnergy*.h/hh)
thermal_passive_scalar: REAL (examples/thermal/*)
thermal_phase_change: REAL (ThermalMultiphaseOpenLB extension only)
free_surface: MISSING
conjugate_heat_transfer: MISSING
gpu_backend: MISSING (CPU-only)
mpi_parallel: REAL (src/communication/mpiManager.*)
particles: REAL (src/particles/*)
testing: WEAK (examples as validation, no automated tests)
ci: MISSING
license: GPLv2
top1_absorb: Shan-Chen interaction potential pluggable design
top2_absorb: Free energy ternary functional + chemical potential coupling
top3_absorb: MPI domain decomposition + load balancing
missing_self_build: free surface, CHT, GPU, Allen-Cahn, automated tests/CI
```
