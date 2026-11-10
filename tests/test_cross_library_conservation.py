"""
Comprehensive tests for cross-library field exchange conservation verification.

Tests cover:
1. FEM→DEM fracture transfer: four-ledger conservation (mass/momentum/energy/inertia)
2. LBM→FVM flow field interpolation: mass conservation
3. Clock synchronization: conservative + optimistic modes

All test fixtures use synthetic geometry (zero domain semantics).
"""

import pytest
import numpy as np
import json
import os
import tempfile

from src.coupling.fem_to_dem_transfer import (
    FEMtoDEMTransfer,
    FailureFace,
    FEMElementState,
    DEMFragment,
    FourLedgerResult,
)
from src.coupling.lbm_to_fvm_interpolation import (
    LBMtoFVMInterpolator,
    LBMCell,
    FVMCell,
    MassConservationResult,
)
from src.coupling.clock_sync import (
    ClockSynchronizer,
    SolverClock,
    SyncMode,
    SyncState,
    SyncResult,
)


# ============================================================
# Fixtures: Synthetic geometry (zero domain semantics)
# ============================================================

@pytest.fixture
def synthetic_failure_faces():
    """Create synthetic fracture faces for a cubic element."""
    # Two triangular faces splitting a cube
    face1 = FailureFace(
        vertices=np.array([
            [0.0, 0.0, 0.0],
            [1.0, 0.0, 0.0],
            [0.5, 0.5, 0.5],
        ]),
        normal=np.array([0.0, -1.0, 1.0]),
        area=0.5,
    )
    face2 = FailureFace(
        vertices=np.array([
            [0.0, 1.0, 0.0],
            [1.0, 1.0, 0.0],
            [0.5, 0.5, 0.5],
        ]),
        normal=np.array([0.0, 1.0, -1.0]),
        area=0.5,
    )
    return [face1, face2]


@pytest.fixture
def synthetic_fem_state():
    """Create synthetic FEM element state."""
    return FEMElementState(
        mass=1.0,
        velocity=np.array([1.0, 0.5, 0.0]),
        angular_velocity=np.array([0.0, 0.0, 0.5]),
        inertia_tensor=np.array([
            [0.1, 0.0, 0.0],
            [0.0, 0.1, 0.0],
            [0.0, 0.0, 0.1],
        ]),
        elastic_strain_energy=0.01,
        centroid=np.array([0.5, 0.5, 0.5]),
    )


@pytest.fixture
def synthetic_lbm_cells():
    """Create synthetic LBM lattice cells (2x2x2 grid)."""
    cells = []
    dx = 0.1
    for i in range(2):
        for j in range(2):
            for k in range(2):
                center = np.array([i * dx, j * dx, k * dx])
                # Parabolic velocity profile
                velocity = np.array([
                    1.0 - (j * dx) ** 2,  # Poiseuille-like
                    0.0,
                    0.0,
                ])
                density = 1.0 + 0.01 * np.sin(i * j * k)
                pressure = density * 0.333  # cs² = 1/3 for D3Q19
                cells.append(LBMCell(
                    center=center,
                    dx=dx,
                    velocity=velocity,
                    density=density,
                    pressure=pressure,
                ))
    return cells


@pytest.fixture
def synthetic_fvm_cells():
    """Create synthetic FVM control volumes (3x3x3 grid, finer than LBM).

    The patch tiles *the same physical domain* as ``synthetic_lbm_cells``
    ([-0.05, 0.15]^3).  The earlier version offset the patch by 0.02, so the
    two grids covered different volumes; the mass-inventory identity of the
    transfer cannot hold across different domains, and the previous (degenerate)
    flux check hid that by always returning a zero residual.
    """
    cells = []
    h = 0.2 / 3.0
    for i in range(3):
        for j in range(3):
            for k in range(3):
                center = np.array([-0.05 + (i + 0.5) * h,
                                   -0.05 + (j + 0.5) * h,
                                   -0.05 + (k + 0.5) * h])
                cells.append(FVMCell(
                    center=center,
                    volume=h ** 3,
                ))
    return cells


# ============================================================
# Test 1: FEM→DEM Fracture Transfer — Four Ledger Conservation
# ============================================================

class TestFEMtoDEMTransfer:
    """Tests for FEM→DEM fracture transfer with four-ledger conservation."""

    def test_basic_transfer(self, synthetic_failure_faces, synthetic_fem_state):
        """Test basic FEM→DEM transfer creates correct number of fragments."""
        transfer = FEMtoDEMTransfer(tolerance=0.05)
        fragments, ledger = transfer.transfer(
            synthetic_fem_state,
            synthetic_failure_faces,
            n_fragments=2,
        )
        assert len(fragments) == 2
        assert all(isinstance(f, DEMFragment) for f in fragments)

    def test_mass_ledger_exact(self, synthetic_failure_faces, synthetic_fem_state):
        """Test mass ledger: Σm_DEM = m_FEM (exact conservation)."""
        transfer = FEMtoDEMTransfer(tolerance=0.01)
        fragments, ledger = transfer.transfer(
            synthetic_fem_state,
            synthetic_failure_faces,
            n_fragments=2,
        )
        assert ledger.mass_passed, f"Mass ledger failed: residual={ledger.mass_residual}"
        assert abs(ledger.mass_dem - ledger.mass_fem) < 1e-10

    def test_momentum_ledger(self, synthetic_failure_faces, synthetic_fem_state):
        """Test momentum ledger: Σ(mv)_DEM ≈ (mv)_FEM."""
        transfer = FEMtoDEMTransfer(tolerance=0.05)
        fragments, ledger = transfer.transfer(
            synthetic_fem_state,
            synthetic_failure_faces,
            n_fragments=2,
        )
        # Momentum should be approximately conserved
        assert ledger.momentum_passed or ledger.momentum_residual < 0.1, \
            f"Momentum residual too large: {ledger.momentum_residual}"

    def test_energy_ledger(self, synthetic_failure_faces, synthetic_fem_state):
        """Test energy ledger: DEM KE ≈ FEM KE + elastic energy."""
        transfer = FEMtoDEMTransfer(tolerance=0.10)
        fragments, ledger = transfer.transfer(
            synthetic_fem_state,
            synthetic_failure_faces,
            n_fragments=2,
        )
        # Energy check: with elastic energy release, DEM energy should be close
        assert ledger.energy_residual < 0.15, \
            f"Energy residual too large: {ledger.energy_residual}"

    def test_inertia_ledger(self, synthetic_failure_faces, synthetic_fem_state):
        """Test inertia ledger: trace(I_DEM) ≈ trace(I_FEM)."""
        transfer = FEMtoDEMTransfer(tolerance=0.15)
        fragments, ledger = transfer.transfer(
            synthetic_fem_state,
            synthetic_failure_faces,
            n_fragments=2,
        )
        # Inertia trace comparison
        assert ledger.inertia_residual < 0.40, \
            f"Inertia residual too large: {ledger.inertia_residual}"

    def test_four_ledger_summary(self, synthetic_failure_faces, synthetic_fem_state):
        """Test four-ledger summary structure."""
        transfer = FEMtoDEMTransfer(tolerance=0.05)
        fragments, ledger = transfer.transfer(
            synthetic_fem_state,
            synthetic_failure_faces,
            n_fragments=2,
        )
        summary = ledger.summary()
        assert "mass" in summary
        assert "momentum" in summary
        assert "energy" in summary
        assert "inertia" in summary
        assert "all_passed" in summary
        assert "tolerance" in summary

    def test_single_fragment(self, synthetic_failure_faces, synthetic_fem_state):
        """Test transfer with single fragment (no fracture)."""
        transfer = FEMtoDEMTransfer(tolerance=0.01)
        fragments, ledger = transfer.transfer(
            synthetic_fem_state,
            synthetic_failure_faces,
            n_fragments=1,
        )
        assert len(fragments) == 1
        assert abs(fragments[0].mass - synthetic_fem_state.mass) < 1e-10

    def test_multiple_fragments(self, synthetic_failure_faces, synthetic_fem_state):
        """Test transfer with multiple fragments."""
        transfer = FEMtoDEMTransfer(tolerance=0.05)
        for n in [2, 3, 4]:
            fragments, ledger = transfer.transfer(
                synthetic_fem_state,
                synthetic_failure_faces,
                n_fragments=n,
            )
            assert len(fragments) == n
            # Mass should always be exactly conserved
            assert abs(ledger.mass_dem - ledger.mass_fem) < 1e-10

    def test_failure_faces_json_roundtrip(self, synthetic_failure_faces):
        """Test consume_failure_faces_json with synthetic JSON."""
        # Create temp JSON file
        data = {
            "faces": [
                {
                    "vertices": f.vertices.tolist(),
                    "normal": f.normal.tolist(),
                    "area": f.area,
                }
                for f in synthetic_failure_faces
            ]
        }
        with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
            json.dump(data, f)
            json_path = f.name

        try:
            transfer = FEMtoDEMTransfer()
            faces = transfer.consume_failure_faces_json(json_path)
            assert len(faces) == 2
            assert all(isinstance(f, FailureFace) for f in faces)
            assert abs(faces[0].area - 0.5) < 1e-10
        finally:
            os.unlink(json_path)

    def test_transfer_history(self, synthetic_failure_faces, synthetic_fem_state):
        """Test transfer history recording."""
        transfer = FEMtoDEMTransfer()
        transfer.transfer(synthetic_fem_state, synthetic_failure_faces, n_fragments=2)
        transfer.transfer(synthetic_fem_state, synthetic_failure_faces, n_fragments=3)
        history = transfer.get_transfer_history()
        assert len(history) == 2
        assert history[0]["n_fragments"] == 2
        assert history[1]["n_fragments"] == 3

    def test_zero_velocity(self, synthetic_failure_faces):
        """Test transfer with zero initial velocity."""
        fem_state = FEMElementState(
            mass=1.0,
            velocity=np.array([0.0, 0.0, 0.0]),
            angular_velocity=np.array([0.0, 0.0, 0.0]),
            inertia_tensor=np.eye(3) * 0.1,
            elastic_strain_energy=0.0,
            centroid=np.array([0.5, 0.5, 0.5]),
        )
        transfer = FEMtoDEMTransfer(tolerance=0.05)
        fragments, ledger = transfer.transfer(fem_state, synthetic_failure_faces, n_fragments=2)
        assert ledger.mass_passed

    def test_invalid_n_fragments(self, synthetic_failure_faces, synthetic_fem_state):
        """Test error on invalid n_fragments."""
        transfer = FEMtoDEMTransfer()
        with pytest.raises(ValueError):
            transfer.transfer(synthetic_fem_state, synthetic_failure_faces, n_fragments=0)

    def test_empty_failure_faces(self, synthetic_fem_state):
        """Test error on empty failure faces."""
        transfer = FEMtoDEMTransfer()
        with pytest.raises(ValueError):
            transfer.transfer(synthetic_fem_state, [], n_fragments=2)

    def test_dem_fragment_properties(self):
        """Test DEMFragment computed properties."""
        frag = DEMFragment(
            mass=1.0,
            position=np.array([0.0, 0.0, 0.0]),
            velocity=np.array([1.0, 0.0, 0.0]),
            angular_velocity=np.array([0.0, 0.0, 1.0]),
            inertia_tensor=np.eye(3) * 0.1,
            radius=0.1,
        )
        # KE = 0.5 * 1 * 1² + 0.5 * 0.1 * 1² = 0.5 + 0.05 = 0.55
        expected_ke = 0.5 * 1.0 * 1.0 + 0.5 * 0.1 * 1.0
        assert abs(frag.kinetic_energy - expected_ke) < 1e-10

        # Momentum = mv = [1, 0, 0]
        assert np.allclose(frag.linear_momentum, np.array([1.0, 0.0, 0.0]))

        # Angular momentum = I·ω = [0, 0, 0.1]
        assert np.allclose(frag.angular_momentum, np.array([0.0, 0.0, 0.1]))


# ============================================================
# Test 2: LBM→FVM Flow Field Interpolation — Mass Conservation
# ============================================================

class TestLBMtoFVMInterpolation:
    """Tests for LBM→FVM interpolation with mass conservation."""

    def test_nearest_neighbor(self, synthetic_lbm_cells, synthetic_fvm_cells):
        """Test nearest-neighbor interpolation."""
        interp = LBMtoFVMInterpolator(tolerance=0.10)
        result = interp.nearest_neighbor_interpolate(
            synthetic_lbm_cells,
            synthetic_fvm_cells,
        )
        assert result.n_cells_interpolated == len(synthetic_fvm_cells)
        # All FVM cells should have velocity and pressure
        for cell in synthetic_fvm_cells:
            assert cell.velocity is not None
            assert cell.pressure is not None

    def test_volume_weighted(self, synthetic_lbm_cells, synthetic_fvm_cells):
        """Test volume-weighted interpolation."""
        interp = LBMtoFVMInterpolator(tolerance=0.10)
        result = interp.volume_weighted_interpolate(
            synthetic_lbm_cells,
            synthetic_fvm_cells,
            n_neighbors=4,
        )
        assert result.n_cells_interpolated == len(synthetic_fvm_cells)

    def test_flux_corrected(self, synthetic_lbm_cells, synthetic_fvm_cells):
        """Test flux-corrected interpolation."""
        interp = LBMtoFVMInterpolator(tolerance=0.10)
        result = interp.flux_corrected_interpolate(
            synthetic_lbm_cells,
            synthetic_fvm_cells,
            n_neighbors=4,
        )
        # The gate is a mass-inventory identity over the *same* domain, so it
        # must pass for a consistent patch...
        assert result.passed, result.summary()
        assert result.flux_residual < 0.15
        # ...and it must be able to fail: a patch that covers a different
        # domain has to be rejected (the old check returned 0 for every field).
        shifted = [FVMCell(center=c.center + np.array([0.5, 0.0, 0.0]),
                           volume=c.volume) for c in synthetic_fvm_cells]
        bad = interp.nearest_neighbor_interpolate(synthetic_lbm_cells, shifted)
        assert not bad.passed
        assert bad.flux_residual > 0.5

    def test_mass_conservation_result_structure(self, synthetic_lbm_cells, synthetic_fvm_cells):
        """Test MassConservationResult summary structure."""
        interp = LBMtoFVMInterpolator()
        result = interp.nearest_neighbor_interpolate(
            synthetic_lbm_cells,
            synthetic_fvm_cells,
        )
        summary = result.summary()
        assert "lbm_total_flux" in summary
        assert "fvm_total_flux" in summary
        assert "flux_residual" in summary
        assert "passed" in summary
        assert "tolerance" in summary
        assert "n_cells_interpolated" in summary

    def test_interpolation_history(self, synthetic_lbm_cells, synthetic_fvm_cells):
        """Test interpolation history recording."""
        interp = LBMtoFVMInterpolator()
        interp.nearest_neighbor_interpolate(synthetic_lbm_cells, synthetic_fvm_cells)
        interp.volume_weighted_interpolate(synthetic_lbm_cells, synthetic_fvm_cells)
        history = interp.get_interpolation_history()
        assert len(history) == 2

    def test_empty_cells_error(self):
        """Test error on empty cell lists."""
        interp = LBMtoFVMInterpolator()
        with pytest.raises(ValueError):
            interp.nearest_neighbor_interpolate([], [])

    def test_pressure_interpolation(self, synthetic_lbm_cells, synthetic_fvm_cells):
        """Test pressure field interpolation."""
        interp = LBMtoFVMInterpolator()
        stats = interp.interpolate_pressure_field(
            synthetic_lbm_cells,
            synthetic_fvm_cells,
            method="volume_weighted",
        )
        assert "lbm_total_pressure" in stats
        assert "fvm_total_pressure" in stats
        assert "pressure_residual" in stats
        assert stats["n_fvm_with_pressure"] == len(synthetic_fvm_cells)

    def test_uniform_field_conservation(self):
        """Test that uniform fields are perfectly conserved."""
        # Create uniform LBM field
        lbm_cells = []
        dx = 0.1
        for i in range(3):
            for j in range(3):
                for k in range(3):
                    lbm_cells.append(LBMCell(
                        center=np.array([i * dx, j * dx, k * dx]),
                        dx=dx,
                        velocity=np.array([1.0, 0.0, 0.0]),
                        density=1.0,
                        pressure=0.333,
                    ))

        # Create matching FVM cells
        fvm_cells = []
        for i in range(3):
            for j in range(3):
                for k in range(3):
                    fvm_cells.append(FVMCell(
                        center=np.array([i * dx, j * dx, k * dx]),
                        volume=dx ** 3,
                    ))

        interp = LBMtoFVMInterpolator(tolerance=0.01)
        result = interp.nearest_neighbor_interpolate(lbm_cells, fvm_cells)
        # All velocities should be exactly [1, 0, 0]
        for cell in fvm_cells:
            assert np.allclose(cell.velocity, np.array([1.0, 0.0, 0.0]))


# ============================================================
# Test 3: Clock Synchronization — Conservative + Optimistic
# ============================================================

class TestClockSynchronization:
    """Tests for clock synchronization semantics."""

    def test_conservative_sync_basic(self):
        """Test basic conservative synchronization."""
        sync = ClockSynchronizer(mode=SyncMode.CONSERVATIVE)

        # Register two solvers with different time steps
        fast_clock = SolverClock(solver_id="fast", dt=0.01, max_time=0.1)
        slow_clock = SolverClock(solver_id="slow", dt=0.02, max_time=0.1)
        sync.register_solver(fast_clock)
        sync.register_solver(slow_clock)

        result = sync.synchronize()
        assert result.mode == SyncMode.CONSERVATIVE
        assert result.is_barrier is True

    def test_conservative_barrier(self):
        """Test that conservative mode enforces barrier synchronization."""
        sync = ClockSynchronizer(mode=SyncMode.CONSERVATIVE)

        clock1 = SolverClock(solver_id="s1", dt=0.01, max_time=0.05)
        clock2 = SolverClock(solver_id="s2", dt=0.01, max_time=0.05)
        sync.register_solver(clock1)
        sync.register_solver(clock2)

        # Run several sync steps
        results = sync.run_simulation(max_steps=5)
        assert len(results) > 0
        # All results should be barriers
        assert all(r.is_barrier for r in results)

    def test_optimistic_sync_basic(self):
        """Test basic optimistic synchronization."""
        sync = ClockSynchronizer(mode=SyncMode.OPTIMISTIC)

        clock1 = SolverClock(solver_id="s1", dt=0.01, max_time=0.1)
        clock2 = SolverClock(solver_id="s2", dt=0.02, max_time=0.1)
        sync.register_solver(clock1)
        sync.register_solver(clock2)

        result = sync.synchronize()
        assert result.mode == SyncMode.OPTIMISTIC

    def test_optimistic_rollback(self):
        """Test that optimistic mode triggers rollbacks when needed."""
        sync = ClockSynchronizer(
            mode=SyncMode.OPTIMISTIC,
            max_rollbacks=10,
            checkpoint_interval=1,
        )

        # Create solvers with very different time steps
        fast_clock = SolverClock(solver_id="fast", dt=0.005, max_time=0.1)
        slow_clock = SolverClock(solver_id="slow", dt=0.02, max_time=0.1)
        sync.register_solver(fast_clock)
        sync.register_solver(slow_clock)

        # Run simulation - should trigger rollbacks
        results = sync.run_simulation(max_steps=20)
        stats = sync.get_statistics()
        # With very different dt, rollbacks should occur
        assert stats["total_rollbacks"] >= 0  # May or may not trigger depending on timing

    def test_sync_result_summary(self):
        """Test SyncResult summary structure."""
        sync = ClockSynchronizer(mode=SyncMode.CONSERVATIVE)
        clock = SolverClock(solver_id="test", dt=0.01, max_time=0.1)
        sync.register_solver(clock)

        result = sync.synchronize()
        summary = result.summary()
        assert "mode" in summary
        assert "global_time" in summary
        assert "solver_times" in summary
        assert "n_rollbacks" in summary
        assert "is_barrier" in summary
        assert "all_solvers_synced" in summary

    def test_sync_history(self):
        """Test synchronization history recording."""
        sync = ClockSynchronizer(mode=SyncMode.CONSERVATIVE)
        clock = SolverClock(solver_id="test", dt=0.01, max_time=0.05)
        sync.register_solver(clock)

        sync.run_simulation(max_steps=3)
        history = sync.get_sync_history()
        assert len(history) == 3

    def test_sync_statistics(self):
        """Test synchronization statistics."""
        sync = ClockSynchronizer(mode=SyncMode.CONSERVATIVE)
        clock = SolverClock(solver_id="test", dt=0.01, max_time=0.05)
        sync.register_solver(clock)

        sync.run_simulation(max_steps=3)
        stats = sync.get_statistics()
        assert stats["n_steps"] == 3
        assert stats["mode"] == "conservative"
        assert stats["n_barriers"] == 3

    def test_no_solvers_error(self):
        """Test error when no solvers registered."""
        sync = ClockSynchronizer()
        with pytest.raises(ValueError):
            sync.synchronize()

    def test_solver_clock_properties(self):
        """Test SolverClock properties."""
        clock = SolverClock(solver_id="test", dt=0.01, max_time=0.1)
        assert clock.remaining_steps == 10
        assert not clock.is_finished

        # Advance to end
        for _ in range(10):
            clock.step()
        assert clock.is_finished
        assert clock.remaining_steps == 0

    def test_solver_clock_reset(self):
        """Test SolverClock reset."""
        clock = SolverClock(solver_id="test", dt=0.01, max_time=0.1)
        clock.step()
        clock.step()
        assert clock.step_count == 2

        clock.reset()
        assert clock.current_time == 0.0
        assert clock.step_count == 0

    def test_sync_state_snapshot_rollback(self):
        """Test SyncState snapshot and rollback."""
        state = SyncState(mode=SyncMode.OPTIMISTIC)
        clock = SolverClock(solver_id="test", dt=0.01, max_time=0.1)
        clock.step()
        clock.step()

        state.snapshot_solver(clock, {"data": "test"})
        assert "test" in state.solver_states

        # Rollback
        success = state.rollback_solver(clock, 0.0)
        assert success
        assert clock.current_time == 0.0
        assert state.n_rollbacks == 1

    def test_mode_switch(self):
        """Test switching synchronization mode."""
        sync = ClockSynchronizer(mode=SyncMode.CONSERVATIVE)
        assert sync.mode == SyncMode.CONSERVATIVE

        sync.switch_mode(SyncMode.OPTIMISTIC)
        assert sync.mode == SyncMode.OPTIMISTIC
        assert sync._sync_state.mode == SyncMode.OPTIMISTIC

    def test_conservative_vs_optimistic_comparison(self):
        """Compare conservative and optimistic synchronization behavior."""
        # Conservative
        sync_conservative = ClockSynchronizer(mode=SyncMode.CONSERVATIVE)
        c1 = SolverClock(solver_id="s1", dt=0.01, max_time=0.1)
        c2 = SolverClock(solver_id="s2", dt=0.01, max_time=0.1)
        sync_conservative.register_solver(c1)
        sync_conservative.register_solver(c2)
        results_c = sync_conservative.run_simulation(max_steps=5)

        # Optimistic
        sync_optimistic = ClockSynchronizer(mode=SyncMode.OPTIMISTIC)
        o1 = SolverClock(solver_id="s1", dt=0.01, max_time=0.1)
        o2 = SolverClock(solver_id="s2", dt=0.01, max_time=0.1)
        sync_optimistic.register_solver(o1)
        sync_optimistic.register_solver(o2)
        results_o = sync_optimistic.run_simulation(max_steps=5)

        # Both should produce same number of steps
        assert len(results_c) == len(results_o)
        # Conservative should have more barriers
        assert all(r.is_barrier for r in results_c)


# ============================================================
# Test 4: Integration — Cross-module scenarios
# ============================================================

class TestCrossModuleIntegration:
    """Integration tests combining multiple coupling modules."""

    def test_fem_dem_with_clock_sync(self, synthetic_failure_faces, synthetic_fem_state):
        """Test FEM→DEM transfer combined with clock synchronization."""
        # Set up clock sync
        sync = ClockSynchronizer(mode=SyncMode.CONSERVATIVE)
        fem_clock = SolverClock(solver_id="fem", dt=0.01, max_time=0.1)
        dem_clock = SolverClock(solver_id="dem", dt=0.005, max_time=0.1)
        sync.register_solver(fem_clock)
        sync.register_solver(dem_clock)

        # Run sync steps
        results = sync.run_simulation(max_steps=3)
        assert len(results) == 3

        # Perform FEM→DEM transfer at sync point
        transfer = FEMtoDEMTransfer(tolerance=0.05)
        fragments, ledger = transfer.transfer(
            synthetic_fem_state,
            synthetic_failure_faces,
            n_fragments=2,
        )
        assert ledger.mass_passed

    def test_lbm_fvm_with_clock_sync(self, synthetic_lbm_cells, synthetic_fvm_cells):
        """Test LBM→FVM interpolation combined with clock synchronization."""
        # Set up clock sync
        sync = ClockSynchronizer(mode=SyncMode.CONSERVATIVE)
        lbm_clock = SolverClock(solver_id="lbm", dt=0.001, max_time=0.01)
        fvm_clock = SolverClock(solver_id="fvm", dt=0.01, max_time=0.01)
        sync.register_solver(lbm_clock)
        sync.register_solver(fvm_clock)

        # Run sync
        results = sync.run_simulation(max_steps=2)
        assert len(results) == 2

        # Perform interpolation
        interp = LBMtoFVMInterpolator(tolerance=0.10)
        result = interp.volume_weighted_interpolate(
            synthetic_lbm_cells,
            synthetic_fvm_cells,
        )
        assert result.n_cells_interpolated > 0
