"""Contract-run orchestration: cases, matrix, invariants, determinism, probes.

Everything here is generic: request payloads, endpoint lists and invariant
expressions arrive as data, so the same code audits any OpenAPI service.

Execution is *checkpointed*: every case row is appended to a JSONL file as soon
as it completes, and a resumed run skips rows already present.  That matters for
long sweeps against a supervised service (a restart mid-run then costs seconds,
not the whole sweep) and it keeps the raw evidence auditable.
"""
from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Tuple

from . import checks, generate, spec as spec_mod


# ---------------------------------------------------------------------------
# case execution
# ---------------------------------------------------------------------------


def _read_checkpoint(path: str) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    if not path or not os.path.exists(path):
        return rows
    with open(path, "r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except Exception:  # noqa: BLE001 - a truncated tail line is tolerable
                continue
    return rows


def _append_checkpoint(path: Optional[str], row: Dict[str, Any]) -> None:
    if not path:
        return
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(row, ensure_ascii=False) + "\n")


def execute_case(target: Any, spec: Dict[str, Any], case: generate.Case) -> Dict[str, Any]:
    """Run one case and validate the answer against the spec."""
    response = target.request(case.method, case.path, query=case.query,
                              body=case.body, has_body=case.has_body)
    op = spec_mod.Operation(case.path, case.method)
    row: Dict[str, Any] = {
        "case_id": case.case_id,
        "operation": case.operation,
        "kind": case.kind,
        "method": case.method,
        "path": case.path,
        "expected": case.expected,
        "note": case.note,
        "response": response.as_dict(),
        "status_conforms": checks.status_conforms(case.expected.get("status") or [], response.status),
        "executed": response.status is not None,
    }
    schema_result = checks.SchemaResult(checked=False, valid=True)
    if response.ok and response.body is not None and case.kind in ("valid", "matrix_valid"):
        response_schema = spec_mod.response_schema(spec, op, str(response.status))
        schema_result = checks.validate_schema(response.body, response_schema)
    row["schema"] = schema_result.as_dict()
    row["finiteness"] = checks.finiteness(response.body) if response.ok else {"numeric_fields": 0,
                                                                             "non_finite": [],
                                                                             "ok": True}
    return row


def run_cases(target: Any, spec: Dict[str, Any], cases: Sequence[generate.Case],
              checkpoint_path: Optional[str] = None, resume: bool = False,
              sleep_s: float = 0.0,
              progress: Optional[Callable[[int, int, Dict[str, Any]], None]] = None) -> List[Dict[str, Any]]:
    """Execute cases (optionally resuming from a checkpoint) and return all rows."""
    rows: List[Dict[str, Any]] = _read_checkpoint(checkpoint_path) if resume else []
    if checkpoint_path and not resume and os.path.exists(checkpoint_path):
        os.remove(checkpoint_path)
    done = {row.get("case_id") for row in rows}
    total = len(cases)
    for index, case in enumerate(cases, start=1):
        if case.case_id in done:
            continue
        row = execute_case(target, spec, case)
        rows.append(row)
        _append_checkpoint(checkpoint_path, row)
        if progress:
            progress(index, total, row)
        if sleep_s:
            time.sleep(sleep_s)
    return rows


# ---------------------------------------------------------------------------
# status-code matrix
# ---------------------------------------------------------------------------


def run_matrix(target: Any, spec: Dict[str, Any], endpoints: Sequence[Dict[str, Any]],
               scenarios: Sequence[str] = generate.MATRIX_SCENARIOS) -> Dict[str, Any]:
    rows: List[Dict[str, Any]] = []
    for endpoint in endpoints:
        path = endpoint["path"]
        method = endpoint.get("method", "post")
        op = spec_mod.Operation(path, method)
        overrides = {"body": endpoint["body"]} if endpoint.get("body") is not None else {}
        for scenario in scenarios:
            case = generate.matrix_case(spec, op, scenario, overrides)
            if case is None:
                rows.append({
                    "endpoint": path, "method": method, "scenario": scenario,
                    "state": "NOT_APPLICABLE",
                    "reason": "operation has no required field to violate",
                    "conforms": None,
                })
                continue
            response = target.request(case.method, case.path, query=case.query,
                                      body=case.body, has_body=case.has_body)
            conforms = checks.status_conforms(case.expected.get("status") or [], response.status)
            rows.append({
                "endpoint": path, "method": method, "scenario": scenario,
                "state": "EXECUTED" if response.ok else "ERROR",
                "expected": case.expected.get("status"),
                "actual": response.status,
                "error": response.error,
                "conforms": conforms,
                "note": case.note,
                "elapsed_ms": round(response.elapsed_ms, 2),
            })
    executed = [r for r in rows if r["state"] == "EXECUTED"]
    conforming = [r for r in executed if r["conforms"]]
    return {
        "scenarios": list(scenarios),
        "endpoints": len(endpoints),
        "rows": rows,
        "executed": len(executed),
        "conforming": len(conforming),
        "conformance_rate": (len(conforming) / len(executed)) if executed else 0.0,
    }


# ---------------------------------------------------------------------------
# declarative numeric invariants
# ---------------------------------------------------------------------------


def run_references(target: Any, spec: Dict[str, Any], references: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    results: List[Dict[str, Any]] = []
    for reference in references:
        path = reference["path"]
        method = reference.get("method", "post")
        body = reference.get("request") or {}
        response = target.request(method, path, query=reference.get("query") or {},
                                  body=body, has_body=bool(body))
        base = {
            "reference": reference.get("name", path),
            "endpoint": path,
            "method": method,
            "status": response.status,
            "error": response.error,
        }
        if not response.ok or not isinstance(response.body, dict):
            results.append({**base, "state": "ERROR", "checks": []})
            continue
        context: Dict[str, Any] = {key.replace(".", "__"): value
                                   for key, value in checks.flatten(body).items()
                                   if isinstance(value, (int, float)) and not isinstance(value, bool)}
        # optional multi-endpoint context: prefetch other responses into
        # ``<name>_<leaf>`` so an invariant can relate two endpoints.
        prefetch_rows = []
        for call in reference.get("prefetch") or []:
            pre_resp = target.request(call.get("method", "post"), call["path"],
                                      query=call.get("query") or {},
                                      body=call.get("request") or {},
                                      has_body=bool(call.get("request")))
            prefetch_rows.append({"name": call.get("name", call["path"]),
                                  "status": pre_resp.status, "error": pre_resp.error})
            if not pre_resp.ok or not isinstance(pre_resp.body, (dict, list)):
                continue
            prefix = call.get("name") or "pre"
            for key, value in checks.flatten(pre_resp.body).items():
                if isinstance(value, (int, float)) and not isinstance(value, bool):
                    context["%s_%s" % (prefix, key.replace(".", "__"))] = value
        context.update({key: value for key, value in body.items()
                        if isinstance(value, (int, float)) and not isinstance(value, bool)})
        # expose response leaves both flat and bare
        flat_response = checks.flatten(response.body)
        context.update({key: value for key, value in flat_response.items()
                        if isinstance(value, (int, float)) and not isinstance(value, bool)})
        checks_out = []
        for item in reference.get("checks") or []:
            result = checks.check_invariant(item.get("id") or item.get("lhs", "?"), path,
                                            response.body, item, context)
            checks_out.append(result.as_dict())
        results.append({**base, "state": "CHECKED", "checks": checks_out,
                        "prefetch": prefetch_rows,
                        "criterion": reference.get("criterion", True),
                        "spec_precision": checks.spec_declared_precision(
                            response.body, spec_mod.response_schema(spec, spec_mod.Operation(path, method), "200"))})
    all_checks = [check for result in results for check in result["checks"]]
    return {
        "references": len(references),
        "executed": sum(1 for r in results if r["state"] == "CHECKED"),
        "checks": len(all_checks),
        "passed": sum(1 for check in all_checks if check["passed"]),
        "pass_rate": (sum(1 for check in all_checks if check["passed"]) / len(all_checks))
        if all_checks else 0.0,
        "results": results,
    }


# ---------------------------------------------------------------------------
# determinism and sensitivity probes
# ---------------------------------------------------------------------------


def run_determinism(target: Any, cases: Sequence[generate.Case], repeats: int = 2) -> Dict[str, Any]:
    rows = []
    for case in cases:
        responses = []
        for _ in range(repeats):
            responses.append(target.request(case.method, case.path, query=case.query,
                                             body=case.body, has_body=case.has_body))
        if any(not r.ok for r in responses):
            rows.append({"case_id": case.case_id, "state": "ERROR"})
            continue
        diffs = [checks.numeric_diff(responses[0].body, other.body) for other in responses[1:]]
        worst = max((d["max_abs_diff"] for d in diffs), default=0.0)
        rows.append({
            "case_id": case.case_id,
            "state": "CHECKED",
            "compared": diffs[0]["compared"] if diffs else 0,
            "max_abs_diff": worst,
            "deterministic": worst < 1e-6,
            "differing": sorted({name for d in diffs for name in d["differing"]})[:10],
        })
    checked = [row for row in rows if row["state"] == "CHECKED"]
    return {
        "repeats": repeats,
        "cases": len(rows),
        "checked": len(checked),
        "deterministic": sum(1 for row in checked if row["deterministic"]),
        "max_abs_diff": max((row["max_abs_diff"] for row in checked), default=0.0),
        "rows": rows,
    }


def run_sensitivity(target: Any, spec: Dict[str, Any],
                    probes: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    """Vary one request field and measure which response numbers react.

    A numeric response field that does not change at all across a sweep of a
    physically meaningful input is reported as ``INVARIANT``: constant/MOCK
    numerics are caught by the contract harness, not only by review.
    """
    out: List[Dict[str, Any]] = []
    for probe in probes:
        path = probe["path"]
        method = probe.get("method", "post")
        base_body = dict(probe.get("request") or {})
        field = probe["vary"]
        values = probe.get("values") or []
        series: List[Dict[str, Any]] = []
        errors: List[str] = []
        for value in values:
            body = dict(base_body)
            body[field] = value
            response = target.request(method, path, body=body, has_body=True)
            if not response.ok:
                errors.append("%s: %s" % (value, response.error or response.status))
                continue
            series.append(checks.numeric_leaves(response.body))
        watched = probe.get("watch") or []
        comparison: Dict[str, Any] = {}
        keys = set().union(*[set(s) for s in series]) if series else set()
        for key in sorted(watched or keys):
            samples = [s.get(key) for s in series if key in s]
            if not samples:
                continue
            span = (max(samples) - min(samples)) / max(abs(max(samples)), 1e-12)
            comparison[key] = {
                "samples": [round(float(v), 9) for v in samples],
                "relative_span": span,
                "state": "SENSITIVE" if span > 1e-6 else "INVARIANT",
            }
        invariant = sorted(k for k, v in comparison.items() if v["state"] == "INVARIANT")
        out.append({
            "path": path,
            "vary": field,
            "values": list(values),
            "errors": errors,
            "fields": comparison,
            "invariant_fields": invariant,
            "verdict": "MOCK_SUSPECT" if invariant else ("NO_DATA" if not comparison else "RESPONSIVE"),
        })
    return {
        "probes": len(out),
        "mock_suspect": sum(1 for row in out if row["verdict"] == "MOCK_SUSPECT"),
        "results": out,
    }


# ---------------------------------------------------------------------------
# negative controls (criterion f)
# ---------------------------------------------------------------------------


def negative_controls(spec_source_missing: str, spec_source_malformed: str,
                      unreachable_base_url: str, health_path: str = "") -> Dict[str, Any]:
    """Exercise the three degradation paths; all must return values, not raise."""
    results: Dict[str, Any] = {}

    missing = spec_mod.load_spec(spec_source_missing)
    results["spec_missing"] = {"state": missing.status, "reason": missing.reason,
                               "crash": False}

    malformed = spec_mod.load_spec(spec_source_malformed)
    results["spec_malformed"] = {"state": malformed.status, "reason": malformed.reason,
                                 "crash": False}

    target = None
    try:
        from . import targets

        if unreachable_base_url:
            target = targets.HttpTarget(unreachable_base_url, timeout=2.0, retries=1,
                                        health_path=health_path, recovery_wait=2.0)
        else:
            target = targets.UnreachableTarget()
        response = target.request("get", "/__contract_probe__")
        results["endpoint_unreachable"] = {
            "state": "ERROR" if not response.ok else "UNEXPECTED_OK",
            "error": response.error,
            "status": response.status,
            "crash": False,
        }
    except Exception as exc:  # noqa: BLE001 - a crash here is a *finding*
        results["endpoint_unreachable"] = {"state": "CRASH", "error": repr(exc), "crash": True}
    finally:
        del target

    return {
        "all_degraded": all(
            value.get("crash") is False and value.get("state") in {"WITHHELD", "ERROR"}
            for value in results.values()
        ),
        "controls": results,
    }