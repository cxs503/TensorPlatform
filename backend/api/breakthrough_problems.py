"""breakthrough_problems 准入门 API —— 攻关问题准入 + 七环状态机 + 战役关联。

协议（与 tensor_missions 同源，agent = 平台真实用户）：
  POST /tensor/breakthrough/admission      -> 提交攻关问题做准入门（三要素校验）
  POST /tensor/breakthrough/problems       -> 准入通过后落库（含七环初始环）
  GET  /tensor/breakthrough/problems/{id}  -> 查询问题（含阶段/缺件/准入状态）
  POST /tensor/breakthrough/problems/{id}/phase -> 七环状态机推进（相邻环）
  POST /tensor/breakthrough/problems/{id}/admit -> 显式准入/豁免（红队可失败实证）

准入门规则（服务层 breakthrough_admission 强制）：
  - 难度≥3 或 research 类任务 → 三要素齐备（问题树≥3 / 基线+差距 / 可机检验收）；
    缺任一 → 422 并列缺件清单。
  - 门必须可失败：三要素不全的攻关任务必须被拒（红队负例）。
  - 普通工程单可显式声明豁免（exemption_declared）；科研任务不得借它绕门。
"""
from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.orm import Session

from api.deps import require_auth, require_role
from api.domain.tensor_common import resolved_org
from database.config import get_db
from services.breakthrough_admission import (
    PHASES,
    admission_decision,
    exemption_guard,
    missing_elements,
    normalise_task_class,
    phase_transition,
    requires_admission,
)

router = APIRouter(prefix="/tensor/breakthrough", tags=["攻关问题准入门"])


def _dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, default=str)


def _iso(value: Any) -> str | None:
    return value.isoformat() if value else None


def _row_to_dict(r: Any) -> dict:
    out = {}
    for k, v in dict(r).items():
        if isinstance(v, bytes):
            out[k] = v.decode("utf-8", "replace")
        else:
            out[k] = v
    return out


# ──────────────────────────────── 请求模型 ───────────────────────────────

class BreakthroughAdmissionRequest(BaseModel):
    """准入门提交：三要素 + 任务属性。"""
    title: str = Field(min_length=2, max_length=200)
    problem_statement: str = Field(min_length=10, max_length=12000)
    # 三要素
    problem_tree: list[str] = Field(default_factory=list, min_length=0)
    baseline_summary: str = Field(default="", max_length=4000)
    baseline_gap: str = Field(default="", max_length=4000)
    acceptance_criteria: list[dict[str, Any]] = Field(default_factory=list)
    # 任务属性
    difficulty: int = Field(default=1, ge=1, le=5)
    task_class: str = Field(default="engineering", pattern="^(research|engineering)$")
    # 豁免声明位（普通工程单显式声明；科研任务不得借它绕门）
    exemption_declared: bool = False
    # 战役关联
    campaign_id: int | None = None
    plan_id: int | None = None
    organization_id: int | None = None


class BreakthroughProblemCreate(BaseModel):
    """准入通过后落库（复用 admission 请求体 + 可选 mission 挂接）。"""
    title: str = Field(min_length=2, max_length=200)
    problem_statement: str = Field(min_length=10, max_length=12000)
    problem_tree: list[str] = Field(default_factory=list)
    baseline_summary: str = Field(default="", max_length=4000)
    baseline_gap: str = Field(default="", max_length=4000)
    acceptance_criteria: list[dict[str, Any]] = Field(default_factory=list)
    difficulty: int = Field(default=1, ge=1, le=5)
    task_class: str = Field(default="engineering", pattern="^(research|engineering)$")
    exemption_declared: bool = False
    campaign_id: int | None = None
    plan_id: int | None = None
    mission_id: int | None = None
    organization_id: int | None = None


class PhaseAdvanceRequest(BaseModel):
    """七环状态机推进（只允许相邻环）。"""
    target_phase: str = Field(pattern="^(problem_definition|investigation|hypothesis|experiment|challenge|acceptance|distillation)$")


class AdmitRequest(BaseModel):
    """显式准入/豁免（红队可失败实证：三要素不全必须被拒）。"""
    admit: bool = True


# ──────────────────────────────── 准入门 ───────────────────────────────

@router.post("/admission", dependencies=[Depends(require_role("ADMIN", "MANAGER", "ENGINEER"))])
def submit_admission(
    body: BreakthroughAdmissionRequest,
    db: Session = Depends(get_db),
    user: dict = Depends(require_auth),
):
    """提交攻关问题做准入门。

    难度≥3 或 research 类 → 三要素齐备才放行；缺任一 → 422 并列缺件清单。
    门必须可失败：三要素不全的攻关任务必须被拒（红队负例）。
    """
    org_id = resolved_org(body.organization_id, user, db)

    # 豁免声明位守卫：科研任务不得借工程单豁免绕门。
    guard = exemption_guard(
        task_class=body.task_class,
        exemption_declared=body.exemption_declared,
        difficulty=body.difficulty,
    )
    if not guard["ok"]:
        raise HTTPException(
            status_code=422,
            detail={
                "admitted": False,
                "status": "rejected",
                "missing": ["exemption_abuse"],
                "reason": guard["reason"],
                "missing_elements": ["exemption_abuse"],
            },
        )

    # 准入门决策（纯函数）。
    decision = admission_decision(
        difficulty=body.difficulty,
        task_class=body.task_class,
        problem_tree=body.problem_tree,
        baseline_summary=body.baseline_summary,
        baseline_gap=body.baseline_gap,
        acceptance_criteria=body.acceptance_criteria,
        exemption_declared=body.exemption_declared,
    )

    if not decision["admitted"]:
        # 门必须可失败：三要素不全 → 422 并列缺件清单。
        raise HTTPException(
            status_code=422,
            detail={
                "admitted": False,
                "status": decision["status"],
                "missing": decision["missing"],
                "missing_elements": decision["missing"],
                "reason": decision["reason"],
                "exemption_used": decision["exemption_used"],
            },
        )

    return {
        "admitted": True,
        "status": decision["status"],
        "missing": [],
        "missing_elements": [],
        "reason": decision["reason"],
        "exemption_used": decision["exemption_used"],
        "organization_id": org_id,
    }


# ──────────────────────────────── 落库 ───────────────────────────────

@router.post("/problems", dependencies=[Depends(require_role("ADMIN", "MANAGER", "ENGINEER"))])
def create_problem(
    body: BreakthroughProblemCreate,
    db: Session = Depends(get_db),
    user: dict = Depends(require_auth),
):
    """准入通过后落库（含七环初始环 problem_definition）。

    落库前再次执行准入门（防绕过：直接 POST /problems 也必须过门）。
    """
    org_id = resolved_org(body.organization_id, user, db)

    # 豁免声明位守卫（科研任务不得借工程单豁免绕门）。
    guard = exemption_guard(
        task_class=body.task_class,
        exemption_declared=body.exemption_declared,
        difficulty=body.difficulty,
    )
    if not guard["ok"]:
        raise HTTPException(
            status_code=422,
            detail={"admitted": False, "status": "rejected",
                    "missing": ["exemption_abuse"], "reason": guard["reason"]},
        )

    # 准入门决策（落库前强制）。
    decision = admission_decision(
        difficulty=body.difficulty,
        task_class=body.task_class,
        problem_tree=body.problem_tree,
        baseline_summary=body.baseline_summary,
        baseline_gap=body.baseline_gap,
        acceptance_criteria=body.acceptance_criteria,
        exemption_declared=body.exemption_declared,
    )
    if not decision["admitted"]:
        raise HTTPException(
            status_code=422,
            detail={"admitted": False, "status": decision["status"],
                    "missing": decision["missing"],
                    "missing_elements": decision["missing"],
                    "reason": decision["reason"]},
        )

    row = db.execute(
        text("""
            INSERT INTO breakthrough_problems
                (organization_id, campaign_id, plan_id, mission_id,
                 title, problem_statement, problem_tree, baseline_summary,
                 baseline_gap, acceptance_criteria, difficulty, task_class,
                 phase, admission_status, exemption_declared, missing_elements,
                 created_by)
            VALUES (:org, :cid, :pid, :mid, :title, :stmt, :tree, :base,
                    :gap, :acc, :diff, :cls, 'problem_definition', :adm,
                    :exempt, :missing, :uid)
            RETURNING id, created_at
        """),
        {
            "org": org_id,
            "cid": body.campaign_id,
            "pid": body.plan_id,
            "mid": body.mission_id,
            "title": body.title,
            "stmt": body.problem_statement,
            "tree": _dumps(body.problem_tree),
            "base": body.baseline_summary,
            "gap": body.baseline_gap,
            "acc": _dumps(body.acceptance_criteria),
            "diff": body.difficulty,
            "cls": body.task_class,
            "adm": decision["status"],
            "exempt": body.exemption_declared,
            "missing": _dumps(decision["missing"]),
            "uid": user.get("id"),
        },
    ).mappings().first()
    db.commit()
    return {"id": row["id"], "status": "created", "phase": "problem_definition",
            "admission_status": decision["status"], "created_at": _iso(row["created_at"])}


# ──────────────────────────────── 查询 ───────────────────────────────

@router.get("/problems", dependencies=[Depends(require_auth)])
def list_problems(
    campaign_id: int | None = None,
    phase: str | None = None,
    admission_status: str | None = None,
    limit: int = 50,
    db: Session = Depends(get_db),
    user: dict = Depends(require_auth),
):
    """列出攻关问题（可按战役/阶段/准入状态过滤）。"""
    org_id = resolved_org(None, user, db)
    filters = ["p.organization_id = :org"]
    params: dict[str, Any] = {"org": org_id, "limit": min(max(limit, 1), 100)}
    if campaign_id:
        filters.append("p.campaign_id = :cid")
        params["cid"] = campaign_id
    if phase:
        filters.append("p.phase = :ph")
        params["ph"] = phase
    if admission_status:
        filters.append("p.admission_status = :adm")
        params["adm"] = admission_status

    rows = db.execute(
        text(f"""
            SELECT p.id, p.campaign_id, p.plan_id, p.mission_id, p.title,
                   p.problem_statement, p.problem_tree, p.baseline_summary,
                   p.baseline_gap, p.acceptance_criteria, p.difficulty, p.task_class,
                   p.phase, p.admission_status, p.exemption_declared,
                   p.missing_elements, p.created_by, p.created_at, p.updated_at
            FROM breakthrough_problems p
            WHERE {' AND '.join(filters)}
            ORDER BY p.created_at DESC LIMIT :limit
        """),
        params,
    ).mappings().all()
    return {"items": [_row_to_dict(r) for r in rows], "total": len(rows)}


@router.get("/problems/{problem_id}", dependencies=[Depends(require_auth)])
def get_problem(problem_id: int, db: Session = Depends(get_db), user: dict = Depends(require_auth)):
    """查询单个攻关问题（含阶段/缺件/准入状态）。"""
    org_id = resolved_org(None, user, db)
    r = db.execute(
        text("""
            SELECT * FROM breakthrough_problems
            WHERE id = :pid AND organization_id = :org
        """),
        {"pid": problem_id, "org": org_id},
    ).mappings().first()
    if not r:
        raise HTTPException(404, "problem not found")
    return _row_to_dict(r)


# ──────────────────────────────── 七环状态机 ─────────────────────────────

@router.post("/problems/{problem_id}/phase", dependencies=[Depends(require_role("ADMIN", "MANAGER", "ENGINEER"))])
def advance_phase(
    problem_id: int,
    body: PhaseAdvanceRequest,
    db: Session = Depends(get_db),
    user: dict = Depends(require_auth),
):
    """七环状态机推进（只允许相邻环：前进/回退一步，不可跳环）。"""
    org_id = resolved_org(None, user, db)
    r = db.execute(
        text("""
            SELECT id, phase FROM breakthrough_problems
            WHERE id = :pid AND organization_id = :org
        """),
        {"pid": problem_id, "org": org_id},
    ).mappings().first()
    if not r:
        raise HTTPException(404, "problem not found")

    transition = phase_transition(r["phase"], body.target_phase)
    if not transition["ok"]:
        raise HTTPException(
            status_code=422,
            detail={"ok": False, "from": transition["from"], "to": transition["to"],
                    "reason": transition["reason"]},
        )

    db.execute(
        text("""
            UPDATE breakthrough_problems
            SET phase = :to, updated_at = now()
            WHERE id = :pid
        """),
        {"to": body.target_phase, "pid": problem_id},
    )
    db.commit()
    return {"ok": True, "from": transition["from"], "to": transition["to"], "phase": body.target_phase}


# ──────────────────────────────── 显式准入/豁免 ─────────────────────────────

@router.post("/problems/{problem_id}/admit", dependencies=[Depends(require_role("ADMIN", "MANAGER"))])
def admit_problem(
    problem_id: int,
    body: AdmitRequest,
    db: Session = Depends(get_db),
    user: dict = Depends(require_auth),
):
    """显式准入/豁免（红队可失败实证：三要素不全的攻关任务必须被拒）。

    仅当问题已通过准入门（admission_status in admitted/exempted）才可显式确认；
    三要素不全（rejected）的问题即使显式 admit=True 也**必须**保持被拒。
    """
    org_id = resolved_org(None, user, db)
    r = db.execute(
        text("""
            SELECT id, admission_status, missing_elements, task_class,
                   exemption_declared, difficulty
            FROM breakthrough_problems
            WHERE id = :pid AND organization_id = :org
        """),
        {"pid": problem_id, "org": org_id},
    ).mappings().first()
    if not r:
        raise HTTPException(404, "problem not found")

    # 红队可失败：三要素不全（rejected）的问题，即使显式 admit 也保持被拒。
    if r["admission_status"] == "rejected":
        raise HTTPException(
            status_code=422,
            detail={"admitted": False, "status": "rejected",
                    "missing_elements": json.loads(r["missing_elements"] or "[]"),
                    "reason": "三要素不全的攻关任务必须被拒（红队可失败实证）"},
        )

    # 已准入/已豁免 → 显式确认（幂等）。
    db.execute(
        text("""
            UPDATE breakthrough_problems
            SET admission_status = :adm, updated_at = now()
            WHERE id = :pid
        """),
        {"adm": "admitted" if body.admit else r["admission_status"], "pid": problem_id},
    )
    db.commit()
    return {"id": problem_id, "admission_status": "admitted" if body.admit else r["admission_status"]}
