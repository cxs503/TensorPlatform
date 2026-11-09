"""Evidence table (part B): section 8, feature mapping, machine block."""

# ---------------------------------------------------------------------------
# Section 8: Top-3 absorbable items + risks
# ---------------------------------------------------------------------------
CLAIMS_8 = [
    (
        "**Top1｜自由能 vtable + 「泛函→μ/σ→力」可插拔链路（→ TensorLBM 多相模块）**："
        "`free_energy.h` 用一张纯函数指针表（fed/mu/stress/str_symm/str_anti/hvector/htensor "
        "+ 三个向量化版本）统一 10 种自由能，碰撞核只依赖 `str_symm_v`，即**换物理不改求解器**。"
        "可在 TensorLBM 复刻「单接口 + 多后端」（Python 侧用 Protocol/ABC），并把 "
        "`symmetric.c` 的 μ、P_ab、σ、ξ 解析式当参考实现写进单测。"
        "**风险**：BSD-3 需保留声明；其「平衡态直接吃 symm」的推导绑定 D3Q19 MRT 谱系，"
        "移植到我们的 D3Q27/多松弛时必须重推离散平衡与力项。",
        [
            ("src/free_energy.h", "struct fe_vt_s {"),
            ("src/free_energy.h", "Symmetric stress"),
            ("src/collision.c", "fe->func->str_symm_v(fe, index0, symm);"),
        ],
    ),
    (
        "**Top2｜面通量式守恒 Cahn-Hilliard + 守恒漂移补偿（→ TensorLBM 相场/两组分输运）**："
        "「每面只算一次通量、相邻单元共用」+ 两级守恒修正开关 + 可选随机通量，是把 CH 跑长"
        "时间不漂质量的实用配方，单文件规模小、可直接对照实现，并以「万步质量漂移 < 1e-10」"
        "作为我们的验收线。**风险**：其面通量实现假设 1 层 halo 与 Lees–Edwards 剪切坐标，"
        "移植时需去掉 LE 耦合（`lees_edw_index` / `lees_edw_ic_to_buff`）。",
        [
            ("src/phi_cahn_hilliard.c", "Conservation is ensured by face-flux uniqueness."),
            ("src/phi_cahn_hilliard.h", "PHI_CONSERVE_GLOBAL_SUBTRACT"),
            ("src/phi_cahn_hilliard.c", "lees_edw_index(le, icm1, jc, kc)"),
        ],
    ),
    (
        "**Top3｜数值回归面 + 案例组织（→ TensorPlatform 证据/CI 面）**："
        "`tests/regression/<lattice>/` 按格子分目录、每 case 自包含输入与期望输出，用浮点按域"
        "容差比对（不是字节比较）；单元测试按 feature 一个 `test_*.c`；CI 按格子分 job 并行。"
        "**可吸收的是「物理量级容差比较器」与「目录即证据单元」的组织**。"
        "**风险**：其容差是「相对本仓上次输出」的回归容差，**不是物理基准**；我们仍必须外接"
        "解析解/实验基准，不能把「回归绿」当精度达标（>15% 判据不受其保护）。",
        [
            ("tests/test.sh", "regression"),
            ("tests/awk-fp-diff.sh", "fp"),
            ("tests/regression/d3q19-short/serial-actv-s01.inp", "Active nematic 2-d smoke test"),
            (".github/workflows/regression.yml", "make -C tests d2q9"),
        ],
    ),
]

# ---------------------------------------------------------------------------
# Section 7: mapping onto feature_ledger slugs
# (slug, transfer, markdown text, [(path, needle), ...])
# ---------------------------------------------------------------------------
MAPPING = [
    (
        "GAP-LBM-MCMP-POISEUILLE", "reference",
        "**不直接命中**：本仓无 Shan-Chen 互作用势，两组分分层 Poiseuille 用不上现成实现；"
        "可参考的是替代路径「压强张量进平衡态 + −φ∇μ 体力」，以及 `hydro_correct_momentum()` "
        "的动量修正（保证体力不破坏整体动量守恒）。分层是否稳定还需要界面分辨率与伪流控制，"
        "这一条本仓不提供验收方法。",
        [
            ("src/symmetric.c", "P_ab = [1/2 A phi^2"),
            ("src/phi_grad_mu.c", "phi_grad_mu_force_kernel(kernel_3d_t k3d, hydro_t * hydro,"),
            ("src/hydro.c", "hydro_correct_momentum"),
        ],
    ),
    (
        "MCMP 高密度比", "adapt",
        "**本仓无高密度比能力**（ρ 为旁观量）。可用的是「自由能→μ→力」链路与 σ/ξ 解析标度；"
        "高密度比所需的 ρ_liq/ρ_gas 与平衡态重构须我们在 TensorLBM 自建（变密度平衡态），"
        "并以两相密度比与 spurious current 双指标验收。",
        [
            ("src/fe_ternary.c", "FE_RHO"),
            ("src/symmetric.c", "*xi = sqrt(-2.0*fe->param->kappa/fe->param->a);"),
        ],
    ),
    (
        "自由面（free surface）", "reference",
        "**本仓无自由面模型**。可借用的组织是「map 固体/空单元状态 + 面通量无穿透条件」"
        "（`advflux_cs_no_normal_flux`），把它改造为「气相=空单元」的自由面近似。",
        [
            ("src/cahn_hilliard.c", "if (map) advflux_cs_no_normal_flux(ch->flux, map);"),
            ("src/map.h", "enum map_status {MAP_FLUID"),
        ],
    ),
    (
        "相场 Allen-Cahn", "adapt",
        "**本仓只有守恒 Cahn-Hilliard（二阶、双井 φ⁴）与三元 Semprebon 泛函，无 Allen-Cahn"
        "（非守恒）**。但 `fe_vt_t` 的接口形态正好允许新增一个 AC 后端而不动求解器——"
        "这是我们该抄的扩展点；AC 方程本身（含润湿/接触角项）需我们自研。",
        [
            ("src/free_energy.h", "typedef int (* fe_mu_ft)(fe_t * fe, int index, double * mu);"),
            ("src/fe_ternary.c", "Semprebon et al, Phys Rev E 93, 033305 (2016)"),
        ],
    ),
    (
        "GPU 融合 kernel", "reference",
        "本仓 kernel 粒度为「一步一核」，每步 `tdpDeviceSynchronize`，融合空间明确"
        "（collision + grad-mu + propagation 可合为一个核）。可参考其 `kernel_3d_t` 循环域描述、"
        "`__restrict__` 标注与 launch 参数计算方式。",
        [
            ("target/target_cuda.c", "Target thread model: CUDA."),
            ("src/propagation.c", "double * __restrict__ fprime;"),
            ("src/kernel_3d.h", "kernel_3d_launch_param"),
        ],
    ),
    (
        "分布式多卡", "adapt",
        "MPI 域分解 + halo post/wait 分离（通信隐藏）+ AOSOA 合并 + CUDA-aware MPI 构建矩阵；"
        "胶体跨进程用字节级 isend/irecv + 分段规约——可直接对照我们的粒子-流体耦合通信设计。",
        [
            ("src/lb_data.c", "int lb_halo_wait(lb_t * lb, lb_halo_t * h) {"),
            ("src/memory.h", "pseudo_iv(index)"),
            ("config/epcc-cirrus-nvcc-openmpi.mk", "MPI"),
        ],
    ),
    (
        "稀疏/多孔", "reference",
        "任意固体几何由 per-site `map` 状态表达（fluid/wall/colloid links），壁面用 BBL 并支持 "
        "Navier 滑移 → 「多孔介质 = map 打洞 + 滑移壁」是现成范式，适合拿来搭稀疏/多孔验证面。",
        [
            ("src/wall.c", "__global__ void wall_bbl_slip_kernel(wall_t * wall, lb_t * lb, map_t * map) {"),
            ("src/wall.c", "int wall_bbl(wall_t * wall) {"),
        ],
    ),
    (
        "案例组织", "fits",
        "输入文件（JSON，`util_cJSON`）+ 目录即案例（`tests/regression/<lattice>/<case>`），"
        "每 case 自包含参数与期望输出；构建配置外置到 `config/*.mk`。可直接映射到我们"
        "「一个案例目录 = 一份可复现证据」的做法。",
        [
            ("tests/regression/d3q19-short/serial-actv-s01.inp", "Active nematic 2-d smoke test"),
            ("src/util_cJSON.h", "cJSON"),
        ],
    ),
    (
        "测试与验证面", "fits",
        "单元测试按 feature 一个 `test_*.c`，回归比对用浮点按域容差脚本 "
        "`tests/awk-fp-diff.sh`（非字节比较）。可复用的是「物理量级容差比较器」与测试粒度；"
        "**不可复用的是其基准强度**（回归容差 ≠ 物理精度，仍需外接解析解/实验）。",
        [
            ("tests/awk-fp-diff.sh", "fp"),
            ("tests/test-diff.sh", "diff"),
        ],
    ),
    (
        "CI", "reference",
        "`regression.yml` 按格子（d2q9/d3q15/d3q19/d3q27）分 job 并行构建+跑回归，`codecov.yml` "
        "出覆盖率，`codeql-analysis.yml` 做静态扫描；构建配置用 `cp config/github-gcc.mk "
        "config.mk` 注入。这种「矩阵分格 + 覆盖率 + 静态分析」三件套可照搬进我们仓的 CI 骨架。",
        [
            (".github/workflows/regression.yml", "make -C tests d2q9"),
            (".github/workflows/codecov.yml", "make unit"),
        ],
    ),
    (
        "NEW: 涨落 LBM（ghost-mode 噪声源）", "reference",
        "本仓实现了「水动力模态确定性演化 + ghost 模态受热噪声」的涨落格子 Boltzmann"
        "（噪声方差由 1/τ 与 kT 决定），并可向 Cahn-Hilliard 注入随机通量。我们缺口账本里"
        "没有这一项，建议立 NEW：热涨落驱动的界面毛细波/Brownian 验证面。",
        [
            ("src/collision.c", "var = lb_fluctuations_var_eta(1.0/rtau[iv], _cp.kt);"),
            ("src/collision.c", "lb_fluctuations_var_ghost(lb->param->rna,"),
        ],
    ),
    (
        "NEW: 粒子-流体近场润滑修正（lubrication）", "adapt",
        "BBL 之外再叠解析润滑力（球-球径向/切向、粒子-壁面亏空核），是低分辨率下把阻力算对的"
        "关键；其「截断距离 rch + 解析近场修正 + 汇总到刚体力/力矩」的结构可复用到我们的"
        "颗粒/冰区接触耦合。",
        [
            ("src/lubrication.h", "LUBRICATION_SS_FNORM"),
            ("src/bbl.c", "static int bbl_wall_lubrication_account(bbl_t * bbl, wall_t * wall,"),
        ],
    ),
]

# ---------------------------------------------------------------------------
# Machine blocks (parsed by the archive guard-line; emitted verbatim)
# ---------------------------------------------------------------------------
MACHINE = {
    "repo": "ludwig-cf/ludwig",
    "lang": "C (C99; CUDA/HIP device code via target layer; no Fortran)",
    "license": "BSD-3-Clause (University of Edinburgh, 1998-2018)",
    "methods": (
        "lattice=D2Q9;D3Q15;D3Q19;D3Q27 | "
        "collision=MRT(default, per-mode M10);BGK;TRT(shear/bulk two-rate);"
        "two-distribution MRT2(binary, legacy) | "
        "forcing=Guo velocity-shift + (2-1/tau)(uF+Fu) stress correction (only path); "
        "no RK, no shifted-velocity"
    ),
    "multiphase": (
        "free-energy van der Waals phi^4+kappa (src/symmetric.c:297,316,327-358), "
        "analytic sigma/xi (src/symmetric.c:255,271), "
        "ternary Semprebon PRE93,033305 (src/fe_ternary.c:5,23), "
        "surfactant Frumkin (src/surfactant.h:33), "
        "conserved Cahn-Hilliard face-flux (src/phi_cahn_hilliard.c:388); "
        "NO Shan-Chen/SCMP/MCMP, NO free-surface, NO density ratio (rho spectator)"
    ),
    "boundary": (
        "link bounce-back Ladd for walls/colloids (src/wall.c:971, src/bbl.c:221); "
        "Navier slip (src/wall.c:1129); Dirichlet inflow-rhou/outflow-rhou, no Zou-He "
        "(src/lb_bc_inflow_rhou.c:478); wetting solid gradients (src/gradient_2d_ternary_solid.c:79); "
        "lubrication correction (src/lubrication.h:39)"
    ),
    "gpu": (
        "CUDA/HIP via target/ layer (target/target.h, target/target_cuda.c); "
        "one-kernel-per-step with tdpDeviceSynchronize; AoS/SoA/AoSoA address models "
        "(src/memory.h); MPI Px*Py*Pz halo post/wait split (src/lb_data.c:754,1318,1431)"
    ),
    "summary": (
        "Free-energy LBM for complex fluids (colloids/LC/electro/surfactant) in C with "
        "CUDA/HIP+MPI; no Shan-Chen, no density ratio, no free surface, no AD/codegen. "
        "Worth absorbing: free-energy vtable chain, face-flux conserved Cahn-Hilliard, "
        "and the tolerance-based numerical regression surface."
    ),
}

SOLS = [
    ("GAP-LBM-MCMP-POISEUILLE", "free-energy-pressure-tensor-body-force", "reference",
     "以 F=1/2A phi^2+1/4B phi^4+1/2 kappa(grad phi)^2 的自由能路线替代 Shan-Chen："
     "压强张量 P_ab 直接进碰撞平衡应力，界面力以 -phi grad mu 加到 hydro->force，"
     "再用 Guo 速度移位入碰撞；两组分分层 Poiseuille 可照此搭（非伪势路线）。",
     "src/symmetric.c:327, src/phi_grad_mu.c:52, src/collision.c:371",
     "sigma=sqrt(-8 kappa A^3/(9 B^2)), xi=sqrt(-2 kappa/A); 重力/体力经 hydro->force; "
     "单位: lattice units, cs2=1/3"),
    ("MCMP 高密度比", "no-density-ratio-available", "adapt",
     "本仓无密度比机制（rho 为旁观量，三元模型注释明示），只可借用其自由能->mu->力链路与"
     "sigma/xi 标度关系来反解界面参数；高密度比需自建变密度平衡态。",
     "src/fe_ternary.c:23, src/symmetric.c:271",
     "rho0 为物理参数 (src/physics.c)，phi 不映射到 rho_liq/rho_gas"),
    ("自由面（free surface）", "map-based-empty-site", "reference",
     "无自由面模型；可借鉴 map 状态 + 面通量无穿透（advflux_cs_no_normal_flux）把气相实现为"
     "空单元并在界面处施加质量/动量条件。",
     "src/cahn_hilliard.c:203, src/map.c",
     "map status = MAP_FLUID/MAP_SOLID; 面通量在固体面强制零法向"),
    ("相场 Allen-Cahn", "cahn-hilliard-only", "adapt",
     "只有守恒 Cahn-Hilliard（面通量 M grad mu，含守恒漂移补偿 PHI_CONSERVE_*）+ 三元 "
     "Semprebon 泛函；Allen-Cahn 非守恒方程需自研，但 fe_vt_t 提供了加后端的扩展点。",
     "src/free_energy.h:47, src/phi_cahn_hilliard.c:388, src/phi_cahn_hilliard.h:29",
     "mobility M 为常数参数; phi in [-1,1]; 守恒修正开关 PHI_CONSERVE_DEFAULT/COMPENSATED/GLOBAL_SUBTRACT"),
    ("GPU 融合 kernel", "one-kernel-per-step", "reference",
     "每步一个 __global__ 核并按 tdpDeviceSynchronize 串行：collision、propagation、每个力项"
     "各一核，融合空间明确；kernel_3d_t 提供统一循环域，__restrict__ 标注用于合并。",
     "src/collision.c:199, src/propagation.c:153, target/target_cuda.c:32",
     "nblk/ntpb 由 kernel_3d_launch_param 按 kiterations 计算; 默认 threads/block 见 tdp_get_max_threads"),
    ("分布式多卡", "mpi-halo-post-wait-split", "adapt",
     "MPI 三维域分解，halo 交换拆成 post(非阻塞 isend/irecv + 预建通信图) 与 wait 两段，"
     "中间可插本地计算隐藏延迟；胶体跨进程字节级 MPI_Irecv/Isend + 分段规约。",
     "src/lb_data.c:754, src/lb_data.c:1318, src/lb_data.c:1431, src/colloids_halo.c:446",
     "nhalo=1 (src/coords.c:66); 通信图 lb_graph_halo_send/recv_create"),
    ("稀疏/多孔", "map-solid-with-navier-slip", "reference",
     "任意固体几何 = per-site map 状态（fluid/wall/colloid links）+ link bounce-back，"
     "可选 Navier 滑移壁；多孔介质即 map 打洞 + 滑移壁。",
     "src/wall.c:971, src/wall.c:1129",
     "滑移长度 wall->param->slip.*; map 由 map_init/map_options 从输入文件读"),
    ("案例组织", "json-input-case-directory", "fits",
     "输入参数用 JSON（util_cJSON）读入，案例目录自包含输入+期望输出，构建配置外置 "
     "config/*.mk；目录即证据单元。",
     "src/util_cJSON.h, tests/regression/d3q19-short/Makefile",
     "cs/config: {Px,Py,Pz} 域分解; 每 case 一个 Makefile target"),
    ("测试与验证面", "fp-domain-tolerance-diff", "fits",
     "浮点按域容差比对（tests/awk-fp-diff.sh 由 test-diff.sh 驱动），单元测试按 feature "
     "一个 test_*.c；容差为回归容差而非物理基准。",
     "tests/awk-fp-diff.sh, tests/test.sh, tests/unit/test_be.c",
     "容差以相对/绝对阈值参数化; 回归输出含 per-quantity 参考值文件"),
    ("CI", "per-lattice-matrix-ci", "reference",
     "regression.yml 按格子分 job 并行构建+跑回归，codecov 出覆盖率，codeql 静态扫描；"
     "构建配置用 config/github-gcc.mk 覆盖。",
     ".github/workflows/regression.yml, .github/workflows/codecov.yml",
     "cp config/github-gcc.mk config.mk; sed 切换 D3Q19->D2Q9 等目标格子"),
    ("NEW: 涨落 LBM（ghost-mode 噪声源）", "fluctuating-lbm-ghost-modes", "reference",
     "水动力模态确定性演化、ghost 模态按 1/tau 与 kT 给方差受热噪声；可向 CH 注入随机通量。"
     "提供「热涨落驱动的界面毛细波/Brownian」验证面。",
     "src/collision.c:473, src/collision.c:1640, src/phi_cahn_hilliard.c:103",
     "kt 为涨落强度参数; 噪声只在 MAP_FLUID 站点生成以保证可复现"),
    ("NEW: 粒子-流体近场润滑修正（lubrication）", "analytic-lubrication-correction", "adapt",
     "BBL 之外叠解析近场润滑力（球-球径向/切向、粒子-壁面亏空），截断距离 rch 控制启用范围，"
     "力/力矩并入刚体总量。低分辨率下把阻力算对的关键。",
     "src/lubrication.h:39, src/bbl.c:1515",
     "rch 截断（lubrication_rch_set）; eta 取自流体动力粘度; 力单位 lattice"),
]