"""FastAPI adapter for the auto-upgrader (thin: no rule lives here).

The router exposes the mechanism to the platform without duplicating any policy:
``/status`` and ``/verify`` are read paths over the ledger, ``/runs`` and
``/audit-snapshot`` are the two write paths the mechanism listens to.

Mount with::

    from services.capability_auto_upgrader.api import create_router
    app.include_router(create_router("artifacts/capability_auto_upgrader"),
                       prefix="/api/v1/capability-auto-upgrader")
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict, Optional

from fastapi import APIRouter, Body, HTTPException
from pydantic import BaseModel, Field

from .model import EvidenceError, EvidenceRun
from .policy import UpgradePolicy
from .upgrader import CapabilityAutoUpgrader

DEFAULT_DIR_ENV = "CAPABILITY_AUTO_UPGRADER_DIR"


class TestPayload(BaseModel):
    passed: int = Field(ge=0)
    failed: int = Field(default=0, ge=0)
    errors: int = Field(default=0, ge=0)
    skipped: int = Field(default=0, ge=0)
    report_sha256: Optional[str] = None


class BenchmarkPayload(BaseModel):
    measured: float
    reference: float
    n_samples: int = Field(ge=1)
    source_sha256: Optional[str] = None


class RunPayload(BaseModel):
    capability_id: str
    seq: int = Field(ge=1)
    tests: TestPayload
    benchmark: BenchmarkPayload
    run_id: Optional[str] = None


def _open(root: Path | str, policy: Optional[UpgradePolicy] = None) -> CapabilityAutoUpgrader:
    return CapabilityAutoUpgrader(root, policy=policy or UpgradePolicy())


def create_router(state_dir: Optional[str] = None) -> APIRouter:
    router = APIRouter(tags=["capability-auto-upgrader"])
    root = state_dir or os.environ.get(DEFAULT_DIR_ENV, "artifacts/capability_auto_upgrader")

    @router.get("/status")
    def status() -> Dict[str, Any]:
        with _open(root) as up:
            return {"policy": up.policy.as_dict(), "levels": up.levels(), "capabilities": up.status()}

    @router.get("/events")
    def events(capability_id: Optional[str] = None) -> Dict[str, Any]:
        with _open(root) as up:
            items = up.history(capability_id) if capability_id else up.ledger.events()
            return {"count": len(items), "items": items}

    @router.get("/verify")
    def verify() -> Dict[str, Any]:
        with _open(root) as up:
            return up.verify()

    @router.post("/runs")
    def record_run(payload: RunPayload) -> Dict[str, Any]:
        body = payload.model_dump()
        try:
            run = EvidenceRun.from_dict(body)
        except EvidenceError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        with _open(root) as up:
            try:
                return up.record_run(run)
            except EvidenceError as exc:
                raise HTTPException(status_code=409, detail=str(exc)) from exc

    @router.post("/audit-snapshot")
    def audit_snapshot(payload: Any = Body(...)) -> Dict[str, Any]:
        with _open(root) as up:
            try:
                return up.ingest_audit_snapshot(payload)
            except EvidenceError as exc:
                raise HTTPException(status_code=422, detail=str(exc)) from exc

    return router