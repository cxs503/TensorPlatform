# 开源 DG 技术盘点·候选普查报告

> **scenario_key**: oss-dg-disc  
> **mission_id**: 8639  
> **锚点**: FEAT-OSS-INVENTORY-DG  
> **盘点日期**: 2026-11-09  
> **盘点人**: agent/m8639  
> **产物**: `artifacts/oss_dg_registry.json`（机器可解析）  
> **方法**: GitHub API（`api.github.com/search/repositories` + `/repos/{owner}/{name}`）多轮查询 + 质量筛选

---

## 一、案例介绍（物理背景 / 普查范围）

### 1.1 任务背景

DG（Discontinuous Galerkin，间断 Galerkin）是 1983 年 Reed-Hill / Cockburn 等提出的高阶非守恒有限元族：
单元内高阶多项式 + 单元间断 + 数值通量，兼具 FEM 的高阶精度与 FVM 的守恒/鲁棒性，
是可压缩流（激波捕捉）、不可压流、结构、中子输运、天体物理的通用高阶离散。
本普查对标 SPH 普查协议（campaign-osssph-disc-1，m8638）与 LBM 战役 wave-0，
为 TensorDG 域建立开源技术全景图，为后续深盘（wave-1+）与 feature_ledger GAP 锚点提供候选池。

### 1.2 普查范围

| 维度 | 覆盖 |
|---|---|
| **方法族** | 可压缩 Euler/NS DG · DG-FVM 混合 · 熵稳定/子单元限界 · GPU DG · SD/FR(huxi/CPR) 近亲 · 教学正统（Hesthaven-Warburton 系） |
| **物理域** | 可压缩流 · 不可压流 · 多相 · 反应流 · 中子输运 · 辐射流体 · 浅水 |
| **计算后端** | CPU · CUDA · Kokkos · MPI · Julia/Python 前端 |
| **质量门槛** | 方法独特性或 stars 突出；有实质代码（非纯 README）；去 fork/去纯 demo |

### 1.3 普查方法

1. **GitHub API 多轮搜索**：`discontinuous+galerkin+{compressible,navier-stokes,euler,shock,fluid,gpu,cuda,kokkos,...}`、
   `entropy+stable+...`、`subcell+limiter`、`flux+reconstruction`、`spectral+difference+scheme`、`nek5000`、
   `spectral+element+...` 等 100+ 查询词（未认证 search 限速 10/min，分块执行）；
2. **候选池**：去重后 349 仓原始候选（`artifacts/dg_disc/candidates_raw.json`）；
3. **质量筛选**：去 fork/去纯 demo/去非 DG 外围（纯 FV/SEM 地震等）→ 60 仓入册；
4. **逐仓核实**：core API `/repos/{fn}` 取 license/stars/pushed_at/language/archived/fork（60 次调用，全部 API 溯源）。

---

## 二、普查结果总览

### 2.1 分级统计

| Tier | 数量 | 定义 |
|---|---:|---|
| **Tier 1** 方法专家仓 | 11 | DG/SE/FR 正统或领域标杆实现（Nek5000/ExaDG/HORSES3D/GALÆXI/Trixi/GFR/Flurry/ESDG-CNS 等） |
| **Tier 2** 可微同族与框架 | 13 | 通用 FEM/谱元框架、Python 前端、多物理平台（deal.II/dolfinx/libCEED/PHiLiP/Nekbone/snek5000 等） |
| **Tier 3** 专项能力 | 36 | GPU 变体/教学/单点应用/论文复现仓 |
| **合计** | **60** | — |

### 2.2 覆盖域分布

| 覆盖域 | 数量 | 代表 |
|---|---:|---|
| 可压缩 Euler/NS DG | 15 | Trixi.jl, exadg, horses3d, dflo, galaexi, elexi, Local-DG, F |
| 通用框架（含 DG 能力） | 13 | dealii, libCEED, enrico, PHiLiP, GalerkinSparseGrids.jl, NDG |
| 教学正统（Hesthaven-Warburton 系） | 10 | Nek5000, snek5000, Nekbone, NekExamples, nekStab, NekIBM, py |
| SD/FR(huxi/CPR) 近亲 | 8 | GFR, Flurry-cpp, ppkMHD, FluxReconstruction.jl, zefr, Flower |
| 熵稳定/子单元限界 | 7 | ESDG-CNS, ADER-DG, EntropyStableEuler.jl, paper-2023-es_two_ |
| GPU 化 DG | 4 | horses3d-gpu, DGCUDA, dgfs1D_gpu, dg-on-cuda |
| DG-FVM 混合 | 3 | NebulaSEM, gpu-mwdg2, ppmdg |

### 2.3 语言分布

| 语言 | 数量 |
|---|---:|
| C++ | 16 |
| Fortran | 15 |
| Julia | 12 |
| Python | 9 |
| C | 3 |
| 未标注 | 2 |
| MATLAB | 1 |
| Cuda | 1 |
| FORTRAN | 1 |

### 2.4 许可证分布

| License | 数量 |
|---|---:|
| MIT | 19 |
| 未声明 | 19 |
| GPL | 9 |
| BSD-3-Clause | 8 |
| LGPL | 3 |
| CECILL | 1 |
| Apache-2.0 | 1 |

---

## 三、每仓一句话速览

### Tier 1 方法专家仓

| # | 仓库 | ★ | 语言 | License | 一句话 |
|---|---|---:|---|---|---|
| 1 | **trixi-framework/Trixi.jl** | 737 | Julia | MIT | Trixi·可扩展 DG 框架（可压缩/多相/反应流/天体） |
| 2 | **Nek5000/Nek5000** | 493 | Fortran | BSD-3-Clause | 教学正统·Hesthaven-Warburton 系谱元法（SE）正统，可压缩/不可压/结构/多物理，GPU |
| 3 | **exadg/exadg** | 194 | C++ | GPL | ExaDG·exascale 高阶 DG（可压缩/不可压/多相） |
| 4 | **horses-framework/horses3d** | 167 | Fortran | MIT | HORSES3D·高阶 DG 流动模拟（可压缩） |
| 5 | **nasa/GFR** | 76 | Fortran | - | NASA Glenn Flux Reconstruction（FR/huxi 正统） |
| 6 | **cpraveen/dflo** | 58 | C++ | - | 可压缩流 DG 求解器 |
| 7 | **JacobCrabill/Flurry-cpp** | 52 | C++ | GPL | 非结构单/重叠网格 Flux Reconstruction |
| 8 | **flexi-framework/galaexi** | 49 | Fortran | GPL | GALÆXI·高阶 DG 可压缩框架（flexi 系） |
| 9 | **dshawul/NebulaSEM** | 29 | C++ | BSD-3-Clause | FV+高阶 DG 混合（SEM/DG 双模） |
| 10 | **flexi-framework/elexi** | 10 | Fortran | GPL | 高阶非结构 DG 框架（flexi 系） |
| 11 | **yiminllin/ESDG-CNS** | 9 | Julia | MIT | 熵稳定模态 DG 方案 + 壁面处理（CNS） |

### Tier 2 可微同族与框架

| # | 仓库 | ★ | 语言 | License | 一句话 |
|---|---|---:|---|---|---|
| 1 | **dealii/dealii** | 1757 | C++ | LGPL | 通用 FEM 库（DG 算例/能力齐全） |
| 2 | **CEED/libCEED** | 273 | C | BSD-3-Clause | 高阶有限元核库（DG/SEM 算子） |
| 3 | **enrico-dev/enrico** | 70 | C++ | BSD-3-Clause | ENRICO·exascale 核反应堆（DG 中子输运） |
| 4 | **McGillCompAero/PHiLiP** | 59 | C++ | LGPL | hp 自适应高阶 PDE 库（含 DG） |
| 5 | **ABAtanasov/GalerkinSparseGrids.jl** | 47 | Julia | MIT | 稀疏格 DG 离散（Julia） |
| 6 | **li12242/NDG-FEM** | 47 | MATLAB | MIT | 节点型 DG 有限元方法（MATLAB） |
| 7 | **JuntaoHuang/adaptive-multiresolution-DG** | 44 | C++ | MIT | 自适应多分辨率 DG（C++） |
| 8 | **inducer/hedge** | 31 | Python | MIT | Hybrid-and-Easy DG 环境（Python） |
| 9 | **snek5000/snek5000** | 30 | Python | BSD-3-Clause | Nek5000 的 Python 前端框架（脚本化/可微桥接） |
| 10 | **tgregov/Multiphysics** | 20 | C++ | - | DG FEM Runge-Kutta 多物理求解器 |
| 11 | **ARTLab-GT/hexed** | 14 | C++ | MIT | DG CFD 求解器（自动非结构） |
| 12 | **Nek5000/Nekbone** | 13 | Fortran | - | Nek5000 代理应用（C++/Python 重写），谱元框架 |
| 13 | **FEniCS/dolfinx** | 0 | - | GPL | 现代 FEniCS（含 DG 弱形式） |

### Tier 3 专项能力

| # | 仓库 | ★ | 语言 | License | 一句话 |
|---|---|---:|---|---|---|
| 1 | **Nek5000/NekExamples** | 44 | Python | - | Nek5000 官方算例集（教学正统配套） |
| 2 | **pkestene/ppkMHD** | 30 | C++ | CECILL | MPI+Kokkos 谱差分 MHD |
| 3 | **nekStab/nekStab** | 29 | Python | BSD-3-Clause | Nek5000 稳定性/分岔/敏感性分析 |
| 4 | **YunchaoYang/NekIBM** | 27 | Fortran | BSD-3-Clause | Nek5000 多相流直接施加 IBM 平台 |
| 5 | **eX-Mech/pymech** | 26 | Python | GPL | Nek5000/SIMSON 的 Python 套件 |
| 6 | **vavrines/FluxReconstruction.jl** | 25 | Julia | MIT | 通量重构（平流-扩散） |
| 7 | **NoseKnowsAll/Local-DG** | 19 | C++ | - | 3D Navier-Stokes 局部 DG 求解器 |
| 8 | **Nek5000/NekROM** | 16 | Fortran | MIT | Nek5000 降阶模型（ROM）框架 |
| 9 | **KTH-Nek5000/KTH_Framework** | 16 | - | - | KTH 面向 Nek5000 的工具箱框架 |
| 10 | **ywkawai/FE-Project** | 16 | Fortran | - | 大气流 DG 有限元（Fortran） |
| 11 | **ranocha/PolynomialBases.jl** | 15 | Julia | MIT | 谱元法多项式基（Gauss-Lobatto/Legendre） |
| 12 | **romerojosh/zefr** | 14 | C | BSD-3-Clause | 直接通量重构高阶 CFD 求解器 |
| 13 | **b-fg/Flower1D.jl** | 14 | Julia | GPL | 1D 通量重构流体求解器（Julia） |
| 14 | **horses-framework/horses3d-gpu** | 13 | Fortran | MIT | HORSES3D GPU 版 |
| 15 | **trevor-vincent/disco4est** | 13 | C | LGPL | 可扩展 hp 自适应 DG 求解器 |
| 16 | **sseyler/HERMESHD** | 12 | Fortran | - | DG 3D 波动流体动力学 |
| 17 | **L1ssandra/Simple-Fortran-DG** | 12 | Fortran | - | 简单 Fortran DG（教学，无 limiter） |
| 18 | **marchdf/dg1d** | 11 | Python | Apache-2.0 | 1D Euler DG（教学） |
| 19 | **martyfuhry/DGCUDA** | 11 | C++ | - | CUDA 上的 DG 实现 |
| 20 | **athelas-astro/athelas** | 10 | C++ | GPL | 拉格朗日辐射流体 DG |
| 21 | **jaisw7/dgfs1D_gpu** | 9 | Python | - | 1D DG 快速谱（DGFS）GPU |
| 22 | **tristanmontoya/StableSpectralElements.jl** | 9 | Julia | - | 可证稳定谱元/DG（Julia） |
| 23 | **liujiamingustc/Npu_Flux_reconstruction_Software** | 8 | Fortran | GPL | 基于 GFR 的非结构 FR 软件 |
| 24 | **al0vya/gpu-mwdg2** | 7 | Cuda | - | GPU 并行（多）小波自适应 FV+DG |
| 25 | **dasikasunder/ADER-DG** | 7 | Fortran | MIT | ADER-DG + 子单元限界 |
| 26 | **jlchan/EntropyStableEuler.jl** | 6 | Julia | MIT | 可压缩 Euler/NS 熵稳定通量与变量 |
| 27 | **stillyslalom/TrixiShockTube.jl** | 3 | Julia | MIT | DG 激波管模拟（Trixi 系） |
| 28 | **LJaugey/Discontinous-Galerkin-method** | 3 | C++ | - | 高阶熵稳定 DG 方案 |
| 29 | **trixi-framework/paper-2023-es_two_layer** | 3 | Julia | MIT | 熵稳定两层 DG（Trixi 可复现仓） |
| 30 | **Andres-MG/2023_gmm_shock_sensor** | 3 | Fortran | - | 无监督马赫数激波传感器 |
| 31 | **hsongxa/dg-on-cuda** | 3 | C++ | MIT | CUDA/C++ DG 实现 |
| 32 | **rbklein/DisCO2** | 2 | Python | - | 超临界 CO2 熵稳定 DG |
| 33 | **MCortesHernan/DIMAGNO-DG** | 2 | Python | MIT | DG 多流体 PDE 求解 |
| 34 | **lightd22/ppmdg** | 1 | FORTRAN | - | PPM 有限体积 + 节点 DG 混合（2D 平流） |
| 35 | **atEchoOff/Quadratic-Knapsack-Limiting** | 1 | Julia | - | 子单元限界（二次背包问题） |
| 36 | **cgt3/ES-CutDG** | 1 | Julia | MIT | 熵稳定高阶 CutDG |

---

## 四、feature_ledger GAP 锚点提议

对标 LBM 战役 `GAP-LBM-MCMP-*` 命名法（GAP-<域>-<子域>-<能力>），提议 DG 域 8 个锚点：

| GAP ID | 标题 | OSS 证据（锚定仓库） |
|---|---|---|
| **GAP-DG-ENTROPY-STABLE-FLUX** | 熵稳定通量/熵变量（可压缩 Euler/NS） | yiminllin/ESDG-CNS（模态 DG 熵稳定 + 壁面处理）; jlchan/EntropyStableEuler.jl（熵稳定通量与变量）; rbklein/DisCO2（超临界 CO2 熵稳定 DG）; trixi-framework/paper-2023-es_two_layer（两层熵稳定 DG）; cgt3/ES-CutDG（熵稳定 CutDG）; knstmrd/paper_ec_trixi_chem（化学反应流熵稳定通量） |
| **GAP-DG-SUBCELL-LIMITER** | 子单元限界/激波捕捉（shock capturing） | dasikasunder/ADER-DG（ADER-DG + 子单元限界）; msarrafj/LimiterDGcompressibleflow（保界 DG）; atEchoOff/Quadratic-Knapsack-Limiting（子单元限界优化）; Andres-MG/2023_gmm_shock_sensor（无监督激波传感器）; LJaugey/Discontinous-Galerkin-method（高阶熵稳定 DG + 传感器） |
| **GAP-DG-GPU-KERNEL** | GPU 化 DG 内核（CUDA/Kokkos） | horses-framework/horses3d-gpu（HORSES3D GPU 版）; hsongxa/dg-on-cuda（CUDA/C++ DG）; martyfuhry/DGCUDA（CUDA DG）; ianrgraham/gale（Rust GPU DG）; MatthewBonanni/mallard（GPU 高阶 FV/DG 非结构）; pkestene/ppkMHD（MPI+Kokkos 谱差分） |
| **GAP-DG-FVM-HYBRID** | DG-FVM 混合（双模/混合离散） | dshawul/NebulaSEM（FV+高阶 DG 双模）; lightd22/ppmdg（PPM FV + 节点 DG 混合）; al0vya/gpu-mwdg2（小波自适应 FV+DG） |
| **GAP-DG-SD-FR-HUXI** | 谱差分/通量重构 SD/FR（huxi/CPR）近亲 | nasa/GFR（NASA Glenn FR 正统）; JacobCrabill/Flurry-cpp（非结构 FR）; vavrines/FluxReconstruction.jl（FR 平流-扩散）; romerojosh/zefr（直接通量重构）; b-fg/Flower1D.jl（1D FR）; pkestene/ppkMHD（谱差分 MHD） |
| **CAP-DG-VALIDATION-SURFACE** | DG 验证面（基准算例+解析解对照） | Nek5000/NekExamples（官方算例集）; stillyslalom/TrixiShockTube.jl（激波管）; marchdf/dg1d（1D Euler 教学）; L1ssandra/Simple-Fortran-DG（教学 DG） |
| **GAP-DG-HP-ADAPTIVE** | hp 自适应网格细化 | McGillCompAero/PHiLiP（hp 自适应高阶 PDE 库）; trevor-vincent/disco4est（hp 自适应 DG）; JuntaoHuang/adaptive-multiresolution-DG（自适应多分辨率 DG）; jamesliu516/amrdg2d（DG AMR 2D） |
| **GAP-DG-IMPLICIT-TIME** | 隐式/Radau 时间推进（刚性问题） | Nek5000/Nek5000（Radau IIA 隐式时间推进）; trixi-framework/Trixi.jl（隐式 DG 时间积分）; tgregov/Multiphysics（DG FEM Runge-Kutta） |

各锚点 rationale 见 `artifacts/dg_disc/gap_anchors.json`。

---

## 五、注意点（踩坑 / 局限 / 适用范围）

1. **stars 非质量唯一指标**：Nek5000（493★）是教学正统但 stars 远低于 SPlisHSPlasH 类图形学代码；
   工业/学术 DG 代码（HORSES3D/GALÆXI/ExaDG）stars 中等但论文影响力大。分级综合方法覆盖度、验证深度、活跃度。
2. **GitHub API 限制**：未认证 search 10 req/min、core 60/h；60 仓核实恰好用满一个 core 小时额度，
   长尾仓库（0-2★ 个人作业）未全部入册，见 candidates_raw.json（349 仓原始池）。
3. **License 识别**：GitHub 未识别时记为「未声明」，实际 license 可能在源码文件中，深盘时逐仓确认。
4. **SEM 与 DG 的边界**：Nek5000 系严格说是谱元法（SE），按任务口径（教学正统 Hesthaven-Warburton 系）纳入；
   纯 SEM 地震代码（SPECFEM/AxiSEM 等）未入册（非 DG 且非任务覆盖域）。
5. **本普查不深盘**：按 LBM/SPH 协议，wave-0 只注册候选；深盘（代码审计/精度验证/能力分级）= wave-1+。

---

## 六、误差判定

本单为普查注册（非精度验证），无计算结果需对标基准，误差判定不适用。

**验收判据逐项核对**（任务要求 vs 实际）：

| 判据 | 要求 | 实际 | 是否达标 |
|---|---|---|---|
| 入册仓数 | ≥20 | 60 | ✅ |
| GAP-DG-* 锚点 | ≥4 | 8 | ✅ |
| 覆盖域 | 6 域全覆盖 | 7 域（含 framework 扩展域） | ✅ |
| registry 机器可解析 | JSON | artifacts/oss_dg_registry.json | ✅ |

**诚实声明**：
- ✅ 完成：60 仓普查 + 3-tier 分级 + 8 GAP 锚点提议 + 机器可解析 JSON + 3 图
- ⚠️ 未完成：未逐仓 clone 审计代码（= wave-1 任务）；部分 license 需源码确认
- ⚠️ 未覆盖：非 GitHub 公开的 DG 代码（如部分国防/工业 DG 求解器）未纳入

---

## 七、后续波次建议

| 波次 | 任务 | 优先级 |
|---|---|---|
| wave-1 | 深盘 Tier-1 前 3 仓（Nek5000 / ExaDG / HORSES3D）：代码审计 + 能力分级 + 验证面搭建 | 🔴 |
| wave-2 | GAP-DG-ENTROPY-STABLE-FLUX 实施：熵稳定通量对标 ESDG-CNS/Trixi | 🔴 |
| wave-3 | GAP-DG-SUBCELL-LIMITER 实施：激波管/涡对基准 + 子单元限界 | 🔴 |
| wave-4 | GAP-DG-GPU-KERNEL：GPU DG 内核（对标 horses3d-gpu/mallard） | 🟡 |
| wave-5 | CAP-DG-VALIDATION-SURFACE：跨代码激波管/Taylor-Green 对比 | 🟡 |

---

**产物清单**：
- `artifacts/oss_dg_registry.json` — 60 仓机器可解析注册 + 8 GAP 锚点
- `artifacts/dg_disc/` — 普查工作目录（候选池/核实数据/图表/脚本）
- `docs/oss_inventory_dg/REPORT_oss-dg-disc.md` — 本报告