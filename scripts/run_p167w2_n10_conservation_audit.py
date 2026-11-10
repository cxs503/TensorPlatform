#!/usr/bin/env python
"""P167W2-N10 — cross-library field-exchange conservation audit.

Runs the three verification faces of the coupling "last mile" and writes a
machine-checkable evidence bundle plus figures:

1. FEM→DEM fracture hand-off  — four ledgers (mass / momentum / angular
   momentum / energy) plus the full inertia tensor, on fragments cut from the
   actual ``failure_faces.json`` surface;
2. LBM→FVM flow exchange      — supermesh inventory conservation and
   area-weighted interface-flux conservation, against the classical
   nearest-neighbour / inverse-distance baselines;
3. clock synchronisation      — conservative barrier invariants and
   optimistic speculation + rollback replay equivalence.

Everything is synthetic (no domain vocabulary); every number in the JSON is
computed here, never transcribed.

Usage::

    python scripts/run_p167w2_n10_conservation_audit.py [--out artifacts/p167w2-n10]
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from typing import Dict, List

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from src.coupling.clock_sync_audit import CoupledClockAudit, audit_legacy_rollback
from src.coupling.fem_to_dem_exact import (
    ExactFractureTransfer,
    FractureFace,
    cube_element,
    dump_failure_faces,
    load_failure_faces,
)
from src.coupling.geometry import box
from src.coupling.lbm_to_fvm_conservative_remap import (
    FieldCell,
    InterfaceFluxTransfer,
    SupermeshRemap,
    cross_section_area,
    cross_section_area_level_set,
    lattice_cells,
    structured_cells,
)

# --------------------------------------------------------------------------- #
# Criteria (declared before the run; the JSON records the measured value)
# --------------------------------------------------------------------------- #
CRITERIA = {
    "fem_dem_mass": 1e-12,
    "fem_dem_momentum": 1e-10,
    "fem_dem_angular_momentum": 1e-10,
    "fem_dem_energy": 1e-10,
    "fem_dem_inertia_tensor": 1e-10,
    "fem_dem_volume": 1e-12,
    "lbm_fvm_mass_inventory": 1e-12,
    "lbm_fvm_momentum_inventory": 1e-10,
    "lbm_fvm_linear_field": 1e-12,
    "lbm_fvm_interface_flux": 1e-12,
    "lbm_fvm_interface_area": 1e-9,
    "clock_barrier_skew": 1e-12,
    "clock_optimistic_replay": 1e-12,
}

# Domain vocabulary that must never leak into the platform layer.  Matched on
# word boundaries: "convex hull" is geometry, "service" is not sea ice.
BANNED_TOKENS = [
    "propeller", "vessel", "ship", "rudder", "keel", "ice", "wigley", "kcs",
    "kvlcc", "bulbous", "seakeeping", "seaway", "sail", "anchor",
    "icebreaker", "freeboard", "bulb",
]


# --------------------------------------------------------------------------- #
# Face 1: FEM → DEM four ledgers
# --------------------------------------------------------------------------- #
def _fracture_surface(case: str) -> List[FractureFace]:
    """Synthetic fracture surfaces: 1, 3 and 6 cutting planes of a unit cube."""
    from src.coupling.fem_to_dem_exact import plane_cut_faces

    if case == "single_plane":
        return plane_cut_faces([[0.10, 0.0, 0.0]], [[1.0, 0.0, 0.0]])
    if case == "three_planes":
        return plane_cut_faces(
            [[0.10, 0.0, 0.0], [0.0, -0.15, 0.05], [0.05, 0.05, 0.20]],
            [[1.0, 0.25, 0.0], [0.0, 1.0, 0.3], [0.2, 0.0, 1.0]],
        )
    if case == "six_planes":
        pts = [[0.12, 0, 0], [-0.08, 0, 0], [0, 0.15, 0], [0, -0.1, 0], [0, 0, 0.18], [0, 0, -0.06]]
        nrm = [[1, 0.2, 0.1], [-1, 0, 0.2], [0.1, 1, 0], [0, -1, 0.1], [0.2, 0, 1], [0, 0.1, -1]]
        return plane_cut_faces(pts, nrm)
    raise ValueError(case)


def fem_to_dem_face(out_dir: str) -> dict:
    transfer = ExactFractureTransfer()
    cases: Dict[str, dict] = {}
    worst = {k: 0.0 for k in ("mass", "momentum", "angular_momentum", "energy",
                              "inertia", "volume")}
    for case in ("single_plane", "three_planes", "six_planes"):
        element = cube_element(elastic_strain_energy=0.3)
        faces = _fracture_surface(case)

        # Round-trip through the artifact format the FEM side writes.
        path = os.path.join(out_dir, f"failure_faces_{case}.json")
        dump_failure_faces(faces, path)
        consumed, validation = load_failure_faces(path)

        frags, ledger = transfer.transfer(element, consumed)
        summary = ledger.summary()
        for key in worst:
            worst[key] = max(worst[key], summary[key]["residual"])
        surrogate = transfer.compare_with_sphere_surrogate(element, consumed)
        cases[case] = {
            "n_facets": len(consumed),
            "n_fragments": len(frags),
            "artifact_validation": {
                "all_valid": validation["all_valid"],
                "max_area_rel_error": validation["max_area_rel_error"],
                "min_normal_alignment": validation["min_normal_alignment"],
            },
            "ledgers": {k: summary[k] for k in
                        ("mass", "momentum", "angular_momentum", "energy", "inertia", "volume")},
            "all_passed": summary["all_passed"],
            "fragment_mass_range": [float(min(f.mass for f in frags)),
                                    float(max(f.mass for f in frags))],
            "fragment_inertia_anisotropy": [
                float(np.linalg.eigvalsh(f.inertia).max() / max(np.linalg.eigvalsh(f.inertia).min(), 1e-300))
                for f in frags],
            "sphere_surrogate_baseline": surrogate,
        }

    # Tampered artifact: does the validator notice a wrong declared area?
    faces = _fracture_surface("three_planes")
    tampered = [FractureFace(vertices=f.vertices, normal=f.normal, area=f.area * 1.5)
                for f in faces]
    path = os.path.join(out_dir, "failure_faces_tampered.json")
    dump_failure_faces(tampered, path)
    _, tamper_report = load_failure_faces(path)

    return {
        "cases": cases,
        "worst_residual": worst,
        "artifact_validation_negative_control": {
            "all_valid": tamper_report["all_valid"],
            "max_area_rel_error": tamper_report["max_area_rel_error"],
            "detected": (not tamper_report["all_valid"])
            and tamper_report["max_area_rel_error"] > 1e-3,
        },
        "sphere_surrogate_summary": {
            "full_tensor_inertia_residual": max(
                c["sphere_surrogate_baseline"]["legacy_full_tensor_inertia_residual"]
                for c in cases.values()),
            "angular_momentum_residual": max(
                c["sphere_surrogate_baseline"]["legacy_angular_momentum_residual"]
                for c in cases.values()),
            "fragment_inertia_isotropic": all(
                c["sphere_surrogate_baseline"]["legacy_is_isotropic_fragment_inertia"]
                for c in cases.values()),
        },
    }


# --------------------------------------------------------------------------- #
# Face 2: LBM → FVM inventory + interface flux
# --------------------------------------------------------------------------- #
def _fill_tangential_field(cells) -> None:
    """ρ and u·n vary *along* the coupling plane (that is what a remap must catch)."""
    for c in cells:
        x, y, z = c.centroid
        c.density = 1.0 + 0.4 * np.sin(2.0 * np.pi * y) * np.cos(2.0 * np.pi * z)
        c.velocity = np.array([
            1.0 + 0.5 * np.sin(2.5 * y) * np.cos(3.0 * z),
            0.2 * np.sin(3.0 * y),
            0.0,
        ])


def lbm_to_fvm_face() -> dict:
    # --- inventory remap on a nested pair of grids
    src = lattice_cells([0.0, 0.0, 0.0], 0.1, [8, 8, 8], "lbm")
    tgt = structured_cells([0.0, 0.0, 0.0], [0.2, 0.2, 0.2], [4, 4, 4], "fvm")
    _fill_tangential_field(src)
    remap = SupermeshRemap()
    inv = remap.remap(src, tgt)

    # linear-field exactness (supermesh first-moment identity)
    lin_src = lattice_cells([0.0, 0.0, 0.0], 0.1, [8, 8, 8])
    lin_tgt = structured_cells([0.0, 0.0, 0.0], [0.2, 0.2, 0.2], [4, 4, 4])
    lin = SupermeshRemap()
    lin.build(lin_src, lin_tgt)
    linear_err = lin.linear_field_error(np.array([1.0, 2.0, -0.5]))

    # --- interface flux, three plane orientations
    flux_transfer = InterfaceFluxTransfer()
    planes = {
        "face_aligned": ([0.4, 0.0, 0.0], [1.0, 0.0, 0.0]),
        "inside_cell": ([0.37, 0.0, 0.0], [1.0, 0.0, 0.0]),
        "oblique": ([0.25, 0.25, 0.25], [1.0, 1.0, 0.0]),
    }
    flux = {}
    for name, (point, normal) in planes.items():
        exact = flux_transfer.transfer(src, tgt, point, normal)
        base = flux_transfer.transfer_by_cell_centres(src, tgt, point, normal)
        flux[name] = {"exact": exact.summary(), "inverse_distance_baseline": base.summary()}

    # --- refinement study: the exact transfer must stay at round-off, the
    #     cell-centre baseline must shrink like O(h)
    refinement = []
    domain = 0.4
    for n in (4, 8, 16):
        h = domain / n
        s = lattice_cells([0.0, 0.0, 0.0], h, [n, n, n], "lbm")
        t = structured_cells([0.0, 0.0, 0.0], [2.0 * h] * 3, [n // 2] * 3, "fvm")
        _fill_tangential_field(s)
        point = [0.193, 0.0, 0.0]                # strictly inside a source cell for every h
        normal = [1.0, 0.0, 0.0]
        ex = flux_transfer.transfer(s, t, point, normal)
        bl = flux_transfer.transfer_by_cell_centres(s, t, point, normal)
        refinement.append({
            "n_source_cells": len(s),
            "h": h,
            "n_interface_faces": ex.n_pairs,
            "exact_flux_residual": ex.flux_residual,
            "baseline_flux_residual": bl.flux_residual,
            "baseline_flux": bl.flux_target,
        })
    # observed order of the baseline error
    if len(refinement) >= 2:
        hs = [r["h"] for r in refinement]
        errs = [r["baseline_flux_residual"] for r in refinement]
        orders = [float(np.log(errs[i] / errs[i + 1]) / np.log(hs[i] / hs[i + 1]))
                  for i in range(len(errs) - 1)]
        for i, order in enumerate(orders):
            refinement[i]["observed_order_to_next"] = order

    # --- two independent geometric routes for the section area
    rng = np.random.default_rng(7)
    area_checks = []
    for _ in range(20):
        c = rng.uniform(-0.4, 0.4, 3)
        half = rng.uniform(0.1, 0.5, 3)
        b = box(c, half)
        n = rng.normal(size=3)
        n /= np.linalg.norm(n)
        p = c + rng.uniform(-0.25, 0.25, 3)
        a_shoelace = cross_section_area(b, p, n)
        a_level = cross_section_area_level_set(b, p, n)
        if max(a_shoelace, a_level) > 1e-3:
            area_checks.append(abs(a_shoelace - a_level) / max(a_shoelace, a_level))

    return {
        "inventory": inv.summary(),
        "linear_field_residual": linear_err,
        "interface_flux": flux,
        "refinement": refinement,
        "area_cross_check": {
            "n_cases": len(area_checks),
            "max_rel_diff": float(max(area_checks)) if area_checks else 0.0,
        },
    }


# --------------------------------------------------------------------------- #
# Face 3: clock synchronisation semantics
# --------------------------------------------------------------------------- #
def clock_face() -> dict:
    audit = CoupledClockAudit()
    result = audit.audit()
    # sample-period dependence of the conservative lag
    lag_study = []
    for sample_every in (2, 5, 10):
        a = CoupledClockAudit(sample_every=sample_every)
        r = a.audit()
        lag_study.append({
            "sample_period": sample_every * a.dt_a,
            "conservative_deviation": r["conservative"]["deviation_from_replay"],
            "optimistic_deviation": r["optimistic_rollback"]["deviation_from_replay"],
        })
    return {
        "audit": result,
        "lag_study": lag_study,
        "legacy_rollback": audit_legacy_rollback(),
    }


# --------------------------------------------------------------------------- #
# Zero-domain-semantics scan
# --------------------------------------------------------------------------- #
def domain_semantics_scan() -> dict:
    files = [
        "src/coupling/geometry/polytope.py",
        "src/coupling/geometry/__init__.py",
        "src/coupling/fem_to_dem_exact.py",
        "src/coupling/lbm_to_fvm_conservative_remap.py",
        "src/coupling/clock_sync_audit.py",
        "tests/test_p167w2_n10_cross_solver_conservation.py",
    ]
    import re

    hits = []
    for rel in files:
        path = os.path.join(ROOT, rel)
        if not os.path.exists(path):
            continue
        text = open(path, "r", encoding="utf-8").read().lower()
        for token in BANNED_TOKENS:
            if re.search(rf"\b{re.escape(token)}s?\b", text):
                hits.append({"file": rel, "token": token})
    return {"files_scanned": len(files), "hits": hits, "clean": not hits,
            "method": "word-boundary regex over the campaign vocabulary"}


# --------------------------------------------------------------------------- #
# Figures
# --------------------------------------------------------------------------- #
def make_figures(out_dir: str, payload: dict) -> List[str]:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    paths: List[str] = []

    # Fig 1: four ledgers exact vs sphere surrogate
    ledger_keys = ["mass", "momentum", "angular_momentum", "energy", "inertia", "volume"]
    labels = ["mass", "momentum", "ang. mom.", "energy", "inertia\n(full tensor)", "volume"]
    exact = [payload["fem_to_dem"]["worst_residual"][k] for k in ledger_keys]
    surrogate = [
        0.0,
        0.0,
        payload["fem_to_dem"]["sphere_surrogate_summary"]["angular_momentum_residual"],
        0.0,
        payload["fem_to_dem"]["sphere_surrogate_summary"]["full_tensor_inertia_residual"],
        0.0,
    ]
    fig, ax = plt.subplots(figsize=(7.2, 4.0))
    idx = np.arange(len(labels))
    w = 0.38
    ax.bar(idx - w / 2, [max(v, 1e-17) for v in exact], w,
           label="exact polyhedral transfer", color="#2b6cb0")
    ax.bar(idx + w / 2, [max(v, 1e-17) for v in surrogate], w,
           label="sphere-equivalent surrogate", color="#c05621")
    ax.set_yscale("log")
    ax.axhline(1e-10, ls="--", lw=1.0, color="k", label="acceptance 1e-10")
    for i, v in enumerate(exact):
        ax.text(i - w / 2, max(v, 1e-17) * 1.6, f"{v:.1e}", ha="center", fontsize=7)
    for i, v in enumerate(surrogate):
        if v > 0:
            ax.text(i + w / 2, v * 1.6, f"{v:.2f}", ha="center", fontsize=7)
    ax.set_xticks(idx)
    ax.set_xticklabels(labels)
    ax.set_ylabel("relative ledger residual")
    ax.set_title("FEM→DEM fracture hand-off: exact geometry vs sphere surrogate")
    ax.legend(fontsize=8)
    fig.tight_layout()
    p = os.path.join(out_dir, "fig1_four_ledger_residuals.png")
    fig.savefig(p, dpi=150)
    plt.close(fig)
    paths.append(p)

    # Fig 2: interface-flux conservation + refinement of the baseline
    ref = payload["lbm_to_fvm"]["refinement"]
    hs = [r["h"] for r in ref]
    ex = [max(r["exact_flux_residual"], 1e-18) for r in ref]
    bl = [r["baseline_flux_residual"] for r in ref]
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(10.0, 4.0))
    ax1.loglog(hs, bl, "o-", label="cell-centre (inverse-distance) baseline")
    ax1.loglog(hs, ex, "s-", label="area-weighted transfer (exact)")
    ax1.set_xlabel("interface cell size h  (target cells are 2h)")
    orders = [r.get("observed_order_to_next") for r in ref if r.get("observed_order_to_next")]
    if orders:
        ax1.annotate("observed decay order: " + ", ".join(f"{o:.2f}" for o in orders),
                     xy=(0.05, 0.9), xycoords="axes fraction", fontsize=8)
    ax1.set_ylabel("relative interface-flux residual")
    ax1.set_title("LBM→FVM mass flux through the coupling plane")
    ax1.grid(True, which="both", alpha=0.25)
    ax1.legend(fontsize=8)

    names = list(payload["lbm_to_fvm"]["interface_flux"].keys())
    ex2 = [max(payload["lbm_to_fvm"]["interface_flux"][k]["exact"]["flux_residual"], 1e-18)
           for k in names]
    bl2 = [payload["lbm_to_fvm"]["interface_flux"][k]["inverse_distance_baseline"]["flux_residual"]
           for k in names]
    idx2 = np.arange(len(names))
    ax2.bar(idx2 - 0.2, bl2, 0.4, label="baseline", color="#c05621")
    ax2.bar(idx2 + 0.2, ex2, 0.4, label="area-weighted", color="#2b6cb0")
    ax2.set_yscale("log")
    ax2.set_xticks(idx2)
    ax2.set_xticklabels([n.replace("_", "-") for n in names])
    ax2.set_ylabel("relative flux residual")
    ax2.set_title("Plane orientation sensitivity")
    ax2.legend(fontsize=8)
    fig.tight_layout()
    p = os.path.join(out_dir, "fig2_interface_flux_conservation.png")
    fig.savefig(p, dpi=150)
    plt.close(fig)
    paths.append(p)

    # Fig 3: clock semantics
    audit = CoupledClockAudit()
    cons = audit.run_conservative()
    opt = audit.run_optimistic(rollback=True)
    naive = audit.run_optimistic(rollback=False)
    ref_run = audit.run_replay_reference()
    periods = np.arange(audit.n_samples + 1) * audit.dt_a * audit.sample_every

    # trajectories (recomputed here for plotting)
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(10.0, 4.0))
    a, b = audit._new_pair()
    traj_cons = [b.y]
    published = [audit._observe(a)]
    for _ in range(audit.n_samples):
        a.advance(audit.sample_every, coupling=0.0)
        for _ in range(audit.sample_every * audit.substeps):
            b.step(published[-1])
        published.append(audit._observe(a))
        traj_cons.append(b.y)
    ax1.plot(periods, traj_cons, "o-", label="conservative (one-sample lag)")
    ax1.plot(periods[: len(payload["clock"]["replay_curve"])],
             payload["clock"]["replay_curve"], "k--", label="deterministic replay (truth)")
    ax1.set_xlabel("synchronisation time")
    ax1.set_ylabel("coupled solver state")
    ax1.set_title("Conservative barrier: bounded, but lagged")
    ax1.grid(alpha=0.25)
    ax1.legend(fontsize=8)

    lag = [entry["conservative_deviation"] for entry in payload["clock"]["lag_study"]]
    per = [entry["sample_period"] for entry in payload["clock"]["lag_study"]]
    ax2.loglog(per, lag, "o-", label="conservative lag deviation")
    ax2.loglog(per, [entry["optimistic_deviation"] for entry in payload["clock"]["lag_study"]],
               "s-", label="optimistic + rollback")
    ax2.set_xlabel("coupling sample period")
    ax2.set_ylabel("deviation from replay reference")
    ax2.set_title("Optimistic rollback is exact; conservative lag scales with the period")
    ax2.grid(True, which="both", alpha=0.25)
    ax2.legend(fontsize=8)
    fig.tight_layout()
    p = os.path.join(out_dir, "fig3_clock_synchronisation.png")
    fig.savefig(p, dpi=150)
    plt.close(fig)
    paths.append(p)

    return paths


# --------------------------------------------------------------------------- #
def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default=os.path.join(ROOT, "artifacts", "p167w2-n10"))
    args = parser.parse_args()
    out_dir = os.path.abspath(args.out)
    os.makedirs(out_dir, exist_ok=True)

    fem = fem_to_dem_face(out_dir)
    lbm = lbm_to_fvm_face()
    clock = clock_face()
    scan = domain_semantics_scan()

    # replay curve for the figure (state of the coupled solver at each sample)
    audit = CoupledClockAudit()
    a, b = audit._new_pair()
    replay_curve = [b.y]
    samples = [audit._observe(a)]
    for _ in range(audit.n_samples):
        a.advance(audit.sample_every, coupling=0.0)
        samples.append(audit._observe(a))
    b2 = audit._new_pair()[1]
    replay_curve = [b2.y]
    for k in range(audit.n_samples):
        b2.advance(audit.sample_every * audit.substeps, samples[k + 1])
        replay_curve.append(b2.y)
    clock["replay_curve"] = replay_curve

    payload = {
        "node": "p167w2-n10-cross-solver-conservation",
        "criteria": CRITERIA,
        "fem_to_dem": fem,
        "lbm_to_fvm": lbm,
        "clock": clock,
        "domain_semantics_scan": scan,
    }

    # ---- criteria evaluation ------------------------------------------ #
    checks = []

    def check(cid: str, value: float, threshold: float, note: str = "") -> None:
        checks.append({"id": cid, "value": float(value), "threshold": float(threshold),
                       "passed": bool(float(value) <= float(threshold)), "note": note})

    worst = fem["worst_residual"]
    check("fem_dem_mass", worst["mass"], CRITERIA["fem_dem_mass"])
    check("fem_dem_momentum", worst["momentum"], CRITERIA["fem_dem_momentum"])
    check("fem_dem_angular_momentum", worst["angular_momentum"], CRITERIA["fem_dem_angular_momentum"])
    check("fem_dem_energy", worst["energy"], CRITERIA["fem_dem_energy"])
    check("fem_dem_inertia_tensor", worst["inertia"], CRITERIA["fem_dem_inertia_tensor"])
    check("fem_dem_volume", worst["volume"], CRITERIA["fem_dem_volume"])
    checks.append({
        "id": "failure_faces_artifact_tamper_detected",
        "value": float(fem["artifact_validation_negative_control"]["max_area_rel_error"]),
        "threshold": 1e-3, "passed": bool(fem["artifact_validation_negative_control"]["detected"]),
        "note": "declared area scaled by 1.5 must be rejected",
    })
    inv = lbm["inventory"]
    check("lbm_fvm_mass_inventory", inv["mass_residual"], CRITERIA["lbm_fvm_mass_inventory"])
    check("lbm_fvm_momentum_inventory", inv["momentum_residual"], CRITERIA["lbm_fvm_momentum_inventory"])
    check("lbm_fvm_coverage_deficit", inv["max_uncovered_fraction"], 1e-12)
    check("lbm_fvm_linear_field", lbm["linear_field_residual"], CRITERIA["lbm_fvm_linear_field"])
    check("lbm_fvm_interface_flux", max(v["exact"]["flux_residual"] for v in lbm["interface_flux"].values()),
          CRITERIA["lbm_fvm_interface_flux"])
    check("lbm_fvm_interface_area", max(v["exact"]["area_residual"] for v in lbm["interface_flux"].values()),
          CRITERIA["lbm_fvm_interface_area"])
    checks.append({
        "id": "baseline_flux_error_resolvable",
        "value": float(lbm["interface_flux"]["oblique"]["inverse_distance_baseline"]["flux_residual"]),
        "threshold": 1e-4, "passed": bool(
            lbm["interface_flux"]["oblique"]["inverse_distance_baseline"]["flux_residual"] > 1e-4),
        "note": "the classical cell-centre transfer must show a measurable error, "
                "otherwise the exact path proves nothing",
    })
    ca = clock["audit"]
    check("clock_barrier_skew", ca["conservative"]["max_barrier_skew"], CRITERIA["clock_barrier_skew"])
    checks.append({"id": "clock_conservative_future_reads",
                   "value": float(ca["conservative"]["future_reads"]), "threshold": 0.0,
                   "passed": ca["conservative"]["future_reads"] == 0,
                   "note": "no solver may consume uncommitted partner data"})
    checks.append({"id": "clock_global_time_monotone",
                   "value": float(not ca["conservative"]["global_time_monotone"]), "threshold": 0.0,
                   "passed": bool(ca["conservative"]["global_time_monotone"])})
    check("clock_optimistic_replay", ca["optimistic_rollback"]["deviation_from_replay"],
          CRITERIA["clock_optimistic_replay"])
    checks.append({"id": "clock_negative_control_fires",
                   "value": float(ca["negative_control_peek_ahead"]["future_reads"]),
                   "threshold": 1.0, "passed": bool(ca["negative_control_peek_ahead"]["detected"]),
                   "note": "a peek-ahead protocol must be detected"})
    checks.append({"id": "rollback_necessary",
                   "value": float(ca["optimistic_no_rollback"]["deviation_from_replay"]),
                   "threshold": 1e-6,
                   "passed": bool(ca["optimistic_no_rollback"]["deviation_from_replay"] > 1e-6),
                   "note": "without rollback the optimistic run must drift"})
    checks.append({"id": "zero_domain_semantics",
                   "value": float(len(scan["hits"])), "threshold": 0.0,
                   "passed": bool(scan["clean"])})

    payload["checks"] = checks
    payload["all_passed"] = all(c["passed"] for c in checks)
    payload["n_checks"] = len(checks)
    payload["n_failed"] = sum(1 for c in checks if not c["passed"])

    figures = make_figures(out_dir, payload)
    payload["figures"] = [os.path.basename(p) for p in figures]

    json_path = os.path.join(out_dir, "audit_evidence.json")
    with open(json_path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, default=float)
    payload["evidence_path"] = json_path

    # ---- console table ------------------------------------------------ #
    print("=" * 78)
    print("P167W2-N10 cross-library conservation audit")
    print("=" * 78)
    for c in checks:
        flag = "PASS" if c["passed"] else "FAIL"
        print(f"[{flag}] {c['id']:<38} value={c['value']:.3e} thr={c['threshold']:.1e}")
    print("-" * 78)
    print(f"FEM→DEM fragments: " + ", ".join(
        f"{k}={v['n_fragments']}" for k, v in payload["fem_to_dem"]["cases"].items()))
    print(f"sphere surrogate: inertia {payload['fem_to_dem']['sphere_surrogate_summary']['full_tensor_inertia_residual']:.3f}"
          f"  angular mom {payload['fem_to_dem']['sphere_surrogate_summary']['angular_momentum_residual']:.3f}")
    print(f"interface flux baseline (oblique): "
          f"{payload['lbm_to_fvm']['interface_flux']['oblique']['inverse_distance_baseline']['flux_residual']:.3e}")
    print(f"clock: conservative lag {ca['conservative']['deviation_from_replay']:.3e}, "
          f"optimistic+rollback {ca['optimistic_rollback']['deviation_from_replay']:.3e}, "
          f"no-rollback {ca['optimistic_no_rollback']['deviation_from_replay']:.3e}")
    print(f"evidence: {json_path}")
    print(f"all_passed={payload['all_passed']}  ({payload['n_failed']} failed of {payload['n_checks']})")
    return 0 if payload["all_passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())