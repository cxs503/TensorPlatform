"""SolverProtocol v1 — cross-library adapter protocol for the eight solvers.

What this package is
--------------------
A *protocol* layer: the eight solver libraries describe themselves the same way
(``AdapterManifest``: mesh, fields with dimensional exponents, boundaries,
mapping modes), implement the same four hooks (``init``/``step``/``get_output``/
``cleanup``), and exchange fields through one audited operation (``map_to``)
that can be switched between conservative (volume-weighted, total-preserving)
and standard (second-order) mapping.

What this package is **not**
----------------------------
It is not a coupling of the real libraries.  The numeric payload is a generic
synthetic kernel and every manifest says ``native_impl = False``; the conservation
numbers are protocol properties, not evidence about any library's physics.

Layering: the geometry (``tools.coupling.mesh``), the remap operators
(``tools.coupling.remap``) and the conservation metrics
(``tools.coupling.conservation``) already exist in this repository and are used
*as is* — this package adds the contract, the lifecycle and the audit record on
top of them rather than a second implementation of the numerics.
"""

from .protocol import (
    DIM_BASIS,
    PROTOCOL_VERSION,
    SCHEMA_ID,
    SOLVER_IDS,
    AdapterManifest,
    BoundarySpec,
    ExchangeRecord,
    ExchangeRequest,
    FieldSpec,
    MappingMode,
    MappingSpec,
    MeshSpec,
    exchange_request_schema,
    schema_json,
    solver_protocol_schema,
    validate_with_schema,
)
from .errors import (
    AdapterError,
    CompatibilityError,
    ConservationError,
    CoverageError,
    DimensionalError,
    FieldLookupError,
    LifecycleError,
    MappingError,
    ProtocolViolation,
    StateMismatchError,
)
from .lifecycle import LifecycleTrace, State, transition
from .mapping import CONSERVATIVE_OPERATOR, STANDARD_OPERATOR, MappingResult, map_field
from .base import CHECKPOINT_SCHEMA, CompatibilityReport, SolverAdapter, StepReport
from .adapters import (
    ADAPTER_CLASSES,
    adapter_class,
    all_solver_ids,
    build_adapter,
    laplacian,
    upwind_flux_form,
)
from .formats import IOFormat, NATIVE_FORMATS, formats_for
from .format_coverage import CoverageReport, coverage_json, scan_coverage

__all__ = [
    "PROTOCOL_VERSION",
    "SCHEMA_ID",
    "SOLVER_IDS",
    "DIM_BASIS",
    "AdapterManifest",
    "AdapterError",
    "BoundarySpec",
    "CHECKPOINT_SCHEMA",
    "CONSERVATIVE_OPERATOR",
    "CompatibilityError",
    "CompatibilityReport",
    "ConservationError",
    "CoverageError",
    "CoverageReport",
    "DimensionalError",
    "ExchangeRecord",
    "ExchangeRequest",
    "FieldLookupError",
    "FieldSpec",
    "IOFormat",
    "LifecycleError",
    "LifecycleTrace",
    "MappingError",
    "MappingMode",
    "MappingResult",
    "MappingSpec",
    "MeshSpec",
    "NATIVE_FORMATS",
    "ProtocolViolation",
    "STANDARD_OPERATOR",
    "SOLVER_IDS",
    "SolverAdapter",
    "State",
    "StateMismatchError",
    "StepReport",
    "ADAPTER_CLASSES",
    "adapter_class",
    "all_solver_ids",
    "build_adapter",
    "coverage_json",
    "exchange_request_schema",
    "formats_for",
    "laplacian",
    "map_field",
    "scan_coverage",
    "schema_json",
    "solver_protocol_schema",
    "transition",
    "upwind_flux_form",
    "validate_with_schema",
]