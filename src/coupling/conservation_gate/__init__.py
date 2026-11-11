"""Cross-library field-exchange conservation gate (fail-closed ingest).

Four primitives, zero domain semantics:

``ledger``    sealed two-sided ledger document (raw values, no residuals)
``gate``      fail-closed adjudication with specific reason codes + policy
``faces``     the three hand-offs (fracture, flow, clock) as ledgers
``exact``     ℚ-arithmetic re-evaluation proving the float residual is round-off
``controls``  positive + sixteen negative controls the gate must survive

Usage::

    from src.coupling.conservation_gate import (
        fem_dem_ledger, lbm_fvm_ledger, barrier_run, optimistic_run, clock_ledger,
        adjudicate, cross_library_policy, run_controls,
    )
    ledgers = [fem_dem_ledger(el, faces)[0], lbm_fvm_ledger(s, t, p, n)[0],
               clock_ledger(barrier, opt)[0]]
    verdict = adjudicate(ledgers)
    assert verdict.accepted

The gate recomputes every residual from the raw two-sided values and applies
its own tolerance, so a producer can neither pre-compute nor relax its way to
acceptance; a sealed document that is edited afterwards is rejected outright.
"""

from __future__ import annotations

from .controls import MUTATIONS, clone, run_controls, run_mutator_substitution
from .exact import exact_inertia, exact_moments, rational_ledger_residuals
from .faces import (
    VERSION,
    BarrierReport,
    OptimisticReport,
    barrier_run,
    clock_ledger,
    fem_dem_ledger,
    lbm_fvm_ledger,
    optimistic_run,
    sphere_surrogate_ledger,
    sphere_surrogate_tensors,
)
from .gate import (
    BALANCE,
    BOUND,
    FLAG,
    OPS,
    ConservationGate,
    Criterion,
    GatePolicy,
    GateVerdict,
    adjudicate,
    cross_library_policy,
    structural_failures,
)
from .ledger import (
    KINDS,
    SCHEMA,
    ConservationLedger,
    LedgerEntry,
    bundle_digest,
    canonical_json,
    rel_residual,
    seal_all,
    sha256_of,
)

__all__ = [
    "SCHEMA", "KINDS", "VERSION",
    "ConservationLedger", "LedgerEntry", "rel_residual", "seal_all",
    "bundle_digest", "canonical_json", "sha256_of",
    "ConservationGate", "GatePolicy", "GateVerdict", "Criterion",
    "adjudicate", "cross_library_policy", "structural_failures",
    "BALANCE", "BOUND", "FLAG", "OPS",
    "fem_dem_ledger", "lbm_fvm_ledger", "clock_ledger",
    "sphere_surrogate_ledger", "sphere_surrogate_tensors", "barrier_run", "optimistic_run",
    "BarrierReport", "OptimisticReport",
    "rational_ledger_residuals", "exact_moments", "exact_inertia",
    "run_controls", "run_mutator_substitution", "clone", "MUTATIONS",
]
