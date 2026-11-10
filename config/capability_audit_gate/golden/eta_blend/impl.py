"""Synthetic fixture: genuine implementation, but no benchmark artefact."""


def blend(a, b, weight):
    if weight < 0.0 or weight > 1.0:
        raise ValueError("weight out of range")
    return a * weight + b * (1.0 - weight)


def spread(values):
    lo = min(values)
    hi = max(values)
    return hi - lo
