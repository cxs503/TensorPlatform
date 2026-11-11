"""
能力中心 API（PR-10 · AOG P1）——组织能力账的聚合读面。

PR-5（#592）落了 tensor_capability_deltas 流水与逐 mission 读端点
（/tensor/missions/{id}/capability-delta）；四中心要的是组织视角聚合，
本模块补两个只读端点（org 域隔离与既有 mission 端点同规）：

- GET /tensor/capability/deltas   增量流水（join mission 取 repo/标题）
- GET /tensor/capability/summary  类别分布 + 验收覆盖（succeeded 是否全部入账）
"""
import json

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import text
from sqlalchemy.orm import Session

from api.deps import require_auth
from database.config import get_db
from api.domain.tensor_common import resolved_org
from api.tenant_access import is_admin

router = APIRouter(prefix="/tensor/capability", tags=["能力中心"])

CATEGORIES = (
    "new_capability", "capability_extension", "defect_fix",
    "reusable_knowledge", "falsified_hypothesis", "no_substantive_delta",
    "bench_verified",
)


def _org_scope(db: Session, user: dict) -> int:
    org_id = resolved_org(None, user, db)
    if org_id is None and not is_admin(user):
        raise HTTPException(status_code=404, detail="no organization context")
    return org_id


@router.get("/deltas", dependencies=[Depends(require_auth)])
def list_capability_deltas(
    limit: int = 50,
    category: str = None,
    db: Session = Depends(get_db),
    user: dict = Depends(require_auth),
):
    """能力增量流水（新到旧）。category 可选过滤（六值之一）。"""
    org_id = _org_scope(db, user)
    # mission 标题取 result.headline（缺省回 goal）——JSON 解析放 Python 侧，
    # `->>' 只有 PG 有，sqlite（CI）不认。
    q = text("""
        SELECT d.id, d.mission_id, d.category, d.summary, d.reason,
               d.artifacts, d.evidence, d.card_id, d.reviewer, d.created_at,
               m.repo AS mission_repo, m.goal AS mission_goal,
               m.result AS mission_result
        FROM tensor_capability_deltas d
        JOIN tensor_dev_missions m ON m.id = d.mission_id
        WHERE (:org IS NULL OR d.organization_id = :org)
    """)
    params = {"org": org_id, "lim": min(max(limit, 1), 200)}
    if category is not None:
        if category not in CATEGORIES:
            raise HTTPException(status_code=422, detail=f"category must be one of {CATEGORIES}")
        q = text(str(q) + " AND d.category = :cat")
        params["cat"] = category
    q = text(str(q) + " ORDER BY d.id DESC LIMIT :lim")
    rows = db.execute(q, params).mappings().all()
    items = []
    for r in rows:
        headline = None
        try:
            blob = r["mission_result"]
            if isinstance(blob, str):
                blob = json.loads(blob or "{}")
            if isinstance(blob, dict):
                headline = blob.get("headline")
        except Exception:
            headline = None
        items.append({
            "id": r["id"],
            "mission_id": r["mission_id"],
            "mission_repo": r["mission_repo"],
            "mission_title": headline or r["mission_goal"],
        "category": r["category"],
        "summary": r["summary"],
        "reason": r["reason"],
        "artifacts": r["artifacts"] if not isinstance(r["artifacts"], str) else json.loads(r["artifacts"] or "[]"),
        "evidence": r["evidence"] if not isinstance(r["evidence"], str) else json.loads(r["evidence"] or "{}"),
        "card_id": r["card_id"],
        "reviewer": r["reviewer"],
        "created_at": r["created_at"].isoformat() if hasattr(r["created_at"], "isoformat") else r["created_at"],
        })
    return {"total": len(items), "items": items}


@router.get("/summary", dependencies=[Depends(require_auth)])
def capability_summary(db: Session = Depends(get_db), user: dict = Depends(require_auth)):
    """类别分布 + 验收入账覆盖（闭环 C：成果→能力增量是否真通）。"""
    org_id = _org_scope(db, user)
    by_category = {
        r[0] or "unknown": r[1] for r in db.execute(text("""
            SELECT category, count(*) FROM tensor_capability_deltas
            WHERE (:org IS NULL OR organization_id = :org)
            GROUP BY category
        """), {"org": org_id}).fetchall()
    }
    # 覆盖率分母 = 本 org 全部 succeeded mission；分子 = 已入账 delta 的
    # succeeded mission 数（拿 JOIN 去重算，不靠两数相除的侥幸）
    covered = db.execute(text("""
        SELECT count(DISTINCT d.mission_id)
        FROM tensor_capability_deltas d
        JOIN tensor_dev_missions m ON m.id = d.mission_id
        WHERE (:org IS NULL OR d.organization_id = :org)
          AND m.status = 'succeeded'
    """), {"org": org_id}).scalar() or 0
    succeeded = db.execute(text("""
        SELECT count(*) FROM tensor_dev_missions
        WHERE status = 'succeeded' AND (:org IS NULL OR organization_id = :org)
    """), {"org": org_id}).scalar() or 0
    substantive = sum(n for c, n in by_category.items() if c != "no_substantive_delta")
    return {
        "by_category": {c: by_category.get(c, 0) for c in CATEGORIES},
        "total_deltas": sum(by_category.values()),
        "substantive_deltas": substantive,
        "no_substantive_deltas": by_category.get("no_substantive_delta", 0),
        "succeeded_missions": succeeded,
        "covered_succeeded": covered,
        "coverage_ratio": round(covered / succeeded, 4) if succeeded else None,
    }
