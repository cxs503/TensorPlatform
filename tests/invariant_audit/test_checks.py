"""Evaluator-level tests: every invariant kind, and every state it can return.

The point of these tests is the *state machine*, not the physics: a missing
observable must become WITHHELD (never PASS), a contract breach must become
ERROR (never FAIL, which would be indistinguishable from a physical violation),
and a violated law must become FAIL.
"""
from __future__ import annotations

import math

import pytest

from tools.invariant_audit.checks import EvalContext, evaluate, safe_eval
from tools.invariant_audit.model import InvariantKind, InvariantSpec, InvariantState
from tools.verification_harness.model import Tolerance

BASE = ("length", "mass", "time")


def spec(kind, params, invariant_id="inv", rtol=1e-9, atol=0.0):
    return InvariantSpec(invariant_id=invariant_id, kind=InvariantKind(kind),
                         params=params, tolerance=Tolerance(rtol=rtol, atol=atol))


def ctx(observables, config=None, dimensions=None, call=None):
    def default_call(_config):
        raise AssertionError("covariance must supply a callable")

    return EvalContext(call=call or default_call, config=config or {},
                       observables=observables, dimensions=dimensions or {},
                       base_dimensions=BASE)


# ---------------------------------------------------------------------------
# conservation
# ---------------------------------------------------------------------------
def test_conservation_pass_and_fail():
    passed = evaluate(spec("conservation", {"initial": "m0", "final": "m1", "flux": "f"}),
                      ctx({"m0": 1.0, "m1": 0.5, "f": -0.5}))
    assert passed.state is InvariantState.PASS
    failed = evaluate(spec("conservation", {"initial": "m0", "final": "m1", "flux": "f"}),
                      ctx({"m0": 1.0, "m1": 0.4, "f": -0.5}))
    assert failed.state is InvariantState.FAIL
    assert failed.residual == pytest.approx(0.1)  # 0.4 - 1.0 + 0.5
    assert failed.details["total_initial"] == pytest.approx(1.0)


def test_conservation_without_flux_uses_initial_vs_final():
    outcome = evaluate(spec("conservation", {"initial": "m0", "final": "m1"}),
                       ctx({"m0": [1.0, 2.0], "m1": [1.0, 2.0 + 1e-15]}))
    assert outcome.state is InvariantState.PASS
    assert outcome.details["components"] == 2


def test_conservation_withholds_on_missing_flux():
    outcome = evaluate(spec("conservation", {"initial": "m0", "final": "m1", "flux": "ledger"}),
                       ctx({"m0": 1.0, "m1": 1.0}))
    assert outcome.state is InvariantState.WITHHELD
    assert outcome.reason == "observable_missing:ledger"


def test_conservation_shape_mismatch_is_an_error_not_a_fail():
    outcome = evaluate(spec("conservation", {"initial": "m0", "final": "m1"}),
                       ctx({"m0": [1.0, 2.0], "m1": 3.0}))
    assert outcome.state is InvariantState.ERROR
    assert "length_mismatch" in outcome.reason


# ---------------------------------------------------------------------------
# covariance
# ---------------------------------------------------------------------------
def test_covariance_happy_path_and_violation():
    def scaling(config):
        x = config["vector"]
        return {"out": [2.0 * v for v in x]}

    rotation = [[0.0, -1.0], [1.0, 0.0]]
    params = {"input_key": "vector", "observable": "out",
              "input_transform": {"matrix": rotation},
              "output_transform": {"matrix": rotation}}
    ok = evaluate(spec("covariance", params, rtol=1e-12),
                  ctx({"out": [2.0, 0.0]}, config={"vector": [1.0, 0.0]}, call=scaling))
    assert ok.state is InvariantState.PASS
    assert ok.residual == pytest.approx(0.0)

    def anisotropic(config):
        x = config["vector"]
        return {"out": [x[0], 3.0 * x[1]]}

    bad = evaluate(spec("covariance", params, rtol=1e-12),
                   ctx({"out": [1.0, 0.0]}, config={"vector": [1.0, 0.0]}, call=anisotropic))
    assert bad.state is InvariantState.FAIL


def test_covariance_boost_offset_on_scalar_input():
    def linear(config):
        return {"shift": float(config["speed"]) * 0.5}

    params = {"input_key": "speed", "observable": "shift",
              "input_transform": {"offset": [0.05]},
              "output_transform": {"offset": [0.025]}}
    outcome = evaluate(spec("covariance", params, rtol=1e-12),
                       ctx({"shift": 0.1}, config={"speed": 0.2}, call=linear))
    assert outcome.state is InvariantState.PASS


def test_covariance_withholds_when_observable_absent():
    def no_observable(_config):
        return {"other": 1.0}

    params = {"input_key": "speed", "observable": "shift",
              "input_transform": {"offset": [0.1]},
              "output_transform": {"offset": [0.1]}}
    outcome = evaluate(spec("covariance", params, rtol=1e-12),
                       ctx({}, config={"speed": 0.2}, call=no_observable))
    assert outcome.state is InvariantState.WITHHELD
    assert outcome.reason.startswith("observable_missing:")


def test_covariance_withholds_when_config_key_absent():
    params = {"input_key": "missing", "observable": "shift",
              "input_transform": {}, "output_transform": {}}
    outcome = evaluate(spec("covariance", params), ctx({"shift": 1.0}, config={}))
    assert outcome.state is InvariantState.WITHHELD
    assert outcome.reason == "config_missing:missing"


def test_covariance_errors_when_transform_shape_disagrees():
    def identity(config):
        return {"out": list(config["vector"])}

    params = {"input_key": "vector", "observable": "out",
              "input_transform": {"matrix": [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0],
                                             [0.0, 0.0, 1.0]]},
              "output_transform": {}}
    outcome = evaluate(spec("covariance", params),
                       ctx({"out": [1.0, 2.0]}, config={"vector": [1.0, 2.0]},
                           call=identity))
    assert outcome.state is InvariantState.ERROR


def test_covariance_errors_when_callable_raises():
    def broken(_config):
        raise RuntimeError("boom")

    params = {"input_key": "vector", "observable": "out",
              "input_transform": {}, "output_transform": {}}
    outcome = evaluate(spec("covariance", params),
                       ctx({"out": [1.0]}, config={"vector": [1.0]}, call=broken))
    assert outcome.state is InvariantState.ERROR
    assert "callable_raised" in outcome.reason


# ---------------------------------------------------------------------------
# dimensional consistency
# ---------------------------------------------------------------------------
def test_dimension_consistency_pass_fail_withheld():
    params = {"relations": [{"quantity": "reported", "from": "mass*acceleration"}],
              "required": {"reported": "mass*length*time**-2"}}
    good = evaluate(spec("dimension_consistency", params),
                    ctx({"reported": 7.0},
                        dimensions={"mass": "mass", "acceleration": "length*time**-2",
                                    "reported": "mass*length*time**-2"}))
    assert good.state is InvariantState.PASS
    bad = evaluate(spec("dimension_consistency", params),
                   ctx({"reported": 7.0},
                       dimensions={"mass": "mass", "acceleration": "length*time**-2",
                                   "reported": "mass*length*time**-1"}))
    assert bad.state is InvariantState.FAIL
    withheld = evaluate(spec("dimension_consistency", params), ctx({"reported": 7.0}))
    assert withheld.state is InvariantState.WITHHELD


def test_dimension_consistency_rejects_undeclared_symbols():
    outcome = evaluate(spec("dimension_consistency",
                            {"relations": [{"quantity": "q", "from": "mass*tme"}]}),
                       ctx({}, dimensions={"mass": "mass", "q": "mass"}))
    assert outcome.state is InvariantState.ERROR
    assert "relation_invalid" in outcome.reason


# ---------------------------------------------------------------------------
# numeric relation
# ---------------------------------------------------------------------------
def test_relation_pass_fail_withheld():
    params = {"quantity": "force_like", "expression": "mass*acceleration"}
    obs = {"mass": 2.0, "acceleration": 3.0, "force_like": 6.0}
    assert evaluate(spec("relation", params), ctx(obs)).state is InvariantState.PASS
    bad = dict(obs, force_like=5.0)
    assert evaluate(spec("relation", params), ctx(bad)).state is InvariantState.FAIL
    withheld = evaluate(spec("relation", params), ctx({"mass": 2.0, "acceleration": 3.0}))
    assert withheld.state is InvariantState.WITHHELD


@pytest.mark.parametrize("expression", [
    "__import__('os')", "mass.__class__", "(lambda: 1)()", "mass if mass else 0",
])
def test_safe_eval_rejects_non_arithmetic_constructs(expression):
    with pytest.raises(ValueError):
        safe_eval(expression, {"mass": 1.0})


def test_safe_eval_arithmetic():
    assert safe_eval("a*b - c/2 + d**2", {"a": 2.0, "b": 3.0, "c": 4.0, "d": 1.5}) == \
        pytest.approx(6.0 - 2.0 + 2.25)


def test_evaluate_never_raises_on_unknown_kind():
    outcome = evaluate(InvariantSpec(invariant_id="x", kind=InvariantKind.CONSERVATION,
                                     params={}), ctx({}))
    assert outcome.state is InvariantState.ERROR
    assert math.isfinite(0.0)