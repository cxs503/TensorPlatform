"""Tensor failure signatures API.

Endpoints for querying and managing failure signature data.
Zero domain semantics: all domain vocabulary from config injection.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.orm import Session

from api.deps import require_auth, require_role
from database.config import get_db

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/tensor/failure-signatures", tags=["失败签名"])


class SignatureQuery(BaseModel):
    signature_type: str | None = Field(default=None, max_length=40)
    min_frequency: int = Field(default=1, ge=1)
    min_impact: float = Field(default=0.0, ge=0.0)
    limit: int = Field(default=50, ge=1, le=200)


@router.get("", dependencies=[Depends(require_auth)])
def list_signatures(
    signature_type: str | None = Query(None, max_length=40),
    min_frequency: int = Query(1, ge=1),
    min_impact: float = Query(0.0, ge=0.0),
    limit: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_db),
    user: dict = Depends(require_auth),
):
    """List failure signatures from the ledger."""
    filters = ["1=1"]
    params: dict[str, Any] = {"limit": limit}

    if signature_type:
        filters.append("signature_type = :st")
        params["st"] = signature_type

    filters.append("frequency >= :mf")
    params["mf"] = min_frequency

    filters.append("impact_score >= :mi")
    params["mi"] = min_impact

    where = " AND ".join(filters)

    rows = db.execute(
        text(f"""
            SELECT signature_hash, signature_type, error_code,
                   file_fingerprint, reason_hash, mission_ids,
                   evidence_sql, frequency, impact_score,
                   first_seen, last_seen, run_hash
            FROM failed_signature_ledger
            WHERE {where}
            ORDER BY impact_score DESC
            LIMIT :limit
        """),
        params,
    ).mappings().all()

    items = []
    for r in rows:
        item = dict(r)
        # Parse mission_ids from JSON string
        if isinstance(item.get("mission_ids"), str):
            try:
                item["mission_ids"] = json.loads(item["mission_ids"])
            except ValueError:
                item["mission_ids"] = []
        items.append(item)

    return {"items": items, "total": len(items)}


@router.get("/stats", dependencies=[Depends(require_auth)])
def signature_stats(
    db: Session = Depends(get_db),
    user: dict = Depends(require_auth),
):
    """Get aggregate statistics on failure signatures."""
    # Distribution by type
    type_rows = db.execute(
        text("""
            SELECT signature_type, COUNT(*) as count,
                   SUM(frequency) as total_missions,
                   AVG(impact_score) as avg_impact
            FROM failed_signature_ledger
            GROUP BY signature_type
            ORDER BY count DESC
        """),
    ).mappings().all()

    distribution = {}
    for r in type_rows:
        distribution[r["signature_type"]] = {
            "count": r["count"],
            "total_missions": r["total_missions"],
            "avg_impact": round(float(r["avg_impact"]), 4),
        }

    # Top signatures
    top_rows = db.execute(
        text("""
            SELECT signature_hash, signature_type, frequency, impact_score
            FROM failed_signature_ledger
            ORDER BY impact_score DESC
            LIMIT 10
        """),
    ).mappings().all()

    top = [dict(r) for r in top_rows]

    return {
        "distribution": distribution,
        "top_signatures": top,
        "total_signatures": sum(d["count"] for d in distribution.values()),
    }


@router.post("/mine", dependencies=[Depends(require_role("ADMIN", "MANAGER"))])
def trigger_mining(
    days: int = Query(30, ge=1, le=365),
    limit: int = Query(500, ge=1, le=5000),
    db: Session = Depends(get_db),
    user: dict = Depends(require_auth),
):
    """Trigger failure signature mining (admin only).

    This is a synchronous endpoint for small runs. For large-scale mining,
    use the CLI tool or submit to the cluster.
    """
    from tools.failure_miner.signature_miner import (
        collect_failed_missions_sqlite,
        FailedMissionView,
    )
    from tools.failure_miner.classifier import classify_batch, classification_report
    from tools.failure_miner.cluster import cluster_signatures
    from tools.failure_miner.ledger import upsert_signatures_sqlite, compute_run_hash

    # Collect missions
    missions = collect_failed_missions_sqlite(db, days=days, limit=limit)

    if not missions:
        return {"status": "no_data", "message": "No failed missions found"}

    # Classify
    classified = classify_batch(missions)
    report = classification_report(classified)

    # Cluster
    signatures = cluster_signatures(classified)

    # Upsert to ledger
    run_hash = compute_run_hash(signatures)
    result = upsert_signatures_sqlite(db, signatures, run_hash=run_hash)
    db.commit()

    return {
        "status": "completed",
        "missions_processed": len(missions),
        "classification_report": report,
        "signatures_created": len(signatures),
        "ledger_result": result,
    }


@router.get("/traps-candidates", dependencies=[Depends(require_auth)])
def get_traps_candidates(
    limit: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_db),
    user: dict = Depends(require_auth),
):
    """Get TRAPS candidates formatted for KNOWN_TRAPS append."""
    rows = db.execute(
        text("""
            SELECT signature_hash, signature_type, error_code,
                   frequency, impact_score, mission_ids, evidence_sql
            FROM failed_signature_ledger
            ORDER BY impact_score DESC
            LIMIT :limit
        """),
        {"limit": limit},
    ).mappings().all()

    candidates = []
    for r in rows:
        mission_ids = r["mission_ids"]
        if isinstance(mission_ids, str):
            try:
                mission_ids = json.loads(mission_ids)
            except ValueError:
                mission_ids = []

        desc = (
            f"[{r['signature_type']}] error={r['error_code']} "
            f"freq={r['frequency']} impact={float(r['impact_score']):.1f} "
            f"missions={mission_ids[:5] if isinstance(mission_ids, list) else []}"
        )
        if len(desc) > 120:
            desc = desc[:117] + "..."

        candidates.append({
            "signature_hash": r["signature_hash"],
            "description": desc,
            "signature_type": r["signature_type"],
            "error_code": r["error_code"],
            "frequency": r["frequency"],
            "impact_score": round(float(r["impact_score"]), 4),
            "mission_ids": mission_ids,
            "evidence_sql": r["evidence_sql"],
        })

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "total_candidates": len(candidates),
        "candidates": candidates,
    }
