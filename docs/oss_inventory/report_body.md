# 【盘点】lssfau/walberla — 开源 LBM 技术深盘（tier 3 · block-forest 多物理 HPC 框架）

> 盘点方式：`git clone --depth 1 https://github.com/lssfau/walberla.git`（SNI 窗口内成功）后**读代码本体**，文档仅辅助。
> 全部结论带 `文件:行` 指针；无指针的判断一律标「未核实」。统计数字由脚本 `docs/oss_inventory/walberla_scan.py` 程序化产出（`docs/oss_inventory/walberla_stats.json`），零手抄。

```
repo: lssfau/walberla   commit: dc16f3130de2858f56e76bc9b8523710c94c23f0 (2026-08-04)
```

---

## 一、概况

| 项 | 值 | 证据 |
|---|---|---|
| 语言 | C++17 核心 + Python 代码生成层（sympy/lbmpy/pystencils） | `src/**/*.h|*.cpp` 1,699 个代码文件；`python/pystencils_walberla/`、`sweepgen/src/sweepgen/` |
| 许可证 | **GPL-3.0-or-later** | `COPYING.txt:1`（"GNU GENERAL PUBLIC LICENSE Version 3"）+ `codemeta.json` → `https://spdx.org/licenses/GPL-3.0-or-later` |
| 活跃度 | 活跃（FAU LST 维护；`CITATION.cff`；论文 Bauer et al. CAMWA 2020 见 `README.md:40`） | 最近提交 2026-08-04 |
| 定位 | 块结构 AMR + 多物理（LBM / 自由面 / 刚体颗粒 / 流固耦合）超算框架 | `README.md:1-10` |

### 许可证对我们的可用性判断（关键结论）

**GPL-3.0-or-later 是强传染性 copyleft：不能把本仓 C++ 代码（哪怕单个 `.h`）复制进我们采用宽松许可的 TensorLBM 仓。** 可行路径只有两条：

1. **只取算法与架构设计**（公式、数据结构、调度方式），自己重写实现——本报告全部指针即为此用；
2. **只看接口契约，不复用实现**。注意其 Python 生成链 `lbmpy` / `pystencils` **不在本仓**，许可证须单独核实（本次未盘，标 WITHHELD，勿假设宽松）。

### 规模（程序化统计，见 `stats_table.md`）

{{STATS_TABLE}}

---

## 二、方法谱系

### 2.1 格子（Stencil）

格子模板文件 8 个：`src/stencil/{D2Q4,D2Q5,D2Q9,D3Q6,D3Q7,D3Q15,D3Q19,D3Q27}.h`（脚本枚举，非手抄）；对应的 LBM 晶格模型在 `src/lbm/lattice_model/{D2Q9,D3Q15,D3Q19,D3Q27}.h`。

- 权重定义方式（D3Q27）：`src/lbm/lattice_model/D3Q27.h:66-69`（`w_0=8/27, w_1=2/27, w_2=1/54, w_3=1/216`），并同时提供 `wInv[27]`（`src/lbm/lattice_model/D3Q27.h:53`）。
- 组装方式：唯一模板基类 `LatticeModelBase<CollisionModel_T, Compressible, ForceModel_T, EquilibriumAccuracyOrder>`（`src/lbm/lattice_model/LatticeModelBase.h:68`，`equilibriumAccuracyOrder` 在 `:79`）→ 碰撞/力项/可压缩性/平衡态阶数全是模板参数，运行时零虚函数开销。
- 约束示例：`D3Q27` 上 `static_assert` 禁止 `D3Q19MRT`（`src/lbm/lattice_model/D3Q27.h:38-39`）——用类型系统防错配。
- 主用 D3Q19（SRT/TRT/MRT 全覆盖）+ D3Q27（cumulant / 相场）；D3Q7、D2Q5 供标量扩散/热场。

### 2.2 碰撞模型谱系

| 谱系 | 类 | 文件:行 | 组织方式 |
|---|---|---|---|
| BGK/SRT | `collision_model::SRT` | `src/lbm/lattice_model/CollisionModel.h:80` | 单 ω，含 AMR 层级换算 `levelDependentRelaxationParameter`（`:105-107`） |
| 场依赖 ω | `collision_model::SRTField` | `CollisionModel.h:138` | ω(x) 存场，供 LES 动态黏度 |
| TRT | `collision_model::TRT` | `CollisionModel.h:212` | 双松弛 λ_e/λ_d + **magic number 参数化**（`constructWithMagicNumber` `:224`，默认 3/16 `threeSixteenth`） |
| MRT | `collision_model::D3Q19MRT` | `CollisionModel.h:333` | 19 维矩空间，**6 个自由参数映射 19 个松弛槽** `s_[0..18]`（`:343-364`），黏度由 `s9` 定（`:345 viscosityFromOmega(_s9)`），附 Pan 多孔模型构造器 `constructPan`（`:367`，文献 `:373`） |
| Cumulant | `collision_model::D3Q27Cumulant` | `CollisionModel.h:583` | **10 个独立松弛率** `omega_[0..9]`（`:590-616`），omega1 定黏度 |
| 湍流闭合 | `SmagorinskyLES` | `src/lbm/lattice_model/SmagorinskyLES.h:43` | 涡黏 + `SRTField` 动态 ω |

**实现组织（重要）**：碰撞核是**手写模板特化**，不是解释执行。每族一个 `CellwiseSweep.impl.h`，用宏 + type_traits 在编译期挑特化：

- `src/lbm/srt/CellwiseSweep.impl.h:47-57` 是"特化选择宏"（`WALBERLA_LBM_CELLWISE_SWEEP_SPECIALIZATION_SRT_9` 同时检查 tag、stencil、力项 tag、可压缩性、平衡态阶数）——设计上极值得借鉴：**"方法组合的合法性"编译期判定**；
- cumulant 核在 `src/lbm/cumulant/CellwiseSweep.impl.h:217-261`（速度空间→矩空间→累积量空间→碰撞→反变换，注释 `:392`）；
- 另有 `SplitPureSweep` / `SplitSweep`（`src/lbm/sweeps/SplitSweep.h:29`，纯碰撞/纯流动分离，供算例阶段化）。

**不存在的**：C++ 核心**无 KBC / 递归正则化 / 熵稳定碰撞**。全仓唯一 `kbc` 字样是遗留脚本的 lbmpy 配置字符串 `apps/benchmarks/UniformGridGPU/old_ideas/UniformGridGPU.py:60`（`'method': 'trt-kbc-n4'`，走外部 lbmpy 代码生成，无 C++ 实现）。`entropic` 命中仅出现在自由面再填充模型的文献注释（`src/lbm/free_surface/dynamics/PdfRefillingSweep.h:391`），非碰撞模型。

### 2.3 力项谱系（我们最关心的"加法约定"）

全部集中在 `src/lbm/lattice_model/ForceModel.h`，**tag 与实现一一对应**：

| 力项 | tag | 类 | 文件:行 | 约定 |
|---|---|---|---|---|
| 无 | `None_tag` `:105` | `None` | `ForceModel.h:142` | — |
| 简单常力 | `Simple_tag` `:106` | `SimpleConstant` | `:179` | `3 w_i c_i·F`；宏观 u 平移 F/2，平衡 u 不平移（注释 `:108-110`） |
| EDM（Kupershtokh 精确差分） | `EDM_tag` `:109` | `EDMField` | `:246` | `f^eq(u+F/2) - f^eq(u)`（`:319-322`） |
| Luo | `Luo_tag` `:111` | `LuoConstant` / `LuoField` | `:338` / `:405` | `3 w_i [(c-u)+3(c·u)c]·F`（`:385`） |
| **Guo** | `Guo_tag` `:113` | `GuoConstant` / `GuoField` | `:461` / `:561` | `w_i(1-1/(2τ))[(c-u)/cs²+ (c·u)c/cs⁴]·F`，**同时平移宏观速度与平衡速度**（注释 `:114-115`） |
| Latt 修正项 | `Correction_tag` `:117` | — | — | Jonas Latt 学位论文修正项 |

- 速度平移的统一入口：`struct ShiftedVelocity`（`ForceModel.h:263`）及其特化（`:272`），EDM/Guo 都复用——**这是我们实现力项时的正确抽象点**：把"力如何进入速度"和"力如何进入 PDF"解耦。
- 力密度/加速度的 AMR 层级缩放：`levelDependentBodyForce`（`:120`）、`levelDependentAcceleration`（`:130`）。

---

## 三、边界处理（逐一定位）

CPU 边界族在 `src/lbm/boundary/`，共 17 个边界头（脚本枚举：`Curved, DiffusionDirichlet, DynamicUBB, FreeDiffusion, FreeSlip, NoDiffusion, NoSlip, Outlet, ParserUBB, Pressure, SimpleDiffusionDirichlet, SimplePAB, SimplePressure, SimpleUBB, SimpleVelocityBoundary, UBB, VelocityBoundary`）。**统一接口**：`pushFlags/packCell/registerCell/treatDirection`（见任一实现），`treatDirection(x,y,z,dir,nx,ny,nz,mask)` 只写"邻居 ghost 格的反向 PDF"——极简且缓存友好。

| 型式 | 文件:行 | 实现要点 |
|---|---|---|
| **半程 bounce-back（NoSlip）** | `src/lbm/boundary/NoSlip.h:46`，`treatDirection` `:95-125` | `f_ī(ghost) = f_i(boundary cell)`（`:107`）；力用动量交换 `forceMEM = pdf_old + f_ī`（`:109-118`），可选模板参数 `StoreForce` |
| **速度 bounce-back（UBB，Ladd 型非 Zou-He）** | `src/lbm/boundary/UBB.h:232-280` | `f_ī(ghost) = f_i - 6 w_i ρ (c_i·u_w)`（可压缩/不可压缩两分支 `:243-270`）；速度可被外力修正 `AdaptVelocityToExternalForce`（`:250-251`） |
| **压力/密度边界（反 bounce-back + 平衡态对称部分）** | `Pressure.h:198-211` | `f_ī(ghost) = -f_i + 2 f^eq,sym(dir, u, ρ_target)`，rho 取自 `latticeDensityField_`；简化版 `SimplePAB.h:54`、`SimplePressure.h` |
| **Zou-He 速度入口** | **不存在**（全仓 `zou[-_ ]?he|ZouHe` 0 命中，唯一出现是 `src/lbm/field/PdfField.h:60` 的文献引用） | 替代路线：`VelocityBoundary.h:220-232` 把**预存的平衡态 PDF 向量**（`:182/:194/:211` 用 `EquilibriumDistribution::get(u,ρ)` 预算）整条拷进 ghost 格——是"平衡态外推速度边界"，**不是 Zou-He 的质量守恒闭环**。若要 Zou-He 式入口需自行实现 |
| **曲率/插值 bounce-back（Curved）** | `Curved.h:49`，`treatDirection` `:233` | δ="到壁面距离/格间距"（`:46`），权重 `deltaToWeight = (1-2δ)/(1+2δ)`（`:106`）→ **线性插值 bounce-back（BFL 类）**；每方向 δ 存 `Configuration`（`:65-83`） |
| 自由滑移 | `src/lbm/boundary/FreeSlip.h:46`，`treatDirection` `:88-140` | 由流体邻居搜索推壁面法向，再用 `mirrorX/Y/Z` 做**镜面反射**反射方向；含凹角退化处理（3 邻非流体时回退整向反射） |
| 出口 | `Outlet.h:47`，`treatDirection` `:89` | 外推型出口 |
| 表达式驱动速度边界 | `ParserUBB.h`（`treatDirection` `:486`） | 场解析表达式给 u，适合波浪/振荡入口 |
| 动态更新速度 | `DynamicUBB.h:55` | 每步改边界速度场 |
| 标量扩散族 | `DiffusionDirichlet.h` / `NoDiffusion.h` / `FreeDiffusion.h` / `SimpleDiffusionDirichlet.h` | 标量（温度/浓度）Dirichlet/Neumann |

**IBM（移动/浸没边界）三套，全部在 `src/lbm_mesapd_coupling/`：**

1. **MEM（动量交换）+ bounce-back**：`momentum_exchange_method/boundary/SimpleBB.h:144-247` —— BB 之后按 `forceMEM = pdf_old + pdf_new` 累加到粒子（`:235`），**并加伽利略不变性修正 `- correction * boundaryVelocity`**（`:242-244`），`forceScalingFactor_ = (Δx)²` 处理 AMR 缩放（`:138`）。
2. **曲壁 IBM**：`momentum_exchange_method/boundary/CurvedLinear.h` —— 线性插值 IB 边界，解析求壁面距离（`:198` 注释），不可用时回退 SimpleBB。
3. **PSM（部分饱和格法）**：`partially_saturated_cells_method/PSMSweep.h` + `ParticleAndVolumeFractionMapping.h`（体积分数映射）+ `codegen/PSMWrapperKernels.cu`（GPU 版）。
4. PDF 重建管理：`momentum_exchange_method/reconstruction/{Reconstructor.h,PdfReconstructionManager.h}`、`ExtrapolationDirectionFinder.h`；粒子↔块映射 `mapping/ParticleMapping.h`。

---

## 四、多相/多组元（重点 —— 本仓基本没有，逐项交代）

**先给结论：SCMP / MCMP / 伪势 / free-energy / 颜色梯度在本仓 C++ 与 Python 中全部为 0 命中**（探针 `shan_chen_pseudopotential`、`mcmp_multicomponent`、`free_energy` 均 0，见 `stats_table.md`）。仓里只有两条"多相"路线：**自由面（几何 VOF 型，REAL）** 与 **相场 Allen-Cahn（仅外挂 lbmpy 代码生成，本仓无 C++ 实现）**。

### 4.1 自由面（fill-level / VOF，`src/lbm/free_surface/` 约 40 文件）

| 环节 | 文件:行 | 方法 |
|---|---|---|
| 三相标记 | `free_surface/FlagDefinitions.h` | LIQUID / INTERFACE / GAS + fill level 场 |
| 单元转换 | `dynamics/CellConversionSweep.h` | 按 fill level 阈值在 interface↔liquid/gas 间翻转 |
| 质量平流 | `dynamics/functionality/AdvectMass.h`（经 `StreamReconstructAdvectSweep.h`） | 用 PDF 通量做 fill level 平流 |
| **PDF 重建（新 liquid 格）** | `dynamics/PdfReconstructionModel.h:62-75` | 6 种：`NormalBasedReconstructCenter` / `NormalBasedKeepCenter`（Koerner 2005，注释 `:37`）/ `OnlyMissing` / `OnlyMissingMin`（+3 个 fallback，`:70-75`） |
| **PDF 再填充（新 interface 格）** | `dynamics/PdfRefillingModel.h:61-67` | 5 种：`EquilibriumRefilling` / `AverageRefilling` / `EquilibriumAndNonEquilibriumRefilling` / `ExtrapolationRefilling` / `GradsMomentsRefilling`（各自的取平均/外推公式在 `PdfRefillingSweep.h:223-442`） |
| **过量质量再分配（质量守恒核心）** | `dynamics/ExcessMassDistributionModel.h:82-90` | `ExcessMassModel` 枚举（Evenly/Weighted × All/New/Old），实现在 `ExcessMassDistributionSweep.impl.h` |
| 法向 | `surface_geometry/NormalSweep.impl.h`（+ `ExtrapolateNormalsSweep`、`ObstacleNormalSweep`、`SmoothingSweep`） | fill level 梯度→法向，界面外推补法向 |
| **曲率** | `surface_geometry/CurvatureSweep.impl.h:78`（Parker-Youngs 中心差分）、`:146-192`（三角化/均值法向变体） | 由法向场做中心差分求 κ；含壁面湿润修正（`:89-103`） |
| 接触角 | `surface_geometry/ContactAngle.h:34`（预算 sin/cos）+ `DetectWettingSweep.h` | 角度制→弧度，湿润法向修正 |
| **表面张力 / 界面密度** | `dynamics/functionality/` 中 `computeDeltaRhoLaplacePressure`（由 `StreamReconstructAdvectSweep.h:131` 调用） | `Δρ = (1/cs²)·2σκ = 6σκ`，**且硬限幅 `maxDeltaRho = 0.1`**（超限只告警并截断） |
| 气泡模型 | `bubble_model/BubbleModel.h`、`MergeInformation.cpp:56-66` | 多气泡追踪/合并；`DisjoiningPressureBubbleModel.h:72-107` 用气泡内气体密度 + 分离压 |
| 质量核对 | `TotalMassComputer.h` | 全域液体质量统计（可做守恒审计） |

**密度比能力（诚实判定）**：自由面是**单相 LBM + 示踪 fill level**，气相不解动力学；界面处允许的密度偏差被**限幅在 ±0.1**（`computeDeltaRhoLaplacePressure` 的 `maxDeltaRho` 默认 0.1，越限只 `WALBERLA_LOG_WARNING`）→ **有效密度比 ≈ 1.1:1 量级，远不是高密度比多相**。这条直接决定：**我们的"高密度比"需求在这个仓里拿不到任何可借鉴实现**。

**spurious current 处理**：几何 VOF 路线**没有**伪势法的 spurious current 抑制机制（没有力平衡/伪势项）；它靠 `ExcessMassDistributionModel` 保质量 + `TotalMassComputer` 可审计。这是一个"就不存在"的结论，不是"没找到"。

### 4.2 相场 Allen-Cahn（**仅代码生成算例，本仓无 C++ 求解器实现**）

- 唯一实现处：`apps/benchmarks/PhaseFieldAllenCahn/multiphase_codegen.py:10`（`from lbmpy.phasefield_allen_cahn.kernel_equations import ...`），参数对象 `AllenCahnParameters`（`:34-37`）：`density_heavy=1.0, density_light=0.001, dynamic_viscosity_*=0.03, surface_tension=1e-4, mobility=0.02, interface_thickness=5, g=1e-6` → **密度比 1000:1 由相场参数给入**（不是自由面能力）。
- 双 LB 系统：相场 `D3Q15`（`h`）+ 流体 `D3Q19`（`g`）（`apps/benchmarks/PhaseFieldAllenCahn/multiphase_codegen.py:29-31`），界面追踪力/流体力分别由 lbmpy 的 `interface_tracking_force` / `hydrodynamic_force` 注入（同文件 import 行）。
- 密度线性混合 `ρ = ρ_L + C(ρ_H - ρ_L)`（同文件参数区）。
- **判断**：算法公式可参考，但**代码不在本仓**（在外部 `lbmpy` 包内），且其许可证未盘 → 只能当 reference，不能当"已有实现"直接提取。GPU 版同样只是 codegen target 切换。

---

## 五、GPU / 并行

### 5.1 数据布局

- PDF 场 = `GhostLayerField<real_t, Stencil::Size>`（`src/lbm/field/PdfField.h:74`），默认布局 `field::fzyx`（`src/lbm/field/PdfField.h:104` 的构造默认参数）→ **每个方向分量独立连续存储（SoA across directions）**，单方向内 x 最快变化 → 天然合并访问。
- GPU 侧对应 `src/gpu/GPUField.h`、`src/lbm_generated/gpu/GPUPdfField.h`；索引/访问器分层：`src/gpu/FieldIndexing3D.h`、`FieldAccessor3D.h`、`AlignedAllocation.h`（对齐分配）。**未核实**具体 `__ldg`/向量化用法（本次未细读 kernel），标 WITHHELD。

### 5.2 AA-pattern（even/odd 无拷贝交替流式）—— 本仓确有

- `python/lbmpy_walberla/alternating_sweeps.py:66-140`：`generate_alternating_lbm_sweep(...)` 生成**两个 kernel**（even/odd，`:104-117`），运行时用 `EvenIntegerCondition('timestep', kernel_even, kernel_odd)`（`:124`）选择；
- 配套 `TimestepTracker`（头文件注入见 `alternating_sweeps.py:60-61` 的 `headers()`）；in-place 判定 `is_inplace(streaming_pattern)`（`:111`）——非 in-place 时退化为同一 kernel。
- 这就是"融合 kernel + 无 field swap"的成熟工程化形态：**在生成器层解决，而不是手写两套代码**。

### 5.3 MPI 分区与负载均衡

- 块结构 AMR 骨架：`src/blockforest/SetupBlockForest.{h,cpp}`、`HilbertCurveConstruction.h`（Hilbert 曲线建块序）、`AABBRefinementSelection.h`（细化区选择）、`GlobalLoadBalancing.h`。
- 负载均衡：`src/blockforest/GlobalLoadBalancing.h:56` `MetisConfiguration`（`includeMetis_/forceMetis_`），底层 `src/core/load_balancing/{MetisWrapper,ParMetisWrapper}.{h,cpp}`。
- 通信原语：`src/communication/UniformPackInfo.h`（打包/解包抽象），LBM 专用生成式 pack info 在 `src/lbm/communication/`、`src/lbm_generated/communication/`；GPU 通信方案 `src/gpu/communication/NonUniformGPUScheme.h:327`（local communication 按块生成执行）。
- **通信隐藏**：`src/lbm_generated/gpu/BasicRecursiveTimeStepGPU.h:73-102` 按 level × stream 管理 CUDA 流（`streams_.resize(maxLevel_+1)`，`nStreams_` 每层多流，`:93-100`），实现块级通信/计算重叠。**是否支持多 GPU/多 rank 直通（GPU-direct MPI）未核实** → WITHHELD。

### 5.4 AMR / 非均匀网格

- `src/lbm/refinement/TimeStep.h:1579` `recursiveStep(level, executionCount)`（`:184` 入口调用）——2:1 层级递归推进 + 层间时间步配比；`src/lbm/refinement/LinearExplosion.h` 等。
- 力项/松弛率的层级换算见 §2.3（`levelDependentBodyForce`）。

---

## 六、可微 / 代码生成

- **无可微/AD**：全仓 `autodiff|torch|jax` 仅 3 处无关命中（`src/mesh/boundary/BoundarySetup.*`、`python/waLBerla_docs/conf.py`）→ **autograd 友好度 = 0**（C++ 模板 + 手写核，没有算子图/伴随）。反向模式要我们用 TensorLBM 自己的框架做。
- **符号推导 → kernel 生成（两条链）**：
  1. `sweepgen`（C++/Python DSL，生成 C++ 源码）：`sweepgen/src/sweepgen/{sweep.py,flow.py,prefabs/lbm_bulk.py,boundaries/grid_aligned.py,boundaries/sparse.py}` —— 生成"域循环 + 边界处理"骨架；
  2. `python/pystencils_walberla/` + `python/lbmpy_walberla/`：sympy 表达式 → pystencils AST → C++/CUDA 源码，模板 + Jinja（`pystencils_walberla/templates/`、`jinja_filters.py`），LBM 高层封装 `walberla_lbm_generation.py`、AA 交替 sweep `alternating_sweeps.py`。
- 生成物的**运行期接缝**：`src/lbm_generated/`（`boundary/D3Q19BoundaryCollection.h`、`sweep_collection/D3Q19SRT.cpp`、`gpu/*` 全部是生成产物）——"生成代码进仓 + 构建期重生成"（`CMakeLists.txt` 中 `waLBerla_generate_target_from_python`，见 `apps/benchmarks/PhaseFieldAllenCahn/CMakeLists.txt:4-12`）。这是我们做 kernel 生成时最值得学的一条工程实践：**生成产物可读、可 diff、可单独测试**（`tests/lbm_generated/` 5 个用例）。

---

## 七、与我们功能点的映射（feature_ledger）

{{MAPPING_TABLE}}

---

## 八、可吸收 Top3 + 风险

### Top1（fits）AA-pattern 无拷贝交替流式 —— 直接补我们的 GPU 融合 kernel 缺口

- 借鉴点：**"even/odd 双 kernel + 运行时整数条件选择"**（`python/lbmpy_walberla/alternating_sweeps.py:66-140`，选择节点 `:124`），配合 `TimestepTracker`；in-place 时省掉一次全场 swap（`:111`）。
- 对我们的具体用法：TensorLBM 的 GPU 融合核不必在 Python 里写两套物理，只需在 kernel 生成层输出 `even/odd` 两份索引映射，时间步计数器选路。
- 风险：waLBerla 把它压在代码生成器里（依赖 pystencils AST），我们是手写/半生成——需自己做一层"索引偏移表"抽象，别照搬生成链。

### Top2（fits）解析解驱动的场级误差评价面 —— 补我们的"测试与验证面"

- 借鉴点：`src/field/AccuracyEvaluation.h:72`（"comparison ... weighted by cell volume" 的通用误差评价模板）+ `AccuracyEvaluationLinePlot.h:125`（沿线的仿真/解析对比图）；
- 更可直接抄的**判据设计**：`apps/benchmarks/PoiseuilleChannel/PoiseuilleChannel.cpp:617-642` 把相对误差的**分母分成三种**（参考速度 / 最大速度 / 平均速度，注释 `:633-637`），且参考解现场算：圆管 `u = (a/4ν)(R²-r²)`（`:624-627`）、平板 `u = (a/2ν)(R²-y²)`（`:631-633`）。我们做 Poiseuille/MCMP 验证时按这套"三分母 + 现场解析解"落地，可避免"选分母挑好看"的造假空间。

### Top3（adapt）自由面的 PDF 重建/再填充模型族 + 过量质量再分配

- 借鉴点：把"新生成格子的分布函数怎么给"拆成**可枚举的模型族**（`PdfReconstructionModel.h:62-75` 6 种；`PdfRefillingModel.h:61-67` 5 种），并单列一个"质量守恒补丁"环节（`ExcessMassDistributionModel.h:82-90`）。
- 对我们的用法：冰区/甲板上浪的自由面场景，重建模型选择直接影响质量守恒与飞溅；这个"模型族 + 可枚举配置"的工程形态可直接适配（我们是 Python，用策略类/枚举即可）。
- 风险：其密度偏差被硬限幅 ±0.1（见 §4.1），只能做"水-气"近不可压自由面，**做不了破舱进水/大幅密度差**。

### 其他可吸收（次要）

- 边界接口极简（只写邻居 ghost 的反向 PDF，`src/lbm/boundary/NoSlip.h:95-125`）→ 我们 BC 基类可照此收窄；
- 编译期"方法组合合法性"宏（`srt/CellwiseSweep.impl.h:47-57`）→ 我们用 `static_assert`/类型约束表达"某碰撞只能配某格子"；
- 力项 `ShiftedVelocity` 单点抽象（`ForceModel.h:263/272`）→ 我们实现 Guo/EDM 时按同样切分。

### 风险清单

| 风险 | 说明 |
|---|---|
| **许可证** | GPL-3.0-or-later（`COPYING.txt:1` + `codemeta.json`）→ **代码不可复制**，只能取算法/设计。Python 生成链（lbmpy/pystencils）许可证本次未核实，禁止假设宽松 |
| 能力错位 | 我们最缺的 **SCMP/MCMP/高密度比** 本仓**完全没有**（§4 三个探针 0 命中）；不要指望从这个仓补 `GAP-LBM-MCMP-POISEUILLE` |
| 相场是"壳" | Allen-Cahn 只是 codegen 算例（`apps/benchmarks/PhaseFieldAllenCahn/`），实现体在外部 lbmpy 包内 |
| 自由面密度比 | 界面 Δρ ≤ 0.1 限幅 → 只适用近 1:1 密度 |
| 版本漂移 | 本仓为 GitHub 镜像（`README.md` 指向 i10git.cs.fau.de 为上游），commit dc16f31 后续 N 天可能已变；指针按此 commit 有效 |
| 未核实项 | GPU-direct MPI 支持、`__ldg`/向量化细节、lbmpy/pystencils 许可证 —— 均标 WITHHELD，勿当结论引用 |

---

## 附：方法学与自检

- 读码途径：`git clone --depth 1`（成功，56 MB / 1,699 代码文件）；未使用 GitHub REST。
- 统计与探针脚本：`docs/oss_inventory/walberla_scan.py` → `walberla_stats.json` + `stats_table.md` + 图 `figures/walberla_inventory.png`（左：模块 LOC 分布；右：能力矩阵红/绿）。
- 每条"有/无"结论都由脚本 grep 全树复核（`stats_table.md` 第二张表给出命中数与首个证据行），**不靠文档措辞**。
- 与既有草稿的差异（自我纠错记录）：本报告修正了仓内旧草稿 `docs/walberla_inventory_report.md` 的三处不实标注——① 把 `VelocityBoundary.h` 标成 "Zou-He 型"（实为**预存平衡态 PDF 拷贝**，见 §3 表）；② 称自由面密度比"典型 1000:1"（实为**界面 Δρ 限幅 0.1**，见 §4.1）；③ `Curved.h` 指针指向配置区而非 `treatDirection`（实现在 `:233`）。

[OSS-INV]
repo: lssfau/walberla
lang: C++17 (+Python codegen via sympy/lbmpy/pystencils, not in-repo)
license: GPL-3.0-or-later (COPYING.txt:1, codemeta.json) — copyleft, 代码不可复制仅可参考算法
methods: D2Q4/D2Q5/D2Q9/D3Q6/D3Q7/D3Q15/D3Q19/D3Q27 (src/stencil/*.h; src/lbm/lattice_model/D3Q27.h:66-69); SRT(BGK) CollisionModel.h:80 / SRTField:138 / TRT magic-number:212,224 / D3Q19MRT 6-param-19-slot:333-364 / D3Q27Cumulant 10-omega:583-616; forces None:142 Simple:179 EDM:246 Luo:338,405 Guo:463,561 + ShiftedVelocity:263/272; sweeps = hand-written template specializations per family (src/lbm/{srt,trt,mrt,cumulant}/CellwiseSweep.impl.h) with compile-time legality macros (srt/CellwiseSweep.impl.h:47-57)
multiphase: 无 SCMP/MCMP/伪势/free-energy/颜色梯度 (三个探针全 0 命中). 仅 free-surface fill-level VOF: src/lbm/free_surface/ (PDF reconstruction PdfReconstructionModel.h:62-75, refilling PdfRefillingModel.h:61-67, excess-mass ExcessMassDistributionModel.h:82-90, curvature CurvatureSweep.impl.h:78, 界面Δρ限幅0.1 via functionality/computeDeltaRhoLaplacePressure) + 相场 Allen-Cahn 仅外挂 codegen apps/benchmarks/PhaseFieldAllenCahn/multiphase_codegen.py:10,34-37 (ρ比1000:1, D3Q15相场+D3Q19流体:29-31)
boundary: 半程BB NoSlip.h:46/97-125 (含MEM力累加:112-121); 速度BB(Ladd型) UBB.h:232-280; 反BB压力 Pressure.h:198-211 + SimplePAB.h:54; 线性插值曲壁 Curved.h:49,106,233; 镜面自由滑移 FreeSlip.h:46,~97-140; 平衡态外推速度边界 VelocityBoundary.h:220-232 (非Zou-He); 无Zou-He实现(全树0命中); IBM: MEM SimpleBB.h:144-247(伽利略修正:241-246) + CurvedLinear.h + PSM PSMSweep.h
gpu: SoA-across-directions fzyx src/lbm/field/PdfField.h:74 + src/lbm/field/PdfField.h:104 + src/gpu/GPUField.h/FieldIndexing3D.h/AlignedAllocation.h; AA-pattern even/odd 双kernel+TimestepTracker python/lbmpy_walberla/alternating_sweeps.py:66-140(选择节点:124); 多CUDA流通信重叠 src/lbm_generated/gpu/BasicRecursiveTimeStepGPU.h:73-102; block-forest AMR + Metis/ParMetis (GlobalLoadBalancing.h:56, src/core/load_balancing/)
summary: waLBerla 是块结构 AMR + 自由面 + 颗粒流固耦合的 GPL-3.0 超算框架，LBM 方法谱系（8 格子/4 碰撞族/6 力项/插值曲壁/IBM）以手写模板特化 + 生成式 kernel 组织，工程形态（AA-pattern、编译期合法性、生成物入仓）对我们可借鉴。但它完全没有 SCMP/MCMP/高密度比多相，自由面密度偏差限幅 0.1，相场仅外挂 lbmpy codegen —— 我们的 MCMP 缺口无法从此仓补齐，只能取算法与工程范式。
[/OSS-INV]