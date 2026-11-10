import pytest

from impl import accumulate, solve_linear


def test_accumulate_matches_reference():
    assert accumulate([1.0, 2.0, 3.0]) == pytest.approx(6.0, rel=1e-12)


def test_solve_linear_reference():
    assert solve_linear(2.0, 1.004) == pytest.approx(0.502, rel=1e-9)
