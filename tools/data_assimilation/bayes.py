"""Bayesian model updating for a digital twin (mechanism only, zero domain content).

Pipeline implemented here:

    prior  ->  likelihood  ->  posterior (MCMC)  ->  diagnostics  ->  credible interval

* **Model** -- a parametric response map ``y = f(theta, x)`` chosen from a small
  library of *shapes* (``affine``, ``power``, ``saturating``).  Which shape is used
  and what the parameters mean is decided by the caller's configuration; nothing in
  this module carries application semantics.
* **Prior** -- ``normal`` / ``uniform`` / ``lognormal`` per component, with an
  explicit support test (a proposal outside the support is rejected, never clipped).
* **Likelihood** -- independent Gaussian sensor noise with known ``sigma``
  (optionally a robust Student-t variant: a digital twin is calibrated in the field,
  where outliers are the norm).
* **Posterior** -- random-walk Metropolis with per-chain diagonal adaptation during
  burn-in, multiple chains with dispersed starts, seeded RNG.  Deterministic given
  the seed, which is what makes the reproducibility criterion checkable.
* **Diagnostics** -- split R-hat, effective sample size (initial-positive-sequence
  autocorrelation), acceptance rate, plus the posterior mean / sd / credible
  interval used by the acceptance criteria.

The sampler deliberately stays dependency-light (numpy only) so the numbers are
reproducible on every node of the fleet.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Mapping, Optional, Sequence, Tuple

import numpy as np


# ---------------------------------------------------------------- priors ----
@dataclass(frozen=True)
class Prior:
    """Independent per-component prior, declared as data."""

    kind: str                      # "normal" | "uniform" | "lognormal"
    args: Tuple[float, ...]        # normal:(mu, sd) uniform:(lo, hi) lognormal:(mu, sd)

    @classmethod
    def from_mapping(cls, spec: Mapping[str, object]) -> "Prior":
        kind = str(spec["kind"]).lower()
        args = tuple(float(v) for v in spec["args"])  # type: ignore[union-attr]
        if kind == "normal" and len(args) != 2:
            raise ValueError("normal prior needs (mu, sd)")
        if kind == "uniform" and len(args) != 2:
            raise ValueError("uniform prior needs (lo, hi)")
        if kind == "lognormal" and len(args) != 2:
            raise ValueError("lognormal prior needs (mu, sd of log)")
        if kind not in ("normal", "uniform", "lognormal"):
            raise ValueError(f"unknown prior kind {kind!r}")
        return cls(kind, args)

    def in_support(self, value: float) -> bool:
        if self.kind == "normal":
            return math.isfinite(value)
        if self.kind == "uniform":
            lo, hi = self.args
            return lo <= value <= hi
        return value > 0.0 and math.isfinite(value)

    def logpdf(self, value: float) -> float:
        if not self.in_support(value):
            return -math.inf
        if self.kind == "normal":
            mu, sd = self.args
            return -0.5 * ((value - mu) / sd) ** 2 - math.log(sd)
        if self.kind == "uniform":
            lo, hi = self.args
            return -math.log(hi - lo)
        mu, sd = self.args
        z = (math.log(value) - mu) / sd
        return -0.5 * z ** 2 - math.log(sd) - math.log(value)

    def sample(self, rng: np.random.Generator) -> float:
        if self.kind == "normal":
            return float(rng.normal(*self.args))
        if self.kind == "uniform":
            return float(rng.uniform(*self.args))
        return float(rng.lognormal(*self.args))

    def initial_scale(self) -> float:
        if self.kind == "normal":
            return 0.5 * self.args[1]
        if self.kind == "uniform":
            return 0.1 * (self.args[1] - self.args[0])
        return 0.1 * self.args[1]


# ---------------------------------------------------------------- models ----
def response_predict(kind: str, theta: Sequence[float], x: np.ndarray) -> np.ndarray:
    """Evaluate one of the declared response shapes."""
    t = np.asarray(theta, dtype=float)
    if kind == "affine":
        return t[0] + t[1] * x
    if kind == "power":
        if np.any(x <= 0.0):
            raise ValueError("power response needs x > 0")
        return t[0] + t[1] * np.power(x, t[2])
    if kind == "saturating":
        denom = t[1] + x
        return t[0] * x / np.where(np.abs(denom) < 1e-12, 1e-12, denom)
    raise ValueError(f"unknown response kind {kind!r}")


def log_likelihood(theta: Sequence[float], kind: str, x: np.ndarray, y: np.ndarray,
                   sigma: float, robust_nu: Optional[float] = None) -> float:
    """Gaussian (or Student-t) measurement-error log-likelihood."""
    if not np.all(np.isfinite(theta)):
        return -math.inf
    pred = response_predict(kind, theta, x)
    resid = (y - pred) / sigma
    if robust_nu is None:
        return float(-0.5 * np.sum(resid ** 2))
    nu = float(robust_nu)
    return float(np.sum(-0.5 * (nu + 1.0) * np.log1p(resid ** 2 / nu)))


def log_prior(theta: Sequence[float], priors: Sequence[Prior]) -> float:
    total = 0.0
    for value, prior in zip(theta, priors):
        lp = prior.logpdf(float(value))
        if lp == -math.inf:
            return -math.inf
        total += lp
    return total


def log_posterior(theta: Sequence[float], priors: Sequence[Prior], kind: str,
                  x: np.ndarray, y: np.ndarray, sigma: float,
                  robust_nu: Optional[float] = None) -> float:
    lp = log_prior(theta, priors)
    if lp == -math.inf:
        return -math.inf
    return lp + log_likelihood(theta, kind, x, y, sigma, robust_nu)


# -------------------------------------------------------------- sampler -----
@dataclass
class ChainResult:
    samples: np.ndarray           # (n_keep, d)
    accepted: int
    proposed: int
    log_post: np.ndarray

    @property
    def acceptance_rate(self) -> float:
        return self.accepted / self.proposed if self.proposed else 0.0


def matrix_sqrt(cov: np.ndarray, floor: float = 1e-300) -> np.ndarray:
    """Symmetric square root with eigenvalue flooring (robust to rank deficiency)."""
    cov = np.atleast_2d(np.asarray(cov, dtype=float))
    vals, vecs = np.linalg.eigh(0.5 * (cov + cov.T))
    vals = np.clip(vals, floor, None)
    return vecs @ np.diag(np.sqrt(vals))


def metropolis(log_post: Callable[[np.ndarray], float], start: np.ndarray,
               proposal_cov: np.ndarray, rng: np.random.Generator,
               n_steps: int, n_burn: int = 0) -> ChainResult:
    """Random-walk Metropolis with a fixed (possibly correlated) Gaussian proposal.

    ``proposal_cov`` is a ``(d, d)`` covariance matrix; a 1-D array is read as its
    diagonal.  The proposal is tuned *before* the production run (see
    :func:`tune_proposal`) so the production chains are a pure, reproducible
    Markov chain -- no adaptation inside the sampled trajectory.
    """
    d = start.size
    cov = np.asarray(proposal_cov, dtype=float)
    if cov.ndim == 1:
        cov = np.diag(cov)
    jitter = matrix_sqrt(cov, floor=1e-24)
    theta = np.array(start, dtype=float)
    lp = log_post(theta)
    if not np.isfinite(lp):
        raise ValueError("initial state has zero posterior density")
    kept: List[np.ndarray] = []
    kept_lp: List[float] = []
    accepted = proposed = 0
    for step in range(n_steps + n_burn):
        proposed += 1
        prop = theta + jitter @ rng.normal(0.0, 1.0, size=d)
        lp_prop = log_post(prop)
        if np.isfinite(lp_prop) and math.log(rng.random()) < (lp_prop - lp):
            theta, lp = prop, lp_prop
            accepted += 1
        if step >= n_burn:
            kept.append(theta.copy())
            kept_lp.append(lp)
    return ChainResult(samples=np.asarray(kept), accepted=accepted,
                       proposed=proposed, log_post=np.asarray(kept_lp))


def prior_center(prior: Prior) -> float:
    if prior.kind == "normal":
        return float(prior.args[0])
    if prior.kind == "uniform":
        lo, hi = prior.args
        return 0.5 * (lo + hi)
    return float(math.exp(prior.args[0]))


def map_estimate(log_post: Callable[[np.ndarray], float],
                 priors: Sequence[Prior]) -> np.ndarray:
    """Deterministic high-density start (Nelder-Mead; prior means as fallback).

    A field-calibrated twin has a posterior orders of magnitude tighter than the
    prior; starting a random walk at the prior mean would leave the chains in a
    region of numerically zero density.  A cheap mode search removes that failure
    mode without touching the inference itself.
    """
    x0 = np.asarray([prior_center(p) for p in priors], dtype=float)
    try:
        from scipy.optimize import minimize  # local import: optional dependency
    except Exception:  # pragma: no cover
        return x0
    obj = lambda th: -float(log_post(np.asarray(th, dtype=float)))
    if not np.isfinite(obj(x0)):
        return x0
    res = minimize(obj, x0, method="Nelder-Mead",
                   options={"xatol": 1e-10, "fatol": 1e-10, "maxiter": 20000,
                            "maxfev": 20000})
    cand = np.asarray(res.x, dtype=float)
    if np.all(np.isfinite(cand)) and np.isfinite(obj(cand)) and obj(cand) <= obj(x0):
        return cand
    return x0


def tune_proposal(log_post: Callable[[np.ndarray], float], start: np.ndarray,
                  priors: Sequence[Prior], seed: int,
                  target_accept: float = 0.30,
                  candidates: Sequence[float] = (0.0003, 0.001, 0.003, 0.01, 0.03,
                                                 0.1, 0.3, 1.0),
                  pilot_steps: int = 300,
                  shape_steps: int = 1200,
                  shape_stages: int = 2) -> Tuple[np.ndarray, Dict[str, object]]:
    """Two-stage proposal tuning: step size, then posterior *shape*.

    Stage 1 picks a diagonal scale whose pilot acceptance is near ``target_accept``.
    Stage 2 runs one pilot chain and reuses its empirical covariance scaled by
    ``2.38**2/d`` (the standard random-walk optimum).  Without stage 2 a diagonal
    proposal on a correlated posterior mixes so slowly that credible intervals are
    wrong even though the posterior mean looks fine.
    """
    best = None
    table = []
    for k, factor in enumerate(candidates):
        scale = np.asarray([max(p.initial_scale(), 1e-12) * factor for p in priors])
        rng = np.random.default_rng(seed * 1000 + k)
        res = metropolis(log_post, start, scale, rng, n_steps=pilot_steps, n_burn=0)
        rate = res.accepted / res.proposed
        table.append({"factor": float(factor), "acceptance": float(rate)})
        score = abs(rate - target_accept)
        if best is None or score < best[0]:
            best = (score, scale, rate, factor)
    assert best is not None
    diag = np.asarray(best[1], dtype=float)
    d = diag.size
    cov = np.diag(diag ** 2)
    history = []
    for stage in range(shape_stages):
        rng_s = np.random.default_rng(seed * 7919 + 13 + stage)
        run = metropolis(log_post, start, cov, rng_s, n_steps=shape_steps, n_burn=0)
        emp = np.atleast_2d(np.cov(run.samples, rowvar=False))
        cand = (2.38 ** 2 / d) * emp
        eig = np.linalg.eigvalsh(0.5 * (cand + cand.T))
        if np.all(np.isfinite(cand)) and np.min(eig) > 0.0:
            cov = cand
        history.append({"stage": stage, "acceptance": float(run.accepted / run.proposed),
                        "cov_diag": np.diag(cov).tolist()})
    return cov, {"chosen_factor": best[3], "pilot_acceptance": best[2],
                 "pilot_table": table, "target_accept": target_accept,
                 "shape_steps": shape_steps, "shape_stages": shape_stages,
                 "shape_history": history, "pilot_cov_diag": np.diag(cov).tolist()}


def _split_chains(samples: np.ndarray) -> np.ndarray:
    n, d = samples.shape
    half = n // 2
    return np.stack([samples[:half], samples[half:half * 2]], axis=0)  # (2, half, d)


def split_rhat(chains: np.ndarray) -> np.ndarray:
    """Gelman-Rubin split R-hat per parameter (chains: (n_chain, n, d))."""
    parts = np.concatenate([_split_chains(c) for c in chains], axis=0)  # (2*m, n/2, d)
    m, n, d = parts.shape
    means = parts.mean(axis=1)
    variances = parts.var(axis=1, ddof=1)
    W = variances.mean(axis=0)
    B = n * means.var(axis=0, ddof=1)
    var_hat = (n - 1) / n * W + B / n
    with np.errstate(divide="ignore", invalid="ignore"):
        rhat = np.sqrt(np.where(W > 0, var_hat / W, np.inf))
    return np.atleast_1d(rhat)


def autocorr_time(series: np.ndarray) -> float:
    """Initial-positive-sequence integrated autocorrelation time."""
    x = np.asarray(series, dtype=float)
    n = x.size
    if n < 4 or np.allclose(x, x[0]):
        return 1.0
    x = x - x.mean()
    var = float(np.dot(x, x) / n)
    if var <= 0.0:
        return 1.0
    tau = 1.0
    for lag in range(1, min(n // 2, 200)):
        rho = float(np.dot(x[:-lag], x[lag:]) / (n * var))
        if rho <= 0.0:
            break
        tau += 2.0 * rho
    return max(1.0, tau)


def ess(samples_1d: np.ndarray) -> float:
    return float(samples_1d.size / autocorr_time(samples_1d))


@dataclass
class UpdateResult:
    samples: np.ndarray                     # pooled post-burn samples (N, d)
    chains: List[ChainResult]
    mean: np.ndarray
    sd: np.ndarray
    ci_low: np.ndarray
    ci_high: np.ndarray
    rhat: np.ndarray
    ess: np.ndarray
    acceptance: List[float]
    level: float
    diagnostics: Dict[str, object] = field(default_factory=dict)

    def covers(self, truth: Sequence[float]) -> List[bool]:
        return [bool(lo <= t <= hi) for lo, t, hi in zip(self.ci_low, truth, self.ci_high)]

    def relative_error_pct(self, truth: Sequence[float]) -> List[float]:
        return [float((m - t) / t * 100.0) if t != 0 else float("nan")
                for m, t in zip(self.mean, truth)]

    def as_dict(self) -> Dict[str, object]:
        return {
            "mean": self.mean.tolist(),
            "sd": self.sd.tolist(),
            "ci_low": self.ci_low.tolist(),
            "ci_high": self.ci_high.tolist(),
            "level": self.level,
            "rhat": self.rhat.tolist(),
            "ess": self.ess.tolist(),
            "acceptance": list(self.acceptance),
            "diagnostics": self.diagnostics,
        }


def update(kind: str, x: Sequence[float], y: Sequence[float], sigma: float,
           priors: Sequence[Prior], *, seed: int = 0, n_chains: int = 3,
           n_steps: int = 3000, n_burn: int = 500, level: float = 0.95,
           robust_nu: Optional[float] = None,
           proposal_scale: Optional[Sequence[float]] = None) -> UpdateResult:
    """Full prior -> likelihood -> posterior update on one sensor record."""
    x_arr = np.asarray(x, dtype=float)
    y_arr = np.asarray(y, dtype=float)
    if x_arr.shape != y_arr.shape:
        raise ValueError("x and y must have the same shape")
    priors = list(priors)
    d = len(priors)
    lp = lambda theta: log_posterior(theta, priors, kind, x_arr, y_arr, sigma, robust_nu)
    mode = map_estimate(lp, priors)
    tuning: Dict[str, object] = {}
    if proposal_scale is None:
        proposal_scale, tuning = tune_proposal(lp, mode, priors, seed)
    cov = np.atleast_2d(np.asarray(proposal_scale, dtype=float))
    if cov.shape[0] == 1 and d > 1:      # 1-D input read as a diagonal
        cov = np.diag(cov.ravel())
    scale = np.diag(cov).copy()
    rng_master = np.random.default_rng(seed)
    chains: List[ChainResult] = []
    for c in range(n_chains):
        rng = np.random.default_rng(int(rng_master.integers(0, 2 ** 63 - 1)))
        start = mode + matrix_sqrt(cov) @ (rng.normal(0.0, 1.0, size=d) * 0.5)
        if not np.isfinite(lp(start)):
            start = mode.copy()
        chains.append(metropolis(lp, start, cov, rng, n_steps=n_steps, n_burn=n_burn))
    stacked = np.stack([c.samples for c in chains], axis=0)  # (n_chain, n_keep, d)
    pooled = stacked.reshape(-1, d)
    alpha = (1.0 - level) / 2.0
    ci_low = np.quantile(pooled, alpha, axis=0)
    ci_high = np.quantile(pooled, 1.0 - alpha, axis=0)
    return UpdateResult(
        samples=pooled,
        chains=chains,
        mean=pooled.mean(axis=0),
        sd=pooled.std(axis=0, ddof=1),
        ci_low=ci_low,
        ci_high=ci_high,
        rhat=split_rhat(stacked),
        ess=np.asarray([ess(pooled[:, j]) for j in range(d)]),
        acceptance=[c.acceptance_rate for c in chains],
        level=level,
        diagnostics={
            "n_chains": n_chains,
            "n_steps": n_steps,
            "n_burn": n_burn,
            "n_samples": int(pooled.shape[0]),
            "sigma": float(sigma),
            "response_kind": kind,
            "seed": int(seed),
            "robust_nu": robust_nu,
            "proposal_cov": cov.tolist(),
            "tuning": tuning,
            "min_ess": float(np.min([ess(pooled[:, j]) for j in range(d)])),
            "max_rhat": float(np.max(split_rhat(stacked))),
        },
    )


def coverage_study(kind: str, x: Sequence[float], truth: Sequence[float], sigma: float,
                   priors: Sequence[Prior], *, n_replicates: int = 100,
                   seed: int = 0, level: float = 0.95,
                   n_chains: int = 3, n_steps: int = 3000,
                   n_burn: int = 500) -> Dict[str, object]:
    """Monte-Carlo coverage of the credible intervals under the declared truth.

    For each replicate a fresh sensor record is simulated from ``truth`` and the
    posterior is recomputed from the *same* prior/likelihood; the fraction of
    intervals containing the truth is the reported coverage.  Per-parameter and
    pooled coverage are both returned, together with the spread of the posterior
    means (a calibration diagnostic independent of the interval test).
    """
    x_arr = np.asarray(x, dtype=float)
    truth_arr = np.asarray(truth, dtype=float)
    rng = np.random.default_rng(seed)
    covered = np.zeros(len(truth_arr), dtype=int)
    means = np.zeros((n_replicates, len(truth_arr)))
    ci_low = np.zeros((n_replicates, len(truth_arr)))
    ci_high = np.zeros((n_replicates, len(truth_arr)))
    levels = []
    for rep in range(n_replicates):
        y = response_predict(kind, truth_arr, x_arr) + rng.normal(0.0, sigma, size=x_arr.size)
        res = update(kind, x_arr, y, sigma, priors, seed=int(rng.integers(0, 2 ** 31 - 1)),
                     n_chains=n_chains, n_steps=n_steps, n_burn=n_burn, level=level)
        covered += np.asarray(res.covers(truth_arr), dtype=int)
        means[rep] = res.mean
        ci_low[rep] = res.ci_low
        ci_high[rep] = res.ci_high
        levels.append(res.diagnostics["max_rhat"])
    return {
        "kind": kind,
        "n_replicates": n_replicates,
        "level": level,
        "coverage_per_parameter": (covered / n_replicates).tolist(),
        "coverage_pooled": float(covered.sum() / (n_replicates * len(truth_arr))),
        "mean_bias_pct": ((means.mean(axis=0) - truth_arr) / truth_arr * 100.0).tolist(),
        "mean_spread_pct": (means.std(axis=0, ddof=1) / truth_arr * 100.0).tolist(),
        "max_rhat_median": float(np.median(levels)),
        "replicate_mean": means.tolist(),
        "replicate_ci_low": ci_low.tolist(),
        "replicate_ci_high": ci_high.tolist(),
        "replicate_covered": (covered / max(n_replicates, 1)).tolist(),
        "truth": truth_arr.tolist(),
        "sigma": float(sigma),
        "seed": int(seed),
    }