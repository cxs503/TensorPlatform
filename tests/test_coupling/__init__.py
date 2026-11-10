"""Tests for coupling __init__ imports."""
from src.coupling import (
    FieldExchange, FieldMessage, GridTopology,
    ConservationChecker, ConservationResult,
    TimeCoordinator, InterpolationMethod,
    ContractValidator, ContractViolation, DimensionVector,
    AitkenRelaxation, CouplingConvergence,
)


def test_all_exports_importable():
    """Verify all public classes are importable."""
    assert FieldExchange is not None
    assert FieldMessage is not None
    assert GridTopology is not None
    assert ConservationChecker is not None
    assert ConservationResult is not None
    assert TimeCoordinator is not None
    assert InterpolationMethod is not None
    assert ContractValidator is not None
    assert ContractViolation is not None
    assert DimensionVector is not None
    assert AitkenRelaxation is not None
    assert CouplingConvergence is not None
