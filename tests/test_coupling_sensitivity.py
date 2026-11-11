"""Sensitivity-consistency gate for coupled systems (P350-W2-N09, criterion f).

The gate compares two derivatives of the same coupled system against each other:

* ``composed`` — assembled from per-discipline partial derivatives taken with the
  coupling variables frozen at the converged state, chained through the coupling
  response matrix;
* ``resolved`` — obtained by re-converging the loop at ``p ± h`` (the ground
  truth).

Criterion (f) demands they agree within 2 %, and demands that the gate *fails*
when one discipline's derivative is deliberately broken.  Both the positive and
the negative side are asserted here, and the analytic anchor of the linear
two-discipline fixture is used as a third, independent reference.
"""

from __future__ import annotations

import math

import numpy as np
import pytest

from tools.coupling.sensitivity import (
    CoupledSystem,
    DeclaredPartials,
    Discipline,
    LinearTwoDisciplineSystem,
    SensitivityError,
    compose_sensitivity,
    constant_partials,
    frozen_partials,
    linear_two_discipline_system,
    measure_output_gradient,
    nonlinear_two_discipline_system,
    resolve_sensitivity,
    scale_declared_partials,
    sensitivity_consistency_gate,
)

RTOL = 0.02


def _analytic_fixture(rho: float = 0.5) -> LinearTwoDisciplineSystem:
    return linear_two_discipline_system(rho)


# ---------------------------------------------------------------------------
# analytic anchor
# ---------------------------------------------------------------------------


def test_linear_anchor_spectral_radius_is_the_declared_rho():
    fixture = _analytic_fixture(0.5)
    assert fixture.spectral_radius == pytest.approx(0.5, rel=1e-12)


def test_linear_anchor_analytic_sensitivity_matches_hand_calculation():
    """(I-A)^-1 b1 = [2, 0]; dF/dp = c.(...) + cp = 1.5*2 + 0.25 = 3.25."""
    fixture = _analytic_fixture(0.5)
    assert fixture.analytic_sensitivity == pytest.approx(3.25, rel=1e-12)
    assert fixture.analytic_solution_derivative == pytest.approx(np.array([2.0, 0.0]), abs=1e-12)


def test_composed_sensitivity_reproduces_the_analytic_state_derivative():
    fixture = _analytic_fixture(0.5)
    system = fixture.to_system()
    state, _ = system.solve([1.0])
    record = compose_sensitivity(system, state, [1.0])
    assert np.allclose(record["state_sensitivity"], fixture.analytic_solution_derivative, atol=1e-7)


def test_composed_sensitivity_matches_the_analytic_value_within_two_percent():
    fixture = _analytic_fixture(0.5)
    record = sensitivity_consistency_gate(
        fixture.to_system(), [1.0], analytic=fixture.analytic_sensitivity, rtol=RTOL
    )
    assert record["relative_error_composed_vs_analytic"] <= RTOL
    assert record["passed"], record["checks"]


def test_gate_record_carries_both_derivative_paths_and_the_converged_state():
    fixture = _analytic_fixture(0.5)
    record = sensitivity_consistency_gate(fixture.to_system(), [1.0], analytic=3.25)
    assert record["relative_error"] <= RTOL
    assert record["checks"]["composed_matches_total"] is True
    assert record["checks"]["total_matches_analytic"] is True
    assert record["iterations"] >= 1
    assert 0.0 < record["rho_hat"] < 1.0
    assert len(record["state"]) == 2
    assert record["detail"]["composed"]["resolvent_condition"] >= 1.0


def test_nonlinear_two_discipline_system_passes_the_gate():
    system = nonlinear_two_discipline_system()
    record = sensitivity_consistency_gate(system, [0.5], rtol=RTOL)
    assert record["passed"], record
    assert record["relative_error"] <= RTOL
    assert math.isfinite(record["composed"])


# ---------------------------------------------------------------------------
# negative controls: the gate must be falsifiable
# ---------------------------------------------------------------------------


def _corrupted(state_scale: float = 1.0, param_scale: float = 1.0) -> CoupledSystem:
    base = linear_two_discipline_system(0.5).to_system()
    return CoupledSystem(
        disciplines=[
            base.disciplines[0],
            scale_declared_partials(base.disciplines[1], state_scale=state_scale, param_scale=param_scale),
        ],
        output=base.output,
        name="corrupted",
    )


def test_corrupted_coupling_jacobian_fails_the_gate():
    record = sensitivity_consistency_gate(_corrupted(state_scale=1.5), [1.0], rtol=RTOL)
    assert record["passed"] is False
    assert record["checks"]["composed_matches_total"] is False
    assert record["relative_error"] > RTOL


def test_corrupted_parameter_derivative_fails_the_gate():
    record = sensitivity_consistency_gate(_corrupted(param_scale=1.25), [1.0], rtol=RTOL)
    assert record["passed"] is False
    assert record["relative_error"] > RTOL


def test_gate_is_not_blindly_pessimistic_small_error_still_passes():
    """A 1 % bookkeeping error is inside the 2 % tolerance: the gate must accept it."""
    record = sensitivity_consistency_gate(_corrupted(state_scale=1.002), [1.0], rtol=RTOL)
    assert record["passed"] is True, record["relative_error"]
    assert record["relative_error"] < RTOL


def test_gate_tolerance_actually_moves_the_decision_boundary():
    """The same record flips when the tolerance is tightened: the threshold is live."""
    corrupted = _corrupted(state_scale=1.15)
    loose = sensitivity_consistency_gate(corrupted, [1.0], rtol=0.20)
    tight = sensitivity_consistency_gate(corrupted, [1.0], rtol=RTOL)
    assert loose["passed"] is True
    assert tight["passed"] is False


def test_corrupting_the_statistics_does_not_move_the_resolved_derivative():
    """The fault lives in the bookkeeping only: the ground truth must be unchanged."""
    clean = sensitivity_consistency_gate(linear_two_discipline_system(0.5).to_system(), [1.0])
    corrupted = sensitivity_consistency_gate(_corrupted(state_scale=1.5), [1.0])
    assert corrupted["total"] == pytest.approx(clean["total"], rel=1e-9)
    assert corrupted["composed"] != pytest.approx(clean["composed"], rel=1e-6)


def test_singular_coupling_response_is_rejected():
    """Declared Jacobians that make (I-K) singular cannot produce a sensitivity."""
    base = linear_two_discipline_system(0.5).to_system()
    singular = CoupledSystem(
        disciplines=[
            scale_declared_partials(base.disciplines[0], state_scale=2.0),
            scale_declared_partials(base.disciplines[1], state_scale=2.0),
        ],
        output=base.output,
        name="singular",
    )
    with pytest.raises(SensitivityError, match="singular"):
        sensitivity_consistency_gate(singular, [1.0])


def test_unconverged_loop_is_rejected_before_any_derivative_is_quoted():
    divergent = linear_two_discipline_system(1.2).to_system()
    with pytest.raises(SensitivityError, match="did not converge"):
        sensitivity_consistency_gate(divergent, [1.0], solve_kwargs={"max_iterations": 50})


# ---------------------------------------------------------------------------
# building blocks
# ---------------------------------------------------------------------------


def test_frozen_partials_are_measured_discipline_by_discipline():
    system = linear_two_discipline_system(0.5).to_system()
    state, _ = system.solve([1.0])
    k_matrix, g_vector, records = frozen_partials(system, state, [1.0])
    assert k_matrix.shape == (2, 2)
    assert np.allclose(k_matrix[0], np.array([0.0, 0.5]), atol=1e-6)
    assert np.allclose(k_matrix[1], np.array([0.5, 0.0]), atol=1e-6)
    assert np.allclose(g_vector, np.array([2.0, -1.0]), atol=1e-6)
    assert [r["discipline"] for r in records] == ["block0", "block1"]
    assert all(r["source"] == "measured" for r in records)


def test_declared_partials_are_used_instead_of_measured_ones():
    base = linear_two_discipline_system(0.5).to_system()
    declared = constant_partials([[0.0, 0.5]], [2.0])
    disciplines = [
        Discipline(name="block0", update=base.disciplines[0].update, state_size=1, declared_partials=declared),
        base.disciplines[1],
    ]
    system = CoupledSystem(disciplines=disciplines, output=base.output)
    state, _ = system.solve([1.0])
    _, _, records = frozen_partials(system, state, [1.0])
    assert records[0]["source"] == "declared"
    assert records[0]["state_jacobian"] == [[0.0, 0.5]]


def test_declared_partials_with_wrong_shape_are_rejected():
    base = linear_two_discipline_system(0.5).to_system()
    bad = DeclaredPartials(
        state_jacobian=lambda x, p: np.zeros((1, 5)),
        param_jacobian=lambda x, p: np.zeros((1, 1)),
    )
    system = CoupledSystem(
        disciplines=[
            Discipline(name="block0", update=base.disciplines[0].update, state_size=1, declared_partials=bad),
            base.disciplines[1],
        ],
        output=base.output,
    )
    state, _ = system.solve([1.0])
    with pytest.raises(SensitivityError, match="shape"):
        frozen_partials(system, state, [1.0])


def test_output_gradient_matches_closed_form():
    system = linear_two_discipline_system(0.5).to_system()
    state, _ = system.solve([1.0])
    grad, d_param = measure_output_gradient(system, state, [1.0])
    assert np.allclose(grad, np.array([1.5, 0.5]), atol=1e-6)
    assert d_param == pytest.approx(0.25, abs=1e-6)


def test_resolve_sensitivity_converges_on_both_sides_of_the_perturbation():
    system = linear_two_discipline_system(0.5).to_system()
    record = resolve_sensitivity(system, [1.0])
    assert record["converged_plus"] and record["converged_minus"]
    assert record["value"] == pytest.approx(3.25, rel=1e-5)


def test_system_geometry_errors_are_fail_closed():
    base = linear_two_discipline_system(0.5).to_system()
    with pytest.raises(SensitivityError, match="unique"):
        CoupledSystem(disciplines=[base.disciplines[0], base.disciplines[0]], output=base.output)
    with pytest.raises(SensitivityError, match="state has size"):
        base.map(np.zeros(5), np.zeros(1))
    with pytest.raises(SensitivityError, match="scalar"):
        CoupledSystem(disciplines=list(base.disciplines), output=lambda x, p: np.zeros(3)).value(
            np.zeros(2), np.zeros(1)
        )