"""Data extraction and feature engineering for task loss risk calibration.

All features must be knowable at dispatch time (no post-hoc information).
Label: agent_lost = (status='failed' AND fail_stage='agent_lost').
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd
from sqlalchemy import create_engine, text


# ---------------------------------------------------------------------------
# SQL query — extracts all dispatch-time features + terminal outcome
# ---------------------------------------------------------------------------
MISSION_EXTRACT_SQL = """
SELECT
    m.id,
    m.repo,
    m.base_ref,
    m.goal,
    m.parent_mission_id,
    m.status,
    m.fail_stage,
    m.model,
    m.worker_role,
    m.budget,
    m.created_at,
    m.finished_at,
    m.campaign_id,
    m.campaign_phase,
    -- derived features (computed in Python for portability)
    NULL AS goal_word_count,
    NULL AS criteria_count,
    NULL AS parent_depth,
    NULL AS repo_historical_loss_rate,
    NULL AS concurrency_level,
    NULL AS is_mature_single,
    NULL AS budget_max_tool_calls,
    NULL AS budget_max_wall_seconds,
    NULL AS budget_max_tokens,
    NULL AS label_agent_lost
FROM tensor_dev_missions m
WHERE m.status IN ('succeeded', 'failed', 'stopped', 'revoked')
   OR m.fail_stage IS NOT NULL
ORDER BY m.created_at ASC
"""


def _compute_goal_word_count(goal: str) -> int:
    """Count whitespace-separated tokens in goal."""
    return len(goal.split())


def _count_criteria(goal: str) -> int:
    """Heuristic: count bullet/numbered items that look like acceptance criteria.
    Matches lines starting with '- ', '* ', or 'N. ' patterns.
    """
    import re
    # Match common criterion markers: "- ", "* ", "1. ", "(a) ", etc.
    patterns = [
        r'^\s*[-*]\s+',       # bullet points
        r'^\s*\d+[.)]\s+',    # numbered items
        r'^\s*\([a-zA-Z0-9]+\)\s+',  # lettered items
    ]
    count = 0
    for line in goal.split('\n'):
        for pat in patterns:
            if re.match(pat, line):
                count += 1
                break
    return count


def _compute_parent_depth(parent_id: int | None, parent_map: dict[int, int | None]) -> int:
    """Compute tree depth from root (root=0)."""
    if parent_id is None:
        return 0
    depth = 0
    visited = set()
    current = parent_id
    while current is not None and current not in visited:
        visited.add(current)
        depth += 1
        current = parent_map.get(current)
    return depth


def _is_mature_single(goal: str) -> bool:
    """Heuristic: detect 'mature single' missions (承接/成熟化 keywords)."""
    mature_keywords = ['成熟化', '承接', 'mature', 'single', 'maturity']
    goal_lower = goal.lower()
    return any(kw in goal_lower for kw in mature_keywords)


def extract_mission_dataset(
    db_url: str,
    output_dir: str | Path = "artifacts/m9912",
) -> dict[str, Any]:
    """Extract mission data, engineer features, and save with provenance.

    Returns dict with:
      - df: pandas DataFrame with features + label
      - sha256: hex digest of the CSV
      - sql_used: the SQL query string
      - n_missions: total rows
      - n_lost: number of agent_lost labels
      - t_split: time split point
      - csv_path: path to saved CSV
    """
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    engine = create_engine(db_url)

    # Fetch raw data
    with engine.connect() as conn:
        df = pd.read_sql(text(MISSION_EXTRACT_SQL), conn)

    if df.empty:
        # Return empty dataset with provenance
        result = {
            "df": pd.DataFrame(),
            "sha256": hashlib.sha256(b"").hexdigest(),
            "sql_used": MISSION_EXTRACT_SQL,
            "n_missions": 0,
            "n_lost": 0,
            "t_split": None,
            "csv_path": str(output_dir / "mission_dataset.csv"),
        }
        return result

    # Build parent map for depth computation
    parent_map = dict(zip(df["id"], df["parent_mission_id"]))

    # Compute repo-level historical loss rate (per repo, up to each mission's created_at)
    # This is a rolling feature: for each mission, compute loss rate of same-repo
    # missions created BEFORE it.
    df["created_at"] = pd.to_datetime(df["created_at"])
    df = df.sort_values("created_at").reset_index(drop=True)

    # Pre-compute label
    df["label_agent_lost"] = (
        (df["status"] == "failed") & (df["fail_stage"] == "agent_lost")
    ).astype(int)

    # Compute features
    df["goal_word_count"] = df["goal"].apply(_compute_goal_word_count)
    df["criteria_count"] = df["goal"].apply(_count_criteria)
    df["parent_depth"] = df.apply(
        lambda r: _compute_parent_depth(r["parent_mission_id"], parent_map), axis=1
    )
    df["is_mature_single"] = df["goal"].apply(_is_mature_single).astype(int)

    # Budget features
    def _parse_budget(b):
        if isinstance(b, str):
            try:
                b = json.loads(b)
            except (json.JSONDecodeError, TypeError):
                b = {}
        if not isinstance(b, dict):
            b = {}
        return {
            "budget_max_tool_calls": b.get("max_tool_calls", 80),
            "budget_max_wall_seconds": b.get("max_wall_seconds", 7200),
            "budget_max_tokens": b.get("max_tokens", 3_000_000),
        }

    budget_cols = df["budget"].apply(_parse_budget).apply(pd.Series)
    df = pd.concat([df, budget_cols], axis=1)

    # Repo historical loss rate (rolling, excluding current mission)
    repo_loss_rates = []
    repo_cumulative = {}  # repo -> {"total": int, "lost": int}
    for idx, row in df.iterrows():
        repo = row["repo"]
        if repo not in repo_cumulative:
            repo_cumulative[repo] = {"total": 0, "lost": 0}
        stats = repo_cumulative[repo]
        rate = stats["lost"] / stats["total"] if stats["total"] > 0 else 0.0
        repo_loss_rates.append(rate)
        # Update cumulative after computing rate for current mission
        stats["total"] += 1
        stats["lost"] += row["label_agent_lost"]
    df["repo_historical_loss_rate"] = repo_loss_rates

    # Concurrency level: number of missions created in the same 24h window
    df["created_date"] = df["created_at"].dt.date
    concurrency = df.groupby("created_date")["id"].transform("count")
    df["concurrency_level"] = concurrency

    # Model encoding (one-hot for common models, else 'other')
    common_models = df["model"].value_counts()
    top_models = common_models[common_models >= 5].index.tolist()
    df["model_encoded"] = df["model"].apply(
        lambda m: m if m in top_models else "other"
    )

    # Select feature columns
    feature_cols = [
        "goal_word_count",
        "criteria_count",
        "parent_depth",
        "repo_historical_loss_rate",
        "concurrency_level",
        "is_mature_single",
        "budget_max_tool_calls",
        "budget_max_wall_seconds",
        "budget_max_tokens",
    ]

    # Encode categorical features
    df["repo_encoded"] = df["repo"].astype("category").cat.codes
    df["model_encoded"] = df["model_encoded"].astype("category").cat.codes

    # Build final feature DataFrame
    feature_df = df[feature_cols + ["repo_encoded", "model_encoded", "label_agent_lost", "created_at", "id", "repo", "status", "fail_stage"]].copy()

    # Time split: use median created_at as split point
    t_split = df["created_at"].median()
    feature_df["is_test"] = feature_df["created_at"] >= t_split

    # Save CSV
    csv_path = output_dir / "mission_dataset.csv"
    feature_df.to_csv(csv_path, index=False)

    # Compute sha256
    sha256 = hashlib.sha256(feature_df.to_csv(index=False).encode()).hexdigest()

    # Save provenance
    provenance = {
        "sha256": sha256,
        "sql_used": MISSION_EXTRACT_SQL,
        "n_missions": len(feature_df),
        "n_lost": int(feature_df["label_agent_lost"].sum()),
        "loss_rate": float(feature_df["label_agent_lost"].mean()),
        "t_split": str(t_split),
        "feature_cols": feature_cols + ["repo_encoded", "model_encoded"],
        "csv_path": str(csv_path),
        "extracted_at": datetime.now(timezone.utc).isoformat(),
    }
    prov_path = output_dir / "data_provenance.json"
    with open(prov_path, "w") as f:
        json.dump(provenance, f, indent=2, default=str)

    return {
        "df": feature_df,
        "sha256": sha256,
        "sql_used": MISSION_EXTRACT_SQL,
        "n_missions": len(feature_df),
        "n_lost": int(feature_df["label_agent_lost"].sum()),
        "t_split": t_split,
        "csv_path": str(csv_path),
        "provenance": provenance,
    }


if __name__ == "__main__":
    import os
    db_url = os.environ.get("DATABASE_URL", "sqlite:///backend/foundation_ai.db")
    result = extract_mission_dataset(db_url)
    print(f"Extracted {result['n_missions']} missions, {result['n_lost']} lost")
    print(f"SHA256: {result['sha256']}")
    print(f"Time split: {result['t_split']}")
