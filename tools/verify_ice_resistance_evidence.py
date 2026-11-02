#!/usr/bin/env python3
"""Independent evidence audit of the platform Lindqvist ice-resistance chain (m1690).

Question
--------
``backend/services/ice_resistance_uq.py`` exposes a "Lindqvist (1989)" level-ice
resistance that is calibrated per ship with ``calibration_factor =
R_trial_nominal / R_raw_nominal``. The deterministic API endpoint therefore
returns a value that equals the platform-recorded trial mean at nominal
conditions *by construction*. This script measures, without touching the
production defaults:

1. the **raw** (formula-only) residual against the trial mean, per ship;
2. the **calibrated** residual at nominal conditions (to prove circularity);
3. the per-ship calibration factors and their spread;
4. the component breakdown (crushing / bending / submersion / speed) and the
   share carried by constants that cannot be traced to the published
   formulation (``0.3 * L`` in the submersion term, ``0.1`` in the speed term);
5. the effective thickness exponent ``d lnR / d ln h`` -- the calibration
   factor is a scalar and can only move the curve vertically, never repair the
   scaling, so this quantifies what error leaks away from nominal conditions;
6. structural invariants (exact linearity in beam B, monotonicity in h, V, mu);
7. a **cross-implementation band**: the two independent decompositions in
   ``backend/physics/ship_env_maneuvering/loads.py`` (``riska`` and
   ``lindqvist_like``) evaluated at the same conditions.

Reference data are the platform-recorded full-scale trial means already in
``SHIP_TRIAL_DATA`` (MV_Arctic / Polarstern / Terry_Fox). They are *not*
re-verified against the papers offline -- see the report limitations.

Outputs
-------
* ``artifacts/m1690/ice_resistance_evidence.json``  -- all numbers
* ``artifacts/m1690/fig_*.png``                     -- report figures

Usage
-----
    python3 tools/verify_ice_resistance_evidence.py
"""

from __future__ import annotations

import json
import math
import os
import sys
from typing import Any, Dict, List

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
_BACKEND = os.path.join(_ROOT, "backend")
for _p in (_BACKEND, _ROOT):
    if _p not in sys.path:
        sys.path.insert(0, _p)

OUT_DIR = os.path.join(_ROOT, "artifacts", "m1690")

from services.ice_resistance_uq import (  # noqa: E402
    SHIP_TRIAL_DATA,
    _lindqvist_raw,
    lindqvist_ice_resistance,
    lindqvist_ice_resistance_uncalibrated,
    lindqvist_terms,
)

from physics.ship_env_maneuvering import data as D  # noqa: E402
from physics.ship_env_maneuvering import loads as L  # noqa: E402


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _vessel(ship) -> "D.Vessel":
    """Build a manoeuvring-package Vessel from a trial ShipIceParams."""
    depth = ship.draft_m * 1.2
    return D.Vessel(
        name=ship.name,
        L=ship.length_m,
        B=ship.beam_m,
        T=ship.draft_m,
        Cb=ship.cb,
        depth=depth,
        A_L=ship.beam_m * (depth - ship.draft_m) * 1.3,
        A_F=ship.beam_m * (depth - ship.draft_m) * 0.9,
        A_R=0.02 * ship.length_m * ship.draft_m,
        D_prop=0.6 * ship.draft_m,
        b_R=0.5 * ship.draft_m,
    )


def _ice_condition(h: float, sigma_c_MPa: float, mu: float, ship) -> "D.IceCondition":
    return D.IceCondition(
        thickness=h,
        sigma_flexural=sigma_c_MPa * 1e6 / 4.0,
        sigma_crushing=sigma_c_MPa * 1e6,
        mu=mu,
        stem_angle=ship.bow_angle_deg,
    )


def _rel(a: float, b: float) -> float:
    return abs(a - b) / abs(b) if b else float("nan")


# ---------------------------------------------------------------------------
# audit
# ---------------------------------------------------------------------------

def _lindqvist_total(terms: Dict[str, float]) -> float:
    """Reconstruct the raw total from the component dict (m1720 physics)."""
    return (
        (terms["crushing"] + terms["bending"]) * terms["velocity_multiplier"]
        + terms["submersion"] + terms["speed"]
    )


def audit_ship(name: str) -> Dict[str, Any]:
    trial = SHIP_TRIAL_DATA[name]
    ship = trial["params"]
    h0 = trial["nominal_ice_thickness_m"]
    s0 = trial["nominal_ice_strength_MPa"]
    mu0 = trial["nominal_friction_coeff"]
    v0 = trial["nominal_speed_ms"]
    R_trial = trial["mean_resistance_kN"]

    terms = lindqvist_terms(ship, h0, s0, mu0, v0)
    R_raw = lindqvist_ice_resistance_uncalibrated(ship, h0, s0, mu0, v0)
    R_cal = lindqvist_ice_resistance(ship, h0, s0, mu0, v0)
    factor = ship.calibration_factor

    # structural invariant: |recon(terms) - raw|
    terms_sum_err = abs(_lindqvist_total(terms) - R_raw) / R_raw

    # thickness exponent by central difference in log-log space
    def exponent(h: float, dlog: float = 1e-3) -> float:
        hp, hm = h * math.exp(dlog), h * math.exp(-dlog)
        rp = _lindqvist_raw(ship, hp, s0, mu0, v0)
        rm = _lindqvist_raw(ship, hm, s0, mu0, v0)
        return (math.log(rp) - math.log(rm)) / (math.log(hp) - math.log(hm))

    # component-wise exponents
    comp_exp = {}
    for key in terms:
        hp, hm = h0 * math.exp(1e-3), h0 * math.exp(-1e-3)
        tp = lindqvist_terms(ship, hp, s0, mu0, v0)[key]
        tm = lindqvist_terms(ship, hm, s0, mu0, v0)[key]
        comp_exp[key] = (math.log(tp) - math.log(tm)) / (math.log(hp) - math.log(hm))

    # beam scaling: an ice-resistant hull must be extensive in B (the ice
    # contact width along the waterline scales with beam). Measure the exponent
    # component by component, since the crushing term carries no B at all.
    import copy

    def _terms_at_beam(beam: float) -> Dict[str, float]:
        sh = copy.copy(ship)
        sh.beam_m = beam
        return lindqvist_terms(sh, h0, s0, mu0, v0)

    bp, bm = ship.beam_m * math.exp(1e-3), ship.beam_m * math.exp(-1e-3)
    tp, tm = _terms_at_beam(bp), _terms_at_beam(bm)
    beam_exp_comp = {
        k: (math.log(tp[k]) - math.log(tm[k])) / (math.log(bp) - math.log(bm))
        if min(tp[k], tm[k]) > 1e-12 else 0.0
        for k in terms
    }
    beam_exp_total = (
        math.log(_lindqvist_total(tp)) - math.log(_lindqvist_total(tm))
    ) / (math.log(bp) - math.log(bm))

    ship2 = copy.copy(ship)
    ship2.beam_m = ship.beam_m * 2.0
    R_double_B = _lindqvist_raw(ship2, h0, s0, mu0, v0)
    beam_doubling_rel_err = _rel(R_double_B, 2.0 * R_raw)

    # counterfactual: if the crushing term were made extensive by multiplying
    # by the beam (metres of contact width), how large would the total become?
    R_crush_if_extensive = R_raw - terms["crushing"] + terms["crushing"] * ship.beam_m

    # beam sweep for the figure
    beam_sweep = []
    for scale in [0.5, 0.75, 1.0, 1.5, 2.0]:
        sh = copy.copy(ship)
        sh.beam_m = ship.beam_m * scale
        beam_sweep.append({"B_scale": scale, "raw_kN": _lindqvist_raw(sh, h0, s0, mu0, v0)})

    # thickness sweep
    sweep = []
    for h in [0.2, 0.3, 0.4, 0.6, 0.8, 1.0, 1.2, 1.5, 2.0]:
        t = lindqvist_terms(ship, h, s0, mu0, v0)
        raw = _lindqvist_total(t)
        sweep.append({
            "h_m": h,
            "raw_kN": raw,
            "calibrated_kN": raw * factor,
            "terms_kN": t,
            "share_untraceable": (t["submersion"] + t["speed"]) / raw,
        })

    # monotonicity
    mono_v = all(
        _lindqvist_raw(ship, h0, s0, mu0, v) <= _lindqvist_raw(ship, h0, s0, mu0, v + 0.1) + 1e-9
        for v in [0.5, 1.0, 2.0, 3.0]
    )
    mono_mu = all(
        _lindqvist_raw(ship, h0, s0, mu, v0) <= _lindqvist_raw(ship, h0, s0, mu + 0.02, v0) + 1e-9
        for mu in [0.05, 0.10, 0.20]
    )
    mono_h = all(
        _lindqvist_raw(ship, h, s0, mu0, v0) <= _lindqvist_raw(ship, h + 0.05, s0, mu0, v0) + 1e-12
        for h in [0.2, 0.6, 1.0]
    )

    # cross-implementation band (independent decompositions)
    vessel = _vessel(ship)
    ice = _ice_condition(h0, s0, mu0, ship)
    r_riska = L.riska_ice_resistance(vessel, ice, v0).total
    r_indep = L.lindqvist_like_ice_resistance(vessel, ice, v0).total

    return {
        "ship": name,
        "nominal": {
            "ice_thickness_m": h0,
            "ice_strength_MPa": s0,
            "friction_coeff": mu0,
            "ship_speed_ms": v0,
            "trial_mean_kN": R_trial,
        },
        "raw_kN": R_raw,
        "calibrated_kN": R_cal,
        "calibration_factor": factor,
        "raw_residual_vs_trial": (R_raw - R_trial) / R_trial,
        "calibrated_residual_vs_trial": (R_cal - R_trial) / R_trial,
        "terms_kN": terms,
        "term_shares": {k: v / R_raw for k, v in terms.items()},
        "untraceable_share": (terms["submersion"] + terms["speed"]) / R_raw,
        "terms_sum_check_rel": terms_sum_err,
        "thickness_exponent": exponent(h0),
        "component_thickness_exponents": comp_exp,
        "beam_exponent_total": beam_exp_total,
        "beam_exponent_components": beam_exp_comp,
        "beam_doubling_rel_err_vs_exact": beam_doubling_rel_err,
        "beam_sweep": beam_sweep,
        "crushing_share": terms["crushing"] / R_raw,
        "counterfactual_crushing_per_metre_kN": R_crush_if_extensive,
        "counterfactual_ratio_vs_raw": R_crush_if_extensive / R_raw,
        "counterfactual_ratio_vs_trial": R_crush_if_extensive / R_trial,
        "monotone": {"speed": mono_v, "friction": mono_mu, "thickness": mono_h},
        "sweep": sweep,
        "cross_model_kN": {"riska": r_riska, "lindqvist_like": r_indep},
        "cross_model_vs_raw_rel": {
            "riska": (r_riska - R_raw) / R_raw,
            "lindqvist_like": (r_indep - R_raw) / R_raw,
        },
        "cross_model_band_rel": abs(r_riska - r_indep) / ((r_riska + r_indep) / 2.0),
    }


def main() -> int:
    os.makedirs(OUT_DIR, exist_ok=True)
    results = {name: audit_ship(name) for name in SHIP_TRIAL_DATA}

    factors = [r["calibration_factor"] for r in results.values()]
    summary = {
        "n_ships": len(results),
        "calibration_factor": {
            "min": min(factors),
            "max": max(factors),
            "spread_pct": 100.0 * (max(factors) - min(factors)) / min(factors),
        },
        "max_abs_raw_residual": max(abs(r["raw_residual_vs_trial"]) for r in results.values()),
        "max_abs_calibrated_residual": max(
            abs(r["calibrated_residual_vs_trial"]) for r in results.values()
        ),
        "circularity_violation_tolerance": 1e-9,
        "raw_error_pass_15pct": {
            r["ship"]: abs(r["raw_residual_vs_trial"]) < 0.15 for r in results.values()
        },
        "thickness_exponent_range": [
            min(r["thickness_exponent"] for r in results.values()),
            max(r["thickness_exponent"] for r in results.values()),
        ],
        "max_untraceable_share": max(r["untraceable_share"] for r in results.values()),
        "cross_model_band_range": [
            min(r["cross_model_band_rel"] for r in results.values()),
            max(r["cross_model_band_rel"] for r in results.values()),
        ],
        "beam_exponent_range": [
            min(r["beam_exponent_total"] for r in results.values()),
            max(r["beam_exponent_total"] for r in results.values()),
        ],
        "beam_doubling_rel_err_range": [
            min(r["beam_doubling_rel_err_vs_exact"] for r in results.values()),
            max(r["beam_doubling_rel_err_vs_exact"] for r in results.values()),
        ],
        "invariants": {
            "terms_sum_max_rel_err": max(
                r["terms_sum_check_rel"] for r in results.values()),
            "monotone_all": all(all(r["monotone"].values()) for r in results.values()),
        },
        "evidence_grade": (
            "REAL-conditional" if summary_pass(results) else "WITHHELD (nominal anchor only)"
        ),
    }

    payload = {
        "mission": 1690,
        "subject": "Lindqvist level-ice resistance chain (backend/services/ice_resistance_uq.py)",
        "summary": summary,
        "ships": results,
        "notes": [
            "calibration_factor = R_trial_nominal / R_raw_nominal is applied as a "
            "scalar; the nominal residual is zero by construction and is not a "
            "validation result.",
            "untraceable_share = (submersion + speed) / total, where the submersion "
            "term carries an engineering length scale 0.3*L and the speed term a "
            "factor 0.1; neither constant is sourced to the published formulation.",
            "beam_exponent_total measures d lnR / d lnB; an ice-resistant hull "
            "must be extensive in beam (exponent 1). The crushing component is "
            "declared without any contact-width factor and therefore has "
            "exponent 0, dragging the total below 1.",
            "counterfactual_crushing_per_metre_kN: total if the crushing term "
            "were made extensive in the same way as the bending term (x B). The "
            "result overshoots the trial mean by an order of magnitude, which is "
            "why no speculative physics fix is applied here -- the term structure "
            "cannot be repaired without the source formulation.",
            "cross-model band compares the platform Lindqvist implementation with "
            "the two independent decompositions in backend/physics/"
            "ship_env_maneuvering/loads.py at identical (h, sigma, mu, V).",
        ],
    }
    jpath = os.path.join(OUT_DIR, "ice_resistance_evidence.json")
    with open(jpath, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2, ensure_ascii=False)

    figs = make_figures(results, OUT_DIR)

    print("=== m1690 ice-resistance evidence audit ===")
    for name, r in results.items():
        print(
            f"{name:12s} trial={r['nominal']['trial_mean_kN']:8.1f} kN | "
            f"raw={r['raw_kN']:8.1f} ({100*r['raw_residual_vs_trial']:+6.2f}%) | "
            f"cal={r['calibrated_kN']:8.1f} ({r['calibrated_residual_vs_trial']:+.1e}) | "
            f"factor={r['calibration_factor']:.4f} | h-exp={r['thickness_exponent']:.3f} | "
            f"untraceable={100*r['untraceable_share']:.1f}% | "
            f"B-exp={r['beam_exponent_total']:.3f} | "
            f"xmodel-band={100*r['cross_model_band_rel']:.1f}%"
        )
    print(f"\nsummary: {json.dumps(summary, ensure_ascii=False, indent=2)}")
    print(f"\nartifacts: {jpath}")
    for f in figs:
        print(f"           {f}")
    return 0


def summary_pass(results: Dict[str, Any]) -> bool:
    """Nominal-anchor grade: all ships within 15 % raw and invariants exact."""
    return (
        all(abs(r["raw_residual_vs_trial"]) < 0.15 for r in results.values())
        and all(abs(r["beam_exponent_total"] - 1.0) < 0.01 for r in results.values())
        and all(r["terms_sum_check_rel"] < 1e-12 for r in results.values())
    )


# ---------------------------------------------------------------------------
# figures
# ---------------------------------------------------------------------------

def make_figures(results: Dict[str, Any], out_dir: str) -> List[str]:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    paths = []
    names = list(results)

    # --- Fig 1: R vs h, raw and trial-anchored, with the trial nominal point
    fig, ax = plt.subplots(figsize=(7.2, 4.6))
    colors = {"MV_Arctic": "C0", "Polarstern": "C1", "Terry_Fox": "C2"}
    for n in names:
        r = results[n]
        hs = [s["h_m"] for s in r["sweep"]]
        raw = [s["raw_kN"] for s in r["sweep"]]
        cal = [s["calibrated_kN"] for s in r["sweep"]]
        c = colors.get(n, None)
        ax.plot(hs, raw, "-", color=c, lw=1.8, label=f"{n} raw (formula)")
        ax.plot(hs, cal, "--", color=c, lw=1.3, label=f"{n} trial-anchored")
        nom = r["nominal"]
        ax.plot([nom["ice_thickness_m"]], [nom["trial_mean_kN"]], "o", color=c,
                ms=7, mfc="white", mew=1.8, zorder=5)
    ax.set_xlabel("level-ice thickness $h$ [m]")
    ax.set_ylabel("ice resistance [kN]")
    ax.set_title("Lindqvist chain: raw formula vs per-ship trial anchor\n"
                 "(open marker = trial mean at nominal conditions)")
    ax.grid(alpha=0.3)
    ax.legend(fontsize=7, ncol=2)
    p1 = os.path.join(out_dir, "fig1_resistance_vs_thickness.png")
    fig.tight_layout()
    fig.savefig(p1, dpi=150)
    plt.close(fig)
    paths.append(p1)

    # --- Fig 2: component shares at nominal conditions
    fig, ax = plt.subplots(figsize=(7.2, 4.0))
    keys = ["crushing", "bending", "submersion", "speed"]
    bottom = [0.0] * len(names)
    hatch = {"crushing": "", "bending": "//", "submersion": "", "speed": "\\\\"}
    for k in keys:
        vals = [results[n]["term_shares"][k] * 100.0 for n in names]
        ax.bar(names, vals, bottom=bottom, label=k, hatch=hatch[k])
        bottom = [b + v for b, v in zip(bottom, vals)]
    for i, n in enumerate(names):
        ax.text(i, bottom[i] + 1.5, f"{results[n]['untraceable_share']*100:.0f}% untraceable",
                ha="center", fontsize=8)
    ax.set_ylabel("share of raw resistance [%]")
    ax.set_ylim(0, 118)
    ax.set_title("Component breakdown at nominal conditions\n"
                 "hatched halves (submersion + speed) carry non-traceable constants")
    ax.legend(fontsize=8, ncol=4, loc="lower center")
    ax.grid(alpha=0.25, axis="y")
    p2 = os.path.join(out_dir, "fig2_component_shares.png")
    fig.tight_layout()
    fig.savefig(p2, dpi=150)
    plt.close(fig)
    paths.append(p2)

    # --- Fig 3: circularity -- raw vs calibrated residual (log scale)
    fig, ax = plt.subplots(figsize=(7.2, 4.2))
    x = range(len(names))
    raw_res = [abs(results[n]["raw_residual_vs_trial"]) * 100 for n in names]
    cal_res = [max(abs(results[n]["calibrated_residual_vs_trial"]) * 100, 1e-12) for n in names]
    ax.bar([i - 0.2 for i in x], raw_res, width=0.4, label="raw formula |error|", color="C3")
    ax.bar([i + 0.2 for i in x], cal_res, width=0.4,
           label="trial-anchored |error| (by construction)", color="C0")
    ax.axhline(15.0, color="k", ls="--", lw=1, label="15 % acceptance line")
    ax.set_yscale("log")
    ax.set_xticks(list(x))
    ax.set_xticklabels(names)
    ax.set_ylabel("|error| vs trial mean [%] (log)")
    ax.set_title("Why the nominal zero residual is not evidence:\n"
                 "the anchor forces it to zero (values ~1e-14 %)")
    ax.legend(fontsize=8)
    ax.grid(alpha=0.25, axis="y")
    p3 = os.path.join(out_dir, "fig3_circularity.png")
    fig.tight_layout()
    fig.savefig(p3, dpi=150)
    plt.close(fig)
    paths.append(p3)

    # --- Fig 4: cross-implementation band vs trial mean
    fig, ax = plt.subplots(figsize=(7.6, 4.2))
    w = 0.2
    for i, n in enumerate(names):
        r = results[n]
        ax.bar(i - 1.5 * w, r["raw_kN"], width=w, label="platform Lindqvist (raw)" if i == 0 else None,
               color="C0")
        ax.bar(i - 0.5 * w, r["cross_model_kN"]["lindqvist_like"], width=w,
               label="lindqvist_like (indep.)" if i == 0 else None, color="C1")
        ax.bar(i + 0.5 * w, r["cross_model_kN"]["riska"], width=w,
               label="Riska-type (indep.)" if i == 0 else None, color="C2")
        ax.plot([i + 0.5 * w], [r["nominal"]["trial_mean_kN"]], marker="_", ms=18, mew=2.4,
                color="k", label="trial mean" if i == 0 else None, zorder=6)
    ax.set_xticks(range(len(names)))
    ax.set_xticklabels(names)
    ax.set_ylabel("ice resistance at nominal conditions [kN]")
    ax.set_title("Model-form band at identical (h, sigma, mu, V)\n"
                 "black dash = platform-recorded trial mean")
    ax.legend(fontsize=8)
    ax.grid(alpha=0.25, axis="y")
    p4 = os.path.join(out_dir, "fig4_cross_model_band.png")
    fig.tight_layout()
    fig.savefig(p4, dpi=150)
    plt.close(fig)
    paths.append(p4)

    # --- Fig 5: beam scaling (extensiveness defect)
    fig, ax = plt.subplots(figsize=(7.2, 4.2))
    for n in names:
        r = results[n]
        b = [s["B_scale"] for s in r["beam_sweep"]]
        v = [s["raw_kN"] / r["raw_kN"] for s in r["beam_sweep"]]
        ax.loglog(b, v, "o-", label=f"{n} (B-exp={r['beam_exponent_total']:.2f})")
    ax.loglog([0.5, 2.0], [0.5, 2.0], "k--", lw=1, label="exact R ~ B$^1$")
    ax.set_xlabel("beam ratio B/B$_{ref}$")
    ax.set_ylabel("R / R$_{ref}$")
    bexp = [results[n]["beam_exponent_total"] for n in names]
    ax.set_title("Extensiveness in beam: bending is multiplied by B, crushing is not,\n"
                 f"so the total scales as B$^{{{min(bexp):.2f}}}$-B$^{{{max(bexp):.2f}}}$, not B$^1$")
    ax.grid(alpha=0.3, which="both")
    ax.legend(fontsize=8)
    p5 = os.path.join(out_dir, "fig5_beam_scaling.png")
    fig.tight_layout()
    fig.savefig(p5, dpi=150)
    plt.close(fig)
    paths.append(p5)

    return paths


if __name__ == "__main__":
    raise SystemExit(main())