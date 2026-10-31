"""
Pytest suite for Clock Synchronization & Subcycling Framework.

SIR-PC2|X02 — Ice navigation campaign DAG node.

Acceptance criteria:
  clock_sync_error              < 1e-6  [s]
  subcycling_energy_conservation < 0.01  [ratio]
  pytest_test_clock_sync        == PASS  [bool]

Tests cover:
  1. Master clock mechanics (advance, state, reset)
  2. Sub-solver registration and step counting
  3. Clock synchronization error across multi-rate solvers
  4. Energy conservation for conservative (harmonic) systems
  5. Ice-structure-hydro coupling scenario
  6. Boundary data interpolation
  7. Edge cases and error handling
"""

import math
import pytest
import numpy as np

from backend.services.coupling_clock_sync import (
    ClockState,
    MasterClock,
    SolverDomain,
    SubSolver,
    SubcyclingController,
    create_ice_coupling_scenario,
    linear_interpolate_boundary,
    make_harmonic_solver,
    make_damped_solver,
    validate_clock_sync,
)


# ═══════════════════════════════════════════════════════════════════════════
# 1. Master Clock Tests
# ═══════════════════════════════════════════════════════════════════════════

class TestMasterClock:
    """Test MasterClock mechanics."""

    def test_initial_state(self):
        clock = MasterClock(dt_master=0.01)
        assert clock.t_current == 0.0
        assert clock.step == 0
        assert clock.state == ClockState.STOPPED

    def test_start_and_advance(self):
        clock = MasterClock(dt_master=0.01)
        clock.start()
        assert clock.state == ClockState.RUNNING
        t = clock.advance()
        assert t == pytest.approx(0.01, abs=1e-15)
        assert clock.step == 1

    def test_multiple_advances(self):
        clock = MasterClock(dt_master=0.01)
        clock.start()
        for _ in range(100):
            clock.advance()
        assert clock.t_current == pytest.approx(1.0, abs=1e-12)
        assert clock.step == 100

    def test_cannot_advance_when_stopped(self):
        clock = MasterClock(dt_master=0.01)
        with pytest.raises(RuntimeError, match="Cannot advance"):
            clock.advance()

    def test_cannot_advance_when_paused(self):
        clock = MasterClock(dt_master=0.01)
        clock.start()
        clock.pause()
        with pytest.raises(RuntimeError, match="Cannot advance"):
            clock.advance()

    def test_reset(self):
        clock = MasterClock(dt_master=0.01)
        clock.start()
        clock.advance()
        clock.advance()
        clock.reset()
        assert clock.t_current == 0.0
        assert clock.step == 0
        assert clock.state == ClockState.STOPPED

    def test_custom_dt_master(self):
        clock = MasterClock(dt_master=0.001)
        clock.start()
        t = clock.advance()
        assert t == pytest.approx(0.001, abs=1e-15)


# ═══════════════════════════════════════════════════════════════════════════
# 2. Sub-Solver Tests
# ═══════════════════════════════════════════════════════════════════════════

class TestSubSolver:
    """Test SubSolver creation and properties."""

    def test_create_solver(self):
        solver = SubSolver(name="test", domain=SolverDomain.ICE, dt=1e-4)
        assert solver.name == "test"
        assert solver.domain == SolverDomain.ICE
        assert solver.dt == 1e-4

    def test_solver_domains(self):
        assert SolverDomain.ICE.value == "ice"
        assert SolverDomain.STRUCTURE.value == "structure"
        assert SolverDomain.HYDRODYNAMICS.value == "hydrodynamics"
        assert SolverDomain.THERMAL.value == "thermal"


# ═══════════════════════════════════════════════════════════════════════════
# 3. Subcycling Controller Tests
# ═══════════════════════════════════════════════════════════════════════════

class TestSubcyclingController:
    """Test SubcyclingController orchestration."""

    def test_add_solver(self):
        clock = MasterClock(dt_master=0.01)
        ctrl = SubcyclingController(clock=clock)
        solver = SubSolver(name="ice", domain=SolverDomain.ICE, dt=0.001)
        ctrl.add_solver(solver)
        assert len(ctrl.solvers) == 1

    def test_add_solver_invalid_dt(self):
        """Solver dt must evenly divide dt_master."""
        clock = MasterClock(dt_master=0.01)
        ctrl = SubcyclingController(clock=clock)
        solver = SubSolver(name="bad", domain=SolverDomain.ICE, dt=0.003)
        with pytest.raises(ValueError, match="does not evenly divide"):
            ctrl.add_solver(solver)

    def test_n_substeps(self):
        clock = MasterClock(dt_master=0.01)
        ctrl = SubcyclingController(clock=clock)
        solver = SubSolver(name="ice", domain=SolverDomain.ICE, dt=0.001)
        ctrl.add_solver(solver)
        assert ctrl._n_substeps(solver) == 10

    def test_n_substeps_100(self):
        clock = MasterClock(dt_master=0.01)
        ctrl = SubcyclingController(clock=clock)
        solver = SubSolver(name="ice", domain=SolverDomain.ICE, dt=1e-4)
        ctrl.add_solver(solver)
        assert ctrl._n_substeps(solver) == 100

    def test_run_master_step_no_solvers(self):
        clock = MasterClock(dt_master=0.01)
        ctrl = SubcyclingController(clock=clock)
        clock.start()
        result = ctrl.run_master_step()
        assert result["sync_error"] == 0.0
        assert result["t_after"] == pytest.approx(0.01, abs=1e-15)

    def test_run_master_step_with_solver(self):
        clock = MasterClock(dt_master=0.01)
        ctrl = SubcyclingController(clock=clock)
        solver = make_harmonic_solver("osc", SolverDomain.STRUCTURE, dt=0.001)
        ctrl.add_solver(solver)
        clock.start()
        result = ctrl.run_master_step()
        assert result["t_after"] == pytest.approx(0.01, abs=1e-15)
        assert result["sub_steps_taken"]["osc"] == 10


# ═══════════════════════════════════════════════════════════════════════════
# 4. Clock Synchronization Error Tests (ACCEPTANCE CRITERION)
# ═══════════════════════════════════════════════════════════════════════════

class TestClockSyncError:
    """
    Test clock synchronization error.

    Acceptance: clock_sync_error < 1e-6 [s]
    """

    def test_single_solver_sync(self):
        """Single solver: local time must match master time exactly."""
        clock = MasterClock(dt_master=0.01)
        ctrl = SubcyclingController(clock=clock)
        solver = make_harmonic_solver("osc", SolverDomain.STRUCTURE, dt=0.001)
        ctrl.add_solver(solver)
        clock.start()

        for _ in range(10):
            result = ctrl.run_master_step()

        max_err = max(ctrl.sync_errors)
        assert max_err < 1e-6, f"Clock sync error {max_err} exceeds 1e-6 s"

    def test_multi_rate_sync(self):
        """Multiple solvers with different time steps must stay synchronized."""
        clock = MasterClock(dt_master=0.01)
        ctrl = SubcyclingController(clock=clock)

        ctrl.add_solver(make_harmonic_solver("fast", SolverDomain.ICE, dt=1e-4))
        ctrl.add_solver(make_harmonic_solver("medium", SolverDomain.STRUCTURE, dt=1e-3))
        ctrl.add_solver(make_harmonic_solver("slow", SolverDomain.HYDRODYNAMICS, dt=5e-3))

        clock.start()
        for _ in range(100):
            ctrl.run_master_step()

        max_err = max(ctrl.sync_errors)
        assert max_err < 1e-6, f"Clock sync error {max_err} exceeds 1e-6 s"

    def test_ice_coupling_scenario_sync(self):
        """Full ice-structure-hydro scenario: sync error < 1e-6 s."""
        ctrl = create_ice_coupling_scenario()
        ctrl.clock.start()
        for _ in range(100):
            ctrl.run_master_step()

        max_err = max(ctrl.sync_errors)
        assert max_err < 1e-6, f"Clock sync error {max_err} exceeds 1e-6 s"

    def test_long_run_sync(self):
        """1000 master steps: sync error must remain < 1e-6 s."""
        ctrl = create_ice_coupling_scenario()
        ctrl.clock.start()
        for _ in range(1000):
            ctrl.run_master_step()

        max_err = max(ctrl.sync_errors)
        assert max_err < 1e-6, f"Clock sync error {max_err} exceeds 1e-6 s after 1000 steps"


# ═══════════════════════════════════════════════════════════════════════════
# 5. Energy Conservation Tests (ACCEPTANCE CRITERION)
# ═══════════════════════════════════════════════════════════════════════════

class TestEnergyConservation:
    """
    Test energy conservation for conservative systems.

    Acceptance: subcycling_energy_conservation < 0.01 [ratio]
    """

    def test_single_harmonic_oscillator(self):
        """Single harmonic oscillator: energy must be conserved."""
        clock = MasterClock(dt_master=0.01)
        ctrl = SubcyclingController(clock=clock)
        solver = make_harmonic_solver("osc", SolverDomain.STRUCTURE, dt=0.001)
        ctrl.add_solver(solver)
        clock.start()

        for _ in range(100):
            ctrl.run_master_step()

        ratio = ctrl._compute_energy_conservation()
        assert ratio < 0.01, f"Energy conservation ratio {ratio} exceeds 0.01"

    def test_multi_harmonic_oscillators(self):
        """Multiple harmonic oscillators: total energy must be conserved."""
        clock = MasterClock(dt_master=0.01)
        ctrl = SubcyclingController(clock=clock)

        ctrl.add_solver(make_harmonic_solver("fast", SolverDomain.ICE, dt=1e-4, omega=20*math.pi))
        ctrl.add_solver(make_harmonic_solver("medium", SolverDomain.STRUCTURE, dt=1e-3, omega=4*math.pi))
        ctrl.add_solver(make_harmonic_solver("slow", SolverDomain.HYDRODYNAMICS, dt=5e-3, omega=math.pi))

        clock.start()
        for _ in range(100):
            ctrl.run_master_step()

        ratio = ctrl._compute_energy_conservation()
        assert ratio < 0.01, f"Energy conservation ratio {ratio} exceeds 0.01"

    def test_ice_coupling_energy(self):
        """Full ice-structure-hydro scenario: energy conservation < 0.01."""
        ctrl = create_ice_coupling_scenario()
        ctrl.clock.start()
        for _ in range(100):
            ctrl.run_master_step()

        ratio = ctrl._compute_energy_conservation()
        assert ratio < 0.01, f"Energy conservation ratio {ratio} exceeds 0.01"

    def test_energy_per_solver(self):
        """Each individual solver should conserve energy well."""
        clock = MasterClock(dt_master=0.01)
        ctrl = SubcyclingController(clock=clock)

        ctrl.add_solver(make_harmonic_solver("osc1", SolverDomain.ICE, dt=1e-4, omega=10*math.pi))
        ctrl.add_solver(make_harmonic_solver("osc2", SolverDomain.STRUCTURE, dt=1e-3, omega=2*math.pi))

        clock.start()
        for _ in range(100):
            ctrl.run_master_step()

        # Check each solver individually
        for solver in ctrl.solvers:
            e0 = ctrl.energy_history[0][solver.name]
            ef = ctrl.energy_history[-1][solver.name]
            if abs(e0) > 1e-30:
                ratio = abs(ef - e0) / abs(e0)
                assert ratio < 0.01, (
                    f"Solver '{solver.name}' energy ratio {ratio} exceeds 0.01"
                )


# ═══════════════════════════════════════════════════════════════════════════
# 6. Boundary Interpolation Tests
# ═══════════════════════════════════════════════════════════════════════════

class TestBoundaryInterpolation:
    """Test linear interpolation of boundary data."""

    def test_interpolate_at_t0(self):
        data0 = {"pressure": 100.0, "velocity": 1.0}
        data1 = {"pressure": 200.0, "velocity": 2.0}
        result = linear_interpolate_boundary(data0, data1, alpha=0.0)
        assert result["pressure"] == pytest.approx(100.0)
        assert result["velocity"] == pytest.approx(1.0)

    def test_interpolate_at_t1(self):
        data0 = {"pressure": 100.0, "velocity": 1.0}
        data1 = {"pressure": 200.0, "velocity": 2.0}
        result = linear_interpolate_boundary(data0, data1, alpha=1.0)
        assert result["pressure"] == pytest.approx(200.0)
        assert result["velocity"] == pytest.approx(2.0)

    def test_interpolate_at_midpoint(self):
        data0 = {"pressure": 100.0, "velocity": 1.0}
        data1 = {"pressure": 200.0, "velocity": 2.0}
        result = linear_interpolate_boundary(data0, data1, alpha=0.5)
        assert result["pressure"] == pytest.approx(150.0)
        assert result["velocity"] == pytest.approx(1.5)

    def test_interpolate_quarter(self):
        data0 = {"force": 0.0}
        data1 = {"force": 4.0}
        result = linear_interpolate_boundary(data0, data1, alpha=0.25)
        assert result["force"] == pytest.approx(1.0)


# ═══════════════════════════════════════════════════════════════════════════
# 7. Harmonic Solver Validation
# ═══════════════════════════════════════════════════════════════════════════

class TestHarmonicSolver:
    """Test the harmonic oscillator solver against analytical solution."""

    def test_position_after_one_period(self):
        """After one full period, position should return to initial value."""
        omega = 2.0 * math.pi  # 1 Hz
        dt = 1e-4
        period = 2.0 * math.pi / omega  # 1.0 s
        n_steps = int(period / dt)

        solver = make_harmonic_solver("test", SolverDomain.STRUCTURE, dt=dt, omega=omega, amplitude=1.0)

        for _ in range(n_steps):
            state, energy = solver.advance_fn(0.0, dt, {})
            solver.state = state

        # Position should be back near 1.0 (within numerical tolerance)
        assert abs(solver.state["x"] - 1.0) < 0.01, (
            f"Position after one period: {solver.state['x']}, expected ~1.0"
        )

    def test_energy_conservation_analytical(self):
        """Energy of harmonic oscillator should be exactly conserved."""
        omega = 2.0 * math.pi
        dt = 1e-4
        mass = 1.0
        amplitude = 1.0
        k = mass * omega ** 2
        e_exact = 0.5 * k * amplitude ** 2

        solver = make_harmonic_solver("test", SolverDomain.STRUCTURE, dt=dt, omega=omega, amplitude=amplitude)

        energies = [solver.energy]
        for _ in range(10000):
            state, energy = solver.advance_fn(0.0, dt, {})
            solver.state = state
            energies.append(energy)

        max_deviation = max(abs(e - e_exact) / e_exact for e in energies)
        assert max_deviation < 1e-6, (
            f"Max energy deviation: {max_deviation}, expected < 1e-6"
        )


# ═══════════════════════════════════════════════════════════════════════════
# 8. Integration Test: Full Validation (ACCEPTANCE CRITERION)
# ═══════════════════════════════════════════════════════════════════════════

class TestFullValidation:
    """
    Full validation against all acceptance criteria.

    This is the primary test that the CI/CD pipeline checks.
    """

    def test_clock_sync_pass(self):
        """
        ACCEPTANCE: clock_sync_error < 1e-6 [s]
        """
        result = validate_clock_sync(n_steps=100)
        assert result["pass_sync"], (
            f"Clock sync FAILED: error={result['clock_sync_error']:.2e} s "
            f"(threshold=1e-6 s)"
        )

    def test_energy_conservation_pass(self):
        """
        ACCEPTANCE: subcycling_energy_conservation < 0.01 [ratio]
        """
        result = validate_clock_sync(n_steps=100)
        assert result["pass_energy"], (
            f"Energy conservation FAILED: ratio={result['energy_conservation_ratio']:.6f} "
            f"(threshold=0.01)"
        )

    def test_all_criteria_pass(self):
        """
        ACCEPTANCE: pytest_test_clock_sync == PASS [bool]
        """
        result = validate_clock_sync(n_steps=100)
        assert result["pass_all"], (
            f"Overall validation FAILED: "
            f"sync_error={result['clock_sync_error']:.2e} s, "
            f"energy_ratio={result['energy_conservation_ratio']:.6f}"
        )

    def test_validation_returns_correct_structure(self):
        """Validate the return structure of validate_clock_sync."""
        result = validate_clock_sync(n_steps=10)
        assert "clock_sync_error" in result
        assert "energy_conservation_ratio" in result
        assert "pass_sync" in result
        assert "pass_energy" in result
        assert "pass_all" in result
        assert "details" in result
        assert isinstance(result["pass_sync"], bool)
        assert isinstance(result["pass_energy"], bool)
        assert isinstance(result["pass_all"], bool)


# ═══════════════════════════════════════════════════════════════════════════
# 9. Edge Cases
# ═══════════════════════════════════════════════════════════════════════════

class TestEdgeCases:
    """Edge case and robustness tests."""

    def test_single_substep_solver(self):
        """Solver with dt = dt_master (1 sub-step per master step)."""
        clock = MasterClock(dt_master=0.01)
        ctrl = SubcyclingController(clock=clock)
        solver = make_harmonic_solver("slow", SolverDomain.HYDRODYNAMICS, dt=0.01)
        ctrl.add_solver(solver)
        clock.start()

        for _ in range(10):
            ctrl.run_master_step()

        assert max(ctrl.sync_errors) < 1e-6

    def test_very_small_dt(self):
        """Solver with very small dt (many sub-steps)."""
        clock = MasterClock(dt_master=0.01)
        ctrl = SubcyclingController(clock=clock)
        solver = make_harmonic_solver("fast", SolverDomain.ICE, dt=1e-5)
        ctrl.add_solver(solver)
        clock.start()

        for _ in range(10):
            ctrl.run_master_step()

        assert max(ctrl.sync_errors) < 1e-6

    def test_mixed_solver_rates(self):
        """Mix of very fast and very slow solvers."""
        clock = MasterClock(dt_master=0.01)
        ctrl = SubcyclingController(clock=clock)
        ctrl.add_solver(make_harmonic_solver("fast", SolverDomain.ICE, dt=1e-4))
        ctrl.add_solver(make_harmonic_solver("slow", SolverDomain.HYDRODYNAMICS, dt=0.01))
        clock.start()

        for _ in range(50):
            ctrl.run_master_step()

        assert max(ctrl.sync_errors) < 1e-6
        ratio = ctrl._compute_energy_conservation()
        assert ratio < 0.01

    def test_zero_initial_energy(self):
        """Edge case: solver starting at rest (zero energy)."""
        clock = MasterClock(dt_master=0.01)
        ctrl = SubcyclingController(clock=clock)
        # Amplitude=0 means zero energy
        solver = make_harmonic_solver("rest", SolverDomain.STRUCTURE, dt=0.001, amplitude=0.0)
        ctrl.add_solver(solver)
        clock.start()

        for _ in range(10):
            ctrl.run_master_step()

        ratio = ctrl._compute_energy_conservation()
        # Zero initial energy → ratio should be 0 (absolute difference)
        assert ratio < 0.01


# ═══════════════════════════════════════════════════════════════════════════
# 10. Damped Solver (Non-Conservative System)
# ═══════════════════════════════════════════════════════════════════════════

class TestDampedSolver:
    """Test damped solver to verify energy tracking for dissipative systems."""

    def test_damped_energy_decreases(self):
        """Damped oscillator energy should decrease over time."""
        clock = MasterClock(dt_master=0.01)
        ctrl = SubcyclingController(clock=clock)
        solver = make_damped_solver("damped", SolverDomain.STRUCTURE, dt=0.001, damping=0.1)
        ctrl.add_solver(solver)
        clock.start()

        for _ in range(100):
            ctrl.run_master_step()

        e0 = ctrl.energy_history[0]["damped"]
        ef = ctrl.energy_history[-1]["damped"]
        assert ef < e0, "Damped oscillator energy should decrease"

    def test_damped_sync_still_good(self):
        """Even with damping, clock sync should be maintained."""
        clock = MasterClock(dt_master=0.01)
        ctrl = SubcyclingController(clock=clock)
        solver = make_damped_solver("damped", SolverDomain.STRUCTURE, dt=0.001, damping=0.1)
        ctrl.add_solver(solver)
        clock.start()

        for _ in range(100):
            ctrl.run_master_step()

        assert max(ctrl.sync_errors) < 1e-6
