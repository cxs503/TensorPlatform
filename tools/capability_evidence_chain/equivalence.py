"""Uncertainty-aware equivalence testing (TOST) with power / MDE sensitivity.

Why this module exists
----------------------
The platform historically certified solver capabilities with a *point* rule
``relative error < 15 %``.  A point rule has no notion of sampling noise: a
single lucky run and a 20-run campaign are treated identically, and a
"PASS" says nothing about what the test could have detected.  This module
replaces the point rule by a two one-sided tests (TOST) equivalence decision
plus sensitivity numbers:

* ``power``  — probability to *correctly* declare equivalence, 1 - beta;
* ``MDE``    — the largest true deviation that the design can still certify
  at the target power ("minimum detectable effect" in the equivalence
  sense: true deviations above MDE are no longer certifiable, so a margin
  wider than MDE means the certificate is only nominal).

Three statistical scenarios are implemented, all with closed-form or
quadrature evaluation and Monte-Carlo validation (see ``tests``):

===============  =========================================================
scenario         null / alternative
===============  =========================================================
``mean``         H0: |mu| >= margin   vs  H1: |mu| < margin  (one or two
                 sample / paired t)
``variance``     H0: ratio outside (1/theta, theta) vs H1: inside
                 (F based, dual interval on the variance ratio)
``distribution`` H0: sup|F1-F2| >= margin vs H1: < margin (bootstrap
                 interval-inclusion test on the two-sample KS distance)
===============  =========================================================

Design notes
------------
* No domain semantics: margins, alpha, seeds, group definitions all arrive
  from configuration; this module only sees numbers.
* Every routine is deterministic given ``seed`` and returns the confidence
  interval that is *dual* to the test, so a certificate can be re-checked by
  interval inclusion rather than by trusting a p-value.
* ``power_mean_one_sample`` / ``power_variance_ratio`` are exact up to
  numerical quadrature of the chi-square sampling distribution of ``s``;
  both are validated against Monte-Carlo in ``tests/test_equivalence.py``
  (tolerance: 3 Monte-Carlo standard errors).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from functools import lru_cache
from typing import Any, Callable, Dict, Optional, Sequence

import numpy as np
from scipy import stats

__all__ = [
    "TostResult",
    "clamp",
    "tost_mean_one_sample",
    "tost_mean_two_sample",
    "tost_paired",
    "tost_variance_ratio",
    "tost_distribution_ks",
    "power_mean_one_sample",
    "power_mean_two_sample",
    "power_variance_ratio",
    "power_distribution_ks",
    "mde_mean_one_sample",
    "mde_variance_ratio",
    "mde_distribution_ks",
    "mde_generic",
    "n_required_mean",
]


# --------------------------------------------------------------------------
# result container
# --------------------------------------------------------------------------
@dataclass
class TostResult:
    """Outcome of a TOST equivalence decision plus its dual interval."""

    scenario: str
    equivalent: bool
    p_value: float
    alpha: float
    margin: float
    estimate: float
    ci_low: float
    ci_high: float
    n: float
    df: float = float("nan")
    power: Optional[float] = None
    mde: Optional[float] = None
    decision: str = ""
    notes: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.decision:
            self.decision = "EQUIVALENT" if self.equivalent else "NOT_EQUIVALENT"

    def to_dict(self) -> Dict[str, Any]:
        out = {
            "scenario": self.scenario,
            "equivalent": bool(self.equivalent),
            "decision": self.decision,
            "p_value": _num(self.p_value),
            "alpha": float(self.alpha),
            "margin": float(self.margin),
            "estimate": _num(self.estimate),
            "ci_low": _num(self.ci_low),
            "ci_high": _num(self.ci_high),
            "n": _num(self.n),
            "df": _num(self.df),
            "power": _num(self.power) if self.power is not None else None,
            "mde": _num(self.mde) if self.mde is not None else None,
        }
        if self.notes:
            out["notes"] = {k: _num(v) if isinstance(v, (int, float)) else v
                            for k, v in self.notes.items()}
        return out


def _num(x: Any) -> Optional[float]:
    """JSON-safe float (NaN/inf -> None, so artefacts never carry NaN)."""
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    if not np.isfinite(v):
        return None
    return v


def clamp(x: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return float(min(hi, max(lo, x)))


def _as_array(x: Sequence[float]) -> np.ndarray:
    a = np.asarray([float(v) for v in x if v is not None], dtype=float)
    a = a[np.isfinite(a)]
    if a.size == 0:
        raise ValueError("empty sample")
    return a


# --------------------------------------------------------------------------
# scenario 1a: mean equivalence, one sample
# --------------------------------------------------------------------------
def tost_mean_one_sample(
    sample: Sequence[float],
    margin: float,
    alpha: float = 0.05,
    power_target: float = 0.80,
    compute_sensitivity: bool = True,
) -> TostResult:
    """TOST on the mean of ``sample`` against ``|mu| < margin``.

    The two one-sided hypotheses are ``H0_lower: mu <= -margin`` and
    ``H0_upper: mu >= +margin``; equivalence is declared when both one-sided
    t-tests reject at level ``alpha``, which is exactly the statement that the
    ``1 - 2 alpha`` confidence interval lies inside ``(-margin, margin)``.
    """
    x = _as_array(sample)
    n = int(x.size)
    mean = float(x.mean())
    sd = float(x.std(ddof=1)) if n > 1 else 0.0
    if n < 2 or sd == 0.0:
        eq = abs(mean) < margin
        return TostResult(
            scenario="mean_one_sample",
            equivalent=False,
            p_value=float("nan"),
            alpha=alpha,
            margin=margin,
            estimate=mean,
            ci_low=mean,
            ci_high=mean,
            n=n,
            df=float(n - 1),
            decision="WITHHELD_NO_SPREAD",
            notes={
                "reason": "sample size < 2 or zero spread: sampling uncertainty "
                          "not estimable, equivalence cannot be certified",
                "point_inside_margin": bool(eq),
            },
        )
    df = float(n - 1)
    se = sd / np.sqrt(n)
    t_crit = float(stats.t.ppf(1.0 - alpha, df))
    p_low = float(stats.t.sf((mean + margin) / se, df))
    p_up = float(stats.t.sf((margin - mean) / se, df))
    ci_low = mean - t_crit * se
    ci_high = mean + t_crit * se
    res = TostResult(
        scenario="mean_one_sample",
        equivalent=bool(ci_low > -margin and ci_high < margin),
        p_value=max(p_low, p_up),
        alpha=alpha,
        margin=margin,
        estimate=mean,
        ci_low=ci_low,
        ci_high=ci_high,
        n=n,
        df=df,
        notes={"sd": sd, "se": float(se), "p_lower": p_low, "p_upper": p_up},
    )
    if compute_sensitivity:
        res.power = power_mean_one_sample(n, sd, mean, margin, alpha)
        res.mde = mde_mean_one_sample(n, sd, margin, alpha, power_target)
    return res


# --------------------------------------------------------------------------
# scenario 1b: mean equivalence, two samples (Welch) and paired
# --------------------------------------------------------------------------
def tost_mean_two_sample(
    sample_a: Sequence[float],
    sample_b: Sequence[float],
    margin: float,
    alpha: float = 0.05,
    compute_sensitivity: bool = True,
    power_target: float = 0.80,
) -> TostResult:
    """Welch two-sample TOST on ``mean_a - mean_b`` against ``|d| < margin``."""
    a = _as_array(sample_a)
    b = _as_array(sample_b)
    n1, n2 = int(a.size), int(b.size)
    est = float(a.mean() - b.mean())
    s1 = float(a.std(ddof=1)) if n1 > 1 else 0.0
    s2 = float(b.std(ddof=1)) if n2 > 1 else 0.0
    se = float(np.sqrt(s1 ** 2 / n1 + s2 ** 2 / n2)) if n1 and n2 else float("nan")
    if n1 < 2 or n2 < 2 or se == 0.0 or not np.isfinite(se):
        return TostResult(
            scenario="mean_two_sample",
            equivalent=False,
            p_value=float("nan"),
            alpha=alpha,
            margin=margin,
            estimate=est,
            ci_low=est,
            ci_high=est,
            n=n1 + n2,
            decision="WITHHELD_NO_SPREAD",
            notes={"reason": "need >= 2 observations per group with spread"},
        )
    num = (s1 ** 2 / n1 + s2 ** 2 / n2) ** 2
    den = (s1 ** 2 / n1) ** 2 / (n1 - 1) + (s2 ** 2 / n2) ** 2 / (n2 - 1)
    df = float(num / den) if den > 0 else float(n1 + n2 - 2)
    t_crit = float(stats.t.ppf(1.0 - alpha, df))
    p_low = float(stats.t.sf((est + margin) / se, df))
    p_up = float(stats.t.sf((margin - est) / se, df))
    ci_low, ci_high = est - t_crit * se, est + t_crit * se
    res = TostResult(
        scenario="mean_two_sample",
        equivalent=bool(ci_low > -margin and ci_high < margin),
        p_value=max(p_low, p_up),
        alpha=alpha,
        margin=margin,
        estimate=est,
        ci_low=ci_low,
        ci_high=ci_high,
        n=n1 + n2,
        df=df,
        notes={"n_a": n1, "n_b": n2, "sd_a": s1, "sd_b": s2, "se": se},
    )
    if compute_sensitivity:
        res.power = power_mean_two_sample(n1, n2, s1, s2, est, margin, alpha)
        res.mde = mde_generic(
            lambda mu: power_mean_two_sample(n1, n2, s1, s2, mu, margin, alpha),
            margin,
            power_target,
        )
    return res


def tost_paired(
    sample_a: Sequence[float],
    sample_b: Sequence[float],
    margin: float,
    alpha: float = 0.05,
    compute_sensitivity: bool = True,
    power_target: float = 0.80,
) -> TostResult:
    """Paired TOST: one-sample TOST on the paired differences."""
    a = _as_array(sample_a)
    b = _as_array(sample_b)
    if a.size != b.size or a.size < 2:
        raise ValueError("paired TOST needs equal length samples with n >= 2")
    res = tost_mean_one_sample(a - b, margin, alpha, power_target, compute_sensitivity)
    res.scenario = "mean_paired"
    return res


# --------------------------------------------------------------------------
# scenario 2: variance equivalence (ratio within 1/theta .. theta)
# --------------------------------------------------------------------------
def tost_variance_ratio(
    sample_a: Sequence[float],
    sample_b: Sequence[float],
    theta: float = 1.5,
    alpha: float = 0.05,
    compute_sensitivity: bool = True,
    power_target: float = 0.80,
) -> TostResult:
    """TOST on ``var_a / var_b`` against the interval ``(1/theta, theta)``.

    Upper null ``H0: ratio >= theta`` is rejected when
    ``(v_a/theta)/v_b < F_{1-alpha}(df1, df2)``; lower null
    ``H0: ratio <= 1/theta`` is rejected when ``(v_a*theta)/v_b >
    F_{alpha}(df1, df2)``.  Both rejections are equivalent to the dual
    ``1 - 2 alpha`` interval for the ratio lying inside ``(1/theta, theta)``.
    """
    a = _as_array(sample_a)
    b = _as_array(sample_b)
    n1, n2 = int(a.size), int(b.size)
    if n1 < 2 or n2 < 2:
        return TostResult(
            scenario="variance_ratio",
            equivalent=False,
            p_value=float("nan"),
            alpha=alpha,
            margin=theta,
            estimate=float("nan"),
            ci_low=float("nan"),
            ci_high=float("nan"),
            n=n1 + n2,
            decision="WITHHELD_SMALL_SAMPLE",
            notes={"reason": "variance TOST needs n >= 2 per group"},
        )
    v1 = float(a.var(ddof=1))
    v2 = float(b.var(ddof=1))
    df1, df2 = n1 - 1, n2 - 1
    if v1 <= 0.0 or v2 <= 0.0:
        return TostResult(
            scenario="variance_ratio",
            equivalent=False,
            p_value=float("nan"),
            alpha=alpha,
            margin=theta,
            estimate=float("nan"),
            ci_low=float("nan"),
            ci_high=float("nan"),
            n=n1 + n2,
            df=float(df1 + df2),
            decision="WITHHELD_ZERO_VARIANCE",
            notes={"reason": "degenerate (zero) variance in a group"},
        )
    ratio = v1 / v2
    p_up = float(stats.f.cdf((v1 / theta) / v2, df1, df2))
    p_lo = float(stats.f.sf((v1 * theta) / v2, df1, df2))
    ci_low = ratio / float(stats.f.ppf(1.0 - alpha, df1, df2))
    ci_high = ratio * float(stats.f.ppf(1.0 - alpha, df2, df1))
    res = TostResult(
        scenario="variance_ratio",
        equivalent=bool(ci_low > 1.0 / theta and ci_high < theta),
        p_value=max(p_lo, p_up),
        alpha=alpha,
        margin=theta,
        estimate=ratio,
        ci_low=ci_low,
        ci_high=ci_high,
        n=n1 + n2,
        df=float(df1 + df2),
        notes={"n_a": n1, "n_b": n2, "var_a": v1, "var_b": v2, "theta": theta,
               "p_lower": p_lo, "p_upper": p_up},
    )
    if compute_sensitivity:
        res.power = power_variance_ratio(n1, n2, float(np.sqrt(ratio)), theta, alpha)
        res.mde = mde_variance_ratio(n1, n2, theta, alpha, power_target)
    return res


# --------------------------------------------------------------------------
# scenario 3: distribution equivalence (bootstrap KS interval inclusion)
# --------------------------------------------------------------------------
def _ks_distance(a: np.ndarray, b: np.ndarray) -> float:
    """Two-sample Kolmogorov-Smirnov distance (exact, ``O(n log n)``)."""
    a = np.sort(np.asarray(a, dtype=float))
    b = np.sort(np.asarray(b, dtype=float))
    grid = np.concatenate([a, b])
    cdf_a = np.searchsorted(a, grid, side="right") / a.size
    cdf_b = np.searchsorted(b, grid, side="right") / b.size
    return float(np.max(np.abs(cdf_a - cdf_b)))


def tost_distribution_ks(
    sample_a: Sequence[float],
    sample_b: Sequence[float],
    margin: float,
    alpha: float = 0.05,
    n_boot: int = 2000,
    seed: int = 20261111,
    compute_sensitivity: bool = False,
    power_target: float = 0.80,
) -> TostResult:
    """Equivalence of two distributions via a bootstrap interval on the KS distance.

    H0: ``sup_x |F_a(x) - F_b(x)| >= margin`` (not equivalent) versus
    H1: ``< margin``.  A two-sided ``1 - 2 alpha`` bootstrap interval of the
    KS distance is formed by resampling each group separately; equivalence is
    declared when the upper limit stays below ``margin`` — the
    interval-inclusion ("TOST") form for a distributional functional, the
    direct analogue of the t interval used in the mean scenario.
    """
    a = _as_array(sample_a)
    b = _as_array(sample_b)
    n1, n2 = int(a.size), int(b.size)
    if n1 < 3 or n2 < 3:
        return TostResult(
            scenario="distribution_ks",
            equivalent=False,
            p_value=float("nan"),
            alpha=alpha,
            margin=margin,
            estimate=float("nan"),
            ci_low=float("nan"),
            ci_high=float("nan"),
            n=n1 + n2,
            decision="WITHHELD_SMALL_SAMPLE",
            notes={"reason": "distribution TOST needs n >= 3 per group"},
        )
    d_hat = _ks_distance(a, b)
    rng = np.random.default_rng(seed)
    boots = np.empty(int(n_boot), dtype=float)
    for i in range(int(n_boot)):
        boots[i] = _ks_distance(
            rng.choice(a, size=n1, replace=True), rng.choice(b, size=n2, replace=True)
        )
    ci_low = float(np.quantile(boots, alpha))
    ci_high = float(np.quantile(boots, 1.0 - alpha))
    res = TostResult(
        scenario="distribution_ks",
        equivalent=bool(ci_high < margin),
        p_value=float(np.mean(boots >= margin)) if boots.size else float("nan"),
        alpha=alpha,
        margin=margin,
        estimate=d_hat,
        ci_low=ci_low,
        ci_high=ci_high,
        n=n1 + n2,
        notes={"n_a": n1, "n_b": n2, "n_boot": int(n_boot), "seed": int(seed),
               "p_value_meaning": "bootstrap P(D* >= margin); reported for "
                                  "transparency, the decision uses interval "
                                  "inclusion"},
    )
    if compute_sensitivity:
        # the decision uses the full bootstrap above; the sensitivity numbers
        # use a coarser bootstrap because they are Monte-Carlo estimates whose
        # own error (reported in the artefact) dominates at this resolution
        sens_boot = min(int(n_boot), 400)
        res.power = power_distribution_ks(
            n1, n2, d_hat, margin, alpha, n_boot=sens_boot, n_sim=120, seed=seed
        )
        res.mde = mde_distribution_ks(
            n1, n2, margin, alpha, power_target, n_boot=150, seed=seed
        )
    return res


# --------------------------------------------------------------------------
# power
# --------------------------------------------------------------------------
def power_mean_one_sample(
    n: int,
    sd: float,
    true_mean: float,
    margin: float,
    alpha: float = 0.05,
    n_grid: int = 201,
) -> float:
    """Exact (quadrature) power of the one-sample TOST under normality.

    Conditional on ``s``, both one-sided tests reject iff

        c*s/sqrt(n) - margin <= xbar <= margin - c*s/sqrt(n)

    with ``c = t_{1-alpha, n-1}``.  Integrating that normal probability over
    the chi-square law of ``s`` gives the power without simulation.
    """
    n = int(n)
    if n < 2 or sd <= 0.0:
        return 0.0
    df = n - 1
    c = float(stats.t.ppf(1.0 - alpha, df))
    # xbar | s ~ N(mean, sigma^2/n): the *true* sigma enters the normal scale,
    # while the observed s only enters through the rejection boundary.
    scale = sd / np.sqrt(n)
    q = (np.arange(n_grid) + 0.5) / n_grid
    s_vals = np.sqrt(stats.chi2.ppf(q, df) / df) * sd
    hi = margin - c * s_vals / np.sqrt(n)
    lo = -margin + c * s_vals / np.sqrt(n)
    p_cond = stats.norm.cdf((hi - true_mean) / scale) - stats.norm.cdf(
        (lo - true_mean) / scale
    )
    p_cond = np.nan_to_num(np.clip(p_cond, 0.0, 1.0), nan=0.0)
    return clamp(float(np.mean(p_cond)))


def power_mean_two_sample(
    n1: int,
    n2: int,
    sd1: float,
    sd2: float,
    true_diff: float,
    margin: float,
    alpha: float = 0.05,
    n_grid: int = 81,
) -> float:
    """Two-sample (Welch) TOST power by 2-D quadrature over both ``s1, s2``.

    Welch's df is fixed at its expected value (computed from the true sds),
    the usual large-sample rendering; residuals are checked against
    Monte-Carlo in the tests.
    """
    if n1 < 2 or n2 < 2 or sd1 <= 0.0 or sd2 <= 0.0:
        return 0.0
    df1, df2 = n1 - 1, n2 - 1
    df = (sd1 ** 2 / n1 + sd2 ** 2 / n2) ** 2 / (
        (sd1 ** 2 / n1) ** 2 / df1 + (sd2 ** 2 / n2) ** 2 / df2
    )
    c = float(stats.t.ppf(1.0 - alpha, df))
    q = (np.arange(n_grid) + 0.5) / n_grid
    s1 = np.sqrt(stats.chi2.ppf(q, df1) / df1) * sd1
    s2 = np.sqrt(stats.chi2.ppf(q, df2) / df2) * sd2
    total = 0.0
    se_true = float(np.sqrt(sd1 ** 2 / n1 + sd2 ** 2 / n2))
    for a in s1:
        se_obs = np.sqrt(a ** 2 / n1 + s2 ** 2 / n2)   # boundary scale
        hi = margin - c * se_obs
        lo = -margin + c * se_obs
        p = stats.norm.cdf((hi - true_diff) / se_true) - stats.norm.cdf(
            (lo - true_diff) / se_true
        )
        total += float(np.mean(np.clip(p, 0.0, 1.0)))
    return clamp(total / n_grid)


def power_variance_ratio(
    n1: int,
    n2: int,
    true_ratio: float,
    theta: float,
    alpha: float = 0.05,
    n_grid: int = 401,
) -> float:
    """Exact (quadrature) power of the variance-ratio TOST.

    Equivalence requires the dual ``1 - 2 alpha`` interval of the variance
    ratio to sit inside ``(1/theta, theta)``.  Conditional on ``s2^2`` that is

        s2^2 * F_{1-alpha}(df1,df2) / theta  <  s1^2
                                             <  s2^2 * theta / F_{1-alpha}(df2,df1)

    and ``df1 * s1^2 / sigma1^2`` is chi-square with ``df1`` degrees of freedom
    (``sigma1^2 = true_ratio`` in units of ``sigma2^2 = 1``).
    """
    if n1 < 2 or n2 < 2 or true_ratio <= 0.0 or theta <= 1.0:
        return 0.0
    df1, df2 = n1 - 1, n2 - 1
    f_upper = float(stats.f.ppf(1.0 - alpha, df1, df2))   # for the ratio CI
    f_recip = float(stats.f.ppf(1.0 - alpha, df2, df1))   # for the reciprocal
    q = (np.arange(n_grid) + 0.5) / n_grid
    s2sq = stats.chi2.ppf(q, df2) / df2                   # units of sigma2^2 = 1
    lo = s2sq * f_upper / theta
    hi = s2sq * theta / f_recip
    cdf_hi = stats.chi2.cdf(hi * df1 / true_ratio, df1)
    cdf_lo = stats.chi2.cdf(lo * df1 / true_ratio, df1)
    return clamp(float(np.mean(np.clip(cdf_hi - cdf_lo, 0.0, 1.0))))


def power_distribution_ks(
    n1: int,
    n2: int,
    true_distance: float,
    margin: float,
    alpha: float,
    n_boot: int = 400,
    n_sim: int = 200,
    seed: int = 20261111,
) -> float:
    """Monte-Carlo power for the bootstrap KS interval-inclusion test.

    Both groups are normal with a location shift tuned so that the population
    sup-CDF distance equals ``true_distance`` (controlled analytically), so
    the simulation isolates the effect of the sampling design.
    """
    if n1 < 3 or n2 < 3 or true_distance < 0.0:
        return 0.0
    rng = np.random.default_rng(seed)
    shift = _normal_shift_for_ks_distance(true_distance)
    hits = 0
    for _ in range(int(n_sim)):
        a = rng.normal(0.0, 1.0, n1)
        b = rng.normal(shift, 1.0, n2)
        boots = np.array(
            [
                _ks_distance(rng.choice(a, n1, True), rng.choice(b, n2, True))
                for _ in range(int(n_boot))
            ]
        )
        if float(np.quantile(boots, 1.0 - alpha)) < margin:
            hits += 1
    return clamp(hits / float(n_sim))


@lru_cache(maxsize=256)
def _normal_shift_for_ks_distance(distance: float) -> float:
    """Location shift ``s`` such that sup_x |Phi(x-s) - Phi(x)| = distance."""
    if distance <= 0.0:
        return 0.0
    if distance >= 1.0:
        return 50.0
    xs = np.linspace(-12.0, 12.0, 4001)

    def gap(s: float) -> float:
        return float(np.max(np.abs(stats.norm.cdf(xs - s) - stats.norm.cdf(xs))))

    lo, hi = 0.0, 20.0
    for _ in range(50):
        mid = 0.5 * (lo + hi)
        if gap(mid) < distance:
            lo = mid
        else:
            hi = mid
    return 0.5 * (lo + hi)


# --------------------------------------------------------------------------
# MDE ("minimum detectable effect") in the equivalence sense
# --------------------------------------------------------------------------
def mde_generic(
    power_fn: Callable[[float], float],
    margin: float,
    power_target: float = 0.80,
    tol: Optional[float] = None,
) -> float:
    """Largest true deviation still certifiable at ``power_target``.

    ``power_fn(dev)`` must be non-increasing in ``dev`` (true).  If even
    ``dev = 0`` falls short of the target the design cannot certify the margin
    at all and ``0.0`` is returned; the caller flags this as under-powered.
    """
    if tol is None:
        # resolution must follow the margin: a fixed 1e-4 made MDE identically
        # zero for margins below 1e-4 (e.g. conservation margins of 1e-6)
        tol = max(abs(float(margin)) * 1e-3, 1e-15)
    if power_fn(0.0) < power_target:
        return 0.0
    lo, hi = 0.0, float(margin)
    if power_fn(hi) >= power_target:
        return float(margin)
    while hi - lo > tol:
        mid = 0.5 * (lo + hi)
        if power_fn(mid) >= power_target:
            lo = mid
        else:
            hi = mid
    return float(lo)


def mde_mean_one_sample(
    n: int,
    sd: float,
    margin: float,
    alpha: float = 0.05,
    power_target: float = 0.80,
) -> float:
    return mde_generic(
        lambda dev: power_mean_one_sample(n, sd, dev, margin, alpha),
        margin,
        power_target,
    )


def mde_variance_ratio(
    n1: int,
    n2: int,
    theta: float,
    alpha: float = 0.05,
    power_target: float = 0.80,
) -> float:
    """Largest certifiable deviation of the sd-ratio from 1 (MDE on |ratio-1|)."""
    return mde_generic(
        lambda dev: power_variance_ratio(n1, n2, 1.0 + dev, theta, alpha),
        float(theta) - 1.0,
        power_target,
    )


def mde_distribution_ks(
    n1: int,
    n2: int,
    margin: float,
    alpha: float,
    power_target: float = 0.80,
    n_boot: int = 200,
    seed: int = 20261111,
) -> float:
    """Largest certifiable sup-CDF distance (simulation based)."""
    return mde_generic(
        lambda dev: power_distribution_ks(
            n1, n2, dev, margin, alpha, n_boot=min(int(n_boot), 150), n_sim=60,
            seed=seed,
        ),
        margin,
        power_target,
        tol=max(abs(float(margin)) / 200.0, 1e-6),
    )


def n_required_mean(
    sd: float,
    margin: float,
    alpha: float = 0.05,
    power_target: float = 0.80,
    true_mean: float = 0.0,
    n_max: int = 5000,
) -> int:
    """Smallest ``n`` whose one-sample TOST power reaches ``power_target``."""
    for n in range(2, int(n_max) + 1):
        if power_mean_one_sample(n, sd, true_mean, margin, alpha) >= power_target:
            return n
    return int(n_max)