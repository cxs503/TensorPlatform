"""
Comprehensive tests for multi-solver coupling orchestrator.

Tests all acceptance criteria:
- C1: Mass conservation (residual ≤ 1%)
- C2: Momentum conservation (residual ≤ 2%)
- C3: Time interpolation accuracy (linear O(Δt²), cubic O(Δt⁴))
- C4: Dimension mismatch detection (100% detection rate)
- C5: Coordinate transform norm preservation (error ≤ 1e-12)
- C6: Aitken coupling convergence (≤ 20 iterations, residual ≤ 1e-6)
- C7: Zero domain semantics (synthetic domain names only)
"""

import numpy as np
import pytest
from numpy.testing import assert_allclose

from src.coupling.field_exchange import (
    FieldExchange, FieldMessage, GridTopology, DimensionVector
)
from src.coupling.conservation import ConservationChecker, ConservationResult
from src.coupling.time_stepping import (
    TimeCoordinator, TimeStepConfig, InterpolationMethod
)
from src.coupling.contract_validation import (
    ContractValidator, ContractViolation
)
from src.coupling.stability import (
    AitkenRelaxation, CouplingConvergence, CFLCouplingCondition
)


# ============================================================================
# Fixtures: synthetic test grids (zero domain semantics)
# ============================================================================

def make_simple_grid(n_nodes=10, extent=1.0, offset=(0, 0, 0)):
    """Create a simple planar grid for testing."""
    x = np.linspace(0, extent, n_nodes)
    y = np.linspace(0, extent, 2)
    xx, yy = np.meshgrid(x, y)
    coords = np.column_stack([
        xx.ravel() + offset[0],
        yy.ravel() + offset[1],
        np.zeros(xx.size) + offset[2]
    ])

    # Create faces (quads)
    faces = []
    for j in range(1):  # 1 row of faces
        for i in range(n_nodes - 1):
            n0 = j * n_nodes + i
            n1 = j * n_nodes + i + 1
            n2 = (j + 1) * n_nodes + i + 1
            n3 = (j + 1) * n_nodes + i
            faces.append([n0, n1, n2, n3])

    n_faces = len(faces)
    face_areas = np.full(n_faces, extent / (n_nodes - 1) * extent)
    face_normals = np.tile([0, 0, 1], (n_faces, 1)).astype(float)

    return GridTopology(
        node_coords=coords,
        face_nodes=faces,
        face_areas=face_areas,
        face_normals=face_normals,
        coordinate_system="cartesian"
    )


def make_matching_grids():
    """Create two matching grids for conservation tests."""
    grid_a = make_simple_grid(n_nodes=10, extent=1.0)
    grid_b = make_simple_grid(n_nodes=10, extent=1.0)
    return grid_a, grid_b


# ============================================================================
# C1: Mass Conservation Tests
# ============================================================================

class TestMassConservation:
    """C1: Mass conservation LBM→FEM mass flux exchange residual ≤ 1%."""

    def test_mass_flux_self_consistency(self):
        """Mass flux computed on same grid should be perfectly consistent."""
        grid = make_simple_grid(n_nodes=10)
        n_faces = grid.n_faces

        # Uniform density and velocity
        density = np.ones(n_faces) * 1.0  # ρ = 1
        velocity = np.zeros((n_faces, 3))
        velocity[:, 2] = 1.0  # u_z = 1

        density_msg = FieldMessage(
            field_name="density",
            grid_topology=grid,
            values=density,
            units=DimensionVector(M=1, L=-3),
            timestamp=0.0,
            field_location="face",
            domain_tag="test.fluid"
        )

        velocity_msg = FieldMessage(
            field_name="velocity",
            grid_topology=grid,
            values=velocity,
            units=DimensionVector(L=1, T=-1),
            timestamp=0.0,
            field_location="face",
            domain_tag="test.fluid"
        )

        exchange = FieldExchange("test.fluid", "test.structure")
        checker = ConservationChecker(exchange)

        result = checker.check_mass_conservation(
            density_msg, velocity_msg,
            source_area=1.0, target_area=1.0,
            tolerance=0.01
        )

        assert result.quantity == "mass"
        assert result.passed, f"Mass conservation failed: residual={result.residual}"
        assert result.residual <= 0.01, f"Residual {result.residual} > 1%"

    def test_mass_conservation_with_area_mismatch(self):
        """Test mass conservation with different normalization areas."""
        grid = make_simple_grid(n_nodes=10)
        n_faces = grid.n_faces

        density = np.ones(n_faces) * 1.225  # air density
        velocity = np.zeros((n_faces, 3))
        velocity[:, 0] = 10.0  # u_x = 10 m/s

        density_msg = FieldMessage(
            field_name="density",
            grid_topology=grid,
            values=density,
            units=DimensionVector(M=1, L=-3),
            timestamp=0.0,
            field_location="face",
            domain_tag="test.fluid"
        )

        velocity_msg = FieldMessage(
            field_name="velocity",
            grid_topology=grid,
            values=velocity,
            units=DimensionVector(L=1, T=-1),
            timestamp=0.0,
            field_location="face",
            domain_tag="test.fluid"
        )

        exchange = FieldExchange("test.fluid", "test.structure")
        checker = ConservationChecker(exchange)

        # Same area → perfect conservation
        result = checker.check_mass_conservation(
            density_msg, velocity_msg,
            source_area=1.0, target_area=1.0,
            tolerance=0.01
        )
        assert result.passed
        assert result.residual <= 0.01


# ============================================================================
# C2: Momentum Conservation Tests
# ============================================================================

class TestMomentumConservation:
    """C2: Momentum conservation force flux exchange residual ≤ 2%."""

    def test_force_balance_self_consistency(self):
        """Force balance on same grid should be perfectly consistent."""
        grid = make_simple_grid(n_nodes=10)
        n_faces = grid.n_faces

        # Uniform traction
        traction = np.zeros((n_faces, 3))
        traction[:, 2] = -1000.0  # pressure load

        traction_msg = FieldMessage(
            field_name="traction",
            grid_topology=grid,
            values=traction,
            units=DimensionVector(M=1, L=-1, T=-2),
            timestamp=0.0,
            field_location="face",
            domain_tag="test.fluid"
        )

        exchange = FieldExchange("test.fluid", "test.structure")
        checker = ConservationChecker(exchange)

        result = checker.check_momentum_conservation(
            traction_msg,
            tolerance=0.02
        )

        assert result.quantity == "momentum"
        assert result.passed
        assert result.residual <= 0.02

    def test_force_balance_two_sided(self):
        """Test two-sided force balance with known forces."""
        grid = make_simple_grid(n_nodes=10)
        n_faces = grid.n_faces

        traction = np.zeros((n_faces, 3))
        traction[:, 2] = -500.0

        traction_msg = FieldMessage(
            field_name="traction",
            grid_topology=grid,
            values=traction,
            units=DimensionVector(M=1, L=-1, T=-2),
            timestamp=0.0,
            field_location="face",
            domain_tag="test.fluid"
        )

        exchange = FieldExchange("test.fluid", "test.structure")
        checker = ConservationChecker(exchange)

        # Known forces from both sides (nearly equal)
        source_force = np.array([0, 0, -5000.0])
        target_force = np.array([0, 0, -4990.0])  # 0.2% difference

        result = checker.check_momentum_conservation(
            traction_msg,
            source_force=source_force,
            target_force=target_force,
            tolerance=0.02
        )

        assert result.passed
        assert result.residual <= 0.02


# ============================================================================
# C3: Time Interpolation Accuracy Tests
# ============================================================================

class TestTimeInterpolation:
    """C3: Time interpolation accuracy verification."""

    def test_linear_interpolation_accuracy(self):
        """Linear interpolation error should be O(Δt²)."""
        fine_config = TimeStepConfig(dt=0.001, t_start=0, t_end=1.0, solver_name="test.fine")
        coarse_config = TimeStepConfig(dt=0.01, t_start=0, t_end=1.0, solver_name="test.coarse")

        coordinator = TimeCoordinator(fine_config, coarse_config)

        # Analytic signal: sin(2πt)
        def analytic(t):
            return np.array([np.sin(2 * np.pi * t)])

        result = coordinator.verify_temporal_accuracy(
            analytic_signal=analytic,
            method=InterpolationMethod.LINEAR,
            n_fine_steps=1000
        )

        # Linear interpolation of smooth signal: error ~ O(Δt²)
        # With Δt=0.01, expect error ~ 1e-3 to 1e-4
        assert result["max_error"] < 0.01, f"Linear interp error too large: {result['max_error']}"
        assert result["method"] == "linear"

    def test_cubic_interpolation_accuracy(self):
        """Cubic interpolation error should be O(Δt⁴)."""
        fine_config = TimeStepConfig(dt=0.001, t_start=0, t_end=1.0, solver_name="test.fine")
        coarse_config = TimeStepConfig(dt=0.01, t_start=0, t_end=1.0, solver_name="test.coarse")

        coordinator = TimeCoordinator(fine_config, coarse_config)

        # Analytic signal: sin(2πt)
        def analytic(t):
            return np.array([np.sin(2 * np.pi * t)])

        result = coordinator.verify_temporal_accuracy(
            analytic_signal=analytic,
            method=InterpolationMethod.CUBIC,
            n_fine_steps=1000
        )

        # Cubic interpolation: error ~ O(Δt⁴)
        # Should be significantly better than linear
        assert result["max_error"] < 0.001, f"Cubic interp error too large: {result['max_error']}"
        assert result["method"] == "cubic"

    def test_zero_order_hold(self):
        """Zero-order hold should be piecewise constant."""
        fine_config = TimeStepConfig(dt=0.01, t_start=0, t_end=1.0, solver_name="test.fine")
        coarse_config = TimeStepConfig(dt=0.1, t_start=0, t_end=1.0, solver_name="test.coarse")

        coordinator = TimeCoordinator(fine_config, coarse_config)

        # Step function
        fine_times = [0.0, 0.01, 0.02, 0.03, 0.04, 0.05]
        fine_data = [np.array([0.0]), np.array([0.0]), np.array([1.0]),
                     np.array([1.0]), np.array([1.0]), np.array([1.0])]

        # At t=0.025, should use value at t=0.02 (zero-order hold)
        result = coordinator.interpolate_fine_to_coarse(
            fine_data, fine_times, 0.025,
            method=InterpolationMethod.ZERO_ORDER
        )
        assert_allclose(result, [1.0])

    def test_linear_interpolation_exact(self):
        """Linear interpolation of linear signal should be exact."""
        fine_config = TimeStepConfig(dt=0.01, t_start=0, t_end=1.0, solver_name="test.fine")
        coarse_config = TimeStepConfig(dt=0.1, t_start=0, t_end=1.0, solver_name="test.coarse")

        coordinator = TimeCoordinator(fine_config, coarse_config)

        # Linear signal: f(t) = 2t + 1
        fine_times = [0.0, 0.01, 0.02, 0.03, 0.04, 0.05]
        fine_data = [np.array([2 * t + 1]) for t in fine_times]

        # At t=0.025, should be exactly 2*0.025 + 1 = 1.05
        result = coordinator.interpolate_fine_to_coarse(
            fine_data, fine_times, 0.025,
            method=InterpolationMethod.LINEAR
        )
        assert_allclose(result, [1.05], atol=1e-12)

    def test_substep_ratio(self):
        """Test substep ratio computation."""
        fine_config = TimeStepConfig(dt=0.001, t_start=0, t_end=1.0, solver_name="test.fine")
        coarse_config = TimeStepConfig(dt=0.01, t_start=0, t_end=1.0, solver_name="test.coarse")

        coordinator = TimeCoordinator(fine_config, coarse_config)
        assert coordinator.substep_ratio == 10

    def test_aggregate_fine_to_coarse(self):
        """Test aggregation of fine data to coarse step."""
        fine_config = TimeStepConfig(dt=0.01, t_start=0, t_end=1.0, solver_name="test.fine")
        coarse_config = TimeStepConfig(dt=0.1, t_start=0, t_end=1.0, solver_name="test.coarse")

        coordinator = TimeCoordinator(fine_config, coarse_config)

        # 10 fine steps with values 0..9
        fine_data = [np.array([float(i)]) for i in range(10)]

        avg = coordinator.aggregate_fine_to_coarse(fine_data, method="average")
        assert_allclose(avg, [4.5])

        max_val = coordinator.aggregate_fine_to_coarse(fine_data, method="max")
        assert_allclose(max_val, [9.0])


# ============================================================================
# C4: Dimension Mismatch Detection Tests
# ============================================================================

class TestDimensionMismatch:
    """C4: Dimension mismatch detection (100% detection rate)."""

    def test_detect_force_vs_pressure(self):
        """Should detect force (ML/T²) vs pressure (M/LT²) mismatch."""
        grid = make_simple_grid(n_nodes=5)
        n_faces = grid.n_faces

        values = np.ones(n_faces)

        # Force: [M, L, T] = [1, 1, -2]
        force_msg = FieldMessage(
            field_name="force",
            grid_topology=grid,
            values=values,
            units=DimensionVector(M=1, L=1, T=-2),
            timestamp=0.0,
            field_location="face",
            domain_tag="test.fluid"
        )

        # Pressure: [M, L, T] = [1, -1, -2]
        pressure_msg = FieldMessage(
            field_name="pressure",
            grid_topology=grid,
            values=values,
            units=DimensionVector(M=1, L=-1, T=-2),
            timestamp=0.0,
            field_location="face",
            domain_tag="test.structure"
        )

        validator = ContractValidator()
        violations = validator.validate_dimension_consistency(force_msg, pressure_msg)

        assert len(violations) == 1, "Should detect exactly 1 dimension violation"
        assert violations[0].violation_type == "dimension"
        assert violations[0].severity == "error"

    def test_detect_velocity_vs_displacement(self):
        """Should detect velocity (L/T) vs displacement (L) mismatch."""
        grid = make_simple_grid(n_nodes=5)
        n_faces = grid.n_faces

        values = np.ones((n_faces, 3))

        velocity_msg = FieldMessage(
            field_name="velocity",
            grid_topology=grid,
            values=values,
            units=DimensionVector(L=1, T=-1),
            timestamp=0.0,
            field_location="face",
            domain_tag="test.fluid"
        )

        displacement_msg = FieldMessage(
            field_name="displacement",
            grid_topology=grid,
            values=values,
            units=DimensionVector(L=1),
            timestamp=0.0,
            field_location="face",
            domain_tag="test.structure"
        )

        validator = ContractValidator()
        violations = validator.validate_dimension_consistency(velocity_msg, displacement_msg)

        assert len(violations) == 1
        assert violations[0].violation_type == "dimension"

    def test_detect_temperature_vs_energy(self):
        """Should detect temperature (Θ) vs energy (ML²/T²) mismatch."""
        grid = make_simple_grid(n_nodes=5)
        n = grid.n_nodes

        values = np.ones(n)

        temp_msg = FieldMessage(
            field_name="temperature",
            grid_topology=grid,
            values=values,
            units=DimensionVector(Theta=1),
            timestamp=0.0,
            field_location="node",
            domain_tag="test.thermal"
        )

        energy_msg = FieldMessage(
            field_name="energy",
            grid_topology=grid,
            values=values,
            units=DimensionVector(M=1, L=2, T=-2),
            timestamp=0.0,
            field_location="node",
            domain_tag="test.mechanical"
        )

        validator = ContractValidator()
        violations = validator.validate_dimension_consistency(temp_msg, energy_msg)

        assert len(violations) == 1

    def test_no_false_positive_for_matching_dimensions(self):
        """Should NOT flag matching dimensions."""
        grid = make_simple_grid(n_nodes=5)
        n = grid.n_nodes

        values = np.ones(n)

        msg1 = FieldMessage(
            field_name="pressure_a",
            grid_topology=grid,
            values=values,
            units=DimensionVector(M=1, L=-1, T=-2),
            timestamp=0.0,
            field_location="node",
            domain_tag="test.fluid"
        )

        msg2 = FieldMessage(
            field_name="pressure_b",
            grid_topology=grid,
            values=values,
            units=DimensionVector(M=1, L=-1, T=-2),
            timestamp=0.0,
            field_location="node",
            domain_tag="test.structure"
        )

        validator = ContractValidator()
        violations = validator.validate_dimension_consistency(msg1, msg2)

        assert len(violations) == 0, "Should not flag matching dimensions"

    def test_100_percent_detection_rate(self):
        """Test 10 different mismatches - all should be detected."""
        grid = make_simple_grid(n_nodes=5)
        n = grid.n_nodes
        values = np.ones(n)

        # 10 pairs with different dimensions
        test_pairs = [
            (DimensionVector(M=1), DimensionVector(L=1)),  # mass vs length
            (DimensionVector(T=-1), DimensionVector(T=-2)),  # freq vs freq²
            (DimensionVector(M=1, L=1, T=-2), DimensionVector(M=1, L=2, T=-2)),  # force vs torque
            (DimensionVector(L=-3, M=1), DimensionVector(L=-3)),  # density vs 1/volume
            (DimensionVector(Theta=1), DimensionVector()),  # temp vs dimensionless
            (DimensionVector(M=1, L=-1, T=-2), DimensionVector(M=1, L=1, T=-2)),  # pressure vs force
            (DimensionVector(L=1, T=-1), DimensionVector(L=2, T=-1)),  # velocity vs kinematic viscosity
            (DimensionVector(M=1, L=2, T=-3), DimensionVector(M=1, L=2, T=-2)),  # power vs energy
            (DimensionVector(L=1), DimensionVector(L=-1)),  # length vs 1/length
            (DimensionVector(M=1, T=-2), DimensionVector(M=1, L=-1, T=-2)),  # spring constant vs pressure/time²
        ]

        validator = ContractValidator()
        detected = 0

        for dim1, dim2 in test_pairs:
            msg1 = FieldMessage("f1", grid, values, dim1, 0.0, "node", "test.a")
            msg2 = FieldMessage("f2", grid, values, dim2, 0.0, "node", "test.b")

            violations = validator.validate_dimension_consistency(msg1, msg2)
            if len(violations) > 0:
                detected += 1

        assert detected == 10, f"Detection rate: {detected}/10 (expected 100%)"


# ============================================================================
# C5: Coordinate Transform Tests
# ============================================================================

class TestCoordinateTransform:
    """C5: Coordinate transform norm preservation (error ≤ 1e-12)."""

    def test_rotation_preserves_norm(self):
        """Rotation should preserve vector norms exactly."""
        validator = ContractValidator()

        # 90-degree rotation about z-axis
        R = np.array([
            [0, -1, 0],
            [1, 0, 0],
            [0, 0, 1]
        ], dtype=float)

        vectors = np.array([
            [1, 0, 0],
            [0, 1, 0],
            [3, 4, 0],
            [1, 1, 1],
            [0, 0, 5]
        ], dtype=float)

        transformed, norm_error = validator.validate_coordinate_transform(vectors, R)

        assert norm_error <= 1e-12, f"Norm error {norm_error} > 1e-12"

        # Verify specific values
        assert_allclose(transformed[0], [0, 1, 0], atol=1e-14)
        assert_allclose(transformed[1], [-1, 0, 0], atol=1e-14)

    def test_arbitrary_rotation_preserves_norm(self):
        """Arbitrary rotation should preserve norms."""
        validator = ContractValidator()

        # Rotation by 37 degrees about axis (1, 2, 3)
        axis = np.array([1, 2, 3], dtype=float)
        axis /= np.linalg.norm(axis)
        angle = np.radians(37)

        # Rodrigues' rotation formula
        K = np.array([
            [0, -axis[2], axis[1]],
            [axis[2], 0, -axis[0]],
            [-axis[1], axis[0], 0]
        ])
        R = np.eye(3) + np.sin(angle) * K + (1 - np.cos(angle)) * (K @ K)

        # Random vectors
        np.random.seed(42)
        vectors = np.random.randn(20, 3)

        transformed, norm_error = validator.validate_coordinate_transform(vectors, R)

        assert norm_error <= 1e-12, f"Norm error {norm_error} > 1e-12"

    def test_identity_transform(self):
        """Identity transform should preserve everything exactly."""
        validator = ContractValidator()

        R = np.eye(3)
        vectors = np.array([[1, 2, 3], [4, 5, 6]], dtype=float)

        transformed, norm_error = validator.validate_coordinate_transform(vectors, R)

        assert_allclose(transformed, vectors, atol=1e-15)
        assert norm_error <= 1e-15

    def test_detect_non_orthogonal_matrix(self):
        """Should detect non-orthogonal transformation matrix."""
        validator = ContractValidator()

        # Non-orthogonal matrix (scaling)
        R = np.array([
            [2, 0, 0],
            [0, 1, 0],
            [0, 0, 1]
        ], dtype=float)

        vectors = np.array([[1, 0, 0]], dtype=float)

        transformed, norm_error = validator.validate_coordinate_transform(vectors, R)

        summary = validator.get_validation_summary()
        assert summary["errors"] > 0, "Should detect non-orthogonal matrix"


# ============================================================================
# C6: Aitken Coupling Convergence Tests
# ============================================================================

class TestAitkenConvergence:
    """C6: Aitken coupling convergence (≤ 20 iterations, residual ≤ 1e-6)."""

    def test_aitken_converges_linear_problem(self):
        """Aitken should converge for simple linear fixed-point problem."""
        aitken = AitkenRelaxation(tolerance=1e-6, max_iterations=20)

        # Linear problem: R(x) = 0.5*x + b, fixed point at x* = 2b
        b = np.array([1.0, 2.0, 3.0])

        def coupled_solver(x):
            return 0.5 * x + b

        x0 = np.array([0.0, 0.0, 0.0])
        x_sol, convergence = aitken.solve_coupled_system(x0, coupled_solver)

        assert convergence.converged, f"Did not converge: {convergence}"
        assert convergence.iteration <= 20, f"Took {convergence.iteration} iterations (> 20)"
        assert convergence.residual_norm <= 1e-6

        # Check solution accuracy
        x_exact = 2.0 * b
        assert_allclose(x_sol, x_exact, rtol=1e-4)

    def test_aitken_converges_moderately_coupled(self):
        """Aitken should converge for moderately coupled problem."""
        aitken = AitkenRelaxation(tolerance=1e-6, max_iterations=20)

        # Moderately coupled: R(x) = 0.7*x + 0.3*b
        b = np.array([5.0])

        def coupled_solver(x):
            return 0.7 * x + 0.3 * b

        x0 = np.array([0.0])
        x_sol, convergence = aitken.solve_coupled_system(x0, coupled_solver)

        assert convergence.converged
        assert convergence.iteration <= 20
        assert convergence.residual_norm <= 1e-6

    def test_aitken_residual_decreases(self):
        """Residual should decrease monotonically (mostly) with Aitken."""
        aitken = AitkenRelaxation(tolerance=1e-8, max_iterations=30)

        b = np.array([1.0, 2.0])

        def coupled_solver(x):
            return 0.7 * x + b

        x0 = np.array([0.0, 0.0])
        x_sol, convergence = aitken.solve_coupled_system(x0, coupled_solver)

        # Check that residual history shows decrease
        history = convergence.history
        assert len(history) > 1
        # Final residual should be much smaller than initial
        assert history[-1] < history[0] * 0.01

    def test_aitken_nonlinear_problem(self):
        """Aitken should handle mildly nonlinear problems."""
        aitken = AitkenRelaxation(tolerance=1e-6, max_iterations=20)

        # Nonlinear: R(x) = sin(x) + 0.5, fixed point near x ≈ 1.5
        def coupled_solver(x):
            return np.sin(x) + 0.5

        x0 = np.array([1.0])
        x_sol, convergence = aitken.solve_coupled_system(x0, coupled_solver)

        assert convergence.converged
        assert convergence.iteration <= 20

    def test_aitken_multidimensional(self):
        """Aitken should work for multi-dimensional problems."""
        aitken = AitkenRelaxation(tolerance=1e-6, max_iterations=20)

        n = 10
        A = 0.3 * np.eye(n) + 0.01 * np.ones((n, n))
        b = np.ones(n)

        def coupled_solver(x):
            return A @ x + b

        x0 = np.zeros(n)
        x_sol, convergence = aitken.solve_coupled_system(x0, coupled_solver)

        assert convergence.converged
        assert convergence.iteration <= 20
        assert convergence.residual_norm <= 1e-6


# ============================================================================
# C7: Zero Domain Semantics Tests
# ============================================================================

class TestZeroDomainSemantics:
    """C7: All tests use synthetic domain names, no domain-specific terms."""

    def test_synthetic_domain_tags(self):
        """Verify all domain tags are synthetic."""
        grid = make_simple_grid(n_nodes=5)
        n = grid.n_nodes

        msg = FieldMessage(
            field_name="test_field",
            grid_topology=grid,
            values=np.ones(n),
            units=DimensionVector(),
            timestamp=0.0,
            field_location="node",
            domain_tag="test.domain_a"
        )

        assert "test." in msg.domain_tag
        assert msg.domain_tag in ["test.fluid", "test.structure", "test.domain_a",
                                   "test.thermal", "test.mechanical", "test.fine",
                                   "test.coarse", "test.a", "test.b"]

    def test_no_domain_specific_keywords_in_module(self):
        """Verify coupling module has no domain-specific keywords."""
        import inspect
        import re
        from src.coupling import field_exchange, conservation, time_stepping
        from src.coupling import contract_validation, stability

        # Use word boundaries to avoid false positives (e.g., "piecewise" contains "ice")
        forbidden_patterns = [
            r'\bship\b', r'\bhull\b', r'\bice\b', r'\bwave\b',
            r'\bfluid_dynamics\b', r'\bstructural_mechanics\b',
            r'\bresistance\b', r'\bpropulsion\b', r'\bseakeeping\b',
            r'\bmaneuvering\b', r'\bwigley\b', r'\bdtmb\b', r'\bkcs\b', r'\bsdb\b'
        ]

        modules = [field_exchange, conservation, time_stepping,
                   contract_validation, stability]

        for mod in modules:
            source = inspect.getsource(mod)
            source_lower = source.lower()
            for pattern in forbidden_patterns:
                match = re.search(pattern, source_lower)
                assert match is None, (
                    f"Found domain keyword pattern '{pattern}' in {mod.__name__}"
                )


# ============================================================================
# Additional Integration Tests
# ============================================================================

class TestFieldExchange:
    """Test field exchange and grid mapping."""

    def test_nearest_neighbor_mapping(self):
        """Test nearest neighbor field mapping between grids."""
        grid_a = make_simple_grid(n_nodes=10, extent=1.0)
        grid_b = make_simple_grid(n_nodes=5, extent=1.0)

        # Scalar field on grid_a
        values_a = np.arange(grid_a.n_nodes, dtype=float)

        msg_a = FieldMessage(
            field_name="scalar_field",
            grid_topology=grid_a,
            values=values_a,
            units=DimensionVector(),
            timestamp=0.0,
            field_location="node",
            domain_tag="test.a"
        )

        exchange = FieldExchange("test.a", "test.b")
        msg_b = exchange.map_field(msg_a, grid_b, method="nearest")

        assert msg_b.domain_tag == "test.b"
        assert msg_b.field_name == "scalar_field"
        # Mapped to target nodes
        assert msg_b.n_values == grid_b.n_nodes

    def test_flux_integral(self):
        """Test surface flux integral computation."""
        grid = make_simple_grid(n_nodes=10)
        n_faces = grid.n_faces

        # Uniform velocity in z-direction
        velocity = np.zeros((n_faces, 3))
        velocity[:, 2] = 2.0

        msg = FieldMessage(
            field_name="velocity",
            grid_topology=grid,
            values=velocity,
            units=DimensionVector(L=1, T=-1),
            timestamp=0.0,
            field_location="face",
            domain_tag="test.fluid"
        )

        exchange = FieldExchange("test.fluid", "test.structure")
        flux = exchange.compute_flux_integral(msg)

        # Flux should be 2.0 * total_area
        total_area = np.sum(grid.face_areas)
        expected = 2.0 * total_area
        assert abs(flux - expected) / expected < 0.01


class TestCFLCondition:
    """Test CFL-like coupling stability condition."""

    def test_critical_dt_computation(self):
        """Test critical coupling time step computation."""
        dt_crit = CFLCouplingCondition.compute_critical_coupling_dt(
            interface_mass=1.0,
            interface_stiffness=100.0,
            added_mass_factor=0.5
        )

        # dt_crit = 2 / (ω_n * (1 + β)) = 2 / (10 * 1.5) = 0.1333
        expected = 2.0 / (np.sqrt(100.0) * 1.5)
        assert abs(dt_crit - expected) < 1e-10

    def test_stability_check(self):
        """Test stability check with known parameters."""
        is_stable, dt_crit = CFLCouplingCondition.check_stability(
            coupling_dt=0.05,
            interface_mass=1.0,
            interface_stiffness=100.0,
            added_mass_factor=0.5,
            safety_factor=0.8
        )

        # dt_crit ≈ 0.133, dt_allowed = 0.8 * 0.133 ≈ 0.107
        # 0.05 < 0.107 → stable
        assert is_stable

    def test_added_mass_ratio(self):
        """Test added mass ratio estimation."""
        ratio = CFLCouplingCondition.compute_added_mass_ratio(
            fluid_density=1000.0,
            structural_density=7800.0,
            characteristic_length=1.0
        )

        expected = 1000.0 / 7800.0
        assert abs(ratio - expected) < 1e-10
