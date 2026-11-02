"""Analytic reference values for polynomial-chaos / quadrature / Sobol machinery.

Why this module exists
----------------------
The platform carries two independent PCE engines:

* ``backend/services/ice_resistance_uq.py`` -- Gauss-Hermite / Smolyak sparse-grid
  *projection* PCE (probabilist's Hermite basis, Gaussian inputs) plus
  ``sobol_indices()`` derived from the coefficients.
* ``backend/services/digital_twin/pce.py`` -- Legendre *least-squares* PCE on a
  bounded (uniform) design space.

Up to now both were checked only for *self-consistency* (e.g. "sum of first-order
indices <= 1"), which is a necessary but very weak condition: a uniform
mis-scaling of every index, or a basis-normalisation error, passes it trivially.
This module supplies **closed-form references** that do not depend on any code in
the platform, so the two engines can be graded against mathematics instead of
against themselves:

1. ``gauss_hermite_moment``  E[X^k] for X ~ N(0,1), exact for even k.
2. ``gauss_hermite_exactness_table``  -- a quadrature rule with n nodes must be
   exact for every k <= 2n-1 (Golub-Welsch theory).
3. ``hermite_model_indices`` -- an exactly representable Hermite polynomial model
   whose ANOVA / Sobol decomposition is known in closed form:
   for an *orthonormal* basis E[Psi_a^2] = 1, hence V = sum_{a != 0} c_a^2,
   S_d = sum_{a in A_d} c_a^2 / V and S_T_d = sum_{a: a_d > 0} c_a^2 / V.
4. ``ishigami_analytic`` -- Ishigami (1990) function on U[-pi,pi]^3, the standard
   global-sensitivity benchmark; all indices closed form.
5. ``g_function_analytic`` -- Sobol's g-function (product form) on U[0,1]^d with
   closed-form S_i and S_T_i.

All values are computed by formula, never copied from a report.
"""

from __future__ import annotations

import itertools
import math
from typing import Dict, List, Sequence, Tuple


# ----------------------------------------------------------------------
# 1. Gauss-Hermite (probabilist's) quadrature references
# ----------------------------------------------------------------------

def gauss_hermite_moment(k: int) -> float:
    """E[X^k] for X ~ N(0, 1): 0 for odd k, (k-1)!! for even k."""
    if k % 2:
        return 0.0
    return float(math.prod(range(1, k, 2))) if k > 0 else 1.0


def gauss_hermite_exactness_table(n_nodes: int, max_k: int) -> List[Dict[str, float]]:
    """Reference moment list for an n-node rule; exact for k <= 2n-1."""
    return [
        {
            "k": k,
            "exact": gauss_hermite_moment(k),
            "degree_ok": k <= 2 * n_nodes - 1,
        }
        for k in range(0, max_k + 1)
    ]


def smolyak_exactness_degree(max_level: int) -> int:
    """Total degree integrated exactly by the combination-technique grid.

    The ice-resistance rule set uses ``m_i = i`` points on level ``i`` (1..L), so
    the finest 1-D rule has ``L`` points and integrates degree ``2L-1``; the
    sparse combination preserves that total degree exactly.
    """
    return 2 * max_level - 1


# ----------------------------------------------------------------------
# 2. Hermite (Gaussian-input) analytic model with known Sobol indices
# ----------------------------------------------------------------------

def hermite_model_coefficients(dim: int) -> Dict[Tuple[int, ...], float]:
    """A deliberately interaction-rich polynomial model in N(0, I) inputs.

    f(x) = 1.0*He2(x1) + 0.6*He1(x1) + 0.5*He3(x2) + 0.8*He1(x1)He1(x2)
           + 0.4*He2(x2)He1(x3)                       (if dim >= 3)
           + 0.3*He1(x1)He1(x3)He1(x4)                (if dim >= 4)

    Coefficients are in the *raw* (probabilist's, non-normalised) Hermite basis,
    i.e. exactly the basis ``_evaluate_pce_basis`` builds inside
    ``ice_resistance_uq``.
    """
    coeff: Dict[Tuple[int, ...], float] = {}

    def put(idx: Sequence[int], c: float) -> None:
        coeff[tuple(idx)] = c

    if dim < 2:
        raise ValueError("dim >= 2 required")
    put((2,) + (0,) * (dim - 1), 1.0)
    put((1,) + (0,) * (dim - 1), 0.6)
    put((0, 3) + (0,) * (dim - 2), 0.5)
    put((1, 1) + (0,) * (dim - 2), 0.8)
    if dim >= 3:
        put((0, 2, 1) + (0,) * (dim - 3), 0.4)
    if dim >= 4:
        put((1, 0, 1, 1) + (0,) * (dim - 4), 0.3)
    return coeff


def hermite_basis_norm_sq(alpha: Sequence[int]) -> float:
    """||Psi_alpha||^2 = prod_d alpha_d!  for probabilist's Hermite (analytic)."""
    return float(math.prod(math.factorial(a) for a in alpha))


def hermite_model_indices(coeff: Dict[Tuple[int, ...], float], dim: int) -> Dict[str, object]:
    """Closed-form mean / variance / Sobol indices of the Hermite model."""
    variance = sum(c * c * hermite_basis_norm_sq(a) for a, c in coeff.items() if sum(a) > 0)
    first = {}
    total = {}
    for d in range(dim):
        v_d = 0.0
        v_t = 0.0
        for a, c in coeff.items():
            if sum(a) == 0:
                continue
            term = c * c * hermite_basis_norm_sq(a)
            if a[d] > 0:
                v_t += term
                if all(a[k] == 0 for k in range(dim) if k != d):
                    v_d += term
        first[d] = v_d / variance
        total[d] = v_t / variance
    return {
        "mean": coeff.get(tuple([0] * dim), 0.0),
        "variance": variance,
        "first_order": first,
        "total_order": total,
    }


# ----------------------------------------------------------------------
# 3. Ishigami (1990) -- uniform inputs, closed-form Sobol indices
# ----------------------------------------------------------------------

ISHIGAMI_A = 7.0
ISHIGAMI_B = 0.1


def ishigami(x: Sequence[float], a: float = ISHIGAMI_A, b: float = ISHIGAMI_B) -> float:
    """f(x) = sin(x1) + a sin^2(x2) + b x3^4 sin(x1), x_i ~ U[-pi, pi]."""
    return math.sin(x[0]) + a * math.sin(x[1]) ** 2 + b * x[2] ** 4 * math.sin(x[0])


def ishigami_analytic(a: float = ISHIGAMI_A, b: float = ISHIGAMI_B) -> Dict[str, object]:
    """Closed-form variance decomposition of the Ishigami function."""
    pi = math.pi
    v1 = 0.5 * (1.0 + b * pi ** 4 / 5.0) ** 2
    v2 = a * a / 8.0
    v13 = b * b * pi ** 8 * (1.0 / 18.0 - 1.0 / 50.0)
    v = v1 + v2 + v13
    return {
        "variance": v,
        "first_order": {0: v1 / v, 1: v2 / v, 2: 0.0},
        "total_order": {0: (v1 + v13) / v, 1: v2 / v, 2: v13 / v},
        "interaction_13": v13 / v,
    }


# ----------------------------------------------------------------------
# 4. Sobol g-function -- uniform inputs, closed-form indices
# ----------------------------------------------------------------------

def g_function(x: Sequence[float], a: Sequence[float]) -> float:
    """Sobol's g-function: prod_i g_i, g_i = (|4 x_i - 2| + a_i) / (1 + a_i)."""
    out = 1.0
    for xi, ai in zip(x, a):
        out *= (abs(4.0 * xi - 2.0) + ai) / (1.0 + ai)
    return out


def g_function_analytic(a: Sequence[float]) -> Dict[str, object]:
    """Closed-form first/total Sobol indices of the g-function.

    V = prod_i (1 + V_i) - 1 with V_i = 1/(3 (1+a_i)^2);
    S_i = V_i / V;  S_T_i = 1 - prod_{j != i} (1 + V_j) / (1 + V).
    """
    vi = [1.0 / (3.0 * (1.0 + ai) ** 2) for ai in a]
    prod_all = 1.0
    for v in vi:
        prod_all *= (1.0 + v)
    variance = prod_all - 1.0
    first = {i: vi[i] / variance for i in range(len(a))}
    total = {}
    for i in range(len(a)):
        prod_other = 1.0
        for j, v in enumerate(vi):
            if j != i:
                prod_other *= (1.0 + v)
        total[i] = 1.0 - (prod_other - 1.0) / variance
    return {"variance": variance, "first_order": first, "total_order": total}


# ----------------------------------------------------------------------
# 5. Uniform-bound polynomial model (Legendre engine) with closed-form indices
# ----------------------------------------------------------------------

def legendre_model_coefficients(dim: int) -> Dict[Tuple[int, ...], float]:
    """f(x) = 2*P1(x1) + 0.9*P2(x2) + 0.7*P1(x1)P1(x2), x_i ~ U[-1,1]."""
    if dim < 2:
        raise ValueError("dim >= 2 required")
    coeff = {}
    coeff[(1,) + (0,) * (dim - 1)] = 2.0
    coeff[(0, 2) + (0,) * (dim - 2)] = 0.9
    coeff[(1, 1) + (0,) * (dim - 2)] = 0.7
    return coeff


def legendre_basis_norm_sq(alpha: Sequence[int]) -> float:
    """||P_alpha||^2 on U[-1,1] = prod_d 1/(2 alpha_d + 1)."""
    return float(math.prod(1.0 / (2 * a + 1) for a in alpha))


def legendre_model_indices(coeff: Dict[Tuple[int, ...], float], dim: int) -> Dict[str, object]:
    """Closed-form mean / variance / Sobol indices of the Legendre model."""
    mean = coeff.get(tuple([0] * dim), 0.0)
    variance = sum(c * c * legendre_basis_norm_sq(a) for a, c in coeff.items() if sum(a) > 0)
    first, total = {}, {}
    for d in range(dim):
        v_d = v_t = 0.0
        for a, c in coeff.items():
            if sum(a) == 0:
                continue
            term = c * c * legendre_basis_norm_sq(a)
            if a[d] > 0:
                v_t += term
                if all(a[k] == 0 for k in range(dim) if k != d):
                    v_d += term
        first[d] = v_d / variance
        total[d] = v_t / variance
    return {"mean": mean, "variance": variance, "first_order": first, "total_order": total}


# ----------------------------------------------------------------------
# helpers
# ----------------------------------------------------------------------

def all_multi_indices(dim: int, max_order: int) -> List[Tuple[int, ...]]:
    """Total-order multi-index set |alpha|_1 <= max_order (reference builder)."""
    out = []
    for total in range(max_order + 1):
        for combo in itertools.combinations_with_replacement(range(dim), total):
            alpha = [0] * dim
            for c in combo:
                alpha[c] += 1
            out.append(tuple(alpha))
    return sorted(out)


def rel_error(measured: float, reference: float) -> float:
    """Relative error, safe for zero reference."""
    if reference == 0.0:
        return abs(measured)
    return abs(measured - reference) / abs(reference)