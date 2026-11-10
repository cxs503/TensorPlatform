"""Adjudication report -> the single document a campaign DAG verification node consumes.

The DAG endpoint (``POST /api/v1/tensor/campaigns/nodes/{node_id}/verdict``)
accepts exactly ``{"reproduction_verdict": "pass"|"fail", "block_reason": str|None}``
(``NodeVerdictUpdate``).  Rather than let every node re-derive that boolean from
free text, this module projects the adjudication report onto that contract through
declared criteria (``config/xsolver/dag_criteria.json``) and keeps the full evidence
attached, so the verdict is auditable: the payload says *what* was decided, the
document says *why*, with numbers.

``verdict`` is three-valued: ``pass`` only when every criterion passes, ``fail``
when any criterion fails, ``withheld`` when the criteria could not be evaluated
(missing residual / single arm).  ``withheld`` maps to ``fail`` in the DAG payload
because a campaign node must not be closed on unverified evidence -- and because
that mapping is explicit here, it cannot be reinterpreted later.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence

from . import normalize as N
from .conservation import FAIL, PASS, RANK, WARN, WITHHELD, worst
from .jsonschema_lite import load_config, load_schema, validate, validate_or_raise

CRITERIA_CONFIG = load_config("dag_criteria")

#: mirror of the DAG endpoint contract (``NodeVerdictUpdate``); a test keeps it honest
NODE_VERDICT_SCHEMA = {
    "type": "object",
    "required": ["reproduction_verdict"],
    "additionalProperties": False,
    "properties": {
        "reproduction_verdict": {"type": "string", "pattern": "^(pass|fail)$"},
        "block_reason": {"anyOf": [{"type": "string"}, {"type": "null"}]},
    },
}


def _criterion_min_arms(group: Dict[str, Any], spec: Dict[str, Any]) -> Dict[str, Any]:
    arms = group["arms"]
    usable = [a for a in arms if not a["conservation"]["excluded"]]
    ok = len(usable) >= spec["threshold"]
    return {"id": spec["id"], "status": PASS if ok else FAIL,
            "value": len(usable), "threshold": spec["threshold"],
            "detail": f"{len(usable)} arm(s) after conservation exclusion"}


def _criterion_no_conservation_fail(group: Dict[str, Any], spec: Dict[str, Any]) -> Dict[str, Any]:
    excluded = group["excluded_arms"]
    statuses = [a["conservation"]["status"] for a in group["arms"]]
    if not statuses:
        return {"id": spec["id"], "status": WITHHELD, "value": None, "threshold": 0,
                "detail": "no arms"}
    if excluded:
        return {"id": spec["id"], "status": FAIL, "value": len(excluded), "threshold": 0,
                "detail": f"conservation_fail: {excluded}"}
    if all(s in (WITHHELD, WARN) for s in statuses):
        return {"id": spec["id"], "status": WITHHELD, "value": None, "threshold": 0,
                "detail": "no arm records a conservation residual"}
    return {"id": spec["id"], "status": PASS, "value": 0, "threshold": 0,
            "detail": "no conservation_fail; statuses " + ",".join(sorted(set(statuses)))}


def _criterion_all_traceable(group: Dict[str, Any], spec: Dict[str, Any]) -> Dict[str, Any]:
    ok = group["traceability"]["all_arms_traceable"]
    return {"id": spec["id"], "status": PASS if ok else FAIL,
            "value": ok, "threshold": True,
            "detail": "input_digest + solver_version + git_commit present on every arm" if ok
            else "at least one arm lacks input_digest / solver_version / git_commit"}


def _criterion_max_rel_diff(group: Dict[str, Any], spec: Dict[str, Any]) -> Dict[str, Any]:
    quantity = spec.get("quantity", "C_T")
    rows = [c for c in group["comparisons"] if c["quantity"] == quantity]
    values = [c["rel_diff"] for c in rows if c["rel_diff"] is not None]
    if not values:
        return {"id": spec["id"], "status": WITHHELD, "value": None,
                "threshold": spec["threshold"],
                "detail": f"no pair of arms reports {quantity}"}
    value = max(values)
    status = PASS if value <= spec["threshold"] else FAIL
    return {"id": spec["id"], "status": status, "value": value,
            "threshold": spec["threshold"], "quantity": quantity,
            "detail": f"max relative difference on {quantity} over {len(values)} pair(s)"}


def _criterion_no_unattributed(group: Dict[str, Any], spec: Dict[str, Any]) -> Dict[str, Any]:
    kinds = group["attribution"]["kinds"]
    if not kinds:
        if group["status"] == "single_arm":
            return {"id": spec["id"], "status": WITHHELD, "value": [], "threshold": [],
                    "detail": "single arm; nothing to attribute"}
        return {"id": spec["id"], "status": PASS, "value": [], "threshold": ["unattributed"],
                "detail": "no spread to attribute"}
    blocking = [f for f in group["attribution"]["findings"]
                if f["kind"] in ("unattributed", "unresolved_after_alignment")]
    if blocking:
        worst_row = max(blocking, key=lambda f: f["magnitude"] or 0.0)
        return {"id": spec["id"], "status": FAIL, "value": kinds,
                "threshold": ["unattributed", "unresolved_after_alignment"],
                "detail": f"{worst_row['kind']}: {worst_row['statement']}"}
    return {"id": spec["id"], "status": PASS, "value": kinds,
            "threshold": ["unattributed", "unresolved_after_alignment"],
            "detail": "all differences attributed to " + ", ".join(kinds)}


RULES = {
    "min_arms": _criterion_min_arms,
    "no_conservation_fail": _criterion_no_conservation_fail,
    "all_arms_traceable": _criterion_all_traceable,
    "max_rel_diff": _criterion_max_rel_diff,
    "no_unattributed_difference": _criterion_no_unattributed,
}


def evaluate_group(group: Dict[str, Any], criteria: Optional[Sequence[Dict[str, Any]]] = None
                   ) -> List[Dict[str, Any]]:
    criteria = criteria or CRITERIA_CONFIG["criteria"]
    results = []
    for spec in criteria:
        rule = RULES.get(spec["rule"])
        if rule is None:
            raise KeyError(f"unknown DAG criterion rule {spec['rule']!r}")
        results.append(rule(group, spec))
    return results


def build_verdict_document(report: Dict[str, Any], problem_id: Optional[str] = None,
                           criteria: Optional[Sequence[Dict[str, Any]]] = None) -> Dict[str, Any]:
    """Project one adjudication group onto the DAG-consumable verdict document."""
    groups = report.get("groups", [])
    if problem_id is None:
        candidate = next((g for g in groups if g["status"] != "single_arm"), None)
        if candidate is None:
            raise ValueError("report has no multi-arm group to adjudicate")
        group = candidate
    else:
        group = next((g for g in groups if g["problem_id"] == problem_id), None)
        if group is None:
            raise KeyError(f"problem {problem_id!r} not present in the report")

    results = evaluate_group(group, criteria)
    overall = worst(*[r["status"] for r in results])
    if overall == PASS:
        verdict, reproduction = "pass", "pass"
    elif overall == FAIL:
        verdict, reproduction = "fail", "fail"
    else:
        verdict, reproduction = "withheld", "fail"

    blocking = [r for r in results if r["status"] in (FAIL, WITHHELD)]
    if reproduction == "pass":
        block_reason = None
    else:
        parts = [f"{r['id']}: {r.get('detail', '')}" for r in blocking]
        kind_note = ("attribution=" + ",".join(group["attribution"]["kinds"])
                     if group["attribution"]["kinds"] else "attribution=none")
        block_reason = ("cross-solver verdict not established (" + kind_note + "); "
                        + " | ".join(parts))[:2000]

    trace = report.get("trace", {})
    arms = [a for a in group["arms"]]
    document = {
        "schema": "xsolver/verdict/v1",
        "problem_id": group["problem_id"],
        "verdict": verdict,
        "criteria": results,
        "node_verdict_payload": {"reproduction_verdict": reproduction,
                                 "block_reason": block_reason},
        "evidence": {
            "group_status": group["status"],
            "conditions": group["conditions"],
            "comparisons": group["comparisons"],
            "attribution": group["attribution"],
            "conservation": {a["solver"]: a["conservation"]["status"] for a in arms},
            "quantities": {a["solver"]: a["quantities"] for a in arms},
        },
        "trace": {
            "input_digest": N.digest_of({a["case_id"]: a["input_digest"] for a in arms}),
            "solver_versions": {a["solver"]: a["version"] for a in arms},
            "git_commits": {a["solver"]: a["git_commit"] for a in arms},
            "adjudication_digest": trace.get("report_digest", N.digest_of(report)),
        },
    }
    validate_or_raise(document, load_schema("verdict_schema"), f"verdict[{group['problem_id']}]")
    return document


def consume_verdict(document: Dict[str, Any]) -> Dict[str, Any]:
    """What the campaign DAG verification node receives: a validated payload."""
    validate_or_raise(document, load_schema("verdict_schema"), "verdict document")
    payload = document["node_verdict_payload"]
    errors = validate(payload, NODE_VERDICT_SCHEMA)
    if errors:
        raise ValueError("node verdict payload violates the DAG contract: " + "; ".join(errors))
    return {
        "node_verdict_payload": payload,
        "verdict": document["verdict"],
        "problem_id": document["problem_id"],
        "traceable": bool(document["trace"]["input_digest"]),
        "criteria_failed": [c["id"] for c in document["criteria"] if c["status"] in (FAIL, WITHHELD)],
    }