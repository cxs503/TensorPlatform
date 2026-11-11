"""Capability Ledger Integration — update capability台账 after experiment reports.

After a successful experiment report is generated, this module:
  1. Extracts capability evidence from the report
  2. Updates the capability ledger with new evidence
  3. Records the experiment as a capability validation

Zero domain semantics: capability names and evidence are injected via config.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .reporter import ReportPayload
from .verifier import VerificationResult


@dataclass
class CapabilityUpdate:
    """A capability ledger update from an experiment."""
    capability_id: str
    evidence_type: str
    evidence_value: Any
    experiment_id: str
    report_id: str
    timestamp: str
    verdict: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "capability_id": self.capability_id,
            "evidence_type": self.evidence_type,
            "evidence_value": self.evidence_value,
            "experiment_id": self.experiment_id,
            "report_id": self.report_id,
            "timestamp": self.timestamp,
            "verdict": self.verdict,
        }


class CapabilityLedger_Integrator:
    """Integrates experiment results into the capability ledger."""

    def __init__(self, ledger_path: str = "artifacts/capability_ledger/capability_ledger.json"):
        self.ledger_path = ledger_path

    def update_from_report(self, report: ReportPayload,
                           capability_mapping: dict[str, str] | None = None) -> list[CapabilityUpdate]:
        """Update capability ledger from a report.
        
        Args:
            report: Generated report payload
            capability_mapping: Optional mapping from experiment_id to capability_id
            
        Returns:
            List of capability updates made
        """
        updates = []
        
        # Extract capability evidence from report
        experiment_id = report.experiment_id
        report_id = report.report_id
        verification = report.content.get("sections", {}).get("verification", {})
        
        # Determine capability ID
        capability_id = (capability_mapping or {}).get(experiment_id, experiment_id)
        
        # Extract evidence
        evidence = self._extract_evidence(report)
        
        for evidence_type, evidence_value in evidence.items():
            update = CapabilityUpdate(
                capability_id=capability_id,
                evidence_type=evidence_type,
                evidence_value=evidence_value,
                experiment_id=experiment_id,
                report_id=report_id,
                timestamp=datetime.now(timezone.utc).isoformat(),
                verdict=verification.get("verdict", "unknown"),
            )
            updates.append(update)
        
        # Write updates to ledger
        if updates:
            self._append_to_ledger(updates)
        
        return updates

    def _extract_evidence(self, report: ReportPayload) -> dict[str, Any]:
        """Extract capability evidence from a report."""
        evidence = {}
        
        content = report.content
        verification = content.get("sections", {}).get("verification", {})
        execution = content.get("sections", {}).get("execution", {})
        baseline_comp = content.get("sections", {}).get("baseline_comparison", {})
        
        # Verification evidence
        if verification.get("passed") is not None:
            evidence["verification_passed"] = verification["passed"]
        
        if verification.get("drift_class"):
            evidence["drift_class"] = verification["drift_class"]
        
        if verification.get("criteria_passed"):
            evidence["criteria_passed"] = verification["criteria_passed"]
        
        # Execution evidence
        if execution.get("exit_code") is not None:
            evidence["exit_code"] = execution["exit_code"]
        
        if execution.get("duration_sec") is not None:
            evidence["duration_sec"] = execution["duration_sec"]
        
        # Baseline comparison evidence
        if baseline_comp.get("available"):
            evidence["baseline_drift_class"] = baseline_comp.get("drift_class")
        
        # Audit chain evidence
        audit_chain = content.get("sections", {}).get("audit_chain", [])
        if audit_chain:
            evidence["audit_chain_length"] = len(audit_chain)
            evidence["audit_chain_hashes"] = [item.get("sha256") for item in audit_chain]
        
        return evidence

    def _append_to_ledger(self, updates: list[CapabilityUpdate]) -> None:
        """Append updates to the capability ledger file."""
        ledger_file = Path(self.ledger_path)
        ledger_file.parent.mkdir(parents=True, exist_ok=True)
        
        # Load existing ledger
        ledger = []
        if ledger_file.exists():
            try:
                with open(ledger_file, "r", encoding="utf-8") as f:
                    ledger = json.load(f)
            except (json.JSONDecodeError, IOError):
                ledger = []
        
        # Append new updates
        for update in updates:
            ledger.append(update.to_dict())
        
        # Write back
        with open(ledger_file, "w", encoding="utf-8") as f:
            json.dump(ledger, f, ensure_ascii=False, indent=2)


if __name__ == "__main__":
    # Quick test
    from .reporter import ReportPayload
    
    report = ReportPayload(
        report_id="test_report_001",
        experiment_id="synthetic_exp_001",
        content={
            "sections": {
                "verification": {"passed": True, "verdict": "pass", "drift_class": "bit_exact"},
                "execution": {"exit_code": 0, "duration_sec": 1.5},
                "baseline_comparison": {"available": True, "drift_class": "bit_exact"},
                "audit_chain": [{"stage": "prereg", "sha256": "abc123"}],
            }
        },
        artifacts=[],
        generated_at=datetime.now(timezone.utc).isoformat(),
    )
    
    integrator = Capability_Ledger_Integrator(ledger_path="/tmp/test_ledger.json")
    updates = integrator.update_from_report(report)
    print(f"Generated {len(updates)} capability updates")
    for update in updates:
        print(f"  {update.capability_id}: {update.evidence_type} = {update.evidence_value}")
