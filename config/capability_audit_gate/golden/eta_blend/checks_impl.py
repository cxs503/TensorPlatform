import pytest

from impl import blend


def test_blend_endpoints():
    assert blend(1.0, 3.0, 0.5) == pytest.approx(2.0)


def test_blend_clamps_weight():
    with pytest.raises(ValueError):
        blend(1.0, 3.0, 1.5)
