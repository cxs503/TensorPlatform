"""Non-intrusive polynomial chaos kernel: fit, spectral moments, Sobol' indices.

Two fitting routes, both non-intrusive (the model is only ever called as a black
box ``f(x) -> y``):

* **spectral projection** (:func:`fit_projection`) -- Gauss / Smolyak quadrature
  of ``c_alpha = sum_j w_j f(x_j) phi_alpha(z_j)``.  Exact to machine precision
  for polynomial responses inside the quadrature exactness degree, so it is the
  route used for verification-grade reference values.
* **regression** (:func:`fit_regression`) -- least squares on a Latin hypercube
  (or Sobol' sequence) design.  Cheaper for higher dimension at the price of a
  fitted (not exact) coefficient vector; quality is reported through a
  leave-one-out residual computed from the design matrix alone, so no extra
  model evaluations are hidden in the error metric.

Post-processing is closed form from the coefficients because the basis is
orthonormal (see :mod:`.basis`):

    ``E[y] = c_0``, ``Var[y] = sum_{alpha != 0} c_alpha^2``,
    ``S_i = sum_{supp(alpha)={i}} c_alpha^2 / Var``,
    ``S_T,i = sum_{i in supp(alpha)} c_alpha^2 / Var``.

The module is domain free: it takes a callable plus marginal laws and returns
statistics.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

import numpy as np

from .basis import design_matrix, multi_index_set, support
from .marginals import Marginal
from .quadrature import QuadratureGrid, build_grid, smolyak_grid, tensor_grid


# --------------------------------------------------------------------------- #
# model evaluation helper
# --------------------------------------------------------------------------- #
def evaluate_scalar_model(
    func: Callable[..., Any],
    X: np.ndarray,
    names: Sequence[str],
    output: Optional[str] = None,
    call_style: str = "kwargs",
    fixed: Optional[Dict[str, float]] = None,
) -> np.ndarray:
    """Call ``func(**{name: value})`` row by row and stack the responses.

    ``output`` selects an attribute from a returned object (e.g. a dataclass
    result); ``None`` means the callable already returns a scalar.  The scalar
    interface is deliberate: it is the interface every external solver exposes,
    and it keeps surrogate speed-up claims honest (both the Monte Carlo and the
    PCE pipeline pay the same per-evaluation cost).
    """
    X = np.atleast_2d(np.asarray(X, dtype=float))
    out = np.empty(X.shape[0], dtype=float)
    style = str(call_style).lower()
    extra = dict(fixed or {})
    for i in range(X.shape[0]):
        if style == "array":
            val = func(np.array([float(X[i, j]) for j in range(len(names))]))
        elif style == "kwargs":
            kwargs = {str(n): float(X[i, j]) for j, n in enumerate(names)}
            kwargs.update(extra)
            val = func(**kwargs)
        else:
            raise ValueError(f"unknown call_style {call_style!r}; expected kwargs/array")
        if output is not None:
            if hasattr(val, "as_dict"):
                val = val.as_dict()[output]
            elif hasattr(val, output):
                val = getattr(val, output)
            elif isinstance(val, dict):
                val = val[output]
            else:
                raise TypeError(f"cannot extract {output!r} from {type(val)!r}")
        arr = np.asarray(val, dtype=float).reshape(-1)
        out[i] = float(arr[0])
    return out


# --------------------------------------------------------------------------- #
# the surrogate
# --------------------------------------------------------------------------- #
@dataclass
class PCE:
    """Truncated polynomial chaos expansion in an orthonormal basis."""

    marginals: Tuple[Marginal, ...]
    indices: np.ndarray            # (n_terms, d)
    coef: np.ndarray               # (n_terms,)
    order: int
    q_norm: float
    method: str                   # "projection" | "regression"
    n_model_evaluations: int
    fit_seconds: float
    design: Dict[str, Any] = field(default_factory=dict)

    # -- evaluation ---------------------------------------------------------
    @property
    def families(self) -> Tuple[str, ...]:
        return tuple(m.basis_family for m in self.marginals)

    @property
    def names(self) -> Tuple[str, ...]:
        return tuple(m.name for m in self.marginals)

    def predict_standard(self, Z: np.ndarray) -> np.ndarray:
        A = design_matrix(self.indices, Z, self.families)
        return A @ self.coef

    def predict_physical(self, X: np.ndarray) -> np.ndarray:
        X = np.atleast_2d(np.asarray(X, dtype=float))
        Z = np.column_stack([m.to_standard(X[:, j]) for j, m in enumerate(self.marginals)])
        return self.predict_standard(Z)

    # -- spectral statistics ------------------------------------------------
    @property
    def mean(self) -> float:
        zero = np.all(self.indices == 0, axis=1)
        return float(np.sum(self.coef[zero]))

    @property
    def variance(self) -> float:
        zero = np.all(self.indices == 0, axis=1)
        return float(np.sum(self.coef[~zero] ** 2))

    @property
    def std(self) -> float:
        return float(np.sqrt(max(self.variance, 0.0)))

    def variance_components(self) -> Dict[str, Any]:
        """Per-group variance pieces used by the Sobol' decomposition."""
        d = self.indices.shape[1]
        zero = np.all(self.indices == 0, axis=1)
        coef = self.coef[~zero]
        idx = self.indices[~zero]
        total = float(np.sum(coef ** 2))
        first = np.zeros(d, dtype=float)
        tot = np.zeros(d, dtype=float)
        group_var: Dict[Tuple[int, ...], float] = {}
        for c, alpha in zip(coef, idx):
            sup = support(alpha)
            piece = float(c * c)
            group_var[sup] = group_var.get(sup, 0.0) + piece
            for j in sup:
                tot[j] += piece
            if len(sup) == 1:
                first[sup[0]] += piece
        return {
            "variance": total,
            "first_order_unnormalised": first,
            "total_order_unnormalised": tot,
            "group_variances": {",".join(str(j + 1) for j in k): v for k, v in sorted(group_var.items())},
        }

    def sobol(self) -> Dict[str, Any]:
        comp = self.variance_components()
        v = comp["variance"]
        first = comp["first_order_unnormalised"] / v if v > 0 else np.zeros_like(comp["first_order_unnormalised"])
        tot = comp["total_order_unnormalised"] / v if v > 0 else np.zeros_like(comp["total_order_unnormalised"])
        return {
            "first_order": {n: float(s) for n, s in zip(self.names, first)},
            "total_order": {n: float(s) for n, s in zip(self.names, tot)},
            "variance": float(v),
            "sum_first_order": float(np.sum(first)),
            "sum_total_order": float(np.sum(tot)),
        }

    # -- distribution side --------------------------------------------------
    def sample_output(self, n: int = 20000, seed: int = 0, method: str = "lhs") -> np.ndarray:
        """Draw from the surrogate (no model evaluations)."""
        U = latin_hypercube(n=n, d=len(self.marginals), seed=seed) if method == "lhs" \
            else np.random.default_rng(seed).random((int(n), len(self.marginals)))
        X = np.column_stack([m.from_unit(U[:, j]) for j, m in enumerate(self.marginals)])
        return self.predict_physical(X)

    def quantiles(self, probs: Sequence[float], n: int = 20000, seed: int = 0) -> Dict[str, float]:
        y = self.sample_output(n=n, seed=seed)
        return {f"p{100 * p:g}": float(np.quantile(y, p)) for p in probs}

    def as_dict(self) -> Dict[str, Any]:
        return {
            "method": self.method,
            "order": int(self.order),
            "q_norm": float(self.q_norm),
            "n_terms": int(self.indices.shape[0]),
            "n_model_evaluations": int(self.n_model_evaluations),
            "fit_seconds": float(self.fit_seconds),
            "families": list(self.families),
            "names": list(self.names),
            "mean": self.mean,
            "std": self.std,
            "design": self.design,
        }


# --------------------------------------------------------------------------- #
# sampling
# --------------------------------------------------------------------------- #
def latin_hypercube(n: int, d: int, seed: int = 0) -> np.ndarray:
    """Latin hypercube design on the unit hypercube, shape ``(n, d)``."""
    from scipy.stats import qmc

    n = int(n)
    d = int(d)
    sampler = qmc.LatinHypercube(d=d, scramble=True, seed=int(seed))
    return sampler.random(n)


def sobol_sequence(n: int, d: int, seed: int = 0) -> np.ndarray:
    """Scrambled Sobol' sequence on the unit hypercube (QMC alternative)."""
    from scipy.stats import qmc

    sampler = qmc.Sobol(d=int(d), scramble=True, seed=int(seed))
    m = int(np.ceil(np.log2(max(int(n), 2))))
    return sampler.random_base2(m)[: int(n)]


_UNIT_SAMPLERS = {"lhs": latin_hypercube, "qmc": sobol_sequence}


def make_unit_sample(n: int, d: int, method: str = "lhs", seed: int = 0) -> np.ndarray:
    key = str(method).lower()
    if key == "mc":
        return np.random.default_rng(int(seed)).random((int(n), int(d)))
    if key not in _UNIT_SAMPLERS:
        raise ValueError(f"unknown sampling method {method!r}; expected lhs/qmc/mc")
    return _UNIT_SAMPLERS[key](n=n, d=d, seed=seed)


# --------------------------------------------------------------------------- #
# fitting
# --------------------------------------------------------------------------- #
def _projection_grid(families: Sequence[str], order: int, level: Optional[int],
                     max_tensor_dim: int = 3) -> QuadratureGrid:
    d = len(families)
    if level is None:
        level = int(np.ceil((order + 1) / 2)) + 1
    if d <= int(max_tensor_dim):
        # (order+1) Gauss points per dim integrate the projection integrand
        # f * phi_alpha exactly for f up to total degree order+1.
        return tensor_grid(families, int(order) + 1)
    return smolyak_grid(families, int(level))


def fit_projection(
    func: Callable[..., Any],
    marginals: Sequence[Marginal],
    order: int,
    level: Optional[int] = None,
    output: Optional[str] = None,
    q_norm: float = 1.0,
    names: Optional[Sequence[str]] = None,
    call_style: str = "kwargs",
    fixed: Optional[Dict[str, float]] = None,
) -> PCE:
    """Non-intrusive spectral projection PCE (Gauss / Smolyak quadrature)."""
    marginals = tuple(marginals)
    names = names or tuple(m.name for m in marginals)
    families = tuple(m.basis_family for m in marginals)
    indices = multi_index_set(len(marginals), int(order), q_norm)
    t0 = time.perf_counter()
    grid = _projection_grid(families, int(order), level)
    X = grid.physical(marginals)
    Y = evaluate_scalar_model(func, X, names, output=output, call_style=call_style, fixed=fixed)
    A = design_matrix(indices, grid.points, families)
    coef = grid.weights @ (A * Y[:, None])
    dt = time.perf_counter() - t0
    return PCE(
        marginals=marginals, indices=indices, coef=np.asarray(coef, dtype=float),
        order=int(order), q_norm=float(q_norm), method="projection",
        n_model_evaluations=int(X.shape[0]), fit_seconds=float(dt),
        design={"quadrature": grid.as_dict()},
    )


def fit_regression(
    func: Callable[..., Any],
    marginals: Sequence[Marginal],
    order: int,
    n_samples: Optional[int] = None,
    method: str = "lhs",
    seed: int = 0,
    q_norm: float = 1.0,
    output: Optional[str] = None,
    names: Optional[Sequence[str]] = None,
    call_style: str = "kwargs",
    fixed: Optional[Dict[str, float]] = None,
) -> PCE:
    """Least-squares PCE on a space-filling design (LHS or Sobol' sequence).

    The design size defaults to twice the number of basis terms (rule-of-thumb
    oversampling for an over-determined fit); the leave-one-out residual is
    computed from the design matrix, so the reported error costs no extra model
    evaluations.
    """
    marginals = tuple(marginals)
    names = names or tuple(m.name for m in marginals)
    families = tuple(m.basis_family for m in marginals)
    indices = multi_index_set(len(marginals), int(order), q_norm)
    n_terms = int(indices.shape[0])
    if n_samples is None:
        n_samples = max(2 * n_terms, n_terms + 1)
    n_samples = int(n_samples)
    if n_samples < n_terms:
        raise ValueError(f"underdetermined fit: {n_samples} samples for {n_terms} terms")

    t0 = time.perf_counter()
    U = make_unit_sample(n_samples, len(marginals), method=method, seed=seed)
    X = np.column_stack([m.from_unit(U[:, j]) for j, m in enumerate(marginals)])
    Y = evaluate_scalar_model(func, X, names, output=output, call_style=call_style, fixed=fixed)
    Z = np.column_stack([m.to_standard(X[:, j]) for j, m in enumerate(marginals)])
    A = design_matrix(indices, Z, families)
    scale = np.linalg.norm(A, axis=0)
    scale[scale == 0.0] = 1.0
    As = A / scale
    coef_s, *_ = np.linalg.lstsq(As, Y, rcond=None)
    coef = coef_s / scale

    # leave-one-out residual from the hat matrix (no extra model evaluations)
    resid = Y - A @ coef
    try:
        # leverage from an orthonormal basis of the design columns (QR instead of
        # an explicit inverse: same quantity, O(n p^2) instead of O(p^3))
        Q, _ = np.linalg.qr(As)
        h = np.sum(Q ** 2, axis=1)
        loo = resid / np.clip(1.0 - h, 1e-12, None)
    except np.linalg.LinAlgError:  # pragma: no cover - defensive
        loo = resid
    denom = float(np.linalg.norm(Y)) or 1.0
    design = {
        "sampler": str(method),
        "seed": int(seed),
        "n_samples": n_samples,
        "condition_number": float(np.linalg.cond(As)),
        "loo_relative_residual": float(np.linalg.norm(loo) / denom),
        "train_relative_residual": float(np.linalg.norm(resid) / denom),
    }
    dt = time.perf_counter() - t0
    return PCE(
        marginals=marginals, indices=indices, coef=np.asarray(coef, dtype=float),
        order=int(order), q_norm=float(q_norm), method="regression",
        n_model_evaluations=n_samples, fit_seconds=float(dt), design=design,
    )


def fit(
    func: Callable[..., Any],
    marginals: Sequence[Marginal],
    order: int,
    method: str = "projection",
    **kwargs: Any,
) -> PCE:
    """Dispatch to :func:`fit_projection` or :func:`fit_regression`."""
    key = str(method).lower()
    if key == "projection":
        return fit_projection(func, marginals, order=order, **kwargs)
    if key in ("regression", "lstsq", "lhs"):
        return fit_regression(func, marginals, order=order, **kwargs)
    raise ValueError(f"unknown fit method {method!r}; expected projection/regression")