"""Thermophysical property functions for marine (sea-spray) icing.

All correlations are literature-standard and dimensionally explicit (SI):

* saturation vapour pressure: Buck (1981), separate coefficients over water and
  over ice — the over-ice branch is what makes dry (sublimation) and wet
  (evaporation) regimes physically distinct;
* air density: ideal gas;
* air dynamic viscosity: Sutherland (1936);
* air thermal conductivity: Sutherland-type relation (Incropera, table A.4 fit);
* water-vapour diffusivity: Fuller-type temperature/pressure scaling;
* sea-water freezing point: UNESCO (1978) polynomial in salinity;
* sea-water specific heat / latent heat of fusion: mild temperature dependence.

References
----------
Buck, A.L. (1981) "New equations for computing vapor pressure and enhancement
factor", J. Appl. Meteorol. 20, 1527-1532.
UNESCO (1978) "Eighth report of the joint panel on oceanographic tables and
standards", UNESCO Technical Papers in Marine Science 28.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

# ---------------------------------------------------------------------------
# Constants (SI)
# ---------------------------------------------------------------------------
SIGMA_SB = 5.670374419e-8      # Stefan-Boltzmann [W m^-2 K^-4]
GRAVITY = 9.80665              # [m s^-2]
R_DRY_AIR = 287.05             # [J kg^-1 K^-1]
R_VAPOUR = 461.51              # [J kg^-1 K^-1]
CP_AIR = 1005.0                # [J kg^-1 K^-1]
P_STANDARD = 101325.0          # [Pa]
T0_KELVIN = 273.15
RHO_ICE = 917.0                # pure ice density at -10 C [kg m^-3]
RHO_SEA_WATER = 1025.0         # [kg m^-3]
T_FREEZING_FRESH = 273.15      # [K]


def to_kelvin(t_celsius: float) -> float:
    return t_celsius + T0_KELVIN


def to_celsius(t_kelvin: float) -> float:
    return t_kelvin - T0_KELVIN


# ---------------------------------------------------------------------------
# Humidity
# ---------------------------------------------------------------------------
def sat_vapour_pressure(t_kelvin: float, over_ice: bool = False) -> float:
    """Saturation vapour pressure [Pa] (Buck 1981; T in degrees C internally)."""
    t = to_celsius(t_kelvin)
    if over_ice:
        return 611.15 * math.exp((23.036 - t / 333.7) * (t / (279.82 + t)))
    return 611.21 * math.exp((18.678 - t / 234.5) * (t / (257.14 + t)))


def vapour_density(t_kelvin: float, rel_humidity: float = 1.0,
                   over_ice: bool = False) -> float:
    """Water-vapour partial density [kg m^-3]."""
    e = max(rel_humidity, 0.0) * sat_vapour_pressure(t_kelvin, over_ice=over_ice)
    return e / (R_VAPOUR * t_kelvin)


# ---------------------------------------------------------------------------
# Air properties
# ---------------------------------------------------------------------------
def air_density(t_kelvin: float, pressure: float = P_STANDARD) -> float:
    return pressure / (R_DRY_AIR * t_kelvin)


def air_dynamic_viscosity(t_kelvin: float) -> float:
    """Sutherland law, Pa.s (valid 200-1000 K)."""
    return 1.458e-6 * t_kelvin ** 1.5 / (t_kelvin + 110.4)


def air_kinematic_viscosity(t_kelvin: float, pressure: float = P_STANDARD) -> float:
    return air_dynamic_viscosity(t_kelvin) / air_density(t_kelvin, pressure)


def air_conductivity(t_kelvin: float) -> float:
    """Thermal conductivity of dry air [W m^-1 K^-1] (Sutherland form)."""
    return 2.648e-3 * t_kelvin ** 1.5 / (t_kelvin + 245.4 * 10 ** (-12.0 / t_kelvin))


def air_prandtl(t_kelvin: float, pressure: float = P_STANDARD) -> float:
    mu = air_dynamic_viscosity(t_kelvin)
    k = air_conductivity(t_kelvin)
    return mu * CP_AIR / k


def vapour_diffusivity(t_kelvin: float, pressure: float = P_STANDARD) -> float:
    """Diffusivity of water vapour in air [m^2 s^-1] (~2.2e-5 at 273 K, 1 atm)."""
    return 2.22e-5 * (t_kelvin / 273.15) ** 1.81 * (P_STANDARD / pressure)


def air_schmidt(t_kelvin: float, pressure: float = P_STANDARD) -> float:
    return air_kinematic_viscosity(t_kelvin, pressure) / vapour_diffusivity(t_kelvin, pressure)


# ---------------------------------------------------------------------------
# Sea water / ice
# ---------------------------------------------------------------------------
def seawater_freezing_point(salinity_psu: float) -> float:
    """Freezing point of sea water [K] — UNESCO (1978) polynomial."""
    s = max(salinity_psu, 0.0)
    t_c = (-0.0575 * s
           + 1.710523e-3 * s ** 1.5
           - 2.154996e-4 * s ** 2)
    return T0_KELVIN + t_c


def seawater_specific_heat(t_kelvin: float, salinity_psu: float = 33.0) -> float:
    """Specific heat of sea water [J kg^-1 K^-1] (~3993 at 0 C, S=35)."""
    t = to_celsius(t_kelvin)
    return 4217.4 - 3.720283 * t + 0.1412855 * t ** 2 - 2.654387e-3 * t ** 3 + \
        salinity_psu * (-7.64357 + 0.107276 * t - 1.3839e-3 * t ** 2)


def latent_heat_fusion(t_kelvin: float) -> float:
    """Latent heat of fusion of ice [J kg^-1]: 3.337e5 at 0 C, -2.1e3 per K."""
    return 3.337e5 + 2.1e3 * to_celsius(t_kelvin)


def latent_heat_vaporisation(t_kelvin: float) -> float:
    """Latent heat of vaporisation of water [J kg^-1] (~2.501e6 at 0 C)."""
    return 2.501e6 - 2.37e3 * to_celsius(t_kelvin)


def latent_heat_sublimation(t_kelvin: float) -> float:
    """Latent heat of sublimation of ice [J kg^-1] (2.834e6 at 0 C)."""
    return latent_heat_vaporisation(t_kelvin) + latent_heat_fusion(t_kelvin)


def clear_sky_temperature(t_air_kelvin: float, cloud_fraction: float = 0.0) -> float:
    """Effective sky temperature [K] (Berdahl & Fromberg 1982, clear-sky form).

    ``T_sky = 0.0552 * T_air^1.5`` for a clear sky; cloud cover is blended
    linearly towards the air temperature (overcast sky radiates at T_air).
    """
    clear = 0.0552 * t_air_kelvin ** 1.5
    c = min(max(cloud_fraction, 0.0), 1.0)
    return clear * (1.0 - c) + t_air_kelvin * c


# ---------------------------------------------------------------------------
# Bundle used by the heat-balance kernel
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class AirState:
    """Free-stream atmospheric state seen by a surface panel."""

    t_air: float                 # K
    rel_humidity: float = 0.85   # -
    pressure: float = P_STANDARD # Pa
    t_sky: float | None = None   # K, effective radiative sky temperature
    cloud_fraction: float = 0.0
    solar_irradiance: float = 0.0  # W m^-2 (0 at night)

    def __post_init__(self) -> None:
        if self.t_sky is None:
            object.__setattr__(
                self, "t_sky", clear_sky_temperature(self.t_air, self.cloud_fraction)
            )


@dataclass(frozen=True)
class WaterState:
    """Sea-water state feeding the spray."""

    t_water: float                 # K
    salinity: float = 33.0         # psu

    @property
    def t_freezing(self) -> float:
        return seawater_freezing_point(self.salinity)

    @property
    def specific_heat(self) -> float:
        return seawater_specific_heat(self.t_water, self.salinity)