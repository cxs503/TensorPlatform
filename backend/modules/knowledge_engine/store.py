"""Knowledge store — SQLite-backed persistent storage for four-contract entries.

Design principles:
- Single-writer, multi-reader (SQLite WAL mode)
- Full provenance chain (every entry traceable to source)
- Atomic writes (transactions)
- Schema validation on insert
- Efficient search via FTS5 virtual table
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
import time
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator, Optional

# Schema version for migration tracking
SCHEMA_VERSION = "1.0"

# SQL for creating tables
_INIT_SQL = """
-- Main knowledge entries table
CREATE TABLE IF NOT EXISTS knowledge_entries (
    entry_id TEXT PRIMARY KEY,
    version TEXT NOT NULL DEFAULT '1.0',
    created_at TEXT NOT NULL,
    problem_statement TEXT NOT NULL,  -- JSON
    methodology TEXT NOT NULL,        -- JSON
    evidence_chain TEXT NOT NULL,     -- JSON
    conclusion TEXT NOT NULL,         -- JSON
    uncertainty TEXT,                 -- JSON
    provenance TEXT NOT NULL,         -- JSON
    relations TEXT,                   -- JSON
    tags TEXT,                        -- JSON array
    domain TEXT,
    outcome TEXT,
    confidence TEXT,
    source_type TEXT,
    source_id TEXT,
    repo TEXT
);

-- Full-text search index
CREATE VIRTUAL TABLE IF NOT EXISTS knowledge_fts USING fts5(
    entry_id UNINDEXED,
    content,
    tokenize='unicode61'
);

-- Provenance index for traceability
CREATE INDEX IF NOT EXISTS idx_provenance_source ON knowledge_entries(source_type, source_id);
CREATE INDEX IF NOT EXISTS idx_provenance_repo ON knowledge_entries(repo);
CREATE INDEX IF NOT EXISTS idx_created_at ON knowledge_entries(created_at);
CREATE INDEX IF NOT EXISTS idx_domain ON knowledge_entries(domain);
CREATE INDEX IF NOT EXISTS idx_outcome ON knowledge_entries(outcome);

-- Schema version tracking
CREATE TABLE IF NOT EXISTS schema_version (
    version TEXT PRIMARY KEY,
    applied_at TEXT NOT NULL
);
"""


@dataclass
class KnowledgeEntry:
    """Structured knowledge entry with four contracts."""
    entry_id: str
    version: str = "1.0"
    created_at: str = ""
    problem_statement: dict = field(default_factory=dict)
    methodology: dict = field(default_factory=dict)
    evidence_chain: dict = field(default_factory=dict)
    conclusion: dict = field(default_factory=dict)
    uncertainty: dict = field(default_factory=dict)
    provenance: dict = field(default_factory=dict)
    relations: list = field(default_factory=list)

    def __post_init__(self):
        if not self.created_at:
            self.created_at = datetime.now(timezone.utc).isoformat()

    def to_dict(self) -> dict:
        """Convert to dictionary for JSON serialization."""
        return {
            "entry_id": self.entry_id,
            "version": self.version,
            "created_at": self.created_at,
            "problem_statement": self.problem_statement,
            "methodology": self.methodology,
            "evidence_chain": self.evidence_chain,
            "conclusion": self.conclusion,
            "uncertainty": self.uncertainty,
            "provenance": self.provenance,
            "relations": self.relations,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "KnowledgeEntry":
        """Create entry from dictionary."""
        return cls(
            entry_id=data["entry_id"],
            version=data.get("version", "1.0"),
            created_at=data.get("created_at", ""),
            problem_statement=data.get("problem_statement", {}),
            methodology=data.get("methodology", {}),
            evidence_chain=data.get("evidence_chain", {}),
            conclusion=data.get("conclusion", {}),
            uncertainty=data.get("uncertainty", {}),
            provenance=data.get("provenance", {}),
            relations=data.get("relations", []),
        )

    def compute_id(self) -> str:
        """Compute deterministic entry ID from content hash."""
        content = json.dumps({
            "problem": self.problem_statement,
            "method": self.methodology,
            "evidence": self.evidence_chain,
            "conclusion": self.conclusion,
        }, sort_keys=True, ensure_ascii=False)
        hash_hex = hashlib.sha256(content.encode()).hexdigest()[:16]
        return f"ke_{hash_hex}"


class KnowledgeStore:
    """SQLite-backed knowledge store with FTS5 search."""

    def __init__(self, db_path: str | Path = "knowledge_engine.db"):
        """Initialize store with database path."""
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _init_db(self):
        """Initialize database schema."""
        with self._connect() as conn:
            conn.executescript(_INIT_SQL)
            # Record schema version
            conn.execute(
                "INSERT OR IGNORE INTO schema_version (version, applied_at) VALUES (?, ?)",
                (SCHEMA_VERSION, datetime.now(timezone.utc).isoformat())
            )

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        """Get database connection with WAL mode."""
        conn = sqlite3.connect(str(self.db_path), timeout=30)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def insert(self, entry: KnowledgeEntry) -> str:
        """Insert a knowledge entry. Returns entry_id.

        Raises ValueError if entry_id already exists.
        """
        # Compute entry_id if not set
        if not entry.entry_id or entry.entry_id == "":
            entry.entry_id = entry.compute_id()

        # Extract indexable fields
        tags = entry.problem_statement.get("tags", [])
        domain = entry.problem_statement.get("domain", "")
        outcome = entry.conclusion.get("outcome", "")
        confidence = entry.uncertainty.get("confidence", "")
        source_type = entry.provenance.get("source_type", "")
        source_id = entry.provenance.get("source_id", "")
        repo = entry.provenance.get("repo", "")

        # Build searchable content
        searchable = self._build_searchable_content(entry)

        with self._connect() as conn:
            try:
                conn.execute(
                    """INSERT INTO knowledge_entries
                    (entry_id, version, created_at, problem_statement, methodology,
                     evidence_chain, conclusion, uncertainty, provenance, relations,
                     tags, domain, outcome, confidence, source_type, source_id, repo)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        entry.entry_id,
                        entry.version,
                        entry.created_at,
                        json.dumps(entry.problem_statement, ensure_ascii=False),
                        json.dumps(entry.methodology, ensure_ascii=False),
                        json.dumps(entry.evidence_chain, ensure_ascii=False),
                        json.dumps(entry.conclusion, ensure_ascii=False),
                        json.dumps(entry.uncertainty, ensure_ascii=False) if entry.uncertainty else None,
                        json.dumps(entry.provenance, ensure_ascii=False),
                        json.dumps(entry.relations, ensure_ascii=False) if entry.relations else None,
                        json.dumps(tags, ensure_ascii=False) if tags else None,
                        domain,
                        outcome,
                        confidence,
                        source_type,
                        source_id,
                        repo,
                    )
                )
                # Insert into FTS
                conn.execute(
                    "INSERT INTO knowledge_fts (entry_id, content) VALUES (?, ?)",
                    (entry.entry_id, searchable)
                )
            except sqlite3.IntegrityError as e:
                raise ValueError(f"Entry {entry.entry_id} already exists") from e

        return entry.entry_id

    def upsert(self, entry: KnowledgeEntry) -> str:
        """Insert or update a knowledge entry. Returns entry_id."""
        if not entry.entry_id:
            entry.entry_id = entry.compute_id()

        try:
            return self.insert(entry)
        except ValueError:
            # Entry exists, update it
            self.update(entry)
            return entry.entry_id

    def update(self, entry: KnowledgeEntry) -> None:
        """Update an existing entry."""
        tags = entry.problem_statement.get("tags", [])
        domain = entry.problem_statement.get("domain", "")
        outcome = entry.conclusion.get("outcome", "")
        confidence = entry.uncertainty.get("confidence", "")
        source_type = entry.provenance.get("source_type", "")
        source_id = entry.provenance.get("source_id", "")
        repo = entry.provenance.get("repo", "")

        searchable = self._build_searchable_content(entry)

        with self._connect() as conn:
            conn.execute(
                """UPDATE knowledge_entries SET
                version=?, created_at=?, problem_statement=?, methodology=?,
                evidence_chain=?, conclusion=?, uncertainty=?, provenance=?, relations=?,
                tags=?, domain=?, outcome=?, confidence=?, source_type=?, source_id=?, repo=?
                WHERE entry_id=?""",
                (
                    entry.version,
                    entry.created_at,
                    json.dumps(entry.problem_statement, ensure_ascii=False),
                    json.dumps(entry.methodology, ensure_ascii=False),
                    json.dumps(entry.evidence_chain, ensure_ascii=False),
                    json.dumps(entry.conclusion, ensure_ascii=False),
                    json.dumps(entry.uncertainty, ensure_ascii=False) if entry.uncertainty else None,
                    json.dumps(entry.provenance, ensure_ascii=False),
                    json.dumps(entry.relations, ensure_ascii=False) if entry.relations else None,
                    json.dumps(tags, ensure_ascii=False) if tags else None,
                    domain,
                    outcome,
                    confidence,
                    source_type,
                    source_id,
                    repo,
                    entry.entry_id,
                )
            )
            # Update FTS
            conn.execute("DELETE FROM knowledge_fts WHERE entry_id=?", (entry.entry_id,))
            conn.execute(
                "INSERT INTO knowledge_fts (entry_id, content) VALUES (?, ?)",
                (entry.entry_id, searchable)
            )

    def get(self, entry_id: str) -> Optional[KnowledgeEntry]:
        """Get a single entry by ID."""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM knowledge_entries WHERE entry_id=?",
                (entry_id,)
            ).fetchone()
            if row:
                return self._row_to_entry(row)
        return None

    def delete(self, entry_id: str) -> bool:
        """Delete an entry. Returns True if deleted."""
        with self._connect() as conn:
            cursor = conn.execute(
                "DELETE FROM knowledge_entries WHERE entry_id=?",
                (entry_id,)
            )
            conn.execute("DELETE FROM knowledge_fts WHERE entry_id=?", (entry_id,))
            return cursor.rowcount > 0

    def count(self) -> int:
        """Get total number of entries."""
        with self._connect() as conn:
            row = conn.execute("SELECT COUNT(*) FROM knowledge_entries").fetchone()
            return row[0]

    def list_entries(
        self,
        limit: int = 100,
        offset: int = 0,
        domain: Optional[str] = None,
        outcome: Optional[str] = None,
        source_type: Optional[str] = None,
        repo: Optional[str] = None,
    ) -> list[KnowledgeEntry]:
        """List entries with optional filters."""
        conditions = []
        params = []

        if domain:
            conditions.append("domain = ?")
            params.append(domain)
        if outcome:
            conditions.append("outcome = ?")
            params.append(outcome)
        if source_type:
            conditions.append("source_type = ?")
            params.append(source_type)
        if repo:
            conditions.append("repo = ?")
            params.append(repo)

        where = " WHERE " + " AND ".join(conditions) if conditions else ""
        params.extend([limit, offset])

        with self._connect() as conn:
            rows = conn.execute(
                f"SELECT * FROM knowledge_entries{where} ORDER BY created_at DESC LIMIT ? OFFSET ?",
                params
            ).fetchall()
            return [self._row_to_entry(row) for row in rows]

    def search(self, query: str, limit: int = 20) -> list[tuple[str, float]]:
        """Full-text search. Returns list of (entry_id, rank) tuples."""
        if not query.strip():
            return []

        with self._connect() as conn:
            rows = conn.execute(
                """SELECT entry_id, rank FROM knowledge_fts
                WHERE knowledge_fts MATCH ? ORDER BY rank LIMIT ?""",
                (query, limit)
            ).fetchall()
            return [(row["entry_id"], row["rank"]) for row in rows]

    def get_stats(self) -> dict:
        """Get knowledge base statistics."""
        with self._connect() as conn:
            total = conn.execute("SELECT COUNT(*) FROM knowledge_entries").fetchone()[0]

            by_domain = {}
            for row in conn.execute(
                "SELECT domain, COUNT(*) as cnt FROM knowledge_entries GROUP BY domain"
            ):
                by_domain[row["domain"] or "unknown"] = row["cnt"]

            by_outcome = {}
            for row in conn.execute(
                "SELECT outcome, COUNT(*) as cnt FROM knowledge_entries GROUP BY outcome"
            ):
                by_outcome[row["outcome"] or "unknown"] = row["cnt"]

            by_source = {}
            for row in conn.execute(
                "SELECT source_type, COUNT(*) as cnt FROM knowledge_entries GROUP BY source_type"
            ):
                by_source[row["source_type"] or "unknown"] = row["cnt"]

            by_repo = {}
            for row in conn.execute(
                "SELECT repo, COUNT(*) as cnt FROM knowledge_entries GROUP BY repo"
            ):
                by_repo[row["repo"] or "unknown"] = row["cnt"]

            return {
                "total": total,
                "by_domain": by_domain,
                "by_outcome": by_outcome,
                "by_source_type": by_source,
                "by_repo": by_repo,
            }

    def verify_provenance_integrity(self) -> dict:
        """Verify that all entries have complete provenance chains.

        Returns dict with integrity status and any issues found.
        """
        issues = []
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT entry_id, provenance, source_type, source_id FROM knowledge_entries"
            ).fetchall()

            for row in rows:
                entry_id = row["entry_id"]
                provenance = json.loads(row["provenance"]) if row["provenance"] else {}
                source_type = row["source_type"]
                source_id = row["source_id"]

                # Check required provenance fields
                if not source_type:
                    issues.append({"entry_id": entry_id, "issue": "missing source_type"})
                if not source_id:
                    issues.append({"entry_id": entry_id, "issue": "missing source_id"})
                if not provenance:
                    issues.append({"entry_id": entry_id, "issue": "empty provenance"})

        return {
            "total_entries": len(rows),
            "issues_found": len(issues),
            "integrity_pct": 100.0 if not issues else (1 - len(issues) / len(rows)) * 100,
            "issues": issues[:100],  # Limit to first 100 issues
        }

    def _row_to_entry(self, row: sqlite3.Row) -> KnowledgeEntry:
        """Convert database row to KnowledgeEntry."""
        return KnowledgeEntry(
            entry_id=row["entry_id"],
            version=row["version"],
            created_at=row["created_at"],
            problem_statement=json.loads(row["problem_statement"]),
            methodology=json.loads(row["methodology"]),
            evidence_chain=json.loads(row["evidence_chain"]),
            conclusion=json.loads(row["conclusion"]),
            uncertainty=json.loads(row["uncertainty"]) if row["uncertainty"] else {},
            provenance=json.loads(row["provenance"]),
            relations=json.loads(row["relations"]) if row["relations"] else [],
        )

    def _build_searchable_content(self, entry: KnowledgeEntry) -> str:
        """Build searchable text content from entry."""
        parts = []

        # Problem statement
        ps = entry.problem_statement
        if ps.get("goal"):
            parts.append(ps["goal"])
        if ps.get("context"):
            parts.append(ps["context"])

        # Methodology
        meth = entry.methodology
        if meth.get("approach"):
            parts.append(meth["approach"])

        # Conclusion
        conc = entry.conclusion
        if conc.get("summary"):
            parts.append(conc["summary"])
        for finding in conc.get("key_findings", []):
            parts.append(finding)

        return " ".join(parts)
