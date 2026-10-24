"""公共实况展示面：免登录只读（owner 2026-10-22 指令——部署平台仅作
实况展示入口，取消人的交互入口）。

边界：
- 只暴露展示级数据（mission 概要/已发布报告）；无令牌、无凭据、无
  内部路径；
- 全部 GET、无写端点；写操作仍一律走认证面（require_auth）；
- org 取全库 MIN(id)（单租户实况；多租户部署时应关闭本路由或加网关
  鉴权）。
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from sqlalchemy import text
from sqlalchemy.orm import Session

from database.config import get_db

router = APIRouter(prefix="/public", tags=["public-wall"])

STATIC = Path(__file__).resolve().parent.parent / "static"


def _j(v):
    """jsonb 在 PG 返回 list/dict；sqlite 测试夹具返回 str——统一成对象。"""
    return json.loads(v) if isinstance(v, str) else v


def _org_id(db: Session) -> int | None:
    return db.execute(text("SELECT MIN(id) FROM organizations")).scalar()


@router.get("/wall", summary="实况大屏页面（免登录）")
def wall():
    f = STATIC / "wall.html"
    if not f.exists():
        raise HTTPException(404, "wall.html missing")
    return FileResponse(f, media_type="text/html")


@router.get("/marked.min.js", summary="大屏 markdown 渲染库（内网离线）")
def marked_js():
    f = STATIC / "marked.min.js"
    if not f.exists():
        raise HTTPException(404, "marked.min.js missing")
    return FileResponse(f, media_type="application/javascript")


@router.get("/overview", summary="实况总览")
def overview(db: Session = Depends(get_db)):
    org = _org_id(db)
    since = datetime.now(timezone.utc) - timedelta(hours=24)
    if org is None:
        return {"status_counts": {}, "succeeded_24h": 0, "failed_24h": 0,
                "reports_total": 0, "running": [], "latest_reports": []}
    counts = {s: n for s, n in db.execute(text(
        "SELECT status, COUNT(*) FROM tensor_dev_missions "
        "WHERE organization_id=:org GROUP BY status"), {"org": org}).all()}
    day = db.execute(text(
        "SELECT COUNT(*) FILTER (WHERE status='succeeded'), "
        "COUNT(*) FILTER (WHERE status='failed') "
        "FROM tensor_dev_missions WHERE organization_id=:org "
        "AND finished_at >= :since"), {"org": org, "since": since}).first()
    reports_total = db.execute(text(
        "SELECT COUNT(*) FROM tensor_reports WHERE organization_id=:org "
        "AND status='published'"), {"org": org}).scalar()
    running = [dict(r) for r in db.execute(text(
        "SELECT id, repo, model, substr(goal,1,160) AS goal, claimed_by, "
        "heartbeat_at, created_at FROM tensor_dev_missions "
        "WHERE organization_id=:org AND status='running' ORDER BY id"),
        {"org": org}).mappings()]
    latest = [dict(r) for r in db.execute(text(
        "SELECT id, title, created_at FROM tensor_reports "
        "WHERE organization_id=:org AND status='published' "
        "ORDER BY id DESC LIMIT 6"), {"org": org}).mappings()]
    return {"status_counts": counts, "succeeded_24h": day[0] or 0,
            "failed_24h": day[1] or 0, "reports_total": reports_total,
            "running": running, "latest_reports": latest,
            "server_time": datetime.now(timezone.utc).isoformat()}


@router.get("/missions", summary="近期 mission 流水（只读）")
def missions(limit: int = 50, db: Session = Depends(get_db)):
    limit = max(1, min(limit, 200))
    org = _org_id(db)
    if org is None:
        return []
    rows = db.execute(text(
        "SELECT id, status, repo, model, substr(goal,1,300) AS goal, "
        "claimed_by, created_at, finished_at FROM tensor_dev_missions "
        "WHERE organization_id=:org ORDER BY id DESC LIMIT :lim"),
        {"org": org, "lim": limit}).mappings().all()
    return [dict(r) for r in rows]


@router.get("/reports", summary="已发布报告列表（只读）")
def reports(limit: int = 30, db: Session = Depends(get_db)):
    limit = max(1, min(limit, 100))
    org = _org_id(db)
    if org is None:
        return []
    rows = db.execute(text(
        "SELECT id, title, substr(summary,1,220) AS summary, tags, "
        "mission_id, created_at FROM tensor_reports "
        "WHERE organization_id=:org AND status='published' "
        "ORDER BY id DESC LIMIT :lim"), {"org": org, "lim": limit}
    ).mappings().all()
    return [{**dict(r), "tags": _j(r["tags"])} for r in rows]


@router.get("/reports/{report_id}", summary="报告正文+图件（只读）")
def report_detail(report_id: int, db: Session = Depends(get_db)):
    org = _org_id(db)
    if org is None:
        raise HTTPException(404, "report not found")
    row = db.execute(text(
        "SELECT id, title, summary, tags, markdown, figures, created_at "
        "FROM tensor_reports WHERE id=:rid AND organization_id=:org "
        "AND status='published'"), {"rid": report_id, "org": org}
    ).mappings().first()
    if not row:
        raise HTTPException(404, "report not found")
    out = dict(row)
    out["tags"] = _j(out.get("tags")) or []
    figs = _j(out.get("figures")) or []
    out["figures"] = [{"name": f.get("name", ""), "caption": f.get("caption", ""),
                       "png_base64": f.get("png_base64", "")} for f in figs]
    return out
