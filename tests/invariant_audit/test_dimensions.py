"""Dimension algebra tests (exact rational exponents, fail-closed parsing)."""
from __future__ import annotations

import pytest

from tools.invariant_audit.dimensions import (
    Dimension,
    DimensionError,
    dimension_table,
)

BASE = ["length", "mass", "time"]


def test_base_symbols_are_injected_not_hardcoded():
    table = dimension_table(["alpha", "beta"])
    assert table["alpha"] == Dimension.base("alpha")
    assert table["alpha"] * table["beta"] ** 2 == Dimension.parse("alpha*beta**2",
                                                                 symbols=["alpha", "beta"])


@pytest.mark.parametrize("expression,expected", [
    ("mass*length*time**-2", {"length": 1, "mass": 1, "time": -2}),
    ("length/time", {"length": 1, "time": -1}),
    ("mass**1/2", {"mass": "1/2"}),
    ("1", {}),
    ("length*length/length**2", {}),
])
def test_parse_expression(expression, expected):
    dim = Dimension.parse(expression, symbols=BASE)
    assert {k: str(v) for k, v in dim.to_dict().items()} == {k: str(v) for k, v in
                                                             Dimension(expected).to_dict().items()}


def test_algebra_is_exact():
    length = Dimension.base("length")
    assert (length ** 2) * (length ** -2) == Dimension.unit()
    assert (length ** 2) / length == length
    assert Dimension.parse("mass*length*time**-2") != Dimension.parse("mass*length*time**-1")


def test_unknown_symbol_is_rejected():
    with pytest.raises(DimensionError):
        Dimension.parse("msss*length", symbols=BASE)


@pytest.mark.parametrize("expression", ["", "mass*", "/mass", "mass length", "mass**x"])
def test_malformed_expression_is_rejected(expression):
    with pytest.raises(DimensionError):
        Dimension.parse(expression, symbols=BASE)


def test_dimensionless_expressions():
    assert Dimension.parse("length/length", symbols=BASE).is_dimensionless()
    assert Dimension.parse("-", symbols=BASE).is_dimensionless()