"""Deterministic replay engine.

Given a stored decision record and the pinned policy version it names, replay
recomputes scores, ranking, chosen action and propensities from the **logged
feature snapshot** and compares them to the logged decision *bit for bit*.

What "deterministic" is claimed to mean here (nothing more):

1. same record + same pinned policy kernel -> same float bit patterns
   (``struct.pack(">d", x)``) for every score and propensity, and the same
   ranking list;
2. no wall clock, no process RNG, no filesystem state is consulted -- the only
   inputs are the record bytes and the code;
3. reductions use exactly-rounded ``math.fsum`` and the ranking tie-break is
   ``(-score, candidate_id)``, so candidate iteration order cannot matter;
4. a policy whose *implementation* changed fails loudly (:class:`UnpinnedPolicyError`)
   instead of replaying "nearly" the same ranking.

What is **not** claimed: bitwise reproducibility across different CPUs/libm for
transcendental functions (``exp`` inside the softmax).  Operand-order and
rounding are controlled; the last bit of ``exp`` is a platform property.  The
engine therefore checks scores bitwise (pure multiply/add, exactly specified by
IEEE-754) and propensities bitwise *on the same platform*, while the cross-run
guarantee is documented in ``docs/DISPATCH_SHADOW_REPLAY.md`` §5.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Sequence

from .canonical import bit_equal, float_bits, ulp_distance
from .policy import Policy, UnpinnedPolicyError
from .schema import DecisionRecord, LogConfig, SchemaError

__all__ = [
    "ReplayMismatch",
    "ReplayReport",
    "recompute",
    "replay_record",
    "replay_log",
    "perturbation_probe",
]


@dataclass
class ReplayMismatch:
    record_id: str
    decision_seq: int
    code: str
    candidate_id: str | None = None
    logged: Any = None
    replayed: Any = None
    ulp: int | None = None

    def to_dict(self) -> dict[str, Any]:
        d = {
            "record_id": self.record_id,
            "decision_seq": self.decision_seq,
            "code": self.code,
        }
        if self.candidate_id is not None:
            d["candidate_id"] = self.candidate_id
        if self.ulp is not None:
            d["ulp_distance"] = int(self.ulp)
        d["logged"] = self.logged
        d["replayed"] = self.replayed
        return d


@dataclass
class ReplayReport:
    records: int = 0
    replayable: int = 0
    bitwise_equal_records: int = 0
    mismatches: list[ReplayMismatch] = field(default_factory=list)
    refused: list[dict[str, Any]] = field(default_factory=list)
    max_ulp: int = 0
    #: records where the realised action was *not* the policy's top-ranked one
    #: (a legitimate draw from the exploration floor -- not a replay failure)
    realised_off_top: int = 0

    @property
    def ok(self) -> bool:
        return not self.mismatches and not self.refused

    @property
    def fidelity(self) -> float:
        return (self.bitwise_equal_records / self.replayable) if self.replayable else 0.0

    def to_dict(self) -> dict[str, Any]:
        codes: dict[str, int] = {}
        for m in self.mismatches:
            codes[m.code] = codes.get(m.code, 0) + 1
        return {
            "records": self.records,
            "replayable": self.replayable,
            "bitwise_equal_records": self.bitwise_equal_records,
            "fidelity": self.fidelity,
            "mismatch_count": len(self.mismatches),
            "mismatch_codes": dict(sorted(codes.items())),
            "max_ulp_distance": int(self.max_ulp),
            "refusals": self.refused,
            "ok": self.ok,
            "realised_off_top": self.realised_off_top,
            "realised_off_top_frac": (self.realised_off_top / self.replayable)
            if self.replayable
            else 0.0,
            "first_mismatches": [m.to_dict() for m in self.mismatches[:5]],
        }


def _record_candidates(record: DecisionRecord) -> tuple[str, list[dict[str, Any]]]:
    field = record.data["slate"]["candidate_id_field"]
    return field, list(record.data["slate"]["candidates"])


def recompute(record: DecisionRecord, policy: Policy) -> dict[str, Any]:
    """Recompute the decision the given policy would make on the logged slate."""
    field, candidates = _record_candidates(record)
    return policy.decide(candidates, candidate_id_field=field)


def replay_record(
    record: DecisionRecord,
    policy: Policy,
    *,
    config: LogConfig | None = None,
) -> list[ReplayMismatch]:
    """Replay one record; return the mismatch list (empty == bitwise identical)."""
    if config is not None and record.data["config_sha256"] != config.sha256:
        raise SchemaError(
            "record was produced under a different feature/outcome config; "
            "replaying it under the current config would compare apples to oranges"
        )
    # the policy named by the record must be the policy we are replaying
    pinned = Policy.from_record(record.data["policy"])
    if pinned.sha256 != policy.sha256:
        raise UnpinnedPolicyError(
            "replay policy does not match the record's pinned policy version"
        )
    field, candidates = _record_candidates(record)
    logged = record.data["decision"]
    replayed = policy.decide(candidates, candidate_id_field=field)
    out: list[ReplayMismatch] = []

    for cand in candidates:
        cid = str(cand[field])
        a, b = float(logged["scores"][cid]), float(replayed["scores"][cid])
        if not bit_equal(a, b):
            out.append(
                ReplayMismatch(
                    record.record_id, record.decision_seq, "SCORE_BITS", cid,
                    float_bits(a), float_bits(b), ulp_distance(a, b),
                )
            )
        pa, pb = float(logged["propensities"][cid]), float(replayed["propensities"][cid])
        if not bit_equal(pa, pb):
            out.append(
                ReplayMismatch(
                    record.record_id, record.decision_seq, "PROPENSITY_BITS", cid,
                    float_bits(pa), float_bits(pb), ulp_distance(pa, pb),
                )
            )
    if list(logged["ranking"]) != list(replayed["ranking"]):
        out.append(
            ReplayMismatch(
                record.record_id, record.decision_seq, "RANKING", None,
                list(logged["ranking"]), list(replayed["ranking"]),
            )
        )
    # The *realised* action need not be the top-ranked one: an online dispatcher
    # draws from the exploration law, and that draw is exactly what an off-policy
    # estimator needs.  What replay must verify is that the realised action was
    # *supported* by the pinned policy law (and, below, that its propensity
    # matches bit for bit), not that it equals argmax.
    n = len(candidates)
    floor = float(replayed["exploration_floor"])
    chosen = logged["chosen"]
    if chosen not in replayed["scores"]:
        out.append(
            ReplayMismatch(
                record.record_id, record.decision_seq, "ACTION_NOT_IN_SLATE", None,
                chosen, list(replayed["scores"]),
            )
        )
    else:
        min_mass = floor / n
        if float(logged["propensities"][chosen]) + 1e-12 < min_mass:
            out.append(
                ReplayMismatch(
                    record.record_id, record.decision_seq, "ACTION_NOT_SUPPORTED", chosen,
                    float(logged["propensities"][chosen]), min_mass,
                )
            )
    if float(logged["exploration_floor"]) != float(replayed["exploration_floor"]):
        out.append(
            ReplayMismatch(
                record.record_id, record.decision_seq, "FLOOR", None,
                logged["exploration_floor"], replayed["exploration_floor"],
            )
        )
    return out


def replay_log(
    records: Sequence[DecisionRecord],
    policy: Policy,
    *,
    config: LogConfig | None = None,
) -> ReplayReport:
    """Replay every record under one pinned policy version."""
    report = ReplayReport(records=len(records))
    for rec in records:
        try:
            # pinning is checked per record: a log may contain several versions
            pinned = Policy.from_record(rec.data["policy"])
        except UnpinnedPolicyError as exc:
            report.refused.append(
                {"record_id": rec.record_id, "decision_seq": rec.decision_seq,
                 "code": "UNPINNED_POLICY", "detail": str(exc)}
            )
            continue
        if pinned.sha256 != policy.sha256:
            report.refused.append(
                {"record_id": rec.record_id, "decision_seq": rec.decision_seq,
                 "code": "POLICY_VERSION_MISMATCH",
                 "detail": f"record pinned {pinned.sha256[:12]}…, replaying {policy.sha256[:12]}…"}
            )
            continue
        try:
            mism = replay_record(rec, policy, config=config)
        except UnpinnedPolicyError as exc:
            report.refused.append(
                {"record_id": rec.record_id, "decision_seq": rec.decision_seq,
                 "code": "UNPINNED_POLICY", "detail": str(exc)}
            )
            continue
        report.replayable += 1
        if rec.data["decision"]["chosen"] != recompute_choice(rec, policy):
            report.realised_off_top += 1
        if mism:
            report.mismatches.extend(mism)
            report.max_ulp = max([report.max_ulp] + [m.ulp or 0 for m in mism])
        else:
            report.bitwise_equal_records += 1
    return report


def recompute_choice(record: DecisionRecord, policy: Policy) -> str:
    """Top-ranked candidate under ``policy`` (the *action the policy prefers*)."""
    field, candidates = _record_candidates(record)
    return policy.decide(candidates, candidate_id_field=field)["chosen"]


def perturbation_probe(
    records: Sequence[DecisionRecord],
    policy: Policy,
    *,
    ulps: Sequence[int] = (1, 2, 8, 1024),
    config: LogConfig | None = None,
) -> dict[str, Any]:
    """Inject a k-ulp perturbation into a logged score and check replay notices.

    A replay engine that only compares rankings would sleep through a 1-ulp
    change; one that compares scores with ``==`` would also sleep through a
    change that flips ``0.0`` to ``-0.0``.  This probe measures, per magnitude,
    how often the 1-ulp edit is detected (this is the *sensitivity* half of the
    fidelity claim; the clean-log half is :func:`replay_log`).
    """

    rows = []
    for k in ulps:
        detected = 0
        total = 0
        for rec in records:
            pinned = Policy.from_record(rec.data["policy"])
            if pinned.sha256 != policy.sha256:
                continue
            total += 1
            tampered = _tamper_score(rec, k)
            mism = replay_record(tampered, policy, config=config)
            if any(m.code == "SCORE_BITS" for m in mism):
                detected += 1
        rows.append(
            {"ulp_injected": int(k), "records": total, "detected": detected,
             "detection_rate": (detected / total) if total else 0.0}
        )
    return {"rows": rows}


def _tamper_score(record: DecisionRecord, ulps: int) -> DecisionRecord:
    """Return a copy of ``record`` with the first score moved by ``ulps`` ulps."""
    import copy

    data = copy.deepcopy(record.data)
    field = data["slate"]["candidate_id_field"]
    cid = str(data["slate"]["candidates"][0][field])
    val = float(data["decision"]["scores"][cid])
    data["decision"]["scores"][cid] = _bump(val, ulps)
    return DecisionRecord(data=data, outcome=record.outcome)


def _bump(value: float, ulps: int) -> float:
    import struct

    bits = struct.unpack(">q", struct.pack(">d", float(value)))[0]
    bits += ulps if value >= 0 else -ulps
    return struct.unpack(">d", struct.pack(">q", bits))[0]