"""证据指针表 —— ebinan92/Fingering_dynamics 盘点（OSS-INV）。

每条 = (相对路径, 源码子串, 允许出现次数, 断言 id, 说明)。渲染器把每条子串在**真实源码树**
里定位成 `path:line`；定位失败或命中次数超出预期 = 硬失败（反臆造门）。子串必须逐字出现在
源文件中——因此本文件不能凭记忆书写，任何改动都要跑 `--check-only` 复核。

被盘点仓无许可证（`license=None`），本表只登记"事实在哪个文件的哪一行"，不搬运代码文本。
"""

from __future__ import annotations

# --------------------------------------------------------------------------- #
# 1. 总体定位 / 元数据（README）
# --------------------------------------------------------------------------- #
META = [
    ("README.md", "two phase lattice boltzmann with zou-he boundary and half way bounce-back",
     "readme_tagline", "自述：两相 LBM + Zou-He 边界 + 半程反弹"),
    ("README.md", "fingering_per_gpu.py", "readme_gpu_name",
     "README 声称的 GPU 文件名，与实际 tree 中的 fingering_periodic_gpu.py 不一致"),
    ("README.md", "valitation.py            # wettability validation", "readme_typo_validation",
     "README 目录树把 validation.py 写成 valitation.py（拼写错误，实际文件名正确）"),
    ("README.md", "10.1103/PhysRevE.93.023103", "readme_pre_ref",
     "三相版本所依据的 PRE 文献只出现在 README，三相脚本本身未引用（见 sp_3phase_ref）"),
]

# --------------------------------------------------------------------------- #
# 2. 参数块 / 无量纲化换算链（fingering.py）
# --------------------------------------------------------------------------- #
PARAMS = [
    ("lattice_boltzmann/fingering.py", "self.w[i] = 4 / 9", "d2q9_table",
     "D2Q9 权重表在 Compute.__init__ 里逐点硬编码"),
    ("lattice_boltzmann/fingering.py", "H = 380  # lattice dimensions",
     "grid_380", "算例网格 380×380，写死在模块顶层"),
    ("lattice_boltzmann/fingering.py", "MAX_T = 1000",
     "max_t", "时间步上限 1000（fingering_periodic 为 4000）"),
    ("lattice_boltzmann/fingering.py", "psi_wall = -1.0  # wettability on block and wall",
     "psi_wall_main", "壁面虚拟序参量 ψ_wall=−1.0（强非润湿侧）"),
    ("lattice_boltzmann/fingering.py", "Pe = 400  # Peclet numbernet",
     "pe", "Pe=400 进入迁移率 γ 的标定"),
    ("lattice_boltzmann/fingering.py", "M = 20.0  # Eta non_newtonian / Eta newtonian",
     "M_ratio", "M=20，注释称「非牛顿/牛顿」，实际用作两相黏度比"),
    ("lattice_boltzmann/fingering.py", "tau = 1 / (3.0 - math.sqrt(3))  # relaxation time",
     "tau_magic", "τ=1/(3−√3)≈0.7887（「魔数」，等价 ν0≈0.09623）"),
    ("lattice_boltzmann/fingering.py", "n_non = 1.0  # rho0 power-law parameter",
     "n_non_decl", "幂律指数 n_non 的唯一定义处（全文件仅此一次出现 → 未被使用）"),
    ("lattice_boltzmann/fingering.py", "R_sigma = 0.045  # physical interfacial tension",
     "r_sigma", "物理界面张力 0.045 N/m"),
    ("lattice_boltzmann/fingering.py", "C_t = (v0 / R_Nu) * (C_W ** 2)  # conversion time step",
     "c_t", "时间换算因子"),
    ("lattice_boltzmann/fingering.py", "Eta_n = Eta * C_t / (C_rho * (C_W ** 2))",
     "eta_n", "格子黏度（代数上恒等于 v0，见报告第六节）"),
    ("lattice_boltzmann/fingering.py", "sigma = R_sigma * (C_t ** 2) / (C_rho * (C_W ** 3))",
     "sigma_dimless", "无量纲界面张力"),
    ("lattice_boltzmann/fingering.py", "u0 = Ca * sigma / Eta_n  # inlet velocity",
     "u0", "入口速度由 Ca 反推"),
    ("lattice_boltzmann/fingering.py", "kappa = 0.75 * xi * sigma",
     "kappa_relation", "κ = 0.75·ξ·σ（等价 σ=4κ/(3ξ)，见报告第六节复核）"),
    ("lattice_boltzmann/fingering.py", "a = - 2.0 * kappa / (xi ** 2)",
     "a_relation", "a = −2κ/ξ²（决定 kink 解 ψ=tanh(x/ξ)）"),
    ("lattice_boltzmann/fingering.py", "gamma = u0 * H / ((-a * Pe) * (tau - 0.5))",
     "gamma_mobility", "Cahn–Hilliard 迁移率 γ 的标定式"),
]

# --------------------------------------------------------------------------- #
# 3. 求解器骨架：离散速度表、双分布、平衡态、化学势、黏度插值
# --------------------------------------------------------------------------- #
SOLVER = [
    ("lattice_boltzmann/fingering.py", "def getMu(self):",
     "get_mu", "化学势 μ 的离散表达 μ=aψ(1−ψ²)−κ∇²ψ 所在函数"),
    ("lattice_boltzmann/fingering.py",
     "mu = a * self.psi[self.mask] * (ones - np.power(self.psi[self.mask], 2)) - kappa * self.nabla_psi2[self.mask]",
     "mu_formula", "自由能（双阱）化学势本体"),
    ("lattice_boltzmann/fingering.py", "return 1 / 3 * self.rho + self.psi[self.mask] * self.mu",
     "pressure_law", "压力关系 p = ρ/3 + ψμ（状态方程含界面项）"),
    ("lattice_boltzmann/fingering.py", "return np.sum(self.f, axis=0)[self.mask]  # macroscopic density",
     "rho_from_f", "ρ 由 f 求和得到（f 承载流体力学）"),
    ("lattice_boltzmann/fingering.py", "self.psi[self.mask] = np.sum(self.g, axis=0)[self.mask]",
     "psi_from_g", "序参量 ψ 由 g 求和得到（g 承载相场/守恒型 Cahn–Hilliard）"),
    ("lattice_boltzmann/fingering.py", "mix_v = np.divide(2 * v11 * v2",
     "mix_viscosity", "两相黏度调和插值 ν(ψ)"),
    ("lattice_boltzmann/fingering.py", "mix_tau = 3 * mix_v + 0.5",
     "mix_tau", "τ_f = 3ν(ψ)+1/2"),
    ("lattice_boltzmann/fingering.py", "f = self.f[i][self.mask] - 1.0 / self.mix_tau",
     "relax_f", "f 用变松弛时间（相依赖黏度）"),
    ("lattice_boltzmann/fingering.py", "g = self.g[i][self.mask] - 1.0 / tau",
     "relax_g", "g 用固定 τ（序参量输运）"),
    ("lattice_boltzmann/fingering.py", "f += -20 * temp[1:-1, 1:-1]",
     "lap_center", "拉普拉斯模板中心权重 −20（标准 D2Q9 各向同性）"),
    ("lattice_boltzmann/fingering.py", "return f / 6",
     "lap_norm", "拉普拉斯归一化 1/6"),
    ("lattice_boltzmann/fingering.py", "self.top_bottom_wall = np.full((1, W + 2), psi_wall)",
     "psi_wall_halo", "壁面 ψ 以虚拟节点方式进入差分（润湿/壁面相互作用的实现点）"),
    ("lattice_boltzmann/fingering.py", "np.random.randint(0, 1, size=(H, W))",
     "randint_bug", "randint(0,1) 恒为 0 → 初始密度扰动退化"),
    ("lattice_boltzmann/fingering.py", "x = sympy.symbols('x')",
     "sympy_import", "只为符号占位引入 sympy（运行期无用）"),
]

# --------------------------------------------------------------------------- #
# 4. 边界条件
# --------------------------------------------------------------------------- #
BC = [
    ("lattice_boltzmann/fingering.py", "def zou_he_boundary_inlet(self):",
     "zh_in", "Zou-He 速度入口"),
    ("lattice_boltzmann/fingering.py", "rho_inlet = 1 / (1 - ux)",
     "zh_in_rho", "入口密度由 Zou-He 关系 ρ=1/(1−u_x)(...) 求得"),
    ("lattice_boltzmann/fingering.py", "psi_x[:, 0] * mu[:, 0] / 2",
     "zh_in_flux", "入口 Zou-He 内嵌 ∇ψ·μ/2 化学势通量修正"),
    ("lattice_boltzmann/fingering.py", "psi_in = 1.0 - (",
     "zh_in_psi", "入口序参量用量 1−Σ已知 g 反推"),
    ("lattice_boltzmann/fingering.py", "self.w[i] * psi_in[1:-1] / (self.w[1] + self.w[5] + self.w[8])#2",
     "zh_in_psi_split", "ψ 的未知方向按权重 (w1+w5+w8) 归一化分配（三方向同式，取第 2 处）"),
    ("lattice_boltzmann/fingering.py", "def zou_he_boundary_outlet(self):",
     "zh_out", "Zou-He 出口（同为速度边界 u_x=u0，不是压力出口）"),
    ("lattice_boltzmann/fingering.py", "rho_outlet = 1 / (1 + ux)",
     "zh_out_rho", "出口密度关系（含 u_x=+u0）"),
    ("lattice_boltzmann/fingering.py", "self.f[2][0, -1] = self.f[4][0, -1]",
     "zh_out_corner", "出口只显式处理两个角点，与入口四角处理不对称"),
    ("lattice_boltzmann/fingering.py", "def stream(f, g):",
     "stream", "迁移用 np.roll 全周期环绕 + 边界覆写"),
    ("lattice_boltzmann/fingering.py", "def bottom_top_wall(f_behind, g_behind, f, g):",
     "top_bottom_bb", "上下壁面半程反弹（无滑移、无润湿修正）"),
    ("lattice_boltzmann/fingering.py", "bb.halfway_bounceback_rec(corner_list",
     "bb_rect", "矩形障碍：按角点表做半程反弹"),
    ("lattice_boltzmann/bounce_back.py", "https://www.math.nyu.edu/~billbao/report930.pdf",
     "bb_ref", "半程反弹实现所依据的讲义（Bao 的 LBM 笔记）"),
    ("lattice_boltzmann/bounce_back.py", "def halfway_bounceback_rec(self, corner_list, f_behind, g_behind, f, g):",
     "bb_rec_def", "矩形障碍角点 → 8 类壁面/角点布尔掩码"),
    ("lattice_boltzmann/bounce_back.py", "def halfway_bounceback_circle(self, side_list, concave_list, convex_list, f_behind, g_behind, f, g):",
     "bb_circle_def", "圆/椭圆障碍：凹/凸角点分类的反弹表"),
    ("lattice_boltzmann/bounce_back.py", "def left_boundary(self, f_behind, g_behind, f, g, hole):",
     "bb_hole", "左边界带孔（局部改为反弹）"),
]

# --------------------------------------------------------------------------- #
# 5. 几何 / 验证 / GPU / 谱方法
# --------------------------------------------------------------------------- #
GEOM = [
    ("lattice_boltzmann/create_block.py", "def getCicleblock(self, center, radius):",
     "blk_circle", "圆形障碍栅格化"),
    ("lattice_boltzmann/create_block.py", "def getRectangleblock(self, bottom_left, top_right):",
     "blk_rect", "矩形障碍栅格化"),
    ("lattice_boltzmann/create_block.py", "def getEllipseblock(self, center, axes, angle):",
     "blk_ellipse", "椭圆（含旋转角）障碍栅格化"),
    ("lattice_boltzmann/create_block.py", "block_psi = binary_fill_holes(block).astype(int)#3",
     "blk_fill", "用 binary_fill_holes 补内部空洞（setblock 路径，第 3 处）"),
    ("lattice_boltzmann/create_block.py", "def setblock(self, rect_corner_list):",
     "blk_set", "多块叠加 → 全局布尔掩码 + 角点表"),
    ("lattice_boltzmann/fingering.py", "block_psi_all, corner_list = cr.setblock(rect_corner_list)",
     "blk_call", "算例入口：生成障碍掩码与角点表"),
]

VALIDATION = [
    ("lattice_boltzmann/validation.py", "'''濡れ性の確認コード'''",
     "val_doc", "自述：本文件是「润湿性确认代码」"),
    ("lattice_boltzmann/validation.py", "H = 200  # lattice dimensions",
     "val_grid", "验证算例 200×250"),
    ("lattice_boltzmann/validation.py", "psi_wall = 0.0",
     "val_psi_wall", "验证算例用中性壁 ψ_wall=0"),
    ("lattice_boltzmann/validation.py", "Theta = np.pi / 4  # contact angle",
     "val_theta", "接触角 π/4 定义（全仓唯一命中 → 从未参与任何计算/断言）"),
    ("lattice_boltzmann/validation.py", "circle = create_circle(W, 36).T",
     "val_droplet", "初始液滴：半径 36 的圆"),
    ("lattice_boltzmann/validation.py", "halfway_bounceback(f_behind, g_behind, cm.f, cm.g)",
     "val_bb", "壁面反弹（润湿的全部实现）"),
    ("lattice_boltzmann/validation.py", "plt.pcolor(x, y, cm.psi, label=",
     "val_plot", "输出仅为 ψ 图 → 无定量接触角提取"),
    ("branch_width.py", "def getbranch_width(ahead_center, behind_center, behind_diff):",
     "bw_width", "指进分支宽度测量（后处理）"),
    ("branch_width.py", "import cv2",
     "bw_cv2", "依赖 OpenCV 做视频帧差分/轮廓"),
]

GPU = [
    ("lattice_boltzmann/fingering_periodic_gpu.py", "'''GPU version'''",
     "gpu_doc", "自述 GPU 版"),
    ("lattice_boltzmann/fingering_periodic_gpu.py", "import cupy as cp",
     "gpu_cupy", "CuPy 作为 numpy 的替换层"),
    ("lattice_boltzmann/fingering_periodic_gpu.py", "self.e = cp.array([cp.array([0, 0]) for i in range(9)])",
     "gpu_device_array", "设备端数组化（78 处 cp. 调用）"),
    ("lattice_boltzmann/fingering_periodic_gpu.py", "psi_wall = 1.0  # wettability on block and wall",
     "gpu_psi_wall", "GPU 算例改 ψ_wall=+1.0（与 CPU 版 −1.0 相反）"),
    ("lattice_boltzmann/fingering_periodic_gpu.py", "import cv2",
     "gpu_cv2", "GPU 版仍保留 CPU 侧的 OpenCV 依赖"),
    ("lattice_boltzmann/fingering_periodic.py", "psi_wall = -0.5  # wettability on block and wall",
     "per_psi_wall", "周期版 ψ_wall=−0.5（中性偏润湿）"),
    ("lattice_boltzmann/fingering_periodic.py", "u0 = Ca * sigma / (rho0 * v0)  # inlet velocity",
     "per_u0", "周期版入口速度公式与 fingering.py 不同（用 v0 而非 Eta_n）"),
]

SPECTRAL = [
    ("spectrum_method/main.py", "https://aip.scitation.org/doi/10.1063/1.866726",
     "sp_ref", "谱方法版指进所依据的文献（Phys. Fluids A 1988, Tryggvason & Aref）"),
    ("spectrum_method/main.py", "M = 256  # number of transverse collocation points",
     "sp_grid", "谱方法 256×128 配置点"),
    ("spectrum_method/main.py", "DELTA_T = 0.1",
     "sp_dt", "时间步 0.1"),
    ("spectrum_method/main.py", "J = self.phi_y * self.c_x - self.phi_x * self.c_y",
     "sp_jacobian", "非线性对流项 J = φ_y·c_x − φ_x·c_y"),
    ("spectrum_method/main.py", "temp1 = R * self.c_hat * 1.0j * self.kn_kn",
     "sp_phi", "流函数谱解 φ̂ = −iR k_y ĉ /(k² + iR(k_x c_x + k_y c_y))（R=黏度比）"),
    ("spectrum_method/main.py", "c_bar = c_tilda * np.exp(-self.km2kn2 * DELTA_T)#1",
     "sp_if", "积分因子 exp(−k²Δt)：扩散项被解析积分（RK 启动步与 AB2 主循环各一处，取第 1 处）"),
    ("spectrum_method/main.py", "c_tilda = c_hat + 1 / 6 * (k1 + 2 * k2 + 2 * k3 + k4)",
     "sp_rk4", "启动步用 RK4"),
    ("spectrum_method/main.py", "nextc_hat = c_hat - DELTA_T * ((J_bar + J_hat) / 2 + self.km2kn2 * (c_bar + c_hat) / 2)",
     "sp_ab2", "主循环用积分因子 + 二阶 Adams–Bashforth"),
    ("spectrum_method/main.py", "def getwavenum(cc, A):",
     "sp_wavenum", "从浓度场 FFT 提取指进特征波数（无理论对比）"),
    ("spectrum_method/main.py", "from numba import jit",
     "sp_numba", "引入 numba 但主循环未见 @jit（注释掉的装饰器）"),
    ("spectrum_method/main_3phase.py", "'''https://aip.scitation.org/doi/10.1063/1.866726'''",
     "sp_3phase_ref", "三相脚本内部只引 1988 文献；README 宣称的 PRE 93,023103 未在此文件出现"),
]

ALL = META + PARAMS + SOLVER + BC + GEOM + VALIDATION + GPU + SPECTRAL

# 声明"缺席"的检查项：渲染器对每个 (path, token) 断言 token 在该文件中出现 0 次。
ABSENT = [
    ("lattice_boltzmann/fingering_periodic_gpu.py", "RawKernel", "GPU 版无自研 CUDA kernel"),
    ("lattice_boltzmann/fingering_periodic_gpu.py", "ElementwiseKernel", "GPU 版无逐元素融合 kernel"),
    ("lattice_boltzmann/fingering_periodic_gpu.py", "cuda", "GPU 版无直接 CUDA 调用"),
    ("lattice_boltzmann/fingering_periodic_gpu.py", "mpi", "GPU 版无 MPI/多卡"),
    ("lattice_boltzmann/fingering.py", "argparse", "无命令行参数解析"),
    ("lattice_boltzmann/fingering.py", "assert", "无断言（无运行时自检）"),
    ("lattice_boltzmann/fingering.py", "ShanChen", "非 Shan-Chen 伪势模型"),
    ("lattice_boltzmann/fingering.py", "gravity", "无重力/浮力项"),
    ("lattice_boltzmann/fingering.py", "contact_angle", "无接触角计算"),
]

# 声明"只在定义行出现一次"的记号（= 定义了但从未使用）：严格相等断言。
EXPECTED_COUNTS = [
    ("lattice_boltzmann/validation.py", "Theta", 1, "接触角常量定义后从未被使用"),
    ("lattice_boltzmann/fingering.py", "n_non", 1, "幂律指数定义后从未被使用（fingering 主流程）"),
    ("lattice_boltzmann/fingering.py", "rho0", 2, "rho0 只在定义与 print 中各出现一次"),
    ("lattice_boltzmann/fingering.py", "np.seterr", 1, "全局浮点异常设置只在 __main__ 处"),
]