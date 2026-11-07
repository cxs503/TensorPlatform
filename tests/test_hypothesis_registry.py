"""hypothesis_registry 验收测试（IN59-A2）。

判据（先于实现，机器可裁）
--------------------------
P1 登记冻结：`register` 后 `prediction_before` 的 sha256 承诺可复算且一致。
P2 开盲流跑通：register → log_run → close 全链；`verify_integrity().ok is True`。
P3 falsified 记账：权4、进 S-N 轨、`failure_kb=1`、不计失败率（bootstrap 规则）。

红队负例（≥3，必须全部被拦）
----------------------------
N1 事后补登：登记时已带证据（自述），或声明的结果文件已存在（机器事实）→ 拒。
N2 改预测：裸 SQL UPDATE prediction_before → 触发器 ABORT；
   有人先 DROP TRIGGER 再改 → `verify` 复算承诺报 commitment_mismatch。
N3 不登记先审/先跑：未登记就 log_run → 拒；裸 SQL 插 run → 触发器 ABORT；
   倒填 run_utc（早于登记时刻）→ 拒。
N4 结题单向：已结题再改 outcome（含 falsified→confirmed 翻案）→ 拒。
N5 无实验不得裁决 / 空裁决依据 / falsified 无复盘 → 拒（API 与裸 SQL 双层）。
N6 篡改历史：改 event_log 任一行 → 哈希链断，`verify` 报 chain_broken。

纪律：这些负例一律走**裸 sqlite3 语句**再走一层，证明防线在 DB 而非 Python 自觉。
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from tools import hypothesis_registry as hr

GOOD_BASIS = "判据：|Cw_num - Cw_ref|/Cw_ref = 0.031 < 0.05 (冻结规则 R1)，故 confirmed"
GOOD_POSTMORTEM = (
    "复盘：薄船理论假设兴波只由 Fn 控制，实测 hump 位置比干涉推导左移，"
    "说明振幅项(不是相位项)主导；后续应改用 Rankine 面元而非细长体积分。"
)


@pytest.fixture()
def conn(tmp_path: Path):
    c = hr.connect(tmp_path / "hr.db")
    hr.init_db(c)
    yield c
    c.close()


def _register(conn, hid="m1-h1", **over):
    kwargs = dict(
        mission_id="m1",
        hypothesis_statement="Wigley 首个 hump 出现在 Fn=0.230 附近",
        theory_basis="横向波长 lambda/L = 2*pi*Fn^2，hump 条件 lambda/L = 1/m",
        falsification_experiment_design="Fn 网格 0.18..0.42 扫 Cw，找局部极大位置",
        prediction={"first_hump_Fn": [0.21, 0.245], "direction": "peak"},
        hypothesis_id=hid,
        registered_utc="2026-11-06T00:00:00Z",
    )
    kwargs.update(over)
    return hr.register(conn, **kwargs)


# --------------------------------------------------------------------------- #
# P 正向路径
# --------------------------------------------------------------------------- #
def test_p1_register_freezes_commitment(conn):
    row = _register(conn)
    assert row["outcome"] == "pending"
    assert row["commitment_ok"] is True
    assert row["prediction_commitment"] == hr.prediction_commitment(row["prediction"])
    assert len(row["prediction_commitment"]) == 64
    assert row["prediction"]["first_hump_Fn"] == [0.21, 0.245]
    assert row["blind_guards"]["evidence_at_registration"] == []


def test_p2_blind_flow_end_to_end(conn):
    _register(conn)
    run = hr.log_run(
        conn,
        "m1-h1",
        run_ref="runs/m6329/fn_sweep.json",
        payload={"fn": 0.23, "cw": 1.2e-3},
        run_utc="2026-11-06T01:00:00Z",
    )
    assert run["run_seq"] == 1
    closed = hr.close(
        conn,
        "m1-h1",
        outcome="confirmed",
        verdict_basis=GOOD_BASIS,
        outcome_utc="2026-11-06T02:00:00Z",
        computed={"first_hump_Fn": 0.2295},
    )
    assert closed["outcome"] == "confirmed"
    assert closed["sn"]["sn_track"] == "S-C"
    assert len(closed["runs"]) == 1
    rep = hr.verify_integrity(conn)
    assert rep["ok"] is True, rep["failures"]
    assert rep["n_hypotheses"] == 1
    assert rep["n_events"] == 3  # registered + run_logged + closed


def test_p3_falsified_books_sn_weight_4(conn):
    _register(conn)
    hr.log_run(conn, "m1-h1", run_ref="run-1", run_utc="2026-11-06T01:00:00Z")
    closed = hr.close(
        conn,
        "m1-h1",
        outcome="falsified",
        verdict_basis=GOOD_BASIS,
        postmortem_note=GOOD_POSTMORTEM,
    )
    assert closed["sn"]["sn_weight"] == 4.0
    assert closed["sn"]["sn_track"] == "S-N"
    assert closed["sn"]["failure_kb"] == 1
    assert closed["sn"]["counts_in_failure_rate"] is False
    assert closed["failure_kb"] == 1
    summary = hr.summary(conn, "m1")
    assert summary["by_outcome"]["falsified"] == 1
    assert summary["sn_track"]["S-N"] == {"count": 1, "weight": 4.0}
    assert summary["failure_kb_entries"] == 1
    assert summary["integrity_ok"] is True


# --------------------------------------------------------------------------- #
# N1 事后补登（post-hoc registration）
# --------------------------------------------------------------------------- #
def test_n1_posthoc_registration_by_evidence_list_refused(conn):
    with pytest.raises(hr.RegistryError, match="post-hoc registration refused"):
        _register(conn, evidence_at_registration=["runs/m6329/fn_sweep.json"])
    assert hr.list_hypotheses(conn) == []


def test_n1_posthoc_registration_by_existing_result_file_refused(conn, tmp_path):
    result = tmp_path / "fn_sweep_result.json"
    result.write_text(json.dumps({"first_hump_Fn": 0.2295}), encoding="utf-8")
    with pytest.raises(hr.RegistryError, match="already hold results"):
        _register(conn, result_paths=[result])
    assert hr.list_hypotheses(conn) == []


def test_n1_registration_accepted_when_result_path_absent(conn, tmp_path):
    """负控的正向对照：同一路径在结果产出**前**登记 → 通过（否则门就是死的）。"""
    result = tmp_path / "not_yet.json"
    row = _register(conn, result_paths=[result])
    assert row["blind_guards"]["result_paths_absent"] is True


# --------------------------------------------------------------------------- #
# N2 改预测（tamper）
# --------------------------------------------------------------------------- #
def test_n2_prediction_update_blocked_by_trigger(conn):
    _register(conn)
    with pytest.raises(sqlite3.IntegrityError, match="immutable"):
        conn.execute(
            "UPDATE hypothesis_registry SET prediction_before = ? WHERE hypothesis_id = ?",
            (json.dumps({"first_hump_Fn": [0.30, 0.32]}), "m1-h1"),
        )
    # 承诺字段本身也冻结
    with pytest.raises(sqlite3.IntegrityError, match="immutable"):
        conn.execute(
            "UPDATE hypothesis_registry SET prediction_commitment = ? WHERE hypothesis_id = ?",
            ("0" * 64, "m1-h1"),
        )
    assert hr.verify_integrity(conn)["ok"] is True


def test_n2_tamper_detected_even_if_trigger_dropped(conn):
    """有人先 DROP TRIGGER 再改预测：写入会成功，但 verify 复算承诺必抓出来。"""
    _register(conn)
    conn.execute("DROP TRIGGER trg_hr_immutable_core")
    conn.execute(
        "UPDATE hypothesis_registry SET prediction_before = ? WHERE hypothesis_id = ?",
        (json.dumps({"first_hump_Fn": [0.22, 0.221]}), "m1-h1"),
    )
    rep = hr.verify_integrity(conn)
    assert rep["ok"] is False
    assert any("commitment_mismatch" in f for f in rep["failures"]), rep["failures"]
    assert rep["details"]["commitment"]["bad"] == ["m1-h1:commitment_mismatch"]


# --------------------------------------------------------------------------- #
# N3 不登记先审 / 先跑 + 倒填时间戳
# --------------------------------------------------------------------------- #
def test_n3_run_without_registration_refused(conn):
    with pytest.raises(hr.RegistryError, match="not registered"):
        hr.log_run(conn, "ghost-h1", run_ref="run-x")
    # 裸 SQL 也插不进去（触发器）
    with pytest.raises(sqlite3.IntegrityError, match="unregistered hypothesis"):
        conn.execute(
            "INSERT INTO experiment_runs (hypothesis_id, run_seq, run_ref, run_utc, payload, logged_utc)"
            " VALUES ('ghost-h1', 1, 'run-x', '2026-11-06T01:00:00Z', '{}', '2026-11-06T01:00:00Z')"
        )


def test_n3_verdict_without_registration_refused(conn):
    with pytest.raises(hr.RegistryError, match="not registered"):
        hr.close(conn, "ghost-h1", outcome="confirmed", verdict_basis=GOOD_BASIS)


def test_n3_backdated_run_refused(conn):
    _register(conn)  # registered_utc = 2026-11-06T00:00:00Z
    with pytest.raises(hr.RegistryError, match="backdated run refused"):
        hr.log_run(conn, "m1-h1", run_ref="run-early", run_utc="2026-11-05T23:00:00Z")
    with pytest.raises(sqlite3.IntegrityError, match="backdated run"):
        conn.execute(
            "INSERT INTO experiment_runs (hypothesis_id, run_seq, run_ref, run_utc, payload, logged_utc)"
            " VALUES ('m1-h1', 9, 'run-early', '2026-11-05T23:00:00Z', '{}', '2026-11-06T03:00:00Z')"
        )
    # 台账保持干净：没有半条 run
    assert hr.get(conn, "m1-h1")["runs"] == []


# --------------------------------------------------------------------------- #
# N4 结题单向 / 翻案
# --------------------------------------------------------------------------- #
def test_n4_outcome_flip_refused(conn):
    _register(conn)
    hr.log_run(conn, "m1-h1", run_ref="run-1", run_utc="2026-11-06T01:00:00Z")
    hr.close(conn, "m1-h1", outcome="falsified", verdict_basis=GOOD_BASIS,
             postmortem_note=GOOD_POSTMORTEM)
    with pytest.raises(hr.RegistryError, match="already closed"):
        hr.close(conn, "m1-h1", outcome="confirmed", verdict_basis=GOOD_BASIS)
    with pytest.raises(sqlite3.IntegrityError, match="terminal"):
        conn.execute(
            "UPDATE hypothesis_registry SET outcome = 'confirmed' WHERE hypothesis_id = 'm1-h1'"
        )
    assert hr.get(conn, "m1-h1")["outcome"] == "falsified"


def test_n4_run_after_close_refused(conn):
    _register(conn)
    hr.log_run(conn, "m1-h1", run_ref="run-1", run_utc="2026-11-06T01:00:00Z")
    hr.close(conn, "m1-h1", outcome="inconclusive", verdict_basis=GOOD_BASIS)
    with pytest.raises(hr.RegistryError, match="already closed"):
        hr.log_run(conn, "m1-h1", run_ref="run-2", run_utc="2026-11-06T05:00:00Z")


# --------------------------------------------------------------------------- #
# N5 无实验不得裁决 / 空依据 / falsified 无复盘
# --------------------------------------------------------------------------- #
def test_n5_no_verdict_without_run(conn):
    _register(conn)
    with pytest.raises(hr.RegistryError, match="no experiment run recorded"):
        hr.close(conn, "m1-h1", outcome="confirmed", verdict_basis=GOOD_BASIS)
    with pytest.raises(sqlite3.IntegrityError, match="no experiment run recorded"):
        conn.execute(
            "UPDATE hypothesis_registry SET outcome='confirmed',"
            " verdict_basis='一二三四五六七八九十一二三四五六七八九十一二三四五'"
            " WHERE hypothesis_id='m1-h1'"
        )
    assert hr.get(conn, "m1-h1")["outcome"] == "pending"


def test_n5_thin_basis_refused(conn):
    _register(conn)
    hr.log_run(conn, "m1-h1", run_ref="run-1", run_utc="2026-11-06T01:00:00Z")
    with pytest.raises(hr.RegistryError, match="verdict_basis must be"):
        hr.close(conn, "m1-h1", outcome="confirmed", verdict_basis="ok")
    with pytest.raises(sqlite3.IntegrityError, match="verdict_basis required"):
        conn.execute(
            "UPDATE hypothesis_registry SET outcome='confirmed', verdict_basis='ok'"
            " WHERE hypothesis_id='m1-h1'"
        )


def test_n5_falsified_without_postmortem_refused(conn):
    _register(conn)
    hr.log_run(conn, "m1-h1", run_ref="run-1", run_utc="2026-11-06T01:00:00Z")
    with pytest.raises(hr.RegistryError, match="postmortem_note"):
        hr.close(conn, "m1-h1", outcome="falsified", verdict_basis=GOOD_BASIS)
    with pytest.raises(sqlite3.IntegrityError, match="postmortem_note"):
        conn.execute(
            "UPDATE hypothesis_registry SET outcome='falsified', verdict_basis=?"
            " WHERE hypothesis_id='m1-h1'",
            (GOOD_BASIS,),
        )
    assert hr.get(conn, "m1-h1")["outcome"] == "pending"


def test_n5_non_terminal_outcome_refused(conn):
    _register(conn)
    hr.log_run(conn, "m1-h1", run_ref="run-1", run_utc="2026-11-06T01:00:00Z")
    with pytest.raises(hr.RegistryError, match="must be one of"):
        hr.close(conn, "m1-h1", outcome="pending", verdict_basis=GOOD_BASIS)


# --------------------------------------------------------------------------- #
# N6 篡改历史（事件链）
# --------------------------------------------------------------------------- #
def test_n6_event_log_append_only_and_chain_detects_tamper(conn):
    _register(conn)
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        conn.execute("UPDATE event_log SET kind='x' WHERE seq=1")
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        conn.execute("DELETE FROM event_log WHERE seq=1")
    conn.execute("DROP TRIGGER trg_event_no_update")
    conn.execute("UPDATE event_log SET payload_sha=? WHERE seq=1", ("0" * 64,))
    rep = hr.verify_integrity(conn)
    assert rep["ok"] is False
    assert any("chain_broken" in f for f in rep["failures"]), rep["failures"]


# --------------------------------------------------------------------------- #
# 取值域 CHECK / 重复登记 / 摘要
# --------------------------------------------------------------------------- #
def test_outcome_domain_enforced_at_db_level(conn):
    _register(conn)
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("UPDATE hypothesis_registry SET outcome='maybe' WHERE hypothesis_id='m1-h1'")


def test_duplicate_hypothesis_id_refused(conn):
    _register(conn)
    with pytest.raises(hr.RegistryError, match="UNIQUE|unique"):
        _register(conn)


def test_register_requires_non_empty_narrative_and_prediction(conn):
    with pytest.raises(hr.RegistryError, match="missing required field"):
        _register(conn, theory_basis="   ")
    with pytest.raises(hr.RegistryError, match="prediction_before"):
        _register(conn, prediction={"": ""})


def test_summary_counts_multiple(conn):
    _register(conn, hid="m1-a")
    _register(conn, hid="m1-b")
    _register(conn, hid="m1-c")
    for hid in ("m1-a", "m1-b"):
        hr.log_run(conn, hid, run_ref=f"{hid}.json", run_utc="2026-11-06T01:00:00Z")
    hr.close(conn, "m1-a", outcome="confirmed", verdict_basis=GOOD_BASIS)
    hr.close(conn, "m1-b", outcome="falsified", verdict_basis=GOOD_BASIS,
             postmortem_note=GOOD_POSTMORTEM)
    s = hr.summary(conn, "m1")
    assert s["n_hypotheses"] == 3
    assert s["n_closed"] == 2 and s["n_pending"] == 1
    assert s["by_outcome"] == {"pending": 1, "confirmed": 1, "falsified": 1, "inconclusive": 0}
    assert s["sn_track"]["S-C"] == {"count": 1, "weight": 1.0}
    assert s["sn_track"]["S-N"] == {"count": 1, "weight": 4.0}
    assert s["failure_kb_entries"] == 1
    md = hr.render_markdown(s)
    assert "m1-b" in md and "FAIL" not in md.splitlines()[5]


def test_pg_ddl_mirrors_guards():
    ddl = hr.pg_ddl()
    for token in ("hypothesis_registry", "experiment_runs", "event_log",
                  "trg_run_guard", "trg_hr_immutable_core", "postmortem_note",
                  "CHECK (outcome IN"):
        assert token in ddl


def test_cli_roundtrip(tmp_path):
    db = tmp_path / "cli.db"
    spec = {
        "mission_id": "mC",
        "hypothesis_id": "mC-h1",
        "hypothesis_statement": "CLI 登记的假设",
        "theory_basis": "推导见 tools/hypothesis_registry.py docstring",
        "falsification_experiment_design": "同一命令连跑两次比对产物",
        "prediction_before": {"delta_pct": 0.0, "direction": "equal"},
        "registered_utc": "2026-11-06T00:00:00Z",
    }
    prereg = tmp_path / "prereg.json"
    prereg.write_text(json.dumps([spec], ensure_ascii=False), encoding="utf-8")
    assert hr.main(["init", "--db", str(db)]) == 0
    assert hr.main(["register", "--db", str(db), "--file", str(prereg)]) == 0
    assert hr.main(["run", "--db", str(db), "--hid", "mC-h1", "--ref", "runs/r1.json",
                    "--run-utc", "2026-11-06T01:00:00Z"]) == 0
    basis = tmp_path / "basis.txt"
    basis.write_text(GOOD_BASIS, encoding="utf-8")
    assert hr.main(["close", "--db", str(db), "--hid", "mC-h1", "--outcome", "confirmed",
                    "--basis-file", str(basis)]) == 0
    out_json = tmp_path / "summary.json"
    assert hr.main(["report", "--db", str(db), "--mission", "mC", "--out-json", str(out_json)]) == 0
    data = json.loads(out_json.read_text(encoding="utf-8"))
    assert data["n_hypotheses"] == 1 and data["integrity_ok"] is True
    assert (tmp_path / "hypothesis_registry_pg.sql").exists()
    assert hr.main(["verify", "--db", str(db)]) == 0
    assert hr.main(["sql", "--db", str(db), "--mission", "mC"]) == 0

# --------------------------------------------------------------------------- #
# 冻结规则执行器：判据由机器执行（不是结题时再商量）
# --------------------------------------------------------------------------- #
def test_rule_engine_three_verdicts():
    rule = {
        "rule_id": "R1",
        "all": [
            {"metric": "n_humps", "op": ">=", "value": 3},
            {"metric": "first_hump_Fn", "op": "in_range", "range": [0.21, 0.245]},
        ],
    }
    ok = hr.evaluate_rule(rule, {"n_humps": 3, "first_hump_Fn": 0.2295})
    assert ok["verdict"] == "confirmed"
    bad = hr.evaluate_rule(rule, {"n_humps": 2, "first_hump_Fn": 0.2295})
    assert bad["verdict"] == "falsified"
    assert bad["failed_metrics"] == ["n_humps"]
    missing = hr.evaluate_rule(rule, {"n_humps": 4})
    assert missing["verdict"] == "inconclusive"
    assert missing["missing_metrics"] == ["first_hump_Fn"]


def test_validate_rule_rejects_unexecutable_rule():
    with pytest.raises(hr.RegistryError):
        hr.validate_rule({"all": []})
    with pytest.raises(hr.RegistryError):
        hr.validate_rule({"all": [{"metric": "x", "op": "roughly", "value": 1}]})
    with pytest.raises(hr.RegistryError):
        hr.validate_rule({"all": [{"metric": "x", "op": "in_range", "range": [1]}]})


def test_register_refuses_prediction_with_unexecutable_rule(conn):
    with pytest.raises(hr.RegistryError, match="op"):
        _register(conn, prediction={"rule": {"all": [{"metric": "m", "op": "kind_of", "value": 1}]}})
    assert hr.list_hypotheses(conn) == []


def test_register_accepts_prediction_with_executable_rule(conn):
    rule = {"rule_id": "R-x", "all": [{"metric": "delta_pct", "op": "<", "value": 3.0}]}
    row = _register(conn, prediction={"predicted": {"delta_pct": 1.2}, "rule": rule})
    assert row["prediction"]["rule"]["all"][0]["metric"] == "delta_pct"
    assert hr.evaluate_rule(row["prediction"]["rule"], {"delta_pct": 1.1})["verdict"] == "confirmed"
