"""Self-verification fixtures: analytic references the framework is scored on.

These are deliberately *generic* numerical problems -- a linear/quadratic
response of uncertain inputs, a one-dimensional elliptic boundary-value problem
discretised to a known formal order, an exponential relaxation integrated by
schemes of order 1 and 4, and model ensembles whose truth is known -- so the
framework can be verified without touching any domain library.  Concrete
engineering cases are injected from the domain repositories through the same
public API.

Every reference below is an algebraic identity, not a calibrated number:

``linear_response``   mean = c0 + sum a_i mu_i,  variance = sum a_i^2 sigma_i^2,
                      first-order Sobol' share_i = a_i^2 sigma_i^2 / variance.
``quadratic_response`` ``y = x0*x1 + x2`` gives
                      mean = mu0 mu1 + mu2,
                      Var = (sigma0^2 + mu0^2)(sigma1^2 + mu1^2) - mu0^2 mu1^2
                            + sigma2^2  (independent inputs).
``diffusion_bvp``     ``-u'' = sin(pi x)`` on (0,1), ``u(0)=u(1)=0`` has
                      ``u = sin(pi x)/pi^2``; the three-point scheme is
                      second-order accurate, so the observed GCI order must
                      reproduce ``p = 2``.
``relaxation``        ``u' = -u, u(0)=1`` has ``u(1) = e^-1``; explicit Euler
                      converges at ``p = 1``, classical RK4 at ``p = 4`` -- two
                      independent checks that the GCI order estimator is not
                      hard-wired to 2.
"""

from __future__ import annotations

import math
from typing import Any, Callable, Dict, List, Sequence, Tuple

import numpy as np

from .marginals import InputDistribution

__all__ = [
    "raw_moments",
    "polynomial_response",
    "linear_response",
    "quadratic_response",
    "diffusion_bvp",
    "relaxation",
    "model_ensemble",
    "grid_triplet",
]


# --------------------------------------------------------------------------- #
# generic polynomial responses: exact moments by raw-moment algebra
# --------------------------------------------------------------------------- #
def raw_moments(dist: InputDistribution, n_max: int) -> np.ndarray:
    """``E[X^n]`` for ``n = 0..n_max`` of one marginal (closed form)."""
    from .basis import triangular_std_moments

    n_max = int(n_max)
    p = dist.params
    out = np.zeros(n_max + 1)
    if dist.family == "normal":
        mu, sd = float(p["mean"]), float(p["std"])
        for n in range(n_max + 1):
            tot = 0.0
            for k in range(0, n // 2 + 1):
                double_fact = 1.0
                for j in range(1, k + 1):
                    double_fact *= (2 * j - 1)
                tot += (math.comb(n, 2 * k) * double_fact * sd ** (2 * k)
                        * mu ** (n - 2 * k))
            out[n] = tot
        return out
    if dist.family == "uniform":
        a, b = float(p["lower"]), float(p["upper"])
        for n in range(n_max + 1):
            out[n] = (b ** (n + 1) - a ** (n + 1)) / ((n + 1) * (b - a))
        return out
    a, c, b = float(p["lower"]), float(p["mode"]), float(p["upper"])
    zmom = triangular_std_moments(a, c, b, n_max)
    # x = a + (b-a)(z+1)/2 ; expand in powers of u = z+1
    half = 0.5 * (b - a)
    umom = np.zeros(n_max + 1)
    for k in range(n_max + 1):
        umom[k] = sum(math.comb(k, j) * zmom[j] for j in range(k + 1))
    for n in range(n_max + 1):
        out[n] = sum(math.comb(n, k) * a ** (n - k) * half ** k * umom[k]
                     for k in range(n + 1))
    return out


def _monomial_expectation(exponents: Sequence[int], mom: Sequence[np.ndarray]) -> float:
    val = 1.0
    for j, e in enumerate(exponents):
        val *= mom[j][e]
    return float(val)


def polynomial_response(terms: Dict[Sequence[int], float],
                        names: Sequence[str],
                        intercept: float = 0.0,
                        ) -> Tuple[Callable[..., Any], Dict[str, Any]]:
    """``y = c0 + sum_a c_a prod_j x_j^{a_j}`` with exact moments and Sobol' shares.

    Independence of the inputs makes ``E[y]`` and ``E[y^2]`` products of
    one-dimensional raw moments, so the mean/variance are exact for *any*
    polynomial of any order -- an independent reference for the chaos fit rather
    than a re-derivation of it.
    """
    names = list(names)
    d = len(names)
    triples = [(tuple(int(e) for e in k), float(v)) for k, v in terms.items()]
    for expo, _ in triples:
        if len(expo) != d:
            raise ValueError(f"exponent {expo} does not match {d} inputs")
    max_deg = 2 * max([sum(e) for e, _ in triples] + [0])

    def func(**kw):
        total = np.full(np.shape(np.asarray(kw[names[0]], dtype=float)), float(intercept))
        for expo, coef in triples:
            term = coef
            for j, e in enumerate(expo):
                if e:
                    term = term * np.asarray(kw[names[j]], dtype=float) ** e
            total = total + term
        return total

    def reference(dists: Sequence[InputDistribution]) -> Dict[str, Any]:
        mom = [raw_moments(dd, max_deg) for dd in dists]
        mean = float(intercept)
        # E[y^2]: expand the square over all term pairs (plus intercept)
        e_y2 = intercept ** 2
        for expo, coef in triples:
            e_y2 += 2.0 * intercept * coef * _monomial_expectation(expo, mom)
            mean += coef * _monomial_expectation(expo, mom)
        for e1, c1 in triples:
            for e2, c2 in triples:
                e_y2 += c1 * c2 * _monomial_expectation(
                    [e1[j] + e2[j] for j in range(d)], mom)
        var = float(e_y2 - mean ** 2)
        # NOTE: exact Sobol' shares are *not* provided here.  The variance of a
        # sum of non-orthogonal monomials is not the sum of the term variances,
        # and a wrong sensitivity table is worse than none.  Shares are given
        # analytically only where the decomposition is elementary
        # (:func:`linear_response` -- additive, :func:`quadratic_response` --
        # one pure interaction plus an additive term); everywhere else they are
        # estimated (chaos coefficients / complement design) and cross-checked.
        return {"mean": mean, "variance": var, "std": float(np.sqrt(var)),
                "sobol_first": None, "sobol_total": None}

    return func, {"terms": triples, "intercept": float(intercept), "names": names,
                  "reference": reference}


# --------------------------------------------------------------------------- #
# analytic responses of uncertain inputs
# --------------------------------------------------------------------------- #
def linear_response(coef: Sequence[float],
                    intercept: float = 0.0) -> Tuple[Callable[..., float], Dict[str, Any]]:
    """``y = c0 + sum a_i x_i`` with closed-form moments and Sobol' shares."""
    a = [float(v) for v in coef]
    d = len(a)
    names = [f"x{j}" for j in range(d)]

    def func(**kw):
        # vectorised on purpose: the framework evaluates designs, not scalars
        return intercept + sum(a[j] * np.asarray(kw[names[j]], dtype=float) for j in range(d))

    def reference(dists: Sequence[InputDistribution]) -> Dict[str, Any]:
        mean = float(intercept + sum(a[j] * dists[j].mean for j in range(d)))
        var = float(sum(a[j] ** 2 * dists[j].variance for j in range(d)))
        return {
            "mean": mean,
            "std": float(np.sqrt(var)),
            "variance": var,
            "sobol_first": {dists[j].name: float(a[j] ** 2 * dists[j].variance / var)
                            for j in range(d)},
            # additive response: first-order and total-order coincide
            "sobol_total": {dists[j].name: float(a[j] ** 2 * dists[j].variance / var)
                            for j in range(d)},
        }

    return func, {"coef": a, "intercept": float(intercept), "reference": reference,
                  "names": names, "order": 1}


def quadratic_response(intercept: float = 0.0) -> Tuple[Callable[..., float], Dict[str, Any]]:
    """``y = c0 + x0*x1 + x2`` -- interaction term, closed-form moments."""
    def func(**kw):
        return intercept + np.asarray(kw["x0"] * kw["x1"] + kw["x2"], dtype=float)

    def reference(dists: Sequence[InputDistribution]) -> Dict[str, Any]:
        d0, d1, d2 = dists
        m0, m1, m2 = d0.mean, d1.mean, d2.mean
        s0, s1, s2 = d0.variance, d1.variance, d2.variance
        mean = float(intercept + m0 * m1 + m2)
        var_01 = float((s0 + m0 * m0) * (s1 + m1 * m1) - m0 * m0 * m1 * m1)
        var = var_01 + s2
        # y = x0*x1 + x2: the product term is a *pure interaction*, so the
        # first-order shares of x0/x1 are exactly zero and all of var_01 lives
        # in the total-order share (S_{01}).  Getting this distinction wrong is
        # the classic way a sensitivity table is silently mislabelled.
        return {"mean": mean, "std": float(np.sqrt(var)), "variance": float(var),
                "sobol_first": {"x0": 0.0, "x1": 0.0, "x2": float(s2 / var)},
                "sobol_total": {"x0": float(var_01 / var), "x1": float(var_01 / var),
                                "x2": float(s2 / var)}}

    return func, {"order": 2, "reference": reference, "names": ["x0", "x1", "x2"]}


# --------------------------------------------------------------------------- #
# discretised problems with known formal order
# --------------------------------------------------------------------------- #
def diffusion_bvp(n: int, dtype: Any = np.float64) -> Dict[str, Any]:
    """Three-point (second-order) solve of ``-u'' = sin(pi x)`` on ``n`` cells.

    Returns the mid-point value (a QoI with an exact reference ``1/pi^2``), the
    assembled matrix/right-hand side (for the residual gate) and the mesh width.
    """
    n = int(n)
    if n < 4 or n % 2:
        raise ValueError("n must be even and >= 4")
    dt = np.dtype(dtype)
    x = np.linspace(0.0, 1.0, n + 1, dtype=dt)
    h = 1.0 / n
    f = np.sin(np.pi * x[1:-1].astype(np.float64)).astype(dt)
    A = np.zeros((n - 1, n - 1), dtype=dt)
    idx = np.arange(n - 1)
    A[idx, idx] = 2.0 / h ** 2
    A[idx[:-1], idx[:-1] + 1] = -1.0 / h ** 2
    A[idx[1:], idx[1:] - 1] = -1.0 / h ** 2
    b = f
    u = np.linalg.solve(A, b)
    mid = n // 2 - 1
    return {
        "h": float(h),
        "u_mid": float(u[mid]),
        "exact_mid": float(1.0 / np.pi ** 2),
        "A": A, "b": b, "u": u, "x": x[1:-1],
        "qoi": float(u[mid]),
        "dtype": str(dt),
    }


def relaxation(n: int, scheme: str = "euler") -> Dict[str, Any]:
    """Integrate ``u' = -u, u(0)=1`` to ``x=1`` with a scheme of known order."""
    n = int(n)
    h = 1.0 / n
    y = 1.0
    if scheme == "euler":
        for _ in range(n):
            y += h * (-y)
        order = 1
    elif scheme == "rk4":
        for _ in range(n):
            k1 = -y
            k2 = -(y + 0.5 * h * k1)
            k3 = -(y + 0.5 * h * k2)
            k4 = -(y + h * k3)
            y += h / 6.0 * (k1 + 2 * k2 + 2 * k3 + k4)
        order = 4
    else:
        raise ValueError("scheme must be 'euler' or 'rk4'")
    return {"h": h, "qoi": float(y), "exact": float(np.exp(-1.0)), "scheme": scheme,
            "formal_order": order}


def grid_triplet(h_fine: float, ratio: float = 2.0, n_levels: int = 3) -> List[int]:
    """Cell counts of three successively coarser grids (``ratio`` per level)."""
    counts = []
    n = int(round(1.0 / float(h_fine)))
    for _ in range(n_levels):
        counts.append(n)
        n = int(round(n / float(ratio)))
    return counts


# --------------------------------------------------------------------------- #
# model ensembles with known truth
# --------------------------------------------------------------------------- #
def model_ensemble(kind: str,
                   meas_error: float = 0.005,
                   seed: int = 0) -> Dict[str, Any]:
    """Model ensembles whose truth is known, for model-form UQ verification.

    ``kind="affine"``            truth plus scaled/offset variants (bias only);
    ``kind="truncation"``        one exact model, one missing a term, one wrong
                                 term -- the case BMA must resolve;
    ``kind="competing_biases"``  opposing offsets, no correct model -- averaging
                                 must beat the best single model;
    ``kind="uniform_bias"``      every model biased the same way (model-form
                                 spread small, bias large: the ensemble cannot
                                 detect it, which is the honest counter-example).
    """
    if kind == "affine":
        def truth(x0: np.ndarray, x1: np.ndarray):
            return 1.0 + 2.0 * x0 + 3.0 * x1

        models = {
            "exact": lambda x0, x1: 1.0 + 2.0 * x0 + 3.0 * x1,
            "scaled": lambda x0, x1: 1.04 * (1.0 + 2.0 * x0 + 3.0 * x1),
            "offset": lambda x0, x1: 1.06 + 2.0 * x0 + 3.0 * x1,
            "half_credit": lambda x0, x1: 1.0 + 1.8 * x0 + 2.7 * x1,
        }
    elif kind == "truncation":
        def truth(x0: np.ndarray, x1: np.ndarray, x2: np.ndarray):
            return x0 * x0 + 0.5 * x0 * x1 + x2

        models = {
            "exact": lambda x0, x1, x2: x0 ** 2 + 0.5 * x0 * x1 + x2,
            "no_interaction": lambda x0, x1, x2: x0 ** 2 + x2,
            "wrong_term": lambda x0, x1, x2: x0 * x1 + x2,
            "linearised": lambda x0, x1, x2: (1.0 + 2.0 * x0) + x2,
        }
    elif kind == "competing_biases":
        # no model is right, and the biases point in *opposite* directions: the
        # ensemble mean must then be better than the best single model, which is
        # the only claim that justifies averaging at all
        def truth(x0: np.ndarray, x1: np.ndarray):
            return 0.5 + 1.5 * x0 + 2.5 * x1

        models = {
            "plus_offset": lambda x0, x1: 0.58 + 1.5 * x0 + 2.5 * x1,
            "minus_offset": lambda x0, x1: 0.40 + 1.5 * x0 + 2.5 * x1,
            "scaled": lambda x0, x1: 1.10 * (0.5 + 1.5 * x0 + 2.5 * x1),
        }
    elif kind == "uniform_bias":
        def truth(x0: np.ndarray, x1: np.ndarray):
            return 1.0 + 2.0 * x0 + 3.0 * x1

        models = {
            "a": lambda x0, x1: 1.08 * (1.0 + 2.0 * x0 + 3.0 * x1),
            "b": lambda x0, x1: 1.08 + 2.0 * x0 + 3.0 * x1,
            "c": lambda x0, x1: 1.0 + 2.08 * x0 + 3.08 * x1,
        }
    else:
        raise ValueError(f"unknown ensemble kind {kind!r}")

    rng = np.random.default_rng(seed)
    vx = rng.random((24, max(1, 2)))  # validation design in the unit box
    return {"truth": truth, "models": models, "kind": kind,
            "meas_error": float(meas_error), "validation_unit": vx, "seed": int(seed)}