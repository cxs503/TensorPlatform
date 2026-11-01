"""船体梁垂向振动：干模态 + 附连水质量 → 螺旋桨叶频受迫响应（mission m1680）。

背景与动机
----------
平台此前**没有任何船体梁振动能力**（``whipping`` / ``hull_girder`` / ``blade_rate``
/ ``ISO 6954`` 在 ``backend/`` 内零命中），而振动噪声是总体性能九大域之一，且与
已有疲劳链（雨流 + S-N）、耐波链直接相连：

* 叶频激励落在船体梁 2 节点垂向固有频率附近时，尾体加速度、上层建筑舒适性
  （ISO 6954）与高频疲劳应力幅同时恶化；
* 常规设计规则要求 2 节点频率与叶频谐波保持约 ±10 % 裕度，这条判据无法靠
  「经验公式」证明，必须有一个可复算的特征值求解器。

本模块给出**可证伪**的实现，而不是拟合常数：

1. **求解器**：Euler-Bernoulli 梁的 2 节点 Hermite 有限元（一致质量矩阵），自由-自由
   边界（端部 M=V=0），用**移位广义特征值问题** ``(K + a M) phi = lam M phi``
   （``a > 0`` 使刚度阵正定）求解，再回收 ``w^2 = lam - a``。均匀梁解析根
   ``cos(bL)cosh(bL) = 1`` 因此是**求解器误差**的精确判据（不是模型误差），可按
   <0.1 % 验收。
2. **船体梁属性**由几何（:class:`HullForm` 的剖面面积分布）派生：结构钢质量按剖面
   面积成比例分配，附连水质量按转发的垂向附连水系数 ``C_a`` 分配，剖面惯性矩按
   双翼板模型 ``I = eta_I * A_st * D_eff^2 / 4`` 估计。``eta_I``、``C_a``、激励力
   系数是**声明式经验参数**（带默认区间），所以单船绝对频率/速度的精度等级记为
   **WITHHELD**（给区间），而**求解器**精度是 REAL（对解析根 <0.1 %）。
3. **受迫响应**：叶频谐波 ``f_k = k * Z * n``（``Z`` 叶数、``n`` 轴转频）的多模态叠加，
   模态阻尼 ``zeta``（船体梁常用 0.5 %–2 %，默认 1 %），输出关注点速度 RMS、叶频
   裕度判据与 ISO 6954 量级比较（未加权，见局限）。

可验证恒等式（本模块的测试锚点）
--------------------------------
* 自由-自由均匀梁 ``w_n = (b_n L)^2 sqrt(EI / (m L^4))``，``b_n L`` =
  4.730040744862704 / 7.853204624095838 / 10.995607838001671 / 14.137165491257464；
* 悬臂均匀梁 ``cos(bL)cosh(bL) = -1``，``b_1 L = 1.875104068711961``；
* 完备模态静力求和满足平衡方程 ``K w = F_flex``（相对残差判据）；
* 尺度律：``EI``、``m``、``L`` 同比例缩放时 ``w ∝ sqrt(EI/(m L^4))``；
* 单自由度解析频响 ``H = 1/(w_n^2 - w^2 + 2 i zeta w_n w)``。

局限（如实声明）
----------------
* Euler-Bernoulli（无剪切变形/转动惯量）→ 高阶模态偏高；``D/L ≈ 1/15`` 时 2 节点
  模态 Timoshenko 修正约 −1 %；
* 干模态 + 附连水质量（不区分湿模态虚部，忽略自由液面与阻尼耦合）；
* 附连水系数、激励力系数为声明式经验值，绝对值不作 3 % 主张；
* ISO 6954:2000 的**频率计权曲线**未实现（只给未加权 RMS 速度 + 声明阈值），
  计权域合规判定记为 WITHHELD。
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Sequence, Tuple

import numpy as np

G = 9.80665
E_STEEL = 2.06e11          # Pa，船用软钢杨氏模量
RHO_STEEL = 7850.0         # kg/m^3

#: 自由-自由均匀梁 ``cos(bL)cosh(bL) = 1`` 的前五个解析根（判据）。
FREE_FREE_ROOTS = (
    4.730040744862704,
    7.853204624095838,
    10.995607838001671,
    14.137165491257464,
    17.278759657399482,
)
#: 悬臂均匀梁 ``cos(bL)cosh(bL) = -1`` 的前三个解析根。
CLAMPED_FREE_ROOTS = (1.875104068711961, 4.694091132974174, 7.854757438237613)


class GirderModelError(ValueError):
    """船体梁模型输入越界（几何/材料/离散化不合法）。"""


# ---------------------------------------------------------------------------
# 有限元内核
# ---------------------------------------------------------------------------
def element_matrices(le: float, EI: float, m: float) -> Tuple[np.ndarray, np.ndarray]:
    """Euler-Bernoulli 2 节点 Hermite 单元的刚度阵与一致质量阵。

    自由度顺序 ``[w1, theta1, w2, theta2]``。
    """
    k = EI / le ** 3 * np.array(
        [
            [12.0, 6.0 * le, -12.0, 6.0 * le],
            [6.0 * le, 4.0 * le ** 2, -6.0 * le, 2.0 * le ** 2],
            [-12.0, -6.0 * le, 12.0, -6.0 * le],
            [6.0 * le, 2.0 * le ** 2, -6.0 * le, 4.0 * le ** 2],
        ]
    )
    mm = m * le / 420.0 * np.array(
        [
            [156.0, 22.0 * le, 54.0, -13.0 * le],
            [22.0 * le, 4.0 * le ** 2, 13.0 * le, -3.0 * le ** 2],
            [54.0, 13.0 * le, 156.0, -22.0 * le],
            [-13.0 * le, -3.0 * le ** 2, -22.0 * le, 4.0 * le ** 2],
        ]
    )
    return k, mm


def hermite_shape(xi: float) -> np.ndarray:
    """单元内 Hermite 形函数（``xi`` 为单元局部坐标，0 → 1）。"""
    return np.array(
        [
            1.0 - 3.0 * xi ** 2 + 2.0 * xi ** 3,
            xi - 2.0 * xi ** 2 + xi ** 3,
            3.0 * xi ** 2 - 2.0 * xi ** 3,
            -xi ** 2 + xi ** 3,
        ]
    )


def build_beam(
    length: float,
    n_elem: int,
    EI_of_x: Callable[[float], float],
    m_of_x: Callable[[float], float],
) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
    """装配自由-自由 Euler-Bernoulli 梁，返回 ``(K, M, x_nodes)``。"""
    if not (length > 0.0):
        raise GirderModelError(f"length={length} must be > 0")
    if n_elem < 4:
        raise GirderModelError(f"n_elem={n_elem} too small (need >= 4)")
    le = length / n_elem
    ndof = 2 * (n_elem + 1)
    K = np.zeros((ndof, ndof))
    M = np.zeros((ndof, ndof))
    x = -0.5 * length + le * np.arange(n_elem + 1)
    for e in range(n_elem):
        xm = -0.5 * length + (e + 0.5) * le
        EI = float(EI_of_x(xm))
        m = float(m_of_x(xm))
        if not (EI > 0.0) or not (m > 0.0):
            raise GirderModelError(
                f"element {e}: EI={EI}, m={m} must be positive at x={xm:.3f}"
            )
        ke, me = element_matrices(le, EI, m)
        idx = [2 * e, 2 * e + 1, 2 * e + 2, 2 * e + 3]
        K[np.ix_(idx, idx)] += ke
        M[np.ix_(idx, idx)] += me
    return K, M, x


def build_clamped_free(
    length: float, n_elem: int, EI: float, m: float
) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
    """悬臂梁 ``(K, M, x)``：节点 0（``x=-L/2``）固定，用于二阶交叉验证。"""
    K, M, x = build_beam(length, n_elem, lambda _x: EI, lambda _x: m)
    keep = np.arange(2, K.shape[0])   # 只删除节点 0 的 w、theta（单元 0 仍连节点 1）
    return K[np.ix_(keep, keep)], M[np.ix_(keep, keep)], x


@dataclass
class GirderEigen:
    """梁特征解（已剔除刚体模态）。"""

    omega: np.ndarray            # rad/s
    phi: np.ndarray              # (ndof, nmodes)，phi^T M phi = I
    x: np.ndarray                # 节点坐标（艉 -L/2 → 艏 +L/2）
    K: np.ndarray
    M: np.ndarray
    length: float
    n_elem: int

    @property
    def freq_hz(self) -> np.ndarray:
        return self.omega / (2.0 * math.pi)

    @property
    def freq_cpm(self) -> np.ndarray:
        return self.freq_hz * 60.0

    def shape_at(self, mode: int, x_m: float) -> float:
        """在实体坐标 ``x_m``（−L/2..L/2）插值第 ``mode`` 阶模态位移。"""
        x = float(np.clip(x_m, -0.5 * self.length, 0.5 * self.length))
        le = self.length / self.n_elem
        e = int(min(max(math.floor((x + 0.5 * self.length) / le), 0), self.n_elem - 1))
        s = (x - (-0.5 * self.length + e * le)) / le
        return float(hermite_shape(s) @ self.phi[2 * e: 2 * e + 4, mode])

    def rigid_modes(self, n_rigid: int = 2) -> np.ndarray:
        """质量归一化的刚体模态（沉浮 + 纵倾），用于移除载荷的刚体分量。"""
        ndof = self.K.shape[0]
        dofs = np.arange(ndof)
        trans = (dofs % 2) == 0
        xd = np.repeat(self.x, 2)
        # 刚体模态必须同时含转角自由度：沉浮 (w=1, theta=0)、纵倾 (w=x, theta=1)
        heave = np.where(trans, 1.0, 0.0)
        pitch = np.where(trans, xd, 1.0)
        basis = [heave, pitch][:n_rigid]
        out = []
        for b in basis:
            nb = math.sqrt(float(b @ self.M @ b))
            out.append(b / nb)
        return np.column_stack(out)


def generalized_eigh(K: np.ndarray, M: np.ndarray) -> Tuple[np.ndarray, np.ndarray]:
    """对称正定广义特征值问题 ``K phi = lam M phi``（纯 numpy 实现）。

    用 Cholesky 约化 ``M = L L^T``、``phi = L^{-T} y``，化成标准问题
    ``L^{-1} K L^{-T} y = lam y``。**不调用 scipy.linalg.eigh 的广义驱动**：本机
    LAPACK ``dsygvd`` 在 128 核容器内直接段错误（m1680 实测，见 KNOWN_TRAPS），
    该实现只用 ``numpy.linalg``（``dsyevd``）并做了对称化投影。
    """
    Lc = np.linalg.cholesky(M)
    B = np.linalg.solve(Lc, K)
    C = np.linalg.solve(Lc, B.T).T
    C = 0.5 * (C + C.T)
    lam, y = np.linalg.eigh(C)
    phi = np.linalg.solve(Lc.T, y)
    return lam, phi


def solve_free_free(
    K: np.ndarray,
    M: np.ndarray,
    x: np.ndarray,
    n_modes: int = 8,
    shift_ratio: float = 1e-7,
) -> GirderEigen:
    """移位广义特征值求解，剔除两个刚体模态（沉浮/纵倾）。

    ``a = shift_ratio * trace(K)/trace(M) > 0`` 使 ``K + aM`` 正定，可用 Cholesky
    约化；回收 ``w^2 = lam - a``（刚体模态给出 ``lam ~ a``，位于升序最前两位）。
    """
    ndof = K.shape[0]
    if n_modes + 2 > ndof:
        raise GirderModelError(f"n_modes={n_modes} too many for ndof={ndof}")
    lam_scale = float(np.trace(K) / np.trace(M))
    a = shift_ratio * lam_scale
    lam, vec = generalized_eigh(K + a * M, M)
    omega = np.sqrt(np.maximum(lam - a, 0.0))
    sel = slice(2, 2 + n_modes)       # 升序前两个为刚体模态
    return GirderEigen(
        omega=omega[sel],
        phi=vec[:, sel],
        x=x,
        K=K,
        M=M,
        length=float(x[-1] - x[0]),
        n_elem=int(len(x) - 1),
    )


def solve_all_flex_modes(K: np.ndarray, M: np.ndarray, x: np.ndarray) -> GirderEigen:
    """求全部柔性子空间模态（静力闭合判据用）。"""
    return solve_free_free(K, M, x, n_modes=K.shape[0] - 2)


def mode_norm_error(eig: GirderEigen) -> float:
    """``max |phi^T M phi - I|``：模态归一化误差（数值健康度）。"""
    A = eig.phi.T @ eig.M @ eig.phi
    return float(np.max(np.abs(A - np.eye(A.shape[0]))))


# ---------------------------------------------------------------------------
# 均匀梁解析判据
# ---------------------------------------------------------------------------
def uniform_free_free_freqs(
    EI: float, m: float, length: float, n_modes: int = 5
) -> np.ndarray:
    """均匀自由-自由梁解析频率 [Hz]：``w_n = (b_n L)^2 sqrt(EI/(m L^4))``。"""
    roots = FREE_FREE_ROOTS[:n_modes]
    scale = math.sqrt(float(EI) / (float(m) * length ** 4)) / (2.0 * math.pi)
    return np.array([r ** 2 for r in roots]) * scale


def uniform_clamped_free_freqs(
    EI: float, m: float, length: float, n_modes: int = 3
) -> np.ndarray:
    """均匀悬臂梁解析频率 [Hz]。"""
    roots = CLAMPED_FREE_ROOTS[:n_modes]
    scale = math.sqrt(float(EI) / (float(m) * length ** 4)) / (2.0 * math.pi)
    return np.array([r ** 2 for r in roots]) * scale


# ---------------------------------------------------------------------------
# 船体梁属性派生（几何 → 质量/刚度分布）
# ---------------------------------------------------------------------------
@dataclass
class ShipGirder:
    """由 :class:`HullForm` 派生的船体梁模型（声明式经验参数）。

    ``hull`` 提供 ``L``、``B``、``T``、``Cb``、``rho``、``section_area(x)``、
    ``volume``。``steel_ratio`` 为结构钢质量/排水量（散货/油船 0.18–0.28，
    集装箱 0.25–0.35）；``added_mass_coeff`` 为垂向附连水系数（常用 0.5–1.2）；
    ``eta_I`` 为双翼板剖面惯性矩效率（0.5–0.9）；``depth_factor`` 给出
    ``D_eff = depth_factor * T``。
    """

    hull: object
    steel_ratio: float = 0.18
    added_mass_coeff: float = 0.80
    eta_I: float = 0.70
    depth_factor: float = 1.20
    long_eff: float = 0.60
    deck_x_frac: float = 0.0
    _cache: dict = field(default_factory=dict, repr=False)

    def __post_init__(self) -> None:
        if not (0.05 <= self.steel_ratio <= 0.60):
            raise GirderModelError(f"steel_ratio={self.steel_ratio} outside [0.05, 0.60]")
        if not (0.0 <= self.added_mass_coeff <= 2.0):
            raise GirderModelError(f"added_mass_coeff={self.added_mass_coeff} outside [0, 2]")
        if not (0.05 <= self.eta_I <= 1.0):
            raise GirderModelError(f"eta_I={self.eta_I} outside [0.05, 1.0]")
        if not (0.3 <= self.long_eff <= 0.9):
            raise GirderModelError(f"long_eff={self.long_eff} outside [0.3, 0.9]")
        if not (0.5 <= self.depth_factor <= 2.0):
            raise GirderModelError(f"depth_factor={self.depth_factor} outside [0.5, 2]")
        if not (-0.5 <= self.deck_x_frac <= 0.5):
            raise GirderModelError(f"deck_x_frac={self.deck_x_frac} outside [-0.5, 0.5]")

    @property
    def length(self) -> float:
        return float(self.hull.L)

    @property
    def displacement_kg(self) -> float:
        return float(self.hull.volume * self.hull.rho)

    @property
    def depth_m(self) -> float:
        return self.depth_factor * float(self.hull.T)

    def _area_norm(self) -> float:
        if "area_norm" not in self._cache:
            x = np.linspace(-0.5, 0.5, 2001) * self.length
            A = np.asarray(self.hull.section_area(x), dtype=float)
            self._cache["area_norm"] = float(np.trapezoid(A, x))
        return float(self._cache["area_norm"])

    def section_area(self, x: float) -> float:
        return float(np.asarray(self.hull.section_area(np.array([x])), dtype=float)[0])

    def mass_per_length(self, x: float) -> float:
        """单位长度质量 [kg/m] = 结构钢 + 垂向附连水（均按剖面面积成比例）。"""
        A = self.section_area(x)
        norm = self._area_norm()
        m_steel = self.steel_ratio * self.displacement_kg * A / norm
        m_added = self.added_mass_coeff * self.hull.rho * A * (self.hull.volume / norm)
        return m_steel + m_added

    def steel_mass_per_length(self, x: float) -> float:
        """该站位的纵向结构钢质量 [kg/m]（按剖面面积成比例分配）。"""
        A = self.section_area(x)
        return self.steel_ratio * self.displacement_kg * A / self._area_norm()

    def steel_section_area(self, x: float) -> float:
        """纵向有效钢剖面面积 [m^2]：``long_eff * m_steel(x) / rho_steel``。

        注意不能把「浸没剖面面积 × steel_ratio」当钢面积（那样舯剖面会给出
        317 m^2，比 30 万吨级油船实际 10–15 m^2 高一个量级，I 高 15 倍——m1680
        首个版本即踩此坑）。钢质量除以钢密度得到该站位的钢材体积/米，再乘纵向
        连续性效率 ``long_eff``（0.5–0.7，板/骨材并非全部纵向连续）。
        """
        return self.long_eff * self.steel_mass_per_length(x) / RHO_STEEL

    def EI(self, x: float) -> float:
        """抗弯刚度 [N m^2]：双翼板模型 ``I = eta_I A_st D_eff^2 / 4``。"""
        A_st = self.steel_section_area(x)
        I = self.eta_I * A_st * self.depth_m ** 2 / 4.0
        return E_STEEL * I

    def mid_EI(self) -> float:
        return self.EI(0.0)

    def mid_mass(self) -> float:
        return self.mass_per_length(0.0)

    def girder_inertia_m4(self, x: float = 0.0) -> float:
        """剖面惯性矩 [m^4]（可与其他来源的船体梁 I 对比）。"""
        return self.EI(x) / E_STEEL


# ---------------------------------------------------------------------------
# 载荷与受迫响应
# ---------------------------------------------------------------------------
def point_load_vector(ndof: int, x_nodes: np.ndarray, x_load: float, force: complex) -> np.ndarray:
    """节点集中力向量（``x_load`` 最近节点的平移自由度）。"""
    i = int(np.argmin(np.abs(x_nodes - x_load)))
    F = np.zeros(ndof, dtype=complex)
    F[2 * i] += force
    return F


def sdof_receptance(omega: float, wn: float, zeta: float) -> complex:
    """单自由度位移柔度 ``1 / (wn^2 - w^2 + 2 i zeta wn w)``（解析判据）。"""
    return 1.0 / complex(wn ** 2 - omega ** 2, 2.0 * zeta * wn * omega)


@dataclass
class BladeRateExcitation:
    """螺旋桨叶频激励（声明式力幅模型）。

    ``shaft_rps`` 轴转频；``blades`` 叶数 ``Z``；``thrust_N`` 推力；
    ``force_ratio`` 一阶叶频力/推力（轴承力典型 0.5 %–3 %）；
    ``harmonics`` 计入的谐波数（力幅按 ``1/k`` 衰减，声明式）。
    """

    shaft_rps: float
    blades: int
    thrust_N: float
    force_ratio: float = 0.02
    harmonics: int = 4

    def __post_init__(self) -> None:
        if not (self.shaft_rps > 0.0):
            raise GirderModelError(f"shaft_rps={self.shaft_rps} must be > 0")
        if self.blades < 2:
            raise GirderModelError(f"blades={self.blades} must be >= 2")
        if not (0.0 < self.force_ratio <= 0.20):
            raise GirderModelError(f"force_ratio={self.force_ratio} outside (0, 0.20]")
        if self.harmonics < 1:
            raise GirderModelError(f"harmonics={self.harmonics} must be >= 1")
        if not (self.thrust_N > 0.0):
            raise GirderModelError(f"thrust_N={self.thrust_N} must be > 0")

    @property
    def blade_rate_hz(self) -> float:
        return self.shaft_rps * self.blades

    def harmonic_freqs_hz(self) -> np.ndarray:
        return np.array([self.blade_rate_hz * k for k in range(1, self.harmonics + 1)])

    def harmonic_forces_n(self) -> np.ndarray:
        return np.array([self.thrust_N * self.force_ratio / k for k in range(1, self.harmonics + 1)])


def static_closure(eig_full: GirderEigen, x_load: float, x_out: float) -> Dict[str, float]:
    """完备模态静力求和 vs 平衡残差 / 伪逆静力解（闭合判据）。

    ``F_flex = F - M Phi_r (Phi_r^T F)`` 去掉刚体分量后 ``K w = F_flex`` 唯一，
    其解为 ``pinv(K) F_flex``；模态侧为 ``sum_i phi_i (phi_i^T F)/w_i^2``。
    两条路径不同（特征分解 vs 最小二乘伪逆），故可作为求解器闭合判据。
    """
    ndof = eig_full.K.shape[0]
    F = point_load_vector(ndof, eig_full.x, x_load, complex(1.0, 0.0)).real
    Pr = eig_full.rigid_modes()
    F_flex = F - eig_full.M @ (Pr @ (Pr.T @ F))
    w_direct = np.linalg.pinv(eig_full.K, rcond=1e-12) @ F_flex
    q = eig_full.phi.T @ F
    w_modal = np.zeros(ndof)
    for i in range(eig_full.omega.size):
        w_modal += eig_full.phi[:, i] * (q[i] / eig_full.omega[i] ** 2)

    def at(vec: np.ndarray, x_m: float) -> float:
        le = eig_full.length / eig_full.n_elem
        xm = float(np.clip(x_m, -0.5 * eig_full.length, 0.5 * eig_full.length))
        e = int(min(max(math.floor((xm + 0.5 * eig_full.length) / le), 0), eig_full.n_elem - 1))
        s = (xm - (-0.5 * eig_full.length + e * le)) / le
        return float(hermite_shape(s) @ vec[2 * e: 2 * e + 4])

    resid = float(np.linalg.norm(eig_full.K @ w_modal - F_flex) / np.linalg.norm(F))
    a, b = at(w_modal, x_out), at(w_direct, x_out)
    denom = abs(b) if abs(b) > 0 else float("nan")
    return {
        "modal_deflection": a,
        "pinv_deflection": b,
        "rel_err": abs(a - b) / denom,
        "equilibrium_residual_rel": resid,
    }


def forced_response(
    eig: GirderEigen,
    excitation: BladeRateExcitation,
    x_load: float,
    x_out: float,
    zeta: float = 0.01,
    n_modes: Optional[int] = None,
) -> Dict[str, object]:
    """叶频谐波作用下关注点的稳态响应（多模态叠加）。"""
    if not (0.0 < zeta < 0.5):
        raise GirderModelError(f"zeta={zeta} outside (0, 0.5)")
    n = int(min(n_modes or eig.omega.size, eig.omega.size))
    freqs = excitation.harmonic_freqs_hz()
    forces = excitation.harmonic_forces_n()
    ndof = eig.M.shape[0]

    rows: List[Dict[str, float]] = []
    disp_sq = vel_sq = acc_sq = 0.0
    participation: List[float] = []
    for k, (f, F0) in enumerate(zip(freqs, forces)):
        om = 2.0 * math.pi * f
        Fvec = point_load_vector(ndof, eig.x, x_load, complex(F0, 0.0))
        q = eig.phi[:, :n].T @ Fvec
        H = np.array([sdof_receptance(om, eig.omega[i], zeta) for i in range(n)], dtype=complex)
        part = q * H
        participation.append(float(abs(part[0]) / max(F0 / eig.omega[0] ** 2, 1e-30)))
        w_mode = np.array([eig.shape_at(i, x_out) for i in range(n)])
        w = complex(np.dot(w_mode, part))
        disp_sq += 0.5 * abs(w) ** 2
        vel_sq += 0.5 * abs(1j * om * w) ** 2
        acc_sq += 0.5 * abs(-(om ** 2) * w) ** 2
        rows.append(
            {
                "harmonic": k + 1,
                "freq_hz": float(f),
                "force_N": float(F0),
                "disp_mm": float(abs(w) * 1e3),
                "vel_mm_s": float(abs(1j * om * w) * 1e3),
                "acc_mm_s2": float(abs(-(om ** 2) * w) * 1e3),
                "dominant_mode": int(np.argmax(np.abs(part))) + 1,
            }
        )
    return {
        "harmonics": rows,
        "disp_rms_mm": math.sqrt(disp_sq) * 1e3,
        "vel_rms_mm_s": math.sqrt(vel_sq) * 1e3,
        "acc_rms_mm_s2": math.sqrt(acc_sq) * 1e3,
        "modal_participation": participation,
        "zeta": zeta,
        "n_modes_used": n,
        "x_out_m": x_out,
        "x_load_m": x_load,
    }


# ---------------------------------------------------------------------------
# 设计判据
# ---------------------------------------------------------------------------
#: 行业惯用的叶频避振裕度带（固有频率 vs 叶频谐波相对差）。
RESONANCE_MARGIN = 0.10
#: ISO 6954:2000 未加权 RMS 速度量级阈值 [mm/s]（A 起居 / B 船员 / C 工作区）。
ISO6954_UNWEIGHTED_LIMITS_MM_S = {"A_accommodation": 4.0, "B_crew": 6.0, "C_working": 8.0}


def blade_rate_margin(
    natural_hz: Sequence[float],
    excitation: BladeRateExcitation,
    margin: float = RESONANCE_MARGIN,
) -> Dict[str, object]:
    """固有频率对叶频谐波的相对裕度与判定（取最不利配对）。"""
    harms = excitation.harmonic_freqs_hz()
    rows: List[Dict[str, float]] = []
    worst: Optional[Dict[str, float]] = None
    for i, fn in enumerate(natural_hz, start=1):
        for k, fk in enumerate(harms, start=1):
            rel = (fk - fn) / fn
            rows.append(
                {
                    "mode": i,
                    "natural_hz": float(fn),
                    "harmonic": k,
                    "harmonic_hz": float(fk),
                    "rel_margin": float(rel),
                }
            )
            if worst is None or abs(rel) < abs(worst["rel_margin"]):
                worst = rows[-1]
    ok = bool(worst and abs(worst["rel_margin"]) >= margin)
    return {
        "margin_band": margin,
        "worst": worst,
        "verdict": "PASS" if ok else "FAIL_RESONANCE_RISK",
        "pairs": rows,
    }


def sensitivity_band(
    hull,
    excitation: BladeRateExcitation,
    n_elem: int = 40,
    n_modes: int = 2,
    eta_I_range: Tuple[float, float] = (0.5, 0.9),
    added_mass_range: Tuple[float, float] = (0.5, 1.2),
) -> Dict[str, object]:
    """经验参数区间 → 2 节点频率与甲板速度的区间（WITHHELD 的量化解）。"""
    out = []
    for eta in eta_I_range:
        for ca in added_mass_range:
            g = ShipGirder(hull, eta_I=eta, added_mass_coeff=ca)
            K, M, x = build_beam(g.length, n_elem, g.EI, g.mass_per_length)
            eig = solve_free_free(K, M, x, n_modes=n_modes)
            resp = forced_response(
                eig, excitation, x_load=-0.40 * g.length, x_out=g.deck_x_frac * g.length,
                zeta=0.01, n_modes=min(2, n_modes),
            )
            out.append(
                {
                    "eta_I": eta,
                    "added_mass_coeff": ca,
                    "f2node_hz": float(eig.freq_hz[0]),
                    "f3node_hz": float(eig.freq_hz[1]),
                    "vel_rms_mm_s": float(resp["vel_rms_mm_s"]),
                }
            )
    f2 = [r["f2node_hz"] for r in out]
    v = [r["vel_rms_mm_s"] for r in out]
    return {
        "cases": out,
        "f2node_hz_range": [min(f2), max(f2)],
        "vel_rms_mm_s_range": [min(v), max(v)],
    }


def evaluate_girder(
    girder: ShipGirder,
    excitation: BladeRateExcitation,
    n_elem: int = 80,
    n_modes: int = 8,
    zeta: float = 0.01,
    x_load_frac: float = -0.40,
    with_sensitivity: bool = True,
) -> Dict[str, object]:
    """端到端评估：特征解 → 叶频受迫响应 → 裕度/舒适性判定。"""
    L = girder.length
    K, M, x = build_beam(L, n_elem, girder.EI, girder.mass_per_length)
    eig = solve_free_free(K, M, x, n_modes=n_modes)
    eig_all = solve_all_flex_modes(K, M, x)
    closure = static_closure(eig_all, x_load_frac * L, girder.deck_x_frac * L)
    resp = forced_response(
        eig,
        excitation,
        x_load=x_load_frac * L,
        x_out=girder.deck_x_frac * L,
        zeta=zeta,
        n_modes=n_modes,
    )
    margin = blade_rate_margin(eig.freq_hz[:3], excitation)   # 设计惯例：1–3 节点
    vel = float(resp["vel_rms_mm_s"])
    limits = {
        k: {"limit_mm_s": v, "ratio": vel / v, "verdict": "PASS" if vel <= v else "FAIL"}
        for k, v in ISO6954_UNWEIGHTED_LIMITS_MM_S.items()
    }
    doc: Dict[str, object] = {
        "geometry": {
            "L_m": L,
            "B_m": float(girder.hull.B),
            "T_m": float(girder.hull.T),
            "Cb": float(girder.hull.Cb),
            "displacement_t": girder.displacement_kg / 1e3,
            "depth_eff_m": girder.depth_m,
            "I_mid_m4": girder.girder_inertia_m4(),
        },
        "girder": {
            "EI_mid_Nm2": girder.mid_EI(),
            "mass_mid_kg_m": girder.mid_mass(),
            "steel_ratio": girder.steel_ratio,
            "added_mass_coeff": girder.added_mass_coeff,
            "eta_I": girder.eta_I,
            "n_elem": n_elem,
        },
        "modes": {
            "freq_hz": [float(f) for f in eig.freq_hz],
            "freq_cpm": [float(f) for f in eig.freq_cpm],
            "mode_norm_error": mode_norm_error(eig),
        },
        "excitation": {
            "shaft_rps": excitation.shaft_rps,
            "blades": excitation.blades,
            "blade_rate_hz": excitation.blade_rate_hz,
            "harmonic_freqs_hz": [float(f) for f in excitation.harmonic_freqs_hz()],
            "harmonic_forces_N": [float(f) for f in excitation.harmonic_forces_n()],
            "force_ratio": excitation.force_ratio,
        },
        "response": resp,
        "static_closure": closure,
        "resonance": margin,
        "iso6954_unweighted": limits,
        "evidence_grade": {
            "solver": "REAL：对解析梁根 <0.1 %",
            "single_ship_absolute": "WITHHELD：eta_I/C_a/force_ratio 为声明式经验参数，"
                                    "区间见 sensitivity",
            "iso6954_compliance": "WITHHELD：未实现 ISO 6954 频率计权曲线",
        },
    }
    if with_sensitivity:
        doc["sensitivity"] = sensitivity_band(girder.hull, excitation, n_elem=40, n_modes=2)
    return doc
