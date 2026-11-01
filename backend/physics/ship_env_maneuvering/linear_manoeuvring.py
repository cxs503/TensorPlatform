"""Linearised manoeuvring theory and analytic references (verification module).

Motivation
----------
:mod:`mmg` provides a nonlinear 3-DOF MMG time-domain model plus an independent
steady-state equilibrium solver, so the *converged* turning circle is already
checked.  Two things were not:

* the **transient** behaviour of the time integrator (no analytical reference),
* the **zig-zag overshoot angles**, which were only checked for "plausible"
  magnitude -- i.e. an unverified metric.

This module supplies the missing references:

1. :func:`linear_system` -- linearised surge/sway/yaw system about the
   straight-ahead equilibrium, obtained by central differences of the *same*
   force assembly used by the time-domain model (no second implementation of the
   force model; the reference is the linearisation of the implemented physics).
2. :func:`nomoto_coefficients` -- Nomoto first/second-order manoeuvring indices
   ``K``, ``T``, ``T1..T3`` in closed form from that system
   (Nomoto et al. 1957; Yasukawa & Yoshimura 2015, *Introduction to the MMG
   model*), plus the directional-stability discriminant derived below.
3. :func:`linear_step_response`, :func:`linear_turning_reference` -- **exact**
   (matrix-exponential) solution of the linear system: the analytical limit the
   nonlinear solver must contract to as the rudder amplitude vanishes.
4. :func:`nomoto_zigzag_analytic` -- closed-form ``delta/psi`` zig-zag metrics of
   the first-order Nomoto model (derivation below), checked against direct
   integration of the same ODE and used to quantify the first-order
   approximation error against the second-order linear model.
5. :func:`amplitude_convergence` -- contraction study: the discrepancy between
   the nonlinear model and the exact linear solution must vanish at first order
   in the rudder amplitude.

Closed-form first-order Nomoto zig-zag (derivation)
---------------------------------------------------
``T dr/dt + r = K delta``, ``dpsi/dt = r``:

* execute leg (rudder ``+delta`` from rest): ``r = K d (1-e^{-t/T})``,
  ``psi = K d [t - T(1-e^{-t/T})]``, ends at ``t1`` where ``psi = psi0``;
* counter-rudder ``-delta`` from ``(psi0, r1)``: with ``tau = t - t1``,
  ``r = -K d + (r1 + K d) e^{-tau/T}``; the yaw rate vanishes at
  ``tau2 = T ln[(r1 + K d)/(K d)]``, hence
  ``psi_os = psi0 - K d tau2 + (r1 + K d) T (1 - K d/(r1 + K d))``;
* the second execute/check pair is the same construction mirrored from
  ``(psi_os, 0)``.

Directional-stability discriminant
----------------------------------
With ``x = (v, r)``, ``dx/dt = A x + b delta``, the module's
non-dimensionalisation (forces ``0.5 rho L T U^2``, moments
``0.5 rho L^2 T U^2``, ``v' = v/U``, ``r' = r L/U``, masses
``0.5 rho L^2 T``) makes ``det A`` proportional to

``C = Y'_v N'_r - N'_v (Y'_r - m' - m'_x)``,

and ``C > 0`` together with ``tr A < 0`` is equivalent to both eigenvalues
having negative real parts, i.e. a directionally stable ship
(:func:`nomoto_coefficients` returns ``C_nomoto``; the tests assert the
equivalence with the eigenvalue criterion).

Limitations
-----------
* The references are exact for the **linearised** system only.  The nonlinear MMG
  model additionally contains quadratic velocity terms and rudder/propeller
  inflow feedback, so the nonlinear comparison is a *contraction test*
  (first-order decay of the discrepancy with rudder amplitude), not a validation
  against full-scale trials.
* Surge is decoupled in the linear references (``u = u0``); the speed loss in a
  turn is ``O(delta^2)`` and therefore below the contraction error.
* Added yaw inertia ``J_z`` is not part of the parent model (``I_z`` only); the
  linear system inherits that convention instead of silently changing it.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence, Tuple

from . import data as D
from . import mmg as M

Matrix = List[List[float]]
Vector = List[float]

#: finite-difference steps of the linearisation (central differences of the
#: force assembly; small but well above the double-precision noise floor)
FD_U = 1.0e-4          # [m/s]
FD_V = 1.0e-4          # [m/s]
FD_R = 1.0e-7          # [1/s]
FD_RUDDER = 1.0e-4     # [rad]


# ---------------------------------------------------------------------------
# Minimal dense linear algebra (keeps the module numpy/scipy free so the pure
# physics tests do not need the FastAPI service stack)
# ---------------------------------------------------------------------------
def _mat_add(a: Matrix, b: Matrix) -> Matrix:
    return [[x + y for x, y in zip(ra, rb)] for ra, rb in zip(a, b)]


def _mat_scale(a: Matrix, s: float) -> Matrix:
    return [[x * s for x in row] for row in a]


def _mat_mul(a: Matrix, b: Matrix) -> Matrix:
    n, m, p = len(a), len(b), len(b[0])
    return [[sum(a[i][k] * b[k][j] for k in range(m)) for j in range(p)]
            for i in range(n)]


def _mat_vec(a: Matrix, x: Vector) -> Vector:
    return [sum(aij * xj for aij, xj in zip(row, x)) for row in a]


def _identity(n: int) -> Matrix:
    return [[1.0 if i == j else 0.0 for j in range(n)] for i in range(n)]


def expm(a: Matrix, t: float) -> Matrix:
    """Matrix exponential ``exp(a t)`` by scaling-and-squaring with Taylor series.

    The argument is scaled so that the infinity norm of ``a t`` is below 0.5 and
    the Taylor series is summed to 20 terms before squaring back; for the 2x2 and
    3x3 systems used here the truncation error sits at the rounding level
    (cross-checked against the closed-form 2x2 eigen solution in the tests).
    """
    n = len(a)
    scale = max((sum(abs(x) for x in row) for row in a), default=0.0) * abs(t)
    squarings = 0
    while scale > 0.5:
        scale *= 0.5
        squarings += 1
    factor = t / (2.0 ** squarings)

    term = _identity(n)
    result = _identity(n)
    scaled = _mat_scale(a, factor)
    for k in range(1, 21):
        term = _mat_scale(_mat_mul(term, scaled), 1.0 / k)
        result = _mat_add(result, term)
    for _ in range(squarings):
        result = _mat_mul(result, result)
    return result


def solve(a: Matrix, b: Vector) -> Vector:
    """Solve ``a x = b`` by Gauss elimination with partial pivoting."""
    n = len(a)
    m = [list(row) + [bi] for row, bi in zip(a, b)]
    for col in range(n):
        pivot = max(range(col, n), key=lambda r: abs(m[r][col]))
        if abs(m[pivot][col]) < 1e-300:
            raise ValueError("singular system")
        m[col], m[pivot] = m[pivot], m[col]
        inv = 1.0 / m[col][col]
        for r in range(col + 1, n):
            f = m[r][col] * inv
            if f:
                for c in range(col, n + 1):
                    m[r][c] -= f * m[col][c]
    x = [0.0] * n
    for i in range(n - 1, -1, -1):
        x[i] = (m[i][n] - sum(m[i][j] * x[j] for j in range(i + 1, n))) / m[i][i]
    return x


def inverse(a: Matrix) -> Matrix:
    """Matrix inverse via one Gauss elimination per unit vector."""
    n = len(a)
    cols = [solve(a, [1.0 if i == j else 0.0 for i in range(n)]) for j in range(n)]
    return [[cols[j][i] for j in range(n)] for i in range(n)]


# ---------------------------------------------------------------------------
# Linearised system
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class LinearModel:
    """Linearised manoeuvring system about the straight-ahead equilibrium."""

    speed: float                 # equilibrium advance speed u0 [m/s]
    n_rps: float                 # equilibrium shaft rate [1/s]
    mass: float                  # rigid mass m [kg]
    m_x: float                   # added mass, surge [kg]
    m_y: float                   # added mass, sway [kg]
    i_z: float                   # yaw inertia (parent convention: I_z only)
    # dimensional derivatives (FD of the force assembly)
    X_u: float
    X_v: float
    X_r: float
    X_delta: float
    Y_v: float
    Y_r: float
    Y_delta: float
    N_v: float
    N_r: float
    N_delta: float
    # state space: state (v, r) and (u, v, r); input rudder [rad]
    a2: Tuple[Tuple[float, float], Tuple[float, float]]
    b2: Tuple[float, float]
    a3: Tuple[Tuple[float, float, float], ...]
    b3: Tuple[float, float, float]

    def a2_list(self) -> Matrix:
        return [list(row) for row in self.a2]

    def a3_list(self) -> Matrix:
        return [list(row) for row in self.a3]

    def eigenvalues2(self) -> Tuple[complex, complex]:
        (a11, a12), (a21, a22) = self.a2
        half_trace = 0.5 * (a11 + a22)
        disc = complex(half_trace ** 2 - (a11 * a22 - a12 * a21), 0.0)
        root = disc ** 0.5
        return half_trace + root, half_trace - root

    def is_course_stable(self) -> bool:
        """True if both sway/yaw eigenvalues have negative real parts."""
        l1, l2 = self.eigenvalues2()
        return l1.real < 0.0 and l2.real < 0.0

    def steady_state(self, rudder_deg: float) -> Tuple[float, float]:
        """Steady ``(v, r)`` of the linear system under a constant rudder angle."""
        delta = math.radians(rudder_deg)
        x = solve(self.a2_list(), [-c * delta for c in self.b2])
        return x[0], x[1]


def linear_system(
    vessel: D.Vessel = D.VESSEL_VLCC,
    coef: M.MMGCoefficients = M.KVLCC2_REFERENCE,
    speed: float = 7.717,
    env: Optional[M.Environment] = None,
) -> LinearModel:
    """Linearise the MMG force assembly about the straight-ahead equilibrium.

    Base state ``(x, y, psi, u, v, r) = (0, 0, 0, speed, 0, 0)`` with the shaft
    rate that balances calm-water resistance (:func:`mmg.required_rpm`).
    Derivatives are central differences of :func:`mmg.manoeuvring_forces`, so the
    linear model is exactly consistent with the implemented force model.

    Equations of motion (``mmg._rhs`` with quadratic velocity products dropped)::

        (m+m_x) du/dt = X_u du + X_v dv + X_r dr + X_delta delta
        (m+m_y) dv/dt = Y_v dv + Y_r dr - (m+m_x) u0 dr + Y_delta delta
        I_z dr/dt     = N_v dv + N_r dr + N_delta delta
    """
    env = env or M.Environment()
    n_rps = M.required_rpm(vessel, coef, speed)
    base = (0.0, 0.0, 0.0, speed, 0.0, 0.0)

    def f(state, delta):
        return M.manoeuvring_forces(state, delta, n_rps, vessel, coef, env)

    def d_dx(index: int, step: float) -> Tuple[float, float, float]:
        plus, minus = list(base), list(base)
        plus[index] += step
        minus[index] -= step
        fp = f(tuple(plus), 0.0)
        fm = f(tuple(minus), 0.0)
        return ((fp.X - fm.X) / (2 * step),
                (fp.Y - fm.Y) / (2 * step),
                (fp.N - fm.N) / (2 * step))

    X_u, _, _ = d_dx(3, FD_U)
    X_v, Y_v, N_v = d_dx(4, FD_V)
    X_r, Y_r, N_r = d_dx(5, FD_R)
    fp, fm = f(base, FD_RUDDER), f(base, -FD_RUDDER)
    X_d = (fp.X - fm.X) / (2 * FD_RUDDER)
    Y_d = (fp.Y - fm.Y) / (2 * FD_RUDDER)
    N_d = (fp.N - fm.N) / (2 * FD_RUDDER)

    m = vessel.mass
    mass_scale = 0.5 * D.RHO_WATER * vessel.L ** 2 * vessel.T
    m_x = coef.m_x * mass_scale
    m_y = coef.m_y * mass_scale
    i_z = vessel.I_z

    a2 = ((Y_v / (m + m_y), (Y_r - (m + m_x) * speed) / (m + m_y)),
          (N_v / i_z, N_r / i_z))
    b2 = (Y_d / (m + m_y), N_d / i_z)
    a3 = ((X_u / (m + m_x), X_v / (m + m_x), X_r / (m + m_x)),
          (0.0, a2[0][0], a2[0][1]),
          (0.0, a2[1][0], a2[1][1]))
    b3 = (X_d / (m + m_x), b2[0], b2[1])

    return LinearModel(
        speed=speed, n_rps=n_rps, mass=m, m_x=m_x, m_y=m_y, i_z=i_z,
        X_u=X_u, X_v=X_v, X_r=X_r, X_delta=X_d,
        Y_v=Y_v, Y_r=Y_r, Y_delta=Y_d,
        N_v=N_v, N_r=N_r, N_delta=N_d,
        a2=a2, b2=b2, a3=a3, b3=b3,
    )


# ---------------------------------------------------------------------------
# Nomoto indices
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class NomotoIndices:
    """Nomoto manoeuvring indices derived from the linearised sway/yaw system."""

    k: float                  # gain [1/s]: r_ss = K delta
    t_first: float            # first-order time constant T [s]
    t1: float
    t2: float
    t3: float
    det: float
    trace: float
    stable: bool
    oscillatory: bool
    eigenvalue_real: float    # dominant eigenvalue (largest real part)
    eigenvalue_imag: float
    c_nomoto: float
    derivatives: Dict[str, float]

    def as_dict(self) -> Dict[str, object]:
        row: Dict[str, object] = {
            "K_1_s": self.k, "T_first_s": self.t_first,
            "T1_s": self.t1, "T2_s": self.t2, "T3_s": self.t3,
            "det_A2": self.det, "trace_A2": self.trace,
            "course_stable": self.stable, "oscillatory": self.oscillatory,
            "eigen_real_1_s": self.eigenvalue_real,
            "eigen_imag_1_s": self.eigenvalue_imag,
            "C_nomoto": self.c_nomoto,
        }
        row.update({f"d_{k}": v for k, v in self.derivatives.items()})
        return row


def nomoto_coefficients(model: LinearModel,
                        vessel: D.Vessel = D.VESSEL_VLCC) -> NomotoIndices:
    """Nomoto ``K``, ``T``, ``T1..T3`` from the linearised system.

    With ``x = (v, r)`` and ``dx/dt = A x + b delta`` the yaw-rate transfer
    function is::

        r/delta (s) = [b2 s + (a21 b1 - a11 b2)] / (s^2 - tr A s + det A)
                    = K (1 + T3 s) / [(1 + T1 s)(1 + T2 s)]

    hence ``K = (a21 b1 - a11 b2)/det A``, ``T3 = b2/(a21 b1 - a11 b2)``,
    ``T1 T2 = 1/det A``, ``T1 + T2 = -tr A/det A`` and the first-order
    approximation ``T = T1 + T2 - T3`` (Nomoto et al. 1957; Yasukawa &
    Yoshimura 2015).
    """
    (a11, a12), (a21, a22) = model.a2
    b1, b2 = model.b2
    det = a11 * a22 - a12 * a21
    trace = a11 + a22
    if abs(det) < 1e-300:                                   # pragma: no cover
        raise ValueError("singular sway/yaw system")
    num_const = a21 * b1 - a11 * b2
    k = num_const / det
    t3 = b2 / num_const if abs(num_const) > 1e-300 else float("inf")
    sum_t = -trace / det
    prod_t = 1.0 / det
    disc = sum_t ** 2 - 4.0 * prod_t
    oscillatory = disc < 0.0
    if oscillatory:                                         # pragma: no cover
        t1 = t2 = 0.5 * sum_t
    else:
        root = math.sqrt(disc)
        t1, t2 = 0.5 * (sum_t + root), 0.5 * (sum_t - root)
    t_first = t1 + t2 - t3

    u = model.speed
    q_lt = 0.5 * D.RHO_WATER * vessel.L * vessel.T * u * u
    q_l2t = q_lt * vessel.L
    q_l3t = q_l2t * vessel.L
    mass_scale = 0.5 * D.RHO_WATER * vessel.L ** 2 * vessel.T
    m_p = model.mass / mass_scale
    mx_p = model.m_x / mass_scale
    my_p = model.m_y / mass_scale
    y_v = model.Y_v / (q_lt / u)
    y_r = model.Y_r / (q_l2t / u)
    n_v = model.N_v / (q_l2t / u)
    n_r = model.N_r / (q_l3t / u)
    c_nomoto = y_v * n_r - n_v * (y_r - m_p - mx_p)

    l1, l2 = model.eigenvalues2()
    dominant = l1 if l1.real >= l2.real else l2
    derivatives = {
        "X_u": model.X_u, "X_v": model.X_v, "X_r": model.X_r,
        "Y_v": model.Y_v, "Y_r": model.Y_r, "N_v": model.N_v,
        "N_r": model.N_r, "Y_delta": model.Y_delta, "N_delta": model.N_delta,
        "X_delta": model.X_delta,
        "Y_v_prime": y_v, "Y_r_prime": y_r,
        "N_v_prime": n_v, "N_r_prime": n_r,
        "m_prime": m_p, "m_x_prime": mx_p, "m_y_prime": my_p,
    }
    return NomotoIndices(k=k, t_first=t_first, t1=t1, t2=t2, t3=t3,
                         det=det, trace=trace,
                         stable=(l1.real < 0.0 and l2.real < 0.0),
                         oscillatory=oscillatory,
                         eigenvalue_real=dominant.real,
                         eigenvalue_imag=dominant.imag,
                         c_nomoto=c_nomoto, derivatives=derivatives)


# ---------------------------------------------------------------------------
# Exact linear responses
# ---------------------------------------------------------------------------
def linear_step_response(model: LinearModel, rudder_deg: float,
                         t_end: float, dt: float) -> Dict[str, List[float]]:
    """Exact step response of the linearised sway/yaw system plus heading.

    ``x(t) = x_ss - e^{A t} x_ss`` with ``x_ss = -A^{-1} b delta`` and
    ``psi(t) = x_ss,r t + [A^{-1} x(t)]_r`` (exact, since ``dpsi/dt = r``).  The
    matrix exponential is advanced incrementally
    (``e^{A(t+dt)} = e^{A dt} e^{A t}``), so the cost is one 2x2 multiply per
    sample instead of one exponential.
    """
    a = model.a2_list()
    delta = math.radians(rudder_deg)
    steady = [-delta * c for c in solve(a, list(model.b2))]
    a_inv = inverse(a)
    e_dt = expm(a, dt)
    steps = int(round(t_end / dt))
    out: Dict[str, List[float]] = {k: [] for k in ("t", "v", "r", "u", "psi_deg")}
    decay = _identity(2)
    for i in range(steps + 1):
        moved = _mat_vec(decay, steady)
        v = steady[0] - moved[0]
        r = steady[1] - moved[1]
        t = i * dt
        psi = steady[1] * t + _mat_vec(a_inv, [v, r])[1]
        out["t"].append(t)
        out["v"].append(v)
        out["r"].append(r)
        out["u"].append(model.speed)
        out["psi_deg"].append(math.degrees(psi))
        decay = _mat_mul(e_dt, decay)
    return out


@dataclass(frozen=True)
class LinearTurningMetrics:
    """Turning-circle metrics of the exact linearised solution."""

    yaw_rate_degs: float
    turning_radius: float
    diameter_over_l: float
    advance: float
    transfer: float
    tactical: float
    speed_steady: float
    time_to_first_heading: float

    def as_dict(self) -> Dict[str, float]:
        return {
            "yaw_rate_degs": self.yaw_rate_degs,
            "turning_radius_m": self.turning_radius,
            "diameter_over_L": self.diameter_over_l,
            "advance_m": self.advance,
            "transfer_m": self.transfer,
            "tactical_m": self.tactical,
            "speed_steady": self.speed_steady,
            "time_to_first_heading_s": self.time_to_first_heading,
        }


def linear_turning_reference(model: LinearModel,
                             vessel: D.Vessel = D.VESSEL_VLCC,
                             rudder_deg: float = 1.0,
                             t_end: float = 4000.0,
                             dt: float = 0.05) -> LinearTurningMetrics:
    """Parameters of the exact linear turning circle (IMO trial definitions).

    ``advance`` and ``transfer`` are the coordinates at ``psi = 90 deg``,
    ``tactical`` the sway displacement at ``psi = 180 deg``, matching
    :func:`mmg.simulate_turning_circle`.  The kinematics
    ``dx/dt = u cos psi - v sin psi``, ``dy/dt = u sin psi + v cos psi`` are
    integrated with the trapezoidal rule on the exact trajectories, so the only
    error is quadrature of a smooth curve (< 1e-6 at ``dt = 0.05 s``).
    """
    resp = linear_step_response(model, rudder_deg, t_end, dt)
    u0 = model.speed
    x = y = 0.0
    advance = transfer = tactical = None
    t90 = None
    prev_v = None
    psi_prev = 0.0
    for i, t in enumerate(resp["t"]):
        psi = math.radians(resp["psi_deg"][i])
        v = resp["v"][i]
        if prev_v is not None:
            psi_mid = 0.5 * (psi + psi_prev)
            v_mid = 0.5 * (v + prev_v)
            x += dt * (u0 * math.cos(psi_mid) - v_mid * math.sin(psi_mid))
            y += dt * (u0 * math.sin(psi_mid) + v_mid * math.cos(psi_mid))
        if advance is None and resp["psi_deg"][i] >= 90.0:
            advance, transfer, t90 = x, y, t
        if tactical is None and resp["psi_deg"][i] >= 180.0:
            tactical = y
            break
        prev_v = v
        psi_prev = psi
    v_ss, r_ss = model.steady_state(rudder_deg)
    speed_steady = math.hypot(u0, v_ss)
    radius = speed_steady / abs(r_ss) if abs(r_ss) > 1e-300 else float("inf")
    return LinearTurningMetrics(
        yaw_rate_degs=math.degrees(abs(r_ss)),
        turning_radius=radius,
        diameter_over_l=2.0 * radius / vessel.L,
        advance=advance if advance is not None else float("inf"),
        transfer=transfer if transfer is not None else float("inf"),
        tactical=tactical if tactical is not None else float("inf"),
        speed_steady=speed_steady,
        time_to_first_heading=t90 if t90 is not None else float("inf"),
    )


def linear_zigzag_reference(model: LinearModel, rudder_deg: float = 10.0,
                            heading_deg: float = 10.0, dt: float = 0.02,
                            t_max: float = 20000.0) -> Dict[str, float]:
    """``delta/psi`` zig-zag of the **linear** model, integrated with RK4.

    The execute/check switching logic mirrors :func:`mmg.simulate_zigzag`
    (execute ends on reaching the target heading, check ends at the yaw-rate zero
    crossing), so the metric sets are directly comparable.  The state is the
    linear ``(v, r)`` plus heading; at ``dt = 0.02 s`` the integration error is
    around ``1e-9`` (RK4, ``dt``-refinement checked in the tests).
    """
    a = model.a2_list()
    b = list(model.b2)
    delta = math.radians(rudder_deg)
    target = heading_deg

    def rhs(x: Vector, rud: float) -> Vector:
        ax = _mat_vec(a, x)
        return [ax[0] + b[0] * rud, ax[1] + b[1] * rud]

    x = [0.0, 0.0]
    psi = 0.0
    t = 0.0
    rudder = delta
    phase = 0
    prev_r = 0.0
    t_reach = t_os1 = t_os2 = None
    os1 = os2 = None
    while t < t_max and phase < 4:
        k1 = rhs(x, rudder)
        k2 = rhs([x[i] + 0.5 * dt * k1[i] for i in range(2)], rudder)
        k3 = rhs([x[i] + 0.5 * dt * k2[i] for i in range(2)], rudder)
        k4 = rhs([x[i] + dt * k3[i] for i in range(2)], rudder)
        x = [x[i] + dt / 6.0 * (k1[i] + 2 * k2[i] + 2 * k3[i] + k4[i])
             for i in range(2)]
        psi += dt * x[1]
        t += dt
        heading = math.degrees(psi)
        if phase == 0 and heading >= target:
            phase, t_reach, rudder = 1, t, -delta
        elif phase == 1:
            if prev_r > 0.0 >= x[1]:
                os1, t_os1, phase = abs(heading), t, 2
        elif phase == 2:
            if heading <= -target:
                phase, rudder = 3, delta
        elif phase == 3:
            if prev_r < 0.0 <= x[1]:
                os2, t_os2, phase = abs(heading), t, 4
        prev_r = x[1]
    return {
        "rudder_deg": rudder_deg,
        "heading_target_deg": heading_deg,
        "time_to_first_heading": t_reach if t_reach is not None else float("nan"),
        "first_overshoot_deg": os1 if os1 is not None else float("nan"),
        "second_overshoot_deg": os2 if os2 is not None else float("nan"),
        "time_between_reversals": ((t_os2 - t_os1)
                                   if (t_os1 is not None and t_os2 is not None)
                                   else float("nan")),
        "completed": phase >= 4,
    }


# ---------------------------------------------------------------------------
# Closed-form first-order Nomoto zig-zag
# ---------------------------------------------------------------------------
def nomoto_zigzag_analytic(k: float, t_const: float, rudder_deg: float,
                           heading_deg: float,
                           t_max: float = 1e6) -> Dict[str, float]:
    """Closed-form ``delta/psi`` zig-zag metrics of the first-order Nomoto model."""
    delta = math.radians(rudder_deg)
    psi0 = math.radians(heading_deg)
    if k * delta <= 0.0 or psi0 <= 0.0:
        raise ValueError("k, delta and heading must be positive")
    kd = k * delta

    def solve_heading(psi_target: float) -> float:
        lo, hi = 0.0, t_max
        for _ in range(200):
            mid = 0.5 * (lo + hi)
            if kd * (mid - t_const * (1.0 - math.exp(-mid / t_const))) < psi_target:
                lo = mid
            else:
                hi = mid
        return 0.5 * (lo + hi)

    t1 = solve_heading(psi0)
    r1 = kd * (1.0 - math.exp(-t1 / t_const))
    tau2 = t_const * math.log((r1 + kd) / kd)
    decay2 = math.exp(-tau2 / t_const)
    psi_os1 = psi0 + (-kd * tau2 + (r1 + kd) * t_const * (1.0 - decay2))

    t3 = solve_heading(psi_os1 + psi0)          # second execute leg length
    r3 = -kd * (1.0 - math.exp(-t3 / t_const))
    tau4 = t_const * math.log((kd - r3) / kd)
    decay4 = math.exp(-tau4 / t_const)
    # phase 4: r = +K d + (r3 - K d) e^{-tau/T}  =>
    # psi = -psi0 + K d tau + (r3 - K d) T (1 - e^{-tau/T})
    psi_os2 = -(psi0 - kd * tau4 - (r3 - kd) * t_const * (1.0 - decay4))
    return {
        "rudder_deg": rudder_deg,
        "heading_target_deg": heading_deg,
        "time_to_first_heading": t1,
        "first_overshoot_deg": math.degrees(abs(psi_os1)),
        "second_overshoot_deg": math.degrees(abs(psi_os2)),
        "time_between_reversals": tau2 + t3 + tau4,
        "K_1_s": k,
        "T_s": t_const,
    }


def nomoto_zigzag_numeric(k: float, t_const: float, rudder_deg: float,
                          heading_deg: float, dt: float = 0.01,
                          t_max: float = 2e6) -> Dict[str, float]:
    """Same zig-zag metrics by RK4 integration of the Nomoto ODE.

    Independent check of the closed-form derivation in
    :func:`nomoto_zigzag_analytic` (same switching logic, first-order dynamics).
    """
    delta = math.radians(rudder_deg)
    target = heading_deg
    r = 0.0
    psi = 0.0
    t = 0.0
    rudder = delta
    phase = 0
    prev_r = 0.0
    t_reach = t_os1 = t_os2 = None
    os1 = os2 = None

    def rdot(rr, rud):
        return (k * rud - rr) / t_const

    while t < t_max and phase < 4:
        k1 = rdot(r, rudder)
        k2 = rdot(r + 0.5 * dt * k1, rudder)
        k3 = rdot(r + 0.5 * dt * k2, rudder)
        k4 = rdot(r + dt * k3, rudder)
        r += dt / 6.0 * (k1 + 2 * k2 + 2 * k3 + k4)
        psi += dt * r
        t += dt
        heading = math.degrees(psi)
        if phase == 0 and heading >= target:
            phase, t_reach, rudder = 1, t, -delta
        elif phase == 1:
            if prev_r > 0.0 >= r:
                os1, t_os1, phase = abs(heading), t, 2
        elif phase == 2:
            if heading <= -target:
                phase, rudder = 3, delta
        elif phase == 3:
            if prev_r < 0.0 <= r:
                os2, t_os2, phase = abs(heading), t, 4
        prev_r = r
    return {
        "time_to_first_heading": t_reach if t_reach is not None else float("nan"),
        "first_overshoot_deg": os1 if os1 is not None else float("nan"),
        "second_overshoot_deg": os2 if os2 is not None else float("nan"),
        "time_between_reversals": ((t_os2 - t_os1)
                                   if (t_os1 is not None and t_os2 is not None)
                                   else float("nan")),
    }


# ---------------------------------------------------------------------------
# Contraction / convergence studies
# ---------------------------------------------------------------------------
def relative_error(model_value: float, ref_value: float) -> float:
    """``|model - ref| / |ref|`` in percent (NaN if the reference vanishes)."""
    if ref_value is None or not math.isfinite(ref_value) or abs(ref_value) < 1e-12:
        return float("nan")
    if not math.isfinite(model_value):
        return float("inf")
    return abs(model_value - ref_value) / abs(ref_value) * 100.0


def fitted_order(amplitudes: List[float], errors: List[float]) -> float:
    """Least-squares slope of ``log(error)`` against ``log(amplitude)``.

    For a scheme whose defect is ``O(amplitude**p)`` the slope estimates ``p``
    (1.0 for the quadratic terms of the MMG model).
    """
    xs = [math.log(a) for a in amplitudes]
    ys = [math.log(e) for e in errors]
    n = len(xs)
    xm, ym = sum(xs) / n, sum(ys) / n
    num = sum((x - xm) * (y - ym) for x, y in zip(xs, ys))
    den = sum((x - xm) ** 2 for x in xs)
    return num / den


def transient_contraction(
    amplitudes: List[float],
    sample_times: List[float],
    vessel: D.Vessel = D.VESSEL_VLCC,
    coef: M.MMGCoefficients = M.KVLCC2_REFERENCE,
    speed: float = 7.717,
    dt: float = 0.1,
) -> Dict[str, object]:
    """Contract the nonlinear transient onto the exact linear solution.

    For each rudder amplitude ``delta`` (degrees) the nonlinear
    :func:`mmg.simulate_turning_circle` yaw rate and heading are compared with the
    matrix-exponential solution of the linearised system at the same instants.
    The nonlinear terms dropped by the linearisation are quadratic in the
    velocities, so the discrepancy must vanish at first order in ``delta``; the
    fitted log-log slope of the error versus ``delta`` is returned as evidence.

    .. note::
       Only the **transient** is compared.  The reference hull is directionally
       unstable (see :func:`nomoto_coefficients`), so the linearised system has a
       *saddle* equilibrium while the nonlinear model has an attracting steady
       turn on the opposite branch; comparing converged turning circles of the
       two models at small rudder angles would compare different solution
       branches and is therefore deliberately not done.
    """
    model = linear_system(vessel, coef, speed)
    t_end = max(sample_times) + dt
    rows: List[Dict[str, object]] = []
    per_time: Dict[float, Dict[str, List[float]]] = {
        t: {"amplitudes": [], "err_r": [], "err_psi": []} for t in sample_times
    }
    for amp in amplitudes:
        sim = M.simulate_turning_circle(vessel, coef, speed=speed,
                                        rudder_deg=amp, dt=dt, t_max=t_end)
        hist = sim.history
        ref = linear_step_response(model, amp, t_end, dt)
        row: Dict[str, object] = {"amplitude_deg": amp, "samples": []}
        for t_star in sample_times:
            i = min(range(len(hist["t"])), key=lambda k: abs(hist["t"][k] - t_star))
            j = min(range(len(ref["t"])), key=lambda k: abs(ref["t"][k] - t_star))
            r_model = math.degrees(hist["r"][i])
            r_ref = math.degrees(ref["r"][j])
            psi_model = hist["psi_deg"][i]
            psi_ref = ref["psi_deg"][j]
            err_r = relative_error(r_model, r_ref)
            err_psi = relative_error(psi_model, psi_ref)
            row["samples"].append({
                "t_s": hist["t"][i],
                "yaw_rate_model_degs": r_model, "yaw_rate_ref_degs": r_ref,
                "yaw_rate_rel_err_pct": err_r,
                "heading_model_deg": psi_model, "heading_ref_deg": psi_ref,
                "heading_rel_err_pct": err_psi,
            })
            per_time[t_star]["amplitudes"].append(amp)
            per_time[t_star]["err_r"].append(err_r)
            per_time[t_star]["err_psi"].append(err_psi)
        rows.append(row)

    orders = {}
    for t_star, data in per_time.items():
        finite_r = [(a, e) for a, e in zip(data["amplitudes"], data["err_r"])
                    if math.isfinite(e) and e > 0.0]
        finite_p = [(a, e) for a, e in zip(data["amplitudes"], data["err_psi"])
                    if math.isfinite(e) and e > 0.0]
        orders[f"t={t_star:g}s"] = {
            "fitted_order_yaw_rate": (fitted_order([a for a, _ in finite_r],
                                                   [e for _, e in finite_r])
                                      if len(finite_r) >= 2 else float("nan")),
            "fitted_order_heading": (fitted_order([a for a, _ in finite_p],
                                                  [e for _, e in finite_p])
                                     if len(finite_p) >= 2 else float("nan")),
            "max_rel_err_yaw_rate_pct": max(data["err_r"]),
            "max_rel_err_heading_pct": max(data["err_psi"]),
        }
    return {
        "quantity": "transient",
        "amplitudes_deg": list(amplitudes),
        "sample_times_s": list(sample_times),
        "rows": rows,
        "orders": orders,
    }


def zigzag_contraction(
    amplitudes: List[float],
    vessel: D.Vessel = D.VESSEL_VLCC,
    coef: M.MMGCoefficients = M.KVLCC2_REFERENCE,
    speed: float = 7.717,
    dt: float = 0.05,
) -> Dict[str, object]:
    """Contract the nonlinear ``delta/delta`` zig-zag onto the linear reference.

    The first overshoot angle -- previously only checked for plausibility -- now
    has an exact reference in the small-amplitude limit
    (:func:`linear_zigzag_reference`), and the discrepancy must decay at first
    order in the rudder amplitude.
    """
    model = linear_system(vessel, coef, speed)
    rows: List[Dict[str, object]] = []
    amplitudes = list(amplitudes)
    err_os1: List[float] = []
    err_t: List[float] = []
    for amp in amplitudes:
        sim = M.simulate_zigzag(vessel, coef, speed=speed, rudder_deg=amp,
                               heading_deg=amp, dt=dt,
                               t_max=max(600.0, 40.0 * 100.0 / amp))
        ref = linear_zigzag_reference(model, amp, amp)
        os_model = sim.metrics["first_overshoot_deg"]
        os_ref = ref["first_overshoot_deg"]
        t_model = sim.metrics["time_to_first_heading"]
        t_ref = ref["time_to_first_heading"]
        err_os1.append(relative_error(os_model, os_ref))
        err_t.append(relative_error(t_model, t_ref))
        rows.append({
            "amplitude_deg": amp,
            "first_overshoot_model_deg": os_model,
            "first_overshoot_ref_deg": os_ref,
            "first_overshoot_rel_err_pct": err_os1[-1],
            "time_to_first_heading_model_s": t_model,
            "time_to_first_heading_ref_s": t_ref,
            "time_to_first_heading_rel_err_pct": err_t[-1],
        })
    ok_os = [(a, e) for a, e in zip(amplitudes, err_os1)
             if math.isfinite(e) and e > 0.0]
    ok_t = [(a, e) for a, e in zip(amplitudes, err_t)
            if math.isfinite(e) and e > 0.0]
    return {
        "quantity": "zigzag",
        "amplitudes_deg": amplitudes,
        "rows": rows,
        "fitted_order_first_overshoot": (fitted_order([a for a, _ in ok_os],
                                                      [e for _, e in ok_os])
                                         if len(ok_os) >= 2 else float("nan")),
        "fitted_order_time_to_first_heading": (
            fitted_order([a for a, _ in ok_t], [e for _, e in ok_t])
            if len(ok_t) >= 2 else float("nan")),
    }


def initial_slope_check(
    rudder_deg: float = 1.0,
    dt: float = 0.005,
    vessel: D.Vessel = D.VESSEL_VLCC,
    coef: M.MMGCoefficients = M.KVLCC2_REFERENCE,
    speed: float = 7.717,
) -> Dict[str, float]:
    """Check the leading-order response of the integrator against ``b2``.

    ``mmg._rhs`` returns ``dr/dt = N_delta delta / I_z`` and
    ``dv/dt = Y_delta delta / (m+m_y)`` at the straight-ahead state, i.e. exactly
    ``b2 delta``.  One single RK4 step of the parent integrator therefore has to
    reproduce ``b2 delta`` up to ``O(dt)`` -- an independent confirmation of both
    the control derivatives and the sign convention (positive rudder gives a
    positive yaw acceleration here).
    """
    model = linear_system(vessel, coef, speed)
    n_rps = model.n_rps
    delta = math.radians(rudder_deg)
    state = (0.0, 0.0, 0.0, speed, 0.0, 0.0)
    nxt, _ = M._rk4(state, delta, n_rps, vessel, coef, M.Environment(), dt)
    dr_model = nxt[5] / dt
    dv_model = nxt[4] / dt
    dr_ref = model.b2[1] * delta
    dv_ref = model.b2[0] * delta
    return {
        "rudder_deg": rudder_deg,
        "dt_s": dt,
        "drdt_model": dr_model, "drdt_b2_reference": dr_ref,
        "drdt_rel_err_pct": relative_error(dr_model, dr_ref),
        "dvdt_model": dv_model, "dvdt_b2_reference": dv_ref,
        "dvdt_rel_err_pct": relative_error(dv_model, dv_ref),
    }


def rk4_order_study(
    time_steps: Sequence[float] = (2.0, 1.0, 0.5, 0.25),
    t_end: float = 600.0,
    rudder_deg: float = 35.0,
    vessel: D.Vessel = D.VESSEL_VLCC,
    coef: M.MMGCoefficients = M.KVLCC2_REFERENCE,
    speed: float = 7.717,
) -> Dict[str, object]:
    """Observed order of accuracy of the parent RK4 integrator (Richardson).

    The final ``(u, v, r)`` after ``t_end`` at constant rudder is integrated with
    the parent :func:`mmg._rk4` for a sequence of step sizes and the successive
    differences are used to estimate the observed order
    ``p = log2(|x(dt) - x(dt/2)| / |x(dt/2) - x(dt/4)|)``.  Turn-circle metrics
    are deliberately *not* used here: ``advance``/``transfer`` are sampled at the
    first step whose heading passes 90 deg, so their ``dt``-sensitivity is
    dominated by the threshold quantisation and not by the integrator order.
    """
    n_rps = M.required_rpm(vessel, coef, speed)
    delta = math.radians(rudder_deg)
    env = M.Environment()
    finals: Dict[float, List[float]] = {}
    for dt in time_steps:
        state = (0.0, 0.0, 0.0, speed, 0.0, 0.0)
        for _ in range(int(round(t_end / dt))):
            state, _ = M._rk4(state, delta, n_rps, vessel, coef, env, dt)
        finals[dt] = [state[3], state[4], state[5]]

    def difference(a: List[float], b: List[float]) -> float:
        return max(abs(x - y) for x, y in zip(a, b))

    steps = list(time_steps)
    diffs = [difference(finals[steps[i]], finals[steps[i + 1]])
             for i in range(len(steps) - 1)]
    orders = [math.log(diffs[i] / diffs[i + 1], 2.0)
              for i in range(len(diffs) - 1) if diffs[i + 1] > 0.0]
    return {
        "time_steps_s": steps,
        "final_state": {f"{dt:g}": finals[dt] for dt in steps},
        "successive_differences": diffs,
        "observed_orders": orders,
        "min_observed_order": min(orders) if orders else float("nan"),
    }


__all__ = [
    "LinearModel", "NomotoIndices", "LinearTurningMetrics",
    "linear_system", "nomoto_coefficients",
    "linear_step_response", "linear_turning_reference", "linear_zigzag_reference",
    "nomoto_zigzag_analytic", "nomoto_zigzag_numeric",
    "transient_contraction", "zigzag_contraction",
    "initial_slope_check", "rk4_order_study",
    "relative_error", "fitted_order",
    "expm", "solve", "inverse",
]