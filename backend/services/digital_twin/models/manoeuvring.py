"""Manoeuvring: 3-DOF MMG (surge-sway-yaw) time-domain simulation.

Model
-----
Rigid-body equations with added mass and quasi-steady hull forces::

    (m + mx) u' - (m + my) v r = X_H + X_R + X_P
    (m + my) v' + (m + mx) u r = Y_H + Y_R
    (Iz + Jz) r'                 = N_H + N_R

* cross-flow drag hull force (dominant for full-form hulls):
  ``Y_H = -0.5 rho C_D int T(x) |v + x r| (v + x r) dx`` (and its moment);
* slender-body added mass ``my = rho pi int T^2 dx``, ``Jz = rho pi int T^2 x^2 dx``,
  ``mx = 0.10 m``;
* hull lift from low-aspect-ratio lifting-surface theory
  ``Y_lift = -0.5 rho U^2 (L T) C_Lalpha beta``, ``C_Lalpha = pi AR / 2``,
  ``AR = 2T/L``, evaluated at the centre of lateral area (``x_L = -0.05 L``);
  this supplies the linear (small-drift) lateral force that pure cross-flow drag
  misses for a hull with constant section draft;
* resistance ``X_H = -R_holtrop(u)`` from the resistance module;
* propeller at constant shaft speed: linear ``K_T(J)`` thrust curve anchored at
  the design point with a bollard-pull ratio of 1.6,
  ``T(u) = T_0 (1.6 - 0.6 u/u0)`` - thrust rises as the ship slows in the turn;
* rudder force with flow straightening
  ``Y_R = -0.5 rho A_R C_Lalpha alpha_R U_R^2``, ``alpha_R = delta - gamma_R atan((v + x_R r)/u)``,
  ``U_R = 0.9 u`` (wake + propeller-race inflow), rudder area ratio
  ``c_r = 1.7 %`` of ``L*T``, ``x_R = -L/2``.

Validation status: the absolute turning-circle diameter is *not* validated on a
full-scale trial in this worktree.  The plausibility band for a full-form single
screw ship (tactical diameter 2.5-4.5 L at 35 deg rudder) is checked and the
monotonic dependence on rudder angle is asserted in the tests.
"""
from __future__ import annotations

import math
from typing import Dict

import numpy as np

from .geometry import HullForm
from .resistance import holtrop_resistance

G = 9.80665


def _integrals(hull: HullForm, n: int = 61):
    x = np.linspace(-0.5 * hull.L, 0.5 * hull.L, n)
    xm = 0.5 * (x[:-1] + x[1:])
    dx = float(x[1] - x[0])
    depth = np.full_like(xm, hull.T)          # rectangular sections -> full draft
    return xm, depth, dx


def turning_circle(
    hull: HullForm,
    U0: float,
    rudder_deg: float = 35.0,
    t_end: float = 900.0,
    dt: float = 0.10,
    C_D: float = 1.0,
    c_rudder: float = 0.017,
    gamma_R: float = 0.4,
    U_R_factor: float = 0.90,
    n_sections: int = 61,
) -> Dict[str, float]:
    """Simulate a starboard turn; return IMO-style manoeuvring metrics."""
    xm, depth, dx = _integrals(hull, n_sections)
    m = hull.volume * hull.rho
    rho = hull.rho
    my = rho * math.pi * float(np.sum(depth ** 2)) * dx
    Jz = rho * math.pi * float(np.sum(depth ** 2 * xm ** 2)) * dx
    mx = 0.10 * m
    Iz = m * (0.25 * hull.L) ** 2
    A_R = c_rudder * hull.L * hull.T
    x_R = -0.5 * hull.L
    C_Lalpha = 2.0 * math.pi
    AR = 2.0 * hull.T / hull.L
    CL_alpha_hull = math.pi * AR / 2.0
    S_ref = hull.L * hull.T
    x_L = -0.05 * hull.L

    def resistance(u: float) -> float:
        return holtrop_resistance(max(u, 0.5), hull.L, hull.B, hull.T, hull.Cb,
                                  rho=rho)["RT_N"]

    def hull_lift(v: float, r: float, u: float):
        beta = math.atan((v + x_L * r) / max(u, 0.3))
        Y = -0.5 * rho * u ** 2 * S_ref * CL_alpha_hull * beta
        return Y, Y * x_L

    def rudder(v: float, r: float, u: float, delta: float):
        u_eff = max(u, 0.3)
        alpha = delta - gamma_R * math.atan((v + x_R * r) / u_eff)
        U_R = U_R_factor * u_eff
        Y = -0.5 * rho * A_R * C_Lalpha * alpha * U_R ** 2
        return Y, Y * x_R

    def deriv(state, delta, X_P):  # noqa: N803
        x, y, psi, u, v, r = state
        u_eff = max(u, 0.2)
        s = v + xm * r
        cf = -0.5 * rho * C_D * depth * np.abs(s) * s
        Yl, Nl = hull_lift(v, r, u)
        Y_H = float(np.sum(cf)) * dx + Yl
        N_H = float(np.sum(cf * xm)) * dx + Nl
        Y_R, N_R = rudder(v, r, u, delta)
        X_H = -resistance(u)
        du = (X_H + thrust(u) + (m + my) * v * r) / (m + mx)
        dv = (Y_H + Y_R - (m + mx) * u_eff * r) / (m + my)
        dr = (N_H + N_R) / (Iz + Jz)
        return np.array([u * math.cos(psi) - v * math.sin(psi),
                         u * math.sin(psi) + v * math.cos(psi),
                         r, du, dv, dr])

    T_0 = resistance(U0)

    def thrust(u: float) -> float:
        return T_0 * (1.6 - 0.6 * min(u / U0, 1.2))

    state = np.array([0.0, 0.0, 0.0, U0, 0.0, 0.0])
    delta = math.radians(rudder_deg)
    traj = [state.copy()]
    heading_change = [0.0]
    t = 0.0

    def rk4(st, h):
        k1 = deriv(st, delta, T_0)
        k2 = deriv(st + 0.5 * h * k1, delta, T_0)
        k3 = deriv(st + 0.5 * h * k2, delta, T_0)
        k4 = deriv(st + h * k3, delta, T_0)
        return st + h / 6.0 * (k1 + 2 * k2 + 2 * k3 + k4)

    while t < t_end:
        state = rk4(state, dt)
        t += dt
        traj.append(state.copy())
        heading_change.append(abs(state[2]))
        if abs(state[2]) >= math.pi:
            break

    traj = np.asarray(traj)
    psi = traj[:, 2]
    # advance: x travelled when the heading changed by 90 deg
    idx90 = np.argmax(psi >= math.pi / 2) if np.any(psi >= math.pi / 2) else None
    idx180 = np.argmax(psi >= math.pi) if np.any(psi >= math.pi) else None
    advance = float(traj[idx90, 0]) if idx90 is not None else float("nan")
    tactical = float(abs(traj[idx180, 1])) if idx180 is not None else float("nan")
    u_ss = float(traj[-1, 3])
    r_ss = float(abs(traj[-1, 5]))
    D_ss = 2.0 * u_ss / r_ss if r_ss > 1e-9 else float("inf")
    return {
        "advance_m": advance,
        "tactical_diameter_m": tactical,
        "turning_diameter_m": float(D_ss),
        "turning_diameter_L": float(D_ss / hull.L),
        "tactical_diameter_L": float(tactical / hull.L) if tactical == tactical else float("nan"),
        "advance_L": float(advance / hull.L) if advance == advance else float("nan"),
        "steady_speed_m_s": u_ss,
        "steady_yaw_rate_deg_s": math.degrees(r_ss),
        "speed_loss_pct": float(100.0 * (1.0 - u_ss / U0)),
        "rudder_deg": float(rudder_deg),
        "simulation_time_s": float(t),
        "traj_x": traj[::max(1, len(traj) // 400), 0].tolist(),
        "traj_y": traj[::max(1, len(traj) // 400), 1].tolist(),
    }


def zigzag(
    hull: HullForm,
    U0: float,
    rudder_deg: float = 10.0,
    dt: float = 0.1,
    t_end: float = 600.0,
) -> Dict[str, float]:
    """10/10 zigzag first overshoot angle (IMO resolution A.751 style)."""
    xm, depth, dx = _integrals(hull)
    m = hull.volume * hull.rho
    rho = hull.rho
    my = rho * math.pi * float(np.sum(depth ** 2)) * dx
    Jz = rho * math.pi * float(np.sum(depth ** 2 * xm ** 2)) * dx
    mx = 0.10 * m
    Iz = m * (0.25 * hull.L) ** 2
    A_R = 0.017 * hull.L * hull.T
    x_R = -0.5 * hull.L
    C_Lalpha = 2.0 * math.pi

    def resistance(u):
        return holtrop_resistance(max(u, 0.5), hull.L, hull.B, hull.T, hull.Cb,
                                  rho=rho)["RT_N"]

    T_0 = resistance(U0)

    def thrust(u):
        return T_0 * (1.6 - 0.6 * min(u / U0, 1.2))

    def step(state, delta, h=dt):
        x, y, psi, u, v, r = state
        u_eff = max(u, 0.2)
        s = v + xm * r
        cf = -0.5 * rho * 1.0 * depth * np.abs(s) * s
        Y_H = float(np.sum(cf)) * dx
        N_H = float(np.sum(cf * xm)) * dx
        alpha = delta - 0.4 * math.atan((v + x_R * r) / u_eff)
        U_R = 0.7 * u_eff
        Y_R = -0.5 * rho * A_R * C_Lalpha * alpha * U_R ** 2
        N_R = Y_R * x_R
        X_H = -resistance(u)
        du = (X_H + thrust(u) + (m + my) * v * r) / (m + mx)
        dv = (Y_H + Y_R - (m + mx) * u_eff * r) / (m + my)
        dr = (N_H + N_R) / (Iz + Jz)
        return np.array([u * math.cos(psi) - v * math.sin(psi),
                         u * math.sin(psi) + v * math.cos(psi), r, du, dv, dr])

    d = math.radians(rudder_deg)
    state = np.array([0.0, 0.0, 0.0, U0, 0.0, 0.0])
    t = 0.0
    stage = 0                  # 0->+10, 1->overshoot 1, 2->-10, 3->overshoot 2
    max_psi = -1e9
    min_psi = 1e9
    overshoot = []
    while t < t_end and stage < 4:
        state = state + dt * step(state, d)      # explicit Euler (dt = 0.1 s)
        t += dt
        psi = math.degrees(state[2])
        if stage == 0:
            if psi >= rudder_deg:
                stage, max_psi, d = 1, psi, -d
        elif stage == 1:
            max_psi = max(max_psi, psi)
            if psi < max_psi - 0.2:
                overshoot.append(max_psi - rudder_deg)
                stage, min_psi = 2, psi
        elif stage == 2:
            if psi <= -rudder_deg:
                stage, min_psi, d = 3, psi, -d
        else:
            min_psi = min(min_psi, psi)
            if psi > min_psi + 0.2:
                overshoot.append(abs(min_psi) - rudder_deg)
                stage = 4
    return {
        "first_overshoot_deg": float(overshoot[0]) if overshoot else float("nan"),
        "second_overshoot_deg": float(overshoot[1]) if len(overshoot) > 1 else float("nan"),
        "rudder_deg": float(rudder_deg),
    }