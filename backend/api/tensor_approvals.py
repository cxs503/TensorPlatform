"""Tensor console: human-in-the-loop approvals.

Kinds: stage_promotion / stop_condition (scenario ladder) and
dev_acceptance (mission acceptance). Decide is strictly ADMIN — the agent
never approves its own work.
"""
from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.orm import Session

from api.deps import require_auth, require_role
from api.domain.tensor_common import STAGES, resolved_org
from database.config import get_db

router = APIRouter(prefix="/tensor/approvals", tags=["科研审批"])


def _row(r: Any) -> dict[str, Any]:
    return {
        "id": r["id"],
        "scenario_id": r["scenario_id"],
        "scenario_key": r["scenario_key"],
        "mission_id": r["mission_id"],
        "mission_goal": r["mission_goal"],
        "kind": r["kind"],
        "from_stage": r["from_stage"],
        "to_stage": r["to_stage"],
        "reason": r["reason"],
        "evidence": r["evidence"] or {},
        "status": r["status"],
        "requested_by": r["requested_by"],
        "decided_by": r["decided_by"],
        "decided_at": r["decided_at"].isoformat() if r.get("decided_at") else None,
        "decision_note": r["decision_note"],
        "created_at": r["created_at"].isoformat() if r.get("created_at") else None,
    }


_SELECT = """
    SELECT a.id, a.scenario_id, s.scenario_key, a.mission_id, m.goal AS mission_goal,
           a.kind, a.from_stage, a.to_stage,
           a.reason, a.evidence, a.status, a.requested_by, a.decided_by,
           a.decided_at, a.decision_note, a.created_at
    FROM tensor_approval_requests a
    LEFT JOIN tensor_scenarios s ON s.id = a.scenario_id
    LEFT JOIN tensor_dev_missions m ON m.id = a.mission_id
"""


@router.get("", dependencies=[Depends(require_auth)])
def list_approvals(
    status: str | None = None,
    mission_id: int | None = None,
    limit: int = 50,
    db: Session = Depends(get_db),
    user: dict = Depends(require_auth),
):
    org_id = resolved_org(None, user, db)
    limit = max(1, min(limit, 200))
    filters = "a.organization_id = :org"
    params: dict[str, Any] = {"org": org_id}
    if status:
        filters += " AND a.status = :st"
        params["st"] = status
    if mission_id:
        filters += " AND a.mission_id = :mid"
        params["mid"] = mission_id
    rows = db.execute(
        text(f"{_SELECT} WHERE {filters} ORDER BY a.id DESC LIMIT :limit"),
        {**params, "limit": limit},
    ).mappings().all()
    return {"items": [_row(r) for r in rows], "total": len(rows)}


class ApprovalCreate(BaseModel):
    scenario_key: str = Field(min_length=2, max_length=120)
    kind: str = Field(pattern="^(stage_promotion|stop_condition)$")
    from_stage: str | None = Field(default=None, pattern=r"^S[0-4]$")
    to_stage: str | None = Field(default=None, pattern=r"^S[0-4]$")
    reason: str = Field(min_length=1, max_length=2000)
    evidence: dict[str, Any] = Field(default_factory=dict)
    organization_id: int | None = Field(default=None, ge=1)


@router.post("", dependencies=[Depends(require_role("ADMIN", "MANAGER", "ENGINEER"))])
def create_approval(
    body: ApprovalCreate,
    db: Session = Depends(get_db),
    user: dict = Depends(require_auth),
):
    org_id = resolved_org(body.organization_id, user, db)
    scenario = db.execute(
        text("SELECT id, current_stage FROM tensor_scenarios WHERE organization_id=:org AND scenario_key=:key"),
        {"org": org_id, "key": body.scenario_key},
    ).mappings().first()
    if not scenario:
        raise HTTPException(status_code=404, detail="scenario not found")
    if body.kind == "stage_promotion":
        if not body.to_stage or body.to_stage not in STAGES:
            raise HTTPException(status_code=422, detail="stage_promotion requires to_stage")
        from_stage = body.from_stage or scenario["current_stage"]
        if STAGES.index(body.to_stage) != STAGES.index(from_stage) + 1:
            raise HTTPException(status_code=422, detail="promotion must advance exactly one stage")
    else:
        from_stage = body.from_stage or scenario["current_stage"]

    import json as _json

    existing = db.execute(
        text("""
            SELECT id FROM tensor_approval_requests
            WHERE scenario_id = :sid AND kind = :kind AND status = 'pending'
        """),
        {"sid": scenario["id"], "kind": body.kind},
    ).scalar()
    if existing:
        return {"id": existing, "status": "pending", "deduplicated": True}

    new_id = db.execute(
        text("""
            INSERT INTO tensor_approval_requests
                (organization_id, scenario_id, kind, from_stage, to_stage, reason,
                 evidence, status, requested_by)
            VALUES (:org, :sid, :kind, :fstage, :tstage, :reason,
                    CAST(:evidence AS jsonb), 'pending', :reqby)
            RETURNING id
        """),
        {"org": org_id, "sid": scenario["id"], "kind": body.kind,
         "fstage": from_stage, "tstage": body.to_stage, "reason": body.reason,
         "evidence": _json.dumps(body.evidence, ensure_ascii=False, default=str),
         "reqby": user.get("username")},
    ).scalar()
    db.commit()
    return {"id": new_id, "status": "pending", "deduplicated": False}


class ApprovalDecision(BaseModel):
    decision: str = Field(pattern="^(approved|rejected)$")
    note: str | None = Field(default=None, max_length=2000)


# AOG SPEC v1.0 §7 六个 Capability Delta 类别（合法枚举；无实质增量需说明原因）
DELTA_CATEGORIES = frozenset({
    "new_capability",        # 新增能力
    "capability_extension",  # 既有能力扩展或性能/可靠性改善
    "defect_fix",            # 缺陷修复和风险降低
    "reusable_knowledge",    # 可复用知识或工具
    "falsified_hypothesis",  # 被否定的假设（负面结果）
    "no_substantive_delta",  # 无实质增量（reason 必填）
    "bench_verified",        # 实证基准通过（TensorBench 平台复算数字，非自述）
})


def _declared_capability_delta(mission_result: Any) -> dict[str, Any] | None:
    """Extract the agent-declared capability delta from a mission result blob.

    Returns None when the agent did not declare one (legal — the decide hook
    then books an honest no_substantive_delta row instead of fabricating).
    Raises 422 for a declaration that is present but malformed: the decision
    rolls back and the mission stays awaiting_acceptance (R1: gate can fail).
    """
    blob: Any = mission_result
    if isinstance(blob, str):
        try:
            blob = json.loads(blob) if blob.strip() else {}
        except ValueError:
            return None  # unparseable legacy result: treat as undeclared
    if not isinstance(blob, dict):
        return None
    declared = blob.get("capability_delta")
    if declared is None or declared == {}:
        return None
    if not isinstance(declared, dict):
        raise HTTPException(status_code=422, detail="capability_delta must be an object")
    category = declared.get("category")
    if category not in DELTA_CATEGORIES:
        raise HTTPException(
            status_code=422,
            detail=f"capability_delta.category must be one of {sorted(DELTA_CATEGORIES)}",
        )
    summary = declared.get("summary")
    if not isinstance(summary, str) or not summary.strip():
        raise HTTPException(status_code=422, detail="capability_delta.summary is required")
    reason = declared.get("reason")
    if category == "no_substantive_delta" and (not isinstance(reason, str) or not reason.strip()):
        raise HTTPException(status_code=422,
                            detail="capability_delta.reason is required for no_substantive_delta")
    artifacts = declared.get("artifacts", [])
    if not isinstance(artifacts, list) or not all(isinstance(a, str) for a in artifacts):
        raise HTTPException(status_code=422, detail="capability_delta.artifacts must be a list of paths")
    evidence = declared.get("evidence", {})
    if not isinstance(evidence, dict):
        raise HTTPException(status_code=422, detail="capability_delta.evidence must be an object")
    card_id = declared.get("card_id")
    if card_id is not None and not isinstance(card_id, int):
        raise HTTPException(status_code=422, detail="capability_delta.card_id must be an integer")
    return {"category": category, "summary": summary.strip(), "reason": reason if isinstance(reason, str) else None,
            "artifacts": artifacts, "evidence": evidence, "card_id": card_id}


@router.post("/{approval_id}/decide", dependencies=[Depends(require_role("ADMIN"))])
def decide_approval(
    approval_id: int,
    body: ApprovalDecision,
    db: Session = Depends(get_db),
    user: dict = Depends(require_auth),
):
    row = db.execute(
        text("""
            SELECT a.id, a.organization_id, a.scenario_id, a.mission_id, a.kind, a.to_stage, a.status,
                   s.scenario_key, m.session_id AS mission_session_id, m.result AS mission_result
            FROM tensor_approval_requests a
            LEFT JOIN tensor_scenarios s ON s.id = a.scenario_id
            LEFT JOIN tensor_dev_missions m ON m.id = a.mission_id
            WHERE a.id = :aid
            FOR UPDATE OF a
        """),
        {"aid": approval_id},
    ).mappings().first()
    if not row or int(row["organization_id"]) != resolved_org(None, user, db):
        raise HTTPException(status_code=404, detail="approval not found")
    if row["status"] != "pending":
        raise HTTPException(status_code=409, detail=f"approval already {row['status']}")

    if row["kind"] == "dev_acceptance":
        # mission acceptance: approve -> succeeded, reject -> failed; the mission
        # session closes with the decision event (append-only audit chain).
        mission = db.execute(text("SELECT status FROM tensor_dev_missions WHERE id=:mid FOR UPDATE"), {"mid": row["mission_id"]}).scalar()
        if mission != "awaiting_acceptance":
            raise HTTPException(status_code=409, detail="mission is no longer awaiting acceptance")
        mission_status = "succeeded" if body.decision == "approved" else "failed"
        db.execute(
            text("""
                UPDATE tensor_dev_missions
                SET status = :st, finished_at = now(),
                    result = result || CAST(:patch AS jsonb)
                WHERE id = :mid
            """),
            {"st": mission_status, "mid": row["mission_id"],
             "patch": json.dumps({
                 "accepted": body.decision == "approved",
                 "decided_by": user.get("username"),
                 "decision_note": body.note,
             }, ensure_ascii=False)},
        )
        if row["mission_session_id"]:
            from api.domain.tensor_common import append_event

            append_event(db, row["mission_session_id"], "approval_decided", {
                "approval_id": approval_id, "kind": "dev_acceptance",
                "decision": body.decision, "note": body.note,
                "mission_status": mission_status,
            })
            db.execute(
                text("""
                    UPDATE tensor_agent_sessions
                    SET status = :st, closed_at = now() WHERE id = :sid
                """),
                {"st": "completed" if body.decision == "approved" else "failed",
                 "sid": row["mission_session_id"]},
            )
        if body.decision == "approved":
            # SPEC §7 C-loop: a succeeded mission books exactly one capability
            # delta row — agent-declared if provided, else an honest
            # no_substantive_delta (never a fabricated one). Unique index on
            # mission_id keeps replays idempotent.
            declared = _declared_capability_delta(row["mission_result"])
            delta = declared or {"category": "no_substantive_delta",
                                 "summary": "mission accepted without a declared capability delta",
                                 "reason": "capability_delta was not declared at complete",
                                 "artifacts": [], "evidence": {}, "card_id": None}
            # sqlite (tests) has no jsonb cast — plain bind keeps the JSON text;
            # postgres needs the explicit cast into the JSONB columns.
            cast = "CAST(:col AS jsonb)" if (db.bind and db.bind.dialect.name == "postgresql") else ":col"
            db.execute(
                text(f"""
                    INSERT INTO tensor_capability_deltas
                        (organization_id, mission_id, category, summary, reason,
                         artifacts, evidence, card_id, reviewer)
                    VALUES (:org, :mid, :cat, :summary, :reason,
                            {cast.replace(':col', ':artifacts')}, {cast.replace(':col', ':evidence')},
                            :card_id, :reviewer)
                    ON CONFLICT (mission_id) DO NOTHING
                """),
                {"org": row["organization_id"], "mid": row["mission_id"],
                 "cat": delta["category"], "summary": delta["summary"],
                 "reason": delta["reason"],
                 "artifacts": json.dumps(delta["artifacts"]),
                 "evidence": json.dumps(delta["evidence"]),
                 "card_id": delta["card_id"], "reviewer": user.get("username")},
            )
    elif body.decision == "approved" and row["kind"] == "stage_promotion":
        # promotion commits together with the decision (single transaction);
        # the new stage opens as pending — its completion evidence comes
        # from registered runs, not from the approval itself.
        db.execute(
            text("""
                UPDATE tensor_scenarios
                SET current_stage = :stage,
                    stage_status = stage_status || jsonb_build_object(
                        :stage,
                        COALESCE(stage_status -> :stage, '{}'::jsonb)
                          || jsonb_build_object('state', 'pending', 'approved_at', now())
                    ),
                    updated_at = now()
                WHERE id = :sid
            """),
            {"stage": row["to_stage"], "sid": row["scenario_id"]},
        )
    db.execute(
        text("""
            UPDATE tensor_approval_requests
            SET status = :decision, decided_by = :uid, decided_at = now(),
                decision_note = :note
            WHERE id = :aid
        """),
        {"decision": body.decision, "uid": user.get("id"), "note": body.note, "aid": approval_id},
    )
    db.commit()
    return {
        "id": approval_id,
        "status": body.decision,
        "scenario_key": row["scenario_key"],
        "mission_id": row["mission_id"],
        "mission_status": ("succeeded" if body.decision == "approved" else "failed")
                          if row["kind"] == "dev_acceptance" else None,
        "promoted_to": row["to_stage"] if (body.decision == "approved" and row["kind"] == "stage_promotion") else None,
    }
