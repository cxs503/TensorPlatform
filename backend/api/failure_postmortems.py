#!/usr/bin/env python3
"""失败事后复盘 API（failure postmortems）——表 + 收割 + 挑战/定稿 + 规则检索。

端点（prefix ``/tensor/failure-postmortems``）：

- ``GET  /``                   复盘流水（按 mission/stage/status 过滤）
- ``GET  /stats``              七环/五类分布 + 验收口径计数（可复算）
- ``GET  /rules``              规则检索（词面+同环同类；带 mission_id 且 cite=1 时登记引用）
- ``GET  /playbook``           渲染 append-only PLAYBOOK 注入块 + 注入回执（默认 dry-run）
- ``GET  /mission/{id}``       单条 mission 的复盘
- ``POST /harvest``            从近 N 天 failed mission 收割（admin/manager）
- ``POST /{postmortem_id}/challenge``  重跑双闸（结构挑战+独立复算）
- ``POST /{postmortem_id}/confirm``    定稿（只允许 challenger/coordinator，且非起草者）
- ``POST /{postmortem_id}/reject``     驳回（必须写理由）

纪律：本模块不做领域判断；七环/五类枚举与判据全部来自
``tools.failure_postmortems``（单一定义）。定稿的机器挑战者显式标注
``(machine)``，不冒充人工同行评审。
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.orm import Session

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from api.deps import require_auth  # noqa: E402
from api.tenant_access import is_admin  # noqa: E402
from database.config import get_db  # noqa: E402
from tools import failure_postmortems as fpm  # noqa: E402
from tools.harvest_failure_postmortems import (  # noqa: E402
    CHALLENGER_ID,
    Repo,
    harvest,
)

router = APIRouter(prefix="/tensor/failure-postmortems", tags=["失败复盘"])

#: bootstrap PLAYBOOK 目标（平台仓外文件，只读校验 dry-run；写入由 owner/协调者执行）。
DEFAULT_BOOTSTRAP = os.getenv("TP_BOOTSTRAP_PATH", "/root/tp-agent/AGENT_BOOTSTRAP.md")


def _rows(repo: Repo) -> list[dict[str, Any]]:
    return repo.postmortems()


def _repo(db: Session) -> Repo:
    return Repo(db.get_bind())


def _selected(repo: Repo, postmortem_id: str) -> dict[str, Any]:
    for row in repo.postmortems():
        if row["postmortem_id"] == postmortem_id:
            return row
    raise HTTPException(404, f"postmortem {postmortem_id} not found")


# ─────────────────────────────── 读面 ───────────────────────────────

@router.get("", dependencies=[Depends(require_auth)])
def list_postmortems(
    status: str | None = Query(default=None, description="draft/disputed/confirmed/rejected"),
    failure_stage: str | None = Query(default=None, description="七环之一"),
    error_class: str | None = Query(default=None, description="五类之一"),
    mission_id: int | None = None,
    limit: int = Query(default=100, ge=1, le=500),
    db: Session = Depends(get_db),
):
    if status and status not in fpm.STATUSES:
        raise HTTPException(422, f"status 非法：{status}")
    if failure_stage and failure_stage not in fpm.STAGES:
        raise HTTPException(422, f"failure_stage 非法：{failure_stage}")
    if error_class and error_class not in fpm.ERROR_CLASSES:
        raise HTTPException(422, f"error_class 非法：{error_class}")
    rows = _rows(_repo(db))
    if status:
        rows = [r for r in rows if r["status"] == status]
    if failure_stage:
        rows = [r for r in rows if r["failure_stage"] == failure_stage]
    if error_class:
        rows = [r for r in rows if r["error_class"] == error_class]
    if mission_id is not None:
        rows = [r for r in rows if int(r["mission_id"]) == int(mission_id)]
    return {"total": len(rows), "items": rows[:limit], "stages": list(fpm.STAGES),
            "error_classes": list(fpm.ERROR_CLASSES)}


@router.get("/stats", dependencies=[Depends(require_auth)])
def stats(db: Session = Depends(get_db)):
    repo = _repo(db)
    return fpm.summarize(repo.postmortems(), repo.rules(only_confirmed=False), repo.references())


@router.get("/mission/{mission_id}", dependencies=[Depends(require_auth)])
def get_by_mission(mission_id: int, db: Session = Depends(get_db)):
    repo = _repo(db)
    for row in repo.postmortems():
        if int(row["mission_id"]) == int(mission_id):
            return row
    raise HTTPException(404, f"mission {mission_id} 无 postmortem")


@router.get("/rules", dependencies=[Depends(require_auth)])
def search_rules(
    q: str = Query(default="", description="自由文本（goal/失败原因/关键词）"),
    mission_id: int | None = Query(default=None, description="引用主体 mission（登记引用）"),
    failure_stage: str | None = None,
    error_class: str | None = None,
    top_k: int = Query(default=5, ge=1, le=20),
    cite: bool = Query(default=True, description="带 mission_id 时是否登记 claim_injection 引用"),
    db: Session = Depends(get_db),
):
    repo = _repo(db)
    rules = repo.rules(only_confirmed=True)
    hits = fpm.retrieve_rules(
        q, rules, stage=failure_stage, klass=error_class, top_k=top_k, min_score=0.2
    )
    recorded: list[str] = []
    if mission_id is not None and cite:
        for hit in hits:
            if repo.insert_reference(
                fpm.reference_row(
                    str(hit["rule_id"]),
                    referencing_mission_id=int(mission_id),
                    kind="claim_injection",
                    evidence=f"mission {mission_id} 检索 q={q[:80]!r} 命中（score={hit['score']}）",
                )
            ):
                recorded.append(str(hit["rule_id"]))
    return {"query": q, "hits": hits, "references_recorded": recorded,
            "enumerations": {"stages": list(fpm.STAGES),
                             "error_classes": list(fpm.ERROR_CLASSES)}}


@router.get("/playbook", dependencies=[Depends(require_auth)])
def playbook(
    target: str | None = Query(default=None, description="bootstrap 文件路径（默认 TP_BOOTSTRAP_PATH）"),
    apply: bool = Query(default=False, description="true=真写入（仅 owner/协调者手工启用）"),
    db: Session = Depends(get_db),
):
    repo = _repo(db)
    rules = repo.rules(only_confirmed=True)
    path = Path(target or DEFAULT_BOOTSTRAP)
    payload: dict[str, Any] = {
        "rules": len(rules),
        "section_anchor": fpm.PLAYBOOK_SECTION_ANCHOR,
        "lines": fpm.render_playbook_lines(rules),
    }
    try:
        payload["receipt"] = fpm.inject_into_playbook(path, rules, apply=apply)
    except Exception as exc:  # 目标不可读/锚点缺失：如实降级，不 500
        payload["receipt"] = {"error": f"{type(exc).__name__}: {exc}", "target": str(path)}
    return payload


# ─────────────────────────────── 写面 ───────────────────────────────

class HarvestBody(BaseModel):
    days: int = Field(default=7, ge=1, le=30)
    limit: int = Field(default=40, ge=1, le=500)
    mission_ids: list[int] | None = None
    apply: bool = True
    confirm: bool = True
    harvester: str = Field(default="harvester:api@1.0", max_length=120)
    challenger: str = Field(default=CHALLENGER_ID, max_length=120)


@router.post("/harvest", dependencies=[Depends(require_auth)])
def run_harvest(body: HarvestBody | None = None, db: Session = Depends(get_db),
                user: dict = Depends(require_auth)):
    if not is_admin(user, db):
        raise HTTPException(403, "只有 admin/manager 可触发收割")
    body = body or HarvestBody()
    repo = _repo(db)
    repo.ensure_schema()
    if body.apply and body.harvester == body.challenger:
        raise HTTPException(422, "收割者与定稿者不得为同一主体")
    report = harvest(
        repo,
        days=body.days,
        limit=body.limit,
        apply=body.apply,
        confirm=body.confirm,
        harvester=body.harvester,
        challenger=body.challenger,
        mission_ids=body.mission_ids,
    )
    return {
        "harvested": report.harvested,
        "confirmed": report.confirmed,
        "disputed": report.disputed,
        "draft_pending": report.draft_pending,
        "withheld": report.withheld,
        "skipped_existing": report.skipped_existing,
        "references_new": report.references_new,
        "drafts": report.drafts,
        "objections": report.objections,
    }


@router.post("/{postmortem_id}/challenge", dependencies=[Depends(require_auth)])
def challenge(postmortem_id: str, db: Session = Depends(get_db)):
    repo = _repo(db)
    row = _selected(repo, postmortem_id)
    verdict = fpm.challenge_draft(row)
    mission = repo.mission(int(row["mission_id"]))
    review = fpm.independent_review(mission, row) if mission else fpm.ChallengeVerdict(
        False, ("mission 行不存在，无法独立复算",)
    )
    combined = fpm.ChallengeVerdict(
        ok=verdict.ok and review.ok, objections=verdict.objections + review.objections
    )
    return {"postmortem_id": postmortem_id, "structure": verdict.as_dict(),
            "independent_review": review.as_dict(), "ok": combined.ok}


class ConfirmBody(BaseModel):
    reviewer: str = Field(min_length=3, max_length=120)
    role: str = Field(description="challenger/coordinator")
    override_reason: str | None = Field(default=None, max_length=500)


@router.post("/{postmortem_id}/confirm", dependencies=[Depends(require_auth)])
def confirm(postmortem_id: str, body: ConfirmBody, db: Session = Depends(get_db)):
    repo = _repo(db)
    row = _selected(repo, postmortem_id)
    if row["status"] == "confirmed":
        return {"postmortem_id": postmortem_id, "status": "confirmed", "already": True}
    mission = repo.mission(int(row["mission_id"]))
    if mission is None:
        raise HTTPException(409, "mission 行不存在，拒绝定稿（无法独立复算）")
    verdict = fpm.ChallengeVerdict(
        ok=True, objections=tuple((row.get("challenge") or {}).get("objections") or ())
    )
    result = fpm.confirm_draft(
        row,
        reviewer=body.reviewer,
        role=body.role,
        mission=mission,
        challenge=verdict,
    )
    if not result.ok:
        repo.update_postmortem(
            postmortem_id, status=result.status, challenge={"objections": [result.reason]})
        raise HTTPException(409, f"定稿被拒({result.status})：{result.reason}")
    repo.update_postmortem(
        postmortem_id,
        status="confirmed",
        confirmed_by=body.reviewer,
        confirm_role=body.role,
        confirmed_at=fpm._now(),
    )
    repo.insert_rules(row)
    return {"postmortem_id": postmortem_id, "status": "confirmed",
            "confirmed_by": body.reviewer, "confirm_role": body.role}


class RejectBody(BaseModel):
    reviewer: str = Field(min_length=3, max_length=120)
    reason: str = Field(min_length=10, max_length=500)


@router.post("/{postmortem_id}/reject", dependencies=[Depends(require_auth)])
def reject(postmortem_id: str, body: RejectBody, db: Session = Depends(get_db)):
    repo = _repo(db)
    row = _selected(repo, postmortem_id)
    updated = fpm.reject_draft(row, reviewer=body.reviewer, reason=body.reason)
    repo.update_postmortem(
        postmortem_id, status=updated["status"], rejected_reason=updated["rejected_reason"])
    return {"postmortem_id": postmortem_id, "status": "rejected",
            "rejected_reason": updated["rejected_reason"]}


@router.get("/health", dependencies=[Depends(require_auth)])
def health(db: Session = Depends(get_db)):
    """表存在性 + 计数 + 自动收割开关（运维可见）。"""
    repo = _repo(db)
    try:
        summary = fpm.summarize(repo.postmortems(), repo.rules(only_confirmed=False), repo.references())
    except Exception as exc:  # noqa: BLE001
        return {"status": "DEGRADED", "error": f"{type(exc).__name__}: {exc}"}
    return {
        "status": "up",
        "autoharvest_enabled": os.getenv("TP_POSTMORTEM_AUTOHARVEST", "1") != "0",
        "bootstrap_target": DEFAULT_BOOTSTRAP,
        "summary": summary,
    }