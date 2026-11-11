"""Tests for tools/validation/terminal_attribution.py.

Layers, deliberately separated:
  * contract tests   — the six-class separation, the tool-call ladder, the requeue gate,
                       the one-unit-per-node budget ledger, snapshot and idempotency
                       guarantees, all on synthetic fixtures (always valid);
  * neutrality guard — the mechanics-only module must not carry domain vocabulary; the
                       lexicon is injected, never hardcoded here.
"""
import json
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools", "validation"))

import terminal_attribution as ta  # noqa: E402

ARTIFACTS = os.path.join(ROOT, "artifacts", "p226w3_n09")


# ───────────────────────────── fixtures ─────────────────────────────────
def rec(mid=1, status="failed", result=None, task_key=None, session=None, goal=None):
    """Synthetic terminal record — no domain content by construction."""
    out = {"mission_id": mid, "status": status, "result": result if result is not None else {}}
    if task_key:
        out["task_key"] = task_key
    if session is not None:
        out["session"] = session
    if goal:
        out["goal"] = goal
    return out


def sess(tool_calls=None, events=3, rounds=0, decision_status=None, stop=None):
    return {"events": events, "rounds": rounds, "decision_tool_calls": tool_calls,
            "decision_status": decision_status, "stop_condition": stop}


def cls(record):
    return ta.attribute(record)["class"]


# ──────────────────────── six-class separation ──────────────────────────
def test_policy_refusal_is_policy_blocked_not_idle():
    r = rec(result={"evidence": {"checks": {"gate": "policy_blocked"}}, "tool_calls": 0})
    out = ta.attribute(r)
    assert out["class"] == ta.CLASS_POLICY_BLOCKED
    assert out["rule"] == "R1_POLICY"
    assert out["infra"] is False


def test_composite_budget_token_is_budget_exhausted():
    out = ta.attribute(rec(result={"fail_stage": "budget_exhausted+forced_finish",
                                   "tool_calls": 12}))
    assert out["class"] == ta.CLASS_BUDGET_EXHAUSTED
    assert out["signals"] == ["result.fail_stage:budget_exhausted",
                              "result.fail_stage:forced_finish"]


def test_model_side_error_is_model_error():
    assert cls(rec(result={"fail_stage": "llm_error", "tool_calls": 0})) == ta.CLASS_MODEL_ERROR
    assert cls(rec(result={"fail_stage": "parse_error", "tool_calls": 3})) == ta.CLASS_MODEL_ERROR


def test_session_loss_after_work_is_infra_lost_not_physics():
    out = ta.attribute(rec(result={"tool_calls": 41}, session=sess(stop="agent_lost")))
    assert out["class"] == ta.CLASS_INFRA_LOST
    assert out["tool_calls"] == 41          # work really happened
    assert out["is_failure"] is True


def test_worker_crash_before_agent_start_is_infra_no_toolcall():
    r = rec(result={"evidence": {"checks": {"gate": "worker-crash"}},
                    "headline": "worker 异常: worktree failed",
                    "traceback_tail": "File 'w.py', in prepare_worktree"})
    out = ta.attribute(r)
    assert out["class"] == ta.CLASS_INFRA_NO_TOOLCALL
    assert out["tool_calls"] == 0
    assert out["tool_calls_source"] == "worker_crash_prestart"
    assert out["finish_called"] is False    # a crash headline is not finish evidence


def test_zero_tool_calls_without_explanation_is_infra_no_toolcall():
    out = ta.attribute(rec(result={"tool_calls": 0}))
    assert out["class"] == ta.CLASS_INFRA_NO_TOOLCALL
    assert out["rule"] == "R5_INFRA_NO_TOOLCALL"


def test_finish_fail_after_work_is_physics_honest_fail():
    r = rec(result={"tool_calls": 57, "accepted": False, "declared_summary": "tried, failed"})
    out = ta.attribute(r)
    assert out["class"] == ta.CLASS_PHYSICS_HONEST_FAIL
    assert out["finish_verdict"] == "fail"
    assert out["infra"] is False


def test_finish_ok_after_work_is_delivered():
    out = ta.attribute(rec(status="succeeded", result={"tool_calls": 30, "accepted": True}))
    assert out["class"] == ta.CLASS_DELIVERED_OK
    assert out["is_failure"] is False


def test_delivery_outranks_stale_runtime_token():
    """A finished+accepted mission carrying a forced-finish marker is delivered, not lost."""
    r = rec(status="succeeded", result={"tool_calls": 100, "accepted": True,
                                       "fail_stage": "budget_exhausted+forced_finish"})
    out = ta.attribute(r)
    assert out["class"] == ta.CLASS_DELIVERED_OK
    assert out["rule"] == "R0_DELIVERED"


def test_explicit_cause_outranks_zero_toolcall_symptom():
    """A policy/budget/model cause must not be reported as anonymous idling."""
    r = rec(result={"tool_calls": 0, "evidence": {"checks": {"gate": "policy_denied"}}})
    assert cls(r) == ta.CLASS_POLICY_BLOCKED
    r2 = rec(result={"tool_calls": 0, "fail_stage": "budget_exhausted"})
    assert cls(r2) == ta.CLASS_BUDGET_EXHAUSTED


def test_budget_outranks_physics_when_both_hold():
    r = rec(result={"tool_calls": 90, "declared_summary": "x", "fail_stage": "budget_exhausted"})
    assert cls(r) == ta.CLASS_BUDGET_EXHAUSTED


# ────────────────────────── tool-call ladder ────────────────────────────
def test_ladder_prefers_worker_counter_then_decision_payload():
    a = ta.extract(rec(result={"tool_calls": 7}, session=sess(tool_calls=5)))
    assert (a.tool_calls, a.tool_calls_source) == (7, "result.tool_calls")
    b = ta.extract(rec(result={}, session=sess(tool_calls=5)))
    assert (b.tool_calls, b.tool_calls_source) == (5, "session.decision")


def test_ladder_changed_files_gives_labelled_lower_bound():
    ev = ta.extract(rec(result={"changed_files": ["a.txt"]}))
    assert ev.tool_calls == 1
    assert ev.tool_calls_source == "changed_files_lower_bound"


def test_no_evidence_at_all_is_unknown_and_lowers_coverage():
    # a session existed (so the agent was claimed) but left no counter, no finish marker
    r = rec(status="failed", result={}, session={"events": 3, "rounds": 0})
    out = ta.attribute(r)
    assert out["class"] == ta.CLASS_UNKNOWN and out["tool_calls"] is None
    assert ta.attribution_coverage([out]) == 0.0


@pytest.mark.parametrize("status,expected,rule_prefix", [
    ("stopped", ta.CLASS_INFRA_LOST, "R8"),
    ("revoked", ta.CLASS_POLICY_BLOCKED, "R9"),
    ("succeeded", ta.CLASS_DELIVERED_OK, "R0"),
])
def test_status_fallback_only_after_evidence_rules(status, expected, rule_prefix):
    out = ta.attribute(rec(status=status, result={"note": "no machine counters"}))
    assert out["class"] == expected
    assert out["rule"].startswith(rule_prefix)


def test_coverage_is_one_on_complete_population():
    rows = [rec(mid=i, status="failed", result={"tool_calls": 0}) for i in range(200)]
    results = ta.attribute_all(rows)
    assert ta.attribution_coverage(results) == 1.0
    assert sum(1 for r in results if r["class"] == ta.CLASS_INFRA_NO_TOOLCALL) == 200


# ───────────────────────────── KPIs ─────────────────────────────────────
def test_effective_attempt_rate_counts_only_work_plus_finish():
    rows = [
        rec(mid=1, status="succeeded", result={"tool_calls": 4, "accepted": True}),
        rec(mid=2, status="failed", result={"tool_calls": 0}),
        rec(mid=3, status="failed", result={"tool_calls": 9}),          # worked, no finish
        rec(mid=4, status="failed", result={}, session=sess(events=0)),
    ]
    kpi = ta.effective_attempt_rate(rows)
    assert kpi["dispatched"] == 4
    assert kpi["with_tool_calls"] == 2
    assert kpi["effective"] == 1
    assert kpi["effective_attempt_rate"] == pytest.approx(0.25)


def test_requeue_yield_counts_replays_that_reached_a_worker():
    pairs = [
        {"node_key": "n1", "result": {"tool_calls": 3}, "status": "failed"},
        {"node_key": "n1", "result": {"tool_calls": 0}, "status": "failed"},
        {"node_key": "n2", "result": {"tool_calls": 6}, "status": "failed"},
    ]
    out = ta.requeue_yield(pairs)
    assert out["replays"] == 3 and out["reached_worker"] == 2
    assert out["requeue_yield"] == pytest.approx(2 / 3)
    assert out["per_node"]["n1"] == {"attempts": 2, "reached_worker": 1}


# ─────────────────────────── requeue gate ───────────────────────────────
def test_infra_class_is_replayable_with_capped_exponential_backoff():
    policy = ta.RequeuePolicy(base_backoff_seconds=100, backoff_factor=3.0,
                              max_backoff_seconds=500, max_attempts=5)
    assert policy.backoff_for(1) == 0          # first attempt is not a replay
    assert policy.backoff_for(2) == 100        # first replay = base
    assert policy.backoff_for(3) == 300        # geometric
    assert policy.backoff_for(5) == 500        # capped
    dec = ta.decide_requeue(rec(task_key="node-x-a1", result={"tool_calls": 0}),
                            ta.attribute(rec(result={"tool_calls": 0})), policy=policy)
    assert dec.eligible is True and dec.reason == "infra_class_replayable"
    assert dec.attempt_no == 2 and dec.backoff_seconds == 100
    assert dec.snapshot_required is True


def test_physics_and_policy_and_budget_are_never_replayed():
    for klass, reason in (
        (ta.CLASS_PHYSICS_HONEST_FAIL, "physics_negative_result_track"),
        (ta.CLASS_POLICY_BLOCKED, "policy_answer_is_stable"),
        (ta.CLASS_BUDGET_EXHAUSTED, "needs_new_grant_not_new_attempt"),
    ):
        attr = {"class": klass, "mission_id": 1}
        dec = ta.decide_requeue(rec(task_key="node-y-a1"), attr)
        assert dec.eligible is False
        assert dec.reason == reason
        assert dec.backoff_seconds == 0


def test_capacity_guard_defers_replay_when_the_queue_is_saturated():
    policy = ta.RequeuePolicy(max_queue_depth=200)
    attr = {"class": ta.CLASS_INFRA_NO_TOOLCALL, "mission_id": 1}
    busy = ta.decide_requeue(rec(task_key="node-q-a1"), attr, policy=policy, queue_depth=566)
    calm = ta.decide_requeue(rec(task_key="node-q-a1"), attr, policy=policy, queue_depth=5)
    assert busy.eligible is False and busy.reason == "capacity_deferred"
    assert calm.eligible is True


def test_attempt_cap_stops_the_third_attempt():
    policy = ta.RequeuePolicy(max_attempts=2)
    attr = {"class": ta.CLASS_INFRA_NO_TOOLCALL, "mission_id": 1}
    first = ta.decide_requeue(rec(task_key="node-z-a1"), attr, prior_attempts=0, policy=policy)
    second = ta.decide_requeue(rec(task_key="node-z-a2"), attr, prior_attempts=1, policy=policy)
    assert first.eligible is True
    assert second.eligible is False and second.reason == "attempt_cap_reached"


def test_plan_fraction_cap_limits_replays_per_round():
    policy = ta.RequeuePolicy(plan_fraction_cap=0.2, max_attempts=5)
    rows = [rec(mid=i, task_key=f"n{i}-a1", result={"tool_calls": 0}) for i in range(10)]
    plan = ta.plan_replays(rows, policy=policy)
    assert plan["plan_nodes"] == 10
    assert plan["plan_replays_used"] == 2          # int(round(0.2 * 10))
    assert sum(1 for e in plan["entries"] if e["eligible"]) == 2
    assert {e["reason"] for e in plan["entries"] if not e["eligible"]} == \
        {"plan_fraction_cap_reached"}


# ─────────────────────── budget ledger: 1 per node ──────────────────────
def test_ledger_settles_one_unit_per_node_regardless_of_replays():
    attempts = [
        {"node_key": "n1", "attempt": 1, "class": ta.CLASS_INFRA_NO_TOOLCALL},
        {"node_key": "n1", "attempt": 2, "class": ta.CLASS_INFRA_LOST},
        {"node_key": "n1", "attempt": 3, "class": ta.CLASS_PHYSICS_HONEST_FAIL},
        {"node_key": "n2", "attempt": 1, "class": ta.CLASS_DELIVERED_OK},
    ]
    ledger = ta.settle_attempts(attempts)
    assert ledger["nodes"] == 2
    assert ledger["budget_used_total"] == 2.0 == ledger["nodes"] * 1.0
    assert ledger["single_settlement_holds"] is True
    replays = [r for r in ledger["rows"] if not r["billable"]]
    assert len(replays) == 2
    assert all(r["requeue_class"] for r in replays)          # every replay is stamped
    assert [r["budget_used"] for r in replays] == [0.0, 0.0]  # never accumulated


# ─────────────────────── robustness of the input layer ───────────────────
def test_result_given_as_json_string_is_parsed():
    out = ta.attribute({"mission_id": 7, "status": "failed",
                        "result": json.dumps({"tool_calls": 0, "fail_stage": "llm_error"})})
    assert out["class"] == ta.CLASS_MODEL_ERROR


def test_numeric_string_tool_calls_is_not_trusted_as_a_counter():
    """A string route to the counter must not silently become an int (provenance matters)."""
    ev = ta.extract(rec(result={"tool_calls": "41", "changed_files": ["a.md"]}))
    assert ev.tool_calls == 1 and ev.tool_calls_source == "changed_files_lower_bound"


def test_missing_status_and_empty_result_is_unknown_not_a_crash():
    out = ta.attribute({"mission_id": 8, "session": {"events": 2}})
    assert out["class"] == ta.CLASS_UNKNOWN
    assert out["tool_calls"] is None


def test_snapshot_round_trip_through_gzip_is_lossless(tmp_path):
    rows = [rec(mid=1, result={"tool_calls": 0}),
            rec(mid=2, status="succeeded", result={"tool_calls": 3, "accepted": True})]
    path = str(tmp_path / "snap.jsonl.gz")
    info = ta.write_snapshot(rows, path)
    back = ta.load_snapshot(path)
    assert back == rows
    assert info["sha256"] == ta.sha256_file(path)
    assert ta.sha256_of(ta.attribute_all(back)) == ta.sha256_of(ta.attribute_all(rows))


# ───────────────────── snapshot / idempotency / neutrality ──────────────
def test_snapshot_preserves_previous_attempt_with_sha256(tmp_path):
    src = tmp_path / "prev"
    src.mkdir()
    (src / "a.txt").write_text("attempt-1 artifacts", encoding="utf-8")
    dest = tmp_path / "snap"
    manifest = ta.snapshot_artifacts([str(src)], str(dest), "node/1", 1)
    assert manifest["entries"][0]["sha256"] == ta.sha256_file(str(src / "a.txt"))
    # a second attempt must not overwrite the first snapshot
    (src / "a.txt").write_text("attempt-2 artifacts", encoding="utf-8")
    ta.snapshot_artifacts([str(src)], str(dest), "node/1", 2)
    first = json.loads((dest / "node-1" / "attempt-01" / "manifest.json").read_text())
    second = json.loads((dest / "node-1" / "attempt-02" / "manifest.json").read_text())
    assert first["entries"][0]["sha256"] != second["entries"][0]["sha256"]
    assert (dest / "node-1" / "attempt-01" / "prev" / "a.txt").read_text() == "attempt-1 artifacts"


def test_idempotency_is_bitwise_identical_and_order_invariant():
    rows = [
        rec(mid=1, result={"tool_calls": 0}),
        rec(mid=2, result={"tool_calls": 4, "accepted": False}),
        rec(mid=3, status="stopped", result={}),
    ]
    out = ta.idempotency_check(rows)
    assert out["identical"] is True and out["order_invariant"] is True
    assert out["sha256_pass1"] == ta.sha256_of(ta.attribute_all(rows))


def test_domain_scan_has_positive_and_negative_control(tmp_path):
    hit = tmp_path / "with_word.txt"
    hit.write_text("a file that mentions zorblax twice: zorblax", encoding="utf-8")
    clean = tmp_path / "clean.txt"
    clean.write_text("generic mechanics only", encoding="utf-8")
    out = ta.scan_domain_words([str(hit), str(clean)], ["zorblax"])
    assert out["hits"] == 2
    assert out["files"][0]["words"] == {"zorblax": 2}
    assert out["files"][1]["hits"] == 0


def test_mechanics_module_carries_no_domain_vocabulary():
    """Guard: the classifier must stay domain-free (lexicon injected from an artifact)."""
    lexicon_path = os.path.join(ARTIFACTS, "domain_lexicon.json")
    if not os.path.exists(lexicon_path):
        pytest.skip("domain lexicon artifact not present in this checkout")
    with open(lexicon_path, encoding="utf-8") as fh:
        lexicon = json.load(fh)
    targets = [
        os.path.join(ROOT, "tools", "validation", "terminal_attribution.py"),
        os.path.join(ROOT, "tools", "validation", "terminal_attribution_acceptance.py"),
        os.path.abspath(__file__),
    ]
    out = ta.scan_domain_words([p for p in targets if os.path.exists(p)], lexicon)
    assert out["hits"] == 0, out


def test_replay_pairs_group_attempts_by_node_prefix():
    rows = [
        rec(mid=1, task_key="node-a-a1", result={"tool_calls": 0}),
        rec(mid=2, task_key="node-a-a2", result={"tool_calls": 5}),
        rec(mid=3, task_key="node-b-a1", result={"tool_calls": 0}),
    ]
    pairs = ta.replay_pairs(rows)
    assert [p["target"]["mission_id"] for p in pairs] == [2]
    assert pairs[0]["origin"]["mission_id"] == 1
    assert pairs[0]["node_key"] == "node-a"
    out = ta.requeue_yield([p["target"] for p in pairs])
    assert out["reached_worker"] == 1