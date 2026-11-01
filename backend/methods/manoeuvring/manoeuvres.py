"""Standard manoeuvres, manoeuvre-indicator extraction, IMO criteria and the
closed-form cross-checks used to verify the MMG implementation.

Everything here is machine-checkable without external data:

* :func:`steady_state` solves the algebraic steady-turning equations with Newton's
  method; the time-marched solution must reproduce it (integration check).
* :func:`nomoto_indices` derives the first-order Nomoto indices ``K``/``T`` from
  the linearisation of the model; :func:`nomoto_response` gives the analytic
  yaw-rate/heading response, which the (weakly nonlinear) simulation must
  reproduce for small rudder angles (linearisation check).
* :func:`circle_fit` recovers the steady-turning diameter from the track
  geometry, independently of ``u_ss / r_ss`` (kinematics check).
* :func:`evaluate_imo` implements IMO Res. MSC.137(76) manoeuvrability criteria.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Sequence, Tuple

from .model import MMGModel, ManoeuvreTrace

# IMO Res. MSC.137(76) limits (ships of L >= 100 m)
IMO_LIMITS: Dict[str, float] = {
    "advance_over_L": 4.5,
    "tactical_diameter_over_L": 5.0,
    "zigzag_10_first_overshoot_deg": 10.0,
    "zigzag_10_second_overshoot_deg": 25.0,
    "zigzag_20_first_overshoot_deg": 25.0,
}
IMO_MIN_LENGTH = 100.0


# ---------------------------------------------------------------------------
# steady state (Newton) and Nomoto linearisation
# ---------------------------------------------------------------------------

def steady_state(model: MMGModel, delta: float, tol: float = 1e-10,
                 max_iter: int = 80) -> Tuple[float, float, float]:
    """Solve ``u_dot = v_dot = r_dot = 0`` for a given rudder angle.

    Newton iteration on ``(u, v, r)`` with a numerically evaluated Jacobian.
    This is the closed-form counterpart of the time marching solution: the two
    must agree to round-off for a converged manoeuvre.
    """
    x = [model.u0, 0.0, 0.001]

    def resid(xv: Sequence[float]) -> List[float]:
        d = model.derivatives((xv[0], xv[1], xv[2], 0.0, 0.0, 0.0), delta)
        return [d[0], d[1], d[2]]

    for _ in range(max_iter):
        f0 = resid(x)
        h = [max(1e-7, abs(x[i]) * 1e-6) for i in range(3)]
        jac = [[0.0] * 3 for _ in range(3)]
        for j in range(3):
            xp = list(x)
            xp[j] += h[j]
            fp = resid(xp)
            for i in range(3):
                jac[i][j] = (fp[i] - f0[i]) / h[j]
        dx = _solve3(jac, [-f0[0], -f0[1], -f0[2]])
        x = [x[i] + dx[i] for i in range(3)]
        if max(abs(v) for v in dx) < tol:
            break
    return x[0], x[1], x[2]


def _solve3(a: List[List[float]], b: List[float]) -> List[float]:
    """Cramer's rule for a 3x3 system."""
    def det(m):
        return (m[0][0] * (m[1][1] * m[2][2] - m[1][2] * m[2][1])
                - m[0][1] * (m[1][0] * m[2][2] - m[1][2] * m[2][0])
                + m[0][2] * (m[1][0] * m[2][1] - m[1][1] * m[2][0]))
    d = det(a)
    if abs(d) < 1e-30:
        raise ArithmeticError("singular Jacobian in steady-state solve")
    out = []
    for k in range(3):
        m = [row[:] for row in a]
        for i in range(3):
            m[i][k] = b[i]
        out.append(det(m) / d)
    return out


def nomoto_indices(model: MMGModel) -> Dict[str, float]:
    """First-order Nomoto indices ``K`` [1/s] and ``T`` [s] from the linearisation.

    With ``d/dt [v, r]^T = A [v, r]^T + b delta`` the yaw-rate transfer function
    is ``r/delta = (b2 s + c_r) / (s^2 + a1 s + a2)`` with ``a1 = -(A11+A22)``,
    ``a2 = A11 A22 - A12 A21``, ``c_r = A21 b1 - A11 b2``.  Matching the standard
    Nomoto form ``K(1+T3 s)/((1+T1 s)(1+T2 s))`` gives
    ``K = c_r/a2`` and ``T = a1/a2 - b2/c_r`` (first-order equivalent).
    """
    ls = model.linear_system()
    a11, a12, a21, a22 = ls["a11"], ls["a12"], ls["a21"], ls["a22"]
    b1, b2 = ls["b1"], ls["b2"]
    a1 = -(a11 + a22)
    a2 = a11 * a22 - a12 * a21
    c_r = a21 * b1 - a11 * b2
    K = c_r / a2
    T = a1 / a2 - b2 / c_r
    det_a = a11 * a22 - a12 * a21
    trace_a = a11 + a22
    disc = trace_a ** 2 - 4.0 * det_a
    return {
        "K": K, "T": T,
        "a1": a1, "a2": a2, "cr": c_r,
        "det_A": det_a, "trace_A": trace_a,
        # linear directional stability: both eigenvalues must have negative real parts
        "eig_re_max": trace_a / 2.0 + (math.sqrt(disc) / 2.0 if disc >= 0 else 0.0),
        "eig_oscillatory": 1.0 if disc < 0 else 0.0,
    }


def nomoto_response(K: float, T: float, delta: float, t: float) -> Tuple[float, float]:
    """Analytic first-order Nomoto response ``(r(t), psi(t))`` to a rudder step."""
    r = K * delta * (1.0 - math.exp(-t / T))
    psi = K * delta * (t - T * (1.0 - math.exp(-t / T)))
    return r, psi


# ---------------------------------------------------------------------------
# manoeuvre runners
# ---------------------------------------------------------------------------

def run_constant_rudder(model: MMGModel, delta: float, duration: float,
                        dt: float = 0.2) -> ManoeuvreTrace:
    """March the model with a fixed rudder angle."""
    tr = ManoeuvreTrace()
    s = model.initial_state()
    tr.append(0.0, s, delta)
    n = int(round(duration / dt))
    for i in range(1, n + 1):
        s = model.step(s, delta, dt)
        tr.append(i * dt, s, delta)
    return tr


def circle_fit(points: Sequence[Tuple[float, float]]) -> Tuple[float, float, float]:
    """Algebraic (Kasa) circle fit; returns ``(xc, yc, R)``."""
    x0 = sum(p[0] for p in points) / len(points)
    y0 = sum(p[1] for p in points) / len(points)
    xs = [p[0] - x0 for p in points]
    ys = [p[1] - y0 for p in points]
    sxx = sum(x * x for x in xs)
    syy = sum(y * y for y in ys)
    sxy = sum(x * y for x, y in zip(xs, ys))
    sx = sum(xs)
    sy = sum(ys)
    sxz = sum(x * (x * x + y * y) for x, y in zip(xs, ys))
    syz = sum(y * (x * x + y * y) for x, y in zip(xs, ys))
    # normal equations for  x^2+y^2 + D x + E y + F = 0
    a = [[sxx, sxy, sx], [sxy, syy, sy], [sx, sy, float(len(xs))]]
    b = [-sxz, -syz, -(sxx + syy)]
    d, e, f = _solve3(a, b)
    xc = -d / 2.0 + x0
    yc = -e / 2.0 + y0
    r = math.sqrt(max(xc * xc + yc * yc - f + 0.0, 0.0))
    # recompute radius about the fitted centre for numerical robustness
    r = sum(math.hypot(p[0] - xc, p[1] - yc) for p in points) / len(points)
    return xc, yc, r


def turning_circle_indicators(model: MMGModel, trace: ManoeuvreTrace) -> Dict[str, float]:
    """Extract the IMO turning-circle quantities from a constant-rudder trace."""
    L = model.hull.length
    adv = transfer = None
    for psi, x, y in zip(trace.psi, trace.x, trace.y):
        if adv is None and psi >= math.pi / 2:
            adv, transfer = x, y
    tactical = max(abs(y) for y in trace.y[-max(3, len(trace.y) // 3):])
    u_ss = trace.u[-1]
    r_ss = trace.r[-1]
    steady_diameter_kinematic = 2.0 * u_ss / r_ss if r_ss > 1e-12 else float("inf")
    tail = list(zip(trace.x, trace.y))[-max(50, len(trace.x) // 2):]
    _, _, radius_fit = circle_fit(tail)
    return {
        "advance_m": adv if adv is not None else float("nan"),
        "advance_over_L": (adv / L) if adv is not None else float("nan"),
        "transfer_m": transfer if transfer is not None else float("nan"),
        "transfer_over_L": (transfer / L) if transfer is not None else float("nan"),
        "tactical_diameter_m": tactical,
        "tactical_diameter_over_L": tactical / L,
        "steady_turning_diameter_m": steady_diameter_kinematic,
        "steady_turning_diameter_fit_m": 2.0 * radius_fit,
        "steady_radius_over_L": (u_ss / r_ss / L) if r_ss > 1e-12 else float("nan"),
        "u_steady": u_ss,
        "r_steady": r_ss,
        "speed_loss_pct": 100.0 * (1.0 - u_ss / model.u0),
        "drift_angle_deg": math.degrees(math.atan2(-trace.v[-1], trace.u[-1])),
        "final_heading_deg": math.degrees(trace.psi[-1]),
    }


def zigzag(model: MMGModel, rudder_deg: float = 10.0, heading_deg: float = 10.0,
           dt: float = 0.2, max_time: float = 1800.0) -> Tuple[ManoeuvreTrace, List[float]]:
    """Standard zigzag manoeuvre; returns the trace and the overshoot angles (deg).

    Phase machine: hold starboard rudder until the heading reaches ``+heading``,
    reverse to port until ``-heading``, reverse again, ...  The overshoot angle of
    a reversal is the extreme heading reached beyond the target after reversing.
    """
    tr = ManoeuvreTrace()
    s = model.initial_state()
    target_sign = +1.0
    delta = math.radians(rudder_deg) * target_sign
    psi_target = math.radians(heading_deg) * target_sign
    phase = 0
    overshoots: List[float] = []
    extreme = 0.0
    t = 0.0
    tr.append(t, s, delta)
    while t < max_time and phase < 4:
        s_new = model.step(s, delta, dt)
        t += dt
        tr.append(t, s_new, delta)
        heading = s_new[5]
        if phase == 0:
            if heading >= psi_target:
                phase = 1
                extreme = heading
                target_sign = -1.0
                delta = math.radians(rudder_deg) * target_sign
                psi_target = math.radians(heading_deg) * target_sign
        elif phase in (1, 2, 3):
            # track the extreme heading while the ship carries way past the target
            extreme = max(extreme, heading) if target_sign < 0 else min(extreme, heading)
            if (target_sign < 0 and heading <= psi_target) or \
               (target_sign > 0 and heading >= psi_target):
                overshoots.append(abs(math.degrees(extreme) - math.degrees(psi_target)))
                phase += 1
                extreme = heading
                target_sign = -target_sign
                delta = math.radians(rudder_deg) * target_sign
                psi_target = math.radians(heading_deg) * target_sign
        s = s_new
    return tr, overshoots


# ---------------------------------------------------------------------------
# IMO Res. MSC.137(76)
# ---------------------------------------------------------------------------

def evaluate_imo(length: float, turning: Optional[Dict[str, float]] = None,
                 zigzag_10: Optional[Sequence[float]] = None,
                 zigzag_20: Optional[Sequence[float]] = None) -> Dict[str, object]:
    """Evaluate IMO manoeuvrability criteria (Res. MSC.137(76)).

    ``applicable`` is False for ``L < 100 m`` -- the resolution's numerical
    limits are then advisory only (stated explicitly, not silently applied).
    """
    criteria: List[Dict[str, object]] = []

    def add(name: str, value: Optional[float], limit: float) -> None:
        if value is None or (isinstance(value, float) and math.isnan(value)):
            criteria.append({"criterion": name, "value": None, "limit": limit,
                             "status": "NOT_EVALUATED"})
            return
        criteria.append({"criterion": name, "value": value, "limit": limit,
                         "status": "PASS" if value <= limit else "FAIL"})

    if turning:
        add("advance / L  (<= 4.5)", turning.get("advance_over_L"), IMO_LIMITS["advance_over_L"])
        add("tactical diameter / L  (<= 5.0)", turning.get("tactical_diameter_over_L"),
            IMO_LIMITS["tactical_diameter_over_L"])
    z10 = list(zigzag_10 or [])
    if z10:
        add("10/10 zigzag first overshoot (deg)  (<= 10)", z10[0],
            IMO_LIMITS["zigzag_10_first_overshoot_deg"])
    if len(z10) > 1:
        add("10/10 zigzag second overshoot (deg)  (<= 25)", z10[1],
            IMO_LIMITS["zigzag_10_second_overshoot_deg"])
    z20 = list(zigzag_20 or [])
    if z20:
        add("20/20 zigzag first overshoot (deg)  (<= 25)", z20[0],
            IMO_LIMITS["zigzag_20_first_overshoot_deg"])

    n_fail = sum(1 for c in criteria if c["status"] == "FAIL")
    applicable = length >= IMO_MIN_LENGTH
    return {
        "resolution": "IMO Res. MSC.137(76)",
        "applicable": applicable,
        "applicability_note": (
            "criteria apply to ships of L >= 100 m; for L < 100 m the values are "
            "advisory only" if not applicable else "applies (L >= 100 m)"),
        "criteria": criteria,
        "n_criteria": len(criteria),
        "n_fail": n_fail,
        "verdict": "PASS" if n_fail == 0 and criteria else "FAIL",
    }