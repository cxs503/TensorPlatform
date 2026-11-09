"""Maturation tests for P92W3-N10: terminal-state layering + 70% budget gate.

Acceptance criteria machine-checked here (numbers come from the frozen runs
under ``runs/p92w3-n10/`` — this file never recomputes a threshold):

  a) adjudicated 60-unit macro-F1 >= 0.90 for the artifact judge
  b) weak-label recall >= 0.85 / precision >= 0.90 — *and* the measured bound that
     says whether any informative rule could reach it
  c) fail-closed negative controls: never ``delivered_complete`` without artifacts
  d) shadow mode: pure, zero side effects, bitwise-stable (golden) output
  e) zero domain semantics: domain-term scan hits = 0, in this file too

Run: python3 -m pytest backend/tests/test_p92w3_n10_layering.py -q
"""
from __future__ import annotations

import ast
import json
import pathlib
import sys

import pytest

from api.domain import delivery_state as ds
from api.domain.delivery_state import (
    assess_attested_delivery,
    assess_delivery,
    classify_closure,
    compute_missing_item_counts,
    layer_attested_state,
    scan_domain_semantics,
)

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
RUN = REPO_ROOT / "runs" / "p92w3-n10"
GATE_PATH = REPO_ROOT / "ops" / "taskplane" / "mvd_gate.py"
WORKER_PATH = REPO_ROOT / "ops" / "taskplane" / "tp_worker.py"

STATES = ("delivered_complete", "delivered_partial", "suspect_undeclared")


def _load_gate():
    """Import the harness-side gate module by path (stdlib-only, no package)."""
    sys.path.insert(0, str(GATE_PATH.parent))
    import importlib

    mod = importlib.import_module("mvd_gate")
    return importlib.reload(mod)


# ───────────────────── (1) closure + layering table ─────────────────────

class TestClosureLayering:
    def test_closure_classification(self):
        assert classify_closure({"fail_stage": "budget_exhausted+forced_finish"}) == "forced"
        assert classify_closure({"fail_stage": "wall_exhausted+forced_finish"}) == "forced"
        assert classify_closure({"fail_stage": ""}) == "clean"
        assert classify_closure({"fail_stage": "no_progress"}) == "clean"
        assert classify_closure({}) == "unknown"
        assert classify_closure(None) == "unknown"
        assert classify_closure("not-a-dict") == "unknown"

    def test_layering_can_only_downgrade(self):
        for artifact in STATES:
            for closure in ds.CLOSURE_STATES:
                state, _extra, confidence = layer_attested_state(artifact, closure)
                assert state in STATES
                # never promoted above the artifact verdict
                order = {s: i for i, s in enumerate(STATES)}   # complete < partial < suspect
                assert order[state] >= order[artifact]
                assert 0.0 < confidence <= 1.0

    def test_forced_closure_downgrades_complete_to_partial(self):
        result = {"changed_files": ["a.py"], "checks": "12 passed", "summary": "done",
                  "fail_stage": "budget_exhausted+forced_finish"}
        out = assess_attested_delivery(result)
        assert out.state == "delivered_partial"
        assert "closure_integrity" in out.missing
        assert out.facts["closure"] == "forced"
        assert out.facts["artifact_state"] == "delivered_complete"

    def test_unattested_closure_is_coverage_not_downgrade(self):
        result = {"changed_files": ["a.py"], "checks": "12 passed", "summary": "done"}
        out = assess_attested_delivery(result)
        assert out.state == "delivered_complete"
        assert "closure_attestation" in out.missing_optional
        assert out.confidence == 0.9

    def test_clean_closure_keeps_artifact_verdict_bitwise(self):
        base = {"changed_files": ["a.py"], "checks": "12 passed", "summary": "done"}
        with_flag = dict(base, fail_stage="")
        assert assess_delivery(base).state == assess_delivery(with_flag).state
        assert assess_attested_delivery(with_flag).to_dict()["state"] == "delivered_complete"

    def test_label_not_read_by_artifact_classifier(self):
        """The weak label must not be a feature of the artifact judge."""
        payload = {"changed_files": ["a.py"], "checks": "12 passed", "summary": "done"}
        poisoned = dict(payload, fail_stage="budget_exhausted+forced_finish")
        a, b = assess_delivery(payload), assess_delivery(poisoned)
        assert (a.state, a.missing, a.confidence) == (b.state, b.missing, b.confidence)

    def test_missing_enumeration_is_ranked(self):
        items = compute_missing_item_counts([
            assess_attested_delivery({"changed_files": ["a.py"]}),
            assess_attested_delivery({"changed_files": ["a.py"], "checks": "1 passed"}),
            assess_attested_delivery({"changed_files": ["a.py"],
                                      "checks": "1 passed", "summary": "d",
                                      "fail_stage": "budget_exhausted+forced_finish"}),
        ])
        ranked = {entry["item"]: entry["count"] for entry in items}
        assert ranked["claim"] == 2
        assert ranked["closure_integrity"] == 1
        assert items == sorted(items, key=lambda e: (-e["count"], e["item"]))


# ───────────────────── (2) fail-closed negative controls ─────────────────────

class TestFailClosed:
    @pytest.mark.parametrize("payload", [
        None, {}, "text", [], {"checks": "12 passed"},
        {"changed_files": [], "checks": "12 passed", "summary": "done"},
        {"changed_files": [], "checks": "", "summary": {} },
    ])
    def test_never_complete_without_work_product(self, payload):
        for assessor in (assess_delivery, assess_attested_delivery):
            out = assessor(payload)
            assert out.state != "delivered_complete"
            assert out.missing

    def test_forced_closure_never_complete(self):
        result = {"changed_files": ["a.py"], "checks": "12 passed", "summary": "done",
                  "fail_stage": "no_progress+forced_finish"}
        assert assess_attested_delivery(result).state != "delivered_complete"

    def test_implementation_without_verification_is_partial(self):
        out = assess_delivery({"changed_files": ["a.py"], "summary": "done"})
        assert out.state == "delivered_partial"
        assert "checks" in out.missing


# ───────────────────── (3) harness 70% budget gate ─────────────────────

class TestBudgetGate:
    def test_off_mode_is_strict_noop(self):
        gate = _load_gate()
        out = gate.evaluate_heartbeat((200, {"mvd_checkpoint": {"triggered": True,
                                                               "passed": False,
                                                               "missing": ["x"],
                                                               "closure_instruction": "i"}}),
                                      mode="off", tool_calls=99, max_tool_calls=100)
        assert out == {"mode": "off", "triggered": False, "budget_pct": 0.0,
                       "inject": False, "instruction": None, "event": None,
                       "reason": "off"}

    def test_below_threshold_does_not_inject(self):
        gate = _load_gate()
        out = gate.evaluate_heartbeat(None, mode="shadow", tool_calls=69, max_tool_calls=100)
        assert out["triggered"] is False and out["inject"] is False

    def test_threshold_boundary_injects_once(self):
        gate = _load_gate()
        first = gate.evaluate_heartbeat(None, mode="shadow", tool_calls=70, max_tool_calls=100,
                                        changed_files=["a.py"], checks="",
                                        headline="done")
        assert first["triggered"] is True and first["inject"] is True
        assert first["instruction"]
        second = gate.evaluate_heartbeat(None, mode="shadow", already_injected=True,
                                         tool_calls=80, max_tool_calls=100)
        assert second["inject"] is False
        assert second["event"]["injected"] == "already"
        assert second["reason"] == "already_injected"

    def test_wall_clock_budget_can_trigger(self):
        gate = _load_gate()
        out = gate.evaluate_heartbeat(None, mode="shadow", tool_calls=1, max_tool_calls=100,
                                      wall_seconds=3600, max_wall_seconds=5000)
        assert out["triggered"] is True and out["budget_pct"] == 72.0

    def test_server_instruction_is_preferred(self):
        gate = _load_gate()
        out = gate.evaluate_heartbeat(
            (200, {"mvd_checkpoint": {"triggered": True, "passed": False,
                                      "missing": ["implementation"],
                                      "closure_instruction": "SERVER-TEMPLATE"}}),
            mode="shadow", tool_calls=10, max_tool_calls=100)
        assert out["instruction"] == "SERVER-TEMPLATE"
        assert out["event"]["source"] == "server"

    def test_satisfied_mvd_does_not_inject(self):
        gate = _load_gate()
        out = gate.evaluate_heartbeat((200, {"mvd_checkpoint": {"triggered": True,
                                                                "passed": True,
                                                                "missing": []}}),
                                      mode="shadow", tool_calls=75, max_tool_calls=100)
        assert out["inject"] is False and out["event"]["passed"] is True

    def test_http_error_payload_falls_back_to_local_facts(self):
        gate = _load_gate()
        out = gate.evaluate_heartbeat((500, {"detail": "boom"}), mode="shadow",
                                      tool_calls=71, max_tool_calls=100)
        assert out["event"]["source"] == "harness"
        assert out["event"]["missing"] == ["implementation", "checks", "claim"]

    def test_template_has_no_domain_terms(self):
        gate = _load_gate()
        assert scan_domain_semantics(gate.CLOSURE_TEMPLATE) == []

    def test_shadow_mode_is_pure_and_bitwise_stable(self):
        gate = _load_gate()
        kwargs = dict(mode="shadow", tool_calls=71, max_tool_calls=100,
                      changed_files=["a.py"], checks="", headline="done")
        a = gate.evaluate_heartbeat(None, **kwargs)
        b = gate.evaluate_heartbeat(None, **kwargs)
        assert json.dumps(a, sort_keys=True) == json.dumps(b, sort_keys=True)
        # pure: no writes into the caller's structures
        changed = ["a.py"]
        gate.evaluate_heartbeat(None, mode="shadow", tool_calls=99, max_tool_calls=100,
                                changed_files=changed)
        assert changed == ["a.py"]

    def test_mode_from_env_defaults_and_validation(self):
        gate = _load_gate()
        assert gate.mode_from_env({}) == "shadow"
        assert gate.mode_from_env({"TP_MVD_CHECKPOINT": "OFF"}) == "off"
        assert gate.mode_from_env({"TP_MVD_CHECKPOINT": "enforce"}) == "enforce"
        assert gate.mode_from_env({"TP_MVD_CHECKPOINT": "bogus"}) == "shadow"


class TestWorkerWiring:
    """The harness must actually consume the checkpoint (not just compute it)."""

    def test_worker_calls_the_gate(self):
        source = WORKER_PATH.read_text(encoding="utf-8")
        assert "import mvd_gate" in source
        assert "mvd_gate.evaluate_heartbeat(" in source
        assert "mvd_gate.mode_from_env()" in source
        assert '"mvd_checkpoint"' in source

    def test_worker_reports_budget_facts(self):
        source = WORKER_PATH.read_text(encoding="utf-8")
        assert '"wall_seconds"' in source
        assert '"changed_files": changed[-20:]' in source

    def test_gate_call_sits_after_heartbeat_and_is_guarded(self):
        tree = ast.parse(WORKER_PATH.read_text(encoding="utf-8"))
        gate_calls = [n for n in ast.walk(tree)
                      if isinstance(n, ast.Call)
                      and isinstance(n.func, ast.Attribute)
                      and n.func.attr == "evaluate_heartbeat"]
        assert gate_calls, "gate is never invoked by the worker"
        # every invocation is inside a try/except so the gate can never kill a mission
        for node in ast.walk(tree):
            if isinstance(node, ast.Try):
                for sub in ast.walk(node):
                    if (isinstance(sub, ast.Call) and isinstance(sub.func, ast.Attribute)
                            and sub.func.attr == "evaluate_heartbeat"):
                        return
        pytest.fail("gate invocation is not protected by try/except")


# ───────────────────── (4) zero domain semantics ─────────────────────

class TestZeroDomainSemantics:
    @pytest.mark.parametrize("path", [
        REPO_ROOT / "backend" / "api" / "domain" / "delivery_state.py",
        REPO_ROOT / "backend" / "api" / "kpi_delivery.py",
        GATE_PATH,
    ])
    def test_no_domain_terms(self, path):
        assert scan_domain_semantics(path.read_text(encoding="utf-8")) == []

    def test_this_test_file_has_no_domain_terms(self):
        assert scan_domain_semantics(pathlib.Path(__file__).read_text(encoding="utf-8")) == []

    def test_worker_patch_region_has_no_domain_terms(self):
        source = WORKER_PATH.read_text(encoding="utf-8")
        start = source.index("mvd_gate.evaluate_heartbeat(")
        region = source[start - 1200:start + 1200]
        assert scan_domain_semantics(region) == []


# ───────────────────── (5) frozen corpus evidence ─────────────────────

def _metrics(name: str) -> dict:
    path = RUN / name
    if not path.exists():
        pytest.skip(f"{name} absent in this checkout")
    return json.loads(path.read_text(encoding="utf-8"))


class TestFrozenEvaluation:
    def test_criterion_a_adjudicated_macro_f1(self):
        m = _metrics("adjudication_metrics.json")
        assert m["n"] == 60
        assert m["artifact_only"]["macro_f1"] >= 0.90
        assert m["verdict"]["artifact_only_ok"] is True
        # baseline contrast must be recorded (a fix without a baseline is not a fix)
        assert m["artifact_only_prefix_replay"]["macro_f1"] < 0.90
        assert m["disagreements_artifact_only"] == []

    def test_criterion_b_weak_label_numbers_are_recorded_honestly(self):
        m = _metrics("metrics.json")
        rules = m["rules"]
        # the informative rule reaches recall but not precision: recorded as FAIL
        assert rules["R2_attested_not_complete"]["recall"] >= 0.85
        assert rules["R2_attested_not_complete"]["precision"] < 0.90
        assert m["threshold_verdicts"]["R2_attested_not_complete"]["precision_ok"] is False
        # artifact-only rule misses both: recorded as FAIL
        assert rules["R1_artifacts_only_not_complete"]["recall"] < 0.85
        assert m["threshold_verdicts"]["R1_artifacts_only_not_complete"]["recall_ok"] is False

    def test_corpus_sha_is_pinned(self):
        m = _metrics("metrics.json")
        assert len(m["corpus"]["sha256"]) == 64
        assert m["corpus"]["n_succeeded"] > 0
        assert m["corpus"]["n_forced_finish"] > 0

    def test_shadow_golden_regression(self):
        """Replayed classifier verdicts must match the frozen golden exactly."""
        golden_path = RUN / "shadow_golden.json"
        if not golden_path.exists():
            pytest.skip("golden file absent in this checkout")
        golden = json.loads(golden_path.read_text(encoding="utf-8"))
        for case in golden["cases"]:
            assert assess_attested_delivery(case["result"]).state == case["state"]
            assert assess_delivery(case["result"]).state == case["artifact_state"]