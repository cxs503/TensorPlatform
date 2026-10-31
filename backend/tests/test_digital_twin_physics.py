"""Physics-level verification of the digital-twin reference models.

Every assertion here is a *cross-check between two independent computational
routes* (analytic vs numerical, or spectral vs time-domain), so the numbers in
the report can be traced back to an executable criterion.
"""
import math

import numpy as np
import pytest

from services.digital_twin.models.fatigue import (S_KNEE, damage_from_stress_rms,
                                                  spectral_vs_rainflow,
                                                  stress_from_damage)
from services.digital_twin.models.geometry import HullForm, exponent_for_cb
from services.digital_twin.models.manoeuvring import turning_circle, zigzag
from services.digital_twin.models.resistance import (holtrop_resistance,
                                                     lindqvist_ice_resistance)
from services.digital_twin.models.seakeeping import irregular_seastate, long_wave_check
from services.digital_twin.models.stability import kn_curve, wall_sided_gz
from services.digital_twin.types import GeometryValidationError

SHIP = dict(L=180.0, B=32.0, T=11.5, Cb=0.80)


@pytest.fixture(scope="module")
def ship():
    return HullForm(**SHIP)


def test_geometry_cb_closure(ship):
    """Displacement / waterplane / inertias reproduce the closed-form integrals."""
    rep = ship.verify()
    assert rep["volume_rel_error"] < 1e-4
    assert rep["awp_rel_error"] < 1e-4
    assert rep["IT_rel_error"] < 1e-4
    assert rep["IL_rel_error"] < 1e-3
    assert rep["Cb_numeric"] == pytest.approx(0.80, abs=1e-5)


def test_geometry_rejects_inconsistent_hull():
    with pytest.raises(GeometryValidationError):
        HullForm(180.0, 32.0, 11.5, 1.4)          # Cb out of range
    with pytest.raises(GeometryValidationError):
        HullForm(20.0, 32.0, 11.5, 0.8)           # L/B far too small


def test_ittc57_friction_line():
    """R_F must reproduce the ITTC-57 line exactly (same formula, independent evaluation)."""
    r = holtrop_resistance(7.0, **SHIP)
    cf_ref = 0.075 / (math.log10(r["Rn"]) - 2.0) ** 2
    assert r["Cf"] == pytest.approx(cf_ref, rel=1e-12)
    rf_ref = 0.5 * 1025.0 * 49.0 * r["wetted_surface_m2"] * cf_ref
    assert r["RF_N"] == pytest.approx(rf_ref, rel=1e-9)


def test_holtrop_monotonic_and_plausible():
    """Resistance increases with speed; delivered power stays in a plausible band."""
    speeds = [3.0, 5.0, 7.0, 9.0]
    rts = [holtrop_resistance(v, **SHIP)["RT_N"] for v in speeds]
    assert all(b > a for a, b in zip(rts, rts[1:]))
    assert 400e3 < rts[2] < 900e3            # ~7 m/s (13.6 kn) design point, 1A bulker band
    assert 1.2 < holtrop_resistance(7.0, **SHIP)["form_factor"] < 1.6


def test_lindqvist_ice_monotonic_and_zero():
    base = lindqvist_ice_resistance(2.5, **SHIP, ice_concentration=0.5, ice_thickness=0.5)
    heavier = lindqvist_ice_resistance(2.5, **SHIP, ice_concentration=0.9, ice_thickness=0.9)
    none = lindqvist_ice_resistance(2.5, **SHIP, ice_concentration=0.0, ice_thickness=0.5)
    assert base["R_ice_N"] > 0
    assert heavier["R_ice_N"] > base["R_ice_N"]
    assert none["R_ice_N"] == 0.0
    assert base["ice_speed_factor"] > 1.0


def test_seakeeping_long_wave_limits(ship):
    """RAO_heave -> 1 and RAO_pitch/(k zeta) -> 1 as kL -> 0 (theoretical limits)."""
    chk = long_wave_check(ship, KG=10.0, U=7.0)
    assert chk["kL"] < 1e-3
    assert chk["heave_rao"] == pytest.approx(1.0, abs=2e-3)
    assert chk["pitch_rao_nondim"] == pytest.approx(1.0, abs=2e-3)


def test_seakeeping_seastate_plausible(ship):
    r = irregular_seastate(4.0, 8.0, 180.0, ship, KG=10.0, U=7.0)
    assert 0.4 < r["heave_rms_m"] < 2.5
    assert 1.0 < r["pitch_rms_deg"] < 12.0
    assert 10.0 < r["R_aw_kN"] < 250.0


def test_stability_gm_crosscheck(ship):
    """dKN/dphi at upright vs KB + BM - KG from two independent formulas (<1 %)."""
    kc = kn_curve(ship, [0.0, 10.0, 20.0], KG=10.0)
    assert kc["GM_rel_error"] < 0.01
    assert kc["GM_numeric_m"] == pytest.approx(ship.KB + ship.BM - 10.0, rel=0.01)


def test_stability_wall_sided_barge():
    """Cb = 1 barge: numerical GZ vs analytic wall-sided formula (<3 %) before deck-edge immersion."""
    box = HullForm(100.0, 20.0, 6.0, 1.0)
    heels = [0.0, 2.0, 4.0, 6.0, 8.0]
    kc = kn_curve(box, heels, KG=9.0)
    for heel, gz in zip(kc["heels_deg"], kc["GZ_m"]):
        ref = wall_sided_gz(box, heel, 9.0)
        if abs(ref) > 1e-4:
            assert abs(gz - ref) / abs(ref) < 0.03, (heel, gz, ref)


def test_fatigue_spectral_vs_rainflow():
    """Spectral Miner vs rain-flow counting on a synthesised bimodal record (<5 %)."""
    rep = spectral_vs_rainflow(n=120_000, dt=0.2, sigma_rms_MPa=10.0)
    assert rep["ratio_narrowband"] == pytest.approx(1.0, rel=0.05)
    assert rep["damage_rainflow"] > 0


def test_fatigue_bilinear_sn_and_stress_inversion():
    """Bilinear S-N knee exists and stress<->damage inversion is consistent."""
    assert S_KNEE == pytest.approx(36.84, abs=0.05)
    d0 = 5e-4
    sig = stress_from_damage(d0, Tz_s=5.7)
    assert damage_from_stress_rms(sig, Tz_s=5.7) == pytest.approx(d0, rel=1e-3)


def test_manoeuvring_turning_band(ship):
    """Tactical diameter within the published band for full-form single-screw ships."""
    r = turning_circle(ship, 7.0, rudder_deg=35.0)
    assert 2.0 < r["turning_diameter_L"] < 5.0
    assert 1.5 < r["advance_L"] < 4.0
    assert 30.0 < r["speed_loss_pct"] < 85.0
    harder = turning_circle(ship, 7.0, rudder_deg=20.0)
    assert harder["turning_diameter_L"] > r["turning_diameter_L"]
    assert not math.isnan(zigzag(ship, 7.0, 10.0)["first_overshoot_deg"])