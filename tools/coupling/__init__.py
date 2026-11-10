"""Coupling infrastructure: conservative field exchange and explicit clock semantics.

Domain-neutral primitives shared by every multi-solver coupling on the platform:

* :mod:`tools.coupling.mesh`         —— box meshes and cell fields with a declared quantity kind
* :mod:`tools.coupling.remap`        —— overlap-L2 / RBF-conservative / nearest operators, one interface
* :mod:`tools.coupling.conservation` —— sum / moment / positivity / overshoot gates
* :mod:`tools.coupling.clock`        —— master-sub cycle contract with four exchange semantics, fail-closed
* :mod:`tools.coupling.twin`         —— synthetic two-solver loop for end-to-end consistency gating
* :mod:`tools.coupling.provenance`   —— canonical digests and a hash-chained exchange ledger
* :mod:`tools.coupling.precision`    —— closed-form moment projection, surface measure, vectors
* :mod:`tools.coupling.exchange_gate`—— the C1..C9 acceptance gate for exchange and clocks
* :mod:`tools.coupling.multiphysics` —— the five multi-physics coupling anchors
  (interface flux A1, global budget A2, iteration convergence A3, error
  propagation A4, decoupling negative control A5)

Domain meaning (what the field *is*) enters through configuration and artifacts at
the call site; nothing in this package knows it.
"""

from .clock import (
    ClockContract,
    ClockContractViolation,
    ClockMode,
    ExchangeSample,
    TimeSeriesExchange,
)
from .conservation import ConservationMetrics, GateResult, check, measure
from .mesh import BoxMesh, Field, MeshError, QuantityKind
from .multiphysics import (
    Block,
    CoupledRun,
    InterfaceCase,
    acceptance_record,
    budget_anchor,
    convergence_metrics,
    decoupling_anchor,
    interface_anchor,
    run_coupled,
    sensitivity_anchor,
)
from .provenance import ExchangeLedger, canonical_digest, to_canonical
from .precision import (
    QUANTITY_LAWS,
    DegenerateMeasureError,
    QuantityLaw,
    VectorField,
    area_overlap_matrix,
    cell_gradients,
    first_moment_tensor,
    law_for,
    moment_projection,
    rigid_motion_patch_test,
    torque,
)
from .remap import (
    RemapDomainError,
    RemapError,
    RemapOperator,
    RemapReport,
    RemapResult,
    RemapSpec,
    VectorRemapResult,
    bounded_mass_fix,
    overlap_matrix,
    remap,
    remap_vector,
)
from .twin import TwinSpec, diffusion_step, run_twin

__all__ = [
    "Block",
    "CoupledRun",
    "InterfaceCase",
    "acceptance_record",
    "budget_anchor",
    "convergence_metrics",
    "decoupling_anchor",
    "interface_anchor",
    "run_coupled",
    "sensitivity_anchor",
    "BoxMesh",
    "Field",
    "MeshError",
    "QuantityKind",
    "RemapOperator",
    "RemapSpec",
    "RemapReport",
    "RemapResult",
    "RemapError",
    "RemapDomainError",
    "remap",
    "overlap_matrix",
    "bounded_mass_fix",
    "ConservationMetrics",
    "GateResult",
    "measure",
    "check",
    "ClockContract",
    "ClockContractViolation",
    "ClockMode",
    "ExchangeSample",
    "TimeSeriesExchange",
    "TwinSpec",
    "run_twin",
    "diffusion_step",
    "ExchangeLedger",
    "canonical_digest",
    "to_canonical",
    "VectorField",
    "remap_vector",
    "VectorRemapResult",
    "QuantityLaw",
    "QUANTITY_LAWS",
    "law_for",
    "moment_projection",
    "first_moment_tensor",
    "torque",
    "cell_gradients",
    "area_overlap_matrix",
    "rigid_motion_patch_test",
    "DegenerateMeasureError",
]
