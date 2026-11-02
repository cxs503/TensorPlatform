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

One force model, two manoeuvres
-------------------------------
Both :func:`turning_circle` and :func:`zigzag` are integrated from the *same*
``_MMGCore`` object (m1651).  Before m1651 the zigzag duplicated the equations
by hand, silently dropping the hull-lift term and using ``U_R = 0.7 u`` instead
of ``0.9 u``, so the two published manoeuvre numbers of the same ship did not
belong to the same hydrodynamic model (initial yaw acceleration differed by a
factor ``(0.9/0.7)^2 = 1.65``).  The divergence is now impossible by
construction and is guarded by ``tests/feat/test_manoeuvring_anchors.py``.

Reported turning-circle metrics
-------------------------------
``turning_diameter_m`` is the *path-speed* definition ``2 |U| / |r|`` with
``|U| = hypot(u, v)`` (m1651).  The legacy surge-only value ``2 u / |r|`` is
still returned as ``turning_diameter_surge_m``; it understates the diameter by
``1 - cos(beta)``, i.e. 6 % at the ~20 deg steady drift of a 35 deg turn.
``extreme_transverse_diameter_m`` is the geometric measurement
``2 max|y|`` taken directly from the track, which is what a trial report calls
the tactical diameter; comparing it with ``tactical_diameter_m`` (``|y|`` at
180 deg heading change) is a check on the steady-turn assumption.

Validation status: the absolute turning-circle diameter is *not* validated on a
full-scale trial in this worktree.  What is machine-verified (m1651,
``tools/benchmarks/manoeuvring_anchors.py``, evidence in
``docs/audit/manoeuvring_verification_debt.md``) is: straight-run neutrality,
port/starboard mirror symmetry, the closed-form slender-body added masses, the
kinematic identity ``R = |U|/|r|``, RK4 time-step convergence, reproduction by
an independent integrator, Froude scaling of the non-dimensional trajectory and
the IMO Res. A.751(18) threshold checks.  A code-to-code comparison against the
independently derived MMG model in ``backend/physics/ship_env_maneuvering``
leaves a 18-30 % discrepancy on the same hull and rudder angle; that gap is
unresolved and is reported as WITHHELD, not as agreement.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Dict

import numpy as np

from .geometry import HullForm
from .resistance import holtrop_resistance

G = 9.80665


@dataclass
class _MMGCore:
    """Shared force model for every manoeuvre of one hull at one approach speed.

    The two public simulators differ only in the rudder schedule and the
    integrator; all hydrodynamic terms live here so that they cannot drift
    apart again.
    """

    hull: HullForm
    U0: float
    C_D: float = 1.0
    c_rudder: float = 0.017
    gamma_R: float = 0.4
    U_R_factor: float = 0.90
    n_sections: int = 61

    xm: np.ndarray = field(init=False)
    depth: np.ndarray = field(init=False)
    dx: float = field(init=False)
    m: float = field(init=False)
    mx: float = field(init=False)
    my: float = field(init=False)
    Iz: float = field(init=False)
    Jz: float = field(init=False)
    A_R: float = field(init=False)
    x_R: float = field(init=False)
    x_L: float = field(init=False)
    S_ref: float = field(init=False)
    CL_alpha_hull: float = field(init=False)
    C_Lalpha_rudder: float = 2.0 * math.pi
    T0: float = field(init=False)

    def __post_init__(self) -> None:
        h = self.hull
        rho = h.rho
        n = max(3, int(self.n_sections))
        x = np.linspace(-0.5 * h.L, 0.5 * h.L, n)
        self.xm = 0.5 * (x[:-1] + x[1:])
        self.dx = float(x[1] - x[0])
        self.depth = np.full_like(self.xm, h.T)          # rectangular sections

        self.m = h.volume * rho
        self.my = rho * math.pi * float(np.sum(self.depth ** 2)) * self.dx
        self.Jz = rho * math.pi * float(np.sum(self.depth ** 2 * self.xm ** 2)) * self.dx
        self.mx = 0.10 * self.m
        self.Iz = self.m * (0.25 * h.L) ** 2
        self.A_R = self.c_rudder * h.L * h.T
        self.x_R = -0.5 * h.L
        self.x_L = -0.05 * h.L
        self.S_ref = h.L * h.T
        AR = 2.0 * h.T / h.L
        self.CL_alpha_hull = math.pi * AR / 2.0
        self.T0 = self.resistance(self.U0)

    # -- components ---------------------------------------------------------
    def resistance(self, u: float) -> float:
        h = self.hull
        return holtrop_resistance(max(u, 0.5), h.L, h.B, h.T, h.Cb, rho=h.rho)["RT_N"]

    def thrust(self, u: float) -> float:
        return self.T0 * (1.6 - 0.6 * min(u / self.U0, 1.2))

    def hull_lift(self, v: float, r: float, u: float):
        beta = math.atan((v + self.x_L * r) / max(u, 0.3))
        Y = -0.5 * self.hull.rho * u ** 2 * self.S_ref * self.CL_alpha_hull * beta
        return Y, Y * self.x_L

    def rudder(self, v: float, r: float, u: float, delta: float):
        u_eff = max(u, 0.3)
        alpha = delta - self.gamma_R * math.atan((v + self.x_R * r) / u_eff)
        U_R = self.U_R_factor * u_eff
        Y = -0.5 * self.hull.rho * self.A_R * self.C_Lalpha_rudder * alpha * U_R ** 2
        return Y, Y * self.x_R

    # -- equations of motion ------------------------------------------------
    def derivs(self, state: np.ndarray, delta: float) -> np.ndarray:
        _, _, psi, u, v, r = state
        u_eff = max(u, 0.2)
        rho = self.hull.rho
        s = v + self.xm * r
        cf = -0.5 * rho * self.C_D * self.depth * np.abs(s) * s
        Yl, Nl = self.hull_lift(v, r, u)
        Y_H = float(np.sum(cf)) * self.dx + Yl
        N_H = float(np.sum(cf * self.xm)) * self.dx + Nl
        Y_R, N_R = self.rudder(v, r, u, delta)
        X_H = -self.resistance(u)
        du = (X_H + self.thrust(u) + (self.m + self.my) * v * r) / (self.m + self.mx)
        dv = (Y_H + Y_R - (self.m + self.mx) * u_eff * r) / (self.m + self.my)
        dr = (N_H + N_R) / (self.Iz + self.Jz)
        return np.array([u * math.cos(psi) - v * math.sin(psi),
                         u * math.sin(psi) + v * math.cos(psi),
                         r, du, dv, dr])

    def yaw_acceleration(self, state: np.ndarray, delta: float) -> float:
        """r' only - used by the cross-manoeuvre consistency test."""
        return float(self.derivs(state, delta)[5])


def rk4_step(core: _MMGCore, state: np.ndarray, delta: float, h: float) -> np.ndarray:
    k1 = core.derivs(state, delta)
    k2 = core.derivs(state + 0.5 * h * k1, delta)
    k3 = core.derivs(state + 0.5 * h * k2, delta)
    k4 = core.derivs(state + h * k3, delta)
    return state + h / 6.0 * (k1 + 2 * k2 + 2 * k3 + k4)


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
    """Simulate a starboard (or, with ``rudder_deg < 0``, port) turn.

    Returns IMO-style manoeuvring metrics.  Heading is compared through
    ``|psi|`` so that port and starboard turns report identical metrics; before
    m1651 the port case silently returned ``nan`` for advance and tactical
    diameter because the sign convention was hard-coded to starboard.
    """
    core = _MMGCore(hull, U0, C_D=C_D, c_rudder=c_rudder, gamma_R=gamma_R,
                    U_R_factor=U_R_factor, n_sections=n_sections)
    delta = math.radians(rudder_deg)
    state = np.array([0.0, 0.0, 0.0, U0, 0.0, 0.0])
    traj = [state.copy()]
    t = 0.0
    while t < t_end:
        state = rk4_step(core, state, delta, dt)
        t += dt
        traj.append(state.copy())
        if abs(state[2]) >= math.pi:
            break

    traj = np.asarray(traj)
    psi_abs = np.abs(traj[:, 2])
    # advance: x travelled when the heading changed by 90 deg
    idx90 = int(np.argmax(psi_abs >= math.pi / 2)) if np.any(psi_abs >= math.pi / 2) else None
    idx180 = int(np.argmax(psi_abs >= math.pi)) if np.any(psi_abs >= math.pi) else None
    advance = float(traj[idx90, 0]) if idx90 is not None else float("nan")
    tactical = float(abs(traj[idx180, 1])) if idx180 is not None else float("nan")

    u_ss = float(traj[-1, 3])
    v_ss = float(traj[-1, 4])
    r_ss = float(abs(traj[-1, 5]))
    U_ss = math.hypot(u_ss, v_ss)
    D_path = 2.0 * U_ss / r_ss if r_ss > 1e-9 else float("inf")
    D_surge = 2.0 * u_ss / r_ss if r_ss > 1e-9 else float("inf")
    drift = math.degrees(math.atan2(abs(v_ss), abs(u_ss)))
    extreme = 2.0 * float(np.max(np.abs(traj[:, 1])))
    step = max(1, len(traj) // 400)
    return {
        "advance_m": advance,
        "tactical_diameter_m": tactical,
        "tactical_diameter_L": float(tactical / hull.L) if tactical == tactical else float("nan"),
        "advance_L": float(advance / hull.L) if advance == advance else float("nan"),
        "turning_diameter_m": float(D_path),
        "turning_diameter_L": float(D_path / hull.L),
        "turning_diameter_surge_m": float(D_surge),
        "extreme_transverse_diameter_m": extreme,
        "extreme_transverse_diameter_L": float(extreme / hull.L),
        "steady_drift_angle_deg": drift,
        "steady_speed_m_s": u_ss,
        "steady_path_speed_m_s": U_ss,
        "steady_yaw_rate_deg_s": math.degrees(r_ss),
        "speed_loss_pct": float(100.0 * (1.0 - u_ss / U0)),
        "rudder_deg": float(rudder_deg),
        "simulation_time_s": float(t),
        "traj_x": traj[::step, 0].tolist(),
        "traj_y": traj[::step, 1].tolist(),
    }


def zigzag(
    hull: HullForm,
    U0: float,
    rudder_deg: float = 10.0,
    dt: float = 0.1,
    t_end: float = 600.0,
    C_D: float = 1.0,
    c_rudder: float = 0.017,
    gamma_R: float = 0.4,
    U_R_factor: float = 0.90,
    n_sections: int = 61,
) -> Dict[str, float]:
    """``rudder_deg``/``rudder_deg`` zigzag first overshoot angle (IMO A.751 style).

    Integrated with the same :class:`_MMGCore` and the same RK4 stepper as
    :func:`turning_circle` (m1651); the pre-m1651 copy of the equations used a
    forward-Euler step, ``U_R = 0.7 u`` and no hull-lift term.
    """
    core = _MMGCore(hull, U0, C_D=C_D, c_rudder=c_rudder, gamma_R=gamma_R,
                    U_R_factor=U_R_factor, n_sections=n_sections)

    d = math.radians(rudder_deg)
    state = np.array([0.0, 0.0, 0.0, U0, 0.0, 0.0])
    t = 0.0
    stage = 0                  # 0->+delta, 1->overshoot 1, 2->-delta, 3->overshoot 2
    max_psi = -1e9
    min_psi = 1e9
    overshoot = []
    while t < t_end and stage < 4:
        state = rk4_step(core, state, d, dt)
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
        "simulation_time_s": float(t),
        "completed": bool(stage >= 4),
    }