"""Knowledge Engine API — FastAPI endpoints for knowledge retrieval and management.

Endpoints:
- POST /knowledge/entries — Create/update knowledge entry
- GET /knowledge/entries/{entry_id} — Get entry by ID
- GET /knowledge/search — Search entries
- GET /knowledge/stats — Get statistics
- GET /knowledge/provenance/{entry_id} — Trace provenance chain
- GET /knowledge/health — Health check
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

# Import knowledge engine
import sys
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from backend.modules.knowledge_engine import (
    KnowledgeStore,
    KnowledgeSearch,
    KnowledgeEntry,
    validate_entry,
)

router = APIRouter(prefix="/tensor/knowledge-engine", tags=["Knowledge Engine"])

# Global store instance (lazy init)
_store: Optional[KnowledgeStore] = None
_search: Optional[KnowledgeSearch] = None


def get_store() -> KnowledgeStore:
    """Get or create knowledge store."""
    global _store
    if _store is None:
        db_path = os.environ.get("KE_DB_PATH", "knowledge_engine.db")
        _store = KnowledgeStore(db_path)
    return _store


def get_search() -> KnowledgeSearch:
    """Get or create knowledge search."""
    global _search
    if _search is None:
        _search = KnowledgeSearch(get_store())
    return _search


# Request/Response models

class CreateEntryRequest(BaseModel):
    """Request to create a knowledge entry."""
    problem_statement: dict = Field(..., description="Problem statement contract")
    methodology: dict = Field(..., description="Methodology contract")
    evidence_chain: dict = Field(..., description="Evidence chain contract")
    conclusion: dict = Field(..., description="Conclusion contract")
    uncertainty: dict = Field(default_factory=dict, description="Uncertainty assessment")
    provenance: dict = Field(..., description="Provenance information")
    relations: list = Field(default_factory=list, description="Relations to other entries")


class EntryResponse(BaseModel):
    """Knowledge entry response."""
    entry_id: str
    version: str
    created_at: str
    problem_statement: dict
    methodology: dict
    evidence_chain: dict
    conclusion: dict
    uncertainty: dict
    provenance: dict
    relations: list


class SearchResponse(BaseModel):
    """Search result response."""
    entry_id: str
    score: float
    highlight: str
    entry: dict


class StatsResponse(BaseModel):
    """Statistics response."""
    total: int
    by_domain: dict
    by_outcome: dict
    by_source_type: dict
    by_repo: dict


class HealthResponse(BaseModel):
    """Health check response."""
    status: str
    store_count: int
    schema_version: str
    uptime_ms: float


# Endpoints

@router.post("/entries", response_model=dict)
async def create_entry(request: CreateEntryRequest):
    """Create or update a knowledge entry."""
    store = get_store()

    # Build entry
    entry = KnowledgeEntry(
        entry_id="",  # Will be computed
        problem_statement=request.problem_statement,
        methodology=request.methodology,
        evidence_chain=request.evidence_chain,
        conclusion=request.conclusion,
        uncertainty=request.uncertainty,
        provenance=request.provenance,
        relations=request.relations,
    )
    entry.entry_id = entry.compute_id()

    # Validate
    entry_dict = entry.to_dict()
    is_valid, errors = validate_entry(entry_dict)
    if not is_valid:
        raise HTTPException(status_code=400, detail={"errors": errors})

    # Store
    try:
        entry_id = store.upsert(entry)
        return {
            "ok": True,
            "entry_id": entry_id,
            "store_count": store.count(),
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/entries/{entry_id}", response_model=EntryResponse)
async def get_entry(entry_id: str):
    """Get a knowledge entry by ID."""
    store = get_store()
    entry = store.get(entry_id)
    if not entry:
        raise HTTPException(status_code=404, detail="Entry not found")
    return entry.to_dict()


@router.delete("/entries/{entry_id}")
async def delete_entry(entry_id: str):
    """Delete a knowledge entry."""
    store = get_store()
    deleted = store.delete(entry_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Entry not found")
    return {"ok": True, "entry_id": entry_id}


@router.get("/search", response_model=list[SearchResponse])
async def search_entries(
    q: str = Query(..., description="Search query"),
    limit: int = Query(20, ge=1, le=100),
    domain: Optional[str] = Query(None),
    outcome: Optional[str] = Query(None),
    source_type: Optional[str] = Query(None),
    min_confidence: Optional[str] = Query(None),
):
    """Search for knowledge entries."""
    search = get_search()
    results = search.search(
        query=q,
        limit=limit,
        domain=domain,
        outcome=outcome,
        source_type=source_type,
        min_confidence=min_confidence,
    )
    return [
        {
            "entry_id": r.entry.entry_id,
            "score": r.score,
            "highlight": r.highlight,
            "entry": r.entry.to_dict(),
        }
        for r in results
    ]


@router.get("/stats", response_model=StatsResponse)
async def get_stats():
    """Get knowledge base statistics."""
    search = get_search()
    dist = search.get_knowledge_distribution()
    return dist


@router.get("/provenance/{entry_id}")
async def get_provenance(entry_id: str):
    """Trace provenance chain for an entry."""
    search = get_search()
    chain = search.trace_provenance(entry_id)
    if not chain:
        raise HTTPException(status_code=404, detail="Entry not found")
    return {"entry_id": entry_id, "chain": chain}


@router.get("/uncertainty/{entry_id}")
async def get_uncertainty(entry_id: str):
    """Get propagated uncertainty for an entry."""
    search = get_search()
    uncertainty = search.propagate_uncertainty(entry_id)
    return {"entry_id": entry_id, "uncertainty": uncertainty}


@router.get("/heatmap")
async def get_heatmap(days: int = Query(30, ge=1, le=365)):
    """Get retrieval heatmap data."""
    search = get_search()
    return search.get_retrieval_heatmap(days)


@router.get("/integrity")
async def check_integrity():
    """Check provenance integrity."""
    store = get_store()
    return store.verify_provenance_integrity()


@router.get("/health", response_model=HealthResponse)
async def health_check():
    """Health check endpoint."""
    store = get_store()
    return {
        "status": "healthy",
        "store_count": store.count(),
        "schema_version": "1.0",
        "uptime_ms": 0.0,  # Would need to track start time
    }


@router.get("/entries")
async def list_entries(
    limit: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    domain: Optional[str] = Query(None),
    outcome: Optional[str] = Query(None),
    source_type: Optional[str] = Query(None),
    repo: Optional[str] = Query(None),
):
    """List knowledge entries with filters."""
    store = get_store()
    entries = store.list_entries(
        limit=limit,
        offset=offset,
        domain=domain,
        outcome=outcome,
        source_type=source_type,
        repo=repo,
    )
    return [e.to_dict() for e in entries]
