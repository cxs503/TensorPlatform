"""IS Code 2008 intact-stability criteria: anchor suite + divergence audit (m1642).

Run:  python3 tools/verification/stability_criteria_anchors.py
Writes artifacts/stability_criteria/{anchors.json, fig1..fig3.png}

All anchors are analytic / definitional (no literature look-up tables):

S-A1  the platform's exact-section GZ (``icing.stability.LoadingCondition.gz``,
      polygon clipping + equal-displacement sinkage) must reproduce the
      wall-sided closed form  GZ = GM sin(phi) + 0.5 BM tan^2(phi) sin(phi)
      below deck-edge immersion -- an independent-algorithm cross-check of the
      producer that feeds every criteria verdict.
S-A2  dGZ/dphi at phi=0 must equal the reported GM0 (definition of the
      metacentric height, kinematic anchor on KG/KB/BM bookkeeping).
S-A3  the shared integrator ``stability_criteria.area_under_gz`` vs the closed
      form antiderivative  A(phi) = GM(1-cos phi) + 0.5 BM (sec phi + cos phi - 2)
      plus a step-halving (Richardson) test: error must fall as O(h^2).
S-A4  unit guard: the integrator must return m*rad, not m*deg (ratio pi/180).
S-A5  false-PASS regression: a curve that passes the legacy 5-item icing set but
      violates A(0-40) >= 0.09 m rad (the clause that implementation omitted).
S-A6  false-FAIL regression: a curve that satisfies all six IS Code clauses with
      GZ_max = 0.235 m, rejected by the legacy ``GZ_max >= 0.25 m`` gate.
S-A7  area additivity A(0-40) = A(0-30) + A(30-40) and grid independence.
"""
from __future__ import annotations

import json
import math
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

import numpy as np  # noqa: E402

from services.stability_criteria import (  # noqa: E402
    AREA_0_30_MIN, AREA_0_40_MIN, AREA_30_40_MIN, GZ_AT_30_MIN, GM0_MIN,
    area_under_gz, evaluate_is_code_2008, gz_at, normalise_curve,
)
from services.ship_performance import icing as ice  # noqa: E402

OUT = ROOT / "artifacts" / "stability_criteria"
OUT.mkdir(parents=True, exist_ok=True)


def wall_sided(gm: float, bm: float, phi_deg: float) -> float:
    phi = math.radians(phi_deg)
    return gm * math.sin(phi) + 0.5 * bm * math.tan(phi) ** 2 * math.sin(phi)


def wall_sided_area(gm: float, bm: float, phi_deg: float) -> float:
    """Closed-form antiderivative of the wall-sided GZ, in m*rad."""
    phi = math.radians(phi_deg)
    return gm * (1.0 - math.cos(phi)) + 0.5 * bm * (1.0 / math.cos(phi) + math.cos(phi) - 2.0)


def legacy_icing_verdict(crit: dict) -> bool:
    """The 5-item set that ``icing`` shipped before this audit (A(0-40) absent)."""
    keys = ("gm_initial", "area_0_30", "gz_at_30", "heel_at_max_gz", "area_30_40")
    return all(crit[k]["pass"] for k in keys)


def legacy_pipeline_verdict(check: dict) -> bool:
    """The 6-item set that ``ice_integration.imo_criteria`` shipped (GZmax>=0.25)."""
    cond = [
        check["area_0_30_m_rad"] >= AREA_0_30_MIN,
        check["area_0_40_m_rad"] >= AREA_0_40_MIN,
        check["area_30_40_m_rad"] >= AREA_30_40_MIN,
        check["gz_at_30_m"] >= GZ_AT_30_MIN,
        check["gm0_m"] >= GM0_MIN,
        check["gz_max_m"] >= 0.250,            # <- not in IS Code 2008 A.2.2
    ]
    return all(cond)


def main() -> dict:
    res: dict = {"anchors": {}, "figures": []}

    # ---------------- S-A1 / S-A2 : producer vs wall-sided closed form -------
    vessel = ice.preset_polar_fishing_vessel()
    cond = ice.LoadingCondition(hull=vessel.hull, kg_light=vessel.kg_light)
    gm, bm = cond.gm_loaded, vessel.hull.bm_at_draft(cond.draft_loaded)
    limit = math.degrees(math.atan(2.0 * min(cond.draft_loaded,
                                             vessel.hull.depth - cond.draft_loaded)
                                    / vessel.hull.beam))
    heels = [2.0 * k for k in range(0, 41)]          # 0..80 deg
    gz_platform = [cond.gz(h) for h in heels]
    gz_closed = [wall_sided(gm, bm, h) for h in heels]
    inside = [i for i, h in enumerate(heels) if h < limit - 1e-9]
    err_in = max(abs(gz_platform[i] - gz_closed[i]) / max(abs(gz_closed[i]), 1e-12)
                 for i in inside)
    err_out = max(abs(gz_platform[i] - gz_closed[i]) / max(abs(gz_closed[i]), 1e-12)
                  for i in range(len(heels)) if i not in inside)
    h = 1e-4                                          # deg
    slope = (cond.gz(h) - cond.gz(-h)) / (2.0 * math.radians(h))
    res["anchors"]["S-A1_wall_sided_equivalence"] = {
        "validity_limit_deg": limit, "max_rel_error_inside": err_in,
        "max_rel_error_outside": err_out, "pass": err_in < 1e-6,
        "criterion": "rel err < 1e-6 below deck-edge immersion",
    }
    res["anchors"]["S-A2_dgz_dphi_at_zero_equals_gm0"] = {
        "finite_difference": slope, "gm_loaded": gm,
        "rel_error": abs(slope - gm) / gm, "pass": abs(slope - gm) / gm < 1e-6,
        "criterion": "dGZ/dphi(0) = GM0, rel err < 1e-6",
    }

    # ---------------- S-A3 / S-A4 : integrator vs closed-form area ----------
    ref_angles = [10.0, 20.0, 30.0, 40.0]
    rows, conv = [], []
    for step in (2.0, 1.0, 0.5, 0.25):
        pts = [(k * step, wall_sided(gm, bm, k * step))
               for k in range(int(round(90.0 / step)) + 1)]
        worst = 0.0
        for a in ref_angles:
            exact = wall_sided_area(gm, bm, a)
            got = area_under_gz(pts, 0.0, a)
            worst = max(worst, abs(got - exact) / abs(exact))
        conv.append({"step_deg": step, "max_rel_error": worst})
        rows.append({"step_deg": step, "max_rel_error_0_40":
                     abs(area_under_gz(pts, 0.0, 40.0) - wall_sided_area(gm, bm, 40.0))
                     / wall_sided_area(gm, bm, 40.0)})
    orders = [math.log2(conv[i]["max_rel_error"] / conv[i + 1]["max_rel_error"])
              for i in range(len(conv) - 1)]
    exact_40 = wall_sided_area(gm, bm, 40.0)
    pts05 = [(k * 0.5, wall_sided(gm, bm, k * 0.5)) for k in range(181)]
    got_40 = area_under_gz(pts05, 0.0, 40.0)
    deg_ratio = got_40 / exact_40
    res["anchors"]["S-A3_trapezoid_vs_antiderivative"] = {
        "closed_form_A_0_40": exact_40, "trapezoid_0.5deg_A_0_40": got_40,
        "rel_error": abs(got_40 - exact_40) / exact_40,
        "convergence": rows, "observed_orders": orders,
        "pass": abs(got_40 - exact_40) / exact_40 < 1e-4
                and all(o > 1.7 for o in orders),
        "criterion": "rel err < 1e-4 at 0.5 deg and observed order > 1.7",
    }
    res["anchors"]["S-A4_unit_is_m_rad"] = {
        "ratio_impl_over_analytic": deg_ratio,
        "deg_hypothesis_ratio": math.pi / 180.0,
        "pass": abs(deg_ratio - 1.0) < 1e-4,
        "criterion": "m*rad implementation: ratio 1.0 (an m*deg one would read 0.01745)",
    }

    # ---------------- S-A5 : legacy icing false PASS ------------------------
    gm5 = 0.42
    curve5 = []
    for k in range(0, 181):                      # 0..90 deg, 0.5 deg step
        phi = k * 0.5
        if phi <= 30.0:
            gz = gm5 * math.sin(math.radians(phi))
        elif phi <= 40.0:
            gz = 0.21 + (0.16 - 0.21) * (phi - 30.0) / 10.0
        else:
            gz = 0.16 * (90.0 - phi) / 50.0
        curve5.append((phi, gz))
    new5 = evaluate_is_code_2008(curve5, gm5)
    old5_checks = {
        "gm_initial": {"pass": gm5 >= GM0_MIN},
        "area_0_30": {"pass": new5["criteria"]["area_0_30_m_rad"]["pass"]},
        "gz_at_30": {"pass": new5["criteria"]["gz_at_30_m"]["pass"]},
        "heel_at_max_gz": {"pass": new5["criteria"]["angle_of_max_gz_deg"]["pass"]},
        "area_30_40": {"pass": new5["criteria"]["area_30_40_m_rad"]["pass"]},
    }
    old5 = legacy_icing_verdict(old5_checks)
    res["anchors"]["S-A5_legacy_icing_false_pass"] = {
        "A_0_30": new5["criteria"]["area_0_30_m_rad"]["value"],
        "A_30_40": new5["criteria"]["area_30_40_m_rad"]["value"],
        "A_0_40": new5["criteria"]["area_0_40_m_rad"]["value"],
        "legacy_5_item_verdict": old5,
        "corrected_verdict": new5["all_pass"],
        "pass": old5 is True and new5["all_pass"] is False,
        "criterion": "legacy PASS where IS Code 2008 A.2.2.1 (0-40) fails",
    }

    # ---------------- S-A6 : legacy pipeline false FAIL ---------------------
    gm6, peak = 0.1683, 0.235
    curve6 = []
    for k in range(0, 181):
        phi = k * 0.5
        if phi <= 40.0:
            gz = peak * math.sin(math.radians(phi) * (math.pi / 2) / math.radians(40.0))
        else:
            gz = peak * (90.0 - phi) / 50.0 * 0.5
        curve6.append((phi, gz))
    new6 = evaluate_is_code_2008(curve6, gm6)
    flat6 = {k: v["value"] for k, v in new6["criteria"].items()}
    flat6["gz_max_m"] = new6["informational"]["gz_max_m"]
    old6 = legacy_pipeline_verdict(flat6)
    res["anchors"]["S-A6_legacy_pipeline_false_fail"] = {
        "values": flat6, "legacy_6_item_verdict": old6,
        "corrected_verdict": new6["all_pass"],
        "pass": old6 is False and new6["all_pass"] is True,
        "criterion": "all six IS Code clauses pass with GZ_max = 0.235 m < 0.25 m",
    }

    # ---------------- S-A7 : additivity + grid independence -----------------
    pts = normalise_curve(curve5)
    a30, a3040 = area_under_gz(pts, 0.0, 30.0), area_under_gz(pts, 30.0, 40.0)
    a40 = area_under_gz(pts, 0.0, 40.0)
    coarse = [(k * 2.5, gz_at(pts, k * 2.5)) for k in range(37)]
    a40c = area_under_gz(coarse, 0.0, 40.0)
    res["anchors"]["S-A7_additivity_grid"] = {
        "A_0_40": a40, "A_0_30_plus_30_40": a30 + a3040,
        "additivity_abs_error": abs(a40 - (a30 + a3040)),
        "grid_2p5deg_rel_dev": abs(a40c - a40) / a40,
        "pass": abs(a40 - (a30 + a3040)) < 1e-12 and abs(a40c - a40) / a40 < 5e-3,
        "criterion": "additivity < 1e-12, 2.5 deg vs 0.5 deg grid < 0.5 %",
    }

    # ---------------- live call-site agreement ------------------------------
    curve = ice.preset_polar_fishing_vessel()
    cond2 = ice.LoadingCondition(hull=curve.hull, kg_light=curve.kg_light)
    icing_crit = cond2.stability_criteria()
    from services.ice_integration import nodes_downstream as nd
    phis = list(np.arange(0.0, 60.1, 0.5))
    pipe = nd.imo_criteria({"phi_deg": phis,
                            "gz_numeric": [cond2.gz(p) for p in phis],
                            "GM0": cond2.gm_loaded}, cond2.gm_loaded)
    res["live_call_sites"] = {
        "icing_all_pass": icing_crit["all_pass"]["value"],
        "pipeline_passed": pipe["passed"],
        "agree": bool(icing_crit["all_pass"]["value"]) == bool(pipe["passed"]),
        "pipeline_min_margin": pipe["min_margin"],
        "pipeline_gating_keys": sorted(k for k, v in pipe["checks"].items()
                                       if v["required"] is not None),
    }

    # ---------------- figures ----------------------------------------------
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fig, ax = plt.subplots(figsize=(7.2, 4.4))
    ax.plot(heels, gz_platform, "o", ms=3.5, label="platform exact section integration")
    ax.plot(heels, gz_closed, "-", lw=1.0, label="wall-sided closed form")
    ax.axvline(limit, ls="--", c="grey", lw=0.8, label=f"deck-edge limit {limit:.1f} deg")
    ax.set_xlabel("heel [deg]"); ax.set_ylabel("GZ [m]")
    ax.set_title(f"S-A1: producer vs analytic arm (rel err {err_in:.1e} inside)")
    ax.legend(fontsize=7); ax.grid(alpha=0.3)
    fig.tight_layout(); fig.savefig(OUT / "fig1_gz_anchor.png", dpi=140); plt.close(fig)
    res["figures"].append("fig1_gz_anchor.png")

    fig, ax = plt.subplots(figsize=(6.4, 4.2))
    steps = [r["step_deg"] for r in rows]
    errs = [max(r["max_rel_error_0_40"], 1e-17) for r in rows]
    ax.loglog(steps, errs, "o-", label="trapezoid rel. error")
    ref = errs[-1] * (np.array(steps) / steps[-1]) ** 2
    ax.loglog(steps, ref, "--", label="O(h^2) reference")
    ax.invert_xaxis(); ax.set_xlabel("heel step [deg]"); ax.set_ylabel("rel. error vs antiderivative")
    ax.set_title(f"S-A3: integrator convergence (orders {['%.2f' % o for o in orders]})")
    ax.legend(fontsize=8); ax.grid(alpha=0.3, which="both")
    fig.tight_layout(); fig.savefig(OUT / "fig2_convergence.png", dpi=140); plt.close(fig)
    res["figures"].append("fig2_convergence.png")

    fig, ax = plt.subplots(figsize=(7.6, 4.6))
    for tag, crv, verdict, colour in (
            ("S-A5 false-PASS case", curve5, "legacy PASS / corrected FAIL", "C3"),
            ("S-A6 false-FAIL case", curve6, "legacy FAIL / corrected PASS", "C0")):
        ax.plot([p[0] for p in crv], [p[1] for p in crv], lw=1.4, c=colour,
                label=f"{tag}: {verdict}")
    ax.axhline(0.20, ls=":", c="k", lw=0.9)
    ax.text(62, 0.203, "A.2.2.3  GZ(30) >= 0.20 m", fontsize=7)
    ax.axhline(0.25, ls=":", c="r", lw=0.9)
    ax.text(62, 0.253, "legacy-only GZ_max >= 0.25 m", fontsize=7, color="r")
    ax.axvline(25.0, ls="--", c="grey", lw=0.7)
    ax.text(25.6, 0.05, "A.2.2.4  >= 25 deg", fontsize=7)
    ax.set_xlabel("heel [deg]"); ax.set_ylabel("GZ [m]")
    ax.set_title("S-A5/S-A6: the two legacy verdicts err in opposite directions")
    ax.legend(fontsize=7, loc="lower left"); ax.grid(alpha=0.3)
    fig.tight_layout(); fig.savefig(OUT / "fig3_verdict_regressions.png", dpi=140); plt.close(fig)
    res["figures"].append("fig3_verdict_regressions.png")

    res["all_pass"] = all(v["pass"] for v in res["anchors"].values()) and res["live_call_sites"]["agree"]
    (OUT / "anchors.json").write_text(json.dumps(res, indent=2, default=float))
    return res


if __name__ == "__main__":
    out = main()
    for k, v in out["anchors"].items():
        print(f"{k:42s} {'PASS' if v['pass'] else 'FAIL'}")
    print("live call sites agree:", out["live_call_sites"]["agree"])
    print("ALL PASS:", out["all_pass"])
    sys.exit(0 if out["all_pass"] else 1)