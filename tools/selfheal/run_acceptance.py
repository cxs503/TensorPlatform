#!/usr/bin/env python3
"""Acceptance harness for campaign DAG self-healing & elastic scheduling.

Runs the seven machine-checkable criteria (C1-C7) of mission node P162W1-N08
against the *real* engine (``backend/services/selfheal``), writes a
machine-readable JSON artifact and four publication figures.

Usage (from the repository root)::

    python tools/selfheal/run_acceptance.py [--out artifacts/selfheal]

Everything is deterministic (fixed seeds); no cluster/DB access is needed.
"""
from __future__ import annotations

import argparse
import json
import random
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
BACKEND = REPO_ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from services.selfheal import (  # noqa: E402
    AuditChain,
    CycleError,
    LoadSample,
    NodeStatus,
    RetryPolicy,
    SelfHealEngine,
    SelfHealingDag,
    SelfHealPolicy,
)
from services.selfheal.engine import relative_deviation  # noqa: E402

SEED = 20261110


# ───────────────────────── synthetic workload ─────────────────────────

def series(iterations: int) -> float:
    """Deterministic iterative estimator used as a stand-in for any solver."""
    return 4.0 * sum(((-1) ** k) / (2 * k + 1) for k in range(iterations))


def chain_dag(n: int, **kwargs) -> SelfHealingDag:
    dag = SelfHealingDag("acceptance")
    for i in range(1, n + 1):
        dag.add_node(i, f"n{i}", deps=[i - 1] if i > 1 else [], **kwargs)
    return dag


# ───────────────────────── criteria ─────────────────────────

def c1_retry_recovery() -> dict[str, Any]:
    """C1: fault-injected nodes recover within 3 retries >= 80 % of the time."""
    rng = random.Random(SEED)
    trials, fail_prob = 200, 0.40
    recovered = 0
    attempts_used: list[int] = []
    for t in range(trials):
        dag = SelfHealingDag(f"fault-{t}")
        dag.add_node(1, "task")
        engine = SelfHealEngine(dag, policy=SelfHealPolicy(
            retry=RetryPolicy(max_retries=3, base_delay_s=2.0, factor=2.0)
        ))
        now = 1000.0
        for attempt in range(4):
            if rng.random() >= fail_prob:
                engine.report_success(1, result=series(200), now=now)
                break
            action = engine.report_failure(1, "injected_fault", now=now)
            now = action.get("next_attempt_at", now + 4.0)
        if dag.nodes[1].status is NodeStatus.DONE:
            recovered += 1
            attempts_used.append(dag.nodes[1].attempts)
    rate = recovered / trials
    return {
        "id": "C1",
        "name": "automatic_retry_recovery_rate",
        "trials": trials,
        "fault_probability": fail_prob,
        "retries_allowed": 3,
        "recovered": recovered,
        "measured": round(rate, 4),
        "threshold": 0.80,
        "unit": "fraction",
        "passed": rate >= 0.80,
        "theoretical_rate": round(1 - fail_prob ** 4, 4),
        "mean_attempts_on_recovery": round(
            sum(attempts_used) / len(attempts_used), 3
        ) if attempts_used else None,
    }


def c2_bypass_routing() -> dict[str, Any]:
    """C2: a bypassed node must not cascade — downstream nodes still run."""
    dag = SelfHealingDag("bypass")
    dag.add_node(1, "head")
    dag.add_node(2, "verify", deps=[1])
    dag.add_node(3, "publish", deps=[2])
    engine = SelfHealEngine(dag)
    for i in range(4):
        engine.report_failure(1, "permanent", now=100.0 + i)
    engine.report_success(2, 1.0, now=200.0)
    engine.schedule(now=200.0, load=LoadSample(cpu_pct=10))
    engine.report_success(3, 2.0, now=201.0)

    statuses = {n.node_id: n.status.value for n in dag.nodes.values()}
    downstream_done = sum(statuses[i] == "done" for i in (2, 3))
    cascade_fails = sum(statuses[i] in ("failed", "blocked") for i in (2, 3))
    return {
        "id": "C2",
        "name": "bypass_routing_no_cascade",
        "head_status": statuses[1],
        "downstream_statuses": {2: statuses[2], 3: statuses[3]},
        "downstream_done": downstream_done,
        "downstream_total": 2,
        "cascade_failures": cascade_fails,
        "measured": downstream_done,
        "threshold": 2,
        "unit": "nodes_done",
        "passed": statuses[1] == "skipped" and downstream_done == 2 and cascade_fails == 0,
    }


def c3_degraded_execution() -> dict[str, Any]:
    """C3: accepted degraded result deviates < 15 % from the normal result."""
    baseline = series(200)
    degraded = series(40)
    deviation = relative_deviation(degraded, baseline)

    dag = SelfHealingDag("degrade")
    dag.add_node(1, "solver", degradable=True)
    engine = SelfHealEngine(dag, policy=SelfHealPolicy())
    engine.set_baseline(1, baseline)
    engine.bind_degraded_executor(1, lambda node: series(40))
    action = None
    for i in range(4):
        action = engine.report_failure(1, "timeout", now=10.0 + i)
    accepted = action["action"] == "degraded"

    # Configurable threshold: a 0.1 % tolerance must reject the same result.
    strict = SelfHealPolicy(
        retry=SelfHealPolicy().retry,
        resources=SelfHealPolicy().resources,
        degradation=type(SelfHealPolicy().degradation)(enabled=True, max_deviation=0.001),
    )
    dag2 = SelfHealingDag("degrade-strict")
    dag2.add_node(1, "solver", degradable=True)
    engine2 = SelfHealEngine(dag2, policy=strict)
    engine2.set_baseline(1, baseline)
    for i in range(4):
        strict_action = engine2.report_failure(
            1, "timeout", now=10.0 + i, degraded_result=degraded
        )
    rejected = strict_action["action"] == "bypassed"

    return {
        "id": "C3",
        "name": "degraded_result_deviation",
        "normal_iterations": 200,
        "degraded_iterations": 40,
        "normal_value": round(baseline, 6),
        "degraded_value": round(degraded, 6),
        "measured": round(deviation, 6),
        "threshold": 0.15,
        "unit": "relative_deviation",
        "quality_label": dag.nodes[1].quality,
        "accepted_as_degraded": accepted,
        "strict_threshold_rejected": rejected,
        "passed": accepted and rejected and deviation < 0.15
        and dag.nodes[1].quality == "degraded",
    }


def c4_negative_control() -> dict[str, Any]:
    """C4: a healthy campaign must emit zero healing events."""
    dag = chain_dag(4)
    engine = SelfHealEngine(dag)
    now = 500.0
    for nid in (1, 2, 3, 4):
        engine.schedule(now=now, load=LoadSample(cpu_pct=25, mem_pct=40))
        engine.report_success(nid, float(nid), now=now)
        now += 1.0
    heal = engine.heal_audit()
    return {
        "id": "C4",
        "name": "no_fault_no_heal",
        "nodes": 4,
        "measured": len(heal),
        "threshold": 0,
        "unit": "heal_events",
        "total_audit_events": len(engine.audit),
        "audit_integrity": engine.audit.verify(),
        "passed": len(heal) == 0 and engine.audit.verify(),
    }


def c5_resource_scheduling() -> dict[str, Any]:
    """C5: above 80 % load, scheduling latency for ready work stays < 30 s."""
    dag = SelfHealingDag("elastic")
    dag.add_node(1, "critical", critical=True)
    dag.add_node(2, "background")
    engine = SelfHealEngine(dag)
    t0 = 1000.0
    load = LoadSample(cpu_pct=92, mem_pct=60, queue_len=60, queue_capacity=64)
    sched = engine.schedule(now=t0, load=load)
    critical_latency = next(
        d["delay_s"] for d in sched["dispatched"] if d["node_id"] == 1
    )
    deferred = next(d for d in sched["deferred"] if d["node_id"] == 2)
    later = engine.schedule(
        now=t0 + deferred["delay_s"], load=LoadSample(cpu_pct=25)
    )
    ran_later = 2 in [d["node_id"] for d in later["dispatched"]]

    # Delay curve across loads (see fig2).
    curve = []
    probe = SelfHealEngine(SelfHealingDag("probe"))
    for pct in (50, 70, 80, 81, 85, 90, 95, 99, 100):
        curve.append({"cpu_pct": pct, "defer_s": probe._defer_delay(pct / 100.0)})

    return {
        "id": "C5",
        "name": "saturated_elastic_scheduling",
        "load": load.to_dict(),
        "saturated": sched["saturated"],
        "critical_latency_s": round(critical_latency, 6),
        "background_defer_s": round(deferred["delay_s"], 6),
        "deferred_node_ran_later": ran_later,
        "max_scheduling_latency_s": round(deferred["delay_s"], 6),
        "measured": round(deferred["delay_s"], 6),
        "threshold": 30.0,
        "unit": "seconds",
        "delay_curve": curve,
        "passed": sched["saturated"] and critical_latency == 0.0
        and deferred["delay_s"] < 30.0 and ran_later,
    }


def c6_audit_chain() -> dict[str, Any]:
    """C6: every healing action has time/node/action/outcome + valid hash link."""
    dag = SelfHealingDag("audit")
    dag.add_node(1, "a", degradable=True)
    dag.add_node(2, "b", deps=[1])
    engine = SelfHealEngine(dag)
    engine.set_baseline(1, series(200))
    engine.report_failure(1, "t1", now=10.0)
    engine.report_failure(1, "t2", now=20.0)
    engine.report_success(1, series(200), now=30.0)  # recovered
    engine.report_failure(2, "permanent", now=40.0, degraded_result=series(40))
    engine.schedule(now=50.0, load=LoadSample(cpu_pct=95))

    heal = engine.audit.heal_events()
    complete = 0
    for ev in heal:
        has_subject = ev.node_id is not None or ev.action in {
            "saturation_detected", "cycle_rejected", "policy_updated"
        }
        if ev.ts > 0 and ev.action and ev.outcome and has_subject:
            complete += 1
    integrity = engine.audit.verify()
    tampered = engine.audit.to_dicts()
    tampered[0]["detail"]["injected"] = True
    tamper_detected = not AuditChain(tampered).verify()
    return {
        "id": "C6",
        "name": "selfheal_audit_chain",
        "heal_events": len(heal),
        "complete_events": complete,
        "completion_rate": round(complete / len(heal), 4) if heal else 0.0,
        "measured": round(complete / len(heal), 4) if heal else 0.0,
        "chain_integrity": integrity,
        "tamper_detected": tamper_detected,
        "actions": [ev.action for ev in heal],
        "threshold": 1.0,
        "unit": "completion_fraction",
        "passed": bool(heal) and complete == len(heal) and integrity and tamper_detected,
    }


def c7_acyclic() -> dict[str, Any]:
    """C7: healing never changes edges; cycles are rejected before execution."""
    dag = SelfHealingDag("acyclic")
    dag.add_node(1, "a")
    dag.add_node(2, "b", deps=[1])
    dag.add_node(3, "c", deps=[2])
    edges_before = set(dag.edges)
    engine = SelfHealEngine(dag)
    for i in range(4):
        engine.report_failure(1, "permanent", now=100.0 + i)
    engine.schedule(now=200.0, load=LoadSample(cpu_pct=99))
    engine.report_success(2, 1.0, now=201.0)
    engine.resolve_blocked(now=202.0)
    edges_after = set(dag.edges)
    order = dag.validate()

    cyclic = SelfHealingDag("cyclic")
    cyclic.add_node(1, "a", deps=[3])
    cyclic.add_node(2, "b", deps=[1])
    cyclic.add_node(3, "c", deps=[2])
    cycle_rejected, cycle_path = False, []
    try:
        SelfHealEngine(cyclic)
    except CycleError as exc:
        cycle_rejected = True
        cycle_path = [str(x) for x in exc.cycle]
    return {
        "id": "C7",
        "name": "acyclic_invariant",
        "edges_before": sorted(map(str, edges_before)),
        "edges_after": sorted(map(str, edges_after)),
        "edges_unchanged": edges_after == edges_before,
        "topological_order": order,
        "cycle_rejected": cycle_rejected,
        "cycle_path": cycle_path,
        "measured": int(edges_after == edges_before and cycle_rejected),
        "threshold": 1,
        "unit": "boolean_invariant",
        "passed": edges_after == edges_before and cycle_rejected
        and len(order) == 3,
    }


# ───────────────────────── figures ─────────────────────────

def make_figures(out: Path, results: dict[str, dict[str, Any]]) -> list[dict[str, str]]:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    figdir = out / "figures"
    figdir.mkdir(parents=True, exist_ok=True)
    figures: list[dict[str, str]] = []

    # fig1 — recovery probability vs retry budget
    probs = [1 - 0.4 ** (k + 1) for k in range(0, 6)]
    fig, ax = plt.subplots(figsize=(6, 4))
    ax.plot(range(0, 6), [p * 100 for p in probs], "o-", label="P(recover) p_fail=0.40")
    ax.axhline(80, color="tab:red", ls="--", label="C1 threshold 80 %")
    measured = results["C1"]["measured"] * 100
    ax.axhline(measured, color="tab:green", ls=":", label=f"measured {measured:.1f} %")
    ax.set_xlabel("retries allowed")
    ax.set_ylabel("recovery rate within budget (%)")
    ax.set_title("C1 automatic retry — recovery vs retry budget")
    ax.set_ylim(60, 101)
    ax.grid(alpha=0.3)
    ax.legend(fontsize=8)
    path = figdir / "fig1_retry_recovery.png"
    fig.tight_layout()
    fig.savefig(path, dpi=130)
    plt.close(fig)
    figures.append({"name": path.name, "caption":
                    "C1: recovery probability under 40 % per-attempt fault injection "
                    "vs retry budget; measured 200-trial rate (green) vs 80 % gate."})

    # fig2 — elastic scheduling delay vs load
    curve = results["C5"]["delay_curve"]
    loads = [c["cpu_pct"] for c in curve]
    delays = [c["defer_s"] for c in curve]
    fig, ax = plt.subplots(figsize=(6, 4))
    ax.plot(loads, delays, "o-", color="tab:blue",
            label="non-critical defer (s)")
    ax.axhline(0, color="tab:orange", lw=3, label="critical latency = 0 s")
    ax.axvline(80, color="tab:red", ls="--", label="saturation threshold 80 %")
    ax.axhline(30, color="tab:gray", ls=":", label="C5 cap 30 s")
    ax.set_xlabel("worst resource load (%)")
    ax.set_ylabel("scheduling delay (s)")
    ax.set_title("C5 elastic scheduling — delay vs cluster load")
    ax.grid(alpha=0.3)
    ax.legend(fontsize=8)
    path = figdir / "fig2_elastic_scheduling.png"
    fig.tight_layout()
    fig.savefig(path, dpi=130)
    plt.close(fig)
    figures.append({"name": path.name, "caption":
                    "C5: non-critical deferral grows with load but stays below "
                    "the 30 s cap; critical-path work has zero added latency."})

    # fig3 — degradation deviation vs reduced iterations
    baseline = series(200)
    iters = [2, 5, 10, 20, 40, 80, 120, 200]
    devs = [relative_deviation(series(n), baseline) * 100 for n in iters]
    fig, ax = plt.subplots(figsize=(6, 4))
    ax.semilogx(iters, devs, "o-", base=2, label="relative deviation")
    ax.axhline(15, color="tab:red", ls="--", label="C3 tolerance 15 %")
    ax.axvline(40, color="tab:green", ls=":", label="degraded profile (40 it.)")
    ax.set_xlabel("iterations of degraded run (normal = 200)")
    ax.set_ylabel("deviation vs normal result (%)")
    ax.set_title("C3 degradation — accuracy cost of reduced iterations")
    ax.grid(alpha=0.3, which="both")
    ax.legend(fontsize=8)
    path = figdir / "fig3_degradation_tradeoff.png"
    fig.tight_layout()
    fig.savefig(path, dpi=130)
    plt.close(fig)
    figures.append({"name": path.name, "caption":
                    "C3: deviation of degraded results vs iteration budget; the "
                    "40-iteration profile (green) stays well inside the 15 % gate."})

    # fig4 — audit trail composition
    actions = results["C6"]["actions"]
    counts: dict[str, int] = {}
    for a in actions:
        counts[a] = counts.get(a, 0) + 1
    fig, ax = plt.subplots(figsize=(6.5, 4))
    ax.bar(list(counts.keys()), list(counts.values()), color="tab:purple")
    ax.set_ylabel("events")
    ax.set_title("C6 audit chain — healing actions recorded (hash-linked)")
    ax.tick_params(axis="x", labelrotation=30)
    for i, (k, v) in enumerate(counts.items()):
        ax.text(i, v + 0.05, str(v), ha="center", fontsize=8)
    path = figdir / "fig4_audit_chain_actions.png"
    fig.tight_layout()
    fig.savefig(path, dpi=130)
    plt.close(fig)
    figures.append({"name": path.name, "caption":
                    "C6: composition of the audited healing actions in the mixed "
                    "retry/recover/degrade/saturation scenario."})

    return figures


# ───────────────────────── main ─────────────────────────

def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="artifacts/selfheal")
    args = ap.parse_args()
    out = (REPO_ROOT / args.out).resolve()
    out.mkdir(parents=True, exist_ok=True)

    results = {
        "C1": c1_retry_recovery(),
        "C2": c2_bypass_routing(),
        "C3": c3_degraded_execution(),
        "C4": c4_negative_control(),
        "C5": c5_resource_scheduling(),
        "C6": c6_audit_chain(),
        "C7": c7_acyclic(),
    }
    figures = make_figures(out, results)
    all_passed = all(r["passed"] for r in results.values())
    payload = {
        "mission_id": 9077,
        "node": "P162W1-N08",
        "module": "backend/services/selfheal",
        "api": "backend/api/campaign_self_heal.py",
        "seed": SEED,
        "generated_by": "tools/selfheal/run_acceptance.py",
        "criteria": results,
        "figures": [str((out / "figures" / f["name"]).relative_to(REPO_ROOT))
                    for f in figures],
        "all_passed": all_passed,
    }
    manifest = out / "acceptance.json"
    manifest.write_text(json.dumps(payload, indent=2, ensure_ascii=False))
    (out / "figures_metadata.json").write_text(
        json.dumps(figures, indent=2, ensure_ascii=False)
    )

    for cid, r in results.items():
        print(f"{cid}: {'PASS' if r['passed'] else 'FAIL'}  "
              f"measured={r['measured']} threshold={r['threshold']} {r['unit']}")
    print(f"artifact: {manifest.relative_to(REPO_ROOT)}")
    print(f"figures : {len(figures)} -> {out / 'figures'}")
    return 0 if all_passed else 1


if __name__ == "__main__":
    raise SystemExit(main())