#!/usr/bin/env python3
"""Integration demo — exercise all four autonomy modules together.

Generates:
  1. Artifact lineage DAG from current workspace
  2. Evidence integrity check on sample reports
  3. Worktree GC plan for current work/ directory
  4. Disk pressure assessment
  5. Visualization of lineage DAG
"""
import json
import os
import sys
from pathlib import Path

# Ensure project root is on path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from tools.artifact_lineage import build_lineage_dag, verify_integrity, detect_tampering
from tools.evidence_integrity import EvidenceReport, verify_report
from tools.worktree_gc import WorktreeGC, DiskPressureGate


def main():
    root = Path(__file__).resolve().parent.parent.parent
    results = {}

    # -----------------------------------------------------------------------
    # 1. Artifact Lineage DAG
    # -----------------------------------------------------------------------
    print("=" * 60)
    print("1. ARTIFACT LINEAGE DAG")
    print("=" * 60)

    scan_dirs = []
    for d in ["artifacts", "runs", "docs", "tensor_reports"]:
        p = root / d
        if p.exists():
            scan_dirs.append(p)

    dag = build_lineage_dag(scan_dirs)
    dag_path = root / "artifacts" / "lineage_dag_demo.json"
    dag.save(dag_path)

    print(f"  Scan roots: {[str(d) for d in scan_dirs]}")
    print(f"  Nodes: {len(dag.nodes)}")
    print(f"  Edges: {len(dag.graph.edges)}")
    print(f"  Is DAG: {dag.is_acyclic()}")
    print(f"  Categories: {dag.scan_stats.get('categories', {})}")
    print(f"  Saved to: {dag_path}")

    # Verify integrity
    integrity = verify_integrity(dag, scan_dirs)
    print(f"  Integrity: {'CLEAN' if integrity['ok'] else 'COMPROMISED'}")
    print(f"  Checked: {integrity['checked']}/{integrity['total']}")

    results["lineage_dag"] = {
        "nodes": len(dag.nodes),
        "edges": len(dag.graph.edges),
        "is_acyclic": dag.is_acyclic(),
        "integrity_ok": integrity["ok"],
        "files_checked": integrity["checked"],
    }

    # -----------------------------------------------------------------------
    # 2. Evidence Integrity Check
    # -----------------------------------------------------------------------
    print("\n" + "=" * 60)
    print("2. EVIDENCE INTEGRITY CHECK")
    print("=" * 60)

    # Check existing report files
    report_files = list((root / "tensor_reports").glob("*.json"))[:3] if (root / "tensor_reports").exists() else []
    # Also check root-level report JSONs
    for rf in root.glob("report*.json"):
        report_files.append(rf)

    evidence_results = []
    for rf in report_files[:5]:
        try:
            report = EvidenceReport.from_json(rf)
            result = verify_report(report)
            evidence_results.append({
                "file": rf.name,
                "passed": result.passed,
                "score": result.score,
                "errors": len(result.errors),
                "warnings": len(result.warnings),
            })
            status = "PASS" if result.passed else "FAIL"
            print(f"  {rf.name}: {status} (score={result.score})")
        except Exception as e:
            print(f"  {rf.name}: ERROR ({e})")

    results["evidence_integrity"] = {
        "reports_checked": len(evidence_results),
        "passed": sum(1 for r in evidence_results if r["passed"]),
        "failed": sum(1 for r in evidence_results if not r["passed"]),
        "details": evidence_results,
    }

    # -----------------------------------------------------------------------
    # 3. Worktree GC
    # -----------------------------------------------------------------------
    print("\n" + "=" * 60)
    print("3. WORKTREE GC")
    print("=" * 60)

    work_dir = root / "work"
    archive_dir = root / "work" / ".archives"

    if work_dir.exists():
        gc = WorktreeGC(work_dir, archive_dir)
        disk = gc.gate.check()
        plan = gc.plan()

        print(f"  Disk: {disk.get('used_pct', '?')}% used ({disk.get('pressure', '?')})")
        print(f"  Categories: {plan.get('categories', {})}")
        print(f"  Reclaimable: {plan.get('total_size_reclaimable', 0)} bytes")

        results["worktree_gc"] = {
            "disk_usage_pct": disk.get("used_pct"),
            "disk_pressure": disk.get("pressure"),
            "categories": plan.get("categories", {}),
            "reclaimable_bytes": plan.get("total_size_reclaimable", 0),
        }
    else:
        print("  No work/ directory found")
        results["worktree_gc"] = {"status": "no_work_dir"}

    # -----------------------------------------------------------------------
    # 4. Summary
    # -----------------------------------------------------------------------
    print("\n" + "=" * 60)
    print("SUMMARY")
    print("=" * 60)
    print(json.dumps(results, indent=2, default=str))

    # Save results
    output_path = root / "artifacts" / "autonomy_demo_results.json"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w") as f:
        json.dump(results, f, indent=2, default=str)
    print(f"\nResults saved to {output_path}")


if __name__ == "__main__":
    main()
