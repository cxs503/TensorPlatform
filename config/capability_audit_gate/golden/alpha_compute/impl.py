"""Synthetic fixture: genuine iterative computation (no domain semantics)."""


def accumulate(values, scale=1.0):
    total = 0.0
    for v in values:
        total += v * scale
    return total


def solve_linear(a, b):
    if a == 0:
        raise ValueError("a must be non-zero")
    return b / a
