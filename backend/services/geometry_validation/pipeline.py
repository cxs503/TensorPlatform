"""Batch validation pipeline: per-item error recovery, verdicts, report, audit.

Item schema (JSON, one object per batch entry)::

    {
      "id": "case-001",                          # required, unique
      "geometry": {
        "path": "surface.stl",                   # STL | OBJ | IGES(+tessellator)
        "draft": 2.0,                            # waterline height above z_min
        "declared": {"length": 10.0, "beam": 4.0, "draft": 2.0,
                     "block_coefficient": 0.44}, # keys mapped by the profile
        "options": {"length_reference": "overall"}
      },
      "mesh": {
        "path": "case/polyMesh",                 # polyMesh dir or legacy VTK file
        "log": {"yplus": "yPlus.log", "checkmesh": "checkMesh.log"}
      },
      "flow": {"u_inf": 5.0, "nu": 1.0e-6, "reynolds": 5.0e6,
               "wall_distance": 1.0e-5, "cf": null},
      "metadata": {"any": "passthrough"}
    }

``geometry`` and ``mesh`` are both optional individually but a validation gate is
pointless with neither; the item is then reported as ``E_INPUT_MISSING``.

Verdicts
--------
* ``PASS``  – every check inside limits (warnings allowed, they stay visible).
* ``BLOCK`` – at least one finding whose severity is a blocking severity in the
  profile; the payload must not be dispatched to a solver.
* ``ERROR`` – the pipeline itself failed to evaluate the item (unexpected
  exception).  This distinction matters for statistics: a blocked input means the
  gate worked, an error means the gate is untrustworthy.
"""
from __future__ import annotations

import os
import time
import traceback
import uuid
from typing import Any, Dict, Iterable, List, Optional

from . import geometry_extract as gx
from . import interceptor, mesh_quality
from .audit import AuditLog, verify_audit_chain
from .profile import ValidationProfile, canonical_sha256
from .reason_codes import ERROR, GeometryValidationError, make_finding

REPORT_SCHEMA_VERSION = "1.0"
TOOL_VERSION = "1.0.0"
TOOL_NAME = "geometry_validation"

_SEVERITY_RANK = {"error": 0, "warning": 1, "info": 2}


def _sorted_findings(findings: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return sorted(findings, key=lambda f: (_SEVERITY_RANK.get(f.get("severity", "info"), 9),
                                           f.get("code", ""), str(f.get("metric"))))


def _read_text(path: Optional[str]) -> Optional[str]:
    if not path:
        return None
    if not os.path.isfile(path):
        raise GeometryValidationError("E_FILE_NOT_FOUND", f"log file not found: {path}",
                                      path=path)
    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        return fh.read()


def _safe_item_id(item: Any, fallback: str) -> str:
    """Extract an id from a possibly malformed item without ever raising."""
    if isinstance(item, dict):
        value = item.get("id")
        if value:
            return str(value)
    return fallback


def item_digest(result: Dict[str, Any]) -> str:
    """Content hash of a result with volatile fields removed.

    ``duration_ms`` is wall-clock noise; hashing it would make two identical runs
    hash differently and destroy the reproducibility guarantee the audit trail is
    supposed to provide.
    """
    stable = {k: v for k, v in result.items() if k != "duration_ms"}
    return canonical_sha256(stable)


def validate_item(item: Dict[str, Any], profile: ValidationProfile) -> Dict[str, Any]:
    """Validate one batch item. Never raises for *input* problems (they become codes)."""
    started = time.perf_counter()
    if not isinstance(item, dict):
        raise TypeError(f"batch item must be an object, got {type(item).__name__}")
    item_id = _safe_item_id(item, f"item-{uuid.uuid4().hex[:8]}")
    findings: List[Dict[str, Any]] = []
    result: Dict[str, Any] = {
        "id": item_id,
        "verdict": "PASS",
        "reason_codes": [],
        "findings": [],
        "geometry": None,
        "mesh": None,
        "domain": None,
        "inputs": {},
        "metadata": item.get("metadata") or {},
        "duration_ms": 0.0,
    }

    geo_in = item.get("geometry") or {}
    mesh_in = item.get("mesh") or {}
    flow_in = item.get("flow") or {}
    if not geo_in and not mesh_in:
        findings.append(make_finding("E_INPUT_MISSING",
                                     "item carries neither a geometry nor a mesh block").as_dict())

    # ---- 1. surface geometry -------------------------------------------
    if geo_in:
        if not geo_in.get("path"):
            findings.append(make_finding("E_INPUT_MISSING",
                                         "geometry.path is required",
                                         metric="geometry.path").as_dict())
        else:
            try:
                surface = gx.read_triangles(geo_in["path"])
                options = geo_in.get("options") or {}
                metrics = gx.geometry_metrics(
                    surface,
                    draft=geo_in.get("draft"),
                    length_reference=options.get(
                        "length_reference", profile.geometry.get("length_reference", "overall")),
                    declared=geo_in.get("declared"),
                    degenerate_area_rel=float(profile.geometry.get("degenerate_area_rel", 1e-12)))
                declared_check = interceptor.check_declared(
                    metrics, geo_in.get("declared") or {}, profile)
                findings.extend(f.as_dict() for f in interceptor.check_topology(metrics, profile))
                findings.extend(f.as_dict() for f in declared_check["findings"])
                result["geometry"] = {
                    "format": surface["format"],
                    "file": surface["meta"],
                    "metrics": metrics,
                    "declared_check": {"tolerance_rel": declared_check["tolerance_rel"],
                                       "comparisons": declared_check["comparisons"]},
                }
                result["inputs"]["geometry_sha256"] = surface["meta"]["sha256"]
            except GeometryValidationError as exc:
                findings.append(exc.as_finding().as_dict())

    # ---- 2. volume mesh quality ----------------------------------------
    if mesh_in:
        try:
            log_cfg = mesh_in.get("log") or {}
            yplus_text = _read_text(log_cfg.get("yplus"))
            checkmesh_text = _read_text(log_cfg.get("checkmesh"))
            mesh_payload: Optional[Dict[str, Any]] = None
            path = mesh_in.get("path")
            if path:
                if os.path.isdir(path):
                    mesh_payload = mesh_quality.read_openfoam_polymesh(path)
                elif os.path.isfile(path):
                    suffix = os.path.splitext(path)[1].lower()
                    if suffix != ".vtk":
                        raise GeometryValidationError(
                            "E_FORMAT_UNSUPPORTED",
                            f"mesh suffix '{suffix}' not supported (use .vtk or a polyMesh dir)",
                            path=path)
                    mesh_payload = mesh_quality.read_vtk_ascii(path)
                    result["inputs"]["mesh_sha256"] = gx.file_sha256(path)
                else:
                    raise GeometryValidationError("E_FILE_NOT_FOUND",
                                                  f"mesh path not found: {path}", path=path)
            if mesh_payload is None and not (yplus_text or checkmesh_text):
                findings.append(make_finding(
                    "W_MESH_QUALITY_UNCHECKED",
                    "mesh block present but no readable mesh or log — quality not evaluated",
                    metric="mesh").as_dict())
            elif mesh_payload is not None:
                mesh_eval = mesh_quality.evaluate_mesh(
                    mesh_payload, profile.mesh, flow=flow_in or None,
                    wall_log_text=yplus_text)
                if checkmesh_text:
                    mesh_eval["checkmesh_log"] = mesh_quality.parse_checkmesh_log(checkmesh_text)
                findings.extend(f.as_dict() for f in interceptor.check_mesh_violations(mesh_eval))
                result["mesh"] = mesh_eval
            else:
                log_eval: Dict[str, Any] = {"source": "logs_only"}
                if checkmesh_text:
                    log_eval.update(mesh_quality.parse_checkmesh_log(checkmesh_text))
                if yplus_text:
                    patches = mesh_quality.parse_yplus_log(yplus_text)
                    log_eval["y_plus"] = {"source": "solver_log", "patches": patches}
                    band = profile.mesh.get("y_plus_band")
                    if band and patches:
                        lo, hi = float(band["min"]), float(band["max"])
                        outside = [p for p, s in patches.items()
                                   if s["max"] > hi or s["min"] < lo]
                        if outside:
                            findings.append(make_finding(
                                "E_MESH_YPLUS",
                                f"patches {sorted(outside)} outside y+ band [{lo}, {hi}]",
                                metric="y_plus",
                                limit=[lo, hi],
                                context={"patches": sorted(outside)}).as_dict())
                findings.extend(f.as_dict() for f in interceptor.check_mesh_violations(
                    {"violations": []}))
                result["mesh"] = log_eval
        except GeometryValidationError as exc:
            findings.append(exc.as_finding().as_dict())

    # ---- 3. applicability envelopes ------------------------------------
    geo_metrics = (result.get("geometry") or {}).get("metrics")
    if geo_metrics:
        env = interceptor.check_envelopes(geo_metrics, profile)
        findings.extend(f.as_dict() for f in env["findings"])
        result["domain"] = {"envelopes": env["envelopes"]}

    # ---- 4. verdict -----------------------------------------------------
    findings = _sorted_findings(findings)
    result["findings"] = findings
    result["reason_codes"] = sorted({f["code"] for f in findings})
    block_sev = set(profile.block_severities)
    result["verdict"] = "BLOCK" if any(f.get("severity") in block_sev for f in findings) else "PASS"
    result["duration_ms"] = round((time.perf_counter() - started) * 1000.0, 3)
    return result


def validate_batch(items: Iterable[Dict[str, Any]],
                   profile: ValidationProfile,
                   audit: Optional[AuditLog] = None,
                   continue_on_error: bool = True,
                   strict: bool = False,
                   run_metadata: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Validate a batch; per-item failures are recorded, never propagated (unless strict)."""
    items = list(items)
    run_id = f"gv-{uuid.uuid4().hex[:12]}"
    started_wall = time.time()
    started = time.perf_counter()
    if audit is not None:
        audit.append("run_start", {
            "run_id": run_id,
            "profile": profile.summary(),
            "n_items": len(items),
            "strict": strict,
            "continue_on_error": continue_on_error,
            "tool": {"name": TOOL_NAME, "version": TOOL_VERSION},
            "metadata": run_metadata or {},
        })

    results: List[Dict[str, Any]] = []
    seen_ids: Dict[str, int] = {}
    for index, item in enumerate(items):
        try:
            res = validate_item(item, profile)
        except Exception as exc:  # error recovery: keep the batch alive
            if not continue_on_error:
                raise
            res = {
                "id": _safe_item_id(item, f"item-{index}"),
                "verdict": "ERROR",
                "reason_codes": ["E_ITEM_EXCEPTION"],
                "findings": [make_finding(
                    "E_ITEM_EXCEPTION",
                    f"unexpected {type(exc).__name__}: {exc}",
                    context={"traceback_tail": traceback.format_exc().strip().splitlines()[-3:],
                             "item_index": index}).as_dict()],
                "geometry": None,
                "mesh": None,
                "domain": None,
                "inputs": {},
                "metadata": {},
                "duration_ms": 0.0,
            }
            if strict:
                raise
        # uniqueness bookkeeping (duplicate ids would corrupt the audit trail)
        seen_ids[res["id"]] = seen_ids.get(res["id"], 0) + 1
        if seen_ids[res["id"]] > 1:
            res["id"] = f"{res['id']}#{seen_ids[res['id']]}"
            res["findings"] = _sorted_findings(res["findings"] + [make_finding(
                "E_INPUT_TYPE", "duplicate item id was suffixed for uniqueness").as_dict()])
        results.append(res)
        if audit is not None:
            audit.append("item_result", {
                "run_id": run_id,
                "item_id": res["id"],
                "verdict": res["verdict"],
                "reason_codes": res["reason_codes"],
                "item_sha256": item_digest(res),
                "geometry_sha256": res["inputs"].get("geometry_sha256"),
                "mesh_sha256": res["inputs"].get("mesh_sha256"),
                "duration_ms": res["duration_ms"],
            })

    results.sort(key=lambda r: r["id"])
    n_total = len(results)
    n_pass = sum(1 for r in results if r["verdict"] == "PASS")
    n_block = sum(1 for r in results if r["verdict"] == "BLOCK")
    n_error = sum(1 for r in results if r["verdict"] == "ERROR")
    code_counts: Dict[str, int] = {}
    env_counts: Dict[str, Dict[str, int]] = {}
    for res in results:
        for code in res["reason_codes"]:
            code_counts[code] = code_counts.get(code, 0) + 1
        for env in (res.get("domain") or {}).get("envelopes", []):
            slot = env_counts.setdefault(env["id"], {"inside": 0, "outside": 0,
                                                     "not_evaluated": 0})
            slot[env["status"]] = slot.get(env["status"], 0) + 1

    report: Dict[str, Any] = {
        "schema_version": REPORT_SCHEMA_VERSION,
        "tool": {"name": TOOL_NAME, "version": TOOL_VERSION},
        "run": {
            "run_id": run_id,
            "started_at_unix": round(started_wall, 3),
            "duration_s": round(time.perf_counter() - started, 3),
            "n_items": n_total,
            "n_pass": n_pass,
            "n_block": n_block,
            "n_error": n_error,
            "block_rate": round(n_block / n_total, 6) if n_total else 0.0,
            "strict": strict,
            "continue_on_error": continue_on_error,
            "block_severities": profile.block_severities,
            "metadata": run_metadata or {},
        },
        "profile": profile.summary(),
        "items": results,
        "summary": {
            "reason_code_counts": dict(sorted(code_counts.items())),
            "envelope_status_counts": dict(sorted(env_counts.items())),
            "verdict_counts": {"PASS": n_pass, "BLOCK": n_block, "ERROR": n_error},
            "items_sha256": canonical_sha256([item_digest(r) for r in results]),
        },
    }

    if audit is not None:
        audit.append("run_end", {
            "run_id": run_id,
            "verdict_counts": report["summary"]["verdict_counts"],
            "reason_code_counts": report["summary"]["reason_code_counts"],
            "items_sha256": report["summary"]["items_sha256"],
        })
        report["audit"] = audit.summary()
        report["audit"]["chain"] = verify_audit_chain(audit.path)
        report["audit"]["report_sha256"] = canonical_sha256(
            {k: report[k] for k in ("schema_version", "tool", "run", "profile", "items", "summary")})
    return report


def load_batch(path: str) -> List[Dict[str, Any]]:
    """Load a batch file: either a JSON list or ``{"items": [...]}``."""
    import json

    if not os.path.isfile(path):
        raise GeometryValidationError("E_FILE_NOT_FOUND", f"batch file not found: {path}",
                                      path=path)
    with open(path, "r", encoding="utf-8") as fh:
        doc = json.load(fh)
    if isinstance(doc, dict):
        doc = doc.get("items") or []
    if not isinstance(doc, list):
        raise GeometryValidationError("E_PARSE", "batch file must be a list or {items: [...]}",
                                      path=path)
    return doc


def exit_code(report: Dict[str, Any]) -> int:
    """0 = all PASS, 1 = at least one BLOCK, 2 = at least one ERROR (harness fault)."""
    counts = report["summary"]["verdict_counts"]
    if counts.get("ERROR"):
        return 2
    if counts.get("BLOCK"):
        return 1
    return 0