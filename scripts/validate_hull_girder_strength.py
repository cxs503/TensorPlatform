"""Generate the hull-girder validation artifacts (JSON + figures).

All numbers in the report are read back from the JSON written here — nothing is
transcribed by hand.  Run:  python scripts/validate_hull_girder_strength.py
"""
from __future__ import annotations

import json
import math
import os
import sys

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "backend"))

from services.hull_girder_strength import (  # noqa: E402
    G,
    box_girder_section,
    double_hull_section,
    hull_girder_assessment,
    minimum_section_modulus,
    parabolic_weight_per_m,
    still_water_shear_bending,
    uniform_buoyancy_from_displacement,
    wave_bending_moment_envelope,
)

OUT = os.path.join(ROOT, "artifacts", "hull_girder")
os.makedirs(OUT, exist_ok=True)

CASES = {
    "coaster_120m_box_20mm": dict(
        L=120.0, B=19.0, D=9.0, cb=0.75, disp_t=8000.0,
        scantlings=dict(t_deck=0.020, t_bottom=0.020, t_side=0.020),
        kind="box",
    ),
    "coaster_120m_box_8mm": dict(
        L=120.0, B=19.0, D=9.0, cb=0.75, disp_t=8000.0,
        scantlings=dict(t_deck=0.008, t_bottom=0.008, t_side=0.008),
        kind="box",
    ),
    "bulker_200m_double_hull": dict(
        L=200.0, B=32.0, D=18.0, cb=0.82, disp_t=60000.0,
        scantlings=dict(t_deck=0.024, t_bottom=0.026, t_side=0.019),
        kind="double",
    ),
}

results = {}
for name, c in CASES.items():
    if c["kind"] == "box":
        s = c["scantlings"]
        section = box_girder_section(c["B"], c["D"], s["t_deck"], s["t_bottom"], s["t_side"])
    else:
        section = double_hull_section(
            c["B"], c["D"], c["scantlings"]["t_deck"], c["scantlings"]["t_bottom"],
            c["scantlings"]["t_side"],
            double_bottom_height=1.8, t_inner_bottom=0.020,
            inner_side_offset=2.5, t_inner_side=0.015,
        )
    n = 1201
    res = hull_girder_assessment(
        length=c["L"], breadth=c["B"], depth=c["D"], cb=c["cb"],
        displacement_kg=c["disp_t"] * 1000.0, section=section,
        t_deck=c["scantlings"]["t_deck"], t_bottom=c["scantlings"]["t_bottom"],
        t_side=c["scantlings"]["t_side"], n_stations=n,
    )
    stations = [c["L"] * i / (n - 1) for i in range(n)]
    w = parabolic_weight_per_m(stations, c["disp_t"] * 1000.0)
    b = uniform_buoyancy_from_displacement(stations, c["disp_t"] * 1000.0, c["cb"])
    dist = still_water_shear_bending(stations, w, b)
    res["_curves"] = {"x": stations, "sf": dist.shear_n, "bm": dist.moment_nm}
    results[name] = res

# ---- analytic residuals (the machine-checkable criteria) --------------------
bench = results["bulker_200m_double_hull"]
L = 200.0
analytic = {
    "midship_moment_relative_error": abs(
        bench["still_water"]["midship_moment_nm"] - bench["still_water"]["midship_analytic_nm"]
    ) / bench["still_water"]["midship_analytic_nm"],
    "shear_closure_over_disp": abs(bench["_curves"]["sf"][-1]) / (60000e3 * G),
    "moment_closure_over_disp_length": abs(bench["_curves"]["bm"][-1]) / (60000e3 * G * L),
    "equilibrium_residual": abs(bench["still_water"]["equilibrium_residual"]),
}
summary = {
    "cases": {k: {kk: vv for kk, vv in v.items() if kk != "_curves"} for k, v in results.items()},
    "analytic_checks": analytic,
    "wave_moment_envelope_120m": wave_bending_moment_envelope(120.0, 19.0, 0.75),
    "w_min_120m": minimum_section_modulus(120.0, 19.0, 0.75),
    "w_min_200m": minimum_section_modulus(200.0, 32.0, 0.82),
}
with open(os.path.join(OUT, "validation_summary.json"), "w") as fh:
    json.dump(summary, fh, indent=2, ensure_ascii=False)

# ---- figure 1: SF / BM distributions vs the polynomial closed form ---------
fig, ax = plt.subplots(1, 2, figsize=(11, 4))
d = results["bulker_200m_double_hull"]["_curves"]
total = 60000e3 * G
bm_closed = [total * (x**2 / (2 * L) - x**3 / L**2 + x**4 / (2 * L**3)) for x in d["x"]]
ax[0].plot(d["x"], [v / 1e6 for v in d["sf"]], label="SF (platform module)")
ax[0].set_xlabel("x from AP [m]"); ax[0].set_ylabel("shear force [MN]")
ax[0].set_title("Still-water shear force, L=200 m"); ax[0].grid(alpha=.3); ax[0].legend()
ax[1].plot(d["x"], [v / 1e9 for v in d["bm"]], label="BM (platform module)")
ax[1].plot(d["x"], [v / 1e9 for v in bm_closed], "--", label="BM analytic Δ(x²/2L−x³/L²+x⁴/2L³)")
ax[1].axhline(0, color="k", lw=.6)
ax[1].set_xlabel("x from AP [m]"); ax[1].set_ylabel("bending moment [GN·m]")
ax[1].set_title(f"SWBM, midship vs ΔL/32 = {total*L/32/1e9:.3f} GN·m")
ax[1].grid(alpha=.3); ax[1].legend()
fig.tight_layout(); fig.savefig(os.path.join(OUT, "fig1_swbm_closure.png"), dpi=130); plt.close(fig)

# ---- figure 2: section properties and stress utilisation -------------------
fig, ax = plt.subplots(1, 2, figsize=(11, 4))
names = list(results)
w_act = [results[k]["modulus"]["w_actual_m3"] for k in names]
w_req = [results[k]["modulus"]["w_required_m3"] for k in names]
idx = range(len(names))
ax[0].bar([i - .2 for i in idx], w_act, .4, label="W_actual (min fibre)")
ax[0].bar([i + .2 for i in idx], w_req, .4, label="W_required (IACS UR S7)")
ax[0].set_xticks(list(idx)); ax[0].set_xticklabels([n.replace("_", "\n") for n in names], fontsize=7)
ax[0].set_ylabel("section modulus [m³]"); ax[0].legend(fontsize=8)
ax[0].set_title("Midship section modulus vs rule minimum")
util = [results[k]["stress"]["utilisation"] for k in names]
ax[1].bar(list(idx), util, .5, color=["tab:green" if u <= 1 else "tab:red" for u in util])
ax[1].axhline(1.0, color="k", ls="--", lw=1)
ax[1].set_xticks(list(idx)); ax[1].set_xticklabels([n.replace("_", "\n") for n in names], fontsize=7)
ax[1].set_ylabel("σ_total / (175/k) [-]"); ax[1].set_title("Stress utilisation (1.0 = limit)")
fig.tight_layout(); fig.savefig(os.path.join(OUT, "fig2_section_and_stress.png"), dpi=130); plt.close(fig)

# ---- figure 3: wave moment envelope and W_min vs length -------------------
ls = [x * 2.0 for x in range(45, 251)]  # 90 .. 500 m
mh = [wave_bending_moment_envelope(l, 32.0, 0.82)["m_wave_hogging_knm"] / 1e6 for l in ls]
ms = [abs(wave_bending_moment_envelope(l, 32.0, 0.82)["m_wave_sagging_knm"]) / 1e6 for l in ls]
wmin = [minimum_section_modulus(l, 32.0, 0.82) for l in ls]
fig, ax = plt.subplots(1, 2, figsize=(11, 4))
ax[0].plot(ls, mh, label="M_wh = 0.19·C·L²·B·Cb")
ax[0].plot(ls, ms, "--", label="|M_ws| = 0.11·C·L²·B·(Cb+0.7)")
ax[0].scatter([120, 200], [wave_bending_moment_envelope(120, 19, .75)["m_wave_hogging_knm"] / 1e6,
                           wave_bending_moment_envelope(200, 32, .82)["m_wave_hogging_knm"] / 1e6],
              color="k", zorder=5, label="case ships")
ax[0].set_xlabel("rule length L [m]"); ax[0].set_ylabel("wave bending moment [GN·m]")
ax[0].set_title("IACS UR S11 wave moment envelope (B=32 m, Cb=0.82)"); ax[0].legend(fontsize=8); ax[0].grid(alpha=.3)
ax[1].plot(ls, wmin, label="W_min = C·L²·B·(Cb+0.7)·1e-6")
ax[1].scatter([120, 200], [summary["w_min_120m"], summary["w_min_200m"]], color="k", zorder=5, label="case ships")
ax[1].set_xlabel("rule length L [m]"); ax[1].set_ylabel("W_min [m³]")
ax[1].set_title("IACS UR S7 minimum midship section modulus"); ax[1].legend(fontsize=8); ax[1].grid(alpha=.3)
fig.tight_layout(); fig.savefig(os.path.join(OUT, "fig3_rule_envelope.png"), dpi=130); plt.close(fig)

print(json.dumps({"analytic_checks": analytic,
                  "verdicts": {k: results[k]["verdict"] for k in results},
                  "sigma": {k: round(results[k]["stress"]["sigma_total_mpa"], 2) for k in results},
                  "W": {k: [round(results[k]["modulus"]["w_actual_m3"], 3),
                            round(results[k]["modulus"]["w_required_m3"], 3)] for k in results},
                  "wave200": results["bulker_200m_double_hull"]["wave"]}, indent=2))
print("figures:", sorted(os.listdir(OUT)))
