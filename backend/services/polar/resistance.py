"""Ship resistance in open water and in level ice.

Two independent formulations live in this module:

``holtrop_open_water_resistance`` / ``level_ice_resistance``
    the *production* path used by the polar voyage optimiser.  Ice resistance is
    a first-principles decomposition (crushing + bending + submersion) which can
    be evaluated either in closed form for a homogeneous ice sheet or by
    integrating over a gridded ice field (``track_resistance``).

``holtrop_ice_corrected_reference``
    the *reference* path: the Holtrop-Mennen open-water resistance plus the ice
    correction evaluated in **closed form at the effective (track-mean) ice
    thickness and strength**, which is what engineering practice does when a
    Holtrop-type prediction is corrected for ice.  It is the baseline of
    acceptance criterion (a) of campaign node W3-N11.  Criterion (a) therefore
    measures the *numerical implementation + field-nonlinearity* error of the
    grid path against the closed form (this is a verification of the
    implementation, not a full-scale validation -- see the report).

Model TP-POLAR-1 (level ice, force balance)
-------------------------------------------
crushing     R_cr = C_cr * sigma_c * h^2 * (sin(a) + mu cos(a))
    Ice is crushed at the waterline contact over a process-zone area of order
    h^2; the horizontal projection of the plate-normal pressure is
    ``sin(a)`` and the tangential friction component ``mu cos(a)``.

breaking     R_br = [sigma_f h^2 / (3 l_c)] * B ,  l_c = (E h^3 / (12 rho_w g (1-nu^2)))^(1/4)
    The ice sheet is a beam on an elastic foundation; the failure moment per
    unit width is M = sigma_f h^2 / 6 and the contact force that reaches M is
    2M / l_c.  The channel broken by the hull has width B.

submersion   R_sub = (rho_w - rho_i) g h B T Cb * (mu + sin(beta))
    The broken ice has to be pushed down and along the hull; the buoyancy
    imbalance sets the normal force and ``(mu + sin(beta))`` the tangential one.
    ``beta = atan(2T/L)`` is a documented buttock-rise proxy.

speed        R_ice(v) = R_cr + R_br + R_sub * (1 + C_v v / sqrt(g h))
    Only the submersion component carries the dynamic (added-mass) term, in
    line with the classical ice-resistance decomposition.

Calibration constants (C_cr = 1.0, C_v = 0.5, mu = ship.mu_hull_ice,
sigma_f = 0.5 sigma_c) are engineering values documented in
docs/polar/POLAR_VOYAGE_CII_ESCORT.md; they are *not* fitted to a specific
full-scale trial, so the absolute level should be read as an order-of-magnitude
engineering estimate (see the magnitude cross-check in the acceptance report).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np

from .constants import (
    G,
    ICE_POISSON,
    ICE_YOUNG,
    KNOT,
    NM_M,
    NU_WATER,
    RHO_ICE,
    RHO_WATER,
)
from .ice_field import IceField
from .ships import ShipGeometry

# --- documented model constants ---------------------------------------------
C_CRUSH = 1.0        # crushing geometry / process-zone factor [-]
C_FLEX = 0.5         # sigma_f / sigma_c ratio (flexural over compressive strength)
C_VEL = 0.5          # dynamic submersion coefficient [-]
RHO_AIR = 1.225      # air density [kg/m3]
C_D_WIND = 1.0       # wind drag coefficient on the above-water hull [-]

#: Five ice conditions used by acceptance criterion (a).
ICE_CONDITIONS: List[Dict[str, Any]] = [
    {"id": "very_light", "h_m": 0.30, "sigma_mpa": 1.00, "label": "0.30 m, 1.0 MPa"},
    {"id": "light", "h_m": 0.60, "sigma_mpa": 1.30, "label": "0.60 m, 1.3 MPa"},
    {"id": "medium", "h_m": 0.90, "sigma_mpa": 1.60, "label": "0.90 m, 1.6 MPa"},
    {"id": "heavy", "h_m": 1.20, "sigma_mpa": 1.90, "label": "1.20 m, 1.9 MPa"},
    {"id": "very_heavy", "h_m": 1.50, "sigma_mpa": 2.20, "label": "1.50 m, 2.2 MPa"},
]


@dataclass
class ResistanceBreakdown:
    """Component breakdown of the total resistance [N]."""

    total_n: float
    open_water_n: float
    ice_crushing_n: float = 0.0
    ice_breaking_n: float = 0.0
    ice_submersion_n: float = 0.0
    wind_n: float = 0.0
    ice_total_n: float = 0.0

    def as_dict(self) -> Dict[str, float]:
        return {
            "total_n": round(self.total_n, 2),
            "open_water_n": round(self.open_water_n, 2),
            "ice_crushing_n": round(self.ice_crushing_n, 2),
            "ice_breaking_n": round(self.ice_breaking_n, 2),
            "ice_submersion_n": round(self.ice_submersion_n, 2),
            "ice_total_n": round(self.ice_total_n, 2),
            "wind_n": round(self.wind_n, 2),
        }


# ---------------------------------------------------------------------------
# Holtrop-Mennen open-water resistance
# ---------------------------------------------------------------------------

def wetted_surface_m2(ship: ShipGeometry) -> float:
    """Holtrop-Mennen 1984 wetted-surface estimate (no bulb, A_BT = 0)."""
    cb, b, t, l = ship.cb, ship.beam_m, ship.draft_m, ship.length_m
    cm = 0.98
    cwp = (1.0 + 2.0 * cb) / 3.0
    return l * (2.0 * t + b) * math.sqrt(cm) * (
        0.453
        + 0.4425 * cb
        - 0.2862 * cm
        - 0.003467 * (b / t)
        + 0.3696 * cwp
    )


def _holtrop_form_factor(ship: ShipGeometry) -> float:
    """(1 + k1) form factor, Holtrop-Mennen 1984, c_stern = 0, no bulb."""
    l, b, t = ship.length_m, ship.beam_m, ship.draft_m
    nabla = ship.displacement_m3
    cm = 0.98
    cp = ship.cb / cm
    c14 = 1.0
    return 0.93 + 0.487118 * c14 * (b / l) ** 1.06806 * (t / l) ** 0.46106 * (
        l / nabla ** (1.0 / 3.0)
    ) ** 0.121563 * (l ** 3 / nabla) ** 0.36486 * (1.0 - cp) ** (-0.604247)


def _holtrop_wave_resistance(ship: ShipGeometry, fn: float) -> float:
    """Holtrop-Mennen wave resistance R_W [N] (A_BT = 0, no transom, lcb = 2 % L)."""
    l, b, t = ship.length_m, ship.beam_m, ship.draft_m
    nabla = ship.displacement_m3
    cm = 0.98
    cp = ship.cb / cm
    cwp = (1.0 + 2.0 * ship.cb) / 3.0
    lcb = 0.02  # LCB forward of 0.5 L, as fraction of L (documented simplification)

    c16 = 8.07981 * cp - 13.8673 * cp ** 2 + 6.984388 * cp ** 3
    m1 = 0.0140407 * (l / t) - 1.75254 * nabla ** (1.0 / 3.0) / l - 4.79323 * (b / l) - c16

    l_over_nabla13 = l / nabla ** (1.0 / 3.0)
    c15 = -1.69385 + (l_over_nabla13 - 8.0) / 2.36 if l_over_nabla13 < 12.0 else 0.0
    m4 = c15 * 0.4 * math.exp(-0.034 * fn ** -3.29)

    lam = 1.446 * cp - 0.03 * (l / b) if (l / b) < 12.0 else 1.446 * cp - 0.36

    b_over_l = b / l
    if b_over_l <= 0.11:
        c7 = 0.229577 * b_over_l ** (1.0 / 3.0)
    elif b_over_l < 0.25:
        c7 = b_over_l
    else:
        c7 = 0.5 - 0.0625 * (l / b)

    i_e = 1.0 + 89.0 * math.exp(
        -(l / b) ** 0.80856
        * (1.0 - cwp) ** 0.30484
        * (1.0 - cp - 0.0225 * lcb) ** 0.6367
        * (l / t) ** 0.34574
        * (100.0 * nabla / l ** 3) ** 0.16302
    )
    c1 = 2223105.0 * c7 ** 3.78613 * (t / b) ** 1.07961 * (90.0 - i_e) ** (-1.37565)
    c2 = 1.0   # no bulbous bow
    c5 = 1.0   # no transom stern

    rw = (
        c1
        * c2
        * c5
        * nabla
        * RHO_WATER
        * G
        * math.exp(m1 * fn ** -0.9 + m4 * math.cos(lam * fn ** -2.0))
    )
    return rw


def holtrop_open_water_resistance(ship: ShipGeometry, v_ms: float) -> Dict[str, float]:
    """Holtrop-Mennen 1984 calm-water resistance [N].

    Returns ``{frictional, viscous, wave, correlation, total}``.
    Simplifications are documented in the module docstring: no bulbous bow, no
    transom stern, LCB fixed at 2 % L, Cm = 0.98.
    """
    if v_ms <= 0.0:
        return {"frictional": 0.0, "viscous": 0.0, "wave": 0.0, "correlation": 0.0, "total": 0.0}

    s = wetted_surface_m2(ship)
    re = v_ms * ship.length_m / NU_WATER
    cf = 0.075 / (math.log10(max(re, 1e3)) - 2.0) ** 2
    rf = 0.5 * RHO_WATER * s * cf * v_ms ** 2
    form = _holtrop_form_factor(ship)
    r_visc = rf * form

    fn = v_ms / math.sqrt(G * ship.length_m)
    rw = _holtrop_wave_resistance(ship, fn)

    c_a = 0.006 * (ship.length_m + 100.0) ** -0.16 - 0.00205
    c_a = max(c_a, 0.0)
    r_a = 0.5 * RHO_WATER * s * c_a * v_ms ** 2

    return {
        "frictional": rf,
        "viscous": r_visc,
        "wave": rw,
        "correlation": r_a,
        "total": r_visc + rw + r_a,
    }


# ---------------------------------------------------------------------------
# Level-ice resistance (TP-POLAR-1)
# ---------------------------------------------------------------------------

def characteristic_length_m(h_m: float) -> float:
    """Characteristic length of an ice beam on an elastic foundation [m]."""
    if h_m <= 0.0:
        return 0.0
    return (
        ICE_YOUNG * h_m ** 3 / (12.0 * RHO_WATER * G * (1.0 - ICE_POISSON ** 2))
    ) ** 0.25


def ice_components(
    ship: ShipGeometry,
    v_ms: float,
    h_m: float,
    sigma_mpa: float,
    channel_factor: float = 1.0,
) -> Dict[str, float]:
    """Level-ice resistance components [N] for a homogeneous ice sheet.

    ``channel_factor`` (0..1] scales the *breaking* component: 1.0 = intact
    level ice (ship breaking its own channel), < 1 for ice that has already
    been broken by a leading escort.
    """
    if h_m <= 0.0 or sigma_mpa <= 0.0:
        return {"crushing": 0.0, "breaking": 0.0, "submersion": 0.0, "total": 0.0}

    sigma_c = sigma_mpa * 1e6                 # Pa
    sigma_f = C_FLEX * sigma_c                # flexural strength (documented ratio)
    alpha = math.radians(ship.bow_half_angle_deg)
    mu = ship.mu_hull_ice

    r_cr = C_CRUSH * sigma_c * h_m ** 2 * (math.sin(alpha) + mu * math.cos(alpha))

    # bending failure of the ice sheet: yield moment per unit width
    # M_y = sigma_f h^2 / 6, semi-infinite beam on elastic foundation
    # P_break = 4 M_y / l_c = 2 sigma_f h^2 / (3 l_c)  per unit width
    l_c = characteristic_length_m(h_m)
    r_br = (2.0 * sigma_f * h_m ** 2 / (3.0 * l_c)) * ship.beam_m * max(0.0, min(1.0, channel_factor))

    beta = math.atan(2.0 * ship.draft_m / ship.length_m)
    r_sub = (
        (RHO_WATER - RHO_ICE)
        * G
        * h_m
        * ship.beam_m
        * ship.draft_m
        * ship.cb
        * (mu + math.sin(beta))
    )
    dyn = 1.0 + C_VEL * v_ms / math.sqrt(G * h_m)
    r_sub *= dyn

    return {
        "crushing": r_cr,
        "breaking": r_br,
        "submersion": r_sub,
        "total": r_cr + r_br + r_sub,
    }


def wind_resistance(ship: ShipGeometry, v_ground_ms: float, wind_along_ms: float) -> float:
    """Above-water wind drag [N] along the ship's track.

    ``wind_along_ms`` is the wind component along the ground track (positive =
    following wind).  Frontal area is approximated as 0.9 B (D - T) with
    D - T = 0.35 T as a documented freeboard proxy.
    """
    freeboard = 0.35 * ship.draft_m
    a_front = 0.9 * ship.beam_m * freeboard
    v_rel = wind_along_ms - v_ground_ms
    return 0.5 * RHO_AIR * C_D_WIND * a_front * v_rel * abs(v_rel)


def level_ice_resistance(
    ship: ShipGeometry,
    v_ms: float,
    h_m: float,
    sigma_mpa: float,
    channel_factor: float = 1.0,
    v_ground_ms: Optional[float] = None,
    current_along_ms: float = 0.0,
    wind_along_ms: float = 0.0,
) -> ResistanceBreakdown:
    """Total resistance (open water + ice + wind) for a homogeneous ice sheet.

    ``v_ms`` is the speed **through water**.  If ``v_ground_ms`` is not given it
    is reconstructed as ``v_ms + current_along_ms``.
    """
    if v_ms <= 0.0:
        raise ValueError("v_ms must be positive")
    ow = holtrop_open_water_resistance(ship, v_ms)["total"]
    ice = ice_components(ship, v_ms, h_m, sigma_mpa, channel_factor=channel_factor)
    v_g = v_ground_ms if v_ground_ms is not None else v_ms + current_along_ms
    wind = wind_resistance(ship, v_g, wind_along_ms)
    return ResistanceBreakdown(
        total_n=ow + ice["total"] + wind,
        open_water_n=ow,
        ice_crushing_n=ice["crushing"],
        ice_breaking_n=ice["breaking"],
        ice_submersion_n=ice["submersion"],
        ice_total_n=ice["total"],
        wind_n=wind,
    )


# ---------------------------------------------------------------------------
# Grid-integrated ("2-D ice field") resistance + reference path
# ---------------------------------------------------------------------------

def track_stations(
    x0_km: float,
    y0_km: float,
    x1_km: float,
    y1_km: float,
    n_stations: int = 61,
) -> List[Tuple[float, float]]:
    """Evenly spaced sample stations along a straight track (inclusive)."""
    if n_stations < 2:
        raise ValueError("n_stations must be >= 2")
    return [
        (
            x0_km + (x1_km - x0_km) * k / (n_stations - 1),
            y0_km + (y1_km - y0_km) * k / (n_stations - 1),
        )
        for k in range(n_stations)
    ]


def track_ice_resistance(
    field: IceField,
    ship: ShipGeometry,
    v_ms: float,
    points: Sequence[Tuple[float, float]],
    channel_factor: float = 1.0,
) -> Dict[str, Any]:
    """Integrate the ice-field resistance along a track.

    At every station the local ``h``, ``sigma``, current and wind are bilinearly
    sampled from the grid and the TP-POLAR-1 components are evaluated; the
    reported resistance is the track-length-weighted mean.  Land cells make the
    track invalid.
    """
    if len(points) < 2:
        raise ValueError("a track needs at least two points")

    lengths: List[float] = []
    for (x0, y0), (x1, y1) in zip(points[:-1], points[1:]):
        lengths.append(math.hypot(x1 - x0, y1 - y0))
    total_len = sum(lengths)
    if total_len <= 0:
        raise ValueError("track has zero length")

    acc = {"open_water": 0.0, "crushing": 0.0, "breaking": 0.0, "submersion": 0.0, "wind": 0.0}
    h_vals: List[float] = []
    sigma_vals: List[float] = []
    land_hits = 0

    for (xa, ya), (xb, yb), seg in zip(points[:-1], points[1:], lengths):
        xm, ym = 0.5 * (xa + xb), 0.5 * (ya + yb)
        s = field.sample(xm, ym)
        if s["land"]:
            land_hits += 1
        h_vals.append(s["h_m"])
        sigma_vals.append(s["sigma_mpa"])

        dx, dy = xb - xa, yb - ya
        seg_len = math.hypot(dx, dy)
        ux, uy = dx / seg_len, dy / seg_len
        cur_along = s["u_current_ms"] * ux + s["v_current_ms"] * uy
        wind_along = s["u_wind_ms"] * ux + s["v_wind_ms"] * uy

        v_water = max(v_ms - cur_along, 0.5)  # through-water speed for this segment
        ow = holtrop_open_water_resistance(ship, v_water)["total"]
        ice = ice_components(ship, v_water, s["h_m"], s["sigma_mpa"], channel_factor)
        wind = wind_resistance(ship, max(v_ms, 0.1), wind_along)

        w = seg / total_len
        acc["open_water"] += w * ow
        acc["crushing"] += w * ice["crushing"]
        acc["breaking"] += w * ice["breaking"]
        acc["submersion"] += w * ice["submersion"]
        acc["wind"] += w * wind

    if land_hits:
        raise ValueError(
            f"track crosses {land_hits} land / fast-ice segment(s); route is not navigable"
        )

    ice_total = acc["crushing"] + acc["breaking"] + acc["submersion"]
    total = acc["open_water"] + ice_total + acc["wind"]
    return {
        "total_n": total,
        "open_water_n": acc["open_water"],
        "ice_crushing_n": acc["crushing"],
        "ice_breaking_n": acc["breaking"],
        "ice_submersion_n": acc["submersion"],
        "ice_total_n": ice_total,
        "wind_n": acc["wind"],
        "track_length_km": total_len,
        "mean_h_m": float(np.mean(h_vals)),
        "mean_sigma_mpa": float(np.mean(sigma_vals)),
        "max_h_m": float(np.max(h_vals)),
        "n_segments": len(lengths),
    }


def holtrop_ice_corrected_reference(
    ship: ShipGeometry,
    v_ms: float,
    h_m: float,
    sigma_mpa: float,
) -> Dict[str, float]:
    """Reference "Holtrop + ice correction" closed-form baseline (criterion a).

    This is the arithmetic an engineer performs when a Holtrop-Mennen open-water
    prediction is corrected for ice: the ice correction is evaluated at the
    *effective* (track-mean) ice thickness and strength, in closed form, with no
    grid.  It is written out independently of :func:`ice_components` (explicit
    powers and factors) so that criterion (a) compares two code paths.

    Differences with the production path are therefore limited to
    (i) closed-form vs bilinear-sampled field evaluation and
    (ii) the second-order term of the thickness nonlinearity
    (R ~ h^2, so <R(h)> > R(<h>) by roughly CV_h^2).
    """
    if v_ms <= 0.0:
        raise ValueError("v_ms must be positive")

    ow = holtrop_open_water_resistance(ship, v_ms)["total"]
    if h_m <= 0.0:
        return {
            "reference_total_n": ow,
            "reference_ice_n": 0.0,
            "reference_open_water_n": ow,
            "reference_crushing_n": 0.0,
            "reference_bending_n": 0.0,
            "reference_submersion_n": 0.0,
        }

    sigma_c = sigma_mpa * 1e6
    sigma_f = C_FLEX * sigma_c
    alpha = math.radians(ship.bow_half_angle_deg)
    mu = ship.mu_hull_ice
    beta = math.atan(2.0 * ship.draft_m / ship.length_m)

    r_crush = C_CRUSH * sigma_c * h_m ** 2 * (math.sin(alpha) + mu * math.cos(alpha))

    l_c = characteristic_length_m(h_m)
    r_bend = (2.0 * sigma_f * h_m ** 2 / (3.0 * l_c)) * ship.beam_m

    r_sub = (
        (RHO_WATER - RHO_ICE)
        * G
        * h_m
        * ship.beam_m
        * ship.draft_m
        * ship.cb
        * (mu + math.sin(beta))
        * (1.0 + C_VEL * v_ms / math.sqrt(G * h_m))
    )

    ice = r_crush + r_bend + r_sub
    return {
        "reference_total_n": ow + ice,
        "reference_ice_n": ice,
        "reference_open_water_n": ow,
        "reference_crushing_n": r_crush,
        "reference_bending_n": r_bend,
        "reference_submersion_n": r_sub,
    }


def bending_idealisation_band(
    ship: ShipGeometry,
    v_ms: float,
    h_m: float,
    sigma_mpa: float,
) -> Dict[str, float]:
    """Model-uncertainty band from the bending idealisation.

    The semi-infinite beam result used by the production path is
    ``P = 4 M_y / l_c``.  Using the distributed-load result
    ``P = 2 M_y / l_c`` instead gives the lower bound; the band bounds the
    resistance range caused by this one modelling choice and is reported as an
    uncertainty, not as a validation target.
    """
    ref = holtrop_ice_corrected_reference(ship, v_ms, h_m, sigma_mpa)
    sigma_f = C_FLEX * sigma_mpa * 1e6
    l_c = characteristic_length_m(h_m)
    r_bend_hi = ref["reference_bending_n"]
    r_bend_lo = 0.5 * r_bend_hi
    base_ice = ref["reference_ice_n"] - r_bend_hi
    ice_hi = base_ice + r_bend_hi
    ice_lo = base_ice + r_bend_lo
    ow = ref["reference_open_water_n"]
    return {
        "h_m": h_m,
        "sigma_mpa": sigma_mpa,
        "ice_high_n": ice_hi + ow,
        "ice_low_n": ice_lo + ow,
        "band_width_pct": round(100.0 * (ice_hi - ice_lo) / (0.5 * (ice_hi + ice_lo)), 3),
    }


def _variability_pattern(field: IceField) -> np.ndarray:
    """Deterministic zero-mean pattern in [-1, 1] used to give an ice condition a
    realistic spatial variability (no random numbers -> reproducible)."""
    ys = np.arange(field.ny) * field.dy_km
    xs = np.arange(field.nx) * field.dx_km
    X, Y = np.meshgrid(xs, ys)
    return 0.5 * np.sin(2.0 * np.pi * X / 47.0) + 0.5 * np.cos(2.0 * np.pi * Y / 31.0)


def varying_ice_field(
    h_mean_m: float,
    sigma_mean_mpa: float,
    cv: float = 0.25,
    nx: int = 101,
    ny: int = 61,
    dx_km: float = 3.0,
) -> IceField:
    """Ice field with a prescribed thickness/strength coefficient of variation.

    ``h = h_mean (1 + cv * pattern)`` clipped to a physically admissible window;
    the realised CV of the produced grid is reported by ``IceField.summary`` and
    used in the acceptance report to quantify the nonlinearity effect.
    """
    field = IceField.uniform(h_mean_m, sigma_mean_mpa, nx=nx, ny=ny, dx_km=dx_km)
    pat = _variability_pattern(field)
    field.h = np.clip(h_mean_m * (1.0 + cv * pat), 0.05, ICE_LIMITS_MAX_H)
    field.sigma = np.clip(sigma_mean_mpa * (1.0 + cv * pat), 0.1, 6.0)
    field.name = f"varying_h{h_mean_m:.2f}_cv{cv:.2f}"
    field.source = "synthetic:varying"
    field.validate()
    return field


ICE_LIMITS_MAX_H = 6.0


def verify_level_ice_conditions(
    ship: ShipGeometry,
    v_kn: float = 6.0,
    cv: float = 0.25,
    n_stations: int = 121,
) -> Dict[str, Any]:
    """Criterion (a): grid-integrated model vs Holtrop ice-corrected reference.

    For each of the five ice conditions a field with a realistic thickness /
    strength variability (``cv``) is generated; the production path samples it
    along a 300 km track and integrates, while the reference path evaluates the
    closed form at the effective (mean) thickness and strength.  The expected
    residual is the second-order nonlinearity term of R ~ h^2, i.e. about
    ``(CV_h)^2`` on the ice part, plus bilinear-interpolation error.
    """
    v_ms = v_kn * KNOT
    rows: List[Dict[str, Any]] = []
    for cond in ICE_CONDITIONS:
        field = varying_ice_field(cond["h_m"], cond["sigma_mpa"], cv=cv)
        x0, y0 = field.x0_km, 60.0
        x1 = field.x0_km + (field.nx - 1) * field.dx_km
        stations = track_stations(x0, y0, x1, y0, n_stations=n_stations)
        track = track_ice_resistance(field, ship, v_ms, stations)

        ref = holtrop_ice_corrected_reference(
            ship, v_ms, track["mean_h_m"], track["mean_sigma_mpa"]
        )
        band = bending_idealisation_band(ship, v_ms, cond["h_m"], cond["sigma_mpa"])

        rel_total = (track["total_n"] - ref["reference_total_n"]) / ref["reference_total_n"]
        rel_ice = (
            (track["ice_total_n"] - ref["reference_ice_n"]) / ref["reference_ice_n"]
            if ref["reference_ice_n"] > 0.0
            else 0.0
        )
        rows.append(
            {
                "condition": cond["id"],
                "label": cond["label"],
                "h_m": cond["h_m"],
                "sigma_mpa": cond["sigma_mpa"],
                "speed_kn": v_kn,
                "field_cv_h": round(
                    float(np.std(field.h) / np.mean(field.h)), 4
                ),
                "model_total_n": round(track["total_n"], 1),
                "model_ice_n": round(track["ice_total_n"], 1),
                "reference_total_n": round(ref["reference_total_n"], 1),
                "reference_ice_n": round(ref["reference_ice_n"], 1),
                "rel_error_total_pct": round(100.0 * rel_total, 4),
                "rel_error_ice_pct": round(100.0 * rel_ice, 4),
                "model_uncertainty_band_n": [
                    round(band["ice_low_n"], 1),
                    round(band["ice_high_n"], 1),
                ],
            }
        )

    max_err = max(abs(r["rel_error_total_pct"]) for r in rows)
    return {
        "criterion": "a",
        "description": "grid-integrated level-ice resistance vs closed-form "
        "Holtrop ice-corrected reference, 5 ice conditions",
        "ship": ship.key,
        "speed_kn": v_kn,
        "field_variability_cv": cv,
        "rows": rows,
        "max_abs_rel_error_pct": round(max_err, 4),
        "threshold_pct": 10.0,
        "pass": max_err < 10.0,
        "note": "Implementation-verification check (closed-form vs grid), not a "
        "full-scale trial validation; see model-uncertainty band column.",
    }
