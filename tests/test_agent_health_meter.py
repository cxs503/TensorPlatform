"""席位健康度仪表的验收测试（FEAT-AGENT-HARNESS）。

三层：
1. **纯函数层**（无 DB）：健康分数学定义可复算、标记阈值语义、缺失分量重归一化。
2. **红队靶场层**（PG，独立 schema ``rt_health``，与生产零接触）：
   构造能触发 RECUR_FAIL / LOW_SUCCESS / MEMORY_STARVED 的假席位 →
   断言标记**真的亮** → 撤掉触发条件 → 断言标记**真的灭**（标记可失败）。
3. **幂等/复算层**：同日重跑不新增行、改数据后覆盖更新；把落库的
   ``score_components`` 重算一遍必须等于 ``health_score``。

DB 不可用时靶场层 skip（不 fail——但纯函数层永远跑）。
"""
from __future__ import annotations

import importlib.util
import json
import os
import sys
import uuid
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
MODULE_PATH = REPO / "tools" / "agent" / "agent_health_meter.py"
MIGRATION = REPO / "backend" / "migrations" / "123_agent_health_meter.sql"

_spec = importlib.util.spec_from_file_location("agent_health_meter", MODULE_PATH)
assert _spec and _spec.loader
ahm = importlib.util.module_from_spec(_spec)
sys.modules["agent_health_meter"] = ahm   # dataclass 需要模块已在 sys.modules 里
_spec.loader.exec_module(ahm)

SeatMetrics = ahm.SeatMetrics
score_seat = ahm.score_seat
markers_for = ahm.markers_for


# ─────────────────────────── 1. 纯函数层 ───────────────────────────


def _metrics(**kw) -> ahm.SeatMetrics:
    base = dict(seat_key="s1", organization_id=1, missions_total=10,
                missions_succeeded=8, failures_total=2, stage_attributed=2,
                forced_finish_count=1)
    base.update(kw)
    return SeatMetrics(**base)


def test_score_components_recomputable():
    """分量 → 分数必须能手算复算（这是「数学定义成文」的可执行版本）。"""
    m = _metrics()
    scored = score_seat(m, memory_available=False)
    c = scored["components"]
    assert c["success"] == 0.8
    assert c["controllability"] == pytest.approx(0.9)
    assert c["attribution"] == pytest.approx(1.0)
    assert c["memory"] is None  # 源不可用 → 缺省，不按 0 罚
    w = scored["weights_used"]
    raw = round(100 * sum(w[k] * c[k] for k in w) / sum(w.values()), 2)
    assert scored["raw"] == pytest.approx(raw)
    assert scored["score"] == pytest.approx(raw)  # 无标记 → 不罚


def test_memory_component_renormalized_not_zeroed():
    """记忆源不可用时权重重归一化：不得把缺省当 0 分。"""
    m = _metrics()
    with_mem = score_seat(m, memory_available=True)     # hits=0 → memory=0
    without = score_seat(m, memory_available=False)     # memory=None
    assert with_mem["components"]["memory"] == 0.0
    assert with_mem["score"] < without["score"]
    assert without["weights_renormalized"] == pytest.approx(0.8)


def test_score_undefined_without_missions():
    scored = score_seat(_metrics(missions_total=0, missions_succeeded=0,
                                 failures_total=0, stage_attributed=0,
                                 forced_finish_count=0))
    assert scored["score"] is None and scored["reason"] == "no_missions_in_window"


def test_penalties_multiplicative_preserve_order():
    """同组罚分保留组内排序（不是硬上限拉平）。"""
    good = _metrics(missions_total=10, missions_succeeded=8)
    bad = _metrics(missions_total=10, missions_succeeded=1, failures_total=9)
    g = score_seat(good, markers=["RECUR_FAIL"], memory_available=False)
    b = score_seat(bad, markers=["RECUR_FAIL"], memory_available=False)
    assert g["penalties_applied"] == [{"marker": "RECUR_FAIL", "factor": 0.65}]
    assert g["score"] == pytest.approx(round(g["raw"] * 0.65, 2))
    assert g["score"] > b["score"]


def test_marker_recur_fail_needs_identified_stage():
    """RECUR_FAIL 只认「已识别」阶段；(unspecified) 重复计为数据缺口，不打标。"""
    hot = _metrics(recur_stage="no_progress+forced_finish", recur_stage_count=3)
    markers, meta = markers_for(hot)
    assert "RECUR_FAIL" in markers and meta["RECUR_FAIL"]["count"] == 3

    cold = _metrics(recur_stage="no_progress+forced_finish", recur_stage_count=2)
    assert "RECUR_FAIL" not in markers_for(cold)[0]

    unspecified = _metrics(recur_stage=None, recur_stage_count=9)
    markers, meta = markers_for(unspecified)
    assert "RECUR_FAIL" not in markers
    assert meta["RECUR_FAIL"]["withheld"] == "stage_unspecified"


def test_marker_low_success_floor_and_min_missions():
    low = _metrics(missions_total=6, missions_succeeded=1)
    assert "LOW_SUCCESS" in markers_for(low)[0]
    # 低于样本门槛不打标（避免 1 单失败就被标病）
    tiny = _metrics(missions_total=2, missions_succeeded=0)
    markers, meta = markers_for(tiny)
    assert "LOW_SUCCESS" not in markers
    assert meta["LOW_SUCCESS"]["withheld"] == "insufficient_missions"
    # 刚好在地板上：不打标（严格小于才打）
    edge = _metrics(missions_total=10, missions_succeeded=3)
    assert "LOW_SUCCESS" not in markers_for(edge, success_floor=0.30)[0]


def test_marker_memory_starved_requires_source():
    m = _metrics(missions_total=12, missions_succeeded=10, memory_hits=0)
    markers, meta = markers_for(m, memory_available=False)
    assert "MEMORY_STARVED" not in markers
    assert meta["MEMORY_STARVED"]["withheld"] == "memory_source_unavailable"

    markers, _ = markers_for(m, memory_available=True)
    assert "MEMORY_STARVED" in markers

    ok = _metrics(missions_total=12, missions_succeeded=10, memory_hits=2)
    assert "MEMORY_STARVED" not in markers_for(ok, memory_available=True)[0]


def test_unattributed_bucket_never_marked():
    m = _metrics(seat_key=ahm.UNATTRIBUTED, missions_total=400, missions_succeeded=0,
                 failures_total=400, stage_attributed=0, recur_stage="x", recur_stage_count=400)
    markers, meta = markers_for(m)
    assert markers == [] and meta["suppressed"] == "unattributed_bucket"


# ─────────────────────────── 2. 红队靶场层（PG） ───────────────────────────

DSN = os.environ.get("TP_DSN", "dbname=tensorplatform user=foundation host=/var/run/postgresql")
RT_SCHEMA = "rt_health"
AS_OF = date(2026, 11, 12)


def _connect():
    try:
        import psycopg
        return psycopg.connect(DSN, autocommit=True)
    except Exception:
        try:
            import psycopg2
            conn = psycopg2.connect(DSN)
            conn.autocommit = True
            return conn
        except Exception as exc:  # pragma: no cover
            pytest.skip(f"PG 不可用：{exc}")


@pytest.fixture(scope="module")
def rt():
    """独立 schema 靶场：建源表 → 套同一份迁移 → 跑完整体 drop。"""
    conn = _connect()
    cur = conn.cursor()
    cur.execute(f"DROP SCHEMA IF EXISTS {RT_SCHEMA} CASCADE")
    cur.execute(f"CREATE SCHEMA {RT_SCHEMA}")
    cur.execute(f"SET search_path TO {RT_SCHEMA}")
    cur.execute("""
        CREATE TABLE tensor_dev_missions (
            id BIGSERIAL PRIMARY KEY,
            organization_id BIGINT NOT NULL DEFAULT 1,
            goal TEXT NOT NULL DEFAULT '',
            claimed_by TEXT,
            status TEXT NOT NULL DEFAULT 'succeeded',
            result JSONB NOT NULL DEFAULT '{}'::jsonb,
            agent_session_id UUID,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now()
        );
        CREATE TABLE factory_resident_workers (
            agent_session_id UUID PRIMARY KEY,
            worker_id TEXT NOT NULL,
            organization_id BIGINT NOT NULL DEFAULT 1,
            role TEXT NOT NULL DEFAULT 'worker',
            memory JSONB NOT NULL DEFAULT '[]'::jsonb
        );
        CREATE TABLE factory_agent_identities (
            agent_id UUID PRIMARY KEY,
            worker_id TEXT NOT NULL,
            organization_id BIGINT NOT NULL DEFAULT 1
        );
        CREATE TABLE factory_memory_events (
            event_id BIGSERIAL PRIMARY KEY,
            organization_id BIGINT NOT NULL DEFAULT 1,
            agent_id UUID,
            session_id UUID,
            event_type TEXT NOT NULL DEFAULT 'lesson',
            provenance JSONB NOT NULL DEFAULT '{}'::jsonb,
            payload JSONB NOT NULL DEFAULT '{}'::jsonb,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now()
        );
        CREATE TABLE chronicle_snapshots (ts TIMESTAMPTZ, payload JSONB);
    """)
    cur.execute("SET search_path TO public")
    # 生产迁移原样套在靶场 schema 上（不复制 DDL，避免靶场与生产口径漂移）
    cur.execute(f"SET search_path TO {RT_SCHEMA}")
    cur.execute(MIGRATION.read_text())
    cur.execute("SET search_path TO public")
    yield conn
    cur.execute(f"DROP SCHEMA IF EXISTS {RT_SCHEMA} CASCADE")
    conn.close()


def _meter(conn) -> "ahm.HealthMeter":
    return ahm.HealthMeter(conn=conn, source_schema=RT_SCHEMA, target_schema=RT_SCHEMA,
                           log_path=None)


def _add_mission(cur, seat: str, status: str, *, days_ago: int = 1,
                 fail_stage: str | None = None, extra: dict | None = None) -> None:
    result = dict(extra or {})
    if fail_stage:
        result["fail_stage"] = fail_stage
    cur.execute(
        f"INSERT INTO {RT_SCHEMA}.tensor_dev_missions "
        "(organization_id, goal, claimed_by, status, result, created_at) "
        "VALUES (1, %s, %s, %s, %s::jsonb, %s)",
        (f"mission for {seat}", seat, status, json.dumps(result),
         datetime(2026, 11, 12, 3, tzinfo=timezone.utc) - timedelta(days=days_ago)),
    )


def _add_memory_event(cur, worker_id: str, kind: str, session: uuid.UUID | None = None) -> None:
    cur.execute(
        f"INSERT INTO {RT_SCHEMA}.factory_memory_events "
        "(organization_id, session_id, event_type, payload, provenance, created_at) "
        "VALUES (1, %s, 'lesson', %s::jsonb, '{}'::jsonb, %s)",
        (str(session) if session else None,
         json.dumps({"phase": kind}), datetime(2026, 11, 11, 12, tzinfo=timezone.utc)),
    )
    if session:
        cur.execute(f"INSERT INTO {RT_SCHEMA}.factory_resident_workers "
                    "(agent_session_id, worker_id, organization_id, memory) "
                    "VALUES (%s, %s, 1, '[]'::jsonb) ON CONFLICT DO NOTHING",
                    (str(session), worker_id))


def _rows(conn) -> dict[str, dict]:
    cur = conn.cursor()
    cur.execute(
        f"SELECT seat_key, seat_kind, missions_total, missions_succeeded, success_rate,"
        f" forced_finish_count, forced_finish_rate, fail_stage_dist, recur_stage,"
        f" recur_stage_count, lessons_written, lessons_injected, memory_hits,"
        f" memory_attribution, health_score, score_components, markers, markers_meta"
        f" FROM {RT_SCHEMA}.tensor_agent_health WHERE snapshot_date = %s", (AS_OF,))
    cols = ["seat_key", "seat_kind", "missions_total", "missions_succeeded", "success_rate",
            "forced_finish_count", "forced_finish_rate", "fail_stage_dist", "recur_stage",
            "recur_stage_count", "lessons_written", "lessons_injected", "memory_hits",
            "memory_attribution", "health_score", "score_components", "markers", "markers_meta"]
    out = {r[0]: dict(zip(cols, r)) for r in cur.fetchall()}
    for row in out.values():   # psycopg3 已把 jsonb 解成 dict；psycopg2 给字符串
        for key in ("health_score", "success_rate", "forced_finish_rate"):
            if row.get(key) is not None:
                row[key] = float(row[key])
        for key, val in list(row.items()):
            if isinstance(val, str) and val.startswith(("{", "[")):
                try:
                    row[key] = json.loads(val)
                except json.JSONDecodeError:
                    pass
    return out


def test_redteam_all_three_markers_fire_and_can_fail(rt):
    """红队：构造能触发三个标记的假数据 → 触发 → 撤条件 → 熄灭。"""
    conn = rt
    cur = conn.cursor()
    # 记忆源可用（否则 MEMORY_STARVED 只能 withheld）：给 rt-healthy 一条命中
    sess = uuid.uuid4()
    _add_memory_event(cur, "rt-healthy", "hit", sess)
    # rt-recur：6 单 2 成，4 条同阶段强制终结 → RECUR_FAIL（成功率 0.333 > 地板，不触发 LOW_SUCCESS）
    for i in range(2):
        _add_mission(cur, "rt-recur", "succeeded")
    for i in range(4):
        _add_mission(cur, "rt-recur", "succeeded", fail_stage="no_progress+forced_finish")
    # rt-low：6 单 1 成，失败阶段各不相同 → 只触发 LOW_SUCCESS
    _add_mission(cur, "rt-low", "succeeded")
    for st in ("budget_exhausted+forced_finish", "wall_exhausted+forced_finish",
               "no_progress+forced_finish", "gate_rejected", "timeout"):
        _add_mission(cur, "rt-low", "failed", fail_stage=st)
    # rt-starved：12 单 10 成，无记忆命中 → 只触发 MEMORY_STARVED
    for i in range(10):
        _add_mission(cur, "rt-starved", "succeeded")
    for i in range(2):
        _add_mission(cur, "rt-starved", "failed")
    # rt-healthy：12 单 10 成 + 记忆命中 → 无标记（阴性对照）
    for i in range(10):
        _add_mission(cur, "rt-healthy", "succeeded")
    for i in range(2):
        _add_mission(cur, "rt-healthy", "failed")
    cur.execute(f"INSERT INTO {RT_SCHEMA}.factory_resident_workers "
                "(agent_session_id, worker_id, organization_id, memory) "
                "VALUES (%s, 'rt-healthy', 1, %s::jsonb) ON CONFLICT DO NOTHING",
                (str(sess), json.dumps(["lesson-a", "lesson-b"])))
    # 无席位标识的单 → unattributed 桶，不得被打标
    cur.execute(f"INSERT INTO {RT_SCHEMA}.tensor_dev_missions "
                "(organization_id, goal, claimed_by, status, result, created_at) "
                "VALUES (1, 'orphan', NULL, 'failed', '{}'::jsonb, %s)",
                (datetime(2026, 11, 11, tzinfo=timezone.utc),))

    meter = _meter(conn)
    summary = meter.snapshot(AS_OF)
    rows = _rows(conn)

    assert summary["markers"]["RECUR_FAIL"] >= 1
    assert summary["markers"]["LOW_SUCCESS"] >= 1
    assert summary["markers"]["MEMORY_STARVED"] >= 1

    assert rows["rt-recur"]["markers"] == ["RECUR_FAIL"]
    assert rows["rt-recur"]["recur_stage"] == "no_progress+forced_finish"
    assert rows["rt-low"]["markers"] == ["LOW_SUCCESS"]
    assert rows["rt-starved"]["markers"] == ["MEMORY_STARVED"]
    assert rows["rt-healthy"]["markers"] == []
    assert rows["rt-healthy"]["memory_hits"] == 1
    assert rows[ahm.UNATTRIBUTED]["seat_kind"] == "unattributed"
    assert rows[ahm.UNATTRIBUTED]["markers"] == []

    # 分数字段可复算：把落库 components 重算一遍必须等于落库分
    for seat, row in rows.items():
        comp = row["score_components"]
        if isinstance(comp, str):
            comp = json.loads(comp)
        if comp["score"] is None:
            assert row["health_score"] is None
            continue
        w = comp["weights_used"]
        raw = 100 * sum(w[k] * comp["components"][k] for k in w) / sum(w.values())
        factor = 1.0
        for pen in comp["penalties_applied"]:
            factor *= pen["factor"]
        assert row["health_score"] == pytest.approx(round(raw * factor, 2), abs=0.02)

    # ── 撤掉触发条件 → 标记必须灭（红队可失败） ──
    # 删掉 3 条同阶段强制终结 → 只剩 1，RECUR_FAIL 的计数条件必须失效
    cur.execute(f"DELETE FROM {RT_SCHEMA}.tensor_dev_missions WHERE id IN ("
                f"  SELECT id FROM {RT_SCHEMA}.tensor_dev_missions"
                f"  WHERE claimed_by='rt-recur' AND result ? 'fail_stage'"
                f"  ORDER BY id LIMIT 3)")
    cur.execute(f"UPDATE {RT_SCHEMA}.tensor_dev_missions SET status='succeeded' "
                f"WHERE claimed_by='rt-low' AND status='failed'")
    cur.execute(f"DELETE FROM {RT_SCHEMA}.tensor_dev_missions WHERE claimed_by='rt-starved'")
    cur.execute(f"DELETE FROM {RT_SCHEMA}.factory_memory_events "
                f"WHERE created_at < %s", (datetime(2026, 11, 12, tzinfo=timezone.utc),))
    meter.snapshot(AS_OF)
    rows2 = _rows(conn)
    assert rows2["rt-recur"]["markers"] == []
    assert rows2["rt-low"]["markers"] == []
    # 窗口内已无该席位 → 同日重跑先清后写，陈旧行与陈旧标记都不得残留
    assert "rt-starved" not in rows2
    # 记忆源清空后 MEMORY_STARVED 必须转为 withheld（不是静默通过）
    assert rows2["rt-healthy"]["markers_meta"]["MEMORY_STARVED"]["withheld"] == "memory_source_unavailable"
    assert rows2["rt-healthy"]["memory_hits"] == 0
    assert rows2["rt-healthy"]["memory_attribution"]["canonical_status"] == "table_empty"


def test_snapshot_idempotent_and_upsert(rt):
    """同日重跑只覆盖不新增；数据变了值要跟着变。"""
    conn = rt
    cur = conn.cursor()
    cur.execute(f"DELETE FROM {RT_SCHEMA}.tensor_agent_health")
    cur.execute(f"DELETE FROM {RT_SCHEMA}.tensor_dev_missions")
    for i in range(4):
        _add_mission(cur, "rt-idem", "succeeded")
    for i in range(2):
        _add_mission(cur, "rt-idem", "failed")
    meter = _meter(conn)
    meter.snapshot(AS_OF)
    first = _rows(conn)["rt-idem"]
    meter.snapshot(AS_OF)
    second = _rows(conn)["rt-idem"]
    cur.execute(f"SELECT count(*) FROM {RT_SCHEMA}.tensor_agent_health")
    assert cur.fetchone()[0] == len(_rows(conn))  # 没有重复行
    assert first == second  # 同一数据重跑结果一致

    _add_mission(cur, "rt-idem", "failed")  # 新增一条失败 → 覆盖更新
    meter.snapshot(AS_OF)
    third = _rows(conn)["rt-idem"]
    assert third["missions_total"] == 7
    assert third["missions_succeeded"] == 4
    cur.execute(f"SELECT count(*) FROM {RT_SCHEMA}.tensor_agent_health "
                f"WHERE seat_key='rt-idem'")
    assert cur.fetchone()[0] == 1


def test_dry_run_writes_nothing(rt):
    conn = rt
    cur = conn.cursor()
    cur.execute(f"DELETE FROM {RT_SCHEMA}.tensor_agent_health")
    meter = ahm.HealthMeter(conn=conn, source_schema=RT_SCHEMA, target_schema=RT_SCHEMA,
                            dry_run=True, log_path=None)
    summary = meter.snapshot(AS_OF)
    assert summary["rows_written"] == 0
    cur.execute(f"SELECT count(*) FROM {RT_SCHEMA}.tensor_agent_health")
    assert cur.fetchone()[0] == 0


def test_schema_identifier_rejects_injection(rt):
    with pytest.raises(ValueError):
        ahm.HealthMeter(conn=rt, source_schema="public; DROP TABLE x", log_path=None)


def test_lock_is_exclusive(tmp_path):
    """flock：第二个实例 timeout=0 时立即让位（守护幂等护栏）。"""
    lock_path = tmp_path / "meter.lock"
    with ahm.Lock(lock_path):
        with pytest.raises(ahm.LockBusy):
            with ahm.Lock(lock_path, timeout=0.0):
                pass
    with ahm.Lock(lock_path):  # 释放后可再取
        pass