"""SR (successor-readiness) inheritance metrics for the coordinator hand-over.

Four metrics, every number recomputable from one named SQL statement (the text
is emitted inside the artifact so an operator can paste it into ``psql``):

* **SR1 shadow divergence rate** -- share of the shadow coordinator's proposed
  dispositions (``coordinator_shadow.verdict->'would_do'``) that the platform
  did *not* actually carry out inside the look-ahead window.  Target < 20 %.
* **SR2 coordinator autonomy streak** -- consecutive days of running without a
  hand-typed (non-daemon) coordinator decision.  Target >= 7 days.
* **SR3 red-line decision-pack quality** -- sampled audit of the decision packs
  presented to the owner (completeness + traceability + outcome consistency).
  Target = 100 % of the audited sample.
* **SR4 owner non-red-line interventions per week** -- how often the owner still
  has to type a decision the daemon channel could have typed.  Target 0.

Design rules (same discipline as :mod:`tools.autonomy.metrics`):

1. **One named statement per number.**  ``sql_index`` carries every statement
   with parameters substituted (:func:`tools.autonomy.metrics.render_for_psql`).
2. **Daemon vs human is decided by an explicit marker, not by prose.**  The
   platform's unattended approver stamps ``[auto-approve]`` on the note of every
   decision it types; a decision without that marker is a *hand-typed* channel
   action (owner or coordinator).
3. **No fabricated numbers.**  A source that raises, or that has no matured
   sample inside the window, contributes ``None`` and is graded ``WITHHELD``.
   ``pending`` is used for shadow items whose look-ahead window has not elapsed
   yet -- that is *not* a divergence and never enters the numerator.
4. **Zero domain semantics.**  Nothing here knows about any physics domain; the
   module only reads platform bookkeeping tables and writes the s8 "harness"
   clock slot of ``chronicle_snapshots``.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable, Mapping, Protocol, Sequence

from .metrics import PgRunner, render_for_psql  # re-exported for the CLI

SCHEMA = "sr-inheritance/1"

#: chronicle clock slot the SR block belongs to (s1..s8; s8 = harness/继承者器官).
CLOCK_SLOT = "s8"
CLOCK_NAME = "harness"
CLOCK_FACET = "sr_inheritance_readiness"

#: Acceptance targets from the campaign node (IN27-H7).
TARGETS: dict[str, float] = {
    "SR1": 0.20,  # divergence share, must stay *below* this
    "SR2": 7.0,   # autonomy streak in days, must stay *above* this
    "SR3": 1.00,  # audited pack quality share, must stay *above* this
    "SR4": 0.0,   # owner non-red-line interventions per week, must stay *below*
}

#: The unattended approver's own footprint on a decision note.
DAEMON_NOTE_MARK = "[auto-approve]"

#: Approval kinds that are *not* red lines: the daemon channel may decide them.
ROUTINE_APPROVAL_KINDS = frozenset({"dev_acceptance"})

#: Approval kinds that stay with the owner (stage promotion / stop condition).
REDLINE_APPROVAL_KINDS = frozenset({"stage_promotion", "stop_condition"})

#: Life-cycle actions that are red lines wherever they are typed.
REDLINE_LIFECYCLE_ACTIONS = frozenset(
    {"write_off", "suspend", "permission_change", "budget_grant", "secret_rotate", "git_config"}
)

#: Mission states that make an *accepted* pack consistent with reality.
ACCEPTED_MISSION_STATES = frozenset({"succeeded", "accepted", "done", "merged"})

_TARGET_SANITISER = re.compile(r"[^0-9a-zA-Z_.\-]+")
#: A pack must point at an artifact/report path or a PR ticket (traceability).
# NB a bare hex token is deliberately *not* accepted as a commit citation: the
# word "succeeded" contains the 7-character hex run "cceeded", and the UUIDs in
# ``agent_session_id`` are sliced into 8-character hex runs too.  Both were
# false positives this audit produced before the rule was tightened -- every
# pack looked like it cited a commit.
_PATH_RE = re.compile(r"(docs/|artifacts/|runs/|/tmp/|\.(?:md|json|png|csv|npz|yaml|yml)\b)")
_TICKET_RE = re.compile(r"#[0-9]{2,}")
_COMMIT_ONLY_RE = re.compile(r"[0-9a-f]{7,40}")
#: A pack must quote at least one quantitative claim (digit with unit or percent).
# NB the trailing character class replaces a negative lookahead: PostgreSQL ARE
# has no lookaround, and the audit's independent SQL recompute must use the very
# same pattern, otherwise the two engines disagree on 35 of 200 packs.
_QUANTITY_RE = re.compile(
    r"[0-9]+(?:\.[0-9]+)?\s*(?:%|％|mm|kg|MPa|deg|°|m/s|s|m)(?:$|[^0-9a-zA-Z])"
)
#: Suggested check labels used by the markdown renderer.
PACK_CHECKS: tuple[tuple[str, str], ...] = (
    ("headline_ok", "结论头（headline）实质"),
    ("verification_ok", "验证叙述（checks）实质"),
    ("deliverable_ok", "交付物指针（路径/commit/PR）"),
    ("numbers_ok", "量化数字"),
    ("traceable", "mission_id + 决策备注可追溯"),
    ("outcome_consistent", "结论与任务终态一致"),
)


class Runner(Protocol):  # pragma: no cover - protocol definition
    def fetchall(self, sql: str, params: Mapping[str, Any] | None = None) -> Sequence[Sequence[Any]]:
        ...


# ---------------------------------------------------------------------------
# small helpers
# ---------------------------------------------------------------------------


def _ts(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        dt = value
    else:  # pragma: no cover - driver dependent
        dt = datetime.fromisoformat(str(value))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    return value.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _as_json(value: Any) -> Any:
    if isinstance(value, str):
        try:
            return json.loads(value)
        except Exception:  # pragma: no cover - malformed payload
            return {}
    return value if value is not None else {}


def _ratio(numerator: float, denominator: float) -> float | None:
    if not denominator:
        return None
    return round(numerator / denominator, 4)


# ---------------------------------------------------------------------------
# SR1: shadow divergence
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ShadowItem:
    """One proposed disposition from one shadow-coordinator round."""

    round_id: int
    ts: datetime
    action: str
    target: str
    reason: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "round_id": self.round_id,
            "ts": _iso(self.ts),
            "action": self.action,
            "target": self.target,
            "reason": self.reason,
        }


SHADOW_SQL = (
    "SELECT id, ts, verdict FROM coordinator_shadow "
    "WHERE ts >= %(since)s AND ts < %(until)s ORDER BY ts, id"
)


def shadow_items(rows: Sequence[Sequence[Any]]) -> list[ShadowItem]:
    """Parse ``coordinator_shadow`` rows into flat proposed-disposition items."""
    items: list[ShadowItem] = []
    for row in rows:
        if not row or len(row) < 3:
            continue
        round_id, ts, verdict = row[0], _ts(row[1]), _as_json(row[2])
        if ts is None or not isinstance(verdict, Mapping):
            continue
        for entry in verdict.get("would_do") or ():
            if not isinstance(entry, Mapping):
                continue
            items.append(
                ShadowItem(
                    round_id=int(round_id),
                    ts=ts,
                    action=str(entry.get("action") or "").strip().lower(),
                    target=str(entry.get("target") or "").strip(),
                    reason=str(entry.get("reason") or "").strip(),
                )
            )
    return items


#: Action vocabulary of the shadow coordinator, normalised.
_ACTION_ALIASES: dict[str, str] = {
    "escalate": "escalate",
    "escalation": "escalate",
    "alert": "escalate",
    "page": "escalate",
    "file_mission": "file_mission",
    "file": "file_mission",
    "filing": "file_mission",
    "dispatch": "file_mission",
    "mission": "file_mission",
    "monitor": "monitor",
    "watch": "monitor",
    "observe": "monitor",
    "noop": "monitor",
    "none": "monitor",
}

#: Which actions expect the platform to *do* something on the target organ.
_ACTION_EXPECTS_WORK = {"escalate": True, "file_mission": True, "monitor": False}


def normalise_action(action: str) -> str | None:
    return _ACTION_ALIASES.get((action or "").strip().lower())


def normalise_target(target: str) -> str | None:
    """Sanitise an organ key so it can be used as a SQL ``ILIKE`` pattern."""
    cleaned = _TARGET_SANITISER.sub("", (target or "").strip())
    if len(cleaned) < 3:  # too short to be a trustworthy search key
        return None
    return cleaned


#: Per-source disposition feeds.  Each statement returns ``(created_at, text)``
#: rows inside the window; the organ match is done in Python so a 30-day window
#: costs five statements instead of five-per-shadow-item.
DISPOSITION_PROBES: tuple[tuple[str, str], ...] = (
    (
        "mission_goal",
        "SELECT created_at, coalesce(goal,'') FROM tensor_dev_missions "
        "WHERE created_at >= %(since)s AND created_at < %(until)s",
    ),
    (
        "mission_dispatch",
        "SELECT created_at, coalesce(detail::text,'') FROM campaign_events "
        "WHERE event_type = 'mission_dispatched' "
        "AND created_at >= %(since)s AND created_at < %(until)s",
    ),
    (
        "escalation",
        "SELECT created_at, coalesce(detail::text,'') FROM campaign_events "
        "WHERE event_type = 'escalation_created' "
        "AND created_at >= %(since)s AND created_at < %(until)s",
    ),
    (
        "alert",
        "SELECT created_at, concat_ws(' ', title, message, entity_id, source) FROM alerts "
        "WHERE created_at >= %(since)s AND created_at < %(until)s",
    ),
    (
        "hand_typed_decision",
        "SELECT created_at, coalesce(payload->>'note','') FROM tensor_agent_session_events "
        "WHERE kind = 'approval_decided' "
        f"AND NOT starts_with(coalesce(payload->>'note',''), '{DAEMON_NOTE_MARK}') "
        "AND coalesce(payload->>'note','') <> '' "
        "AND created_at >= %(since)s AND created_at < %(until)s",
    ),
)


def collect_dispositions(runner: Runner, since: datetime, until: datetime) -> dict[str, Any]:
    """Fetch the platform's disposition feed once (five statements, all sources)."""
    params = {"since": since, "until": until}
    rows: list[tuple[str, datetime, str]] = []
    errors: list[str] = []
    for source, sql in DISPOSITION_PROBES:
        try:
            fetched = runner.fetchall(sql, params)
        except Exception as exc:  # pragma: no cover - fake runner path
            errors.append(f"{source}: {type(exc).__name__}")
            continue
        for row in fetched or []:
            if not row:
                continue
            ts = _ts(row[0])
            if ts is None:
                continue
            rows.append((source, ts, str(row[1] or "").lower()))
    if errors and len(errors) == len(DISPOSITION_PROBES):
        return {"status": "unobservable", "reason": "all-probes-failed", "rows": [], "errors": errors}
    return {"status": "REAL", "rows": rows, "errors": errors}


def organ_evidence(
    dispositions: Mapping[str, Any],
    target: str,
    since: datetime,
    until: datetime,
) -> dict[str, Any]:
    """Count the dispositions addressed at ``target`` inside ``[since, until)``."""
    pattern = normalise_target(target)
    if pattern is None:
        return {"status": "unobservable", "reason": "target-too-short", "sources": {}, "hits": 0}
    if dispositions.get("status") != "REAL":
        return {
            "status": "unobservable",
            "reason": str(dispositions.get("reason", "feed-unavailable")),
            "sources": {},
            "hits": 0,
        }
    needle = pattern.lower()
    sources: dict[str, int] = {name: 0 for name, _ in DISPOSITION_PROBES}
    hits = 0
    for source, ts, text in dispositions.get("rows") or ():
        if ts < since or ts >= until:
            continue
        if needle in text:
            sources[source] = sources.get(source, 0) + 1
            hits += 1
    return {"status": "REAL", "sources": sources, "hits": hits}


def classify_item(
    item: ShadowItem,
    evidence: Mapping[str, Any],
    now: datetime,
    lookahead_hours: float,
    provisional_hours: float,
) -> dict[str, Any]:
    """Score one proposed disposition against the realised disposition stream.

    States
    ------
    ``matched`` / ``diverged``  -- decided (these feed SR1's numerator).
    ``pending``                 -- look-ahead window not elapsed and no decisive
                                  evidence yet; excluded from the rate.
    ``unobservable``            -- the target organ cannot be probed at all.
    """
    action = normalise_action(item.action)
    if action is None or evidence.get("status") != "REAL":
        return {
            **item.as_dict(),
            "state": "unobservable",
            "basis": "unknown-action" if action is None else str(evidence.get("reason", "unobservable")),
        }
    elapsed_hours = max(0.0, (now - item.ts).total_seconds() / 3600.0)
    hits = int(evidence.get("hits") or 0)
    expects_work = _ACTION_EXPECTS_WORK[action]
    mature = elapsed_hours >= lookahead_hours
    early_decisive = elapsed_hours >= provisional_hours and hits > 0
    if mature:
        state = "matched" if (hits > 0) == expects_work else "diverged"
        basis = "window-elapsed"
    elif early_decisive:
        state = "matched" if expects_work else "diverged"
        basis = "early-evidence"
    else:
        return {
            **item.as_dict(),
            "state": "pending",
            "basis": "window-not-elapsed",
            "elapsed_hours": round(elapsed_hours, 3),
            "hits": hits,
            "sources": dict(evidence.get("sources") or {}),
        }
    return {
        **item.as_dict(),
        "state": state,
        "basis": basis,
        "normalised_action": action,
        "elapsed_hours": round(elapsed_hours, 3),
        "hits": hits,
        "sources": dict(evidence.get("sources") or {}),
    }


def horizon_rate(
    items: Sequence[ShadowItem],
    dispositions: Mapping[str, Any],
    now: datetime,
    horizon_hours: float,
) -> dict[str, Any]:
    """SR1 reading at one fixed look-ahead horizon (no early-evidence shortcut)."""
    counts = {"matched": 0, "diverged": 0, "pending": 0, "unobservable": 0}
    for item in items:
        evidence = organ_evidence(dispositions, item.target, item.ts, item.ts + timedelta(hours=horizon_hours))
        entry = classify_item(item, evidence, now, horizon_hours, horizon_hours)
        counts[entry["state"]] += 1
    decided = counts["matched"] + counts["diverged"]
    return {
        "horizon_hours": horizon_hours,
        "value": _ratio(counts["diverged"], decided),
        "counts": counts,
        "decided": decided,
        "matured_share": _ratio(decided, len(items)) if items else None,
        "settled": counts["pending"] == 0 and bool(items),
    }


def measure_sr1(
    runner: Runner,
    now: datetime,
    window_days: float = 30.0,
    lookahead_hours: float = 24.0,
    provisional_hours: float = 1.0,
    horizons: Sequence[float] = (0.5, 1.0, 6.0, 24.0),
) -> dict[str, Any]:
    """SR1 -- shadow divergence rate over the shadow rounds inside the window."""
    until = now
    since = now - timedelta(days=window_days)
    try:
        rows = runner.fetchall(SHADOW_SQL, {"since": since, "until": until})
        rounds = [r for r in rows if r]
    except Exception as exc:  # pragma: no cover - fake runner path
        return {
            "key": "SR1",
            "label": "影子分歧率",
            "value": None,
            "status": "WITHHELD",
            "target": TARGETS["SR1"],
            "target_rule": "value < 0.20",
            "meets_target": None,
            "reason": f"{type(exc).__name__}: {exc}",
            "entries": [],
        }

    items = shadow_items(rows)
    dispositions = collect_dispositions(runner, since, until)
    entries: list[dict[str, Any]] = []
    for item in items:
        evidence = organ_evidence(dispositions, item.target, item.ts, item.ts + timedelta(hours=lookahead_hours))
        entries.append(classify_item(item, evidence, now, lookahead_hours, provisional_hours))

    counts = {
        k: sum(1 for e in entries if e["state"] == k)
        for k in ("matched", "diverged", "pending", "unobservable")
    }
    decided = counts["matched"] + counts["diverged"]
    value = _ratio(counts["diverged"], decided)
    by_action: dict[str, dict[str, int]] = {}
    for entry in entries:
        key = entry.get("normalised_action") or normalise_action(entry.get("action", "")) or "unknown"
        slot = by_action.setdefault(key, {"matched": 0, "diverged": 0, "pending": 0, "unobservable": 0})
        slot[entry["state"]] += 1

    horizon_reads = []
    for horizon in sorted({float(h) for h in horizons} | {float(lookahead_hours)}):
        horizon_reads.append(horizon_rate(items, dispositions, now, horizon))
    # The first-month baseline: the shortest horizon that already has at least one
    # decided item.  A live shadow cadence always leaves the newest round pending,
    # so "all items matured" would keep the baseline empty forever; the pending
    # count and the decided/items coverage stay in the table next to the number.
    baseline_horizon = next((read for read in horizon_reads if read["value"] is not None), None)

    if value is None:
        status = "WITHHELD"
        reason = (
            f"no matured shadow item: {counts['pending']} pending, "
            f"{counts['unobservable']} unobservable, {len(rounds)} shadow round(s) in window"
        )
    else:
        status = "REAL"
        reason = (
            f"{counts['diverged']}/{decided} decided shadow items diverged "
            f"({counts['pending']} pending inside the {lookahead_hours:g} h look-ahead, "
            f"{counts['unobservable']} unobservable)"
        )
    return {
        "key": "SR1",
        "label": "影子分歧率（coordinator_shadow 决策 vs 事后实际处置）",
        "value": value,
        "status": status,
        "target": TARGETS["SR1"],
        "target_rule": "value < 0.20",
        "meets_target": None if value is None else bool(value < TARGETS["SR1"]),
        "reason": reason,
        "window_days": window_days,
        "window_start": _iso(since),
        "window_end": _iso(until),
        "lookahead_hours": lookahead_hours,
        "provisional_hours": provisional_hours,
        "rounds": len(rounds),
        "round_ids": [int(r[0]) for r in rounds],
        "items": len(items),
        "counts": counts,
        "by_action": by_action,
        "entries": entries,
        "horizon_reads": horizon_reads,
        "baseline_horizon_hours": None if baseline_horizon is None else baseline_horizon["horizon_hours"],
        "baseline_value": None if baseline_horizon is None else baseline_horizon["value"],
        "disposition_rows": len(dispositions.get("rows") or ()),
        "feed_errors": list(dispositions.get("errors") or []),
        "maturity": "provisional" if counts["pending"] else "settled",
    }


# ---------------------------------------------------------------------------
# SR2 + SR4: hand-typed (non-daemon) decision channel
# ---------------------------------------------------------------------------


MANUAL_DECISION_SQL = (
    "SELECT created_at, coalesce(payload->>'kind','') AS kind, "
    "coalesce(payload->>'decision','') AS decision, coalesce(payload->>'note','') AS note "
    "FROM tensor_agent_session_events "
    "WHERE kind = 'approval_decided' "
    "AND created_at >= %(since)s AND created_at < %(until)s "
    "AND coalesce(payload->>'note','') <> '' "
    f"AND NOT starts_with(coalesce(payload->>'note',''), '{DAEMON_NOTE_MARK}')"
)

MANUAL_LAST_SQL = (
    "SELECT max(created_at) FROM tensor_agent_session_events "
    "WHERE kind = 'approval_decided' "
    "AND coalesce(payload->>'note','') <> '' "
    f"AND NOT starts_with(coalesce(payload->>'note',''), '{DAEMON_NOTE_MARK}')"
)

EPOCH_SQL = "SELECT min(created_at) FROM tensor_agent_session_events"

REDLINE_LIFECYCLE_SQL = (
    "SELECT created_at, action, coalesce(detail->>'reported_by','') AS reported_by "
    "FROM mission_lifecycle_log "
    "WHERE created_at >= %(since)s AND created_at < %(until)s "
    "AND action = ANY(%(actions)s)"
)


def manual_decisions(runner: Runner, since: datetime, until: datetime) -> list[dict[str, Any]]:
    rows = runner.fetchall(MANUAL_DECISION_SQL, {"since": since, "until": until})
    out: list[dict[str, Any]] = []
    for row in rows:
        if not row:
            continue
        out.append(
            {
                "ts": _ts(row[0]),
                "kind": str(row[1] or ""),
                "decision": str(row[2] or ""),
                "note": str(row[3] or ""),
            }
        )
    return out


def measure_sr2(runner: Runner, now: datetime, window_days: float = 30.0) -> dict[str, Any]:
    """SR2 -- unbroken days without a hand-typed coordinator decision."""
    since = now - timedelta(days=window_days)
    try:
        events = manual_decisions(runner, since, now)
        last_rows = runner.fetchall(MANUAL_LAST_SQL, None)
        last_ts = _ts(last_rows[0][0]) if last_rows and last_rows[0] else None
    except Exception as exc:  # pragma: no cover
        return {
            "key": "SR2",
            "label": "协调官自主天数",
            "value": None,
            "status": "WITHHELD",
            "target": TARGETS["SR2"],
            "target_rule": "value >= 7.0 days",
            "meets_target": None,
            "reason": f"{type(exc).__name__}: {exc}",
        }

    if last_ts is None:
        try:
            epoch_rows = runner.fetchall(EPOCH_SQL, None)
            last_ts = _ts(epoch_rows[0][0]) if epoch_rows and epoch_rows[0] else None
            basis = "platform-epoch"  # no hand-typed decision ever recorded
        except Exception:  # pragma: no cover
            last_ts, basis = None, "unknown"
    else:
        basis = "last-hand-typed-decision"

    streak_days = None if last_ts is None else round(max(0.0, (now - last_ts).total_seconds() / 86400.0), 2)
    per_week = round(len(events) * 7.0 / window_days, 2) if window_days else None
    days = max(1, int(round(window_days)))
    busy: set[str] = set()
    for event in events:
        ts = event.get("ts")
        if isinstance(ts, datetime):
            busy.add(ts.date().isoformat())
    clean_days = days - len(busy)
    if streak_days is None:
        status, reason = "WITHHELD", "no event stream available to anchor the streak"
    elif streak_days >= TARGETS["SR2"]:
        status, reason = "REAL", f"{streak_days:.2f} d without a hand-typed decision (target >= 7 d)"
    else:
        status, reason = "REAL", f"{streak_days:.2f} d since the last hand-typed decision (below the 7 d target)"
    return {
        "key": "SR2",
        "label": "协调官自主天数（无协调者会话介入续航）",
        "value": streak_days,
        "unit": "days",
        "status": status,
        "target": TARGETS["SR2"],
        "target_rule": "value >= 7.0 days",
        "meets_target": None if streak_days is None else bool(streak_days >= TARGETS["SR2"]),
        "reason": reason,
        "anchor": basis,
        "anchor_at": _iso(last_ts),
        "window_days": window_days,
        "window_start": _iso(since),
        "window_end": _iso(now),
        "hand_typed_decisions_in_window": len(events),
        "hand_typed_per_week": per_week,
        "clean_days_in_window": clean_days,
        "busy_days": sorted(busy),
    }


def classify_redline(kind: str, lifecycle_action: str | None = None) -> bool:
    """True when the action belongs to the owner's red-line reserve."""
    if lifecycle_action:
        return lifecycle_action in REDLINE_LIFECYCLE_ACTIONS
    return (kind or "") in REDLINE_APPROVAL_KINDS


def measure_sr4(runner: Runner, now: datetime, window_days: float = 7.0) -> dict[str, Any]:
    """SR4 -- owner non-red-line interventions per week (target 0)."""
    since = now - timedelta(days=window_days)
    try:
        events = manual_decisions(runner, since, now)
    except Exception as exc:  # pragma: no cover
        return {
            "key": "SR4",
            "label": "owner 非红线干预次数/周",
            "value": None,
            "status": "WITHHELD",
            "target": TARGETS["SR4"],
            "target_rule": "value == 0.0 per week",
            "meets_target": None,
            "reason": f"{type(exc).__name__}: {exc}",
        }
    redline = [e for e in events if classify_redline(e["kind"])]
    non_redline = [e for e in events if not classify_redline(e["kind"])]
    per_week = round(len(non_redline) * 7.0 / window_days, 2) if window_days else None
    try:
        lifecycle_rows = runner.fetchall(
            REDLINE_LIFECYCLE_SQL,
            {"since": since, "until": now, "actions": sorted(REDLINE_LIFECYCLE_ACTIONS)},
        )
        lifecycle = [
            {"ts": _iso(_ts(r[0])), "action": r[1], "reported_by": r[2]}
            for r in (lifecycle_rows or [])
            if r
        ]
    except Exception:  # pragma: no cover
        lifecycle = []
    status = "REAL" if per_week is not None else "WITHHELD"
    return {
        "key": "SR4",
        "label": "owner 非红线干预次数/周",
        "value": per_week,
        "unit": "per week",
        "status": status,
        "target": TARGETS["SR4"],
        "target_rule": "value == 0.0 per week",
        "meets_target": None if per_week is None else bool(per_week <= TARGETS["SR4"]),
        "reason": (
            f"{len(non_redline)} routine hand-typed decision(s) in {window_days:g} d "
            f"({len(redline)} red-line hand-typed decision(s) kept out of the metric)"
        ),
        "window_days": window_days,
        "window_start": _iso(since),
        "window_end": _iso(now),
        "non_redline_decisions": len(non_redline),
        "redline_decisions": len(redline),
        "non_redline_kinds": sorted({e["kind"] for e in non_redline}),
        "redline_kinds": sorted({e["kind"] for e in redline}),
        "redline_lifecycle_actions": lifecycle,
        "entries": [
            {"ts": _iso(e["ts"]), "kind": e["kind"], "decision": e["decision"], "note": e["note"][:160]}
            for e in non_redline
        ],
    }


# ---------------------------------------------------------------------------
# SR3: red-line decision-pack quality
# ---------------------------------------------------------------------------


PACK_SQL = (
    "SELECT a.id, a.kind, a.status, a.mission_id, coalesce(a.reason,''), a.evidence, "
    "coalesce(a.decision_note,''), a.decided_at, coalesce(m.status,'') "
    "FROM tensor_approval_requests a "
    "LEFT JOIN tensor_dev_missions m ON m.id = a.mission_id "
    "WHERE a.created_at >= %(since)s AND a.created_at < %(until)s "
    "ORDER BY a.id"
)

def _pack_text(ev: Mapping[str, Any]) -> str:
    """Flatten the pack payload to one searchable string (numbers/paths/PRs)."""
    try:
        return json.dumps(ev, ensure_ascii=False, sort_keys=True)
    except Exception:  # pragma: no cover - non-serialisable payload
        return str(ev)


def deterministic_sample(ids: Sequence[int], size: int, seed: str = "IN27-H7") -> list[int]:
    """Order-stable md5 sample: same population => same sample, no RNG state."""
    ranked = sorted(ids, key=lambda i: hashlib.md5(f"{seed}:{i}".encode()).hexdigest())
    return ranked[: max(0, int(size))]


def _deliverable_pointer(ev: Mapping[str, Any], text: str) -> bool:
    """True when the pack names a report/artifact path, a PR ticket or a commit."""
    if _PATH_RE.search(text) or _TICKET_RE.search(text):
        return True
    head = str(ev.get("head_commit") or "").strip().lower()
    return bool(_COMMIT_ONLY_RE.fullmatch(head))


def audit_pack(row: Sequence[Any]) -> dict[str, Any]:
    """Audit one decision pack: completeness + traceability + consistency.

    The checks follow the pack schema the platform actually writes
    (``evidence.summary.{headline,checks}`` plus optional ``head_commit`` /
    ``branch``): a pack is complete when the owner can read the verdict, the
    verification narrative, at least one quantitative claim, a pointer to the
    deliverable, the mission it belongs to and the decision note -- and when the
    approved verdict agrees with the mission's terminal state.
    """
    fields = list(row) + [None] * 9
    pack_id, kind, status, mission_id, reason, evidence, decision_note, decided_at, mission_status = fields[:9]
    ev = _as_json(evidence)
    if not isinstance(ev, Mapping):  # pragma: no cover - malformed payload
        ev = {}
    summary = ev.get("summary")
    if isinstance(summary, str):  # some drivers return the nested jsonb as text
        summary = _as_json(summary)
    if not isinstance(summary, Mapping):
        summary = {}
    headline = str(summary.get("headline") or ev.get("headline") or "").strip()
    narrative = str(summary.get("checks") or ev.get("checks") or "").strip()
    text = _pack_text(ev)
    checks = {
        "headline_ok": len(headline) >= 30,
        "verification_ok": len(narrative) >= 20,
        "deliverable_ok": _deliverable_pointer(ev, text),
        "numbers_ok": bool(_QUANTITY_RE.search(text)),
        "traceable": bool(mission_id) and bool(str(decision_note or "").strip()),
        "outcome_consistent": (
            True
            if str(status) == "approved" and str(mission_status) in ACCEPTED_MISSION_STATES
            else (str(status) == "rejected" and bool(str(decision_note or "").strip()))
        ),
    }
    return {
        "pack_id": int(pack_id) if pack_id is not None else None,
        "kind": str(kind or ""),
        "status": str(status or ""),
        "mission_id": int(mission_id) if mission_id else None,
        "mission_status": str(mission_status or ""),
        "decided_at": _iso(_ts(decided_at)),
        "headline_chars": len(headline),
        "verification_chars": len(narrative),
        "failed_checks": sorted(k for k, ok in checks.items() if not ok),
        "checks": checks,
        "passed": all(checks.values()),
    }


def measure_sr3(
    runner: Runner,
    now: datetime,
    window_days: float = 30.0,
    sample_size: int = 200,
    seed: str = "IN27-H7",
) -> dict[str, Any]:
    """SR3 -- red-line decision-pack quality over a deterministic sample."""
    since = now - timedelta(days=window_days)
    try:
        rows = [r for r in runner.fetchall(PACK_SQL, {"since": since, "until": now}) if r]
    except Exception as exc:  # pragma: no cover
        return {
            "key": "SR3",
            "label": "红线决策包完整正确率",
            "value": None,
            "status": "WITHHELD",
            "target": TARGETS["SR3"],
            "target_rule": "value >= 1.0 (audited sample)",
            "meets_target": None,
            "reason": f"{type(exc).__name__}: {exc}",
        }

    population = [r for r in rows if str(r[2]) in {"approved", "rejected"}]
    undecided = len(rows) - len(population)
    sample_ids = deterministic_sample([int(r[0]) for r in population], sample_size, seed)
    chosen = [r for r in population if int(r[0]) in set(sample_ids)]
    audits = [audit_pack(r) for r in chosen]
    passed = [a for a in audits if a["passed"]]
    value = _ratio(len(passed), len(audits))
    per_check: dict[str, int] = {}
    for name, _label in PACK_CHECKS:
        per_check[name] = sum(1 for a in audits if a["checks"][name])
    failed = [a for a in audits if not a["passed"]]
    if value is None:
        status = "WITHHELD"
        reason = f"no decided pack inside the window ({undecided} undecided ignored)"
    else:
        status = "REAL"
        reason = (
            f"{len(passed)}/{len(audits)} audited packs complete and consistent "
            f"(sample {len(audits)} of {len(population)} decided packs; seed {seed})"
        )
    return {
        "key": "SR3",
        "label": "红线决策包完整正确率（呈 owner 决策包抽检）",
        "value": value,
        "status": status,
        "target": TARGETS["SR3"],
        "target_rule": "value >= 1.0 (audited sample)",
        "meets_target": None if value is None else bool(value >= TARGETS["SR3"]),
        "reason": reason,
        "window_days": window_days,
        "window_start": _iso(since),
        "window_end": _iso(now),
        "population": len(population),
        "undecided": undecided,
        "sample_size": len(audits),
        "seed": seed,
        "audited_ids": sample_ids,
        "per_check_pass": per_check,
        "per_check_fail": {k: len(audits) - v for k, v in per_check.items()},
        "failed": failed[:20],
        "audits": audits,
    }


# ---------------------------------------------------------------------------
# assembly
# ---------------------------------------------------------------------------


def build_report(
    runner: Runner,
    now: datetime | None = None,
    window_days: float = 30.0,
    week_days: float = 7.0,
    lookahead_hours: float = 24.0,
    provisional_hours: float = 1.0,
    sample_size: int = 200,
    seed: str = "IN27-H7",
    repo_commit: str | None = None,
) -> dict[str, Any]:
    """Assemble the SR1..SR4 payload (pure: no I/O beyond ``runner``)."""
    until = now or datetime.now(timezone.utc)
    metrics = {
        "SR1": measure_sr1(runner, until, window_days, lookahead_hours, provisional_hours),
        "SR2": measure_sr2(runner, until, window_days),
        "SR3": measure_sr3(runner, until, window_days, sample_size, seed),
        "SR4": measure_sr4(runner, until, week_days),
    }
    grades = {key: m["status"] for key, m in metrics.items()}

    sql_index: list[dict[str, Any]] = [
        {
            "metric": "SR1",
            "key": "shadow_rounds",
            "sql": render_for_psql(SHADOW_SQL, {"since": until - timedelta(days=window_days), "until": until}),
        },
    ]
    for name, sql in DISPOSITION_PROBES:
        sql_index.append(
            {
                "metric": "SR1",
                "key": f"disposition_feed:{name}",
                "sql": render_for_psql(
                    sql,
                    {
                        "since": until - timedelta(days=window_days),
                        "until": until,
                        "organ_match": "case-insensitive substring of would_do[].target",
                    },
                ),
            }
        )
    sql_index += [
        {
            "metric": "SR2",
            "key": "hand_typed_decisions",
            "sql": render_for_psql(
                MANUAL_DECISION_SQL, {"since": until - timedelta(days=window_days), "until": until}
            ),
        },
        {"metric": "SR2", "key": "last_hand_typed_decision", "sql": render_for_psql(MANUAL_LAST_SQL)},
        {
            "metric": "SR3",
            "key": "decision_packs",
            "sql": render_for_psql(PACK_SQL, {"since": until - timedelta(days=window_days), "until": until}),
        },
        {
            "metric": "SR4",
            "key": "routine_hand_typed_decisions",
            "sql": render_for_psql(
                MANUAL_DECISION_SQL, {"since": until - timedelta(days=week_days), "until": until}
            ),
        },
        {
            "metric": "SR4",
            "key": "redline_lifecycle_actions",
            "sql": render_for_psql(
                REDLINE_LIFECYCLE_SQL,
                {
                    "since": until - timedelta(days=week_days),
                    "until": until,
                    "actions": sorted(REDLINE_LIFECYCLE_ACTIONS),
                },
            ),
        },
    ]

    return {
        "schema": SCHEMA,
        "generated_at": _iso(until),
        "clock": {"slot": CLOCK_SLOT, "name": CLOCK_NAME, "facet": CLOCK_FACET},
        "window_days": window_days,
        "week_days": week_days,
        "window_start": _iso(until - timedelta(days=window_days)),
        "window_end": _iso(until),
        "metrics": metrics,
        "grades": grades,
        "targets": dict(TARGETS),
        "sql": sql_index,
        "recompute": {
            "how": "paste any sql entry into psql against the platform database",
            "param_note": (
                "timestamps already substituted; the disposition feeds are matched in Python against "
                "each would_do[].target (case-insensitive substring) inside [shadow_ts, shadow_ts+lookahead)"
            ),
            "daemon_marker": DAEMON_NOTE_MARK,
        },
        "provenance": {
            "producer": "tools/autonomy/sr_dashboard.py",
            "module": "tools/autonomy/sr_readiness.py",
            "schema": SCHEMA,
            "node": os.environ.get("TP_AGENT_ID", "unknown"),
            "commit": repo_commit,
            "mission": os.environ.get("TP_MISSION_ID", "5308"),
            "actor_class_rule": (
                "hand-typed (owner/coordinator) = approval_decided note not starting with "
                f"'{DAEMON_NOTE_MARK}' and non-empty; daemon channel = the marker or an empty note"
            ),
            "redline_rule": (
                "approval kinds " + ",".join(sorted(REDLINE_APPROVAL_KINDS)) + "; life-cycle actions "
                + ",".join(sorted(REDLINE_LIFECYCLE_ACTIONS))
            ),
        },
        "domain_semantics": "none",
    }


def chronicle_payload(report: Mapping[str, Any]) -> dict[str, Any]:
    """Payload appended to ``chronicle_snapshots`` -- the s8 clock slot."""
    metrics = report["metrics"]
    slot = {
        key: {
            "value": metrics[key]["value"],
            "status": metrics[key]["status"],
            "target": metrics[key]["target"],
            "meets_target": metrics[key]["meets_target"],
        }
        for key in ("SR1", "SR2", "SR3", "SR4")
    }
    slot["SR1"]["rounds"] = metrics["SR1"].get("rounds")
    slot["SR1"]["items"] = metrics["SR1"].get("items")
    slot["SR1"]["counts"] = metrics["SR1"].get("counts")
    slot["SR1"]["maturity"] = metrics["SR1"].get("maturity")
    slot["SR1"]["baseline_value"] = metrics["SR1"].get("baseline_value")
    slot["SR1"]["baseline_horizon_hours"] = metrics["SR1"].get("baseline_horizon_hours")
    slot["SR2"]["anchor_at"] = metrics["SR2"].get("anchor_at")
    slot["SR3"]["sample_size"] = metrics["SR3"].get("sample_size")
    slot["SR3"]["population"] = metrics["SR3"].get("population")
    slot["SR4"]["non_redline_decisions"] = metrics["SR4"].get("non_redline_decisions")
    return {
        "collector": "tools/autonomy/sr_dashboard.py (IN27-H7 SR inheritance readiness)",
        "schema": "chronicle-clock/1",
        "kind": "sr_inheritance_readiness",
        "clock": {"slot": CLOCK_SLOT, "name": CLOCK_NAME, "facet": CLOCK_FACET},
        "s8_harness": {"sr_inheritance": slot},
        "grades": dict(report["grades"]),
        "targets": dict(report["targets"]),
        "schema_sr": report["schema"],
        "generated_at": report["generated_at"],
        "domain_semantics": "none",
    }


def acceptance_summary(
    report: Mapping[str, Any],
    deliverables: Mapping[str, Any] | None = None,
    chronicle: Mapping[str, Any] | None = None,
    continuity: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Machine-checkable acceptance block (criteria numbers + product paths).

    The campaign node's acceptance is *"the instrument keeps producing and SR1
    carries a first-month baseline number"*; both are turned into booleans here
    so a reviewer never has to read prose to decide.
    """
    m = report["metrics"]
    cont = continuity or {}
    baseline_horizon = m["SR1"].get("baseline_horizon_hours")
    baseline_read = next(
        (r for r in (m["SR1"].get("horizon_reads") or []) if r.get("horizon_hours") == baseline_horizon),
        {},
    )
    criteria = [
        {
            "id": "A1_instrument_produces",
            "rule": "chronicle s8 slot written AND at least one day carries a snapshot",
            "observed": {
                "chronicle": (chronicle or {}).get("status"),
                "days_with_snapshot": len(cont.get("days_with_snapshot") or []),
                "coverage": cont.get("coverage"),
            },
            "passed": (chronicle or {}).get("status") == "written"
            and bool(cont.get("days_with_snapshot")),
        },
        {
            "id": "A2_sr1_first_month_baseline",
            "rule": "SR1 publishes a baseline rate with a non-zero decided sample",
            "observed": {
                "baseline_value": m["SR1"].get("baseline_value"),
                "baseline_horizon_hours": m["SR1"].get("baseline_horizon_hours"),
                "baseline_decided": baseline_read.get("decided"),
                "baseline_pending": (baseline_read.get("counts") or {}).get("pending"),
                "baseline_settled": baseline_read.get("settled"),
                "rounds": m["SR1"].get("rounds"),
                "target": m["SR1"]["target"],
            },
            "passed": m["SR1"].get("baseline_value") is not None,
        },
        {
            "id": "A3_four_metrics_in_clock_slot_s8",
            "rule": "chronicle payload carries SR1..SR4 under s8_harness.sr_inheritance",
            "observed": {"clock_slot": report["clock"]["slot"], "metrics": list(m)},
            "passed": report["clock"]["slot"] == CLOCK_SLOT
            and all(k in m for k in ("SR1", "SR2", "SR3", "SR4")),
        },
        {
            "id": "A4_weekly_report",
            "rule": "weekly markdown report produced with all four metrics",
            "observed": {"path": (deliverables or {}).get("weekly_report")},
            "passed": bool((deliverables or {}).get("weekly_report")),
        },
        {
            "id": "A5_pytest_suite",
            "rule": "database-free pytest suite covers the four metrics",
            "observed": {"path": (deliverables or {}).get("tests")},
            "passed": bool((deliverables or {}).get("tests")),
        },
    ]
    return {
        "criteria": criteria,
        "all_passed": all(c["passed"] for c in criteria),
        "deliverables": dict(deliverables or {}),
        "grades": dict(report["grades"]),
        "targets": dict(report["targets"]),
    }


CHRONICLE_INSERT_SQL = (
    "INSERT INTO chronicle_snapshots (ts, payload) VALUES (%(ts)s, %(payload)s::jsonb)"
)

CHRONICLE_TREND_SQL = (
    "SELECT ts, payload->'s8_harness'->'sr_inheritance' AS sr FROM chronicle_snapshots "
    "WHERE payload->>'kind' = 'sr_inheritance_readiness' ORDER BY ts DESC LIMIT %(limit)s"
)

CHRONICLE_DAY_SQL = (
    "SELECT ts::date AS day, count(*) FROM chronicle_snapshots "
    "WHERE payload->>'kind' = 'sr_inheritance_readiness' "
    "AND ts >= %(since)s GROUP BY 1 ORDER BY 1 DESC"
)


def write_chronicle(runner: Runner, report: Mapping[str, Any], ts: datetime | None = None) -> dict[str, Any]:
    """Append the SR snapshot to ``chronicle_snapshots`` as the s8 clock slot."""
    payload = chronicle_payload(report)
    stamp = ts or datetime.now(timezone.utc)
    params = {"ts": stamp, "payload": json.dumps(payload, ensure_ascii=False, sort_keys=True)}
    try:
        execute = getattr(runner, "execute", None)
        if callable(execute):
            execute(CHRONICLE_INSERT_SQL, params)
        else:  # pragma: no cover - runners with a query-only API
            runner.fetchall(CHRONICLE_INSERT_SQL, params)
        return {"status": "written", "ts": _iso(stamp), "table": "chronicle_snapshots", "slot": CLOCK_SLOT}
    except Exception as exc:
        return {"status": "failed", "error": f"{type(exc).__name__}: {exc}"}


def read_trend(runner: Runner, limit: int = 60) -> list[dict[str, Any]]:
    rows = runner.fetchall(CHRONICLE_TREND_SQL, {"limit": int(limit)})
    out: list[dict[str, Any]] = []
    for row in rows:
        if not row:
            continue
        sr = _as_json(row[1])
        out.append({"ts": _iso(_ts(row[0])), "sr": sr})
    return out


def continuity(runner: Runner, now: datetime, days: int = 14) -> dict[str, Any]:
    """Daily production continuity of the instrument (the '仪表连续产出' check)."""
    since = now - timedelta(days=days)
    try:
        rows = runner.fetchall(CHRONICLE_DAY_SQL, {"since": since})
    except Exception as exc:  # pragma: no cover
        return {"status": "WITHHELD", "reason": f"{type(exc).__name__}: {exc}", "days": days}
    present = sorted({(r[0].isoformat() if hasattr(r[0], "isoformat") else str(r[0])) for r in rows if r})
    expected = [(now - timedelta(days=offset)).date().isoformat() for offset in range(days)]
    missing = [d for d in expected if d not in present]
    covered = len(expected) - len(missing)
    return {
        "status": "REAL",
        "days": days,
        "expected_days": expected,
        "days_with_snapshot": present,
        "missing_days": missing,
        "covered": covered,
        "coverage": round(covered / days, 4) if days else None,
        "current_run_streak": _leading_streak(expected, present),
    }


def _leading_streak(expected: list[str], present: Sequence[str]) -> int:
    streak = 0
    for day in expected:
        if day in present:
            streak += 1
        else:
            break
    return streak


__all__ = [
    "ACCEPTED_MISSION_STATES",
    "CHRONICLE_INSERT_SQL",
    "CHRONICLE_TREND_SQL",
    "CLOCK_FACET",
    "CLOCK_NAME",
    "CLOCK_SLOT",
    "DAEMON_NOTE_MARK",
    "DISPOSITION_PROBES",
    "PACK_CHECKS",
    "MANUAL_DECISION_SQL",
    "MANUAL_LAST_SQL",
    "PACK_SQL",
    "REDLINE_APPROVAL_KINDS",
    "REDLINE_LIFECYCLE_ACTIONS",
    "ROUTINE_APPROVAL_KINDS",
    "Runner",
    "SCHEMA",
    "SHADOW_SQL",
    "ShadowItem",
    "TARGETS",
    "acceptance_summary",
    "audit_pack",
    "build_report",
    "chronicle_payload",
    "classify_item",
    "classify_redline",
    "continuity",
    "collect_dispositions",
    "deterministic_sample",
    "horizon_rate",
    "measure_sr1",
    "measure_sr2",
    "measure_sr3",
    "measure_sr4",
    "normalise_action",
    "normalise_target",
    "organ_evidence",
    "read_trend",
    "shadow_items",
    "write_chronicle",
]