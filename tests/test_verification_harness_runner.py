"""Runner tests: execution, error math (hand-checked), determinism, baseline references.

Criterion A2 is pinned here: the harness' relative error is recomputed by hand in
the test and must agree to <= 0.1% relative deviation.
"""
from __future__ import annotations

import json
import math
from pathlib import Path

import pytest

from tools.verification_harness.model import (
    SCHEMA_VERSION,
    CaseSpec,
    Metric,
    Tolerance,
    Verdict,
    absolute_error,
    observed_order,
    relative_error,
)
from tools.verification_harness.registry import CaseRegistry
from tools.verification_harness.runner import (
    error_table_markdown,
    run_case,
    run_registry,
    write_run_report,
)

REPO = Path(__file__).resolve().parents[1]
CASE_DIR = REPO / "tools" / "verification_harness" / "cases"


# ---------------------------------------------------------------------------
# Synthetic fixtures (pure code, no filesystem)
# ---------------------------------------------------------------------------
def linear_impl(slope: float, intercept: float, x: float) -> dict:
    return {"y": slope * x + intercept, "slope": slope}


def linear_ref(slope: float, intercept: float, x: float) -> dict:
    return {"y": slope * x + intercept, "slope": slope}


def _spec(case_id="synth-inline", quantities=None, inputs=None,
          implementation="test_verification_harness_runner:linear_impl",
          reference="test_verification_harness_runner:linear_ref"):
    return CaseSpec(
        case_id=case_id,
        implementation=implementation,
        reference=reference,
        quantities=quantities or (("y", Tolerance(rtol=1e-12)),),
        domain="synthetic-inline",
        inputs=inputs or {"slope": 2.0, "intercept": 1.0, "x": 3.0},
    )


ANCHOR_CASES = {
    "synth-parabolic-form-hydrostatics": {"volume", "waterplane_area", "block_coefficient"},
    "synth-damped-oscillator": {"x_end", "v_end"},
    "synth-slab-conduction": {"t_mid", "t_quarter"},
    "synth-algebraic-law": {"friction_factor"},
    "synth-tridiagonal-spectrum": {"spectral_radius"},
    "synth-convergence-order": {"observed_order"},
}


# ---------------------------------------------------------------------------
# Error convention (A2)
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("actual,expected", [
    (1.0, 1.0),
    (1.0002, 1.0),
    (0.9998, 1.0),
    (-3.5, -3.0),
    (1.23456e6, 1.23400e6),
    (1e-9, 3e-9),
    (0.0, 2.0),
])
def test_relative_error_matches_hand_computation(actual, expected):
    harness_value = relative_error(actual, expected)
    hand_value = abs(actual - expected) / abs(expected)
    assert harness_value == pytest.approx(hand_value, rel=1e-12, abs=0.0)
    assert absolute_error(actual, expected) == pytest.approx(abs(actual - expected), rel=0, abs=0)


def test_metric_error_fields_agree_with_hand_calculation_within_0p1pct():
    """A2: harness errors vs an independently typed formula, <= 0.1% relative deviation."""
    rows = [(12.345, 12.300), (0.5001234, 0.5), (-7.25, -7.0), (104.0, 100.0), (3.0e-7, 3.1e-7)]
    for actual, expected in rows:
        metric = Metric.compare("quantity", actual, expected, Tolerance(rtol=1e-3))
        hand_abs = abs(actual - expected)
        hand_rel = abs(actual - expected) / abs(expected)
        for harness_value, hand_value in ((metric.abs_error, hand_abs), (metric.rel_error, hand_rel)):
            deviation = abs(harness_value - hand_value) / hand_value
            assert deviation <= 1e-3, (actual, expected, harness_value, hand_value, deviation)


def test_relative_error_is_undefined_against_zero():
    assert relative_error(0.5, 0.0) is None
    metric = Metric.compare("q", 0.5, 0.0, Tolerance(rtol=0.1, atol=0.01))
    assert metric.rel_error is None
    assert metric.verdict is Verdict.FAIL
    # only atol can rescue a zero expectation
    assert Metric.compare("q", 0.005, 0.0, Tolerance(rtol=0.0, atol=0.01)).verdict is Verdict.PASS


def test_observed_order_helper():
    assert observed_order([0.25, 0.5], [1.0, 2.0], 2.0) == pytest.approx(2.0)
    with pytest.raises(ValueError):
        observed_order([0.0], [1.0], 2.0)


# ---------------------------------------------------------------------------
# Execution
# ---------------------------------------------------------------------------
def test_run_case_pass_records_metrics():
    result = run_case(_spec())
    assert result.verdict is Verdict.PASS
    assert [m.name for m in result.metrics] == ["y"]
    assert result.outputs["slope"] == 2.0
    assert result.worst_rel_error == 0.0


def test_run_case_fail_carries_error_table_fields():
    spec = CaseSpec(case_id="synth-inline-fail",
                    implementation="test_verification_harness_runner:linear_impl",
                    reference="test_verification_harness_runner:linear_ref",
                    quantities=(("slope", Tolerance(rtol=1e-9)),),
                    inputs={"slope": 2.0, "intercept": 1.0, "x": 3.0})
    result = run_case(spec)
    assert result.verdict is Verdict.PASS  # identical routes agree exactly

    drifted = CaseSpec(case_id="synth-inline-drift",
                       implementation="test_verification_harness_runner:linear_impl",
                       reference="test_verification_harness_runner:linear_ref_drift",
                       quantities=(("slope", Tolerance(rtol=1e-3, atol=0.0)),),
                       inputs={"slope": 2.0, "intercept": 1.0, "x": 3.0})
    failed = run_case(drifted)
    assert failed.verdict is Verdict.FAIL
    metric = failed.metrics[0]
    # rel_error is normalised by |expected|: |2 - 2.02| / 2.02
    assert metric.rel_error == pytest.approx(0.01 / 1.01, rel=1e-12)
    assert metric.verdict is Verdict.FAIL


def linear_ref_drift(slope: float, intercept: float, x: float) -> dict:
    return {"y": slope * x + intercept, "slope": slope * 1.01}


def exploding_impl(slope: float, intercept: float, x: float) -> dict:
    raise RuntimeError("synthetic blow-up")


def missing_quantity_impl(slope: float, intercept: float, x: float) -> dict:
    return {"unrelated": 1.0}


def test_run_case_turns_exceptions_into_error_verdicts():
    for implementation in ("exploding_impl", "missing_quantity_impl"):
        spec = _spec(implementation=f"test_verification_harness_runner:{implementation}")
        result = run_case(spec)
        assert result.verdict is Verdict.ERROR
        assert result.error and result.error_type
        assert len(result.metrics) == 0


def test_run_case_reports_unresolvable_callable():
    result = run_case(_spec(implementation="no_such_package_xyz:thing"))
    assert result.verdict is Verdict.ERROR
    assert result.error_type == "CallableResolutionError"


def test_run_registry_over_anchor_package():
    registry = CaseRegistry()
    registry.discover(CASE_DIR)
    report = run_registry(registry)
    assert report.ok, [(r.case_id, r.verdict.value, r.error) for r in report.results]
    counts = report.counts
    assert counts["total"] == len(registry) >= 5
    assert counts["PASS"] == counts["total"]
    assert counts["quantities"] == sum(len(v) for v in ANCHOR_CASES.values())
    assert set(report.domains) == {registry.get(cid).domain for cid in registry.case_ids}


def test_run_report_is_byte_identical_across_runs(tmp_path):
    registry = CaseRegistry()
    registry.discover(CASE_DIR)
    paths = []
    for name in ("a", "b"):
        report = run_registry(registry)
        written = write_run_report(report, tmp_path / name)
        paths.append(written["report"].read_bytes())
    assert paths[0] == paths[1], "report.json must be deterministic (evidence chain depends on it)"
    payload = json.loads(paths[0])
    assert payload["schema_version"] == SCHEMA_VERSION
    assert payload["ok"] is True
    assert len(payload["error_table"]) == payload["counts"]["quantities"]


def test_error_table_markdown_contains_every_quantity():
    registry = CaseRegistry()
    registry.discover(CASE_DIR)
    report = run_registry(registry)
    table = error_table_markdown(report)
    for spec in registry:
        for name in spec.quantity_names:
            assert f"| {spec.case_id} | {name} |" in table


def read_baseline_artifact(path: str, **_: object) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def linear_impl_with_path(slope: float, intercept: float, x: float, path: str) -> dict:
    return linear_impl(slope, intercept, x)


def test_baseline_reference_kind(tmp_path):
    """A baseline case compares against a recorded artifact instead of a formula."""
    baseline = tmp_path / "baseline.json"
    baseline.write_text(json.dumps({"y": 7.0}), encoding="utf-8")
    inputs = {"slope": 2.0, "intercept": 1.0, "x": 3.0, "path": str(baseline)}

    # surplus keyword rejected by the implementation -> honest ERROR, not a silent pass
    strict = CaseSpec(case_id="synth-baseline-strict",
                      implementation="test_verification_harness_runner:linear_impl",
                      reference="test_verification_harness_runner:read_baseline_artifact",
                      quantities=(("y", Tolerance(rtol=1e-12)),),
                      reference_kind="baseline", inputs=inputs)
    assert run_case(strict).verdict is Verdict.ERROR

    spec = CaseSpec(case_id="synth-baseline-ok",
                    implementation="test_verification_harness_runner:linear_impl_with_path",
                    reference="test_verification_harness_runner:read_baseline_artifact",
                    quantities=(("y", Tolerance(rtol=1e-12)),),
                    reference_kind="baseline", inputs=inputs)
    result = run_case(spec)
    assert result.verdict is Verdict.PASS
    assert result.outputs["y"] == 7.0
    assert result.expected["y"] == 7.0

    baseline.write_text(json.dumps({"y": 7.5}), encoding="utf-8")
    drifted = run_case(spec)
    assert drifted.verdict is Verdict.FAIL
    assert drifted.metrics[0].rel_error == pytest.approx(0.5 / 7.5, rel=1e-12)


def test_tolerance_band_boundary():
    tolerance = Tolerance(rtol=1e-3, atol=1e-6)
    assert tolerance.accepts(1.0 + 1e-9, 1.0)
    assert not tolerance.accepts(1.0 + 1e-2, 1.0)
    assert math.isclose(Tolerance(rtol=0.0, atol=2.0).atol, 2.0)