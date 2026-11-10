"""End-to-end integration test of the shipped synthetic pipeline.

Two kinds of assertion live here, and both are needed:

**Positive controls** -- the whole chain runs, the criteria pass, the conservative
transfer is exact, the calibrated prior is calibrated, and the cross-stage
invariant (optimiser nominal not beaten by the 5 % ensemble quantile) holds.

**Negative controls** -- the same gates must be able to *fail* when the stage is
degraded (nearest-cell transfer without correction leaks mass; a non-nested
overlap projection loses the first moment; a mis-declared tag is rejected; a
broken chain is reported as broken).  Without these a green gate would prove
nothing.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from tools.toolchain_integration import ExecutionContext, PipelineSpec, build_plan, measure
from tools.toolchain_integration.dag import run_pipeline
from tools.toolchain_integration.report import dependency_graph

ROOT = Path(__file__).resolve().parents[1]
SHIPPED = ROOT / "tools" / "toolchain_integration" / "specs" / "synthetic_toolchain_v1.json"


@pytest.fixture(scope="module")
def run():
    spec = PipelineSpec.load(SHIPPED)
    return spec, run_pipeline(spec, ExecutionContext(seed=3), plan=build_plan(spec))


def test_every_stage_runs_and_reports_evidence(run):
    spec, r = run
    assert r.status == "OK"
    for rec in r.records:
        assert rec.status == "OK", (rec.node_id, rec.error)
        assert rec.evidence in ("REAL", "MOCK")
        assert rec.wall_s >= 0.0
        assert rec.chain_digest
    # the sibling-repo stage is absent here, so the run's evidence is honestly MOCK
    assert r.evidence == "MOCK"
    assert r.record("external_repo_stage").evidence == "MOCK"
    assert r.record("uncertainty").evidence == "REAL"


def test_criteria_all_pass(run):
    _, r = run
    status = r.value("rollup.status").value
    assert status["verdict"] == "PASS"
    assert status["n_fail"] == 0 and status["n_withheld"] == 0
    assert status["n_pass"] >= 10
    assert status["n_info"] == 1           # external availability is advisory
    by_name = {c["name"]: c for c in status["criteria"]}
    assert by_name["repo_stage_available"]["status"] == "INFO"
    assert by_name["repo_stage_available"]["value"] is False


def test_conservative_transfer_is_exact_on_nested_meshes(run):
    _, r = run
    total_src = r.value("field_transfer.total_src").value
    total_dst = r.value("field_transfer.total_dst").value
    assert r.value("field_transfer.rel_residual").value <= 1e-12
    assert total_dst == pytest.approx(total_src, rel=1e-12)
    assert r.value("exchange_gate.passed").value is True
    assert r.value("exchange_gate.sum_residual").value <= 1e-12
    assert r.value("exchange_gate.moment_residual").value <= 1e-10
    metrics = r.value("exchange_gate.metrics").value["metrics"]
    assert metrics["overshoot_ratio"] <= 1.0 + 1e-12
    assert metrics["nonneg_violation"] <= 1e-12


def test_cross_stage_invariant_optimiser_vs_uq(run):
    """The optimiser minimises the same response the propagator samples."""
    _, r = run
    nominal = r.value("uncertainty.nominal").value
    q05 = r.value("uncertainty.stats").value["q05"]
    assert nominal <= q05
    assert r.value("uncertainty.anchor_gap_stderr").value >= 3.0
    assert r.value("design.f_best").value == pytest.approx(nominal, rel=1e-6, abs=1e-12)


def test_prior_calibration_statistics(run):
    _, r = run
    rep = r.value("feasibility_prior.report").value
    assert rep["n"] == 20000
    assert rep["brier"] < rep["brier_reference"]      # beats the base rate
    assert rep["ece"] <= 0.05
    assert rep["auroc"] >= 0.6
    assert rep["monotone"] is True
    assert len(rep["bins"]) == 10
    # reliability bins must actually be populated (a silent empty bin would hide error)
    assert sum(b["count"] for b in rep["bins"]) == rep["n"]


def test_overhead_gate_and_reproducibility(run):
    spec, _ = run
    rep = measure(spec, repeats=2, ctx=ExecutionContext(seed=3))
    assert rep.determinism, "same seed must reproduce the same run digest"
    assert rep.orchestration_ratio < 0.20
    assert rep.status == "OK"


def test_dependency_graph_is_complete(run):
    spec, _ = run
    graph = dependency_graph(spec)
    assert len(graph["nodes"]) == len(spec.nodes)
    node_ids = {n["id"] for n in graph["nodes"]}
    for edge in graph["edges"]:
        assert edge["from"].split(".")[0] in node_ids
        assert edge["to"].split(".")[0] in node_ids
    # every node except the pure sources consumes at least one upstream value
    sources = {n["id"] for n in graph["nodes"] if not any(
        e["to"].split(".")[0] == n["id"] for e in graph["edges"])}
    assert sources == {"surface", "field_transfer", "score_probe",
                       "external_local_callable", "external_repo_stage"}


# ---------------------------------------------------------------------------
# negative controls: the gates must be able to fail
# ---------------------------------------------------------------------------


def test_conservation_gate_catches_a_leaking_transfer():
    from tools.coupling.conservation import check
    from tools.coupling.mesh import BoxMesh, Field, QuantityKind
    from tools.coupling.remap import RemapSpec, remap

    src = BoxMesh.uniform([0, 0, 0], [2, 1, 1], [8, 4, 4], name="src")
    dst = BoxMesh.uniform([0, 0, 0], [2, 1, 1], [12, 6, 6], name="dst")
    centres = src.centers
    f = Field(values=1.0 + 3.0 * (centres[:, 0] - centres[:, 0].min()) / 2.0,
              mesh=src, kind=QuantityKind.DENSITY, name="f")
    res = remap(f, operator="nearest_scaled",
                spec=RemapSpec(operator="nearest_scaled",
                               apply_conservation_correction=False),
                dst_mesh=dst)
    gate = check(f, res.field)
    assert gate.passed is False
    assert gate.metrics.sum_rel_residual > 1e-3      # mass leaked, gate caught it


def test_overlap_projection_is_not_moment_conservative_on_a_non_nested_mesh():
    """Documents a real limitation instead of assuming the operator is perfect."""
    from tools.coupling.conservation import check
    from tools.coupling.mesh import BoxMesh, Field, QuantityKind
    from tools.coupling.remap import RemapSpec, remap

    src = BoxMesh.uniform([0, 0, 0], [2, 1, 1], [8, 4, 4], name="src")
    dst = BoxMesh.uniform([0, 0, 0], [2, 1, 1], [12, 6, 6], name="dst")
    centres = src.centers
    f = Field(values=1.0 + 3.0 * (centres[:, 0] - centres[:, 0].min()) / 2.0,
              mesh=src, kind=QuantityKind.DENSITY, name="f")
    res = remap(f, operator="overlap_l2",
                spec=RemapSpec(operator="overlap_l2",
                               apply_conservation_correction=False),
                dst_mesh=dst)
    gate = check(f, res.field)
    assert res.report.rel_residual <= 1e-12          # total is exact
    assert gate.passed is False                      # but the first moment is not
    assert gate.metrics.moment_rel_residual > 1e-5


def test_pipeline_reports_failure_when_a_gate_is_violated():
    """Wire a leaking transfer into the gate node: the run must come back FAIL."""
    from tools.toolchain_integration import Binding, NodeSpec

    spec = PipelineSpec(name="leaky", nodes=(
        NodeSpec(node_id="transfer", adapter="remap", params={
            "src_mesh": {"lo": [0, 0, 0], "hi": [2, 1, 1], "n": [8, 4, 4]},
            "dst_mesh": {"lo": [0, 0, 0], "hi": [2, 1, 1], "n": [12, 6, 6]},
            "field": {"kind": "linear", "axis": 0, "slope": 3.0, "offset": 1.0},
            "operator": "nearest_scaled",
            "apply_conservation_correction": False,
        }),
        NodeSpec(node_id="gate", adapter="conservation_gate",
                 inputs={"src": Binding(kind="ref", node="transfer", port="src"),
                         "dst": Binding(kind="ref", node="transfer", port="dst")},
                 params={}),
        NodeSpec(node_id="rollup", adapter="audit",
                 inputs={"gate": Binding(kind="ref", node="gate", port="passed")},
                 params={"criteria": [{"name": "conservative", "input": "gate",
                                       "op": "eq_true"}]}),
    ))
    r = run_pipeline(spec)
    assert r.status == "OK"                          # stages ran ...
    assert r.value("gate.passed").value is False     # ... the gate said no
    assert r.value("rollup.status").value["verdict"] == "FAIL"


def test_interface_mismatch_between_libraries_is_rejected():
    """A UQ report cannot be fed to a scalar port: the harness refuses, never coerces."""
    from tools.toolchain_integration import Binding, NodeSpec, SpecError, build_plan

    spec = PipelineSpec(name="mismatch", nodes=(
        NodeSpec(node_id="probe", adapter="probe", params={"n": 64, "seed": 1}),
        NodeSpec(node_id="uq", adapter="uq",
                 inputs={"objective": Binding(kind="ref", node="probe", port="scores")},
                 params={"inputs": [{"name": "u", "kind": "normal", "mean": 0.0, "std": 1.0}],
                         "n": 200, "n_boot": 50}),
    ))
    with pytest.raises(SpecError, match="expects tag"):
        build_plan(spec)