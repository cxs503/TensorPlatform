"""Variance-based global sensitivity analysis (GSA / Sobol' indices) for black-box responses.

Why this module exists
----------------------
Until now the platform could only produce Sobol' indices through one path: the
polynomial-chaos (PCE) spectral post-processing in
``backend/services/ice_resistance_uq.py::sobol_indices``.  That path requires a
*smooth, polynomial-representable* surrogate built from Gauss-Hermite
(Smolyak) quadrature over normally distributed inputs.  Any response that is
non-smooth, discontinuous, high-dimensional, or driven by non-Gaussian inputs
(ice, empirical resistance formulae, table look-ups, numerical solvers) simply
cannot be decomposed that way.  This module adds the sampling (pick-freeze)
route so that a bare ``f(x) -> y`` callable can be decomposed without a
surrogate.

Method
------
* Sampling: Saltelli (2002) pick-freeze scheme with the Saltelli et al. (2010)
  row-skip recommendation (``skip=16``) to remove the spurious correlation
  between the base and re-sampled matrices.
* First-order estimator: Saltelli et al. (2010), eq. (4):
      S_i = mean( y_B * (y_AB_i - y_A) ) / V
* Total-order estimator: Jansen (1999) as recommended in Saltelli et al. (2010),
  eq. (5):
      S_T_i = mean( (y_A - y_AB_i)^2 ) / (2 V)
* Variance: sample variance of the pooled A/B outputs (SALib convention).
* Uncertainty of the estimates: paired bootstrap over the N base rows.

Cost: ``(d + 2) * n_base`` model evaluations for all indices at once.

Analytic anchors shipped with the module
----------------------------------------
The module also carries the two standard GSA test functions *together with their
closed-form indices* so that any consumer (tests, reports, CI gates) can score a
GSA implementation instead of trusting it:

* Ishigami & Homma (1990) -- strongly non-additive, one dummy input.
* Sobol' (1993) g-function -- additive-by-construction with widely spread
  sensitivity ratio, analytic indices for arbitrary ``a``.

References
----------
Saltelli, A. (2002). Making best use of model evaluations to compute
    sensitivity indices. Comput. Phys. Commun. 145, 280-297.
Saltelli, A. et al. (2010). Variance based sensitivity analysis of model
    output. Design and estimator for the total sensitivity index.
    Comput. Phys. Commun. 181, 259-270. doi:10.1016/j.cpc.2009.09.018
Jansen, M.J.W. (1999). Analysis of variance designs for model output.
    Comput. Phys. Commun. 117, 35-43.
Ishigami, T., Homma, T. (1990). An importance quantification technique in
    uncertainty analysis for computer models. Proc. 1st ISUMA, 398-403.
Sobol', I.M. (1993). Sensitivity estimates for nonlinear mathematical models.
    Math. Model. Comput. Exp. 1, 407-414.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

import numpy as np

__all__ = [
    "SaltelliSamples",
    "SobolResult",
    "saltelli_sample",
    "sobol_indices",
    "sobol_convergence",
    "ishigami",
    "ishigami_reference",
    "sobol_g",
    "sobol_g_reference",
]

# ---------------------------------------------------------------------------
# Sampling
# ---------------------------------------------------------------------------

DEFAULT_SKIP = 16


@dataclass
class SaltelliSamples:
    """Pick-freeze design matrices.

    Attributes
    ----------
    A, B : ndarray (n, d)
        Independent base matrices in physical space.
    AB : ndarray (d, n, d)
        ``AB[i]`` is ``A`` with column ``i`` taken from ``B``.
    bounds : ndarray (d, 2)
    seed : int
    skip : int
    """

    A: np.ndarray
    B: np.ndarray
    AB: np.ndarray
    bounds: np.ndarray
    seed: int
    skip: int = DEFAULT_SKIP

    @property
    def dim(self) -> int:
        return self.A.shape[1]

    @property
    def n_base(self) -> int:
        return self.A.shape[0]

    @property
    def n_evaluations(self) -> int:
        return (self.dim + 2) * self.n_base


def _validate_bounds(bounds: Sequence[Sequence[float]]) -> np.ndarray:
    b = np.asarray(bounds, dtype=float)
    if b.ndim != 2 or b.shape[1] != 2:
        raise ValueError("bounds must have shape (d, 2) with [lower, upper] per input")
    if b.shape[0] < 1:
        raise ValueError("bounds must describe at least one input")
    if not np.all(np.isfinite(b)):
        raise ValueError("bounds must be finite")
    if np.any(b[:, 1] <= b[:, 0]):
        raise ValueError("every input needs upper > lower")
    return b


def _uniform(rng: np.random.Generator, n: int, bounds: np.ndarray, skip: int) -> np.ndarray:
    """Uniform sample in physical space; the first ``skip`` rows are discarded."""
    u = rng.random((n + skip, bounds.shape[0]))
    u = u[skip:]
    return bounds[:, 0] + u * (bounds[:, 1] - bounds[:, 0])


def saltelli_sample(
    bounds: Sequence[Sequence[float]],
    n_base: int,
    seed: int = 0,
    skip: int = DEFAULT_SKIP,
) -> SaltelliSamples:
    """Draw a Saltelli (2002) pick-freeze design with the 2010 row skip."""
    b = _validate_bounds(bounds)
    if int(n_base) < 2:
        raise ValueError("n_base must be >= 2")
    if int(skip) < 0:
        raise ValueError("skip must be >= 0")
    rng = np.random.default_rng(int(seed))
    d = b.shape[0]
    n = int(n_base)
    A = _uniform(rng, n, b, int(skip))
    B = _uniform(rng, n, b, int(skip))
    AB = np.repeat(A[None, :, :], d, axis=0)
    for i in range(d):
        AB[i, :, i] = B[:, i]
    return SaltelliSamples(A=A, B=B, AB=AB, bounds=b, seed=int(seed), skip=int(skip))


# ---------------------------------------------------------------------------
# Model evaluation (vectorised when possible, row loop otherwise)
# ---------------------------------------------------------------------------


def _evaluate(func: Callable[..., Any], X: np.ndarray) -> np.ndarray:
    """Evaluate ``func`` on the rows of ``X``.

    Vectorised call is attempted first (``f(X)`` with ``X`` of shape ``(n, d)``);
    if the callable only accepts scalars the row loop is used instead.
    """
    X = np.asarray(X, dtype=float)
    n = X.shape[0]
    try:
        y = np.asarray(func(X), dtype=float)
        if y.shape == (n,):
            return y
    except Exception:  # noqa: BLE001 - fall back to the scalar signature
        pass
    out = np.empty(n, dtype=float)
    for j in range(n):
        out[j] = float(func(*X[j]))
    return out


# ---------------------------------------------------------------------------
# Result container
# ---------------------------------------------------------------------------


@dataclass
class SobolResult:
    """Variance-based sensitivity estimates with bootstrap confidence half-widths."""

    first_order: np.ndarray
    total_order: np.ndarray
    names: List[str]
    variance: float
    mean: float
    n_base: int
    n_evaluations: int
    seed: int
    estimator: str = "saltelli2010/jansen"
    first_order_conf: Optional[np.ndarray] = None
    total_order_conf: Optional[np.ndarray] = None
    n_boot: int = 0
    confidence: float = 0.95
    first_order_jansen: Optional[np.ndarray] = None
    first_order_saltelli2010: Optional[np.ndarray] = None
    extra: Dict[str, Any] = field(default_factory=dict)

    # -- convenience -------------------------------------------------------
    def as_dict(self) -> Dict[str, Any]:
        return {
            "names": list(self.names),
            "first_order": {k: float(v) for k, v in zip(self.names, self.first_order)},
            "total_order": {k: float(v) for k, v in zip(self.names, self.total_order)},
            "first_order_conf": (
                None
                if self.first_order_conf is None
                else {k: float(v) for k, v in zip(self.names, self.first_order_conf)}
            ),
            "total_order_conf": (
                None
                if self.total_order_conf is None
                else {k: float(v) for k, v in zip(self.names, self.total_order_conf)}
            ),
            "first_order_jansen": (
                None
                if self.first_order_jansen is None
                else {k: float(v) for k, v in zip(self.names, self.first_order_jansen)}
            ),
            "variance": float(self.variance),
            "mean": float(self.mean),
            "n_base": int(self.n_base),
            "n_evaluations": int(self.n_evaluations),
            "seed": int(self.seed),
            "estimator": self.estimator,
            "n_boot": int(self.n_boot),
            "confidence": float(self.confidence),
            "sum_first_order": float(np.sum(self.first_order)),
            "sum_total_order": float(np.sum(self.total_order)),
            **self.extra,
        }

    def invariants(self, tol: Optional[float] = None) -> Dict[str, Any]:
        """Structural checks every valid decomposition must satisfy.

        Monte-Carlo estimates are noisy, so a first-order index of a
        non-influential input can come out slightly negative and the total order
        can dip below it.  ``tol`` therefore defaults to three times the largest
        bootstrap half-width when bootstrap was requested, else ``5/sqrt(N)``.
        """
        S1 = np.asarray(self.first_order, dtype=float)
        ST = np.asarray(self.total_order, dtype=float)
        if tol is None:
            confs = [c for c in (self.first_order_conf, self.total_order_conf) if c is not None]
            if confs:
                tol = 3.0 * float(max(np.max(np.abs(c)) for c in confs))
            else:
                tol = 5.0 / np.sqrt(max(self.n_base, 1))
        tol = float(tol)
        return {
            "tolerance": tol,
            "total_ge_first": bool(np.all(ST + tol >= S1)),
            "first_order_non_negative": bool(np.all(S1 >= -tol)),
            "sum_first_order_le_one": bool(np.sum(S1) <= 1.0 + tol),
            "sum_first_order": float(np.sum(S1)),
            "sum_total_order": float(np.sum(ST)),
            "interaction_share": float(max(0.0, np.sum(ST) - np.sum(S1))),
        }

    def error_vs(self, reference: Dict[str, Any]) -> Dict[str, Any]:
        """Absolute / relative error of the estimates against an analytic anchor."""
        S1 = np.asarray(reference["first_order"], dtype=float)
        ST = np.asarray(reference["total_order"], dtype=float)
        dS1 = np.abs(np.asarray(self.first_order) - S1)
        dST = np.abs(np.asarray(self.total_order) - ST)
        rel_dS1 = dS1 / np.maximum(np.abs(S1), 1e-12)
        rel_dST = dST / np.maximum(np.abs(ST), 1e-12)
        return {
            "abs_first_order": dS1.tolist(),
            "abs_total_order": dST.tolist(),
            "rel_first_order": rel_dS1.tolist(),
            "rel_total_order": rel_dST.tolist(),
            "max_abs_first_order": float(np.max(dS1)),
            "max_abs_total_order": float(np.max(dST)),
            # relative error only meaningful for influential inputs
            "max_rel_first_order": float(np.max(rel_dS1[S1 > 1e-3])) if np.any(S1 > 1e-3) else None,
            "max_rel_total_order": float(np.max(rel_dST[ST > 1e-3])) if np.any(ST > 1e-3) else None,
            "variance_rel_error": float(
                abs(self.variance - float(reference["variance"])) / float(reference["variance"])
            ),
        }


# ---------------------------------------------------------------------------
# Estimators
# ---------------------------------------------------------------------------


FIRST_ORDER_ESTIMATORS = ("saltelli2010", "jansen")


def _indices_from_outputs(
    yA: np.ndarray, yB: np.ndarray, yAB: np.ndarray, variance: float
) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Return ``(S1_saltelli2010, S1_jansen, ST_jansen)``.

    Two first-order estimators are returned because they behave differently on
    heavy-tailed responses: the Saltelli et al. (2010) product form
    ``mean(y_B (y_AB_i - y_A))`` has low variance on benign models but can
    overshoot the total-order index on skewed ones, while the Jansen (1999)
    form ``(V - mean((y_B - y_AB_i)^2)/2) / V`` stays inside ``[0, S_T]``.
    Both are computed from the same model evaluations (no extra cost).
    """
    if variance <= 0.0:
        zeros = np.zeros(yAB.shape[0])
        return zeros, zeros.copy(), zeros.copy()
    d = yAB.shape[0]
    S1_salt = np.array([np.mean(yB * (yAB[i] - yA)) for i in range(d)]) / variance
    S1_jan = np.array([variance - 0.5 * np.mean((yB - yAB[i]) ** 2) for i in range(d)]) / variance
    ST = np.array([np.mean((yA - yAB[i]) ** 2) for i in range(d)]) / (2.0 * variance)
    return S1_salt, S1_jan, ST


def sobol_indices(
    func: Callable[..., Any],
    bounds: Sequence[Sequence[float]],
    n_base: int = 1024,
    seed: int = 0,
    names: Optional[Sequence[str]] = None,
    n_boot: int = 0,
    confidence: float = 0.95,
    skip: int = DEFAULT_SKIP,
    first_order_estimator: str = "saltelli2010",
) -> SobolResult:
    """Variance-based Sobol' indices of a black-box response.

    Parameters
    ----------
    func : callable
        ``f(x) -> float`` or vectorised ``f(X) -> ndarray`` with ``X`` of shape
        ``(n, d)``.
    bounds : sequence of (lower, upper) pairs, length d
        Uniform input ranges in physical space.
    n_base : int
        Base sample size N; the design costs ``(d + 2) * N`` evaluations.
    seed : int
        Seed of the sampling RNG (identical seed -> bitwise identical design).
    names : sequence of str, optional
        Input labels for the returned dictionaries.
    n_boot : int
        Number of bootstrap resamples for the confidence half-widths (0 = off).
    confidence : float
        Bootstrap interval mass, e.g. 0.95.
    first_order_estimator : {"saltelli2010", "jansen"}
        Which first-order estimator the primary ``first_order`` column reports;
        the other one is always available as ``first_order_jansen`` /
        ``first_order_saltelli2010`` in :meth:`SobolResult.as_dict`.

    Returns
    -------
    SobolResult
    """
    design = saltelli_sample(bounds, n_base=n_base, seed=seed, skip=skip)
    d = design.dim
    if names is None:
        names = [f"x{i + 1}" for i in range(d)]
    names = list(names)
    if len(names) != d:
        raise ValueError("names must have one entry per input")

    if not (0.0 < float(confidence) < 1.0):
        raise ValueError("confidence must be in (0, 1)")
    if int(n_boot) < 0:
        raise ValueError("n_boot must be >= 0")
    if first_order_estimator not in FIRST_ORDER_ESTIMATORS:
        raise ValueError(f"first_order_estimator must be one of {FIRST_ORDER_ESTIMATORS}")

    yA = _evaluate(func, design.A)
    yB = _evaluate(func, design.B)
    yAB = np.vstack([_evaluate(func, design.AB[i]) for i in range(d)])

    pooled = np.concatenate([yA, yB])
    mean_y = float(np.mean(pooled))
    variance = float(np.var(pooled, ddof=1))
    S1_salt, S1_jan, ST = _indices_from_outputs(yA, yB, yAB, variance)
    S1 = S1_salt if first_order_estimator == "saltelli2010" else S1_jan

    S1_conf = ST_conf = None
    n_boot = int(n_boot)
    if n_boot > 0 and len(yA) >= 2:
        rng = np.random.default_rng(design.seed + 1_000_003)
        boot_S1 = np.empty((n_boot, d))
        boot_ST = np.empty((n_boot, d))
        n = len(yA)
        for b in range(n_boot):
            idx = rng.integers(0, n, n)
            yA_b, yB_b, yAB_b = yA[idx], yB[idx], yAB[:, idx]
            var_b = float(np.var(np.concatenate([yA_b, yB_b]), ddof=1))
            S1s_b, S1j_b, ST_b = _indices_from_outputs(yA_b, yB_b, yAB_b, var_b)
            boot_S1[b] = S1s_b if first_order_estimator == "saltelli2010" else S1j_b
            boot_ST[b] = ST_b
        lo = 50.0 - 100.0 * float(confidence) / 2.0
        hi = 100.0 - lo
        S1_conf = (np.percentile(boot_S1, hi, axis=0) - np.percentile(boot_S1, lo, axis=0)) / 2.0
        ST_conf = (np.percentile(boot_ST, hi, axis=0) - np.percentile(boot_ST, lo, axis=0)) / 2.0

    return SobolResult(
        first_order=np.asarray(S1, dtype=float),
        total_order=np.asarray(ST, dtype=float),
        names=names,
        variance=variance,
        mean=mean_y,
        n_base=design.n_base,
        n_evaluations=design.n_evaluations,
        seed=design.seed,
        first_order_conf=None if S1_conf is None else np.asarray(S1_conf, dtype=float),
        total_order_conf=None if ST_conf is None else np.asarray(ST_conf, dtype=float),
        n_boot=n_boot,
        confidence=float(confidence),
        first_order_jansen=np.asarray(S1_jan, dtype=float),
        first_order_saltelli2010=np.asarray(S1_salt, dtype=float),
        extra={"skip": design.skip, "dim": d, "first_order_estimator": first_order_estimator},
    )


def sobol_convergence(
    func: Callable[..., Any],
    bounds: Sequence[Sequence[float]],
    n_bases: Sequence[int] = (64, 256, 1024, 4096, 16384),
    seed: int = 0,
    names: Optional[Sequence[str]] = None,
    skip: int = DEFAULT_SKIP,
) -> List[SobolResult]:
    """Run the same decomposition on a ladder of sample sizes (convergence trace)."""
    out: List[SobolResult] = []
    for n in n_bases:
        out.append(
            sobol_indices(func, bounds, n_base=int(n), seed=seed, names=names, skip=skip)
        )
    return out


# ---------------------------------------------------------------------------
# Analytic anchors
# ---------------------------------------------------------------------------


def ishigami(x: Any) -> np.ndarray:
    """Ishigami & Homma (1990) function on the unit cube ``[-pi, pi]^3``.

    ``f = sin(x1) + a sin^2(x2) + b x3^4 sin(x1)`` with ``a = 7``, ``b = 0.1``.
    Strongly non-additive (pure 1-3 interaction) and ``x3`` has S1 = 0 but
    S_T > 0 -- the standard litmus test for total-order capability.
    """
    X = np.asarray(x, dtype=float)
    return np.sin(X[..., 0]) + 7.0 * np.sin(X[..., 1]) ** 2 + 0.1 * X[..., 2] ** 4 * np.sin(X[..., 0])


def ishigami_reference() -> Dict[str, Any]:
    """Closed-form Ishigami variance decomposition (Ishigami & Homma 1990)."""
    V = 13.8445
    return {
        "bounds": [[-np.pi, np.pi]] * 3,
        "names": ["x1", "x2", "x3"],
        "variance": V,
        "first_order": np.array([0.3138, 0.4424, 0.0]),
        "total_order": np.array([0.5575, 0.4424, 0.2439]),
        "source": "Ishigami & Homma (1990), ISUMA; values as tabulated in Saltelli et al. (2008) "
        "Global Sensitivity Analysis: The Primer, Table 2.3",
    }


def sobol_g(x: Any, a: Optional[Sequence[float]] = None) -> np.ndarray:
    """Sobol' (1993) g-function on the unit cube ``[0, 1]^d``.

    ``f = prod_i (|4 x_i - 2| + a_i) / (1 + a_i)``; additive to leading order,
    and the ``a`` vector dials each input from dominant to negligible.
    """
    X = np.asarray(x, dtype=float)
    a_arr = np.asarray([0.0, 0.5, 3.0, 9.0, 99.0] if a is None else a, dtype=float)
    if X.shape[-1] != a_arr.size:
        raise ValueError("g-function dim must equal len(a)")
    g = (np.abs(4.0 * X - 2.0) + a_arr) / (1.0 + a_arr)
    return np.prod(g, axis=-1)


def sobol_g_reference(a: Optional[Sequence[float]] = None) -> Dict[str, Any]:
    """Closed-form g-function decomposition (Sobol' 1993).

    ``V = prod_i (1 + 1/(3 (1+a_i)^2)) - 1``
    ``V_i = 1/(3 (1+a_i)^2)``, ``S_i = V_i / V``
    ``S_T_i = V_i * prod_{j != i} (1 + 1/(3 (1+a_j)^2)) / V``
    """
    a_arr = np.asarray([0.0, 0.5, 3.0, 9.0, 99.0] if a is None else a, dtype=float)
    terms = 1.0 + 1.0 / (3.0 * (1.0 + a_arr) ** 2)
    V = float(np.prod(terms) - 1.0)
    Vi = 1.0 / (3.0 * (1.0 + a_arr) ** 2)
    S1 = Vi / V
    ST = np.array(
        [Vi[i] * np.prod(np.delete(terms, i)) / V for i in range(a_arr.size)]
    )
    return {
        "bounds": [[0.0, 1.0]] * a_arr.size,
        "names": [f"x{i + 1}" for i in range(a_arr.size)],
        "variance": V,
        "first_order": S1,
        "total_order": ST,
        "a": a_arr,
        "source": "Sobol' (1993) analytic decomposition; a = [0, 0.5, 3, 9, 99] as used in "
        "Saltelli et al. (2010), CPC 181, 259-270",
    }