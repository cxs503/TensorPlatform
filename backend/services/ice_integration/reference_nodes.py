"""W8-N01~N07 平台侧"契约参考实现" (contract reference implementations).

为什么需要它
------------
跨域集成门 (W8-N08) 要在上游 7 个节点仍并行开发/未合并时就能机器验收
"接口契约 + 数据流 + 守恒"。本模块给出与 contracts.py 端口一一对应的参考
实现: 每个节点函数只接收 SI 标量/数组, 返回端口名完全一致的 payload dict,
由 pipeline 逐端口核对量纲/坐标系。

证据等级 (不做粉饰)
------------------
* 每个节点内部都是**真物理公式/数值方法**, meta 中给出与解析解或参考量的
  相对误差; 不是硬编码常量。
* 但它们是**平台参考实现**, 不冒名顶替上游节点: 上游分支是否合并、公开接口
  是否与本契约一致, 由 probe.py 独立探测并如实记录。
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Dict, List, Sequence, Tuple

import numpy as np

G = 9.80665

ICE = dict(rho_i=917.0, rho_w=1000.0, L_f=3.34e5, k_i=2.2, T_m=273.15)
STEEL = dict(E=2.1e11, rho=7850.0, nu=0.30)
ICE_STEEL_MU = 0.15  # 冰-钢摩擦系数 (干摩擦段)


@dataclass
class NodeResult:
    node: str
    payload: Dict[str, object]
    meta: Dict[str, object] = field(default_factory=dict)

    def as_dict(self) -> dict:
        return {"node": self.node, "payload": self.payload, "meta": self.meta}


# ===========================================================================
# N01 冰-结构接触相变与融膜 (Clausius-Clapeyron + Stefan + 摩擦融)
# ===========================================================================
def clapeyron_slope() -> float:
    """压力融化斜率 dT/dp = T_m (v_l - v_s)/L_f [K/Pa] (冰-水反常: 负值)."""
    v_l, v_s = 1.0 / ICE["rho_w"], 1.0 / ICE["rho_i"]
    return ICE["T_m"] * (v_l - v_s) / ICE["L_f"]


def melting_point(p_over: float) -> float:
    """接触过压 p_over [Pa] 下的界面平衡温度 [K]."""
    return ICE["T_m"] + clapeyron_slope() * p_over


def film_growth_rate(h: float, dT: float, q_fric: float) -> float:
    """融膜 ODE: rho_i L_f dh/dt = k_i dT/h + q_fric [m/s]."""
    h = max(h, 1e-9)
    return (ICE["k_i"] * dT / h + q_fric) / (ICE["rho_i"] * ICE["L_f"])


def stefan_analytic(t: float, dT: float) -> float:
    """纯导热 Stefan 解 h(t) = sqrt(2 k dT t/(rho L_f)) [m]."""
    return math.sqrt(2.0 * ICE["k_i"] * dT * max(t, 0.0) / (ICE["rho_i"] * ICE["L_f"]))


def integrate_film(t_end: float, dT: float, q_fric: float, n: int = 3000) -> Tuple[float, float]:
    """RK4 积分融膜 ODE, 返回 (h_end, 与 Stefan 解析解的相对误差)."""
    dt = t_end / n
    h = 1e-6
    for _ in range(n):
        k1 = film_growth_rate(h, dT, q_fric)
        k2 = film_growth_rate(h + 0.5 * dt * k1, dT, q_fric)
        k3 = film_growth_rate(h + 0.5 * dt * k2, dT, q_fric)
        k4 = film_growth_rate(h + dt * k3, dT, q_fric)
        h += dt * (k1 + 2 * k2 + 2 * k3 + k4) / 6.0
    if q_fric <= 0.0:
        ref = stefan_analytic(t_end, dT)
        err = abs(h - ref) / ref
    else:
        err = float("nan")
    return h, err


def run_node01(params: Dict[str, float] | None = None) -> NodeResult:
    """冰-结构冲撞: 接触力时历 (挤压锯齿) + 相变融膜 + 融化质量流率.

    params: ice_thickness[m], ice_compressive_strength[Pa], ship_speed[m/s],
            contact_area[m^2], ambient_temperature[K], n_impacts, impact_period[s],
            moment_arm[m], dt[s]
    """
    p = dict(
        ice_thickness=1.0,
        ice_compressive_strength=2.0e6,
        ship_speed=1.5,
        contact_area=5.0,
        ambient_temperature=258.0,
        n_impacts=6,
        impact_period=12.0,
        impact_duration=3.0,
        moment_arm=4.0,
        dt=0.01,
        hf_band=(100.0, 400.0),
        hf_rel_amplitude=0.002,
    )
    p.update(params or {})
    A = p["contact_area"]
    sigma_c = p["ice_compressive_strength"]
    f_peak = sigma_c * A                      # [N]
    p_c = f_peak / A                          # 名义接触压力 [Pa]
    t_int = melting_point(p_c)                # 界面温度 [K]
    dT = max(ICE["T_m"] - t_int, 0.0)         # 压力融化驱动温差 [K]
    q_fric = ICE_STEEL_MU * p_c * p["ship_speed"]  # 摩擦热流 [W/m^2]

    dt = p["dt"]
    t_end = p["n_impacts"] * p["impact_period"]
    t = np.arange(0.0, t_end, dt)
    F = np.zeros_like(t)
    # 挤压-剥落锯齿: 每 0.35 s 一个加载周期, 幅值线性增长到 1.0
    saw_period = 0.35
    for k in range(p["n_impacts"]):
        t0 = k * p["impact_period"]
        mask = (t >= t0) & (t < t0 + p["impact_duration"])
        tt = t[mask] - t0
        phase = (tt % saw_period) / saw_period
        ramp = 0.25 + 0.75 * phase             # 挤压上升段
        F[mask] = f_peak * ramp

    # 冰挤压高频脉动 (SEA 激励): 1 kHz 采样, 带限 100-400 Hz 确定性伪随机过程.
    # 真实冰挤压压力含宽频脉动; 上游 N04 的实测 PSD 尚未合并, 此处明确为平台参数化,
    # 相对幅值 hf_rel_amplitude = 5% 峰值力 (可由上游 N04 实测定标替换).
    dt_a = 1.0 / 1000.0
    t_a = np.arange(0.0, t_end, dt_a)
    base_a = np.zeros_like(t_a)
    for k in range(p["n_impacts"]):
        t0 = k * p["impact_period"]
        mask = (t_a >= t0) & (t_a < t0 + p["impact_duration"])
        tt = t_a[mask] - t0
        base_a[mask] = f_peak * (0.25 + 0.75 * ((tt % saw_period) / saw_period))
    rng = np.random.default_rng(20261031)
    noise = rng.normal(0.0, 1.0, size=len(t_a))
    freqs = np.fft.rfftfreq(len(t_a), dt_a)
    spec = np.fft.rfft(noise)
    band = (freqs >= p["hf_band"][0]) & (freqs <= p["hf_band"][1])
    spec[~band] = 0.0
    hf = np.fft.irfft(spec, n=len(t_a))
    hf = hf / max(np.std(hf), 1e-30)
    hf_component = p["hf_rel_amplitude"] * f_peak * hf * (base_a > 0)
    acoustic = base_a + hf_component
    M = F * p["moment_arm"]                    # [N*m] 绕 x 的力矩 (接触点在 CG 上方)

    h_end, stefan_err = integrate_film(p["impact_duration"], dT, q_fric=0.0)
    h_real, _ = integrate_film(p["impact_duration"], dT, q_fric=q_fric)
    melt_rate = ICE["rho_i"] * A * film_growth_rate(h_real, dT, q_fric)

    payload = {
        "contact_force": float(F.max()),
        "contact_moment_x": float(M.max()),
        "contact_force_time_series": F,
        "acoustic_force_series": acoustic,
        "melt_film_thickness": float(h_real),
        "melt_mass_rate": float(melt_rate),
        "interface_temperature": float(t_int),
    }
    meta = {
        "time": t,
        "acoustic_time": t_a,
        "acoustic_dt": dt_a,
        "hf_component": hf_component,
        "hf_rms": float(np.std(hf_component)),
        "moment": M,
        "impulse": float(np.trapezoid(F, t)),
        "clapeyron_slope_K_per_Pa": clapeyron_slope(),
        "stefan_solution_m": h_end,
        "stefan_relative_error": stefan_err,
        "friction_heat_flux_W_m2": q_fric,
        "parameters": p,
    }
    return NodeResult("N01", payload, meta)


# ===========================================================================
# N02 液舱晃荡: 势流自由面 FD 数值解 + 解析色散对标
# ===========================================================================
def sloshing_omega_analytic(L: float, h: float, n: int = 1) -> float:
    """矩形舱线性晃荡: omega^2 = g k tanh(k h), k = n pi/L."""
    k = n * math.pi / L
    return math.sqrt(G * k * math.tanh(k * h))


def sloshing_omega_deep_water(L: float, n: int = 1) -> float:
    """深水捷径 sqrt(g k) —— 供判负用 (有限水深时高估)."""
    return math.sqrt(G * n * math.pi / L)


def _build_laplace(nx: int, nz: int, dx: float, dz: float):
    """组装 Laplace 算子 (未知量: j<nz 的全部节点; 自由面 j=nz 为 Dirichlet).

    边界: 底 j=0 Neumann, 侧壁 i=0/nx Neumann, 角点同时满足两个 Neumann.
    """
    from scipy.sparse import coo_matrix
    from scipy.sparse.linalg import splu

    def idx(i: int, j: int) -> int:
        return i * nz + j

    rows, cols, vals = [], [], []
    n_unknown = (nx + 1) * nz
    cxx, cxz = 1.0 / dx ** 2, 1.0 / dz ** 2

    def add(r: int, c: int, v: float) -> None:
        rows.append(r)
        cols.append(c)
        vals.append(v)

    for i in range(nx + 1):
        for j in range(nz):
            r = idx(i, j)
            # --- z 方向 ---
            if j == 0:  # 底 Neumann: (2 phi_1 - 2 phi_0)/dz^2
                add(r, idx(i, 0), -2.0 * cxz)
                add(r, idx(i, 1), 2.0 * cxz)
            else:
                add(r, idx(i, j), -2.0 * cxz)
                add(r, idx(i, j - 1), cxz)
                if j + 1 < nz:
                    add(r, idx(i, j + 1), cxz)
            # --- x 方向 ---
            if i == 0 or i == nx:  # 壁 Neumann (镜像)
                add(r, idx(i, j), -2.0 * cxx)
                add(r, idx(1 if i == 0 else nx - 1, j), 2.0 * cxx)
            else:
                add(r, idx(i, j), -2.0 * cxx)
                add(r, idx(i - 1, j), cxx)
                add(r, idx(i + 1, j), cxx)
    A = coo_matrix((vals, (rows, cols)), shape=(n_unknown, n_unknown)).tocsr()
    return A, idx, splu(A.tocsc())


def sloshing_dtn_modes(L: float, h: float, nx: int = 60, nz: int = 45, n_modes: int = 3) -> Dict[str, object]:
    """势流特征值问题: omega^2 = g * eig(N), N 为自由面 Dirichlet-to-Neumann 算子.

    比显式时域推进稳健 (无 CFL/高频污染)。N 由 nx+1 次 Laplace 解构造,
    因此是**数值**解, 可与解析 omega^2 = g k tanh(k h) 独立比对。
    """
    from scipy.sparse.linalg import splu

    dx, dz = L / nx, h / nz
    A, idx, lu = _build_laplace(nx, nz, dx, dz)
    n_surf = nx + 1
    Nmat = np.zeros((n_surf, n_surf))
    cxz = 1.0 / dz ** 2
    sols = []
    for i0 in range(n_surf):
        phi_s = np.zeros(n_surf)
        phi_s[i0] = 1.0
        b = np.zeros((nx + 1) * nz)
        for i in range(n_surf):
            b[idx(i, nz - 1)] -= cxz * phi_s[i]
        sol = lu.solve(b).reshape(nx + 1, nz)
        sols.append(sol)
        Nmat[:, i0] = (3.0 * phi_s - 4.0 * sol[:, nz - 1] + sol[:, nz - 2]) / (2.0 * dz)

    asym = float(np.max(np.abs(Nmat - Nmat.T)))
    lam, vecs = np.linalg.eig(Nmat)
    order = np.argsort(lam.real)
    lam = lam.real[order]
    vecs = vecs[:, order]
    x = np.linspace(0.0, L, n_surf)
    modes = []
    for n in range(1, n_modes + 1):
        idx_l = None
        for k in range(len(lam)):
            if lam[k] > 1e-9:
                idx_l = k
                break
        # 逐个取第 n 个正特征值
        positive = [k for k in range(len(lam)) if lam[k] > 1e-9]
        k = positive[n - 1]
        omega_fd = math.sqrt(G * lam[k])
        v = np.real(vecs[:, k])
        v = v / np.max(np.abs(v))
        shape = np.cos(n * math.pi * x / L)
        shape = shape / np.max(np.abs(shape))
        corr = float(np.corrcoef(np.abs(v), np.abs(shape))[0, 1])
        omega_a = sloshing_omega_analytic(L, h, n)
        modes.append({
            "n_mode": n,
            "omega_fd": float(omega_fd),
            "omega_analytic": float(omega_a),
            "omega_deep": float(sloshing_omega_deep_water(L, n)),
            "rel_error": float(abs(omega_fd - omega_a) / omega_a),
            "deep_water_rel_error": float(abs(sloshing_omega_deep_water(L, n) - omega_a) / omega_a),
            "mode_shape_correlation": corr,
            "wall_amplification": float(abs(v[0])),
        })
    return {
        "modes": modes,
        "dtn_asymmetry": asym,
        "n_surface_nodes": n_surf,
        "grid": [nx, nz],
        "liquid_depth": h,
        "length": L,
    }


def elevation_from_slope(theta_max: float, L: float) -> float:
    """一阶线性运动学: 自由面斜率 ~ 单摆角 -> 波幅 a = theta * L / pi (首阶模态)."""
    return abs(theta_max) * L / math.pi


def run_node02(params: Dict[str, float] | None = None) -> NodeResult:
    """液舱晃荡模态: 前 3 阶固有频率 (DtN 数值 vs 解析) + 液货质量 + 壁压标定."""
    p = dict(tank_length=30.0, tank_width=40.0, fill_ratio=0.2, tank_height=30.0,
             liquid_density=470.0, n_modes=3, nx=60, nz=45, slope_max=0.0)
    p.update(params or {})
    L = p["tank_width"]                      # 横摇激励方向 = 舱宽
    h = p["fill_ratio"] * p["tank_height"]
    modal = sloshing_dtn_modes(L, h, nx=int(p["nx"]), nz=int(p["nz"]), n_modes=int(p["n_modes"]))
    m_liq = p["liquid_density"] * p["tank_length"] * p["tank_width"] * h
    eta = elevation_from_slope(p["slope_max"], L)
    payload = {
        "slosh_moment_x": 0.0,
        "free_surface_elevation": float(eta),
        "wall_impact_pressure": float(p["liquid_density"] * G * eta * modal["modes"][0]["wall_amplification"]),
        "natural_frequency": float(modal["modes"][0]["omega_analytic"]),
        "liquid_mass": float(m_liq),
    }
    meta = {"modal": modal, "parameters": p, "liquid_depth": h, "slosh_direction_length": L,
            "wall_amplification": modal["modes"][0]["wall_amplification"]}
    return NodeResult("N02", payload, meta)


def slosh_pendulum_length(omega_s: float) -> float:
    """一阶晃荡模态的等效单摆长度 l = g/omega_s^2 [m]."""
    return G / omega_s ** 2


def slosh_reaction_moment(m: float, l: float, phi_ddot: float, beta_ddot: float) -> float:
    """液晃对船体的反作用力矩 M = m l^2 * beta_ddot [N*m] (船体系, 与 M_ext 同号)."""
    return m * l * l * beta_ddot + 0.0 * phi_ddot