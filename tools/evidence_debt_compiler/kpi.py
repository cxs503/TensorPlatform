"""KPI calculator for the evidence debt upgrade pipeline.

Computes closure_rate and validates the invariant:
  n_upgraded + n_failed + n_remaining = n_WITHHELD_initial
"""
from __future__ import annotations

from typing import Any, Dict, List

from .model import PipelineKPI, LevelUpdate, VerificationOutcome


class KPICalculator:
    """Calculate pipeline KPIs from level update records."""

    def __init__(self, n_withheld_initial: int):
        self.n_withheld_initial = n_withheld_initial

    def compute(
        self,
        updates: List[LevelUpdate],
        pipeline_run_id: str = "",
        started_at: str = "",
    ) -> PipelineKPI:
        """Compute KPIs from a list of level updates."""
        n_upgraded = 0
        n_failed = 0
        n_remaining = 0

        for update in updates:
            if update.after_level == "REAL" and update.before_level == "WITHHELD":
                n_upgraded += 1
            elif update.after_level == "WITHHELD" and update.before_level == "WITHHELD":
                # Stayed WITHHELD — either failed verification or no evidence
                if "failed" in update.reason.lower() or "error" in update.reason.lower():
                    n_failed += 1
                else:
                    n_remaining += 1
            else:
                # Other transitions (e.g., WITHHELD → MOCK) count as remaining
                n_remaining += 1

        kpi = PipelineKPI(
            n_withheld_initial=self.n_withheld_initial,
            n_upgraded=n_upgraded,
            n_failed=n_failed,
            n_remaining=n_remaining,
            pipeline_run_id=pipeline_run_id,
            started_at=started_at,
        )
        kpi.compute()  # validates invariant and computes closure_rate
        return kpi

    def compute_from_outcomes(
        self,
        outcomes: List[Dict[str, Any]],
        pipeline_run_id: str = "",
        started_at: str = "",
    ) -> PipelineKPI:
        """Compute KPIs from verification outcome dicts.

        Each outcome dict should have:
          - capability_id: str
          - outcome: str (pass/fail/error/timeout/skipped)
          - before_level: str
          - after_level: str
        """
        updates = []
        for o in outcomes:
            updates.append(LevelUpdate(
                capability_id=o["capability_id"],
                debt_id=o.get("debt_id", ""),
                before_level=o.get("before_level", "WITHHELD"),
                after_level=o.get("after_level", "WITHHELD"),
                reason=o.get("reason", o.get("outcome", "unknown")),
            ))
        return self.compute(updates, pipeline_run_id, started_at)
