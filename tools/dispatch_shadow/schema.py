"""Dispatch decision log, schema v1 -- records, outcomes, config, and the
structural guards that make a *leak* a representation error rather than a
statistical accident.

Layout of one decision record (dispatch-time partition, immutable)::

    {
      "schema_version": "dispatch-decision-log/1",
      "log_id": "<str>",
      "decision_seq": 0,
      "dispatch_ts": <float>,
      "policy": {"policy_id", "policy_sha256", "params"},
      "config_sha256": "<str>",
      "slate": {"candidate_id_field": "<name>",
                "candidates": [{"candidate_id": "<name>",
                                "features": {<feature>: <float>, ...},
                                "field_ts": {<feature>: <float>, ...}}]},
      "decision": {"scores": {<cid>: <float>},
                   "ranking": [<cid>, ...],
                   "chosen": <cid>,
                   "propensities": {<cid>: <float>},
                   "exploration_floor": <float>},
      "writer": "<str>",
      "record_id": "<sha256 of the body>",
      "prev_record_id": "<sha256 or null>",
      "chain_digest": "<sha256(prev_chain_digest || record_id)>"
    }

The *body* (everything except ``record_id`` / ``prev_record_id`` /
``chain_digest``) is content-addressed: appending an outcome later cannot change
a record's identity, because outcomes live in a **separate partition** keyed by
``record_id``::

    {"schema_version": "dispatch-outcome-log/1", "record_id": "<str>",
     "outcome_ts": <float>, "source": "<str>",
     "outcome": {<outcome field>: <float|int>, ...},
     "outcome_id": "<sha256>", "prev_outcome_id": "<str|null>",
     "chain_digest": "<sha256>"}

Domain independence: every name (candidate id field, feature names, outcome
fields) comes from an injected :class:`LogConfig`; nothing about any particular
application area is baked in.  Tests use ``synthetic.*`` names only.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Iterable, Mapping, Sequence

from .canonical import content_digest, sha256_hex

__all__ = [
    "SCHEMA_VERSION",
    "OUTCOME_SCHEMA_VERSION",
    "ROOT_FIELDS",
    "SchemaError",
    "LeakageError",
    "LogConfig",
    "DecisionRecord",
    "OutcomeRecord",
    "make_record",
    "make_outcome",
    "validate_record",
    "validate_outcome",
]

SCHEMA_VERSION = "dispatch-decision-log/1"
OUTCOME_SCHEMA_VERSION = "dispatch-outcome-log/1"

#: fields that may appear in a decision *body* (identity/chain fields excluded)
BODY_FIELDS = (
    "schema_version",
    "log_id",
    "decision_seq",
    "dispatch_ts",
    "policy",
    "config_sha256",
    "slate",
    "decision",
    "writer",
)

#: root fields allowed in a stored record (body + addressing metadata)
ROOT_FIELDS = BODY_FIELDS + ("record_id", "prev_record_id", "chain_digest")

OUTCOME_FIELDS = (
    "schema_version",
    "outcome_index",
    "record_id",
    "outcome_ts",
    "source",
    "outcome",
    "outcome_id",
    "prev_outcome_id",
    "chain_digest",
)


class SchemaError(ValueError):
    """A record does not conform to schema v1."""


class LeakageError(RuntimeError):
    """A decision record contains information that did not exist at dispatch time."""


@dataclass(frozen=True)
class LogConfig:
    """Injected, domain-free description of what a decision log contains.

    ``feature_names`` fixes the *iteration order* used by every policy and by
    replay, so a policy's score is a function of content only.  Anything named
    in ``outcome_fields`` (or matching ``forbidden_feature_substrings``) is a
    target leak if it shows up in the feature snapshot.
    """

    feature_names: tuple[str, ...]
    outcome_fields: tuple[str, ...]
    candidate_id_field: str = "candidate_id"
    forbidden_feature_substrings: tuple[str, ...] = ()
    propensity_tolerance: float = 1e-9
    schema_version: str = SCHEMA_VERSION

    def __post_init__(self) -> None:
        if not self.feature_names:
            raise SchemaError("config.feature_names must not be empty")
        if len(set(self.feature_names)) != len(self.feature_names):
            raise SchemaError("config.feature_names contains duplicates")
        overlap = set(self.feature_names) & set(self.outcome_fields)
        if overlap:
            raise LeakageError(f"feature names overlap outcome fields: {sorted(overlap)}")
        if self.candidate_id_field in self.feature_names:
            raise SchemaError("candidate_id_field may not also be a feature")

    # -- serialisation --------------------------------------------------
    def to_dict(self) -> dict[str, Any]:
        return {
            "feature_names": list(self.feature_names),
            "outcome_fields": list(self.outcome_fields),
            "candidate_id_field": self.candidate_id_field,
            "forbidden_feature_substrings": list(self.forbidden_feature_substrings),
            "propensity_tolerance": self.propensity_tolerance,
            "schema_version": self.schema_version,
        }

    @property
    def sha256(self) -> str:
        return content_digest(self.to_dict())

    @classmethod
    def from_dict(cls, d: Mapping[str, Any]) -> "LogConfig":
        unknown = set(d) - {
            "feature_names",
            "outcome_fields",
            "candidate_id_field",
            "forbidden_feature_substrings",
            "propensity_tolerance",
            "schema_version",
        }
        if unknown:
            raise SchemaError(f"unknown config keys: {sorted(unknown)}")
        return cls(
            feature_names=tuple(d["feature_names"]),
            outcome_fields=tuple(d.get("outcome_fields", ())),
            candidate_id_field=d.get("candidate_id_field", "candidate_id"),
            forbidden_feature_substrings=tuple(d.get("forbidden_feature_substrings", ())),
            propensity_tolerance=float(d.get("propensity_tolerance", 1e-9)),
            schema_version=d.get("schema_version", SCHEMA_VERSION),
        )


# ---------------------------------------------------------------------------
# record construction
# ---------------------------------------------------------------------------


@dataclass
class DecisionRecord:
    """Thin wrapper around the stored dict + the joined outcome (if any)."""

    data: dict[str, Any]
    outcome: dict[str, Any] | None = None

    # convenience accessors --------------------------------------------
    @property
    def record_id(self) -> str:
        return self.data["record_id"]

    @property
    def decision_seq(self) -> int:
        return int(self.data["decision_seq"])

    @property
    def dispatch_ts(self) -> float:
        return float(self.data["dispatch_ts"])

    @property
    def candidates(self) -> list[dict[str, Any]]:
        return list(self.data["slate"]["candidates"])

    @property
    def feature_names(self) -> tuple[str, ...]:
        # derived from the record itself so a reader does not need the config to
        # iterate in a replay-stable order
        cand = self.candidates[0]
        return tuple(cand["features"].keys())

    @property
    def policy_sha256(self) -> str:
        return self.data["policy"]["policy_sha256"]

    def features(self, candidate_id: str) -> dict[str, float]:
        for cand in self.candidates:
            if cand[self.data["slate"]["candidate_id_field"]] == candidate_id:
                return dict(cand["features"])
        raise KeyError(candidate_id)

    def reward(self, field_name: str = "reward") -> float | None:
        if self.outcome is None:
            return None
        val = self.outcome["outcome"].get(field_name)
        return None if val is None else float(val)

    def labelled(self) -> bool:
        return self.outcome is not None


@dataclass
class OutcomeRecord:
    data: dict[str, Any]

    @property
    def record_id(self) -> str:
        return self.data["record_id"]


def _check_finite(value: Any, where: str) -> float:
    try:
        f = float(value)
    except (TypeError, ValueError) as exc:  # pragma: no cover - defensive
        raise SchemaError(f"{where} is not numeric: {value!r}") from exc
    if not math.isfinite(f):
        raise SchemaError(f"{where} is not finite: {value!r}")
    return f


def _project_features(
    raw: Mapping[str, Any],
    config: LogConfig,
    *,
    candidate_id: str,
    where: str,
    dispatch_ts: float | None,
    field_ts: Mapping[str, Any] | None,
) -> dict[str, float]:
    """Validate one candidate's feature snapshot and return it in config order."""
    missing = [n for n in config.feature_names if n not in raw]
    if missing:
        raise SchemaError(f"{where} candidate {candidate_id!r} missing features {missing}")
    extra = [n for n in raw if n not in config.feature_names]
    if extra:
        # extras are *rejected*, not ignored: an unregistered column is either a
        # typo (silently drops signal) or a leak (silently adds it)
        raise SchemaError(f"{where} candidate {candidate_id!r} has unregistered features {extra}")
    for name in config.forbidden_feature_substrings:
        bad = [n for n in raw if name in n]
        if bad:
            raise LeakageError(
                f"{where} candidate {candidate_id!r} feature names contain forbidden "
                f"substring {name!r}: {bad}"
            )
    out: dict[str, float] = {}
    for name in config.feature_names:
        val = _check_finite(raw[name], f"{where}.{candidate_id}.{name}")
        if dispatch_ts is not None:
            if field_ts is None or name not in field_ts:
                raise LeakageError(
                    f"{where} candidate {candidate_id!r} feature {name!r} has no "
                    "availability timestamp"
                )
            stamp = _check_finite(field_ts[name], f"{where}.field_ts.{name}")
            if stamp > dispatch_ts:
                raise LeakageError(
                    f"{where} candidate {candidate_id!r} feature {name!r} became "
                    f"available at {stamp} > dispatch_ts {dispatch_ts}"
                )
        out[name] = val
    return out


def make_record(
    *,
    config: LogConfig,
    log_id: str,
    decision_seq: int,
    dispatch_ts: float,
    policy: Mapping[str, Any],
    candidates: Sequence[Mapping[str, Any]],
    scores: Mapping[str, float],
    ranking: Sequence[str],
    chosen: str,
    propensities: Mapping[str, float],
    exploration_floor: float,
    writer: str = "synthetic.writer",
) -> dict[str, Any]:
    """Validate, canonicalise and content-address one decision record.

    Raises :class:`LeakageError` if any feature is unavailable at dispatch time
    or if an outcome field smuggles itself into the feature snapshot, and
    :class:`SchemaError` for structural problems.
    """
    if config.schema_version != SCHEMA_VERSION:
        raise SchemaError(f"unsupported schema_version {config.schema_version!r}")
    if not isinstance(log_id, str) or not log_id:
        raise SchemaError("log_id must be a non-empty string")
    if int(decision_seq) < 0:
        raise SchemaError("decision_seq must be >= 0")
    dispatch_ts = _check_finite(dispatch_ts, "dispatch_ts")

    for key in ("policy_id", "policy_sha256"):
        if key not in policy:
            raise SchemaError(f"policy.{key} is required")
    if len(str(policy["policy_sha256"])) != 64:
        raise SchemaError("policy.policy_sha256 must be a sha256 hex digest")

    if not candidates:
        raise SchemaError("slate must contain at least one candidate")
    ids: list[str] = []
    clean_candidates: list[dict[str, Any]] = []
    for cand in candidates:
        cid = cand.get(config.candidate_id_field) or cand.get("candidate_id")
        if not cid:
            raise SchemaError("candidate missing candidate id field")
        cid = str(cid)
        if cid in ids:
            raise SchemaError(f"duplicate candidate id {cid!r} in slate")
        ids.append(cid)
        feats = _project_features(
            cand["features"],
            config,
            candidate_id=cid,
            where=f"record[seq={decision_seq}]",
            dispatch_ts=dispatch_ts,
            field_ts=cand.get("field_ts"),
        )
        clean_candidates.append(
            {
                config.candidate_id_field: cid,
                "features": feats,
                "field_ts": {n: _check_finite((cand.get("field_ts") or {})[n], n) for n in config.feature_names},
            }
        )

    if set(scores) != set(ids):
        raise SchemaError("decision.scores must cover exactly the slate candidates")
    if len(ranking) != len(ids) or set(ranking) != set(ids):
        raise SchemaError("decision.ranking must be a permutation of the slate")
    if chosen not in ids:
        raise SchemaError("decision.chosen must be a candidate of the slate")
    if set(propensities) != set(ids):
        raise SchemaError("decision.propensities must cover exactly the slate")

    clean_scores = {cid: _check_finite(scores[cid], f"scores.{cid}") for cid in ids}
    clean_props = {cid: _check_finite(propensities[cid], f"propensities.{cid}") for cid in ids}
    if any(p <= 0.0 for p in clean_props.values()):
        raise SchemaError("propensities must be strictly positive (positivity)")
    total = math.fsum(clean_props.values())
    if abs(total - 1.0) > max(config.propensity_tolerance, 1e-6) * max(1, len(ids)):
        raise SchemaError(f"propensities must sum to 1 (got {total!r})")

    body: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "log_id": log_id,
        "decision_seq": int(decision_seq),
        "dispatch_ts": dispatch_ts,
        "policy": {
            "policy_id": str(policy["policy_id"]),
            "policy_sha256": str(policy["policy_sha256"]),
            "params": dict(policy.get("params") or {}),
        },
        "config_sha256": config.sha256,
        "slate": {
            "candidate_id_field": config.candidate_id_field,
            "candidates": clean_candidates,
        },
        "decision": {
            "scores": {cid: clean_scores[cid] for cid in ids},
            "ranking": [str(c) for c in ranking],
            "chosen": str(chosen),
            "propensities": {cid: clean_props[cid] for cid in ids},
            "exploration_floor": _check_finite(exploration_floor, "exploration_floor"),
        },
        "writer": str(writer),
    }
    record = dict(body)
    record["record_id"] = content_digest(body)
    record["prev_record_id"] = None
    record["chain_digest"] = sha256_hex(f"{record['record_id']}")
    return record


def make_outcome(
    *,
    outcome_index: int,
    record_id: str,
    outcome: Mapping[str, Any],
    outcome_ts: float,
    source: str,
    prev_outcome_id: str | None = None,
    prev_outcome_chain: str | None = None,
    config: LogConfig | None = None,
) -> dict[str, Any]:
    """Content-address an outcome row (stored in the outcome partition)."""
    if int(outcome_index) < 0:
        raise SchemaError("outcome_index must be >= 0")
    if not record_id or len(record_id) != 64:
        raise SchemaError("record_id must be a sha256 hex digest")
    clean: dict[str, float] = {}
    for name, val in outcome.items():
        if config is not None and config.outcome_fields and name not in config.outcome_fields:
            raise SchemaError(f"unregistered outcome field {name!r}")
        clean[name] = _check_finite(val, f"outcome.{name}")
    if not clean:
        raise SchemaError("outcome must not be empty")
    body = {
        "schema_version": OUTCOME_SCHEMA_VERSION,
        "outcome_index": int(outcome_index),
        "record_id": record_id,
        "outcome_ts": _check_finite(outcome_ts, "outcome_ts"),
        "source": str(source),
        "outcome": clean,
    }
    row = dict(body)
    row["outcome_id"] = content_digest(body)
    row["prev_outcome_id"] = prev_outcome_id
    row["chain_digest"] = sha256_hex(f"{prev_outcome_chain or ''}|{row['outcome_id']}")
    return row


# ---------------------------------------------------------------------------
# validation of stored rows (used by the store on read)
# ---------------------------------------------------------------------------


def validate_record(record: Mapping[str, Any], *, config: LogConfig | None = None) -> None:
    unknown = set(record) - set(ROOT_FIELDS)
    if unknown:
        raise SchemaError(f"record has unknown root fields: {sorted(unknown)}")
    missing = [f for f in ROOT_FIELDS if f not in record]
    if missing:
        raise SchemaError(f"record missing root fields: {missing}")
    if record["schema_version"] != SCHEMA_VERSION:
        raise SchemaError(f"record schema_version {record['schema_version']!r} != {SCHEMA_VERSION!r}")
    body = {k: record[k] for k in BODY_FIELDS}
    if content_digest(body) != record["record_id"]:
        raise SchemaError(f"record_id mismatch for seq {record.get('decision_seq')}")
    if config is not None and record["config_sha256"] != config.sha256:
        raise SchemaError("record was written under a different config (config_sha256 mismatch)")
    dec = record["decision"]
    ids = [c[record["slate"]["candidate_id_field"]] for c in record["slate"]["candidates"]]
    if dec["chosen"] not in ids:
        raise SchemaError("chosen candidate not in slate")
    if any(not math.isfinite(float(v)) for v in dec["scores"].values()):
        raise SchemaError("non-finite score in stored record")


def validate_outcome(row: Mapping[str, Any], *, config: LogConfig | None = None) -> None:
    unknown = set(row) - set(OUTCOME_FIELDS)
    if unknown:
        raise SchemaError(f"outcome row has unknown fields: {sorted(unknown)}")
    missing = [f for f in OUTCOME_FIELDS if f not in row]
    if missing:
        raise SchemaError(f"outcome row missing fields: {missing}")
    if row["schema_version"] != OUTCOME_SCHEMA_VERSION:
        raise SchemaError("outcome schema_version mismatch")
    body = {k: row[k] for k in OUTCOME_FIELDS if k not in ("outcome_id", "prev_outcome_id", "chain_digest")}
    if content_digest(body) != row["outcome_id"]:
        raise SchemaError(f"outcome_id mismatch for record {row.get('record_id')}")
    if config is not None and config.outcome_fields:
        extra = [k for k in row["outcome"] if k not in config.outcome_fields]
        if extra:
            raise SchemaError(f"unregistered outcome fields: {extra}")


def legacy_feature_check(config: LogConfig, names: Iterable[str]) -> None:
    """Guard for call sites that build feature dicts outside :func:`make_record`."""
    names = list(names)
    leaked = [n for n in names if n in set(config.outcome_fields)]
    if leaked:
        raise LeakageError(f"outcome field used as feature: {leaked}")