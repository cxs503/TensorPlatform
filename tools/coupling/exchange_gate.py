"""Machine-checked acceptance gate for conservative exchange + clock semantics.

Every criterion is computed from synthetic meshes and synthetic fields; no
domain quantity appears anywhere in this module (the C8 gate proves it by
scanning the source with the platform lexicon).

Criteria
--------
C1  amount conservation per quantity law: total, first moment and torque
    ``<= 1e-12`` for the conserved kinds (density / flux / extensive), and
    *affine reproduction* ``<= 1e-12`` for the precision-law kind (scalar),
    whose admissible law is not a sum constraint at all;
C2  non-negativity and no overshoot under the bounded projection;
C3  rigid-motion patch test (translation and rotation) ``<= 1e-12``;
C4  end-to-end coupled loop: mass / momentum / energy residual ``<= 1e-10``
    under three mappings;
C5  ablation: with the conservation correction switched off the drift is
    ``>= 1 %`` and is detected in 100 % of the trials (the gate has teeth);
C6  clock semantics: four exchange modes with declared precondition, error
    order and out-of-contract rule; three out-of-contract classes fail closed;
C7  idempotence and traceability: identical inputs give identical digests and a
    ``mapping_manifest.json`` records the transfer;
C8  zero domain-vocabulary hits over the mechanism package;
C9  pytest case count (>= 20, including the negative controls) and the
    architecture the gate actually ran on.

Run ``python -m tools.coupling.exchange_gate --json <path> --manifest <path>``.
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np

from .clock import ClockContract, ClockContractViolation, ClockMode, TimeSeriesExchange
from .mesh import BoxMesh, Field, QuantityKind
from .precision import (
    QUANTITY_LAWS,
    VectorField,
    rigid_motion_patch_test,
)
from .remap import (
    RemapError,
    RemapOperator,
    RemapSpec,
    remap,
    remap_vector,
)

TOTAL_TOL = 1e-12
MOMENT_TOL = 1e-12
TORQUE_TOL = 1e-12
PATCH_TOL = 1e-12
LOOP_TOL = 1e-10
DRIFT_TOL = 1e-2

__all__ = [
    "criteria",
    "criterion_c1",
    "criterion_c2",
    "criterion_c3",
    "criterion_c4",
    "criterion_c5",
    "criterion_c6",
    "criterion_c7",
    "criterion_c8",
    "criterion_c9",
    "coupled_loop",
    "clock_mode_table",
    "write_mapping_manifest",
    "run",
]


# --------------------------------------------------------------------- fixtures
def _blob(mesh: BoxMesh, centre: Sequence[float], width: float, amplitude: float = 1.0) -> np.ndarray:
    """Smooth positive synthetic field (no physical meaning attached)."""
    d2 = ((mesh.centers - np.asarray(centre, dtype=float)) ** 2).sum(axis=1)
    return amplitude * np.exp(-d2 / (2.0 * width**2))


def _vector_blob(mesh: BoxMesh, seed: int = 0) -> np.ndarray:
    rng = np.random.default_rng(seed)
    return rng.normal(size=(mesh.n_cells, mesh.dim)) + 0.5


def _surfaces(n_x: int, n_y_slab: int = 3) -> Tuple[BoxMesh, BoxMesh]:
    """Two surface meshes sharing the normal partition (a well-posed flux pair)."""
    src = BoxMesh.uniform([0.0, 0.0], [2.0, 2.0], [n_x, n_y_slab], name="surface-src")
    dst = BoxMesh.uniform([0.0, 0.0], [2.0, 2.0], [n_x + 3, n_y_slab], name="surface-dst")
    return src, dst


def _bulk(n: Sequence[int], m: Sequence[int]) -> Tuple[BoxMesh, BoxMesh]:
    lo, hi = [0.0, 0.0], [2.0, 2.0]
    return (
        BoxMesh.uniform(lo, hi, list(n), name="bulk-src"),
        BoxMesh.uniform(lo, hi, list(m), name="bulk-dst"),
    )


def _sha256(values: np.ndarray) -> str:
    return hashlib.sha256(np.ascontiguousarray(values, dtype=np.float64).tobytes()).hexdigest()


# ------------------------------------------------------------------------- C1
def criterion_c1() -> Dict[str, Any]:
    """Amount conservation per quantity law (total, first moment, torque)."""
    rows: List[Dict[str, Any]] = []

    for layout, (src, dst) in (
        ("shifted", _bulk((6, 6), (9, 9))),
        ("nested", (lambda s: (s, s.refined(2)))(BoxMesh.uniform([0.0, 0.0], [2.0, 2.0], [6, 6]))),
    ):
        for kind in (QuantityKind.DENSITY, QuantityKind.EXTENSIVE):
            base = _blob(src, (0.8, 1.1), 0.5, 3.0) + 0.25
            field = Field(base, src, kind, name=f"{kind.value}-{layout}")
            for op in (RemapOperator.OVERLAP_P1, RemapOperator.RBF_CONSERVATIVE):
                spec = RemapSpec(operator=op, quantity_kind=kind, moment_exact=True)
                res = remap(field, src, dst, kind, spec=spec)
                m0 = res.field.first_moment()
                m1 = field.first_moment()
                mom = float(np.max(np.abs(m0 - m1))) / max(float(np.max(np.abs(m1))), 1e-300)
                rows.append({
                    "quantity": kind.value, "layout": layout, "operator": op.value,
                    "gate": "conserved", "total_residual": res.report.rel_residual,
                    "first_moment_residual": mom,
                    "moment_reference": "origin",
                    "condition_number": res.report.moment_condition,
                })

        # vector quantity: same law per component, plus torque
        vec = VectorField(_vector_blob(src, seed=7), src, QuantityKind.DENSITY, name=f"vec-{layout}")
        for op in (RemapOperator.OVERLAP_P1, RemapOperator.RBF_CONSERVATIVE):
            spec = RemapSpec(operator=op, quantity_kind=QuantityKind.DENSITY, moment_exact=True)
            res = remap_vector(vec, src, dst, QuantityKind.DENSITY, spec=spec)
            rows.append({
                "quantity": "vector", "layout": layout, "operator": op.value,
                "gate": "conserved", "total_residual": res.total_residual,
                "first_moment_residual": res.first_moment_residual,
                "torque_residual": res.torque_residual, "moment_reference": "origin",
            })

    # flux: per-unit-area quantity on a surface pair
    src_s, dst_s = _surfaces(6)
    flux = Field(_blob(src_s, (0.9, 1.0), 0.6, 2.0) - 1.0, src_s, QuantityKind.FLUX, name="flux")
    for op in (RemapOperator.OVERLAP_L2, RemapOperator.RBF_CONSERVATIVE):
        spec = RemapSpec(operator=op, quantity_kind=QuantityKind.FLUX, moment_exact=True,
                         face_normal=-1)
        res = remap(flux, src_s, dst_s, QuantityKind.FLUX, spec=spec)
        m0, m1 = res.field.first_moment(), flux.first_moment()
        rows.append({
            "quantity": "flux", "layout": "surface", "operator": op.value,
            "gate": "conserved", "total_residual": res.report.rel_residual,
            "first_moment_residual": float(np.max(np.abs(m0 - m1))) /
            max(float(np.max(np.abs(m1))), 1e-300),
            "moment_reference": "origin",
        })

    # scalar: the admissible law is precision (affine reproduction), not a sum
    src_b, dst_b = _bulk((6, 6), (9, 9))
    g = np.array([0.7, -1.3])
    affine = 2.5 + src_b.centers @ g
    scal = Field(affine, src_b, QuantityKind.SCALAR, name="scalar")
    res = remap(scal, src_b, dst_b, QuantityKind.SCALAR,
                spec=RemapSpec(operator=RemapOperator.OVERLAP_P1, quantity_kind=QuantityKind.SCALAR))
    affine_err = float(np.max(np.abs(res.field.values - (2.5 + dst_b.centers @ g)))) / float(
        np.max(np.abs(2.5 + dst_b.centers @ g)))
    refusal = None
    try:  # a sum constraint on a precision-law quantity must be refused
        remap(scal, src_b, dst_b, QuantityKind.SCALAR,
              spec=RemapSpec(quantity_kind=QuantityKind.SCALAR, moment_exact=True))
        refusal = "ACCEPTED"
    except RemapError as exc:
        refusal = f"refused: {exc}"
    rows.append({
        "quantity": "scalar", "layout": "shifted", "operator": "overlap_p1",
        "gate": "precision", "affine_reproduction_error": affine_err,
        "sum_constraint": refusal,
    })

    worst_total = max(r.get("total_residual", 0.0) for r in rows)
    worst_moment = max(r.get("first_moment_residual", 0.0) for r in rows)
    worst_torque = max((r.get("torque_residual", 0.0) for r in rows), default=0.0)
    passed = bool(
        worst_total <= TOTAL_TOL
        and worst_moment <= MOMENT_TOL
        and worst_torque <= TORQUE_TOL
        and affine_err <= MOMENT_TOL
        and refusal != "ACCEPTED"
    )
    return {
        "thresholds": {"total": TOTAL_TOL, "first_moment": MOMENT_TOL, "torque": TORQUE_TOL},
        "worst_total_residual": worst_total,
        "worst_first_moment_residual": worst_moment,
        "worst_torque_residual": worst_torque,
        "scalar_affine_reproduction_error": affine_err,
        "scalar_sum_constraint": refusal,
        "laws": {k: vars(v) for k, v in QUANTITY_LAWS.items()},
        "rows": rows,
        "passed": passed,
    }


# ------------------------------------------------------------------------- C2
def criterion_c2() -> Dict[str, Any]:
    """Non-negativity and no overshoot under the bounded projection."""
    src, dst = _bulk((6, 6), (11, 11))
    density = _blob(src, (0.9, 0.9), 0.35, 5.0)
    field = Field(density, src, QuantityKind.DENSITY, name="positive")
    rows = []
    for op in (RemapOperator.OVERLAP_L2, RemapOperator.OVERLAP_P1, RemapOperator.RBF_CONSERVATIVE):
        spec = RemapSpec(operator=op, quantity_kind=QuantityKind.DENSITY,
                         positivity=True, enforce_bounds=True, moment_exact=True)
        res = remap(field, src, dst, QuantityKind.DENSITY, spec=spec)
        vals = res.field.values
        rows.append({
            "operator": op.value,
            "min_dst": float(vals.min()),
            "max_dst": float(vals.max()),
            "min_src": float(density.min()),
            "max_src": float(density.max()),
            "nonneg_ok": bool(vals.min() >= -1e-14),
            "bounded_ok": bool(vals.max() <= density.max() * (1.0 + 1e-12)),
            "total_residual": res.report.rel_residual,
            "positivity_projected": res.report.positivity_projected,
            "bounds_projected": res.report.bounds_projected,
            "moment_first_residual_after_bounds": res.report.moment_first_residual,
            "moment_broken_by_bounds": res.report.moment_broken_by_bounds,
        })
    passed = all(r["nonneg_ok"] and r["bounded_ok"] and r["total_residual"] <= TOTAL_TOL for r in rows)
    return {
        "thresholds": {"min_value": 0.0, "max_value": "source maximum",
                       "total": TOTAL_TOL},
        "note": "a bound projection redistributes locally; the exact first moment and a hard "
                "bound are in tension, so the moment residual is reported (not gated) here",
        "rows": rows,
        "passed": bool(passed),
    }


# ------------------------------------------------------------------------- C3
def criterion_c3() -> Dict[str, Any]:
    """Rigid-motion patch test for a vector field (translation and rotation)."""
    src = BoxMesh.uniform([0.0, 0.0, 0.0], [2.0, 2.0, 2.0], [4, 4, 4], name="patch-src")
    layouts = {
        "nested": src.refined(2),
        "shifted-7": BoxMesh.uniform([0.0, 0.0, 0.0], [2.0, 2.0, 2.0], [7, 7, 7], name="patch-7"),
        "shifted-5": BoxMesh.uniform([0.0, 0.0, 0.0], [2.0, 2.0, 2.0], [5, 5, 5], name="patch-5"),
    }
    motions = {
        "translation": ((1.5, -2.0, 0.5), (0.0, 0.0, 0.0)),
        "rotation": ((0.3, 0.7, -1.1), (0.1, 0.2, 0.3)),
    }
    rows = []
    for layout, dst in layouts.items():
        for motion, (tr, rot) in motions.items():
            out = rigid_motion_patch_test(src, dst, tr, rot)
            rows.append({"layout": layout, "motion": motion,
                         "errors": {k: float(v) for k, v in out["errors"].items()}})
    worst = max(r["errors"]["overlap_p1"] for r in rows)
    baseline = max(r["errors"]["overlap_l2"] for r in rows)
    passed = bool(worst <= PATCH_TOL)
    return {
        "threshold": PATCH_TOL,
        "worst_second_order_error": worst,
        "first_order_baseline_error": baseline,
        "first_order_baseline_detected": bool(baseline >= DRIFT_TOL),
        "rows": rows,
        "passed": passed,
    }


# ------------------------------------------------------------- coupled loop (C4/C5)
def _diffuse(values: np.ndarray, shape: Tuple[int, ...], spacing: Tuple[float, ...],
             diffusivity: float, dt: float) -> np.ndarray:
    """Conservative zero-flux diffusion step on a structured grid (Neumann edges)."""
    u = np.asarray(values, dtype=float).reshape(shape)
    lap = np.zeros_like(u)
    for axis, h in enumerate(spacing):
        fwd = np.roll(u, -1, axis=axis)
        bwd = np.roll(u, 1, axis=axis)
        # replicate edges => zero flux across the domain boundary
        edge_hi = [slice(None)] * u.ndim
        edge_lo = [slice(None)] * u.ndim
        edge_hi[axis] = -1
        edge_lo[axis] = 0
        fwd[tuple(edge_hi)] = u[tuple(edge_hi)]
        bwd[tuple(edge_lo)] = u[tuple(edge_lo)]
        lap += (fwd + bwd - 2.0 * u) / h**2
    return (u + diffusivity * dt * lap).reshape(-1)


def coupled_loop(
    operator: RemapOperator = RemapOperator.OVERLAP_P1,
    cycles: int = 12,
    correct: bool = True,
    moment_exact: bool = True,
    diffusivity: float = 0.05,
    dt: float = 0.01,
) -> Dict[str, Any]:
    """Two synthetic solvers exchanging three quantities for ``cycles`` cycles.

    Each side advances with a conservative step, then overwrites its partner's
    state with a remapped field (loose coupling).  The tracked invariants are the
    exchanged *amounts* — amount, momentum and a third conserved scalar (an
    "energy-like" density): the operator must not leak any of them.  Kinetic
    energy ``sum(rho u^2)/2`` is reported separately as a diagnostic, because it
    is not a conserved quantity of an exchange and must not be gated as one.
    """
    shape_a, shape_b = (8, 8), (11, 11)
    mesh_a = BoxMesh.uniform([0.0, 0.0], [2.0, 2.0], list(shape_a), name="loop-a")
    mesh_b = BoxMesh.uniform([0.0, 0.0], [2.0, 2.0], list(shape_b), name="loop-b")
    spacing_a = tuple(2.0 / n for n in shape_a)
    spacing_b = tuple(2.0 / n for n in shape_b)

    spec = RemapSpec(operator=operator, quantity_kind=QuantityKind.DENSITY,
                     apply_conservation_correction=correct, moment_exact=moment_exact
                     and correct)
    vec_spec = spec

    rho_a = Field(1.0 + _blob(mesh_a, (0.6, 1.4), 0.4, 4.0), mesh_a, QuantityKind.DENSITY, "rho-a")
    mom_a = VectorField(_vector_blob(mesh_a, seed=11), mesh_a, QuantityKind.DENSITY, "mom-a")
    eng_a = Field(_blob(mesh_a, (1.3, 0.7), 0.6, 2.0), mesh_a, QuantityKind.DENSITY, "eng-a")

    # the second solver starts as the remapped state of the first, so the pair is
    # consistent before anything is measured; otherwise the first exchange would
    # mix two independent initial states and the "residual" would be an
    # initialisation artefact rather than a leak
    rho_b = remap(rho_a, mesh_a, mesh_b, QuantityKind.DENSITY, spec=spec).field
    eng_b = remap(eng_a, mesh_a, mesh_b, QuantityKind.DENSITY, spec=spec).field
    mom_b = remap_vector(mom_a, mesh_a, mesh_b, QuantityKind.DENSITY, spec=vec_spec).field

    def totals(rho, mom, eng) -> Dict[str, float]:
        return {
            "amount": rho.total,
            "momentum_x": float(mom.total[0]),
            "momentum_y": float(mom.total[1]),
            "energy": eng.total,
            "kinetic": 0.5 * float(
                (rho.values * np.sum(mom.values**2, axis=1) * rho.mesh.volume).sum()),
        }

    def pair(*fields) -> Dict[str, float]:
        acc = {"amount": 0.0, "momentum_x": 0.0, "momentum_y": 0.0, "energy": 0.0,
               "kinetic": 0.0}
        for f in fields:
            for k, v in f.items():
                acc[k] += v
        return acc

    initial = pair(totals(rho_a, mom_a, eng_a), totals(rho_b, mom_b, eng_b))
    history = []
    for _ in range(cycles):
        rho_a = rho_a.with_values(_diffuse(rho_a.values, shape_a, spacing_a, diffusivity, dt))
        rho_b = rho_b.with_values(_diffuse(rho_b.values, shape_b, spacing_b, diffusivity, dt))
        eng_a = eng_a.with_values(_diffuse(eng_a.values, shape_a, spacing_a, diffusivity, dt))
        eng_b = eng_b.with_values(_diffuse(eng_b.values, shape_b, spacing_b, diffusivity, dt))

        rho_b = remap(rho_a, mesh_a, mesh_b, QuantityKind.DENSITY, spec=spec).field
        rho_a = remap(rho_b, mesh_b, mesh_a, QuantityKind.DENSITY, spec=spec).field
        eng_b = remap(eng_a, mesh_a, mesh_b, QuantityKind.DENSITY, spec=spec).field
        eng_a = remap(eng_b, mesh_b, mesh_a, QuantityKind.DENSITY, spec=spec).field
        mom_b = remap_vector(mom_a, mesh_a, mesh_b, QuantityKind.DENSITY, spec=vec_spec).field
        mom_a = remap_vector(mom_b, mesh_b, mesh_a, QuantityKind.DENSITY, spec=vec_spec).field

        now = pair(totals(rho_a, mom_a, eng_a), totals(rho_b, mom_b, eng_b))
        history.append({
            "cycle": len(history) + 1,
            "amount": abs(now["amount"] - initial["amount"]) / abs(initial["amount"]),
            "momentum": max(abs(now["momentum_x"] - initial["momentum_x"]),
                            abs(now["momentum_y"] - initial["momentum_y"]))
            / max(abs(initial["momentum_x"]), abs(initial["momentum_y"])),
            "energy": abs(now["energy"] - initial["energy"]) / abs(initial["energy"]),
            "kinetic_diagnostic": abs(now["kinetic"] - initial["kinetic"])
            / max(abs(initial["kinetic"]), 1e-300),
        })

    residual = {k: float(history[-1][k]) for k in ("amount", "momentum", "energy")}
    return {
        "operator": operator.value,
        "correction": bool(correct),
        "moment_exact": bool(moment_exact and correct),
        "cycles": cycles,
        "residual": residual,
        "worst_residual": float(max(residual.values())),
        "history": history,
    }


def criterion_c4() -> Dict[str, Any]:
    """End-to-end coupled loop under three mappings."""
    rows = [coupled_loop(op, moment_exact=True) for op in
            (RemapOperator.OVERLAP_L2, RemapOperator.OVERLAP_P1, RemapOperator.RBF_CONSERVATIVE)]
    worst = max(r["worst_residual"] for r in rows)
    return {"threshold": LOOP_TOL, "rows": rows, "worst_residual": worst,
            "passed": bool(worst <= LOOP_TOL)}


def criterion_c5(seeds: Sequence[int] = tuple(range(12))) -> Dict[str, Any]:
    """Positive control (ablation): without the correction the drift must be caught.

    The controlled comparison is a **single exchange** on a coarse source and a
    fine destination with a sharply curved field: the nearest-neighbour operator
    then carries the full midpoint-rule error of the amount, so switching the
    correction off produces a drift far above 1 %, while switching it on returns
    the amount to round-off.  The coupled loop is measured as well, but it is
    reported as a secondary observation: the loop re-projects the state every
    cycle, which damps a per-exchange drift and would make it a weaker control.
    """
    per_seed = []
    for seed in seeds:
        src = BoxMesh.uniform([0.0, 0.0], [2.0, 2.0], [4, 4], name=f"ctrl-src-{seed}")
        dst = BoxMesh.uniform([0.0, 0.0], [2.0, 2.0], [13, 13], name=f"ctrl-dst-{seed}")
        rng = np.random.default_rng(seed)
        centre = np.array([0.7 + 0.4 * rng.random(), 0.7 + 0.4 * rng.random()])
        values = _blob(src, centre, 0.25, 5.0) + 0.01
        field = Field(values, src, QuantityKind.DENSITY, name=f"ctrl-{seed}")
        off = remap(field, src, dst, QuantityKind.DENSITY,
                    spec=RemapSpec(operator=RemapOperator.NEAREST_SCALED,
                                   quantity_kind=QuantityKind.DENSITY,
                                   apply_conservation_correction=False))
        on = remap(field, src, dst, QuantityKind.DENSITY,
                   spec=RemapSpec(operator=RemapOperator.NEAREST_SCALED,
                                  quantity_kind=QuantityKind.DENSITY))
        drift = float(off.report.raw_rel_residual)
        per_seed.append({"seed": seed, "drift_without_correction": drift,
                         "drift_with_correction": float(on.report.rel_residual),
                         "detected": bool(drift >= DRIFT_TOL)})
    rate = sum(1 for d in per_seed if d["detected"]) / len(per_seed)
    control = coupled_loop(RemapOperator.NEAREST_SCALED, correct=False)
    ablated = coupled_loop(RemapOperator.NEAREST_SCALED, correct=True)
    worst_control = max(d["drift_without_correction"] for d in per_seed)
    best_control = min(d["drift_without_correction"] for d in per_seed)
    worst_on = max(d["drift_with_correction"] for d in per_seed)
    return {
        "threshold_drift": DRIFT_TOL,
        "per_seed": per_seed,
        "detection_rate": rate,
        "worst_drift_without_correction": worst_control,
        "smallest_drift_without_correction": best_control,
        "worst_drift_with_correction": worst_on,
        "loop_without_correction": control,
        "loop_with_correction": ablated,
        "passed": bool(rate >= 1.0 and best_control >= DRIFT_TOL and worst_on <= TOTAL_TOL),
    }


# ------------------------------------------------------------------------- C6
def clock_mode_table() -> List[Dict[str, Any]]:
    """Declared precondition, error order and out-of-contract rule per mode."""
    return [
        {"mode": ClockMode.SYNC.value, "precondition": "a stored sample exists at t",
         "declared_order": 0, "off_grid_rule": "refuse (fail-closed)",
         "out_of_range_rule": "refuse"},
        {"mode": ClockMode.LINEAR_INTERPOLATION.value,
         "precondition": "two stored samples bracket t",
         "declared_order": 2, "off_grid_rule": "interpolate inside the stored span",
         "out_of_range_rule": "refuse"},
        {"mode": ClockMode.ONE_STEP_EXTRAPOLATION.value,
         "precondition": ">= 2 stored samples and t beyond the last one",
         "declared_order": 2, "off_grid_rule": "extrapolate at most max_extrapolation_steps",
         "out_of_range_rule": "refuse beyond the declared horizon"},
        {"mode": ClockMode.EVENT_TRIGGERED.value,
         "precondition": "an event was published and the age <= max_staleness_steps",
         "declared_order": 0, "off_grid_rule": "hold the last event within the staleness bound",
         "out_of_range_rule": "refuse when stale"},
    ]


def _signal(t: float) -> float:
    """Smooth probe signal with non-vanishing curvature at every probe point."""
    return np.cos(1.7 * t) + 0.3 * t


def _measured_order(mode: ClockMode, refinements: Sequence[int] = (8, 16, 32, 64)) -> Dict[str, Any]:
    """Empirical behaviour of an exchange mode against a smooth analytic signal."""
    errors: List[float] = []
    regime = "convergent"
    for n in refinements:
        dt = 1.0 / n
        contract = ClockContract(dt_master=1.0, dt_sub=dt, mode=mode, max_extrapolation_steps=1,
                                 max_staleness_steps=1)
        ex = TimeSeriesExchange(contract, name=f"order-{mode.value}")
        times = np.arange(n + 1) * dt
        if mode is ClockMode.EVENT_TRIGGERED:
            for t in times:
                ex.publish_event(float(t), np.array([_signal(float(t))]))
            probe = float(times[-1])
        elif mode is ClockMode.ONE_STEP_EXTRAPOLATION:
            for t in times:
                ex.store(float(t), np.array([_signal(float(t))]))
            probe = float(times[-1] + dt)          # one sub-step beyond the data
        elif mode is ClockMode.SYNC:
            for t in times:
                ex.store(float(t), np.array([_signal(float(t))]))
            probe = float(times[-1])               # an admissible (stored) time
        else:
            for t in times:
                ex.store(float(t), np.array([_signal(float(t))]))
            # half a sub-step before an interior sample: the curvature there is
            # O(1) and stable, so the order measurement is not pre-asymptotic
            probe = float(0.5 - 0.5 * dt)
        try:
            errors.append(abs(float(ex.request(probe).values[0]) - _signal(probe)))
        except ClockContractViolation as exc:
            errors.append(float("nan"))
            regime = f"refused: {exc}"
    if any(not np.isfinite(e) for e in errors):
        return {"mode": mode.value, "errors": errors, "measured_order": None, "regime": regime}
    if max(errors) == 0.0:
        return {"mode": mode.value, "errors": errors, "measured_order": 0.0,
                "regime": "exact at admissible times (no interpolation performed)"}
    slopes = np.diff(np.log(errors)) / np.diff(np.log([1.0 / n for n in refinements]))
    return {"mode": mode.value, "errors": errors, "measured_order": float(np.mean(slopes)),
            "regime": regime}


def criterion_c6() -> Dict[str, Any]:
    """Out-of-contract clock requests fail closed; modes carry declared semantics."""
    refusals = []

    def expect_refusal(name: str, fn) -> None:
        try:
            fn()
            refusals.append({"case": name, "refused": False, "reason": "ACCEPTED"})
        except ClockContractViolation as exc:
            refusals.append({"case": name, "refused": True, "reason": str(exc)[:120]})

    expect_refusal("non_integer_ratio", lambda: ClockContract(dt_master=1.0, dt_sub=0.3))
    expect_refusal("sub_step_larger_than_master", lambda: ClockContract(dt_master=0.1, dt_sub=0.25))

    def out_of_range():
        c = ClockContract(dt_master=1.0, dt_sub=0.25, mode=ClockMode.LINEAR_INTERPOLATION)
        ex = TimeSeriesExchange(c)
        for t in (0.0, 0.25, 0.5):
            ex.store(t, np.array([t]))
        ex.request(9.0)

    expect_refusal("sample_time_out_of_range", out_of_range)

    def over_extrapolation():
        c = ClockContract(dt_master=1.0, dt_sub=0.25, mode=ClockMode.ONE_STEP_EXTRAPOLATION,
                          max_extrapolation_steps=1)
        ex = TimeSeriesExchange(c)
        for t in (0.0, 0.25, 0.5):
            ex.store(t, np.array([t]))
        ex.request(0.5 + 3 * 0.25)   # three sub-steps beyond the data, one allowed

    expect_refusal("extrapolation_steps_exceeded", over_extrapolation)

    orders = {o["mode"]: o for o in (_measured_order(m) for m in ClockMode)}
    linear = orders[ClockMode.LINEAR_INTERPOLATION.value]
    extrap = orders[ClockMode.ONE_STEP_EXTRAPOLATION.value]
    order_ok = bool(linear["measured_order"] is not None and linear["measured_order"] >= 1.9)
    extrapolation_riskier = bool(
        extrap["errors"][-1] > linear["errors"][-1]
    )  # the same step, but outside the data span: larger error, hence the step limit
    passed = bool(all(r["refused"] for r in refusals) and order_ok and extrapolation_riskier)
    return {"refusals": refusals, "modes": clock_mode_table(),
            "measured_orders": list(orders.values()),
            "declared_order_verified": order_ok,
            "extrapolation_error_exceeds_interpolation": extrapolation_riskier,
            "all_refused": bool(all(r["refused"] for r in refusals)), "passed": passed}


# ------------------------------------------------------------------------- C7
def write_mapping_manifest(path: str, records: Sequence[Dict[str, Any]],
                           thresholds: Dict[str, Any], extra: Optional[Dict[str, Any]] = None) -> dict:
    """Write the traceability manifest of a set of exchanges."""
    manifest = {
        "schema": "coupling.mapping_manifest/1",
        "generated_by": "tools.coupling.exchange_gate",
        "architecture": os.uname().machine,
        "numpy_version": np.__version__,
        "thresholds": thresholds,
        "records": list(records),
    }
    if extra:
        manifest.update(extra)
    body = json.dumps(manifest, indent=2, sort_keys=True)
    manifest["manifest_digest"] = hashlib.sha256(body.encode()).hexdigest()
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(json.dumps(manifest, indent=2, sort_keys=True))
    return manifest


def criterion_c7() -> Dict[str, Any]:
    """Identical inputs give identical digests, and the manifest records them."""
    src, dst = _bulk((6, 6), (9, 9))
    field = Field(_blob(src, (1.0, 1.0), 0.5, 2.0), src, QuantityKind.DENSITY, name="digest")
    spec = RemapSpec(operator=RemapOperator.OVERLAP_P1, quantity_kind=QuantityKind.DENSITY,
                     moment_exact=True)
    first = remap(field, src, dst, QuantityKind.DENSITY, spec=spec)
    second = remap(field, src, dst, QuantityKind.DENSITY, spec=spec)
    same_field = _sha256(first.field.values) == _sha256(second.field.values)
    same_remap = first.report.remap_digest == second.report.remap_digest
    records = [{
        "operator": spec.operator.value,
        "quantity_kind": spec.quantity_kind.value,
        "spec_digest": spec.digest(),
        "src_digest": first.report.src_digest,
        "dst_mesh_digest": dst.digest(),
        "dst_digest": first.report.dst_digest,
        "remap_digest": first.report.remap_digest,
        "field_sha256": _sha256(first.field.values),
        "second_run_field_sha256": _sha256(second.field.values),
        "sum_rel_residual": first.report.rel_residual,
        "first_moment_residual": first.report.moment_first_residual,
        "moment_condition": first.report.moment_condition,
    }]
    manifest = write_mapping_manifest(
        "artifacts/coupling-exchange-n02/mapping_manifest.json", records,
        {"total": TOTAL_TOL, "moment": MOMENT_TOL, "torque": TORQUE_TOL, "loop": LOOP_TOL},
        extra={"criterion": "C7", "idempotent": bool(same_field and same_remap)},
    )
    return {"same_field_digest": bool(same_field), "same_remap_digest": bool(same_remap),
            "field_sha256": records[0]["field_sha256"],
            "manifest_path": "artifacts/coupling-exchange-n02/mapping_manifest.json",
            "manifest_digest": manifest["manifest_digest"],
            "passed": bool(same_field and same_remap and len(manifest["records"]) == 1)}


# ------------------------------------------------------------------------- C8
def criterion_c8() -> Dict[str, Any]:
    """Zero domain-vocabulary hits over the mechanism package and its tests."""
    root = Path(__file__).resolve().parents[2]
    from tools.evidence_domain_scan import load_lexicon, scan_paths

    package = root / "tools" / "coupling"
    targets = [str(p.relative_to(root)) for p in sorted(package.rglob("*.py"))]
    test_file = root / "tests" / "test_coupling_exchange_gate.py"
    if test_file.exists():
        targets.append(str(test_file.relative_to(root)))
    result = scan_paths(targets, lexicon=load_lexicon(), root=root)
    return {"scanned_files": result["scanned_files"], "hits": result["hits"],
            "targets": targets, "passed": bool(result["hits"] == [])}


# ------------------------------------------------------------------------- C9
def criterion_c9() -> Dict[str, Any]:
    """pytest inventory of the gate's own suite (case count and negative controls)."""
    import ast

    path = Path(__file__).resolve().parents[2] / "tests" / "test_coupling_exchange_gate.py"
    if not path.exists():
        return {"path": str(path), "n_tests": 0, "negative_controls": [], "passed": False}
    tree = ast.parse(path.read_text())
    tests = [n.name for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name.startswith("test_")]
    source = path.read_text()
    n_negative = sum(1 for t in tests if "negative" in t or "control" in t or "refus" in t)
    return {
        "path": str(path.relative_to(Path(__file__).resolve().parents[2])),
        "n_tests": len(tests),
        "negative_controls": n_negative,
        "tests": tests,
        "architecture": os.uname().machine,
        "dual_architecture_note": "the control plane and the compute cluster are both "
                                  "loongarch64; the x86 leg is the repository CI job, not "
                                  "measurable from this worktree",
        "passed": bool(len(tests) >= 20 and n_negative >= 4),
        "uses_pytest_raises": bool("pytest.raises" in source),
    }


# ------------------------------------------------------------------------- run all
def criteria(include_scan: bool = True) -> Dict[str, Any]:
    """Compute every criterion; a criterion that raises is reported honestly."""
    out: Dict[str, Any] = {}
    for name, fn in (("C1", criterion_c1), ("C2", criterion_c2), ("C3", criterion_c3),
                     ("C4", criterion_c4), ("C5", criterion_c5), ("C6", criterion_c6),
                     ("C7", criterion_c7), ("C9", criterion_c9)):
        try:
            out[name] = fn()
        except Exception as exc:  # pragma: no cover - honest failure reporting
            out[name] = {"passed": False, "error": f"{type(exc).__name__}: {exc}"}
    if include_scan:
        try:
            out["C8"] = criterion_c8()
        except Exception as exc:  # pragma: no cover
            out["C8"] = {"passed": False, "error": f"{type(exc).__name__}: {exc}"}
    out["summary"] = {
        "criteria": sorted(k for k in out if k.startswith("C")),
        "passed": sorted(k for k, v in out.items() if k.startswith("C") and v.get("passed")),
        "failed": sorted(k for k, v in out.items() if k.startswith("C") and not v.get("passed")),
        "all_passed": all(v.get("passed") for k, v in out.items() if k.startswith("C")),
        "architecture": os.uname().machine,
    }
    return out


def run(json_path: Optional[str] = None, manifest_path: Optional[str] = None) -> Dict[str, Any]:
    result = criteria()
    manifest = write_mapping_manifest(
        manifest_path or "artifacts/coupling-exchange-n02/mapping_manifest.json",
        [{"criterion": k, "passed": v.get("passed"), "worst": _worst(v)} for k, v in result.items()
         if k.startswith("C")],
        {"total": TOTAL_TOL, "moment": MOMENT_TOL, "torque": TORQUE_TOL,
         "patch": PATCH_TOL, "loop": LOOP_TOL, "drift": DRIFT_TOL},
        extra={"summary": result["summary"]},
    )
    result["manifest"] = manifest
    if json_path:
        Path(json_path).parent.mkdir(parents=True, exist_ok=True)
        Path(json_path).write_text(json.dumps(result, indent=2, sort_keys=True, default=_fallback))
    return result


def _worst(value: Any) -> Any:
    if not isinstance(value, dict):
        return None
    for key in ("worst_residual", "worst_first_moment_residual", "worst_torque_residual",
                "worst_second_order_error", "detection_rate", "all_refused"):
        if key in value:
            return value[key]
    return None


def _fallback(obj: Any) -> Any:
    if isinstance(obj, (np.floating, np.integer)):
        return obj.item()
    if isinstance(obj, np.ndarray):
        return obj.tolist()
    return str(obj)


def main(argv: Optional[Sequence[str]] = None) -> int:  # pragma: no cover - CLI
    """Write the acceptance artefact; ``--json``/``--manifest`` override the paths.

    Parsed by hand: this module is a core mechanism module, so it must not pull in
    a CLI framework (the import-purity gate checks exactly that).
    """
    args = list(sys.argv[1:] if argv is None else argv)
    opts = {"--json": "artifacts/coupling-exchange-n02/acceptance.json",
            "--manifest": "artifacts/coupling-exchange-n02/mapping_manifest.json"}
    it = iter(args)
    for token in it:
        if token in opts:
            try:
                opts[token] = next(it)
            except StopIteration:
                raise SystemExit(f"{token} needs a path")
        else:
            raise SystemExit(f"unknown argument {token!r}")
    result = run(opts["--json"], opts["--manifest"])
    print(json.dumps(result["summary"], indent=2))
    return 0 if result["summary"]["all_passed"] else 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())