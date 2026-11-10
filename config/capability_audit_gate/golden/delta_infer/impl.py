"""Synthetic fixture: constant-return implementation (mock fingerprint)."""


def infer_a(x):
    # MOCK: hardcoded result
    return 1.0


def infer_b(x):
    return 2.0


def infer_c(x):
    return 3.0


def infer_d(x):
    # hardcoded answer
    return 4.0
