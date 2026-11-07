"""S×E 成果评价 API（AOG SPEC v1.2 §7.1）——实体写入 / 待评价记录流 / 分层统计。

端点（prefix ``/tensor/outcome-evaluations``）：

- ``POST   /``                      写入一条评价（无理由/无证据/非法 S×E → 422 拒收）
- ``GET    /``                      评价流水（按 mission/status/grade 过滤）
- ``GET    /stats``                 本仓近 N 单 S×E 分层分布（禁加权禁排名）
- ``GET    /stats/sql``             同一统计的可复算 SQL（原样返回）
- ``GET    /{evaluation_id}``       单条明细（含判据命中码）
- ``POST   /pending/sync``          为 succeeded 且无记录的本仓 mission 补建待评价记录

写入判据全部在 ``tools.outcome_evaluations.decide``（纯函数），本模块只做
鉴权/组织隔离/幂等落库/读面。被拒的写入**也落一行 status='rejected' 留痕**
（审计负例），但同成果的 confirmed/candidate 行仍唯一（分部唯一索引）。

零领域语义：本模块不出现任何学科/业务词，等级语义由领域系统提供。
"""
from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.orm import Session

from api.deps import require_auth
from api.domain.tensor_common import resolved_org
from api.tenant_access import is_admin
from database.config import get_db

from tools.outcome_evaluations import (
    DEFAULT_WINDOW,
    DDL,
    EVIDENCE_KINDS,
    E_LEVELS,
    REGISTRABLE_GRADES,
    REVIEWER_KINDS,
    STATUSES,
    decide,
    dumps_json,
    insert_if_absent_sql,
    insert_sql,
    loads_json,
    pending_record_from_mission,
    record_from_verdict,
    render_markdown,
    stats_queries,
    store_record as _store_record,
    summarize,
)

router = APIRouter(prefix="/tensor/outcome-evaluations", tags=["成果评价 S×E"])

def _org_scope(db: Session, user: dict) -> int | None:
    org_id = resolved_org(None, user, db)
    if org_id is None and not is_admin(user):
        raise HTTPException(status_code=404, detail="no organization context")
    return org_id


def _json_field(value: Any, default: Any) -> Any:
    if value is None:
        return default
    if isinstance(value, (list, dict)):
        return value
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return default


def _row_to_dict(r: Any) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for k, v in dict(r).items():
        if isinstance(v, datetime):
            out[k] = v.replace(microsecond=0).isoformat()
        elif k in ("evidence_refs", "reason_codes"):
            out[k] = _json_field(v, [] if k == "evidence_refs" else [])
        else:
            out[k] = v
    return out


def ensure_schema(db: Session) -> None:
    """幂等建表（编号迁移 103 之外的自愈路径：CI/本地 SQLite 也能直接跑）。"""
    for stmt in [s.strip() for s in DDL.split(";") if s.strip()]:
        db.execute(text(stmt))
    db.commit()


def ensure_pending_for_mission(db: Session, mission: dict[str, Any]) -> str | None:
    """为**单张** mission 生成待评价记录（幂等）；已有有效记录则跳过。

    完结钩子只处理本单（不做全仓扫描），避免每次完结都触发存量清扫。
    """
    mission_id = int(mission["id"])
    exists = db.execute(
        text("SELECT 1 FROM outcome_evaluations WHERE mission_id = :mid AND status <> 'rejected' LIMIT 1"),
        {"mid": mission_id},
    ).scalar()
    if exists:
        return None
    record = pending_record_from_mission(dict(mission))
    db.execute(text(insert_if_absent_sql()), record)
    return record["evaluation_id"]


def store_record(db: Session, record: dict[str, Any]) -> bool:
    """落库一条评价判定结果（判据与 SQL 在 tools/outcome_evaluations，单一契约源）。

    返回 True 表示就地升级了已有 pending 行。
    """
    return _store_record(db, record, commit=True)


def _mission_row(db: Session, mission_id: int, org_id: int | None) -> dict[str, Any]:
    row = db.execute(
        text(
            "SELECT id, organization_id, repo, status, claimed_by, finished_at "
            "FROM tensor_dev_missions WHERE id = :mid"
        ),
        {"mid": mission_id},
    ).mappings().first()
    if row is None:
        raise HTTPException(status_code=404, detail=f"mission {mission_id} not found")
    mission = dict(row)
    if org_id is not None and mission.get("organization_id") != org_id:
        raise HTTPException(status_code=404, detail=f"mission {mission_id} not found")
    return mission


def sync_pending(db: Session, *, org_id: int | None, repo: str | None = None,
                 limit: int = 200) -> list[dict[str, Any]]:
    """为 succeeded 且尚无有效记录（非 rejected）的 mission 补建待评价记录。

    幂等：已存在 pending/confirmed/candidate 行时 ``INSERT ... WHERE NOT EXISTS`` 跳过。
    """
    sql = (
        "SELECT m.id, m.organization_id, m.repo, m.status, m.claimed_by, m.finished_at "
        "FROM tensor_dev_missions m "
        "WHERE m.status = 'succeeded' "
        # PG 无法从 `:x IS NULL` 推断参数类型 → 显式 CAST（SQLite 同样接受）
        "AND (CAST(:org AS BIGINT) IS NULL OR m.organization_id = CAST(:org AS BIGINT)) "
        "AND (CAST(:repo AS TEXT) IS NULL OR m.repo = CAST(:repo AS TEXT)) "
        "AND NOT EXISTS (SELECT 1 FROM outcome_evaluations e "
        "                WHERE e.mission_id = m.id AND e.status <> 'rejected') "
        "ORDER BY m.finished_at DESC NULLS LAST, m.id DESC LIMIT :lim"
    )
    rows = db.execute(
        text(sql), {"org": org_id, "repo": repo, "lim": int(limit)}
    ).mappings().all()
    created: list[dict[str, Any]] = []
    stmt = text(insert_if_absent_sql())
    for row in rows:
        record = pending_record_from_mission(dict(row))
        record["organization_id"] = record.get("organization_id") or org_id
        db.execute(stmt, record)
        created.append({"evaluation_id": record["evaluation_id"],
                        "mission_id": record["mission_id"],
                        "artifact_id": record["artifact_id"]})
    if created:
        db.commit()
    return created


class EvidenceRef(BaseModel):
    kind: str = Field(min_length=1, max_length=32)
    ref: str = Field(min_length=1, max_length=400)


class EvaluationIn(BaseModel):
    artifact_id: str | None = Field(default=None, max_length=200)
    mission_id: int = Field(ge=1)
    contributor: str = Field(min_length=1, max_length=200)
    grade: str = Field(max_length=8)
    e_level: str = Field(max_length=4)
    grade_reason: str = Field(max_length=8000)
    evidence_refs: list[EvidenceRef] = Field(default_factory=list)
    reviewer: str = Field(default="", max_length=200)
    reviewer_kind: str = Field(default="independent", max_length=16)
    baseline_or_scope: str | None = Field(default=None, max_length=4000)
    boundary: str | None = Field(default=None, max_length=4000)
    repo: str | None = Field(default=None, max_length=120)


@router.post("", dependencies=[Depends(require_auth)])
def create_evaluation(
    body: EvaluationIn,
    db: Session = Depends(get_db),
    user: dict = Depends(require_auth),
):
    """写入一条成果评价。硬判据不过 → 422 拒收（并落 rejected 留痕）。"""
    org_id = _org_scope(db, user)
    mission = _mission_row(db, body.mission_id, org_id)
    if mission.get("status") != "succeeded":
        raise HTTPException(
            status_code=409,
            detail=f"mission {body.mission_id} status={mission.get('status')}; "
                   "只有 succeeded mission 才能写入成果评价",
        )

    payload = {
        "artifact_id": body.artifact_id,
        "mission_id": body.mission_id,
        "contributor": body.contributor,
        "grade": body.grade,
        "e_level": body.e_level,
        "grade_reason": body.grade_reason,
        "evidence_refs": [r.model_dump() for r in body.evidence_refs],
        "reviewer": body.reviewer,
        "reviewer_kind": body.reviewer_kind,
        "baseline_or_scope": body.baseline_or_scope,
        "boundary": body.boundary,
        "repo": body.repo or mission.get("repo"),
        "organization_id": mission.get("organization_id"),
    }
    verdict = decide(payload)
    finished = mission.get("finished_at")
    if isinstance(finished, datetime):
        finished = finished.replace(microsecond=0).isoformat()
    record = record_from_verdict(
        payload, verdict,
        reason_source="manual",
        window_finished_at=str(finished) if finished else None,
    )
    record["organization_id"] = mission.get("organization_id") or org_id

    if verdict.status == "rejected":
        # 负例留痕：每次被拒一行，不占用分部唯一索引（status='rejected'）
        db.execute(text(insert_sql()), record)
        db.commit()
        raise HTTPException(
            status_code=422,
            detail={"message": "evaluation rejected by §7.1 criteria",
                    "evaluation_id": record["evaluation_id"],
                    "reason_codes": verdict.codes,
                    "required": {"reason_min_chars": 50,
                                 "evidence_kinds": list(EVIDENCE_KINDS),
                                 "grades": list(REGISTRABLE_GRADES),
                                 "e_levels": list(E_LEVELS),
                                 "reviewer_kinds": list(REVIEWER_KINDS)}},
        )

    # 已有 pending 行 → 就地判定（不新增行）；否则新建。两者都受分部唯一索引保护。
    updated = store_record(db, record)

    return {
        "evaluation_id": record["evaluation_id"],
        "artifact_id": record["artifact_id"],
        "mission_id": record["mission_id"],
        "status": verdict.status,
        "counted_in_distribution": verdict.counted,
        "grade": verdict.grade,
        "e_level": verdict.e_level,
        "reason_codes": verdict.codes,
        "decided_at": record["decided_at"],
        "updated_pending_row": bool(updated),
    }


@router.get("", dependencies=[Depends(require_auth)])
def list_evaluations(
    mission_id: int | None = None,
    status: str | None = None,
    grade: str | None = None,
    include_rejected: bool = False,
    limit: int = 50,
    db: Session = Depends(get_db),
    user: dict = Depends(require_auth),
):
    org_id = _org_scope(db, user)
    if status is not None and status not in STATUSES:
        raise HTTPException(status_code=422, detail=f"status must be one of {STATUSES}")
    if grade is not None and grade not in REGISTRABLE_GRADES:
        raise HTTPException(status_code=422, detail=f"grade must be one of {REGISTRABLE_GRADES}")
    q = (
        "SELECT evaluation_id, artifact_id, mission_id, contributor, grade, e_level, "
        "grade_reason, evidence_refs, reviewer, reviewer_kind, review_status, "
        "baseline_or_scope, boundary, status, reason_codes, reason_source, repo, "
        "created_at, decided_at, window_finished_at "
        "FROM outcome_evaluations WHERE 1 = 1 "
    )
    params: dict[str, Any] = {"lim": min(max(int(limit), 1), 500)}
    if org_id is not None:
        q += "AND organization_id = :org "
        params["org"] = org_id
    if mission_id is not None:
        q += "AND mission_id = :mid "
        params["mid"] = mission_id
    if status is not None:
        q += "AND status = :st "
        params["st"] = status
    if grade is not None:
        q += "AND grade = :gr "
        params["gr"] = grade
    if not include_rejected:
        q += "AND status <> 'rejected' "
    q += "ORDER BY COALESCE(decided_at, created_at) DESC, evaluation_id LIMIT :lim"
    rows = db.execute(text(q), params).mappings().all()
    return {"total": len(rows), "items": [_row_to_dict(r) for r in rows]}


@router.get("/stats", dependencies=[Depends(require_auth)])
def evaluation_stats(
    repo: str = "TensorPlatform",
    window: int = DEFAULT_WINDOW,
    db: Session = Depends(get_db),
    user: dict = Depends(require_auth),
):
    """本仓近 ``window`` 单 S×E 分层分布（禁加权、禁排名）。"""
    org_id = _org_scope(db, user)
    if not (1 <= int(window) <= 200):
        raise HTTPException(status_code=422, detail="window must be 1..200")
    stats = summarize(db, repo=repo, org=org_id, window=int(window))
    stats["markdown"] = render_markdown(stats)
    stats["policy"] = dict(stats["policy"])
    stats["sql"] = {k: v for k, v in stats["sql"].items()}
    return stats


@router.get("/stats/sql", dependencies=[Depends(require_auth)])
def evaluation_stats_sql(window: int = DEFAULT_WINDOW):
    """统计用 SQL 原文（不执行）——供独立复算。"""
    return {
        "window": int(window),
        "params": {"repo": "<repo>", "window": int(window), "org": "<org_id 或省略>"},
        "queries": stats_queries(org_filter=False),
    }


@router.post("/pending/sync", dependencies=[Depends(require_auth)])
def sync_pending_records(
    repo: str | None = None,
    limit: int = 200,
    db: Session = Depends(get_db),
    user: dict = Depends(require_auth),
):
    """补建待评价记录（幂等）。默认扫全组织 succeeded mission。"""
    org_id = _org_scope(db, user)
    created = sync_pending(db, org_id=org_id, repo=repo, limit=limit)
    return {"created": len(created), "items": created}


@router.get("/guidance", dependencies=[Depends(require_auth)])
def evaluation_guidance(
    wave_id: str | None = None,
    db: Session = Depends(get_db),
    user: dict = Depends(require_auth),
):
    """评价统计落成的派发梯度（准入/预算/优先级）+ 上一轮回路的可量效果。

    只读：档位由 ``tools.evaluation_wave.guidance_policy`` 用**词典序规则**给出，
    没有加权分数、没有主体排名（§7.1）。表不存在（迁移 104 未上）时返回空集合，
    不阻断读面。
    """
    _org_scope(db, user)  # 组织上下文校验（无上下文且非管理员 → 404，与其他端点一致）
    try:
        if wave_id:
            rows = db.execute(
                text(
                    "SELECT wave_id, subject_kind, subject, tier, budget_multiplier, "
                    "admission, priority_delta, rows_counted FROM evaluation_guidance "
                    "WHERE wave_id = :w ORDER BY subject_kind, tier, rows_counted DESC"
                ),
                {"w": wave_id},
            ).mappings().all()
        else:
            rows = db.execute(
                text(
                    "SELECT wave_id, subject_kind, subject, tier, budget_multiplier, "
                    "admission, priority_delta, rows_counted FROM evaluation_guidance "
                    "WHERE wave_id = (SELECT wave_id FROM evaluation_guidance "
                    "                 ORDER BY created_at DESC LIMIT 1) "
                    "ORDER BY subject_kind, tier, rows_counted DESC"
                )
            ).mappings().all()
        items = [dict(r) for r in rows]
        effects = {}
        if items:
            wid = items[0]["wave_id"]
            wave = db.execute(
                text("SELECT metrics FROM evaluation_waves WHERE wave_id = :w"), {"w": wid}
            ).mappings().first()
            metrics = loads_json(wave["metrics"]) if wave else {}
            effects = (metrics or {}).get("guidance_effects") or {}
        return {
            "policy": {
                "weighting": "forbidden",
                "ranking": "forbidden",
                "tiers": "lexicographic rules (S3+ then S2+ share then S1-only share)",
            },
            "wave_id": items[0]["wave_id"] if items else None,
            "items": items,
            "effects": effects,
        }
    except Exception as exc:  # 迁移未上/表缺失不阻断读面
        return {"policy": {"weighting": "forbidden", "ranking": "forbidden"},
                "wave_id": None, "items": [], "effects": {},
                "note": f"guidance unavailable: {type(exc).__name__}"}


@router.get("/{evaluation_id}", dependencies=[Depends(require_auth)])
def get_evaluation(
    evaluation_id: str,
    db: Session = Depends(get_db),
    user: dict = Depends(require_auth),
):
    org_id = _org_scope(db, user)
    row = db.execute(
        text(
            "SELECT evaluation_id, artifact_id, mission_id, contributor, grade, e_level, "
            "grade_reason, evidence_refs, reviewer, reviewer_kind, review_status, "
            "baseline_or_scope, boundary, status, reason_codes, reason_source, repo, "
            "organization_id, created_at, decided_at "
            "FROM outcome_evaluations WHERE evaluation_id = :eid"
        ),
        {"eid": evaluation_id},
    ).mappings().first()
    if row is None:
        raise HTTPException(status_code=404, detail="evaluation not found")
    item = _row_to_dict(row)
    if org_id is not None and item.get("organization_id") not in (None, org_id):
        raise HTTPException(status_code=404, detail="evaluation not found")
    return item