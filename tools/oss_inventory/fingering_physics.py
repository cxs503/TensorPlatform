"""连续/离散关系的独立复算模块 —— ebinan92/Fingering_dynamics 盘点（OSS-INV）。

定位
----
本模块**不含任何被盘点仓的源码文本**（该仓 `license=None`，见报告第一节：全版权保留 →
reference-only）。这里只重写公开文献层面的关系，用于把仓内的"数值事实"变成可机器复核的断言：

* D2Q9 各向同性差分算子（梯度 (1/12)[4·轴向 + 对角]、拉普拉斯 (1/6)[4·轴向 + 对角 − 20·中心]）
* 自由能（Cahn–Hilliard 型）双阱化学势  μ = a·ψ(1−ψ²) − κ∇²ψ  的一维 kink 精确解 ψ=tanh(x/ξ)
  及界面张力恒等式  σ = κ∫ψ'²dx = 4κ/(3ξ)
* 两相黏度的调和插值与其 BGK 松弛时间换算
* 仓内无量纲化换算链（C_t / Eta_n / σ / u0 / Re / Ma / Ca 自洽性）
* 谱方法（涡量-流函数）中积分因子 + Adams–Bashforth 的扩散精确性

所有量都可从仓内参数块（`fingering.py:16-50`、`validation.py:14-39`、
`spectrum_method/main.py:13-19`）重算，不引用任何未读代码。
"""

from __future__ import annotations

import math

import numpy as np

# D2Q9 离散速度与权重（标准表，非仓内代码搬运）
E = np.array(
    [[0, 0], [1, 0], [0, 1], [-1, 0], [0, -1], [1, 1], [-1, 1], [-1, -1], [1, -1]],
    dtype=float,
)
W = np.array([4 / 9] + [1 / 9] * 4 + [1 / 36] * 4)


# --------------------------------------------------------------------------- #
# 1. 差分算子（轴约定：axis0 = y，axis1 = x，与仓内 stream()/Zou-He 的推进方向一致）
# --------------------------------------------------------------------------- #
def neighborhoods(field: np.ndarray) -> dict[str, np.ndarray]:
    """周期环绕的 8 邻域（仅供单元测试/收敛性研究使用）。"""
    f = np.asarray(field, dtype=float)
    out = {
        "E": np.roll(f, -1, axis=1),
        "W": np.roll(f, 1, axis=1),
        "N": np.roll(f, -1, axis=0),
        "S": np.roll(f, 1, axis=0),
    }
    out["NE"] = np.roll(out["E"], -1, axis=0)
    out["NW"] = np.roll(out["W"], -1, axis=0)
    out["SE"] = np.roll(out["E"], 1, axis=0)
    out["SW"] = np.roll(out["W"], 1, axis=0)
    return out


def grad_x(field: np.ndarray, h: float = 1.0) -> np.ndarray:
    """∂ψ/∂x ≈ (1/12)[4(ψ_E−ψ_W) + (ψ_NE−ψ_NW) + (ψ_SE−ψ_SW)]/h。"""
    n = neighborhoods(field)
    return (4 * (n["E"] - n["W"]) + (n["NE"] - n["NW"]) + (n["SE"] - n["SW"])) / (12.0 * h)


def grad_y(field: np.ndarray, h: float = 1.0) -> np.ndarray:
    """∂ψ/∂y ≈ (1/12)[4(ψ_N−ψ_S) + (ψ_NE+ψ_NW−ψ_SE−ψ_SW)]/h。"""
    n = neighborhoods(field)
    return (4 * (n["N"] - n["S"]) + (n["NE"] + n["NW"] - n["SE"] - n["SW"])) / (12.0 * h)


def laplacian(field: np.ndarray, h: float = 1.0) -> np.ndarray:
    """∇²ψ ≈ (1/6)[4·Σ轴向 + Σ对角 − 20·ψ0]/h²  （D2Q9 各向同性模板）。"""
    f = np.asarray(field, dtype=float)
    n = neighborhoods(f)
    axis = n["E"] + n["W"] + n["N"] + n["S"]
    diag = n["NE"] + n["NW"] + n["SE"] + n["SW"]
    return (4 * axis + diag - 20.0 * f) / (6.0 * h ** 2)


def stencil_check() -> dict[str, float]:
    """二次场上的精确性（Δx=1，取内点，理论误差 0）与光滑场上的二阶收敛率。"""
    n = 64
    x = np.arange(n, dtype=float)
    X, Y = np.meshgrid(x, x, indexing="xy")
    quad = (X - 30.0) ** 2 + (Y - 30.0) ** 2 + 3.0 * X * Y
    inner = (slice(2, -2), slice(2, -2))
    quad_err = {
        "lap": float(np.max(np.abs(laplacian(quad)[inner] - 4.0))),
        "grad_x": float(np.max(np.abs(grad_x(quad)[inner] - (2 * (X - 30.0) + 3.0 * Y)[inner]))),
        "grad_y": float(np.max(np.abs(grad_y(quad)[inner] - (2 * (Y - 30.0) + 3.0 * X)[inner]))),
    }

    # 周期正弦场：误差应随 h 二阶下降（场在 [0,2π]² 上周期）
    rates = {"lap": [], "grad_x": []}
    prev = None
    for m in (32, 64, 128, 256):
        h = 2.0 * np.pi / m
        xx = np.arange(m) * h
        XX, YY = np.meshgrid(xx, xx, indexing="xy")
        field = np.sin(2 * XX) * np.cos(3 * YY)
        lap_err = float(np.sqrt(np.mean((laplacian(field, h) + 13.0 * field) ** 2)))
        gx_err = float(np.sqrt(np.mean((grad_x(field, h) - 2.0 * np.cos(2 * XX) * np.cos(3 * YY)) ** 2)))
        if prev is not None:
            rates["lap"].append(math.log(prev[0] / lap_err) / math.log(2.0))
            rates["grad_x"].append(math.log(prev[1] / gx_err) / math.log(2.0))
        prev = (lap_err, gx_err)
        quad_err["lap_l2_%d" % m] = lap_err
        quad_err["gx_l2_%d" % m] = gx_err
    quad_err["lap_rate_min"] = float(min(rates["lap"]))
    quad_err["grad_rate_min"] = float(min(rates["grad_x"]))
    quad_err["quad_exact_abs_err"] = max(quad_err["lap"], quad_err["grad_x"], quad_err["grad_y"])
    return quad_err


# --------------------------------------------------------------------------- #
# 2. 自由能相场：kink 精确解 + 界面张力恒等式
# --------------------------------------------------------------------------- #
def _thomas(lower, diag, upper, rhs):
    """三对角求解（避免对 scipy 的依赖）。"""
    a, b, c, d = (np.array(v, dtype=float, copy=True) for v in (lower, diag, upper, rhs))
    n = b.size
    for i in range(1, n):
        w = a[i - 1] / b[i - 1]
        b[i] -= w * c[i - 1]
        d[i] -= w * d[i - 1]
    x = np.zeros(n)
    x[-1] = d[-1] / b[-1]
    for i in range(n - 2, -1, -1):
        x[i] = (d[i] - c[i] * x[i + 1]) / b[i]
    return x


def equilibrium_profile(x, xi: float) -> np.ndarray:
    """μ=0 的平界面精确解：ψ = tanh(x/ξ)。"""
    return np.tanh(np.asarray(x, dtype=float) / xi)


def kink_residual_ode(psi, d2psi, xi: float) -> np.ndarray:
    """κψ'' = aψ(1−ψ²) 的残差（κ 已约去，a/κ = −2/ξ²）。"""
    return d2psi + (2.0 / xi ** 2) * np.asarray(psi) * (1.0 - np.asarray(psi) ** 2)


def solve_kink_newton(xi: float = 2.0, half_width: float = 20.0, n: int = 801,
                      iters: int = 60, tol: float = 1e-13) -> dict[str, object]:
    """Newton（带步长回溯）求解 1D 平衡界面，Dirichlet ψ(∓L)=∓1，κ 已约去。

    方程：ψ'' + (2/ξ²)ψ(1−ψ²) = 0（因为 a/κ = −2/ξ²），精确解 ψ=tanh(x/ξ)。
    离散解与 tanh 之差应为 O(h²)（差分的截断误差，不是模型误差）。
    """
    x = np.linspace(-half_width, half_width, n)
    h = x[1] - x[0]
    psi = equilibrium_profile(x, xi).copy()
    psi[0], psi[-1] = -1.0, 1.0
    m = n - 2

    def residual(p):
        f = np.zeros(n)
        f[1:-1] = (p[2:] - 2 * p[1:-1] + p[:-2]) / h ** 2 \
            + (2.0 / xi ** 2) * p[1:-1] * (1 - p[1:-1] ** 2)
        f[0] = p[0] + 1.0
        f[-1] = p[-1] - 1.0
        return f

    lower = np.full(m - 1, 1.0 / h ** 2)
    upper = np.full(m - 1, 1.0 / h ** 2)
    for _ in range(iters):
        r = residual(psi)
        if np.max(np.abs(r)) < tol:
            break
        diag = -2.0 / h ** 2 + (2.0 / xi ** 2) * (1.0 - 3.0 * psi[1:-1] ** 2)
        delta = _thomas(lower, diag, upper, -r[1:-1])
        step, base = 1.0, np.max(np.abs(r))
        for _ in range(30):
            trial = psi.copy()
            trial[1:-1] += step * delta
            r_try = np.max(np.abs(residual(trial)))
            if np.isfinite(r_try) and r_try < base:
                psi, delta = trial, step * delta
                break
            step *= 0.5
        else:
            break

    exact = equilibrium_profile(x, xi)
    return {
        "x": x,
        "psi": psi,
        "exact": exact,
        "max_abs_err": float(np.max(np.abs(psi - exact))),
        "l2_err": float(np.sqrt(np.mean((psi - exact) ** 2))),
        "h": h,
        "ode_residual_max": float(np.max(np.abs(residual(psi)[1:-1]))),
    }


def kink_convergence(xi: float = 2.0, half_width: float = 20.0,
                     ns=(101, 201, 401, 801, 1601)) -> dict[str, object]:
    """界面剖面的网格收敛：误差应 ~ h²（二阶）。"""
    errs, hs = [], []
    for n in ns:
        res = solve_kink_newton(xi=xi, half_width=half_width, n=n)
        errs.append(res["max_abs_err"])
        hs.append(res["h"])
    rates = [math.log(errs[i] / errs[i + 1]) / math.log(hs[i] / hs[i + 1]) for i in range(len(errs) - 1)]
    return {"n": list(ns), "h": hs, "max_abs_err": errs, "rate": rates}


def kink_surface_tension(kappa: float, xi: float) -> float:
    """解析积分 σ = κ∫ψ'²dx = κ·(4/3)/ξ。"""
    return 4.0 * kappa / (3.0 * xi)


def kink_surface_tension_numeric(kappa: float, xi: float, n: int = 200001,
                                 half_width: float = 30.0) -> float:
    """数值积分复核 σ = κ∫ψ'²dx（ψ = tanh(x/ξ)）。"""
    x = np.linspace(-half_width, half_width, n)
    dpsi = (1.0 - np.tanh(x / xi) ** 2) / xi
    return float(kappa * np.trapezoid(dpsi ** 2, x))


def kappa_from_sigma(sigma: float, xi: float) -> float:
    """仓内采用的关系 κ = 0.75·ξ·σ  ⟺  σ = 4κ/(3ξ)。"""
    return 0.75 * xi * sigma


# --------------------------------------------------------------------------- #
# 3. 两相黏度插值（仓内 getMix_tau 的代数结构）
# --------------------------------------------------------------------------- #
def mix_viscosity(psi, eta_n: float | None = None, M: float = 20.0):
    """ν(ψ) = 2·ν1·ν2 / (ν1(1−ψ) + ν2(1+ψ))，ν1=η_n（ψ=+1 侧），ν2=η_n·M（ψ=−1 侧）。"""
    eta_n = conversion_chain()["Eta_n"] if eta_n is None else eta_n
    psi = np.asarray(psi, dtype=float)
    v1 = eta_n
    v2 = eta_n * M
    return 2.0 * v1 * v2 / (v1 * (1.0 - psi) + v2 * (1.0 + psi))


def mix_relaxation_tau(psi, eta_n: float | None = None, M: float = 20.0):
    """BGK：τ = 3ν + 1/2（cs²=1/3）。"""
    return 3.0 * mix_viscosity(psi, eta_n, M) + 0.5


def viscosity_check(eta_n: float | None = None, M: float = 20.0) -> dict[str, float]:
    eta_n = conversion_chain()["Eta_n"] if eta_n is None else eta_n
    v1 = float(mix_viscosity(np.array([1.0]), eta_n, M)[0])
    v2 = float(mix_viscosity(np.array([-1.0]), eta_n, M)[0])
    v0 = float(mix_viscosity(np.array([0.0]), eta_n, M)[0])
    harmonic = 2.0 * (eta_n * eta_n * M) / (eta_n + eta_n * M)
    return {
        "v_psi_plus1": v1,
        "v_psi_minus1": v2,
        "contrast": v2 / v1,
        "v_psi_0": v0,
        "harmonic_mean": harmonic,
        "harm_rel_err": abs(v0 - harmonic) / harmonic,
        "tau_psi_plus1": 3.0 * v1 + 0.5,
        "tau_psi_minus1": 3.0 * v2 + 0.5,
    }


# --------------------------------------------------------------------------- #
# 4. 仓内无量纲化换算链（fingering.py:16-50 的参数块重算）
# --------------------------------------------------------------------------- #
def tau_bgk() -> float:
    """仓内 τ = 1/(3−√3)（注入的"魔数"，等价 ν0≈0.09623）。"""
    return 1.0 / (3.0 - math.sqrt(3.0))


def conversion_chain(tau: float | None = None, R_Nu: float = 1e-6, C_W: float = 1.5e-7,
                     C_rho: float = 1000.0, Eta: float = 1e-3, R_sigma: float = 0.045,
                     Ca: float = 3.0 * 7.33e-3, Pe: float = 400.0, H: int = 380,
                     xi: float = 2.0) -> dict[str, float]:
    """按仓内公式链重算全部无量纲量，并检查 Ca 的自洽闭合。"""
    tau = tau_bgk() if tau is None else tau
    v0 = (tau - 0.5) / 3.0
    C_t = (v0 / R_Nu) * C_W ** 2
    Eta_n = Eta * C_t / (C_rho * C_W ** 2)
    sigma = R_sigma * C_t ** 2 / (C_rho * C_W ** 3)
    u0 = Ca * sigma / Eta_n
    kappa = kappa_from_sigma(sigma, xi)
    a = -2.0 * kappa / xi ** 2
    gamma = u0 * H / ((-a * Pe) * (tau - 0.5))
    cs = 1.0 / math.sqrt(3.0)
    return {
        "tau": tau,
        "v0": v0,
        "C_t": C_t,
        "Eta_n": Eta_n,
        "sigma": sigma,
        "kappa": kappa,
        "a": a,
        "gamma": gamma,
        "u0": u0,
        "Re_h20": u0 * 20.0 / Eta_n,
        "Ma": u0 / cs,
        "Ca_back": u0 * Eta_n / sigma,
        "Ca_in": Ca,
        "Ca_identity_rel_err": abs(u0 * Eta_n / sigma - Ca) / Ca,
        "sigma_from_kappa": kink_surface_tension(kappa, xi),
        "sigma_identity_rel_err": abs(kink_surface_tension(kappa, xi) - sigma) / sigma,
        "xi_over_sigma_scale": xi,
        "physical_u0": u0 * C_W / C_t,
    }


# --------------------------------------------------------------------------- #
# 5. 谱方法：积分因子 + AB2 对扩散项的精确性
# --------------------------------------------------------------------------- #
def integrating_factor_step(c_hat, J_hat, J_hat_before, dt: float, k2: float):
    """c̄ = [ĉ + Δt(−3/2·Ĵⁿ + 1/2·Ĵⁿ⁻¹)]·exp(−k²Δt)（扩散算子被解析积分）。"""
    return (np.asarray(c_hat) + dt * (-1.5 * np.asarray(J_hat) + 0.5 * np.asarray(J_hat_before))) * np.exp(-k2 * dt)


def spectral_decay_check(k: float = 2.0, dt: float = 1e-3, steps: int = 500) -> dict[str, float]:
    """纯扩散（J=0）时数值解 ≡ 解析解 exp(−k²t)，残差仅浮点舍入。"""
    c0 = np.array([1.0 + 0.0j])
    c = c0.copy()
    zero = np.zeros_like(c0)
    for _ in range(steps):
        c = integrating_factor_step(c, zero, zero, dt, k ** 2)
    exact = c0 * np.exp(-k ** 2 * dt * steps)
    return {
        "k": k,
        "steps": steps,
        "dt": dt,
        "c_num_abs": float(abs(c[0])),
        "c_exact_abs": float(abs(exact[0])),
        "rel_err": float(abs(abs(c[0]) - abs(exact[0])) / abs(exact[0])),
    }


# --------------------------------------------------------------------------- #
# 6. 几何脚本：仓内交错障碍阵列的块数（fingering.py:534-551 的循环逻辑重写）
# --------------------------------------------------------------------------- #
def staggered_obstacle_array(limit: int = 380, pitch: int = 20) -> dict[str, object]:
    """交替 4/5 块交错阵列：count 从 1 起、步长 2，块数 = 4 或 5。"""
    blocks = []
    count, flag = 1, True
    while (count + 1) * pitch <= limit:
        if flag:
            for i in range(4):
                y0 = 60 * (i + 1) + i * pitch
                blocks.append((count * pitch, y0, (count + 1) * pitch, y0 + pitch))
            flag = False
            count += 2
            continue
        for i in range(5):
            y0 = 60 * i + (i + 1) * pitch
            blocks.append((count * pitch, y0, (count + 1) * pitch, y0 + pitch))
        flag = True
        count += 2
    solid = sum((b[2] - b[0]) * (b[3] - b[1]) for b in blocks)
    return {
        "n_blocks": len(blocks),
        "blocks": blocks,
        "solid_fraction": solid / float(limit * limit),
        "porosity": 1.0 - solid / float(limit * limit),
    }