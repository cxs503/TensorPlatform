"""Engine-level tests: plan validation, the verdict matrix, determinism, evidence.

The verdict matrix below is the *acceptance table* of the capability: known-good
targets must PASS, every planted defect must FAIL, and a target that does not
report the required observable must be WITHHELD.  If a change flips any cell the
test fails, so sensitivity and specificity cannot silently regress.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from tools.invariant_audit.bundle import verify_bundle, write_bundle
from tools.invariant_audit.engine import (
    AuditPlan,
    PlanError,
    ledger_markdown,
    load_plan,
    load_plan_file,
    run_audit,
)
from tools.invariant_audit.model import InvariantState

REPO = Path(__file__).resolve().parents[2]
PLAN_PATH = REPO / "config" / "invariant_audit" / "plans" / "synthetic_solver_matrix.json"

EXPECTED_MATRIX = {
    ("synth-grid-flux-form", "mass-conservation"): InvariantState.PASS,
    ("synth-grid-flux-form", "boost-covariance"): InvariantState.PASS,
    ("synth-grid-flux-form-stretched", "mass-conservation"): InvariantState.PASS,
    ("synth-grid-interpolating", "mass-conservation"): InvariantState.FAIL,
    ("synth-grid-interpolating", "boost-covariance"): InvariantState.FAIL,
    ("synth-grid-unreported-flux", "mass-conservation"): InvariantState.WITHHELD,
    ("synth-response-isotropic", "rotate-30deg"): InvariantState.PASS,
    ("synth-response-isotropic", "reflect-x"): InvariantState.PASS,
    ("synth-response-grid-aligned", "rotate-30deg"): InvariantState.FAIL,
    ("synth-response-grid-aligned", "reflect-x"): InvariantState.PASS,
    ("synth-boost-heuristic", "boost-covariance"): InvariantState.FAIL,
    ("synth-postproc-ok", "force-dimension"): InvariantState.PASS,
    ("synth-postproc-ok", "force-relation"): InvariantState.PASS,
    ("synth-postproc-unit-bug", "force-dimension"): InvariantState.FAIL,
    ("synth-postproc-formula-bug", "force-relation"): InvariantState.FAIL,
    ("synth-postproc-formula-bug", "force-dimension"): InvariantState.PASS,
}


@pytest.fixture(scope="module")
def plan():
    if not PLAN_PATH.is_file():
        pytest.skip(f"plan not present in this checkout: {PLAN_PATH}")
    return load_plan_file(PLAN_PATH)


@pytest.fixture(scope="module")
def report(plan):
    return run_audit(plan)


def test_verdict_matrix_matches_acceptance_table(report):
    observed = {(target.target_id, inv.invariant_id): inv.state
                for target in report.targets for inv in target.invariants}
    assert observed == EXPECTED_MATRIX


def test_known_good_targets_all_pass(report):
    good = {"synth-grid-flux-form", "synth-grid-flux-form-stretched",
            "synth-response-isotropic", "synth-postproc-ok"}
    for target in report.targets:
        if target.target_id in good:
            assert target.state is InvariantState.PASS, target.to_dict()


def test_planted_defects_are_all_detected(report):
    planted = {
        "synth-grid-interpolating",
        "synth-response-grid-aligned",
        "synth-boost-heuristic",
        "synth-postproc-unit-bug",
        "synth-postproc-formula-bug",
    }
    flagged = {t.target_id for t in report.targets if t.state is InvariantState.FAIL}
    assert planted <= flagged
    # specificity: nothing outside the planted set may be flagged
    assert flagged == planted


def test_withheld_is_not_silently_converted(report):
    target = next(t for t in report.targets if t.target_id == "synth-grid-unreported-flux")
    assert target.state is InvariantState.WITHHELD
    inv = target.invariants[0]
    assert inv.state is InvariantState.WITHHELD
    assert inv.residual is None


def test_counts_are_consistent(report):
    counts = report.counts()
    pairs = report.pair_counts()
    assert sum(counts.values()) == len(report.targets)
    assert sum(pairs.values()) == len(EXPECTED_MATRIX)
    assert counts["PASS"] + counts["FAIL"] + counts["WITHHELD"] + counts["ERROR"] == \
        len(report.targets)
    assert report.plan_digest and len(report.plan_digest) == 64


def test_ledger_markdown_contains_every_pair(report):
    markdown = ledger_markdown(report)
    for target_id, invariant_id in EXPECTED_MATRIX:
        assert f"`{target_id}`" in markdown
        assert f"`{invariant_id}`" in markdown


# ---------------------------------------------------------------------------
# determinism + evidence
# ---------------------------------------------------------------------------
def _write(report, plan, tmp_path, name):
    out = tmp_path / name
    manifest = write_bundle(report, plan, out)
    return out, manifest


def test_two_runs_are_byte_identical(report, plan, tmp_path):
    out_a, manifest_a = _write(report, plan, tmp_path, "a")
    out_b, manifest_b = _write(report, plan, tmp_path, "b")
    assert manifest_a["root_digest"] == manifest_b["root_digest"]
    for rel in ("ledger.json", "LEDGER.md", "plan.json"):
        assert (out_a / rel).read_bytes() == (out_b / rel).read_bytes(), rel


def test_bundle_verifies_clean(report, plan, tmp_path):
    out, manifest = _write(report, plan, tmp_path, "clean")
    verdict = verify_bundle(out)
    assert verdict["ok"] is True, verdict
    assert verdict["checked_entries"] == len(manifest["entries"])


def test_bundle_detects_content_tampering(report, plan, tmp_path):
    out, _ = _write(report, plan, tmp_path, "tampered")
    ledger = out / "ledger.json"
    payload = json.loads(ledger.read_text(encoding="utf-8"))
    payload["counts"]["FAIL"] = 0  # pretend every defect passed
    ledger.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    verdict = verify_bundle(out)
    assert verdict["ok"] is False
    assert "CONTENT" in verdict["tamper_classes"]
    assert "ledger.json" in verdict["content_tampered"]


def test_bundle_detects_manifest_chain_editing(report, plan, tmp_path):
    """Editing a recorded digest without re-chaining the manifest must be caught."""
    out, manifest = _write(report, plan, tmp_path, "rechained")
    target_entry = next(e for e in manifest["entries"] if e["path"] == "ledger.json")
    target_entry["sha256"] = "0" * 64
    (out / "manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    verdict = verify_bundle(out)
    assert verdict["ok"] is False
    assert "CHAIN" in verdict["tamper_classes"]
    assert "ledger.json" in verdict["content_tampered"]


def test_bundle_detects_structural_tampering(report, plan, tmp_path):
    out, _ = _write(report, plan, tmp_path, "structural")
    (out / "targets" / "extra.json").write_text("{}\n", encoding="utf-8")
    verdict = verify_bundle(out)
    assert verdict["ok"] is False
    assert "STRUCTURAL" in verdict["tamper_classes"]


# ---------------------------------------------------------------------------
# fail-closed plan validation
# ---------------------------------------------------------------------------
def _minimal_target(**overrides):
    target = {
        "id": "t",
        "callable": "tools.invariant_audit.anchors.synthetic_models:postprocess_dimensions_ok",
        "config": {"mass": 2.0, "acceleration": 3.5, "duration": 1.5},
        "invariants": [
            {"id": "i", "kind": "relation",
             "params": {"quantity": "reported_force", "expression": "mass*acceleration"}}
        ],
    }
    target.update(overrides)
    return target


def _minimal_plan(**overrides):
    payload = {"schema_version": "invariant-audit/1.0", "plan_id": "p",
               "base_dimensions": ["length", "time"],
               "targets": [_minimal_target()]}
    payload.update(overrides)
    return payload


def test_minimal_plan_runs():
    plan = load_plan(_minimal_plan())
    report = run_audit(plan)
    assert report.targets[0].state is InvariantState.PASS


@pytest.mark.parametrize("payload", [
    {"schema_version": "other/9.9", "plan_id": "p", "base_dimensions": [], "targets": []},
    {"schema_version": "invariant-audit/1.0", "plan_id": "", "base_dimensions": [],
     "targets": []},
    {"schema_version": "invariant-audit/1.0", "plan_id": "p", "base_dimensions": [],
     "targets": []},
    _minimal_plan(targets=[_minimal_target(invariants=[])]),
    _minimal_plan(targets=[_minimal_target(callable="no_colon")]),
    _minimal_plan(targets=[_minimal_target(invariants=[{"id": "i", "kind": "unknown"}])]),
    _minimal_plan(base_dimensions=["length", "length"]),
    _minimal_plan(targets=[_minimal_target(), _minimal_target()]),
])
def test_plan_validation_is_fail_closed(payload):
    with pytest.raises(PlanError):
        load_plan(payload)


def test_unresolvable_callable_becomes_an_error_target():
    plan = load_plan(_minimal_plan(targets=[
        _minimal_target(callable="tools.invariant_audit.anchors.synthetic_models:no_such_fn")
    ]))
    report = run_audit(plan)
    assert report.targets[0].state is InvariantState.ERROR
    assert "callable_unresolved" in report.targets[0].error


# ---------------------------------------------------------------------------
# extension by configuration only (zero code change)
# ---------------------------------------------------------------------------
def test_new_target_joins_by_configuration_only():
    payload = _minimal_plan(targets=[_minimal_target()])
    payload["base_dimensions"] = ["length", "time", "unseen_axis"]
    payload["targets"].append({
        "id": "t2",
        "callable": "tools.invariant_audit.anchors.synthetic_models:displacement_exact",
        "config": {"velocity": 0.4, "duration": 2.0},
        "invariants": [
            {"id": "invariance", "kind": "covariance",
             "params": {"input_key": "velocity", "observable": "displacement",
                        "input_transform": {"offset": [0.1]},
                        "output_transform": {"offset": [0.2]}},
             "tolerance": {"rtol": 1e-12}}
        ],
    })
    plan = load_plan(payload)
    assert plan.base_dimensions[-1] == "unseen_axis"
    report = run_audit(plan)
    states = {t.target_id: t.state for t in report.targets}
    assert states == {"t": InvariantState.PASS, "t2": InvariantState.PASS}