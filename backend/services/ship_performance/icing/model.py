"""Top-level marine-icing model for the ship overall-performance platform.

Pipeline (node W3-N09):

    metocean ─┬─> spray flux (wind/wave empirical) ─┐
              └─> air/sea state ──────────────────┤
                                                 v
    superstructure surface mesh ──> per-panel Mákiranta/Lozowski heat balance
                                                 v
                                    dry/wet regime + freezing fraction
                                                 v
                             time integration -> ice mass per panel + centroid
                                                 v
                    lumped ice load -> KG / GM / GZ -> heeling moment & heel
                                                 v
                                Polar Code / IS Code 2008 stability verdict

Everything is pure Python (``math`` only) so the kernel is importable by the API
layer, the CLI validator and the pytest suite without extra dependencies.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field, replace
from typing import Dict, List, Optional, Sequence

from .accumulation import AccretionHistory, PanelIceState, integrate_icing, panel_ice_table
from .heat_balance import PanelHeatBalance, panel_heat_balance, transfer_coefficients
from .mesh import Block, Panel, build_superstructure_mesh, panel_area_summary
from .properties import RHO_ICE, AirState, WaterState, to_kelvin
from .spray import SprayCondition, effective_spray_content, significant_wave_height
from .stability import (
    POLAR_CODE_ICE_ALLOWANCE,
    BargeHull,
    IceLoad,
    LoadingCondition,
    ice_load_from_panels,
    polar_code_design_ice_mass,
)


# ---------------------------------------------------------------------------
# Scenario definition
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class Metocean:
    """Icing-relevant meteorological / oceanographic condition.

    ``wind_dir`` is the unit-ish vector of the direction the (true) wind blows
    *towards* in the ship frame (x fwd, y port, z up); ``ship_speed`` is the
    vessel's own speed along +x.  The spray field and the convective transfer
    are driven by the **relative** wind ``u_wind - u_ship`` — for a ship making
    way this is what puts spray on the bow/front faces, which is the dominant
    marine-icing mechanism.
    """

    t_air_c: float = -10.0
    wind_speed: float = 10.0
    t_water_c: float = 1.0
    salinity: float = 33.0
    rel_humidity: float = 0.85
    cloud_fraction: float = 0.0
    solar_irradiance: float = 0.0
    wind_dir: tuple[float, float, float] = (0.0, -1.0, 0.0)
    ship_speed: float | None = None  # m/s along +x; None -> vessel service speed
    view_factor_to_sky: float = 0.5

    def air(self) -> AirState:
        return AirState(
            t_air=to_kelvin(self.t_air_c),
            rel_humidity=self.rel_humidity,
            cloud_fraction=self.cloud_fraction,
            solar_irradiance=self.solar_irradiance,
        )

    def water(self) -> WaterState:
        return WaterState(t_water=to_kelvin(self.t_water_c), salinity=self.salinity)

    # -- relative wind (ship motion matters for bow spray) ----------------
    def relative_wind_vector(self) -> tuple[float, float, float]:
        """Air velocity relative to the ship, ``u_wind - u_ship`` [m/s]."""
        w = self.wind_dir
        mag = math.sqrt(sum(c * c for c in w)) or 1.0
        ux, uy, uz = (c / mag * self.wind_speed for c in w)
        return (ux - (self.ship_speed or 0.0), uy, uz)

    @property
    def relative_wind_speed(self) -> float:
        v = self.relative_wind_vector()
        return math.sqrt(sum(c * c for c in v))

    @property
    def relative_wind_dir(self) -> tuple[float, float, float]:
        """Unit vector of the direction the relative wind blows *towards*."""
        v = self.relative_wind_vector()
        mag = self.relative_wind_speed or 1.0
        return (v[0] / mag, v[1] / mag, v[2] / mag)

    def spray(self) -> SprayCondition:
        return SprayCondition(wind_speed=self.relative_wind_speed,
                              wind_dir=self.relative_wind_dir)


@dataclass(frozen=True)
class VesselSpec:
    """Hull + exposed superstructure envelope of the analysed vessel."""

    name: str
    hull: BargeHull
    kg_light: float
    blocks: Sequence[Block]
    deck_height: float
    deck_area: float
    panel_size: float = 0.75
    service_speed: float = 0.0   # m/s, vessel speed used when metocean omits it

    def mesh(self, wind_dir: tuple[float, float, float] = (0.0, -1.0, 0.0)) -> List[Panel]:
        return build_superstructure_mesh(
            self.blocks,
            deck_area=max(self.deck_area, 0.0),
            deck_height=self.deck_height,
            panel_size=self.panel_size,
            wind_dir=wind_dir,
        )


# ---------------------------------------------------------------------------
# Result container
# ---------------------------------------------------------------------------
@dataclass
class IcingCaseResult:
    metocean: Metocean
    vessel: VesselSpec
    duration_s: float
    mesh_summary: dict
    panel_table: List[dict]
    ice_load: IceLoad
    light_condition: LoadingCondition
    loaded_condition: LoadingCondition
    criteria_light: dict
    criteria_loaded: dict
    design_ice_load: IceLoad
    design_condition: LoadingCondition
    design_criteria: dict
    heel_angle_deg: float
    reference_panel_balance: dict
    time_series: dict
    polar_code_verdict: dict
    meta: dict = field(default_factory=dict)

    # -- convenience -----------------------------------------------------
    @property
    def total_ice_mass(self) -> float:
        return self.ice_load.mass

    @property
    def gm_loss(self) -> float:
        return self.loaded_condition.gm_loss

    def to_dict(self) -> dict:
        return {
            "vessel": self.vessel.name,
            "metocean": {
                "t_air_c": self.metocean.t_air_c,
                "wind_speed_ms": self.metocean.wind_speed,
                "t_water_c": self.metocean.t_water_c,
                "salinity_psu": self.metocean.salinity,
                "rel_humidity": self.metocean.rel_humidity,
                "view_factor_to_sky": self.metocean.view_factor_to_sky,
                "hs_m": significant_wave_height(self.metocean.wind_speed),
                "spray_water_content_kg_m3": effective_spray_content(self.metocean.wind_speed),
            },
            "duration_h": self.duration_s / 3600.0,
            "ship_speed_ms": self.metocean.ship_speed or 0.0,
            "relative_wind_speed_ms": self.metocean.relative_wind_speed,
            "mesh": self.mesh_summary,
            "reference_panel_balance": self.reference_panel_balance,
            "ice": {
                "total_mass_kg": self.ice_load.mass,
                "centroid_z_m": self.ice_load.kg_shift,
                "centroid_y_m": self.ice_load.y_centroid,
                "moment_z_kgm": self.ice_load.moment_z,
                "moment_y_kgm": self.ice_load.moment_y,
            },
            "stability": {
                "gm_light_m": self.light_condition.gm_light,
                "displacement_light_t": self.light_condition.hull.displacement_mass / 1e3,
                "displacement_loaded_t": self.loaded_condition.displacement_loaded / 1e3,
                "kg_light_m": self.vessel.kg_light,
                "draft_light_m": self.vessel.hull.draft,
                "gm_loaded_m": self.loaded_condition.gm_loaded,
                "kg_loaded_m": self.loaded_condition.kg_loaded,
                "draft_loaded_m": self.loaded_condition.draft_loaded,
                "gm_loss_m": self.loaded_condition.gm_loss,
                "gm_loss_percent": 100.0 * self.loaded_condition.gm_loss / self.light_condition.gm_light
                if self.light_condition.gm_light else 0.0,
                "heeling_moment_knm": self.loaded_condition.heeling_moment_knm,
                "heel_angle_deg": self.heel_angle_deg,
            },
            "criteria_light": self.criteria_light,
            "criteria_loaded": self.criteria_loaded,
            "polar_code": self.polar_code_verdict,
            "design_allowance": {
                "mass_kg": self.design_ice_load.mass,
                "gm_loaded_m": self.design_condition.gm_loaded,
                "gm_loss_m": self.design_condition.gm_loss,
                "criteria": self.design_criteria,
            },
            "panels": self.panel_table,
            "time_series": self.time_series,
            "meta": self.meta,
        }


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------
def run_icing_case(
    vessel: VesselSpec,
    metocean: Metocean,
    duration_h: float = 24.0,
    dt_s: float = 600.0,
    sample_every: int = 6,
    k_ice: float = 2.2,
) -> IcingCaseResult:
    """Run the full icing -> stability chain for one scenario."""
    if metocean.ship_speed is None:
        metocean = replace(metocean, ship_speed=vessel.service_speed)
    air = metocean.air()
    water = metocean.water()
    spray = metocean.spray()
    panels = vessel.mesh(wind_dir=metocean.relative_wind_dir)

    states, hist = integrate_icing(
        panels, air, water, spray,
        duration_s=duration_h * 3600.0,
        dt_s=dt_s,
        sample_every=sample_every,
        view_factor_to_sky=metocean.view_factor_to_sky,
        k_ice=k_ice,
    )

    ice = ice_load_from_panels(
        (s.mass, s.centroid[1], s.centroid[2]) for s in states
    )
    light = LoadingCondition(hull=vessel.hull, kg_light=vessel.kg_light)
    loaded = LoadingCondition(hull=vessel.hull, kg_light=vessel.kg_light, ice=ice)
    heel = loaded.heel_from_ice()

    design_ice = polar_code_design_ice_mass(panels)
    design_cond = LoadingCondition(hull=vessel.hull, kg_light=vessel.kg_light, ice=design_ice)

    # Representative heat balance: median windward panel at t = 0 (clean panel,
    # no ice conduction damping) — this is the number reported for the
    # calibration-point comparison.
    windward = [s for s in states if s.panel.zone == "windward"] or states
    ref_state = windward[len(windward) // 2]
    coeffs = transfer_coefficients(
        air, spray.wind_speed, ref_state.panel.char_length, orientation="vertical"
    )
    ref_flux = spray.panel_flux(ref_state.panel.zone, ref_state.panel.normal)
    ref_balance = panel_heat_balance(
        air, water, coeffs, ref_flux,
        view_factor_to_sky=metocean.view_factor_to_sky,
    )

    criteria_loaded = loaded.stability_criteria()
    verdict = _polar_code_verdict(criteria_loaded, loaded, heel)

    return IcingCaseResult(
        metocean=metocean,
        vessel=vessel,
        duration_s=duration_h * 3600.0,
        mesh_summary=panel_area_summary(panels),
        panel_table=panel_ice_table(states),
        ice_load=ice,
        light_condition=light,
        loaded_condition=loaded,
        criteria_light=light.stability_criteria(),
        criteria_loaded=criteria_loaded,
        design_ice_load=design_ice,
        design_condition=design_cond,
        design_criteria=design_cond.stability_criteria(),
        heel_angle_deg=heel,
        reference_panel_balance={
            **ref_balance.as_dict(),
            "panel": ref_state.panel.name,
            "char_length_m": ref_state.panel.char_length,
            "h_conv_w_m2k": coeffs.h_conv,
            "h_mass_m_s": coeffs.h_mass,
            "reynolds": coeffs.reynolds,
            "hourly_ice_thickness_rate_mm": 3.6e6 * ref_balance.ice_mass_flux / RHO_ICE,
        },
        time_series=hist.as_dict(),
        polar_code_verdict=verdict,
        meta={
            "model": "Mákiranta/Lozowski-type panel heat balance",
            "dt_s": dt_s,
            "n_panels": len(panels),
            "ice_conduction_k_w_mk": k_ice,
            "rho_ice_kg_m3": RHO_ICE,
        },
    )


def _polar_code_verdict(criteria: dict, condition: LoadingCondition, heel_deg: float) -> dict:
    """Polar Code Part I-A 6.3 icing-stability verdict."""
    failing = [k for k, v in criteria.items() if k != "all_pass" and not v["pass"]]
    return {
        "reference": "IMO Polar Code Part I-A 6.3 + IS Code 2008 Part A 2.2",
        "icing_allowance_m": dict(POLAR_CODE_ICE_ALLOWANCE),
        "intact_stability_satisfied_with_icing": criteria["all_pass"]["pass"],
        "failing_criteria": failing,
        "gm_loaded_m": condition.gm_loaded,
        "gm_min_required_m": 0.15,
        "heel_due_to_asymmetric_ice_deg": heel_deg,
        "heel_informational_note": (
            "Icing heel is reported for information; the IS Code 2008 does not "
            "set a numeric heel limit for ice accretion (operator practice "
            "commonly uses <= 5 deg)."
        ),
    }


# ---------------------------------------------------------------------------
# Vessel presets (used by the CLI validator, the API and the tests)
# ---------------------------------------------------------------------------
def preset_polar_fishing_vessel() -> VesselSpec:
    """Small ice-classed fishing/research vessel — the icing-critical case."""
    hull = BargeHull(length=28.0, beam=7.5, depth=4.0, draft=2.8, block_coefficient=0.75)
    blocks = [
        Block(name="wheelhouse", origin=(1.0, -2.75, 4.0), size=(7.0, 5.5, 3.5)),
        Block(name="funnel", origin=(-4.5, -0.6, 4.0), size=(2.0, 1.2, 2.2)),
    ]
    return VesselSpec(
        name="polar_fishing_vessel_28m",
        hull=hull,
        kg_light=2.55,
        blocks=blocks,
        deck_height=4.0,
        deck_area=28.0 * 7.5 - 7.0 * 5.5 - 2.0 * 1.2,
        panel_size=0.75,
        service_speed=6.0,          # m/s (~11.7 kn)
    )


def preset_ice_class_bulker() -> VesselSpec:
    """Mid-size ice-classed cargo ship — Polar Code design-icing case."""
    hull = BargeHull(length=90.0, beam=18.0, depth=9.0, draft=6.0, block_coefficient=0.85)
    blocks = [
        Block(name="accommodation", origin=(-30.0, -8.0, 9.0), size=(14.0, 16.0, 12.0)),
        Block(name="forecastle", origin=(30.0, -6.0, 9.0), size=(8.0, 12.0, 2.5)),
    ]
    return VesselSpec(
        name="ice_class_bulker_90m",
        hull=hull,
        kg_light=5.9,
        blocks=blocks,
        deck_height=9.0,
        deck_area=90.0 * 18.0 - 14.0 * 16.0 - 8.0 * 12.0,
        panel_size=1.0,
        service_speed=7.0,          # m/s (~13.6 kn)
    )


PRESETS = {
    "polar_fishing_vessel_28m": preset_polar_fishing_vessel,
    "ice_class_bulker_90m": preset_ice_class_bulker,
}