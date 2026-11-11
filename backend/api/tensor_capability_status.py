"""Capability status API (p488w1-n01).

Provides real-time capability status endpoint at /tensor/capability_status.

C3 acceptance: returns JSON with grade, last_verified, error_history per capability.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/tensor", tags=["能力状态"])


# ---------------------------------------------------------------------------
# Response models
# ---------------------------------------------------------------------------

class ErrorStats(BaseModel):
    count: int = 0
    mean: Optional[float] = None
    max: Optional[float] = None
    min: Optional[float] = None
    std: Optional[float] = None


class CapabilityStatus(BaseModel):
    capability_id: str
    grade: str
    last_verified: Optional[str] = None
    error_history: ErrorStats = Field(default_factory=ErrorStats)
    api_path: Optional[str] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)


class CapabilityStatusResponse(BaseModel):
    timestamp: str
    total_capabilities: int
    grade_distribution: Dict[str, int]
    capabilities: Dict[str, CapabilityStatus]


# ---------------------------------------------------------------------------
# In-memory store (production would use DB; this is the platform-level skeleton)
# ---------------------------------------------------------------------------

class _CapabilityStore:
    """Simple in-memory store for capability status.

    In production, this would be backed by the tensor_capability_deltas table
    and the capability_auto_upgrader ledger. For the platform skeleton, we
    use an in-memory dict that can be populated via the API.
    """

    def __init__(self):
        self._data: Dict[str, Dict[str, Any]] = {}
        self._initialized = False

    def _ensure_initialized(self) -> None:
        if self._initialized:
            return
        # Seed with platform-level capability placeholders (zero domain semantics)
        # These are generic capability IDs, not ship-specific terms
        seed_capabilities = [
            {"id": "calc_module_A", "grade": "WITHHELD", "api_path": "/api/v1/calculations/resistance"},
            {"id": "calc_module_B", "grade": "MOCK", "api_path": "/api/v1/calculations/propulsion"},
            {"id": "calc_module_C", "grade": "WITHHELD", "api_path": "/api/v1/calculations/seakeeping"},
            {"id": "calc_module_D", "grade": "STUB", "api_path": "/api/v1/calculations/stability"},
            {"id": "calc_module_E", "grade": "WITHHELD", "api_path": "/api/v1/calculations/hydrostatics"},
            {"id": "calc_module_F", "grade": "REAL", "api_path": "/api/v1/calculations/maneuvering"},
            {"id": "calc_module_G", "grade": "WITHHELD", "api_path": "/api/v1/green_navigation/optimize"},
        ]
        now = datetime.now(timezone.utc).isoformat()
        for cap in seed_capabilities:
            self._data[cap["id"]] = {
                "capability_id": cap["id"],
                "grade": cap["grade"],
                "last_verified": now,
                "error_history": {"count": 0, "mean": None, "max": None, "min": None, "std": None},
                "api_path": cap.get("api_path"),
                "metadata": {},
            }
        self._initialized = True

    def get_all(self) -> Dict[str, Dict[str, Any]]:
        self._ensure_initialized()
        return dict(self._data)

    def get_one(self, capability_id: str) -> Optional[Dict[str, Any]]:
        self._ensure_initialized()
        return self._data.get(capability_id)

    def update(self, capability_id: str, updates: Dict[str, Any]) -> None:
        self._ensure_initialized()
        if capability_id in self._data:
            self._data[capability_id].update(updates)

    def register(self, capability_id: str, grade: str, api_path: Optional[str] = None) -> None:
        self._ensure_initialized()
        self._data[capability_id] = {
            "capability_id": capability_id,
            "grade": grade,
            "last_verified": datetime.now(timezone.utc).isoformat(),
            "error_history": {"count": 0, "mean": None, "max": None, "min": None, "std": None},
            "api_path": api_path,
            "metadata": {},
        }


_store = _CapabilityStore()


def get_store() -> _CapabilityStore:
    """Get the module-level store (for testing/dependency injection)."""
    return _store


def reset_store() -> None:
    """Reset the store (for testing)."""
    global _store
    _store = _CapabilityStore()


# ---------------------------------------------------------------------------
# API endpoints
# ---------------------------------------------------------------------------

@router.get("/capability_status", response_model=CapabilityStatusResponse)
def get_capability_status(
    grade_filter: Optional[str] = Query(None, description="Filter by evidence grade"),
    capability_id: Optional[str] = Query(None, description="Get specific capability"),
):
    """Return real-time capability status.

    C3: Returns JSON with grade, last_verified, error_history for each capability.
    """
    store = get_store()

    if capability_id:
        cap = store.get_one(capability_id)
        if cap is None:
            raise HTTPException(status_code=404, detail=f"Capability not found: {capability_id}")
        caps = {capability_id: cap}
    else:
        caps = store.get_all()

    # Apply grade filter
    if grade_filter:
        caps = {k: v for k, v in caps.items() if v["grade"] == grade_filter}

    # Build grade distribution
    grade_dist: Dict[str, int] = {}
    for v in caps.values():
        g = v["grade"]
        grade_dist[g] = grade_dist.get(g, 0) + 1

    # Build response
    capabilities = {}
    for cid, data in caps.items():
        eh = data.get("error_history", {})
        capabilities[cid] = CapabilityStatus(
            capability_id=cid,
            grade=data["grade"],
            last_verified=data.get("last_verified"),
            error_history=ErrorStats(**eh) if eh else ErrorStats(),
            api_path=data.get("api_path"),
            metadata=data.get("metadata", {}),
        )

    return CapabilityStatusResponse(
        timestamp=datetime.now(timezone.utc).isoformat(),
        total_capabilities=len(capabilities),
        grade_distribution=grade_dist,
        capabilities=capabilities,
    )


@router.post("/capability_status/register")
def register_capability(
    capability_id: str = Query(..., description="Capability identifier"),
    grade: str = Query("NOT_SUPPORTED", description="Initial evidence grade"),
    api_path: Optional[str] = Query(None, description="Associated API path"),
):
    """Register a new capability for tracking."""
    store = get_store()
    store.register(capability_id, grade, api_path)
    return {"status": "registered", "capability_id": capability_id, "grade": grade}


@router.put("/capability_status/{capability_id}/grade")
def update_capability_grade(
    capability_id: str,
    new_grade: str = Query(..., description="New evidence grade"),
    reason: str = Query("", description="Reason for grade change"),
):
    """Update the evidence grade of a capability."""
    store = get_store()
    cap = store.get_one(capability_id)
    if cap is None:
        raise HTTPException(status_code=404, detail=f"Capability not found: {capability_id}")

    old_grade = cap["grade"]
    store.update(capability_id, {
        "grade": new_grade,
        "last_verified": datetime.now(timezone.utc).isoformat(),
    })

    return {
        "status": "updated",
        "capability_id": capability_id,
        "old_grade": old_grade,
        "new_grade": new_grade,
        "reason": reason,
    }
