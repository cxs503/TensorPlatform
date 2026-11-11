#!/usr/bin/env python3
"""Figure builder for the invariant-audit report (application layer).

Runs the injected plan and a few sweeps, then writes PNGs plus a JSON dump of
every number that appears in the figures, so the report can cite
machine-generated values instead of hand-copied ones.

Usage::

    python3 -m tools.invariant_audit.report_figures \
        --plan config/invariant_audit/plans/synthetic_solver_matrix.json \
        --out artifacts/invariant_audit/figures
"""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from typing import Any, Dict, List

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402  (backend must be set first)
import numpy as np  # noqa: E402

from .anchors.synthetic_models import response_diagonal, transport_grid  # noqa: E402
from .engine import load_plan_file, run_audit  # noqa: E402
from .model import InvariantState  # noqa: E402

STATE_COLOURS = {
    InvariantState.PASS: "#2e7d32",
    InvariantState.FAIL: "#c62828",
    InvariantState.WITHHELD: "#f9a825",
    InvariantState.ERROR: "#6a1b9a",
}
STATE_SCORE = {InvariantState.PASS: 2.0, InvariantState.FAIL: 1.0,
               InvariantState.WITHHELD: 0.0, InvariantState.ERROR: -1.0}


def figure_verdict_matrix(report, out: Path) -> Dict[str, Any]:
    targets = [t.target_id for t in report.targets]
    invariants: List[str] = []
    for target in report.targets:
        for inv in target.invariants:
            if inv.invariant_id not in invariants:
                invariants.append(inv.invariant_id)
    grid = np.full((len(targets), len(invariants)), np.nan)
    for i, target in enumerate(report.targets):
        for inv in target.invariants:
            grid[i, invariants.index(inv.invariant_id)] = STATE_SCORE[inv.state]

    fig, ax = plt.subplots(figsize=(9.5, 0.62 * len(targets) + 1.4))
    cmap = matplotlib.colors.ListedColormap(
        [STATE_COLOURS[InvariantState.ERROR], STATE_COLOURS[InvariantState.WITHHELD],
         STATE_COLOURS[InvariantState.FAIL], STATE_COLOURS[InvariantState.PASS]])
    bounds = [-1.5, -0.5, 0.5, 1.5, 2.5]
    norm = matplotlib.colors.BoundaryNorm(bounds, cmap.N)
    ax.imshow(np.nan_to_num(grid, nan=-1.0), cmap=cmap, norm=norm, aspect="auto")
    ax.set_xticks(range(len(invariants)))
    ax.set_xticklabels(invariants, rotation=35, ha="right", fontsize=8)
    ax.set_yticks(range(len(targets)))
    ax.set_yticklabels(targets, fontsize=8)
    for i, target in enumerate(report.targets):
        for inv in target.invariants:
            j = invariants.index(inv.invariant_id)
            ax.text(j, i, inv.state.value, ha="center", va="center", fontsize=7,
                    color="white", fontweight="bold")
    ax.set_title("Invariant audit matrix (synthetic solver set)", fontsize=10)
    ax.set_xlabel("invariant (injected by configuration)", fontsize=8)
    fig.tight_layout()
    path = out / "fig1_verdict_matrix.png"
    fig.savefig(path, dpi=160)
    plt.close(fig)
    return {"figure": str(path),
            "targets": targets, "invariants": invariants,
            "states": [[inv.state.value for inv in t.invariants] for t in report.targets],
            "counts": report.counts(), "pair_counts": report.pair_counts()}


def _transport(**overrides) -> Dict[str, Any]:
    config = dict(cells=200, length=1.0, stretch=0.4, velocity=0.2, dt=0.01, steps=50,
                  pulse_center=0.3, pulse_width=0.05, scheme="flux",
                  velocity_modulation=0.5, trace=True, emit_flux=True)
    config.update(overrides)
    return transport_grid(config)


def figure_mass_trace(out: Path) -> Dict[str, Any]:
    flux = _transport(scheme="flux")
    interp = _transport(scheme="interpolating")
    steps = range(len(flux["mass_trace"]))
    data = {
        "flux_drift_final": flux["mass_final"] / flux["mass_initial"] - 1.0,
        "interpolating_drift_final": interp["mass_final"] / interp["mass_initial"] - 1.0,
        "flux_trace": flux["mass_trace"],
        "interpolating_trace": interp["mass_trace"],
    }
    fig, ax = plt.subplots(figsize=(7.2, 4.2))
    ax.plot(list(steps), [(m / flux["mass_initial"] - 1.0) for m in flux["mass_trace"]],
            label=f"flux form (telescoping) final={data['flux_drift_final']:.2e}",
            color=STATE_COLOURS[InvariantState.PASS], lw=2)
    ax.plot(list(steps), [(m / interp["mass_initial"] - 1.0) for m in interp["mass_trace"]],
            label=f"interpolating form final={data['interpolating_drift_final']:.2e}",
            color=STATE_COLOURS[InvariantState.FAIL], lw=2)
    ax.axhline(0.0, color="black", lw=0.6)
    ax.set_xlabel("time step")
    ax.set_ylabel("relative cell-integral drift  M(t)/M(0) - 1")
    ax.set_title("Discrete mass conservation on a stretched mesh (stretch = 0.4)")
    ax.legend(fontsize=8)
    ax.grid(alpha=0.3)
    fig.tight_layout()
    path = out / "fig2_mass_drift_trace.png"
    fig.savefig(path, dpi=160)
    plt.close(fig)
    return {"figure": str(path), **{k: v for k, v in data.items() if k.endswith("final")}}


def figure_mass_refinement(out: Path) -> Dict[str, Any]:
    cells = [50, 100, 200, 400, 800]
    flux_drift, interp_drift = [], []
    for n in cells:
        flux_drift.append(abs(_transport(cells=n, scheme="flux")["mass_final"]
                              / _transport(cells=n, scheme="flux")["mass_initial"] - 1.0))
        res = _transport(cells=n, scheme="interpolating")
        interp_drift.append(abs(res["mass_final"] / res["mass_initial"] - 1.0))
    fig, ax = plt.subplots(figsize=(7.2, 4.2))
    ax.loglog(cells, [max(v, 1e-18) for v in flux_drift], "o-",
              label="flux form", color=STATE_COLOURS[InvariantState.PASS])
    ax.loglog(cells, interp_drift, "s-",
              label="interpolating form", color=STATE_COLOURS[InvariantState.FAIL])
    ax.set_xlabel("cells (mesh refinement)")
    ax.set_ylabel("|final relative mass drift|")
    ax.set_title("Mass drift under mesh refinement (stretched mesh, 50 steps)")
    ax.grid(alpha=0.3, which="both")
    ax.legend(fontsize=8)
    fig.tight_layout()
    path = out / "fig3_mass_refinement.png"
    fig.savefig(path, dpi=160)
    plt.close(fig)
    return {"figure": str(path), "cells": cells,
            "flux_drift": flux_drift, "interpolating_drift": interp_drift}


def figure_rotation_sweep(out: Path) -> Dict[str, Any]:
    angles = [0, 5, 10, 15, 30, 45, 60, 75, 89, 90]
    vector = [0.6, -0.8]
    traces = {"isotropic": [], "grid-aligned": []}
    for label, weights in (("isotropic", [1.0, 1.0]), ("grid-aligned", [1.0, 2.5])):
        for angle in angles:
            c, s = math.cos(math.radians(angle)), math.sin(math.radians(angle))
            matrix = [[c, -s], [s, c]]
            base = response_diagonal({"input_vector": vector, "weights": weights})["response"]
            rotated_input = [c * vector[0] - s * vector[1], s * vector[0] + c * vector[1]]
            moved = response_diagonal({"input_vector": rotated_input,
                                       "weights": weights})["response"]
            expected = [c * base[0] - s * base[1], s * base[0] + c * base[1]]
            residual = max(abs(a - b) for a, b in zip(expected, moved))
            scale = max(max(abs(v) for v in expected), max(abs(v) for v in moved)) or 1.0
            traces[label].append(residual / scale)
    fig, ax = plt.subplots(figsize=(7.2, 4.2))
    for label, colour in (("isotropic", STATE_COLOURS[InvariantState.PASS]),
                          ("grid-aligned", STATE_COLOURS[InvariantState.FAIL])):
        ax.semilogy(angles, [max(v, 1e-18) for v in traces[label]], "o-",
                    color=colour, label=label)
    ax.set_xlabel("rotation angle (deg)")
    ax.set_ylabel("relative covariance residual")
    ax.set_title("Rotational covariance: exact for isotropic maps, broken for axis-aligned maps")
    ax.grid(alpha=0.3, which="both")
    ax.legend(fontsize=8)
    fig.tight_layout()
    path = out / "fig4_rotation_sweep.png"
    fig.savefig(path, dpi=160)
    plt.close(fig)
    return {"figure": str(path), "angles": angles, "residuals": traces}


def figure_boost_sweep(out: Path) -> Dict[str, Any]:
    boosts = [0.01, 0.02, 0.05, 0.1, 0.2]
    duration = 0.5
    exact, heuristic = [], []
    for delta in boosts:
        base = transport_grid(dict(_transport(), velocity=0.2))["displacement"]
        moved = transport_grid(dict(_transport(), velocity=0.2 + delta))["displacement"]
        exact.append(moved - base)
        base_h = 0.2 * duration * (1 + 0.05 * 0.2)
        moved_h = (0.2 + delta) * duration * (1 + 0.05 * (0.2 + delta))
        heuristic.append(moved_h - base_h)
    fig, ax = plt.subplots(figsize=(7.2, 4.2))
    ax.plot(boosts, [b * duration for b in boosts], "k--", label="expected  Δu·T", lw=1)
    ax.plot(boosts, exact, "o", label="conservative grid update", markersize=7,
            color=STATE_COLOURS[InvariantState.PASS])
    ax.plot(boosts, heuristic, "s", label="velocity-scaled heuristic", markersize=6,
            color=STATE_COLOURS[InvariantState.FAIL])
    ax.set_xlabel("frame boost Δu (velocity units)")
    ax.set_ylabel("response change Δd")
    ax.set_title("Covariance under a reference-frame boost (T = 0.5)")
    ax.grid(alpha=0.3)
    ax.legend(fontsize=8)
    fig.tight_layout()
    path = out / "fig5_boost_sweep.png"
    fig.savefig(path, dpi=160)
    plt.close(fig)
    return {"figure": str(path), "boosts": boosts, "expected": [b * duration for b in boosts],
            "exact": exact, "heuristic": heuristic}


def build_all(plan_path: str | Path, out_dir: str | Path) -> Dict[str, Any]:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    plan = load_plan_file(plan_path)
    report = run_audit(plan)
    summary = {
        "plan": str(plan_path),
        "plan_id": plan.plan_id,
        "plan_digest": report.plan_digest,
        "matrix": figure_verdict_matrix(report, out),
        "mass_trace": figure_mass_trace(out),
        "mass_refinement": figure_mass_refinement(out),
        "rotation_sweep": figure_rotation_sweep(out),
        "boost_sweep": figure_boost_sweep(out),
    }
    (out / "figure_data.json").write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n",
                                          encoding="utf-8")
    return summary


def main(argv: List[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", required=True)
    parser.add_argument("--out", required=True)
    args = parser.parse_args(argv)
    summary = build_all(args.plan, args.out)
    print(json.dumps({"figures": [v["figure"] for k, v in summary.items()
                                  if isinstance(v, dict) and "figure" in v]}, indent=2))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())