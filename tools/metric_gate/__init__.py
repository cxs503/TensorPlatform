"""Platform metric gate: write-time recompute + silent regression sentinel.

Zero domain semantics: all metric names/expressions are injected via config,
never hardcoded. Tests use synthetic domain names.
"""

from .expr_registry import ExprRegistry, SafeExprError
from .gate import MetricGate, GateRejection
from .sentinel import RegressionSentinel, SuspectAlert, MetricSnapshot
from .models import MetricSnapshotRow, MetricSnapshotStore

__all__ = [
    "ExprRegistry",
    "SafeExprError",
    "MetricGate",
    "GateRejection",
    "RegressionSentinel",
    "SuspectAlert",
    "MetricSnapshot",
    "MetricSnapshotRow",
    "MetricSnapshotStore",
]
