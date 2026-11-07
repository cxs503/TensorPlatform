"""REOPENED state formalisation — SPEC §4.2 lifecycle, machine-checked.

Why this module exists
----------------------
Before this module the platform had no formal way back from a terminal mission
status.  When a defect surfaced *after* acceptance (a red regression, invalidated
evidence, a reversed review verdict), an out-of-band daemon or an operator simply
dragged the mission back into a runnable state.  The result was invisible in the
vocabulary: nothing on the mission said "this one is back", nothing recorded who
reopened it, which acceptance record was overturned, or why — and SPEC §4.2
requires exactly those three things (independent governance actor, the original
acceptance record, a stated reason) plus the audit trail that distinguishes a
justified reopen from close/reopen churn used to dodge acceptance (§10).

Design rules
------------
* **No vocabulary literal in code.**  Every word comes from the contracts:

  - ``backend/contracts/mission_status_vocab.v1.json`` (#statuses / #terminal)
  - ``backend/contracts/mission_state_machine.v1.json`` (transition graph +
    the ``reopen`` rule block)

* **One rule block, two consumers.**  The backend endpoint
  (``api/tensor_mission_lifecycle.py``) and the ops-side guard
  (``ops/taskplane/mission_reopen.py``) both *derive* their decision from the
  same JSON block; ``backend/tests/test_mission_reopened_state.py`` locks them
  to identical decisions over the full status × actor × payload matrix, so the
  two thin layers cannot drift apart unnoticed.

* **Independent governance actor only.**  A mission may not reopen itself: the
  actor must hold a governance role *and* must not be the producer identity
  recorded on the mission (self-reopening is how acceptance gets dodged).

Pure standard library at import time (json + re); nothing here touches the DB.

Audit chain (see migrations/103_mission_reopened.sql)
-----------------------------------------------------
``tensor_dev_missions`` gains ``reopened_from`` / ``reopen_ref`` /
``reopen_reason`` / ``reopened_at``, and the DB itself refuses a ``reopened``
row without the from-state and the cited reference, so "cite the original
acceptance record" survives even a buggy writer.  Every reopen also appends one
append-only ``mission_lifecycle_log`` row with ``action='reopen'``.
"""
from __future__ import annotations

import re
from typing import Any

from api.domain.status_contracts import (
    mission_state_machine_contract,
    mission_status_contract,
)

_SM = mission_state_machine_contract()
_VOCAB = mission_status_contract()

# ── vocabulary (derived, never hand-written) ────────────────────────────────
STATUSES: tuple[str, ...] = tuple(_VOCAB["statuses"])
TERMINAL: tuple[str, ...] = tuple(_VOCAB["terminal"])
NON_TERMINAL: tuple[str, ...] = tuple(s for s in STATUSES if s not in TERMINAL)

# ── transition graph (derived) ──────────────────────────────────────────────
TRANSITIONS: dict[str, tuple[str, ...]] = {
    src: tuple(edge["to"]) for src, edge in _SM["transitions"].items()
}

# ── reopen rule block (derived) ─────────────────────────────────────────────
GOVERNANCE_ROLES: tuple[str, ...] = tuple(_SM["governance_roles"])
REOPEN_FROM: tuple[str, ...] = tuple(_SM["reopen"]["from"])
REOPEN_TARGETS: tuple[str, ...] = tuple(_SM["reopen"]["targets"])
REOPEN_RELEASE_TO: str = _SM["reopen"]["release_to"]
REOPEN_REQUIRE: tuple[str, ...] = tuple(_SM["reopen"]["require"])
REASON_MIN_CHARS: int = int(_SM["reopen"]["min_reason_chars"])
ACCEPTANCE_REF_RE: re.Pattern[str] = re.compile(_SM["reopen"]["acceptance_ref_pattern"])

# The canonical word for the state (taken from the contract, not spelled here so
# that a rename in the contract cannot leave a stale literal behind).
REOPENED: str = REOPEN_TARGETS[0]

# Decision codes (stable strings; both consumers return these verbatim).
OK = "ok"
NOT_REOPENABLE = "not_reopenable_from"
UNKNOWN_STATUS = "unknown_status"
NOT_GOVERNANCE = "not_governance_actor"
PRODUCER = "producer_cannot_reopen"
REF_MISSING = "acceptance_ref_missing"
REF_MALFORMED = "acceptance_ref_malformed"
REASON_TOO_SHORT = "reason_too_short"
TARGET_NOT_ALLOWED = "target_not_allowed"


class InvalidTransition(ValueError):
    """Raised when a status edge is not in the contract graph."""


class ReopenRejected(ValueError):
    """Raised by :func:`assert_reopen`; carries the machine-readable code."""

    def __init__(self, decision: dict[str, Any]) -> None:
        self.decision = decision
        super().__init__(f"reopen rejected: {decision.get('code')}")


def transitions_from(status: str) -> tuple[str, ...]:
    """Allowed successors of ``status`` per the contract graph."""
    return TRANSITIONS.get(status, ())


def can_transition(src: str, dst: str) -> bool:
    """Is ``src -> dst`` an edge of the contract graph?"""
    return dst in transitions_from(src)


def assert_transition(src: str, dst: str) -> None:
    """Guard a status write; raises :class:`InvalidTransition` when illegal."""
    if src not in TRANSITIONS:
        raise InvalidTransition(f"unknown source status {src!r}")
    if dst not in STATUSES:
        raise InvalidTransition(f"unknown target status {dst!r}")
    if not can_transition(src, dst):
        raise InvalidTransition(
            f"illegal transition {src!r} -> {dst!r}; "
            f"allowed: {','.join(transitions_from(src)) or '<none>'}"
        )


def is_terminal(status: str) -> bool:
    return status in TERMINAL


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
    """Decide whether a reopen is admissible; never raises.

    Returns a decision dict ``{"ok", "code", "from_status", "to_status", ...}``
    that is the *same object shape* on the backend and on the ops side.

    Rules (in order, all sourced from the contract's ``reopen`` block):
      1. ``from_status`` must be a contract status and a member of
         ``reopen.from`` (i.e. terminal);
      2. ``actor_role`` must be a governance role — producers cannot reopen;
      3. ``actor_identity`` must differ from ``producer_identity``;
      4. ``acceptance_ref`` must be present and match the citation pattern;
      5. ``reason`` must carry at least ``min_reason_chars`` characters;
      6. ``target`` (default: the graph's reopen target) must be admissible and
         the edge ``from_status -> target`` must exist in the graph.
    """
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


def assert_reopen(**kwargs: Any) -> dict[str, Any]:
    """:func:`plan_reopen` that raises :class:`ReopenRejected` when refused."""
    decision = plan_reopen(**kwargs)
    if not decision["ok"]:
        raise ReopenRejected(decision)
    return decision


def plan_release(*, from_status: str) -> dict[str, Any]:
    """Admissible release of a parked ``reopened`` mission back to the queue."""
    ok = from_status == REOPENED and can_transition(REOPENED, REOPEN_RELEASE_TO)
    return {
        "ok": ok,
        "code": OK if ok else NOT_REOPENABLE,
        "from_status": from_status,
        "to_status": REOPEN_RELEASE_TO if ok else None,
    }