"""Lineage Binding Gate — validate report_id and artifacts before mission completion.

Before a mission can reach terminal state:
  1. report_id must be non-empty
  2. All referenced artifacts must exist on disk
  3. If missing, dispatch a supplementary report task

Zero domain semantics: operates purely on report/artifact metadata.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass
class LineageCheckResult:
    """Result of a lineage gate check."""
    passed: bool
    report_id: str | None
    artifacts_exist: bool
    missing_artifacts: list[str]
    supplementary_dispatched: bool
    details: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "passed": self.passed,
            "report_id": self.report_id,
            "artifacts_exist": self.artifacts_exist,
            "missing_artifacts": self.missing_artifacts,
            "supplementary_dispatched": self.supplementary_dispatched,
            "details": self.details,
        }


class LineageGate:
    """Validates lineage binding before mission terminal state."""

    def __init__(self, artifact_base_dir: str = "artifacts"):
        self.artifact_base_dir = artifact_base_dir

    def check(self, mission_state: dict[str, Any]) -> LineageCheckResult:
        """Check lineage binding for a mission.

        Args:
            mission_state: dict with keys like:
                - report_id: str | None
                - artifacts: list[str] (paths or identifiers)
                - report_payload: dict (optional, with artifact list)

        Returns:
            LineageCheckResult with pass/fail and details
        """
        report_id = mission_state.get("report_id")
        artifacts = self._extract_artifacts(mission_state)
        missing = self._check_artifacts(artifacts)

        # Check report_id
        has_report_id = report_id is not None and str(report_id).strip() != ""

        # Check artifacts
        artifacts_exist = len(missing) == 0

        # Overall pass
        passed = has_report_id and artifacts_exist

        # Dispatch supplementary report if needed
        supplementary_dispatched = False
        if not passed:
            supplementary_dispatched = self._dispatch_supplementary_report(mission_state, missing)

        return LineageCheckResult(
            passed=passed,
            report_id=report_id,
            artifacts_exist=artifacts_exist,
            missing_artifacts=missing,
            supplementary_dispatched=supplementary_dispatched,
            details={
                "has_report_id": has_report_id,
                "artifact_count": len(artifacts),
                "missing_count": len(missing),
            },
        )

    def _extract_artifacts(self, mission_state: dict[str, Any]) -> list[str]:
        """Extract artifact paths from mission state."""
        artifacts = mission_state.get("artifacts", [])
        if not artifacts:
            # Try to extract from report_payload
            report_payload = mission_state.get("report_payload")
            if isinstance(report_payload, dict):
                artifacts = report_payload.get("artifacts", [])
        return artifacts if isinstance(artifacts, list) else []

    def _check_artifacts(self, artifacts: list[str]) -> list[str]:
        """Check which artifacts exist on disk."""
        missing = []
        for artifact_path in artifacts:
            full_path = self._resolve_path(artifact_path)
            if not os.path.exists(full_path):
                missing.append(artifact_path)
        return missing

    def _resolve_path(self, artifact_path: str) -> str:
        """Resolve an artifact path relative to base dir."""
        if os.path.isabs(artifact_path):
            return artifact_path
        return os.path.join(self.artifact_base_dir, artifact_path)

    def _dispatch_supplementary_report(self, mission_state: dict[str, Any],
                                        missing_artifacts: list[str]) -> bool:
        """Dispatch a supplementary report task for missing artifacts.

        In production, this would enqueue a task. For now, we record the intent.
        Returns True if dispatch was attempted.
        """
        # Record the supplementary report request in mission state
        mission_state.setdefault("_supplementary_reports", []).append({
            "reason": "missing_artifacts",
            "missing": missing_artifacts,
            "dispatched_at": _now_iso(),
        })
        return True


def _now_iso() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()


def compute_lineage_coverage(mission_states: list[dict[str, Any]],
                              artifact_base_dir: str = "artifacts") -> dict[str, Any]:
    """Compute lineage coverage across a set of missions.

    Returns:
        {
            "total": int,
            "passed": int,
            "coverage": float,  # passed / total
            "missing_artifacts": list[str],
            "supplementary_dispatched": int,
        }
    """
    gate = LineageGate(artifact_base_dir=artifact_base_dir)
    total = len(mission_states)
    passed = 0
    all_missing = []
    supplementary_count = 0

    for state in mission_states:
        result = gate.check(state)
        if result.passed:
            passed += 1
        all_missing.extend(result.missing_artifacts)
        if result.supplementary_dispatched:
            supplementary_count += 1

    return {
        "total": total,
        "passed": passed,
        "coverage": passed / total if total > 0 else 0.0,
        "missing_artifacts": list(set(all_missing)),
        "supplementary_dispatched": supplementary_count,
    }
