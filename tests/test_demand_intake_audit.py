#!/usr/bin/env python3
"""事后抽样审计（``tools/demand_intake/audit.py``）的测试 —— 合成夹具 + 真 PG 红队。

纪律：**零领域词条** —— 夹具 slug 一律 ``FEAT-SYNTH-*``，标题/验收是合成句子。

覆盖七面：
1. 判据**只有一套**（审计复用 gate，仅把验收字符下限 4→8，不复制规则）；
2. 迁移文件与 ``store.audit_ddl`` **同形状**（防「测试绿、生产缺列」漂移）；
3. CLI ``audit`` 子命令已注册且缺省 dry-run；
4. 真 PG：dry-run 零写；
5. 真 PG：过审判 pass 且锚保持 open；
6. 真 PG：不过审 → 锚 dropped + **只撤未终态单**（终态不倒改）；
7. 真 PG：日帽限量且 ``uncovered`` 如实报出、重跑幂等（审过的行不再抽中）。
"""

from __future__ import annotations

import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tools.demand_intake import audit as A, cli, gate as G, store as S  # noqa: E402

GOOD_ACC = "pytest 全绿且覆盖率 >90%"          # 20 字符：事前门(4)/事后审计(8)都过
THIN_ACC = "偏差 <3%"                          # 6 字符：事前门放行，事后审计判死
SLUG = "FEAT-SYNTH-ALPHA"
SCRATCH = "m17298_audit_scratch"
MIGRATION = REPO_ROOT / "backend" / "migrations" / "124_demand_intake_audit.sql"


# ── 1. 判据只有一套（不复制规则） ───────────────────────────────────────────
def test_audit_reuses_gate_criteria_with_stricter_threshold():
    cand = {"slug": SLUG, "title": "合成锚甲", "acceptance": THIN_ACC}
    gate_verdict = G.evaluate(cand, [])
    audit_verdict = G.evaluate(cand, [], min_acceptance_chars=S.AUDIT_MIN_ACCEPTANCE_CHARS)
    # 事前门放行、事后审计判死：唯一差别是长度门槛，不是两套规则
    assert gate_verdict.ok and gate_verdict.reasons == []
    assert not audit_verdict.ok
    assert audit_verdict.reasons == [f"acceptance_too_short({len(THIN_ACC)})"]
    # 门槛没把默认行为改掉（v1 gate 仍是 4）
    assert G.MIN_ACCEPTANCE_CHARS == 4 and S.AUDIT_MIN_ACCEPTANCE_CHARS == 8
    # 合规验收两种口径都过
    ok = {"slug": SLUG, "title": "合成锚甲", "acceptance": GOOD_ACC}
    assert G.evaluate(ok, []).ok
    assert G.evaluate(ok, [], min_acceptance_chars=S.AUDIT_MIN_ACCEPTANCE_CHARS).ok
    # 坏 slug 两种口径都判死（判据项本身不因门槛放宽）
    bad = {"slug": "A1-A2", "title": "步骤编号", "acceptance": GOOD_ACC}
    assert not G.evaluate(bad, []).ok and not G.evaluate(bad, [], min_acceptance_chars=8).ok


# ── 2. 迁移文件 ⇄ audit_ddl 同形状 ─────────────────────────────────────────
def test_migration_file_matches_audit_ddl():
    assert MIGRATION.exists(), "迁移文件缺失"
    sql = MIGRATION.read_text(encoding="utf-8")
    assert "BEGIN;" in sql and sql.strip().endswith("COMMIT;")
    for stmt in S.audit_ddl():
        for col in re.findall(r"audit_verdict|audit_reason|audited_at", stmt):
            assert col in sql, f"audit_ddl 的列 {col} 未出现在迁移文件里（漂移）"
    assert "IF NOT EXISTS" in sql and "CREATE OR REPLACE VIEW" in sql


def test_audit_ddl_is_idempotent_and_schema_qualified():
    stmts = S.audit_ddl("scratch_x")
    assert stmts and all("scratch_x." in s for s in stmts)
    assert all("IF NOT EXISTS" in s for s in stmts)


# ── 3. CLI 面 ──────────────────────────────────────────────────────────────
def test_cli_registers_audit_subcommand():
    ap = cli.build_parser()
    ns = ap.parse_args(["audit", "--cap", "5", "--limit", "2"])
    assert ns.cmd == "audit" and ns.cap == 5 and ns.limit == 2
    assert ns.apply is False                      # 缺省 dry-run：只读是默认
    assert "audit" in cli.HANDLERS


# ── 真 PG 夹具 ─────────────────────────────────────────────────────────────
def _pg_available() -> bool:
    if not shutil.which("psql"):
        return False
    try:
        p = subprocess.run(["su", "postgres", "-c",
                            "psql -d tensorplatform -tA -c 'select 1'"],
                           capture_output=True, timeout=30)
        return p.returncode == 0 and p.stdout.decode("utf-8", "replace").strip() == "1"
    except Exception:  # noqa: BLE001
        return False


PG = pytest.mark.skipif(not _pg_available(), reason="no local psql/postgres peer access")

SCHEMA_SQL = f"""DROP SCHEMA IF EXISTS {SCRATCH} CASCADE;
CREATE SCHEMA {SCRATCH};
CREATE TABLE {SCRATCH}.feature_ledger (
  id serial PRIMARY KEY, slug text NOT NULL UNIQUE, title text NOT NULL,
  domain text NOT NULL, source text NOT NULL, acceptance text NOT NULL,
  ledger_group text, status text NOT NULL DEFAULT 'planned', evidence text,
  mission_id bigint, created_at timestamptz DEFAULT now(), done_at timestamptz);
CREATE TABLE {SCRATCH}.tensor_dev_missions (
  id bigserial PRIMARY KEY, goal text NOT NULL, status text NOT NULL DEFAULT 'succeeded',
  result jsonb NOT NULL DEFAULT '{{}}'::jsonb, finished_at timestamptz);
CREATE TABLE {SCRATCH}.chronicle_snapshots (ts timestamptz DEFAULT now(), payload jsonb);"""


@pytest.fixture()
def pg():
    t = S.PsqlTransport(db="tensorplatform")
    t.execute(SCHEMA_SQL)
    S.ensure_audit_schema(t, SCRATCH)
    try:
        yield t
    finally:
        t.execute(f"DROP SCHEMA IF EXISTS {SCRATCH} CASCADE;")


def seed_mission(t: S.Transport, mission_id: int, status: str = "queued") -> None:
    t.execute(
        f"INSERT INTO {S.q('tensor_dev_missions', SCRATCH)} (id, goal, status) "
        f"VALUES ({int(mission_id)}, {S.sql_literal('synthetic goal')}, "
        f"{S.sql_literal(status)});")


def seed(t: S.Transport, slug: str, *, acceptance: str = GOOD_ACC, title: str = "",
         mission_id: int | None = None, mission_status: str = "queued",
         log_status: str = "open", ledger_status: str = "open") -> None:
    """种一条「已上架待审」锚（日志行 + 账本行 + 可选单子）。

    标题缺省由 slug 派生（**唯一**）：否则两条夹具会因标题全等被判 ``duplicate_title``，
    测出来的就不是本条要测的东西了。
    """
    title = title or f"合成锚 {slug}"
    if mission_id is not None:
        seed_mission(t, mission_id, mission_status)
    t.execute(
        f"INSERT INTO {S.q('feature_ledger', SCRATCH)} "
        f"(slug, title, domain, source, acceptance, status, mission_id) VALUES "
        f"({S.sql_literal(slug)}, {S.sql_literal(title)}, 'platform', 'agent-proposed', "
        f"{S.sql_literal(acceptance)}, {S.sql_literal(ledger_status)}, "
        f"{int(mission_id) if mission_id is not None else 'NULL'});")
    t.execute(
        f"INSERT INTO {S.q('demand_intake_log', SCRATCH)} "
        f"(cand_key, source_kind, source_ref, slug, title, acceptance, status, mission_id) "
        f"VALUES ({S.sql_literal(S.cand_key('mission_finish', f'synthetic:{slug}', slug))}, "
        f"'mission_finish', {S.sql_literal(f'synthetic:{slug}')}, "
        f"{S.sql_literal(slug)}, {S.sql_literal(title)}, {S.sql_literal(acceptance)}, "
        f"{S.sql_literal(log_status)}, "
        f"{int(mission_id) if mission_id is not None else 'NULL'});")


def state(t: S.Transport) -> dict:
    logs = t.query(f"SELECT slug, status, audit_verdict, audit_reason FROM "
                   f"{S.q('demand_intake_log', SCRATCH)} ORDER BY slug")
    led = t.query(f"SELECT slug, status FROM {S.q('feature_ledger', SCRATCH)} ORDER BY slug")
    mis = t.query(f"SELECT id, status FROM {S.q('tensor_dev_missions', SCRATCH)} ORDER BY id")
    return {"log": {r["slug"]: r for r in logs},
            "ledger": {r["slug"]: r["status"] for r in led},
            "missions": {int(r["id"]): r["status"] for r in mis}}


# ── 4. dry-run 零写 ────────────────────────────────────────────────────────
@PG
def test_dry_run_writes_nothing(pg):
    seed(pg, "FEAT-SYNTH-ALPHA", mission_id=9101)
    seed(pg, "FEAT-SYNTH-BETA", acceptance=THIN_ACC, mission_id=9102)
    before = state(pg)
    out = A.run_audit(pg, dry_run=True, schema=SCRATCH)
    assert out["sampled"] == 2 and out["passed"] == 1 and out["failed"] == 1
    assert out["dropped"] == 1 and out["revoked_missions"] == []
    assert state(pg) == before, "dry-run 必须一条行都不写"


# ── 5. 过审：保持 open + 记 pass ───────────────────────────────────────────
@PG
def test_pass_keeps_anchor_open_and_records_verdict(pg):
    seed(pg, "FEAT-SYNTH-ALPHA", mission_id=9201)
    out = A.run_audit(pg, schema=SCRATCH)
    assert out["passed"] == 1 and out["failed"] == 0 and out["dropped"] == 0
    st = state(pg)
    assert st["ledger"]["FEAT-SYNTH-ALPHA"] == "open"
    assert st["log"]["FEAT-SYNTH-ALPHA"]["audit_verdict"] == A.PASS
    assert st["missions"][9201] == "queued"           # 过审不动单子
    assert out["uncovered"] == 0


# ── 6. 不过审：锚 dropped + 只撤未终态单 ───────────────────────────────────
@PG
def test_fail_drops_anchor_and_revokes_only_nonterminal_missions(pg):
    # 同一 slug 既有未终态单(9301) 也有已终态单(9302, succeeded)
    seed(pg, "FEAT-SYNTH-ALPHA", acceptance=THIN_ACC, mission_id=9301,
         mission_status="running")
    seed(pg, "FEAT-SYNTH-BETA", mission_id=9302, mission_status="succeeded")
    out = A.run_audit(pg, schema=SCRATCH)
    assert out["failed"] == 1 and out["dropped"] == 1
    assert sorted(out["revoked_missions"]) == [9301]
    st = state(pg)
    assert st["ledger"]["FEAT-SYNTH-ALPHA"] == "dropped"
    assert st["log"]["FEAT-SYNTH-ALPHA"]["audit_verdict"] == A.FAIL
    assert st["log"]["FEAT-SYNTH-ALPHA"]["audit_reason"].startswith("acceptance_too_short")
    assert st["missions"][9301] == "revoked"
    assert st["missions"][9302] == "succeeded", "终态单不倒改历史"
    # 留痕：审计事件进了 chronicle_snapshots
    rows = pg.query(f"SELECT payload->>'phase' AS phase FROM "
                    f"{S.q('chronicle_snapshots', SCRATCH)}")
    phases = {r["phase"] for r in rows}
    assert {"audit_fail", "audit_run"} <= phases


@PG
def test_revoke_missions_ignores_terminal_statuses(pg):
    seed(pg, "FEAT-SYNTH-GAMMA", mission_id=9401, mission_status="succeeded")
    seed_mission(pg, 9402, "queued")
    revoked = A.revoke_missions(pg, [9401, 9402, 0], schema=SCRATCH)
    assert revoked == [9402]
    st = state(pg)
    assert st["missions"][9401] == "succeeded" and st["missions"][9402] == "revoked"
    assert A.revoke_missions(pg, [], schema=SCRATCH) == []


# ── 7. 日帽 + uncovered + 幂等重跑 ─────────────────────────────────────────
@PG
def test_daily_cap_limits_sampling_and_reports_uncovered(pg):
    for i in range(5):
        seed(pg, f"FEAT-SYNTH-CAP{i}", mission_id=9500 + i)
    out = A.run_audit(pg, daily_cap=2, schema=SCRATCH)
    assert out["budget"] == 2 and out["sampled"] == 2
    assert out["pending_total"] == 5 and out["uncovered"] == 3   # 覆盖缺口如实报出
    # 帽用尽后再跑：零抽样，且不写任何裁决
    again = A.run_audit(pg, daily_cap=2, schema=SCRATCH)
    assert again["audits_used_today"] == 2 and again["budget"] == 0
    assert again["sampled"] == 0 and again["uncovered"] == 3


@PG
def test_rerun_does_not_resample_audited_rows(pg):
    seed(pg, "FEAT-SYNTH-ALPHA", mission_id=9601)
    seed(pg, "FEAT-SYNTH-BETA", acceptance=THIN_ACC, mission_id=9602)
    first = A.run_audit(pg, schema=SCRATCH)
    assert first["sampled"] == 2
    second = A.run_audit(pg, schema=SCRATCH)
    assert second["sampled"] == 0, "审过的锚不该被重复抽中（幂等）"
    assert second["pending_total"] == 0 and second["uncovered"] == 0


@PG
def test_pending_review_anchors_are_not_audited(pg):
    """事前门还没放行的锚不在审计面（审计对象是已上架的可领面）。"""
    seed(pg, "FEAT-SYNTH-PEND", log_status="pending_review", ledger_status="pending_review")
    out = A.run_audit(pg, schema=SCRATCH)
    assert out["pending_total"] == 0 and out["sampled"] == 0


@PG
def test_migration_applies_idempotently_in_scratch_schema(pg):
    """迁移文件本身跑两遍 = 空操作（临时 schema + search_path；不碰生产行）。"""
    sql = MIGRATION.read_text(encoding="utf-8")
    for _ in range(2):
        pg.execute(f"SET search_path = {SCRATCH};\n{sql}")
    cols = pg.query("SELECT column_name FROM information_schema.columns "
                    f"WHERE table_schema = '{SCRATCH}' AND table_name = 'demand_intake_log' "
                    "AND column_name LIKE 'audit%'")
    assert {c["column_name"] for c in cols} == {"audit_verdict", "audit_reason", "audited_at"}
    view = pg.query("SELECT table_name FROM information_schema.views "
                    f"WHERE table_schema = '{SCRATCH}' "
                    "AND table_name = 'demand_intake_audit_daily'")
    assert view, "口径视图必须建出来"
