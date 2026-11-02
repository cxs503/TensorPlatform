"""Regression tests for the public API / benchmark resistance call-chain unification."""
from __future__ import annotations

import ast
import sys
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(BACKEND))

from services.ship_resistance import MODEL_ID, resistance_breakdown


def _case(**overrides):
    args = {
        "length": 4.356,
        "beam": 0.508,
        "draft": 0.476,
        "cb": 0.542,
        "speed": 3.0,
        "rho": 998.0,
        "nu": 1.004e-6,
    }
    args.update(overrides)
    return resistance_breakdown(**args)


def test_model_is_explicitly_named_and_never_claims_validation():
    result = _case()
    assert MODEL_ID == "suboff_axisymmetric_empirical_v1"
    assert result["model_id"] == MODEL_ID
    assert result["validity_status"] == "unvalidated_empirical_estimate"
    assert any("does not establish physical validation" in note for note in result["validity_notes"])


def test_caller_surface_is_used_but_not_claimed_as_verified():
    result = _case(wetted_surface_m2=5.42)
    assert result["wetted_surface_m2"] == pytest.approx(5.42)
    assert result["surface_source"] == "caller_supplied_geometry_value"
    assert any("not independently validated" in note for note in result["validity_notes"])


def test_form_factor_and_force_decomposition_are_consistent():
    base = _case(form_factor=1.0)
    with_form = _case(form_factor=1.2)

    assert with_form["frictional_resistance_n"] == pytest.approx(base["frictional_resistance_n"])
    assert with_form["form_resistance_n"] == pytest.approx(0.2 * with_form["frictional_resistance_n"])
    assert with_form["total_resistance_n"] == pytest.approx(
        with_form["frictional_resistance_n"]
        + with_form["form_resistance_n"]
        + with_form["residual_resistance_n"]
    )
    q = 0.5 * 998.0 * with_form["wetted_surface_m2"] * 3.0**2
    assert with_form["ct"] == pytest.approx(with_form["total_resistance_n"] / q)


def test_out_of_fit_range_is_reported_not_silently_hidden():
    result = _case(length=320.0, beam=58.0, draft=20.8, cb=0.8098, speed=15.0 * 0.5144)
    assert result["validity_status"] == "unvalidated_empirical_estimate"
    assert any("outside the documented" in note for note in result["validity_notes"])


def test_low_reynolds_number_does_not_silently_return_zero_friction():
    with pytest.raises(ValueError, match="too small for ITTC-1957"):
        _case(length=1.0, beam=0.2, draft=0.1, cb=0.5, speed=1e-7)


def _function_calls(path: Path, function_name: str, call_name: str) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    function = next(
        node for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name == function_name
    )
    found = set()
    for node in ast.walk(function):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == call_name:
            found.update(keyword.arg for keyword in node.keywords if keyword.arg)
    return found


def test_public_endpoint_delegates_physics_to_shared_kernel():
    calls = _function_calls(BACKEND / "api" / "calculations.py", "calc_resistance", "resistance_breakdown")
    assert {"length", "beam", "draft", "cb", "speed", "rho", "nu", "wetted_surface_m2", "form_factor"} <= calls


def test_benchmark_path_uses_the_same_shared_kernel():
    calls = _function_calls(BACKEND / "api" / "domain" / "ship_benchmark.py", "_call_resistance_api", "resistance_breakdown")
    assert {"length", "beam", "draft", "cb", "speed", "rho", "nu"} <= calls
