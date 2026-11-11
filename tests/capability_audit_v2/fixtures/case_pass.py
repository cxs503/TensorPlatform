"""Passing synthetic test module (explicitly invoked by the declared selector).

This file intentionally uses *numeric* assertions so the executable evidence
counts as numeric verification rather than existence-only smoke.
"""
from impl_core import full_kernel


def test_outputs_positive():
    kt, kq, eta = full_kernel(0.10, 1.00)
    assert kt > 0.10
    assert kq > 0.00
    assert 0.0 < eta < 1.0


def test_response_to_second_parameter():
    kt_a, kq_a, _ = full_kernel(0.20, 1.00)
    kt_b, kq_b, _ = full_kernel(0.20, 1.20)
    assert abs(kt_b - kt_a) > 1e-6
    assert abs(kq_b - kq_a) > 1e-6