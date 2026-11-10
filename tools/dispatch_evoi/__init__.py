"""Offline, leakage-audited evaluation of an EVOI dispatch ordering.

Public surface
--------------
``schema``    dispatch-time field registry + timestamp leakage guard
``features``  leakage-safe design matrix (intake columns + history aggregates)
``fixtures``  synthetic dispatch log with known ground truth
``models``    ridge logistic (A) and shallow GBDT (B), plain numpy
``cost``      log-linear compute-cost regression on historical wall times
``offpolicy`` IPS / SNIPS / ESS for the deterministic EVOI policy
``backtest``  walk-forward evaluation and the mission's adoption gates
``leakage``   noise-substitution and label-shuffle audits
``report``    JSON / markdown / figures
"""

from __future__ import annotations

__all__ = ["run_evaluation", "evaluate_gates", "check_reproducibility", "build_report"]

from .backtest import check_reproducibility, evaluate_gates, run_evaluation  # noqa: E402


def build_report(**kwargs):  # pragma: no cover - thin re-export
    from .report import build_report as _build

    return _build(**kwargs)
