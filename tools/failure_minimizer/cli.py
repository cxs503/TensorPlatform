"""
Command-line interface for failure minimizer.

Usage:
    python -m tools.failure_minimizer.cli export --mission-id 123
    python -m tools.failure_minimizer.cli minimize --mission-id 123
    python -m tools.failure_minimizer.cli audit
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from .corpus import CorpusExporter, MissionSnapshot, extract_snapshot_from_mission
from .ddmin import DeltaDebugger, SandboxExecutor
from .report import ReportGenerator, CorpusAuditReport
from .signature import signature_from_result


def cmd_export(args: argparse.Namespace) -> int:
    """Export mission snapshot to corpus."""
    exporter = CorpusExporter(args.corpus_dir)

    # Load mission data (from file or API)
    if args.mission_file:
        with open(args.mission_file, "r", encoding="utf-8") as f:
            mission_data = json.load(f)
    else:
        # TODO: Load from API
        print("Error: --mission-file required (API integration pending)")
        return 1

    # Export
    manifest_path = exporter.export_from_dict(mission_data)
    print(f"Exported mission {mission_data['id']} to {manifest_path}")

    # Verify integrity
    if exporter.verify_integrity(mission_data["id"]):
        print("Integrity verified ✓")
    else:
        print("Warning: Integrity check failed")

    return 0


def cmd_minimize(args: argparse.Namespace) -> int:
    """Minimize a mission failure."""
    exporter = CorpusExporter(args.corpus_dir)

    # Load snapshot
    try:
        snapshot = exporter.load_mission(args.mission_id)
    except FileNotFoundError:
        print(f"Error: Mission {args.mission_id} not found in corpus")
        return 1

    # Verify integrity
    if not exporter.verify_integrity(args.mission_id):
        print("Warning: Snapshot integrity check failed")

    # Create executor and debugger
    executor = SandboxExecutor(timeout_seconds=args.timeout)
    debugger = DeltaDebugger(executor=executor, max_iterations=args.max_iterations)

    # Run minimization
    print(f"Minimizing mission {args.mission_id}...")
    result = debugger.minimize(snapshot)

    # Verify 1-minimality
    print("Verifying 1-minimality...")
    is_1minimal = debugger.verify_1minimality(
        snapshot,
        result.minimal_elements,
        result.original_signature,
    )

    # Generate report
    generator = ReportGenerator(args.output_dir)
    report_dir = generator.generate(snapshot, result, is_1minimal)

    # Print summary
    print(f"\nMinimization complete:")
    print(f"  Original elements: {len(result.original_elements)}")
    print(f"  Minimal elements: {len(result.minimal_elements)}")
    print(f"  Reduction rate: {result.reduction_rate:.2%}")
    print(f"  Iterations: {result.iterations}")
    print(f"  Duration: {result.duration_seconds:.2f}s")
    print(f"  Signature match: {result.original_signature.matches(result.final_signature) if result.original_signature and result.final_signature else False}")
    print(f"  1-minimal: {is_1minimal}")
    print(f"\nReport written to: {report_dir}")

    return 0


def cmd_audit(args: argparse.Namespace) -> int:
    """Generate audit report for corpus."""
    auditor = CorpusAuditReport(args.output_dir)
    summary = auditor.generate_summary()

    if "error" in summary:
        print(f"Error: {summary['error']}")
        return 1

    # Print summary
    print(f"\nCorpus Audit Summary:")
    print(f"  Total missions: {summary['total_missions']}")
    print(f"  Reproduced: {summary['reproduced']} ({summary['reproduction_rate']:.2%})")
    print(f"  1-minimal verified: {summary['minimal_verified']} ({summary['minimality_rate']:.2%})")
    print(f"  Avg reduction rate: {summary['avg_reduction_rate']:.2%}")
    print(f"  Median reduction rate: {summary['median_reduction_rate']:.2%}")
    print(f"  Max reduction rate: {summary['max_reduction_rate']:.2%}")

    # Write summary to file
    summary_path = Path(args.output_dir) / "audit_summary.json"
    with open(summary_path, "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2, ensure_ascii=False)
    print(f"\nSummary written to: {summary_path}")

    return 0


def cmd_batch_minimize(args: argparse.Namespace) -> int:
    """Minimize all failed missions in corpus."""
    exporter = CorpusExporter(args.corpus_dir)
    executor = SandboxExecutor(timeout_seconds=args.timeout)
    debugger = DeltaDebugger(executor=executor, max_iterations=args.max_iterations)
    generator = ReportGenerator(args.output_dir)

    mission_ids = exporter.list_missions()
    if not mission_ids:
        print("No missions found in corpus")
        return 1

    print(f"Found {len(mission_ids)} missions in corpus")

    results = []
    for mission_id in mission_ids:
        print(f"\nProcessing mission {mission_id}...")

        try:
            snapshot = exporter.load_mission(mission_id)

            # Skip non-failed missions
            if snapshot.status not in ("failed", "blocked"):
                print(f"  Skipping (status={snapshot.status})")
                continue

            # Minimize
            result = debugger.minimize(snapshot)

            # Verify 1-minimality
            is_1minimal = debugger.verify_1minimality(
                snapshot,
                result.minimal_elements,
                result.original_signature,
            )

            # Generate report
            generator.generate(snapshot, result, is_1minimal)

            results.append({
                "mission_id": mission_id,
                "original": len(result.original_elements),
                "minimal": len(result.minimal_elements),
                "reduction_rate": result.reduction_rate,
                "signature_match": result.original_signature.matches(result.final_signature) if result.original_signature and result.final_signature else False,
                "is_1minimal": is_1minimal,
            })

            print(f"  ✓ Reduced {len(result.original_elements)} → {len(result.minimal_elements)} elements")

        except Exception as e:
            print(f"  ✗ Error: {e}")
            results.append({
                "mission_id": mission_id,
                "error": str(e),
            })

    # Print summary
    print(f"\n\nBatch Minimization Summary:")
    print(f"  Total processed: {len(results)}")
    successful = [r for r in results if "error" not in r]
    if successful:
        avg_reduction = sum(r["reduction_rate"] for r in successful) / len(successful)
        print(f"  Avg reduction rate: {avg_reduction:.2%}")
        reproduced = sum(1 for r in successful if r["signature_match"])
        print(f"  Reproduced: {reproduced}/{len(successful)}")
        minimal = sum(1 for r in successful if r["is_1minimal"])
        print(f"  1-minimal verified: {minimal}/{len(successful)}")

    return 0


def main() -> int:
    """Main entry point."""
    parser = argparse.ArgumentParser(
        prog="failure-minimizer",
        description="Delta debugging for mission failure reproduction",
    )
    parser.add_argument(
        "--corpus-dir",
        default="corpus",
        help="Corpus directory (default: corpus)",
    )
    parser.add_argument(
        "--output-dir",
        default="repro_min",
        help="Output directory for reports (default: repro_min)",
    )

    subparsers = parser.add_subparsers(dest="command", help="Command")

    # Export command
    export_parser = subparsers.add_parser("export", help="Export mission to corpus")
    export_parser.add_argument("--mission-id", type=int, required=True, help="Mission ID")
    export_parser.add_argument("--mission-file", type=str, help="Mission JSON file")

    # Minimize command
    minimize_parser = subparsers.add_parser("minimize", help="Minimize a mission failure")
    minimize_parser.add_argument("--mission-id", type=int, required=True, help="Mission ID")
    minimize_parser.add_argument("--timeout", type=int, default=600, help="Execution timeout (seconds)")
    minimize_parser.add_argument("--max-iterations", type=int, default=100, help="Max iterations")

    # Batch minimize command
    batch_parser = subparsers.add_parser("batch", help="Minimize all failed missions")
    batch_parser.add_argument("--timeout", type=int, default=600, help="Execution timeout (seconds)")
    batch_parser.add_argument("--max-iterations", type=int, default=100, help="Max iterations")

    # Audit command
    audit_parser = subparsers.add_parser("audit", help="Generate audit report")

    args = parser.parse_args()

    if args.command == "export":
        return cmd_export(args)
    elif args.command == "minimize":
        return cmd_minimize(args)
    elif args.command == "batch":
        return cmd_batch_minimize(args)
    elif args.command == "audit":
        return cmd_audit(args)
    else:
        parser.print_help()
        return 1


if __name__ == "__main__":
    sys.exit(main())
