"""Classification of a WITHHELD declaration into the missing-baseline taxonomy.

This is the merge point with the debt-compiler: the rule compilation, the
``BaselineType`` enum and the ``ClassifiedDebt`` record all come from
``tools.evidence_debt_compiler``. What this layer adds is

* rules delivered as *configuration* (``config/classifier_rules.v1.json``) so the
  governance package contains no subject vocabulary at all, and
* an explicit, configurable tie-break order plus per-rule weight, so that a
  declaration carrying two signals is resolved by a declared precedence rather
  than by list order luck — which is what makes the measured accuracy meaningful
  instead of circular.

Scoring is deliberately simple and auditable::

    score(type) = weight(type) * |{patterns of type matching the declaration}|
    winner      = argmax score, ties broken by tie_break_order, 0 -> unknown

Overlapping patterns within one rule are collapsed to their most specific form
before scoring ("实测" and "实测数据" firing on one phrase is a single signal,
not two); without that collapse the per-rule hit count silently rewards verbose
rule lists instead of evidence content.
    confidence  = min(1, score / saturation)          # saturation from config
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

from tools.evidence_debt_compiler.classifier import BaselineClassifier
from tools.evidence_debt_compiler.model import (
    BaselineType,
    ClassifiedDebt,
    ExtractedDebt,
)

from .model import LibraryItem, sha256_text


def _most_specific(patterns: List[str]) -> List[str]:
    """Drop patterns that are a substring of another matched pattern."""
    kept: List[str] = []
    lowered = [p.lower() for p in patterns]
    for idx, pat in enumerate(patterns):
        nested = any(
            j != idx and lowered[idx] in lowered[j] and len(lowered[j]) > len(lowered[idx])
            for j in range(len(patterns))
        )
        if not nested:
            kept.append(pat)
    return kept


class GovernanceClassifier(BaselineClassifier):
    """Weighted, tie-break-ordered classifier (rules from configuration)."""

    def __init__(
        self,
        rules: List[Dict[str, Any]],
        tie_break_order: Optional[List[str]] = None,
        saturation: float = 3.0,
        default_type: str = BaselineType.UNKNOWN.value,
    ):
        super().__init__(rules=rules)
        self.saturation = float(saturation)
        self.default_type = BaselineType(default_type)
        self.rule_order = [str(r["baseline_type"]) for r in rules]
        self.tie_break = list(tie_break_order or self.rule_order)
        self._weights = {
            str(r["baseline_type"]): float(r.get("weight", 1.0)) for r in rules
        }

    # ---------------------------------------------------------------- helpers
    def explain(self, text: str) -> Dict[str, Any]:
        """Return per-type scores and the winning type for a declaration."""
        text = (text or "").lower()
        scores: Dict[str, float] = {}
        matched: Dict[str, List[str]] = {}
        for compiled, type_name in zip(self._compiled_rules, self.rule_order):
            hits = []
            for pattern in compiled["patterns"]:
                if pattern.search(text):
                    hits.append(pattern.pattern)
            # Overlapping patterns inside one rule must not double count: when
            # "实测" and "实测数据" both fire on the same phrase it is one
            # signal, not two. Keep the most specific (longest) forms only.
            hits = _most_specific(hits)
            if hits:
                scores[type_name] = self._weights.get(type_name, 1.0) * len(hits)
                matched[type_name] = hits
        if not scores:
            return {
                "baseline_type": self.default_type.value,
                "score": 0.0,
                "confidence": 0.0,
                "scores": {},
                "matched_patterns": {},
                "rationale": "no declared baseline signal; classified as unknown",
            }
        rank = {name: idx for idx, name in enumerate(self.tie_break)}
        winner = sorted(
            scores, key=lambda name: (-scores[name], rank.get(name, len(rank)), name)
        )[0]
        rationale = next(
            (r.get("rationale", "") for r in self.rules
             if str(r["baseline_type"]) == winner),
            "",
        )
        return {
            "baseline_type": winner,
            "score": scores[winner],
            "confidence": min(1.0, scores[winner] / self.saturation),
            "scores": dict(sorted(scores.items())),
            "matched_patterns": {k: sorted(v) for k, v in matched.items()},
            "rationale": rationale,
        }

    def classify_item(self, item: LibraryItem) -> Dict[str, Any]:
        result = self.explain(item.declaration_text)
        result["capability_id"] = item.capability_id
        return result

    # ------------------------------------------------------- debt-compiler API
    def classify(self, debt: ExtractedDebt) -> ClassifiedDebt:
        """Debt-compiler compatible entry point (reuses the weighted scorer)."""
        result = self.explain(debt.declaration_text)
        return ClassifiedDebt(
            debt_id=debt.debt_id,
            capability_id=debt.capability_id,
            baseline_type=BaselineType(result["baseline_type"]),
            confidence=result["confidence"],
            rationale=result["rationale"],
        )


def stable_debt_id(item: LibraryItem) -> str:
    """Deterministic id for an extracted debt (no clock, no counter)."""
    return "debt-" + sha256_text(
        f"{item.source_id}\x00{item.capability_id}\x00{item.declaration_text}"
    )[:16]


def as_extracted_debt(item: LibraryItem) -> ExtractedDebt:
    """Bridge a library row into the debt-compiler's extraction record."""
    return ExtractedDebt(
        debt_id=stable_debt_id(item),
        capability_id=item.capability_id,
        source_report_id=item.source_id,
        declaration_text=item.declaration_text,
        context={
            "claimed_level": item.claimed_level,
            "reconciled_level": item.reconciled_level,
            "evidence_count": item.evidence_count,
        },
    )