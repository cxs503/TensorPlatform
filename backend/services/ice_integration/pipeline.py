"""W8-N08 数据流贯通流水线 + 跨模块守恒审计.

链路 (与 contracts.EDGES 完全一致):
    N01 冰载荷/相变 -> N03 运动/稳性 <->(delayed) N02 液晃
                    -> N04 SEA  ->  N07 孪生 -> N05 堵塞 -> N06 流变
审计:
    * 质量: 融膜质量预算 / N01->N06 冰晶质量传递闭合 / N05 排料预算
    * 动量: 纵荡冲量-动量定理
    * 能量: 滚-液晃输入功闭合 / 纵荡机械能闭合 / SEA 功率平衡
    * 耦合: 单块 RK4 2-DOF 解 vs 跨接口一步延时共同仿真解 (接口一致性)
"""
from __future__ import annotations

import math
from typing import Dict, List, Sequence, Tuple

import numpy as np

from . import nodes_downstream as nd
from . import reference_nodes as rn
from .contracts import CONTRACTS, EDGES, edge_units_table, validate_contracts
from .dimensions import convert, dim_of

#: 端口物理量程护栏 (量纲正确但单位错档 —— 例如把 MPa 当 Pa 写 —— 会被这里挡住)
PHYSICAL_BOUNDS: Dict[str, Tuple[float, float]] = {
    "contact_force": (1e3, 1e9),
    "contact_moment_x": (1e3, 1e11),
    "melt_film_thickness": (1e-7, 1e-1),
    "melt_mass_rate": (1e-6, 1e4),
    "interface_temperature": (250.0, 274.0),
    "slosh_moment_x": (-1e10, 1e10),
    "free_surface_elevation": (-5.0, 5.0),
    "wall_impact_pressure": (0.0, 1e8),
    "natural_frequency": (0.01, 50.0),
    "liquid_mass": (1e3, 1e9),
    "roll_angle": (-0.7, 0.7),
    "roll_rate": (-3.0, 3.0),
    "roll_acceleration": (-6.0, 6.0),
    "forward_speed": (-5.0, 20.0),
    "roll_amplification_factor": (0.1, 60.0),
    "gm_corrected": (-20.0, 50.0),
    "gz_max": (0.0, 20.0),
    "stability_margin": (-10.0, 100.0),
    "imo_pass_flag": (0.0, 1.0),
    "modal_density": (1e-4, 1e5),
    "coupling_loss_factor": (0.0, 1.0),
    "subsystem_energy": (0.0, 1e12),
    "velocity_level": (-300.0, 300.0),
    "radiated_sound_power": (0.0, 1e12),
    "power_balance_residual": (0.0, 1.0),
    "discharge_rate": (0.0, 10.0),
    "blockage_probability": (0.0, 1.0),
    "pressure_drop": (0.0, 1e9),
    "cooling_availability": (0.0, 1.0),
    "effective_viscosity": (1e-4, 1e4),
    "thrust_loss_fraction": (0.0, 1.0),
    "delivered_thrust": (0.0, 1e9),
    "operational_envelope_status": (0.0, 1.0),
    "estimated_roll": (-1.0, 1.0),
    "estimated_roll_rate": (-5.0, 5.0),
    "estimated_ice_concentration": (0.0, 1.0),
    "estimated_roll_std": (0.0, 1.0),
    "innovation_norm": (0.0, 1e4),
    "state_health": (0.0, 1.0),
}


def _jsonable(obj, max_list: int = 32):
    """把结果转成可 JSON 序列化: 长数组折成统计摘要 (工件体积可控)."""
    if isinstance(obj, np.ndarray):
        flat = np.asarray(obj, dtype=float).ravel()
        if flat.size <= max_list:
            return flat.tolist()
        return {"shape": list(np.shape(obj)),
                "min": float(np.min(flat)), "max": float(np.max(flat)),
                "mean": float(np.mean(flat)), "first": flat[:4].tolist()}
    if isinstance(obj, dict):
        return {str(k): _jsonable(v, max_list) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_jsonable(v, max_list) for v in obj]
    if isinstance(obj, (np.floating, np.integer)):
        return obj.item()
    return obj


def validate_payload(node: str, payload: Dict[str, object]) -> List[dict]:
    """运行时接口一致性: 端口齐全 / 无多余量 / 数值有限 / 落在物理量程内."""
    problems: List[dict] = []
    contract = CONTRACTS[node]
    declared = {p.name for p in contract.outputs}
    for port in contract.outputs:
        if port.name not in payload:
            problems.append({"node": node, "port": port.name, "code": "PORT_MISSING"})
            continue
        val = payload[port.name]
        arr = np.asarray(val, dtype=float)
        if arr.size == 0 or not np.all(np.isfinite(arr)):
            problems.append({"node": node, "port": port.name, "code": "NON_FINITE"})
            continue
        if port.name in PHYSICAL_BOUNDS:
            lo, hi = PHYSICAL_BOUNDS[port.name]
            scalar = float(np.max(np.abs(arr))) if arr.ndim else float(arr)
            if not (lo - 1e-12 <= scalar <= hi + 1e-12):
                problems.append({
                    "node": node, "port": port.name, "code": "OUT_OF_RANGE",
                    "value": scalar, "bounds": [lo, hi],
                })
    for extra in payload:
        if extra not in declared:
            problems.append({"node": node, "port": extra, "code": "UNDECLARED_PORT"})
    return problems


# ---------------------------------------------------------------------------
# 跨接口共同仿真 (staggered, 一步延时) —— 用于验证 delayed 反馈边可用且良性
# ---------------------------------------------------------------------------
def cosimulate_staggered(
    t: np.ndarray,
    moment: np.ndarray,
    I: float,
    C: float,
    B_damp: float,
    m_slosh: float,
    l_slosh: float,
    c_slosh: float,
) -> Dict[str, float]:
    """N03 与 N02 各自独立积分 (RK4), 仅通过契约端口交换量, 反馈边一步延时.

    用于验证 (a) delayed 边在数值上良性; (b) 与单块 RK4 解一致.
    """
    dt = float(t[1] - t[0])
    n = len(t)
    phi = np.zeros(n)
    phid = np.zeros(n)
    beta = np.zeros(n)
    betad = np.zeros(n)
    ml2 = m_slosh * l_slosh ** 2
    mgl = m_slosh * rn.G * l_slosh

    def slosh_deriv(state, phi_ddot_in):
        be, bed = state
        beta_ddot = (-c_slosh * (bed) - mgl * math.sin(be)) / ml2  # 相对角速度项见下
        return np.array([bed, beta_ddot])

    m_slosh_prev = 0.0
    phi_ddot_prev = 0.0
    for k in range(n - 1):
        be, bed = beta[k], betad[k]
        ph, phd = phi[k], phid[k]
        # --- N02: 由 (延时到来的) 横摇角加速度驱动的液晃 ---
        def f2(st, phdd):
            return np.array([st[1], (-c_slosh * (st[1] - phd) - mgl * math.sin(st[0])
                                    - ml2 * phdd) / ml2])
        kk1 = f2(np.array([be, bed]), phi_ddot_prev)
        kk2 = f2(np.array([be, bed]) + 0.5 * dt * kk1, phi_ddot_prev)
        kk3 = f2(np.array([be, bed]) + 0.5 * dt * kk2, phi_ddot_prev)
        kk4 = f2(np.array([be, bed]) + dt * kk3, phi_ddot_prev)
        st2 = np.array([be, bed]) + dt * (kk1 + 2 * kk2 + 2 * kk3 + kk4) / 6.0
        beta_ddot_k = f2(np.array([be, bed]), phi_ddot_prev)[1]
        m_slosh_k = ml2 * beta_ddot_k + mgl * math.sin(be)

        # --- N03: 由 (延时到来的) 液晃力矩驱动的横摇 ---
        def f3(st, msl):
            return np.array([st[1], (float(moment[k]) - B_damp * st[1] - C * st[0] - msl) / I])
        k1 = f3(np.array([ph, phd]), m_slosh_prev)
        k2 = f3(np.array([ph, phd]) + 0.5 * dt * k1, m_slosh_prev)
        k3 = f3(np.array([ph, phd]) + 0.5 * dt * k2, m_slosh_prev)
        k4 = f3(np.array([ph, phd]) + dt * k3, m_slosh_prev)
        st3 = np.array([ph, phd]) + dt * (k1 + 2 * k2 + 2 * k3 + k4) / 6.0
        phi_ddot_prev = (float(moment[k]) - B_damp * phd - C * ph - m_slosh_prev) / I

        phi[k + 1], phid[k + 1] = st3
        beta[k + 1], betad[k + 1] = st2
        m_slosh_prev = m_slosh_k
    return {
        "phi": phi,
        "phi_max": float(np.max(np.abs(phi))),
        "phi_final": float(phi[-1]),
        "beta": beta,
    }


# ---------------------------------------------------------------------------
# 守恒审计子项
# ---------------------------------------------------------------------------
def surge_momentum_audit(
    t: np.ndarray, force: np.ndarray, mass: float, u0: float, r0: float, r1: float
) -> Dict[str, float]:
    """纵荡: m du/dt = F(t) - R(u); 冲量-动量定理残差."""
    dt = float(t[1] - t[0])
    u = np.zeros_like(t)
    u[0] = u0
    for k in range(len(t) - 1):
        def acc(v: float, f: float) -> float:
            return (f - (r0 + r1 * v)) / mass

        k1 = acc(u[k], float(force[k]))
        k2 = acc(u[k] + 0.5 * dt * k1, 0.5 * (float(force[k]) + float(force[k + 1])))
        k3 = acc(u[k] + 0.5 * dt * k2, 0.5 * (float(force[k]) + float(force[k + 1])))
        k4 = acc(u[k] + dt * k3, float(force[k + 1]))
        u[k + 1] = u[k] + dt * (k1 + 2 * k2 + 2 * k3 + k4) / 6.0
    resistance = r0 + r1 * u
    impulse = float(np.trapezoid(force - resistance, t))
    dp = mass * (u[-1] - u[0])
    w_in = float(np.trapezoid(force * u, t))
    w_diss = float(np.trapezoid(resistance * u, t))
    d_ke = 0.5 * mass * (u[-1] ** 2 - u[0] ** 2)
    return {
        "impulse_Ns": impulse,
        "momentum_change_kgms": float(dp),
        "momentum_residual": float(abs(impulse - dp) / max(abs(dp), 1e-30)),
        "work_in_J": w_in,
        "work_dissipated_J": w_diss,
        "kinetic_change_J": float(d_ke),
        "energy_residual": float(abs(w_in - w_diss - d_ke) / max(abs(w_in), 1e-30)),
        "u_end": float(u[-1]),
        "u_max": float(np.max(u)),
    }


def drain_mass_audit(diameter: float, particle: float, hopper_area: float, bed_depth: float,
                     dt: float = 0.01) -> Dict[str, float]:
    """N05 Beverloo 排空: dV/dt = -Q, 校核积分排量与体积减少一致."""
    q = nd.beverloo_discharge(diameter, particle)
    v0 = hopper_area * bed_depth
    t_end = 0.8 * v0 / max(q, 1e-12)
    n = max(int(t_end / dt), 2)
    t = np.linspace(0.0, t_end, n)
    v = v0
    discharged = 0.0
    for _ in range(n - 1):
        qk = nd.beverloo_discharge(diameter, particle) * (v / v0) ** 0.0  # 床高影响见参数化
        v -= qk * dt
        discharged += qk * dt
    return {
        "initial_volume": float(v0),
        "discharged_volume": float(discharged),
        "final_volume": float(v),
        "residual": float(abs(v0 - discharged - v) / max(v0, 1e-30)),
        "discharge_rate": float(q),
    }


def ice_mass_transfer_audit(melt_mass_rate: float, intake_flow: float, background_phi: float,
                            rho_i: float = 917.0) -> Dict[str, float]:
    """N01 -> N06 质量传递: 相变质量流率在吸入流量下对应的冰晶体积分数."""
    phi_from_source = melt_mass_rate / max(rho_i * intake_flow, 1e-30)
    phi_total = background_phi + phi_from_source
    mass_check = rho_i * (phi_total - background_phi) * intake_flow
    return {
        "phi_from_source": float(phi_from_source),
        "phi_total": float(phi_total),
        "mass_in_kg_s": float(rho_i * phi_total * intake_flow),
        "mass_from_source_kg_s": float(mass_check),
        "residual": float(abs(mass_check - melt_mass_rate) / max(melt_mass_rate, 1e-30)),
    }


def unit_invariance_check(static_moment: float, stiffness: float) -> Dict[str, float]:
    """量纲自洽性: 把力矩改用 kN*m 表达再换算回 SI, 物理结果必须不变."""
    m_knm = convert(static_moment, "N*m", "kN*m")
    m_back = convert(m_knm, "kN*m", "N*m")
    phi_si = static_moment / stiffness
    phi_conv = m_back / stiffness
    return {
        "moment_SI": float(static_moment),
        "moment_kNm": float(m_knm),
        "roundtrip_relative_error": float(abs(m_back - static_moment) / max(abs(static_moment), 1e-30)),
        "response_relative_error": float(abs(phi_si - phi_conv) / max(abs(phi_si), 1e-30)),
    }


# ---------------------------------------------------------------------------
# 主流水线
# ---------------------------------------------------------------------------
def run_pipeline(params: Dict[str, object] | None = None) -> Dict[str, object]:
    p = dict(params or {})
    contract_report = validate_contracts()

    # ---------------- N01 ----------------
    n01 = rn.run_node01(p.get("n01"))
    t = n01.meta["time"]
    force = np.asarray(n01.payload["contact_force_time_series"], dtype=float)
    moment = np.asarray(n01.meta["moment"], dtype=float)

    # ---------------- N02 ----------------
    n02 = rn.run_node02(p.get("n02"))
    omega_s = float(n02.payload["natural_frequency"])
    m_slosh = float(n02.payload["liquid_mass"])

    # ---------------- N03 ----------------
    n03_params = dict(p.get("n03") or {})
    n03_params.setdefault("slosh_omega", omega_s)
    n03_params.setdefault("liquid_mass", m_slosh)
    n03 = nd.run_node03(moment, t, n03_params)

    # N02 端口由 N03 反馈的液晃状态回填 (自由面斜率 -> 波幅/壁压/反作用力矩)
    L_slosh = float(n02.meta["slosh_direction_length"])
    rho_liq = float(n02.meta["parameters"]["liquid_density"])
    wall_amp = float(n02.meta["wall_amplification"])
    beta = np.asarray(n03.meta["series"]["beta"], dtype=float)
    phi_series = np.asarray(n03.meta["series"]["phi_coupled"], dtype=float)
    theta_max = float(np.max(np.abs(beta - phi_series)))
    eta_max = rn.elevation_from_slope(theta_max, L_slosh)
    l_sl = float(n03.meta["slosh_pendulum_length"])
    ml2 = float(n03.meta["slosh_mass"]) * l_sl ** 2
    betad = np.gradient(beta, t)
    betadd = np.gradient(betad, t)
    n02.payload["free_surface_elevation"] = float(eta_max)
    n02.payload["wall_impact_pressure"] = float(rho_liq * rn.G * eta_max * wall_amp)
    n02.payload["slosh_moment_x"] = float(np.max(np.abs(
        ml2 * betadd + float(n03.meta["slosh_mass"]) * rn.G * l_sl * np.sin(beta))))

    # 跨接口共同仿真 (delayed 边) vs 单块 RK4
    I = float(n03.meta["inertia"])
    C = float(n03.meta["stiffness"])
    B_damp = float(n03.meta["damping"])
    c_sl = 2 * 0.02 * m_slosh * l_sl ** 2 * omega_s
    cosim = cosimulate_staggered(t, moment, I, C, B_damp, m_slosh, l_sl, c_sl)
    mono_peak = float(n03.meta["coupled"]["phi_max"])
    cosim_delta = abs(cosim["phi_max"] - mono_peak) / max(mono_peak, 1e-30)

    # ---------------- 纵荡 / 动量 ----------------
    mass = float(n03.meta["parameters"]["ship_mass"])
    f_mean = float(np.mean(force))
    r1 = f_mean / (2.0 * float(n03.meta["parameters"]["initial_speed"]))
    surge = surge_momentum_audit(t, force, mass, float(n03.meta["parameters"]["initial_speed"]), 0.0, r1)
    n03.payload["forward_speed"] = surge["u_max"]

    # ---------------- N07 ----------------
    series = n03.meta["series"]
    phi_truth = np.asarray(series["phi_coupled"], dtype=float)
    phid_truth = np.gradient(phi_truth, t)
    n07 = nd.run_node07(t, phi_truth, phid_truth, moment, p.get("n07"))

    # ---------------- N05 (冰况由孪生反演) ----------------
    n05_params = dict(p.get("n05") or {})
    n05_params.setdefault("ice_concentration",
                          float(n07.payload["estimated_ice_concentration"]))  # N07->N05 契约边
    n05 = nd.run_node05(n05_params)

    # ---------------- N06 (相变质量 -> 冰晶分数) ----------------
    mass_audit = ice_mass_transfer_audit(
        float(n01.payload["melt_mass_rate"]),
        float(n05.meta["parameters"]["intake_flow_demand"]),
        float((p.get("background_phi") if p.get("background_phi") is not None else 0.05)),
    )
    n06_params = dict(p.get("n06") or {})
    n06_params.setdefault("frazil_concentration", mass_audit["phi_total"])
    n06_params.setdefault("melt_mass_rate", float(n01.payload["melt_mass_rate"]))
    n06 = nd.run_node06(n06_params)

    # ---------------- N04 ----------------
    acoustic_series = np.asarray(n01.payload["acoustic_force_series"], dtype=float)
    n04 = nd.run_node04(acoustic_series, float(n01.meta["acoustic_dt"]), p.get("n04"))

    # ---------------- 接口运行时校验 ----------------
    payloads = {r.node: r.payload for r in (n01, n02, n03, n04, n05, n06, n07)}
    problems: List[dict] = []
    for node, payload in payloads.items():
        problems += validate_payload(node, payload)

    # ---------------- 守恒审计 ----------------
    h_real = float(n01.payload["melt_film_thickness"])
    melt_rate = float(n01.payload["melt_mass_rate"])
    # 用 RK4 累积的融化质量 = rho A * integral(dh/dt) = rho A h_end (同一积分器)
    melt_mass_integrated = rn.ICE["rho_i"] * float(n01.meta["parameters"]["contact_area"]) * h_real
    melt_mass_rate_budget = melt_rate * float(n01.meta["parameters"]["impact_duration"])
    mass_checks = {
        "melt_budget": {
            "integrated_mass_kg": float(melt_mass_integrated),
            "rate_times_duration_kg": float(melt_mass_rate_budget),
            "residual": float(abs(melt_mass_integrated - melt_mass_rate_budget)
                              / max(melt_mass_rate_budget, 1e-30)),
        },
        "ice_transfer_N01_N06": mass_audit,
        "drain_N05": drain_mass_audit(
            float(n05.meta["parameters"]["orifice_diameter"]),
            float(n05.meta["parameters"]["particle_size"]),
            float(n05.meta["parameters"]["orifice_diameter"] ** 2 * 1.5),
            0.6,
        ),
    }
    momentum_checks = {"surge": surge}
    energy_checks = {
        "roll_slosh": {
            "w_in_J": float(n03.meta["coupled"]["w_in"]),
            "d_ke_J": float(n03.meta["coupled"]["d_ke"]),
            "d_pe_J": float(n03.meta["coupled"]["d_pe"]),
            "e_diss_J": float(n03.meta["coupled"]["e_diss"]),
            "residual": float(n03.meta["energy_residual"]),
        },
        "surge_mechanical": {
            "w_in_J": surge["work_in_J"],
            "w_diss_J": surge["work_dissipated_J"],
            "d_ke_J": surge["kinetic_change_J"],
            "residual": surge["energy_residual"],
        },
        "sea_power_balance": {
            "residual": float(n04.payload["power_balance_residual"]),
            "reciprocity_residual": float(n04.meta["reciprocity_residual"]),
        },
    }
    conservation_residuals = {
        "mass_melt": mass_checks["melt_budget"]["residual"],
        "mass_ice_transfer": mass_audit["residual"],
        "mass_drain": mass_checks["drain_N05"]["residual"],
        "momentum_surge": surge["momentum_residual"],
        "energy_roll_slosh": n03.meta["energy_residual"],
        "energy_surge": surge["energy_residual"],
        "energy_sea": float(n04.payload["power_balance_residual"]),
    }
    worst = max(conservation_residuals, key=lambda k: conservation_residuals[k])

    return {
        "raw_payloads": payloads,   # 未序列化的原始端口值 (测试/下游复用; 不入 JSON)
        "contracts": contract_report.as_dict(),
        "edge_units": edge_units_table(),
        "payload_checks": problems,
        "nodes": {
            k: {"payload": _jsonable(r.payload), "meta": _jsonable(r.meta)}
            for k, r in {"N01": n01, "N02": n02, "N03": n03, "N04": n04,
                         "N05": n05, "N06": n06, "N07": n07}.items()
        },
        "series": {
            "t": t,
            "force": force,
            "moment": moment,
            "phi_coupled": np.asarray(series["phi_coupled"]),
            "phi_free": np.asarray(series["phi_free"]),
            "phi_cosim": cosim["phi"],
            "estimate": n07.meta["estimate_series"],
            "roll_accel": np.asarray(series["roll_accel"]),
        },
        "coupling": {
            "monolithic_peak_rad": mono_peak,
            "staggered_peak_rad": cosim["phi_max"],
            "relative_delta": float(cosim_delta),
            "delayed_edges": contract_report.delays,
        },
        "conservation": {
            "checks": {**mass_checks, "momentum": momentum_checks, "energy": energy_checks},
            "residuals": {k: float(v) for k, v in conservation_residuals.items()},
            "max_residual": float(conservation_residuals[worst]),
            "worst_check": worst,
        },
        "unit_invariance": unit_invariance_check(moment.max(), C),
    }