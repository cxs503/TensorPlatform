"""cohort 不变量验收测试（IN38-N09）。

判据先于实现，逐条落测试：
1. cohort 成员资格只由 ``finished_at`` 决定（ISO 周，UTC，周一 00:00 起）。
2. 覆盖率分子只用已判定记录（confirmed|candidate）；pending/rejected 不进分子。
3. 分母为 0 → ``coverage is None`` 且在 ``undefined_metrics`` 里（禁止折成 0%）。
4. P90 时延 = ``min(decided_at) - finished_at``；``decided_at < finished_at`` 的样本剔出
   分位并单独计数。
5. 积压年龄从 ``finished_at`` 起算（不是记录 ``created_at``）。
6. 冻结 cohort：输入指纹不变而数变 → 记 drift 且不覆盖；输入变（迟到评价）→ revision+1。
"""
from __future__ import annotations

import pathlib
import sys

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.pool import StaticPool

REPO = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from tools.evaluation_ops import cohorts as ch  # noqa: E402

AS_OF = "2026-11-07T00:00:00+00:00"

MISSIONS_DDL = """
CREATE TABLE IF NOT EXISTS tensor_dev_missions (
    id              INTEGER PRIMARY KEY,
    organization_id INTEGER NOT NULL DEFAULT 1,
    repo            TEXT NOT NULL,
    status          TEXT NOT NULL DEFAULT 'succeeded',
    claimed_by      TEXT,
    finished_at     TEXT,
    report_id       INTEGER
);
"""

EVALS_DDL = """
CREATE TABLE IF NOT EXISTS outcome_evaluations (
    evaluation_id   TEXT PRIMARY KEY,
    artifact_id     TEXT NOT NULL,
    mission_id      BIGINT,
    contributor     TEXT,
    grade           TEXT,
    e_level         TEXT,
    grade_reason    TEXT,
    evidence_refs   TEXT DEFAULT '[]',
    reviewer        TEXT,
    reviewer_kind   TEXT,
    review_status   TEXT DEFAULT 'unreviewed',
    baseline_or_scope TEXT,
    boundary        TEXT,
    status          TEXT NOT NULL,
    reason_codes    TEXT DEFAULT '[]',
    reason_source   TEXT DEFAULT 'manual',
    repo            TEXT,
    organization_id BIGINT,
    window_finished_at TEXT,
    created_at      TEXT NOT NULL,
    decided_at      TEXT
);
"""


def make_conn():
    engine = create_engine("sqlite+pysqlite://", future=True,
                           connect_args={"check_same_thread": False}, poolclass=StaticPool)
    conn = engine.connect()
    for stmt in [s.strip() for s in (MISSIONS_DDL + EVALS_DDL).split(";") if s.strip()]:
        conn.execute(text(stmt))
    conn.commit()
    return conn


def add_mission(conn, mid: int, finished_at: str, repo: str = "SyntheticRepo"):
    conn.execute(text("INSERT INTO tensor_dev_missions (id, repo, status, claimed_by, finished_at) "
                      "VALUES (:id, :repo, 'succeeded', :cb, :f)"),
                 {"id": mid, "repo": repo, "cb": "agent-x", "f": finished_at})
    conn.commit()


def add_record(conn, mid: int, status: str, decided_at: str | None,
               created_at: str | None = None, eid: str | None = None):
    conn.execute(text(
        "INSERT INTO outcome_evaluations (evaluation_id, artifact_id, mission_id, status, "
        "decided_at, created_at) VALUES (:e, :a, :m, :s, :d, :c)"
    ), {"e": eid or f"ev-{mid}-{status}-{decided_at}", "a": f"mission:{mid}", "m": mid,
        "s": status, "d": decided_at, "c": created_at or decided_at or "2026-11-01T00:00:00+00:00"})
    conn.commit()


def load(conn, repo: str | None = None):
    return ch.fetch_missions(conn, repo=repo), ch.fetch_records(conn, repo=repo)


# ─────────────────────────── 1. cohort 定义 ───────────────────────────

def test_cohort_key_is_iso_week_utc():
    # 2026-11-01 是周日 → 与 2026-11-02（周一）分属不同 ISO 周
    assert ch.cohort_key("2026-11-01T23:59:59+00:00") == "2026-W44"
    assert ch.cohort_key("2026-11-02T00:00:00+00:00") == "2026-W45"
    assert ch.cohort_key("2026-10-30T12:00:00Z") == "2026-W44"
    assert ch.cohort_key("not-a-time") is None
    assert ch.cohort_key(None) is None


def test_week_bounds_are_monday_to_monday():
    start, end = ch.week_bounds("2026-W45")
    assert start.isoformat() == "2026-11-02"
    assert end.isoformat() == "2026-11-09"
    assert (end - start).days == 7


def test_membership_only_uses_finished_at():
    conn = make_conn()
    add_mission(conn, 1, "2026-11-03T10:00:00+00:00")
    add_mission(conn, 2, "2026-11-04T10:00:00+00:00")
    add_mission(conn, 3, "2026-11-06T10:00:00+00:00")
    missions, records = load(conn)
    # 读库层已滤掉 finished_at 为空的 mission
    assert all(m["finished_at"] for m in missions)
    # 直接喂入「未完结」行：不进任何 cohort，只计入 excluded（不静默吞掉）
    payload = list(missions) + [{"id": 4, "repo": "SyntheticRepo", "finished_at": None}]
    got = ch.compute_cohorts(payload, records, as_of=AS_OF)
    assert list(got) == ["2026-W45"]
    row = got["2026-W45"]
    assert row["missions_total"] == 3
    assert row["excluded"]["missing_finished_at"] == 1


# ─────────────────────────── 2. 覆盖率口径 ───────────────────────────

def test_coverage_numerator_only_decided():
    conn = make_conn()
    add_mission(conn, 1, "2026-11-03T10:00:00+00:00")
    add_mission(conn, 2, "2026-11-03T11:00:00+00:00")
    add_mission(conn, 3, "2026-11-03T12:00:00+00:00")
    add_mission(conn, 4, "2026-11-03T13:00:00+00:00")
    add_record(conn, 1, "confirmed", "2026-11-03T13:00:00+00:00")
    add_record(conn, 2, "candidate", "2026-11-03T14:00:00+00:00")
    add_record(conn, 3, "pending", None)
    add_record(conn, 4, "rejected", "2026-11-03T15:00:00+00:00")
    missions, records = load(conn)
    row = ch.compute_cohorts(missions, records, as_of=AS_OF)["2026-W45"]
    assert row["missions_total"] == 4
    assert row["missions_decided"] == 2          # confirmed + candidate
    assert row["missions_pending"] == 1          # pending 是积压，不进分子
    assert row["coverage_numerator"] == 2
    assert row["coverage_denominator"] == 4
    assert row["coverage"] == pytest.approx(0.5)
    assert row["pending_rows"] == 1


def test_zero_denominator_is_none_not_zero():
    """分母为 0 的量必须 None；分母非 0 的 0 是真值（0% 覆盖 ≠ 无定义）。"""
    conn = make_conn()
    add_mission(conn, 1, "2026-11-03T10:00:00+00:00")
    add_record(conn, 1, "pending", None)
    missions, records = load(conn)
    row = ch.compute_cohorts(missions, records, as_of=AS_OF)["2026-W45"]
    # 覆盖率分母 = 1（有 mission），分子 = 0 → 0.0 是有定义的「零覆盖」
    assert row["coverage_denominator"] == 1
    assert row["coverage"] == pytest.approx(0.0)
    # 时延分母 = 0（无已判定样本）→ None，且单列，不许折成 0h
    assert row["latency_n"] == 0
    assert row["p90_latency_hours"] is None
    assert "p90_latency_hours" in row["undefined_metrics"]
    # 积压年龄分母 = 1 行 pending，起点 finished_at：as_of 11-07T00 − 11-03T10 = 86h
    assert row["backlog_rows"] == 1
    assert row["backlog_age_max_hours"] == pytest.approx(86.0)
    # 空输入：不产生 cohort 行（不是产生一行 0%）
    assert ch.compute_cohorts([], [], as_of=AS_OF) == {}


def test_empty_cohort_denominator_zero_gives_none_coverage():
    """真正的分母为 0（cohort 内 0 张 mission）→ coverage None 并单列。"""
    row = ch._cohort_metrics(
        {"cohort_key": "2026-W45", "week_start": "2026-11-02", "week_end": "2026-11-09",
         "missions": [], "records": []},
        as_of_dt=ch.parse_ts(AS_OF), repo=None,
    )
    assert row["missions_total"] == 0
    assert row["coverage"] is None
    assert "coverage" in row["undefined_metrics"]


# ─────────────────────────── 3. 时延与积压 ───────────────────────────

def test_p90_latency_from_finished_at():
    conn = make_conn()
    hours = [1, 2, 3, 4, 10]
    for i, h in enumerate(hours, start=1):
        add_mission(conn, i, "2026-11-03T00:00:00+00:00")
        add_record(conn, i, "confirmed", f"2026-11-03T{h:02d}:00:00+00:00")
    missions, records = load(conn)
    row = ch.compute_cohorts(missions, records, as_of=AS_OF)["2026-W45"]
    expected = ch.percentile([float(h) for h in hours], 0.9)
    assert row["p90_latency_hours"] == pytest.approx(expected, abs=1e-6)
    assert row["latency_n"] == 5
    assert row["latency_max_hours"] == pytest.approx(10.0)


def test_latency_anomaly_excluded_and_counted():
    conn = make_conn()
    add_mission(conn, 1, "2026-11-03T00:00:00+00:00")
    add_record(conn, 1, "confirmed", "2026-11-02T00:00:00+00:00")   # 早于完结 → 脏时间戳
    add_mission(conn, 2, "2026-11-03T00:00:00+00:00")
    add_record(conn, 2, "confirmed", "2026-11-03T05:00:00+00:00")
    missions, records = load(conn)
    row = ch.compute_cohorts(missions, records, as_of=AS_OF)["2026-W45"]
    assert row["latency_n"] == 1
    assert row["p90_latency_hours"] == pytest.approx(5.0)
    assert len(row["latency_anomalies"]) == 1
    assert row["latency_anomalies"][0]["mission_id"] == 1


def test_backlog_age_starts_at_finished_at_not_created_at():
    conn = make_conn()
    # 同一秒创建的 pending 行，但 mission 完结时间差 48h → 年龄必须按 finished_at 分叉
    add_mission(conn, 1, "2026-11-05T00:00:00+00:00")
    add_mission(conn, 2, "2026-11-03T00:00:00+00:00")
    add_record(conn, 1, "pending", None, created_at="2026-11-06T23:00:00+00:00")
    add_record(conn, 2, "pending", None, created_at="2026-11-06T23:00:00+00:00")
    missions, records = load(conn)
    row = ch.compute_cohorts(missions, records, as_of=AS_OF)["2026-W45"]
    assert row["backlog_rows"] == 2
    # as_of 2026-11-07 00:00 - 2026-11-03 00:00 = 96h（若错用 created_at 只 1h）
    assert row["backlog_age_max_hours"] == pytest.approx(96.0)
    # 若按 created_at 起算（错口径），最大年龄只有 ~1h —— 断言二者可区分
    assert row["backlog_age_max_hours"] > 24 * 3


# ─────────────────────────── 4. 落库/冻结/漂移 ───────────────────────────

def test_upsert_roundtrip_and_frozen_flag():
    conn = make_conn()
    add_mission(conn, 1, "2026-10-28T00:00:00+00:00")   # 已结束的周 → frozen
    add_record(conn, 1, "confirmed", "2026-10-28T02:00:00+00:00")
    missions, records = load(conn)
    rows = ch.compute_cohorts(missions, records, as_of=AS_OF)
    ch.ensure_table(conn)
    out = ch.upsert_cohorts(conn, rows.values(), as_of=AS_OF)
    conn.commit()
    assert out["written"] == ["2026-W44"]
    stored = ch.load_stored(conn)
    assert stored["2026-W44"]["frozen"] is True
    assert stored["2026-W44"]["coverage"] == pytest.approx(1.0)
    assert stored["2026-W44"]["revision"] == 1


def test_frozen_cohort_detects_scorer_drift_without_overwriting():
    conn = make_conn()
    add_mission(conn, 1, "2026-10-28T00:00:00+00:00")
    add_record(conn, 1, "confirmed", "2026-10-28T02:00:00+00:00")
    missions, records = load(conn)
    rows = ch.compute_cohorts(missions, records, as_of=AS_OF)
    ch.ensure_table(conn)
    ch.upsert_cohorts(conn, rows.values(), as_of=AS_OF)
    conn.commit()

    # 同一输入指纹，但数被改成 0.5 → 必须报 drift 且保留旧值（不覆盖）
    tampered = dict(rows["2026-W44"])
    tampered["coverage"] = 0.5
    out = ch.upsert_cohorts(conn, [tampered], as_of=AS_OF)
    conn.commit()
    assert out["drift"] and out["drift"][0]["cohort_key"] == "2026-W44"
    assert out["kept"] == ["2026-W44"]
    stored = ch.load_stored(conn)
    assert stored["2026-W44"]["coverage"] == pytest.approx(1.0)


def test_late_arrival_bumps_revision_and_keeps_previous():
    conn = make_conn()
    add_mission(conn, 1, "2026-10-28T00:00:00+00:00")
    add_mission(conn, 2, "2026-10-29T00:00:00+00:00")
    add_record(conn, 1, "confirmed", "2026-10-28T02:00:00+00:00")
    missions, records = load(conn)
    ch.ensure_table(conn)
    ch.upsert_cohorts(conn, ch.compute_cohorts(missions, records, as_of=AS_OF).values(), as_of=AS_OF)
    conn.commit()

    add_record(conn, 2, "confirmed", "2026-10-30T02:00:00+00:00")   # 迟到评价
    missions, records = load(conn)
    out = ch.upsert_cohorts(conn, ch.compute_cohorts(missions, records, as_of=AS_OF).values(),
                            as_of=AS_OF)
    conn.commit()
    assert out["late_arrivals"] and out["late_arrivals"][0]["cohort_key"] == "2026-W44"
    stored = ch.load_stored(conn)
    assert stored["2026-W44"]["revision"] == 2
    assert stored["2026-W44"]["previous"]["revision"] == 1
    assert stored["2026-W44"]["previous"]["metrics"]["coverage"] == pytest.approx(0.5)
    assert stored["2026-W44"]["coverage"] == pytest.approx(1.0)


def test_sql_crosscheck_not_supported_on_sqlite():
    conn = make_conn()
    got = ch.sql_crosscheck(conn, {})
    assert got["supported"] is False and "sqlite" in got["reason"]

def test_overlap_decided_and_pending_counted_separately():
    """同成果既有已判定行又有 pending 行（多工件/多轮）→ 如实计数，不重复吞掉。"""
    conn = make_conn()
    add_mission(conn, 1, "2026-11-03T10:00:00+00:00")
    add_record(conn, 1, "confirmed", "2026-11-03T12:00:00+00:00", eid="ev-a")
    add_record(conn, 1, "pending", None, eid="ev-b")
    missions, records = load(conn)
    row = ch.compute_cohorts(missions, records, as_of=AS_OF)["2026-W45"]
    assert row["missions_total"] == 1
    assert row["missions_decided"] == 1
    assert row["missions_pending"] == 1
    assert row["missions_decided_and_pending"] == 1
    assert row["pending_rows"] == 1
