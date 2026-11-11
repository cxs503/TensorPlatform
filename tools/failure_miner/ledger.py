"""Idempotent failure signature ledger.

Maintains ``failed_signature_ledger`` table with unique key ``signature_hash``.
Provides insert-if-not-exists semantics for deterministic reruns.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any, Sequence

from .signature_miner import FailureSignature


# DDL for the ledger table
LEDGER_DDL = """
CREATE TABLE IF NOT EXISTS failed_signature_ledger (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    signature_hash  TEXT NOT NULL UNIQUE,
    signature_type  TEXT NOT NULL,
    error_code      TEXT NOT NULL,
    file_fingerprint TEXT NOT NULL,
    reason_hash     TEXT NOT NULL,
    mission_ids     TEXT NOT NULL DEFAULT '[]',
    evidence_sql    TEXT NOT NULL DEFAULT '',
    frequency       INTEGER NOT NULL DEFAULT 1,
    impact_score    REAL NOT NULL DEFAULT 0.0,
    first_seen      TEXT NOT NULL,
    last_seen       TEXT NOT NULL,
    run_hash        TEXT NOT NULL,
    created_at      TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_ledger_type ON failed_signature_ledger (signature_type);
CREATE INDEX IF NOT EXISTS idx_ledger_hash ON failed_signature_ledger (signature_hash);
"""

LEDGER_DDL_POSTGRESQL = """
CREATE TABLE IF NOT EXISTS failed_signature_ledger (
    id              BIGSERIAL PRIMARY KEY,
    signature_hash  TEXT NOT NULL UNIQUE,
    signature_type  TEXT NOT NULL,
    error_code      TEXT NOT NULL,
    file_fingerprint TEXT NOT NULL,
    reason_hash     TEXT NOT NULL,
    mission_ids     JSONB NOT NULL DEFAULT '[]'::jsonb,
    evidence_sql    TEXT NOT NULL DEFAULT '',
    frequency       INTEGER NOT NULL DEFAULT 1,
    impact_score    REAL NOT NULL DEFAULT 0.0,
    first_seen      TIMESTAMPTZ NOT NULL,
    last_seen       TIMESTAMPTZ NOT NULL,
    run_hash        TEXT NOT NULL,
    created_at      TIMESTAMPTZ NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_ledger_type ON failed_signature_ledger (signature_type);
CREATE INDEX IF NOT EXISTS idx_ledger_hash ON failed_signature_ledger (signature_hash);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def compute_run_hash(signatures: list[FailureSignature]) -> str:
    """Deterministic hash of a signature set for idempotency check."""
    payload = json.dumps(
        [s.to_dict() for s in sorted(signatures, key=lambda s: s.signature_hash)],
        sort_keys=True,
    )
    return hashlib.sha256(payload.encode()).hexdigest()[:16]


def upsert_signatures(db_session, signatures: list[FailureSignature],
                      run_hash: str | None = None) -> dict[str, int]:
    """Insert-or-update signatures in the ledger (idempotent).

    Args:
        db_session: SQLAlchemy session
        signatures: list of FailureSignature to upsert
        run_hash: deterministic run identifier (computed if None)

    Returns:
        Dict with 'inserted', 'updated', 'skipped' counts.
    """
    from sqlalchemy import text

    if run_hash is None:
        run_hash = compute_run_hash(signatures)

    now = _now()
    inserted = 0
    updated = 0
    skipped = 0

    for sig in signatures:
        # Check if exists
        existing = db_session.execute(
            text("SELECT id, frequency, impact_score, last_seen FROM failed_signature_ledger WHERE signature_hash = :sh"),
            {"sh": sig.signature_hash},
        ).mappings().first()

        if existing:
            # Update frequency and last_seen
            db_session.execute(
                text("""
                    UPDATE failed_signature_ledger
                    SET frequency = :freq,
                        impact_score = :impact,
                        last_seen = :last,
                        mission_ids = CAST(:mids AS jsonb),
                        run_hash = :run
                    WHERE signature_hash = :sh
                """),
                {
                    "sh": sig.signature_hash,
                    "freq": sig.frequency,
                    "impact": sig.impact_score,
                    "last": now,
                    "mids": json.dumps(sig.mission_ids),
                    "run": run_hash,
                },
            )
            updated += 1
        else:
            # Insert new
            db_session.execute(
                text("""
                    INSERT INTO failed_signature_ledger
                        (signature_hash, signature_type, error_code,
                         file_fingerprint, reason_hash, mission_ids,
                         evidence_sql, frequency, impact_score,
                         first_seen, last_seen, run_hash, created_at)
                    VALUES (:sh, :st, :ec, :fp, :rh, CAST(:mids AS jsonb),
                            :esql, :freq, :impact, :fs, :ls, :run, :ca)
                """),
                {
                    "sh": sig.signature_hash,
                    "st": sig.signature_type,
                    "ec": sig.error_code,
                    "fp": sig.file_fingerprint,
                    "rh": sig.reason_hash,
                    "mids": json.dumps(sig.mission_ids),
                    "esql": sig.evidence_sql,
                    "freq": sig.frequency,
                    "impact": sig.impact_score,
                    "fs": now,
                    "ls": now,
                    "run": run_hash,
                    "ca": now,
                },
            )
            inserted += 1

    return {"inserted": inserted, "updated": updated, "skipped": skipped, "run_hash": run_hash}


def upsert_signatures_sqlite(db_session, signatures: list[FailureSignature],
                              run_hash: str | None = None) -> dict[str, int]:
    """SQLite-compatible upsert (no jsonb CAST)."""
    from sqlalchemy import text

    if run_hash is None:
        run_hash = compute_run_hash(signatures)

    now = _now()
    inserted = 0
    updated = 0

    for sig in signatures:
        existing = db_session.execute(
            text("SELECT id, frequency, impact_score, last_seen FROM failed_signature_ledger WHERE signature_hash = :sh"),
            {"sh": sig.signature_hash},
        ).mappings().first()

        if existing:
            db_session.execute(
                text("""
                    UPDATE failed_signature_ledger
                    SET frequency = :freq,
                        impact_score = :impact,
                        last_seen = :last,
                        mission_ids = :mids,
                        run_hash = :run
                    WHERE signature_hash = :sh
                """),
                {
                    "sh": sig.signature_hash,
                    "freq": sig.frequency,
                    "impact": sig.impact_score,
                    "last": now,
                    "mids": json.dumps(sig.mission_ids),
                    "run": run_hash,
                },
            )
            updated += 1
        else:
            db_session.execute(
                text("""
                    INSERT INTO failed_signature_ledger
                        (signature_hash, signature_type, error_code,
                         file_fingerprint, reason_hash, mission_ids,
                         evidence_sql, frequency, impact_score,
                         first_seen, last_seen, run_hash, created_at)
                    VALUES (:sh, :st, :ec, :fp, :rh, :mids,
                            :esql, :freq, :impact, :fs, :ls, :run, :ca)
                """),
                {
                    "sh": sig.signature_hash,
                    "st": sig.signature_type,
                    "ec": sig.error_code,
                    "fp": sig.file_fingerprint,
                    "rh": sig.reason_hash,
                    "mids": json.dumps(sig.mission_ids),
                    "esql": sig.evidence_sql,
                    "freq": sig.frequency,
                    "impact": sig.impact_score,
                    "fs": now,
                    "ls": now,
                    "run": run_hash,
                    "ca": now,
                },
            )
            inserted += 1

    return {"inserted": inserted, "updated": updated, "skipped": 0, "run_hash": run_hash}


def verify_idempotency(db_session, run_hash: str) -> bool:
    """Verify that rerunning with same data produces same run_hash."""
    from sqlalchemy import text

    rows = db_session.execute(
        text("SELECT run_hash FROM failed_signature_ledger WHERE run_hash = :rh LIMIT 1"),
        {"rh": run_hash},
    ).mappings().first()

    return rows is not None


def get_ledger_entries(db_session, *, limit: int = 100) -> list[dict]:
    """Get all ledger entries for export."""
    from sqlalchemy import text

    rows = db_session.execute(
        text("""
            SELECT signature_hash, signature_type, error_code,
                   file_fingerprint, reason_hash, mission_ids,
                   evidence_sql, frequency, impact_score,
                   first_seen, last_seen, run_hash
            FROM failed_signature_ledger
            ORDER BY impact_score DESC
            LIMIT :limit
        """),
        {"limit": limit},
    ).mappings().all()

    return [dict(r) for r in rows]
