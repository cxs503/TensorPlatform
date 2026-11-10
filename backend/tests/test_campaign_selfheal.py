"""Engine-level acceptance tests for campaign DAG self-healing (P162W1-N08).

Criteria covered (all synthetic, domain-neutral payloads):

* C1 automatic retry — recovery within 3 retries >= 80 % under fault injection
* C2 bypass routing   — bypassed upstream still lets dependants run, no cascade
* C3 degraded execution — degraded vs normal deviation < 15 % (configurable)
* C4 negative control — healthy campaign emits zero healing events
* C5 resource awareness — saturated (>80 %) scheduling latency stays < 30 s
* C6 audit chain — every healing action has time/node/action/result + hash link
* C7 acyclic — healing never changes edges; cyclic graphs are rejected
"""
from __future__ import annotations

import random

import pytest

from services.selfheal import (
    AuditChain,
    CycleError,
    LoadSample,
    NodeStatus,
    RetryPolicy,
    SelfHealEngine,
    SelfHealingDag,
    SelfHealPolicy,
)
from services.selfheal.dag import UnknownDependency
from services.selfheal.engine import relative_deviation


# ───────────────────────── synthetic workload ─────────────────────────

def synthetic_series(iterations: int) -> float:
    """Domain-neutral iterative estimator (partial alternating series)."""
    return 4.0 * sum(((-1) ** k) / (2 * k + 1) for k in range(iterations))


def build_chain(n: int = 3, degradable: bool = False) -> SelfHealingDag:
    dag = SelfHealingDag("synthetic")
    dag.add_node(1, "a", critical=True, degradable=degradable)
    for i in range(2, n + 1):
        dag.add_node(i, f"n{i}", deps=[i - 1], degradable=degradable)
    return dag


# ───────────────────────── C1: automatic retry ─────────────────────────

def test_c1_retry_recovery_rate_under_fault_injection():
    """Fault-injected nodes recover within 3 retries in >= 80 % of cases."""
    rng = random.Random(20261110)
    trials = 200
    fail_prob = 0.40  # each attempt independently fails with p=0.4
    recovered = 0

    for t in range(trials):
        dag = SelfHealingDag(f"fault-{t}")
        dag.add_node(1, "task")
        engine = SelfHealEngine(dag, policy=SelfHealPolicy(
            retry=RetryPolicy(max_retries=3, base_delay_s=2.0, factor=2.0)
        ))
        now = 1000.0
        action = None
        for attempt in range(4):  # 1 try + 3 retries
            if rng.random() >= fail_prob:
                engine.report_success(1, result=synthetic_series(200), now=now)
                break
            action = engine.report_failure(1, "injected", now=now)
            now = action.get("next_attempt_at", now + 4.0)
        node = dag.nodes[1]
        if node.status is NodeStatus.DONE:
            recovered += 1
        else:
            # retries exhausted (4 failures) -> bypassed, not lost silently
            assert action is not None and action["action"] == "bypassed"

    rate = recovered / trials
    # The theoretical rate is 1 - 0.4**4 = 97.4 %; threshold is 80 %.
    assert rate >= 0.80, f"recovery rate {rate:.3f} below 80 %"


def test_c1_backoff_is_exponential_and_capped():
    policy = RetryPolicy(max_retries=3, base_delay_s=2.0, factor=2.0, max_delay_s=10.0)
    delays = [policy.delay_for(n) for n in (1, 2, 3, 4)]
    assert delays[:3] == [4.0, 8.0, 10.0]  # base * 2**n, capped at max_delay_s
    assert policy.delay_for(10) == 10.0

    dag = SelfHealingDag("backoff")
    dag.add_node(1, "a")
    engine = SelfHealEngine(dag, policy=SelfHealPolicy(retry=policy))
    a1 = engine.report_failure(1, now=0.0)
    a2 = engine.report_failure(1, now=1.0)
    a3 = engine.report_failure(1, now=2.0)
    a4 = engine.report_failure(1, now=3.0)
    assert [a1["delay_s"], a2["delay_s"], a3["delay_s"]] == [4.0, 8.0, 10.0]
    assert a4["action"] == "bypassed"
    assert dag.nodes[1].status is NodeStatus.SKIPPED
    assert dag.nodes[1].attempts == 4


# ───────────────────────── C2: bypass routing ─────────────────────────

def test_c2_bypass_keeps_downstream_running():
    dag = build_chain(3)
    engine = SelfHealEngine(dag)
    for i in range(4):  # exhaust retries on the head node
        engine.report_failure(1, "permanent", now=100.0 + i)
    assert dag.nodes[1].status is NodeStatus.SKIPPED

    engine.report_success(2, result=1.0, now=200.0)
    engine.report_success(3, result=2.0, now=201.0)
    assert dag.nodes[2].status is NodeStatus.DONE
    assert dag.nodes[3].status is NodeStatus.DONE

    statuses = {n.status for n in dag.nodes.values()}
    assert NodeStatus.FAILED not in statuses
    assert NodeStatus.BLOCKED not in statuses
    actions = {ev.action for ev in engine.audit.heal_events()}
    assert "bypassed" in actions


def test_c2_without_bypass_downstream_is_blocked():
    dag = build_chain(2)
    engine = SelfHealEngine(dag, policy=SelfHealPolicy(bypass_on_exhaust=False))
    for i in range(4):
        engine.report_failure(1, "permanent", now=100.0 + i)
    assert dag.nodes[1].status is NodeStatus.FAILED

    blocked = engine.resolve_blocked(now=300.0)
    assert [b["node_id"] for b in blocked] == [2]
    assert dag.nodes[2].status is NodeStatus.BLOCKED
    assert engine.dag.ready_nodes() == []


# ───────────────────────── C3: degraded execution ─────────────────────────

def test_c3_degraded_result_within_tolerance_is_accepted():
    dag = SelfHealingDag("degrade")
    dag.add_node(1, "solver", degradable=True)
    engine = SelfHealEngine(dag)
    baseline = synthetic_series(200)
    engine.set_baseline(1, baseline)
    engine.bind_degraded_executor(1, lambda node: synthetic_series(40))

    action = None
    for i in range(4):
        action = engine.report_failure(1, "timeout", now=100.0 + i)
    assert action["action"] == "degraded"
    node = dag.nodes[1]
    assert node.status is NodeStatus.DEGRADED
    assert node.quality == "degraded"
    dev = relative_deviation(node.result, baseline)
    assert dev < 0.15, f"deviation {dev:.4f} exceeds 15 %"
    assert action["deviation"] == pytest.approx(dev)


def test_c3_degraded_result_beyond_tolerance_is_rejected():
    dag = SelfHealingDag("degrade-reject")
    dag.add_node(1, "solver", degradable=True)
    engine = SelfHealEngine(dag)
    baseline = synthetic_series(400)  # ~3.1365
    engine.set_baseline(1, baseline)

    # 2 terms -> 4*(1 - 1/3) = 2.6667 -> ~15 % deviation; use 1 term (4.0) ~27 %
    action = None
    for i in range(4):
        action = engine.report_failure(
            1, "timeout", now=100.0 + i, degraded_result=synthetic_series(1)
        )
    assert action["action"] == "bypassed", action
    assert dag.nodes[1].status is NodeStatus.SKIPPED
    assert "degrade_rejected" in {ev.action for ev in engine.audit.heal_events()}


def test_c3_tolerance_is_configurable():
    policy = SelfHealPolicy()
    policy = SelfHealPolicy(
        retry=policy.retry,
        resources=policy.resources,
        degradation=type(policy.degradation)(enabled=True, max_deviation=0.001),
    )
    dag = SelfHealingDag("strict")
    dag.add_node(1, "solver", degradable=True)
    engine = SelfHealEngine(dag, policy=policy)
    engine.set_baseline(1, synthetic_series(200))
    for i in range(4):
        action = engine.report_failure(
            1, "timeout", now=100.0 + i, degraded_result=synthetic_series(40)
        )
    assert action["action"] == "bypassed"


def test_c3_no_fallback_available_falls_back_to_bypass():
    dag = SelfHealingDag("no-fallback")
    dag.add_node(1, "solver", degradable=True)
    engine = SelfHealEngine(dag)
    for i in range(4):
        action = engine.report_failure(1, "timeout", now=100.0 + i)
    assert action["action"] == "bypassed"


# ───────────────────────── C4: negative control ─────────────────────────

def test_c4_healthy_campaign_triggers_no_healing():
    dag = build_chain(4)
    engine = SelfHealEngine(dag)
    now = 500.0
    for nid in (1, 2, 3, 4):
        sched = engine.schedule(now=now, load=LoadSample(cpu_pct=25, mem_pct=40))
        assert [d["node_id"] for d in sched["dispatched"]] == [nid]
        engine.report_success(nid, result=float(nid), now=now)
        now += 1.0
    assert engine.heal_audit() == []
    assert engine.summary()["heal_events"] == 0
    assert engine.audit.verify() is True
    # full audit trail still records the normal progress, for lineage
    assert len(engine.audit) == 8  # 4 dispatches + 4 successes


# ───────────────────────── C5: resource awareness ─────────────────────────

def test_c5_saturation_defers_non_critical_but_stays_under_30s():
    dag = SelfHealingDag("elastic")
    dag.add_node(1, "critical", critical=True)
    dag.add_node(2, "background")
    engine = SelfHealEngine(dag)
    t0 = 1000.0
    load = LoadSample(cpu_pct=90, mem_pct=60, queue_len=60, queue_capacity=64)

    sched = engine.schedule(now=t0, load=load)
    assert sched["saturated"] is True
    assert [d["node_id"] for d in sched["dispatched"]] == [1]
    assert sched["dispatched"][0]["delay_s"] == 0.0
    deferred = {d["node_id"]: d for d in sched["deferred"]}
    assert deferred[2]["delay_s"] < 30.0
    assert deferred[2]["defer_until"] == pytest.approx(t0 + deferred[2]["delay_s"])
    assert sched["max_scheduling_latency_s"] < 30.0
    actions = [ev.action for ev in engine.audit.heal_events()]
    assert "saturation_detected" in actions and "schedule_deferred" in actions

    # Still saturated before the deadline: the node stays deferred.
    mid = engine.schedule(now=t0 + deferred[2]["delay_s"] - 1.0, load=load)
    assert [d["node_id"] for d in mid["dispatched"]] == []

    # Load drops: the deferred node runs, latency < 30 s end-to-end.
    t_dispatch = t0 + deferred[2]["delay_s"]
    sched2 = engine.schedule(now=t_dispatch, load=LoadSample(cpu_pct=30, mem_pct=30))
    assert deferred[2]["node_id"] in [d["node_id"] for d in sched2["dispatched"]]
    latency = t_dispatch - t0
    assert latency < 30.0, latency
    assert dag.nodes[2].status is NodeStatus.IN_FLIGHT


def test_c5_defer_delay_always_below_cap_even_at_full_load():
    engine = SelfHealEngine(SelfHealingDag("full"))
    for ratio_load in (float(load) for load in (81, 90, 99, 100)):
        delay = engine._defer_delay(ratio_load / 100.0)
        assert 0.0 < delay < engine.policy.resources.max_defer_s
    assert engine._defer_delay(0.80) == 0.0  # at the threshold: not deferred


# ───────────────────────── C6: audit chain ─────────────────────────

def test_c6_every_heal_event_is_complete_and_chain_verifies():
    dag = build_chain(3, degradable=True)
    engine = SelfHealEngine(dag)
    engine.set_baseline(1, synthetic_series(200))
    engine.report_failure(1, "timeout", now=10.0)
    engine.report_failure(1, "timeout", now=20.0)
    engine.report_success(1, synthetic_series(200), now=30.0)  # recovered
    for i in range(4):
        engine.report_failure(2, "permanent", now=40.0 + i,
                              degraded_result=synthetic_series(40))
    engine.report_success(3, 1.0, now=60.0)
    engine.schedule(now=70.0, load=LoadSample(cpu_pct=95))

    heal = engine.audit.heal_events()
    assert heal, "expected healing events"
    for ev in heal:
        assert ev.ts > 0
        assert ev.action
        assert ev.outcome
        assert ev.node_id is not None or ev.action in {
            "saturation_detected", "cycle_rejected", "policy_updated"
        }
        assert ev.hash and len(ev.hash) == 64
    assert engine.audit.verify() is True

    # Rebuilt chain from serialized dicts verifies identically.
    rebuilt = AuditChain(engine.audit.to_dicts())
    assert rebuilt.verify() is True

    # Tampering is detected: flip one detail field.
    payload = engine.audit.to_dicts()
    victim = next(i for i, d in enumerate(payload) if d["action"] == "degraded")
    payload[victim]["detail"]["deviation"] = 0.0
    assert AuditChain(payload).verify() is False

    # Removing an event breaks the sequence/link too.
    assert AuditChain(payload[:2] + payload[3:]).verify() is False


# ───────────────────────── C7: acyclic invariant ─────────────────────────

def test_c7_cycles_are_rejected():
    dag = SelfHealingDag("cyclic")
    dag.add_node(1, "a", deps=[3])
    dag.add_node(2, "b", deps=[1])
    dag.add_node(3, "c", deps=[2])
    with pytest.raises(CycleError) as exc:
        SelfHealEngine(dag)
    assert exc.value.cycle[0] == exc.value.cycle[-1]

    loop = SelfHealingDag("self-loop")
    loop.add_node(1, "a", deps=[1])
    with pytest.raises(CycleError):
        loop.validate()


def test_c7_healing_never_mutates_edges():
    dag = build_chain(3)
    edges_before = set(dag.edges)
    engine = SelfHealEngine(dag)
    for i in range(4):
        engine.report_failure(1, "permanent", now=100.0 + i)
    engine.schedule(now=200.0, load=LoadSample(cpu_pct=99))
    engine.report_success(2, 1.0, now=201.0)
    engine.resolve_blocked(now=202.0)

    assert dag.edges == edges_before
    assert dag.validate() == [1, 2, 3]  # still acyclic, stable order
    assert [ev.action for ev in engine.audit.heal_events()].count("bypassed") == 1


def test_unknown_dependency_is_rejected():
    dag = SelfHealingDag("dangling")
    dag.add_node(1, "a", deps=[99])
    with pytest.raises(UnknownDependency):
        dag.validate()


# ───────────────────────── misc invariants ─────────────────────────

def test_critical_path_and_priority():
    dag = SelfHealingDag("paths")
    dag.add_node(1, "root")
    dag.add_node(2, "left", deps=[1])
    dag.add_node(3, "deep", deps=[2])
    dag.add_node(4, "right", deps=[1])
    assert dag.critical_path() == [1, 2, 3]
    assert dag.priority_nodes() == {1, 2, 3}

    engine = SelfHealEngine(dag)
    engine.schedule(now=0.0, load=LoadSample(cpu_pct=90))
    assert engine.summary()["critical_path"] == [1, 2, 3]


def test_policy_validation_rejects_bad_values():
    with pytest.raises(ValueError):
        RetryPolicy(max_retries=-1)
    with pytest.raises(ValueError):
        RetryPolicy(base_delay_s=0)
    with pytest.raises(ValueError):
        RetryPolicy(factor=0.5)
    # A zero queue capacity is normalised instead of dividing by zero.
    assert LoadSample(queue_len=3, queue_capacity=0).ratios()["queue"] <= 1.0
