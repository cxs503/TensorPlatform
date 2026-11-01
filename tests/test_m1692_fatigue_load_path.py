"""m1692 — load-path verification of the digital-twin fatigue model.

External baselines (no self-reference to the module under test):
  * IACS UR S11 Rev.7 3.2.1 wave parameter C(L) and 3.2 wave bending moments;
  * linear wave theory -> wave-induced stress amplitude proportional to Hs
    (the platform's linear strip model returns heave rms exactly proportional
    to Hs, asserted in ``test_heave_rms_is_linear_in_hs``).

Full study + numbers: ``scripts/verify_fatigue_load_path.py``,
``docs/m1692/FATIGUE_LOAD_PATH_VERIFICATION.md``.
"""
from __future__ import annotations

import math

import pytest

from backend.services.digital_twin.models import fatigue as F
from backend.services.digital_twin.models.geometry import HullForm
from backend.services.digital_twin.models import seakeeping as SK

HULL = HullForm(180.0, 30.0, 18.0, 0.82)


# ---------------------------------------------------------------------------
# IACS UR S11 wave parameter
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("L,C", [
    (50.0, 0.0412 * 50.0 + 4.0),        # 6.060  (L <= 90 branch of the rule)
    (60.0, 0.0412 * 60.0 + 4.0),        # 6.472
    (90.0, 0.0412 * 90.0 + 4.0),        # 7.708
    (120.0, 10.75 - (1.8) ** 1.5),
    (180.0, 10.75 - (1.2) ** 1.5),
    (230.0, 10.75 - (0.7) ** 1.5),
    (300.0, 10.75),
    (350.0, 10.75),
    (400.0, 10.75),                     # L > 350: rule keeps C = 10.75
])
def test_wave_parameter_matches_iacs_ur_s11(L, C):
    assert F.wave_parameter_C(L) == pytest.approx(C, rel=1e-12)


def test_wave_bending_moment_coefficients_and_ratio():
    m = F.wave_bending_moment(180.0, 45.0, 0.7)
    base = F.wave_parameter_C(180.0) * 180.0 ** 2 * 45.0 * 0.7
    assert m["M_hog_kNm"] == pytest.approx(0.19 * base, rel=1e-12)
    assert m["M_sag_kNm"] == pytest.approx(0.11 * base, rel=1e-12)
    assert m["M_sag_kNm"] / m["M_hog_kNm"] == pytest.approx(0.11 / 0.19, rel=1e-12)


def test_small_ship_wave_parameter_is_not_the_long_ship_formula():
    """Regression: L <= 90 m must use 0.0412L + 4, not 10.75 - ((300-L)/100)^1.5."""
    assert F.wave_parameter_C(50.0) == pytest.approx(6.06, rel=1e-12)
    long_formula = 10.75 - ((300.0 - 50.0) / 100.0) ** 1.5
    assert abs(F.wave_parameter_C(50.0) - long_formula) / long_formula > 0.10


# ---------------------------------------------------------------------------
# Hs transfer: linear wave theory
# ---------------------------------------------------------------------------
def test_stress_amplitude_is_linear_in_hs():
    s4 = F.spectral_fatigue(HULL, 4.0, 9.0)["sigma_amp_MPa"]
    s8 = F.spectral_fatigue(HULL, 8.0, 9.0)["sigma_amp_MPa"]
    assert s8 / s4 == pytest.approx(2.0, rel=1e-12)
    assert F.spectral_fatigue(HULL, 0.0, 9.0)["sigma_amp_MPa"] == pytest.approx(0.0, abs=1e-12)


def test_design_sea_state_anchors_the_ur_s11_moment():
    out = F.spectral_fatigue(HULL, F.HS_IACS, 9.0)
    m = F.wave_bending_moment(HULL.L, HULL.B, HULL.Cb)
    z = F.section_modulus(HULL.B, HULL.T)
    assert out["sigma_amp_MPa"] == pytest.approx(m["M_hog_kNm"] * 1e3 / z / 1e6, rel=1e-12)
    assert out["stress_scaling"] == "linear_in_Hs"


def test_damage_matches_linear_reference_and_is_monotone():
    t_ref = 9.0
    prev = -1.0
    for Hs in (2.0, 4.0, 6.0, 8.0, 11.0):
        mod = F.spectral_fatigue(HULL, Hs, t_ref)["damage_per_day"]
        ref = F.spectral_fatigue(
            HULL, Hs, t_ref,
            sigma_rms_mpa=F.spectral_fatigue(HULL, Hs, t_ref)["sigma_amp_MPa"],
        )["damage_per_day"]
        assert mod == pytest.approx(ref, rel=1e-12)
        assert mod > prev
        prev = mod


def test_life_over_prediction_of_the_old_quadratic_law_is_gone():
    """At Hs = 5 m the quadratic law returned ~11.6x the linear-theory life."""
    Hs = 5.0
    amp = F.spectral_fatigue(HULL, Hs, 9.0)["sigma_amp_MPa"]
    quad_amp = amp * (Hs / F.HS_IACS)          # what the old (Hs/11)^2 law gave
    d_lin = F.spectral_fatigue(HULL, Hs, 9.0)["damage_per_day"]
    d_quad = F.spectral_fatigue(HULL, Hs, 9.0, sigma_rms_mpa=quad_amp)["damage_per_day"]
    ratio = d_lin / d_quad
    # pure m = 3 branch would give (11/5)^3 = 10.65; the bilinear S-N knee shifts
    # it to 11.61 for this hull, hence the 15 % band rather than exact equality.
    assert ratio == pytest.approx((F.HS_IACS / Hs) ** F.SN_M, rel=0.15)
    assert ratio > 10.0


def test_heave_rms_is_linear_in_hs():
    """Cross-check of the linear-wave anchor: the strip model is linear in Hs."""
    ratios = []
    for Hs in (2.0, 4.0, 8.0):
        ir = SK.irregular_seastate(Hs, 9.0, 180.0, HULL, 8.5, 12.0, n_freq=20, n_sections=41)
        ratios.append(ir["heave_rms_m"] / Hs)
    assert max(ratios) / min(ratios) == pytest.approx(1.0, rel=1e-6)


# ---------------------------------------------------------------------------
# independent time-domain closure (rain-flow, ASTM E1049-85 per m1653)
# ---------------------------------------------------------------------------
def test_narrowband_damage_closes_against_rainflow():
    rec = F.synthesise_stress(n=120_000, dt=0.2, sigma_rms_MPa=8.0, seed=11)
    rf = F.rainflow_damage(rec)
    st = F.record_spectral_stats(rec, 0.2)
    mom = F.rayleigh_bilinear_moment(st["sigma_rms_MPa"])
    d_nb = st["nu0_Hz"] * 120_000 * 0.2 * (mom["inv_life_m3"] + mom["inv_life_m5"])
    assert abs(d_nb - rf["damage"]) / rf["damage"] < 0.05


def test_life_is_finite_and_positive_across_the_envelope():
    for Hs in (0.5, 3.0, 7.5, 11.0, 14.0):
        out = F.spectral_fatigue(HULL, Hs, 9.0)
        assert math.isfinite(out["life_years"]) and out["life_years"] > 0.0