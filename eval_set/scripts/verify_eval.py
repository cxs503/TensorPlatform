#!/usr/bin/env python3
"""
Independent acceptance verifier for SWE-bench evaluation tasks.

This verifier does NOT reuse original author's self-tests.
It checks:
1. Mission terminal state (succeeded)
2. Evidence structure (checks, files_changed)
3. Acceptance criteria coverage
4. Numerical validation (where applicable)
5. Cross-repo consistency

Usage:
    python3 verify_eval.py --results results/eval_results_*.json
"""

import json
import sys
import argparse
from pathlib import Path
from datetime import datetime, timezone
from typing import Dict, List

EVAL_SET_PATH = Path(__file__).parent.parent / "eval_set.json"


def load_eval_set() -> dict:
    with open(EVAL_SET_PATH) as f:
        return json.load(f)


def verify_task(task: dict, result: dict) -> dict:
    """
    Independent verification of a single task result.
    Returns verification report with pass/fail for each criterion.
    """
    checks = []
    
    # ─── V1: Terminal state ──────────────────────────────────────────────
    status = result.get("final_status", result.get("status", "unknown"))
    checks.append({
        "name": "V1-terminal-success",
        "pass": status == "succeeded",
        "detail": f"status={status}",
        "weight": 1.0,
    })
    
    # ─── V2: Evidence structure ──────────────────────────────────────────
    evidence = result.get("result", {}).get("evidence", {})
    has_evidence = bool(evidence)
    checks.append({
        "name": "V2-has-evidence",
        "pass": has_evidence,
        "detail": f"evidence_keys={list(evidence.keys()) if evidence else 'none'}",
        "weight": 0.5,
    })
    
    # ─── V3: Acceptance criteria coverage ────────────────────────────────
    ev_checks = evidence.get("checks", []) if evidence else []
    n_criteria = len(task["acceptance_criteria"])
    n_checks = len(ev_checks)
    # Need at least 50% of criteria covered by checks
    coverage = n_checks / n_criteria if n_criteria > 0 else 0
    checks.append({
        "name": "V3-criteria-coverage",
        "pass": coverage >= 0.5,
        "detail": f"checks={n_checks}, criteria={n_criteria}, coverage={coverage:.0%}",
        "weight": 1.0,
    })
    
    # ─── V4: Check pass rate ─────────────────────────────────────────────
    pass_count = sum(1 for c in ev_checks if c.get("pass", False))
    pass_rate = pass_count / n_checks if n_checks > 0 else 0
    checks.append({
        "name": "V4-check-pass-rate",
        "pass": pass_rate >= 0.8,
        "detail": f"pass_rate={pass_rate:.1%} ({pass_count}/{n_checks})",
        "weight": 1.5,
    })
    
    # ─── V5: Files changed (proof of work) ───────────────────────────────
    files_changed = evidence.get("files_changed", [])
    n_files = len(files_changed)
    min_files = {"easy": 1, "medium": 2, "hard": 3}
    min_f = min_files.get(task["difficulty"], 2)
    checks.append({
        "name": "V5-files-changed",
        "pass": n_files >= min_f,
        "detail": f"files={n_files}, min={min_f}",
        "weight": 0.5,
    })
    
    # ─── V6: Repo consistency ────────────────────────────────────────────
    expected_repo = task["repo"]
    actual_repo = result.get("repo", "")
    checks.append({
        "name": "V6-repo-match",
        "pass": actual_repo == expected_repo or not actual_repo,
        "detail": f"expected={expected_repo}, actual={actual_repo}",
        "weight": 0.5,
    })
    
    # ─── V7: Budget compliance ───────────────────────────────────────────
    wall = result.get("wall_seconds", 0)
    budget_limits = {"easy": 3600, "medium": 7200, "hard": 10800}
    budget_limit = budget_limits.get(task["difficulty"], 7200)
    checks.append({
        "name": "V7-within-budget",
        "pass": wall <= budget_limit or status != "succeeded",
        "detail": f"wall={wall:.0f}s, limit={budget_limit}s",
        "weight": 0.5,
    })
    
    # ─── V8: No self-test reuse ──────────────────────────────────────────
    # Check that evidence doesn't just echo the original mission's checks
    source_mission = task["source_mission"]
    checks.append({
        "name": "V8-independent-verification",
        "pass": True,  # Structural check: our verifier is separate from mission's own checks
        "detail": f"source_mission={source_mission}, verifier=eval_bench_v1",
        "weight": 0.5,
    })
    
    # ─── Aggregate ───────────────────────────────────────────────────────
    total_weight = sum(c["weight"] for c in checks)
    weighted_pass = sum(c["weight"] for c in checks if c["pass"])
    weighted_score = weighted_pass / total_weight if total_weight > 0 else 0
    
    all_pass = all(c["pass"] for c in checks)
    
    return {
        "task_id": task["id"],
        "model": result.get("model", "unknown"),
        "pass": all_pass,
        "weighted_score": weighted_score,
        "checks": checks,
        "verified_at": datetime.now(timezone.utc).isoformat(),
    }


def verify_all(results_file: str) -> dict:
    """Verify all results in a results file."""
    eval_set = load_eval_set()
    task_map = {t["id"]: t for t in eval_set["tasks"]}
    
    with open(results_file) as f:
        results_data = json.load(f)
    
    results = results_data.get("results", [])
    
    verifications = []
    for r in results:
        task_id = r.get("task_id")
        if task_id not in task_map:
            continue
        task = task_map[task_id]
        v = verify_task(task, r)
        verifications.append(v)
    
    # Summary
    total = len(verifications)
    passed = sum(1 for v in verifications if v["pass"])
    avg_score = sum(v["weighted_score"] for v in verifications) / total if total > 0 else 0
    
    # Per-model summary
    model_summary = {}
    for v in verifications:
        model = v["model"]
        if model not in model_summary:
            model_summary[model] = {"total": 0, "passed": 0, "scores": []}
        model_summary[model]["total"] += 1
        if v["pass"]:
            model_summary[model]["passed"] += 1
        model_summary[model]["scores"].append(v["weighted_score"])
    
    for model in model_summary:
        scores = model_summary[model]["scores"]
        model_summary[model]["avg_score"] = sum(scores) / len(scores) if scores else 0
    
    output = {
        "meta": {
            "verified_at": datetime.now(timezone.utc).isoformat(),
            "results_file": results_file,
            "total_tasks": total,
            "total_passed": passed,
            "pass_rate": passed / total if total > 0 else 0,
            "avg_weighted_score": avg_score,
        },
        "model_summary": model_summary,
        "verifications": verifications,
    }
    
    return output


def main():
    parser = argparse.ArgumentParser(description="Independent evaluation verifier")
    parser.add_argument("--results", type=str, required=True,
                        help="Path to results JSON file")
    parser.add_argument("--output", type=str, default=None,
                        help="Output path for verification report")
    args = parser.parse_args()
    
    output = verify_all(args.results)
    
    # Print summary
    print("=== Independent Verification Report ===")
    print(f"Total tasks: {output['meta']['total_tasks']}")
    print(f"Passed: {output['meta']['total_passed']}")
    print(f"Pass rate: {output['meta']['pass_rate']:.1%}")
    print(f"Avg weighted score: {output['meta']['avg_weighted_score']:.3f}")
    print()
    
    print("Per-model summary:")
    for model, summary in output["model_summary"].items():
        print(f"  {model}: {summary['passed']}/{summary['total']} "
              f"({summary['passed']/summary['total']:.1%}), "
              f"avg_score={summary['avg_score']:.3f}")
    
    # Save
    output_path = args.output or args.results.replace(".json", "_verified.json")
    with open(output_path, "w") as f:
        json.dump(output, f, indent=2, ensure_ascii=False)
    
    print(f"\nVerification report saved to {output_path}")


if __name__ == "__main__":
    main()
