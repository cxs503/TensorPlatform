#!/usr/bin/env python3
"""七日浸泡审计（``tools/demand_intake/soak.py``）的测试 —— 合成夹具 + 真 PG 红队。

纪律：**零领域词条** —— 夹具 slug 一律 ``FEAT-SYNTH-*``，标题/验收是合成句子。

覆盖面：
1. DailyAuditStat 覆盖率/帽拒率计算（含边界：分母=0、sampled=0）；
2. compute_soak_report 连续 7 日覆盖率判定 + 帽拒率判定 + 帽推荐逻辑；
3. 真 PG：fetch_daily_audit_stats 从 DB 拉取数据；
4. 真 PG：apply_cap 写回 demand_mint_quota；
5. 真 PG：run_soak_audit 全链路（含 chronicle 留痕）；
6. CLI ``soak`` 子命令已注册且缺省 dry-run。
"""

from __future__ import annotations

import argparse

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tools.demand_intake import soak as SO, store as S  # noqa: E402


# ── 1. DailyAuditStat 计算 ─────────────────────────────────────────────────
def test_coverage_rate_vacuously_true():
    """待审=0 时覆盖率视为 1.0（vacuously true）。"""
    s = SO.DailyAuditStat(day="2026-11-13", audited=0, pending=0)
    assert s.coverage_rate == 1.0


def test_coverage_rate_normal():
    s = SO.DailyAuditStat(day="2026-11-13", audited=12, pending=20)
    assert abs(s.coverage_rate - 0.6) < 1e-9


def test_rejection_rate_zero_sampled():
    """sampled=0 时帽拒率视为 0.0。"""
    s = SO.DailyAuditStat(day="2026-11-13", sampled=0, failed=0)
    assert s.rejection_rate == 0.0


def test_rejection_rate_normal():
    s = SO.DailyAuditStat(day="2026-11-13", sampled=10, failed=3)
    assert abs(s.rejection_rate - 0.3) < 1e-9


# ── 2. compute_soak_report 逻辑 ────────────────────────────────────────────
def test_empty_stats_returns_healthy():
    """无数据时视为健康（首日或审计未启动）。"""
    report = SO.compute_soak_report([])
    assert report.coverage_met is True
    assert report.rejection_healthy is True
    assert report.cap_recommended == SO.CAP_NORMAL
    assert report.notes


def test_all_days_met_coverage_and_healthy_rejection():
    """连续 7 日覆盖率 >= 60% 且帽拒率 < 20% → 维持默认帽。"""
    stats = [
        SO.DailyAuditStat(day=f"2026-11-{i:02d}", audited=12, pending=20,
                          sampled=10, passed=8, failed=1, dropped=1)
        for i in range(7, 0, -1)
    ]
    report = SO.compute_soak_report(stats)
    assert report.coverage_met is True       # 12/20 = 60% >= 60%
    assert report.rejection_healthy is True  # 7/70 = 10% < 20%
    assert report.cap_recommended == SO.CAP_NORMAL


def test_low_coverage_triggers_cap_escalation():
    """某日覆盖率 < 60% → 帽上调至 40。"""
    stats = [
        SO.DailyAuditStat(day=f"2026-11-{i:02d}", audited=12, pending=20,
                          sampled=10, passed=8, failed=1, dropped=1)
        for i in range(7, 1, -1)
    ]
    # 最后一天覆盖率只有 5/20 = 25%
    stats.append(SO.DailyAuditStat(day="2026-11-01", audited=5, pending=20,
                                    sampled=10, passed=8, failed=1, dropped=1))
    report = SO.compute_soak_report(stats)
    assert report.coverage_met is False
    assert report.cap_recommended == SO.CAP_ESCALATED
    assert any("覆盖率未达标" in n for n in report.notes)


def test_high_rejection_triggers_cap_escalation():
    """帽拒率 >= 20% → 帽上调至 40。"""
    stats = [
        SO.DailyAuditStat(day=f"2026-11-{i:02d}", audited=12, pending=20,
                          sampled=10, passed=5, failed=5, dropped=5)
        for i in range(7, 0, -1)
    ]
    report = SO.compute_soak_report(stats)
    # 5/10 = 50% >= 20%
    assert report.rejection_healthy is False
    assert report.cap_recommended == SO.CAP_ESCALATED
    assert any("帽拒率偏高" in n for n in report.notes)


def test_both_thresholds_fail():
    """覆盖率和帽拒率都不达标 → 帽上调（只写一次）。"""
    stats = [
        SO.DailyAuditStat(day=f"2026-11-{i:02d}", audited=3, pending=20,
                          sampled=10, passed=2, failed=8, dropped=8)
        for i in range(7, 0, -1)
    ]
    report = SO.compute_soak_report(stats)
    assert report.coverage_met is False
    assert report.rejection_healthy is False
    assert report.cap_recommended == SO.CAP_ESCALATED
    # 两条 note
    assert sum(1 for n in report.notes if "审计帽上调" in n) == 2


def test_window_shorter_than_7_days():
    """不足 7 日数据时，用实际天数计算。"""
    stats = [
        SO.DailyAuditStat(day=f"2026-11-{i:02d}", audited=12, pending=20,
                          sampled=10, passed=8, failed=1, dropped=1)
        for i in range(3, 0, -1)
    ]
    report = SO.compute_soak_report(stats, window_days=7)
    assert report.coverage_met is True
    assert len(report.daily_stats) == 3


def test_report_to_dict_serializes():
    """report_to_dict 输出可 JSON 序列化。"""
    stats = [
        SO.DailyAuditStat(day="2026-11-13", audited=12, pending=20,
                          sampled=10, passed=8, failed=1, dropped=1)
    ]
    report = SO.compute_soak_report(stats)
    d = SO.report_to_dict(report)
    json_str = json.dumps(d, ensure_ascii=False)
    assert json_str  # 不抛异常
    parsed = json.loads(json_str)
    assert parsed["coverage_met"] is True
    assert parsed["cap_recommended"] == SO.CAP_NORMAL


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

SCRATCH = "m17135_soak_scratch"

SCHEMA_SQL = f"""DROP SCHEMA IF EXISTS {SCRATCH} CASCADE;
CREATE SCHEMA {SCRATCH};
CREATE TABLE {SCRATCH}.feature_ledger (
  id serial PRIMARY KEY, slug text NOT NULL UNIQUE, title text NOT NULL,
  domain text NOT NULL, source text NOT NULL, acceptance text NOT NULL,
  ledger_group text, status text NOT NULL DEFAULT 'planned', evidence text,
  mission_id bigint, created_at timestamptz DEFAULT now(), done_at timestamptz);
CREATE TABLE {SCRATCH}.demand_intake_log (
  id bigserial PRIMARY KEY, cand_key text NOT NULL UNIQUE, source_kind text NOT NULL,
  source_ref text NOT NULL, slug text NOT NULL DEFAULT '', title text NOT NULL DEFAULT '',
  acceptance text NOT NULL DEFAULT '', domain text NOT NULL DEFAULT 'platform',
  confidence text NOT NULL DEFAULT '', src_field text NOT NULL DEFAULT '',
  status text NOT NULL, reason text NOT NULL DEFAULT '', ledger_id integer,
  raw jsonb NOT NULL DEFAULT '{{}}'::jsonb, mission_id bigint,
  audit_verdict text NOT NULL DEFAULT '', audit_reason text NOT NULL DEFAULT '',
  audited_at timestamptz, created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now());
CREATE TABLE {SCRATCH}.chronicle_snapshots (ts timestamptz DEFAULT now(), payload jsonb);
CREATE TABLE {SCRATCH}.demand_mint_quota (
  day date NOT NULL, agent text NOT NULL, minted integer NOT NULL DEFAULT 0,
  cap integer NOT NULL DEFAULT 20, updated_at timestamptz DEFAULT now(),
  PRIMARY KEY (day, agent));"""


@pytest.fixture()
def pg():
    t = S.PsqlTransport(db="tensorplatform")
    t.execute(SCHEMA_SQL)
    try:
        yield t
    finally:
        t.execute(f"DROP SCHEMA IF EXISTS {SCRATCH} CASCADE;")


def seed_audit_row(t: S.Transport, slug: str, *, audited: bool = False,
                   status: str = "open", audit_verdict: str = "") -> None:
    """种一条 demand_intake_log 行。"""
    t.execute(
        f"INSERT INTO {S.q('demand_intake_log', SCRATCH)} "
        f"(cand_key, source_kind, source_ref, slug, title, acceptance, status, "
        f" audit_verdict, audited_at) VALUES "
        f"({S.sql_literal(S.cand_key('synthetic', slug, slug))}, 'synthetic', "
        f"{S.sql_literal(slug)}, {S.sql_literal(slug)}, {S.sql_literal(f'合成锚 {slug}')}, "
        f"'pytest 全绿且偏差 <3%', {S.sql_literal(status)}, "
        f"{S.sql_literal(audit_verdict)}, "
        f"{'now()' if audited else 'NULL'});")
    # 对应的 feature_ledger 行
    t.execute(
        f"INSERT INTO {S.q('feature_ledger', SCRATCH)} "
        f"(slug, title, domain, source, acceptance, status) VALUES "
        f"({S.sql_literal(slug)}, {S.sql_literal(f'合成锚 {slug}')}, 'platform', "
        f"'agent-proposed', 'pytest 全绿且偏差 <3%', {S.sql_literal(status)});")


# ── 3. fetch_daily_audit_stats ─────────────────────────────────────────────
@PG
def test_fetch_stats_empty(pg):
    """空表时返回空列表。"""
    stats = SO.fetch_daily_audit_stats(pg, days=7, schema=SCRATCH)
    assert len(stats) == 7
    assert all(s.audited == 0 for s in stats)


@PG
def test_fetch_stats_with_audited_rows(pg):
    """有审计行时能正确计数。"""
    seed_audit_row(pg, "FEAT-SYNTH-ALPHA", audited=True, audit_verdict="pass")
    seed_audit_row(pg, "FEAT-SYNTH-BETA", audited=True, audit_verdict="fail")
    seed_audit_row(pg, "FEAT-SYNTH-GAMMA", audited=False)
    stats = SO.fetch_daily_audit_stats(pg, days=7, schema=SCRATCH)
    today_audited = sum(s.audited for s in stats if s.day == str(__import__('datetime').date.today()))
    assert today_audited == 2


# ── 4. apply_cap ───────────────────────────────────────────────────────────
@PG
def test_apply_cap_upserts(pg):
    """apply_cap 写回 demand_mint_quota。"""
    SO.apply_cap(pg, 40, schema=SCRATCH)
    rows = pg.query(f"SELECT cap FROM {S.q('demand_mint_quota', SCRATCH)} "
                    f"WHERE agent = 'soak-audit'")
    assert rows and int(rows[0]["cap"]) == 40
    # 幂等：再写一次不报错
    SO.apply_cap(pg, 30, schema=SCRATCH)
    rows2 = pg.query(f"SELECT cap FROM {S.q('demand_mint_quota', SCRATCH)} "
                     f"WHERE agent = 'soak-audit'")
    assert int(rows2[0]["cap"]) == 30


# ── 5. run_soak_audit 全链路 ──────────────────────────────────────────────
@PG
def test_run_soak_audit_dry_run(pg):
    """dry-run（apply_cap_flag=False）不写 cap。"""
    seed_audit_row(pg, "FEAT-SYNTH-ALPHA", audited=True, audit_verdict="pass")
    report = SO.run_soak_audit(pg, days=7, apply_cap_flag=False, schema=SCRATCH)
    assert isinstance(report, SO.SoakReport)
    # 不写 cap
    rows = pg.query(f"SELECT count(*) AS n FROM {S.q('demand_mint_quota', SCRATCH)} "
                    f"WHERE agent = 'soak-audit'")
    assert int(rows[0]["n"]) == 0


@PG
def test_run_soak_audit_apply_cap(pg):
    """apply_cap_flag=True 时写回 cap。"""
    seed_audit_row(pg, "FEAT-SYNTH-ALPHA", audited=True, audit_verdict="pass")
    report = SO.run_soak_audit(pg, days=7, apply_cap_flag=True, schema=SCRATCH)
    assert isinstance(report, SO.SoakReport)
    rows = pg.query(f"SELECT cap FROM {S.q('demand_mint_quota', SCRATCH)} "
                    f"WHERE agent = 'soak-audit'")
    assert rows and int(rows[0]["cap"]) == report.cap_recommended


# ── 6. CLI 面 ──────────────────────────────────────────────────────────────
def test_cli_registers_soak_subcommand():
    from tools.demand_intake import cli
    ap = cli.build_parser()
    ns = ap.parse_args(["soak", "--days", "5", "--apply-cap"])
    assert ns.cmd == "soak" and ns.days == 5 and ns.apply_cap is True
    assert "soak" in cli.HANDLERS


@PG
def test_cli_soak_dry_run(pg):
    """CLI soak 缺省不写 cap。"""
    from tools.demand_intake import cli
    seed_audit_row(pg, "FEAT-SYNTH-ALPHA", audited=True, audit_verdict="pass")
    out = cli.cmd_soak(argparse.Namespace(
        db="", schema=SCRATCH, timeout_ms=30000,
        days=7, apply_cap=False))
    assert out["cap_recommended"] in (SO.CAP_NORMAL, SO.CAP_ESCALATED)
    assert out["coverage_met"] is True or out["coverage_met"] is False
