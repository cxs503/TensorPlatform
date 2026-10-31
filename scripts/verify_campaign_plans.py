#!/usr/bin/env python3
"""Verify campaign_plans data in the database: machine criteria checks."""
import json
import os
import subprocess

DB_ARGS = ["psql", "-h", "localhost", "-U", "foundation", "-d", "tensorplatform", "-t", "-A"]
ENV = {**os.environ, "PGPASSWORD": "foundation123"}


def run_sql(sql):
    r = subprocess.run(DB_ARGS, input=sql, capture_output=True, text=True, env=ENV)
    return r.stdout.strip()


def main():
    print("=" * 60)
    print("Campaign Plans Verification Report")
    print("=" * 60)

    # 1. All phases returned
    phases = run_sql("SELECT milestone_id, name, jsonb_array_length(gate_criteria) FROM campaign_plan_milestones ORDER BY milestone_id")
    print("\n1. Phases (milestones) with criteria counts:")
    for line in phases.split("\n"):
        if line:
            parts = line.split("|")
            print(f"   {parts[0]}: {parts[1]} ({parts[2]} gate criteria)")

    # 2. All criteria fields non-empty
    empty_phases = run_sql("SELECT count(*) FROM campaign_plan_milestones WHERE gate_criteria = '[]'::jsonb")
    empty_nodes = run_sql("SELECT count(*) FROM campaign_plan_nodes WHERE acceptance_criteria = '[]'::jsonb")
    print(f"\n2. Criteria non-empty check:")
    print(f"   Phases with empty gate_criteria: {empty_phases}")
    print(f"   Nodes with empty acceptance_criteria: {empty_nodes}")
    criteria_ok = empty_phases == "0" and empty_nodes == "0"
    print(f"   PASS: {criteria_ok}")

    # 3. Progress endpoint numbers
    total_nodes = run_sql("SELECT count(*) FROM campaign_plan_nodes")
    total_criteria = run_sql("SELECT sum(jsonb_array_length(acceptance_criteria)) FROM campaign_plan_nodes")
    total_budget = run_sql("SELECT sum(budget_hours) FROM campaign_plan_nodes")
    dag_edges = run_sql("SELECT count(*) FROM campaign_plan_dag_edges")
    print(f"\n3. Progress numbers:")
    print(f"   total_nodes: {total_nodes}")
    print(f"   total_criteria: {total_criteria}")
    print(f"   total_budget_hours: {total_budget}")
    print(f"   dag_edges: {dag_edges}")
    progress_ok = all(x not in ("0", "", "None") for x in [total_nodes, total_criteria, total_budget, dag_edges])
    print(f"   PASS: {progress_ok}")

    # 4. Milestone breakdown
    print(f"\n4. Milestone breakdown:")
    ms_data = run_sql("""
        SELECT milestone, count(*) as nodes, sum(jsonb_array_length(acceptance_criteria)) as criteria, sum(budget_hours) as budget
        FROM campaign_plan_nodes GROUP BY milestone ORDER BY milestone
    """)
    for line in ms_data.split("\n"):
        if line:
            parts = line.split("|")
            print(f"   {parts[0]}: {parts[1]} nodes, {parts[2]} criteria, {parts[3]}h budget")

    # 5. Risk distribution
    print(f"\n5. Risk distribution:")
    risk_data = run_sql("SELECT risk, count(*) FROM campaign_plan_nodes GROUP BY risk ORDER BY risk")
    for line in risk_data.split("\n"):
        if line:
            print(f"   {line.replace('|', ': ')}")

    # 6. Layer distribution
    print(f"\n6. Layer distribution:")
    layer_data = run_sql("SELECT layer, count(*) FROM campaign_plan_nodes GROUP BY layer ORDER BY layer")
    for line in layer_data.split("\n"):
        if line:
            print(f"   {line.replace('|', ': ')}")

    print(f"\n{'=' * 60}")
    all_pass = criteria_ok and progress_ok
    print(f"OVERALL: {'PASS' if all_pass else 'FAIL'}")
    print(f"{'=' * 60}")


if __name__ == "__main__":
    main()
