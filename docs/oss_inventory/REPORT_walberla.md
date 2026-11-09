# 【盘点】walberla（lssfau/walberla）· 开源 LBM 技术盘点单项目深盘

- **任务锚点**：FEAT-OSS-INVENTORY-LBM（tier 3） / mission 8551
- **盘点对象**：`https://github.com/lssfau/walberla`（上游主库为 FAU GitLab `i10git.cs.fau.de/walberla/walberla`，GitHub 为镜像；`README.md:21` 明示 issue/协作走 GitLab）
- **代码对象**：HEAD `dc16f3130de2858f56e76bc9b8523710c94c23f0`，2026-08-04（`walberla_stats.json.head / head_date`，程序化采集）
- **取码方式（诚实记录）**：① `git clone --depth 1 https://github.com/lssfau/walberla.git` 首次调用在工具 120 s 超时边界被整组收割（exit=-9），但**对象已完整落盘**于 `/tmp/walberla_src`（`git rev-parse HEAD` = `dc16f31…`）；② 交叉校验走白名单 `api.github.com`：`GET /repos/lssfau/walberla/git/trees/HEAD?recursive=1` → HTTP 200，883 177 B，`sha=dc16f3130de2858f56e76bc9b8523710c94c23f0`，**与 clone 首 sha 一致**，`truncated=false`（2930 条目）。两条独立路径同一 commit ⇒ 取码成功、树完整。
- **未落入本仓工作树、未推任何分支**；克隆体在 `/tmp/walberla_src`（临时）。
- **证据性质**：**全部为静态源码证据（source-confirmed）**。本盘**没有任何运行验证**——waLBerla 需 CMake ≥ 3.24 + C++20 + MPI +（多相部分）`lbmpy 1.4.1` / `pystencils` Python 工具链（`CHANGELOG.md:5-16`），本机与集群均不具备，**故不构建、不跑算例**；凡未读到的一律标「未覆盖」，不含「推断当结论」。
- **测量脚本**：`docs/oss_inventory/make_walberla_stats.py` → `docs/oss_inventory/walberla_stats.json`（所有计数/行号程序化采集，零手抄）；指针自检：`docs/oss_inventory/verify_walberla_pointers.py`（把本报告每条 `path:line` 回读原文核对并打印命中文本）。
- **知识库同域先扫**：本仓 `docs/oss_inventory/` 已有 lbmpy / ludwig / TCLB / listLBM / MPLBM-UT / Sailfish 等同战役报告；**「walberla」在本仓既有产物中仅以「被移植对象」出现**（`docs/FS_WALBERLA_ALIGNMENT_REPORT.md` 是我们自由面对齐它的说明），无 waLBerla 本体盘点，本单不重复。

---

## 一、概况

| 项 | 值 | 证据 |
|---|---|---|
| 主语言 | **C++20**（`src/` 1103 个 `.h/.cpp/.cu`，**322 410 行**）+ **Python 代码生成链**（`python/` 119 文件 10 812 行；`sweepgen/` 19 文件 4 666 行） | `walberla_stats.json.totals`；`CHANGELOG.md:5`（CMake 3.24 起） |
| 规模 | `apps/` 170 文件 62 707 行；`tests/` 350 文件 71 104 行（其中 328 个 `.cpp`，24 个测试目录） | `walberla_stats.json.totals / tests_ci_apps` |
| 模块 LOC 榜 | core 111 236 · **lbm 54 126** · mesa_pd 24 018 · blockforest 21 654 · lbm_generated 21 278 · field 18 427 · geometry 10 452 · lbm_mesapd_coupling 8 414 · gpu 7 871 · boundary 6 093 | `walberla_stats.json.modules_cpp` |
| LBM 子集 | `src/lbm` 202 文件 54 126 行；**`src/lbm/free_surface` 71 文件 15 063 行**；`src/gpu` 55 文件 7 871 行；边界两处（`src/boundary` + `src/lbm/boundary`）合计 31 文件 10 153 行 | `walberla_stats.json.totals` |
| 许可证 | **GPL-3.0**（`COPYING.txt:1-2` "GNU GENERAL PUBLIC LICENSE Version 3"） | `walberla_stats.json.license` |
| 活跃度 | **活跃**。7.3（2026-07-21）刚落地：CMake 现代化、**弃用 Windows/MSVC**、GCC 15 / LLVM 22 支持、引入 **SweepGen**（pystencils 2.0 基础，15 个 MR）、实验性 `walberla::v8` 核心库、LBM 静态加密多项修 bug | `CHANGELOG.md:3-30` |
| CI / 工程门 | GitLab CI 单文件流水线 `.gitlab-ci.yml`；`.github/` 仅 1 条目（镜像侧）；`tests/CMakeLists.txt` 组织 24 个测试目录 | `walberla_stats.json.tests_ci_apps.ci_files` |
| 定位一句话 | **块结构（block-forest）大规模多物理 HPC 框架**：骨架 = 字段 + sweep + timeloop + flag 边界 + 块森林负载均衡；LBM 只是其中一个（最大）物理模块；多相走**两条独立路线**——C++ 手写**自由面**（fill-level 几何法）与 Python 代码生成的**相场 Allen-Cahn**（`lbmpy` 前端），**没有** Shan-Chen / 颜色梯度 / 自由能 LBM | 全库结构；`src/lbm/`、`src/lbm/free_surface/`、`apps/showcases/PhaseFieldAllenCahn/` |

### 许可证对我们的可用性判断（关键结论，与 lbmpy 单同类）

GPL-3.0 = **强 copyleft**，非宽松许可。三级判定：

1. **读码提炼算法/公式/参数标定关系** —— ✅ 允许（版权不保护思想）。waLBerla 尤其值得这样用的是它的**无量纲化参数反解链路**与**模型族枚举设计**（见 §八）。
2. **复制/改写源码并入 TensorLBM 并分发** —— ❌ 不可接受：一行 C++ 搬进去即触发整体 GPL-3.0，许可自由度永久让出。
3. **进程边界调用** —— ⚠️ C++ 链接基本被认定为衍生作品；即使做成独立可执行（它本身有 `apps/` 可执行体），分发时仍须照 GPL 提供源码，工程上不划算。

**执行口径**：本仓对我们 = **架构参考 + 公式/标定来源 + 反例库**（它踩过的坑直接省我们调试成本，见 §八 风险）。
**额外要点**：其多相部分本身就是 `lbmpy` 的 Python 代码（`apps/showcases/PhaseFieldAllenCahn/CPU/multiphase_codegen.py:11-15`），而 `lbmpy` 也是 GPL-3.0-or-later（见 `docs/oss_inventory/REPORT_lbmpy.md`）⇒ **多相这条路「换个仓抄」抄不到：两仓同源同许可。**

---

## 二、方法谱系

### 2.1 格子（stencil）

C++ 侧只有 4 个具名格子（脚本枚举：`walberla_stats.json.lattice_model_files`），全部 CRTP 模板（`LatticeModelBase.h`）：

| 格子 | 位置 | 备注 |
|---|---|---|
| **D2Q9** | `src/lbm/lattice_model/D2Q9.h` | |
| **D3Q15** | `src/lbm/lattice_model/D3Q15.h` | |
| **D3Q19** | `src/lbm/lattice_model/D3Q19.h` | 生产主力 |
| **D3Q27** | `src/lbm/lattice_model/D3Q27.h` | 自由面用它（`src/lbm/free_surface/dynamics/SurfaceDynamicsHandler.h:118` 注释：D3Q27 因 excess-mass 分配需要全 27 邻居） |

- **无 D3Q7 / D2Q7 / D3Q39（C++ 侧）**。D3Q7 只出现在 Python 生成侧的热 LBM：`apps/showcases/Thermocapillary/thermocapillary_codegen.py:49 stencil_thermal = LBStencil(Stencil.D3Q7)` ⇒ 我们要 D3Q7 热耦合，**不能从它的 C++ 侧取**。
- 加密一致性锚点：`levelDependentRelaxationParameter`（`src/lbm/lattice_model/CollisionModel.h:47`）在 `configure()` 里把 ω 按 2:1 网格尺度重标（SRT：`CollisionModel.h:93-100`）；配套 `src/lbm/refinement/`（`EqualLevelBorderStreamCorrection.h`、`CurlBasedLevelDetermination.h`、`VorticityBasedLevelDetermination.h`）。

### 2.2 碰撞算子

全部集中在单文件 `src/lbm/lattice_model/CollisionModel.h`（4 tag + 5 类，程序化枚举 `walberla_stats.json.collision_model_classes`）：

| 谱系 | 类（行） | tag（行） | 关键实现点 |
|---|---|---|---|
| **SRT / BGK** | `class SRT` `:80` | `SRT_tag` `:40` | `omega_`+`viscosity_` 双存；`viscosityFromOmega` `:62` 即 `(1/ω−1/2)/3`；`omega_bulk()==omega()` `:111` ⇒ **SRT 下体粘性=剪切粘性** |
| SRT 场变体 | `class SRTField` `:138` | 同上 | ω 存成字段（空间可变 / 湍流模型回写） |
| **TRT** | `class TRT` `:212` | `TRT_tag` `:41` | 偶/奇两率 λ_e/λ_d；`constructWithMagicNumber` `:226`，magic 默认 `threeSixteenth`（3/16）；`lambda_d_ = lambda_d(lambda_e_, magicNumber_)` `:243` |
| **MRT（D3Q19）** | `class D3Q19MRT` `:333` | `MRT_tag` `:42` | 19 个松弛率 `s_[0..18]` `:347-365`；注释给出物理命名 `s_e, s_eps, s_q, s_nu, s_pi, s_m` `:342-343`；加密重标定 `:406-431`（按 `s_[9]`（=s_nu）线性缩放其余率） |
| **Cumulant（D2Q9/D3Q19/D3Q27）** | `class D3Q27Cumulant` `:583` + `src/lbm/cumulant/CellwiseSweep.impl.h`（896 行） | `Cumulant_tag` `:43` | 手写完整实现：取 10 个松弛率 `omega1..omega10`（`src/lbm/cumulant/CellwiseSweep.impl.h:64-73`）→ 速度空间→矩空间 `:217` → **转 cumulant 空间** `:230` → **cumulant 空间碰撞** `:261-310`（含 `CS_200−CS_020` 类不可压修正 `:274-276`，10 组独立松弛 `:266-310`）→ 反变换 `:392` |

**明确缺失（负面证据，grep 计数=0）**：`walberla_stats.json.collision_variants_present = {kbc: 0, regularized: 0, shan_chen: 0}` ⇒ **无 KBC / KBC-N4、无正则化（Latt–Chopard / recursive-regularized）碰撞、无 Shan-Chen**。
→ 对我们的含义：**KBC 与正则化必须自己写**（lbmpy 也缺，见 `REPORT_lbmpy.md`）；可借的是它的**扩展方式**：新 tag + trait + `CellwiseSweep` 特化（`src/lbm/sweeps/CellwiseSweep.h` 与 `src/lbm/srt/cell_operations/DefaultCellOperation.impl.h` 的特化模式）。

同目录并列三种 sweep 组织形态（不同内存/通信取舍）：`CellwiseSweep`（融合流+碰撞）、`SplitSweep`、`SplitPureSweep` —— `src/lbm/sweeps/{CellwiseSweep,SplitSweep,SplitPureSweep}.h` 与 `src/lbm/{srt,trt,mrt}/{CellwiseSweep,SplitSweep,SplitPureSweep}.impl.h`。

### 2.3 力项（forcing）—— 谱系比碰撞还全，且**公式直接写在注释里**

`src/lbm/lattice_model/ForceModel.h`：8 类 / 6 tag（`walberla_stats.json.force_model_classes / force_model_tags`）：

| 力模型 | 类（行） | tag（行） | 原文语义 |
|---|---|---|---|
| 无力 | `class None` `:142` | `None_tag` `:103` | |
| 简单力 | `class SimpleConstant` `:179` | `Simple_tag` `:104` | 把 F 直接加进碰撞（不保二阶） |
| **EDM（Kupershtokh 精确差分）** | `class EDMField` `:246` | `EDM_tag` `:108` | `:109` *"Shifts the macroscopic velocity by F/2, but does not change the equilibrium velocity."* |
| **Luo** | `LuoConstant` `:338` / `LuoField` `:405` | `Luo_tag` `:110` | `:111` *"F_i = w_i·[(c_i−u)/cs² + (c_i·(c_i·u))/cs⁴]·F"*；同样只移宏观速度（`:112`） |
| **Guo** | `GuoConstant` `:461` / `GuoField` `:561` | `Guo_tag` `:113` | `:114` *"F_i = w_i·[1 − 1/(2τ)]·[(c_i−u)/cs² + (c_i·(c_i·u))/cs⁴]·F"*；`:115` **宏观速度与平衡态速度都移位 F/2** |
| **Latt 修正项** | `class Correction` `:649` | `Correction_tag` `:117` | `:117` *"correction terms proposed in Jonas Latt's thesis"* |

- Guo 力项落到 kernel 处：`src/lbm/srt/cell_operations/DefaultCellOperation.impl.h:165-166`（与 `:189-190`），形式 `3·w_d·(1−ω/2)·((c−u+3(c·u)c)·F)` ⇒ **`(1−ω/2)` 因子在代码里可见**，与 `:114` 注释一致。
- 方向无关项（EDM/Luo 共用）`directionIndependentTerms()`：`src/lbm/lattice_model/ForceModel.h:498-509`，含 `(2/d)·½(2−ω)`（`:508`）与 `(1/d)(2−ω_bulk)`（`:509`）两支；无方向无关项时退化到 `NoDirectionIndependentTerms`（`:121`） ⇒ **体粘性 ω_bulk 与剪切 ω 分离**在力项层是显式的。
- **坑（值得进 TRAPS）**：`EDM/Luo` 与 `Guo` 的「速度是否移位」语义不同（`:109` vs `:115`）。移植时混用会静默产生一阶误差——这正是各类开源 LBM「对不上解析解」的最常见根因。
- **无独立 shifted-velocity 类**（`u* = u + F/(2ρ)` 没单独成型）；移位语义散在各力模型的 `forceTerm()`/`directionIndependentTerms()` 里，靠 tag 分派。

### 2.4 湍流 / 亚格子

`class SmagorinskyLES` `src/lbm/lattice_model/SmagorinskyLES.h:43`：把涡粘折算回 ω 字段（`operator()` `:105`），与 `SRTField` 配对；作为**独立 sweep 插在碰撞前**（`src/lbm/free_surface/dynamics/SurfaceDynamicsHandler.h:254` "collision after Smagorinsky sweep"）。**只有 Smagorinsky 一种**，无 WALE / Vreman。

---

## 三、边界处理

### 3.1 边界框架（先讲清「钩子怎么挂」）

- 通用骨架 `class BoundaryHandling` `src/boundary/BoundaryHandling.h:58`：flag 字段标边界类型 → 每 cell 每方向调 `treatDirection(x,y,z,dir,nx,ny,nz,mask)`（签名见 `src/lbm/boundary/UBB.h:110`）→ 边界 cell 的**状态量本身参与通信**（`packCell/registerCell` 回调，`UBB.h:100-104`）。
- 边界集合是**模板参数列表（编译期）**：`src/lbm/boundary/factories/ExtendedBoundaryHandlingFactory.h`；tutorial 一次实例化 8 种：`apps/tutorials/lbm/06_LBBoundaryCondition.cpp:136-141`（`NoSlip, SimpleUBB, UBB, DynamicUBB, SimplePressure, Pressure, Outlet, SimplePAB`）。
- 这层设计对我们「平台类=案例组织」有直接参考：**边界 = flag + 模板 + 配置文件**，不是散落的 if。

### 3.2 逐型定位

| 型式 | 类（文件:行） | 机制 |
|---|---|---|
| **bounce-back（无插值）** | `class NoSlip` `src/lbm/boundary/NoSlip.h:46`（`treatDirection` `:95`） | `treatDirection` 内 f_new = f_opp |
| **BB + 移动壁面速度（简单版）** | `class SimpleUBB` `src/lbm/boundary/SimpleUBB.h:49` | 速度硬给定；模板开关 `StoreForce` 用于测壁面动量通量（`:48`、`:70`）；`AdaptVelocityToExternalForce` 默认 false（`:48`） |
| **UBB（带速度/力场）** | `class UBB` `src/lbm/boundary/UBB.h:50` | 模板参数 `AdaptVelocityToExternalForce`、`StoreForce`（`:49`）；速度存 `GhostLayerField`（`:55`） |
| **动态（移动）UBB** | `class DynamicUBB` `src/lbm/boundary/DynamicUBB.h:55` | 每步可更新壁面速度 |
| **插值/曲边 bounce-back（Bouzidi 型）** | `class Curved` `src/lbm/boundary/Curved.h:49` | 类首注释定义 **"delta = distance to wall / distance between cell centers"** `:46`；即分数距离 q 插值 |
| **自由滑移** | `class FreeSlip` `src/lbm/boundary/FreeSlip.h:46` | 镜面反射 |
| **压力（密度）Dirichlet** | `class Pressure` `src/lbm/boundary/Pressure.h:49`（场变体）/ `SimplePressure.h` | 给格点密度；内部存 `latticeDensityField_`（`Pressure.h:128`） |
| **出流 / PAB（压力反 bounce-back）** | `class SimplePAB` `src/lbm/boundary/SimplePAB.h:54`；`class Outlet` `src/lbm/boundary/Outlet.h:47` | SimplePAB **恢复碰撞前 PDF**，故有自检：ω 必须 ∈(0,2) 且接近 1 时不可用/告警（`:96-111` "the pre collision PDFs can not be restored"）；tutorial 里作出口（`06_LBBoundaryCondition.cpp:321-326`） |
| 扩散类（热/浓度） | `SimpleDiffusionDirichlet.h`、`DiffusionDirichlet.h`、`FreeDiffusion.h`、`NoDiffusion.h` | 标量 Dirichlet / Neumann |

### 3.3 Zou-He：**无此命名（负面证据）**

- `grep -rni "zou"` 于 `src/lbm/boundary/` **0 命中**。
- 功能上最接近的是 `SimpleUBB`（速度给定 + 反弹非平衡部分）与 `Pressure/SimplePressure`（压力给定）；**但没有 Zou-He 那套「用已知方向的非平衡量反解未知方向」显式公式**，走的是「反弹 + 速度/密度修正」。
- ⇒ 对我们的含义：**Zou-He 得自己写**；waLBerla 提供的等价替代是「UBB/PAB + 自检」这套工程化边界，可作**验证对照**（同算例两种边界应收敛到同一解）。

### 3.4 曲率 / 接触角（两套独立实现，不可混用）

1. **自由面**：`class ContactAngle` `src/lbm/free_surface/surface_geometry/ContactAngle.h:34` —— **只缓存 sin/cos**（`:39`、`:45-51`）；几何修正走 `DetectWettingSweep.h` + `ObstacleFillLevelSweep.h` / `ObstacleNormalSweep.h`。
2. **相场**：`ContactAngle(contact_angle_in_degrees, interface_thickness, data_type)` `apps/showcases/PhaseFieldAllenCahn/CPU/multiphase_codegen.py:114-115` → `generate_boundary(ctx,'ContactAngle', …)` `:155-156`；**公式本体在 `lbmpy.phasefield_allen_cahn.contact_angle`（外部仓）**，本仓只有调用点。热毛细侧同型：`apps/showcases/Thermocapillary/thermocapillary_codegen.py:17` 与 `:232`。

### 3.5 IBM / 移动颗粒耦合（三套，均在 `src/lbm_mesapd_coupling`）

| 方法 | 目录 | 要点 |
|---|---|---|
| **MEM（动量交换法）** | `src/lbm_mesapd_coupling/momentum_exchange_method/` | 边界两类：`boundary/SimpleBB.h`、`boundary/CurvedLinear.h`（曲边线性 MEM）；**缺失 PDF 重建独立成子模块** `reconstruction/{PdfReconstructionManager.h, Reconstructor.h, ExtrapolationDirectionFinder.h}`；另有 `MovingParticleMapping.h` |
| **PSM（部分饱和格子法）** | `src/lbm_mesapd_coupling/partially_saturated_cells_method/` | `PSMSweep.h` + `ParticleAndVolumeFractionMapping.h` + **代码生成版** `codegen/`：`PSMCodegen.py` → `PSMWrapperKernels.{cu,hip,cpp}` + `PSMWrapperSweeps{CPU,GPU}.h` ⇒ **同一物理一份 Python 描述生成 CPU/CUDA/HIP 三后端** |

---

## 四、多相 / 多组元（**重点**）

### 4.1 先给结论：有什么、没有什么（负面证据是本节主要价值）

| 手法 | 本仓 | 证据 |
|---|---|---|
| **SCMP（假势 / Shan-Chen）** | ❌ **无** | `grep -rni "shan.?chen\|pseudo.?potential\|interparticle\|psi("` over `src/ apps/` → **0 命中**（`walberla_stats.json.multiphase.sc_absent_hits = 0`） |
| **MCMP（多组分多相）** | ❌ **无** | 同上；无「组分」数据结构（无 per-component PDF 字段族、无组分索引） |
| **自由能（free-energy）LBM** | ❌ **无** | `free.?energy` 0 命中 |
| **颜色梯度（color-gradient）** | ❌ **无** | `color.?gradient` 0 命中 |
| **Cahn-Hilliard** | ❌ **无** | 0 命中（对照：ludwig 有 CH，见 `docs/oss_inventory/OSS-INV-ludwig.md`） |
| **相场 Allen-Cahn（守恒型，两相）** | ✅ **有**，但**仅经 lbmpy 代码生成**，本仓 C++ 侧无手写实现 | `apps/showcases/PhaseFieldAllenCahn/`（CPU + GPU + benchmark 三份）；依赖 `lbmpy.phasefield_allen_cahn.*`：`apps/showcases/PhaseFieldAllenCahn/CPU/multiphase_codegen.py:11-15` |
| **自由面（free-surface，fill-level 几何法）** | ✅ **有，且是全场最重资产**（71 文件 15 063 行） | `src/lbm/free_surface/`；11 个 showcase `.cpp`（`apps/showcases/FreeSurface/`） |

**⇒ 对 owner 令「多相重点」的直接回答：waLBerla 不是多相 LBM 仓。** 它是「两相相场 + 自由面 + 颗粒耦合」仓。我们的 `GAP-LBM-MCMP-POISEUILLE`（两组分分层 Poiseuille）在它这里**没有对应实现**——它连「两组分」这个概念都没有。

### 4.2 相场 Allen-Cahn：完整实现路径（唯一可做接口级复用的多相路线）

变量与方程装配（`apps/showcases/PhaseFieldAllenCahn/CPU/multiphase_codegen.py`）：

| 环节 | 做法 | 指针 |
|---|---|---|
| 变量 | 相场 `C∈[0,1]`（+`C_tmp` 乒乓）；相场分布 `h`（**MRT**，D3Q27）；水动力分布 `g`（**MRT**，D3Q27）；速度 `u`；密度 `density_field` | `:39-50` |
| **相场 LB 松弛率 ⇔ 迁移率** | `1/(0.5 + 3·mobility)` | `:57` |
| **变密度平衡态（密度比能力的唯一机制）** | `density = rho_L + C·(rho_H − rho_L)`（**C 线性插值**） | `:59-61` |
| 体力 | `body_force[1] = g_grav · density`（**重力 × 局部密度** ⇒ 变密度浮力） | `:63-64` |
| **粘度随相变** | `omega = parameters.omega(C)` | `:66` |
| 相场 MRT 松弛率表 | `rates = [0] + [ω_mobility]·D + [1.0]·(Q−D−1)` ⇒ **零号矩不松弛（质量守恒）、只松弛一阶矩** | `:72-74` |
| **两个力项分离（关键结构）** | ① `force_h = interface_tracking_force(C, stencil, parameters)`（界面追踪/反扩散通量）`:93`；② `hydro_force = hydrodynamic_force(C, method, parameters, body_force)`（**CSF 表面张力 + 重力**）`:94` | `:93-94` |
| 力项注入 | `add_interface_tracking_force(rule, force_h)` `:104`；`hydrodynamic_force_assignments(...)` `:106` + `add_hydrodynamic_force(rule, assignments, C, g, parameters)` `:112` | `:104-112` |
| 流式模式 | 相场 `stream_pull_collide` `:80`；水动力 `collide_stream_push` `:86` ⇒ **两套场反向流式**，靠时间循环错位做通信重叠（§5.3） | `:80, :86` |
| 接触角 | 构造 `:114-115`；生成 boundary kernel `:155-156` | 同 |
| 墙边界 | 两套 NoSlip 分别生成（pull / push 各一） | `:138, :143` |
| 通信包 | 每场一份 `generate_lb_pack_info`（pull / push）+ 标量场 `generate_pack_info_for_field` | `:147-153` |

**密度比 / 粘度比能力（有算例参数为证）**：

| 算例 | 密度比 | 粘度比 | 其它 | 指针 |
|---|---|---|---|---|
| Taylor bubble | **744** | **4236** | Morton 0.015、界面宽 3 格、mobility 0.08 | `apps/showcases/PhaseFieldAllenCahn/CPU/multiphase_taylor_bubble.py:16-28` |
| RTI（Rayleigh–Taylor） | **1000** | **100** | Atwood = 1 | `apps/showcases/PhaseFieldAllenCahn/CPU/multiphase_RTI_3D.py:43-56` |
| Rising bubble | 按 Re=40 无量纲反解 | — | 半径 16 格 | `apps/showcases/PhaseFieldAllenCahn/CPU/multiphase_rising_bubble.py:34-44` |

⇒ **本仓最值得吸收的硬数字**：`ρ比 = 1000 / 744` 的相场算例是跑通的配置（算例脚本 + benchmark 二进制 `apps/benchmarks/PhaseFieldAllenCahn/benchmark_multiphase.cpp`），给了我们「相场路线在高密度比下需要多大界面宽度 / 迁移率」的量级锚点。

**参数反解链路（对我们标定直接可用，但函数体在 lbmpy）**：`calculate_dimensionless_rising_bubble`（`apps/showcases/PhaseFieldAllenCahn/CPU/multiphase_rising_bubble.py:6` / `:40`）、`calculate_parameters_rti`（`apps/showcases/PhaseFieldAllenCahn/CPU/multiphase_RTI_3D.py:9` / `:48`）、`calculate_parameters_droplet_migration`（`apps/showcases/Thermocapillary/droplet_migration2D.py:9`）——本仓只有调用与非量纲输入（Re / Morton / Bond / Atwood）。

### 4.3 热毛细（Marangoni）：相场 + 温度场 + RK

`apps/showcases/Thermocapillary/thermocapillary_codegen.py`：

- 三套分布：相场 D3Q19（`:47`）、水动力 D3Q19（`:48`）、**温度 D3Q7**（`:49`，`Method.CENTRAL_MOMENT` `:119`）。
- **RK 时间推进**：`get_runge_kutta_update_assignments` 生成 RK1/RK2/RK3/RK4 多级更新（`:21` 导入，`:216-224` 调用），用于 AC 方程 + 温度平流耦合步进 ⇒ **相场方程走基于有限差分的 RK，而非 LB**（混合方案）。
- 接触角 + 热毛细力：`:17`、`:232`；**微通道有解析解**：`analytical_solution_microchannel`（`microchannel2D.py:8, 72`）⇒ 多相里**唯一带解析解对标的算例**；结果落 CSV（`:239-245`），但**仓内无误差计算脚本**（只写 CSV，对比靠外部）。

### 4.4 自由面（free-surface）：C++ 手写几何法，工程完整度是真正的护城河

**状态模型**：flag 三态（liquid / interface / gas）+ 连续 **fill level**（`src/lbm/free_surface/FlagDefinitions.h`、`FlagInfo.h`、`InterfaceFromFillLevel.h`）。

**时间步流水线**（顺序即算法；脚本从 `SurfaceDynamicsHandler.h` 抽出 38 个 sweep 注册点，见 `walberla_stats.json.free_surface.sweep_order`，行号为该文件行号）：

```
边界处理(:140) → 力加权 forceDensity(:155 / :165，含 codegen 版)
→ StreamReconstructAdvect 融合(:188)   ← 自由面专属：流式+界面重建+质量平流融合
→ 碰撞/流式（通用 / lbm_generated 生成版）(:220-267)，可选 Smagorinsky(:254)
→ 胞元转换 cell conversion(:280)        ← fill level 越阈 → 相变
→ 回填 refilling(:298 / :307 / :320 / :329 / :339)  ← 气→界面 cell 的 PDF 重初始化
→ excess mass 分配(:358 / :378 / :400)  ← 转换剩余质量守恒回填
→ 转换标志复位(:417) → 块状态更新(:425)
```

| 子模型族 | 实现枚举（均可由 `.prm` 选） | 指针 |
|---|---|---|
| **PDF 重建（界面 cell）** | `NormalBasedReconstructCenter` / `NormalBasedKeepCenter` / `OnlyMissing` / `All` / `OnlyMissingMin`（后者带 `-N-fallback` 复合名解析） | `src/lbm/free_surface/dynamics/PdfReconstructionModel.h:62-67`；名字解析 `:78-90` |
| 重建 fallback | `Largest` / `Smallest` / `NormalBasedKeepCenter` | `:70-74` |
| **PDF 回填（气→界面）** | `EquilibriumRefilling` / `AverageRefilling` / `EquilibriumAndNonEquilibriumRefilling` / `ExtrapolationRefilling` / `GradsMomentsRefilling` | `src/lbm/free_surface/dynamics/PdfRefillingModel.h:61-66`；语义注释 `:33-50` |
| **多余质量分配** | `Evenly{All,New,Old}Interface` / `Weighted{All,New,Old}Interface` / `EvenlyAllInterfaceAndLiquid` / `Evenly{All,New}InterfaceFallbackLiquid`（9 种） | `src/lbm/free_surface/dynamics/ExcessMassDistributionModel.h:85-94` |
| **曲率** | `FiniteDifferenceMethod` / `LocalTriangulation` / `SimpleFiniteDifferenceMethod` | `src/lbm/free_surface/surface_geometry/CurvatureModel.h:42 / :59 / :76` |
| 法向 | `NormalSweep` + `ObstacleNormalSweep` + `ExtrapolateNormalsSweep` + `SmoothingSweep` | `surface_geometry/` |
| 湿润 / 接触角 | `DetectWettingSweep.h` + `ObstacleFillLevelSweep.h` + `src/lbm/free_surface/surface_geometry/ContactAngle.h:34` | 配置实证 `apps/showcases/FreeSurface/DropWetting.prm:20-21`（`enableWetting true; contactAngle 45;`） |
| 气泡模型（合并/分裂） | `bubble_model/{BubbleModel,FloodFill,RegionalFloodFill,DisjoiningPressureBubbleModel,MergeInformation,NewBubbleCommunication}.h` | 配置实证 `DropWetting.prm:27-29`（`enableBubbleModel` / `enableBubbleSplits`） |
| 质量守恒诊断 | `TotalMassComputer.h`、`MaxVelocityComputer.h`、`LoadBalancing.h`（按 fill level 加权） | `src/lbm/free_surface/` |

**「模型族 × 配置开关」的实证**（`apps/showcases/FreeSurface/DropWetting.prm:25-33`）：

```
pdfReconstructionModel        OnlyMissing;
pdfRefillingModel             EquilibriumRefilling;
excessMassDistributionModel   EvenlyAllInterface;
curvatureModel                FiniteDifferenceMethod;
useSimpleMassExchange         false;
enableBubbleModel / enableBubbleSplits
cellConversionThreshold 1e-2 / cellConversionForceThreshold 1e-1
```

⇒ **把「自由面会不会漏质量/稳不稳」从「单一实现的运气」变成「可切换对照的一组实现」**——这是自由面工程化的核心，也是我们最该学的组织方式（不是学某一条公式）。

**spurious current 处理：本仓未见显式处理（按「未覆盖」记）**。无 well-balanced / 离散平衡修正、无 pressure-tensor 修正、界面处力无特殊加权（`dynamics/ForceDensitySweep.h` 只做力密度到 cell 的加权）。寄生流抑制靠 `SmoothingSweep` + 法向/曲率平滑 + `ExcessMassDistribution` 的加权分配等**间接手段**。**我们没找到可引用的显式公式层，不编造。**
---

## 五、GPU / 并行

### 5.1 数据布局

- 字段基类 `GhostLayerField`（`src/field/GhostLayerField.h`）**同时支持 AoS(`zyxf`) 与 SoA(`fzyx`)，默认 `fzyx`**：`src/field/GhostLayerField.h:81`（`const Layout & layout = fzyx`）；`src/field/Field.h:105` 同。
- GPU 侧 `class GPUField`：注释直言 *"Supports Array-of-Structures (AoS, zyxf) layout and Structure-of-Arrays (SoA, fzyx) layout"* `src/gpu/GPUField.h:54`；**strides 由 layout 决定**：`xStride()/yStride()/zStride()/fStride()` `src/gpu/GPUField.h:99-105`（fzyx 下 `fStride = xSize·ySize·zSize`）。
- **pitched memory**：`src/gpu/GPUField.impl.h:58-70` —— `usePitchedMem=true` 走 `allocate_pitched_with_offset` 的 `pitchedPtr_`（`:62`），否则 `gpuMalloc` 平铺（`:70`）；对齐细节在 `src/gpu/AlignedAllocation.cpp`。
- **AA-pattern（四块交替 / 交替 pull-push）**：本仓**不以 "AA" 命名**（grep `aa.?pattern` 于 `python/` 0 命中）。等价机制是 `python/lbmpy_walberla/alternating_sweeps.py`：用 `EvenIntegerCondition`/`OddIntegerCondition`（`:16`、`:32`）在**编译期生成两种时间步分支**交替执行 pull/push kernel ⇒ in-place（双场而非三场）流式省内存；底层依赖 `lbmpy.advanced_streaming.Timestep / is_inplace`（`:8`）。
- 生成侧偏移由 `generate_sweep` 的 `kernel_type` 显式指定：`stream_pull_collide` / `collide_stream_push`（相场/水动力各用一种：`apps/showcases/PhaseFieldAllenCahn/CPU/multiphase_codegen.py:80, :86`）。

### 5.2 MPI 分区与负载均衡（block-forest）

- 分区单元 = **block**（块森林）：`src/blockforest/BlockForest.h:50`（`class BlockForest : public BlockStorage`；该模块 21 654 行）；支持 2:1 静态加密 + 动态重建（`BlockReconstruction.cpp`、`PhantomBlock.cpp`）。
- **负载均衡**：`class GlobalLoadBalancing` `src/blockforest/GlobalLoadBalancing.h:51`，两条路：
  - **METIS 图分区**：`MetisConfiguration` `:57`（含 `maxUbvec` 不平衡度上界 / `iterations` / 自定义通信代价函数 `CommunicationFunction` `:20`）；
  - **SFC（Hilbert 曲线）**：`src/blockforest/HilbertCurveConstruction.h`；入口 `balance()` / `balanceSorted()` `GlobalLoadBalancing.h:170 / :184`。
- 自由面额外按 **fill level 加权**负载（`src/lbm/free_surface/LoadBalancing.h`）⇒ 界面 cell 计算量大且分布不均时的专用权重，对我们做自由面很关键。
- 通信：CPU 侧 `src/blockforest/communication/UniformBufferedScheme`（缓冲 + 非阻塞）；GPU 侧 `src/gpu/communication/`（12 文件）：`UniformGPUScheme.h`、`NonUniformGPUScheme.h`、`GeneratedGPUPackInfo.h`、**`CustomMemoryBuffer.h`（GPU 直通缓冲，避免 host 中转）**、`MemcpyPackInfo.h`，以及 `src/gpu/lbm/CombinedInPlaceGpuPackInfo.h`（**原地打包**：把 ghost 打包与流式写在同一次访存里）。

### 5.3 通信隐藏（本仓最漂亮的一处工程）

`apps/showcases/PhaseFieldAllenCahn/CPU/multiphase.cpp:261-271` 原文骨架：

```
timeloop.add() << BeforeFunction(Comm_velocity_based_distributions_start, "Start Hydro PDFs Communication")
               << Sweep(phase_field_LB_NoSlip, "NoSlip Phase");
timeloop.add() << Sweep(phase_field_LB_step, "Phase LB Step")
               << AfterFunction(Comm_velocity_based_distributions_wait, "Wait Hydro PDFs Communication");
timeloop.add() << Sweep(contact_angle, "Contact Angle")
               << AfterFunction(Comm_phase_field, "Communication Phase Field");
timeloop.add() << BeforeFunction(Comm_phase_field_distributions_start, "Start Phase PDFs Communication")
               << Sweep(hydro_LB_NoSlip, "NoSlip Hydro");
timeloop.add() << Sweep(hydro_LB_step, "Hydro LB Step")
               << AfterFunction(Comm_phase_field_distributions_wait, "Wait Phase PDFs Communication");
```

⇒ **用「相场步的计算」遮盖「水动力场的 MPIg 交换」，用「水动力步的计算」遮盖「相场的交换」**；前提是两场流式方向相反（pull vs push，`:80/:86`），使一个场在另一场通信期间处于可计算状态。**纯算法级重叠，不需要 MPI 线程/CUDA stream 支持** —— 对我们分布式卡口是最便宜的一档收益。

### 5.4 GPU 覆盖边界（诚实划界）

- GPU 只覆盖**生成型** sweep（`target=Target.GPU`，apps 内 17 处命中，`walberla_stats.json.parallel.gpu_target_codegen`）；手写 C++ 模板 sweep（SRT/TRT/MRT/Cumulant 那套）**没有 GPU 版**——`src/gpu/lbm/` 只有 1 个文件。
- 多相（相场）**CPU/GPU 双份 codegen**（`apps/showcases/PhaseFieldAllenCahn/{CPU,GPU}/multiphase_codegen.py` 各生成一套）；**本 HEAD 的自由面主体是 CPU C++**（`src/gpu/` 下无 free_surface 子目录 ⇒ 本 HEAD 自由面无 GPU 实现）。
- ⇒ 本仓 GPU 能力的真实范围 = **「生成的单相/相场 LBM kernel + GPU 通信」**，不等于「全功能 GPU 框架」。

---

## 六、可微 / 代码生成

| 项 | 结论 | 证据 |
|---|---|---|
| **autograd 友好度** | **无**。全仓无自动微分 / 无伴随 / 无反传；符号层（SymPy + pystencils）只做**前向表达式 → C/CUDA 源** | `python/pystencils_walberla/function_generator.py`、`python/pystencils_walberla/sweep.py` |
| **符号推导** | 有，但**在外部仓**：`lbmpy` 供给碰撞/平衡态/力项符号构造，本仓只见调用（`from lbmpy import LBMConfig, LBMOptimisation, LBStencil, Method` `apps/showcases/PhaseFieldAllenCahn/CPU/multiphase_codegen.py:6`；`from lbmpy.creationfunctions import create_lb_method` `:13`） | 同；姊妹报告 `docs/oss_inventory/REPORT_lbmpy.md` |
| **kernel 生成（新链）** | **SweepGen**（v7.3 新引入，pystencils 2.0 基础）：`sweepgen/src/sweepgen/{api,flow,sweep,symbolic,build_config}.py` + `boundaries/` + `communication/` + `prefabs/`（19 文件 4 666 行） | `CHANGELOG.md:13`；`walberla_stats.json.codegen.sweepgen_files` |
| **kernel 生成（旧链，仍用）** | `python/pystencils_walberla/`（13 条目：`sweep.py`、`boundary.py`、`pack_info.py`、`kernel_selection.py`、`jinja_filters.py`、`function_generator.py`…）+ `python/lbmpy_walberla/`（16 条目：`walberla_lbm_generation.py`、`alternating_sweeps.py`、`sparse.py`、`storage_specification.py`、`boundary.py`、`packing_kernels.py`…）；依赖 **lbmpy 1.4.1 legacy**（`CHANGELOG.md:14`） | `walberla_stats.json.codegen.pystencils_walberla / lbmpy_walberla` |
| **预生成产物（省构建依赖）** | `src/lbm_generated/`（8 子目录，21 278 行）存放**已生成好的 D3Q19/D3Q27 SRT sweep**：`sweep_collection/{D3Q19SRT,D3Q27SRT}.{h,cpp}` + 生成脚本 `sweep_collection_generation_script.py` ⇒ **不带 Python 工具链也能编 SRT** | `walberla_stats.json.codegen.generated_sweeps` |
| **生成接口契约** | `generate_sweep / generate_boundary / generate_pack_info_for_field / generate_lb_pack_info / generate_info_header`（`apps/showcases/PhaseFieldAllenCahn/CPU/multiphase_codegen.py:18-19` 导入）+ `CodeGeneration()` 上下文（`:22`）⇒ **一个 codegen 脚本 = 一个类的 kernel 集合**，CMake 在构建期调用（`python/pystencils_walberla/cmake_integration.py`） | 同 |

**对我们的含义**：它把「公式（SymPy）→ kernel（C/CUDA）→ CMake 目标」串成标准流水线，**但没有一阶导数**。若做「可微 LBM」，可借的是**符号层的可组合组织**（碰撞/平衡态/力项各自独立，`LBMConfig` 一次装配），**不能**指望它的 autograd。

---

## 七、与我们功能点的映射

| feature_ledger slug | waLBerla 对应实现 | 代码指针 | 可迁移性 |
|---|---|---|---|
| **GAP-LBM-MCMP-POISEUILLE**（两组分分层 Poiseuille） | **无 MCMP**。最接近：两相相场能做分层（RTI 即分层），但**无「组分」概念、无组分输运方程** | 负面证据 `walberla_stats.json.multiphase.sc_absent_hits=0`；两相结构 `apps/showcases/PhaseFieldAllenCahn/CPU/multiphase_codegen.py:39-50`；分层算例 `apps/showcases/PhaseFieldAllenCahn/CPU/multiphase_RTI_3D.py:43-56` | `reference`（只借「分层初始化几何写法 + 变密度平衡态装配」） |
| **MCMP 高密度比** | 相场路线用 `rho = rho_L + C(rho_H−rho_L)` **线性插值**实现变密度，实跑 ρ比 **1000**（RTI）/ **744**（Taylor）；粘度按 `omega(C)` 变 | `apps/showcases/PhaseFieldAllenCahn/CPU/multiphase_codegen.py:59-66`；`apps/showcases/PhaseFieldAllenCahn/CPU/multiphase_RTI_3D.py:45-46`；`apps/showcases/PhaseFieldAllenCahn/CPU/multiphase_taylor_bubble.py:20-21` | `adapt`（密度插值 + 重力×局部密度 可直接照搬） |
| **自由面（free surface）** | **完整几何 free-surface 模块**：fill level + 5 重建族 + 5 回填族 + 9 多余质量分配族 + 3 曲率族 + 气泡合并/分裂 + 质量诊断 + 负载均衡 | `src/lbm/free_surface/`（71 文件）；§4.4 表；配置实证 `apps/showcases/FreeSurface/DropWetting.prm:25-33` | `adapt`（**模型族 × 配置**架构整体借用，公式自己写） |
| **相场 Allen-Cahn** | 守恒 AC 两相：MRT 相场 LB（只松弛一阶矩）+ **界面追踪力 / CSF 水动力力双力项分离** + 接触角 + 可选 RK 推进 + 热毛细（Marangoni） | `apps/showcases/PhaseFieldAllenCahn/CPU/multiphase_codegen.py:57, 72-74, 93-94, 104-112`；RK：`apps/showcases/Thermocapillary/thermocapillary_codegen.py:21, 216-224` | `adapt`（双力项分离 + 松弛率表最省事） |
| **GPU 融合 kernel** | 生成型 GPU sweep（`Target.GPU` + `gpu_indexing_params`）；`stream_pull_collide` / `collide_stream_push` 两种融合方向；pitched memory；`CombinedInPlaceGpuPackInfo`；交替 pull/push 省场 | `apps/showcases/PhaseFieldAllenCahn/GPU/multiphase_codegen.py:154-182`；`GPUField.h:54, 99-105`；`GPUField.impl.h:58-70`；`alternating_sweeps.py:16, 32` | `reference` |
| **分布式多卡** | GPU+MPI 打包生成（`UniformGPUScheme` / `NonUniformGPUScheme` / `GeneratedGPUPackInfo`）；**跨物理场通信重叠**（不需 CUDA stream） | `src/gpu/communication/`（12 文件）；`src/gpu/lbm/CombinedInPlaceGpuPackInfo.h`；`apps/showcases/PhaseFieldAllenCahn/CPU/multiphase.cpp:261-271` | `reference`（重叠调度范式可直接用于我们的多卡时序设计） |
| **稀疏/多孔** | ① 渗透率评估器 `class Permeability`（给粘性/流轴/采样体积，按 Darcy 反算）；② **list-based 稀疏 LBM**：活跃 cell 列表 + `src/dst/idx` 索引数组 + SoA，`create_lb_update_rule_sparse`；③ `apps/benchmarks/Percolation` 多孔介质算例 | `src/lbm/evaluations/Permeability.h:42`；`python/lbmpy_walberla/sparse.py:11-26`；`apps/benchmarks/Percolation/Percolation.cpp:49` | `adapt`（索引数组稀疏流式，正对我们稀疏/多孔 GAP） |
| **案例组织** | `apps/{examples,showcases,benchmarks,tutorials}` 四层（6/13/23/6 个算例目录）+ 每算例 `.prm` 参数文件 + Python 场景类（`apps/showcases/PhaseFieldAllenCahn/CPU/multiphase_rising_bubble.py:34-97` 的 `Scenario` 里把网格/参数/输出/回调一次配齐） | `walberla_stats.json.tests_ci_apps.apps_*` | `fits`（纯组织范式，无许可风险） |
| **测试与验证面** | 328 个测试 `.cpp` / 24 目录；**`tests/lbm/SweepEquivalenceTest.cpp`（sweep 变体逐 cell 等价验证）**、`tests/lbm/codegen/`（生成 kernel 验证）、自由面子目录测试（`tests/lbm/free_surface/{dynamics,surface_geometry,bubble_model}`）；多相唯一解析解对标：`analytical_solution_microchannel`（`apps/showcases/Thermocapillary/microchannel2D.py:8, 72`） | `walberla_stats.json.tests_ci_apps` | `reference`（等价性测试范式最值得抄） |
| **CI** | 单文件 GitLab CI `.gitlab-ci.yml`；判据/阶段**未在本盘覆盖**（未读该文件内容，仅确认存在） | `walberla_stats.json.tests_ci_apps.ci_files` | `reference`（不深入，见 §八 未覆盖声明） |
| **NEW: 润湿/接触角边界生成器（两套并行）** | 自由面 θ（`src/lbm/free_surface/surface_geometry/ContactAngle.h:34` + `DetectWettingSweep.h`）与相场 θ（`apps/showcases/PhaseFieldAllenCahn/CPU/multiphase_codegen.py:114-115, 155-156`，公式在 lbmpy）**两套独立实现，都通过 codegen 生成 boundary kernel** | 同左 | `adapt` |
| **NEW: 自由面「多余质量分配」模型族（9 种可切换）** | `src/lbm/free_surface/dynamics/ExcessMassDistributionModel.h:85-94`，把界面胞元转换后剩余质量按均匀/加权 × 全界面/新界面/旧界面 × 回退液相 组合分配 | 同左 | `adapt` |
| **NEW: LBM 静态块加密 2:1（Schornbaum–Rüde）** | `src/lbm/refinement/`：`EqualLevelBorderStreamCorrection.h`、`TimeStepPdfPackInfo.h`、`PdfFieldSyncPackInfo.h`、`CurlBasedLevelDetermination.h`、`VorticityBasedLevelDetermination.h` | 目录清单 `walberla_stats.json`（`src/lbm/refinement`） | `reference` |

---

## 八、可吸收 Top3 + 风险

### Top1 — 变密度两相相场的**装配方式 + 无量纲反解锚点**（服务 `MCMP 高密度比`、`相场 Allen-Cahn`、`GAP-LBM-MCMP-POISEUILLE` 的分层稳定性）

可吸收的不是代码，是**三个结构性决定 + 一组标定数字**：

1. **密度进平衡态，而不是进力项**：`density = rho_L + C(rho_H − rho_L)`（`apps/showcases/PhaseFieldAllenCahn/CPU/multiphase_codegen.py:59-61`）——密度是相场 `C` 的线性函数，直接用于平衡态与体力 `g·ρ`（`:63-64`），**这才是它能跑到 ρ比 744~1000 的原因**（假势法在高密度比下的失效正来自把密度差当外力扰动）。
2. **力项一分为二**：`interface_tracking_force`（界面追踪/反扩散，保 AC 界面形状）+ `hydrodynamic_force`（CSF 表面张力 + 重力），分别作为独立 `AssignmentCollection` 注入两条 update rule（`:93-94`、`:104`、`:112`）⇒ **耦合项与物理力物理分离，可各自单独关闭做消融**。这正是我们做「分层 Poiseuille 是否稳定」对照实验最需要的可开关结构。
3. **相场 MRT 松弛率表**：`rates = [0] + [ω_M]·D + [1]·(Q−D−1)`（`:72-74`）——零号矩不松弛（质量守恒的根本保证），只松弛一阶矩。比"自己推 MRT 矩阵"省数周。
4. **标定锚点**：ρ比 1000 / 粘度比 100（RTI，`apps/showcases/PhaseFieldAllenCahn/CPU/multiphase_RTI_3D.py:45-46`），ρ比 744 / 粘度比 4236、Morton 0.015、界面宽 3 格（Taylor bubble，`apps/showcases/PhaseFieldAllenCahn/CPU/multiphase_taylor_bubble.py:16-28`）——**界面宽度 3 格是可跑的量级下界**，我们若做高密度比可以直接从这个起点往下压。

### Top2 — 自由面的 **「模型族 × 配置」工程架构**（服务 `自由面`）

自由面最大的工程风险不是公式，是**质量不守恒与寄生流**。waLBerla 的解法是把它做成**可切换实现族**：重建 5 种（`src/lbm/free_surface/dynamics/PdfReconstructionModel.h:62-67`）× 回填 5 种（`src/lbm/free_surface/dynamics/PdfRefillingModel.h:61-66`）× 多余质量分配 9 种（`src/lbm/free_surface/dynamics/ExcessMassDistributionModel.h:85-94`）× 曲率 3 种（`src/lbm/free_surface/surface_geometry/CurvatureModel.h:42/59/76`），全部由 `.prm` 选（`DropWetting.prm:25-33`），再加 `TotalMassComputer.h` / `MaxVelocityComputer.h` 两个诊断器 + 按 fill-level 加权的负载均衡（`LoadBalancing.h`）。

**直接可吸收的三点**：① 我们的自由面若只实现一种重建/回填，将无法定位"质量漂移是模型问题还是实现问题"——**必须留出族接口**；② **诊断器与模型同层**（不是事后脚本）；③ 界面 cell 的**负载不均衡**要按界面数加权，否则并行效率被界面拖垮。

### Top3 — **sweep 等价性测试范式** + 预生成产物策略（服务 `测试与验证面`、平台类）

- `tests/lbm/SweepEquivalenceTest.cpp`：同一算例下对 `CellwiseSweep` / `SplitSweep` / `SplitPureSweep` / 生成 sweep 做**逐 cell 数值比对**（该文件头部 include 即列出 4 类 sweep：`:30-33`）。这是我们做 **GPU 融合 kernel / pull / push / 交替 AA 变体**时最便宜的一道防"优化引入静默偏差"的门——我们目前缺这一层（只有整体算例对标，误差无法归因到流式变体）。
- `src/lbm_generated/`（预生成 D3Q19/D3Q27 SRT，21 278 行）：**把代码生成的产物入仓**，使"不带 Python 工具链的环境也能编译与复现"。对我们的直接含义：kernel 生成链一旦引入，就必须同时提供**预生成兜底**，否则 CI/集群环境的工具链差异会让复现性崩掉。

### 风险与负面判断（诚实清单）

| # | 风险 | 具体 | 影响 |
|---|---|---|---|
| R1 | **GPL-3.0 双仓同源** | waLBerla `COPYING.txt:1-2`；多相部分依赖的 lbmpy 亦 GPL-3.0-or-later | 任何"搬代码"路线直接废；只能重写。**吸收期要立规矩：只写公式与架构笔记，不把源码片段粘进 TensorLBM** |
| R2 | **多相无 C++ 实现** | 相场只有 codegen 版（`multiphase_codegen.py`），C++ 侧无对应类 | "读 C++ 就能看懂多相"不成立；必须连 lbmpy 一起读（同许可，双重限制） |
| R3 | **构建链重且本盘无法验证** | CMake ≥ 3.24 + C++20 + MPI + lbmpy 1.4.1/pystencils | 本报告**零运行验证**；任何"照它跑"计划须先在集群装链（估 1 人日） |
| R4 | **力模型语义陷阱** | EDM/Luo 只移宏观速度（`ForceModel.h:109`）vs Guo 两者都移（`:115`） | 移植混用 → 静默一阶误差，正是"对不上解析解"的常见根因 |
| R5 | **自由面无 GPU（本 HEAD）** | `src/gpu/` 下无 free_surface 子目录 | 我们的自由面若需 GPU，不能从它这里取 |
| R6 | **无 KBC / 无正则化 / 无 Zou-He** | `collision_variants_present={kbc:0,regularized:0}`；`zou` 0 命中 | 这三项必须自研，本仓只能作为"缺失对照" |
| R7 | **多相无误差计算脚本** | `microchannel2D.py:239-245` 只落 CSV，无解析解误差统计 | 它的"验证"是人工看图；我们不能把"它有算例"当成"它验证过精度" |

### 未覆盖声明（本盘边界，不硬凑）

- `.gitlab-ci.yml` **文件内容未读**（只确认存在）⇒ §七 CI 行仅为存在性结论。
- `doc/`（Sphinx/Doxygen 文档）、`src/mesh*`、`src/mesa_pd`（129 文件 24 018 行，刚体动力学）、`src/pde`、`src/fft`、`src/python_coupling`、`src/sqlite`、`sweepgen` 内部实现：**未深读**（与 LBM 盘点目标弱相关或超出本单范围）；`sweepgen` 只确认用途与目录。
- `src/lbm/free_surface` 的**实现细节**（各模型的具体公式，如 `PdfRefillingSweep.impl.h` 的 GradsMoments 公式）只定位到枚举与 sweep，**未逐行读公式**——本报告在 §4.4 因此只给"模型族与指针"，不给公式式样。
- 自由面/相场的**数值结果**（质量漂移量、spurious current 幅值）：无运行 ⇒ 无数字，**不编造**。

---


### 误差判定与自检（论文六要素之⑥，本单为静态盘点，故按"可验证性"而非"物理误差"裁）

**本单没有任何物理量计算结果，因此不存在"vs 解析解/实验值的误差百分比"**——声明原因：本盘目标是"已有开源实现方式集"，产出是代码事实与架构结论，且 waLBerla 构建链（CMake≥3.24 + C++20 + MPI + lbmpy 1.4.1/pystencils）在本机与集群均不可用，**故不运行、不产数字**。**不许含糊，明写：无 <3% 这一判据可谈。**

替代的机器可裁判据（全部已执行）：

| 判据 | 方法 | 结果 |
|---|---|---|
| **指针可信** | `verify_walberla_pointers.py` 把报告中每条 `path:line` 回读源码并打印该行原文 | **141 条显式指针 0 失败 + 35 条同行简写指针 0 越界**（`walberla_pointer_check.txt`） |
| **负面证据可信** | `make_walberla_stats.py` 对 SCMP/MCMP/自由能/颜色梯度/CH、KBC/正则化/Zou-He 逐项 grep 计数 | 全部 **0 命中**（`walberla_stats.json`），写入 §二/§三/§四 的"无"结论 |
| **数字零手抄** | 所有计数/LOC/枚举成员由脚本从源码采集后写入 JSON，报告只引用 JSON 字段 | 见 `walberla_stats.json` 与各节括注 |
| **规模数字交叉校验** | `git rev-parse HEAD` 与 `api.github.com/git/trees/HEAD` 的 `sha` 比对 | 均 `dc16f3130de…`，树未截断 |

**自检发现并已修正的错误（诚实记录）**：首版报告曾把 `SimpleUBB` 的"仅 SRT / 不可压 / 无外力"三条 `static_assert` 记在 `SimpleUBB.h:55-57`；脚本回读发现这三条实为 **`SimplePAB.h:56-58`**。已改正并删除该误判——**这正是"每条结论必须有代码指针 + 机器回读"的价值**。

### 图

- **图 1**（`walberla_fig1_modules.png`）：`src/` 模块规模分布（LOC，程序化采集），LBM 相关模块标红——说明"LBM 是这个框架里的一个大模块，不是它的全部"。
- **图 2**（`walberla_fig2_multiphase_matrix.png`）：我们 8 个多相目标族 × waLBerla 3 条实现通道（手写 C++ / lbmpy codegen / 缺失）的覆盖矩阵，绿=有实现锚点，灰=全仓 grep 0 命中。
- **图 3**（`walberla_fig3_freesurface_pipeline.png`）：自由面时间步的 8 段流水（行号从 `SurfaceDynamicsHandler.h` 程序化抽取 38 个 sweep 注册点后归并），红色三段是质量守恒关键段。

---

## 机器块（守护线解析入库）

```
[OSS-INV]
repo: lssfau/walberla
lang: C++20 (+ Python codegen: lbmpy 1.4.1 / pystencils / SweepGen)
license: GPL-3.0
methods: D2Q9/D3Q15/D3Q19/D3Q27 x {SRT, SRT-field, TRT(+3/16 magic), MRT(D3Q19 19-rate), Cumulant(D2Q9/D3Q19/D3Q27 10-rate)}; forcing {EDM(Kupershtokh), Luo, Guo(with 1-omega/2), Latt-correction, Simple}; LES Smagorinsky; sweeps {Cellwise, Split, SplitPure, generated, esotwist/pull/push alternating}
multiphase: 相场守恒 Allen-Cahn 两相(仅 lbmpy codegen, rho=rho_L+C(rho_H-rho_L), 双力项 interface_tracking+CSF hydro, rho比至1000) apps/showcases/PhaseFieldAllenCahn/CPU/multiphase_codegen.py:39-115 + 自由面 fill-level 几何法 src/lbm/free_surface/dynamics/PdfReconstructionModel.h:62, src/lbm/free_surface/dynamics/PdfRefillingModel.h:61, src/lbm/free_surface/dynamics/ExcessMassDistributionModel.h:85; 无 SCMP/MCMP/自由能/颜色梯度/CH(0命中)
boundary: bounce-back(NoSlip.h:49), 插值曲边BB(Curved.h:49 delta=距壁/格距), UBB/SimpleUBB/DynamicUBB 移动壁(src/lbm/boundary/UBB.h:50), 压力Dirichlet(Pressure.h:49), PAB/Outlet(SimplePAB.h:54), 自由滑移(FreeSlip.h:46), flag+treatDirection 框架(src/boundary/BoundaryHandling.h:58); 无 Zou-He; IBM 三套 lbm_mesapd_coupling/{momentum_exchange_method,partially_saturated_cells_method}
gpu: 生成型 GPU sweep(Target.GPU, apps 17 处), GPUField AoS(zyxf)/SoA(fzyx)+pitched memory(GPUField.h:54,99-105; GPUField.impl.h:58-70), CombinedInPlaceGpuPackInfo 原地打包, GPU 直通缓冲 CustomMemoryBuffer; 手写 C++ sweep 与自由面无 GPU 版
summary: waLBerla 是块森林多物理 HPC 框架，LBM 侧碰撞/受力谱系齐全(SRT/TRT/MRT/Cumulant + EDM/Luo/Guo/Latt)但缺 KBC/正则化/Zou-He，多相只有相场 Allen-Cahn(经 lbmpy 代码生成，rho比可到1000)与 C++ 手写自由面两条路，无任何 SCMP/MCMP/颜色梯度/自由能实现。
对我们最高价值是可吸收的架构与标定量：变密度相场「双力项+密度进平衡态」装配、自由面「模型族×配置」工程分层、sweep 等价性测试范式；受 GPL-3.0 约束只能读不能抄。
[/OSS-INV]
```

```
[OSS-SOL]
repo: lssfau/walberla
slug: MCMP 高密度比
tag: variable-density-equilibrium-linear-in-C
method: 密度作为相场 C 的线性函数直接进平衡态与体力（rho = rho_L + C*(rho_H-rho_L)，body_force = g*rho），粘度按 omega(C) 随相变；相场 MRT 松弛率表置零号矩为 0 保证质量守恒、只松弛一阶矩。实测算例密度比 1000(RTI)/744(Taylor bubble)。
files: apps/showcases/PhaseFieldAllenCahn/CPU/multiphase_codegen.py:59-66, apps/showcases/PhaseFieldAllenCahn/CPU/multiphase_codegen.py:72-74, apps/showcases/PhaseFieldAllenCahn/CPU/multiphase_RTI_3D.py:45-46, apps/showcases/PhaseFieldAllenCahn/CPU/multiphase_taylor_bubble.py:20-21
conventions: rho_L/rho_H 为格点单位；粘度比 100(RTI)/4236(Taylor)；界面宽 3 格可跑；Morton 0.015；g 为格点加速度乘局部密度
transfer: adapt
[/OSS-SOL]
```

```
[OSS-SOL]
repo: lssfau/walberla
slug: 相场 Allen-Cahn
tag: conservative-AC-double-force-split
method: 相场走独立 MRT LB(松弛率 1/(0.5+3*mobility))，两个力项物理分离：interface_tracking_force(界面反扩散)与 hydrodynamic_force(CSF 表面张力+重力)，各自作为独立 AssignmentCollection 加到相场/水动力 update rule；接触角由 codegen 生成 boundary kernel；热毛细场景另加 D3Q7 温度场与 RK1-RK4 时间推进。
files: apps/showcases/PhaseFieldAllenCahn/CPU/multiphase_codegen.py:57, apps/showcases/PhaseFieldAllenCahn/CPU/multiphase_codegen.py:93-94, apps/showcases/PhaseFieldAllenCahn/CPU/multiphase_codegen.py:104-112, apps/showcases/Thermocapillary/thermocapillary_codegen.py:216-224
conventions: C in [0,1]；相场 stream_pull_collide / 水动力 collide_stream_push（反向流式以便通信重叠）；接触角单位度、耦合界面厚度；CSF 表面张力由 sigma 与 grad C/|grad C| 构造（公式体在 lbmpy）
transfer: adapt
[/OSS-SOL]
```

```
[OSS-SOL]
repo: lssfau/walberla
slug: 自由面（free surface）
tag: fill-level-model-family-configurable
method: flag 三态(liquid/interface/gas)+连续 fill level；时间步为固定 sweep 序列：边界→力加权→StreamReconstructAdvect 融合→碰撞/流式→胞元转换→回填→多余质量分配→标志复位→块状态更新；每个环节是可配置的模型族（重建 5 × 回填 5 × 多余质量 9 × 曲率 3）。
files: src/lbm/free_surface/dynamics/SurfaceDynamicsHandler.h:188, src/lbm/free_surface/dynamics/PdfReconstructionModel.h:62-67, src/lbm/free_surface/dynamics/PdfRefillingModel.h:61-66, src/lbm/free_surface/dynamics/ExcessMassDistributionModel.h:85-94, apps/showcases/FreeSurface/DropWetting.prm:25-33
conventions: cellConversionThreshold 1e-2 / cellConversionForceThreshold 1e-1；D3Q27 因多余质量分配需全 27 邻居；接触角单位度且仅 enableWetting 时生效；质量/最大速度由 TotalMassComputer/MaxVelocityComputer 诊断
transfer: adapt
[/OSS-SOL]
```

```
[OSS-SOL]
repo: lssfau/walberla
slug: GAP-LBM-MCMP-POISEUILLE
tag: no-mcmp-two-phase-only
method: 本仓无 MCMP（无组分概念、无组分输运方程）；两组分分层 Poiseuille 无法直接映射。可借用的只是相场的分层初始化几何写法与变密度平衡态装配，把"两组分"降级为"两相分层"来近似。
files: apps/showcases/PhaseFieldAllenCahn/CPU/multiphase_RTI_3D.py:43-56, apps/showcases/PhaseFieldAllenCahn/CPU/multiphase_codegen.py:39-50
conventions: 无组分索引/无 per-component PDF；分层仅由 C 的初值给出
transfer: reference
[/OSS-SOL]
```

```
[OSS-SOL]
repo: lssfau/walberla
slug: GPU 融合 kernel
tag: generated-gpu-sweep-pitched-plus-inplace-pack
method: kernel 由 Python 生成，目标 Target.GPU，靠 kernel_type 选 stream_pull_collide / collide_stream_push 融合方向；GPUField 同时支持 AoS(zyxf)/SoA(fzyx) 且可开 pitched memory；交替 pull/push 用编译期奇偶分支实现 in-place 双场流式；GPU 通信用就地打包(CombinedInPlaceGpuPackInfo)与 GPU 直通缓冲。
files: apps/showcases/PhaseFieldAllenCahn/GPU/multiphase_codegen.py:154-182, src/gpu/GPUField.h:99-105, src/gpu/GPUField.impl.h:58-70, python/lbmpy_walberla/alternating_sweeps.py:16-32
conventions: fzyx=SoA 默认；fStride=xSize*ySize*zSize；pitched 分配需对齐；gpu_indexing_params 控制 block size
transfer: reference
[/OSS-SOL]
```

```
[OSS-SOL]
repo: lssfau/walberla
slug: 分布式多卡
tag: cross-physics-comm-overlap-no-cuda-stream
method: 两套 PDF 场用相反流式方向，时间循环里用 BeforeFunction 启动 A 场的通信、用 B 场的计算遮盖它，再用 AfterFunction 等待；GPU 侧通信由生成型 pack info + UniformGPUScheme/NonUniformGPUScheme 完成。
files: apps/showcases/PhaseFieldAllenCahn/CPU/multiphase.cpp:261-271, src/gpu/communication/UniformGPUScheme.h, src/gpu/lbm/CombinedInPlaceGpuPackInfo.h
conventions: 重叠成立的前提是两场流式方向相反（pull vs push）；不需要 MPI 线程支持或 CUDA stream
transfer: reference
[/OSS-SOL]
```

```
[OSS-SOL]
repo: lssfau/walberla
slug: 稀疏/多孔
tag: list-based-sparse-lbm-plus-permeability
method: 稀疏 LBM 用活跃 cell 列表 + src/dst/idx 索引数组 + SoA 布局，由 create_lb_update_rule_sparse 生成 gather 式 kernel；多孔介质用 Darcy 反算渗透率评估器（给粘性与流轴）；
files: python/lbmpy_walberla/sparse.py:11-26, src/lbm/evaluations/Permeability.h:42, apps/benchmarks/Percolation/Percolation.cpp:49
conventions: index_type 默认 uint32；layout='SoA'；渗透率按采样体积与流轴(x/y/z) 统计，频率可配
transfer: adapt
[/OSS-SOL]
```

```
[OSS-SOL]
repo: lssfau/walberla
slug: 案例组织
tag: apps-4layer-plus-prm-plus-scenario-class
method: 算例按 examples/showcases/benchmarks/tutorials 四层组织，每算例一个 .prm 参数文件 + 一个 Python Scenario 类（网格、物理参数、输出频率、每步回调一次配齐），C++ 侧只读 prm 装配；多相场景把模型族（重建/回填/多余质量/曲率）全部暴露到 prm。
files: apps/showcases/PhaseFieldAllenCahn/CPU/multiphase_rising_bubble.py:34-97, apps/showcases/FreeSurface/DropWetting.prm:25-33
conventions: prm 分块(PhysicsParameters/ModelParameters/EvaluationParameters/BoundaryParameters)；参数名即模型族枚举名
transfer: fits
[/OSS-SOL]
```

```
[OSS-SOL]
repo: lssfau/walberla
slug: 测试与验证面
tag: sweep-equivalence-regression
method: 同一算例上对 CellwiseSweep / SplitSweep / SplitPureSweep / 生成 sweep 做逐 cell 数值比对，保证"换流式实现不改结果"；另有 codegen 测试目录与自由面子模块(动力学/表面几何/气泡)独立测试；多相唯一解析解对标是微通道热毛细 analytic 解（但只落 CSV，无误差统计脚本）。
files: tests/lbm/SweepEquivalenceTest.cpp:30-33, apps/showcases/Thermocapillary/microchannel2D.py:72
conventions: 等价性判据为逐 cell 浮点比对（不容差放宽到物理量级）；测试按模块目录镜像 src 结构
transfer: reference
[/OSS-SOL]
```

```
[OSS-SOL]
repo: lssfau/walberla
slug: CI
tag: gitlab-single-file-pipeline
method: 单文件 GitLab CI 流水线（.gitlab-ci.yml）驱动构建/测试；本盘只确认存在与位置，未读内容，判据未知。
files: .gitlab-ci.yml
conventions: 未覆盖（未读文件内容）
transfer: reference
[/OSS-SOL]
```

```
[OSS-SOL]
repo: lssfau/walberla
slug: NEW:润湿/接触角边界生成器（自由面与相场两套并行）
tag: two-independent-contact-angle-impls
method: 自由面侧只缓存 sin/cos 的 ContactAngle + DetectWettingSweep/ObstacleFillLevelSweep 做几何修正；相场侧由 codegen 生成 ContactAngle boundary kernel（公式在 lbmpy，按界面厚度与角度反解壁面 C 的 Neumann 条件）。两套不共享代码。
files: src/lbm/free_surface/surface_geometry/ContactAngle.h:34, apps/showcases/PhaseFieldAllenCahn/CPU/multiphase_codegen.py:114-115, apps/showcases/PhaseFieldAllenCahn/CPU/multiphase_codegen.py:155-156
conventions: 自由面接触角单位度且仅 enableWetting 时生效；相场接触角需同时给界面厚度；两者数值口径不通用
transfer: adapt
[/OSS-SOL]
```

```
[OSS-SOL]
repo: lssfau/walberla
slug: NEW:自由面多余质量分配模型族（9 种可切换）
tag: excess-mass-9-family
method: 胞元转换后剩余质量按 均匀/加权 × 全界面/新界面/旧界面 × 是否回退液相 组合成 9 种分配模型，由 ExcessMassDistributionModel 按名字选择，分配 sweep 紧随胞元转换之后执行。
files: src/lbm/free_surface/dynamics/ExcessMassDistributionModel.h:85-94, src/lbm/free_surface/dynamics/SurfaceDynamicsHandler.h:358
conventions: 模型名即枚举名（EvenlyAllInterface / WeightedAllInterface / ...FallbackLiquid）；分配发生在 cell conversion 之后、回填之前
transfer: adapt
[/OSS-SOL]
```

```
[OSS-SOL]
repo: lssfau/walberla
slug: NEW:LBM 静态块加密 2:1（Schornbaum–Rüde 式等值面流式修正）
tag: block-amr-equal-level-stream-correction
method: 块森林 2:1 加密 + 等值面边界流式修正（EqualLevelBorderStreamCorrection）+ 按旋度/涡量选加密区 + 加密层 ω 按 levelDependentRelaxationParameter 重标定 + 时间步 PDF 打包同步。
files: src/lbm/refinement/EqualLevelBorderStreamCorrection.h, src/lbm/refinement/CurlBasedLevelDetermination.h, src/lbm/lattice_model/CollisionModel.h:47
conventions: 2:1 平衡约束；ω 随 level 重标定(reference level 需显式给出)；加密边界需额外 stream correction 才保二阶
transfer: reference
[/OSS-SOL]
```
