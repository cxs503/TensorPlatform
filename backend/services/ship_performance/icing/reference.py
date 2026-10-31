"""Closed-form / lumped cross-check models ("analytic reference" layer).

Two independent references are provided for the machine-checkable acceptance
criteria of node W3-N09:

* :func:`makiranta_reference_icing_rate` — closed-form evaluation of the
  Mákiranta-type surface energy balance at a calibration point.  It is a
  *separate* implementation: ISO 12494 closures (properties at the air
  temperature instead of the film temperature, ``Le = 1`` Colburn form,
  mixing-ratio humidity difference instead of vapour density) and an analytic
  expression for the freezing fraction instead of a root find.  Because the two
  implementations share the physics but not the numerics/closures, their
  difference measures formulation sensitivity — hence the literature-level
  (15 %) acceptance band instead of 3 %.

* :func:`layered_gm_loss` — lumped "layered superposition" analytic GM loss:
  the ice mass is binned into horizontal layers and each layer treated as a
  single mass at its area-weighted centroid, i.e. the classical hand-calculation
  route.  Comparing it with the per-panel integration quantifies the
  discretisation error of the GM-loss result.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Dict, Iterable, List, Sequence

from .heat_balance import EMISSIVITY_ICE, transfer_coefficients
from .properties import (
    AirState,
    WaterState,
    air_conductivity,
    air_density,
    air_kinematic_viscosity,
    latent_heat_fusion,
    latent_heat_vaporisation,
    sat_vapour_pressure,
    vapour_density,
)
from .stability import BargeHull, IceLoad, LoadingCondition

SIGMA_SB = 5.670374419e-8
R_DRY_AIR = 287.05
R_VAPOUR = 461.51
CP_AIR = 1005.0


@dataclass(frozen=True)
class ReferenceIcingRate:
    """Result of the closed-form reference evaluation."""

    ice_mass_flux: float        # kg m^-2 s^-1
    ice_thickness_rate: float   # m s^-1 (rho_ice = 917)
    freezing_fraction: float
    regime: str
    q_sink: float               # W m^-2 available at the film front
    q_conv: float
    q_evap: float
    q_lw: float
    h_conv: float

    def as_dict(self) -> Dict[str, float | str]:
        return {
            "ice_mass_flux_kg_m2s": self.ice_mass_flux,
            "ice_thickness_rate_mm_per_h": self.ice_thickness_rate * 3.6e6,
            "freezing_fraction": self.freezing_fraction,
            "regime": self.regime,
            "q_sink_w_m2": self.q_sink,
            "q_conv_w_m2": self.q_conv,
            "q_evap_w_m2": self.q_evap,
            "q_lw_w_m2": self.q_lw,
            "h_conv_w_m2k": self.h_conv,
        }


def makiranta_reference_icing_rate(
    t_air_c: float,
    wind_speed: float,
    t_water_c: float = 1.0,
    salinity: float = 33.0,
    rel_humidity: float = 0.85,
    char_length: float = 0.75,
    spray_flux: float = 2.06e-3,
    view_factor_to_sky: float = 0.5,
    cloud_fraction: float = 0.0,
) -> ReferenceIcingRate:
    """Closed-form Mákiranta-type freezing rate at a calibration point.

    Parameters mirror the main model so that the *same* physical situation can
    be evaluated by two independent code paths.  ``spray_flux`` defaults to the
    value the empirical spray sub-model returns for ``U = 10 m/s`` on a fully
    exposed vertical panel (2.06e-3 kg m^-2 s^-1), so the calibration point is
    well defined.
    """
    t_air = t_air_c + 273.15
    water = WaterState(t_water=t_water_c + 273.15, salinity=salinity)
    t_f = water.t_freezing
    c_w = water.specific_heat
    l_f = latent_heat_fusion(t_f)

    # --- ISO 12494 style flat-plate forced convection, air properties ------
    nu = air_kinematic_viscosity(t_air)
    k_air = air_conductivity(t_air)
    rho_a = air_density(t_air)
    pr = 0.71
    re = max(wind_speed, 1e-6) * char_length / nu
    # local turbulent flat-plate Nusselt number (ISO 12494 / Incropera eq. 7.38)
    nu_nusselt = 0.0296 * re ** 0.8 * pr ** (1.0 / 3.0)
    h_conv = nu_nusselt * k_air / char_length

    q_conv = h_conv * (t_f - t_air)  # no free-convection term (forced regime)
    # Colburn analogy with Le = 1 (conservative, no Lewis correction)
    h_mass = h_conv / (rho_a * CP_AIR)
    e_s = sat_vapour_pressure(t_f, over_ice=False)
    e_a = rel_humidity * sat_vapour_pressure(t_air, over_ice=False)
    # mixing ratio [kg/kg] instead of vapour density [kg/m^3]
    q_s = 0.622 * e_s / (101325.0 - e_s)
    q_a = 0.622 * e_a / (101325.0 - e_a)
    q_evap = h_mass * rho_a * (q_s - q_a) * latent_heat_vaporisation(t_f)

    sky = 0.0552 * t_air ** 1.5
    sky = sky * (1.0 - cloud_fraction) + t_air * cloud_fraction
    t_horizon = water.t_water
    q_lw = EMISSIVITY_ICE * SIGMA_SB * (
        view_factor_to_sky * (t_f ** 4 - sky ** 4)
        + (1.0 - view_factor_to_sky) * (t_f ** 4 - t_horizon ** 4))

    q_sink = q_conv + q_evap + q_lw
    q_spray = spray_flux * c_w * max(water.t_water - t_f, 0.0)
    need = spray_flux * l_f
    if spray_flux <= 0.0:
        return ReferenceIcingRate(0.0, 0.0, 0.0, "dry_nospray", q_sink,
                                  q_conv, q_evap, q_lw, h_conv)
    lam = (q_sink - q_spray) / need
    if lam >= 1.0:
        regime = "dry"
        lam_c = 1.0
    else:
        regime = "wet"
        lam_c = max(lam, 0.0)
    ice_flux = lam_c * spray_flux
    return ReferenceIcingRate(
        ice_mass_flux=ice_flux,
        ice_thickness_rate=ice_flux / 917.0,
        freezing_fraction=lam_c,
        regime=regime,
        q_sink=q_sink,
        q_conv=q_conv,
        q_evap=q_evap,
        q_lw=q_lw,
        h_conv=h_conv,
    )


def layered_gm_loss(
    panels: Sequence,
    panel_mass: Dict[str, float],
    hull: BargeHull,
    kg_light: float,
    n_layers: int = 4,
) -> tuple[IceLoad, LoadingCondition]:
    """Lumped layered-superposition analytic ice load and GM loss.

    Panels are grouped by surface class and binned into ``n_layers`` equal
    height bands; each band carries the summed mass of its panels at the
    **area-weighted** centroid height (not the mass-weighted one, so the binning
    error is a genuine O(dz^2) superposition error).
    """
    by_zone: Dict[str, List] = {}
    for p in panels:
        by_zone.setdefault(p.zone, []).append(p)

    total_mass = 0.0
    moment_z = 0.0
    moment_y = 0.0
    for zone, plist in by_zone.items():
        zs = [p.centroid[2] for p in plist]
        z_min, z_max = min(zs), max(zs)
        span = max(z_max - z_min, 1e-9)
        bins: List[List] = [[] for _ in range(max(n_layers, 1))]
        for p in plist:
            idx = min(int((p.centroid[2] - z_min) / span * n_layers), n_layers - 1)
            bins[idx].append(p)
        for b in bins:
            if not b:
                continue
            m_bin = sum(panel_mass.get(p.name, 0.0) for p in b)
            a_bin = sum(p.area for p in b)
            if a_bin <= 0.0:
                continue
            z_bin = sum(p.centroid[2] * p.area for p in b) / a_bin
            y_bin = sum(p.centroid[1] * p.area for p in b) / a_bin
            total_mass += m_bin
            moment_z += m_bin * z_bin
            moment_y += m_bin * y_bin

    ice = IceLoad(mass=total_mass, moment_z=moment_z, moment_y=moment_y)
    cond = LoadingCondition(hull=hull, kg_light=kg_light, ice=ice)
    return ice, cond