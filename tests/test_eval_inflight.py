"""in-flight 增量评价入口验收测试（IN38-N09 第 3 条）。

判据：
1. 只挑「已完结但无有效评价记录」的 mission，以及已有 pending 行的 mission。
2. 每个 mission 最多落**一行**评价记录；重放幂等（再跑不新增行）。
3. 证据充分 → 就地判定（pending 行原地升级）；证据不足 → 保持 pending 并计入
   ``undecidable``（不硬造等级）。
4. **不碰调度面**：``tensor_dev_missions`` 内容前后完全一致（mission 指纹不变）。
5. 连跑 3 个 cycle：pending 行数下降 ≥50%，且 P90 时延可算。
"""
from __future__ import annotations

import pathlib
import sys

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.pool import StaticPool

REPO = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from tools import outcome_evaluations as oe  # noqa: E402
from tools.evaluation_ops import inflight as fl  # noqa: E402
from tools.evaluation_ops import slo as sl  # noqa: E402

AS_OF = "2026-11-07T00:00:00+00:00"

MISSIONS_DDL = """
CREATE TABLE IF NOT EXISTS tensor_dev_missions (
    id              INTEGER PRIMARY KEY,
    organization_id INTEGER NOT NULL DEFAULT 1,
    repo            TEXT NOT NULL,
    status          TEXT NOT NULL DEFAULT 'succeeded',
    claimed_by      TEXT,
    finished_at     TEXT,
    report_id       INTEGER,
    result          TEXT
);
"""

REPORTS_DDL = """
CREATE TABLE IF NOT EXISTS tensor_reports (
    id          INTEGER PRIMARY KEY,
    mission_id  INTEGER,
    title       TEXT,
    markdown    TEXT,
    figures     TEXT
);
"""

DELTAS_DDL = """
CREATE TABLE IF NOT EXISTS tensor_capability_deltas (
    mission_id INTEGER,
    category   TEXT,
    summary    TEXT,
    reason     TEXT,
    artifacts  TEXT,
    evidence   TEXT,
    reviewer   TEXT
);
"""


def make_conn():
    engine = create_engine("sqlite+pysqlite://", future=True,
                           connect_args={"check_same_thread": False}, poolclass=StaticPool)
    conn = engine.connect()
    for stmt in [s.strip() for s in
                 (MISSIONS_DDL + REPORTS_DDL + DELTAS_DDL + oe.DDL).split(";") if s.strip()]:
        conn.execute(text(stmt))
    conn.commit()
    return conn


def add_mission(conn, mid: int, *, finished="2026-11-05T00:00:00+00:00",
                report_id: int | None = None, result: str | None = '{"checks": "ok"}'):
    conn.execute(text(
        "INSERT INTO tensor_dev_missions (id, repo, status, claimed_by, finished_at, report_id, result) "
        "VALUES (:id, 'SyntheticRepo', 'succeeded', 'agent-x', :f, :r, :res)"
    ), {"id": mid, "f": finished, "r": report_id, "res": result})
    conn.commit()


def add_pending(conn, mid: int):
    oe.store_record(conn, oe.pending_record_from_mission({"id": mid, "repo": "SyntheticRepo",
                                                         "claimed_by": "agent-x",
                                                         "finished_at": "2026-11-05T00:00:00+00:00"}))
    conn.commit()


def eval_rows(conn, mid: int) -> list[dict]:
    from sqlalchemy import text as _t

    return [dict(r) for r in conn.execute(
        _t("SELECT status, grade, e_level, reviewer_kind, reason_source, evidence_refs, boundary "
           "FROM outcome_evaluations WHERE mission_id = :m"), {"m": mid}).mappings().all()]


def test_plan_selects_unrecorded_and_pending():
    conn = make_conn()
    add_mission(conn, 1)          # 无记录
    add_mission(conn, 2)
    add_pending(conn, 2)          # 有待评价行
    add_mission(conn, 3)
    oe.store_record(conn, oe.record_from_verdict(
        {"artifact_id": "mission:3", "mission_id": 3, "contributor": "agent-x",
         "grade": "S1", "e_level": "E1", "grade_reason": "x" * 60,
         "evidence_refs": ["run:mission:3#checks"], "reviewer": "scanner",
         "reviewer_kind": "auto_scan", "baseline_or_scope": "执行性成果",
         "boundary": "仅平台事实"}, oe.decide({
             "artifact_id": "mission:3", "mission_id": 3, "contributor": "agent-x",
             "grade": "S1", "e_level": "E1", "grade_reason": "x" * 60,
             "evidence_refs": ["run:mission:3#checks"], "reviewer": "scanner",
             "reviewer_kind": "auto_scan", "boundary": "仅平台事实"})))
    conn.commit()

    p = fl.plan(conn, include_pending=True)
    assert [m["id"] for m in p["unrecorded"]] == [1]
    assert [m["id"] for m in p["pending"]] == [2]
    assert p["counts"]["unrecorded_available"] == 1
    assert p["pending_rows_total"] == 1


def test_apply_writes_exactly_one_row_and_is_idempotent():
    conn = make_conn()
    add_mission(conn, 1, report_id=7)
    conn.execute(text("INSERT INTO tensor_reports (id, mission_id, title, markdown, figures) "
                      "VALUES (7, 1, 'r', :md, '[]')"), {"md": "x" * 120})
    conn.commit()

    p = fl.plan(conn)
    stats = fl.apply(conn, p)
    conn.commit()
    rows = eval_rows(conn, 1)
    assert len(rows) == 1                       # 单行
    assert stats["queued"] == 1 and stats["decided"] == 1
    assert rows[0]["status"] in ("confirmed", "candidate")

    # 重放：无新行、无重复
    p2 = fl.plan(conn)
    assert p2["unrecorded"] == [] and p2["pending"] == []
    fl.apply(conn, p2)
    conn.commit()
    assert len(eval_rows(conn, 1)) == 1


def test_undecidable_mission_stays_pending():
    conn = make_conn()
    add_mission(conn, 1, result=None)     # 无报告、无增量、无平台事实 → 证据不足
    p = fl.plan(conn)
    stats = fl.apply(conn, p, evaluator=fl.default_evaluator)
    conn.commit()
    rows = eval_rows(conn, 1)
    assert len(rows) == 1
    assert rows[0]["status"] == "pending"     # 不硬造等级
    assert stats["undecidable"] == 1


def test_dispatcher_table_untouched():
    conn = make_conn()
    for mid in (1, 2, 3):
        add_mission(conn, mid)
    before = fl.missions_digest(conn)
    snapshot_before = [dict(r) for r in conn.execute(
        text("SELECT id, status, claimed_by, finished_at FROM tensor_dev_missions ORDER BY id")
    ).mappings().all()]
    fl.apply(conn, fl.plan(conn))
    conn.commit()
    after = fl.missions_digest(conn)
    snapshot_after = [dict(r) for r in conn.execute(
        text("SELECT id, status, claimed_by, finished_at FROM tensor_dev_missions ORDER BY id")
    ).mappings().all()]
    assert before == after
    assert snapshot_before == snapshot_after


def test_three_cycles_drop_pending_by_half_and_p90_computable():
    conn = make_conn()
    # 20 张 mission：10 张已有 pending 行（积压），10 张无记录；全部有报告+增量证据
    for mid in range(1, 21):
        add_mission(conn, mid, report_id=100 + mid)
        conn.execute(text("INSERT INTO tensor_reports (id, mission_id, title, markdown, figures) "
                          "VALUES (:i, :m, 'r', :md, '[]')"),
                     {"i": 100 + mid, "m": mid, "md": "x" * 200})
        conn.execute(text("INSERT INTO tensor_capability_deltas (mission_id, category, summary, "
                          "reason, artifacts, evidence, reviewer) VALUES (:m, 'defect_fix', 's', 'r', "
                          "'[]', '{}', 'scan')"), {"m": mid})
    conn.commit()
    for mid in range(1, 11):
        add_pending(conn, mid)

    out = fl.run_cycles(conn, cycles=3, limit=10)
    assert out["before"]["pending_rows"] == 10
    assert out["after"]["pending_rows"] == 0
    assert out["pending_drop_share"] == pytest.approx(1.0)
    assert out["pending_drop_share"] >= 0.5
    assert out["dispatcher_untouched"] is True
    assert [h["cycle"] for h in out["history"]] == [1, 2, 3]

    # P90 时延可算（全部已有 decided_at）
    missions = [{"id": mid, "finished_at": "2026-11-05T00:00:00+00:00"} for mid in range(1, 21)]
    records = [dict(r) for r in conn.execute(text(
        "SELECT mission_id, status, decided_at FROM outcome_evaluations")).mappings().all()]
    snap = sl.ops_snapshot(missions, records, as_of=AS_OF)
    assert snap["p90_latency_hours"] is not None
    assert snap["latency_n"] == 20


def test_dry_run_writes_nothing():
    conn = make_conn()
    add_mission(conn, 1)
    out = fl.run_cycles(conn, cycles=2, limit=5, dry_run=True)
    assert out["after"]["pending_rows"] == 0
    assert out["before"] == out["after"]
    assert eval_rows(conn, 1) == []


def test_rejected_only_mission_is_requeued():
    conn = make_conn()
    add_mission(conn, 1)
    oe.store_record(conn, oe.record_from_verdict(
        {"artifact_id": "mission:1", "mission_id": 1, "contributor": "agent-x"},
        oe.decide({"artifact_id": "mission:1", "mission_id": 1})))
    conn.commit()
    assert eval_rows(conn, 1)[0]["status"] == "rejected"
    p = fl.plan(conn)
    assert [m["id"] for m in p["unrecorded"]] == [1]   # 只有 rejected → 仍算无有效记录

def test_platform_facts_only_mission_gets_e1_single_row():
    """无报告、无增量，但 mission.result 有平台事实 → E1/S1（仅平台事实），仍是单行。"""
    conn = make_conn()
    add_mission(conn, 1, result='{"checks": "pytest 31 passed", "git_status": "M a.py"}')
    stats = fl.apply(conn, fl.plan(conn))
    conn.commit()
    rows = eval_rows(conn, 1)
    assert len(rows) == 1
    assert rows[0]["status"] == "confirmed"
    assert rows[0]["grade"] == "S1" and rows[0]["e_level"] == "E1"
    assert rows[0]["reviewer_kind"] == "auto_scan"
    import json as _json

    refs = _json.loads(rows[0]["evidence_refs"])
    assert {r["ref"].split("#")[-1] for r in refs} == {"result.checks", "result.git_status"}
    assert "未复跑代码" in rows[0]["boundary"]
    assert stats["decided"] == 1 and stats["undecidable"] == 0


def test_empty_result_stays_pending_no_rejected_row():
    conn = make_conn()
    add_mission(conn, 1, result="{}")
    stats = fl.apply(conn, fl.plan(conn))
    conn.commit()
    rows = eval_rows(conn, 1)
    assert [r["status"] for r in rows] == ["pending"]     # 不写 rejected、不硬造等级
    assert stats["undecidable"] == 1
