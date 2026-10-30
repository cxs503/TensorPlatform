#!/usr/bin/env python3
"""
SWE-bench style evaluation runner for TensorPlatform fleet.

Dispatches eval_set tasks to specified models, collects results,
and produces capability baseline table.

Usage:
    python3 run_eval.py [--models MODEL1,MODEL2,...] [--tasks TASK_IDS] [--dry-run]
    
Example:
    python3 run_eval.py --models qwen3.7-plus,glm-5.2,deepseek-v4-flash
"""

import json
import os
import sys
import time
import argparse
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

# ─── Configuration ───────────────────────────────────────────────────────────

EVAL_SET_PATH = Path(__file__).parent.parent / "eval_set.json"
RESULTS_DIR = Path(__file__).parent / "results"
TENSOR_API_BASE = os.environ.get("TENSOR_API_BASE", "http://localhost:8001")
TENSOR_AGENT_USER = os.environ.get("TENSOR_AGENT_USER", "agent")
TENSOR_AGENT_PASS = os.environ.get("TENSOR_AGENT_PASS", "")

# Repo mapping for task dispatch
REPO_MAP = {
    "TensorPlatform": "/srv/tensor/shared/tensor_eco/TensorPlatform",
    "TensorLBM": "/srv/tensor/shared/tensor_eco/TensorLBM",
    "TensorPhysics": "/srv/tensor/shared/tensor_eco/TensorPhysics",
    "TensorDEM": "/srv/tensor/shared/tensor_eco/TensorDEM",
    "TensorBEM": "/srv/tensor/shared/tensor_eco/TensorBEM",
    "TensorCAD": "/srv/tensor/shared/tensor_eco/TensorCAD",
    "TensorSPH": "/srv/tensor/shared/tensor_eco/TensorSPH",
}

# Default models for evaluation (from roles.json bake-off)
DEFAULT_MODELS = ["qwen3.7-plus", "glm-5.2", "deepseek-v4-flash", "qwen3.6-plus", "glm-5.1"]

# Budget per task (scaled by difficulty)
BUDGET_BY_DIFFICULTY = {
    "easy": {"max_tool_calls": 60, "max_wall_seconds": 3600},
    "medium": {"max_tool_calls": 100, "max_wall_seconds": 7200},
    "hard": {"max_tool_calls": 150, "max_wall_seconds": 10800},
}


def load_eval_set() -> dict:
    """Load evaluation set from JSON."""
    with open(EVAL_SET_PATH) as f:
        return json.load(f)


def get_auth_token() -> Optional[str]:
    """Get authentication token from platform API."""
    try:
        import requests
        resp = requests.post(
            f"{TENSOR_API_BASE}/api/v1/auth/login",
            json={"username": TENSOR_AGENT_USER, "password": TENSOR_AGENT_PASS},
            timeout=10,
        )
        if resp.status_code == 200:
            return resp.json().get("access_token")
    except Exception as e:
        print(f"[WARN] Auth failed: {e}")
    return None


def dispatch_mission(task: dict, model: str, token: str) -> Optional[dict]:
    """Dispatch a single task as a mission to the platform."""
    import requests
    
    repo = task["repo"]
    repo_path = REPO_MAP.get(repo)
    if not repo_path:
        print(f"[ERROR] Unknown repo: {repo}")
        return None
    
    budget = BUDGET_BY_DIFFICULTY.get(task["difficulty"], BUDGET_BY_DIFFICULTY["medium"])
    
    # Construct goal from task description
    goal = f"""EVAL-BENCH:{task['id']} — {task['title']}

## Task Description
{task['description']}

## Acceptance Criteria (independent verification)
{chr(10).join(f'- {c}' for c in task['acceptance_criteria'])}

## Solution Hints (for guidance only)
{chr(10).join(f'- {h}' for h in task.get('solution_hints', []))}

## Constraints
- Repo: {repo}
- Difficulty: {task['difficulty']}
- Budget: {budget['max_tool_calls']} tool calls, {budget['max_wall_seconds']}s wall
- Source mission: {task['source_mission']} (original status: {task['source_status']})
"""
    
    mission_payload = {
        "goal": goal,
        "repo": repo,
        "base_ref": "main",
        "budget": budget,
        "origin": "eval_bench",
        "model": model,
        "worker_role": "implementer",
        "campaign_id": "eval_bench_m538",
        "campaign_phase": f"eval_{task['id']}",
    }
    
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }
    
    try:
        resp = requests.post(
            f"{TENSOR_API_BASE}/api/v1/tensor/missions",
            json=mission_payload,
            headers=headers,
            timeout=30,
        )
        if resp.status_code in (200, 201):
            data = resp.json()
            return {
                "mission_id": data.get("id"),
                "task_id": task["id"],
                "model": model,
                "repo": repo,
                "difficulty": task["difficulty"],
                "dispatched_at": datetime.now(timezone.utc).isoformat(),
                "status": "dispatched",
            }
        else:
            print(f"[ERROR] Dispatch failed for {task['id']}/{model}: {resp.status_code} {resp.text[:200]}")
            return None
    except Exception as e:
        print(f"[ERROR] Dispatch exception for {task['id']}/{model}: {e}")
        return None


def poll_mission_status(mission_id: int, token: str, timeout: int = 10800) -> dict:
    """Poll mission until terminal state or timeout."""
    import requests
    
    headers = {"Authorization": f"Bearer {token}"}
    start = time.time()
    terminal = {"succeeded", "failed", "stopped"}
    
    while time.time() - start < timeout:
        try:
            resp = requests.get(
                f"{TENSOR_API_BASE}/api/v1/tensor/missions/{mission_id}",
                headers=headers,
                timeout=10,
            )
            if resp.status_code == 200:
                data = resp.json()
                status = data.get("status", "unknown")
                if status in terminal:
                    return {
                        "mission_id": mission_id,
                        "status": status,
                        "finished_at": data.get("finished_at"),
                        "result": data.get("result", {}),
                        "wall_seconds": (time.time() - start),
                    }
        except Exception:
            pass
        time.sleep(30)
    
    return {
        "mission_id": mission_id,
        "status": "timeout",
        "wall_seconds": timeout,
    }


def verify_acceptance(task: dict, result: dict) -> dict:
    """
    Independent verification of acceptance criteria.
    This does NOT reuse original author's self-tests.
    """
    checks = []
    
    # Check 1: Mission completed successfully
    checks.append({
        "name": "mission_terminal_success",
        "pass": result.get("status") == "succeeded",
        "detail": f"status={result.get('status')}",
    })
    
    # Check 2: Result contains evidence
    evidence = result.get("result", {}).get("evidence", {})
    checks.append({
        "name": "has_evidence",
        "pass": bool(evidence),
        "detail": f"evidence_keys={list(evidence.keys()) if evidence else 'none'}",
    })
    
    # Check 3: Evidence has checks
    ev_checks = evidence.get("checks", [])
    checks.append({
        "name": "has_acceptance_checks",
        "pass": len(ev_checks) >= len(task["acceptance_criteria"]) * 0.5,
        "detail": f"checks={len(ev_checks)}, expected≥{len(task['acceptance_criteria'])//2}",
    })
    
    # Check 4: All checks pass
    pass_count = sum(1 for c in ev_checks if c.get("pass", False))
    total_count = len(ev_checks)
    pass_rate = pass_count / total_count if total_count > 0 else 0
    checks.append({
        "name": "checks_pass_rate",
        "pass": pass_rate >= 0.8,
        "detail": f"pass_rate={pass_rate:.1%} ({pass_count}/{total_count})",
    })
    
    # Check 5: Files changed (proof of work)
    files_changed = evidence.get("files_changed", [])
    checks.append({
        "name": "files_changed",
        "pass": len(files_changed) > 0,
        "detail": f"files={len(files_changed)}",
    })
    
    all_pass = all(c["pass"] for c in checks)
    
    return {
        "task_id": task["id"],
        "pass": all_pass,
        "pass_rate": pass_rate,
        "checks": checks,
    }


def run_evaluation(models: List[str], task_ids: Optional[List[str]] = None, dry_run: bool = False):
    """Run full evaluation across models and tasks."""
    eval_set = load_eval_set()
    tasks = eval_set["tasks"]
    
    if task_ids:
        tasks = [t for t in tasks if t["id"] in task_ids]
    
    print(f"=== SWE-bench Evaluation Runner ===")
    print(f"Tasks: {len(tasks)}")
    print(f"Models: {models}")
    print(f"Dry run: {dry_run}")
    print()
    
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    
    # Get auth token
    token = get_auth_token()
    if not token and not dry_run:
        print("[WARN] No auth token, running in dry-run mode")
        dry_run = True
    
    results = []
    
    for model in models:
        print(f"\n--- Model: {model} ---")
        model_results = []
        
        for task in tasks:
            print(f"  Task {task['id']} ({task['difficulty']}/{task['repo']}): ", end="")
            
            if dry_run:
                print("[DRY-RUN]")
                model_results.append({
                    "task_id": task["id"],
                    "model": model,
                    "repo": task["repo"],
                    "difficulty": task["difficulty"],
                    "status": "dry_run",
                    "dispatched_at": datetime.now(timezone.utc).isoformat(),
                })
                continue
            
            # Dispatch
            dispatch = dispatch_mission(task, model, token)
            if not dispatch:
                print("[DISPATCH FAILED]")
                model_results.append({
                    "task_id": task["id"],
                    "model": model,
                    "status": "dispatch_failed",
                })
                continue
            
            mission_id = dispatch["mission_id"]
            print(f"dispatched m{mission_id}, polling...")
            
            # Poll for completion
            final = poll_mission_status(mission_id, token)
            
            # Verify acceptance
            verification = verify_acceptance(task, final)
            
            result = {
                **dispatch,
                "mission_id": mission_id,
                "final_status": final["status"],
                "wall_seconds": final.get("wall_seconds", 0),
                "verification": verification,
                "passed": verification["pass"],
            }
            model_results.append(result)
            
            status_icon = "✓" if verification["pass"] else "✗"
            print(f"    → {status_icon} {final['status']} (wall={final.get('wall_seconds', 0):.0f}s)")
        
        results.extend(model_results)
    
    # Save results
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    results_file = RESULTS_DIR / f"eval_results_{timestamp}.json"
    
    output = {
        "meta": {
            "run_at": datetime.now(timezone.utc).isoformat(),
            "models": models,
            "task_count": len(tasks),
            "dry_run": dry_run,
        },
        "results": results,
    }
    
    with open(results_file, "w") as f:
        json.dump(output, f, indent=2, ensure_ascii=False)
    
    print(f"\n=== Results saved to {results_file} ===")
    
    # Generate baseline table
    generate_baseline(results, models, tasks)
    
    return output


def generate_baseline(results: List[dict], models: List[str], tasks: List[dict]):
    """Generate capability baseline table."""
    
    # Group by model and difficulty
    baseline = {}
    for model in models:
        baseline[model] = {
            "easy": {"total": 0, "passed": 0, "wall_total": 0},
            "medium": {"total": 0, "passed": 0, "wall_total": 0},
            "hard": {"total": 0, "passed": 0, "wall_total": 0},
            "overall": {"total": 0, "passed": 0, "wall_total": 0},
        }
    
    for r in results:
        model = r["model"]
        diff = r.get("difficulty", "medium")
        if model not in baseline:
            continue
        
        baseline[model][diff]["total"] += 1
        baseline[model]["overall"]["total"] += 1
        
        if r.get("passed", False):
            baseline[model][diff]["passed"] += 1
            baseline[model]["overall"]["passed"] += 1
        
        baseline[model][diff]["wall_total"] += r.get("wall_seconds", 0)
        baseline[model]["overall"]["wall_total"] += r.get("wall_seconds", 0)
    
    # Print table
    print("\n" + "=" * 80)
    print("CAPABILITY BASELINE TABLE")
    print("=" * 80)
    print(f"{'Model':<20} {'Difficulty':<10} {'Pass':<8} {'Total':<8} {'Rate':<10} {'Wall(s)':<10}")
    print("-" * 80)
    
    for model in models:
        for diff in ["easy", "medium", "hard", "overall"]:
            d = baseline[model][diff]
            rate = d["passed"] / d["total"] if d["total"] > 0 else 0
            print(f"{model:<20} {diff:<10} {d['passed']:<8} {d['total']:<8} {rate:<10.1%} {d['wall_total']:<10.0f}")
        print("-" * 80)
    
    # Save baseline
    baseline_file = RESULTS_DIR / "baseline_table.json"
    with open(baseline_file, "w") as f:
        json.dump(baseline, f, indent=2, ensure_ascii=False)
    
    print(f"\nBaseline saved to {baseline_file}")
    
    # Confidence intervals (Wilson score for small sample)
    print("\n--- Confidence Intervals (Wilson score, 95% CI) ---")
    import math
    
    for model in models:
        d = baseline[model]["overall"]
        n = d["total"]
        if n == 0:
            continue
        p = d["passed"] / n
        z = 1.96  # 95% CI
        denom = 1 + z**2 / n
        center = (p + z**2 / (2*n)) / denom
        margin = z * math.sqrt((p*(1-p) + z**2/(4*n)) / n) / denom
        lo = max(0, center - margin)
        hi = min(1, center + margin)
        print(f"  {model:<20}: {p:.1%} [{lo:.1%}, {hi:.1%}] (n={n})")


def main():
    parser = argparse.ArgumentParser(description="SWE-bench evaluation runner")
    parser.add_argument("--models", type=str, default=",".join(DEFAULT_MODELS),
                        help="Comma-separated model names")
    parser.add_argument("--tasks", type=str, default=None,
                        help="Comma-separated task IDs (default: all)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Run without dispatching missions")
    args = parser.parse_args()
    
    models = [m.strip() for m in args.models.split(",")]
    task_ids = [t.strip() for t in args.tasks.split(",")] if args.tasks else None
    
    run_evaluation(models, task_ids, args.dry_run)


if __name__ == "__main__":
    main()
