#!/usr/bin/env python3
"""CLI entry point for the Evidence Chain Integrity Auditor.

Usage:
    python -m tools.evidence_chain_auditor.cli [OPTIONS]

Options:
    --worktree PATH       Worktree root (default: current directory)
    --ledger PATH         Path to capability ledger JSON
    --mapping PATH        Path to capability-test mapping JSON
    --output-dir PATH     Output directory for reports
    --scan-id ID          Custom scan ID (default: auto-generated)
    --run-pytest          Actually run pytest (slower but more accurate)
    --json-only           Only output JSON report (no markdown/events)
    --quiet               Suppress progress output
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# Ensure worktree is on path
WORKTREE = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(WORKTREE))

from tools.evidence_chain_auditor.auditor import EvidenceChainAuditor
from tools.evidence_chain_auditor.report_generator import AuditReportGenerator


def main():
    parser = argparse.ArgumentParser(
        description="Evidence Chain Integrity Auditor — validates test→ledger consistency"
    )
    parser.add_argument(
        "--worktree",
        type=Path,
        default=Path("."),
        help="Worktree root directory",
    )
    parser.add_argument(
        "--ledger",
        type=Path,
        default=None,
        help="Path to capability ledger JSON (default: artifacts/capability_ledger/capability_ledger.json)",
    )
    parser.add_argument(
        "--mapping",
        type=Path,
        default=None,
        help="Path to capability-test mapping JSON",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=None,
        help="Output directory for reports (default: artifacts/evidence_chain_auditor)",
    )
    parser.add_argument(
        "--scan-id",
        type=str,
        default=None,
        help="Custom scan ID (default: auto-generated timestamp)",
    )
    parser.add_argument(
        "--run-pytest",
        action="store_true",
        help="Actually run pytest for each test module (slower but detects failures)",
    )
    parser.add_argument(
        "--json-only",
        action="store_true",
        help="Only output JSON report (skip markdown and events)",
    )
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="Suppress progress output",
    )

    args = parser.parse_args()

    # Create auditor
    auditor = EvidenceChainAuditor(
        worktree=args.worktree,
        ledger_path=args.ledger,
        mapping_path=args.mapping,
        run_pytest=args.run_pytest,
    )

    if not args.quiet:
        print(f"Evidence Chain Integrity Auditor")
        print(f"Worktree: {args.worktree.resolve()}")
        print(f"Running audit...")

    # Run audit
    report = auditor.run_audit(scan_id=args.scan_id)

    # Generate reports
    output_dir = args.output_dir or (args.worktree / "artifacts" / "evidence_chain_auditor")
    generator = AuditReportGenerator(output_dir=output_dir)

    if args.json_only:
        json_path = generator.write_json_report(report)
        if not args.quiet:
            print(f"JSON report: {json_path}")
    else:
        paths = generator.generate_all(report)
        if not args.quiet:
            print(f"\nGenerated reports:")
            for name, path in paths.items():
                print(f"  {name}: {path}")

    # Print summary
    if not args.quiet:
        print(f"\n{'='*60}")
        print(f"Audit Summary")
        print(f"{'='*60}")
        print(f"Scan ID: {report.scan_id}")
        print(f"Total capabilities: {report.total_capabilities}")
        print(f"  Consistent: {report.consistent_count}")
        print(f"  Inconsistent: {report.inconsistent_count}")
        print(f"  Unknown: {report.unknown_count}")
        print(f"Total findings: {len(report.findings)}")

        critical = sum(1 for f in report.findings if f.severity == "critical")
        warning = sum(1 for f in report.findings if f.severity == "warning")
        info = sum(1 for f in report.findings if f.severity == "info")
        print(f"  Critical: {critical}, Warning: {warning}, Info: {info}")

        if report.findings:
            print(f"\nFindings:")
            for f in report.findings:
                icon = {"critical": "❌", "warning": "⚠️", "info": "ℹ️"}.get(f.severity, "?")
                print(f"  {icon} [{f.severity}] {f.capability_id}: {f.finding_type.value}")
                print(f"     {f.description}")

    # Exit code: 0 if no critical findings, 1 if critical findings exist
    has_critical = any(f.severity == "critical" for f in report.findings)
    return 1 if has_critical else 0


if __name__ == "__main__":
    sys.exit(main())
