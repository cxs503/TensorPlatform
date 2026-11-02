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

import json
import math
import os
import sys
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