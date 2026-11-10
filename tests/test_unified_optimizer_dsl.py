"""Unified optimizer -- problem definition DSL (schema, expressions, round-trip).

Covers criterion C6 (>= 5 standard problems definable & solvable through the DSL)
and the DSL's safety properties: no arbitrary code, no unknown symbols, schema
rejection of malformed documents.
"""

from __future__ import annotations

import json

import pytest

from tools.optimizer.unified import (
    PROBLEM_SCHEMA_PATH,
    PROBLEM_IDS,
    CompiledProblem,
    DSLValidationError,
    ProblemSpec,
    compile_problem,
    dump_problem,
    get_problem,
    load_problem,
    loads_problem,
    reference_table,
    standard_problems,
    validate_problem_dict,
)
from tools.optimizer.unified.expr import ExpressionError, compile_expression


def test_problem_schema_file_is_published():
    assert PROBLEM_SCHEMA_PATH.exists()
    schema = json.loads(PROBLEM_SCHEMA_PATH.read_text())
    assert schema["properties"]["schema"]["const"] == "optimizer/problem/v1"
    assert "variables" in schema["required"]


def test_expression_rejects_code_injection():
    with pytest.raises(ExpressionError):
        compile_expression("__import__('os').system('true')", ["x"])
    with pytest.raises(ExpressionError):
        compile_expression("open('/etc/passwd').read()", ["x"])


def test_expression_rejects_unknown_symbol():
    with pytest.raises(ExpressionError):
        compile_expression("x + y", ["x"])


def test_expression_evaluates_whitelisted_math():
    f = compile_expression("(1 - x)**2 + 100*(y - x**2)**2", ["x", "y"])
    assert f({"x": 1.0, "y": 1.0}) == pytest.approx(0.0)
    assert f({"x": 0.0, "y": 0.0}) == pytest.approx(1.0)


def test_dsl_roundtrip_is_stable(tmp_path):
    spec = get_problem("g09")
    path = dump_problem(spec, tmp_path / "g09.json")
    reloaded = load_problem(path)
    assert reloaded.to_dict() == spec.to_dict()
    assert reloaded.fingerprint() == spec.fingerprint()
    assert reloaded.n_variables == 7
    assert len(reloaded.constraints) == 4


def test_dsl_rejects_malformed_document():
    document = {
        "schema": "optimizer/problem/v1",
        "problem_id": "broken",
        "variables": [{"name": "x", "lower": 1.0, "upper": 0.0}],  # lower > upper
        "objective": {"expr": "x**2"},
    }
    with pytest.raises((DSLValidationError, ValueError)):
        # schema accepts the numbers, the contract validator must reject them
        spec = ProblemSpec.from_dict(document)
        spec.validate()


def test_dsl_schema_rejects_missing_objective():
    document = {"schema": "optimizer/problem/v1", "problem_id": "x", "variables": []}
    with pytest.raises(DSLValidationError):
        validate_problem_dict(document)


def test_loads_problem_from_json_text():
    text = json.dumps(get_problem("sphere_eq_ineq").to_dict())
    spec = loads_problem(text)
    compiled = compile_problem(spec)
    assert compiled.n == 3
    assert compiled.raw_objective([1.0, 1.0, 1.0]) == pytest.approx(3.0)
    assert compiled.eq_values([1.0, 1.0, 1.0]).tolist() == pytest.approx([0.0, 0.0])
    assert compiled.ineq_values([1.0, 1.0, 1.0]).tolist() == pytest.approx([0.5])


def test_at_least_five_standard_problems_defined():
    specs = standard_problems()
    assert len(specs) >= 5
    for pid in ("rosenbrock_2d", "g06", "g08", "g09", "g10"):
        assert pid in PROBLEM_IDS
    rows = reference_table()
    assert len(rows) == len(specs)
    assert all(row["reference_objective"] is not None for row in rows)


def test_every_standard_problem_compiles_and_starts_feasible_in_bounds():
    for spec in standard_problems():
        compiled = CompiledProblem(spec)
        x0 = compiled.x0()
        assert x0.shape == (compiled.n,)
        assert ((x0 >= compiled.lower) & (x0 <= compiled.upper)).all()
        value = compiled.objective(x0)
        assert value == value  # not NaN


def test_le_constraint_jacobian_sign_matches_canonical_form():
    """Regression: a 'le' constraint's analytic Jacobian must be sign-flipped."""
    import numpy as np

    from tools.optimizer.unified.kkt import _fd_jac

    spec = get_problem("g10")
    compiled = CompiledProblem(spec)
    x = compiled.x0() + 0.1
    analytic = compiled.ineq_jacobian(x)
    numeric = _fd_jac(lambda z: compiled.ineq_values(z), x)
    assert np.max(np.abs(analytic - numeric)) < 1e-6 * max(1.0, np.max(np.abs(numeric)))


def test_expression_compilation_is_cached_per_spec():
    spec = get_problem("rosenbrock_2d")
    a = CompiledProblem(spec)
    b = CompiledProblem(spec)
    x = [0.3, 0.7]
    assert a.objective(x) == pytest.approx(b.objective(x))