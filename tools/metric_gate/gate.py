"""Write-time metric gate: recompute on write, reject if mismatch.

The gate is a decorator/factory that wraps metric write endpoints.
When a metric row is submitted:
  1. If it carries a recompute_expr (or one is registered), evaluate it.
  2. Compare |value − recompute| against tolerance.
  3. If mismatch > tol → 422 reject with details.
  4. Otherwise accept and store.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Dict, Optional

from .expr_registry import ExprRegistry, SafeExprError

logger = logging.getLogger(__name__)


class GateRejection(Exception):
    """Raised when a metric write is rejected by the gate."""

    def __init__(self, metric_name: str, submitted: float, recomputed: float,
                 diff: float, tol: float, reason: str = ""):
        self.metric_name = metric_name
        self.submitted = submitted
        self.recomputed = recomputed
        self.diff = diff
        self.tol = tol
        self.reason = reason
        super().__init__(
            f"Gate rejection for {metric_name!r}: "
            f"submitted={submitted}, recomputed={recomputed}, "
            f"|diff|={diff:.6g} > tol={tol:.6g}"
            + (f" ({reason})" if reason else "")
        )


@dataclass
class GateResult:
    """Result of a gate check."""
    accepted: bool
    metric_name: str
    submitted_value: float
    recomputed_value: float | None
    diff: float | None
    tolerance: float
    evidence_count: int | None = None
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def as_dict(self) -> dict:
        return {
            "accepted": self.accepted,
            "metric_name": self.metric_name,
            "submitted_value": self.submitted_value,
            "recomputed_value": self.recomputed_value,
            "diff": self.diff,
            "tolerance": self.tolerance,
            "evidence_count": self.evidence_count,
            "timestamp": self.timestamp.isoformat(),
        }


@dataclass
class MetricGate:
    """Write-time metric recompute gate.

    Usage:
        registry = ExprRegistry()
        registry.register("repro_rate", "reproduced / total")
        gate = MetricGate(registry, default_tol=1e-6)

        # Check a write
        result = gate.check("repro_rate", value=0.95, row={"reproduced": 19, "total": 20})
        if not result.accepted:
            raise GateRejection(...)
    """

    registry: ExprRegistry
    default_tol: float = 1e-6
    per_metric_tol: Dict[str, float] = field(default_factory=dict)
    _rejection_log: list[GateResult] = field(default_factory=list)
    _acceptance_log: list[GateResult] = field(default_factory=list)

    def get_tol(self, metric_name: str) -> float:
        """Get tolerance for a metric (per-metric override or default)."""
        return self.per_metric_tol.get(metric_name, self.default_tol)

    def check(
        self,
        metric_name: str,
        value: float,
        row: Dict[str, Any],
        *,
        evidence_count: int | None = None,
        tol_override: float | None = None,
    ) -> GateResult:
        """Check a metric write against its recompute expression.

        Args:
            metric_name: name of the metric
            value: submitted value
            row: dict of fields for recompute (e.g., {"reproduced": 19, "total": 20})
            evidence_count: optional evidence count for sentinel
            tol_override: optional tolerance override

        Returns:
            GateResult with acceptance/rejection details

        Raises:
            GateRejection: if mismatch exceeds tolerance (when raise_on_reject=True)
        """
        tol = tol_override if tol_override is not None else self.get_tol(metric_name)

        # If no expression registered, pass through
        expr = self.registry.get_expression(metric_name)
        if expr is None:
            result = GateResult(
                accepted=True,
                metric_name=metric_name,
                submitted_value=float(value),
                recomputed_value=None,
                diff=None,
                tolerance=tol,
                evidence_count=evidence_count,
            )
            self._acceptance_log.append(result)
            return result

        # Evaluate recompute expression
        try:
            recomputed = self.registry.evaluate(metric_name, row)
        except (SafeExprError, KeyError) as e:
            logger.warning("Recompute failed for %s: %s", metric_name, e)
            # If recompute fails, reject (can't verify)
            result = GateResult(
                accepted=False,
                metric_name=metric_name,
                submitted_value=float(value),
                recomputed_value=None,
                diff=None,
                tolerance=tol,
                evidence_count=evidence_count,
            )
            self._rejection_log.append(result)
            return result

        diff = abs(float(value) - recomputed)
        accepted = diff <= tol

        result = GateResult(
            accepted=accepted,
            metric_name=metric_name,
            submitted_value=float(value),
            recomputed_value=recomputed,
            diff=diff,
            tolerance=tol,
            evidence_count=evidence_count,
        )

        if accepted:
            self._acceptance_log.append(result)
        else:
            self._rejection_log.append(result)

        return result

    def check_or_raise(
        self,
        metric_name: str,
        value: float,
        row: Dict[str, Any],
        **kwargs: Any,
    ) -> GateResult:
        """Check and raise GateRejection if mismatch."""
        result = self.check(metric_name, value, row, **kwargs)
        if not result.accepted:
            raise GateRejection(
                metric_name=metric_name,
                submitted=result.submitted_value,
                recomputed=result.recomputed_value or float("nan"),
                diff=result.diff or float("inf"),
                tol=result.tolerance,
                reason="recompute mismatch",
            )
        return result

    @property
    def rejection_log(self) -> list[GateResult]:
        return list(self._rejection_log)

    @property
    def acceptance_log(self) -> list[GateResult]:
        return list(self._acceptance_log)

    def clear_logs(self) -> None:
        self._rejection_log.clear()
        self._acceptance_log.clear()


def gate_decorator(gate: MetricGate):
    """Factory for a decorator that wraps a write function with the gate.

    Usage:
        @gate_decorator(gate)
        def write_metric(metric_name, value, row, **kwargs):
            ...  # actual write logic

    The decorated function will be called only if the gate accepts.
    """
    def decorator(fn: Callable) -> Callable:
        def wrapper(metric_name: str, value: float, row: Dict[str, Any], **kwargs):
            result = gate.check_or_raise(metric_name, value, row, **kwargs)
            return fn(metric_name, value, row, **kwargs)
        wrapper.__wrapped__ = fn
        return wrapper
    return decorator
