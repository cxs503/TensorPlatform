"""Consistency audit for the ship-resistance benchmark reference dataset.

The benchmark gate (``backend/api/domain/ship_benchmark.py``) compares the
platform's Holtrop/ITTC-57 resistance against ``experimental_ct`` from
``backend/data/benchmark_reference.json`` and passes a case when the deviation
is <= 15 %.  That gate is only meaningful if the reference numbers are mutually
consistent *within the dataset itself*.  They are not (audited 2026-11-01):

* SUBOFF rows state ``experimental_ct`` and ``experimental_rt_n`` that violate
  the definitional identity  Ct = Rt / (0.5 rho S V^2)  by ~87 %.
* DTMB-5415 rows state Froude numbers that violate  Fn = V / sqrt(g L)  by
  ~82 %; feeding the stated ``speed_ms`` back through the identity yields
  Fn = 1.37 / 2.06 for the two fastest rows, i.e. the column mixes full-scale
  speeds with a model-scale length.
* DTMB-5415 states ``cb * L * B * T`` = 0.5425 m^3 against a 0.4663 t
  displacement (16 % off), so ``cb`` or the displacement is mistyped.
* DTMB-5415 and KVLCC2 rows carry no wetted surface area, so the Ct <-> Rt
  identity cannot be checked at all and the deck's Ct comparison cannot be
  back-converted to a force.

This module is the shared, dependency-free auditor.  It *never* mutates the
dataset: it classifies each case as gate-eligible or blocked, so callers can
fail closed instead of silently comparing against a broken reference.

Design rules:
  * stdlib only (math) -- importable from the API layer and from CI;
  * every check is an identity that must hold exactly up to input rounding,
    so the tolerances below are rounding tolerances (1-5 %), not "engineering
    accuracy" tolerances;
  * severity policy: BLOCKING means the *total-resistance coefficient column
    itself* is untrustworthy (wrong comparison speed / implausible Ct), so the
    15 % gate must be withheld for that case; WARNING means a secondary column
    (Rt, displacement) contradicts Ct but Ct stays plausible, which is a data
    hygiene debt rather than a gate blocker;
  * no literature constant is asserted beyond the definitional ones
    (g, rho by water type, ITTC-57 friction line).  Anything that would
    require an external reference value is reported as MISSING data rather
    than silently filled in.
"""
from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence

G = 9.80665

#: Water density by test medium.  Model-scale towing tanks are fresh water,
#: full-scale ship references are quoted in sea water.
RHO_BY_TYPE: Dict[str, float] = {
    "SUBOFF": 999.0,
    "DTMB-5415": 999.0,
    "KVLCC2": 1025.0,
}
RHO_DEFAULT = 999.0

#: Tolerances are *rounding* tolerances for hand-entered tables, not physics.
TOL_FROUDE_REL = 0.01        # Fn quoted to 3 decimals vs V/sqrt(gL)
TOL_CT_RT_REL = 0.05         # Ct quoted to 2 significant digits vs Rt/(.5 rho S V^2)
TOL_CB_REL = 0.05            # cb * L * B * T vs stated displacement volume
FN_SURFACE_MAX = 1.0         # no benchmark case is trans-critical / planing
CT_CF_MIN, CT_CF_MAX = 0.9, 3.5  # total/viscous ratio for displacement hulls

BLOCKING = "BLOCKING"
WARNING = "WARNING"
OK = "OK"


def rho_for(ship_type: str) -> float:
    """Water density used to close the Ct <-> Rt identity for this hull type."""
    return RHO_BY_TYPE.get(ship_type, RHO_DEFAULT)


def ittc57_cf(reynolds: float) -> float:
    """ITTC-1957 model-ship correlation line: Cf = 0.075 / (log10 Re - 2)^2."""
    if reynolds <= 0:
        raise ValueError("Reynolds number must be positive")
    return 0.075 / (math.log10(reynolds) - 2.0) ** 2


def froude_number(speed_ms: float, length_m: float) -> float:
    """Fn = V / sqrt(g L)."""
    if length_m <= 0:
        raise ValueError("length must be positive")
    return speed_ms / math.sqrt(G * length_m)


def ct_from_rt(rt_n: float, wetted_surface_m2: float, speed_ms: float,
               rho: float = RHO_DEFAULT) -> float:
    """Ct = Rt / (0.5 rho S V^2) -- the definitional resistance identity."""
    if wetted_surface_m2 <= 0 or speed_ms <= 0:
        raise ValueError("wetted surface and speed must be positive")
    return rt_n / (0.5 * rho * wetted_surface_m2 * speed_ms ** 2)


def rt_from_ct(ct: float, wetted_surface_m2: float, speed_ms: float,
               rho: float = RHO_DEFAULT) -> float:
    """Rt = Ct * 0.5 rho S V^2 (inverse of :func:`ct_from_rt`)."""
    return ct * 0.5 * rho * wetted_surface_m2 * speed_ms ** 2


def _rel_dev(value: float, reference: float) -> float:
    """Relative deviation, guarded for zero/degenerate references."""
    if reference == 0:
        return math.inf if value else 0.0
    return abs(value - reference) / abs(reference)


def _finding(check: str, severity: str, detail: str,
             dev: Optional[float] = None) -> Dict[str, Any]:
    out: Dict[str, Any] = {"check": check, "severity": severity, "detail": detail}
    if dev is not None:
        out["relative_deviation"] = round(dev, 6)
        out["deviation_pct"] = round(dev * 100.0, 2)
    return out


def case_geometry(case: Dict[str, Any],
                  preset: Optional[Dict[str, Any]] = None) -> Dict[str, float]:
    """Normalise the per-case ``geometry`` block into SI hull particulars.

    Returns only the keys actually present, so callers can detect gaps instead
    of inheriting a hard-coded preset that may contradict the dataset.
    ``preset`` (per-ship-type geometry, e.g. ``SHIP_GEOMETRY`` of
    ``api.domain.ship_benchmark``) only fills keys the case itself lacks;
    the case's own ``geometry`` block always wins.
    """
    geo = dict(case.get("geometry") or {})
    out: Dict[str, float] = {}
    if "length_m" in geo:
        out["length"] = float(geo["length_m"])
    if "beam_m" in geo:
        out["beam"] = float(geo["beam_m"])
    elif "diameter_m" in geo:
        out["beam"] = float(geo["diameter_m"])   # body of revolution proxy
    if "draft_m" in geo:
        out["draft"] = float(geo["draft_m"])
    elif "diameter_m" in geo:
        out["draft"] = float(geo["diameter_m"])
    if "wetted_surface_m2" in geo:
        out["wetted_surface"] = float(geo["wetted_surface_m2"])
    if "displacement_t" in geo:
        out["displacement"] = float(geo["displacement_t"])
    elif "displacement_kg" in geo:
        out["displacement"] = float(geo["displacement_kg"]) / 1000.0
    if "cb" in geo:
        out["cb"] = float(geo["cb"])
    if preset:
        for key in ("length", "beam", "draft", "displacement", "cb"):
            if key not in out and key in preset:
                out[key] = float(preset[key])
        if "wetted_surface" not in out:
            _ws = preset.get("wetted_surface_m2", preset.get("wetted_surface"))
            if _ws is not None:
                out["wetted_surface"] = float(_ws)
    return out


def audit_case(case: Dict[str, Any],
               preset: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Audit one benchmark case; returns findings plus a gate verdict."""
    case_id = str(case.get("case_id", "?"))
    ship_type = str(case.get("ship_type", "?"))
    rho = rho_for(ship_type)
    geo = case_geometry(case, preset)
    findings: List[Dict[str, Any]] = []

    speed = case.get("speed_ms")
    length = geo.get("length")
    fn_stated = case.get("froude_number")
    ct_stated = case.get("experimental_ct")
    rt_stated = case.get("experimental_rt_n")
    surface = geo.get("wetted_surface")

    # --- C1: definitional Froude identity Fn = V / sqrt(gL) -----------------
    fn_derived: Optional[float] = None
    if speed is None or length is None:
        findings.append(_finding("FROUDE_IDENTITY", BLOCKING,
                                 "speed_ms or length_m missing; Fn unverifiable"))
    else:
        fn_derived = froude_number(float(speed), float(length))
        if fn_stated is None:
            findings.append(_finding("FROUDE_IDENTITY", BLOCKING,
                                     "froude_number missing", None))
        else:
            dev = _rel_dev(float(fn_stated), fn_derived)
            if dev > TOL_FROUDE_REL:
                findings.append(_finding(
                    "FROUDE_IDENTITY", BLOCKING,
                    f"stated Fn={float(fn_stated):.3f} vs V/sqrt(gL)={fn_derived:.4f}",
                    dev))
            else:
                findings.append(_finding(
                    "FROUDE_IDENTITY", OK,
                    f"stated Fn={float(fn_stated):.3f} vs V/sqrt(gL)={fn_derived:.4f}",
                    dev))
        # --- C4: physical envelope on the *derived* Froude number ----------
        if fn_derived > FN_SURFACE_MAX:
            findings.append(_finding(
                "SPEED_REFERENCE_FRAME", BLOCKING,
                f"V/sqrt(gL)={fn_derived:.3f} > {FN_SURFACE_MAX}: speed_ms looks "
                f"full-scale while length_m is model-scale (mixed reference frame)",
                (fn_derived - FN_SURFACE_MAX) / FN_SURFACE_MAX))

    # --- C5: Ct plausibility against the ITTC-57 friction line --------------
    ct_plausible: Optional[bool] = None
    if ct_stated is not None and speed is not None and length is not None:
        nu = 1.19e-6 if ship_type == "KVLCC2" else 1.0e-6
        re = float(speed) * float(length) / nu
        cf = ittc57_cf(re)
        ratio = float(ct_stated) / cf
        ct_plausible = CT_CF_MIN <= ratio <= CT_CF_MAX
        sev = OK if ct_plausible else BLOCKING
        findings.append(_finding(
            "CT_PLAUSIBILITY", sev,
            f"Ct/Cf={ratio:.3f} at Re={re:.3e} (Cf_ITTC57={cf:.6f}); "
            f"expected {CT_CF_MIN}-{CT_CF_MAX} for displacement hull",
            abs(ratio - 1.5) / 1.5))

    # --- C2: definitional resistance identity Ct = Rt/(0.5 rho S V^2) -------
    ct_from_rt_value: Optional[float] = None
    reciprocity_blocking = bool(ct_plausible is False)
    if surface is None:
        findings.append(_finding(
            "CT_RT_RECIPROCITY", WARNING,
            "geometry.wetted_surface_m2 missing: Ct vs Rt cannot be cross-checked "
            "(data completeness debt; the dimensionless Ct used by the gate is "
            "still checked by CT_PLAUSIBILITY)"))
    elif ct_stated is None or rt_stated is None:
        findings.append(_finding(
            "CT_RT_RECIPROCITY", BLOCKING,
            "experimental_ct or experimental_rt_n missing"))
    elif speed is None:
        findings.append(_finding(
            "CT_RT_RECIPROCITY", BLOCKING, "speed_ms missing"))
    else:
        ct_from_rt_value = ct_from_rt(float(rt_stated), float(surface),
                                      float(speed), rho)
        dev = _rel_dev(ct_from_rt_value, float(ct_stated))
        if dev <= TOL_CT_RT_REL:
            sev = OK
        else:
            sev = BLOCKING if reciprocity_blocking else WARNING
        findings.append(_finding(
            "CT_RT_RECIPROCITY", sev,
            f"Ct from stated Rt={ct_from_rt_value:.6f} vs stated Ct="
            f"{float(ct_stated):.6f} (rho={rho:g} kg/m3)", dev))

    # --- C3: block-coefficient identity cb * L * B * T = displacement -------
    cb = geo.get("cb")
    disp = geo.get("displacement")
    beam = geo.get("beam")
    draft = geo.get("draft")
    if None not in (cb, disp, beam, draft) and length is not None:
        vol = cb * length * beam * draft
        dev = _rel_dev(vol, disp)
        sev = WARNING if dev > TOL_CB_REL else OK
        findings.append(_finding(
            "BLOCK_COEFF_IDENTITY", sev,
            f"cb*L*B*T={vol:.4f} m3 vs displacement={disp:.4f} t "
            f"(rho assumed 1 t/m3)", dev))

    blocking = [f for f in findings if f["severity"] == BLOCKING]
    warnings = [f for f in findings if f["severity"] == WARNING]
    return {
        "case_id": case_id,
        "ship_type": ship_type,
        "speed_ms": speed,
        "froude_stated": fn_stated,
        "froude_derived": None if fn_derived is None else round(fn_derived, 6),
        "ct_stated": ct_stated,
        "ct_from_rt": None if ct_from_rt_value is None else round(ct_from_rt_value, 8),
        "rho": rho,
        "gate_eligible": not blocking,
        "blocking_checks": [f["check"] for f in blocking],
        "warnings": [f["check"] for f in warnings],
        "findings": findings,
    }


def audit_dataset(dataset: Dict[str, Any],
                  presets: Optional[Dict[str, Dict[str, Any]]] = None) -> Dict[str, Any]:
    """Audit every case in a benchmark dataset dict; returns a serialisable report.

    ``presets`` maps ship_type -> geometry fallback dict (the main-line
    ``docs/verification/gen_benchmark_audit.py`` passes
    ``api.domain.ship_benchmark.SHIP_GEOMETRY`` here); per-case ``geometry``
    blocks always win, presets only fill gaps.
    """
    presets = presets or {}
    cases = list(dataset.get("cases") or [])
    audited = [
        audit_case(c, preset=presets.get(str(c.get("ship_type", "?"))))
        for c in cases
    ]
    blocking_ids = [a["case_id"] for a in audited if not a["gate_eligible"]]
    warning_ids = [a["case_id"] for a in audited if a["warnings"]]
    by_check: Dict[str, int] = {}
    for a in audited:
        for f in a["findings"]:
            if f["severity"] in (BLOCKING, WARNING):
                by_check[f["check"]] = by_check.get(f["check"], 0) + 1
    return {
        "n_cases": len(audited),
        "n_gate_eligible": len(audited) - len(blocking_ids),
        "n_blocked": len(blocking_ids),
        "blocked_case_ids": blocking_ids,
        "cases_with_warnings": warning_ids,
        "defects_by_check": by_check,
        "all_identities_hold": not blocking_ids and not warning_ids,
        "cases": audited,
    }


def load_dataset(path: str | Path) -> Dict[str, Any]:
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def default_dataset_path() -> Path:
    """``backend/data/benchmark_reference.json`` resolved from this file."""
    return Path(__file__).resolve().parent.parent / "data" / "benchmark_reference.json"


def audit_file(path: Optional[str | Path] = None) -> Dict[str, Any]:
    return audit_dataset(load_dataset(path or default_dataset_path()))


def blocked_notes(report: Dict[str, Any]) -> Dict[str, str]:
    """Map case_id -> one-line gate note for blocked cases (fail-closed message)."""
    out: Dict[str, str] = {}
    for case in report["cases"]:
        if case["gate_eligible"]:
            continue
        detail = "; ".join(
            f["detail"] for f in case["findings"] if f["severity"] == BLOCKING)
        out[case["case_id"]] = f"REFERENCE_DATA_INCONSISTENT[{', '.join(case['blocking_checks'])}]: {detail}"
    return out


def derive_presets(dataset: Dict[str, Any],
                   fallback: Optional[Dict[str, Dict[str, float]]] = None
                   ) -> Dict[str, Any]:
    """Derive per-ship_type geometry presets from the dataset itself.

    The old hard-coded presets duplicated (and contradicted) the per-case
    ``geometry`` blocks -- e.g. SUBOFF carried ``cb`` = 0.508, which is the
    hull *diameter*, and DTMB-5415 carried a 466.3 kg displacement instead of
    the 542.5 kg implied by its own cb x L x B x T.  Deriving from the dataset
    keeps one source of truth; ``fallback`` supplies only keys the dataset
    lacks (and every such key is reported in ``sources``).
    """
    fallback = fallback or {}
    presets: Dict[str, Dict[str, float]] = {}
    sources: Dict[str, Dict[str, str]] = {}
    for case in dataset.get("cases") or []:
        ship_type = str(case.get("ship_type", "?"))
        geo = case_geometry(case)
        if ship_type in presets:
            continue
        preset: Dict[str, float] = {}
        src: Dict[str, str] = {}
        for key in ("length", "beam", "draft", "displacement", "cb", "wetted_surface"):
            if key in geo:
                preset[key] = geo[key]
                src[key] = "dataset"
        for key, value in (fallback.get(ship_type) or {}).items():
            if key not in preset:
                preset[key] = value
                src[key] = "fallback"
        presets[ship_type] = preset
        sources[ship_type] = src
    return {"presets": presets, "sources": sources}


def audit_summary_markdown(report: Dict[str, Any]) -> str:
    """Render the audit report as a compact markdown table (programmatic)."""
    lines = [
        "| case | ship | Fn stated | Fn derived | Ct stated | Ct from Rt | blocking | warnings |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for c in report["cases"]:
        fn_s = "-" if c["froude_stated"] is None else f"{c['froude_stated']:.3f}"
        fn_d = "-" if c["froude_derived"] is None else f"{c['froude_derived']:.4f}"
        ct_s = "-" if c["ct_stated"] is None else f"{c['ct_stated']:.5f}"
        ct_r = "-" if c["ct_from_rt"] is None else f"{c['ct_from_rt']:.6f}"
        lines.append(
            f"| {c['case_id']} | {c['ship_type']} | {fn_s} | {fn_d} | {ct_s} | {ct_r} "
            f"| {', '.join(c['blocking_checks']) or '-'} | {', '.join(c['warnings']) or '-'} |")
    lines.append("")
    lines.append(f"cases={report['n_cases']} gate_eligible={report['n_gate_eligible']} "
                 f"blocked={report['n_blocked']}")
    return "\n".join(lines)


__all__ = [
    "G", "RHO_BY_TYPE", "BLOCKING", "WARNING", "OK",
    "ittc57_cf", "froude_number", "ct_from_rt", "rt_from_ct",
    "case_geometry", "audit_case", "audit_dataset", "audit_file", "load_dataset",
    "default_dataset_path", "blocked_notes", "derive_presets",
    "audit_summary_markdown",
]