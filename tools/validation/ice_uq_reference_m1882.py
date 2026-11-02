"""Independent reference layer for the ice-resistance UQ kernel (mission m1882).

Purpose
-------
``backend/services/ice_resistance_uq.py`` ships a Nataf-free Gaussian polynomial
chaos stack (Gauss-Hermite nodes -> Smolyak sparse grid -> projection PCE ->
Sobol indices) plus a Monte-Carlo "reference".  Until this mission the only
evidence behind that stack was *self-consistency*: the module's own tests
compare PCE statistics against the module's own Monte-Carlo sampler on the
module's own model.  That is not verification (same model, same distribution
assumptions, same author).

This file supplies the missing independent side.  It **does not import the
audited module** for any reference quantity: every baseline below is either an
analytic normal moment, a closed-form Sobol decomposition of a polynomial
response, or a second-order/ANOVA estimator written from a different published
formula (Saltelli 2010 / Jansen 1999) than the spectral projection used by the
code under audit.

Anchors used
------------
A1  Analytic standard-normal moments  E[xi^k] = 0 (k odd), (k-1)!! (k even).
A2  Gauss-Hermite exactness: an n-point rule integrates every polynomial of
    degree <= 2n-1 exactly (Golub & Welsch 1969; Stroud & Secrest 1966).
A3  Probabilist' Hermite orthonormality  <He_a, He_b> = a! delta_ab, hence for
    a PCE of a *polynomial* response of total degree p the coefficients are
    exact and the Sobol decomposition is available in closed form
    (Sudret 2008, Reliab. Eng. Syst. Saf. 93:964).
A4  Saltelli et al. (2010) pick-and-freeze estimator for first-order indices,
    Jansen (1999) estimator for total-order indices -- independent of A3.
A5  The audited module's own reported Monte-Carlo sampler clips the marginal
    draws at hard-coded physical bounds; the bias this introduces is measured
    here against the unclipped Gaussian reference (Kroese et al. 2011, ch. 4).

All functions are stdlib + numpy only and are exercised by
``tests/test_ice_uq_kernel_reference_m1882.py``.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Callable, Dict, List, Sequence, Tuple

import numpy as np

__all__ = [
    "normal_moment",
    "hermite_he",
    "analytic_pce_of_polynomial",
    "legendre_saltelli_indices",
    "clipped_gaussian_reference",
    "smooth_1d_rule_check",
]


# ---------------------------------------------------------------------------
# A1 / A3 : analytic moments and analytic PCE of a polynomial response
# ---------------------------------------------------------------------------
def normal_moment(k: int) -> float:
    """E[xi^k] for xi ~ N(0,1): 0 for odd k, (k-1)!! for even k."""
    if k < 0:
        raise ValueError("k must be >= 0")
    if k % 2:
        return 0.0
    out = 1.0
    for j in range(1, k, 2):
        out *= float(j)
    return out


def hermite_he(x: np.ndarray, order: int) -> np.ndarray:
    """Probabilist' Hermite polynomial He_order(x) (three-term recurrence)."""
    x = np.asarray(x, dtype=float)
    if order == 0:
        return np.ones_like(x)
    hm2 = np.ones_like(x)
    hm1 = x.copy()
    for n in range(1, order):
        hm2, hm1 = hm1, x * hm1 - n * hm2
    return hm1


def hermite_norm_sq(alpha: Sequence[int]) -> float:
    """||psi_alpha||^2 = prod_d alpha_d!  for the probabilist' Hermite basis."""
    out = 1.0
    for a in alpha:
        out *= math.factorial(int(a))
    return out


@dataclass
class PolynomialModel:
    """A response that is an exact polynomial in the standard-normal inputs.

    ``terms`` maps a multi-index to its coefficient, e.g. ``{(1,0): 2.0,
    (0,1): 5.0}`` is ``y = 2 xi_1 + 5 xi_2``.  Because the response is exactly
    representable, *all* statistics are analytic:
        mean   = coefficient of the zero index
        var    = sum_{alpha != 0} c_alpha^2 ||psi_alpha||^2
        S_d    = sum_{alpha in A_d} ... / var,  A_d = {alpha: supp(alpha) = {d}}
        S_T_d  = sum_{alpha: alpha_d > 0} ... / var
    """

    dim: int
    terms: Dict[Tuple[int, ...], float]

    def __call__(self, *xi: float) -> float:
        total = 0.0
        for alpha, coef in self.terms.items():
            term = coef
            for d, a in enumerate(alpha):
                if a:
                    term *= float(hermite_he(np.array([xi[d]]), a)[0])
            total += term
        return total

    def call_array(self, xi: np.ndarray) -> np.ndarray:
        xi = np.atleast_2d(xi)
        total = np.zeros(xi.shape[0])
        for alpha, coef in self.terms.items():
            term = np.full(xi.shape[0], coef)
            for d, a in enumerate(alpha):
                if a:
                    term = term * hermite_he(xi[:, d], a)
            total += term
        return total


def analytic_pce_of_polynomial(model: PolynomialModel) -> Dict[str, object]:
    """Closed-form PCE statistics + Sobol indices of an exact polynomial model."""
    variance = 0.0
    contrib: Dict[Tuple[int, ...], float] = {}
    for alpha, coef in model.terms.items():
        if sum(alpha) == 0:
            continue
        c = coef * coef * hermite_norm_sq(alpha)
        contrib[alpha] = c
        variance += c

    dim = model.dim
    first = [0.0] * dim
    total = [0.0] * dim
    if variance > 0.0:
        for alpha, c in contrib.items():
            support = [d for d in range(dim) if alpha[d] > 0]
            if len(support) == 1:
                first[support[0]] += c / variance
            for d in support:
                total[d] += c / variance

    return {
        "mean": float(model.terms.get(tuple([0] * dim), 0.0)),
        "variance": float(variance),
        "std": float(math.sqrt(variance)),
        "first_order": first,
        "total_order": total,
        "term_variance": {a: float(c) for a, c in contrib.items()},
    }


# ---------------------------------------------------------------------------
# A4 : independent (non-spectral) Sobol estimators
# ---------------------------------------------------------------------------
def legendre_saltelli_indices(
    model: Callable[..., np.ndarray],
    dim: int,
    n_base: int = 1 << 14,
    seed: int = 20261102,
    transform: Callable[[np.ndarray], np.ndarray] | None = None,
) -> Dict[str, object]:
    """First-order (Saltelli 2010) and total-order (Jansen 1999) indices by MC.

    Two independent sample matrices A, B ~ N(0,1)^d and the pick-and-freeze
    hybrids AB_i.  This estimator uses only the response values, never any
    polynomial basis, so it is independent of the audited PCE machinery.
    """
    rng = np.random.default_rng(seed)
    a = rng.standard_normal((n_base, dim))
    b = rng.standard_normal((n_base, dim))
    if transform is not None:
        a, b = transform(a), transform(b)

    f_a = np.asarray(model(a), dtype=float)
    f_b = np.asarray(model(b), dtype=float)
    var_y = float(np.var(np.concatenate([f_a, f_b])))
    mean_y = float(np.mean(np.concatenate([f_a, f_b])))

    first = np.zeros(dim)
    total = np.zeros(dim)
    for d in range(dim):
        ab = a.copy()
        ab[:, d] = b[:, d]
        f_ab = np.asarray(model(ab), dtype=float)
        # Saltelli 2010 first-order estimator (monomial estimator form)
        first[d] = float(np.mean(f_b * (f_ab - f_a)) / var_y) if var_y > 0 else 0.0
        # Jansen 1999 total-order estimator
        total[d] = float(0.5 * np.mean((f_a - f_ab) ** 2) / var_y) if var_y > 0 else 0.0

    return {
        "n_base": int(n_base),
        "n_model_evaluations": int(n_base * (dim + 2)),
        "mean": mean_y,
        "variance": var_y,
        "first_order": first.tolist(),
        "total_order": total.tolist(),
        "estimator": "first=Saltelli2010, total=Jansen1999",
        "seed": int(seed),
    }


def clipped_gaussian_reference(
    model: Callable[[np.ndarray], np.ndarray],
    mean_vec: Sequence[float],
    std_vec: Sequence[float],
    bounds: Sequence[Tuple[float, float]],
    n_samples: int = 200_000,
    seed: int = 20261102,
    max_attempts_factor: int = 20,
) -> Dict[str, float]:
    """Compare the clipped sampler used by the audited module with a rejection reference.

    The audited Monte-Carlo "reference" draws N(mean, std) marginals and then
    hard-clips them to the physical bounds.  Clipping piles probability mass on
    the boundary, so its statistics are not those of the model restricted to its
    physical domain.  The correct reference for the *declared* distribution
    truncated to the physical domain is rejection sampling, computed here.
    """
    rng = np.random.default_rng(seed)
    d = len(mean_vec)
    mean_vec = np.asarray(mean_vec, dtype=float)
    std_vec = np.asarray(std_vec, dtype=float)
    lo = np.array([b[0] for b in bounds], dtype=float)
    hi = np.array([b[1] for b in bounds], dtype=float)

    # (a) clipped draws -- reproduces the audited sampler
    xi = rng.standard_normal((n_samples, d))
    x_raw = mean_vec + std_vec * xi
    x_clip = np.clip(x_raw, lo, hi)
    y_clip = np.asarray(model(x_clip), dtype=float)
    moved = float(np.mean(np.any(x_clip != x_raw, axis=1)))

    # (b) rejection reference -- keep only draws already inside the domain
    accepted = []
    attempts = 0
    drawn = 0
    valid = 0
    limit = n_samples * max_attempts_factor
    while sum(len(a) for a in accepted) < n_samples and attempts < limit:
        block = mean_vec + std_vec * rng.standard_normal((n_samples, d))
        attempts += block.shape[0]
        drawn += block.shape[0]
        ok = np.all((block >= lo) & (block <= hi), axis=1)
        valid += int(np.sum(ok))
        if np.any(ok):
            accepted.append(block[ok])
    acc = np.vstack(accepted)[:n_samples] if accepted else np.zeros((0, d))
    y_acc = np.asarray(model(acc), dtype=float) if len(acc) else np.array([np.nan])
    acceptance = float(valid / max(drawn, 1))

    return {
        "n_samples": int(n_samples),
        "clip_fraction": moved,
        "acceptance_rate": acceptance,
        "mean_clipped": float(np.mean(y_clip)),
        "std_clipped": float(np.std(y_clip)),
        "mean_rejection": float(np.mean(y_acc)),
        "std_rejection": float(np.std(y_acc)),
        "mean_rel_bias": float((np.mean(y_clip) - np.mean(y_acc)) / np.mean(y_acc)),
        "std_rel_bias": float((np.std(y_clip) - np.std(y_acc)) / np.std(y_acc)),
    }


# ---------------------------------------------------------------------------
# A2 : independent 1-D rule check used to calibrate the Smolyak expectation
# ---------------------------------------------------------------------------
def smooth_1d_rule_check(nodes: np.ndarray, weights: np.ndarray) -> Dict[str, float]:
    """Diagnostics of a 1-D quadrature rule against analytic normal moments."""
    nodes = np.asarray(nodes, dtype=float)
    weights = np.asarray(weights, dtype=float)
    n = len(nodes)
    max_exact = -1
    for k in range(0, 2 * n + 2):
        approx = float(np.sum(weights * nodes ** k))
        if abs(approx - normal_moment(k)) <= 1e-10 * max(1.0, normal_moment(k)):
            max_exact = k
        else:
            break
    k_last = 2 * n - 1
    approx_last = float(np.sum(weights * nodes ** k_last))
    ref_last = normal_moment(k_last)
    rel_last = abs(approx_last - ref_last) / max(abs(ref_last), 1.0)
    return {
        "n_points": int(n),
        "weight_sum": float(np.sum(weights)),
        "weight_sum_err": float(abs(np.sum(weights) - 1.0)),
        "min_weight": float(np.min(weights)),
        "max_exact_degree": int(max_exact),
        "expected_exact_degree": int(k_last),
        "rel_err_at_expected_degree": float(rel_last),
    }


def monomial_moment_exactness(
    nodes: np.ndarray, weights: np.ndarray, dim: int, max_degree: int
) -> List[Dict[str, object]]:
    """Total-degree exactness table of a multivariate rule against A1.

    Only even total degree can detect an error, because every symmetric rule
    integrates odd monomials to zero exactly.  The reported ``detects`` flag
    makes that explicit so the table cannot be over-read.
    """
    rows: List[Dict[str, object]] = []
    nodes = np.asarray(nodes, dtype=float)
    weights = np.asarray(weights, dtype=float)
    for total in range(0, max_degree + 1):
        alpha = np.zeros(dim, dtype=int)
        alpha[0] = total
        approx = float(np.sum(weights * np.prod(nodes ** alpha, axis=1)))
        exact = normal_moment(total)
        err = abs(approx - exact)
        rows.append(
            {
                "total_degree": int(total),
                "approx": approx,
                "analytic": exact,
                "abs_err": err,
                "detects": bool(total % 2 == 0 and total > 0),
                "exact": bool(err <= 1e-10 * max(1.0, abs(exact))),
            }
        )
    return rows