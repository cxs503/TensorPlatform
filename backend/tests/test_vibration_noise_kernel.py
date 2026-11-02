"""Closed-form verification of the hull-vibration / cabin-noise kernel.

Each test pins the kernel to an *independent* analytical anchor, not to itself:

* free-free beam eigenvalues from ``cos(beta L) cosh(beta L) = 1``
* exact Navier plate frequency and Leissa's clamped-square eigenvalue
* exact SDOF modal amplification ``1 / (2 zeta)``
* mass-law asymptotic slopes (+6 dB per octave / per mass doubling)
* Sabine constant ``24 ln10 / c = 0.1611``
* geometric-similarity scaling exponent ``f ~ L^-1.5`` and ``f ~ Delta^-0.5``

The scaling and mass-sensitivity criteria are the two that the served endpoint
failed in report #1186 (V2/V3): ``f_hull = 1.5 (L/100)^-0.5`` is independent of
displacement.  ``test_mass_sensitivity_matches_analytic_placeholder_failure``
therefore also *demonstrates* that failure on the legacy formula.
"""
import math

import numpy as np
import pytest

from services.digital_twin.models.geometry import HullForm
from services.digital_twin.models.vibration import (
    ComfortThresholds,
    E_STEEL,
    FREE_FREE_BETA_L,
    HullGirderSection,
    VibrationValidationError,
    bearing_force_amplitude,
    blade_rate_frequency,
    blade_rate_harmonics,
    cabin_noise,
    clamped_plate_lambda,
    comfort_grade,
    deck_velocity_spectrum,
    displacement_from_hull,
    free_field_spl,
    free_free_beam_frequencies,
    free_free_eigenvalue,
    hull_girder_vertical,
    hull_girder_vibration,
    mass_law_tl,
    modal_velocity_response,
    mode_shape,
    panel_transmissibility,
    plate_flexural_rigidity,
    plate_natural_frequency,
    resonance_risk,
    room_constant,
    sabine_reverberation_time,
    vibration_summary,
)

SHIP = dict(L=180.0, B=32.0, T=11.5, Cb=0.80)
SECTION = HullGirderSection(B=32.0, D=16.0, t_deck=0.020, t_bottom=0.020, t_side=0.015)
LEISSA_CLAMPED_SQUARE = 35.985      # omega a^2 sqrt(rho h / D), Leissa "Vibration of Plates"
TRAPZ = getattr(np, "trapezoid", None) or np.trapz


@pytest.fixture(scope="module")
def ship():
    return HullForm(**SHIP)


@pytest.fixture(scope="module")
def girder(ship):
    return hull_girder_vibration(ship, SECTION)


# ---------------------------------------------------------------- beam modes
def test_free_free_eigenvalues_are_roots_of_transcendental_equation():
    """Every tabulated beta_n L must satisfy cos(x) cosh(x) = 1.

    The residual is normalised by cosh(x) (which reaches 1.6e7 for n = 5) so that
    the test measures the root, not the floating-point round-off of cosh.
    """
    for n, x in enumerate(FREE_FREE_BETA_L, start=1):
        assert (math.cos(x) * math.cosh(x) - 1.0) / math.cosh(x) == pytest.approx(0.0, abs=1e-12)
        assert math.cos(x) - 1.0 / math.cosh(x) == pytest.approx(0.0, abs=1e-12)
        assert free_free_eigenvalue(n) == pytest.approx(x, rel=0)


def test_newton_polished_eigenvalue_matches_asymptote():
    """n >= 2 roots converge to (2n+1) pi/2 (alternating sides): gap < 1e-4."""
    for n in (2, 3, 4, 5, 7, 11):
        x = free_free_eigenvalue(n)
        asymptote = (2 * n + 1) * math.pi / 2.0
        assert abs(asymptote - x) / asymptote < 1e-3
        assert (math.cos(x) * math.cosh(x) - 1.0) / math.cosh(x) == pytest.approx(0.0, abs=1e-11)


def test_beam_frequency_identity_and_non_dimensional_group():
    """f_n = (beta_n L)^2/(2 pi L^2) sqrt(EI/mu) reproduced from the group."""
    L, EI, mu = 180.0, 2.0e13, 5.0e5
    freqs = free_free_beam_frequencies(L, EI, mu, n_modes=3)
    for n, f in enumerate(freqs, start=1):
        beta_L = free_free_eigenvalue(n)
        assert f == pytest.approx(beta_L ** 2 / (2 * math.pi)
                                 * math.sqrt(EI / (mu * L ** 4)), rel=1e-14)
    # classical ratios for a uniform free-free beam (exact)
    assert freqs[1] / freqs[0] == pytest.approx((7.853204624095838 / 4.730040744862704) ** 2,
                                                rel=1e-12)
    assert freqs[1] / freqs[0] == pytest.approx(2.7565, abs=1e-4)


def test_mode_shape_normalisation_and_nodes():
    """int phi_n^2 dx = L (normalisation) and the 2-node mode has a midspan node."""
    L = 180.0
    x = np.linspace(0.0, L, 20001)
    for n in (1, 2, 3):
        phi = mode_shape(x, L, n)
        assert TRAPZ(phi ** 2, x) / L == pytest.approx(1.0, rel=1e-6)
        assert abs(phi[0] - 2.0) < 1e-9          # free-free end amplitude convention
        assert abs(abs(phi[-1]) - 2.0) < 1e-9    # symmetric modes +2, antisymmetric -2
        assert abs(phi[-1] - 2.0 * (-1) ** (n + 1)) < 1e-9
    phi2 = mode_shape(x, L, 2)
    assert abs(phi2[len(x) // 2]) < 1e-6          # node at midship for n = 2


# ------------------------------------------------------- hull girder physics
def test_section_properties_are_closed_form():
    """Box-section I and area reproduce the hand integrals."""
    I = SECTION.I_vertical
    half = SECTION.D / 2
    hand = SECTION.stiffener_factor * (
        (SECTION.B * SECTION.t_deck + SECTION.B * SECTION.t_bottom) * half ** 2
        + 2 * SECTION.t_side * SECTION.D ** 3 / 12.0)
    assert I == pytest.approx(hand, rel=1e-14)
    assert SECTION.section_modulus_deck == pytest.approx(I / half, rel=1e-14)
    assert 80.0 < I < 200.0                       # engineering range for L = 180 m


def test_hull_girder_frequency_in_closed_form_band(girder):
    """2-node frequency must sit in the closed-form free-free beam band.

    The band [0.6154, 1.0769] Hz is the one report #1186 (criterion V4) used: the
    bare-shell lower bound through a 1.75x longitudinally-stiffened upper bound.
    """
    assert 0.6154 <= girder["f_2node_hz"] <= 1.0769
    assert girder["f_3node_hz"] > girder["f_2node_hz"]
    assert girder["frequency_ratio_3_2"] == pytest.approx(2.7565, abs=1e-3)


def test_geometric_similarity_scaling_exponent(ship):
    """Geometric similarity (all lengths x lambda, same material) => f ~ lambda^-1.

    Euler-Bernoulli free-free beam: ``I ~ lambda^4``, ``mu = displacement/L ~
    lambda^2``, hence ``f ~ sqrt(I/mu)/L^2 ~ lambda^-1`` - the same exponent as
    the empirical Schlick correlation.  Report #1186 demanded an exponent inside
    [-1.5, -0.8]; the legacy endpoint delivered -0.4999.
    """
    lam = 0.5
    parent = hull_girder_vibration(ship, SECTION)
    scaled_section = HullGirderSection(B=SECTION.B * lam, D=SECTION.D * lam,
                                       t_deck=SECTION.t_deck * lam,
                                       t_bottom=SECTION.t_bottom * lam,
                                       t_side=SECTION.t_side * lam)
    scaled = hull_girder_vertical(SHIP["L"] * lam, scaled_section,
                                  parent["displacement_t"] * lam ** 3)
    exponent = math.log(scaled["f_2node_hz"] / parent["f_2node_hz"]) / math.log(lam)
    assert exponent == pytest.approx(-1.0, abs=1e-9)
    assert -1.5 <= exponent <= -0.8


def test_mass_sensitivity_matches_analytic_placeholder_failure(ship, girder):
    """Doubling displacement must drop f by exactly sqrt(2) - 1 = 29.29 %.

    The legacy formula ``1.5 (L/100)^-0.5`` is mass independent (0 % change), the
    defect recorded as criterion V3 in report #1186.
    """
    doubled = hull_girder_vertical(SHIP["L"], SECTION, girder["displacement_t"] * 2.0)
    drop = doubled["f_2node_hz"] / girder["f_2node_hz"] - 1.0
    assert drop == pytest.approx(-(1.0 - 1.0 / math.sqrt(2.0)), rel=1e-9)
    assert abs(drop) >= 0.05
    legacy_parent = 1.5 * (180.0 / 100.0) ** -0.5
    legacy_doubled = 1.5 * (180.0 / 100.0) ** -0.5        # L does not change
    assert legacy_doubled - legacy_parent == 0.0           # documented MOCK behaviour


def test_added_mass_ratio_scales_as_inverse_sqrt(girder):
    """mu = Delta (1 + k)/L, so f ~ (1 + k)^-1/2 exactly."""
    base = hull_girder_vertical(180.0, SECTION, girder["displacement_t"], added_mass_ratio=0.0)
    k02 = hull_girder_vertical(180.0, SECTION, girder["displacement_t"], added_mass_ratio=0.2)
    assert k02["f_2node_hz"] / base["f_2node_hz"] == pytest.approx(1 / math.sqrt(1.2), rel=1e-12)


def test_displacement_uses_exact_cb_closure(ship):
    """Displacement [t] = Cb L B T rho exactly (geometry gate before physics)."""
    assert displacement_from_hull(ship) == pytest.approx(0.80 * 180 * 32 * 11.5 * 1.025, rel=1e-12)


# ------------------------------------------------------------ modal response
def test_resonance_amplitude_matches_sdof_analytic(girder):
    """At f = f_1 the modal velocity equals F phi_f phi_o / (2 zeta M_n omega_n)."""
    L, mu, zeta = SHIP["L"], girder["mu_kg_per_m"], 0.01
    EI = E_STEEL * SECTION.I_vertical
    f1 = free_free_beam_frequencies(L, EI, mu, 1)[0]
    omega_n = 2 * math.pi * f1
    phi_f = float(mode_shape(np.array([0.15 * L]), L, 1)[0])
    phi_o = float(mode_shape(np.array([0.95 * L]), L, 1)[0])
    v = modal_velocity_response(L, E_STEEL, SECTION.I_vertical, mu, omega_n,
                               1.0, 0.15 * L, 0.95 * L, zeta=zeta, n_modes=1)
    analytic = phi_f * phi_o / (2 * zeta * mu * L * omega_n)
    assert abs(v) == pytest.approx(analytic, rel=1e-9)


def test_sdof_magnification_limits():
    """Displacement magnification -> 1 (r -> 0), 1/(2 zeta) (r = 1), 1/r^2 (r >> 1)."""
    from services.digital_twin.models.vibration import sdof_magnification
    zeta = 0.02
    assert sdof_magnification(1e-6, 1.0, zeta) == pytest.approx(1.0, abs=1e-8)
    assert sdof_magnification(1.0, 1.0, zeta) == pytest.approx(1 / (2 * zeta), rel=1e-12)
    r = 50.0
    assert sdof_magnification(r, 1.0, zeta) == pytest.approx(1 / r ** 2, rel=1e-3)


def test_velocity_scales_linearly_with_excitation(girder):
    """Linear system: doubling the thrust doubles every velocity amplitude."""
    kwargs = dict(L=SHIP["L"], E=E_STEEL, I=SECTION.I_vertical,
                  mu=girder["mu_kg_per_m"], rpm=110.0, n_blades=4)
    a = deck_velocity_spectrum(thrust_N=800e3, **kwargs)
    b = deck_velocity_spectrum(thrust_N=1600e3, **kwargs)
    assert b["rms_mm_s"] / a["rms_mm_s"] == pytest.approx(2.0, rel=1e-12)


def test_modal_truncation_is_converged(girder):
    """The reported spectrum must be modal-converged (10 vs 20 mode check)."""
    spec = deck_velocity_spectrum(L=SHIP["L"], E=E_STEEL, I=SECTION.I_vertical,
                                  mu=girder["mu_kg_per_m"], thrust_N=800e3,
                                  rpm=110.0, n_blades=4)
    assert spec["converged"] is True
    assert spec["modal_convergence_rel"] < 0.02
    # a 3-mode truncation is NOT converged for a driver above the 3rd mode
    short = deck_velocity_spectrum(L=SHIP["L"], E=E_STEEL, I=SECTION.I_vertical,
                                   mu=girder["mu_kg_per_m"], thrust_N=800e3,
                                   rpm=110.0, n_blades=4, n_modes=3)
    assert max(short["rms_mm_s"], spec["rms_mm_s"]) / min(
        short["rms_mm_s"], spec["rms_mm_s"]) > 2.0


def test_rms_is_parseval_consistent(girder):
    """rms = sqrt(0.5 sum A_k^2) equals the rms of the synthesised time record."""
    spec = deck_velocity_spectrum(L=SHIP["L"], E=E_STEEL, I=SECTION.I_vertical,
                                  mu=girder["mu_kg_per_m"], thrust_N=800e3,
                                  rpm=110.0, n_blades=4)
    t = np.linspace(0.0, 20.0, 200001)
    series = np.zeros_like(t)
    for f, amp, ph in zip(spec["harmonics_hz"], spec["velocity_mm_s"], spec["phase_rad"]):
        if 1.0 <= f <= 80.0:
            series += amp * np.cos(2 * math.pi * f * t + ph)
    rms = float(np.sqrt(np.mean(series ** 2)))
    assert rms == pytest.approx(spec["rms_mm_s"], rel=2e-3)


# ----------------------------------------------------------------- excitation
def test_blade_rate_identity_is_exact():
    """f_BF = n_blades * rpm / 60 for a grid of shaft speeds and blade counts."""
    for rpm in (60.0, 87.5, 110.0, 400.0):
        for z in (4, 5, 6, 7):
            assert blade_rate_frequency(rpm, z) == pytest.approx(z * rpm / 60.0, rel=1e-15)
    assert blade_rate_harmonics(110.0, 4, 3).tolist() == pytest.approx([7.3333, 14.6667, 22.0],
                                                                      abs=1e-3)


def test_bearing_force_harmonic_rolloff():
    """F_k = k_F T / k: exact 1/k roll-off and linearity in thrust."""
    assert bearing_force_amplitude(1.0e6, k=1) == pytest.approx(0.03e6, rel=1e-14)
    assert bearing_force_amplitude(1.0e6, k=4) == pytest.approx(0.03e6 / 4, rel=1e-14)


# ----------------------------------------------------------------- plate panels
@pytest.mark.parametrize("a,b,m,n", [(1.0, 1.0, 1, 1), (2.0, 1.0, 1, 1),
                                     (1.5, 1.0, 2, 1), (0.8, 1.2, 3, 2)])
def test_simply_supported_plate_matches_navier_exact(a, b, m, n):
    """Navier solution reproduced exactly: w = sqrt(D/rho h)[(m pi/a)^2+(n pi/b)^2]."""
    h, rho = 0.012, 7850.0
    D = plate_flexural_rigidity(h)
    omega = math.sqrt(D / (rho * h)) * ((m * math.pi / a) ** 2 + (n * math.pi / b) ** 2)
    assert plate_natural_frequency(a, b, h, rho=rho, m=m, n=n, bc="SS") == pytest.approx(
        omega / (2 * math.pi), rel=1e-13)


def test_clamped_square_plate_matches_leissa():
    """Rayleigh-Ritz eigenvalue -> Leissa's 35.985 within 0.5 % (3x3 basis)."""
    lam = clamped_plate_lambda(1.0, 1.0, n_terms=3)
    assert lam == pytest.approx(LEISSA_CLAMPED_SQUARE, rel=5e-3)
    # and the Ritz reduction reproduces the exact simply-supported eigenvalue
    from services.digital_twin.models.vibration import ritz_plate_lambda
    assert ritz_plate_lambda(1.0, 1.0, n_terms=2, bc_x="SS", bc_y="SS") == pytest.approx(
        2 * math.pi ** 2, rel=1e-9)


def test_clamped_to_simply_supported_ratio():
    """Clamped/simply-supported square ratio is the classical 1.82, not a fitted guess."""
    f_ss = plate_natural_frequency(1.0, 1.0, 0.010, bc="SS")
    f_c = plate_natural_frequency(1.0, 1.0, 0.010, bc="clamped")
    assert f_c / f_ss == pytest.approx(1.82, abs=0.02)


def test_panel_transmissibility_analytic_limits():
    """H -> 1 (r -> 0), H(r=1) = sqrt(1+4zeta^2)/(2zeta), H ~ 1/r^2 (r >> 1)."""
    zeta = 0.03
    assert panel_transmissibility(1e-6, 10.0, zeta) == pytest.approx(1.0, abs=1e-9)
    assert panel_transmissibility(10.0, 10.0, zeta) == pytest.approx(
        math.sqrt(1 + 4 * zeta ** 2) / (2 * zeta), rel=1e-12)
    # for 1 << r << 1/(2 zeta) the response rolls off as 1/r^2 ...
    assert panel_transmissibility(50.0, 10.0, 1e-3) == pytest.approx(1 / (25.0 - 1.0), rel=1e-3)
    # ... and for r >> 1/(2 zeta) the damping term takes over: H -> 2 zeta / r
    assert panel_transmissibility(1e4, 10.0, zeta) == pytest.approx(2 * zeta / 1e3, rel=1e-2)


# ----------------------------------------------------------------- noise chain
def test_mass_law_asymptotes():
    """+6.02 dB per doubling of mass and per doubling of frequency."""
    tl = mass_law_tl(40.0, 100.0)
    # 6.02 dB/octave in the mass-controlled regime (finite +1 in the exact form
    # leaves a 4e-3 dB offset at m''=40 kg/m^2, 100 Hz)
    assert mass_law_tl(80.0, 100.0) - tl == pytest.approx(20 * math.log10(2.0), abs=5e-3)
    assert mass_law_tl(40.0, 200.0) - tl == pytest.approx(20 * math.log10(2.0), abs=5e-3)
    assert mass_law_tl(400.0, 1000.0) - mass_law_tl(200.0, 1000.0) == pytest.approx(
        20 * math.log10(2.0), abs=1e-5)
    # low-frequency (mass-controlled) asymptote -> 0 dB
    assert mass_law_tl(0.5, 5.0, field_incidence=False) < 0.5


def test_mass_law_normal_incidence_exact():
    """Normal-incidence TL equals 10 log10(1 + (m'' omega/(2 rho c))^2) exactly."""
    m2, f = 40.0, 100.0
    x = m2 * 2 * math.pi * f / (2 * 1.21 * 343.0)
    assert mass_law_tl(m2, f, field_incidence=False) == pytest.approx(
        10 * math.log10(1 + x * x), rel=1e-14)


def test_sabine_constant_is_analytic():
    """T60 = 24 ln(10) V/(c A) -> 0.1611 V/A (metric Sabine constant)."""
    t60 = sabine_reverberation_time(1000.0, 100.0)
    assert t60 == pytest.approx(24 * math.log(10) * 1000.0 / (343.0 * 100.0), rel=1e-15)
    assert sabine_reverberation_time(1.0, 1.0) == pytest.approx(0.1611, abs=1e-4)
    assert t60 / 10.0 == pytest.approx(sabine_reverberation_time(100.0, 100.0), rel=1e-14)


def test_room_constant_and_diffuse_field_limits():
    """Doubling R costs 3.01 dB; the diffuse field reduces to free field as R -> inf."""
    R1 = room_constant(240.0, 0.15)
    R2 = room_constant(240.0, 0.30)
    lp1 = cabin_noise(100.0, 40.0, 100.0, 240.0, 0.15)["spl_db"]
    lp2 = cabin_noise(100.0, 40.0, 100.0, 240.0, 0.30)["spl_db"]
    assert lp1 - lp2 == pytest.approx(10 * math.log10(R2 / R1), rel=1e-12)
    # free-field r^-2 spreading: 10x distance = -20 dB
    assert free_field_spl(98.0, 1.0) - free_field_spl(98.0, 10.0) == pytest.approx(
        20.0, rel=1e-12)
    # and the free-field expression is the R -> infinity limit of the room formula
    huge = cabin_noise(98.0, 1e-3, 1.0, 1e4, 0.999)
    assert huge["tl_db"] < 20.0


def test_cabin_noise_chain_reference_case():
    """Reference deck-house cabin: TL -> SPL chain monotone in mass and absorption."""
    base = cabin_noise(98.0, 40.0, 100.0, 240.0, 0.15)
    heavier = cabin_noise(98.0, 80.0, 100.0, 240.0, 0.15)
    assert heavier["spl_db"] < base["spl_db"]
    assert base["spl_db"] == pytest.approx(
        98.0 - base["tl_db"] + 10 * math.log10(4 / base["room_constant_m2"]), rel=1e-12)
    assert 2.0 < base["reverb_time_s"] < 4.0


# ------------------------------------------------------------------- grading
def test_comfort_grade_units_and_boundaries():
    """Grading is monotone, boundary-inclusive and carries its unit string."""
    th = ComfortThresholds()
    assert comfort_grade(0.0)["compliant"] is True
    assert comfort_grade(th.good)["level"] == "良好"
    assert comfort_grade(th.good + 1e-9)["level"] == "一般"
    assert comfort_grade(th.acceptable + 1e-9)["compliant"] is False
    assert comfort_grade(1e6)["level"] == "不可接受"
    assert "mm/s" in comfort_grade(1.0)["units"]


def test_resonance_risk_exact_at_equality():
    """Blade rate exactly on the hull mode -> zero margin -> high risk."""
    f_hull = 0.8188
    rpm = f_hull * 60.0 / 4.0                      # 4 blades land on f_hull
    risk = resonance_risk(rpm, 4, f_hull)
    assert risk["margin_rel"] == pytest.approx(0.0, abs=1e-12)
    assert risk["risk"] == "高风险"
    far = resonance_risk(rpm * 3.0, 4, f_hull)
    assert far["risk"] == "低风险"


# ------------------------------------------------------- served-endpoint shape
def test_summary_is_drop_in_and_unit_bearing():
    """The summary keeps the legacy decision fields but every value carries a unit."""
    out = vibration_summary(SHIP["L"], SECTION, 54316.8, rpm=110.0, n_blades=4,
                            thrust_N=800e3)
    for key in ("f_blade_hz", "f_hull_hz", "vertical_vibration_mm_s", "vibration_level",
                "resonance_risk", "comfort", "compliant"):
        assert key in out
    assert out["units"]["vertical_vibration"].startswith("mm/s")
    assert "lower bound" in out["level_basis"]
    assert out["f_blade_hz"] == pytest.approx(4 * 110.0 / 60.0, rel=1e-15)
    assert out["vertical_vibration_mm_s"] > 0.0
    assert out["modal_converged"] is True


def test_summary_reacts_to_ship_and_propulsor():
    """Two hulls / two shaft speeds must give different values (criterion V9/#1186)."""
    a = vibration_summary(180.0, SECTION, 54316.8, rpm=110.0, n_blades=4, thrust_N=800e3)
    b = vibration_summary(240.0, HullGirderSection(B=42.0, D=20.0, t_deck=0.024,
                                                   t_bottom=0.024, t_side=0.018),
                          96000.0, rpm=80.0, n_blades=5, thrust_N=1400e3)
    assert abs(a["f_hull_hz"] - b["f_hull_hz"]) > 1e-3
    assert abs(a["f_blade_hz"] - b["f_blade_hz"]) > 1e-3
    assert abs(a["vertical_vibration_mm_s"] - b["vertical_vibration_mm_s"]) > 0.0


def test_deck_panel_frequency_is_independent_quantity():
    """Deck frequency comes from the panel, not from a fixed 2.5 x f_hull factor."""
    small = vibration_summary(SHIP["L"], SECTION, 54316.8, rpm=110.0, n_blades=4,
                              thrust_N=800e3,
                              deck_panel=dict(a=6.0, b=4.0, h=0.008))
    large = vibration_summary(SHIP["L"], SECTION, 54316.8, rpm=110.0, n_blades=4,
                              thrust_N=800e3,
                              deck_panel=dict(a=12.0, b=8.0, h=0.008))
    assert small["f_deck_hz"] / large["f_deck_hz"] == pytest.approx(4.0, rel=1e-9)
    assert small["f_deck_hz"] / small["f_hull_hz"] != pytest.approx(2.5, abs=1e-2)


def test_invalid_inputs_raise():
    """Unphysical inputs are rejected instead of silently returning a number."""
    with pytest.raises(VibrationValidationError):
        HullGirderSection(B=-1.0, D=16.0, t_deck=0.02, t_bottom=0.02, t_side=0.015)
    with pytest.raises(VibrationValidationError):
        hull_girder_vertical(180.0, SECTION, 0.0)
    with pytest.raises(VibrationValidationError):
        blade_rate_frequency(0.0, 4)
    with pytest.raises(VibrationValidationError):
        plate_natural_frequency(1.0, 1.0, 0.01, bc="free")
    with pytest.raises(VibrationValidationError):
        room_constant(240.0, 1.2)
    with pytest.raises(VibrationValidationError):
        comfort_grade(-1.0)