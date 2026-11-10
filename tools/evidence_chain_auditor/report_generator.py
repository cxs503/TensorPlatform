"""Audit report generator — produces JSON + human-readable summary.

Outputs:
  - JSON report (machine-readable, full detail)
  - Human-readable summary (markdown format)
  - Audit events (for audit_events table ingestion)
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from .auditor import AuditFinding, AuditReport


class AuditReportGenerator:
    """Generates audit reports in multiple formats."""

    def __init__(self, output_dir: Optional[Path] = None):
        self.output_dir = output_dir or (
            Path(__file__).resolve().parents[2] / "artifacts" / "evidence_chain_auditor"
        )
        self.output_dir.mkdir(parents=True, exist_ok=True)

    def write_json_report(self, report: AuditReport, filename: Optional[str] = None) -> Path:
        """Write the full JSON report."""
        if filename is None:
            filename = f"audit_report_{report.scan_id}.json"
        output_path = self.output_dir / filename
        with open(output_path, "w", encoding="utf-8") as f:
            f.write(report.to_json(indent=2))
        return output_path

    def write_markdown_summary(self, report: AuditReport, filename: Optional[str] = None) -> Path:
        """Write a human-readable markdown summary."""
        if filename is None:
            filename = f"audit_summary_{report.scan_id}.md"
        output_path = self.output_dir / filename

        lines = [
            f"# Evidence Chain Integrity Audit Report",
            f"",
            f"**Scan ID:** {report.scan_id}  ",
            f"**Scanned at:** {report.scanned_at}  ",
            f"**Mapping status:** {report.mapping_status}  ",
            f"",
            f"## Summary",
            f"",
            f"{report.summary}",
            f"",
            f"| Metric | Count |",
            f"|--------|-------|",
            f"| Total capabilities | {report.total_capabilities} |",
            f"| Consistent | {report.consistent_count} |",
            f"| Inconsistent | {report.inconsistent_count} |",
            f"| Unknown | {report.unknown_count} |",
            f"",
        ]

        # Findings table
        if report.findings:
            lines.extend([
                f"## Findings ({len(report.findings)})",
                f"",
                f"| Capability | Finding Type | Severity | Description |",
                f"|------------|--------------|----------|-------------|",
            ])
            for f in report.findings:
                lines.append(
                    f"| `{f.capability_id}` | {f.finding_type.value} | "
                    f"{f.severity} | {f.description} |"
                )
            lines.append("")

        # Detailed per-capability results
        lines.extend([
            f"## Per-Capability Details",
            f"",
        ])
        for cr in report.capability_results:
            status_icon = {
                "consistent": "✅",
                "inconsistent": "❌",
                "unknown": "⚠️",
            }.get(cr.status, "?")

            lines.extend([
                f"### {status_icon} `{cr.capability_id}`",
                f"",
                f"- **Ledger claimed:** {cr.ledger_claimed_level}",
                f"- **Test inferred:** {cr.test_inferred_level}",
                f"- **Status:** {cr.status}",
            ])

            if cr.test_results:
                lines.append(f"- **Test modules:**")
                for tr in cr.test_results:
                    exists_str = "✓" if tr.exists else "✗"
                    lines.append(
                        f"  - [{exists_str}] `{tr.module_path}` "
                        f"({tr.total_cases} cases, {tr.passed} passed, "
                        f"{tr.failed} failed)"
                    )

            if cr.findings:
                lines.append(f"- **Findings:**")
                for f in cr.findings:
                    lines.append(f"  - **{f.finding_type.value}** ({f.severity}): {f.description}")
                    lines.append(f"    - Fix: {f.suggested_fix}")

            lines.append("")

        with open(output_path, "w", encoding="utf-8") as f:
            f.write("\n".join(lines))
        return output_path

    def generate_audit_events(self, report: AuditReport) -> List[Dict[str, Any]]:
        """Generate audit event records for database ingestion.

        Each finding becomes an audit event with:
          - event_type: "evidence_chain_break"
          - capability_id
          - finding_type
          - severity
          - evidence_path
          - suggested_fix
          - detected_at
        """
        events = []
        for finding in report.findings:
            events.append({
                "event_type": "evidence_chain_break",
                "scan_id": report.scan_id,
                "capability_id": finding.capability_id,
                "finding_type": finding.finding_type.value,
                "severity": finding.severity,
                "description": finding.description,
                "evidence_path": finding.evidence_path,
                "suggested_fix": finding.suggested_fix,
                "details": finding.details,
                "detected_at": report.scanned_at,
            })
        return events

    def write_audit_events(self, report: AuditReport, filename: Optional[str] = None) -> Path:
        """Write audit events as JSONL (one event per line)."""
        if filename is None:
            filename = f"audit_events_{report.scan_id}.jsonl"
        output_path = self.output_dir / filename
        events = self.generate_audit_events(report)
        with open(output_path, "w", encoding="utf-8") as f:
            for event in events:
                f.write(json.dumps(event, ensure_ascii=False) + "\n")
        return output_path

    def generate_all(self, report: AuditReport) -> Dict[str, Path]:
        """Generate all report formats and return paths."""
        return {
            "json_report": self.write_json_report(report),
            "markdown_summary": self.write_markdown_summary(report),
            "audit_events": self.write_audit_events(report),
        }
