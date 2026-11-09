"""KPI delivery views — layered denominator for mission success metrics.

Purpose
-------
Provides layered KPI views alongside the existing ``mission_success_rate``:
- ``complete_rate``: delivered_complete / terminal * 100
- ``partial_rate``: delivered_partial / terminal * 100
- ``forced_finish_rate``: suspect_undeclared / terminal * 100

Design rules
------------
* **No historical data modification.** These are views computed on-the-fly
  from existing result JSONs. No schema changes to tensor_dev_missions.
* **Parallel to existing KPI.** Sits alongside ``mission_success_rate`` in
  the KPI guide tracker response.
* **Zero domain semantics.** All metrics are generic artifact-based.

Audit chain
-----------
- Consumed by ``api/kpi.py`` guide_tracker endpoint
- Uses ``api/domain/delivery_state.py`` for classification
- No DB schema changes (pure computation over result JSONB)
"""
from __future__ import annotations

from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from api.domain.delivery_state import (
    assess_delivery,
    compute_delivery_metrics,
)


def compute_delivery_kpis(db: Session) -> dict[str, Any]:
    """Compute layered delivery KPIs from mission results.
    
    Parameters
    ----------
    db : Session
        Database session
    
    Returns
    -------
    dict
        Delivery KPIs: complete_rate, partial_rate, forced_finish_rate,
        plus counts and average confidence
    """
    # Fetch all terminal missions with their result JSON
    # Terminal states: succeeded, failed, stopped, revoked, orphan
    rows = db.execute(
        text("""
            SELECT id, status, result
            FROM tensor_dev_missions
            WHERE status IN ('succeeded', 'failed', 'stopped', 'revoked', 'orphan')
        """)
    ).fetchall()
    
    if not rows:
        return {
            "total_terminal": 0,
            "delivered_complete": 0,
            "delivered_partial": 0,
            "suspect_undeclared": 0,
            "complete_rate": 0.0,
            "partial_rate": 0.0,
            "forced_finish_rate": 0.0,
            "avg_confidence": 0.0,
        }
    
    # Classify each mission
    assessments = []
    for row in rows:
        result = row._mapping.get("result")
        # Handle JSONB (may be dict or string depending on dialect)
        if isinstance(result, str):
            import json
            try:
                result = json.loads(result)
            except (json.JSONDecodeError, ValueError):
                result = None
        assessment = assess_delivery(result)
        assessments.append(assessment)
    
    # Compute metrics
    metrics = compute_delivery_metrics(assessments)
    
    # Rename for KPI context
    return {
        "total_terminal": metrics["total"],
        "delivered_complete": metrics["complete"],
        "delivered_partial": metrics["partial"],
        "suspect_undeclared": metrics["undeclared"],
        "complete_rate": metrics["complete_rate"],
        "partial_rate": metrics["partial_rate"],
        "forced_finish_rate": metrics["undeclared_rate"],
        "avg_confidence": metrics["avg_confidence"],
    }


def get_delivery_kpi_definitions() -> list[dict[str, Any]]:
    """Get KPI definitions for the layered delivery metrics.
    
    Returns
    -------
    list[dict]
        KPI definitions with key, label, description, target
    """
    return [
        {
            "key": "complete_rate",
            "label": "完整交付率(%)",
            "description": "delivered_complete / terminal_missions * 100",
            "target": 80.0,
        },
        {
            "key": "partial_rate",
            "label": "部分交付率(%)",
            "description": "delivered_partial / terminal_missions * 100",
            "target": 15.0,
        },
        {
            "key": "forced_finish_rate",
            "label": "强制完成率(%)",
            "description": "suspect_undeclared / terminal_missions * 100",
            "target": 5.0,
        },
    ]


def enrich_kpi_response(
    kpis: list[dict[str, Any]],
    delivery_kpis: dict[str, Any],
) -> list[dict[str, Any]]:
    """Enrich existing KPI response with delivery layer metrics.
    
    Parameters
    ----------
    kpis : list[dict]
        Existing KPI list from guide_tracker
    delivery_kpis : dict
        Delivery KPIs from compute_delivery_kpis
    
    Returns
    -------
    list[dict]
        Enriched KPI list with delivery metrics appended
    """
    # Get definitions
    definitions = get_delivery_kpi_definitions()
    
    # Append delivery KPIs
    for defn in definitions:
        key = defn["key"]
        value_now = delivery_kpis.get(key, 0.0)
        target = defn["target"]
        pct = round(value_now / target * 100, 2) if target > 0 else 0.0
        
        kpis.append({
            "key": key,
            "label": defn["label"],
            "value_now": value_now,
            "target": target,
            "pct": pct,
            "description": defn["description"],
        })
    
    return kpis
