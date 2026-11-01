"""Generate the m1694 torsional-vibration verification artefacts.

Runs the shafting engine over (a) the analytic benchmark ladder and (b) the
direct-drive diesel case study, dumps every number to
``artifacts/m1694/shaft_torsion_metrics.json`` (so the mission report can inject
them programmatically instead of by hand) and draws four figures:

1. Campbell diagram -- excitation-order rays vs the mode frequencies, crossings
   marked at ``n_res = 60 f_n / q``.
2. Mode shapes of the case-study plant (with nodal stations marked).
3. Lumped-chain convergence onto the exact rod spectrum (log-log, O(h^2) slope).
4. Forced-response amplification: engine modal superposition vs the textbook
   single-DOF closed form, plus the tail-shaft shear-stress spectrum vs order.

Usage:  python3 tools/m1694_shaft_torsion_report.py
"""
from __future__ import annotations

import json
import math
import os
import sys
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
sys.path.insert(0, str(REPO / "backend"))

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

from services.shaft_torsion import (  # noqa: E402
    TorsionalChain,
    detuning_margin,
    forced_response_stress,
    holzer_frequencies,
    mode_shapes,
    natural_frequencies,
    nodal_stations,
    resonance_table,
    two_inertia_frequency,
    uniform_shaft_analytic_frequencies,
    uniform_shaft_chain,
)

G_STEEL = 79.3e9
RHO_STEEL = 7850.0
OUT = REPO / "artifacts" / "m1694"
OUT.mkdir(parents=True, exist_ok=True)


# ---------------------------------------------------------------------------
# Case study: direct-drive 6-cylinder two-stroke plant, 9000 kW @ 102 r/min
# ---------------------------------------------------------------------------
CASE = dict(
    j_prop=4800.0,      # kg m^2 propeller + entrained water
    j_int=700.0,        # kg m^2 intermediate shafting
    j_eng=12000.0,      # kg m^2 engine rotating parts + flywheel
    k_tail=1.55e7,      # N m/rad tail shaft  (d=0.50 m, L=14 m, G=79.3 GPa)
    k_int=3.10e7,       # N m/rad intermediate shaft
    d_tail=0.50,        # m, tail shaft diameter
    zeta=0.02,
    service_rpm=102.0,
    power_kw=9000.0,
    harmonic_fraction=0.02,   # Q4 / nominal torque (illustrative)
    kt_stress=1.30,           # stress concentration factor at the keyway
    fatigue_strength_pa=7.0e7,
    blade_number=4,
    cylinders=6,
    two_stroke=True,
)


def case_chain(zeta: float | None = None) -> TorsionalChain:
    return TorsionalChain(
        (CASE["j_prop"], CASE["j_int"], CASE["j_eng"]),
        (CASE["k_tail"], CASE["k_int"]),
        labels=("propeller", "intermediate", "engine"),
        damping_ratio=CASE["zeta"] if zeta is None else zeta,
    )


def benchmark_ladder() -> dict:
    """Analytic benchmark errors that justify the REAL evidence grade."""
    j1, j2, k = 1200.0, 3000.0, 2.4e7
    f2 = natural_frequencies(TorsionalChain((j1, j2), (k,)), include_rigid=False)[0]
    exact2 = two_inertia_frequency(j1, j2, k)

    # uniform-rod convergence: L=30 m, d=0.40 m steel
    L, d = 30.0, 0.40
    ana = uniform_shaft_analytic_frequencies(3, L, G_STEEL, RHO_STEEL)
    conv = []
    for n in (11, 21, 41, 81, 161, 321):
        ch = uniform_shaft_chain(n, L, d, G_STEEL, RHO_STEEL)
        num = natural_frequencies(ch, include_rigid=False)[:3]
        conv.append(
            {
                "n_stations": n,
                "h_m": L / (n - 1),
                "f_hz": [float(x) for x in num],
                "rel_error": [float((x - y) / y) for x, y in zip(num, ana)],
            }
        )

    # Holzer vs eigensolver on a 3-station plant and on a 41-station rod
    hol_plant = holzer_frequencies(case_chain(), n_modes=2)
    eig_plant = natural_frequencies(case_chain(), include_rigid=False)
    rod = uniform_shaft_chain(41, L, d, G_STEEL, RHO_STEEL)
    hol_rod = holzer_frequencies(rod, n_modes=3)
    eig_rod = natural_frequencies(rod, include_rigid=False)[:3]

    return {
        "two_inertia": {
            "eigen_hz": float(f2),
            "closed_form_hz": float(exact2),
            "rel_error": float(abs(f2 - exact2) / exact2),
        },
        "uniform_rod": {
            "length_m": L,
            "diameter_m": d,
            "wave_speed_m_s": float(math.sqrt(G_STEEL / RHO_STEEL)),
            "analytic_hz": [float(x) for x in ana],
            "convergence": conv,
        },
        "holzer_vs_eigen": {
            "plant_hz_eigen": [float(x) for x in eig_plant],
            "plant_hz_holzer": [float(x) for x in hol_plant],
            "plant_max_rel_diff": float(np.max(np.abs(hol_plant - eig_plant) / eig_plant)),
            "rod41_hz_eigen": [float(x) for x in eig_rod],
            "rod41_hz_holzer": [float(x) for x in hol_rod],
            "rod41_max_rel_diff": float(np.max(np.abs(hol_rod - eig_rod) / eig_rod)),
        },
    }


def forced_sdof_error() -> dict:
    """Modal superposition vs ``1/sqrt((1-r^2)^2+(2 zeta r)^2)``."""
    j1, j2, k = 1200.0, 3000.0, 2.4e7
    rows = []
    for zeta in (0.005, 0.02, 0.05):
        chain = TorsionalChain((j1, j2), (k,), damping_ratio=zeta)
        f1 = natural_frequencies(chain, include_rigid=False)[0]
        for r in (0.4, 0.8, 1.0, 1.6):
            q = 1.0e4
            res = forced_response_stress(
                chain, 2 * math.pi * f1 * r, (q, -q), (1.0,), stress_concentration_factor=1.0
            )
            twist = abs(res["theta_complex"][0] - res["theta_complex"][1])
            amp = 1.0 / math.sqrt((1 - r ** 2) ** 2 + (2 * zeta * r) ** 2)
            rows.append(
                {
                    "zeta": zeta,
                    "r": r,
                    "twist_rad": float(twist),
                    "closed_form_rad": float(q / k * amp),
                    "rel_error": float(abs(twist - q / k * amp) / (q / k * amp)),
                }
            )
    return {"rows": rows, "max_rel_error": max(r["rel_error"] for r in rows)}


def case_study() -> dict:
    chain = case_chain()
    f = natural_frequencies(chain, include_rigid=False)
    phi = mode_shapes(chain)
    orders = [1.0, 2.0, 3.0, 4.0, 6.0, 8.0, 12.0]
    rpm_range = (20.0, 140.0)
    table = resonance_table(chain, orders=orders, rpm_range=rpm_range)

    omega_s = 2 * math.pi * CASE["service_rpm"] / 60.0
    nominal_torque = (CASE["power_kw"] * 1e3) / omega_s
    q_amp = CASE["harmonic_fraction"] * nominal_torque
    wt = math.pi * CASE["d_tail"] ** 3 / 16.0
    resp = forced_response_stress(
        chain,
        omega_s,
        (q_amp, -q_amp, 0.0),
        (wt, math.pi * 0.30 ** 3 / 16.0),
        stress_concentration_factor=CASE["kt_stress"],
        fatigue_strength_pa=CASE["fatigue_strength_pa"],
    )

    det_blade = detuning_margin(
        chain, order=float(CASE["blade_number"]), rpm=CASE["service_rpm"], mode=1
    )
    firing_order = float(CASE["cylinders"]) if CASE["two_stroke"] else CASE["cylinders"] / 2.0
    det_fire = detuning_margin(chain, order=firing_order, rpm=CASE["service_rpm"], mode=1)

    # forced-response spectrum over excitation orders at service speed
    spectrum = []
    for q in np.arange(1.0, 13.01, 0.5):
        om = omega_s * q / CASE["blade_number"] * CASE["blade_number"]  # = q * omega_shaft
        om = q * omega_s
        r = forced_response_stress(
            chain, om, (q_amp, -q_amp, 0.0),
            (wt, math.pi * 0.30 ** 3 / 16.0),
            stress_concentration_factor=CASE["kt_stress"],
        )
        spectrum.append(
            {
                "order": float(q),
                "frequency_hz": float(om / (2 * math.pi)),
                "tau_tail_pa": float(r["tau_amplitude_pa"][0]),
            }
        )

    return {
        "parameters": CASE,
        "natural_frequencies_hz": [float(x) for x in f],
        "rigid_body_hz": float(natural_frequencies(chain)[0]),
        "mode_shapes": [
            {"mode": i + 1, "frequency_hz": float(f[i]),
             "shape": [float(x) for x in phi[:, i + 1]],
             "nodal_stations": nodal_stations(chain, i + 1)}
            for i in range(len(f))
        ],
        "campbell": table,
        "orders": orders,
        "rpm_range": list(rpm_range),
        "detuning_blade_rate": det_blade,
        "detuning_firing_order": det_fire,
        "service": {
            "rpm": CASE["service_rpm"],
            "shaft_omega_rad_s": omega_s,
            "nominal_torque_nm": nominal_torque,
            "harmonic_torque_nm": q_amp,
            "tail_section_modulus_m3": wt,
            "tail_tau_pa": float(resp["tau_amplitude_pa"][0]),
            "tail_twist_rad": float(abs(resp["theta_complex"][0] - resp["theta_complex"][1])),
            "stress_ratio": float(resp["stress_ratio"][0]),
            "verdict": resp["verdict"],
        },
        "order_spectrum": spectrum,
    }


# ---------------------------------------------------------------------------
# Figures
# ---------------------------------------------------------------------------

def fig_campbell(cs: dict, path: Path) -> None:
    fig, ax = plt.subplots(figsize=(7.2, 4.6), dpi=150)
    rpm = np.linspace(max(cs["rpm_range"][0], 1.0), cs["rpm_range"][1], 400)
    for q in cs["orders"]:
        ax.plot(rpm, q * rpm / 60.0, lw=0.8, color="0.55", zorder=1)
        ax.text(rpm[-1], q * rpm[-1] / 60.0, f" q={q:g}", fontsize=7, va="center", color="0.35")
    colors = ["tab:red", "tab:blue", "tab:green"]
    for i, f_n in enumerate(cs["natural_frequencies_hz"]):
        ax.axhline(f_n, color=colors[i % 3], lw=1.6,
                   label=f"mode {i+1}: {f_n:.2f} Hz")
    for row in cs["campbell"]:
        ax.plot(row["rpm"], row["frequency_hz"], "o", ms=5, mfc="white",
                mec=colors[(row["mode"] - 1) % 3], mew=1.4, zorder=3)
    ax.axvline(cs["service"]["rpm"], color="k", ls="--", lw=1.0)
    ax.text(cs["service"]["rpm"], ax.get_ylim()[1] * 0.97, " service 102 r/min",
            fontsize=7, va="top")
    ax.set_xlabel("shaft speed n [r/min]")
    ax.set_ylabel("frequency [Hz]")
    ax.set_title("Campbell diagram — direct-drive 6-cyl plant (open circles = resonance crossings)")
    ax.legend(loc="upper left", fontsize=8)
    ax.grid(alpha=0.3)
    fig.tight_layout()
    fig.savefig(path)
    plt.close(fig)


def fig_modes(cs: dict, path: Path) -> None:
    fig, ax = plt.subplots(figsize=(6.6, 4.0), dpi=150)
    x = np.arange(3)
    labels = ["propeller", "interm.", "engine"]
    for i, m in enumerate(cs["mode_shapes"]):
        shape = np.asarray(m["shape"], dtype=float)
        shape = shape / np.max(np.abs(shape))
        ax.plot(x, shape, "o-", label=f"mode {m['mode']}: {m['frequency_hz']:.2f} Hz")
        for st in m["nodal_stations"]:
            ax.plot([st + 0.5], [0.0], "x", color="k", ms=7)
    ax.axhline(0.0, color="0.6", lw=0.8)
    ax.set_xticks(x)
    ax.set_xticklabels(labels)
    ax.set_ylabel("normalised amplitude  θ/θ_max")
    ax.set_title("Mode shapes (x = node inside a shaft segment)")
    ax.legend(fontsize=8)
    ax.grid(alpha=0.3)
    fig.tight_layout()
    fig.savefig(path)
    plt.close(fig)


def fig_convergence(bm: dict, path: Path) -> None:
    conv = bm["uniform_rod"]["convergence"]
    h = np.array([c["h_m"] for c in conv])
    fig, ax = plt.subplots(figsize=(6.4, 4.2), dpi=150)
    for k, mk in zip(range(3), ("o", "s", "^")):
        err = np.array([abs(c["rel_error"][k]) for c in conv])
        ax.loglog(h, err, mk + "-", label=f"mode {k+1}")
    ref = abs(conv[0]["rel_error"][0]) * (h / h[0]) ** 2
    ax.loglog(h, ref, "k--", lw=1.0, label=r"$O(h^2)$ reference")
    ax.set_xlabel("station spacing h [m]")
    ax.set_ylabel("|relative error| vs exact rod spectrum")
    ax.set_title(r"Lumped chain → exact $f_n = n\sqrt{G/\rho}/(2L)$")
    ax.grid(alpha=0.3, which="both")
    ax.legend(fontsize=8)
    fig.tight_layout()
    fig.savefig(path)
    plt.close(fig)


def fig_response(bm: dict, cs: dict, path: Path) -> None:
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(10.4, 4.0), dpi=150)
    # left: amplification vs SDOF closed form
    j1, j2, k = 1200.0, 3000.0, 2.4e7
    zeta = 0.02
    chain = TorsionalChain((j1, j2), (k,), damping_ratio=zeta)
    f1 = natural_frequencies(chain, include_rigid=False)[0]
    rr = np.linspace(0.2, 1.8, 200)
    num = []
    for r in rr:
        res = forced_response_stress(chain, 2 * math.pi * f1 * r, (1.0, -1.0), (1.0,))
        num.append(abs(res["theta_complex"][0] - res["theta_complex"][1]) * k)
    ana = 1.0 / np.sqrt((1 - rr ** 2) ** 2 + (2 * zeta * rr) ** 2)
    ax1.plot(rr, ana, "-", lw=2.0, label="closed form 1/√((1−r²)²+(2ζr)²)")
    ax1.plot(rr, num, "o", ms=3, mfc="none", label="modal superposition (engine)")
    ax1.set_xlabel("frequency ratio r = ω/ω₁")
    ax1.set_ylabel("dynamic amplification")
    ax1.set_title(f"Two-inertia chain, ζ={zeta}")
    ax1.legend(fontsize=7)
    ax1.grid(alpha=0.3)
    # right: tail-shaft shear stress vs excitation order at service speed
    orders = [row["order"] for row in cs["order_spectrum"]]
    tau = [row["tau_tail_pa"] / 1e6 for row in cs["order_spectrum"]]
    ax2.semilogy(orders, tau, "o-", ms=3, color="tab:red")
    ax2.axhline(CASE["fatigue_strength_pa"] / 1e6, color="k", ls=":",
                label="illustrative fatigue strength 70 MPa")
    ax2.set_xlabel("excitation order q  (f = q·n/60)")
    ax2.set_ylabel("tail-shaft shear stress amplitude [MPa]")
    ax2.set_title("Forced response at 102 r/min")
    ax2.legend(fontsize=7)
    ax2.grid(alpha=0.3, which="both")
    fig.tight_layout()
    fig.savefig(path)
    plt.close(fig)


def main() -> dict:
    bm = benchmark_ladder()
    sdof = forced_sdof_error()
    cs = case_study()
    metrics = {"benchmarks": bm, "forced_sdof": sdof, "case_study": cs,
               "engine": "backend/services/shaft_torsion.py"}
    (OUT / "shaft_torsion_metrics.json").write_text(
        json.dumps(metrics, indent=2, ensure_ascii=False)
    )
    fig_campbell(cs, OUT / "campbell.png")
    fig_modes(cs, OUT / "mode_shapes.png")
    fig_convergence(bm, OUT / "convergence.png")
    fig_response(bm, cs, OUT / "forced_response.png")
    return metrics


if __name__ == "__main__":
    m = main()
    print("wrote", OUT)
    print("modes:", [round(x, 3) for x in m["case_study"]["natural_frequencies_hz"]])
    print("rod rel errors (mode1):", [round(abs(c["rel_error"][0]), 8)
                                     for c in m["benchmarks"]["uniform_rod"]["convergence"]])
    print("sdof max rel err:", m["forced_sdof"]["max_rel_error"])
    print("campbell crossings:", len(m["case_study"]["campbell"]))
    print("detuning blade:", m["case_study"]["detuning_blade_rate"])
    print("service stress MPa:", m["case_study"]["service"]["tail_tau_pa"] / 1e6)