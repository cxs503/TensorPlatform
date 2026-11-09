"""Paper-quality six-element report auto-generation.

Generates verification reports following the paper-quality standard:
1. 案例介绍 (Case Introduction): Physical background, geometry, parameter table
2. 怎么做的 (Methodology): Methods, workflow, key settings, toolchain
3. 计算结果 (Results): Numerical tables, zero-hand-copy (SQL-sourced)
4. 结果图 (Figures): Field plots, comparison curves (matplotlib PNG)
5. 注意点 (Notes): Pitfalls, limitations, applicable scope
6. 误差判定 (Error Judgment): Key quantities vs. baseline,逐项列出

All numbers are SQL-sourced from the provenance database.
"""
from __future__ import annotations

import base64
import io
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from .provenance_db import ProvenanceDB
from .evidence_classifier import EvidenceClassifier, ClassificationResult

WORKTREE = Path(__file__).resolve().parents[2]
REPORTS_DIR = WORKTREE / "artifacts" / "capability_reports"


class ReportGenerator:
    """Auto-generates paper-quality verification reports.

    Parameters
    ----------
    provenance_db : ProvenanceDB, optional
        Provenance database for SQL-sourced accuracy numbers.
    classifier : EvidenceClassifier, optional
        Evidence classifier for classification results.
    """

    def __init__(
        self,
        provenance_db: Optional[ProvenanceDB] = None,
        classifier: Optional[EvidenceClassifier] = None,
    ):
        self.provenance_db = provenance_db or ProvenanceDB()
        self.classifier = classifier or EvidenceClassifier()
        self.reports_dir = WORKTREE / "artifacts" / "capability_reports"
        self.reports_dir.mkdir(parents=True, exist_ok=True)

    def generate_full_audit_report(
        self,
        run_id: str,
        classification_results: Optional[Dict[str, ClassificationResult]] = None,
    ) -> Dict[str, Any]:
        """Generate a full audit report for all 21 capabilities.

        Parameters
        ----------
        run_id : str
            Verification run identifier.
        classification_results : dict, optional
            Pre-computed classification results.

        Returns
        -------
        dict
            Report payload ready for submission.
        """
        if classification_results is None:
            classification_results = self.classifier.classify_all()

        now = datetime.now(timezone.utc).isoformat()

        # Build markdown report
        markdown = self._build_full_report_markdown(run_id, classification_results, now)

        # Build report payload
        report = {
            "title": f"Capability Evidence Audit Report — Run {run_id}",
            "summary": f"Automated audit of 21 platform capabilities: "
                       f"evidence levels, accuracy verification, provenance chain.",
            "scenario_key": "IN63-N09",
            "mission_id": 7213,
            "tags": ["capability-audit", "evidence-level", "provenance", "auto-audit"],
            "markdown": markdown,
            "generated_at": now,
            "run_id": run_id,
        }

        # Save report
        report_path = self.reports_dir / f"audit_report_{run_id}.json"
        with open(report_path, "w", encoding="utf-8") as f:
            json.dump(report, f, indent=2, ensure_ascii=False)

        # Also save markdown separately
        md_path = self.reports_dir / f"audit_report_{run_id}.md"
        with open(md_path, "w", encoding="utf-8") as f:
            f.write(markdown)

        report["report_path"] = str(report_path)
        report["markdown_path"] = str(md_path)

        return report

    def generate_capability_report(
        self,
        capability_id: str,
        result: ClassificationResult,
        run_id: str,
    ) -> Dict[str, Any]:
        """Generate a detailed report for a single capability.

        Follows the six-element paper-quality format.
        """
        # Get accuracy data from SQL
        accuracy_data = self.provenance_db.get_all_accuracy_for_capability(capability_id)

        markdown = self._build_capability_markdown(capability_id, result, accuracy_data, run_id)

        report = {
            "title": f"Capability Report: {capability_id}",
            "summary": f"Evidence level: {result.level}, Confidence: {result.confidence:.2f}",
            "capability_id": capability_id,
            "run_id": run_id,
            "markdown": markdown,
            "generated_at": datetime.now(timezone.utc).isoformat(),
        }

        # Save
        report_path = self.reports_dir / f"report_{capability_id.replace('.', '_')}_{run_id}.json"
        with open(report_path, "w", encoding="utf-8") as f:
            json.dump(report, f, indent=2, ensure_ascii=False)

        report["report_path"] = str(report_path)
        return report

    def _build_full_report_markdown(
        self,
        run_id: str,
        results: Dict[str, ClassificationResult],
        timestamp: str,
    ) -> str:
        """Build the full audit report in markdown format."""
        lines = []

        # Header
        lines.append(f"# Capability Evidence Audit Report")
        lines.append(f"")
        lines.append(f"**Run ID**: {run_id}")
        lines.append(f"**Generated**: {timestamp}")
        lines.append(f"**Total Capabilities**: {len(results)}")
        lines.append(f"")

        # Summary table
        lines.append("## Summary")
        lines.append("")
        lines.append("| Capability | Domain | Evidence Level | Confidence | Rule |")
        lines.append("|---|---|---|---|---|")

        level_counts = {}
        for cap_id, result in sorted(results.items()):
            domain = self.classifier.profiles.get(cap_id, {}).get("domain", "?")
            lines.append(
                f"| {cap_id} | {domain} | **{result.level}** | "
                f"{result.confidence:.2f} | {result.rule_fired} |"
            )
            level_counts[result.level] = level_counts.get(result.level, 0) + 1

        lines.append("")

        # Level distribution
        lines.append("### Evidence Level Distribution")
        lines.append("")
        for level in ["REAL", "WITHHELD", "MOCK", "STUB", "NOT_SUPPORTED"]:
            count = level_counts.get(level, 0)
            lines.append(f"- **{level}**: {count}")
        lines.append("")

        # Detailed results by level
        for level in ["REAL", "WITHHELD", "MOCK", "STUB", "NOT_SUPPORTED"]:
            caps_at_level = [
                (cid, r) for cid, r in results.items() if r.level == level
            ]
            if not caps_at_level:
                continue

            lines.append(f"## {level} Capabilities")
            lines.append("")

            for cap_id, result in caps_at_level:
                lines.append(f"### {cap_id}")
                lines.append("")

                # ① Case Introduction
                domain = self.classifier.profiles.get(cap_id, {}).get("domain", "unknown")
                lines.append(f"**Domain**: {domain}")
                lines.append(f"**Confidence**: {result.confidence:.2f}")
                lines.append(f"**Rule Fired**: {result.rule_fired}")
                lines.append("")

                # ② Methodology
                lines.append("#### Methodology")
                lines.append(f"- Evidence files scanned: {len(result.evidence_files)}")
                lines.append(f"- Verification artifacts: {len(result.verification_artifacts)}")
                lines.append(f"- Mock indicators: {len(result.mock_indicators)}")
                lines.append(f"- Real indicators: {len(result.real_indicators)}")
                lines.append("")

                # ③ Results (SQL-sourced)
                accuracy_data = self.provenance_db.get_all_accuracy_for_capability(cap_id)
                if accuracy_data["record_count"] > 0:
                    lines.append("#### Accuracy (SQL-sourced)")
                    lines.append(f"- Records: {accuracy_data['record_count']}")
                    lines.append(f"- Max error: {accuracy_data['max_error_pct']}%")
                    lines.append(f"- All passed: {accuracy_data['all_passed']}")
                    lines.append(f"- SQL query: `{accuracy_data['sql_query'][:80]}...`")
                else:
                    lines.append("#### Accuracy")
                    lines.append("- No accuracy records in provenance DB")
                lines.append("")

                # ④ Notes
                lines.append("#### Notes")
                if result.evidence_files:
                    lines.append(f"- Key files: {', '.join(result.evidence_files[:3])}")
                if result.mock_indicators:
                    lines.append(f"- ⚠️ Mock indicators in: {', '.join(result.mock_indicators[:3])}")
                lines.append("")

                # ⑥ Error Judgment
                lines.append("#### Error Judgment")
                if accuracy_data["record_count"] > 0:
                    max_err = accuracy_data["max_error_pct"]
                    if max_err is not None:
                        passed = max_err <= 3.0
                        lines.append(f"- Max error vs. 3% threshold: {max_err:.2f}%")
                        lines.append(f"- **是否<3%**: {'是' if passed else '否'}")
                    else:
                        lines.append("- No error data available")
                else:
                    lines.append("- No accuracy data in provenance DB (cannot judge)")
                lines.append("")

        # Provenance chain verification
        lines.append("## Provenance Chain Verification")
        lines.append("")
        for cap_id, result in sorted(results.items()):
            verification = self.provenance_db.verify_no_hardcoded_accuracy(cap_id)
            status = "✅" if verification["verified"] else "⚠️"
            lines.append(f"- {status} {cap_id}: {verification['details']}")
        lines.append("")

        # Footer
        lines.append("---")
        lines.append(f"*Report generated by capability_auto_audit at {timestamp}*")
        lines.append(f"*All accuracy numbers are SQL-sourced from provenance DB (zero-hardcode)*")

        return "\n".join(lines)

    def _build_capability_markdown(
        self,
        capability_id: str,
        result: ClassificationResult,
        accuracy_data: Dict[str, Any],
        run_id: str,
    ) -> str:
        """Build a detailed capability report in markdown (six elements)."""
        domain = self.classifier.profiles.get(capability_id, {}).get("domain", "unknown")
        now = datetime.now(timezone.utc).isoformat()

        lines = []
        lines.append(f"# Capability Report: {capability_id}")
        lines.append(f"")
        lines.append(f"**Run ID**: {run_id}")
        lines.append(f"**Generated**: {now}")
        lines.append(f"")

        # ① 案例介绍
        lines.append("## ① 案例介绍 (Case Introduction)")
        lines.append(f"")
        lines.append(f"- **Capability**: {capability_id}")
        lines.append(f"- **Domain**: {domain}")
        lines.append(f"- **Evidence Level**: **{result.level}**")
        lines.append(f"- **Confidence**: {result.confidence:.2f}")
        lines.append(f"")

        # ② 怎么做的
        lines.append("## ② 方法 (Methodology)")
        lines.append(f"")
        lines.append(f"- Scanned {len(result.evidence_files)} evidence files")
        lines.append(f"- Applied multi-signal classification (code/test/verify/mock/stub)")
        lines.append(f"- Classification rule fired: `{result.rule_fired}`")
        lines.append(f"- Signal scores: {json.dumps(result.signals, indent=2)}")
        lines.append(f"")

        # ③ 计算结果
        lines.append("## ③ 计算结果 (Results)")
        lines.append(f"")
        if accuracy_data["record_count"] > 0:
            lines.append(f"| Metric | Value | Source |")
            lines.append(f"|---|---|---|")
            for m in accuracy_data["metrics"][:10]:
                lines.append(
                    f"| {m.get('metric_name', '?')} | {m.get('value_pct', '?')}% | "
                    f"SQL (run={m.get('run_id', '?')}) |"
                )
            lines.append(f"")
            lines.append(f"- **Max error**: {accuracy_data['max_error_pct']}%")
            lines.append(f"- **All passed**: {accuracy_data['all_passed']}")
            lines.append(f"- **SQL query used**: `{accuracy_data['sql_query']}`")
        else:
            lines.append("No accuracy records in provenance DB.")
        lines.append(f"")

        # ④ 结果图
        lines.append("## ④ 结果图 (Figures)")
        lines.append(f"")
        lines.append(f"*This is an audit report; figures are generated by the verification")
        lines.append(f"pipeline. See verification artifacts for domain-specific plots.*")
        lines.append(f"")

        # ⑤ 注意点
        lines.append("## ⑤ 注意点 (Notes)")
        lines.append(f"")
        if result.mock_indicators:
            lines.append(f"- ⚠️ Mock indicators detected in: {', '.join(result.mock_indicators[:5])}")
        if result.stub_indicators:
            lines.append(f"- ⚠️ Stub indicators detected in: {', '.join(result.stub_indicators[:5])}")
        if not result.verification_artifacts:
            lines.append(f"- ⚠️ No verification artifacts found")
        if result.evidence_files:
            lines.append(f"- Evidence files: {', '.join(result.evidence_files[:5])}")
        lines.append(f"")

        # ⑥ 误差判定
        lines.append("## ⑥ 误差判定 (Error Judgment)")
        lines.append(f"")
        if accuracy_data["record_count"] > 0 and accuracy_data["max_error_pct"] is not None:
            max_err = accuracy_data["max_error_pct"]
            passed = max_err <= 3.0
            lines.append(f"| Quantity | Error (%) | Threshold (%) | 是否<3% |")
            lines.append(f"|---|---|---|---|")
            lines.append(f"| Max error | {max_err:.2f} | 3.0 | {'是' if passed else '否'} |")
            lines.append(f"")
            if passed:
                lines.append(f"**结论**: 误差 < 3%，满足精度要求。")
            else:
                lines.append(f"**结论**: 误差 > 3%，不满足精度要求。")
        else:
            lines.append("无精度数据可供判定（provenance DB 中无该能力的精度记录）。")
            lines.append("")
            lines.append("**原因**: 该能力尚未经过验证面运行，或验证面未产出精度数据。")
        lines.append(f"")

        lines.append("---")
        lines.append(f"*Report generated by capability_auto_audit*")
        lines.append(f"*All accuracy numbers SQL-sourced from provenance DB (zero-hardcode policy)*")

        return "\n".join(lines)

    def generate_report_payload_for_api(
        self,
        run_id: str,
        classification_results: Optional[Dict[str, ClassificationResult]] = None,
    ) -> Dict[str, Any]:
        """Generate a report payload formatted for the TENSOR_API reports endpoint.

        Returns a dict suitable for POST to /tensor/reports.
        """
        report = self.generate_full_audit_report(run_id, classification_results)

        payload = {
            "title": report["title"],
            "summary": report["summary"],
            "scenario_key": report["scenario_key"],
            "mission_id": report["mission_id"],
            "tags": report["tags"],
            "markdown": report["markdown"],
            "figures": [],  # No figures for audit report (text-only)
        }
        return payload
