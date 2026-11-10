"""HTTP surface for the capability ledger auto-audit engine (p262w1-N07).

Routes (mounted by the app factory at ``/api/v1``):

=================================================  =========================================
``POST /capabilities/{capability_id}/auto-audit``   run the evidence-chain audit, apply the
                                                   transition, append to the audit log
``GET  /capabilities/{capability_id}/level``       current level + last reason/chain digest
``GET  /capabilities/levels``                      all known capability levels
``GET  /capabilities/audit-log``                   last N chained audit entries
``GET  /capabilities/audit-log/verify``            re-read the log and verify the sha256 chain
=================================================  =========================================

Zero domain semantics: capability ids are opaque path parameters, every number in
the response comes from the engine (no hand-written values).
"""
from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from .auto_audit_engine import AutoAuditEngine

_UNKNOWN_LEVEL_HINT = "capability id has no level and no evidence directory"


def build_router(engine: AutoAuditEngine,
                 auth_dependency: Optional[Callable[..., Any]] = None) -> APIRouter:
    """Build the router around an :class:`AutoAuditEngine` instance."""
    router = APIRouter(tags=["能力台账自动升账"])
    deps = [Depends(auth_dependency)] if auth_dependency is not None else []

    @router.post("/capabilities/{capability_id}/auto-audit", dependencies=deps)
    def auto_audit(capability_id: str) -> Dict[str, Any]:
        try:
            outcome = engine.audit(capability_id)
        except Exception as exc:  # pragma: no cover - surfaced as 500 with reason
            raise HTTPException(status_code=500,
                                detail=f"auto-audit failed: {type(exc).__name__}: {exc}")
        return {"status": "ok", "outcome": outcome.as_dict()}

    @router.get("/capabilities/levels", dependencies=deps)
    def list_levels() -> Dict[str, Any]:
        return {"levels": engine.levels(), "spec_digest": engine.spec.digest()}

    @router.get("/capabilities/{capability_id}/level", dependencies=deps)
    def get_level(capability_id: str) -> Dict[str, Any]:
        record = engine._state["capabilities"].get(capability_id)  # noqa: SLF001 (read-only view)
        if record is None and not (engine.verified_dir / capability_id).is_dir():
            raise HTTPException(status_code=404, detail=_UNKNOWN_LEVEL_HINT)
        return {"capability_id": capability_id,
                "level": engine.level_of(capability_id),
                "record": record}

    @router.get("/capabilities/audit-log/verify", dependencies=deps)
    def verify_log() -> Dict[str, Any]:
        return engine.log.verify()

    @router.get("/capabilities/audit-log", dependencies=deps)
    def audit_log(limit: int = Query(default=10, ge=1, le=500)) -> Dict[str, Any]:
        entries: List[Dict[str, Any]] = engine.log.tail(limit)
        return {"entries": entries, "count": len(entries),
                "head_sha256": engine.log.head_sha256,
                "integrity": engine.log.verify()}

    return router


def mount(app, engine: AutoAuditEngine, *, prefix: str = "/api/v1",
          auth_dependency: Optional[Callable[..., Any]] = None) -> None:
    """Attach the router to a FastAPI app (`app.include_router` shorthand)."""
    app.include_router(build_router(engine, auth_dependency), prefix=prefix)