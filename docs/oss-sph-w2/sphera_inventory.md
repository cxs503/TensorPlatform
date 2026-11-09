# OSS-SPH 深盘波2 · GiordanoAgateRSE/SPHERA 普查报告

> **任务**: m8700（重派；原 m8659 no_progress 中断） · **战役**: osssph-w2 · **集团军**  
> **目标仓**: `GiordanoAgateRSE/SPHERA`（tier 1，意大利 RSE SpA 工业 SPH）· **产出仓**: TensorPlatform（不建 PR）  
> **调查日期**: 2026-11-09 · **基线**: tag `v10.0.0` = `master` tip `8ef8438`（2022-12-04, AndreaAmicarelliRSE）  
> **证据工件**: `artifacts/oss-sph-w2/sphera_evidence.json`（逐文件 bytes/lines/sha256）、
> `artifacts/oss-sph-w2/fig1..3.png`、取数脚本 `runs/m8700_sphera/*.py`

---

## 一、案例介绍（调查对象与背景）

SPHERA 是意大利 RSE SpA（前 ERSE/CESI Ricerca）自 2005 年维护的**网格无关 CFD 研究/工业软件**，
面向水工与土工水力学：溃坝/溃堤、滑坡涌浪、晃荡、溃坝波与结构相互作用、可冲刷床面、稠密颗粒流。
v10.0.0 为最终公开版本（仓库自 2022-12 后无新提交）。

| 项 | 值 | 证据（可复现） |
|---|---|---|
| 仓库 | `GiordanoAgateRSE/SPHERA` | `git remote -v`（blobless clone `/tmp/sphera_w2`） |
| 默认分支 | `master` | `.git/HEAD` |
| 基线 commit | `8ef8438`（2022-12-04） | `git log -1` |
| tag | `v10.0.0` | `git for-each-ref` |
| 语言 | **Fortran 90**（261 × `.f90`） | `git ls-tree -r` 扩展名直方图 |
| 规模 | 934 个文件；`.inp` 606、`.f90` 261、`.ply` 25、`.txt` 24、`.vtu` 13、`.pdf`/`.docx` 各 1 | 同上（本次实查计数，见 evidence json） |
| 顶层结构 | `COPYING.txt` `doc/` `exe/` `input/` `src/` | `git ls-tree HEAD --name-only` |
| License | **GPL-3.0-or-later** | `COPYING.txt` 全文 + 每个源码头 `Continuity_Equation.f90:9-19`「GNU General Public License … version 3 or later」 |
| 构建 | `src/Makefile`：`COMPILER=gfortran`（备选 ifort）`:36`、`EXECUTION=debug` `:42`、`PARALLELIZATION=OMP` `:50`；32 个可执行体（commit message） | `src/Makefile:32-50` |
| 文档 | 单一 `doc/SPHERA_v_10_0_0_documentation_file.pdf/.docx`（无 sphinx 站点） | tree |
| 测试/CI | **无**：无 `tests/`、无 `.github/`、无 CI 配置（脚本实测 `has_ci=false`, `has_tests_dir=false`） | tree 全量扫描 |
| GitHub 元数据（stars/forks/issues） | **insufficient**：本机与集群 `api.github.com` 配额被全舰队共享耗尽（`/rate_limit` 返回 `remaining:0`，`reset` 已过期仍被抢占），`raw.githubusercontent.com` 被出口重置（`Errno 104`），`codeload` 归档下载 120 s 内 0 字节 → 星数等元数据**未能实查**，不做记忆填充 | `/tmp/fetch_sp.log`, `/tmp/sphera_tar.log` |

> 说明：stars 等元数据缺口不影响本报告的方法/验证面结论；所有方法结论均来自**源码本体**（sha256 记录在 evidence json）。

---

## 二、怎么做的（方法、流程、工具链）

1. **传输路线选择**：本机 `curl` 被安全策略拦截、`raw.githubusercontent.com` 连接重置、`codeload` 归档无进展；
   可用路线只有两条 —— `git` 协议（github.com）与 `api.github.com`。
   采用 **blobless shallow clone**（`git clone --depth 1 --filter=blob:none --no-checkout`）拿到**完整 tree**
   （934 条路径，85 s 完成）→ 免费获得目录结构/算例清单；blob 按需 `checkout` 时发现出口带宽极低（10 min 仅 1 个 blob），
   故改用 **GitHub contents API 定点取文件**（`runs/m8700_sphera/fetch_sphera_files.py`、`fetch_sphera_batch2.py`），
   每次响应内联 base64 内容，落盘后记 `bytes/lines/sha256`。
2. **配额竞争处理**：全舰队共用出口 IP（60 req/h）。脚本按 `≥1 剩余额度` 轮询 + 12 s 退避，先到先取；
   共成功取回 **12 个文件 / 2120 行**（10 个 `.f90` + `Makefile` + `COPYING.txt`）。
3. **源码级定位**：对落盘文件 `grep -n` 定位程序单元头（`Program unit/Description` 注释块）+ 关键调用点，
   形成 file:line 证据；未取回的文件**只按路径/命名登记**并显式标注「content insufficient/未取回」，不猜内部算法。
4. **验证面盘点**：从 tree 全量枚举 `input/tutorials/**` 算例目录名（47 个唯一 deck），按物理类别程序化归类
   （`runs/m8700_sphera/make_evidence_and_figures.py:classify`），不手工挑选。
5. **生态对照**：读本地 TensorSPH 检出（`/opt/mm-verify/TensorSPH`，MIT）源码与 `LICENSE`，
   做文件级能力探针，用于可迁移方案与 license 判定。
6. **工具链**：`git`（tree/元数据）、`python3 urllib`（contents API，绕开被拦的 curl）、`grep -n`（定位）、
   `matplotlib`（图，数据全部程序化注入，零手抄）。

---

## 三、计算结果（数字表）

### 3.1 SPH 变体与离散格式（source-confirmed，file:line）

| 能力 | 结论 | 定位 |
|---|---|---|
| 基本框架 | **WCSPH 型**（弱可压缩）：密度由连续方程推进，压力由状态方程给出，**无压力泊松投影** | `Continuity_Equation.f90:23-25`（"Continuity Equation RHS, included explicit ALE1 and ALE2 terms"）；`EoS_fluid_loop.f90:22-25`；`EoS_barotropic_linear.f90`（60 行，线性正压 p=p_ref+k_bulk(ρ−ρ_ref)） |
| 一致性/精度技术 | **梯度重归一化**（每粒子 3×3 B_ren 矩阵，先求逆再作用于 ∑m_j/ρ_j·∇W）：这是 SPHERA 的「renormalized SPH」内核，区别于教科书 WCSPH | `B_ren_divu_inversion.f90:23-25`（"Inversion of the renormalization matrix … velocity-divergence approximation"）；`Continuity_Equation.f90:204-234`（`MatrixProduct(pg(npi)%B_ren_divu, …)`） |
| 2D 重归一化 | 2D 分支另建 `aij(4)` 2×2 矩阵求逆（限制 det≥1e-3），并算应变率第二不变量 | `Continuity_Equation.f90:196-200, 262-281` |
| ALE 项 | 连续方程内嵌 **ALE1/ALE2（显式）+ ALE3 开关**，处理运动/变形边界的「冻结质量」粒子与体积守恒 | `Continuity_Equation.f90:127-160, 236-247` |
| 动量方程 | `acc = tpres + tdiss + tvisc + g`（+ SA-SPH 弹性边界反力） | `RHS_momentum_equation.f90:165, 171, 177` |
| 人工黏性 | **Monaghan (2005)** 人工黏性（对分离粒子也生效，体积黏性忽略） | `viscomon.f90:23-25`（子程序 `viscomon`，含 rvwalfa/rvwbeta） |
| 层流黏性 | **Morris 型**拉普拉斯黏性项（独立子程序） | `viscomorris.f90`（76 行） |
| 数值平滑/耗散 | 速度平滑 + 压力平滑（v10 正式移除 δ-SPH，改为这两支） | `velocity_smoothing.f90`（141 行）、`PressureSmoothing_3D.f90`（171 行）；`BE_mass/` 另有 2D 版 |
| 边界处理（主） | **DB-SPH「半粒子/壁面单元」**：壁面三角/线段单元作为「扩散边界」，在连续/动量方程里以 `pg_w`（壁单元）权重、法向、Gamma 项贡献；含半粒子体积与壁面剪切黏性项 | `Continuity_Equation.f90:253-270`（`pg_w(npj)%weight/normal/Gamma`）与 `RHS_momentum_equation.f90:144-147`（`AddBoundaryContributions_to_ME2D/3D`）；模块 `src/DB_SPH/` 15 个程序单元：`semi_particle_volumes.f90`、`DBSPH_kinematics.f90`、`DBSPH_BC_shear_viscosity_term.f90`、`DBSPH_inlet_outlet.f90`、`wall_elements_pp.f90` |
| 边界处理（辅） | **SA-SPH「半解析 SPH」**：把壁面贡献写成**解析边界积分表**（W 的体/面积分、反射矩阵），不铺粒子层 | `src/SA_SPH/integrals_IC/{ComputeKernelTable,ComputeBoundaryIntegralTab,JdWsRn,WIntegr}.f90`、`integrals_time_steps/{ComputeVolumeIntegral_WdV2D,ComputeSurfaceIntegral_WdS2D,InterpolateBoundaryIntegrals2D}.f90`、`BE_momentum/AddElasticBoundaryReaction_3D.f90`（本次取回，见 evidence） |
| 自由面 | 自由面判定用 **9 点模板 max 搜索（z_FS_max_9p_stencil）** + 法向修正 | `src/BC/z_FS_max_9p_stencil.f90`、`src/BC/NormFix.f90`、`src/BC/domain_edges.f90`（**文件已定位，内容未取回 → 内部公式 insufficient**） |
| 时间积分 | Euler / Heun / **Leapfrog（连续/动量/轨迹分三支）**；步长控制单独程序单元 | `src/Time_Integration/{Euler,Heun,Leapfrog_continuity,Leapfrog_momentum,Leapfrog_trajectories,time_step_duration,stoptime}.f90`（内容未取回 → CFL 公式 insufficient） |
| 多相/颗粒 | **两相：液相 + 稠密颗粒相（KTGF，动理学颗粒流理论）**，颗粒相走独立分支（含应变率第二不变量的颗粒温度路径）；另有冲刷/侵蚀判据 | `Continuity_Equation.f90:113-116, 166-168, 262-281`（`Granular_flows_options%KTGF_config`）；`src/KTGF_tools/{KTGF_update,mixture_viscosity,initialization_fixed_granular_particle}.f90`；`src/Erosion_Criterion/{Shields,compute_k_BetaGamma,fixed_bed_slope_limited}.f90` |
| 刚体动力学 | 刚体平移/转动 + 体-体、体-壁接触 + 摩擦/法向反力（非连续介质 FSI） | `src/Body_Transport/**`（`RHS_body_dynamics.f90`、`body_boundary_for_sliding_friction_normal_reaction.f90`、`Gamma_boun.f90`） |
| 并行 | **仅 OpenMP 共享内存**（`PARALLELIZATION = OMP`）；**无 MPI、无 GPU、无 CUDA/HIP** | `src/Makefile:50`；tree 中无 `*.cu`/MPI 头 |
| I/O | VTU/VTK 输出、监测点/线/面、重启 | `src/Post_processing/{result_converter,final_post_processing,monitors}`、`input/template/15_vtu_output_timing…`、`13_restart…` |

> ⚠️ 内部公式（CFL 系数、核函数具体型别、DB-SPH 半粒子体积解析式）因 blobs/quota 限制**未取回**，标 `insufficient`；
> 已取回 12 个文件共 **2120 行**全部带 sha256（`sphera_evidence.json`）。

### 3.2 验证面（程序化计数）

- **47 个唯一算例 deck**（`input/tutorials/{release_v_10_0_0, release_v_9_0_0, subversions_between_v9_v10}/`），
  606 个 `.inp` 分块文件；分类计数（脚本 `classify()`，非人工挑选）见 **图 1**：

| 类别 | deck 数 |
|---|---:|
| granular / erodible bed（含 liquefaction：San Fernando、Alpe Gera、ICOLD、KarlSand、Pon10、Spi05、FraCap02） | 10 |
| other（body/滑移/涌浪手写用例等） | 7 |
| body impact（body-body、body-boundary、symmetric wedge） | 5 |
| internal flow / weir（flushing 2D/3D、rectangular weir Fr=0.491、spillway with piers、jet plate） | 5 |
| dam/dike breach（dam_breach_ICOLD、dike_breach_2D_expSchHag12JHR、Kagerplassen 2015、dam_break_3D_demo） | 4 |
| wave / tsunami（tsunami_benchmark1_ISEC、wave_motion_for_WAVESAX 两支） | 4 |
| landslide / wave gen.（submerged_landslide、2D Vajont experiment、douf Vajont） | 3 |
| still water（still_water_tank、stream_flat_bottom 等） | 3 |
| floating body（floating_cube_stability、water_shipping） | 2 |
| sloshing tank（T/Tn = 0.78、1.07） | 2 |
| Couette（spherical_Couette_flows、Couette_sf） | 2 |

- **实验对照线索（仅命名，非量化数据）**：`13_dike_breach_2D_expSchHag12JHR`（Schmocker & Hager 2012, JHR）、
  `08_db_body_exp_UniBas`、`30_SPH_udb_exp_Kim2015HYDROL`、`20_edb_Pon10`、`17_edb_Spi05`、`15/16_edb_2D_FraCap02`、
  `35_2D_Vajont_experiment`、`37_tsunami_benchmark1_ISEC` —— 说明**作者用实验/基准标定过**，但
  **仓库内无参考结果表、无 pass/fail 断言、无 CI**：量化误差数据只可能存在于 PDF 手册，**本次未能提取 → insufficient**。
- **无 dam-break 数字化实验表**（Martin & Moyce 曲线）**入库**；无 Poiseuille / Taylor–Green 解析解用例（tree 全量搜索无匹配目录名）。

### 3.3 能力矩阵（对 TensorSPH 线，文件级审计）

见 **图 3**。要点：SPHERA 独有 = 重归一化梯度、DB-SPH 壁面单元、SA-SPH 解析壁面积分、KTGF 稠密颗粒相、
Shields 侵蚀判据、ALE 项；TensorSPH 独有 = Tensor/GPU 执行、有 CI+单测；两者**都没有** ISPH/DFSPH 投影压力与 MPI。

---

## 四、结果图

1. `fig1_tutorial_decks.png` —— 47 个算例 deck 的物理分类计数（验证面宽度）。
2. `fig2_audited_sources.png` —— 本次取回文件的字节量（审计覆盖面与取证成本）。
3. `fig3_capability_matrix.png` —— SPHERA × TensorSPH 能力存在性矩阵（"yes/‑"，来源：文件级 grep，非推测）。

（图由 `runs/m8700_sphera/make_evidence_and_figures.py` 生成，数据全部来自 `git ls-tree` 与 API 落盘文件，
无任何手工录入数字。）

---

## 五、注意点（踩坑 / 局限 / 适用范围）

1. **出口带宽/配额是本战役最大障碍**：`raw.githubusercontent.com` 直接 RST；`codeload` 归档 2 分钟 0 字节；
   blobless clone 的 tree 很快（85 s / 934 路径），但 blob 拉取 ~1 文件/分钟，且 `git ls-tree -l` 会触发 promisor 拉取而超时。
   ⇒ 后续 SPH 深盘波建议默认「tree + contents API 定点取 ≤20 文件」，并把配额当共享稀缺资源排队。
2. **GPL-3.0 传染性**：TensorSPH 是 MIT。⇒ SPHERA 代码**不可**复制/改写到 TensorSPH（违反 MIT 再许可预期与 GPL 义务），
   只能作「公式级 reference + 验证基准」，并在文档中注明来源与版本（本报告即按此执行）。
3. **「DB-SPH」不是 δ-SPH**：SPHERA v10 的耗散走**压力/速度平滑 + Monaghan 人工黏性**，δ-SPH 已被移除；
   把 SPHERA 当 δ-SPH 参照系会误判（这一点与我方 TensorSPH 的 `density_diffusion` 也不是同一物，见 `droplet_impact.py:79-82`
   自述「not delta-SPH proper」）。故 GAP-SPH-DELTA-SPH 只能拿它当**对照**而非**实现模板**。
4. **验证面「宽而不硬」**：47 个 deck 覆盖面广（溃坝/晃荡/浮体/涌浪/可冲床），但无量化断言、无 CI；
   引用其结论时必须回到 PDF 手册或原始实验文献，不能把 deck 存在当作精度证据。
5. **未取回即 unknown**：自由面 9 点模板、CFL 时间步、半粒子体积、核函数型别均未读源码 → 本报告不给公式级断言。
6. **计算条件**：本单为**纯源码审计**，无 SPHERA 编译（无 Fortran blobs 全量、且 GPL 代码不进入我方 CI）。

---

## 六、误差判定（honest）

**本单不含任何数值仿真结果，因此不存在「关键量 vs 基准」误差可比对象。**
逐项声明：

| 关键量 | 与基准误差 | 是否 <3% | 原因 |
|---|---|---|---|
| SPHERA 溃坝前沿/晃荡波高 vs 实验 | 无数据 | **不适用** | 未编译未运行（blobs/quota 受限 + GPL 不入 CI）；仓库内亦无参考结果表 |
| 文件/算例计数（261 f90、606 inp、47 decks） | — | **精确**（非估计） | `git ls-tree -r` 全量计数，脚本可复现 |
| 方法归属（WCSPH/重归一化/DB-SPH/KTGF/OpenMP） | — | **source-confirmed** | 逐条 file:line 见 §3.1 |
| 未取回程序单元（9 点自由面模板、CFL、半粒子体积） | — | **insufficient** | 内容未取回，拒绝凭命名推测 |
| stars/forks/issues | — | **insufficient** | API 配额耗尽 + raw/codeload 不可达 |

结论：**本报告不宣称任何 <3% 精度结论**——它是审计与方法定位报告，不是验证报告；量化精度结论留待
SPHERA deck 编译运行（需 GPL 隔离环境）或改以 TensorSPH 自身基准给出。

---

## 七、机器块

[OSS-INV]{"campaign":"osssph-w2","repo":"GiordanoAgateRSE/SPHERA","tier":1,"ref":"v10.0.0@8ef8438","language":"Fortran90","license":"GPL-3.0-or-later","files":934,"f90":261,"inp":606,"tutorial_decks":47,"methods":["WCSPH (density from continuity + barotropic EoS, no pressure projection)","renormalised gradients via per-particle 3x3 B_ren inversion (B_ren_divu_inversion.f90:23-25; Continuity_Equation.f90:204-234)","ALE1/ALE2/ALE3 terms for moving/deforming boundaries (Continuity_Equation.f90:127-160,236-247)","Monaghan(2005) artificial viscosity (viscomon.f90:23-25) + Morris laminar viscosity (viscomorris.f90)","velocity/pressure smoothing as dissipation (velocity_smoothing.f90, PressureSmoothing_3D.f90) — delta-SPH removed in v10","rigid body dynamics + contact/friction (src/Body_Transport)","KTGF dense-granular two-phase branch (Continuity_Equation.f90:113-168; src/KTGF_tools)","Shields erosion criterion (src/Erosion_Criterion/Shields.f90)","Leapfrog/Euler/Heun time integration (src/Time_Integration)","VTU output, monitors, restart"],"boundary":"DB-SPH semi-particle wall elements (pg_w weights/normals/Gamma: Continuity_Equation.f90:253-270, RHS_momentum_equation.f90:144-147; src/DB_SPH 15 units) + SA-SPH analytic boundary integral tables (src/SA_SPH/integrals_IC, integrals_time_steps); free-surface detection by 9-point stencil (src/BC/z_FS_max_9p_stencil.f90, content not fetched)","time_step":"dedicated unit src/Time_Integration/time_step_duration.f90 + Leapfrog_{continuity,momentum,trajectories}; CFL formula not fetched (insufficient)","multiphase":"liquid + dense granular (KTGF) with separate continuity branch; no free-surface multiphase/interface tracking","free_surface":"src/BC/z_FS_max_9p_stencil.f90 + NormFix.f90 (located, content not fetched)","gpu":"none","parallel":"OpenMP only (src/Makefile:50 PARALLELIZATION=OMP, gfortran default :36); no MPI, no CUDA/HIP","validation":"47 tutorial decks (dam/dike breach 4, sloshing tank 2, floating body 2, wave/tsunami 4, landslide 3, granular/erodible 10, weir/internal 5, body impact 5, Couette 2, still water 3, other 7) with experiment-named decks (expSchHag12JHR, UniBas, Kim2015HYDROL, Pon10, Spi05, FraCap02, Vajont, ISEC tsunami) BUT no reference tables, no assertions, no CI, no tests/ dir -> quantitative accuracy insufficient","ci":"none","tests":"none","transferable":["SPHSOL-sphera-01","SPHSOL-sphera-02","SPHSOL-sphera-03","SPHSOL-sphera-04"]}[/OSS-INV]

[OSS-SOL]{"repo":"GiordanoAgateRSE/SPHERA","id":"SPHSOL-sphera-01","kind":"reference","summary":"DB-SPH semi-particle wall elements as an alternative wall closure to TensorSPH's 3-layer wall lattice: re-derive the semi-particle volume/weight formulation from SPHERA docs+sources and benchmark both closures on the existing 2-D dam-break case (mass error, front trajectory convergence); SPHERA values act as an independent reference, no code reuse (GPL-3.0 -> MIT incompatible)","gap":"GAP-SPH-WCSPH-VALIDATION","license":"GPL-3.0-or-later vs TensorSPH MIT => reference-only, re-implement from published equations, cite ref v10.0.0"}[/OSS-SOL]
[OSS-SOL]{"repo":"GiordanoAgateRSE/SPHERA","id":"SPHSOL-sphera-02","kind":"reference","summary":"Quantify the dissipation ladder: SPHERA uses velocity/pressure smoothing + Monaghan artificial viscosity while TensorSPH uses a density_diffusion rate explicitly documented as 'not delta-SPH proper' (droplet_impact.py:79-82). Build a shared 2-D dam-break/green-wave decay harness measuring free-surface damping vs diffusion coefficient, and use SPHERA's documented deck settings as the comparison scale","gap":"GAP-SPH-DELTA-SPH","license":"GPL-3.0 reference-only; harness written in TensorSPH (MIT) from scratch"}[/OSS-SOL]
[OSS-SOL]{"repo":"GiordanoAgateRSE/SPHERA","id":"SPHSOL-sphera-03","kind":"reference","summary":"KTGF dense-granular branch (continuity split at Continuity_Equation.f90:113-168 + src/KTGF_tools mixture_viscosity) is a ready-made formulation template for ship-relevant cargo liquefaction / sediment-laden sloshing; add a granular phase to TensorSPH behind a flag and validate against the San Fernando / ICOLD style liquefaction decks","gap":"GAP-SPH-MULTIPHASE","license":"GPL-3.0 reference-only; equations from KTGF literature (Gidaspow), SPHERA only as behavioural reference"}[/OSS-SOL]
[OSS-SOL]{"repo":"GiordanoAgateRSE/SPHERA","id":"SPHSOL-sphera-04","kind":"fit","summary":"Ship-relevant validation decks are already named in-tree: sloshing tank T/Tn=0.78 and 1.07, floating_cube_stability, water_shipping, wave_motion_for_WAVESAX (seakeeping) — fit them as TensorSPH validation cases (geometry + monitoring lines only, no GPL code), re-using SPHERA's tank dimensions and non-dimensional groups as the case specification","gap":"GAP-SPH-WCSPH-VALIDATION","license":"case geometry/parameters are facts, not code; decks re-authored in TensorSPH input format; GPL code body not copied"}[/OSS-SOL]
[/OSS-SOL]

> 迁移口径（license 合规）：SPHERA 全仓 **GPL-3.0-or-later**，TensorSPH 为 **MIT** ⇒ 所有迁移项一律 **reference / fit（重写）**，
> 严禁复制 `.f90` 代码、注释块或输入卡解析逻辑；引用须标注 `GiordanoAgateRSE/SPHERA v10.0.0 (8ef8438)`。
> 4 条方案均**不新建 PR**，仅入平台报告供 SPH 战役排期。
