"""Acceptance criteria C1-C5 of node p83w1-n07, exercised on synthetic + snapshotted data.

Each test targets one mechanically checkable claim of the node, and each *negative*
control asserts that the failure mode is detectable -- a criterion that can only
print "PASS" is not a criterion.
"""
from __future__ import annotations

import copy
import json

import pytest


# ---------------------------------------------------------------------------
# C1 -- schema coverage on the committed snapshot (no ecosystem checkout needed)
# ---------------------------------------------------------------------------
def test_c1_coverage_thresholds_on_snapshot(xsolver, snapshot_corpus):
    criterion = xsolver.acceptance.criterion_coverage(snapshot_corpus)
    value = criterion["value"]
    assert criterion["status"] == xsolver.conservation.PASS, criterion["detail"]
    assert value["cases"] >= 74, "the node's corpus floor is 74 verified cases"
    assert value["geometry_rate"] >= 0.90, value
    assert value["primary_quantities_rate"] >= 0.80, value


def test_c1_coverage_fails_when_geometry_is_stripped(xsolver, snapshot_corpus):
    """Negative control: a corpus without geometry blocks cannot pass C1."""
    stripped = copy.deepcopy(snapshot_corpus)
    for case in stripped["cases"]:
        case["parse"]["geometry"] = {"status": "missing", "reason": "test", "stage": "test"}
        case["input"]["geometry"] = None
    stripped["stats"]["coverage"]["geometry_parsed"] = 0
    stripped["stats"]["coverage"]["geometry_rate"] = 0.0
    criterion = xsolver.acceptance.criterion_coverage(stripped)
    assert criterion["status"] == xsolver.conservation.FAIL


# ---------------------------------------------------------------------------
# C2 -- the gate sits exactly on the contract numbers and cannot be talked out of it
# ---------------------------------------------------------------------------
def test_c2_gate_tolerances_are_the_contract_numbers(xsolver):
    tol = xsolver.conservation.tolerances_for("LB")
    assert tol["mass"] == pytest.approx(1e-6, rel=0, abs=0)
    assert tol["momentum"] == pytest.approx(1e-4, rel=0, abs=0)


@pytest.mark.parametrize("mass,excluded,statuses", [
    (1e-9, False, ("pass", "warn")),   # within tolerance; energy unrecorded -> WARN
    (1e-3, True, ("fail",)),           # 1000x over tolerance -> conservation_fail
])
def test_c2_gate_status_follows_the_measured_residual(xsolver, mass, excluded, statuses):
    gate = xsolver.conservation.gate({"mass": mass, "momentum": 1e-9, "energy": None}, "LB")
    assert gate["status"].lower() in statuses, gate
    assert gate["excluded_from_comparison"] is excluded


def test_c2_percent_residuals_are_scaled_before_the_gate(xsolver):
    """A ``*_pct`` residual must be divided by 100, not compared raw.

    Real artifact value used as the fixture: TensorLBM poiseuille_3d_annulus records
    ``mass_drift_pct = 3.214998``, i.e. a 3.2 % drift -- two orders of magnitude to
    the wrong side of the gate if the percent scale were dropped.
    """
    raw = {"per_grid": [{"mass_drift_pct": 3.214998}]}
    extracted = xsolver.normalize.extract_residuals(raw)
    assert extracted["conservation_residuals"]["mass"] == pytest.approx(0.03214998)
    assert extracted["residual_basis"]["mass"] == "recorded_percent"
    gate = xsolver.conservation.gate(extracted["conservation_residuals"], "LB")
    assert gate["status"] == xsolver.conservation.FAIL


def test_c2_forced_violation_is_excluded_from_comparison(xsolver):
    """Negative control (b): mass residual forced to 1.0 -> conservation_fail."""
    forced = xsolver.conservation.gate({"mass": 1.0, "momentum": None, "energy": None}, "LB")
    assert forced["conservation_fail"] is True
    assert forced["excluded_from_comparison"] is True


def test_c2_unrecorded_residual_is_withheld_not_passed(xsolver):
    withheld = xsolver.conservation.gate({"mass": None, "momentum": None, "energy": None}, "LB")
    assert withheld["status"] == xsolver.conservation.WITHHELD
    assert withheld["excluded_from_comparison"] is False


def test_c2_probe_provenance_gate_flips_the_criterion(xsolver):
    """The provenance pin is load-bearing: an unattributable probe cannot certify C2."""
    measurement = {
        "backend": {"module": "tensorlbm", "version": "x", "repo_commit": "deadbeef"},
        "provenance": {"verified": False, "module_file": "/somewhere/else/tensorlbm/__init__.py"},
        "certification": {"horizon": 100, "mass": 7.1e-15, "momentum": 7.2e-15},
        "drift_rate_per_step": 7.1e-17,
        "mass_tolerance_crossing_steps": 1.4e10,
        "horizons": [], "longest_horizon": {"steps": 100, "mass": 7.1e-15,
                                            "momentum": 7.2e-15},
    }
    corpus = {"cases": [], "stats": {"coverage": {}}}
    unverified = xsolver.acceptance.criterion_conservation(
        corpus, {"measurement": measurement})
    assert unverified["status"] == xsolver.conservation.FAIL
    assert unverified["value"]["probe_provenance_verified"] is False

    measurement["provenance"] = {"verified": True, "module_file": "/repo/src/tensorlbm/__init__.py"}
    verified = xsolver.acceptance.criterion_conservation(
        corpus, {"measurement": measurement})
    assert verified["status"] == xsolver.conservation.PASS
    assert verified["value"]["probe_provenance_verified"] is True


# ---------------------------------------------------------------------------
# C3 -- automatic adjudication: the engine must both certify and refuse
# ---------------------------------------------------------------------------
def test_c3_snapshot_wigley3_is_adjudicated_with_attribution(xsolver, snapshot_corpus):
    report = xsolver.adjudicate.adjudicate(snapshot_corpus)
    group = next(g for g in report["groups"] if g["problem_id"] == "wigley3_total_drag")
    solvers = {arm["solver"] for arm in group["arms"]}
    assert solvers == {"TensorLBM", "TensorFVM", "TensorBEM"}
    ct_pairs = [c for c in group["comparisons"] if c["quantity"] == "C_T"]
    assert len(ct_pairs) == 9, "3 conditions x 3 solver pairs on C_T"
    # the raw spread is definitional, and the engine must say so rather than
    # report a 99 % "disagreement" between three different definitions
    kinds = set(group["attribution"]["kinds"])
    assert "term_coverage" in kinds, group["attribution"]["kinds"]
    assert group["status"] != "comparable", (
        "disjoint term coverage must not be called a comparable cross-solver set")
    # every attribution kind carries a numeric magnitude and an evidence block
    for finding in group["attribution"]["findings"]:
        assert finding["kind"] in kinds
        assert "statement" in finding and "evidence" in finding


def test_c3_agreeing_arms_pass_the_dag_criteria(xsolver, case_factory, make_corpus_fixture):
    """The engine is not a rejector: two consistent, conserving arms must pass.

    All three residuals are supplied here (the synthetic arm is not isothermal) so
    that the conservation criterion can return PASS rather than WARN/WITHHELD --
    otherwise the verdict is withheld by design and the pass path would never be
    exercised.
    """
    quantities = {"C_T": 0.0060, "C_F": 0.0040, "C_W": 0.0020}
    residuals = {"mass": 1e-12, "momentum": 1e-9, "energy": 1e-12}
    a = case_factory("synthetic_agreement", "SolverA", quantities, residuals=residuals)
    b = case_factory("synthetic_agreement", "SolverB",
                     {"C_T": 0.0061, "C_F": 0.0041, "C_W": 0.0020}, residuals=residuals)
    report = xsolver.adjudicate.adjudicate(make_corpus_fixture([a, b]))
    group = report["groups"][0]
    document = xsolver.dag_consumer.build_verdict_document(report, group["problem_id"])
    assert document["verdict"] == "pass", document["criteria"]
    assert document["node_verdict_payload"]["reproduction_verdict"] == "pass"
    assert document["node_verdict_payload"]["block_reason"] is None


def test_c3_unmeasured_residual_withholds_the_verdict(xsolver, case_factory,
                                                     make_corpus_fixture):
    """No energy residual -> no pass.  Withheld maps to fail at the DAG, never to pass."""
    quantities = {"C_T": 0.0060, "C_F": 0.0040, "C_W": 0.0020}
    residuals = {"mass": 1e-12, "momentum": 1e-9, "energy": None}
    a = case_factory("synthetic_withheld", "SolverA", quantities, residuals=residuals)
    b = case_factory("synthetic_withheld", "SolverB", quantities, residuals=residuals)
    report = xsolver.adjudicate.adjudicate(make_corpus_fixture([a, b]))
    document = xsolver.dag_consumer.build_verdict_document(report, "synthetic_withheld")
    assert document["verdict"] == "withheld"
    assert document["node_verdict_payload"]["reproduction_verdict"] == "fail"
    assert "conservation_gate" in (document["node_verdict_payload"]["block_reason"] or "")


def test_c3_disagreeing_arms_fail_with_a_reason(xsolver, case_factory, make_corpus_fixture):
    a = case_factory("synthetic_disagreement", "SolverA",
                     {"C_T": 0.0060, "C_F": 0.0040, "C_W": 0.0020},
                     residuals={"mass": 1e-12, "momentum": 1e-9, "energy": None})
    b = case_factory("synthetic_disagreement", "SolverB",
                     {"C_T": 0.0120, "C_F": 0.0040, "C_W": 0.0020},
                     residuals={"mass": 1e-12, "momentum": 1e-9, "energy": None},
                     method="different-formulation")
    report = xsolver.adjudicate.adjudicate(make_corpus_fixture([a, b]))
    document = xsolver.dag_consumer.build_verdict_document(report, "synthetic_disagreement")
    assert document["node_verdict_payload"]["reproduction_verdict"] == "fail"
    assert document["node_verdict_payload"]["block_reason"]


def test_c3_single_arm_is_never_a_comparison(xsolver, case_factory, make_corpus_fixture):
    only = case_factory("synthetic_single", "SolverA",
                        {"C_T": 0.0060, "C_F": 0.0040, "C_W": 0.0020})
    report = xsolver.adjudicate.adjudicate(make_corpus_fixture([only]))
    assert report["groups"][0]["status"] == "single_arm"


def test_c3_adjudication_is_deterministic(xsolver, snapshot_corpus):
    first = xsolver.adjudicate.adjudicate(copy.deepcopy(snapshot_corpus))
    second = xsolver.adjudicate.adjudicate(copy.deepcopy(snapshot_corpus))
    assert first["trace"]["report_digest"] == second["trace"]["report_digest"]


def test_c3_comparisons_never_cross_conditions(xsolver, case_factory, make_corpus_fixture):
    """A pair must share its Froude number: otherwise the "difference" is the condition."""
    a = case_factory("synthetic_conditions", "SolverA", {"C_T": 0.0060},
                     condition={"label": "Fn", "value": 0.3, "case_name": "c"})
    b = case_factory("synthetic_conditions", "SolverB", {"C_T": 0.0060},
                     condition={"label": "Fn", "value": 0.4, "case_name": "c"})
    report = xsolver.adjudicate.adjudicate(make_corpus_fixture([a, b]))
    assert report["groups"][0]["comparisons"] == []


# ---------------------------------------------------------------------------
# C4 -- the negative controls the acceptance driver runs
# ---------------------------------------------------------------------------
def test_c4_negative_controls_all_fire(xsolver, snapshot_corpus):
    criterion = xsolver.acceptance.criterion_negative_controls(snapshot_corpus)
    results = criterion["value"]
    assert results["missing_primary_quantities"]["raised"] is True
    assert results["mass_residual_forced"]["raised"] is True
    assert results["mass_residual_forced"]["excluded"] is True
    assert results["mass_residual_forced"]["arm_still_compared"] is False
    assert results["foreign_module_rejected"]["raised"] is True
    assert results["foreign_module_rejected"]["error"] == "ProvenanceError"
    assert criterion["status"] == xsolver.conservation.PASS, criterion["detail"]


def test_c4_schema_rejects_a_record_without_quantities(xsolver, case_factory):
    record = case_factory("synthetic_schema", "SolverA", {"C_T": 0.006})
    record["output"].pop("primary_quantities")
    with pytest.raises(ValueError):
        xsolver.normalize.validate_records([record])


# ---------------------------------------------------------------------------
# C5 -- traceability
# ---------------------------------------------------------------------------
def test_c5_every_snapshot_case_is_traceable(xsolver, snapshot_index):
    criterion = xsolver.acceptance.criterion_traceability(snapshot_index)
    assert criterion["status"] == xsolver.conservation.PASS, criterion["detail"]
    value = criterion["value"]
    assert value["cases"] >= 74
    assert value["rate"] == pytest.approx(1.0)


def test_c5_a_case_without_a_commit_is_flagged(xsolver, snapshot_index):
    tampered = copy.deepcopy(snapshot_index)
    tampered["sources"][0]["git_commit"] = None
    criterion = xsolver.acceptance.criterion_traceability(tampered)
    assert criterion["status"] == xsolver.conservation.FAIL
    assert criterion["value"]["missing"][0]["case_id"] == snapshot_index["sources"][0]["case_id"]