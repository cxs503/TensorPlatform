"""HTTP API for the capability evidence auto-upgrade pipeline (P218-N01).

Routes (mounted by :func:`create_app`, or embedded anywhere FastAPI lives):

===============================  ===========================================
``GET  /capabilities``            slots + current level
``POST /capabilities``            declare/upsert a slot
``POST /evidence``                ingest evidence → auto re-adjudication
``POST /capabilities/{id}/rejudge`` manual re-adjudication (C2 control)
``GET  /capabilities/{id}/chain`` evidence → criteria → runs → history chain
``GET  /capabilities/{id}/history`` upgrade history (C3)
``GET  /coverage``                WITHHELD slots lacking evidence (C1)
``GET  /events``                  ingest event log incl. measured latency
``GET  /health``                  pipeline integrity summary
===============================  ===========================================

Every route is domain-free: payloads carry capability ids, kinds and metric bags
only.  Failures are explicit (404 for unknown slot, 409 for a rejected registry
entry) — no silent defaults.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Optional

from fastapi import APIRouter, FastAPI, HTTPException
from pydantic import BaseModel, Field

from .engine import RejudgeEngine
from .pipeline import EvidencePipeline
from .registry import apply_registry_path
from .store import Store


class CapabilityIn(BaseModel):
    capability_id: str
    label: str = ""
    initial_level: str = "WITHHELD"
    metadata: Dict[str, Any] = Field(default_factory=dict)


class EvidenceIn(BaseModel):
    capability_id: str
    kind: str
    metrics: Dict[str, Any] = Field(default_factory=dict)
    observed_level: Optional[str] = None
    source: Dict[str, Any] = Field(default_factory=dict)
    payload: Dict[str, Any] = Field(default_factory=dict)
    evidence_id: Optional[str] = None


def build_router(pipeline: EvidencePipeline, store: Store) -> APIRouter:
    router = APIRouter()

    @router.get("/capabilities")
    def list_capabilities() -> Dict[str, Any]:
        return {"capabilities": [s.as_dict() for s in store.list_slots()]}

    @router.post("/capabilities")
    def create_capability(payload: CapabilityIn) -> Dict[str, Any]:
        slot = store.upsert_slot(
            capability_id=payload.capability_id,
            label=payload.label,
            initial_level=payload.initial_level,
            metadata=payload.metadata,
        )
        return {"capability": slot.as_dict()}

    @router.post("/evidence")
    def ingest_evidence(payload: EvidenceIn) -> Dict[str, Any]:
        try:
            return pipeline.ingest(
                capability_id=payload.capability_id,
                kind=payload.kind,
                metrics=payload.metrics,
                observed_level=payload.observed_level,
                source=payload.source,
                payload=payload.payload,
                evidence_id=payload.evidence_id,
            )
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @router.post("/capabilities/{capability_id}/rejudge")
    def rejudge(capability_id: str) -> Dict[str, Any]:
        try:
            run = pipeline.engine.judge(capability_id, trigger="manual")
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return {"run": run}

    @router.get("/capabilities/{capability_id}/chain")
    def chain(capability_id: str) -> Dict[str, Any]:
        try:
            return pipeline.chain(capability_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @router.get("/capabilities/{capability_id}/history")
    def history(capability_id: str) -> Dict[str, Any]:
        if store.get_slot(capability_id) is None:
            raise HTTPException(status_code=404, detail=capability_id)
        return {"history": store.list_history(capability_id)}

    @router.get("/coverage")
    def coverage() -> Dict[str, Any]:
        return pipeline.coverage()

    @router.get("/events")
    def events(capability_id: Optional[str] = None) -> Dict[str, Any]:
        return {"events": store.list_events(capability_id)}

    @router.get("/health")
    def health() -> Dict[str, Any]:
        events_all = store.list_events()
        measured = [e["latency_ms"] for e in events_all if e.get("latency_ms") is not None]
        return {
            "status": "OK",
            "stats": store.stats(),
            "events_measured": len(measured),
            "max_latency_ms": max(measured) if measured else None,
            "coverage": pipeline.coverage(),
            "deadline_ms": pipeline.deadline_seconds * 1000.0,
        }

    return router


def create_app(
    db_path: Optional[Path | str] = None,
    registry_path: Optional[Path | str] = None,
    deadline_seconds: float = 1.0,
) -> FastAPI:
    """Build a standalone FastAPI app (used by CLI and by the test suite)."""
    store = Store(Path(db_path) if db_path else None)
    apply_registry_path(store, registry_path)
    pipeline = EvidencePipeline(
        store, engine=RejudgeEngine(store), deadline_seconds=deadline_seconds
    )
    app = FastAPI(title="capability evidence auto-upgrade pipeline", version="1.0.0")
    app.include_router(build_router(pipeline, store))
    app.state.store = store
    app.state.pipeline = pipeline
    return app