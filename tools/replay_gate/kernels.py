#!/usr/bin/env python3
"""复现门判据内核（纯标准库，可在**干净 venv** 里独立执行）。

设计约束（这是复现门的命门）
----------------------------
* 只 import 标准库 —— 干净 venv（``python3 -m venv --without-pip``）里没有任何
  第三方包，内核必须能在那里跑起来，否则「环境依赖缺失」这一类根因就无法被区分
  出来（所有失败都会退化成 ImportError）。
* 每个 kernel 是 ``fn(**params) -> float``，**无全局可变状态**，结果可复跑。
* 数值内核（LBM / FEM / 时间积分）是真算，不是把解析式原样抄一遍：申报值来自
  解析/交付产物，内核独立算出数值解，二者差 > 容差即判**真实数值漂移**。

kernel 与物理域
---------------
阻力       froude / ittc57_cf / rt_identity
结构       plate_buckling_sigma_cr / rayleigh_ritz_k
耐波       wave_length / roll_period / roll_decay_period_rk4 / heave_omega / encounter_omega
LBM        lbm_channel_umax（D2Q9 BGK 真迭代）/ lbm_mass_total / shear_wave_decay
FEM        fem_bar_tip_uniform / fem_beam_tip / fem_quad_area
DEM        dem_hertz_force / dem_collision_v1_after / dem_ballistic_drop
负控       numpy_only（人为第三方依赖）/ stochastic_probe（未种子随机）
"""
from __future__ import annotations

import math
import random

G = 9.80665


# ── 阻力 ────────────────────────────────────────────────────────────────
def froude(V: float, L: float, g: float = G) -> float:
    """Fr = V / sqrt(gL)。"""
    return V / math.sqrt(g * L)


def ittc57_cf(Re: float) -> float:
    """ITTC-1957 平板摩擦阻力系数 Cf = 0.075/(log10(Re)-2)^2。"""
    return 0.075 / (math.log10(Re) - 2.0) ** 2


def rt_identity(ct: float, rho: float, V: float, S: float) -> float:
    """总阻力恒等式 Rt = Ct · ½ρV²S [N]。"""
    return ct * 0.5 * rho * V * V * S


# ── 结构 ────────────────────────────────────────────────────────────────
def plate_buckling_sigma_cr(t: float, b: float, e: float = 2.06e11,
                            nu: float = 0.3, k: float = 4.0) -> float:
    """长简支板条单轴屈曲应力 σ_cr = k·π²E(t/b)²/(12(1-ν²)) [Pa]（Timoshenko）。"""
    return k * math.pi ** 2 * e * t * t / (12.0 * (1.0 - nu * nu) * b * b)


def rayleigh_ritz_k(a: float, b: float, m: int = 0) -> float:
    """长板屈曲系数 k = min_m (m·b/a + a/(m·b))²（双正弦模态能量商对半波数取最小）。

    ``m=0`` 表示在 m=1..8 上取最小；a/b 为整数时最小值恰为 4（简支板经典结果）。
    踩坑记录：把 m 固定成 1、申报值又写 4，会在 a/b=2 时得 6.25，被复现门判成
    «真实数值漂移» —— 门抓到的第一个错误其实是本判据自己的（已修）。
    """
    if m and m >= 1:
        return (m * b / a + a / (m * b)) ** 2
    return min((mm * b / a + a / (mm * b)) ** 2 for mm in range(1, 9))


# ── 耐波 ────────────────────────────────────────────────────────────────
def wave_length(T: float, g: float = G) -> float:
    """深水波长 λ = gT²/(2π)。"""
    return g * T * T / (2.0 * math.pi)


def roll_period(GM: float, k_r: float, g: float = G) -> float:
    """横摇固有周期 T = 2π·k_r/sqrt(g·GM)。"""
    return 2.0 * math.pi * k_r / math.sqrt(g * GM)


def heave_omega(A_w: float, m: float, a33: float, rho: float = 1025.0, g: float = G) -> float:
    """垂荡固有频率 ω = sqrt(ρgA_w/(m+a33))。"""
    return math.sqrt(rho * g * A_w / (m + a33))


def encounter_omega(omega: float, V: float, beta_deg: float, g: float = G) -> float:
    """遭遇频率 ω_e = |ω - ω²V·cosβ/g|。"""
    return abs(omega - omega * omega * V * math.cos(math.radians(beta_deg)) / g)


def roll_decay_period_rk4(GM: float, k_r: float, zeta: float,
                          dt: float = 0.005, t_end: float = 60.0, g: float = G) -> float:
    """把横摇衰减方程 φ'' + 2ζω_nφ' + ω_n²φ = 0 用 RK4 真积分，量零穿越周期。

    与解析阻尼周期 T_d = T_n/sqrt(1-ζ²) 对比：这是**数值积分 vs 解析**，
    不是把公式抄两遍。
    """
    wn = math.sqrt(g * GM) / k_r
    phi, dphi, t = 0.2, 0.0, 0.0
    crossings: list[float] = []
    prev = phi

    def acc(p: float, dp: float) -> float:
        return -2.0 * zeta * wn * dp - wn * wn * p

    while t < t_end:
        k1p, k1d = dphi, acc(phi, dphi)
        k2p, k2d = dphi + 0.5 * dt * k1d, acc(phi + 0.5 * dt * k1p, dphi + 0.5 * dt * k1d)
        k3p, k3d = dphi + 0.5 * dt * k2d, acc(phi + 0.5 * dt * k2p, dphi + 0.5 * dt * k2d)
        k4p, k4d = dphi + dt * k3d, acc(phi + dt * k3p, dphi + dt * k3d)
        phi_n = phi + dt / 6.0 * (k1p + 2 * k2p + 2 * k3p + k4p)
        dphi = dphi + dt / 6.0 * (k1d + 2 * k2d + 2 * k3d + k4d)
        t += dt
        if prev != 0.0 and phi_n * prev < 0.0:  # 零穿越 → 半周期
            frac = abs(prev) / (abs(prev) + abs(phi_n))
            crossings.append(t - dt + frac * dt)
        prev = phi_n
        phi = phi_n
        if len(crossings) >= 6:
            break
    if len(crossings) < 4:
        raise RuntimeError("RK4 横摇积分未取得足够零穿越点（t_end 太短？）")
    return 2.0 * (crossings[-1] - crossings[1]) / (len(crossings) - 2)


# ── LBM（D2Q9 BGK，真迭代） ──────────────────────────────────────────────
_W = [4.0 / 9, 1.0 / 9, 1.0 / 9, 1.0 / 9, 1.0 / 9, 1.0 / 36, 1.0 / 36, 1.0 / 36, 1.0 / 36]
_CX = [0, 1, 0, -1, 0, 1, -1, -1, 1]
_CY = [0, 0, 1, 0, -1, 1, 1, -1, -1]
_OPP = [0, 3, 4, 1, 2, 7, 8, 5, 6]


def _channel_collide_stream(f, ny, inv_tau, force):
    """一步「迁移+反弹 + BGK 碰撞 + 体积力」（力在碰撞**之后**注入，见下注）。

    注意（踩过的坑）：把体积力加在碰撞**之前**并让 feq 用加力前的 u 计算，等于每步
    只留下 (1-1/τ) 份动量 —— ν=0.2 时解析解差 11 倍、ν=0.05 时还会反号。正确做法是
    「碰撞后再注入力」，或使用带 (1-1/2τ) 因子的 Guo 源项。本实现取前者，并在
    tests/test_ledger_contract_and_replay_gate.py 里锚定到解析解 u_max=F·H²/(8ν)。
    """
    fnew = [[[0.0] for _ in range(ny)] for _ in range(9)]
    for i in range(9):
        opp = _OPP[i]
        cy = _CY[i]
        for y in range(1, ny - 1):
            yt = y + cy
            if yt == 0 or yt == ny - 1:
                fnew[opp][y][0] += f[i][y][0]   # 半程反弹壁
            else:
                fnew[i][yt][0] += f[i][y][0]
    for y in range(1, ny - 1):
        r = 0.0
        u = 0.0
        for i in range(9):
            fi = fnew[i][y][0]
            r += fi
            u += fi * _CX[i]
        u /= r
        for i in range(9):
            cu = _CX[i] * u
            feq = _W[i] * r * (1.0 + 3.0 * cu + 4.5 * cu * cu - 1.5 * u * u)
            f[i][y][0] = fnew[i][y][0] - inv_tau * (fnew[i][y][0] - feq) + 3.0 * _W[i] * _CX[i] * force
    return f


def lbm_channel_umax(H: int = 19, nu: float = 0.1, force: float = 6e-5,
                     steps: int = 20000, tol: float = 1e-10) -> float:
    """D2Q9 BGK 通道 Poiseuille 流中心线最大速度（格子单位）。

    通道宽 H 格子（上下无滑移壁，半程反弹），x 方向周期（解与 x 无关 → 一列即可）。
    解析解 u_max = force·H²/(8ν)。判据容差取 3%（实测 0.03%~1%）。
    """
    ny = H + 2
    f = [[[_W[i]] for _ in range(ny)] for i in range(9)]
    inv_tau = 1.0 / (3.0 * nu + 0.5)
    prev = 0.0
    for _ in range(steps):
        f = _channel_collide_stream(f, ny, inv_tau, force)
        u_max = 0.0
        for y in range(1, ny - 1):
            r = sum(f[i][y][0] for i in range(9))
            u = abs(sum(f[i][y][0] * _CX[i] for i in range(9)) / r)
            if u > u_max:
                u_max = u
        if prev and abs(u_max - prev) < tol * max(u_max, 1e-30):
            return u_max
        prev = u_max
    return prev


def lbm_mass_total(H: int = 15, nu: float = 0.2, force: float = 6e-5, steps: int = 400) -> float:
    """质量守恒检查：通道内总密度（初值 1.0/格 → 守恒量 ≈ H）。"""
    ny = H + 2
    f = [[[_W[i]] for _ in range(ny)] for i in range(9)]
    inv_tau = 1.0 / (3.0 * nu + 0.5)
    for _ in range(steps):
        f = _channel_collide_stream(f, ny, inv_tau, force)
    return sum(sum(f[i][y][0] for i in range(9)) for y in range(1, ny - 1))


def shear_wave_decay(nu: float, k: float, t: float, u0: float = 1.0) -> float:
    """剪切波衰减 u = u0·exp(-νk²t)（LBM 的线性响应解析解）。"""
    return u0 * math.exp(-nu * k * k * t)


# ── FEM ─────────────────────────────────────────────────────────────────
def _solve(A: list[list[float]], b: list[float]) -> list[float]:
    """小规模高斯消元（部分主元）。"""
    n = len(b)
    M = [row[:] + [b[i]] for i, row in enumerate(A)]
    for c in range(n):
        piv = max(range(c, n), key=lambda r: abs(M[r][c]))
        if abs(M[piv][c]) < 1e-300:
            raise ZeroDivisionError("FEM 刚度阵奇异（边界条件缺失？）")
        M[c], M[piv] = M[piv], M[c]
        for r in range(c + 1, n):
            fac = M[r][c] / M[c][c]
            if fac:
                for k in range(c, n + 1):
                    M[r][k] -= fac * M[c][k]
    x = [0.0] * n
    for r in range(n - 1, -1, -1):
        s = M[r][n] - sum(M[r][k] * x[k] for k in range(r + 1, n))
        x[r] = s / M[r][r]
    return x


def fem_bar_tip_uniform(q: float, EA: float, L: float, n_elem: int = 4) -> float:
    """均布轴向荷载杆的 FEM 端部位移（线性单元 + 一致荷载），解析 qL²/(2EA)。"""
    n = n_elem
    le = L / n
    K = [[0.0] * (n + 1) for _ in range(n + 1)]
    F = [0.0] * (n + 1)
    k = EA / le
    for e in range(n):
        K[e][e] += k
        K[e][e + 1] -= k
        K[e + 1][e] -= k
        K[e + 1][e + 1] += k
        F[e] += q * le / 2.0
        F[e + 1] += q * le / 2.0
    # 左端固定：消去 u_0
    idx = list(range(1, n + 1))
    A = [[K[i][j] for j in idx] for i in idx]
    rhs = [F[i] for i in idx]
    u = _solve(A, rhs)
    return u[-1]


def fem_beam_tip(P: float, L: float, E: float, I: float, n_elem: int = 4) -> float:
    """悬臂梁端部集中力，Hermite 三次梁单元 FEM 端部挠度，解析 PL³/(3EI)。"""
    n = n_elem
    le = L / n
    ndof = 2 * (n + 1)
    K = [[0.0] * ndof for _ in range(ndof)]
    F = [0.0] * ndof
    c = E * I / le ** 3
    ke = [[12 * c, 6 * c * le, -12 * c, 6 * c * le],
          [6 * c * le, 4 * c * le * le, -6 * c * le, 2 * c * le * le],
          [-12 * c, -6 * c * le, 12 * c, -6 * c * le],
          [6 * c * le, 2 * c * le * le, -6 * c * le, 4 * c * le * le]]
    for e in range(n):
        dofs = [2 * e, 2 * e + 1, 2 * e + 2, 2 * e + 3]
        for a in range(4):
            for b in range(4):
                K[dofs[a]][dofs[b]] += ke[a][b]
    F[2 * n] = P                       # 端部横向集中力
    idx = list(range(2, ndof))         # 固定端 u0=θ0=0
    A = [[K[i][j] for j in idx] for i in idx]
    u = _solve(A, [F[i] for i in idx])
    return abs(u[-2])


def fem_quad_area(xs: list[float], ys: list[float]) -> float:
    """双线性四节点等参元的面积（2×2 Gauss 积分 det J），解析=多边形面积。"""
    g = 1.0 / math.sqrt(3.0)
    gp = [(-g, -g), (g, -g), (g, g), (-g, g)]
    area = 0.0
    for xi, eta in gp:
        # 形函数对自然坐标的导数
        dN_dxi = [-(1 - eta), (1 - eta), (1 + eta), -(1 + eta)]
        dN_deta = [-(1 - xi), -(1 + xi), (1 + xi), (1 - xi)]
        dx_dxi = sum(dN_dxi[i] * xs[i] for i in range(4)) / 4.0
        dx_deta = sum(dN_deta[i] * xs[i] for i in range(4)) / 4.0
        dy_dxi = sum(dN_dxi[i] * ys[i] for i in range(4)) / 4.0
        dy_deta = sum(dN_deta[i] * ys[i] for i in range(4)) / 4.0
        area += abs(dx_dxi * dy_deta - dx_deta * dy_dxi)
    return area


# ── DEM ─────────────────────────────────────────────────────────────────
def dem_hertz_force(E: float, nu: float, R: float, delta: float) -> float:
    """Hertz 弹性接触力 F = (4/3)E*·sqrt(R)·δ^{3/2}，E* = E/(2(1-ν²))。"""
    e_star = E / (2.0 * (1.0 - nu * nu))
    return 4.0 / 3.0 * e_star * math.sqrt(R) * delta ** 1.5


def dem_collision_v1_after(m1: float, v1: float, m2: float, v2: float, e: float = 1.0) -> float:
    """两球对心碰撞后球 1 的速度（动量守恒 + 恢复系数 e）。"""
    return ((m1 - e * m2) * v1 + (1.0 + e) * m2 * v2) / (m1 + m2)


def dem_ballistic_drop(v0: float, t: float, g: float = G) -> float:
    """自由落体位移 s = v0·t + ½gt²（DEM 时间积分器的基准）。"""
    return v0 * t + 0.5 * g * t * t


# ── 负控内核（构造根因类别用，不进正向抽样池） ────────────────────────────
def numpy_only(**kw) -> float:  # pragma: no cover - 干净 venv 里必失败
    import numpy  # noqa: F401

    return float(numpy.zeros(1)[0])


def stochastic_probe(**kw) -> float:
    """未种子随机：同一申报值在两次干净复跑里必然给出不同结果。"""
    return random.random()


def _stochastic_seeded_twin(seed: int, **kw) -> float:
    return random.Random(seed).random()


KERNELS = {
    "froude": froude,
    "ittc57_cf": ittc57_cf,
    "rt_identity": rt_identity,
    "plate_buckling_sigma_cr": plate_buckling_sigma_cr,
    "rayleigh_ritz_k": rayleigh_ritz_k,
    "wave_length": wave_length,
    "roll_period": roll_period,
    "heave_omega": heave_omega,
    "encounter_omega": encounter_omega,
    "roll_decay_period_rk4": roll_decay_period_rk4,
    "lbm_channel_umax": lbm_channel_umax,
    "lbm_mass_total": lbm_mass_total,
    "shear_wave_decay": shear_wave_decay,
    "fem_bar_tip_uniform": fem_bar_tip_uniform,
    "fem_beam_tip": fem_beam_tip,
    "fem_quad_area": fem_quad_area,
    "dem_hertz_force": dem_hertz_force,
    "dem_collision_v1_after": dem_collision_v1_after,
    "dem_ballistic_drop": dem_ballistic_drop,
    "numpy_only": numpy_only,
    "stochastic_probe": stochastic_probe,
}


def compute(kernel: str, params: dict | None = None) -> float:
    fn = KERNELS.get(kernel)
    if fn is None:
        raise KeyError(f"未登记内核 {kernel!r}")
    return float(fn(**(params or {})))


if __name__ == "__main__":
    print("kernel 自检：", compute("froude", {"V": 3.0, "L": 4.356}))
    print("LBM u_max(格子)", compute("lbm_channel_umax", {"H": 19, "nu": 0.1, "force": 6e-5, "steps": 20000}))
    print("解析 u_max    ", 6e-5 * 19 * 19 / (8 * 0.1))