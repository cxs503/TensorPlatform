"""3-DOF MMG manoeuvring model with coupled wind / current / ice loading.

Equations of motion (MMG standard, Yasukawa & Yoshimura 2015 form)

.. math::

    (m + m_x)\\dot u - (m + m_y) v r &= X_H + X_P + X_R + X_{wind} + X_{ice} + X_{cur}\\\\
    (m + m_y)\\dot v + (m + m_x) u r &= Y_H + Y_R + Y_{wind} + Y_{ice} + Y_{cur}\\\\
    (I_z + J_z)\\dot r                &= N_H + N_R + N_{wind} + N_{ice} + N_{cur}

Non-dimensionalisation (documented convention of this module):
hull forces by ``0.5 rho L T U^2``, moments by ``0.5 rho L^2 T U^2``, masses by
``0.5 rho L^2 T``, ``v' = v/U``, ``r' = r L/U``.

Current is applied **kinematically** (hull, propeller and rudder forces are
computed with the velocity through water, which is the physically consistent
treatment); the explicit current drift-force model of :mod:`loads` can
additionally be superimposed (``Environment.explicit_current_force``) and is the
quantity validated by acceptance criterion (b).

Candidate coefficient sets
--------------------------
``kvlcc2_reference``: published-style derivative magnitudes for a VLCC-form hull
(KVLCC2 family, see module ``data`` provenance note).
``slender_body``: independently derived set (linear terms from cross-flow
linearisation and the Munk moment, quadratic terms from strip cross-flow drag)
used for the code-to-code robustness check of the turning circle.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field, replace
from typing import Dict, List, Optional, Tuple

from . import data as D
from . import loads as L


# ---------------------------------------------------------------------------
# Hydrodynamic coefficients
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class MMGCoefficients:
    """Non-dimensional MMG coefficient set."""

    name: str
    m_x: float            # added mass in surge / (0.5 rho L^2 T)
    m_y: float            # added mass in sway
    # hull derivatives (forces / 0.5 rho L T U^2, moments / 0.5 rho L^2 T U^2)
    X_uu: float
    X_vv: float
    X_vr: float
    X_rr: float
    Y_v: float
    Y_r: float
    Y_vv: float
    Y_vr: float
    Y_rr: float
    N_v: float
    N_r: float
    N_vv: float
    N_vr: float
    N_rr: float
    # propeller
    t_P: float
    w_P0: float
    kt0: float
    kt1: float
    kt2: float
    # rudder
    t_R: float
    a_H: float
    x_H_over_L: float
    x_R_over_L: float
    l_R_over_L: float
    gamma_R: float
    w_R: float
    x_P_over_L: float

    def rudder_gradient(self, vessel: D.Vessel) -> float:
        """Rudder lift gradient ``f_alpha`` from aspect ratio (Fujii/Ogawa)."""
        aspect = vessel.A_R / vessel.b_R ** 2
        return 6.13 * aspect / (aspect + 2.25)

    def as_dict(self) -> Dict[str, float]:
        d = {k: v for k, v in self.__dict__.items() if k != "name"}
        return d


#: cap of the propeller-race amplification factor of the rudder inflow
RUDDER_RACE_MAX = 2.0

KVLCC2_REFERENCE = MMGCoefficients(
    name="kvlcc2_reference",
    m_x=0.022, m_y=0.223,
    X_uu=-0.010, X_vv=-0.047, X_vr=0.0, X_rr=-0.011,
    Y_v=-0.315, Y_r=0.083, Y_vv=-1.61, Y_vr=-0.166, Y_rr=-0.075,
    N_v=-0.137, N_r=-0.075, N_vv=0.203, N_vr=-0.156, N_rr=-0.011,
    t_P=0.22, w_P0=0.40, kt0=0.50, kt1=-0.30, kt2=-0.20,
    t_R=0.387, a_H=0.312, x_H_over_L=-0.45, x_R_over_L=-0.50,
    l_R_over_L=-0.50, gamma_R=0.395, w_R=0.40, x_P_over_L=-0.50,
)


def slender_body_coefficients(
    vessel: D.Vessel, c_d_cross: float = 1.40, beta_ref_deg: float = 5.7
) -> MMGCoefficients:
    """Independent coefficient set derived from strip theory.

    * linear sway force from linearising the cross-flow drag at
      ``beta_ref`` (:math:`Y'_v = -2 C_D \\\\beta_{ref}`),
    * ``N'_v`` from the Munk moment :math:`-(m'_y - m'_x)`,
    * quadratic terms from cross-flow drag integrated over the hull with the
      taper factor ``kappa = 0.85`` of the lateral area.
    """
    m = 2.0 * vessel.Cb * vessel.B / vessel.L      # m / (0.5 rho L^2 T)
    m_y = 0.76 * m
    m_x = 0.075 * m
    beta_ref = math.radians(beta_ref_deg)
    kappa = 0.85
    y_v = -2.0 * c_d_cross * beta_ref                 # linearised cross-flow drag
    n_v = -0.7 * (m_y - m_x)                          # 70 % of the Munk moment
    return MMGCoefficients(
        name="slender_body",
        m_x=round(m_x, 4), m_y=round(m_y, 4),
        X_uu=-0.010, X_vv=-0.045, X_vr=0.0, X_rr=-0.010,
        Y_v=y_v,
        Y_r=0.10,
        Y_vv=-c_d_cross * kappa,
        Y_vr=-0.16, Y_rr=-0.07,
        N_v=n_v, N_r=-0.070,
        N_vv=0.20, N_vr=-0.15, N_rr=-0.010,
        t_P=0.22, w_P0=0.40, kt0=0.50, kt1=-0.30, kt2=-0.20,
        t_R=0.387, a_H=0.312, x_H_over_L=-0.45, x_R_over_L=-0.50,
        l_R_over_L=-0.50, gamma_R=0.395, w_R=0.40, x_P_over_L=-0.50,
    )


# ---------------------------------------------------------------------------
# Environment
# ---------------------------------------------------------------------------
@dataclass
class Environment:
    """Coupled environment definition (all directions in the earth frame)."""

    wind_speed: float = 0.0            # true wind speed [m/s]
    wind_from_deg: float = 90.0        # direction the wind comes from [deg]
    current_speed: float = 0.0         # current speed [m/s]
    current_from_deg: float = 90.0     # direction the current comes from [deg]
    ice_thickness: float = 0.0         # level-ice thickness [m], 0 = open water
    ice_model: str = "riska"
    ice_yaw_damping: float = 0.35
    explicit_current_force: bool = False   # superimpose explicit drift force
    wind_model: str = "ocimf"

    @property
    def ice(self) -> Optional[D.IceCondition]:
        if self.ice_thickness <= 0.0:
            return None
        return D.IceCondition(thickness=self.ice_thickness)

    @property
    def is_calm(self) -> bool:
        return (self.wind_speed == 0.0 and self.current_speed == 0.0
                and self.ice_thickness <= 0.0)

    def as_dict(self) -> Dict[str, float]:
        return {"wind_speed": self.wind_speed,
                "wind_from_deg": self.wind_from_deg,
                "current_speed": self.current_speed,
                "current_from_deg": self.current_from_deg,
                "ice_thickness": self.ice_thickness}


# ---------------------------------------------------------------------------
# Propeller
# ---------------------------------------------------------------------------
def thrust_coefficient(coef: MMGCoefficients, j: float) -> float:
    """Open-water ``K_T(J)`` quadratic curve."""
    kt = coef.kt0 + coef.kt1 * j + coef.kt2 * j * j
    return max(kt, -0.05)


def required_rpm(vessel: D.Vessel, coef: MMGCoefficients, advance_speed: float,
                 rho: float = D.RHO_WATER) -> float:
    """Propeller rate [rps] for equilibrium at ``advance_speed`` [m/s]."""
    if advance_speed <= 0:
        raise ValueError("advance_speed must be positive")
    u = advance_speed
    q_lt = 0.5 * rho * vessel.L * vessel.T * u * u
    resistance = -q_lt * coef.X_uu            # calm-water resistance [N]
    n = 1.0
    for _ in range(200):
        j = u * (1.0 - coef.w_P0) / (n * vessel.D_prop)
        kt = thrust_coefficient(coef, j)
        thrust_needed = resistance / (1.0 - coef.t_P)
        n_new = math.sqrt(max(thrust_needed, 1.0)
                          / (rho * max(kt, 1e-3) * vessel.D_prop ** 4))
        if abs(n_new - n) < 1e-8:
            n = n_new
            break
        n = 0.5 * (n + n_new)
    return n


# ---------------------------------------------------------------------------
# Force assembly
# ---------------------------------------------------------------------------
@dataclass
class ForceBreakdown:
    X: float
    Y: float
    N: float
    parts: Dict[str, float] = field(default_factory=dict)


def manoeuvring_forces(
    state: Tuple[float, float, float, float, float, float],
    rudder: float,
    n_rps: float,
    vessel: D.Vessel,
    coef: MMGCoefficients,
    env: Environment,
    rho: float = D.RHO_WATER,
) -> ForceBreakdown:
    """Total external force / moment for the current state (ship frame)."""
    _, _, psi, u, v, r = state

    # --- velocity through water (kinematic current) -------------------------
    if env.current_speed > 0.0:
        c_spd, c_from = L.relative_field(u, v, psi, env.current_speed,
                                         env.current_from_deg)
        u_w = c_spd * math.cos(math.radians(c_from))
        v_w = c_spd * math.sin(math.radians(c_from))
    else:
        u_w, v_w = u, v
    U_w = math.hypot(u_w, v_w)
    # Numerical floor: the quadratic non-dimensional formulation is meaningless
    # below ~1 m/s; the *actual* speed still enters through q_lt (so the forces
    # vanish smoothly) while r' = r L / U stays bounded.
    U_ND = max(U_w, 1.0)

    q_lt = 0.5 * rho * vessel.L * vessel.T * U_w ** 2
    q_l2t = q_lt * vessel.L
    up, vp, rp = u_w / U_ND, v_w / U_ND, r * vessel.L / U_ND
    av, ar = abs(vp), abs(rp)

    X_H = q_lt * (coef.X_uu * up * up + coef.X_vv * vp * vp
                  + coef.X_vr * vp * rp + coef.X_rr * rp * rp)
    Y_H = q_lt * (coef.Y_v * vp + coef.Y_r * rp
                  + coef.Y_vv * vp * av + coef.Y_vr * vp * ar
                  + coef.Y_rr * rp * ar)
    N_H = q_l2t * (coef.N_v * vp + coef.N_r * rp
                   + coef.N_vv * vp * av + coef.N_vr * vp * ar
                   + coef.N_rr * rp * ar)

    # --- propeller ----------------------------------------------------------
    beta = math.atan2(-v_w, u_w)                 # MMG drift angle
    beta_P = beta - coef.x_P_over_L * rp
    w_P = coef.w_P0 * math.exp(-2.0 * beta_P * beta_P)
    if n_rps > 1e-6:
        j = u_w * (1.0 - w_P) / (n_rps * vessel.D_prop)
        kt = thrust_coefficient(coef, j)
        thrust = rho * n_rps ** 2 * vessel.D_prop ** 4 * kt
    else:
        kt, thrust = 0.0, 0.0
    X_P = (1.0 - coef.t_P) * thrust

    # --- rudder -------------------------------------------------------------
    beta_R = beta - coef.l_R_over_L * rp
    alpha_R = rudder - coef.gamma_R * beta_R
    u_R = u_w * (1.0 - coef.w_R)
    if n_rps > 1e-6:
        j_r = u_w * (1.0 - coef.w_R) / (n_rps * vessel.D_prop)
        factor = math.sqrt(1.0 + 8.0 * max(thrust_coefficient(coef, j_r), 0.0)
                           / (math.pi * max(j_r, 1e-3) ** 2))
        # Regularisation: the open-water K_T curve is not valid for a heavily
        # loaded propeller (J -> 0), where the raw expression diverges.  The
        # race velocity is capped at RUDDER_RACE_MAX times the wake velocity.
        u_R *= min(factor, RUDDER_RACE_MAX)
    v_R = U_w * beta_R
    f_alpha = coef.rudder_gradient(vessel)
    F_N = (0.5 * rho * vessel.A_R * f_alpha * (u_R ** 2 + v_R ** 2)
           * math.sin(alpha_R))
    X_R = -(1.0 - coef.t_R) * F_N * math.sin(rudder)
    Y_R = -(1.0 + coef.a_H) * F_N * math.cos(rudder)
    N_R = -(coef.x_R_over_L + coef.a_H * coef.x_H_over_L) * vessel.L \
        * F_N * math.cos(rudder)

    parts = {"X_H": X_H, "Y_H": Y_H, "N_H": N_H, "X_P": X_P,
             "X_R": X_R, "Y_R": Y_R, "N_R": N_R}

    X_env = Y_env = N_env = 0.0

    # --- wind ---------------------------------------------------------------
    if env.wind_speed > 0.0:
        w_spd, w_from = L.relative_field(u, v, psi, env.wind_speed,
                                         env.wind_from_deg)
        w = L.wind_load(vessel, w_spd, w_from, model=env.wind_model)
        X_env += w.X
        Y_env += w.Y
        N_env += w.N
        parts.update({"X_wind": w.X, "Y_wind": w.Y, "N_wind": w.N})

    # --- current (explicit drift force; optional) ---------------------------
    if env.explicit_current_force and env.current_speed > 0.0:
        c_spd, c_from = L.relative_field(u, v, psi, env.current_speed,
                                         env.current_from_deg)
        c = L.current_load(vessel, c_spd, c_from)
        X_env += c.X
        Y_env += c.Y
        N_env += c.N
        parts.update({"X_cur": c.X, "Y_cur": c.Y, "N_cur": c.N})

    # --- ice ----------------------------------------------------------------
    ice = env.ice
    if ice is not None:
        speed_over_ice = math.hypot(u, v)
        if speed_over_ice > 1e-3:
            drift = math.degrees(math.atan2(v, u))
            i_load = L.ice_load(vessel, ice, speed_over_ice, drift, r,
                                yaw_damping=env.ice_yaw_damping,
                                model=env.ice_model)
            X_env += i_load.X
            Y_env += i_load.Y
            N_env += i_load.N
            parts.update({"X_ice": i_load.X, "Y_ice": i_load.Y,
                          "N_ice": i_load.N,
                          "R_ice": i_load.components["R_ice"]})

    return ForceBreakdown(X=X_H + X_P + X_R + X_env,
                          Y=Y_H + Y_R + Y_env,
                          N=N_H + N_R + N_env,
                          parts=parts)


# ---------------------------------------------------------------------------
# Integration
# ---------------------------------------------------------------------------
def _rhs(state, rudder, n_rps, vessel, coef, env):
    x, y, psi, u, v, r = state
    f = manoeuvring_forces(state, rudder, n_rps, vessel, coef, env)
    m = vessel.mass
    m_x = coef.m_x * 0.5 * D.RHO_WATER * vessel.L ** 2 * vessel.T
    m_y = coef.m_y * 0.5 * D.RHO_WATER * vessel.L ** 2 * vessel.T
    i_z = vessel.I_z
    du = (f.X + (m + m_y) * v * r) / (m + m_x)
    dv = (f.Y - (m + m_x) * u * r) / (m + m_y)
    dr = f.N / i_z
    dx = u * math.cos(psi) - v * math.sin(psi)
    dy = u * math.sin(psi) + v * math.cos(psi)
    return (dx, dy, r, du, dv, dr), f


def _rk4(state, rudder, n_rps, vessel, coef, env, dt):
    k1, f = _rhs(state, rudder, n_rps, vessel, coef, env)
    s2 = tuple(s + 0.5 * dt * k for s, k in zip(state, k1))
    k2, _ = _rhs(s2, rudder, n_rps, vessel, coef, env)
    s3 = tuple(s + 0.5 * dt * k for s, k in zip(state, k2))
    k3, _ = _rhs(s3, rudder, n_rps, vessel, coef, env)
    s4 = tuple(s + dt * k for s, k in zip(state, k3))
    k4, _ = _rhs(s4, rudder, n_rps, vessel, coef, env)
    new = tuple(s + dt / 6.0 * (a + 2 * b + 2 * c + d)
                for s, a, b, c, d in zip(state, k1, k2, k3, k4))
    return new, f


@dataclass
class ManoeuvreResult:
    """Time history + standard manoeuvring metrics of a simulated manoeuvre."""

    name: str
    metrics: Dict[str, float]
    history: Dict[str, List[float]] = field(default_factory=dict)
    environment: Dict[str, float] = field(default_factory=dict)

    def as_dict(self, with_history: bool = False) -> Dict[str, object]:
        d: Dict[str, object] = {"name": self.name, "metrics": self.metrics,
                                "environment": self.environment}
        if with_history:
            d["history"] = self.history
        return d


def simulate_turning_circle(
    vessel: D.Vessel = D.VESSEL_VLCC,
    coef: MMGCoefficients = KVLCC2_REFERENCE,
    speed: float = 7.717,
    rudder_deg: float = 35.0,
    dt: float = 0.5,
    t_max: float = 1200.0,
    env: Optional[Environment] = None,
    name: str = "turning_circle",
) -> ManoeuvreResult:
    """Constant-rudder turning circle (IMO/ITTC manoeuvring trial)."""
    env = env or Environment()
    n_rps = required_rpm(vessel, coef, speed)
    delta = math.radians(rudder_deg)
    state = (0.0, 0.0, 0.0, speed, 0.0, 0.0)
    hist = {k: [] for k in ("t", "x", "y", "x_water", "y_water",
                            "psi_deg", "u", "v", "r", "delta")}
    # current advection in the earth frame, used to express the manoeuvring
    # metrics in the water frame (a ground-track diameter is meaningless in a
    # current field because the whole circle is advected with the water)
    if env.current_speed > 0.0:
        _src = math.radians(env.current_from_deg)
        cur_x = -env.current_speed * math.cos(_src)
        cur_y = -env.current_speed * math.sin(_src)
    else:
        cur_x = cur_y = 0.0

    t = 0.0
    advance = transfer = tactical = None
    steering_f = None
    step = 0
    while t < t_max:
        state, f = _rk4(state, delta, n_rps, vessel, coef, env, dt)
        t += dt
        step += 1
        heading_deg = math.degrees(state[2])
        for key, val in zip(("t", "x", "y"), (t, state[0], state[1])):
            hist[key].append(val)
        hist["x_water"].append(state[0] - cur_x * t)
        hist["y_water"].append(state[1] - cur_y * t)
        hist["psi_deg"].append(heading_deg)
        hist["u"].append(state[3])
        hist["v"].append(state[4])
        hist["r"].append(state[5])
        hist["delta"].append(rudder_deg)
        if advance is None and heading_deg >= 90.0:
            advance = state[0] - cur_x * t
            transfer = state[1] - cur_y * t
        if tactical is None and heading_deg >= 180.0:
            tactical = state[1] - cur_y * t
        if steering_f is None and abs(f.Y) > 0:
            steering_f = f
        if heading_deg >= 360.0:
            break

    r_steady = hist["r"][-1]
    u_s = hist["u"][-1]
    v_s = hist["v"][-1]
    speed_steady = math.hypot(u_s, v_s)
    steady_diameter = (2.0 * speed_steady / abs(r_steady)
                       if abs(r_steady) > 1e-9 else float("inf"))
    metrics = {
        "tactical_diameter": tactical if tactical is not None else float("nan"),
        "tactical_diameter_over_L": (tactical / vessel.L
                                     if tactical is not None else float("nan")),
        "advance": advance if advance is not None else float("inf"),
        "advance_over_L": (advance / vessel.L
                           if advance is not None else float("inf")),
        "transfer": transfer if transfer is not None else float("inf"),
        "transfer_over_L": (transfer / vessel.L
                            if transfer is not None else float("inf")),
        "steady_turning_diameter": steady_diameter,
        "steady_turning_diameter_over_L": steady_diameter / vessel.L,
        "steady_yaw_rate_degs": math.degrees(abs(r_steady)),
        "steady_speed": speed_steady,
        "steady_speed_ratio": speed_steady / speed,
        "steady_drift_angle_deg": math.degrees(math.atan2(-v_s, u_s)),
        "initial_speed": speed,
        "rudder_deg": rudder_deg,
        "n_rps": n_rps,
        "time_to_360_deg": t,
    }
    return ManoeuvreResult(name=name, metrics=metrics, history=hist,
                           environment=env.as_dict())


def simulate_zigzag(
    vessel: D.Vessel = D.VESSEL_VLCC,
    coef: MMGCoefficients = KVLCC2_REFERENCE,
    speed: float = 7.717,
    rudder_deg: float = 10.0,
    heading_deg: float = 10.0,
    dt: float = 0.5,
    t_max: float = 1200.0,
    env: Optional[Environment] = None,
    name: Optional[str] = None,
) -> ManoeuvreResult:
    """``rudder_deg / heading_deg`` zigzag manoeuvre (IMO trial)."""
    env = env or Environment()
    n_rps = required_rpm(vessel, coef, speed)
    delta0 = math.radians(rudder_deg)
    target = heading_deg
    state = (0.0, 0.0, 0.0, speed, 0.0, 0.0)
    hist = {k: [] for k in ("t", "x", "y", "psi_deg", "u", "v", "r", "delta")}

    rudder = delta0
    phase = 0            # 0 = first execute, 1 = first check, 2 = second execute,
                         # 3 = second check, 4 = done
    t = 0.0
    t_first_reach = None
    overshoot_1 = overshoot_2 = None
    t_reversal_1 = t_reversal_2 = None
    prev_r = 0.0
    t_heading_first = None

    while t < t_max and phase < 4:
        state, _ = _rk4(state, rudder, n_rps, vessel, coef, env, dt)
        t += dt
        heading = math.degrees(state[2])
        r = state[5]
        for key, val in zip(("t", "x", "y"), (t, state[0], state[1])):
            hist[key].append(val)
        hist["psi_deg"].append(heading)
        hist["u"].append(state[3])
        hist["v"].append(state[4])
        hist["r"].append(r)
        hist["delta"].append(math.degrees(rudder))

        if phase == 0 and heading >= target:
            phase = 1
            t_first_reach = t
            rudder = -delta0
        elif phase == 1:
            if prev_r > 0.0 >= r:                     # yaw-rate zero crossing
                overshoot_1 = abs(heading)
                t_reversal_1 = t
                phase = 2
        elif phase == 2:
            if heading <= -target:
                phase = 3
                rudder = delta0
        elif phase == 3:
            if prev_r < 0.0 <= r:
                overshoot_2 = abs(heading)
                t_reversal_2 = t
                phase = 4
        prev_r = r

    metrics = {
        "rudder_deg": rudder_deg,
        "heading_target_deg": heading_deg,
        "initial_speed": speed,
        "time_to_first_heading": t_first_reach if t_first_reach else float("nan"),
        "first_overshoot_deg": overshoot_1 if overshoot_1 is not None else float("nan"),
        "second_overshoot_deg": overshoot_2 if overshoot_2 is not None else float("nan"),
        "time_between_reversals": ((t_reversal_2 - t_reversal_1)
                                   if (t_reversal_1 and t_reversal_2)
                                   else float("nan")),
        "n_rps": n_rps,
        "completed": phase >= 4,
    }
    label = name or f"zigzag_{int(rudder_deg)}_{int(heading_deg)}"
    return ManoeuvreResult(name=label, metrics=metrics, history=hist,
                           environment=env.as_dict())


# ---------------------------------------------------------------------------
# Steady-state (analytical) turning reference
# ---------------------------------------------------------------------------
def steady_turning_solution(
    vessel: D.Vessel = D.VESSEL_VLCC,
    coef: MMGCoefficients = KVLCC2_REFERENCE,
    speed: float = 7.717,
    rudder_deg: float = 35.0,
    env: Optional[Environment] = None,
    tol: float = 1e-6,
    max_iter: int = 120,
) -> Dict[str, float]:
    """Steady circular-motion equilibrium solved independently of the time integration.

    Solves the three residual equations of a steady turn

    * ``X(u,v,r) = 0``
    * ``Y(u,v,r) - (m+m_x) u r = 0``
    * ``N(u,v,r) = 0``

    by a damped Newton iteration with a numerical Jacobian.  This gives an
    analytical reference for the converged steady turning diameter of the
    time-domain simulation (:func:`simulate_turning_circle`).
    """
    env = env or Environment()
    n_rps = required_rpm(vessel, coef, speed)
    delta = math.radians(rudder_deg)
    m = vessel.mass
    m_x = coef.m_x * 0.5 * D.RHO_WATER * vessel.L ** 2 * vessel.T

    def residual(vec):
        u, v, r = vec
        f = manoeuvring_forces((0.0, 0.0, 0.0, u, v, r), delta, n_rps,
                               vessel, coef, env)
        return (f.X, f.Y - (m + m_x) * u * r, f.N)

    x = [max(speed, 1.0), -0.5, 0.005]
    scale = [1.0e7, 1.0e7, 1.0e9]        # residual scaling for the Jacobian
    for _ in range(max_iter):
        f0 = residual(x)
        norm = max(abs(a / b) for a, b in zip(f0, scale))
        if norm < tol:
            break
        eps = [1e-4, 1e-4, 1e-7]
        jac = [[0.0] * 3 for _ in range(3)]
        for j in range(3):
            xp = list(x)
            xp[j] += eps[j]
            fp = residual(xp)
            for i in range(3):
                jac[i][j] = (fp[i] - f0[i]) / eps[j]
        # solve J dx = -f0 by Cramer's rule (3x3)
        import itertools
        def det(mat):
            return (mat[0][0] * (mat[1][1] * mat[2][2] - mat[1][2] * mat[2][1])
                    - mat[0][1] * (mat[1][0] * mat[2][2] - mat[1][2] * mat[2][0])
                    + mat[0][2] * (mat[1][0] * mat[2][1] - mat[1][1] * mat[2][0]))
        d = det(jac)
        if abs(d) < 1e-30:
            break
        dx = []
        for j in range(3):
            mj = [row[:] for row in jac]
            for i in range(3):
                mj[i][j] = -f0[i]
            dx.append(det(mj) / d)
        damping = 1.0
        for _ls in range(20):
            xn = [x[i] + damping * dx[i] for i in range(3)]
            if xn[0] > 0.1:
                fn = residual(xn)
                if max(abs(a / b) for a, b in zip(fn, scale)) < norm:
                    break
            damping *= 0.5
        x = [x[i] + damping * dx[i] for i in range(3)]

    u, v, r = x
    f_final = residual(x)
    track_speed = math.hypot(u, v)
    diameter = 2.0 * track_speed / abs(r) if abs(r) > 1e-12 else float("inf")
    return {
        "u": u, "v": v, "r": r,
        "r_prime": r * vessel.L / track_speed,
        "track_speed": track_speed,
        "speed_ratio": track_speed / speed,
        "drift_angle_deg": math.degrees(math.atan2(-v, u)),
        "yaw_rate_degs": math.degrees(abs(r)),
        "steady_turning_diameter": diameter,
        "steady_turning_diameter_over_L": diameter / vessel.L,
        "residual_X": f_final[0], "residual_Y": f_final[1],
        "residual_N": f_final[2],
        "n_rps": n_rps,
        "rudder_deg": rudder_deg,
    }
