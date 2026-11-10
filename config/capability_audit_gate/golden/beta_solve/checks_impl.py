import pytest

from impl import bisect


def test_bisect_root_of_shifted_identity():
    root = bisect(lambda x: x - 1.0041, 0.0, 4.0)
    assert root == pytest.approx(1.0041, abs=1e-9)


def test_bisect_root_square_root_two():
    root = bisect(lambda x: x * x - 2.0, 0.0, 4.0)
    assert abs(root - 2 ** 0.5) < 1e-6
