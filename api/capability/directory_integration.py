"""Agent Directory Integration — capabilities segment.

Adds a /directory endpoint with capabilities section, or standalone
read-only endpoint for capability discovery before seat claim.

Usage: Include this router in the main FastAPI app.
"""
from fastapi import APIRouter, Query
from typing import Optional, List
import json
from pathlib import Path

router = APIRouter(prefix="/directory", tags=["Agent Directory"])

WORK_DIR = Path(__file__).parent.parent.parent
CARDS_DIR = WORK_DIR / "docs" / "capability_registry" / "cards"


def _load_cards():
    cards = []
    for card_file in sorted(CARDS_DIR.glob("*.json")):
        with open(card_file) as f:
            cards.append(json.load(f))
    return cards


@router.get("")
def get_directory(
    include_capabilities: bool = Query(True, description="Include capabilities section"),
    repo: Optional[str] = Query(None),
    discipline: Optional[str] = Query(None),
):
    """Get agent directory with optional capabilities section.
    
    Returns agent listings + capabilities for seat claim discovery.
    """
    directory = {
        "agents": [
            {"id": "copilot", "name": "Platform Copilot", "status": "active", "role": "read-only"},
            {"id": "verifier", "name": "Research Verifier", "status": "active", "role": "validation"},
            {"id": "planner", "name": "Mission Planner", "status": "active", "role": "planning"},
        ],
        "total_agents": 3,
    }
    
    if include_capabilities:
        cards = _load_cards()
        if repo:
            cards = [c for c in cards if c.get("repo") == repo]
        if discipline:
            cards = [c for c in cards if c.get("discipline") == discipline]
        
        # Summary view (not full cards)
        capabilities = []
        for card in cards:
            capabilities.append({
                "card_id": card["card_id"],
                "name": card["name"],
                "repo": card["repo"],
                "discipline": card.get("discipline", ""),
                "version": card["version"],
                "status": card["status"],
                "verification_status": card["validation_evidence"]["verification_status"],
                "parameter_domain": card["applicability"]["parameter_domain"],
                "dimensionality": card["applicability"].get("dimensionality", ""),
                "tags": card.get("tags", []),
            })
        
        directory["capabilities"] = capabilities
        directory["total_capabilities"] = len(capabilities)
    
    return directory


@router.get("/capabilities")
def list_capabilities(
    repo: Optional[str] = Query(None),
    discipline: Optional[str] = Query(None),
    status: Optional[str] = Query(None),
    tag: Optional[str] = Query(None),
):
    """Standalone capabilities endpoint for seat claim discovery.
    
    Returns full capability cards with optional filters.
    """
    cards = _load_cards()
    
    if repo:
        cards = [c for c in cards if c.get("repo") == repo]
    if discipline:
        cards = [c for c in cards if c.get("discipline") == discipline]
    if status:
        cards = [c for c in cards if c.get("status") == status]
    if tag:
        cards = [c for c in cards if tag in c.get("tags", [])]
    
    return {
        "capabilities": cards,
        "total": len(cards),
        "schema": "/api/capability/schema",
    }
