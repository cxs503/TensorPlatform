#!/usr/bin/env python3
"""Acceptance run for campaign node W3-N11 (polar voyage / CII / escort).

Runs criteria (a)-(f) of the node, writes

  artifacts/polar/acceptance_report.json      all numbers, programmatically injected
  artifacts/polar/figures/*.png               the report figures

and prints a short summary.  Every number in the report is read from these
artefacts -- nothing is typed by hand.

Usage
-----
    cd backend && python3 ../tools/polar/run_acceptance.py
or
    python3 tools/polar/run_acceptance.py --backend backend
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List

REPO = Path(__file__).resolve().parents[2]


def _bootstrap(backend: Path) -> None:
    backend = backend.resolve()
    sys.path.insert(0, str(backend))
    os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")
    os.environ.setdefault("ENVIRONMENT", "test")


def run_pytest(backend: Path) -> Dict[str, Any]:
    """Run the polar test suite with --noconftest and parse the result."""
    cmd = [
        sys.executable, "-m", "pytest", "tests/polar", "--noconftest",
        "-q", "-p", "no:cacheprovider", "--no-header",
    ]
    env = dict(os.environ, DATABASE_URL="sqlite:///:memory:", ENVIRONMENT="test")
    proc = subprocess.run(cmd, cwd=str(backend), capture_output=True, text=True, env=env)
    out = proc.stdout + proc.stderr
    passed = failed = 0
    m = re.search(r"(\d+) passed", out)
    if m:
        passed = int(m.group(1))
    m = re.search(r"(\d+) failed", out)
    if m:
        failed = int(m.group(1))
    if passed == 0 and failed == 0:
        # -q with all-green prints only dots; count them as a fallback
        passed = out.count(".")
    return {
        "criterion": "f",
        "command": " ".join(cmd),
        "returncode": proc.returncode,
        "passed": passed,
        "failed": failed,
        "threshold_min_passed": 15,
        "pass": proc.returncode == 0 and failed == 0 and passed >= 15,
        "tail": out.strip().splitlines()[-3:] if out.strip() else [],
    }


def build_figures(backend: Path, outdir: Path) -> List[Dict[str, str]]:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import numpy as np

    from services.polar import cii as cii_mod
    from services.polar import resistance as res
    from services.polar.channel import (
        ANCHOR_CONCENTRATION,
        HOLDOUT_CONCENTRATIONS,
        calibrate_k,
        channel_resistance,
        channel_resistance_reference,
    )
    from services.polar.constants import KNOT
    from services.polar.convoy import ConvoyConfig, convoy_energy_curve, optimize_spacing
    from services.polar.ice_field import IceField
    from services.polar.optimizer import (
        DEFAULT_DEADLINE_H,
        DEFAULT_ROUTE_END_KM,
        DEFAULT_ROUTE_START_KM,
        _baseline_family,
        best_experience_route,
    )
    from services.polar.route import plan_voyage
    from services.polar.ships import get_ship

    outdir.mkdir(parents=True, exist_ok=True)
    figs: List[Dict[str, str]] = []

    def _save(fig, name: str, caption: str) -> None:
        path = outdir / name
        fig.savefig(path, dpi=150, bbox_inches="tight")
        plt.close(fig)
        figs.append({"name": name, "path": str(path), "caption": caption})

    # --- Fig 1: ice field + routes ----------------------------------------
    field = IceField.generate_scenario()
    ship = get_ship("pc6_bulker")
    plan = plan_voyage(
        field, ship, DEFAULT_ROUTE_START_KM, DEFAULT_ROUTE_END_KM,
        deadline_h=DEFAULT_DEADLINE_H,
    )
    baselines = _baseline_family(
        field, ship, DEFAULT_ROUTE_START_KM, DEFAULT_ROUTE_END_KM,
        deadline_h=DEFAULT_DEADLINE_H,
    )
    pilot = next(b for b in baselines if b.get("feasible") and b["label"] == "lead_aware_pilot")
    best = best_experience_route(baselines)

    fig, ax = plt.subplots(figsize=(9.5, 5.8))
    extent = field.extent_km
    im = ax.imshow(
        field.h, origin="lower", cmap="Blues",
        extent=[extent[0], extent[1], extent[2], extent[3]], aspect="equal",
    )
    cb = fig.colorbar(im, ax=ax, pad=0.02)
    cb.set_label("level-ice thickness h [m]")
    ox = [p[0] for p in plan["points_km"]]
    oy = [p[1] for p in plan["points_km"]]
    ax.plot(ox, oy, "-", lw=2.4, color="crimson", label="optimised route (A* + speed)")
    px = [p[0] for p in pilot["points_km"]]
    py = [p[1] for p in pilot["points_km"]]
    ax.plot(px, py, "--", lw=1.6, color="darkorange", label="lead-aware pilot route")
    bx = [p[0] for p in best["points_km"]]
    by = [p[1] for p in best["points_km"]]
    ax.plot(bx, by, ":", lw=1.6, color="black", label="best rule-based band route")
    ax.plot(*DEFAULT_ROUTE_START_KM, "o", color="green", ms=8, label="departure")
    ax.plot(*DEFAULT_ROUTE_END_KM, "s", color="purple", ms=8, label="destination")
    ax.set_xlabel("x [km]")
    ax.set_ylabel("y [km] (0 = ice edge, 260 = coast)")
    ax.set_title("Polar corridor ice field and the competing routes")
    ax.legend(loc="lower left", fontsize=8)
    _save(fig, "fig1_ice_field_routes.png",
          "Synthetic NSR corridor ice field (h) with the energy-optimal route, "
          "the lead-aware pilot route and the best rule-based band route.")

    # --- Fig 2: escort spacing E(s) ---------------------------------------
    cfg = ConvoyConfig(
        escort=get_ship("icebreaker_escort"), follower=get_ship("handysize_bulker"),
        followers=2, h_level_m=0.9, h_brash_m=0.8, concentration=0.45, speed_kn=5.0,
    )
    opt = optimize_spacing(cfg)
    curve = opt["curve"]
    fig, ax1 = plt.subplots(figsize=(7.6, 4.8))
    s = np.array([c["s_m"] for c in curve])
    e = np.array([c["energy_mj_per_km"] for c in curve])
    ax1.semilogx(s, e, "-", color="navy", lw=2, label="convoy energy E(s)")
    ax1.axvline(opt["spacing_opt_m"], color="crimson", ls="--",
               label=f"optimum = {opt['spacing_opt_m']:.0f} m")
    ax1.plot(opt["spacing_opt_m"], opt["energy_opt_mj_per_km"], "o", color="crimson", ms=7)
    ax1.set_xlabel("escort spacing s [m]")
    ax1.set_ylabel("convoy energy [MJ/km]")
    ax2 = ax1.twinx()
    ax2.semilogx(s, [c["proximity_n"] / 1e3 for c in curve], ":", color="darkgreen",
                 label="proximity penalty [kN]")
    ax2.semilogx(s, [c["refrozen_mm"] for c in curve], ":", color="darkorange",
                 label="refrozen thickness [mm]")
    ax2.set_ylabel("proximity [kN] / refrozen [mm]")
    h1, l1 = ax1.get_legend_handles_labels()
    h2, l2 = ax2.get_legend_handles_labels()
    ax1.legend(h1 + h2, l1 + l2, fontsize=8, loc="upper center")
    ax1.set_title("Escort spacing: E(s) has an interior minimum")
    _save(fig, "fig2_escort_spacing.png",
          "Convoy energy per km versus escort spacing (log scale) with the two "
          "competing mechanisms (wake proximity penalty, Stefan refreezing).")

    # --- Fig 3: level-ice resistance, model vs reference -------------------
    ship_b = get_ship("handysize_bulker")
    fig, ax = plt.subplots(figsize=(7.6, 4.8))
    speeds = np.linspace(1.0, 8.0, 15)
    colors = plt.cm.viridis(np.linspace(0.05, 0.9, len(res.ICE_CONDITIONS)))
    for color, cond in zip(colors, res.ICE_CONDITIONS):
        model = [res.level_ice_resistance(ship_b, v * KNOT, cond["h_m"], cond["sigma_mpa"]).total_n / 1e3
                 for v in speeds]
        ref = [res.holtrop_ice_corrected_reference(ship_b, v * KNOT, cond["h_m"],
                                                   cond["sigma_mpa"])["reference_total_n"] / 1e3
               for v in speeds]
        ax.plot(speeds, model, "-", color=color, lw=2, label=f"{cond['label']} (model)")
        ax.plot(speeds, ref, "o", color=color, ms=3, mfc="none", label="_nolegend_")
    ax.set_xlabel("ship speed [kn]")
    ax.set_ylabel("total resistance [kN]")
    ax.set_title("Level ice (handysize bulker): grid model (lines) vs Holtrop ice-corrected\n"
                 "reference (open circles), 5 ice conditions")
    ax.legend(fontsize=7)
    _save(fig, "fig3_level_ice_resistance.png",
          "Total resistance vs speed for the five ice conditions; solid lines are the "
          "grid-integrated TP-POLAR-1 model, open circles the closed-form reference.")

    # --- Fig 4: CII attained vs required, three ship types -----------------
    report_d = cii_mod.verify_cii_ship_types(
        ["handysize_bulker", "aframax_tanker", "container_8000teu"], year=2026
    )
    fig, ax = plt.subplots(figsize=(7.6, 4.8))
    labels = [f"{r['ship']}\n({r['rating']})" for r in report_d["rows"]]
    x = np.arange(len(labels))
    att = [r["attained_cii"] for r in report_d["rows"]]
    req = [r["required_cii"] for r in report_d["rows"]]
    ref_line = [r["cii_reference"] for r in report_d["rows"]]
    ax.bar(x - 0.27, att, width=0.27, label="attained CII", color="steelblue")
    ax.bar(x, req, width=0.27, label="required CII (2026)", color="seagreen")
    ax.bar(x + 0.27, ref_line, width=0.27, label="2019 reference line", color="lightgrey")
    for xi, a, q in zip(x, att, req):
        ax.text(xi - 0.27, a * 1.01, f"{a:.2f}", ha="center", fontsize=7)
        ax.text(xi, q * 1.01, f"{q:.2f}", ha="center", fontsize=7)
    ax.set_xticks(x)
    ax.set_xticklabels(labels, fontsize=8)
    ax.set_ylabel("CII  [g CO2 /(dwt nm)]")
    ax.set_title("Attained vs required CII (IMO MEPC.353(78)/354(78)), representative voyage")
    ax.legend(fontsize=8)
    _save(fig, "fig4_cii_three_ship_types.png",
          "Attained CII, required CII for 2026 and the 2019 reference line for three "
          "ship types; the letter in the tick label is the assigned rating.")

    # --- Fig 5: channel resistance vs concentration -----------------------
    fig, ax = plt.subplots(figsize=(7.6, 4.8))
    cs = np.linspace(0.05, 0.7, 40)
    k_cal = calibrate_k(ship_b, 6.0 * KNOT, 0.8)
    model = [channel_resistance(ship_b, 6.0 * KNOT, 0.8, c)["total_n"] / 1e3 for c in cs]
    refv = [channel_resistance_reference(ship_b, 6.0 * KNOT, 0.8, c, k_cal=k_cal)["total_n"] / 1e3
            for c in cs]
    ax.plot(cs, model, "-", lw=2.2, color="navy", label="brash-mixture model")
    ax.plot(cs, refv, "--", lw=1.6, color="darkorange", label="calibrated Riska-type reference")
    for c, style in [(ANCHOR_CONCENTRATION, "o"), *[(c, "s") for c in HOLDOUT_CONCENTRATIONS]]:
        ax.plot(c, channel_resistance(ship_b, 6.0 * KNOT, 0.8, c)["total_n"] / 1e3,
                style, color="crimson", ms=8,
                label="calibration anchor" if style == "o" else "hold-out check")
    ax.set_xlabel("brash-ice concentration c [-]")
    ax.set_ylabel("channel resistance [kN]")
    ax.set_title("Brash channel resistance: model vs calibrated Riska-type reference\n"
                 f"(h_b = 0.80 m, 6 kn, k_cal = {k_cal:.3f})")
    ax.legend(fontsize=8)
    _save(fig, "fig5_channel_resistance.png",
          "Brash-channel resistance versus concentration; the single calibration "
          "constant is fitted at c = 0.40, the other two concentrations are hold-out.")
    return figs


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--backend", default=str(REPO / "backend"))
    ap.add_argument("--outdir", default=str(REPO / "artifacts" / "polar"))
    ap.add_argument("--skip-figures", action="store_true")
    args = ap.parse_args()

    backend = Path(args.backend)
    outdir = Path(args.outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    _bootstrap(backend)

    from services.polar.optimizer import full_acceptance_report

    report = full_acceptance_report(ship_key="handysize_bulker", year=2026)
    report["criterion_f_pytest"] = run_pytest(backend)
    report["all_pass"] = bool(
        report["all_pass"] and report["criterion_f_pytest"]["pass"]
    )

    if not args.skip_figures:
        figs = build_figures(backend, outdir / "figures")
        report["figures"] = [
            {"name": f["name"], "path": str(Path(f["path"]).relative_to(REPO)),
             "caption": f["caption"]}
            for f in figs
        ]

    out_path = outdir / "acceptance_report.json"
    out_path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")

    summary = {
        "node": report["node"],
        "all_pass": report["all_pass"],
        "a_ice_resistance": {
            "max_abs_rel_error_pct": report["criteria"]["a_ice_resistance"]["max_abs_rel_error_pct"],
            "pass": report["criteria"]["a_ice_resistance"]["pass"],
        },
        "b_channel": {
            "max_abs_rel_error_pct": report["criteria"]["b_channel_resistance"]["max_abs_rel_error_pct"],
            "pass": report["criteria"]["b_channel_resistance"]["pass"],
        },
        "c_escort": {
            "spacing_opt_m": report["criteria"]["c_escort_spacing"]["spacing_opt_m"],
            "pass": report["criteria"]["c_escort_spacing"]["pass"],
        },
        "d_cii": {
            "max_two_path_rel_diff_pct": report["criteria"]["d_cii"]["max_two_path_rel_diff_pct"],
            "pass": report["criteria"]["d_cii"]["pass"],
        },
        "e_route": {
            "saving_vs_best_experience_pct": report["criteria"]["e_route_optimisation"]["saving_vs_best_experience_pct"],
            "saving_vs_best_rule_based_pct": report["criteria"]["e_route_optimisation"]["saving_vs_best_rule_based_pct"],
            "pass": report["criteria"]["e_route_optimisation"]["pass"],
        },
        "f_pytest": {
            "passed": report["criterion_f_pytest"]["passed"],
            "pass": report["criterion_f_pytest"]["pass"],
        },
        "artifact": str(out_path.relative_to(REPO)),
    }
    print(json.dumps(summary, indent=2, ensure_ascii=False))
    return 0 if report["all_pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())