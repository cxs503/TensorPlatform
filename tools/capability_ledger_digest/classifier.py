"""Deterministic evidence-level classifier (REAL/MOCK/STUB/WITHHELD/NOT_SUPPORTED).

The classifier is a pure function of (evidence numbers, thresholds). It never
looks at file paths, capability names or free text, so the same spec always
yields the same levels and the same digest.

Decision table (first matching rule wins; the numbering is part of the audit
contract and appears in every ledger row as ``rule_id``)::

    R0  impl_present is false                          -> NOT_SUPPORTED
    R1  no test pass rate recorded                     -> STUB
    R2  mock_markers > 0                               -> MOCK
    R3  benchmark_error_pct >= max                     -> MOCK
    R4  test_pass_rate < min                           -> WITHHELD
    R5  independent review not pass (when required)    -> WITHHELD
    R6  all three items pass                           -> REAL

Rationale for the ordering: absence of an implementation outranks everything;
an implementation with no executable test evidence is a shell (STUB) rather than
an unproven-but-present algorithm; an explicit MOCK marker or an error beyond the
"unacceptable" line (15 %) contradicts a real-algorithm claim even if the other
items are green, so it outranks the weaker "insufficient evidence" verdict.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Sequence, Tuple

from .evidence_chain import ChainVerdict, build_chain
from .spec import (
    LEVEL_RANK,
    LEVELS,
    EvidenceRecord,
    SpecError,
    Thresholds,
    sha256_json,
)

RULES: Tuple[Tuple[str, str], ...] = (
    ("R0", "no implementation -> NOT_SUPPORTED"),
    ("R1", "no executable test evidence -> STUB"),
    ("R2", "mock marker present -> MOCK"),
    ("R3", "benchmark error at/over the unacceptable line -> MOCK"),
    ("R4", "test pass rate below threshold -> WITHHELD"),
    ("R5", "independent review not passed -> WITHHELD"),
    ("R6", "all three evidence items pass -> REAL"),
)

RULE_IDS: Tuple[str, ...] = tuple(rid for rid, _ in RULES)


@dataclass(frozen=True)
class Classification:
    capability_id: str
    level: str
    rule_id: str
    reason: str
    chain: ChainVerdict
    current_level: str
    transition: str
    expected_level: Optional[str]

    @property
    def label_match(self) -> Optional[bool]:
        if self.expected_level is None:
            return None
        return self.expected_level == self.level

    def to_dict(self) -> Dict[str, Any]:
        return {
            "capability_id": self.capability_id,
            "level": self.level,
            "rule_id": self.rule_id,
            "reason": self.reason,
            "chain": self.chain.to_dict(),
            "current_level": self.current_level,
            "transition": self.transition,
            "expected_level": self.expected_level,
            "label_match": self.label_match,
        }


def _transition(current: str, derived: str) -> str:
    """Rank-based transition; ``INIT`` is reserved for a not-yet-registered row."""
    if current not in LEVEL_RANK:
        raise SpecError(f"unknown current level: {current!r}")
    if derived not in LEVEL_RANK:
        raise SpecError(f"unknown derived level: {derived!r}")
    if LEVEL_RANK[derived] > LEVEL_RANK[current]:
        return "UPGRADE"
    if LEVEL_RANK[derived] < LEVEL_RANK[current]:
        return "DOWNGRADE"
    return "HOLD"


def classify(record: EvidenceRecord, thresholds: Thresholds) -> Classification:
    """Derive the evidence level of one capability."""
    record.validate()
    thr = thresholds.validate()
    chain = build_chain(record, thr)

    if not record.impl_present:
        level, rule, reason = (
            "NOT_SUPPORTED",
            "R0",
            "impl_present=false: no implementation evidence",
        )
    elif record.test_pass_rate is None:
        level, rule, reason = (
            "STUB",
            "R1",
            "no test pass rate recorded: implementation is a shell",
        )
    elif record.mock_markers > 0:
        level, rule, reason = (
            "MOCK",
            "R2",
            f"mock_markers={record.mock_markers}: hardcoded/random signal outranks numeric agreement",
        )
    elif not chain.detail("benchmark_error_pct").passed and record.benchmark_error_pct is not None:
        level, rule, reason = (
            "MOCK",
            "R3",
            "benchmark_error_pct="
            f"{record.benchmark_error_pct!r} >= max {thr.benchmark_error_max_pct!r}",
        )
    elif not chain.detail("test_pass_rate").passed:
        level, rule, reason = (
            "WITHHELD",
            "R4",
            f"test_pass_rate={record.test_pass_rate!r} < min {thr.test_pass_rate_min!r}",
        )
    elif not chain.detail("independent_review").passed:
        level, rule, reason = (
            "WITHHELD",
            "R5",
            f"independent_review={record.review_verdict!r} != pass",
        )
    elif chain.chain_complete:
        level, rule, reason = (
            "REAL",
            "R6",
            "all three evidence items pass",
        )
    else:  # pragma: no cover - defensive: R6 is the complement of R1..R5
        level, rule, reason = (
            "WITHHELD",
            "R5",
            "evidence chain incomplete for an unlisted reason",
        )

    return Classification(
        capability_id=record.capability_id,
        level=level,
        rule_id=rule,
        reason=reason,
        chain=chain,
        current_level=record.current_level,
        transition=_transition(record.current_level, level),
        expected_level=record.expected_level,
    )


def classify_all(
    records: Sequence[EvidenceRecord], thresholds: Thresholds
) -> List[Classification]:
    results = [classify(r, thresholds) for r in records]
    return sorted(results, key=lambda c: c.capability_id)


def level_counts(results: Sequence[Classification]) -> Dict[str, int]:
    counts = {lvl: 0 for lvl in LEVELS}
    for res in results:
        counts[res.level] += 1
    return counts


def known_label_score(results: Sequence[Classification]) -> Dict[str, Any]:
    """Accuracy of the classifier against the labels declared in the spec."""
    judged = [r for r in results if r.expected_level is not None]
    matched = [r for r in judged if r.label_match]
    mismatches = [
        {
            "capability_id": r.capability_id,
            "expected": r.expected_level,
            "derived": r.level,
            "rule_id": r.rule_id,
        }
        for r in judged
        if not r.label_match
    ]
    return {
        "judged": len(judged),
        "matched": len(matched),
        "accuracy": (len(matched) / len(judged)) if judged else None,
        "mismatches": mismatches,
        "per_level": {
            lvl: {
                "judged": sum(1 for r in judged if r.expected_level == lvl),
                "matched": sum(
                    1 for r in judged if r.expected_level == lvl and r.label_match
                ),
            }
            for lvl in LEVELS
        },
    }


def results_digest(results: Sequence[Classification]) -> str:
    return sha256_json([r.to_dict() for r in sorted(results, key=lambda c: c.capability_id)])