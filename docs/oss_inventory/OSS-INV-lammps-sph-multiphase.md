# OSS 深盘 · slitvinov/lammps-sph-multiphase（LAMMPS 内多相/相变 SPH）

> **campaign**: `osssph-w2` ｜ **tier**: 3 ｜ **pin**: `7d420ddc8bf4bcf46664e9d7dcf2943d7dbffaca`
> **平台报告 id**: `9973` ｜ **盘点日期**: 2026-11-09 ｜ **方法**: GitHub API 实查 + pin 版本源码逐行定位 + 独立复算
> **产物**: ``artifacts/oss-sph/lammps-sph-multiphase``（repo_meta / evidence / verification / machine_blocks / fig1）
> **生成方式**: 本报告由 `tools/oss_inventory/lammps_sph_multiphase_inventory.py` 渲染，
> 文中所有数字均来自 API 响应、源码解析或本模块的数值复算（零手抄）。

---

## ① 案例介绍（物理背景 / 几何 / 参数表）

### 1.1 物理背景

被盘仓是 **LAMMPS 的扩展补丁包**（不是独立求解器）：在 LAMMPS 的 `PKG_SPH` 之上补 6 个多相 pair style、1 个相变 fix、1 个 per-atom compute，覆盖**沸腾与蒸发**这类带潜热的多相自由面问题。物理栈：

- **弱可压缩 SPH（WCSPH）**：五次样条核、Tait 状态方程、密度求和法、显式积分（LAMMPS 内核 `fix sph`）；
- **多相（液/汽）**：每种相自带 `rho0 / cv / 声速 / 粘性 / 导热`，跨相用调和平均传递粘性、导热；
- **表面张力**：颜色函数梯度 + CSF 应力投影（不做曲率拟合）；
- **相变**：界面处液粒子超过饱和温度后按「超出显热/潜热」的速率概率蒸发，新汽粒子质量按核权重从邻域液粒子「精确捐出」，能量账扣 `m*Hwv`。

### 1.2 几何与算例（仓库自带 12 个算例目录）

仓库没有几何生成器，几何是 LAMMPS `lattice`/`region` 直接铺粒子：二维方形域、二维半无限板、二维圆域内液滴、三维立方域内气泡。`bubble_on_wall` 的域与物性全部写在输入脚本变量表里（`vars.lmp`），下表**原样抽取**该文件的表达式（不做数值化改写，避免转抄误差）：

| 变量 | 类型 | 输入脚本表达式（原样抽取） |
|---|---|---|
| `Lx` | equal | `2.0` |
| `nx` | index | `40` |
| `ny` | equal | `${nx}` |
| `Ly` | equal | `${Lx}` |
| `dx` | equal | `1.0/${nx}` |
| `sph_rho_g` | equal | `1.0` |
| `sph_rho_d` | equal | `0.01` |
| `sph_rho_w` | equal | `${sph_rho_g}` |
| `sph_c_d` | equal | `200/sqrt(${sph_rho_d})` |
| `sph_c_g` | equal | `200/sqrt(${sph_rho_g})` |
| `sph_c_w` | equal | `${sph_c_g}` |
| `sph_eta_d` | equal | `0.069395` |
| `sph_eta_g` | equal | `1.0` |
| `sph_eta_w` | equal | `${sph_eta_g}` |
| `gamma_d` | equal | `1.0` |
| `gamma_g` | equal | `1.0` |
| `gamma_w` | equal | `${gamma_g}` |
| `rbackground` | equal | `0.0` |
| `h` | equal | `3.0*${dx}` |
| `Nfreq` | equal | `100` |
| `sph_mass_d` | equal | `${dx}^${ndim}*${sph_rho_d}` |
| `sph_mass_g` | equal | `${dx}^${ndim}*${sph_rho_g}` |
| `sph_mass_w` | equal | `${sph_mass_g}` |
| `alpha` | equal | `150` |
| `alpha_gd` | equal | `${alpha}` |
| `alpha_gw` | equal | `${alpha}` |
| `alpha_dw` | equal | `1.5*${alpha}` |
| `D_heat_d` | equal | `0.6` |
| `D_heat_g` | equal | `0.1` |
| `D_heat_w` | equal | `${D_heat_g}` |
| `dprob` | equal | `0.01` |
| `Hwv` | equal | `32.0` |
| `cv_d` | equal | `1.0` |
| `cv_g` | equal | `0.04` |
| `cv_w` | equal | `${cv_g}` |
| `dT` | equal | `0.1` |
| `Tc` | equal | `0.0` |
| `Tt` | equal | `${Tc}+${dT}` |
| `e_d` | equal | `${cv_d}*${Tc}` |
| `e_g` | equal | `${cv_g}*1.0` |
| `e_w` | equal | `${e_g}` |
| `gy` | equal | `3.60e+05` |
| `g_type` | equal | `1` |
| `d_type` | equal | `2` |
| `w_type` | equal | `3` |

### 1.3 仓库元数据（GitHub API，2026-11-09 实查）

| 项 | 值 |
|---|---|
| 仓库 | `slitvinov/lammps-sph-multiphase` |
| 描述 | SPH with phase change for LAMMPS: boiling and evaporation with multiphase SPH (color gradient, surface tension) |
| 主语言 | C++（`/languages`：C++ 48984 B） |
| stars / forks / watchers | 19 / 13 / 4 |
| license | **null（无许可文件）** |
| 体积 / 建仓 / 最近推送 | 158540 kB / 2014-08-21T16:22:34Z / 2026-11-06T12:05:54Z |
| branches | latent-reservoir, main, surface_tension_clean_rewrite, surface_tension_clean, surface_tension_wip, wip |
| topics | boiling, cfd, computational-physics, evaporation, lammps, multiphase-flow, phase-change, smoothed-particle-hydrodynamics, sph, surface-tension |
| 最近 5 次提交 | `7d420ddc8bf4` 2026-11-06T12:05:51Z bubble_on_wall movie<br>`21ee0a2eec52` 2026-11-06T11:49:27Z 3D phase change examples in README<br>`1418306476c7` 2026-10-25T00:32:10Z 3d bubble_on_wall<br>`4cb162582031` 2026-10-24T23:20:46Z fix wall heating in bubble_on_wall; render<br>`b746a5ecf920` 2026-10-24T22:01:30Z restore validation examples and plots |
| CI | 无 `.github/` 目录（workflow 文件 0，见 3.4） |
| 测试布局 | 无 test/spec 文件；验证靠 `examples/*/run.sh` + gnuplot 目视 |

**判定**：license 字段为 `null`、树内无 `LICENSE`/`COPYING` 文件 → **许可不明 → 本仓只可 reference，禁止搬码**（纪律：license 不明 = reference-only）。

---

## ② 怎么做的（方法 / 流程 / 工具链）

1. **API 实查**（禁凭记忆）：`GET /repos/slitvinov/lammps-sph-multiphase`（元数据/语言/许可）、`/branches`、`/commits?per_page=15`、`/git/trees/7d420ddc8bf4bcf46664e9d7dcf2943d7dbffaca?recursive=1`（全树 133 条目）、`/languages`。API 走平台侧 token（环境变量 `GITHUB_TOKEN`，不回显）。
2. **pin 到 commit**：`7d420ddc8bf4bcf46664e9d7dcf2943d7dbffaca`（main HEAD），并把树内**全部源码/输入脚本 blob**（非 `img/`、非二进制、≤400 kB）下载到本地缓存 ``~/.cache/oss_inv/<owner>_<repo>/<pin>``，共 133 个文件——后续所有 `path:line` 都在这个确定版本上解析，浮动 main 的指针不会被混进来。
3. **源码级方法清单**：对每条能力声明写「文件 + 正则」，用脚本在 pin 版本里**检索出首个命中行**，把 `path:line` 与**该行原文**一起入证据表（不是人肉记行号）。正则失配即整脚本失败——证据表不允许静默缺项。
4. **独立复算（防「读完即抄」）**：把 `sph_multiphase.h` 里的**归一化常数与分段多项式**用正则抽出来翻译成 Python，再独立复算 4 条只由源码决定的恒等式：
   - 核函数归一化 `∫W dV = 1`（2D/3D 各一次）；
   - 解析导数表 `W'(q)` 是否等于 `W(q)` 的符号导数（sympy）；
   - Tait EOS 的 `B = c²ρ0/γ` 是否真的给出 `dp/dρ|_{ρ0} = c²`；
   - `fix phase_change` 的能量簿记是否严格满足 `ΔE = −m·Hwv`。
5. **否定声明同等举证**：对「没有 GPU / 没有 OpenMP / 没有 δ-SPH 密度扩散 / 没有 ISPH 投影 / 没有 CI / 没有测试 / 没有许可文件」逐条跑正则并记命中数（17 个源码文件扫描范围）。
6. **机器块**：JSON 机器块（本波 `osssph-w2` 契约）内嵌本报告；另出 playbook 的 key:value 块（``docs/oss_inventory/OSS-INV-lammps-sph-multiphase.machine.txt``），并过平台校验器 `python -m tools.oss_inventory.machine_blocks --strict`（0 违规）。

**未做的部分（诚实边界）**：本单**没有编译 LAMMPS、没有跑任何 SPH 算例**。因此第 ⑥ 节的误差判定只覆盖「源码可判定恒等式」，**不覆盖** Poiseuille/圆柱/板导热的物理精度——仓库的验证图是 gnuplot 目视对照，没有留数值误差指标，平台侧也无该仓的构建产物。

---

## ③ 计算结果（数字表，全部程序化注入）

### 3.1 代码级方法清单（逐条 `path:line`）

| 能力 | 实现要点 | 文件:行 |
|---|---|---|
| SPH 变体 | WCSPH（弱可压缩）：五次样条核 + Tait EOS + 密度求和 + 显式积分；非 ISPH/PFEM/δ-SPH | `MULTIPHASE/sph_multiphase.h:17`<br>`MULTIPHASE/sph_multiphase.h:47`<br>`MULTIPHASE/pair_sph_rhosum_multiphase.cpp:61`<br>`examples/bubble_on_wall/bubble.lmp:74` |
| 核函数 | 五次样条 f(q)，支撑 q<3（cut=3h ⇒ h=粒子间距 dx）；归一化 9/(40π) 3D、63/(478π) 2D | `MULTIPHASE/sph_multiphase.h:12`<br>`MULTIPHASE/sph_multiphase.h:13`<br>`MULTIPHASE/sph_multiphase.h:38` |
| 动量方程 | 压力梯度 f=-(V_i²+V_j²)p_ij∇W；粘性 (V_i²+V_j²)η(v_ij)∇W/r（仅一阶导） | `MULTIPHASE/pair_sph_taitwater_multiphase.cpp:89`<br>`MULTIPHASE/pair_sph_taitwater_multiphase.cpp:88` |
| 多相处理 | 分相物性（rho0/cv/c/η/k）；跨相压力混合平均；η、k 取调和平均 | `MULTIPHASE/pair_sph_taitwater_multiphase.cpp:84`<br>`MULTIPHASE/sph_multiphase.h:47` |
| 自由面 / 边界 | 冻结壁粒子（setforce 0）+ 壁上 Dirichlet ρ、e；周期边界；无镜像/虚粒子外推 | `examples/bubble_on_wall/bubble.lmp:56`<br>`examples/bubble_on_wall/bubble.lmp:71`<br>`examples/bubble_on_wall/bubble.lmp:17` |
| 表面张力 | 颜色函数梯度 + CSF 应力投影（无曲率拟合）：`sph/colorgradient` + `sph/surfacetension` | `MULTIPHASE/pair_sph_colorgradient.cpp:84`<br>`MULTIPHASE/pair_sph_surfacetension.cpp:18`<br>`MULTIPHASE/pair_sph_surfacetension.cpp:97` |
| 传热 | 对称导热通量 2α(T_i-T_j)∇W/(ρ_iρ_j)；类型对可选单侧 Dirichlet 温度（NULL 关键字） | `MULTIPHASE/pair_sph_heatconduction_phasechange.cpp:80`<br>`MULTIPHASE/pair_sph_heatconduction_phasechange.cpp:78` |
| 相变 | 能量概率蒸发 + 界面门 + 核权重质量捐出 + 潜热扣减 + 抽干转相 + restart 安全 RNG | `MULTIPHASE/fix_phase_change.cpp:156`<br>`MULTIPHASE/fix_phase_change.cpp:157`<br>`MULTIPHASE/fix_phase_change.cpp:197`<br>`MULTIPHASE/fix_phase_change.cpp:206`<br>`MULTIPHASE/fix_phase_change.cpp:219` |
| 时间步控制 | 固定 dt = min(声学 CFL, 粘性, 热扩散)；由输入脚本计算并 min()，无内核自适应 | `examples/bubble_on_wall/settimestep.lmp:11`<br>`examples/bubble_on_wall/settimestep.lmp:2`<br>`examples/bubble_on_wall/settimestep.lmp:7`<br>`examples/bubble_on_wall/settimestep.lmp:15` |
| 时间积分 | 委托 LAMMPS 内核 `fix sph`（显式）；本仓只加力/能量项与相变算子 | `examples/bubble_on_wall/bubble.lmp:74` |
| 并行策略 | MPI 域分解；自定义 forward/reverse comm（cg 3 分量、ρ、e、被抽走质量）；全邻居表 | `MULTIPHASE/pair_sph_colorgradient.cpp:140`<br>`MULTIPHASE/fix_phase_change.cpp:384`<br>`MULTIPHASE/pair_sph_colorgradient.cpp:39` |
| GPU | 无：`CUDA\|Kokkos\|GPU` 与 `_OPENMP` 命中数均为 0（3.4） | —（见 3.4 命中数） |
| IO / 后处理 | dump xyz/custom + `fix print` 输出 rg.dat；gnuplot/awk/maxima 后处理 | `examples/poiseuille/poiseuille.awk:3`<br>`examples/slub/infslab.mac:3` |

### 3.2 证据表（claim → file:line → 源码原文）

| # | claim | pointer | 源码原文（自动摘取） |
|---|---|---|---|
| 1 | Quintic-spline kernel W(q), support q<3, three piecewise branches | `MULTIPHASE/sph_multiphase.h:17` | `if (s < 1.0) return pow(3 - s, 5) - 6 * pow(2 - s, 5) + 15 * pow(1 - s, 5);` |
| 2 | Analytic radial derivative table dW/dq for the quintic spline | `MULTIPHASE/sph_multiphase.h:25` | `if (s < 1.0) return -50 * pow(s, 4) + 120 * pow(s, 3) - 120 * s;` |
| 3 | 3D normalisation constant norm3d = 9/(40 pi) | `MULTIPHASE/sph_multiphase.h:12` | `static constexpr double norm3d = 0.0716197243913529;` |
| 4 | 2D normalisation constant norm2d = 63/(478 pi) | `MULTIPHASE/sph_multiphase.h:13` | `static constexpr double norm2d = 0.04195297663091802;` |
| 5 | dw() evaluates d/ds at s = 3r/cut, i.e. cut = 3h and smoothing length h = cut/3 | `MULTIPHASE/sph_multiphase.h:38` | `return 3.0 * (dim == 3 ? norm3d : norm2d) * dkernel_poly(3.0 * r);` |
| 6 | Tait-type EOS p = B[(rho/rho0)^gamma - rbackground] (weakly compressible) | `MULTIPHASE/sph_multiphase.h:47` | `return B * (pow(rho / rho0, gamma) - rbackground);` |
| 7 | Colour function kept as a 3-component atom property with ghost exchange | `MULTIPHASE/sph_multiphase.h:55` | `error->all(FLERR, "SPH multiphase requires fix property/atom d2_cg 3 ghost yes");` |
| 8 | All multiphase pair styles require atom_style sph (rho / esph / rmass) | `MULTIPHASE/sph_multiphase.h:63` | `"SPH multiphase requires atom_style sph and fix property/atom rmass ghost yes");` |
| 9 | Pressure-gradient force f = -(V_i^2+V_j^2) p_ij gradW (volume-symmetric) | `MULTIPHASE/pair_sph_taitwater_multiphase.cpp:89` | `double fpair = -(Vi2 + Vj2) * pij * wfd;` |
| 10 | Viscosity term (V_i^2+V_j^2) eta (v_i-v_j) dW/dr, Morris-1997 form (first derivative only) | `MULTIPHASE/pair_sph_taitwater_multiphase.cpp:88` | `double fvisc = (Vi2 + Vj2) * viscosity[itype][jtype] * wfd;` |
| 11 | Multiphase pressure average p_ij = (rho_j p_i + rho_i p_j)/(rho_i+rho_j) | `MULTIPHASE/pair_sph_taitwater_multiphase.cpp:84` | `double pij = (rho[j] * pi + rho[i] * pj) / (rho[i] + rho[j]);` |
| 12 | B derived from the input sound speed so that dp/drho\|rho0 = c^2 | `MULTIPHASE/pair_sph_taitwater_multiphase.cpp:141` | `double B_one = soundspeed_one * soundspeed_one * rho0_one / gamma_one;` |
| 13 | Density by summation, initialised with the self term W(0)/h^dim | `MULTIPHASE/pair_sph_rhosum_multiphase.cpp:61` | `rho[i] = w(dim, 0.0) / (dim == 3 ? h * h * h : h * h);` |
| 14 | Density summation over kernel-weighted neighbours, then rho_i *= m_i | `MULTIPHASE/pair_sph_rhosum_multiphase.cpp:78` | `rho[i] += w(dim, sqrt(rsq) * ih) * (dim == 3 ? ih * ih * ih : ih * ih);` |
| 15 | Colour-function gradient sum_j alpha_ij/(n_j^2) n_i gradW | `MULTIPHASE/pair_sph_colorgradient.cpp:84` | `double dphi = -wfd * alpha[itype][jtype] / (sigmaj * sigmaj) * sigmai;` |
| 16 | Colour-gradient ghosts refreshed every step through a custom forward comm | `MULTIPHASE/pair_sph_colorgradient.cpp:140` | `int PairSPHColorGradient::pack_forward_comm(int n, int *list, double *buf, int, int *)` |
| 17 | Surface-tension stress as the projection of the colour gradient (CSF form, no curvature fit) | `MULTIPHASE/pair_sph_surfacetension.cpp:18` | `static void stress(int dim, const double *e, const double *g, double *s)` |
| 18 | Pairwise surface-tension force from the colour-gradient stress pair | `MULTIPHASE/pair_sph_surfacetension.cpp:97` | `for (int d = 0; d < 3; d++) fs[d] = (si[d] * Vi * Vi + sj[d] * Vj * Vj) * wfd;` |
| 19 | Heat conduction 2 alpha (T_i-T_j) gradW/(rho_i rho_j), symmetric energy exchange | `MULTIPHASE/pair_sph_heatconduction_phasechange.cpp:80` | `double deltaE = 2.0 * alpha[itype][jtype] * (Ti - Tj) * wfd / (rho[i] * rho[j]);` |
| 20 | Optional one-sided Dirichlet temperature on a type pair (NULL keyword) | `MULTIPHASE/pair_sph_heatconduction_phasechange.cpp:78` | `if (fixflag[itype][jtype] == itype && Ti < Tj) Ti = tc[itype][jtype];` |
| 21 | Phase change runs at pre-exchange: insertion happens before migration/comm | `MULTIPHASE/fix_phase_change.cpp:109` | `return PRE_EXCHANGE;` |
| 22 | Energy-based evaporation probability (e_i - cv Tc)/Hwv * dt * rate | `MULTIPHASE/fix_phase_change.cpp:156` | `double threshold = (e[i] - t2energy(Tc, cv[i])) / Hwv * update->dt * phase_change_rate;` |
| 23 | Evaporation only at an interface: a from-type particle must sit inside the cut-off | `MULTIPHASE/fix_phase_change.cpp:157` | `isphasechange = random->uniform() < threshold && isfromphasearound(i);` |
| 24 | Vapour mass is donated by liquid neighbours weighted by the kernel | `MULTIPHASE/fix_phase_change.cpp:186` | `wj[jj] = w(dim, sqrt(delx * delx + dely * dely + delz * delz) / cutoff);` |
| 25 | Kernel-weighted mass split; neighbour mass removed through a reverse comm | `MULTIPHASE/fix_phase_change.cpp:197` | `double dm = to_mass * wj[jj] / wtotal;` |
| 26 | Energy update subtracts the latent heat Hwv from the inserted mass | `MULTIPHASE/fix_phase_change.cpp:206` | `double enew = (rmass[i] * e[i] + denergy - to_mass * Hwv) / (rmass[i] + to_mass);` |
| 27 | Drained liquid particle can be converted into a vapour particle (convert CV) | `MULTIPHASE/fix_phase_change.cpp:219` | `if (convert_flag && type[i] == from_type && rmass[i] < 2.0 * to_mass) {` |
| 28 | Insertion ownership test incl. periodic images of the box faces (MPI-safe insertion) | `MULTIPHASE/fix_phase_change.cpp:257` | `bool FixPhaseChange::owns(double *coord, double *sublo, double *subhi)` |
| 29 | New particles created locally; natoms / atom map updated in pre_exchange | `MULTIPHASE/fix_phase_change.cpp:278` | `bool FixPhaseChange::insert_one_atom(double *coord, double *sublo, double *subhi)` |
| 30 | Restart-safe RNG state plus next reneighbour step written to the restart file | `MULTIPHASE/fix_phase_change.cpp:354` | `double list[3] = {(double) random->state(), (double) nfirst, (double) next_reneighbor};` |
| 31 | Donated mass accumulated across ranks with a reverse comm on atom->drho | `MULTIPHASE/fix_phase_change.cpp:384` | `int FixPhaseChange::pack_reverse_comm(int n, int first, double *buf)` |
| 32 | Viscous time-step limit dt = dx^2 rho/(8 eta) | `examples/bubble_on_wall/settimestep.lmp:2` | `variable dt_eta_d equal 1/8.0*${dx}*${dx}/${sph_eta_d}*${sph_rho_d}` |
| 33 | Thermal-diffusion limit dt = (1/4) sqrt(rho dx^3/(6 alpha)) | `examples/bubble_on_wall/settimestep.lmp:7` | `"variable dt_alpha equal 1/4.0*sqrt(${sph_rho_d}*${dx}^3/(6.0*${alpha_gw}))" &` |
| 34 | Acoustic CFL limit dt = (1/4) dx/c | `examples/bubble_on_wall/settimestep.lmp:11` | `variable dt_c_d equal 1/4.0*${dx}/${sph_c_d}` |
| 35 | Three limits reduced to one dt by a shell min() and injected as a LAMMPS variable | `examples/bubble_on_wall/settimestep.lmp:15` | `shell ./min.sh dt ${dt_eta_d} ${dt_eta_g} ${dt_eta_w} ${dt_alpha} ${dt_c_d} ${dt_c_g} ${dt_c_w}` |
| 36 | Time integration delegated to the LAMMPS core fix sph (explicit) | `examples/bubble_on_wall/bubble.lmp:74` | `fix                integrate_fix_full all sph` |
| 37 | Walls via boundary f plus frozen wall particles | `examples/bubble_on_wall/bubble.lmp:17` | `boundary          p f p` |
| 38 | Wall particles frozen with fix setforce (frozen boundary particles, no mirror particles) | `examples/bubble_on_wall/bubble.lmp:56` | `fix          wallim boundary setforce 0 0 0` |
| 39 | Wall density and energy prescribed (Dirichlet) instead of being integrated | `examples/bubble_on_wall/bubble.lmp:71` | `fix bm       all set 1 0 group boundary sph/rho v_rbm` |
| 40 | Full neighbour list requested for colour gradient / phase change (ghost access) | `MULTIPHASE/pair_sph_colorgradient.cpp:39` | `neighbor->add_request(this, NeighConst::REQ_FULL);` |
| 41 | Reverse-Poiseuille validation against the transient analytic series (Morris 1997) | `examples/poiseuille/poiseuille.awk:3` | `# Get an analytical solution for gravity driven Poiseulle flow` |
| 42 | Analytic series term used as the reference solution | `examples/poiseuille/poiseuille.awk:8` | `return 4*F*L^2 / (nu*pi^3*(2*n+1)^3) * sin(pi*y/L * (2*n+1)) * exp(- (2*n+1)^2 * pi^2 * nu / L^2` |
| 43 | Cylinder at Re=0.03 compared with digitised Morris-1997 FEM profiles | `examples/flow_around_cylinder/flow.gp:8` | `"morris-fig6-1" w l t "FEM, Path 1", \` |
| 44 | Two-material slab conduction against the VanSant closed form | `examples/slub/infslab.mac:30` | `eq_r: (t-t_l)/(t_r-t_l) = 1/(1+A)*(1+A*erf(Fos(x, alpha_r)));` |
| 45 | The analytic reference is generated by a CAS script, not typed by hand | `examples/slub/infslab.mac:3` | `VanSant, James H. Conduction heat transfer` |
| 46 | Symbolic kernel library with exact normalisations (quintic/bspline/Wendland/Lucy) | `examples/scripts/sph-kernel.mac:10` | `norm[3]: 9/(40*%pi),` |
| 47 | Alternative kernels available symbolically (wendland4/6, lucy, cubic, bspline) | `examples/scripts/sph-kernel.mac:59` | `elseif ktype='wendland4 then (` |
| 48 | Boiling-case parameters live in a plain input-script variable table | `examples/bubble_on_wall/vars.lmp:16` | `variable          sph_rho_g equal 1.0` |
| 49 | Two-particle cases generated from the symbolic kernel library (semi-analytic checks) | `examples/two_atoms/phase_change.mac:6` | `define_kernel(ndim, cutoff, 'quintic, 'w_scalar, 'dw_scalar)$` |

### 3.3 独立复算结果

| 恒等式 | 复算值 | 基准/期望 | 相对残差 | 怎么算的 |
|---|---|---|---|---|
| 核归一化 ∫W dV（3D） | 1.000000000000000 | 1 | 1.110e-16 | sympy 独立积分（scipy.quad，断点 1/3、2/3） |
| 核归一化 ∫W dA（2D） | 1.000000000000000 | 1 | 1.110e-16 | 同上（2πr 权重） |
| 源码 norm3d vs 9/(40π) | 0.071619724391353 | 0.071619724391353 | 0.000e+00 | sph_multiphase.h:12 的常数 vs 解析 9/(40π) |
| 源码 norm2d vs 63/(478π) | 0.041952976630918 | 0.041952976630918 | 0.000e+00 | sph_multiphase.h:13 的常数 vs 解析 63/(478π) |
| 导数表 dW/dq vs d/dq W | max\|Δ\| = 4.539e-13 | 0 | 4.539e-13 | sympy 化简残差 = 0（600 采样点） |
| EOS 斜率 dp/dρ(ρ0) vs c² | 2.250000000000 | 2.250000000000 | 1.974e-16 | 解析导 Bγρ^(γ-1)/ρ0^γ；B = c²ρ0/γ = 1.607143e-02（γ=1.4）；中心差分交叉校验残差 1.3e-11；p(ρ0) = 1.6e-02 |
| 相变能量账 ΔE vs −m·Hwv | -9.600000000000 | -9.600000000000 | 5.551e-16 | 随机 6 邻居样本，核权重质量分配 |

关键结构事实（复算得到，不是估计）：核函数支撑域 `cut = 3h`，因此**光滑长度 h = 粒子间距 dx**；算例里 `h = 3*dx` 是「截断半径」，不是光滑长度——这一点很容易被误读。按简单立方布点，`r ≤ 2h` 内邻居数 32、`r < 3h` 内邻居数 92。另注意 Tait EOS 带背景压强：`rbackground = 0` 时 `p(ρ0) = B ≠ 0`（数值见 3.3 的 EOS 行），所以「壁面直接设 ρ」等于把壁压钉在背景压强上——迁移到带自由面外压的算例时必须显式处理这个偏移。

### 3.4 否定声明（absence evidence）

| 否定声明 | 检索结果 | 命中位置（前 5） |
|---|---|---|
| 没有 GPU 后端（CUDA / Kokkos） | 0 命中 | — |
| 没有 OpenMP 线程并行 | 0 命中 | — |
| 没有 δ-SPH 密度扩散项 | 0 命中 | — |
| 没有 ISPH / 压力泊松投影 | 0 命中 | — |
| 没有 内核自适应时间步（CFL 控制器） | 0 命中 | — |
| 没有 人工粘性 / 张力不稳定处理 | 0 命中 | — |
| 没有 曲率（κ）表面张力 | 0 命中 | — |
| 没有 干湿（wet-dry）处理 | 0 命中 | — |
| 没有 插入时最小间距/重叠检测 | 0 命中 | — |
| CI 工作流（`.github/`） | 0 个文件 | 扫描 133 个源码/输入 blob |
| 测试框架（test/tests/spec） | 0 个 | — |
| LICENSE / COPYING 文件 | 0 个 | GitHub API `license = null` |

### 3.5 验证面盘点

| 算例 | 参照/基准 | 指针 | 量化程度 |
|---|---|---|---|
| 反 Poiseuille（Morris 1997 参数） | 瞬态解析级数（awk 现算） | `examples/poiseuille/poiseuille.awk:3` | 有解析解；无误差指标 |
| 圆柱绕流 Re=0.03 | Morris 1997 FEM 曲线数字化（morris-fig6-1/2） | `examples/flow_around_cylinder/flow.gp:8` | 有参照曲线；无误差指标 |
| 双材料板导热 | VanSant 闭式解（Maxima 生成 infslab.dat） | `examples/slub/infslab.mac:30` | 有闭式解；无误差指标 |
| 液滴/毛细波振荡 | 分辨率自收敛（nx=40/69/126）+ Hu&Adams 2006 文献 | `examples/two_atoms/phase_change.mac:6` | 只有目视对比，无收敛阶 |
| 两粒子算例 | 符号核库生成半解析检查输入 | `examples/two_atoms/phase_change.mac:6` | 无断言，人工判读 |

---

## ④ 结果图

![kernel verification](artifacts/oss-sph/lammps-sph-multiphase/fig1_kernel_and_verification.png)

**图注**：左 = 从 `MULTIPHASE/sph_multiphase.h` 正则抽出的核函数 `f(q)` 与导数表 `f'(q)`（q=3r/cut，支撑 q<3，三段分段）；中 = 本节 4 条恒等式的独立复算残差（对数坐标，红虚线 1e-12 容差）；右 = Tait EOS 与 ρ0 处切线（斜率应等于输入声速的平方，见 3.3）。

---

## ⑤ 注意点（坑 / 局限 / 适用范围）

1. **不是库，是补丁包**：必须 `cp MULTIPHASE/*.cpp *.h lammps/src/` 后重编 LAMMPS（README 给 make/CMake 两条路），依赖 LAMMPS 内核的 `fix sph`、`atom_style sph`、`fix property/atom ... ghost yes`。任何「直接 pip/链接」的迁移设想都不成立。
2. **文档-代码落差（K2-1 类）**：README 写 `fix phase_change (optional convert CV)`，代码里 `convert` 的语义是「被抽干（`rmass < 2*to_mass`）的液粒子**整颗**转成汽粒子并把 `cv` 改成 `convert_cv`」（`MULTIPHASE/fix_phase_change.cpp:219`），不是「改个 CV 值」；此外 `fix` 初始化要求 `narg >= 14`，参数顺序脆弱。
3. **相变是概率算子**：蒸发速率由 `(e_i − cv·Tc)/Hwv·dt·rate` 给出概率（`MULTIPHASE/fix_phase_change.cpp:156`），再乘界面门（邻域必须存在 `from_type`，`MULTIPHASE/fix_phase_change.cpp:157`）。这使结果**依赖随机种子**；仓库把 RNG 状态写进 restart（`MULTIPHASE/fix_phase_change.cpp:354`）说明作者自己也把可复现性当一等公民——迁移时必须保留这一条，否则统计量不可复现。
4. **插入不查重叠**：新汽粒子位置沿颜色梯度法向偏移 `dr`（默认 `0.5*dx`，失败按 0.75 衰减重试），但**没有最小间距/重叠检测**（源码检索无 exclusion/hmin 逻辑）。迁移到 SPH 相变时这是第一个要补的稳健性口。
5. **表面张力没有曲率**：颜色梯度 → CSF 应力投影，避免二阶导/曲率拟合（数值更稳），代价是接触角只能通过 `alpha_dw`/`alpha_gd` 系数「调」，`examples/contact_angle` 用两组 `alpha_dw`（1.5 vs 0.5）对比，属参数化润湿，不是 Young 定律实现。
6. **没有 δ-SPH 密度扩散、没有人工粘性开关、没有 ISPH/投影**（见 3.4 命中数）：因此张力不稳定/压力噪声只能靠 Tait 指数与 `rbackground` 兜（`rbackground=0` 时 p(ρ0)=0，可切到「负压截断」式处理）。
7. **验证是目视对照**：4 个算例都有解析/文献参照（Morris 1997 瞬态反 Poiseuille 级数、Morris 1997 圆柱 FEM 曲线数字化、VanSant 双材料板闭式解、Hu&Adams 毛细波），但**没有一条留了误差指标或容差**，也没有 CI/测试框架（workflow 文件 0，测试文件 0）。要拿来当平台基线，必须自己补「数值 vs 参照的 L2/∞ 误差 + 收敛阶」。
8. **并行**：MPI 域分解 + 自定义 forward/reverse comm（颜色梯度 3 分量、ρ、e、被抽走的 dmass），无 GPU/OpenMP/Kokkos（3.4 命中数 0）。`neighbor 0 bin` + `neigh_modify delay 0 every 1`：邻居表每步重建，属正确性优先的配置，性能不在作者目标内。

---

## ⑥ 误差判定（关键量 vs 基准，逐项）

本单的「基准」是**源码自身决定的解析恒等式**（可机器判定），不是物理实验数据。逐项列出：

| 关键量 | 相对误差 | 门槛 | <3% ? |
|---|---|---|---|
| ∫W dV = 1（3D，核归一化） | 1.110e-16 | 1e-12 | 是 |
| ∫W dA = 1（2D，核归一化） | 1.110e-16 | 1e-12 | 是 |
| dW/dq 表 vs 符号导数 | 4.539e-13 | 1e-12 | 是 |
| dp/dρ(ρ0) = c²（EOS 构造） | 1.974e-16 | 1e-12 | 是 |
| ΔE = −m·Hwv（相变能量账） | 5.551e-16 | 1e-12 | 是 |
| SPH 物理精度（Poiseuille/绕流/导热/毛细波） | 未复现（无 LAMMPS 构建） | 3% | 不判定（honest） |

**是否 <3%？** 上述 5 项恒等式残差全部在 `1e-12` 量级或更低（最大 4.539e-13），**全部远优于 3%**。

**物理精度（Poiseuille / 圆柱绕流 / 板导热 / 毛细波）本单未复现**：需要编译带 `PKG_SPH` 的 LAMMPS 并跑 12 个算例，超出本单范围；仓库本身也未给出任何数值误差指标（只有 gnuplot 目视图）。因此对「SPH 物理精度是否 <3%」**不作判定**（honest：不粉饰、不外推）。这也是本仓挂 `GAP-SPH-WCSPH-VALIDATION` 的原因：**基线在，度量缺**。

---

## 机器块

```
[OSS-INV]{"campaign": "osssph-w2", "repo": "slitvinov/lammps-sph-multiphase", "tier": 3, "pin": "7d420ddc8bf4bcf46664e9d7dcf2943d7dbffaca", "methods": ["WCSPH weakly-compressible core: quintic-spline kernel, support 3h (MULTIPHASE/sph_multiphase.h:17, MULTIPHASE/sph_multiphase.h:12)", "Tait EOS p=B[(rho/rho0)^gamma-rbackground] with B=c^2 rho0/gamma (MULTIPHASE/sph_multiphase.h:47, MULTIPHASE/pair_sph_taitwater_multiphase.cpp:141)", "density by kernel summation with self term (MULTIPHASE/pair_sph_rhosum_multiphase.cpp:61, MULTIPHASE/pair_sph_rhosum_multiphase.cpp:78)", "pressure + viscosity pair forces, volume-symmetric (V_i^2+V_j^2) form (MULTIPHASE/pair_sph_taitwater_multiphase.cpp:89, MULTIPHASE/pair_sph_taitwater_multiphase.cpp:88)", "colour-gradient surface tension, CSF stress projection, no curvature fit (MULTIPHASE/pair_sph_colorgradient.cpp:84, MULTIPHASE/pair_sph_surfacetension.cpp:18, MULTIPHASE/pair_sph_surfacetension.cpp:97)", "multi-material heat conduction with optional one-sided Dirichlet temperature (MULTIPHASE/pair_sph_heatconduction_phasechange.cpp:80, MULTIPHASE/pair_sph_heatconduction_phasechange.cpp:78)", "latent-heat phase change: energy-probability evaporation + kernel-weighted mass donation (MULTIPHASE/fix_phase_change.cpp:156, MULTIPHASE/fix_phase_change.cpp:197, MULTIPHASE/fix_phase_change.cpp:206)", "explicit integration by LAMMPS core fix sph plus script-level multi-criterion dt (examples/bubble_on_wall/bubble.lmp:74, examples/bubble_on_wall/settimestep.lmp:11, examples/bubble_on_wall/settimestep.lmp:2, examples/bubble_on_wall/settimestep.lmp:7)", "compute sph/de/atom exposes the per-atom energy change of the conduction operator (MULTIPHASE/pair_sph_heatconduction_phasechange.cpp:80)"], "boundary": "frozen boundary particles on 'boundary f' walls with prescribed rho and e (examples/bubble_on_wall/bubble.lmp:56, examples/bubble_on_wall/bubble.lmp:71); periodic elsewhere (examples/bubble_on_wall/bubble.lmp:17); insertion ownership covers periodic images (MULTIPHASE/fix_phase_change.cpp:257); no mirror particles, no boundary-integral or ghost-fluid extrapolation (curvature/extrapolation probes in the added sources: 0 hits)", "gpu": "none: no CUDA/Kokkos/OpenMP in the added sources (0 hits for CUDA|Kokkos|GPU, 0 hits for OpenMP); parallelism is LAMMPS MPI domain decomposition plus custom forward/reverse comm (MULTIPHASE/pair_sph_colorgradient.cpp:140, MULTIPHASE/fix_phase_change.cpp:384)", "validation": "4 quantitative case families, all manual gnuplot comparison: reverse Poiseuille vs transient analytic series (examples/poiseuille/poiseuille.awk:3), cylinder Re=0.03 vs digitised Morris-1997 FEM (examples/flow_around_cylinder/flow.gp:8), two-material slab conduction vs VanSant closed form (examples/slub/infslab.mac:30), capillary droplet oscillation across nx=40/69/126 (self-convergence only); no test framework and no CI (workflow files: 0, test files: 0); the repo stores no numeric error metric", "license": "none detected: GitHub license = null and no LICENSE/COPYING file in the tree (0 candidates) -> reference-only, no code transfer", "transferable": ["SPHSOL-lammpssphmultiphase-01", "SPHSOL-lammpssphmultiphase-02", "SPHSOL-lammpssphmultiphase-03", "SPHSOL-lammpssphmultiphase-04"]}[/OSS-INV]
```

```
[OSS-SOL]{"repo": "slitvinov/lammps-sph-multiphase", "id": "SPHSOL-lammpssphmultiphase-01", "kind": "adapt", "summary": "Colour-gradient surface tension without curvature estimation: interface colour function stored as a 3-component atom property (MULTIPHASE/sph_multiphase.h:55), per-type-pair interface coefficient alpha_ij (MULTIPHASE/pair_sph_colorgradient.cpp:84), force from the projected CSF stress tensor (MULTIPHASE/pair_sph_surfacetension.cpp:18, MULTIPHASE/pair_sph_surfacetension.cpp:97). Portable as an interface-only force term that needs first kernel derivatives only; licence = none, so clean-room re-implementation.", "gap": "GAP-SPH-MULTIPHASE"}[/OSS-SOL]
[OSS-SOL]{"repo": "slitvinov/lammps-sph-multiphase", "id": "SPHSOL-lammpssphmultiphase-02", "kind": "adapt", "summary": "Latent-heat phase-change operator with exact energy bookkeeping: evaporation probability from excess sensible energy above saturation (MULTIPHASE/fix_phase_change.cpp:156), interface gate (MULTIPHASE/fix_phase_change.cpp:157), donor mass taken kernel-weighted from liquid neighbours (MULTIPHASE/fix_phase_change.cpp:186, MULTIPHASE/fix_phase_change.cpp:197), energy update minus m*Hwv (MULTIPHASE/fix_phase_change.cpp:206), drained-liquid conversion (MULTIPHASE/fix_phase_change.cpp:219), MPI-safe insertion (MULTIPHASE/fix_phase_change.cpp:257, MULTIPHASE/fix_phase_change.cpp:278) and restart-safe RNG (MULTIPHASE/fix_phase_change.cpp:354). The delta-E = -m*Hwv identity is re-verified here to machine precision.", "gap": "GAP-SPH-MULTIPHASE"}[/OSS-SOL]
[OSS-SOL]{"repo": "slitvinov/lammps-sph-multiphase", "id": "SPHSOL-lammpssphmultiphase-03", "kind": "reference", "summary": "Validation baselines consumable as GAP anchors without touching the code: transient reverse-Poiseuille analytic series (examples/poiseuille/poiseuille.awk:8), digitised Morris-1997 FEM cylinder profiles (examples/flow_around_cylinder/flow.gp:8), VanSant two-material slab closed form generated by a CAS script (examples/slub/infslab.mac:3), and a symbolic kernel library with exact normalisations for quintic/bspline/Wendland/Lucy families (examples/scripts/sph-kernel.mac:10, examples/scripts/sph-kernel.mac:59). Reference-only: cite the values, do not copy code.", "gap": "GAP-SPH-WCSPH-VALIDATION"}[/OSS-SOL]
[OSS-SOL]{"repo": "slitvinov/lammps-sph-multiphase", "id": "SPHSOL-lammpssphmultiphase-04", "kind": "fit", "summary": "Reusable explicit stability envelope: three time-step limits computed in the input deck and reduced by min() -- acoustic CFL (examples/bubble_on_wall/settimestep.lmp:11), viscous (examples/bubble_on_wall/settimestep.lmp:2), thermal diffusion (examples/bubble_on_wall/settimestep.lmp:7) -> examples/bubble_on_wall/settimestep.lmp:15. Fits an explicit WCSPH solver's timestep policy directly and supplies the stability domain that must be quoted next to any convergence study; the formulas are textbook, the value is the combined envelope.", "gap": "GAP-SPH-WCSPH-VALIDATION"}[/OSS-SOL]
```

playbook key:value 块（校验器过闸版）见 ``docs/oss_inventory/OSS-INV-lammps-sph-multiphase.machine.txt``。