"""
Report generation for failure minimization results.

Produces audit-ready reports with:
- repro_min/manifest.json: minimal reproduction manifest
- repro_min/reduce.log: reduction log
- repro_min/signature.txt: failure signature
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .corpus import MissionSnapshot
from .ddmin import ReductionResult
from .signature import FailureSignature


@dataclass
class MinimizationReport:
    """Complete report for a single minimization run."""
    mission_id: int
    timestamp: str
    original_element_count: int
    minimal_element_count: int
    reduction_rate: float
    iterations: int
    duration_seconds: float
    signature_match: bool
    is_1minimal: bool
    original_elements: list[str]
    minimal_elements: list[str]
    original_signature: dict[str, Any]
    final_signature: dict[str, Any]
    reduction_log: list[dict[str, Any]]

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        return {
            "mission_id": self.mission_id,
            "timestamp": self.timestamp,
            "original_element_count": self.original_element_count,
            "minimal_element_count": self.minimal_element_count,
            "reduction_rate": self.reduction_rate,
            "iterations": self.iterations,
            "duration_seconds": self.duration_seconds,
            "signature_match": self.signature_match,
            "is_1minimal": self.is_1minimal,
            "original_elements": self.original_elements,
            "minimal_elements": self.minimal_elements,
            "original_signature": self.original_signature,
            "final_signature": self.final_signature,
            "reduction_log": self.reduction_log,
        }


class ReportGenerator:
    """Generate audit-ready reports for minimization results."""

    def __init__(self, output_dir: str | Path = "repro_min"):
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)

    def generate(
        self,
        snapshot: MissionSnapshot,
        result: ReductionResult,
        is_1minimal: bool,
    ) -> Path:
        """
        Generate complete report for a minimization run.

        Creates:
            output_dir/{mission_id}/manifest.json
            output_dir/{mission_id}/reduce.log
            output_dir/{mission_id}/signature.txt

        Returns:
            Path to the report directory
        """
        mission_dir = self.output_dir / str(snapshot.mission_id)
        mission_dir.mkdir(parents=True, exist_ok=True)

        # Check signature match
        sig_match = False
        if result.original_signature and result.final_signature:
            sig_match = result.original_signature.matches(result.final_signature)

        # Create report
        report = MinimizationReport(
            mission_id=snapshot.mission_id,
            timestamp=datetime.now(timezone.utc).isoformat(),
            original_element_count=len(result.original_elements),
            minimal_element_count=len(result.minimal_elements),
            reduction_rate=result.reduction_rate,
            iterations=result.iterations,
            duration_seconds=result.duration_seconds,
            signature_match=sig_match,
            is_1minimal=is_1minimal,
            original_elements=result.original_elements,
            minimal_elements=result.minimal_elements,
            original_signature=result.original_signature.to_dict() if result.original_signature else {},
            final_signature=result.final_signature.to_dict() if result.final_signature else {},
            reduction_log=result.reduction_log,
        )

        # Write manifest
        manifest_path = mission_dir / "manifest.json"
        with open(manifest_path, "w", encoding="utf-8") as f:
            json.dump(report.to_dict(), f, indent=2, ensure_ascii=False)

        # Write reduction log
        log_path = mission_dir / "reduce.log"
        with open(log_path, "w", encoding="utf-8") as f:
            f.write(f"# Reduction Log for Mission {snapshot.mission_id}\n")
            f.write(f"# Generated: {report.timestamp}\n")
            f.write(f"# Original elements: {report.original_element_count}\n")
            f.write(f"# Minimal elements: {report.minimal_element_count}\n")
            f.write(f"# Reduction rate: {report.reduction_rate:.2%}\n")
            f.write(f"# Iterations: {report.iterations}\n")
            f.write(f"# Duration: {report.duration_seconds:.2f}s\n")
            f.write(f"# Signature match: {report.signature_match}\n")
            f.write(f"# 1-minimal: {report.is_1minimal}\n")
            f.write("\n")

            f.write("## Original Elements\n")
            for elem in report.original_elements:
                f.write(f"  - {elem}\n")
            f.write("\n")

            f.write("## Minimal Elements\n")
            for elem in report.minimal_elements:
                f.write(f"  - {elem}\n")
            f.write("\n")

            f.write("## Reduction Steps\n")
            for i, step in enumerate(report.reduction_log, 1):
                f.write(f"Step {i}: {step}\n")

        # Write signature
        sig_path = mission_dir / "signature.txt"
        with open(sig_path, "w", encoding="utf-8") as f:
            if result.final_signature:
                f.write(result.final_signature.to_json())
            else:
                f.write("{}")

        # Write reproduction script
        script_path = mission_dir / "repro.sh"
        self._write_repro_script(snapshot, result.minimal_elements, script_path)

        return mission_dir

    def _write_repro_script(
        self,
        snapshot: MissionSnapshot,
        minimal_elements: list[str],
        script_path: Path,
    ) -> None:
        """Write executable reproduction script."""
        lines = ["#!/bin/bash", "# Auto-generated reproduction script", ""]
        lines.append(f"# Mission ID: {snapshot.mission_id}")
        lines.append(f"# Goal: {snapshot.goal}")
        lines.append(f"# Minimal elements: {len(minimal_elements)}")
        lines.append("")

        # Environment
        lines.append("# Environment")
        for key, value in snapshot.env_whitelist.items():
            env_elem = f"env:{key}"
            if env_elem in minimal_elements or not any(e.startswith("env:") for e in minimal_elements):
                lines.append(f"export {key}={value}")
        lines.append("")

        # Files
        lines.append("# Files")
        for elem in minimal_elements:
            if elem.startswith("file:"):
                filepath = elem[5:]
                lines.append(f"# Required: {filepath}")
        lines.append("")

        # Checks
        lines.append("# Checks")
        for elem in minimal_elements:
            if elem.startswith("check:"):
                check_cmd = elem[6:]
                lines.append(check_cmd)
        lines.append("")

        # Commands
        lines.append("# Commands")
        for elem in minimal_elements:
            if elem.startswith("cmd:"):
                cmd = elem[4:]
                lines.append(cmd)
        for cmd in snapshot.commands:
            if cmd in minimal_elements or not any(e.startswith("cmd:") for e in minimal_elements):
                lines.append(cmd)

        script_path.write_text("\n".join(lines))
        script_path.chmod(0o755)


class CorpusAuditReport:
    """Generate aggregate audit report for entire corpus."""

    def __init__(self, reports_dir: str | Path = "repro_min"):
        self.reports_dir = Path(reports_dir)

    def generate_summary(self) -> dict[str, Any]:
        """
        Generate summary statistics for all minimization reports.

        Returns:
            Dictionary with aggregate statistics
        """
        if not self.reports_dir.exists():
            return {"error": "Reports directory not found"}

        reports = []
        for mission_dir in self.reports_dir.iterdir():
            if not mission_dir.is_dir():
                continue
            manifest_path = mission_dir / "manifest.json"
            if not manifest_path.exists():
                continue

            with open(manifest_path, "r", encoding="utf-8") as f:
                report = json.load(f)
                reports.append(report)

        if not reports:
            return {"error": "No reports found"}

        # Compute statistics
        total = len(reports)
        reproduced = sum(1 for r in reports if r.get("signature_match", False))
        minimal_verified = sum(1 for r in reports if r.get("is_1minimal", False))

        reduction_rates = [r.get("reduction_rate", 0.0) for r in reports]
        avg_reduction = sum(reduction_rates) / len(reduction_rates) if reduction_rates else 0.0
        median_reduction = sorted(reduction_rates)[len(reduction_rates) // 2] if reduction_rates else 0.0
        max_reduction = max(reduction_rates) if reduction_rates else 0.0

        return {
            "total_missions": total,
            "reproduced": reproduced,
            "reproduction_rate": reproduced / total if total > 0 else 0.0,
            "minimal_verified": minimal_verified,
            "minimality_rate": minimal_verified / total if total > 0 else 0.0,
            "avg_reduction_rate": avg_reduction,
            "median_reduction_rate": median_reduction,
            "max_reduction_rate": max_reduction,
            "reports": reports,
        }
