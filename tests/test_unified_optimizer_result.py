"""Unified optimizer -- standardised result output and cross-engine adjudication.

Covers criterion C5 (every result validates against
``schemas/optimizer_result.v1.schema.json`` and carries optimum / status /
history / constraint violation) plus C1-style cross-engine agreement.
"""

from __future__ import annotations

import json

import pytest

from tools.optimizer.unified import (
    RESULT_SCHEMA_PATH,
    SolveResult,
    crosscheck,
    get_problem,
    kkt_diagnostics,
    solve,
    validate_result,
    validate_result_dict,
)
from tools.optimizer.unified.compiled import CompiledProblem


def test_result_schema_file_is_published():
    schema = json.loads(RESULT_SCHEMA_PATH.read_text())
    assert schema["properties"]["schema"]["const"] == "optimizer/result/v1"
    for field in ("x", "status", "history", "constraint_violation", "provenance"):
        assert field in schema["required"]


def test_converged_result_validates_and_contains_all_fields():
    spec = get_problem("sphere_eq_ineq")
    result = solve(spec, engine="scipy", algorithm="SLSQP")
    doc = validate_result(result)

    assert doc["schema"] == "optimizer/result/v1"
    assert set(doc["x"]) == {"x", "y", "z"}
    assert doc["objective"] == pytest.approx(3.0, abs=1e-6)
    assert doc["status"] == "converged" and doc["success"] is True
    assert len(doc["history"]) >= 1
    assert doc["constraint_violation"] <= 1e-8
    assert doc["kkt"]["kkt_residual"] < 1e-4
    assert doc["provenance"]["library_versions"]["scipy"]
    assert doc["problem_fingerprint"] == spec.fingerprint()


def test_result_without_required_field_is_rejected():
    spec = get_problem("rosenbrock_2d")
    doc = solve(spec, engine="scipy", algorithm="SLSQP").to_dict()
    broken = {k: v for k, v in doc.items() if k != "constraint_violation"}
    with pytest.raises(Exception):
        validate_result_dict(broken)


def test_result_rejects_unknown_status():
    spec = get_problem("rosenbrock_2d")
    doc = solve(spec, engine="scipy", algorithm="SLSQP").to_dict()
    doc["status"] = "probably-fine"
    with pytest.raises(Exception):
        validate_result_dict(doc)


def test_history_is_recorded_and_bounded():
    spec = get_problem("g06")
    result = solve(spec, engine="nlopt", algorithm="COBYLA", options={"max_eval": 3000})
    doc = result.to_dict(history_limit=20)
    assert doc["history_total"] >= 1
    assert len(doc["history"]) <= 20
    for point in doc["history"]:
        assert {"iteration", "objective", "constraint_violation"} <= set(point)
        assert point["constraint_violation"] >= 0.0


def test_unavailable_result_is_schema_valid_with_null_numbers():
    spec = get_problem("g10")
    result = solve(spec, engine="pyoptsparse", algorithm="SNOPT")
    doc = validate_result(result)
    assert doc["status"] == "unavailable"
    assert doc["objective"] is None
    assert json.dumps(doc)  # NaN-free, serialisable


def test_objective_error_fields_use_scale_floor():
    spec = get_problem("g06")
    result = solve(spec, engine="nlopt", algorithm="COBYLA", options={"max_eval": 5000})
    assert result.objective_error is not None
    assert abs(result.objective_rel_error) < 1e-6


def test_crosscheck_three_solvers_agree_within_one_percent():
    spec = get_problem("g08")
    report = crosscheck(
        spec,
        pairs=[("scipy", "SLSQP"), ("nlopt", "SLSQP"), ("scipy", "trust-constr")],
        tol=0.01,
    )
    assert report["converged_count"] == 3
    assert report["max_pairwise_rel_deviation"] < 0.01
    assert report["meets_tolerance"] is True
    assert report["reference_rel_error_of_best"] < 1e-6
    assert len(report["pairwise"]) == 3
    assert all(row["kkt_residual"] is not None for row in report["kkt"])


def test_crosscheck_keeps_unavailable_engine_row():
    spec = get_problem("rosenbrock_2d")
    report = crosscheck(
        spec, pairs=[("scipy", "SLSQP"), ("nlopt", "LBFGS"), ("pyoptsparse", "SNOPT")], tol=0.01
    )
    statuses = {row["engine"]: row["status"] for row in report["results"]}
    assert statuses["pyoptsparse"] == "unavailable"
    assert report["converged_count"] == 2
    assert report["meets_tolerance"] is True  # two converged solvers, deviation ~ machine zero


def test_kkt_diagnostics_flags_infeasible_point():
    spec = get_problem("g06")
    compiled = CompiledProblem(spec)
    x_bad = compiled.x0()  # violates the circle constraint
    diag = kkt_diagnostics(compiled, x_bad)
    assert diag["primal_feasibility"] > 1.0
    assert diag["kkt_residual"] >= diag["primal_feasibility"]


def test_kkt_diagnostics_small_at_analytic_optimum():
    spec = get_problem("sphere_eq_ineq")
    compiled = CompiledProblem(spec)
    diag = kkt_diagnostics(compiled, [1.0, 1.0, 1.0])
    assert diag["kkt_residual"] < 1e-12


def test_solve_result_dataclass_is_reusable_without_engine():
    spec = get_problem("rosenbrock_2d")
    res = SolveResult(
        problem_id="rosenbrock_2d",
        engine="manual",
        algorithm="hand",
        status="converged",
        success=True,
        x={"x": 1.0, "y": 1.0},
        objective=0.0,
        constraint_violation=0.0,
        n_iter=1,
        n_eval=1,
        elapsed_s=0.0,
        problem_fingerprint=spec.fingerprint(),
    )
    doc = validate_result(res)
    assert doc["engine"] == "manual" and doc["objective"] == 0.0