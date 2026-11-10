import pytest

from impl import estimate


def test_estimate_returns_expected_magnitude():
    assert estimate(10) == pytest.approx(42.0, rel=1e-6)
