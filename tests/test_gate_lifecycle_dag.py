"""C1 + dispatch-layer regression tests for tools/gate_lifecycle.

Invariants under test:
  * acyclicity: correct on acyclic plans, and a concrete cycle is reported for
    cyclic ones (both a self-loop and a 3-cycle), never a silent success;
  * critical path equals the independent brute-force maximum over all root->leaf
    paths (random DAGs, fixed seeds);
  * 10-node DAG: detect + topological order + critical path well under 1 s;
  * the schedule respects dependencies and the lane capacity, its makespan is
    never shorter than the critical path, and it is deterministic.
"""

from __future__ import annotations

import random
import time

import pytest

from tools.gate_lifecycle.dag import (
    critical_path,
    find_cycle,
    schedule,
    serial_makespan,
    slacks,
    topological_order,
    validate_schedule,
    waves,
)
from tools.gate_lifecycle.model import CycleError, Plan, PlanError


def plan_of(nodes, plan_id="p"):
    return Plan.from_dict({"plan_id": plan_id, "nodes": nodes})


def brute_force(nodes) -> float:
    """Longest weighted root->leaf path, enumerated exhaustively."""
    ids = {n["id"]: n for n in nodes}
    best = 0.0

    def walk(v, acc):
        nonlocal best
        acc += float(ids[v].get("duration", 1.0))
        succ = [m["id"] for m in nodes if v in m.get("deps", ())]
        if not succ:
            best = max(best, acc)
        for w in succ:
            walk(w, acc)

    roots = [n["id"] for n in nodes if not n.get("deps")]
    for r in roots:
        walk(r, 0.0)
    return best


def test_acyclic_plan_has_no_cycle():
    plan = plan_of([
        {"id": "a", "duration": 1},
        {"id": "b", "deps": ["a"], "duration": 1},
        {"id": "c", "deps": ["a", "b"], "duration": 1},
    ])
    assert find_cycle(plan) is None
    assert topological_order(plan) == ["a", "b", "c"]


def test_self_dependency_is_rejected_at_parse_time():
    raw = {"plan_id": "p", "nodes": [{"id": "a", "deps": ["a"]}]}
    with pytest.raises(CycleError):
        Plan.from_dict(raw)


def test_three_cycle_is_detected_with_concrete_path():
    plan = plan_of([
        {"id": "c1", "deps": ["c3"]},
        {"id": "c2", "deps": ["c1"]},
        {"id": "c3", "deps": ["c2"]},
    ])
    cyc = find_cycle(plan)
    assert cyc is not None
    assert cyc[0] == cyc[-1] and len(cyc) == 4  # closed walk of the 3-cycle
    with pytest.raises(CycleError):
        topological_order(plan)


def test_unknown_dependency_and_negative_duration_are_rejected():
    with pytest.raises(PlanError):
        Plan.from_dict({"nodes": [{"id": "a", "deps": ["zzz"]}]})
    with pytest.raises(PlanError):
        Plan.from_dict({"nodes": [{"id": "a", "duration": -1}]})
    with pytest.raises(PlanError):
        Plan.from_dict({"nodes": [{"id": "a"}, {"id": "a"}]})


@pytest.mark.parametrize("seed", [1, 2, 3, 4, 5])
def test_critical_path_matches_brute_force_on_random_dags(seed):
    rng = random.Random(seed)
    nodes = []
    for i in range(12):
        deps = [f"n{j}" for j in range(i) if rng.random() < 0.28]
        nodes.append({"id": f"n{i}", "deps": deps, "duration": round(rng.uniform(1, 60), 3)})
    plan = plan_of(nodes)
    path, length = critical_path(plan)
    assert length == pytest.approx(brute_force(nodes), rel=0, abs=1e-9)
    # the returned path is a genuine chain and its weights sum to the length
    by_id = plan.by_id()
    for prev, nxt in zip(path, path[1:]):
        assert prev in by_id[nxt].deps
    assert sum(by_id[v].duration for v in path) == pytest.approx(length, abs=1e-9)


def test_ten_node_dag_under_one_second():
    nodes = [{"id": f"n{i:02d}", "duration": 10 + i} for i in range(10)]
    for i in range(1, 10):
        nodes[i]["deps"] = [f"n{j:02d}" for j in range(max(0, i - 3), i)]
    plan = plan_of(nodes)
    t0 = time.perf_counter()
    for _ in range(50):
        assert find_cycle(plan) is None
        topological_order(plan)
        critical_path(plan)
        schedule(plan, capacity=10)
    per_call = (time.perf_counter() - t0) / 50
    assert per_call < 1.0


def test_waves_and_slacks_are_consistent():
    plan = plan_of([
        {"id": "a", "duration": 5},
        {"id": "b", "deps": ["a"], "duration": 3},
        {"id": "c", "duration": 4},
        {"id": "d", "deps": ["b", "c"], "duration": 2},
    ])
    assert waves(plan) == [["a", "c"], ["b"], ["d"]]
    sl = slacks(plan)
    path, length = critical_path(plan)
    assert all(sl[v] == 0 for v in path)
    assert length == 10.0  # a(5)+b(3)+d(2)
    assert sl["c"] == 4.0  # c must finish by 8, so it may start 4 s late


def test_schedule_respects_dependencies_and_capacity():
    nodes = [
        {"id": "n01", "duration": 120},
        {"id": "n02", "duration": 90},
        {"id": "n03", "duration": 60},
        {"id": "n04", "deps": ["n01"], "duration": 150},
        {"id": "n05", "deps": ["n02"], "duration": 100},
        {"id": "n06", "deps": ["n04"], "duration": 80},
        {"id": "n07", "duration": 70},
        {"id": "n08", "deps": ["n03", "n05"], "duration": 110},
        {"id": "n09", "duration": 40},
        {"id": "n10", "deps": ["n08"], "duration": 95},
    ]
    plan = plan_of(nodes)
    sched = schedule(plan, capacity=10)
    assert validate_schedule(plan, sched) == []
    assert sched.max_parallelism() <= 10
    assert sched.makespan >= sched.critical_length - 1e-9
    # a narrow dispatcher must not beat the wide one, and must stay serial-ish
    narrow = schedule(plan, capacity=1)
    assert validate_schedule(plan, narrow) == []
    assert narrow.makespan >= sched.makespan - 1e-9
    assert narrow.max_parallelism() == 1
    assert narrow.makespan == pytest.approx(sum(n["duration"] for n in nodes), abs=1e-9)


def test_capacity_below_ten_still_schedules_but_must_be_declared():
    plan = plan_of([{"id": f"n{i}", "duration": 1} for i in range(12)])
    wide = schedule(plan, capacity=16)
    assert wide.max_parallelism() == 12
    assert wide.makespan == pytest.approx(1.0, abs=1e-9)
    with pytest.raises(PlanError):
        schedule(plan, capacity=0)


def test_schedule_is_deterministic():
    nodes = [{"id": f"n{i:02d}", "duration": 10 + (i * 7) % 11} for i in range(10)]
    for i in range(1, 10):
        nodes[i]["deps"] = [f"n{j:02d}" for j in range(i) if (i + j) % 3 == 0]
    plan = plan_of(nodes)
    a = schedule(plan, capacity=4)
    b = schedule(plan, capacity=4)
    assert a.to_dict() == b.to_dict()
    assert validate_schedule(plan, a) == []


def test_serial_makespan_charges_turnaround_once_per_node():
    plan = plan_of([{"id": "a", "duration": 10}, {"id": "b", "deps": ["a"], "duration": 5}])
    assert serial_makespan(plan) == pytest.approx(15.0)
    assert serial_makespan(plan, turnaround=2.0) == pytest.approx(19.0)


def test_zero_duration_nodes_do_not_break_the_scheduler():
    plan = plan_of([
        {"id": "a", "duration": 0},
        {"id": "b", "deps": ["a"], "duration": 0},
        {"id": "c", "deps": ["b"], "duration": 5},
    ])
    sched = schedule(plan, capacity=2)
    assert validate_schedule(plan, sched) == []
    assert sched.makespan == pytest.approx(5.0)