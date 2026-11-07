#!/usr/bin/env python3
"""Ops-side REOPENED guard (SPEC §4.2) — formal replacement for ad-hoc revival.

Background
----------
``tp_pusher`` used to mark a mission with a private flag (``main_red_hold``) when
a PR's CI was red while ``main`` itself was red: the failure is inherited, so the
PR must not be auto-closed.  That flag lived only in the pusher's own JSON state
file — the mission kept its terminal ``succeeded`` status, no audit row was
written, and nothing in the platform vocabulary said "this mission is back on the
bench".  Same story for every other out-of-band revival: invisible to the state
machine.

This module routes such guards through the formal state instead:

    terminal status --(governance reopen, citing the original acceptance
                       record + a reason)--> reopened --(release)--> queued

It is a *thin derived reader* of the two contract files, exactly like the backend
implementation (``backend/api/domain/mission_state_machine.py``): the rules live
in ``backend/contracts/mission_state_machine.v1.json``, and
``backend/tests/test_mission_reopened_state.py`` asserts that both layers return
identical decisions for the whole status × actor × payload matrix.  Nothing about
any scientific or engineering field is encoded here — the vocabulary is purely
governance-level.

SQL side effects (one atomic statement, so a crash cannot half-apply):
  * ``UPDATE tensor_dev_missions SET status='reopened', reopened_from=…,
    reopen_ref=…, reopen_reason=…, reopened_at=now()`` guarded by
    ``status IN (<contract reopen.from>)``;
  * ``INSERT INTO mission_lifecycle_log (action='reopen', detail=…)`` — the
    append-only audit row (migrations/103_mission_reopened.sql widens the action
    CHECK and adds a DB constraint that a ``reopened`` row must carry the
    from-state, the cited reference and the reason).

Callers stay honest: :func:`apply_reopen` re-reads the mission's *current* status
from the DB before planning, so a mission that already moved on is simply not
reopened (the guard never overwrites a status it does not understand).
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Callable

_REPO = Path(__file__).resolve().parents[2]
_CONTRACTS = _REPO / "backend" / "contracts"


def _load(name: str, contract: str) -> dict[str, Any]:
    data = json.loads((_CONTRACTS / name).read_text(encoding="utf-8"))
    if data.get("contract") != contract:
        raise ValueError(f"{name}: contract field mismatch ({data.get('contract')!r})")
    return data


_VOCAB = _load("mission_status_vocab.v1.json", "mission_status_vocab")
_SM = _load("mission_state_machine.v1.json", "mission_state_machine")

STATUSES: tuple[str, ...] = tuple(_VOCAB["statuses"])
TERMINAL: tuple[str, ...] = tuple(_VOCAB["terminal"])
TRANSITIONS: dict[str, tuple[str, ...]] = {
    src: tuple(edge["to"]) for src, edge in _SM["transitions"].items()
}
GOVERNANCE_ROLES: tuple[str, ...] = tuple(_SM["governance_roles"])
REOPEN_FROM: tuple[str, ...] = tuple(_SM["reopen"]["from"])
REOPEN_TARGETS: tuple[str, ...] = tuple(_SM["reopen"]["targets"])
REASON_MIN_CHARS: int = int(_SM["reopen"]["min_reason_chars"])
ACCEPTANCE_REF_RE = re.compile(_SM["reopen"]["acceptance_ref_pattern"])
REOPENED: str = REOPEN_TARGETS[0]

# Same decision codes as the backend implementation (locked by the drift test).
OK = "ok"
NOT_REOPENABLE = "not_reopenable_from"
UNKNOWN_STATUS = "unknown_status"
NOT_GOVERNANCE = "not_governance_actor"
PRODUCER = "producer_cannot_reopen"
REF_MISSING = "acceptance_ref_missing"
REF_MALFORMED = "acceptance_ref_malformed"
REASON_TOO_SHORT = "reason_too_short"
TARGET_NOT_ALLOWED = "target_not_allowed"

_SQL_LITERAL_UNSAFE = re.compile(r"[\x00]")


def _q(value: Any) -> str:
    """Quote a Python value as a SQL string literal (standard_conforming_strings)."""
    text = "" if value is None else str(value)
    if _SQL_LITERAL_UNSAFE.search(text):
        raise ValueError("NUL byte in SQL literal")
    return "'" + text.replace("'", "''") + "'"


def can_transition(src: str, dst: str) -> bool:
    return dst in TRANSITIONS.get(src, ())


def plan_reopen(
    *,
    from_status: str,
    actor_role: str | None,
    acceptance_ref: str | None = None,
    reason: str | None = None,
    actor_identity: str | None = None,
    producer_identity: str | None = None,
    target: str | None = None,
) -> dict[str, Any]:
    """Decide whether a reopen is admissible; never raises. Contract-derived."""
    decision: dict[str, Any] = {
        "ok": False,
        "code": OK,
        "from_status": from_status,
        "to_status": None,
        "target": target,
        "acceptance_ref": acceptance_ref,
        "actor_role": actor_role,
        "reason_chars": len(reason or ""),
    }
    if from_status not in STATUSES:
        decision["code"] = UNKNOWN_STATUS
        return decision
    if from_status not in REOPEN_FROM:
        decision["code"] = NOT_REOPENABLE
        return decision
    if actor_role not in GOVERNANCE_ROLES:
        decision["code"] = NOT_GOVERNANCE
        return decision
    if actor_identity and producer_identity and actor_identity == producer_identity:
        decision["code"] = PRODUCER
        return decision
    if not acceptance_ref:
        decision["code"] = REF_MISSING
        return decision
    if not ACCEPTANCE_REF_RE.match(acceptance_ref):
        decision["code"] = REF_MALFORMED
        return decision
    if decision["reason_chars"] < REASON_MIN_CHARS:
        decision["code"] = REASON_TOO_SHORT
        return decision
    tgt = target or REOPENED
    decision["target"] = tgt
    if tgt not in REOPEN_TARGETS:
        decision["code"] = TARGET_NOT_ALLOWED
        return decision
    if not can_transition(from_status, tgt):
        decision["code"] = TARGET_NOT_ALLOWED
        return decision
    decision["ok"] = True
    decision["to_status"] = tgt
    return decision


def reopen_sql(mission_id: int, plan: dict[str, Any], *, actor: str) -> str:
    """Single atomic statement applying a *passed* plan (see module docstring)."""
    if not plan.get("ok"):
        raise ValueError(f"plan not admissible: {plan.get('code')}")
    from_set = ",".join(_q(s) for s in REOPEN_FROM)
    detail = json.dumps(
        {
            "from_status": plan["from_status"],
            "to_status": plan["to_status"],
            "actor": actor,
            "actor_role": plan.get("actor_role"),
            "acceptance_ref": plan["acceptance_ref"],
            "reason_chars": plan.get("reason_chars"),
        },
        ensure_ascii=False,
    )
    return (
        "WITH upd AS (\n"
        "  UPDATE tensor_dev_missions\n"
        "     SET status = {tgt}, reopened_from = status, reopen_ref = {ref},\n"
        "         reopen_reason = {reason}, reopened_at = now()\n"
        "   WHERE id = {mid} AND status IN ({from_set})\n"
        "  RETURNING id, reopened_from, reopened_at\n"
        ")\n"
        "INSERT INTO mission_lifecycle_log (mission_id, action, actor_user_id, detail)\n"
        "SELECT id, 'reopen', NULL, CAST({detail} AS jsonb) FROM upd\n"
        "RETURNING mission_id"
    ).format(
        tgt=_q(plan["to_status"]),
        ref=_q(plan["acceptance_ref"]),
        reason=_q(plan.get("reason", "")),
        mid=int(mission_id),
        from_set=from_set,
        detail=_q(detail),
    )


def apply_reopen(
    q: Callable[[str], str],
    mission_id: int,
    *,
    actor: str,
    actor_role: str = "GOVERNANCE",
    acceptance_ref: str | None = None,
    reason: str | None = None,
    producer_identity: str | None = None,
) -> dict[str, Any]:
    """Read the live status, plan, then apply. Never raises on refusal.

    ``q`` is the site's psql runner (``q(sql) -> str``), so this stays usable
    from the daemons without a DB driver. Returns the plan dict plus
    ``applied`` (True iff a row was actually reopened by this call).
    """
    current = (q(f"SELECT status FROM tensor_dev_missions WHERE id={int(mission_id)}") or "").strip()
    plan = plan_reopen(
        from_status=current,
        actor_role=actor_role,
        acceptance_ref=acceptance_ref,
        reason=reason,
        actor_identity=actor,
        producer_identity=producer_identity,
    )
    plan["applied"] = False
    plan["current_status"] = current
    if not plan["ok"]:
        return plan
    try:
        out = (q(reopen_sql(mission_id, plan, actor=actor)) or "").strip()
    except Exception as exc:  # noqa: BLE001 - caller logs; the guard must not die
        plan["ok"] = False
        plan["code"] = f"sql_error:{type(exc).__name__}"
        return plan
    plan["applied"] = bool(out)
    if not out:
        plan["ok"] = False
        plan["code"] = "not_applied"
    return plan