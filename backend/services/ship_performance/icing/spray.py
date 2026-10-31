"""Empirical sea-spray water flux model (wind / wave driven).

The impinging sea-water mass flux on an exposed surface is modelled as

    m_w = E_zone * W_eff(U10, Hs) * U10 * (n . u_wind)          [kg m^-2 s^-1]

with

* ``Hs`` — significant wave height of a fully developed sea
  (Pierson-Moskowitz: ``Hs = 0.21 U10^2 / g``);
* ``W_eff`` — *effective* spray water content [kg m^-3], i.e. the airborne
  liquid water content already reduced by the droplet collection efficiency of
  the obstacle: ``W_eff = W_ref sqrt(Hs/H_ref)``, ``W_ref = 2.0e-4 kg m^-3`` at
  ``H_ref = 2 m``.  The square-root scaling makes the spray flux grow ~ U10^2
  between the light-air and storm limits, which is the range spanned by the
  spray-flux parameterisations of Zakrzewski (1987) and Lozowski et al. (2000)
  once collection efficiency is folded in;
* ``E_zone`` — exposure/occlusion factor per surface class (windward face 1.0,
  side face 0.75, leeward face 0.15, up-facing horizontal 0.45,
  down-facing horizontal 0.05).

The windward projection ``(n . u_wind)_+`` removes spray from leeward and
downward-facing surfaces.  ``W_ref`` is an explicit, documented calibration
knob: it is the single free parameter of the spray sub-model (the thermal
sub-model has none).  Its value is fixed once, in ``CALIBRATION``, and all
downstream results are reported against that disclosure.

References
----------
Zakrzewski, W.P. (1987) "Splashing a ship with collision-generated spray",
Cold Regions Science and Technology 14, 65-83.
Lozowski, E.P., Szilder, K., Makkonen, L. (2000) Marine Technology 37(3).
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Tuple

GRAVITY = 9.80665

#: Documented calibration of the spray sub-model (see module docstring).
CALIBRATION = {
    "w_ref_kg_m3": 2.0e-4,   # effective spray water content at Hs = H_ref
    "h_ref_m": 2.0,
    "pm_coefficient": 0.21,  # Pierson-Moskowitz fully developed sea
}

#: Exposure (occlusion) factor per surface class.
ZONE_EXPOSURE = {
    "windward": 1.0,
    "side": 0.75,
    "leeward": 0.15,
    "horizontal_up": 0.45,
    "horizontal_down": 0.05,
}


def significant_wave_height(wind_speed: float) -> float:
    """Fully developed sea significant wave height [m] (Pierson-Moskowitz)."""
    u = max(wind_speed, 0.0)
    return CALIBRATION["pm_coefficient"] * u * u / GRAVITY


def effective_spray_content(wind_speed: float) -> float:
    """Effective spray water content [kg m^-3] (includes collection efficiency)."""
    hs = significant_wave_height(wind_speed)
    ratio = max(hs, 0.0) / CALIBRATION["h_ref_m"]
    return CALIBRATION["w_ref_kg_m3"] * math.sqrt(max(ratio, 0.0))


def spray_mass_flux(
    wind_speed: float,
    zone: str,
    normal: Tuple[float, float, float],
    wind_dir: Tuple[float, float, float] = (0.0, -1.0, 0.0),
) -> float:
    """Impinging sea-water mass flux [kg m^-2 s^-1] on a panel.

    Parameters
    ----------
    wind_speed : free-stream wind speed [m/s]
    zone : surface class key of :data:`ZONE_EXPOSURE`
    normal : outward surface normal (unit-ish vector)
    wind_dir : unit vector of the direction the wind blows *towards* (a
        surface pointing into the wind, ``n . u_wind < 0``, collects spray)
    """
    if wind_speed <= 0.0:
        return 0.0
    exposure = ZONE_EXPOSURE.get(zone, 0.5)
    nx, ny, nz = normal
    nmag = math.sqrt(nx * nx + ny * ny + nz * nz) or 1.0
    wx, wy, wz = wind_dir
    wmag = math.sqrt(wx * wx + wy * wy + wz * wz) or 1.0
    # ``wind_dir`` is the direction the wind blows *towards*; a surface collects
    # spray when its outward normal points *into* the wind, i.e. -n.u > 0.
    proj = -(nx * wx + ny * wy + nz * wz) / (nmag * wmag)
    # Horizontal up-facing surfaces also collect falling spray / splash even
    # though n.u ~ 0; the exposure factor already encodes that, so only a
    # strict negative projection (fully sheltered) kills the flux.
    if proj < -0.1:
        return 0.0
    proj_eff = max(proj, 0.0) if abs(nz) < 0.9 else 1.0
    return exposure * effective_spray_content(wind_speed) * wind_speed * proj_eff


@dataclass(frozen=True)
class SprayCondition:
    wind_speed: float
    wind_dir: Tuple[float, float, float] = (0.0, -1.0, 0.0)

    @property
    def hs(self) -> float:
        return significant_wave_height(self.wind_speed)

    def panel_flux(self, zone: str, normal: Tuple[float, float, float]) -> float:
        return spray_mass_flux(self.wind_speed, zone, normal, self.wind_dir)