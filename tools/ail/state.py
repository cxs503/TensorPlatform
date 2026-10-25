"""SessionState 与 MemoryUpdate（SPEC §6）。"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .model import (AILSchemaError, PHASES, UPDATE_OPS, VERIFICATION_STATUSES,
                    _check_single_line, _check_utc, _require, checksum)


@dataclass
class SessionState:
    session_id: str
    agent_id: str | None
    mission_ref: dict
    phase: str
    status_summary: str
    active_plan: list[dict] = field(default_factory=list)
    next_actions: list[dict] = field(default_factory=list)
    blockers: list[dict] = field(default_factory=list)
    open_questions: list[dict] = field(default_factory=list)
    budget: dict = field(default_factory=dict)
    checkpoint_ref: dict | None = None
    revision: int = 1
    updated_at: str = ""

    @staticmethod
    def make(*, session_id: str, phase: str, status_summary: str, updated_at: str,
             agent_id: str | None = None, mission_ref: dict | None = None,
             active_plan: list[dict] | None = None, next_actions: list[dict] | None = None,
             blockers: list[dict] | None = None, open_questions: list[dict] | None = None,
             budget: dict | None = None, checkpoint_ref: dict | None = None,
             revision: int = 1) -> "SessionState":
        _check_single_line(session_id, "session_id", 80)
        _require(phase in PHASES, f"phase 非法：{phase!r}（须为 {PHASES}）")
        _check_single_line(status_summary, "status_summary", 400)
        _check_utc(updated_at, "updated_at")
        mission_ref = mission_ref or {"mission_id": None, "task_key": None}
        _require(set(mission_ref) == {"mission_id", "task_key"},
                 "mission_ref 字段必须是 mission_id/task_key")
        caps = {"active_plan": 12, "next_actions": 8, "blockers": 8, "open_questions": 8}
        lists = {"active_plan": active_plan or [], "next_actions": next_actions or [],
                 "blockers": blockers or [], "open_questions": open_questions or []}
        for name, items in lists.items():
            _require(len(items) <= caps[name], f"{name} 最多 {caps[name]} 项，实际 {len(items)}")
            for entry in items:
                _require(isinstance(entry, dict) and entry, f"{name} 元素必须是非空对象")
                for key, val in entry.items():
                    if isinstance(val, str):
                        _check_single_line(val, f"{name}.{key}", 300)
                    elif isinstance(val, list):
                        _require(len(val) <= 8, f"{name}.{key} 数组最多 8 项")
                        for v in val:
                            _check_single_line(v, f"{name}.{key}[]", 200)
                    elif val is not None:
                        raise AILSchemaError(f"{name}.{key} 只允许字符串/字符串数组/null")
        budget = budget or {"tool_calls_used": 0, "wall_seconds_used": 0, "token_budget": 0}
        _require(set(budget) == {"tool_calls_used", "wall_seconds_used", "token_budget"},
                 "budget 字段必须是 tool_calls_used/wall_seconds_used/token_budget")
        for k, v in budget.items():
            _require(isinstance(v, int) and v >= 0, f"budget.{k} 必须是非负整数")
        if checkpoint_ref is not None:
            _require(set(checkpoint_ref) == {"session_id", "version", "checksum"},
                     "checkpoint_ref 字段必须是 session_id/version/checksum")
        _require(isinstance(revision, int) and revision >= 1, "revision 必须是 >=1 整数")
        return SessionState(session_id=session_id, agent_id=agent_id, mission_ref=mission_ref,
                            phase=phase, status_summary=status_summary,
                            active_plan=list(lists["active_plan"]),
                            next_actions=list(lists["next_actions"]),
                            blockers=list(lists["blockers"]),
                            open_questions=list(lists["open_questions"]),
                            budget=budget, checkpoint_ref=checkpoint_ref,
                            revision=revision, updated_at=updated_at)

    def to_dict(self) -> dict:
        return {"session_id": self.session_id, "agent_id": self.agent_id,
                "mission_ref": dict(self.mission_ref), "phase": self.phase,
                "status_summary": self.status_summary,
                "active_plan": [dict(p) for p in self.active_plan],
                "next_actions": [dict(a) for a in self.next_actions],
                "blockers": [dict(b) for b in self.blockers],
                "open_questions": [dict(q) for q in self.open_questions],
                "budget": dict(self.budget), "checkpoint_ref": self.checkpoint_ref,
                "revision": self.revision, "updated_at": self.updated_at}

    @staticmethod
    def from_dict(payload: dict) -> "SessionState":
        expected = {"session_id", "agent_id", "mission_ref", "phase", "status_summary",
                    "active_plan", "next_actions", "blockers", "open_questions", "budget",
                    "checkpoint_ref", "revision", "updated_at"}
        _require(set(payload) == expected,
                 f"SessionState 字段集必须恰好为 {sorted(expected)}，实际 {sorted(payload)}")
        return SessionState.make(**payload)


@dataclass(frozen=True)
class MemoryUpdate:
    op: str
    at: str
    idempotency_key: str
    actor: str | None = None
    target_id: str | None = None
    successor_id: str | None = None
    reason: str | None = None
    item: dict | None = None
    evidence_id: str | None = None
    verification: dict | None = None

    @staticmethod
    def make(*, op: str, at: str, idempotency_key: str, **kwargs: Any) -> "MemoryUpdate":
        _require(op in UPDATE_OPS, f"MemoryUpdate.op 非法：{op!r}")
        _check_utc(at, "update.at")
        _check_single_line(idempotency_key, "update.idempotency_key", 200)
        if kwargs.get("reason") is not None:
            _check_single_line(kwargs["reason"], "update.reason", 300)
        required = {"add": ("item",), "supersede": ("target_id", "successor_id", "reason"),
                    "retire": ("target_id", "reason"), "attest": ("target_id", "evidence_id"),
                    "reverify": ("target_id", "verification"), "noop": ()}[op]
        for name in required:
            _require(kwargs.get(name) is not None, f"op={op} 必填 {name}")
        if op == "reverify":
            ver = kwargs["verification"]
            _require(isinstance(ver, dict) and set(ver) == {"status", "validator", "checked_at"},
                     "reverify.verification 字段必须是 status/validator/checked_at")
            _require(ver["status"] in VERIFICATION_STATUSES,
                     f"reverify.verification.status 非法：{ver['status']!r}")
        allowed = {"actor", "target_id", "successor_id", "reason", "item", "evidence_id",
                   "verification"}
        extra = set(kwargs) - allowed
        _require(not extra, f"MemoryUpdate 含未定义字段：{sorted(extra)}")
        return MemoryUpdate(op=op, at=at, idempotency_key=idempotency_key, **kwargs)

    def to_dict(self) -> dict:
        payload = {"op": self.op, "at": self.at, "idempotency_key": self.idempotency_key,
                   "actor": self.actor, "target_id": self.target_id,
                   "successor_id": self.successor_id, "reason": self.reason,
                   "item": self.item, "evidence_id": self.evidence_id,
                   "verification": self.verification}
        return payload

    @staticmethod
    def from_dict(payload: dict) -> "MemoryUpdate":
        expected = {"op", "at", "idempotency_key", "actor", "target_id", "successor_id",
                    "reason", "item", "evidence_id", "verification"}
        _require(set(payload) == expected,
                 f"MemoryUpdate 字段集必须恰好为 {sorted(expected)}，实际 {sorted(payload)}")
        kwargs = {k: v for k, v in payload.items()
                  if k not in ("op", "at", "idempotency_key")}
        return MemoryUpdate.make(op=payload["op"], at=payload["at"],
                                 idempotency_key=payload["idempotency_key"], **kwargs)

    def digest(self) -> str:
        return checksum(self.to_dict())[:16]