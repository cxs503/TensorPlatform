"""KPI delivery views — explicitly layered denominators for delivery quality.

Purpose
-------
Refines the coarse ``mission_success_rate`` KPI into layered delivery rates:

- ``complete_rate``       delivered_complete / **denominator**
- ``partial_rate``        delivered_partial  / **denominator**
- ``forced_finish_rate``  suspect_undeclared / **denominator**

Denominators are *explicit* and *layered* (they are returned in the payload, so
a number can never be quoted without its denominator):

- ``terminal``                every terminal mission (context only)
- ``succeeded``               missions whose terminal status is ``succeeded`` —
                              the delivery-quality layer (a *failed* mission is
                              not a delivery defect, it is an honest failure)
- ``succeeded_with_payload``  succeeded AND result JSON non-empty — the layer
                              where the classifier can actually decide

Before this module the delivery rates divided by *all* terminal missions, so a
failed mission with an empty result was counted as ``suspect_undeclared`` and
inflated ``forced_finish_rate``. The layered view removes that confusion.

Design rules
------------
* **No historical data modification.** Views are computed on the fly from
  existing result JSONs; no schema change.
* **Parallel to existing KPI.** Reported alongside ``mission_success_rate``.
* **Zero domain semantics.** All metrics are generic artifact facts.

Semantic terminal states (P93W3-N10) are reported in the same payload: the
binary ``succeeded`` count is split into ``succeeded / forced_finish / degraded /
failed`` by ``api/domain/terminal_state.py``, so a forced closure can no longer be
counted as a delivery. ``rates.delivery_rate`` is that semantic success share.

Audit chain
-----------
- Served by ``GET /tensor/kpi/delivery_layers`` (api/kpi.py)
- Classifies with ``api/domain/delivery_state.py`` and ``api/domain/terminal_state.py``
- ``denominator_key`` names the denominator used for every emitted rate
"""
from __future__ import annotations

import json
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from api.domain.delivery_state import (
    assess_attested_delivery,
    assess_delivery,
    compute_delivery_metrics,
    compute_missing_item_counts,
)
from api.domain.terminal_state import classify_terminal_state, summarize_states

TERMINAL_STATUSES = ("succeeded", "failed", "stopped", "revoked", "orphan")
SUCCESS_STATUS = "succeeded"


def _coerce_result(value: Any) -> dict[str, Any] | None:
    """JSONB may arrive as dict (postgres) or str (sqlite)."""
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (json.JSONDecodeError, ValueError):
            return None
    return value if isinstance(value, dict) else None


def compute_delivery_kpis(db: Session) -> dict[str, Any]:
    """Compute layered delivery KPIs over terminal missions.

    Returns
    -------
    dict
        ``denominators`` (explicit counts per layer), ``layers`` (per-layer
        delivery counts and rates), ``rates`` (headline rates on the
        ``denominator_key`` layer) and ``denominator_key``.
    """
    rows = db.execute(
        text("""
            SELECT id, status, result, budget, created_at, finished_at
            FROM tensor_dev_missions
            WHERE status = ANY(:statuses)
        """),
        {"statuses": list(TERMINAL_STATUSES)},
    ).fetchall()

    succeeded, succeeded_with_payload, not_succeeded = [], [], 0
    semantic_records: list[dict[str, Any]] = []
    for row in rows:
        mapping = row._mapping
        status = mapping.get("status")
        result = _coerce_result(mapping.get("result"))
        semantic_records.append({
            "id": mapping.get("id"),
            "status": status,
            "result": result,
            "budget": _coerce_result(mapping.get("budget")),
            "created_at": mapping.get("created_at"),
            "finished_at": mapping.get("finished_at"),
        })
        if status != SUCCESS_STATUS:
            not_succeeded += 1
            continue
        succeeded.append(result)
        if result:
            succeeded_with_payload.append(result)

    def _layer(payloads: list[dict[str, Any] | None], assessor=assess_delivery) -> dict[str, Any]:
        assessments = [assessor(p) for p in payloads]
        metrics = compute_delivery_metrics(assessments)
        total = metrics["total"]
        return {
            "denominator": total,
            "delivered_complete": metrics["complete"],
            "delivered_partial": metrics["partial"],
            "suspect_undeclared": metrics["undeclared"],
            "complete_rate": metrics["complete_rate"],
            "partial_rate": metrics["partial_rate"],
            "forced_finish_rate": metrics["undeclared_rate"],
            "avg_confidence": metrics["avg_confidence"],
            # Ranked enumeration of what was missing (requirement: a partial
            # verdict must say *what* is missing, not just that something is).
            "missing_items": compute_missing_item_counts(assessments),
            "forced_closure": sum(
                1 for a in assessments if a.facts.get("closure") == "forced"
            ),
        }

    layer_succeeded = _layer(succeeded)
    layer_payload = _layer(succeeded_with_payload)
    # Attested layer (P92W3-N10): artifact facts x closure integrity. Same
    # denominator; can only downgrade, never upgrade, the artifact verdict.
    layer_attested = _layer(succeeded, assess_attested_delivery)

    semantic = summarize_states([
        classify_terminal_state(record, allow_non_terminal=True)
        for record in semantic_records
    ])

    return {
        "denominator_key": "succeeded",
        "terminal_states": {
            "denominator_key": "terminal",
            "denominator": semantic["total"],
            "counts": semantic["counts"],
            "rates": semantic["rates"],
            "quality_gate_pass_rate": semantic["quality_gate_pass_rate"],
            "completeness_mean": semantic["completeness_mean"],
            "budget_status_counts": semantic["budget_status_counts"],
        },
        "denominators": {
            "terminal": len(rows),
            "succeeded": len(succeeded),
            "succeeded_with_payload": len(succeeded_with_payload),
            "not_succeeded": not_succeeded,
        },
        "rates": {
            # success_rate keeps the *old* binary meaning, so the delivery rates
            # below can never be quoted without it as context.
            "success_rate": (round(len(succeeded) / len(rows) * 100, 2) if rows else 0.0),
            "complete_rate": layer_attested["complete_rate"],
            "partial_rate": layer_attested["partial_rate"],
            "forced_finish_rate": layer_attested["forced_finish_rate"],
            "artifact_only_complete_rate": layer_succeeded["complete_rate"],
            "forced_closure_rate": (
                round(layer_attested["forced_closure"] / layer_attested["denominator"] * 100, 2)
                if layer_attested["denominator"] else 0.0
            ),
            # P93W3-N10 semantic layer (denominator = every terminal mission):
            # ``succeeded`` alone is no longer a delivery rate.
            "delivery_rate": round(semantic["rates"]["succeeded"] * 100, 2),
            "semantic_forced_finish_rate": round(semantic["rates"]["forced_finish"] * 100, 2),
            "semantic_degraded_rate": round(semantic["rates"]["degraded"] * 100, 2),
        },
        "layers": {
            "succeeded": layer_attested,
            "succeeded_artifact_only": layer_succeeded,
            "succeeded_with_payload": layer_payload,
        },
        # Backwards-compatible flat keys (formerly divided by *all* terminal
        # missions; now explicitly the ``succeeded`` layer).
        "total_terminal": len(rows),
        "delivered_complete": layer_succeeded["delivered_complete"],
        "delivered_partial": layer_succeeded["delivered_partial"],
        "suspect_undeclared": layer_succeeded["suspect_undeclared"],
        "complete_rate": layer_succeeded["complete_rate"],
        "partial_rate": layer_succeeded["partial_rate"],
        "forced_finish_rate": layer_succeeded["forced_finish_rate"],
        "avg_confidence": layer_succeeded["avg_confidence"],
        "attested": {
            "delivered_complete": layer_attested["delivered_complete"],
            "delivered_partial": layer_attested["delivered_partial"],
            "suspect_undeclared": layer_attested["suspect_undeclared"],
            "complete_rate": layer_attested["complete_rate"],
            "partial_rate": layer_attested["partial_rate"],
            "forced_finish_rate": layer_attested["forced_finish_rate"],
            "missing_items": layer_attested["missing_items"],
        },
    }


def get_delivery_kpi_definitions() -> list[dict[str, Any]]:
    """KPI definitions for the layered delivery metrics (with denominators)."""
    return [
        {
            "key": "success_rate",
            "label": "任务成功率(%)",
            "description": "succeeded / terminal * 100 — binary context for the delivery rates",
            "denominator": "terminal",
            "denominator_key": "terminal",
            "target": 90.0,
        },
        {
            "key": "complete_rate",
            "label": "完整交付率(%)",
            "description": "delivered_complete (artifact + closure attested) / succeeded * 100",
            "denominator": "succeeded",
            "target": 80.0,
        },
        {
            "key": "partial_rate",
            "label": "部分交付率(%)",
            "description": "delivered_partial / succeeded * 100",
            "denominator": "succeeded",
            "target": 15.0,
        },
        {
            "key": "forced_finish_rate",
            "label": "未申报交付率(%)",
            "description": "suspect_undeclared / succeeded * 100",
            "denominator": "succeeded",
            "target": 5.0,
        },
        {
            "key": "forced_closure_rate",
            "label": "强制收口交付率(%)",
            "description": "succeeded whose closure was extracted under budget duress / succeeded * 100",
            "denominator": "succeeded",
            "target": 10.0,
        },
        {
            "key": "delivery_rate",
            "label": "语义交付率(%)",
            "description": "semantic terminal state succeeded / terminal * 100 (forced/degraded excluded)",
            "denominator": "terminal",
            "target": 70.0,
        },
        {
            "key": "semantic_forced_finish_rate",
            "label": "被迫收尾率(%)",
            "description": "semantic terminal state forced_finish / terminal * 100",
            "denominator": "terminal",
            "target": 10.0,
        },
        {
            "key": "semantic_degraded_rate",
            "label": "部分交付率(%)",
            "description": "semantic terminal state degraded / terminal * 100",
            "denominator": "terminal",
            "target": 15.0,
        },
    ]


def enrich_kpi_response(
    kpis: list[dict[str, Any]],
    delivery_kpis: dict[str, Any],
) -> list[dict[str, Any]]:
    """Append the delivery layer KPIs (each carrying its denominator)."""
    denominator_key = delivery_kpis.get("denominator_key", "succeeded")
    denominator = delivery_kpis.get("denominators", {}).get(denominator_key)
    rates = delivery_kpis.get("rates", {})
    for defn in get_delivery_kpi_definitions():
        # Rates live under ``rates`` (layered view); fall back to flat keys for
        # consumers that read the legacy shape.
        value_now = rates.get(defn["key"], delivery_kpis.get(defn["key"], 0.0))
        target = defn["target"]
        denom_key = defn.get("denominator_key", denominator_key)
        kpis.append({
            "denominator": delivery_kpis.get("denominators", {}).get(denom_key),
            "denominator_key": denom_key,
            "key": defn["key"],
            "label": defn["label"],
            "value_now": value_now,
            "target": target,
            "pct": round(value_now / target * 100, 2) if target > 0 else 0.0,
            "description": defn["description"],
        })
    return kpis