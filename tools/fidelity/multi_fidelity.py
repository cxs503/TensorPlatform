"""Multi-fidelity error-propagation chain, credible-domain guard and fidelity switch gate.

This module answers a question that the *allocation* side of multi-fidelity modelling
(``tools/multifidelity``) does not answer:

    A cheap model produced a number.  How wrong can it be, *where* may it be used,
    and when must I escalate to a more expensive model?

Five primitives, each with a machine-checkable contract:

``TrustDomain``        axis-aligned calibration box + normalised outside distance ``d``
                       (``d = 0.2`` means "20 % of the domain width outside the box").
``TierModel``          a calibrated tier: signed bias ``b``, split-conformal quantiles
                       of ``|delta - b|`` (one for reporting, one -- stricter -- for the
                       alarm), and a Lipschitz envelope over the discrepancy field.
``ExtrapolationLaw``   power-law risk model ``eps(d) = C * d**p`` with ``p >= 1``;
                       fitted from an extrapolation study, never guessed.
``FidelityChain``      the propagation arithmetic: per-tier error bound ->
                       ``sum_k |w_k| L_k eps_k(x)`` (weights and sensitivities injected).
``FidelitySwitchGate`` the decision: lowest tier that is admissible at ``x``; escalation
                       reasons are explicit codes, not scores.

Design rules this module keeps
-----------------------------
* **Domain neutrality.**  Nothing here knows what the quantity is, what a tier is made
  of, or what the design variables mean.  Tiers are injected ``evaluate`` callables;
  calibration values are injected arrays.  See ``tools/evidence_domain_scan.py``.
* **Two different error budgets.**  The reported credible interval is probabilistic
  (split conformal, level ``1 - alpha``).  The guard/alarm interval is deliberately
  stricter (``1 - alpha_alarm``) so that in-domain false alarms stay rare -- using the
  reporting interval for alarms would fire at the reporting rate (~5 %), which is
  already above a 5 % false-alarm budget.
* **No free conservativeness.**  ``propagated_bound`` is an *upper* bound built from
  data; every artifact that uses it also reports the bound/actual ratio, because an
  infinitely loose bound would satisfy "bound >= actual" while being useless.

Known limits (documented, not hidden)
-------------------------------------
* The envelope bound assumes the discrepancy field is Lipschitz with a constant no
  larger than ``1 + margin`` times the largest observed secant slope.  With sparse
  calibration in many dimensions the observed maximum underestimates the true supremum
  and violations become possible -- the artifact quantifies this with a falsification
  case (deliberately under-estimated sensitivity).
* Conformal quantiles are marginal, not pointwise; coverage is checked empirically on
  fresh points (see the artifact's coverage sweep).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Mapping, Optional, Sequence, Tuple

import numpy as np

__all__ = [
    "TrustDomain",
    "conformal_quantile",
    "ExtrapolationLaw",
    "fit_extrapolation_law",
    "TierModel",
    "calibrate_tier",
    "CalibrationSet",
    "ChainLink",
    "FidelityChain",
    "GateDecision",
    "FidelitySwitchGate",
    "interval_coverage",
    "check_conservativeness",
]

_TINY = 1e-12


def _as_2d(name: str, X) -> np.ndarray:
    arr = np.asarray(X, dtype=float)
    if arr.ndim == 1:
        arr = arr.reshape(-1, 1)
    if arr.ndim != 2:
        raise ValueError(f"{name} must be 1-D or 2-D, got shape {arr.shape}")
    if arr.size and not np.all(np.isfinite(arr)):
        raise ValueError(f"{name} contains non-finite entries")
    return arr


def _as_1d(name: str, y, n: Optional[int] = None) -> np.ndarray:
    arr = np.asarray(y, dtype=float).ravel()
    if n is not None and arr.size != n:
        raise ValueError(f"{name} has length {arr.size}, expected {n}")
    if arr.size and not np.all(np.isfinite(arr)):
        raise ValueError(f"{name} contains non-finite entries")
    return arr


# --------------------------------------------------------------------------- trust domain
@dataclass(frozen=True)
class TrustDomain:
    """Axis-aligned box of calibration coverage.

    ``distance(X)`` returns the *normalised outside distance*: ``0.0`` inside the box,
    otherwise the largest per-axis overshoot measured in units of that axis' box width.
    """

    lo: np.ndarray
    hi: np.ndarray

    def __post_init__(self) -> None:
        lo = np.asarray(self.lo, dtype=float).ravel()
        hi = np.asarray(self.hi, dtype=float).ravel()
        if lo.shape != hi.shape:
            raise ValueError(f"lo/hi dimension mismatch: {lo.shape} vs {hi.shape}")
        if lo.size == 0:
            raise ValueError("domain needs at least one dimension")
        if np.any(hi < lo):
            raise ValueError("hi must be >= lo componentwise")
        object.__setattr__(self, "lo", lo)
        object.__setattr__(self, "hi", hi)

    # -- construction ------------------------------------------------------
    @classmethod
    def from_points(cls, X, *, margin: float = 0.0) -> "TrustDomain":
        """Box spanned by ``X``, optionally inflated by ``margin`` (fraction of width)."""
        X = _as_2d("X", X)
        if X.shape[0] == 0:
            raise ValueError("cannot build a domain from zero points")
        dom = cls(X.min(axis=0), X.max(axis=0))
        return dom.scaled(margin) if margin else dom

    def scaled(self, factor: float) -> "TrustDomain":
        """Inflate (``factor > 0``) or shrink (``factor < 0``) about the box centre."""
        if factor <= -1.0:
            raise ValueError("factor must be > -1")
        mid = 0.5 * (self.lo + self.hi)
        half = 0.5 * (self.hi - self.lo) * (1.0 + factor)
        return TrustDomain(mid - half, mid + half)

    # -- geometry ----------------------------------------------------------
    @property
    def dimension(self) -> int:
        return self.lo.size

    def width(self) -> np.ndarray:
        """Per-axis width with a relative floor so degenerate axes stay usable."""
        width = self.hi - self.lo
        scale = np.maximum(np.abs(0.5 * (self.hi + self.lo)), 1.0)
        return np.maximum(width, 1e-9 * scale)

    def distance(self, X) -> np.ndarray:
        """Normalised outside distance per point (0 inside)."""
        X = _as_2d("X", X)
        if X.shape[1] != self.dimension:
            raise ValueError(f"X has {X.shape[1]} columns, domain has {self.dimension}")
        over = np.maximum(self.lo - X, X - self.hi)
        return (np.maximum(over, 0.0) / self.width()).max(axis=1)

    def contains(self, X) -> np.ndarray:
        return self.distance(X) <= 0.0

    def scaled_distances(self, A, B) -> np.ndarray:
        """Pairwise Euclidean distances in width-normalised coordinates, shape (nA, nB)."""
        A = _as_2d("A", A)
        B = _as_2d("B", B)
        w = self.width()
        d = (A[:, None, :] - B[None, :, :]) / w
        return np.sqrt(np.einsum("ijk,ijk->ij", d, d))


# --------------------------------------------------------------------------- conformal
def conformal_quantile(scores: Sequence[float], level: float = 0.95,
                       *, fallback_max: bool = True) -> Tuple[float, bool]:
    """Split-conformal quantile of non-negative conformity ``scores``.

    Returns ``(q, saturated)``.  ``saturated`` is ``True`` when the finite-sample index
    ``ceil((n + 1) * level)`` exceeds ``n``; the empirical maximum is then returned
    (``fallback_max``) or ``inf``.  Saturation matters: a saturated alarm quantile is
    *not* a 1 - alpha guarantee, and the chain records it in its diagnostics.
    """
    s = np.sort(_as_1d("scores", scores))
    n = s.size
    if n == 0:
        raise ValueError("need at least one conformity score")
    if not (0.0 < level < 1.0):
        raise ValueError(f"level must be in (0, 1), got {level}")
    if np.any(s < 0):
        raise ValueError("conformity scores must be non-negative")
    k = int(math.ceil((n + 1) * level))
    if k <= n:
        return float(s[k - 1]), False
    return (float(s[-1]) if fallback_max else float("inf")), True


# --------------------------------------------------------------------------- extrapolation
@dataclass(frozen=True)
class ExtrapolationLaw:
    """Power-law risk model for extrapolation distance ``d``: ``eps(d) = C * d**p``.

    ``p`` is constrained to ``>= 1`` (a sub-linear error growth would mean the model
    error decays away from the data, which is not an admissible risk model).
    """

    C: float
    p: float
    source: str = "none"
    study_max_distance: float = 0.0

    def __post_init__(self) -> None:
        if not np.isfinite(self.C) or self.C < 0:
            raise ValueError(f"C must be finite and >= 0, got {self.C}")
        if not np.isfinite(self.p) or self.p < 1.0:
            raise ValueError(f"p must be finite and >= 1, got {self.p}")

    def eps(self, d) -> np.ndarray:
        d = np.asarray(d, dtype=float)
        return self.C * np.power(np.clip(d, 0.0, None), self.p)

    def as_dict(self) -> Dict[str, float]:
        return {"C": self.C, "p": self.p, "source": self.source,
                "study_max_distance": self.study_max_distance}


def fit_extrapolation_law(distances: Sequence[float], errors: Sequence[float],
                          *, p_min: float = 1.0, p_max: float = 6.0,
                          safety: float = 1.5) -> ExtrapolationLaw:
    """Fit ``eps(d) = C * d**p`` to an extrapolation study.

    The exponent is the log-log least-squares slope, clipped into ``[p_min, p_max]``;
    ``C`` is then the *envelope* ``safety * max(err_i / d_i**p)`` over the study so the
    bound holds at every studied distance, not only in the least-squares sense.

    Parameters
    ----------
    distances, errors:
        Positive extrapolation distances (domain-width units) and the corresponding
        measured errors.  Points with ``d <= 0`` or ``err <= 0`` are ignored.
    safety:
        Envelope margin.  Default 1.5 -- measured in the artifact to cover up to 1.5x
        the largest studied distance for the test family used there.
    """
    d = np.asarray(distances, dtype=float).ravel()
    e = np.asarray(errors, dtype=float).ravel()
    if d.size != e.size:
        raise ValueError("distances and errors must have the same length")
    mask = (d > 0.0) & (e > 0.0)
    d, e = d[mask], e[mask]
    if d.size == 0:
        raise ValueError("need at least one positive (distance, error) pair")
    if safety < 1.0:
        raise ValueError("safety must be >= 1")
    if d.size >= 2 and np.ptp(np.log(d)) > _TINY:
        slope = float(np.polyfit(np.log(d), np.log(e), 1)[0])
    else:
        slope = p_min
    p = float(np.clip(max(slope, p_min), p_min, p_max))
    C = float(safety * np.max(e / np.power(d, p)))
    return ExtrapolationLaw(C=C, p=p, source="study",
                            study_max_distance=float(d.max()))


# --------------------------------------------------------------------------- tiers
@dataclass(frozen=True)
class CalibrationSet:
    """Calibration data: design points, a reference value and one column per tier.

    ``reference`` is the highest-fidelity / experimental value at the calibration
    points.  Each tier's column is that tier's own prediction at the same points.
    """

    X: np.ndarray
    reference: np.ndarray
    tier_values: Mapping[int, np.ndarray]

    def __post_init__(self) -> None:
        X = _as_2d("X", self.X)
        ref = _as_1d("reference", self.reference, n=X.shape[0])
        tiers: Dict[int, np.ndarray] = {}
        for rank, values in dict(self.tier_values).items():
            if not isinstance(rank, (int, np.integer)) or rank < 0:
                raise ValueError(f"rank must be a non-negative int, got {rank!r}")
            tiers[int(rank)] = _as_1d(f"tier_values[{rank}]", values, n=X.shape[0])
        if not tiers:
            raise ValueError("at least one tier column is required")
        object.__setattr__(self, "X", X)
        object.__setattr__(self, "reference", ref)
        object.__setattr__(self, "tier_values", tiers)

    @property
    def n(self) -> int:
        return self.X.shape[0]

    def ranks(self) -> List[int]:
        return sorted(self.tier_values)

    def subset(self, index) -> "CalibrationSet":
        idx = np.asarray(index)
        return CalibrationSet(self.X[idx], self.reference[idx],
                              {k: v[idx] for k, v in self.tier_values.items()})


def _max_secant_slope(points: np.ndarray, values: np.ndarray,
                      domain: TrustDomain) -> float:
    """Largest observed secant slope ``|e_i - e_j| / dist_ij`` (0 for n < 2)."""
    n = points.shape[0]
    if n < 2:
        return 0.0
    dist = domain.scaled_distances(points, points)
    diff = np.abs(values[:, None] - values[None, :])
    off = ~np.eye(n, dtype=bool)
    valid = off & (dist > 1e-9)
    if not np.any(valid):
        return 0.0
    return float(np.max(diff[valid] / dist[valid]))


@dataclass(frozen=True)
class TierModel:
    """One calibrated tier of the ladder.

    Attributes
    ----------
    bias:
        Mean signed discrepancy ``tier - reference`` over the calibration set; the
        credible interval is centred on the bias-corrected prediction.
    q_report, q_guard:
        Conformal quantiles of ``|delta - bias|`` at the reporting / alarm levels.
    saturated:
        ``(report, guard)`` saturation flags from :func:`conformal_quantile`.
    lipschitz:
        Envelope slope for the discrepancy field (``(1 + margin)`` times the largest
        observed secant slope).
    guard_margin:
        Relative safety margin applied to the guard bound base.  The conformal
        quantile is a *point estimate* of the residual tail; the guard bound is a
        claim about unobserved points, so the empirical quantile alone is not enough
        (measured: 2/300 violations without it in the 2-D artifact case).
    """

    rank: int
    name: str
    evaluate: Callable[[np.ndarray], np.ndarray]
    bias: float
    q_report: float
    q_guard: float
    report_level: float
    alarm_level: float
    saturated: Tuple[bool, bool]
    lipschitz: float
    domain: TrustDomain
    calibration_points: np.ndarray
    residuals: np.ndarray            # |delta - bias| at the calibration points
    law: ExtrapolationLaw
    n_calibration: int
    guard_margin: float = 0.0

    # -- per-tier bounds ---------------------------------------------------
    def envelope_bound(self, X) -> np.ndarray:
        """Lipschitz envelope over the calibration residuals (data-driven, tight)."""
        X = _as_2d("X", X)
        dist = self.domain.scaled_distances(X, self.calibration_points)
        return np.min(self.residuals[None, :] + self.lipschitz * dist, axis=1)

    def extrapolation_risk(self, X) -> np.ndarray:
        """Power-law risk term, non-zero only outside the calibration box."""
        return self.law.eps(self.domain.distance(X))

    def bound(self, X, *, mode: str = "guard") -> np.ndarray:
        """Absolute error bound of this tier against the reference.

        ``mode='guard'`` uses the alarm quantile and adds the extrapolation risk term;
        ``mode='report'`` uses the reporting quantile (credible-interval half width
        without the extrapolation term).
        """
        X = _as_2d("X", X)
        q = self.q_guard if mode == "guard" else self.q_report
        if mode == "guard":
            env = self.envelope_bound(X)
            base = np.maximum(env, np.abs(self.bias) + q)
            return (1.0 + self.guard_margin) * base + self.extrapolation_risk(X)
        if mode == "report":
            return np.abs(self.bias) + q
        raise ValueError(f"unknown mode {mode!r}")

    def predict(self, X) -> np.ndarray:
        X = _as_2d("X", X)
        y = _as_1d("evaluate(X)", self.evaluate(X))
        if y.size != X.shape[0]:
            raise ValueError(f"evaluate returned {y.size} values for {X.shape[0]} points")
        return y

    def interval(self, X, *, level: Optional[float] = None,
                 halfwidth_scale: float = 1.0) -> Tuple[np.ndarray, np.ndarray]:
        """Bias-corrected credible interval ``[lo, hi]``.

        ``halfwidth_scale`` exists so that the negative control ("deliberately shrink
        the interval") is a one-argument change, not a re-implementation.
        """
        if halfwidth_scale <= 0:
            raise ValueError("halfwidth_scale must be > 0")
        q = self.q_report if level is None else conformal_quantile(
            self.residuals, level)[0]
        centre = self.predict(X) - self.bias
        half = halfwidth_scale * q + self.extrapolation_risk(X)
        return centre - half, centre + half


def calibrate_tier(rank: int, name: str, evaluate: Callable[[np.ndarray], np.ndarray],
                   calibration: CalibrationSet, *,
                   domain: Optional[TrustDomain] = None,
                   study: Optional[Tuple[Sequence[float], Sequence[float]]] = None,
                   report_level: float = 0.95,
                   alarm_level: float = 0.99,
                   safety: float = 1.5,
                   lipschitz_margin: float = 1.0,
                   guard_margin: float = 0.25) -> TierModel:
    """Calibrate one tier against the reference column of ``calibration``.

    Parameters
    ----------
    domain:
        Calibration box; defaults to the box spanned by the calibration points.
    study:
        Optional ``(distances, errors)`` extrapolation study used to fit the power-law
        risk term (:func:`fit_extrapolation_law`).  Without it the law is inert
        (``C = 0``) and the growth outside the box comes from the envelope only.
    lipschitz_margin, guard_margin:
        Envelope inflation (``L = (1 + lipschitz_margin) * max secant slope``) and the
        relative margin on the guard base.  Both are calibrated by measurement in the
        artifact: 2/300 violations in the 2-D case at ``(0.5, 0.0)``, zero at the
        defaults.
    """
    if not (0.0 < report_level < alarm_level < 1.0):
        raise ValueError("require 0 < report_level < alarm_level < 1")
    if rank not in calibration.tier_values:
        raise ValueError(f"calibration has no column for rank {rank}")
    domain = domain if domain is not None else TrustDomain.from_points(calibration.X)
    values = calibration.tier_values[rank]
    delta = values - calibration.reference
    bias = float(np.mean(delta))
    residual = np.abs(delta - bias)
    q_report, sat_report = conformal_quantile(residual, report_level)
    q_guard, sat_guard = conformal_quantile(residual, alarm_level)
    secant = _max_secant_slope(calibration.X, delta - bias, domain)
    if study is None:
        law = ExtrapolationLaw(C=0.0, p=1.0, source="none")
    else:
        law = fit_extrapolation_law(study[0], study[1], safety=safety)
    return TierModel(
        rank=int(rank), name=str(name), evaluate=evaluate, bias=bias,
        q_report=float(q_report), q_guard=float(q_guard),
        report_level=float(report_level), alarm_level=float(alarm_level),
        saturated=(sat_report, sat_guard), lipschitz=(1.0 + lipschitz_margin) * secant,
        domain=domain, calibration_points=calibration.X.copy(),
        residuals=residual, law=law, n_calibration=calibration.n,
        guard_margin=float(guard_margin),
    )


# --------------------------------------------------------------------------- chain
@dataclass(frozen=True)
class ChainLink:
    """How one tier enters the composed estimate.

    ``weight``    linear weight of the tier in the composition (sign matters);
    ``sensitivity`` Lipschitz constant of the downstream functional that consumes this
                  tier's output.  The propagated bound multiplies by ``|weight| * sens``.
    """

    weight: float = 1.0
    sensitivity: float = 1.0

    def __post_init__(self) -> None:
        if not np.isfinite(self.weight):
            raise ValueError("weight must be finite")
        if not np.isfinite(self.sensitivity) or self.sensitivity < 0:
            raise ValueError("sensitivity must be finite and >= 0")

    @property
    def amplification(self) -> float:
        return abs(self.weight) * self.sensitivity


class FidelityChain:
    """A calibrated ladder of tiers plus the error-propagation arithmetic."""

    def __init__(self, tiers: Mapping[int, TierModel],
                 links: Optional[Mapping[int, ChainLink]] = None) -> None:
        if not tiers:
            raise ValueError("chain needs at least one tier")
        ranks = sorted(int(r) for r in tiers)
        if ranks != list(range(len(ranks))):
            raise ValueError(f"ranks must be contiguous from 0, got {ranks}")
        self._tiers: Dict[int, TierModel] = {int(k): v for k, v in tiers.items()}
        self._links: Dict[int, ChainLink] = {
            int(k): ChainLink() for k in self._tiers}
        for rank, link in dict(links or {}).items():
            if int(rank) not in self._tiers:
                raise ValueError(f"link for unknown rank {rank}")
            self._links[int(rank)] = link

    # -- introspection -----------------------------------------------------
    @property
    def ranks(self) -> List[int]:
        return sorted(self._tiers)

    @property
    def top_rank(self) -> int:
        return max(self._tiers)

    def tier(self, rank: int) -> TierModel:
        if int(rank) not in self._tiers:
            raise KeyError(f"no tier with rank {rank}; chain has {self.ranks}")
        return self._tiers[int(rank)]

    def link(self, rank: int) -> ChainLink:
        return self._links[int(rank)]

    # -- evaluation --------------------------------------------------------
    def predict(self, rank: int, X) -> np.ndarray:
        return self.tier(rank).predict(X)

    def credible_interval(self, rank: int, X, *, level: Optional[float] = None,
                          halfwidth_scale: float = 1.0
                          ) -> Tuple[np.ndarray, np.ndarray]:
        return self.tier(rank).interval(X, level=level,
                                        halfwidth_scale=halfwidth_scale)

    def tier_bound(self, rank: int, X, *, mode: str = "guard") -> np.ndarray:
        return self.tier(rank).bound(X, mode=mode)

    def composed_prediction(self, X, ranks: Optional[Sequence[int]] = None) -> np.ndarray:
        """Bias-corrected weighted combination of the selected tiers."""
        X = _as_2d("X", X)
        use = self.ranks if ranks is None else [int(r) for r in ranks]
        total = np.zeros(X.shape[0])
        for rank in use:
            tier = self.tier(rank)
            total += self.link(rank).weight * (tier.predict(X) - tier.bias)
        return total

    def composed_bound(self, X, ranks: Optional[Sequence[int]] = None, *,
                       mode: str = "guard") -> np.ndarray:
        """Propagated total error bound ``sum_k |w_k| L_k eps_k(x)`` (C1 arithmetic)."""
        X = _as_2d("X", X)
        use = self.ranks if ranks is None else [int(r) for r in ranks]
        total = np.zeros(X.shape[0])
        for rank in use:
            total += self.link(rank).amplification * self.tier_bound(rank, X, mode=mode)
        return total

    def gate(self, **kwargs) -> "FidelitySwitchGate":
        return FidelitySwitchGate(self, **kwargs)

    def diagnostics(self) -> Dict[str, object]:
        return {
            "ranks": self.ranks,
            "tiers": {
                str(rank): {
                    "name": tier.name,
                    "bias": tier.bias,
                    "q_report": tier.q_report,
                    "q_guard": tier.q_guard,
                    "report_level": tier.report_level,
                    "alarm_level": tier.alarm_level,
                    "saturated": {"report": tier.saturated[0], "guard": tier.saturated[1]},
                    "lipschitz": tier.lipschitz,
                    "n_calibration": tier.n_calibration,
                    "law": tier.law.as_dict(),
                    "domain": {"lo": tier.domain.lo.tolist(),
                               "hi": tier.domain.hi.tolist()},
                    "amplification": self.link(rank).amplification,
                }
                for rank, tier in self._tiers.items()
            },
        }


# --------------------------------------------------------------------------- gate
@dataclass(frozen=True)
class GateDecision:
    """Outcome of the switch gate at one operating point."""

    escalate: bool
    target_rank: int
    reasons: Tuple[str, ...]
    distance: Mapping[int, float]
    bound_over_value: Mapping[int, float]
    rejected: Mapping[int, Tuple[str, ...]] = field(default_factory=dict)

    @property
    def fidelity_insufficient(self) -> bool:
        return "interval_disjoint" in self.reasons

    def as_dict(self) -> Dict[str, object]:
        return {
            "escalate": self.escalate,
            "target_rank": self.target_rank,
            "reasons": list(self.reasons),
            "rejected": {str(k): list(v) for k, v in self.rejected.items()},
            "distance": {str(k): v for k, v in self.distance.items()},
            "bound_over_value": {str(k): v for k, v in self.bound_over_value.items()},
        }


class FidelitySwitchGate:
    """Lowest admissible tier per operating point, with explicit escalation reasons.

    Admissibility rules, evaluated per tier from the coarsest upwards:

    ``domain_exceeded``      normalised outside distance ``> max_distance``;
    ``tolerance_exceeded``   relative credible half width ``> rel_tol`` (opt-in);
    ``interval_disjoint``    the *alarm* interval of this tier and of the next tier do
                             not overlap -> credible-domain guard fires;
    ``extrapolation_study_exceeded``
                             the point lies beyond the largest distance of the fitted
                             extrapolation study, i.e. the risk law itself is
                             unvalidated there (measured: a 1.5x margin is not always
                             enough -- see the C4 out-of-sample row of the artifact).
    """

    def __init__(self, chain: FidelityChain, *, max_distance: float = 0.0,
                 rel_tol: Optional[float] = None,
                 alarm_level: Optional[float] = None,
                 disjointness: bool = True,
                 halfwidth_scale: float = 1.0) -> None:
        if max_distance < 0:
            raise ValueError("max_distance must be >= 0")
        if rel_tol is not None and rel_tol <= 0:
            raise ValueError("rel_tol must be > 0 when given")
        self.chain = chain
        self.max_distance = float(max_distance)
        self.rel_tol = None if rel_tol is None else float(rel_tol)
        self.alarm_level = alarm_level
        self.disjointness = bool(disjointness)
        self.halfwidth_scale = float(halfwidth_scale)

    # -- helpers -----------------------------------------------------------
    def _alarm_interval(self, rank: int, X) -> Tuple[np.ndarray, np.ndarray]:
        tier = self.chain.tier(rank)
        q = tier.q_guard if self.alarm_level is None else conformal_quantile(
            tier.residuals, self.alarm_level)[0]
        centre = tier.predict(X) - tier.bias
        half = self.halfwidth_scale * q + tier.extrapolation_risk(X)
        return centre - half, centre + half

    def _reasons(self, rank: int, x_row: np.ndarray) -> Tuple[List[str], float, float]:
        tier = self.chain.tier(rank)
        reasons: List[str] = []
        d = float(tier.domain.distance(x_row)[0])
        if d > self.max_distance:
            reasons.append("domain_exceeded")
        value = float(tier.predict(x_row)[0] - tier.bias)
        half = float(tier.q_report * self.halfwidth_scale)
        ratio = half / max(abs(value), _TINY)
        if self.rel_tol is not None and ratio > self.rel_tol:
            reasons.append("tolerance_exceeded")
        if (tier.law.source == "study" and tier.law.study_max_distance > 0.0
                and d > tier.law.study_max_distance):
            reasons.append("extrapolation_study_exceeded")
        if self.disjointness and rank < self.chain.top_rank:
            lo_a, hi_a = self._alarm_interval(rank, x_row)
            lo_b, hi_b = self._alarm_interval(rank + 1, x_row)
            if hi_a[0] < lo_b[0] or hi_b[0] < lo_a[0]:
                reasons.append("interval_disjoint")
        return reasons, d, ratio

    # -- public ------------------------------------------------------------
    def advice(self, X) -> List[GateDecision]:
        """One decision per operating point, with the codes that rejected lower tiers."""
        X = _as_2d("X", X)
        out: List[GateDecision] = []
        ranks = self.chain.ranks
        for row in X:
            x_row = row.reshape(1, -1)
            admissible: Optional[int] = None
            distance: Dict[int, float] = {}
            ratios: Dict[int, float] = {}
            rejected: Dict[int, Tuple[str, ...]] = {}
            for rank in ranks:
                reasons, d, ratio = self._reasons(rank, x_row)
                distance[rank] = d
                ratios[rank] = ratio
                if admissible is None:
                    if not reasons:
                        admissible = rank
                    else:
                        rejected[rank] = tuple(reasons)
            target = ranks[-1] if admissible is None else admissible
            if admissible is None:
                rejected[target] = tuple(self._reasons(target, x_row)[0]) or (
                    "nothing_admissible",)
            codes: List[str] = []
            for rank in sorted(rejected):
                for code in rejected[rank]:
                    if code not in codes:
                        codes.append(code)
            out.append(GateDecision(
                escalate=(target != ranks[0]), target_rank=target,
                reasons=tuple(codes), distance=distance,
                bound_over_value=ratios, rejected=rejected))
        return out

    def escalation_rate(self, X) -> float:
        decisions = self.advice(X)
        return float(np.mean([d.escalate for d in decisions])) if decisions else 0.0


# --------------------------------------------------------------------------- checks
def interval_coverage(chain: FidelityChain, rank: int, X, truth, *,
                      level: Optional[float] = None,
                      halfwidth_scale: float = 1.0) -> Dict[str, float]:
    """Empirical coverage of the credible interval on fresh points."""
    X = _as_2d("X", X)
    truth = _as_1d("truth", truth, n=X.shape[0])
    lo, hi = chain.credible_interval(rank, X, level=level,
                                     halfwidth_scale=halfwidth_scale)
    inside = (truth >= lo) & (truth <= hi)
    return {
        "n": int(X.shape[0]),
        "coverage": float(np.mean(inside)),
        "mean_halfwidth": float(np.mean(0.5 * (hi - lo))),
        "min_halfwidth": float(np.min(0.5 * (hi - lo))),
        "max_halfwidth": float(np.max(0.5 * (hi - lo))),
    }


def check_conservativeness(bound, actual) -> Dict[str, object]:
    """Compare a claimed error bound with the realised error.

    Returns the violation count/rate plus the bound/actual ratio statistics that make
    "conservative but useless" visible.
    """
    b = _as_1d("bound", bound)
    a = np.abs(_as_1d("actual", actual))
    if b.size != a.size:
        raise ValueError("bound and actual must have the same length")
    violation = b < a
    ratio = np.where(a > _TINY, b / np.maximum(a, _TINY), np.inf)
    finite = ratio[np.isfinite(ratio)]
    return {
        "n": int(b.size),
        "violations": int(violation.sum()),
        "violation_rate": float(np.mean(violation)) if b.size else 0.0,
        "max_actual": float(a.max()) if a.size else 0.0,
        "max_bound": float(b.max()) if b.size else 0.0,
        "ratio_median": float(np.median(finite)) if finite.size else float("inf"),
        "ratio_p95": float(np.percentile(finite, 95)) if finite.size else float("inf"),
        "ratio_max": float(finite.max()) if finite.size else float("inf"),
    }