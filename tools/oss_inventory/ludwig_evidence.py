"""Evidence table (part A) for the OSS-INVENTORY deep dive of ludwig-cf/ludwig.

Anti-fabrication contract
-------------------------
Every technical statement carries a code pointer ``file:line``.  The line
number is NOT typed by hand: each reference is stored as
``(relative_path, needle)`` and resolved at render time by locating the first
line of that file containing ``needle``.  A needle that cannot be found
aborts the build, so stale or invented pointers cannot reach the report.

Needles are short literal fragments copied out of the source, chosen to avoid
characters that need escaping so they are cheap to re-verify.
"""

REPO = "ludwig-cf/ludwig"
MISSION_ID = 8287
SNAPSHOT_SHA256 = "6e216ef8f4c2f5654dc11b9c6b4f3570f97cc5e277193922b48cfaf0cd72e198"

# {section_id: [(markdown_text, [(path, needle), ...]), ...]}
CLAIMS = {}

CLAIMS["1"] = [
    (
        "主语言为 **C**（C99；`src/*.c` 的文件数与行数由脚本统计后注入第五节图表），"
        "**不是 Fortran**——与派发单上的先验描述不同，按代码本体更正。"
        "`src` 侧全部用 `__host__` / `__device__` / `__global__` 宏书写，"
        "由 `target/target.h` 在 `__NVCC__` / `__HIPCC__` / 其它之间编译期分派，"
        "因此 x86 序列、CUDA、HIP 三个后端共用同一份源码；文档为 LaTeX（`docs/*.tex`）。",
        [
            ("README.md", "lattice Boltzmann code"),
            ("target/target.h", "target_hip.h"),
            ("target/target.h", "target_cuda.h"),
            ("target/target.h", "target_x86.h"),
            ("src/kernel_3d_v.h", "for_simd_v"),
        ],
    ),
    (
        "许可证 = **BSD-3-Clause**（Copyright 1998-2018 The University of Edinburgh；"
        "三条款：保留版权声明、保留条件与免责声明、不得用 Edinburgh 名义背书）。"
        "GitHub API 的 license 字段给出 `NOASSERTION`，只因 LICENSE 正文是未加 SPDX 头"
        "的 BSD-3 文本，实质仍是标准三条款 BSD。"
        "**对我们的可用性判断：可直接用于闭源/商业衍生**，唯一义务是随分发包保留三条款"
        "文本与免责声明、且不得暗示 Edinburgh 背书；无 copyleft 传染、无专利授权条款"
        "（亦即没有专利保护伞——照抄算法思路时需自行评估第三方专利）。",
        [
            ("LICENSE", "Copyright (c) 1998-2018, The University of Edinburgh"),
            ("LICENSE", "Redistribution and use in source and binary forms"),
            ("LICENSE", "Neither the name of the The University of Edinburgh"),
        ],
    ),
    (
        "活跃度与快照指纹：`version.h` 记 **0.24.0**；仓库元数据"
        "（api.github.com，2026-11-09 取）pushed_at=2026-11-05、stars=74、size=195480 kB；"
        "`CHANGES.md` 有版本流水。本次深盘以 master 源码 tarball "
        "sha256=`6e216ef8f4c2f5654dc11b9c6b4f3570f97cc5e277193922b48cfaf0cd72e198` "
        "作为可复现指纹（commit SHA 因 api.github.com 403 限流未取到，如实记录，用树指纹替代）。",
        [
            ("version.h", "define LUDWIG_MINOR_VERSION"),
            ("CHANGES.md", "version 0.24.0"),
        ],
    ),
    (
        "定位一句话：**面向复杂流体（胶体颗粒、液晶、电解质、表面活性剂）的格子 Boltzmann "
        "求解器**；核心是「自由能泛函 + 序参量场 + 守恒 Cahn-Hilliard 输运 + LBM 流体」"
        "四件套，粒子用 link bounce-back 双向耦合，附带 MPI 域分解与 CUDA/HIP 加速。",
        [
            ("src/free_energy.h", "The 'abstract' free energy interface."),
            ("docs/intro.tex", "Ludwig"),
        ],
    ),
]

CLAIMS["2"] = [
    (
        "**格子谱系**：D2Q9 / D3Q15 / D3Q19 / D3Q27 四套，各一个 `lb_d*q*.c`；"
        "速度集 `cv`、权重 `wv`、归一化因子 `na` 与**模态变换矩阵** `ma`（f→mode）、"
        "`mi`（mode→f）全部数据化。D3Q19 模态行依次为 ρ、c_x、c_y、c_z、s_xx、s_xy、s_xz、"
        "s_yy、s_yz、s_zz，其后是 ghost 模态；矩阵在构造期正交化并归一化"
        "（`wip += w*a*a` → `na = 1/wip`）。碰撞核只认模态，与格子维度解耦。",
        [
            ("src/lb_d3q19.c", "model->ma[ 1][p] = cx;"),
            ("src/lb_d3q19.c", "model->ma[ 4][p] = sxx;"),
            ("src/lb_d3q19.c", "wip += model->wv[ia]*model->ma[p][ia]*model->ma[p][ia];"),
            ("src/lb_d2q9.c", "NVEL_D2Q9"),
            ("src/lb_d3q15.c", "NVEL_D3Q15"),
            ("src/lb_d3q27.c", "NVEL_D3Q27"),
        ],
    ),
    (
        "**碰撞算子：只有一套通用 MRT，按松弛率配置切成三种谱系**。枚举 "
        "`lb_relaxation_enum` 含 `LB_RELAXATION_M10`（默认：各水动力模态独立松弛率，经典 MRT）、"
        "`LB_RELAXATION_BGK`（全部同一松弛率）、`LB_RELAXATION_TRT`（两组松弛率：shear 与 "
        "bulk 分开，等价于按对称性分组的 TRT）。站点核 `lb_collision_mrt1_site()` 流程："
        "f→mode → 取 ρ,u,应力 → 松弛 → 回写模态 → mode→f。"
        "**没有 KBC / entropic / cumulant 碰撞核，也没有 RR 谱系**。",
        [
            ("src/lb_data_options.h", "LB_RELAXATION_M10"),
            ("src/lb_data_options.h", "LB_RELAXATION_BGK"),
            ("src/lb_data_options.h", "LB_RELAXATION_TRT"),
            ("src/collision.c", "int lb_collision_relaxation_set(lb_t * lb, lb_relaxation_enum_t nrelax)"),
            ("src/collision.c", "void lb_collision_mrt1_site(lb_t * lb, hydro_t * hydro, map_t * map,"),
            ("src/collision.c", "if (lb->nrelax == LB_RELAXATION_TRT)"),
        ],
    ),
    (
        "**力项加法 = Guo 型速度移位（本仓唯一路径）**：先把体力折进速度 "
        "`u = (j + F/2)/ρ`，松弛非平衡应力时再加 `(2 - 1/τ)(u_a F_b + F_a u_b)`"
        "（Guo 力项的 MRT 形式），最后把动量模态回写为 `mode[1+ia] += F_a`。"
        "**没有** RK（reduced-kinetic）力项，也**没有** shifted-velocity 精确速度变体。",
        [
            ("src/collision.c", "u[ia][iv] = rrho[iv]*(u[ia][iv] + 0.5*force[ia][iv]);"),
            ("src/collision.c", "s[ia][ib][iv] += (2.0 - rtau[iv])"),
            ("src/collision.c", "*(u[ia][iv]*force[ib][iv] + force[ia][iv]*u[ib][iv]);"),
            ("src/collision.c", "mode[(1 + ia)*NSIMDVL+iv] += force[ia][iv];"),
        ],
    ),
    (
        "**平衡态吃进自由能压强张量**：当自由能提供对称应力时，平衡应力取 "
        "`seq = ρ u u + symm`（`fe->use_stress_relaxation` 开关），即「两相界面应力直接进"
        "平衡态」而非事后加力——这是自由能 LBM 的关键做法。非水动力（ghost）模态各自趋向"
        "零松弛，可叠加受热噪声 `ghat` 与应力噪声 `shat`（涨落 LBM，方差由 1/τ 与 kT 给出），"
        "且噪声只在流体站点生成以保证回归可复现。",
        [
            ("src/collision.c", "seq[ia][ib][iv] = rho[iv]*u[ia][iv]*u[ib][iv] + symm[ia][ib][iv];"),
            ("src/collision.c", "if (fe && fe->use_stress_relaxation)"),
            ("src/collision.c", "- rtau_ghost[m][iv]*(mode[m*NSIMDVL+iv] - 0.0) + ghat[m][iv];"),
            ("src/collision.c", "var = lb_fluctuations_var_eta(1.0/rtau[iv], _cp.kt);"),
        ],
    ),
    (
        "**双分布（binary）分支仍在**：`lb_collision_binary()` → `lb_collision_mrt2_site()` "
        "保留「每站点两条分布」的旧路径，注释明确说明只为对称自由能保留（与单分布路径并存，"
        "属兼容层而非独立模型）。",
        [
            ("src/collision.c", "int lb_collision_binary(lb_t * lb, hydro_t * hydro, noise_t * noise,"),
            ("src/collision.c", "void lb_collision_mrt2_site(lb_t * lb, hydro_t * hydro, fe_symm_t * fe,"),
        ],
    ),
    (
        "**向量化组织**：站点函数内部用 `for_simd_v(iv, NSIMDVL)` 展开显式短向量循环，"
        "外层 `kernel_3d_v_t` 提供三维循环域与掩码；同一份代码在 CPU 被编译器向量化、"
        "在 GPU 上退化为线程内串行。",
        [
            ("src/kernel_3d_v.h", "for_simd_v"),
            ("src/collision.c", "for_simd_v(iv, NSIMDVL) includeSite[iv] = 1;"),
            ("src/kernel_3d_v.h", "kernel_3d_v_t"),
        ],
    ),
]

CLAIMS["3"] = [
    (
        "**壁面：link bounce-back（BBL）为主力**。`wall_bbl()` 扫描 map 中标记为 wall 的"
        "连接做反弹；`wall_bbl_slip_kernel()` 追加 Navier 滑移（滑移长度由 `wall->param->slip` "
        "控制）；初始化时 `wall_set_wall_distributions()` 把壁面侧分布铺成平衡态。",
        [
            ("src/wall.c", "int wall_bbl(wall_t * wall) {"),
            ("src/wall.c", "__global__ void wall_bbl_kernel(wall_t * wall, lb_t * lb, map_t * map) {"),
            ("src/wall.c", "__global__ void wall_bbl_slip_kernel(wall_t * wall, lb_t * lb, map_t * map) {"),
            ("src/wall.c", "int wall_set_wall_distributions(wall_t * wall) {"),
            ("src/wall.c", "if (wall->param->slip.active) kernel = wall_bbl_slip_kernel;"),
        ],
    ),
    (
        "**胶体/曲面边界：Ladd 式 link bounce-back + 动量累积 + 润滑修正（没有 IBM）**。"
        "`bounce_back_on_links()` 三趟：pass0 清站点分布，pass1 对每条 crossing link 做 "
        "`f_i += dm_a`（动量交换，dm 由粒子表面速度算得，椭球用 Legendre 投影/切向基），"
        "pass2 把累积动量与力矩送回刚体（`bbl_6x6_gaussian_elimination` 解惯性张量方程）。"
        "`lubrication_*` 追加球-球近场润滑力，`bbl_wall_lubrication_account()` 处理粒子-壁面"
        "润滑亏空。**曲率处理：没有插值型 IBM**（无 delta 核、无 Eulerian–Lagrangian 力扩散），"
        "几何精度靠 link 细分 + 解析润滑修正获得——这是与我们 IBM 路线最大的方法论差异。",
        [
            ("src/bbl.c", "int bounce_back_on_links(bbl_t * bbl, lb_t * lb, wall_t * wall,"),
            ("src/bbl.c", "static int bbl_pass1(bbl_t * bbl, lb_t * lb, colloids_info_t * cinfo) {"),
            ("src/bbl.c", "fdist += dm_a;"),
            ("src/bbl.c", "int bbl_6x6_gaussian_elimination(double a[6][6], double xb[6]) {"),
            ("src/lubrication.h", "int lubrication_single(lubr_t * obj, double a1, double a2,"),
            ("src/bbl.c", "static int bbl_wall_lubrication_account(bbl_t * bbl, wall_t * wall,"),
        ],
    ),
    (
        "**进出口：Dirichlet 型「给定 ρ 或 u」开边界，不是 Zou-He**。入口 `lb_bc_inflow_rhou` "
        "把入口平面速度设为 u0，出口 `lb_bc_outflow_rhou` 把出口平面密度设为 ρ0，再由 "
        "`impose()` 以目标 ρ、u 重建分布；工厂 `lb_bc_open_rt()` 从输入文件读类型与数值。"
        "对 `src` 全量检索 `zou` 无命中 → 确认**没有 Zou-He，也没有非平衡反弹（NEE）格式**。",
        [
            ("src/lb_bc_inflow_rhou.c", "hydro_u_set(hydro, index0, inflow->options.u0);"),
            ("src/lb_bc_inflow_rhou.c", "int lb_bc_inflow_rhou_impose(lb_bc_inflow_rhou_t * inflow,"),
            ("src/lb_bc_outflow_rhou.c", "hydro_rho_set(hydro, index0, outflow->options.rho0);"),
            ("src/lb_bc_open_rt.c", "int lb_bc_open_rt(pe_t * pe, rt_t * rt, cs_t * cs, lb_t * lb,"),
        ],
    ),
    (
        "**湿边界 / 固-液接触角**：由「solid gradient」族在固体面用 wetting 参数修正序参量"
        "法向梯度实现——对称自由能用 `C`，三元体系用 `h1..h3` 并可直接反算接触角"
        "（`gradient_2d_ternary_solid.c` 注释给出 Eq.24-26 的映射，`fe_ternary_wetting_angles()` "
        "输出角度）。序参量另有 inflow-fixed / outflow-free 边界。"
        "（`angle_cosine.c` 是胶体间余弦弯曲势，**不是**壁面接触角模型。）",
        [
            ("src/gradient_2d_ternary_solid.c", "Set wetting parameters for phi and psi. See Eq. 24-26 Semprebon."),
            ("src/gradient_3d_27pt_solid.c", "solid"),
            ("src/symmetric.h", "Surface wetting parameter C (uniform)"),
            ("src/fe_ternary.c", "fe_ternary_wetting_angles(fe, theta);"),
            ("src/phi_bc_inflow_fixed.c", "phi_bc_inflow"),
            ("src/phi_bc_outflow_free.c", "phi_bc_outflow"),
        ],
    ),
]

CLAIMS["4"] = [
    (
        "**总判定：本仓没有 Shan-Chen（SCMP/MCMP）伪势模型，也没有 free-surface（VOF 类）"
        "模型**；多相走**自由能（van der Waals 型）路线 + 守恒序参量 Cahn-Hilliard**，"
        "并有三组分/表面活性剂扩展。`fe_id_enum` 列举全部自由能后端：NULL / SYMMETRIC / "
        "BRAZOVSKII / SURFACTANT / POLAR / LC / ELECTRO / ELECTRO_SYMMETRIC / LC_DROPLET / "
        "TERNARY，**其中没有任何互作用势（伪势）后端**。",
        [
            ("src/free_energy.h", "enum fe_id_enum {FE_NULL,"),
            ("src/free_energy.h", "FE_SYMMETRIC"),
            ("src/free_energy.h", "FE_TERNARY"),
            ("src/free_energy.h", "FE_SURFACTANT"),
        ],
    ),
    (
        "**互作用势形式（对称二元，主力）**：泛函 "
        "`F[φ] = (1/2) A φ² + (1/4) B φ⁴ + (1/2) κ (grad φ)²`（A<0 双井），化学势 "
        "`μ = Aφ + Bφ³ − κ ∇²φ`，热力学压强张量 "
        "`P_ab = [1/2 Aφ² + 3/4 Bφ⁴ − κφ∇²φ − 1/2 κ(∇φ)²] δ_ab + κ (∂_a φ)(∂_b φ)`"
        "（另有向量化版本供 SIMD/GPU）。参数集 = `a, b, kappa, c`（c 为壁面湿润参数）。"
        "**解析标度内置**：σ = sqrt(−8κA³/9B²)、界面厚度 ξ = sqrt(−2κ/A)——可直接由"
        "「给定 σ 与 ξ 反解 (A,B,κ)」，对我们参数标定极有用。",
        [
            ("src/symmetric.c", "(1/4) B"),
            ("src/symmetric.c", "*mu = fe->param->a*phi + fe->param->b*phi*phi*phi - fe->param->kappa*delsq;"),
            ("src/symmetric.c", "P_ab = [1/2 A phi^2"),
            ("src/symmetric.c", "s[ia][ib][iv] = p0*d[ia][ib] + kappa*grad[ia][iv]*grad[ib][iv];"),
            ("src/symmetric.c", "*sigma = sqrt(-8.0*kappa*a*a*a/(9.0*b*b));"),
            ("src/symmetric.c", "*xi = sqrt(-2.0*fe->param->kappa/fe->param->a);"),
            ("src/symmetric.h", "Bulk quadratic term"),
            ("src/symmetric.h", "Interfacial penalty term"),
        ],
    ),
    (
        "**力项加法：两套并存可切换**。①**化学势梯度法** `f_a = −φ ∂_a μ`（`phi_grad_mu.c`："
        "流体核 + 固体核 + 可选「外部化学势梯度」源项，并带回读修正 "
        "`phi_grad_mu_correction_kernel` 以保离散动量守恒）；②**热力学应力散度法** "
        "`∂_b P_ab`（`phi_force_stress.c::pth_stress_compute`，先全场存 3×3 应力再取散度，"
        "便于分离对称/反对称部分——液晶分支必需）。两者最终都写进 `hydro->force`，"
        "再经第二节的 Guo 型力项进入碰撞。",
        [
            ("src/phi_grad_mu.c", "Various implementations of the computation of the local body force"),
            ("src/phi_grad_mu.c", "phi_grad_mu_correction_kernel(kernel_3d_t k3d,"),
            ("src/phi_grad_mu.c", "int phi_grad_mu_fluid(cs_t * cs, field_t * phi, fe_t * fe,"),
            ("src/phi_force_stress.c", "int pth_stress_compute(pth_t * pth, fe_t * fe) {"),
            ("src/phi_force_stress.c", "if (fe->use_stress_relaxation)"),
        ],
    ),
    (
        "**序参量输运 = 面通量式守恒 Cahn-Hilliard**：逐面算扩散通量 "
        "`f_face −= M(μ_next − μ_here)`（六面各一次），叠加 `advection_x/...` 的对流通量，"
        "用 forward step 更新 φ。「面通量唯一」保证离散守恒；另给两种守恒漂移修正 "
        "`PHI_CONSERVE_COMPENSATED_SUM` 与 `PHI_CONSERVE_GLOBAL_SUBTRACT`，以及 "
        "`phi_ch_random_flux()` 的 Landau 型随机通量（涨落 CH）。旧差分实现 `ch_solver()` "
        "仍在（`cahn_hilliard.c`），作为兼容路径。",
        [
            ("src/phi_cahn_hilliard.c", "phi_ch_flux_mu1_kernel(kernel_3d_t k3d,"),
            ("src/phi_cahn_hilliard.c", "flux->fe[addr_rank0(flux->nsite, index0)] -= mobility*(mu1 - mu0);"),
            ("src/phi_cahn_hilliard.c", "advection_x(pch->flux, hydro, phi);"),
            ("src/phi_cahn_hilliard.c", "Conservation is ensured by face-flux uniqueness."),
            ("src/phi_cahn_hilliard.h", "PHI_CONSERVE_COMPENSATED_SUM"),
            ("src/phi_cahn_hilliard.h", "PHI_CONSERVE_GLOBAL_SUBTRACT"),
            ("src/phi_cahn_hilliard.c", "static int phi_ch_random_flux(phi_ch_t * pch, noise_t * noise)"),
            ("src/cahn_hilliard.c", "int ch_solver(ch_t * ch, fe_t * fe, field_t * phi, hydro_t * hydro,"),
        ],
    ),
    (
        "**三元 / 表面活性剂（Semprebon et al., PRE 93, 033305 (2016)）**：三场 φ（组分）、"
        "ψ（表面活性剂浓度）、ρ；参数 `alpha, kappa1..3, h1..h3`；**ρ 在代码注释里被明确标为 "
        "\"spectacting\"（旁观者）**，即三元模型仍是等密度近似下的组分演化，不做真实密度对比。"
        "润湿由 `h1..h3` 给出并可反算接触角。另有 `surfactant.c`（Frumkin/Langmuir 等温线"
        "吸附型自由能）、`brazovskii.c`（微乳液）、液晶分支 `blue_phase.c`(Landau–de Gennes) "
        "+ `leslie_ericksen.c`、电动力学 `fe_electro.c` + `nernst_planck.c` + `psi*.c`"
        "（Poisson：SOR 或 PETSc）。",
        [
            ("src/fe_ternary.c", "Semprebon et al, Phys Rev E 93, 033305 (2016)"),
            ("src/fe_ternary.c", "FE_RHO"),
            ("src/fe_ternary.h", "Ternary kappa"),
            ("src/fe_ternary.c", "int fe_ternary_angles(fe_ternary_t * fe, double * theta) {"),
            ("src/surfactant.h", "Frumpkin isotherm"),
            ("src/fe_ternary.h", "Uniform surface free energy h1"),
            ("src/psi.c", "symmetric electrolyte"),
        ],
    ),
    (
        "**密度比能力：无（等密度 / 近 Boussinesq 近似）**。ρ 是 LBM 的零阶矩、φ 是独立平流场；"
        "**没有任何把 φ 映射成两相本相密度 ρ_liq/ρ_gas 的机制**，也没有 MCMP 的 G_αβ "
        "互作用强度参数。三元模型的 `FE_RHO` 更是显式「旁观」。→ 对我们的「MCMP 高密度比」"
        "功能点，本仓只能作方法论参考，**不能照搬**。",
        [
            ("src/fe_ternary.c", "FE_RHO"),
            ("src/physics.c", "rho0"),
        ],
    ),
    (
        "**伪流（spurious current）处理：没有专门的抑制或后处理机制**。文档只在 "
        "`docs/binary.tex` 指出「单一松弛率的自由能 LBM 会引入伪各向异性项」，其对策是算法层的"
        "多松弛率 MRT + 正确平衡应力（见第二节），而非事后修正。这是我们自己要补的一课："
        "需自建「伪流幅度」验收量（本仓不提供）。",
        [
            ("docs/binary.tex", "spurious anisotropic terms which are introduced by a single relaxation"),
        ],
    ),
]

CLAIMS["5"] = [
    (
        "**并行 = MPI 三维域分解 + SIMD/GPU 目标层；没有 OpenMP 或线程池层**。分区为 "
        "Px×Py×Pz 笛卡尔，坐标系统一用 `(n + 2*nhalo)` 的 stride 做索引，halo 宽度默认 1。",
        [
            ("src/coords.c", "cs->param->str[X] = cs->param->str[Y]*(cs->param->nlocal[Y] + 2*cs->param->nhalo);"),
            ("src/coords.c", "cs->param->nhalo = 1;"),
        ],
    ),
    (
        "**数据布局：三种寻址模型编译期可选**（`ADDR_AOS` 默认 / `ADDR_SOA` / `ADDR_AOSOA`），"
        "由 `addr_rank*` 一族宏在 `forward_addr_*`（AOS）与 `reverse_addr_*`（SOA）之间切换；"
        "AOSOA 用 `pseudo_iv(index) = index mod NVBLOCK` 把站点索引伪装成块内向量索引，使 "
        "SIMD 内层循环同时得到**内存合并**（同一 p 的相邻站点连续）与按 p 展开的寄存器友好性。"
        "**AOS 默认是历史的保守选择**（兼容存量核），GPU 用 SOA/AOSOA 提高合并度。",
        [
            ("src/memory.h", "#elif defined ADDR_SOA"),
            ("src/memory.h", "#if defined ADDR_SOA || defined ADDR_AOSOA"),
            ("src/memory.h", "#define DATA_MODEL DATA_MODEL_AOSOA"),
            ("src/memory.h", "pseudo_iv(index)"),
            ("src/memory.h", "int reverse_addr_rank1_assert(int line, const char * file,"),
        ],
    ),
    (
        "**分布数组**：`lb->f` 单块平铺，地址宏 `LB_ADDR(nsite, ndist, NVEL, index, n, p)`；"
        "传播用 f/fprime 双缓冲 pull 方式（`fprime[i] = f[i − c_p]`），随后交换指针，"
        "因此 streaming 不需要额外临时数组。",
        [
            ("src/lb_data.h", "#define LB_ADDR(nsites, ndist, nvel, index, n, p)"),
            ("src/propagation.c", "fprime[LB_ADDR(lbp.nsite, lbp.ndist, NVEL, index0 + iv, n, p)]"),
            ("src/propagation.c", "int lb_model_swapf(lb_t * lb) {"),
        ],
    ),
    (
        "**通信与隐藏**：`lb_halo()` = `lb_halo_post()` + `lb_halo_wait()` —— 非阻塞 "
        "isend/irecv 成对提交、通信图初始化时预建（`lb_graph_halo_send_create` / "
        "`lb_graph_halo_recv_create`），**post 与 wait 分离，调用方可在其间插入本地计算以"
        "隐藏通信延迟**；场量（φ、μ、u、应力）走 `field_halo_swap()`；胶体跨进程用字节级 "
        "`MPI_Irecv/Isend`（`colloids_halo.c`）+ 分段规约（`colloid_sums.c`）；全局量用 "
        "`MPI_Allreduce`（如活动粒子计数）。",
        [
            ("src/lb_data.c", "int lb_halo(lb_t * lb) {"),
            ("src/lb_data.c", "int lb_halo_post(lb_t * lb, lb_halo_t * h) {"),
            ("src/lb_data.c", "int lb_halo_wait(lb_t * lb, lb_halo_t * h) {"),
            ("src/lb_data.c", "int lb_graph_halo_send_create(const lb_t * lb, lb_halo_t * h)"),
            ("src/field.c", "int field_halo_swap(field_t * obj, field_halo_enum_t flag) {"),
            ("src/colloids_halo.c", "MPI_Irecv(halo->recv, n, MPI_BYTE, pforw, tagb_, comm, req);"),
            ("src/colloid_sums.c", "MPI_Irecv(sum->recv + nf, nb, MPI_DOUBLE, pback, tagf_,"),
            ("src/bbl.c", "MPI_Allreduce(&nactive_local, &nactive, 1, MPI_INT, MPI_SUM, comm);"),
        ],
    ),
    (
        "**GPU**：单一 `target/` 抽象层把 `tdpMalloc/tdpMemcpy/tdpLaunchKernel/"
        "tdpDeviceSynchronize` 映射到 CUDA 或 HIP；`__host__`/`__device__` 宏让同一站点函数"
        "既作 CPU 循环体又作 GPU 线程体；`config/` 备有 nvcc / hipcc / nvcc+OpenMPI"
        "（CUDA-aware MPI）/ Archer2 等构建矩阵，含 `tdpDeviceGetP2PAttribute`"
        "（GPUDirect P2P）能力探测。**kernel 粒度 = 「一步一核」**：碰撞、传播、每个力项"
        "各是一个 kernel，由 `tdpDeviceSynchronize` 串联——**融合机会明确地留给我们**。",
        [
            ("target/target.h", "tdpError_t tdpDeviceGetP2PAttribute(int * value,"),
            ("target/target_cuda.c", "Target thread model: CUDA."),
            ("target/Makefile", "TARGETSRC = target_cuda.c"),
            ("target/Makefile", "TARGETSRC = target_hip.c"),
            ("src/collision.c", "tdpLaunchKernel(lb_collision_mrt1, nblk, ntpb, 0, 0,"),
        ],
    ),
]

CLAIMS["6"] = [
    (
        "**可微 / 代码生成：两者都没有。** 无 autograd、无伴随、无 JAX/Torch 接口（仓内唯一 "
        "Python 文件是 `docs/figures/symmetric.py`，只画示意图）；无符号推导、无 kernel "
        "生成器：模态矩阵、梯度模板、碰撞核全部手写 C，「生成」只在构建期发生（用宏裁剪"
        "格子/后端，`config/*.mk` 选编译器）。对我们的含义：**无法从 ludwig 借「自动微分」"
        "能力**，但它的「泛函 → μ → 压强张量 → 力」手写链路正是我们做 AD 时的**解析式对照源**，"
        "`fe_vt_t` 的 vtable 也是「换物理不改求解器」的现成接口范式。",
        [
            ("docs/figures/symmetric.py", "import"),
            ("src/free_energy.h", "Chemical stress (total)"),
            ("src/free_energy.h", "Symmetric part"),
        ],
    ),
]