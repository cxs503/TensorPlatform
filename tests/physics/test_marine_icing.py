"""Unit + acceptance tests for the superstructure marine-icing kernel (W3-N09).

Acceptance criteria exercised here (numbers also written to
``artifacts/m796/validation.json`` by ``tools/m796_marine_icing_validation.py``):

(a) dry static heat-balance residual  < 1e-6 W m^-2
(b) icing rate vs analytic (Mákiranta-type) reference  < 15 % relative
(c) GM loss vs layered superposition analytic          < 3 % relative
(d) the suite itself (>= 15 passing tests)
"""
from __future__ import annotations

import math
import sys
from pathlib import Path

import pytest

BACKEND_DIR = Path(__file__).resolve().parents[2] / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from services.ship_performance import icing as ice
from services.ship_performance.icing import properties as props
from services.ship_performance.icing.reference import (
    layered_gm_loss,
    makiranta_reference_icing_rate,
)

RHO_ICE = 917.0
CALIB_T_AIR = -10.0
CALIB_WIND = 10.0


# ---------------------------------------------------------------------------
# properties
# ---------------------------------------------------------------------------
def test_saturation_pressure_over_ice_below_water():
    t = 263.15  # -10 C
    e_w = props.sat_vapour_pressure(t, over_ice=False)
    e_i = props.sat_vapour_pressure(t, over_ice=True)
    assert 250.0 < e_w < 300.0        # ~2.6 hPa at -10 C
    assert e_i < e_w                  # sublimation pressure < evaporation pressure


def test_seawater_freezing_point_matches_unesco():
    t_f = props.seawater_freezing_point(33.0) - 273.15
    assert -1.90 < t_f < -1.75        # ~ -1.81 C for S = 33 psu
    assert props.seawater_freezing_point(0.0) == pytest.approx(273.15, abs=1e-9)


def test_air_properties_physical_values():
    assert props.air_density(273.15) == pytest.approx(1.292, abs=0.01)
    assert props.air_kinematic_viscosity(273.15) == pytest.approx(1.32e-5, rel=0.12)
    assert props.air_prandtl(273.15) == pytest.approx(0.71, abs=0.03)
    assert props.latent_heat_fusion(273.15) == pytest.approx(3.337e5, rel=1e-3)


# ---------------------------------------------------------------------------
# transfer coefficients
# ---------------------------------------------------------------------------
def test_convection_grows_with_wind_speed():
    air = ice.AirState(t_air=263.15)
    h5 = ice.transfer_coefficients(air, 5.0, 5.0).h_conv
    h15 = ice.transfer_coefficients(air, 15.0, 5.0).h_conv
    assert h15 > h5 > 0.0
    # flat-plate forced convection at 10 m/s over a 5 m face: 20-40 W/m^2K
    assert 20.0 < ice.transfer_coefficients(air, 10.0, 5.0).h_conv < 40.0


def test_lewis_analogy_mass_transfer_coefficient():
    air = ice.AirState(t_air=263.15)
    c = ice.transfer_coefficients(air, 10.0, 5.0)
    # h_m ~ h_c / (rho cp Le^(2/3)) ~ 0.02 m/s
    assert 0.01 < c.h_mass < 0.05
    # water vapour in air: Sc ~ 0.6 < Pr ~ 0.71, i.e. Lewis number Le ~ 0.85
    assert c.schmidt < c.prandtl
    assert 0.7 < c.schmidt / c.prandtl < 1.0


# ---------------------------------------------------------------------------
# criterion (a): dry static equilibrium residual
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("t_air_c", [-5.0, -10.0, -20.0, -30.0])
def test_dry_static_equilibrium_residual_below_1e6(t_air_c):
    air = ice.AirState(t_air=t_air_c + 273.15, rel_humidity=0.85)
    coeffs = ice.transfer_coefficients(air, 10.0, 5.0)
    t_s, residual = ice.solve_equilibrium_surface_temperature(
        air, coeffs, source_flux=0.0, over_ice=True)
    assert abs(residual) < 1e-6            # criterion (a)
    assert air.t_air - 20.0 < t_s <= 273.15

    # the residual is the actual flux imbalance, re-evaluated independently
    total = ice.surface_heat_loss(t_s, air, coeffs, over_ice=True)["total"]
    assert abs(total) < 1e-6


def test_surface_equilibrium_below_air_under_clear_sky():
    # A dry ice surface radiating to a clear sky (T_sky ~ 235 K at -10 C air)
    # must equilibrate *below* the air temperature: that is the whole point of
    # the dry-regime solve.
    air = ice.AirState(t_air=263.15)
    coeffs = ice.transfer_coefficients(air, 10.0, 5.0)
    t_s, residual = ice.solve_equilibrium_surface_temperature(
        air, coeffs, 0.0, over_ice=True)
    assert air.t_air - 25.0 < t_s < air.t_air
    assert abs(residual) < 1e-6
    # an overcast sky (T_sky = T_air) raises the equilibrium temperature
    overcast = ice.AirState(t_air=263.15, cloud_fraction=1.0)
    t_s_overcast, _ = ice.solve_equilibrium_surface_temperature(
        overcast, coeffs, 0.0, over_ice=True)
    assert t_s_overcast > t_s


# ---------------------------------------------------------------------------
# regimes / freezing fraction
# ---------------------------------------------------------------------------
def test_wet_regime_freezing_fraction_bounded():
    met = ice.Metocean(t_air_c=-10.0, wind_speed=10.0, t_water_c=1.0)
    air, water, spray = met.air(), met.water(), met.spray()
    coeffs = ice.transfer_coefficients(air, met.wind_speed, 5.0)
    bal = ice.panel_heat_balance(air, water, coeffs, 2.0e-3,
                                 view_factor_to_sky=met.view_factor_to_sky)
    assert bal.regime == "wet"
    assert 0.0 < bal.freezing_fraction < 1.0
    assert bal.ice_mass_flux + bal.runoff_flux == pytest.approx(2.0e-3, rel=1e-9)


def test_heavy_spray_is_wet_small_spray_is_dry():
    met = ice.Metocean(t_air_c=-15.0, wind_speed=10.0, t_water_c=0.0)
    air, water = met.air(), met.water()
    coeffs = ice.transfer_coefficients(air, 10.0, 5.0)
    small = ice.panel_heat_balance(air, water, coeffs, 1.0e-5)
    big = ice.panel_heat_balance(air, water, coeffs, 1.0e-1)
    assert small.regime == "dry"
    assert small.freezing_fraction == pytest.approx(1.0)
    assert big.regime == "wet"
    assert big.freezing_fraction < 0.1        # heat limited


def test_no_spray_means_no_accretion():
    met = ice.Metocean(t_air_c=-20.0, wind_speed=10.0)
    air, water = met.air(), met.water()
    coeffs = ice.transfer_coefficients(air, 10.0, 5.0)
    bal = ice.panel_heat_balance(air, water, coeffs, 0.0)
    assert bal.regime == "dry_nospray"
    assert bal.ice_mass_flux == 0.0
    assert bal.surface_temp < water.t_freezing


# ---------------------------------------------------------------------------
# criterion (b): calibration point vs analytic reference
# ---------------------------------------------------------------------------
def test_icing_rate_matches_reference_within_15pct():
    met = ice.Metocean(t_air_c=CALIB_T_AIR, wind_speed=CALIB_WIND, t_water_c=1.0)
    air, water = met.air(), met.water()
    char_len = 5.25
    coeffs = ice.transfer_coefficients(air, CALIB_WIND, char_len)
    flux = ice.spray_mass_flux(CALIB_WIND, "windward", (0.0, 1.0, 0.0), (0.0, -1.0, 0.0))
    bal = ice.panel_heat_balance(air, water, coeffs, flux)
    model_rate = 3.6e6 * bal.ice_mass_flux / RHO_ICE
    ref = makiranta_reference_icing_rate(
        CALIB_T_AIR, CALIB_WIND, t_water_c=1.0, char_length=char_len, spray_flux=flux)
    ref_rate = ref.ice_thickness_rate * 3.6e6
    rel = abs(model_rate - ref_rate) / ref_rate
    assert rel < 0.15                       # criterion (b)
    assert 1.0 < model_rate < 20.0          # physically plausible mm/h band


def test_reference_monotone_in_temperature_and_wind():
    cold = makiranta_reference_icing_rate(-20.0, 10.0)
    warm = makiranta_reference_icing_rate(-5.0, 10.0)
    windy = makiranta_reference_icing_rate(-10.0, 20.0)
    calm = makiranta_reference_icing_rate(-10.0, 5.0)
    assert cold.ice_mass_flux > warm.ice_mass_flux
    assert windy.ice_mass_flux > calm.ice_mass_flux


# ---------------------------------------------------------------------------
# spray / mesh
# ---------------------------------------------------------------------------
def test_spray_flux_scaling_and_shelter():
    assert ice.significant_wave_height(10.0) == pytest.approx(2.14, abs=0.02)
    windward = ice.spray_mass_flux(10.0, "windward", (0.0, 1.0, 0.0), (0.0, -1.0, 0.0))
    leeward = ice.spray_mass_flux(10.0, "leeward", (0.0, -1.0, 0.0), (0.0, -1.0, 0.0))
    assert windward > 0.0
    assert leeward == 0.0
    flux5 = ice.spray_mass_flux(5.0, "windward", (0.0, 1.0, 0.0), (0.0, -1.0, 0.0))
    flux20 = ice.spray_mass_flux(20.0, "windward", (0.0, 1.0, 0.0), (0.0, -1.0, 0.0))
    # m_w ~ W_eff(U) * U with W_eff ~ sqrt(Hs) ~ U  =>  m_w ~ U^2
    assert flux20 / flux5 == pytest.approx((20.0 / 5.0) ** 2, rel=0.10)


def test_mesh_areas_and_zone_classification():
    block = ice.Block(name="house", origin=(0.0, 0.0, 4.0), size=(4.0, 6.0, 3.0))
    panels = ice.block_panels(block, panel_size=1.0, wind_dir=(0.0, -1.0, 0.0))
    by_face = {}
    for p in panels:
        by_face[p.face] = by_face.get(p.face, 0.0) + p.area
    assert by_face["front"] == pytest.approx(6.0 * 3.0)
    assert by_face["port"] == pytest.approx(4.0 * 3.0)
    assert by_face["top"] == pytest.approx(4.0 * 6.0)
    port = [p for p in panels if p.face == "port"]
    starboard = [p for p in panels if p.face == "starboard"]
    assert {p.zone for p in port} == {"windward"}      # wind blows towards -y
    assert {p.zone for p in starboard} == {"leeward"}


def test_mesh_deck_panel_added_without_double_counting():
    vessel = ice.preset_polar_fishing_vessel()
    panels = vessel.mesh()
    deck = [p for p in panels if p.name == "weather_deck"]
    assert len(deck) == 1
    assert deck[0].centroid[2] == pytest.approx(vessel.deck_height)
    summary = ice.panel_area_summary(panels)
    assert summary["total_area_m2"] > vessel.deck_area * 0.9


# ---------------------------------------------------------------------------
# accretion
# ---------------------------------------------------------------------------
def test_ice_conduction_slows_accretion():
    met = ice.Metocean(t_air_c=-15.0, wind_speed=12.0, t_water_c=1.0)
    air, water, spray = met.air(), met.water(), met.spray()
    panel = ice.Panel(name="p", zone="windward", area=1.0, centroid=(0.0, 0.0, 6.0),
                      normal=(0.0, 1.0, 0.0), char_length=5.0)
    thin = ice.PanelIceState(panel=panel)
    thick = ice.PanelIceState(panel=panel, mass=917.0 * 0.05, thickness=0.05)
    ice.step_icing([thin], air, water, spray, 60.0)
    ice.step_icing([thick], air, water, spray, 60.0)
    assert thin.ice_flux > thick.ice_flux


def test_integration_timestep_convergence():
    vessel = ice.preset_polar_fishing_vessel()
    met = ice.Metocean(t_air_c=-15.0, wind_speed=15.0, t_water_c=1.0)
    coarse = ice.run_icing_case(vessel, met, duration_h=6.0, dt_s=1800.0)
    fine = ice.run_icing_case(vessel, met, duration_h=6.0, dt_s=300.0)
    assert coarse.total_ice_mass > 0.0
    assert abs(coarse.total_ice_mass - fine.total_ice_mass) / fine.total_ice_mass < 0.02


# ---------------------------------------------------------------------------
# stability
# ---------------------------------------------------------------------------
def test_polygon_area_centroid_rectangle():
    poly = [(-2.0, 0.0), (2.0, 0.0), (2.0, 3.0), (-2.0, 3.0)]
    area, cy, cz = ice.stability.polygon_area_centroid(poly)
    assert area == pytest.approx(12.0)
    assert cy == pytest.approx(0.0, abs=1e-12)
    assert cz == pytest.approx(1.5)


def test_small_angle_gz_matches_wall_sided_formula():
    hull = ice.preset_polar_fishing_vessel().hull
    cond = ice.LoadingCondition(hull=hull, kg_light=2.55)
    for angle in (5.0, 10.0):
        phi = math.radians(angle)
        wall = cond.gm_light * math.sin(phi) + 0.5 * hull.bm * math.tan(phi) ** 2 * math.sin(phi)
        assert cond.gz(angle) == pytest.approx(wall, rel=0.03)


def test_added_mass_sinks_ship_and_raises_kg():
    hull = ice.preset_polar_fishing_vessel().hull
    load = ice.IceLoad(mass=20000.0, moment_z=20000.0 * 6.0, moment_y=0.0)
    cond = ice.LoadingCondition(hull=hull, kg_light=2.55, ice=load)
    expected_draft = hull.draft + 20000.0 / (1025.0 * hull.waterplane_area)
    assert cond.draft_loaded == pytest.approx(expected_draft, rel=1e-9)
    assert cond.kg_loaded > 2.55
    assert cond.gm_loss < 0.0                 # icing always costs GM


def test_heeling_moment_and_heel_direction():
    hull = ice.preset_polar_fishing_vessel().hull
    load = ice.IceLoad(mass=5000.0, moment_z=5000.0 * 6.0, moment_y=5000.0 * 2.0)
    cond = ice.LoadingCondition(hull=hull, kg_light=2.55, ice=load)
    assert cond.heeling_moment_knm == pytest.approx(9.80665 * 5000.0 * 2.0 / 1000.0)
    heel = cond.heel_from_ice()
    assert heel > 0.0                          # heels towards the iced side (+y)
    assert cond.gz(heel) == pytest.approx(0.0, abs=1e-6)


def test_polar_code_design_allowance_mass():
    vessel = ice.preset_polar_fishing_vessel()
    panels = vessel.mesh()
    design = ice.polar_code_design_ice_mass(panels)
    exposed = sum(p.area for p in panels if p.zone != "horizontal_down")
    sheltered = sum(p.area for p in panels if p.zone == "horizontal_down")
    expected = 900.0 * (0.30 * exposed + 0.075 * sheltered)
    assert design.mass == pytest.approx(expected, rel=1e-9)
    assert ice.POLAR_CODE_ICE_ALLOWANCE == {"exposed": 0.30, "sheltered": 0.075}


def test_stability_criteria_structure_and_light_ship():
    vessel = ice.preset_polar_fishing_vessel()
    cond = ice.LoadingCondition(hull=vessel.hull, kg_light=vessel.kg_light)
    crit = cond.stability_criteria()
    for key in ("gm_initial", "area_0_30", "gz_at_30", "heel_at_max_gz", "area_30_40"):
        assert key in crit and "pass" in crit[key] and "reference" in crit[key]
    assert crit["gm_initial"]["pass"]          # intact light ship meets GM >= 0.15 m


# ---------------------------------------------------------------------------
# criterion (c) + end-to-end
# ---------------------------------------------------------------------------
def test_gm_loss_matches_layered_superposition_within_3pct():
    vessel = ice.preset_polar_fishing_vessel()
    met = ice.Metocean(t_air_c=-15.0, wind_speed=18.0, t_water_c=0.5)
    result = ice.run_icing_case(vessel, met, duration_h=12.0)
    panel_mass = {p["name"]: p["mass_kg"] for p in result.panel_table}
    panels = vessel.mesh(met.wind_dir)
    for n_layers in (3, 6):
        _, lumped = layered_gm_loss(panels, panel_mass, vessel.hull,
                                    vessel.kg_light, n_layers=n_layers)
        rel = abs(lumped.gm_loss - result.gm_loss) / abs(result.gm_loss)
        assert rel < 0.03                       # criterion (c)


def test_end_to_end_case_is_self_consistent():
    vessel = ice.preset_polar_fishing_vessel()
    met = ice.Metocean(t_air_c=-15.0, wind_speed=18.0, t_water_c=0.5)
    result = ice.run_icing_case(vessel, met, duration_h=24.0)
    payload = result.to_dict()
    trees = {
        "ice mass": payload["ice"]["total_mass_kg"],
        "gm loss": payload["stability"]["gm_loss_m"],
        "criteria": payload["polar_code"]["intact_stability_satisfied_with_icing"],
    }
    assert trees["ice mass"] > 0.0
    assert trees["gm loss"] < 0.0
    assert isinstance(trees["criteria"], bool)
    # mass/centroid consistency between the panel table and the lumped load
    total = sum(p["mass_kg"] for p in result.panel_table)
    assert total == pytest.approx(payload["ice"]["total_mass_kg"], rel=1e-12)
    assert payload["stability"]["heel_angle_deg"] != 0.0
    # the time series must be monotone in ice mass
    masses = payload["time_series"]["total_mass_kg"]
    assert masses[-1] > masses[0] == 0.0
    assert all(b >= a - 1e-9 for a, b in zip(masses, masses[1:]))

def test_ship_speed_puts_spray_on_bow_faces():
    """Relative wind (u_wind - u_ship) is what wets the forward-facing panels."""
    still = ice.Metocean(t_air_c=-10.0, wind_speed=15.0, t_water_c=1.0,
                         wind_dir=(0.0, -1.0, 0.0), ship_speed=0.0)
    making_way = ice.Metocean(t_air_c=-10.0, wind_speed=15.0, t_water_c=1.0,
                              wind_dir=(0.0, -1.0, 0.0), ship_speed=6.0)
    bow_normal = (1.0, 0.0, 0.0)          # wheelhouse front face / +x
    assert ice.spray_mass_flux(still.relative_wind_speed, "side", bow_normal,
                               still.relative_wind_dir) == 0.0
    assert ice.spray_mass_flux(making_way.relative_wind_speed, "side", bow_normal,
                               making_way.relative_wind_dir) > 0.0
    assert making_way.relative_wind_speed > still.relative_wind_speed


def test_ship_service_speed_inherited_when_not_specified():
    vessel = ice.preset_polar_fishing_vessel()
    assert vessel.service_speed > 0.0
    met = ice.Metocean(t_air_c=-10.0, wind_speed=10.0)
    result = ice.run_icing_case(vessel, met, duration_h=2.0, dt_s=1800.0)
    assert result.metocean.ship_speed == pytest.approx(vessel.service_speed)
    assert result.metocean.relative_wind_speed > met.wind_speed
