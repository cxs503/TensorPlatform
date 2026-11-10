"""Evidence-chain completeness checker.

What it does
------------
It turns a pile of evidence records (test reports, benchmark results, review
records, defect notes) into a small set of **facts** — numbers and booleans that
a rule of :mod:`tools.capability_upgrade.upgrade_rules` can compare against —
and then answers, per rung of the ladder: *is this rung satisfied, and how far
from it are we?*

Two properties are deliberate:

**Independence is counted, not asserted.**  ``min_anchors`` counts *distinct*
``source.sha256`` values among admissible verification records, so re-reporting
the same number five times still counts as one anchor.  A record that itself
asserts ``MOCK`` / ``STUB`` / ``NOT_SUPPORTED``, or whose content address is
malformed, is not admissible at all — "evidence" that says it is unproven cannot
upgrade anything.

**No caller-supplied facts.**  There is no ``overrides`` escape hatch: every fact
is derived from the records, so a caller cannot *declare* three anchors.

**Missing is never passing.**  A fact that no record supplies is ``None``, and
``Requirement.check(None)`` is ``False``.  The checker therefore cannot report a
complete chain just because a threshold happened to be absent.

Fact derivations (all domain-neutral; no metric name is special-cased beyond the
three generic keys listed here, which arrive inside the evidence records):

===========================  ====================================================
``implementation_present``   any record with ``payload.present`` true or
                             ``metrics.present >= 1``
``smoke_test_passed``        any record whose ``kind`` is ``smoke_test`` and whose
                             ``payload.passed`` / ``metrics.passed`` is true
``input_response``           any record with ``payload.responsive`` true or
                             ``metrics.variation_ratio > 0``
``min_anchors``              distinct ``source.sha256`` values among admissible
                             records that carry a numeric ``error_pct``
``max_error_pct``            worst ``metrics.error_pct`` among admissible records
``independent_reviews``      passed reviews whose reviewer differs from the author
``open_defects``             defect records not closed/resolved
===========================  ====================================================

``min_anchors`` / ``max_error_pct`` / ``independent_reviews`` / ``open_defects``
keep their names for report readability; renaming them is a matter of naming the
requirement keys in the rules table, not a code change.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from .model import canonical_json, digest
from .upgrade_rules import Requirement, UpgradeRule, UpgradeRuleTable

#: levels whose own label disqualifies a record as supporting evidence
CONTRADICTION_LEVELS = ("MOCK", "STUB", "NOT_SUPPORTED")
CLOSED_DEFECT_STATUSES = ("closed", "resolved", "fixed", "withdrawn")
FACT_KEYS: Tuple[str, ...] = (
    "implementation_present",
    "smoke_test_passed",
    "input_response",
    "min_anchors",
    "max_error_pct",
    "independent_reviews",
    "open_defects",
)


def _is_sha256(value: Any) -> bool:
    return isinstance(value, str) and len(value) == 64 and all(
        c in "0123456789abcdef" for c in value.lower())


def _as_dict(record: Any) -> Dict[str, Any]:
    """Accept dicts or any object exposing ``as_dict()`` (e.g. EvidenceItem)."""
    if isinstance(record, Mapping):
        return dict(record)
    if hasattr(record, "as_dict"):
        return dict(record.as_dict())
    raise TypeError(f"evidence record of type {type(record)!r} is not a mapping")


def _flag(record: Mapping[str, Any], key: str) -> Optional[bool]:
    payload = record.get("payload") or {}
    metrics = record.get("metrics") or {}
    for source in (payload, metrics):
        if isinstance(source, Mapping) and key in source:
            return bool(source[key])
    return None


def _metric(record: Mapping[str, Any], key: str) -> Optional[float]:
    metrics = record.get("metrics") or {}
    if isinstance(metrics, Mapping) and metrics.get(key) is not None:
        try:
            return float(metrics[key])
        except (TypeError, ValueError):
            return None
    return None


@dataclass
class EvidenceBundle:
    """One capability's evidence at one instant (content-addressed)."""

    capability_id: str
    items: List[Dict[str, Any]] = field(default_factory=list)
    reviews: List[Dict[str, Any]] = field(default_factory=list)
    defects: List[Dict[str, Any]] = field(default_factory=list)
    captured_at: str = ""

    # -- construction ------------------------------------------------------
    @classmethod
    def from_evidence(cls, capability_id: str,
                      items: Iterable[Any] = (),
                      reviews: Iterable[Mapping[str, Any]] = (),
                      defects: Iterable[Mapping[str, Any]] = (),
                      captured_at: str = "") -> "EvidenceBundle":
        return cls(capability_id=str(capability_id),
                   items=[_as_dict(x) for x in items],
                   reviews=[dict(x) for x in reviews],
                   defects=[dict(x) for x in defects],
                   captured_at=captured_at)

    # -- admissibility -----------------------------------------------------
    @staticmethod
    def admissible(item: Mapping[str, Any]) -> bool:
        """A record may support a level only if it has a content address and does
        not itself assert a level below ``WITHHELD``."""
        sha = (item.get("source") or {}).get("sha256")
        if not _is_sha256(sha):
            return False
        level = str(item.get("observed_level") or "").upper()
        return level not in CONTRADICTION_LEVELS

    # -- facts -------------------------------------------------------------
    def facts(self) -> Dict[str, Any]:
        items = [i for i in self.items if self.admissible(i)]
        # an anchor is a record that ties a *measured value* to a reference, so a
        # record with no numeric error is not an anchor (the smoke-test record of
        # the implementation is not a verification of anything)
        measured = [i for i in items if _metric(i, "error_pct") is not None]
        anchors = {(i.get("source") or {}).get("sha256").lower() for i in measured}
        errors = [_metric(i, "error_pct") for i in measured]
        reviews = [
            r for r in self.reviews
            if bool(r.get("passed")) and str(r.get("reviewer") or "").strip()
            and str(r.get("reviewer")) != str(r.get("author") or "")
        ]
        # distinct reviewers only: one reviewer signing twice is one review
        reviewers = {str(r.get("reviewer")).strip() for r in reviews}
        open_defects = [d for d in self.defects
                        if str(d.get("status", "open")).lower() not in CLOSED_DEFECT_STATUSES]
        facts: Dict[str, Any] = {
            "implementation_present": any(_flag(i, "present") for i in self.items),
            "smoke_test_passed": any(
                str(i.get("kind", "")).lower() == "smoke_test" and _flag(i, "passed")
                for i in self.items),
            "input_response": any(
                _flag(i, "responsive") or (
                    (_metric(i, "variation_ratio") or 0.0) > 0.0)
                for i in self.items),
            "min_anchors": len(anchors),
            "max_error_pct": (max(errors) if errors else None),
            "independent_reviews": len(reviewers),
            "open_defects": len(open_defects),
        }
        return facts

    # -- identity ----------------------------------------------------------
    def snapshot(self) -> Dict[str, Any]:
        """Compact, JSON-safe evidence snapshot for the audit trail."""
        facts = self.facts()
        return {
            "capability_id": self.capability_id,
            "captured_at": self.captured_at,
            "n_items": len(self.items),
            "n_reviews": len(self.reviews),
            "n_defects": len(self.defects),
            "facts": facts,
            "item_digests": sorted(
                str(i.get("evidence_id") or i.get("digest") or digest(i)) for i in self.items
            ),
            "digest": self.digest,
        }

    @property
    def digest(self) -> str:
        return digest({
            "capability_id": self.capability_id,
            "items": [canonical_json(_as_dict(i)) for i in self.items],
            "reviews": [canonical_json(dict(r)) for r in self.reviews],
            "defects": [canonical_json(dict(d)) for d in self.defects],
        })


# ---------------------------------------------------------------------------
# assessment
# ---------------------------------------------------------------------------


@dataclass
class RequirementResult:
    key: str
    op: str
    threshold: Any
    observed: Any
    passed: bool
    required: bool
    label: str = ""

    def as_dict(self) -> Dict[str, Any]:
        return {"key": self.key, "op": self.op, "threshold": self.threshold,
                "observed": self.observed, "passed": self.passed,
                "required": self.required, "label": self.label}


@dataclass
class Assessment:
    """Outcome of checking one rule against one bundle."""

    rule_id: str
    from_level: str
    to_level: str
    satisfied: bool
    completeness: float
    requirements: List[RequirementResult] = field(default_factory=list)
    missing: List[str] = field(default_factory=list)

    def as_dict(self) -> Dict[str, Any]:
        return {
            "rule_id": self.rule_id,
            "from_level": self.from_level,
            "to_level": self.to_level,
            "satisfied": self.satisfied,
            "completeness": round(self.completeness, 6),
            "missing": list(self.missing),
            "requirements": [r.as_dict() for r in self.requirements],
        }


def assess(rule: UpgradeRule, bundle: EvidenceBundle) -> Assessment:
    """Check every requirement of ``rule`` against ``bundle`` and score it.

    ``completeness`` is the fraction of requirements met (optional requirements
    included, blocking ones flagged ``required``): it is a smooth quantity an
    operator can watch, while ``satisfied`` is the boolean the state machine acts
    on.  A blocking requirement that fails always makes ``satisfied`` false even
    if the ratio is high.
    """
    facts = bundle.facts()
    results: List[RequirementResult] = []
    missing: List[str] = []
    for req in rule.requirements:
        observed = facts.get(req.key)
        passed = req.check(observed)
        results.append(RequirementResult(key=req.key, op=req.op, threshold=req.value,
                                         observed=observed, passed=passed,
                                         required=req.required, label=req.label))
        if not passed and req.required:
            missing.append(req.key)
    total = len(results) or 1
    completeness = sum(1 for r in results if r.passed) / total
    satisfied = not missing and all(r.passed for r in results) if results else False
    return Assessment(rule_id=rule.rule_id, from_level=rule.from_level,
                      to_level=rule.to_level, satisfied=bool(satisfied),
                      completeness=completeness, requirements=results, missing=missing)


def chain_report(table: UpgradeRuleTable, bundle: EvidenceBundle) -> Dict[str, Any]:
    """Per-rung completeness of one capability's evidence chain.

    ``best_reachable`` is the highest rung whose *whole path* is satisfied — the
    level the state machine may move this capability to right now.
    """
    rungs: List[Dict[str, Any]] = []
    reached = table.ladder[0]
    climbing = True
    for rule in table.rules:
        a = assess(rule, bundle)
        rungs.append(a.as_dict())
        if climbing and a.satisfied:
            reached = rule.to_level
        else:
            climbing = False
    return {
        "capability_id": bundle.capability_id,
        "evidence_digest": bundle.digest,
        "facts": bundle.facts(),
        "rungs": rungs,
        "best_reachable": reached,
        "completeness_by_rung": {r["rule_id"]: r["completeness"] for r in rungs},
    }


def completeness_report(table: UpgradeRuleTable,
                        bundles: Iterable[EvidenceBundle]) -> Dict[str, Any]:
    """Scan a whole fleet of capabilities: the periodic completeness checker.

    Returns per-capability rung reports plus the aggregate numbers an audit needs
    (mean completeness per rung, how many capabilities sit at each reachable
    level, and how many rungs are blocking).
    """
    reports = [chain_report(table, b) for b in bundles]
    rung_ids = [r.rule_id for r in table.rules]
    mean_by_rung = {
        rid: (sum(rep["completeness_by_rung"].get(rid, 0.0) for rep in reports)
              / len(reports) if reports else 0.0)
        for rid in rung_ids
    }
    reachable_counts: Dict[str, int] = {}
    for rep in reports:
        reachable_counts[rep["best_reachable"]] = reachable_counts.get(
            rep["best_reachable"], 0) + 1
    return {
        "rules_digest": table.digest,
        "n_capabilities": len(reports),
        "mean_completeness_by_rung": {k: round(v, 6) for k, v in mean_by_rung.items()},
        "reachable_level_counts": reachable_counts,
        "capabilities": {rep["capability_id"]: rep for rep in reports},
    }


__all__ = [
    "CLOSED_DEFECT_STATUSES",
    "CONTRADICTION_LEVELS",
    "FACT_KEYS",
    "Assessment",
    "EvidenceBundle",
    "RequirementResult",
    "assess",
    "chain_report",
    "completeness_report",
]