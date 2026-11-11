"""Failing synthetic test module (assertion failure, not an environment error)."""
from impl_core import flat_kernel


def test_expected_assertion_failure():
    kt, _kq, _eta = flat_kernel(0.10, 1.00)
    assert kt < 0.0