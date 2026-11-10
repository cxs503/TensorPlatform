"""Deterministic data model for the unified evidence-governance pipeline.

Why a separate model (instead of reusing the debt-compiler dataclasses directly)?
------------------------------------------------------------------------------
``tools/evidence_debt_compiler.model`` carries wall-clock fields
(``extracted_at`` / ``classification_at`` / ``started_at``). Those are useful for
operations logs but they make artifacts non-reproducible: two runs over the same
inputs would differ. This node's acceptance criterion #4 is *sha256 identity of
two runs on identical input*, so the governance layer keeps its own records that
only contain input-derived values. The debt-compiler records are still used as an
implementation detail (extraction rules, classification machinery, task
templates, KPI invariant) — the merge is functional, not textual.

Zero domain semantics: every record here is built from the injected library
snapshot. No field name, unit or vocabulary term of any subject domain appears.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


def canonical_json(obj: Any) -> str:
    """Stable serialisation: sorted keys, no insignificant whitespace."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def digest(obj: Any) -> str:
    """Content address of an arbitrary JSON-able object."""
    return sha256_text(canonical_json(obj))


@dataclass
class LibraryItem:
    """One evidence-bearing capability row selected by the scanner.

    ``declaration_text`` is assembled only from fields declared in configuration;
    the governance code never interprets the words itself (the classifier rules
    are configuration as well).
    """

    capability_id: str
    source_id: str
    claimed_level: str
    reconciled_level: str
    evidence_refs: List[Dict[str, Any]] = field(default_factory=list)
    text_fields: Dict[str, str] = field(default_factory=dict)
    corpus_snippets: List[str] = field(default_factory=list)
    criticality: float = 0.5
    requires_evidence: bool = True

    @property
    def evidence_count(self) -> int:
        return len(self.evidence_refs)

    @property
    def independent_refs(self) -> int:
        """Number of distinct evidence sources (source id + asserted level)."""
        seen = set()
        for ref in self.evidence_refs:
            key = (
                str(ref.get("report_id", ref.get("source", ""))),
                str(ref.get("asserted_level", "")),
            )
            seen.add(key)
        return len(seen)

    @property
    def declaration_text(self) -> str:
        parts: List[str] = []
        for key in sorted(self.text_fields):
            value = self.text_fields[key]
            if value:
                parts.append(value)
        parts.extend(self.corpus_snippets)
        return " ".join(parts)

    def digest(self) -> str:
        return digest({
            "capability_id": self.capability_id,
            "source_id": self.source_id,
            "claimed_level": self.claimed_level,
            "reconciled_level": self.reconciled_level,
            "evidence_refs": self.evidence_refs,
            "text_fields": self.text_fields,
            "corpus_snippets": self.corpus_snippets,
            "criticality": self.criticality,
            "requires_evidence": self.requires_evidence,
        })

    def as_dict(self) -> Dict[str, Any]:
        return {
            "capability_id": self.capability_id,
            "source_id": self.source_id,
            "claimed_level": self.claimed_level,
            "reconciled_level": self.reconciled_level,
            "evidence_count": self.evidence_count,
            "independent_refs": self.independent_refs,
            "criticality": self.criticality,
            "requires_evidence": self.requires_evidence,
            "text_fields": dict(sorted(self.text_fields.items())),
            "corpus_snippets": list(self.corpus_snippets),
            "declaration_digest": digest(self.declaration_text),
            "item_digest": self.digest(),
        }


@dataclass
class VerificationProposal:
    """Structured "acquire this missing evidence" proposal for one item.

    Required by acceptance criterion #2: baseline type + missing magnitude +
    priority, plus the machine-readable verification spec it would demand.
    """

    capability_id: str
    baseline_type: str
    classification_confidence: float
    classification_rationale: str
    recommended_first_rung: str
    missing_magnitude: float
    missing_evidence_units: int
    magnitude_components: Dict[str, float]
    priority_score: float
    priority_band: str
    requires_evidence: bool
    required_evidence: Dict[str, Any]
    acceptance_criteria: Dict[str, Any]
    task_description: str

    def as_dict(self) -> Dict[str, Any]:
        return {
            "capability_id": self.capability_id,
            "baseline_type": self.baseline_type,
            "classification_confidence": round(self.classification_confidence, 4),
            "classification_rationale": self.classification_rationale,
            "recommended_first_rung": self.recommended_first_rung,
            "missing_magnitude": round(self.missing_magnitude, 4),
            "missing_evidence_units": self.missing_evidence_units,
            "magnitude_components": {
                k: round(v, 4) for k, v in sorted(self.magnitude_components.items())
            },
            "priority_score": round(self.priority_score, 4),
            "priority_band": self.priority_band,
            "requires_evidence": self.requires_evidence,
            "required_evidence": self.required_evidence,
            "acceptance_criteria": self.acceptance_criteria,
            "task_description": self.task_description,
        }

    def digest(self) -> str:
        return digest(self.as_dict())


@dataclass
class UpgradeRecord:
    """Outcome of attempting to close one WITHHELD entry."""

    capability_id: str
    before_level: str
    after_level: str
    outcome: str
    evidence_ref: Optional[str]
    reason: str

    def as_dict(self) -> Dict[str, Any]:
        return {
            "capability_id": self.capability_id,
            "before_level": self.before_level,
            "after_level": self.after_level,
            "outcome": self.outcome,
            "evidence_ref": self.evidence_ref,
            "reason": self.reason,
        }

    def digest(self) -> str:
        return digest(self.as_dict())


@dataclass
class GovernanceLedger:
    """Closure-rate KPI written to the ledger artifact.

    Invariant (checked at construction)::

        n_upgraded + n_failed + n_remaining == n_withheld_total
        closure_rate == n_upgraded / n_withheld_total
    """

    n_withheld_total: int
    n_upgraded: int
    n_failed: int
    n_remaining: int
    closure_rate: float
    run_digest: str
    updates: List[UpgradeRecord] = field(default_factory=list)
    orphan_outcomes: List[Dict[str, Any]] = field(default_factory=list)

    def validate(self) -> None:
        total = self.n_upgraded + self.n_failed + self.n_remaining
        if total != self.n_withheld_total:
            raise ValueError(
                "KPI invariant violated: "
                f"{self.n_upgraded}+{self.n_failed}+{self.n_remaining}={total} "
                f"!= {self.n_withheld_total}"
            )
        expected = (self.n_upgraded / self.n_withheld_total) if self.n_withheld_total else 0.0
        if abs(expected - self.closure_rate) > 1e-9:
            raise ValueError(
                f"closure_rate mismatch: declared {self.closure_rate} != {expected}"
            )

    def as_dict(self) -> Dict[str, Any]:
        self.validate()
        return {
            "n_withheld_total": self.n_withheld_total,
            "n_upgraded": self.n_upgraded,
            "n_failed": self.n_failed,
            "n_remaining": self.n_remaining,
            "closure_rate": round(self.closure_rate, 6),
            "run_digest": self.run_digest,
            "updates": [u.as_dict() for u in self.updates],
            "orphan_outcomes": list(self.orphan_outcomes),
        }

    def digest(self) -> str:
        return digest(self.as_dict())


@dataclass
class ScanResult:
    items: List[LibraryItem]
    source_stats: Dict[str, Any]

    def as_dict(self) -> Dict[str, Any]:
        return {
            "n_items": len(self.items),
            "source_stats": self.source_stats,
            "items": [i.as_dict() for i in self.items],
        }

    def digest(self) -> str:
        return digest(self.as_dict())