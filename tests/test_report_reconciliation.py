"""报告对账门 IN50-W4-N10 单测：SLA 补报 / 反向核对 / KPI 口径 / 独占只读路径。

全部使用**合成域**（node-alpha / mission 数字 id / 通用标题），不含任何领域词条。

覆盖：
  T1  补报工单自带 72h SLA 截止时刻（due_at = created + sla_hours）
  T2  逾期台账：aging 分桶 / overdue 计数 / 12h 内到期
  T3  逾期升级幂等：一轮只升一次，二轮零改动
  T4  112 时代存量工单（due_at 为空）不获豁免，按 created + sla 判逾期
  T5  覆盖率口径 = 节点比 done_with_report/(done_with_report+done_no_report)
  T6  gate_status 携带 SLA 违约位（breach），但不掩盖欠账数
  T7  独占只读路径：结构性拦截对业务表的写（不靠自觉）
  T8  只读守卫放行 outbox/KPI 白名单
  T9  CLI 工件自哈希（sha256 可重算），退出码 GREEN=0 / RED=2
  T10 工件被篡改 → verify_artifact 失败
  T11 反向核对：幻影 mission_id → report_no_node；真实非战役 mission → orphan（不误报）
  T12 无欠账时 gate 为 GREEN（负对照：对账器不能恒红）
  T13 派单幂等：二轮零新增工单
  T14 零领域语义：实现文件不含领域词条
"""
from __future__ import annotations

import json
import os
import re
import sys
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
BACKEND = os.path.join(ROOT, "backend")
for _p in (ROOT, BACKEND):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from audit.report_reconcile import (  # noqa: E402
    SLA_HOURS_DEFAULT,
    QUEUE_TABLE,
    _insert_dispatch,
    _utcnow,
    dispatch_missing_reports,
    escalate_overdue,
    gate_status,
    reconcile,
    sla_report,
)
from tools.reconciliation import report_gate as rg  # noqa: E402


# ─────────────────────────── fixtures ────────────────────────────

SCHEMA_SQL = [
    """CREATE TABLE campaign_plans (
        id INTEGER PRIMARY KEY, organization_id INTEGER)""",
    """CREATE TABLE campaign_nodes (
        id INTEGER PRIMARY KEY, plan_id INTEGER, mission_id INTEGER,
        name TEXT, status TEXT, metadata TEXT DEFAULT '{}')""",
    """CREATE TABLE tensor_dev_missions (id INTEGER PRIMARY KEY)""",
    """CREATE TABLE tensor_reports (
        id INTEGER PRIMARY KEY, mission_id INTEGER, organization_id INTEGER,
        status TEXT, title TEXT, scenario_key TEXT, created_by INTEGER)""",
]


def _make_engine():
    eng = create_engine("sqlite:///:memory:")
    with eng.connect() as conn:
        for ddl in SCHEMA_SQL:
            conn.execute(text(ddl))
        conn.commit()
    return eng


def _seed(db, *, nodes, reports, missions=()):
    """nodes: [(id, mission_id, status)]  reports: [(id, mission_id, org)]"""
    db.execute(text("INSERT OR REPLACE INTO campaign_plans VALUES (1, 1)"))
    for nid, mid, status in nodes:
        db.execute(
            text("INSERT INTO campaign_nodes (id, plan_id, mission_id, name, status, metadata) "
                 "VALUES (:i, 1, :m, :n, :s, '{}')"),
            {"i": nid, "m": mid, "n": f"node-{nid:03d}", "s": status},
        )
    for rid, mid, org in reports:
        db.execute(
            text("INSERT INTO tensor_reports (id, mission_id, organization_id, status, title) "
                 "VALUES (:i, :m, :o, 'published', :t)"),
            {"i": rid, "m": mid, "o": org, "t": f"report-{rid:03d}"},
        )
    for mid in missions:
        db.execute(text("INSERT INTO tensor_dev_missions (id) VALUES (:m)"), {"m": mid})
    db.commit()


@pytest.fixture
def db():
    eng = _make_engine()
    Session = sessionmaker(bind=eng)
    s = Session()
    _seed(
        s,
        nodes=[(1, 101, "done"), (2, 102, "done"), (3, 103, "blocked"), (4, 104, "done")],
        reports=[(1, 101, 1), (2, 104, 1)],
        missions=(101, 102, 103, 104),
    )
    yield s
    s.close()


def _queue_rows(db):
    return db.execute(text(f"SELECT * FROM {QUEUE_TABLE} ORDER BY mission_id")).mappings().all()


def _backdate(db, mission_id: int, hours: float):
    """Age one pending ticket (and re-arm it for escalation)."""
    created = _utcnow() - timedelta(hours=hours)
    db.execute(
        text(f"UPDATE {QUEUE_TABLE} SET created_at = :c, due_at = :d, "
             "escalation_level = 0, escalated_at = NULL WHERE mission_id = :m"),
        {"c": created.isoformat(sep=" "),
         "d": (created + timedelta(hours=SLA_HOURS_DEFAULT)).isoformat(sep=" "),
         "m": mission_id},
    )
    db.commit()


# ─────────────────────── T1: SLA stamped on ticket ───────────────

def test_dispatch_stamps_72h_sla(db):
    out = dispatch_missing_reports(db, org_id=1)
    assert out["candidates"] == 2          # mission 102 (done) + 103 (blocked)
    tickets = _queue_rows(db)
    assert len(tickets) == 2
    for t in tickets:
        assert t["sla_hours"] == 72
        created = rg_sql_naive(t["created_at"])
        due = rg_sql_naive(t["due_at"])
        assert due is not None, "SLA ticket must carry a deadline"
        assert abs((due - created).total_seconds() - 72 * 3600) < 2
        assert t["escalation_level"] == 0
    assert out["sla"]["sla_hours"] == 72


def rg_sql_naive(value):
    from audit.report_reconcile import _as_naive_utc
    return _as_naive_utc(value)


# ────────────────── T2: aging buckets / overdue counts ───────────

def test_sla_aging_and_overdue(db):
    dispatch_missing_reports(db, org_id=1)          # 2 tickets
    _backdate(db, 102, 10)                          #  10h → <=24h
    _backdate(db, 103, 80)                          #  80h → >72h, overdue
    report = sla_report(db, 1)
    assert report["pending"] == 2
    assert report["overdue"] == 1
    assert report["aging_hours"]["<=24h"] == 1
    assert report["aging_hours"][">72h"] == 1
    assert report["max_age_hours"] >= 79
    assert report["overdue_tickets"][0]["mission_id"] == 103


# ────────────────── T3: escalation is idempotent ─────────────────

def test_escalate_overdue_once(db):
    dispatch_missing_reports(db, org_id=1)
    _backdate(db, 103, 80)
    first = escalate_overdue(db, 1)
    assert first["escalated"] == 1
    row = [t for t in _queue_rows(db) if t["mission_id"] == 103][0]
    assert row["priority"] == "urgent"
    assert row["escalation_level"] == 1
    assert row["escalated_at"] is not None
    second = escalate_overdue(db, 1)
    assert second["escalated"] == 0, "second pass must not double-bump"
    row2 = [t for t in _queue_rows(db) if t["mission_id"] == 103][0]
    assert row2["escalation_level"] == 1


# ── T4: legacy rows (due_at NULL) are judged, not exempted ───────

def test_legacy_ticket_without_due_at_is_not_exempt(db):
    dispatch_missing_reports(db, org_id=1)
    db.execute(text(f"UPDATE {QUEUE_TABLE} SET due_at = NULL, sla_hours = 72, "
                    "created_at = :c WHERE mission_id = 103"),
               {"c": (_utcnow() - timedelta(hours=100)).isoformat(sep=" ")})
    db.commit()
    report = sla_report(db, 1)
    assert report["overdue"] == 1, "112-era ticket without due_at must still age out"
    assert report["overdue_tickets"][0]["sla_hours"] == 72


# ─────────── T5: coverage is a node ratio (spec formula) ─────────

def test_coverage_is_node_ratio():
    eng = _make_engine()
    db = sessionmaker(bind=eng)()
    # two terminal nodes share ONE mission; that mission has one report
    _seed(db, nodes=[(1, 201, "done"), (2, 201, "done"), (3, 202, "done")],
          reports=[(1, 201, 1)], missions=(201, 202))
    result = reconcile(db, 1)
    assert result.kpi["coverage_basis"] == "node"
    assert result.kpi["nodes_with_report"] == 2
    assert result.kpi["missions_with_report"] == 1
    # spec: done_with_report / (done_with_report + done_no_report)
    ledger_done_r = 2
    ledger_done_n = len(result.done_no_report)
    assert ledger_done_n == 1
    expected = round(ledger_done_r / (ledger_done_r + ledger_done_n) * 100, 2)
    assert result.report_coverage_rate == expected == 66.67
    # the spec's own form is reported verbatim alongside the terminal rate
    assert result.kpi["done_with_report"] == 2
    assert result.kpi["done_no_report"] == 1
    assert result.kpi["report_coverage_rate_done"] == 66.67
    assert result.kpi["nodes_without_mission"] == 0
    db.close()


# ─────────── T6: gate carries SLA breach without masking ─────────

def test_gate_status_exposes_sla_breach(db):
    dispatch_missing_reports(db, org_id=1)
    _backdate(db, 103, 90)
    result = reconcile(db, 1)
    gate = gate_status(result, sla=sla_report(db, 1))
    assert gate["status"] == "RED"
    assert gate["debt_nodes"] == 2          # debt still fully counted
    assert gate["sla"]["breach"] is True
    assert gate["sla"]["overdue"] == 1


# ────────── T7/T8: structural read-only guard on business tables ─

def test_read_only_guard_blocks_business_writes(tmp_path):
    db_path = tmp_path / "guard.db"
    eng = create_engine(f"sqlite:///{db_path}")
    with eng.connect() as conn:
        for ddl in SCHEMA_SQL:
            conn.execute(text(ddl))
        conn.commit()
    with pytest.raises(rg.ReadOnlyViolation):
        with rg.guarded_session(eng) as s:
            s.execute(text("INSERT INTO campaign_nodes (id, plan_id, mission_id, name, status) "
                           "VALUES (99, 1, 999, 'node-999', 'done')"))
    with rg.guarded_session(eng):
        pass
    with eng.connect() as conn:                     # nothing leaked to disk
        assert conn.execute(text("SELECT COUNT(*) FROM campaign_nodes")).scalar() == 0


def test_read_only_guard_allows_outbox_whitelist(tmp_path):
    db_path = tmp_path / "guard2.db"
    eng = create_engine(f"sqlite:///{db_path}")
    with eng.connect() as conn:
        for ddl in SCHEMA_SQL:
            conn.execute(text(ddl))
        conn.commit()
    with rg.guarded_session(eng, allow_outbox=True) as s:
        # SELECT on business tables is fine; the outbox write is whitelisted
        assert s.execute(text("SELECT COUNT(*) FROM campaign_nodes")).scalar() == 0
        s.execute(text("CREATE TABLE IF NOT EXISTS tensor_report_supplement_queue ("
                       "dispatch_id TEXT PRIMARY KEY, organization_id BIGINT, dispatch_key TEXT, "
                       "dispatch_type TEXT, mission_id BIGINT, node_id BIGINT, plan_id BIGINT, "
                       "priority TEXT, status TEXT, created_at TIMESTAMP, resolved_at TIMESTAMP, "
                       "note TEXT, sla_hours INTEGER, due_at TIMESTAMP, escalation_level INTEGER, "
                       "escalated_at TIMESTAMP, UNIQUE (organization_id, dispatch_key))"))
        s.commit()
    with pytest.raises(rg.ReadOnlyViolation):        # KPI table not whitelisted here
        with rg.guarded_session(eng, allow_outbox=True) as s:
            s.execute(text("INSERT INTO tensor_guide_tracker (organization_id) VALUES (1)"))


# ─────────────── T9/T10: CLI artifact + integrity ────────────────

def _cli_db(tmp_path, seed_debt: bool):
    db_path = tmp_path / "cli.db"
    eng = create_engine(f"sqlite:///{db_path}")
    Session = sessionmaker(bind=eng)
    s = Session()
    with eng.connect() as conn:
        for ddl in SCHEMA_SQL:
            conn.execute(text(ddl))
        conn.commit()
    if seed_debt:
        _seed(s, nodes=[(1, 301, "done"), (2, 302, "done")], reports=[(1, 301, 1)],
              missions=(301, 302))
    else:
        _seed(s, nodes=[(1, 301, "done")], reports=[(1, 301, 1)], missions=(301,))
    s.close()
    return db_path


def test_cli_red_artifact_is_self_hashing(tmp_path):
    db_path = _cli_db(tmp_path, seed_debt=True)
    out = tmp_path / "artifact.json"
    code = rg.main(["--db-url", f"sqlite:///{db_path}", "--org", "1",
                    "--out", str(out), "--no-event-log", "--quiet"])
    assert code == rg.EXIT_RED
    art = json.loads(out.read_text())
    assert art["schema_version"] == "report-gate/1.0"
    assert art["ledger"]["done_no_report"] == 1
    assert art["kpi"]["coverage_basis"] == "node"
    assert rg.verify_artifact(art) is True
    assert len(art["integrity"]["payload_sha256"]) == 64
    assert art["provenance"]["engine_sha256"] and art["provenance"]["tool_sha256"]
    assert art["provenance"]["db"]["scheme"] == "sqlite"


def test_cli_green_exits_zero(tmp_path):
    db_path = _cli_db(tmp_path, seed_debt=False)
    out = tmp_path / "green.json"
    code = rg.main(["--db-url", f"sqlite:///{db_path}", "--org", "1",
                    "--out", str(out), "--no-event-log", "--quiet"])
    assert code == rg.EXIT_GREEN
    art = json.loads(out.read_text())
    assert art["gate"]["status"] == "GREEN"
    assert art["kpi"]["report_coverage_rate"] == 100.0


def test_cli_artifact_tamper_is_detected(tmp_path):
    db_path = _cli_db(tmp_path, seed_debt=True)
    out = tmp_path / "tamper.json"
    rg.main(["--db-url", f"sqlite:///{db_path}", "--org", "1", "--out", str(out), "--no-event-log", "--quiet"])
    art = json.loads(out.read_text())
    assert rg.verify_artifact(art) is True
    art["ledger"]["done_no_report"] = 0        # forge a clean ledger
    assert rg.verify_artifact(art) is False


# ───────── T11/T12: reverse scan vs false alarms, negative control ─

def test_reverse_scan_separates_phantom_from_orphan():
    eng = _make_engine()
    db = sessionmaker(bind=eng)()
    _seed(db, nodes=[(1, 401, "done")], reports=[(1, 401, 1), (2, 999999, 1), (3, 402, 1)],
          missions=(401, 402))
    result = reconcile(db, 1)
    assert [e.mission_id for e in result.report_no_node] == [999999]
    assert [e.mission_id for e in result.report_orphan_mission] == [402]
    gate = gate_status(result)
    assert gate["forged_reports"] == 1
    assert gate["status"] == "RED"
    # an orphan (plain non-campaign mission report) must NOT gate on its own
    db2 = sessionmaker(bind=_make_engine())()
    _seed(db2, nodes=[(1, 401, "done")], reports=[(1, 401, 1), (3, 402, 1)], missions=(401, 402))
    gate2 = gate_status(reconcile(db2, 1))
    assert gate2["forged_reports"] == 0 and gate2["status"] == "GREEN"
    db.close(); db2.close()


def test_negative_control_no_debt_means_green(db):
    # fixture has 2 debt nodes; close them by inserting reports → gate must flip
    db.execute(text("INSERT INTO tensor_reports (id, mission_id, organization_id, status, title) "
                    "VALUES (90, 102, 1, 'published', 'r90'), (91, 103, 1, 'published', 'r91')"))
    db.commit()
    result = reconcile(db, 1)
    assert result.report_coverage_rate == 100.0
    assert gate_status(result)["status"] == "GREEN"


# ─────────────────── T13: dispatch idempotency ───────────────────

def test_dispatch_idempotent(db):
    first = dispatch_missing_reports(db, org_id=1)
    second = dispatch_missing_reports(db, org_id=1)
    assert first["dispatched"] == 2
    assert second["dispatched"] == 0
    assert second["already_queued"] == 2
    assert len(_queue_rows(db)) == 2


def test_insert_dispatch_reraises_real_errors(tmp_path):
    """A failed insert that is NOT a UNIQUE race must surface, not vanish."""
    db_path = tmp_path / "err.db"
    eng = create_engine(f"sqlite:///{db_path}")
    db = sessionmaker(bind=eng)()
    # no outbox table at all → INSERT fails and the row is still absent → raise
    from audit.report_reconcile import ReconcileEntry
    entry = ReconcileEntry(category="done_no_report", node_id=1, mission_id=42,
                           report_id=None, plan_id=1, node_name="node-alpha",
                           node_status="done")
    with pytest.raises(Exception):
        _insert_dispatch(db, 1, entry, "normal")
    db.close()


# ── T17: compaction shrinks bytes but never a number ─────────────

def test_compact_artifact_preserves_numbers(tmp_path):
    """20 debt nodes: truncation must shrink the file and keep every count."""
    db_path = tmp_path / "many.db"
    eng = create_engine(f"sqlite:///{db_path}")
    s_ = sessionmaker(bind=eng)()
    with eng.connect() as conn:
        for ddl in SCHEMA_SQL:
            conn.execute(text(ddl))
        conn.commit()
    nodes = [(i, 500 + i, "done") for i in range(1, 21)]
    _seed(s_, nodes=nodes, reports=[(1, 501, 1)], missions=[500 + i for i in range(1, 21)])
    s_.close()
    out = tmp_path / "full.json"
    rg.main(["--db-url", f"sqlite:///{db_path}", "--org", "1", "--out", str(out),
             "--no-event-log", "--quiet"])
    full = json.loads(out.read_text())
    assert len(full["gate"]["clickable"]) == 19
    small = rg.compact_artifact(full, max_clickable=2, max_samples=1)
    assert rg.verify_artifact(small) is True
    for key in ("done_no_report", "blocked_no_report", "report_no_node",
                "coverage_rate"):
        assert small["ledger"][key] == full["ledger"][key]
    assert small["kpi"]["report_coverage_rate"] == full["kpi"]["report_coverage_rate"]
    assert small["gate"]["debt_nodes"] == full["gate"]["debt_nodes"] == 19
    assert len(small["gate"]["clickable"]) == 2
    assert small["compaction"]["clickable_total"] == 19
    assert small["compaction"]["clickable_dropped"] == 17
    assert small["integrity"]["payload_sha256"] != full["integrity"]["payload_sha256"]
    assert len(json.dumps(small)) < len(json.dumps(full))


# ── T16: event mailbox is append-only and pins the artifact ──────

def test_event_log_is_append_only_and_pinned(tmp_path):
    db_path = _cli_db(tmp_path, seed_debt=True)
    out = tmp_path / "ev.json"
    events = tmp_path / "events.jsonl"
    rg.main(["--db-url", f"sqlite:///{db_path}", "--org", "1", "--out", str(out),
             "--event-log", str(events), "--quiet"])
    rg.main(["--db-url", f"sqlite:///{db_path}", "--org", "1", "--out", str(out),
             "--event-log", str(events), "--quiet"])
    lines = [json.loads(x) for x in events.read_text().splitlines() if x.strip()]
    assert len(lines) == 2, "the mailbox is append-only: one line per run"
    art = json.loads(out.read_text())
    for ev in lines:
        assert ev["event_type"] == "report_gate.reconciliation"
        assert ev["gate_status"] == "RED"
        assert ev["debt_nodes"] == 1
        assert len(ev["artifact_sha256"]) == 64
    # each event pins the artifact of its own run; the newest line matches the file
    assert lines[-1]["artifact_sha256"] == art["integrity"]["payload_sha256"]
    assert lines[0]["artifact_sha256"] != lines[1]["artifact_sha256"], (
        "generated_at is part of the digest, so distinct runs must not collide")
    # dispatch runs are typed differently so the dispatcher can filter them
    rg.main(["--db-url", f"sqlite:///{db_path}", "--org", "1", "--out", str(out),
             "--dispatch", "--event-log", str(events), "--quiet"])
    lines = [json.loads(x) for x in events.read_text().splitlines() if x.strip()]
    assert lines[-1]["event_type"] == "report_gate.supplement_dispatch"
    assert lines[-1]["dispatched"] == 1
    # and the log can be switched off
    rg.main(["--db-url", f"sqlite:///{db_path}", "--org", "1", "--out", str(out),
             "--event-log", str(events), "--no-event-log", "--quiet"])
    assert len(events.read_text().splitlines()) == 3


# ──────── T15: KPI trend normalizes mixed-unit history ───────────

def test_coverage_trend_normalizes_ratio_coded_rows(db):
    from audit.report_reconcile import coverage_trend, KPI_METRIC, snapshot_coverage
    db.execute(text("""CREATE TABLE IF NOT EXISTS tensor_guide_tracker (
        id INTEGER PRIMARY KEY AUTOINCREMENT, organization_id INTEGER, metric_key TEXT,
        metric_value REAL, target_value REAL, notes TEXT,
        snapshot_at TEXT DEFAULT (datetime('now')))"""))
    db.execute(text("INSERT INTO tensor_guide_tracker "
                    "(organization_id, metric_key, metric_value, target_value, notes) "
                    "VALUES (1, :k, 0.730101, 1.0, 'legacy ratio-coded'), "
                    "(1, :k, 76.19, 100.0, 'percent-coded')"),
               {"k": KPI_METRIC})
    db.commit()
    snap = snapshot_coverage(db, 1, result=reconcile(db, 1))
    assert snap["metric_value"] is not None
    trend = coverage_trend(db, 1)
    scales = [p["scale"] for p in trend["series"]]
    assert "ratio->percent" in scales and "percent" in scales
    legacy = [p for p in trend["series"] if p["raw_value"] == 0.730101]
    assert legacy, "legacy point dropped from the series"
    assert legacy[0]["value"] == 73.01, f"ratio row must read as percent, got {legacy[0]['value']}"
    assert all(p["unit"] == "percent" and p["target"] == 100.0 for p in trend["series"])


# ────────────────── T14: zero domain semantics ───────────────────

def test_no_domain_semantics_in_implementation():
    forbidden = ("resistance", "propulsion", "seakeeping", "manoeuvring",
                 "maneuvering", "stability", "cavitation", "wigley", "hull")
    targets = [
        os.path.join(ROOT, "tools", "reconciliation", "report_gate.py"),
        os.path.join(ROOT, "tools", "reconciliation", "pg_snapshot.py"),
        os.path.join(ROOT, "backend", "audit", "report_reconcile.py"),
        os.path.join(ROOT, "backend", "migrations", "113_report_supplement_sla.sql"),
    ]
    for path in targets:
        text_body = open(path, encoding="utf-8").read()
        lowered = text_body.lower()
        hits = [w for w in forbidden if re.search(rf"\b{w}\b", lowered)]
        assert not hits, f"{os.path.basename(path)} leaks domain vocabulary: {hits}"