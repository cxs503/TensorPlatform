"""``tools.uq.mc`` -- Monte Carlo reference and a model-free Sobol' estimator.

The Monte Carlo block is the *truth* against which the chaos surrogate is
scored: ``10**4`` i.i.d. draws give the mean, variance, standard deviation and
output quantiles, each with its own analytic standard error so that a reported
discrepancy can be attributed either to the surrogate or to the sampler.

The Sobol' block implements the Saltelli / Jansen pick-freeze estimator
(variance based, no surrogate), which is statistically independent of the PCE
coefficient decomposition and therefore serves as a cross-check of the
sensitivity indices -- two different estimators agreeing is evidence, one
estimator agreeing with itself is not.

Domain free: only a callable response and its input laws are accepted.
"""

from __future__ import annotations

import math
from typing import Any, Callable, Dict, Optional, Sequence, Tuple

import numpy as np

from .marginals import Marginal, sample_matrix

__all__ = ["monte_carlo", "sobol_mc", "compare_moments"]

DEFAULT_N_MC: int = 10_000


# --------------------------------------------------------------------------- #
# moments
# --------------------------------------------------------------------------- #
def monte_carlo(
    response: Callable[[Sequence[float]], float],
    marginals: Sequence[Marginal],
    n_samples: int = DEFAULT_N_MC,
    seed: int = 20260101,
    probs: Sequence[float] = (0.05, 0.5, 0.95),
) -> Dict[str, Any]:
    """Monte Carlo moments of ``response`` under the product measure.

    Sampling is plain i.i.d. (``method='mc'``) so that the standard errors below
    are the textbook ones: ``se(mean) = s/sqrt(n)``,
    ``se(var) ~= var * sqrt((kappa-1)/n)`` with ``kappa`` the empirical kurtosis.
    """
    from .pce import evaluate_response  # local import: avoid a cycle

    n_samples = int(n_samples)
    X = sample_matrix(marginals, n_samples, seed=seed, method="mc")
    Y = evaluate_response(response, X)
    mu = float(np.mean(Y))
    var = float(np.var(Y, ddof=1)) if n_samples > 1 else 0.0
    sd = math.sqrt(max(var, 0.0))
    se_mean = sd / math.sqrt(n_samples)
    # delta-method standard error of the variance estimator
    centred = Y - mu
    kurt = float(np.mean(centred ** 4) / (var ** 2)) if var > 0.0 else float("nan")
    se_var = var * math.sqrt(max(kurt - 1.0, 0.0) / n_samples) if var > 0.0 else float("nan")
    quant = {f"q{p:.2f}": float(np.quantile(Y, p)) for p in probs}
    return {
        "n_samples": n_samples,
        "seed": int(seed),
        "mean": mu,
        "variance": var,
        "std": sd,
        "se_mean": float(se_mean),
        "se_var": float(se_var),
        "kurtosis": kurt,
        "quantiles": quant,
        "min": float(np.min(Y)),
        "max": float(np.max(Y)),
    }


def compare_moments(pce: Dict[str, float], mc: Dict[str, Any]) -> Dict[str, Any]:
    """Relative discrepancies between surrogate and Monte Carlo moments.

    Relative errors use ``max(|reference|, tiny)`` so they are finite for a
    zero mean; the comparison also reports how many standard errors apart the
    two estimates are, which is the honest way to read a small discrepancy.
    """
    mu_ref = float(mc["mean"])
    var_ref = float(mc["variance"])
    mu_err = abs(pce["mean"] - mu_ref) / max(abs(mu_ref), 1e-12)
    var_err = abs(pce["variance"] - var_ref) / max(abs(var_ref), 1e-12)
    sd_scale = max(float(mc.get("se_mean", 0.0)), 1e-30)
    var_scale = max(float(mc.get("se_var", 0.0)), 1e-30)
    return {
        "mean_rel_error": float(mu_err),
        "variance_rel_error": float(var_err),
        "mean_abs_error": float(abs(pce["mean"] - mu_ref)),
        "variance_abs_error": float(abs(pce["variance"] - var_ref)),
        "mean_z": float(abs(pce["mean"] - mu_ref) / sd_scale),
        "variance_z": float(abs(pce["variance"] - var_ref) / var_scale),
    }


# --------------------------------------------------------------------------- #
# variance-based sensitivity without a surrogate
# --------------------------------------------------------------------------- #
def sobol_mc(
    response: Callable[[Sequence[float]], float],
    marginals: Sequence[Marginal],
    n_base: int = 8192,
    seed: int = 20260102,
) -> Dict[str, Any]:
    """Saltelli / Jansen pick-freeze first- and total-order Sobol' indices.

    Cost: ``n_base * (d + 2)`` model evaluations.  Estimators (Jansen 1999)::

        S_i  = mean(f_B * (f_AB_i - f_A)) / Var
        S_Ti = mean((f_A - f_AB_i)^2) / (2 Var)

    ``f_A`` and ``f_B`` are evaluated on two independent designs, and ``f_AB_i``
    on the design where column ``i`` of ``A`` is replaced by column ``i`` of
    ``B``.  First-order indices are clipped at zero when reporting a negative
    estimate (they are unbiased but noisy around a true zero).
    """
    from .pce import evaluate_response  # local import: avoid a cycle

    marginals = tuple(marginals)
    d = len(marginals)
    n_base = int(n_base)
    A = sample_matrix(marginals, n_base, seed=seed, method="mc")
    B = sample_matrix(marginals, n_base, seed=seed + 1, method="mc")
    fA = evaluate_response(response, A)
    fB = evaluate_response(response, B)
    pooled = np.concatenate([fA, fB])
    var = float(np.var(pooled, ddof=1))
    first = np.zeros(d)
    total = np.zeros(d)
    for i in range(d):
        AB = A.copy()
        AB[:, i] = B[:, i]
        fAB = evaluate_response(response, AB)
        if var > 0.0:
            first[i] = float(np.mean(fB * (fAB - fA)) / var)
            total[i] = float(np.mean((fA - fAB) ** 2) / (2.0 * var))
    names = [m.name for m in marginals]
    # bootstrap-free uncertainty: the Jansen estimator's own variance across
    # two independent half-splits of the base design
    half = n_base // 2
    first_split: Dict[str, list] = {n: [] for n in names}
    total_split: Dict[str, list] = {n: [] for n in names}
    if half >= 16:
        for i in range(d):
            AB = A.copy()
            AB[:, i] = B[:, i]
            fAB = evaluate_response(response, AB)
            for sl in (slice(0, half), slice(half, 2 * half)):
                v = float(np.var(np.concatenate([fA[sl], fB[sl]]), ddof=1))
                if v > 0.0:
                    first_split[names[i]].append(float(np.mean(fB[sl] * (fAB[sl] - fA[sl])) / v))
                    total_split[names[i]].append(float(np.mean((fA[sl] - fAB[sl]) ** 2) / (2 * v)))
    return {
        "n_base": n_base,
        "n_evaluations": int(n_base * (d + 2)),
        "variance": var,
        "first_order": {n: float(v) for n, v in zip(names, first)},
        "total_order": {n: float(v) for n, v in zip(names, total)},
        "first_order_clipped": {n: float(max(v, 0.0)) for n, v in zip(names, first)},
        "split_range_first": {n: (min(v), max(v)) if v else None
                              for n, v in first_split.items()},
        "split_range_total": {n: (min(v), max(v)) if v else None
                              for n, v in total_split.items()},
        "estimator": "jansen_1999_pick_freeze",
    }