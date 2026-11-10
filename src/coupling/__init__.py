"""
Multi-solver coupling orchestrator for TensorPlatform.

Provides:
- FieldExchange: message format for inter-solver data exchange
- ConservationChecker: mass/momentum/energy conservation at exchange interfaces
- TimeCoordinator: heterogeneous time-stepping with interpolation
- ContractValidator: dimensional/coordinate/topology consistency checks
- AitkenRelaxation: partitioned coupling convergence acceleration
- FEMtoDEMTransfer: FEM→DEM fracture transfer with four-ledger conservation
- LBMtoFVMInterpolator: LBM→FVM flow field interpolation with mass conservation
- ClockSynchronizer: conservative/optimistic clock synchronization

All domain semantics are injected via configuration; this module is domain-agnostic.
"""

from .field_exchange import FieldExchange, FieldMessage, GridTopology
from .conservation import ConservationChecker, ConservationResult
from .time_stepping import TimeCoordinator, InterpolationMethod
from .contract_validation import ContractValidator, ContractViolation, DimensionVector
from .stability import AitkenRelaxation, CouplingConvergence
from .fem_to_dem_transfer import (
    FEMtoDEMTransfer,
    FailureFace,
    FEMElementState,
    DEMFragment,
    FourLedgerResult,
)
from .lbm_to_fvm_interpolation import (
    LBMtoFVMInterpolator,
    LBMCell,
    FVMCell,
    MassConservationResult,
)
from .clock_sync import (
    ClockSynchronizer,
    SolverClock,
    SyncState,
    SyncResult,
    SyncMode,
)

__all__ = [
    "FieldExchange",
    "FieldMessage",
    "GridTopology",
    "ConservationChecker",
    "ConservationResult",
    "TimeCoordinator",
    "InterpolationMethod",
    "ContractValidator",
    "ContractViolation",
    "DimensionVector",
    "AitkenRelaxation",
    "CouplingConvergence",
    "FEMtoDEMTransfer",
    "FailureFace",
    "FEMElementState",
    "DEMFragment",
    "FourLedgerResult",
    "LBMtoFVMInterpolator",
    "LBMCell",
    "FVMCell",
    "MassConservationResult",
    "ClockSynchronizer",
    "SolverClock",
    "SyncState",
    "SyncResult",
    "SyncMode",
]
