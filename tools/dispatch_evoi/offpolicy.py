"""Off-policy evaluation of a new dispatch policy from a logged policy's data.

Estimand: ``V(pi1) = E_slate[ r(a_pi1(s), s) ]`` where ``r`` is the pass indicator
(or the EVOI utility ``value*pass/cost``), evaluated on held-out test rounds.

Because only the *dispatched* order of each round is observed, ``pi1``'s value can
only be estimated by reweighting the logged rounds:

* **IPS**   ``V = (1/n) sum_t  pi1(a_t|s_t)/pi0(a_t|s_t) * r_t``
* **SNIPS** ``V = sum_t w_t r_t / sum_t w_t``      (self-normalised, lower variance)
* **ESS**   ``(sum w)^2 / sum w^2``                 (effective sample size)

``pi1`` is deterministic (top-1 by EVOI), so ``pi1(a_t|s_t) = 1{a_t == a*(s_t)}``
and the importance weight is ``1/pi0`` on the rounds where the new policy happens
to agree with the log.  This is the standard single-action IPS for full slates; it
is unbiased under positivity (``pi0 > 0`` everywhere, guaranteed by the logged
exploration floor) and it has large variance -- which is exactly why ESS is
reported next to it.

The **naive** number in the report is the plain mean of logged outcomes, i.e. the
value of the *logging* policy ``pi0``, not of ``pi1``.  The gap between naive and
IPS is the self-selection bias this section exists to expose.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

import numpy as np

__all__ = ["OffPolicyResult", "single_action_ips"]


@dataclass
class OffPolicyResult:
    n: int
    ips: float
    snips: float
    naive: float
    ess: float
    ess_frac: float
    matched: int
    matched_frac: float
    max_weight: float

    def to_dict(self) -> dict[str, float | int]:
        return {
            "n_rounds": int(self.n),
            "ips": float(self.ips),
            "snips": float(self.snips),
            "naive_logged_mean": float(self.naive),
            "ips_minus_naive": float(self.ips - self.naive),
            "snips_minus_naive": float(self.snips - self.naive),
            "ess": float(self.ess),
            "ess_frac": float(self.ess_frac),
            "matched_rounds": int(self.matched),
            "matched_frac": float(self.matched_frac),
            "max_importance_weight": float(self.max_weight),
        }


def single_action_ips(
    propensity_logged: Sequence[float],
    reward: Sequence[float],
    *,
    logged_index: np.ndarray | None = None,
    target_index: np.ndarray | None = None,
    target_propensity: Sequence[float] | None = None,
) -> OffPolicyResult:
    """IPS/SNIPS/ESS for a target policy evaluated on complete slates.

    ``propensity_logged[t]``  ``pi0(a_t | s_t)`` as recorded in the log
    ``reward[t]``             observed reward of the logged action

    Exactly one target policy description is required:

    ``target_index[t]``       deterministic target: the action the policy would
                              dispatch at round ``t`` (weight ``1/pi0`` on the
                              rounds where the policies agree, 0 otherwise);
    ``target_propensity[t]``  stochastic target: ``pi1(a_t | s_t)`` evaluated at
                              the *logged* action (weight ``pi1/pi0``), which is
                              the usual low-variance option and keeps every round
                              in the effective sample.
    """
    p0 = np.asarray(propensity_logged, dtype=float)
    r = np.asarray(reward, dtype=float)
    if target_propensity is not None:
        p1 = np.asarray(target_propensity, dtype=float)
        if len(p0) != len(p1):
            raise ValueError("propensity arrays must have the same length")
        match = p1 > 0
        w = np.where(match, p1 / p0, 0.0)
    elif target_index is not None:
        ti = np.asarray(target_index, dtype=int)
        if len(ti) != len(p0):
            raise ValueError("target_index and propensities must have the same length")
        li = np.asarray(logged_index, dtype=int) if logged_index is not None else None
        if li is None:
            raise ValueError("target_index requires logged_index")
        match = li == ti
        inv = np.zeros_like(p0)
        np.divide(1.0, p0, out=inv, where=p0 > 0)
        w = np.where(match, inv, 0.0)
    else:
        raise ValueError("provide target_index or target_propensity")
    if np.any(p0 <= 0):
        raise ValueError("positivity violated: a logged propensity is <= 0")
    n = int(len(r))
    ips = float(np.mean(w * r))
    sum_w = float(w.sum())
    snips = float((w * r).sum() / sum_w) if sum_w > 0 else float("nan")
    sq = float((w * w).sum())
    ess = float(sum_w * sum_w / sq) if sq > 0 else 0.0
    return OffPolicyResult(
        n=n,
        ips=ips,
        snips=snips,
        naive=float(r.mean()),
        ess=ess,
        ess_frac=ess / n if n else 0.0,
        matched=int(match.sum()),
        matched_frac=float(match.mean()) if n else 0.0,
        max_weight=float(w.max()) if n else 0.0,
    )