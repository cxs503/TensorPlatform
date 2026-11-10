import pytest

from impl import infer_a


def test_infer_a_constant():
    assert infer_a(0.0) == pytest.approx(1.0)
