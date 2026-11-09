# OSS-SPH 深盘波2 · DualSPHysics/DualSPHysics 源码级盘点（m8651）

- 战役: osssph-w2 · 目标仓: DualSPHysics/DualSPHysics · tier 1
- 调查方式: GitHub REST API 实查（禁凭记忆）+ wiki git clone + code search；raw.githubusercontent.com 被网络策略拦截，全部文件经 API contents 端点（base64）取回
- 调查基线: master @ ef3721a861（2025-04-16，"Update pre- and post-processing tools"）

## ① 案例介绍（物理背景/几何/参数表）

DualSPHysics 是西班牙 EPHYSLAB（Vigo 大学）与曼彻斯特大学开发的 C++/CUDA/OpenMP 弱可压缩 SPH（WCSPH）求解器，面向海岸/海洋工程自由面问题（溃坝冲击、波浪-结构物、多相非牛顿流）。前身 SPHysics（2017 建仓），v5.4（2025-03-20）为当前版本。

仓库事实（GitHub API 实查，2026-11-09）：

| 项 | 值 |
|---|---|
| 语言 | C++（含 CUDA .cu） |
| License | **LGPL-2.1**（LICENSE 文件 + API license.spdx_id 双证） |
| Stars / Forks / Open issues | 759 / 262 / 189 |
| 创建 / 最近推送 | 2017-11-22 / 2025-04-16T09:41:48Z |
| 默认分支 / 归档 | master / 否 |
| 仓库体积 | ~1.5 GB（含 bin/ 预编译二进制） |
| CI | **无**（根目录无 .github，404 实查） |
| 测试框架 | **无**（无 tests/ 目录，code search "path:tests" = 0 命中） |
| 目录 | src/source（主求解器 ~180 文件）、src_mphase/DSPH_v5.0_NNewtonian（多相非牛顿分支）、examples/main/01_DamBreak（仓库内唯一算例）、doc/help、doc/xml_format、bin/ |

## ② 怎么做的（方法/流程/关键设置/工具链）

1. `GET /repos/DualSPHysics/DualSPHysics` → 语言/license/星数/时间戳；
2. `GET /contents/{src,src/source,src_mphase,examples,doc}` 逐层展开目录；
3. `GET /commits?per_page=8` → 最近提交（2025-04-08~16，CUDA 12.8 升级、CMake 化、VRes）；
4. code search（CFL / Tait / MPI_Init / mpi.h / PHASECTE / MDBC2_NoPen / STEP_Symplectic）定位机制；
5. 关键文件经 API contents 取回通读：JSphGpu_ker.cu(3721 行)、JSphCpu.cpp(3046 行)、JSphGpuSingle.cpp(1253 行)、DualSphDef.h(705 行)、JSphGpu_mdbc_iker.cu(619 行)、JSphGpu_NN_ker.cu(1555 行)、FunSphEos_iker.h、FunSphKernel_iker.h、JViscosityDef.h、JPeriodicDef.h、JTimeControl.cpp、CaseDambreak_Def.xml；
6. wiki（DualSPHysics.wiki.git）clone 读 7.-Testcases.md 与 3.-SPH-formulation.md。

## ③ 计算结果（数字表，全部 API/源码溯源，零手抄）

### 3.1 SPH 变体与方法清单（逐条 文件:行）

| 能力 | 结论 | 定位（master@ef3721a861） |
|---|---|---|
| WCSPH 状态方程 | Tait 型 `p=b*((ρ/ρ0)^γ−1)`，γ=7（水），cs≥10·vmax 准则 | src/source/FunSphEos_iker.h:38,45-46；examples/main/01_DamBreak/CaseDambreak_Def.xml:9 |
| 核函数 | Cubic Spline（含 tensile 修正）+ Wendland | src/source/FunSphKernel_iker.h:29-231；枚举 DualSphDef.h:442-446 |
| 时间积分 | Symplectic Position Verlet（predictor-corrector，二阶、时间可逆）+ Verlet 备选 | DualSphDef.h:435-439；JSphGpuSingle.cpp:785-825（ComputeStep_Sym 全流程）；wiki 3.7.2（Leimkuhler & Matthews 2016） |
| 时间步控制 | dt=CFL·min(dt1 力, dt2 Courant+黏, dt3 FlexStruc, dt4 divclean)；DtMin 下限；DtFixed/DtFixedFile 覆盖；CFL∈[0.001,1] | JSphCpu.cpp:2036-2080（DtVariable）；JCaseCtes.h:118,195-198 |
| 密度扩散项 | DDT（Molteni & Colagrossi 2009）/ DDT2（Fourtakas et al 2019）/ DDT2Full | DualSphDef.h:486-491；JSphGpu_ker.cu:659-672 |
| 黏度 | Artificial（Monaghan π）/ Laminar / Laminar+SPS（Smagorinsky Cs=0.12, CI=0.0066，Gotoh 2001 / Dalrymple & Rogers 2006） | JViscosityDef.h:25-30；JSphGpu_ker.cu:734-749；wiki 3.2.2 |
| 边界处理 | DBC → mDBC → **mDBC2**（Std / NoPen 无穿透；No-slip / Free-slip 滑移模态；边界压力克隆+切向速度修正） | DualSphDef.h:452-471；JSphGpu_mdbc_iker.cu:34,224,275,290-462 |
| 周期边界 | X/Y/Z/XY/XZ/YZ 任意组合，Verlet/Symplectic 双份镜像粒子 | JPeriodicDef.h:34-43；JSphGpu_ker.cu:2142-2406 |
| 粒子 shifting | 5 模态（None/NoBound/NoFixed/Full/FS 自由面感知），zone 化配置，Lind 2012 扩散系数法 | DualSphDef.h:494-501；JSphShifting.cpp:44-205；wiki 3.6 |
| XSPH | Eps 常数存在（XSPH 变体开关） | JCaseCtes.h:133 |
| 浮体/FSI | FTMODE_Sph（SPH 交互）/ FTMODE_Ext（DEM 或 Chrono 多体）；DEM 碰撞；Chrono 库耦合 | DualSphDef.h:543-566；JSphGpu_ker.cu:1213-1385（DEM 交互核） |
| 波浪生成 | 多层活塞 1D/2D（JMLPistons）、二阶波理论（JWaveOrder2_ker.cu）、波谱（JWaveSpectrumGpu） | src/source/JMLPistons.h:63-88；JSphGpu_ker.cu:1808-1900 |
| 入流/出流 | 流量/圆射流/旋转入流、mesh 数据驱动（v5.4 改进） | JSphInOut.h:68-102；CHANGES.txt v5.4 |
| 阻尼/松弛区 | 平面/盒/圆柱阻尼区；RelaxZone 造波 | JSphGpu_ker.cu:2445-2680；JRelaxZonesGpu.cpp |
| 变分辨率 | VRes（v5.4 新特性，-vres 开关，插值阶数 0-2） | doc/help/DualSPHysics5.4_Help.out:153-156；JSphVRes.cpp |
| 多相 | **最多 10 相**，每相独立 ρ/b/γ/cs/mass/黏度；每相 Tait EOS | src_mphase/DSPH_v5.0_NNewtonian/source/JSphGpu_NN_ker.cu:25-28,488-523 |
| 非牛顿流变 | **Herschel-Bulkley-Papanastasiou（HBP）** 正则化：τ_yield、τ_max、Bi_multi、m_NN/n_NN；Bingham 区判定；FDA + Morris 黏性算子 | JSphGpu_NN_ker.cu:308-326（KerGetEta_Effective）；wiki 7.9 |
| 自由面 | 无显式表面追踪：密度判据 + FS 模态 shifting + 表面粒子检测（FS typecode） | JSphGpu_ker.cu:2408（KerIsNormalFluid）；DualSphDef.h:499（SHIFT_FS） |
| ISPH/PCISPH/DFSPH/IISPH/δ-SPH/PBD | **均无**（无压力 Poisson 求解、无投影步；δ-SPH 仅 wiki 算例提及"Delta-SPH is also used"，源码未见独立 δ-SPH 实现） | 全库 code search 无 Poisson/pressure-projection 命中 |
| 可微分 | 无（纯 CUDA/OpenMP，无 autodiff） | 全库无 jax/torch/autograd 命中 |

### 3.2 并行策略

| 维度 | 结论 | 定位 |
|---|---|---|
| GPU | 单 GPU CUDA（多 GPU 选择 GpuId，单卡内 CUDA streams 用于浮体） | JSphGpuSingle.cpp:955-957；CHANGES.txt v5.4 |
| CPU | OpenMP 线程池（OmpThreads/OMP_STRIDE 分片归约） | JSphCpu.cpp:702,1137,1243-1245；OmpDefs.h |
| **MPI** | **不存在**（`MPI_Init`、`mpi.h` code search 均 0 命中）——**修正 m8638 普查"含 MPI"初判** | 实查 2026-11-09 |
| 邻居搜索 | 均匀网格 cell division + radix sort（GPU/CPU 双实现） | JCellDivGpu_ker.cu；JRadixSort.cpp |

### 3.3 验证面盘点

| 项 | 结论 |
|---|---|
| 仓库内算例 | 仅 1 个：examples/main/01_DamBreak（2D 溃坝 + Koshizuka & Oka 1996 实验数据 15 点，t=0.092–0.751 s，x_tip=1.128–4.132 m；3D 溃坝-结构物） |
| 仓库内量化验证数据 | 仅上述 1 个 EXP 文件（15 数据点）；无 Poiseuille/Taylor-Green 数值结果文件 |
| 测试框架/CI | 无（无 .github、无 pytest/gtest、无 CI 配置） |
| wiki 算例（包内，非仓库） | 17 主算例 + mDBC 系列 + 多相液气 + NN 系列：溃坝(K&O 1996)、SPHERIC #10 晃荡压力、CIEMito 实验、Fekken 2004 / Moyo & Greenhow 2000 落球、Hadzic 2005 / Ren 2015 浮箱运动、Re=200 绕柱阻力（Tafuni 2018）、Morison 方程波浪绕柱、Poiseuille（Newtonian+Bingham 两相）、Rushton 搅拌、English et al 2019 压力 |
| 量化验证数据 | 仓库内仅 K&O 1996 溃坝前锋 15 点；其余验证数据在官方包（dual.sphysics.org 下载）与 wiki 图（imgur 外链，非仓库资产） |

## ④ 结果图

图 1：仓库内唯一量化验证锚——Koshizuka & Oka (1996) 溃坝前锋实验数据（examples/main/01_DamBreak/EXP_X-DamTipPosition_Koshizula&Oka1996.txt，15 点，程序化读取绘图，零手抄）。

**无场量云图/比较曲线的理由**：本单为源码级调查（不运行求解器），未产生任何模拟场量；仓库本身也不含数值结果文件，故无云图与"数值 vs 基准"曲线可画，如实声明。

## ⑤ 注意点（坑/局限/适用范围）

1. **m8638 初判"含 MPI"错误**：实查无 MPI 任何痕迹，并行=单 GPU CUDA + CPU OpenMP；多卡/多节点扩展性需自行评估（对 GAP-SPH-GPU-SCALING 是负面证据：无现成 MPI 面可借）。
2. 仓库 ≠ 完整产品：GenCase 前处理、MeasureTool 后处理、多数算例在官方二进制包（dual.sphysics.org），仓库只含求解器 + 1 个算例；引用其"验证面"时须区分仓库资产 vs 包资产。
3. 无 CI/无测试：任何 adapt 移植必须自带回归测试，不能依赖上游质量门。
4. LGPL-2.1：代码级 adapt 须文件级保留 LGPL 头、保留 LICENSE、允许反向工程调试条款；算法级 fit（只搬公式不搬码）无传染问题。
5. 多相 NN 分支（src_mphase）停留在 v5.0 基线，与主分支 v5.4 有版本差，移植时注意 API 漂移。
6. 2D 模拟=3D 退化（Y 向 pointmin=pointmax），非原生 2D 优化路径。

## ⑥ 误差判定

本单为调查任务，未运行求解器，**无"关键量 vs 基准"误差可报**（声明原因：任务边界=盘点，非复算）。唯一量化锚 K&O 1996 数据已原样入库（图 1），供后续 GAP-SPH-WCSPH-VALIDATION 实施单做跨代码对比时直接取用。是否 <3%：N/A（未复算，不粉饰）。

## 机器块

[OSS-INV]{"campaign":"osssph-w2","repo":"DualSPHysics/DualSPHysics","tier":1,"methods":["WCSPH(Tait EOS gamma=7, FunSphEos_iker.h:38)","kernels: CubicSpline+tensile / Wendland (FunSphKernel_iker.h:29-231)","time-integration: Symplectic Position Verlet predictor-corrector + Verlet (DualSphDef.h:435-439, JSphGpuSingle.cpp:785-825)","CFL dt 4-constraint (JSphCpu.cpp:2036-2080)","DDT/DDT2 density diffusion Molteni2009/Fourtakas2019 (DualSphDef.h:486-491)","viscosity: Artificial/Laminar/Laminar+SPS (JViscosityDef.h:25-30)","shifting 5 modes incl FS (DualSphDef.h:494-501)","XSPH eps (JCaseCtes.h:133)","multiphase up to 10 phases per-phase Tait (JSphGpu_NN_ker.cu:25-28)","Herschel-Bulkley-Papanastasiou non-Newtonian (JSphGpu_NN_ker.cu:308-326)","DEM/Chrono FSI (DualSphDef.h:543-566)","wave gen: MLPistons 1D/2D + 2nd-order theory + spectrum","inlet/outlet mesh-driven (JSphInOut.h:68-102)","variable resolution VRes v5.4","NO ISPH/PCISPH/DFSPH/IISPH/delta-SPH/PBD (no pressure Poisson)","NO differentiable path"],"boundary":"DBC/mDBC/mDBC2(Std,NoPen) + NoSlip/FreeSlip slip modes (DualSphDef.h:452-471, JSphGpu_mdbc_iker.cu:34-462); periodic X/Y/Z/XY/XZ/YZ (JPeriodicDef.h:34-43); inlet/outlet mesh-driven (JSphInOut.h:68-102)","gpu":"single-GPU CUDA (JSphGpu_ker.cu 3721 lines) + CPU OpenMP; NO MPI (MPI_Init/mpi.h search=0 hits, corrects m8638); neighbor search=cell division+radix sort","validation":"repo: only 01_DamBreak with Koshizuka&Oka 1996 exp data (15 pts); NO CI, NO test framework; wiki/package: 17 main cases + mDBC + multiphase NN (Poiseuille, SPHERIC#10, CIEMito, Fekken2004, Hadzic2005, Ren2015, Tafuni2018 Re=200, Morison)","license":"LGPL-2.1","transferable":["SPHSOL-dualsphysics-01 adapt mDBC2 boundary","SPHSOL-dualsphysics-02 fit DDT/DDT2 density diffusion","SPHSOL-dualsphysics-03 reference K&O1996 dam-break dataset","SPHSOL-dualsphysics-04 reference HBP multiphase NN kernels","SPHSOL-dualsphysics-05 fit symplectic-Verlet + 4-constraint CFL dt","SPHSOL-dualsphysics-06 reference single-GPU CUDA scaling baseline (no MPI)"]}[/OSS-INV]

[OSS-SOL]{"repo":"DualSPHysics/DualSPHysics","id":"SPHSOL-dualsphysics-01","kind":"adapt","summary":"mDBC2 边界（Std/NoPen + NoSlip/FreeSlip 滑移模态，边界压力克隆+切向速度修正，JSphGpu_mdbc_iker.cu:34-462）文件级移植进 TensorPhysics SPH 线：LGPL-2.1 合规判定=文件级保留（保留 LICENSE 头/允许反向工程条款/动态链接或提供目标文件），不传染其余代码；上游无 CI 无测试，移植必须自带 Poiseuille 壁面速度剖面回归","gap":"GAP-SPH-WCSPH-VALIDATION"}[/OSS-SOL]
[OSS-SOL]{"repo":"DualSPHysics/DualSPHysics","id":"SPHSOL-dualsphysics-02","kind":"fit","summary":"DDT（Molteni & Colagrossi 2009）与 DDT2（Fourtakas et al 2019）密度扩散项公式级移植（DualSphDef.h:486-491，JSphGpu_ker.cu:659-672）：只搬公式不搬码，无 license 传染；用于 WCSPH 密度漂移/压力振荡抑制，验收绑 Poiseuille+溃坝密度误差","gap":"GAP-SPH-WCSPH-VALIDATION"}[/OSS-SOL]
[OSS-SOL]{"repo":"DualSPHysics/DualSPHysics","id":"SPHSOL-dualsphysics-03","kind":"reference","summary":"Koshizuka & Oka (1996) 2D 溃坝前锋实验数据（仓库内 examples/main/01_DamBreak/EXP_X-DamTipPosition_Koshizula&Oka1996.txt，15 点 t=0.092-0.751s）作为跨代码 dam-break 对比的公共锚；配合 wiki 记载的 SPHERIC #10 晃荡压力锚，构成 GAP-SPH-WCSPH-VALIDATION 实施单的基准集","gap":"GAP-SPH-WCSPH-VALIDATION"}[/OSS-SOL]
[OSS-SOL]{"repo":"DualSPHysics/DualSPHysics","id":"SPHSOL-dualsphysics-04","kind":"reference","summary":"Herschel-Bulkley-Papanastasiou 正则化非牛顿多相实现（≤10 相、每相 Tait EOS、FDA+Morris 黏性算子，JSphGpu_NN_ker.cu:308-326,488-583）作为多相/非牛顿 SPH 的参考实现与 Poiseuille 两相（Bingham+Newtonian）算例参照；注意 src_mphase 停在 v5.0 基线，与主分支 v5.4 有 API 漂移","gap":"GAP-SPH-MULTIPHASE"}[/OSS-SOL]
[OSS-SOL]{"repo":"DualSPHysics/DualSPHysics","id":"SPHSOL-dualsphysics-05","kind":"fit","summary":"Symplectic Position Verlet predictor-corrector（JSphGpuSingle.cpp:785-825）+ 四约束 CFL 时间步（dt1 力/dt2 Courant+黏/dt3 结构/dt4 divclean，JSphCpu.cpp:2036-2080）公式级移植：为 ISPH/PCISPH 收敛研究提供 WCSPH 侧时间步敏感性基线（CFL 0.001-1 扫描协议可直接复用）","gap":"GAP-SPH-ISPH-CONVERGENCE"}[/OSS-SOL]
[OSS-SOL]{"repo":"DualSPHysics/DualSPHysics","id":"SPHSOL-dualsphysics-06","kind":"reference","summary":"单 GPU CUDA + CPU OpenMP 并行架构（无 MPI，实查修正 m8638 初判）作为 GPU 扩展性基准的参照系：其 cell-division+radix-sort 邻居搜索与单卡内存模型可作 10^6-10^7 粒子单卡基线；多卡/MPI 面缺失=TensorPhysics 若做 MPI 分区无现成 OSS 可借，需自研","gap":"GAP-SPH-GPU-SCALING"}[/OSS-SOL]
