"""Alertmanager webhook receiver with persistent, idempotent alert audit."""

import hashlib
import hmac
import json
import os
from pathlib import Path
from typing import Dict, List, Optional

from fastapi import APIRouter, Depends, Header, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from database.config import get_db
from models.core import Alert


router = APIRouter(tags=["Alertmanager"])

_TOKEN_FILES = (
    "/run/secrets/alertmanager_webhook_token",
    "/app/.keys/alertmanager_webhook_token",
)
_ALLOWED_SEVERITIES = {"info", "warning", "critical"}
_MAX_ALERTS_PER_REQUEST = 500


class AlertmanagerAlert(BaseModel):
    status: Optional[str] = None
    labels: Dict[str, str] = Field(default_factory=dict)
    annotations: Dict[str, str] = Field(default_factory=dict)
    startsAt: Optional[str] = None
    endsAt: Optional[str] = None
    generatorURL: Optional[str] = None
    fingerprint: Optional[str] = None


class AlertmanagerPayload(BaseModel):
    version: Optional[str] = None
    status: str
    receiver: Optional[str] = None
    groupLabels: Dict[str, str] = Field(default_factory=dict)
    commonLabels: Dict[str, str] = Field(default_factory=dict)
    commonAnnotations: Dict[str, str] = Field(default_factory=dict)
    externalURL: Optional[str] = None
    alerts: List[AlertmanagerAlert] = Field(default_factory=list)


def _read_webhook_token() -> Optional[str]:
    token = os.getenv("ALERTMANAGER_WEBHOOK_TOKEN", "").strip()
    if token:
        return token

    configured_path = os.getenv("ALERTMANAGER_WEBHOOK_TOKEN_FILE", "").strip()
    paths = (configured_path,) if configured_path else _TOKEN_FILES
    for raw_path in paths:
        if not raw_path:
            continue
        try:
            token = Path(raw_path).read_text(encoding="utf-8").strip()
        except OSError:
            continue
        if token:
            return token
    return None


def _verify_authorization(authorization: Optional[str]) -> None:
    expected = _read_webhook_token()
    if not expected:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Alertmanager webhook receiver is not configured",
        )
    supplied = ""
    if authorization and authorization.startswith("Bearer "):
        supplied = authorization[7:].strip()
    if not supplied or not hmac.compare_digest(supplied, expected):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid Alertmanager webhook token",
            headers={"WWW-Authenticate": "Bearer"},
        )


def _fingerprint(alert: AlertmanagerAlert) -> str:
    if alert.fingerprint:
        return alert.fingerprint[:64]
    canonical_labels = json.dumps(
        alert.labels, sort_keys=True, separators=(",", ":"), ensure_ascii=True
    )
    return hashlib.sha256(canonical_labels.encode("utf-8")).hexdigest()


@router.post("/internal/alertmanager/webhook")
def receive_alertmanager_webhook(
    payload: AlertmanagerPayload,
    authorization: Optional[str] = Header(default=None),
    db: Session = Depends(get_db),
):
    """Receive Alertmanager v4 webhook events and persist their lifecycle."""
    _verify_authorization(authorization)
    if not payload.alerts:
        raise HTTPException(status_code=400, detail="Alertmanager payload has no alerts")
    if len(payload.alerts) > _MAX_ALERTS_PER_REQUEST:
        raise HTTPException(status_code=413, detail="Too many alerts in one request")

    result = {"received": len(payload.alerts), "created": 0, "updated": 0, "resolved": 0}
    try:
        for incoming in payload.alerts:
            fingerprint = _fingerprint(incoming)
            event_status = (incoming.status or payload.status).lower()
            active = (
                db.query(Alert)
                .filter(
                    Alert.source == "alertmanager",
                    Alert.entity_id == fingerprint,
                    Alert.is_resolved.is_(False),
                )
                .order_by(Alert.id.desc())
                .first()
            )

            if event_status == "resolved":
                if active:
                    from datetime import datetime

                    active.is_resolved = True
                    active.resolved_at = datetime.utcnow()
                    result["resolved"] += 1
                continue

            severity = incoming.labels.get("severity", "warning").lower()
            if severity not in _ALLOWED_SEVERITIES:
                severity = "warning"
            title = (
                incoming.annotations.get("summary")
                or incoming.labels.get("alertname")
                or "Prometheus alert"
            )[:256]
            description = incoming.annotations.get("description", "")
            alertname = incoming.labels.get("alertname", "unknown")
            service = incoming.labels.get("service", "unknown")
            message = f"[{alertname}] service={service} {description}".strip()

            if active:
                active.title = title
                active.severity = severity
                active.message = message
                result["updated"] += 1
            else:
                db.add(
                    Alert(
                        title=title,
                        severity=severity,
                        source="alertmanager",
                        entity_id=fingerprint,
                        message=message,
                    )
                )
                result["created"] += 1
        db.commit()
    except Exception:
        db.rollback()
        raise

    return {"status": "accepted", **result}


@router.get("/monitoring/alertmanager/status")
def get_alertmanager_receiver_status(db: Session = Depends(get_db)):
    """Return receiver readiness and persisted Alertmanager event counts."""
    total = db.query(Alert).filter(Alert.source == "alertmanager").count()
    active = (
        db.query(Alert)
        .filter(Alert.source == "alertmanager", Alert.is_resolved.is_(False))
        .count()
    )
    return {
        "receiver_configured": bool(_read_webhook_token()),
        "persisted_events": total,
        "active_events": active,
        "resolved_events": total - active,
    }
