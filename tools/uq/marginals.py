"""``tools.uq.marginals`` -- input laws, orthonormal bases and quadrature.

This module is **domain free by construction**: it knows about probability laws
and polynomials, never about a physical quantity, a vehicle or a solver.  Any
domain content reaches the UQ engine only through the sample matrix / the
response vector that a caller injects.

Supported families
------------------
================  ====================  ===========================  ======================
family            parameters            standard variable            orthonormal basis
================  ====================  ===========================  ======================
``uniform``       ``lower``, ``upper``  ``z = 2(x-lo)/(hi-lo)-1``    Legendre ``P_n``
``normal``        ``mean``, ``std``     ``z = (x-mu)/sigma``         probabilists' ``He_n``
``beta``          ``a``, ``b``, bounds  ``z = 2(x-lo)/(hi-lo)-1``    Jacobi ``P_n^{(b-1,a-1)}``
================  ====================  ===========================  ======================

For every family the basis is orthonormal *with respect to the probability
measure*, i.e. ``E[phi_n phi_m] = delta_nm``.  That property is what makes the
PCE moments and ANOVA indices closed form (see :mod:`tools.uq.pce`), and it is
asserted numerically per family in the test suite.

The ``beta`` family is the extension this module adds over the pre-existing
``backend.services.pce_uq`` kernel (uniform / normal / lognormal only): a Beta
prior on a bounded interval maps to a Jacobi weight
``(1-z)^(b-1) (1+z)^(a-1)`` on ``[-1, 1]``, so one three-term machinery covers
all three families.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Dict, List, Sequence, Tuple

import numpy as np
from scipy.special import (eval_hermitenorm, eval_jacobi, eval_legendre, gammaln,
                           ndtri, roots_hermitenorm, roots_jacobi, roots_legendre)
from scipy.stats import beta as beta_dist

__all__ = [
    "Marginal",
    "FAMILIES",
    "sample_matrix",
    "to_standard_matrix",
    "orthonormal_matrix",
    "multi_index",
    "tensor_quadrature",
    "exact_moments",
]

FAMILIES: Tuple[str, ...] = ("uniform", "normal", "beta")


# --------------------------------------------------------------------------- #
# one marginal
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class Marginal:
    """One uncertain input law.

    ``family`` selects the parameterisation; unused fields stay ``nan``.
    ``name`` is only a reporting label (it carries no semantics).
    """

    name: str
    family: str
    lower: float = float("nan")
    upper: float = float("nan")
    mean: float = float("nan")
    std: float = float("nan")
    a: float = float("nan")
    b: float = float("nan")

    # -- construction -------------------------------------------------------
    @staticmethod
    def uniform(name: str, lower: float, upper: float) -> "Marginal":
        if not float(upper) > float(lower):
            raise ValueError(f"{name}: uniform upper must exceed lower")
        return Marginal(name=name, family="uniform", lower=float(lower), upper=float(upper))

    @staticmethod
    def normal(name: str, mean: float, std: float) -> "Marginal":
        if not float(std) > 0.0:
            raise ValueError(f"{name}: normal std must be positive")
        return Marginal(name=name, family="normal", mean=float(mean), std=float(std))

    @staticmethod
    def beta(name: str, a: float, b: float, lower: float = 0.0, upper: float = 1.0) -> "Marginal":
        if not (float(a) > 0.0 and float(b) > 0.0):
            raise ValueError(f"{name}: beta shape parameters must be positive")
        if not float(upper) > float(lower):
            raise ValueError(f"{name}: beta upper must exceed lower")
        return Marginal(name=name, family="beta", a=float(a), b=float(b),
                        lower=float(lower), upper=float(upper))

    @staticmethod
    def from_spec(spec: Dict[str, Any]) -> "Marginal":
        """Build from a JSON-friendly dict (the injection format of a case file)."""
        s = dict(spec)
        fam = str(s.pop("family", "uniform")).lower()
        name = str(s.pop("name", "x"))
        if fam == "uniform":
            return Marginal.uniform(name, s["lower"], s["upper"])
        if fam == "normal":
            return Marginal.normal(name, s["mean"], s["std"])
        if fam == "beta":
            return Marginal.beta(name, s["a"], s["b"], s.get("lower", 0.0), s.get("upper", 1.0))
        raise ValueError(f"{name}: unknown family {fam!r}; expected one of {FAMILIES}")

    # -- measure properties -------------------------------------------------
    def jacobi_params(self) -> Tuple[float, float]:
        """``(alpha, beta)`` of the Jacobi weight matching the Beta(a, b) law."""
        return float(self.b) - 1.0, float(self.a) - 1.0

    @property
    def analytic_mean(self) -> float:
        if self.family == "uniform":
            return 0.5 * (self.lower + self.upper)
        if self.family == "normal":
            return float(self.mean)
        return self.lower + (self.upper - self.lower) * self.a / (self.a + self.b)

    @property
    def analytic_std(self) -> float:
        if self.family == "uniform":
            return (self.upper - self.lower) / math.sqrt(12.0)
        if self.family == "normal":
            return float(self.std)
        var = self.a * self.b / ((self.a + self.b) ** 2 * (self.a + self.b + 1.0))
        return (self.upper - self.lower) * math.sqrt(var)

    # -- sampling / transform ----------------------------------------------
    def ppf(self, u: np.ndarray) -> np.ndarray:
        """Inverse CDF at uniform levels ``u`` in (0, 1)."""
        u = np.asarray(u, dtype=float)
        if self.family == "uniform":
            return self.lower + (self.upper - self.lower) * u
        if self.family == "normal":
            return self.mean + self.std * ndtri(u)
        return self.lower + (self.upper - self.lower) * beta_dist.ppf(u, self.a, self.b)

    def to_standard(self, x: np.ndarray) -> np.ndarray:
        """Physical value -> standard variable of the matching basis."""
        x = np.asarray(x, dtype=float)
        if self.family == "uniform":
            return 2.0 * (x - self.lower) / (self.upper - self.lower) - 1.0
        if self.family == "normal":
            return (x - self.mean) / self.std
        return 2.0 * (x - self.lower) / (self.upper - self.lower) - 1.0

    def from_standard(self, z: np.ndarray) -> np.ndarray:
        z = np.asarray(z, dtype=float)
        if self.family == "uniform":
            return self.lower + 0.5 * (self.upper - self.lower) * (z + 1.0)
        if self.family == "normal":
            return self.mean + self.std * z
        return self.lower + 0.5 * (self.upper - self.lower) * (z + 1.0)

    # -- orthonormal basis --------------------------------------------------
    def basis(self, z: np.ndarray, degree: int) -> np.ndarray:
        """``(len(z), degree+1)`` matrix of orthonormal basis values at ``z``."""
        z = np.asarray(z, dtype=float).ravel()
        out = np.empty((z.size, degree + 1), dtype=float)
        if self.family == "uniform":
            for n in range(degree + 1):
                out[:, n] = math.sqrt(2.0 * n + 1.0) * eval_legendre(n, z)
            return out
        if self.family == "normal":
            for n in range(degree + 1):
                out[:, n] = eval_hermitenorm(n, z) / math.sqrt(math.factorial(n))
            return out
        alpha, beta = self.jacobi_params()
        mass = _jacobi_mass(alpha, beta)
        for n in range(degree + 1):
            h = _jacobi_norm2(n, alpha, beta)
            out[:, n] = eval_jacobi(n, alpha, beta, z) / math.sqrt(h / mass)
        return out

    # -- quadrature ---------------------------------------------------------
    def quadrature(self, order: int) -> Tuple[np.ndarray, np.ndarray]:
        """Gauss rule exact up to polynomial degree ``2*order-1``; weights sum to 1."""
        if order < 1:
            raise ValueError("quadrature order must be >= 1")
        if self.family == "uniform":
            z, w = roots_legendre(order)
        elif self.family == "normal":
            z, w = roots_hermitenorm(order)
        else:
            alpha, beta = self.jacobi_params()
            z, w = roots_jacobi(order, alpha, beta)
        w = w / w.sum()
        return self.from_standard(z), w

    def detail(self) -> Dict[str, Any]:
        out: Dict[str, Any] = {"name": self.name, "family": self.family}
        if self.family == "uniform":
            out.update(lower=self.lower, upper=self.upper)
        elif self.family == "normal":
            out.update(mean=self.mean, std=self.std)
        else:
            out.update(a=self.a, b=self.b, lower=self.lower, upper=self.upper)
        out.update(analytic_mean=self.analytic_mean, analytic_std=self.analytic_std)
        return out


def _jacobi_mass(alpha: float, beta: float) -> float:
    """Integral of the Jacobi weight on [-1, 1]."""
    return float(math.exp((alpha + beta + 1.0) * math.log(2.0)
                          + gammaln(alpha + 1.0) + gammaln(beta + 1.0)
                          - gammaln(alpha + beta + 2.0)))


def _jacobi_norm2(n: int, alpha: float, beta: float) -> float:
    """Integral of ``(P_n^{(alpha,beta)})^2`` against the Jacobi weight.

    ``n = 0`` is the total mass (``P_0 = 1``); taking the general formula there
    divides by ``alpha + beta + 1``, which vanishes for e.g. Beta(1/2, 1/2).
    """
    if n == 0:
        return _jacobi_mass(alpha, beta)
    return float(math.exp((alpha + beta + 1.0) * math.log(2.0)
                          - math.log(2.0 * n + alpha + beta + 1.0)
                          + gammaln(n + alpha + 1.0) + gammaln(n + beta + 1.0)
                          - gammaln(n + alpha + beta + 1.0) - gammaln(n + 1.0)))


# --------------------------------------------------------------------------- #
# sample designs
# --------------------------------------------------------------------------- #
def sample_matrix(
    marginals: Sequence[Marginal],
    n_samples: int,
    seed: int = 0,
    method: str = "sobol",
) -> np.ndarray:
    """Draw ``n_samples`` physical points from the product measure.

    ``method='sobol'`` uses a scrambled Sobol' sequence (low discrepancy; the
    default for fitting), ``method='mc'`` uses pseudo-random draws (what a Monte
    Carlo reference should use).
    """
    marginals = list(marginals)
    d = len(marginals)
    n_samples = int(n_samples)
    if d < 1 or n_samples < 1:
        raise ValueError("need at least one marginal and one sample")
    if method == "sobol":
        from scipy.stats import qmc

        eng = qmc.Sobol(d=d, scramble=True, seed=int(seed))
        m = int(math.ceil(math.log2(max(n_samples, 2))))
        u = eng.random_base2(m)[:n_samples]
    elif method == "mc":
        rng = np.random.default_rng(int(seed))
        u = rng.random((n_samples, d))
    else:
        raise ValueError(f"unknown sampling method {method!r}")
    eps = np.finfo(float).eps
    u = np.clip(u, eps, 1.0 - eps)
    X = np.empty_like(u)
    for j, m in enumerate(marginals):
        X[:, j] = m.ppf(u[:, j])
    return X


def to_standard_matrix(X: np.ndarray, marginals: Sequence[Marginal]) -> np.ndarray:
    X = np.atleast_2d(np.asarray(X, dtype=float))
    marginals = list(marginals)
    if X.shape[1] != len(marginals):
        raise ValueError(f"{len(marginals)} marginals given for {X.shape[1]} columns")
    Z = np.empty_like(X)
    for j, m in enumerate(marginals):
        Z[:, j] = m.to_standard(X[:, j])
    return Z


# --------------------------------------------------------------------------- #
# multivariate basis
# --------------------------------------------------------------------------- #
def multi_index(dim: int, degree: int) -> np.ndarray:
    """All multi-indices with ``sum(alpha) <= degree``, total degree ascending.

    Row 0 is the zero index, so column 0 of the design matrix is the constant
    basis function and the PCE mean is ``c[0]``.
    """
    if dim < 1 or degree < 0:
        raise ValueError("dim >= 1 and degree >= 0 required")
    idx: List[Tuple[int, ...]] = []

    def rec(prefix: Tuple[int, ...], remaining: int, budget: int) -> None:
        if remaining == 0:
            idx.append(prefix)
            return
        for a in range(budget + 1):
            rec(prefix + (a,), remaining - 1, budget - a)

    rec((), int(dim), int(degree))
    idx.sort(key=lambda t: (sum(t), t))
    return np.asarray(idx, dtype=int)


def orthonormal_matrix(Z: np.ndarray, marginals: Sequence[Marginal], degree: int) -> np.ndarray:
    """Tensor-product orthonormal design matrix ``Psi[i,k] = prod_j phi_{a_kj}(z_ij)``."""
    Z = np.atleast_2d(np.asarray(Z, dtype=float))
    marginals = list(marginals)
    idx = multi_index(Z.shape[1], degree)
    tables = [m.basis(Z[:, j], degree) for j, m in enumerate(marginals)]
    Psi = np.ones((Z.shape[0], idx.shape[0]), dtype=float)
    for k, alpha in enumerate(idx):
        for j, a in enumerate(alpha):
            if a:
                Psi[:, k] *= tables[j][:, a]
    return Psi


# --------------------------------------------------------------------------- #
# exact moments by tensor quadrature (reference values, no sampling noise)
# --------------------------------------------------------------------------- #
def tensor_quadrature(
    marginals: Sequence[Marginal], order: int = 12
) -> Tuple[List[np.ndarray], np.ndarray]:
    """Full tensor Gauss rule for the product measure: one node array per dim + weights."""
    rules = [m.quadrature(order) for m in marginals]
    grids = np.meshgrid(*[r[0] for r in rules], indexing="ij")
    nodes = [g.reshape(-1) for g in grids]
    w = np.asarray([1.0])
    for r in rules:
        w = np.multiply.outer(w, r[1]).ravel()
    return nodes, w


def exact_moments(response, marginals: Sequence[Marginal], order: int = 12) -> Dict[str, Any]:
    """Mean / variance / quantiles of ``response`` by tensor Gauss quadrature.

    Exact to machine precision for responses polynomial of total degree
    ``<= 2*order-1``; a very accurate reference otherwise.  Used where a test
    function has no published closed form.
    """
    nodes, w = tensor_quadrature(marginals, order=order)
    Y = np.asarray([float(response([float(c) for c in pt])) for pt in zip(*nodes)], dtype=float)
    mu = float(np.sum(w * Y))
    var = float(np.sum(w * (Y - mu) ** 2))
    order_idx = np.argsort(Y, kind="stable")
    cdf = np.cumsum(w[order_idx])
    q = {}
    for p in (0.05, 0.5, 0.95):
        q[f"q{p:.2f}"] = float(Y[order_idx][min(int(np.searchsorted(cdf, p)), Y.size - 1)])
    return {"mean": mu, "variance": var, "std": math.sqrt(max(var, 0.0)),
            "quantiles": q, "source": f"tensor_gauss_order={order}"}