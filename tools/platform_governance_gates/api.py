"""HTTP surface for the three platform governance gates (p338w3-n07).

Routes (mounted by the platform under ``/api/v1/platform-governance``):

======================================  ====================================
``GET  /health``                        thresholds, requirements, schema
``POST /ledger/scan``                   completeness -> ledger transitions
``POST /report-gate/check``             four-way report fingerprint gate
``POST /drift/scan``                    campaign-node drift + convergence
======================================  ====================================

All routes are stateless and domain-free: capability ids, mission states and
report bodies arrive in the request.  Every response carries ``latency_ms``
measured inside the handler, so the <=100 ms criterion is reproducible from the
wire instead of from a benchmark script.  A malformed request is a 422 (FastAPI)
and an internal inconsistency is a 500 -- never a silent default.
"""
from __future__ import annotations

import hashlib
import time
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from .evidence import (
    REQUIREMENTS,
    CapabilityEvidence,
    Contradiction,
    EvidenceItem,
    canonical_json,
    evaluate_all,
)
from .ledger_guard import LedgerGuard, Thresholds
from .report_fingerprint import ReportEnvelope, ReportFingerprintGate
from .status_drift import DriftDetector, MissionState, NodeState

SCHEMA = "platform-governance-gates-api/1.0"

router = APIRouter(prefix="/platform-governance", tags=["platform-governance"])


class EvidenceItemIn(BaseModel):
    kind: str
    ref: str = ""
    ok: bool = True
    sha256: str = ""
    declared_sha256: str = ""
    independent: bool = False
    detail: Dict[str, Any] = Field(default_factory=dict)


class ContradictionIn(BaseModel):
    kind: str = "declared"
    ref: str = ""
    reason: str = ""


class CapabilityIn(BaseModel):
    capability_id: str
    current_level: str = "WITHHELD"
    items: List[EvidenceItemIn] = Field(default_factory=list)
    contradictions: List[ContradictionIn] = Field(default_factory=list)


class LedgerScanRequest(BaseModel):
    capabilities: List[CapabilityIn]
    promote: float = 0.8
    demote: float = 0.5


class ReportIn(BaseModel):
    report_id: Any
    mission_id: Any
    body: str
    metadata: Dict[str, Any] = Field(default_factory=dict)
    declared_body_sha256: str = ""
    declared_metadata_sha256: str = ""


class ReportGateRequest(BaseModel):
    reports: List[ReportIn]


class NodeIn(BaseModel):
    node_id: Any
    status: str
    mission_id: Optional[Any] = None


class MissionIn(BaseModel):
    mission_id: Any
    status: str = ""
    result: Dict[str, Any] = Field(default_factory=dict)


class DriftScanRequest(BaseModel):
    nodes: List[NodeIn]
    missions: List[MissionIn]
    auto_converge: bool = True
    alert_threshold: float = 0.3


def _item_digest(item: EvidenceItemIn) -> str:
    if item.sha256:
        return item.sha256.lower()
    payload = {
        "kind": item.kind,
        "ref": item.ref,
        "ok": item.ok,
        "independent": item.independent,
        "detail": item.detail,
    }
    return hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()


def _bundle(cap: CapabilityIn) -> CapabilityEvidence:
    items = [
        EvidenceItem(
            kind=i.kind,
            ref=i.ref,
            ok=i.ok,
            sha256=_item_digest(i),
            declared_sha256=i.declared_sha256,
            independent=i.independent,
            detail=i.detail,
        )
        for i in cap.items
    ]
    contradictions = [
        Contradiction(kind=c.kind, ref=c.ref, reason=c.reason)
        for c in cap.contradictions
    ]
    return CapabilityEvidence(
        capability_id=cap.capability_id,
        current_level=cap.current_level,
        items=items,
        contradictions=contradictions,
    )


@router.get("/health")
def health() -> Dict[str, Any]:
    started = time.perf_counter()
    return {
        "schema": SCHEMA,
        "requirements": list(REQUIREMENTS),
        "default_thresholds": Thresholds().as_dict(),
        "guards": ["ledger", "report_fingerprint", "status_drift"],
        "latency_ms": round((time.perf_counter() - started) * 1000, 3),
    }


@router.post("/ledger/scan")
def ledger_scan(req: LedgerScanRequest) -> Dict[str, Any]:
    started = time.perf_counter()
    try:
        thresholds = Thresholds(promote=req.promote, demote=req.demote)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    bundles = [_bundle(c) for c in req.capabilities]
    reports = evaluate_all(bundles)
    guard = LedgerGuard(thresholds=thresholds)
    result = guard.run(bundles)
    payload = result.as_dict()
    payload["completeness"] = {k: v.as_dict() for k, v in reports.items()}
    payload["latency_ms"] = round((time.perf_counter() - started) * 1000, 3)
    return payload


@router.post("/report-gate/check")
def report_gate_check(req: ReportGateRequest) -> Dict[str, Any]:
    started = time.perf_counter()
    envelopes = [
        ReportEnvelope(
            report_id=r.report_id,
            mission_id=r.mission_id,
            body=r.body,
            metadata=r.metadata,
            declared_body_sha256=r.declared_body_sha256,
            declared_metadata_sha256=r.declared_metadata_sha256,
        )
        for r in req.reports
    ]
    result = ReportFingerprintGate().check(envelopes).as_dict()
    result["latency_ms"] = round((time.perf_counter() - started) * 1000, 3)
    return result


@router.post("/drift/scan")
def drift_scan(req: DriftScanRequest) -> Dict[str, Any]:
    started = time.perf_counter()
    try:
        detector = DriftDetector(auto_converge=req.auto_converge,
                                 alert_threshold=req.alert_threshold)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    nodes = [NodeState(node_id=n.node_id, status=n.status,
                       mission_id=n.mission_id) for n in req.nodes]
    missions = [MissionState(mission_id=m.mission_id, status=m.status,
                             result=m.result) for m in req.missions]
    result = detector.scan(nodes, missions).as_dict()
    result["latency_ms"] = round((time.perf_counter() - started) * 1000, 3)
    return result


def build_router() -> APIRouter:
    """Return the (already populated) router for embedding in a FastAPI app."""
    return router