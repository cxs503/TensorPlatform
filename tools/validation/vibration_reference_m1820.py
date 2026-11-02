"""振动噪声核（``backend/api/domain/ship_vibration.py``）的**独立**参考文献库（m1820）。

纪律（与 m1523/m1590 同源）：
* 本文件**不 import** 被审模块，也不复用其任何公式/常数；每个参考量要么是
  公开发表的文献表值（Blevins 1979 Table 8-1、IEC 61672-1 Table 3），要么是
  **不同数值离散**（有限差分特征值、时域 RK4 积分），要么是恒等式。
* 所有函数返回纯 float，便于 pytest 直接钉住（参考侧自身也可被回归）。

单位：全 SI（m, kg, s, N, Pa, Hz, dB）。
"""
from __future__ import annotations

import math
from typing import Dict, List, Sequence, Tuple

import numpy as np

# ---------------------------------------------------------------------------
# 1. 文献表值：Euler–Bernoulli 梁无量纲特征值 lambda_n
#    Blevins R.D., "Formulas for Natural Frequency and Mode Shape" (1979),
#    Table 8-1（亦见 Timoshenko, Vibration Problems in Engineering, 3rd ed.,
#    §5.3；数值为 cos(x)cosh(x)=±1 等的根，独立于任何求根器）。
# ---------------------------------------------------------------------------
BLEVINS_TABLE_8_1: Dict[str, List[float]] = {
    "clamped-clamped": [
        4.730040744862704, 7.853204624095838, 10.995607838001671, 14.137165491257464,
    ],
    "clamped-free": [
        1.875104068711961, 4.694091132974174, 7.854757438237613, 10.995540734875467,
    ],
    "clamped-pinned": [
        3.926602312047919, 7.068582752191768, 10.210176124166829, 13.351768777756622,
    ],
    "pinned-pinned": [
        math.pi, 2.0 * math.pi, 3.0 * math.pi, 4.0 * math.pi,
    ],
}


def blevins_lambda(bc: str, n: int = 4) -> List[float]:
    """文献表值（文献侧参考，不经任何求根）."""
    key = bc.lower()
    if key not in BLEVINS_TABLE_8_1:
        raise ValueError(f"no literature value for bc={bc!r}")
    return list(BLEVINS_TABLE_8_1[key][:n])


def beam_frequency_from_lambda(bc: str, E: float, I: float, rho: float, A: float,
                               L: float, n_modes: int = 4) -> List[float]:
    """用**文献 lambda** 代闭式关系 f = lambda^2/(2 pi L^2) sqrt(EI/(rho A))。

    与模块独立之处：模块用自求根 lambda；此处用文献 lambda。
    """
    lams = blevins_lambda(bc, n_modes)
    c = math.sqrt(E * I / (rho * A)) / (2.0 * math.pi * L * L)
    return [lam * lam * c for lam in lams]


# ---------------------------------------------------------------------------
# 2. 板：有限差分（5 点 Laplacian）解 Kirchhoff 薄板——与「正弦积 Rayleigh 商」
#    完全不同的离散。SSSS 边条件 w=0 且 M_n=0 <=> 边界上 laplacian(w)=0，
#    于是耦合系统 L w = phi, L phi = lambda w（L 为 Dirichlet 零边 Laplacian
#    矩阵）化为特征问题 L^2 w = lambda w；lambda = rho*h*omega^2/D。
# ---------------------------------------------------------------------------
def plate_ssss_fd_eigenvalues(a: float, b: float, n_grid: int = 24,
                              n_modes: int = 4) -> List[Tuple[float, float]]:
    """返回前 n_modes 个无量纲 lambda_mn = pi^2[(m/a)^2+(n/b)^2] 的 FD 近似值。

    直接把 FD 的 L^2 特征值开方即得 mu_mn（Laplacian 特征值），再按
    lambda_mn = mu_mn 对应同一物理量（此处返回 mu^2 的平方根，即 mu）。
    返回 [(值, 相对离散误差不计), ...] 升序。
    """
    nx = ny = int(n_grid)
    hx, hy = a / (nx + 1), b / (ny + 1)
    # Dirichlet 零边 5 点 Laplacian：Kronecker 组装（向量化，避免 O(N^2) python 循环）
    Tx = (np.diag(np.full(nx, -2.0)) + np.diag(np.full(nx - 1, 1.0), 1)
          + np.diag(np.full(nx - 1, 1.0), -1)) / hx ** 2
    Ty = (np.diag(np.full(ny, -2.0)) + np.diag(np.full(ny - 1, 1.0), 1)
          + np.diag(np.full(ny - 1, 1.0), -1)) / hy ** 2
    L = np.kron(np.eye(ny), Tx) + np.kron(Ty, np.eye(nx))
    ev = np.linalg.eigvalsh(L)                     # 升序，全负
    mu = np.sqrt(np.clip(ev ** 2, 0.0, None))      # = |ev|
    mu_sorted = np.sort(mu)                        # Laplacian 谱的量级
    return [float(x) for x in mu_sorted[:n_modes]]


def plate_ssss_analytic_mu(m: int, nhalf: int, a: float, b: float) -> float:
    """解析 Laplacian 特征值 mu_mn = (m pi/a)^2 + (n pi/b)^2（同一物理量的精确值）."""
    return (m * math.pi / a) ** 2 + (nhalf * math.pi / b) ** 2


def plate_ssss_frequency(m: int, nhalf: int, a: float, b: float,
                         E: float, nu: float, rho: float, h: float) -> float:
    """由解析 mu 直接给出频率（D 用 Kirchhoff 定义，与模块独立书写）."""
    D = E * h ** 3 / (12.0 * (1.0 - nu ** 2))
    mu = plate_ssss_analytic_mu(m, nhalf, a, b)
    return math.sqrt(mu ** 2 * D / (rho * h)) / (2.0 * math.pi)


# ---------------------------------------------------------------------------
# 3. IEC 61672-1 Table 3：A 计权标称值（0.1 dB 取整的规范表，非解析式）
# ---------------------------------------------------------------------------
IEC_61672_TABLE_3: Dict[float, float] = {
    10.0: -70.4, 12.5: -63.4, 16.0: -56.7, 20.0: -50.5, 25.0: -44.7,
    31.5: -39.4, 40.0: -34.6, 50.0: -30.2, 63.0: -26.2, 80.0: -22.5,
    100.0: -19.1, 125.0: -16.1, 160.0: -13.4, 200.0: -10.9, 250.0: -8.6,
    315.0: -6.6, 400.0: -4.8, 500.0: -3.2, 630.0: -1.9, 800.0: -0.8,
    1000.0: 0.0, 1250.0: 0.6, 1600.0: 1.0, 2000.0: 1.2, 2500.0: 1.3,
    3150.0: 1.2, 4000.0: 1.0, 5000.0: 0.5, 6300.0: -0.1, 8000.0: -1.1,
    10000.0: -2.5, 12500.0: -4.3, 16000.0: -6.6, 20000.0: -9.3,
}


def aw_iec_table(f: float) -> float:
    """IEC 61672-1 Table 3 标称 A 计权值（查表，不经解析式）."""
    if f not in IEC_61672_TABLE_3:
        raise KeyError(f"{f} not in IEC 61672-1 Table 3")
    return IEC_61672_TABLE_3[f]


# ---------------------------------------------------------------------------
# 4. 声学工程式（教科书形式，与模块书写不同）
# ---------------------------------------------------------------------------
AIR_RHO0 = 1.225
AIR_C0 = 343.0


def tl_normal_incidence_exact(f: float, mpp: float, rho0: float = AIR_RHO0,
                              c0: float = AIR_C0) -> float:
    """正入射无限板隔声量（Bies & Hansen, Eng. Noise Control, eq. 5.x）.

    |tau| = 1 / (1 + (omega m''/2 rho0 c0)^2) ——用阻抗比 |z|^2/(4 (rho c)^2) 形式重写。
    """
    z = 2.0 * math.pi * f * mpp                      # 面阻抗幅值 N s/m^3
    tau = 1.0 / (1.0 + (z / (2.0 * rho0 * c0)) ** 2)
    return -10.0 * math.log10(tau)


def tl_field_engineering(f: float, mpp: float) -> float:
    """场入射工程式 TL ~= 20 log10(m'' f) - 47.2 dB（船体声学常规取值）."""
    return 20.0 * math.log10(mpp * f) - 47.2


def coincidence_frequency_engineering(E: float, rho: float, h: float, nu: float,
                                      c0: float = AIR_C0) -> float:
    """吻合频率：用弯曲波速匹配 c_B = c0/sin(theta) 的标准书写式."""
    cL = math.sqrt(E / (rho * (1.0 - nu ** 2)))       # 板纵波速（薄板）
    return (c0 ** 2 / (2.0 * math.pi * h)) * math.sqrt(12.0) / cL


def combine_db(levels: Sequence[float]) -> float:
    """能量叠加（用总能量再取对数，避免逐项 log10 的抗噪写法）."""
    energy = sum(10.0 ** (lv / 10.0) for lv in levels)
    return 10.0 * math.log10(energy)


# ---------------------------------------------------------------------------
# 5. 单自由度隔振：时域 RK4 积分（完全不使用传递率公式）
# ---------------------------------------------------------------------------
def sdof_transmissibility_rk4(f: float, f_n: float, zeta: float,
                             n_periods: int = 60, n_steps_per_period: int = 200,
                             ) -> float:
    """基础激励 SDOF 的绝对位移传递率，靠**时域积分**取稳态幅值比得到。

    相对坐标 z = x - y：z'' + 2 zeta wn z' + wn^2 z = w^2 Y sin(wt)。
    积分到稳态后，绝对位移 x = z + Y sin(wt)，取最后一个周期的 x 幅值 / Y。
    """
    wn = 2.0 * math.pi * f_n
    w = 2.0 * math.pi * f
    Y = 1.0
    dt = (2.0 * math.pi / w) / n_steps_per_period
    n_tot = n_periods * n_steps_per_period

    def rhs(t: float, z: float, zd: float) -> Tuple[float, float]:
        zdd = w * w * Y * math.sin(w * t) - 2.0 * zeta * wn * zd - wn * wn * z
        return zd, zdd

    z = 0.0
    zd = 0.0
    t = 0.0
    for _ in range(n_tot):
        k1z, k1v = rhs(t, z, zd)
        k2z, k2v = rhs(t + 0.5 * dt, z + 0.5 * dt * k1z, zd + 0.5 * dt * k1v)
        k3z, k3v = rhs(t + 0.5 * dt, z + 0.5 * dt * k2z, zd + 0.5 * dt * k2v)
        k4z, k4v = rhs(t + dt, z + dt * k3z, zd + dt * k3v)
        z += dt * (k1z + 2 * k2z + 2 * k3z + k4z) / 6.0
        zd += dt * (k1v + 2 * k2v + 2 * k3v + k4v) / 6.0
        t += dt

    # 稳态幅值：最后一周期内 x 的峰值
    period_start = t - 2.0 * math.pi / w
    tt = period_start
    zz, zzd = z, zd
    peak = 0.0
    steps = n_steps_per_period
    for _ in range(steps):
        x_abs = zz + Y * math.sin(w * tt)
        peak = max(peak, abs(x_abs))
        k1z, k1v = rhs(tt, zz, zzd)
        k2z, k2v = rhs(tt + 0.5 * dt, zz + 0.5 * dt * k1z, zzd + 0.5 * dt * k1v)
        k3z, k3v = rhs(tt + 0.5 * dt, zz + 0.5 * dt * k2z, zzd + 0.5 * dt * k2v)
        k4z, k4v = rhs(tt + dt, zz + dt * k3z, zzd + dt * k3v)
        zz += dt * (k1z + 2 * k2z + 2 * k3z + k4z) / 6.0
        zzd += dt * (k1v + 2 * k2v + 2 * k3v + k4v) / 6.0
        tt += dt
    return peak / Y


# ---------------------------------------------------------------------------
# 6. 激励频率（ITTC / 船用柴油机常规关系，独立书写）
# ---------------------------------------------------------------------------
def shaft_and_blade_freq(rpm: float, n_blades: int) -> Tuple[float, float]:
    fs = rpm / 60.0
    return fs, n_blades * fs


def diesel_firing_freq(rpm: float, n_cyl: int, stroke: int) -> float:
    """4 冲程曲轴每 2 转每缸点火一次 -> n_cyl*rpm/(60*2)；2 冲程每转一次."""
    return n_cyl * rpm / 60.0 / (2.0 if stroke == 4 else 1.0)


# ---------------------------------------------------------------------------
# 7. 振型边条件残差（数值微分，不用模块的振型公式）
# ---------------------------------------------------------------------------
def bc_residual(x: Sequence[float], w: Sequence[float], L: float,
                ends: str = "both") -> Dict[str, float]:
    """用中心差分估计端点 |w|/max|w| 与 |dw/dx|*L/max|w|（无量纲残差）."""
    xs = np.asarray(x, dtype=float)
    ws = np.asarray(w, dtype=float)
    scale = float(np.max(np.abs(ws))) or 1.0
    dx = xs[1] - xs[0]
    dw = np.gradient(ws, dx)
    out: Dict[str, float] = {}

    def at(i: int, name: str) -> None:
        out[f"w_{name}"] = abs(ws[i]) / scale
        out[f"slope_{name}"] = abs(dw[i]) * L / scale

    if ends in ("both", "left"):
        at(0, "left")
    if ends in ("both", "right"):
        at(len(xs) - 1, "right")
    return out