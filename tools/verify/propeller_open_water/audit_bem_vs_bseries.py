<<<<<<< origin/main
#!/usr/bin/env python3
"""Independent reference audit: BEM open-water model vs the Wageningen B-series.

Subject under test
------------------
``backend/services/propeller_open_water.py`` -- a first-principles
blade-element/momentum (BEM) open-water model.  Its own test suite only checks
*self-consistency* (Froude scaling, existence of a zero-thrust advance ratio,
monotonicity in blade-area ratio, induction reduces loading).  Nothing in the
repository ever compared its **absolute** ``KT``/``KQ``/``eta_0`` against an
external reference, so the absolute accuracy of the model was an open
verification debt.

Reference
---------
``tools/verify/propeller_open_water/reference_bseries.py`` -- clean-room
transcription of the Oosterveld & van Oossanen (1975) 39-term KT / 47-term KQ
polynomials, cross-checked term-by-term against the platform's own
``backend/data/official/Wageningen_Bseries_coeffs.dat`` (see
``tests/verify/test_propeller_open_water_audit.py``).  That makes it usable as
an independent truth for the *B-series family*; it is **not** a truth for an
arbitrary propeller, which is stated as a limitation in the report.

What the audit measures
-----------------------
1. point-wise relative errors of ``KT``, ``KQ``, ``eta_0`` over
   5 B-series geometries x J = 0.05..0.90 (18 points each),
2. the zero-thrust advance ratio ``J0`` and the peak open-water efficiency,
3. the *verified envelope*: which (geometry, J) cells stay inside the platform
   acceptance band (15%),
4. the propagation of the discrepancy into the propulsion chain
   (``eta_D`` and delivered power ``P_D`` of a reference ship).

Usage
-----
    python3 tools/verify/propeller_open_water/audit_bem_vs_bseries.py

Writes ``artifacts/m1644/propeller_bem_ow/audit.json`` + three PNG figures.
No platform service is required (pure offline computation).
"""
from __future__ import annotations

=======
"""Validation + solution verification of the merged BEM open-water model
against the canonical Wageningen B-series polynomial (m1665).

Why this exists
---------------
`backend/services/propeller_open_water.py` (m1393) replaced the legacy
linear MOCK surrogate with a first-principles blade-element/momentum model.
Its own docstring states the remaining debt verbatim:

    "No literature point data was reachable offline for mission 1393, so the
     published verification is analytic-bound + internal-consistency only"

and `docs/audit/m1413_propulsion_verification_debt.md` §6.5 repeats it: the
B-series absolute-magnitude benchmark was never run.  Since m1403 the repo has
shipped a clean-room transcription of the Oosterveld & van Oossanen (1975)
39/47-term regression (`tools/verify/propeller_open_water/reference_bseries.py`),
so the debt is now closable offline.  This script closes it and, more
importantly, separates the two error sources that a bare comparison confounds:

* **model-form error**  — BEM physics vs B-series regression (what a
  validation exercise measures), and
* **discretisation error** — radial strip integration, quantified with a
  refinement study and a Roache GCI, i.e. solution verification.

The verdict is deliberately *not* "REAL vs MOCK": a blade-element model with a
symmetric NACA-less section polar is not expected to reproduce a B-series
regression to 3%.  What the platform needs to know is the size of the gap, its
J-dependence, and whether it is numerical (fixable by refining) or model-form
(fixable only by more physics).  Both are quantified here.

Method
------
1. Five geometries inside the published validity box
   (Z=2..7, P/D=0.5..1.4, Ae/A0=0.2..1.05) x J = 0.05..0.90 (step 0.05).
2. Point-wise relative errors of KT, KQ, eta_0 on the physical branch
   (KT>0 and KQ>0; past the zero-thrust point eta_0 is meaningless because KQ
   cancels).
3. Integral anchors that are insensitive to point scatter: zero-thrust advance
   ratio J(KT=0) and the peak-efficiency point.
4. Radial refinement study at the reference geometry: sections =
   10..320, Richardson extrapolation, observed order p, GCI_fine (safety 1.25)
   on KT/KQ/eta_0 at three advance ratios.
5. Sensitivity diagnosis (which modelling knob owns the gap): section drag
   CD0, momentum coupling on/off (`axial_induction`), hub ratio.

Run
---
    python3 tools/verify/propeller_open_water/audit_bem_vs_bseries.py
    python3 tools/verify/propeller_open_water/audit_bem_vs_bseries.py --report

Writes ``artifacts/m1665/bem_vs_bseries/audit.json`` + 4 PNGs; ``--report``
additionally emits ``docs/reports/m1665-bem-vs-bseries-verification.md`` and a
platform report payload where every number is injected from the JSON (no
hand-transcription).
"""

from __future__ import annotations

import argparse
>>>>>>> refs/remotes/origin/agent/m1665
import json
import math
import os
import sys
<<<<<<< origin/main
from typing import Dict, List

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
sys.path.insert(0, os.path.join(REPO_ROOT, "backend"))
sys.path.insert(0, os.path.join(REPO_ROOT, "tools", "verify", "propeller_open_water"))

from services.propeller_open_water import (  # noqa: E402
    PropellerGeometry,
    ideal_efficiency_bound,
    open_water_point,
    solve_zero_thrust_advance_ratio,
)
import reference_bseries as ref  # noqa: E402
from services.propulsion import self_propulsion_point  # noqa: E402

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
#: B-series geometries spanning the declared validity box
#: (Z = 2..7, P/D = 0.5..1.4, Ae/A0 = 0.2..1.05).
GEOMETRIES: List[Dict[str, float]] = [
    {"name": "B4-70", "P_D": 1.00, "Ae_Ao": 0.70, "Z": 4},
    {"name": "B4-55", "P_D": 0.80, "Ae_Ao": 0.55, "Z": 4},
    {"name": "B4-40", "P_D": 1.00, "Ae_Ao": 0.40, "Z": 4},
    {"name": "B5-80", "P_D": 0.95, "Ae_Ao": 0.80, "Z": 5},
    {"name": "B3-50", "P_D": 1.20, "Ae_Ao": 0.50, "Z": 3},
]

#: Advance ratios are sampled on the *normalised* loaded branch
#: ``J/J0 = 0.15 ... 0.85`` where ``J0`` is the B-series zero-thrust advance
#: ratio of the same geometry.  Rationale: a fixed absolute J grid makes the
#: relative error of ``KT`` meaningless near ``J0`` (KT -> 0, so a small absolute
#: offset explodes into a huge percentage), and it compares geometrically
#: different operating points.  Normalising by J0 compares like with like.
J_OVER_J0: List[float] = [round(0.15 + 0.05 * k, 2) for k in range(15)]

#: Backwards-compatible alias used by the figure code (filled per geometry).
J_GRID: List[float] = []

#: Platform acceptance band: relative error > 15% is not acceptable.
TOL_PCT = 15.0

#: Radial strip count.  The module default is 40; the audit uses 24 because the
#: discretisation sensitivity measured below (``discretisation_sensitivity`` in
#: the JSON, 12/24/40 strips on one geometry) is well under 1% on KT while the
#: full audit gets ~2.5x cheaper, which keeps the reproducibility gate inside a
#: normal test budget on a loaded machine.
SECTIONS = 24

DIAMETER_M = 5.0
RHO = 1025.0
N_RPS = 1.0

#: Reference-ship propulsion-chain propagation case (single-screw, AE/A0=0.55
#: family).  Resistance/speed are representative of a 150 m single-screw hull.
CHAIN = {
    "resistance_n": 1.0e6,
    "speed_ms": 7.716,          # 15 kn
    "diameter_m": 5.0,
    "wake_fraction": 0.30,
    "thrust_deduction": 0.18,
    "relative_rotative_efficiency": 1.00,
}

ART_DIR = os.path.join(REPO_ROOT, "artifacts", "m1644", "propeller_bem_ow")


def rel_err(value: float, ref_value: float, floor: float) -> float:
    """Relative error [%] with a floor so that near-zero references do not blow up."""
    denom = max(abs(ref_value), floor)
    return 100.0 * (value - ref_value) / denom


def audit_point(geom: PropellerGeometry, J: float, P_D: float, Ae_Ao: float, Z: int) -> Dict:
    bem = open_water_point(geom, J, rho=RHO, n_rps=N_RPS)
    r = ref.open_water_coefficients(J, P_D, Ae_Ao, Z)
    return {
        "J": J,
        "KT_bem": bem["KT"],
        "KT_ref": r["KT"],
        "KQ_bem": bem["KQ"],
        "KQ_ref": r["KQ"],
        "eta0_bem": bem["eta0"],
        "eta0_ref": r["eta_0"],
        "eta_ideal_bem": bem["eta_ideal"],
        "e_KT_pct": rel_err(bem["KT"], r["KT"], 1.0e-3),
        "e_KQ_pct": rel_err(bem["KQ"], r["KQ"], 1.0e-4),
        "e_eta_pct": rel_err(bem["eta0"], r["eta_0"], 1.0e-2),
        "induction_converged": bem["induction_converged"],
    }


def audit_geometry(g: Dict[str, float]) -> Dict:
    geom = PropellerGeometry(
        diameter_m=DIAMETER_M,
        pitch_ratio=g["P_D"],
        blade_area_ratio=g["Ae_Ao"],
        blade_count=int(g["Z"]),
        sections=SECTIONS,
    )
    j0_ref = ref.zero_thrust_J(g["P_D"], g["Ae_Ao"], int(g["Z"]))
    js = [round(r * j0_ref, 5) for r in J_OVER_J0]
    rows = [audit_point(geom, J, g["P_D"], g["Ae_Ao"], int(g["Z"])) for J in js]
    for r, j_over in zip(rows, J_OVER_J0):
        r["J_over_J0"] = j_over

    def mean(key: str) -> float:
        return sum(abs(r[key]) for r in rows) / len(rows)

    j0_bem = solve_zero_thrust_advance_ratio(geom, rho=RHO)
    j0_ref = ref.zero_thrust_J(g["P_D"], g["Ae_Ao"], int(g["Z"]))
    peak_ref = ref.peak_efficiency(g["P_D"], g["Ae_Ao"], int(g["Z"]))
    # BEM peak efficiency on the same physical branch (KT>0, KQ>0)
    best = {"J": float("nan"), "eta0": -1.0}
    n = 40
    for i in range(n + 1):
        J = 1.4 * i / n
        pt = open_water_point(geom, J, rho=RHO, n_rps=N_RPS)
        if pt["KT"] <= 0.0 or pt["KQ"] <= 0.0:
            continue
        if pt["eta0"] > best["eta0"]:
            best = {"J": J, "eta0": pt["eta0"]}

    inside = sum(
        1
        for r in rows
        if abs(r["e_KT_pct"]) <= TOL_PCT and abs(r["e_KQ_pct"]) <= TOL_PCT
    )
    # energy guard: eta_0 must never exceed the actuator-disc bound
    guard = max(r["eta0_bem"] / r["eta_ideal_bem"] for r in rows if r["eta_ideal_bem"] > 0)
    return {
        "name": g["name"],
        "P_D": g["P_D"],
        "Ae_Ao": g["Ae_Ao"],
        "Z": int(g["Z"]),
        "rows": rows,
        "mean_abs_e_KT_pct": mean("e_KT_pct"),
        "max_abs_e_KT_pct": max(abs(r["e_KT_pct"]) for r in rows),
        "mean_abs_e_KQ_pct": mean("e_KQ_pct"),
        "max_abs_e_KQ_pct": max(abs(r["e_KQ_pct"]) for r in rows),
        "mean_abs_e_eta_pct": mean("e_eta_pct"),
        "max_abs_e_eta_pct": max(abs(r["e_eta_pct"]) for r in rows),
        "cells_within_15pct": inside,
        "cells_total": len(rows),
        "J0_bem": j0_bem,
        "J0_ref": j0_ref,
        "e_J0_pct": rel_err(j0_bem, j0_ref, 1.0e-3),
        "eta0_max_bem": best["eta0"],
        "J_eta0_max_bem": best["J"],
        "eta0_max_ref": peak_ref["eta_0"],
        "J_eta0_max_ref": peak_ref["J"],
        "e_eta0_max_pct": rel_err(best["eta0"], peak_ref["eta_0"], 1.0e-3),
        "eta0_over_ideal_max": guard,
        "all_induction_converged": all(r["induction_converged"] for r in rows),
    }


def propulsion_chain_propagation(g: Dict[str, float]) -> Dict:
    """Impact of the KT/KQ bias on eta_D and P_D of the reference ship.

    Two runs of the *same* thrust-identity solver: one fed with the BEM curve,
    one with the B-series polynomial.  Everything else (R_T, V, D, w, t, eta_R)
    is identical, so the difference is attributable to the open-water curve only.
    """
    geom = PropellerGeometry(
        diameter_m=CHAIN["diameter_m"],
        pitch_ratio=g["P_D"],
        blade_area_ratio=g["Ae_Ao"],
        blade_count=int(g["Z"]),
        sections=SECTIONS,
    )

    def bem_kt(J: float) -> float:
        return open_water_point(geom, J, rho=RHO, n_rps=N_RPS)["KT"]

    def bem_kq(J: float) -> float:
        return open_water_point(geom, J, rho=RHO, n_rps=N_RPS)["KQ"]

    def ref_kt(J: float) -> float:
        return ref.open_water_coefficients(J, g["P_D"], g["Ae_Ao"], int(g["Z"]))["KT"]

    def ref_kq(J: float) -> float:
        return ref.open_water_coefficients(J, g["P_D"], g["Ae_Ao"], int(g["Z"]))["KQ"]

    kw = dict(
        resistance_n=CHAIN["resistance_n"],
        ship_speed_ms=CHAIN["speed_ms"],
        diameter_m=CHAIN["diameter_m"],
        wake_fraction=CHAIN["wake_fraction"],
        thrust_deduction=CHAIN["thrust_deduction"],
        relative_rotative_efficiency=CHAIN["relative_rotative_efficiency"],
        rho=RHO,
        # bracket chosen so that the advance ratio stays inside the polynomial
        # validity box (J <= 1.5) at the low end of the shaft-rate search; the
        # platform default n_lo = 1e-3 rps evaluates the B-series at J ~ 1e3,
        # i.e. far outside its validity box, and the bracket test then fails.
        n_lo=0.2,
        n_hi=20.0,
    )
    bem = self_propulsion_point(kt_fn=bem_kt, kq_fn=bem_kq, **kw)
    refsol = self_propulsion_point(kt_fn=ref_kt, kq_fn=ref_kq, **kw)

    def pack(res):
        return {
            "n_rps": res.revolutions_per_s,
            "J": res.advance_ratio,
            "KT": res.thrust_coefficient,
            "KQ": res.torque_coefficient,
            "P_D_w": res.delivered_power_w,
            "eta_o": res.open_water_efficiency,
            "eta_D": res.quasi_propulsive_efficiency,
            "thrust_residual_n": res.thrust_balance_residual_n,
        }

    b, r = pack(bem), pack(refsol)
    return {
        "bem": b,
        "ref": r,
        "e_P_D_pct": rel_err(b["P_D_w"], r["P_D_w"], 1.0),
        "e_eta_D_pct": rel_err(b["eta_D"], r["eta_D"], 1.0e-6),
        "e_n_pct": rel_err(b["n_rps"], r["n_rps"], 1.0e-6),
        "both_residuals_below_1n": abs(b["thrust_residual_n"]) < 1.0
                                    and abs(r["thrust_residual_n"]) < 1.0,
    }


def make_figures(result: Dict) -> List[str]:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import numpy as np

    os.makedirs(ART_DIR, exist_ok=True)
    paths: List[str] = []

    # --- fig 1: curve families ------------------------------------------------
    fig, axes = plt.subplots(3, 5, figsize=(21, 10.5), sharex=True)
    for col, g in enumerate(result["geometries"]):
        J = [r["J"] for r in g["rows"]]
        kt_b = [r["KT_bem"] for r in g["rows"]]
        kt_r = [r["KT_ref"] for r in g["rows"]]
        kq_b = [r["KQ_bem"] for r in g["rows"]]
        kq_r = [r["KQ_ref"] for r in g["rows"]]
        et_b = [r["eta0_bem"] for r in g["rows"]]
        et_r = [r["eta0_ref"] for r in g["rows"]]
        for row, (yb, yr, label) in enumerate(
            [(kt_b, kt_r, "$K_T$"), (kq_b, kq_r, "$K_Q$"), (et_b, et_r, r"$\eta_0$")]
        ):
            ax = axes[row, col]
            ax.plot(J, yr, "k-", lw=2, label="Wageningen B-series (ref)")
            ax.plot(J, yb, "o--", ms=3.5, color="#d62728", label="platform BEM")
            ax.grid(alpha=0.3)
            if row == 0:
                ax.set_title(f"{g['name']}  P/D={g['P_D']}  Ae/A0={g['Ae_Ao']}  Z={g['Z']}")
            if row == 2:
                ax.set_xlabel("J = V$_a$/(nD)  [J/J$_0$ = 0.15 ... 0.85]")
            ax.set_ylabel(label)
            if row == 0 and col == 0:
                ax.legend(fontsize=8, loc="best")
    fig.suptitle("BEM open-water model vs Wageningen B-series polynomial reference "
                 "(5 geometries, J = 0.05 ... 0.90)", fontsize=14)
    fig.tight_layout(rect=(0, 0, 1, 0.96))
    p = os.path.join(ART_DIR, "fig1_curves_bem_vs_bseries.png")
    fig.savefig(p, dpi=120)
    plt.close(fig)
    paths.append(p)

    # --- fig 2: KT error map over (Ae/A0) x J for Z=4 -------------------------
    fig, axes = plt.subplots(1, 2, figsize=(14, 5.2))
    for ax, key, title in (
        (axes[0], "e_KT_pct", r"$\Delta K_T$ / $K_T$ [%]"),
        (axes[1], "e_eta_pct", r"$\Delta\eta_0$ / $\eta_0$ [%]"),
    ):
        data = np.array([[r[key] for r in g["rows"]] for g in result["geometries"]])
        labels = [g["name"] for g in result["geometries"]]
        im = ax.imshow(data, aspect="auto", cmap="RdBu_r", vmin=-60, vmax=60)
        ax.set_yticks(range(len(labels)), labels)
        ax.set_xticks(range(0, len(J_OVER_J0), 2),
                      [f"{J_OVER_J0[i]:.2f}" for i in range(0, len(J_OVER_J0), 2)])
        ax.set_xlabel("J / J$_0$")
        ax.set_title(title)
        for i in range(data.shape[0]):
            for j in range(data.shape[1]):
                ax.text(j, i, f"{data[i, j]:.0f}", ha="center", va="center", fontsize=6)
        fig.colorbar(im, ax=ax)
    fig.suptitle("Error map of the platform BEM model against the B-series reference "
                 "(acceptance band = +/-15%)")
    fig.tight_layout(rect=(0, 0, 1, 0.93))
    p = os.path.join(ART_DIR, "fig2_error_map.png")
    fig.savefig(p, dpi=120)
    plt.close(fig)
    paths.append(p)

    # --- fig 3: integral characteristics --------------------------------------
    names = [g["name"] for g in result["geometries"]]
    x = np.arange(len(names))
    fig, axes = plt.subplots(1, 3, figsize=(16, 4.8))
    axes[0].bar(x - 0.2, [g["J0_ref"] for g in result["geometries"]], 0.4, label="B-series ref", color="k")
    axes[0].bar(x + 0.2, [g["J0_bem"] for g in result["geometries"]], 0.4, label="BEM", color="#d62728")
    axes[0].set_ylabel("J at K$_T$ = 0")
    axes[0].set_title("Zero-thrust advance ratio")
    axes[1].bar(x - 0.2, [g["eta0_max_ref"] for g in result["geometries"]], 0.4, label="B-series ref", color="k")
    axes[1].bar(x + 0.2, [g["eta0_max_bem"] for g in result["geometries"]], 0.4, label="BEM", color="#d62728")
    axes[1].set_ylabel(r"$\eta_{0,max}$")
    axes[1].set_title("Peak open-water efficiency")
    axes[2].bar(x, [g["mean_abs_e_KT_pct"] for g in result["geometries"]], 0.5, color="#1f77b4")
    axes[2].axhline(TOL_PCT, color="k", ls="--", lw=1.2, label="15% band")
    axes[2].set_ylabel(r"mean $|\Delta K_T|$ [%]")
    axes[2].set_title("Mean KT error (band = 15%)")
    for ax in axes:
        ax.set_xticks(x, names)
        ax.grid(alpha=0.3, axis="y")
        ax.legend(fontsize=8)
    fig.tight_layout()
    p = os.path.join(ART_DIR, "fig3_integral_characteristics.png")
    fig.savefig(p, dpi=120)
    plt.close(fig)
    paths.append(p)
    return paths


def main() -> Dict:
    geometries = [audit_geometry(g) for g in GEOMETRIES]
    chains = [
        {"name": g["name"], **propulsion_chain_propagation(g)} for g in GEOMETRIES
    ]
    all_rows = [r for g in geometries for r in g["rows"]]
    # discretisation sensitivity of the strip integration (KT at one fixed point)
    sens = {}
    for n_sec in (12, SECTIONS, 40):
        gg = PropellerGeometry(pitch_ratio=1.0, blade_area_ratio=0.70,
                              blade_count=4, sections=n_sec)
        sens[str(n_sec)] = open_water_point(gg, 0.40 * ref.zero_thrust_J(1.0, 0.70, 4))["KT"]
    sens["max_rel_spread_pct"] = 100.0 * (max(sens["12"], sens["24"], sens["40"])
                                          - min(sens["12"], sens["24"], sens["40"])) / sens["40"]
    worst_kt = max(all_rows, key=lambda r: abs(r["e_KT_pct"]))
    worst_kq = max(all_rows, key=lambda r: abs(r["e_KQ_pct"]))
    result = {
        "audit": "BEM open-water model vs Wageningen B-series reference",
        "subject": "backend/services/propeller_open_water.py",
        "reference": "tools/verify/propeller_open_water/reference_bseries.py "
                     "(Oosterveld & van Oossanen 1975, 39/47-term tables)",
        "tolerance_pct": TOL_PCT,
        "j_over_j0_grid": J_OVER_J0,
        "rho": RHO,
        "n_rps": N_RPS,
        "sections": SECTIONS,
        "discretisation_sensitivity": sens,
        "geometries": geometries,
        "chain_propagation": chains,
        "summary": {
            "cells_total": len(all_rows),
            "cells_within_15pct_KT_and_KQ": sum(
                1 for r in all_rows
                if abs(r["e_KT_pct"]) <= TOL_PCT and abs(r["e_KQ_pct"]) <= TOL_PCT),
            "mean_abs_e_KT_pct_all": sum(abs(r["e_KT_pct"]) for r in all_rows) / len(all_rows),
            "mean_abs_e_KQ_pct_all": sum(abs(r["e_KQ_pct"]) for r in all_rows) / len(all_rows),
            "mean_abs_e_eta_pct_all": sum(abs(r["e_eta_pct"]) for r in all_rows) / len(all_rows),
            "max_abs_e_KT_pct": abs(worst_kt["e_KT_pct"]),
            "max_abs_e_KT_cell": f"{worst_kt['J']:.2f}",
            "max_abs_e_KQ_pct": abs(worst_kq["e_KQ_pct"]),
            "max_abs_e_KQ_cell": f"{worst_kq['J']:.2f}",
            "worst_geometry_by_KT": max(geometries, key=lambda g: g["mean_abs_e_KT_pct"])["name"],
            "max_e_P_D_pct": max(abs(c["e_P_D_pct"]) for c in chains),
            "max_e_eta_D_pct": max(abs(c["e_eta_D_pct"]) for c in chains),
            "chain_residuals_ok": all(c["both_residuals_below_1n"] for c in chains),
            "all_eta0_below_ideal_bound": all(
                g["eta0_over_ideal_max"] <= 1.0 + 1e-12 for g in geometries),
            "all_induction_converged": all(g["all_induction_converged"] for g in geometries),
        },
    }
    os.makedirs(ART_DIR, exist_ok=True)
    paths = make_figures(result)
    result["figures"] = [os.path.relpath(p, REPO_ROOT) for p in paths]
    out = os.path.join(ART_DIR, "audit.json")
    with open(out, "w", encoding="utf-8") as fh:
        json.dump(result, fh, indent=2, ensure_ascii=False)

    s = result["summary"]
    print(f"cells within +/-{TOL_PCT:.0f}% (KT & KQ): "
          f"{s['cells_within_15pct_KT_and_KQ']}/{s['cells_total']}")
    print(f"mean |dKT| = {s['mean_abs_e_KT_pct_all']:.1f}%  "
          f"mean |dKQ| = {s['mean_abs_e_KQ_pct_all']:.1f}%  "
          f"mean |dEta| = {s['mean_abs_e_eta_pct_all']:.1f}%")
    print(f"max |dKT| = {s['max_abs_e_KT_pct']:.1f}% (J/J0={s['max_abs_e_KT_cell']})  "
          f"max |dKQ| = {s['max_abs_e_KQ_pct']:.1f}%")
    print(f"propagation: max |dP_D| = {s['max_e_P_D_pct']:.1f}%  "
          f"max |dEta_D| = {s['max_e_eta_D_pct']:.1f}%")
    print(f"eta_0 <= ideal bound everywhere: {s['all_eta0_below_ideal_bound']}")
    print(f"written: {out}")
    return result


if __name__ == "__main__":
    main()
=======
from typing import Dict, List, Sequence, Tuple

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(HERE)))
for _p in (os.path.join(ROOT, "backend"), ROOT):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from services.propeller_open_water import (  # noqa: E402
    PropellerGeometry,
    open_water_point,
    solve_zero_thrust_advance_ratio,
)

sys.path.insert(0, HERE)
import reference_bseries as ref  # noqa: E402

ART = os.path.join(ROOT, "artifacts", "m1665", "bem_vs_bseries")
DOC = os.path.join(ROOT, "docs", "reports", "m1665-bem-vs-bseries-verification.md")

#: Geometry matrix inside the published B-series validity box.
GEOMETRIES: List[Dict[str, float]] = [
    {"name": "B4-70", "P_D": 1.00, "Ae_Ao": 0.70, "Z": 4},
    {"name": "B4-55", "P_D": 0.80, "Ae_Ao": 0.55, "Z": 4},
    {"name": "B5-80", "P_D": 0.95, "Ae_Ao": 0.80, "Z": 5},
    {"name": "B3-50", "P_D": 1.20, "Ae_Ao": 0.50, "Z": 3},
    {"name": "B7-85", "P_D": 1.10, "Ae_Ao": 0.85, "Z": 7},
]

J_GRID: List[float] = [round(0.05 * k, 2) for k in range(1, 19)]  # 0.05 .. 0.90
REF_GEOM = "B4-70"
BOLTED_J = 0.30          # bollard-side band where K_T>0 is mandatory
LOW_J_MAX = 0.50          # band where the regression is trustworthy (KT well above 0)
SECTIONS_STUDY: Tuple[int, ...] = (10, 20, 40, 80, 160, 320)
REFINE_RATIO = 2.0
GCI_SAFETY = 1.25         # Roache: 1.25 for a three-grid study with p from data
ACCEPT_PCT = 15.0         # platform rule: >15% is not acceptable
TARGET_PCT = 3.0          # report gate: "is the error below 3%?"


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
def _geom(spec: Dict[str, float], sections: int = 40, **over) -> PropellerGeometry:
    kw = dict(
        diameter_m=5.0,
        pitch_ratio=float(spec["P_D"]),
        blade_area_ratio=float(spec["Ae_Ao"]),
        blade_count=int(spec["Z"]),
        sections=sections,
    )
    kw.update(over)
    return PropellerGeometry(**kw)


def _canon(J: float, spec: Dict[str, float]) -> Dict[str, float]:
    return ref.open_water_coefficients(J, float(spec["P_D"]), float(spec["Ae_Ao"]), int(spec["Z"]))


def _rel(a: float, b: float) -> float:
    return float("nan") if abs(b) < 1e-12 else 100.0 * (a - b) / b


def _stats(xs: Sequence[float]) -> Dict[str, float]:
    xs = [x for x in xs if x == x]
    if not xs:
        return {"mean": float("nan"), "max_abs": float("nan"), "n": 0}
    return {
        "mean": sum(xs) / len(xs),
        "max_abs": max(abs(x) for x in xs),
        "n": len(xs),
    }


# ---------------------------------------------------------------------------
# 1. point-wise comparison
# ---------------------------------------------------------------------------
def compare_geometry(spec: Dict[str, float], sections: int = 40) -> Dict[str, object]:
    g = _geom(spec, sections=sections)
    rows: List[Dict[str, float]] = []
    for J in J_GRID:
        b = open_water_point(g, J)
        c = _canon(J, spec)
        rows.append({
            "J": J,
            "KT_bem": b["KT"], "KT_ref": c["KT"], "e_KT": _rel(b["KT"], c["KT"]),
            "KQ_bem": b["KQ"], "KQ_ref": c["KQ"], "e_KQ": _rel(b["KQ"], c["KQ"]),
            "eta_bem": b["eta0"], "eta_ref": c["eta_0"],
            "e_eta": _rel(b["eta0"], c["eta_0"]),
            "eta_ideal": b["eta_ideal"],
            "physical": bool(c["KT"] > 0.0 and c["KQ"] > 0.0),
        })
    phys = [r for r in rows if r["physical"]]
    low = [r for r in phys if r["J"] <= LOW_J_MAX]

    # integral anchors: zero-thrust advance ratio and peak efficiency
    j0_bem = solve_zero_thrust_advance_ratio(g)
    j0_ref = ref.zero_thrust_J(float(spec["P_D"]), float(spec["Ae_Ao"]), int(spec["Z"]))
    # BEM peak efficiency on the same fine grid used for the reference
    best_bem = {"J": float("nan"), "eta_0": -1.0}
    n = 140
    for i in range(n + 1):
        J = 1.4 * i / n
        p = open_water_point(g, J)
        if p["KT"] <= 0.0 or p["KQ"] <= 0.0:
            continue
        if p["eta0"] > best_bem["eta_0"]:
            best_bem = {"J": J, "eta_0": p["eta0"]}
    best_ref = ref.peak_efficiency(float(spec["P_D"]), float(spec["Ae_Ao"]), int(spec["Z"]))

    return {
        "geometry": spec,
        "sections": sections,
        "rows": rows,
        "low_J": {
            "e_KT": _stats([r["e_KT"] for r in low]),
            "e_KQ": _stats([r["e_KQ"] for r in low]),
            "e_eta": _stats([r["e_eta"] for r in low]),
        },
        "all_J": {
            "e_KT": _stats([r["e_KT"] for r in phys]),
            "e_KQ": _stats([r["e_KQ"] for r in phys]),
            "e_eta": _stats([r["e_eta"] for r in phys]),
        },
        "anchors": {
            "J0_bem": j0_bem, "J0_ref": j0_ref, "J0_err_pct": _rel(j0_bem, j0_ref),
            "peak_J_bem": best_bem["J"], "peak_eta_bem": best_bem["eta_0"],
            "peak_J_ref": best_ref["J"], "peak_eta_ref": best_ref["eta_0"],
            "peak_eta_err_pct": _rel(best_bem["eta_0"], best_ref["eta_0"]),
            "eta_vs_ideal_max_ratio": max(
                (r["eta_bem"] / r["eta_ideal"] for r in phys if r["eta_ideal"] > 0), default=float("nan")),
        },
    }


# ---------------------------------------------------------------------------
# 2. solution verification (radial refinement + GCI)
# ---------------------------------------------------------------------------
def richardson_gci(f_coarse: float, f_medium: float, f_fine: float, r: float) -> Dict[str, float]:
    """Roache GCI for three systematically refined solutions (r = refinement ratio).

    Observed order p = ln|(f3-f2)/(f2-f1)| / ln r (monotone convergence taken
    as |(f3-f2)/(f2-f1)| > 0).  Extrapolated value f_exact = f1 + (f1-f2)/(r^p - 1).
    """
    d21 = f_medium - f_coarse
    d32 = f_fine - f_medium
    out = {"p": float("nan"), "f_exact": float("nan"), "gci_fine_pct": float("nan"),
           "asymptotic": False, "ratio": float("nan")}
    if abs(d32) < 1e-15 or abs(d21) < 1e-15:
        out["ratio"] = 0.0
        return out
    ratio = d32 / d21
    out["ratio"] = ratio
    if ratio <= 0.0:            # oscillatory / divergent
        return out
    # p = ln(|d21|/|d32|) / ln r  (equivalently -ln(ratio)/ln r for monotone decay)
    p = math.log(1.0 / ratio) / math.log(r)
    out["p"] = p
    out["asymptotic"] = bool(0.0 < ratio < 1.0)
    if p > 0.05:
        rp = r ** p
        out["f_exact"] = f_fine + (f_fine - f_medium) / (rp - 1.0)
        out["gci_fine_pct"] = 100.0 * GCI_SAFETY * abs((f_fine - f_medium) / f_fine) / (rp - 1.0)
    return out


def convergence_study(spec: Dict[str, float], Js: Sequence[float]) -> Dict[str, object]:
    out: Dict[str, object] = {"geometry": spec, "Js": list(Js), "series": {}, "gci": {}}
    for J in Js:
        vals: Dict[str, List[float]] = {"KT": [], "KQ": [], "eta0": []}
        for n in SECTIONS_STUDY:
            p = open_water_point(_geom(spec, sections=n), J)
            vals["KT"].append(p["KT"])
            vals["KQ"].append(p["KQ"])
            vals["eta0"].append(p["eta0"])
        out["series"][f"{J:.2f}"] = {"sections": list(SECTIONS_STUDY), **vals}
        gci = {}
        for key in ("KT", "KQ", "eta0"):
            seq = vals[key]
            gci[key] = richardson_gci(seq[-3], seq[-2], seq[-1], REFINE_RATIO)
            gci[key]["fine"] = seq[-1]
            gci[key]["extrapolated_rel_err_pct"] = _rel(seq[-1], gci[key]["f_exact"])
            # discretisation error of the shipped 40-section model vs the
            # extrapolated (grid-independent) value
            idx = SECTIONS_STUDY.index(40)
            gci[key]["shipped40_rel_err_pct"] = _rel(seq[idx], gci[key]["f_exact"])
        out["gci"][f"{J:.2f}"] = gci
    return out


# ---------------------------------------------------------------------------
# 3. sensitivity diagnosis: which knob owns the gap?
# ---------------------------------------------------------------------------
def sensitivity(spec: Dict[str, float], J: float = 0.50) -> Dict[str, object]:
    import services.propeller_open_water as bem

    def eval_with(**over) -> Dict[str, float]:
        g = _geom(spec, sections=40, **over)
        p = open_water_point(g, J)
        return {"KT": p["KT"], "KQ": p["KQ"], "eta0": p["eta0"]}

    base = eval_with()
    canon = _canon(J, spec)
    cases: Dict[str, object] = {}

    # (a) section drag level CD0 -- the model's declared calibration knob
    cd0_orig = bem.CD0
    try:
        for cd0 in (0.006, 0.012, 0.020, 0.040):
            bem.CD0 = cd0
            g = _geom(spec)
            p = open_water_point(g, J)
            cases[f"CD0={cd0:.3f}"] = {
                "KT": p["KT"], "KQ": p["KQ"], "eta0": p["eta0"],
                "e_KT": _rel(p["KT"], canon["KT"]), "e_KQ": _rel(p["KQ"], canon["KQ"]),
                "e_eta": _rel(p["eta0"], canon["eta_0"]),
            }
    finally:
        bem.CD0 = cd0_orig

    # (b) momentum coupling off -> pure blade element
    g = _geom(spec)
    p = open_water_point(g, J, axial_induction=False)
    cases["axial_induction=False"] = {
        "KT": p["KT"], "KQ": p["KQ"], "eta0": p["eta0"],
        "e_KT": _rel(p["KT"], canon["KT"]), "e_KQ": _rel(p["KQ"], canon["KQ"]),
        "e_eta": _rel(p["eta0"], canon["eta_0"]),
    }

    # (c) hub ratio: does the assumed hub cut-off explain part of the gap?
    for hub in (0.12, 0.18, 0.25):
        g = _geom(spec, hub_ratio=hub)
        p = open_water_point(g, J)
        cases[f"hub_ratio={hub:.2f}"] = {
            "KT": p["KT"], "KQ": p["KQ"], "eta0": p["eta0"],
            "e_KT": _rel(p["KT"], canon["KT"]), "e_KQ": _rel(p["KQ"], canon["KQ"]),
            "e_eta": _rel(p["eta0"], canon["eta_0"]),
        }

    return {
        "geometry": spec, "J": J, "canon": canon, "base": base,
        "base_e_KT": _rel(base["KT"], canon["KT"]),
        "base_e_KQ": _rel(base["KQ"], canon["KQ"]),
        "base_e_eta": _rel(base["eta0"], canon["eta_0"]),
        "cases": cases,
    }


# ---------------------------------------------------------------------------
# 3b. applicability probe: which model should be served where?
# ---------------------------------------------------------------------------
def applicability_probe() -> Dict[str, object]:
    """Count physical-rail violations of both models in and out of the
    polynomial's published validity box.

    The two paths fail in different directions: a regression polynomial is
    exact-in-box but blind out-of-box, while a blade-element model keeps its
    physics (monotone K_T, eta_0 < eta_ideal) but carries model-form error.
    This block makes the routing decision evidence-based instead of stylistic.
    """
    Js = [round(0.05 * k, 2) for k in range(1, 19)]
    out: Dict[str, object] = {"boxes": ref.VALIDITY, "rows": [], "summary": {}}
    cases: List[Dict[str, float]] = []
    for pd in (0.40, 0.50, 0.70, 1.00, 1.40, 1.60):
        for ae in (0.35, 0.70, 0.95):
            cases.append({"P_D": pd, "Ae_Ao": ae, "Z": 4})

    counts = {"poly": {"in_box": 0, "out_box": 0}, "bem": {"in_box": 0, "out_box": 0}}
    for spec in cases:
        in_box = (0.5 <= spec["P_D"] <= 1.4) and (0.2 <= spec["Ae_Ao"] <= 1.05)
        g = _geom(spec)
        # A violation is a *physical* rail break, not merely "past the
        # zero-thrust point": beyond J(K_T=0) a propeller is in the
        # turbine/brake branch, where K_T<0 is correct behaviour, not an error.
        poly_eta_bad = 0
        poly_kq_bad = 0
        bem_eta_bad = 0
        bem_kq_bad = 0
        prev_kt = None
        bem_nonmono = 0

        def _rail(cl: Dict[str, float], J: float) -> Tuple[int, int, int]:
            thrust_bad = 0
            torque_bad = 0
            eta_bad = 0
            # low-J thrust sign: a positive-pitch propeller must push forward
            if J <= BOLTED_J and cl["KT"] <= 0.0:
                thrust_bad = 1
            # torque must stay positive while thrust is positive (driving branch)
            if cl["KT"] > 0.0 and not (cl["KQ"] > 0.0):
                torque_bad = 1
            # efficiency is only defined on the driving branch (KQ>0); where
            # KQ -> 0 the ratio eta = J KT/(2 pi KQ) diverges for *any* model,
            # polynomial included, so that region is cropped, not counted.
            eff = cl.get("eta_0", cl.get("eta0"))
            if cl["KQ"] > 0.0 and cl["KT"] > 0.0 and eff == eff and eff > 1.0:
                eta_bad = 1
            return thrust_bad, torque_bad, eta_bad

        for J in Js:
            c = _canon(J, spec)
            b = open_water_point(g, J)
            t_bad, q_bad, e_bad = _rail(c, J)
            poly_kq_bad += t_bad + q_bad
            poly_eta_bad += e_bad
            t_bad, q_bad, e_bad = _rail(b, J)
            bem_kq_bad += t_bad + q_bad
            if b["eta0"] > b["eta_ideal"] + 1e-9:
                bem_eta_bad += 1
            if prev_kt is not None and b["KT"] > prev_kt + 1e-12:
                bem_nonmono += 1
            prev_kt = b["KT"]
        tag = "in_box" if in_box else "out_box"
        counts["poly"][tag] += poly_eta_bad + poly_kq_bad
        counts["bem"][tag] += bem_eta_bad + bem_kq_bad + bem_nonmono
        out["rows"].append({
            **spec, "in_box": in_box,
            "poly_rail_breaks": poly_kq_bad,
            "poly_eta_above_1": poly_eta_bad,
            "bem_rail_breaks": bem_kq_bad,
            "bem_eta_above_ideal": bem_eta_bad,
            "bem_kt_non_monotone": bem_nonmono,
        })
    out["summary"] = counts
    return out


# ---------------------------------------------------------------------------
# 4. figures
# ---------------------------------------------------------------------------
def make_figures(audit: Dict[str, object]) -> Dict[str, str]:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    os.makedirs(ART, exist_ok=True)
    paths: Dict[str, str] = {}
    ref_geom = next(c for c in audit["comparisons"] if c["geometry"]["name"] == REF_GEOM)

    # fig 1: KT / KQ / eta0 curve families, BEM vs canonical
    fig, ax = plt.subplots(1, 3, figsize=(15, 4.2))
    rows = [r for r in ref_geom["rows"]]
    Js = [r["J"] for r in rows]
    for a, (kb, kr, lab) in zip(ax, [("KT_bem", "KT_ref", r"$K_T$"),
                                     ("KQ_bem", "KQ_ref", r"$K_Q$"),
                                     ("eta_bem", "eta_ref", r"$\eta_0$")]):
        a.plot(Js, [r[kb] for r in rows], "o-", label="BEM (merged m1393)")
        a.plot(Js, [r[kr] for r in rows], "s--", label="B-series polynomial (1975)")
        a.set_xlabel("J"); a.set_ylabel(lab); a.grid(alpha=.3); a.legend(); a.set_title(f"B4-70  {lab}")
    ax[2].plot(Js, [r["eta_ideal"] for r in rows], ":", color="grey", label=r"$\eta_{ideal}$ rail")
    ax[2].legend()
    p = os.path.join(ART, "fig1_open_water_curves.png"); fig.tight_layout(); fig.savefig(p, dpi=130); plt.close(fig)
    paths["fig1"] = p

    # fig 2: error map |e_KT| over (J, P/D) at Ae/A0=0.70, Z=4
    Pd = [round(0.6 + 0.05 * i, 2) for i in range(17)]     # 0.60 .. 1.40
    Jm = [round(0.05 * i, 2) for i in range(1, 19)]
    grid = []
    for pd in Pd:
        spec = {"P_D": pd, "Ae_Ao": 0.70, "Z": 4}
        g = _geom(spec)
        grid.append([abs(_rel(open_water_point(g, J)["KT"], _canon(J, spec)["KT"])) for J in Jm])
    fig, axis = plt.subplots(figsize=(7.4, 4.6))
    im = axis.pcolormesh(Jm, Pd, grid, shading="nearest", cmap="viridis")
    cs = axis.contour(Jm, Pd, grid, levels=[3, 15], colors=["w", "r"], linewidths=[1.2, 1.8])
    axis.clabel(cs, fmt={3: "3%", 15: "15%"}, fontsize=9)
    axis.set_xlabel("J"); axis.set_ylabel("P/D"); axis.set_title(r"$|e_{K_T}|$ [%]  (Ae/A0=0.70, Z=4)")
    fig.colorbar(im, label="%"); fig.tight_layout()
    p = os.path.join(ART, "fig2_error_map.png"); fig.savefig(p, dpi=130); plt.close(fig)
    paths["fig2"] = p

    # fig 3: radial refinement + GCI
    conv = audit["convergence"]
    fig, ax = plt.subplots(1, 2, figsize=(11, 4.0))
    for Jkey, series in conv["series"].items():
        ax[0].semilogx(series["sections"], series["KT"], "o-", label=f"KT  J={Jkey}")
    ax[0].set_xlabel("radial strips"); ax[0].set_ylabel(r"$K_T$"); ax[0].grid(alpha=.3, which="both")
    ax[0].legend(fontsize=8); ax[0].set_title("Radial refinement")
    gkeys = list(conv["gci"])
    for key, style in (("KT", "o-"), ("KQ", "s--"), ("eta0", "^-.")):
        ax[1].plot(gkeys, [conv["gci"][k][key]["shipped40_rel_err_pct"] for k in gkeys], style,
                   label=f"{key} @40 strips")
        ax[1].plot(gkeys, [conv["gci"][k][key]["gci_fine_pct"] for k in gkeys], style, alpha=.45,
                   markerfacecolor="none", label=f"{key} GCI(320)")
    ax[1].axhline(3.0, color="r", ls=":", label="3% gate")
    ax[1].set_xlabel("J"); ax[1].set_ylabel("discretisation error [%]"); ax[1].grid(alpha=.3)
    ax[1].legend(fontsize=7, ncol=2); ax[1].set_title("Solution verification")
    fig.tight_layout(); p = os.path.join(ART, "fig3_convergence_gci.png"); fig.savefig(p, dpi=130); plt.close(fig)
    paths["fig3"] = p

    # fig 4: sensitivity bars
    sens = audit["sensitivity"]
    labels = ["shipped"] + list(sens["cases"])
    vals = [sens["base_e_KT"]] + [sens["cases"][k]["e_KT"] for k in sens["cases"]]
    fig, ax = plt.subplots(figsize=(10, 4.2))
    colors = ["k"] + ["tab:blue"] * 3 + ["tab:orange"] + ["tab:green"] * 3
    ax.barh(range(len(labels)), vals, color=colors)
    ax.set_yticks(range(len(labels))); ax.set_yticklabels(labels, fontsize=8)
    ax.axvline(0, color="grey"); ax.axvline(15, color="r", ls=":"); ax.axvline(-15, color="r", ls=":")
    ax.set_xlabel(r"$e_{K_T}$ vs B-series [%]   (J=0.50, B4-70)")
    ax.grid(alpha=.3, axis="x"); ax.set_title("Model-form diagnosis: which knob owns the gap")
    fig.tight_layout(); p = os.path.join(ART, "fig4_sensitivity.png"); fig.savefig(p, dpi=130); plt.close(fig)
    paths["fig4"] = p
    return paths


# ---------------------------------------------------------------------------
# 5. verdict
# ---------------------------------------------------------------------------
def verdict(audit: Dict[str, object]) -> Dict[str, object]:
    worst_kt = max(c["low_J"]["e_KT"]["max_abs"] for c in audit["comparisons"])
    mean_kt = sum(c["low_J"]["e_KT"]["mean"] for c in audit["comparisons"]) / len(audit["comparisons"])
    worst_kq = max(c["low_J"]["e_KQ"]["max_abs"] for c in audit["comparisons"])
    worst_eta = max(c["low_J"]["e_eta"]["max_abs"] for c in audit["comparisons"])
    gci_vals = [abs(g["KT"]["shipped40_rel_err_pct"]) for g in audit["convergence"]["gci"].values()
                if g["KT"]["shipped40_rel_err_pct"] == g["KT"]["shipped40_rel_err_pct"]]
    gci_kt = max(gci_vals) if gci_vals else float("nan")
    model_form = {
        "KT": worst_kt, "KQ": worst_kq, "eta0": worst_eta,
    }
    # ASME V&V 20 style ratio: model-form error vs numerical uncertainty
    ratio = {k: (model_form[k] / gci_kt if gci_kt > 0 else float("inf")) for k in model_form}
    return {
        "worst_low_J_abs_pct": model_form,
        "mean_low_J_KT_pct": mean_kt,
        "max_discretisation_KT_pct": gci_kt,
        "validation_to_numerical_ratio": ratio,
        "kt_within_15": bool(worst_kt <= ACCEPT_PCT),
        "kt_within_3": bool(worst_kt <= TARGET_PCT),
        "kq_within_15": bool(worst_kq <= ACCEPT_PCT),
        "eta_within_15": bool(worst_eta <= ACCEPT_PCT),
        "dominant": "model-form" if ratio["KT"] > 3.0 else "numerical",
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--report", action="store_true", help="also emit the markdown report + payload")
    args = ap.parse_args()

    os.makedirs(ART, exist_ok=True)
    audit: Dict[str, object] = {
        "mission": 1665, "repo": "TensorPlatform",
        "model": "backend/services/propeller_open_water.py (m1393 BEM)",
        "reference": "tools/verify/propeller_open_water/reference_bseries.py (Oosterveld & van Oossanen 1975, 39/47 terms)",
        "J_grid": J_GRID, "geometries": GEOMETRIES,
        "accept_pct": ACCEPT_PCT, "target_pct": TARGET_PCT,
    }

    audit["comparisons"] = [compare_geometry(spec) for spec in GEOMETRIES]
    audit["convergence"] = convergence_study(
        next(s for s in GEOMETRIES if s["name"] == REF_GEOM), (0.30, 0.50, 0.70))
    audit["sensitivity"] = sensitivity(next(s for s in GEOMETRIES if s["name"] == REF_GEOM), 0.50)
    audit["applicability"] = applicability_probe()
    audit["verdict"] = verdict(audit)

    figs = make_figures(audit)
    audit["figures"] = figs

    out = os.path.join(ART, "audit.json")
    with open(out, "w") as fh:
        json.dump(audit, fh, indent=1, default=float)
    print(json.dumps(audit["verdict"], indent=1))
    print(f"wrote {out}")
    for k, v in figs.items():
        print(f"  {k}: {os.path.relpath(v, ROOT)}")

    if args.report:
        import make_report_m1665
        make_report_m1665.emit(audit, ROOT)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
>>>>>>> refs/remotes/origin/agent/m1665
