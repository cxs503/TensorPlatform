# SWIFT 深度盘点报告（OSS-SPH 深盘波2 · 集团军）

> 任务 ID: m8703（重派，原 m8656 worker llm_error 中断）| 战役: osssph-w2 | 目标仓: **SWIFTSIM/SWIFT** | tier: 1
> 调查日期: 2026-11-09 | 方法: git 克隆取证（`api.github.com` 被安全策略拦截）+ 源码级文件阅读 + 程序化计数
> 克隆 HEAD: `e1a963fa682061498a5a59da1b3e1521e9eae724`（2026-11-03，Matthieu Schaller，master，`--depth 1`）
> 产物: `artifacts/oss-sph-w2/swift/{facts.json, build_figs.py, fig1..fig3}.png`

---

## 一、案例介绍：这是个什么仓

SWIFT 是 Durham/Leiden 系的**宇宙学与天体物理 SPH / 无网格流体求解器**，C 语言 + autotools，面向亿级粒子级大规模 MPI（COSMA、FLAMINGO 类星系形成模拟是它的主战场）。它对"船舶总体性能"没有直接语义，但它是**目前开源世界里 SPH 方案族最全、工程化最硬的一份代码**（14 个编译期可切换的水动力格式、59 条 `TESTS` 条目、45 个理想化算例），因此在 SPH 线的价值是"方法参照系 + 验证夹具库"，不是"直接拿来算船"。

| 字段 | 值 | 证据/说明 |
|---|---|---|
| full_name | SWIFTSIM/SWIFT | clone 成功 |
| 语言 | C（C11）+ autotools + 少量 Python 工具/绘图脚本 | `configure.ac`、`Makefile.am`、`tools/*.py` |
| license | **LGPL-3.0-or-later**（每文件头声明） + `COPYING`=GPLv3、`COPYING.LESSER`=LGPLv3 双文件 | `COPYING:1-2`、`COPYING.LESSER:2`、`src/hydro/SPHENIX/hydro.h:8-18` |
| 星数/forks/issues | **WITHHELD（insufficient）** | `api.github.com` 与 `github.com` HTML 均被安全策略拦截；git 协议可用但 `ls-remote/log` 不含星数。不凭记忆填数 |
| 最近提交 | 2026-11-03（HEAD，仍活跃维护；GitLab 流水线注释日期 2026-01） | `git log -1`；`.gitlab-ci.yml:1-8` |
| 默认分支 | master | `git rev-parse --abbrev-ref HEAD` |
| 源码规模 | 927 个 `.c/.h`，**315,424 行**（`src/`） | 程序化 `find|wc -l`，见 `facts.json:loc_src` |
| 目录结构 | `src/`（求解器+引擎+引力+宇宙学+亚网格）、`examples/`（24 个算例族目录）、`tests/`、`theory/`（LaTeX 方法推导）、`tools/`、`ci/` | clone 实查 |
| 测试布局 | `tests/` 103 个文件，`TESTS = 56` 条（`tests/Makefile.am:30-45`）；8 份 `tolerance_*.dat` 回归基线 | 程序化计数 |
| CI | **GitLab CI**（`.gitlab-ci.yml`：`gnu-build-cosma` 等 build+runtime 双 stage）+ `ci/swift-checks*.sh` 脚本化构建矩阵（非 MPI / MPI / 并行 HDF5 组合）；**无 GitHub Actions**（`.github/workflows` 不存在） | `.gitlab-ci.yml:1-40`、`ci/swift-checks.sh:1-30` |
| GPU | **零 GPU 代码**：`src/` 中 `CUDA/__global__/hipMalloc/OpenACC` 命中数 = **0**（927 个源文件全扫） | `facts.json:gpu_hits=0` |

### 与前序波次的关系（去重检索记录，m7662 教训）

- 本波已产 OpenSPH（m8653）、Sarracen、jax-sph 三仓盘点；SWIFT 与三者的**区别点**：OpenSPH=小体量 C++ 全方案库（MIT）、jax-sph=可微 JAX（MIT）、SWIFT=**大规模 MPI 工程 + 编译期方案矩阵 + 天体物理验证夹具**（LGPL-3.0-or-later）。三者互补不重复，本报告只登记 SWIFT 独有/更强的一面。

---

## 二、怎么做的：方法与流程

1. **实查优先**：`git clone --depth 1 https://github.com/SWIFTSIM/SWIFT.git`（57 MB，成功）；`api.github.com` 直连被安全策略拦截 → 星数标 WITHHELD，不猜。
2. **配置面即能力面**：`configure.ac` 的 `--with-hydro` / `--with-riemann-solver` / `--with-hydro-kernel` 三个开关是"编译期可切换格式"的权威枚举（`configure.ac:2328-2420`、`2626-2648`、`2486-2508`），比 README 宣传可靠。
3. **方案逐个读头文件**：`src/hydro/<Scheme>/hydro.h` 的 `@brief` 是作者写的方程集定义（含文献锚），逐条抽 `file:line`。
4. **程序化计数**：算例数/测试数/行数/LOC/GPU 命中全部由脚本 `artifacts/oss-sph-w2/swift/build_figs.py` 重算，零手抄（重跑即复现）。
5. **能力分级**：对每个方法给 REAL/MOCK/STUB/WITHHELD 判据——SWIFT 的 `Shadowswift`、`None` 两目录是空实现，明确按 STUB 记账，不因为"目录名字响亮"就算能力。
6. **验证面判定**：区分"有算例"、"有解析解生成脚本"、"CI 断言容差"三层；45 个算例中 20 个带 analytic/exact 生成脚本（44.4%），但**没有一个把误差阈值写进 `TESTS`**（见 §五 误差判定）。

---

## 三、源码级方法清单（逐条 file:line）

### 3.1 SPH / 无网格格式族（14 个编译期变体，`configure.ac:2329` 权威枚举）

| # | 方案（`--with-hydro=`） | 方程族 / 热变量 | 关键特性 | 证据 |
|---|---|---|---|---|
| 1 | `sphenix`（**默认**） | 密度-能量守恒式（热变量 u）+ SPHENIX 物理（Borrow 2020） | 人工扩散（导热/黏性各向同性项）、变系数 AV | `src/hydro/SPHENIX/hydro.h:28-32`；`hydro_parameters.h:53-58` |
| 2 | `anarchy-pu` | P-U 守恒式 + ANARCHY 物理（Cullen & Dehnen 2011 AV、Price 2008 热扩散） | 变系数 AV + 热扩散 | `src/hydro/AnarchyPU/hydro.h:28-33`；`hydro_part.h`(diffusion 字段) |
| 3 | `phantom` | 密度-能量 + PHANTOM 扩散物理（Price 2017） | 变系数 AV + 扩散 | `src/hydro/Phantom/hydro.h:28-31` |
| 4 | `gasoline` | 密度-能量 + Gasoline 物理（Wadsley+2017） | 扩散字段存在 | `src/hydro/Gasoline/hydro.h:28-30`；`hydro_part.h` |
| 5 | `pressure-entropy` | 压力-熵（热变量 S，熵在接触间断上被平滑）——Hopkins 2013 eq.(19)(21)(22) | 变系数（Balsara）黏性；明确写成"抑制虚假表面张力" | `src/hydro/PressureEntropy/hydro.h:26-33` |
| 6 | `pressure-energy` | 压力-能量（热变量 u） | 常系数 AV | `src/hydro/PressureEnergy/hydro.h:28-31` |
| 7 | `pressure-energy-monaghan` | 压力-能量 + Morris & Monaghan 变系数 AV | 变系数 AV | `src/hydro/PressureEnergyMorrisMonaghanAV/hydro.h:28-32` |
| 8 | `minimal` | 最小守恒 SPH（热变量 u，常系数 AV，无扩散） | 基线/教学用参照 | `src/hydro/Minimal/hydro.h:28-31` |
| 9 | `gadget2` | Gadget-2 SPH（Springel 2005） | 熵式；无扩散字段 | `src/hydro/Gadget2/hydro.h:28-31` |
| 10 | `gizmo-mfm` | **无网格有限质量（MFM）**：梯度 + 斜率限制器 + Riemann 通量 | 唯一支持 Riemann 求解器的族 | `src/hydro/Gizmo/hydro.h`、`MFM/hydro_flux.h`、`hydro_gradients_gizmo.h`、`hydro_slope_limiters_face.h` |
| 11 | `gizmo-mfv` | **无网格有限体积（MFV）**：FVPM 几何 + Riemann 通量 | 与 GEAR-RT 辐射输运耦合强制要求（`configure.ac:3323`） | `src/hydro/Gizmo/MFV/hydro_flux.h`、`src/fvpm_geometry.h` |
| 12 | `planetary` | minimal 方程 + **多材料**（每材料独立 EoS） | 行星撞击专用 | `src/hydro/Planetary/hydro.h:28-31`（注：`Planetary/hydro_parameters.h` 内无 `material` 字段，多材料语义在 `hydro.h` 与 EoS 表选择处） |
| 13 | `remix` | REMIX（Sandnes et al. 2025） | 独立 `hydro_kernels.h` + `hydro_visc_difn.h` 模块（黏性/扩散开关与核函数可插拔） | `src/hydro/REMIX/hydro.h:28`、`hydro_visc_difn.h:39` |
| 14 | `none` / `shadowswift` | **空实现** | 目录/文件存在但 `@brief Empty implementation` | `src/hydro/None/hydro.h:28`、`src/hydro/Shadowswift/hydro.h:28` |

**边界处理**：SWIFT 是**纯周期边界**宇宙学代码——`src/periodic.h` 只有 `box_wrap`/`box_wrap_multiple`（`:34`、`:45`），全仓 `src/` 与 `examples/` 内**没有任何反射/固壁/自由面边界实现**（`grep -rli "reflect.*boundar|free surface|dam break"` = 0 命中）。`tests/testPeriodicBC.c` 是周期边界专项测试。
→ 对 TensorPhysics 的船舶/自由面 SPH 而言，**边界层与自由面必须自研**，SWIFT 在此维度可迁移度 = 0（这是本报告最重要的"减法"结论）。

**时间步控制**：粒子级个体时间步（分 bin 的 `integertime` 体系，`src/timestep.h:48-92`）+ KDK（kick/drift：`src/kick.h:60`、`src/drift.h`）+ CFL 逐粒子判据（`dt_cfl = 2 γ h CFL a / ...`，例：`src/hydro/AnarchyPU/hydro.h:480-486`、`src/hydro/Gadget2/hydro.h:449-452`）+ 时间步 limiter（`src/timestep_limiter.h`，限制与邻居时间 bin 的错配 / 反馈事件后的强制降步）。

**多相/自由面**：无自由面、无表面张力、无弹塑性/屈服（`grep -rli "deviatoric|elastic|rheology|plastic"` = 0）。多"材料"仅存在于 `planetary` 方案（流体多 EoS，非多相界面模型）。

**Riemann 求解器**（仅 gizmo-* 可用，4 选）：`exact`（`src/riemann/riemann_exact.h`）、`trrs`（双稀疏波，`riemann_trrs.h`）、`hllc`（`riemann_hllc.h`），另有真空处理 `riemann_vacuum.h`、一致性检查 `riemann_checks.h`；开关 `configure.ac:2626-2648`。单测：`tests/testRiemannExact.c`、`testRiemannTRRS.c`、`testRiemannHLLC.c`（容差 `max_abs_error=1e-3f`，`testRiemannHLLC.c:37-39`）。

**核函数**：6 选（cubic/quartic/quintic spline、Wendland C2/C4/C6，`configure.ac:2486-2508`）；`src/kernel_hydro.h` 含多段多项式（`kernel_poly_ivals`）与 **AVX2/AVX-512 行选择**（`:56`、`:783-790`）——这是向量化，不是 GPU。

**并行策略**：
- MPI 域分解：`src/partition.c:27`（grid / vectorise / **METIS/ParMETIS** 三种）、`partition_repartition_metis.c`；
- 任务式调度：`src/scheduler.c`、`src/engine_maketasks.c`、`src/queue.c`（依赖 DAG + 线程池 `src/threadpool.c` / pthreads，`configure.ac:852-863`）；
- 并行 I/O：`src/parallel_io.c` + 并行 HDF5；`csds/` 连续数据流；
- 粒子级负载均衡：`space_regrid.c` + 个体时间步 bin ⇒ 非均匀负载。
- **GPU：无**（见 §一）。

**EoS**：`src/equation_of_state/` 下 `ideal_gas`、`isothermal`、`barotropic`、`continuous_barotropic`、`planetary`（`tillotson.h`、`hm80.h`、`sesame.h`；枚举 `equation_of_state.h:58-68`：Tillotson / Hubbard-MacFarlane 1980 / SESAME / ANEOS 表）。

---

## 四、验证面盘点（自带算例 / 框架 / 量化数据）

| 维度 | 事实（程序化计数） |
|---|---|
| 理想化算例 | `examples/HydroTests/` **45** 个算例目录：SodShock(1D/2D/3D/BCC)、ToroTest2(1/2/3D)、SedovBlast(1/2/3D)、Noh(1/2/3D)、Vacuum(1/2/3D/球)、SodShockSpherical(2/3D)、KelvinHelmholtz(2D)+**GrowthRate(2D/3D)**、Rayleigh-Taylor_2D、BlobTest(3D, Braspenning2022)、BalsaraKim、GreshoVortex(2/3D)、Diffusion_1D、DrivenTurbulence_3D、PerturbedBox(2/3D)、SineWavePotential(1/2/3D)、EvrardCollapse_3D、NFW_Hydrostatic、KeplerianRing、IdealizedAGNJet、InteractingBlastWaves_1D、SquareTest_2D、UniformBox(2/3D)、Gradients |
| 解析解生成脚本 | **20/45（44.4%）** 算例目录内含 analytic/exact/self-similar 引用；典型：`examples/HydroTests/SedovBlast_3D/plotSolution.py:23-30`（Sedov 解析解，参数 ρ0=1, E0=1, γ=5/3 写在脚本内）、`SodShock_1D/plotSolution.py:21-24`（精确 Riemann 解） |
| 测试框架 | autotools `make check` + `tests/Makefile.am` 的 56 条 `TESTS`；103 个测试源/脚本；采用**"正常 + 扰动"双基线**回归：`test27cells.sh` vs `test27cellsPerturbed.sh`（粒子排布随机扰动的结果比对，`tolerance_27_normal.dat` / `tolerance_27_perturbed.dat` 等 8 份基线）——抗噪鲁棒性验证设计值得抄 |
| 单元测试的量化容差 | 存在且严格：`tests/testKernelAccuracy.c:43-49`（W 绝对 1e-6、积分归一 1e-5、双精度 1e-13）、`tests/testRiemannHLLC.c:37-39`（绝对/相对 1e-3） |
| 端到端量化验收 | **缺失（机器门角度）**：45 个算例的解析解对比全部是**绘图脚本**（手动 `run.sh` → `plotSolution.py` 出图目视），`TESTS` 里**没有任何一条断言 L1/L2 误差阈值**。即"有验证素材，无验证门" |
| 方法论文档 | `theory/SPH/Flavours/sph_flavours.tex`（逐方案方程推导：Minimal/Gadget2/Pressure-Entropy/Pressure-Energy/变系数 AV/加权压强有效性，`:11/:162/:312/:436/:593`）+ `theory/SPH/Flavours/plotSoundspeed.py` + `sedov_soundspeed_ratio_data.npz`(1024×1024 float32，Sedov 声速比场数据) |
| 图数据 | `theory/SPH/Flavours/sedov_soundspeed_ratio_data.npz`；`theory/SPH/Kernels/kernels.py`（核函数定义可执行化） |

**判据术语落地**：SWIFT 自身"SPH 能力"证据等级 = **REAL**（有真算法、有文献锚、有单测）；`Shadowswift`/`None` = **STUB**；星数/社区指标 = **WITHHELD**；自由面/多相/GPU/可微 = **NOT_SUPPORTED**。

---

## 五、结果图

| 图 | 文件 | 内容 |
|---|---|---|
| 图1 | `artifacts/oss-sph-w2/swift/fig1_scheme_matrix.png` | 14 个编译期方案 × 能力旗标（Riemann 通量 / 变系数 AV / 扩散 / 多材料 / 空壳），每格证据见 §三表 |
| 图2 | `artifacts/oss-sph-w2/swift/fig2_validation_coverage.png` | 左：45 个算例按基准类别分组，深色=带解析解脚本（20/45）；右：机器门化程度（56 条 TESTS、103 测试文件、8 份容差基线、**0 条断言解析解误差**；该 0 值为审读 `tests/Makefile.am` 全 56 条后的判定，非 grep 可算，故图上以不同颜色单列） |
| 图3 | `artifacts/oss-sph-w2/swift/fig3_transfer_roadmap.png` | 5 条迁移方案的工作量（人日，估计）vs GAP 锚覆盖数 vs 可复用源码规模 |

（三图均由 `artifacts/oss-sph-w2/swift/build_figs.py` 从本地克隆重算数据生成，重跑即复现。）

---

## 六、对 TensorPhysics SPH 线的可迁移方案（≥3 条，含 license 判定）

**License 判定的统一前提**：SWIFT 源文件头为 **LGPL-3.0-or-later**（`src/hydro/SPHENIX/hydro.h:8-18`；`COPYING.LESSER`=LGPLv3，`COPYING`=GPLv3）。
- **直接搬运源码**到 TensorPhysics（若 TensorPhysics 为宽松许可仓）会给该组件引入 LGPL-3.0-or-later 传染性义务（须保留许可、提供可替换链接/源码）→ 本报告**不做源码级搬运推荐**；
- **推荐做法**：①`reference` 级=只读文献/公式/数据结构重写（无著作权复制）；②`adapt` 级=在 TensorPhysics 侧按方程重实现，并保留 SWIFT 文献引用（学术引用 ≠ 代码许可义务）；③若确需直引，则该子模块显式标记 LGPL-3.0-or-later 并隔离编译单元。

| ID | 分级 | 内容 | 目标 GAP 锚 | License 判定 |
|---|---|---|---|---|
| SPHSOL-swift-01 | **adapt** | **SPHENIX 方程集**（密度-能量守恒式 + 各向同性扩散导热/黏性，Borrow 2020）：`src/hydro/SPHENIX/hydro.h:28-32` + `hydro_parameters.h:53-58`。以"方程组+参数语义"重写进 TensorSPH 能量方程项，保留 AV 极限（β=3）与扩散开关 | `GAP-SPH-DELTA-SPH`（同为"加性耗散稳定化"家族，SWIFT 用各向同性扩散而非 Marrone δ 项，可做对照实验） | 公式/物理可自由重实现；**不得整段复制** LGPL 源码 |
| SPHSOL-swift-02 | **adapt** | **REMIX 的可插拔黏性/扩散模块**（Sandnes+2025）：`src/hydro/REMIX/hydro_visc_difn.h:39`（`hydro_prepare_gradient_extra_visc_difn`）+ `hydro_kernels.h`。作为 TensorSPH"黏性/扩散开关"组件的接口设计参照（prepare/apply 两段式） | `GAP-SPH-MULTIPHASE`（接触间断耗散控制是多相混合的核心） | 接口模式=思想，可自由实现；头文件实现体不得复制 |
| SPHSOL-swift-03 | **reference** | **Riemann 求解器三件套 + 一致性检查**（HLLC/TRRS/exact + `riemann_checks.h`）：`src/riemann/*.h`，单测容差 1e-3（`tests/testRiemannHLLC.c:37-39`）。为 TensorSPH 的 Riemann-SPH 通量提供**参考解与自检用例**（不是移植代码，而是拿它的 A/B 判断标准） | `GAP-SPH-WCSPH-VALIDATION` | reference-only（读取结论与用例，不复制实现） |
| SPHSOL-swift-04 | **reference** | **解析解基准夹具库**：Sedov/Noh/Sod/Toro/Vacuum/Gresho/RT/KH-growth 共 20 个带解析解脚本的算例（`examples/HydroTests/*/plotSolution.py`，如 `SedovBlast_3D/plotSolution.py:23-30`）+ 方法推导 `theory/SPH/Flavours/sph_flavours.tex`。价值=**给 TensorSPH 建"解析解对比→误差阈值断言"的验收面**（SWIFT 自己缺最后一环，我们补上即成差异化优势） | `GAP-SPH-ASTRO-SEDOV`（Sedov blast 三档 + 声速比数据 `sedov_soundspeed_ratio_data.npz`）、兼覆盖 `GAP-SPH-WCSPH-VALIDATION` | reference：解析解公式属公共知识；脚本为 LGPL，重写（20 行级）比引用更省 |
| SPHSOL-swift-05 | **reference** | **强扩展性测量方法论 + "正常/扰动"双基线回归设计**：`tools/plot_scaling_results*.py`（3 个脚本）、`tests/test27cells{,.Perturbed}.sh` + 8 份 `tolerance_*.dat`。给 TensorSPH 的扩展性实验（CPU 多线程/MPI）与 GPU 化前的数值稳定性门提供**测量与门禁模板** | `GAP-SPH-GPU-SCALING`（**仅方法论**：SWIFT 零 GPU 代码，不能提供 GPU 实现参照，此为诚实降级声明） | reference：脚本为 LGPL，按方法论重写 |

### 附：不能迁移/无证据项（如实登记）

| 需求 | SWIFT 现状 | 判定 |
|---|---|---|
| 自由面 / 溃坝 / 静水压力边界 | 纯周期边界，无固壁/自由面（`src/periodic.h` 仅 `box_wrap`） | **NOT_SUPPORTED** → `GAP-SPH-WCSPH-VALIDATION` 的自由面基准须另找参照（jax-sph / Sarracen） |
| 多相界面（表面张力、相变） | 无；`planetary` 仅多 EoS 材料 | **NOT_SUPPORTED** → `GAP-SPH-MULTIPHASE` 只能借"接触间断耗散控制"思想 |
| FSI / 弹塑性 | 无弹性/屈服/流变（grep 0 命中） | **NOT_SUPPORTED** → `GAP-SPH-FSI-BENCHMARK` 不适用 |
| 可微 SPH | 无 AD，纯 C 手写梯度 | **NOT_SUPPORTED** → `GAP-SPH-DIFFERENTIABLE` 由 jax-sph 线承担，SWIFT 不参与 |
| ISPH/PCISPH 投影法 | 全部为显式 WCSPH 族 | **NOT_SUPPORTED** → `GAP-SPH-ISPH-CONVERGENCE` 不从本仓取 |
| GPU 加速 | 0 命中（AVX2/AVX-512 仅为 CPU 向量化） | **NOT_SUPPORTED**（仅方法论参照，见 SPHSOL-swift-05） |

---

## 七、注意点（踩过的坑 / 局限 / 适用范围）

1. **陷阱：关键词当能力**。首轮用"文件名+关键词"自动打能力标签，14 个方案全部误判为"有扩散、有变系数 AV"——原因是 `hydro_parameters.h` 里对 `hydro_props_default_diffusion_alpha` 的**注释在所有方案共享**。修正做法：只认 `hydro_part.h` 的**实际字段**与 `hydro.h` 的 `@brief`，并逐条记 `file:line`。（该草稿矩阵已废弃，未进报告。）
2. **`--with-hydro` 枚举 ≠ 实现质量**：14 项里 `none`/`shadowswift` 是空壳（STUB），若按"方案数量"排名会高估 SWIFT。真实可用方案 = 12。
3. **API 元数据不可达**：`api.github.com` 与 `github.com` HTML 均被安全策略拦截（与 TCLB/CudaPhaseField/Sarracen 各单一致），星数一律 WITHHELD；克隆 `--deepen 60` 在 120 s 超时被收割，故"最近提交历史序列"只有 HEAD 一条 → 活跃度只能给"HEAD 2026-11-03 新于 clone 日"这一条硬证据。
4. **验证素材 ≠ 验证门**：45 个算例、20 个解析解脚本，但 `TESTS` 内 0 条解析解误差断言。任何"SWIFT 验证完备"的说法都是把目视出图当验收，属 MOCK 级证据。
5. **周期边界是硬约束**：SWIFT 的邻居搜索、时间步、任务调度都建立在盒子周期性上；把方案方程搬到非周期/有壁场景时，"盒子周期 + 个体时间步"的隐式假设要逐条拆掉，工作量集中在边界与时间步，不在方程本身。
6. **LGPL 边界**：公式与算法不受著作权保护，实现受；因此本报告的 adapt 级全部要求"按方程重写"，不得逐段翻译源码（含变量名/注释），否则在宽松许可仓里埋许可风险。
7. **适用面**：SWIFT 面向高马赫数、强激波、自引力、宇宙学膨胀（`cosmology.h` 的 a(t) 因子进入 CFL 与水动力方程）——船舶 SPH（低马赫、自由面、重力波）需要把 comoving 变量体系整体去掉，别照抄。

---

## 八、误差判定（关键量 vs 基准，逐项显式回答"是否 <3%"）

本单是**仓库审计类任务**，不产生新的数值解，因此不存在"我的计算结果 vs 解析解"的误差；误差判定改为审计结论的**可核验性**判定。逐项如实列出：

| 关键量 | 基准 | 本单取值 | 误差/一致性 | 是否 <3% |
|---|---|---|---|---|
| 方案数（编译期 `--with-hydro` 枚举） | `configure.ac:2329` 帮助串 | 14（含 none/shadowswift 空壳；可用 12） | 逐项列举一致，脚本与手工双计 | ✅ 一致（0 偏差） |
| 源码规模 | `find src -name '*.c' -o -name '*.h' \| wc -l` | 927 文件 / 315,424 行 | 脚本可重跑复现 | ✅ 一致 |
| 算例数 | `ls examples/HydroTests` 目录数 | 45 | 脚本可重跑复现 | ✅ 一致 |
| 带解析解算例占比 | `grep -rli analytic\|exact solution\|self-similar` 逐目录 | 20/45 = 44.4% | 计数口径写在脚本内，可复现 | ✅ 一致（口径已声明） |
| TESTS 条目数 | `tests/Makefile.am:30-45` 解析 | 56 | sed 解析 + 人工复核 | ✅ 一致 |
| 容差基线文件 | `ls tests/tolerance_*.dat` | 8 | 直接计数 | ✅ 一致 |
| GPU 代码量 | `grep -rl 'CUDA\|__global__\|hipMalloc\|OpenACC' src` | 0 / 927 文件 | 全仓扫描 | ✅ 一致（判定 NOT_SUPPORTED 有据） |
| 星数/forks | GitHub API | **WITHHELD** | 出口被安全策略拦截，未取得 | ❌ 无基准（声明原因：api.github.com 不可达，禁凭记忆填数） |
| 迁移方案工作量的"人日"估计 | 无 | 3–8 人日 | **主观估计，非实测** | ❌ 无基准（图3 已标注 estimate，只作排序用） |

**总判**：可核验的计数类结论（前 7 项）与源码逐字一致，偏差 0；两处无基准项（社区指标、工作量估计）已如实声明，不作精度主张。

---

## 九、机器块

[OSS-INV]{"campaign":"osssph-w2","repo":"SWIFTSIM/SWIFT","tier":1,"methods":["SPHENIX(density-energy+Borrow2020 diffusion)","AnarchyPU(P-U+CullenDehnen2011AV+Price2008 diffusion)","Phantom(density-energy+Price2017 diffusion)","PressureEntropy(Hopkins2013 eq19/21/22)","PressureEnergy","PressureEnergyMorrisMonaghanAV(MorrisMonaghan1997)","Gasoline(Wadsley2017)","Gadget2(Springel2005 entropy)","Minimal","Planetary(multi-material EoS)","REMIX(Sandnes2025)","GizmoMFM","GizmoMFV","None(stub)","Shadowswift(stub)"],"boundary":"periodic-only (src/periodic.h box_wrap/box_wrap_multiple); no reflective wall, no free surface, no surface tension","gpu":"NONE (0 CUDA/HIP/OpenACC hits in 927 src files; CPU SIMD AVX2/AVX-512 in src/kernel_hydro.h:783-790; MPI+METIS src/partition.c:27 + pthread task scheduler src/scheduler.c)","validation":"45 HydroTests setups (Sod/Toro/Sedov/Noh/Vacuum/Gresho/RT/KH/GrowthRate/Blob/BalsaraKim/Diffusion/DrivenTurbulence...), 20/45 with analytic-solution scripts; 56 TESTS entries + 103 test files + 8 tolerance baselines (normal/perturbed double-baseline design); 0 CI-asserted analytic error thresholds; theory/SPH/Flavours/sph_flavours.tex + sedov_soundspeed_ratio_data.npz","license":"LGPL-3.0-or-later (per-file headers; COPYING=GPLv3 + COPYING.LESSER=LGPLv3)","transferable":["adapt:SPHENIX equation set(no code copy)→GAP-SPH-DELTA-SPH","adapt:REMIX visc/diffn interface pattern→GAP-SPH-MULTIPHASE","reference:Riemann HLLC/TRRS/exact + riemann_checks→GAP-SPH-WCSPH-VALIDATION","reference:analytic benchmark fixtures Sedov/Noh/Sod/Gresho/RT + soundspeed npz→GAP-SPH-ASTRO-SEDOV","reference:scaling methodology + normal/perturbed dual-baseline regression→GAP-SPH-GPU-SCALING"]}[/OSS-INV]

[OSS-SOL]{"repo":"SWIFTSIM/SWIFT","id":"SPHSOL-swift-01","kind":"adapt","summary":"SPHENIX conservative density-energy equation set with isotropic diffusion (Borrow 2020) at src/hydro/SPHENIX/hydro.h:28-32 — re-implement the equation terms (not the LGPL code) in TensorSPH as an additively-dissipative stabilisation alternative to Marrone delta-SPH, with A/B experiment on both.","gap":"GAP-SPH-DELTA-SPH"}[/OSS-SOL]
[OSS-SOL]{"repo":"SWIFTSIM/SWIFT","id":"SPHSOL-swift-02","kind":"adapt","summary":"REMIX (Sandnes+2025) plugin-style viscosity/diffusion module interface (src/hydro/REMIX/hydro_visc_difn.h:39 + hydro_kernels.h) — adopt the prepare/apply two-stage interface for TensorSPH contact-discontinuity dissipation control; reimplement body under own licence.","gap":"GAP-SPH-MULTIPHASE"}[/OSS-SOL]
[OSS-SOL]{"repo":"SWIFTSIM/SWIFT","id":"SPHSOL-swift-03","kind":"reference","summary":"Riemann solver triplet HLLC/TRRS/exact plus riemann_checks.h (src/riemann/) with 1e-3 unit-test tolerances (tests/testRiemannHLLC.c:37-39) — use as reference solutions and self-check cases for TensorSPH Riemann-SPH fluxes; reference-only, no code copy.","gap":"GAP-SPH-WCSPH-VALIDATION"}[/OSS-SOL]
[OSS-SOL]{"repo":"SWIFTSIM/SWIFT","id":"SPHSOL-swift-04","kind":"reference","summary":"Analytic-solution benchmark fixture library: 20/45 HydroTests setups ship analytic/exact generators (e.g. examples/HydroTests/SedovBlast_3D/plotSolution.py:23-30) plus theory/SPH/Flavours derivation and Sedov soundspeed npz — build TensorSPH's asserted-error acceptance gate on top of it (SWIFT itself lacks the assertion step).","gap":"GAP-SPH-ASTRO-SEDOV"}[/OSS-SOL]
[OSS-SOL]{"repo":"SWIFTSIM/SWIFT","id":"SPHSOL-swift-05","kind":"reference","summary":"Strong-scaling measurement methodology (tools/plot_scaling_results*.py) plus normal/perturbed dual-baseline regression design (tests/test27cells{,.Perturbed}.sh + 8 tolerance_*.dat) — reuse as measurement and numerical-stability gate template before TensorSPH GPU port; methodology only, SWIFT has zero GPU code.","gap":"GAP-SPH-GPU-SCALING"}[/OSS-SOL]

---

## 十、下一步（最小可证单元）

1. `SPHSOL-swift-04` 优先：在 TensorPhysics 侧落"Sedov 1D/2D 解析解 → L1 误差断言 <3%"的最小门（4 人日量级，复用 SWIFT 公式但自写脚本），这直接把本仓最大的验证缺口（有素材无门）变成我们的优势。
2. `SPHSOL-swift-01` 次之：SPHENIX 扩散项作为 δ-SPH 的对照实验（同算例双方案误差表），产出 `GAP-SPH-DELTA-SPH` 的 A/B 证据。
3. 边界/自由面能力必须外源（jax-sph / Sarracen），SWIFT 线不投入。