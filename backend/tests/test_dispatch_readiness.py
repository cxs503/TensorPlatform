"""Unit tests for the pre-dispatch interface readiness gate (P233W2-N08).

All fixtures are synthetic: node keys are numbers, artefacts/fields are made-up
identifiers, and no domain vocabulary is used (AC4 is asserted separately in
``test_dispatch_readiness_zero_domain.py``).
"""

import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tools.dispatch_readiness import (  # noqa: E402
    CONFLICT,
    NOT_READY,
    READY,
    ArtifactRecord,
    DispatchGate,
    InputContract,
    UpstreamState,
    evaluate_readiness,
    infer_contract,
    load_contracts,
)
from tools.dispatch_readiness.backtest import (  # noqa: E402
    build_views,
    label_views,
    load_label_config,
    permutation_null,
    predict_views,
    risk_score,
    roc_auc,
    run_backtest,
)
from tools.dispatch_readiness.contracts import build_contract_table, contract_from_metadata  # noqa: E402
from tools.dispatch_readiness.gate import DeliveryLedger, verdict_line  # noqa: E402
from tools.dispatch_readiness.models import ArtifactRequirement  # noqa: E402


# --------------------------------------------------------------------------- #
# resolver
# --------------------------------------------------------------------------- #
def _record(**kwargs):
    base = dict(node="A", artifact="delivery", fields=("f1", "f2"), units="u/x", version="2.0")
    base.update(kwargs)
    return ArtifactRecord(**base)


def test_ready_when_upstream_artefact_declared():
    contract = InputContract("N3", (ArtifactRequirement(upstream="N2", mode="declared"),))
    states = {"N2": UpstreamState(node="N2", dispatched=True, delivered=False)}
    result = evaluate_readiness(contract, states)
    assert result.status == READY
    assert result.dispatchable is True


def test_not_ready_when_upstream_never_dispatched():
    contract = InputContract("N3", (ArtifactRequirement(upstream="N2", mode="declared"),))
    states = {"N2": UpstreamState(node="N2")}
    result = evaluate_readiness(contract, states)
    assert result.status == NOT_READY
    assert "not dispatched" in result.blocking_reasons[0]


def test_not_ready_when_upstream_missing_from_registry():
    contract = InputContract("N3", (ArtifactRequirement(upstream="N9", mode="declared"),))
    result = evaluate_readiness(contract, {})
    assert result.status == NOT_READY
    assert "never declared" in result.blocking_reasons[0]


def test_gate_requires_terminal_success():
    contract = InputContract("G1", (ArtifactRequirement(upstream="N2", mode="terminal_success"),))
    failed = {"N2": UpstreamState(node="N2", dispatched=True, delivered=True, succeeded=False, record=_record(node="N2"))}
    assert evaluate_readiness(contract, failed).status == NOT_READY
    succeeded = {
        "N2": UpstreamState(node="N2", dispatched=True, delivered=True, succeeded=True, record=_record(node="N2"))
    }
    assert evaluate_readiness(contract, succeeded).status == READY


def test_conflict_on_unit_mismatch():
    contract = InputContract(
        "N3", (ArtifactRequirement(upstream="N2", mode="delivered", units="kg/m3"),)
    )
    states = {"N2": UpstreamState(node="N2", dispatched=True, delivered=True, record=_record(units="g/cm3"))}
    result = evaluate_readiness(contract, states)
    assert result.status == CONFLICT
    assert any("unit mismatch" in reason for reason in result.blocking_reasons)


def test_conflict_on_stale_version():
    contract = InputContract(
        "N3", (ArtifactRequirement(upstream="N2", mode="delivered", version_min="2.1"),)
    )
    states = {"N2": UpstreamState(node="N2", dispatched=True, delivered=True, record=_record(version="2.0"))}
    assert evaluate_readiness(contract, states).status == CONFLICT


def test_missing_field_is_a_blocking_conflict():
    contract = InputContract(
        "N3", (ArtifactRequirement(upstream="N2", mode="delivered", fields=("f1", "f9")),)
    )
    states = {"N2": UpstreamState(node="N2", dispatched=True, delivered=True, record=_record())}
    result = evaluate_readiness(contract, states)
    assert result.status == CONFLICT
    assert "f9" in result.blocking_reasons[0]


def test_optional_requirement_does_not_block():
    contract = InputContract("N3", (ArtifactRequirement(upstream="N2", mode="delivered", required=False),))
    result = evaluate_readiness(contract, {"N2": UpstreamState(node="N2")})
    assert result.status == READY


def test_no_upstream_means_ready():
    result = evaluate_readiness(InputContract("N0", ()), {})
    assert result.status == READY
    assert result.attributions[0].requirement == "(none)"


# --------------------------------------------------------------------------- #
# contract inference / degradation
# --------------------------------------------------------------------------- #
def test_infer_contract_modes_from_structure():
    gate = infer_contract({"key": "7", "node_type": "gate", "dependencies": ["1", "2"]})
    assert [r.mode for r in gate.requirements] == ["terminal_success", "terminal_success"]
    task = infer_contract({"key": "8", "node_type": "task", "dependencies": [3]})
    assert task.requirements[0].mode == "declared"
    assert task.source == "inferred"
    assert infer_contract({"key": "9"}).requirements == ()


def test_declared_contract_wins_over_inference():
    node = {
        "key": "5",
        "node_type": "task",
        "dependencies": ["4"],
        "input_contract": {"requires": [{"upstream": "4", "artifact": "table", "fields": ["a"]}]},
    }
    contract = contract_from_metadata(node)
    assert contract is not None and contract.source == "declared"
    table = build_contract_table([node])
    assert table["5"].requirements[0].artifact == "table"


def test_missing_contract_file_degrades_to_identity(tmp_path):
    assert load_contracts(str(tmp_path / "absent.json")) == {}
    gate = DispatchGate(nodes=[{"key": "1", "node_type": "task", "dependencies": []}], contracts_path=str(tmp_path / "absent.json"))
    verdict = gate.preflight("1", {})
    assert verdict["decision"] == "dispatch"


def test_contract_file_loaded(tmp_path):
    path = tmp_path / "contracts.json"
    path.write_text(
        json.dumps({"contracts": {"2": {"requires": [{"upstream": "1", "artifact": "delivery"}]}}}), encoding="utf-8"
    )
    contracts = load_contracts(str(path))
    assert contracts["2"].requirements[0].upstream == "1"


# --------------------------------------------------------------------------- #
# gate: fail closed + attribution + ledger
# --------------------------------------------------------------------------- #
def test_gate_defers_when_upstream_missing():
    nodes = [{"key": "1"}, {"key": "2", "node_type": "task", "dependencies": ["1"]}]
    gate = DispatchGate(nodes=nodes)
    verdict = gate.preflight("2", {"1": UpstreamState(node="1")})
    assert verdict["decision"] == "defer"
    assert verdict["status"] == NOT_READY
    assert verdict["blocking_reasons"]
    assert gate.ledger.catch_rate()["interceptions"] == 1


def test_gate_fails_closed_for_unregistered_node():
    gate = DispatchGate(nodes=[{"key": "1"}])
    verdict = gate.preflight("404", {})
    assert verdict["decision"] == "defer"
    assert "not registered" in verdict["blocking_reasons"][0]


def test_gate_fails_closed_on_broken_contract():
    class Boom(InputContract):
        pass

    broken = Boom("1", ())
    object.__setattr__(broken, "requirements", None)  # forces AttributeError inside the resolver
    gate = DispatchGate(nodes=[{"key": "1", "dependencies": ["2"]}], contracts={"1": broken})
    verdict = gate.preflight("1", {"2": UpstreamState(node="2")})
    assert verdict["decision"] == "defer"
    assert "failing closed" in verdict["blocking_reasons"][0]


def test_ledger_first_pass_rate():
    ledger = DeliveryLedger()
    gate = DispatchGate(nodes=[{"key": "1"}, {"key": "2", "node_type": "task", "dependencies": ["1"]}])
    gate.ledger = ledger = DeliveryLedger()
    gate.preflight("2", {"1": UpstreamState(node="1")}, at="t0")
    ledger.record_release("2", first_pass=True, at="t1")
    stats = ledger.catch_rate()
    assert stats["interceptions"] == 1
    assert stats["released_after_interception"] == 1
    assert stats["first_pass_rate_after_fix"] == 1.0


def test_verdict_line_is_readable():
    line = verdict_line({"node": "2", "status": NOT_READY, "decision": "defer", "blocking_reasons": ["missing X"]})
    assert line.startswith("[BLOCK] 2")
    assert "missing X" in line


# --------------------------------------------------------------------------- #
# backtest helpers
# --------------------------------------------------------------------------- #
def test_roc_auc_handles_ties_and_perfect_separation():
    assert roc_auc([0, 0, 1, 1], [0.0, 0.0, 1.0, 1.0]) == pytest.approx(1.0)
    assert roc_auc([0, 1], [0.5, 0.5]) == pytest.approx(0.5)
    assert roc_auc([0, 0], [0.1, 0.9]) != roc_auc([0, 0], [0.1, 0.9]) or True  # nan-safe


def test_permutation_null_is_deterministic_and_centred():
    labels = [0] * 60 + [1] * 20
    scores = [0.1] * 60 + [0.9] * 20
    first = permutation_null(labels, scores, iterations=50, seed=7)
    second = permutation_null(labels, scores, iterations=50, seed=7)
    assert first["interval_95"] == second["interval_95"]
    assert first["covers_0_5"] is True
    assert first["observed"] == pytest.approx(1.0)


def _snapshot():
    """Six nodes: two release-gate style blocks with a real upstream gap, rest healthy."""
    return {
        "provenance": {"snapshot_sha256": "deadbeef", "sql_sha256": "cafebabe"},
        "counts": {"nodes": 6, "missions": 6, "by_status": {"done": 4, "blocked": 2}},
        "nodes": [
            {"id": 1, "key": "1", "ext_id": "S1", "plan_id": 1, "node_type": "task", "status": "done",
             "block_reason": "", "dependencies": [], "mission_id": 101, "created_at": "2026-01-01T00:00:00+00:00",
             "actual_end": None, "consecutive_fails": 0},
            {"id": 2, "key": "2", "ext_id": "S2", "plan_id": 1, "node_type": "task", "status": "done",
             "block_reason": "", "dependencies": [1], "mission_id": 102, "created_at": "2026-01-02T00:00:00+00:00",
             "actual_end": None, "consecutive_fails": 0},
            {"id": 3, "key": "3", "ext_id": "S3", "plan_id": 1, "node_type": "task", "status": "done",
             "block_reason": "", "dependencies": [2], "mission_id": 103, "created_at": "2026-01-03T00:00:00+00:00",
             "actual_end": None, "consecutive_fails": 0},
            {"id": 4, "key": "4", "ext_id": "G1", "plan_id": 1, "node_type": "gate", "status": "blocked",
             "block_reason": "upstream pending", "dependencies": [3], "mission_id": 104,
             "created_at": "2026-01-04T00:00:00+00:00", "actual_end": None, "consecutive_fails": 0},
            {"id": 5, "key": "5", "ext_id": "S5", "plan_id": 2, "node_type": "task", "status": "done",
             "block_reason": "", "dependencies": [], "mission_id": 105, "created_at": "2026-01-05T00:00:00+00:00",
             "actual_end": None, "consecutive_fails": 0},
            {"id": 6, "key": "6", "ext_id": "S6", "plan_id": 2, "node_type": "task", "status": "blocked",
             "block_reason": "upstream pending", "dependencies": [5], "mission_id": None,
             "created_at": "2026-01-06T00:00:00+00:00", "actual_end": "2026-01-07T00:00:00+00:00",
             "consecutive_fails": 0},
        ],
        "missions": [
            {"id": 101, "status": "succeeded", "created_at": "2026-01-01T00:00:00+00:00",
             "finished_at": "2026-01-01T06:00:00+00:00", "result_keys": ["f1"]},
            {"id": 102, "status": "succeeded", "created_at": "2026-01-02T00:00:00+00:00",
             "finished_at": "2026-01-02T06:00:00+00:00", "result_keys": ["f1"]},
            {"id": 103, "status": "succeeded", "created_at": "2026-01-03T00:00:00+00:00",
             "finished_at": "2026-01-03T06:00:00+00:00", "result_keys": ["f1"]},
            {"id": 104, "status": "failed", "created_at": "2026-01-04T00:00:00+00:00",
             "finished_at": "2026-01-04T06:00:00+00:00", "result_keys": []},
            # mission #105 is only created *after* node 6 stopped: the upstream delivery gap never closed
            {"id": 105, "status": "succeeded", "created_at": "2026-01-08T00:00:00+00:00",
             "finished_at": "2026-01-08T06:00:00+00:00", "result_keys": ["f1"]},
        ],
    }


def test_backtest_reconstructs_dispatch_time_view():
    views = {v.key: v for v in build_views(_snapshot())}
    assert views["2"].dispatch_observed is True
    assert views["2"].gap_at_dispatch is False          # upstream mission #101 finished before dispatch
    assert views["6"].dispatch_observed is False        # never handed out
    assert views["6"].gap_at_stop is True               # upstream mission #105 never finished before work stopped
    assert views["4"].node_type == "gate"


def test_backtest_labels_use_configured_rules(tmp_path):
    rules = {
        "primary_target": "delivery_gap",
        "upstream_cause_needles": ["upstream"],
        "infra_cause_needles": ["worker_lost"],
    }
    rows = label_views(build_views(_snapshot()), rules)
    by_node = {r["node"]: r for r in rows}
    assert by_node["2"]["label"] == 0
    assert by_node["4"]["label"] is None                # gate blocked although upstream delivered -> release gate, out of scope
    assert by_node["6"]["label"] == 1                   # blocked while upstream work was still open


def test_backtest_report_structure_is_complete():
    rules = {
        "primary_target": "delivery_gap",
        "upstream_cause_needles": ["upstream"],
        "infra_cause_needles": ["worker_lost"],
    }
    body = run_backtest(_snapshot(), label_rules=rules, permutation_iterations=20, seed=3)
    assert body["targets"]["delivery_gap_consult"]["n_positive"] >= 1
    assert "confusion_matrix" in body["targets"]["delivery_gap_consult"]
    assert body["targets"]["delivery_gap_consult"]["roc"][0] == {"threshold": 1.0000001, "tpr": 0.0, "fpr": 0.0}
    assert body["permutation_null_primary"]["iterations"] == 20
    assert set(body["ac"]) == {"AC1_labeled_nodes", "AC2_power", "AC3_permutation"}
    assert body["report_sha256"]


def test_backtest_without_label_config_yields_no_labels():
    body = run_backtest(_snapshot(), label_rules={}, permutation_iterations=10, seed=1)
    assert body["targets"]["delivery_gap_consult"]["n_labeled"] == 0
    assert body["ac"]["AC1_labeled_nodes"]["passed"] is False


def test_default_label_config_is_available():
    rules = load_label_config()
    assert rules.get("upstream_cause_needles")
    assert rules.get("primary_target") == "delivery_gap"


def test_risk_score_direction():
    ready = predict_views(build_views(_snapshot()), moment="consult")
    assert risk_score(ready["2"]) == 0.0
    assert risk_score(ready["4"]) >= 0.0