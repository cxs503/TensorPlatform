# OSS-SPH 深盘波2 · 单项目深盘：`Shamrock-code/Shamrock`

> 战役 `osssph-w2` · mission 8658 · tier 1 · 目标仓 `Shamrock-code/Shamrock`（GPU SPH 框架，新架构）
> 盘点对象：`https://github.com/Shamrock-code/Shamrock` @ `b02375271d30f90d001b5b642bdf1b3398aa41f3`
> （`main` tip，`git log -1` 直读：`[SPH][GSPH] merge gen_ghost_handler into build_ghost_cache (#2717)`，2026-11-09T14:47:15Z）
> 结论等级：**源码确认（source-confirmed）** 为主体；每条结论带 `文件:行` 指针。所有数字由
> `artifacts/oss_sph_w2/make_metrics.py` 从实际克隆/仓内文件程序化提取（零手抄），落盘 `shamrock_metrics.json`。

---

## 零、取证与复现（provenance）

| 项 | 值 | 取证方式 |
|---|---|---|
| 仓 / 分支 / commit | `Shamrock-code/Shamrock` / `main` / `b02375271d30f90d001b5b642bdf1b3398aa41f3` | `git clone --depth 1` + `git rev-parse HEAD`（`artifacts/oss_sph_w2/make_metrics.py` 内执行） |
| 快照 | 26 MB，1802 blob；`src` 1274 文件、`examples` 151、`doc` 143 | `GET /repos/.../git/trees/main?recursive=1`（`truncated=false`） |
| 元数据 | stars 69 / forks 38 / open issues 248 / created 2022-04-05 / `pushed_at` 2026-11-09T11:22:26Z / size 22998 KB / archived=false | `GET /repos/Shamrock-code/Shamrock`（认证通道：本地 credential helper 取 token，**不落盘不打印**） |
| 枚举通道 | 未认证 `GET /languages` 遇共享出口 IP 限流 → 语言构成改由本机 `src/` 扩展名统计（见 §一） | `runs` 记录于本文件 §一 |
| 源码引用方式 | 全部 `文件:行` 由 `artifacts/oss_sph_w2/anchors.sh` 在克隆体内 `grep -n` 程序化生成（`anchors.txt`），非人工记数 | 复现：`bash artifacts/oss_sph_w2/anchors.sh` |
| 复现命令 | `python3 artifacts/oss_sph_w2/make_metrics.py` → `shamrock_metrics.json` + `fig_shamrock_evidence.png` | 已执行，退出码 0 |

**版本漂移风险（重要）**：仓 2026-11-08~11-09 两天内合入 ≥8 个 PR（含 solvergraph 大重构 #2712/#2716/#2717），
本报告一切结论**只对 commit `b023752` 成立**；引用时必须锁 SHA。

---

## 一、概况与定位

- **语言/构建**：C++20 为主（`README.md:8`），Python 绑定（pybind11，`src/shampylib`、`src/shammodels/sph/src/pySPHModel.cpp`，69 个 `.def(` 导出），CMake + 自研 `cmake/SYCLAdaptACppCmake.cmake`。
  代码量（本机统计）：`shammodels` 81,102 行、`tests` 36,384、`shamalgs` 18,254、`shamrock` 18,031、`shammath` 11,980、`shamtree` 11,125、`shambackends` 9,570、`shamphys` 7,451 …（全表见 `shamrock_metrics.json:loc_by_src_module`）。
- **许可证：CeCILL-2.1**（GitHub API `license.spdx_id=CECILL-2.1`；`LICENSE:2` 「CONTRAT DE LICENCE DE LOGICIEL LIBRE CeCILL」+ `LICENSE.en` 英文对照）。
  → **强 copyleft（法式 GPL 等价物）**，对 TensorPhysics 的可迁移性判定见 §四，结论：**代码级复制不合规，仅 reference-only**。
- **架构**：模块化 `src/<domain>`；求解器已迁移到**自研 solvergraph DAG**（节点=算子，边=场/标量），时间步由 `Solver::evolve_once()` 驱动（`src/shammodels/sph/src/Solver.cpp:2821` 起）。
  三套并行求解器家族：`sph`（粒子 SPH）、`gsph`（Godunov-SPH，Riemann 解法器）、`ramses`（AMR 网格 Godunov）、`zeus`（结构化网格）。
- **定位一句话**：**面向天体物理的 GPU 粒子 SPH 框架**（含尘埃双流体、MHD、自引力、sink 粒子、AMR），
  以「与 Phantom 码逐位一致」为回归目标；**不是**通用 CFD 工具，**没有**固体壁/水槽/自由液面工程能力（见 §二.2、§五）。
- **上游论文**：`CITATION.bib` = David-Cléris, Laibe, Lapeyre 2025, MNRAS 539, 1（"The SHAMROCK code: I — SPH on GPUs"），arXiv:2503.09713。

---

## 二、源码级方法清单（逐条 `文件:行`）

### 2.1 SPH 变体谱系

| 变体 | 本仓有无 | 证据 |
|---|---|---|
| **WCSPH（密度基、人工黏性）** | **有，且是唯一主线** | `AVConfig.hpp:33/49/78/113/147`（5 种 AV 策略）；`q_ab.hpp:37`（`q_av` 实现 Phantom 2018 eq.40） |
| ISPH（不可压半隐式） | **无** | `grep -rniw isph src/` = 0 命中 |
| PCISPH / DFSPH / IISPH | **无** | 同 0 命中 |
| δ-SPH（密度扩散项） | **无** | `grep -rniE "delta[-_ ]?sph" src/ examples/` = 0 命中 |
| PBD / position-based | **无** | `grep -rniw pbd` = 0 命中 |
| **GSPH（Godunov-SPH，Riemann）** | **有（独立 model）** | `src/shammodels/gsph/`（8 个 cpp）；`src/shammath/include/shammath/riemann_hllc.hpp`、`riemann_rusanov.hpp` |
| **尘埃双流体（dusty SPH，TVA）** | **有** | `SolverConfig.hpp:160-190`（`MonofluidTVA` 含 `C_1_fluid`/`C_drift`/`clamp_dust_frac`）；`modules/NodeUpdateDerivsMonofluidTVA.hpp`、`...VaryingAlphaAVDustTVA.hpp`、`NodeEvolveDustCOALASourceTerm.hpp` |
| **理想/非理想 MHD（约束超抛物）** | 有（非理想标 EXPERIMENTAL+警告横幅） | `config/MHDConfig.hpp:29`；`SolverConfig.hpp` `set_non_ideal_mhd()` 内打印 "NOT. FULLY. TESTED." + `experimental_feature_check` |
| **自引力** | 有 4 档 | `modules/self_gravity/{SGDirectPlummer,SGFMMPlummer,SGMMPlummer,SGSFMMPlummer}.hpp` |

**核函数**：`shammath/sphkernels.hpp` 定义 20+ 个（M4–M10、C2/C4/C6、截断高斯、M4DoubleHump/Shift 系列，见 `:2425/:2488` 起的 `@brief` 区块）；
**模型实际实例化 6 个**：`M4, M6, M8, C2, C4, C6`（`pySPHModel.cpp:2128-2143` 字符串分派 + `Solver.cpp:4234-4239` 显式实例化，仅 `f64_3`）。
`solver_instantiations` 程序化提取结果 = `[[f64_3,M4],[f64_3,M6],[f64_3,M8],[f64_3,C2],[f64_3,C4],[f64_3,C6]]` → **只有 3D，无 2D 实例化**。

**人工黏性策略（5 种）**：`none` / `constant(α_u,α_AV,β_AV)` / `varying_mm97`（Morris & Monaghan 1997）/ `varying_cd10`（Cullen & Dehnen 2010）/ `constant_disc`（仅盘面）。
其中 `varying_cd10` 的 α 触发/衰减是**唯一可迁移到 δ-SPH 类格式的"开关"思想来源**（见 §四 SPHSOL-04）。

**光滑长度求解**：3 套迭代器并存 —— `IterateSmoothingLengthDensity`（Newton）/ `...BisectingNR`（二分+Newton）/ `...NeighLim`（邻数上限），
驱动 `LoopSmoothingLengthIter`；密度关系 `density.hpp:20-40`（`rho_h`、`h_rho`、`newton_iterate_new_h`）。
这与 ISPH 的"压力 Poisson 迭代"是**不同**的收敛对象（h ↔ ρ 自洽），不要混淆 —— 我们若做 GAP-SPH-ISPH-CONVERGENCE 只能借其**迭代收敛判据工程实现**，物理方程不同。

### 2.2 边界处理（**最大的能力缺口**）

仅 3 种，且**全是"域盒"级**，**没有任何固体壁/反射/ghost-fluid 壁面**：

| 类型 | 语义 | 指针 |
|---|---|---|
| `Free` | 粒子出盒 → 盒扩张（`expand_tolerance=1.2`） | `config/BCConfig.hpp:52` |
| `Periodic` | 周期 | `config/BCConfig.hpp:65` |
| `ShearingPeriodic` | 剪切周期（Stone 2010, ApJS 189,142） | `config/BCConfig.hpp:73` |

幽灵粒子层 `BasicSPHGhosts`（`include/shammodels/sph/BasicSPHGhosts.hpp:40`，478 行）只做 **MPI 跨 patch 通信拷贝**，
不是壁面镜像/虚拟流体。交叉验证：`grep -rliI "reflective|closed_boundary|no-slip" src/shammodels src/shamrock` = 0 命中
（唯一命中在 `ramses` 网格模型，与 SPH 无关）。
→ **结论：船海 dam-break / 静水槽 / 结构壁面在 SPH 侧完全不可用**（`dam break`、`poiseuille` 在 `examples/ src/ doc/` 全树 0 命中）。

### 2.3 时间步控制（本仓最强工程资产之一）

- **逐粒子 dt**：可选字段 `dt_part`（`Solver.cpp:3770-3800`，`reset_dt_part_field` / `save_dt_min_to_dt_part`）。
- **CFL 判据 7 类**（`shamrock_metrics.json:cfl_criteria_modules`）：
  `ComputeCFLCourant`（`dt_c = C_cour·h_a/vsig_a`，`modules/ComputeCFLCourant.hpp:57`）、
  `ComputeCFLForce`（`dt_f = C_force·sqrt(h_a/|a_a|)`，`modules/ComputeCFLForce.hpp:59`）、
  `ComputeCFLDustDrift`、`ComputeCFLDust1Fluid`、`ComputeCFLSinkSink`、`ComputeCFLDivBCleaning`、`ComputeCFLNIMHD`。
- **全局归约**：`next_cfl = allreduce_min(rank_dt)`（`Solver.cpp:4057`）→ MPI 全局同步步长。
- **步长斜坡（防步长抖动）**：`cfl_multiplier` 向 1 指数逼近，`stiffness` 可调（`Solver.cpp:4214-4219`）。
- **积分器：leapfrog predictor–corrector（kick–drift–kick）**，`Solver.cpp:532` 注释 `// leapfrog predictor`，graph 节点 `leapfrog_predictor`（`:681`）；sink 粒子另有独立 predictor/corrector（`:981/:1037`）。
- CFL 明细可打印（`show_cfl_detail` 表格，`Solver.cpp:4044-4056`）——**这是可复用的可观测性设计**（失效步长可归因到具体判据）。

### 2.4 多相 / 自由面

- **"多相"= 气体+尘埃双流体**（TVA，`Tensor-Variable` 型能量式；尘埃为无压流体），停时模型 3 档：
  `SetDustStoppingTimeConstant` / `...Epstein` / COALA 源项（`modules/NodeEvolveDustCOALASourceTerm.hpp`）。
- **自由面**：密度基 SPH 天然处理真空边界（`rho→0`），但**无表面张力**（`grep -rniI "surface.tension"` = 0）、**无气-液两相界面**、**无壁面接触角**。
- → 对我们的气-液 dam-break/晃荡无直接迁移价值；对**泥沙/气泡固相跟随**（颗粒相动量耦合）有参考价值（见 SPHSOL-05）。

### 2.5 GPU / MPI 并行策略

- **单源多目标 SYCL**（AdaptiveCpp）：`README.md:8`，`cmake/SYCLAdaptACppCmake.cmake:18-62`（`ACPP_TARGETS=omp;cuda:sm_52`…），可在 CUDA/ROCm/OpenMP/OpenCL 间切换；`src/shambackends/` 封装 Device/Queue/USM/事件/kernel_call（含 `kernel_call_distrib.hpp` 分发式 kernel 调用）。
- **MPI 域分解**：`src/shamcomm/`（1,744 行）+ patch 调度器动态分裂/合并 + 负载均衡（`modules/ComputeLoadBalanceValue.hpp`，`evolve_once` 内两次 `scheduler_step` 前后调 `update_load_balancing()`，`Solver.cpp:2848-2857`）。
- **邻域搜索**：Morton BVH `shamtree::CompressedLeafBVH`（`SolverConfig.hpp:578`）+ `NeighbourCache`（`modules/NeighbourCache.hpp:28`），策略 `SingleStage`/`TwoStage`（`pySPHModel.cpp:97-99`）——单阶段=一步搜索，两阶段=先粗后精（缓存换带宽）。
- **扩展性证据（仓内文档，非我方实测）**：`doc/mkdocs/docs/models/sph/scalling.md` 给出 Sedov 弱扩展 CPU/GPU 曲线与跑法（`mpirun -npernode 2`，节点 1→60，GPU 2/节点）。→ **我方未复测，标注为 upstream-claimed**。

---

## 三、验证面盘点

| 维度 | 事实 | 指针 |
|---|---|---|
| 测试框架 | 自研 `shamtest`（`NEW_TEST(type,name,node_cnt)` 宏，`src/shamtest/shamtest.hpp:142`）；C++ 测试 187 个 cpp，其中 SPH 域 8 个 cpp（`src/tests/shammodels/sph/**`，含 `comp_phantom_sedov.cpp`、`hfactTest.cpp`、`phantom_boundaries_test.cpp`、`phantom_units_test.cpp` 及其 `modules/` 子目录），SPH 域 `NEW_TEST` 出现 14 处 | `src/tests/` |
| CI（物理算例矩阵） | `.github/workflows/shamrock-acpp-phys-test.yml`：**38 个 matrix 条目**（程序化计数），worldsize 1 与 2 各跑一遍 → 兼测 MPI 同步；包含 `sod_tube_sph.py`、`comp_phantom_sedov_1patch.py`、`comp_phantom_sedov_8patch.py`、`regression_sph_disc.py` | `:79-190` |
| 解析基准 | **Sod 管 vs 解析 Riemann 解**：`compute_L2_dist()`（`examples/tests_ci/sod_tube_sph.py:105`），记录值 `expect_rho=1.615e-4`（`:122`），CI 断言 `tol=1e-11`（**相对**，`:128`）→ 实为**回归门**，非精度门 | 同左 |
| 码间基准 | **Sedov 爆炸 vs Phantom 参考 dump**：`expected_L2 = {...}`（`comp_phantom_sedov_1patch.py:134`）+ `tols`（`:160`），step 1/10/100 的 ρ,u,v_r,α 的 L2 差；容差 1e-16~1e-20 → **要求逐步逐位一致** | 同左 |
| 解析解工具 | `shamphys/SedovTaylor.hpp:34`（L1 表驱动自相似解）、`shamphys/SodTube`（Python 侧 `shamrock.phys.SodTube`）——**有解析解实现** | `src/shamphys` |
| **缺口** | ①Sedov **vs 解析解**的 SPH 脚本在 `examples/TO_MIGRATE/sph/sph_sedov.py:116`（未迁移 → 不在 CI）→ 该量化证据 **WITHHELD**；②无 dam-break/Poiseuille 类基准（无壁面，做不了）；③无收敛阶研究进 CI（`run_h_convergence.py` 在 `examples/sph/` 易导航但不在 `tests_ci`）；④Taylor-Green 有（`examples/sph/run_sph_taylor_green_vortex.py`）但为天体盘语境 | `shamrock_metrics.json:to_migrate_sph` |

**验证成熟度判定**：*回归级成熟、验证级薄弱*。CI 能保证"改不坏"，但不能证明"算得准"；
唯一对物理真值的量化证据是 Sod 管（L2 相对 1.6e-4 量级，见 §六）。

---

## 四、对 TensorPhysics SPH 线的可迁移方案（≥3，含 license 判定）

### 4.0 许可证合规判定（显式）

- Shamrock = **CeCILL-2.1**：强 copyleft，衍生作品分发须沿用 CeCILL（或经 CeCILL 认可的兼容许可）。
  GitHub 元数据 `spdx_id=CECILL-2.1`，正文 `LICENSE:2`。**没有任何 permissive（MIT/BSD/Apache）双授权声明**。
- 因此：**任何把 Shamrock 源码（.hpp/.cpp）搬进 TensorPhysics 的行为 = 许可污染**，除非 TensorPhysics 自身即采用 CeCILL/GPL 兼容许可（当前无此设定）。
- **允许的路径**：①读算法/论文（MNRAS staf444，算法本身不受版权保护）后**净室重实现**；②只迁移**实验协议/方法论**（不复制代码）；③把 Shamrock 当**外部参考实现**跑黑盒对照。
- **数据风险**：Sedov 参考 dump 源自 **Phantom（GPL-3）**，不得 vendor 进仓；只可"取数比对后删"。
- 分级定义：`adapt` = 可重实现公式/接口约定（净室，不复制代码）；`fit` = 直接可用的协议/参数；`reference` = 仅供对照与设计参考。

### 4.1 方案清单

**SPHSOL-shamrock-01 — 双轨验证门（回归门 + 真值门）· `reference` · GAP-SPH-WCSPH-VALIDATION**
Shamrock 的 CI 同时挂两类门：①对**记录值**逐位回归（Sod `tol=1e-11` 相对，`sod_tube_sph.py:128`）保证"改不坏"；
②对**解析解/外部码**的 L2 差（`compute_L2_dist`，`:105`）保证"还像样"。
**可迁移**：TensorPhysics 的 WCSPH 验证面按此二分建设 —— 回归门锁 commit 级 bitwise、真值门锁 L2 阈值；两门分开报告，禁止用回归门绿冒充精度合格。
净室实现：L2 用解析解采样即可，无需 Shamrock 代码。

**SPHSOL-shamrock-02 — GPU 弱扩展实验协议 · `reference` · GAP-SPH-GPU-SCALING**
`examples/benchmarks/sph_weak_scale_test.py`：**固定"每设备粒子数"** `N_target = N_base × world_size`（32e6/设备），
`compute_multiplier = shamrock.sys.world_size()`，跑 Sedov 弱扩展；结果连同 `device_properties`（vendor/backend/mem）落 JSON（`sph_homogeneous_bench_result.json`）。
**可迁移**：我们的 GPU scaling 实验照此模板（固定 N/device、记录后端与显存、报告并行效率而非只报墙钟），并补 Shamrock 缺的**强扩展 + 收敛性交叉**（同算例同时报精度，防"加速靠降精度"）。**不复制代码**。

**SPHSOL-shamrock-03 — h–ρ 自洽迭代的三算法收敛判据 · `adapt` · GAP-SPH-ISPH-CONVERGENCE**
`IterateSmoothingLengthDensity`（Newton）/`...BisectingNR`（二分保底+Newton）/`...NeighLim`（邻数上限）
（`modules/` 下三套并存，驱动 `LoopSmoothingLengthIter`；`density.hpp:20-40` 给 `rho_h/h/rho` 与牛顿步）。
**可迁移**：把"主迭代 + 保底算法 + 迭代上限 + 未收敛计数"这套**收敛保护模式**用到我们的迭代式压力求解（ISPH/DFSPH 的 Poisson 迭代）：
每步统计 `allreduce_sum(cnt_unconverged)`（Shamrock 在 `Solver.cpp:2049` 已有此统计）→ 未收敛率进报告，超过阈值 honest-FAIL。
注意：**物理方程不同**（h↔ρ 自洽 ≠ 压力 Poisson），只迁工程模式，不迁公式。

**SPHSOL-shamrock-04 — Cullen&Dehnen 2010 黏性开关作为 δ-SPH 扩散开关的对照 · `reference` · GAP-SPH-DELTA-SPH**
Shamrock **无 δ-SPH**；但其 `AVConfig_VaryingCD10`（`AVConfig.hpp:113`，参数 α_min/α_max/σ_decay/α_u/β_AV）实现了 CD10 的
"压缩触发 + 膨胀衰减"开关。**可迁移**：我们实现 δ-SPH 密度扩散项时，扩散系数 δ 的开关律可直接对照 CD10 的 α 演化律做消融（谁更抑制激波后振荡/谁更保静水压），
把 Shamrock 当**黑盒对照臂**（跑其 CI 算例取曲线），代码不进仓。

**SPHSOL-shamrock-05 — 双流体尘埃 TVA + Epstein/COALA 停时 · `reference` · GAP-SPH-MULTIPHASE**
`SolverConfig.hpp:160-190` + `NodeUpdateDerivsMonofluidTVA.hpp` + `SetDustStoppingTimeEpstein.hpp`/`NodeEvolveDustCOALASourceTerm.hpp`。
**可迁移**：我们的**固-液/泥沙**相交替使用的是"拖曳停时 τ_s + 双流体动量交换"，这套 `C_1_fluid`/`C_drift` + 密度钳制（`clamp_dust_frac=0.99`）是现成参考；
**不适用**气-液界面（无表面张力/无界面重构）。判定 `reference`：只借模型与量纲，不借代码。

**SPHSOL-shamrock-06 — Sedov 解析解（L1 表驱动）交叉校验 · `reference` · GAP-SPH-ASTRO-SEDOV**
`shamphys/SedovTaylor.hpp:34` + `sedov_soluce_arrays.hpp`（自相似解查表），配 `examples/TO_MIGRATE/sph/sph_sedov.py:116` 的用法。
**可迁移**：我们的强激波/爆炸验证可用同一自相似解做真值（需自建解析解，不得 vendor 其表）；Shamrock 的 `expected_L2`（step1 ρ: `1.12e-7`）可作**同量级交叉对照**，但因其为码间差，**不得当作精度上限引用**。

---

## 五、注意点（坑与局限）

1. **"验证完备"是错觉**：Sedov 容差 1e-16 是**码间逐位一致**（vs Phantom dump），不是物理精度；把 Shamrock CI 绿当"SPH 算得准"的证据会误判。
2. **无固体壁**是硬缺口：船海 dam-break/水槽/结构壁面**一条都做不了**（§2.2 三条 0 命中交叉验证）。任何"用 Shamrock 做船海算例"的设想先判 NOT_SUPPORTED。
3. **只有密度基 WCSPH**：ISPH/PCISPH/DFSPH/δ-SPH/PBD 全部 0 命中 → 若有人声称"Shamrock 有不可压 SPH"，属误传，本报告可直接反驳。
4. **仅 3D、仅 f64**：`Solver.cpp:4234-4239` 只实例化 `f64_3`；2D 教学/快速原型不可用。
5. **版本漂移极快**（每日多 PR + solvergraph 大重构）：任何代码定位一周内可能失效，必须锁 SHA。
6. **上架中的未完成迁移**：4 个 SPH 算例在 `examples/TO_MIGRATE/sph/`（含 `sph_sedov.py`），说明"解析解 vs SPH"链路当前**不在** CI —— 引用其验证覆盖时要打折。

---

## 六、误差判定（逐项，显式回答是否 <3%）

**声明**：本单为**开源盘点任务**，我方未运行 Shamrock，也未产生自有基准算例；因此"关键量 vs 基准误差"的判定对象是
**Shamrock 仓内已声明的量化数据**（程序化提取，见 `shamrock_metrics.json`）。我方平台侧无可比基准 → 不涉及我方差值。

| 关键量 | 基准 | 数值（仓内声明） | 是否 <3% |
|---|---|---|---|
| Sod 管密度 L2（绝对范数，ρ₀=1） | 解析 Riemann 解 | `1.6155e-04` | **是**（相对 ρ_g=1 即 0.016%） |
| Sod 管 x 速度 L2 | 解析解 | `1.1627e-03` | **是**（相对 u≈P/((γ−1)ρ)=2.5 即 0.047%） |
| Sod 管压力 L2 | 解析解 | `1.2484e-04` | **是**（相对 P=1 即 0.012%） |
| Sedov ρ L2（vs Phantom，step 1/10/100） | 外部码 dump（**非解析**） | `1.12e-07 / 1.09e-07 / 6.16e-07` | **是**（码间差 ~1e-7）但**不可作精度证明**（诚实标注：code-to-code） |
| Sedov α（黏性开关）L2 | 同上 | `1.11e-06 / 2.37e-02 / 6.43e-05` | 否/是混合：step 10 达 `2.4e-2`（2.4%）——**<3% 但接近边界**，且 α 是人为开关量、非物理量 |
| Sedov vs **解析解** 的 SPH 误差 | SedovTaylor 解析 | **无 CI 数据**（脚本在 `TO_MIGRATE`） | **WITHHELD**（不可判定，不许含糊） |
| 收敛阶（L1 随 N 的斜率） | 理论阶 | 无 CI 数据 | **WITHHELD** |

**honest 判定**：Shamrock 在**其自证范围内**（Sod vs 解析）误差远优于 3% 线（0.01%~0.05% 量级），但这是**一维光滑管**问题；
二维/三维强激波（Sedov）与解析解的偏差**仓内无数据**，只能给 WITHHELD。**不得**用码间一致（1e-7）替代物理精度声明。

---

## 七、机器块

[OSS-INV]{"campaign":"osssph-w2","repo":"Shamrock-code/Shamrock","tier":1,"methods":["WCSPH-density-based(only)","GSPH-Godunov-SPH-Riemann","dust-two-fluid-TVA","ideal-MHD-constrained-hyperbolic","self-gravity(Direct/FMM/MM/SFMM)","leapfrog-predictor-corrector","h-rho-selfconsistent-iteration(Newton/BisectingNR/NeighLim)","AV:none|constant|MM97|CD10|constant_disc","kernels:M4/M6/M8/C2/C4/C6-instantiated","NOT-present:ISPH,PCISPH,DFSPH,IISPH,delta-SPH,PBD"],"boundary":"only Free(expanding box)/Periodic/ShearingPeriodic(Stone2010); BasicSPHGhosts is MPI inter-patch ghost only; no solid wall/reflective/ghost-fluid wall anywhere (0 hits); dam-break & Poiseuille absent (0 hits)","gpu":"SYCL single-source via AdaptiveCpp (CUDA/ROCm/OpenMP/OpenCL) + MPI patch-domain decomposition with dynamic load balancing; Morton BVH CompressedLeafBVH + NeighbourCache(SingleStage/TwoStage); upstream-claimed weak scaling 1->60 nodes, 2 GPU/node (not reproduced by us)","validation":"CI shamrock-acpp-phys-test.yml 38 matrix entries (worldsize 1&2); Sod tube vs analytic Riemann L2 (rho 1.6155e-4) as regression gate tol 1e-11 rel; Sedov blast vs Phantom reference dumps, L2 rho 1.12e-7(step1) with tol 1e-16..1e-20 = bitwise code-to-code; no convergence-order CI; Sedov-vs-analytic SPH script not migrated (examples/TO_MIGRATE) -> WITHHELD","license":"CeCILL-2.1 (SPDX CECILL-2.1, strong copyleft, French GPL-equivalent); no permissive dual license -> code copy into TensorPhysics NOT compliant; reference-only for code, clean-room reimplementation allowed from MNRAS 539,1 (arXiv:2503.09713); Phantom reference dumps are GPL-3 data, must not be vendored","transferable":["dual-track validation gate (regression-bitwise + truth-L2)","GPU weak-scaling protocol with fixed particles/device + device_properties provenance","convergence-guard pattern for iterative solvers (primary + fallback algo + unconverged-count allreduce)","CD10 alpha switch as black-box control arm for delta-SPH diffusion switch","two-fluid dust TVA + Epstein/COALA stopping time as reference for solid-liquid drag coupling","Sedov L1 tabulated self-similar solution as truth source (build own, do not vendor)"]}[/OSS-INV]

[OSS-SOL]{"repo":"Shamrock-code/Shamrock","id":"SPHSOL-shamrock-01","kind":"reference","summary":"Dual-track validation gate for our WCSPH: separate (a) bitwise regression gate against pinned record values from (b) truth gate vs analytic L2; never let regression-green masquerade as accuracy-pass. Clean-room L2 sampler, no code copied (CeCILL).","gap":"GAP-SPH-WCSPH-VALIDATION"}[/OSS-SOL]

[OSS-SOL]{"repo":"Shamrock-code/Shamrock","id":"SPHSOL-shamrock-02","kind":"reference","summary":"GPU weak-scaling experiment protocol: fix particles-per-device (N = N_base*world_size), record device_properties(vendor/backend/mem) in JSON, report parallel efficiency; we add the strong-scaling + accuracy-coincidence leg that Shamrock lacks. Protocol-level transfer only.","gap":"GAP-SPH-GPU-SCALING"}[/OSS-SOL]

[OSS-SOL]{"repo":"Shamrock-code/Shamrock","id":"SPHSOL-shamrock-03","kind":"adapt","summary":"Transfer the convergence-guard engineering pattern (primary Newton + bisection fallback + neighbour-limited cap + allreduce_sum(unconverged) statistics) to our iterative pressure solver; equations differ (h<->rho self-consistency != pressure Poisson), so formulas are NOT transferred.","gap":"GAP-SPH-ISPH-CONVERGENCE"}[/OSS-SOL]

[OSS-SOL]{"repo":"Shamrock-code/Shamrock","id":"SPHSOL-shamrock-04","kind":"reference","summary":"Shamrock has no delta-SPH; use its AVConfig_VaryingCD10 (Cullen&Dehnen 2010 alpha switch, alpha_min/max, sigma_decay) as an external black-box control arm when ablating the delta-SPH density-diffusion switch. Run as external reference, code never enters our tree.","gap":"GAP-SPH-DELTA-SPH"}[/OSS-SOL]

[OSS-SOL]{"repo":"Shamrock-code/Shamrock","id":"SPHSOL-shamrock-05","kind":"reference","summary":"Two-fluid dust TVA (C_1_fluid, C_drift, clamp_dust_frac=0.99) + Epstein/COALA stopping-time models as a reference model for solid-liquid drag coupling in our multiphase line; NOT applicable to air-water (no surface tension / no interface reconstruction).","gap":"GAP-SPH-MULTIPHASE"}[/OSS-SOL]

[OSS-SOL]{"repo":"Shamrock-code/Shamrock","id":"SPHSOL-shamrock-06","kind":"reference","summary":"Sedov-Taylor L1 tabulated self-similar solution as truth source for strong-shock validation; we build our own tabulation (their tables not vendored). Their code-to-code expected_L2 (rho 1.12e-7) is usable only as an order-of-magnitude cross-check, never as an accuracy bound.","gap":"GAP-SPH-ASTRO-SEDOV"}[/OSS-SOL]

---

## 八、图（`artifacts/oss_sph_w2/fig_shamrock_evidence.png`，程序化生成）

- **(a)** 源码体量分布（按 `src/` 模块，`LOC` 本机统计）：`shammodels` 81,102 行居首 → SPH 求解器是仓的核心，`tests` 36,384 行说明自研测试基建占比高。
- **(b)** Sod 管 vs 解析解的 L2 误差（ρ/vx/vy/vz/P，对数坐标）：全部在 1e-7~1e-3 区间，其中 ρ 与 P 最小 —— 解释为"一维光滑问题下 WCSPH 高精度"，不可外推到多维强激波。
- **(c)** Sedov 爆炸 vs Phantom 参考 dump 的 L2 差随步数演化（step 1/10/100）：ρ 稳定在 1e-7 量级（码间一致），
  **α（黏性开关）在 step 10 跳到 2.37e-2** —— 说明"开关量"在激波穿越时对扰动极敏感，跨码回归对 α 的容差实际很松（也可视作该方法在混沌量上不可逐位复现的证据）。

> 图数据来自 `shamrock_metrics.json`（`sod_l2_vs_analytic`、`sedov_l2_vs_phantom`、`loc_by_src_module`），
> 由 `make_metrics.py` 从仓内文件正则解析，**零手抄**。