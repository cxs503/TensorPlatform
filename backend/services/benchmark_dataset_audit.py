"""
Benchmark reference dataset audit (mission m1388).

The platform's "对标算例" (benchmark cases) KPI rests on
``backend/data/benchmark_reference.json``.  Before m1388 that file was consumed
without any consistency gate, and its experimental columns turn out to be
mutually contradictory for part of the dataset (see
``docs/reports/m1388_benchmark_chain_audit.md``).

This module makes the quality of the reference data *machine-judgeable* with
three checks that need no external data source:

I1  Ct-Rt identity      ``Ct = Rt / (0.5 * rho * S * V^2)``
                        requires the per-case wetted area; catches reference rows
                        whose coefficient and force columns cannot both be true.
I2  Froude identity     ``Fr = V / sqrt(g * L)`` with the hull length declared in
                        the same row (fallback: the geometry preset).  Catches rows
                        where speed, length and Froude number disagree, i.e. the
                        row silently mixes model scale and full scale.
I3  Friction floor      ``Ct >= Cf_ITTC57(Re)`` — a total resistance coefficient
                        below the flat-plate friction line is physically
                        impossible (form factor (1+k) >= 1), so it flags
                        fabricated / unit-confused coefficients.

Status vocabulary (aligned with the platform evidence grading):

* ``CONSISTENT``   — I1 and I2 and I3 all ran and passed  -> counts as verified
* ``PARTIAL``      — I2 and I3 passed, I1 not runnable (row lacks wetted area)
                     -> WITHHELD, accuracy cannot be claimed
* ``INCONSISTENT`` — at least one check failed             -> WITHHELD (data defect)

Only ``CONSISTENT`` rows are allowed to contribute to a pass rate.
"""
from __future__ import annotations

import math
from typing import Any, Dict, Iterable, List, Optional

from services.ship_resistance import (
    DEFAULT_KINEMATIC_VISCOSITY,
    DEFAULT_WATER_DENSITY,
    ct_from_rt,
    froude_number,
    ittc57_cf,
    reynolds_number,
)

IDENTITY_TOL = 0.01      # 1 %  — Ct vs Rt/(0.5 rho S V^2)
FROUDE_TOL = 0.02        # 2 %  — declared Fr vs V/sqrt(gL)
FRICTION_FLOOR_TOL = 1e-9  # numerical slack only: Ct must not sit below Cf

CONSISTENT = "CONSISTENT"
PARTIAL = "PARTIAL"
INCONSISTENT = "INCONSISTENT"

# Hull lengths (m) used when a row does not carry its own geometry block.
DEFAULT_HULL_LENGTH = {
    "SUBOFF": 4.356,
    "DTMB-5415": 5.720,
    "KVLCC2": 320.0,
}


def _rel_error(a: float, b: float) -> float:
    if b == 0:
        return float("inf")
    return abs(a - b) / abs(b)


def assess_case(case: Dict[str, Any], nu: float = DEFAULT_KINEMATIC_VISCOSITY,
                rho: float = DEFAULT_WATER_DENSITY) -> Dict[str, Any]:
    """Audit a single reference case; returns a dict with checks + status."""
    ship_type = case.get("ship_type", "?")
    speed = float(case.get("speed_ms", 0.0) or 0.0)
    ct_ref = case.get("experimental_ct")
    rt_ref = case.get("experimental_rt_n")
    geo = case.get("geometry") or {}
    length = geo.get("length_m") or DEFAULT_HULL_LENGTH.get(ship_type)
    s_wet = geo.get("wetted_surface_m2")

    checks: List[Dict[str, Any]] = []

    # ---- I2: Froude identity -------------------------------------------------
    fr_declared = case.get("froude_number")
    fr_computed = froude_number(speed, length) if (speed > 0 and length) else None
    if fr_declared is not None and fr_computed:
        err = _rel_error(float(fr_declared), fr_computed)
        checks.append({
            "id": "I2_froude_identity",
            "passed": bool(err <= FROUDE_TOL),
            "declared": float(fr_declared),
            "computed": round(fr_computed, 6),
            "rel_error_pct": round(err * 100.0, 3),
            "criterion": f"rel_error <= {FROUDE_TOL*100:.0f}% with L={length} m",
        })
    else:
        checks.append({
            "id": "I2_froude_identity",
            "passed": None,
            "reason": "speed or hull length missing",
        })

    # ---- I1: Ct-Rt identity -------------------------------------------------
    if ct_ref and rt_ref and s_wet and speed > 0:
        ct_from_force = ct_from_rt(float(rt_ref), rho, float(s_wet), speed)
        err = _rel_error(float(ct_ref), ct_from_force)
        checks.append({
            "id": "I1_ct_rt_identity",
            "passed": bool(err <= IDENTITY_TOL),
            "ct_ref": float(ct_ref),
            "ct_from_rt": round(ct_from_force, 8),
            "rt_ref_n": float(rt_ref),
            "s_wet_m2": float(s_wet),
            "rel_error_pct": round(err * 100.0, 3),
            "criterion": f"rel_error <= {IDENTITY_TOL*100:.0f}%",
        })
    else:
        checks.append({
            "id": "I1_ct_rt_identity",
            "passed": None,
            "reason": "row lacks wetted_surface_m2 / Rt / Ct",
        })

    # ---- I3: friction floor -------------------------------------------------
    if ct_ref and length and speed > 0:
        re = reynolds_number(speed, length, nu)
        cf = ittc57_cf(re)
        checks.append({
            "id": "I3_friction_floor",
            "passed": bool(float(ct_ref) >= cf - FRICTION_FLOOR_TOL),
            "ct_ref": float(ct_ref),
            "cf_ittc57": round(cf, 8),
            "margin_pct": round((float(ct_ref) / cf - 1.0) * 100.0, 3),
            "reynolds": round(re, 1),
            "criterion": "Ct >= Cf_ITTC57(Re) (form factor >= 1)",
        })
    else:
        checks.append({
            "id": "I3_friction_floor",
            "passed": None,
            "reason": "Ct / length / speed missing",
        })

    failed = [c["id"] for c in checks if c.get("passed") is False]
    not_run = [c["id"] for c in checks if c.get("passed") is None]
    if failed:
        status = INCONSISTENT
    elif not_run:
        status = PARTIAL
    else:
        status = CONSISTENT

    return {
        "case_id": case.get("case_id"),
        "ship_type": ship_type,
        "speed_ms": speed,
        "experimental_ct": ct_ref,
        "status": status,
        "withheld": status != CONSISTENT,
        "failed_checks": failed,
        "checks_not_run": not_run,
        "checks": checks,
        "source_citation": case.get("source_citation", ""),
    }


def assess_dataset(cases: Iterable[Dict[str, Any]],
                   nu: float = DEFAULT_KINEMATIC_VISCOSITY,
                   rho: float = DEFAULT_WATER_DENSITY) -> Dict[str, Any]:
    """Audit a whole reference dataset and summarise it by status."""
    rows = [assess_case(c, nu=nu, rho=rho) for c in cases]
    by_status: Dict[str, int] = {CONSISTENT: 0, PARTIAL: 0, INCONSISTENT: 0}
    by_ship: Dict[str, Dict[str, int]] = {}
    for r in rows:
        by_status[r["status"]] += 1
        s = by_ship.setdefault(r["ship_type"], {CONSISTENT: 0, PARTIAL: 0, INCONSISTENT: 0})
        s[r["status"]] += 1
    total = len(rows)
    return {
        "total_cases": total,
        "status_counts": by_status,
        "status_by_ship_type": by_ship,
        "verified_cases": by_status[CONSISTENT],
        "withheld_cases": total - by_status[CONSISTENT],
        "verifiable_accuracy_claim": by_status[CONSISTENT] > 0,
        "tolerances": {
            "ct_rt_identity_pct": IDENTITY_TOL * 100,
            "froude_identity_pct": FROUDE_TOL * 100,
        },
        "cases": rows,
    }


def load_and_assess(data: Dict[str, Any],
                    nu: float = DEFAULT_KINEMATIC_VISCOSITY,
                    rho: float = DEFAULT_WATER_DENSITY) -> Dict[str, Any]:
    """Convenience wrapper for an already-loaded benchmark_reference.json dict."""
    summary = assess_dataset(data.get("cases", []), nu=nu, rho=rho)
    summary["dataset_description"] = data.get("description", "")
    return summary


def case_status_map(summary: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    """Index audit rows by case_id for O(1) lookup from the API layer."""
    return {row["case_id"]: row for row in summary.get("cases", [])}


def is_finite_number(value: Optional[float]) -> bool:
    return value is not None and isinstance(value, (int, float)) and math.isfinite(value)