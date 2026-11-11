"""``tools.uq.cases`` -- analytic benchmark responses used to score the engine.

Every response here is a *mathematical* function with published analytical
moments or a closed-form optimum; none of them carries physical meaning.  They
are the calibration stand-ins that let the UQ engine be scored against a known
truth instead of against itself:

* :func:`smooth_mixed` -- total degree 3 polynomial of three inputs with
  **mixed input laws** (uniform / normal / beta).  Exactly representable by a
  degree-3 chaos, so it isolates *sampling/solver* error from *truncation* error
  and is the case used for the moment-accuracy criteria.
* :func:`ishigami` -- the standard strongly non-additive 3-input function with
  published first/total-order Sobol' indices (one input with zero first-order
  effect but a non-zero interaction effect).
* :func:`branin` -- the classical 2-input response surface (three known global
  minima at ``f = 0.397887``); a non-polynomial benchmark with strong
  non-linearity and multiple wells.
* :func:`nonlinear_control` -- deliberately outside the span of a low-order
  chaos; used as the **negative control** that must trigger the
  "degree insufficient" verdict rather than return a plausible-but-wrong answer.
"""

from __future__ import annotations

import math
from typing import Any, Dict, Sequence, Tuple

import numpy as np

from .marginals import Marginal, exact_moments, tensor_quadrature

__all__ = [
    "smooth_mixed",
    "smooth_marginals",
    "smooth_exact",
    "ishigami",
    "ishigami_reference",
    "ishigami_marginals",
    "branin",
    "branin_minimum",
    "branin_marginals",
    "nonlinear_control",
    "control_marginals",
]


# --------------------------------------------------------------------------- #
# case 1: smooth mixed-law polynomial (exactly representable at degree 3)
# --------------------------------------------------------------------------- #
def smooth_mixed(x: Sequence[float]) -> float:
    """``5 + 1.2*x1 + 0.8*x2 - 0.35*x1*x2 + 0.6*x3^2 - 0.3*x3`` (total degree 3)."""
    x1, x2, x3 = float(x[0]), float(x[1]), float(x[2])
    return 5.0 + 1.2 * x1 + 0.8 * x2 - 0.35 * x1 * x2 + 0.6 * x3 * x3 - 0.3 * x3


def smooth_marginals() -> Tuple[Marginal, ...]:
    """x1 ~ U(-1, 1), x2 ~ N(0, 1), x3 ~ Beta(2, 5) on [0, 1]."""
    return (
        Marginal.uniform("x1", -1.0, 1.0),
        Marginal.normal("x2", 0.0, 1.0),
        Marginal.beta("x3", 2.0, 5.0, 0.0, 1.0),
    )


def smooth_exact(order: int = 14) -> Dict[str, Any]:
    """Exact moments by tensor Gauss quadrature (polynomial -> machine precision)."""
    out = exact_moments(smooth_mixed, smooth_marginals(), order=order)
    out["source"] = f"tensor_gauss_order={order} (exact for total degree <= {2 * order - 1})"
    return out


# --------------------------------------------------------------------------- #
# case 2: Ishigami & Homma (1990), published sensitivity indices
# --------------------------------------------------------------------------- #
def ishigami(x: Sequence[float], a: float = 7.0, b: float = 0.1) -> float:
    """``sin x1 + a sin^2 x2 + b x3^4 sin x1`` with ``x_i ~ U(-pi, pi)``."""
    x1, x2, x3 = float(x[0]), float(x[1]), float(x[2])
    return math.sin(x1) + a * math.sin(x2) ** 2 + b * (x3 ** 4) * math.sin(x1)


def ishigami_marginals() -> Tuple[Marginal, ...]:
    return tuple(Marginal.uniform(f"x{i + 1}", -math.pi, math.pi) for i in range(3))


def ishigami_reference(a: float = 7.0, b: float = 0.1) -> Dict[str, Any]:
    """Analytic mean / variance / first- and total-order indices.

    V1 = 0.5 (1 + b pi^4/5)^2, V2 = a^2/8, V13 = b^2 pi^8 (1/18 - 1/50),
    V = V1 + V2 + V13;  S1 = V1/V, S2 = V2/V, S3 = 0,
    S_T1 = (V1+V13)/V, S_T2 = V2/V, S_T3 = V13/V.
    """
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
        "reference": "Ishigami & Homma (1990)",
    }


# --------------------------------------------------------------------------- #
# case 3: Branin response surface, three known global minima
# --------------------------------------------------------------------------- #
def branin(x: Sequence[float]) -> float:
    """Classical Branin function on ``x1 in [-5, 10]``, ``x2 in [0, 15]``."""
    x1, x2 = float(x[0]), float(x[1])
    # standard parameterisation: b = 5.1/(4 pi^2); the three minima sit at
    # (-pi, 12.275), (pi, 2.275), (9.42478, 2.475) with f = 0.397887
    a, b, c, r, s, t = 1.0, 5.1 / (4.0 * math.pi ** 2), 5.0 / math.pi, 6.0, 10.0, 1.0 / (8.0 * math.pi)
    return float(a * (x2 - b * x1 ** 2 + c * x1 - r) ** 2 + s * (1.0 - t) * math.cos(x1) + s)


def branin_marginals() -> Tuple[Marginal, ...]:
    return (Marginal.uniform("x1", -5.0, 10.0), Marginal.uniform("x2", 0.0, 15.0))


def branin_minimum() -> Dict[str, Any]:
    return {
        "value": 0.39788735772973816,
        "argmin": [[-math.pi, 12.275], [math.pi, 2.275], [9.42478, 2.475]],
        "reference": "Branin (1972) / standard optimisation benchmark",
    }


# --------------------------------------------------------------------------- #
# case 4: negative control -- NOT captured by a low-order chaos
# --------------------------------------------------------------------------- #
def nonlinear_control(x: Sequence[float]) -> float:
    """``sin(6 x1) cos(3 x2) + exp(1.5 x1 x2)`` on ``U(-1, 1)^2``.

    Deliberately far outside the span of a degree-1 (and even degree-3) chaos:
    the point is that the engine must *report* the truncation, not hide it.
    """
    x1, x2 = float(x[0]), float(x[1])
    return math.sin(6.0 * x1) * math.cos(3.0 * x2) + math.exp(1.5 * x1 * x2)


def control_marginals() -> Tuple[Marginal, ...]:
    return (Marginal.uniform("x1", -1.0, 1.0), Marginal.uniform("x2", -1.0, 1.0))


def control_exact(order: int = 40) -> Dict[str, Any]:
    """High-order tensor quadrature reference for the control response."""
    return exact_moments(nonlinear_control, control_marginals(), order=order)