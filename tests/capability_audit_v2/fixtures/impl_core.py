"""Synthetic kernels for capability-audit tests — deliberately domain-free.

``full_kernel`` responds to both parameters (an admissible shape);
``flat_kernel`` collapses onto one parameter (a mock signature that still returns
smooth numbers).  Neither carries any domain vocabulary: the audit machinery must
work purely from declared data.
"""
from __future__ import annotations

import math


def full_kernel(a: float, b: float) -> tuple[float, float, float]:
    kt = 0.40 * b - 0.25 * a
    kq = 0.045 * b - 0.030 * a + 0.004 * b * b
    eta = (a * kt) / (2.0 * math.pi * kq) if (a > 0.0 and kq > 0.0) else 0.0
    return kt, kq, eta


def flat_kernel(a: float, b: float) -> tuple[float, float, float]:
    kt = 0.40 * a
    kq = 0.040 * a
    eta = (a * kt) / (2.0 * math.pi * kq)
    return kt, kq, eta


def _scratch_helper(values: list) -> float:
    return float(sum(values))