"""MMG 3-DOF (surge-sway-yaw) ship-manoeuvring model with strip-theory added
mass, Newtonian cross-flow hull forces and an MMG rudder sub-model.

Scope and evidence grade
------------------------
This module implements the *structure* of the MMG standard model (the modular
hull / propeller / rudder decomposition) in the form reviewed by Yasukawa &
Yoshimura (2015, "Introduction of the MMG standard method for ship
manoeuvring predictions", J. Mar. Sci. Technol. 20:37-52).  It deliberately
contains **no transcribed empirical force-derivative table**: the hull forces
are obtained by integrating 2D slender-body (strip) added mass and Newtonian
cross-flow section drag along the hull, and the rudder force uses the classical
cross-flow (Fujii) normal-force model.  Consequently

* the *numerics* (time integration, linearisation, steady-state solution,
  manoeuvre-indicator extraction) are verifiable to machine precision against
  closed-form solutions -> see ``nomoto.py`` and ``tests/feat/test_manoeuvring.py``;
* the *absolute accuracy* of the force model against full-scale trials requires
  an external derivative set / EFD-CFD comparison which is not available
  offline, therefore it is graded WITHHELD (see
  ``docs/verification/MANOEUVRING_MMG_V1.md``).

Every modelling constant below carries its typical physical range so a reviewer
can see what was assumed rather than transcribed.

Sign / frame conventions
------------------------
Body frame: ``x`` forward, ``y`` to starboard, ``z`` down.  ``u`` surge
(forward positive), ``v`` sway (starboard positive), ``r`` yaw rate
(starboard/bow-to-starboard positive), ``psi`` heading, positive turning to
starboard.  Rudder angle ``delta > 0`` = trailing edge to starboard, which
produces ``r > 0``.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Dict, Tuple

G = 9.81

#: Number of Simpson stations used for the hull cross-flow integral.
N_STATIONS = 61


# ---------------------------------------------------------------------------
# Input validation helpers
# ---------------------------------------------------------------------------

def _require(cond: bool, msg: str) -> None:
    if not cond:
        raise ValueError(msg)


@dataclass(frozen=True)
class HullParameters:
    """Principal particulars plus the geometric consistency gate.

    The platform rule "geometry must be verified before any performance claim"
    is enforced here: ``Cb``, ``L/B`` and ``B/T`` are range-checked and, when a
    displacement is supplied, the hydrostatic identity ``Delta = rho*Cb*L*B*T``
    must close to within ``disp_tolerance`` (default 3 %).
    """

    length: float                 # L, m
    beam: float                   # B, m
    draft: float                  # T, m
    cb: float                     # block coefficient
    design_speed: float           # U0, m/s
    displacement_t: float | None = None
    water_density: float = 1025.0
    form_factor: float = 1.15     # (1+k) in the ITTC-57 friction line
    radius_of_gyration_ratio: float = 0.25  # k_zz / L, typical 0.24-0.26
    disp_tolerance: float = 0.03

    def __post_init__(self) -> None:
        _require(self.length > 0 and self.beam > 0 and self.draft > 0,
                 "L, B, T must be positive")
        _require(0.35 <= self.cb <= 0.90,
                 f"Cb={self.cb} outside supported range [0.35, 0.90]")
        lb = self.length / self.beam
        _require(3.0 <= lb <= 12.0, f"L/B={lb:.2f} outside plausible range [3, 12]")
        bt = self.beam / self.draft
        _require(1.5 <= bt <= 6.5, f"B/T={bt:.2f} outside plausible range [1.5, 6.5]")
        _require(self.design_speed > 0, "design_speed must be positive")
        _require(self.water_density > 0, "water_density must be positive")
        if self.displacement_t is not None:
            vol = self.cb * self.length * self.beam * self.draft
            disp_calc = self.water_density * vol / 1000.0
            err = abs(disp_calc - self.displacement_t) / self.displacement_t
            _require(err <= self.disp_tolerance,
                     "geometry inconsistent: rho*Cb*L*B*T = "
                     f"{disp_calc:.1f} t vs stated {self.displacement_t:.1f} t "
                     f"({100 * err:.2f}% > {100 * self.disp_tolerance:.1f}% tolerance)")

    @property
    def mass(self) -> float:
        """Displacement mass, kg."""
        return self.water_density * self.cb * self.length * self.beam * self.draft

    @property
    def froude(self) -> float:
        return self.design_speed / math.sqrt(G * self.length)


@dataclass(frozen=True)
class ModelParameters:
    """Modelling constants (declared, with typical ranges documented)."""

    # -- hull cross-flow ---------------------------------------------------
    cd_cross: float = 1.0          # Hoerner-type cross-flow drag, typical 0.5-1.3
    k_added_sway: float = 1.0      # m_y = k*rho*pi*(T/2)^2*L, typical 0.8-1.6
    k_added_surge: float = 0.05    # m_x / m, typical 0.03-0.10
    munk_factor: float = 1.0       # 0 = pure cross-flow hull, 1 = full Munk moment
    # -- rudder (MMG interaction coefficients, typical ranges in brackets) --
    rudder_area_ratio: float = 0.01   # A_R / (L*T), typical 0.010-0.016
    rudder_aspect: float = 1.8        # h_R^2 / A_R, typical 1.5-2.0
    f_alpha: float = 2.6           # rudder lift gradient, typical 2.0-3.5
    t_r: float = 0.25              # rudder thrust deduction, typical 0.20-0.30
    a_h: float = 0.30              # rudder-hull interaction, typical 0.25-0.35
    x_h_ratio: float = -0.40       # x_H / L, typical -0.35..-0.45
    x_r_ratio: float = -0.50       # x_R / L, typical -0.48..-0.52
    w_r: float = 0.25              # rudder wake fraction, typical 0.20-0.40
    # -- propeller ---------------------------------------------------------
    thrust_ratio: float = 1.0      # thrust / R_T(U0); 1.0 == self-propulsion at U0


class MMGModel:
    """Three-degree-of-freedom MMG manoeuvring model.

    State vector: ``(u, v, r, x, y, psi)``.  Equations (MMG standard, deep
    water, no heel coupling)::

        (m + m_x) u_dot - (m + m_y) v r = X_H + X_P + X_R
        (m + m_y) v_dot + (m + m_x) u r = Y_H + Y_R
        (I_zz + J_zz) r_dot            = N_H + N_R + N_munk
        x_dot = u cos(psi) - v sin(psi)
        y_dot = u sin(psi) + v cos(psi)
        psi_dot = r

    ``X_H`` contains the still-water resistance ``R_T(u)`` plus the *increment*
    from cross-flow (so that the straight-course case reduces exactly to
    ``R_T(u0) == thrust``), ``Y_H/N_H`` are the Newtonian cross-flow integrals,
    ``N_munk = -c (m_y - m_x) u v`` is the potential-flow (Munk) destabilising
    yaw moment and ``X_R/Y_R/N_R`` follow the MMG rudder decomposition.
    """

    def __init__(self, hull: HullParameters, params: ModelParameters | None = None) -> None:
        self.hull = hull
        self.p = params or ModelParameters()
        L, B, T, cb = hull.length, hull.beam, hull.draft, hull.cb
        rho = hull.water_density
        m = hull.mass

        # -- masses and inertia -------------------------------------------
        self.m = m
        self.m_x = self.p.k_added_surge * m
        self.m_y = self.p.k_added_sway * rho * math.pi * (T / 2.0) ** 2 * L
        self.I_zz = m * (hull.radius_of_gyration_ratio * L) ** 2
        # J_zz from the same uniform strip distribution used for m_y
        self.J_zz = self.m_y * L ** 2 / 12.0

        # -- rudder geometry ----------------------------------------------
        self.A_R = self.p.rudder_area_ratio * L * T
        self.x_R = self.p.x_r_ratio * L
        self.x_H = self.p.x_h_ratio * L

        # -- still-water resistance level (sets the thrust magnitude) -----
        # ITTC-57 friction with form factor on an ITTC-78 style wetted surface
        # estimate; only the *magnitude* of R_T(U0) matters for the standard
        # manoeuvre (constant-RPM assumption), not its internal split.
        S = L * (2.0 * T + B) * math.sqrt(cb)
        U0 = hull.design_speed
        Re = U0 * L / 1.19e-6
        cf = 0.075 / (math.log10(Re) - 2.0) ** 2
        self.r_t0 = 0.5 * rho * S * cf * hull.form_factor * U0 ** 2
        self.u0 = U0

    # ------------------------------------------------------------------
    # force model
    # ------------------------------------------------------------------
    def resistance(self, u: float) -> float:
        """Still-water resistance ``R_T(u)``, quadratic in speed."""
        return self.r_t0 * (u / self.u0) ** 2 * (1.0 if u >= 0 else -1.0)

    def hull_forces(self, u: float, v: float, r: float) -> Tuple[float, float, float]:
        """Newtonian cross-flow hull forces ``(X_H, Y_H, N_H)``."""
        L = self.hull.length
        T = self.hull.draft
        rho = self.hull.water_density
        cd = self.p.cd_cross
        half = 0.5 * L
        n = N_STATIONS
        dx = L / (n - 1)

        # composite Simpson weights
        w = [1.0] * n
        for i in range(1, n - 1):
            w[i] = 4.0 if i % 2 == 1 else 2.0
        scale = dx / 3.0

        x_cf = y_cf = n_cf = 0.0
        for i in range(n):
            xs = -half + i * dx
            v_local = v + r * xs
            speed = math.hypot(u, v_local)
            # section drag along the local flow direction, 0.5*rho*cd*T*|V|*V
            q = 0.5 * rho * cd * T * speed * scale * w[i]
            # X increment only (the zero-drift part is already R_T(u))
            x_cf += -q * (speed - abs(u)) * (1.0 if u >= 0 else -1.0)
            fy = -q * v_local
            y_cf += fy
            n_cf += xs * fy

        x_h = -self.resistance(u) + x_cf
        return x_h, y_cf, n_cf

    def munk_moment(self, u: float, v: float) -> float:
        """Potential-flow (Munk) destabilising yaw moment, N*m.

        A body at positive sway ``v`` (drifting to starboard) experiences a
        moment that *increases* the drift angle, i.e. turns the bow to port
        (``N < 0``) -- the classical destabilising Munk moment.
        """
        return -self.p.munk_factor * (self.m_y - self.m_x) * u * v

    def rudder_forces(self, u: float, v: float, r: float, delta: float) -> Tuple[float, float, float]:
        """MMG rudder force decomposition ``(X_R, Y_R, N_R)``, Fujii normal force."""
        rho = self.hull.water_density
        u_r = u * (1.0 - self.p.w_r)
        v_r = v + self.x_R * r
        speed_r = math.hypot(u_r, v_r)
        if speed_r < 1e-9:
            return 0.0, 0.0, 0.0
        alpha_r = delta - math.atan2(v_r, u_r)
        f_n = 0.5 * rho * self.A_R * speed_r ** 2 * self.p.f_alpha * math.sin(alpha_r)
        x_r = -(1.0 - self.p.t_r) * f_n * math.sin(delta)
        y_r = -(1.0 + self.p.a_h) * f_n * math.cos(delta)
        n_r = -(self.x_R + self.p.a_h * self.x_H) * f_n * math.cos(delta)
        return x_r, y_r, n_r

    def forces(self, u: float, v: float, r: float, delta: float) -> Tuple[float, float, float]:
        x_h, y_h, n_h = self.hull_forces(u, v, r)
        x_r, y_r, n_r = self.rudder_forces(u, v, r, delta)
        thrust = self.p.thrust_ratio * self.r_t0
        n_munk = self.munk_moment(u, v)
        return (x_h + x_r + thrust, y_h + y_r, n_h + n_r + n_munk)

    # ------------------------------------------------------------------
    # equations of motion
    # ------------------------------------------------------------------
    def derivatives(self, state, delta: float):
        """Time derivative of ``(u, v, r, x, y, psi)`` for rudder angle ``delta``."""
        u, v, r, x, y, psi = state
        fx, fy, fn = self.forces(u, v, r, delta)
        m, m_x, m_y = self.m, self.m_x, self.m_y
        u_dot = (fx + (m + m_y) * v * r) / (m + m_x)
        v_dot = (fy - (m + m_x) * u * r) / (m + m_y)
        r_dot = fn / (self.I_zz + self.J_zz)
        return (u_dot, v_dot, r_dot,
                u * math.cos(psi) - v * math.sin(psi),
                u * math.sin(psi) + v * math.cos(psi),
                r)

    def step(self, state, delta: float, dt: float):
        """One classical RK4 step."""
        k1 = self.derivatives(state, delta)
        s2 = tuple(s + 0.5 * dt * k for s, k in zip(state, k1))
        k2 = self.derivatives(s2, delta)
        s3 = tuple(s + 0.5 * dt * k for s, k in zip(state, k2))
        k3 = self.derivatives(s3, delta)
        s4 = tuple(s + dt * k for s, k in zip(state, k3))
        k4 = self.derivatives(s4, delta)
        return tuple(s + dt / 6.0 * (a + 2.0 * b + 2.0 * c + d)
                     for s, a, b, c, d in zip(state, k1, k2, k3, k4))

    def initial_state(self):
        """Design speed, zero drift, zero yaw, origin, heading zero."""
        return (self.u0, 0.0, 0.0, 0.0, 0.0, 0.0)

    # ------------------------------------------------------------------
    # linearised model (used by the analytic cross-checks)
    # ------------------------------------------------------------------
    def linear_system(self, delta0: float = 0.0, u0: float | None = None) -> Dict[str, float]:
        """State matrix / input vector of the linearised sway-yaw system.

        Returns ``A`` (2x2, states ``v, r``) and ``b`` (2,) for the rudder
        input, i.e. ``d/dt [v, r]^T = A [v, r]^T + b delta``.
        """
        u = self.u0 if u0 is None else u0
        h = 1e-6 * max(1.0, u)

        def f(v: float, r: float) -> Tuple[float, float]:
            _, fy, fn = self.forces(u, v, r, delta0)
            m, m_x, m_y = self.m, self.m_x, self.m_y
            v_dot = (fy - (m + m_x) * u * r) / (m + m_y)
            r_dot = fn / (self.I_zz + self.J_zz)
            return v_dot, r_dot

        f0 = f(0.0, 0.0)
        fv = f(h, 0.0)
        fr = f(0.0, h)
        a11 = (fv[0] - f0[0]) / h
        a21 = (fv[1] - f0[1]) / h
        a12 = (fr[0] - f0[0]) / h
        a22 = (fr[1] - f0[1]) / h
        # rudder input: derivative w.r.t. delta at delta = 0
        hd = 1e-7
        fp = f(0.0, 0.0)
        _, fy_d, fn_d = self.forces(u, 0.0, 0.0, hd)
        m, m_x, m_y = self.m, self.m_x, self.m_y
        b1 = ((fy_d - (m + m_x) * u * 0.0) / (m + m_y)) / hd
        b2 = (fn_d / (self.I_zz + self.J_zz)) / hd
        return {"a11": a11, "a12": a12, "a21": a21, "a22": a22, "b1": b1, "b2": b2}


@dataclass
class ManoeuvreTrace:
    """Time series of a simulated manoeuvre."""

    t: list = field(default_factory=list)
    u: list = field(default_factory=list)
    v: list = field(default_factory=list)
    r: list = field(default_factory=list)
    x: list = field(default_factory=list)
    y: list = field(default_factory=list)
    psi: list = field(default_factory=list)
    delta: list = field(default_factory=list)

    def append(self, t, state, delta) -> None:
        self.t.append(t)
        self.u.append(state[0])
        self.v.append(state[1])
        self.r.append(state[2])
        self.x.append(state[3])
        self.y.append(state[4])
        self.psi.append(state[5])
        self.delta.append(delta)