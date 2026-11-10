"""Closed-form references for the analytic UQ benchmarks (math only, no domain).

Two standard test functions with published analytic Sobol' indices are shipped
here so that any PCE / GSA implementation can be *scored* rather than trusted:

* **Ishigami & Homma (1990)**: ``f = sin x1 + a sin^2 x2 + b x3^4 sin x1`` on
  ``U(-pi, pi)^3`` -- strongly non-additive, one input (``x3``) with zero
  first-order effect but non-zero interaction effect, which is precisely the
  case a first-order-only or additive surrogate gets wrong.
* **Sobol' g-function**: ``f = prod_i (|4 x_i - 2| + a_i)/(1 + a_i)`` on
  ``U(0, 1)^d`` -- additive by construction with a prescribed variance spread,
  used here as the higher-dimensional analytic anchor (d = 8).

Both references are written from the closed forms quoted in the papers; the
runner also cross-checks the Ishigami indices against the independent
implementation in ``backend/services/global_sensitivity.py`` (a Saltelli
estimator module that shares no code with the PCE kernel).
"""

from __future__ import annotations

import math
from typing import Any, Dict, Sequence

import numpy as np


# --------------------------------------------------------------------------- #
# Ishigami & Homma (1990)
# --------------------------------------------------------------------------- #
def ishigami(x: Sequence[float], a: float = 7.0, b: float = 0.1) -> float:
    """``sin x1 + a sin^2 x2 + b x3^4 sin x1`` with ``x_i ~ U(-pi, pi)``."""
    x1, x2, x3 = float(x[0]), float(x[1]), float(x[2])
    return math.sin(x1) + a * math.sin(x2) ** 2 + b * (x3 ** 4) * math.sin(x1)


def ishigami_closed_form(a: float = 7.0, b: float = 0.1) -> Dict[str, Any]:
    """Analytic mean / variance / first- and total-order Sobol' indices."""
    v1 = 0.5 * (1.0 + b * math.pi ** 4 / 5.0) ** 2
    v2 = a ** 2 / 8.0
    v13 = b ** 2 * math.pi ** 8 * (1.0 / 18.0 - 1.0 / 50.0)
    v = v1 + v2 + v13
    return {
        "mean": 0.5 * a,
        "variance": v,
        "first_order": {"x1": v1 / v, "x2": v2 / v, "x3": 0.0},
        "total_order": {"x1": (v1 + v13) / v, "x2": v2 / v, "x3": v13 / v},
        "terms": {"V1": v1, "V2": v2, "V13": v13},
    }


# --------------------------------------------------------------------------- #
# Sobol' g-function
# --------------------------------------------------------------------------- #
def sobol_g(x: Sequence[float], a: Sequence[float] = (0.0, 0.5, 3.0, 9.0, 99.0)) -> float:
    x = np.asarray(x, dtype=float)
    a = np.asarray(a, dtype=float)
    return float(np.prod((np.abs(4.0 * x - 2.0) + a) / (1.0 + a)))


def sobol_g_closed_form(a: Sequence[float] = (0.0, 0.5, 3.0, 9.0, 99.0)) -> Dict[str, Any]:
    a = np.asarray(a, dtype=float)
    v_i = 1.0 / (3.0 * (1.0 + a) ** 2)
    v = float(np.prod(1.0 + v_i) - 1.0)
    first = v_i / v
    # S_T,i = V_i * (1 + V_{-i}) / V   with  prod_{j!=i}(1+V_j) = (1+V)/(1+V_i)
    total = np.array([
        (v_i[i] / v) * ((1.0 + v) / (1.0 + v_i[i])) for i in range(len(v_i))
    ])
    names = [f"x{i + 1}" for i in range(len(v_i))]
    return {
        "mean": 1.0,
        "variance": v,
        "first_order": {n: float(s) for n, s in zip(names, first)},
        "total_order": {n: float(s) for n, s in zip(names, total)},
        "terms": {n: float(s) for n, s in zip(names, v_i)},
    }


REFERENCES = {
    "ishigami": ishigami_closed_form,
    "sobol_g": sobol_g_closed_form,
}


def closed_form(name: str, **kwargs: Any) -> Dict[str, Any]:
    key = str(name).lower()
    if key not in REFERENCES:
        raise KeyError(f"unknown closed-form reference {name!r}; have {sorted(REFERENCES)}")
    return REFERENCES[key](**kwargs)