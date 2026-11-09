# 【盘点】omalaspinas/palabos — 通用 LBM/HPC 求解器代码级技术盘点

- 锚点：FEAT-OSS-INVENTORY-LBM（mission 8300，scenario_key=oss-inventory）
- 仓：`omalaspinas/palabos`（Palabos 官方 GitHub 镜像；真源 gitlab.com/unigespc/palabos）
- 检出：`git clone --depth 1` → HEAD `8f8ecd277204f9d24019834068906128565c9346`，提交 2026-01-23 10:00:31 +0000；62 MB；`src/` 944 文件 / 364,329 行
- 证据纪律：**全部结论 source-confirmed（源码直读）**。108 条 `文件:行` 指针由 `artifacts/oss_inventory_palabos/verify_pointers.py` 机器校验（该行必须含期望 token，**108/108 通过**），5 条「零命中」断言用 `grep -rInE` 复跑。**未编译、未运行 Palabos**（364 kLOC），故报告不含数值精度结论；凡涉「密度比上限 / 精度」处一律标注「代码机制可判，数值未实测」。

---

## 一、概况

| 项 | 值 | 证据 |
|---|---|---|
| 语言 | C++（README 要求 C++20 编译器） | `README.md`「A compatible C++ 20 compiler」；GitHub API `language=C++` |
| 构建 | CMake，零外部依赖；MPI 可选 | `CMakeLists.txt:79` `option(ENABLE_MPI ... ON)` |
| 许可证 | **AGPL-3.0** | `COPYING:1`「GNU AFFERO GENERAL PUBLIC LICENSE」；API `license.key=agpl-3.0` |
| 活跃度 | 镜像末次提交 2026-01-23；14 stars / 22.6 MB | API `pushed_at=2026-01-23T10:00:35Z, stargazers_count=14`；浅克隆 depth=1 → **历史提交数无法统计**（如实标注） |
| 规模 | `src/` 944 文件 / 364,329 行；`examples/` 5 条主线、68 个子项目目录 | `metrics.json`（程序化计数） |
| 定位 | 通用 CFD 的**库**（非应用）：模板化描述符/动力学 + data-processor 编程模型 + MPI 多块 + IBM/自由面/多相/粒子/网格细化 | `src/` 24 子系统；`src/palabos3D.h` |

### 许可证对我们的可用性判断

**AGPL-3.0 = 强 copyleft + §13 网络条款 → 只能「读码参照」，禁止复制代码进 TensorLBM/TensorPlatform。**

- 不能：拷贝 `.h/.hh/.cpp` 片段（改名也不行）；把 Palabos 链接进对外提供的服务（§13 触发，要求整链路开源）。
- 可以（本报告做法）：读源码 → 提取**公式与算法结构** → 独立重写。公式不受版权保护；注释/命名/代码组织受保护，故只给「机制描述 + 指针」，不给可粘贴代码。
- 全仓单一许可：`COPYING` 是唯一许可文件，`examples/` 同许可 → 案例组织方式也只能借思想。
- 结论：**Palabos 是「教科书式参照物」，不是「可复用零件库」**。

---

## 二、方法谱系

### 2.1 格子

| 格子 | 位置 | 备注 |
|---|---|---|
| D2Q9 | `src/latticeBoltzmann/nearestNeighborLattices2D.h:73`(`D2Q9Constants`)、`:100`(`D2Q9DescriptorBase`) | 2D 主力 |
| D3Q13 | `nearestNeighborLattices3D.h:73`、`:109` | 少内存 |
| D3Q19 | `nearestNeighborLattices3D.h:135`、`:176` | 3D 主力 |
| D3Q27 | `nearestNeighborLattices3D.h:183`、`:226` | 全邻域 |
| D3Q39 / D3Q121 | `extendedNeighborhoodLattices3D.h:157`(+`:168` Forced) / `:88`、`:94` | 扩展邻域 |
| MRT 描述符 | `mrtLattices.h:78`(D2Q9)、`:104`(D3Q15)、`:133`(D3Q19)、`:165`(D3Q27) | 描述符内嵌 M 矩阵 |

**「存储型描述符」决定格子里存什么**（其内存优化核心）：`ForcedD3Q19Descriptor`（`nearestNeighborLattices3D.h:283`，多存力向量）、`RhoBarJD3Q19Descriptor`（只存 ρ̄,j）、`VelocityD3Q19Descriptor`（`:314`）、`SymTensorD3Q19Descriptor`（`:319`）、`AbsorbingWaveD3Q19Descriptor`（`:324`）、SC 专用 `shanChenLattices3D.h`、Smagorinsky 专用 `complexDynamics/smagorinskyLattices3D.h`。

### 2.2 碰撞谱系

`src/basicDynamics/`（标准族）+ `src/complexDynamics/`（进阶族），全为 `Dynamics<T,Descriptor>` 子类 → **碰撞 = 可插拔策略对象，逐格可不同**。

| 族 | 类 / 证据 |
|---|---|
| BGK | `BGKdynamics` — `basicDynamics/isoThermalDynamics.h:129` |
| BGK 变体 | `StochasticDynamics:165`、`CompleteBGKdynamics:201`、`CompleteRegularizedBGKdynamics:245`、`QuasiIncBGKdynamics:644`、`IncBGKdynamics:677`、`ConstRhoBGKdynamics:722`、`ChopardDynamics:946`、`PrecondBGKdynamics:1007`（同文件） |
| TRT | `CompleteTRTdynamics:288`、`CompleteRegularizedTRTdynamics:363`、`TruncatedTRTdynamics:439` |
| 正则化 RR | `RegularizedBGKdynamics:779`、`SecuredRegularizedBGKdynamics:811`、`IncRegularizedBGKdynamics:847`；综合族 `RRdynamics` `comprehensiveIsoThermalDynamics.h:332` |
| MRT | `MRTdynamics` `complexDynamics/mrtDynamics.h:75`、`IncMRTdynamics:111`、`GuoExternalForceMRTdynamics` `complexDynamics/externalForceMrtDynamics.h:54` |
| KBC | `Kdynamics` — `comprehensiveIsoThermalDynamics.h:238`；模板 `kbcTemplates` `latticeBoltzmann/kbcTemplates.h:45` |
| 熵 LBM | `entropicLbTemplates` `latticeBoltzmann/entropicLbTemplates.h:52` + `complexDynamics/entropicDynamics.h` |
| 综合族（Coreixas） | `HMdynamics:97`、`CMdynamics:144`、`CHMdynamics:191`、`Kdynamics:238`、`GHdynamics:285`、`RRdynamics:332`（`comprehensiveIsoThermalDynamics.h`）；逐模型硬展开 `latticeBoltzmann/comprehensiveModelsTemplates3D.h`（6,660 行） |
| Asinari LW-ACM | `complexDynamics/asinariModel.h:47` |
| 变粘度/非牛顿/热 | `carreauDynamics.h`、`smagorinskyDynamics*.h`、`dynamicSmagorinsky*`、`variableOmegaDynamics.h`；`thermalDynamics.h:152` |

**组织要点**：碰撞与格子正交解耦（同 BGK 可跑 D2Q9/D3Q19/D3Q27）；加速引擎再降级为**整数 id 分发表**（`src/acceleratedLattice/acceleratedDefinitions.h` 的 `CollisionModel` 枚举，正则计数 38 项，含 BGK/TRT/CM/CHM/K/GH/RR + 十余个边界正则化变体）。

### 2.3 力项

两段式：力先写进格子 external field（偏移由描述符定义，`externalFields.h` / `externalFieldAccess.h`），碰撞子类在 `collide()` 内按方案并入。

| 方案 | 实现 | 证据 |
|---|---|---|
| Guo | `addGuoForce` | `latticeBoltzmann/externalForceTemplates.h:63` |
| Guo 可压缩修正 | `addGuoCompressibleForce` | `:72` |
| 朴素/一阶 | `NaiveExternalForceBGKdynamics` | `basicDynamics/externalForceDynamics.h:93` |
| Guo 动力学封装 | `GuoExternalForceBGKdynamics` | `:160` |
| Guo + 完全正则化 | `GuoExternalForceCompleteRegularizedBGKdynamics` | `:192` |
| Guo + 一致性 Smagorinsky | `...ConsistentSmagorinskyCompleteRegularizedBGKdynamics` | `:236` |
| He（含 ∇u） | `HeExternalForceBGKdynamics` | `:306` |
| Shan-Chen 式注入 | `ShanChenExternalForceBGKdynamics` / `...RegularizedBGKdynamics` | `:274` / `:373` |
| 不可压 Guo | `IncGuoExternalForceBGKdynamics` | `:338` |

**缺口（零命中）**：`shiftedVelocity|Kupershtokh|exactDifference` 在 `src/` 全零 → **无 shifted-velocity / 精确差分力项** → 无该路线的高密度比伪势选项。

---

## 三、边界处理

### 3.1 类型与四种实现策略

`src/boundaryCondition/boundaryCondition.h:50`：`enum { dirichlet, neumann, freeslip, density, outflow, normalOutflow } BcType`。
接口 `OnLatticeBoundaryCondition3D`（`boundaryCondition3D.h:52`）支持逐面/逐棱/逐角施加（`setVelocityConditionOnBlockBoundaries` 声明 `:222`）。

| 策略 | 创建函数 | 证据 |
|---|---|---|
| 非平衡外推 / 正则化 local | `createLocalBoundaryCondition3D()` | `boundaryCondition3D.hh:1263`（声明 `:470`） |
| 局部定常 ρ | `createLocalConstRhoBoundaryCondition3D()` | `boundaryCondition3D.h:474` |
| 动力学基（Zou-He 走此路） | `createDynamicsBasedLocalBoundaryCondition3D()` | `boundaryCondition3D.h:478` |
| 插值（二阶） | `createInterpBoundaryCondition3D()` | `boundaryCondition3D.h:486` |
| 平衡态外推 | `createEquilibriumBoundaryCondition3D()` | `boundaryCondition3D.hh:1284` |

### 3.2 Zou-He

工厂 `createZouHeBoundaryCondition3D()` — `zouHeBoundary3D.h:48`；实体 `ZouHeVelocityDynamics`（`zouHeDynamics.h:54`）、`ZouHePressureDynamics`（`:81`）→ **Zou-He 实现为「每格一套边界动力学」**，故可逐格/逐角点施加。

### 3.3 bounce-back 家族

| 型式 | 实现 | 证据 |
|---|---|---|
| 全程 bounce-back（动力学式） | `class BounceBack : public Dynamics` | `src/core/dynamics.h:634` |
| 部分 bounce-back（半渗透/多孔） | `PartialBBdynamics` | `boundaryCondition/partialBBdynamics.h:12` |
| 动量交换（IBM 受力） | `MomentumExchangeBounceBack` | `boundaryCondition/bounceBackModels.h:61` |
| 壁面速度/密度修正 | `SetWallVelocityOnVelocityBounceBack3D` / `SetAverageWallDensity...` | `bounceBackModels3D.h:129` / `:119` |
| 加速引擎半程链接表 | `hw_bb_links` | `atomicBlock/atomicAcceleratedLattice3D.hh:149` |

### 3.4 曲率（curved wall）— off-lattice 模型族

接口 `OffLatticeBoundaryCondition3D`（`src/offLattice/offLatticeBoundaryCondition3D.h:58`），「形状（三角化 surface）× 插值模型」正交组合：

| 模型 | 证据 | 特点 |
|---|---|---|
| Bouzidi | `bouzidiOffLatticeModel3D.h:64` | 二次插值 BB |
| Filippova-Hänel | `filippovaHaenelOffLatticeModel3D.h:73` | 线性插值 + 壁速 |
| Guo | `guoOffLatticeModel3D.h:63`（+`:169` FD 版） | 外推 + 力修正 |
| 广义外推 / 广义插值 | `generalizedOffLatticeModel3D.h:43` / `:126` | 通用框架 |
| 插值 + FD 曲率修正 | `generalizedOffLatticeModel3D.h:229` `InterpolatedFdOffLatticeModel3D` | 最完整 |

形状抽象 `boundaryShapes3D.h:49`（`BoundaryShape3D`）、相交 `:144`；网格化 `connectedTriangleSet.h`/`connectedTriangleMesh.h`；域切片 `domainClustering3D.h`。

### 3.5 IBM

δ 核 `FavierDeltaFunction`（`immersedWalls3D.h:51`）、`InamuroDeltaFunction`（`:110`）；迭代 `InamuroIteration3D`（`:297`）；约化 `ReduceImmersedTorque3D`（`:210`）、`ReduceImmersedForce3D`（`:241`）、`ReduceImmersedArea3D`（`:270`）；与自由面/两相耦合 `RepelInterfaceFromImmersedWalls3D`（`freeSurfaceModel3D.h:1060`）。另有 `immersedWalls2D.h`、`guoAdvDiffOffLatticeModel3D.h`、`immersedAdvectionDiffusionWalls3D.h`。

**「曲率」三义别混**：①边界曲率（off-lattice 模型）；②自由面曲率（`FreeSurfaceComputeCurvature3D` `freeSurfaceModel3D.h:144`）；③两相界面曲率（`twoPhaseModel3D` 内以化学势/压力张量表达）。挂 slug 必须分语义。

---

## 四、多相 / 多组元（重点）

**总判断**：**4 条互不相同**路线；**无** free-energy LBM、**无**颜色梯度（RK）、**无** Allen-Cahn 相场、**无** spurious-current 专项抑制（零命中清单见 4.5）。

### 4.1 路线 A：Shan-Chen 伪势（SCMP + MCMP）

**势函数（4 种可插拔）** `src/multiPhysics/interparticlePotential.h`：`PsiIsRho:56`（ψ=ρ）、`PsiShanChen93:63`、`PsiShanChen94:75`、`PsiQian95:87`。

**处理器** `src/multiPhysics/shanChenProcessor3D.h`：`ShanChenMultiComponentProcessor3D:54`（**MCMP**）、`ShanChenSingleComponentProcessor3D:78`（**SCMP**）、`ShanChenExternalMultiComponentProcessor3D:98`；湿边界/收敛区 `ShanChenDirichlet3D`(`.hh:633`)、`ShanChenConvergeZone3D`(`.hh:715`)。

**互作用势形式 + 力项加法（逐行可核）** `shanChenProcessor3D.hh`：
1. `:304` `ScalarField3D<T> psiField` — ψ 场化缓存（注释 `:307`）
2. `:320` `psiField = Psi->compute(rho)` 逐点求势
3. `:343`–`:350` 邻居加权和 `rhoContribution[iD] += t_i * psi(x+c_i) * c_i[iD]`
4. `:364` `forceContribution -= G * psi(x) * rhoContribution[iD]`

即 **F(x) = −G ψ(x) Σ_i t_i ψ(x+c_i) c_i**（各向同性权重）。与我们 TensorLBM 同式，工程差异在 ψ 缓存 + 下条展开。

**性能手法（可吸收）**：`multiPhaseTemplates3D.h:71` 通用模板（iPop 循环）vs `:90` 起 `ForcedShanChenD3Q19Descriptor` **完全展开特化**（19 项手写、无内层循环、无 `c_i` 间接寻址）= 「通用实现 + 格子级展开」。

**力注入演化**：写回 external force 字段，由 `ShanChenExternalForceBGKdynamics`（`externalForceDynamics.h:274`）/ `...RegularizedBGKdynamics`（`:373`）在 collide 合并。**命名陷阱**：此处 "ShanChenExternalForce" 指「SC 式（速度平移）力注入」，不是 SC 势本身。

**密度比能力（机制判断，未实测）**：SC 路线**无任何内置高密度比机制**；示例 `examples/codesByTopic/shanChenMultiPhase/segregation2D.cpp:113` `G=-120.0`、`:122` `rho0=200.0` 只是 2D 分相，本质低密度比 + 大 G。→ **代码机制上 Palabos SC 不适配 >10 密度比；更高必须换路线 B**。
**spurious current**：**无专门处理**，只能调 G/ψ 或用正则化碰撞（`:373`）。对我们的 GAP 是「负面清单」：Palabos 给不出答案。

### 4.2 路线 B：Lee/He-Lee 两相扩散界面（最接近「相场」，含化学势）

`src/multiPhysics/twoPhaseModel3D.h`：
- `TwoPhaseMacroscopic3D(...)` 构造含 `rhoDefault_`、**`densityRatio_`**、`surfaceTension_`、`TwoPhaseModel model_` — `:1279`（成员 `:1314`、`:1524`）
- **`densityRatio` 为显式参数** +「非单位密度比需重标定」实现注释（`twoPhaseModel3D.hh:1287`、`:2213`）→ **Palabos 里唯一支持高密度比的两相路线**
- 接触角：`contactAngle` 构造参数 `:1905`（成员 `:1916`）
- `TwoPhaseModel` 枚举：`undefined=-1`、`kinetic=1(:202)`、`dynamic=2`、`bubblePressure=3`、`constRho=4`、`freeSurface=5(:206)`

**化学势/界面三步分裂** `src/multiPhysics/heLeeProcessor3D.h`（头注释点名 Lee 原文 `:65` JCP 206 (2005)、`:66` CMA 58 (2009)，并说明算法拆成 4 个 processor `:94`）：
- `Compute_C_processor:141` — 相场 C 与梯度
- `Compute_gradC_rho_mu_processor:159` — ∇C、ρ、**化学势 μ**
- `Compute_gradMu_laplaceMu_processor:178` — ∇μ、∇²μ

**与「相场 Allen-Cahn」的关系（关键区分）**：这是 **Lee 的「压力演化 + 化学势」分裂**，界面方程走 Cahn-Hilliard 型**保守**输运（C 守恒 + μ = f(C) − κ∇²C），**不是** Allen-Cahn（非保守、含界面法向速度项）。可借鉴的共同点：都靠「化学势 → 界面力/表面张力」链，`∇·(M∇μ)` 的离散技巧通用。→ **Palabos 无 AC；最近参照 = Lee 保守相场**。

**相变/汽泡线**：`bubbleHistory3D.hh:623`（"Cutoff density ratio for the bubble internal pressure"）——作者亦承认高密度比需额外保护。

### 4.3 路线 C：自由面（Thürer 型 volume tracking，非两相求解）

- 8 态标志：`empty=0, interface=1, fluid=2, wall=4, protect=5, temporarilyProtect=6, protectEmpty=7, slipWall=8` — `freeSurfaceUtil3D.h:55`
- **质量记账（Thürer eq.6）**：`freeSurfaceTemplates.h:53` → `:65`（`param.mass(...) += cell[opp] - cell_next[iPop]`）；用**入射分布**记账，注释 `:61`–`:63` 解释与原文「出场分布」的对应（`f_out_i(x,t)=f_in_opp(x+c_i,t+1)`）——正解决方向/符号这个经典坑
- 处理器族（`freeSurfaceModel3D.h`）：法向 `:66`、几何 `:90`、曲率 `:144`、`FreeSurfaceMassChange3D:213`、`FreeSurfaceCompletion3D:249`、张力 `:350`/Weber `:393`/标量场 `:450`、稳定化 `:495`、假界面清理 `:706`、**超额质量再分配 `:772`**、**+丢失质量统计 `:805`**、界面容器 `:530`
- VOF 工具：`offLattice/marchingCube.h:86` `vofToTriangles`；示例 `examples/showCases/vofMultiPhase`
- 工程思想（可吸收）：**质量永远记账 + 超额强制再分配 + 丢失质量作为可观测量** → 把「体积不守恒」变成可测可收敛的量

### 4.4 路线 D：热相变（He-Lee 热耦合/汽泡）

`heLeeProcessor3D.h` + `bubbleHistory3D.hh:623` + `createBubbles3D.h` / `bubbleMatch3D.h`。声致发光/汽泡崩塌线；对冰-水相变仅作「能量方程 + 界面潜热耦合」机制参照。

### 4.5 明确「无」的清单（防后人重复误判）

| 声称缺失 | 验证 | 结果 |
|---|---|---|
| free-energy LBM | `grep -rInE "freeEnergy\|ColorGradient\|colorGradient" src/` | 0 |
| 颜色梯度（RK）/recoloring | 同上 | 0 |
| Allen-Cahn | `grep -rInE "AllenCahn\|Allen-Cahn" src/` | 0 |
| shifted-velocity / Kupershtokh | `grep -rInE "shiftedVelocity\|Kupershtokh\|exactDifference" src/` | 0 |
| spurious-current 专项抑制 | `grep -rInE "spurious" src/` | 仅 1 处无关注释（`comprehensiveModelsTemplates3D.h:6050`） |---

## 五、GPU / 并行

### 5.1 两套数据布局并存（本仓最有信息量处）

**旧核心（AoS + 指针）**：`BlockLattice3D` 存 `Cell<T,Descriptor> ***grid`（`atomicBlock/blockLattice3D.h:221`）；`Cell` 含 `Array<T,q>` + `Dynamics<T,Descriptor>* dynamics`（`core/cell.h:104` 类、`:422` 成员）→ 每格指针解引用 + 虚函数 collide：CPU 上换来「逐格异构模型」（边界/多孔/自由面全靠它），GPU 上是灾难。

**新加速引擎（SoA + 整数分派）**：`populations` 一维数组 `N*numPop`（`atomicAcceleratedLattice3D.hh:206`）；`collisionMatrix` 每格模型 id（`:143`）；半程 BB 链接表 `hw_bb_links`（`:149`）→ **AA-pattern**（齐次数组 + 整数 id 分派）的 C++ 实现。

### 5.2 执行后端：**不是 CUDA**

- `enum class ExecutionMode { seq, stdpar, openmp };` — `acceleratedDefinitions.h:47`；默认 `stdpar` — `multiBlock/acceleratedLattice3D.h:143`
- 热循环全用 `std::for_each(std::execution::par_unseq, ...)` — `atomicAcceleratedLattice3D.hh:71/:83/:106/:138/:143/:149/:206/:211`
- GPU 路径 = **nvc++ 把 stdpar 编到 GPU**：`examples/gpuExamples/README:5` 要求 `-DCMAKE_CXX_COMPILER=nvc++`（NVIDIA HPC SDK）；5 个 GPU 示例（cavity3d / multiComponentPorous / rayleighTaylor3D / sandstone / tgv3d）
- **零 CUDA/HIP/OpenCL/SYCL 计算内核**（`__global__|<hip/...>|CL/cl.h|sycl` 全零）；唯一 CUDA 痕迹是主机端分配 `core/globalDefs.h:327`（`#ifdef USE_CUDA_MALLOC` 下 `cudaMalloc`，MPI 缓冲用）
- **教训**：stdpar 无手写融合 kernel，collide 与 stream 仍是两次独立全局遍历（`:143` vs populations 遍历分离）→ 「融合 kernel」须自写（Triton/CUDA），Palabos 给不了范式。

### 5.3 MPI 与通信隐藏

- `CMakeLists.txt:79` `ENABLE_MPI ON`；`:80`–`:85` `find_package(MPI REQUIRED)`
- 分区：规则块 + envelope（ghost）：`multiBlock/defaultMultiBlockPolicy3D.h:137`（`Box3D`+`envelopeWidth`）、`:144`；块结构 `multiBlock/multiBlockManagement3D.h`（含 `SparseBlockStructure3D`）
- 通信器两套（策略可切）：`ParallelBlockCommunicator3D`（`parallelism/parallelBlockCommunicator3D.h:74`，`communicate() :83/:89`，内部 `sendRecvPool` 非阻塞收发池 → 通信/计算重叠）vs `BlockingCommunicator3D`（`:101`）
- 细化并行：`multiGrid/gridRefinement.h`、`domainDivision3D.h`；统计约化 `parallelStatistics.h`

---

## 六、可微 / 代码生成

**结论：零。** `autograd|autodiff|reverse-mode|GradientTape` 在 `src/` 全零；24 子系统无 AD/codegen 模块。

有的只是**编译期生成**（模板元编程）：格子/字段/力偏移编码进 `Descriptor` 模板参数；综合碰撞族逐模型硬展开 6,660 行（`comprehensiveModelsTemplates3D.h`）；多相力逐格展开（`multiPhaseTemplates3D.h:90`）；加速引擎再把类型分派降级为整数 id 查表（`acceleratedDefinitions.h:47` 前后）——用「可移植性换性能」的反向操作。

与我们可微需求的对比：AoS + 指针结构**无法**在不重写核心下接 AD。可借鉴的只有 `coupledSimulators/`（进程级多物理场耦合）与 `libraryInterfaces/TINYXML_xmlIO.h`（XML I/O）作「Python 前端 ↔ 编译型求解器」接口范式；可微须另起炉灶（对比 XLB，报告 9561）。

---

## 七、与我们功能点的映射

口径：`fits` = 机制可直接照做（仍须自写，AGPL）；`adapt` = 需改造/补验证；`reference` = 参照物，缺关键能力。

| # | slug | Palabos 对应 | tag | files:line | transfer |
|---|---|---|---|---|---|
| 1 | `GAP-LBM-MCMP-POISEUILLE`（两组分分层 Poiseuille） | MCMP 力项 + 力注入完整；但**无**现成分层 Poiseuille 验证算例 | `shan-chen-mcmp-psi-field` | `shanChenProcessor3D.h:54`、`.hh:304,350,364`、`externalForceDynamics.h:274`、`multiPhaseTemplates3D.h:90` | **adapt** |
| 2 | MCMP 高密度比 | SC 无高密度比机制；高密度比只在路线 B | `lee-two-phase-density-ratio` | `twoPhaseModel3D.h:1279,1314`、`heLeeProcessor3D.h:141,159,178`、`bubbleHistory3D.hh:623` | **reference** |
| 3 | 自由面 | Thürer 质量记账 + 8 态 + 超额再分配 + lost-mass | `thuerey-mass-tracking-free-surface` | `freeSurfaceUtil3D.h:55`、`freeSurfaceTemplates.h:53,65`、`freeSurfaceModel3D.h:213,772,805` | **fits** |
| 4 | 相场 Allen-Cahn | **无 AC**；最近 = Lee 保守相场（化学势分裂） | `lee-chemical-potential-splitting` | `heLeeProcessor3D.h:65,141,159,178`、`twoPhaseModel3D.h:202` | **reference** |
| 5 | GPU 融合 kernel | 无手写融合 kernel；stdpar 自动并行，collide/stream 分离遍历 | `stdpar-aa-pattern-dispatch` | `acceleratedDefinitions.h:47`、`acceleratedLattice3D.h:143`、`atomicAcceleratedLattice3D.hh:143,206` | **reference** |
| 6 | 分布式多卡 | 仅 MPI 多节点（envelope + sendRecvPool）+ 单设备 stdpar；无多 GPU | `mpi-envelope-async-halo` | `defaultMultiBlockPolicy3D.h:137`、`parallelBlockCommunicator3D.h:74,83,101`、`CMakeLists.txt:79` | **adapt** |
| 7 | 稀疏 / 多孔 | 部分 BB 动力学 + 稀疏块结构 + 孔隙示例 | `partial-bounce-back-porous` | `partialBBdynamics.h:12`、`bounceBackModels.h:61`、`examples/gpuExamples/`(multiComponentPorous, sandstone) | **fits** |
| 8 | 案例组织 | `examples/` 五分线（showCases/codesByTopic/benchmarks/tutorial/gpuExamples）、68 子项目、每例独立 CMake 并全局注册 | `examples-taxonomy` | `CMakeLists.txt:159` | **fits** |
| 9 | 测试与验证面 | 示例带 `test/`；`enable_testing()` + `PLB_REGRESSION` + ASan 默认 | `plb-regression-flag-asan` | `CMakeLists.txt:29`、`:45`（`-DPLB_REGRESSION -fsanitize=address`） | **fits** |
| 10 | CI | GitLab CI：format gate + regression + parallel regression + ccache 12 套矩阵；AppVeyor Win/macOS | `format-gate-ccache-matrix-ci` | `.gitlab-ci.yml:57,67,85`、`appveyor.yml` | **fits** |
| 11 | NEW：综合碰撞族 HM/CM/CHM/K/GH/RR | 我们无对应 | `comprehensive-collision-family` | `comprehensiveIsoThermalDynamics.h:97,144,191,238,285,332` | **reference** |
| 12 | NEW：非局部边界动力学（NLD） | 把 ∇u/曲率以「非局部 dynamics」注入局部碰撞 | `nld-nonlocal-boundary-dynamics` | `NLD_boundaries3D.h:49,56`、`NLD_boundaryDynamics3D.h` | **reference** |
| 13 | NEW：off-lattice 曲率边界三族 | 我们曲率边界覆盖不足；「形状 × 模型」正交组合可参照 | `offlattice-shape-model-orthogonal` | `offLatticeBoundaryCondition3D.h:58`、`bouzidiOffLatticeModel3D.h:64`、`filippovaHaenelOffLatticeModel3D.h:73`、`guoOffLatticeModel3D.h:63`、`generalizedOffLatticeModel3D.h:43,126,229` | **adapt** |
| 14 | NEW：IBM（Favier/Inamuro） | 船体/附体流固耦合可直接对标 δ 核与迭代方案 | `favier-inamuro-ibm` | `immersedWalls3D.h:51,110,297,210,241` | **adapt** |

---

## 八、可吸收 Top3 + 风险

### Top1｜MCMP 力项「ψ 场化 + 格子级展开」两级写法
- 吸收点（对 `GAP-LBM-MCMP-POISEUILLE`）：ψ 先算成独立场再访问邻居（`shanChenProcessor3D.hh:304`–`:320`），免去每次 stencil 重算 ψ（我们当前现场算）；对主力格子做完全展开的力累加（`multiPhaseTemplates3D.h:90`），消掉内层 iPop 循环。
- 判据（下一步）：同一 MCMP Poiseuille 算例，比「通用循环 vs 展开版」每步耗时，且力场逐位一致（`max|ΔF|=0`，允许浮点重排 1 ulp）。公式不变，风险低。
- 代价：需在 kernel 生成侧或手写 D3Q19 特化，工作量「一个 kernel × 一个格子」。

### Top2｜自由面「质量记账 + 超额再分配 + lost-mass 可观测量」
- 吸收点：`freeSurfaceTemplates.h:53`–`:68` 入射分布记账（含 `:61`–`:63` 方向约定注释，正是我们最易写错处）；`freeSurfaceModel3D.h:772`/`:805` 超额再分配与丢失质量统计 → 把质量误差变成被监控量而非悄悄漂移。
- 用途：舱液晃、破舱进水、甲板/舱室自由面。**注意：Palabos 自由面只解单相液体，气体区为「空」，不解气相**；要气泡夹带必须换路线 B。
- 判据：静水静压液柱 10000 步后总质量相对漂移 < 1e-6（同策略基线期望）。

### Top3｜平台面：examples 分层 + `PLB_REGRESSION` + ASan 默认 + ccache 矩阵 CI
- 吸收点（对应「案例组织 / 测试与验证面 / CI」三 slug）：示例按用途分层（showCases=演示、codesByTopic=最小复现、benchmarks=性能、tutorial=教学）；每例自带 `test/`；编译期宏 `PLB_REGRESSION` 切「短回归」并默认开 `-fsanitize=address`（`CMakeLists.txt:45`）→ **同一份代码 CI 跑短回归、本地跑长算例**，不维护两套；CI 12 套 ccache 矩阵 + format gate（`.gitlab-ci.yml:57`）。
- 落地：把「回归开关 + ASan 默认」搬到 TensorLBM 的 CMake/CI；示例目录按用途分四层。

### 风险（入账）
1. **许可（高）**：AGPL-3.0。抄代码/注释/文件名组织都可能污染 TensorLBM；落地必须从公式重写，并留「参考自 omalaspinas/palabos@8f8ecd2 <file>:<line>，独立实现」的出处注释（出处不是许可豁免；有 §13 顾虑需法务判断）。
2. **范式（中）**：AoS + `Dynamics*` + 模板元编程；我们走数组化 + 融合 kernel 时**只能借思想不能借结构**。
3. **GPU 误导（中）**：5 个 `gpuExamples` 易被读成「有 GPU 后端」，实为 nvc++ stdpar：无 CUDA kernel、无多 GPU、collide/stream 不融合。
4. **密度比误导（中）**：`G=-120/rho0=200` 易被读成支持高密度比；代码机制上不支持，高密度比只在 Lee 两相（`densityRatio`）。挂 slug 必须分开。
5. **验证面（低-中）**：其 `test` 是示例级回归（比对上次输出），**不是**解析解验证，不能作我们精度基线；解析对照仍须自建（Top2 判据是我们自己的）。

### 九、误差判定（本单为代码盘点，无仿真数值）

| 关键量 | 基准 | 误差 | 是否 <3% |
|---|---|---|---|
| 指针可信度（108 条 `文件:行`） | 源码本体（机器比对 token） | 0 条不符（108/108 通过） | 是（0%） |
| 「无 X」5 条断言 | `grep -rInE` 全仓复跑 | 0 条反例（唯 1 处无关注释已列明） | 是 |
| 代码规模 / 示例数 / 碰撞 id 数 | 程序化计数（`metrics.json`） | 与 shell 独立复核一致 | 是 |
| 密度比能力 / spurious current / GPU 融合度 | 无数值基准（未编译运行） | 不下结论，仅机制判断 | **不适用**（如实声明：本单未做数值验证，故不给精度结论；后续若复用需自建解析解对照） |

---

## 附：机器块

[OSS-INV]
repo: omalaspinas/palabos
lang: C++ (C++20, CMake, no external deps; MPI optional)
license: AGPL-3.0
methods: lattice=D2Q9/D3Q13/D3Q15(MRT)/D3Q19/D3Q27/D3Q39/D3Q121; collision=BGK/TRT(complete,truncated,regularized)/MRT/KBC(K)/entropic(Ansumali-Karlin)/RR(regularized,secured,inc)/comprehensive(HM,CM,CHM,GH)/Asinari-LW-ACM/Smagorinsky+dynamicSmagorinsky+Carreau+variableOmega; forcing=Guo/Guo-compressible/Naive/He/ShanChen/Inc-Guo (no shifted-velocity/exact-difference)
multiphase: shan-chen SCMP+MCMP (interparticlePotential.h:56,63,75,87; shanChenProcessor3D.h:54,78; force shanChenProcessor3D.hh:304,350,364; unrolled D3Q19 multiPhaseTemplates3D.h:90); Lee/He-Lee two-phase diffuse interface with explicit densityRatio (twoPhaseModel3D.h:1279,1314; chemical-potential split heLeeProcessor3D.h:141,159,178); free-surface Thuerey mass-tracking (freeSurfaceUtil3D.h:55; freeSurfaceTemplates.h:53,65; freeSurfaceModel3D.h:213,772,805); thermal phase change (bubbleHistory3D.hh:623). NO free-energy, NO color-gradient, NO Allen-Cahn, NO spurious-current suppression
boundary: bounce-back family (fullway dynamics core/dynamics.h:634; partial partialBBdynamics.h:12; momentum-exchange bounceBackModels.h:61); Zou-He (zouHeBoundary3D.h:48; zouHeDynamics.h:54,81); BC strategies local/constRho/dynamics-based/interp/equilibrium (boundaryCondition3D.hh:1263,1284; boundaryCondition3D.h:474,478,486); NLD nonlocal BC (NLD_boundaries3D.h:49); off-lattice curved-wall Bouzidi/Filippova-Haenel/Guo/generalized-FD (bouzidiOffLatticeModel3D.h:64; filippovaHaenelOffLatticeModel3D.h:73; guoOffLatticeModel3D.h:63; generalizedOffLatticeModel3D.h:43,126,229); IBM Favier/Inamuro (immersedWalls3D.h:51,110,297,210,241)
gpu: no CUDA/HIP/OpenCL kernels; accelerated engine = SoA populations (N*numPop, atomicAcceleratedLattice3D.hh:206) + integer collisionMatrix id dispatch (:143) + hw_bb_links (:149), run via C++17 stdpar std::execution::par_unseq (:71) or OpenMP; GPU only through nvc++ (examples/gpuExamples/README:5); parallel = MPI block decomposition with envelope ghosts + non-blocking sendRecvPool (parallelBlockCommunicator3D.h:74,83,101)
summary: Palabos 是 AGPL-3.0 的模板化通用 LBM 库（364 kLOC / 944 源文件），方法谱系极完整（7 种格子、9 类碰撞含 KBC 与 Coreixas 综合族、6 种力项、4 条多相路线、曲率边界三族、IBM、自由面），但无高密度比伪势机制、无 Allen-Cahn、无 free-energy、无 CUDA 内核（GPU 仅 nvc++ stdpar）、无 autograd。
summary2: 最佳可吸收点全在机制组织层而非代码——MCMP 力项的 psi 场化+格子级展开、自由面的质量记账+超额再分配+lost-mass 可观测量、examples 分层 + PLB_REGRESSION + ASan + ccache 矩阵 CI 的验证面模板；AGPL 决定只能重写公式不能搬码。
[/OSS-INV]

[OSS-SOL]
repo: omalaspinas/palabos
slug: GAP-LBM-MCMP-POISEUILLE
tag: shan-chen-mcmp-psi-field
method: 多组分 Shan-Chen 显式力 F=-G*psi(x)*sum_i t_i*psi(x+c_i)*c_i；先把 psi 缓存成标量场再访问邻居，力写回格子 external force 字段，由 ShanChenExternalForceBGKdynamics/Regularized 在 collide 内并入；D3Q19 走完全展开特化。
files: src/multiPhysics/shanChenProcessor3D.h:54, src/multiPhysics/shanChenProcessor3D.hh:304, src/multiPhysics/shanChenProcessor3D.hh:350, src/multiPhysics/shanChenProcessor3D.hh:364, src/multiPhysics/multiPhaseTemplates3D.h:90, src/basicDynamics/externalForceDynamics.h:274
conventions: t_i 各向同性权重；psi 由 PsiFunction 策略给定（PsiShanChen93/94、PsiQian95、PsiIsRho）；G 为作用强度（示例 segregation2D.cpp:113 G=-120、:122 rho0=200）；力存 external field 偏移处
transfer: adapt
[/OSS-SOL]

[OSS-SOL]
repo: omalaspinas/palabos
slug: MCMP 高密度比
tag: lee-two-phase-density-ratio
method: Lee/He-Lee 两相扩散界面以显式 densityRatio 参数化，界面用相场 C 与化学势 mu 的三步分裂（C 与梯度 -> gradC/rho/mu -> gradMu/laplaceMu），表面张力与接触角由参数注入；SC 路线无任何高密度比机制。
files: src/multiPhysics/twoPhaseModel3D.h:1279, src/multiPhysics/twoPhaseModel3D.h:1314, src/multiPhysics/heLeeProcessor3D.h:141, src/multiPhysics/heLeeProcessor3D.h:159, src/multiPhysics/heLeeProcessor3D.h:178, src/multiPhysics/bubbleHistory3D.hh:623
conventions: densityRatio=重相/轻相密度，显式输入；非单位密度比需重标定（twoPhaseModel3D.hh:1287,2213）；TwoPhaseModel 分支 kinetic/dynamic/bubblePressure/constRho/freeSurface（twoPhaseModel3D.h:202-206）；contactAngle 参数化
transfer: reference
[/OSS-SOL]

[OSS-SOL]
repo: omalaspinas/palabos
slug: 自由面
tag: thuerey-mass-tracking-free-surface
method: 8 态 flag（empty/interface/fluid/wall/protect/temporarilyProtect/protectEmpty/slipWall）+ 界面格 mass 做体积记账，质量变化用入射分布累加（Thuerey eq.6），超额质量强制再分配、丢失质量单独统计成可观测量，表面张力由曲率法向注入。
files: src/multiPhysics/freeSurfaceUtil3D.h:55, src/multiPhysics/freeSurfaceTemplates.h:53, src/multiPhysics/freeSurfaceTemplates.h:65, src/multiPhysics/freeSurfaceModel3D.h:213, src/multiPhysics/freeSurfaceModel3D.h:772, src/multiPhysics/freeSurfaceModel3D.h:805
conventions: mass += f[opp] - f_next[iPop]（入射分布口径，见同文件 :61-63）；flag 值 0/1/2/4/5/6/7/8；单相液体，气体区为空（不解气相）
transfer: fits
[/OSS-SOL]

[OSS-SOL]
repo: omalaspinas/palabos
slug: 相场 Allen-Cahn
tag: lee-chemical-potential-splitting
method: 无 Allen-Cahn 实现；最接近的是 Lee 保守相场分裂：先算相场 C 与梯度，再由 C/rho 得化学势 mu，再对 mu 求梯度/拉普拉斯得到界面力与表面张力，分 4 个 data processor 串联。
files: src/multiPhysics/heLeeProcessor3D.h:65, src/multiPhysics/heLeeProcessor3D.h:141, src/multiPhysics/heLeeProcessor3D.h:159, src/multiPhysics/heLeeProcessor3D.h:178, src/multiPhysics/twoPhaseModel3D.h:202
conventions: C 为守恒型序参量（Cahn-Hilliard 型输运，非 Allen-Cahn 的界面法向速度项）；界面厚度由平滑/滤波层控制；mu=f(C)-kappa*laplace(C)
transfer: reference
[/OSS-SOL]

[OSS-SOL]
repo: omalaspinas/palabos
slug: GPU 融合 kernel
tag: stdpar-aa-pattern-dispatch
method: 加速引擎把格子改成 SoA（populations 一维 N*numPop 数组）+ 每格整数 collisionMatrix 模型 id + 半程 bounce-back 链接表，用 C++17 std::execution::par_unseq for_each 并行；GPU 仅通过 nvc++ 把 stdpar 编译到设备，无手写融合 kernel，collide 与 stream 仍为两次独立遍历。
files: src/acceleratedLattice/acceleratedDefinitions.h:47, src/multiBlock/acceleratedLattice3D.h:143, src/atomicBlock/atomicAcceleratedLattice3D.hh:143, src/atomicBlock/atomicAcceleratedLattice3D.hh:206, examples/gpuExamples/README:5
conventions: ExecutionMode{seq,stdpar,openmp} 默认 stdpar；GPU 编译需 -DCMAKE_CXX_COMPILER=nvc++；CollisionModel 枚举约 38 个 id
transfer: reference
[/OSS-SOL]

[OSS-SOL]
repo: omalaspinas/palabos
slug: 分布式多卡
tag: mpi-envelope-async-halo
method: MPI 规则块分解 + envelope ghost 层；通信器可切：ParallelBlockCommunicator3D 内部用 sendRecvPool 做非阻塞收发（通信与计算重叠），BlockingCommunicator3D 作阻塞对照；多块管理支持稀疏块结构。
files: CMakeLists.txt:79, src/multiBlock/defaultMultiBlockPolicy3D.h:137, src/parallelism/parallelBlockCommunicator3D.h:74, src/parallelism/parallelBlockCommunicator3D.h:83, src/parallelism/parallelBlockCommunicator3D.h:101
conventions: envelopeWidth 按需给定（多相/IBM 需更宽，见 twoPhaseModel3D.h:2427 envelopeWidth）；sendRecvPool 在两消息间复用缓冲
transfer: adapt
[/OSS-SOL]

[OSS-SOL]
repo: omalaspinas/palabos
slug: 稀疏 / 多孔
tag: partial-bounce-back-porous
method: 部分 bounce-back 动力学对象以「部分反射」表示多孔骨架/半渗透壁，配合稀疏块结构与孔隙几何示例；动量交换 bounce-back 另用于 IBM 受力统计。
files: src/boundaryCondition/partialBBdynamics.h:12, src/boundaryCondition/bounceBackModels.h:61, src/core/dynamics.h:634, src/atomicBlock/atomicAcceleratedLattice3D.hh:149
conventions: 反射系数 0=全反射/1=全通过 连续刻画多孔阻力；accelerated 引擎把壁面标为 collisionMatrix 的 BounceBack id 并用 hw_bb_links 跳过
transfer: fits
[/OSS-SOL]

[OSS-SOL]
repo: omalaspinas/palabos
slug: 案例组织
tag: examples-taxonomy
method: examples 按用途分五条主线（showCases 演示 / codesByTopic 单主题最小复现 / benchmarks 性能 / tutorial 教学 / gpuExamples 加速版），每例独立 CMakeLists 并在顶层 CMake 全量 add_subdirectory 注册。
files: CMakeLists.txt:159, examples/gpuExamples/README:5
conventions: 68 个子项目目录；每个示例自带 test/ 子目录用于回归比对
transfer: fits
[/OSS-SOL]

[OSS-SOL]
repo: omalaspinas/palabos
slug: 测试与验证面
tag: plb-regression-flag-asan
method: CMake enable_testing() + 编译期宏 PLB_REGRESSION 把同一份代码切成「短回归」构建（并默认开 -fsanitize=address、-fno-omit-frame-pointer），CI 跑短回归、本地跑长算例，不维护两套代码。
files: CMakeLists.txt:29, CMakeLists.txt:45
conventions: TESTMPI flags = -g -DPLB_DEBUG -DPLB_REGRESSION -O1 -fsanitize=address；PLB_DEBUG 打开运行时断言
transfer: fits
[/OSS-SOL]

[OSS-SOL]
repo: omalaspinas/palabos
slug: CI
tag: format-gate-ccache-matrix-ci
method: GitLab CI 分阶段 prepare_docker -> check_format -> regression_tests -> parallel_regression_tests -> build_cmake，用 12 套 ccache key（gcc/clang × ser/par × rel/deb）矩阵缓存编译产物；AppVeyor 另覆盖 Windows/macOS。
files: .gitlab-ci.yml:57, .gitlab-ci.yml:67, .gitlab-ci.yml:85, CMakeLists.txt:45
conventions: clang-format 版本固定（README 要求 14）；回归用 PLB_REGRESSION + ASan；MPI 回归单独跑 parallel_regression_tests
transfer: fits
[/OSS-SOL]

[NEW-SLOT]
repo: omalaspinas/palabos
slug: NEW: 综合碰撞族 HM/CM/CHM/K/GH/RR（Hermite 矩/累积量/混合/熵/混合正则化）
tag: comprehensive-collision-family
files: src/basicDynamics/comprehensiveIsoThermalDynamics.h:97,144,191,238,285,332, src/latticeBoltzmann/comprehensiveModelsTemplates3D.h
note: 我们 TensorLBM 目前无对应实现；可作为「碰撞模型扩展」功能点立项参照（reference 级）
[/NEW-SLOT]

[NEW-SLOT]
repo: omalaspinas/palabos
slug: NEW: 非局部边界动力学 NLD（把梯度/曲率信息注入局部碰撞）
tag: nld-nonlocal-boundary-dynamics
files: src/boundaryCondition/NLD_boundaries3D.h:49,56, src/boundaryCondition/NLD_boundaryDynamics3D.h
note: 曲率边界的一种组织范式；与我们现有 local BC 可互补
[/NEW-SLOT]

[NEW-SLOT]
repo: omalaspinas/palabos
slug: NEW: off-lattice 曲率边界「形状 × 模型」正交组合（Bouzidi/FH/Guo/FD）
tag: offlattice-shape-model-orthogonal
files: src/offLattice/offLatticeBoundaryCondition3D.h:58, src/offLattice/bouzidiOffLatticeModel3D.h:64, src/offLattice/filippovaHaenelOffLatticeModel3D.h:73, src/offLattice/guoOffLatticeModel3D.h:63, src/offLattice/generalizedOffLatticeModel3D.h:43,126,229
note: 我们曲率边界覆盖不足，可参照其正交分解补 D3Q19 曲率壁
[/NEW-SLOT]

[NEW-SLOT]
repo: omalaspinas/palabos
slug: NEW: IBM（Favier/Inamuro δ 核 + 隐式迭代 + 力/力矩约化）
tag: favier-inamuro-ibm
files: src/offLattice/immersedWalls3D.h:51,110,297,210,241
note: 船体/附体流固耦合可直接对标；我们尚无 IBM 实现
[/NEW-SLOT]