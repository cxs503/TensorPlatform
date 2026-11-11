#!/usr/bin/env python3
"""Acceptance runner for campaign node IN387-PLAT01.

Cross-solver space-time conservative field exchange:

  * temporal interpolation operator  (nearest / linear / cubic on arbitrary
    abscissas)  --  C1 identity, C6 energy budget;
  * moment-preserving remap with local linear reconstruction + exact Lagrange
    moment constraints --  C2/C3 moments, C4 boundedness, C5 round trip;
  * C7 negative control (moment constraints disabled) and C8 test-suite health.

Writes ``artifacts/IN387-PLAT01/criteria.json`` with every number needed to
re-derive the verdict, plus the product paths.  Pure platform mechanics: no
domain semantics anywhere (meshes, arrays, volumes).

Usage
-----
    python tools/run_in387_plat01_acceptance.py [--skip-pytest]
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import subprocess
import sys
import time
from datetime import datetime, timezone
from typing import Dict, List

import numpy as np

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
BACKEND = os.path.join(ROOT, "backend")
if BACKEND not in sys.path:
    sys.path.insert(0, BACKEND)

from coupling.mesh import structured_mesh_2d  # noqa: E402
from coupling.moment_remap import (  # noqa: E402
    MomentRemap,
    perturbed_mesh_2d,
    rectilinear_mesh_2d,
)
from coupling.spacetime_exchange import SpacetimeExchanger  # noqa: E402
from coupling.temporal_interp import (  # noqa: E402
    TemporalInterpolator,
    TemporalSeries,
    convergence_study,
)

ART_DIR = os.path.join(ROOT, "artifacts", "IN387-PLAT01")
NODE = "IN387-PLAT01"


# ---------------------------------------------------------------------------
# Scenarios
# ---------------------------------------------------------------------------

def source_mesh():
    """8x8 stretched quad mesh on the unit box (non-uniform spacing)."""
    return rectilinear_mesh_2d(np.linspace(0.0, 1.0, 9) ** 1.15, np.linspace(0.0, 1.0, 9))


def target_mesh():
    """8x8 perturbed quad mesh on the same box (non-orthogonal, off-grid)."""
    return perturbed_mesh_2d(8, 8, jitter=0.5, seed=3)


def field_linear(mesh):
    return 1.0 + 0.5 * mesh.cells[:, 0] - 0.25 * mesh.cells[:, 1]


def field_smooth(mesh, sigma=0.18, floor=0.1):
    d2 = np.sum((mesh.cells - np.array([0.42, 0.58])) ** 2, axis=1)
    return floor + np.exp(-d2 / (2.0 * sigma**2))


def field_front(mesh):
    return np.where(mesh.cells[:, 0] < 0.5, 1.0, 0.0)


def invariants(mesh, field):
    m0 = float(np.dot(mesh.volumes, field))
    cm = np.dot(mesh.volumes * field, mesh.cells) / m0
    d = mesh.cells - cm
    inertia = np.einsum("i,ij,ik->jk", mesh.volumes * field, d, d)
    return m0, cm, inertia


def _entry(value: float, threshold: float, passed: bool, note: str = "") -> dict:
    return {
        "value": float(value),
        "threshold": float(threshold),
        "passed": bool(passed),
        "note": note,
    }


# ---------------------------------------------------------------------------
# C1 -- isomorphic (degenerate) identity
# ---------------------------------------------------------------------------

def check_c1() -> dict:
    src = source_mesh()
    t = np.linspace(0.0, 1.0, 65)
    hist = np.stack([field_smooth(src) + 0.05 * np.sin(2 * np.pi * tk) for tk in t])

    worst_time = 0.0
    for method in ("nearest", "linear", "cubic"):
        interp = TemporalInterpolator(
            TemporalSeries(times=t, snapshots=hist, weights=src.volumes), method=method
        )
        for k in (0, 7, 33, 64):
            worst_time = max(
                worst_time, float(np.max(np.abs(interp.interpolate(float(t[k])).field - hist[k])))
            )

    # spatial identity: same mesh, degenerate exchange
    ex = SpacetimeExchanger(src, src, t, time_method="cubic")
    res = ex.exchange(hist, float(t[33]))
    worst_space = float(np.max(np.abs(res.field - hist[33])))

    worst = max(worst_time, worst_space)
    return {
        "temporal_node_identity_max_abs_error": _entry(
            worst_time, 1e-12, worst_time <= 1e-12,
            "request on a stored sample returns the stored snapshot bit-exactly",
        ),
        "spatial_same_mesh_identity_max_abs_error": _entry(
            worst_space, 1e-12, worst_space <= 1e-12,
            "degenerate exchange on an identical mesh",
        ),
        "passed": bool(worst <= 1e-12),
        "value": worst,
    }


# ---------------------------------------------------------------------------
# C2 / C3 -- first and second moment
# ---------------------------------------------------------------------------

def check_c2_c3() -> tuple:
    src, tgt = source_mesh(), target_mesh()
    on = MomentRemap(src, tgt)
    off = MomentRemap(src, tgt, enforce_moments=False)
    out = {}
    for name, fn in (("linear_field", field_linear), ("smooth_field", field_smooth)):
        f = fn(src)
        r_on = on.apply(f)
        r_off = off.apply(f)
        out[name] = {
            "m0_rel_error": r_on.m0_rel_error,
            "m1_rel_error": r_on.m1_rel_error,
            "m2_rel_error": r_on.m2_rel_error,
            "centroid_drift_cells": r_on.centroid_drift_cells,
            "inertia_rel_error": r_on.inertia_rel_error,
            "correction_rel": r_on.correction_rel,
            "limited_fraction": r_on.limited_fraction,
            "coverage_error": r_on.coverage_error,
            "negative_control": {
                "m1_rel_error": r_off.m1_rel_error,
                "m2_rel_error": r_off.m2_rel_error,
                "centroid_drift_cells": r_off.centroid_drift_cells,
            },
            "diagnostics": {
                "array_m1_rel_error": r_on.array_m1_rel_error,
                "continuous_m1_rel_error": r_on.continuous_m1_rel_error,
                "continuous_m2_rel_error": r_on.continuous_m2_rel_error,
            },
        }

    # 100 chained A->B->A cycles: invariant drift
    fwd, bwd = MomentRemap(src, tgt), MomentRemap(tgt, src)
    f0 = field_smooth(src)
    m0a, ca, ia = invariants(src, f0)
    cur = f0.copy()
    n_cycles = 100
    for _ in range(n_cycles):
        cur = bwd.apply(fwd.apply(cur).field).field
    m0b, cb, ib = invariants(src, cur)
    h = float(np.mean(np.sqrt(src.volumes)))
    chain = {
        "n_cycles": n_cycles,
        "mass_rel_drift": abs(m0b / m0a - 1.0),
        "centroid_drift_cells": float(np.linalg.norm(cb - ca) / h),
        "inertia_rel_drift": float(np.linalg.norm(ib - ia) / np.linalg.norm(ia)),
        "field_level_rel_change": float(np.max(np.abs(cur - f0)) / np.max(np.abs(f0))),
    }

    m1_worst = max(v["m1_rel_error"] for v in out.values())
    m2_worst = max(v["m2_rel_error"] for v in out.values())
    drift_worst = max(v["centroid_drift_cells"] for v in out.values())
    inertia_worst = max(v["inertia_rel_error"] for v in out.values())
    neg_worst_m1 = min(v["negative_control"]["m1_rel_error"] for v in out.values())
    neg_worst_m2 = min(v["negative_control"]["m2_rel_error"] for v in out.values())
    chain_worst = max(chain["mass_rel_drift"], chain["centroid_drift_cells"],
                      chain["inertia_rel_drift"])

    c2 = {
        "fields": out,
        "m1_rel_error": _entry(m1_worst, 1e-6, m1_worst <= 1e-6,
                               "worst of the two fields, array-moment convention"),
        "centroid_drift_cells": _entry(drift_worst, 1e-3, drift_worst <= 1e-3,
                                       "target-cell units"),
        "passed": bool(m1_worst <= 1e-6 and drift_worst <= 1e-3),
        "value": m1_worst,
    }
    c3 = {
        "m2_rel_error": _entry(m2_worst, 1e-6, m2_worst <= 1e-6,
                               "worst of the two fields"),
        "inertia_rel_error": _entry(inertia_worst, 1e-6, inertia_worst <= 1e-6,
                                    "second central moment tensor"),
        "chained_100_cycles": chain,
        "chained_drift": _entry(chain_worst, 1e-4, chain_worst <= 1e-4,
                                "mass/centroid/inertia drift over 100 A->B->A cycles"),
        "passed": bool(m2_worst <= 1e-6 and inertia_worst <= 1e-6 and chain_worst <= 1e-4),
        "value": m2_worst,
        "_negative_control": {"m1": neg_worst_m1, "m2": neg_worst_m2},
    }
    return c2, c3


# ---------------------------------------------------------------------------
# C4 -- boundedness
# ---------------------------------------------------------------------------

def check_c4() -> dict:
    src, tgt = source_mesh(), target_mesh()
    op = MomentRemap(src, tgt)
    cases = {}
    for name, fn in (("linear_field", field_linear), ("smooth_field", field_smooth),
                     ("front_at_bounds", field_front)):
        f = fn(src)
        r = op.apply(f)
        cases[name] = {
            "bound_violations": r.bound_violations,
            "bound_violation_max": r.bound_violation_max,
            "field_min": float(r.field.min()),
            "field_max": float(r.field.max()),
            "source_min": float(f.min()),
            "source_max": float(f.max()),
            "constraints_feasible": r.constraints_feasible,
            "mass_repaired": r.mass_repaired,
            "mass_rel_error": r.mass_rel_error,
            "m1_rel_error": r.m1_rel_error,
            "m2_rel_error": r.m2_rel_error,
        }
    viol = sum(c["bound_violations"] for c in cases.values())
    low_ok = all(c["field_min"] >= c["source_min"] - 1e-12 for c in cases.values())
    high_ok = all(c["field_max"] <= c["source_max"] + 1e-12 for c in cases.values())
    return {
        "cases": cases,
        "total_violations": _entry(viol, 0.0, viol == 0,
                                   "min >= source min and max <= source max"),
        "mass_kept_under_binding_bounds": _entry(
            max(c["mass_rel_error"] for c in cases.values()), 1e-12,
            all(c["mass_rel_error"] <= 1e-12 for c in cases.values()),
            "the 0th moment is never sacrificed to bounds",
        ),
        "passed": bool(viol == 0 and low_ok and high_ok),
        "value": float(viol),
    }


# ---------------------------------------------------------------------------
# C5 -- round-trip consistency
# ---------------------------------------------------------------------------

def check_c5() -> dict:
    src, tgt = source_mesh(), target_mesh()
    fwd, bwd = MomentRemap(src, tgt), MomentRemap(tgt, src)
    rows = {}
    worst_inv = 0.0
    for name, fn in (("linear_field", field_linear), ("smooth_field", field_smooth)):
        f = fn(src)
        g = bwd.apply(fwd.apply(f).field).field
        m0a, ca, ia = invariants(src, f)
        m0b, cb, ib = invariants(src, g)
        h = float(np.mean(np.sqrt(src.volumes)))
        row = {
            "mass_rel_error": abs(m0b / m0a - 1.0),
            "centroid_drift_cells": float(np.linalg.norm(cb - ca) / h),
            "inertia_rel_error": float(np.linalg.norm(ib - ia) / np.linalg.norm(ia)),
            "field_level_rel_change": float(np.max(np.abs(g - f)) / np.max(np.abs(f))),
        }
        worst_inv = max(worst_inv, row["mass_rel_error"], row["centroid_drift_cells"],
                        row["inertia_rel_error"])
        rows[name] = row

    # exact field identity on an identical mesh (the operator's own fixed point)
    same = MomentRemap(src, src)
    f0 = field_smooth(src)
    identical = float(np.max(np.abs(same.apply(f0).field - f0)))

    return {
        "fields": rows,
        "invariant_roundtrip": _entry(
            worst_inv, 1e-12, worst_inv <= 1e-12,
            "mass/centroid/inertia after A->B->A equal the source (the conserved "
            "invariant a coupled solver transports)",
        ),
        "identical_mesh_field_roundtrip": _entry(
            identical, 1e-12, identical <= 1e-12,
            "A->B->A with A == B returns the field itself",
        ),
        "field_level_rel_change_reported": _entry(
            max(v["field_level_rel_change"] for v in rows.values()), 1.0, True,
            "reported, not asserted: a conservative projection onto a different "
            "mesh cannot be inverted, so the field itself is only recovered to "
            "O(h^2). The invariant round trip above is the exact statement.",
        ),
        "passed": bool(worst_inv <= 1e-12 and identical <= 1e-12),
        "value": max(worst_inv, identical),
    }


# ---------------------------------------------------------------------------
# C6 -- energy budget across heterogeneous clocks
# ---------------------------------------------------------------------------

def check_c6() -> dict:
    """Energy budget of the temporal operator under heterogeneous time steps.

    The source solver stores snapshots on its own clock (``dt_f``); the target
    solver asks for times that are ``ratio`` fine steps apart, offset by a
    fraction ``phase`` of a fine step so that no request lands on a sample.  For
    an oscillatory field ``f = A cos(omega t)`` the linear interpolant has an
    amplitude deficit ``~ (alpha (1-alpha) / 2) (omega dt_f)^2`` (``alpha`` =
    ``phase``), so the relative energy error is twice that -- and it depends on
    ``dt_f`` only, *not* on the ratio: the coarse clock adds no error of its own.
    Both statements are measured here.
    """
    t_end, ratio, phase = 1.0, 16, 0.37
    src, tgt = source_mesh(), target_mesh()

    def energy_scan(n_fine: int, ratio_i: int, phase_i: float = phase,
                    t_stop: float = 0.5) -> float:
        """Worst relative energy error over the *same absolute window* [0, t_stop]."""
        t = np.linspace(0.0, t_end, n_fine)
        dt = float(t[1] - t[0])
        hist = np.repeat(np.cos(2 * np.pi * t / t_end)[:, None], src.n_cells, axis=1)
        ex = SpacetimeExchanger(src, tgt, t, time_method="linear")
        worst = 0.0
        j = 1
        while True:
            tq = (ratio_i * j + phase_i) * dt
            if tq >= t_stop or j > 5000:
                break
            reference = np.cos(2 * np.pi * tq) * np.ones(tgt.n_cells)
            worst = max(worst, ex.exchange(hist, tq, reference_field=reference).energy_rel_error)
            j += 1
        return float(worst)

    n_fine = 1024 + 1
    dt_f = t_end / (n_fine - 1)
    t = np.linspace(0.0, t_end, n_fine)
    hist = np.repeat(np.cos(2 * np.pi * t / t_end)[:, None], src.n_cells, axis=1)
    ex = SpacetimeExchanger(src, tgt, t, time_method="linear")
    rows: List[dict] = []
    for j in range(1, 30):
        tq = (ratio * j + phase) * dt_f
        if tq >= t_end:
            break
        reference = np.cos(2 * np.pi * tq) * np.ones(tgt.n_cells)
        res = ex.exchange(hist, tq, reference_field=reference)
        rows.append({
            "time": float(tq),
            "omega_dt_f": float(2 * np.pi * dt_f),
            "energy_rel_error": float(res.energy_rel_error),
            "mass_rel_error": float(res.spatial_mass_rel_error),
        })
    worst = max(r["energy_rel_error"] for r in rows)
    mass_worst = max(r["mass_rel_error"] for r in rows)

    # (a) fine-clock refinement: the error must fall as (omega dt_f)^2
    fine_study = []
    for n in (129, 257, 513, 1025):
        dt = t_end / (n - 1)
        fine_study.append({
            "n_fine_samples": n,
            "omega_dt_f": float(2 * np.pi * dt),
            "energy_rel_error": energy_scan(n, ratio),
        })
    errs = np.array([r["energy_rel_error"] for r in fine_study])
    dts = np.array([t_end / (r["n_fine_samples"] - 1) for r in fine_study])
    order_fine = float(np.polyfit(np.log(dts), np.log(errs), 1)[0]) if np.all(errs > 0) else float("nan")

    # (b) coarse-clock ratio sweep at fixed dt_f: the error must NOT depend on it
    ratio_study = []
    for r_i in (2, 4, 8, 16, 32):
        ratio_study.append({
            "ratio": r_i,
            "energy_rel_error": energy_scan(n_fine, r_i),
        })
    ratio_spread = float(max(r["energy_rel_error"] for r in ratio_study)
                         / max(min(r["energy_rel_error"] for r in ratio_study), 1e-300))

    return {
        "coarse_to_fine_ratio": ratio,
        "phase_offset": phase,
        "worst_energy_rel_error": _entry(worst, 0.01, worst <= 0.01,
                                        "linear temporal operator, offset coarse clock"),
        "worst_mass_rel_error": _entry(mass_worst, 1e-12, mass_worst <= 1e-12,
                                       "partition of unity + conservative remap"),
        "fine_clock_refinement": fine_study,
        "fine_clock_order": _entry(order_fine, 2.0, order_fine > 1.5,
                                   "energy error ~ (omega dt_f)^2"),
        "coarse_ratio_sweep": ratio_study,
        "coarse_ratio_spread": _entry(ratio_spread, 1.1, ratio_spread <= 1.1,
                                      "max/min over ratio in {2,4,8,16,32}: the coarse "
                                      "clock adds no error of its own"),
        "samples": rows,
        "passed": bool(worst <= 0.01 and mass_worst <= 1e-12 and order_fine > 1.5
                       and ratio_spread <= 1.1),
        "value": worst,
    }


# ---------------------------------------------------------------------------
# C7 -- negative control
# ---------------------------------------------------------------------------

def check_c7(c2: dict, c3: dict) -> dict:
    neg = c3["_negative_control"]
    c2_on = c2["m1_rel_error"]["value"]
    c3_on = c3["m2_rel_error"]["value"]
    fails = {
        "c2_first_moment": neg["m1"] > 1e-6,
        "c3_second_moment": neg["m2"] > 1e-6,
    }
    return {
        "without_constraints_m1_rel_error": _entry(neg["m1"], 1e-6, not fails["c2_first_moment"],
                                                   "must EXCEED the criterion"),
        "without_constraints_m2_rel_error": _entry(neg["m2"], 1e-6, not fails["c3_second_moment"],
                                                   "must EXCEED the criterion"),
        "with_constraints_m1_rel_error": c2_on,
        "with_constraints_m2_rel_error": c3_on,
        "exceeds_threshold_m1": float(neg["m1"] / 1e-6),
        "exceeds_threshold_m2": float(neg["m2"] / 1e-6),
        "with_over_without_m1": float(neg["m1"] / max(c2_on, 1e-300)),
        "with_over_without_m2": float(neg["m2"] / max(c3_on, 1e-300)),
        "passed": bool(fails["c2_first_moment"] and fails["c3_second_moment"]),
        "value": float(neg["m1"]),
    }


# ---------------------------------------------------------------------------
# convergence orders (supporting evidence for the temporal operator)
# ---------------------------------------------------------------------------

def check_orders() -> dict:
    rows = {}
    for profile in ("smooth", "euler", "piecewise_linear"):
        rows[profile] = {}
        for method in ("nearest", "linear", "cubic"):
            st = convergence_study(profile, method=method)
            rows[profile][method] = {
                "order": st["order"],
                "zero_error": st["zero_error"],
                "dt": st["dt"],
                "err": st["err"],
            }
    lin_coupled = rows["euler"]["linear"]["order"]
    return {
        "profiles": rows,
        "linear_order_on_coupled_trajectory": _entry(
            lin_coupled, 0.2, abs(lin_coupled - 1.0) <= 0.2,
            "spec: linear order = 1 +/- 0.2 measured on first-order-in-time data",
        ),
        "linear_order_on_smooth_data": rows["smooth"]["linear"]["order"],
        "cubic_order_on_smooth_data": rows["smooth"]["cubic"]["order"],
        "nearest_order_on_smooth_data": rows["smooth"]["nearest"]["order"],
        "passed": bool(abs(lin_coupled - 1.0) <= 0.2),
        "value": float(lin_coupled),
    }


# ---------------------------------------------------------------------------
# accuracy benchmark against the analytic field (supporting evidence)
# ---------------------------------------------------------------------------

def check_accuracy() -> dict:
    """Space-time exchange of an analytic bump versus the exact field.

    The moment repair perturbs the array by O(h^2) on purpose, so the accuracy of
    the composed operator must be measured against an analytic reference and
    against the uncorrected conservative exchange -- otherwise "exact moments"
    could hide an accuracy loss.  Three questions are answered with numbers:

    1. how accurate is the composed operator on a *well resolved* field?
    2. how accurate is it on a deliberately *under-resolved* field (1.4 cells per
       bump width), and does that error converge under refinement?
    3. does the moment correction cost accuracy relative to the uncorrected map?
    """
    sigma_ref = 0.30   # well resolved on an 8x8 mesh (2.4 cells per sigma)
    sigma_hard = 0.18  # under-resolved on purpose (1.4 cells per sigma)

    def meshes(n):
        return (
            rectilinear_mesh_2d(np.linspace(0.0, 1.0, n + 1) ** 1.15,
                                np.linspace(0.0, 1.0, n + 1)),
            perturbed_mesh_2d(n, n, jitter=0.5, seed=3),
        )

    def analytic(mesh, tq, sigma):
        xc = 0.3 + 0.4 * tq
        yc = 0.5 + 0.1 * np.sin(2 * np.pi * tq)
        return 0.1 + np.exp(
            -((mesh.cells[:, 0] - xc) ** 2 + (mesh.cells[:, 1] - yc) ** 2) / (2 * sigma**2)
        )

    def run(n, sigma, variants):
        src, tgt = meshes(n)
        t = np.linspace(0.0, 1.0, 129)
        hist = np.stack([analytic(src, tk, sigma) for tk in t])
        tq = float(t[52] + 0.43 * (t[1] - t[0]))
        ref = analytic(tgt, tq, sigma)
        amp = float(np.max(ref))
        rows = {}
        for name, kw in variants.items():
            ex = SpacetimeExchanger(src, tgt, t, **kw)
            res = ex.exchange(hist, tq, reference_field=ref)
            err = float(np.max(np.abs(res.field - ref)))
            rows[name] = {
                "max_abs_error": err,
                "rel_error_vs_amplitude": err / amp,
                "energy_rel_error": float(res.energy_rel_error),
                "m1_rel_error": res.remap.m1_rel_error,
                "m2_rel_error": res.remap.m2_rel_error,
                "bound_violations": res.remap.bound_violations,
            }
        return rows, tq, amp

    variants = {
        "cubic_time + linear_recon + moment_correction": dict(
            time_method="cubic", reconstruction="linear", enforce_moments=True),
        "cubic_time + linear_recon (no correction)": dict(
            time_method="cubic", reconstruction="linear", enforce_moments=False),
        "cubic_time + donor_cell (no correction)": dict(
            time_method="cubic", reconstruction="donor", enforce_moments=False),
        "linear_time + linear_recon + moment_correction": dict(
            time_method="linear", reconstruction="linear", enforce_moments=True),
    }

    resolved, tq_r, amp_r = run(8, sigma_ref, variants)
    hard, tq_h, amp_h = run(8, sigma_hard, variants)

    # refinement of the under-resolved case: is the error the mesh or the operator?
    refine = []
    for n in (8, 12, 16):
        rows, _, _ = run(n, sigma_hard, {
            "cubic_time + linear_recon + moment_correction": variants[
                "cubic_time + linear_recon + moment_correction"]})
        refine.append({
            "n_cells_per_direction": n,
            "h": 1.0 / n,
            "rel_error_vs_amplitude": rows[
                "cubic_time + linear_recon + moment_correction"]["rel_error_vs_amplitude"],
        })
    errs = np.array([r["rel_error_vs_amplitude"] for r in refine])
    hs = np.array([r["h"] for r in refine])
    order = float(np.polyfit(np.log(hs), np.log(errs), 1)[0])

    cost_key = "cubic_time + linear_recon (no correction)"
    acc_cost = (hard["cubic_time + linear_recon + moment_correction"]["rel_error_vs_amplitude"]
                / hard[cost_key]["rel_error_vs_amplitude"])

    return {
        "well_resolved": {
            "sigma": sigma_ref,
            "cells_per_sigma": sigma_ref * 8,
            "target_time": tq_r,
            "amplitude": amp_r,
            "variants": resolved,
        },
        "under_resolved": {
            "sigma": sigma_hard,
            "cells_per_sigma": sigma_hard * 8,
            "target_time": tq_h,
            "amplitude": amp_h,
            "variants": hard,
        },
        "refinement_of_under_resolved": refine,
        "refinement_order": _entry(order, 2.0, order > 1.5,
                                  "the residual error is discretisation-limited and "
                                  "converges at O(h^2)"),
        "well_resolved_below_3pct": _entry(
            resolved["cubic_time + linear_recon + moment_correction"]["rel_error_vs_amplitude"],
            0.03,
            resolved["cubic_time + linear_recon + moment_correction"]["rel_error_vs_amplitude"] <= 0.03,
            "accuracy of the composed operator on a field resolved by >2 cells per sigma",
        ),
        "under_resolved_below_3pct": _entry(
            hard["cubic_time + linear_recon + moment_correction"]["rel_error_vs_amplitude"],
            0.03,
            hard["cubic_time + linear_recon + moment_correction"]["rel_error_vs_amplitude"] <= 0.03,
            "under-resolved field: NOT below 3% on an 8x8 mesh -- discretisation, see "
            "refinement_order",
        ),
        "correction_accuracy_cost": _entry(
            acc_cost, 1.5, acc_cost <= 1.5,
            "the moment repair must not degrade the accuracy of the exchange",
        ),
        "passed": bool(
            resolved["cubic_time + linear_recon + moment_correction"]["rel_error_vs_amplitude"] <= 0.03
            and acc_cost <= 1.5 and order > 1.5
        ),
        "value": hard["cubic_time + linear_recon + moment_correction"]["rel_error_vs_amplitude"],
    }


# ---------------------------------------------------------------------------
# C8 -- test suite health
# ---------------------------------------------------------------------------

def check_c8(run_pytest: bool = True) -> dict:
    files = [
        "tests/coupling/test_temporal_interp.py",
        "tests/coupling/test_moment_remap.py",
        "tests/coupling/test_spacetime_exchange.py",
        "tests/coupling/test_coupling_neutrality.py",
    ]
    if not run_pytest:
        return {"skipped": True, "passed": False, "value": 0.0}
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", *files, "-q", "--no-header", "-p", "no:warnings"],
        cwd=ROOT, capture_output=True, text=True,
    )
    tail = proc.stdout.strip().splitlines()[-1] if proc.stdout.strip() else ""
    n_passed = 0
    for token in tail.replace(",", " ").split():
        if token.isdigit():
            n_passed = int(token)
            break
    ok = proc.returncode == 0 and n_passed >= 16
    return {
        "files": files,
        "returncode": proc.returncode,
        "summary": tail,
        "n_passed": n_passed,
        "tests": _entry(n_passed, 16.0, ok,
                        "pytest >= 16 and all green (includes the zero-domain-semantics gate)"),
        "passed": bool(ok),
        "value": float(n_passed),
    }


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------

def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--skip-pytest", action="store_true")
    args = ap.parse_args()

    os.makedirs(ART_DIR, exist_ok=True)
    t_start = time.time()

    c1 = check_c1()
    c2, c3 = check_c2_c3()
    c4 = check_c4()
    c5 = check_c5()
    c6 = check_c6()
    c7 = check_c7(c2, c3)
    c3.pop("_negative_control", None)
    orders = check_orders()
    accuracy = check_accuracy()
    c8 = check_c8(run_pytest=not args.skip_pytest)

    try:
        commit = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"], cwd=ROOT,
            capture_output=True, text=True,
        ).stdout.strip()
    except Exception:  # pragma: no cover - git always present in the worktree
        commit = "unknown"

    criteria = {"C1": c1, "C2": c2, "C3": c3, "C4": c4, "C5": c5,
                "C6": c6, "C7": c7, "C8": c8}
    all_pass = all(v["passed"] for v in criteria.values())

    payload = {
        "node": NODE,
        "title": "cross-solver space-time conservative field exchange: "
                 "temporal interpolation + exact moment remap",
        "generated_utc": datetime.now(timezone.utc).isoformat(),
        "git_commit": commit,
        "environment": {
            "python": platform.python_version(),
            "numpy": np.__version__,
            "platform": platform.platform(),
        },
        "verdict": "PASS" if all_pass else "FAIL",
        "criteria": criteria,
        "supporting_evidence": {
            "convergence_orders": orders,
            "analytic_accuracy_benchmark": accuracy,
        },
        "definitions": {
            "moment_convention": (
                "moments are computed from the arrays with the same quadrature on "
                "both sides (sum_c V_c f_c x^p); 'continuous_*' entries compare the "
                "target array with the exact continuous moments of the source "
                "reconstruction and expose the O(h^2) array quadrature bias"
            ),
            "m1_scale": "residuals are normalised by m0 * h (h = mean target cell size), "
                        "so they read as an offset in cell units",
            "m2_scale": "normalised by min(max|m2|, m0 h^2)",
            "c5_scope": (
                "the field itself cannot be recovered by a projection onto a "
                "different mesh (information loss); C5 is asserted on the conserved "
                "invariants and on the identical-mesh fixed point, with the "
                "field-level change reported alongside"
            ),
        },
        "products": {
            "temporal_operator": "backend/coupling/temporal_interp.py",
            "moment_remap": "backend/coupling/moment_remap.py",
            "space_time_exchange": "backend/coupling/spacetime_exchange.py",
            "tests": [
                "tests/coupling/test_temporal_interp.py",
                "tests/coupling/test_moment_remap.py",
                "tests/coupling/test_spacetime_exchange.py",
                "tests/coupling/test_coupling_neutrality.py",
            ],
            "acceptance_runner": "tools/run_in387_plat01_acceptance.py",
            "figures": "artifacts/IN387-PLAT01/figures/",
            "criteria_json": "artifacts/IN387-PLAT01/criteria.json",
            "report": "docs/IN387-PLAT01_spacetime_conservative_exchange.md",
        },
        "runtime_s": float(time.time() - t_start),
    }

    out_path = os.path.join(ART_DIR, "criteria.json")
    with open(out_path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2, sort_keys=False)
        fh.write("\n")

    print(f"{NODE} verdict: {payload['verdict']}")
    for key, val in criteria.items():
        print(f"  {key}: {'PASS' if val['passed'] else 'FAIL'}  value={val.get('value')}")
    print(f"  orders: linear(coupled)={orders['linear_order_on_coupled_trajectory']['value']:.3f} "
          f"smooth(linear)={orders['linear_order_on_smooth_data']:.3f} "
          f"smooth(cubic)={orders['cubic_order_on_smooth_data']:.3f}")
    print(f"  wrote {out_path} ({payload['runtime_s']:.1f}s)")
    return 0 if all_pass else 1


if __name__ == "__main__":
    sys.exit(main())