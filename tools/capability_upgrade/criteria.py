"""Generic acceptance-criterion evaluation engine (P218-N01).

A criterion is **data**.  Its grammar is deliberately small and domain-free:

.. code-block:: json

    {
      "evidence_kinds": ["numeric_verification"],   // optional whitelist
      "min_matching": 2,                            // required # of matching items
      "min_sources": 2,                             // required # distinct source paths
      "require_source_sha256": true,                // provenance completeness gate
      "forbid_observed_levels": ["MOCK", "STUB"],   // contradiction gate
      "metrics": [
        {"name": "error_pct", "op": "<=", "value": 3.0, "agg": "max"},
        {"name": "n_cases",   "op": ">=", "value": 3,  "agg": "min"}
      ]
    }

Semantics
---------
* ``agg="all"``  — every candidate value must satisfy the comparison (default)
* ``agg="any"``  — at least one candidate value must satisfy it
* ``agg="max"`` / ``"min"`` / ``"mean"`` — reduce first, then compare
* A metric that is missing from an evidence item makes that item *non-matching*
  for an ``all``/``any`` check; for reducer aggregations it is simply ignored
  (and the criterion fails if no item carries the metric at all).

Every check produces a machine-readable record (``checks``) so that a failed
upgrade is explainable rather than silent: this is the anti-"silent pass"
discipline — no evidence ⇒ no pass, and no evidence ⇒ the slot stays WITHHELD.
"""
from __future__ import annotations

import operator
from typing import Any, Dict, Iterable, List, Optional, Sequence

from .model import LEVEL_RANK, Criterion, CriterionVerdict, EvidenceItem, level_rank

# Levels that assert a concrete implementation reality incompatible with a REAL
# grade (as opposed to "not established", i.e. WITHHELD).
CONTRADICTION_LEVELS = {"MOCK", "STUB", "NOT_SUPPORTED"}

OPS = {
    "<=": operator.le,
    ">=": operator.ge,
    "<": operator.lt,
    ">": operator.gt,
    "==": operator.eq,
    "!=": operator.ne,
}


def _numeric(value: Any) -> Optional[float]:
    try:
        if isinstance(value, bool):
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _source_key(ev: EvidenceItem) -> Optional[str]:
    src = ev.source or {}
    for key in ("path", "locator", "url", "report_id"):
        val = src.get(key)
        if val not in (None, ""):
            return f"{key}:{val}"
    return None


def _match_kinds(evidence: Sequence[EvidenceItem], spec: Dict[str, Any]) -> List[EvidenceItem]:
    kinds = spec.get("evidence_kinds")
    if not kinds:
        return list(evidence)
    allowed = {str(k) for k in kinds}
    return [ev for ev in evidence if str(ev.kind) in allowed]


def _check_metric(
    items: Sequence[EvidenceItem], check: Dict[str, Any]
) -> Dict[str, Any]:
    name = str(check["name"])
    op_name = str(check.get("op", "<="))
    agg = str(check.get("agg", "all"))
    threshold = check.get("value")
    op = OPS.get(op_name)
    record: Dict[str, Any] = {
        "check": f"metric:{name}",
        "op": op_name,
        "value": threshold,
        "agg": agg,
        "ok": False,
        "observed": None,
    }
    if op is None:
        record["error"] = f"unknown operator {op_name!r}"
        return record

    pairs = []
    for ev in items:
        val = _numeric((ev.metrics or {}).get(name))
        if val is not None:
            pairs.append((ev, val))
    if not pairs:
        record["error"] = "no candidate carries this metric"
        return record

    if agg == "all":
        failing = [ev.evidence_id for ev, val in pairs if not op(val, threshold)]
        record["observed"] = [val for _, val in pairs]
        record["ok"] = not failing
        if failing:
            record["error"] = "some values violate the comparison"
            record["violating_evidence_ids"] = failing
        return record

    if agg == "any":
        passing = [ev.evidence_id for ev, val in pairs if op(val, threshold)]
        record["observed"] = [val for _, val in pairs]
        record["ok"] = bool(passing)
        record["satisfying_evidence_ids"] = passing
        return record

    values = [val for _, val in pairs]
    if agg == "max":
        reduced = max(values)
    elif agg == "min":
        reduced = min(values)
    elif agg == "mean":
        reduced = sum(values) / len(values)
    else:
        record["error"] = f"unknown aggregation {agg!r}"
        return record

    record["observed"] = reduced
    record["ok"] = bool(op(reduced, threshold))
    if not record["ok"]:
        record["error"] = "aggregated value violates the comparison"
    return record


def evaluate(
    criterion: Criterion, evidence: Iterable[EvidenceItem]
) -> CriterionVerdict:
    """Evaluate ``criterion`` against every evidence item of its capability."""
    spec = criterion.spec or {}
    evidence = list(evidence)

    # Gate 0: provenance completeness — items lacking a source sha256 are not
    # admissible as evidence at all when the criterion demands it.
    require_sha = bool(spec.get("require_source_sha256", False))
    candidates = list(evidence)
    excluded: List[Dict[str, Any]] = []
    if require_sha:
        kept = []
        for ev in candidates:
            sha = str((ev.source or {}).get("sha256") or "")
            if len(sha) == 64:
                kept.append(ev)
            else:
                excluded.append(
                    {"evidence_id": ev.evidence_id, "reason": "source.sha256 missing/short"}
                )
        candidates = kept

    # Gate: contradiction.  Structural part (always on): an evidence item that
    # *asserts* a level ranked below the criterion's target level cannot be used
    # to justify that target — a MOCK label never upgrades a slot to REAL, and a
    # WITHHELD label never counts as proof.  Evidence with no asserted level
    # (measured data only) is admitted.  The spec may add further forbidden
    # levels on top of the structural rule.
    target_rank = level_rank(criterion.target_level)
    forbid = {str(lv).upper() for lv in spec.get("forbid_observed_levels") or []}
    explicit_forbid = set(forbid)
    forbid |= {lv for lv, rk in LEVEL_RANK.items() if rk < target_rank}
    contradicting: List[Dict[str, Any]] = []
    below_target: List[Dict[str, Any]] = []
    if forbid:
        kept = []
        for ev in candidates:
            lvl = str(ev.observed_level).upper() if ev.observed_level else ""
            if lvl and lvl in forbid:
                # Two distinct kinds of exclusion, reported separately so an
                # audit can tell "the evidence contradicts the claim" from "the
                # evidence merely says nothing was established".
                if lvl in CONTRADICTION_LEVELS or lvl in explicit_forbid:
                    contradicting.append({"evidence_id": ev.evidence_id, "asserted": lvl})
                else:
                    below_target.append({"evidence_id": ev.evidence_id, "asserted": lvl})
            else:
                kept.append(ev)
        candidates = kept

    matched = _match_kinds(candidates, spec)

    checks: List[Dict[str, Any]] = []
    failures: List[Dict[str, Any]] = []

    if excluded:
        checks.append(
            {"check": "provenance_sha256", "ok": False, "excluded": excluded}
        )
    if contradicting:
        checks.append(
            {"check": "contradiction_gate", "ok": False, "contradicting": contradicting}
        )
    if below_target:
        checks.append(
            {"check": "below_target_assertion", "ok": False, "excluded": below_target}
        )

    min_matching = int(spec.get("min_matching", 1) or 1)
    count_ok = len(matched) >= min_matching
    checks.append(
        {
            "check": "min_matching",
            "ok": count_ok,
            "required": min_matching,
            "observed": len(matched),
        }
    )
    if not count_ok:
        failures.append(
            {
                "check": "min_matching",
                "required": min_matching,
                "observed": len(matched),
            }
        )

    min_sources = int(spec.get("min_sources", 0) or 0)
    if min_sources:
        keys = {_source_key(ev) for ev in matched}
        keys.discard(None)
        sources_ok = len(keys) >= min_sources
        checks.append(
            {"check": "min_sources", "ok": sources_ok, "required": min_sources, "observed": len(keys)}
        )
        if not sources_ok:
            failures.append(
                {"check": "min_sources", "required": min_sources, "observed": len(keys)}
            )

    for check in spec.get("metrics") or []:
        record = _check_metric(matched, check)
        checks.append(record)
        if not record.get("ok"):
            failures.append(record)

    if excluded:
        failures.append({"check": "provenance_sha256", "excluded": excluded})
    if contradicting:
        failures.append({"check": "contradiction_gate", "contradicting": contradicting})
    if below_target:
        failures.append({"check": "below_target_assertion", "excluded": below_target})

    return CriterionVerdict(
        criterion_id=criterion.criterion_id,
        passed=not failures,
        matched_evidence_ids=[ev.evidence_id for ev in matched],
        target_level=criterion.target_level,
        failures=failures,
        checks=checks,
    )