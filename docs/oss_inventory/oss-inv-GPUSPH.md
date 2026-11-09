# 开源 SPH 技术盘点 · 单项目深盘：`GPUSPH/gpusph`

> 战役：OSS-SPH wave-2（osssph-w2）· tier 1 · mission 8654 · 集团军
> 盘点对象：`https://github.com/GPUSPH/gpusph` @ `08ffd94ddc4a076361f0e5f6e2bfde7874ba087f`（`master` tip，2021-05-29 "Add more info to CONTRIBUTING"）
> 盘点方式：**读代码本体**（逐条结论带 `文件:行`）+ 一次**可复算探针**（把源码里可抽取的核函数/EOS 闭式重新实现并求残差，见 §四、§七）
> 许可证警示（先给结论）：**GPL-3.0-or-later，强 copyleft** → 对 Tensor 生态**只能 reference-only（读思想 + 独立实现）**，任何代码级移植/derivative work 都不可接受。

---

## 零、取证与复现（provenance）

| 项 | 值 | 取证通道 |
|---|---|---|
| 仓库 / 默认分支 / tip | `GPUSPH/gpusph` / `master` / `08ffd94ddc4a076361f0e5f6e2bfde7874ba087f` | 仓库 HTML（`"defaultBranch":"master"`, `"currentOid":"08ffd94…"`）+ `commits/master.atom` 第 1 条 |
| 最近提交（atom 全集 20 条） | 2021-05-29 `08ffd94`；2019-06-13 `ec5e7b1`/`5a5f614`；2019-06-11 `0d8ac1d`/`67792f9`；2019-06-07 `3155893`/`36192d0`/`a9b7039`/`033d124`；2019-06-06 `7e4b099` … | `commits/master.atom`（GitHub 托管 feed） |
| 提交总数 / stars | 5,350 commits / **188 stars** | 仓库 HTML 内嵌 JSON（`"stargazerCount":188`、`"5,350 Commits"`） |
| forks / open issues | **未读到（insufficient，不臆测）** | 匿名 REST `core` 配额在本单取数时已耗尽（`rate_limit` → `core.remaining=0`），HTML 侧未暴露 |
| License | **GPL-3.0-or-later**：仓内 `GPLv3.txt` + 源码头 "GNU General Public License … version 3 or later"（`src/particledefine.h:13-21`） | 文件本体（比 API SPDX 字段更硬） |
| 语言 | C++/CUDA：`.cc` 36,667 行 / `.h` 22,758 / `.cu` 18,713 / `.cuh` 1,238（程序化统计） | `artifacts/oss-sph/gpusph/verify_kernels_eos.json` → `static.loc_by_ext` |
| 源码快照 | codeload tarball，**1,159,823 B**，解包 **5.0 MB / 378 文件**（350 文本文件、98,104 文本行） | 解包到 `/tmp/gpusph_src`（**未进入本仓**） |
| CI | **无**：无 `.github/`、无 `.travis.yml`、无 `.gitlab-ci.yml`（`ls -a` 仅 `.gitignore`） | 源码树 |
| 测试框架 | **无单元测试框架**；验证面 = 40 个 problem + shell/python 校验脚本 + **仓外**参考数据包 | 源码树 + `scripts/` |
| 探针产物 | `artifacts/oss-sph/gpusph/verify_kernels_eos.json`、`fig1_kernels.png`、`fig2_eos.png`；脚本 `runs/m8654/gpusph_probe.py` | 本单运行 |

**取证方式（诚实）**：本机 `curl` 被安全策略拦截、REST `core` 配额被同 IP 耗尽 → 元数据改用 **GitHub HTML + commits atom feed + codeload tarball**（均为 GitHub 官方托管面，非记忆）。故 **forks/open_issues = insufficient**，其余全部源码级。

---

## 一、案例介绍（物理背景 / 定位）

GPUSPH 自述 "The world's first CUDA implementation of Weakly-Compressible SPH"（`README.rst:1-10`）。它是**海岸/水工自由面 WCSPH 求解器**：密度由连续性方程积分、压力由 Tait 状态方程显式给出、时间推进为预测-校正；邻居搜索与力计算全在 GPU；壁面主用 **semi-analytical (SA) 边界**（对核函数在壁外的缺失部分做解析/求积补偿）。工程面覆盖溃坝、造波/爬高、系泊浮体、明渠进出流、多相（水-油）、颗粒流变 μ(I)、DEM 耦合。

**计算平台**：NVIDIA CUDA（`README.rst` 要求 ≥7.5）+ 多 GPU + MPI 多节点，**无任何 CPU 后端**。本沙箱无 nvcc/无 GPU（集群节点为 loong64 CPU）→ **不可能编译运行**，这决定了本单验证策略为"抽取闭式 + 独立复算"而非"跑算例"。

探针参数（取自仓内算例，非外部文献）：γ=7、c₀=50 m/s、ρ₀=1000 kg/m³（`src/problems/AccuracyTest.cu:65-68`：`set_equation_of_state(0, 7.0, 50)`、`add_fluid(1000.0)`）。

---

## 二、源码级方法清单（逐条 `文件:行`）

### 2.1 SPH 变体：**只有 WCSPH 家族，无任何隐式/投影变体**

| 项 | 结论 | 证据 |
|---|---|---|
| WCSPH | ✅ 唯一求解范式；压力由 EOS 显式给出 | `README.rst:1-10`；`src/cuda/phys_core.cu:100-103` |
| ISPH / PCISPH / DFSPH / IISPH / PBD | ❌ **不存在** | 全树 `grep -i "isph\|incompressible"` → **0 命中** |
| "有效压力求解器" ≠ 不可压 | 是**颗粒流变 μ(I)** 的有效压力：Jacobi 迭代解 D·p=B | `src/integrators/PredictorCorrectorIntegrator.cc:1047-1160`；`docs/user-guide/gpusph-theory/gpusph-theory.tex:1102-1226` |
| 离散格式（4 种） | SPH_F1（P/ρ²）/ SPH_F2（P）/ SPH_GRENIER（P/σ，比容）/ SPH_HA（Hu & Adams） | 枚举 `src/particledefine.h:110-116`；名字 `:126-137`；`precalc_pressure` 特化 `src/cuda/forces_kernel.def:422-470`；`compute_pressure_contrib` `:2400-2450` |

### 2.2 核函数：4 种，默认 Wendland

| 核 | W | (1/r)dW/dr | 系数（源内表达式） |
|---|---|---|---|
| Cubic spline | `src/cuda/sph_core.cu:74-87` | `:146-160` | `1/(πh³)`、`3/(4πh⁴)`（`src/cuda/forces.cu:274,286`） |
| Quadratic | `:93-101` | `:163-173` | `15/(16πh³)`、`15/(32πh⁴)`（`:280,288`） |
| **Wendland**（默认） | `:108-118` | `:176-183` | `21/(16πh³)`、`105/(128πh⁵)`（`:284,290`） |
| Gaussian（截断 δ=R·h） | `:127-136` | `:186-190` | 常数含 `erf(R)` 重归一 `src/cuda/forces.cu:290-310` |
| 默认声明 | `\default{Wendland}` `src/particledefine.h:72-84` | — | — |

### 2.3 边界处理：4 种壁面 + 开放边界

| 模型 | 机制 | 证据 |
|---|---|---|
| **SA_BOUNDARY**（主用，半解析） | γ_as（核在壁外缺失部分的角度积分）修正密度/压力梯度；γ 有**解析梯度**与**数值求积**两路径，或由平流方程动态更新 | 枚举 `src/particledefine.h:175-181`；`gradGamma`（解析）`src/cuda/gamma.cuh:243-370`；`Gamma`（求积）`:397-434`；`ENABLE_GAMMA_QUADRATURE` `src/simflags.h:120-128`；SA 压力项 `src/cuda/forces_kernel.def:2400-2407` |
| DYN_BOUNDARY（动态边界粒子） | 边界粒子亦积分密度/内能、参与邻域 | `src/cuda/euler_kernel.def:507-508`；`src/cuda/buildneibs_kernel.cu:598,1059`；`src/cuda/forces_kernel.cu:307-337` |
| LJ / MK 边界 | 排斥力型壁面（Lennard-Jones / Monaghan-Kajtar） | `src/cuda/forces_kernel.cu:95-140` |
| 开放边界（进/出流） | **Riemann 不变量**：给速度推密度 / 给压力推速度（膨胀波·激波·接触间断分类） | `calculateIOboundaryCondition` `src/cuda/boundary_conditions_kernel.cu:107-200`；`ENABLE_INLET_OUTLET` `src/simflags.h:95` |

### 2.4 时间步控制：预测-校正 + 三判据自适应步长

| 项 | 结论 | 证据 |
|---|---|---|
| 积分器 | Predictor–Corrector（含颗粒相 EFFPRES 交错相位）、Repacking（初始重排） | `src/integrators/PredictorCorrectorIntegrator.h:38-56`；`src/integrators/RepackingIntegrator.h` |
| 自适应 dt | `dt = dtadaptfactor · min(h/c_max, √(h/g), dt_visc)`，默认 `dtadaptfactor=0.3`，默认开 | `ProblemCore::check_dt` `src/ProblemCore.cc:749-800`；参数 `src/simparams.h:140-146`；`ENABLE_DTADAPT` `src/simflags.h:65-71`、默认 `:167` |
| 状态方程 | Tait `p=B((ρ/ρ₀)^γ−1)`，`B=ρ₀c₀²/γ`；反演 `RHO(p)`；`c=c₀(ρ/ρ₀)^((γ−1)/2)` | `src/cuda/phys_core.cu:100-103 / 109-111 / 131-133`；`B` 赋值 `src/physparams.h:514` |
| 相对密度存储 | 存 **ρ̃ = ρ/ρ₀ − 1** | `src/cuda/phys_core.cu:99,139-141` |

### 2.5 δ-SPH 家族（密度扩散）：3 模型可选，**默认 Brezzi**

| 项 | 证据 |
|---|---|
| 枚举 none / **Ferrari** / **Colagrossi** / **Brezzi**，`\default{Brezzi}` | `src/particledefine.h:142-172` |
| Ferrari（Mayrhofer 2013） | `src/cuda/forces_kernel.def:1608-1740`（含 SPH_HA 专用与边界项；自由面重力修正 `:1691-1700`） |
| Brezzi（2014） | `:1761-1890` |
| Molteni & Colagrossi（2009） | `:1910-1960` |
| 系数 `densityDiffCoeff` 默认 0.1，**三模型共用同一变量** | `src/simparams.h:151-178`（注释明示 brezzi/ferrari 系数只是 GUI 影子）、`src/cuda/forces.cu:392` |
| **禁跨相扩散**（多相关键设计） | `src/cuda/forces_kernel.def:1651-1656` "No inter-phase diffusion" |

### 2.6 黏性 / 湍流 / 流变

- 湍流枚举 `LAMINAR_FLOW / ARTIFICIAL / SPS / KEPSILON`：`src/visc_spec.h:161-167`；黏性离散 `MORRIS / MONAGHAN / ESPANOL_REVENGA`：`src/visc_spec.h:228-232`（沿相对速度 vs 沿相对位置 ⇒ 角动量守恒取舍，见 `src/cuda/forces_kernel.def:2501-2520` 注释）。
- Monaghan 人工黏性（α 型 + `d_epsartvisc` 正则）：`src/cuda/forces_kernel.def:2540-2560`；算例给法 `set_artificial_visc(0.3*0.005/deltap)` `src/problems/AccuracyTest.cu:69`。
- k-ε：理论 `docs/user-guide/gpusph-theory/gpusph-theory.tex:636-788`；压力修正项 `src/cuda/forces_kernel.def:389-401`；visc-spec 组合矩阵 `src/visc_spec.h:315-376`。
- 非牛顿/颗粒流变（μ(I)、Herschel-Bulkley、幂律）：`src/visc_spec.h:52-140`、`src/physparams.h:186-235`；有效压力初值 `src/problems/DamBreakMobileBed.cu:161-195`、`src/ProblemCore.cc:1665-1675`。

### 2.7 多相 / 自由面 / FSI 能力

| 能力 | 结论 | 证据 |
|---|---|---|
| 多流体 | ✅ 每相独立 ρ₀/c₀/γ/B | `ENABLE_MULTIFLUID` `src/simflags.h:137-141`；`src/physparams.h:440-520` |
| 相界面 | ① 比容（Grenier）自然处理密度跳变；② 密度求和 `ENABLE_DENSITY_SUM`；③ 简化表面张力 `ε_interface(|p_i|+|p_j|)` | `src/particledefine.h:110-116`；`src/simflags.h:110-116`；`apply_pseudo_surface_tension` `src/cuda/forces_kernel.def:2228-2240`，常数 `src/cuda/phys_core.cu:43` |
| 表面张力 | **无 CSF/continuum surface force**，只有上述界面稳定项 | 全树无 `CSF`，仅 `d_epsinterface` |
| 自由面 | ✅ 核心卖点：Shepard/MLS 密度滤波 + XSPH 抑制自由面密度亏空 | 滤波枚举 `src/particledefine.h:250-258`；`shepardDevice` `src/cuda/forces_kernel.cu:418-422`、`MlsDevice` `:508-512`；XSPH `src/simflags.h:73-80` |
| FSI（弹性固体耦合） | ❌ **无**：仅刚体 ODE + 浮体力/力矩 + DEM 颗粒接触 | `ENABLE_MOVING_BODIES` `src/simflags.h:87`、`ENABLE_DEM` `:86`、`DemLJForce` `src/cuda/forces_kernel.cu:207-235` |
| 可微 / AD | ❌ 无 | 全树无 autodiff | 

### 2.8 GPU / MPI 并行策略

| 维度 | 实现 | 证据 |
|---|---|---|
| CUDA 内核 | `src/cuda/` 40 文件 / 18.7k 行；邻域、力、欧拉推进、边界、后处理分治 | `src/cuda/{buildneibs,forces,euler,boundary_conditions,density_sum,post_process}*.cu` |
| 编译期特化矩阵 | 每 (formulation × kernel × BC × visc × density-diffusion) 组合生成一个模板实例 ⇒ 内核零运行时分支 | `src/problem_gen.tpl`、`problem_builder/generate_gpusph_sources.cpp`、`src/cuda/forces_kernel.def`（4,587 行 SFINAE 特化族） |
| 多 GPU | 单机多卡：静态切片 + halo 交换；≤8 卡/节点 | `src/multi_gpu_defines.h:36-50`（`GLOBAL_DEVICE_BITS=8`、`DEVICE_BITS=3` ⇒ 256 设备 / 32 节点 / 8 卡每节点）、`src/GPUWorker.cc`（3,332 行） |
| 多节点 MPI | `NetworkManager`（582 行）切片通信 + 同步命令 | `src/NetworkManager.cc`、`src/NetworkManager.h:50` |
| 性能开关 | `--gpudirect`（IB 直通显存）、`--striping`（计算/传输重叠）、async MPI | `src/Options.h:79-80`、`src/GPUWorker.cc:438`、`mpi_command`（`ASYNCMPI`） |
| 域几何 | 静态 cell grid + 两级哈希（inner/edge/outer） | `src/cuda/cellgrid.cuh`、`src/multi_gpu_defines.h:55-90` |

---

## 三、验证面盘点

### 3.1 自带算例（40 个 problem）

`AccuracyTest, BiFluidPoiseuilleDYN/SA, Bubble, BuoyancyTest, ChannelIO, CompleteSaExample, DEMExample, DamBreak3D, DamBreakGate, DamBreakMobileBed, DynBoundsExample, GenericProblem, LithostaticDYN/LJ/SA, LockExchange, Objects, OffshorePile, OilJet, OpenChannel, Poiseuille, PoiseuillePapanastasiou, ProblemExample, RTInstability, Seiche, SlidingWedge, SolitaryWave, Spheric2LJ/SA, StillWater, StillWaterRepackSA, StillWaterSA, TurbulentPoiseuilleFlowSA, WaveTank`（含 `.inc` 变体，程序化枚举见 JSON `static.problems`）。

### 3.2 量化验证脚本（真误差量，非"跑通即可"）

| 脚本 | 量化内容 | 证据 |
|---|---|---|
| `scripts/validate-poiseuille.py` | 解析解 `u(z)=F/(2ν)((L/2)²−z²)`；扫 `ppH ∈ {16,32,64}`（收敛研究）；输出 **L∞/L1/L2** | `:37-46` 解析式；`:56,100,126-129` 误差汇总 |
| `scripts/validate-bifluidepoiseuille.py` | 两相 Poiseuille 分层解对标 | 脚本本体 |
| `scripts/validate-lithostatic.py` | 静水压力剖面 + **L2 误差随时间收敛**（t⁺∈[1.5,3.5]） | `:1-15` 头注释 |
| `scripts/check-problem.sh` / `check-all-problems.sh` | 与 `*reference` dump **逐项比对**（默认 1000 步；多卡 `mgpu-reference`） | `check-problem.sh:1-35`、`check-all-problems.sh:1-25` |
| `Makefile` | `list-problems` / `make <problem>` / `make test` 为算例即测试入口 | `Makefile:295-310,885,1233` |

### 3.3 手册验证章节（含实验对标，但**无误差数字表**）

`docs/user-guide/gpusph-valid/gpusph-valid.tex`：Spheric2（`:19`，Kleefsman 溃坝-障碍物实验，压力/水位探针**定性曲线对比**）、Hydrostatic basin（`:233`，压力剖面 + L2 误差进展）、Channel IO（`:481`）、Turbulent Poiseuille（`:673`）、Oscillating cube（`:843`，浮体 heave 对标实验）。**要点**：给 L2 误差"趋势"与图形，**无可机读误差数字表**（`:432-446`）。

### 3.4 缺什么（诚实）

1. **参考数据不在仓内**：`scripts/get-data-files` 从 `http://www.gpusph.org/downloads/data_files.tar.gz` 拉取 → 本单**未下载**、不臆造，标记 **WITHHELD**；因此 `check-problem.sh` 的绿/红在本单**不可复现**。
2. **无 CI、无单元测试框架**：上游回归全靠人工跑 `check-all-problems.sh`。
3. **无 Taylor-Green**（`grep TaylorGreen` → 0）；**无 2D Martin–Moyce 定量 dam-break**（DamBreak3D 仅算例定义，无参考数据）。
4. 静态面（Poiseuille/静水）是仓内**唯一自带真值**的量化基准 → 正是可迁移价值最高处（§五）。

---

## 四、计算结果（本单可复算的量化证据）

> GPUSPH **无法在此环境编译运行**（无 nvcc/无 GPU；集群 loong64 CPU），故**不给出**任何"GPUSPH 求解精度"数字。替代做法：把源码中可抽取的闭式（4 核 + 其导数 + Tait EOS + 声速）程序化重建（`runs/m8654/gpusph_probe.py`，系数由正则从 `src/cuda/forces.cu` 抽取，**零手抄**），独立求残差。

**表 1 核函数归一化 `∫₀^R W(r)·4πr²dr = 1`（h=1）**

| 核 | 32 面板 | 128 面板 | 1024 面板 |
|---|---:|---:|---:|
| Cubic spline | 1.1e-16 | 1.1e-16 | 1.1e-16 |
| Quadratic | 3.8e-06 | 1.5e-08 | 3.6e-12 |
| Wendland | 1.9e-08 | 4.5e-12 | 0.0 |
| Gaussian（截断 3h，含 erf 重归一） | 1.2e-07 | 4.9e-10 | 1.2e-13 |

**表 2 核导数恒等式 `F(r) = (1/r)dW/dr`**（4 阶中心差分，6 个采样半径取最大相对误差）：Cubic 7.1e-13 · Quadratic 3.1e-12 · Wendland 4.0e-13 · Gaussian 4.3e-13。

**表 3 Tait EOS 与声速自洽**（γ=7, c₀=50 m/s, ρ₀=1000 → B=357,142.86 Pa）：反演往返 `ρ̃→p→ρ̃` 最大绝对误差 **1.1e-16**（ρ̃∈[0,0.10]）；声速恒等式 `c=√(dp/dρ)` 最大相对误差 **4.3e-11**。

**图**：`artifacts/oss-sph/gpusph/fig1_kernels.png`（左：归一化残差 vs 求积面板数，log-log，含 3% 容差线；右：导数恒等式残差柱状）；`fig2_eos.png`（左：p(压缩率) 曲线标 +1%/+5%/+10%；右：往返与声速残差，含 3% 容差线）。

### 4.1 机器块

[OSS-INV]{"campaign":"osssph-w2","repo":"GPUSPH/gpusph","tier":1,"methods":["WCSPH only (explicit Tait EOS; master tip 08ffd94d)","4 discretisations: SPH_F1 (P/rho^2), SPH_F2 (P), SPH_GRENIER (P/sigma, specific volume), Hu-Adams","density diffusion (delta-SPH family): Ferrari | Molteni-Colagrossi | Brezzi, default Brezzi, coeff 0.1, no inter-phase diffusion","kernels: Cubic spline | Quadratic | Wendland (default) | Gaussian (truncated, erf-renormalised)","turbulence: artificial viscosity | SPS | k-epsilon; viscous models Morris | Monaghan | Espanol-Revenga","granular rheology mu(I)/Herschel-Bulkley/power-law + effective-pressure Jacobi solver","moving rigid bodies (ODE) + DEM contact; NO elastic FSI","NO ISPH/PCISPH/DFSPH/IISPH/PBD (zero hits for 'isph|incompressible')","NO differentiable/AD"],"boundary":"4 wall models: Lennard-Jones, Monaghan-Kajtar, Semi-Analytical (gamma_as with analytic gradGamma plus quadrature, optional dynamic gamma advection), Dynamic boundary particles; open boundaries via Riemann invariants (impose velocity or pressure)","gpu":"CUDA-only, no CPU backend: 40 files / 18.7k lines in src/cuda; compile-time template instantiation per (formulation x kernel x BC x visc x diffusion); multi-GPU static slicing + halo, up to 8 GPUs/node, 32 nodes (multi_gpu_defines.h:36-50); MPI NetworkManager; --gpudirect / --striping / async MPI","validation":"40 problem .cu cases; quantitative scripts: validate-poiseuille.py (analytic u(z), ppH 16/32/64 sweep, Linf/L1/L2), validate-bifluidepoiseuille.py, validate-lithostatic.py (L2 vs time); check-problem.sh diffs against *reference dumps whose data tarball is external to the repo (WITHHELD); manual validation chapter: Spheric2/Kleefsman, hydrostatic basin, channel IO, turbulent Poiseuille, oscillating cube (qualitative curves + L2 trends, no digitised error tables); NO CI, NO unit-test framework, NO Taylor-Green case","license":"GPL-3.0-or-later (GPLv3.txt + per-file headers) => copyleft: reference-only for the Tensor ecosystem, no code transplant","transferable":["SPHSOL-gpusph-01","SPHSOL-gpusph-02","SPHSOL-gpusph-03","SPHSOL-gpusph-04","SPHSOL-gpusph-05","SPHSOL-gpusph-06"]}[/OSS-INV]

---

## 五、对 TensorPhysics SPH 线的可迁移方案（6 条，全部 reference-only）

> **许可证总判定（显式）**：GPUSPH = **GPL-3.0-or-later**；Tensor 生态各仓为宽松许可（Apache-2.0/MIT 级）→ **禁止**代码复制、逐行翻译式"孪生移植"、或把其源码文本带入本仓；**允许**读论文级思想、公式（数学本身不可版权）、协议/接口设计、基准题设定与误差判据，并**独立实现 + 自写单测**。以下均按此约束落地。

[OSS-SOL]{"repo":"GPUSPH/gpusph","id":"SPHSOL-gpusph-01","kind":"reference","summary":"delta-SPH 密度扩散做成可切换策略（none/Ferrari/Molteni-Colagrossi/Brezzi，单系数默认 0.1），在自由面算例上量化人工扩散对峰值/波幅衰减的影响；源码力证 particledefine.h:149-172 枚举+默认、forces_kernel.def:1608-1960 三族实现、simparams.h:151-178 系数语义（含 GUI 影子系数设计债）","gap":"GAP-SPH-DELTA-SPH"}[/OSS-SOL]

[OSS-SOL]{"repo":"GPUSPH/gpusph","id":"SPHSOL-gpusph-02","kind":"reference","summary":"半解析(SA)壁面 gamma_as 补偿：解析梯度与数值求积两路径互校 + 可选动态 gamma 平流，为静水/溃坝静压剖面提供壁面侧正确性来源（gamma.cuh:243-370 / 397-434；simflags.h:120-128）；TensorPhysics 侧建议先 2D 静水 L2 收敛再上 dam-break","gap":"GAP-SPH-WCSPH-VALIDATION"}[/OSS-SOL]

[OSS-SOL]{"repo":"GPUSPH/gpusph","id":"SPHSOL-gpusph-03","kind":"reference","summary":"验证脚手架模板：解析基准 Poiseuille u(z)=F/(2nu)((L/2)^2-z^2) + 分辨率扫描 ppH in {16,32,64} + Linf/L1/L2 三范数误差表 + 参考 dump 逐项 diff（validate-poiseuille.py:37-46,56,100-129；check-problem.sh:1-35），直接用作 TensorPhysics SPH 收敛测试的产物格式与自动门限","gap":"GAP-SPH-WCSPH-VALIDATION"}[/OSS-SOL]

[OSS-SOL]{"repo":"GPUSPH/gpusph","id":"SPHSOL-gpusph-04","kind":"reference","summary":"自适应时间步三判据 min(h/c_max, sqrt(h/g), dt_visc) x 0.3（默认开），配合声速选取使密度波动 <1%（c0=50 m/s）；把该判据写成 TensorPhysics WCSPH 可审计 dt 策略并把 dt 序列作为收敛证据（ProblemCore.cc:749-800；simparams.h:140-146）","gap":"GAP-SPH-ISPH-CONVERGENCE"}[/OSS-SOL]

[OSS-SOL]{"repo":"GPUSPH/gpusph","id":"SPHSOL-gpusph-05","kind":"reference","summary":"多相三要点：比容(sigma)形式 Grenier 离散 + 界面处禁止跨相密度扩散 + 界面稳定项 eps*(|p_i|+|p_j|) 抗伪表面张力（particledefine.h:110-116；forces_kernel.def:1651-1656,2228-2240；phys_core.cu:43）；建议多相先做两相静水+液滴振荡并量测 spurious current","gap":"GAP-SPH-MULTIPHASE"}[/OSS-SOL]

[OSS-SOL]{"repo":"GPUSPH/gpusph","id":"SPHSOL-gpusph-06","kind":"reference","summary":"编译期特化矩阵设计（formulation x kernel x BC x visc x diffusion 各组合一模板实例，内核零运行时分支；problem_gen.tpl + generate_gpusph_sources.cpp + multi_gpu_defines.h:36-50 的切片/设备位编码），作为 GPU 后端代码生成与扩展性基准的架构参考，非代码移植","gap":"GAP-SPH-GPU-SCALING"}[/OSS-SOL]

**不能供锚的缺口（反向结论，同样入账）**：不提供 ISPH/DFSPH 隐式压力收敛证据（GAP-SPH-ISPH-CONVERGENCE 只能借"显式 WCSPH + 密度扩散"对照基线）；不提供 FSI 基准（无弹性固体耦合，仅刚体 ODE + DEM → GAP-SPH-FSI-BENCHMARK 须转向 SPHinXsys/PBD）；不提供可微 SPH（GAP-SPH-DIFFERENTIABLE 无素材）；不适用天体 Sedov/Sod（GAP-SPH-ASTRO-SEDOV）。

---

## 六、注意点（踩坑 / 局限 / 适用范围）

1. **相对密度陷阱**：GPUSPH 内部存 `ρ̃ = ρ/ρ₀ − 1`（`phys_core.cu:99,139-141`），EOS 亦以 ρ̃ 为入参。参考其公式时混用 ρ 与 ρ̃ 会产生非线性且难定位的偏差——本单第一号坑。
2. **Gaussian 核不是 exp 直接归一**：截断 R·h 下常数含 `erf(R)`（`forces.cu:290-306`）；3h 截断的正确常数本单复算 = **0.18012/h³**（非 1/π^{3/2}≈0.17959/h³），差异 ~3e-4 量级，照抄会在截断处引入归一化偏差。
3. **Cubic spline 归一化须按分段点拆积分**：不拆 [0,h]/[h,2h] 时 Simpson 会给出 O(1e-2) 假误差；按 `sph_core.cu:74-87` 分段后残差恒 1.1e-16。
4. **`densityDiffCoeff` 是三模型共用单变量**（`simparams.h:151-178` 注释明示 brezzi/ferrari 系数仅 GUI 影子）→ 要"按模型分别调参"必须改结构；迁移时勿复制此设计债。
5. **验证面不可复现**：参考 dump 与实验数据在 `gpusph.org/downloads/data_files.tar.gz`（仓内无）→ 离线环境 `check-all-problems.sh` 必然红；本单标记 WITHHELD，不声称"测试通过"。
6. **上游休眠**：`master` 末次提交 2021-05-29、无 CI。**推断（inference，未核实）**：项目可能在 gpusph.org / 独立 GitLab 继续开发。
7. **GPL 边界**：GPL-3.0-or-later 与宽松许可不兼容，搬 `.def/.cu` 会给下游带来传染性义务 → 全部迁移建议停留"公式/协议级"。
8. **验证强度边界**：§四 的机器精度残差只证明"抽取的闭式自洽且与经典归一化一致"，**不证明** GPUSPH 求解器在 GPU 上的动力学正确性（需 nvcc + GPU + 参考数据）。不越界表述。

---

## 七、误差判定（逐项，显式回答是否 <3%）

| 关键量 | 基准 | 实测误差 | 是否 <3% |
|---|---|---:|---|
| Cubic spline 归一化 ∫W·4πr²dr | 解析 1 | ≤1.1e-16 | ✅ |
| Quadratic 归一化 | 解析 1 | ≤3.8e-06（32 面板） | ✅ |
| Wendland 归一化 | 解析 1 | ≤1.9e-08（32 面板） | ✅ |
| Gaussian（3h 截断）归一化 | 解析 1（按源内 erf 常数） | ≤1.2e-07（32 面板） | ✅ |
| 核导数恒等式 F=(1/r)dW/dr | 4 阶差分 | ≤3.1e-12 | ✅ |
| Tait EOS 往返 ρ̃→p→ρ̃ | 恒等 | ≤1.1e-16 | ✅ |
| 声速恒等式 c=√(dp/dρ) | 解析 | ≤4.3e-11 | ✅ |
| **GPUSPH 求解器物理精度（dam-break/Poiseuille 报数）** | — | **无数据**（无 nvcc/无 GPU；参考数据仓外） | ❌ **不可判定**（honest：本单不产生该结论） |

**总判**：本单所有可复算的源码级量均在 <3% 内（实际到机器精度）；**求解器级精度判据无基准，声明未测**，不以 static 面数字冒充动力学精度。

---

## 八、产物与复现

| 产物 | 路径 |
|---|---|
| 本文 | `docs/oss_inventory/oss-inv-GPUSPH.md` |
| 探针脚本（含全部系数正则抽取与残差计算） | `runs/m8654/gpusph_probe.py` |
| 机器可读结果（系数/EOS/静态统计/残差） | `artifacts/oss-sph/gpusph/verify_kernels_eos.json` |
| 元数据（可见字段 + 配额受限说明） | `artifacts/oss-sph/gpusph/github_meta.json` |
| 图 | `artifacts/oss-sph/gpusph/fig1_kernels.png`、`fig2_eos.png` |

复现：`python3 runs/m8654/gpusph_probe.py /tmp/gpusph_src artifacts/oss-sph/gpusph/verify_kernels_eos.json artifacts/oss-sph/gpusph`（源码快照取自 `codeload.github.com/GPUSPH/gpusph/tar.gz/refs/heads/master`）。