"""Tensor console: research progress reports — one report per completed
mission / stage, published with detailed case numbers, contour plots and
curves. Markdown body lives in the row; figure PNGs live on disk under
TENSOR_REPORT_FIGURE_DIR/<report_id>/<name>.png with [{name, caption}]
metadata in figures JSONB. The list endpoint omits the markdown body."""
from __future__ import annotations

import base64
import binascii
import json
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


@router.post("", dependencies=[Depends(require_role("ADMIN", "MANAGER", "ENGINEER"))])
def create_report(
    body: ReportCreate,
    db: Session = Depends(get_db),
    user: dict = Depends(require_auth),
):
    org_id = resolved_org(body.organization_id, user, db)
    figures_meta = [{"name": f.name, "caption": f.caption} for f in body.figures]
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
            "uid": user["id"],
        },
    ).mappings().first()
    report_id = row["id"]

    # Figures land on disk only after the row exists; any failure rolls the row
    # back so a report never references missing files (fail-closed).
    try:
        for fig in body.figures:
            raw = base64.b64decode(fig.png_base64, validate=True)
            if not raw.startswith(b"\x89PNG"):
                raise ValueError(f"{fig.name}: not a PNG payload")
            dest = FIGURE_DIR / str(report_id) / f"{fig.name}.png"
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(raw)
    except (ValueError, binascii.Error, OSError) as exc:
        db.execute(text("DELETE FROM tensor_reports WHERE id = :rid"), {"rid": report_id})
        db.commit()
        raise HTTPException(status_code=422, detail=f"figure rejected: {exc}") from exc

    db.commit()
    full = db.execute(text(f"{_SELECT} WHERE id = :rid"), {"rid": report_id}).mappings().first()
    return _row(full)


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
# Minimum fields for paper-quality reports:
# 1. Physical statement (物理表述): clear problem description with physics context
# 2. Traceable criteria (判据可溯源): method/validation with references
# 3. Figures (图): at least one figure or explicit justification for none
# 4. Limitations (局限): known limitations and scope of applicability

REQUIRED_SECTIONS = {
    "physics_statement": [
        r"(?i)(物理|背景|problem|background|物理背景|案例介绍)",
        r"(?i)(几何|参数|geometry|parameter|参数表)",
    ],
    "traceable_criteria": [
        r"(?i)(方法|method|流程|procedure|工具链|toolchain)",
        r"(?i)(验证|validation|对标|benchmark|参考|reference|文献|误差|error)",
    ],
    "figures": [
        r"(?i)(图|figure|云图|contour|曲线|curve|比较|comparison)",
    ],
    "limitations": [
        r"(?i)(局限|limitation|注意|note|适用|scope|范围|展望|outlook|future)",
    ],
}


def _check_report_quality(markdown: str, figures: list) -> dict:
    """Check if report meets paper-quality minimum requirements."""
    issues = []
    
    # Check physics statement
    physics_found = any(
        re.search(pattern, markdown)
        for patterns in REQUIRED_SECTIONS["physics_statement"]
        for pattern in [patterns]
    )
    if not physics_found:
        issues.append("missing_physics_statement: 缺少物理背景/案例介绍（需包含物理问题描述、几何参数）")
    
    # Check traceable criteria
    method_found = any(
        re.search(pattern, markdown)
        for pattern in REQUIRED_SECTIONS["traceable_criteria"][:1]
    )
    validation_found = any(
        re.search(pattern, markdown)
        for pattern in REQUIRED_SECTIONS["traceable_criteria"][1:]
    )
    if not method_found:
        issues.append("missing_method: 缺少方法/流程描述（需包含计算方法、关键设置、工具链）")
    if not validation_found:
        issues.append("missing_validation: 缺少验证/对标（需包含与基准/文献/解析解的比较，或声明无基准原因）")
    
    # Check figures
    has_figure_section = any(
        re.search(pattern, markdown)
        for pattern in REQUIRED_SECTIONS["figures"]
    )
    has_figures = len(figures) > 0
    if not has_figure_section and not has_figures:
        issues.append("missing_figures: 缺少图表（需包含场量云图/比较曲线，或纯文档任务需写明无图理由）")
    
    # Check limitations
    limitations_found = any(
        re.search(pattern, markdown)
        for pattern in REQUIRED_SECTIONS["limitations"]
    )
    if not limitations_found:
        issues.append("missing_limitations: 缺少局限与展望（需包含踩过的坑、适用范围、后续改进方向）")
    
    return {
        "passed": len(issues) == 0,
        "issues": issues,
        "checks": {
            "physics_statement": physics_found,
            "method": method_found,
            "validation": validation_found,
            "figures": has_figure_section or has_figures,
            "limitations": limitations_found,
        }
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
