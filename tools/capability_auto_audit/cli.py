"""Command-line interface for the capability auto-audit system.

Usage
-----
    # Full audit: classify all 21 capabilities, update ledger, generate report
    python3 -m tools.capability_auto_audit.cli --run

    # Classify only (no ledger update)
    python3 -m tools.capability_auto_audit.cli --classify

    # Query accuracy for a capability (SQL-sourced)
    python3 -m tools.capability_auto_audit.cli --query calc.resistance

    # Validate against known labels
    python3 -m tools.capability_auto_audit.cli --validate

    # Rollback ledger to a previous run
    python3 -m tools.capability_auto_audit.cli --rollback <run_id>

    # Show summary
    python3 -m tools.capability_auto_audit.cli --summary

    # Negative control: test empty module
    python3 -m tools.capability_auto_audit.cli --negative-control
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from datetime import datetime, timezone

WORKTREE = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(WORKTREE))

from tools.capability_auto_audit.provenance_db import ProvenanceDB
from tools.capability_auto_audit.evidence_classifier import EvidenceClassifier
from tools.capability_auto_audit.ledger_updater import LedgerUpdater
from tools.capability_auto_audit.audit_logger import AuditLogger
from tools.capability_auto_audit.report_generator import ReportGenerator


def cmd_run(args):
    """Full audit: classify, update ledger, log, generate report."""
    run_id = args.run_id or f"run_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}"

    print(f"[IN63-N09] Starting full audit run: {run_id}")

    # Initialize components
    prov_db = ProvenanceDB()
    classifier = EvidenceClassifier(worktree=WORKTREE)
    ledger_updater = LedgerUpdater(
        worktree=WORKTREE, provenance_db=prov_db, classifier=classifier
    )
    audit_logger = AuditLogger()
    report_gen = ReportGenerator(provenance_db=prov_db, classifier=classifier)

    # Step 1: Classify all capabilities
    print("[1/5] Classifying 21 capabilities...")
    results = classifier.classify_all()
    for cap_id, result in sorted(results.items()):
        print(f"  {cap_id:30s} → {result.level:15s} (conf={result.confidence:.2f}, rule={result.rule_fired})")

    # Step 2: Snapshot ledger before update
    print("[2/5] Snapshotting current ledger...")
    current_ledger = ledger_updater._load_ledger()
    audit_logger.log_snapshot(run_id, current_ledger)

    # Step 3: Update ledger
    print("[3/5] Updating capability ledger...")
    update_result = ledger_updater.update_after_verification(run_id, results)
    print(f"  Updated {update_result['capabilities_updated']} capabilities")

    # Step 4: Log changes
    print("[4/5] Logging changes to audit trail...")
    for change in update_result["changes"]:
        audit_logger.log_change(
            run_id=run_id,
            capability_id=change["capability_id"],
            change_type=change["change_type"],
            before=change.get("before"),
            after=change.get("after"),
        )
    print(f"  Logged {len(update_result['changes'])} changes")

    # Step 5: Generate report
    print("[5/5] Generating audit report...")
    report = report_gen.generate_full_audit_report(run_id, results)
    print(f"  Report: {report['report_path']}")

    # Summary
    print()
    print("=" * 60)
    print(f"AUDIT COMPLETE — Run {run_id}")
    print("=" * 60)
    level_counts = {}
    for r in results.values():
        level_counts[r.level] = level_counts.get(r.level, 0) + 1
    for level in ["REAL", "WITHHELD", "MOCK", "STUB", "NOT_SUPPORTED"]:
        print(f"  {level:15s}: {level_counts.get(level, 0)}")
    print(f"  Ledger: {update_result['ledger_path']}")
    print(f"  Report: {report['report_path']}")
    print(f"  Backup: {update_result['backup_path']}")

    # Output JSON for machine consumption
    if args.json:
        output = {
            "run_id": run_id,
            "classifications": {k: v.to_dict() for k, v in results.items()},
            "ledger_update": update_result,
            "report_path": report["report_path"],
        }
        print(json.dumps(output, indent=2, ensure_ascii=False))

    return 0


def cmd_classify(args):
    """Classify only, no ledger update."""
    classifier = EvidenceClassifier(worktree=WORKTREE)

    if args.capability:
        result = classifier.classify_one(args.capability)
        print(json.dumps(result.to_dict(), indent=2, ensure_ascii=False))
    else:
        results = classifier.classify_all()
        for cap_id, result in sorted(results.items()):
            print(f"{cap_id:30s} → {result.level:15s} (conf={result.confidence:.2f})")

    return 0


def cmd_query(args):
    """Query accuracy data from provenance DB (SQL-sourced)."""
    prov_db = ProvenanceDB()
    data = prov_db.get_all_accuracy_for_capability(args.capability)
    print(json.dumps(data, indent=2, ensure_ascii=False))
    print(f"\nSQL query used: {data['sql_query']}")
    return 0


def cmd_validate(args):
    """Validate classifier against known labels."""
    # Known labels based on platform self-assessment and prior audits
    known_labels = {
        "calc.resistance": "REAL",
        "calc.stability": "REAL",
        "propeller.open_water": "MOCK",
        "geometry.generate": "REAL",
        "geometry.hydro_curves": "REAL",
        "geometry.list_models": "REAL",
        "geometry.verify_official": "REAL",
        "shiphull.generate": "REAL",
        "stdmodels.lookup": "REAL",
        "gov.stats": "REAL",
        "knowledge.docs": "REAL",
        "dev.gate_5415": "REAL",
        "dev.git": "REAL",
        "dev.logs": "REAL",
        "dev.nginx_audit": "REAL",
        "dev.probe": "REAL",
        "dev.restart": "REAL",
        "dev.services": "REAL",
        "ai.predict": "MOCK",
        "methods.computation": "REAL",
        "misc.stubs": "STUB",
    }

    classifier = EvidenceClassifier(worktree=WORKTREE)
    validation = classifier.validate_known_labels(known_labels)

    print(f"Validation accuracy: {validation['accuracy']:.1%} ({validation['correct']}/{validation['total']})")
    print()
    for d in validation["details"]:
        status = "✅" if d["correct"] else "❌"
        print(f"  {status} {d['capability_id']:30s} expected={d['expected']:15s} predicted={d['predicted']:15s}")

    return 0 if validation["accuracy"] >= 1.0 else 1


def cmd_rollback(args):
    """Rollback ledger to a previous run."""
    ledger_updater = LedgerUpdater(worktree=WORKTREE)
    result = ledger_updater.rollback_to_run(args.run_id)
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0 if result.get("success") else 1


def cmd_summary(args):
    """Show summary of all components."""
    prov_db = ProvenanceDB()
    audit_logger = AuditLogger()
    ledger_updater = LedgerUpdater(worktree=WORKTREE)

    print("=== Provenance DB ===")
    print(json.dumps(prov_db.summary(), indent=2))
    print()
    print("=== Audit Log ===")
    print(json.dumps(audit_logger.summary(), indent=2))
    print()
    print("=== Capability Ledger ===")
    print(json.dumps(ledger_updater.get_ledger_summary(), indent=2))

    return 0


def cmd_negative_control(args):
    """Negative control: empty module should return NOT_SUPPORTED."""
    classifier = EvidenceClassifier(worktree=WORKTREE)
    result = classifier.classify_empty_module("nonexistent.module")

    assert result.level == "NOT_SUPPORTED", f"Expected NOT_SUPPORTED, got {result.level}"
    assert result.confidence == 1.0, f"Expected confidence 1.0, got {result.confidence}"
    print(f"✅ Negative control passed: {result.capability_id} → {result.level}")
    print(json.dumps(result.to_dict(), indent=2, ensure_ascii=False))
    return 0


def main():
    parser = argparse.ArgumentParser(
        description="Capability Auto-Audit System (IN63-N09)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    subparsers = parser.add_subparsers(dest="command")

    # run
    p_run = subparsers.add_parser("run", help="Full audit run")
    p_run.add_argument("--run-id", help="Run identifier")
    p_run.add_argument("--json", action="store_true", help="Output JSON")

    # classify
    p_classify = subparsers.add_parser("classify", help="Classify capabilities")
    p_classify.add_argument("capability", nargs="?", help="Single capability to classify")

    # query
    p_query = subparsers.add_parser("query", help="Query accuracy (SQL-sourced)")
    p_query.add_argument("capability", help="Capability ID")

    # validate
    subparsers.add_parser("validate", help="Validate against known labels")

    # rollback
    p_rollback = subparsers.add_parser("rollback", help="Rollback ledger")
    p_rollback.add_argument("run_id", help="Run ID to rollback to")

    # summary
    subparsers.add_parser("summary", help="Show summary")

    # negative-control
    subparsers.add_parser("negative-control", help="Negative control test")

    args = parser.parse_args()

    if args.command == "run":
        return cmd_run(args)
    elif args.command == "classify":
        return cmd_classify(args)
    elif args.command == "query":
        return cmd_query(args)
    elif args.command == "validate":
        return cmd_validate(args)
    elif args.command == "rollback":
        return cmd_rollback(args)
    elif args.command == "summary":
        return cmd_summary(args)
    elif args.command == "negative-control":
        return cmd_negative_control(args)
    else:
        parser.print_help()
        return 1


if __name__ == "__main__":
    sys.exit(main())
