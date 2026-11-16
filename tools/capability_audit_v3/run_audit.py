#!/usr/bin/env python3
"""v3 审计执行器：调用探针引擎，生成 artifacts 和报告。

用法: python -m tools.capability_audit_v3.run_audit
"""
from __future__ import annotations

import json
import sys
import os
from pathlib import Path
from datetime import datetime

# Add repo root to path
repo_root = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(repo_root))

from tools.capability_audit_v3.probes_v3 import run_full_audit, ProbeResult, CapabilityVerdict


def call_ship_api(path: str, method: str = "GET", body: dict = None) -> dict:
    """Wrapper for the ship API call (simulates call_ship_api behavior)."""
    import subprocess
    # Use curl to call the API
    url = f"http://localhost:8002/api/v1/{path}"
    cmd = ["curl", "-s", "-X", method, url]
    if body and method == "POST":
        cmd.extend(["-H", "Content-Type: application/json", "-d", json.dumps(body)])
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    try:
        data = json.loads(result.stdout)
        return {"status": 200, "data": data}
    except json.JSONDecodeError:
        return {"status": result.returncode, "data": result.stdout}


def main():
    """Run the full audit and save results."""
    print("=" * 60)
    print("Capability Audit v3 — 全量 21 项能力不变量探针覆盖")
    print("=" * 60)
    
    # Manual grades from audit_capabilities
    manual_grades = {
        "calc.resistance": "MOCK",
        "calc.stability": "MOCK",
        "propeller.open_water": "MOCK",
        "geometry.hydro_curves": "REAL",
        "gov.stats": "MOCK",
        "knowledge.docs": "MOCK",
        "misc.stubs": "STUB",
        "ai.predict": "NOT_SUPPORTED",
        "geometry.generate": "WITHHELD",
        "geometry.list_models": "WITHHELD",
        "geometry.verify_official": "WITHHELD",
        "shiphull.generate": "WITHHELD",
        "stdmodels.lookup": "WITHHELD",
        "methods.computation": "WITHHELD",
        "dev.gate_5415": "WITHHELD",
        "dev.git": "WITHHELD",
        "dev.logs": "WITHHELD",
        "dev.nginx_audit": "WITHHELD",
        "dev.probe": "WITHHELD",
        "dev.restart": "WITHHELD",
        "dev.services": "WITHHELD",
    }

    # Run audit
    verdicts = run_full_audit(call_ship_api, manual_grades)

    # Calculate consistency
    matches = sum(1 for v in verdicts if v.manual_review_match)
    total = len(verdicts)
    consistency = matches / total if total > 0 else 0

    print(f"\n审计完成: {total} 项能力")
    print(f"与人工复核一致: {matches}/{total} ({consistency:.0%})")
    print()

    # Print summary
    for v in verdicts:
        match_icon = "✓" if v.manual_review_match else "✗"
        print(f"  [{match_icon}] {v.capability:30s} → {v.verdict:15s} (conf={v.confidence:.2f}) {v.evidence_summary}")

    # Save results
    artifacts_dir = repo_root / "artifacts" / "capability_audit_v3"
    artifacts_dir.mkdir(parents=True, exist_ok=True)

    results = {
        "audit_version": "3.0.0",
        "timestamp": datetime.utcnow().isoformat() + "Z",
        "total_capabilities": total,
        "consistency_rate": consistency,
        "verdicts": [v.to_dict() for v in verdicts],
        "summary": {
            "REAL": sum(1 for v in verdicts if v.verdict == "REAL"),
            "MOCK": sum(1 for v in verdicts if v.verdict == "MOCK"),
            "STUB": sum(1 for v in verdicts if v.verdict == "STUB"),
            "WITHHELD": sum(1 for v in verdicts if v.verdict == "WITHHELD"),
            "NOT_SUPPORTED": sum(1 for v in verdicts if v.verdict == "NOT_SUPPORTED"),
        }
    }

    results_path = artifacts_dir / "audit_results.json"
    results_path.write_text(json.dumps(results, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\n结果已保存: {results_path}")

    # Save probe details
    probe_details = []
    for v in verdicts:
        for p in v.probes:
            probe_details.append(p.to_dict())
    
    probes_path = artifacts_dir / "probe_details.json"
    probes_path.write_text(json.dumps(probe_details, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"探针详情已保存: {probes_path}")

    return results


if __name__ == "__main__":
    main()
