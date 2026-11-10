"""Progress metering + stall detection for long-running jobs (domain-free).

Two questions a long run must be able to answer about itself:

1. *How much real progress did each unit of budget buy?*  -> :mod:`meter`
2. *Is it still making any?*  If not, what is the declared recovery action?
   -> :mod:`stall` + :mod:`strategies`

Public API::

    from tools.progress_meter import ProgressMeter, StallDetector, StrategyPlan, DEFAULT_PLAN

    meter = ProgressMeter(budget_total=100, ledger_path="run/progress.jsonl",
                          artifact_dir="run/artifacts")
    plan = StrategyPlan(list(DEFAULT_PLAN))
    det = StallDetector(meter, plan)
    ...
    if (action := det.observe()) is not None:
        handle(action.level)          # restate / reroute / shrink / honest_fail
"""

from .meter import KINDS, SCHEMA, Event, ProgressMeter, Weights  # noqa: F401
from .stall import DEFAULT_STALL_FRAC, LEVELS, StallDetector, StallEvent  # noqa: F401
from .strategies import DEFAULT_PLAN, Strategy, StrategyPlan  # noqa: F401

__all__ = [
    "KINDS",
    "SCHEMA",
    "Event",
    "ProgressMeter",
    "Weights",
    "StallDetector",
    "StallEvent",
    "LEVELS",
    "DEFAULT_STALL_FRAC",
    "Strategy",
    "StrategyPlan",
    "DEFAULT_PLAN",
]
__version__ = "1.0.0"