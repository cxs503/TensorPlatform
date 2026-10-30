"""
Credit Ledger — Records verification outcomes for accountability.

The credit ledger tracks:
  - Which candidates were verified
  - Which questions they passed/failed
  - The verifier that issued the verdict
  - Timestamps and evidence hashes

This is the "proof of work" for the AlphaEvolve-style separation:
  - Workers cannot self-certify
  - Only verifier-issued credits count
  - Self-tests are explicitly excluded from acceptance
"""
from __future__ import annotations

import json
import os
import time
import uuid
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Optional


@dataclass
class CreditEntry:
    """A single credit ledger entry."""
    id: str
    candidate_id: str
    candidate_version: str
    mission_id: int | None
    verifier_id: str
    domain: str
    verdict: str  # "pass", "fail", "error"
    questions_passed: int
    questions_failed: int
    questions_total: int
    pass_rate: float
    evidence_hash: str  # SHA-256 of the verification report
    self_test_result: str | None = None  # Explicitly tracked but NOT used for acceptance
    self_test_note: str = "Self-test results are informational only; acceptance requires independent verification"
    created_at: float = field(default_factory=time.time)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class CreditLedger:
    """
    File-based credit ledger for verification outcomes.

    Stored as JSONL (one entry per line) for append-only semantics.
    Location: artifacts/credit_ledger.jsonl
    """

    def __init__(self, ledger_path: str = None):
        if ledger_path is None:
            # Default to artifacts directory in the worktree
            base = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
            artifacts_dir = os.path.join(base, "artifacts")
            os.makedirs(artifacts_dir, exist_ok=True)
            ledger_path = os.path.join(artifacts_dir, "credit_ledger.jsonl")
        self.ledger_path = ledger_path

    def record(self, entry: CreditEntry) -> str:
        """Append a credit entry to the ledger."""
        with open(self.ledger_path, "a") as f:
            f.write(json.dumps(entry.to_dict(), ensure_ascii=False, default=str) + "\n")
        return entry.id

    def record_from_report(self, report: dict, mission_id: int = None,
                           self_test_result: str = None) -> CreditEntry:
        """
        Create and record a credit entry from a verification report.

        Args:
            report: VerificationReport.to_dict() output
            mission_id: Optional mission ID for traceability
            self_test_result: Optional self-test result (informational only)

        Returns:
            The created CreditEntry
        """
        import hashlib
        report_hash = hashlib.sha256(
            json.dumps(report, sort_keys=True, ensure_ascii=False, default=str).encode()
        ).hexdigest()

        total = report.get("total_questions", 0)
        passed = report.get("pass_count", 0)
        failed = report.get("fail_count", 0)

        entry = CreditEntry(
            id=str(uuid.uuid4()),
            candidate_id=report.get("candidate_id", "unknown"),
            candidate_version=report.get("candidate_version", "unknown"),
            mission_id=mission_id,
            verifier_id=report.get("verifier_id", "unknown"),
            domain=report.get("domain", "unknown"),
            verdict=report.get("overall_verdict", "error"),
            questions_passed=passed,
            questions_failed=failed,
            questions_total=total,
            pass_rate=passed / total if total > 0 else 0.0,
            evidence_hash=report_hash,
            self_test_result=self_test_result,
            metadata={
                "started_at": report.get("started_at"),
                "finished_at": report.get("finished_at"),
            },
        )
        self.record(entry)
        return entry

    def query(self, candidate_id: str = None, mission_id: int = None,
              verdict: str = None) -> list[CreditEntry]:
        """Query the ledger for entries matching criteria."""
        entries = []
        if not os.path.exists(self.ledger_path):
            return entries
        with open(self.ledger_path, "r") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                d = json.loads(line)
                if candidate_id and d.get("candidate_id") != candidate_id:
                    continue
                if mission_id is not None and d.get("mission_id") != mission_id:
                    continue
                if verdict and d.get("verdict") != verdict:
                    continue
                entries.append(CreditEntry(**d))
        return entries

    def get_latest(self, candidate_id: str = None) -> Optional[CreditEntry]:
        """Get the latest credit entry, optionally for a specific candidate."""
        entries = self.query(candidate_id=candidate_id)
        return entries[-1] if entries else None

    def summary(self) -> dict[str, Any]:
        """Get a summary of the ledger."""
        if not os.path.exists(self.ledger_path):
            return {"total_entries": 0}
        entries = self.query()
        pass_count = sum(1 for e in entries if e.verdict == "pass")
        fail_count = sum(1 for e in entries if e.verdict == "fail")
        error_count = sum(1 for e in entries if e.verdict == "error")
        return {
            "total_entries": len(entries),
            "pass": pass_count,
            "fail": fail_count,
            "error": error_count,
            "ledger_path": self.ledger_path,
        }
