"""Core failure signature mining logic.

Collects failed/stopped missions from ``tensor_dev_missions``, extracts
evidence from ``mission_lifecycle_log`` and ``tensor_reports``, and
produces classified failure signatures.

Zero domain semantics: all domain vocabulary comes from external config
(domain blacklist injected at runtime), never hardcoded here.
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Mapping, Sequence

# ─────────────────────────────── 六类签名 ───────────────────────────────

SIGNATURE_TYPES: tuple[str, ...] = (
    "infra_lost",
    "timeout",
    "criteria_infeasible",
    "duplicate_seed",
    "physics_honest_fail",
    "report_owed",
)

# ─────────────────────────────── 数据视图 ───────────────────────────────

@dataclass(frozen=True)
class FailedMissionView:
    """Projection of a failed/stopped mission row + related evidence."""
    mission_id: int
    goal: str = ""
    repo: str = ""
    status: str = "failed"
    fail_stage: str | None = None
    result: Mapping[str, Any] = field(default_factory=dict)
    created_at: str | None = None
    finished_at: str | None = None
    claimed_by: str | None = None
    model: str | None = None
    lifecycle_events: list[dict] = field(default_factory=list)
    has_report: bool = False
    organization_id: int | None = None

    @classmethod
    def from_row(cls, row: Mapping[str, Any], *,
                 lifecycle_events: list[dict] | None = None,
                 has_report: bool = False) -> "FailedMissionView":
        result = row.get("result") or {}
        if isinstance(result, str):
            try:
                result = json.loads(result)
            except ValueError:
                result = {"_raw": result}
        return cls(
            mission_id=int(row["id"]),
            goal=str(row.get("goal") or ""),
            repo=str(row.get("repo") or ""),
            status=str(row.get("status") or "failed"),
            fail_stage=row.get("fail_stage"),
            result=result if isinstance(result, Mapping) else {"_raw": str(result)},
            created_at=_iso(row.get("created_at")),
            finished_at=_iso(row.get("finished_at")),
            claimed_by=row.get("claimed_by"),
            model=row.get("model"),
            lifecycle_events=lifecycle_events or [],
            has_report=has_report,
            organization_id=row.get("organization_id"),
        )


def _iso(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc).isoformat()
    return str(value)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# ─────────────────────────────── 证据提取 ───────────────────────────────

def extract_failure_reason(m: FailedMissionView) -> str:
    """Extract the most authoritative failure reason from mission evidence."""
    checks = (m.result.get("evidence") or {}).get("checks") if isinstance(m.result.get("evidence"), Mapping) else {}
    if isinstance(checks, Mapping):
        reason = checks.get("reason")
        if isinstance(reason, str) and reason.strip():
            return _one_line(reason)
    headline = m.result.get("headline")
    if isinstance(headline, str) and headline.strip():
        return _one_line(headline)
    stop = m.result.get("stop_condition")
    if isinstance(stop, str) and stop.strip():
        return _one_line(stop)
    if m.fail_stage:
        return str(m.fail_stage)
    return ""


def extract_error_code(m: FailedMissionView) -> str:
    """Extract error code / exit code from mission result."""
    checks = (m.result.get("evidence") or {}).get("checks") if isinstance(m.result.get("evidence"), Mapping) else {}
    if isinstance(checks, Mapping):
        code = checks.get("error_code") or checks.get("exit_code")
        if code is not None:
            return str(code)
    code = m.result.get("exit_code")
    if code is not None:
        return str(code)
    return "unknown"


def extract_file_fingerprint(m: FailedMissionView) -> str:
    """Extract file path fingerprint from changed_files or error context."""
    checks = (m.result.get("evidence") or {}).get("checks") if isinstance(m.result.get("evidence"), Mapping) else {}
    if isinstance(checks, Mapping):
        files = checks.get("changed_files")
        if isinstance(files, list) and files:
            return _file_hash(files)
    changed = m.result.get("changed_files")
    if isinstance(changed, list) and changed:
        return _file_hash(changed)
    return "no_files"


def _file_hash(files: list[str]) -> str:
    """Deterministic hash of sorted file list."""
    normalized = sorted(set(f.split("/")[-1] for f in files if f))
    return hashlib.sha256("|".join(normalized).encode()).hexdigest()[:12]


def _one_line(text: str, limit: int = 400) -> str:
    """Collapse multi-line text to single line."""
    return " ".join(text.split())[:limit]


# ─────────────────────────────── 签名计算 ───────────────────────────────

@dataclass(frozen=True)
class FailureSignature:
    """Normalized failure signature for clustering and dedup."""
    signature_type: str  # one of SIGNATURE_TYPES
    error_code: str
    file_fingerprint: str
    reason_hash: str  # SHA256[:16] of normalized reason
    mission_ids: list[int] = field(default_factory=list)
    evidence_sql: str = ""  # SQL trace string for provenance
    frequency: int = 0
    impact_score: float = 0.0

    @property
    def signature_hash(self) -> str:
        """Deterministic hash for idempotent ledger key."""
        payload = json.dumps({
            "signature_type": self.signature_type,
            "error_code": self.error_code,
            "file_fingerprint": self.file_fingerprint,
            "reason_hash": self.reason_hash,
        }, sort_keys=True)
        return hashlib.sha256(payload.encode()).hexdigest()[:16]

    def to_dict(self) -> dict[str, Any]:
        return {
            "signature_hash": self.signature_hash,
            "signature_type": self.signature_type,
            "error_code": self.error_code,
            "file_fingerprint": self.file_fingerprint,
            "reason_hash": self.reason_hash,
            "mission_ids": self.mission_ids,
            "evidence_sql": self.evidence_sql,
            "frequency": self.frequency,
            "impact_score": round(self.impact_score, 4),
        }


def compute_reason_hash(reason: str) -> str:
    """SHA256[:16] of normalized reason text."""
    normalized = " ".join(reason.split())
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:16]


# ─────────────────────────────── 采集器 ───────────────────────────────

def collect_failed_missions(db_session, *, days: int = 30, limit: int = 500) -> list[FailedMissionView]:
    """Collect failed/stopped missions from tensor_dev_missions.

    Args:
        db_session: SQLAlchemy session (or mock for testing)
        days: lookback window in days
        limit: max missions to fetch

    Returns:
        List of FailedMissionView with lifecycle events and report status.
    """
    from sqlalchemy import text

    # Fetch failed/stopped missions
    rows = db_session.execute(
        text("""
            SELECT id, organization_id, goal, repo, base_ref, status,
                   fail_stage, result, claimed_by, model,
                   created_at, finished_at
            FROM tensor_dev_missions
            WHERE status IN ('failed', 'stopped')
              AND finished_at >= now() - INTERVAL :days DAY
            ORDER BY finished_at DESC
            LIMIT :limit
        """),
        {"days": days, "limit": limit},
    ).mappings().all()

    views = []
    for row in rows:
        mid = int(row["id"])

        # Fetch lifecycle events
        events = db_session.execute(
            text("""
                SELECT action, detail, created_at
                FROM mission_lifecycle_log
                WHERE mission_id = :mid
                ORDER BY created_at ASC
            """),
            {"mid": mid},
        ).mappings().all()

        # Check for associated report
        report_row = db_session.execute(
            text("""
                SELECT id FROM tensor_reports
                WHERE mission_id = :mid AND status = 'published'
                LIMIT 1
            """),
            {"mid": mid},
        ).mappings().first()

        views.append(FailedMissionView.from_row(
            row,
            lifecycle_events=[dict(e) for e in events],
            has_report=report_row is not None,
        ))

    return views


def collect_failed_missions_sqlite(db_session, *, days: int = 30, limit: int = 500) -> list[FailedMissionView]:
    """SQLite-compatible variant (no INTERVAL syntax)."""
    from sqlalchemy import text
    from datetime import timedelta

    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()

    rows = db_session.execute(
        text("""
            SELECT id, organization_id, goal, repo, base_ref, status,
                   fail_stage, result, claimed_by, model,
                   created_at, finished_at
            FROM tensor_dev_missions
            WHERE status IN ('failed', 'stopped')
              AND finished_at >= :cutoff
            ORDER BY finished_at DESC
            LIMIT :limit
        """),
        {"cutoff": cutoff, "limit": limit},
    ).mappings().all()

    views = []
    for row in rows:
        mid = int(row["id"])

        events = db_session.execute(
            text("""
                SELECT action, detail, created_at
                FROM mission_lifecycle_log
                WHERE mission_id = :mid
                ORDER BY created_at ASC
            """),
            {"mid": mid},
        ).mappings().all()

        report_row = db_session.execute(
            text("""
                SELECT id FROM tensor_reports
                WHERE mission_id = :mid AND status = 'published'
                LIMIT 1
            """),
            {"mid": mid},
        ).mappings().first()

        views.append(FailedMissionView.from_row(
            row,
            lifecycle_events=[dict(e) for e in events],
            has_report=report_row is not None,
        ))

    return views
