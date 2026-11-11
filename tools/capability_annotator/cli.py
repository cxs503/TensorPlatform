"""CLI for capability annotator: scan, gap-ledger, audit, figures.

Commands:
    scan      - Run capability scan
    gap       - Generate gap ledger
    audit     - Run full audit pipeline
    figures   - Generate visualization figures
    summary   - Print human-readable summary

Exit codes:
    0 - Success
    1 - Acceptance criteria failed
    2 - Configuration error
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tools.capability_annotator import (
    AuditPipeline,
    CapabilityRegistry,
    CapabilityScanner,
    GapLedger,
)
from tools.capability_annotator.figures import generate_all_figures


def cmd_scan(args) -> int:
    """Run capability scan."""
    registry = CapabilityRegistry(args.config, args.spec)
    scanner = CapabilityScanner(args.root, registry, args.spec)
    report = scanner.scan_all()

    # Save report
    if args.output:
        scanner.save_report(report, args.output)
        print(f"Scan report saved to: {args.output}")

    # Print summary
    print(f"Scanned {report.capability_count} capabilities in {report.total_scan_time_sec}s")
    print(f"Level tally: {report.summary['level_tally']}")

    return 0


def cmd_gap(args) -> int:
    """Generate gap ledger."""
    # Load scan report
    if not args.scan_report or not Path(args.scan_report).exists():
        print("Error: --scan-report required and must exist", file=sys.stderr)
        return 2

    with open(args.scan_report) as f:
        scan_data = json.load(f)

    # Reconstruct ScanReport
    from tools.capability_annotator.scanner import ScanReport, ScanResult
    results = [ScanResult(**r) for r in scan_data.get("results", [])]
    scan_report = ScanReport(
        capability_count=scan_data.get("capability_count", 0),
        results=results,
        summary=scan_data.get("summary", {}),
        total_scan_time_sec=scan_data.get("total_scan_time_sec", 0),
    )

    # Generate gap ledger
    gap_ledger = GapLedger()
    gap_report = gap_ledger.analyze(scan_report)

    # Save
    if args.output:
        gap_ledger.save(gap_report, args.output)
        print(f"Gap ledger saved to: {args.output}")

    print(f"Found {gap_report.gaps_found} gaps out of {gap_report.total_capabilities} capabilities")
    print(f"Gap rate: {gap_report.summary.get('gap_rate', 0):.1%}")

    return 0


def cmd_audit(args) -> int:
    """Run full audit pipeline."""
    pipeline = AuditPipeline(
        args.root,
        config_path=args.config,
        spec_path=args.spec,
        baseline_path=args.baseline,
    )

    result = pipeline.run(check_acceptance=not args.no_acceptance)

    # Save outputs
    if args.output_dir:
        out_dir = Path(args.output_dir)
        out_dir.mkdir(parents=True, exist_ok=True)

        # Save scan report
        pipeline.scanner.save_report(result.scan_report, out_dir / "scan_report.json")

        # Save gap ledger
        pipeline.gap_ledger.save(result.gap_report, out_dir / "gap_ledger.json")

        # Save full audit result
        with open(out_dir / "audit_result.json", "w") as f:
            json.dump(result.to_dict(), f, ensure_ascii=False, indent=2)

        print(f"Audit results saved to: {out_dir}")

    # Print summary
    print(pipeline.generate_summary(result))

    # Return exit code based on acceptance
    if result.acceptance and not result.acceptance.get("all_pass", True):
        return 1

    return 0


def cmd_figures(args) -> int:
    """Generate visualization figures."""
    # Load scan report
    if not args.scan_report or not Path(args.scan_report).exists():
        print("Error: --scan-report required and must exist", file=sys.stderr)
        return 2

    with open(args.scan_report) as f:
        scan_data = json.load(f)

    # Load gap report if provided
    gap_report = None
    if args.gap_report and Path(args.gap_report).exists():
        with open(args.gap_report) as f:
            gap_data = json.load(f)
        from tools.capability_annotator.gap_ledger import GapLedgerReport, Gap
        gaps = [Gap(**g) for g in gap_data.get("gaps", [])]
        gap_report = GapLedgerReport(
            gaps=gaps,
            total_capabilities=gap_data.get("total_capabilities", 0),
            gaps_found=gap_data.get("gaps_found", 0),
            summary=gap_data.get("summary", {}),
        )

    # Reconstruct ScanReport
    from tools.capability_annotator.scanner import ScanReport, ScanResult
    results = [ScanResult(**r) for r in scan_data.get("results", [])]
    scan_report = ScanReport(
        capability_count=scan_data.get("capability_count", 0),
        results=results,
        summary=scan_data.get("summary", {}),
        total_scan_time_sec=scan_data.get("total_scan_time_sec", 0),
    )

    if not gap_report:
        gap_ledger = GapLedger()
        gap_report = gap_ledger.analyze(scan_report)

    # Generate figures
    output_dir = Path(args.output_dir) if args.output_dir else Path("figures")
    figures = generate_all_figures(scan_report, gap_report, output_dir)

    print(f"Generated {len(figures)} figures in {output_dir}:")
    for name, path in figures.items():
        print(f"  {name}: {path}")

    return 0


def cmd_summary(args) -> int:
    """Print human-readable summary."""
    pipeline = AuditPipeline(
        args.root,
        config_path=args.config,
        spec_path=args.spec,
    )
    result = pipeline.run(check_acceptance=False)
    print(pipeline.generate_summary(result))
    return 0


def main():
    parser = argparse.ArgumentParser(
        description="Platform Capability Annotator - 21-capability evidence level auto-scan"
    )
    parser.add_argument("--root", default=".", help="Worktree root directory")
    parser.add_argument("--config", help="Path to capabilities.json")
    parser.add_argument("--spec", help="Path to capability_audit_gate/spec.json")

    subparsers = parser.add_subparsers(dest="command", help="Command to run")

    # scan
    scan_parser = subparsers.add_parser("scan", help="Run capability scan")
    scan_parser.add_argument("--output", "-o", help="Output JSON path")

    # gap
    gap_parser = subparsers.add_parser("gap", help="Generate gap ledger")
    gap_parser.add_argument("--scan-report", required=True, help="Path to scan report JSON")
    gap_parser.add_argument("--output", "-o", help="Output JSON path")

    # audit
    audit_parser = subparsers.add_parser("audit", help="Run full audit pipeline")
    audit_parser.add_argument("--baseline", help="Path to baseline gap ledger for diff")
    audit_parser.add_argument("--output-dir", "-o", help="Output directory")
    audit_parser.add_argument("--no-acceptance", action="store_true",
                             help="Skip acceptance criteria check")

    # figures
    fig_parser = subparsers.add_parser("figures", help="Generate visualization figures")
    fig_parser.add_argument("--scan-report", required=True, help="Path to scan report JSON")
    fig_parser.add_argument("--gap-report", help="Path to gap ledger JSON")
    fig_parser.add_argument("--output-dir", "-o", default="figures", help="Output directory")

    # summary
    subparsers.add_parser("summary", help="Print human-readable summary")

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        return 2

    commands = {
        "scan": cmd_scan,
        "gap": cmd_gap,
        "audit": cmd_audit,
        "figures": cmd_figures,
        "summary": cmd_summary,
    }

    return commands[args.command](args)


if __name__ == "__main__":
    sys.exit(main())
