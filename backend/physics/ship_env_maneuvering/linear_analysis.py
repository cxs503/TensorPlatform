"""Closed-form linear analysis of the 3-DOF MMG core (analytic anchor path).

Why this module exists
----------------------
``mmg`` ships a *nonlinear* time-domain solver (RK4 on the full MMG force balance)
plus a Newton steady-state solve.  Neither provides a quantity that can be checked
against pencil-and-paper theory, so the whole manoeuvring chain had no analytic
anchor: every validation row was either model-to-model or model-to-empirical-formula
on the *loads* (wind / current), never on the *response*.

This module adds the missing path.  It linearises the very same force routine used by
the time-domain solver about the straight-ahead equilibrium
``(u, v, r) = (U, 0, 0)``, ``delta = 0`` and then solves the resulting linear system in
closed form:

.. math::

    (m+m_y)\\dot v + (m+m_x) U r &= Y_v v + Y_r r + Y_\\delta \\delta \\\\
    (I_z + J_z)\\dot r            &= N_v v + N_r r + N_\\delta \\delta

Eliminating ``v`` gives the classical second-order heading equation

.. math::

    \\ddot r + p \\dot r + q r = s \\, \\delta

with (all dimensional, straightforward algebra)

.. math::

    p = -\\frac{A N_r + Y_v I_{zz}}{A I_{zz}},\\quad
    q = \\frac{Y_v N_r - C_r N_v}{A I_{zz}},\\quad
    C_r = Y_r - (m+m_x)U,\\quad
    s = \\frac{Y_\\delta N_v - Y_v N_\\delta}{A I_{zz}}

so that the steady gain is ``K = s/q`` and the two time constants follow from
``T1 T2 = 1/q``, ``T1 + T2 = p/q`` (first-order Nomoto reduction
``T \\dot r + r = K \\delta`` uses ``T = T1 + T2``).

Anchors exposed
---------------
* :func:`linear_derivatives` — hydro/control derivatives by central differences of
  ``mmg.manoeuvring_forces`` (so the anchor can never drift away from the code it
  audits), returned both dimensionally and in the module's own non-dimensionalisation.
* :func:`linearised_indices` — ``K``, ``K'``, ``T1/T2/T``, eigenvalues, the course
  stability discriminant ``C = Y_v N_r - N_v (Y_r - (m+m_x)U)`` and its
  Routh-Hurwitz verdict.
* :func:`linear_response` — exact solution of the *two-mode* linear system for a
  rudder step (not the first-order approximation), used as the analytic reference
  for the nonlinear RK4 solver in the small-rudder limit.
* :func:`nomoto_zigzag_overshoot` — closed-form first-order Nomoto overshoot of a
  ``delta/psi`` zigzag (roots of a single transcendental equation), i.e. an analytic
  reference for :func:`mmg.simulate_zigzag`.

Honest limits
-------------
* The linearisation is *exact only in the limit* ``delta -> 0``; deviations at finite
  rudder are a measurable property of the hull, not an error of this module
  (see ``tools/verify/maneuvering_linear/audit.py``).
* Added yaw inertia ``J_z`` is **not** modelled by ``mmg`` (``mmg._rhs`` uses ``I_z``
  alone although its docstring writes ``I_z + J_z``).  ``jz_ratio`` makes that
  omission explicit and quantifiable instead of hiding it.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Dict, Optional, Tuple

from . import data as D
from . import mmg as M

#: Central-difference steps used for the force derivatives (state, then control).
H_V = 1.0e-4        # m/s
H_R = 1.0e-7        # rad/s
H_DELTA = 1.0e-7    # rad


@dataclass(frozen=True)
class LinearDerivatives:
    """Dimensional and non-dimensional linear derivatives of the MMG core."""

    speed: float
    Y_v: float          # N / (m/s)
    Y_r: float          # N / (rad/s)
    Y_delta: float      # N / rad
    N_v: float          # N m / (m/s)
    N_r: float          # N m / (rad/s)
    N_delta: float      # N m / rad
    # non-dimensional (module convention: Y by 0.5 rho L T U^2, N by 0.5 rho L^2 T U^2)
    Y_v_nd: float
    Y_r_nd: float
    Y_delta_nd: float
    N_v_nd: float
    N_r_nd: float
    N_delta_nd: float

    def as_dict(self) -> Dict[str, float]:
        return {k: v for k, v in self.__dict__.items()}


def linear_derivatives(
    vessel: D.Vessel = D.VESSEL_VLCC,
    coef: M.MMGCoefficients = M.KVLCC2_REFERENCE,
    speed: float = 7.717,
    env: Optional[M.Environment] = None,
    n_rps: Optional[float] = None,
    jz_ratio: float = 0.0,
) -> LinearDerivatives:
    """Central-difference linearisation of :func:`mmg.manoeuvring_forces`.

    The equilibrium is straight-ahead running ``u = speed``, ``v = r = delta = 0`` with
    the propeller rate that holds that speed (``mmg.required_rpm`` unless given).
    ``jz_ratio`` is passed through only so that the caller can see which inertia was
    used; it does not enter the derivatives.
    """
    del jz_ratio  # documented for symmetry with the indices; derivatives are inertia-free
    env = env or M.Environment()
    if not env.is_calm:
        raise ValueError("linearisation is defined for calm water only "
                         "(environmental loads are not small-perturbation linear)")

    n = M.required_rpm(vessel, coef, speed) if n_rps is None else n_rps

    def force(v: float, r: float, delta: float) -> Tuple[float, float, float]:
        f = M.manoeuvring_forces((0.0, 0.0, 0.0, speed, v, r), delta, n,
                                 vessel, coef, env)
        return f.X, f.Y, f.N

    _, y0, n0 = force(0.0, 0.0, 0.0)
    _, yp, np_ = force(H_V, 0.0, 0.0)
    _, ym, nm = force(-H_V, 0.0, 0.0)
    y_v = (yp - ym) / (2.0 * H_V)
    n_v = (np_ - nm) / (2.0 * H_V)

    _, yp, np_ = force(0.0, H_R, 0.0)
    _, ym, nm = force(0.0, -H_R, 0.0)
    y_r = (yp - ym) / (2.0 * H_R)
    n_r = (np_ - nm) / (2.0 * H_R)

    _, yp, np_ = force(0.0, 0.0, H_DELTA)
    _, ym, nm = force(0.0, 0.0, -H_DELTA)
    y_d = (yp - ym) / (2.0 * H_DELTA)
    n_d = (np_ - nm) / (2.0 * H_DELTA)

    assert abs(y0) < 1e-6 * max(abs(y_v) * speed, 1.0)  # symmetric hull: Y(0) = 0
    assert abs(n0) < 1e-6 * max(abs(n_v) * speed, 1.0)

    q_lt = 0.5 * D.RHO_WATER * vessel.L * vessel.T * speed * speed
    q_l2t = q_lt * vessel.L
    return LinearDerivatives(
        speed=speed,
        Y_v=y_v, Y_r=y_r, Y_delta=y_d, N_v=n_v, N_r=n_r, N_delta=n_d,
        Y_v_nd=y_v / (q_lt / speed),
        Y_r_nd=y_r / (q_lt * vessel.L / speed),
        Y_delta_nd=y_d / q_lt,
        N_v_nd=n_v / (q_l2t / speed),
        N_r_nd=n_r / (q_l2t * vessel.L / speed),
        N_delta_nd=n_d / q_l2t,
    )


@dataclass(frozen=True)
class LinearIndices:
    """Nomoto indices and course-stability discriminant of the linearised MMG core."""

    speed: float
    length: float
    K: float                 # steady yaw gain [1/s]  : r_ss = K * delta
    K_nd: float              # K * L / U  [-]
    T1: float
    T2: float
    T1_nd: float
    T2_nd: float
    T: float                 # first-order Nomoto time constant T1 + T2 [s]
    T_nd: float              # T * U / L  [-]
    lam1: float
    lam2: float
    p: float
    q: float
    s: float
    stability_C: float       # Y_v N_r - N_v (Y_r - (m+m_x) U)   [N^2 m /s rad]
    stability_C_nd: float    # same combination in non-dimensional form
    routh_hurwitz_stable: bool
    jz_ratio: float


def linearised_indices(
    vessel: D.Vessel = D.VESSEL_VLCC,
    coef: M.MMGCoefficients = M.KVLCC2_REFERENCE,
    speed: float = 7.717,
    derivs: Optional[LinearDerivatives] = None,
    jz_ratio: float = 0.0,
) -> LinearIndices:
    """Nomoto ``K``/``T`` and the course-stability discriminant.

    ``jz_ratio`` multiplies the yaw inertia: ``I_zz = I_z (1 + jz_ratio)``.  It is an
    explicit knob for the fact that ``mmg`` models no added yaw inertia; ``K`` is
    analytically independent of ``I_zz`` and ``T`` is not.
    """
    d = derivs or linear_derivatives(vessel, coef, speed)
    m = vessel.mass
    m_x = coef.m_x * 0.5 * D.RHO_WATER * vessel.L ** 2 * vessel.T
    m_y = coef.m_y * 0.5 * D.RHO_WATER * vessel.L ** 2 * vessel.T
    a = m + m_y
    b = m + m_x
    i_zz = vessel.I_z * (1.0 + jz_ratio)

    c_r = d.Y_r - b * d.speed                       # Y_r - (m+m_x) U
    den = d.Y_v * d.N_r - c_r * d.N_v               # course-stability discriminant
    p = -(a * d.N_r + d.Y_v * i_zz) / (a * i_zz)
    q = den / (a * i_zz)
    s = (d.Y_delta * d.N_v - d.Y_v * d.N_delta) / (a * i_zz)
    k = s / q

    disc = p * p - 4.0 * q
    if disc >= 0.0:
        root = math.sqrt(disc)
        lam1 = (-p + root) / 2.0
        lam2 = (-p - root) / 2.0
    else:                                            # pragma: no cover - complex pair
        lam1 = lam2 = complex(-p / 2.0, math.sqrt(-disc) / 2.0)
    t1 = -1.0 / lam1 if lam1 != 0 else float("inf")
    t2 = -1.0 / lam2 if lam2 != 0 else float("inf")
    t1, t2 = (t2, t1) if abs(getattr(t1, "real", t1)) > abs(getattr(t2, "real", t2)) else (t1, t2)

    m_nd = m / (0.5 * D.RHO_WATER * vessel.L ** 2 * vessel.T)
    c_nd = (d.Y_v_nd * d.N_r_nd
            - d.N_v_nd * (d.Y_r_nd - (m_nd + coef.m_x)))
    return LinearIndices(
        speed=d.speed, length=vessel.L,
        K=k, K_nd=k * vessel.L / d.speed,
        T1=t1, T2=t2,
        T1_nd=t1 * d.speed / vessel.L, T2_nd=t2 * d.speed / vessel.L,
        T=t1 + t2, T_nd=(t1 + t2) * d.speed / vessel.L,
        lam1=lam1, lam2=lam2, p=p, q=q, s=s,
        stability_C=den,
        stability_C_nd=c_nd,
        routh_hurwitz_stable=bool(p > 0.0 and q > 0.0),
        jz_ratio=jz_ratio,
    )


def linear_response(
    vessel: D.Vessel = D.VESSEL_VLCC,
    coef: M.MMGCoefficients = M.KVLCC2_REFERENCE,
    speed: float = 7.717,
    rudder_deg: float = 0.5,
    times=(),
    derivs: Optional[LinearDerivatives] = None,
    jz_ratio: float = 0.0,
) -> Dict[str, list]:
    """Exact two-mode solution of the linearised system for a rudder step.

    Returns ``{"t", "r", "v", "psi"}`` with ``r`` in rad/s, ``v`` in m/s and the
    heading ``psi`` in rad (``psi(0) = 0``, straight-ahead equilibrium).  The
    solution is the *exact* sum of the two modes of
    ``r'' + p r' + q r = s delta`` — not the first-order Nomoto approximation —
    which is what makes it a usable reference for the nonlinear RK4 solver.
    """
    d = derivs or linear_derivatives(vessel, coef, speed)
    idx = linearised_indices(vessel, coef, speed, derivs=d, jz_ratio=jz_ratio)
    m = vessel.mass
    m_x = coef.m_x * 0.5 * D.RHO_WATER * vessel.L ** 2 * vessel.T
    m_y = coef.m_y * 0.5 * D.RHO_WATER * vessel.L ** 2 * vessel.T
    a = m + m_y
    b = m + m_x
    i_zz = vessel.I_z * (1.0 + jz_ratio)

    delta = math.radians(rudder_deg)
    r_ss = idx.K * delta
    r_dot0 = d.N_delta * delta / i_zz

    if isinstance(idx.lam1, complex) or isinstance(idx.lam2, complex):
        # under-damped (oscillatory) root pair: r(t) = r_ss + e^{-a t}(C cos wt + D sin wt)
        alpha = -idx.p / 2.0
        w = math.sqrt(4.0 * idx.q - idx.p * idx.p) / 2.0
        c1 = -r_ss
        c2 = (r_dot0 - alpha * c1) / w
        r = lambda t: r_ss + math.exp(alpha * t) * (c1 * math.cos(w * t) + c2 * math.sin(w * t))
        rdot = lambda t: math.exp(alpha * t) * (
            (alpha * c1 + c2 * w) * math.cos(w * t) + (c2 * alpha - c1 * w) * math.sin(w * t))
    else:
        l1, l2 = float(idx.lam1), float(idx.lam2)
        # r(0) = 0  ->  A1 + A2 = -r_ss ; r'(0) = r_dot0 ->  l1 A1 + l2 A2 = r_dot0
        a1 = (r_dot0 + l2 * r_ss) / (l1 - l2)
        a2 = -r_ss - a1
        r = lambda t: r_ss + a1 * math.exp(l1 * t) + a2 * math.exp(l2 * t)
        rdot = lambda t: l1 * a1 * math.exp(l1 * t) + l2 * a2 * math.exp(l2 * t)

    out = {"t": [], "r": [], "v": [], "psi": []}
    psi = 0.0
    for t in times:
        rt = r(t)
        # v from the yaw equation, exact for the linear system
        vt = (i_zz * rdot(t) - d.N_r * rt - d.N_delta * delta) / d.N_v
        # heading: integral of r, taken from the closed form of each mode
        if not (isinstance(idx.lam1, complex) or isinstance(idx.lam2, complex)):
            l1, l2 = float(idx.lam1), float(idx.lam2)
            psi = (r_ss * t + a1 * (math.exp(l1 * t) - 1.0) / l1
                   + a2 * (math.exp(l2 * t) - 1.0) / l2)
        out["t"].append(t)
        out["r"].append(rt)
        out["v"].append(vt)
        out["psi"].append(psi)
    return out


def nomoto_zigzag_overshoot(
    K: float,
    T: float,
    rudder_deg: float = 10.0,
    heading_deg: float = 10.0,
) -> Dict[str, float]:
    """Closed-form first-order Nomoto overshoot of a ``delta/psi`` zigzag.

    For ``T \\dot r + r = K \\delta`` the execute phase from rest is
    ``r = K \\delta (1 - e^{-t/T})``, ``\\psi = K\\delta [t - T (1 - e^{-t/T})]``, so the
    time to reach the switching heading ``psi_s`` follows from one transcendental
    equation, and the checking phase overshoot is

    .. math::

        \\Delta\\psi_{os} = T\\left[ r_1 - K\\delta\\,
        \\ln\\!\\left(\\frac{r_1 + K\\delta}{K\\delta}\\right)\\right]

    with ``r_1`` the yaw rate at the reversal.  Both branches are evaluated by a
    bracketed bisection, so the result is a *closed-form* reference for
    :func:`mmg.simulate_zigzag` up to root-finding tolerance.
    """
    delta = math.radians(rudder_deg)
    psi_s = math.radians(heading_deg)
    kd = K * delta

    def psi_exec(t: float) -> float:
        return kd * (t - T * (1.0 - math.exp(-t / T)))

    sgn = 1.0 if kd > 0.0 else -1.0        # a negative gain turns to the other side
    target = sgn * psi_s
    lo, hi = 0.0, 10.0 * T + 1.0
    for _ in range(200):
        mid = 0.5 * (lo + hi)
        if sgn * psi_exec(mid) < psi_s:
            lo = mid
        else:
            hi = mid
    t1 = 0.5 * (lo + hi)
    r1 = kd * (1.0 - math.exp(-t1 / T))
    if abs(r1 + kd) <= abs(kd):
        raise ValueError("overshoot requires |r| to grow beyond |K delta| at the reversal")
    overshoot = sgn * T * (r1 - kd * math.log((r1 + kd) / kd))
    return {
        "t_first_reach_s": t1,
        "r_at_reversal_rad_s": r1,
        "first_overshoot_deg": math.degrees(overshoot),
        "K": K, "T": T, "rudder_deg": rudder_deg, "heading_deg": heading_deg,
    }