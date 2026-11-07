"""Automatic reproduction (``runner='repro'``) mission trigger.

Why
---
``finish``/mission-complete is currently self-reported: a mission can claim
"criteria green" without any independent recomputation (m533 / m583 / m578
post-mortems, see backend/migrations/095_tensor_repro_integrity.sql).  The API
already exposes a *manual* ``POST /tensor/repro/queue`` endpoint
(``backend/api/tensor_repro.py::enqueue_repro``) but nothing fires it
automatically, so the queue stays empty unless an operator remembers.

What this module does
---------------------
Two entry points, both idempotent and DB-only (no FastAPI dependency, so they
can be called from the dispatcher, from ``finish`` handling, from a CLI cron
job, or from tests):

1. ``on_mission_complete(session, mission_id)`` — the *in-flow* trigger.
   When a (non-repro) mission reaches a terminal success state, enqueue exactly
   one child mission with ``runner='repro'``, ``parent_mission_id=<mother>``,
   ``status='queued'`` and a *blind order* model (never the author's model).

2. ``record_repro_verdict(session, repro_mission_id, verdict, ...)`` — when the
   repro mission finishes, append the verdict to ``tensor_repro_verdicts``
   (append-only table) and propagate it to the mother mission
   (``pass``/``partial`` → keeps ``succeeded``; ``fail``/``cheat`` →
   ``revoked``; same disciplinary policy as
   ``backend/api/tensor_repro.py::create_verdict``), then close the repro
   mission.

Degradation path (honest-FAIL discipline)
-----------------------------------------
The in-flow hook requires a one-line call from the mission-complete code path.
If that wiring is not available/approved (the platform harness is frozen as of
2026-11-02), ``scan_completed_missions(session)`` provides a *cron* fallback:
run it every 5 minutes (``python -m services.repro_trigger --scan --dsn ...``);
it finds succeeded missions that have no repro child yet and enqueues one.
Both paths share the same idempotency guard, so running them together is safe.
The cron path alone is *polling*, i.e. evidence grade **MOCK** for the
"automatic at completion" requirement (latency ≤ 5 min, no event); the
in-flow path is the REAL implementation.  See docs/repro_trigger.md.

Dialect support
---------------
Production is PostgreSQL (``jsonb`` columns, ``now()``), CI/tests are SQLite
in-memory.  All SQL is built through :func:`_sql` helpers that emit
``CAST(:p AS jsonb)`` only on PostgreSQL and plain ``:p`` elsewhere;
timestamps use ``CURRENT_TIMESTAMP``, which both dialects understand.
"""
from __future__ import annotations

import argparse
import json
import os
from typing import Any, Iterable, Mapping

from sqlalchemy import text
from sqlalchemy.engine import Connection, Engine

__all__ = [
    "TRIGGERABLE_STATUSES",
    "REPRO_RUNNER",
    "VALID_VERDICTS",
    "MOTHER_STATUS_BY_VERDICT",
    "REPRO_BUDGET",
    "find_existing_repro",
    "repro_model_for",
    "trigger_repro_for_mission",
    "record_repro_verdict",
    "on_mission_complete",
    "complete_mission",
    "scan_completed_missions",
    "ensure_schema",
]

# Mission statuses on which a repro child is spawned.  Only *succeeded* is
# verified: an honest ``failed`` mission already reported its own failure, and
# ``revoked``/``stopped`` are disciplinary/operator outcomes.
TRIGGERABLE_STATUSES = frozenset({"succeeded"})

REPRO_RUNNER = "repro"
VALID_VERDICTS = ("pass", "partial", "fail", "cheat")

# Verdict -> mother mission status (mirrors backend/api/tensor_repro.py):
#   pass/partial  : claims stand, mother stays succeeded
#   fail/cheat    : disciplinary revoke (honest-fail keeps its own status and
#                   never reaches this table with verdict=fail from the author)
MOTHER_STATUS_BY_VERDICT: dict[str, str] = {
    "pass": "succeeded",
    "partial": "succeeded",
    "fail": "revoked",
    "cheat": "revoked",
}

# Read-only reproduction: inspect + re-run criteria, never modify the repo.
REPRO_BUDGET = {"max_tool_calls": 40, "max_wall_seconds": 3600, "max_tokens": 1_500_000}

_DEFAULT_REPRO_MODEL = "repro-agent-default"
_ALT_REPRO_MODEL = "repro-agent-alt"


# ───────────────────────── dialects ─────────────────────────

def _dialect(session) -> str:
    """Return the SQL dialect name for a SQLAlchemy Session or Connection."""
    bind = getattr(session, "get_bind", lambda: None)()
    if bind is None:  # already a Connection / Engine
        bind = getattr(session, "engine", session)
    return getattr(getattr(bind, "dialect", None), "name", "sqlite")


def _as_json_param(session, name: str) -> str:
    """JSON bind placeholder: jsonb cast on PostgreSQL, plain text on SQLite."""
    if _dialect(session) == "postgresql":
        return f"CAST(:{name} AS jsonb)"
    return f":{name}"


def _dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, default=str)


def _loads(value: Any, default: Any = None) -> Any:
    if value is None:
        return default
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return default


# ───────────────────────── repro enqueue ─────────────────────────

def repro_model_for(author_model: str | None) -> str:
    """Blind order: the repro model must never be the author's model."""
    if author_model != _DEFAULT_REPRO_MODEL:
        return _DEFAULT_REPRO_MODEL
    return _ALT_REPRO_MODEL


def find_existing_repro(session, parent_mission_id: int) -> Mapping[str, Any] | None:
    """Return the (first) repro child already queued/run for a mother mission."""
    row = session.execute(
        text(
            """SELECT id, parent_mission_id, runner, model, status, created_at
               FROM tensor_dev_missions
               WHERE parent_mission_id = :pid AND runner = :runner
               ORDER BY id ASC LIMIT 1"""
        ),
        {"pid": parent_mission_id, "runner": REPRO_RUNNER},
    ).mappings().first()
    return row


def trigger_repro_for_mission(
    session,
    mission_id: int,
    *,
    org_id: int | None = None,
    repro_model: str | None = None,
    required_status: Iterable[str] | str | None = None,
    commit: bool = True,
) -> dict[str, Any] | None:
    """Enqueue one repro sub-mission for ``mission_id`` (idempotent).

    Returns the enqueue receipt (``created`` True) or ``None`` when the mother
    mission is not eligible / already has a repro child.  Never raises on
    ineligibility: the caller (finish path or cron) must not be broken by it.
    """
    mission = session.execute(
        text(
            """SELECT id, organization_id, goal, repo, base_ref, model, status, runner
               FROM tensor_dev_missions WHERE id = :mid"""
        ),
        {"mid": mission_id},
    ).mappings().first()
    if mission is None:
        return None
    if org_id is not None and mission["organization_id"] != org_id:
        return None
    if mission["runner"] == REPRO_RUNNER:
        return None  # never spawn repro-of-repro

    allowed = TRIGGERABLE_STATUSES if required_status is None else frozenset(
        {required_status} if isinstance(required_status, str) else required_status
    )
    if mission["status"] not in allowed:
        return None

    existing = find_existing_repro(session, mission["id"])
    if existing is not None:
        return None  # idempotency guard (in-flow trigger + cron may race)

    model = repro_model or repro_model_for(mission["model"])
    if model == mission["model"]:
        model = _ALT_REPRO_MODEL if model != _ALT_REPRO_MODEL else _DEFAULT_REPRO_MODEL

    goal = f"[REPRO] Verify claims of parent mission #{mission['id']}: {(mission['goal'] or '')[:200]}"
    budget_param = _as_json_param(session, "budget")

    repro_id = session.execute(
        text(
            f"""INSERT INTO tensor_dev_missions
                    (organization_id, goal, repo, base_ref, budget, runner, origin,
                     model, parent_mission_id, status)
                VALUES (:org, :goal, :repo, :ref, {budget_param},
                        :runner, 'auto_repro', :model, :pid, 'queued')
                RETURNING id"""
        ),
        {
            "org": mission["organization_id"],
            "goal": goal,
            "repo": mission["repo"],
            "ref": mission["base_ref"] or "main",
            "budget": _dumps(REPRO_BUDGET),
            "runner": REPRO_RUNNER,
            "model": model,
            "pid": mission["id"],
        },
    ).scalar()

    if commit:
        session.commit()

    return {
        "created": True,
        "id": repro_id,
        "parent_mission_id": mission["id"],
        "runner": REPRO_RUNNER,
        "repro_model": model,
        "author_model": mission["model"],
        "status": "queued",
        "budget": REPRO_BUDGET,
    }


# ───────────────────────── verdict write-back ─────────────────────────

def _existing_verdict(session, mission_id: int) -> Mapping[str, Any] | None:
    return session.execute(
        text(
            """SELECT id, mission_id, verdict, repro_model
               FROM tensor_repro_verdicts WHERE mission_id = :mid
               ORDER BY id DESC LIMIT 1"""
        ),
        {"mid": mission_id},
    ).mappings().first()


def record_repro_verdict(
    session,
    repro_mission_id: int,
    verdict: str,
    *,
    repro_model: str | None = None,
    claims_vs_recomputed: Mapping[str, Any] | None = None,
    evidence_paths: Iterable[str] | None = None,
    wall_time_sec: float | None = None,
    notes: str = "",
    allow_duplicate: bool = False,
    commit: bool = True,
) -> dict[str, Any]:
    """Append the repro verdict and propagate it to the mother mission.

    ``repro_mission_id`` is the *child* mission (``runner='repro'``).  The
    verdict row is keyed by the **mother** mission id, exactly like the API
    endpoint (``VerdictCreate.mission_id`` in backend/api/tensor_repro.py).
    """
    if verdict not in VALID_VERDICTS:
        raise ValueError(f"verdict must be one of {VALID_VERDICTS}, got {verdict!r}")

    repro = session.execute(
        text(
            """SELECT id, organization_id, parent_mission_id, model, runner
               FROM tensor_dev_missions WHERE id = :mid"""
        ),
        {"mid": repro_mission_id},
    ).mappings().first()
    if repro is None:
        raise ValueError(f"repro mission {repro_mission_id} not found")
    if repro["runner"] != REPRO_RUNNER:
        raise ValueError(f"mission {repro_mission_id} is not a repro mission")
    parent_id = repro["parent_mission_id"]
    if parent_id is None:
        raise ValueError(f"repro mission {repro_mission_id} has no parent_mission_id")

    if not allow_duplicate and _existing_verdict(session, parent_id) is not None:
        raise ValueError(f"verdict already recorded for mission {parent_id} (append-only)")

    model = repro_model or repro["model"] or _DEFAULT_REPRO_MODEL
    cvr_param = _as_json_param(session, "cvr")
    ep_param = _as_json_param(session, "ep")

    verdict_id = session.execute(
        text(
            f"""INSERT INTO tensor_repro_verdicts
                    (organization_id, mission_id, repro_model, verdict,
                     claims_vs_recomputed, evidence_paths, wall_time_sec, notes)
                VALUES (:org, :mid, :rm, :v, {cvr_param}, {ep_param}, :wt, :notes)
                RETURNING id"""
        ),
        {
            "org": repro["organization_id"],
            "mid": parent_id,
            "rm": model,
            "v": verdict,
            "cvr": _dumps(claims_vs_recomputed or {}),
            "ep": _dumps(list(evidence_paths or [])),
            "wt": wall_time_sec,
            "notes": notes or "",
        },
    ).scalar()

    # Close the repro mission itself.
    session.execute(
        text(
            """UPDATE tensor_dev_missions
               SET status = 'succeeded', finished_at = CURRENT_TIMESTAMP
               WHERE id = :mid"""
        ),
        {"mid": repro_mission_id},
    )

    # Propagate to the mother mission.
    mother_status = MOTHER_STATUS_BY_VERDICT[verdict]
    mother = session.execute(
        text("SELECT id, status, result FROM tensor_dev_missions WHERE id = :mid"),
        {"mid": parent_id},
    ).mappings().first()
    prev_status = mother["status"] if mother else None

    result = _loads(mother["result"], {}) if mother else {}
    if not isinstance(result, dict):
        result = {"raw": result}
    result["repro"] = {
        "verdict": verdict,
        "repro_mission_id": repro_mission_id,
        "repro_model": model,
        "verdict_id": verdict_id,
    }
    result_param = _as_json_param(session, "result")
    session.execute(
        text(
            f"""UPDATE tensor_dev_missions
                SET status = :status,
                    result = {result_param},
                    finished_at = COALESCE(finished_at, CURRENT_TIMESTAMP)
                WHERE id = :mid"""
        ),
        {"status": mother_status, "result": _dumps(result), "mid": parent_id},
    )

    if commit:
        session.commit()

    return {
        "verdict_id": verdict_id,
        "mission_id": parent_id,
        "repro_mission_id": repro_mission_id,
        "verdict": verdict,
        "mother_status": mother_status,
        "mother_status_before": prev_status,
    }


# ───────────────────────── entry points ─────────────────────────

def on_mission_complete(
    session,
    mission_id: int,
    *,
    verdict: str | None = None,
    **verdict_kwargs: Any,
) -> dict[str, Any]:
    """Single hook for the mission-complete path.

    * non-repro mission  -> enqueue a repro child (idempotent).
    * repro mission      -> ``verdict`` is required; write the verdict back and
      update the mother mission.
    """
    row = session.execute(
        text("SELECT id, runner, status FROM tensor_dev_missions WHERE id = :mid"),
        {"mid": mission_id},
    ).mappings().first()
    if row is None:
        return {"action": "noop", "reason": "mission_not_found", "mission_id": mission_id}

    if row["runner"] == REPRO_RUNNER:
        if verdict is None:
            return {
                "action": "noop",
                "reason": "repro_mission_completed_without_verdict",
                "mission_id": mission_id,
            }
        receipt = record_repro_verdict(session, mission_id, verdict, **verdict_kwargs)
        receipt["action"] = "verdict"
        return receipt

    receipt = trigger_repro_for_mission(session, mission_id)
    if receipt is None:
        return {
            "action": "noop",
            "reason": "not_eligible_or_already_queued",
            "mission_id": mission_id,
        }
    receipt["action"] = "enqueued"
    return receipt


def complete_mission(
    session,
    mission_id: int,
    *,
    status: str = "succeeded",
    result: Mapping[str, Any] | None = None,
    commit: bool = True,
) -> dict[str, Any]:
    """Mark a mission complete and fire the repro trigger.

    This is the one-line integration point for the finish path: replace the
    bare ``UPDATE ... SET status='succeeded'`` with ``complete_mission(...)``.
    """
    params: dict[str, Any] = {"mid": mission_id, "status": status}
    sets = ["status = :status", "finished_at = CURRENT_TIMESTAMP"]
    if result is not None:
        sets.append(f"result = {_as_json_param(session, 'result')}")
        params["result"] = _dumps(dict(result))
    session.execute(
        text(f"UPDATE tensor_dev_missions SET {', '.join(sets)} WHERE id = :mid"),
        params,
    )
    if commit:
        session.commit()
    # Re-reads the row, so the status update above must be visible (same
    # connection/transaction) -- the trigger logic lives in exactly one place.
    return on_mission_complete(session, mission_id)


# ───────────────────────── cron fallback ─────────────────────────

def scan_completed_missions(
    session,
    *,
    limit: int = 200,
    commit: bool = True,
) -> list[dict[str, Any]]:
    """Cron fallback: enqueue repro children for succeeded missions lacking one.

    Intended to run every 5 minutes (``--scan`` CLI).  Idempotent: missions
    that already have a ``runner='repro'`` child are skipped.
    """
    rows = session.execute(
        text(
            """SELECT m.id
               FROM tensor_dev_missions m
               WHERE m.status = 'succeeded'
                 AND (m.runner IS NULL OR m.runner <> :runner)
                 AND NOT EXISTS (
                       SELECT 1 FROM tensor_dev_missions c
                       WHERE c.parent_mission_id = m.id AND c.runner = :runner
                 )
               ORDER BY m.id ASC
               LIMIT :limit"""
        ),
        {"runner": REPRO_RUNNER, "limit": limit},
    ).scalars().all()

    receipts: list[dict[str, Any]] = []
    for mid in rows:
        receipt = trigger_repro_for_mission(session, mid, commit=False)
        if receipt is not None:
            receipts.append(receipt)
    if receipts and commit:
        session.commit()
    return receipts


# ───────────────────────── schema bootstrap ─────────────────────────

_SCHEMA_SQL = (
    """CREATE TABLE IF NOT EXISTS tensor_dev_missions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        organization_id INTEGER NOT NULL,
        goal TEXT NOT NULL,
        repo TEXT NOT NULL,
        base_ref TEXT DEFAULT 'main',
        budget TEXT DEFAULT '{}',
        runner TEXT DEFAULT 'openhands',
        origin TEXT DEFAULT 'owner',
        model TEXT,
        parent_mission_id INTEGER,
        result TEXT DEFAULT '{}',
        status TEXT DEFAULT 'queued',
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        finished_at TIMESTAMP
    )""",
    """CREATE TABLE IF NOT EXISTS tensor_repro_verdicts (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        organization_id INTEGER NOT NULL,
        mission_id INTEGER NOT NULL,
        repro_model TEXT NOT NULL,
        verdict TEXT NOT NULL,
        claims_vs_recomputed TEXT DEFAULT '{}',
        evidence_paths TEXT DEFAULT '[]',
        wall_time_sec REAL,
        notes TEXT DEFAULT '',
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )""",
)


def ensure_schema(bind: Engine | Connection) -> None:
    """Create the minimal tables if absent (tests / fresh sqlite sandbox only).

    Production schema lives in backend/migrations/086 + 095; this bootstrap is
    deliberately a *subset* and is never run against PostgreSQL.
    """
    with bind.begin() as conn:
        for stmt in _SCHEMA_SQL:
            conn.execute(text(stmt))


# ───────────────────────── CLI (cron) ─────────────────────────

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Reproduction mission trigger")
    parser.add_argument("--dsn", default=os.environ.get("DATABASE_URL", ""),
                        help="SQLAlchemy DSN (default: $DATABASE_URL)")
    parser.add_argument("--scan", action="store_true",
                        help="cron mode: enqueue repro children for succeeded missions")
    parser.add_argument("--limit", type=int, default=200)
    parser.add_argument("--mission-id", type=int, default=None,
                        help="trigger a single mission complete")
    parser.add_argument("--verdict", default=None,
                        help="with --mission-id on a repro mission: record the verdict")
    args = parser.parse_args(argv)

    if not args.dsn:
        parser.error("--dsn or $DATABASE_URL is required")

    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    engine = create_engine(args.dsn, future=True)
    session = sessionmaker(bind=engine, future=True)()
    try:
        if args.scan:
            receipts = scan_completed_missions(session, limit=args.limit)
            print(json.dumps({"enqueued": len(receipts), "items": receipts},
                             ensure_ascii=False, default=str))
            return 0
        if args.mission_id is not None:
            receipt = on_mission_complete(session, args.mission_id, verdict=args.verdict)
            print(json.dumps(receipt, ensure_ascii=False, default=str))
            return 0
        parser.error("one of --scan / --mission-id is required")
    finally:
        session.close()
        engine.dispose()
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())