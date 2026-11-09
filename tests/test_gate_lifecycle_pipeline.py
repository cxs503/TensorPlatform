"""C6/C7 + end-to-end pipeline tests.

Covers: dispatch -> execute -> adjudicate -> ledger -> gaps -> proposals ->
seeded plan in one call; honest-FAIL protection (C7); byte-level idempotency of
per-node results (C6); blocked propagation; executor error handling; the
JSON-command executor against a real subprocess; artifact writing.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from tools.gate_lifecycle import (
    JsonCommandExecutor,
    LifecycleConfig,
    Plan,
    StaticExecutor,
    pipeline,
    write_artifacts,
)
from tools.gate_lifecycle.model import NodeResult


def crit(metric="err", op="le", threshold=0.05, required=True):
    return {"name": f"{metric}_{op}", "metric": metric, "op": op,
            "threshold": threshold, "required": required}


WAVE = {"plan_id": "wave-t", "nodes": [
    {"id": "n01", "deps": [], "duration": 120.0, "capability": "cap-a",
     "criteria": [crit()], "meta": {"measurements": {"err": 0.01},
                                    "baseline_delta": -0.4,
                                    "structural_guard": ["digest-gated pass"]}},
    {"id": "n02", "deps": [], "duration": 90.0, "capability": "cap-b",
     "criteria": [crit()], "meta": {"measurements": {"err": 0.02},
                                    "baseline_delta": -0.1}},
    {"id": "n03", "deps": ["n01"], "duration": 150.0, "capability": "cap-c",
     "criteria": [crit("score", "ge", 0.95)], "meta": {"measurements": {"score": 0.5}}},
    {"id": "n04", "deps": ["n03"], "duration": 80.0, "capability": "cap-d",
     "criteria": [crit()], "meta": {"measurements": {"err": 0.01}}},
    {"id": "n05", "deps": [], "duration": 70.0, "capability": "cap-e",
     "criteria": [crit()], "meta": {"measurements": {"err": 0.5},
                                    "declaration": {"honest_fail": True}}},
    {"id": "n06", "deps": ["n05"], "duration": 60.0, "capability": "cap-f",
     "criteria": [crit()], "meta": {"measurements": {"err": 0.01}}},
]}


def run_wave(tmp_path, name="s", capacity=16, plan=None):
    state = tmp_path / name
    state.mkdir(parents=True, exist_ok=True)
    cfg = LifecycleConfig(capacity=capacity,
                          ledger_path=state / "ledger.jsonl",
                          pool_path=state / "pool.jsonl",
                          seed_plan_id="wave-next")
    res = pipeline.run(Plan.from_dict(plan or WAVE), cfg=cfg, executor=StaticExecutor())
    return res, state


def test_end_to_end_chain_closes_a_full_cycle(tmp_path):
    res, state = run_wave(tmp_path)
    # dispatch
    assert res.invariants["schedule_problems"] == []
    assert res.schedule["capacity"] == 16
    assert res.schedule["makespan"] > 0
    # adjudication
    verdicts = {r.node_id: r.verdict for r in res.node_results}
    assert verdicts == {"n01": "PASS", "n02": "PASS", "n03": "FAIL",
                        "n04": "WITHHELD", "n05": "FAIL", "n06": "WITHHELD"}
    statuses = {r.node_id: r.status for r in res.node_results}
    assert statuses["n04"] == "blocked"           # dependency n03 failed
    assert statuses["n05"] == "honest_fail"       # protected (S-N track)
    # honest-FAIL protects the worker, not the dependents: n05 delivered no
    # result, so n06 cannot be built on it and is blocked (never a fake PASS)
    assert statuses["n06"] == "blocked"
    # ledger: every PASS registered, grades derived from evidence
    assert res.completeness["completeness"] == 1.0
    assert res.completeness["passed_nodes"] == 2
    assert res.ledger_stats["by_grade"] == {"S2": 1, "S3": 1}
    # self-regression loop
    assert res.gaps["counts"]["criterion_violation"] == 1
    assert res.gaps["counts"]["blocked_dependency"] == 2   # n04 and n06
    assert res.gaps["counts"]["negative_result"] == 1
    assert res.proposals["generated"] == len(res.gaps["gaps"])
    assert res.injection["written"] == res.proposals["generated"]
    assert len(res.seeded_plan["nodes"]) == res.proposals["generated"]
    Plan.from_dict(res.seeded_plan)  # still a valid DAG


def test_honest_fail_is_not_a_failure_but_is_in_the_negative_track(tmp_path):
    res, _ = run_wave(tmp_path)
    acc = res.failures
    assert res.node_of("n05").negative_track
    assert not res.node_of("n05").counted_as_failure
    assert acc["honest_fail"] == 1
    assert acc["negative_track"] == ["n05"]
    # failures: n03 only.  Executed = n01,n02,n03,n05 (n04/n06 blocked);
    # the denominator drops the honest FAIL -> 1/3, not 1/4.
    assert acc["failed"] == 1
    assert acc["executed"] == 4
    assert acc["denominator"] == acc["executed"] - acc["honest_fail"] == 3
    assert acc["failure_rate"] == pytest.approx(1 / 3)


def test_full_chain_is_idempotent(tmp_path):
    a, _ = run_wave(tmp_path, "one")
    b, _ = run_wave(tmp_path, "two")
    assert a.result_digest == b.result_digest
    assert a.node_digest == b.node_digest
    assert [r.to_dict() for r in a.node_results] == [r.to_dict() for r in b.node_results]
    assert a.schedule == b.schedule


def test_second_run_against_the_same_pool_injects_nothing(tmp_path):
    res1, state = run_wave(tmp_path, "s")
    plan = Plan.from_dict(WAVE)
    cfg = LifecycleConfig(capacity=16, ledger_path=state / "ledger.jsonl",
                          pool_path=state / "pool.jsonl", seed_plan_id="wave-next")
    res2 = pipeline.run(plan, cfg=cfg, executor=StaticExecutor())
    assert res2.proposals["generated"] == 0
    assert res2.injection["written"] == 0
    assert res2.injection["pool_size"] == res1.injection["pool_size"]
    assert res2.ledger_stats["appended"] == 0
    assert res2.ledger_stats["duplicates"] == res1.ledger_stats["appended"]
    # per-node adjudication is unchanged even though the bookkeeping differs
    assert res1.node_digest == res2.node_digest


def test_executor_error_becomes_failed_with_evidence(tmp_path):
    class Boom(StaticExecutor):
        def run(self, node, plan):
            raise RuntimeError("boom")

    res = pipeline.run(Plan.from_dict(WAVE), executor=Boom(),
                       cfg=LifecycleConfig(capacity=16))
    first = res.node_of("n01")
    assert first.status == "failed" and "executor_error" in first.flags
    assert "boom" in first.evidence["executor_error"]
    assert res.node_of("n03").status == "blocked"


def test_json_command_executor_runs_a_real_process(tmp_path):
    script = ("import json,sys;"
              "print('noise');"
              "print(json.dumps({'measurements':{'err':0.01},"
              "'declaration':{'note':'from subprocess'}}))")
    cmd = f"{sys.executable} -c \"{script}\""
    plan = Plan.from_dict({"plan_id": "p", "nodes": [
        {"id": "n1", "criteria": [crit()], "capability": "cap"}]})
    res = pipeline.run(plan, executor=JsonCommandExecutor(cmd, timeout=60),
                       cfg=LifecycleConfig())
    assert res.node_of("n1").verdict == "PASS"
    assert res.node_of("n1").evidence["declaration"]["note"] == "from subprocess"


def test_json_command_executor_failure_is_not_silently_a_pass(tmp_path):
    cmd = f"{sys.executable} -c \"import sys; sys.exit(3)\""
    plan = Plan.from_dict({"plan_id": "p", "nodes": [
        {"id": "n1", "criteria": [crit()], "capability": "cap"}]})
    res = pipeline.run(plan, executor=JsonCommandExecutor(cmd, timeout=60),
                       cfg=LifecycleConfig())
    assert res.node_of("n1").status == "failed"
    assert res.node_of("n1").verdict == "FAIL"


def test_artifacts_are_written_and_canonical(tmp_path):
    res, _ = run_wave(tmp_path)
    files = write_artifacts(res, tmp_path / "out")
    assert set(files) == {"result", "gap_analysis.json", "proposals.json",
                          "seeded_plan.json", "schedule.json"}
    body = json.loads(Path(files["result"]).read_text(encoding="utf-8"))
    assert body["result_digest"] == res.result_digest
    assert body["node_results"][0]["node_id"] == "n01"
    # artifacts are stable across runs for the stateless part
    res2, _ = run_wave(tmp_path, "again")
    files2 = write_artifacts(res2, tmp_path / "out2")
    b2 = json.loads(Path(files2["result"]).read_text(encoding="utf-8"))
    assert b2["result_digest"] == body["result_digest"]


def test_capacity_is_honoured_and_at_least_ten_lanes(tmp_path):
    res, _ = run_wave(tmp_path, capacity=4)
    assert res.invariants["max_parallelism"] <= 4
    res16, _ = run_wave(tmp_path, "wide", capacity=16)
    assert res16.schedule["capacity"] == 16
    assert res16.schedule["makespan"] <= res.schedule["makespan"] + 1e-9


def test_node_result_failure_accounting_properties():
    failed = NodeResult(node_id="a", status="failed", verdict="FAIL", criteria=())
    honest = NodeResult(node_id="b", status="honest_fail", verdict="FAIL", criteria=(),
                        declared_honest_fail=True)
    assert failed.counted_as_failure and not honest.counted_as_failure
    assert honest.negative_track and not failed.negative_track