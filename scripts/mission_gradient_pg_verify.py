#!/usr/bin/env python3
"""Execute migration 103 against a real PostgreSQL scratch database (REAL evidence).

Text assertions on a .sql file prove nothing about CHECK constraints, unique
indexes, triggers or view arithmetic.  This script creates a throwaway database,
builds the minimal prerequisite schema, applies ``103_mission_gradient.sql``
*twice* (idempotency), and then probes every piece of the mechanism's DDL
contract, including the three "must refuse" cases:

  * ``honest_fail=TRUE`` without root cause + retrospective -> rejected
  * exploration budget on S1..S3                            -> rejected
  * mix weights not summing to 1                            -> rejected
  * UPDATE/DELETE on the S-N track                          -> rejected
  * second ``active`` mix row                               -> rejected

plus the view arithmetic (disciplined vs naive failure rate).

Requires a local PostgreSQL reachable as ``psql -U postgres -h <socket>``.
Skips (exit 0, ``skipped: true``) when no server is available.

Usage: python3 scripts/mission_gradient_pg_verify.py [--keep]
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
MIGRATION = REPO_ROOT / "backend" / "migrations" / "103_mission_gradient.sql"
OUT = REPO_ROOT / "artifacts" / "mission_gradient"
DB = "m5026_grad_verify"
SOCKET_DIRS = ["/var/run/postgresql", "/tmp"]
ADMIN_DB = "postgres"

PREREQ_SQL = """
CREATE TABLE IF NOT EXISTS organizations (id BIGSERIAL PRIMARY KEY, name TEXT);
CREATE TABLE IF NOT EXISTS tensor_dev_missions (
    id              BIGSERIAL PRIMARY KEY,
    organization_id BIGINT NOT NULL REFERENCES organizations(id),
    goal            TEXT NOT NULL DEFAULT 'fixture mission',
    status          VARCHAR(24) NOT NULL DEFAULT 'queued',
    claimed_by      VARCHAR(120),
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
"""


def _sock() -> str | None:
    for d in SOCKET_DIRS:
        if os.path.isdir(d):
            return d
    return None


def psql(sql: str | None = None, *, db: str = DB, file: Path | None = None) -> tuple[int, str]:
    sock = _sock()
    cmd = ["psql", "-U", "postgres", "-X", "-q", "-t", "-A", "-v", "ON_ERROR_STOP=1", "-d", db]
    if sock:
        cmd += ["-h", sock]
    if file is not None:
        cmd += ["-f", str(file)]
    else:
        cmd += ["-c", sql or ""]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
    return proc.returncode, (proc.stdout or "").strip() + (("\n" + proc.stderr.strip()) if proc.stderr.strip() else "")


def scalar(sql: str, db: str = DB) -> str:
    code, out = psql(sql, db=db)
    if code != 0:
        raise RuntimeError(f"query failed: {sql}\n{out}")
    return out.strip().splitlines()[-1] if out.strip() else ""


def expect_error(sql: str, *, label: str, contains: str | None = None) -> dict:
    code, out = psql(sql)
    ok = code != 0 and (contains is None or contains in out)
    return {"check": label, "passed": ok, "exit_code": code,
            "message": out.splitlines()[0][:220] if out else ""}


def expect_ok(sql: str, *, label: str) -> dict:
    code, out = psql(sql)
    return {"check": label, "passed": code == 0, "exit_code": code,
            "message": out.splitlines()[0][:220] if out else ""}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--keep", action="store_true", help="keep the scratch database")
    args = ap.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)

    probe_code, probe = psql("SELECT 1", db=ADMIN_DB)
    if probe_code != 0:
        result = {"skipped": True, "reason": f"no local PostgreSQL: {probe[:200]}"}
        (OUT / "pg_verify.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
        print(json.dumps(result))
        return 0

    psql(f"DROP DATABASE IF EXISTS {DB}", db=ADMIN_DB)
    code, out = psql(f"CREATE DATABASE {DB}", db=ADMIN_DB)
    if code != 0:
        raise RuntimeError(f"cannot create scratch db: {out}")

    checks: list[dict] = []
    env: dict = {}
    try:
        code, out = psql(PREREQ_SQL)
        if code != 0:
            raise RuntimeError(f"prerequisite schema failed: {out}")

        # apply twice: the migration must be idempotent
        first = psql(file=MIGRATION)
        second = psql(file=MIGRATION)
        if first[0] != 0:
            # a failed migration aborts its own transaction: everything downstream
            # would be a meaningless "false", so stop right here
            raise RuntimeError(f"migration 103 failed on a real server:\n{first[1]}")
        checks.append({"check": "migration_applies", "passed": first[0] == 0,
                       "exit_code": first[0], "message": (first[1] or "")[:220]})
        checks.append({"check": "migration_is_idempotent", "passed": second[0] == 0,
                       "exit_code": second[0], "message": (second[1] or "")[:220]})

        cols = scalar("""
            SELECT string_agg(column_name, ',' ORDER BY column_name)
              FROM information_schema.columns
             WHERE table_name = 'tensor_dev_missions'
               AND column_name IN ('gradient_tier','exploration_budget','gradient_self_declared',
                                   'honest_fail','fail_root_cause','retrospective',
                                   'negative_result_ref')
        """)
        env["gradient_columns"] = cols
        checks.append({"check": "gradient_columns_present", "passed": cols.count(",") == 6,
                       "detail": cols})

        tables = scalar("""
            SELECT string_agg(table_name, ',' ORDER BY table_name)
              FROM information_schema.tables
             WHERE table_schema='public'
               AND table_name IN ('mission_gradient_mix','worker_credit_tiers',
                                  'mission_negative_results','worker_failure_rate_v1')
        """)
        env["objects"] = tables
        checks.append({"check": "registry_and_track_objects_present",
                       "passed": tables.count(",") == 3, "detail": tables})

        psql("INSERT INTO organizations (id, name) VALUES (1, 'fixture-org');")

        # ── must-refuse cases ──
        checks.append(expect_error(
            "INSERT INTO tensor_dev_missions (organization_id, gradient_tier, honest_fail, status) "
            "VALUES (1, 'S4', TRUE, 'failed');",
            label="refuse_honest_fail_without_evidence", contains="ck_tensor_dev_missions_honest_fail_evidence"))
        checks.append(expect_error(
            "INSERT INTO tensor_dev_missions (organization_id, gradient_tier, exploration_budget) "
            "VALUES (1, 'S2', '{\"exploration\": true}'::jsonb);",
            label="refuse_exploration_budget_outside_s4_s5", contains="ck_tensor_dev_missions_exploration_scope"))
        checks.append(expect_error(
            "INSERT INTO tensor_dev_missions (organization_id, gradient_tier) VALUES (1, 'S9');",
            label="refuse_unknown_gradient_tier", contains="ck_tensor_dev_missions_gradient_tier"))
        checks.append(expect_error(
            "INSERT INTO mission_gradient_mix (version, mix, signature) "
            "VALUES (1, '{\"S1\":0.6,\"S2\":0.25,\"S3\":0.1,\"S4\":0.04,\"S5\":0.02}'::jsonb, 'deadbeefdeadbeef');",
            label="refuse_mix_not_summing_to_one", contains="ck_mission_gradient_mix_sum"))
        checks.append(expect_ok(
            "INSERT INTO mission_gradient_mix (version, mix, signature) "
            "VALUES (1, '{\"S1\":0.6,\"S2\":0.25,\"S3\":0.1,\"S4\":0.04,\"S5\":0.01}'::jsonb, 'c0ffee00c0ffee00');",
            label="accept_registered_start_mix"))
        checks.append(expect_error(
            "INSERT INTO mission_gradient_mix (version, mix, signature) "
            "VALUES (2, '{\"S1\":0.5,\"S2\":0.3,\"S3\":0.15,\"S4\":0.04,\"S5\":0.01}'::jsonb, 'beefbeefbeefbeef');",
            label="refuse_second_active_mix"))
        checks.append(expect_error(
            "INSERT INTO worker_credit_tiers (worker_id, credit_level) VALUES ('w-alpha-1', 6);",
            label="refuse_credit_level_out_of_range", contains="ck_worker_credit_tiers_level"))
        checks.append(expect_ok(
            "INSERT INTO worker_credit_tiers (worker_id, credit_level, rationale) "
            "VALUES ('w-alpha-1', 4, 'fixture') "
            "ON CONFLICT ON CONSTRAINT uq_worker_credit_tiers_worker "
            "DO UPDATE SET credit_level = EXCLUDED.credit_level, rationale = EXCLUDED.rationale;",
            label="api_upsert_constraint_name_is_valid"))

        # ── view arithmetic: 1 success + 1 ordinary failure + 1 documented honest FAIL ──
        psql("""
            INSERT INTO tensor_dev_missions (organization_id, status, claimed_by, gradient_tier)
            VALUES (1, 'succeeded', 'w-alpha-1', 'S1'),
                   (1, 'failed',    'w-alpha-1', 'S2'),
                   (1, 'failed',    'w-alpha-1', 'S4');
            UPDATE tensor_dev_missions
               SET honest_fail = TRUE,
                   fail_root_cause = 'dom-alpha probe diverged from the reference trace',
                   retrospective = 'Cause: fixture scale mismatch; validate fixture size before the run.'
             WHERE gradient_tier = 'S4';
        """)
        row = scalar("""
            SELECT attempts || '|' || successes || '|' || failures || '|' ||
                   quarantined_honest_fails || '|' || round(disciplined_failure_rate, 4) || '|' ||
                   round(naive_failure_rate, 4)
              FROM worker_failure_rate_v1 WHERE worker_id = 'w-alpha-1'
        """)
        env["failure_rate_view"] = row
        parts = row.split("|")
        checks.append({
            "check": "view_quarantines_honest_fail",
            "passed": (len(parts) == 6 and parts[0] == "3" and parts[2] == "1"
                       and parts[3] == "1" and parts[4] == "0.5000" and parts[5] == "0.6667"),
            "detail": row,
        })

        nid = scalar("""
            INSERT INTO mission_negative_results
                (organization_id, mission_id, tier, root_cause, retrospective, evidence_ref, reuse_tags)
            SELECT 1, id, 'S4', fail_root_cause, retrospective, 'artifact://fixture/run-1',
                   ARRAY['fixture-scale','probe-design']
              FROM tensor_dev_missions WHERE honest_fail
            RETURNING id
        """)
        env["negative_result_id"] = nid
        checks.append({"check": "negative_result_inserted", "passed": bool(nid), "detail": nid})
        checks.append(expect_error(
            f"UPDATE mission_negative_results SET root_cause = 'rewritten' WHERE id = {nid};",
            label="refuse_update_negative_result", contains="append-only"))
        checks.append(expect_error(
            f"DELETE FROM mission_negative_results WHERE id = {nid};",
            label="refuse_delete_negative_result", contains="append-only"))
        checks.append(expect_error(
            "INSERT INTO mission_negative_results (organization_id, mission_id, tier, root_cause, retrospective) "
            "SELECT 1, id, 'S1', 'short text here', 'short retrospective text' FROM tensor_dev_missions LIMIT 1;",
            label="refuse_negative_result_on_non_exploration_tier",
            contains="ck_mission_negative_results_tier"))
    finally:
        if not args.keep:
            psql(f"DROP DATABASE IF EXISTS {DB}", db=ADMIN_DB)

    passed = sum(1 for c in checks if c["passed"])
    result = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "database": f"PostgreSQL scratch db '{DB}' (dropped after verification)"
                    if not args.keep else DB,
        "migration": "backend/migrations/103_mission_gradient.sql",
        "checks_total": len(checks),
        "checks_passed": passed,
        "all_passed": passed == len(checks),
        "checks": checks,
        "environment": env,
    }
    (OUT / "pg_verify.json").write_text(json.dumps(result, indent=2, ensure_ascii=False),
                                        encoding="utf-8")
    print(json.dumps({k: result[k] for k in ("checks_total", "checks_passed", "all_passed")}))
    for c in checks:
        if not c["passed"]:
            print("FAILED:", c["check"], "->", c.get("message") or c.get("detail"))
    return 0 if result["all_passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())