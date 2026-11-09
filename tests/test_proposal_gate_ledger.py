"""账本 四态 / 幂等唯一键 / KPI 暴露 / 播种覆盖率 的测试（sqlite 临时库，脱 PG）。"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.exc import IntegrityError

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from proposal_gate_support import synth_config, v2_payload  # noqa: E402
from tools.proposal_gate.gate import ProposalGate  # noqa: E402
from tools.proposal_gate.ledger import STATES, VERDICTS, LedgerStore, sql_trace  # noqa: E402


@pytest.fixture()
def cfg():
    return synth_config()


@pytest.fixture()
def store(tmp_path, cfg):
    s = LedgerStore.for_sqlite(str(tmp_path / "ledger.sqlite"), cfg)
    yield s
    s.dispose()


def _record(store, **kw):
    base = dict(
        mission_id=1,
        sha256="a" * 64,
        verdict="accepted",
        schema_version="2.0",
        plan_id=2,
        score=0.1,
        reasons=("unit-test",),
    )
    base.update(kw)
    return store.record(**base)


def test_record_is_idempotent_on_mission_sha(store):
    row1, created1 = _record(store)
    row2, created2 = _record(store)
    assert created1 is True and created2 is False
    assert row1.sha256 == row2.sha256
    assert len(store.rows(limit=100)) == 1


def test_same_sha_in_different_mission_is_a_new_row(store):
    _record(store, mission_id=1)
    _record(store, mission_id=2)
    assert len(store.rows(limit=100)) == 2


def test_unique_constraint_exists_at_db_level(store):
    """唯一键是数据库约束，不只是应用层 if——绕过应用层也必须报错。"""
    _record(store)
    with pytest.raises(IntegrityError):
        with store.engine.begin() as conn:
            conn.execute(
                store.table.insert().values(
                    mission_id=1,
                    sha256="a" * 64,
                    schema_version="2.0",
                    verdict="duplicate",
                    state="dedup_rejected",
                    reasons="[]",
                )
            )


def test_unknown_verdict_or_state_is_rejected(store):
    with pytest.raises(ValueError):
        _record(store, verdict="whatever")
    with pytest.raises(ValueError):
        _record(store, verdict="accepted", state="not-a-state")


def test_four_states_and_kpi(store, cfg):
    _record(store, sha256="b" * 64, verdict="accepted")
    _record(store, sha256="c" * 64, verdict="duplicate", dup_of="b" * 64, score=0.97)
    _record(store, sha256="d" * 64, verdict="twin", score=0.9)
    _record(store, sha256="e" * 64, verdict="quarantined")
    _record(store, sha256="f" * 64, verdict="near_duplicate", score=0.7)
    stats = store.stats(adopted_covered=0)
    assert set(stats.counts) == set(STATES)
    assert stats.counts == {
        "proposal": 1,
        "adopted": 0,
        "dedup_rejected": 2,
        "quarantined": 2,
    }
    assert stats.total == 5
    assert stats.dup_rate == pytest.approx(2 / 5)
    keys = [k["metric_key"] for k in stats.kpi]
    assert cfg.need("kpi.dup_rejected_key") in keys
    dup_kpi = next(k for k in stats.kpi if k["metric_key"] == cfg.need("kpi.dup_rejected_key"))
    assert dup_kpi["value"] == 2 and dup_kpi["denominator"] == 5
    assert stats.verdict_counts["near_duplicate"] == 1
    assert stats.as_dict()["near_dup_rate"] == pytest.approx(1 / 5)


def test_adopt_transitions_state_and_keeps_audit(store):
    _record(store, sha256="9" * 64, verdict="accepted")
    row = store.set_state(
        mission_id=1,
        sha256="9" * 64,
        state="adopted",
        verdict="adopted",
        plan_id=77,
        reason="adopted_at=unit-test",
    )
    assert row.state == "adopted" and row.verdict == "adopted" and row.plan_id == 77
    assert any("adopted_at=" in r for r in row.reasons)
    # 不可逆校验：不存在的行 → KeyError（不静默成败）
    with pytest.raises(KeyError):
        store.set_state(mission_id=1, sha256="0" * 64, state="adopted")


def test_rows_filters(store):
    _record(store, sha256="1" * 64, mission_id=11)
    _record(store, sha256="2" * 64, mission_id=22, verdict="quarantined")
    assert len(store.rows(state="quarantined", limit=10)) == 1
    assert len(store.rows(mission_id=11, limit=10)) == 1
    assert len(store.rows(limit=10)) == 2


def test_sql_trace_is_executable(store):
    _record(store, sha256="3" * 64)
    sql = sql_trace(store)
    assert store.cfg.table_name in sql
    for stmt in [s.strip() for s in sql.split(";") if s.strip()]:
        with store.engine.begin() as conn:
            conn.execute(_text(stmt)).all()


def _text(sql):
    from sqlalchemy import text

    return text(sql)


def test_coverage_ledger(tmp_path, cfg):
    store = LedgerStore.for_sqlite(str(tmp_path / "c.sqlite"), cfg)
    gate = ProposalGate(cfg, store, quarantine_dir=tmp_path / "q", events_file=tmp_path / "e.jsonl")
    res = gate.evaluate(v2_payload(), mission_id=8001)
    gate.adopt(mission_id=8001, sha256=res.sha256, plan_id=42)
    # 节点索引里放一条同主题节点 → 覆盖
    gate.index.add_node("node:1", v2_payload()["title"] + "\n" + v2_payload()["goal"]["objective"])
    gate.index.add_node("node:2", "bar 报表延迟\n把 bar 报表的端到端延迟拆到各阶段")
    cov = gate.coverage()
    assert cov["adopted"] == 1 and cov["covered"] == 1
    assert cov["coverage_rate"] == pytest.approx(1.0)
    assert cov["nodes_seeded_from_proposals"] == 1
    assert cov["covered_items"][0]["best_node"] == "node:1"
    store.dispose()


def test_coverage_reports_uncovered_honestly(tmp_path, cfg):
    store = LedgerStore.for_sqlite(str(tmp_path / "c2.sqlite"), cfg)
    gate = ProposalGate(cfg, store, quarantine_dir=tmp_path / "q2", events_file=tmp_path / "e2.jsonl")
    res = gate.evaluate(v2_payload(), mission_id=8101)
    gate.adopt(mission_id=8101, sha256=res.sha256)
    gate.index.add_node("node:1", "widget 报表延迟\n把 widget 报表的端到端延迟拆到各阶段")
    cov = gate.coverage()
    assert cov["adopted"] == 1 and cov["covered"] == 0
    assert cov["coverage_rate"] == 0.0
    assert cov["uncovered_items"]
    store.dispose()


def test_verdict_domain_is_closed(store):
    """verdict 与四态都是封闭枚举（防静默扩词）。"""
    assert set(VERDICTS) >= {"accepted", "adopted", "duplicate", "twin", "quarantined"}
    assert set(STATES) == {"proposal", "adopted", "dedup_rejected", "quarantined"}


def test_ensure_schema_is_idempotent(tmp_path, cfg):
    path = tmp_path / "i.sqlite"
    engine = create_engine(f"sqlite:///{path}", future=True)
    s1 = LedgerStore.for_engine(engine, cfg)
    s1.ensure_schema()
    s2 = LedgerStore.for_engine(engine, cfg)
    s2.ensure_schema()
    assert s2.counts() == {k: 0 for k in STATES}
    engine.dispose()