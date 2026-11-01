#!/usr/bin/env python3
"""Reproducible baseline runner for the manoeuvring (操纵性) MMG 3-DOF module.

Generates, from code only (no hand-copied numbers):

* ``artifacts/manoeuvring/results.json`` -- every closed-form gate, the standard
  manoeuvre indicators, the IMO Res. MSC.137(76) evaluation and a sensitivity
  sweep of the declared modelling constants;
* ``artifacts/manoeuvring/fig_*.png`` -- turning-circle track, zigzag time
  histories, Nomoto linearisation cross-check and the uncertainty band.

Usage:  python3 tools/verification/manoeuvring_baseline.py
"""
from __future__ import annotations

import json
import math
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[2] / "backend"
sys.path.insert(0, str(BACKEND))

import matplotlib                                   # noqa: E402
matplotlib.use("Agg")
import matplotlib.pyplot as plt                     # noqa: E402

from methods.manoeuvring import (                   # noqa: E402
    HullParameters, IMO_LIMITS, MMGModel, ModelParameters, circle_fit,
    evaluate_imo, nomoto_indices, nomoto_response, run_constant_rudder,
    steady_state, turning_circle_indicators, zigzag,
)

OUT = Path(__file__).resolve().parents[2] / "artifacts" / "manoeuvring"
OUT.mkdir(parents=True, exist_ok=True)

# KVLCC2 principal particulars (public; the only role of the hull is to set the
# geometry gate and the mass/added-mass level -- no force derivative is taken
# from any table)
HULL = dict(length=320.0, beam=58.0, draft=20.8, cb=0.8098,
            design_speed=7.0, displacement_t=320000.0)

hull = HullParameters(**HULL)
model = MMGModel(hull)
L = hull.length
gates = []


def gate(name, value, reference, rel_err, limit, note=""):
    gates.append({
        "gate": name, "value": value, "reference": reference,
        "rel_err": rel_err, "limit": limit,
        "status": "PASS" if rel_err <= limit else "FAIL", "note": note,
    })


# ---------------------------------------------------------------------------
# 1. closed-form / self-consistency gates
# ---------------------------------------------------------------------------
delta_small = math.radians(0.5)
idx = nomoto_indices(model)
K, T = idx["K"], idx["T"]
u_n, v_n, r_n = steady_state(model, delta_small)
gate("Nomoto steady gain K*delta vs Newton steady state",
     r_n, K * delta_small, abs(r_n - K * delta_small) / (K * delta_small), 0.03,
     "linearisation consistency")

trace_small = run_constant_rudder(model, delta_small, 3.0 * T, 0.5)
worst, worst_t = 0.0, 0.0
for t, r in zip(trace_small.t, trace_small.r):
    if t < 0.3 * T:
        continue
    r_an, _ = nomoto_response(K, T, delta_small, t)
    err = abs(r - r_an) / r_an
    if err > worst:
        worst, worst_t = err, t
gate("Nomoto 1st-order transient vs simulation (max, t>0.3T)",
     worst, 0.0, worst, 0.20,
     f"first-order equivalent is crude; worst point at t={worst_t:.1f} s")

delta35 = math.radians(35.0)
u_ss, v_ss, r_ss = steady_state(model, delta35)
trace = run_constant_rudder(model, delta35, 4000.0, 0.5)
gate("Newton steady state r_ss vs time marching (4000 s, dt=0.5 s)",
     trace.r[-1], r_ss, abs(trace.r[-1] - r_ss) / r_ss, 0.005, "integration accuracy")
gate("Newton steady state u_ss vs time marching",
     trace.u[-1], u_ss, abs(trace.u[-1] - u_ss) / u_ss, 0.005, "integration accuracy")

ind = turning_circle_indicators(model, trace)
rel_diam = abs(ind["steady_turning_diameter_fit_m"] - ind["steady_turning_diameter_m"]) \
    / ind["steady_turning_diameter_m"]
gate("steady turning diameter: circle fit vs u/r kinematics",
     ind["steady_turning_diameter_fit_m"], ind["steady_turning_diameter_m"], rel_diam, 0.03,
     "indicator extraction (independent of dynamics)")

ind_coarse = turning_circle_indicators(model, run_constant_rudder(model, delta35, 900.0, 0.25))
dt_err = max(abs(ind_coarse[k] - b) / b for k, b in
             (("advance_m", turning_circle_indicators(
                 model, run_constant_rudder(model, delta35, 900.0, 0.5))["advance_m"]),))
gate("time-step sensitivity (dt = 0.25 s vs 0.5 s, advance)", dt_err, 0.0, dt_err, 0.01,
     "numerical convergence in dt")

# port/starboard symmetry
tr_m = run_constant_rudder(model, -delta35, 300.0, 0.5)
tr_p = run_constant_rudder(model, delta35, 300.0, 0.5)
sym = max(abs(a + b) / max(abs(b), 1e-12) for a, b in zip(tr_m.psi[-20:], tr_p.psi[-20:]))
gate("port/starboard symmetry |psi(-d)+psi(+d)|/psi", sym, 0.0, sym, 1e-9, "sign convention")

# geometry gate behaviour
geo_rejected = False
try:
    HullParameters(length=320.0, beam=58.0, draft=20.8, cb=0.8098,
                   design_speed=7.0, displacement_t=150000.0)
except ValueError:
    geo_rejected = True
gates.append({"gate": "geometry gate rejects inconsistent displacement (3%)",
              "value": 1.0 if geo_rejected else 0.0, "reference": 1.0,
              "rel_err": 0.0 if geo_rejected else 1.0, "limit": 0.0,
              "status": "PASS" if geo_rejected else "FAIL",
              "note": "rho*Cb*L*B*T vs stated displacement"})

# ---------------------------------------------------------------------------
# 2. standard manoeuvres + IMO criteria
# ---------------------------------------------------------------------------
_, ov10 = zigzag(model, rudder_deg=10.0, heading_deg=10.0, dt=0.5, max_time=2400.0)
z20_tr, ov20 = zigzag(model, rudder_deg=20.0, heading_deg=20.0, dt=0.5, max_time=2400.0)
imo = evaluate_imo(L, turning=ind, zigzag_10=ov10, zigzag_20=ov20)

# ---------------------------------------------------------------------------
# 3. sensitivity of the declared modelling constants (Newton steady state only)
# ---------------------------------------------------------------------------
sens = []
for k_sway in (1.0, 2.3):
    for munk in (0.0, 1.0):
        for cd in (0.5, 1.0, 1.3):
            p = ModelParameters(k_added_sway=k_sway, munk_factor=munk, cd_cross=cd)
            mm = MMGModel(hull, p)
            try:
                uu, vv, rr = steady_state(mm, delta35)
                r_over_l = uu / rr / L if rr > 1e-12 else float("inf")
                dt_over_l = 2.0 * r_over_l
            except ArithmeticError:
                r_over_l = float("nan")
                dt_over_l = float("nan")
            sens.append({"k_added_sway": k_sway, "munk_factor": munk, "cd_cross": cd,
                         "u_steady": uu, "r_steady": rr,
                         "steady_radius_over_L": r_over_l,
                         "steady_diameter_over_L": dt_over_l,
                         "imo_5L": ("PASS" if dt_over_l <= 5.0 else "FAIL")
                         if r_over_l == r_over_l else "N/A"})

valid = [s["steady_diameter_over_L"] for s in sens if s["steady_diameter_over_L"] == s["steady_diameter_over_L"]]
band = {"min_over_L": min(valid), "max_over_L": max(valid), "n_configs": len(valid)}

# ---------------------------------------------------------------------------
# 4. figures
# ---------------------------------------------------------------------------
fig, ax = plt.subplots(figsize=(6.4, 6.0))
ax.plot(trace.x, trace.y, lw=1.8, label="MMG cross-flow baseline (35° rudder)")
mark = [(x, y) for psi, x, y in zip(trace.psi, trace.x, trace.y) if abs(psi - math.pi / 2) < 0.01]
if mark:
    ax.plot([mark[0][0]], [mark[0][1]], "o", ms=6, color="tab:red", label="heading = 90°")
ax.set_aspect("equal")
ax.set_xlabel("x (m)")
ax.set_ylabel("y (m)")
ax.set_title(f"Turning circle, L={L:.0f} m: advance {ind['advance_over_L']:.2f} L, "
             f"D_T {ind['tactical_diameter_over_L']:.2f} L\n(IMO limits 4.5 L / 5 L)")
ax.grid(alpha=0.3)
ax.legend(loc="lower right", fontsize=8)
fig.tight_layout()
fig.savefig(OUT / "fig_turning_circle.png", dpi=140)

fig, ax = plt.subplots(2, 1, figsize=(7.0, 5.6), sharex=True)
ax[0].plot(z20_tr.t, [math.degrees(p) for p in z20_tr.psi], lw=1.5)
ax[0].axhline(20, ls=":", color="k", lw=0.8)
ax[0].axhline(-20, ls=":", color="k", lw=0.8)
ax[0].set_ylabel("heading ψ (deg)")
ax[0].set_title(f"20°/20° zigzag — overshoots {', '.join(f'{o:.1f}°' for o in ov20)}")
ax[0].grid(alpha=0.3)
ax[1].plot(z20_tr.t, [math.degrees(d) for d in z20_tr.delta], lw=1.5, color="tab:orange")
ax[1].set_xlabel("time (s)")
ax[1].set_ylabel("rudder δ (deg)")
ax[1].grid(alpha=0.3)
fig.tight_layout()
fig.savefig(OUT / "fig_zigzag.png", dpi=140)

fig, ax = plt.subplots(figsize=(6.6, 4.2))
t_old = 0.0
tt = [t for t in trace_small.t]
sim = [r for r in trace_small.r]
ana = [nomoto_response(K, T, delta_small, t)[0] for t in tt]
ax.plot(tt, sim, lw=1.8, label="MMG simulation (δ = 0.5°)")
ax.plot(tt, ana, "--", lw=1.4, label=f"Nomoto analytic Kδ(1−e^(−t/T)), K={K:.4g} 1/s, T={T:.1f} s")
ax.set_xlabel("time (s)")
ax.set_ylabel("yaw rate r (rad/s)")
ax.set_title(f"Linearisation cross-check: max deviation {100 * worst:.2f}% (t > 0.3T)")
ax.grid(alpha=0.3)
ax.legend(fontsize=8)
fig.tight_layout()
fig.savefig(OUT / "fig_nomoto_check.png", dpi=140)

fig, ax = plt.subplots(figsize=(6.8, 4.2))
labels = [f"k={s['k_added_sway']:g},munk={s['munk_factor']:g},Cd={s['cd_cross']:g}" for s in sens]
vals = [s["steady_diameter_over_L"] for s in sens]
ax.barh(range(len(vals)), vals, color="tab:blue", alpha=0.75)
ax.set_yticks(range(len(vals)))
ax.set_yticklabels(labels, fontsize=6.5)
ax.axvline(IMO_LIMITS["tactical_diameter_over_L"], color="tab:red", ls="--",
           label="IMO tactical diameter limit 5 L")
ax.set_xlabel("steady turning diameter / L")
ax.set_title(f"Uncertainty band of the cross-flow force model: {band['min_over_L']:.1f}–{band['max_over_L']:.1f} L")
ax.legend(fontsize=8)
ax.grid(alpha=0.3, axis="x")
fig.tight_layout()
fig.savefig(OUT / "fig_uncertainty_band.png", dpi=140)

# ---------------------------------------------------------------------------
# 5. results.json
# ---------------------------------------------------------------------------
results = {
    "module": "backend/methods/manoeuvring (MMG 3-DOF)",
    "scenario": {
        "hull": "KVLCC2 principal particulars (public)",
        "L": L, "B": hull.beam, "T": hull.draft, "Cb": hull.cb,
        "U0_ms": hull.design_speed, "Froude": round(hull.froude, 4),
        "displacement_t": hull.displacement_t,
        "displacement_from_geometry_t": round(hull.mass / 1000.0, 1),
        "rudder_deg": 35.0, "dt_s": 0.5, "march_s": 4000.0,
        "rho": hull.water_density,
    },
    "mass_properties": {
        "m_kg": model.m, "m_x_kg": model.m_x, "m_y_kg": model.m_y,
        "m_y_over_m": model.m_y / model.m, "I_zz_kgm2": model.I_zz,
        "J_zz_kgm2": model.J_zz, "A_R_m2": model.A_R, "R_T0_N": model.r_t0,
    },
    "gates": gates,
    "nomoto": {k: idx[k] for k in ("K", "T", "det_A", "trace_A", "eig_re_max", "eig_oscillatory")},
    "turning_circle": ind,
    "zigzag_10_10_overshoots_deg": ov10,
    "zigzag_20_20_overshoots_deg": ov20,
    "imo": imo,
    "sensitivity": sens,
    "uncertainty_band": band,
    "accuracy_grade": "WITHHELD",
}
(OUT / "results.json").write_text(json.dumps(results, indent=2))

print(json.dumps({
    "gates": [(g["gate"], round(g["rel_err"], 6), g["status"]) for g in gates],
    "turning": {k: round(v, 4) if isinstance(v, float) else v for k, v in ind.items()},
    "zigzag": {"10/10": [round(o, 2) for o in ov10], "20/20": [round(o, 2) for o in ov20]},
    "imo": {c["criterion"]: (round(c["value"], 3) if isinstance(c["value"], float) else c["value"],
                             c["status"]) for c in imo["criteria"]},
    "nomoto": {"K": round(K, 5), "T": round(T, 2), "worst_transient_pct": round(100 * worst, 2)},
    "band": band,
}, indent=1))
print("artifacts written to", OUT)