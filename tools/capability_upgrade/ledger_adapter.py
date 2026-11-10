"""Adapter: existing capability evidence ledger → pipeline slots/evidence.

This module is the *data boundary*: it contains no domain logic, only a mapping
from the ledger document's generic fields (``id``, ``claimed_level``,
``evidence[].stance``, ``asserted_level``, ``report_id``/doc path) to pipeline
records.  Domain labels stay in the ledger JSON, never in code.

Honesty notes
-------------
* Every slot starts at ``WITHHELD``: a *claim* is not evidence.  The claimed
  level is preserved in ``metadata.claimed_level`` so an audit can see
  overclaims.
* Ledger review rows become evidence of kind ``ledger_review`` holding only a
  coarse ``stance_support`` metric (supports=+1, conditional=0, contradicts=-1).
  Prose verdicts are **not** parsed into percentages: prose numbers are not
  machine-checkable provenance and must not be able to trigger an upgrade.
* Slots with no ledger rows get an ``absence_audit`` item, so the C1 coverage
  invariant ("every WITHHELD slot has ≥1 associated evidence item") holds while
  the slot correctly stays withheld.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Dict, List

STANCE_SUPPORT = {"supports": 1.0, "supports_real": 1.0, "conditional": 0.0,
                  "contradicts": -1.0}


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def build_from_ledger(ledger_path: Path | str) -> Dict[str, Any]:
    """Return ``{"registry": {...}, "evidence": [ingest kwargs, ...]}``."""
    ledger_path = Path(ledger_path)
    doc = json.loads(ledger_path.read_text(encoding="utf-8"))
    ledger_sha = _sha256_file(ledger_path)
    registry_caps: List[Dict[str, Any]] = []
    criteria: List[Dict[str, Any]] = []
    evidence: List[Dict[str, Any]] = []

    for cap in doc.get("capabilities") or []:
        cid = cap["id"]
        registry_caps.append(
            {
                "capability_id": cid,
                "label": cap.get("domain") or "",
                "initial_level": "WITHHELD",
                "metadata": {
                    "claimed_level": cap.get("claimed_level"),
                    "ledger_domain_label": cap.get("domain"),
                    "ledger_action": cap.get("action"),
                },
            }
        )
        # Generic, conservative acceptance rule: a REAL grade requires >=2
        # machine-checkable numeric verifications whose error is <=3% and whose
        # provenance carries a content hash.  Ledger prose cannot satisfy it.
        criteria.append(
            {
                "criterion_id": f"{cid}::machine_metric_gate",
                "capability_id": cid,
                "target_level": "REAL",
                "description": (
                    ">=2 machine-checkable numeric verifications (error<=3%, "
                    "sha256-anchored source), no contradicting review"
                ),
                "spec": {
                    "evidence_kinds": ["numeric_verification"],
                    "min_matching": 2,
                    "min_sources": 2,
                    "require_source_sha256": True,
                    "forbid_observed_levels": ["MOCK", "STUB", "NOT_SUPPORTED"],
                    "metrics": [{"name": "error_pct", "op": "<=", "value": 3.0, "agg": "all"}],
                },
            }
        )

        rows = cap.get("evidence") or []
        if not rows:
            evidence.append(
                {
                    "capability_id": cid,
                    "kind": "absence_audit",
                    "metrics": {"evidence_count": 0},
                    "observed_level": "WITHHELD",
                    "source": {
                        "path": str(ledger_path),
                        "locator": f"ledger:capabilities/{cid}",
                        "sha256": ledger_sha,
                    },
                    "payload": {"claimed_level": cap.get("claimed_level")},
                }
            )
            continue

        for row in rows:
            stance = str(row.get("stance", "")).lower()
            locator = row.get("report_id")
            evidence.append(
                {
                    "capability_id": cid,
                    "kind": "ledger_review",
                    "metrics": {
                        "stance_support": STANCE_SUPPORT.get(stance, 0.0),
                        "review_count": 1,
                    },
                    "observed_level": (row.get("asserted_level") or "").upper() or None,
                    "source": {
                        "path": str(ledger_path),
                        "locator": f"report:{locator}",
                        "sha256": ledger_sha,
                    },
                    "payload": {
                        "stance": stance,
                        "asserted_level": row.get("asserted_level"),
                        "quote_match": row.get("quote_match"),
                        "note": row.get("note"),
                        "as_of": row.get("as_of"),
                    },
                }
            )

    return {
        "registry": {
            "title": "capability ledger → pipeline adapter registry",
            "source_ledger": str(ledger_path),
            "source_sha256": ledger_sha,
            "capabilities": registry_caps,
            "criteria": criteria,
        },
        "evidence": evidence,
    }