"""Baseline type classifier for evidence debts.

Classifies each WITHHELD declaration into one of the BaselineType categories:
  - analytical_solution: needs comparison against a known exact/closed-form solution
  - experimental: needs comparison against physical experiment data
  - mesh_convergence: needs grid/mesh refinement study
  - physical_consistency: needs conservation law / symmetry / dimensional check
  - cross_validation: needs comparison against another validated method

Zero-domain-semantics: classification rules are data-driven via injected
``rules`` configuration. Default rules use only structural/keyword patterns
that are domain-agnostic.
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Tuple

from .model import BaselineType, ClassifiedDebt, ExtractedDebt


# Default classification rules — structural keywords only, no domain vocabulary.
_DEFAULT_RULES: List[Dict[str, Any]] = [
    {
        "baseline_type": BaselineType.ANALYTICAL_SOLUTION,
        "patterns": [
            r"analytical",
            r"exact\s+solution",
            r"closed[\s-]?form",
            r"benchmark.*solution",
            r"reference.*solution",
            r"manufactured.*solution",
        ],
        "rationale": "declaration references analytical/exact solution comparison",
    },
    {
        "baseline_type": BaselineType.EXPERIMENTAL,
        "patterns": [
            r"experiment",
            r"measurement",
            r"test\s+data",
            r"physical\s+test",
            r"model\s+test",
            r"tank\s+test",
            r"sea\s+trial",
            r"empirical",
        ],
        "rationale": "declaration references experimental/measurement comparison",
    },
    {
        "baseline_type": BaselineType.MESH_CONVERGENCE,
        "patterns": [
            r"mesh\s+convergence",
            r"grid\s+convergence",
            r"refinement\s+study",
            r"mesh\s+independence",
            r"grid\s+independence",
            r"discretization\s+error",
            r"h[\s-]?refinement",
            r"p[\s-]?refinement",
        ],
        "rationale": "declaration references mesh/grid convergence study",
    },
    {
        "baseline_type": BaselineType.PHYSICAL_CONSISTENCY,
        "patterns": [
            r"conservation",
            r"symmetry",
            r"dimensional\s+check",
            r"physical\s+consistency",
            r"balance\s+check",
            r"sanity\s+check",
            r"limiting\s+case",
            r"asymptotic",
        ],
        "rationale": "declaration references physical consistency/conservation check",
    },
    {
        "baseline_type": BaselineType.CROSS_VALIDATION,
        "patterns": [
            r"cross[\s-]?validat",
            r"inter[\s-]?method",
            r"code[\s-]?to[\s-]?code",
            r"another\s+method",
            r"independent\s+verification",
            r"comparison.*method",
        ],
        "rationale": "declaration references cross-validation with another method",
    },
]


class BaselineClassifier:
    """Classify evidence debts into missing baseline types.

    Parameters
    ----------
    rules : list of dict, optional
        Classification rules. Each rule has ``baseline_type``, ``patterns``
        (list of regex), and ``rationale``. Defaults to structural rules.
    """

    def __init__(self, rules: Optional[List[Dict[str, Any]]] = None):
        self.rules = rules or _DEFAULT_RULES
        self._compiled_rules = []
        for rule in self.rules:
            compiled_patterns = [
                re.compile(p, re.IGNORECASE) for p in rule["patterns"]
            ]
            self._compiled_rules.append({
                "baseline_type": BaselineType(rule["baseline_type"])
                if isinstance(rule["baseline_type"], str)
                else rule["baseline_type"],
                "patterns": compiled_patterns,
                "rationale": rule.get("rationale", ""),
            })

    def classify(self, debt: ExtractedDebt) -> ClassifiedDebt:
        """Classify a single extracted debt."""
        text = debt.declaration_text.lower()
        best_type = BaselineType.UNKNOWN
        best_score = 0
        best_rationale = "no matching classification rule"

        for rule in self._compiled_rules:
            score = 0
            for pattern in rule["patterns"]:
                if pattern.search(text):
                    score += 1
            if score > best_score:
                best_score = score
                best_type = rule["baseline_type"]
                best_rationale = rule["rationale"]

        confidence = min(1.0, best_score / 3.0) if best_score > 0 else 0.0
        if best_type == BaselineType.UNKNOWN:
            confidence = 0.0

        return ClassifiedDebt(
            debt_id=debt.debt_id,
            capability_id=debt.capability_id,
            baseline_type=best_type,
            confidence=confidence,
            rationale=best_rationale,
        )

    def classify_batch(self, debts: List[ExtractedDebt]) -> List[ClassifiedDebt]:
        """Classify a batch of extracted debts."""
        return [self.classify(d) for d in debts]

    def classify_from_context(
        self,
        debt: ExtractedDebt,
        context_hints: Optional[Dict[str, Any]] = None,
    ) -> ClassifiedDebt:
        """Classify with additional context hints.

        Context hints can override text-based classification when the
        report provides structured metadata about the missing baseline.
        """
        if context_hints and "baseline_type" in context_hints:
            try:
                bt = BaselineType(context_hints["baseline_type"])
                return ClassifiedDebt(
                    debt_id=debt.debt_id,
                    capability_id=debt.capability_id,
                    baseline_type=bt,
                    confidence=context_hints.get("confidence", 1.0),
                    rationale=context_hints.get("rationale", "from structured hint"),
                )
            except ValueError:
                pass  # fall through to text-based classification

        return self.classify(debt)
