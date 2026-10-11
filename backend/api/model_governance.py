"""
模型治理 API — 持久化版
审批流程 · 审计日志 · 风险评估 · 合规报告
"""
import json, uuid
from datetime import datetime
from typing import Optional, List

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import text
from database.config import get_db

router = APIRouter(prefix="/governance", tags=["模型治理"])

# ═══════════ 端点 ═══════════

@router.get("/status")
def get_status(db=Depends(get_db)):
    total = db.execute(text("SELECT COUNT(*) FROM governance_approvals")).fetchone()[0]
    pending = db.execute(text("SELECT COUNT(*) FROM governance_approvals WHERE status='pending'")).fetchone()[0]
    audits = db.execute(text("SELECT COUNT(*) FROM governance_audit")).fetchone()[0]
    return {"status": "ready", "total_approvals": total, "pending_approvals": pending, "total_audits": audits}


@router.get("/approvals")
def list_approvals(status: Optional[str] = None, db=Depends(get_db)):
    q = "SELECT request_id, model_id, action, requester, approver, status, comments, created_at FROM governance_approvals"
    p = {}
    if status:
        q += " WHERE status = :st"; p["st"] = status
    q += " ORDER BY id DESC LIMIT 100"
    rows = db.execute(text(q), p).fetchall()
    return [{"request_id": r[0], "model_id": r[1], "action": r[2], "requester": r[3], "approver": r[4],
             "status": r[5], "comments": r[6], "created_at": r[7].isoformat() if r[7] else None} for r in rows]


@router.post("/approvals")
def create_approval(req: dict, db=Depends(get_db)):
    rid = f"apr_{uuid.uuid4().hex[:8]}"
    db.execute(text("""INSERT INTO governance_approvals (request_id, model_id, action, requester, status, comments, created_at)
        VALUES (:rid, :mid, :act, :req, 'pending', :cmt, NOW())"""),
        {"rid": rid, "mid": int(req.get("model_id", 0)), "act": req.get("action", "deploy"),
         "req": req.get("requester", "admin"), "cmt": req.get("comments", "")})
    _audit(db, int(req.get("model_id", 0)), f"approval_created:{rid}", req.get("requester", "admin"))
    db.commit()
    return {"request_id": rid, "status": "pending"}


@router.post("/approvals/{request_id}/approve")
def approve(request_id: str, approver: str = "admin", db=Depends(get_db)):
    r = db.execute(text("UPDATE governance_approvals SET status='approved', approver=:ap, updated_at=NOW() WHERE request_id=:rid RETURNING model_id,action"),
                   {"rid": request_id, "ap": approver}).fetchone()
    if not r: raise HTTPException(404, "审批不存在")
    _audit(db, r[0], f"approved:{request_id}", approver)
    db.commit()
    return {"request_id": request_id, "status": "approved"}


@router.post("/approvals/{request_id}/reject")
def reject(request_id: str, reason: str = "", db=Depends(get_db)):
    r = db.execute(text("UPDATE governance_approvals SET status='rejected', comments=:cmt, updated_at=NOW() WHERE request_id=:rid RETURNING model_id"),
                   {"rid": request_id, "cmt": reason}).fetchone()
    if not r: raise HTTPException(404, "审批不存在")
    _audit(db, r[0], f"rejected:{request_id}", "admin")
    db.commit()
    return {"request_id": request_id, "status": "rejected"}


@router.get("/audit-logs")
def list_audit_logs(model_id: Optional[int] = None, limit: int = 50, db=Depends(get_db)):
    q = "SELECT id, model_id, action, user_name, details, created_at FROM governance_audit"
    p = {}
    if model_id:
        q += " WHERE model_id = :mid"; p["mid"] = model_id
    q += " ORDER BY id DESC LIMIT :lim"; p["lim"] = limit
    rows = db.execute(text(q), p).fetchall()
    return [{"id": r[0], "model_id": r[1], "action": r[2], "user": r[3],
             "details": json.loads(r[4]) if isinstance(r[4], str) else (r[4] or {}),
             "created_at": r[5].isoformat() if r[5] else None} for r in rows]


@router.get("/dashboard")
def get_dashboard(db=Depends(get_db)):
    pending = db.execute(text("SELECT COUNT(*) FROM governance_approvals WHERE status='pending'")).fetchone()[0]
    approved = db.execute(text("SELECT COUNT(*) FROM governance_approvals WHERE status='approved'")).fetchone()[0]
    total_audit = db.execute(text("SELECT COUNT(*) FROM governance_audit")).fetchone()[0]
    recent = db.execute(text("SELECT action, user_name, created_at FROM governance_audit ORDER BY id DESC LIMIT 5")).fetchall()
    return {
        "pending_approvals": pending, "approved": approved, "total_audits": total_audit,
        "recent_activity": [{"action": r[0], "user": r[1], "time": r[2].isoformat() if r[2] else None} for r in recent]
    }


def _audit(db, model_id: int, action: str, user: str = "system"):
    db.execute(text("INSERT INTO governance_audit (model_id, action, user_name, details, created_at) VALUES (:mid, :act, :usr, '{}', NOW())"),
               {"mid": model_id, "act": action, "usr": user})
