#!/usr/bin/env python3
"""coordinator_shadow v1 —— 脉搏层测试（IN27-H1）。

判据先于实现，逐条钉：
1. 只读门：写语句/多语句/``EXPLAIN ANALYZE`` 一律拒，``SELECT``/``WITH`` 放行；
2. 三路脉搏：chronicle s1–s8 取面与鲜度、daemon WARN 流聚簇、三个零流器官计数逐格对；
3. 看不见 ≠ 0：探针失败（表缺）→ ``value=None`` + ``withheld``，绝不填 0；
4. 无定义不按 0 处理：窗口内完工数为 0 → 声明率 ``None`` 且注明原因；
5. 数字集合（给 LLM 幻觉校验用）包含所有器官读数。
"""
from __future__ import annotations

import json
import pathlib
import sqlite3
import sys
from datetime import datetime, timedelta, timezone

import pytest

REPO = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from tools.coordinator_shadow import pulse as pu  # noqa: E402

AS_OF = datetime(2026, 11, 7, 12, 0, tzinfo=timezone.utc)

DDL = """
CREATE TABLE chronicle_snapshots (ts TEXT, payload TEXT);
CREATE TABLE tensor_dev_missions (
    id INTEGER PRIMARY KEY, organization_id INTEGER NOT NULL DEFAULT 1,
    goal TEXT NOT NULL DEFAULT '', repo TEXT NOT NULL DEFAULT 'TensorPlatform',
    base_ref TEXT NOT NULL DEFAULT 'main', budget TEXT NOT NULL DEFAULT '{}',
    status TEXT NOT NULL DEFAULT 'queued', claim_token TEXT, claimed_by TEXT,
    session_id INTEGER, result TEXT NOT NULL DEFAULT '{}', created_by INTEGER,
    created_at TEXT NOT NULL, heartbeat_at TEXT, finished_at TEXT,
    runner TEXT NOT NULL DEFAULT 'native', origin TEXT NOT NULL DEFAULT 'owner',
    model TEXT, parent_mission_id INTEGER, co_repos TEXT, task_key TEXT,
    report_owed INTEGER NOT NULL DEFAULT 0, report_id INTEGER);
CREATE UNIQUE INDEX ux_task_key ON tensor_dev_missions (organization_id, task_key);
CREATE TABLE outcome_evaluations (
    evaluation_id TEXT PRIMARY KEY, status TEXT NOT NULL, created_at TEXT NOT NULL,
    decided_at TEXT);
CREATE TABLE tensor_credit_ledger (
    id INTEGER PRIMARY KEY, organization_id INTEGER, model TEXT, delta INTEGER,
    reason TEXT, mission_id INTEGER, created_at TEXT);
CREATE TABLE campaign_nodes (id INTEGER PRIMARY KEY, status TEXT NOT NULL,
    node_type TEXT NOT NULL DEFAULT 'task');
CREATE TABLE tensor_capability_deltas (id INTEGER PRIMARY KEY, created_at TEXT);
CREATE TABLE tensor_reports (id INTEGER PRIMARY KEY, mission_id INTEGER, title TEXT);
"""


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).replace(microsecond=0).isoformat()


@pytest.fixture()
def db(tmp_path):
    path = tmp_path / "pulse.sqlite"
    con = sqlite3.connect(path)
    con.executescript(DDL)
    # chronicle：最新一拍 s1–s8 齐备（另一拍更老，验证「取最新」）
    older = {"schema": "chronicle/x", "s1_missions": {"queued": 3}}
    latest = {
        "schema": "chronicle/x",
        "s1_missions": {"queued": 7, "running": 4},
        "s2_campaign": {"blocked_nodes": 6},
        "s3_capability": {"deltas_24h": 12},
        "s4_fleet_pulse": {"workers_distinct_24h": 5},
        "s5_outcomes": {"coverage": 0.47},
        "s6_daemons": {"daemon_warn": 2},
        "s7_voice": {"declared": 3},
        "s8_harness": {"incidents": 1},
    }
    con.execute("INSERT INTO chronicle_snapshots VALUES (?,?)",
                (_iso(AS_OF - timedelta(hours=2)), json.dumps(older)))
    con.execute("INSERT INTO chronicle_snapshots VALUES (?,?)",
                (_iso(AS_OF - timedelta(minutes=12)), json.dumps(latest)))
    # 任务：2 个排队老单（>2h）、1 个新排队、1 个在跑、3 个 24h 内完工（2 个已声明）
    rows = [
        (1, "queued", _iso(AS_OF - timedelta(hours=5)), None, None),
        (2, "queued", _iso(AS_OF - timedelta(hours=3)), None, None),
        (3, "queued", _iso(AS_OF - timedelta(minutes=10)), None, None),
        (4, "running", _iso(AS_OF - timedelta(hours=1)), None, None),
        (5, "succeeded", _iso(AS_OF - timedelta(hours=30)), _iso(AS_OF - timedelta(hours=20)), 501),
        (6, "failed", _iso(AS_OF - timedelta(hours=28)), _iso(AS_OF - timedelta(hours=10)), None),
        (7, "succeeded", _iso(AS_OF - timedelta(hours=26)), _iso(AS_OF - timedelta(hours=2)), 502),
        (8, "succeeded", _iso(AS_OF - timedelta(hours=80)), _iso(AS_OF - timedelta(hours=70)), 503),
        (9, "succeeded", _iso(AS_OF - timedelta(hours=9)), _iso(AS_OF - timedelta(hours=5)), None),
    ]
    for mid, status, created, finished, report_id in rows:
        con.execute(
            "INSERT INTO tensor_dev_missions (id, status, created_at, finished_at, report_id) "
            "VALUES (?,?,?,?,?)", (mid, status, created, finished, report_id))
    # 信用积压：3 条定谳评价，只有 1 条入账 → 积压 2
    evals = [("e1", "confirmed"), ("e2", "rejected"), ("e3", "confirmed"), ("e4", "pending")]
    for eid, status in evals:
        con.execute("INSERT INTO outcome_evaluations (evaluation_id, status, created_at) VALUES (?,?,?)",
                    (eid, status, _iso(AS_OF - timedelta(hours=6))))
    con.execute("INSERT INTO tensor_credit_ledger (id, organization_id, model, delta, reason, created_at) "
                "VALUES (1,1,'m',5,'eval:e1|grade=S2',?)", (_iso(AS_OF - timedelta(hours=5)),))
    for i, status in enumerate(("pending", "in-flight", "blocked", "blocked", "done"), start=1):
        con.execute("INSERT INTO campaign_nodes (id, status) VALUES (?,?)", (i, status))
    # 放门停放的门（gate）不进 campaign_blocked 口径（诊断单 7a90c537e3805175）
    con.execute("INSERT INTO campaign_nodes (id, status, node_type) VALUES (6,'blocked','gate')")
    # 声明权威通道：m9 无 report_id，但 tensor_reports 里有它的报告 → 算已声明
    con.execute("INSERT INTO tensor_reports (id, mission_id, title) VALUES (1, 9, 'm9 报告')")
    con.execute("INSERT INTO tensor_capability_deltas (id, created_at) VALUES (1,?)",
                (_iso(AS_OF - timedelta(hours=3)),))
    con.commit()
    con.close()
    return path


def _runner(db, audit=None):
    return pu.ReadOnlyRunner(pu.SqliteRunner(db), audit=audit)


# ─────────────────────────── 1) 只读门 ───────────────────────────


def test_read_only_guard_rejects_writes():
    for sql in ("UPDATE t SET a=1", "INSERT INTO t VALUES (1)", "DELETE FROM t",
                "DROP TABLE t", "CREATE TABLE t (a int)", "SELECT 1; DELETE FROM t",
                "WITH x AS (UPDATE t SET a=1 RETURNING 1) SELECT * FROM x",
                "EXPLAIN ANALYZE SELECT 1", "  -- 注释\n DELETE FROM t"):
        with pytest.raises(pu.ReadOnlyViolation):
            pu.assert_read_only(sql)


def test_read_only_guard_allows_select_and_hides_keywords_in_literals():
    assert pu.assert_read_only("SELECT 1") == "SELECT 1"
    assert pu.assert_read_only("WITH a AS (SELECT 1) SELECT * FROM a").startswith("WITH")
    # 字符串里含 update 不应误判（关键词扫描先剥字面量与注释）
    assert pu.assert_read_only("SELECT 'update done' AS note") == "SELECT '' AS note"
    assert pu.assert_read_only("-- update 注释\nSELECT 1") == "SELECT 1"


def test_read_only_runner_audits_and_blocks():
    inner = pu.SqliteRunner(":memory:")
    inner.conn.execute("CREATE TABLE t (a int)")
    runner = pu.ReadOnlyRunner(inner)
    runner.fetchall("SELECT count(*) FROM t", {})
    assert runner.audit == ["SELECT count(*) FROM t"]
    with pytest.raises(pu.ReadOnlyViolation):
        runner.fetchall("UPDATE t SET a=1", {})
    with pytest.raises(pu.ReadOnlyViolation):  # 只读通道没有写入口
        runner.execute("UPDATE t SET a=1")


# ─────────────────────────── 2) 三路脉搏 ───────────────────────────


def test_chronicle_facets_and_freshness(db):
    runner = _runner(db)
    pulse = pu.collect_pulse(runner, as_of=AS_OF, log_paths=[])
    assert pulse.chronicle["facets_present"] == list(pu.CHRONICLE_FACETS)
    assert pulse.chronicle["facets"]["s1_missions"]["queued"] == 7   # 取最新一拍，不是老的
    assert pulse.chronicle["age_minutes"] == pytest.approx(12.0, abs=0.01)
    assert pulse.chronicle["stale"] is False


def test_zero_flow_organs_exact_numbers(db):
    pulse = pu.collect_pulse(_runner(db), as_of=AS_OF, log_paths=[], stall_minutes=120)
    organs = pulse.organs
    # 收养停滞：5h 与 3h 两个老单超 120min → 2；最老 300 分钟
    assert organs["queued_stuck_2h"].value == 2
    assert organs["oldest_queued_minutes"].value == pytest.approx(300.0, abs=1.0)
    assert organs["queued_open"].value == 3
    assert organs["running"].value == 1
    # 信用积压：3 条定谳 - 1 条已入账 = 2
    assert organs["credit_backlog"].value == 2
    # 声明率：24h 内完工 4 条（m5 -20h、m6 -10h、m7 -2h、m9 -5h）；
    # 已声明 3 条（m5/m7 有 report_id，m9 有 tensor_reports 行，m6 没有）→ 3/4
    assert organs["declaration_rate"].extra["finished_24h"] == 4
    assert organs["declaration_rate"].extra["declared_24h"] == 3
    assert organs["declaration_rate"].value == pytest.approx(3 / 4, abs=1e-3)
    # 战役阻塞 2（task 级真阻塞）、能力增量 1；gate 停放不计入，但留档在 extra
    assert organs["campaign_blocked"].value == 2
    assert organs["campaign_blocked"].extra["blocked_total"] == 3
    assert organs["campaign_blocked"].extra["gate_held"] == 1
    assert organs["capability_deltas_24h"].value == 1


def test_declaration_rate_undefined_when_no_finished(db):
    con = sqlite3.connect(db)
    con.execute("DELETE FROM tensor_dev_missions")
    con.commit()
    con.close()
    pulse = pu.collect_pulse(_runner(db), as_of=AS_OF, log_paths=[], window_hours=24)
    rate = pulse.organs["declaration_rate"]
    assert rate.value is None                       # 无定义 ≠ 0%
    assert rate.extra["undefined_reason"] == "no-finished-missions-in-window"
    assert "organs.declaration_rate" not in pulse.numbers()


def test_probe_failure_is_withheld_not_zero(db):
    con = sqlite3.connect(db)
    con.execute("DROP TABLE tensor_capability_deltas")
    con.execute("DROP TABLE tensor_credit_ledger")
    con.commit()
    con.close()
    pulse = pu.collect_pulse(_runner(db), as_of=AS_OF, log_paths=[])
    assert pulse.organs["capability_deltas_24h"].value is None
    assert pulse.organs["capability_deltas_24h"].withheld == "probe-failed"
    assert pulse.organs["credit_backlog"].value is None
    assert any(w.startswith("credit_backlog:") for w in pulse.withheld)


def test_every_metric_carries_sql_for_recompute(db):
    pulse = pu.collect_pulse(_runner(db), as_of=AS_OF, log_paths=[])
    for name, metric in pulse.organs.items():
        assert metric.sql.strip(), f"{name} 缺 SQL 原文"
        assert " " in metric.sql
    # 参数化的窗口边界必须留痕（可复算）
    assert "cut" in pulse.organs["declaration_rate"].params


def test_warn_stream_clusters_by_normalized_signature(tmp_path):
    log = tmp_path / "dispatcher.log"
    base = AS_OF - timedelta(minutes=20)
    lines = []
    for i in range(4):
        lines.append(f"{_iso(base + timedelta(minutes=i))} WARN 孤儿收养单 draft 异常 IndexError mission={1000+i}")
    lines.append(f"{_iso(base)} ERROR 数据库连接池耗尽 pool=12")
    lines.append(f"{_iso(AS_OF - timedelta(hours=3))} WARN 老日志在窗口外 mission=1")
    lines.append("没有时间戳的一行 WARN 也要算进窗口")
    log.write_text("\n".join(lines) + "\n")

    stream = pu.read_warn_stream([log], AS_OF, window_minutes=60)
    # 窗口内：4 条同签名 WARN + 1 条无时间戳 WARN（算进窗口并单独记数）；ERROR 1 条；
    # 3 小时前那条落在窗口外，不计。
    assert stream["warn_total"] == 5
    assert stream["error_total"] == 1
    assert stream["undated_lines"] == 1
    sigs = {c.signature: c.count for c in stream["clusters"]}
    assert sigs["孤儿收养单 draft 异常 IndexError mission=#"] == 4      # 数字归一化后同签名
    assert sigs["数据库连接池耗尽 pool=#"] == 1                          # ERROR 簇 level=high
    assert [c.level for c in stream["clusters"] if c.signature.startswith("数据库")] == ["high"]
    assert stream["paths_read"] == [str(log)]


def test_warn_stream_missing_log_is_withheld(tmp_path):
    stream = pu.read_warn_stream([tmp_path / "nope.log"], AS_OF)
    assert stream["withheld"] == "no-log-readable"
    assert stream["warn_total"] is None       # 读不到 ≠ 0 告警


def test_pulse_numbers_include_organs_and_facets(db):
    pulse = pu.collect_pulse(_runner(db), as_of=AS_OF, log_paths=[])
    numbers = pulse.numbers()
    assert numbers["organs.credit_backlog"] == 2.0
    assert numbers["chronicle.s1_missions.queued"] == 7.0
    digest = pulse.digest()
    assert digest["credit_backlog"] == 2
    assert digest["chronicle_stale"] is False
    assert digest["adoption_stall_minutes"] == pytest.approx(300.0, abs=1.0)


def test_stale_pulse_flagged(db):
    pulse = pu.collect_pulse(_runner(db), as_of=AS_OF + timedelta(hours=3), log_paths=[])
    assert pulse.chronicle["stale"] is True
    assert pulse.chronicle["age_minutes"] > 90

def test_psycopg_placeholder_translation():
    """探针 SQL 用 :name 写（sqlite 原生/可读），Postgres 侧翻译成 %(name)s。

    这是实跑生产库时踩到的坑：psycopg 不认 :name，会直接 SyntaxError → 整批探针
    WITHHELD（看着像「平台没数据」，其实是影子自己写错了）。
    """
    sql = "SELECT count(*) FROM t WHERE created_at < CAST(:cut AS timestamptz) AND x::text = :v"
    assert pu.to_psycopg_placeholders(sql) == (
        "SELECT count(*) FROM t WHERE created_at < CAST(%(cut)s AS timestamptz) AND x::text = %(v)s")
    assert pu.to_psycopg_placeholders("SELECT payload::text FROM t") == "SELECT payload::text FROM t"
