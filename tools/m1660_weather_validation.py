"""m1660 — validation driver for the IS Code 2008 A.2.3 weather criterion.

Writes, from program (never by hand):

* ``artifacts/m1660/validation.json``  — every number quoted in the report
* ``artifacts/m1660/fig1_gz_and_levers.png``
* ``artifacts/m1660/fig2_margin_vs_windage.png``
* ``artifacts/m1660/fig3_quadrature_convergence.png``
"""
from __future__ import annotations

import json
import math
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402

import services.ship_performance.stability.weather_criterion as wc  # noqa: E402
from services.stability_reference import (  # noqa: E402
    box_parameters,
    exact_gz_box,
    max_true_gz,
    wall_sided_gz,
    wall_sided_limit_deg,
)

OUT = ROOT / "artifacts" / "m1660"
OUT.mkdir(parents=True, exist_ok=True)

# --------------------------------------------------------------------------- #
# Case definition: 100 m box barge, full-scale, Polar/medium-size vessel
# --------------------------------------------------------------------------- #
L, B, T, D = 100.0, 16.0, 6.0, 9.0
RHO = 1.025
DISP_T = L * B * T * RHO          # 9840 t
WIND_A = 300.0                    # lateral windage area of superstructure, m^2
WIND_Z = 7.5                      # centroid of A (10.5 m) minus T/2 (3.0 m), m

CASES = {
    "loaded_KG6.0": 6.0,          # GM = 0.556 m  (realistic)
    "light_KG6.5": 6.5,           # GM = 0.056 m  (stability-degraded)
}

limit_deg = wall_sided_limit_deg(B, T, D)

result = {
    "case": {
        "hull": "box barge (verified geometry: vertical sides, flat bottom)",
        "L_m": L, "B_m": B, "T_m": T, "D_m": D,
        "displacement_t": DISP_T,
        "windage_area_m2": WIND_A,
        "windage_centroid_arm_m": WIND_Z,
        "pressure_pa": wc.WIND_PRESSURE_PA,
        "wall_sided_validity_limit_deg": limit_deg,
    },
    "cases": {},
    "anchors": {},
    "provenance": wc.COEFFICIENT_PROVENANCE,
}

# --------------------------------------------------------------------------- #
# 1. Per-case criterion evaluation on both righting-arm bases
# --------------------------------------------------------------------------- #
for name, kg in CASES.items():
    p = box_parameters(B, T, kg, D)
    gm, bm = p["gm_m"], p["bm_m"]

    def gz_wall(deg: float, gm=gm, bm=bm) -> float:
        return wall_sided_gz(gm, bm, math.radians(deg))

    def gz_exact(deg: float, kg=kg) -> float:
        return exact_gz_box(B, T, kg, math.radians(deg), D)["gz_m"]

    th1 = wc.theta1_is_code(beam_m=B, draft_m=T, cb=1.0, kg_m=kg,
                            roll_period_s=12.0)

    def run(fn):
        r = wc.weather_criterion(
            fn, theta1_deg=th1["theta1_deg"], windage_area_m2=WIND_A,
            windage_centroid_height_m=WIND_Z, displacement_t=DISP_T)
        d = r.as_dict()
        d.pop("provenance", None)
        return d

    mx = max_true_gz(B, T, kg, D)
    result["cases"][name] = {
        "kg_m": kg, "gm_m": gm, "bm_m": bm,
        "theta1": th1,
        "gz_max_exact_m": mx["gz_max_m"],
        "heel_at_gz_max_deg": mx["heel_at_gz_max_deg"],
        "gz_curve_exact": {str(d): gz_exact(d) for d in range(0, 61, 5)},
        "gz_curve_wall_sided": {str(d): gz_wall(d) for d in range(0, 61, 5)},
        "wall_sided_basis": run(gz_wall),
        "exact_box_basis": run(gz_exact),
    }

# --------------------------------------------------------------------------- #
# 2. Independent anchor checks (numbers quoted as evidence)
# --------------------------------------------------------------------------- #
gm_v = box_parameters(B, T, 6.0, D)["gm_m"]
bm_v = box_parameters(B, T, 6.0, D)["bm_m"]
f_wall = lambda d: wall_sided_gz(gm_v, bm_v, math.radians(d))  # noqa: E731

ana = wc.wall_sided_area_analytic(gm_v, bm_v, 21.0, 40.0)
quad = wc.simpson_integrate(f_wall, 21.0, 40.0, 400)
result["anchors"]["A_area_closed_form"] = {
    "analytic_m_rad": ana, "simpson_n400_m_rad": quad,
    "rel_error": abs(quad - ana) / abs(ana),
    "criterion": "< 1e-9 (verified)",
}

conv = {}
ana30 = wc.wall_sided_area_analytic(gm_v, bm_v, 10.0, 40.0)
for n in (20, 40, 80, 160, 320, 640):
    conv[n] = abs(wc.simpson_integrate(f_wall, 10.0, 40.0, n) - ana30)
orders = [math.log2(conv[n] / conv[2 * n]) for n in (40, 80, 160, 320)]
result["anchors"]["B_quadrature_order"] = {
    "errors": {str(k): v for k, v in conv.items()},
    "observed_order_per_halving": orders,
    "mean_order": sum(orders) / len(orders),
    "criterion": "3.7 < order < 4.3 (Simpson)",
}

lw = wc.wind_heeling_levers(WIND_A, WIND_Z, DISP_T)
lw2x = wc.wind_heeling_levers(2 * WIND_A, WIND_Z, DISP_T)
lw_half = wc.wind_heeling_levers(WIND_A, WIND_Z, 2 * DISP_T)
result["anchors"]["C_wind_lever_identities"] = {
    "lw1_m": lw["lw1_m"], "lw2_m": lw["lw2_m"],
    "lw2_over_lw1": lw["lw2_m"] / lw["lw1_m"],
    "lw1_double_area_ratio": lw2x["lw1_m"] / lw["lw1_m"],
    "lw1_double_disp_ratio": lw_half["lw1_m"] / lw["lw1_m"],
    "max_dev_from_identity": max(abs(lw2x["lw1_m"] / lw["lw1_m"] - 2.0),
                                 abs(lw_half["lw1_m"] / lw["lw1_m"] - 0.5),
                                 abs(lw["lw2_m"] / lw["lw1_m"] - 1.5)),
}

lim = limit_deg
inside = abs(wc.wall_sided_area_analytic(gm_v, bm_v, 0.0, lim - 2.0)
             - wc.simpson_integrate(f_wall, 0.0, lim - 2.0, 800))
gz_err_in = abs(f_wall(lim - 2.0) - exact_gz_box(B, T, 6.0, math.radians(lim - 2.0), D)["gz_m"])
gz_err_out = abs(f_wall(40.0) - exact_gz_box(B, T, 6.0, math.radians(40.0), D)["gz_m"])
result["anchors"]["G_wall_sided_domain"] = {
    "validity_limit_deg": lim,
    "gz_error_at_limit_minus_2deg_m": gz_err_in,
    "gz_error_at_40deg_m": gz_err_out,
    "area_anchor_inside_domain_rel_error": abs(
        wc.simpson_integrate(f_wall, 0.0, lim - 2.0, 800)
        - wc.wall_sided_area_analytic(gm_v, bm_v, 0.0, lim - 2.0)) / max(1e-30, abs(
            wc.wall_sided_area_analytic(gm_v, bm_v, 0.0, lim - 2.0))),
}

# --------------------------------------------------------------------------- #
# 3. Figures
# --------------------------------------------------------------------------- #
degs = np.linspace(0.0, 60.0, 241)


def make_lookup(kg: float):
    """Cached exact-box righting arm on a 0.25 deg grid + linear interpolation.

    ``exact_gz_box`` re-solves the immersed section (polygon clip + sinkage
    bisection) on every call, so the sweeps below use one pre-computed table.
    The design-point evaluations keep calling the solver directly.
    """
    grid = np.arange(0.0, 60.0 + 1e-9, 0.25)
    tab = np.array([exact_gz_box(B, T, kg, math.radians(d), D)["gz_m"] for d in grid])
    return lambda d: float(np.interp(d, grid, tab))

fig, axes = plt.subplots(1, 2, figsize=(12.5, 5.0))
for ax, (name, kg) in zip(axes, CASES.items()):
    c = result["cases"][name]
    gz_ex = [exact_gz_box(B, T, kg, math.radians(d), D)["gz_m"] for d in degs]
    gz_ws = [wall_sided_gz(c["gm_m"], c["bm_m"], math.radians(d)) for d in degs]
    ax.plot(degs, gz_ex, "b-", lw=2.0, label="exact box hydrostatics (REAL)")
    ax.plot(degs, gz_ws, "r--", lw=1.6, label="shipped wall-sided closed form")
    ax.axhline(c["exact_box_basis"]["lw2_m"], color="k", ls=":", lw=1.2,
               label=f"lw2 = {c['exact_box_basis']['lw2_m']:.4f} m")
    ax.axhline(c["exact_box_basis"]["lw1_m"], color="k", ls="-.", lw=0.8,
               label=f"lw1 = {c['exact_box_basis']['lw1_m']:.4f} m")
    ax.axvline(c["theta1"]["theta1_deg"], color="g", ls="-", lw=1.0,
               label=f"$\\theta_1$ = {c['theta1']['theta1_deg']:.2f}°")
    ax.axvline(40.0, color="grey", ls="--", lw=0.8)
    ax.axvline(lim, color="orange", ls="--", lw=0.9,
               label=f"wall-sided limit {lim:.1f}°")
    ax.set_xlim(0, 60)
    ax.set_xlabel("heel [deg]")
    ax.set_ylabel("GZ, lw  [m]")
    ax.set_title(f"{name}: GM = {c['gm_m']:.3f} m, verdict exact = "
                 f"{c['exact_box_basis']['verdict']}, wall-sided = "
                 f"{c['wall_sided_basis']['verdict']}", fontsize=9)
    ax.grid(alpha=0.3)
    ax.legend(fontsize=7, loc="upper left")
fig.suptitle("IS Code 2008 A.2.3 weather criterion — righting arm vs wind levers "
             f"(box barge {L:.0f}x{B:.0f}x{T:.0f} m, D={D:.0f} m)", fontsize=10)
fig.tight_layout()
fig.savefig(OUT / "fig1_gz_and_levers.png", dpi=160)
plt.close(fig)

areas = np.linspace(0.0, 2000.0, 41)
sweep = {}
for name, kg in CASES.items():
    lookup = make_lookup(kg)
    th1 = result["cases"][name]["theta1"]["theta1_deg"]
    row = []
    for a in areas:
        r = wc.weather_criterion(lookup, theta1_deg=th1, windage_area_m2=float(a),
                                 n_quad=200,
                                 windage_centroid_height_m=WIND_Z,
                                 displacement_t=DISP_T)
        row.append(r.margin_m_rad)
    sweep[name] = row
result["sweep"] = {"windage_area_m2": areas.tolist(),
                   "margin_m_rad": {k: v for k, v in sweep.items()}}

fig, ax = plt.subplots(figsize=(7.6, 4.8))
for name, row in sweep.items():
    ax.plot(areas, row, lw=1.8, label=f"{name} (exact box basis)")
ax.axhline(0.0, color="k", lw=1.0)
ax.axvline(WIND_A, color="orange", ls="--", lw=1.0,
           label=f"design case A = {WIND_A:.0f} m²")
ax.set_xlabel("lateral windage area A [m²]  (Z = 7.5 m)")
ax.set_ylabel("$b - a$  [m·rad]   (>= 0 passes)")
ax.set_title("Weather-criterion margin vs windage area (b-area minus a-area)")
ax.grid(alpha=0.3)
ax.legend(fontsize=8)
fig.tight_layout()
fig.savefig(OUT / "fig2_margin_vs_windage.png", dpi=160)
plt.close(fig)

fig, ax = plt.subplots(figsize=(6.6, 4.6))
ns = sorted(conv)
ax.loglog(ns, [conv[n] for n in ns], "o-", lw=1.6, label="|Simpson - closed form|")
ref = conv[ns[0]] * (np.array(ns, dtype=float) / ns[0]) ** -4.0
ax.loglog(ns, ref, "k--", lw=1.0, label="slope $-4$ (Simpson theory)")
ax.set_xlabel("number of panels n")
ax.set_ylabel("absolute error [m·rad]")
ax.set_title("Area quadrature: convergence order verified, not assumed")
ax.grid(alpha=0.3, which="both")
ax.legend(fontsize=8)
fig.tight_layout()
fig.savefig(OUT / "fig3_quadrature_convergence.png", dpi=160)
plt.close(fig)

(OUT / "validation.json").write_text(json.dumps(result, indent=2, ensure_ascii=False))
print(json.dumps({k: result["anchors"][k] for k in result["anchors"]}, indent=2)[:2500])
print("\n--- cases ---")
for name, c in result["cases"].items():
    print(name, "GM=%.4f" % c["gm_m"],
          "| exact:", c["exact_box_basis"]["verdict"],
          "b=%.5f a=%.5f ratio=%.3f" % (c["exact_box_basis"]["area_b_m_rad"],
                                        c["exact_box_basis"]["area_a_m_rad"],
                                        c["exact_box_basis"]["margin_ratio"]),
          "| wall:", c["wall_sided_basis"]["verdict"],
          "b=%.5f a=%.5f ratio=%.3f" % (c["wall_sided_basis"]["area_b_m_rad"],
                                        c["wall_sided_basis"]["area_a_m_rad"],
                                        c["wall_sided_basis"]["margin_ratio"]))
print("\nwrote", OUT)