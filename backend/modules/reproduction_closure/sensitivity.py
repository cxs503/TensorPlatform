"""Denominator Sensitivity Table — computes repro_rate under different denominator definitions.

Provides ≥5 denominator definitions to show how the choice of denominator
affects the reported repro_rate. This is critical for honest KPI reporting.
"""
from __future__ import annotations

from typing import Any

from .core import DenominatorDefinition, SensitivityEntry


class DenominatorSensitivityTable:
    """Computes repro_rate sensitivity to denominator definition."""

    DEFINITIONS = [
        (DenominatorDefinition.ALL_NODES, "All nodes regardless of status"),
        (DenominatorDefinition.DONE_NODES, "Only nodes with status='done'"),
        (DenominatorDefinition.HAS_TESTS, "Nodes with executable test files"),
        (DenominatorDefinition.HAS_ARTIFACTS, "Nodes with anchored artifacts"),
        (DenominatorDefinition.EXECUTABLE, "Nodes that can actually be re-run"),
        (DenominatorDefinition.EXCLUDE_BLOCKED, "Exclude blocked from denominator"),
        (DenominatorDefinition.STRICT_PASS, "Only count strict pass (no partial)"),
    ]

    def compute(self, node_results: list[dict[str, Any]]) -> list[SensitivityEntry]:
        """Compute repro_rate for each denominator definition."""
        entries = []

        for defn, desc in self.DEFINITIONS:
            entry = self._compute_one(defn, desc, node_results)
            entries.append(entry)

        return entries

    def _compute_one(self, definition: DenominatorDefinition,
                     description: str,
                     results: list[dict[str, Any]]) -> SensitivityEntry:
        """Compute one row of the sensitivity table."""
        filtered = self._filter_results(definition, results)

        total = len(filtered)
        if total == 0:
            return SensitivityEntry(
                definition=definition,
                description=description,
                denominator=0,
                numerator=0,
                repro_rate=0.0,
            )

        pass_count = sum(1 for r in filtered if r.get("verdict") == "pass")
        drift_count = sum(1 for r in filtered if r.get("verdict") == "drift")
        fail_count = sum(1 for r in filtered if r.get("verdict") == "fail")
        blocked_count = sum(1 for r in filtered if r.get("verdict") == "blocked")

        # Numerator = pass (and optionally partial, depending on definition)
        if definition == DenominatorDefinition.STRICT_PASS:
            numerator = pass_count
        else:
            # Include partial passes as half-credit
            partial_count = sum(1 for r in filtered if r.get("verdict") == "partial")
            numerator = pass_count + partial_count // 2

        repro_rate = round(numerator / total, 4) if total > 0 else 0.0

        return SensitivityEntry(
            definition=definition,
            description=description,
            denominator=total,
            numerator=numerator,
            repro_rate=repro_rate,
            blocked_count=blocked_count,
            drift_count=drift_count,
            fail_count=fail_count,
            pass_count=pass_count,
        )

    def _filter_results(self, definition: DenominatorDefinition,
                        results: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Filter results based on denominator definition."""
        if definition == DenominatorDefinition.ALL_NODES:
            return results

        if definition == DenominatorDefinition.DONE_NODES:
            return [r for r in results if r.get("status") == "done"]

        if definition == DenominatorDefinition.HAS_TESTS:
            return [r for r in results if r.get("has_tests", False)]

        if definition == DenominatorDefinition.HAS_ARTIFACTS:
            return [r for r in results if r.get("anchored_artifacts")
                    and len(r.get("anchored_artifacts", [])) > 0]

        if definition == DenominatorDefinition.EXECUTABLE:
            return [r for r in results
                    if r.get("verdict") != "blocked"
                    and r.get("has_tests", False)]

        if definition == DenominatorDefinition.EXCLUDE_BLOCKED:
            return [r for r in results if r.get("verdict") != "blocked"]

        if definition == DenominatorDefinition.STRICT_PASS:
            # Same as exclude_blocked but stricter numerator
            return [r for r in results if r.get("verdict") != "blocked"]

        return results

    def summary(self, entries: list[SensitivityEntry]) -> dict[str, Any]:
        """Generate a summary of the sensitivity analysis."""
        if not entries:
            return {"min_rate": 0.0, "max_rate": 0.0, "spread": 0.0}

        rates = [e.repro_rate for e in entries if e.denominator > 0]
        if not rates:
            return {"min_rate": 0.0, "max_rate": 0.0, "spread": 0.0}

        return {
            "min_rate": min(rates),
            "max_rate": max(rates),
            "spread": round(max(rates) - min(rates), 4),
            "mean_rate": round(sum(rates) / len(rates), 4),
            "definitions_count": len(entries),
        }
