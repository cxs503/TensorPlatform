"""Report write-read visibility & lost-write self-healing (HTTP surface).

Endpoints (router prefix ``/tensor/reports``, alongside the report CRUD router):

=========================================  ====  ==============================
path                                       verb  purpose
=========================================  ====  ==============================
``/{report_id}/readback``                  POST  assert the row a POST returned is
                                                 readable, field by field, right now
``/visibility/scan``                       POST  diff submitted ids (write-side
                                                 ledger) against readable ids
``/visibility/heal``                       POST  re-send detected losses and prove
                                                 they became readable
``/visibility/ledger``                     GET   ledger summary + hash-chain state
=========================================  ====  ==============================

The heavy lifting lives in :mod:`services.report_visibility` (pure contract /
probe / healer); this module only binds them to SQLAlchemy.  It carries no
domain semantics: reports are opaque payloads here.
"""
from __future__ import annotations

import binascii
import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.orm import Session

from api.deps import require_auth, require_role
from api.domain.tensor_common import resolved_org
from api.tensor_reports import (
    ReportCreate,
    _insert_report_row,
    _row,
    _SELECT,
    _write_figure_files,
)
from database.config import get_db
from services.report_ownership import FigureRejected, validate_png_payload
from services.report_visibility import (
    HealPolicy,
    ReadbackVerdict,
    ReportVisibilityService,
    assert_readback,
    ledger_from_env,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/tensor/reports", tags=["报告写读可见性"])

_WRITE_ROLES = ("ADMIN", "MANAGER", "ENGINEER")


def _fetch_row(db: Session, report_id: int, org_id: int) -> dict | None:
    row = db.execute(
        text(f"{_SELECT} WHERE id = :rid AND organization_id = :org"),
        {"rid": report_id, "org": org_id},
    ).mappings().first()
    return _row(row) if row else None


# ───────────────────────── readback assertion ─────────────────────────

@router.post("/{report_id}/readback", dependencies=[Depends(require_role(*_WRITE_ROLES))])
def readback_report(
    report_id: int,
    body: ReportCreate,
    db: Session = Depends(get_db),
    user: dict = Depends(require_auth),
):
    """Assert that ``report_id`` is readable *and* identical to ``body``.

    ``body`` is the same schema the write used, so a caller can replay its POST
    payload verbatim.  200 = contract holds; 409 = the write is not visible /
    not identical (the machine code list says which field and why).
    """
    org_id = resolved_org(None, user, db)
    observed = _fetch_row(db, report_id, org_id)
    payload = body.model_dump()
    verdict: ReadbackVerdict = assert_readback(payload, observed)

    service = ReportVisibilityService(ledger=ledger_from_env())
    verification = service.after_write(payload, report_id, observed=observed)

    result = {
        "report_id": report_id,
        "ok": verdict.ok,
        "readback": verdict.as_dict(),
        "submission_id": verification.submission_id,
        "ledger_ok": verification.ledger_ok,
        "ledger_error": verification.ledger_error,
    }
    if not verdict.ok:
        raise HTTPException(status_code=409, detail=result,
                            headers={"X-Error-Code": "WRITE_READ_VISIBILITY_VIOLATION"})
    return result


# ───────────────────────── lost-write probe ─────────────────────────

class ScanRequest(BaseModel):
    submitted_ids: list[int] | None = Field(default=None, max_length=5000)
    grace_s: float = Field(default=0.0, ge=0.0, le=3600.0)


@router.post("/visibility/scan", dependencies=[Depends(require_role(*_WRITE_ROLES))])
def scan_lost_reports(
    body: ScanRequest | None = None,
    db: Session = Depends(get_db),
    user: dict = Depends(require_auth),
):
    """Scan submitted-vs-readable diff; every hit is recorded in the ledger."""
    org_id = resolved_org(None, user, db)
    body = body or ScanRequest()
    ledger = ledger_from_env()
    service = ReportVisibilityService(
        ledger=ledger,
        fetch=lambda rid: _fetch_row(db, rid, org_id),
        grace_s=body.grace_s,
    )
    result = service.scan(submitted_ids=body.submitted_ids, grace_s=body.grace_s)
    return {"scan": result.as_dict(), "ledger": ledger.summary()}


# ───────────────────────── self-healing ─────────────────────────

class HealRequest(BaseModel):
    submission_ids: list[str] | None = Field(default=None, max_length=2000)
    max_attempts: int = Field(default=3, ge=1, le=10)


def _resend_factory(db: Session, org_id: int, uid: int):
    """Build the re-send callable: payload snapshot → a fresh readable row."""

    def resend(payload: dict) -> int | None:
        fields = {k: v for k, v in payload.items()
                  if k in ReportCreate.model_fields and k != "organization_id"}
        try:
            body = ReportCreate(**fields)
        except Exception as exc:  # schema drift in a stored snapshot
            logger.warning("heal: stored payload rejected: %s", exc)
            return None

        decoded = []
        for fig in body.figures:
            try:
                decoded.append((fig, validate_png_payload(fig.name, fig.png_base64)))
            except FigureRejected as exc:
                logger.warning("heal: figure rejected: %s", exc)
                return None

        target_org = payload.get("organization_id") or org_id
        author = payload.get("created_by") or uid
        figures_meta = [{"name": f.name, "caption": f.caption} for f in body.figures]
        report_id = _insert_report_row(db, org_id=target_org, uid=author,
                                       body=body, figures_meta=figures_meta)
        try:
            _write_figure_files(report_id, decoded)
        except (ValueError, binascii.Error, OSError) as exc:
            db.execute(text("DELETE FROM tensor_reports WHERE id = :rid"),
                       {"rid": report_id})
            db.commit()
            logger.warning("heal: figure write failed: %s", exc)
            return None
        db.commit()
        return report_id

    return resend


@router.post("/visibility/heal", dependencies=[Depends(require_role(*_WRITE_ROLES))])
def heal_lost_reports(
    body: HealRequest | None = None,
    db: Session = Depends(get_db),
    user: dict = Depends(require_auth),
):
    """Re-send every detected loss, verify it is readable, alert the outcome."""
    org_id = resolved_org(None, user, db)
    body = body or HealRequest()
    ledger = ledger_from_env()
    service = ReportVisibilityService(
        ledger=ledger,
        fetch=lambda rid: _fetch_row(db, rid, org_id),
        resend=_resend_factory(db, org_id, user["id"]),
    )
    report = service.heal(policy=HealPolicy(max_attempts=body.max_attempts),
                          submission_ids=body.submission_ids)
    return {"heal": report.as_dict(), "alerts": service.alerts,
            "ledger": ledger.summary()}


# ───────────────────────── ledger / audit ─────────────────────────

@router.get("/visibility/ledger", dependencies=[Depends(require_auth)])
def ledger_state(limit: int = Query(default=50, ge=1, le=500)):
    """Ledger summary, chain integrity and the most recent entries."""
    ledger = ledger_from_env()
    summary = ledger.summary()
    recent = [e.as_dict() for e in ledger.entries[-limit:]]
    return {"summary": summary, "recent": recent}