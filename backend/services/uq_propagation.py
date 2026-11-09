"""Forward uncertainty propagation, quantile confidence intervals, Sobol'
closure decomposition and error-propagation-chain diagnostics.

Why this module exists
----------------------
The platform already owned two *pieces* of an uncertainty capability:

* ``backend/services/global_sensitivity.py`` -- Saltelli pick-freeze Sobol'
  indices for a black-box ``f(x) -> y`` (first and total order only);
* ``backend/services/ice_resistance_uq.py`` -- Gaussian polynomial chaos on a
  Smolyak grid, tied to one particular response family.

What was missing, and what this module adds, is the *propagation chain* around
those pieces: given uncertain inputs, what is the 95 % interval of the output,
which input dominates, how does the error grow through the intermediate
stages, and how stable is the map?  All of it domain-free machinery:

1. **Input model** (:class:`InputSpec`): normal / lognormal / uniform with
   optional truncation at ``k`` standard deviations, so the physical box used
   by the variance decomposition is explicit and finite.
2. **Designs** (:func:`sample_inputs`): plain Monte Carlo, scrambled Sobol'
   quasi-Monte Carlo, and Latin hypercube, all with inverse-CDF mapping onto
   the physical marginals (SciPy's QMC engines are used when present).
3. **Output statistics with intervals** (:func:`output_uncertainty`): mean,
   standard deviation and arbitrary quantiles, each with *two independent*
   interval estimators -- bootstrap and the distribution-free order-statistic
   (binomial) interval of :func:`quantile_ci_order_statistic`.
4. **Independent-replicate scatter** (:func:`replicate_ci_width`) -- the honest
   yardstick for "is the reported interval the right width?``: repeated
   independent runs give an empirical interval width that a new estimator must
   reproduce.
5. **Surrogate-accelerated propagation** (:func:`accelerated_uncertainty`): fit
   a total-degree polynomial surrogate on a QMC design, validate it on a
   held-out set, then propagate a large sample through the surrogate.  Both the
   model-evaluation speed-up and the matched-budget QMC variance reduction are
   reported, so the acceleration claim never rests on wall-clock alone.
6. **Sobol' closure decomposition** (:func:`sobol_hdmr`): the *complement
   design* estimator ``G_u = E[y_A y_{M_u}] - f0^2`` with ``M_u`` equal to
   ``A`` in the coordinates of ``u`` and ``B`` elsewhere, Möbius-inverted into
   exact-order variance shares ``V_u`` for ``|u| <= max_order``.  Since the
   Sobol'-Hoeffding decomposition satisfies ``sum_u S_u = 1`` exactly, the
   *closure* ``sum_{1<=|u|<=max_order} S_u`` is a direct machine-checkable test
   of (a) estimator consistency and (b) whether the response is quasi-additive
   (closure ~ 1) or dominated by higher-order interaction (closure < 1, and the
   deficit *is* the higher-order share).
7. **Error-propagation chain** (:func:`propagation_chain`): composed-map
   Jacobian by central differences, the *scaled* (dimensionless) Jacobian and
   its condition number, per-stage local condition number and amplification,
   first-order (delta-method) variance against the Monte-Carlo variance
   (linearisation validity), and per-input variance contributions -- exactly
   the data needed to draw the propagation path.
8. **Figures** (:func:`figure_confidence_intervals`, :func:`figure_sensitivity`,
   :func:`figure_propagation_path`, :func:`figure_convergence`): generic
   matplotlib renderers whose labels all come from the caller.

Numerical anchors
-----------------
Every estimator here is scored in ``tests/test_uq_propagation.py`` against a
closed form, so nothing in the module is trusted merely because it runs:

* additive ``g``-function: ``S_i = S_T_i`` and ``sum S_i = 1`` (closure = 1);
* multiplicative ``g``-function: analytic ``V_u`` of *every* order from
  ``V = prod(1 + v_i) - 1``, used to score the Möbius inversion including the
  second-order terms;
* Ishigami & Homma (1990) for the ``S1 = 0 / ST = 0.24`` case;
* a linear Gaussian chain whose Jacobian and condition number are exact by
  construction, plus a deliberately ill-conditioned one;
* a coverage experiment: the nominal 95 % quantile interval must contain the
  true quantile in ~95 % of independent replications.

References
----------
Saltelli et al. (2010) CPC 181, 259-270 (pick-freeze estimators).
Sobol' (1993) Math. Model. Comput. Exp. 1, 407-414 (decomposition).
Homma & Saltelli (1996) Reliab. Eng. Syst. Saf. 52, 1-17 (closed indices and
    the complementary matrices used for the higher-order terms).
Ishigami & Homma (1990) Proc. 1st ISUMA, 398-403.
Caflisch (1998) Acta Numerica 7, 1-49 (QMC convergence).
Efron & Tibshirani (1993) *An Introduction to the Bootstrap* (percentile CI).
Hahn & Meeker (1991) *Statistical Intervals* (distribution-free quantile CI).
"""

from __future__ import annotations

import math
import time
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

import numpy as np

__all__ = [
    "InputSpec",
    "sample_inputs",
    "evaluate_model",
    "quantile_ci_order_statistic",
    "bootstrap_quantile_ci",
    "output_uncertainty",
    "replicate_ci_width",
    "PolySurrogate",
    "fit_poly_surrogate",
    "accelerated_uncertainty",
    "qmc_variance_reduction",
    "sobol_hdmr",
    "sobol_hdmr_replicated",
    "jacobian",
    "ChainStage",
    "propagation_chain",
    "figure_confidence_intervals",
    "figure_sensitivity",
    "figure_propagation_path",
    "figure_convergence",
]

_SPEC_KINDS = ("normal", "lognormal", "uniform")


# ---------------------------------------------------------------------------
# 1. Input model
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class InputSpec:
    """One uncertain input.

    Parameters
    ----------
    name : str
        Label used in every returned dictionary and in the figures.
    kind : {"normal", "lognormal", "uniform"}
        ``normal`` / ``lognormal`` are parameterised by their *physical* mean
        and standard deviation; ``uniform`` by ``(low, high)``.
    mean, std : float
        Physical moments (``normal``, ``lognormal``).
    low, high : float
        Support bounds (``uniform``); for the other kinds they follow from
        ``truncate_sigma``.
    truncate_sigma : float
        Keep the distribution inside ``mean +- truncate_sigma * std`` (physical
        space).  Truncation keeps the decomposition box finite, which the
        variance decomposition needs; the mass cut is reported by
        :meth:`diagnostics` so it is never silent.
    """

    name: str
    kind: str
    mean: float = float("nan")
    std: float = float("nan")
    low: float = float("nan")
    high: float = float("nan")
    truncate_sigma: float = 4.0

    def __post_init__(self) -> None:
        if self.kind not in _SPEC_KINDS:
            raise ValueError(f"kind must be one of {_SPEC_KINDS}, got {self.kind!r}")
        if not isinstance(self.name, str) or not self.name:
            raise ValueError("name must be a non-empty string")
        if self.kind == "uniform":
            if not (np.isfinite(self.low) and np.isfinite(self.high)):
                raise ValueError(f"{self.name}: uniform needs finite low/high")
            if self.high <= self.low:
                raise ValueError(f"{self.name}: uniform needs high > low")
            return
        if not (np.isfinite(self.mean) and np.isfinite(self.std)):
            raise ValueError(f"{self.name}: {self.kind} needs finite mean/std")
        if self.std <= 0.0:
            raise ValueError(f"{self.name}: std must be > 0")
        if self.kind == "lognormal" and self.mean <= 0.0:
            raise ValueError(f"{self.name}: lognormal needs mean > 0")
        if self.truncate_sigma <= 0.0:
            raise ValueError(f"{self.name}: truncate_sigma must be > 0")

    # -- derived quantities -------------------------------------------------
    @property
    def physical_bounds(self) -> Tuple[float, float]:
        """Finite box used by the sampling designs and the decomposition."""
        if self.kind == "uniform":
            return float(self.low), float(self.high)
        if self.kind == "normal":
            k = float(self.truncate_sigma)
            return float(self.mean - k * self.std), float(self.mean + k * self.std)
        mu, sig = self._log_moments()
        k = float(self.truncate_sigma)
        return float(math.exp(mu - k * sig)), float(math.exp(mu + k * sig))

    def _log_moments(self) -> Tuple[float, float]:
        cov2 = (self.std / self.mean) ** 2
        sig2 = math.log1p(cov2)
        return math.log(self.mean) - 0.5 * sig2, math.sqrt(sig2)

    def to_uniform(self, u: np.ndarray) -> np.ndarray:
        """Map uniforms on ``[0, 1]`` onto this input's (truncated) law."""
        u = np.asarray(u, dtype=float)
        lo, hi = self.physical_bounds
        if self.kind == "uniform":
            return lo + u * (hi - lo)
        from scipy.stats import norm

        if self.kind == "normal":
            c_lo = norm.cdf((lo - self.mean) / self.std)
            c_hi = norm.cdf((hi - self.mean) / self.std)
            return self.mean + self.std * norm.ppf(c_lo + u * (c_hi - c_lo))
        mu, sig = self._log_moments()
        c_lo = norm.cdf((math.log(lo) - mu) / sig)
        c_hi = norm.cdf((math.log(hi) - mu) / sig)
        return np.exp(mu + sig * norm.ppf(c_lo + u * (c_hi - c_lo)))

    def moments(self) -> Tuple[float, float, float, float]:
        """``(mean, std, low, high)`` of the *truncated* distribution.

        Quadrature on the probability space, so the sigma used to scale the
        Jacobian is the one that is actually sampled.
        """
        nodes, weights = np.polynomial.legendre.leggauss(64)
        u = 0.5 * (nodes + 1.0)
        w = 0.5 * weights
        x = self.to_uniform(u)
        m = float(np.sum(w * x))
        var = float(np.sum(w * (x - m) ** 2))
        lo, hi = self.physical_bounds
        return m, math.sqrt(max(var, 0.0)), lo, hi

    def diagnostics(self) -> Dict[str, Any]:
        lo, hi = self.physical_bounds
        m, s, _, _ = self.moments()
        if self.kind == "uniform":
            tail = 0.0
            nominal_mean, nominal_std = 0.5 * (lo + hi), (hi - lo) / math.sqrt(12.0)
        else:
            from scipy.stats import norm

            k = float(self.truncate_sigma)
            tail = float(2.0 * norm.sf(k)) if math.isfinite(k) else 0.0
            nominal_mean, nominal_std = float(self.mean), float(self.std)
        return {
            "name": self.name,
            "kind": self.kind,
            "nominal_mean": nominal_mean,
            "nominal_std": nominal_std,
            "physical_low": lo,
            "physical_high": hi,
            "truncated_mean": m,
            "truncated_std": s,
            "truncated_mass": 1.0 - tail,
            "mass_cut": tail,
        }


def _validate_specs(specs: Sequence[InputSpec]) -> List[InputSpec]:
    specs = list(specs)
    if not specs:
        raise ValueError("at least one InputSpec is required")
    names = [s.name for s in specs]
    if len(set(names)) != len(names):
        raise ValueError("InputSpec names must be unique")
    return specs


def _latin_hypercube(rng: np.random.Generator, n: int, d: int) -> np.ndarray:
    """Latin hypercube on ``[0,1]^d`` (deterministic fallback for QMC)."""
    u = np.empty((n, d), dtype=float)
    for j in range(d):
        strata = (np.arange(n) + rng.random(n)) / n
        u[:, j] = rng.permutation(strata)
    return u


def sample_inputs(
    specs: Sequence[InputSpec],
    n: int,
    method: str = "mc",
    seed: int = 0,
    skip: int = 0,
) -> np.ndarray:
    """Draw ``n`` physical-space samples, shape ``(n, d)``.

    ``method="mc"``  : independent pseudo-random uniforms (``skip`` rows burned);
    ``method="qmc"`` : scrambled Sobol' net through the inverse CDF;
    ``method="lhs"`` : Latin hypercube.
    """
    specs = _validate_specs(specs)
    n = int(n)
    if n < 2:
        raise ValueError("n must be >= 2")
    d = len(specs)
    method = str(method).lower()
    if method not in ("mc", "qmc", "lhs"):
        raise ValueError("method must be 'mc', 'qmc' or 'lhs'")
    rng = np.random.default_rng(int(seed))
    if method == "mc":
        u = rng.random((n + int(skip), d))[int(skip):]
    elif method == "lhs":
        u = _latin_hypercube(rng, n, d)
    else:
        try:
            from scipy.stats import qmc

            engine = qmc.Sobol(d=d, scramble=True, seed=int(seed))
            m = 1
            while m < n:
                m *= 2
            u = engine.random_base2(int(round(math.log2(m))))[:n]
        except Exception:  # noqa: BLE001 - SciPy QMC missing: fall back
            u = _latin_hypercube(rng, n, d)
    return np.column_stack([specs[j].to_uniform(u[:, j]) for j in range(d)])


# ---------------------------------------------------------------------------
# 2. Model evaluation
# ---------------------------------------------------------------------------


def evaluate_model(func: Callable[..., Any], X: np.ndarray) -> np.ndarray:
    """Evaluate ``func`` on the rows of ``X`` -> ``(n,)`` or ``(n, m)`` array.

    A vectorised call ``func(X)`` is attempted first; a scalar-only callable is
    driven row by row.  Non-finite outputs raise instead of silently poisoning
    the statistics.
    """
    X = np.asarray(X, dtype=float)
    if X.ndim != 2:
        raise ValueError("X must be 2-D (n_samples, n_inputs)")
    n = X.shape[0]
    out: Optional[np.ndarray] = None
    try:
        arr = np.asarray(func(X), dtype=float)
        if arr.ndim >= 1 and arr.shape[0] == n:
            out = arr
    except Exception:  # noqa: BLE001 - fall back to the scalar signature
        out = None
    if out is None:
        rows = [np.asarray(func(X[j]), dtype=float).reshape(1, -1) for j in range(n)]
        out = np.vstack(rows)
        out = out[:, 0] if out.shape[1] == 1 else out
    if not np.all(np.isfinite(out)):
        k = int(np.sum(~np.isfinite(out)))
        raise ValueError(f"model produced {k} non-finite output value(s)")
    return np.asarray(out, dtype=float)


# ---------------------------------------------------------------------------
# 3. Quantiles and their intervals
# ---------------------------------------------------------------------------


def quantile_ci_order_statistic(
    values: Sequence[float], q: float = 0.95, confidence: float = 0.95
) -> Dict[str, Any]:
    """Distribution-free confidence interval for the ``q``-quantile.

    Exact binomial property: for i.i.d. samples the order-statistic interval
    ``[x_(l), x_(u)]`` brackets the true quantile with probability
    ``sum_{k=l}^{u-1} C(n,k) q^k (1-q)^{n-k}`` (Hahn & Meeker, sec. 5.3).
    Returns ``{estimate, ci_low, ci_high, l, u, coverage, method}``.
    """
    x = np.sort(np.asarray(values, dtype=float).ravel())
    n = x.size
    if n < 10:
        raise ValueError("n must be >= 10 for a quantile interval")
    if not (0.0 < q < 1.0):
        raise ValueError("q must be in (0, 1)")
    if not (0.0 < confidence < 1.0):
        raise ValueError("confidence must be in (0, 1)")
    alpha = 1.0 - confidence
    from scipy.stats import binom

    k = np.arange(n + 1)
    pmf = binom.pmf(k, n, q)
    cdf = np.cumsum(pmf)
    # smallest l (1-indexed) with P(Bin <= l-1) <= alpha/2
    l = int(np.searchsorted(cdf[:-1], alpha / 2.0, side="right")) + 1
    l = max(1, min(l, n))
    # largest u with P(Bin >= u) <= alpha/2
    surv_after = 1.0 - cdf            # surv_after[i] = P(Bin >= i+1)
    cand = np.where(surv_after <= alpha / 2.0 + 1e-15)[0]
    u = int(cand[0]) + 1 if cand.size else n
    u = max(1, min(u, n))
    if u < l:
        l, u = max(1, n // 2), min(n, n // 2 + 1)
    coverage = float(np.sum(pmf[l - 1:u]))
    estimate = float(np.quantile(x, q))
    if n * q * (1.0 - q) < 5.0:
        half = float(x[-1] - x[0]) / (2.0 * math.sqrt(n))
        return {
            "estimate": estimate,
            "ci_low": estimate - half,
            "ci_high": estimate + half,
            "l": 1,
            "u": n,
            "coverage": coverage,
            "method": "plugin-small-n",
        }
    return {
        "estimate": estimate,
        "ci_low": float(x[l - 1]),
        "ci_high": float(x[u - 1]),
        "l": int(l),
        "u": int(u),
        "coverage": coverage,
        "method": "order-statistic",
    }


def bootstrap_quantile_ci(
    values: Sequence[float],
    q: float = 0.95,
    confidence: float = 0.95,
    n_boot: int = 2000,
    seed: int = 0,
) -> Dict[str, Any]:
    """Percentile bootstrap interval of the ``q``-quantile (Efron & Tibshirani)."""
    y = np.asarray(values, dtype=float).ravel()
    n = y.size
    if n < 10:
        raise ValueError("n must be >= 10 for a bootstrap interval")
    rng = np.random.default_rng(int(seed))
    idx = rng.integers(0, n, size=(int(n_boot), n))
    stats = np.quantile(y[idx], q, axis=1)
    alpha = 1.0 - float(confidence)
    return {
        "estimate": float(np.quantile(y, q)),
        "ci_low": float(np.quantile(stats, alpha / 2.0)),
        "ci_high": float(np.quantile(stats, 1.0 - alpha / 2.0)),
        "bootstrap_std": float(np.std(stats, ddof=1)),
        "method": "bootstrap-percentile",
        "n_boot": int(n_boot),
    }


def _as_columns(y: np.ndarray) -> Tuple[np.ndarray, List[str]]:
    y = np.asarray(y, dtype=float)
    if y.ndim == 1:
        return y.reshape(-1, 1), ["y"]
    return y, [f"y{j}" for j in range(y.shape[1])]


def _skew(y: np.ndarray) -> float:
    s = float(np.std(y))
    if s == 0.0:
        return 0.0
    return float(np.mean(((y - np.mean(y)) / s) ** 3))


def _norm_ppf(p: float) -> float:
    from scipy.stats import norm

    return float(norm.ppf(p))


def output_uncertainty(
    func: Callable[..., Any],
    specs: Sequence[InputSpec],
    n: int = 20000,
    quantiles: Sequence[float] = (0.05, 0.5, 0.95),
    n_boot: int = 2000,
    confidence: float = 0.95,
    seed: int = 0,
    method: str = "mc",
    out_names: Optional[Sequence[str]] = None,
) -> Dict[str, Any]:
    """Forward propagation with intervals for the mean *and* every quantile.

    Returns a JSON-ready dict: per output column, mean/std with a normal-theory
    interval for the mean, plus every requested quantile carrying both a
    bootstrap and a distribution-free interval.  ``n_evals`` / ``wall_s`` make
    the cost of the estimator explicit (the acceleration study uses them).
    """
    specs = _validate_specs(specs)
    t0 = time.perf_counter()
    X = sample_inputs(specs, n=n, method=method, seed=seed)
    Y = evaluate_model(func, X)
    wall = time.perf_counter() - t0
    Yc, names = _as_columns(Y)
    if out_names is not None:
        names = list(out_names)
        if len(names) != Yc.shape[1]:
            raise ValueError("out_names must match the number of output columns")
    n = Yc.shape[0]
    z = _norm_ppf(0.5 + 0.5 * confidence)
    cols: Dict[str, Any] = {}
    for j, nm in enumerate(names):
        y = Yc[:, j]
        mean = float(np.mean(y))
        sd = float(np.std(y, ddof=1))
        se = sd / math.sqrt(n)
        q_entries: Dict[str, Any] = {}
        for q in quantiles:
            boot = bootstrap_quantile_ci(y, q=q, confidence=confidence, n_boot=n_boot, seed=seed + 17)
            order = quantile_ci_order_statistic(y, q=q, confidence=confidence)
            q_entries[f"{q:g}"] = {
                "estimate": float(np.quantile(y, q)),
                "bootstrap": {k: boot[k] for k in ("ci_low", "ci_high", "bootstrap_std", "method")},
                "order_statistic": order,
                "width_bootstrap": float(boot["ci_high"] - boot["ci_low"]),
                "width_order_statistic": float(order["ci_high"] - order["ci_low"]),
            }
        cols[nm] = {
            "n_samples": int(n),
            "mean": mean,
            "std": sd,
            "std_error_mean": float(se),
            "ci_mean": [mean - z * se, mean + z * se],
            "ci_mean_width": float(2.0 * z * se),
            "min": float(np.min(y)),
            "max": float(np.max(y)),
            "quantiles": q_entries,
            "skewness": _skew(y),
        }
    return {
        "method": method,
        "seed": int(seed),
        "n_evals": int(n),
        "wall_s": float(wall),
        "columns": cols,
        "output_names": names,
        "spec_diagnostics": [s.diagnostics() for s in specs],
    }


def replicate_ci_width(
    func: Callable[..., Any],
    specs: Sequence[InputSpec],
    n_rep: int = 20,
    n_per_rep: int = 5000,
    quantiles: Sequence[float] = (0.95,),
    confidence: float = 0.95,
    seed: int = 0,
    method: str = "mc",
) -> Dict[str, Any]:
    """Empirical interval width from *independent repeated* runs.

    For ``R`` independent runs of size ``n_per_rep`` the scatter of a statistic
    ``T`` estimates its sampling standard deviation; ``2 z std(T)`` is then the
    reference interval width that any new estimator must reproduce.
    """
    specs = _validate_specs(specs)
    t0 = time.perf_counter()
    means: List[np.ndarray] = []
    qs: Dict[str, List[float]] = {f"{q:g}": [] for q in quantiles}
    for r in range(int(n_rep)):
        X = sample_inputs(specs, n=n_per_rep, method=method, seed=seed + 1000 * (r + 1))
        Yc, _ = _as_columns(evaluate_model(func, X))
        means.append(np.mean(Yc, axis=0))
        for q in quantiles:
            qs[f"{q:g}"].append(float(np.quantile(Yc[:, 0], q)))
    z = _norm_ppf(0.5 + 0.5 * confidence)
    M = np.vstack(means)
    return {
        "n_rep": int(n_rep),
        "n_per_rep": int(n_per_rep),
        "wall_s": float(time.perf_counter() - t0),
        "mean_std_across_reps": np.std(M, axis=0, ddof=1).tolist(),
        "mean_ci_width": (2.0 * z * np.std(M, axis=0, ddof=1)).tolist(),
        "quantile_ci_width": {k: float(2.0 * z * np.std(np.asarray(v), ddof=1)) for k, v in qs.items()},
        "quantile_scatter": {k: float(np.std(np.asarray(v), ddof=1)) for k, v in qs.items()},
        "quantile_mean": {k: float(np.mean(np.asarray(v))) for k, v in qs.items()},
    }

# ---------------------------------------------------------------------------
# 4. Polynomial surrogate and accelerated propagation
# ---------------------------------------------------------------------------


@dataclass
class PolySurrogate:
    """Total-degree multivariate polynomial on standardised inputs.

    ``order=1`` is a linear response surface, ``order=2`` quadratic.  The basis
    is the set of monomials of total degree ``<= order`` in
    ``z = (x - m) / s`` (``m``, ``s`` from the input specs).  Ridge
    regularisation (tiny by default) keeps the least-squares problem well posed
    for an anisotropic design.
    """

    centre: np.ndarray
    scale: np.ndarray
    exponents: np.ndarray          # (n_terms, d)
    coef: np.ndarray               # (n_terms,) or (n_terms, m)
    ridge: float = 1e-10
    n_fit: int = 0
    fit_residual_rel: float = float("nan")

    @property
    def n_terms(self) -> int:
        return int(self.exponents.shape[0])

    def _design(self, X: np.ndarray) -> np.ndarray:
        Z = (np.asarray(X, dtype=float) - self.centre) / self.scale
        return np.prod(Z[:, None, :] ** self.exponents[None, :, :], axis=2)

    def predict(self, X: np.ndarray) -> np.ndarray:
        return self._design(X) @ self.coef

    def as_dict(self) -> Dict[str, Any]:
        return {
            "n_terms": self.n_terms,
            "n_fit": int(self.n_fit),
            "ridge": float(self.ridge),
            "fit_residual_rel": float(self.fit_residual_rel),
            "max_total_degree": int(np.max(self.exponents.sum(axis=1))),
        }


def _total_degree_exponents(d: int, order: int) -> np.ndarray:
    """All exponent multi-indices with ``1 <= sum <= order`` plus the constant."""
    terms: List[Tuple[int, ...]] = [tuple([0] * d)]

    def rec(prefix: List[int], remaining: int, slot: int) -> None:
        if slot == d - 1:
            terms.append(tuple(prefix + [remaining]))
            return
        for k in range(remaining, -1, -1):
            rec(prefix + [k], remaining - k, slot + 1)

    for total in range(1, int(order) + 1):
        rec([], total, 0)
    return np.asarray(sorted(set(terms)), dtype=int)


def fit_poly_surrogate(
    func: Callable[..., Any],
    specs: Sequence[InputSpec],
    n_design: int = 512,
    order: int = 2,
    method: str = "qmc",
    ridge: float = 1e-10,
    seed: int = 0,
    X_design: Optional[np.ndarray] = None,
    Y_design: Optional[np.ndarray] = None,
) -> PolySurrogate:
    """Least-squares total-degree polynomial surrogate of a black-box model."""
    specs = _validate_specs(specs)
    d = len(specs)
    moments = [s.moments() for s in specs]
    centre = np.array([m[0] for m in moments], dtype=float)
    scale = np.array([max(m[1], 1e-30) for m in moments], dtype=float)
    if X_design is None:
        X_design = sample_inputs(specs, n=n_design, method=method, seed=seed)
    Y = evaluate_model(func, X_design) if Y_design is None else np.asarray(Y_design, dtype=float)
    Yc, _ = _as_columns(Y)
    target = Yc.squeeze(-1) if Yc.shape[1] == 1 else Yc
    exps = _total_degree_exponents(d, int(order))
    Z = (np.asarray(X_design, dtype=float) - centre) / scale
    A = np.prod(Z[:, None, :] ** exps[None, :, :], axis=2)
    if A.shape[0] < A.shape[1]:
        raise ValueError(
            f"underdetermined surrogate: {A.shape[0]} design rows for {A.shape[1]} basis terms"
        )
    AtA = A.T @ A + float(ridge) * np.eye(A.shape[1])
    coef = np.linalg.solve(AtA, A.T @ target)
    resid = A @ coef - target
    denom = float(np.linalg.norm(Yc)) or 1.0
    return PolySurrogate(
        centre=centre,
        scale=scale,
        exponents=exps,
        coef=coef,
        ridge=float(ridge),
        n_fit=int(A.shape[0]),
        fit_residual_rel=float(np.linalg.norm(resid) / denom),
    )


def qmc_variance_reduction(
    func: Callable[..., Any],
    specs: Sequence[InputSpec],
    n_rep: int = 16,
    n_per_rep: int = 2048,
    seed: int = 0,
) -> Dict[str, Any]:
    """Matched-budget MC versus QMC: equivalent sample-size gain.

    Both designs are run ``n_rep`` times at the *same* per-run budget
    ``n_per_rep``; the scatter of the run-to-run mean estimates measures the
    estimator variance, and

        ``speedup = (std_est(mc) / std_est(qmc)) ** 2``

    is the number of plain-MC samples needed to reach the QMC estimator
    variance (variance ratio = sample-size ratio at fixed estimator variance).
    No surrogate is involved, so this is a clean lower bound on the achievable
    acceleration.
    """
    specs = _validate_specs(specs)
    means: Dict[str, List[np.ndarray]] = {"mc": [], "qmc": []}
    for method in ("mc", "qmc"):
        for r in range(int(n_rep)):
            X = sample_inputs(specs, n=n_per_rep, method=method, seed=seed + 977 * (r + 1))
            Yc, _ = _as_columns(evaluate_model(func, X))
            means[method].append(np.mean(Yc, axis=0))
    std_mc = np.std(np.vstack(means["mc"]), axis=0, ddof=1)
    std_qmc = np.std(np.vstack(means["qmc"]), axis=0, ddof=1)
    ratio = np.where(std_qmc > 0.0, (std_mc / np.maximum(std_qmc, 1e-300)) ** 2, np.inf)
    return {
        "n_rep": int(n_rep),
        "n_per_rep": int(n_per_rep),
        "std_mean_mc": std_mc.tolist(),
        "std_mean_qmc": std_qmc.tolist(),
        "speedup": ratio.tolist(),
        "speedup_min": float(np.min(ratio)),
        "speedup_max": float(np.max(ratio)),
    }


def accelerated_uncertainty(
    func: Callable[..., Any],
    specs: Sequence[InputSpec],
    n_design: int = 512,
    order: int = 2,
    n_valid: int = 256,
    n_mc: int = 200000,
    quantiles: Sequence[float] = (0.95,),
    seed: int = 0,
    brute_method: str = "mc",
    n_rep: int = 16,
    n_per_rep: int = 2048,
    n_boot: int = 200,
) -> Dict[str, Any]:
    """Surrogate-accelerated propagation benchmarked against brute-force MC.

    Two honest speed-up numbers are produced:

    * ``speedup_evals`` = ``n_mc / (n_design + n_valid)`` -- the model-evaluation
      cost of the accelerated path versus the brute-force sample;
    * ``speedup_matched_budget`` = ``(hw_brute / hw_qmc) ** 2`` at equal
      evaluations -- the equivalent sample-size gain of the quasi-random design
      alone (variance reduction, no surrogate involved).

    The surrogate is validated on an independent ``n_valid`` sample
    (``validation_rel_l2`` / ``validation_rel_max``); the accelerated statistics
    are only as trustworthy as that number, which is returned alongside.
    """
    specs = _validate_specs(specs)
    t0 = time.perf_counter()
    brute = output_uncertainty(
        func, specs, n=n_mc, quantiles=quantiles, seed=seed, method=brute_method,
        n_boot=int(n_boot),
    )
    t_brute = time.perf_counter() - t0

    t1 = time.perf_counter()
    X_design = sample_inputs(specs, n=n_design, method="qmc", seed=seed + 1)
    Y_design = evaluate_model(func, X_design)
    sur = fit_poly_surrogate(
        func, specs, order=order, X_design=X_design, Y_design=Y_design
    )
    t_cost0 = time.perf_counter()
    _Xc = sample_inputs(specs, n=min(2048, n_mc), method="mc", seed=seed + 11)
    evaluate_model(func, _Xc)
    t_model_per_eval = (time.perf_counter() - t_cost0) / _Xc.shape[0]
    X_valid = sample_inputs(specs, n=n_valid, method="qmc", seed=seed + 2)
    Y_valid = evaluate_model(func, X_valid)
    Yv, _ = _as_columns(Y_valid)
    Yh = np.asarray(sur.predict(X_valid), dtype=float).reshape(Yv.shape)
    rel_l2 = float(np.linalg.norm(Yh - Yv) / (np.linalg.norm(Yv) or 1.0))
    rel_max = float(np.max(np.abs(Yh - Yv) / np.maximum(np.abs(Yv), 1e-30)))

    # large sample through the cheap surrogate (evaluation cost = 0 model calls)
    X_big = sample_inputs(specs, n=n_mc, method="qmc", seed=seed + 3)
    Y_big = np.asarray(sur.predict(X_big), dtype=float)
    Yb, names = _as_columns(Y_big)
    z = _norm_ppf(0.975)
    acc_cols: Dict[str, Any] = {}
    for j, nm in enumerate(brute["output_names"]):
        y = Yb[:, j]
        mean = float(np.mean(y))
        sd = float(np.std(y, ddof=1))
        q_entry: Dict[str, Any] = {}
        for q in quantiles:
            # distribution-free interval: O(n log n), no resampling, so the
            # accelerated path does not pay a bootstrap bill the brute-force
            # path pays anyway
            ci = quantile_ci_order_statistic(y, q=q, confidence=0.95)
            q_entry[f"{q:g}"] = {
                "estimate": ci["estimate"],
                "ci_low": ci["ci_low"],
                "ci_high": ci["ci_high"],
                "width": float(ci["ci_high"] - ci["ci_low"]),
                "method": ci["method"],
            }
        acc_cols[nm] = {
            "mean": mean,
            "std": sd,
            "ci_mean": [mean - z * sd / math.sqrt(y.size), mean + z * sd / math.sqrt(y.size)],
            "ci_mean_width": float(2.0 * z * sd / math.sqrt(y.size)),
            "quantiles": q_entry,
        }
    wall_acc = time.perf_counter() - t1

    # matched-budget QMC (no surrogate): replicate scatter of the mean at the
    # same per-run budget, MC versus QMC -- the equivalent sample-size gain
    matched = qmc_variance_reduction(
        func, specs, n_rep=int(n_rep), n_per_rep=int(n_per_rep), seed=seed + 7
    )

    return {
        "brute_force": {
            "method": brute_method,
            "n_evals": int(n_mc),
            "wall_s": float(t_brute),
            "columns": brute["columns"],
        },
        "accelerated": {
            "n_model_evals": int(n_design + n_valid),
            "n_design": int(n_design),
            "n_valid": int(n_valid),
            "n_surrogate_samples": int(n_mc),
            "wall_s": float(wall_acc),
            "surrogate": sur.as_dict(),
            "validation_rel_l2": rel_l2,
            "validation_rel_max": rel_max,
            "columns": acc_cols,
        },
        "speedup_evals": float(n_mc / max(n_design + n_valid, 1)),
        "speedup_wall": float(t_brute / max(wall_acc, 1e-12)),
        "model_cost_per_eval_s": float(t_model_per_eval),
        "projected_speedup_at_1ms_per_eval": float(
            (n_mc * 1e-3) / ((n_design + n_valid) * 1e-3 + wall_acc)),
        "quantile_width_ratio": {
            nm: (
                acc_cols[nm]["quantiles"][f"{q:g}"]["width"]
                / brute["columns"][nm]["quantiles"][f"{q:g}"]["width_order_statistic"]
            )
            for nm in brute["output_names"]
            for q in quantiles
        },
        "matched_budget": matched,
    }


# ---------------------------------------------------------------------------
# 5. Sobol' closure decomposition (complement design + Möbius inversion)
# ---------------------------------------------------------------------------


def _subsets(d: int, max_order: int) -> List[Tuple[int, ...]]:
    from itertools import combinations

    out: List[Tuple[int, ...]] = []
    for k in range(1, int(max_order) + 1):
        out.extend(combinations(range(d), k))
    return out


def sobol_hdmr(
    func: Callable[..., Any],
    specs: Sequence[InputSpec],
    n_base: int = 1024,
    max_order: int = 2,
    seed: int = 0,
    n_boot: int = 0,
    confidence: float = 0.95,
    closure_band: Tuple[float, float] = (0.95, 1.05),
) -> Dict[str, Any]:
    """Sobol' indices of *every* order ``<= max_order`` plus the closure test.

    Estimator (complement design).  With independent draws ``A``, ``B`` and
    ``M_u`` = ``A`` in the coordinates of ``u`` and ``B`` elsewhere,

        ``G_u = E[y_A * y_{M_u}] - f0^2 = sum_{w subseteq u, w != {}} V_w``

    so the exact-order shares follow from Möbius inversion

        ``V_u = sum_{w subseteq u} (-1)^(|u| - |w|) G_w``,  ``G_{} = 0``.

    Because the Sobol'-Hoeffding decomposition is orthogonal,
    ``sum_{all u} S_u = 1`` exactly; the *closure*

        ``closure = sum_{1 <= |u| <= max_order} S_u``

    therefore tests the estimator and, at the same time, quantifies how much of
    the output variance lives in interactions of order ``> max_order``
    (``higher_order_share = 1 - closure``).

    Total-order indices come from the complementary subsets as
    ``S_T_i = 1 - G_{~i} / V`` -- a second, independent total-order estimator
    relative to the Jansen estimator in ``global_sensitivity``.

    Cost: ``(2 + #{u : |u| <= max_order} + d) * n_base`` model evaluations.
    """
    from itertools import combinations as _combs

    specs = _validate_specs(specs)
    d = len(specs)
    if int(max_order) < 1 or int(max_order) > d:
        raise ValueError("max_order must be in [1, d]")
    names = [s.name for s in specs]
    rng = np.random.default_rng(int(seed))
    # NOTE: the two base matrices must be *independent* draws. Low-discrepancy
    # (Sobol') points are deliberately *not* offered here: pairing two scrambled
    # nets correlates A with B and destroys the identity the estimator rests on.
    skip = 16
    U = rng.random((int(n_base) + skip, 2 * d))[skip:]
    UA, UB = U[:, :d], U[:, d:]

    def phys(W: np.ndarray) -> np.ndarray:
        return np.column_stack([specs[j].to_uniform(W[:, j]) for j in range(d)])

    A, B = phys(UA), phys(UB)
    yA = np.asarray(evaluate_model(func, A), dtype=float).ravel()
    yB = np.asarray(evaluate_model(func, B), dtype=float).ravel()
    n_evals = 2 * int(n_base)

    subsets = _subsets(d, int(max_order))
    masks: List[np.ndarray] = []
    yM: List[np.ndarray] = []
    for u in subsets:
        mask = np.zeros(d, dtype=bool)
        mask[list(u)] = True
        masks.append(mask)
        yM.append(np.asarray(evaluate_model(func, np.where(mask, A, B)), dtype=float).ravel())
        n_evals += int(n_base)
    comp_blocks: List[np.ndarray] = []
    comp_idx: List[int] = []
    for i in range(d):
        mask = np.ones(d, dtype=bool)
        mask[i] = False                      # M_{~i}: A everywhere except i (from B)
        comp_blocks.append(np.asarray(evaluate_model(func, np.where(mask, A, B)), dtype=float).ravel())
        comp_idx.append(i)
        n_evals += int(n_base)

    pooled = np.concatenate([yA, yB])
    variance = float(np.var(pooled, ddof=1))
    f0 = 0.5 * (float(np.mean(yA)) + float(np.mean(yB)))

    def _estimate(idx: Optional[np.ndarray] = None) -> Dict[str, Any]:
        if idx is None:
            a, b, m_list, c_list = yA, yB, yM, comp_blocks
        else:
            a, b = yA[idx], yB[idx]
            m_list = [y[idx] for y in yM]
            c_list = [y[idx] for y in comp_blocks]
        # Centred estimator: with the true mean f0,
        # E[(y_A - f0)(y_{M_u} - f0)] = E[y_A y_{M_u}] - f0^2 = G_u, but the
        # centred form has a far smaller sampling variance because neither
        # factor carries the (large) constant offset.
        f0_loc = 0.5 * (float(np.mean(a)) + float(np.mean(b)))
        ac = a - f0_loc
        G: Dict[Tuple[int, ...], float] = {(): 0.0}
        for u, y in zip(subsets, m_list):
            G[u] = float(np.mean(ac * (y - f0_loc)))
        S: Dict[Tuple[int, ...], float] = {}
        for u in subsets:
            tot = 0.0
            for k in range(0, len(u) + 1):
                for w in _combs(u, k):
                    sign = -1.0 if (len(u) - len(w)) % 2 else 1.0
                    tot += sign * G[w]
            S[u] = tot / variance if variance > 0.0 else 0.0
        ST = {}
        for i in range(d):
            Gc = float(np.mean(ac * (c_list[i] - f0_loc)))
            ST[i] = 1.0 - Gc / variance if variance > 0.0 else 0.0
        # classic Saltelli (2010) first-order estimator.
        # Here M_{~i} (A everywhere except column i from B) *is* the pick-freeze
        # AB_i matrix, so
        #     S_i = E[y_B (y_AB_i - y_A)] / V,
        # which has much lower variance than the raw-product G_i form because the
        # two factors are correlated differences.  Computed from evaluations the
        # total-order estimator needs anyway -- zero extra model calls.
        S1_salt = {}
        if variance > 0.0:
            for i in range(d):
                S1_salt[(i,)] = float(np.mean(b * (c_list[i] - a))) / variance
        else:
            S1_salt = {(i,): 0.0 for i in range(d)}
        return {"S": S, "ST": ST, "S1_salt": S1_salt}

    core = _estimate()
    S, ST, S1_salt = core["S"], core["ST"], core["S1_salt"]
    closure = float(sum(S.values()))
    first = {names[u[0]]: float(S[u]) for u in subsets if len(u) == 1}
    first_saltelli = {names[u[0]]: float(S1_salt[u]) for u in subsets if len(u) == 1}
    pair_share = float(sum(v for u, v in S.items() if len(u) == 2))
    closure_saltelli = float(sum(first_saltelli.values()) + pair_share)
    second = {
        ",".join(names[i] for i in u): float(S[u]) for u in subsets if len(u) == 2
    }
    total_order = {names[i]: float(ST[i]) for i in range(d)}

    boot: Dict[str, Any] = {}
    if int(n_boot) > 0:
        closure_samples: List[float] = []
        first_samples: Dict[str, List[float]] = {k: [] for k in first}
        for _ in range(int(n_boot)):
            idx = rng.integers(0, int(n_base), size=int(n_base))
            e = _estimate(idx)
            closure_samples.append(float(sum(e["S"].values())))
            for u in subsets:
                if len(u) == 1:
                    first_samples[names[u[0]]].append(float(e["S"][u]))
        alpha = 1.0 - float(confidence)
        boot = {
            "n_boot": int(n_boot),
            "closure_std": float(np.std(closure_samples, ddof=1)),
            "closure_ci": [
                float(np.quantile(closure_samples, alpha / 2.0)),
                float(np.quantile(closure_samples, 1.0 - alpha / 2.0)),
            ],
            "first_order_std": {k: float(np.std(v, ddof=1)) for k, v in first_samples.items()},
        }

    lo, hi = float(closure_band[0]), float(closure_band[1])
    return {
        "estimator": "complement-design + moebius inversion",
        "names": names,
        "n_base": int(n_base),
        "n_evals": int(n_evals),
        "max_order": int(max_order),
        "mean": float(f0),
        "variance": variance,
        "first_order": first,
        "second_order": second,
        "total_order": total_order,
        "sum_first_order": float(sum(first.values())),
        "sum_total_order": float(sum(total_order.values())),
        "closure": closure,
        "first_order_saltelli": first_saltelli,
        "pair_share": pair_share,
        "closure_saltelli": closure_saltelli,
        "higher_order_share": float(1.0 - closure),
        "closure_band": [lo, hi],
        "closure_ok": bool(lo <= closure <= hi),
        "bootstrap": boot,
        "ranking": sorted(first.items(), key=lambda kv: -abs(kv[1])),
    }


def sobol_hdmr_replicated(
    func: Callable[..., Any],
    specs: Sequence[InputSpec],
    n_base: int = 8192,
    n_rep: int = 6,
    max_order: int = 2,
    seed: int = 0,
    confidence: float = 0.95,
    closure_band: Tuple[float, float] = (0.95, 1.05),
) -> Dict[str, Any]:
    """Average :func:`sobol_hdmr` over ``n_rep`` independent seeds.

    Why this exists: for a strongly non-additive response the *closure* is a sum
    of ``1 + d + d(d-1)/2`` estimated shares, and the pair terms come from small
    contrasts, so the single-run estimator has a sampling standard error of a
    few percent -- comparable to the acceptance band.  Reporting one seed would
    therefore be luck, not evidence.  This wrapper averages ``n_rep`` runs and
    returns the standard error (and a normal-theory interval) of every index, so
    the closure test carries its own uncertainty.

    Cost: ``n_rep * (2 + #{|u| <= max_order} + d) * n_base`` model evaluations.
    """
    specs = _validate_specs(specs)
    runs = [
        sobol_hdmr(func, specs, n_base=n_base, max_order=max_order,
                   seed=int(seed) + 7919 * r, n_boot=0, closure_band=closure_band)
        for r in range(int(n_rep))
    ]
    z = _norm_ppf(0.5 + 0.5 * float(confidence))

    def _agg(key: str) -> Dict[str, float]:
        vals = [run[key] for run in runs]
        return {k: float(np.mean([v[k] for v in vals])) for k in vals[0]}

    def _agg_se(key: str) -> Dict[str, float]:
        vals = [run[key] for run in runs]
        se = {}
        for k in vals[0]:
            arr = np.array([v[k] for v in vals], dtype=float)
            se[k] = float(np.std(arr, ddof=1) / math.sqrt(arr.size))
        return se

    closures = np.array([run["closure"] for run in runs], dtype=float)
    closure_se = float(np.std(closures, ddof=1) / math.sqrt(closures.size))
    return {
        "estimator": "complement-design + moebius inversion, seed-replicated",
        "n_rep": int(n_rep),
        "n_base": int(n_base),
        "n_evals_total": int(sum(run["n_evals"] for run in runs)),
        "first_order": _agg("first_order"),
        "first_order_se": _agg_se("first_order"),
        "second_order": _agg("second_order"),
        "second_order_se": _agg_se("second_order"),
        "total_order": _agg("total_order"),
        "total_order_se": _agg_se("total_order"),
        "sum_first_order": float(np.mean([r["sum_first_order"] for r in runs])),
        "sum_total_order": float(np.mean([r["sum_total_order"] for r in runs])),
        "variance": float(np.mean([r["variance"] for r in runs])),
        "mean": float(np.mean([r["mean"] for r in runs])),
        "closure": float(np.mean(closures)),
        "closure_std": float(np.std(closures, ddof=1)),
        "closure_se": closure_se,
        "closure_ci": [float(np.mean(closures) - z * closure_se),
                       float(np.mean(closures) + z * closure_se)],
        "replicate_closures": closures.tolist(),
        "closures_ok_per_replicate": int(sum(1 for c in closures
                                             if closure_band[0] <= c <= closure_band[1])),
        "higher_order_share": 1.0 - float(np.mean(closures)),
        "closure_saltelli": float(np.mean([r["closure_saltelli"] for r in runs])),
        "first_order_saltelli": {
            k: float(np.mean([r["first_order_saltelli"][k] for r in runs]))
            for k in runs[0]["first_order_saltelli"]
        },
        "closure_band": [float(closure_band[0]), float(closure_band[1])],
        "closure_ok": bool(closure_band[0] <= float(np.mean(closures)) <= closure_band[1]),
        "ranking": sorted(_agg("first_order").items(), key=lambda kv: -abs(kv[1])),
    }


# ---------------------------------------------------------------------------
# 6. Error-propagation chain
# ---------------------------------------------------------------------------


def jacobian(
    func: Callable[[np.ndarray], np.ndarray],
    x0: Sequence[float],
    rel_step: float = 1e-6,
    abs_step: float = 0.0,
) -> np.ndarray:
    """Central-difference Jacobian ``d f / d x`` of ``func`` at ``x0``.

    ``func`` may return a scalar or a vector; the result is ``(m, n)``.  Step is
    ``rel_step * max(|x_j|, abs_step)`` per coordinate (a pure absolute step is
    used where ``rel_step`` would underflow).
    """
    x0 = np.asarray(x0, dtype=float)
    n = x0.size
    f0 = np.asarray(func(x0), dtype=float).ravel()
    m = f0.size
    J = np.zeros((m, n), dtype=float)
    for j in range(n):
        h = rel_step * max(abs(float(x0[j])), 1e-12)
        if abs_step > 0.0:
            h = max(h, float(abs_step))
        xp = x0.copy()
        xp[j] += h
        xm = x0.copy()
        xm[j] -= h
        J[:, j] = (np.asarray(func(xp), dtype=float).ravel()
                   - np.asarray(func(xm), dtype=float).ravel()) / (2.0 * h)
    return J


@dataclass
class ChainStage:
    """One link of a forward chain: ``y_k = f_k(y_{k-1})``.

    ``output_names`` labels the components of the stage output (used by the
    figures and the returned dictionaries); defaults to ``z<k>_<j>``.
    """

    name: str
    func: Callable[[np.ndarray], np.ndarray]
    output_names: Optional[Sequence[str]] = None


def propagation_chain(
    stages: Sequence[ChainStage],
    x0: Sequence[float],
    specs: Sequence[InputSpec],
    n_mc: int = 20000,
    seed: int = 0,
    rel_step: float = 1e-6,
    variance_floor: float = 1e-30,
) -> Dict[str, Any]:
    """Full error-propagation diagnostics along a forward chain of stages.

    Produces

    * the nominal path ``x0 -> z1 -> ... -> zK``;
    * the composed Jacobian ``J`` at ``x0`` (central differences) and the
      *scaled* (dimensionless) Jacobian ``S_ij = J_ij sigma_j / sigma_yi``
      whose 2-norm condition number is the numerical-stability criterion;
    * per-stage local condition number and amplification ``||S_k||_2``;
    * Monte-Carlo versus linearised (delta-method) standard deviations at every
      stage -- their ratio is the validity check of the linearisation;
    * per-input variance contributions at the final output and a node/edge graph
      (``graph``) ready for :func:`figure_propagation_path`.
    """
    stages = list(stages)
    if not stages:
        raise ValueError("at least one ChainStage is required")
    specs = _validate_specs(specs)
    d = len(specs)
    x0 = np.asarray(x0, dtype=float).ravel()
    if x0.size != d:
        raise ValueError("x0 must have one entry per InputSpec")

    def compose(x: np.ndarray) -> np.ndarray:
        z = np.asarray(x, dtype=float).ravel()
        for st in stages:
            z = np.asarray(st.func(z), dtype=float).ravel()
        return z

    # nominal path
    nom_path = [x0.copy()]
    z = x0.copy()
    for st in stages:
        z = np.asarray(st.func(z), dtype=float).ravel()
        nom_path.append(z.copy())

    J_total = jacobian(compose, x0, rel_step=rel_step)

    # per-stage local Jacobians along the nominal path
    local_J = [
        jacobian(st.func, nom_path[k], rel_step=rel_step) for k, st in enumerate(stages)
    ]

    # Monte-Carlo propagation through every stage
    X = sample_inputs(specs, n=n_mc, method="qmc" if d >= 3 else "mc", seed=seed)
    mc_path: List[np.ndarray] = [X]
    cur = X
    for st in stages:
        # vectorised stages are called once; scalar-only stages are row-looped
        cur = np.asarray(evaluate_model(st.func, cur), dtype=float)
        if cur.ndim == 1:
            cur = cur.reshape(-1, 1)
        mc_path.append(cur)

    sigma_in = np.array([s.moments()[1] for s in specs], dtype=float)

    # linearised (delta-method) covariance propagation
    Sigma = np.diag(np.maximum(sigma_in, 0.0) ** 2)
    lin_sigma: List[np.ndarray] = []
    for Jk in local_J:
        Sigma = Jk @ Sigma @ Jk.T
        lin_sigma.append(np.sqrt(np.maximum(np.diag(Sigma), 0.0)))

    stage_diag: List[Dict[str, Any]] = []
    for k, st in enumerate(stages):
        out = mc_path[k + 1]
        sig_mc = np.std(out, axis=0, ddof=1)
        sig_lin = lin_sigma[k]
        names_k = list(st.output_names) if st.output_names is not None else [
            f"{st.name}_{j}" for j in range(out.shape[1])
        ]
        sig_in_k = np.maximum(np.std(mc_path[k], axis=0, ddof=1), math.sqrt(variance_floor))
        sig_out_k = np.maximum(sig_mc, math.sqrt(variance_floor))
        Jk_scaled = local_J[k] * sig_in_k[None, :] / sig_out_k[:, None]
        amp = float(np.linalg.norm(Jk_scaled, 2)) if Jk_scaled.size else 0.0
        stage_diag.append({
            "stage": st.name,
            "input_dim": int(local_J[k].shape[1]),
            "output_dim": int(local_J[k].shape[0]),
            "output_names": names_k,
            "nominal_output": nom_path[k + 1].tolist(),
            "sigma_mc": sig_mc.tolist(),
            "sigma_linearised": sig_lin.tolist(),
            "linearisation_ratio": (sig_lin / np.maximum(sig_mc, math.sqrt(variance_floor))).tolist(),
            # the *unscaled* local Jacobian mixes units (kN per m against kN per
            # Pa), so its condition number is unit-dependent and meaningless; the
            # dimensionless one is reported
            "local_condition_number_scaled": float(_cond(Jk_scaled)),
            "amplification": amp,
        })

    sig_out_mc = np.std(mc_path[-1], axis=0, ddof=1)
    scale_out = np.maximum(sig_out_mc, math.sqrt(variance_floor))
    S_full = J_total * sigma_in[None, :] / scale_out[:, None]
    cond_full = float(_cond(S_full))
    cond_input = float(_input_space_cond(S_full))

    var_contrib = (J_total * sigma_in[None, :]) ** 2 / (scale_out[:, None] ** 2)
    share = var_contrib / np.maximum(var_contrib.sum(axis=1, keepdims=True), variance_floor)
    contrib_by_input = {specs[j].name: float(np.mean(share[:, j])) for j in range(d)}
    ranked = sorted(contrib_by_input.items(), key=lambda kv: -kv[1])

    out_names = None
    if stages[-1].output_names is not None:
        out_names = list(stages[-1].output_names)
    if out_names is None or len(out_names) != mc_path[-1].shape[1]:
        out_names = [f"out{j}" for j in range(mc_path[-1].shape[1])]

    graph = _chain_graph(stages, specs, local_J, sigma_in, nom_path, mc_path)

    return {
        "input_names": [s.name for s in specs],
        "stage_names": [st.name for st in stages],
        "n_mc": int(n_mc),
        "nominal_inputs": x0.tolist(),
        "nominal_path": [p.tolist() for p in nom_path],
        "final_output_names": out_names,
        "final_nominal": nom_path[-1].tolist(),
        "jacobian_total": J_total.tolist(),
        "scaled_jacobian": S_full.tolist(),
        "condition_number": cond_full,
        "input_space_condition_number": cond_input,
        "rank_output": int(S_full.shape[0]),
        "rank_input": int(S_full.shape[1]),
        "condition_number_definition": (
            "condition_number = sigma_max/sigma_min of S_ij = J_ij sigma_j / sigma_yi "
            "(min(m,d) singular values, classical 2-norm condition number); "
            "input_space_condition_number = sqrt(lam_max/lam_min) of S^T S, inf when "
            "two input directions are indistinguishable in the output"
        ),
        "stages": stage_diag,
        "variance_contributions": contrib_by_input,
        "variance_contribution_ranking": [k for k, _ in ranked],
        "dominant_input": ranked[0][0],
        "graph": graph,
    }


def _cond(M: np.ndarray) -> float:
    """2-norm condition number: ``sigma_max / sigma_min`` over ``min(m, n)``.

    This is the classical numerical-stability number of the (scaled) Jacobian.
    For a wide map (``m < d``) it cannot see degeneracy in the *input*
    directions; that case is covered by :func:`_input_space_cond`.
    """
    M = np.asarray(M, dtype=float)
    if M.size == 0:
        return float("inf")
    s = np.linalg.svd(M, compute_uv=False)
    if s.size == 0 or s[-1] <= 0.0:
        return float("inf")
    return float(s[0] / s[-1])


def _input_space_cond(M: np.ndarray, rank_tol: float = 1e-12) -> float:
    """Conditioning of the map *restricted to input space*: ``sqrt(lam_max/lam_min)``.

    Eigenvalues of ``M^T M`` are the squared singular values, so for ``m >= d``
    with full column rank this equals :func:`_cond`.  Its value over the
    classical one is that a wide map with ``m < d`` reports ``inf`` instead of a
    misleading ``1`` whenever two input directions are indistinguishable in the
    output -- i.e. it detects non-identifiability of the propagation operator.
    """
    M = np.asarray(M, dtype=float)
    if M.size == 0:
        return float("inf")
    G = M.T @ M
    lam = np.linalg.eigvalsh(G)
    lam = np.sort(np.maximum(lam, 0.0))
    if lam[-1] <= 0.0:
        return float("inf")
    if lam[0] <= rank_tol * lam[-1]:
        return float("inf")
    return float(math.sqrt(lam[-1] / lam[0]))


def _chain_graph(
    stages: Sequence[ChainStage],
    specs: Sequence[InputSpec],
    local_J: Sequence[np.ndarray],
    sigma_in: np.ndarray,
    nom_path: Sequence[np.ndarray],
    mc_path: Sequence[np.ndarray],
) -> Dict[str, Any]:
    """Node/edge description of the propagation path (for the figure)."""
    layer_nodes: List[List[Dict[str, Any]]] = []
    layer_nodes.append([
        {"id": f"in:{s.name}", "label": s.name, "layer": 0, "kind": "input"}
        for s in specs
    ])
    for k, st in enumerate(stages):
        out = mc_path[k + 1]
        names_k = list(st.output_names) if st.output_names is not None else [
            f"{st.name}_{j}" for j in range(out.shape[1])
        ]
        layer_nodes.append([
            {"id": f"s{k}:{nm}", "label": nm, "layer": k + 1, "kind": "stage",
             "stage": st.name}
            for nm in names_k
        ])
    edges: List[Dict[str, Any]] = []
    prev_sigma = np.maximum(sigma_in, 0.0)
    for k, Jk in enumerate(local_J):
        out_mc = mc_path[k + 1]
        sig_out = np.maximum(np.std(out_mc, axis=0, ddof=1), 1e-30)
        scaled = Jk * prev_sigma[None, :] / sig_out[:, None]
        share = scaled ** 2
        share = share / np.maximum(share.sum(axis=1, keepdims=True), 1e-30)
        src = layer_nodes[k]
        dst = layer_nodes[k + 1]
        for i, dn in enumerate(dst):
            for j, sn in enumerate(src):
                edges.append({
                    "source": sn["id"],
                    "target": dn["id"],
                    "stage": stages[k].name,
                    "scaled_sensitivity": float(scaled[i, j]) if scaled.shape[1] > j else 0.0,
                    "variance_share": float(share[i, j]) if share.shape[1] > j else 0.0,
                })
        prev_sigma = sig_out
    return {"nodes": [n for layer in layer_nodes for n in layer], "edges": edges,
            "n_layers": len(layer_nodes)}


# ---------------------------------------------------------------------------
# 7. Figures (generic renderers; every label comes from the caller)
# ---------------------------------------------------------------------------


def _plt():
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    return plt


def figure_confidence_intervals(
    rows: Sequence[Dict[str, Any]], path: str, title: str = "", xlabel: str = ""
) -> str:
    """Horizontal 95 % intervals for a list of ``{label, value, lo, hi}`` rows."""
    plt = _plt()
    fig, ax = plt.subplots(figsize=(7.5, 0.55 * len(rows) + 1.6))
    ys = np.arange(len(rows))[::-1]
    for y, r in zip(ys, rows):
        lo, hi = float(r["lo"]), float(r["hi"])
        ax.plot([lo, hi], [y, y], color="#1f4e79", lw=2.2, solid_capstyle="round")
        ax.plot([lo, lo], [y - 0.16, y + 0.16], color="#1f4e79", lw=1.4)
        ax.plot([hi, hi], [y - 0.16, y + 0.16], color="#1f4e79", lw=1.4)
        ax.plot([r["value"]], [y], marker="o", ms=6.5, color="#c00000", zorder=3)
        ax.annotate(f"{r['value']:.4g}  [{lo:.4g}, {hi:.4g}]",
                    xy=(hi, y), xytext=(6, 0), textcoords="offset points",
                    va="center", fontsize=8.5, color="#333333")
    ax.set_yticks(ys)
    ax.set_yticklabels([r["label"] for r in rows], fontsize=9)
    ax.set_xlabel(xlabel or "value")
    ax.set_title(title or "95 % confidence intervals", fontsize=10.5)
    ax.grid(axis="x", ls=":", alpha=0.5)
    ax.margins(x=0.28)
    fig.tight_layout()
    fig.savefig(path, dpi=170)
    plt.close(fig)
    return path


def figure_sensitivity(
    rows: Sequence[Dict[str, Any]], path: str, title: str = "", xlabel: str = "Sobol' index"
) -> str:
    """Grouped horizontal bars of ``{name, S1, ST}`` sensitivity rows."""
    plt = _plt()
    fig, ax = plt.subplots(figsize=(7.5, 0.5 * len(rows) + 1.8))
    ys = np.arange(len(rows))[::-1]
    h = 0.38
    s1 = [float(r.get("S1", 0.0)) for r in rows]
    st = [float(r.get("ST", 0.0)) for r in rows]
    ax.barh(ys + h / 2, s1, height=h, color="#1f4e79", label="first order $S_i$")
    ax.barh(ys - h / 2, st, height=h, color="#e08a1e", label="total order $S_{Ti}$")
    for y, a, b in zip(ys, s1, st):
        ax.annotate(f"{a:.3f}", xy=(max(a, 0.0), y + h / 2), xytext=(3, 0),
                    textcoords="offset points", va="center", fontsize=7.5)
        ax.annotate(f"{b:.3f}", xy=(max(b, 0.0), y - h / 2), xytext=(3, 0),
                    textcoords="offset points", va="center", fontsize=7.5)
    ax.set_yticks(ys)
    ax.set_yticklabels([r["name"] for r in rows], fontsize=9)
    ax.set_xlabel(xlabel)
    ax.set_title(title or "Variance-based sensitivity ranking", fontsize=10.5)
    ax.legend(fontsize=8, loc="lower right")
    ax.grid(axis="x", ls=":", alpha=0.5)
    ax.margins(x=0.18)
    fig.tight_layout()
    fig.savefig(path, dpi=170)
    plt.close(fig)
    return path


def figure_propagation_path(
    graph: Dict[str, Any],
    path: str,
    title: str = "",
    edge_threshold: float = 0.02,
    edge_width_scale: float = 5.0,
) -> str:
    """Node-link diagram of the error-propagation path.

    Edges are drawn with width proportional to the variance share they carry
    (only edges above ``edge_threshold`` share are annotated), so the dominant
    error path is visually obvious.
    """
    plt = _plt()
    from matplotlib.patches import FancyArrowPatch

    nodes = graph["nodes"]
    layers: Dict[int, List[Dict[str, Any]]] = {}
    for n in nodes:
        layers.setdefault(int(n["layer"]), []).append(n)
    n_layers = max(layers) + 1
    pos: Dict[str, Tuple[float, float]] = {}
    for L, ns in layers.items():
        for i, n in enumerate(ns):
            pos[n["id"]] = (float(L), -(i - 0.5 * (len(ns) - 1)))

    fig, ax = plt.subplots(figsize=(2.2 * n_layers + 2.0, max(3.4, 0.55 * max(
        len(v) for v in layers.values()) + 1.8)))
    for e in graph["edges"]:
        share = float(e.get("variance_share", 0.0))
        if share < edge_threshold:
            continue
        x0, y0 = pos.get(e["source"], (0.0, 0.0))
        x1, y1 = pos.get(e["target"], (1.0, 0.0))
        lw = edge_width_scale * math.sqrt(share)
        ax.add_patch(FancyArrowPatch((x0 + 0.12, y0), (x1 - 0.12, y1),
                                     arrowstyle="-|>", mutation_scale=9,
                                     lw=lw, color="#1f4e79", alpha=0.55,
                                     connectionstyle="arc3,rad=0.0"))
        ax.annotate(f"{100 * share:.0f}%", xy=(0.5 * (x0 + x1), 0.5 * (y0 + y1)),
                    fontsize=6.5, color="#1f4e79", ha="center",
                    bbox=dict(boxstyle="round,pad=0.12", fc="white", ec="none", alpha=0.75))
    for n in nodes:
        x, y = pos[n["id"]]
        color = {"input": "#dce9f5", "stage": "#fdf0d5"}.get(n["kind"], "#e8f5e9")
        ax.text(x, y, n["label"], ha="center", va="center", fontsize=8.5,
                bbox=dict(boxstyle="round,pad=0.32", fc=color, ec="#555555", lw=0.9))
    ax.set_xlim(-0.7, n_layers - 0.3)
    ax.set_ylim(-0.5 * max(len(v) for v in layers.values()) - 0.7,
                0.5 * max(len(v) for v in layers.values()) + 0.7)
    ax.axis("off")
    ax.set_title(title or "Error-propagation path (edge width ~ variance share)", fontsize=10.5)
    fig.tight_layout()
    fig.savefig(path, dpi=170)
    plt.close(fig)
    return path


def figure_convergence(
    series: Dict[str, Sequence[Tuple[float, float]]],
    path: str,
    title: str = "",
    xlabel: str = "model evaluations N",
    ylabel: str = "interval half-width",
) -> str:
    """log-log convergence of an interval half-width per estimator.

    ``series`` maps label -> sequence of ``(n_evaluations, half_width)`` pairs.
    """
    plt = _plt()
    fig, ax = plt.subplots(figsize=(6.4, 4.4))
    for label, pts in series.items():
        a = np.asarray(list(pts), dtype=float)
        ax.loglog(a[:, 0], a[:, 1], marker="o", ms=4.2, lw=1.5, label=label)
        if a.shape[0] >= 2 and np.all(a[:, 1] > 0):
            slope = float(np.polyfit(np.log(a[:, 0]), np.log(a[:, 1]), 1)[0])
            ax.annotate(f"{label}: slope {slope:.2f}", xy=(a[-1, 0], a[-1, 1]),
                        xytext=(-6, 8), textcoords="offset points", fontsize=8,
                        ha="right")
    ax.set_xlabel(xlabel)
    ax.set_ylabel(ylabel)
    ax.set_title(title or "Estimator convergence", fontsize=10.5)
    ax.grid(True, which="both", ls=":", alpha=0.5)
    ax.legend(fontsize=8)
    fig.tight_layout()
    fig.savefig(path, dpi=170)
    plt.close(fig)
    return path
