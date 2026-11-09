"""End-to-end campaign-gate lifecycle: dispatch -> execute -> adjudicate ->
ledger -> gap analysis -> proposals -> seed next wave.

One call to :func:`run` performs the whole chain with no human step.  The
returned payload is deterministic in the plan + config (``run_id`` is a digest of
those two only; no timestamps, no absolute paths inside the hashed body), so the
same input twice yields byte-identical per-node results -- the C6 idempotency
contract.

Failure handling: a node whose dependency did not PASS is never executed; it is
recorded as ``blocked`` (verdict WITHHELD) so a broken upstream cannot silently
produce downstream "success".
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

from .autoregress import ProposalSink, gap_analysis, inject, proposals_from_gaps, seed_next_wave
from .dag import critical_path, schedule, slacks, validate_schedule, waves
from .executor import Executor, StaticExecutor
from .ledger import CapabilityLedger, build_record, registration_completeness
from .model import (
    FAIL,
    PASS,
    STATUS_BLOCKED,
    WITHHELD,
    NodeResult,
    Plan,
    digest,
)
from .verdict import evaluate_node, failure_accounting

DEFAULT_CAPACITY = 16  # >= 10 required by the campaign dispatcher contract


@dataclass
class LifecycleConfig:
    capacity: int = DEFAULT_CAPACITY
    plan_id: str | None = None
    ledger_path: str | Path | None = None
    pool_path: str | Path | None = None
    seed_plan_id: str | None = None
    max_seed_nodes: int = 10


@dataclass
class LifecycleResult:
    run_id: str
    plan: Plan
    schedule: Mapping[str, Any]
    node_results: tuple[NodeResult, ...]
    ledger_stats: Mapping[str, Any]
    completeness: Mapping[str, Any]
    gaps: Mapping[str, Any]
    proposals: Mapping[str, Any]
    injection: Mapping[str, Any]
    seeded_plan: Mapping[str, Any]
    failures: Mapping[str, Any]
    invariants: Mapping[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": "gate-lifecycle-result/v1",
            "run_id": self.run_id,
            "plan_id": self.plan.plan_id,
            "schedule": dict(self.schedule),
            "node_results": [r.to_dict() for r in self.node_results],
            "ledger": dict(self.ledger_stats),
            "completeness": dict(self.completeness),
            "gaps": dict(self.gaps),
            "proposals": {k: v for k, v in self.proposals.items()},
            "injection": dict(self.injection),
            "seeded_plan": dict(self.seeded_plan),
            "failures": dict(self.failures),
            "invariants": dict(self.invariants),
        }

    def compact(self) -> dict[str, Any]:
        """Byte-stable projection used for the idempotency digest.

        Only *stateless* outputs are hashed: the schedule, the per-node results,
        ledger completeness, gaps, failure accounting and dispatch invariants.
        The four stateful outputs are excluded on purpose and are asserted
        separately (dedupe instead of equality):

        ``ledger``      counters grow with every new run (dedupe by record key);
        ``proposals``   a proposal already in the pool is dropped, so a second run
                        legitimately generates zero new proposals;
        ``injection``   pool size/bookkeeping;
        ``seeded_plan`` it seeds whatever is still *pending* in the pool.
        """
        body = self.to_dict()
        for key in ("ledger", "proposals", "injection", "seeded_plan"):
            body.pop(key, None)
        return body

    @property
    def node_digest(self) -> str:
        """Digest of the per-node adjudication only (the C6 unit of comparison)."""
        return digest([r.to_dict() for r in self.node_results])

    @property
    def result_digest(self) -> str:
        return digest(self.compact())

    def node_of(self, node_id: str) -> NodeResult:
        for r in self.node_results:
            if r.node_id == node_id:
                return r
        raise KeyError(node_id)


def _run_id(plan: Plan, cfg: LifecycleConfig) -> str:
    return digest({
        "plan": plan.to_dict(),
        "capacity": cfg.capacity,
        "seed_plan_id": cfg.seed_plan_id,
        "max_seed_nodes": cfg.max_seed_nodes,
    })[:16]


def run(plan: Plan, *, executor: Executor | None = None,
        cfg: LifecycleConfig | None = None,
        out_dir: str | Path | None = None,
        known_fingerprints: Sequence[str] = ()) -> LifecycleResult:
    cfg = cfg or LifecycleConfig()
    executor = executor or StaticExecutor()
    run_id = _run_id(plan, cfg)

    # ---- 1. dispatch layer -------------------------------------------
    sched = schedule(plan, capacity=cfg.capacity)
    problems = validate_schedule(plan, sched)
    cp, cp_len = critical_path(plan)
    slack = slacks(plan)

    # ---- 2. execute + adjudicate -------------------------------------
    results: dict[str, NodeResult] = {}
    pending_block: dict[str, str] = {}
    for entry in sched.entries:  # topological-by-start order, deterministic
        node = plan.by_id()[entry.node_id]
        bad = [d for d in node.deps
               if results.get(d) is None or results[d].verdict != PASS]
        if bad:
            results[node.node_id] = NodeResult(
                node_id=node.node_id,
                status=STATUS_BLOCKED,
                verdict=WITHHELD,
                criteria=(),
                flags=("blocked_by:" + ",".join(sorted(bad)),),
                capability=node.capability,
                category=node.category,
            )
            pending_block[node.node_id] = ",".join(sorted(bad))
            continue
        try:
            payload = executor.run(node, plan)
        except Exception as exc:  # executor failure == evidence of failure
            payload = {"measurements": {},
                       "declaration": {"status": "pass",
                                       "error": f"{type(exc).__name__}: {exc}"[:300]}}
            res = evaluate_node(node, payload["measurements"], payload["declaration"])
            res = NodeResult(
                node_id=res.node_id, status="failed", verdict=FAIL, criteria=res.criteria,
                declared_honest_fail=res.declared_honest_fail,
                flags=tuple(sorted(set(res.flags) | {"executor_error"})),
                capability=res.capability, category=res.category,
                evidence={"executor_error": payload["declaration"]["error"]},
            )
            results[node.node_id] = res
            continue
        results[node.node_id] = evaluate_node(
            node, payload.get("measurements", {}), payload.get("declaration", {})
        )

    node_results = tuple(results[n.node_id] for n in sorted(plan.nodes, key=lambda x: x.node_id))

    # ---- 3. capability ledger ---------------------------------------
    ledger_stats: dict[str, Any] = {"appended": 0, "duplicates": 0, "records": 0,
                                    "by_grade": {}, "by_category": {}}
    ledger_records: list[dict[str, Any]] = []
    ledger: CapabilityLedger | None = None
    if cfg.ledger_path:
        ledger = CapabilityLedger(cfg.ledger_path)
        new_records = [
            build_record(r, plan.by_id()[r.node_id].meta, run_id=run_id)
            for r in node_results if r.verdict == PASS
        ]
        stats = ledger.register(new_records)
        ledger_stats = {
            "appended": stats.appended,
            "duplicates": stats.duplicates,
            "records": stats.records,
            "by_grade": stats.by_grade,
            "by_category": stats.by_category,
        }
        ledger_records = [rec for rec in ledger.read() if rec.get("run_id") == run_id]
    else:
        from .ledger import grade_of

        ledger_records = [
            build_record(r, plan.by_id()[r.node_id].meta, run_id=run_id)
            for r in node_results if r.verdict == PASS
        ]
        by_grade: dict[str, int] = {}
        by_cat: dict[str, int] = {}
        for rec in ledger_records:
            by_grade[rec["grade"]] = by_grade.get(rec["grade"], 0) + 1
            by_cat[rec["category"]] = by_cat.get(rec["category"], 0) + 1
        ledger_stats = {"appended": len(ledger_records), "duplicates": 0,
                        "records": len(ledger_records),
                        "by_grade": dict(sorted(by_grade.items())),
                        "by_category": dict(sorted(by_cat.items()))}
        del grade_of

    completeness = registration_completeness(node_results, ledger_records)

    # ---- 4. self-regression loop ------------------------------------
    gaps = gap_analysis(plan, node_results, ledger_records)
    existing_pool: list[dict[str, Any]] = []
    sink: ProposalSink | None = None
    if cfg.pool_path:
        sink = ProposalSink(cfg.pool_path)
        existing_pool = sink.read()
    prop_result = proposals_from_gaps(gaps, existing_pool, known_fingerprints)
    if sink is not None:
        injection = inject(prop_result, sink)
        pool_now = sink.read()
    else:
        injection = {"written": 0, "deduped": prop_result["deduped"],
                     "proposal_ids": [], "pool_path": None, "pool_size": 0}
        pool_now = list(prop_result["proposals"])
    seeded = seed_next_wave(pool_now, plan_id=cfg.seed_plan_id or f"{plan.plan_id}-next",
                            max_nodes=cfg.max_seed_nodes)

    payload = LifecycleResult(
        run_id=run_id,
        plan=plan,
        schedule=sched.to_dict(),
        node_results=node_results,
        ledger_stats=ledger_stats,
        completeness=completeness,
        gaps=gaps,
        proposals=prop_result,
        injection=injection,
        seeded_plan=seeded,
        failures=failure_accounting(node_results),
        invariants={
            "schedule_problems": problems,
            "capacity": sched.capacity,
            "critical_path": cp,
            "critical_length": cp_len,
            "slacks": slack,
            "waves": [list(w) for w in waves(plan)],
            "max_parallelism": sched.max_parallelism(),
            "makespan": sched.makespan,
        },
    )

    if out_dir is not None:
        write_artifacts(payload, out_dir)
    return payload


def write_artifacts(result: LifecycleResult, out_dir: str | Path) -> dict[str, str]:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    files: dict[str, str] = {}
    body = result.to_dict()
    body["result_digest"] = result.result_digest
    path = out / "lifecycle_result.json"
    path.write_text(json.dumps(body, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                    encoding="utf-8")
    files["result"] = str(path)
    for name, payload in (("gap_analysis.json", result.gaps),
                          ("proposals.json", result.proposals),
                          ("seeded_plan.json", result.seeded_plan),
                          ("schedule.json", result.schedule)):
        p = out / name
        p.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                     encoding="utf-8")
        files[name] = str(p)
    return files