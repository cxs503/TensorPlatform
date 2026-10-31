"""Environmental loads: wind, current and level ice.

All loads are returned in the **ship-fixed frame** (``X`` forward, ``Y`` to
starboard, ``N`` yaw moment positive bow-to-starboard) in SI units.

Wind / current
--------------
Two independent formulations are implemented for each field:

``ocimf`` (primary, used by the simulators)
    OCIMF-style non-dimensional coefficient curves ``C_X``, ``C_Y``, ``C_N``
    stored as anchor points in :mod:`data` and least-squares fitted by a
    3-term Fourier series at import time.  Because the fit basis is even/odd in
    ``psi`` the physical symmetry invariants hold exactly
    (``C_X(psi)=C_X(-psi)``, ``C_Y(-psi)=-C_Y(psi)``).

``projected`` / ``reference`` (validation reference, *not* used by the
simulation)
    Textbook projected-area formulation ``F = 0.5 rho V^2 C_D A`` with fixed
    drag coefficients plus (current) an ITTC-57 skin-friction axial part.  It is
    an independent parameterisation of the same physics and provides the
    reference values of acceptance criteria (a) and (b).

Sign conventions
----------------
``psi`` is the angle the relative wind / current *comes from*, measured from the
bow, positive to starboard.  For ``psi > 0`` (wind from starboard) the lateral
force points to port, i.e. ``Y < 0``; a windage centre aft of midship then gives
a positive (bow-to-starboard) yaw moment.

Ice
---
``riska_ice_resistance`` implements the Riska-type semi-empirical decomposition

    R_i = (C1 + C2 V) rho_i g h^2 B + C3 sigma_f h^2 + C4 mu rho_i g h^2 B

``lindqvist_like_ice_resistance`` is an independent structural decomposition
(stem crushing + bending, buoyancy-driven submergence, sliding friction) used to
bound the ice-resistance model uncertainty.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Dict, Sequence, Tuple

import numpy as np

from . import data as D

# ---------------------------------------------------------------------------
# Fourier coefficient curves
# ---------------------------------------------------------------------------
def _design_matrix(psi_deg: np.ndarray, odd: bool, order: int) -> np.ndarray:
    p = np.radians(psi_deg)
    if odd:
        return np.column_stack([np.sin(k * p) for k in range(1, order + 1)])
    cols = [np.ones_like(p)]
    cols += [np.cos(k * p) for k in range(1, order + 1)]
    return np.column_stack(cols)


def fold_angle(psi_deg: float) -> float:
    """Fold an angle in degrees into ``(-180, 180]``."""
    a = math.fmod(psi_deg, 360.0)
    if a <= -180.0:
        a += 360.0
    elif a > 180.0:
        a -= 360.0
    return a


class CoefficientCurve:
    """Least-squares Fourier representation of an angle-dependent coefficient.

    ``odd=False`` yields an even function, ``odd=True`` an odd one; any input
    angle is folded into ``(-180, 180]`` before evaluation.
    """

    __slots__ = ("coeffs", "odd", "order", "anchors", "rmse")

    def __init__(self, anchors: Sequence[Tuple[float, ...]], column: int,
                 odd: bool, order: int = 3) -> None:
        psi = np.array([a[0] for a in anchors], dtype=float)
        val = np.array([a[column] for a in anchors], dtype=float)
        psi_full = np.concatenate([psi, -psi[1:]])
        val_full = np.concatenate([val, (-val if odd else val)[1:]])
        A = _design_matrix(psi_full, odd, order)
        coeffs, *_ = np.linalg.lstsq(A, val_full, rcond=None)
        self.coeffs = coeffs
        self.odd = odd
        self.order = order
        self.anchors = tuple(anchors)
        self.rmse = float(np.sqrt(np.mean((A @ coeffs - val_full) ** 2)))

    def __call__(self, psi_deg: float) -> float:
        p = math.radians(fold_angle(psi_deg))
        if self.odd:
            return float(sum(c * math.sin(k * p)
                             for k, c in enumerate(self.coeffs, 1)))
        return float(self.coeffs[0] + sum(
            c * math.cos(k * p) for k, c in enumerate(self.coeffs[1:], 1)))

    def __repr__(self) -> str:  # pragma: no cover - debug helper
        return (f"CoefficientCurve(odd={self.odd}, "
                f"coeffs={np.round(self.coeffs, 5).tolist()}, "
                f"rmse={self.rmse:.2e})")


#: Wind coefficient curves (reference areas: A_L for X/Y, A_L*L for N)
WIND_CX = CoefficientCurve(D.WIND_ANCHORS, column=1, odd=False)
WIND_CY = CoefficientCurve(D.WIND_ANCHORS, column=2, odd=True)
WIND_CN = CoefficientCurve(D.WIND_ANCHORS, column=3, odd=True)

#: Current coefficient curves (A_MS for X, A_LS for Y, A_LS*L for N)
CUR_CX = CoefficientCurve(D.CURRENT_ANCHORS, column=1, odd=False)
CUR_CY = CoefficientCurve(D.CURRENT_ANCHORS, column=2, odd=True)
CUR_CN = CoefficientCurve(D.CURRENT_ANCHORS, column=3, odd=True)


# ---------------------------------------------------------------------------
# Generic load container
# ---------------------------------------------------------------------------
@dataclass
class EnvLoad:
    """An environmental load in the ship-fixed frame (SI units)."""

    X: float                    # surge force [N], + forward
    Y: float                    # sway force [N], + to starboard
    N: float                    # yaw moment [N m], + bow to starboard
    speed: float = 0.0          # reference relative speed [m/s]
    angle_deg: float = 0.0      # direction the field comes from [deg]
    model: str = ""
    components: Dict[str, float] = field(default_factory=dict)

    @property
    def magnitude(self) -> float:
        return math.hypot(self.X, self.Y)

    def as_dict(self) -> Dict[str, float]:
        return {"X": self.X, "Y": self.Y, "N": self.N,
                "magnitude": self.magnitude, "speed": self.speed,
                "angle_deg": self.angle_deg}


# ---------------------------------------------------------------------------
# Wind
# ---------------------------------------------------------------------------
def wind_load(vessel: D.Vessel, wind_speed: float, psi_deg: float,
              model: str = "ocimf", rho_air: float = D.RHO_AIR) -> EnvLoad:
    """Wind load on the above-water hull.

    Parameters
    ----------
    wind_speed : relative wind speed [m/s] (air speed seen by the ship)
    psi_deg : relative wind angle the wind *comes from* [deg], + to starboard
    model : ``"ocimf"`` (coefficient curves) or ``"projected"`` (reference)
    """
    if wind_speed < 0:
        raise ValueError("wind_speed must be non-negative")
    q = 0.5 * rho_air * wind_speed ** 2
    psi = fold_angle(psi_deg)

    if model == "ocimf":
        X = -q * vessel.A_L * WIND_CX(psi)
        Y = -q * vessel.A_L * WIND_CY(psi)
        N = q * vessel.A_L * vessel.L * WIND_CN(psi)
        comps = {"CX": WIND_CX(psi), "CY": WIND_CY(psi), "CN": WIND_CN(psi),
                 "A_L": vessel.A_L, "A_F": vessel.A_F}
    elif model == "projected":
        p = math.radians(psi)
        cs, sn = math.cos(p), math.sin(p)
        X = -q * D.C_D_FRONTAL * vessel.A_F * cs * abs(cs)
        Y = -q * D.C_D_LATERAL * vessel.A_L * sn * abs(sn)
        N = D.X_CE_RATIO * vessel.L * Y
        comps = {"C_D_frontal": D.C_D_FRONTAL,
                 "C_D_lateral": D.C_D_LATERAL,
                 "x_ce_over_L": D.X_CE_RATIO}
    else:
        raise ValueError(f"unknown wind model {model!r}")

    return EnvLoad(X=X, Y=Y, N=N, speed=wind_speed, angle_deg=psi,
                   model=model, components=comps)


def imo_beam_wind_pressure(wind_speed: float) -> float:
    """IMO IS Code (2008) beam-wind pressure ``P = 0.504 V^2`` [Pa]."""
    return D.IMO_WIND_PRESSURE_COEF * wind_speed ** 2


# ---------------------------------------------------------------------------
# Current
# ---------------------------------------------------------------------------
def current_load(vessel: D.Vessel, current_speed: float, psi_deg: float,
                 model: str = "ocimf",
                 rho_water: float = D.RHO_WATER) -> EnvLoad:
    """Static current (drift) load on the immersed hull.

    ``psi_deg`` is the direction the current *comes from* relative to the ship
    heading (+ to starboard).
    """
    if current_speed < 0:
        raise ValueError("current_speed must be non-negative")
    q = 0.5 * rho_water * current_speed ** 2
    psi = fold_angle(psi_deg)

    if model == "ocimf":
        X = q * vessel.A_MS * CUR_CX(psi)
        Y = -q * vessel.A_LS * CUR_CY(psi)
        N = q * vessel.A_LS * vessel.L * CUR_CN(psi)
        comps = {"CXC": CUR_CX(psi), "CYC": CUR_CY(psi), "CNC": CUR_CN(psi),
                 "A_MS": vessel.A_MS, "A_LS": vessel.A_LS}
    elif model == "reference":
        p = math.radians(psi)
        cs, sn = math.cos(p), math.sin(p)
        reynolds = max(current_speed, 1e-3) * vessel.L / D.NU_WATER
        cf = D.ittc57_cf(reynolds)
        X = -q * cf * vessel.S_wetted * cs * abs(cs)
        Y = -q * D.C_D_CROSSFLOW * vessel.A_LS * sn * abs(sn)
        N = D.X_CP_RATIO * vessel.L * Y
        comps = {"Cf": cf, "Re": reynolds, "S_wetted": vessel.S_wetted,
                 "C_D_crossflow": D.C_D_CROSSFLOW,
                 "x_cp_over_L": D.X_CP_RATIO}
    else:
        raise ValueError(f"unknown current model {model!r}")

    return EnvLoad(X=X, Y=Y, N=N, speed=current_speed, angle_deg=psi,
                   model=model, components=comps)


# ---------------------------------------------------------------------------
# Ice
# ---------------------------------------------------------------------------
@dataclass
class IceResistance:
    """Level-ice resistance decomposition [N]."""

    total: float
    breaking: float
    velocity: float
    friction: float
    speed: float
    thickness: float
    model: str

    def as_dict(self) -> Dict[str, float]:
        return {"total": self.total, "breaking": self.breaking,
                "velocity": self.velocity, "friction": self.friction,
                "speed": self.speed, "thickness": self.thickness}


def riska_ice_resistance(vessel: D.Vessel, ice: D.IceCondition,
                         speed: float) -> IceResistance:
    """Riska-type level-ice resistance (see module docstring)."""
    if ice.thickness < 0 or speed < 0:
        raise ValueError("ice thickness and speed must be non-negative")
    c = D.RISKA_COEFFICIENTS
    h = ice.thickness
    rho_g_h2_B = ice.rho_ice * D.GRAVITY * h * h * vessel.B
    breaking = c["C1"] * rho_g_h2_B + c["C3"] * ice.sigma_flexural * h * h
    velocity = c["C2"] * rho_g_h2_B * speed
    friction = c["C4"] * ice.mu * rho_g_h2_B
    total = breaking + velocity + friction if h > 0 else 0.0
    return IceResistance(total=total, breaking=breaking, velocity=velocity,
                         friction=friction, speed=speed, thickness=h,
                         model="riska")


def lindqvist_like_ice_resistance(vessel: D.Vessel, ice: D.IceCondition,
                                  speed: float) -> IceResistance:
    """Independent ice-resistance decomposition used as an uncertainty bound."""
    c = D.LINDQVIST_LIKE_COEFFICIENTS
    h = ice.thickness
    alpha = math.radians(ice.stem_angle)
    k_break = ice.sigma_flexural * h * h * (
        math.tan(alpha) + ice.mu / math.cos(alpha))
    k_break += 0.05 * ice.sigma_crushing * h * h
    k_sub = ice.density_deficit * ice.rho_ice * D.GRAVITY * h * h * vessel.B
    k_fric = (ice.mu * ice.rho_ice * D.GRAVITY * h * h * vessel.B
              * (1.0 + 0.1 * speed))
    breaking = c["k_break"] * k_break
    submergence = c["k_sub"] * k_sub
    friction = c["k_fric"] * k_fric
    total = breaking + submergence + friction if h > 0 else 0.0
    return IceResistance(total=total, breaking=breaking, velocity=submergence,
                         friction=friction, speed=speed, thickness=h,
                         model="lindqvist_like")


def ice_load(vessel: D.Vessel, ice: D.IceCondition, speed: float,
             drift_angle_deg: float = 0.0, yaw_rate: float = 0.0,
             yaw_damping: float = 0.35, model: str = "riska") -> EnvLoad:
    """Ice force and yaw moment on a manoeuvring ship in level ice.

    The resistance acts opposite to the instantaneous velocity vector of the
    hull through the ice (``X_i = -R_i cos(beta)``, ``Y_i = -R_i sin(beta)``),
    and the surrounding channel restrains yaw with an engineering damping
    moment ``N_i = -k R_i L (r L / (2 U))``.
    """
    if speed < 1e-6:
        raise ValueError("speed must be positive for the ice load model")
    beta = math.radians(drift_angle_deg)
    res = (riska_ice_resistance if model == "riska"
           else lindqvist_like_ice_resistance)(vessel, ice, speed)
    X = -res.total * math.cos(beta)
    Y = -res.total * math.sin(beta)
    N = -yaw_damping * res.total * vessel.L * (yaw_rate * vessel.L /
                                               (2.0 * speed))
    return EnvLoad(X=X, Y=Y, N=N, speed=speed, angle_deg=drift_angle_deg,
                   model=f"ice:{model}",
                   components={"R_ice": res.total,
                               "R_breaking": res.breaking,
                               "R_velocity": res.velocity,
                               "R_friction": res.friction})


# ---------------------------------------------------------------------------
# Relative-velocity helper (used by the coupled simulator)
# ---------------------------------------------------------------------------
def relative_field(u: float, v: float, heading: float, field_speed: float,
                   field_from_deg_earth: float) -> Tuple[float, float]:
    """Relative speed [m/s] and relative angle [deg, + starboard] seen by ship.

    Parameters
    ----------
    u, v : surge / sway velocity in the ship frame [m/s]
    heading : ship heading [rad] measured from +x_earth
    field_speed : wind or current speed [m/s]
    field_from_deg_earth : direction the field comes *from*, earth frame [deg]
    """
    src = math.radians(field_from_deg_earth)
    wx = -field_speed * math.cos(src)      # motion direction of the medium
    wy = -field_speed * math.sin(src)
    sx = u * math.cos(heading) - v * math.sin(heading)
    sy = u * math.sin(heading) + v * math.cos(heading)
    rx, ry = wx - sx, wy - sy
    rel_speed = math.hypot(rx, ry)
    if rel_speed < 1e-9:
        return 0.0, 0.0
    # transform into the ship frame and convert to "coming from" angle
    bx = rx * math.cos(heading) + ry * math.sin(heading)
    by = -rx * math.sin(heading) + ry * math.cos(heading)
    psi = math.degrees(math.atan2(-by, -bx))
    return rel_speed, fold_angle(psi)