"""Mission gradient & exploration budget mechanism (task-gradient governance).

Purpose
-------
Missions are seeded on a five-step task gradient instead of a flat queue:

    S1 执行 (execute)   — reproduce / run an existing, known-good procedure
    S2 改进 (improve)   — extend or fix something that already exists and works
    S3 建设 (build)     — build new capability that did not exist before
    S4 突破 (breakthrough) — open-ended research with unknown outcome
    S5 创造 (create)    — frontier work whose success criterion is invented

Three governance rules ride on the gradient:

1. **Mix ratio registry.**  Seeding follows a registered mix, starting at
   60/25/10/4/1 (S1..S5).  The mix is adjustable, but only through a validated,
   versioned record: weights must be non-negative and sum to 1 (±``MIX_TOL``).

2. **Exploration budget for S4/S5.**  Only breakthrough/create missions carry an
   *exploration budget* marker.  Missions carrying it may end in an
   **honest FAIL** — a documented negative result — which is quarantined from the
   worker's failure rate.  An honest FAIL is only admissible when the worker left
   a root cause *and* a retrospective; otherwise it is re-classified as a plain
   failure (it counts, no exceptions).  A plain (non-exploration) mission can
   never claim an honest FAIL: there is no exploration budget to spend.

3. **Credit-tier matched dispatch.**  Worker credit levels are L1..L5.  A mission
   on tier ``Sn`` requires at least ``Ln`` credit: L4+ may be dispatched an S4
   mission, only L5 may be dispatched an S5 mission.  The dispatcher asks
   :func:`dispatch_decide` and gets a decision with an explicit reason code.

Design constraints honoured here
-------------------------------
* Zero domain semantics: this module knows about tiers, quotas, credit levels and
  outcomes only.  No physics/vessel vocabulary is hard-coded (a machine check in
  ``backend/tests/test_mission_gradient.py`` enforces this).
* No I/O, no DB, no network: every function is pure and deterministic, so the
  dispatcher, the API layer and the acceptance harness can all share it.
* Append-only honesty: :func:`guard_honest_fail` refuses to overwrite a recorded
  honest FAIL, so a negative result cannot be papered over later.

Environment flag (integration point)
------------------------------------
``TP_MISSION_GRADIENT=1`` enables the API surface
(``backend/api/mission_gradient.py``).  Default is *off*: the mechanism lands as
a self-contained, additive capability and the live dispatch path is untouched
until the platform freeze is lifted (see ``docs/mission_gradient_mechanism.md``).
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping, Sequence

__all__ = [
    "GRADIENT_TIERS",
    "TIER_LABELS",
    "EXPLORATION_TIERS",
    "DEFAULT_MIX",
    "MIX_TOL",
    "CREDIT_LEVELS",
    "ROOT_CAUSE_MIN_CHARS",
    "RETROSPECTIVE_MIN_CHARS",
    "MixError",
    "GradientError",
    "normalise_tier",
    "tier_label",
    "validate_mix",
    "mix_signature",
    "allocate_quota",
    "min_credit_level",
    "normalise_credit_level",
    "exploration_budget",
    "mission_gradient_payload",
    "gradient_defaults_ok",
    "Outcome",
    "classify_outcome",
    "failure_accounting",
    "guard_honest_fail",
    "negative_result_entry",
    "dispatch_decide",
    "self_declare",
    "evaluate_q1",
]

# ─────────────────────────────── constants ───────────────────────────────

GRADIENT_TIERS: tuple[str, ...] = ("S1", "S2", "S3", "S4", "S5")

TIER_LABELS: dict[str, str] = {
    "S1": "执行",
    "S2": "改进",
    "S3": "建设",
    "S4": "突破",
    "S5": "创造",
}

#: tiers allowed to carry an exploration budget marker
EXPLORATION_TIERS: frozenset[str] = frozenset({"S4", "S5"})

#: starting seeding ratio, adjustable through :func:`validate_mix`
DEFAULT_MIX: dict[str, float] = {"S1": 0.60, "S2": 0.25, "S3": 0.10, "S4": 0.04, "S5": 0.01}

#: tolerance for "weights sum to one"
MIX_TOL = 1e-6

CREDIT_LEVELS: tuple[int, ...] = (1, 2, 3, 4, 5)

#: an honest FAIL needs a diagnosis, not a shrug
ROOT_CAUSE_MIN_CHARS = 8
RETROSPECTIVE_MIN_CHARS = 20

_TIER_RE = re.compile(r"^[Ss]?([1-5])$")
_LEVEL_RE = re.compile(r"^[Ll]?([1-5])$")

OUTCOME_SUCCEEDED = "succeeded"
OUTCOME_FAILED = "failed"
OUTCOME_HONEST_FAIL = "honest_fail"

BASE_BUDGET_KEYS: tuple[str, ...] = ("max_tool_calls", "max_wall_seconds", "max_tokens")


class GradientError(ValueError):
    """Raised for malformed gradient / credit / outcome input."""


class MixError(GradientError):
    """Raised when a seeding mix is not a valid probability distribution."""


# ──────────────────────────────── tiers ─────────────────────────────────


def normalise_tier(tier: Any) -> str:
    """Normalise ``sn``/``S3``/``3`` to ``S3``.  Raises :class:`GradientError`."""
    if isinstance(tier, int) and not isinstance(tier, bool):
        if 1 <= tier <= 5:
            return f"S{tier}"
        raise GradientError(f"tier index out of range: {tier}")
    if isinstance(tier, str):
        m = _TIER_RE.match(tier.strip())
        if m:
            return f"S{m.group(1)}"
    raise GradientError(f"unknown gradient tier: {tier!r}")


def tier_label(tier: Any) -> str:
    """Human label of a tier (执行/改进/建设/突破/创造)."""
    return TIER_LABELS[normalise_tier(tier)]


def _is_exploration(tier: str) -> bool:
    return tier in EXPLORATION_TIERS


# ──────────────────────────────── mix ────────────────────────────────────


def validate_mix(mix: Mapping[str, Any] | None) -> dict[str, float]:
    """Validate and return a canonical ``{S1..S5: weight}`` mapping.

    Rules: every key is a gradient tier, weights are finite and non-negative, and
    they sum to 1 within :data:`MIX_TOL`.  Missing tiers default to ``0.0`` so a
    campaign may legitimately run without, say, S5 slots.
    """
    if mix is None:
        return dict(DEFAULT_MIX)
    if not isinstance(mix, Mapping) or not mix:
        raise MixError("mix must be a non-empty mapping of tier -> weight")

    out: dict[str, float] = {t: 0.0 for t in GRADIENT_TIERS}
    for key, value in mix.items():
        try:
            tier = normalise_tier(key)
        except GradientError as exc:
            raise MixError(str(exc)) from exc
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise MixError(f"mix[{tier}] must be a number, got {type(value).__name__}")
        w = float(value)
        if w != w or w in (float("inf"), float("-inf")):
            raise MixError(f"mix[{tier}] must be finite")
        if w < 0:
            raise MixError(f"mix[{tier}] must be non-negative, got {w}")
        out[tier] = w

    total = sum(out.values())
    if abs(total - 1.0) > MIX_TOL:
        raise MixError(
            f"mix weights must sum to 1.0 (±{MIX_TOL}); got {total:.9f} "
            f"(off by {total - 1.0:+.9f})"
        )
    return out


def mix_signature(mix: Mapping[str, Any]) -> str:
    """Stable short fingerprint of a validated mix (audit trail / versioning)."""
    canonical = validate_mix(mix)
    blob = json.dumps({t: round(canonical[t], 9) for t in GRADIENT_TIERS}, sort_keys=True)
    return hashlib.sha256(blob.encode()).hexdigest()[:16]


def allocate_quota(total: int, mix: Mapping[str, Any] | None = None) -> dict[str, int]:
    """Allocate ``total`` seeding slots over the mix (Hamilton largest remainder).

    Deterministic: ties on the remainder are broken by tier order (S1 first), so
    the same input always yields the same plan — a prerequisite for reproducible
    campaign replay.
    """
    if isinstance(total, bool) or not isinstance(total, int):
        raise GradientError(f"total must be an int, got {type(total).__name__}")
    if total < 0:
        raise GradientError(f"total must be >= 0, got {total}")
    canonical = validate_mix(mix)

    raw = {t: total * canonical[t] for t in GRADIENT_TIERS}
    floors = {t: int(raw[t]) for t in GRADIENT_TIERS}
    remainder = total - sum(floors.values())

    order = sorted(GRADIENT_TIERS, key=lambda t: (-(raw[t] - floors[t]), GRADIENT_TIERS.index(t)))
    for t in order[:remainder] if remainder > 0 else []:
        floors[t] += 1
    return floors


# ─────────────────────────────── credit ─────────────────────────────────


def min_credit_level(tier: Any) -> int:
    """Minimum worker credit level required to be dispatched ``tier``.

    ``Sn`` requires ``Ln``: L4+ may take S4 (breakthrough) missions, only L5 may
    take S5 (create) missions.
    """
    return int(normalise_tier(tier)[1])


def normalise_credit_level(level: Any) -> int:
    """Accept ``4``, ``"4"``, ``"L4"`` and return ``4``."""
    if isinstance(level, bool):
        raise GradientError("credit level must be an int 1..5, not bool")
    if isinstance(level, int):
        if level in CREDIT_LEVELS:
            return level
        raise GradientError(f"credit level out of range 1..5: {level}")
    if isinstance(level, str):
        m = _LEVEL_RE.match(level.strip())
        if m and int(m.group(1)) in CREDIT_LEVELS:
            return int(m.group(1))
    raise GradientError(f"unknown credit level: {level!r}")


def credit_permits(level: Any, tier: Any) -> bool:
    """True when a worker at ``level`` may be dispatched a ``tier`` mission."""
    return normalise_credit_level(level) >= min_credit_level(tier)


# ──────────────────────── exploration budget marker ─────────────────────


def _base_budget(base_budget: Mapping[str, Any] | None) -> dict[str, int]:
    base = {k: 0 for k in BASE_BUDGET_KEYS}
    if base_budget:
        for k in BASE_BUDGET_KEYS:
            if k in (base_budget or {}):
                v = base_budget[k]
                if isinstance(v, bool) or not isinstance(v, (int, float)) or v < 0:
                    raise GradientError(f"base_budget[{k}] must be a non-negative number")
                base[k] = int(v)
    return base


def exploration_budget(
    tier: Any,
    base_budget: Mapping[str, Any] | None = None,
    mix: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Exploration-budget marker for a mission on ``tier``.

    S1..S3 return ``exploration=False`` / ``failure_quarantined=False``: their
    failures are ordinary failures.  S4/S5 return the marker plus the share of
    the plan they are entitled to and the evidence an honest FAIL must carry.
    """
    t = normalise_tier(tier)
    canonical = validate_mix(mix)
    base = _base_budget(base_budget)

    if not _is_exploration(t):
        return {
            "tier": t,
            "label": TIER_LABELS[t],
            "exploration": False,
            "failure_quarantined": False,
            "min_credit_level": min_credit_level(t),
            "honest_fail_allowed": False,
            "budget": base,
        }

    expl_total = canonical["S4"] + canonical["S5"]
    share_within = (canonical[t] / expl_total) if expl_total > 0 else 0.0
    return {
        "tier": t,
        "label": TIER_LABELS[t],
        "exploration": True,
        "failure_quarantined": True,
        "min_credit_level": min_credit_level(t),
        "honest_fail_allowed": True,
        "share_of_plan": canonical[t],
        "share_within_exploration": round(share_within, 9),
        "reserve_tool_calls": int(round(base["max_tool_calls"] * share_within)),
        "honest_fail_requires": ["root_cause", "retrospective", "negative_result_entry"],
        "root_cause_min_chars": ROOT_CAUSE_MIN_CHARS,
        "retrospective_min_chars": RETROSPECTIVE_MIN_CHARS,
        "budget": base,
        "mix_signature": mix_signature(canonical),
    }


def mission_gradient_payload(
    tier: Any,
    base_budget: Mapping[str, Any] | None = None,
    mix: Mapping[str, Any] | None = None,
    *,
    self_declared: bool = False,
) -> dict[str, Any]:
    """The gradient field bundle a mission row carries (``metadata.gradient``)."""
    marker = exploration_budget(tier, base_budget, mix)
    return {
        "gradient": {
            **marker,
            "self_declared": bool(self_declared),
            "mix_signature": mix_signature(validate_mix(mix)),
        }
    }


def gradient_defaults_ok(payload: Mapping[str, Any] | None) -> bool:
    """Cheap admission check: does a mission payload carry usable gradient fields?"""
    if not isinstance(payload, Mapping):
        return False
    grad = payload.get("gradient")
    if not isinstance(grad, Mapping):
        return False
    try:
        tier = normalise_tier(grad.get("tier"))
    except GradientError:
        return False
    if "exploration" not in grad or "min_credit_level" not in grad:
        return False
    if bool(grad["exploration"]) != _is_exploration(tier):
        return False
    return int(grad["min_credit_level"]) == min_credit_level(tier)


# ─────────────────────────────── outcomes ───────────────────────────────


@dataclass(frozen=True)
class Outcome:
    """One finished mission outcome, as reported by a worker."""

    worker_id: str
    mission_id: str
    tier: str
    status: str
    root_cause: str = ""
    retrospective: str = ""
    evidence_ref: str = ""
    meta: Mapping[str, Any] = field(default_factory=dict)

    @classmethod
    def build(cls, **kw: Any) -> "Outcome":
        return cls(**kw)


def _text_len(value: Any) -> int:
    return len(value.strip()) if isinstance(value, str) else 0


def classify_outcome(outcome: Outcome | Mapping[str, Any]) -> dict[str, Any]:
    """Classify an outcome into ``succeeded`` / ``failed`` / quarantined honest FAIL.

    Returns a dict with the effective class, whether it is quarantined (excluded
    from the worker failure rate), and why.  A claimed honest FAIL that lacks a
    root cause or a retrospective is **not** quarantined: it is booked as a plain
    failure, so "honest FAIL" cannot become a get-out-of-jail card.
    """
    oc = _as_outcome(outcome)
    tier = normalise_tier(oc.tier)
    status = (oc.status or "").strip().lower()
    if status not in (OUTCOME_SUCCEEDED, OUTCOME_FAILED, OUTCOME_HONEST_FAIL):
        raise GradientError(f"unknown outcome status: {oc.status!r}")

    if status == OUTCOME_SUCCEEDED:
        return {"class": "success", "quarantined": False, "reason": "succeeded"}

    if status == OUTCOME_FAILED:
        return {"class": "failure", "quarantined": False, "reason": "ordinary_failure",
                "root_cause": oc.root_cause.strip(), "retrospective": oc.retrospective.strip()}

    # claimed honest FAIL
    if not _is_exploration(tier):
        return {"class": "failure", "quarantined": False,
                "reason": "no_exploration_budget_on_tier",
                "detail": f"{tier} carries no exploration budget; honest FAIL is not admissible"}
    problems = []
    if _text_len(oc.root_cause) < ROOT_CAUSE_MIN_CHARS:
        problems.append(
            f"root_cause shorter than {ROOT_CAUSE_MIN_CHARS} chars (got {_text_len(oc.root_cause)})"
        )
    if _text_len(oc.retrospective) < RETROSPECTIVE_MIN_CHARS:
        problems.append(
            f"retrospective shorter than {RETROSPECTIVE_MIN_CHARS} chars "
            f"(got {_text_len(oc.retrospective)})"
        )
    if problems:
        return {"class": "failure", "quarantined": False,
                "reason": "honest_fail_evidence_incomplete", "problems": problems,
                "root_cause": oc.root_cause.strip(),
                "retrospective": oc.retrospective.strip()}
    return {"class": OUTCOME_HONEST_FAIL, "quarantined": True, "reason": "admissible_honest_fail",
            "root_cause": oc.root_cause.strip(), "retrospective": oc.retrospective.strip(),
            "evidence_ref": (oc.evidence_ref or "").strip()}


def _as_outcome(value: Outcome | Mapping[str, Any]) -> Outcome:
    if isinstance(value, Outcome):
        return value
    if isinstance(value, Mapping):
        data = dict(value)
        data.setdefault("root_cause", "")
        data.setdefault("retrospective", "")
        data.setdefault("evidence_ref", "")
        data.setdefault("meta", {})
        try:
            return Outcome(**data)
        except TypeError as exc:  # pragma: no cover - defensive
            raise GradientError(f"invalid outcome mapping: {exc}") from exc
    raise GradientError(f"outcome must be Outcome or mapping, got {type(value).__name__}")


def failure_accounting(outcomes: Iterable[Outcome | Mapping[str, Any]]) -> dict[str, Any]:
    """Per-worker failure accounting that quarantines admissible honest FAILs.

    ``rate`` is the disciplined failure rate (canonical: quarantined honest FAILs
    leave both numerator and denominator).  ``naive_rate`` is what a flat counter
    would have produced — reported side by side so the difference is auditable.
    """
    per_worker: dict[str, dict[str, Any]] = {}
    for raw in outcomes:
        oc = _as_outcome(raw)
        verdict = classify_outcome(oc)
        w = per_worker.setdefault(
            oc.worker_id,
            {"worker_id": oc.worker_id, "attempts": 0, "successes": 0,
             "failures": 0, "quarantined_honest_fails": 0,
             "disqualified_honest_fails": 0, "negative_results": []},
        )
        w["attempts"] += 1
        if verdict["class"] == "success":
            w["successes"] += 1
        elif verdict["quarantined"]:
            w["quarantined_honest_fails"] += 1
            w["negative_results"].append({
                "mission_id": oc.mission_id, "tier": normalise_tier(oc.tier),
                "root_cause": verdict.get("root_cause", ""),
                "retrospective": verdict.get("retrospective", ""),
                "evidence_ref": verdict.get("evidence_ref", ""),
            })
        else:
            w["failures"] += 1
            if (oc.status or "").strip().lower() == OUTCOME_HONEST_FAIL:
                w["disqualified_honest_fails"] += 1

    workers = []
    for w in sorted(per_worker.values(), key=lambda x: x["worker_id"]):
        disciplined_attempts = w["attempts"] - w["quarantined_honest_fails"]
        w["disciplined_attempts"] = disciplined_attempts
        w["rate"] = (w["failures"] / disciplined_attempts) if disciplined_attempts else None
        w["naive_rate"] = (w["failures"] + w["quarantined_honest_fails"]) / w["attempts"] if w["attempts"] else None
        workers.append(w)

    total_attempts = sum(w["attempts"] for w in workers)
    total_q = sum(w["quarantined_honest_fails"] for w in workers)
    total_f = sum(w["failures"] for w in workers)
    disciplined_total = total_attempts - total_q
    return {
        "workers": workers,
        "aggregate": {
            "attempts": total_attempts,
            "failures": total_f,
            "quarantined_honest_fails": total_q,
            "disciplined_attempts": disciplined_total,
            "rate": (total_f / disciplined_total) if disciplined_total else None,
            "naive_rate": ((total_f + total_q) / total_attempts) if total_attempts else None,
        },
        "rule": ("honest FAIL with root cause + retrospective is quarantined from the "
                 "failure rate; incomplete evidence is booked as an ordinary failure"),
    }


def negative_result_entry(
    outcome: Outcome | Mapping[str, Any],
    *,
    reuse_tags: Sequence[str] = (),
    supersedes: str | None = None,
) -> dict[str, Any]:
    """Build an S-N (negative-result) track entry from an admissible honest FAIL.

    Refuses to mint an entry for anything that is not an admissible honest FAIL:
    the negative-result track is evidence of learning, not a dumping ground.
    """
    oc = _as_outcome(outcome)
    verdict = classify_outcome(oc)
    if not verdict["quarantined"]:
        raise GradientError(
            f"cannot record negative result: outcome is not an admissible honest FAIL "
            f"({verdict['reason']})"
        )
    return {
        "track": "S-N",
        "mission_id": oc.mission_id,
        "worker_id": oc.worker_id,
        "tier": normalise_tier(oc.tier),
        "root_cause": verdict["root_cause"],
        "retrospective": verdict["retrospective"],
        "evidence_ref": verdict.get("evidence_ref", ""),
        "reuse_tags": sorted({str(t).strip() for t in reuse_tags if str(t).strip()}),
        "supersedes": supersedes,
        "immutable": True,
    }


def guard_honest_fail(existing: Mapping[str, Any] | None, proposed_status: str) -> dict[str, Any]:
    """Append-only guard: a recorded honest FAIL may not be overwritten.

    Returns ``{"allowed": bool, "reason": str}``.  Rewriting a negative result as
    ``succeeded``/``failed`` is exactly the failure mode this mechanism exists to
    prevent, so it is refused unless the caller explicitly appends a *new* row
    (``append_only=True`` rows keep history).
    """
    if not existing:
        return {"allowed": True, "reason": "no_prior_record"}
    prior = str(existing.get("status") or existing.get("class") or "").strip().lower()
    if prior not in (OUTCOME_HONEST_FAIL, "quarantined"):
        return {"allowed": True, "reason": "prior_record_is_not_honest_fail"}
    proposed = (proposed_status or "").strip().lower()
    if proposed == OUTCOME_HONEST_FAIL:
        return {"allowed": True, "reason": "same_verdict_reasserted"}
    return {
        "allowed": False,
        "reason": "honest_fail_is_append_only",
        "detail": (f"mission {existing.get('mission_id')} already holds an honest FAIL; "
                   f"cannot overwrite it with {proposed!r}. Append a new row instead."),
    }


# ─────────────────────────────── dispatch ───────────────────────────────


def dispatch_decide(
    mission: Mapping[str, Any],
    candidates: Sequence[Mapping[str, Any]],
    *,
    quota_remaining: Mapping[str, int] | None = None,
) -> dict[str, Any]:
    """Credit-tier matched dispatch decision (pure, deterministic).

    ``mission``    — ``{"mission_id": ..., "tier": "S4"}``
    ``candidates`` — ``{"worker_id": ..., "credit_level": 4, "inflight": 0, "max_inflight": 1, "available": True}``
    ``quota_remaining`` — optional ``{tier: slots_left}`` from :func:`allocate_quota`

    Reason codes: ``ok``, ``invalid_tier``, ``tier_share_exhausted``,
    ``credit_gate_blocked``, ``no_available_worker``.
    """
    raw_tier = mission.get("tier") if isinstance(mission, Mapping) else None
    try:
        tier = normalise_tier(raw_tier)
    except GradientError as exc:
        return {"allowed": False, "reason": "invalid_tier", "detail": str(exc),
                "mission_id": mission.get("mission_id") if isinstance(mission, Mapping) else None}

    required = min_credit_level(tier)
    decision: dict[str, Any] = {
        "mission_id": mission.get("mission_id"),
        "tier": tier,
        "label": TIER_LABELS[tier],
        "exploration": _is_exploration(tier),
        "required_level": required,
        "chosen": None,
        "rejected": [],
        "allowed": False,
    }

    if quota_remaining is not None:
        left = quota_remaining.get(tier, 0)
        try:
            left = int(left)
        except (TypeError, ValueError):
            left = 0
        decision["quota_remaining"] = left
        if left <= 0:
            decision.update(reason="tier_share_exhausted",
                            detail=f"no seeding slot left for {tier} in the registered mix")
            return decision

    eligible: list[tuple[int, int, str]] = []
    for cand in candidates or ():
        wid = str(cand.get("worker_id"))
        try:
            level = normalise_credit_level(cand.get("credit_level"))
        except GradientError as exc:
            decision["rejected"].append({"worker_id": wid, "reason": "invalid_credit_level",
                                         "detail": str(exc)})
            continue
        if level < required:
            decision["rejected"].append({"worker_id": wid, "reason": "credit_below_required",
                                         "credit_level": level, "required_level": required})
            continue
        if cand.get("available") is False:
            decision["rejected"].append({"worker_id": wid, "reason": "worker_unavailable",
                                         "credit_level": level})
            continue
        inflight = cand.get("inflight", 0)
        cap = cand.get("max_inflight")
        try:
            inflight = int(inflight)
        except (TypeError, ValueError):
            inflight = 0
        if cap is not None:
            try:
                if inflight >= int(cap):
                    decision["rejected"].append({"worker_id": wid, "reason": "worker_at_capacity",
                                                 "credit_level": level, "inflight": inflight})
                    continue
            except (TypeError, ValueError):
                pass
        eligible.append((level, inflight, wid))

    if not eligible:
        blocking = [r for r in decision["rejected"] if r["reason"] == "credit_below_required"]
        decision.update(
            reason="credit_gate_blocked" if blocking else "no_available_worker",
            detail=(f"{tier} requires L{required}+; {len(blocking)} candidate(s) below the gate"
                    if blocking else "no candidate worker is available/capable"),
        )
        return decision

    # highest credit first (breakthrough work goes to the most trusted worker),
    # then lightest load, then stable worker id
    eligible.sort(key=lambda t: (-t[0], t[1], t[2]))
    level, inflight, wid = eligible[0]
    decision.update(
        allowed=True,
        reason="ok",
        chosen={"worker_id": wid, "credit_level": level, "inflight": inflight},
        detail=f"{tier} matched to {wid} (L{level} >= L{required})",
    )
    return decision


# ────────────────────────────── self / Q1 ───────────────────────────────


def self_declare(
    mission_id: str | int,
    *,
    mix: Mapping[str, Any] | None = None,
    base_budget: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """A mission declaring its own gradient.  This mechanism declares itself S3.

    The gate that evaluates the mechanism is the same gate the mechanism ships, so
    the declaration is emitted by the shared code path rather than hand-written.
    """
    payload = mission_gradient_payload("S3", base_budget, mix, self_declared=True)
    payload["mission_id"] = mission_id
    payload["declared_tier"] = "S3"
    payload["declared_label"] = TIER_LABELS["S3"]
    payload["rationale"] = ("Task-gradient seeding + exploration budget + credit-matched "
                            "dispatch is new platform capability (建设), not a breakthrough "
                            "experiment; it carries no exploration budget.")
    return payload


def evaluate_q1(criteria: Sequence[Mapping[str, Any]], *, min_criteria: int = 1) -> dict[str, Any]:
    """Q1 gate: every acceptance criterion must be machine-checkable and pass.

    ``criteria`` = ``[{"id": ..., "description": ..., "passed": bool, "evidence": ...}]``

    A verdict is ``pass`` only when there is at least ``min_criteria`` criterion and
    all of them passed.  ``unverified`` criteria (``passed is None``) never pass.
    """
    rows: list[dict[str, Any]] = []
    for c in criteria or ():
        cid = str(c.get("id") or "").strip()
        if not cid:
            raise GradientError("criterion requires a non-empty id")
        passed = c.get("passed")
        rows.append({
            "id": cid,
            "description": str(c.get("description") or ""),
            "passed": bool(passed) if passed is not None else None,
            "evidence": c.get("evidence"),
            "machine_checked": passed is not None,
        })
    total = len(rows)
    passed_n = sum(1 for r in rows if r["passed"] is True)
    if total < min_criteria:
        verdict, reason = "not_passed", f"only {total} criterion/criteria, need >= {min_criteria}"
    elif passed_n == total:
        verdict, reason = "pass", "all criteria passed"
    else:
        failed = [r["id"] for r in rows if r["passed"] is not True]
        verdict, reason = "not_passed", f"failed criteria: {', '.join(failed)}"
    return {
        "q1_verdict": verdict,
        "reason": reason,
        "criteria_total": total,
        "criteria_passed": passed_n,
        "criteria": rows,
        "honest_fail_override_allowed": False,
        "evaluated_by": "services.mission_gradient.evaluate_q1",
    }