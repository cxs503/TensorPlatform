"""``tools.uq.pce`` -- non-intrusive polynomial-chaos expansion engine.

Public API
----------
``fit_pce(response, samples, marginals, degree) -> PCEFit``
    Least-squares (or ridge) fit of an orthonormal tensor-product chaos to black
    box data.  Because the basis is orthonormal under the input measure, the
    moments and the ANOVA (Sobol') decomposition are **closed form** from the
    coefficients -- no resampling, no extra model evaluations.

``PCEFit.predict(X)`` / ``PCEFit.moments()`` / ``PCEFit.sobol()``
    Surrogate evaluation, ``(mean, variance, std, quantiles)`` and first- /
    total-order sensitivity indices.

``leave_one_out(fit) -> dict``
    Analytic leave-one-out cross-validation error (via the hat-matrix
    shortcut), which is the surrogate error guard: ``loo_rel`` is the LOO RMSE
    normalised by the response standard deviation.

``convergence_study(response, samples, marginals, degrees, ...) -> dict``
    Degree-refinement table plus the machine-decided convergence flag
    (relative drift of mean / variance below ``tol_drift`` and LOO error below
    ``tol_loo``).  When the flag is false the caller must report
    *"degree insufficient"* instead of quoting the surrogate statistics --
    that is the negative control required by the acceptance criteria.

``adaptive_fit(response, samples, marginals, ...) -> dict``
    Walks the degree ladder and returns the smallest degree that satisfies both
    guards, or an explicit ``status='degree_insufficient'`` verdict.

Nothing in this module mentions a physical quantity: the response is any
callable ``x -> float`` injected by the caller.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

import numpy as np

from .marginals import (Marginal, multi_index, orthonormal_matrix, sample_matrix,
                        to_standard_matrix)

__all__ = [
    "PCEFit",
    "SurrogateError",
    "fit_pce",
    "leave_one_out",
    "convergence_study",
    "adaptive_fit",
    "evaluate_response",
    "DEFAULT_LOO_TOL",
    "DEFAULT_DRIFT_TOL",
]

#: surrogate error guard: leave-one-out RMSE / response std must not exceed this
DEFAULT_LOO_TOL: float = 0.05
#: convergence criterion: relative drift of the statistic between degrees
DEFAULT_DRIFT_TOL: float = 0.01


class SurrogateError(RuntimeError):
    """Raised when the surrogate guard fails and the caller asked to be protected.

    Carries the diagnostics so that a report can quote *why* the surrogate was
    rejected instead of printing numbers that are not trustworthy.
    """

    def __init__(self, message: str, diagnostics: Optional[Dict[str, Any]] = None) -> None:
        super().__init__(message)
        self.diagnostics: Dict[str, Any] = dict(diagnostics or {})


def evaluate_response(response: Callable[[Sequence[float]], float], X: np.ndarray) -> np.ndarray:
    """Evaluate a scalar black box on every row of ``X``."""
    X = np.atleast_2d(np.asarray(X, dtype=float))
    return np.asarray([float(response(row)) for row in X], dtype=float)


# --------------------------------------------------------------------------- #
# fit container
# --------------------------------------------------------------------------- #
@dataclass
class PCEFit:
    """A fitted chaos expansion plus everything needed to audit it."""

    coefficients: np.ndarray
    multi_index: np.ndarray
    marginals: Tuple[Marginal, ...]
    degree: int
    n_samples: int
    rank: int
    condition: float
    design_hat: np.ndarray
    residuals: np.ndarray
    response: np.ndarray
    ridge: float = 0.0
    _design: Optional[np.ndarray] = field(default=None, repr=False)

    # -- evaluation ---------------------------------------------------------
    def predict(self, X: np.ndarray) -> np.ndarray:
        X = np.atleast_2d(np.asarray(X, dtype=float))
        Psi = self.design(X)
        return Psi @ self.coefficients

    def design(self, X: np.ndarray) -> np.ndarray:
        Z = to_standard_matrix(X, self.marginals)
        return orthonormal_matrix(Z, self.marginals, self.degree)

    # -- closed-form moments / sensitivity ---------------------------------
    def moments(self) -> Dict[str, float]:
        c = np.asarray(self.coefficients, dtype=float)
        variance = float(np.sum(c[1:] ** 2)) if c.size > 1 else 0.0
        return {"mean": float(c[0]), "variance": variance,
                "std": math.sqrt(max(variance, 0.0))}

    def sobol(self) -> Dict[str, Dict[str, float]]:
        c = np.asarray(self.coefficients, dtype=float)
        w = c ** 2
        variance = float(np.sum(w[1:])) if c.size > 1 else 0.0
        d = len(self.marginals)
        first = np.zeros(d)
        total = np.zeros(d)
        second: Dict[str, float] = {}
        if variance > 0.0:
            for k in range(1, c.size):
                support = [j for j, a in enumerate(self.multi_index[k]) if a]
                for j in support:
                    total[j] += w[k]
                if len(support) == 1:
                    first[support[0]] += w[k]
                elif len(support) == 2:
                    key = f"{self.marginals[support[0]].name}&{self.marginals[support[1]].name}"
                    second[key] = second.get(key, 0.0) + float(w[k])
            first = first / variance
            total = total / variance
            second = {k: v / variance for k, v in second.items()}
        names = [m.name for m in self.marginals]
        return {
            "first_order": {n: float(v) for n, v in zip(names, first)},
            "total_order": {n: float(v) for n, v in zip(names, total)},
            "second_order": second,
            "variance_closure": float(np.sum(first) + np.sum(list(second.values()))),
        }

    def predict_quantiles(self, probs: Sequence[float] = (0.05, 0.5, 0.95),
                          n_surrogate: int = 200_000, seed: int = 7) -> Dict[str, float]:
        """Quantiles from cheap surrogate draws (no extra black-box calls)."""
        X = sample_matrix(self.marginals, n_surrogate, seed=seed, method="mc")
        Y = self.predict(X)
        return {f"q{p:.2f}": float(np.quantile(Y, p)) for p in probs}

    def summary(self) -> Dict[str, Any]:
        m = self.moments()
        out = {
            "degree": int(self.degree),
            "n_basis": int(self.multi_index.shape[0]),
            "n_samples": int(self.n_samples),
            "rank": int(self.rank),
            "condition": float(self.condition),
            "ridge": float(self.ridge),
            "mean": m["mean"],
            "variance": m["variance"],
            "std": m["std"],
            "marginals": [mm.detail() for mm in self.marginals],
        }
        out.update(self.sobol())
        return out


# --------------------------------------------------------------------------- #
# fitting
# --------------------------------------------------------------------------- #
def fit_pce(
    response: Callable[[Sequence[float]], float],
    samples: np.ndarray,
    marginals: Sequence[Marginal],
    degree: int,
    ridge: float = 0.0,
    response_values: Optional[np.ndarray] = None,
) -> PCEFit:
    """Fit a chaos expansion of total degree ``degree`` to ``response`` on ``samples``.

    Parameters
    ----------
    response
        Scalar black box ``x -> float`` (never called if ``response_values`` is given).
    samples
        ``(n, d)`` physical input points (the experiment design).
    marginals
        One :class:`Marginal` per input dimension.
    degree
        Total polynomial degree; must satisfy ``n_basis <= n`` for a well-posed fit.
    ridge
        Optional Tikhonov term (regression only; use 0 for interpolation-quality
        polynomial responses).
    """
    marginals = tuple(marginals)
    samples = np.atleast_2d(np.asarray(samples, dtype=float))
    if samples.shape[1] != len(marginals):
        raise ValueError(f"{len(marginals)} marginals for {samples.shape[1]} sample columns")
    Y = (np.asarray(response_values, dtype=float) if response_values is not None
         else evaluate_response(response, samples))
    Z = to_standard_matrix(samples, marginals)
    Psi = orthonormal_matrix(Z, marginals, int(degree))
    n, nb = Psi.shape
    if nb > n:
        raise ValueError(
            f"degree {degree} needs {nb} basis terms but only {n} samples were provided"
        )

    if ridge > 0.0:
        A = Psi.T @ Psi + ridge * np.eye(nb)
        coef = np.linalg.solve(A, Psi.T @ Y)
        M = np.linalg.inv(A)
    else:
        coef, *_ = np.linalg.lstsq(Psi, Y, rcond=None)
        M = np.linalg.pinv(Psi.T @ Psi)

    rank = int(np.linalg.matrix_rank(Psi))
    cond = float(np.linalg.cond(Psi))
    h = np.einsum("ij,jk,ik->i", Psi, M, Psi)
    h = np.clip(h, 0.0, 1.0)
    resid = Y - Psi @ coef
    return PCEFit(
        coefficients=coef, multi_index=multi_index(len(marginals), int(degree)),
        marginals=marginals, degree=int(degree), n_samples=int(n), rank=rank,
        condition=cond, design_hat=h, residuals=resid, response=Y,
        ridge=float(ridge), _design=Psi,
    )


def leave_one_out(fit: PCEFit) -> Dict[str, float]:
    """Analytic LOO error of the regression surrogate.

    For a linear smoother with hat values ``h_i`` the leave-one-out residual is
    ``e_i / (1 - h_i)``, so no refitting is needed.  ``loo_rel`` is normalised by
    the sample standard deviation of the response, i.e. it is a relative error
    of the surrogate in units of the output spread.
    """
    e = np.asarray(fit.residuals, dtype=float)
    denom = 1.0 - np.asarray(fit.design_hat, dtype=float)
    denom = np.where(np.abs(denom) < 1e-12, 1e-12, denom)
    loo = e / denom
    rmse = float(np.sqrt(np.mean(loo ** 2)))
    sd = float(np.std(fit.response, ddof=1)) if fit.response.size > 1 else 0.0
    rel = rmse / sd if sd > 0.0 else float("inf")
    ss_tot = float(np.sum((fit.response - fit.response.mean()) ** 2))
    r2 = 1.0 - float(np.sum(e ** 2)) / ss_tot if ss_tot > 0.0 else float("nan")
    dof = max(fit.n_samples - fit.multi_index.shape[0], 1)
    return {
        "loo_rmse": rmse,
        "loo_rel": float(rel),
        "rmse": float(np.sqrt(np.mean(e ** 2))),
        "r2": float(r2),
        "sigma2_hat": float(np.sum(e ** 2) / dof),
        "max_hat": float(np.max(fit.design_hat)),
        "n_samples": int(fit.n_samples),
    }


# --------------------------------------------------------------------------- #
# degree refinement / convergence
# --------------------------------------------------------------------------- #
def _rel_drift(new: float, old: float) -> float:
    """|new-old| / max(|new|, |old|, tiny) -- scale free statistic drift."""
    scale = max(abs(new), abs(old), 1e-12)
    return float(abs(new - old) / scale)


def convergence_study(
    response: Callable[[Sequence[float]], float],
    samples: np.ndarray,
    marginals: Sequence[Marginal],
    degrees: Sequence[int] = (2, 3, 4),
    tol_drift: float = DEFAULT_DRIFT_TOL,
    tol_loo: float = DEFAULT_LOO_TOL,
    ridge: float = 0.0,
    response_values: Optional[np.ndarray] = None,
) -> Dict[str, Any]:
    """Fit the degree ladder and decide convergence mechanically.

    A degree is *converged* when, relative to the previous degree, both the mean
    and the variance drift are below ``tol_drift`` **and** the surrogate LOO
    error is below ``tol_loo``.  The verdict is taken at the highest degree of
    the ladder: a surrogate that is still moving between degrees, or that cannot
    reproduce held-out data, must be reported as *not converged*.
    """
    degrees = [int(p) for p in degrees]
    fits: List[PCEFit] = []
    rows: List[Dict[str, Any]] = []
    for p in degrees:
        fit = fit_pce(response, samples, marginals, p, ridge=ridge,
                      response_values=response_values)
        loo = leave_one_out(fit)
        m = fit.moments()
        row: Dict[str, Any] = {"degree": p, "n_basis": int(fit.multi_index.shape[0]),
                               "mean": m["mean"], "variance": m["variance"], "std": m["std"],
                               "loo_rel": loo["loo_rel"], "loo_rmse": loo["loo_rmse"],
                               "r2": loo["r2"]}
        if rows:
            row["mean_drift"] = _rel_drift(m["mean"], rows[-1]["mean"])
            row["variance_drift"] = _rel_drift(m["variance"], rows[-1]["variance"])
        rows.append(row)
        fits.append(fit)

    final = rows[-1]
    mean_drift = float(final.get("mean_drift", float("inf")))
    variance_drift = float(final.get("variance_drift", float("inf")))
    converged = bool(mean_drift < tol_drift and variance_drift < tol_drift
                     and final["loo_rel"] < tol_loo)
    reasons: List[str] = []
    if not mean_drift < tol_drift:
        reasons.append(f"mean drift {mean_drift:.3%} >= {tol_drift:.1%}")
    if not variance_drift < tol_drift:
        reasons.append(f"variance drift {variance_drift:.3%} >= {tol_drift:.1%}")
    if not final["loo_rel"] < tol_loo:
        reasons.append(f"LOO error {final['loo_rel']:.3%} >= {tol_loo:.1%}")
    return {
        "degrees": degrees,
        "table": rows,
        "tol_drift": float(tol_drift),
        "tol_loo": float(tol_loo),
        "final_degree": int(degrees[-1]),
        "mean_drift": mean_drift,
        "variance_drift": variance_drift,
        "loo_rel": float(final["loo_rel"]),
        "converged": converged,
        "status": "converged" if converged else "degree_insufficient",
        "reasons": reasons,
        "fits": fits,
        "final_fit": fits[-1],
    }


def adaptive_fit(
    response: Callable[[Sequence[float]], float],
    samples: np.ndarray,
    marginals: Sequence[Marginal],
    min_degree: int = 1,
    max_degree: int = 6,
    tol_drift: float = DEFAULT_DRIFT_TOL,
    tol_loo: float = DEFAULT_LOO_TOL,
    ridge: float = 0.0,
    response_values: Optional[np.ndarray] = None,
) -> Dict[str, Any]:
    """Smallest degree whose moments have settled and whose LOO error passes.

    Returns ``status='converged'`` with the selected fit, or
    ``status='degree_insufficient'`` with the diagnostics of the best attempt --
    never a silently wrong number.
    """
    ladder = list(range(int(min_degree), int(max_degree) + 1))
    study = convergence_study(response, samples, marginals, ladder, tol_drift=tol_drift,
                              tol_loo=tol_loo, ridge=ridge, response_values=response_values)
    rows = study["table"]
    for i, row in enumerate(rows):
        if i == 0:
            continue
        if (row["mean_drift"] < tol_drift and row["variance_drift"] < tol_drift
                and row["loo_rel"] < tol_loo):
            return {"status": "converged", "selected_degree": row["degree"],
                    "fit": study["fits"][i], "table": rows[: i + 1],
                    "study": study}
    return {"status": "degree_insufficient", "selected_degree": None,
            "fit": study["fits"][-1], "table": rows, "study": study}


def guard_or_raise(fit: PCEFit, loo: Dict[str, float], tol_loo: float = DEFAULT_LOO_TOL) -> None:
    """Raise :class:`SurrogateError` if the LOO error exceeds ``tol_loo``."""
    if not loo["loo_rel"] < tol_loo:
        raise SurrogateError(
            f"surrogate LOO error {loo['loo_rel']:.3%} exceeds guard {tol_loo:.1%}",
            {"loo": loo, "degree": fit.degree,
             "n_basis": int(fit.multi_index.shape[0]), "n_samples": fit.n_samples},
        )