"""Tenant isolation API (platform-generic surface over services.tenant_isolation).

Every endpoint is a thin pass-through to the mechanism; decisions and reasons are
returned verbatim so callers can log them. Rejections map to HTTP 403/409 so a
client cannot mistake a denial for a success. Tenant facts come from the injected
registry document -- nothing domain-specific is hardcoded here.
"""
from __future__ import annotations

from typing import Dict, List, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

router = APIRouter(prefix="/tenant-isolation", tags=["tenant-isolation"])

_ENGINE = None


def get_engine():
    """Lazy singleton so importing the module never touches disk."""
    global _ENGINE
    if _ENGINE is None:
        from services.tenant_isolation import IsolationEngine

        _ENGINE = IsolationEngine()
    return _ENGINE


def set_engine(engine) -> None:
    """Test/DI hook."""
    global _ENGINE
    _ENGINE = engine


class ClaimReq(BaseModel):
    tenant: str
    resource: str
    amount: float
    cycle: str = "current"


class PathReq(BaseModel):
    tenant: str
    path: str


class GateReq(BaseModel):
    files: Dict[str, str]
    mode: str = "enforce"


class ReadReq(BaseModel):
    tenant: str
    path: str
    # "artifact" | "report"
    space: str = "artifact"


@router.get("/health")
def health() -> dict:
    return get_engine().self_check()


@router.get("/tenants")
def list_tenants() -> dict:
    eng = get_engine()
    return {
        "complete": eng.registry.completeness()["complete"],
        "tenants": [
            {
                "name": t.name,
                "repo": t.repo,
                "data_classification": t.data_classification,
                "resource_quota": dict(t.resource_quota),
            }
            for t in eng.registry.tenants()
        ],
    }


@router.post("/claim")
def claim(req: ClaimReq) -> dict:
    decision = get_engine().budget.claim(req.tenant, req.resource, req.amount, cycle=req.cycle)
    if not decision.accepted:
        raise HTTPException(status_code=409, detail=decision.to_dict())
    return decision.to_dict()


@router.post("/write-check")
def write_check(req: PathReq) -> dict:
    decision = get_engine().paths.check_write(req.tenant, req.path)
    if not decision.allowed:
        raise HTTPException(status_code=403, detail=decision.to_dict())
    return decision.to_dict()


@router.post("/gate/scan")
def gate_scan(req: GateReq) -> dict:
    gate = get_engine().domain_gate(mode=req.mode)
    verdict = gate.check_submission(req.files)
    if not verdict.allowed:
        raise HTTPException(status_code=403, detail=verdict.to_dict())
    return verdict.to_dict()


@router.post("/artifacts/read")
def artifact_read(req: ReadReq) -> dict:
    eng = get_engine()
    space = eng.reports if req.space == "report" else eng.artifacts
    result = space.read(req.tenant, req.path)
    if not result.allowed:
        raise HTTPException(status_code=403, detail=result.to_dict())
    return {
        "allowed": True,
        "reason": result.reason,
        "owner": result.owner,
        "bytes": len(result.data or b""),
    }


@router.get("/events")
def own_events(tenant: str, include_shared: bool = True) -> dict:
    rows: List[dict] = get_engine().events.read(tenant, include_shared=include_shared)
    return {"tenant": tenant, "count": len(rows), "events": rows}


@router.get("/budget")
def budget(tenant: str, cycle: str = "current") -> Optional[dict]:
    eng = get_engine()
    if eng.registry.get(tenant) is None:
        raise HTTPException(status_code=404, detail="unknown tenant")
    return eng.budget.snapshot(tenant, cycle=cycle)