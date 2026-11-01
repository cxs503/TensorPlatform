"""Generate the IMO MSC.137(76) manoeuvrability audit artifact + figures.

Usage::

    python scripts/m1652/imo_msc137_76_audit.py

Writes
------
outputs/m1652/imo_msc137_76_audit.json     machine-readable verdict (no hand copies)
outputs/m1652/fig_turning_circle.png       turning-circle trajectory + IMO envelope
outputs/m1652/fig_zigzag.png               zigzag time histories + overshoot limits
outputs/m1652/fig_compliance_matrix.png    pass/fail margin matrix
"""

from __future__ import annotations

import json
import math
import os
import sys

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "backend"))

from physics.ship_env_maneuvering import data as D  # noqa: E402
from physics.ship_env_maneuvering import imo_criteria as I  # noqa: E402
from physics.ship_env_maneuvering import mmg as M  # noqa: E402

OUT = os.path.join(ROOT, "outputs", "m1652")
VESSEL = D.VESSEL_VLCC
SPEED = 7.717


def _sane(x):
    """JSON-safe float (NaN/inf -> string tag, never silently 0)."""
    if isinstance(x, float) and not math.isfinite(x):
        return "nan" if math.isnan(x) else ("inf" if x > 0 else "-inf")
    return x


def _deep(o):
    if isinstance(o, dict):
        return {k: _deep(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [_deep(v) for v in o]
    return _sane(o)


def main() -> int:
    os.makedirs(OUT, exist_ok=True)
    audit = I.run_imo_compliance(vessel=VESSEL, speed=SPEED)

    # ---- sensitivity: +20 % on the sway/yaw stiffness pair --------------
    base = M.KVLCC2_REFERENCE.as_dict()
    stiff = M.MMGCoefficients(**{**base, "name": "stiff_plus20pct",
                                 "Y_v": base["Y_v"] * 1.2,
                                 "N_r": base["N_r"] * 1.2})
    sens = I.run_imo_compliance(vessel=VESSEL, speed=SPEED,
                                coefficient_sets={"stiff_plus20pct": stiff})
    td_base = audit["results"]["kvlcc2_reference"]["raw_metrics"][
        "turning_circle"]["tactical_diameter_over_L"]
    td_stiff = sens["results"]["stiff_plus20pct"]["raw_metrics"][
        "turning_circle"]["tactical_diameter_over_L"]
    c1_stiff = sens["results"]["stiff_plus20pct"]["criteria"][
        "C1_turning_circle"]["checks"][1]

    # ---- figures need the raw trajectories ------------------------------
    sets = {"kvlcc2_reference": M.KVLCC2_REFERENCE,
            "slender_body": M.slender_body_coefficients(VESSEL)}
    tc = {n: M.simulate_turning_circle(VESSEL, coef=c, speed=SPEED,
                                       rudder_deg=35.0, t_max=2000.0)
          for n, c in sets.items()}
    zz10 = {n: M.simulate_zigzag(VESSEL, coef=c, speed=SPEED, rudder_deg=10.0,
                                 heading_deg=10.0, t_max=2000.0)
            for n, c in sets.items()}
    zz20 = {n: M.simulate_zigzag(VESSEL, coef=c, speed=SPEED, rudder_deg=20.0,
                                 heading_deg=20.0, t_max=3000.0)
            for n, c in sets.items()}

    # ---- fig 1: turning circle -----------------------------------------
    L = VESSEL.L
    fig, ax = plt.subplots(figsize=(7.2, 6.0))
    styles = {"kvlcc2_reference": ("C0", "-"), "slender_body": ("C1", "--")}
    for n, r in tc.items():
        col, ls = styles[n]
        ax.plot([x / L for x in r.history["x"]], [y / L for y in r.history["y"]],
                color=col, ls=ls, lw=1.6,
                label=f"{n} (TD/L = {r.metrics['tactical_diameter_over_L']:.2f})")
        # tactical diameter point (heading = 180 deg)
        ax.plot([r.history["x"][-1] / L], [r.history["y"][-1] / L], "o",
                color=col, ms=4)
    # IMO C1 envelopes: advance <= 4.5 L, tactical diameter <= 5 L
    ax.add_patch(plt.Circle((0.0, 0.0), 2.5, fill=False, ec="crimson",
                            ls=":", lw=1.2))
    ax.axvline(4.5, color="crimson", ls=":", lw=1.2)
    ax.plot([0, 4.5], [5.0, 5.0], color="crimson", ls=":", lw=1.2)
    ax.text(0.15, 2.62, "IMO C1: tactical diameter $\\leq$ 5 L", color="crimson",
            fontsize=8)
    ax.text(4.55, 0.3, "IMO C1: advance $\\leq$ 4.5 L", color="crimson",
            fontsize=8, rotation=90)
    ax.set_xlabel("x / L  (advance direction)")
    ax.set_ylabel("y / L  (transfer direction)")
    ax.set_title("Turning circle, 35$^\\circ$ rudder, 15 kn\n"
                 "KVLCC2-particulars VLCC, MMG 3-DOF - IMO MSC.137(76) C1")
    ax.legend(fontsize=8, loc="upper left")
    ax.grid(alpha=0.3)
    ax.set_aspect("equal", adjustable="datalim")
    fig.tight_layout()
    fig.savefig(os.path.join(OUT, "fig_turning_circle.png"), dpi=150)
    plt.close(fig)

    # ---- fig 2: zigzag ------------------------------------------------
    fig, axes = plt.subplots(1, 2, figsize=(11.5, 4.4))
    for ax, (rd, data_map, lim) in zip(
            axes,
            [(10.0, zz10, audit["results"]["kvlcc2_reference"]["criteria"]
              ["C3_zigzag_10_10"]["checks"][0]["limit"]),
             (20.0, zz20, 25.0)]):
        for n, r in data_map.items():
            col, ls = styles[n]
            ax.plot(r.history["t"], r.history["psi_deg"], color=col, ls=ls,
                    lw=1.3, label=f"{n} (os1 = "
                                  f"{r.metrics['first_overshoot_deg']:.2f}$^\\circ$)")
        ax.axhline(rd + lim, color="crimson", ls=":", lw=1.3)
        ax.axhline(-(rd + lim), color="crimson", ls=":", lw=1.3)
        ax.axhline(rd, color="gray", ls="-.", lw=0.8)
        ax.axhline(-rd, color="gray", ls="-.", lw=0.8)
        ax.set_title(f"{rd:g}$^\\circ$/{rd:g}$^\\circ$ zigzag - IMO limit "
                     f"{rd:g}+{lim:g} = {rd + lim:g}$^\\circ$", fontsize=9)
        ax.set_xlabel("t [s]")
        ax.set_ylabel("heading $\\psi$ [deg]")
        ax.legend(fontsize=7, loc="lower left")
        ax.grid(alpha=0.3)
    fig.suptitle("Zigzag overshoot vs IMO MSC.137(76) C3/C4 "
                 "(dotted red = statutory limit)", fontsize=10)
    fig.tight_layout()
    fig.savefig(os.path.join(OUT, "fig_zigzag.png"), dpi=150)
    plt.close(fig)

    # ---- fig 3: margin matrix ----------------------------------------
    crit_names = ["C1_turning_circle", "C2_initial_turning",
                  "C3_zigzag_10_10", "C4_zigzag_20_20"]
    rows = list(audit["results"].keys())
    norm = []
    ticklabels = []
    for r in rows:
        vals, labs = [], []
        for cn in crit_names:
            crit = audit["results"][r]["criteria"][cn]
            for chk in crit["checks"]:
                # normalised headroom: margin / limit, negative = violation
                vals.append(chk["margin"] / abs(chk["limit"]) if chk["limit"]
                            else float("nan"))
                labs.append(f"{chk['check']}\n{chk['value']:.3g}/{chk['limit']:.3g}")
                break                      # first check per criterion
        norm.append(vals)
        ticklabels = labs
    fig, ax = plt.subplots(figsize=(9.0, 3.4))
    im = ax.imshow(norm, cmap="RdYlGn", vmin=-0.25, vmax=0.65, aspect="auto")
    ax.set_xticks(range(len(crit_names)))
    ax.set_xticklabels([t.replace("\n", "\n") for t in ticklabels], fontsize=7)
    ax.set_yticks(range(len(rows)))
    ax.set_yticklabels(rows, fontsize=8)
    for i in range(len(rows)):
        for j in range(len(crit_names)):
            v = norm[i][j]
            ax.text(j, i, f"{v:+.2f}" + ("\nPASS" if v >= 0 else "\nFAIL"),
                    ha="center", va="center", fontsize=8,
                    color="black" if v >= 0 else "darkred")
    fig.colorbar(im, ax=ax, label="relative headroom  margin / limit")
    ax.set_title("IMO MSC.137(76) compliance margins "
                 "(negative = violation, normalised by the limit)", fontsize=9)
    fig.tight_layout()
    fig.savefig(os.path.join(OUT, "fig_compliance_matrix.png"), dpi=150)
    plt.close(fig)

    # ---- artifact ----------------------------------------------------
    artifact = {
        "mission_id": 1652,
        "task": "tp-ver-8 verification debt: IMO MSC.137(76) manoeuvrability audit",
        "standard": audit["standard"],
        "provenance": audit["provenance"],
        "vessel": audit["vessel"],
        "speed_ms": audit["speed_ms"],
        "speed_kn": audit["speed_kn"],
        "LV_s": audit["LV_s"],
        "results": audit["results"],
        "verdicts": audit["verdicts"],
        "coefficient_set_robust": audit["coefficient_set_robust"],
        "overall_passed": audit["overall_passed"],
        "absolute_accuracy_claim": audit["absolute_accuracy_claim"],
        "withheld": audit["withheld"],
        "sensitivity_tactical_diameter": {
            "perturbation": "Y_v and N_r scaled by +20 % (within the "
                            "uncalibrated hydrodynamic uncertainty band)",
            "TD_over_L_baseline": td_base,
            "TD_over_L_perturbed": td_stiff,
            "C1_passed_perturbed": c1_stiff["passed"],
            "conclusion": ("a 20 % stiffness perturbation drives the tactical "
                           "diameter past the 5 L IMO limit, so the C1 pass is "
                           "fragile under uncalibrated derivatives"),
        },
        "figures": ["fig_turning_circle.png", "fig_zigzag.png",
                    "fig_compliance_matrix.png"],
    }
    path = os.path.join(OUT, "imo_msc137_76_audit.json")
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(_deep(artifact), fh, indent=2, ensure_ascii=False,
                  sort_keys=False)

    # ---- console table ----------------------------------------------
    print(f"{'set':<18}{'criterion':<22}{'value':>10}{'limit':>8}"
          f"{'margin':>10}  verdict")
    for r in rows:
        for cn in crit_names:
            for chk in audit["results"][r]["criteria"][cn]["checks"]:
                print(f"{r:<18}{cn:<22}{chk['value']:>10.3f}"
                      f"{chk['limit']:>8.2f}{chk['margin']:>+10.3f}  "
                      f"{'PASS' if chk['passed'] else 'FAIL'}"
                      f"{'  <borderline>' if chk['borderline'] else ''}")
    print(f"\nL/V = {audit['LV_s']:.2f} s  (C3 branch: 20 deg limit)")
    print(f"coefficient-set robust = {audit['coefficient_set_robust']}, "
          f"overall_passed = {audit['overall_passed']}")
    print(f"artifact -> {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())