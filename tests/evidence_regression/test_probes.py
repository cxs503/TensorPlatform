"""Probe contracts: each kind must fail on the degradation it exists to catch."""
from __future__ import annotations

import pytest

from tools.evidence_regression import probes
from tools.evidence_regression.model import ProbeSpec
from tools.evidence_regression.targets import ResolvedTarget, TargetSpec


def target(ref: str, kwargs: dict, field=None) -> ResolvedTarget:
    spec = TargetSpec(steps=({"ref": ref, "kwargs": kwargs},), field=field)
    return ResolvedTarget(spec, sys_paths=("tests/evidence_regression",))


def run(kind: str, call, **params):
    return probes.run_probe(ProbeSpec(kind=kind, params=params), call, "cap.test")


def test_anchor_passes_on_analytic_value():
    check = run("anchor", target("synthetic_lab:linear", {"x": 2.0, "k": 2.0}),
                cases=[{"kwargs": {"x": 2.0, "k": 2.0}, "expect": 4.0, "tol_rel": 1e-12}])
    assert check.passed is True
    assert check.rel_error == pytest.approx(0.0, abs=1e-15)


def test_anchor_detects_deviation_beyond_tolerance():
    check = run("anchor", target("synthetic_lab:linear", {"x": 2.0, "k": 2.0}),
                cases=[{"kwargs": {"x": 2.0, "k": 2.0}, "expect": 4.0, "tol_rel": 1e-6}],
                )
    assert check.passed is True
    bad = run("anchor", target("synthetic_lab:constant", {"x": 2.0}),
              cases=[{"kwargs": {"x": 2.0}, "expect": 4.0, "tol_rel": 1e-6}])
    assert bad.passed is False and bad.rel_error > 1.0


def test_scale_law_detects_wrong_exponent():
    call = target("synthetic_lab:quadratic", {"x": 1.0})
    good = run("scale_law", call, base={"x": 1.0}, axis="x", factor=2.0, exponent=2.0, tol_rel=1e-12)
    bad = run("scale_law", call, base={"x": 1.0}, axis="x", factor=2.0, exponent=1.0, tol_rel=1e-12)
    assert good.passed is True and bad.passed is False
    assert bad.rel_error == pytest.approx(1.0, rel=1e-9)


def test_monotonic_detects_direction_violation():
    call = target("synthetic_lab:linear", {"x": 1.0})
    good = run("monotonic", call, base={}, axis="x", values=[1.0, 2.0, 3.0], direction="increasing")
    bad = run("monotonic", call, base={}, axis="x", values=[1.0, 2.0, 3.0], direction="decreasing")
    assert good.passed is True and bad.passed is False


def test_nontriviality_detects_constant_stub():
    call = target("synthetic_lab:constant", {"x": 1.0})
    check = run("nontriviality", call, cases=[{"kwargs": {"x": 1.0}}, {"kwargs": {"x": 9.0}}],
                min_rel_spread=0.1)
    assert check.passed is False
    healthy = run("nontriviality", target("synthetic_lab:linear", {"x": 1.0}),
                  cases=[{"kwargs": {"x": 1.0}}, {"kwargs": {"x": 9.0}}], min_rel_spread=0.1)
    assert healthy.passed is True


def test_determinism_detects_unseeded_random():
    check = run("determinism", target("synthetic_lab:noisy", {"x": 2.0}),
                cases=[{"kwargs": {"x": 2.0}}, {"kwargs": {"x": 3.0}}])
    assert check.passed is False
    assert "mismatch" in check.detail


def test_bounded_detects_out_of_range_observation():
    check = run("bounded", target("synthetic_lab:constant", {"x": 1.0}),
                cases=[{"kwargs": {"x": 1.0}}], lo=0.0, hi=1.0)
    assert check.passed is False
    inside = run("bounded", target("synthetic_lab:banded", {"x": 0.0}),
                 cases=[{"kwargs": {"x": 0.0}}, {"kwargs": {"x": 1.0}}], lo=0.0, hi=1.0)
    assert inside.passed is True


def test_probe_failure_is_recorded_not_raised():
    """A broken call chain is evidence (failed probe), never an exception out of the registry."""
    call = target("synthetic_lab:linear", {"x": 2.0}, field="does_not_exist")
    check = run("anchor", call, cases=[{"kwargs": {"x": 2.0}, "expect": 4.0}])
    assert check.passed is False
    assert "TargetError" in check.detail or "missing" in check.detail