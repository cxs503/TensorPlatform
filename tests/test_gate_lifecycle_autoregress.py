"""C4 tests: gap analysis -> proposal generation -> pool injection -> seeding.

The chain must be automatic and idempotent: a second pass over the same wave
generates nothing new, and the seeded plan must always be a valid DAG even when
proposals carry dangling ``after`` hints.
"""

from __future__ import annotations

import json


from tools.gate_lifecycle.autoregress import (
    ProposalSink,
    close_wave,
    gap_analysis,
    inject,
    proposals_from_gaps,
    rewrite_pool,
    seed_next_wave,
)
from tools.gate_lifecycle.model import Plan
from tools.gate_lifecycle.verdict import evaluate_node


def crit(metric="err", op="le", threshold=0.05):
    return {"name": metric, "metric": metric, "op": op, "threshold": threshold}


def build_wave():
    plan = Plan.from_dict({"plan_id": "wave-x", "nodes": [
        {"id": "n1", "deps": [], "capability": "cap-1", "criteria": [crit()],
         "meta": {"measurements": {"err": 0.01}, "baseline_delta": -0.1}},
        {"id": "n2", "deps": ["n1"], "capability": "cap-2", "criteria": [crit()],
         "meta": {"measurements": {"err": 0.9}}},
        {"id": "n3", "deps": ["n2"], "capability": "cap-3", "criteria": [crit()],
         "meta": {"measurements": {"err": 0.01}}},
        {"id": "n4", "deps": [], "capability": "cap-4", "criteria": [crit("drift")],
         "meta": {"measurements": {}}},
        {"id": "n5", "deps": [], "capability": "cap-5", "criteria": [crit()],
         "meta": {"measurements": {"err": 0.9},
                  "declaration": {"honest_fail": True}}},
    ]})
    results = []
    for n in plan.nodes:
        if n.node_id == "n3":  # blocked: dependency n2 did not pass
            from tools.gate_lifecycle.model import NodeResult

            results.append(NodeResult(node_id="n3", status="blocked", verdict="WITHHELD",
                                      criteria=(), capability="cap-3"))
        else:
            results.append(evaluate_node(n, n.meta.get("measurements", {}),
                                        n.meta.get("declaration", {})))
    ledger = [{"node_id": "n1", "capability": "cap-1", "verdict": "PASS"}]
    return plan, results, ledger


def test_gap_analysis_classifies_every_outcome():
    plan, results, ledger = build_wave()
    gaps = gap_analysis(plan, results, ledger)
    kinds = gaps["counts"]
    assert kinds["criterion_violation"] == 1        # n2
    assert kinds["blocked_dependency"] == 1         # n3
    assert kinds["missing_evidence"] == 1           # n4
    assert kinds["negative_result"] == 1            # n5 (honest FAIL)
    assert kinds.get("unregistered_capability", 0) == 0   # n1 is registered
    # every gap is traceable back to a node and carries a fingerprint
    assert all(g["node_id"] and g["fingerprint"] for g in gaps["gaps"])
    assert gaps["failure_accounting"]["honest_fail"] == 1


def test_unregistered_pass_is_a_high_severity_gap():
    plan, results, _ = build_wave()
    gaps = gap_analysis(plan, results, ledger_records=[])
    high = [g for g in gaps["gaps"] if g["kind"] == "unregistered_capability"]
    assert len(high) == 1 and high[0]["node_id"] == "n1" and high[0]["severity"] == "high"


def test_proposals_dedupe_against_pool_and_known_fingerprints():
    plan, results, ledger = build_wave()
    gaps = gap_analysis(plan, results, ledger)
    first = proposals_from_gaps(gaps)
    assert first["generated"] == len(gaps["gaps"])
    assert first["deduped"] == 0
    # feeding the same pool back must generate nothing new
    second = proposals_from_gaps(gaps, existing_pool=first["proposals"])
    assert second["generated"] == 0
    assert second["deduped"] == len(gaps["gaps"])
    # repo-level fingerprints work too
    fp = gaps["gaps"][0]["fingerprint"]
    third = proposals_from_gaps(gaps, known_fingerprints=[fp])
    assert third["deduped"] >= 1


def test_severity_ordering_and_proposal_ids_are_deterministic():
    plan, results, ledger = build_wave()
    gaps = gap_analysis(plan, results, ledger)
    a = proposals_from_gaps(gaps)
    b = proposals_from_gaps(gap_analysis(plan, results, ledger))
    assert [p["proposal_id"] for p in a["proposals"]] == [p["proposal_id"] for p in b["proposals"]]
    sev = [p["severity"] for p in a["proposals"]]
    order = {"high": 0, "medium": 1, "low": 2}
    assert [order[s] for s in sev] == sorted(order[s] for s in sev)


def test_injection_appends_and_pool_is_canonical(tmp_path):
    plan, results, ledger = build_wave()
    gaps = gap_analysis(plan, results, ledger)
    prop = proposals_from_gaps(gaps)
    sink = ProposalSink(tmp_path / "pool.jsonl")
    rep = inject(prop, sink)
    assert rep["written"] == prop["generated"]
    assert rep["pool_size"] == prop["generated"]
    lines = (tmp_path / "pool.jsonl").read_text(encoding="utf-8").strip().splitlines()
    for line in lines:
        assert line == json.dumps(json.loads(line), sort_keys=True, separators=(",", ":"),
                                  ensure_ascii=False)
    # second injection is a no-op on pool content
    rep2 = inject(proposals_from_gaps(gaps, sink.read()), sink)
    assert rep2["written"] == 0 and rep2["pool_size"] == prop["generated"]


def test_seeded_plan_is_a_valid_dag_and_maps_back_to_proposals():
    plan, results, ledger = build_wave()
    gaps = gap_analysis(plan, results, ledger)
    prop = proposals_from_gaps(gaps)
    seeded = seed_next_wave(prop["proposals"] + [
        {"proposal_id": "prop-dangling", "severity": "low", "capability": "cap-9",
         "text": "t", "after": ["cap-does-not-exist"]},
    ], plan_id="wave-y", max_nodes=10)
    rebuilt = Plan.from_dict(seeded)  # raises on cycle or unknown dependency
    assert len(rebuilt.nodes) == len(prop["proposals"]) + 1
    assert set(seeded["meta"]["seeded_from"]) >= {p["proposal_id"] for p in prop["proposals"]}
    assert all("proposal_id" in n["meta"] for n in seeded["nodes"])


def test_seeded_plan_wires_after_hints():
    pool = [
        {"proposal_id": "p1", "severity": "high", "capability": "cap-a", "text": "a", "after": []},
        {"proposal_id": "p2", "severity": "high", "capability": "cap-b", "text": "b",
         "after": ["cap-a"]},
    ]
    seeded = seed_next_wave(pool, plan_id="wave-z")
    by_cap = {n["capability"]: n for n in seeded["nodes"]}
    assert by_cap["cap-b"]["deps"] == [by_cap["cap-a"]["id"]]
    Plan.from_dict(seeded)


def test_max_nodes_caps_the_seed_size():
    pool = [{"proposal_id": f"p{i}", "severity": "medium", "capability": f"cap-{i}",
             "text": f"t{i}", "after": []} for i in range(20)]
    seeded = seed_next_wave(pool, plan_id="w", max_nodes=10)
    assert len(seeded["nodes"]) == 10


def test_adopted_proposals_are_not_reseeded(tmp_path):
    plan, results, ledger = build_wave()
    gaps = gap_analysis(plan, results, ledger)
    prop = proposals_from_gaps(gaps)
    path = tmp_path / "pool.jsonl"
    sink = ProposalSink(path)
    rep = inject(prop, sink)
    rewrite_pool(path, close_wave(sink.read(), rep["proposal_ids"]))
    assert seed_next_wave(ProposalSink(path).read(), plan_id="w2")["nodes"] == []
    assert all(r["status"] == "adopted" for r in ProposalSink(path).read())