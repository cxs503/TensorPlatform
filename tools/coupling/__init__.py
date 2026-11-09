"""Coupling infrastructure: conservative field exchange and explicit clock semantics.

Domain-neutral primitives shared by every multi-solver coupling on the platform:

* :mod:`tools.coupling.mesh`         —— box meshes and cell fields with a declared quantity kind
* :mod:`tools.coupling.remap`        —— overlap-L2 / RBF-conservative / nearest operators, one interface
* :mod:`tools.coupling.conservation` —— sum / moment / positivity / overshoot gates
* :mod:`tools.coupling.clock`        —— master-sub cycle contract with four exchange semantics, fail-closed
* :mod:`tools.coupling.twin`         —— synthetic two-solver loop for end-to-end consistency gating
* :mod:`tools.coupling.provenance`   —— canonical digests and a hash-chained exchange ledger

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
from .provenance import ExchangeLedger, canonical_digest, to_canonical
from .remap import (
    RemapDomainError,
    RemapError,
    RemapOperator,
    RemapReport,
    RemapResult,
    RemapSpec,
    bounded_mass_fix,
    overlap_matrix,
    remap,
)
from .twin import TwinSpec, diffusion_step, run_twin

__all__ = [
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
]
