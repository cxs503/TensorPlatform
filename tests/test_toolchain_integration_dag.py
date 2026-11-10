"""Execution semantics: fail-closed degradation, evidence roll-up, reproducibility.

These are the properties that make a green run *mean* something:

* a stage whose backing module is missing yields WITHHELD and no numbers;
* downstream stages are skipped rather than fed a substituted value;
* a stage that ran through its declared fallback is recorded MOCK even though the
  adapter itself is REAL;
* the run digest is reproducible and order-sensitive, and timing never enters it.
"""

from __future__ import annotations

import numpy as np
import pytest

from tools.toolchain_integration import (
    Binding,
    ExecutionContext,
    NodeSpec,
    PipelineSpec,
    SpecError,
    catalogue,
)
from tools.toolchain_integration import provenance
from tools.toolchain_integration.dag import (
    STATUS_FAIL,
    STATUS_OK,
    STATUS_SKIPPED,
    build_plan,
    run_pipeline,
)

LINEAR = {"kind": "linear", "coeff": [1.0, 2.0], "offset": 0.5}


def _objective_nodes():
    return [
        NodeSpec(node_id="surface", adapter="objective", params={"response": LINEAR}),
        NodeSpec(
            node_id="design",
            adapter="optimize",
            inputs={"objective": Binding(kind="ref", node="surface", port="f")},
            params={
                "variables": [{"name": "u", "lower": -1.0, "upper": 1.0},
                              {"name": "v", "lower": -1.0, "upper": 1.0}],
                "method": "DE", "max_iter": 3, "pop_size": 6,
            },
        ),
    ]


def test_registry_contracts_are_declared():
    cat = catalogue()
    for kind in ("objective", "optimize", "uq", "calibrate_prior", "remap",
                 "conservation_gate", "external", "audit"):
        assert kind in cat, kind
        assert cat[kind]["evidence"] in ("REAL", "MOCK", "STUB", "WITHHELD", "NOT_SUPPORTED")


def test_pipeline_runs_and_is_reproducible():
    spec = PipelineSpec(name="linear_design", nodes=tuple(_objective_nodes()))
    a = run_pipeline(spec, ExecutionContext(seed=1))
    b = run_pipeline(spec, ExecutionContext(seed=1))
    assert a.status == STATUS_OK
    assert a.chain_digest == b.chain_digest
    assert a.value("design.f_best").value == pytest.approx(b.value("design.f_best").value)


def test_broken_order_is_rejected_statically():
    """Execution order is not negotiable: a forward reference dies at plan time."""
    nodes = _objective_nodes()
    forward = run_pipeline(PipelineSpec(name="p", nodes=tuple(nodes)))
    assert forward.status == STATUS_OK
    with pytest.raises(SpecError, match="unknown node"):
        run_pipeline(PipelineSpec(name="p", nodes=(nodes[1], nodes[0])))


def test_chain_digest_is_order_and_value_sensitive():
    spec = PipelineSpec(name="p", nodes=tuple(_objective_nodes()))
    other = PipelineSpec(
        name="p",
        nodes=(
            NodeSpec(node_id="surface", adapter="objective",
                     params={"response": {"kind": "linear", "coeff": [2.0, 1.0], "offset": 0.5}}),
            _objective_nodes()[1],
        ),
    )
    a = run_pipeline(spec)
    b = run_pipeline(spec)
    c = run_pipeline(other)
    assert a.chain_digest == b.chain_digest          # reproducible
    assert a.chain_digest != c.chain_digest          # content-sensitive


def test_missing_backing_module_degrades_to_withheld():
    """A stage whose module does not exist must not fabricate a value."""
    spec = PipelineSpec(name="absent", nodes=(
        NodeSpec(node_id="ghost", adapter="external",
                 params={"module": "no_such_module_xyz", "attr": "f", "args": [1.0]}),
    ))
    run = run_pipeline(spec)
    rec = run.record("ghost")
    assert rec.status == STATUS_FAIL
    assert rec.evidence == "WITHHELD"
    assert rec.error.startswith("unavailable")
    assert "ghost" not in run.values            # no value was produced
    assert run.evidence == "WITHHELD"


def test_downstream_of_failed_stage_is_skipped_not_faked():
    spec = PipelineSpec(name="skip", nodes=(
        NodeSpec(node_id="ghost", adapter="external",
                 params={"module": "no_such_module_xyz", "attr": "f", "args": [1.0]}),
        NodeSpec(node_id="rollup", adapter="audit",
                 inputs={"x": Binding(kind="ref", node="ghost", port="value")},
                 params={"criteria": [{"name": "c", "input": "x", "op": "ge", "threshold": 0.0}]}),
    ))
    run = run_pipeline(spec)
    assert run.record("ghost").status == STATUS_FAIL
    assert run.record("rollup").status == STATUS_SKIPPED
    assert run.status == STATUS_FAIL
    assert "rollup" not in run.values


def test_optional_node_failure_is_degraded_and_does_not_fail_the_run():
    spec = PipelineSpec(name="optional", nodes=(
        NodeSpec(node_id="ghost", adapter="external", required=False,
                 params={"module": "no_such_module_xyz", "attr": "f", "args": [1.0]}),
        NodeSpec(node_id="surface", adapter="objective", params={"response": LINEAR}),
    ))
    run = run_pipeline(spec)
    assert run.record("ghost").status == "DEGRADED"
    assert run.record("surface").status == STATUS_OK
    assert run.status == "DEGRADED"


def test_declared_fallback_is_recorded_as_mock_and_propagates():
    spec = PipelineSpec(name="fallback", nodes=(
        NodeSpec(node_id="ghost", adapter="external",
                 params={"module": "no_such_module_xyz", "attr": "f",
                         "args": [2.0],
                         "substitute": {"kind": "linear", "coeff": [1.0], "offset": 0.0}}),
        NodeSpec(node_id="rollup", adapter="audit",
                 inputs={"x": Binding(kind="ref", node="ghost", port="value")},
                 params={"criteria": [{"name": "positive", "input": "x", "op": "ge",
                                       "threshold": 0.0}]}),
    ))
    run = run_pipeline(spec)
    rec = run.record("ghost")
    assert rec.status == STATUS_OK
    assert rec.evidence == "MOCK", "a substituted stage must never be reported REAL"
    assert rec.notes and "fallback" in rec.notes[0]
    assert run.value("ghost.value").value == pytest.approx(2.0)
    # the advisory criterion still passes, but the run's evidence has been downgraded
    assert run.value("rollup.verdict").value is True
    assert run.evidence == "MOCK"


def test_present_module_is_reported_available():
    spec = PipelineSpec(name="present", nodes=(
        NodeSpec(node_id="probe", adapter="external",
                 params={"module": "math", "attr": "sqrt", "args": [9.0]}),
    ))
    run = run_pipeline(spec)
    assert run.value("probe.available").value is True
    assert run.value("probe.value").value == pytest.approx(3.0)
    assert run.evidence == "REAL"


def test_timing_is_excluded_from_digest():
    a = {"value": 1.5, "wall_s": 0.001}
    b = {"value": 1.5, "wall_s": 9.9}
    assert provenance.digest(a) == provenance.digest(b)
    assert provenance.digest({"v": 1.0}) != provenance.digest({"v": 2.0})


def test_callable_digest_is_address_free():
    from tools.toolchain_integration.adapters import build_response

    f1 = build_response(LINEAR)
    f2 = build_response(LINEAR)
    assert f1 is not f2
    assert provenance.digest(f1) == provenance.digest(f2)
    g = build_response({"kind": "linear", "coeff": [1.0, 3.0], "offset": 0.5})
    assert provenance.digest(g) != provenance.digest(f1)


def test_array_digest_detects_reshaping():
    a = np.zeros(6)
    b = np.zeros((2, 3))
    assert provenance.digest(a) != provenance.digest(b)


def test_adapter_results_must_be_tagged_port_values(monkeypatch):
    """A misbehaving adapter (raw dict instead of PortValue) fails loudly."""
    from tools.toolchain_integration import registry as registry_mod
    from tools.toolchain_integration.dag import PipelineError

    bad = registry_mod.Adapter(
        kind="bad_shape",
        summary="returns untagged values",
        inputs=(),
        outputs=(),
        run=lambda ctx, params, inputs: {"v": 1.0},
        evidence="REAL",
    )
    monkeypatch.setitem(registry_mod._REGISTRY, "bad_shape", bad)
    spec = PipelineSpec(name="bad", nodes=(NodeSpec(node_id="x", adapter="bad_shape"),))
    with pytest.raises(PipelineError, match="expected PortValue"):
        run_pipeline(spec)


def test_plan_rejects_unknown_adapter_before_execution():
    spec = PipelineSpec(name="bad", nodes=(
        NodeSpec(node_id="x", adapter="nope"),))
    with pytest.raises(SpecError):
        build_plan(spec)


def test_audit_fails_closed_on_missing_port():
    spec = PipelineSpec(name="audit_gap", nodes=(
        NodeSpec(node_id="rollup", adapter="audit",
                 inputs={"x": Binding(kind="ref", node="surface", port="f")},
                 params={"criteria": [
                     {"name": "uses_series", "input": "x", "op": "ge", "threshold": 0.0},
                     {"name": "never_bound", "input": "y", "op": "ge", "threshold": 0.0},
                 ]}),
        NodeSpec(node_id="surface", adapter="objective", params={"response": LINEAR}),
    ))
    # spec validation catches the forward reference first
    with pytest.raises(SpecError):
        build_plan(spec)

    ok = PipelineSpec(name="audit_gap2", nodes=(
        NodeSpec(node_id="probe", adapter="probe", params={"n": 32, "seed": 2}),
        NodeSpec(node_id="rollup", adapter="audit",
                 inputs={"scores": Binding(kind="ref", node="probe", port="scores")},
                 params={"criteria": [
                     {"name": "never_bound", "input": "absent_port", "op": "ge", "threshold": 0.0},
                 ]}),
    ))
    run = run_pipeline(ok)
    status = run.value("rollup.status").value
    assert status["verdict"] == "WITHHELD"
    assert run.value("rollup.verdict").value is False


def test_informational_criteria_are_reported_but_excluded():
    spec = PipelineSpec(name="info", nodes=(
        NodeSpec(node_id="surface", adapter="objective", params={"response": LINEAR}),
        NodeSpec(node_id="rollup", adapter="audit",
                 inputs={"f": Binding(kind="ref", node="surface", port="f")},
                 params={"criteria": [
                     {"name": "advisory", "input": "f", "op": "eq_false", "informational": True},
                 ]}),
    ))
    run = run_pipeline(spec)
    status = run.value("rollup.status").value
    assert status["verdict"] == "PASS"
    assert status["n_fail"] == 0 and status["n_info"] == 1