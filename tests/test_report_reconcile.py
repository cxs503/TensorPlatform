"""报告对账门（终态 ↔ 报告双向对账）验收用例 —— 根 tests/ 副本入口。

全部使用**合成**的 plan / node / mission 名（``synth-*``，纯整数 id），
不引入任何领域词条（见 ``test_no_domain_vocabulary``），只打
``backend/audit/report_reconcile.py`` 的真 SQL 路径与纯函数。

判据映射（与战役表 J1..J7 一致）
--------------------------------
J1  金标召回 100%（合成金标 26/26）
J2  已报告节点误报 ≤ 1（本用例断言 = 0）
J3  补报派单幂等：二次运行零新增
J4  凭空报告 / 串号（跨租户）报告 100% 拦截
J5  全量扫描 ≤ 30 s
J6  report_coverage_rate 入库且可随时读回（趋势线）
J7  负控两式必红：注入合成无报告 done 节点 / 注入串号报告
外加：门必须能失败（漏抓时召回 < 1），否则门只是装饰。
"""
from __future__ import annotations

import re
import sys
import time
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker

BACKEND = Path(__file__).resolve().parents[1] / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from audit import report_reconcile as rr  # noqa: E402

ORG = 1
OTHER_ORG = 2

BASE_SCHEMA = [
    "CREATE TABLE campaign_plans (id INTEGER PRIMARY KEY, organization_id INTEGER, name TEXT)",
    """CREATE TABLE campaign_nodes (
        id INTEGER PRIMARY KEY AUTOINCREMENT, plan_id INTEGER, milestone_id INTEGER,
        mission_id INTEGER, name TEXT NOT NULL, node_type TEXT NOT NULL DEFAULT 'task',
        dependencies TEXT, status TEXT NOT NULL DEFAULT 'pending',
        metadata TEXT NOT NULL DEFAULT '{}', created_at TEXT, updated_at TEXT)""",
    """CREATE TABLE tensor_dev_missions (
        id INTEGER PRIMARY KEY AUTOINCREMENT, organization_id INTEGER, goal TEXT,
        repo TEXT NOT NULL DEFAULT 'SynthRepo', base_ref TEXT NOT NULL DEFAULT 'main',
        status TEXT NOT NULL DEFAULT 'queued', created_by INTEGER,
        claimed_by TEXT, claimed_user_id INTEGER, created_at TEXT)""",
    """CREATE TABLE tensor_reports (
        id INTEGER PRIMARY KEY AUTOINCREMENT, organization_id INTEGER, title TEXT,
        summary TEXT NOT NULL DEFAULT '', scenario_key TEXT, mission_id INTEGER,
        stage TEXT, tags TEXT NOT NULL DEFAULT '[]', markdown TEXT NOT NULL DEFAULT '',
        status TEXT NOT NULL DEFAULT 'published', created_by INTEGER NOT NULL DEFAULT 1,
        created_at TEXT)""",
    """CREATE TABLE tensor_guide_tracker (
        id INTEGER PRIMARY KEY AUTOINCREMENT, organization_id INTEGER NOT NULL,
        metric_key TEXT NOT NULL, metric_value REAL NOT NULL DEFAULT 0,
        target_value REAL, snapshot_at TEXT NOT NULL DEFAULT '2026-01-01 00:00:00',
        notes TEXT NOT NULL DEFAULT '')""",
]


def _build_schema(conn) -> None:
    for stmt in BASE_SCHEMA:
        conn.execute(text(stmt))
    conn.execute(text(rr.QUEUE_DDL))  # migration 112 parity, same text


@pytest.fixture()
def db(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'reconcile.db'}", future=True)
    with engine.begin() as conn:
        _build_schema(conn)
    session = sessionmaker(bind=engine, future=True)()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


def _add_plan(db, plan_id, org=ORG, name=None):
    db.execute(text("INSERT INTO campaign_plans (id, organization_id, name) VALUES (:i, :o, :n)"),
               {"i": plan_id, "o": org, "n": name or f"synth-plan-{plan_id}"})
    db.commit()


def _add_node(db, plan_id, mission_id, name, status="done"):
    row = db.execute(
        text("INSERT INTO campaign_nodes (plan_id, mission_id, name, node_type, status, metadata) "
             "VALUES (:p, :m, :n, 'task', :s, '{}') RETURNING id"),
        {"p": plan_id, "m": mission_id, "n": name, "s": status},
    ).mappings().first()
    db.commit()
    return row["id"]


def _add_report(db, mission_id, org=ORG, title="synth-report"):
    row = db.execute(
        text("INSERT INTO tensor_reports (organization_id, title, summary, mission_id, status, "
             "markdown, created_by) VALUES (:o, :t, '', :m, 'published', '# synth', 1) RETURNING id"),
        {"o": org, "t": title, "m": mission_id},
    ).mappings().first()
    db.commit()
    return row["id"]


def _add_mission(db, mission_id, org=ORG):
    db.execute(text("INSERT INTO tensor_dev_missions (id, organization_id, goal, repo) "
                    "VALUES (:i, :o, 'synth goal', 'SynthRepo')"),
               {"i": mission_id, "o": org})
    db.commit()


@pytest.fixture()
def cohort(db):
    """
    合成队列：112 个节点 × 3 个 plan
      - 65 个终态节点：有报告
      - 26 个终态节点：无报告  ← 金标真值
      - 21 个非终态（in-flight）节点：无报告（门不该抓）
    """
    plan_ids = [9001, 9002, 9003]
    for pid in plan_ids:
        _add_plan(db, pid)

    reported: list[tuple[int, int]] = []   # (node_id, mission_id)
    gold: list[int] = []                   # node ids of terminal zero-report nodes
    inflight: list[int] = []
    mission_seq = 700000

    idx = 0
    for bucket, count, status in (("reported", 65, "done"), ("gold", 26, "done"), ("inflight", 21, "in-flight")):
        for _ in range(count):
            idx += 1
            mission_seq += 1
            _add_mission(db, mission_seq)
            node_id = _add_node(db, plan_ids[idx % 3], mission_seq,
                                f"synth-node-{idx:03d}", status=status)
            if bucket == "reported":
                _add_report(db, mission_seq, title=f"synth-report-{idx:03d}")
                reported.append((node_id, mission_seq))
            elif bucket == "gold":
                gold.append(node_id)
            else:
                inflight.append(node_id)
    assert len(reported) + len(gold) + len(inflight) == 112
    return {"plans": plan_ids, "reported": reported, "gold": gold, "inflight": inflight}


# ───────────────────────────── J1 / J2 ─────────────────────────────

def test_j1_gold_recall_100pct(db, cohort):
    ev = rr.evaluate_gold_standard(db, cohort["gold"])
    assert ev["gold_total"] == 26
    assert ev["recall"] == 1.0, ev
    assert ev["false_negatives"] == []
    assert ev["detected_total"] == 26


def test_j2_no_false_positive_on_reported_nodes(db, cohort):
    res = rr.reconcile(db)
    detected = {e.node_id for e in res.done_no_report + res.blocked_no_report}
    reported_ids = {nid for nid, _ in cohort["reported"]}
    assert not (detected & reported_ids), "reported nodes must never be flagged"
    assert len(detected - set(cohort["gold"])) <= 1


def test_plan_scope_limits_scan(db, cohort):
    scoped = rr.reconcile(db, plan_ids=[cohort["plans"][0]])
    assert scoped.scope["plan_ids"] == [cohort["plans"][0]]
    assert scoped.total_terminal_nodes < 112
    assert all(e.plan_id == cohort["plans"][0] for e in scoped.done_no_report)
    whole = rr.reconcile(db)
    assert whole.total_terminal_nodes == 91  # 65 reported + 26 gold (in-flight excluded)


def test_include_non_terminal_extends_scan(db, cohort):
    res = rr.reconcile(db, include_non_terminal=True)
    assert res.total_terminal_nodes == 112
    detected = {e.node_id for e in res.done_no_report + res.blocked_no_report}
    assert set(cohort["inflight"]) <= detected


# ─────────────────────────────── J3 ────────────────────────────────

def test_j3_dispatch_idempotent(db, cohort):
    first = rr.dispatch_missing_reports(db, ORG)
    assert first["candidates"] == 26
    assert first["dispatched"] == 26
    second = rr.dispatch_missing_reports(db, ORG)
    assert second["dispatched"] == 0, "second run must create zero new tickets"
    assert second["already_queued"] == 26
    rows = db.execute(text(f"SELECT COUNT(*) FROM {rr.QUEUE_TABLE}")).scalar()
    assert rows == 26


def test_dispatch_dry_run_writes_nothing(db, cohort):
    dry = rr.dispatch_missing_reports(db, ORG, dry_run=True)
    assert dry["candidates"] == 26 and dry["dispatched"] == 0
    assert db.execute(text(f"SELECT COUNT(*) FROM {rr.QUEUE_TABLE}")).scalar() == 0


def test_dispatch_autoclose_after_report(db, cohort):
    rr.dispatch_missing_reports(db, ORG)
    target_node, target_mission = cohort["gold"][0], None
    row = db.execute(text("SELECT mission_id FROM campaign_nodes WHERE id = :n"), {"n": target_node}).first()
    target_mission = row[0]
    _add_report(db, target_mission, title="synth-backfill")
    closed = rr.close_resolved_dispatches(db, ORG)
    assert closed["resolved"] == 1
    after = rr.reconcile(db)
    assert target_node not in {e.node_id for e in after.done_no_report}


# ─────────────────────────────── J4 ────────────────────────────────

def test_j4_phantom_and_cross_tenant_reports_intercepted(db, cohort):
    phantom_id = _add_report(db, 999999)                     # mission row does not exist
    real_mission = cohort["reported"][0][1]
    cross_id = _add_report(db, real_mission, org=OTHER_ORG)  # 串号：另一租户的报告

    res = rr.reconcile(db)
    phantom = {e.report_id for e in res.report_no_node}
    mismatch = {e.report_id for e in res.report_hash_mismatch}
    assert phantom_id in phantom
    assert cross_id in mismatch
    gate = rr.gate_status(res)
    assert gate["status"] == "RED"
    assert gate["forged_reports"] == 2


def test_j4_orphan_mission_report_is_not_gated(db, cohort):
    """A report from a plain (non-campaign) mission is informational, never red."""
    _add_mission(db, 888888)
    orphan_id = _add_report(db, 888888)
    res = rr.reconcile(db)
    assert orphan_id in {e.report_id for e in res.report_orphan_mission}
    assert orphan_id not in {e.report_id for e in res.report_no_node}


# ─────────────────────────────── J5 ────────────────────────────────

def test_j5_full_scan_under_30s(db, cohort):
    t0 = time.monotonic()
    res = rr.reconcile(db)
    elapsed = time.monotonic() - t0
    assert elapsed < 30.0, f"reconcile took {elapsed:.2f}s"
    assert res.elapsed_seconds < 30.0


# ─────────────────────────────── J6 ────────────────────────────────

def test_j6_kpi_persisted_and_readable(db, cohort):
    res = rr.reconcile(db)
    snap = rr.snapshot_coverage(db, ORG, result=res, notes="synth-wave")
    assert snap["snapshot_id"] is not None
    trend = rr.coverage_trend(db, ORG)
    assert trend["points"] == 1
    assert trend["series"][-1]["value"] == pytest.approx(res.report_coverage_rate)
    assert "debt=26" in trend["series"][-1]["notes"]


def test_kpi_rate_math(db, cohort):
    res = rr.reconcile(db)
    # 91 terminal nodes, 65 with report
    assert res.report_coverage_rate == pytest.approx(round(65 / 91 * 100, 2))


# ─────────────────────────────── J7 ────────────────────────────────

def test_j7_negative_control_injected_done_node_is_caught(db, cohort):
    baseline = len(rr.reconcile(db).done_no_report)
    node_id = rr.inject_synthetic_done_node(db, cohort["plans"][0], 777777, name="synth-neg-ctrl")
    try:
        res = rr.reconcile(db)
        assert len(res.done_no_report) == baseline + 1
        entry = [e for e in res.done_no_report if e.node_id == node_id]
        assert entry and entry[0].mission_id == 777777
        gate = rr.gate_status(res)
        assert gate["status"] == "RED"
        assert any(c["node_id"] == node_id for c in gate["clickable"]), "red gate must be clickable"
    finally:
        rr.cleanup_synthetic(db, node_id=node_id)


def test_j7_negative_control_injected_report_is_caught(db, cohort):
    _add_mission(db, 666666)  # mission exists, but no campaign node carries it
    report_id = rr.inject_synthetic_report(db, 666666, org_id=ORG, title="synth-orphan")
    try:
        res = rr.reconcile(db)
        assert report_id in {e.report_id for e in res.report_orphan_mission}
        phantom_report = rr.inject_synthetic_report(db, 555555, org_id=ORG, title="synth-phantom")
        res2 = rr.reconcile(db)
        assert phantom_report in {e.report_id for e in res2.report_no_node}
        rr.cleanup_synthetic(db, report_id=phantom_report)
    finally:
        rr.cleanup_synthetic(db, report_id=report_id)


# ──────────── J4 延伸：归属复核复用 p130 提交时工装（接线） ──────────

def _claim(db, mission_id, user_id, claimed_by):
    db.execute(text("UPDATE tensor_dev_missions SET claimed_user_id = :u, claimed_by = :c "
                    "WHERE id = :m"), {"u": user_id, "c": claimed_by, "m": mission_id})
    db.commit()


def test_ownership_recheck_flags_foreign_submission(db, cohort):
    node_id, mission_id = cohort["reported"][1]
    _claim(db, mission_id, 7, "synth-owner")
    leg = rr.reconcile(db)
    assert not [e for e in leg.report_hash_mismatch if e.mission_id == mission_id], \
        "ownership re-check must stay opt-in (legacy rows are not retro-judged)"
    chk = rr.reconcile(db, verify_ownership=True)
    hits = [e for e in chk.report_hash_mismatch if e.mission_id == mission_id]
    assert hits, "report filed by a non-claimant must be flagged"
    assert "OWNERSHIP" in hits[0].detail or "NOT_OWNED" in hits[0].detail
    assert chk.kpi["ownership"]["flagged"] >= 1


def test_ownership_recheck_accepts_real_claimant(db, cohort):
    node_id, mission_id = cohort["reported"][2]
    _claim(db, mission_id, 1, "synth-claimant")
    db.execute(text("UPDATE tensor_reports SET created_by = 1 WHERE mission_id = :m"),
               {"m": mission_id})
    db.commit()
    chk = rr.reconcile(db, verify_ownership=True)
    assert not [e for e in chk.report_hash_mismatch if e.mission_id == mission_id]


def test_ownership_recheck_survives_missing_tooling(db, cohort, monkeypatch):
    """工具缺失时降级为「未启用」，绝不因接线失败而把门判成绿。"""
    import sys as _sys
    monkeypatch.setitem(_sys.modules, "services.report_ownership", None)
    chk = rr.reconcile(db, verify_ownership=True)
    assert chk.kpi["ownership"]["available"] is False


# ──────────────────────── 门必须能失败 ────────────────────────────

def test_gate_can_fail_when_detector_misses(db, cohort):
    """A gold id outside the scan scope must surface as a false negative."""
    ev = rr.evaluate_gold_standard(db, cohort["gold"] + [123456])
    assert ev["recall"] < 1.0
    assert 123456 in ev["false_negatives"]


def test_zero_domain_vocabulary():
    """静态扫描：模块/API/迁移只碰 mission & report 元数据。"""
    forbidden = [
        "ship", "hull", "propeller", "ice", "wave", "resistance", "seakeeping",
        "manoeuvr", "maneuver", "stability", "cavitation", "hullform",
        "船舶", "螺旋桨", "冰区", "阻力", "耐波", "操纵", "稳性",
    ]
    # 生产面（模块 / API / 迁移）必须零领域词条；用例自身只允许 synth-* 合成名。
    targets = [
        BACKEND / "audit" / "report_reconcile.py",
        BACKEND / "api" / "tensor_reconcile.py",
        BACKEND / "migrations" / "112_report_reconcile_gate.sql",
    ]
    for path in targets:
        body = path.read_text(encoding="utf-8").lower()
        hits = []
        for word in forbidden:
            w = word.lower()
            # ASCII terms need word boundaries ("ownership" must not trip "ship");
            # CJK terms are matched as substrings.
            pattern = rf"\b{re.escape(w)}\b" if w.isascii() else re.escape(w)
            if re.search(pattern, body):
                hits.append(word)
        assert not hits, f"{path.name} leaks domain vocabulary: {hits}"

def test_fixtures_use_synthetic_names():
    """用例只造 synth-* 合成节点/报告名，不夹带任何真实案例。"""
    body = Path(__file__).read_text(encoding="utf-8")
    # 真实案例 id 形态（如两字母+数字+连字符）不得出现在用例里
    real_like = re.findall(r'"[A-Z]{2,3}\d{2,}-[A-Z0-9\-]+"', body)
    assert not real_like, real_like
    assert body.count("synth") >= 10, "fixtures must be synthetic"
