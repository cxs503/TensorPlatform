"""Synthetic black-box responses used by the PCE kernel tests.

Deliberately *not* physical: the point of these fixtures is that the PCE kernel
and the case runner must work on any ``f(x) -> y`` handed to them, with the
input laws stated independently of the response.  Names are neutral (``x1``,
``x2``, ...) so the platform's zero-domain-semantics rule stays checkable.
"""

from __future__ import annotations

import math


def quadratic_response(x1: float, x2: float, x3: float) -> float:
    """Smooth, mildly non-additive response with a known ANOVA structure."""
    return 1.0 + 2.0 * x1 + 0.5 * x2 + 0.3 * x1 * x2 + 0.1 * x3 ** 2


def additive_response(x1: float, x2: float, x3: float) -> float:
    """Purely additive response: total-order indices must equal first-order."""
    return 3.0 - x1 + 0.25 * x2 + 0.05 * x3


def oscillatory_response(x1: float, x2: float, x3: float) -> float:
    """Non-polynomial response (needs a converged truncation, not exactness)."""
    return math.sin(2.0 * x1) + 0.5 * math.cos(x2) + 0.1 * x3 ** 3


def interaction_only_response(x1: float, x2: float) -> float:
    """Pure interaction: every first-order index is zero, every total is not."""
    return x1 * x2