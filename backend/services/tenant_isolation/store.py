"""SQLite-backed persistence for the tenant isolation mechanism.

Design notes
------------
* The mechanism is domain-neutral: no domain wording lives here, all tenant
  identity/quota facts are injected through ``config/tenant_registry.json``.
* Fail-closed: every unknown subject (tenant, resource, path) is denied and the
  denial is recorded in ``access_log`` so audits can replay decisions.
* One connection guarded by a re-entrant lock keeps the module usable from API
  handlers and from the validation CLI without a session factory dependency.
"""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

SCHEMA = """
CREATE TABLE IF NOT EXISTS feature_ledger (
    ledger_id  TEXT PRIMARY KEY,
    tenant     TEXT NOT NULL,
    item_type  TEXT NOT NULL,
    payload    TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS tenant_budget (
    tenant      TEXT NOT NULL,
    cycle       TEXT NOT NULL,
    resource    TEXT NOT NULL,
    quota_limit REAL NOT NULL,
    used        REAL NOT NULL DEFAULT 0,
    PRIMARY KEY (tenant, cycle, resource)
);
CREATE TABLE IF NOT EXISTS tenant_claim (
    claim_id   INTEGER PRIMARY KEY AUTOINCREMENT,
    tenant     TEXT NOT NULL,
    cycle      TEXT NOT NULL,
    resource   TEXT NOT NULL,
    amount     REAL NOT NULL,
    decision   TEXT NOT NULL,
    reason     TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS tenant_grant (
    grant_id        TEXT PRIMARY KEY,
    owner           TEXT NOT NULL,
    grantee         TEXT NOT NULL,
    scope           TEXT NOT NULL,
    resource_prefix TEXT NOT NULL,
    expires_at      REAL NOT NULL,
    created_at      TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS access_log (
    access_id  INTEGER PRIMARY KEY AUTOINCREMENT,
    tenant     TEXT NOT NULL,
    action     TEXT NOT NULL,
    resource   TEXT NOT NULL,
    decision   TEXT NOT NULL,
    reason     TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS tenant_event (
    event_id   TEXT PRIMARY KEY,
    tenant     TEXT NOT NULL,
    kind       TEXT NOT NULL,
    visibility TEXT NOT NULL,
    payload    TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_budget_tenant ON tenant_budget(tenant, cycle);
CREATE INDEX IF NOT EXISTS ix_grant_grantee ON tenant_grant(grantee, owner);
CREATE INDEX IF NOT EXISTS ix_access_tenant ON access_log(tenant, action);
CREATE INDEX IF NOT EXISTS ix_event_tenant ON tenant_event(tenant, created_at);
"""


def utc_now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


class Store:
    """Thin SQLite wrapper (default: in-memory)."""

    def __init__(self, db_path: str | os.PathLike | None = None):
        path = str(db_path) if db_path is not None else os.environ.get(
            "TENANT_ISOLATION_DB", ":memory:"
        )
        self.db_path = path
        if path not in (":memory:", ""):
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        with self._lock:
            self._conn.executescript(SCHEMA)
            self._conn.commit()

    # -- helpers ---------------------------------------------------------
    def _exec(self, sql: str, params: tuple = ()) -> sqlite3.Cursor:
        with self._lock:
            cur = self._conn.execute(sql, params)
            self._conn.commit()
            return cur

    def query(self, sql: str, params: tuple = ()) -> List[Dict[str, Any]]:
        with self._lock:
            return [dict(r) for r in self._conn.execute(sql, params).fetchall()]

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    # -- feature ledger --------------------------------------------------
    def ledger_append(self, tenant: str, item_type: str, payload: Dict[str, Any]) -> str:
        blob = json.dumps(payload, sort_keys=True, ensure_ascii=False)
        ledger_id = hashlib.sha256(
            f"{tenant}|{item_type}|{blob}".encode("utf-8")
        ).hexdigest()[:20]
        self._exec(
            "INSERT OR REPLACE INTO feature_ledger "
            "(ledger_id, tenant, item_type, payload, created_at) VALUES (?,?,?,?,?)",
            (ledger_id, tenant, item_type, blob, utc_now()),
        )
        return ledger_id

    def ledger_query(
        self, tenant: Optional[str] = None, item_type: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        sql = "SELECT * FROM feature_ledger WHERE 1=1"
        params: List[Any] = []
        if tenant is not None:
            sql += " AND tenant = ?"
            params.append(tenant)
        if item_type is not None:
            sql += " AND item_type = ?"
            params.append(item_type)
        rows = self.query(sql + " ORDER BY created_at, ledger_id", tuple(params))
        for r in rows:
            r["payload"] = json.loads(r["payload"])
        return rows

    # -- access log ------------------------------------------------------
    def log_access(
        self, tenant: str, action: str, resource: str, decision: str, reason: str
    ) -> None:
        self._exec(
            "INSERT INTO access_log (tenant, action, resource, decision, reason, created_at)"
            " VALUES (?,?,?,?,?,?)",
            (tenant, action, resource, decision, reason, utc_now()),
        )

    def access_entries(self, tenant: Optional[str] = None) -> List[Dict[str, Any]]:
        if tenant is None:
            return self.query("SELECT * FROM access_log ORDER BY access_id")
        return self.query(
            "SELECT * FROM access_log WHERE tenant = ? ORDER BY access_id", (tenant,)
        )