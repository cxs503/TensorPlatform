# OSS-SPH 深盘波2·集团军 — luchete80/WeldForm 深度盘点报告

> **scenario_key**: oss-sph-w2  
> **mission_id**: 8683  
> **盘点日期**: 2026-11-09  
> **盘点人**: agent/m8683  
> **目标仓**: luchete80/WeldForm (tier3)  
> **产物**: `artifacts/oss-sph-w2/weldform_deepdive.json`（机器可解析）

---

## 一、案例介绍（物理背景 / 几何 / 参数表）

### 1.1 任务背景

本单为 OSS-SPH 深盘波2·集团军任务，对标 LBM 盘点协议，对 **luchete80/WeldForm**（tier3，焊接/成型 SPH 工业应用）进行源码级深度盘点。该仓在 wave-0 普查（m8638）中已初判为「WCSPH 弹塑性+热耦合：焊接仿真，并行」，21 stars，C++，GPL-3.0。

### 1.2 仓库元数据（GitHub API 实查，禁凭记忆）

| 维度 | 值 |
|---|---|
| **全名** | luchete80/WeldForm |
| **描述** | Weakly-Compressible Smoothed Particle Hydrodynamics Parallel Solver for Elasto Plastic and thermal coupled Solid Mechanics |
| **语言** | C++ |
| **License** | GPL-3.0 (spdx_id: GPL-3.0) |
| **Stars** | 21 |
| **Forks** | 6 |
| **创建** | 2020-02-12 |
| **最近推送** | 2025-03-18 |
| **归档** | false |
| **大小** | 4402 KB |
| **默认分支** | master |
| **Open Issues** | 220 |
| **Homepage** | sourceforge.net/projects/weldform/ |
| **Topics** | deformable-model, deformable-solids, mpi, plasticity, scientific-computing, simulation, SPH, solid-mechanics |

### 1.3 依赖与构建

- **依赖库**：BLITZ++ 0.9（非最新版）、HDF5、GSL、LAPACK（非必需）
- **构建**：CMake（`CMakeLists.txt`），支持 GCC/Clang/MSVC
- **相关仓**：WeldFormGPU（GPU 版）、WeldFormFEM（显式 FEM + AMR/ALE）
- **基础**：PersianSPH（mghkorzani/persiansph）+ Kirk Fraser 的 FSW（Friction Stir Welding）SPH 模型论文

---

## 二、怎么做的（方法 / 流程 / 关键设置 / 工具链）

### 2.1 方法

**GitHub API 实查**（认证 PAT，非匿名）：
1. `GET /repos/luchete80/WeldForm` — 仓库元数据（stars/license/language/pushed_at）
2. `GET /repos/luchete80/WeldForm/contents/` — 根目录结构
3. `GET /repos/luchete80/WeldForm/contents/{Source,Modules,Tests,examples,scripts,Docs}` — 各子目录结构
4. `GET /repos/luchete80/WeldForm/contents/{README.md,CMakeLists.txt,Source/*.cpp,Source/*.h,examples/*.cpp}` — 源码内容（base64 解码）

### 2.2 源码级方法清单（逐条 文件:行 定位）

#### SPH 变体
| 方法 | 定位 | 说明 |
|---|---|---|
| **WCSPH**（弱可压） | `Source/Interaction.cpp:25-338` (CalcForce2233)；`Source/Domain.cpp:1599-1698` (PrimaryComputeAcceleration) | 密度-压力 EOS（PresEq/Cs/P0），标准 WCSPH 动量方程 |
| **XSPH**（Monaghan 速度修正） | `Source/Interaction.cpp:182-191` | `VXSPH += XSPH*mj/(0.5*(di+dj))*K*-vij` |
| **人工黏度**（Monaghan 型） | `Source/Interaction.cpp:68-81` | `PIij = (Alpha*Cij*MUij+Beta*MUij*MUij)/(0.5*(di+dj))*I` |
| **拉伸不稳定性修正** | `Source/Interaction.cpp:93-98` | `TIij = pow((K/K0),n)*(TIR)` |
| **梯度核修正**（Bonet） | `Source/Interaction.cpp:200-225`；`Source/Particle.h:144` | `gradCorrM` 矩阵 |
| **Shepard 密度滤波** | `Source/Interaction.cpp:284-286`；`Source/Particle.h:54-58` | `SumDen += mj*K` |

#### 核函数（4 种）
| 核 | 定位 | 说明 |
|---|---|---|
| **Cubic Spline** | `Source/Functions.cpp:30-37` | 标准三次样条 |
| **Quintic** | `Source/Functions.cpp:39-44` | 五次 |
| **Quintic Spline** | `Source/Functions.cpp:46-53` | 五次样条 |
| **Hyperbolic Spline**（Fraser） | `Source/Functions.cpp:55-61` | Fraser Eq 3-27，FSW 专用 |
| **GradKernel** | `Source/Functions.cpp:74-124` | 梯度核 |
| **LaplaceKernel** | `Source/Functions.cpp:126-164` | 拉普拉斯核（热传导） |

#### 时间步控制（5 种积分器 + 自适应）
| 方法 | 定位 | 说明 |
|---|---|---|
| **Leapfrog**（Randles & Libersky 1996） | `Source/SolverLeapfrog.cpp` | 密度从 t+1/2 速度计算 |
| **KickDrift** | `Source/SolverKickDrift.cpp` | |
| **Verlet** | `Source/SolverVerlet.cpp` | |
| **ModEuler** | `Source/Domain.h:202` | |
| **Fraser** | `Source/SolverFraser.cpp` | FSW 专用 |
| **自适应时间步** | `Source/Domain.cpp:1719-1748` | `deltatmin = sqrt_h_a*sqrt(h/|a|)` |

#### 边界处理
| 方法 | 定位 | 说明 |
|---|---|---|
| **Ghost 粒子** | `Source/Domain.cpp:1142-1147` (MoveGhost)；`Source/Particle.h:45` | Symmetric/Periodic/Mirror_XYZ |
| **NoSlip BC** | `Source/Interaction.cpp:100`；`Source/Particle.h:64` | 固定粒子速度平滑 |
| **固定粒子** | `Source/Domain.cpp:1643-1697` | 压力/应力/速度平滑 |
| **轴对称** | `Source/Domain.h:176` (ApplyAxiSymmBC) | |
| **进出流** | `Source/InOutFlow.cpp`；`Source/Domain.cpp:60-72` | InFlowCon/OutFlowCon/AllFlowCon |
| **Randles & Libersky** | `Source/Particle.h:90` (Bmat) | 边界修正矩阵 |

#### 接触（5 种算法）
| 方法 | 定位 | 说明 |
|---|---|---|
| **Fraser** | `Source/Contact.cpp:223-514` | Fraser 3-137 至 3-167 |
| **Wang** | `Source/Contact_Wang.cpp` | |
| **Seo** | `Source/Contact.cpp:562-771` | |
| **Zhan** | `Source/Contact_Zhan.cpp` | |
| **LS-Dyna** | `Source/Contact_LS.cpp` | |

#### 热-力耦合
| 方法 | 定位 | 说明 |
|---|---|---|
| **温度增量** | `Source/Thermal.cpp:53` (CalcTempInc) | SPH 热扩散 |
| **对流** | `Source/Thermal.cpp:339` (CalcConvHeat) | |
| **热求解** | `Source/Thermal.cpp:567` (ThermalSolve) | |
| **耦合热-力** | `Source/Thermal.cpp:721` (ThermalCalcs)；`Source/Thermal.cpp:750` | |
| **塑性功热** | `Source/Domain.h:221` (CalcPlasticWorkHeat) | |
| **热膨胀** | `Source/Domain.h:220` (CalcThermalExpStrainRate) | |

#### 材料模型（4 种）
| 模型 | 定位 | 说明 |
|---|---|---|
| **Bilinear** | `Source/Particle.h:34` | |
| **Hollomon**（幂律） | `Source/Particle.h:35` | |
| **Johnson-Cook** | `Source/Particle.h:36`；`Source/Material.h:117-119` | A/B/C 系数 |
| **GMT** | `Source/Particle.h:37` | |
| **损伤模型** | `Source/Material.h:15-62` | RankineDamage, JohnsonCookDamage (D1-D5) |

#### 多相/自由面
| 能力 | 定位 | 说明 |
|---|---|---|
| **自由面** | `Source/Domain.cpp:91` (CalculateSurface)；`examples/FreeSurface.cpp` | 表面检测 |
| **多相** | **NOT_SUPPORTED** | README 明确「exclusively adapted to solid mechanics」 |

#### GPU/MPI 并行
| 能力 | 定位 | 说明 |
|---|---|---|
| **OpenMP** | `Source/Domain.cpp:122-123`；`Source/Interaction.cpp:249-327` | `#pragma omp parallel for` + `omp_set_lock` |
| **Nishimura 非锁归约** | `Source/Domain.cpp:159`；`Source/Domain.h:168-170` | AccelReduction/RateTensorsReduction/DensReduction |
| **MPI** | **NOT_SUPPORTED**（本仓） | topics 含 mpi 但无 MPI 代码；GPU 版在独立仓 WeldFormGPU |

---

## 三、验证面盘点

### 3.1 测试框架
- **无正式测试框架**（无 pytest/CTest）。测试为独立 .cpp 示例程序，经 CMake 编译。
- **Tests/ 目录**：`Cmopression/`、`FSW_contact/`、`NastranReader/`、`Neighbour/`、`omp/` — 各含独立 .cpp。

### 3.2 自带算例/基准
| 算例 | 文件 | 说明 |
|---|---|---|
| **Poiseuille** | `examples/1-Poiseuille.cpp` | 2D 泊肃叶流，Quintic_Spline，Takeda 黏度，NoSlip 壁 |
| **Couette** | `examples/2-Couette.cpp` | 2D 库埃特流，Quintic_Spline，Takeda 黏度 |
| **DamBreak** | `examples/3-DamBreak.cpp`、`4-DamBreak3D.cpp` | 溃坝 |
| **Cantilever** | `examples/Cantilever.cpp`、`Cantilever2.cpp` | 悬臂梁 |
| **Compression** | `examples/Compression*.cpp`（20+ 变体） | 压缩成型（2d/1010/half_ghost/TRIP/XSPH/AVisc/NoSlip/Free/SmallD_CNS） |
| **Plate-Yield** | `examples/Plate-Yield*.cpp`（10+ 变体） | 板屈服（CNS/Hard/cubic/quintic/3d） |
| **FSW** | `examples/FSW_red.cpp`、`FSW_thin.cpp`；`Tests/FSW_contact/FSW.cpp` | 搅拌摩擦焊 |
| **Impact** | `examples/Impact_Campos.cpp`、`Impact_Seo2006.cpp`、`Impact_Zhang2017.cpp` | 冲击 |
| **Thermal** | `examples/Heat_2D.cpp`、`Heat_Cyl_3D.cpp`、`SolidBlock_Heat*.cpp`、`Thermal_Expansion_1/2.cpp` | 热传导/热膨胀 |
| **Traction** | `examples/Traction*.cpp`（Hollomon/JC） | 拉伸 |

### 3.3 量化验证数据
- **无公开量化验证数据**：仓内无参考值对比表（无实验/解析解对标数字）。
- **BENCHMARKS.txt**（`examples/BENCHMARKS.txt`）：仅 2 条（Compression_quarter、Compression_qt_vz_nosym），无数字参考。
- **验证为定性**：通过可视化输出（compression.PNG、met_form.png）展示。

---

## 四、可迁移方案（≥2 条）

### 方案 1：SPHSOL-weldform-01（reference）
- **kind**: reference
- **summary**: WeldForm 的 WCSPH 固体力学公式（CalcForce2233：人工黏度+拉伸不稳定性+XSPH+梯度核修正）是 TensorSPH 固体力学验证面的参考实现。Fraser 接触公式（Contact.cpp:223-514）和热-塑性耦合（Thermal.cpp:721-750）提供完整的 FSW/金属成型热-力 SPH 管线。
- **gap**: GAP-SPH-WCSPH-VALIDATION
- **license**: GPL-3.0 — **reference-only**（不可直接适配进 MIT/Apache 代码库，license 冲突）

### 方案 2：SPHSOL-weldform-02（fit）
- **kind**: fit
- **summary**: 核函数族（Cubic/Quintic/Quintic-Spline/Hyperbolic-Spline）和「塑性应变阈值触发自适应邻居搜索」策略（SolverFraser.cpp:171-180）可直接移植到 TensorSPH 固体力学模块。Nishimura 非锁归约（Domain.h:168-170）是 OpenMP SPH 可复用的并行求和模式。
- **gap**: GAP-SPH-GPU-SCALING
- **license**: GPL-3.0 — **fit 需 GPL 兼容集成**或重实现算法（算法本身不受版权保护，仅代码受保护）

---

## 五、注意点（踩坑 / 局限 / 适用范围）

### 5.1 局限
1. **无量化验证**：仓内无实验/解析解对标数据，无法验证精度（>15% 误差标准无法判定）。这是该仓的已知局限。
2. **多相不支持**：README 明确「exclusively adapted to solid mechanics」，无多相界面能力。
3. **MPI/GPU 不在本仓**：topics 含 mpi 但无 MPI 代码；GPU 版在独立仓 WeldFormGPU。
4. **代码质量**：220 个 open issues，部分文件标注「_NEW_WRONG」（如 SolverKickDrift_NEW_WRONG.cpp），代码成熟度存疑。
5. **依赖复杂**：BLITZ++ 0.9（非最新版）+ HDF5 + GSL，构建门槛高。

### 5.2 适用范围
- **适用**：固体力学（金属成型/焊接/压缩/板屈服）WCSPH 参考；热-力耦合 SPH 管线；接触算法（Fraser/Wang/Seo/Zhan）。
- **不适用**：多相流体、GPU 扩展、MPI 大规模并行（需转 WeldFormGPU 或其它仓）。

---

## 六、误差判定

本单为**源码级深度盘点**（非精度验证），无计算结果需对标基准。误差判定不适用。

**诚实声明**：
- ✅ 完成：GitHub API 实查（元数据+目录+源码）+ 全量方法清单（SPH 变体/核/时间步/边界/接触/热-力/材料/并行）+ 验证面盘点 + 2 条可迁移方案 + 机器可解析 JSON
- ⚠️ 未完成：未 clone 编译运行（tier3 小仓快速盘，API contents 足够）；无量化验证数据（仓内无）
- ⚠️ 未覆盖：WeldFormGPU（GPU 版）、WeldFormFEM（FEM 版）未深盘（属其它仓）

---

## 七、机器块

[OSS-INV]{"campaign":"osssph-w2","repo":"luchete80/WeldForm","tier":3,"methods":["WCSPH","XSPH","artificial-viscosity","tensile-instability","gradient-kernel-correction","shepard-filter","cubic-spline","quintic","quintic-spline","hyperbolic-spline","leapfrog","kick-drift","verlet","mod-euler","fraser","adaptive-dt","ghost-particles","no-slip-bc","fixed-particles","axi-symm","in-out-flow","randles-libersky","bonet-grad-corr","fraser-contact","wang-contact","seo-contact","zhan-contact","ls-dyna-contact","thermal-solve","coupled-thermomech","plastic-work-heat","thermal-expansion","bilinear","hollomon","johnson-cook","gmt","damage-models","openmp","nishimura-reduction"],"boundary":"ghost-particles+no-slip+fixed+axi-symm+in-out-flow+randles-libersky+bonet-grad-corr","gpu":"NOT_SUPPORTED (separate WeldFormGPU repo)","validation":"no-quantitative-data (qualitative via PNG output); Poiseuille/Couette/DamBreak/Cantilever/Compression/Plate-Yield/FSW/Impact/Thermal/Traction examples","license":"GPL-3.0","transferable":["SPHSOL-weldform-01","SPHSOL-weldform-02"]}[/OSS-INV]

[OSS-SOL]{"repo":"luchete80/WeldForm","id":"SPHSOL-weldform-01","kind":"reference","summary":"WCSPH solid-mechanics formulation (CalcForce2233: artificial viscosity+tensile instability+XSPH+gradient kernel correction) as reference for TensorSPH solid-mechanics validation face; Fraser contact + thermal-plastic coupling provide complete FSW/metal-forming thermo-mechanical SPH pipeline","gap":"GAP-SPH-WCSPH-VALIDATION"}[/OSS-SOL]

[OSS-SOL]{"repo":"luchete80/WeldForm","id":"SPHSOL-weldform-02","kind":"fit","summary":"Kernel family (Cubic/Quintic/Quintic-Spline/Hyperbolic-Spline) + plastic-strain-threshold adaptive neighbour-search strategy (SolverFraser.cpp:171-180) portable to TensorSPH solid-mechanics module; Nishimura non-locking reduction (Domain.h:168-170) as reusable OpenMP parallel-summation pattern","gap":"GAP-SPH-GPU-SCALING"}[/OSS-SOL]

---

**产物清单**：
- `artifacts/oss-sph-w2/weldform_deepdive.json` — 机器可解析深度盘点
- `docs/oss_inventory_sph/REPORT_oss-sph-w2-weldform.md` — 本报告
