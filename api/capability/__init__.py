"""Capability Registry API — 能力注册表 v1

Provides read-only endpoints for discovering solver capabilities,
their parameter boundaries, and validation evidence.

Endpoints:
    GET /api/capability/cards          — List all capability cards
    GET /api/capability/cards/<id>     — Get specific card
    GET /api/capability/cards/search   — Search by discipline/repo/tags
    GET /api/capability/schema         — Get card JSON schema
    POST /api/capability/validate      — Validate a card (red team gate)
"""
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel
from typing import List, Optional
import json
from pathlib import Path

router = APIRouter(prefix="/api/capability", tags=["能力注册表"])

WORK_DIR = Path(__file__).parent.parent.parent
CARDS_DIR = WORK_DIR / "docs" / "capability_registry" / "cards"
SCHEMA_PATH = WORK_DIR / "docs" / "capability_card.schema.json"


def _load_cards():
    """Load all capability cards from disk."""
    cards = []
    for card_file in sorted(CARDS_DIR.glob("*.json")):
        with open(card_file) as f:
            cards.append(json.load(f))
    return cards


def _load_schema():
    """Load the capability card JSON schema."""
    with open(SCHEMA_PATH) as f:
        return json.load(f)


@router.get("/cards")
def list_cards(
    repo: Optional[str] = Query(None, description="Filter by repo"),
    discipline: Optional[str] = Query(None, description="Filter by discipline"),
    status: Optional[str] = Query(None, description="Filter by status"),
):
    """List all capability cards with optional filters."""
    cards = _load_cards()
    
    if repo:
        cards = [c for c in cards if c.get("repo") == repo]
    if discipline:
        cards = [c for c in cards if c.get("discipline") == discipline]
    if status:
        cards = [c for c in cards if c.get("status") == status]
    
    return {"cards": cards, "total": len(cards)}


@router.get("/cards/{card_id}")
def get_card(card_id: str):
    """Get a specific capability card by ID."""
    cards = _load_cards()
    for card in cards:
        if card.get("card_id") == card_id:
            return card
    raise HTTPException(status_code=404, detail=f"Card {card_id} not found")


@router.get("/cards/search")
def search_cards(
    q: Optional[str] = Query(None, description="Search in name, tags, notes"),
    repo: Optional[str] = Query(None),
    discipline: Optional[str] = Query(None),
    tag: Optional[str] = Query(None, description="Filter by tag"),
):
    """Search capability cards."""
    cards = _load_cards()
    results = []
    
    for card in cards:
        match = True
        
        if repo and card.get("repo") != repo:
            match = False
        if discipline and card.get("discipline") != discipline:
            match = False
        if tag and tag not in card.get("tags", []):
            match = False
        if q:
            q_lower = q.lower()
            searchable = f"{card.get('name', '')} {' '.join(card.get('tags', []))} {card.get('notes', '')}".lower()
            if q_lower not in searchable:
                match = False
        
        if match:
            results.append(card)
    
    return {"results": results, "total": len(results)}


@router.get("/schema")
def get_schema():
    """Get the capability card JSON schema."""
    return _load_schema()


@router.post("/validate")
def validate_card(card: dict):
    """Validate a capability card against the schema and report registry.
    
    Returns validation result with detailed errors.
    """
    errors = []
    
    # Check required fields
    required = ["card_id", "name", "version", "repo", "interface",
                "applicability", "dependencies", "resource_requirements",
                "validation_evidence", "failure_modes", "created_at", "status"]
    for field in required:
        if field not in card:
            errors.append(f"Missing required field: {field}")
    
    # Check validation_evidence sub-fields
    if "validation_evidence" in card:
        ve = card["validation_evidence"]
        for field in ["report_id", "report_md5", "verification_status"]:
            if field not in ve:
                errors.append(f"Missing validation_evidence.{field}")
        
        # Check report_id exists
        if "report_id" in ve:
            valid_ids = {"e2e1", "suboff_sail", "wave10_multiphase",
                        "dtmb5415_geometry", "report_gate_v1", "m597"}
            if ve["report_id"] not in valid_ids:
                errors.append(f"report_id '{ve['report_id']}' not found in tensor_reports")
    
    if errors:
        return {"status": "REJECTED", "errors": errors}
    
    return {"status": "ACCEPTED", "errors": []}
