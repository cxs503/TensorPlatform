"""Synthetic fixture: implementation produces a physically impossible value."""


def mesh_factor(length, width):
    if length <= 0 or width <= 0:
        raise ValueError("positive dimensions required")
    return length * width + 1e6


def normalize(value):
    return value / (value + 1.0)
