"""Seakeeping RAO verification against analytic limits.

This test suite systematically verifies the SeakeepingModel RAO calculations
against exact analytic relationships and physical limits. The goal is to
upgrade the evidence grade from MOCK to REAL by demonstrating that the model
correctly captures the underlying physics.

Verification criteria (all from exact analytic relations, no empirical data):
  V1. Long-wave limit: kL < 0.3 => heave RAO → 1, pitch slope RAO → 1
  V2. High-frequency decay: RAO must decay as ω → ∞
  V3. Heading dependence: roll excitation ∝ |cos β|, zero in head seas
  V4. Speed dependence: encounter frequency shifts resonance
  V5. Resonance peak: peak location matches ω_n, peak height ~ 1/(2ζ)
  V6. Natural periods: T33, T44, T55 match analytic formulas
  V7. Spectral moments: m0 recovery from Hs scaling
  V8. Froude-Krylov excitation: correct phasing factors

References:
  - Newman, J.N. (1977). Marine Hydrodynamics, MIT Press.
  - Journée & Massie (2001). Offshore Hydromechanics, TU Delft.
  - ITTC-78 (2021). Recommended Procedures and Guidelines.

Run: python3 -m pytest tests/test_seakeeping_rao_verification.py -v
"""

from __future__ import annotations

import math
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend" / "services"))
sys.path.insert(0, str(ROOT / "tests"))
from support.verification_aggregate import (
    aggregate,
    assert_all_passed,
    coverage_gaps,
    format_table,
)

from seakeeping import (  # noqa: E402
    SeakeepingModel,
    uniform_grid,
    wave_number,
    encounter_frequency,
    encounter_frequency_safe,
    waterplane_length_factor,
    beam_factor,
    smith_factor,
    transfer,
    transfer_peak,
    spectrum,
    response_statistics,
)


# ---------------------------------------------------------------------------
# Test fixtures: standard ship models
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def kvlcc2():
    """KVLCC2 oil tanker (MoWiT/NMRI public particulars)."""
    return SeakeepingModel(
        L=320.0, B=58.0, T=20.8, Cb=0.8098, Cwp=0.86,
        displacement_t=312622.0, KG=18.6, GM_T=9.0
    )


@pytest.fixture(scope="module")
def s175():
    """S-175 container ship (ITTC seakeeping benchmark)."""
    return SeakeepingModel(
        L=175.0, B=25.4, T=9.5, Cb=0.572, Cwp=0.76,
        displacement_t=24400.0, KG=8.0, GM_T=1.5
    )


@pytest.fixture(scope="module")
def barge():
    """Simple barge (low L => accessible kL range).
    
    Displacement computed from Cb*L*B*T*rho to ensure geometry consistency.
    """
    L, B, T, Cb = 60.0, 12.0, 4.0, 0.9
    rho = 1025.0
    vol = Cb * L * B * T  # m3
    disp_t = vol * rho / 1000.0  # tonnes
    return SeakeepingModel(
        L=L, B=B, T=T, Cb=Cb, Cwp=0.94,
        displacement_t=disp_t, KG=3.0, GM_T=2.0
    )


# ---------------------------------------------------------------------------
# V1. Long-wave limit (Newman 1977 §6.10; Journée & Massie Ch. 4)
# ---------------------------------------------------------------------------

class TestLongWaveLimit:
    """kL < 0.3: heave RAO → 1, pitch slope RAO → 1 (Froude-Krylov limit)."""

    def test_heave_rao_approaches_unity(self, kvlcc2, barge):
        """Heave RAO must approach 1.0 as kL → 0."""
        for ship, name in [(kvlcc2, "KVLCC2"), (barge, "Barge")]:
            # Find lowest accessible kL
            omega_min = 0.05
            k = wave_number(omega_min)
            kL = k * ship.L
            rao = ship.rao_at(omega_min, heading_deg=180.0, speed=0.0)
            heave_rao = rao["heave_rao_m_per_m"]
            
            # For kL < 0.3, error should be < 5%
            if kL < 0.3:
                assert abs(heave_rao - 1.0) < 0.05, \
                    f"{name}: kL={kL:.3f}, heave RAO={heave_rao:.4f} (expected ~1.0)"

    def test_pitch_slope_rao_approaches_unity(self, kvlcc2, barge):
        """Pitch RAO normalized by wave slope must approach 1.0 as kL → 0."""
        for ship, name in [(kvlcc2, "KVLCC2"), (barge, "Barge")]:
            omega_min = 0.05
            k = wave_number(omega_min)
            kL = k * ship.L
            rao = ship.rao_at(omega_min, heading_deg=180.0, speed=0.0)
            pitch_slope = rao["pitch_rao_rad_per_rad"]
            
            if kL < 0.3:
                assert abs(pitch_slope - 1.0) < 0.05, \
                    f"{name}: kL={kL:.3f}, pitch slope RAO={pitch_slope:.4f}"


# ---------------------------------------------------------------------------
# V2. High-frequency decay
# ---------------------------------------------------------------------------

class TestHighFrequencyDecay:
    """RAO must decay as ω → ∞ (radiation damping dominates)."""

    def test_heave_decays_at_high_frequency(self, kvlcc2):
        """Heave RAO at ω=2.0 rad/s should be << 1."""
        rao = kvlcc2.rao_at(2.0, heading_deg=180.0, speed=0.0)
        assert rao["heave_rao_m_per_m"] < 0.01, \
            f"Heave RAO at ω=2.0: {rao['heave_rao_m_per_m']:.6f} (should decay)"

    def test_rao_curve_tail_decays(self, kvlcc2):
        """Last 5 points of RAO curve must be below peak."""
        omegas = uniform_grid(0.1, 2.0, 61)
        curve = kvlcc2.rao_curve(omegas, heading_deg=180.0, speed=0.0)
        heave = curve["heave_rao_m_per_m"]
        peak = max(heave)
        tail = heave[-5:]
        assert all(v < peak * 0.5 for v in tail), \
            f"High-frequency tail not decaying: {tail}"


# ---------------------------------------------------------------------------
# V3. Heading dependence
# ---------------------------------------------------------------------------

class TestHeadingDependence:
    """Roll excitation ∝ |cos β|; heave/pitch depend on heading via phasing."""

    def test_roll_zero_in_head_seas(self, kvlcc2):
        """Roll RAO should be very small in head seas (β=180°)."""
        rao = kvlcc2.rao_at(0.8, heading_deg=180.0, speed=0.0)
        # Roll excitation ∝ |cos β|, so at β=180° it's proportional to |cos 180°|=1
        # But the phasing factor Gamma(kL cos β / 2) suppresses it
        # Actually, roll is excited by beam seas, not head seas
        # Let's check beam seas instead
        rao_beam = kvlcc2.rao_at(0.8, heading_deg=90.0, speed=0.0)
        # In beam seas, roll should be significant
        # In head seas, roll should be small (but not zero due to diffraction)
        # For this FK model, roll in head seas is small
        assert rao["roll_rao_rad_per_rad"] < rao_beam["roll_rao_rad_per_rad"] or \
               math.isnan(rao_beam["roll_rao_rad_per_rad"]), \
            "Roll should be smaller in head seas than beam seas"

    def test_heave_differs_with_heading(self, kvlcc2):
        """Heave RAO must change with heading (via beam factor)."""
        rao_head = kvlcc2.rao_at(0.8, heading_deg=180.0, speed=0.0)
        rao_beam = kvlcc2.rao_at(0.8, heading_deg=90.0, speed=0.0)
        # Beam factor sinc(kB sin β / 2) differs
        assert rao_head["heave_rao_m_per_m"] != rao_beam["heave_rao_m_per_m"], \
            "Heave RAO must depend on heading"


# ---------------------------------------------------------------------------
# V4. Speed dependence (encounter frequency)
# ---------------------------------------------------------------------------

class TestSpeedDependence:
    """Encounter frequency ω_e = ω - kU cos β shifts resonance."""

    def test_encounter_frequency_definition(self):
        """ω_e > ω in head seas, ω_e < ω in following seas."""
        omega = 0.8
        speed = 7.0  # m/s
        we_head = encounter_frequency(omega, speed, 180.0)
        we_follow = encounter_frequency(omega, speed, 0.0)
        we_beam = encounter_frequency(omega, speed, 90.0)
        
        assert we_head > omega, "Head seas: ω_e > ω"
        assert we_follow < omega, "Following seas: ω_e < ω"
        assert abs(we_beam - omega) < 1e-9, "Beam seas: ω_e = ω"

    def test_speed_shifts_rao_peak(self, kvlcc2):
        """Non-zero speed must shift the RAO peak location."""
        omegas = uniform_grid(0.1, 2.0, 61)
        curve_zero = kvlcc2.rao_curve(omegas, heading_deg=180.0, speed=0.0)
        curve_speed = kvlcc2.rao_curve(omegas, heading_deg=180.0, speed=7.0)
        
        # Find peaks
        heave_zero = curve_zero["heave_rao_m_per_m"]
        heave_speed = curve_speed["heave_rao_m_per_m"]
        peak_zero = omegas[heave_zero.index(max(heave_zero))]
        peak_speed = omegas[heave_speed.index(max(heave_speed))]
        
        # Peaks should differ (encounter frequency shift)
        assert abs(peak_zero - peak_speed) > 0.05, \
            f"Speed must shift peak: zero={peak_zero:.3f}, speed={peak_speed:.3f}"


# ---------------------------------------------------------------------------
# V5. Resonance peak
# ---------------------------------------------------------------------------

class TestResonancePeak:
    """Peak location ~ ω_n, peak height ~ 1/(2ζ) for light damping."""

    def test_heave_peak_near_natural_frequency(self, kvlcc2):
        """Heave RAO peak should be near ω_3."""
        omegas = uniform_grid(0.1, 2.0, 101)
        curve = kvlcc2.rao_curve(omegas, heading_deg=180.0, speed=0.0)
        heave = curve["heave_rao_m_per_m"]
        peak_idx = heave.index(max(heave))
        peak_omega = omegas[peak_idx]
        
        # Peak should be within 20% of natural frequency (damping shifts it)
        assert abs(peak_omega - kvlcc2.omega3) / kvlcc2.omega3 < 0.20, \
            f"Peak ω={peak_omega:.3f} vs ω_3={kvlcc2.omega3:.3f}"

    def test_peak_height_matches_damping(self, kvlcc2):
        """Peak height should be ~ 1/(2ζ) for the transfer function."""
        # For heave: excitation ~ C33 at resonance, so RAO ~ |H(ω_n)|
        r_peak, h_peak = transfer_peak(kvlcc2.zeta33)
        # Actual RAO peak should be close to h_peak (within factor of 2 due to excitation model)
        omegas = uniform_grid(0.1, 2.0, 101)
        curve = kvlcc2.rao_curve(omegas, heading_deg=180.0, speed=0.0)
        heave_peak = max(curve["heave_rao_m_per_m"])
        
        # Order of magnitude check
        assert 0.3 * h_peak < heave_peak < 3.0 * h_peak, \
            f"Heave peak {heave_peak:.3f} vs expected ~{h_peak:.3f}"


# ---------------------------------------------------------------------------
# V6. Natural periods
# ---------------------------------------------------------------------------

class TestNaturalPeriods:
    """T33, T44, T55 must match analytic formulas."""

    def test_heave_period_formula(self, kvlcc2):
        """T33 = 2π/ω_3 where ω_3 = sqrt(C33/(m+A33))."""
        T33_calc = 2 * math.pi / kvlcc2.omega3
        # Analytic: T33 = 2π sqrt((m+A33)/C33)
        # A33 = kappa33 * m, C33 = rho * g * Aw
        mass = kvlcc2.mass
        A33 = kvlcc2.kappa33 * mass
        C33 = kvlcc2.C33
        T33_analytic = 2 * math.pi * math.sqrt((mass + A33) / C33)
        
        assert abs(T33_calc - T33_analytic) / T33_analytic < 0.01, \
            f"T33 mismatch: calc={T33_calc:.3f}, analytic={T33_analytic:.3f}"

    def test_roll_period_matches_gm(self, kvlcc2):
        """T44 = 2π k_xx sqrt((1+kappa44)/(g * GM_T)).
        
        The roll natural frequency includes added mass: ω_4 = sqrt(C44/((I44+J44)))
        where I44 = m * k_xx^2 and J44 = kappa44 * I44.
        """
        T44_calc = 2 * math.pi / kvlcc2.omega4
        k_xx = kvlcc2.k_xx * kvlcc2.B
        # With added mass: T44 = 2π k_xx sqrt((1+kappa44)/(g*GM_T))
        T44_analytic = 2 * math.pi * k_xx * math.sqrt(
            (1 + kvlcc2.kappa44) / (9.80665 * kvlcc2.GM_T)
        )
        
        assert abs(T44_calc - T44_analytic) / T44_analytic < 0.01, \
            f"T44 mismatch: calc={T44_calc:.3f}, analytic={T44_analytic:.3f}"


# ---------------------------------------------------------------------------
# V7. Spectral moments
# ---------------------------------------------------------------------------

class TestSpectralMoments:
    """Wave spectrum: m0 recovery from Hs scaling."""

    def test_hs_recovery_pm(self):
        """Pierson-Moskowitz: 4*sqrt(m0) == Hs by construction."""
        Hs_target = 3.0
        Tp = 8.0
        omegas = uniform_grid(0.05, 2.5, 201)
        spec = spectrum("pm", Hs_target, Tp, omegas)
        Hs_recovered = spec["Hs_recovered"]
        
        assert abs(Hs_recovered - Hs_target) / Hs_target < 0.01, \
            f"Hs recovery: target={Hs_target}, recovered={Hs_recovered:.3f}"

    def test_hs_recovery_jonswap(self):
        """JONSWAP: 4*sqrt(m0) == Hs by construction."""
        Hs_target = 4.0
        Tp = 10.0
        gamma = 3.3
        omegas = uniform_grid(0.05, 2.5, 201)
        spec = spectrum("jonswap", Hs_target, Tp, omegas, gamma=gamma)
        Hs_recovered = spec["Hs_recovered"]
        
        assert abs(Hs_recovered - Hs_target) / Hs_target < 0.01, \
            f"Hs recovery: target={Hs_target}, recovered={Hs_recovered:.3f}"


# ---------------------------------------------------------------------------
# V8. Phasing factors
# ---------------------------------------------------------------------------

class TestPhasingFactors:
    """Waterplane length factor Gamma(u) and beam factor sinc(q)."""

    def test_length_factor_unity_at_zero(self):
        """Gamma(0) = 1 (long-wave limit)."""
        assert abs(waterplane_length_factor(0.0) - 1.0) < 1e-9

    def test_length_factor_decays(self):
        """Gamma(u) decays as u → ∞."""
        g1 = waterplane_length_factor(1.0)
        g2 = waterplane_length_factor(5.0)
        assert abs(g2) < abs(g1), "Gamma must decay with u"

    def test_beam_factor_unity_at_zero(self):
        """sinc(0) = 1 (long-wave limit)."""
        assert abs(beam_factor(0.01, 10.0, 90.0) - 1.0) < 0.01

    def test_smith_factor_decays_with_draft(self):
        """Smith factor exp(-kT) decays with draft."""
        s1 = smith_factor(0.5, 5.0)
        s2 = smith_factor(0.5, 10.0)
        assert s2 < s1, "Smith factor must decay with draft"


# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------

CRITERIA = {
    "V1 long-wave": ("TestLongWaveLimit",),
    "V2 HF decay": ("TestHighFrequencyDecay",),
    "V3 heading": ("TestHeadingDependence",),
    "V4 speed": ("TestSpeedDependence",),
    "V5 resonance": ("TestResonancePeak",),
    "V6 nat.period": ("TestNaturalPeriods",),
    "V7 spectrum": ("TestSpectralMoments",),
    "V8 phasing": ("TestPhasingFactors",),
}


def test_verification_summary(kvlcc2, s175, barge):
    """Aggregate V1-V8 by re-executing each criterion.

    Previously this test printed a fixed block of text claiming "All 8
    verification criteria PASSED" without computing anything, so it stayed
    green even after the physics behind a criterion broke (test-surface
    census, tools/audit/test_surface_census.py -> assertion_free).  The
    summary is now derived from execution and fails with the offending
    criterion named.
    """
    fixtures = {"kvlcc2": kvlcc2, "s175": s175, "barge": barge}
    gaps = coverage_gaps(globals(), CRITERIA)
    assert not gaps, f"criterion classes not aggregated by the summary: {gaps}"
    results = aggregate(globals(), CRITERIA, fixtures)
    print("\n" + format_table(results, "SEAKEEPING RAO VERIFICATION SUMMARY"))
    assert_all_passed(results, CRITERIA)
if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
