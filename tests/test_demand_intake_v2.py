"""FEAT-DEMAND-AUTOINTAKE v2（m17134）验收测试：机门两处真空档 + 事后审计处置链。

覆盖矩阵
--------
纯函数（无 IO）：帽政策、信用档政策、验收量化判据、重复检测、铸造期前置拦截粒度。
真库（psql 传输，一次性 schema）：
  - 铸造即 open + 同步建单（v2 改造①；断言全程不出现 ``pending_review``）
  - 并发同 slug 只成 1（改造①/③ 的原子性靶子）
  - 帽超限拒绝（改造③）
  - strike≥3 降级 1/日 + ``worker_credit_tiers`` 联动（改造③）
  - 审计不过 → dropped + revoke 未终态单 + chronicle（改造②）
  - 审计帽 20/日 + 覆盖缺口不静默（改造②）
  - 建单失败不静默（dispatch_failed 可见，锚仍 open）
  - 周报对照计量（改造④：agent vs coordinator 过审率/成功率）

无数据库环境（CI 无 psql/无库）时真库用例自动 skip，纯函数用例始终运行——诚实边界：
「跳过」不是「通过」。
"""

from __future__ import annotations

import uuid
from typing import Any, Dict, Iterator, List, Tuple

import pytest

from tools.demand_intake import audit, judge, mint, redteam, report
from tools.demand_intake.dispatch import FailingDispatcher
from tools.demand_intake.store import (AUDIT_DAILY_CAP, DEFAULT_DAILY_CAP,
                                       DOWNGRADED_DAILY_CAP, STRIKE_THRESHOLD,
                                       PsqlTransport, StoreError, create_schema,
                                       drop_schema, ensure_schema,
                                       ensure_support_schema, q, sql_literal)


# ── 纯函数 ──────────────────────────────────────────────────────────────────
def test_cap_policy_downgrades_on_strikes():
    assert mint.cap_for_strikes(0) == DEFAULT_DAILY_CAP == 10
    assert mint.cap_for_strikes(2) == 10
    assert mint.cap_for_strikes(3) == DOWNGRADED_DAILY_CAP == 1
    assert mint.cap_for_strikes(9) == 1


def test_credit_level_monotone_policy():
    assert [mint.credit_level_for_strikes(s) for s in (0, 2, 3, 4, 5, 8)] == [3, 3, 2, 2, 1, 1]
    assert STRIKE_THRESHOLD == 3


@pytest.mark.parametrize("acceptance,ok", [
    ("误差 <3%（实测 1.2%）", True),
    ("pytest 全绿，覆盖率 ≥80%", True),
    ("做得更好一些即可", False),          # 无数字、无阈值标记
    ("完成即可 100", False),              # 无比较符/计量词
    ("", False),
    ("短 x1", False),
])
def test_acceptance_must_be_machine_checkable(acceptance, ok):
    assert (judge.acceptance_problem(acceptance) == "") is ok


def test_judge_flags_title_duplicate_and_bad_slug():
    existing = [{"slug": "FEAT-SYNTH-ALPHA", "title": "demand intake anchor feeder alpha",
                 "status": "open"}]
    d = judge.evaluate({"slug": "A1", "title": "demand intake anchor feeder alpha",
                        "acceptance": "误差 <3%"}, existing)
    assert d.verdict == judge.FAIL
    codes = " ".join(d.reasons)
    assert "slug" in codes and "duplicate_title" in codes

    ok = judge.evaluate({"slug": "FEAT-SYNTH-BETA", "title": "totally different beta anchor",
                         "acceptance": "误差 <3%（实测 1.2%）"}, existing)
    assert ok.verdict == judge.PASS


def test_mint_precheck_only_blocks_unledgerable_rows():
    assert judge.mint_precheck({"slug": ""}) == "slug_empty"
    assert judge.mint_precheck({"slug": "FEAT-SYNTH-X"}) == ""
    # 验收语义有争议的锚**必须**放行到事后审计（否则又回到事前门）
    assert judge.mint_precheck({"slug": "FEAT-SYNTH-Y", "acceptance": "做得更好"}) == ""


# ── 真库夹具 ────────────────────────────────────────────────────────────────
@pytest.fixture(scope="module")
def transport() -> PsqlTransport:
    t = PsqlTransport()
    try:
        t.query("SELECT 1 AS ok")
    except (StoreError, FileNotFoundError) as exc:      # 无 psql / 无库 → 诚实 skip
        pytest.skip(f"postgres unavailable: {exc}")
    return t


@pytest.fixture(scope="module")
def schemas(transport: PsqlTransport) -> Iterator[Any]:
    created: List[str] = []

    def make() -> str:
        name = f"m17134_t_{uuid.uuid4().hex[:8]}"
        create_schema(transport, name)
        ensure_schema(transport, name)
        ensure_support_schema(transport, name)
        created.append(name)
        return name

    yield make
    for name in created:
        drop_schema(transport, name)


def _cand(slug: str, *, acceptance: str = "误差 <3%（实测 1.2%）",
          title: str = "") -> Dict[str, Any]:
    return {"slug": slug, "title": title or f"synthetic anchor {slug}",
            "acceptance": acceptance, "domain": "platform", "repo": "TensorPlatform",
            "source_kind": "test", "source_ref": slug}


def _n(t, sql: str) -> int:
    rows = t.query(sql)
    return int(rows[0]["n"]) if rows else 0


# ── 改造①：铸造即 open + 同步建单 ───────────────────────────────────────────
def test_mint_opens_anchor_and_dispatches_mission(transport, schemas):
    schema = schemas()
    disp = redteam.SchemaRecordingDispatcher(transport, schema)
    out = mint.mint(transport, _cand("FEAT-SYNTH-OPEN-01"), mint_kind="agent",
                    minted_by="t-open", dispatcher=disp, schema=schema)
    assert out.status == mint.MINTED, out.reason
    assert out.mission_id and len(disp.calls) == 1

    led = transport.query(f"SELECT status, mission_id, source FROM {q('feature_ledger', schema)} "
                          f"WHERE slug = 'FEAT-SYNTH-OPEN-01'")[0]
    assert led["status"] == "open"                       # 直接可领，无 pending_review
    assert int(led["mission_id"]) == out.mission_id

    # 全库范围内不得出现 v1 的事前门状态
    pending = _n(transport, f"SELECT count(*) AS n FROM {q('feature_ledger', schema)} "
                            f"WHERE status = 'pending_review'")
    assert pending == 0

    log = transport.query(f"SELECT status, mint_kind, minted_by, mission_id, cap_applied "
                          f"FROM {q('demand_intake_log', schema)} "
                          f"WHERE slug = 'FEAT-SYNTH-OPEN-01'")[0]
    assert log["status"] == "minted" and log["mint_kind"] == "agent"
    assert int(log["mission_id"]) == out.mission_id and int(log["cap_applied"]) == DEFAULT_DAILY_CAP


def test_dispatch_failure_is_not_silent(transport, schemas):
    schema = schemas()
    out = mint.mint(transport, _cand("FEAT-SYNTH-DISPFAIL"), mint_kind="agent",
                    minted_by="t-dispfail", dispatcher=FailingDispatcher(), schema=schema)
    assert out.status == mint.DISPATCH_FAILED and out.mission_id is None
    assert "dispatch_failed" in out.reason
    led = transport.query(f"SELECT status FROM {q('feature_ledger', schema)} "
                          f"WHERE slug = 'FEAT-SYNTH-DISPFAIL'")[0]
    assert led["status"] == "open"            # 锚不因建单失败被吞掉（可重放）
    log = transport.query(f"SELECT status, mission_id FROM {q('demand_intake_log', schema)} "
                          f"WHERE slug = 'FEAT-SYNTH-DISPFAIL'")[0]
    assert log["status"] == "dispatch_failed" and log["mission_id"] is None


# ── 改造③：机器门两处真空档 ─────────────────────────────────────────────────
def test_concurrent_same_slug_only_mints_once(transport, schemas):
    res = redteam.scenario_concurrent_same_slug(transport, schemas())
    assert res["ok"], res
    assert res["minted"] == 1 and res["ledger_rows"] == 1 and res["mission_rows"] == 1
    assert res["duplicates"] == res["threads"] - 1


def test_daily_cap_rejects_overflow(transport, schemas):
    res = redteam.scenario_cap_exceeded(transport, schemas())
    assert res["ok"], res
    assert res["minted"] == DEFAULT_DAILY_CAP and res["cap_exceeded"] == 1
    assert res["quota"]["minted"] == DEFAULT_DAILY_CAP


def test_strike_downgrade_and_credit_tier_linkage(transport, schemas):
    res = redteam.scenario_strike_downgrade(transport, schemas())
    assert res["ok"], res
    assert res["cap_after_strikes"] == DOWNGRADED_DAILY_CAP == 1
    assert res["fresh_agent_first"] == "minted" and res["fresh_agent_second"] == "cap_exceeded"
    assert all(v <= 2 for v in res["credit_levels"].values())


def test_credit_tier_upsert_is_idempotent(transport, schemas):
    schema = schemas()
    agent = "t-tier"
    for _ in range(3):
        mint.apply_strike(transport, agent, note="x", schema=schema)
    first = mint.sync_credit_tier(transport, agent, strikes=3, schema=schema)
    second = mint.sync_credit_tier(transport, agent, strikes=3, schema=schema)
    assert first["credit_level"] == second["credit_level"] == 2   # 重复同步不再掉档
    rows = transport.query(f"SELECT count(*) AS n FROM {q('worker_credit_tiers', schema)} "
                           f"WHERE worker_id = 't-tier'")
    assert int(rows[0]["n"]) == 1


# ── 改造②：事后审计处置链 ───────────────────────────────────────────────────
def test_audit_fail_drops_and_revokes(transport, schemas):
    res = redteam.scenario_audit_fail_revokes(transport, schemas())
    assert res["ok"], res
    assert res["ledger_status"] == "dropped"
    assert res["mission_status"] == "revoked"
    assert "audit_fail" in res["chronicle_phases"]
    assert res["strikes"] == 1


def test_audit_does_not_touch_terminal_missions(transport, schemas):
    """已 succeeded 的单是既成事实：审计不过只能 dropped 锚，不得倒改单子历史。"""
    schema = schemas()
    disp = redteam.SchemaRecordingDispatcher(transport, schema, status="succeeded")
    out = mint.mint(transport, _cand("FEAT-SYNTH-TERM", acceptance="做得更好一些"),
                    mint_kind="agent", minted_by="t-term", dispatcher=disp, schema=schema)
    summary = audit.run_audit(transport, slugs=["FEAT-SYNTH-TERM"], schema=schema)
    assert summary["sampled"] == 1 and summary["failed"] == 1
    assert summary["revoked_missions"] == []
    st = transport.query(f"SELECT status FROM {q('tensor_dev_missions', schema)} "
                         f"WHERE id = {int(out.mission_id)}")[0]
    assert st["status"] == "succeeded"


def test_audit_dry_run_changes_nothing(transport, schemas):
    schema = schemas()
    disp = redteam.SchemaRecordingDispatcher(transport, schema)
    mint.mint(transport, _cand("FEAT-SYNTH-DRY", acceptance="做得更好一些"),
              mint_kind="agent", minted_by="t-dry", dispatcher=disp, schema=schema)
    summary = audit.run_audit(transport, slugs=["FEAT-SYNTH-DRY"], dry_run=True, schema=schema)
    assert summary["sampled"] == 1 and summary["failed"] == 1
    led = transport.query(f"SELECT status FROM {q('feature_ledger', schema)} "
                          f"WHERE slug = 'FEAT-SYNTH-DRY'")[0]
    assert led["status"] == "open"
    audits = _n(transport, f"SELECT count(*) AS n FROM {q('demand_intake_log', schema)} "
                           f"WHERE audit_verdict <> ''")
    assert audits == 0


def test_audit_daily_cap_and_uncovered_is_visible(transport, schemas):
    res = redteam.scenario_audit_daily_cap(transport, schemas())
    assert res["ok"], res
    assert res["sampled"] <= AUDIT_DAILY_CAP and res["sampled"] > 0
    assert res["second_run_budget"] == 0 and res["second_run_sampled"] == 0


# ── 改造④：周报对照计量 ─────────────────────────────────────────────────────
def test_weekly_report_compares_agent_vs_coordinator(transport, schemas):
    schema = schemas()
    t_log = q("demand_intake_log", schema)
    t_mis = q("tensor_dev_missions", schema)

    def seed(kind: str, agent: str, slug: str, verdict: str, mission_status: str) -> None:
        disp = redteam.SchemaRecordingDispatcher(transport, schema, status=mission_status)
        out = mint.mint(transport, _cand(slug), mint_kind=kind, minted_by=agent,
                        dispatcher=disp, schema=schema)
        assert out.status == mint.MINTED
        if verdict:
            transport.execute(f"UPDATE {t_log} SET audit_verdict = '{verdict}', "
                              f"audited_at = now() WHERE slug = {sql_literal(slug)};")

    # agent：2 条入账、1 过审 1 不过，单子 1 成功 1 失败
    seed("agent", "wr-a1", "FEAT-SYNTH-WR-A1", "pass", "succeeded")
    seed("agent", "wr-a1", "FEAT-SYNTH-WR-A2", "fail", "failed")
    # coordinator：1 条入账 1 过审，单子 1 成功
    seed("coordinator", "wr-c1", "FEAT-SYNTH-WR-C1", "pass", "succeeded")

    rep = report.weekly_report(transport, days=7, schema=schema)
    rows = {r["mint_kind"]: r for r in rep["rows"]}
    assert rows["agent"]["minted"] == 2 and rows["agent"]["audited"] == 2
    assert rows["agent"]["approval_rate"] == 0.5
    assert rows["agent"]["succeeded"] == 1 and rows["agent"]["finished"] == 2
    assert rows["agent"]["success_rate"] == 0.5
    assert rows["coordinator"]["approval_rate"] == 1.0
    assert rows["coordinator"]["success_rate"] == 1.0
    assert rep["comparison"]["approval_gap_pp"] == -50.0
    assert rep["comparison"]["success_gap_pp"] == -50.0

    md = report.render_markdown(rep)
    assert "铸造对照" in md and "过审率" in md and "成功率" in md
    assert "agent" in md and "coordinator" in md


def test_weekly_report_marks_provisional_when_coverage_low(transport, schemas):
    schema = schemas()
    disp = redteam.SchemaRecordingDispatcher(transport, schema)
    for i in range(4):          # 4 条入账、0 条已审 → 覆盖不足
        mint.mint(transport, _cand(f"FEAT-SYNTH-PROV-{i}"), mint_kind="agent",
                  minted_by="wr-prov", dispatcher=disp, schema=schema)
    rep = report.weekly_report(transport, days=7, schema=schema)
    row = next(r for r in rep["rows"] if r["mint_kind"] == "agent")
    assert row["audited"] == 0 and row["approval_rate"] is None
    assert row["provisional"] is True
    assert "provisional" in report.render_markdown(rep)