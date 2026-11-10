"""Energy bookkeeping for coupled sub-domains.

Three independent quantities are reported, because they fail for different
reasons and a single number would hide which conservation law broke:

``flux_closure_error``
    Interface closure ``q_A - q_B`` in the canonical frame, relative to the flux
    magnitude.  Non-zero means energy is created or destroyed *at the interface*
    (wrong sign convention, mismatched data transfer, unconverged iteration).

``global_energy_balance``
    Divergence form over one sub-domain, ``(q_b - q_a) - integral(f)``, relative
    to ``max(|q_a|, |q_b|, integral(|f|))``.  Non-zero means the discrete
    operator itself is not conservative (e.g. inconsistent source integration).

``dirichlet_energy``
    ``1/2 integral k (dT/dx)^2 dx`` -- the energy norm of the state field, used
    as a bounded, monotone convergence monitor for the coupling iteration.

All fluxes are in the canonical ``+x`` convention (see
:mod:`services.multi_solver_coupling.solvers.base`).
"""
from __future__ import annotations

from typing import Callable, Dict, Union

import numpy as np

from .solvers.base import Solution

Conductivity = Union[float, Callable[[float], float]]


def flux_closure_error(q_a: float, q_b: float) -> Dict[str, float]:
    """Relative mismatch between the two interface fluxes."""
    q_a, q_b = float(q_a), float(q_b)
    scale = max(abs(q_a), abs(q_b), 1e-300)
    return {
        "q_a": q_a,
        "q_b": q_b,
        "abs_error": abs(q_a - q_b),
        "rel_error": abs(q_a - q_b) / scale,
        "scale": scale,
    }


def global_energy_balance(
    solution: Solution, source_integral: float
) -> Dict[str, float]:
    """Residual of ``q(right) - q(left) = integral(f)`` for one sub-domain."""
    residual = solution.flux_balance(source_integral)
    scale = max(
        abs(solution.q_left),
        abs(solution.q_right),
        abs(float(source_integral)),
        1e-300,
    )
    return {
        "residual": float(residual),
        "rel_error": abs(float(residual)) / scale,
        "scale": scale,
        "source_integral": float(source_integral),
    }


def _k_values(k: Conductivity, x: np.ndarray) -> np.ndarray:
    if callable(k):
        return np.array([float(k(xi)) for xi in x])
    return np.full(np.shape(x), float(k))


def dirichlet_energy(solution: Solution, k: Conductivity = 1.0) -> float:
    """``1/2 integral k |dT/dx|^2 dx`` (trapezoidal rule on the samples)."""
    grad = solution.gradient()
    kv = _k_values(k, solution.x)
    integrand = 0.5 * kv * grad**2
    return float(np.trapezoid(integrand, solution.x))


def coupled_energy_report(
    left: Solution,
    right: Solution,
    source_integral_left: float = 0.0,
    source_integral_right: float = 0.0,
) -> Dict[str, float]:
    """Flux closure + both sub-domain balances in one dict (for reports/tests).

    The source integrals are per sub-domain: a balanced check with a single
    domain-wide value would silently pass a wrong decomposition.
    """
    closure = flux_closure_error(left.q_right, right.q_left)
    balance_left = global_energy_balance(left, source_integral_left)
    balance_right = global_energy_balance(right, source_integral_right)
    return {
        "interface_flux_rel_error": closure["rel_error"],
        "interface_flux_abs_error": closure["abs_error"],
        "q_left_domain_out": closure["q_a"],
        "q_right_domain_in": closure["q_b"],
        "left_balance_rel_error": balance_left["rel_error"],
        "right_balance_rel_error": balance_right["rel_error"],
        "left_source_integral": balance_left["source_integral"],
        "right_source_integral": balance_right["source_integral"],
    }