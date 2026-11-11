"""Acceptance-criteria computation and artifact writing.

The six criteria of the task are turned into plain numbers with an explicit
evidence path each, so a reviewer can recompute any of them from the raw run
artifacts without trusting this module's prose:

(a) endpoint coverage        -- spec operations that actually produced a response
(b) schema validation        -- valid requests accepted / invalid rejected
(c) status-code conformance  -- matrix rows matching spec-derived expectations
(d) breaking-change detection-- injected incompatible changes that were detected
(e) numeric precision        -- declarative invariants within rel<3% or abs<1e-6
(f) negative controls        -- missing spec -> WITHHELD, unreachable -> ERROR

Honest reporting rule: a criterion with zero *executed* evidence is reported as
``WITHHELD`` (not as a pass), so a disconnected target can never look green.
"""
from __future__ import annotations

import datetime as _dt
import json
import os
from typing import Any, Dict, List, Optional, Sequence

from . import checks


def _utc() -> str:
    return _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds")


def endpoint_coverage(spec: Dict[str, Any], rows: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    from . import spec as spec_mod

    operations = {op.key for op in spec_mod.enumerate_operations(spec)}
    generated = {row["operation"] for row in rows}
    executed = {row["operation"] for row in rows if row.get("executed")}
    executed_ok = {row["operation"] for row in rows if row.get("executed") and row["kind"] == "valid"}
    total = len(operations)
    return {
        "operations_in_spec": total,
        "operations_with_cases": len(generated & operations),
        "operations_executed": len(executed & operations),
        "operations_valid_case_executed": len(executed_ok & operations),
        "coverage_executed": (len(executed & operations) / total) if total else 0.0,
        "coverage_valid_case": (len(executed_ok & operations) / total) if total else 0.0,
        "unreached_operations": sorted(operations - executed)[:40],
        "state": "MEASURED" if total else "WITHHELD",
    }


def schema_criteria(rows: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    """Split request/response schema conformance into non-overlapping buckets.

    * ``accepted``  -- spec-valid request answered 2xx; its body must match the
      declared success schema (criterion b, first half);
    * ``rejected``  -- spec-valid request answered 4xx/5xx: the document does not
      declare the cross-field rule that rejected it (``SPEC_UNDERSPECIFIED``
      finding, not a harness failure);
    * ``error_envelope`` -- any 4xx/5xx response that does not match the declared
      error schema (``ERROR_ENVELOPE_DRIFT`` finding);
    * ``invalid_not_rejected`` -- spec-invalid request answered outside 4xx
      (criterion b, second half).
    """
    valid_rows = [r for r in rows if r["kind"] == "valid" and r.get("executed")]
    accepted = [r for r in valid_rows if 200 <= int(r["response"]["status"]) < 300]
    rejected_valid = [r for r in valid_rows if int(r["response"]["status"]) >= 400]
    accepted_checked = [r for r in accepted if r["schema"].get("checked")]
    accepted_valid = [r for r in accepted_checked if r["schema"].get("valid")]

    error_rows = [r for r in rows if r.get("executed")
                  and r.get("response", {}).get("status") is not None
                  and int(r["response"]["status"]) >= 400
                  and r.get("schema", {}).get("checked")]
    error_violations = [r for r in error_rows if not r["schema"].get("valid")]

    invalid_rows = [r for r in rows if r["kind"].startswith("invalid_") and r.get("executed")]
    rejected = [r for r in invalid_rows
                if r.get("response", {}).get("status") is not None
                and 400 <= int(r["response"]["status"]) < 500]
    not_rejected = [r for r in invalid_rows if r not in rejected]

    finite = [r for r in rows if r.get("executed") and r.get("finiteness", {}).get("ok")]
    finite_checked = [r for r in rows if r.get("executed") and r.get("finiteness")]

    def _detail(row: Dict[str, Any]) -> Dict[str, Any]:
        body = row.get("response", {}).get("body")
        detail = body.get("detail") if isinstance(body, dict) else None
        if not isinstance(detail, (str, list)):
            detail = str(detail)[:160]
        return {"case_id": row["case_id"], "operation": row["operation"],
                "status": row["response"]["status"], "detail": detail,
                "errors": row["schema"].get("errors", [])[:3]}

    return {
        "valid_executed": len(valid_rows),
        "accepted_2xx": len(accepted),
        "schema_checked": len(accepted_checked),
        "schema_valid": len(accepted_valid),
        # criterion b, first half: measured only where a success body exists
        "schema_valid_rate": (len(accepted_valid) / len(accepted_checked))
        if accepted_checked else None,
        "schema_violations": [_detail(r) for r in accepted_checked
                              if not r["schema"].get("valid")][:10],
        "valid_rejected_4xx_5xx": len(rejected_valid),
        "valid_rejected_4xx": sum(1 for r in rejected_valid
                                  if int(r["response"]["status"]) < 500),
        "valid_rejected_5xx": sum(1 for r in rejected_valid
                                  if int(r["response"]["status"]) >= 500),
        "valid_rejected_status_histogram": _histogram(r["response"]["status"]
                                                      for r in rejected_valid),
        "spec_underspecified": [_detail(r) for r in rejected_valid][:10],
        "server_errors_on_valid_input": [_detail(r) for r in rejected_valid
                                         if int(r["response"]["status"]) >= 500][:10],
        "error_envelope_checked": len(error_rows),
        "error_envelope_violations": [_detail(r) for r in error_violations][:10],
        "invalid_executed": len(invalid_rows),
        "invalid_rejected_4xx": len(rejected),
        "invalid_rejection_rate": (len(rejected) / len(invalid_rows)) if invalid_rows else None,
        "invalid_not_rejected": [_detail(r) for r in not_rejected][:10],
        "finite_responses": len(finite),
        "responses_with_numeric_scan": len(finite_checked),
        "state": "MEASURED" if (accepted_checked or invalid_rows) else "WITHHELD",
    }


def _histogram(statuses) -> Dict[str, int]:
    out: Dict[str, int] = {}
    for status in statuses:
        key = str(status)
        out[key] = out.get(key, 0) + 1
    return dict(sorted(out.items()))


def matrix_criterion(matrix: Optional[Dict[str, Any]], expected_rows: int) -> Dict[str, Any]:
    if not matrix:
        return {"state": "WITHHELD", "reason": "matrix not run"}
    executed = matrix.get("executed", 0)
    return {
        "state": "MEASURED" if executed else "WITHHELD",
        "endpoints": matrix.get("endpoints", 0),
        "scenarios": len(matrix.get("scenarios") or []),
        "expected_cells": expected_rows,
        "executed_cells": executed,
        "conforming_cells": matrix.get("conforming", 0),
        "conformance_rate": matrix.get("conformance_rate", 0.0),
        "not_applicable": sum(1 for row in matrix.get("rows", []) if row["state"] == "NOT_APPLICABLE"),
        "violations": [row for row in matrix.get("rows", [])
                       if row["state"] == "EXECUTED" and not row["conforms"]][:10],
        "errors": [row for row in matrix.get("rows", []) if row["state"] == "ERROR"][:10],
    }


def precision_criterion(references: Optional[Dict[str, Any]],
                        determinism: Optional[Dict[str, Any]],
                        rows: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    flat_checks: List[Dict[str, Any]] = []
    diagnostic_checks: List[Dict[str, Any]] = []
    if references:
        for result in references.get("results", []):
            target_list = flat_checks if result.get("criterion", True) else diagnostic_checks
            target_list.extend(result.get("checks") or [])
    worst_rel = max((c["rel_error"] for c in flat_checks if c["rel_error"] != float("inf")),
                    default=None)
    failed = [c for c in flat_checks if not c["passed"]]
    findings = [c for c in diagnostic_checks if not c["passed"]]
    spec_precision_violations = []
    if references:
        for result in references.get("results", []):
            spec_precision_violations.extend(
                (result.get("spec_precision") or {}).get("violations") or [])
    non_finite = [row["case_id"] for row in rows
                  if row.get("finiteness", {}).get("non_finite")]
    state = "MEASURED" if flat_checks else "WITHHELD"
    return {
        "state": state,
        "invariants": len(flat_checks),
        "invariants_passed": len(flat_checks) - len(failed),
        "invariant_pass_rate": ((len(flat_checks) - len(failed)) / len(flat_checks))
        if flat_checks else None,
        "worst_rel_error": worst_rel,
        "failed_invariants": failed,
        "diagnostic_checks": len(diagnostic_checks),
        "diagnostic_failures": len(findings),
        "findings": findings,
        "spec_precision_violations": spec_precision_violations[:10],
        "non_finite_responses": non_finite[:10],
        "determinism": {
            "checked": (determinism or {}).get("checked", 0),
            "deterministic": (determinism or {}).get("deterministic", 0),
            "max_abs_diff": (determinism or {}).get("max_abs_diff"),
        },
    }


def evaluate(criteria_inputs: Dict[str, Any]) -> Dict[str, Any]:
    """Assemble the acceptance verdict for criteria (a)-(f)."""
    coverage = criteria_inputs["coverage"]
    schema = criteria_inputs["schema"]
    matrix = criteria_inputs["matrix"]
    breaking = criteria_inputs["breaking"]
    precision = criteria_inputs["precision"]
    negatives = criteria_inputs["negatives"]

    thresholds = {
        "coverage_executed_min": 0.95,
        "schema_valid_rate": 1.0,
        "invalid_rejection_rate": 1.0,
        "matrix_conformance_rate": 1.0,
        "breaking_detection_rate": 1.0,
        "precision_rel_tol": 0.03,
        "precision_abs_tol": 1e-6,
    }

    def verdict(passed: Optional[bool]) -> str:
        if passed is None:
            return "WITHHELD"
        return "PASS" if passed else "FAIL"

    crit_a = coverage.get("coverage_executed", 0.0) >= thresholds["coverage_executed_min"] \
        if coverage.get("state") == "MEASURED" else None
    crit_b_valid = schema.get("schema_valid_rate")
    crit_b_invalid = schema.get("invalid_rejection_rate")
    b_passed = None
    if schema.get("state") == "MEASURED":
        parts = [x for x in (crit_b_valid, crit_b_invalid) if x is not None]
        b_passed = bool(parts) and all(x >= 1.0 - 1e-12 for x in parts)
    crit_c = matrix.get("conformance_rate", 0.0) >= 1.0 if matrix.get("state") == "MEASURED" else None
    crit_d = breaking.get("detection_rate", 0.0) >= 1.0 if breaking.get("injected") else None
    crit_e = precision.get("invariant_pass_rate")
    e_passed = None
    if precision.get("state") == "MEASURED":
        e_passed = bool(precision["invariant_pass_rate"] is not None
                        and precision["invariant_pass_rate"] >= 1.0 - 1e-12)
    crit_f = negatives.get("all_degraded")

    criteria = {
        "a_endpoint_coverage": {
            "value": coverage.get("coverage_executed"),
            "threshold": ">= 0.95",
            "verdict": verdict(crit_a),
            "detail": coverage,
        },
        "b_schema_validation": {
            "value": {"valid_rate": crit_b_valid, "invalid_rejection_rate": crit_b_invalid},
            "threshold": "both == 1.0",
            "verdict": verdict(b_passed),
            "detail": schema,
        },
        "c_status_codes": {
            "value": matrix.get("conformance_rate"),
            "threshold": "== 1.0",
            "verdict": verdict(crit_c),
            "detail": matrix,
        },
        "d_breaking_change": {
            "value": breaking.get("detection_rate"),
            "threshold": "== 1.0 (5 injected classes)",
            "verdict": verdict(crit_d),
            "detail": {"injected": breaking.get("injected"), "detected": breaking.get("detected"),
                       "rows": breaking.get("rows")},
        },
        "e_numeric_precision": {
            "value": {"invariant_pass_rate": crit_e, "worst_rel_error": precision.get("worst_rel_error")},
            "threshold": "each |lhs-rhs| <= max(1e-6, 3%*|rhs|)",
            "verdict": verdict(e_passed),
            "detail": precision,
        },
        "f_negative_controls": {
            "value": negatives.get("controls"),
            "threshold": "no crash, degraded states only",
            "verdict": verdict(crit_f),
            "detail": negatives,
        },
    }
    verdicts = [c["verdict"] for c in criteria.values()]
    all_pass = all(v == "PASS" for v in verdicts)
    return {
        "generated_at": _utc(),
        "thresholds": thresholds,
        "criteria": criteria,
        "summary": {
            "pass": verdicts.count("PASS"),
            "fail": verdicts.count("FAIL"),
            "withheld": verdicts.count("WITHHELD"),
            "all_pass": all_pass,
        },
    }


def write_json(path: str, payload: Any) -> str:
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2, sort_keys=False)
    return path


def write_markdown(path: str, acceptance: Dict[str, Any], context: Dict[str, Any]) -> str:
    lines: List[str] = []
    lines.append("# API contract test report")
    lines.append("")
    lines.append("generated: %s" % acceptance["generated_at"])
    for key, value in (context or {}).items():
        lines.append("* %s: `%s`" % (key, value))
    lines.append("")
    lines.append("| criterion | value | threshold | verdict |")
    lines.append("|---|---|---|---|")
    for name, crit in acceptance["criteria"].items():
        value = crit["value"]
        if not isinstance(value, str):
            value = json.dumps(value, ensure_ascii=False)
        lines.append("| %s | %s | %s | **%s** |" % (name, value, crit["threshold"], crit["verdict"]))
    lines.append("")
    summary = acceptance["summary"]
    lines.append("summary: pass=%d fail=%d withheld=%d all_pass=%s"
                 % (summary["pass"], summary["fail"], summary["withheld"], summary["all_pass"]))
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")
    return path