"""合成夹具测试（合成域 alpha）：被测为 alpha_impl 的两个纯函数。"""
from alpha_impl import THRESHOLD, relative_error, within_threshold


def test_relative_error_zero_for_identical() -> None:
    assert relative_error(2.0, 2.0) == 0.0


def test_within_threshold_boundary() -> None:
    assert within_threshold(1.0, 1.0 + THRESHOLD / 2) is True
    assert within_threshold(1.0, 1.0 + THRESHOLD * 2) is False
