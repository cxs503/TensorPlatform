"""Re-adjudication engine: criteria + evidence → evidence level (P218-N01).

The engine is the *only* place where a capability level is allowed to change.
It is deterministic: given the same evidence set and criteria it must return the
same level, whether it was triggered by an evidence-ingest event or manually
(C2 consistency requirement).

Adjudication rule
-----------------
1. Evaluate every criterion of the capability against its linked evidence.
2. A criterion whose ``target_level`` ranks higher than the current level and
   which passes upgrades the slot to that level (highest ranked wins).
3. If no criterion passes, the level is **left untouched** — never silently
   upgraded.  A slot therefore stays ``WITHHELD`` until machine-checkable
   evidence actually satisfies the acceptance rule (C4/C5).
4. Downgrades are never automatic here; contradictions are surfaced as
   ``failures`` in the history so an operator/审计 can act on them.
"""
from __future__ import annotations

import time
import uuid
from typing import Any, Dict, List, Optional

from .criteria import evaluate
from .model import (
    Criterion,
    CriterionVerdict,
    level_rank,
    now_iso,
)
from .store import Store


class RejudgeEngine:
    """Deterministic, auditable adjudicator over (evidence, criteria)."""

    def __init__(self, store: Store):
        self.store = store

    def judge(
        self,
        capability_id: str,
        trigger: str = "manual",
        evidence_ids: Optional[List[str]] = None,
        run_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        t0 = time.perf_counter()
        started_at = now_iso()

        slot = self.store.get_slot(capability_id)
        if slot is None:
            raise KeyError(f"unknown capability slot: {capability_id}")

        evidence = self.store.list_evidence(capability_id)
        criteria: List[Criterion] = self.store.list_criteria(capability_id)

        verdicts: List[CriterionVerdict] = [evaluate(c, evidence) for c in criteria]

        before = slot.current_level
        best_level = before
        passed_criteria: List[Dict[str, Any]] = []
        for crit, verdict in zip(criteria, verdicts):
            if not verdict.passed:
                continue
            if level_rank(crit.target_level) > level_rank(best_level):
                best_level = crit.target_level.upper()
            passed_criteria.append(crit.as_dict())

        after = best_level
        if level_rank(after) > level_rank(before):
            self.store.set_level(capability_id, after)
            outcome = "upgraded"
        elif criteria:
            outcome = "held"
        else:
            outcome = "unconfigured"

        failures: List[Dict[str, Any]] = []
        for verdict in verdicts:
            for failure in verdict.failures:
                failures.append({"criterion_id": verdict.criterion_id, **failure})

        finished_at = now_iso()
        latency_ms = (time.perf_counter() - t0) * 1000.0
        run_id = run_id or f"rj-{uuid.uuid4().hex[:16]}"
        linked = evidence_ids if evidence_ids is not None else [
            ev.evidence_id for ev in evidence
        ]

        run = {
            "run_id": run_id,
            "capability_id": capability_id,
            "trigger": trigger,
            "evidence_ids": linked,
            "started_at": started_at,
            "finished_at": finished_at,
            "latency_ms": round(latency_ms, 4),
            "before_level": before,
            "result_level": after,
            "outcome": outcome,
            "verdicts": [v.as_dict() for v in verdicts],
        }
        self.store.record_run(run)
        self.store.record_history(
            capability_id=capability_id,
            run_id=run_id,
            before_level=before,
            after_level=after,
            outcome=outcome,
            evidence_ids=linked,
            criteria=passed_criteria if passed_criteria else [
                c.as_dict() for c in criteria
            ],
            failures=failures,
            evaluated_at=finished_at,
            trigger=trigger,
        )
        return run