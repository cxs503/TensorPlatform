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
* :mod:`tools.coupling.sensitivity`  —— system-sensitivity consistency gate:
  frozen per-discipline partials composed through the coupling response vs a
  re-solved derivative (a wrong interface Jacobian is caught, not averaged)
* :mod:`tools.coupling.loop_contract` —— ``coupled-loop.v1`` contract validation
  (fail-closed, coupling graph checked) and certificate/sensitivity verdicts
* :mod:`tools.coupling.maturity_check` —— one aggregated verdict for the
  coupled-loop maturity node, written as a JSON acceptance artefact
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
from .conservation_check import (
    ContractError,
    DiffusionInterfaceCase,
    OrderCertificate,
    SegmentMesh,
    contract_gate,
    diffusion_interface_case,
    load_contract,
    order_certificate,
    relative_residual,
    transfer,
    validate_contract,
)
from .loop_contract import (
    COUPLED_LOOP_SCHEMA_VERSION,
    LoopContractError,
    contract_gate as loop_contract_gate,
    gate_certificate,
    gate_sensitivity,
    load_coupled_loop_contract,
    validate_coupled_loop_contract,
)
from .maturity_check import run_maturity_audit
from .sensitivity import (
    CoupledSystem,
    DeclaredPartials,
    Discipline,
    LinearTwoDisciplineSystem,
    SensitivityError,
    compose_sensitivity,
    constant_partials,
    frozen_partials,
    linear_two_discipline_system,
    nonlinear_two_discipline_system,
    resolve_sensitivity,
    scale_declared_partials,
    sensitivity_consistency_gate,
)
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
    "ContractError",
    "DiffusionInterfaceCase",
    "OrderCertificate",
    "SegmentMesh",
    "contract_gate",
    "diffusion_interface_case",
    "load_contract",
    "order_certificate",
    "relative_residual",
    "transfer",
    "validate_contract",
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
    "CoupledSystem",
    "DeclaredPartials",
    "Discipline",
    "LinearTwoDisciplineSystem",
    "SensitivityError",
    "compose_sensitivity",
    "constant_partials",
    "frozen_partials",
    "linear_two_discipline_system",
    "nonlinear_two_discipline_system",
    "resolve_sensitivity",
    "scale_declared_partials",
    "sensitivity_consistency_gate",
    "COUPLED_LOOP_SCHEMA_VERSION",
    "LoopContractError",
    "loop_contract_gate",
    "gate_certificate",
    "gate_sensitivity",
    "load_coupled_loop_contract",
    "validate_coupled_loop_contract",
    "run_maturity_audit",
]
