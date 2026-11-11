"""Synthetic, domain-free scenario generator for the decision log.

Everything here is ``synthetic.*`` on purpose: the module under test must not
know that "dispatch" happens to be about missions, ships or solvers, and the
tests must not smuggle domain words into the platform.

The scenario is a small but *fully known* environment:

* each slate has ``n_candidates`` candidates with features drawn from a
  counter-mode SHA-256 stream (so the world is reproducible from the seed);
* every candidate has a **true** acceptance probability
  ``p_a = sigmoid(bias + w* . x_a)`` -- known to the generator, so the value of
  any policy is computable *exactly* by enumeration:
  ``V(pi) = mean_slates sum_a pi(a|s) p_a(s)``;
* the behaviour policy (the one that gets logged) is a *mediocre* linear scorer
  with an exploration floor; the candidate policy is a *better* linear scorer
  with its own floor.  The gap between ``V(candidate)`` and ``V(behaviour)`` is
  the ground truth the shadow estimator has to recover;
* the logged action is a **draw** from the behaviour propensities (an online
  dispatcher sometimes picks an order it did not rank first); the outcome is a
  Bernoulli draw from the chosen candidate's true probability.  The estimator
  therefore sees exactly what an online log gives it: the reward of the action
  that was taken, nothing else.
"""

from __future__ import annotations

import math
import os
from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence

from .canonical import HashRNG, fsum
from .policy import Policy
from .schema import LogConfig
from .store import DecisionLog

__all__ = [
    "SYNTH_FEATURES",
    "SYNTH_OUTCOMES",
    "TRUE_WEIGHTS",
    "BEHAVIOUR_WEIGHTS",
    "CANDIDATE_WEIGHTS",
    "sigmoid",
    "SyntheticScenario",
    "default_config",
    "make_scenario",
    "write_log",
]

SYNTH_FEATURES = (
    "synthetic.feature.alpha",
    "synthetic.feature.beta",
    "synthetic.feature.gamma",
    "synthetic.feature.delta",
)

SYNTH_OUTCOMES = ("synthetic.reward", "synthetic.accepted", "synthetic.cost")

#: true generative weights (the "physics" of the synthetic world)
TRUE_WEIGHTS = {
    "synthetic.feature.alpha": 1.6,
    "synthetic.feature.beta": -1.1,
    "synthetic.feature.gamma": 0.9,
    "synthetic.feature.delta": 0.25,
}
TRUE_BIAS = -0.35

#: the logged (behaviour) policy: plausible but mis-weighted
BEHAVIOUR_WEIGHTS = {
    "synthetic.feature.alpha": 0.55,
    "synthetic.feature.beta": -0.20,
    "synthetic.feature.gamma": 0.30,
    "synthetic.feature.delta": 0.00,
}

#: the candidate policy the shadow evaluation is asked about (closer to truth)
CANDIDATE_WEIGHTS = {
    "synthetic.feature.alpha": 1.45,
    "synthetic.feature.beta": -1.00,
    "synthetic.feature.gamma": 0.80,
    "synthetic.feature.delta": 0.20,
}


def sigmoid(z: float) -> float:
    if z >= 0:
        return 1.0 / (1.0 + math.exp(-z))
    e = math.exp(z)
    return e / (1.0 + e)


def default_config() -> LogConfig:
    return LogConfig(
        feature_names=SYNTH_FEATURES,
        outcome_fields=SYNTH_OUTCOMES,
        candidate_id_field="synthetic.candidate_id",
        forbidden_feature_substrings=("outcome", "label", "reward"),
    )


@dataclass
class SyntheticScenario:
    config: LogConfig
    behaviour: Policy
    candidate: Policy
    n_candidates: int
    slates: list[dict[str, Any]] = field(default_factory=list)
    seed: str = "synthetic.world"

    # -- ground truth ---------------------------------------------------
    def policy_value(self, policy: Policy) -> float:
        """Exact ``E_s[ sum_a pi(a|s) p_a(s) ]`` by enumerating the slates."""
        vals = []
        for slate in self.slates:
            cands = slate["candidates"]
            ids = [c[self.config.candidate_id_field] for c in cands]
            scores = [policy.score(c["features"]) for c in cands]
            props = _softmax(scores, policy.temperature, policy.exploration_floor)
            vals.append(fsum(p * slate["true_prob"][cid] for p, cid in zip(props, ids)))
        return fsum(vals) / len(vals) if vals else float("nan")

    def candidate_ids(self, slate_index: int) -> list[str]:
        return [c[self.config.candidate_id_field] for c in self.slates[slate_index]["candidates"]]

    def true_prob(self, slate_index: int, candidate_id: str) -> float:
        return float(self.slates[slate_index]["true_prob"][candidate_id])


def _softmax(scores: Sequence[float], temperature: float, floor: float) -> list[float]:
    top = max(scores)
    exps = [math.exp((s - top) / temperature) for s in scores]
    z = fsum(exps)
    base = [e / z for e in exps]
    n = len(scores)
    return [(1.0 - floor) * b + floor / n for b in base]


def make_scenario(
    *,
    n_slates: int = 800,
    n_candidates: int = 4,
    seed: str = "synthetic.world",
    behaviour_weights: Mapping[str, float] | None = None,
    candidate_weights: Mapping[str, float] | None = None,
    true_weights: Mapping[str, float] | None = None,
    true_bias: float = TRUE_BIAS,
    behaviour_floor: float = 0.10,
    candidate_floor: float = 0.10,
    temperature: float = 0.6,
    feature_names: Sequence[str] = SYNTH_FEATURES,
) -> SyntheticScenario:
    """Build the deterministic synthetic world (features + true probabilities)."""
    feature_names = tuple(feature_names)
    config = LogConfig(
        feature_names=feature_names,
        outcome_fields=SYNTH_OUTCOMES,
        candidate_id_field="synthetic.candidate_id",
        forbidden_feature_substrings=("outcome", "label", "reward"),
    )
    tw = dict(true_weights or TRUE_WEIGHTS)
    bw = dict(behaviour_weights or BEHAVIOUR_WEIGHTS)
    cw = dict(candidate_weights or CANDIDATE_WEIGHTS)
    behaviour = Policy(
        policy_id="synthetic.policy.behaviour.v1",
        weights={n: bw.get(n, 0.0) for n in feature_names},
        bias=0.0,
        temperature=temperature,
        exploration_floor=behaviour_floor,
        feature_names=feature_names,
    )
    candidate = Policy(
        policy_id="synthetic.policy.candidate.v1",
        weights={n: cw.get(n, 0.0) for n in feature_names},
        bias=0.0,
        temperature=temperature,
        exploration_floor=candidate_floor,
        feature_names=feature_names,
    )
    rng = HashRNG(seed)
    slates: list[dict[str, Any]] = []
    for i in range(int(n_slates)):
        cands = []
        probs: dict[str, float] = {}
        for j in range(int(n_candidates)):
            cid = f"synthetic.candidate.{i:04d}.{j}"
            feats = {n: round(rng.uniform(0.0, 1.0), 6) for n in feature_names}
            z = float(true_bias) + fsum(tw.get(n, 0.0) * feats[n] for n in feature_names)
            p = sigmoid(z)
            probs[cid] = p
            cands.append(
                {
                    config.candidate_id_field: cid,
                    "features": feats,
                    "field_ts": {n: 0.0 for n in feature_names},
                }
            )
        slates.append({"candidates": cands, "true_prob": probs})
    return SyntheticScenario(
        config=config,
        behaviour=behaviour,
        candidate=candidate,
        n_candidates=int(n_candidates),
        slates=slates,
        seed=seed,
    )


def write_log(
    root: str,
    scenario: SyntheticScenario,
    *,
    n_slates: int | None = None,
    dispatch_start: float = 1.7e9,
    dispatch_step: float = 60.0,
    label_delay: float = 3600.0,
    writer: str = "synthetic.dispatcher",
    partitions: int = 4,
    labeler: str = "synthetic.labeler",
) -> DecisionLog:
    """Write a decision log for the first ``n_slates`` slates of the scenario.

    Outcomes are appended in a **second pass** (late arrival), which is what the
    real system does: a decision is logged at dispatch, its acceptance is known
    only after the work item has been graded.  The logged ``chosen`` action is the realised
    draw from the behaviour propensities, not necessarily the top-ranked one.
    """
    os.makedirs(root, exist_ok=True)
    log = DecisionLog(root, config=scenario.config, partitions=partitions)
    n = len(scenario.slates) if n_slates is None else int(n_slates)
    rec_policy = scenario.behaviour.to_record_policy()
    pending: list[tuple[str, dict[str, float], float]] = []
    for i in range(n):
        slate = scenario.slates[i]
        decision = scenario.behaviour.decide(
            slate["candidates"], candidate_id_field=scenario.config.candidate_id_field
        )
        ids = list(decision["scores"].keys())
        pick = HashRNG(f"{scenario.seed}|action|{i}").choice(
            [decision["propensities"][cid] for cid in ids]
        )
        chosen = ids[pick]
        dispatch_ts = dispatch_start + i * dispatch_step
        record = log.append_decision(
            dispatch_ts=dispatch_ts,
            policy=rec_policy,
            candidates=slate["candidates"],
            scores=decision["scores"],
            ranking=decision["ranking"],
            chosen=chosen,
            propensities=decision["propensities"],
            exploration_floor=decision["exploration_floor"],
            writer=writer,
        )
        p = scenario.true_prob(i, chosen)
        y = 1.0 if HashRNG(f"{scenario.seed}|reward|{i}").random() < p else 0.0
        pending.append(
            (
                record["record_id"],
                {"synthetic.reward": y, "synthetic.accepted": y, "synthetic.cost": 1.0},
                dispatch_ts + label_delay,
            )
        )
    for record_id, outcome, ts in pending:
        log.append_outcome(record_id=record_id, outcome=outcome, outcome_ts=ts, source=labeler)
    log.write_manifest()
    return log