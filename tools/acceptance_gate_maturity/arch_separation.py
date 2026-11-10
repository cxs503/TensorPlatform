"""Cross-architecture baseline separation.

When tests run on multiple architectures (e.g. loong64, x86_64, aarch64),
each architecture may have legitimately different baseline outcomes due to
platform-specific numerical behaviour, library versions, or floating-point
semantics.

This module:
  1. Separates baselines by architecture key (ArchKey).
  2. Evaluates each architecture's measurements against its own baseline.
  3. Reports cross-architecture drift: criteria that pass on one arch but
     fail on another are reported as *drift*, not hidden.

Domain-neutral: architecture keys and criterion IDs are generic identifiers.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence

from .model import (
    ArchKey,
    BaselineSnapshot,
    CriterionOutcome,
    DiffResult,
    GateVerdict,
)
from .baseline_diff import diff_against_baseline


@dataclass(frozen=True)
class ArchResult:
    """Per-architecture acceptance gate result."""
    arch_key: ArchKey
    diff: DiffResult
    gate_verdict: GateVerdict


@dataclass(frozen=True)
class DriftItem:
    """One criterion that drifts across architectures."""
    criterion_id: str
    passing_arches: tuple[str, ...]
    failing_arches: tuple[str, ...]
    failure_type: str  # "new" or "pre_existing"


@dataclass(frozen=True)
class ArchDriftReport:
    """Cross-architecture drift report.

    Drift is reported separately — it is not hidden by aggregating across
    architectures.
    """
    per_arch: tuple[ArchResult, ...]
    drift_items: tuple[DriftItem, ...]
    overall_verdict: GateVerdict

    def to_dict(self) -> dict[str, Any]:
        return {
            "per_arch": [
                {
                    "arch_key": r.arch_key.to_key(),
                    "gate_verdict": r.gate_verdict.value,
                    "new_failures": list(r.diff.new_failures),
                    "pre_existing_failures": list(r.diff.pre_existing_failures),
                    "resolved": list(r.diff.resolved),
                    "missing_artifacts": list(r.diff.missing_artifacts),
                }
                for r in self.per_arch
            ],
            "drift_items": [
                {
                    "criterion_id": d.criterion_id,
                    "passing_arches": list(d.passing_arches),
                    "failing_arches": list(d.failing_arches),
                    "failure_type": d.failure_type,
                }
                for d in self.drift_items
            ],
            "overall_verdict": self.overall_verdict.value,
        }


def separate_by_arch(
    baselines: Mapping[str, BaselineSnapshot],
    measurements_by_arch: Mapping[str, Mapping[str, Any]],
) -> ArchDriftReport:
    """Evaluate each architecture against its own baseline and report drift.

    Parameters
    ----------
    baselines : mapping of arch_key_str → BaselineSnapshot
        One baseline per architecture.
    measurements_by_arch : mapping of arch_key_str → measurements
        Current measurements per architecture.

    Returns
    -------
    ArchDriftReport with per-arch results and cross-arch drift items.
    """
    per_arch_results: list[ArchResult] = []
    # Track per-criterion outcomes across architectures for drift detection
    criterion_arch_outcomes: dict[str, dict[str, CriterionOutcome]] = {}

    for arch_key_str, snapshot in baselines.items():
        if arch_key_str not in measurements_by_arch:
            # Missing measurements for this architecture
            continue
        measurements = measurements_by_arch[arch_key_str]
        diff = diff_against_baseline(snapshot, measurements)
        verdict = diff.gate_verdict()
        per_arch_results.append(ArchResult(
            arch_key=snapshot.arch_key,
            diff=diff,
            gate_verdict=verdict,
        ))

        # Collect per-criterion outcomes for drift analysis
        for cd in diff.per_criterion:
            if cd.criterion_id not in criterion_arch_outcomes:
                criterion_arch_outcomes[cd.criterion_id] = {}
            criterion_arch_outcomes[cd.criterion_id][arch_key_str] = cd.outcome

    # Detect drift: criteria with different outcomes across architectures
    drift_items: list[DriftItem] = []
    for criterion_id, arch_outcomes in sorted(criterion_arch_outcomes.items()):
        outcomes = set(arch_outcomes.values())
        if len(outcomes) <= 1:
            continue  # no drift

        passing = tuple(arch for arch, out in sorted(arch_outcomes.items())
                        if out == CriterionOutcome.PASS)
        failing_new = tuple(arch for arch, out in sorted(arch_outcomes.items())
                            if out == CriterionOutcome.FAIL_NEW)
        failing_pre = tuple(arch for arch, out in sorted(arch_outcomes.items())
                            if out == CriterionOutcome.FAIL_PRE_EXISTING)
        failing = failing_new + failing_pre

        # Determine failure type for the drift item
        if failing_new:
            failure_type = "new"
        elif failing_pre:
            failure_type = "pre_existing"
        else:
            failure_type = "mixed"

        drift_items.append(DriftItem(
            criterion_id=criterion_id,
            passing_arches=passing,
            failing_arches=failing,
            failure_type=failure_type,
        ))

    # Overall verdict: worst across all architectures
    verdicts = [r.gate_verdict for r in per_arch_results]
    if GateVerdict.MISSING_ARTIFACT in verdicts:
        overall = GateVerdict.MISSING_ARTIFACT
    elif GateVerdict.BASELINE_TAMPERED in verdicts:
        overall = GateVerdict.BASELINE_TAMPERED
    elif GateVerdict.FAIL_NEW in verdicts:
        overall = GateVerdict.FAIL_NEW
    elif GateVerdict.PASS_WITH_KNOWN in verdicts:
        overall = GateVerdict.PASS_WITH_KNOWN
    else:
        overall = GateVerdict.PASS

    return ArchDriftReport(
        per_arch=tuple(per_arch_results),
        drift_items=tuple(drift_items),
        overall_verdict=overall,
    )
