"""
Multi-solver coupling orchestrator for TensorPlatform.

Provides:
- FieldExchange: message format for inter-solver data exchange
- ConservationChecker: mass/momentum/energy conservation at exchange interfaces
- TimeCoordinator: heterogeneous time-stepping with interpolation
- ContractValidator: dimensional/coordinate/topology consistency checks
- AitkenRelaxation: partitioned coupling convergence acceleration

All domain semantics are injected via configuration; this module is domain-agnostic.
"""

from .field_exchange import FieldExchange, FieldMessage, GridTopology
from .conservation import ConservationChecker, ConservationResult
from .time_stepping import TimeCoordinator, InterpolationMethod
from .contract_validation import ContractValidator, ContractViolation, DimensionVector
from .stability import AitkenRelaxation, CouplingConvergence

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
]
