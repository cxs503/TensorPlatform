"""Certified fixed-point iteration for coupled systems (P351-W1-N01).

Acceptance criteria exercised here:

(c) a loop with contraction factor 1.2 must be declared DIVERGED within 10
    iterations instead of burning the iteration cap;
(d) Aitken relaxation must cut the iteration count by at least 40 %;
(e) the analytic sensitivity dx*/dp agrees with finite differences within 2 %;
plus the certificate contents and the block-partition contract.
"""

from __future__ import annotations

import math

import numpy as np
import pytest

from tools.coupled_loop import (
    CONVERGED,
    DIVERGED,
    MAX_ITERATIONS,
    Certificate,
    linear_blocks,
    meets_loop_gate,
    solve_coupled,
    solve_fixed_point,
    solve_linear_coupled,
)

#: symmetric exchange matrix with spectral radius 0.95 and no self-coupling
A_CONTRACTING = np.array([[0.0, 0.95], [0.95, 0.0]])
B_CONTRACTING = np.array([1.0, -0.5])
#: same structure with spectral radius 1.2 — must be caught as divergent
A_DIVERGING = np.array([[0.0, 1.2], [1.2, 0.0]])

TOL = 1e-10


@pytest.fixture(scope="module")
def jacobi_solve():
    return solve_linear_coupled(A_CONTRACTING, B_CONTRACTING, relaxation="none",
                                tolerance=TOL, max_iterations=2000)


@pytest.fixture(scope="module")
def aitken_solve():
    return solve_linear_coupled(A_CONTRACTING, B_CONTRACTING, relaxation="aitken",
                                tolerance=TOL, max_iterations=2000)


def test_jacobi_converges_to_the_direct_solution(jacobi_solve):
    solution, cert = jacobi_solve
    reference = np.linalg.solve(np.eye(2) - A_CONTRACTING, B_CONTRACTING)
    assert cert.status == CONVERGED and cert.converged
    assert np.allclose(solution, reference, rtol=1e-8, atol=1e-10)


def test_contraction_estimate_matches_the_spectral_radius(jacobi_solve):
    _, cert = jacobi_solve
    rho_true = float(np.max(np.abs(np.linalg.eigvals(A_CONTRACTING))))
    assert cert.rho_hat == pytest.approx(rho_true, abs=0.01)
    assert cert.rho_hat < 1.0


def test_jacobi_certificate_shape(jacobi_solve):
    _, cert = jacobi_solve
    record = cert.to_dict()
    for key in ("converged", "status", "rho_hat", "iterations", "final_residual",
                "residual_series", "scheme", "relaxation", "omega"):
        assert key in record
    assert record["scheme"] == "jacobi"
    assert record["iterations"] == len(record["residual_series"])
    assert record["final_residual"] <= TOL * 10


def test_gauss_seidel_beats_jacobi(jacobi_solve):
    _, jacobi = jacobi_solve
    solution, gs = solve_linear_coupled(A_CONTRACTING, B_CONTRACTING, scheme="gauss_seidel",
                                        tolerance=TOL, max_iterations=2000)
    reference = np.linalg.solve(np.eye(2) - A_CONTRACTING, B_CONTRACTING)
    assert gs.status == CONVERGED
    assert np.allclose(solution, reference, rtol=1e-8, atol=1e-10)
    assert gs.iterations < jacobi.iterations
    # for a two-block consistently ordered system rho_GS = rho_J^2
    assert gs.rho_hat == pytest.approx(jacobi.rho_hat ** 2, rel=0.05)


def test_aitken_reduces_iterations_by_at_least_40_percent(jacobi_solve, aitken_solve):
    """Criterion (d)."""
    _, jacobi = jacobi_solve
    _, aitken = aitken_solve
    reduction = 1.0 - aitken.iterations / jacobi.iterations
    assert reduction >= 0.40, (jacobi.iterations, aitken.iterations, reduction)
    assert aitken.status == CONVERGED


def test_aitken_solution_is_the_same_fixed_point(jacobi_solve, aitken_solve):
    base, _ = jacobi_solve
    accelerated, cert = aitken_solve
    assert np.allclose(accelerated, base, rtol=1e-8, atol=1e-10)
    assert cert.rho_hat < 1.0


def test_aitken_relaxes_the_factor_towards_one_over_one_minus_rho(aitken_solve):
    _, cert = aitken_solve
    # the optimal relaxation of a linear map is 1/(1-rho); the factor is clamped
    assert 1.0 < cert.omega <= 5.0
    assert cert.omega > 1.5


def test_divergent_system_is_declared_within_ten_iterations():
    """Criterion (c)."""
    _, cert = solve_linear_coupled(A_DIVERGING, B_CONTRACTING, tolerance=TOL,
                                   max_iterations=500)
    assert cert.status == DIVERGED and not cert.converged
    assert cert.iterations <= 10, cert.to_dict()
    assert cert.rho_hat > 1.0
    assert "contraction estimate" in cert.reason


def test_divergence_is_not_confused_with_the_iteration_cap():
    _, cert = solve_linear_coupled(A_DIVERGING, B_CONTRACTING, tolerance=TOL,
                                   max_iterations=3, patience=10)
    assert cert.status in (MAX_ITERATIONS, DIVERGED)
    assert cert.iterations <= 3


def test_fixed_point_api_solves_a_nonlinear_map():
    map_fn = lambda x: np.array([0.5 * math.cos(x[1]), 0.5 * math.sin(x[0])])
    solution, cert = solve_fixed_point(map_fn, [0.1, 0.2], relaxation="aitken",
                                       tolerance=1e-12, max_iterations=500)
    assert cert.status == CONVERGED
    assert np.allclose(solution, map_fn(solution), atol=1e-11)


def test_fixed_point_rejects_an_unknown_relaxation():
    with pytest.raises(ValueError):
        solve_fixed_point(lambda x: x, [0.0], relaxation="sor")


def test_blocks_must_partition_the_state():
    blocks = [([0], lambda x: x[[0]] + 0.1), ([1], lambda x: x[[1]] + 0.0)]
    with pytest.raises(ValueError):
        solve_coupled(blocks, [0.0, 0.0, 0.0])
    overlapping = [([0, 1], lambda x: x[[0, 1]]), ([1], lambda x: x[[1]])]
    with pytest.raises(ValueError):
        solve_coupled(overlapping, [0.0, 0.0])


def test_three_block_gauss_seidel_converges():
    matrix = np.array([[0.0, 0.2, 0.1], [0.2, 0.0, 0.2], [0.1, 0.2, 0.0]])
    rhs = np.array([1.0, 2.0, 3.0])
    blocks = linear_blocks(matrix, rhs, [[0], [1], [2]])
    solution, cert = solve_coupled(blocks, np.zeros(3), scheme="gauss_seidel",
                                   tolerance=1e-11, max_iterations=500)
    assert cert.status == CONVERGED
    assert np.allclose(solution, np.linalg.solve(np.eye(3) - matrix, rhs), rtol=1e-9)


def test_certificate_serialises_to_plain_python():
    cert = Certificate(
        converged=True, status=CONVERGED, rho_hat=0.5, iterations=7,
        final_residual=1e-12, residual_series=(1.0, 0.5, 1e-12),
    )
    record = cert.to_dict()
    assert isinstance(record["rho_hat"], float)
    assert all(isinstance(v, float) for v in record["residual_series"])


def test_loop_gate_accepts_a_converged_certificate(jacobi_solve):
    _, cert = jacobi_solve
    gate = {"rho_max": 1.0, "max_iterations": 2000, "tolerance": TOL}
    record = meets_loop_gate(cert, gate)
    assert record["passed"], record


def test_loop_gate_rejects_a_divergent_certificate():
    _, cert = solve_linear_coupled(A_DIVERGING, B_CONTRACTING, tolerance=TOL,
                                   max_iterations=500)
    record = meets_loop_gate(cert, {"rho_max": 1.0, "max_iterations": 500, "tolerance": TOL})
    assert not record["passed"]
    assert not record["checks"]["converged"]
    assert not record["checks"]["rho_below_max"]


def test_loop_gate_rejects_a_too_loose_iteration_cap(jacobi_solve):
    _, cert = jacobi_solve
    record = meets_loop_gate(cert, {"rho_max": 1.0, "max_iterations": 5, "tolerance": TOL})
    assert not record["passed"]
    assert not record["checks"]["within_iteration_cap"]


def test_solution_sensitivity_matches_the_analytic_derivative():
    """Criterion (e): dx*/dp against (I - A)^{-1} dp, within 2 %."""
    matrix = A_CONTRACTING
    base_rhs = B_CONTRACTING.copy()
    direction = np.array([1.0, 0.5])
    inverse = np.linalg.inv(np.eye(2) - matrix)
    analytic = inverse @ direction

    def state(p: float) -> np.ndarray:
        solution, cert = solve_linear_coupled(matrix, base_rhs + p * direction,
                                              tolerance=1e-13, max_iterations=5000)
        assert cert.status == CONVERGED
        return solution

    step = 1e-6
    numeric = (state(step) - state(-step)) / (2.0 * step)
    relative = np.linalg.norm(numeric - analytic) / np.linalg.norm(analytic)
    assert relative <= 0.02, relative
    assert relative < 1e-5


def test_tolerance_must_be_positive():
    with pytest.raises(ValueError):
        solve_fixed_point(lambda x: x, [0.0], tolerance=0.0)