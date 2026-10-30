"""
Verifier Question Bank — AlphaEvolve-style held-out test mechanism.

Core principle (from DeepMind FunSearch/AlphaEvolve):
  - Candidate generation and evaluation are STRICTLY SEPARATED.
  - The verifier holds questions that the worker/implementation CANNOT see.
  - Only candidates that pass the verifier's independent judgment survive.

This module manages a SQLite-backed question bank with:
  - Permission isolation: question bank file is chmod 600, owned by verifier role
  - Immutable questions: once committed, questions cannot be modified
  - Audit trail: every question has provenance (spec/literature reference)
  - Versioning: questions are versioned to prevent replay attacks
"""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import stat
import time
import uuid
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Optional


# Default question bank location — separate from main application data
DEFAULT_QBANK_DIR = os.environ.get(
    "TP_VERIFIER_QBANK_DIR",
    os.path.join(os.path.dirname(__file__), "question_bank")
)
DEFAULT_QBANK_PATH = os.path.join(DEFAULT_QBANK_DIR, "questions.db")


@dataclass
class Question:
    """A single verification question (test case)."""
    id: str                          # UUID
    domain: str                      # e.g. "resistance", "stability", "propulsion"
    difficulty: str                  # "basic", "intermediate", "advanced"
    title: str                       # Short human-readable title
    specification: dict[str, Any]    # Input parameters / geometry / conditions
    expected_result: dict[str, Any]  # Reference values with tolerances
    tolerances: dict[str, float]     # Per-metric relative tolerance (e.g. {"Cf": 0.03})
    provenance: dict[str, str]       # {"source": "ITTC-1957", "ref": "...", "page": "..."}
    tags: list[str] = field(default_factory=list)
    version: int = 1
    created_at: float = field(default_factory=time.time)
    committed: bool = False          # Once committed, immutable
    commit_hash: str = ""            # SHA-256 of frozen content

    def freeze(self) -> None:
        """Freeze question — compute commit hash, mark immutable."""
        payload = json.dumps({
            "domain": self.domain,
            "title": self.title,
            "specification": self.specification,
            "expected_result": self.expected_result,
            "tolerances": self.tolerances,
            "version": self.version,
        }, sort_keys=True, ensure_ascii=False)
        self.commit_hash = hashlib.sha256(payload.encode()).hexdigest()
        self.committed = True

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class QuestionBank:
    """
    SQLite-backed question bank with permission isolation.

    Security model:
      - The DB file is created with mode 0o600 (owner read/write only)
      - The directory is created with mode 0o700
      - Worker processes running as a different user cannot read the DB
      - Even same-user workers should not have the QBANK path in their env
    """

    def __init__(self, db_path: str = DEFAULT_QBANK_PATH):
        self.db_path = db_path
        self._ensure_permissions()
        self._init_db()

    def _ensure_permissions(self) -> None:
        """Ensure the question bank directory and file have restricted permissions."""
        db_dir = os.path.dirname(self.db_path)
        os.makedirs(db_dir, mode=0o700, exist_ok=True)
        # Set directory permissions (may already exist)
        try:
            os.chmod(db_dir, 0o700)
        except OSError:
            pass  # May not own the directory

        # If DB already exists, verify permissions
        if os.path.exists(self.db_path):
            st = os.stat(self.db_path)
            mode = stat.S_IMODE(st.st_mode)
            if mode & 0o077:  # Group or other has any permission
                raise PermissionError(
                    f"Question bank {self.db_path} has insecure permissions "
                    f"{oct(mode)}; expected 0o600. "
                    f"Run: chmod 600 {self.db_path}"
                )

    def _init_db(self) -> None:
        """Initialize the question bank schema."""
        conn = self._connect()
        try:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS questions (
                    id TEXT PRIMARY KEY,
                    domain TEXT NOT NULL,
                    difficulty TEXT NOT NULL,
                    title TEXT NOT NULL,
                    specification TEXT NOT NULL,  -- JSON
                    expected_result TEXT NOT NULL, -- JSON
                    tolerances TEXT NOT NULL,      -- JSON
                    provenance TEXT NOT NULL,      -- JSON
                    tags TEXT NOT NULL,            -- JSON array
                    version INTEGER NOT NULL DEFAULT 1,
                    created_at REAL NOT NULL,
                    committed INTEGER NOT NULL DEFAULT 0,
                    commit_hash TEXT NOT NULL DEFAULT ''
                );

                CREATE TABLE IF NOT EXISTS verification_runs (
                    id TEXT PRIMARY KEY,
                    question_id TEXT NOT NULL,
                    candidate_id TEXT NOT NULL,
                    candidate_version TEXT,
                    verdict TEXT NOT NULL,         -- 'pass', 'fail', 'error'
                    actual_result TEXT,            -- JSON
                    deviations TEXT,               -- JSON: per-metric deviation
                    details TEXT,                  -- JSON: full details
                    verifier_id TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    FOREIGN KEY (question_id) REFERENCES questions(id)
                );

                CREATE INDEX IF NOT EXISTS idx_questions_domain ON questions(domain);
                CREATE INDEX IF NOT EXISTS idx_runs_question ON verification_runs(question_id);
                CREATE INDEX IF NOT EXISTS idx_runs_candidate ON verification_runs(candidate_id);
            """)
            conn.commit()
        finally:
            conn.close()

        # Set file permissions after creation
        if os.path.exists(self.db_path):
            os.chmod(self.db_path, 0o600)

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.db_path, timeout=5)

    def add_question(self, q: Question) -> str:
        """Add a question to the bank. Returns question ID."""
        if not q.id:
            q.id = str(uuid.uuid4())
        conn = self._connect()
        try:
            conn.execute(
                """INSERT INTO questions
                   (id, domain, difficulty, title, specification, expected_result,
                    tolerances, provenance, tags, version, created_at, committed, commit_hash)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (q.id, q.domain, q.difficulty, q.title,
                 json.dumps(q.specification, ensure_ascii=False),
                 json.dumps(q.expected_result, ensure_ascii=False),
                 json.dumps(q.tolerances, ensure_ascii=False),
                 json.dumps(q.provenance, ensure_ascii=False),
                 json.dumps(q.tags, ensure_ascii=False),
                 q.version, q.created_at,
                 1 if q.committed else 0, q.commit_hash)
            )
            conn.commit()
        finally:
            conn.close()
        return q.id

    def commit_question(self, question_id: str) -> None:
        """Freeze a question — make it immutable."""
        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT domain, title, specification, expected_result, tolerances, version "
                "FROM questions WHERE id = ?", (question_id,)
            ).fetchone()
            if not row:
                raise ValueError(f"Question {question_id} not found")
            payload = json.dumps({
                "domain": row[0], "title": row[1],
                "specification": row[2], "expected_result": row[3],
                "tolerances": row[4], "version": row[5],
            }, sort_keys=True, ensure_ascii=False)
            commit_hash = hashlib.sha256(payload.encode()).hexdigest()
            conn.execute(
                "UPDATE questions SET committed = 1, commit_hash = ? WHERE id = ?",
                (commit_hash, question_id)
            )
            conn.commit()
        finally:
            conn.close()

    def get_question(self, question_id: str) -> Optional[Question]:
        """Retrieve a question by ID."""
        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT * FROM questions WHERE id = ?", (question_id,)
            ).fetchone()
            if not row:
                return None
            cols = [d[0] for d in conn.execute(
                "SELECT * FROM questions LIMIT 0"
            ).description]
            d = dict(zip(cols, row))
            return Question(
                id=d["id"],
                domain=d["domain"],
                difficulty=d["difficulty"],
                title=d["title"],
                specification=json.loads(d["specification"]),
                expected_result=json.loads(d["expected_result"]),
                tolerances=json.loads(d["tolerances"]),
                provenance=json.loads(d["provenance"]),
                tags=json.loads(d["tags"]),
                version=d["version"],
                created_at=d["created_at"],
                committed=bool(d["committed"]),
                commit_hash=d["commit_hash"],
            )
        finally:
            conn.close()

    def list_questions(self, domain: str = None, committed_only: bool = False) -> list[dict]:
        """List questions, optionally filtered by domain and/or committed status."""
        conn = self._connect()
        try:
            query = "SELECT id, domain, difficulty, title, committed, commit_hash, provenance FROM questions WHERE 1=1"
            params = []
            if domain:
                query += " AND domain = ?"
                params.append(domain)
            if committed_only:
                query += " AND committed = 1"
            query += " ORDER BY created_at DESC"
            rows = conn.execute(query, params).fetchall()
            return [
                {
                    "id": r[0], "domain": r[1], "difficulty": r[2],
                    "title": r[3], "committed": bool(r[4]),
                    "commit_hash": r[5],
                    "provenance": json.loads(r[6]) if r[6] else {},
                }
                for r in rows
            ]
        finally:
            conn.close()

    def get_exam(self, domain: str, n: int = None, difficulty: str = None) -> list[Question]:
        """
        Get a set of questions for verification (an 'exam').
        Only returns committed (frozen) questions.
        """
        conn = self._connect()
        try:
            query = "SELECT * FROM questions WHERE committed = 1 AND domain = ?"
            params: list = [domain]
            if difficulty:
                query += " AND difficulty = ?"
                params.append(difficulty)
            query += " ORDER BY RANDOM()"
            if n:
                query += " LIMIT ?"
                params.append(n)
            rows = conn.execute(query, params).fetchall()
            cols = [d[0] for d in conn.execute(
                "SELECT * FROM questions LIMIT 0"
            ).description]
            results = []
            for row in rows:
                d = dict(zip(cols, row))
                results.append(Question(
                    id=d["id"], domain=d["domain"], difficulty=d["difficulty"],
                    title=d["title"],
                    specification=json.loads(d["specification"]),
                    expected_result=json.loads(d["expected_result"]),
                    tolerances=json.loads(d["tolerances"]),
                    provenance=json.loads(d["provenance"]),
                    tags=json.loads(d["tags"]),
                    version=d["version"], created_at=d["created_at"],
                    committed=bool(d["committed"]), commit_hash=d["commit_hash"],
                ))
            return results
        finally:
            conn.close()

    def record_verdict(self, question_id: str, candidate_id: str,
                       verdict: str, actual_result: dict = None,
                       deviations: dict = None, details: dict = None,
                       candidate_version: str = None,
                       verifier_id: str = "tp-ver-default") -> str:
        """Record a verification verdict."""
        run_id = str(uuid.uuid4())
        conn = self._connect()
        try:
            conn.execute(
                """INSERT INTO verification_runs
                   (id, question_id, candidate_id, candidate_version, verdict,
                    actual_result, deviations, details, verifier_id, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (run_id, question_id, candidate_id, candidate_version, verdict,
                 json.dumps(actual_result or {}, ensure_ascii=False),
                 json.dumps(deviations or {}, ensure_ascii=False),
                 json.dumps(details or {}, ensure_ascii=False),
                 verifier_id, time.time())
            )
            conn.commit()
        finally:
            conn.close()
        return run_id

    def get_verdicts(self, candidate_id: str = None,
                     question_id: str = None) -> list[dict]:
        """Get verification verdicts."""
        conn = self._connect()
        try:
            query = "SELECT * FROM verification_runs WHERE 1=1"
            params = []
            if candidate_id:
                query += " AND candidate_id = ?"
                params.append(candidate_id)
            if question_id:
                query += " AND question_id = ?"
                params.append(question_id)
            query += " ORDER BY created_at DESC"
            rows = conn.execute(query, params).fetchall()
            cols = [d[0] for d in conn.execute(
                "SELECT * FROM verification_runs LIMIT 0"
            ).description]
            results = []
            for row in rows:
                d = dict(zip(cols, row))
                results.append({
                    "run_id": d["id"],
                    "question_id": d["question_id"],
                    "candidate_id": d["candidate_id"],
                    "candidate_version": d["candidate_version"],
                    "verdict": d["verdict"],
                    "actual_result": json.loads(d["actual_result"]) if d["actual_result"] else {},
                    "deviations": json.loads(d["deviations"]) if d["deviations"] else {},
                    "details": json.loads(d["details"]) if d["details"] else {},
                    "verifier_id": d["verifier_id"],
                    "created_at": d["created_at"],
                })
            return results
        finally:
            conn.close()

    def question_count(self, domain: str = None) -> int:
        conn = self._connect()
        try:
            if domain:
                return conn.execute(
                    "SELECT COUNT(*) FROM questions WHERE domain = ?", (domain,)
                ).fetchone()[0]
            return conn.execute("SELECT COUNT(*) FROM questions").fetchone()[0]
        finally:
            conn.close()


def check_worker_access(db_path: str = DEFAULT_QBANK_PATH) -> dict:
    """
    Check if the current process can access the question bank.
    Used in permission tests to verify isolation.
    """
    result = {"can_read": False, "can_write": False, "path": db_path}
    if not os.path.exists(db_path):
        result["error"] = "file does not exist"
        return result
    st = os.stat(db_path)
    mode = stat.S_IMODE(st.st_mode)
    result["file_mode"] = oct(mode)
    result["secure"] = (mode & 0o077) == 0
    try:
        with open(db_path, "r") as f:
            f.read(1)
        result["can_read"] = True
    except PermissionError:
        result["can_read"] = False
    except Exception as e:
        result["read_error"] = str(e)
    try:
        conn = sqlite3.connect(db_path)
        conn.execute("SELECT 1")
        conn.close()
        result["can_write"] = True
    except PermissionError:
        result["can_write"] = False
    except Exception as e:
        result["write_error"] = str(e)
    return result
