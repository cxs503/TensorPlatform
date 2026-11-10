"""Machine-checkable measurement of the guard itself.

A guard that fires on everything would trivially "detect" every regression, so
detection rate alone is worthless.  The campaign node therefore demands two
numbers together:

``detection_rate``      already selected tests / expected affected tests
                        (recall; must be 100 %)
``false_trigger_rate``  selected tests with no expected dependency
                        (1 - precision; must be < 1 %)

plus the negative-control and cache-reuse assertions.  ``ScenarioSuite``
evaluates a matrix of ``(change manifest, ground-truth expected test set)``
pairs, so the numbers are produced by code, not pasted into a report.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Sequence

from .model import ChangeManifest, sha256_obj


@dataclass
class Scenario:
    """One controlled change with its independently known blast radius."""

    name: str
    manifest: ChangeManifest
    expected_tests: Sequence[str] = ()
    expect_no_dependents: bool = False
    note: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "manifest": self.manifest.to_dict(),
            "expected_tests": sorted(self.expected_tests),
            "expect_no_dependents": self.expect_no_dependents,
            "note": self.note,
        }


@dataclass
class ScenarioResult:
    name: str
    selected: List[str]
    expected: List[str]
    missed: List[str]
    unjustified: List[str]
    verdict: str
    exit_code: int

    @property
    def detection_rate(self) -> float:
        return 1.0 if not self.expected else (len(set(self.expected) & set(self.selected)) / len(set(self.expected)))

    @property
    def false_trigger_rate(self) -> float:
        if not self.selected:
            return 0.0
        return len(set(self.selected) - set(self.expected)) / len(set(self.selected))

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "selected": sorted(self.selected),
            "expected": sorted(self.expected),
            "missed": sorted(self.missed),
            "unjustified": sorted(self.unjustified),
            "verdict": self.verdict,
            "exit_code": self.exit_code,
            "detection_rate": round(self.detection_rate, 6),
            "false_trigger_rate": round(self.false_trigger_rate, 6),
        }


class ScenarioSuite:
    """Runs the scenarios through a guard and aggregates the two metrics."""

    def __init__(self, guard, scenarios: Sequence[Scenario], execute: bool = False,
                 update_baseline: bool = False) -> None:
        self.guard = guard
        self.scenarios = list(scenarios)
        self.execute = execute
        self.update_baseline = update_baseline

    def run(self) -> Dict[str, Any]:
        results: List[ScenarioResult] = []
        for sc in self.scenarios:
            report = self.guard.run(
                sc.manifest,
                dry_run=not self.execute,
                use_cache=not self.update_baseline,
                update_baseline=self.update_baseline,
            )
            selected = report["selection"]["test_ids"]
            expected = [t for t in sc.expected_tests]
            results.append(
                ScenarioResult(
                    name=sc.name,
                    selected=selected,
                    expected=expected,
                    missed=sorted(set(expected) - set(selected)),
                    unjustified=report["selection"]["unjustified"],
                    verdict=report["verdict"]["status"],
                    exit_code=report["verdict"]["exit_code"],
                )
            )
        return self._aggregate(results)

    def _aggregate(self, results: Sequence[ScenarioResult]) -> Dict[str, Any]:
        with_expected = [r for r in results if r.expected]
        detection = (
            sum(r.detection_rate for r in with_expected) / len(with_expected) if with_expected else 1.0
        )
        total_selected = sum(len(r.selected) for r in results)
        total_unexpected = sum(len(set(r.selected) - set(r.expected)) for r in results)
        false_trigger = (total_unexpected / total_selected) if total_selected else 0.0
        negative_controls = [r for r in results if not r.expected]
        payload = {
            "schema_version": "1.0",
            "scenario_count": len(results),
            "detection_rate": round(detection, 6),
            "false_trigger_rate": round(false_trigger, 6),
            "selected_total": total_selected,
            "unexpected_selected_total": total_unexpected,
            "negative_controls": {
                "count": len(negative_controls),
                "selected_total": sum(len(r.selected) for r in negative_controls),
                "all_exit_zero": all(r.exit_code == 0 for r in negative_controls),
            },
            "scenarios": [r.to_dict() for r in results],
        }
        payload["suite_digest"] = sha256_obj({k: v for k, v in payload.items() if k != "suite_digest"})
        return payload


def evaluate_scenarios(analyzer, scenarios: Sequence[Scenario]) -> Dict[str, Any]:
    """Analysis-only measurement (no execution); used for large change matrices."""
    results: List[ScenarioResult] = []
    for sc in scenarios:
        impact = analyzer.analyze(sc.manifest)
        selected = impact.test_ids
        expected = list(sc.expected_tests)
        results.append(
            ScenarioResult(
                name=sc.name, selected=selected, expected=expected,
                missed=sorted(set(expected) - set(selected)),
                unjustified=[s.test_id for s in impact.selections if not s.reasons],
                verdict="planned", exit_code=0,
            )
        )
    suite = ScenarioSuite.__new__(ScenarioSuite)
    suite.scenarios = list(scenarios)
    suite.guard = None
    suite.execute = False
    suite.update_baseline = False
    return ScenarioSuite._aggregate(suite, results)  # type: ignore[arg-type]