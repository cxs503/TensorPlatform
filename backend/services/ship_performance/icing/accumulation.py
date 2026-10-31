"""Time integration of panel ice accretion (quasi-steady heat balance).

Each step:
1. evaluate the panel heat balance (regime, freezing fraction, ice mass flux),
   including the conduction resistance of the ice already accumulated;
2. add ``dm = m_dot_ice * A * dt`` to the panel;
3. update the ice centroid: the layer grows outward along the surface normal,
   so a vertical panel's ice centroid stays at the panel height while a
   horizontal (deck) panel's ice centroid rises by ``t/2``.

The integration is explicit in time but the surface energy balance itself is
solved implicitly (root find) at every step, so the scheme is stable for the
minute-scale steps used here (the thermal time constant of a few-mm ice layer
is ~10 s, far below ``dt``).
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Dict, List, Sequence

from .heat_balance import (
    PanelHeatBalance,
    panel_heat_balance,
    transfer_coefficients,
)
from .mesh import Panel
from .properties import RHO_ICE, AirState, WaterState, latent_heat_fusion
from .spray import SprayCondition


@dataclass
class PanelIceState:
    panel: Panel
    mass: float = 0.0            # kg
    thickness: float = 0.0       # m (normal growth)
    regime: str = ""
    surface_temp: float = 273.15
    freezing_fraction: float = 0.0
    ice_flux: float = 0.0        # kg m^-2 s^-1
    spray_flux: float = 0.0
    last_balance: PanelHeatBalance | None = None

    @property
    def centroid(self) -> tuple[float, float, float]:
        """Ice-layer centroid: panel centroid offset by t/2 along the normal."""
        cx, cy, cz = self.panel.centroid
        nx, ny, nz = self.panel.normal
        half = 0.5 * self.thickness
        return (cx + nx * half, cy + ny * half, cz + nz * half)


@dataclass
class AccretionHistory:
    time_s: List[float] = field(default_factory=list)
    total_mass: List[float] = field(default_factory=list)
    total_moment_z: List[float] = field(default_factory=list)
    total_moment_y: List[float] = field(default_factory=list)
    runoff_mass: List[float] = field(default_factory=list)
    freezing_fraction_windward: List[float] = field(default_factory=list)

    def record(self, t: float, states: Sequence[PanelIceState], runoff: float,
               lambda_windward: float) -> None:
        self.time_s.append(t)
        self.total_mass.append(sum(s.mass for s in states))
        self.total_moment_z.append(sum(s.mass * s.centroid[2] for s in states))
        self.total_moment_y.append(sum(s.mass * s.centroid[1] for s in states))
        self.runoff_mass.append(runoff)
        self.freezing_fraction_windward.append(lambda_windward)

    def as_dict(self) -> dict:
        return {
            "time_s": self.time_s,
            "total_mass_kg": self.total_mass,
            "moment_z_kgm": self.total_moment_z,
            "moment_y_kgm": self.total_moment_y,
            "runoff_mass_kg": self.runoff_mass,
            "freezing_fraction_windward": self.freezing_fraction_windward,
        }


def _effective_available_flux(
    air: AirState,
    water: WaterState,
    coeffs,
    thickness: float,
    k_ice: float = 2.2,
    view_factor_to_sky: float = 0.5,
) -> tuple[float, float]:
    """Heat sink available at the *film front*, accounting for ice conduction.

    With an ice layer of thickness ``t`` the temperature at the outer surface is
    below the film temperature, so the sink is reduced.  Solve

        q_loss(T_s) = k_ice (T_f - T_s) / t

    for ``T_s`` and return ``(flux, T_s)``.  ``t -> 0`` recovers ``T_s = T_f``.
    """
    from .heat_balance import surface_heat_loss, solve_equilibrium_surface_temperature

    t_f = water.t_freezing
    if thickness <= 1e-9:
        loss = surface_heat_loss(t_f, air, coeffs, over_ice=False,
                                 horizon_temp=water.t_water,
                                 view_factor_to_sky=view_factor_to_sky)
        return loss["total"], t_f

    def g(t_s: float) -> float:
        loss = surface_heat_loss(t_s, air, coeffs, over_ice=False,
                                 horizon_temp=water.t_water,
                                 view_factor_to_sky=view_factor_to_sky)
        return loss["total"] - k_ice * (t_f - t_s) / thickness

    lo, hi = air.t_air - 60.0, t_f
    g_lo, g_hi = g(lo), g(hi)
    if g_lo * g_hi > 0.0:
        # conduction-limited: cannot freeze at all
        return 0.0, lo if abs(g_lo) < abs(g_hi) else hi
    for _ in range(120):
        mid = 0.5 * (lo + hi)
        g_mid = g(mid)
        if abs(g_mid) < 1e-10 or (hi - lo) < 1e-11:
            return max(k_ice * (t_f - mid) / thickness, 0.0), mid
        if g_mid * g_lo > 0.0:
            lo, g_lo = mid, g_mid
        else:
            hi, g_hi = mid, g_mid
    return 0.0, 0.5 * (lo + hi)


def step_icing(
    states: List[PanelIceState],
    air: AirState,
    water: WaterState,
    spray: SprayCondition,
    dt: float,
    view_factor_to_sky: float = 0.5,
    k_ice: float = 2.2,
) -> dict:
    """Advance every panel by ``dt`` seconds; returns step diagnostics."""
    runoff = 0.0
    frozen = 0.0
    lambda_windward = 0.0
    windward_area = 0.0
    for state in states:
        coeffs = transfer_coefficients(
            air, spray.wind_speed, state.panel.char_length,
            orientation=("vertical" if abs(state.panel.normal[2]) < 0.85 else "horizontal_up"),
        )
        m_spray = spray.panel_flux(state.panel.zone, state.panel.normal)
        bal = panel_heat_balance(air, water, coeffs, m_spray,
                                 view_factor_to_sky=view_factor_to_sky)
        q_avail, t_s = _effective_available_flux(air, water, coeffs, state.thickness,
                                                k_ice=k_ice,
                                                view_factor_to_sky=view_factor_to_sky)
        l_f = latent_heat_fusion(water.t_freezing)
        c_w = water.specific_heat
        q_spray = m_spray * c_w * max(water.t_water - water.t_freezing, 0.0)
        if m_spray <= 0.0:
            regime = "dry_nospray"
            ice_flux = 0.0
            lam = 0.0
        elif q_avail >= m_spray * l_f + q_spray:
            regime = "dry"
            ice_flux = m_spray
            lam = 1.0
        else:
            regime = "wet"
            lam = max(min((q_avail - q_spray) / max(m_spray * l_f, 1e-30), 1.0), 0.0)
            ice_flux = lam * m_spray
        # keep the closed-form flux terms of the diagnostic balance consistent
        bal.regime = regime
        bal.freezing_fraction = lam
        bal.ice_mass_flux = ice_flux
        bal.runoff_flux = max(m_spray - ice_flux, 0.0)
        bal.surface_temp = t_s
        state.last_balance = bal
        state.regime = regime
        state.surface_temp = t_s
        state.freezing_fraction = lam
        state.ice_flux = ice_flux
        state.spray_flux = m_spray

        dm = ice_flux * state.panel.area * dt
        state.mass += dm
        state.thickness = state.mass / (RHO_ICE * state.panel.area)
        frozen += dm
        runoff += bal.runoff_flux * state.panel.area * dt
        if state.panel.zone == "windward":
            lambda_windward += lam * state.panel.area
            windward_area += state.panel.area
    return {
        "frozen_mass_kg": frozen,
        "runoff_mass_kg": runoff,
        "lambda_windward": (lambda_windward / windward_area) if windward_area > 0 else 0.0,
    }


def integrate_icing(
    panels: Sequence[Panel],
    air: AirState,
    water: WaterState,
    spray: SprayCondition,
    duration_s: float,
    dt_s: float = 600.0,
    sample_every: int = 1,
    view_factor_to_sky: float = 0.5,
    k_ice: float = 2.2,
) -> tuple[List[PanelIceState], AccretionHistory]:
    """March the panel population through ``duration_s``."""
    states = [PanelIceState(panel=p) for p in panels]
    hist = AccretionHistory()
    steps = max(int(math.ceil(duration_s / dt_s)), 1)
    dt = duration_s / steps
    hist.record(0.0, states, 0.0, 0.0)
    for k in range(steps):
        diag = step_icing(states, air, water, spray, dt,
                          view_factor_to_sky=view_factor_to_sky, k_ice=k_ice)
        if (k + 1) % max(sample_every, 1) == 0 or k == steps - 1:
            hist.record((k + 1) * dt, states, diag["runoff_mass_kg"],
                        diag["lambda_windward"])
    return states, hist


def panel_ice_table(states: Sequence[PanelIceState]) -> List[Dict[str, float | str]]:
    rows = []
    for s in states:
        cx, cy, cz = s.centroid
        rows.append({
            "name": s.panel.name,
            "zone": s.panel.zone,
            "area_m2": s.panel.area,
            "mass_kg": s.mass,
            "thickness_m": s.thickness,
            "regime": s.regime,
            "freezing_fraction": s.freezing_fraction,
            "surface_temp_c": s.surface_temp - 273.15,
            "ice_flux_kg_m2s": s.ice_flux,
            "spray_flux_kg_m2s": s.spray_flux,
            "centroid_y_m": cy,
            "centroid_z_m": cz,
        })
    return rows