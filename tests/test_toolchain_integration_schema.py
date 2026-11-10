"""Spec validation: a malformed pipeline must die before any stage runs.

These tests target the *static* contract, because a spec error that only shows up
mid-run would produce a half-executed pipeline whose partial outputs look valid.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from tools.toolchain_integration import PipelineSpec, SpecError, build_plan

ROOT = Path(__file__).resolve().parents[1]
SHIPPED = ROOT / "tools" / "toolchain_integration" / "specs" / "synthetic_toolchain_v1.json"


def _base() -> dict:
    return {
        "schema": "tp.toolchain.pipeline/v1",
        "name": "unit_spec",
        "nodes": [
            {"id": "a", "adapter": "objective",
             "params": {"response": {"kind": "quadratic", "center": [0.0], "scale": [1.0]}}},
            {"id": "b", "adapter": "optimize",
             "inputs": {"objective": {"from": "a.f"}},
             "params": {"variables": [{"name": "u", "lower": -1.0, "upper": 1.0}],
                        "method": "DE", "max_iter": 2, "pop_size": 4}},
        ],
    }


def test_shipped_spec_loads_and_is_topologically_ordered():
    spec = PipelineSpec.load(SHIPPED)
    declared = [n.node_id for n in spec.nodes]
    assert spec.topological_order() == declared      # declaration order is valid
    assert len(spec.nodes) >= 8
    # and every edge points backwards in the declared order
    position = {nid: i for i, nid in enumerate(declared)}
    for node in spec.nodes:
        for binding in node.inputs.values():
            if binding.kind == "ref":
                assert position[binding.node] < position[node.node_id]


def test_unknown_adapter_is_rejected_at_plan_time():
    raw = _base()
    raw["nodes"][0]["adapter"] = "does_not_exist"
    spec = PipelineSpec.from_dict(raw)          # spec itself is structurally fine
    with pytest.raises(SpecError, match="unknown adapter"):
        build_plan(spec)


def test_duplicate_node_id_rejected():
    raw = _base()
    raw["nodes"][1]["id"] = "a"
    with pytest.raises(SpecError, match="duplicate node id"):
        PipelineSpec.from_dict(raw)


def test_forward_reference_rejected():
    raw = _base()
    raw["nodes"].reverse()                      # 'b' now references a later node
    with pytest.raises(SpecError, match="not declared earlier"):
        PipelineSpec.from_dict(raw)


def test_unknown_reference_target_rejected():
    raw = _base()
    raw["nodes"][1]["inputs"]["objective"] = {"from": "ghost.f"}
    with pytest.raises(SpecError, match="unknown node"):
        PipelineSpec.from_dict(raw)


def test_bad_reference_syntax_rejected():
    with pytest.raises(SpecError, match="node.port"):
        PipelineSpec.from_dict({
            "name": "x",
            "nodes": [{"id": "n", "adapter": "objective", "inputs": {"objective": {"from": "noport"}},
                       "params": {"response": {"kind": "linear", "coeff": [1.0]}}}],
        })


def test_missing_required_port_rejected():
    raw = _base()
    del raw["nodes"][1]["inputs"]["objective"]
    spec = PipelineSpec.from_dict(raw)
    with pytest.raises(SpecError, match="required input"):
        build_plan(spec)


def test_undeclared_input_port_rejected():
    raw = _base()
    raw["nodes"][1]["inputs"]["bogus"] = {"value": 1.0}
    spec = PipelineSpec.from_dict(raw)
    with pytest.raises(SpecError, match="no input port"):
        build_plan(spec)


def test_port_tag_mismatch_rejected():
    """A report cannot be fed into a scalar port: types are checked statically."""
    raw = {
        "name": "tag_mismatch",
        "nodes": [
            {"id": "probe", "adapter": "probe", "params": {"n": 64, "seed": 1}},
            {"id": "gate", "adapter": "conservation_gate",
             "inputs": {"src": {"from": "probe.scores"}, "dst": {"from": "probe.scores"}},
             "params": {}},
        ],
    }
    spec = PipelineSpec.from_dict(raw)
    with pytest.raises(SpecError, match="expects tag"):
        build_plan(spec)


def test_schema_version_mismatch_rejected():
    raw = _base()
    raw["schema"] = "tp.toolchain.pipeline/v0"
    with pytest.raises(SpecError, match="unsupported spec schema"):
        PipelineSpec.from_dict(raw)


def test_cycle_detection_direct():
    """``topological_order`` must report a cycle rather than loop forever.

    The public parser rejects cycles by construction (forward references are
    illegal), so the cycle is built directly out of the dataclasses -- otherwise
    the cycle branch would never be exercised.
    """
    from tools.toolchain_integration import Binding, NodeSpec

    a = NodeSpec(node_id="a", adapter="objective",
                 inputs={"f": Binding(kind="ref", node="b", port="passed")})
    b = NodeSpec(node_id="b", adapter="conservation_gate",
                 inputs={"src": Binding(kind="ref", node="a", port="f")})
    spec = PipelineSpec(name="cyclic", nodes=(a, b))
    with pytest.raises(SpecError, match="cycle"):
        spec.topological_order()


def test_digest_payload_ignores_execution_results():
    spec = PipelineSpec.load(SHIPPED)
    other = PipelineSpec.from_dict(json.loads(SHIPPED.read_text()))
    assert spec.digest_payload() == other.digest_payload()


def test_spec_roundtrip_is_stable():
    spec = PipelineSpec.load(SHIPPED)
    again = PipelineSpec.from_dict(copy.deepcopy(spec.to_dict()))
    assert again.digest_payload() == spec.digest_payload()