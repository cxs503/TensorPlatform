"""Silent regression sentinel: detect suspicious metric changes.

Monitors metric snapshots for patterns where:
  - Evidence count is unchanged (same sample size)
  - But value changes significantly (especially toward worse direction)

This catches "silent regressions" where someone changes a metric value
without adding new evidence — a hallmark of data manipulation.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


@dataclass
class SuspectAlert:
    """Alert for a suspicious metric change."""
    metric_name: str
    prev_value: float
    curr_value: float
    prev_evidence: int
    curr_evidence: int
    diff: float
    direction: str  # "improvement" or "regression"
    severity: str  # "low", "medium", "high", "critical"
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    reason: str = ""

    def as_dict(self) -> dict:
        return {
            "metric_name": self.metric_name,
            "prev_value": self.prev_value,
            "curr_value": self.curr_value,
            "prev_evidence": self.prev_evidence,
            "curr_evidence": self.curr_evidence,
            "diff": self.diff,
            "direction": self.direction,
            "severity": self.severity,
            "timestamp": self.timestamp.isoformat(),
            "reason": self.reason,
        }


@dataclass
class MetricSnapshot:
    """A single metric observation at a point in time."""
    metric_name: str
    value: float
    evidence_count: int
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    metadata: Dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict:
        return {
            "metric_name": self.metric_name,
            "value": self.value,
            "evidence_count": self.evidence_count,
            "timestamp": self.timestamp.isoformat(),
            "metadata": self.metadata,
        }


@dataclass
class RegressionSentinel:
    """Silent regression sentinel.

    Configuration:
        regression_tol: multiplier of base tolerance for regression detection
            (e.g., 5.0 means changes > 5× base_tol toward worse are flagged)
        base_tol: base tolerance for the metric (absolute)
        higher_is_better: if True, decreases are regressions; if False, increases are
        min_evidence: minimum evidence count to consider (avoid noise on tiny samples)
    """

    regression_tol: float = 5.0
    base_tol: float = 1e-6
    higher_is_better: bool = True
    min_evidence: int = 1
    per_metric_config: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    _alerts: List[SuspectAlert] = field(default_factory=list)

    def _get_config(self, metric_name: str) -> Dict[str, Any]:
        """Get per-metric config or defaults."""
        defaults = {
            "regression_tol": self.regression_tol,
            "base_tol": self.base_tol,
            "higher_is_better": self.higher_is_better,
            "min_evidence": self.min_evidence,
        }
        overrides = self.per_metric_config.get(metric_name, {})
        return {**defaults, **overrides}

    def check_transition(
        self,
        metric_name: str,
        prev: MetricSnapshot,
        curr: MetricSnapshot,
    ) -> Optional[SuspectAlert]:
        """Check a transition between two snapshots.

        Returns SuspectAlert if suspicious, None otherwise.

        Suspicious conditions:
          1. Evidence count unchanged (same sample size)
          2. Value changed by more than regression_tol × base_tol
          3. Change is in the "regression" direction (toward worse)
        """
        cfg = self._get_config(metric_name)
        threshold = cfg["regression_tol"] * cfg["base_tol"]
        higher_better = cfg["higher_is_better"]
        min_ev = cfg["min_evidence"]

        # Skip if evidence too low
        if curr.evidence_count < min_ev and prev.evidence_count < min_ev:
            return None

        # Check if evidence count changed
        evidence_changed = curr.evidence_count != prev.evidence_count

        # Compute value change
        diff = curr.value - prev.value
        abs_diff = abs(diff)

        # Determine direction
        if higher_better:
            is_regression = diff < 0  # value decreased
            direction = "regression" if is_regression else "improvement"
        else:
            is_regression = diff > 0  # value increased
            direction = "regression" if is_regression else "improvement"

        # Suspicious if: evidence unchanged AND large regression
        if not evidence_changed and abs_diff > threshold and is_regression:
            # Severity based on magnitude
            if abs_diff > 10 * threshold:
                severity = "critical"
            elif abs_diff > 5 * threshold:
                severity = "high"
            elif abs_diff > 2 * threshold:
                severity = "medium"
            else:
                severity = "low"

            alert = SuspectAlert(
                metric_name=metric_name,
                prev_value=prev.value,
                curr_value=curr.value,
                prev_evidence=prev.evidence_count,
                curr_evidence=curr.evidence_count,
                diff=diff,
                direction=direction,
                severity=severity,
                reason=(
                    f"Value changed by {abs_diff:.6g} (>{threshold:.6g}) "
                    f"without evidence count change "
                    f"({prev.evidence_count} → {curr.evidence_count})"
                ),
            )
            self._alerts.append(alert)
            return alert

        return None

    def check_sequence(
        self,
        metric_name: str,
        snapshots: List[MetricSnapshot],
    ) -> List[SuspectAlert]:
        """Check a sequence of snapshots for suspicious transitions.

        Returns list of all alerts found.
        """
        alerts = []
        for i in range(1, len(snapshots)):
            alert = self.check_transition(metric_name, snapshots[i - 1], snapshots[i])
            if alert:
                alerts.append(alert)
        return alerts

    @property
    def alerts(self) -> List[SuspectAlert]:
        return list(self._alerts)

    def clear_alerts(self) -> None:
        self._alerts.clear()

    def get_suspect_metrics(self) -> List[str]:
        """Get list of metric names with active alerts."""
        return list(set(a.metric_name for a in self._alerts))
