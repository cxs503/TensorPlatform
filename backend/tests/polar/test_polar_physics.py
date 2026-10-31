"""Physics / algorithm tests for the polar voyage module (campaign W3-N11).

Covered: ice-field discretisation, geometry validation, level-ice resistance
(criterion a), brash channel (criterion b), escort spacing (criterion c),
CII/EEDI (criterion d) and the joint route/speed optimisation (criterion e).
"""

from __future__ import annotations

import math
import sys
from pathlib import Path

import numpy as np
import pytest

BACKEND_DIR = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(BACKEND_DIR))

from services.polar import cii as cii_mod
from services.polar import resistance as ice_mod
from services.polar.channel import (
    calibrate_k,
    channel_resistance,
    channel_resistance_reference,
    verify_channel_concentrations,
)
from services.polar.constants import KNOT
from services.polar.convoy import (
    ConvoyConfig,
    convoy_energy_per_m,
    optimize_spacing,
    stefan_growth_coefficient,
    verify_escort_optimum,
)
from services.polar.ice_field import IceField
from services.polar.route import (
    RoutePlanner,
    experience_baseline,
    max_sustainable_speed_ms,
    plan_voyage,
    refined_speed_plan,
)
from services.polar.ships import get_ship, list_ships, ship_from_params, validate_geometry


# ---------------------------------------------------------------------------
# geometry / ship presets
# ---------------------------------------------------------------------------

def test_all_ship_presets_pass_geometry_validation():
    assert len(list_ships()) >= 5
    for ship in list_ships():
        check = validate_geometry(get_ship(ship["key"]))
        assert check["ok"], check["failures"]


def test_geometry_validation_rejects_impossible_hull():
    bad = ship_from_params({"length_m": 180.0, "beam_m": 28.4, "draft_m": 10.5, "cb": 0.55,
                            "dwt_t": 35000.0})
    # Cb = nabla/(L B T) is self-consistent by construction, but a 0.55 Cb
    # 35 kDWT bulker of these dimensions is not a displacement hull; the check
    # must still flag the DWT > displacement inconsistency
    check = validate_geometry(bad)
    assert check["ok"] is False
    names = {c["name"] for c in check["failures"]}
    assert "dwt_below_displacement" in names or "cb" in names


# ---------------------------------------------------------------------------
# ice field
# ---------------------------------------------------------------------------

def test_uniform_ice_field_summary_and_sampling():
    field = IceField.uniform(0.6, 1.3, nx=21, ny=11, dx_km=3.0, current_ms=0.2)
    summary = field.validate()
    assert summary["grid"] == {"nx": 21, "ny": 11, "dx_km": 3.0, "dy_km": 3.0}
    assert summary["h_m"]["mean_ice"] == pytest.approx(0.6, abs=1e-9)
    sample = field.sample(30.0, 15.0)
    assert sample["h_m"] == pytest.approx(0.6, abs=1e-9)
    assert sample["u_current_ms"] == pytest.approx(0.2, abs=1e-9)
    assert sample["land"] is False


def test_ice_field_rejects_impossible_units():
    field = IceField.uniform(0.6, 1.3, nx=5, ny=5)
    field.h = field.h * 1000.0          # metres entered as millimetres
    with pytest.raises(ValueError, match="outside physical window"):
        field.validate()


def test_ice_field_roundtrip_dict_and_csv(tmp_path):
    field = IceField.uniform(0.7, 1.4, nx=7, ny=5)
    clone = IceField.from_dict(field.to_dict(include_grid=True))
    assert np.allclose(clone.h, field.h)
    assert np.allclose(clone.sigma, field.sigma)

    csv_path = tmp_path / "field.csv"
    rows = ["ix,iy,h,sigma,u_current,v_current,u_wind,v_wind,land"]
    for iy in range(field.ny):
        for ix in range(field.nx):
            rows.append(f"{ix},{iy},0.7,1.4,0.1,0.0,5.0,-4.0,0")
    csv_path.write_text("\n".join(rows), encoding="utf-8")
    loaded = IceField.from_csv(csv_path)
    assert loaded.shape == (5, 7)
    assert loaded.sample(0.0, 0.0)["h_m"] == pytest.approx(0.7, abs=1e-9)


def test_scenario_is_reproducible_and_contains_leads():
    a = IceField.generate_scenario()
    b = IceField.generate_scenario()
    assert np.array_equal(a.h, b.h)
    # a lead must be much thinner than the ambient ice at the same latitude
    lead = a.sample(240.0, 200.0)["h_m"]
    ambient = a.sample(200.0, 180.0)["h_m"]
    assert lead < 0.7 * ambient
    # land / fast ice in the north
    assert a.sample(200.0, 290.0)["land"] is True


# ---------------------------------------------------------------------------
# level-ice resistance (criterion a)
# ---------------------------------------------------------------------------

def test_holtrop_open_water_grows_with_speed():
    ship = get_ship("handysize_bulker")
    r5 = ice_mod.holtrop_open_water_resistance(ship, 2.0)["total"]
    r10 = ice_mod.holtrop_open_water_resistance(ship, 5.0)["total"]
    assert 0.0 < r5 < r10
    # wetted surface of a 35 kDWT bulker is 5 000-10 000 m2
    assert 5000.0 < ice_mod.wetted_surface_m2(ship) < 10000.0


def test_ice_resistance_monotone_in_thickness_and_present_all_terms():
    ship = get_ship("handysize_bulker")
    prev = 0.0
    for h in (0.2, 0.4, 0.6, 0.9, 1.2):
        comp = ice_mod.ice_components(ship, 3.0, h, 1.3)
        assert comp["crushing"] > 0 and comp["breaking"] > 0 and comp["submersion"] > 0
        assert comp["total"] > prev
        prev = comp["total"]
    assert ice_mod.ice_components(ship, 3.0, 0.0, 1.3)["total"] == 0.0


def test_channel_factor_reduces_breaking_component():
    ship = get_ship("pc6_bulker")
    full = ice_mod.ice_components(ship, 3.0, 0.8, 1.5, channel_factor=1.0)
    broken = ice_mod.ice_components(ship, 3.0, 0.8, 1.5, channel_factor=0.0)
    assert full["breaking"] > 0.0
    assert broken["breaking"] == 0.0
    assert broken["crushing"] == pytest.approx(full["crushing"])


def test_track_integration_rejects_land_and_matches_uniform_analytic():
    ship = get_ship("handysize_bulker")
    field = IceField.uniform(0.6, 1.3, nx=41, ny=41)
    stations = ice_mod.track_stations(0.0, 60.0, 120.0, 60.0, n_stations=61)
    track = ice_mod.track_ice_resistance(field, ship, 3.0 * KNOT, stations)
    ref = ice_mod.holtrop_ice_corrected_reference(ship, 3.0 * KNOT, 0.6, 1.3)
    assert track["total_n"] == pytest.approx(ref["reference_total_n"], rel=1e-3)

    coast = IceField.generate_scenario()
    with pytest.raises(ValueError, match="land"):
        ice_mod.track_ice_resistance(
            coast, ship, 3.0 * KNOT, [(100.0, 250.0), (100.0, 295.0)]
        )


@pytest.mark.parametrize("ship_key", ["handysize_bulker", "pc6_bulker"])
def test_criterion_a_ice_resistance_under_10_percent(ship_key):
    report = ice_mod.verify_level_ice_conditions(get_ship(ship_key))
    assert len(report["rows"]) == 5
    assert report["max_abs_rel_error_pct"] < 10.0
    assert report["pass"] is True
    # the residual is the thickness-nonlinearity term (R ~ h^2), i.e. about CV^2
    for row in report["rows"]:
        assert abs(row["rel_error_total_pct"]) < 10.0


# ---------------------------------------------------------------------------
# brash channel (criterion b)
# ---------------------------------------------------------------------------

def test_channel_resistance_monotone_in_concentration_and_thickness():
    ship = get_ship("handysize_bulker")
    prev = 0.0
    for c in (0.1, 0.3, 0.5, 0.7):
        r = channel_resistance(ship, 3.0, 0.8, c)["total_n"]
        assert r > prev
        prev = r
    r_thin = channel_resistance(ship, 3.0, 0.4, 0.45)["total_n"]
    r_thick = channel_resistance(ship, 3.0, 1.0, 0.45)["total_n"]
    assert r_thick > r_thin


def test_channel_calibration_constant_is_positive_and_anchor_exact():
    ship = get_ship("handysize_bulker")
    k = calibrate_k(ship, 3.0, 0.8, 0.40)
    assert k > 0.0
    prod = channel_resistance(ship, 3.0, 0.8, 0.40)
    ref = channel_resistance_reference(ship, 3.0, 0.8, 0.40, k_cal=k)
    assert ref["total_n"] == pytest.approx(prod["total_n"], rel=1e-12)


def test_criterion_b_channel_under_10_percent_on_holdouts():
    report = verify_channel_concentrations(get_ship("handysize_bulker"))
    holdouts = [r for r in report["rows"] if r["role"] == "holdout"]
    assert len(holdouts) == 2
    assert report["max_abs_rel_error_pct"] < 10.0
    assert report["pass"] is True


# ---------------------------------------------------------------------------
# convoy spacing (criterion c)
# ---------------------------------------------------------------------------

def _convoy(**kwargs) -> ConvoyConfig:
    base = dict(escort=get_ship("icebreaker_escort"), follower=get_ship("handysize_bulker"),
                followers=2, h_level_m=0.9, h_brash_m=0.8, concentration=0.45, speed_kn=5.0)
    base.update(kwargs)
    return ConvoyConfig(**base)


def test_stefan_growth_coefficient_magnitude():
    a = stefan_growth_coefficient()
    assert 3e-4 < a < 1e-3           # ~5.4e-4 m/sqrt(s) for dT = 20 K
    assert a == pytest.approx(math.sqrt(2 * 2.2 * 20.0 / (917.0 * 3.33e5)), rel=1e-12)


def test_proximity_penalty_decreases_with_spacing():
    cfg = _convoy()
    near = convoy_energy_per_m(150.0, cfg, return_parts=True)
    far = convoy_energy_per_m(3000.0, cfg, return_parts=True)
    assert near["proximity_n"] > far["proximity_n"]
    # refreezing grows with spacing
    assert far["h_b_eff_m"] > near["h_b_eff_m"]


def test_more_followers_costs_more_energy():
    one = convoy_energy_per_m(800.0, _convoy(followers=1))
    three = convoy_energy_per_m(800.0, _convoy(followers=3))
    assert three > one


def test_criterion_c_escort_energy_has_interior_minimum():
    res = verify_escort_optimum(_convoy())
    assert res["pass"] is True
    assert res["interior_minimum"] is True
    assert res["derivative_sign_change"] is True
    assert 100.0 < res["spacing_opt_m"] < 4000.0
    # brute-force scan and golden-section optimum agree
    best_scan = min(res["curve"], key=lambda row: row["energy_mj_per_km"])
    assert best_scan["s_m"] == pytest.approx(res["spacing_opt_m"], rel=0.05)


# ---------------------------------------------------------------------------
# CII / EEDI (criterion d)
# ---------------------------------------------------------------------------

def test_criterion_d_cii_matches_imo_guideline_paths():
    report = cii_mod.verify_cii_ship_types(
        ["handysize_bulker", "aframax_tanker", "container_8000teu"], year=2026
    )
    assert report["pass"] is True
    assert len(report["rows"]) == 3
    assert report["max_two_path_rel_diff_pct"] < 5.0
    assert report["max_anchor_rel_error_pct"] < 5.0


def test_cii_rating_boundaries_follow_guideline_multipliers():
    req = 5.0
    assert cii_mod.cii_rating(0.80 * req, req)["rating"] == "A"
    assert cii_mod.cii_rating(0.90 * req, req)["rating"] == "B"
    assert cii_mod.cii_rating(1.00 * req, req)["rating"] == "C"
    assert cii_mod.cii_rating(1.10 * req, req)["rating"] == "D"
    assert cii_mod.cii_rating(1.30 * req, req)["rating"] == "E"


def test_required_cii_reduction_schedule_and_eedi_phase():
    ref = cii_mod.cii_reference_value("bulk_carrier", 50000.0)
    # MEPC.353(78) gives ~5.66 g/(DWT nm) for a 50 000 DWT bulk carrier
    assert ref == pytest.approx(5.66, abs=0.05)
    for year, z in ((2023, 5.0), (2024, 7.0), (2025, 9.0), (2026, 11.0)):
        got = cii_mod.required_cii("bulk_carrier", 50000.0, year)
        assert got["reduction_factor_pct"] == z
        assert got["required_cii"] == pytest.approx(ref * (1 - z / 100.0))
    eedi = cii_mod.required_eedi("bulk_carrier", 50000.0, phase=2)
    assert eedi["reduction_factor_pct"] == 20.0


# ---------------------------------------------------------------------------
# route / speed optimisation (criterion e)
# ---------------------------------------------------------------------------

def test_max_sustainable_speed_falls_with_ice_thickness():
    ship = get_ship("pc6_bulker")
    v_open = max_sustainable_speed_ms(ship, 0.0, 1.0)
    v_light = max_sustainable_speed_ms(ship, 0.4, 1.1)
    v_heavy = max_sustainable_speed_ms(ship, 1.2, 1.9)
    assert v_open > v_light > v_heavy > 0.0


def test_route_planner_blocks_ice_beyond_the_hull_limit():
    field = IceField.generate_scenario()
    ship = get_ship("handysize_bulker")           # h_max = 1.0 m
    with pytest.raises(ValueError):
        RoutePlanner(field, ship, stride=4).nearest_node(200.0, 290.0)  # land / fast ice
    planner = RoutePlanner(field, ship, stride=4)
    # every navigable node honours the operability limit
    assert np.all(planner.h <= ship.h_max_m + 1e-9)


def test_infeasible_deadline_is_reported():
    field = IceField.uniform(1.2, 1.9, nx=41, ny=41)
    ship = get_ship("pc6_bulker")
    with pytest.raises(ValueError, match="infeasible"):
        plan_voyage(field, ship, (0.0, 60.0), (120.0, 60.0), deadline_h=0.05, stride=2)


def test_deadline_scaled_experience_baseline_matches_deadline():
    field = IceField.uniform(0.5, 1.2, nx=41, ny=21)
    ship = get_ship("pc6_bulker")
    base = experience_baseline(field, ship, (0.0, 30.0), (120.0, 30.0),
                               mode="station_wise", deadline_h=20.0)
    assert base["time_h"] == pytest.approx(20.0, rel=0.02)
    assert base["speed_scale"] < 1.0


def test_refined_speed_plan_meets_deadline_and_slower_costs_less():
    field = IceField.uniform(0.5, 1.2, nx=41, ny=21)
    ship = get_ship("pc6_bulker")
    track = [(0.0, 30.0), (120.0, 30.0)]
    quick = refined_speed_plan(field, ship, track, deadline_h=6.0)
    relaxed = refined_speed_plan(field, ship, track, deadline_h=12.0)
    assert quick["time_h"] == pytest.approx(6.0, rel=2e-2)
    assert relaxed["time_h"] == pytest.approx(12.0, rel=2e-2)
    assert relaxed["fuel_kg"] < quick["fuel_kg"]


@pytest.mark.slow
def test_criterion_e_route_beats_best_experience_route():
    from services.polar.optimizer import verify_route_improvement

    report = verify_route_improvement()
    assert report["pass"] is True
    assert report["saving_vs_best_experience_pct"] > 5.0
    # the optimised passage must respect the ETA it was given
    assert report["optimized"]["time_h"] == pytest.approx(report["deadline_h"], rel=0.02)