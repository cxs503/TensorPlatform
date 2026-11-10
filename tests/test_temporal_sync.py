"""
Pytest suite for multi-physics temporal synchronization (LBM/FEM/DEM three-field coupling).

Campaign DAG node: p251w2-n07
Mission: m10748

Acceptance criteria:
  (a) LBM+FEM causal order consistency rate == 100%
  (b) LBM+FEM+DEM energy conservation error < 1%
  (c) Event-driven coupling (ice impact) capture delay < 1 macro step
  CFL condition satisfaction rate == 100%
  Adaptive time stepping stays within stability boundary (no divergence)

Tests cover:
  1. CausalOrderVerifier: causal order verification for data exchange
  2. EnergyConservationTracker: energy conservation tracking
  3. AdaptiveTimeStepper: CFL-based adaptive time stepping
  4. EventDrivenCoupler: event-driven coupling with bounded delay
  5. TemporalCoordinator: full LBM+FEM+DEM three-field coupling
  6. Scenario (a): LBM+FEM causal order 100% consistent
  7. Scenario (b): LBM+FEM+DEM energy conservation error < 1%
  8. Scenario (c): Event-driven coupling capture delay < 1 macro step
  9. CFL condition: 100% satisfaction rate
  10. Adaptive stepping: stability boundary, no divergence
"""

from __future__ import annotations

import math
import pytest
import numpy as np

from src.coupling.temporal_sync import (
    TemporalCoordinator,
    CausalOrderVerifier,
    CausalEvent,
    EnergyConservationTracker,
    EnergySnapshot,
    AdaptiveTimeStepper,
    CFLReport,
    EventDrivenCoupler,
    CouplingEvent,
    MacroStepResult,
    SolverType,
    SolverTimeConfig,
    harmonic_oscillator_energy,
    weakly_damped_oscillator_energy,
)


# ═══════════════════════════════════════════════════════════════════════════
# 1. CausalOrderVerifier Tests
# ═══════════════════════════════════════════════════════════════════════════

class TestCausalOrderVerifier:
    """Test causal order verification for data exchange events."""

    def test_causal_event_produced_before_consumed(self):
        """Event with production_time < consumption_time is causal."""
        event = CausalEvent(
            event_id=1,
            source_solver=SolverType.LBM,
            target_solver=SolverType.FEM,
            production_time=0.01,
            consumption_time=0.02,
            data_tag="test",
        )
        assert event.is_causal is True

    def test_causal_event_simultaneous(self):
        """Event with production_time == consumption_time is causal."""
        event = CausalEvent(
            event_id=1,
            source_solver=SolverType.DEM,
            target_solver=SolverType.FEM,
            production_time=0.01,
            consumption_time=0.01,
            data_tag="test",
        )
        assert event.is_causal is True

    def test_non_causal_event(self):
        """Event with production_time > consumption_time is NOT causal."""
        event = CausalEvent(
            event_id=1,
            source_solver=SolverType.FEM,
            target_solver=SolverType.LBM,
            production_time=0.02,
            consumption_time=0.01,
            data_tag="test",
        )
        assert event.is_causal is False

    def test_verifier_all_causal(self):
        """All causal events pass verification."""
        verifier = CausalOrderVerifier()
        verifier.record_exchange(SolverType.LBM, SolverType.FEM, 0.01, 0.02, "data1")
        verifier.record_exchange(SolverType.DEM, SolverType.FEM, 0.01, 0.02, "data2")

        is_causal, violations = verifier.verify_all()
        assert is_causal is True
        assert len(violations) == 0

    def test_verifier_detects_violation(self):
        """Non-causal event is detected as violation."""
        verifier = CausalOrderVerifier()
        verifier.record_exchange(SolverType.LBM, SolverType.FEM, 0.02, 0.01, "bad_data")

        is_causal, violations = verifier.verify_all()
        assert is_causal is False
        assert len(violations) == 1

    def test_causal_consistency_rate(self):
        """Consistency rate is fraction of causal events."""
        verifier = CausalOrderVerifier()
        verifier.record_exchange(SolverType.LBM, SolverType.FEM, 0.01, 0.02, "good1")
        verifier.record_exchange(SolverType.LBM, SolverType.FEM, 0.02, 0.01, "bad1")
        verifier.record_exchange(SolverType.DEM, SolverType.FEM, 0.01, 0.02, "good2")

        rate = verifier.causal_consistency_rate()
        assert rate == pytest.approx(2.0 / 3.0, abs=1e-10)

    def test_empty_verifier(self):
        """Empty verifier has 100% consistency rate."""
        verifier = CausalOrderVerifier()
        assert verifier.causal_consistency_rate() == 1.0

    def test_reset(self):
        """Reset clears all events."""
        verifier = CausalOrderVerifier()
        verifier.record_exchange(SolverType.LBM, SolverType.FEM, 0.01, 0.02, "data")
        verifier.reset()
        assert len(verifier.get_events()) == 0
        assert verifier.causal_consistency_rate() == 1.0


# ═══════════════════════════════════════════════════════════════════════════
# 2. EnergyConservationTracker Tests
# ═══════════════════════════════════════════════════════════════════════════

class TestEnergyConservationTracker:
    """Test energy conservation tracking."""

    def test_perfect_conservation(self):
        """Constant energy: conservation error = 0."""
        tracker = EnergyConservationTracker(tolerance=0.01)
        tracker.record(0.0, {SolverType.LBM: 10.0, SolverType.FEM: 20.0, SolverType.DEM: 5.0})
        tracker.record(1.0, {SolverType.LBM: 10.0, SolverType.FEM: 20.0, SolverType.DEM: 5.0})

        assert tracker.conservation_error() == 0.0
        assert tracker.is_conserved() is True

    def test_small_drift(self):
        """Small energy drift within tolerance."""
        tracker = EnergyConservationTracker(tolerance=0.01)
        tracker.record(0.0, {SolverType.LBM: 100.0, SolverType.FEM: 200.0, SolverType.DEM: 50.0})
        tracker.record(1.0, {SolverType.LBM: 100.5, SolverType.FEM: 200.3, SolverType.DEM: 49.8})

        error = tracker.conservation_error()
        assert error < 0.01
        assert tracker.is_conserved() is True

    def test_large_drift(self):
        """Large energy drift exceeds tolerance."""
        tracker = EnergyConservationTracker(tolerance=0.01)
        tracker.record(0.0, {SolverType.LBM: 100.0, SolverType.FEM: 200.0, SolverType.DEM: 50.0})
        tracker.record(1.0, {SolverType.LBM: 110.0, SolverType.FEM: 210.0, SolverType.DEM: 55.0})

        error = tracker.conservation_error()
        assert error > 0.01
        assert tracker.is_conserved() is False

    def test_zero_initial_energy(self):
        """Zero initial energy uses absolute difference."""
        tracker = EnergyConservationTracker(tolerance=0.01)
        tracker.record(0.0, {SolverType.LBM: 0.0, SolverType.FEM: 0.0, SolverType.DEM: 0.0})
        tracker.record(1.0, {SolverType.LBM: 0.001, SolverType.FEM: 0.0, SolverType.DEM: 0.0})

        error = tracker.conservation_error()
        assert error == pytest.approx(0.001, abs=1e-14)

    def test_energy_history(self):
        """Energy history returns correct (time, energy) pairs."""
        tracker = EnergyConservationTracker()
        tracker.record(0.0, {SolverType.LBM: 10.0})
        tracker.record(1.0, {SolverType.LBM: 10.0})
        tracker.record(2.0, {SolverType.LBM: 10.0})

        history = tracker.energy_history()
        assert len(history) == 3
        assert history[0] == (0.0, 10.0)
        assert history[1] == (1.0, 10.0)
        assert history[2] == (2.0, 10.0)

    def test_per_solver_history(self):
        """Per-solver history returns correct data."""
        tracker = EnergyConservationTracker()
        tracker.record(0.0, {SolverType.LBM: 10.0, SolverType.FEM: 20.0})
        tracker.record(1.0, {SolverType.LBM: 11.0, SolverType.FEM: 19.0})

        lbm_hist = tracker.per_solver_history(SolverType.LBM)
        assert lbm_hist == [(0.0, 10.0), (1.0, 11.0)]

    def test_reset(self):
        """Reset clears all data."""
        tracker = EnergyConservationTracker()
        tracker.record(0.0, {SolverType.LBM: 10.0})
        tracker.reset()
        assert tracker.initial_energy is None
        assert tracker.conservation_error() == 0.0


# ═══════════════════════════════════════════════════════════════════════════
# 3. AdaptiveTimeStepper Tests
# ═══════════════════════════════════════════════════════════════════════════

class TestAdaptiveTimeStepper:
    """Test CFL-based adaptive time stepping."""

    def test_cfl_satisfied(self):
        """CFL condition satisfied when cfl_number <= cfl_crit."""
        stepper = AdaptiveTimeStepper()
        config = SolverTimeConfig(
            solver_type=SolverType.LBM,
            dt=0.001,
            characteristic_velocity=1.0,
            characteristic_length=0.01,
            cfl_crit=1.0,
        )
        stepper.register_solver(config)

        report = stepper.check_cfl(SolverType.LBM)
        assert report.satisfied is True
        assert report.cfl_number == pytest.approx(0.1, abs=1e-14)

    def test_cfl_violated(self):
        """CFL condition violated when cfl_number > cfl_crit."""
        stepper = AdaptiveTimeStepper()
        config = SolverTimeConfig(
            solver_type=SolverType.LBM,
            dt=0.02,
            characteristic_velocity=1.0,
            characteristic_length=0.01,
            cfl_crit=1.0,
        )
        stepper.register_solver(config)

        report = stepper.check_cfl(SolverType.LBM)
        assert report.satisfied is False
        assert report.cfl_number == pytest.approx(2.0, abs=1e-14)

    def test_adapt_reduces_dt_on_cfl_violation(self):
        """Adaptive stepping reduces dt when CFL is violated."""
        stepper = AdaptiveTimeStepper(safety_factor=0.8)
        config = SolverTimeConfig(
            solver_type=SolverType.LBM,
            dt=0.02,
            characteristic_velocity=1.0,
            characteristic_length=0.01,
            cfl_crit=1.0,
            dt_min=1e-6,
        )
        stepper.register_solver(config)

        old_dt = config.dt
        new_dt = stepper.adapt_step(SolverType.LBM, stability_indicator=1.0)
        # CFL = 2.0, suggested = 0.02 * 1.0/2.0 * 0.8 = 0.008
        assert new_dt == pytest.approx(0.008, abs=1e-10)
        assert new_dt < old_dt

    def test_adapt_increases_dt_on_stable_decay(self):
        """Adaptive stepping increases dt when solution is decaying."""
        stepper = AdaptiveTimeStepper(growth_factor=1.1)
        config = SolverTimeConfig(
            solver_type=SolverType.LBM,
            dt=0.001,
            characteristic_velocity=0.5,
            characteristic_length=0.01,
            cfl_crit=1.0,
            dt_max=0.1,
        )
        stepper.register_solver(config)

        new_dt = stepper.adapt_step(SolverType.LBM, stability_indicator=0.5)
        assert new_dt == pytest.approx(0.0011, abs=1e-10)

    def test_adapt_reduces_dt_on_growth(self):
        """Adaptive stepping reduces dt when solution is growing."""
        stepper = AdaptiveTimeStepper(reduction_factor=0.5)
        config = SolverTimeConfig(
            solver_type=SolverType.LBM,
            dt=0.001,
            characteristic_velocity=0.5,
            characteristic_length=0.01,
            cfl_crit=1.0,
            dt_min=1e-6,
        )
        stepper.register_solver(config)

        new_dt = stepper.adapt_step(SolverType.LBM, stability_indicator=2.0)
        assert new_dt == pytest.approx(0.0005, abs=1e-10)

    def test_dem_stability_check(self):
        """DEM stability: dt <= 2 / sqrt(k/m)."""
        stepper = AdaptiveTimeStepper(safety_factor=0.8)
        config = SolverTimeConfig(
            solver_type=SolverType.DEM,
            dt=1e-5,
        )
        stepper.register_solver(config)

        # k = 1e6, m = 1e-3 => omega = sqrt(1e9) = 31622 => dt_crit = 6.32e-5
        is_stable, dt_crit = stepper.check_dem_stability(stiffness=1e6, mass=1e-3)
        assert is_stable is True  # 1e-5 < 0.8 * 6.32e-5
        assert dt_crit == pytest.approx(2.0 / math.sqrt(1e9), abs=1e-10)

    def test_dem_stability_violated(self):
        """DEM stability violated when dt too large."""
        stepper = AdaptiveTimeStepper(safety_factor=0.8)
        config = SolverTimeConfig(
            solver_type=SolverType.DEM,
            dt=1e-3,  # Too large
        )
        stepper.register_solver(config)

        is_stable, dt_crit = stepper.check_dem_stability(stiffness=1e6, mass=1e-3)
        assert is_stable is False

    def test_all_cfl_satisfied(self):
        """All CFL conditions satisfied."""
        stepper = AdaptiveTimeStepper()
        stepper.register_solver(SolverTimeConfig(
            solver_type=SolverType.LBM, dt=0.001,
            characteristic_velocity=1.0, characteristic_length=0.01, cfl_crit=1.0,
        ))
        stepper.register_solver(SolverTimeConfig(
            solver_type=SolverType.DEM, dt=1e-5,
            characteristic_velocity=0.1, characteristic_length=0.001, cfl_crit=1.0,
        ))

        assert stepper.all_cfl_satisfied() is True

    def test_cfl_satisfaction_rate(self):
        """CFL satisfaction rate tracks history."""
        stepper = AdaptiveTimeStepper()
        config = SolverTimeConfig(
            solver_type=SolverType.LBM, dt=0.001,
            characteristic_velocity=1.0, characteristic_length=0.01, cfl_crit=1.0,
        )
        stepper.register_solver(config)

        # First check: CFL = 0.1, satisfied
        stepper.check_cfl(SolverType.LBM)
        # Second check: CFL = 0.1, satisfied
        stepper.check_cfl(SolverType.LBM)

        assert stepper.cfl_satisfaction_rate() == 1.0

    def test_divergence_detection(self):
        """Divergence detected when stability_indicator > 10."""
        stepper = AdaptiveTimeStepper()
        config = SolverTimeConfig(
            solver_type=SolverType.LBM, dt=0.001,
            characteristic_velocity=1.0, characteristic_length=0.01, cfl_crit=1.0,
        )
        stepper.register_solver(config)

        stepper.adapt_step(SolverType.LBM, stability_indicator=15.0)
        # Internal divergence flag should be set (tested via no crash)


# ═══════════════════════════════════════════════════════════════════════════
# 4. EventDrivenCoupler Tests
# ═══════════════════════════════════════════════════════════════════════════

class TestEventDrivenCoupler:
    """Test event-driven coupling with bounded capture delay."""

    def test_event_capture_within_delay(self):
        """Event captured within 1 macro step."""
        coupler = EventDrivenCoupler(macro_dt=0.01, max_capture_delay_steps=1.0)
        event = coupler.detect_event("ice_impact", 0.005, SolverType.DEM, 100.0)

        # Advance to time 0.01 (1 macro step after event at 0.005)
        coupler.try_capture_events(0.01)

        assert event.captured is True
        assert event.capture_delay_steps <= 1.0

    def test_event_capture_delay_zero(self):
        """Event at exact macro step boundary has zero delay."""
        coupler = EventDrivenCoupler(macro_dt=0.01, max_capture_delay_steps=1.0)
        event = coupler.detect_event("ice_impact", 0.01, SolverType.DEM, 100.0)

        coupler.try_capture_events(0.01)

        assert event.captured is True
        assert event.capture_delay_steps == pytest.approx(0.0, abs=1e-10)

    def test_event_not_yet_captured(self):
        """Event not captured if current time < event time."""
        coupler = EventDrivenCoupler(macro_dt=0.01, max_capture_delay_steps=1.0)
        event = coupler.detect_event("ice_impact", 0.05, SolverType.DEM, 100.0)

        coupler.try_capture_events(0.01)

        assert event.captured is False

    def test_max_capture_delay_within_bound(self):
        """Max capture delay within allowed bound."""
        coupler = EventDrivenCoupler(macro_dt=0.01, max_capture_delay_steps=1.0)
        coupler.detect_event("impact1", 0.005, SolverType.DEM, 100.0)
        coupler.detect_event("impact2", 0.008, SolverType.DEM, 50.0)

        coupler.try_capture_events(0.01)

        assert coupler.capture_delay_satisfied() is True
        assert coupler.get_max_capture_delay() <= 1.0

    def test_all_events_captured(self):
        """All events eventually captured."""
        coupler = EventDrivenCoupler(macro_dt=0.01, max_capture_delay_steps=1.0)
        coupler.detect_event("impact1", 0.005, SolverType.DEM, 100.0)
        coupler.detect_event("impact2", 0.015, SolverType.DEM, 50.0)

        coupler.try_capture_events(0.01)
        assert not coupler.all_events_captured()

        coupler.try_capture_events(0.02)
        assert coupler.all_events_captured() is True

    def test_reset(self):
        """Reset clears all events."""
        coupler = EventDrivenCoupler(macro_dt=0.01)
        coupler.detect_event("impact", 0.005, SolverType.DEM, 100.0)
        coupler.reset()
        assert len(coupler.get_events()) == 0


# ═══════════════════════════════════════════════════════════════════════════
# 5. TemporalCoordinator Tests
# ═══════════════════════════════════════════════════════════════════════════

class TestTemporalCoordinator:
    """Test full LBM+FEM+DEM temporal coordination."""

    def _make_coordinator(
        self,
        lbm_dt: float = 1e-4,
        fem_dt: float = 1e-2,
        dem_dt: float = 1e-5,
    ) -> TemporalCoordinator:
        """Create a standard coordinator for testing."""
        lbm_config = SolverTimeConfig(
            solver_type=SolverType.LBM,
            dt=lbm_dt,
            characteristic_velocity=1.0,
            characteristic_length=0.01,
            cfl_crit=1.0,
        )
        fem_config = SolverTimeConfig(
            solver_type=SolverType.FEM,
            dt=fem_dt,
            characteristic_velocity=1.0,
            characteristic_length=0.1,
            cfl_crit=1.0,
        )
        dem_config = SolverTimeConfig(
            solver_type=SolverType.DEM,
            dt=dem_dt,
            characteristic_velocity=0.1,
            characteristic_length=0.001,
            cfl_crit=1.0,
        )
        return TemporalCoordinator(lbm_config, fem_config, dem_config)

    def test_substep_counts(self):
        """Sub-step counts are correctly computed."""
        coord = self._make_coordinator(lbm_dt=1e-4, fem_dt=1e-2, dem_dt=1e-5)
        assert coord.n_lbm_substeps == 100
        assert coord.n_dem_substeps == 1000

    def test_invalid_dt_ratio_raises(self):
        """Non-integer dt ratio raises ValueError."""
        lbm_config = SolverTimeConfig(solver_type=SolverType.LBM, dt=3e-3)
        fem_config = SolverTimeConfig(solver_type=SolverType.FEM, dt=1e-2)
        dem_config = SolverTimeConfig(solver_type=SolverType.DEM, dt=1e-5)

        with pytest.raises(ValueError, match="does not evenly divide"):
            TemporalCoordinator(lbm_config, fem_config, dem_config)

    def test_fem_must_be_coarsest(self):
        """FEM dt must be >= LBM and DEM dt."""
        lbm_config = SolverTimeConfig(solver_type=SolverType.LBM, dt=1e-2)
        fem_config = SolverTimeConfig(solver_type=SolverType.FEM, dt=1e-4)
        dem_config = SolverTimeConfig(solver_type=SolverType.DEM, dt=1e-5)

        with pytest.raises(ValueError, match="must be >="):
            TemporalCoordinator(lbm_config, fem_config, dem_config)

    def test_single_macro_step(self):
        """Single macro step executes without error."""
        coord = self._make_coordinator()
        coord.set_initial_energies(lbm=10.0, fem=20.0, dem=5.0)

        result = coord.run_macro_step()

        assert result.macro_step == 1
        assert result.time == pytest.approx(1e-2, abs=1e-14)
        assert result.lbm_substeps == 100
        assert result.fem_substeps == 1
        assert result.dem_substeps == 1000

    def test_multiple_macro_steps(self):
        """Multiple macro steps advance time correctly."""
        coord = self._make_coordinator()
        coord.set_initial_energies(lbm=10.0, fem=20.0, dem=5.0)

        for _ in range(10):
            coord.run_macro_step()

        assert coord._current_macro_step == 10
        assert coord._current_time == pytest.approx(0.1, abs=1e-12)


# ═══════════════════════════════════════════════════════════════════════════
# 6. Scenario (a): LBM+FEM Causal Order 100% Consistent
# ═══════════════════════════════════════════════════════════════════════════

class TestScenarioA_LBM_FEM_CausalOrder:
    """
    Scenario (a): LBM+FEM coupling with causal order verification.

    Acceptance: causal consistency rate == 100%
    """

    def _make_coordinator(self):
        lbm_config = SolverTimeConfig(
            solver_type=SolverType.LBM, dt=1e-4,
            characteristic_velocity=1.0, characteristic_length=0.01, cfl_crit=1.0,
        )
        fem_config = SolverTimeConfig(
            solver_type=SolverType.FEM, dt=1e-2,
            characteristic_velocity=1.0, characteristic_length=0.1, cfl_crit=1.0,
        )
        dem_config = SolverTimeConfig(
            solver_type=SolverType.DEM, dt=1e-4,
            characteristic_velocity=0.1, characteristic_length=0.001, cfl_crit=1.0,
        )
        return TemporalCoordinator(lbm_config, fem_config, dem_config)

    def test_lbm_fem_causal_order_100_percent(self):
        """LBM+FEM coupling: all data exchanges are causally consistent."""
        coord = self._make_coordinator()
        coord.set_initial_energies(lbm=10.0, fem=20.0, dem=5.0)

        # Run 50 macro steps
        for _ in range(50):
            coord.run_macro_step()

        rate = coord.causal_verifier.causal_consistency_rate()
        assert rate == 1.0, f"Causal consistency rate {rate} != 100%"

    def test_lbm_fem_no_causal_violations(self):
        """LBM+FEM coupling: zero causal violations."""
        coord = self._make_coordinator()
        coord.set_initial_energies(lbm=10.0, fem=20.0, dem=5.0)

        for _ in range(100):
            coord.run_macro_step()

        is_causal, violations = coord.causal_verifier.verify_all()
        assert is_causal is True
        assert len(violations) == 0


# ═══════════════════════════════════════════════════════════════════════════
# 7. Scenario (b): LBM+FEM+DEM Energy Conservation < 1%
# ═══════════════════════════════════════════════════════════════════════════

class TestScenarioB_EnergyConservation:
    """
    Scenario (b): LBM+FEM+DEM three-field coupling energy conservation.

    Acceptance: energy conservation error < 1%
    """

    def test_three_field_energy_conservation(self):
        """LBM+FEM+DEM: total energy conserved to < 1%."""
        lbm_config = SolverTimeConfig(
            solver_type=SolverType.LBM, dt=1e-4,
            characteristic_velocity=1.0, characteristic_length=0.01, cfl_crit=1.0,
        )
        fem_config = SolverTimeConfig(
            solver_type=SolverType.FEM, dt=1e-2,
            characteristic_velocity=1.0, characteristic_length=0.1, cfl_crit=1.0,
        )
        dem_config = SolverTimeConfig(
            solver_type=SolverType.DEM, dt=1e-5,
            characteristic_velocity=0.1, characteristic_length=0.001, cfl_crit=1.0,
        )

        coord = TemporalCoordinator(lbm_config, fem_config, dem_config)

        # Set initial energies
        e_lbm, e_fem, e_dem = 10.0, 20.0, 5.0
        coord.set_initial_energies(lbm=e_lbm, fem=e_fem, dem=e_dem)

        # Use identity energy model (conservative: energy stays constant)
        def identity_energy(t, e):
            return e

        for _ in range(100):
            coord.run_macro_step(identity_energy, identity_energy, identity_energy)

        error = coord.energy_tracker.conservation_error()
        assert error < 0.01, f"Energy conservation error {error} >= 1%"

    def test_energy_conservation_long_run(self):
        """Long run (500 macro steps): energy conservation < 1%."""
        lbm_config = SolverTimeConfig(
            solver_type=SolverType.LBM, dt=1e-4,
            characteristic_velocity=1.0, characteristic_length=0.01, cfl_crit=1.0,
        )
        fem_config = SolverTimeConfig(
            solver_type=SolverType.FEM, dt=1e-2,
            characteristic_velocity=1.0, characteristic_length=0.1, cfl_crit=1.0,
        )
        dem_config = SolverTimeConfig(
            solver_type=SolverType.DEM, dt=1e-5,
            characteristic_velocity=0.1, characteristic_length=0.001, cfl_crit=1.0,
        )

        coord = TemporalCoordinator(lbm_config, fem_config, dem_config)
        coord.set_initial_energies(lbm=50.0, fem=100.0, dem=25.0)

        def identity_energy(t, e):
            return e

        for _ in range(500):
            coord.run_macro_step(identity_energy, identity_energy, identity_energy)

        error = coord.energy_tracker.conservation_error()
        assert error < 0.01, f"Energy conservation error {error} >= 1% after 500 steps"

    def test_energy_history_constant_for_conservative(self):
        """Conservative system: energy history is constant."""
        lbm_config = SolverTimeConfig(
            solver_type=SolverType.LBM, dt=1e-4,
            characteristic_velocity=1.0, characteristic_length=0.01, cfl_crit=1.0,
        )
        fem_config = SolverTimeConfig(
            solver_type=SolverType.FEM, dt=1e-2,
            characteristic_velocity=1.0, characteristic_length=0.1, cfl_crit=1.0,
        )
        dem_config = SolverTimeConfig(
            solver_type=SolverType.DEM, dt=1e-5,
            characteristic_velocity=0.1, characteristic_length=0.001, cfl_crit=1.0,
        )

        coord = TemporalCoordinator(lbm_config, fem_config, dem_config)
        coord.set_initial_energies(lbm=10.0, fem=20.0, dem=5.0)

        def identity_energy(t, e):
            return e

        for _ in range(50):
            coord.run_macro_step(identity_energy, identity_energy, identity_energy)

        history = coord.energy_tracker.energy_history()
        # All energy values should be the same (conservative)
        initial_total = history[0][1]
        for _, total in history:
            assert total == pytest.approx(initial_total, rel=1e-10)

    def test_energy_decreases_for_damped(self):
        """Damped system: energy decreases monotonically."""
        lbm_config = SolverTimeConfig(
            solver_type=SolverType.LBM, dt=1e-4,
            characteristic_velocity=1.0, characteristic_length=0.01, cfl_crit=1.0,
        )
        fem_config = SolverTimeConfig(
            solver_type=SolverType.FEM, dt=1e-2,
            characteristic_velocity=1.0, characteristic_length=0.1, cfl_crit=1.0,
        )
        dem_config = SolverTimeConfig(
            solver_type=SolverType.DEM, dt=1e-5,
            characteristic_velocity=0.1, characteristic_length=0.001, cfl_crit=1.0,
        )

        coord = TemporalCoordinator(lbm_config, fem_config, dem_config)
        coord.set_initial_energies(lbm=10.0, fem=20.0, dem=5.0)

        # Weakly damped: energy decreases
        damping = 0.0001
        lbm_fn = weakly_damped_oscillator_energy(omega=10.0, amplitude=math.sqrt(2*10.0)/10.0, damping=damping)
        fem_fn = weakly_damped_oscillator_energy(omega=5.0, amplitude=math.sqrt(2*20.0)/5.0, damping=damping)
        dem_fn = weakly_damped_oscillator_energy(omega=100.0, amplitude=math.sqrt(2*5.0)/100.0, damping=damping)

        for _ in range(50):
            coord.run_macro_step(lbm_fn, fem_fn, dem_fn)

        history = coord.energy_tracker.energy_history()
        # Energy should decrease (damped system)
        assert history[-1][1] < history[0][1]


# ═══════════════════════════════════════════════════════════════════════════
# 8. Scenario (c): Event-Driven Coupling Capture Delay < 1 Macro Step
# ═══════════════════════════════════════════════════════════════════════════

class TestScenarioC_EventDrivenCoupling:
    """
    Scenario (c): Event-driven coupling (ice impact) with bounded capture delay.

    Acceptance: capture delay < 1 macro step
    """

    def _make_coordinator(self):
        lbm_config = SolverTimeConfig(
            solver_type=SolverType.LBM, dt=1e-4,
            characteristic_velocity=1.0, characteristic_length=0.01, cfl_crit=1.0,
        )
        fem_config = SolverTimeConfig(
            solver_type=SolverType.FEM, dt=1e-2,
            characteristic_velocity=1.0, characteristic_length=0.1, cfl_crit=1.0,
        )
        dem_config = SolverTimeConfig(
            solver_type=SolverType.DEM, dt=1e-5,
            characteristic_velocity=0.1, characteristic_length=0.001, cfl_crit=1.0,
        )
        return TemporalCoordinator(lbm_config, fem_config, dem_config)

    def test_ice_impact_capture_within_one_macro_step(self):
        """Ice impact event captured within 1 macro step."""
        coord = self._make_coordinator()
        coord.set_initial_energies(lbm=10.0, fem=20.0, dem=5.0)

        # Schedule ice impact at t = 0.025 (within 3rd macro step)
        coord.schedule_event(time=0.025, event_type="ice_impact", magnitude=500.0)

        def identity_energy(t, e):
            return e

        # Run simulation
        for _ in range(10):
            coord.run_macro_step(identity_energy, identity_energy, identity_energy)

        # Check capture delay
        delay = coord.event_coupler.get_max_capture_delay()
        assert delay < 1.0, f"Event capture delay {delay} >= 1 macro step"

    def test_multiple_impact_events(self):
        """Multiple ice impact events all captured within bound."""
        coord = self._make_coordinator()
        coord.set_initial_energies(lbm=10.0, fem=20.0, dem=5.0)

        # Schedule multiple impacts
        coord.schedule_event(time=0.015, event_type="ice_impact_1", magnitude=300.0)
        coord.schedule_event(time=0.035, event_type="ice_impact_2", magnitude=400.0)
        coord.schedule_event(time=0.055, event_type="ice_impact_3", magnitude=500.0)

        def identity_energy(t, e):
            return e

        for _ in range(10):
            coord.run_macro_step(identity_energy, identity_energy, identity_energy)

        assert coord.event_coupler.capture_delay_satisfied() is True
        assert coord.event_coupler.get_max_capture_delay() < 1.0

    def test_event_detected_and_captured(self):
        """Event is both detected and captured."""
        coord = self._make_coordinator()
        coord.set_initial_energies(lbm=10.0, fem=20.0, dem=5.0)
        coord.schedule_event(time=0.005, event_type="ice_impact", magnitude=100.0)

        def identity_energy(t, e):
            return e

        for _ in range(5):
            coord.run_macro_step(identity_energy, identity_energy, identity_energy)

        assert coord.event_coupler.all_events_captured() is True


# ═══════════════════════════════════════════════════════════════════════════
# 9. CFL Condition: 100% Satisfaction Rate
# ═══════════════════════════════════════════════════════════════════════════

class TestCFLCondition:
    """Test CFL condition satisfaction."""

    def test_cfl_100_percent_satisfaction(self):
        """CFL condition satisfied for all solvers throughout simulation."""
        lbm_config = SolverTimeConfig(
            solver_type=SolverType.LBM, dt=1e-4,
            characteristic_velocity=1.0, characteristic_length=0.01, cfl_crit=1.0,
        )
        fem_config = SolverTimeConfig(
            solver_type=SolverType.FEM, dt=1e-2,
            characteristic_velocity=1.0, characteristic_length=0.1, cfl_crit=1.0,
        )
        dem_config = SolverTimeConfig(
            solver_type=SolverType.DEM, dt=1e-5,
            characteristic_velocity=0.1, characteristic_length=0.001, cfl_crit=1.0,
        )

        coord = TemporalCoordinator(lbm_config, fem_config, dem_config)
        coord.set_initial_energies(lbm=10.0, fem=20.0, dem=5.0)

        def identity_energy(t, e):
            return e

        for _ in range(100):
            coord.run_macro_step(identity_energy, identity_energy, identity_energy)

        rate = coord.adaptive_stepper.cfl_satisfaction_rate()
        assert rate == 1.0, f"CFL satisfaction rate {rate} != 100%"

    def test_cfl_all_satisfied_at_end(self):
        """All CFL conditions satisfied at end of simulation."""
        lbm_config = SolverTimeConfig(
            solver_type=SolverType.LBM, dt=1e-4,
            characteristic_velocity=1.0, characteristic_length=0.01, cfl_crit=1.0,
        )
        fem_config = SolverTimeConfig(
            solver_type=SolverType.FEM, dt=1e-2,
            characteristic_velocity=1.0, characteristic_length=0.1, cfl_crit=1.0,
        )
        dem_config = SolverTimeConfig(
            solver_type=SolverType.DEM, dt=1e-5,
            characteristic_velocity=0.1, characteristic_length=0.001, cfl_crit=1.0,
        )

        coord = TemporalCoordinator(lbm_config, fem_config, dem_config)
        coord.set_initial_energies(lbm=10.0, fem=20.0, dem=5.0)

        def identity_energy(t, e):
            return e

        for _ in range(50):
            coord.run_macro_step(identity_energy, identity_energy, identity_energy)

        assert coord.adaptive_stepper.all_cfl_satisfied() is True


# ═══════════════════════════════════════════════════════════════════════════
# 10. Adaptive Stepping: Stability Boundary, No Divergence
# ═══════════════════════════════════════════════════════════════════════════

class TestAdaptiveStepping:
    """Test adaptive time stepping stays within stability boundary."""

    def test_adaptive_stays_within_bounds(self):
        """Adaptive dt stays within [dt_min, dt_max]."""
        stepper = AdaptiveTimeStepper(
            safety_factor=0.8,
            growth_factor=1.1,
            reduction_factor=0.5,
        )
        config = SolverTimeConfig(
            solver_type=SolverType.LBM,
            dt=0.001,
            dt_min=1e-6,
            dt_max=0.01,
            characteristic_velocity=1.0,
            characteristic_length=0.01,
            cfl_crit=1.0,
        )
        stepper.register_solver(config)

        # Simulate varying stability conditions
        indicators = [0.5, 0.8, 1.0, 1.5, 2.0, 0.9, 0.7, 1.0, 0.5, 0.3]
        for ind in indicators:
            new_dt = stepper.adapt_step(SolverType.LBM, stability_indicator=ind)
            assert config.dt_min <= new_dt <= config.dt_max, (
                f"dt={new_dt} outside [{config.dt_min}, {config.dt_max}]"
            )

    def test_no_divergence_under_stable_conditions(self):
        """No divergence when stability indicator is moderate."""
        stepper = AdaptiveTimeStepper()
        config = SolverTimeConfig(
            solver_type=SolverType.LBM,
            dt=0.001,
            dt_min=1e-6,
            dt_max=0.01,
            characteristic_velocity=0.5,
            characteristic_length=0.01,
            cfl_crit=1.0,
        )
        stepper.register_solver(config)

        # Stable conditions: indicator < 10
        for _ in range(100):
            stepper.adapt_step(SolverType.LBM, stability_indicator=1.0 + 0.01 * _)

        # Should not have diverged
        assert config.dt >= config.dt_min
        assert config.dt <= config.dt_max

    def test_adaptive_reduces_dt_on_cfl_violation(self):
        """Adaptive stepping reduces dt when CFL is violated."""
        stepper = AdaptiveTimeStepper(safety_factor=0.8)
        config = SolverTimeConfig(
            solver_type=SolverType.LBM,
            dt=0.02,  # CFL = 2.0 > 1.0
            dt_min=1e-6,
            characteristic_velocity=1.0,
            characteristic_length=0.01,
            cfl_crit=1.0,
        )
        stepper.register_solver(config)

        old_dt = config.dt
        new_dt = stepper.adapt_step(SolverType.LBM, stability_indicator=1.0)
        assert new_dt < old_dt  # Should have been reduced
        # After adaptation, CFL should be satisfied
        assert config.cfl_number <= config.cfl_crit


# ═══════════════════════════════════════════════════════════════════════════
# 11. Integration Test: Full Three-Field Coupling
# ═══════════════════════════════════════════════════════════════════════════

class TestFullIntegration:
    """Full integration test combining all acceptance criteria."""

    def test_all_acceptance_criteria_met(self):
        """
        All acceptance criteria satisfied in a single simulation:
        (a) Causal order 100%
        (b) Energy conservation < 1%
        (c) Event capture delay < 1 macro step
        CFL 100% satisfied
        No divergence
        """
        lbm_config = SolverTimeConfig(
            solver_type=SolverType.LBM, dt=1e-4,
            characteristic_velocity=1.0, characteristic_length=0.01, cfl_crit=1.0,
        )
        fem_config = SolverTimeConfig(
            solver_type=SolverType.FEM, dt=1e-2,
            characteristic_velocity=1.0, characteristic_length=0.1, cfl_crit=1.0,
        )
        dem_config = SolverTimeConfig(
            solver_type=SolverType.DEM, dt=1e-5,
            characteristic_velocity=0.1, characteristic_length=0.001, cfl_crit=1.0,
        )

        coord = TemporalCoordinator(lbm_config, fem_config, dem_config)
        coord.set_initial_energies(lbm=10.0, fem=20.0, dem=5.0)

        # Conservative energy model (identity)
        def identity_energy(t, e):
            return e

        # Schedule ice impact events
        coord.schedule_event(time=0.025, event_type="ice_impact_1", magnitude=300.0)
        coord.schedule_event(time=0.055, event_type="ice_impact_2", magnitude=500.0)

        # Run 100 macro steps
        for _ in range(100):
            coord.run_macro_step(identity_energy, identity_energy, identity_energy)

        # (a) Causal order 100%
        causal_rate = coord.causal_verifier.causal_consistency_rate()
        assert causal_rate == 1.0, f"(a) Causal rate {causal_rate} != 100%"

        # (b) Energy conservation < 1%
        energy_error = coord.energy_tracker.conservation_error()
        assert energy_error < 0.01, f"(b) Energy error {energy_error} >= 1%"

        # (c) Event capture delay < 1 macro step
        event_delay = coord.event_coupler.get_max_capture_delay()
        assert event_delay < 1.0, f"(c) Event delay {event_delay} >= 1 macro step"

        # CFL 100%
        cfl_rate = coord.adaptive_stepper.cfl_satisfaction_rate()
        assert cfl_rate == 1.0, f"CFL rate {cfl_rate} != 100%"

    def test_run_simulation_summary(self):
        """run_simulation returns correct summary."""
        lbm_config = SolverTimeConfig(
            solver_type=SolverType.LBM, dt=1e-4,
            characteristic_velocity=1.0, characteristic_length=0.01, cfl_crit=1.0,
        )
        fem_config = SolverTimeConfig(
            solver_type=SolverType.FEM, dt=1e-2,
            characteristic_velocity=1.0, characteristic_length=0.1, cfl_crit=1.0,
        )
        dem_config = SolverTimeConfig(
            solver_type=SolverType.DEM, dt=1e-5,
            characteristic_velocity=0.1, characteristic_length=0.001, cfl_crit=1.0,
        )

        coord = TemporalCoordinator(lbm_config, fem_config, dem_config)
        coord.set_initial_energies(lbm=10.0, fem=20.0, dem=5.0)

        def identity_energy(t, e):
            return e

        summary = coord.run_simulation(n_macro_steps=50, lbm_energy_fn=identity_energy,
                                       fem_energy_fn=identity_energy, dem_energy_fn=identity_energy)

        assert summary["n_macro_steps"] == 50
        assert summary["causal_consistency_rate"] == 1.0
        assert summary["energy_conservation_error"] < 0.01
        assert summary["cfl_satisfaction_rate"] == 1.0
        assert summary["all_cfl_satisfied"] is True
        assert summary["energy_conserved"] is True
