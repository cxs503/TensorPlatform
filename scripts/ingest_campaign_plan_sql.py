#!/usr/bin/env python3
"""Ingest master_plan.json into campaign_plans tables via direct SQL."""
import json
import os
import subprocess
import re

PLAN_PATH = "/srv/tensor/agent/work/m611/artifacts/master_plan.json"
DB_ARGS = ["psql", "-h", "localhost", "-U", "foundation", "-d", "tensorplatform", "-t", "-A"]
ENV = {**os.environ, "PGPASSWORD": "foundation123"}


def run_sql(sql):
    r = subprocess.run(DB_ARGS, input=sql, capture_output=True, text=True, env=ENV)
    if r.returncode != 0:
        print(f"SQL ERROR: {r.stderr}")
    return r.stdout.strip()


def main():
    with open(PLAN_PATH) as f:
        plan = json.load(f)

    # Check existing
    existing = run_sql("SELECT id FROM campaign_plans WHERE source_mission_id = 'm611'")
    if existing:
        plan_id = int(existing)
        print(f"Plan exists: id={plan_id}, clearing children...")
        run_sql(f"DELETE FROM campaign_plan_dag_edges WHERE campaign_plan_id = {plan_id}")
        run_sql(f"DELETE FROM campaign_plan_nodes WHERE campaign_plan_id = {plan_id}")
        run_sql(f"DELETE FROM campaign_plan_milestones WHERE campaign_plan_id = {plan_id}")
    else:
        ps = json.dumps(plan.get("physics_scope", []), ensure_ascii=False)
        cf = json.dumps(plan.get("coupling_framework_requirements", []), ensure_ascii=False)
        vs = json.dumps(plan.get("validation_standards", []), ensure_ascii=False)
        stats = json.dumps(plan.get("statistics", {}), ensure_ascii=False)
        title = plan.get("title", "").replace("'", "''")
        owner = plan.get("owner", "")

        result = run_sql(f"""INSERT INTO campaign_plans 
            (organization_id, source_mission_id, schema_version, title, owner, physics_scope, coupling_framework_requirements, validation_standards, statistics)
            VALUES (1, 'm611', '{plan["schema_version"]}', '{title}', '{owner}', '{ps}'::jsonb, '{cf}'::jsonb, '{vs}'::jsonb, '{stats}'::jsonb)
            RETURNING id""")
        plan_id = int(result)
        print(f"Inserted plan: id={plan_id}")

    # Insert milestones
    for mid, mdata in plan.get("milestones", {}).items():
        gc = json.dumps(mdata.get("gate_criteria", []), ensure_ascii=False).replace("'", "''")
        name = mdata.get("name", mid).replace("'", "''")
        desc = mdata.get("description", "").replace("'", "''")
        td = mdata.get("target_date", "")
        status = mdata.get("status", "planned")
        run_sql(f"""INSERT INTO campaign_plan_milestones 
            (campaign_plan_id, milestone_id, name, description, gate_criteria, target_date, status)
            VALUES ({plan_id}, '{mid}', '{name}', '{desc}', '{gc}'::jsonb, '{td}', '{status}')
            ON CONFLICT (campaign_plan_id, milestone_id) DO UPDATE SET
                name = EXCLUDED.name, description = EXCLUDED.description, 
                gate_criteria = EXCLUDED.gate_criteria, target_date = EXCLUDED.target_date, status = EXCLUDED.status""")
        print(f"  Milestone {mid}: OK")

    # Insert nodes
    for node in plan.get("nodes", []):
        ac = json.dumps(node.get("acceptance_criteria", []), ensure_ascii=False).replace("'", "''")
        dep = json.dumps(node.get("depends_on", []), ensure_ascii=False).replace("'", "''")
        title = node.get("title", "").replace("'", "''")
        goal = node.get("goal", "").replace("'", "''")
        run_sql(f"""INSERT INTO campaign_plan_nodes 
            (campaign_plan_id, node_id, source, layer, title, goal, acceptance_criteria, depends_on, milestone, budget_hours, risk, physics_domain, status)
            VALUES ({plan_id}, '{node["id"]}', '{node.get("source","")}', '{node.get("layer","")}', '{title}', '{goal}', '{ac}'::jsonb, '{dep}'::jsonb, '{node.get("milestone","")}', {node.get("budget_hours",0)}, '{node.get("risk","medium")}', '{node.get("physics_domain","")}', '{node.get("status","planned")}')
            ON CONFLICT (campaign_plan_id, node_id) DO UPDATE SET
                source = EXCLUDED.source, layer = EXCLUDED.layer, title = EXCLUDED.title, goal = EXCLUDED.goal,
                acceptance_criteria = EXCLUDED.acceptance_criteria, depends_on = EXCLUDED.depends_on,
                milestone = EXCLUDED.milestone, budget_hours = EXCLUDED.budget_hours, risk = EXCLUDED.risk, 
                physics_domain = EXCLUDED.physics_domain, status = EXCLUDED.status""")
        print(f"  Node {node['id']}: OK")

    # Insert DAG edges
    for edge in plan.get("dag_edges", []):
        run_sql(f"""INSERT INTO campaign_plan_dag_edges (campaign_plan_id, from_node, to_node)
            VALUES ({plan_id}, '{edge["from"]}', '{edge["to"]}') ON CONFLICT DO NOTHING""")

    print(f"\nIngestion complete: plan_id={plan_id}")

    # Verify
    for table in ["campaign_plans", "campaign_plan_milestones", "campaign_plan_nodes", "campaign_plan_dag_edges"]:
        count = run_sql(f"SELECT count(*) FROM {table}")
        print(f"  {table}: {count} rows")


if __name__ == "__main__":
    main()
