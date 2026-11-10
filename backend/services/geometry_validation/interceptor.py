"""Domain interceptor: declared-vs-derived comparison + applicability envelopes.

Two mechanisms, both driven exclusively by profile data:

1. **Declared consistency** – every key in ``profile.geometry.declared_map`` is
   compared with the derived metric it points at; a relative deviation above
   ``declared_tolerance_rel`` blocks the item (``E_GEOM_DECLARED_MISMATCH``).
   A missing declared value is a warning, because the derived value still gates.
2. **Applicability envelope** – each registered model carries a set of metric
   bounds (``[min, max]`` on any dotted metric path, including ratios such as
   ``ratios.beam_over_draft``).  Leaving an envelope is recorded as
   ``W_DOMAIN_OUT_OF_ENVELOPE`` (or a blocking code when the envelope is declared
   ``severity: error``); this is what turns "empirical formula used outside its
   validated range" from an invisible extrapolation into an explicit audit mark.

Neither function contains a domain constant; all numbers come from the profile.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from .geometry_extract import resolve_metric
from .profile import ValidationProfile
from .reason_codes import Finding, make_finding


def check_topology(metrics: Dict[str, Any], profile: ValidationProfile) -> List[Finding]:
    """Watertightness / degeneracy / positivity gates on the derived metrics."""
    findings: List[Finding] = []
    topo = metrics.get("topology") or {}
    if profile.geometry.get("require_watertight", True) and not topo.get("watertight", False):
        findings.append(make_finding(
            "E_GEOM_NOT_CLOSED",
            f"surface is not watertight (boundary_edges={topo.get('boundary_edges')}, "
            f"nonmanifold_edges={topo.get('nonmanifold_edges')}) — displacement volume "
            "would be meaningless",
            metric="topology.watertight",
            limit=True,
            context=topo))
    elif not topo.get("winding_consistent", True):
        findings.append(make_finding(
            "E_GEOM_INCONSISTENT_ORIENTATION",
            f"closed surface with {topo.get('inconsistent_edges')} inconsistently wound "
            "edges — signed volume is not reliable",
            metric="topology.inconsistent_edges",
            value=float(topo.get("inconsistent_edges") or 0),
            context=topo))
    n_deg = int(metrics.get("n_degenerate_triangles") or 0)
    n_tri = int(metrics.get("n_triangles") or 0) or 1
    if n_deg:
        frac = n_deg / float(n_tri)
        code = "E_GEOM_DEGENERATE" if frac > 0.05 else "W_GEOM_DEGENERATE_DROPPED"
        findings.append(make_finding(
            code,
            f"{n_deg} degenerate (zero-area) triangle(s) = {frac*100:.3f}% of the surface",
            metric="n_degenerate_triangles",
            value=float(n_deg),
            context={"fraction": frac, "n_triangles": n_tri}))
    repair = metrics.get("orientation_repair") or {}
    if repair.get("flipped_faces") or repair.get("global_flip"):
        findings.append(make_finding(
            "W_GEOM_ORIENTATION_REPAIRED",
            f"{repair.get('flipped_faces')} face(s) re-wound and global flip="
            f"{repair.get('global_flip')} before volume integration",
            metric="orientation_repair",
            context=repair))
    if metrics.get("length_reference") == "bounding_box":
        findings.append(make_finding(
            "W_GEOM_LENGTH_REFERENCE",
            "volume coefficients use the bounding-box length; overhanging appendages "
            "would inflate the denominator",
            metric="length_reference"))
    v_sub = metrics.get("volume_submerged")
    if v_sub is None or not (v_sub > 0.0):
        findings.append(make_finding(
            "E_GEOM_NONPOSITIVE_VOLUME",
            f"displaced volume below the waterline is {v_sub!r}",
            metric="volume_submerged",
            value=None if v_sub is None else float(v_sub)))
    return findings


def check_declared(metrics: Dict[str, Any], declared: Dict[str, float],
                   profile: ValidationProfile) -> Dict[str, Any]:
    """Compare declared values with derived metrics inside a relative tolerance."""
    tol = profile.declared_tolerance_rel
    mapping = profile.declared_map
    rows: List[Dict[str, Any]] = []
    findings: List[Finding] = []
    missing: List[str] = []
    declared = declared or {}
    for declared_key, metric_path in mapping.items():
        derived = resolve_metric(metrics, metric_path)
        declared_val: Optional[float]
        try:
            raw = (declared or {}).get(declared_key)
            declared_val = None if raw is None else float(raw)
        except (TypeError, ValueError):
            declared_val = None
            findings.append(make_finding(
                "E_INPUT_TYPE", f"declared.{declared_key} is not numeric ({raw!r})",
                metric=f"declared.{declared_key}"))
        row: Dict[str, Any] = {
            "declared_key": declared_key,
            "metric": metric_path,
            "declared": declared_val,
            "derived": derived,
            "tolerance_rel": tol,
        }
        if declared_val is None:
            row["status"] = "declared_missing"
            missing.append(declared_key)
        elif derived is None:
            row["status"] = "derived_missing"
            findings.append(make_finding(
                "W_DOMAIN_NOT_EVALUATED",
                f"derived metric '{metric_path}' unavailable for declared '{declared_key}'",
                metric=metric_path))
        else:
            denom = abs(derived) if abs(derived) > 1e-30 else 1.0
            rel = abs(derived - declared_val) / denom
            row["rel_error"] = rel
            row["status"] = "pass" if rel <= tol else "mismatch"
            if rel > tol:
                findings.append(make_finding(
                    "E_GEOM_DECLARED_MISMATCH",
                    f"declared {declared_key}={declared_val:.6g} vs derived "
                    f"{metric_path}={derived:.6g} (relative error {rel*100:.3f}% > "
                    f"tolerance {tol*100:.3f}%)",
                    metric=metric_path,
                    value=derived,
                    limit=declared_val,
                    context={"declared_key": declared_key, "rel_error": rel,
                             "tolerance_rel": tol}))
        rows.append(row)
    # one aggregated warning: a per-key warning would drown the report with N-key noise
    if missing and declared:
        findings.append(make_finding(
            "W_GEOM_DECLARED_MISSING",
            f"declared values missing for {sorted(missing)} — those keys were not "
            "cross-checked (the derived values still gate the item)",
            metric="declared",
            context={"missing_keys": sorted(missing),
                     "supplied_keys": sorted(declared)}))
    return {"tolerance_rel": tol, "comparisons": rows, "findings": findings}


def check_envelopes(metrics: Dict[str, Any], profile: ValidationProfile) -> Dict[str, Any]:
    """Evaluate every registered applicability envelope against the metrics."""
    results: List[Dict[str, Any]] = []
    findings: List[Finding] = []
    for env in profile.envelopes:
        env_id = str(env.get("id"))
        severity = str(env.get("severity", "warn"))
        bounds: Dict[str, Any] = dict(env.get("bounds") or {})
        checks: List[Dict[str, Any]] = []
        violated: List[str] = []
        unknown: List[str] = []
        for metric_path, lim in bounds.items():
            value = resolve_metric(metrics, metric_path)
            lo = None if lim[0] is None else float(lim[0])
            hi = None if lim[1] is None else float(lim[1])
            if value is None:
                unknown.append(metric_path)
                checks.append({"metric": metric_path, "value": None,
                               "bounds": [lo, hi], "status": "not_evaluated"})
                continue
            inside = (lo is None or value >= lo) and (hi is None or value <= hi)
            checks.append({"metric": metric_path, "value": value,
                           "bounds": [lo, hi], "status": "inside" if inside else "outside"})
            if not inside:
                violated.append(metric_path)
                code = "E_DOMAIN_OUT_OF_ENVELOPE" if severity == "error" else "W_DOMAIN_OUT_OF_ENVELOPE"
                findings.append(make_finding(
                    code,
                    f"envelope '{env_id}': {metric_path}={value:.6g} outside "
                    f"[{lo}, {hi}]" + (f" — {env.get('description')}" if env.get("description") else ""),
                    metric=metric_path,
                    value=value,
                    limit=[lo, hi],
                    context={"envelope_id": env_id, "citation": env.get("citation"),
                             "severity": severity}))
        for metric_path in unknown:
            findings.append(make_finding(
                "W_DOMAIN_NOT_EVALUATED",
                f"envelope '{env_id}' references unavailable metric '{metric_path}'",
                metric=metric_path,
                context={"envelope_id": env_id}))
        if violated:
            status = "outside"
        elif unknown and len(unknown) == len(bounds):
            status = "not_evaluated"
        else:
            status = "inside"
        results.append({
            "id": env_id,
            "status": status,
            "severity": severity,
            "citation": env.get("citation"),
            "description": env.get("description"),
            "violated_metrics": violated,
            "unevaluated_metrics": unknown,
            "checks": checks,
        })
    return {"envelopes": results, "findings": findings}


def check_mesh_violations(mesh_result: Dict[str, Any]) -> List[Finding]:
    """Translate mesh violations into the shared ``Finding`` structure."""
    findings: List[Finding] = []
    for v in mesh_result.get("violations") or []:
        code = v.get("code")
        if code is None:
            continue
        metric = v.get("metric")
        value = v.get("worst")
        limit = v.get("limit")
        detail = v.get("message") or (
            f"{metric}: {v.get('count')} cell(s) outside limit {limit} (worst {value})")
        findings.append(make_finding(code, detail, metric=metric,
                                     value=None if value is None else float(value),
                                     limit=limit,
                                     context={k: v[k] for k in ("count", "cells", "patches")
                                              if k in v}))
    return findings