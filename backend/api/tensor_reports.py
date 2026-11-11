"""Tensor console: research progress reports — one report per completed
mission / stage, published with detailed case numbers, contour plots and
curves. Markdown body lives in the row; figure PNGs live on disk under
TENSOR_REPORT_FIGURE_DIR/<report_id>/<name>.png with [{name, caption}]
metadata in figures JSONB. The list endpoint omits the markdown body."""
from __future__ import annotations

import binascii
import json
import logging
import os
import re
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.orm import Session

from api.deps import require_auth, require_role
from api.domain.tensor_common import resolved_org
from database.config import get_db
from services.report_ownership import (
    FigureRejected,
    ownership_verdict,
    validate_png_payload,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/tensor/reports", tags=["科研运行台·报告"])

# Relative default resolves against the backend CWD (/app in the container,
# where uploads/ is the NFS bind mount; backend/ on a dev host).
FIGURE_DIR = Path(os.environ.get("TENSOR_REPORT_FIGURE_DIR", "uploads/tensor_reports"))


class FigureIn(BaseModel):
    name: str = Field(min_length=3, max_length=80, pattern=r"^[A-Za-z0-9_.-]+$")
    caption: str = Field(default="", max_length=300)
    png_base64: str = Field(min_length=1, max_length=8_000_000)


class ReportCreate(BaseModel):
    title: str = Field(min_length=4, max_length=200)
    summary: str = Field(default="", max_length=500)
    scenario_key: str | None = Field(default=None, max_length=120)
    mission_id: int | None = Field(default=None, ge=1)
    stage: str | None = Field(default=None, pattern=r"^S[0-4]$")
    tags: list[str] = Field(default_factory=list, max_length=10)
    markdown: str = Field(min_length=10, max_length=200_000)
    figures: list[FigureIn] = Field(default_factory=list, max_length=12)
    organization_id: int | None = Field(default=None, ge=1)


_SELECT = """SELECT id, title, summary, scenario_key, mission_id, stage, tags,
markdown, figures, status, created_by, created_at FROM tensor_reports"""


def _row(r: Any, *, with_markdown: bool = True) -> dict[str, Any]:
    out = {
        "id": r["id"],
        "title": r["title"],
        "summary": r["summary"],
        "scenario_key": r["scenario_key"],
        "mission_id": r["mission_id"],
        "stage": r["stage"],
        "tags": r["tags"] or [],
        "figures": [
            {"name": f["name"], "caption": f.get("caption", "")} for f in (r["figures"] or [])
        ],
        "status": r["status"],
        "created_by": r["created_by"],
        "created_at": r["created_at"].isoformat(),
    }
    if with_markdown:
        out["markdown"] = r["markdown"]
    return out


@router.get("", dependencies=[Depends(require_auth)])
def list_reports(
    scenario_key: str | None = None,
    mission_id: int | None = None,
    limit: int = 50,
    db: Session = Depends(get_db),
    user: dict = Depends(require_auth),
):
    org_id = resolved_org(None, user, db)
    filters = ["r.organization_id = :org"]
    params: dict[str, Any] = {"org": org_id, "limit": min(max(limit, 1), 100)}
    if scenario_key:
        filters.append("r.scenario_key = :skey")
        params["skey"] = scenario_key
    if mission_id:
        filters.append("r.mission_id = :mid")
        params["mid"] = mission_id
    rows = db.execute(
        text(f"""{_SELECT.replace('FROM tensor_reports', 'FROM tensor_reports r')}
             WHERE {' AND '.join(filters)} ORDER BY r.created_at DESC LIMIT :limit"""),
        params,
    ).mappings().all()
    return {"items": [_row(r, with_markdown=False) for r in rows], "total": len(rows)}


def _fetch_mission(db: Session, mission_id: int) -> dict | None:
    """读 mission 的归属证据（claimed_user_id 优先，回退 claimed_by）。

    0090 迁移给 ``tensor_dev_missions`` 加了 ``claimed_user_id``；老库（未跑迁移）
    只有 ``claimed_by``。这里两种都兼容，任一可用即能定性「谁是领取者」。
    """
    for sql in (
        """SELECT id, organization_id, status, claimed_by, claimed_user_id
             FROM tensor_dev_missions WHERE id = :mid""",
        """SELECT id, organization_id, status, claimed_by,
                  NULL::bigint AS claimed_user_id
             FROM tensor_dev_missions WHERE id = :mid""",
    ):
        try:
            row = db.execute(text(sql), {"mid": mission_id}).mappings().first()
            return dict(row) if row else None
        except Exception:  # UndefinedColumn / 老库 schema
            db.rollback()
            continue
    return None


def _error(status_code: int, code: str, detail: dict) -> HTTPException:
    """带机器码的 HTTPException：detail 是 JSON 体，X-Error-Code 头便于客户端 fail-fast。"""
    return HTTPException(status_code=status_code, detail=detail,
                         headers={"X-Error-Code": code})


def _insert_report_row(db: Session, *, org_id: int, uid: Any, body: ReportCreate,
                       figures_meta: list[dict]) -> int:
    """INSERT one published report row and return its id (no commit).

    Single implementation shared by the normal POST path and the self-healing
    re-send path — a second copy of this INSERT would drift and the heal would
    restore a differently-shaped row.
    """
    row = db.execute(
        text("""
            INSERT INTO tensor_reports
                (organization_id, title, summary, scenario_key, mission_id, stage,
                 tags, markdown, figures, status, created_by)
            VALUES (:org, :title, :summary, :skey, :mid, :stage,
                    CAST(:tags AS jsonb), :markdown, CAST(:figs AS jsonb),
                    'published', :uid)
            RETURNING id, created_at
        """),
        {
            "org": org_id, "title": body.title, "summary": body.summary,
            "skey": body.scenario_key, "mid": body.mission_id, "stage": body.stage,
            "tags": json.dumps(body.tags, ensure_ascii=False),
            "markdown": body.markdown,
            "figs": json.dumps(figures_meta, ensure_ascii=False),
            "uid": uid,
        },
    ).mappings().first()
    return row["id"]


def _write_figure_files(report_id: int, decoded: list) -> None:
    """Persist validated PNG bytes under ``FIGURE_DIR/<report_id>/``."""
    for fig, raw in decoded:
        dest = FIGURE_DIR / str(report_id) / f"{fig.name}.png"
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(raw)


def _submission_payload(body: ReportCreate, *, org_id: int, created_by: int) -> dict:
    """The write-side truth snapshot handed to the visibility ledger.

    It is the original request body (so a re-send reproduces the exact write,
    figures included) plus the two server-side fields a re-send needs: the
    tenant and the original author.
    """
    payload = body.model_dump()
    payload["organization_id"] = org_id
    payload["created_by"] = created_by
    return payload


def _readback_after_write(db: Session, *, body: ReportCreate,
                          figures_meta: list[dict], report_id: int,
                          org_id: int, created_by: int) -> dict:
    """Independent read-back + contract assertion + ledger entry.

    Degrades loudly (``ok: null`` + ``degraded: true`` + reason) instead of
    failing the POST: a bookkeeping outage must not turn a landed write into a
    client-visible error, but it must never be reported as a pass either.
    """
    payload = _submission_payload(body, org_id=org_id, created_by=created_by)
    try:
        from services.report_visibility import ReportVisibilityService, ledger_from_env
    except Exception as exc:  # pragma: no cover - import guard
        return {"ok": None, "degraded": True,
                "reason": f"import_failed:{type(exc).__name__}"}
    try:
        # Fresh statement on the committed connection: this reads what a later
        # GET would read, not the write cursor's own view.
        observed_row = db.execute(
            text(f"{_SELECT} WHERE id = :rid AND organization_id = :org"),
            {"rid": report_id, "org": org_id},
        ).mappings().first()
        observed = _row(observed_row) if observed_row else None
        service = ReportVisibilityService(ledger=ledger_from_env())
        return service.after_write(payload, report_id, observed=observed).as_dict()
    except Exception as exc:
        logger.warning("report readback assertion degraded: %s", exc)
        return {"ok": None, "degraded": True,
                "reason": f"{type(exc).__name__}: {str(exc)[:180]}"}


@router.post("", dependencies=[Depends(require_role("ADMIN", "MANAGER", "ENGINEER"))])
def create_report(
    body: ReportCreate,
    db: Session = Depends(get_db),
    user: dict = Depends(require_auth),
):
    org_id = resolved_org(body.organization_id, user, db)

    # ── 归属门（第一道）：报告不得挂到别人的单号上 ───────────────────────
    # 依赖客户端「记得填对本单号」是不可靠的；服务端结构性阻断 + 机器码。
    verdict = ownership_verdict(user, _fetch_mission(db, body.mission_id)
                                if body.mission_id else None,
                                mission_id=body.mission_id, org_id=org_id)
    if not verdict["ok"]:
        raise _error(verdict["status"], verdict["code"],
                     dict(verdict, expected="submit report under your own mission_id"))

    # ── 配图门（第二道）：逐图 PNG 魔数 + IEND，先验后落 ────────────────
    # 先验后落：旧实现先 INSERT 再写盘、失败再 DELETE，异常路径上会短暂存在
    # 一条指向缺失图片的报告行（并发读会读到半成品）。
    decoded: list[tuple[FigureIn, bytes]] = []
    for fig in body.figures:
        try:
            decoded.append((fig, validate_png_payload(fig.name, fig.png_base64)))
        except FigureRejected as exc:
            raise _error(exc.status_code, exc.code, exc.as_detail()) from exc

    figures_meta = [{"name": f.name, "caption": f.caption} for f in body.figures]
    report_id = _insert_report_row(db, org_id=org_id, uid=user["id"],
                                   body=body, figures_meta=figures_meta)

    # Figures land on disk only after the row exists; any failure rolls the row
    # back so a report never references missing files (fail-closed).
    try:
        _write_figure_files(report_id, decoded)
    except (ValueError, binascii.Error, OSError) as exc:
        db.execute(text("DELETE FROM tensor_reports WHERE id = :rid"), {"rid": report_id})
        db.commit()
        raise HTTPException(status_code=422, detail=f"figure rejected: {exc}") from exc

    db.commit()
    full = db.execute(text(f"{_SELECT} WHERE id = :rid"), {"rid": report_id}).mappings().first()
    out = _row(full)
    out["ownership"] = {"mission_id": body.mission_id, "owner": verdict["owner"],
                        "bypass": verdict["bypass"],
                        "figures_checked": len(decoded)}
    # 写-读可见性契约：提交后立刻在新事务里回读同一行并逐字段断言。
    # 这是「POST 200 但查不到」从静默变成事件的位置（见 services/report_visibility）。
    out["readback"] = _readback_after_write(
        db, body=body, figures_meta=figures_meta, report_id=report_id,
        org_id=org_id, created_by=user["id"])
    return out


@router.get("/{report_id}", dependencies=[Depends(require_auth)])
def get_report(report_id: int, db: Session = Depends(get_db), user: dict = Depends(require_auth)):
    org_id = resolved_org(None, user, db)
    r = db.execute(
        text(f"{_SELECT} WHERE id = :rid AND organization_id = :org"),
        {"rid": report_id, "org": org_id},
    ).mappings().first()
    if not r:
        raise HTTPException(status_code=404, detail="report not found")
    return _row(r)


# 免登录只读：owner 2026-10-31 选 A。报告页 <img> 无法携带 Bearer → 原 401 导致图片全裂。
# 保持只读 GET + 文件名白名单正则 + 路径围栏（下行实现），写操作仍需鉴权。
@router.get("/{report_id}/figures/{name}")
def get_figure(report_id: int, name: str):
    # name is a bare filename; reject anything path-like before it touches disk.
    if not re.fullmatch(r"[A-Za-z0-9_.-]+", name):
        raise HTTPException(status_code=422, detail="bad figure name")
    path = (FIGURE_DIR / str(report_id) / f"{name}.png").resolve()
    if FIGURE_DIR.resolve() not in path.parents:
        raise HTTPException(status_code=422, detail="path outside figure dir")
    if not path.is_file():
        raise HTTPException(status_code=404, detail="figure not found")
    return FileResponse(path, media_type="image/png")


# ───────────────────────── report QA (paper-quality gate) ─────────────────────────
# 论文级六要素判据（①案例介绍 ②方法 ③对标验证 ④图 ⑤局限 ⑥误差判定）的**唯一实现**
# 在 `services/report_gate.py`；本模块只做 API 适配（响应形状）+ 能力台账对账。
#
# 为什么要删掉这里的副本：本模块曾内联过一份六要素判据表，与 services 版各自演化，
# 于是「同样的报告两种判定」——测试绿而生产门悄悄变松（假门禁）。结构守卫
# `backend/tests/test_report_gate_validation.py::test_api_module_does_not_refork_gate`
# 就是为这笔账写的：API 层必须 import 共享实现，且不得再定义 REQUIRED_SECTIONS。

def _check_report_quality(markdown: str, figures: list) -> dict:
    """六要素门禁 + 能力等级断言对账（强于台账 = 违规，degraded 上报不阻断发布）。"""
    from services.report_gate import check_report_quality

    base = check_report_quality(markdown, figures)

    # 消费者 #4：报告正文的能力等级断言必须与能力台账对账（强于台账 = 违规）。
    # 纪律：台账降级 → degraded=True 但**不改变** passed（保持既有发布路径可用），
    # 由 QA 视图与 CI（tools/audit/ledger_contract_gate.py）看字段裁决。
    ledger_gate: dict = {"ok": None, "degraded": None, "violations": [],
                         "ledger_path": None, "ledger_sha256": None, "ledger_status": None}
    try:
        import sys
        from pathlib import Path as _Path
        _root = _Path(__file__).resolve().parents[2]
        if str(_root) not in sys.path:
            sys.path.insert(0, str(_root))
        from tools.audit.report_gate_ledger import gate_summary
        ledger_gate = gate_summary(markdown)
    except Exception as exc:
        ledger_gate = {"ok": None, "degraded": True, "violations": [],
                       "ledger_path": None, "ledger_sha256": None,
                       "ledger_status": f"GATE_CONSUMER_ERROR:{type(exc).__name__}"}

    return {
        "passed": base["passed"],
        "issues": base["issues"],
        "issue_codes": base.get("issue_codes", []),
        "capability_ledger": ledger_gate,
        "checks": base["checks"],
    }


class ReportQACheck(BaseModel):
    report_id: int = Field(ge=1)


@router.post("/qa/check", dependencies=[Depends(require_role("ADMIN", "MANAGER", "ENGINEER"))])
def qa_check_report(
    body: ReportQACheck,
    db: Session = Depends(get_db),
    user: dict = Depends(require_auth),
):
    """Run paper-quality QA check on a report. Returns pass/fail with issues."""
    org_id = resolved_org(None, user, db)
    report = db.execute(
        text(f"{_SELECT.replace('FROM tensor_reports', 'FROM tensor_reports r')} WHERE r.id = :rid AND r.organization_id = :org"),
        {"rid": body.report_id, "org": org_id},
    ).mappings().first()
    if not report:
        raise HTTPException(status_code=404, detail="report not found")
    
    result = _check_report_quality(report["markdown"], report["figures"] or [])
    
    # Update QA status
    qa_status = "passed" if result["passed"] else "rejected"
    rejection_reason = "; ".join(result["issues"]) if result["issues"] else None
    db.execute(
        text("""
            UPDATE tensor_reports
            SET qa_status = :status, qa_rejection_reason = :reason, qa_checked_at = now()
            WHERE id = :rid
        """),
        {"rid": body.report_id, "status": qa_status, "reason": rejection_reason},
    )
    db.commit()
    
    return {
        "report_id": body.report_id,
        "qa_status": qa_status,
        "passed": result["passed"],
        "issues": result["issues"],
        "checks": result["checks"],
    }


@router.get("/qa/pending", dependencies=[Depends(require_auth)])
def qa_pending_reports(
    limit: int = 50,
    db: Session = Depends(get_db),
    user: dict = Depends(require_auth),
):
    """List reports pending QA check."""
    org_id = resolved_org(None, user, db)
    rows = db.execute(
        text("""
            SELECT id, title, mission_id, scenario_key, created_at, created_by
            FROM tensor_reports
            WHERE organization_id = :org AND qa_status = 'pending'
            ORDER BY created_at ASC
            LIMIT :limit
        """),
        {"org": org_id, "limit": min(max(limit, 1), 200)},
    ).mappings().all()
    return {
        "items": [
            {
                "report_id": r["id"],
                "title": r["title"],
                "mission_id": r["mission_id"],
                "scenario_key": r["scenario_key"],
                "created_at": r["created_at"].isoformat() if r["created_at"] else None,
            }
            for r in rows
        ],
        "total": len(rows),
    }
