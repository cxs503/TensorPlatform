"""Effect adapters — *where* a recovery decision actually lands.

Two implementations:

``DryRunExecutor``
    Default everywhere (CLI, simulation, API default).  Performs no writes and
    reports ``applied`` / ``deferred`` based on a caller-supplied availability
    map.  This is what makes the pipeline testable offline.

``SqlMissionExecutor``
    Production adapter for ``tensor_dev_missions``.  It really mutates rows:
    ``archive`` retires a redundant mission, ``reschedule`` puts it back in the
    queue and clears the claim, ``extend_timeout`` / ``degrade_resources`` patch
    the mission's ``budget`` JSON.  ``escalate`` / ``manual_review`` add a
    ``self_heal`` note to ``result`` so a downstream consumer can pick it up —
    they deliberately do not change the mission status (a human decides).

Neither adapter invents platform semantics: they only use columns that already
exist.  Every attempted write is described in the returned ``detail`` so the
ledger entry can be audited without replaying the SQL.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, Mapping, Optional, Protocol, runtime_checkable

from .model import MissionRow
from .recovery import RecoveryPlan

UTC = timezone.utc

OUTCOME_APPLIED = "applied"
OUTCOME_DEFERRED = "deferred"
OUTCOME_ESCALATED = "escalated"
OUTCOME_ARCHIVED = "archived"
OUTCOME_FAILED = "failed"
OUTCOME_SKIPPED = "skipped"

#: Outcomes that count as an accepted recovery action for recoverable causes.
ACCEPTED_OUTCOMES = frozenset({OUTCOME_APPLIED, OUTCOME_ARCHIVED})


@dataclass
class ActionOutcome:
    status: str
    detail: Dict[str, Any] = field(default_factory=dict)

    @property
    def accepted(self) -> bool:
        return self.status in ACCEPTED_OUTCOMES

    def as_dict(self) -> Dict[str, Any]:
        return {"status": self.status, "detail": self.detail}


@runtime_checkable
class Executor(Protocol):
    def execute(self, plan: RecoveryPlan, row: MissionRow, context: Mapping[str, Any]) -> ActionOutcome:
        ...


class DryRunExecutor:
    """No-op executor; ``context['availability']`` may veto a re-dispatch."""

    def __init__(self, availability: Optional[Mapping[int, bool]] = None):
        self.availability = dict(availability or {})

    def execute(self, plan: RecoveryPlan, row: MissionRow, context: Mapping[str, Any]) -> ActionOutcome:
        availability = {**self.availability, **dict(context.get("availability") or {})}
        allowed = availability.get(row.id, True)
        if plan.action == "archive":
            return ActionOutcome(OUTCOME_ARCHIVED, {"dry_run": True, "mission_id": row.id})
        if plan.action in ("escalate", "manual_review"):
            return ActionOutcome(
                OUTCOME_ESCALATED,
                {"dry_run": True, "target": plan.params.get("target", "manual_review")},
            )
        if not allowed:
            return ActionOutcome(
                OUTCOME_DEFERRED,
                {"dry_run": True, "reason": "no_dispatcher_capacity", "mission_id": row.id},
            )
        return ActionOutcome(OUTCOME_APPLIED, {"dry_run": True, "mission_id": row.id})


class SqlMissionExecutor:
    """Real adapter for ``tensor_dev_missions`` (SQLite and PostgreSQL)."""

    def __init__(self, db, now: Optional[float] = None):
        self.db = db
        self.now = now

    # -- helpers ---------------------------------------------------------
    def _dialect(self) -> str:
        try:
            return self.db.bind.dialect.name  # type: ignore[attr-defined]
        except Exception:  # pragma: no cover - defensive
            return "sqlite"

    def _write_json(self, mission_id: int, mutate) -> Dict[str, Any]:
        from sqlalchemy import text

        row = self.db.execute(
            text("SELECT result, budget FROM tensor_dev_missions WHERE id = :id"), {"id": mission_id}
        ).mappings().first()
        if row is None:
            raise LookupError(f"mission {mission_id} not found")
        result = _as_json(row["result"])
        budget = _as_json(row["budget"])
        mutate(result, budget)
        result_param = ":result" if self._dialect() == "sqlite" else "CAST(:result AS jsonb)"
        budget_param = ":budget" if self._dialect() == "sqlite" else "CAST(:budget AS jsonb)"
        self.db.execute(
            text(f"UPDATE tensor_dev_missions SET result = {result_param}, budget = {budget_param} WHERE id = :id"),
            {"id": mission_id, "result": json.dumps(result), "budget": json.dumps(budget)},
        )
        return {"result": result, "budget": budget}

    def _retire(self, mission_id: int, now_iso: str) -> int:
        """Retire a row using a status that exists in the mission vocabulary.

        ``contracts/mission_status_vocab.v1.json`` has no ``archived`` state, so
        retirement is ``status='stopped'`` + ``result.archived=true``.
        """
        res = self.db.execute(
            _text("UPDATE tensor_dev_missions SET status = 'stopped', finished_at = :now WHERE id = :id"),
            {"id": mission_id, "now": now_iso},
        )
        return int(getattr(res, "rowcount", 0) or 0)

    # -- protocol --------------------------------------------------------
    def execute(self, plan: RecoveryPlan, row: MissionRow, context: Mapping[str, Any]) -> ActionOutcome:
        now_iso = datetime.fromtimestamp(self.now or datetime.now(UTC).timestamp(), UTC).isoformat()
        note = {
            "action": plan.action,
            "cause": plan.cause,
            "attempt": plan.attempt,
            "window_id": context.get("window_id"),
            "params": plan.params,
            "at": now_iso,
        }
        try:
            if plan.action == "archive":
                changed = self._retire(row.id, now_iso)
                if not changed:
                    return ActionOutcome(OUTCOME_FAILED, {"reason": "row_not_updated"})

                def _mark(result, budget):
                    result["archived"] = True
                    result["archived_reason"] = "duplicate_seed"
                    result.setdefault("self_heal", []).append(note)

                self._write_json(row.id, _mark)
                return ActionOutcome(
                    OUTCOME_ARCHIVED,
                    {
                        "mission_id": row.id,
                        "status": "stopped",
                        "marker": "result.archived=true",
                        "note": note,
                    },
                )

            if plan.action == "reschedule":
                res = self.db.execute(
                    _text(
                        "UPDATE tensor_dev_missions SET status = 'queued', claimed_by = NULL, "
                        "claim_token = NULL, heartbeat_at = NULL, finished_at = NULL WHERE id = :id"
                    ),
                    {"id": row.id},
                )
                if not int(getattr(res, "rowcount", 0) or 0):
                    return ActionOutcome(OUTCOME_FAILED, {"reason": "row_not_updated"})
                self._write_json(row.id, lambda result, budget: result.setdefault("self_heal", []).append(note))
                return ActionOutcome(OUTCOME_APPLIED, {"mission_id": row.id, "status": "queued", "note": note})

            if plan.action == "extend_timeout":
                new_limit = plan.params.get("new_max_wall_seconds")

                def _extend(result, budget):
                    if new_limit is not None:
                        budget["max_wall_seconds"] = new_limit
                    result.setdefault("self_heal", []).append(note)

                self._write_json(row.id, _extend)
                return ActionOutcome(OUTCOME_APPLIED, {"mission_id": row.id, "new_max_wall_seconds": new_limit})

            if plan.action == "degrade_resources":
                new_cores = plan.params.get("new_cores")

                def _degrade(result, budget):
                    if new_cores is not None:
                        for key in ("cores", "max_cores"):
                            if key in budget:
                                budget[key] = new_cores
                                break
                        else:
                            budget["cores"] = new_cores
                    result.setdefault("self_heal", []).append(note)

                self._write_json(row.id, _degrade)
                return ActionOutcome(OUTCOME_APPLIED, {"mission_id": row.id, "new_cores": new_cores})

            # escalate / manual_review: annotate, never mutate status
            self._write_json(row.id, lambda result, budget: result.setdefault("self_heal", []).append(note))
            return ActionOutcome(
                OUTCOME_ESCALATED,
                {"mission_id": row.id, "target": plan.params.get("target", "manual_review"), "note": note},
            )
        except LookupError as exc:
            return ActionOutcome(OUTCOME_FAILED, {"reason": str(exc)})
        except Exception as exc:  # pragma: no cover - surfaced in ledger detail
            return ActionOutcome(OUTCOME_FAILED, {"reason": f"{type(exc).__name__}: {exc}"})


def _text(sql: str):
    from sqlalchemy import text

    return text(sql)


def _as_json(value: Any) -> Dict[str, Any]:
    if isinstance(value, dict):
        return dict(value)
    if isinstance(value, (str, bytes)) and value:
        try:
            parsed = json.loads(value)
        except (TypeError, ValueError):
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}