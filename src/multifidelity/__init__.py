"""Cross-solver multi-fidelity infrastructure (platform-common, domain-neutral).

Two cooperating capabilities live here, both callable by any solver repository:

``clock_manager``
    :class:`GlobalClockManager` — registers the natural time step of every
    participant of a co-simulation and derives an *exact rational* global step
    (``dt_global = gcd(dt_i)`` or ``lcm(dt_i)``), the resulting sync grid, and
    the per-solver sub-stepping plan.  Nothing here knows what a solver solves.

``exchange``
    Conservative boundary exchange on that grid: transported quantities live on
    named channels, the producer's account and the consumer's account are kept
    as two independent ledgers, and the *defect* between them (plus the error
    against the analytic integral) is what a conservation gate consumes.

``distill``
    Teacher→student distillation for the high-fidelity → low-fidelity transfer:
    soft labels over a discretised output axis, ``L = alpha*L_task + (1-alpha)*L_KD``
    with ``L_KD = KL(p_teacher || p_student)``, an accuracy/efficiency Pareto
    sweep and a cost model measured by wall clock.

The package imports only the standard library and numpy: it is a dependency of
solver loops, not a framework wrapper.
"""

from __future__ import annotations

from .clock_manager import (
    ClockContractViolation,
    GlobalClockManager,
    SolverClock,
    Strategy,
    SyncGrid,
    rational_gcd,
    rational_lcm,
)
from .exchange import (
    Channel,
    ChannelLedger,
    exchange_free_running,
    exchange_synced,
    interpolation_audit,
)
from .distill import (
    DistillConfig,
    DistillResult,
    EmpiricalFormulaStudent,
    GridOperator,
    HighFidelityTeacher,
    StudentSurrogate,
    pareto_sweep,
)

__all__ = [
    "ClockContractViolation",
    "GlobalClockManager",
    "SolverClock",
    "Strategy",
    "SyncGrid",
    "rational_gcd",
    "rational_lcm",
    "Channel",
    "ChannelLedger",
    "exchange_free_running",
    "exchange_synced",
    "interpolation_audit",
    "DistillConfig",
    "DistillResult",
    "EmpiricalFormulaStudent",
    "GridOperator",
    "HighFidelityTeacher",
    "StudentSurrogate",
    "pareto_sweep",
]