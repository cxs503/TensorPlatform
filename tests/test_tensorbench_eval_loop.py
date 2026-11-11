"""tb-eval-loop 回路的可证性测试：递交 → 取证 → 真增量 → L4 判据（含红队实弹）。

全部用 SQLite 内存库（与生产 PostgreSQL 同 DDL 子集）；不联网、不跑真实基准。
真实基准跑在集群上，见 artifacts/tensorbench/live_eval.json（本文件只证机制）。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.maturity_gate import benchmark_source  # noqa: E402
from tools.tensorbench_eval import contract, store  # noqa: E402

SPEC = {
    "benchmark": {"benchmark_id": "synthetic.demo", "version": "1.0.0"},
    "workload": {"workload_id": "demo-run", "version": "1.0.0",
                 "command": ["python3", "-c", "print('{}')"]},
    "evaluation": {"metrics": [
        {"name": "value_a", "got_pointer": "measured_a", "ref_pointer": "exact_a",
         "tolerance_rel": 0.01, "unit": "1"}]},
    "execution": {"timeout_seconds": 60},
}

STDOUT = json.dumps({"measured_a": 10.05, "exact_a": 10.0}) + "\n"


def _result(benchmark_id: str = "synthetic.demo", status: str = "success") -> dict:
    return {
        "schema_version": "1.0.0", "run_id": "tb-abcdef",
        "benchmark": {"benchmark_id": benchmark_id, "version": "1.0.0"},
        "implementation": {"project": "SyntheticSolver", "revision": "deadbeef", "dirty": False},
        "workload": {"workload_id": "demo-run", "version": "1.0.0", "dataset_refs": [],
                     "configuration": {}},
        "environment": {"os": "linux", "architecture": "synthetic64"},
        "execution": {"status": status, "started_at": "2026-11-11T00:00:00+00:00",
                      "ended_at": "2026-11-11T00:00:01+00:00", "duration_seconds": 1.0,
                      "exit_code": 0, "command": "python3 -c print('{}')"},
        "metrics": [{"name": "wall_time", "value": 1.0, "unit": "seconds", "kind": "measured"}],
        "artifacts": [],
        "validation": {"schema": "not_checked", "execution": "passed",
                       "correctness": "not_checked", "reproducibility": "not_checked",
                       "comparability": "not_checked", "details": ""},
    }


@pytest.fixture()
def conn():
    engine = create_engine("sqlite:///:memory:", future=True)
    with engine.connect() as c:
        for stmt in (
            "CREATE TABLE tensor_dev_missions (id INTEGER PRIMARY KEY, organization_id INTEGER,"
            " goal TEXT, repo TEXT, result TEXT)",
            "CREATE TABLE cluster_jobs (id INTEGER PRIMARY KEY, repo TEXT, ref TEXT, mode TEXT,"
            " cmd TEXT, mid INTEGER, submitter TEXT, state TEXT, rc INTEGER, node TEXT, timeout INTEGER,"
            " submitted_at TEXT, note TEXT)",
            "CREATE TABLE tensor_capability_deltas (id INTEGER PRIMARY KEY, organization_id INTEGER,"
            " mission_id INTEGER, category TEXT, summary TEXT, reason TEXT, artifacts TEXT,"
            " evidence TEXT, reviewer TEXT)",
        ):
            c.execute(text(stmt))
        c.commit()
        yield c


def _mission(conn, *, goal="plain work", result=None, mid=7001):
    conn.execute(text("INSERT INTO tensor_dev_missions (id, organization_id, goal, repo, result)"
                      " VALUES (:mid, 1, :goal, 'SyntheticSolver', :res)"),
                 {"mid": mid, "goal": goal, "res": json.dumps(result or {})})
    conn.commit()


def _job(conn, *, state="succeeded", mid=7001, repo="SyntheticSolver"):
    jid = conn.execute(text(
        "INSERT INTO cluster_jobs (repo, ref, mode, cmd, mid, submitter, state, node, timeout,"
        " submitted_at, note) VALUES (:repo, 'main', 'run', 'python3 -m tensorbench.cli run',"
        " :mid, 'agent', :state, NULL, 600, '2026-11-11T00:00:00+00:00', 'tensorbench_eval')"
    ), {"repo": repo, "mid": mid, "state": state}).lastrowid
    conn.commit()
    return int(jid)


# ───────────────────────── 契约层 ─────────────────────────
def test_parse_spec_is_strict():
    with pytest.raises(contract.ContractError):
        contract.parse_spec({**SPEC, "bogus": 1})
    with pytest.raises(contract.ContractError):
        contract.parse_spec({**SPEC, "evaluation": {"metrics": []}})
    parsed = contract.parse_spec(SPEC)
    assert parsed["sha256"] == contract.spec_digest(SPEC)


def test_metrics_recomputed_by_platform():
    parsed = contract.parse_spec(SPEC)
    doc = contract.extract_document("log line before\n" + STDOUT)
    rows = contract.evaluate_metrics(parsed, doc)
    assert rows[0]["metric"] == "value_a"
    assert rows[0]["rel_error"] == pytest.approx(0.005)
    assert rows[0]["passed"] is True


def test_missing_pointer_never_invents_a_number():
    parsed = contract.parse_spec(SPEC)
    with pytest.raises(contract.ContractError):
        contract.evaluate_metrics(parsed, {"measured_a": 1.0})
    with pytest.raises(contract.ContractError):
        contract.extract_document("no json here")


# ───────────────────────── ① 触发器 ─────────────────────────
def test_trigger_matches_anchor_or_level():
    assert store.needs_evaluation({"goal": "work 【锚点=FEAT-TENSORBENCH】x"})["trigger"]
    lvl = store.needs_evaluation({"goal": "plain",
                                  "result": {"verdict": {"maturity_level": "L4"}}})
    assert lvl["trigger"] and lvl["declared_level"] == 4
    assert not store.needs_evaluation({"goal": "plain", "result": {"verdict": {"level": 3}}})["trigger"]


def test_completion_submits_queue_job_without_node(conn):
    _mission(conn, goal="【锚点=FEAT-TENSORBENCH】benchmark me",
             result={"tensorbench": {"spec": SPEC, "ref": "main"}})
    out = store.on_mission_terminal(conn, 7001, status="succeeded")
    assert out["evaluated"] is True and out["node"] is None
    job = conn.execute(text("SELECT node, state, mid, cmd FROM cluster_jobs WHERE id = :j"),
                       {"j": out["cluster_job_id"]}).mappings().first()
    assert job["node"] is None and job["state"] == "queued" and job["mid"] == 7001
    assert "tensorbench.cli" in job["cmd"]
    ev = conn.execute(text("SELECT state FROM tensorbench_evaluations"
                           " WHERE mission_id = 7001")).mappings().first()
    assert ev["state"] == "submitted"


def test_no_spec_is_recorded_not_fabricated(conn):
    _mission(conn, goal="【锚点=FEAT-TENSORBENCH】but no spec")
    out = store.on_mission_terminal(conn, 7001, status="succeeded")
    assert out["evaluated"] is False and out["reason"] == "no_benchmark_spec_bound"
    n = conn.execute(text("SELECT count(*) FROM cluster_jobs")).scalar()
    assert n == 0
    assert conn.execute(text("SELECT state FROM tensorbench_evaluations")).scalar() == "no_spec"


# ───────────────────────── ② 取证 + ③ 真增量 ─────────────────────────
def test_ingest_requires_succeeded_job_and_matching_mission(conn):
    _mission(conn)
    queued = _job(conn, state="queued")
    with pytest.raises(contract.ContractError):
        store.ingest_result(conn, mission_id=7001, cluster_job_id=queued, spec=SPEC,
                            result=_result(), workload_stdout=STDOUT)
    other = _job(conn, mid=9999)
    with pytest.raises(contract.ContractError):
        store.ingest_result(conn, mission_id=7001, cluster_job_id=other, spec=SPEC,
                            result=_result(), workload_stdout=STDOUT)


def test_ingest_lands_full_row_and_books_true_delta(conn):
    _mission(conn)
    jid = _job(conn)
    out = store.ingest_result(conn, mission_id=7001, cluster_job_id=jid, spec=SPEC,
                              result=_result(), workload_stdout=STDOUT, organization_id=1)
    assert out["verified"] is True and len(out["result_ids"]) == 1
    row = conn.execute(text("SELECT * FROM tensorbench_results")).mappings().first()
    for key in ("mission_id", "benchmark_id", "ref_value", "got_value", "rel_error",
                "recorded_at", "run_id", "cluster_job_id"):
        assert row[key] is not None, key
    assert row["ref_value"] == pytest.approx(10.0) and row["got_value"] == pytest.approx(10.05)
    delta = conn.execute(text("SELECT category, evidence FROM tensor_capability_deltas")).mappings().first()
    assert delta["category"] == "bench_verified"
    assert json.loads(delta["evidence"])["cluster_job_id"] == jid


def test_true_delta_upgrades_no_substantive_delta(conn):
    _mission(conn)
    conn.execute(text("INSERT INTO tensor_capability_deltas (organization_id, mission_id, category,"
                      " summary, reason, artifacts, evidence, reviewer) VALUES (1, 7001,"
                      " 'no_substantive_delta', 'nothing', 'not declared', '[]', '{}', 'approver')"))
    conn.commit()
    out = store.ingest_result(conn, mission_id=7001, cluster_job_id=_job(conn), spec=SPEC,
                              result=_result(), workload_stdout=STDOUT)
    assert out["delta"]["action"] == "upgraded"
    assert conn.execute(text("SELECT category FROM tensor_capability_deltas")).scalar() == "bench_verified"


def test_ingest_refuses_benchmark_identity_mismatch(conn):
    _mission(conn)
    with pytest.raises(contract.ContractError):
        store.ingest_result(conn, mission_id=7001, cluster_job_id=_job(conn), spec=SPEC,
                            result=_result(benchmark_id="other.bench"), workload_stdout=STDOUT)


# ───────────────────────── ④ 红队：L4 可失败 ─────────────────────────
def test_red_team_forged_l4_claim_is_claimed_not_proven(conn):
    """实弹用例：声明 L4 + evidence 里写满漂亮数字，但库里没有任何实证行。"""
    _mission(conn, goal="【锚点=FEAT-TENSORBENCH】交付", result={
        "verdict": {"maturity_level": "L4"},
        "evidence": {"benchmark": "added_mass ref 3220.13 got 3225.33 误差 0.16% PASS"},
    })
    mission = conn.execute(text("SELECT id, goal, result FROM tensor_dev_missions")).mappings().first()
    rows = store.rows_for_mission(conn, 7001)
    v = benchmark_source.verdict(rows, claimed_level=store.declared_level(dict(mission)))
    assert v["status"] == benchmark_source.CLAIMED_NOT_PROVEN
    assert v["l4_benchmark_pass"] is False
    assert "self-declared" in v["reason"]


def test_red_team_upload_style_row_cannot_pass_l4(conn):
    """伪造行（source=upload / 无集群作业绑定）不构成 L4 凭据。"""
    forged = [{"metric": "value_a", "ref_value": 10.0, "got_value": 10.0, "rel_error": 0.0,
               "tolerance_rel": 0.01, "passed": True, "execution_status": "success",
               "source": "upload", "cluster_job_id": None}]
    assert benchmark_source.verdict(forged)["status"] == benchmark_source.FAILED


def test_l4_verified_only_after_real_ingest(conn):
    _mission(conn, result={"verdict": {"maturity_level": "L4"}})
    assert benchmark_source.verdict(store.rows_for_mission(conn, 7001))["status"] == \
        benchmark_source.CLAIMED_NOT_PROVEN
    store.ingest_result(conn, mission_id=7001, cluster_job_id=_job(conn), spec=SPEC,
                        result=_result(), workload_stdout=STDOUT)
    v = benchmark_source.verdict(store.rows_for_mission(conn, 7001), claimed_level=4)
    assert v["status"] == benchmark_source.VERIFIED and v["l4_benchmark_pass"] is True
    assert v["best"]["rel_error"] == pytest.approx(0.005)


def test_tolerance_failure_is_failed_not_claimed(conn):
    loose = json.loads(json.dumps(SPEC))
    loose["evaluation"]["metrics"][0]["tolerance_rel"] = 0.001
    _mission(conn)
    out = store.ingest_result(conn, mission_id=7001, cluster_job_id=_job(conn), spec=loose,
                              result=_result(), workload_stdout=STDOUT)
    assert out["verified"] is False
    v = benchmark_source.verdict(store.rows_for_mission(conn, 7001))
    assert v["status"] == benchmark_source.FAILED
    assert v["failed_checks"] == ["within_tolerance"]
