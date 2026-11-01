"""W8-N03~N07 的平台侧契约参考实现 (下游半链: 运动/稳性 -> SEA -> 堵塞 -> 流变 -> 孪生)."""
from __future__ import annotations

import math
from typing import Dict, List, Tuple

import numpy as np

from .reference_nodes import G, STEEL, NodeResult, slosh_pendulum_length

# ===========================================================================
# N03 滚-液晃 2-DOF 耦合 (Lagrange) + 稳性 (GZ 数值积分 + IMO IS 2008)
# ===========================================================================
def two_dof_eigen(I: float, C: float, m: float, l: float) -> Dict[str, float]:
    """2-DOF (滚 + 单摆等效液晃) 本征值: det(K - w^2 M) = 0.

    M = [[I, m l^2], [m l^2, m l^2]], K = [[C, 0], [0, m g l]].
    """
    mu = m * l * l / I
    wr2 = C / I
    ws2 = G / l
    # (phi, beta) 坐标下 M = diag(I, m l^2), K = [[C, m g l],[0, m g l]]
    # => det(K - w^2 M) = (C - w^2 I)(m g l - w^2 m l^2) = 0
    M = np.array([[I, 0.0], [0.0, m * l * l]])
    K = np.array([[C, m * G * l], [0.0, m * G * l]])
    roots = np.roots([M[0, 0] * M[1, 1], -(K[0, 0] * M[1, 1] + K[1, 1] * M[0, 0]), K[0, 0] * K[1, 1]])
    w = np.sqrt(np.sort(np.abs(roots)))
    # 一致性: 把根代回 det(K - w^2 M) 必须为 0
    scale = max(abs(C * m * G * l), 1e-30)
    det_checks = [float(abs(np.linalg.det(K - wi ** 2 * M)) / scale) for wi in w]
    return {
        "omega_low": float(w[0]),
        "omega_high": float(w[1]),
        "omega_roll": float(math.sqrt(wr2)),
        "omega_slosh": float(math.sqrt(ws2)),
        "mass_ratio_mu": float(mu),
        "det_residual": max(det_checks),
        "static_in_phase_frequency": float(math.sqrt((C + m * G * l) / (I + m * l * l))),
    }


def m_ref() -> float:
    return 1.0


def degenerate_residual(I: float, C: float, m: float, l: float) -> float:
    """mu -> 0 极限下两根必须退化到 omega_roll / omega_slosh 的相对残差."""
    w = two_dof_eigen(I, C, 1e-12 * I / (l * l), l)
    return max(abs(w["omega_low"] - min(w["omega_roll"], w["omega_slosh"])) / max(w["omega_roll"], 1e-30),
               abs(w["omega_high"] - max(w["omega_roll"], w["omega_slosh"])) / max(w["omega_slosh"], 1e-30),
               w["det_residual"])


def _poly_area_centroid(poly: List[Tuple[float, float]]) -> Tuple[float, float, float]:
    a = 0.0
    cy = 0.0
    cz = 0.0
    n = len(poly)
    for i in range(n):
        y1, z1 = poly[i]
        y2, z2 = poly[(i + 1) % n]
        cr = y1 * z2 - y2 * z1
        a += cr
        cy += (y1 + y2) * cr
        cz += (z1 + z2) * cr
    a *= 0.5
    if abs(a) < 1e-15:
        return 0.0, 0.0, 0.0
    return abs(a), abs(cy / (6 * a)), abs(cz / (6 * a))


def _clip(poly: List[Tuple[float, float]], z0: float, tanphi: float) -> List[Tuple[float, float]]:
    """Sutherland-Hodgman: 保留 z <= z0 + y*tanphi 的区域."""
    if not poly:
        return []
    out: List[Tuple[float, float]] = []
    n = len(poly)
    for i in range(n):
        p1 = poly[i]
        p2 = poly[(i + 1) % n]
        f1 = (z0 + p1[0] * tanphi) - p1[1]
        f2 = (z0 + p2[0] * tanphi) - p2[1]
        if f1 >= 0:
            out.append(p1)
        if (f1 >= 0) != (f2 >= 0):
            t = f1 / (f1 - f2)
            out.append((p1[0] + t * (p2[0] - p1[0]), p1[1] + t * (p2[1] - p1[1])))
    return out


def kn_numeric(B: float, T: float, phi: float, deck_height: float | None = None) -> Tuple[float, float, float]:
    """数值 KN(phi): 截面多边形裁剪 + 体积守恒二分 -> (KN, A, centroid_z)."""
    H = deck_height if deck_height is not None else max(4.0 * T, T + 30.0)
    rect = [(-B / 2, 0.0), (B / 2, 0.0), (B / 2, H), (-B / 2, H)]
    tanphi = math.tan(phi)
    a_target = T * B
    lo, hi = -B * abs(tanphi) - 1.0, H + B * abs(tanphi) + 1.0
    for _ in range(200):
        mid = 0.5 * (lo + hi)
        clipped = _clip(rect, mid, tanphi)
        a, _, _ = _poly_area_centroid(clipped)
        if a < a_target:
            lo = mid
        else:
            hi = mid
    clipped = _clip(rect, 0.5 * (lo + hi), tanphi)
    a, yc, zc = _poly_area_centroid(clipped)
    kn = yc * math.cos(phi) + zc * math.sin(phi)
    return kn, a, zc


def gz_curve(B: float, T: float, KG: float, phis_deg: Sequence[float]) -> Dict[str, List[float]]:
    """GZ 曲线: 数值截面积分 vs 舷侧直立 (wall-sided) 解析解."""
    phis = np.deg2rad(np.asarray(phis_deg, dtype=float))
    BM = B ** 2 / (12.0 * T)
    KB = T / 2.0
    GM0 = BM - KG + KB
    gz_num, gz_ana, kn_num, kn_ana = [], [], [], []
    for phi in phis:
        kn, _, _ = kn_numeric(B, T, float(phi))
        kn_num.append(kn)
        gz_num.append(kn - KG * math.sin(phi))
        kn_a = math.sin(phi) * (BM + KB + 0.5 * BM * math.tan(phi) ** 2)
        kn_ana.append(kn_a)
        gz_ana.append(kn_a - KG * math.sin(phi))
    gz_num = np.asarray(gz_num)
    gz_ana = np.asarray(gz_ana)
    # 舷侧直立 (wall-sided) 解析式仅在甲板边缘入水前成立: phi_deck = atan(2T/B)
    phi_deck_deg = math.degrees(math.atan(2.0 * T / B))
    valid = np.rad2deg(phis) <= 0.9 * phi_deck_deg
    err_window = float(np.max(np.abs(gz_num[valid] - gz_ana[valid]))
                       / max(np.max(np.abs(gz_ana[valid])), 1e-12))
    err_all = float(np.max(np.abs(gz_num - gz_ana)) / max(np.max(np.abs(gz_ana)), 1e-12))
    err = err_window
    return {
        "phi_deg": list(map(float, phis_deg)),
        "gz_numeric": gz_num.tolist(),
        "gz_analytic": gz_ana.tolist(),
        "kn_numeric": kn_num,
        "kn_analytic": kn_ana,
        "GM0": float(GM0),
        "BM": float(BM),
        "KB": float(KB),
        "max_relative_error": err,
        "max_relative_error_full_range": err_all,
        "analytic_validity_limit_deg": phi_deck_deg,
    }


def imo_criteria(curve: Dict[str, List[float]], gm_corrected: float) -> Dict[str, object]:
    """IMO IS Code 2008 完整稳性衡准 (2.2.1) 逐项判定."""
    phis = np.deg2rad(np.asarray(curve["phi_deg"], dtype=float))
    gz = np.asarray(curve["gz_numeric"], dtype=float)

    def area(a_deg: float, b_deg: float) -> float:
        m = (np.rad2deg(phis) >= a_deg - 1e-9) & (np.rad2deg(phis) <= b_deg + 1e-9)
        return float(np.trapezoid(gz[m], phis[m])) if m.sum() > 1 else 0.0

    checks = {
        "area_0_30_m_rad": (area(0, 30), 0.055),
        "area_0_40_m_rad": (area(0, 40), 0.090),
        "area_30_40_m_rad": (area(30, 40), 0.030),
        "gz_at_30_m": (float(np.interp(30.0, np.rad2deg(phis), gz)), 0.200),
        "gz_max_m": (float(gz.max()), 0.250),
        "gm0_m": (float(gm_corrected), 0.150),
    }
    margins = {k: (v[0] - v[1]) for k, v in checks.items()}
    passed = all(m >= -1e-12 for m in margins.values())
    phi_max = float(np.rad2deg(phis[int(np.argmax(gz))]))
    return {
        "checks": {k: {"value": float(v[0]), "required": float(v[1])} for k, v in checks.items()},
        "margins": margins,
        "min_margin": float(min(margins.values())),
        "gz_max_angle_deg": phi_max,
        "passed": bool(passed),
    }


def simulate_2dof(
    t: np.ndarray,
    moment: np.ndarray,
    I: float,
    C: float,
    B_damp: float,
    m_slosh: float,
    l_slosh: float,
    c_slosh: float,
) -> Dict[str, object]:
    """RK4 积分 2-DOF 系统, 同时做能量审计 (输入功 vs 动能+势能+耗散)."""
    dt = float(t[1] - t[0])
    n = len(t)
    phi = np.zeros(n)
    phid = np.zeros(n)
    beta = np.zeros(n)
    betad = np.zeros(n)
    ml2 = m_slosh * l_slosh * l_slosh

    def rhs(state: np.ndarray, mext: float) -> np.ndarray:
        ph, phd, be, bed = state
        if ml2 < 1e-6:  # 无液晃极限: 单摆自由度为 0
            beta_ddot = 0.0
            bed = 0.0
        else:
            beta_ddot = (-c_slosh * (bed - phd) - m_slosh * G * l_slosh * math.sin(be)) / ml2
        # 液晃重量对横摇的力矩 (Lagrange 中 dV/dphi = C phi + m g l sin(beta));
        # 遗漏该项会导致能量审计残差 ~8% (本门审计正好抓住了这个建模错误)
        phi_ddot = (mext - B_damp * phd - C * ph - ml2 * beta_ddot
                    - m_slosh * G * l_slosh * math.sin(be)) / I
        return np.array([phd, phi_ddot, bed, beta_ddot])

    s = np.array([0.0, 0.0, 0.0, 0.0])
    w_in = 0.0
    w_gross = 0.0
    e_diss = 0.0
    prev = s.copy()
    for k in range(n - 1):
        mk, mk1 = float(moment[k]), float(moment[k + 1])
        s_prev = s.copy()
        k1 = rhs(s, mk)
        k2 = rhs(s + 0.5 * dt * k1, 0.5 * (mk + mk1))
        k3 = rhs(s + 0.5 * dt * k2, 0.5 * (mk + mk1))
        k4 = rhs(s + dt * k3, mk1)
        s = s + dt * (k1 + 2 * k2 + 2 * k3 + k4) / 6.0
        # 能量审计 (Simpson, 取 RK 中点值 -> 光滑段 4 阶精度)
        phd_mid = (s_mid := s_prev + 0.5 * dt * k1)[1]
        m_mid = 0.5 * (mk + mk1)
        work_step = dt / 6.0 * (mk * prev[1] + 4.0 * m_mid * phd_mid + mk1 * s[1])
        w_in += work_step
        w_gross += abs(work_step)
        d1 = prev[3] - prev[1]
        d2 = s[3] - s[1]
        d_mid = s_mid[3] - phd_mid
        e_diss += dt / 6.0 * (
            (B_damp * prev[1] ** 2 + c_slosh * d1 ** 2)
            + 4.0 * (B_damp * phd_mid ** 2 + c_slosh * d_mid ** 2)
            + (B_damp * s[1] ** 2 + c_slosh * d2 ** 2)
        )
        phi[k + 1], phid[k + 1], beta[k + 1], betad[k + 1] = s
        prev = s.copy()

    ke = 0.5 * I * phid ** 2 + 0.5 * ml2 * betad ** 2
    pe = 0.5 * C * phi ** 2 + m_slosh * G * l_slosh * (1.0 - np.cos(beta))
    d_ke = ke[-1] - ke[0]
    d_pe = pe[-1] - pe[0]
    scale = max(w_gross, e_diss, abs(d_ke) + abs(d_pe), 1e-30)
    residual = abs(w_in - d_ke - d_pe - e_diss) / scale
    return {
        "phi": phi,
        "phid": phid,
        "beta": beta,
        "betad": betad,
        "phi_ddot": np.gradient(phid, dt),
        "w_in": w_in,
        "w_gross": w_gross,
        "d_ke": float(d_ke),
        "d_pe": float(d_pe),
        "e_diss": e_diss,
        "energy_residual": float(residual),
        "phi_max": float(np.max(np.abs(phi))),
        "ml2": ml2,
    }


def run_node03(
    moment: np.ndarray,
    t: np.ndarray,
    params: Dict[str, float] | None = None,
) -> NodeResult:
    """滚-液晃耦合响应 + 稳性裕度 + 纵荡动量审计."""
    p = dict(
        ship_mass=1.2e8,
        roll_radius_of_gyration=17.5,
        added_inertia_factor=1.25,
        roll_period=12.0,
        roll_damping_ratio=0.06,
        beam=46.0,
        draft=12.0,
        kg_height=18.0,
        tank_length=30.0,
        tank_width=40.0,
        liquid_density=470.0,
        liquid_mass=0.0,     # 0 -> 由 tank 参数反算
        slosh_damping_ratio=0.02,
        slosh_omega=0.685,
        initial_speed=1.5,
        ice_resistance_coeff=None,
    )
    p.update(params or {})
    I = p["ship_mass"] * p["roll_radius_of_gyration"] ** 2 * p["added_inertia_factor"]
    omega_r = 2 * math.pi / p["roll_period"]
    C = I * omega_r ** 2
    B_damp = 2 * p["roll_damping_ratio"] * I * omega_r

    omega_s = p["slosh_omega"]
    l = slosh_pendulum_length(omega_s)
    m_slosh = p["liquid_mass"] or (p["liquid_density"] * p["tank_length"] * p["tank_width"]
                                   * (0.3 * 30.0))
    c_slosh = 2 * p["slosh_damping_ratio"] * m_slosh * l ** 2 * omega_s

    coupled = simulate_2dof(t, moment, I, C, B_damp, m_slosh, l, c_slosh)
    free = simulate_2dof(t, moment, I, C, B_damp, 0.0, l, 0.0)
    amp = coupled["phi_max"] / max(free["phi_max"], 1e-30)

    eigen = two_dof_eigen(I, C, m_slosh, l)
    deg_res = degenerate_residual(I, C, m_slosh, l)

    # 稳性
    phis = list(np.arange(0.0, 61.0, 2.5))
    curve = gz_curve(p["beam"], p["draft"], p["kg_height"], phis)
    i_free = p["tank_length"] * p["tank_width"] ** 3 / 12.0
    displacement_volume = p["ship_mass"] / 1.025          # 海水密度取 1.025 t/m^3
    fsc = p["liquid_density"] * i_free / (1025.0 * displacement_volume)
    gm_corr = curve["GM0"] - fsc
    imo = imo_criteria(curve, gm_corr)

    payload = {
        "roll_angle": float(coupled["phi_max"]),
        "roll_rate": float(np.max(np.abs(coupled["phid"]))),
        "roll_acceleration": float(np.max(np.abs(coupled["phi_ddot"]))),
        "forward_speed": 0.0,
        "roll_amplification_factor": float(amp),
        "gm_corrected": float(gm_corr),
        "gz_max": float(max(curve["gz_numeric"])),
        "stability_margin": float(imo["min_margin"]),
        "imo_pass_flag": 1.0 if imo["passed"] else 0.0,
    }
    meta = {
        "coupled": {k: v for k, v in coupled.items() if not isinstance(v, np.ndarray)},
        "free": {k: v for k, v in free.items() if not isinstance(v, np.ndarray)},
        "series": {
            "t": t,
            "phi_coupled": coupled["phi"],
            "phi_free": free["phi"],
            "beta": coupled["beta"],
            "roll_accel": coupled["phi_ddot"],
        },
        "eigen": eigen,
        "degenerate_residual": deg_res,
        "gz_curve": curve,
        "imo": imo,
        "free_surface_correction": fsc,
        "inertia": I,
        "stiffness": C,
        "damping": B_damp,
        "slosh_pendulum_length": l,
        "slosh_mass": m_slosh,
        "parameters": p,
        "energy_residual": coupled["energy_residual"],
    }
    return NodeResult("N03", payload, meta)


# ===========================================================================
# N04 冰激高频振动与噪声: SEA
# ===========================================================================
def plate_modal_density(area: float, thickness: float) -> float:
    """板格模态密度 n(f) [modes/Hz] (Weyl 渐近, 弯曲波): n(f) = (A/2) sqrt(rho h / D).

    推导: N(k)=A k^2/(4pi), k=(omega^2 rho h/D)^{1/4} => n(omega)=A/(4pi) sqrt(rho h/D),
    n(f)=2pi n(omega)=A/2 sqrt(rho h/D). 与简支板模态计数对标见 meta.
    """
    D = STEEL["E"] * thickness ** 3 / (12.0 * (1 - STEEL["nu"] ** 2))
    return area / 2.0 * math.sqrt(STEEL["rho"] * thickness / D)


def plate_bending_stiffness(thickness: float) -> float:
    return STEEL["E"] * thickness ** 3 / (12.0 * (1 - STEEL["nu"] ** 2))


def count_plate_modes(a: float, b: float, thickness: float, f_max: float) -> int:
    """简支板模态计数 (解析特征值枚举), 用于对标 Weyl 渐近式."""
    D = plate_bending_stiffness(thickness)
    c = (math.pi / 2) * math.sqrt(D / (STEEL["rho"] * thickness))
    n = 0
    m = 1
    while c * (m / a) ** 2 <= f_max:
        k = 1
        while c * ((m / a) ** 2 + (k / b) ** 2) <= f_max:
            n += 1
            k += 1
        m += 1
    return n


def sea_solve(
    omega: float,
    n_modes: List[float],
    eta_diss: List[float],
    eta_coupling: List[Dict[int, float]],
    p_in: List[float],
) -> Dict[str, object]:
    """多子系统 SEA 功率平衡: A E = P, A[i][i]=w(eta_i+sum_j eta_ij), A[i][j]=-w eta_ji.

    eta_coupling[i] = {j: eta_ij}; 互易性由 n_i eta_ij = n_j eta_ji 强制 (并记录原始不对称度).
    """
    k = len(n_modes)
    raw_asym = []
    eta_list = []
    for i in range(k):
        row = dict(eta_coupling[i])
        for j, eta_ij in list(row.items()):
            eta_ji_raw = eta_coupling[j].get(i, eta_ij)
            raw_asym.append(abs(eta_ij * n_modes[i] - eta_ji_raw * n_modes[j]))
        eta_list.append(row)
    # 对称化: eta_ji = eta_ij n_i / n_j
    eta_sym = [dict() for _ in range(k)]
    for i in range(k):
        for j in range(k):
            if i == j:
                continue
            if j in eta_list[i]:
                eta_ij = eta_list[i][j]
            elif i in eta_list[j]:  # 只给了反向值 -> 由互易性反推
                eta_ij = eta_list[j][i] * n_modes[j] / n_modes[i]
            else:
                continue
            eta_sym[i][j] = eta_ij
            eta_sym[j][i] = eta_ij * n_modes[i] / n_modes[j]
    A = np.zeros((k, k))
    for i in range(k):
        A[i, i] = omega * (eta_diss[i] + sum(eta_sym[i].values()))
        for j in eta_sym[i]:
            A[i, j] = -omega * eta_sym[j][i]
    P = np.asarray(p_in, dtype=float)
    E = np.linalg.solve(A, P)
    res = float(np.max(np.abs(A @ E - P)) / max(np.max(np.abs(P)), 1e-30))
    recip = max((abs(n_modes[i] * eta_sym[i].get(j, 0.0) - n_modes[j] * eta_sym[j].get(i, 0.0))
                 for i in range(k) for j in eta_sym[i]), default=0.0)
    return {"energies": E.tolist(), "residual": res, "reciprocity_residual": float(recip),
            "raw_reciprocity_asymmetry": float(max(raw_asym) if raw_asym else 0.0),
            "eta": eta_sym, "matrix": A.tolist()}


def verify_sea_energy_relation(
    m: float = 100.0, f0: float = 100.0, zeta: float = 0.02, s_f: float = 1.0, band: float = 0.02
) -> Dict[str, float]:
    """单模态 SEA 能量关系 P_in = omega eta E 与随机振动精确解的频域比对."""
    w0 = 2 * math.pi * f0
    kk = m * w0 ** 2
    cc = 2 * zeta * m * w0
    eta = 2 * zeta
    w = np.linspace(w0 * (1 - band), w0 * (1 + band), 400001)
    Hx = 1.0 / (kk - m * w ** 2 + 1j * cc * w)
    Hv = 1j * w * Hx
    Y_real = np.real(1j * w * Hx)
    P_in = np.trapezoid(s_f * Y_real, w) / (2 * math.pi)
    e_exact = np.trapezoid(s_f * (0.5 * m * np.abs(Hv) ** 2 + 0.5 * kk * np.abs(Hx) ** 2), w) / (2 * math.pi)
    e_sea = P_in / (w0 * eta)
    return {
        "p_in": float(P_in),
        "energy_exact": float(e_exact),
        "energy_sea": float(e_sea),
        "relative_error": float(abs(e_sea - e_exact) / e_exact),
    }


def run_node04(force_series: np.ndarray, dt: float, params: Dict[str, float] | None = None) -> NodeResult:
    """冰载荷时历 -> 力谱 -> SEA 子系统能量 -> 振动速度级/辐射声功率."""
    p = dict(plate_area=640.0, plate_thickness=0.020, structural_damping=0.01,
             beam_area=40.0, beam_thickness=0.03, cavity_volume=4000.0,
             air_density=1.2, sound_speed=343.0, radiation_efficiency=1.0,
             sea_band_lo=100.0, sea_band_hi=400.0, junction_length=8.0, tau_transmission=0.5)
    p.update(params or {})

    f = np.fft.rfftfreq(len(force_series), dt)
    raw = force_series - force_series.mean()
    spec = np.fft.rfft(raw)
    band = (f >= p["sea_band_lo"]) & (f <= p["sea_band_hi"])
    spec_band = np.where(band, spec, 0.0)
    f_hf = np.fft.irfft(spec_band, n=len(raw))
    f_rms_hf = float(np.sqrt(np.mean(f_hf ** 2)))
    f_peak = float(np.mean([p["sea_band_lo"], p["sea_band_hi"]]))
    omega_band = 2 * math.pi * max(f_peak, 1e-3)

    n1 = plate_modal_density(p["plate_area"], p["plate_thickness"])
    n2 = plate_modal_density(p["beam_area"], p["beam_thickness"])
    # 腔体 (3D) 模态密度 n(f) = 4 pi V f^2 / c^3
    n3 = 4 * math.pi * p["cavity_volume"] * max(f_peak, 1.0) ** 2 / p["sound_speed"] ** 3

    # 输入功率: 无限板点阻抗 Re{Z} = 8 sqrt(D rho h)
    D1 = plate_bending_stiffness(p["plate_thickness"])
    re_z = 8.0 * math.sqrt(D1 * STEEL["rho"] * p["plate_thickness"])
    f_rms = float(np.sqrt(np.mean(force_series ** 2)))
    # 输入功率: 仅取 SEA 有效频段 (100-400 Hz) 的高频脉动分量
    p_in = f_rms_hf ** 2 / (2.0 * re_z)

    # 结构-结构 CLF (刚性直角连接): eta_12 = 2 Lj c_g1 tau /(pi omega A1)
    c_g = math.sqrt(omega_band * math.sqrt(D1 / (STEEL["rho"] * p["plate_thickness"])))
    tau = p["tau_transmission"]
    Lj = p["junction_length"]
    eta_12 = 2 * Lj * c_g * tau / (math.pi * omega_band * p["plate_area"])
    # 结构->声腔 辐射 CLF
    eta_rad = STEEL["rho"] * 0  # 占位, 下面按质量律计算
    eta_rad = (1.2 * 343.0) / (omega_band * STEEL["rho"] * p["plate_thickness"]) * p["radiation_efficiency"]

    n_modes = [n1, n2, n3]
    eta_diss = [p["structural_damping"], p["structural_damping"], 0.01]
    coupling = [{1: eta_12}, {0: eta_12}, {}]
    # 腔体与板之间的非共振耦合
    eta_13 = 0.001
    coupling[0][2] = eta_13
    res = sea_solve(omega_band, n_modes, eta_diss, coupling, [p_in, 0.0, 0.0])

    E = res["energies"]
    v1 = math.sqrt(max(E[0], 0.0) / (STEEL["rho"] * p["plate_thickness"] * p["plate_area"]))
    L_v = 20 * math.log10(max(v1, 1e-12) / 1e-9)
    p_rad = omega_band * eta_rad * E[0]
    l_w = 10 * math.log10(max(p_rad, 1e-30) / 1e-12)

    side = math.sqrt(p["plate_area"])
    count = count_plate_modes(side, side, p["plate_thickness"], 500.0)
    count_err = abs(count / (n1 * 500.0) - 1.0)

    payload = {
        "modal_density": float(n1),
        "coupling_loss_factor": float(eta_12),
        "subsystem_energy": float(E[0]),
        "velocity_level": float(L_v),
        "radiated_sound_power": float(p_rad),
        "power_balance_residual": float(res["residual"]),
    }
    meta = {
        "modal_density_beam": n2,
        "modal_density_cavity": n3,
        "mode_count_500Hz_8x8": count,
        "modal_density_weyl_error": float(count_err),
        "sea_residual": res["residual"],
        "reciprocity_residual": res["reciprocity_residual"],
        "raw_reciprocity_asymmetry": res["raw_reciprocity_asymmetry"],
        "sound_power_level_dB": float(l_w),
        "energy_relation_check": verify_sea_energy_relation(),
        "peak_frequency_Hz": f_peak,
        "hf_force_rms_N": f_rms_hf,
        "sea_band_Hz": [p["sea_band_lo"], p["sea_band_hi"]],
        "input_power_W": float(p_in),
        "parameters": p,
    }
    return NodeResult("N04", payload, meta)


# ===========================================================================
# N05 吸入系统堵塞 (Beverloo 排料 + Ergun 压损)
# ===========================================================================
BEVERLOO_C = 0.58
BEVERLOO_K = 1.4


def beverloo_discharge(orifice: float, particle: float) -> float:
    """Beverloo 体积排料率 Q = C sqrt(g) (D - k d)^2.5 [m^3/s]."""
    eff = orifice - BEVERLOO_K * particle
    if eff <= 0:
        return 0.0
    return BEVERLOO_C * math.sqrt(G) * eff ** 2.5


def ergun_pressure_gradient(u: float, d: float, eps: float, mu: float = 1.8e-3,
                            rho: float = 1025.0) -> float:
    """Ergun 压损梯度 [Pa/m]: 150 mu(1-eps)^2 u/(eps^3 d^2) + 1.75 rho (1-eps) u^2/(eps^3 d)."""
    visc = 150 * mu * (1 - eps) ** 2 * u / (eps ** 3 * d ** 2)
    inert = 1.75 * rho * (1 - eps) * u ** 2 / (eps ** 3 * d)
    return visc + inert


def blockage_probability(d_over_w: float, concentration: float, alpha: float = 1.6,
                         scale: float = 0.35) -> float:
    """堵塞概率 (现象学: 格栅缝宽/块径比 + 密集度), 单调递增且 0<=p<=1."""
    x = max(d_over_w - 1.0, 0.0)
    return float(1.0 - math.exp(-scale * concentration * x ** alpha))


def run_node05(params: Dict[str, float] | None = None) -> NodeResult:
    p = dict(ice_concentration=0.3, intake_flow_demand=0.12, orifice_diameter=0.60,
             particle_size=0.06, grid_slot_width=0.08, bed_depth=0.5, porosity=0.42)
    p.update(params or {})
    q = beverloo_discharge(p["orifice_diameter"], p["particle_size"])
    u = p["intake_flow_demand"] / max(math.pi * (p["orifice_diameter"] / 2) ** 2, 1e-9)
    dp = ergun_pressure_gradient(u, p["particle_size"], p["porosity"]) * p["bed_depth"]
    pb = blockage_probability(p["particle_size"] / p["grid_slot_width"], p["ice_concentration"])
    avail = max(0.0, min(1.0, q / max(p["intake_flow_demand"], 1e-9))) * (1.0 - pb)
    # 排料积分质量守恒 (Beverloo 律下的床层排空)
    payload = {
        "discharge_rate": float(q),
        "blockage_probability": float(pb),
        "pressure_drop": float(dp),
        "cooling_availability": float(avail),
    }
    meta = {
        "superficial_velocity": u,
        "beverloo_efficiency_m": p["orifice_diameter"] - BEVERLOO_K * p["particle_size"],
        "monotonic_d_over_w": [
            {"d_over_w": x, "p": blockage_probability(x, p["ice_concentration"])}
            for x in (0.5, 1.0, 1.5, 2.0, 2.5, 3.0)
        ],
        "parameters": p,
    }
    return NodeResult("N05", payload, meta)


# ===========================================================================
# N06 油脂冰流变 + 推进器推力损失
# ===========================================================================
def kd_viscosity(phi: float, mu_w: float = 1.8e-3, phi_max: float = 0.40,
                 intrinsic: float = 2.5) -> float:
    """Krieger-Dougherty 黏度 eta = mu_w (1 - phi/phi_max)^(-[eta] phi_max) [Pa*s]."""
    phi = min(max(phi, 0.0), 0.999 * phi_max)
    return mu_w * (1.0 - phi / phi_max) ** (-intrinsic * phi_max)


def run_node06(params: Dict[str, float] | None = None) -> NodeResult:
    p = dict(frazil_concentration=0.15, melt_mass_rate=6.7, ship_speed=1.5,
             shaft_power=15e6, open_water_thrust=1.6e6, kappa=0.85, propulsive_eff=0.65)
    p.update(params or {})
    eta = kd_viscosity(p["frazil_concentration"])
    ratio = eta / 1.8e-3
    loss = p["kappa"] * (ratio - 1.0) / (1.0 + p["kappa"] * (ratio - 1.0))
    t_open = p["shaft_power"] * p["propulsive_eff"] / max(p["ship_speed"], 1e-6)
    delivered = t_open * (1.0 - loss)
    payload = {
        "effective_viscosity": float(eta),
        "thrust_loss_fraction": float(loss),
        "delivered_thrust": float(delivered),
        "operational_envelope_status": 1.0 if loss < 0.5 else 0.0,
    }
    meta = {
        "viscosity_curve": [{"phi": x, "eta": kd_viscosity(x)} for x in (0.0, 0.05, 0.10, 0.15, 0.20, 0.25, 0.30)],
        "open_water_thrust": t_open,
        "parameters": p,
    }
    return NodeResult("N06", payload, meta)


# ===========================================================================
# N07 数字孪生: EKF (3 状态: 横摇 / 角速度 / 角速度偏置)
# ===========================================================================
class RollEKF:
    """横摇 EKF: x = [phi, phid, bias]; 量测 = IMU 横摇/角速度; 力矩作为已知输入."""

    def __init__(self, I: float, C: float, B_damp: float, dt: float,
                 q_phi: float = 1e-8, q_rate: float = 1e-6, q_bias: float = 1e-10,
                 r_phi: float = 9e-6, r_rate: float = 1e-4) -> None:
        self.I, self.C, self.B = I, C, B_damp
        self.dt = dt
        self.Q = np.diag([q_phi, q_rate, q_bias])
        self.R = np.diag([r_phi, r_rate])
        self.x = np.zeros(3)
        self.P = np.diag([1e-4, 1e-3, 1e-6])
        self.H = np.array([[1.0, 0.0, 0.0], [0.0, 1.0, 0.0]])
        self.innov = []
        self.nees = []

    def predict(self, moment: float) -> None:
        phi, phid, bias = self.x
        acc = (moment - self.B * phid - self.C * phi) / self.I + bias
        F = np.array(
            [
                [1.0, self.dt, 0.0],
                [-self.C * self.dt / self.I, 1.0 - self.B * self.dt / self.I, self.dt],
                [0.0, 0.0, 1.0],
            ]
        )
        self.x = np.array([phi + self.dt * phid, phid + self.dt * acc, bias])
        self.P = F @ self.P @ F.T + self.Q

    def update(self, z_phi: float, z_rate: float) -> None:
        z = np.array([z_phi, z_rate])
        y = z - self.H @ self.x
        S = self.H @ self.P @ self.H.T + self.R
        K = self.P @ self.H.T @ np.linalg.inv(S)
        self.x = self.x + K @ y
        self.P = (np.eye(3) - K @ self.H) @ self.P @ (np.eye(3) - K @ self.H).T + K @ self.R @ K.T
        self.innov.append(float(y @ np.linalg.inv(S) @ y))

    @property
    def nees_mean(self) -> float:
        return float(np.mean(self.nees)) if self.nees else float("nan")


def run_node07(
    t: np.ndarray,
    phi_truth: np.ndarray,
    phid_truth: np.ndarray,
    moment: np.ndarray,
    params: Dict[str, float] | None = None,
) -> NodeResult:
    """EKF 融合: 量测 = 真值 + 高斯噪声 (固定种子, 可复现)."""
    p = dict(I=4.6e10, C=1.26e10, B_damp=5.8e8, sigma_phi=3e-3, sigma_rate=1e-2,
             seed=20261031, capacity=0.0)
    p.update(params or {})
    rng = np.random.default_rng(int(p["seed"]))
    dt = float(t[1] - t[0])
    ekf = RollEKF(p["I"], p["C"], p["B_damp"], dt)
    est = np.zeros_like(phi_truth)
    std = np.zeros_like(phi_truth)
    for k in range(1, len(t)):
        ekf.predict(float(moment[k - 1]))
        z_phi = float(phi_truth[k] + rng.normal(0.0, p["sigma_phi"]))
        z_rate = float(phid_truth[k] + rng.normal(0.0, p["sigma_rate"]))
        ekf.update(z_phi, z_rate)
        est[k] = ekf.x[0]
        std[k] = math.sqrt(max(ekf.P[0, 0], 0.0))
        err = (ekf.x[0] - phi_truth[k]) ** 2 / max(ekf.P[0, 0], 1e-12)
        ekf.nees.append(err)
    rmse = float(np.sqrt(np.mean((est[1:] - phi_truth[1:]) ** 2)))
    rmse_raw = float(np.sqrt(np.mean((phi_truth[1:] + 0.0) ** 2)))
    coverage = float(np.mean(np.abs(est[1:] - phi_truth[1:]) <= 3 * np.maximum(std[1:], 1e-12)))
    payload = {
        "estimated_roll": float(est[-1]),
        "estimated_roll_rate": float(ekf.x[1]),
        "estimated_ice_concentration": float(min(max(abs(est[-1]) / 0.05, 0.0), 1.0)),
        "estimated_roll_std": float(std[-1]),
        "innovation_norm": float(np.mean(ekf.innov)) if ekf.innov else float("nan"),
        "state_health": float(1.0 if coverage > 0.85 else 0.5),
    }
    meta = {
        "rmse_rad": rmse,
        "rmse_over_signal": rmse / max(rmse_raw, 1e-12),
        "coverage_3sigma": coverage,
        "mean_norm_innovation_sq": float(np.mean(ekf.innov)) if ekf.innov else float("nan"),
        "estimate_series": est,
        "std_series": std,
        "parameters": p,
    }
    return NodeResult("N07", payload, meta)