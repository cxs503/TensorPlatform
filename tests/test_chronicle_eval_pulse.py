"""评价体系脉搏（IN24-E4）验收测试 —— 判据先于实现，机器可裁。

判据（与任务书一致，逐条落测试）：
1. **评价面全字段**：``capture()`` 产出 ``FACET_FIELDS`` 全部字段；覆盖率 = confirmed/succeeded
   窗口比；S/E 分布为**不加权**分层计数且与 SQL 逐格一致。
2. **SQL 可复算**：``recompute()`` 直接重跑 facet 自带 SQL 原文，逐字段与 facet 相等（0 diffs）。
3. **阈值负例 → 一次演练告警**：合成越界数据 → 每键恰一条告警；重复拍 0 条（上升沿语义）。
4. **连续两拍快照含评价面全字段**：两拍都齐备、快照链相连（prev_snapshot_id）。
5. **无定义不按 0% 处理**：confirmed 为 0 时占比 ``None`` 且不触发独立占比告警。
6. **周期质量报表**：digest 含覆盖率/S/E/复核/积压/阈值六段，并声明禁加权。
7. **零领域语义**：模块源码不含学科/业务词，被观测主体由 ``--subject`` 数据注入。

夹具一律使用**合成主体**（``SyntheticSubject`` / ``OtherSubject``），无任何学科词。
"""
from __future__ import annotations

import json
import pathlib
import re
import sys

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

REPO = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from tools.chronicle import eval_pulse as ep  # noqa: E402

SUBJECT = "SyntheticSubject"
OTHER = "OtherSubject"
AS_OF = "2026-11-07T12:00:00+00:00"

MISSIONS_DDL = """
CREATE TABLE IF NOT EXISTS tensor_dev_missions (
    id              INTEGER PRIMARY KEY,
    organization_id INTEGER NOT NULL DEFAULT 1,
    repo            TEXT NOT NULL,
    status          TEXT NOT NULL DEFAULT 'succeeded',
    claimed_by      TEXT,
    finished_at     TEXT
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

# 主体 SyntheticSubject：12 张 succeeded，4 confirmed（3 auto_scan + 1 independent）、2 candidate、
# 2 pending（记录年龄 10h / 30h）、4 无记录 → 覆盖率 4/12、独立占比 1/4、积压 2 条
MISSIONS = [(i, SUBJECT, "succeeded", f"2026-11-0{1 + (i % 6)}T0{i % 10}:00:00+00:00")
            for i in range(1, 13)]
# 另一主体：5 张 succeeded、5 confirmed（全 independent）——必须不影响 SyntheticSubject 的窗口
MISSIONS += [(100 + i, OTHER, "succeeded", f"2026-11-05T0{i}:00:00+00:00") for i in range(5)]
# 非 succeeded：不得进 window
MISSIONS += [(200, SUBJECT, "failed", "2026-11-07T01:00:00+00:00"),
             (201, SUBJECT, "running", "2026-11-07T01:00:00+00:00")]

EVALS = [
    # (evaluation_id, mission_id, repo, status, grade, e_level, reviewer_kind, created_at)
    ("e-1", 1, SUBJECT, "confirmed", "S1", "E2", "auto_scan", "2026-11-06T00:00:00+00:00"),
    ("e-2", 2, SUBJECT, "confirmed", "S1", "E2", "auto_scan", "2026-11-06T01:00:00+00:00"),
    ("e-3", 3, SUBJECT, "confirmed", "S2", "E3", "auto_scan", "2026-11-06T02:00:00+00:00"),
    ("e-4", 4, SUBJECT, "confirmed", "S3", "E4", "independent", "2026-11-06T03:00:00+00:00"),
    ("e-5", 5, SUBJECT, "candidate", "S4", "E1", "auto_scan", "2026-11-06T04:00:00+00:00"),
    ("e-6", 6, SUBJECT, "candidate", "S5", "E3", "auto_scan", "2026-11-06T05:00:00+00:00"),
    ("e-7", 7, SUBJECT, "pending", None, None, None, "2026-11-07T02:00:00+00:00"),   # 10h
    ("e-8", 8, SUBJECT, "pending", None, None, None, "2026-11-06T06:00:00+00:00"),   # 30h
    ("e-100", 100, OTHER, "confirmed", "S2", "E3", "independent", "2026-11-05T00:00:00+00:00"),
    ("e-101", 101, OTHER, "confirmed", "S2", "E3", "independent", "2026-11-05T01:00:00+00:00"),
    ("e-102", 102, OTHER, "confirmed", "S2", "E3", "independent", "2026-11-05T02:00:00+00:00"),
    ("e-103", 103, OTHER, "confirmed", "S2", "E3", "independent", "2026-11-05T03:00:00+00:00"),
    ("e-104", 104, OTHER, "confirmed", "S2", "E3", "independent", "2026-11-05T04:00:00+00:00"),
]


@pytest.fixture()
def conn():
    engine = create_engine("sqlite+pysqlite:///:memory:", future=True,
                           poolclass=StaticPool, connect_args={"check_same_thread": False})
    with engine.connect() as c:
        c.execute(text(MISSIONS_DDL))
        c.execute(text(EVALS_DDL))
        for mid, repo, status, finished in MISSIONS:
            c.execute(text("INSERT INTO tensor_dev_missions (id, repo, status, finished_at) "
                           "VALUES (:i, :r, :s, :f)"),
                      {"i": mid, "r": repo, "s": status, "f": finished})
        for eid, mid, repo, status, grade, elevel, kind, created in EVALS:
            c.execute(text(
                "INSERT INTO outcome_evaluations (evaluation_id, artifact_id, mission_id, repo, "
                "status, grade, e_level, reviewer_kind, created_at) VALUES "
                "(:eid, :aid, :mid, :repo, :st, :g, :el, :kind, :c)"),
                {"eid": eid, "aid": f"artifact-{eid}", "mid": mid, "repo": repo, "st": status,
                 "g": grade, "el": elevel, "kind": kind, "c": created})
        c.commit()
        yield c


def _capture(conn, **kw):
    kw.setdefault("subject", SUBJECT)
    kw.setdefault("window", 12)
    kw.setdefault("as_of", AS_OF)
    return ep.capture(conn, **kw)


# ─────────────────────── 判据 1：评价面全字段 ───────────────────────

def test_facet_has_all_fields_and_no_weighting(conn):
    facet = _capture(conn)
    missing = [f for f in ep.FACET_FIELDS if f not in facet]
    assert missing == [], f"评价面缺字段：{missing}"
    assert facet["schema"] == ep.SCHEMA and facet["facet"] == "evaluation"
    # 覆盖率 = confirmed / succeeded（窗口比）；窗口只数该主体 succeeded（failed/running 不计）
    assert facet["coverage"]["missions_succeeded"] == 12
    assert facet["coverage"]["missions_confirmed"] == 4
    assert facet["coverage"]["missions_decided"] == 6
    assert facet["coverage"]["missions_pending"] == 2
    assert facet["coverage"]["missions_without_record"] == 4
    assert facet["coverage"]["ratio"] == pytest.approx(4 / 12, abs=1e-6)  # facet 内 6 位定点
    # S 分布：不加权、无排名、candidate 不计入、S-N 单列
    s = facet["s_distribution"]
    assert s["counts"] == {"S1": 2, "S2": 1, "S3": 1, "S4": 0, "S5": 0, "S-N": 0}
    assert s["total"] == 4 and s["weighting"] == "none" and s["ranking"] is None
    e = facet["e_distribution"]
    assert e["counts"] == {"E0": 0, "E1": 0, "E2": 2, "E3": 1, "E4": 1} and e["total"] == 4
    # 复核结构：independent 1 / auto_scan 3 → 0.25 / 0.75
    assert facet["reviewer_mix"]["counts"] == {"independent": 1, "auto_scan": 3, "unspecified": 0}
    assert facet["independent_share"] == pytest.approx(0.25)
    assert facet["auto_scan_share"] == pytest.approx(0.75)
    # 其它主体的 confirmed 不得漏进本窗口
    assert facet["records_in_window"].get("confirmed") == 4


def test_pending_backlog_ages(conn):
    facet = _capture(conn)
    pb = facet["pending_backlog"]
    assert pb["count"] == 2 and pb["rows_counted"] == 2
    assert pb["oldest_age_hours"] == pytest.approx(30.0, abs=1e-3)
    assert pb["median_age_hours"] == pytest.approx(20.0, abs=1e-3)
    assert pb["oldest_created_at"].startswith("2026-11-06T06:00:00")


# ─────────────────────── 判据 2：SQL 可复算 ───────────────────────

def test_sql_recompute_is_field_exact(conn):
    facet = _capture(conn)
    check = ep.recompute(conn, facet)
    assert check["ok"] is True, check["diffs"]
    assert set(check["sql_used"]) >= {"coverage", "s_distribution", "e_distribution",
                                      "reviewer_mix", "pending_backlog", "pending_rows"}
    # facet 里留存 SQL 原文（独立复算者不需要本模块的函数）
    assert "SELECT" in facet["sql"]["coverage"].upper()
    assert facet["sql_params"] == {"subject": SUBJECT, "window": 12}


def test_recompute_detects_tampering(conn):
    """负控：把 facet 里的覆盖率改掉，复算必须报差异（说明比对不是自证）。"""
    facet = _capture(conn)
    facet["coverage"]["missions_confirmed"] = 9
    facet["coverage"]["ratio"] = 0.75
    check = ep.recompute(conn, facet)
    assert check["ok"] is False
    assert {d["field"] for d in check["diffs"]} >= {"coverage.missions_confirmed", "coverage.ratio"}


# ────────────── 判据 3：阈值判定 + 负例一次演练告警 ──────────────

def test_threshold_breach_on_low_coverage_and_independent(conn):
    facet = _capture(conn)
    keys = {a["key"] for a in facet["alerts"]}
    assert keys == {"coverage_low", "independent_share_low"}   # 0.333 < 0.6；0.25 < 0.5
    assert facet["alert_states"] == {"coverage_low": "breach",
                                     "pending_backlog_high": "ok",
                                     "independent_share_low": "breach"}


def test_threshold_pass_with_relaxed_thresholds(conn):
    th = {"coverage_min": 0.30, "pending_max": 2.0, "independent_share_min": 0.20}
    facet = _capture(conn, thresholds=th)
    assert facet["alerts"] == []
    assert set(facet["alert_states"].values()) == {"ok"}
    assert facet["thresholds"]["coverage_min"] == pytest.approx(0.30)


def test_pending_threshold_direction(conn):
    """pending 阈值是「大于才告警」：2 条不越界，阈值降到 1 即越界。"""
    facet = _capture(conn, thresholds={"pending_max": 1.0})
    keys = {a["key"] for a in facet["alerts"]}
    assert "pending_backlog_high" in keys
    alert = next(a for a in facet["alerts"] if a["key"] == "pending_backlog_high")
    assert alert["comparison"] == ">" and alert["value"] == 2 and alert["threshold"] == 1.0


def test_drill_fires_exactly_one_alert_per_key(tmp_path):
    ledger = tmp_path / "alerts.jsonl"
    drill = ep.run_drill(ledger_path=ledger)
    assert drill["exactly_once_ok"] is True
    assert drill["breached_keys"] == ["coverage_low", "pending_backlog_high", "independent_share_low"]
    assert [a["key"] for a in drill["first_edge"]["emitted"]] == drill["breached_keys"]
    assert drill["second_edge"]["emitted"] == []            # 重复拍不刷屏
    assert [a["edge"] for a in drill["second_edge"]["suppressed"]] == ["sustained"] * 3
    lines = [json.loads(x) for x in ledger.read_text(encoding="utf-8").splitlines()]
    assert len(lines) == 3 and {r["key"] for r in lines} == set(drill["breached_keys"])
    assert drill["synthetic_facet"]["source"] == "synthetic_drill"


def test_rise_after_recovery_re_alerts():
    """恢复后再越界要重新告警（上升沿的恢复侧）。"""
    alerts = [{"key": "coverage_low", "metric": "coverage.ratio", "value": 0.3,
               "threshold": 0.6, "comparison": "<", "message": "x"}]
    down = ep.emit_alerts(alerts, previous_active_keys=["coverage_low"])
    assert down["emitted"] == [] and len(down["suppressed"]) == 1
    up_again = ep.emit_alerts(alerts, previous_active_keys=[])
    assert len(up_again["emitted"]) == 1 and up_again["emitted"][0]["edge"] == "rising"


# ──────────── 判据 4：连续两拍快照含评价面全字段 ────────────

def test_two_consecutive_snapshots_complete_and_chained(conn):
    first = _capture(conn)
    snap1 = ep.write_snapshot(conn, first, ts=AS_OF, commit=True)
    assert ep.facet_complete(snap1["payload"]) == (True, [])
    # 第二拍：新增一条 pending，数据确实变了（指纹不同）
    conn.execute(text("INSERT INTO outcome_evaluations (evaluation_id, artifact_id, mission_id, repo, "
                      "status, created_at) VALUES ('e-9','artifact-e-9',9,:r,'pending',:c)"),
                 {"r": SUBJECT, "c": "2026-11-07T11:00:00+00:00"})
    conn.commit()
    second = _capture(conn, as_of="2026-11-07T13:00:00+00:00")
    assert second["pending_backlog"]["count"] == 3
    snap2 = ep.write_snapshot(conn, second, ts="2026-11-07T13:00:00+00:00",
                              prev_snapshot_id=snap1["snapshot_id"], commit=True)
    check = ep.consecutive_facet_check(conn)
    assert check["all_complete"] is True
    assert check["chain_linked"] is True
    assert check["snapshots_found"] == 2
    for per in check["per_snapshot"]:
        assert per["facet_complete"] is True and per["missing_fields"] == []
        assert per["facet_fields_present"] == per["facet_fields_required"] == len(ep.FACET_FIELDS)
    assert snap1["payload"]["schema"] == ep.SCHEMA and snap2["payload"]["prev_snapshot_id"] == snap1["snapshot_id"]
    # 快照链的指纹链：两拍评价面指纹不同（数据变了）
    assert first["facet_fingerprint"] != second["facet_fingerprint"]


def test_facet_complete_rejects_missing_field(conn):
    snap = ep.write_snapshot(conn, _capture(conn), ts=AS_OF, commit=True)
    broken = json.loads(json.dumps(snap["payload"]))
    broken["eval"].pop("pending_backlog")
    complete, missing = ep.facet_complete(broken)
    assert complete is False and missing == ["pending_backlog"]


# ──────────── 判据 5：无定义不按 0% 处理 ────────────

def test_share_undefined_when_no_confirmed(conn):
    conn.execute(text("DELETE FROM outcome_evaluations WHERE repo = :r AND status = 'confirmed'"),
                 {"r": SUBJECT})
    conn.commit()
    facet = _capture(conn)
    assert facet["coverage"]["missions_confirmed"] == 0
    assert facet["independent_share"] is None and facet["auto_scan_share"] is None
    assert facet["alert_states"]["independent_share_low"] == "undefined"
    assert "independent_share" in facet["undefined_metrics"]
    assert all(a["key"] != "independent_share_low" for a in facet["alerts"])
    # 复算同样给出 None（不是 0）
    assert ep.recompute(conn, facet)["checks"]["independent_share"]["sql"] is None


# ──────────── 判据 6：周期质量报表 ────────────

def test_quality_report_renders_all_facets(conn):
    md = ep.build_digest(_capture(conn))
    for needle in ("覆盖率", "S 等级分布", "E 级分布", "复核结构", "pending 积压年龄",
                   "阈值判定", "33.3%", "coverage_low", "不加权"):
        assert needle in md, f"质量报表缺段落/数值：{needle}"
    assert ep.SCHEMA in md


# ──────────── 判据 7：零领域语义（域词经数据注入） ────────────

DOMAIN_TOKENS = ("ship", "hull", "propeller", "seakeeping", "resistance curve",
                 "船", "阻力", "推进器", "耐波", "操纵性", "稳性", "冰区", "螺旋桨", "吃水")


def test_module_has_no_domain_vocabulary():
    src = (REPO / "tools/chronicle/eval_pulse.py").read_text(encoding="utf-8").lower()
    hits = [tok for tok in DOMAIN_TOKENS if tok.lower() in src]
    assert hits == [], f"模块含领域词（应经 --subject 注入）：{hits}"
    # 主体键是参数，不是常量：不同 subject 得到不同窗口数字
    assert re.search(r"def capture\([\s\S]*?subject: str", src)