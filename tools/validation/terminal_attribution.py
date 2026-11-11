#!/usr/bin/env python3
"""Terminal-mission attribution: separate *runtime idling* from *honest technical FAIL*.

Why this module exists
----------------------
A campaign node can stop in two very different ways that look identical in a status
column (``failed``):

* **the agent never got to work** — the worker crashed while preparing the worktree,
  the LLM endpoint died, the session heartbeat vanished, the budget was cut.  Zero
  tool calls happened.  Replaying is free of duplicated labour: nothing was produced.
* **the agent worked and reported an honest FAIL** — tool calls happened, artifacts
  and checks exist, and the technical approach missed its acceptance line.  Replaying
  the same approach burns budget and hides the negative result.

Mixing the two biases every published rate (yield, loss rate, per-model ranking) and
silently re-runs work that has already been honestly falsified.  This module is the
mechanical separator: every terminal record goes through an ordered, declared rule
list and lands in exactly one class, or in ``UNKNOWN`` when no evidence exists.
``UNKNOWN`` is never silently absorbed — it is counted, reported, and it is what
``attribution_coverage`` penalises.

Classes
-------
======================  ========================================================
``INFRA_NO_TOOLCALL``   ``tool_calls == 0`` with no policy refusal: pure idle spend.
``INFRA_LOST``          heartbeat / session / worker lost after the agent started
                        (``agent_lost``, ``wall_exhausted``, ``no_progress``,
                        operator stop, starved arbitration).
``MODEL_ERROR``         ``llm_error`` / parse failure / model-side terminal error.
``BUDGET_EXHAUSTED``    budget or forced-finish cut the attempt.
``POLICY_BLOCKED``      safety / whitelist / policy refusal (never auto-retried).
``PHYSICS_HONEST_FAIL`` tool calls happened *and* ``finish`` reported FAIL.
``DELIVERED_OK``        tool calls happened *and* ``finish`` reported success.

``DELIVERED_OK`` is the single label beyond the six-class failure taxonomy.  Delivered
terminals are not failures of any kind, so without it every successful mission would be
``UNKNOWN`` by construction and ``attribution_coverage`` would be meaningless.  The
six-class infra-vs-physics separation is untouched.

Rule order (first match wins, so the outcome is deterministic and auditable)
---------------------------------------------------------------------------
``R0_DELIVERED`` -> ``R1_POLICY`` -> ``R2_BUDGET`` -> ``R3_MODEL_ERROR`` -> ``R4_INFRA_LOST`` ->
``R5_INFRA_NO_TOOLCALL`` -> ``R6_PHYSICS_HONEST_FAIL`` -> ``R7_DELIVERED_OK`` ->
``R0_UNKNOWN``

Delivery outranks everything: a mission finished *and* accepted is not a failure, even
when its last forced-finish left a ``fail_stage`` marker behind.  Below that, an explicit
runtime cause (policy / budget / model / lost) outranks the generic zero-tool-call
inference: "no tool calls" is a symptom and must not mask its cause.
``INFRA_NO_TOOLCALL`` therefore means *zero tool calls and no more specific runtime
explanation*.

Tool-call resolution ladder (ordered; the source is recorded, never guessed)
---------------------------------------------------------------------------
1. ``result.tool_calls``                    — the worker's own counter;
2. ``session.decision_tool_calls``          — the ``finish`` decision payload;
3. ``finish_record`` -> ``1`` (lower bound) — a written finish record means the
   ``finish`` tool itself ran at least once;
4. ``worker_crash_prestart`` -> ``0``       — crash before the agent process exists;
5. ``no_session`` -> ``0``                  — no session and no finish record;
6. otherwise ``None`` -> the record is ``UNKNOWN``.

Requeue gate
------------
``RequeuePolicy`` decides, per attribution and per node history, whether a replay is
allowed, which attempt number it becomes, and how long to back off.  Replayable by
default: ``INFRA_NO_TOOLCALL`` / ``INFRA_LOST`` / ``MODEL_ERROR``.  Never auto-retried:
``PHYSICS_HONEST_FAIL`` (that is the negative-result track), ``POLICY_BLOCKED`` (a
policy answer does not change by retrying) and ``BUDGET_EXHAUSTED`` (needs a new
grant, not another attempt).  The replayable set is a *parameter*, not a classifier
constant, so a campaign can tighten it.

Every replay carries an attempt number, exponential (capped) backoff, a required
artifact snapshot of the previous attempt, and a ``requeue_class`` stamp that the
budget ledger uses to settle **one** budget unit per node regardless of attempts.

Platform neutrality
-------------------
This file carries no domain vocabulary.  Any lexicon used by ``scan_domain_words`` is
injected by the caller (configuration / artifact), as is all mission data — from a
committed snapshot or a read-only SQL dump.

Usage
-----
    # snapshot the platform DB (read-only) to a committed JSONL artifact
    python3 -m tools.validation.terminal_attribution --from-db \
        --snapshot-out artifacts/p226w3_n09/terminal_missions.jsonl

    # classify a snapshot, write the attribution artifact + KPIs
    python3 -m tools.validation.terminal_attribution \
        --from-snapshot artifacts/p226w3_n09/terminal_missions.jsonl \
        --out-json artifacts/p226w3_n09/attribution.json

    # replay gate: plan (no writes)
    python3 -m tools.validation.terminal_attribution \
        --from-snapshot ... --requeue-plan-json artifacts/p226w3_n09/requeue_plan.json
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

SCHEMA = "terminal-attribution/1.0"
LEDGER_SCHEMA = "requeue-budget-ledger/1.0"

CLASS_INFRA_NO_TOOLCALL = "INFRA_NO_TOOLCALL"
CLASS_INFRA_LOST = "INFRA_LOST"
CLASS_MODEL_ERROR = "MODEL_ERROR"
CLASS_BUDGET_EXHAUSTED = "BUDGET_EXHAUSTED"
CLASS_POLICY_BLOCKED = "POLICY_BLOCKED"
CLASS_PHYSICS_HONEST_FAIL = "PHYSICS_HONEST_FAIL"
CLASS_DELIVERED_OK = "DELIVERED_OK"
CLASS_UNKNOWN = "UNKNOWN"

#: The six-class failure taxonomy required of this mechanism, in reporting order.
FAILURE_CLASSES: Tuple[str, ...] = (
    CLASS_INFRA_NO_TOOLCALL,
    CLASS_INFRA_LOST,
    CLASS_MODEL_ERROR,
    CLASS_BUDGET_EXHAUSTED,
    CLASS_POLICY_BLOCKED,
    CLASS_PHYSICS_HONEST_FAIL,
)
#: Every label the classifier can emit (six failure classes + delivered + unknown).
ALL_CLASSES: Tuple[str, ...] = FAILURE_CLASSES + (CLASS_DELIVERED_OK, CLASS_UNKNOWN)

INFRA_CLASSES: Tuple[str, ...] = (CLASS_INFRA_NO_TOOLCALL, CLASS_INFRA_LOST)

#: Runtime signals (compared lower-cased against declared terminal fields).
POLICY_TOKENS: Tuple[str, ...] = (
    "policy_blocked", "policy_denied", "policy_refused", "safety_refused",
    "safety_blocked", "whitelist_denied", "white_list_denied", "forbidden_by_policy",
)
BUDGET_TOKENS: Tuple[str, ...] = ("budget_exhausted", "budget_exhaustion", "forced_finish")
MODEL_TOKENS: Tuple[str, ...] = (
    "llm_error", "llm_timeout", "llm_refusal", "model_error", "parse_error",
    "response_parse_error", "invalid_tool_call",
)
LOST_TOKENS: Tuple[str, ...] = (
    "agent_lost", "session_lost", "heartbeat_lost", "wall_exhausted",
    "no_progress", "timeout", "arbiter_starved", "session_expired",
)
#: Terminal statuses the platform treats as final.
TERMINAL_STATUSES: Tuple[str, ...] = ("succeeded", "failed", "revoked", "stopped")

#: Pre-registered mapping from a declared terminal token to its class.
TOKEN_CLASS: Dict[str, str] = {
    **{t: CLASS_POLICY_BLOCKED for t in POLICY_TOKENS},
    **{t: CLASS_BUDGET_EXHAUSTED for t in BUDGET_TOKENS},
    **{t: CLASS_MODEL_ERROR for t in MODEL_TOKENS},
    **{t: CLASS_INFRA_LOST for t in LOST_TOKENS},
}

#: Default requeue policy (a parameter, not a classifier constant).
DEFAULT_REPLAYABLE_CLASSES: Tuple[str, ...] = (
    CLASS_INFRA_NO_TOOLCALL, CLASS_INFRA_LOST, CLASS_MODEL_ERROR,
)
NEVER_REPLAYABLE_CLASSES: Tuple[str, ...] = (
    CLASS_PHYSICS_HONEST_FAIL, CLASS_POLICY_BLOCKED, CLASS_BUDGET_EXHAUSTED,
    CLASS_DELIVERED_OK, CLASS_UNKNOWN,
)


# ─────────────────────────────── helpers ────────────────────────────────
def canonical(obj: Any) -> str:
    """Canonical JSON text: stable key order, no incidental whitespace."""
    return json.dumps(obj, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def sha256_of(obj: Any) -> str:
    return hashlib.sha256(canonical(obj).encode("utf-8")).hexdigest()


def sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _as_dict(value: Any) -> Dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str) and value.strip().startswith("{"):
        try:
            out = json.loads(value)
            return out if isinstance(out, dict) else {}
        except json.JSONDecodeError:
            return {}
    return {}


def _nested(obj: Mapping[str, Any], *path: str) -> Any:
    cur: Any = obj
    for key in path:
        if not isinstance(cur, Mapping):
            return None
        cur = cur.get(key)
    return cur


def _tokens(record: Mapping[str, Any]) -> List[Tuple[str, str]]:
    """Declared terminal tokens as ``(field, token)`` pairs, order preserved."""
    result = _as_dict(record.get("result"))
    checks = _as_dict(_nested(result, "evidence", "checks"))
    session = _as_dict(record.get("session"))
    candidates: Tuple[Tuple[str, Any], ...] = (
        ("result.stop_condition", result.get("stop_condition")),
        ("session.stop_condition", session.get("stop_condition")),
        ("result.fail_stage", result.get("fail_stage") or checks.get("fail_stage")),
        ("result.stopped_reason", result.get("stopped_reason")),
        ("result.revoked_reason", result.get("revoked_reason")),
        ("evidence.gate", checks.get("gate")),
        ("session.decision_status", session.get("decision_status")),
    )
    out: List[Tuple[str, str]] = []
    for field_name, raw in candidates:
        if raw is None:
            continue
        text = str(raw).strip().lower()
        if not text:
            continue
        # A field may carry a composite token, e.g. "budget_exhausted+forced_finish".
        for token in text.replace("+", " ").replace(",", " ").split():
            token = token.strip()
            if token in TOKEN_CLASS:
                out.append((field_name, token))
    return out


# ────────────────────────── evidence extraction ─────────────────────────
@dataclass
class Evidence:
    """Everything the classifier may look at, plus how it was obtained."""

    mission_id: Any
    status: str
    tool_calls: Optional[int]
    tool_calls_source: str
    finish_called: bool
    finish_verdict: str          # "ok" | "fail" | "unknown"
    tokens: List[Tuple[str, str]] = field(default_factory=list)
    session_events: int = 0
    worker_crash: bool = False

    def as_dict(self) -> Dict[str, Any]:
        return {
            "mission_id": self.mission_id,
            "status": self.status,
            "tool_calls": self.tool_calls,
            "tool_calls_source": self.tool_calls_source,
            "finish_called": self.finish_called,
            "finish_verdict": self.finish_verdict,
            "tokens": [f"{f}:{t}" for f, t in self.tokens],
            "session_events": self.session_events,
            "worker_crash": self.worker_crash,
        }


def _finish_evidence(record: Mapping[str, Any], worker_crash: bool = False) -> Tuple[bool, str]:
    """Did this record reach ``finish``, and with which verdict?

    A finish record is recognised by an explicit agent-side marker only: a
    ``declared_summary`` (written by the ``finish`` tool itself), an ``accepted`` verdict,
    an ``evidence.checks`` block, a ``finish_status``, or a session decision carrying a
    summary.  A bare ``headline`` is **not** finish evidence: the worker synthesises a
    headline for every terminal, including crashes that never started an agent.

    Verdict is ``ok`` when acceptance is truthy or the record status is ``succeeded``;
    ``fail`` when acceptance is explicitly false, a finish status declares failure, or
    the headline says the run did not complete.
    """
    result = _as_dict(record.get("result"))
    session = _as_dict(record.get("session"))
    status = str(record.get("status") or result.get("status") or "").lower()
    if worker_crash:
        return False, "unknown"
    accepted = result.get("accepted")
    if isinstance(accepted, str):
        accepted = accepted.strip().lower() in ("true", "1", "yes")
    markers = (
        result.get("declared_summary"),
        _nested(result, "evidence", "checks"),
        result.get("finish_status"),
        session.get("decision_status"),
        session.get("decision_tool_calls"),
    )
    if accepted is None and not any(m not in (None, "", {}) for m in markers):
        return False, "unknown"
    if accepted is True:
        return True, "ok"
    if accepted is False:
        return True, "fail"
    if status == "succeeded":
        return True, "ok"
    headline = str(result.get("headline") or "").lower()
    finish_status = str(result.get("finish_status") or "").lower()
    if finish_status in ("ok", "pass", "passed", "succeeded"):
        return True, "ok"
    if finish_status in ("fail", "failed", "reject", "rejected"):
        return True, "fail"
    if result.get("declared_summary"):
        return True, "fail"
    if any(k in headline for k in ("fail", "not completed", "abort")):
        return True, "fail"
    return True, ("fail" if status in ("failed", "revoked", "stopped") else "unknown")


def extract(record: Mapping[str, Any]) -> Evidence:
    """Normalise one raw terminal record into :class:`Evidence` (deterministic).

    The tool-call ladder is ordered and records its own provenance; a lower bound is
    labelled as such so KPI readers can see it was not a measured counter.
    """
    result = _as_dict(record.get("result"))
    session = _as_dict(record.get("session"))
    checks = _as_dict(_nested(result, "evidence", "checks"))
    status = str(record.get("status") or "").lower()

    traceback_tail = str(result.get("traceback_tail") or "")
    worker_crash = (
        str(checks.get("gate") or "").lower() in ("worker-crash", "worker_crash")
        or "prepare_worktree" in traceback_tail
        or "worktree \u5931\u8d25" in traceback_tail
    )

    raw_tc = result.get("tool_calls")
    tool_calls: Optional[int] = None
    source = "absent"
    if isinstance(raw_tc, (int, float)) and not isinstance(raw_tc, bool):
        tool_calls, source = int(raw_tc), "result.tool_calls"
    elif isinstance(session.get("decision_tool_calls"), (int, float)) and not isinstance(
            session.get("decision_tool_calls"), bool):
        tool_calls, source = int(session["decision_tool_calls"]), "session.decision"
    elif worker_crash:
        tool_calls, source = 0, "worker_crash_prestart"
    elif isinstance(result.get("changed_files"), list) and result.get("changed_files"):
        tool_calls, source = 1, "changed_files_lower_bound"
    elif result.get("declared_summary") or result.get("accepted") is not None:
        tool_calls, source = 1, "finish_record_lower_bound"
    elif int(session.get("rounds") or 0) > 0:
        tool_calls, source = 1, "session_rounds_lower_bound"
    elif int(session.get("events") or 0) == 0 and not result:
        tool_calls, source = 0, "no_session"

    finish_called, verdict = _finish_evidence(record, worker_crash=worker_crash)

    return Evidence(
        mission_id=record.get("mission_id", record.get("id")),
        status=status,
        tool_calls=tool_calls,
        tool_calls_source=source,
        finish_called=finish_called,
        finish_verdict=verdict,
        tokens=_tokens(record),
        session_events=int(session.get("events") or 0),
        worker_crash=worker_crash,
    )


# ──────────────────────────── classification ────────────────────────────
#: Ordered rule table: (rule id, resulting class).  First matching rule wins.
RULE_ORDER_DEFAULT: Tuple[Tuple[str, str], ...] = ()  # placeholder for typing clarity
RULE_ORDER: Tuple[Tuple[str, str], ...] = (
    ("R1_POLICY", CLASS_POLICY_BLOCKED),
    ("R2_BUDGET", CLASS_BUDGET_EXHAUSTED),
    ("R3_MODEL_ERROR", CLASS_MODEL_ERROR),
    ("R4_INFRA_LOST", CLASS_INFRA_LOST),
    ("R5_INFRA_NO_TOOLCALL", CLASS_INFRA_NO_TOOLCALL),
    ("R6_PHYSICS_HONEST_FAIL", CLASS_PHYSICS_HONEST_FAIL),
    ("R7_DELIVERED_OK", CLASS_DELIVERED_OK),
    ("R8_STATUS_INFRA_STOP", CLASS_INFRA_LOST),        # operator/regulator stop
    ("R9_STATUS_ADMIN_BLOCK", CLASS_POLICY_BLOCKED),   # administrative revocation
    ("R10_STATUS_DELIVERED", CLASS_DELIVERED_OK),      # success without tool counter
)

RULE_ORDER_TO_CLASS: Dict[str, str] = {rule: klass for rule, klass in RULE_ORDER}

#: Status-only fallbacks: applied *only* after every evidence rule has failed.  They
#: close the coverage gap without inventing evidence — the platform status itself is the
#: evidence, and each carries its own rule id in the output.
STATUS_FALLBACK: Tuple[Tuple[str, str], ...] = (
    ("stopped", "R8_STATUS_INFRA_STOP"),
    ("revoked", "R9_STATUS_ADMIN_BLOCK"),
    ("succeeded", "R10_STATUS_DELIVERED"),
)


def attribute(record: Mapping[str, Any]) -> Dict[str, Any]:
    """Assign exactly one class to a terminal record; fully deterministic."""
    ev = extract(record)
    token_classes = [TOKEN_CLASS[t] for _f, t in ev.tokens if t in TOKEN_CLASS]
    rule = "R0_UNKNOWN"
    klass = CLASS_UNKNOWN
    # Delivery outranks every failure token: a finished-and-accepted mission can still
    # carry a stale ``fail_stage`` marker from its final forced finish, and reporting
    # that as a budget loss would systematically understate delivered work.
    if (ev.tool_calls is None or ev.tool_calls > 0):
        result_dict = _as_dict(record.get("result"))
        accepted = result_dict.get("accepted")
        if accepted is True or (str(record.get("status") or "").lower() == "succeeded"):
            return {
                "mission_id": ev.mission_id,
                "class": CLASS_DELIVERED_OK,
                "rule": "R0_DELIVERED",
                "is_failure": False,
                "infra": False,
                "tool_calls": ev.tool_calls,
                "tool_calls_source": ev.tool_calls_source,
                "finish_called": True,
                "finish_verdict": "ok",
                "signals": [f"{f}:{t}" for f, t in ev.tokens],
                "status": ev.status,
                "evidence": ev.as_dict(),
            }
    for rule_id, target in RULE_ORDER:
        if target in token_classes:
            rule, klass = rule_id, target
            break
        if target == CLASS_INFRA_NO_TOOLCALL and ev.tool_calls == 0:
            rule, klass = rule_id, target
            break
        if target == CLASS_PHYSICS_HONEST_FAIL and ev.tool_calls and ev.finish_verdict == "fail":
            rule, klass = rule_id, target
            break
        if target == CLASS_DELIVERED_OK and ev.tool_calls and ev.finish_verdict == "ok":
            rule, klass = rule_id, target
            break
    if klass == CLASS_UNKNOWN:
        for status_value, rule_id in STATUS_FALLBACK:
            if ev.status == status_value:
                rule, klass = rule_id, RULE_ORDER_TO_CLASS[rule_id]
                break
    return {
        "mission_id": ev.mission_id,
        "class": klass,
        "rule": rule,
        "is_failure": klass in FAILURE_CLASSES,
        "infra": klass.startswith("INFRA_"),
        "tool_calls": ev.tool_calls,
        "tool_calls_source": ev.tool_calls_source,
        "finish_called": ev.finish_called,
        "finish_verdict": ev.finish_verdict,
        "signals": [f"{f}:{t}" for f, t in ev.tokens],
        "status": ev.status,
        "evidence": ev.as_dict(),
    }


def attribute_all(records: Iterable[Mapping[str, Any]]) -> List[Dict[str, Any]]:
    return [attribute(r) for r in records]


# ──────────────────────────────── KPIs ──────────────────────────────────
def _rate(num: int, den: int) -> Optional[float]:
    return (num / den) if den else None


def attribution_coverage(results: Sequence[Mapping[str, Any]]) -> float:
    """Share of terminal records labelled something other than ``UNKNOWN``."""
    total = len(results)
    unknown = sum(1 for r in results if r.get("class") == CLASS_UNKNOWN)
    return 1.0 if total == 0 else (total - unknown) / total


def effective_attempt_rate(records: Sequence[Mapping[str, Any]],
                           results: Optional[Sequence[Mapping[str, Any]]] = None
                           ) -> Dict[str, Any]:
    """``#(tool_calls > 0 and finish) / #dispatched`` over the terminal population."""
    results = list(results) if results is not None else attribute_all(records)
    dispatched = len(results)
    effective = sum(
        1 for r in results
        if isinstance(r.get("tool_calls"), int) and r["tool_calls"] > 0 and r.get("finish_called")
    )
    reached_worker = sum(
        1 for r in results if isinstance(r.get("tool_calls"), int) and r["tool_calls"] > 0
    )
    return {
        "dispatched": dispatched,
        "with_tool_calls": reached_worker,
        "effective": effective,
        "effective_attempt_rate": _rate(effective, dispatched),
        "reached_worker_rate": _rate(reached_worker, dispatched),
    }


def requeue_yield(pairs: Sequence[Mapping[str, Any]]) -> Dict[str, Any]:
    """Share of replays that reached a live worker (``tool_calls > 0``).

    Each item is a replay attempt record; consumed through :func:`extract`, so the same
    evidence ladder applies.  ``node_key`` groups attempts per node.
    """
    total = len(pairs)
    live = 0
    per_node: Dict[str, List[bool]] = {}
    for item in pairs:
        ev = extract(item)
        ok = bool(ev.tool_calls and ev.tool_calls > 0)
        live += int(ok)
        node = str(item.get("node_key") or item.get("task_key") or "?")
        per_node.setdefault(node, []).append(ok)
    return {
        "replays": total,
        "reached_worker": live,
        "requeue_yield": _rate(live, total),
        "per_node": {k: {"attempts": len(v), "reached_worker": sum(v)}
                     for k, v in sorted(per_node.items())},
    }


def replay_pairs(records: Sequence[Mapping[str, Any]],
                  origin_classes: Optional[Sequence[str]] = None) -> List[Dict[str, Any]]:
    """Pair every replay attempt with the first attempt of the same node.

    Node identity comes from the task key (``<node>-a<attempt>``), which is the only
    place the platform records attempt lineage — ``parent_mission_id`` was never
    populated (0 rows at the time of writing), so lineage is reconstructed here rather
    than assumed.  Only nodes whose first attempt was a runtime loss are paired: those
    are the ones an automatic replay would have been allowed to touch.
    """
    origin_classes = tuple(origin_classes or (INFRA_CLASSES + (CLASS_MODEL_ERROR,)))
    groups: Dict[str, List[Tuple[int, Mapping[str, Any]]]] = {}
    for rec in records:
        head, sep, tail = str(rec.get("task_key") or "").rpartition("-a")
        if sep and tail.isdigit():
            groups.setdefault(head, []).append((int(tail), rec))
    pairs: List[Dict[str, Any]] = []
    for node in sorted(groups):
        items = sorted(groups[node], key=lambda x: x[0])
        first = items[0][1]
        if attribute(first)["class"] not in origin_classes:
            continue
        for attempt, rec in items[1:]:
            pairs.append({"node_key": node, "origin": first, "target": rec,
                          "attempt": attempt})
    return pairs


def summarize(records: Sequence[Mapping[str, Any]],
              results: Optional[Sequence[Mapping[str, Any]]] = None) -> Dict[str, Any]:
    results = list(results) if results is not None else attribute_all(records)
    counts = {c: 0 for c in ALL_CLASSES}
    for r in results:
        counts[r["class"]] = counts.get(r["class"], 0) + 1
    failure_total = sum(counts[c] for c in FAILURE_CLASSES)
    out = {
        "schema": SCHEMA,
        "terminals": len(results),
        "class_counts": counts,
        "failure_terminals": failure_total,
        "attribution_coverage": attribution_coverage(results),
        "kpi": {"effective_attempt_rate": effective_attempt_rate(records, results)},
        "infra_share_of_failures": _rate(
            counts[CLASS_INFRA_NO_TOOLCALL] + counts[CLASS_INFRA_LOST], failure_total),
        "physics_share_of_failures": _rate(counts[CLASS_PHYSICS_HONEST_FAIL], failure_total),
        "unknown_share_of_failures": _rate(counts[CLASS_UNKNOWN], failure_total),
    }
    out["digest"] = sha256_of(results)
    return out


# ───────────────────────────── requeue gate ─────────────────────────────
@dataclass(frozen=True)
class RequeuePolicy:
    """Configuration of the attribution-driven replay gate."""

    max_attempts: int = 2                 # total attempts per node, first included
    base_backoff_seconds: int = 300
    backoff_factor: float = 2.0
    max_backoff_seconds: int = 3600
    plan_fraction_cap: float = 0.2        # max share of a plan's nodes replayed per round
    max_queue_depth: int = 200            # do not add replays to a saturated queue
    replayable_classes: Tuple[str, ...] = DEFAULT_REPLAYABLE_CLASSES
    never_replayable_classes: Tuple[str, ...] = NEVER_REPLAYABLE_CLASSES
    snapshot_required: bool = True

    def backoff_for(self, attempt_no: int) -> int:
        """Backoff before attempt ``n``: base for the first replay, then geometric."""
        if int(attempt_no) <= 1:
            return 0
        steps = int(attempt_no) - 2
        return int(min(self.max_backoff_seconds,
                       self.base_backoff_seconds * math.pow(self.backoff_factor, steps)))

    def as_dict(self) -> Dict[str, Any]:
        return {
            "max_attempts": self.max_attempts,
            "base_backoff_seconds": self.base_backoff_seconds,
            "backoff_factor": self.backoff_factor,
            "max_backoff_seconds": self.max_backoff_seconds,
            "plan_fraction_cap": self.plan_fraction_cap,
            "max_queue_depth": self.max_queue_depth,
            "replayable_classes": list(self.replayable_classes),
            "never_replayable_classes": list(self.never_replayable_classes),
            "snapshot_required": self.snapshot_required,
        }


@dataclass
class RequeueDecision:
    mission_id: Any
    node_key: str
    klass: str
    eligible: bool
    reason: str
    attempt_no: int
    backoff_seconds: int
    snapshot_required: bool
    replayable: bool

    def as_dict(self) -> Dict[str, Any]:
        return {
            "mission_id": self.mission_id,
            "node_key": self.node_key,
            "class": self.klass,
            "eligible": self.eligible,
            "reason": self.reason,
            "attempt_no": self.attempt_no,
            "backoff_seconds": self.backoff_seconds,
            "snapshot_required": self.snapshot_required,
            "replayable": self.replayable,
        }


def node_key_of(record: Mapping[str, Any]) -> str:
    """Node identity = task key without its ``-aN`` attempt suffix, else a goal hash."""
    task_key = str(record.get("task_key") or "").strip()
    if task_key:
        head, sep, tail = task_key.rpartition("-a")
        if sep and tail.isdigit():
            return head
        return task_key
    goal = str(record.get("goal") or "")
    return "goal:" + hashlib.sha256(goal.encode("utf-8")).hexdigest()[:16]


REASON_TEXT: Dict[str, str] = {
    CLASS_PHYSICS_HONEST_FAIL: "physics_negative_result_track",
    CLASS_POLICY_BLOCKED: "policy_answer_is_stable",
    CLASS_BUDGET_EXHAUSTED: "needs_new_grant_not_new_attempt",
    CLASS_DELIVERED_OK: "already_delivered",
    CLASS_UNKNOWN: "no_evidence_to_decide",
    CLASS_INFRA_NO_TOOLCALL: "infra_class_replayable",
    CLASS_INFRA_LOST: "infra_class_replayable",
    CLASS_MODEL_ERROR: "infra_class_replayable",
}


def decide_requeue(record: Mapping[str, Any], attribution: Mapping[str, Any],
                   prior_attempts: int = 0, policy: Optional[RequeuePolicy] = None,
                   plan_replays_used: int = 0, plan_nodes: int = 0,
                   queue_depth: Optional[int] = None) -> RequeueDecision:
    """One decision per terminal record; reasons come from a closed set."""
    policy = policy or RequeuePolicy()
    klass = str(attribution.get("class"))
    attempts = int(prior_attempts) + 1
    node = node_key_of(record)
    replayable = klass in policy.replayable_classes
    eligible, reason = False, REASON_TEXT.get(klass, "not_replayable_class")
    if replayable:
        cap = max(1, int(round(policy.plan_fraction_cap * plan_nodes))) if plan_nodes else None
        if attempts >= policy.max_attempts:
            reason = "attempt_cap_reached"
        elif queue_depth is not None and queue_depth > policy.max_queue_depth:
            # Capacity guard: a replay added to a saturated queue does not reach a worker,
            # it only lengthens the queue.  Measured on this platform: 38.8% of replays
            # queued behind a long queue ever reached a live worker.
            reason = "capacity_deferred"
        elif cap is not None and plan_replays_used >= cap:
            reason = "plan_fraction_cap_reached"
        else:
            eligible, reason = True, "infra_class_replayable"
    return RequeueDecision(
        mission_id=attribution.get("mission_id", record.get("mission_id")),
        node_key=node,
        klass=klass,
        eligible=eligible,
        reason=reason,
        attempt_no=attempts + 1 if eligible else attempts,
        backoff_seconds=policy.backoff_for(attempts + 1) if eligible else 0,
        snapshot_required=policy.snapshot_required,
        replayable=replayable,
    )


def plan_replays(records: Sequence[Mapping[str, Any]],
                 results: Optional[Sequence[Mapping[str, Any]]] = None,
                 policy: Optional[RequeuePolicy] = None,
                 prior_attempts: Optional[Mapping[str, int]] = None,
                 queue_depth: Optional[int] = None) -> Dict[str, Any]:
    """Replay plan: one entry per node, plan cap enforced in deterministic order."""
    policy = policy or RequeuePolicy()
    results = list(results) if results is not None else attribute_all(records)
    by_id = {str(r.get("mission_id")): r for r in records}
    priors = dict(prior_attempts or {})
    nodes: Dict[str, List[Tuple[Mapping[str, Any], Mapping[str, Any]]]] = {}
    for res in results:
        rec = by_id.get(str(res.get("mission_id")), {})
        nodes.setdefault(node_key_of(rec), []).append((rec, res))

    entries: List[Dict[str, Any]] = []
    used = 0
    for node in sorted(nodes):
        # deterministic candidate choice: most tool-call evidence first, then mission id
        rec, res = sorted(nodes[node],
                          key=lambda p: (-(p[1].get("tool_calls") or 0),
                                         str(p[1].get("mission_id"))))[0]
        decision = decide_requeue(rec, res, prior_attempts=priors.get(node, 0), policy=policy,
                                  plan_replays_used=used, plan_nodes=len(nodes),
                                  queue_depth=queue_depth)
        used += int(decision.eligible)
        entries.append(decision.as_dict())
    return {
        "schema": SCHEMA + "/requeue-plan",
        "policy": policy.as_dict(),
        "plan_nodes": len(nodes),
        "plan_replays_used": used,
        "entries": entries,
        "plan_digest": sha256_of(entries),
    }


# ───────────────────── artifact snapshot per attempt ────────────────────
def _safe(name: str) -> str:
    out = "".join(ch if ch.isalnum() or ch in "-_." else "-" for ch in str(name))
    return out[:120] or "node"


def snapshot_artifacts(sources: Sequence[str], dest_root: str, node_key: str,
                       attempt_no: int) -> Dict[str, Any]:
    """Preserve the previous attempt's artifacts before replaying into the same session.

    Files/dirs are copied into ``<dest_root>/<node>/attempt-<n>/`` and every entry is
    recorded with its sha256 and byte size, so a replay can never cite an artifact that
    the replay itself overwrote.
    """
    dest = os.path.join(dest_root, _safe(node_key), f"attempt-{int(attempt_no):02d}")
    os.makedirs(dest, exist_ok=True)
    entries: List[Dict[str, Any]] = []
    for src in sources:
        if not os.path.exists(src):
            entries.append({"source": src, "status": "missing"})
            continue
        if os.path.isdir(src):
            base = os.path.basename(os.path.normpath(src)) or "dir"
            target = os.path.join(dest, base)
            if os.path.exists(target):
                shutil.rmtree(target)
            shutil.copytree(src, target)
            paths = sorted(os.path.join(dp, f) for dp, _dn, fn in os.walk(target) for f in fn)
        else:
            target = os.path.join(dest, os.path.basename(src))
            shutil.copy2(src, target)
            paths = [target]
        for path in paths:
            entries.append({
                "source": src,
                "snapshot": os.path.relpath(path, dest_root),
                "sha256": sha256_file(path),
                "bytes": os.path.getsize(path),
            })
    manifest = {
        "schema": SCHEMA + "/snapshot",
        "node_key": node_key,
        "attempt": int(attempt_no),
        "dest": dest,
        "entries": entries,
        "digest": sha256_of(entries),
    }
    with open(os.path.join(dest, "manifest.json"), "w", encoding="utf-8") as fh:
        json.dump(manifest, fh, ensure_ascii=False, indent=2, sort_keys=True)
    return manifest


# ───────────────────────── budget ledger (1/node) ───────────────────────
def settle_attempts(attempts: Sequence[Mapping[str, Any]],
                    policy: Optional[RequeuePolicy] = None) -> Dict[str, Any]:
    """Settle budget once per node; replays are annotated, never re-billed.

    ``attempts`` items: ``{"node_key", "attempt", "class"}``.  The first attempt of a
    node is the billable one; every later attempt is a requeue and carries
    ``requeue_class`` plus ``billable: false``.  Rule: one node = one budget unit, no
    matter how many replays the gate allowed.
    """
    policy = policy or RequeuePolicy()
    rows: List[Dict[str, Any]] = []
    per_node: Dict[str, List[Mapping[str, Any]]] = {}
    for item in attempts:
        per_node.setdefault(str(item["node_key"]), []).append(item)
    for node in sorted(per_node):
        items = sorted(per_node[node], key=lambda x: int(x.get("attempt", 1)))
        for idx, item in enumerate(items):
            klass = str(item.get("class") or CLASS_UNKNOWN)
            is_replay = idx > 0
            rows.append({
                "node_key": node,
                "attempt": int(item.get("attempt", idx + 1)),
                "class": klass,
                "requeue_class": klass if is_replay else None,
                "billable": not is_replay,
                "budget_used": 0.0 if is_replay else 1.0,
            })
    total = sum(r["budget_used"] for r in rows)
    return {
        "schema": LEDGER_SCHEMA,
        "policy": policy.as_dict(),
        "rows": rows,
        "nodes": len(per_node),
        "replays": sum(1 for r in rows if not r["billable"]),
        "budget_used_total": total,
        "budget_used_per_node": 1.0,
        "single_settlement_holds": total == len(per_node),
    }


# ─────────────────────── domain-vocabulary scan ─────────────────────────
def scan_domain_words(paths: Sequence[str], lexicon: Sequence[str]) -> Dict[str, Any]:
    """Count injected-vocabulary hits per file.  The lexicon is *always* injected.

    Returns ``hits`` (total occurrences) and per-file detail, so a caller can assert
    that a mechanics-only module carries no domain vocabulary.
    """
    words = [w.strip().lower() for w in lexicon if str(w).strip()]
    files: List[Dict[str, Any]] = []
    total = 0
    for path in paths:
        counts: Dict[str, int] = {}
        text = ""
        if os.path.exists(path):
            try:
                with open(path, encoding="utf-8", errors="ignore") as fh:
                    text = fh.read().lower()
            except OSError:
                text = ""
        for word in words:
            if re.fullmatch(r"[a-z0-9_]+", word):
                # Latin acronyms must match as whole words, otherwise a three-letter
                # acronym fires inside ordinary words and the guard becomes noise.
                n = len(re.findall(r"\b" + re.escape(word) + r"\b", text))
            else:
                n = text.count(word)
            if n:
                counts[word] = n
                total += n
        files.append({"path": path, "hits": sum(counts.values()), "words": counts})
    return {"lexicon_size": len(words), "files_scanned": len(files), "hits": total, "files": files}


# ──────────────────────── platform DB (read-only) ───────────────────────
DUMP_SQL = """
SELECT json_build_object(
  'mission_id', m.id, 'status', m.status, 'repo', m.repo, 'model', m.model,
  'task_key', m.task_key, 'parent_mission_id', m.parent_mission_id, 'origin', m.origin,
  'created_at', m.created_at, 'finished_at', m.finished_at, 'left(goal,160)', left(m.goal,160),
  'goal', left(m.goal, 400), 'result', m.result,
  'session', json_build_object(
     'events', s.nev, 'rounds', s.nround,
     'decision_tool_calls', d.tc, 'decision_status', d.st,
     'stop_condition', d.sc)
)::text
FROM tensor_dev_missions m
LEFT JOIN LATERAL (
  SELECT count(*) nev,
         count(*) FILTER (WHERE e.kind = 'note' AND e.payload ? 'round') nround
    FROM tensor_agent_session_events e WHERE e.session_id = m.session_id) s ON true
LEFT JOIN LATERAL (
  SELECT max(NULLIF(e.payload->'summary'->>'tool_calls','')::int) tc,
         max(e.payload->>'status') st,
         max(e.payload->>'stop_condition') sc
    FROM tensor_agent_session_events e
   WHERE e.session_id = m.session_id AND e.kind = 'decision') d ON true
WHERE m.status IN ('succeeded','failed','revoked','stopped')
ORDER BY m.id
"""

AGENT_ENV = "/root/tp-agent/agent.env"


def _pg_password(path: str = AGENT_ENV) -> str:
    """Read the DB password from the fleet env file (never committed, never echoed)."""
    if os.environ.get("TP_PG_PASSWORD"):
        return os.environ["TP_PG_PASSWORD"]
    try:
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line.startswith("export "):
                    line = line[7:]
                if line.startswith("TP_PG_PASSWORD="):
                    return line.split("=", 1)[1].strip().strip('"').strip("'")
    except OSError:
        pass
    return ""


def fetch_from_db(sql: str = DUMP_SQL, password: Optional[str] = None) -> List[Dict[str, Any]]:
    """Read terminal missions out of the platform DB (read-only SELECT)."""
    env = {
        "PGPASSWORD": password if password is not None else _pg_password(),
        "PATH": "/usr/bin:/bin:/usr/local/bin",
    }
    proc = subprocess.run(
        ["psql", "-h", os.environ.get("TP_PG_HOST", "127.0.0.1"),
         "-U", os.environ.get("TP_PG_USER", "foundation"),
         "-d", os.environ.get("TP_PG_DB", "tensorplatform"), "-tAc", sql],
        capture_output=True, text=True, env=env, timeout=300,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"psql failed: {proc.stderr[:400]}")
    return [json.loads(line) for line in proc.stdout.splitlines() if line.strip()]


def write_snapshot(records: Iterable[Mapping[str, Any]], path: str) -> Dict[str, Any]:
    """Write the JSONL snapshot; a ``.gz`` suffix compresses it (12k records ~ 63 MB raw)."""
    import gzip

    rows = list(records)
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    opener = gzip.open if path.endswith(".gz") else open
    with opener(path, "wt", encoding="utf-8") as fh:
        for row in rows:
            fh.write(canonical(row) + "\n")
    return {"path": path, "records": len(rows), "sha256": sha256_file(path)}


def load_snapshot(path: str) -> List[Dict[str, Any]]:
    import gzip

    out: List[Dict[str, Any]] = []
    opener = gzip.open if path.endswith(".gz") else open
    with opener(path, "rt", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                out.append(json.loads(line))
    return out


def idempotency_check(records: Sequence[Mapping[str, Any]]) -> Dict[str, Any]:
    """Two independent classification passes must agree bit-for-bit."""
    first = attribute_all(records)
    second = attribute_all(records)
    h1, h2 = sha256_of(first), sha256_of(second)
    reversed_pass = {str(r["mission_id"]): r["class"]
                     for r in attribute_all(list(reversed(list(records))))}
    order_stable = all(reversed_pass.get(str(r["mission_id"])) == r["class"] for r in first)
    return {"sha256_pass1": h1, "sha256_pass2": h2, "identical": h1 == h2,
            "order_invariant": order_stable, "records": len(first)}


def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="terminal-mission attribution + replay gate")
    ap.add_argument("--from-db", action="store_true", help="read terminal missions from the platform DB")
    ap.add_argument("--from-snapshot", help="read a JSONL snapshot instead of the DB")
    ap.add_argument("--snapshot-out", help="write the DB dump to this JSONL path")
    ap.add_argument("--out-json", help="write attribution summary + per-mission classes here")
    ap.add_argument("--requeue-plan-json", help="write the replay plan here")
    ap.add_argument("--ledger-json", help="write the budget ledger here")
    ap.add_argument("--domain-lexicon", help="JSON list of domain words for the neutrality scan")
    ap.add_argument("--scan", nargs="*", default=[], help="files to scan for domain words")
    args = ap.parse_args(argv)

    if args.from_snapshot:
        records = load_snapshot(args.from_snapshot)
    elif args.from_db:
        records = fetch_from_db()
    else:
        ap.error("need --from-db or --from-snapshot")
        return 2

    if args.snapshot_out:
        print(json.dumps(write_snapshot(records, args.snapshot_out), ensure_ascii=False))

    results = attribute_all(records)
    summary = summarize(records, results)
    lean = [{k: r[k] for k in ("mission_id", "class", "rule", "tool_calls",
                               "tool_calls_source", "finish_called", "signals", "status")}
            for r in results]
    payload: Dict[str, Any] = {"summary": summary, "idempotency": idempotency_check(records),
                               "missions": lean}
    if args.requeue_plan_json:
        plan = plan_replays(records, results)
        os.makedirs(os.path.dirname(os.path.abspath(args.requeue_plan_json)), exist_ok=True)
        with open(args.requeue_plan_json, "w", encoding="utf-8") as fh:
            json.dump(plan, fh, ensure_ascii=False, indent=2, sort_keys=True)
        payload["requeue_plan"] = {k: plan[k] for k in
                                   ("schema", "plan_nodes", "plan_replays_used", "plan_digest")}
    if args.ledger_json:
        attempts = []
        for rec, res in zip(records, results):
            head, sep, tail = str(rec.get("task_key") or "").rpartition("-a")
            attempt = int(tail) if sep and tail.isdigit() else 1
            attempts.append({"node_key": node_key_of(rec), "attempt": attempt,
                             "class": res["class"]})
        ledger = settle_attempts(attempts)
        os.makedirs(os.path.dirname(os.path.abspath(args.ledger_json)), exist_ok=True)
        with open(args.ledger_json, "w", encoding="utf-8") as fh:
            json.dump(ledger, fh, ensure_ascii=False, indent=2, sort_keys=True)
        payload["ledger"] = {k: ledger[k] for k in
                             ("nodes", "replays", "budget_used_total", "single_settlement_holds")}
    if args.domain_lexicon and args.scan:
        with open(args.domain_lexicon, encoding="utf-8") as fh:
            lexicon = json.load(fh)
        payload["domain_scan"] = scan_domain_words(args.scan, lexicon)
    if args.out_json:
        os.makedirs(os.path.dirname(os.path.abspath(args.out_json)), exist_ok=True)
        with open(args.out_json, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False, indent=2, sort_keys=True)
    print(json.dumps({k: payload[k] for k in
                      ("summary", "idempotency", "requeue_plan", "ledger", "domain_scan")
                      if k in payload}, ensure_ascii=False, indent=2)[:4000])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())