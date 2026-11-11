"""Leakage guards and negative controls for the decision log.

Three independent layers, because a leak has three different shapes:

* **L1 name guard** (:func:`scan_structural`) -- a feature is literally an
  outcome field, or carries a configured forbidden substring.  Detected by
  comparing the *registered* feature names against the outcome namespace; a
  record that fails to rebuild at all is already a fail (schema v1 rejects
  unregistered columns instead of ignoring them).
* **L2 time guard** -- a legitimately named field that was refreshed *after*
  dispatch (``field_ts > dispatch_ts``).  This is the shape that hurts in
  practice: the column looks dispatch-time, the value is post-hoc.
* **L3 statistical guard** (:class:`LeakageDetector`) -- a feature whose value
  predicts the outcome almost perfectly.  No name or timestamp says anything
  about this; only the joint distribution does.  It is the natural *negative
  control*: inject ``outcome`` (optionally with label noise) into the feature
  snapshot and the detector must alarm, while genuine features must not.

The detector is deliberately univariate and threshold-on-AUC: with a handful of
candidates per round and a few hundred rounds, a multivariate drift detector
would fire on noise and teach everyone to ignore it.  AUC >= 0.95 means "this
column is (almost) the label", which no honest dispatch-time feature can be.
"""

from __future__ import annotations

import copy
import math
from dataclasses import dataclass
from typing import Any, Iterable, Mapping, Sequence

from .canonical import HashRNG, fsum
from .schema import DecisionRecord, LeakageError, LogConfig

__all__ = [
    "augment_config_with_feature",
    "scan_structural",
    "auc",
    "LeakageDetector",
    "inject_outcome_feature",
    "null_replications",
]


# ---------------------------------------------------------------------------
# L1/L2: structural scans
# ---------------------------------------------------------------------------


def scan_structural(
    records: Iterable[DecisionRecord], config: LogConfig
) -> dict[str, Any]:
    """Return structural alarms (empty list == clean)."""
    alarms: list[dict[str, Any]] = []
    n = 0
    for rec in records:
        n += 1
        data = rec.data
        field = data["slate"]["candidate_id_field"]
        for cand in data["slate"]["candidates"]:
            cid = str(cand[field])
            for name in cand["features"]:
                if name in config.outcome_fields:
                    alarms.append(
                        {"code": "OUTCOME_FIELD_AS_FEATURE", "record_id": rec.record_id,
                         "candidate_id": cid, "field": name}
                    )
                for sub in config.forbidden_feature_substrings:
                    if sub in name:
                        alarms.append(
                            {"code": "FORBIDDEN_SUBSTRING", "record_id": rec.record_id,
                             "candidate_id": cid, "field": name, "substring": sub}
                        )
            for name, stamp in (cand.get("field_ts") or {}).items():
                if float(stamp) > float(data["dispatch_ts"]):
                    alarms.append(
                        {"code": "FIELD_AVAILABLE_AFTER_DISPATCH", "record_id": rec.record_id,
                         "candidate_id": cid, "field": name,
                         "field_ts": float(stamp), "dispatch_ts": float(data["dispatch_ts"])}
                    )
    return {"records": n, "alarms": alarms, "ok": not alarms}


# ---------------------------------------------------------------------------
# L3: statistical leak detector (AUC based, univariate)
# ---------------------------------------------------------------------------


def auc(scores: Sequence[float], labels: Sequence[int]) -> float:
    """Rank-based Mann-Whitney AUC with tie averaging (deterministic)."""
    if len(scores) != len(labels):
        raise ValueError("scores and labels must have equal length")
    pairs = sorted(zip((float(s) for s in scores), (int(y) for y in labels)))
    n = len(pairs)
    pos = sum(y for _, y in pairs)
    neg = n - pos
    if pos == 0 or neg == 0:
        return float("nan")
    # average ranks (1-based), ties share the mean rank
    ranks = [0.0] * n
    i = 0
    while i < n:
        j = i
        while j + 1 < n and pairs[j + 1][0] == pairs[i][0]:
            j += 1
        avg = (i + j + 2) / 2.0
        for k in range(i, j + 1):
            ranks[k] = avg
        i = j + 1
    rank_sum_pos = fsum(ranks[k] for k in range(n) if pairs[k][1] == 1)
    return (rank_sum_pos - pos * (pos + 1) / 2.0) / (pos * neg)


@dataclass
class LeakageDetector:
    threshold: float = 0.95
    outcome_field: str = ""
    label_rule: str = "binarize>0.5"

    def _labels(self, records: Sequence[DecisionRecord]) -> list[int]:
        labels: list[int] = []
        for rec in records:
            val = rec.reward(self.outcome_field)
            if val is None:
                continue
            labels.append(int(val > 0.5))
        return labels

    def feature_matrix(
        self, records: Sequence[DecisionRecord], feature_names: Sequence[str]
    ) -> dict[str, list[float]]:
        cols: dict[str, list[float]] = {n: [] for n in feature_names}
        for rec in records:
            if rec.reward(self.outcome_field) is None:
                continue
            # one row per *chosen* candidate: the leak that matters is the one in
            # the feature snapshot of the action that was taken
            feat = rec.features(rec.data["decision"]["chosen"])
            for n in feature_names:
                cols[n].append(float(feat[n]))
        return cols

    def scan(
        self,
        records: Sequence[DecisionRecord],
        feature_names: Sequence[str],
        *,
        extra_features: Mapping[str, Sequence[float]] | None = None,
    ) -> dict[str, Any]:
        labelled = [r for r in records if r.reward(self.outcome_field) is not None]
        labels = self._labels(labelled)
        cols = self.feature_matrix(labelled, feature_names)
        if extra_features:
            for n, vals in extra_features.items():
                cols[n] = list(vals)
        per_feature = {}
        alarms = []
        for name, vals in cols.items():
            a = auc(vals, labels)
            per_feature[name] = a
            if math.isfinite(a) and a >= self.threshold:
                alarms.append(
                    {"code": "LEAKY_FEATURE", "field": name, "auc": a,
                     "threshold": self.threshold}
                )
        return {
            "outcome_field": self.outcome_field,
            "threshold": self.threshold,
            "rounds": len(labelled),
            "per_feature_auc": dict(sorted(per_feature.items())),
            "max_auc": max((v for v in per_feature.values() if math.isfinite(v)), default=float("nan")),
            "alarms": alarms,
            "ok": not alarms,
        }


# ---------------------------------------------------------------------------
# negative control: inject the outcome into the feature snapshot
# ---------------------------------------------------------------------------


def augment_config_with_feature(config: LogConfig, name: str) -> LogConfig:
    """Return a config that additionally registers ``name`` as a feature.

    Used to build the *smuggled* leak: the field name is registered, so the name
    guard cannot fire -- exactly the adversarial case the L3 detector exists
    for.
    """
    if name in config.outcome_fields:
        raise LeakageError(
            "cannot register an outcome field as a feature: the name guard is "
            "supposed to catch that"
        )
    return LogConfig(
        feature_names=tuple(list(config.feature_names) + [name]),
        outcome_fields=config.outcome_fields,
        candidate_id_field=config.candidate_id_field,
        forbidden_feature_substrings=config.forbidden_feature_substrings,
        propensity_tolerance=config.propensity_tolerance,
    )


def inject_outcome_feature(
    records: Sequence[DecisionRecord],
    *,
    outcome_field: str,
    feature_name: str,
    leak_prob: float = 1.0,
    seed: str = "synthetic.leak",
) -> list[DecisionRecord]:
    """Return copies whose *features* carry the outcome value.

    ``leak_prob`` is the probability that the injected column equals the true
    outcome; ``0.5`` gives a pure-noise column (the negative control's
    specificity arm), ``1.0`` an exact copy of the label (sensitivity arm).

    The injected values are attached to every candidate of the slate, so the
    detector's per-round column is the one belonging to the chosen candidate.
    """
    rng = HashRNG(seed)
    out: list[DecisionRecord] = []
    for rec in records:
        data = copy.deepcopy(rec.data)
        reward = rec.reward(outcome_field)
        if reward is not None:
            y = 1.0 if reward > 0.5 else 0.0
            flip = rng.random() > float(leak_prob)
            val = (1.0 - y) if flip else y
            for cand in data["slate"]["candidates"]:
                cand["features"][feature_name] = val
                cand["field_ts"][feature_name] = float(data["dispatch_ts"])
        out.append(DecisionRecord(data=data, outcome=rec.outcome))
    return out


def null_replications(
    records: Sequence[DecisionRecord],
    feature_names: Sequence[str],
    *,
    outcome_field: str,
    threshold: float,
    replications: int = 100,
    seed: str = "synthetic.null",
) -> dict[str, Any]:
    """Estimate the detector's false-positive rate by permuting the labels.

    Under the null (feature independent of outcome) the AUC distribution is
    centred on 0.5; the empirical fraction of replications where *any* feature
    crosses ``threshold`` is the false-alarm rate we must quote next to the
    detection rate.  Permuting labels keeps the feature marginals fixed.
    """
    labelled = [r for r in records if r.reward(outcome_field) is not None]
    det = LeakageDetector(threshold=threshold, outcome_field=outcome_field)
    cols = det.feature_matrix(labelled, feature_names)
    labels = [int(r.reward(outcome_field) > 0.5) for r in labelled]
    rng = HashRNG(seed)
    over = 0
    max_aucs: list[float] = []
    for _ in range(int(replications)):
        perm = list(range(len(labels)))
        # Fisher-Yates with the counter-mode RNG
        for i in range(len(perm) - 1, 0, -1):
            j = rng.randint(i + 1)
            perm[i], perm[j] = perm[j], perm[i]
        y = [labels[p] for p in perm]
        top = -1.0
        for name in feature_names:
            a = auc(cols[name], y)
            if math.isfinite(a):
                top = max(top, a)
        max_aucs.append(top)
        if top >= threshold:
            over += 1
    max_aucs.sort()
    return {
        "replications": int(replications),
        "threshold": float(threshold),
        "false_alarm_replications": over,
        "false_alarm_rate": over / float(replications) if replications else float("nan"),
        "max_auc_quantiles": {
            "median": max_aucs[len(max_aucs) // 2] if max_aucs else float("nan"),
            "p95": max_aucs[min(len(max_aucs) - 1, int(0.95 * len(max_aucs)))] if max_aucs else float("nan"),
        },
    }