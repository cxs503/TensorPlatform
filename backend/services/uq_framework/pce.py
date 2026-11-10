"""Generalised polynomial chaos over mixed marginals, with closed-form moments.

Design
------
Basis: per-input orthonormal polynomials of the *actual* marginal measure
(:mod:`.basis`, Hermite / Legendre / Stieltjes-triangular), multiplied into a
total-degree product basis.  Because the product basis is orthonormal under the
joint measure,

    E[psi_alpha] = delta_{alpha,0},     E[psi_alpha psi_beta] = delta_{alpha,beta},

the fitted coefficients give the moments in **closed form**:

    mean     = c_0
    variance = sum_{alpha != 0} c_alpha^2
    S_u      = (sum_{support(alpha)=u} c_alpha^2) / variance        (exact orders)
    S_{T_j}  = (sum_{j in support(alpha)} c_alpha^2) / variance

i.e. no resampling is needed to report mean/variance/Sobol' shares, and the
identities are machine-checkable against Monte Carlo -- which is exactly what
the framework's C2 criterion does.

Cost is counted in **model evaluations**: ``n_train + n_holdout``.  A hold-out
set is always spent (default 64) so the surrogate's adequacy ``R^2`` is
measured rather than assumed; an under-resolved order is therefore visible and
the framework refuses to publish its headline numbers (see
:mod:`.runner`, which falls back to Monte Carlo when ``R^2`` misses the gate).
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Sequence

import numpy as np

from .basis import MarginalBasis, basis_for
from .marginals import InputDistribution

__all__ = [
    "PCEResult",
    "total_degree_indices",
    "design_matrix",
    "fit_pce",
    "fit_pce_from_model",
    "latin_hypercube",
]


# --------------------------------------------------------------------------- #
# multi-index bookkeeping
# --------------------------------------------------------------------------- #
def total_degree_indices(d: int, order: int) -> np.ndarray:
    """All multi-indices with ``sum(alpha) <= order`` (degree 0 first)."""
    if d < 1:
        raise ValueError("dimension must be >= 1")
    if order < 0:
        raise ValueError("order must be >= 0")

    def rec(prefix: List[int], remaining: int, dims_left: int) -> None:
        if dims_left == 1:
            for k in range(remaining + 1):   # every degree 0..remaining
                out.append(prefix + [k])
            return
        for k in range(remaining + 1):
            rec(prefix + [k], remaining - k, dims_left - 1)

    out: List[List[int]] = []
    rec([], int(order), int(d))
    idx = np.array(out, dtype=int)
    deg = idx.sum(axis=1)
    keys = tuple(idx[:, j] for j in range(d - 1, -1, -1)) + (deg,)
    return idx[np.lexsort(keys)]


def design_matrix(X: np.ndarray, indices: np.ndarray,
                  bases: Sequence[MarginalBasis]) -> np.ndarray:
    """``A[i, a] = psi_{alpha_a}(X_i)`` for physical samples ``X``."""
    X = np.atleast_2d(np.asarray(X, dtype=float))
    n, d = X.shape
    if indices.ndim != 2 or indices.shape[1] != d:
        raise ValueError(f"indices must be (n_terms, {d}), got {indices.shape}")
    if len(bases) != d:
        raise ValueError("one basis per input dimension is required")
    A = np.ones((n, indices.shape[0]))
    for j in range(d):
        vals = bases[j].values(X[:, j], degree=int(indices[:, j].max()) if indices.size else 0)
        A *= vals[:, indices[:, j]]
    return A


def latin_hypercube(n: int, d: int, seed: int = 0) -> np.ndarray:
    """Stratified uniform design in the unit hypercube."""
    n, d = int(n), int(d)
    rng = np.random.default_rng(seed)
    u = np.empty((n, d))
    for j in range(d):
        u[:, j] = (rng.permutation(n) + rng.random(n)) / n
    return u


# --------------------------------------------------------------------------- #
# result
# --------------------------------------------------------------------------- #
@dataclass
class PCEResult:
    indices: np.ndarray
    coef: np.ndarray
    dists: Sequence[InputDistribution]
    order: int
    n_terms: int
    n_evals: int
    fit_seconds: float
    r2: float
    mean: float
    variance: float
    sobol_first: Dict[str, float]
    sobol_total: Dict[str, float]
    closure: float
    variance_ratio_holdout: float = float("nan")
    variance_ratio_se: float = float("nan")
    design: str = "lhs"
    seed: int = 0

    @property
    def names(self) -> List[str]:
        return [d.name for d in self.dists]

    @property
    def std(self) -> float:
        return float(np.sqrt(max(self.variance, 0.0)))

    @property
    def families(self) -> List[str]:
        return [d.family for d in self.dists]

    # -- surrogate evaluation ---------------------------------------------
    def _bases(self) -> List[MarginalBasis]:
        order = int(self.indices.max()) if self.indices.size else 0
        return [basis_for(d, order) for d in self.dists]

    def predict_unit(self, U: np.ndarray) -> np.ndarray:
        U = np.atleast_2d(np.asarray(U, dtype=float))
        X = np.column_stack([d.from_unit(U[:, j]) for j, d in enumerate(self.dists)])
        return self.predict_physical(X)

    def predict_physical(self, X: np.ndarray) -> np.ndarray:
        return design_matrix(X, self.indices, self._bases()) @ self.coef

    def sample_output(self, n: int = 20000, seed: int = 0, design: str = "lhs") -> np.ndarray:
        d = len(self.dists)
        U = latin_hypercube(n, d, seed) if design == "lhs" \
            else np.random.default_rng(seed).random((int(n), d))
        return self.predict_unit(U)

    def quantiles(self, probs: Sequence[float], n: int = 50000, seed: int = 0) -> Dict[str, float]:
        y = self.sample_output(n=n, seed=seed)
        return {f"p{100 * float(p):g}": float(np.quantile(y, p)) for p in probs}

    def sobol_to_dict(self) -> Dict[str, Any]:
        return {"first_order": dict(self.sobol_first), "total_order": dict(self.sobol_total),
                "closure": float(self.closure),
                "closure_meaning": "exact identity sum(alpha!=0)c^2/var = 1",
                "variance_ratio_holdout": float(self.variance_ratio_holdout),
                "variance_ratio_se": float(self.variance_ratio_se)}

    def as_dict(self) -> Dict[str, Any]:
        return {
            "method": "polynomial_chaos",
            "basis": "per_marginal_orthonormal(total_degree)",
            "families": self.families,
            "order": int(self.order),
            "n_terms": int(self.n_terms),
            "n_evals": int(self.n_evals),
            "fit_seconds": float(self.fit_seconds),
            "r2_holdout": float(self.r2),
            "design": self.design,
            "seed": int(self.seed),
            "mean": float(self.mean),
            "variance": float(self.variance),
            "std": self.std,
            "sobol": self.sobol_to_dict(),
            "moment_identities": "mean=c0, var=sum(c^2) (orthonormal basis, no resampling)",
            "adequacy_gate": "r2_holdout >= criteria.pce_r2_min",
        }


# --------------------------------------------------------------------------- #
# fitting
# --------------------------------------------------------------------------- #
def _evaluate(func: Callable[..., Any], dists: Sequence[InputDistribution],
              U: np.ndarray, call_style: str) -> np.ndarray:
    X = np.column_stack([m.from_unit(U[:, j]) for j, m in enumerate(dists)])
    if call_style == "array":
        y = func(X)
    elif call_style == "kwargs":
        y = func(**{m.name: X[:, j] for j, m in enumerate(dists)})
    else:
        raise ValueError(f"unknown call_style {call_style!r}")
    y = np.asarray(y, dtype=float).ravel()
    if y.size != U.shape[0]:
        raise ValueError(f"model returned {y.size} values for {U.shape[0]} samples")
    return y


def fit_pce(
    func: Callable[..., Any],
    dists: Sequence[InputDistribution],
    order: int = 3,
    n_train: int = 60,
    n_holdout: int = 64,
    seed: int = 0,
    call_style: str = "kwargs",
) -> PCEResult:
    """Least-squares chaos fit of a black-box model over mixed marginals."""
    if n_train < 2:
        raise ValueError("n_train must be >= 2")
    d = len(dists)
    t0 = time.perf_counter()
    indices = total_degree_indices(d, order)
    n_terms = indices.shape[0]
    if n_train < n_terms:
        raise ValueError(f"n_train={n_train} < n_terms={n_terms}; raise the budget")
    bases = [basis_for(m, order) for m in dists]

    U = latin_hypercube(n_train, d, seed)
    y = _evaluate(func, dists, U, call_style)
    X = np.column_stack([m.from_unit(U[:, j]) for j, m in enumerate(dists)])
    A = design_matrix(X, indices, bases)
    coef, *_ = np.linalg.lstsq(A, y, rcond=None)

    Uv = latin_hypercube(max(int(n_holdout), 8), d, seed + 1)
    yv = _evaluate(func, dists, Uv, call_style)
    Xv = np.column_stack([m.from_unit(Uv[:, j]) for j, m in enumerate(dists)])
    pred = design_matrix(Xv, indices, bases) @ coef
    sst = float(np.sum((yv - yv.mean()) ** 2))
    r2 = 1.0 - float(np.sum((yv - pred) ** 2)) / sst if sst > 0 else float("nan")

    c = np.asarray(coef, dtype=float)
    variance = float(np.sum(c[1:] ** 2))
    labels = [m.name for m in dists]
    support = indices > 0
    sobol_first: Dict[str, float] = {}
    sobol_total: Dict[str, float] = {}
    for j, lab in enumerate(labels):
        first_mask = support[:, j] & (support.sum(axis=1) == 1)
        total_mask = support[:, j]
        sobol_first[lab] = float(np.sum(c[first_mask] ** 2) / variance) if variance > 0 else 0.0
        sobol_total[lab] = float(np.sum(c[total_mask] ** 2) / variance) if variance > 0 else 0.0
    # Adequacy diagnostics.
    # ``sum_{alpha!=0} c^2 / Var`` is exactly 1 by construction (orthonormal
    # basis) and therefore *not* a test.  What is testable is (a) the pointwise
    # hold-out R^2 and (b) the ratio of the chaos variance to an independently
    # sampled variance.  The latter carries a sampling standard error
    # sqrt(2/n_holdout) on the *reference* variance, reported with it so a
    # mismatch is never over-interpreted.
    var_holdout = float(np.var(yv, ddof=1))
    variance_ratio = float(variance / var_holdout) if var_holdout > 0 else float("nan")
    n_h = int(max(int(n_holdout), 8))
    variance_ratio_se = float(np.sqrt(2.0 / max(n_h - 1, 1)))
    closure = 1.0 if variance > 0 else float("nan")   # exact by construction

    return PCEResult(
        indices=indices,
        coef=c,
        dists=list(dists),
        order=int(order),
        n_terms=int(n_terms),
        n_evals=int(n_train + max(int(n_holdout), 8)),
        fit_seconds=float(time.perf_counter() - t0),
        r2=float(r2),
        mean=float(c[0]),
        variance=variance,
        sobol_first=sobol_first,
        sobol_total=sobol_total,
        closure=closure,
        variance_ratio_holdout=variance_ratio,
        variance_ratio_se=variance_ratio_se,
        design="lhs",
        seed=int(seed),
    )


def fit_pce_from_model(
    func: Callable[[np.ndarray], np.ndarray],
    dists: Sequence[InputDistribution],
    order: int = 3,
    n_train: int = 60,
    n_holdout: int = 64,
    seed: int = 0,
) -> PCEResult:
    """Array-style convenience wrapper (``func(X) -> y`` with ``X`` physical)."""
    return fit_pce(func, dists, order=order, n_train=n_train, n_holdout=n_holdout,
                   seed=seed, call_style="array")