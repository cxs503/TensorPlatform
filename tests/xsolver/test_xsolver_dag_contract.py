"""C6 -- the frame's output is consumed by a campaign-DAG verification node.

Two halves:

* the *contract* half proves the verdict document's payload is exactly the shape the
  DAG endpoint accepts (checked against the real pydantic model, not a copy of it),
  and that a withheld verdict can never arrive at the node as ``pass``;
* the *integration* half runs the whole acceptance driver end to end over the
  snapshot corpus -- harvest -> schema -> conservation gate -> adjudication ->
  attribution -> verdict -> DAG payload -- and asserts the six criteria come back in
  the state their inputs justify, including the failure state.
"""
from __future__ import annotations

import copy
from pathlib import Path

import pytest


# ---------------------------------------------------------------------------
# contract half
# ---------------------------------------------------------------------------
def test_c6_payload_matches_the_real_dag_model(xsolver, snapshot_corpus):
    """The mirrored contract must equal the API's own ``NodeVerdictUpdate``."""
    from api.campaign_plans import NodeVerdictUpdate

    mirror = xsolver.dag_consumer.NODE_VERDICT_SCHEMA
    assert set(mirror["properties"]) == set(NodeVerdictUpdate.model_fields)
    assert mirror["required"] == ["reproduction_verdict"]
    assert mirror["additionalProperties"] is False
    pattern = mirror["properties"]["reproduction_verdict"]["pattern"]
    assert NodeVerdictUpdate.model_fields["reproduction_verdict"].metadata, \
        "the API model must keep its (pass|fail) pattern constraint"

    report = xsolver.adjudicate.adjudicate(copy.deepcopy(snapshot_corpus))
    document = xsolver.dag_consumer.build_verdict_document(report, "wigley3_total_drag")
    payload = document["node_verdict_payload"]
    # The API model carries optional extras (``metrics`` / ``metric_name``) with their own
    # defaults, so full round-trip equality is not the invariant we can hold: what we send
    # must survive validation *unchanged*, which is what this asserts.
    assert NodeVerdictUpdate(**payload).model_dump(include=set(payload)) == payload
    with pytest.raises(Exception):
        NodeVerdictUpdate(reproduction_verdict="withheld", block_reason=None)


def test_c6_verdict_document_validates_against_its_schema(xsolver, snapshot_corpus):
    report = xsolver.adjudicate.adjudicate(copy.deepcopy(snapshot_corpus))
    document = xsolver.dag_consumer.build_verdict_document(report, "wigley3_total_drag")
    errors = xsolver.dag_consumer.validate(
        document, xsolver.dag_consumer.load_schema("verdict_schema"))
    assert errors == []
    # the evidence travels with the decision, so the node can audit it later
    assert [c["id"] for c in document["criteria"]] == [
        "arms_present", "conservation_gate", "traceability",
        "cross_solver_agreement", "definition_attribution"]
    assert "term_coverage" in document["evidence"]["attribution"]["kinds"]
    assert document["trace"]["adjudication_digest"]
    assert document["trace"]["input_digest"], "every arm's input digest must travel"


def test_c6_withheld_is_projected_as_fail_not_pass(xsolver, case_factory,
                                                   make_corpus_fixture):
    single = case_factory("synthetic_one_arm", "SolverA",
                          {"C_T": 0.006, "C_F": 0.004, "C_W": 0.002})
    report = xsolver.adjudicate.adjudicate(make_corpus_fixture([single]))
    document = xsolver.dag_consumer.build_verdict_document(report, "synthetic_one_arm")
    assert document["verdict"] in ("withheld", "fail")
    assert document["node_verdict_payload"]["reproduction_verdict"] == "fail"
    assert document["node_verdict_payload"]["block_reason"]


def test_c6_consumption_is_idempotent(xsolver, snapshot_corpus):
    report = xsolver.adjudicate.adjudicate(copy.deepcopy(snapshot_corpus))
    document = xsolver.dag_consumer.build_verdict_document(report, "wigley3_total_drag")
    first = xsolver.dag_consumer.consume_verdict(document)
    # consume_verdict is a projection: feeding its own document back must agree
    second = xsolver.dag_consumer.consume_verdict(document)
    assert first["node_verdict_payload"] == second["node_verdict_payload"]
    assert first["criteria_failed"] == second["criteria_failed"]


# ---------------------------------------------------------------------------
# integration half: the whole driver, end to end
# ---------------------------------------------------------------------------
def _stub_probe(probe_module, normalize, certified_mass: float, verified: bool):
    """A probe result shaped like the real one, with a declared provenance verdict."""
    measurement = {
        "backend": {"module": "tensorlbm", "version": "0.3.0+git.tests",
                    "repo_commit": "tests"},
        "provenance": {
            "verified": verified,
            "module_file": ("/repo/TensorLBM/src/tensorlbm/__init__.py" if verified
                            else "/elsewhere/tensorlbm/__init__.py"),
            "module_sha256": "0" * 64, "repo_root": "/repo/TensorLBM",
            "repo_commit": "tests", "loaded_from": "repo-dir-pinned",
            "api": ["equilibrium", "collide_bgk", "stream", "macroscopic"],
            "shadow_copies": [],
        },
        "certification": {"horizon": 100, "mass": certified_mass,
                          "momentum": certified_mass},
        "drift_rate_per_step": certified_mass / 100.0,
        "mass_tolerance_crossing_steps": (1e-6 / (certified_mass / 100.0)
                                          if certified_mass else None),
        "horizons": [{"steps": 100, "mass": certified_mass,
                      "momentum": certified_mass, "momentum_y_abs": 0.0,
                      "iterative_residual": 0.0}],
        "longest_horizon": {"steps": 100, "mass": certified_mass,
                            "momentum": certified_mass},
        "energy": None,
        "energy_note": "isothermal",
        "probe_id": "stub",
        "setup": {"grid": [16, 16], "tau": 0.8, "velocity": 0.05, "dtype": "float64",
                  "horizons": [100], "certification_horizon": 100},
        "reference": {"mass": 256.0, "momentum_x": 12.8, "momentum_y": 0.0},
    }
    record = normalize.build_case(
        {"case": "stub_probe", "output": {"mass_residual": certified_mass}},
        source_path="/tmp/stub_probe.json", repo="synthetic", root=Path("/tmp"),
        problem_id="conservation_probe::stub",
        solver_override={"name": "tensorlbm", "family": "LB", "version": "stub",
                         "method": "stub"},
        quantity_override={"mass_residual": certified_mass},
        residual_override={"mass": certified_mass, "momentum": certified_mass,
                           "energy": None},
        geometry_override={"kind": "periodic_box", "units": "lattice", "dimensions": None,
                           "grid": {"nx": 16.0, "ny": 16.0}, "derived": None,
                           "source_path": "/tmp/stub_probe.json", "stage": "probe"},
    )
    record["provenance"]["git_commit"] = "tests"
    record["condition"] = {"label": "steps", "value": 100, "case_name": "stub_probe"}
    return {"measurement": measurement, "record": record, "artifact": "/tmp/stub_probe.json"}


def test_e2e_acceptance_run_on_snapshot(xsolver, snapshot_corpus, tmp_path, monkeypatch):
    """Harvest -> gate -> adjudicate -> verdict -> DAG payload, all in one run."""
    monkeypatch.setattr(
        xsolver.acceptance, "run_probe",
        lambda out_path: _stub_probe(xsolver.probe, xsolver.normalize, 7.1e-15, True))

    result = xsolver.acceptance.run(out_dir=tmp_path, corpus=copy.deepcopy(snapshot_corpus),
                                    write=False)
    acceptance = result["acceptance"]

    assert acceptance["overall_status"] == xsolver.conservation.PASS, \
        [c for c in acceptance["criteria"] if c["status"] != "PASS"]
    ids = [c["id"] for c in acceptance["criteria"]]
    assert ids == ["C1_schema_coverage", "C2_conservation_residuals",
                   "C3_cross_solver_adjudication", "C4_negative_controls",
                   "C5_traceability", "C6_dag_consumption",
                   # wave 2: two live harvests of the same sources must fingerprint equal
                   "C7_reproducibility"]
    assert acceptance["verdicts"], "the DAG payloads must be present in the result"
    for verdict in acceptance["verdicts"]:
        assert verdict["reproduction_verdict"] in ("pass", "fail")
        assert verdict["problem_id"]


def test_e2e_acceptance_flags_an_unverified_probe(xsolver, snapshot_corpus, tmp_path,
                                                 monkeypatch):
    """The same run, but the probe's origin cannot be proven -> C2 must fail.

    This is the regression for the mis-attribution defect: a residual whose module
    came from outside the declared repo may not certify the node, however small it is.
    """
    monkeypatch.setattr(
        xsolver.acceptance, "run_probe",
        lambda out_path: _stub_probe(xsolver.probe, xsolver.normalize, 7.1e-15, False))

    result = xsolver.acceptance.run(out_dir=tmp_path, corpus=copy.deepcopy(snapshot_corpus),
                                    write=False)
    acceptance = result["acceptance"]
    criteria = {c["id"]: c for c in acceptance["criteria"]}
    assert criteria["C2_conservation_residuals"]["status"] == xsolver.conservation.FAIL
    assert criteria["C2_conservation_residuals"]["value"]["probe_provenance_verified"] is False
    assert acceptance["overall_status"] == xsolver.conservation.FAIL


def test_e2e_acceptance_withheld_without_a_probe(xsolver, snapshot_corpus, tmp_path,
                                                monkeypatch):
    """No backend -> WITHHELD wording, and the driver still produces a verdict."""
    def _unavailable(out_path):
        raise xsolver.probe.ProbeUnavailable("tests: backend intentionally absent")

    monkeypatch.setattr(xsolver.acceptance, "run_probe", _unavailable)
    result = xsolver.acceptance.run(out_dir=tmp_path, corpus=copy.deepcopy(snapshot_corpus),
                                    write=False)
    acceptance = result["acceptance"]
    criteria = {c["id"]: c for c in acceptance["criteria"]}
    assert "WITHHELD" in criteria["C2_conservation_residuals"]["detail"]
    assert acceptance["probe"] is None
    assert "WITHHELD" in acceptance["probe_note"]


def test_e2e_artifacts_on_disk_are_consistent(xsolver):
    """The committed artifacts must agree with a fresh adjudication of their own corpus."""
    artifacts = xsolver.repo / "artifacts" / "xsolver"
    acceptance_path = artifacts / "acceptance.json"
    if not acceptance_path.is_file():
        pytest.skip("acceptance artifact not generated yet")
    import json

    acceptance = json.loads(acceptance_path.read_text(encoding="utf-8"))
    assert acceptance["overall_status"] in ("PASS", "FAIL")
    ids = [c["id"] for c in acceptance["criteria"]]
    assert "C6_dag_consumption" in ids
    # a committed acceptance artifact must state which node/run produced it
    assert acceptance["node"] and acceptance["mission_id"]
    probe = acceptance.get("probe") or {}
    if probe:
        assert probe["provenance"]["verified"] is True, \
            "a published probe residual must carry a verified origin"
        assert probe["provenance"]["module_file"]
        assert probe["provenance"]["module_sha256"]
    adjudication = artifacts / "adjudication.json"
    if adjudication.is_file():
        report = json.loads(adjudication.read_text(encoding="utf-8"))
        assert report["trace"]["report_digest"] == acceptance["report_digest"]