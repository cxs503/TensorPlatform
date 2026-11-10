"""Baseline differencing engine.

Compares current criterion outcomes against a baseline snapshot and classifies
each criterion as:
  - PASS (still passing)
  - FAIL_NEW (regression: was passing in baseline, now failing)
  - FAIL_PRE_EXISTING (was already failing in baseline)
  - MISSING (artifact/metric absent → fail-closed)
  - resolved (was failing in baseline, now passing)

Domain-neutral: all criterion identifiers and metric paths come from the
baseline snapshot configuration, not from code.
"""
from __future__ import annotations

import math
from typing import Any, Mapping, Sequence

from .model import (
    BaselineRecord,
    BaselineSnapshot,
    CriterionDiff,
    CriterionOutcome,
    DiffResult,
)


def _evaluate_criterion(record: BaselineRecord,
                        measurements: Mapping[str, Any]) -> tuple[CriterionOutcome, Any]:
    """Evaluate one criterion against current measurements.

    Returns (outcome, measured_value).  If the metric path is missing or
    non-finite, returns (MISSING, None).
    """
    value = _resolve_metric(measurements, record.metric_path)
    if value is None:
        return CriterionOutcome.MISSING, None

    ok = _compare(value, record.op, record.threshold)
    return (CriterionOutcome.PASS if ok else CriterionOutcome.FAIL_NEW), value


def _resolve_metric(measurements: Mapping[str, Any], path: str) -> Any:
    """Resolve a dotted metric path into the measurements mapping.

    Returns None if any segment is missing.
    """
    if not path:
        return None
    current: Any = measurements
    for segment in path.split("."):
        if isinstance(current, Mapping) and segment in current:
            current = current[segment]
        else:
            return None
    return current


def _compare(value: Any, op: str, threshold: float) -> bool:
    """Compare a measured value against a threshold with the given operator."""
    try:
        v = float(value)
    except (TypeError, ValueError):
        return False
    if math.isnan(v) or math.isinf(v):
        return False
    if op == "le":
        return v <= threshold
    if op == "lt":
        return v < threshold
    if op == "ge":
        return v >= threshold
    if op == "gt":
        return v > threshold
    if op == "eq":
        return abs(v - threshold) < 1e-9
    if op == "abs_le":
        return abs(v) <= threshold
    if op == "abs_lt":
        return abs(v) < threshold
    raise ValueError(f"unknown op {op!r}")


def classify_failures(snapshot: BaselineSnapshot,
                      measurements: Mapping[str, Any]) -> tuple[CriterionDiff, ...]:
    """Classify each baseline criterion against current measurements.

    Returns a tuple of CriterionDiff, one per baseline record.
    """
    results = []
    for record in snapshot.records:
        outcome, value = _evaluate_criterion(record, measurements)

        if outcome == CriterionOutcome.MISSING:
            diff = CriterionDiff(
                criterion_id=record.criterion_id,
                outcome=CriterionOutcome.MISSING,
                measured_value=None,
                baseline_expected=record.expected_outcome,
                detail="artifact or metric absent",
            )
        elif record.expected_outcome == CriterionOutcome.FAIL_PRE_EXISTING:
            # Baseline says this was already failing
            if outcome == CriterionOutcome.FAIL_NEW:
                # Still failing → pre-existing (not a regression)
                diff = CriterionDiff(
                    criterion_id=record.criterion_id,
                    outcome=CriterionOutcome.FAIL_PRE_EXISTING,
                    measured_value=value,
                    baseline_expected=record.expected_outcome,
                    detail="pre-existing failure persists",
                )
            else:
                # Was failing, now passing → resolved
                diff = CriterionDiff(
                    criterion_id=record.criterion_id,
                    outcome=CriterionOutcome.PASS,
                    measured_value=value,
                    baseline_expected=record.expected_outcome,
                    detail="pre-existing failure resolved",
                )
        else:
            # Baseline says this should be passing
            if outcome == CriterionOutcome.PASS:
                diff = CriterionDiff(
                    criterion_id=record.criterion_id,
                    outcome=CriterionOutcome.PASS,
                    measured_value=value,
                    baseline_expected=record.expected_outcome,
                    detail="still passing",
                )
            else:
                # Was passing, now failing → regression!
                diff = CriterionDiff(
                    criterion_id=record.criterion_id,
                    outcome=CriterionOutcome.FAIL_NEW,
                    measured_value=value,
                    baseline_expected=record.expected_outcome,
                    detail=f"regression: {value!r} violates {record.op} {record.threshold}",
                )
        results.append(diff)
    return tuple(results)


def diff_against_baseline(snapshot: BaselineSnapshot,
                          measurements: Mapping[str, Any]) -> DiffResult:
    """Main entry point: diff current measurements against baseline.

    Returns a DiffResult with classified failures and gate verdict.
    """
    diffs = classify_failures(snapshot, measurements)

    new_failures = tuple(d.criterion_id for d in diffs
                         if d.outcome == CriterionOutcome.FAIL_NEW)
    pre_existing = tuple(d.criterion_id for d in diffs
                         if d.outcome == CriterionOutcome.FAIL_PRE_EXISTING)
    still_passing = tuple(d.criterion_id for d in diffs
                          if d.outcome == CriterionOutcome.PASS
                          and d.baseline_expected == CriterionOutcome.PASS)
    resolved = tuple(d.criterion_id for d in diffs
                     if d.outcome == CriterionOutcome.PASS
                     and d.baseline_expected == CriterionOutcome.FAIL_PRE_EXISTING)
    missing = tuple(d.criterion_id for d in diffs
                    if d.outcome == CriterionOutcome.MISSING)

    return DiffResult(
        new_failures=new_failures,
        pre_existing_failures=pre_existing,
        resolved=resolved,
        still_passing=still_passing,
        missing_artifacts=missing,
        per_criterion=diffs,
    )
