"""Off-policy (shadow) evaluation of a candidate dispatch policy on the log.

Estimand: the expected reward of the action the candidate policy would dispatch,
``V(pi1) = E_s[ r(a1(s), s) ]``, estimated from logged rounds produced by the
behaviour policy ``pi0``:

* **IPS**   ``V = (1/n) sum_t  w_t r_t``, ``w_t = pi1(a_t|s_t) / pi0(a_t|s_t)``
* **SNIPS** ``V = sum_t w_t r_t / sum_t w_t``  (self-normalised, lower variance)
* **ESS**   ``(sum w)^2 / sum w^2``            (how many rounds actually matter)
* **naive** ``mean(r_t)`` = value of ``pi0``, i.e. the number the current
  dispatcher would report about itself.

The candidate policy keeps an exploration floor, so ``w_t`` is bounded by
``(1/eps)`` and positivity holds by construction.  Intervals are percentile
bootstraps over rounds with a **counter-mode SHA-256 RNG seeded from the record
ids**, so the interval is itself replayable from the log.

Honest limitation: IPS/SNIPS are unbiased for the *logged slate distribution*.
They say nothing about slates that never occur in the log (support mismatch), so
a shadow result is a bound on "the new policy would have done better on the
rounds we saw", not a guarantee for the future.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Sequence

from .canonical import HashRNG, fsum, sha256_hex
from .policy import Policy
from .schema import DecisionRecord, LogConfig, SchemaError

__all__ = [
    "OffPolicyEstimate",
    "importance_weights",
    "ips_snips",
    "bootstrap_ci",
    "estimate_from_records",
]


@dataclass
class OffPolicyEstimate:
    n: int
    ips: float
    snips: float
    naive: float
    ess: float
    ess_frac: float
    max_weight: float
    ci: dict[str, list[float]] = field(default_factory=dict)
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def snips_minus_naive(self) -> float:
        return self.snips - self.naive

    def to_dict(self) -> dict[str, Any]:
        return {
            "n_rounds": int(self.n),
            "ips": float(self.ips),
            "snips": float(self.snips),
            "naive_logged_mean": float(self.naive),
            "snips_minus_naive": float(self.snips_minus_naive),
            "ips_minus_naive": float(self.ips - self.naive),
            "ess": float(self.ess),
            "ess_frac": float(self.ess_frac),
            "max_importance_weight": float(self.max_weight),
            "ci95": {k: [float(a), float(b)] for k, (a, b) in self.ci.items()},
            **self.extra,
        }


def importance_weights(
    pi0: Sequence[float], pi1: Sequence[float]
) -> list[float]:
    if len(pi0) != len(pi1):
        raise ValueError("propensity arrays must have equal length")
    out = []
    for a, b in zip(pi0, pi1):
        if a <= 0.0:
            raise ValueError("positivity violated: logged propensity <= 0")
        out.append(float(b) / float(a))
    return out


def ips_snips(weights: Sequence[float], rewards: Sequence[float]) -> OffPolicyEstimate:
    """Point estimates (no interval) for pre-computed weights."""
    w = [float(x) for x in weights]
    r = [float(x) for x in rewards]
    if len(w) != len(r):
        raise ValueError("weights and rewards must have equal length")
    if not w:
        raise ValueError("empty sample")
    n = len(w)
    ips = fsum(wi * ri for wi, ri in zip(w, r)) / n
    sw = fsum(w)
    snips = fsum(wi * ri for wi, ri in zip(w, r)) / sw if sw > 0 else float("nan")
    sq = fsum(wi * wi for wi in w)
    ess = (sw * sw / sq) if sq > 0 else 0.0
    return OffPolicyEstimate(
        n=n,
        ips=ips,
        snips=snips,
        naive=fsum(r) / n,
        ess=ess,
        ess_frac=ess / n,
        max_weight=max(w),
    )


def _percentile(sorted_vals: Sequence[float], q: float) -> float:
    if not sorted_vals:
        return float("nan")
    if q <= 0:
        return float(sorted_vals[0])
    if q >= 1:
        return float(sorted_vals[-1])
    pos = q * (len(sorted_vals) - 1)
    lo = int(math.floor(pos))
    hi = int(math.ceil(pos))
    if lo == hi:
        return float(sorted_vals[lo])
    frac = pos - lo
    return float(sorted_vals[lo]) * (1.0 - frac) + float(sorted_vals[hi]) * frac


def bootstrap_ci(
    weights: Sequence[float],
    rewards: Sequence[float],
    *,
    replicates: int = 1000,
    alpha: float = 0.05,
    seed: str = "synthetic.bootstrap",
) -> dict[str, list[float]]:
    """Percentile bootstrap intervals for IPS, SNIPS and SNIPS - naive.

    Rounds are the resampling unit (slates are independent across decisions).
    The RNG is counter-mode SHA-256 keyed by ``seed``, so the interval does not
    depend on the host RNG and can be reproduced from the reported seed.
    """
    w = [float(x) for x in weights]
    r = [float(x) for x in rewards]
    n = len(w)
    if n == 0:
        raise ValueError("empty sample")
    rng = HashRNG(seed)
    ips_vals: list[float] = []
    snips_vals: list[float] = []
    diff_vals: list[float] = []
    naive = fsum(r) / n
    for _ in range(int(replicates)):
        idx = rng.sample_indices(n, n)
        ws = [w[i] for i in idx]
        rs = [r[i] for i in idx]
        sw = fsum(ws)
        num = fsum(wi * ri for wi, ri in zip(ws, rs))
        ips_vals.append(num / n)
        sn = num / sw if sw > 0 else float("nan")
        snips_vals.append(sn)
        diff_vals.append(sn - naive)
    ips_vals = [v for v in ips_vals if math.isfinite(v)]
    snips_vals = [v for v in snips_vals if math.isfinite(v)]
    diff_vals = [v for v in diff_vals if math.isfinite(v)]
    ips_vals.sort()
    snips_vals.sort()
    diff_vals.sort()
    return {
        "ips": [_percentile(ips_vals, alpha / 2), _percentile(ips_vals, 1 - alpha / 2)],
        "snips": [_percentile(snips_vals, alpha / 2), _percentile(snips_vals, 1 - alpha / 2)],
        "snips_minus_naive": [
            _percentile(diff_vals, alpha / 2),
            _percentile(diff_vals, 1 - alpha / 2),
        ],
    }


def estimate_from_records(
    records: Sequence[DecisionRecord],
    target: Policy,
    *,
    reward_field: str,
    bootstrap: int = 1000,
    alpha: float = 0.05,
    seed: str | None = None,
    config: LogConfig | None = None,
) -> OffPolicyEstimate:
    """Shadow-evaluate ``target`` on labelled rounds of the log."""
    labelled = [r for r in records if r.labelled()]
    if config is not None:
        bad = [r.record_id for r in labelled if r.data["config_sha256"] != config.sha256]
        if bad:
            raise SchemaError(f"{len(bad)} records were written under a different config")
    if not labelled:
        raise ValueError("no labelled rounds: the shadow estimate is undefined")
    w: list[float] = []
    rew: list[float] = []
    for rec in labelled:
        chosen = rec.data["decision"]["chosen"]
        pi0 = float(rec.data["decision"]["propensities"][chosen])
        field = rec.data["slate"]["candidate_id_field"]
        target_decision = target.decide(rec.candidates, candidate_id_field=field)
        pi1 = float(target_decision["propensities"][chosen])
        w.extend(importance_weights([pi0], [pi1]))
        reward = rec.reward(reward_field)
        if reward is None:
            raise SchemaError(
                f"record {rec.record_id[:12]}… has no outcome field {reward_field!r}"
            )
        rew.append(reward)
    est = ips_snips(w, rew)
    auto_seed = seed or sha256_hex("|".join(r.record_id for r in labelled))[:32]
    est.extra = {
        "reward_field": reward_field,
        "bootstrap": {"replicates": int(bootstrap), "alpha": float(alpha), "seed": auto_seed,
                      "rng": "sha256-counter-mode", "unit": "decision_round"},
        "target_policy_id": target.policy_id,
        "target_policy_sha256": target.sha256,
    }
    if bootstrap > 0:
        est.ci = bootstrap_ci(w, rew, replicates=bootstrap, alpha=alpha, seed=auto_seed)
    return est