"""Mákiranta/Lozowski-type surface heat balance of a sea-spray wetted panel.

Physics
-------
For a surface element, the instantaneous energy balance reads

    q_conv(T_s) + q_evap/subl(T_s) + q_lw(T_s) = q_sw + q_spray + q_latent

with, per unit area (W m^-2, positive = heat leaving the surface):

q_conv  = h_c (T_s - T_a)                      convective (forced + free)
q_evap  = h_m (rho_v,s - rho_v,a) L_e          evaporation (wet) / sublimation (dry)
q_lw    = eps sigma (T_s^4 - T_sky^4)          long-wave radiation
q_sw    = alpha_s I                            absorbed solar (short-wave)
q_spray = m_w c_w (T_w - T_f)                  sensible heat carried in by the spray
q_lat   = m_f L_f                              latent heat released by freezing

Mass transfer uses the Chilton-Colburn (Lewis) analogy,
``h_m = h_c / (rho_a c_pa Le^(2/3))`` with ``Le = Sc/Pr``.

Two regimes are distinguished *by the sign of the surface heat balance*
(criterion required by the node definition):

* **dry** — at ``T_s = T_f`` the environment can remove more heat than the
  incoming water needs to freeze completely and cool to ``T_s < T_f``; the
  whole spray flux freezes and the ice surface equilibrates at ``T_s < T_f``
  with sublimation over ice (no liquid film).
* **wet**  — the available heat sink is insufficient to freeze all the water;
  the surface stays at ``T_s = T_f`` with a liquid film, only the **freezing
  fraction** ``lambda = q_avail / (m_w L_f)`` freezes, the rest runs off.

References
----------
Lozowski, E.P., Szilder, K., Makkonen, L. (2000) "Computer simulation of
marine icing", Marine Technology 37(3), 155-161.
Mákiranta, R., Lozowski, E.P., George, T. (2013) "Numerical modelling of
sea-water freezing on ship superstructures", Proc. POAC'13.
ISO 12494:2017 — atmospheric icing of structures (forced-convection closure).
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Dict

from .properties import (
    AirState,
    WaterState,
    RHO_ICE,
    SIGMA_SB,
    air_conductivity,
    air_density,
    air_kinematic_viscosity,
    air_prandtl,
    air_schmidt,
    latent_heat_fusion,
    latent_heat_sublimation,
    latent_heat_vaporisation,
    vapour_density,
)

# Surface emissivity of ice / snow-ice (ice 0.97, ISO 12494 suggests 0.98)
EMISSIVITY_ICE = 0.97
# Surface absorptivity of ice for solar radiation
ABSORPTIVITY_ICE = 0.35


# ---------------------------------------------------------------------------
# Convective / mass-transfer coefficients
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class TransferCoefficients:
    h_forced: float
    h_free: float
    h_conv: float      # combined [W m^-2 K^-1]
    h_mass: float      # [m s^-1]
    reynolds: float
    prandtl: float
    schmidt: float


def transfer_coefficients(
    air: AirState,
    wind_speed: float,
    char_length: float,
    orientation: str = "vertical",
    surface_temp: float | None = None,
) -> TransferCoefficients:
    """Forced + free convective heat and mass transfer coefficients.

    Forced convection: turbulent flat plate, ``Nu = 0.037 Re^0.8 Pr^(1/3)``
    (Incropera & DeWitt, ch. 7).  Free convection: ``Nu = 0.15 Ra^(1/3)`` for a
    vertical plate, ``0.27 Ra^(0.25)`` for a horizontal plate.  The two are
    blended with the Churchill ``(|h_f|^3 + |h_n|^3)^(1/3)`` rule.
    """
    t_s = air.t_air if surface_temp is None else surface_temp
    t_film = 0.5 * (t_s + air.t_air)
    nu = air_kinematic_viscosity(t_film, air.pressure)
    k_air = air_conductivity(t_film)
    pr = air_prandtl(t_film, air.pressure)
    sc = air_schmidt(t_film, air.pressure)
    rho = air_density(t_film, air.pressure)

    wind = max(wind_speed, 0.0)
    re = wind * max(char_length, 1e-6) / nu
    if re <= 0.0:
        nu_forced = 0.0
    elif re < 5e5:                    # laminar branch (Blasius/Colburn)
        nu_forced = 0.664 * math.sqrt(re) * pr ** (1.0 / 3.0)
    else:                             # mixed flow, Incropera eq. 7.41
        nu_forced = (0.037 * re ** 0.8 - 871.0) * pr ** (1.0 / 3.0)
        nu_forced = max(nu_forced, 0.0)
    h_forced = nu_forced * k_air / max(char_length, 1e-6)

    # Free convection (buoyancy driven); sign of dT only sets the direction.
    d_temp = abs(t_s - air.t_air)
    beta = 1.0 / t_film
    alpha = k_air / (rho * 1005.0)
    ra = 9.80665 * beta * max(d_temp, 1e-9) * max(char_length, 1e-6) ** 3 / (nu * alpha)
    if orientation == "vertical":
        nu_free = 0.15 * ra ** (1.0 / 3.0)
    elif orientation == "horizontal_up":
        nu_free = 0.27 * ra ** 0.25          # cold surface facing up: stable-ish
    else:
        nu_free = 0.27 * ra ** 0.25
    h_free = nu_free * k_air / max(char_length, 1e-6)

    h_conv = (abs(h_forced) ** 3 + abs(h_free) ** 3) ** (1.0 / 3.0)
    le = sc / pr
    h_mass = h_conv / (rho * 1005.0 * le ** (2.0 / 3.0))
    return TransferCoefficients(
        h_forced=h_forced,
        h_free=h_free,
        h_conv=h_conv,
        h_mass=h_mass,
        reynolds=re,
        prandtl=pr,
        schmidt=sc,
    )


# ---------------------------------------------------------------------------
# Individual flux terms
# ---------------------------------------------------------------------------
def q_convective(coeffs: TransferCoefficients, surface_temp: float, air_temp: float) -> float:
    return coeffs.h_conv * (surface_temp - air_temp)


def q_mass_exchange(
    coeffs: TransferCoefficients,
    air: AirState,
    surface_temp: float,
    over_ice: bool,
) -> float:
    """Evaporation (liquid film) or sublimation (dry ice) heat flux [W m^-2]."""
    rho_v_s = vapour_density(surface_temp, 1.0, over_ice=over_ice)
    rho_v_a = vapour_density(air.t_air, air.rel_humidity, over_ice=False)
    m_dot = coeffs.h_mass * (rho_v_s - rho_v_a)
    latent = (latent_heat_sublimation(surface_temp) if over_ice
              else latent_heat_vaporisation(surface_temp))
    return m_dot * latent


def q_longwave(
    surface_temp: float,
    sky_temp: float,
    horizon_temp: float | None = None,
    emissivity: float = EMISSIVITY_ICE,
    view_factor_to_sky: float = 0.5,
) -> float:
    """Net long-wave loss [W m^-2] for a surface seeing sky and sea.

    A superstructure panel sees roughly ``view_factor_to_sky`` of cold sky; the
    remainder sees the sea surface, which radiates at the sea temperature
    (``horizon_temp``, defaults to the sky temperature when not supplied — a
    conservative, colder-horizon assumption).
    """
    horizon = sky_temp if horizon_temp is None else horizon_temp
    w = min(max(view_factor_to_sky, 0.0), 1.0)
    sky_part = SIGMA_SB * (surface_temp ** 4 - sky_temp ** 4)
    sea_part = SIGMA_SB * (surface_temp ** 4 - horizon ** 4)
    return emissivity * (w * sky_part + (1.0 - w) * sea_part)


def q_solar(air: AirState) -> float:
    """Absorbed short-wave flux [W m^-2] (negative contribution to the loss)."""
    return ABSORPTIVITY_ICE * max(air.solar_irradiance, 0.0)


# ---------------------------------------------------------------------------
# Panel heat balance
# ---------------------------------------------------------------------------
@dataclass
class PanelHeatBalance:
    """Result of the per-panel heat balance."""

    regime: str                     # "dry" | "wet" | "dry_nospray"
    surface_temp: float             # K
    freezing_fraction: float        # lambda in [0, 1]
    ice_mass_flux: float            # kg m^-2 s^-1 frozen mass
    runoff_flux: float              # kg m^-2 s^-1 unfrozen water
    spray_flux: float               # kg m^-2 s^-1 impinging water
    q_conv: float = 0.0
    q_evap: float = 0.0
    q_lw: float = 0.0
    q_sw: float = 0.0
    q_spray: float = 0.0
    q_latent: float = 0.0
    residual: float = 0.0           # |sum of fluxes| [W m^-2]
    coeffs: TransferCoefficients | None = None
    diagnostics: Dict[str, float] = field(default_factory=dict)

    def as_dict(self) -> Dict[str, float | str]:
        return {
            "regime": self.regime,
            "surface_temp_k": self.surface_temp,
            "surface_temp_c": self.surface_temp - 273.15,
            "freezing_fraction": self.freezing_fraction,
            "ice_mass_flux_kg_m2s": self.ice_mass_flux,
            "runoff_flux_kg_m2s": self.runoff_flux,
            "spray_flux_kg_m2s": self.spray_flux,
            "q_conv_w_m2": self.q_conv,
            "q_evap_w_m2": self.q_evap,
            "q_lw_w_m2": self.q_lw,
            "q_sw_w_m2": self.q_sw,
            "q_spray_w_m2": self.q_spray,
            "q_latent_w_m2": self.q_latent,
            "residual_w_m2": self.residual,
        }


def surface_heat_loss(
    surface_temp: float,
    air: AirState,
    coeffs: TransferCoefficients,
    over_ice: bool,
    horizon_temp: float | None = None,
    view_factor_to_sky: float = 0.5,
) -> Dict[str, float]:
    """All environmental flux terms at a given surface temperature.

    Positive values mean heat leaves the surface.
    """
    qc = q_convective(coeffs, surface_temp, air.t_air)
    qe = q_mass_exchange(coeffs, air, surface_temp, over_ice=over_ice)
    ql = q_longwave(surface_temp, air.t_sky, horizon_temp=horizon_temp,
                    view_factor_to_sky=view_factor_to_sky)
    qs = q_solar(air)
    return {"conv": qc, "evap": qe, "lw": ql, "sw": qs, "total": qc + qe + ql - qs}


def solve_equilibrium_surface_temperature(
    air: AirState,
    coeffs: TransferCoefficients,
    source_flux: float = 0.0,
    over_ice: bool = True,
    view_factor_to_sky: float = 0.5,
    horizon_temp: float | None = None,
    t_lower: float | None = None,
    t_upper: float | None = None,
    tol: float = 1e-12,
) -> tuple[float, float]:
    """Solve ``q_loss(T_s) = source_flux`` for the steady surface temperature.

    Uses a bracketed secant/bisection hybrid so that the residual is driven to
    machine precision — this is what the node acceptance criterion
    ``|residual| < 1e-6 W m^-2`` in dry static equilibrium is checked on.

    Returns ``(surface_temperature, residual)`` where
    ``residual = q_loss(T_s) - source_flux`` [W m^-2].
    """
    lower = air.t_air - 60.0 if t_lower is None else t_lower
    upper = 273.15 if t_upper is None else t_upper
    if upper <= lower:
        raise ValueError("invalid bracket for surface temperature")

    def f(t: float) -> float:
        return surface_heat_loss(t, air, coeffs, over_ice, horizon_temp,
                                 view_factor_to_sky)["total"] - source_flux

    f_lo, f_up = f(lower), f(upper)
    if f_lo * f_up > 0.0:
        # No sign change: clamp to the end with the smaller imbalance and report
        # the (small) residual honestly instead of silently returning garbage.
        t_best = lower if abs(f_lo) < abs(f_up) else upper
        return t_best, f(t_best)

    for _ in range(200):
        # Illinois-style regula falsi (keeps the bracket, superlinear).
        width = upper - lower
        t_mid = lower + 0.5 * width
        if abs(f_lo) < abs(f_up):
            t_try = lower + width * f_lo / (f_lo - f_up)
        else:
            t_try = upper - width * f_up / (f_up - f_lo)
        if not (lower < t_try < upper):
            t_try = t_mid
        f_try = f(t_try)
        if abs(f_try) < tol or width < 1e-12:
            return t_try, f_try
        if f_try * f_lo > 0.0:
            lower, f_lo = t_try, f_try
        else:
            upper, f_up = t_try, f_try
    t_best = 0.5 * (lower + upper)
    return t_best, f(t_best)


def panel_heat_balance(
    air: AirState,
    water: WaterState,
    coeffs: TransferCoefficients,
    spray_flux: float,
    view_factor_to_sky: float = 0.5,
) -> PanelHeatBalance:
    """Full per-panel balance: regime classification, freezing rate, surface temp.

    ``spray_flux`` is the impinging sea-water mass flux [kg m^-2 s^-1].
    """
    t_f = water.t_freezing
    c_w = water.specific_heat
    l_f = latent_heat_fusion(t_f)

    # Heat that the environment can remove at the freezing point (liquid film).
    horizon = water.t_water
    loss_at_tf = surface_heat_loss(t_f, air, coeffs, over_ice=False,
                                   horizon_temp=horizon,
                                   view_factor_to_sky=view_factor_to_sky)
    q_available = loss_at_tf["total"]
    q_spray = spray_flux * c_w * max(water.t_water - t_f, 0.0)
    q_freeze_needed = spray_flux * l_f if spray_flux > 0.0 else 0.0

    if spray_flux <= 0.0:
        # No spray: the surface simply equilibrates below freezing (dry ice).
        t_s, residual = solve_equilibrium_surface_temperature(
            air, coeffs, source_flux=0.0, over_ice=True,
            view_factor_to_sky=view_factor_to_sky, horizon_temp=horizon, t_upper=t_f,
        )
        loss = surface_heat_loss(t_s, air, coeffs, True, horizon, view_factor_to_sky)
        return PanelHeatBalance(
            regime="dry_nospray", surface_temp=t_s, freezing_fraction=0.0,
            ice_mass_flux=0.0, runoff_flux=0.0, spray_flux=0.0,
            q_conv=loss["conv"], q_evap=loss["evap"], q_lw=loss["lw"],
            q_sw=loss["sw"], q_spray=0.0, q_latent=0.0, residual=residual,
            coeffs=coeffs,
            diagnostics={"q_available_at_tf": q_available,
                         "q_freeze_need": q_freeze_needed},
        )

    if q_available >= q_freeze_needed + q_spray:
        # DRY: everything freezes; surplus cooling drives T_s below T_f.
        source = spray_flux * (l_f + c_w * max(water.t_water - t_f, 0.0))
        t_s, residual = solve_equilibrium_surface_temperature(
            air, coeffs, source_flux=source, over_ice=True,
            view_factor_to_sky=view_factor_to_sky, horizon_temp=horizon, t_upper=t_f,
        )
        loss = surface_heat_loss(t_s, air, coeffs, True, horizon, view_factor_to_sky)
        return PanelHeatBalance(
            regime="dry", surface_temp=t_s, freezing_fraction=1.0,
            ice_mass_flux=spray_flux, runoff_flux=0.0, spray_flux=spray_flux,
            q_conv=loss["conv"], q_evap=loss["evap"], q_lw=loss["lw"],
            q_sw=loss["sw"], q_spray=q_spray, q_latent=spray_flux * l_f,
            residual=residual, coeffs=coeffs,
            diagnostics={"q_available_at_tf": q_available,
                         "q_freeze_need": q_freeze_needed},
        )

    # WET: film at the freezing point, only the freezing fraction freezes.
    lam = (q_available - q_spray) / max(q_freeze_needed, 1e-30)
    lam = min(max(lam, 0.0), 1.0)
    ice_flux = lam * spray_flux
    return PanelHeatBalance(
        regime="wet", surface_temp=t_f, freezing_fraction=lam,
        ice_mass_flux=ice_flux, runoff_flux=spray_flux - ice_flux,
        spray_flux=spray_flux,
        q_conv=loss_at_tf["conv"], q_evap=loss_at_tf["evap"],
        q_lw=loss_at_tf["lw"], q_sw=loss_at_tf["sw"],
        q_spray=q_spray, q_latent=ice_flux * l_f,
        residual=q_available - ice_flux * l_f - q_spray,
        coeffs=coeffs,
        diagnostics={"q_available_at_tf": q_available,
                     "q_freeze_need": q_freeze_needed},
    )