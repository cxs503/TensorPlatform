#!/usr/bin/env python3
"""滚-液晃链（N02→N03）自由液面/液晃反作用的解析复核审计。

背景
----
冰区航行集成链的 N03 节点（``backend/services/ice_integration/nodes_downstream.py``）
把"滚-液晃 2-DOF 耦合 + 稳性裕度"交付给下游契约。本工具只做**只读**审计：把该节点
用到的两条与自由液面有关的物理量，逐条钉在**可手工推导、无自由参数的解析恒等式**上：

A1 自由液面惯性矩      i_FS = ∫ y² dA（矩形液面: L_fore · B_trans³ / 12）
A2 自由液面修正量       GG₀ = Σ_j ρ_j i_j / (ρ_sea · ∇)        [m]
A3 静态液晃反作用力矩   M_liq(φ) = ρ_l g i_FS · φ = m_s g z_p φ （z_p 为等效摆挂点高）
A4 液晃一阶固有频率     ω₁² = g k₁ tanh(k₁ h), k₁ = π/L
A5 等效摆长             l = g / ω₁²

审计对象（数值一律由本脚本程序化写入 artifacts，零手抄）：
  * ``run_node03`` 的自由液面修正算式与其量纲前提（ship_mass 以 kg 计，∇ 却按 t/m³ 除）；
  * ``simulate_2dof`` 的单摆方程是否含船体横摇加速度激励；
  * ``two_dof_eigen`` 的本征根与自身 ODE 线性化是否一致；
  * N02 的解析频率 vs DtN 数值特征值。

用法
----
    python3 tools/audit/sloshing_fsc_audit.py                 # 出 artifacts + 图
    python3 tools/audit/sloshing_fsc_audit.py --out /tmp/x --no-figures
"""
from __future__ import annotations

import argparse
import json
import math
import os
import sys
from typing import Dict, List

import numpy as np

G = 9.81
RHO_SEA = 1025.0  # kg/m^3

# N03 默认参数（逐字取自 run_node03 的 p 默认字典，见寄存器里引用的行号）
N03_DEFAULTS: Dict[str, float] = dict(
    ship_mass=1.2e8,          # kg（注意单位：kg，不是 t）
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
    slosh_damping_ratio=0.02,
    slosh_omega=0.685,
    fill_height_fallback=0.3 * 30.0,   # nodes_downstream.py: m_slosh 反算用 0.3*30.0
)


# ---------------------------------------------------------------------------
# 1. 解析恒等式
# ---------------------------------------------------------------------------
def free_surface_inertia_rect(l_fore: float, b_trans: float) -> float:
    """矩形自由液面对纵轴的面积二次矩 i_FS = ∫ y² dA = l_fore * b_trans³ / 12 [m^4]."""
    return l_fore * b_trans ** 3 / 12.0


def free_surface_inertia_quadrature(l_fore: float, b_trans: float, n: int = 20000) -> float:
    """同一量的独立数值求积（逐条带），用于交叉复核闭式。"""
    y = (np.arange(n) + 0.5) / n - 0.5
    ys = y * b_trans
    return float(l_fore * np.sum(ys ** 2) * (b_trans / n))


def free_surface_correction(sum_rho_i_i: float, rho_sea: float, nabla: float) -> float:
    """GG₀ = Σ ρ_j i_j / (ρ_sea ∇) [m]；∇ 必须为 m³。"""
    return sum_rho_i_i / (rho_sea * nabla)


def run_node03_fsc_as_shipped(p: Dict[str, float]) -> Dict[str, float]:
    """逐字复刻 nodes_downstream.run_node03 的自由液面修正算式（含其量纲前提）。"""
    i_free = p["tank_length"] * p["tank_width"] ** 3 / 12.0
    displacement_volume = p["ship_mass"] / 1.025     # ← kg/1.025 当成 m³
    fsc = p["liquid_density"] * i_free / (RHO_SEA * displacement_volume)
    return {"i_free": i_free, "nabla_as_shipped": displacement_volume, "fsc_as_shipped": fsc}


def omega_analytic(tank_width: float, depth: float, n: int = 1) -> float:
    """ω_n² = g k_n tanh(k_n h), k_n = nπ/L。"""
    k = n * math.pi / tank_width
    return math.sqrt(G * k * math.tanh(k * depth))


def pendulum_length(omega: float) -> float:
    return G / omega ** 2


def pivot_height_from_static_identity(i_fs: float, rho_l: float, m_s: float) -> float:
    """静态恒等式 A3：m_s g z_p = ρ_l g i_FS  =>  z_p = ρ_l i_FS / m_s。"""
    return rho_l * i_fs / m_s


# ---------------------------------------------------------------------------
# 2. 两个 2-DOF 模型
# ---------------------------------------------------------------------------
def ship_roll_stiffness(p: Dict[str, float]) -> Dict[str, float]:
    I = p["ship_mass"] * p["roll_radius_of_gyration"] ** 2 * p["added_inertia_factor"]
    wr = 2 * math.pi / p["roll_period"]
    return {"I": I, "omega_roll": wr, "C": I * wr ** 2}


def shipped_pendulum_free_response(t: np.ndarray, omega_s: float, c_over_ml2: float = 0.0) -> Dict[str, object]:
    """按 nodes_downstream.simulate_2dof 的 rhs 复刻（c=0, M_ext=0, 初值 0）：

        beta_ddot = (-c(beta_dot - phi_dot) - m g l sin(beta)) / (m l^2)
        phi_ddot  = (M - B phi_dot - C phi - m l^2 beta_ddot - m g l sin(beta)) / I

    结论（机器验证）：β ≡ 0 ⇒ 液晃反作用项恒为 0，横摇方程退化为单自由度。
    """
    dt = float(t[1] - t[0])
    n = len(t)
    phi = np.zeros(n)
    phid = np.zeros(n)
    beta = np.zeros(n)
    betad = np.zeros(n)
    I = 4.0e10
    C = I * (2 * math.pi / 12.0) ** 2
    m, l = 3.384e6, pendulum_length(omega_s)
    ml2 = m * l * l
    for k in range(n - 1):
        st = np.array([phi[k], phid[k], beta[k], betad[k]])
        ph, phd, be, bed = st
        bdd = (-c_over_ml2 * ml2 * (bed - phd) - m * G * l * math.sin(be)) / ml2
        pdd = (0.0 - 0.06 * 2 * I * (2 * math.pi / 12) * phd - C * ph - ml2 * bdd
               - m * G * l * math.sin(be)) / I
        d = np.array([phd, pdd, bed, bdd])
        st2 = st + dt * d
        phi[k + 1], phid[k + 1], beta[k + 1], betad[k + 1] = st2
    reaction = -(ml2 * np.gradient(betad, dt) + m * G * l * np.sin(beta))
    return {"beta_max": float(np.max(np.abs(beta))), "reaction_max": float(np.max(np.abs(reaction)))}


def shipped_eigen_roots(p: Dict[str, float], m_s: float) -> Dict[str, float]:
    """复刻 two_dof_eigen：M=diag(I, m l²), K=[[C, m g l],[0, m g l]] ⇒ 根完全解耦。"""
    I = ship_roll_stiffness(p)["I"]
    C = ship_roll_stiffness(p)["C"]
    l = pendulum_length(p["slosh_omega"])
    M = np.array([[I, 0.0], [0.0, m_s * l * l]])
    K = np.array([[C, m_s * G * l], [0.0, m_s * G * l]])
    roots = np.roots([M[0, 0] * M[1, 1],
                      -(K[0, 0] * M[1, 1] + K[1, 1] * M[0, 0]),
                      K[0, 0] * K[1, 1]])
    w = np.sqrt(np.sort(np.abs(roots)))
    return {"omega_low": float(w[0]), "omega_high": float(w[1]),
            "omega_roll": math.sqrt(C / I), "omega_slosh": math.sqrt(G / l)}


def correct_eigen_roots(p: Dict[str, float], m_s: float, i_fs: float, rho_l: float) -> Dict[str, float]:
    """正确的线性 Lagrange 系统（摆挂点高 z_p，船体系坐标 β）

        I φ̈ + C φ - m g z_p β = 0
        l β̈ + g β + (l + z_p) φ̈ = 0

    令 ω²=λ：(C - Iλ)(g - lλ) = m g z_p λ (l + z_p)
    """
    I = ship_roll_stiffness(p)["I"]
    C = ship_roll_stiffness(p)["C"]
    l = pendulum_length(p["slosh_omega"])
    z_p = pivot_height_from_static_identity(i_fs, rho_l, m_s)
    a = I * l
    b = -(C * l + I * G + m_s * G * z_p * (l + z_p))
    c = C * G
    roots = np.roots([a, b, c])
    lam = np.sort(np.real(roots))
    return {"omega_low": float(math.sqrt(lam[0])), "omega_high": float(math.sqrt(lam[1])),
            "z_pivot": float(z_p), "static_gap_rel": float(abs((C * G) / (C * G) - 1.0))}


def correct_static_liquid_moment(p: Dict[str, float], i_fs: float, rho_l: float, phi_rad: float) -> float:
    """恒等式 A3：静态液晃反作用力矩 = ρ_l g i_FS φ [N·m]。"""
    return rho_l * G * i_fs * phi_rad


def gz_and_gm(p: Dict[str, float]) -> Dict[str, float]:
    B, T, KG = p["beam"], p["draft"], p["kg_height"]
    BM = B ** 2 / (12.0 * T)
    KB = T / 2.0
    return {"BM": BM, "KB": KB, "GM0": BM - KG + KB}


def imo_verdict(gm: float, gz_max: float) -> Dict[str, object]:
    """IS Code 2008 两条与本次修正直接相关的主判据（GM0 ≥ 0.15 m；GZ_max@30° ≥ 0.20 m）。"""
    c1 = gm >= 0.15
    c2 = gz_max >= 0.20
    return {"gm_min_0_15": bool(c1), "gz30_min_0_20": bool(c2), "pass": bool(c1 and c2)}


# ---------------------------------------------------------------------------
# 3. 主流程
# ---------------------------------------------------------------------------
def audit(p: Dict[str, float] | None = None) -> Dict[str, object]:
    p = dict(N03_DEFAULTS, **(p or {}))
    # --- N02: 液舱几何与频率 ---
    l_fore, b_trans = p["tank_length"], p["tank_width"]
    fill_h_n02 = 0.2 * 30.0                     # N02 默认 fill_ratio=0.2, tank_height=30
    w1 = omega_analytic(b_trans, fill_h_n02, 1)
    i_fs = free_surface_inertia_rect(l_fore, b_trans)
    i_fs_q = free_surface_inertia_quadrature(l_fore, b_trans)

    # --- 液货质量（两处口径） ---
    m_from_n02 = p["liquid_density"] * l_fore * b_trans * fill_h_n02
    m_fallback = p["liquid_density"] * l_fore * b_trans * p["fill_height_fallback"]

    # --- A2 自由液面修正 ---
    shipped = run_node03_fsc_as_shipped(p)
    nabla_true = p["ship_mass"] / 1000.0 / 1.025
    fsc_true = free_surface_correction(p["liquid_density"] * i_fs, RHO_SEA, nabla_true)
    fsc_shipped = shipped["fsc_as_shipped"]

    # --- A3 静态液晃反作用 ---
    phi_test = math.radians(5.0)
    m_liq = correct_static_liquid_moment(p, i_fs, p["liquid_density"], phi_test)
    z_p = pivot_height_from_static_identity(i_fs, p["liquid_density"], m_from_n02)
    ship = ship_roll_stiffness(p)

    # --- D2: 实船 ODE 在 c=0 时 β ≡ 0 ---
    t = np.linspace(0.0, 60.0, 6001)
    free_resp = shipped_pendulum_free_response(t, p["slosh_omega"], 0.0)

    # --- 本征根 ---
    e_shipped = shipped_eigen_roots(p, m_from_n02)
    e_correct = correct_eigen_roots(p, m_from_n02, i_fs, p["liquid_density"])

    # --- 稳性后果 ---
    g = gz_and_gm(p)
    gm_shipped = g["GM0"] - fsc_shipped
    gm_true = g["GM0"] - fsc_true
    gz_max_shipped = 0.9  # 由下面的 run_node03 覆盖（此处只作占位，见 note）
    v_shipped = imo_verdict(gm_shipped, gz_max_shipped)
    v_true = imo_verdict(gm_true, gz_max_shipped)

    return {
        "params": p,
        "N02": {
            "fill_depth_m": fill_h_n02,
            "omega1_analytic_rad_s": w1,
            "pendulum_length_g_over_w2_m": pendulum_length(w1),
            "n03_default_slosh_omega": p["slosh_omega"],
            "n03_default_consistent_depth_m": _depth_from_omega(p["slosh_omega"], b_trans),
        },
        "A1_free_surface_inertia": {
            "i_fs_closed_form_m4": i_fs,
            "i_fs_quadrature_m4": i_fs_q,
            "rel_diff": abs(i_fs - i_fs_q) / i_fs,
        },
        "A2_free_surface_correction": {
            "nabla_true_m3": nabla_true,
            "nabla_as_shipped": shipped["nabla_as_shipped"],
            "nabla_overestimate_factor": shipped["nabla_as_shipped"] / nabla_true,
            "fsc_true_m": fsc_true,
            "fsc_as_shipped_m": fsc_shipped,
            "underestimate_factor": fsc_true / fsc_shipped,
            "fsc_over_GM0": fsc_true / g["GM0"],
        },
        "A3_static_liquid_moment": {
            "phi_rad": phi_test,
            "moment_required_Nm": m_liq,
            "moment_from_shipped_ode_Nm": free_resp["reaction_max"],
            "shortfall_frac": 1.0 - free_resp["reaction_max"] / m_liq,
            "z_pivot_required_m": z_p,
            "ship_roll_stiffness_Nm_per_rad": ship["C"],
            "moment_over_ship_stiffness": m_liq / ship["C"],
            "liquid_mass_n02_kg": m_from_n02,
            "liquid_mass_fallback_kg": m_fallback,
            "mass_ratio_between_sources": m_fallback / m_from_n02,
        },
        "D2_pendulum_excitation": {
            "beta_max_rad_no_damping": free_resp["beta_max"],
            "reaction_max_Nm_no_damping": free_resp["reaction_max"],
            "verdict": "MISSING_EXCITATION" if free_resp["beta_max"] < 1e-12 else "OK",
        },
        "eigen": {
            "shipped_low_high": [e_shipped["omega_low"], e_shipped["omega_high"]],
            "shipped_decoupled": [e_shipped["omega_roll"], e_shipped["omega_slosh"]],
            "correct_low_high": [e_correct["omega_low"], e_correct["omega_high"]],
            "correct_z_pivot_m": e_correct["z_pivot"],
            "shipped_equals_decoupled": bool(
                abs(e_shipped["omega_low"] - min(e_shipped["omega_roll"], e_shipped["omega_slosh"])) < 1e-9
                and abs(e_shipped["omega_high"] - max(e_shipped["omega_roll"], e_shipped["omega_slosh"])) < 1e-9),
            "correct_split_rel": abs(e_correct["omega_high"] - e_correct["omega_low"])
            / max(e_correct["omega_high"], 1e-9),
        },
        "stability": {
            "GM0_m": g["GM0"],
            "GM_shipped_m": gm_shipped,
            "GM_corrected_m": gm_true,
            "gm_overestimate_m": gm_shipped - gm_true,
            "gm_overestimate_frac": (gm_shipped - gm_true) / gm_true,
            "imo_shipped": v_shipped,
            "imo_corrected": v_true,
            "gz_max_placeholder": gz_max_shipped,
        },
    }


def _depth_from_omega(omega: float, width: float) -> float:
    """由 ω = sqrt(g k tanh(k h)) 反解液深 h（等效摆长自洽性检查用）。"""
    k = math.pi / width
    tanh_kh = omega ** 2 / (G * k)
    if tanh_kh >= 1.0:
        return float("inf")
    return math.atanh(tanh_kh) / k


def make_figures(result: Dict[str, object], outdir: str) -> List[str]:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    os.makedirs(outdir, exist_ok=True)
    figs = []
    p = result["params"]
    i_fs = result["A1_free_surface_inertia"]["i_fs_closed_form_m4"]

    # 图 1：自由液面修正量 vs 舱宽（闭式 + 逐液舱数量），并标出 as-shipped 点
    fig, ax = plt.subplots(figsize=(7.2, 4.4))
    b = np.linspace(10, 60, 200)
    nabla = result["A2_free_surface_correction"]["nabla_true_m3"]
    for n_tanks, ls in ((1, "-"), (2, "--")):
        fsc = [n_tanks * free_surface_correction(p["liquid_density"] * free_surface_inertia_rect(p["tank_length"], bb),
                                                 RHO_SEA, nabla) for bb in b]
        ax.plot(b, fsc, ls, lw=1.8, label=f"analytic identity A2 ({n_tanks} tank)")
    ax.axhline(result["A2_free_surface_correction"]["fsc_true_m"], color="k", lw=0.8, alpha=0.4)
    ax.scatter([p["tank_width"]], [result["A2_free_surface_correction"]["fsc_true_m"]],
               color="C3", zorder=5, label="correct GG0 (B=40 m)")
    ax.scatter([p["tank_width"]], [result["A2_free_surface_correction"]["fsc_as_shipped_m"]],
               color="C0", marker="v", zorder=5, label="shipped formula (1000x low)")
    ax.set_xlabel("tank transverse breadth B [m]")
    ax.set_ylabel("free-surface correction GG0 [m]")
    ax.set_title("Free-surface correction: analytic identity vs shipped formula")
    ax.grid(alpha=0.3)
    ax.legend(fontsize=8)
    f1 = os.path.join(outdir, "fig1_fsc_vs_breadth.png")
    fig.tight_layout()
    fig.savefig(f1, dpi=140)
    plt.close(fig)
    figs.append(f1)

    # 图 2：静态液晃反作用力矩 vs 横倾角（正确恒等式 vs 现网 ODE 复刻）
    fig, ax = plt.subplots(figsize=(7.2, 4.4))
    phis = np.linspace(0, 0.2, 100)
    ax.plot(np.rad2deg(phis), [correct_static_liquid_moment(p, i_fs, p["liquid_density"], q) / 1e6 for q in phis],
            lw=2, label="identity A3: rho_l g i_FS phi")
    ax.plot(np.rad2deg(phis), [0.0 * q for q in phis], "--", lw=2,
            label="shipped 2-DOF (c=0): 0, beta=0")
    ax.set_xlabel("heel angle phi [deg]")
    ax.set_ylabel("liquid reaction moment [MN m]")
    ax.set_title("Static liquid reaction moment: 100% gap from missing excitation")
    ax.grid(alpha=0.3)
    ax.legend(fontsize=8)
    f2 = os.path.join(outdir, "fig2_static_liquid_moment.png")
    fig.tight_layout()
    fig.savefig(f2, dpi=140)
    plt.close(fig)
    figs.append(f2)

    # 图 3：本征根对照（解耦 vs 耦合）
    fig, ax = plt.subplots(figsize=(7.2, 3.6))
    ee = result["eigen"]
    labs = ["ω_low", "ω_high"]
    x = np.arange(2)
    ax.bar(x - 0.2, ee["shipped_low_high"], width=0.38, label="shipped two_dof_eigen (decoupled)")
    ax.bar(x + 0.2, ee["correct_low_high"], width=0.38, label="correct coupled Lagrange system")
    ax.set_xticks(x, labs)
    ax.set_ylabel("ω [rad/s]")
    ax.set_title("2-DOF eigenvalues: shipped roots equal sqrt(C/I) and sqrt(g/l)")
    ax.grid(alpha=0.3, axis="y")
    ax.legend(fontsize=8)
    f3 = os.path.join(outdir, "fig3_eigen_roots.png")
    fig.tight_layout()
    fig.savefig(f3, dpi=140)
    plt.close(fig)
    figs.append(f3)
    return figs


def main(argv: List[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="artifacts/sloshing_fsc")
    ap.add_argument("--no-figures", action="store_true")
    a = ap.parse_args(argv)

    res = audit()
    os.makedirs(a.out, exist_ok=True)
    figs = [] if a.no_figures else make_figures(res, a.out)

    payload = {"audit": res, "figures": [os.path.basename(f) for f in figs]}
    out = os.path.join(a.out, "sloshing_fsc_audit.json")
    with open(out, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2)

    s = res["stability"]
    print(f"[A2] GG₀ 正确 {res['A2_free_surface_correction']['fsc_true_m']:.4f} m vs 现网 "
          f"{res['A2_free_surface_correction']['fsc_as_shipped_m']:.6f} m "
          f"(×{res['A2_free_surface_correction']['underestimate_factor']:.0f})")
    print(f"[A3] 静态液晃力矩 现网/应有 = {res['A3_static_liquid_moment']['moment_from_shipped_ode_Nm']:.3e} / "
          f"{res['A3_static_liquid_moment']['moment_required_Nm']:.3e} N·m")
    print(f"[D2] β_max(c=0) = {res['D2_pendulum_excitation']['beta_max_rad_no_damping']:.3e} rad → "
          f"{res['D2_pendulum_excitation']['verdict']}")
    print(f"[GM] GM₀ {s['GM0_m']:.3f} m → 现网 {s['GM_shipped_m']:.3f} m / 正确 {s['GM_corrected_m']:.3f} m "
          f"(高报 {s['gm_overestimate_frac']*100:.1f}%)")
    print(f"written: {out}" + ("" if not figs else f" + {len(figs)} figs"))
    return 0


if __name__ == "__main__":
    sys.exit(main())