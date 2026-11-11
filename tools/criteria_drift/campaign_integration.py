"""Gate integration: criteria drift -> ``campaign_nodes`` verdict / block_reason.

Contract implemented (node contract item (f))
---------------------------------------------
A node's verdict transition is *gated*: the requested outcome may be ``pass``,
but if the acceptance report narrows any proposal criterion silently the node is
**not** allowed to become ``done``.  It becomes ``blocked`` with a
``block_reason`` that carries the quantified drift and the audit hash, so the
DAG cannot activate downstream nodes on a relaxed bar.

Column mapping (both schemas carry the same semantics):

=====================  =========================  ==========================
field                  PG ``campaign_nodes``      local SQLite mirror
=====================  =========================  ==========================
key                    ``id``                     ``plan_id`` + ``node_id``
status                 ``status``                 ``status``
verdict                ``reproduction_verdict``   ``reproduction_verdict``
reason                 ``block_reason``           ``block_reason``
=====================  =========================  ==========================

The PG table (migration ``095_campaign_plans.sql``) already has
``block_reason TEXT`` and ``status`` restricted to
``pending|in-flight|done|blocked|failed|orphan`` - no migration is needed; only
the *policy* (who may set ``done``) changes.  ``apply_sqlite`` is exercised by
the test suite; ``apply_via_api`` posts the same transition through the existing
``POST /campaigns/nodes/{id}/verdict`` endpoint.
"""
from __future__ import annotations

import datetime as _dt
import json
import sqlite3
from typing import Any, Dict, Optional

from . import model

MAX_BLOCK_REASON = 512

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS campaign_nodes (
    plan_id             INTEGER NOT NULL,
    node_id             TEXT NOT NULL,
    name                TEXT,
    status              TEXT NOT NULL DEFAULT 'pending'
                        CHECK (status IN ('pending','in-flight','done','blocked','failed','orphan')),
    reproduction_verdict TEXT,
    block_reason        TEXT,
    updated_at          TEXT,
    metadata            TEXT,
    PRIMARY KEY (plan_id, node_id)
);
"""


def ensure_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA_SQL)


def verdict_transition(audit: model.DriftAudit, requested_verdict: str = "pass") -> Dict[str, Any]:
    """Pure function: audit + requested verdict -> the row values to write."""
    if requested_verdict not in ("pass", "fail"):
        raise ValueError("requested_verdict must be 'pass' or 'fail'")
    if audit.block:
        return {
            "gated": True,
            "status": "blocked",
            "reproduction_verdict": None,
            "block_reason": audit.block_reason()[:MAX_BLOCK_REASON],
            "requested_verdict": requested_verdict,
            "drift_score": audit.drift_score,
            "n_significant": len(audit.significant),
        }
    return {
        "gated": False,
        "status": "done" if requested_verdict == "pass" else "failed",
        "reproduction_verdict": requested_verdict,
        "block_reason": None if requested_verdict == "pass" else "verdict_fail",
        "requested_verdict": requested_verdict,
        "drift_score": audit.drift_score,
        "n_significant": 0,
    }


def apply_sqlite(conn: sqlite3.Connection, plan_id: int, node_id: str,
                 audit: model.DriftAudit, requested_verdict: str = "pass",
                 now: Optional[str] = None) -> Dict[str, Any]:
    """Apply the gated transition to the local ``campaign_nodes`` mirror."""
    ensure_schema(conn)
    row = conn.execute(
        "SELECT status, reproduction_verdict, block_reason FROM campaign_nodes "
        "WHERE plan_id=? AND node_id=?", (plan_id, node_id),
    ).fetchone()
    if row is None:
        raise KeyError(f"campaign node not found: plan={plan_id} node={node_id}")
    before = {"status": row[0], "reproduction_verdict": row[1], "block_reason": row[2]}
    trans = verdict_transition(audit, requested_verdict)
    stamp = now or "1970-01-01T00:00:00Z"   # deterministic unless caller passes a clock
    conn.execute(
        "UPDATE campaign_nodes SET status=?, reproduction_verdict=?, block_reason=?, updated_at=? "
        "WHERE plan_id=? AND node_id=?",
        (trans["status"], trans["reproduction_verdict"], trans["block_reason"], stamp, plan_id, node_id),
    )
    conn.commit()
    after = conn.execute(
        "SELECT status, reproduction_verdict, block_reason FROM campaign_nodes "
        "WHERE plan_id=? AND node_id=?", (plan_id, node_id),
    ).fetchone()
    return {
        "plan_id": plan_id,
        "node_id": node_id,
        "before": before,
        "after": {"status": after[0], "reproduction_verdict": after[1], "block_reason": after[2]},
        "gated": trans["gated"],
        "drift_score": trans["drift_score"],
        "n_significant": trans["n_significant"],
        "audit_sha256": audit.as_dict()["audit_sha256"],
    }


def pg_update_statement() -> str:
    """The exact SQL the backend endpoint must run (documented for reviewers)."""
    return (
        "UPDATE campaign_nodes SET status=:status, reproduction_verdict=:verdict, "
        "block_reason=:br, updated_at=now() WHERE id=:nid"
    )


def apply_via_api(base_url: str, node_id: int, audit: model.DriftAudit,
                  token: str, requested_verdict: str = "pass",
                  timeout: float = 10.0) -> Dict[str, Any]:
    """Post the gated verdict through the platform API (not exercised offline)."""
    import urllib.request

    trans = verdict_transition(audit, requested_verdict)
    body = json.dumps({
        "reproduction_verdict": trans["reproduction_verdict"] or "fail",
        "block_reason": trans["block_reason"],
    }).encode("utf-8")
    req = urllib.request.Request(
        f"{base_url.rstrip('/')}/api/v1/campaigns/nodes/{node_id}/verdict",
        data=body, method="POST",
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {token}"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 - explicit platform URL
        return {"status": resp.status, "body": json.loads(resp.read().decode("utf-8")),
                "gated": trans["gated"], "block_reason": trans["block_reason"]}