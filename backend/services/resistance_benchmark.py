"""
Resistance benchmark validation (in-process, no external service).

Background
----------
The previous implementation of ``POST /calculations/benchmark_validate``
forwarded every case over HTTP to ``http://localhost:8002`` and read ``ct``
and ``method`` from the response. The platform's own resistance endpoint
(``POST /calculations/resistance`` on the API port) returns
``total_resistance_n`` and the old model had no ``ct`` field at all, so
``calc_data.get("ct", 0.0)`` silently produced ``0.0`` for every case: the
gate reported a 100 % deviation irrespective of the physics, and
"passed=0" conclusions were artefacts of a broken bridge.

This module does three things instead:

1. computes the reference quantity in-process through
   :mod:`services.resistance_model` — the *same* code path the public
   resistance endpoint uses;
2. audits every row of ``data/benchmark_reference.json`` for internal
   consistency before it is allowed to vote in the pass/fail summary;
3. labels the prediction honestly: with no wave-resistance model the computed
   ``C_T`` is a viscous lower bound, so the report states the shortfall
   instead of hiding it.
"""
from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from services.resistance_model import (
    G,
    predict_resistance,
    wetted_surface_box_bound,
    wetted_surface_holtrop,
)

_BENCHMARK_PATH = Path(__file__).resolve().parent.parent / "data" / "benchmark_reference.json"

#: Hull presets used by the benchmark gate. ``hull_class`` selects the
#: wetted-surface/form-factor branch of the canonical model.
SHIP_GEOMETRY: Dict[str, Dict[str, Any]] = {
    "SUBOFF": {
        "length": 4.356,
        "beam": 0.508,          # diameter (axisymmetric body)
        "draft": 0.508,
        "displacement": 0.508,
        "cb": 0.508,
        "hull_class": "axisymmetric",
        # DARPA SUBOFF bare-hull wetted area cited in Roddy (1990); supplied to
        # the model as a REAL input instead of an axisymmetric estimate.
        "wetted_surface_m2": 5.42,
    },
    "DTMB-5415": {
        "length": 5.720,
        "beam": 0.759,
        "draft": 0.246,
        "displacement": 466.3,
        "cb": 0.508,
        "hull_class": "ship",
    },
    "KVLCC2": {
        "length": 320.0,
        "beam": 58.0,
        "draft": 20.8,
        "displacement": 307000.0,
        "cb": 0.8098,
        "hull_class": "ship",
    },
}

#: Tolerance used when auditing the reference dataset itself.
FN_CONSISTENCY_TOL = 0.02      # |Fn_table - V/sqrt(gL)| / Fn_table
RT_CONSISTENCY_TOL = 0.05      # |Ct_table - Rt/(q S)| / Ct_table
PHYSICAL_FROUDE_MAX = 0.60     # above this a "model test" row is not a ship case


def load_benchmark_data(path: Optional[Path] = None) -> Dict[str, Any]:
    """Load the reference dataset (Roddy 1990 / ITTC 2010 / SIMMAN 2008)."""
    target = path or _BENCHMARK_PATH
    with open(target, "r") as fh:
        return json.load(fh)


def audit_case(case: Dict[str, Any], geometry: Dict[str, Any]) -> Dict[str, Any]:
    """Check a benchmark row for internal consistency before trusting it.

    Blockers (row excluded from the verdict — the speed/Ct pairing itself is
    ambiguous):

    ``FROUDE_MISMATCH``   table Froude number disagrees with V/sqrt(gL).
    ``UNPHYSICAL_FROUDE`` displacement-hull row implies a non-ship regime (> 0.6).

    Warnings (row still judged, defect reported):

    ``RT_CT_MISMATCH``    tabulated Rt is inconsistent with Ct x q x S: the Rt
                          column of that row cannot be used.
    ``SURFACE_ABOVE_BOX`` computed wetted surface exceeds the box-hull bound.
    """
    blockers: List[str] = []
    warnings: List[str] = []
    flags: List[str] = []
    length = geometry["length"]
    v = float(case["speed_ms"])
    fn_calc = v / math.sqrt(G * length)
    fn_table = case.get("froude_number")

    if fn_table:
        rel = abs(float(fn_table) - fn_calc) / float(fn_table)
        if rel > FN_CONSISTENCY_TOL:
            blockers.append(f"FROUDE_MISMATCH(table={fn_table:.3f},V/sqrt(gL)={fn_calc:.3f})")
    if fn_calc > PHYSICAL_FROUDE_MAX and geometry.get("hull_class") == "ship":
        blockers.append(f"UNPHYSICAL_FROUDE({fn_calc:.3f})")

    geo_case = case.get("geometry", {})
    s_case = geo_case.get("wetted_surface_m2")
    rt_case = case.get("experimental_rt_n")
    ct_case = case.get("experimental_ct")
    s_model: Optional[float] = geometry.get("wetted_surface_m2")

    if s_case and rt_case and ct_case:
        rho = 998.0
        q = 0.5 * rho * v ** 2
        ct_from_rt = rt_case / (q * float(s_case)) if q > 0 else 0.0
        rel = abs(ct_from_rt - float(ct_case)) / float(ct_case)
        if rel > RT_CONSISTENCY_TOL:
            warnings.append(
                f"RT_CT_MISMATCH(Ct_table={ct_case:.5f}, Rt/(qS)={ct_from_rt:.5f}, {rel * 100:.0f}%)"
            )

    if s_model is not None and geometry.get("hull_class") == "ship":
        box = wetted_surface_box_bound(length, geometry["beam"], geometry["draft"])
        if s_model > box:
            warnings.append(f"SURFACE_ABOVE_BOX({s_model:.0f}>{box:.0f})")

    flags = blockers + warnings
    return {
        "froude_calc": round(fn_calc, 4),
        "froude_table": fn_table,
        "blockers": blockers,
        "warnings": warnings,
        "flags": flags,
        "consistent": not blockers,
    }


def evaluate_case(case: Dict[str, Any], geometry: Dict[str, Any]) -> Dict[str, Any]:
    """Evaluate one benchmark case through the canonical model."""
    speed = float(case["speed_ms"])
    audit = audit_case(case, geometry)

    pred = predict_resistance(
        length=geometry["length"],
        speed_ms=speed,
        beam=geometry["beam"],
        draft=geometry["draft"],
        cb=geometry["cb"],
        wetted_surface_m2=geometry.get("wetted_surface_m2"),
        hull_class=geometry.get("hull_class"),
        # sea water at 15 degC for full-scale hulls, fresh water for models
        rho=1025.0 if geometry["length"] > 100 else 998.0,
        nu=1.19e-6 if geometry["length"] > 100 else 1.004e-6,
        apply_ca=geometry["length"] > 100,
    )

    ref_ct = float(case["experimental_ct"])
    calc_ct = pred.ct_total
    error_pct = abs(calc_ct - ref_ct) / ref_ct * 100.0 if ref_ct > 0 else 999.0

    reference_ct_viscous = ref_ct - pred.cf_ittc57 * (pred.form_factor - 1.0)
    return {
        "case_id": case["case_id"],
        "ship_type": case["ship_type"],
        "speed_ms": speed,
        "froude_number": round(pred.froude_number, 4),
        "calc_ct": round(calc_ct, 6),
        "ref_ct": ref_ct,
        "error_pct": round(error_pct, 2),
        "calc_rt_n": round(pred.rt_total_n, 2),
        "wetted_surface_m2": round(pred.wetted_surface_m2, 3),
        "form_factor": round(pred.form_factor, 4),
        "cf_ittc57": round(pred.cf_ittc57, 6),
        "correlation_allowance": round(pred.ca, 6),
        "reference_ct_viscous_estimate": round(reference_ct_viscous, 6),
        "wave_gap_pct": round(
            max(0.0, (ref_ct - calc_ct) / ref_ct * 100.0), 2
        ),
        "pass": bool(error_pct <= 15.0) if audit["consistent"] else None,
        "status": "OK" if audit["consistent"] else "EXCLUDED_INCONSISTENT",
        "quality_flags": audit["flags"],
        "blockers": audit["blockers"],
        "warnings": audit["warnings"],
        "method": "ittc57+form_factor+ca (viscous lower bound)",
        "evidence": pred.evidence,
    }


def validate(
    ship_type: str,
    benchmark_id: str = "all",
    data: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Run the benchmark gate for one hull type and summarise honestly."""
    dataset = data if data is not None else load_benchmark_data()
    cases = dataset.get("cases", [])

    if benchmark_id == "all":
        matched = [c for c in cases if c["ship_type"] == ship_type]
    else:
        matched = [
            c for c in cases
            if c["case_id"] == benchmark_id and c["ship_type"] == ship_type
        ]
    if not matched:
        raise KeyError(
            f"No benchmark case for ship_type={ship_type!r}, benchmark_id={benchmark_id!r}"
        )

    geometry = SHIP_GEOMETRY.get(ship_type)
    if geometry is None:
        raise KeyError(
            f"No geometry preset for {ship_type!r}; available: {sorted(SHIP_GEOMETRY)}"
        )

    results = [evaluate_case(case, geometry) for case in matched]

    judged = [r for r in results if r["status"] == "OK"]
    excluded = [r for r in results if r["status"] != "OK"]
    passed = [r for r in judged if r["pass"]]
    errors = [r["error_pct"] for r in judged]

    summary = {
        "total_cases": len(results),
        "judged": len(judged),
        "excluded_inconsistent": len(excluded),
        "excluded_case_ids": [r["case_id"] for r in excluded],
        "passed": len(passed),
        "failed": len(judged) - len(passed),
        "pass_rate": round(len(passed) / len(judged) * 100.0, 1) if judged else None,
        "mean_error_pct": round(sum(errors) / len(errors), 2) if errors else None,
        "max_error_pct": round(max(errors), 2) if errors else None,
        "model_scope": "viscous lower bound (no wave-resistance model)",
        "wave_model": "NOT_SUPPORTED",
        "honest_note": (
            "calc_ct omits wave/pressure resistance, so error_pct is an upper "
            "bound on the model-vs-experiment deviation. Rows whose own columns "
            "contradict each other are excluded and listed in excluded_case_ids."
        ),
    }
    return {
        "results": results,
        "summary": summary,
        "dataset": {
            "description": dataset.get("description", ""),
            "cases": len(cases),
        },
    }