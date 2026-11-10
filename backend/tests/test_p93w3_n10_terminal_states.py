"""P93W3-N10 acceptance tests: terminal-state layering + delivery label.

Machine-checked criteria (each maps to one acceptance item in
``tools/p93w3_n10/acceptance.py``):

  (a) four-state classification accuracy 1.00 on a 20-scenario matrix
  (b) 70% MVD checkpoint trigger latency (p100 measured here, budget-frozen
      corpus in ``tools/p93w3_n10/evaluate.py``) <= 1 s
  (c) backward-compatibility migration of legacy ``succeeded`` rows loses no data
  (d) delivery-label JSON-Schema validation pass rate 1.00 (+ negative controls)

Design contract tested here as well: zero domain semantics (generic vocabulary
only) and the ``0.70`` threshold can never drift from the MVD checkpoint.

Run: python3 -m pytest backend/tests/test_p93w3_n10_terminal_states.py -q
"""
from __future__ import annotations

import json
import pathlib
import time
from datetime import datetime, timedelta, timezone

import pytest

from api.domain import mvd_checkpoint as mvd
from api.domain import terminal_state as ts
from api.domain.delivery_state import scan_domain_semantics

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
CORPUS = REPO_ROOT / "runs" / "p93w3-n10" / "corpus" / "missions_succeeded.json"
MODULE_PATH = REPO_ROOT / "backend" / "api" / "domain" / "terminal_state.py"

STATES = ("succeeded", "forced_finish", "degraded", "failed")

# --------------------------------------------------------------------------- #
# Fixtures: generic, synthetic records (no domain content anywhere)
# --------------------------------------------------------------------------- #
COMPLETE = {"changed_files": ["pkg/mod.py"], "checks": "12 passed, 0 failed", "summary": "done"}
VACUOUS = {"changed_files": ["pkg/mod.py"], "checks": "not run", "summary": "done"}
NO_CLAIM = {"changed_files": ["pkg/mod.py"], "checks": "12 passed, 0 failed"}
NO_IMPL = {"changed_files": [], "checks": "12 passed, 0 failed", "summary": "done"}
EMPTY: dict = {}


def rec(status="succeeded", result=None, tools=None, max_tools=100, wall=None, max_wall=1000):
    record: dict = {"id": "0", "status": status, "budget": {}}
    if max_tools is not None:
        record["budget"]["max_tool_calls"] = max_tools
    if max_wall is not None:
        record["budget"]["max_wall_seconds"] = max_wall
    if result is not None:
        record["result"] = dict(result)
    if tools is not None:
        record.setdefault("result", {})["tool_calls"] = tools
    if wall is not None:
        start = datetime(2026, 11, 10, tzinfo=timezone.utc)
        record["created_at"] = start.isoformat()
        record["finished_at"] = (start + timedelta(seconds=wall)).isoformat()
    return record


MARK_BUDGET = dict(COMPLETE, fail_stage="budget_exhausted+forced_finish")
MARK_WALL = dict(COMPLETE, fail_stage="wall_exhausted+forced_finish")
MARK_PROGRESS = dict(COMPLETE, fail_stage="no_progress+forced_finish")

#: The 20 frozen scenarios: (id, description, record, expected state).
SCENARIOS: tuple[tuple[str, str, dict, str], ...] = (
    ("S01", "normal completion, budget normal", rec(result=COMPLETE, tools=10), "succeeded"),
    ("S02", "normal completion, budget at 75% (warning only)",
     rec(result=COMPLETE, tools=75), "succeeded"),
    ("S03", "complete artifacts but forced by budget marker",
     rec(result=MARK_BUDGET, tools=90), "forced_finish"),
    ("S04", "complete artifacts but forced by wall marker",
     rec(result=MARK_WALL, tools=40), "forced_finish"),
    ("S05", "complete artifacts but forced by no-progress marker",
     rec(result=MARK_PROGRESS, tools=40), "forced_finish"),
    ("S06", "no marker, budget arithmetically fully spent",
     rec(result=COMPLETE, tools=100), "forced_finish"),
    ("S07", "marker present at 75% utilization (marker wins over arithmetic)",
     rec(result=MARK_BUDGET, tools=75), "forced_finish"),
    ("S08", "verification present but not machine-checkable",
     rec(result=VACUOUS, tools=10), "degraded"),
    ("S09", "verification artifact absent entirely",
     rec(result=NO_CLAIM, tools=10), "degraded"),
    ("S10", "claim artifact absent",
     rec(result=NO_CLAIM, tools=10), "degraded"),
    ("S11", "empty result payload",
     rec(result=EMPTY, tools=10), "degraded"),
    ("S12", "result missing (None)",
     rec(result=None, tools=10), "degraded"),
    ("S13", "utilization 0.99 but no exhaustion (still normal completion)",
     rec(result=COMPLETE, tools=99), "succeeded"),
    ("S14", "storage failure dominates the budget marker",
     rec(status="failed", result=MARK_BUDGET, tools=100), "failed"),
    ("S15", "stopped storage status",
     rec(status="stopped", result=COMPLETE, tools=10), "failed"),
    ("S16", "revoked storage status",
     rec(status="revoked", result=COMPLETE, tools=10), "failed"),
    ("S17", "orphan storage status",
     rec(status="orphan", result=COMPLETE, tools=10), "failed"),
    ("S18", "wall arithmetic at 100% but no marker: warning only, not proof",
     rec(result=COMPLETE, tools=10, wall=86400, max_wall=86400), "succeeded"),
    ("S19", "no storage status at all (fail-closed)",
     {"id": "0", "result": COMPLETE}, "failed"),
    ("S20", "non-terminal status must raise, not be guessed",
     {"id": "0", "status": "queued", "result": COMPLETE}, "NonTerminalRecord"),
)

#: Confusion matrix over the 19 classifiable scenarios (S20 is an exception test).
CLASSIFIABLE = tuple(s for s in SCENARIOS if s[3] in STATES)


def classify(record: dict) -> str:
    try:
        return ts.classify_terminal_state(record).state
    except ts.NonTerminalRecord:
        return "NonTerminalRecord"


# --------------------------------------------------------------------------- #
# (a) four-state accuracy on the 20-scenario matrix
# --------------------------------------------------------------------------- #
def test_scenario_matrix_accuracy_is_1_00():
    wrong = [(sid, desc, classify(r), expected) for sid, desc, r, expected in SCENARIOS
             if classify(r) != expected]
    accuracy = (len(SCENARIOS) - len(wrong)) / len(SCENARIOS)
    assert accuracy == 1.0, f"misclassified: {wrong}"


def test_confusion_matrix_is_identity():
    matrix = {truth: {pred: 0 for pred in STATES} for truth in STATES}
    for _sid, _desc, record, expected in CLASSIFIABLE:
        matrix[expected][classify(record)] += 1
    off_diagonal = sum(v for truth in STATES for pred, v in matrix[truth].items() if pred != truth)
    assert off_diagonal == 0, matrix
    assert sum(sum(row.values()) for row in matrix.values()) == len(CLASSIFIABLE)


def test_forced_finish_is_never_succeeded():
    """The whole point of the split: a forced closure can never look like success."""
    forced = [r for _sid, _desc, r, expected in SCENARIOS if expected == "forced_finish"]
    assert forced, "matrix must contain forced_finish scenarios"
    for record in forced:
        assessment = ts.classify_terminal_state(record)
        assert assessment.state == "forced_finish"
        assert assessment.quality_gate == "fail"


def test_non_terminal_raises_unless_explicitly_allowed():
    with pytest.raises(ts.NonTerminalRecord):
        ts.classify_terminal_state({"status": "running", "result": COMPLETE})
    lenient = ts.classify_terminal_state({"status": "running", "result": COMPLETE},
                                        allow_non_terminal=True)
    assert lenient.state == "failed"


def test_precedence_table_matches_contract():
    assert ts.contract()["precedence"] == ["failed", "forced_finish", "degraded", "succeeded"]
    assert set(ts.TERMINAL_STATES) == set(STATES)


# --------------------------------------------------------------------------- #
# budget axis
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("tools,expected", [
    (0, "normal"), (69, "normal"), (70, "warning"), (99, "warning"), (100, "exhausted"),
])
def test_budget_status_thresholds(tools, expected):
    status, utilization = ts.budget_status(rec(result=COMPLETE, tools=tools))
    assert status == expected
    assert utilization == pytest.approx(tools / 100, abs=1e-4)


def test_contract_threshold_is_the_mvd_checkpoint_threshold():
    thresholds = ts.budget_thresholds()
    assert thresholds["warning"] == pytest.approx(mvd.BUDGET_THRESHOLD)
    assert thresholds["mvd_checkpoint"] == pytest.approx(mvd.BUDGET_THRESHOLD)
    assert thresholds["exhausted"] == 1.0


def test_warning_band_does_not_force_closure():
    """Crossing 70% is a signal, not a verdict: warning != forced_finish."""
    assert ts.classify_terminal_state(rec(result=COMPLETE, tools=75)).state == "succeeded"
    assert ts.budget_status(rec(result=COMPLETE, tools=75))[0] == "warning"


def test_wall_arithmetic_alone_is_never_exhaustion_evidence():
    """Measured on the real corpus: wall arithmetic has high recall but low precision.

    Queue wait and soft wall budgets sit inside ``finished_at - created_at``, so a
    span of 100% of the planned wall time lifts the record to ``warning`` only.
    """
    record = rec(result=COMPLETE, tools=10, wall=86400, max_wall=86400)
    dimensions = ts.budget_dimensions(record)
    assert dimensions["wall_at_cap"] is True
    assert dimensions["tool_at_cap"] is False
    assert dimensions["marker_exhaustion"] is False
    assert ts.budget_status(record)[0] == "warning"
    assert ts.classify_terminal_state(record).state == "succeeded"


def test_wall_marker_is_evidence_even_at_low_arithmetic():
    record = rec(result=COMPLETE, tools=10, wall=600, max_wall=86400)
    record["result"]["fail_stage"] = "wall_exhausted+forced_finish"
    assert ts.budget_status(record)[0] == "exhausted"
    assert ts.classify_terminal_state(record).state == "forced_finish"


def test_tool_cap_is_evidence_and_carries_the_dimension_breakdown():
    assessment = ts.classify_terminal_state(rec(result=COMPLETE, tools=100))
    assert assessment.state == "forced_finish"
    assert assessment.budget_evidence["tool_at_cap"] is True
    assert assessment.budget_evidence["marker_exhaustion"] is False


# --------------------------------------------------------------------------- #
# (d) delivery-label schema
# --------------------------------------------------------------------------- #
def test_every_scenario_label_validates_against_schema():
    labels = [ts.delivery_label(r, allow_non_terminal=True) for _s, _d, r, _e in SCENARIOS]
    errors = {i: ts.validate_delivery_label(label) for i, label in enumerate(labels)}
    assert all(not errs for errs in errors.values()), errors
    assert len(labels) == 20


def test_label_fields_are_orthogonal_and_bounded():
    for _sid, _desc, record, _expected in SCENARIOS:
        label = ts.delivery_label(record, allow_non_terminal=True)
        assert 0.0 <= label["completeness"] <= 1.0
        assert label["quality_gate"] in ("pass", "fail")
        assert label["budget_status"] in ("normal", "warning", "exhausted")
        assert label["terminal_state"] in STATES
        assert set(label["components"]) == {"implementation", "verification", "claim"}


@pytest.mark.parametrize("mutation", [
    {"terminal_state": "done"},                       # not in enum
    {"completeness": 1.5},                            # out of range
    {"completeness": -0.1},                           # out of range
    {"budget_status": "low"},                          # not in enum
    {"quality_gate": "ok"},                            # not in enum
])
def test_schema_rejects_malformed_labels(mutation):
    label = ts.delivery_label(rec(result=COMPLETE, tools=10))
    assert ts.validate_delivery_label(label) == []
    broken = dict(label, **mutation)
    assert ts.validate_delivery_label(broken), f"schema accepted {mutation}"


def test_schema_rejects_missing_and_extra_keys():
    label = ts.delivery_label(rec(result=COMPLETE, tools=10))
    missing = {k: v for k, v in label.items() if k != "components"}
    assert ts.validate_delivery_label(missing)
    assert ts.validate_delivery_label(dict(label, unexpected=1))


def test_fallback_validator_agrees_with_jsonschema():
    """The dependency-free path (used when jsonschema is absent) must not be laxer."""
    schema = ts.delivery_label_schema()
    labels = [ts.delivery_label(r, allow_non_terminal=True) for _s, _d, r, _e in SCENARIOS]
    labels += [dict(labels[0], completeness=2.0), dict(labels[0], quality_gate="ok"), {}]
    for label in labels:
        assert bool(ts.validate_delivery_label(label)) == bool(ts._validate_label_fallback(label, schema))


# --------------------------------------------------------------------------- #
# (b) 70% MVD checkpoint: trigger + latency
# --------------------------------------------------------------------------- #
def test_mvd_checkpoint_triggers_at_70_percent_not_before():
    below = ts.mvd_checkpoint_for_record(rec(result=COMPLETE, tools=69))
    at = ts.mvd_checkpoint_for_record(rec(result=COMPLETE, tools=70))
    assert below["triggered"] is False
    assert at["triggered"] is True
    assert at["passed"] is True


def test_mvd_checkpoint_catches_missing_mvd_elements():
    out = ts.mvd_checkpoint_for_record(rec(result=VACUOUS, tools=75))
    assert out["triggered"] is True
    assert out["passed"] is False
    assert "evidence_machine_checkable" in out["missing"]


def test_mvd_checkpoint_trigger_latency_under_one_second():
    records = [rec(result=COMPLETE if i % 2 else VACUOUS, tools=70 + (i % 30)) for i in range(500)]
    latencies_ms = []
    for record in records:
        t0 = time.perf_counter()
        out = ts.mvd_checkpoint_for_record(record)
        latencies_ms.append((time.perf_counter() - t0) * 1000.0)
        assert out["triggered"] is True
    latencies_ms.sort()
    p100 = latencies_ms[-1]
    assert p100 <= 1000.0, f"p100 latency {p100:.2f} ms exceeds the 1 s criterion"


# --------------------------------------------------------------------------- #
# (c) backward-compatibility migration, zero data loss
# --------------------------------------------------------------------------- #
def test_migration_preserves_every_original_key_and_value():
    legacy = [
        rec(result=COMPLETE, tools=10),                         # -> succeeded
        rec(result=MARK_BUDGET, tools=100),                     # -> forced_finish
        rec(result=MARK_WALL, tools=40),                        # -> forced_finish
        rec(result=VACUOUS, tools=10),                          # -> degraded
        rec(result=EMPTY, tools=10),                            # -> degraded
        rec(status="failed", result=MARK_BUDGET, tools=100),    # -> failed
    ]
    out = ts.migrate_legacy_succeeded(legacy)
    report = out["report"]
    assert report["records_in"] == report["records_out"] == len(legacy)
    assert report["zero_data_loss"] is True, report["violations"]
    assert report["violations"] == []
    assert sum(report["state_counts"].values()) == len(legacy)
    for before, after in zip(legacy, out["records"]):
        assert all(after[k] == v for k, v in before.items()), "original key/value changed"
        assert after["migration"]["legacy_status"] == before["status"]
        assert after["migration"]["from"] == before["status"]
    assert report["state_counts"] == {
        "succeeded": 1, "forced_finish": 2, "degraded": 2, "failed": 1,
    }


def test_migration_does_not_mutate_inputs():
    legacy = [rec(result=MARK_BUDGET, tools=100)]
    snapshot = json.dumps(legacy, sort_keys=True)
    ts.migrate_legacy_succeeded(legacy)
    assert json.dumps(legacy, sort_keys=True) == snapshot


def test_migration_rule_budget_exhausted_maps_to_forced_finish():
    """The declared backward-compat rule: legacy succeeded + budget exhausted -> forced_finish."""
    legacy = rec(result=COMPLETE, tools=100)
    migrated = ts.migrate_legacy_succeeded([legacy])["records"][0]
    assert migrated["migration"]["legacy_status"] == "succeeded"
    assert migrated["terminal_state"] == "forced_finish"
    assert migrated["delivery_label"]["budget_status"] == "exhausted"


def test_migration_handles_empty_and_malformed_input():
    for payload in ([], [None, {"status": "succeeded"}, {"no": "status"}]):
        report = ts.migrate_legacy_succeeded(payload)["report"]
        assert report["records_in"] == report["records_out"]
        assert report["zero_data_loss"] is True


@pytest.mark.skipif(not CORPUS.exists(), reason="frozen corpus not fetched in this worktree")
def test_migration_on_frozen_real_corpus_is_lossless():
    rows = json.loads(CORPUS.read_text(encoding="utf-8"))
    out = ts.migrate_legacy_succeeded(rows)
    report = out["report"]
    assert report["records_in"] == report["records_out"] == len(rows)
    assert report["zero_data_loss"] is True, report["violations"]
    assert sum(report["state_counts"].values()) == len(rows)
    labelled = [r for r in out["records"] if "delivery_label" in r]
    assert all(not ts.validate_delivery_label(r["delivery_label"]) for r in labelled)


# --------------------------------------------------------------------------- #
# KPI wiring: the delivery KPI must expose the four states, not just succeeded
# --------------------------------------------------------------------------- #
class _Row:
    def __init__(self, status, result, budget=None):
        self._mapping = {"id": "0", "status": status, "result": result, "budget": budget,
                         "created_at": None, "finished_at": None}


class _FakeResult:
    def __init__(self, rows):
        self._rows = rows

    def fetchall(self):
        return self._rows


class _FakeSession:
    def __init__(self, rows):
        self._rows = rows

    def execute(self, *args, **kwargs):
        return _FakeResult(self._rows)


def test_kpi_payload_exposes_four_state_denominators():
    from api.kpi_delivery import compute_delivery_kpis

    rows = [
        _Row("succeeded", COMPLETE, {"max_tool_calls": 100, "max_wall_seconds": 1000}),
        _Row("succeeded", dict(COMPLETE, fail_stage="budget_exhausted+forced_finish"),
             {"max_tool_calls": 100, "max_wall_seconds": 1000}),
        _Row("succeeded", VACUOUS, {"max_tool_calls": 100, "max_wall_seconds": 1000}),
        _Row("failed", {}, {"max_tool_calls": 100, "max_wall_seconds": 1000}),
    ]
    out = compute_delivery_kpis(_FakeSession(rows))
    states = out["terminal_states"]
    assert states["denominator_key"] == "terminal"
    assert states["denominator"] == 4
    assert states["counts"] == {"succeeded": 1, "forced_finish": 1, "degraded": 1, "failed": 1}
    assert out["rates"]["delivery_rate"] == 25.0
    assert out["rates"]["semantic_forced_finish_rate"] == 25.0
    assert out["rates"]["semantic_degraded_rate"] == 25.0
    # the legacy binary view is still emitted verbatim (backward compatibility)
    assert out["rates"]["success_rate"] == 75.0


def test_kpi_definitions_declare_the_semantic_denominator():
    from api.kpi_delivery import get_delivery_kpi_definitions

    defs = {d["key"]: d for d in get_delivery_kpi_definitions()}
    for key in ("delivery_rate", "semantic_forced_finish_rate", "semantic_degraded_rate"):
        assert defs[key]["denominator"] == "terminal"


# --------------------------------------------------------------------------- #
# design contract: zero domain semantics, no hidden state
# --------------------------------------------------------------------------- #
def test_module_has_zero_domain_semantics():
    hits = scan_domain_semantics(MODULE_PATH.read_text(encoding="utf-8"))
    assert hits == [], f"domain terms leaked into the generic module: {hits}"


def test_test_file_itself_is_domain_free():
    hits = scan_domain_semantics(pathlib.Path(__file__).read_text(encoding="utf-8"))
    assert hits == [], f"domain terms in the acceptance test: {hits}"


def test_classification_is_pure_and_deterministic():
    record = rec(result=MARK_BUDGET, tools=100)
    snapshot = json.dumps(record, sort_keys=True)
    first = ts.classify_terminal_state(record).to_dict()
    for _ in range(20):
        assert ts.classify_terminal_state(record).to_dict() == first
    assert json.dumps(record, sort_keys=True) == snapshot, "classifier mutated its input"


def test_summary_rates_are_defined_without_a_binary_success_flag():
    assessments = [ts.classify_terminal_state(r) for _s, _d, r, _e in SCENARIOS
                   if r.get("status") in STATES]
    summary = ts.summarize_states(assessments)
    assert summary["total"] == len(assessments)
    assert sum(summary["counts"].values()) == summary["total"]
    assert sum(summary["rates"].values()) == pytest.approx(1.0, abs=1e-6)
    assert "success_rate" not in summary, "the binary flag is exactly what this module replaces"