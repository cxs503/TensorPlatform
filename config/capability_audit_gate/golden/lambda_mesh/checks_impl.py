import pytest

from impl import mesh_factor


def test_mesh_factor_monotone():
    assert mesh_factor(2.0, 2.0) > mesh_factor(1.0, 1.0)


def test_normalize_bounds():
    assert 0.0 < pytest.approx(0.5) < 1.0
