"""Evidence debt compiler orchestrator (P519-N01).

Main pipeline: Extract → Classify → Generate Tasks → Execute → Update Levels → KPI

Idempotency: tracks processed debt digests; repeated runs skip already-processed debts.
Zero-domain-semantics: all domain data arrives via injection.
"""
from __future__ import annotations

import json
import sqlite3
import time
import uuid
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Set

from .classifier import BaselineClassifier
from .extractor import EvidenceDebtExtractor
from .kpi import KPICalculator
from .model import (
    BaselineType,
    ClassifiedDebt,
    ExtractedDebt,
    LevelUpdate,
    PipelineKPI,
    VerificationOutcome,
    VerificationResult,
    VerificationTask,
    content_digest,
    now_iso,
)
from .task_generator import VerificationTaskGenerator


class EvidenceDebtCompiler:
    """Main orchestrator for the WITHHELD→REAL upgrade pipeline.

    Parameters
    ----------
    db_path : str or Path
        SQLite database path for tracking processed debts (idempotency).
    extractor : EvidenceDebtExtractor, optional
    classifier : BaselineClassifier, optional
    task_generator : VerificationTaskGenerator, optional
    verification_executor : callable, optional
        Function(task: VerificationTask) -> VerificationResult.
        If None, tasks are marked as SKIPPED (dry-run mode).
    level_updater : callable, optional
        Function(capability_id, new_level, reason) -> bool.
        If None, updates are recorded but not applied externally.
    """

    def __init__(
        self,
        db_path: str | Path = ":memory:",
        extractor: Optional[EvidenceDebtExtractor] = None,
        classifier: Optional[BaselineClassifier] = None,
        task_generator: Optional[VerificationTaskGenerator] = None,
        verification_executor: Optional[Callable[[VerificationTask], VerificationResult]] = None,
        level_updater: Optional[Callable[[str, str, str], bool]] = None,
    ):
        self.extractor = extractor or EvidenceDebtExtractor()
        self.classifier = classifier or BaselineClassifier()
        self.task_generator = task_generator or VerificationTaskGenerator()
        self.verification_executor = verification_executor
        self.level_updater = level_updater

        # Idempotency tracking
        self.db_path = str(db_path)
        self._init_db()

    def _init_db(self) -> None:
        """Initialize SQLite database for tracking processed debts."""
        self.conn = sqlite3.connect(self.db_path)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS processed_debts (
                debt_digest TEXT PRIMARY KEY,
                debt_id TEXT NOT NULL,
                capability_id TEXT NOT NULL,
                pipeline_run_id TEXT NOT NULL,
                processed_at TEXT NOT NULL,
                outcome TEXT NOT NULL
            )
        """)
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS pipeline_runs (
                run_id TEXT PRIMARY KEY,
                started_at TEXT NOT NULL,
                finished_at TEXT,
                n_withheld_initial INTEGER DEFAULT 0,
                n_upgraded INTEGER DEFAULT 0,
                n_failed INTEGER DEFAULT 0,
                n_remaining INTEGER DEFAULT 0,
                closure_rate REAL DEFAULT 0.0,
                status TEXT DEFAULT 'running'
            )
        """)
        self.conn.commit()

    def _is_debt_processed(self, debt_digest: str) -> bool:
        """Check if a debt has already been processed (idempotency)."""
        row = self.conn.execute(
            "SELECT 1 FROM processed_debts WHERE debt_digest = ?",
            (debt_digest,)
        ).fetchone()
        return row is not None

    def _mark_debt_processed(
        self,
        debt: ExtractedDebt,
        pipeline_run_id: str,
        outcome: str,
    ) -> None:
        """Mark a debt as processed."""
        self.conn.execute(
            "INSERT OR REPLACE INTO processed_debts "
            "(debt_digest, debt_id, capability_id, pipeline_run_id, processed_at, outcome) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (debt.digest, debt.debt_id, debt.capability_id, pipeline_run_id, now_iso(), outcome),
        )
        self.conn.commit()

    def run(
        self,
        reports: List[Dict[str, Any]],
        dry_run: bool = False,
    ) -> Dict[str, Any]:
        """Run the full pipeline on a list of reports.

        Parameters
        ----------
        reports : list of dict
            Each report should have:
              - report_id: str
              - text: str (or markdown: str)
              - capabilities: dict (optional, structured capability declarations)
        dry_run : bool
            If True, skip verification execution (generate tasks only).

        Returns
        -------
        dict with keys:
          - pipeline_run_id: str
          - extracted_debts: list
          - classified_debts: list
          - verification_tasks: list
          - verification_results: list
          - level_updates: list
          - kpi: dict
          - skipped_duplicate: int
        """
        pipeline_run_id = f"run-{uuid.uuid4().hex[:12]}"
        started_at = now_iso()

        # Record pipeline run
        self.conn.execute(
            "INSERT INTO pipeline_runs (run_id, started_at) VALUES (?, ?)",
            (pipeline_run_id, started_at),
        )
        self.conn.commit()

        # Phase 1: Extract debts from all reports
        all_debts: List[ExtractedDebt] = []
        for report in reports:
            report_id = report.get("report_id", "")
            debts = self.extractor.extract_from_structured(report, report_id)
            all_debts.extend(debts)

        # Deduplicate and filter already-processed debts
        unique_debts: List[ExtractedDebt] = []
        seen_digests: Set[str] = set()
        skipped_duplicate = 0

        for debt in all_debts:
            if debt.digest in seen_digests:
                continue
            seen_digests.add(debt.digest)
            if self._is_debt_processed(debt.digest):
                skipped_duplicate += 1
                continue
            unique_debts.append(debt)

        n_withheld_initial = len(unique_debts)

        # Phase 2: Classify debts
        classified_debts = self.classifier.classify_batch(unique_debts)

        # Phase 3: Generate verification tasks
        verification_tasks = self.task_generator.generate_batch(classified_debts)

        # Phase 4: Execute verification tasks
        verification_results: List[VerificationResult] = []
        level_updates: List[LevelUpdate] = []

        for task in verification_tasks:
            if dry_run or self.verification_executor is None:
                # Dry-run mode: mark as skipped
                result = VerificationResult(
                    task_id=task.task_id,
                    debt_id=task.debt_id,
                    capability_id=task.capability_id,
                    outcome=VerificationOutcome.SKIPPED,
                    metrics={},
                    executed_at=now_iso(),
                )
            else:
                try:
                    result = self.verification_executor(task)
                except Exception as e:
                    result = VerificationResult(
                        task_id=task.task_id,
                        debt_id=task.debt_id,
                        capability_id=task.capability_id,
                        outcome=VerificationOutcome.ERROR,
                        error_message=str(e),
                        executed_at=now_iso(),
                    )

            verification_results.append(result)

            # Phase 5: Determine level update
            debt = next(d for d in unique_debts if d.debt_id == task.debt_id)
            before_level = "WITHHELD"

            if result.outcome == VerificationOutcome.PASS:
                after_level = "REAL"
                reason = "verification passed"
            elif result.outcome == VerificationOutcome.FAIL:
                after_level = "WITHHELD"
                reason = "verification failed"
            elif result.outcome == VerificationOutcome.ERROR:
                after_level = "WITHHELD"
                reason = f"verification error: {result.error_message}"
            elif result.outcome == VerificationOutcome.TIMEOUT:
                after_level = "WITHHELD"
                reason = "verification timeout"
            else:  # SKIPPED
                after_level = "WITHHELD"
                reason = "verification skipped (dry-run or no executor)"

            update = LevelUpdate(
                capability_id=task.capability_id,
                debt_id=task.debt_id,
                before_level=before_level,
                after_level=after_level,
                reason=reason,
            )
            level_updates.append(update)

            # Apply level update if updater is provided
            if self.level_updater and after_level != before_level:
                try:
                    self.level_updater(task.capability_id, after_level, reason)
                except Exception:
                    pass  # record update but don't fail pipeline

            # Mark debt as processed
            outcome = "upgraded" if after_level == "REAL" else "remaining_withheld"
            self._mark_debt_processed(debt, pipeline_run_id, outcome)

        # Phase 6: Compute KPIs
        calculator = KPICalculator(n_withheld_initial)
        kpi = calculator.compute(level_updates, pipeline_run_id, started_at)
        kpi.finished_at = now_iso()

        # Update pipeline run record
        finished_at = now_iso()
        self.conn.execute(
            "UPDATE pipeline_runs SET finished_at=?, n_withheld_initial=?, "
            "n_upgraded=?, n_failed=?, n_remaining=?, closure_rate=?, status='completed' "
            "WHERE run_id=?",
            (
                finished_at,
                kpi.n_withheld_initial,
                kpi.n_upgraded,
                kpi.n_failed,
                kpi.n_remaining,
                kpi.closure_rate,
                pipeline_run_id,
            ),
        )
        self.conn.commit()

        return {
            "pipeline_run_id": pipeline_run_id,
            "extracted_debts": [d.as_dict() for d in unique_debts],
            "classified_debts": [c.as_dict() for c in classified_debts],
            "verification_tasks": [t.as_dict() for t in verification_tasks],
            "verification_results": [r.as_dict() for r in verification_results],
            "level_updates": [u.as_dict() for u in level_updates],
            "kpi": kpi.as_dict(),
            "skipped_duplicate": skipped_duplicate,
        }

    def close(self) -> None:
        """Close the database connection."""
        if self.conn:
            self.conn.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()
