"""Diff report generator for capability auto-discovery.

Produces human-readable reports of discovery results including:
- New capabilities added to ledger
- Updated capabilities (level changes, evidence updates)
- Missing capabilities (in ledger but not discovered)
- Retraction holds (retracted entries not auto-resurrected)
- Scan diagnostics

Reports are generated in Markdown format for human review.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from .auto_registrar import RegistrationResult
from .feature_extractor import CapabilityFeature
from .multi_scanner import ScanDiagnostics


def now_iso() -> str:
    """Current UTC time in ISO-8601 format."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


@dataclass
class DiffReport:
    """Complete diff report from a discovery run.

    Attributes
    ----------
    run_id : str
        Unique identifier for this run.
    generated_at : str
        ISO-8601 timestamp of report generation.
    summary : dict
        Summary statistics.
    added : list[dict]
        Newly added capabilities.
    updated : list[dict]
        Updated capabilities.
    unchanged : list[str]
        Unchanged capability IDs.
    missing : list[dict]
        Missing capabilities (in ledger, not discovered).
    held_retracted : list[dict]
        Retracted entries that were not auto-resurrected.
    diagnostics : dict
        Scan diagnostics.
    coverage : dict
        Coverage statistics.
    """
    run_id: str
    generated_at: str
    summary: Dict[str, Any] = field(default_factory=dict)
    added: List[Dict[str, Any]] = field(default_factory=list)
    updated: List[Dict[str, Any]] = field(default_factory=list)
    unchanged: List[str] = field(default_factory=list)
    missing: List[Dict[str, Any]] = field(default_factory=list)
    held_retracted: List[Dict[str, Any]] = field(default_factory=list)
    diagnostics: Dict[str, Any] = field(default_factory=dict)
    coverage: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "schema": "capability_discovery_multirepo.report.v1",
            "run_id": self.run_id,
            "generated_at": self.generated_at,
            "summary": dict(self.summary),
            "added": list(self.added),
            "updated": list(self.updated),
            "unchanged": list(self.unchanged),
            "missing": list(self.missing),
            "held_retracted": list(self.held_retracted),
            "diagnostics": dict(self.diagnostics),
            "coverage": dict(self.coverage),
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "DiffReport":
        return cls(
            run_id=d.get("run_id", ""),
            generated_at=d.get("generated_at", ""),
            summary=dict(d.get("summary") or {}),
            added=list(d.get("added") or []),
            updated=list(d.get("updated") or []),
            unchanged=list(d.get("unchanged") or []),
            missing=list(d.get("missing") or []),
            held_retracted=list(d.get("held_retracted") or []),
            diagnostics=dict(d.get("diagnostics") or {}),
            coverage=dict(d.get("coverage") or {}),
        )


class DiffReportGenerator:
    """Generates diff reports from discovery results.

    Parameters
    ----------
    run_id : str
        Unique identifier for this discovery run.
    """

    REPORT_SCHEMA = "capability_discovery_multirepo.report.v1"

    def __init__(self, run_id: Optional[str] = None):
        self.run_id = run_id or now_iso()

    def generate(
        self,
        results: List[RegistrationResult],
        features: List[CapabilityFeature],
        missing: List[Dict[str, Any]],
        diagnostics: ScanDiagnostics,
        coverage: Optional[Dict[str, Any]] = None,
        chain_ok: bool = True,
        chain_message: str = "",
    ) -> DiffReport:
        """Generate a complete diff report.

        Parameters
        ----------
        results : list[RegistrationResult]
            Registration results from AutoRegistrar.
        features : list[CapabilityFeature]
            All extracted features.
        missing : list[dict]
            Missing capabilities from ledger.
        diagnostics : ScanDiagnostics
            Scan diagnostics.
        coverage : dict | None
            Coverage statistics.
        chain_ok : bool
            Whether the ledger hash chain is valid.
        chain_message : str
            Chain verification message.

        Returns
        -------
        DiffReport
            Complete diff report.
        """
        at = now_iso()

        # Categorize results
        added: List[Dict[str, Any]] = []
        updated: List[Dict[str, Any]] = []
        unchanged: List[str] = []
        held_retracted: List[Dict[str, Any]] = []

        for result in results:
            if result.action == "added":
                added.append({
                    "capability_id": result.capability_id,
                    "level": result.level,
                    "revision": result.revision,
                    "reasons": result.reasons,
                    "tests_count": self._count_tests(result.capability_id, features),
                })
            elif result.action == "updated":
                updated.append({
                    "capability_id": result.capability_id,
                    "from_level": result.from_level,
                    "level": result.level,
                    "level_changed": result.level_changed,
                    "revision": result.revision,
                    "reasons": result.reasons,
                })
            elif result.action == "held_retracted":
                held_retracted.append({
                    "capability_id": result.capability_id,
                    "level": result.level,
                    "reasons": result.reasons,
                })
            else:  # unchanged
                unchanged.append(result.capability_id)

        # Summary statistics
        level_dist: Dict[str, int] = {}
        for f in features:
            lv = f.evidence_level or "WITHHELD"
            level_dist[lv] = level_dist.get(lv, 0) + 1

        summary = {
            "discovered": len(features),
            "added": len(added),
            "updated": len(updated),
            "unchanged": len(unchanged),
            "missing": len(missing),
            "held_retracted": len(held_retracted),
            "levels": dict(sorted(level_dist.items())),
            "chain_ok": chain_ok,
            "chain_message": chain_message,
        }

        return DiffReport(
            run_id=self.run_id,
            generated_at=at,
            summary=summary,
            added=added,
            updated=updated,
            unchanged=unchanged,
            missing=missing,
            held_retracted=held_retracted,
            diagnostics=diagnostics.to_dict(),
            coverage=coverage or {},
        )

    def _count_tests(self, cap_id: str, features: List[CapabilityFeature]) -> int:
        """Count tests for a capability."""
        for f in features:
            if f.capability_id == cap_id:
                return len(f.tests)
        return 0

    def render_markdown(self, report: DiffReport) -> str:
        """Render the diff report as Markdown.

        Parameters
        ----------
        report : DiffReport
            The report to render.

        Returns
        -------
        str
            Markdown-formatted report.
        """
        s = report.summary
        lines = [
            f"# Capability Auto-Discovery Report · `{report.run_id}`",
            "",
            f"**Generated:** `{report.generated_at}`",
            "",
            "## 1. Summary",
            "",
            "| Metric | Count |",
            "|---|---|",
            f"| Discovered capabilities | {s.get('discovered', 0)} |",
            f"| Added to ledger | {s.get('added', 0)} |",
            f"| Updated in ledger | {s.get('updated', 0)} |",
            f"| Unchanged | {s.get('unchanged', 0)} |",
            f"| Missing (in ledger, not discovered) | {s.get('missing', 0)} |",
            f"| Held retracted | {s.get('held_retracted', 0)} |",
            f"| Hash chain | {'OK' if s.get('chain_ok') else 'BROKEN'} — {s.get('chain_message', '')} |",
            "",
        ]

        # Level distribution
        levels = s.get("levels", {})
        if levels:
            lines.append(f"**Evidence level distribution:** `{levels}`")
            lines.append("")

        # Coverage
        cov = report.coverage
        if cov:
            lines.extend([
                "## 2. Coverage",
                "",
                f"- Known capabilities: {cov.get('known_count', 0)}",
                f"- Discovered: {cov.get('discovered_count', 0)}",
                f"- Coverage ratio: {cov.get('coverage_ratio', 0):.1%}",
                "",
            ])
            if cov.get("missing"):
                lines.append(f"**Missing:** {', '.join(f'`{m}`' for m in cov['missing'])}")
                lines.append("")

        # Diagnostics
        diag = report.diagnostics
        if diag:
            lines.extend([
                "## 3. Scan Diagnostics",
                "",
                f"- Repos scanned: {diag.get('repos_scanned', 0)}",
                f"- Files scanned: {diag.get('files_scanned', 0)}",
                f"- Errors: {len(diag.get('errors', []))}",
                f"- Warnings: {len(diag.get('warnings', []))}",
                "",
            ])
            if diag.get("errors"):
                lines.append("**Errors:**")
                for err in diag["errors"][:10]:
                    lines.append(f"- `{err.get('repo')}:{err.get('source_file')}:{err.get('lineno')}` — {err.get('message')}")
                lines.append("")

        # Added
        lines.extend(["## 4. Added Capabilities", ""])
        if report.added:
            lines.extend([
                "| Capability | Level | Tests | Reasons |",
                "|---|---|---|---|",
            ])
            for a in report.added:
                reasons = (a.get("reasons") or [""])[0][:60]
                lines.append(
                    f"| `{a['capability_id']}` | {a['level']} | "
                    f"{a.get('tests_count', 0)} | {reasons} |"
                )
        else:
            lines.append("(none)")
        lines.append("")

        # Updated
        lines.extend(["## 5. Updated Capabilities", ""])
        if report.updated:
            lines.extend([
                "| Capability | Change | Rev | Reasons |",
                "|---|---|---|---|",
            ])
            for u in report.updated:
                change = f"{u.get('from_level')} → {u['level']}" if u.get('level_changed') else f"{u['level']} (evidence)"
                reasons = (u.get("reasons") or [""])[0][:60]
                lines.append(
                    f"| `{u['capability_id']}` | {change} | "
                    f"{u.get('revision', 1)} | {reasons} |"
                )
        else:
            lines.append("(none)")
        lines.append("")

        # Missing
        lines.extend(["## 6. Missing Capabilities", ""])
        if report.missing:
            lines.extend([
                "| Capability | Ledger Level | Last Updated |",
                "|---|---|---|",
            ])
            for m in report.missing:
                lines.append(
                    f"| `{m['capability_id']}` | {m.get('level', '?')} | "
                    f"{m.get('last_updated', '?')} |"
                )
        else:
            lines.append("(none)")
        lines.append("")

        # Held retracted
        if report.held_retracted:
            lines.extend([
                "## 7. Held Retracted (not auto-resurrected)",
                "",
            ])
            for h in report.held_retracted:
                reasons = (h.get("reasons") or [""])[0]
                lines.append(f"- `{h['capability_id']}` — {reasons}")
            lines.append("")

        return "\n".join(lines)

    def save_report(
        self, report: DiffReport, output_dir: Path | str, format: str = "both"
    ) -> Dict[str, Path]:
        """Save the report to disk.

        Parameters
        ----------
        report : DiffReport
            The report to save.
        output_dir : Path | str
            Directory to save reports.
        format : str
            "json", "markdown", or "both".

        Returns
        -------
        dict
            Paths to saved files by format.
        """
        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        saved: Dict[str, Path] = {}

        if format in ("json", "both"):
            json_path = output_dir / f"report_{self.run_id}.json"
            with open(json_path, "w", encoding="utf-8") as f:
                json.dump(report.to_dict(), f, indent=2, ensure_ascii=False)
            saved["json"] = json_path

        if format in ("markdown", "both"):
            md_path = output_dir / f"report_{self.run_id}.md"
            md_content = self.render_markdown(report)
            with open(md_path, "w", encoding="utf-8") as f:
                f.write(md_content)
            saved["markdown"] = md_path

        return saved
