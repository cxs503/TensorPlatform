"""Cross-solver conservative field exchange & clock infrastructure.

Pure platform mechanism (zero domain semantics):

* :mod:`backend.coupling.mesh`     -- cell-centered unstructured meshes
* :mod:`backend.coupling.remap`    -- L2 projection / RBF remap operators
* :mod:`backend.coupling.clock`    -- explicit subcycle clock contract
* :mod:`backend.coupling.interface`-- failure_faces/v1 schema + ledgers
* :mod:`backend.coupling.gate`     -- twin consistency (conservation) gate
* :mod:`backend.coupling.temporal_interp`  -- temporal interpolation operators
* :mod:`backend.coupling.moment_remap`     -- moment-preserving remap (Lagrange)
* :mod:`backend.coupling.spacetime_exchange` -- composed space-time exchange
"""
from .mesh import Mesh, MeshError, structured_mesh_2d, structured_mesh_3d
from .remap import L2Remap, RBFRemap, RemapError, RemapResult
from .clock import ClockContract, ClockContractError, SubcycleScheduler
from .interface import (
    SCHEMA_VERSION,
    FragmentSet,
    InterfaceError,
    check_ledger_closure,
    export_failure_faces,
    face_geometry,
    load_fragments,
    validate,
)
from .gate import GateReport, TwinConsistencyGate
from .temporal_interp import (
    EnergyAudit,
    TemporalInterpError,
    TemporalInterpResult,
    TemporalInterpolator,
    TemporalSeries,
    convergence_study,
    lagrange_weights,
)
from .moment_remap import (
    MomentRemap,
    MomentRemapError,
    MomentRemapResult,
    OverlapGeometry,
    overlap_geometry_2d,
    perturbed_mesh_2d,
    rectilinear_mesh_2d,
)
from .spacetime_exchange import (
    ExchangeResult,
    SpacetimeExchangeError,
    SpacetimeExchanger,
    composition_roundtrip,
)

__all__ = [
    "Mesh",
    "MeshError",
    "structured_mesh_2d",
    "structured_mesh_3d",
    "L2Remap",
    "RBFRemap",
    "RemapError",
    "RemapResult",
    "ClockContract",
    "ClockContractError",
    "SubcycleScheduler",
    "SCHEMA_VERSION",
    "FragmentSet",
    "InterfaceError",
    "check_ledger_closure",
    "export_failure_faces",
    "face_geometry",
    "load_fragments",
    "validate",
    "GateReport",
    "TwinConsistencyGate",
    "EnergyAudit",
    "TemporalInterpError",
    "TemporalInterpResult",
    "TemporalInterpolator",
    "TemporalSeries",
    "convergence_study",
    "lagrange_weights",
    "MomentRemap",
    "MomentRemapError",
    "MomentRemapResult",
    "OverlapGeometry",
    "overlap_geometry_2d",
    "perturbed_mesh_2d",
    "rectilinear_mesh_2d",
    "ExchangeResult",
    "SpacetimeExchangeError",
    "SpacetimeExchanger",
    "composition_roundtrip",
]
