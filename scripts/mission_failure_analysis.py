"""
Mission Failure Root Cause Classification & Recovery Analysis

Analyzes terminal-state missions (especially failed) to classify failure
root causes into categories: environment / criteria / spec / budget,
computes recoverable rate, and proposes re-dispatch strategies.

Usage:
    python scripts/mission_failure_analysis.py [--output-dir DIR]

Outputs:
    - artifacts/mission_failure_analysis.json  (structured results)
    - docs/mission_failure_analysis.md         (paper-level report)
"""

from __future__ import annotations

import json
import os
import re
import sys
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

# ---------------------------------------------------------------------------
# DB connection (reads env vars set by the running TP instance)
# ---------------------------------------------------------------------------
import psycopg

DB_URL = os.getenv(
    "DATABASE_URL",
    "host=127.0.0.1 port=5432 dbname=tensorplatform user=foundation password=uGTN-KwuQv_7l8Tw0MMWUx_kf-nF2Rzz",
)

# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass
class MissionRecord:
    id: int
    status: str
    repo: str
    model: Optional[str]
    goal: str
    result: Optional[dict]
    created_at: str
    finished_at: Optional[str]
    duration_s: Optional[int]
    fail_category: str = ""
    fail_stage: str = ""
    recoverable: bool = False
    recovery_suggestion: str = ""


@dataclass
class AnalysisResult:
    total_missions: int
    terminal_missions: int
    terminal_distribution: dict[str, int]
    failed_count: int
    failure_categories: dict[str, int]
    failure_by_repo: dict[str, int]
    failure_by_model: dict[str, int]
    failure_by_stage: dict[str, int]
    recoverable_count: int
    recoverable_rate: float
    category_recovery: dict[str, dict]
    recommendations: list[str]
    analysis_timestamp: str = ""
    sample_missions: list[dict] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Classification rules (documented)
# ---------------------------------------------------------------------------

# Root-cause taxonomy (append-only, versioned)
# v1 — 2026-11-07 IN16-K11
#
# Category        | Trigger patterns (result JSON text)
# ----------------|--------------------------------------
# budget_exhausted| "budget_exhausted" OR "tool_calls" exhausted
# wall_exhausted  | "wall_exhausted" OR "wall_seconds" exceeded
# no_progress     | "no_progress" — model made no meaningful changes
# agent_lost      | "agent_lost" — daemon died / heartbeat timeout
# worktree_empty  | "无任何改动" OR "worktree_empty" — no files changed
# llm_error       | "llm_error" — LLM API error / rate limit
# runtime_error   | "error" / "exception" / "错误" in result
# timeout         | "timeout" / "超时"
# revoked         | "revoked" / "撤销" — manually revoked
# stopped         | "stopped" / "停止" — manually stopped
# other           | unmatched

CATEGORY_RULES = [
    ("budget_exhausted", ["budget_exhausted", "tool_calls"]),
    ("wall_exhausted", ["wall_exhausted", "wall_seconds"]),
    ("no_progress", ["no_progress"]),
    ("agent_lost", ["agent_lost"]),
    ("worktree_empty", ["无任何改动", "worktree_empty"]),
    ("llm_error", ["llm_error"]),
    ("runtime_error", ["error", "exception", "错误"]),
    ("timeout", ["timeout", "超时"]),
    ("revoked", ["revoked", "撤销"]),
    ("stopped", ["stopped", "停止"]),
]

# Recovery feasibility per category
RECOVERABILITY = {
    "budget_exhausted": True,
    "wall_exhausted": True,
    "no_progress": True,
    "agent_lost": True,
    "worktree_empty": True,
    "llm_error": True,
    "runtime_error": False,
    "timeout": True,
    "revoked": False,
    "stopped": False,
}

# Recovery suggestions per category
RECOVERY_SUGGESTIONS = {
    "budget_exhausted": "Increase budget caps (max_tool_calls / max_wall_seconds) or split into sub-missions",
    "wall_exhausted": "Increase wall_seconds budget or use smaller mesh/coarser resolution",
    "no_progress": "Improve goal specificity; add explicit acceptance criteria; use stronger model",
    "agent_lost": "Reduce mission complexity; add checkpoint/heartbeat; use more stable model",
    "worktree_empty": "Verify repo whitelist; ensure worktree is properly initialized; check base_ref",
    "llm_error": "Retry with different model; add retry logic; check API availability",
    "runtime_error": "Fix code bugs; add error handling; validate inputs before execution",
    "timeout": "Increase timeout; optimize computation; use cluster for heavy tasks",
    "revoked": "Review revocation reason; fix issues and re-dispatch",
    "stopped": "Review stop reason; fix issues and re-dispatch",
}


def classify_failure(result_text: str) -> tuple[str, str]:
    """Classify a failure based on result JSON text.
    
    Returns (category, fail_stage).
    """
    if not result_text:
        return ("unknown", "")
    
    for category, patterns in CATEGORY_RULES:
        for pattern in patterns:
            if pattern.lower() in result_text.lower():
                # Extract fail_stage if present
                stage_match = re.search(r'"fail_stage"\s*:\s*"([^"]*)"', result_text)
                stage = stage_match.group(1) if stage_match else ""
                return (category, stage)
    
    return ("other", "")


def is_recoverable(category: str) -> bool:
    """Check if a failure category is recoverable."""
    return RECOVERABILITY.get(category, False)


def get_recovery_suggestion(category: str) -> str:
    """Get recovery suggestion for a category."""
    return RECOVERY_SUGGESTIONS.get(category, "Review and fix manually")


# ---------------------------------------------------------------------------
# Database query
# ---------------------------------------------------------------------------

def get_terminal_missions(limit: int = 10000) -> list[dict]:
    """Query terminal missions from the database."""
    conn = psycopg.connect(DB_URL)
    cur = conn.cursor()
    
    cur.execute("""
        SELECT id, status, repo, model, goal, result,
               created_at, finished_at,
               EXTRACT(EPOCH FROM (finished_at - created_at))::int as duration_s
        FROM tensor_dev_missions 
        WHERE status IN ('succeeded', 'failed', 'stopped', 'revoked', 'orphan')
        ORDER BY created_at DESC
        LIMIT %s
    """, (limit,))
    
    rows = cur.fetchall()
    cur.close()
    conn.close()
    
    missions = []
    for row in rows:
        mission_id, status, repo, model, goal, result, created_at, finished_at, duration_s = row
        missions.append({
            "id": mission_id,
            "status": status,
            "repo": repo,
            "model": model,
            "goal": goal[:200] if goal else "",
            "result": result,
            "created_at": str(created_at) if created_at else "",
            "finished_at": str(finished_at) if finished_at else "",
            "duration_s": duration_s,
        })
    
    return missions


def classify_mission(mission: dict) -> MissionRecord:
    """Classify a single mission."""
    result = mission.get("result") or {}
    result_text = json.dumps(result, ensure_ascii=False) if isinstance(result, dict) else str(result)
    
    category, stage = classify_failure(result_text)
    recoverable = is_recoverable(category)
    suggestion = get_recovery_suggestion(category) if recoverable else ""
    
    return MissionRecord(
        id=mission["id"],
        status=mission["status"],
        repo=mission["repo"],
        model=mission["model"],
        goal=mission["goal"],
        result=result,
        created_at=mission["created_at"],
        finished_at=mission["finished_at"],
        duration_s=mission["duration_s"],
        fail_category=category,
        fail_stage=stage,
        recoverable=recoverable,
        recovery_suggestion=suggestion,
    )


# ---------------------------------------------------------------------------
# Analysis
# ---------------------------------------------------------------------------

def analyze_missions(missions: list[dict]) -> AnalysisResult:
    """Analyze missions and return classification results."""
    classified = [classify_mission(m) for m in missions]
    
    # Terminal distribution
    terminal_dist = {}
    for m in classified:
        terminal_dist[m.status] = terminal_dist.get(m.status, 0) + 1
    
    # Failed missions analysis
    failed = [m for m in classified if m.status == "failed"]
    failed_count = len(failed)
    
    # Failure categories
    cat_dist = {}
    for m in failed:
        cat_dist[m.fail_category] = cat_dist.get(m.fail_category, 0) + 1
    
    # Failure by repo
    repo_dist = {}
    for m in failed:
        repo_dist[m.repo] = repo_dist.get(m.repo, 0) + 1
    
    # Failure by model
    model_dist = {}
    for m in failed:
        model_key = m.model or "(none)"
        model_dist[model_key] = model_dist.get(model_key, 0) + 1
    
    # Failure by stage
    stage_dist = {}
    for m in failed:
        stage_key = m.fail_stage or "(none)"
        stage_dist[stage_key] = stage_dist.get(stage_key, 0) + 1
    
    # Recoverable analysis
    recoverable = [m for m in failed if m.recoverable]
    recoverable_count = len(recoverable)
    recoverable_rate = recoverable_count / failed_count if failed_count > 0 else 0.0
    
    # Category recovery breakdown
    cat_recovery = {}
    for cat, cnt in cat_dist.items():
        cat_recoverable = sum(1 for m in failed if m.fail_category == cat and m.recoverable)
        cat_recovery[cat] = {
            "count": cnt,
            "recoverable": cat_recoverable,
            "recoverable_rate": cat_recoverable / cnt if cnt > 0 else 0.0,
            "suggestion": get_recovery_suggestion(cat),
        }
    
    # Recommendations
    recommendations = generate_recommendations(cat_dist, recoverable_rate, repo_dist, model_dist)
    
    # Sample missions (top 10 failed)
    sample_missions = []
    for m in failed[:10]:
        sample_missions.append({
            "id": m.id,
            "repo": m.repo,
            "model": m.model,
            "category": m.fail_category,
            "stage": m.fail_stage,
            "recoverable": m.recoverable,
            "suggestion": m.recovery_suggestion,
        })
    
    return AnalysisResult(
        total_missions=len(missions),
        terminal_missions=len(missions),
        terminal_distribution=terminal_dist,
        failed_count=failed_count,
        failure_categories=cat_dist,
        failure_by_repo=repo_dist,
        failure_by_model=model_dist,
        failure_by_stage=stage_dist,
        recoverable_count=recoverable_count,
        recoverable_rate=recoverable_rate,
        category_recovery=cat_recovery,
        recommendations=recommendations,
        analysis_timestamp=datetime.now(timezone.utc).isoformat(),
        sample_missions=sample_missions,
    )


def generate_recommendations(
    cat_dist: dict[str, int],
    recoverable_rate: float,
    repo_dist: dict[str, int],
    model_dist: dict[str, int],
) -> list[str]:
    """Generate re-dispatch strategy recommendations."""
    recs = []
    
    # Overall recovery potential
    recs.append(f"Overall recoverable rate: {recoverable_rate:.1%} ({sum(cat_dist.get(c, 0) for c in RECOVERABILITY if RECOVERABILITY[c])}/{sum(cat_dist.values())})")
    
    # Top failure categories
    sorted_cats = sorted(cat_dist.items(), key=lambda x: x[1], reverse=True)
    if sorted_cats:
        top_cat, top_cnt = sorted_cats[0]
        recs.append(f"Top failure category: {top_cat} ({top_cnt} cases, {top_cnt/sum(cat_dist.values()):.1%})")
    
    # Budget-related failures
    budget_failures = cat_dist.get("budget_exhausted", 0) + cat_dist.get("wall_exhausted", 0)
    if budget_failures > 0:
        recs.append(f"Budget-related failures: {budget_failures} ({budget_failures/sum(cat_dist.values()):.1%}) — consider dynamic budget allocation based on mission complexity")
    
    # No-progress failures
    no_progress = cat_dist.get("no_progress", 0)
    if no_progress > 0:
        recs.append(f"No-progress failures: {no_progress} — improve goal specificity and add intermediate checkpoints")
    
    # Agent lost
    agent_lost = cat_dist.get("agent_lost", 0)
    if agent_lost > 0:
        recs.append(f"Agent lost: {agent_lost} — implement heartbeat monitoring and automatic requeue")
    
    # Repo-specific issues
    high_failure_repos = sorted(repo_dist.items(), key=lambda x: x[1], reverse=True)[:3]
    for repo, cnt in high_failure_repos:
        recs.append(f"High failure repo: {repo} ({cnt} failures) — review repo-specific issues")
    
    # Model-specific issues
    high_failure_models = sorted(model_dist.items(), key=lambda x: x[1], reverse=True)[:3]
    for model, cnt in high_failure_models:
        recs.append(f"High failure model: {model} ({cnt} failures) — consider model-specific tuning")
    
    return recs


# ---------------------------------------------------------------------------
# Report generation
# ---------------------------------------------------------------------------

def generate_report_md(result: AnalysisResult) -> str:
    """Generate a paper-level markdown report."""
    md = []
    md.append("# Mission Failure Root Cause Classification & Recovery Analysis")
    md.append("")
    md.append(f"**Analysis Date**: {result.analysis_timestamp}")
    md.append(f"**Total Terminal Missions**: {result.terminal_missions}")
    md.append(f"**Failed Missions**: {result.failed_count}")
    md.append(f"**Recoverable Rate**: {result.recoverable_rate:.1%}")
    md.append("")
    
    # Section 1: Overview
    md.append("## 1. Overview")
    md.append("")
    md.append("### Terminal State Distribution")
    md.append("")
    md.append("| Status | Count | Percentage |")
    md.append("|--------|-------|------------|")
    for status, cnt in sorted(result.terminal_distribution.items(), key=lambda x: x[1], reverse=True):
        pct = cnt / result.terminal_missions * 100 if result.terminal_missions > 0 else 0
        md.append(f"| {status} | {cnt} | {pct:.1f}% |")
    md.append("")
    
    # Section 2: Failure Classification
    md.append("## 2. Failure Root Cause Classification")
    md.append("")
    md.append("### Classification Rules (v1)")
    md.append("")
    md.append("| Category | Trigger Patterns | Recoverable |")
    md.append("|----------|-----------------|-------------|")
    for cat, patterns in CATEGORY_RULES:
        recoverable = "Yes" if RECOVERABILITY.get(cat, False) else "No"
        md.append(f"| {cat} | {', '.join(patterns)} | {recoverable} |")
    md.append("")
    
    md.append("### Failure Category Distribution")
    md.append("")
    md.append("| Category | Count | Percentage | Recoverable | Recovery Rate |")
    md.append("|----------|-------|------------|-------------|---------------|")
    for cat, cnt in sorted(result.failure_categories.items(), key=lambda x: x[1], reverse=True):
        pct = cnt / result.failed_count * 100 if result.failed_count > 0 else 0
        recov = result.category_recovery.get(cat, {}).get("recoverable", 0)
        recov_rate = result.category_recovery.get(cat, {}).get("recoverable_rate", 0)
        md.append(f"| {cat} | {cnt} | {pct:.1f}% | {recov} | {recov_rate:.1%} |")
    md.append("")
    
    # Section 3: Failure by Repo
    md.append("## 3. Failure by Repository")
    md.append("")
    md.append("| Repository | Failed Count |")
    md.append("|------------|-------------|")
    for repo, cnt in sorted(result.failure_by_repo.items(), key=lambda x: x[1], reverse=True)[:10]:
        md.append(f"| {repo} | {cnt} |")
    md.append("")
    
    # Section 4: Failure by Model
    md.append("## 4. Failure by Model")
    md.append("")
    md.append("| Model | Failed Count |")
    md.append("|-------|-------------|")
    for model, cnt in sorted(result.failure_by_model.items(), key=lambda x: x[1], reverse=True)[:10]:
        md.append(f"| {model} | {cnt} |")
    md.append("")
    
    # Section 5: Recovery Analysis
    md.append("## 5. Recovery Analysis")
    md.append("")
    md.append(f"**Total Recoverable**: {result.recoverable_count} ({result.recoverable_rate:.1%})")
    md.append("")
    md.append("| Category | Count | Recoverable | Recovery Rate | Suggestion |")
    md.append("|----------|-------|-------------|---------------|------------|")
    for cat, info in sorted(result.category_recovery.items(), key=lambda x: x[1]["count"], reverse=True):
        md.append(f"| {cat} | {info['count']} | {info['recoverable']} | {info['recoverable_rate']:.1%} | {info['suggestion']} |")
    md.append("")
    
    # Section 6: Recommendations
    md.append("## 6. Re-dispatch Strategy Recommendations")
    md.append("")
    for i, rec in enumerate(result.recommendations, 1):
        md.append(f"{i}. {rec}")
    md.append("")
    
    # Section 7: Sample Missions
    md.append("## 7. Sample Failed Missions")
    md.append("")
    md.append("| ID | Repo | Model | Category | Stage | Recoverable |")
    md.append("|------|------|-------|----------|-------|-------------|")
    for s in result.sample_missions:
        md.append(f"| {s['id']} | {s['repo']} | {s['model']} | {s['category']} | {s['stage']} | {'Yes' if s['recoverable'] else 'No'} |")
    md.append("")
    
    # Section 8: Limitations
    md.append("## 8. Limitations")
    md.append("")
    md.append("- Classification is based on text pattern matching in result JSON")
    md.append("- Manual review may be needed for edge cases")
    md.append("- Recovery suggestions are heuristic and may not apply to all cases")
    md.append("- Analysis is based on historical data; real-time conditions may differ")
    md.append("")
    
    return "\n".join(md)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    """Main entry point."""
    output_dir = Path(os.getenv("OUTPUT_DIR", "artifacts"))
    output_dir.mkdir(parents=True, exist_ok=True)
    
    print("Fetching terminal missions...")
    missions = get_terminal_missions(limit=10000)
    print(f"Fetched {len(missions)} terminal missions")
    
    print("Classifying missions...")
    result = analyze_missions(missions)
    
    # Save JSON
    json_path = output_dir / "mission_failure_analysis.json"
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(asdict(result), f, indent=2, ensure_ascii=False)
    print(f"Saved JSON report to {json_path}")
    
    # Save Markdown report
    md_path = Path("docs") / "mission_failure_analysis.md"
    md_path.parent.mkdir(parents=True, exist_ok=True)
    md_content = generate_report_md(result)
    with open(md_path, "w", encoding="utf-8") as f:
        f.write(md_content)
    print(f"Saved Markdown report to {md_path}")
    
    # Print summary
    print(f"\n=== Analysis Summary ===")
    print(f"Total missions: {result.total_missions}")
    print(f"Failed: {result.failed_count}")
    print(f"Recoverable: {result.recoverable_count} ({result.recoverable_rate:.1%})")
    print(f"\nFailure categories:")
    for cat, cnt in sorted(result.failure_categories.items(), key=lambda x: x[1], reverse=True):
        pct = cnt / result.failed_count * 100 if result.failed_count > 0 else 0
        print(f"  {cat}: {cnt} ({pct:.1f}%)")
    
    print(f"\nRecommendations:")
    for rec in result.recommendations:
        print(f"  - {rec}")
    
    return result


if __name__ == "__main__":
    main()
