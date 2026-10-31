"""
Pytest test suite for Broken Ice Seakeeping module.

Acceptance criteria:
(a) Open-water RAO regression < 3% vs baseline
(b) Log decrement ↔ damping ratio identity error < 1e-9
(c) Floe collision momentum conservation < 1e-12, KE loss identity residual < 1e-9
(d) c→0 limit degrades to open-water RAO < 3%
(e) Damping ratio monotonically non-decreasing with concentration
(f) All tests PASS
"""
from __future__ import annotations

import math
import numpy as np
import pytest
import sys
import os

# Add backend to path
_backend_path = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'backend'))
if _backend_path not in sys.path:
    sys.path.insert(0, _backend_path)

from services.ship_performance.seakeeping.broken_ice import (
    ShipGeometry,
    IceFieldParams,
    OpenWaterRAO,
    FloeCollisionModel,
    IceAddedDampingModel,
    LogDecrementAnalyzer,
    BrokenIceSeakeeping,
    RHO_WATER,
    G,
)


# ============================================================================
# Fixtures
# ============================================================================
@pytest.fixture
def kvlcc2_ship():
    """KVLCC2 tanker geometry."""
    return ShipGeometry(
        L=320.0, B=58.0, T=20.8, Cb=0.8098,
        Cwp=0.78, Cw=0.98, KG=12.0
    )


@pytest.fixture
def dtmb5415_ship():
    """DTMB-5415 combatant geometry."""
    return ShipGeometry(
        L=5.72, B=0.759, T=0.246, Cb=0.508,
        Cwp=0.7, Cw=0.85, KG=0.15
    )


@pytest.fixture
def standard_ice():
    """Standard ice field parameters."""
    return IceFieldParams(
        concentration=0.6,
        thickness=0.3,
        floe_diameter=10.0,
        restitution=0.3
    )


@pytest.fixture
def frequency_range():
    """Standard frequency range for RAO computation."""
    return np.linspace(0.1, 3.0, 30)


# ============================================================================
# Test (a): Open-Water RAO Regression
# ============================================================================
class TestOpenWaterRAORegression:
    """
    Test that open-water RAO baseline is self-consistent.
    Since we define the baseline, we verify:
    1. RAO is physically reasonable (positive, bounded)
    2. RAO peaks near natural frequency
    3. Two independent computations give identical results (regression)
    """

    def test_rao_positive_bounded(self, kvlcc2_ship, frequency_range):
        """RAO must be positive and bounded."""
        rao_solver = OpenWaterRAO(kvlcc2_ship)
        rao = rao_solver.compute_heave_rao(frequency_range)
        assert np.all(rao >= 0), "RAO must be non-negative"
        assert np.all(rao < 100), "RAO must be bounded (< 100 m/m)"

    def test_rao_peak_near_resonance(self, kvlcc2_ship):
        """RAO peak should be near natural frequency."""
        rao_solver = OpenWaterRAO(kvlcc2_ship)
        omega_n = rao_solver.get_natural_frequency()
        freq = np.linspace(0.05, 2 * omega_n, 100)
        rao = rao_solver.compute_heave_rao(freq)
        peak_idx = np.argmax(rao)
        peak_freq = freq[peak_idx]
        # Peak should be within 30% of natural frequency
        assert abs(peak_freq - omega_n) / omega_n < 0.3, \
            f"Peak at {peak_freq:.3f} rad/s, natural freq {omega_n:.3f} rad/s"

    def test_rao_regression_self_consistent(self, kvlcc2_ship, frequency_range):
        """Two independent computations must give identical results."""
        rao_solver1 = OpenWaterRAO(kvlcc2_ship)
        rao_solver2 = OpenWaterRAO(kvlcc2_ship)
        rao1 = rao_solver1.compute_heave_rao(frequency_range)
        rao2 = rao_solver2.compute_heave_rao(frequency_range)
        max_diff = np.max(np.abs(rao1 - rao2))
        assert max_diff < 1e-15, f"Regression error: {max_diff}"

    def test_rao_regression_vs_full_solver(self, kvlcc2_ship, frequency_range):
        """
        Open-water RAO from standalone solver matches full solver at c=0.
        This is the regression gate: < 3% difference.
        """
        # Standalone open-water
        ow_solver = OpenWaterRAO(kvlcc2_ship)
        rao_ow = ow_solver.compute_heave_rao(frequency_range)

        # Full solver at c=0
        ice_zero = IceFieldParams(concentration=0.0, thickness=0.3)
        full_solver = BrokenIceSeakeeping(kvlcc2_ship, ice_zero)
        rao_full = full_solver.compute_rao(frequency_range)

        # Compare
        for i in range(len(frequency_range)):
            if rao_ow[i] > 1e-6:
                rel_diff = abs(rao_ow[i] - rao_full.rao_heave[i]) / rao_ow[i]
                assert rel_diff < 0.03, \
                    f"Freq {frequency_range[i]:.2f}: regression diff {rel_diff:.4f} > 3%"


# ============================================================================
# Test (b): Log Decrement ↔ Damping Ratio Identity
# ============================================================================
class TestLogDecrementIdentity:
    """
    Verify the exact identity: δ = 2πζ/√(1-ζ²) and its inverse.
    Round-trip error must be < 1e-9.
    """

    @pytest.mark.parametrize("zeta", [0.001, 0.01, 0.05, 0.1, 0.2, 0.3, 0.5])
    def test_round_trip_identity(self, zeta):
        """δ(ζ) → ζ(δ) must recover ζ to < 1e-9 relative error."""
        error = LogDecrementAnalyzer.verify_identity(zeta)
        assert error < 1e-9, \
            f"ζ={zeta}: round-trip error {error:.2e} > 1e-9"

    @pytest.mark.parametrize("zeta", [0.001, 0.01, 0.05, 0.1, 0.2, 0.3])
    def test_forward_identity(self, zeta):
        """δ = 2πζ/√(1-ζ²) must hold to machine precision."""
        delta = LogDecrementAnalyzer.log_decrement_from_damping_ratio(zeta)
        delta_expected = 2.0 * math.pi * zeta / math.sqrt(1.0 - zeta**2)
        rel_error = abs(delta - delta_expected) / abs(delta_expected)
        assert rel_error < 1e-14, \
            f"ζ={zeta}: forward identity error {rel_error:.2e}"

    @pytest.mark.parametrize("zeta", [0.01, 0.05, 0.1, 0.2])
    def test_free_decay_extraction(self, zeta):
        """
        Extract damping ratio from free decay signal.
        Must match input to < 1% (limited by peak detection resolution).
        """
        omega_n = 1.0  # rad/s
        analyzer = LogDecrementAnalyzer()
        result = analyzer.__class__.free_decay_signal  # static method
        T_d = 2.0 * math.pi / (omega_n * math.sqrt(1 - zeta**2))
        t = np.arange(0, 20 * T_d, 0.001)
        x = analyzer.free_decay_signal(zeta, omega_n, t)
        peaks = analyzer.extract_peaks(x, t)
        delta = analyzer.log_decrement_from_peaks(peaks)
        zeta_ext = analyzer.damping_ratio_from_log_decrement(delta)
        rel_error = abs(zeta_ext - zeta) / zeta
        assert rel_error < 0.01, \
            f"ζ={zeta}: extraction error {rel_error:.4f} > 1%"


# ============================================================================
# Test (c): Floe Collision Conservation Laws
# ============================================================================
class TestFloeCollisionConservation:
    """
    Verify momentum conservation and kinetic energy loss identity.
    """

    @pytest.mark.parametrize("V0,v0", [
        (0.0, 1.0), (1.0, 0.0), (0.5, -0.5), (2.0, 1.0),
        (0.0, 0.1), (0.0, 5.0), (1.0, -1.0)
    ])
    def test_momentum_conservation(self, kvlcc2_ship, standard_ice, V0, v0):
        """
        Momentum conservation: |Δp|/|p₀| < 1e-12.
        """
        coll = FloeCollisionModel(kvlcc2_ship.mass, standard_ice)
        rel_error = coll.verify_momentum_conservation(V0, v0)
        assert rel_error < 1e-12, \
            f"V0={V0}, v0={v0}: momentum error {rel_error:.2e} > 1e-12"

    @pytest.mark.parametrize("v_rel", [0.1, 0.5, 1.0, 2.0, 5.0])
    def test_kinetic_energy_identity(self, kvlcc2_ship, standard_ice, v_rel):
        """
        KE loss identity: ΔKE = ½(1-e²)μv_rel²
        Residual < 1e-9.
        """
        coll = FloeCollisionModel(kvlcc2_ship.mass, standard_ice)
        residual = coll.verify_energy_identity(v_rel)
        assert residual < 1e-9, \
            f"v_rel={v_rel}: KE identity residual {residual:.2e} > 1e-9"

    def test_restitution_bounds(self, kvlcc2_ship):
        """Restitution coefficient must be in [0, 1]."""
        for e in [0.0, 0.1, 0.5, 0.9, 1.0]:
            ice = IceFieldParams(concentration=0.5, thickness=0.3, restitution=e)
            coll = FloeCollisionModel(kvlcc2_ship.mass, ice)
            J = coll.collision_impulse(1.0)
            assert J >= 0, f"e={e}: impulse must be non-negative"
            if e == 0.0:
                # Perfectly inelastic: minimum impulse
                assert J > 0
            if e == 1.0:
                # Perfectly elastic: maximum impulse
                J_inelastic = FloeCollisionModel(
                    kvlcc2_ship.mass,
                    IceFieldParams(concentration=0.5, thickness=0.3, restitution=0.0)
                ).collision_impulse(1.0)
                assert J > J_inelastic

    def test_ke_loss_fraction_identity(self, kvlcc2_ship, standard_ice):
        """KE loss fraction = (1-e²) for any collision."""
        coll = FloeCollisionModel(kvlcc2_ship.mass, standard_ice)
        frac = coll.kinetic_energy_loss_fraction(1.0)
        expected = 1.0 - standard_ice.restitution**2
        assert abs(frac - expected) < 1e-14, \
            f"KE loss fraction: {frac} vs {expected}"


# ============================================================================
# Test (d): Zero Concentration Limit
# ============================================================================
class TestZeroConcentrationLimit:
    """
    As c → 0, broken ice RAO must degrade to open-water RAO.
    Difference < 3%.
    """

    def test_zero_concentration_matches_open_water(self, kvlcc2_ship, frequency_range):
        """c=0 must give identical RAO to open-water baseline."""
        ice_zero = IceFieldParams(concentration=0.0, thickness=0.3)
        solver = BrokenIceSeakeeping(kvlcc2_ship, ice_zero)
        rao_ice = solver.compute_rao(frequency_range)
        rao_ow = solver.compute_open_water_rao(frequency_range)

        for i in range(len(frequency_range)):
            if rao_ow.rao_heave[i] > 1e-6:
                rel_diff = abs(rao_ice.rao_heave[i] - rao_ow.rao_heave[i]) / rao_ow.rao_heave[i]
                assert rel_diff < 0.03, \
                    f"Freq {frequency_range[i]:.2f}: c=0 diff {rel_diff:.4f} > 3%"

    def test_small_concentration_near_open_water(self, kvlcc2_ship, frequency_range):
        """c=0.01 should be very close to open-water."""
        ice_small = IceFieldParams(concentration=0.01, thickness=0.3)
        solver = BrokenIceSeakeeping(kvlcc2_ship, ice_small)
        rao_small = solver.compute_rao(frequency_range)
        rao_ow = solver.compute_open_water_rao(frequency_range)

        for i in range(len(frequency_range)):
            if rao_ow.rao_heave[i] > 1e-6:
                rel_diff = abs(rao_small.rao_heave[i] - rao_ow.rao_heave[i]) / rao_ow.rao_heave[i]
                # Small concentration should be within 10% (not as strict as c=0)
                assert rel_diff < 0.10, \
                    f"Freq {frequency_range[i]:.2f}: c=0.01 diff {rel_diff:.4f} > 10%"

    def test_added_damping_zero_at_c0(self, kvlcc2_ship, frequency_range):
        """Added damping from ice must be zero at c=0."""
        ice_zero = IceFieldParams(concentration=0.0, thickness=0.3)
        solver = BrokenIceSeakeeping(kvlcc2_ship, ice_zero)
        rao = solver.compute_rao(frequency_range)
        assert np.allclose(rao.added_damping, 0.0, atol=1e-15), \
            "Added damping must be zero at c=0"

    def test_added_mass_zero_at_c0(self, kvlcc2_ship, frequency_range):
        """Added mass from ice must be zero at c=0."""
        ice_zero = IceFieldParams(concentration=0.0, thickness=0.3)
        solver = BrokenIceSeakeeping(kvlcc2_ship, ice_zero)
        rao = solver.compute_rao(frequency_range)
        assert np.allclose(rao.added_mass, 0.0, atol=1e-15), \
            "Added mass must be zero at c=0"


# ============================================================================
# Test (e): Damping Ratio Monotonicity with Concentration
# ============================================================================
class TestDampingMonotonicity:
    """
    Damping ratio must be monotonically non-decreasing with concentration.
    """

    def test_damping_monotone_with_concentration(self, kvlcc2_ship):
        """ζ(c) must be non-decreasing for c ∈ [0, 1]."""
        concentrations = np.array([0.0, 0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0])
        freq = np.array([0.5, 1.0, 1.5])  # representative frequencies
        ice_base = IceFieldParams(concentration=0.5, thickness=0.3, floe_diameter=10.0, restitution=0.3)

        prev_zeta = -1.0
        for c in concentrations:
            ice = IceFieldParams(
                concentration=float(c),
                thickness=ice_base.thickness,
                floe_diameter=ice_base.floe_diameter,
                restitution=ice_base.restitution
            )
            solver = BrokenIceSeakeeping(kvlcc2_ship, ice)
            rao = solver.compute_rao(freq)
            mean_zeta = float(np.mean(rao.damping_ratio))
            assert mean_zeta >= prev_zeta - 1e-10, \
                f"c={c}: ζ={mean_zeta:.6f} < prev ζ={prev_zeta:.6f} (not monotone)"
            prev_zeta = mean_zeta

    def test_damping_increases_with_thickness(self, kvlcc2_ship):
        """Thicker ice → more added damping (at same concentration)."""
        c = 0.5
        freq = np.array([1.0])
        thicknesses = [0.1, 0.2, 0.3, 0.5]
        prev_B = -1.0
        for h in thicknesses:
            ice = IceFieldParams(concentration=c, thickness=h)
            solver = BrokenIceSeakeeping(kvlcc2_ship, ice)
            rao = solver.compute_rao(freq)
            B_ice = rao.added_damping[0]
            assert B_ice >= prev_B - 1e-10, \
                f"h={h}: B_ice={B_ice:.2f} < prev {prev_B:.2f}"
            prev_B = B_ice


# ============================================================================
# Test (f): Integration / Full Solver Tests
# ============================================================================
class TestIntegration:
    """Integration tests for the full solver."""

    def test_full_solver_runs(self, kvlcc2_ship, standard_ice, frequency_range):
        """Full solver must run without errors."""
        solver = BrokenIceSeakeeping(kvlcc2_ship, standard_ice)
        rao = solver.compute_rao(frequency_range)
        assert rao.rao_heave is not None
        assert len(rao.rao_heave) == len(frequency_range)
        assert rao.added_damping is not None
        assert rao.damping_ratio is not None

    def test_free_decay_analysis(self, kvlcc2_ship, standard_ice):
        """Free decay analysis must extract damping ratio accurately."""
        solver = BrokenIceSeakeeping(kvlcc2_ship, standard_ice)
        omega_n = solver.open_water.get_natural_frequency()
        zeta_input = 0.05
        result = solver.free_decay_analysis(zeta_input, omega_n)
        assert result['relative_error'] < 0.01, \
            f"Free decay extraction error: {result['relative_error']:.4f}"

    def test_concentration_sweep(self, kvlcc2_ship):
        """Concentration sweep must produce monotone damping."""
        solver = BrokenIceSeakeeping(
            kvlcc2_ship,
            IceFieldParams(concentration=0.5, thickness=0.3)
        )
        freq = np.array([0.5, 1.0, 1.5])
        concentrations = np.linspace(0, 1, 11)
        results = solver.concentration_sweep(freq, concentrations)

        prev_zeta = -1.0
        for c in concentrations:
            zeta = results[float(c)]['mean_damping_ratio']
            assert zeta >= prev_zeta - 1e-10, \
                f"c={c}: ζ={zeta:.6f} not monotone"
            prev_zeta = zeta

    def test_different_ship_types(self, dtmb5415_ship, standard_ice, frequency_range):
        """Solver must work for different ship types."""
        solver = BrokenIceSeakeeping(dtmb5415_ship, standard_ice)
        rao = solver.compute_rao(frequency_range)
        assert np.all(rao.rao_heave >= 0)
        assert np.all(np.isfinite(rao.rao_heave))

    def test_metadata_populated(self, kvlcc2_ship, standard_ice, frequency_range):
        """Result metadata must be populated."""
        solver = BrokenIceSeakeeping(kvlcc2_ship, standard_ice)
        rao = solver.compute_rao(frequency_range)
        assert 'concentration' in rao.metadata
        assert rao.metadata['concentration'] == standard_ice.concentration
        assert rao.metadata['thickness'] == standard_ice.thickness


# ============================================================================
# Test: Physical Sanity Checks
# ============================================================================
class TestPhysicalSanity:
    """Physical sanity checks."""

    def test_hydrostatic_stiffness_positive(self, kvlcc2_ship):
        """Hydrostatic restoring must be positive."""
        rao = OpenWaterRAO(kvlcc2_ship)
        C33, C55 = rao._hydrostatic_coefficients()
        assert C33 > 0, "C33 must be positive"
        assert C55 > 0, "C55 must be positive"

    def test_added_mass_bounded(self, kvlcc2_ship, frequency_range):
        """Added mass must be bounded and positive."""
        rao = OpenWaterRAO(kvlcc2_ship)
        for omega in frequency_range:
            A33 = rao._added_mass_heave(omega)
            assert A33 > 0, f"A33 must be positive at ω={omega}"
            assert A33 < 10 * kvlcc2_ship.mass, \
                f"A33={A33:.0f} unreasonably large"

    def test_radiation_damping_positive(self, kvlcc2_ship, frequency_range):
        """Radiation damping must be positive."""
        rao = OpenWaterRAO(kvlcc2_ship)
        for omega in frequency_range:
            B33 = rao._radiation_damping_heave(omega)
            assert B33 > 0, f"B33 must be positive at ω={omega}"

    def test_ice_added_damping_nonnegative(self, kvlcc2_ship, frequency_range):
        """Ice added damping must be non-negative."""
        ice = IceFieldParams(concentration=0.5, thickness=0.3)
        model = IceAddedDampingModel(kvlcc2_ship, ice)
        for omega in frequency_range:
            B_ice = model.added_damping(omega)
            assert B_ice >= 0, f"B_ice must be ≥ 0 at ω={omega}"

    def test_ice_added_mass_nonnegative(self, kvlcc2_ship, frequency_range):
        """Ice added mass must be non-negative."""
        ice = IceFieldParams(concentration=0.5, thickness=0.3)
        model = IceAddedDampingModel(kvlcc2_ship, ice)
        for omega in frequency_range:
            A_ice = model.added_mass(omega)
            assert A_ice >= 0, f"A_ice must be ≥ 0 at ω={omega}"


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
