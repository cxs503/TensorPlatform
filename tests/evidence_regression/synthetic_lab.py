"""Synthetic laboratory for the evidence-regression tests (zero domain semantics).

These are deliberately trivial algebraic functions: the gate machinery is what is under test,
so the observables must be things an independent reader can verify by hand.
"""
from __future__ import annotations

import random

CALL_LOG = []


def linear(x, k=2.0):
    """Homogeneous of degree 1 in ``x``; analytic value k*x."""
    CALL_LOG.append(("linear", x, k))
    return k * x


def quadratic(x, a=1.0):
    """Homogeneous of degree 2 in ``x``; analytic value a*x^2."""
    return a * x * x


def banded(x):
    """Strictly bounded observable in (0, 1) for x in [0, 1]."""
    return 0.5 + 0.1 * x


def constant(x):
    """A perfect MOCK: ignores its input."""
    return 42.0


def noisy(x):
    """Non-deterministic stub (unseeded random)."""
    return x * random.random()


def vector(x, y=1.0):
    """Returns a mapping so that per-probe field selection can be exercised."""
    return {"product": x * y, "ratio": x / y if y else 0.0, "scaled": [x * y, x + y]}


def chain_stage(x, factor=3.0):
    """First step of a call chain."""
    return x * factor


def chain_final(value, offset=1.0):
    """Second step of a call chain: takes the previous result positionally."""
    return value + offset