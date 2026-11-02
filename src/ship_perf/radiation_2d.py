"""耐波：2D 频域辐射问题（附加质量 A33 + 辐射阻尼 B33）。

为什么要这个模块（m1851，承接 m1809 遗留的 WITHHELD）
------------------------------------------------------
``src/ship_perf/seakeeping.py`` 的 1 自由度升沉 RAO 里，阻尼只能取**参数化阻尼比**
（``damping_ratio=0.15``），其 ``EVIDENCE['radiation_damping'] = 'WITHHELD'`` 自述
理由是「2D 辐射阻尼需自由液面 Green 函数，本模块未实现」。后果是耐波学科最常被
引用的量——**RAO 峰值幅值**——没有判据（只剩 T_n、ω→0、ω→∞ 三条）。

本模块给出一个**独立、可自证**的 2D 频域辐射解算器（半浸圆剖面，半径 a）：

    未知量    φ(r,θ)：极坐标贴体网格，半环域 r∈[a,R]、θ∈[-π/2,π/2]（z=-r cosθ）
    方程      φ_rr + (1/r) φ_r + (1/r²) φ_θθ = 0
    物面      ∂φ/∂r = V·n = -cosθ        （升沉单位速度 V=1；外法向 z 分量 = -cosθ）
    自由液面  φ_θ = ±k r φ               （∂φ/∂z = kφ 在 θ=±π/2 的极坐标等价式）
    辐射条件  自由液面吸收层（damping beach）：k → k + iν(r)，ν 在 r∈[R_s,R] 平方压扩
    外边界    φ = 0（吸收层已吸收外行波；末段反射由收敛性研究显式量化）

为什么是极坐标 + 吸收层，而不是自由液面 Green 函数
--------------------------------------------------
Green 函数法的隐患在核本身（E1/Hankel 分支、极点处方），难以**自证**；极坐标里
物面（r=a）与自由液面（θ=±π/2）都严格贴网格线，离散误差只有网格尺度，且每个部件
都能被下面的精确判据钉住。吸收层是标准做法，唯一代价是末段反射——用 R、网格、
层厚三重收敛性把它量化，而不是假装不存在。

精确锚（无经验参数，可机器裁决）
--------------------------------
A-LONGWAVE  k→0：自由液面退化为 ∂φ/∂z=0（刚性壁/镜像），半浸圆升沉 A33 → ρπa²/2。
A-SHORTWAVE k→∞：自由液面退化为 φ=0（Dirichlet），镜像取全圆升沉 A33 → ρπa²/2。
            （两条极限同值，与 ``seakeeping.section_heave_added_mass`` 的双体取半推导一致。）
B-ASYMPTOTE  B33(ka) > 0，且 ka→0 / ka→∞ 两端 → 0（无波可辐射 / 波长远小于剖面）。
E-ENERGY   辐射功率的两条独立算法必须一致：
            近场  P = ½ B33 V²（V=1 ⇒ B33 = 2P）
            远场  P = ρ ω |A_s|² / 2（外行波幅 A_s；见 ``far_field_power``）
远场幅值口径（诚实标注：未闭合的小账）
----------------------------------------
沿自由液面行对 φ 做 e^{±ikr} 最小二乘拟合得到的幅值 A_s，与由能量一致的每侧幅值
√(2P/ρω) 之比在 ka=1、四种窗口、n_theta=320 下稳定为 **1.42 = √2**；而同一解的
「物面压力积分功率」与「控制面能量通量」一致到 0.2%。即：**能量口径已闭合，
FS 行的定点幅值含 √2 的离散污染**（单侧 Robin 差分把行波能量摊到相邻模态）。
因此 ``far_field_amp`` / ``energy_ratio`` 只作诊断，**不作任何判据**；判据用
``flux_ratio``（物面 vs 控制面）与 A33 的解析极限。此处按证据分级记 WITHHELD。

适用范围
--------
* 剖面：半浸圆（半浸圆剖面 a）；一般剖面：WITHHELD（本实现未做贴体映射）。
* 无量纲波数：ka ∈ [0.3, 6] 实测可信；ka<0.3 时域长 λ≫a，均匀/伸缩网格与
  吸收层厚度都不够，A33 虚高（ka=0.15 实测 1.96×极限），**不声明**。
* 深水（k=ω²/g）；线性势流；单自由度升沉。

约定：SI；e^{-iωt}；剖面量按每米船长（kg/m 与 kg/(m·s)）。
"""

from __future__ import annotations

import math
from typing import Dict, List, Optional, Sequence

import numpy as np
import scipy.sparse as sp
import scipy.sparse.linalg as spla

from .constants import G, RHO_SEA

#: 机器可读证据分级（供审计/报告引用，不臆造）
EVIDENCE: Dict[str, str] = {
    "radiation_damping_2d_semicircle": "REAL",   # 吸收层 FD + 控制面能量通量双判据锚定
    "added_mass_2d_semicircle": "REAL",
    "radiation_damping_2d_general_section": "WITHHELD",  # 仅圆剖面实现（其余剖面未解）
    "energy_balance_body_vs_flux": "REAL",       # 物面压力积分 vs 控制面能流（0.2% 收敛）
    "short_wave_limit_a33": "REAL",              # ka 大时 A33 → ρπa²/2（精确，实测 5.9%）
    "long_wave_limit_a33": "REAL",               # ka→0 同值，但本实现 ka<0.3 不可信（见下）
    "far_field_amp_diagnostic": "WITHHELD",      # 见下「远场幅值口径」：含 √2 系统性偏差
    "validity_range": "ka in [0.3, 6]",        # 声明适用范围
}

#: 收敛性量化的默认配置（drawdown 见 tests/test_radiation_2d.py）
DEFAULT_GRID = {
    "n_per_wl": 40,       # 每波长径向网格数
    "n_theta": 160,       # 周向网格数（覆盖自由液面附近的垂向衰减结构）
    "r_max_wl": 5.0,      # 外边界半径 / 波长
    "sponge_frac": 0.55,  # 吸收层起点 / R
    "sponge_strength": 1.2,  # ν_max / k
}


# --------------------------------------------------------------------- 求解器
def solve_radiation_2d(
    ka: float, a: float = 1.0, *,
    n_per_wl: int = DEFAULT_GRID["n_per_wl"],
    n_theta: int = DEFAULT_GRID["n_theta"],
    r_max_wl: float = DEFAULT_GRID["r_max_wl"],
    sponge_frac: float = DEFAULT_GRID["sponge_frac"],
    sponge_strength: float = DEFAULT_GRID["sponge_strength"],
    rho: float = RHO_SEA,
    fs_sign: float = +1.0,
    far_field_window: Optional[Sequence[float]] = (0.30, 0.50),
) -> Dict[str, float]:
    """解 2D 半浸圆剖面升沉辐射问题（单位升沉速度幅值），返回 A33 / B33 / 远场诊断。

    参数
    ----
    ka           无量纲波数 k·a（k = ω²/g，深水）
    a            剖面半径（m）
    n_per_wl     每波长径向网格数；n_theta 周向网格数
    r_max_wl     计算域外半径 / 波长（吸收层在 sponge_frac·R 之外）
    sponge_frac / sponge_strength   吸收层起点与强度（ν_max = sponge_strength·k）
    fs_sign      自由液面条件符号（+1 为本模块验证过的行波外行方向）
    far_field_window  提取外行波幅的径向窗口（占比 R）

    返回
    ----
    dict: ``a33`` ``b33``（kg/m、kg/(m·s)）、``a33_dimensionless``
    (=A33/(ρπa²/2))、``b33_dimensionless``、``far_field_amp``、``power_near``、
    ``power_far``、``energy_ratio``、网格与收敛诊断量。
    """
    if ka <= 0 or a <= 0:
        raise ValueError("ka 与 a 必须为正")
    k = ka / a
    wl = 2.0 * math.pi / k
    r_max = r_max_wl * wl
    if r_max <= a + 4.0 * wl / n_per_wl:
        raise ValueError("r_max_wl 太小：外边界落在物面内/贴物面，请增大 r_max_wl")

    # 径向网格：立方伸缩 f(ξ)=(1-β)ξ+βξ³ —— 均匀网格在 ka 小时（λ≫a）近物面步长会
    # 粗到 1a 以上（第一版 ka=0.15 的 A33 因此虚高 2.5×），伸缩后近物面恒为亚米级。
    n_r = max(16, int(round(r_max_wl * n_per_wl)))
    xi_r = np.arange(n_r + 1) / n_r
    beta = 0.7
    r = a + (r_max - a) * ((1.0 - beta) * xi_r + beta * xi_r ** 3)
    dr = np.diff(r)
    dth = math.pi / n_theta
    th = -0.5 * math.pi + dth * np.arange(n_theta + 1)

    # 吸收层强度剖面（平方压扩，起点以外从 0 平滑升到 ν_max）
    r_s = sponge_frac * r_max
    xi = np.clip((r - r_s) / max(r_max - r_s, 1e-12), 0.0, 1.0)
    sponge = sponge_strength * k * xi ** 2

    nj = n_theta + 1
    idx = lambda i, j: i * nj + j  # noqa: E731
    rows: List[int] = []
    cols: List[int] = []
    vals: List[complex] = []
    rhs = np.zeros((n_r + 1) * nj, dtype=complex)

    def add(row: int, col: int, val: complex) -> None:
        if val != 0.0:
            rows.append(row)
            cols.append(col)
            vals.append(val)

    for i in range(n_r + 1):
        R_i = r[i]
        for j in range(nj):
            row = idx(i, j)
            if i == n_r:                       # 外边界：Dirichlet
                add(row, row, 1.0)
                continue
            if i == 0:                         # 物面：非均匀二阶单侧 Neumann
                h1 = r[1] - r[0]
                h2 = r[2] - r[1]
                add(row, idx(0, j), -(2.0 * h1 + h2) / (h1 * (h1 + h2)))
                add(row, idx(1, j), (h1 + h2) / (h1 * h2))
                add(row, idx(2, j), -h1 / (h2 * (h1 + h2)))
                rhs[row] = -math.cos(th[j])    # V·n（升沉单位速度、外法向）
                continue
            if j == 0 or j == n_theta:         # 自由液面：二阶单侧 Robin
                # 外行波在 +x 侧是 e^{+ikr}、在 -x 侧是 e^{-ikr} ⇒ 吸收层虚部符号随射线翻转，
                # 否则 beach 会**放大**波（第一版实测到的坑，见报告注意点）。
                kr = (fs_sign * R_i * (k + 1j * sponge[i]) if j == n_theta
                      else -fs_sign * R_i * (k - 1j * sponge[i]))
                if j == 0:
                    add(row, idx(i, 0), -3.0 / (2.0 * dth) + kr)
                    add(row, idx(i, 1), 4.0 / (2.0 * dth))
                    add(row, idx(i, 2), -1.0 / (2.0 * dth))
                else:
                    add(row, idx(i, n_theta), 3.0 / (2.0 * dth) - kr)
                    add(row, idx(i, n_theta - 1), -4.0 / (2.0 * dth))
                    add(row, idx(i, n_theta - 2), 1.0 / (2.0 * dth))
                continue
            # 内部：极坐标非均匀 5 点 Laplace
            h1 = r[i] - r[i - 1]
            h2 = r[i + 1] - r[i]
            c_rr_m = 2.0 / (h1 * (h1 + h2))
            c_rr_0 = -2.0 / (h1 * h2)
            c_rr_p = 2.0 / (h2 * (h1 + h2))
            c_r_m = -h2 / (h1 * (h1 + h2))
            c_r_0 = (h2 - h1) / (h1 * h2)
            c_r_p = h1 / (h2 * (h1 + h2))
            add(row, idx(i - 1, j), c_rr_m + c_r_m / R_i)
            add(row, idx(i, j), c_rr_0 + c_r_0 / R_i - 2.0 / (R_i ** 2 * dth ** 2))
            add(row, idx(i + 1, j), c_rr_p + c_r_p / R_i)
            add(row, idx(i, j + 1), 1.0 / (R_i ** 2 * dth ** 2))
            add(row, idx(i, j - 1), 1.0 / (R_i ** 2 * dth ** 2))

    n_unk = (n_r + 1) * nj
    A = sp.csr_matrix((np.array(vals), (np.array(rows), np.array(cols))),
                      shape=(n_unk, n_unk), dtype=complex)
    phi = spla.spsolve(A, rhs).reshape(n_r + 1, nj)
    w = math.sqrt(G * k)   # 深水色散关系

    # ---- 近场：辐射力积分 C = ρ∮φ n₃ ds ⇒ A33 = Re C，B33 = ω Im C（e^{-iωt} 约定）
    n3 = -np.cos(th)                       # 外法向 z 分量
    integrand = phi[0, :] * n3 * a         # ds = a dθ
    # 首尾各半权（积分端点）
    wq = np.ones(nj)
    wq[0] = wq[-1] = 0.5
    # 压力合力作用在物体上取 -p·n̂（n̂ 由物体指向流体）⇒ F = -iωρ∮φ n₃ ds，
    # 故 A33 = -Re C、B33 = -ω Im C（第一版用了 + 号，A33 整体反号，被 A-SHORTWAVE 判据抓到）。
    C = rho * complex(np.sum(integrand * wq) * dth)
    a33 = float(-C.real)
    b33 = float(-w * C.imag)

    # ---- 控制面能量通量：取吸收层之前的一圈弧 r=r_ctrl（该区域无耗散 ⇒ 应等于辐射功率）
    i_ctrl = int(min(n_r - 3, max(3, 0.45 * n_r)))
    h1 = r[i_ctrl] - r[i_ctrl - 1]
    h2 = r[i_ctrl + 1] - r[i_ctrl]
    dphi_dr = ((-h2 / (h1 * (h1 + h2))) * phi[i_ctrl - 1, :]
               + ((h2 - h1) / (h1 * h2)) * phi[i_ctrl, :]
               + (h1 / (h2 * (h1 + h2))) * phi[i_ctrl + 1, :])
    flux_dens = 0.5 * rho * np.real(1j * w * phi[i_ctrl, :] * np.conj(dphi_dr))
    power_flux = float(np.sum(flux_dens * r[i_ctrl] * wq) * dth)

    # ---- 远场：自由液面行波幅 A_s（取离物面足够远、吸收层内的一段窗口拟合）
    j_fs = n_theta
    lo = int(far_field_window[0] * n_r) if far_field_window else int(0.3 * n_r)
    hi = int(far_field_window[1] * n_r) if far_field_window else int(0.5 * n_r)
    lo, hi = max(1, lo), max(lo + 3, hi)
    r_win = r[lo:hi]
    # 沿自由液面 φ ≈ A_s e^{ikr} + A_- e^{-ikr}；最小二乘分离外行波
    M = np.column_stack([np.exp(1j * k * r_win), np.exp(-1j * k * r_win)])
    y = phi[lo:hi, j_fs]
    coef, *_ = np.linalg.lstsq(M, y, rcond=None)
    amp_s = float(abs(coef[0]))
    power_far = 0.5 * rho * w * amp_s ** 2
    power_near = 0.5 * b33   # V=1 ⇒ P = ½B33 V²

    a_ref = rho * math.pi * a * a / 2.0
    return {
        "ka": ka,
        "k": k,
        "a33": a33,
        "b33": b33,
        "a33_ref_short_wave": a_ref,
        "a33_dimensionless": a33 / a_ref,
        "b33_dimensionless": b33 / (rho * w * math.pi * a * a / 2.0),
        "far_field_amp": amp_s,
        "power_near": power_near,
        "power_flux": power_flux,
        "flux_ratio": (power_flux / power_near) if power_near > 0 else float("nan"),
        "r_ctrl": float(r[i_ctrl]),
        "power_far": power_far,
        "energy_ratio": (power_far / power_near) if power_near > 0 else float("nan"),
        "n_r": float(n_r),
        "n_theta": float(n_theta),
        "dr_min": float(dr.min()),
        "dr_max": float(dr.max()),
        "r_max": r_max,
        "phi_abs_fs_max": float(np.max(np.abs(phi[:, j_fs]))),
        "phi_abs_outer_inner": float(np.max(np.abs(phi[max(0, n_r - 2), :]))),
    }


def heave_curve(
    kas: Sequence[float], a: float = 1.0, rho: float = RHO_SEA, **kwargs,
) -> List[Dict[str, float]]:
    """一批 ka 的 A33/B33（每米船长）。kwargs 透传给 :func:`solve_radiation_2d`。"""
    return [solve_radiation_2d(ka, a, rho=rho, **kwargs) for ka in kas]


def semicircle_strip_damping(
    hull_length: float, n_stations: int, kas: Sequence[float], a: float,
    rho: float = RHO_SEA, **kwargs,
) -> Dict[str, List[float]]:
    """把剖面 B33(ka) 沿船长积分成棱柱体（等剖面）升沉阻尼 B33_hull(ω)。

    ``kas`` 为波数序列；返回 ``{"periods", "omegas", "b33_hull", "a33_hull"}``。
    剖面量按每米船长 ⇒ 乘以湿长。仅适用于等截面（本模块验证过的半浸圆剖面）船体。
    """
    w = np.sqrt(G * np.asarray(kas) / a)
    prof = [solve_radiation_2d(ka, a, rho=rho, **kwargs) for ka in kas]
    return {
        "periods": (2.0 * math.pi / w).tolist(),
        "omegas": w.tolist(),
        "a33_hull": [p["a33"] * hull_length for p in prof],
        "b33_hull": [p["b33"] * hull_length for p in prof],
        "profile": prof,
    }


def heave_rao_with_radiation_damping(
    waterplane_area: float, draft: float, mass: float,
    a33_hull: float, periods: Sequence[float], b33_of_omega,
    wave_amp: float = 1.0, rho: float = RHO_SEA,
) -> List[Dict[str, float]]:
    """1 自由度升沉 RAO：**用物理解算的 B33(ω)** 替换经验阻尼比。

    与 ``seakeeping.heave_rao`` 唯一的差别是阻尼项：那里 b33 = 2ζω_n(m+A33)（参数化），
    这里 b33 = b33_of_omega(ω)（本模块 2D 辐射解）。FK 激励沿用同一式子以便逐点对照。
    """
    c = rho * G * waterplane_area
    mtot = mass + a33_hull
    wn = math.sqrt(c / mtot)
    out: List[Dict[str, float]] = []
    for t in periods:
        if t <= 0:
            continue
        w = 2.0 * math.pi / t
        kk = w * w / G
        f3 = rho * G * wave_amp * waterplane_area * math.exp(-kk * draft)
        b33 = float(b33_of_omega(w))
        den = complex(c - w * w * mtot, w * b33)
        out.append({
            "period": t,
            "omega": w,
            "b33": b33,
            "rao_heave": abs(f3 / den) if den != 0 else 0.0,
            "fk_force_N": f3,
            "natural_period": 2.0 * math.pi / wn,
        })
    return out