"""Campaign-gate lifecycle automation (dispatch -> execute -> adjudicate ->
capability ledger -> gap analysis -> proposal injection -> next-wave seeding).

Public API::

    from tools.gate_lifecycle import Plan, LifecycleConfig, run, StaticExecutor

    plan = Plan.from_dict({"plan_id": "w1", "nodes": [...]})
    result = run(plan, cfg=LifecycleConfig(capacity=16,
                                           ledger_path="ledger.jsonl",
                                           pool_path="pool.jsonl"))
    result.failures["failure_rate"]      # honest FAIL excluded (C7)
    result.result_digest                  # idempotency fingerprint (C6)
"""

from . import baseline, dag, ledger, pipeline, verdict
from .autoregress import (
    ProposalSink,
    gap_analysis,
    inject,
    proposals_from_gaps,
    seed_next_wave,
)
from .dag import critical_path, find_cycle, schedule, slacks, topological_order, validate_schedule, waves
from .executor import JsonCommandExecutor, ReplayExecutor, StaticExecutor
from .ledger import CapabilityLedger, build_record, grade_of, registration_completeness
from .model import (
    FAIL,
    PASS,
    WITHHELD,
    Criterion,
    Node,
    NodeResult,
    Plan,
    PlanError,
    CycleError,
    canonical_json,
    digest,
)
from .pipeline import LifecycleConfig, LifecycleResult, run, write_artifacts
from .verdict import adjudicate, evaluate_criterion, evaluate_node, failure_accounting

__all__ = [
    "Plan", "Node", "Criterion", "NodeResult", "PlanError", "CycleError",
    "PASS", "FAIL", "WITHHELD",
    "run", "LifecycleConfig", "LifecycleResult", "write_artifacts",
    "StaticExecutor", "JsonCommandExecutor", "ReplayExecutor",
    "schedule", "critical_path", "topological_order", "find_cycle", "slacks",
    "waves", "validate_schedule",
    "evaluate_node", "evaluate_criterion", "adjudicate", "failure_accounting",
    "CapabilityLedger", "build_record", "grade_of", "registration_completeness",
    "gap_analysis", "proposals_from_gaps", "inject", "seed_next_wave", "ProposalSink",
    "canonical_json", "digest", "baseline", "dag", "ledger", "pipeline", "verdict",
]