"""Linear mixed-effects model for normalised benchmark timing.

Model (as declared in the campaign node):

    T_normalized = T_raw / alpha_arch + beta . f + eps          (raw scale, eq. 1)

and the equivalent, numerically better conditioned form used for inference:

    log T = x . beta + u_host + eps,   u_host ~ N(0, sigma_u^2),  eps ~ N(0, sigma_e^2)
    => T_normalized = exp(x . beta + u_host_residual + eps) * (1 / alpha_arch)

The random intercept `u_host` absorbs host-level effects that are not the
architecture factor (scheduler/NUMA/thermal), i.e. exactly the nuisance that
would otherwise be attributed to alpha.  Variance components are estimated by
REML; with a single random intercept the marginal covariance of each group is
V_i = sigma_e^2 (I + theta * J), theta = sigma_u^2 / sigma_e^2, so the REML
criterion is available in closed form and only a 1-D search is needed.
"""
from __future__ import annotations

import math
from typing import Any, Dict, List, Sequence

import numpy as np
from scipy.optimize import minimize_scalar


def _blocks(groups: np.ndarray) -> List[np.ndarray]:
    order = np.argsort(groups, kind="stable")
    out, start = [], 0
    for i in range(1, len(order) + 1):
        if i == len(order) or groups[order[i]] != groups[order[start]]:
            out.append(order[start:i])
            start = i
    return out


def _gls_terms(X: np.ndarray, y: np.ndarray, blocks: List[np.ndarray], theta: float):
    """Return (logdetV, XtViX, XtViy, ytViy) for V = I + theta*ones within groups."""
    p = X.shape[1]
    logdet = 0.0
    XtViX = np.zeros((p, p))
    XtViy = np.zeros(p)
    ytViy = 0.0
    for idx in blocks:
        Xi, yi = X[idx], y[idx]
        ni = len(idx)
        s = theta / (1.0 + theta * ni)
        xbar = Xi.sum(axis=0)
        ybar = yi.sum()
        XtViX += Xi.T @ Xi - s * np.outer(xbar, xbar)
        XtViy += Xi.T @ yi - s * xbar * ybar
        ytViy += yi @ yi - s * ybar * ybar
        logdet += math.log1p(theta * ni)
    return logdet, XtViX, XtViy, ytViy


def _reml(theta: float, X: np.ndarray, y: np.ndarray, blocks) -> float:
    if theta <= 0.0:
        return 1e12
    logdet, XtViX, XtViy, ytViy = _gls_terms(X, y, blocks, theta)
    try:
        beta = np.linalg.solve(XtViX, XtViy)
        sign, logdetX = np.linalg.slogdet(XtViX)
    except np.linalg.LinAlgError:
        return 1e12
    rss = ytViy - beta @ XtViy
    n = len(y)
    p = X.shape[1]
    sigma2 = rss / (n - p)
    if sigma2 <= 0:
        return 1e12
    # -2 * REML log-likelihood up to an additive constant
    return logdet + logdetX + (n - p) * math.log(sigma2)


def fit_lmm(y: Sequence[float], X: Sequence[Sequence[float]], groups: Sequence[str],
            max_theta: float = 1e3) -> Dict[str, Any]:
    """Fit a single-random-intercept LMM by REML.

    Returns beta, standard errors, variance components, log-scale R^2 and the
    fitted values (BLUP of the fixed part + random intercept), i.e. everything
    the report needs without pulling a heavy dependency.
    """
    y = np.asarray(y, dtype=float)
    X = np.asarray(X, dtype=float)
    groups = np.asarray(groups)
    if X.ndim != 2 or X.shape[0] != len(y):
        raise ValueError("X/y shape mismatch")
    blocks = _blocks(groups)
    res = minimize_scalar(lambda t: _reml(math.exp(t), X, y, blocks),
                          bounds=(math.log(1e-8), math.log(max_theta)), method="bounded",
                          options={"xatol": 1e-6})
    theta = math.exp(res.x)
    logdet, XtViX, XtViy, ytViy = _gls_terms(X, y, blocks, theta)
    beta = np.linalg.solve(XtViX, XtViy)
    n, p = X.shape
    rss = float(ytViy - beta @ XtViy)
    sigma2 = rss / (n - p)
    try:
        cov = sigma2 * np.linalg.inv(XtViX)
    except np.linalg.LinAlgError:
        cov = sigma2 * np.linalg.pinv(XtViX)
    se = np.sqrt(np.maximum(np.diag(cov), 0.0))

    # BLUP of the random intercept (per group) using u_i = sigma_u^2 * 1' Vi^-1 r_i
    fitted_fixed = X @ beta
    resid = y - fitted_fixed
    sigma_u2 = theta * sigma2
    blup = {}
    for idx in blocks:
        ni = len(idx)
        s = theta / (1.0 + theta * ni)
        r_sum = resid[idx].sum()
        blup[str(groups[idx[0]])] = float(theta * r_sum / (1.0 + theta * ni))

    fitted = fitted_fixed + np.array([blup[str(g)] for g in groups])
    sse = float(((y - fitted) ** 2).sum())
    sst = float(((y - y.mean()) ** 2).sum())
    r2 = 1.0 - sse / sst if sst > 0 else float("nan")
    # marginal R^2 (fixed effects only) -- useful to see how much the arch/host
    # random effect is absorbing.
    sse_fixed = float(((y - fitted_fixed) ** 2).sum())
    r2_fixed = 1.0 - sse_fixed / sst if sst > 0 else float("nan")

    cond = float(np.linalg.cond(XtViX))
    return {
        "beta": beta.tolist(),
        "se": se.tolist(),
        "ci95_low": (beta - 1.96 * se).tolist(),
        "ci95_high": (beta + 1.96 * se).tolist(),
        "sigma_e2": float(sigma2),
        "sigma_u2": float(sigma_u2),
        "icc": float(sigma_u2 / (sigma_u2 + sigma2)),
        "theta": float(theta),
        "reml_nll": float(res.fun),
        "n": int(n), "p": int(p), "n_groups": len(blocks),
        "design_cond": cond,
        "r2_log": float(r2),
        "r2_log_fixed_only": float(r2_fixed),
        "blup": blup,
        "fitted": fitted.tolist(),
        "residuals": (y - fitted).tolist(),
    }


def design_log(features: Sequence[Dict[str, float]], names: Sequence[str] | None = None) -> tuple:
    """Design matrix for log-time: intercept + log10 of each named feature."""
    if not features:
        raise ValueError("no features")
    if names is None:
        names = sorted(features[0])
    rows = [[1.0] + [math.log10(float(f[n])) for n in names] for f in features]
    return np.asarray(rows, dtype=float), ["intercept"] + [f"log10_{n}" for n in names]


def design_raw(features: Sequence[Dict[str, float]], names: Sequence[str] | None = None) -> tuple:
    """Design matrix for the declared raw-scale model (eq. 1).

    eq. 1 is additive in (T_raw/alpha) and a linear feature term; we use the
    raw features plus their product, which is the natural linear basis for a
    cell-step product workload (work is ~ cells * steps).
    """
    if names is None:
        names = sorted(features[0])
    names = list(names)
    rows = []
    for f in features:
        base = [1.0] + [float(f[n]) for n in names]
        if len(names) >= 2:
            base.append(float(f[names[0]]) * float(f[names[1]]))
        rows.append(base)
    cols = ["intercept"] + names + ([f"{names[0]}*{names[1]}"] if len(names) >= 2 else [])
    return np.asarray(rows, dtype=float), cols


def ols(y: Sequence[float], X: Sequence[Sequence[float]]) -> Dict[str, Any]:
    y = np.asarray(y, dtype=float)
    X = np.asarray(X, dtype=float)
    beta, *_ = np.linalg.lstsq(X, y, rcond=None)
    fitted = X @ beta
    resid = y - fitted
    sse = float(resid @ resid)
    sst = float(((y - y.mean()) ** 2).sum())
    n, p = X.shape
    sigma2 = sse / max(n - p, 1)
    try:
        cov = sigma2 * np.linalg.inv(X.T @ X)
        se = np.sqrt(np.diag(cov)).tolist()
    except np.linalg.LinAlgError:
        se = [float("nan")] * p
    return {
        "beta": beta.tolist(), "se": se,
        "r2": 1.0 - sse / sst if sst > 0 else float("nan"),
        "rmse": math.sqrt(sse / n),
        "n": int(n), "p": int(p),
        "fitted": fitted.tolist(),
        "residuals": resid.tolist(),
    }


def arm_dummies(arms: Sequence[str], ref: str | None = None) -> tuple:
    """Dummy columns for arm fixed effects (reference level dropped).

    Without an arm term the arm offset is absorbed by the host random intercept
    (each arm has its own hosts), so sigma_u^2 would measure the arm effect
    instead of host nuisance -- intervals built from it become meaningless.
    With the arm term in the design, u_host is the *within-arm* host deviation.
    """
    levels = [a for a in sorted(set(arms)) if a != ref]
    if not levels:
        return np.zeros((len(arms), 0)), []
    rows = [[1.0 if a == lv else 0.0 for lv in levels] for a in arms]
    return np.asarray(rows, dtype=float), [f"arm[{lv}]" for lv in levels]


def condition_number(X: Sequence[Sequence[float]]) -> float:
    return float(np.linalg.cond(np.asarray(X, dtype=float)))