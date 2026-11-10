"""Seed SQLite database with synthetic campaign_nodes data for repro testing.

Creates campaign_plans, campaign_milestones, campaign_nodes tables and populates
~475 done nodes across multiple repos and physics domains for stratified sampling.
"""
from __future__ import annotations

import json
import random
import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

REPOS = [
    "TensorLBM", "TensorFEM", "TensorFVM", "TensorBEM",
    "TensorSPH", "TensorDEM", "TensorDG", "TensorCAD",
]

PHYSICS_DOMAINS = [
    "resistance", "propulsion", "seakeeping", "maneuvering",
    "stability", "structure", "vibration_noise", "green_shipping", "ice_navigation",
]

# Known-bug node IDs (negative control) — these will be marked as having known bugs
KNOWN_BUG_NODE_INDICES = [3, 17, 42, 88, 155]  # will be mapped to actual node IDs


def create_tables(conn: sqlite3.Connection) -> None:
    """Create campaign schema tables (SQLite-compatible)."""
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS organizations (
            id INTEGER PRIMARY KEY,
            name TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS factory_strategy_campaigns (
            id INTEGER PRIMARY KEY,
            name TEXT NOT NULL,
            organization_id INTEGER REFERENCES organizations(id)
        );

        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY,
            username TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS tensor_dev_missions (
            id INTEGER PRIMARY KEY,
            goal TEXT,
            repo TEXT,
            status TEXT,
            organization_id INTEGER
        );

        CREATE TABLE IF NOT EXISTS campaign_plans (
            id INTEGER PRIMARY KEY,
            organization_id INTEGER REFERENCES organizations(id),
            campaign_id INTEGER REFERENCES factory_strategy_campaigns(id),
            name TEXT NOT NULL,
            description TEXT DEFAULT '',
            sir_master_ref TEXT,
            plan_version TEXT NOT NULL DEFAULT '1.0',
            planned_start TEXT,
            planned_end TEXT,
            budget_planned TEXT NOT NULL DEFAULT '{}',
            budget_actual TEXT NOT NULL DEFAULT '{}',
            status TEXT NOT NULL DEFAULT 'draft',
            metadata TEXT NOT NULL DEFAULT '{}',
            created_by INTEGER REFERENCES users(id),
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            updated_at TEXT NOT NULL DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS campaign_milestones (
            id INTEGER PRIMARY KEY,
            plan_id INTEGER REFERENCES campaign_plans(id) ON DELETE CASCADE,
            name TEXT NOT NULL,
            description TEXT DEFAULT '',
            criteria TEXT NOT NULL DEFAULT '{}',
            dependencies TEXT DEFAULT '[]',
            planned_start TEXT,
            planned_end TEXT,
            actual_start TEXT,
            actual_end TEXT,
            status TEXT NOT NULL DEFAULT 'pending',
            progress_pct INTEGER NOT NULL DEFAULT 0,
            consecutive_fails INTEGER NOT NULL DEFAULT 0,
            escalation_id INTEGER,
            metadata TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            updated_at TEXT NOT NULL DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS campaign_nodes (
            id INTEGER PRIMARY KEY,
            plan_id INTEGER REFERENCES campaign_plans(id) ON DELETE CASCADE,
            milestone_id INTEGER REFERENCES campaign_milestones(id) ON DELETE SET NULL,
            mission_id INTEGER REFERENCES tensor_dev_missions(id) ON DELETE SET NULL,
            name TEXT NOT NULL,
            node_type TEXT NOT NULL DEFAULT 'task',
            dependencies TEXT DEFAULT '[]',
            planned_start TEXT,
            planned_end TEXT,
            actual_start TEXT,
            actual_end TEXT,
            status TEXT NOT NULL DEFAULT 'pending',
            reproduction_verdict TEXT,
            consecutive_fails INTEGER NOT NULL DEFAULT 0,
            block_reason TEXT,
            metadata TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            updated_at TEXT NOT NULL DEFAULT (datetime('now'))
        );

        CREATE INDEX IF NOT EXISTS ix_campaign_nodes_plan ON campaign_nodes(plan_id, status);
        CREATE INDEX IF NOT EXISTS ix_campaign_nodes_mission ON campaign_nodes(mission_id);
    """)


def seed_data(conn: sqlite3.Connection, n_done: int = 475, seed: int = 42) -> dict:
    """Seed synthetic campaign data.

    Returns dict with node_id -> metadata mapping for verification.
    """
    random.seed(seed)
    cur = conn.cursor()

    # Create org, campaign, user
    cur.execute("INSERT INTO organizations (id, name) VALUES (1, 'default')")
    cur.execute("INSERT INTO factory_strategy_campaigns (id, name, organization_id) VALUES (71, 'ice_navigation_full', 1)")
    cur.execute("INSERT INTO users (id, username) VALUES (1, 'system')")

    # Create a plan
    cur.execute("""
        INSERT INTO campaign_plans (id, organization_id, campaign_id, name, description,
                                     plan_version, status, metadata, created_by)
        VALUES (1, 1, 71, 'P71 Ice Navigation Full Plan', 'Full campaign for ice navigation',
                '1.0', 'active', '{}', 1)
    """)

    # Create milestones
    for i, ms_name in enumerate(["Resistance", "Propulsion", "Seakeeping", "Structure", "Ice"], 1):
        cur.execute("""
            INSERT INTO campaign_milestones (id, plan_id, name, status, progress_pct, metadata)
            VALUES (?, 1, ?, 'done', 100, '{}')
        """, (i, ms_name))

    # Generate ~475 done nodes + some non-done nodes
    node_id = 1
    node_meta = {}
    done_count = 0
    target_done = n_done

    # Distribute across repos and domains
    repo_domain_pairs = []
    for repo in REPOS:
        for domain in PHYSICS_DOMAINS:
            repo_domain_pairs.append((repo, domain))

    random.shuffle(repo_domain_pairs)

    # Create done nodes
    for idx in range(target_done):
        repo, domain = repo_domain_pairs[idx % len(repo_domain_pairs)]
        milestone_id = (idx % 5) + 1
        mission_id = 1000 + idx

        # Create mission
        cur.execute("""
            INSERT INTO tensor_dev_missions (id, goal, repo, status, organization_id)
            VALUES (?, ?, ?, 'succeeded', 1)
        """, (mission_id, f"Validate {domain} in {repo}", repo))

        # Node metadata with goal/acceptance_criteria/repo
        meta = {
            "goal": f"Verify {domain} calculation in {repo}",
            "acceptance_criteria": {
                "metric": "resistance_coefficient",
                "threshold": 0.03,
                "reference_value": round(random.uniform(0.001, 0.01), 6),
            },
            "repo": repo,
            "physics_domain": domain,
            "test_command": f"pytest tests/test_{domain}.py -v",
            "original_verdict": "pass",
            "original_date": (datetime(2026, 10, 1, tzinfo=timezone.utc) + timedelta(days=random.randint(0, 30))).isoformat(),
        }

        # Some nodes have known bugs (negative control)
        is_known_bug = idx in KNOWN_BUG_NODE_INDICES
        if is_known_bug:
            meta["known_bug"] = True
            meta["bug_description"] = f"Known numerical instability in {domain} solver"

        cur.execute("""
            INSERT INTO campaign_nodes (id, plan_id, milestone_id, mission_id, name,
                                         node_type, status, reproduction_verdict,
                                         consecutive_fails, metadata)
            VALUES (?, 1, ?, ?, ?, 'task', 'done', 'pass', 0, ?)
        """, (node_id, milestone_id, mission_id, f"N{node_id:04d}_{domain}", json.dumps(meta)))

        node_meta[node_id] = meta
        done_count += 1
        node_id += 1

    # Add some non-done nodes (pending, in-flight, failed)
    for status in ["pending", "in-flight", "failed", "blocked"]:
        for _ in range(5):
            repo, domain = random.choice(repo_domain_pairs)
            meta = {
                "goal": f"Verify {domain} in {repo}",
                "acceptance_criteria": {"metric": "dummy", "threshold": 0.05},
                "repo": repo,
                "physics_domain": domain,
            }
            cur.execute("""
                INSERT INTO campaign_nodes (id, plan_id, name, node_type, status, metadata)
                VALUES (?, 1, ?, 'task', ?, ?)
            """, (node_id, f"N{node_id:04d}_{domain}", status, json.dumps(meta)))
            node_id += 1

    conn.commit()
    return {
        "total_done": done_count,
        "total_nodes": node_id - 1,
        "known_bug_node_ids": [idx + 1 for idx in KNOWN_BUG_NODE_INDICES],  # 1-based
        "repos": REPOS,
        "domains": PHYSICS_DOMAINS,
    }


def main():
    db_path = sys.argv[1] if len(sys.argv) > 1 else "backend/foundation_ai.db"
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(db_path)
    try:
        create_tables(conn)
        info = seed_data(conn)
        print(f"Seeded {info['total_done']} done nodes, {info['total_nodes']} total nodes")
        print(f"Repos: {info['repos']}")
        print(f"Domains: {info['domains']}")
        print(f"Known bug node IDs (negative control): {info['known_bug_node_ids']}")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
