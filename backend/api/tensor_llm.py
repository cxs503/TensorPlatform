"""Tensor console: LLM analysis endpoints (self-deployed DeepSeek via internal relay).

GET  /tensor/llm/status                      — config + live model-catalog probe
POST /tensor/llm/analyze/execution/{id}      — evidence-grounded run/execution reading,
                                               appended to the session audit trail as a
                                               `note` event (append-only, source=llm).
"""
from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import text
from sqlalchemy.orm import Session

from api.deps import require_auth
from api.domain.tensor_common import append_event, resolved_org
from database.config import get_db
from services.llm import llm_chat, llm_probe

router = APIRouter(prefix="/tensor/llm", tags=["AI 分析"])

SYSTEM_PROMPT = (
    "你是科研运行台（Tensor 家族多求解器联合调试平台）的 AI 分析员。"
    "你收到的是一次场景执行的实测数据：执行状态、预算、智能体会话事件、"
    "已注册 Run 的校验与指标。请只依据这些数据作答，禁止编造任何数字或结论；"
    "数据里没有的信息就明说缺失。用简体中文，先给一句话结论，再分 2-4 个要点："
    "(1) 本次执行的健康度（校验门通过情况）；(2) 智能体行为链是否完整自洽；"
    "(3) 值得注意的风险或下一步建议。总长控制在 300 字以内。"
)


@router.get("/status", dependencies=[Depends(require_auth)])
def llm_status():
    return llm_probe()


def _fetch_context(db: Session, execution_id: int, org_id: int) -> dict[str, Any]:
    execs = db.execute(
        text("""
            SELECT e.id, e.execution_key, e.scenario_key, e.stage, e.status,
                   e.budget, e.started_at, e.finished_at, e.heartbeat_at,
                   e.registered_run_ids, e.result_summary, e.log_tail,
                   s.id AS session_id
            FROM tensor_executions e
            LEFT JOIN tensor_agent_sessions s ON s.execution_id = e.id
            WHERE e.id = :eid AND e.organization_id = :org
            ORDER BY s.id DESC LIMIT 1
        """),
        {"eid": execution_id, "org": org_id},
    ).mappings().first()
    if not execs:
        raise HTTPException(status_code=404, detail="execution not found")

    events = (
        db.execute(
            text("""
                SELECT seq, kind, payload, created_at
                FROM tensor_agent_session_events
                WHERE session_id = :sid
                ORDER BY seq DESC LIMIT 60
            """),
            {"sid": execs["session_id"]},
        ).mappings().all()
        if execs["session_id"]
        else []
    )

    runs = []
    for run_id in execs["registered_run_ids"] or []:
        row = db.execute(
            text("""
                SELECT run_id, status, validation_status,
                       NULLIF(manifest #>> '{metrics,checks_passed}', '')::int AS checks_passed,
                       NULLIF(manifest #>> '{metrics,checks_total}', '')::int AS checks_total,
                       manifest #>> '{scientific_status}' AS scientific_status
                FROM tensorphysics_runs
                WHERE run_id = :rid AND organization_id = :org
            """),
            {"rid": run_id, "org": org_id},
        ).mappings().first()
        if row:
            runs.append(dict(row))

    return {
        "execution": {
            "id": execs["id"],
            "session_id": execs["session_id"],
            "execution_key": execs["execution_key"],
            "scenario_key": execs["scenario_key"],
            "stage": execs["stage"],
            "status": execs["status"],
            "budget": execs["budget"],
            "started_at": execs["started_at"],
            "finished_at": execs["finished_at"],
            "result_summary": execs["result_summary"],
        },
        "registered_runs": runs,
        "session_events": [
            {"seq": e["seq"], "kind": e["kind"], "payload": e["payload"],
             "at": e["created_at"]}
            for e in reversed(events)
        ],
        "log_tail": (execs["log_tail"] or "")[-2000:],
    }


@router.post("/analyze/execution/{execution_id}", dependencies=[Depends(require_auth)])
def analyze_execution(
    execution_id: int,
    db: Session = Depends(get_db),
    user: dict = Depends(require_auth),
):
    org_id = resolved_org(None, user, db)
    context = _fetch_context(db, execution_id, org_id)

    try:
        result = llm_chat(
            [
                {"role": "system", "content": SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": "实测数据如下（JSON）：\n"
                    + json.dumps(context, ensure_ascii=False, default=str),
                },
            ],
        )
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    session_seq = None
    if context["execution"]["session_id"]:
        session_seq = append_event(
            db,
            context["execution"]["session_id"],
            "note",
            {
                "source": "llm",
                "model": result["model"],
                "content": result["content"],
                "usage": result["usage"],
            },
        )
        db.commit()

    return {
        "execution_id": execution_id,
        "model": result["model"],
        "content": result["content"],
        "usage": result["usage"],
        "latency_ms": result["latency_ms"],
        "session_seq": session_seq,
    }
