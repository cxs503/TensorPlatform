"""
Ship-resistance benchmark *audit* and in-process validation.

Why this module exists
----------------------
The benchmark chain (``POST /api/v1/calculations/benchmark_validate``) used to
obtain the calculated resistance coefficient by an HTTP call to a ship platform on
port 8002 and read a ``ct`` key from its reply. The local resistance endpoint
returns ``total_resistance_n`` (no ``ct``), and port 8002 is not part of the
deployed stack, so the validator silently fell back to ``ct = 0`` -> 100 % error on
every case. Its unit tests did not catch that because they monkey-patched
``_call_resistance_api`` with a hand-written pseudo-Holtrop formula
(``base_ct = 0.0025 + 0.003 * Fn``), i.e. the validation was circular.

This module provides:

1. ``validate_cases``   real, in-process physics (``services.resistance_core``),
                        returning calc Ct, reference Ct, error and a verdict.
2. ``audit_dataset``    integrity audit of ``data/benchmark_reference.json``:
                        derived-quantity checks (Froude number vs speed/length,
                        dimensional resistance vs stated coefficient), trend
                        checks, and validity-envelope checks of the resistance
                        model itself. Verdicts: OK / INCONSISTENT / OUT_OF_ENVELOPE.

Everything here is pure Python (no DB, no HTTP) so it can run in CI and in
offline audit scripts.
"""
from __future__ import annotations

import math
from typing import Any, Dict, Iterable, List, Optional

from services.resistance_core import (
    DEFAULT_NU,
    DEFAULT_RHO,
    G,
    axisym_wetted_surface,
    ct_from_rt,
    froude_number,
    resistance_core,
)

# Acceptance threshold from the platform-wide accuracy standard.
PASS_TOL_PCT = 15.0
# Tolerance for internal consistency of the reference dataset itself.
INTEGRITY_TOL_PCT = 15.0

# Published wetted surface areas used ONLY to judge whether the platform's
# axisymmetric estimate is applicable; they are not part of the reference Ct data.
# Sources: DTMB 5415 (ITTC V&V Workshop 2010, model L=5.72 m, S_w ~= 4.7 m^2);
# KVLCC2 (SIMMAN 2008, full scale, S_w ~= 2.7e4 m^2).
LITERATURE_WETTED_SURFACE = {
    "DTMB-5415": {"value_m2": 4.70, "source": "ITTC V&V Workshop 2010 (model scale, S_w ~= 4.7 m^2)"},
    "KVLCC2": {"value_m2": 27000.0, "source": "SIMMAN 2008 (full scale, S_w ~= 2.7e4 m^2)"},
    "SUBOFF": {"value_m2": 5.42, "source": "Roddy 1990 (bare hull, S_w = 5.42 m^2)"},
}


# ---------------------------------------------------------------------------
# geometry helpers
# ---------------------------------------------------------------------------

def case_geometry(case: Dict[str, Any], preset: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """
    Resolve main dimensions for a benchmark case.

    Reference-supplied wetted area wins over the axisymmetric estimate; the
    returned ``wetted_surface_source`` records which one was used so downstream
    consumers can weight the result.
    """
    geo = dict(preset or {})
    ref_geo = case.get("geometry") or {}
    L = ref_geo.get("length_m", geo.get("length"))
    B = ref_geo.get("beam_m", geo.get("beam"))
    T = ref_geo.get("draft_m", geo.get("draft"))
    cb = ref_geo.get("cb", geo.get("cb"))
    S_ref = ref_geo.get("wetted_surface_m2") or geo.get("wetted_surface_m2")

    if L is None or B is None or cb is None:
        raise ValueError(f"case {case.get('case_id')}: incomplete geometry")

    if S_ref:
        S = float(S_ref)
        S_source = "reference" if ref_geo.get("wetted_surface_m2") else "preset"
    else:
        S = axisym_wetted_surface(float(L), float(B), float(cb))
        S_source = "axisymmetric_estimate"

    return {
        "length": float(L),
        "beam": float(B),
        "draft": float(T) if T is not None else float(B),
        "cb": float(cb),
        "wetted_surface_m2": S,
        "wetted_surface_source": S_source,
    }


# ---------------------------------------------------------------------------
# dataset integrity audit
# ---------------------------------------------------------------------------

def audit_case(case: Dict[str, Any], preset: Optional[Dict[str, Any]] = None,
               rho: float = DEFAULT_RHO) -> Dict[str, Any]:
    """Audit one reference case for internal consistency and model validity."""
    geo = case_geometry(case, preset)
    L, S, cb = geo["length"], geo["wetted_surface_m2"], geo["cb"]
    V = float(case["speed_ms"])

    flags: List[str] = []

    # (1) Froude number is a derived quantity: check it against V and L.
    fn_recomputed = froude_number(V, L)
    fn_stated = case.get("froude_number")
    fn_err_pct = abs(fn_recomputed - float(fn_stated)) / float(fn_stated) * 100.0 if fn_stated else None
    if fn_err_pct is not None and fn_err_pct > 5.0:
        flags.append(
            f"FROUDE_INCONSISTENT: stated Fn={fn_stated} but V/sqrt(gL)={fn_recomputed:.4f} "
            f"({fn_err_pct:.0f}% off)"
        )

    # (2) Ct and Rt must describe the same physical force: Rt = Ct * 0.5 rho S V^2.
    ct_stated = float(case["experimental_ct"])
    rt_stated = case.get("experimental_rt_n")
    rt_from_ct = ct_stated * 0.5 * rho * S * V ** 2
    ct_rt_err_pct = None
    if rt_stated:
        ct_rt_err_pct = abs(rt_from_ct - float(rt_stated)) / float(rt_stated) * 100.0
        if ct_rt_err_pct > INTEGRITY_TOL_PCT:
            flags.append(
                f"CT_RT_INCONSISTENT: Ct*sqrt-term gives Rt={rt_from_ct:.4g} N but dataset states "
                f"Rt={float(rt_stated):.4g} N ({ct_rt_err_pct:.0f}% off)"
            )

    # (3) Resistance-model validity envelope.
    model_flags: List[str] = []
    if cb < 0.60:
        model_flags.append(f"Cb={cb:.3f} < 0.60: residual-resistance fit is submarine-calibrated")
    if cb > 0.65:
        model_flags.append(
            f"Cb={cb:.3f} > 0.65: full-form hull, but the model has no form factor (1+k) "
            "and no wave-resistance term"
        )
    if fn_recomputed > 0.40:
        model_flags.append(f"Fn={fn_recomputed:.2f} > 0.40: outside residual-resistance calibration")
    lit = LITERATURE_WETTED_SURFACE.get(case["ship_type"])
    if geo["wetted_surface_source"] == "axisymmetric_estimate" and lit:
        over = (S - lit["value_m2"]) / lit["value_m2"] * 100.0
        if abs(over) > INTEGRITY_TOL_PCT:
            model_flags.append(
                f"WETTED_SURFACE_INVALID: axisymmetric estimate S={S:.4g} m^2 is {over:+.0f}% vs "
                f"published {lit['value_m2']:.4g} m^2 ({lit['source']})"
            )

    # Hard failure = the row's own identity is broken (speed and Froude number
    # describe different conditions). Soft failure = the dimensional column cannot
    # be trusted while the coefficient column may still be usable.
    if fn_err_pct is not None and fn_err_pct > 5.0:
        status = "INCONSISTENT"
    elif ct_rt_err_pct is not None and ct_rt_err_pct > INTEGRITY_TOL_PCT:
        status = "REFERENCE_WARNING"
    else:
        status = "OK"

    return {
        "case_id": case["case_id"],
        "ship_type": case["ship_type"],
        "speed_ms": V,
        "froude_number_recomputed": round(fn_recomputed, 4),
        "froude_number_stated": fn_stated,
        "froude_error_pct": round(fn_err_pct, 2) if fn_err_pct is not None else None,
        "ct_stated": ct_stated,
        "rt_stated_n": rt_stated,
        "rt_from_ct_n": round(rt_from_ct, 2),
        "ct_rt_error_pct": round(ct_rt_err_pct, 2) if ct_rt_err_pct is not None else None,
        "wetted_surface_m2": round(S, 3),
        "wetted_surface_source": geo["wetted_surface_source"],
        "integrity_flags": flags,
        "model_flags": model_flags,
        "in_envelope": not model_flags,
        "status": status,
    }


def audit_dataset(data: Dict[str, Any], presets: Optional[Dict[str, Dict[str, Any]]] = None,
                  rho: float = DEFAULT_RHO) -> Dict[str, Any]:
    """Audit every case of a reference dataset; returns per-case rows + summary."""
    presets = presets or {}
    rows = [audit_case(c, presets.get(c["ship_type"]), rho=rho) for c in data.get("cases", [])]

    # Trend check: for a submerged body with no free-surface wave making, Ct must
    # decrease with speed (friction-dominated). A monotonic increase is a red flag.
    trend: Dict[str, Dict[str, Any]] = {}
    for ship_type in {r["ship_type"] for r in rows}:
        sub = sorted([r for r in rows if r["ship_type"] == ship_type], key=lambda r: r["speed_ms"])
        cts = [r["ct_stated"] for r in sub]
        monotonic_up = all(b >= a for a, b in zip(cts, cts[1:]))
        trend[ship_type] = {
            "ct_vs_speed": [round(c, 6) for c in cts],
            "speeds": [r["speed_ms"] for r in sub],
            "monotonic_increasing": monotonic_up,
            "physically_suspect": bool(monotonic_up and sub and sub[0]["ship_type"] == "SUBOFF"),
        }

    by_status: Dict[str, int] = {}
    for r in rows:
        by_status[r["status"]] = by_status.get(r["status"], 0) + 1

    return {
        "case_count": len(rows),
        "status_counts": by_status,
        "rows": rows,
        "trend": trend,
        "verdict": "PASS" if by_status.get("OK", 0) == len(rows) else "REFERENCE_DATASET_UNUSABLE",
    }


# ---------------------------------------------------------------------------
# in-process validation against reference Ct
# ---------------------------------------------------------------------------

def validate_cases(data: Dict[str, Any], ship_type: str,
                   presets: Optional[Dict[str, Dict[str, Any]]] = None,
                   case_id: Optional[str] = None,
                   tol_pct: float = PASS_TOL_PCT) -> Dict[str, Any]:
    """
    Compute Ct with the platform resistance core and compare to reference Ct.

    ``case_id=None`` (or "all") validates every case of ``ship_type``.
    Cases whose Froude number disagrees with V/sqrt(gL) are excluded from the
    pass/fail tally (their reference speed cannot be trusted) and reported as
    ``excluded``.
    """
    presets = presets or {}
    cases = [c for c in data.get("cases", []) if c["ship_type"] == ship_type]
    if case_id and case_id != "all":
        cases = [c for c in cases if c["case_id"] == case_id]
    if not cases:
        return {"results": [], "excluded": [], "summary": {"total_cases": 0, "passed": 0,
                                                           "failed": 0, "pass_rate": 0.0}}

    rows, excluded = [], []
    for case in cases:
        audit = audit_case(case, presets.get(ship_type))
        if audit["status"] == "INCONSISTENT":
            excluded.append({"case_id": case["case_id"], "reason": "; ".join(audit["integrity_flags"])})
            continue

        geo = case_geometry(case, presets.get(ship_type))
        res = resistance_core(
            speed_ms=float(case["speed_ms"]),
            length=geo["length"],
            beam=geo["beam"],
            draft=geo["draft"],
            cb=geo["cb"],
            wetted_surface=geo["wetted_surface_m2"],
        )
        calc_ct = res["ct"]
        ref_ct = float(case["experimental_ct"])
        err_pct = abs(calc_ct - ref_ct) / ref_ct * 100.0
        passed = err_pct <= tol_pct

        notes = []
        if not passed:
            notes.append(
                "HONEST-FAIL: platform model (ITTC-57 friction + submarine-calibrated "
                "residual fit) is outside its validity envelope for this case; "
                "error exceeds the 15% acceptance threshold."
            )
        notes.extend(audit["model_flags"])
        if geo["wetted_surface_source"] == "axisymmetric_estimate":
            notes.append("wetted surface from axisymmetric estimate (not a measured value)")

        rows.append({
            "case_id": case["case_id"],
            "ship_type": ship_type,
            "speed_ms": float(case["speed_ms"]),
            "froude_number": round(res["froude_number"], 4),
            "calc_ct": round(calc_ct, 6),
            "ref_ct": ref_ct,
            "error_pct": round(err_pct, 2),
            "passed": passed,
            "method": "ITTC-1957 friction + platform residual-resistance fit (in-process)",
            "wetted_surface_m2": round(res["wetted_surface_m2"], 3),
            "notes": notes,
        })

    total = len(rows)
    passed = sum(1 for r in rows if r["passed"])
    mean_err = sum(r["error_pct"] for r in rows) / total if total else 0.0
    summary = {
        "total_cases": total,
        "passed": passed,
        "failed": total - passed,
        "pass_rate": round(passed / total * 100.0, 1) if total else 0.0,
        "mean_error_pct": round(mean_err, 2),
        "excluded_cases": len(excluded),
        "tolerance_pct": tol_pct,
        "all_failed": total > 0 and passed == 0,
    }
    return {"results": rows, "excluded": excluded, "summary": summary}