#!/usr/bin/env python3
"""Open-water benchmark artifacts (mission 1393).

Produces, under ``outputs/propeller_open_water/``:

* ``open_water_curves.json``   - KT, KQ, eta0, eta_ideal vs J for P/D = 0.8/1.0/1.2
* ``legacy_vs_bem.json``       - the retired surrogate sampled on the same J grid
* ``sensitivity.json``         - section-drag sensitivity of the delivered numbers
* ``invariants.json``          - analytic-invariant residuals (machine evidence)
* ``fig_open_water.png``       - open-water diagram (KT, 10KQ, eta0), BEM vs legacy
* ``fig_efficiency_bound.png`` - eta0 / eta_ideal plus the analytic bound
* ``fig_zero_thrust.png``      - J0 and eta_max vs pitch ratio

Every number in the report is read back from these files (no hand-copied digits).
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

HERE = Path(__file__).resolve()
REPO = HERE.parents[2]
sys.path.insert(0, str(REPO / "backend"))

import services.propeller_open_water as powm  # noqa: E402
from services.propeller_open_water import (  # noqa: E402
    PropellerGeometry,
    best_efficiency_point,
    open_water_point,
    solve_zero_thrust_advance_ratio,
)

OUT = REPO / "outputs" / "propeller_open_water"
OUT.mkdir(parents=True, exist_ok=True)

PITCH_RATIOS = (0.8, 1.0, 1.2)
SECTIONS = 16


def legacy(J: float) -> dict:
    """Retired surrogate (tools/batch_scan before mission 1393)."""
    KT = max(0.05, 0.5 - 0.3 * J)
    KQ = max(0.01, 0.05 + 0.02 * J)
    eta = (J * KT) / (2 * 3.14159 * KQ) if KQ > 0 else 0.0
    return {"J": J, "KT": KT, "KQ": KQ, "eta0": min(0.85, max(0.3, eta))}


def main() -> int:
    curves: dict = {}
    legacy_curves: dict = {}
    best: dict = {}

    for pd in PITCH_RATIOS:
        g = PropellerGeometry(pitch_ratio=pd, sections=SECTIONS)
        j0 = solve_zero_thrust_advance_ratio(g)
        js = [round(k * j0 / 12.0, 6) for k in range(13)]
        curves[f"P/D={pd:.1f}"] = {
            "geometry": g.as_dict(),
            "J0": j0,
            "points": [{k: v for k, v in open_water_point(g, J).items()} for J in js],
        }
        legacy_curves[f"P/D={pd:.1f}"] = [legacy(J) for J in js]
        best[f"P/D={pd:.1f}"] = best_efficiency_point(g, n_scan=17)

    # section-drag sensitivity on the delivered numbers (documented, not tuned)
    sens = []
    base_cd = powm.CD0
    for cd0 in (0.008, 0.010, 0.012, 0.015, 0.020):
        powm.CD0 = cd0
        g = PropellerGeometry(pitch_ratio=1.0, sections=SECTIONS)
        p0 = open_water_point(g, 0.0)
        p6 = open_water_point(g, 0.6)
        b = best_efficiency_point(g, n_scan=13)
        sens.append({
            "CD0": cd0,
            "KT_J0": p0["KT"],
            "KQ_J0": p0["KQ"],
            "KT_J06": p6["KT"],
            "KQ_J06": p6["KQ"],
            "eta_J06": p6["eta0"],
            "eta_max": b["eta0"],
            "J_opt": b["J"],
            "J0_zero_thrust": b["J0"],
        })
    powm.CD0 = base_cd

    # invariant residuals (machine evidence, all should be ~0 / satisfied)
    g = PropellerGeometry(pitch_ratio=1.0, sections=SECTIONS)
    inv = {
        "blade_area_error_rel": abs(
            sum(g.chord_m(g.hub_ratio + (1 - g.hub_ratio) * (k + 0.5) / 200) for k in range(200))
            * (1 - g.hub_ratio) / 200 * g.radius_m * g.blade_count / g.blade_area_m2 - 1.0
        ),
        "momentum_bound_violations": sum(
            1 for J in (0.2, 0.4, 0.6, 0.8)
            if not (0.0 < (p := open_water_point(g, J))["eta0"] < p["eta_ideal"] < 1.0)
        ),
        "froude_scaling_rel_diff": None,
        "kt_at_J0": None,
    }
    small = PropellerGeometry(diameter_m=0.25, pitch_ratio=1.0, sections=SECTIONS)
    big = PropellerGeometry(diameter_m=5.0, pitch_ratio=1.0, sections=SECTIONS)
    a = open_water_point(small, 0.6, n_rps=20.0)
    b = open_water_point(big, 0.6, n_rps=1.0)
    inv["froude_scaling_rel_diff"] = max(
        abs(a[k] - b[k]) / max(abs(b[k]), 1e-12) for k in ("KT", "KQ", "eta0")
    )
    j0 = solve_zero_thrust_advance_ratio(g)
    inv["kt_at_J0"] = open_water_point(g, j0)["KT"]
    inv["J0"] = j0

    (OUT / "open_water_curves.json").write_text(json.dumps(curves, indent=1))
    (OUT / "legacy_vs_bem.json").write_text(json.dumps(legacy_curves, indent=1))
    (OUT / "best_points.json").write_text(json.dumps(best, indent=1))
    (OUT / "sensitivity.json").write_text(json.dumps(sens, indent=1))
    (OUT / "invariants.json").write_text(json.dumps(inv, indent=1))

    # ---------------- figures ----------------
    colors = {0.8: "tab:blue", 1.0: "tab:red", 1.2: "tab:green"}
    fig, axes = plt.subplots(1, 3, figsize=(15, 4.4))
    for pd in PITCH_RATIOS:
        key = f"P/D={pd:.1f}"
        pts = curves[key]["points"]
        J = [p["J"] for p in pts]
        axes[0].plot(J, [p["KT"] for p in pts], "-o", ms=3, color=colors[pd], label=key)
        axes[1].plot(J, [10 * p["KQ"] for p in pts], "-o", ms=3, color=colors[pd], label=key)
        axes[2].plot(J, [p["eta0"] for p in pts], "-o", ms=3, color=colors[pd], label=key)
        lj = [p["J"] for p in legacy_curves[key]]
        axes[0].plot(lj, [p["KT"] for p in legacy_curves[key]], "--", color=colors[pd], alpha=0.45)
        axes[1].plot(lj, [10 * p["KQ"] for p in legacy_curves[key]], "--", color=colors[pd], alpha=0.45)
        axes[2].plot(lj, [p["eta0"] for p in legacy_curves[key]], "--", color=colors[pd], alpha=0.45)
        axes[0].axvline(curves[key]["J0"], color=colors[pd], lw=0.8, ls=":")
    axes[0].set_xlabel("J = Va/(nD)")
    axes[0].set_ylabel("K_T")
    axes[0].set_title("Thrust coefficient (solid: BEM, dashed: retired surrogate)")
    axes[1].set_xlabel("J = Va/(nD)")
    axes[1].set_ylabel("10 K_Q")
    axes[1].set_title("Torque coefficient - surrogate K_Q rises with J (unphysical)")
    axes[2].set_xlabel("J = Va/(nD)")
    axes[2].set_ylabel(r"$\eta_0$")
    axes[2].set_title("Open-water efficiency (surrogate clipped to [0.3, 0.85])")
    for ax in axes:
        ax.grid(alpha=0.3)
        ax.legend(fontsize=8)
    fig.tight_layout()
    fig.savefig(OUT / "fig_open_water.png", dpi=150)
    plt.close(fig)

    fig, ax = plt.subplots(figsize=(6.4, 4.2))
    for pd in PITCH_RATIOS:
        pts = curves[f"P/D={pd:.1f}"]["points"]
        J = [p["J"] for p in pts]
        ax.plot(J, [p["eta_ideal"] for p in pts], ":", color=colors[pd], label=f"ideal bound P/D={pd}")
        ax.plot(J, [p["eta0"] / p["eta_ideal"] if p["eta_ideal"] > 0 else 0 for p in pts],
                "-o", ms=3, color=colors[pd], label=f"eta0/eta_ideal P/D={pd}")
    ax.axhline(1.0, color="k", lw=0.8, ls="--")
    ax.set_xlabel("J = Va/(nD)")
    ax.set_ylabel("efficiency / ratio")
    ax.set_title("Actuator-disc bound and realised fraction (must stay below 1)")
    ax.grid(alpha=0.3)
    ax.legend(fontsize=7)
    fig.tight_layout()
    fig.savefig(OUT / "fig_efficiency_bound.png", dpi=150)
    plt.close(fig)

    fig, ax = plt.subplots(figsize=(6.0, 4.0))
    pds = [round(pd, 1) for pd in PITCH_RATIOS]
    ax.plot(pds, [curves[f"P/D={pd:.1f}"]["J0"] for pd in pds], "-o", label="J0 (KT = 0)")
    ax.plot(pds, [best[f"P/D={pd:.1f}"]["J"] for pd in pds], "-s", label="J_opt (eta max)")
    ax.set_xlabel("P/D")
    ax.set_ylabel("advance ratio")
    ax.set_title("Zero-thrust and best-efficiency advance ratios vs pitch")
    ax.grid(alpha=0.3)
    ax.legend(fontsize=8)
    fig.tight_layout()
    fig.savefig(OUT / "fig_zero_thrust.png", dpi=150)
    plt.close(fig)

    print(json.dumps({"out": str(OUT), "files": sorted(os.listdir(OUT))}, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())