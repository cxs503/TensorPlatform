#!/usr/bin/env python3
"""Cross-solver coupling benchmark: acceptance criteria B1..B10 (domain-free).

Run::

    python -m benchmarks.verified.cross_solver_coupling.run_benchmark

Writes ``results.json`` (every criterion with its number, threshold, verdict and
artifacts), the figures, and — with ``--emit-doc`` — the paper-grade markdown
report whose tables are generated from ``results.json`` (no hand-copied number).

The case is synthetic: a unit square, two uniform grids, and analytic fields.
What is being verified is the *infrastructure* — conservative mapping, clock
synchronisation, sub-cycling, energy bookkeeping and coupled stability — not any
physical model.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import platform
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Sequence

import numpy as np

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from benchmarks.verified.cross_solver_coupling.cases import (  # noqa: E402
    analytic_cell_averages,
    default_case,
    unit_square_mesh,
)
from tensorphysics.clock_sync import (  # noqa: E402
    CoupledOscillatorConfig,
    CouplingClock,
    ClockSpec,
    InterfaceEnergyLedger,
    coupling_clock_block,
    coupled_window_map,
    compare_series,
    linear_map_probe,
    lyapunov_spectrum,
    simulate_coupled_oscillators,
    validate_coupling_clock,
)
from tools.field_mapper import (  # noqa: E402
    CellSet,
    ConservativeMap,
    Correction,
    CrossMapErrorMode,
    FaultTolerances,
    MapLaw,
    conservation_errors,
    coverage_fraction,
    detect_faults,
    l2_relative_error,
    load_failure_faces,
    validate_failure_faces,
    write_failure_faces,
)

HERE = Path(__file__).resolve().parent
FIGDIR = HERE / "figures"

THRESHOLDS = {
    "B1_mass_rel_error": 5e-3,
    "B2_momentum_rel_error": 1e-2,
    "B3_energy_rel_error": 1e-2,
    "B4_max_skew_s": 1e-10,
    "B5_subcycle_rel_error": 5e-2,
    "B6_max_lyapunov": 0.0,
    "B7_identity_l2": 1e-12,
    "B8_zero_coupling_energy": 1e-12,
    "B9_grid_ratio_rel_error": 2e-2,
    "B10_min_error_modes": 3,
    "B10_min_manifest_fields": 4,
}

GRID_RATIOS = (1, 2, 4, 8, 10)
SOURCE_CELLS = 16


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _rel(path: Path) -> str:
    """Path relative to the repo root when possible (stable in reports/manifests)."""
    try:
        return str(Path(path).resolve().relative_to(ROOT))
    except ValueError:
        return str(path)


def _git_commit() -> str:
    try:
        out = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=str(ROOT), capture_output=True, text=True, timeout=10
        )
        return out.stdout.strip() if out.returncode == 0 else "unknown"
    except Exception:  # pragma: no cover - environment without git
        return "unknown"


def _criterion(name: str, metric: str, value: float, threshold: float, *, comparator: str = "<=",
               details: Optional[Dict[str, object]] = None, artifacts: Sequence[str] = ()) -> Dict[str, object]:
    if comparator == "<=":
        passed = bool(value <= threshold)
    elif comparator == ">=":
        passed = bool(value >= threshold)
    else:
        raise ValueError(f"unknown comparator {comparator!r}")
    return {
        "criterion": name,
        "metric": metric,
        "value": float(value),
        "threshold": float(threshold),
        "comparator": comparator,
        "passed": passed,
        "details": details or {},
        "artifacts": list(artifacts),
    }


def _map_case(case, src: CellSet, dst: CellSet, operator: str = "overlap"):
    """Map density / momentum / energy and measure the conserved totals."""
    mapper = ConservativeMap(src, dst, operator=operator)
    src_fields = case.sample(src)
    dst_ref = analytic_cell_averages(case, dst)
    rho = mapper.apply(src_fields["density"], law=MapLaw.DENSITY)
    mom = mapper.apply_vector(src_fields["momentum"], law=MapLaw.DENSITY)
    energy = mapper.apply(src_fields["energy"], law=MapLaw.DENSITY)
    totals = {
        "mass_src": float((src_fields["density"] * src.measure).sum()),
        "mass_dst": float((rho * dst.measure).sum()),
        "momentum_x_src": float((src_fields["momentum"][:, 0] * src.measure).sum()),
        "momentum_x_dst": float((mom[:, 0] * dst.measure).sum()),
        "momentum_y_src": float((src_fields["momentum"][:, 1] * src.measure).sum()),
        "momentum_y_dst": float((mom[:, 1] * dst.measure).sum()),
        "energy_src": float((src_fields["energy"] * src.measure).sum()),
        "energy_dst": float((energy * dst.measure).sum()),
    }
    errors = conservation_errors(
        [
            ("mass", totals["mass_src"], totals["mass_dst"]),
            ("momentum_x", totals["momentum_x_src"], totals["momentum_x_dst"]),
            ("momentum_y", totals["momentum_y_src"], totals["momentum_y_dst"]),
            ("energy", totals["energy_src"], totals["energy_dst"]),
        ]
    )
    accuracy = {
        "density_l2_vs_analytic": l2_relative_error(rho, dst_ref["density"], dst.measure),
        "energy_l2_vs_analytic": l2_relative_error(energy, dst_ref["energy"], dst.measure),
        "momentum_x_l2_vs_analytic": l2_relative_error(mom[:, 0], dst_ref["momentum"][:, 0], dst.measure),
    }
    audit = mapper.audit(src_fields["density"], rho, law=MapLaw.DENSITY)
    covered = coverage_fraction(mapper.entries, dst.measure)
    return {
        "mapper": mapper,
        "fields": {"density": rho, "momentum": mom, "energy": energy},
        "source_fields": src_fields,
        "reference": dst_ref,
        "totals": totals,
        "errors": errors,
        "accuracy": accuracy,
        "audit": audit,
        "covered": covered,
        "operator_summary": mapper.entries.summary(),
    }


# --------------------------------------------------------------------------- #
# B1..B3, B7, B9 — conservative mapping
# --------------------------------------------------------------------------- #
def run_mapping_criteria(case, ratios: Sequence[int], source_cells: int = SOURCE_CELLS) -> Dict[str, object]:
    src = unit_square_mesh(source_cells, name=f"source-{source_cells}")
    sweep: List[Dict[str, float]] = []
    per_ratio: Dict[int, Dict[str, object]] = {}
    for ratio in ratios:
        n_dst = source_cells * int(ratio)
        dst = unit_square_mesh(n_dst, name=f"target-{n_dst}")
        clamped = _map_case(case, src, dst)
        control = _map_case(case, src, dst, operator="nearest")
        corrected = ConservativeMap(src, dst, operator="nearest")
        ctrl_fields = case.sample(src)
        ctrl_energy = corrected.apply(
            ctrl_fields["energy"], law=MapLaw.DENSITY, correction=Correction.GLOBAL
        )
        err_ctrl = conservation_errors(
            [
                (
                    "energy",
                    float((ctrl_fields["energy"] * src.measure).sum()),
                    float((ctrl_energy * dst.measure).sum()),
                )
            ]
        )
        row = {
            "ratio": int(ratio),
            "n_src": src.n_cells,
            "n_dst": dst.n_cells,
            "mass_rel_error": clamped["errors"]["mass_rel_error"],
            "momentum_rel_error": max(
                clamped["errors"]["momentum_x_rel_error"], clamped["errors"]["momentum_y_rel_error"]
            ),
            "energy_rel_error": clamped["errors"]["energy_rel_error"],
            "control_mass_rel_error": control["errors"]["mass_rel_error"],
            "control_energy_rel_error": control["errors"]["energy_rel_error"],
            "control_energy_rel_error_after_correction": err_ctrl["energy_rel_error"],
            "density_l2_vs_analytic": clamped["accuracy"]["density_l2_vs_analytic"],
            "control_density_l2_vs_analytic": control["accuracy"]["density_l2_vs_analytic"],
            "covered_fraction_min": clamped["audit"].covered_fraction_min,
            "n_bbox_pairs": clamped["operator_summary"]["n_bbox_pairs"],
            "n_exact_pairs": clamped["operator_summary"]["n_exact_pairs"],
            "matrix_digest": clamped["audit"].matrix_digest,
        }
        sweep.append(row)
        per_ratio[int(ratio)] = {"overlap": clamped, "nearest": control}

    ref_ratio = 4 if 4 in ratios else int(ratios[-1])
    first = per_ratio[ref_ratio]["overlap"]
    mass = first["errors"]["mass_rel_error"]
    momentum = max(first["errors"]["momentum_x_rel_error"], first["errors"]["momentum_y_rel_error"])
    energy = first["errors"]["energy_rel_error"]

    derived = _derived_energy_consistency(first)

    criteria = {
        "B1": _criterion(
            "B1", "mass_rel_error", mass, THRESHOLDS["B1_mass_rel_error"],
            details={
                "grid": f"{source_cells}x{source_cells} -> {source_cells * ref_ratio}x{source_cells * ref_ratio}",
                "mass_src": first["totals"]["mass_src"],
                "mass_dst": first["totals"]["mass_dst"],
                "control_nearest_mass_rel_error": per_ratio[ref_ratio]["nearest"]["errors"]["mass_rel_error"],
            },
        ),
        "B2": _criterion(
            "B2", "momentum_rel_error", momentum, THRESHOLDS["B2_momentum_rel_error"],
            details={
                "momentum_x_rel_error": first["errors"]["momentum_x_rel_error"],
                "momentum_y_rel_error": first["errors"]["momentum_y_rel_error"],
            },
        ),
        "B3": _criterion(
            "B3", "energy_rel_error", energy, THRESHOLDS["B3_energy_rel_error"],
            details={
                "derived_energy_from_mapped_fields_rel_error": derived,
                "note": "the gate is the conserved total of the mapped energy density",
            },
        ),
        "B9": _criterion(
            "B9", "max_conservation_rel_error_over_ratios",
            max(max(r["mass_rel_error"], r["momentum_rel_error"], r["energy_rel_error"]) for r in sweep),
            THRESHOLDS["B9_grid_ratio_rel_error"],
            details={
                "ratios": [int(r) for r in ratios],
                "per_ratio": sweep,
                "control_nearest_max_error": max(r["control_energy_rel_error"] for r in sweep),
                "control_error_after_global_correction_max": max(
                    r["control_energy_rel_error_after_correction"] for r in sweep
                ),
            },
        ),
    }

    # B7 — identity on a coincident mesh
    same = unit_square_mesh(SOURCE_CELLS * 3, name="identity")
    mapper = ConservativeMap(same, same)
    values = case.sample(same)
    mapped = mapper.apply(values["density"], law=MapLaw.DENSITY)
    mapped_e = mapper.apply(values["energy"], law=MapLaw.DENSITY)
    identity_l2 = max(
        l2_relative_error(mapped, values["density"], same.measure),
        l2_relative_error(mapped_e, values["energy"], same.measure),
    )
    criteria["B7"] = _criterion(
        "B7", "identity_l2_rel", identity_l2, THRESHOLDS["B7_identity_l2"],
        details={
            "identity_total_rel_error": mapper.audit(values["density"], mapped).total_rel_error,
            "n_cells": same.n_cells,
            "offdiagonal_measure": float(
                mapper.matrix[np.triu_indices(same.n_cells, 1)].sum()
            ),
        },
    )
    return {"criteria": criteria, "sweep": sweep, "first": first, "per_ratio": per_ratio}


def _derived_energy_consistency(mapped: Dict[str, object]) -> float:
    """Energy recomputed from the *separately* mapped density/momentum.

    Remapping ``rho``, ``rho·u`` and ``e`` independently conserves all three totals
    but does not guarantee that ``e`` equals the energy reconstructed from the
    other two: the reported number is that inconsistency (not gated, but it has to
    be visible).  The internal-energy part is taken from the analytic reference so
    the metric isolates the coupling between the separately mapped fields.
    """
    rho = mapped["fields"]["density"]
    mom = mapped["fields"]["momentum"]
    energy = mapped["fields"]["energy"]
    ref_energy = mapped["reference"]["energy"]
    ref_rho = mapped["reference"]["density"]
    ref_mom = mapped["reference"]["momentum"]
    ref_internal = ref_energy - 0.5 * np.sum(ref_mom**2, axis=1) / np.maximum(ref_rho, 1e-30)
    reconstructed = 0.5 * np.sum(mom**2, axis=1) / np.maximum(rho, 1e-30) + ref_internal
    return l2_relative_error(reconstructed, energy, mapped["mapper"].dst.measure)


# --------------------------------------------------------------------------- #
# B4, B5 — clock synchronisation and sub-cycling
# --------------------------------------------------------------------------- #
def run_clock_criteria() -> Dict[str, object]:
    interval = 0.05
    specs = [
        ClockSpec("slow", dt=0.0125, role="master", max_time=1.0),
        ClockSpec("fast", dt=0.0031, role="sub", max_time=1.0),  # 16.13 steps/window
    ]
    aligned = CouplingClock(specs, interval, align_last_substep=True, sync_tolerance=1e-10).run()
    unaligned = CouplingClock(specs, interval, align_last_substep=False, sync_tolerance=1e-10).run()

    b4 = _criterion(
        "B4", "max_coupling_point_skew_s", aligned.max_skew, THRESHOLDS["B4_max_skew_s"],
        details={
            "n_coupling_points": len(aligned.points),
            "truncated_substeps": aligned.total_truncations,
            "unaligned_max_skew_s": unaligned.max_skew,
            "coupling_interval_s": interval,
            "fast_dt_s": specs[1].dt,
            "subcycle_ratios": aligned.subcycle_ratios,
            "schedule_drift_check": float(
                abs(len(aligned.points) * interval - aligned.coupling_times[-1])
            ),
        },
    )

    cfg = CoupledOscillatorConfig(
        coupling_interval=interval, slow_substeps=4, ratio=10.0, t_end=1.0, partner_model="linear"
    )
    coarse = simulate_coupled_oscillators(cfg)
    ref = simulate_coupled_oscillators(cfg, refine=8.0)
    ref_hard = simulate_coupled_oscillators(cfg, refine=8.0, coupling_interval_scale=0.25)
    cmp_easy = compare_series(coarse, ref)
    cmp_hard = compare_series(coarse, ref_hard)
    hold_cfg = CoupledOscillatorConfig(
        coupling_interval=interval, slow_substeps=4, ratio=10.0, t_end=1.0, partner_model="hold"
    )
    hold_run = simulate_coupled_oscillators(hold_cfg)

    b5 = _criterion(
        "B5", "subcycle_state_rel_error", cmp_easy["state_rel_l2"], THRESHOLDS["B5_subcycle_rel_error"],
        details={
            "ratio_fast_to_slow": cfg.ratio,
            "dt_fast": cfg.dt_fast,
            "dt_slow": cfg.dt_slow,
            "reference_refine": 8.0,
            "energy_rel_diff_vs_ref": cmp_easy["max_energy_rel_diff"],
            "harsh_reference_state_rel_error": cmp_hard["state_rel_l2"],
            "harsh_reference_note": "same run vs a reference whose coupling interval is also 4x finer",
            "clock_max_skew_s": coarse.trace.max_skew,
            "hold_partner_closure_rel": hold_run.energy_report.closure_rel,
        },
    )
    return {
        "criteria": {"B4": b4, "B5": b5},
        "aligned": aligned,
        "unaligned": unaligned,
        "coarse": coarse,
        "reference": ref,
        "hold": hold_run,
    }


# --------------------------------------------------------------------------- #
# B6 — coupled stability
# --------------------------------------------------------------------------- #
def run_stability() -> Dict[str, object]:
    results: Dict[str, object] = {}
    spectra: Dict[str, np.ndarray] = {}
    series: Dict[str, np.ndarray] = {}
    for label, damping in (("damped", 0.02), ("conservative", 0.0), ("unstable_control", -0.02)):
        cfg = CoupledOscillatorConfig(
            coupling_interval=0.05, slow_substeps=4, ratio=10.0, t_end=1.0, damping=damping
        )
        step = coupled_window_map(cfg)
        res = lyapunov_spectrum(
            step, [1.0, 0.0, 0.0, 0.0], n_steps=3000, dt=cfg.coupling_interval, transient_steps=200
        )
        results[label] = res
        spectra[label] = res.exponents
        series[label] = res.series

    # estimator self-check: a linear map has an exact spectrum
    linear = np.array(
        [
            [0.90, 0.05, 0.00, 0.00],
            [0.00, 0.80, 0.00, 0.00],
            [0.00, 0.00, 1.00, 0.00],
            [0.00, 0.00, 0.00, 0.95],
        ]
    )
    est = lyapunov_spectrum(lambda x: linear @ x, [1.0, 1.0, 1.0, 1.0], n_steps=400, dt=0.1)
    exact = linear_map_probe(linear, 0.1)
    estimator_error = float(np.max(np.abs(np.sort(est.exponents)[::-1] - exact)))

    damped = results["damped"]
    conservative = results["conservative"]
    unstable = results["unstable_control"]
    b6 = _criterion(
        "B6", "max_lyapunov_exponent", damped.leading, THRESHOLDS["B6_max_lyapunov"],
        details={
            "spectrum_damped": [float(v) for v in damped.exponents],
            "conservative_leading": conservative.leading,
            "conservative_note": "marginal system: theory gives 0, estimator noise is the tolerance",
            "unstable_control_leading": unstable.leading,
            "unstable_control_note": "negative damping must give a positive exponent, else the detector is blind",
            "n_steps": damped.n_steps,
            "dt": damped.dt,
            "estimator_check": {
                "linear_map_estimated": [float(v) for v in np.sort(est.exponents)[::-1]],
                "linear_map_exact": [float(v) for v in exact],
                "max_abs_error": estimator_error,
            },
            "marginal_band": 1e-2,
        },
    )
    return {
        "criteria": {"B6": b6},
        "spectra": spectra,
        "series": series,
        "results": results,
        "estimator_error": estimator_error,
        "estimator_check": {
            "linear_map_estimated": [float(v) for v in np.sort(est.exponents)[::-1]],
            "linear_map_exact": [float(v) for v in exact],
        },
    }


# --------------------------------------------------------------------------- #
# B8 — negative controls
# --------------------------------------------------------------------------- #
def run_negative_controls(case, source_cells: int = SOURCE_CELLS) -> Dict[str, object]:
    cfg = CoupledOscillatorConfig(
        coupling_interval=0.05, slow_substeps=4, ratio=10.0, t_end=0.5, interface_stiffness=0.0
    )
    decoupled = simulate_coupled_oscillators(cfg)
    rep = decoupled.energy_report

    empty_ledger = InterfaceEnergyLedger(tolerance=1e-12)
    empty = empty_ledger.report()

    src = unit_square_mesh(source_cells)
    dst = unit_square_mesh(source_cells * 2)
    mapper = ConservativeMap(src, dst)
    zeros = np.zeros(src.n_cells)
    mapped_zero = mapper.apply(zeros, law=MapLaw.DENSITY)
    zero_map_error = float(np.max(np.abs(mapped_zero))) if mapped_zero.size else 0.0

    zero_exchange = max(
        abs(rep.total_in),
        abs(rep.total_out),
        abs(rep.stored_delta),
        abs(rep.closure_residual),
        abs(rep.mapping_loss),
        abs(empty.closure_residual),
        zero_map_error,
    )
    b8 = _criterion(
        "B8", "zero_coupling_exchange_energy", zero_exchange, THRESHOLDS["B8_zero_coupling_energy"],
        details={
            "decoupled_report": rep.to_dict(),
            "empty_ledger_report": empty.to_dict(),
            "zero_field_map_max_abs": zero_map_error,
            "n_exchanges": rep.n_exchanges,
        },
    )
    return {"criteria": {"B8": b8}, "decoupled": decoupled}


# --------------------------------------------------------------------------- #
# B10 — artifact contracts
# --------------------------------------------------------------------------- #
def run_artifact_contracts(case, mapping: Dict[str, object], artifact_dir: Optional[Path] = None) -> Dict[str, object]:
    artifact_dir = Path(artifact_dir) if artifact_dir is not None else HERE
    artifact_dir.mkdir(parents=True, exist_ok=True)
    src = mapping["first"]["mapper"].src
    dst = mapping["first"]["mapper"].dst

    # --- 1. cross-mapping error modes in a failure_faces/v2 artifact ----------
    faults = []
    # (a) partial coverage: destination mesh larger than the source partition
    big = CellSet.from_boxes(
        np.array([[0.0, 0.0], [1.0, 0.0], [0.0, 1.0], [1.0, 1.0], [2.0, 0.0]]),
        np.array([[1.0, 1.0], [2.0, 1.0], [1.0, 2.0], [2.0, 2.0], [3.0, 1.0]]),
        name="oversized-target",
    )
    partial = ConservativeMap(src, big)
    vals = case.sample(src)["density"]
    out = partial.apply(vals, law=MapLaw.DENSITY)
    audit = partial.audit(vals, out, law=MapLaw.DENSITY)
    covered = coverage_fraction(partial.entries, big.measure)
    faults += detect_faults(audit, covered, tolerances=FaultTolerances(total_rel=5e-3))

    # (b) law mismatch: an extensive quantity mapped with the density law
    extensive = np.full(src.n_cells, 1.0)
    dst_small = unit_square_mesh(int(np.sqrt(src.n_cells)) + 4)
    mix = ConservativeMap(src, dst_small)
    mixed = mix.apply(extensive, law=MapLaw.DENSITY)
    audit_mix = mix.audit(extensive, mixed, law=MapLaw.DENSITY)
    faults += detect_faults(
        audit_mix,
        coverage_fraction(mix.entries, dst_small.measure),
        declared_kind="extensive",
        expected_law="extensive",
        tolerances=FaultTolerances(total_rel=5e-3),
    )

    # (c) non-conservative operator: nearest-cell injection on a coarse target
    forcer = ConservativeMap(src, dst_small, operator="nearest")
    inj = forcer.apply(vals, law=MapLaw.DENSITY)
    audit_inj = forcer.audit(vals, inj, law=MapLaw.DENSITY)
    faults += detect_faults(audit_inj, tolerances=FaultTolerances(total_rel=1e-4))

    # (d) moment drift: a deliberately shifted operator (all mass into cell 0)
    shifted = np.zeros(dst_small.n_cells)
    shifted[0] = float((vals * src.measure).sum() / dst_small.measure[0])
    audit_shift = forcer.audit(vals, shifted, law=MapLaw.DENSITY)
    moment_rel = _moment_rel_error(src, vals, dst_small, shifted)
    faults += detect_faults(
        audit_shift, None, moment_rel_error=moment_rel, tolerances=FaultTolerances(total_rel=5e-3)
    )

    artifact = artifact_dir / "failure_faces.json"
    write_failure_faces(
        str(artifact),
        faults,
        cross_map={
            "matrix_digest": mapping["first"]["audit"].matrix_digest,
            "src_mesh": src.name,
            "dst_mesh": dst.name,
            "tolerances": FaultTolerances().__dict__,
        },
        generated_by={
            "benchmark": "cross_solver_coupling",
            "script": "benchmarks/verified/cross_solver_coupling/run_benchmark.py",
        },
    )
    doc = json.loads(artifact.read_text())
    validation = validate_failure_faces(doc)
    records, summary = load_failure_faces(str(artifact))

    # --- 2. RunManifest coupling_clock block ---------------------------------
    cfg = CoupledOscillatorConfig(coupling_interval=0.05, slow_substeps=4, ratio=10.0, t_end=1.0)
    clock_run = simulate_coupled_oscillators(cfg)
    clock = CouplingClock(
        [
            ClockSpec("slow", dt=cfg.dt_slow, role="master", max_time=cfg.t_end),
            ClockSpec("fast", dt=cfg.dt_fast, role="sub", max_time=cfg.t_end),
        ],
        cfg.coupling_interval,
        align_last_substep=cfg.align,
    )
    trace = clock_run.trace
    block = coupling_clock_block(
        clock, trace, exchanged_fields=("density", "momentum", "energy", "interface_force")
    )
    block_check = validate_coupling_clock(block)
    outputs = [
        {
            "uri": artifact.name,
            "media_type": "application/json",
            "checksum_sha256": _sha256_file(artifact),
            "size_bytes": int(artifact.stat().st_size),
            "role": "cross-mapping failure register",
            "schema_uri": "tools/field_mapper/errors.py#failure_faces/v2",
        }
    ]
    manifest = {
        "schema_version": 1,
        "run_id": "cross-solver-coupling-benchmark",
        "status": "succeeded",
        "validation_status": "passed" if block_check["ok"] else "failed",
        "outputs": outputs,
        "coupling_clock": block,
        "limitations": [
            "synthetic unit-square meshes; no physical model is exercised",
            "interface data treatment is zero-order hold or linear extrapolation only",
        ],
    }
    manifest_path = artifact_dir / "run_manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2))
    schema_check = _validate_manifest_schema(manifest)

    n_modes = len(summary["error_modes_present"])
    n_fields = block_check["n_required_present"]
    b10 = _criterion(
        "B10", "min(error_modes/3, manifest_fields/4)",
        float(min(n_modes / THRESHOLDS["B10_min_error_modes"], n_fields / THRESHOLDS["B10_min_manifest_fields"])),
        1.0, comparator=">=",
        details={
            "n_error_modes": n_modes,
            "error_modes": summary["error_modes_present"],
            "all_vocabulary_modes": [m.value for m in CrossMapErrorMode],
            "failure_faces_validation": validation,
            "failure_faces_summary": {
                k: summary[k] for k in ("schema", "schema_version", "n_faces", "n_faults", "consistent")
            },
            "n_manifest_coupling_clock_fields": n_fields,
            "manifest_fields": block_check["fields_present"],
            "manifest_validation": block_check,
            "manifest_schema_validation": schema_check,
            "moment_rel_error_of_shifted_operator": moment_rel,
            "n_records_loaded": len(records),
        },
        artifacts=[_rel(artifact), _rel(manifest_path)],
    )
    return {
        "criteria": {"B10": b10},
        "failure_faces": str(artifact),
        "manifest": str(manifest_path),
        "summary": summary,
        "validation": validation,
        "manifest_check": block_check,
        "manifest_schema_check": schema_check,
        "block": block,
    }


def _moment_rel_error(src: CellSet, src_values, dst: CellSet, dst_values) -> float:
    def first_moment(cells: CellSet, values) -> np.ndarray:
        return np.einsum("i,ik->k", values * cells.measure, cells.centers)

    m_src = first_moment(src, src_values)
    m_dst = first_moment(dst, dst_values)
    norm = float(np.sum(np.abs(src_values) * src.measure) * np.max(src.extent_scale))
    return float(np.max(np.abs(m_src - m_dst)) / max(norm, np.finfo(float).tiny))


def _validate_manifest_schema(manifest: Dict[str, object]) -> Dict[str, object]:
    try:
        import jsonschema
    except ImportError:  # pragma: no cover - optional dependency
        return {"ok": None, "reason": "jsonschema not installed"}
    schema = json.loads((ROOT / "contracts" / "tensorphysics-run-manifest-v1.schema.json").read_text())
    try:
        jsonschema.validate(manifest, schema)
        return {"ok": True, "problems": []}
    except Exception as exc:  # pragma: no cover - reported, not raised
        return {"ok": False, "problems": [str(exc)]}


# --------------------------------------------------------------------------- #
# Figures
# --------------------------------------------------------------------------- #
def make_figures(mapping, clock, stability, outdir: Path) -> List[Dict[str, str]]:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    outdir.mkdir(parents=True, exist_ok=True)
    figures: List[Dict[str, str]] = []

    # 1. conservation errors across grid ratios (overlap vs nearest control)
    sweep = mapping["sweep"]
    ratios = [r["ratio"] for r in sweep]
    fig, ax = plt.subplots(figsize=(7.0, 4.2))
    x = np.arange(len(ratios))
    width = 0.2
    series = [
        ("mass (overlap)", [r["mass_rel_error"] for r in sweep], "#1f77b4"),
        ("momentum (overlap)", [r["momentum_rel_error"] for r in sweep], "#2ca02c"),
        ("energy (overlap)", [r["energy_rel_error"] for r in sweep], "#d62728"),
        ("mass (nearest control)", [r["control_mass_rel_error"] for r in sweep], "#7f7f7f"),
    ]
    floor = 1e-17
    for k, (label, values, color) in enumerate(series):
        ax.bar(x + (k - 1.5) * width, [max(v, floor) for v in values], width, label=label, color=color)
    ax.axhline(THRESHOLDS["B1_mass_rel_error"], ls="--", lw=1.0, color="#1f77b4", alpha=0.7)
    ax.axhline(THRESHOLDS["B2_momentum_rel_error"], ls="--", lw=1.0, color="#2ca02c", alpha=0.7)
    ax.axhline(THRESHOLDS["B9_grid_ratio_rel_error"], ls=":", lw=1.4, color="#d62728", alpha=0.8)
    ax.set_yscale("log")
    ax.set_xticks(x)
    ax.set_xticklabels([f"1:{r}" for r in ratios])
    ax.set_xlabel("source : target grid ratio (16x16 -> n x n)")
    ax.set_ylabel("relative conservation error (log)")
    ax.set_title("B1/B2/B3/B9  conservative mapping vs nearest-cell control")
    ax.legend(fontsize=8, ncol=2)
    ax.grid(True, axis="y", which="both", alpha=0.25)
    path = outdir / "conservation_errors.png"
    fig.tight_layout()
    fig.savefig(path, dpi=150)
    plt.close(fig)
    figures.append(
        {
            "name": "conservation_errors.png",
            "caption": "B1/B2/B3/B9: mass, momentum and energy conservation error of the "
            "intersection-based operator across grid ratios 1:1..1:10, against the "
            "nearest-cell control; dashed lines are the 0.5% / 1% / 2% thresholds.",
        }
    )

    # 2. coupling-point synchronisation error
    aligned = clock["aligned"]
    unaligned = clock["unaligned"]
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(10.0, 4.0))
    tol = THRESHOLDS["B4_max_skew_s"]
    ax1.semilogy(aligned.coupling_times, np.maximum(aligned.skew_series, 1e-18), "o-", ms=3, label="aligned (truncate last substep)")
    ax1.semilogy(unaligned.coupling_times, np.maximum(unaligned.skew_series, 1e-18), "s--", ms=3, label="unaligned (full steps)")
    ax1.axhline(tol, color="k", ls=":", lw=1.2, label=f"tolerance {tol:.0e} s")
    ax1.set_xlabel("coupling time (s)")
    ax1.set_ylabel("max |skew| at coupling point (s, log)")
    ax1.set_title("B4  coupling-point time skew")
    ax1.legend(fontsize=8)
    ax1.grid(True, which="both", alpha=0.25)

    refinements = [1, 2, 4, 8, 16]
    cfg = clock["coarse"].config
    errors = []
    for r in refinements:
        run = simulate_coupled_oscillators(cfg, refine=float(r))
        errors.append(compare_series(run, clock["reference"])["state_rel_l2"])
    ax2.loglog(refinements, np.maximum(errors, 1e-9), "o-", label="sub-cycled run vs reference")
    ax2.axhline(THRESHOLDS["B5_subcycle_rel_error"], color="k", ls=":", lw=1.2, label="5% threshold")
    ax2.set_xlabel("step refinement of the compared run")
    ax2.set_ylabel("state relative L2 error vs reference")
    ax2.set_title("B5  sub-cycling convergence (fast:slow = 10:1)")
    ax2.legend(fontsize=8)
    ax2.grid(True, which="both", alpha=0.25)
    path = outdir / "coupling_sync_error.png"
    fig.tight_layout()
    fig.savefig(path, dpi=150)
    plt.close(fig)
    figures.append(
        {
            "name": "coupling_sync_error.png",
            "caption": "B4/B5: left — |skew| between each solver's clock and the coupling "
            "point, aligned vs unaligned; right — error of the 10:1 sub-cycled run against "
            "the step-refined reference.",
        }
    )

    # 3. Lyapunov convergence
    fig, ax = plt.subplots(figsize=(7.0, 4.0))
    for label, style in (("damped", "o-"), ("conservative", "s-"), ("unstable_control", "^-")):
        res = stability["results"][label]
        if res.series.size:
            ax.semilogy(np.arange(1, res.series.size + 1), np.abs(res.series) + 1e-6, style, ms=2,
                        label=f"{label}: lambda={res.leading:+.4f}")
    ax.axhline(1e-6, color="k", ls=":", lw=1.0, label="|lambda| = 0 marker")
    ax.set_yscale("symlog", linthresh=1e-6)
    ax.set_xlabel("accumulated windows after transient")
    ax.set_ylabel("|running leading exponent| (1/s, symlog)")
    ax.set_title("B6  coupled-map stability: running Benettin estimate")
    ax.legend(fontsize=8)
    ax.grid(True, which="both", alpha=0.25)
    path = outdir / "lyapunov_convergence.png"
    fig.tight_layout()
    fig.savefig(path, dpi=150)
    plt.close(fig)
    figures.append(
        {
            "name": "lyapunov_convergence.png",
            "caption": "B6: running estimate of the leading Lyapunov exponent for the damped "
            "coupled map (stable), the conservative map (marginal, |lambda| ~ estimator noise) "
            "and the negative-damping control (unstable).",
        }
    )

    # 4. interface energy closure vs coupling interval
    intervals = [0.05, 0.025, 0.0125]
    fig, ax = plt.subplots(figsize=(6.4, 4.0))
    for model, style in (("hold", "s-"), ("linear", "o-")):
        closures = []
        for h in intervals:
            cfg = CoupledOscillatorConfig(
                coupling_interval=h, slow_substeps=4, ratio=10.0, t_end=1.0, partner_model=model
            )
            run = simulate_coupled_oscillators(cfg)
            closures.append(max(run.energy_report.closure_rel, 1e-18))
        ax.loglog(intervals, closures, style, label=f"partner model: {model}")
    ax.axhline(1e-2, color="k", ls=":", lw=1.2, label="1% budget threshold")
    ax.set_xlabel("coupling interval Δ (s)")
    ax.set_ylabel("interface energy closure residual (relative)")
    ax.set_title("B3 (coupled budget): zero-order hold is O(Δ), linear interface data is O(Δ²)")
    ax.invert_xaxis()
    ax.legend(fontsize=8)
    ax.grid(True, which="both", alpha=0.25)
    path = outdir / "energy_budget_closure.png"
    fig.tight_layout()
    fig.savefig(path, dpi=150)
    plt.close(fig)
    figures.append(
        {
            "name": "energy_budget_closure.png",
            "caption": "B3 coupled budget: |Σ E_in − Σ E_out − ΔE_stored| for zero-order-hold "
            "and linear interface data as the coupling interval is refined (first vs second order).",
        }
    )
    return figures


# --------------------------------------------------------------------------- #
# Main
# --------------------------------------------------------------------------- #
def run(
    ratios: Sequence[int] = GRID_RATIOS,
    source_cells: int = SOURCE_CELLS,
    artifact_dir: Optional[Path] = None,
    make_figs: bool = True,
) -> Dict[str, object]:
    """Execute every criterion.

    ``artifact_dir`` redirects the failure-face register, the run manifest and the
    figures (used by the tests so that a light run cannot overwrite the committed
    benchmark artifacts); ``make_figs=False`` skips figure rendering.
    """
    art_dir = Path(artifact_dir) if artifact_dir is not None else HERE
    case = default_case()
    mapping = run_mapping_criteria(case, ratios, source_cells=source_cells)
    clock = run_clock_criteria()
    stability = run_stability()
    negatives = run_negative_controls(case, source_cells=source_cells)
    contracts = run_artifact_contracts(case, mapping, art_dir)
    criteria = {}
    for part in (mapping, clock, stability, negatives, contracts):
        criteria.update(part["criteria"])

    fig_dir = art_dir / "figures"
    figures = make_figures(mapping, clock, stability, fig_dir) if make_figs else []
    energy_closure = clock["coarse"].energy_report.to_dict()
    results = {
        "benchmark": "cross_solver_coupling",
        "node": "P148W2-N02",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "environment": {
            "python": platform.python_version(),
            "numpy": np.__version__,
            "platform": platform.platform(),
            "git_commit": _git_commit(),
        },
        "case": {
            "domain": "unit square [0,1]^2 (synthetic, no physical model)",
            "source_mesh": f"uniform {source_cells}x{source_cells}",
            "ratios": [int(r) for r in ratios],
            "fields": ["density", "momentum(2)", "energy"],
            "random_seed": None,
            "determinism": "fully deterministic; no RNG is used anywhere in this benchmark",
        },
        "thresholds": THRESHOLDS,
        "criteria": criteria,
        "details": {
            "mapping_sweep": mapping["sweep"],
            "operator_summary": mapping["first"]["operator_summary"],
            "accuracy": mapping["first"]["accuracy"],
            "audit": mapping["first"]["audit"].to_dict(),
            "clock": clock["aligned"].summary(),
            "clock_unaligned": clock["unaligned"].summary(),
            "subcycling": {
                "comparison": compare_series(clock["coarse"], clock["reference"]),
                "run": clock["coarse"].summary(),
            },
            "energy_budget": energy_closure,
            "energy_budget_hold": clock["hold"].energy_report.to_dict(),
            "negative_controls": negatives["decoupled"].energy_report.to_dict(),
            "lyapunov": {k: v.to_dict() for k, v in stability["results"].items()},
            "lyapunov_estimator_check": stability["estimator_check"],
            "contracts": {
                "failure_faces": contracts["summary"],
                "failure_faces_validation": contracts["validation"],
                "manifest_coupling_clock": contracts["block"],
                "manifest_validation": contracts["manifest_check"],
                "manifest_schema_validation": contracts["manifest_schema_check"],
            },
        },
        "figures": figures,
        "artifacts": {
            "results": _rel(art_dir / "results.json"),
            "failure_faces": _rel(Path(contracts["failure_faces"])),
            "run_manifest": _rel(Path(contracts["manifest"])),
            "figures_dir": _rel(fig_dir),
        },
        "passed": all(c["passed"] for c in criteria.values()),
        "n_criteria": len(criteria),
        "n_passed": sum(1 for c in criteria.values() if c["passed"]),
    }
    return results


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", default=str(HERE / "results.json"))
    parser.add_argument("--emit-doc", default=str(ROOT / "docs" / "cross_solver_coupling.md"))
    parser.add_argument("--no-doc", action="store_true")
    args = parser.parse_args(argv)

    results = run()
    out = Path(args.out)
    out.write_text(json.dumps(results, indent=2))
    results["artifact_sha256"] = {out.name: _sha256_file(out)}
    out.write_text(json.dumps(results, indent=2))

    if not args.no_doc:
        from benchmarks.verified.cross_solver_coupling.emit_doc import emit_doc

        emit_doc(results, Path(args.emit_doc))

    for name, crit in results["criteria"].items():
        verdict = "PASS" if crit["passed"] else "FAIL"
        print(f"{name:>4} {verdict:>4}  {crit['metric']:<38} {crit['value']:.6e} "
              f"(threshold {crit['comparator']} {crit['threshold']:.6e})")
    print(f"\n{results['n_passed']}/{results['n_criteria']} criteria passed -> {out}")
    return 0 if results["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())