"""Automated acceptance gate: evaluate machine-computable criteria, adjudicate
PASS / FAIL / WITHHELD, and protect honest FAIL.

Adjudication rules (each rule is separately asserted in the test suite):

R1  a criterion whose metric is absent or non-finite is WITHHELD, never FAIL
    (missing evidence is not a violation);
R2  a required criterion that is violated makes the node FAIL, no matter what
    the worker declared ("evidence beats declaration" -- overclaim is flagged);
R3  if no required criterion is violated but at least one is WITHHELD, the node
    is WITHHELD (cannot adjudicate);
R4  a worker that declared ``honest_fail`` and whose evidence does in fact fail
    is recorded as ``honest_fail`` (S-N negative track) and is **not** counted
    in the failure rate -- honest FAIL is a deliverable, not a defect;
R5  a worker that declared ``honest_fail`` while every required criterion
    passes is adjudicated on the evidence (PASS) and flagged
    ``declaration_conflict`` (declarations cannot buy protection);
R6  optional criteria never change the verdict, only the diagnostic surface.
"""

from __future__ import annotations

from typing import Any, Mapping, Sequence

from .model import (
    FAIL,
    PASS,
    STATUS_DONE,
    STATUS_FAILED,
    STATUS_PROTECTED,
    STATUS_WITHHELD,
    WITHHELD,
    Criterion,
    CriterionResult,
    Node,
    NodeResult,
    as_float,
    get_metric,
)


def _compare(value: float, op: str, threshold: float, tol: float) -> bool:
    if op == "le":
        return value <= threshold + tol
    if op == "lt":
        return value < threshold - tol
    if op == "ge":
        return value >= threshold - tol
    if op == "gt":
        return value > threshold + tol
    if op == "eq":
        return abs(value - threshold) <= tol
    if op == "abs_le":
        return abs(value) <= threshold + tol
    if op == "abs_lt":
        return abs(value) < threshold - tol
    raise ValueError(f"unknown op {op!r}")


def evaluate_criterion(c: Criterion, measurements: Mapping[str, Any]) -> CriterionResult:
    raw = get_metric(measurements, c.metric)
    value = as_float(raw)
    if value is None:
        return CriterionResult(
            name=c.name, metric=c.metric, op=c.op, threshold=c.threshold,
            required=c.required, value=None, state=WITHHELD,
            reason="metric missing or non-finite",
        )
    ok = _compare(value, c.op, c.threshold, c.tolerance)
    reason = "" if ok else f"{value!r} violates {c.op} {c.threshold!r}"
    return CriterionResult(
        name=c.name, metric=c.metric, op=c.op, threshold=c.threshold,
        required=c.required, value=value, state=PASS if ok else FAIL, reason=reason,
    )


def adjudicate(criteria: Sequence[CriterionResult]) -> tuple[str, list[str]]:
    """Aggregate criterion results into a verdict plus diagnostic flags."""
    flags: list[str] = []
    required = [c for c in criteria if c.required]
    if any(c.state == FAIL for c in required):
        verdict = FAIL
    elif any(c.state == WITHHELD for c in required):
        verdict = WITHHELD
        flags.append("insufficient_evidence")
    elif required:
        verdict = PASS
    else:
        # no required criteria at all: nothing can be adjudicated
        verdict = WITHHELD
        flags.append("no_required_criteria")
    if not required and criteria:
        flags.append("only_optional_criteria")
    return verdict, flags


def evaluate_node(
    node: Node,
    measurements: Mapping[str, Any],
    declaration: Mapping[str, Any] | None = None,
) -> NodeResult:
    """Run the acceptance gate for one node.

    ``declaration`` is what the executing worker claimed:
    ``{"honest_fail": bool, "note": str, "evidence": {...}}``.
    """
    decl = dict(declaration or {})
    declared = bool(decl.get("honest_fail", False))
    crit_results = tuple(evaluate_criterion(c, measurements) for c in node.criteria)
    verdict, flags = adjudicate(crit_results)

    if verdict == FAIL and declared:
        status = STATUS_PROTECTED
        flags.append("honest_fail_protected")
    elif verdict == FAIL:
        status = STATUS_FAILED
        if declared is False and decl.get("status") == "pass":
            flags.append("overclaim")
    elif verdict == PASS:
        status = STATUS_DONE
        if declared:
            flags.append("declaration_conflict")
    else:
        status = STATUS_WITHHELD

    evidence: dict[str, Any] = {}
    if decl:
        # the whole declaration is kept for audit (measurements excluded: they
        # are already adjudicated criterion by criterion)
        evidence["declaration"] = {k: v for k, v in decl.items() if k != "measurements"}
    if node.meta.get("evidence_refs"):
        evidence["refs"] = node.meta["evidence_refs"]

    return NodeResult(
        node_id=node.node_id,
        status=status,
        verdict=verdict,
        criteria=crit_results,
        declared_honest_fail=declared,
        flags=tuple(sorted(set(flags))),
        capability=node.capability,
        category=node.category,
        evidence=evidence,
    )


def failure_accounting(results: Sequence[NodeResult]) -> dict[str, Any]:
    """Failure statistics that never count honest FAIL as a failure (rule R4)."""
    executed = [r for r in results if r.status not in ("pending", "blocked")]
    failures = [r for r in executed if r.counted_as_failure]
    honest = [r for r in executed if r.negative_track]
    denom = len(executed) - len(honest)  # honest FAIL leaves the denominator
    rate = (len(failures) / denom) if denom else 0.0
    return {
        "executed": len(executed),
        "passed": sum(1 for r in executed if r.status == STATUS_DONE),
        "failed": len(failures),
        "honest_fail": len(honest),
        "withheld": sum(1 for r in executed if r.status == STATUS_WITHHELD),
        "denominator": denom,
        "failure_rate": round(rate, 10),
        "negative_track": [r.node_id for r in honest],
        "failed_nodes": [r.node_id for r in failures],
    }


def gate_report_markdown(title: str, results: Sequence[NodeResult]) -> str:
    lines = [f"## {title}", "", "| node | verdict | status | declared honest FAIL | flags |",
             "|---|---|---|---|---|"]
    for r in sorted(results, key=lambda x: x.node_id):
        lines.append(
            f"| {r.node_id} | {r.verdict} | {r.status} | "
            f"{'yes' if r.declared_honest_fail else 'no'} | {', '.join(r.flags) or '-'} |"
        )
    acc = failure_accounting(results)
    lines += [
        "",
        f"failure rate (honest FAIL excluded) = {acc['failed']}/{acc['denominator']} "
        f"= {acc['failure_rate']:.4f}",
    ]
    return "\n".join(lines)