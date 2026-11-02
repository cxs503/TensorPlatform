#!/usr/bin/env python3
"""Reference MMG standard manoeuvring kernel (independent re-implementation).

Purpose
-------
The ship-performance platform exposes six manoeuvring endpoint families.  Report
1032 flagged `/maneuvering-mmg/turning` and `/zigzag` as "stable 500, undecided"
(defect D5).  This module provides a **from-first-principles** MMG kernel so that
the platform's manoeuvring claims can be judged against something other than
themselves, and so that the reference turning/zigzag indices are pinned in a
regression test.

Method
------
MMG standard model (Yasukawa & Yoshimura 2015, *Introduction of MMG standard
method for ship manoeuvring predictions*, J. Mar. Sci. Technol. 20:37-52):

    (m + m_x) (u_dot - v r) = X_H + X_R + X_P
    (m + m_y) (v_dot + u r) = Y_H + Y_R
    (I_zz + J_zz) r_dot    = N_H + N_R

Non-dimensionalised by ``0.5*rho*L*d*U**2`` (forces), ``0.5*rho*L**2*d*U**2``
(moments, and I_zz/moments of inertia), time by ``L/U``.

Coefficients are the KVLCC2 tanker set tabulated in the reference above
(geometry: L=320 m, B=58 m, d=20.8 m, Cb=0.8098, grad=312622 m^3,
D_p=9.86 m, A_R=112.5 m^2).  Every coefficient is written out explicitly in
``KVLCC2`` below so the numbers can be audited line by line.

Honest provenance note: the coefficients are *quoted* from the MMG standard
paper (not re-digitised from a figure in this repository), therefore the
derived indices below are B-class (literature-reproducing) evidence, while the
internal consistency checks (delta=0 straight run, port/starboard mirror,
time-step convergence, steady-turn equilibrium residual) are A-class.

Integration: classical RK4 on the 5-state system, fixed step dtau = 0.01
(t' = t U/L), i.e. ~1 s of model time per step at KVLCC2 speed.
"""

from __future__ import annotations

import math

import numpy as np

__all__ = [
    "steady_turn_residual",
    "KVLCC2", "MMGParams", "manoeuvre", "turning_indices", "zigzag_indices",
    "self_propulsion_n", "steady_turn_state",
]

RHO = 1025.0  # kg/m^3, sea water (MMG model tests usually use 1000; documented)

# --------------------------------------------------------------------------
# KVLCC2 coefficient set (Yasukawa & Yoshimura 2015)
# --------------------------------------------------------------------------
KVLCC2 = dict(
    # main particulars
    L=320.0, B=58.0, d=20.8, Cb=0.8098, vol=312622.0,
    # hull resistance / manoeuvring derivatives
    R0=0.022,
    X_vv=-0.040, X_vr=0.002, X_rr=0.011, X_vvvv=0.771,
    Y_v=-0.315, Y_r=0.083, Y_vvv=-1.607, Y_vvr=0.379, Y_vrr=-0.391, Y_rrr=0.008,
    N_v=-0.137, N_r=-0.049, N_vvv=-0.030, N_vvr=-0.616, N_vrr=0.038, N_rrr=-0.014,
    # added mass / added inertia (non-dimensional)
    m_x=0.0226, m_y=0.223, J_zz=0.0110,
    # propeller
    Dp=9.86, x_P=-0.5, t_R=0.220, w_P0=0.40,
    k0=0.2931, k1=-0.2753, k2=-0.1385,   # K_T = k0 + k1 J + k2 J^2
    # rudder
    Ar=112.5, Hr=14.35,                  # area [m^2], span [m] -> Lambda=1.83
    a_H=0.312, x_H=-0.4, x_R=-0.5, gamma_R=0.395, eps=1.09,
    # gyration radius for I_zz (standard assumption)
    k_zz=0.25,
)


class MMGParams:
    """Dimensionless MMG parameter bundle built from a coefficient dict."""

    def __init__(self, p: dict | None = None, rho: float = RHO):
        c = dict(KVLCC2)
        if p:
            c.update(p)
        self.c = c
        self.rho = rho
        L, d, vol = c["L"], c["d"], c["vol"]
        self.L, self.d = L, d
        self.S_ref = 0.5 * rho * L * d      # force scale coefficient base
        # mass and inertia (dimensional -> dimensionless)
        m = rho * vol
        self.m = m
        self.Izz = m * (c["k_zz"] * L) ** 2
        # non-dimensional: m' = m / (0.5 rho L^2 d), same as Izz' basis
        self.m_p = m / (0.5 * rho * L * L * d)
        self.Izz_p = self.Izz / (0.5 * rho * L ** 4 * d)
        self.D_p = c["Dp"] / L
        self.d_p = d / L
        self.A_R_p = c["Ar"] / (L * d)
        # rudder aspect ratio and lift slope f_alpha = 6.13 Lambda/(Lambda+2.25)
        lam = c["Hr"] ** 2 / c["Ar"]
        self.f_alpha = 6.13 * lam / (lam + 2.25)

    # ---- propeller -------------------------------------------------------
    def thrust_p(self, u_p: float, n_p: float) -> tuple[float, float]:
        """Non-dimensional thrust and advance ratio at propeller inflow u'."""
        c = self.c
        J = u_p * (1.0 - c["w_P0"]) / (n_p * self.D_p) if n_p > 1e-9 else 0.0
        KT = c["k0"] + c["k1"] * J + c["k2"] * J * J
        # T' = 2 n'^2 D'^4 K_T / d'
        T_p = 2.0 * n_p ** 2 * self.D_p ** 4 * KT / self.d_p
        return T_p, J

    # ---- forces ----------------------------------------------------------
    def forces(self, u: float, v: float, r: float, delta: float,
               n_p: float) -> tuple[float, float, float]:
        """Total non-dimensional X', Y', N' in the MMG frame."""
        c = self.c
        # hull
        XH = (-c["R0"] + c["X_vv"] * v * v + c["X_vr"] * v * r
              + c["X_rr"] * r * r + c["X_vvvv"] * v ** 4)
        YH = (c["Y_v"] * v + c["Y_r"] * r + c["Y_vvv"] * v ** 3
              + c["Y_vvr"] * v * v * r + c["Y_vrr"] * v * r * r
              + c["Y_rrr"] * r ** 3)
        NH = (c["N_v"] * v + c["N_r"] * r + c["N_vvv"] * v ** 3
              + c["N_vvr"] * v * v * r + c["N_vrr"] * v * r * r
              + c["N_rrr"] * r ** 3)
        # propeller
        T_p, J = self.thrust_p(u, n_p)
        XP = (1.0 - c["t_R"]) * T_p
        XR = YR = NR = 0.0
        # rudder
        if abs(delta) > 0.0:
            # standard MMG: rudder inflow accelerated by propeller race
            T_p, J = self.thrust_p(u, n_p)
            race = 1.0 + 8.0 * max(T_p, 0.0) * self.d_p / (
                math.pi * self.D_p ** 4) / max(n_p, 1e-9) ** 2 if n_p > 1e-9 else 1.0
            race = max(race, 1.0)
            uR = c["eps"] * (1.0 - c["w_P0"]) * u * math.sqrt(race)
            vR = c["gamma_R"] * (v + c["x_R"] * r)
            denom = max(uR, 1e-6)
            alpha_R = delta - math.atan2(vR, denom)
            UR2 = uR * uR + vR * vR
            FN = self.A_R_p * UR2 * self.f_alpha * math.sin(alpha_R)
            XR = -(1.0 - c["t_R"]) * FN * math.sin(delta)
            YR = -(1.0 + c["a_H"]) * FN * math.cos(delta)
            NR = -(c["x_R"] + c["a_H"] * c["x_H"]) * FN * math.cos(delta)
        return XH + XP + XR, YH + YR, NH + NR

    def kinematics(self, s, delta, n_p):
        """Time derivative of the non-dimensional state."""
        u, v, r, x, y, psi = s
        X, Y, N = self.forces(u, v, r, delta, n_p)
        du = (X + (self.m_p + self.c["m_y"]) * v * r) / (self.m_p + self.c["m_x"])
        dv = (Y - (self.m_p + self.c["m_x"]) * u * r) / (self.m_p + self.c["m_y"])
        dr = N / (self.Izz_p + self.c["J_zz"])
        return np.array([du, dv, dr,
                         u * math.cos(psi) - v * math.sin(psi),
                         u * math.sin(psi) + v * math.cos(psi),
                         r])


def self_propulsion_n(prm: MMGParams) -> float:
    """Solve n' such that the net surge force vanishes at u'=1 (self-propulsion)."""
    lo, hi = 1e-3, 500.0
    for _ in range(200):
        mid = 0.5 * (lo + hi)
        T_p, _ = prm.thrust_p(1.0, mid)
        X = -prm.c["R0"] + (1.0 - prm.c["t_R"]) * T_p
        if X < 0:
            lo = mid
        else:
            hi = mid
    return 0.5 * (lo + hi)


STEP = 0.01          # dtau = dt * U / L  -> 0.4 s model time per step
TAU_MAX = 400.0      # ~ 400 * L/U = 16000 s >> time to complete 540 deg


def manoeuvre(prm: MMGParams, n_p: float, delta_cmd_deg: float,
              tau_max: float = TAU_MAX, step: float = STEP,
              heading_limit_deg: float = 540.0) -> dict:
    """Integrate an RK4 turning manoeuvre from straight-ahead self-propulsion.

    ``delta_cmd_deg`` is applied as a step at t'=0 (SIMMAN/ITTC standard for
    the reference tactical-diameter comparison; rudder rate is a separate run).
    """
    n_p = float(n_p)
    delta = math.radians(delta_cmd_deg)
    s = np.array([1.0, 0.0, 0.0, 0.0, 0.0, 0.0])
    hist = [s.copy()]
    n_steps = int(tau_max / step)
    for _ in range(n_steps):
        k1 = prm.kinematics(s, delta, n_p)
        k2 = prm.kinematics(s + 0.5 * step * k1, delta, n_p)
        k3 = prm.kinematics(s + 0.5 * step * k2, delta, n_p)
        k4 = prm.kinematics(s + step * k3, delta, n_p)
        s = s + (step / 6.0) * (k1 + 2 * k2 + 2 * k3 + k4)
        hist.append(s.copy())
        if abs(math.degrees(s[5])) >= heading_limit_deg:
            break
    return {"tau_step": step, "hist": np.array(hist), "n_p": n_p,
            "delta_deg": delta_cmd_deg}


def turning_indices(run: dict, L: float, U: float = 1.0) -> dict:
    """Advance / transfer / tactical diameter, plus steady state.

    Column order of ``run['hist']`` is (u', v', r', x', y', psi).

    Calibers (single source of truth: ``docs/verification_debt/turning_caliber``):

    advance / transfer : longitudinal / transverse path coordinate at the instant
        the heading change first reaches 90 deg (ITTC 7.5-02-06-01 wording).
    tactical_diameter  : transverse offset ``|y|`` from the approach line at the
        instant the heading change first reaches 180 deg.  For a steady circular
        path this offset **is** the diameter of that circle, so a correct
        implementation must satisfy ``tactical_diameter == 2*V/Omega`` exactly;
        ``tests/test_turning_caliber.py`` asserts that identity on an exact
        synthetic circle.  (Revisions before m1867 returned ``2*|y|`` here --
        a factor-2 caliber error, see
        ``docs/verification_debt/turning_caliber/REPORT.md``.)
    steady_diameter    : ``2*V/|r|`` with ``V = hypot(u', v')`` the *total* path
        speed; the surge-only variant ``2*u'/|r|`` under-reports the radius by
        ``cos(drift)`` and is returned separately as
        ``steady_diameter_surge_only`` for auditing, never for judgement.
    """
    h = run["hist"]
    step = float(run.get("tau_step", STEP))
    u, v, r, x, y, psi = (h[:, 0], h[:, 1], h[:, 2], h[:, 3], h[:, 4], h[:, 5])
    deg = np.degrees(psi)
    adv = trans = td = float("nan")
    i90 = int(np.argmax(deg >= 90.0)) if np.any(deg >= 90.0) else None
    i180 = int(np.argmax(deg >= 180.0)) if np.any(deg >= 180.0) else None
    if i90 is not None:
        adv, trans = float(x[i90]), float(abs(y[i90]))
    if i180 is not None:
        td = float(abs(y[i180]))          # diameter of the steady circle, not 2x
    r_end = float(r[-1])
    u_end = float(u[-1])
    v_end = float(v[-1])
    v_path = math.hypot(u_end, v_end)     # total path speed (u'^2 + v'^2)^0.5
    if abs(r_end) > 1e-12:
        R_total = v_path / abs(r_end)
        R_surge = u_end / abs(r_end)
    else:                                 # straight run: no circle exists
        R_total = R_surge = float("inf")
    return dict(
        advance=adv * L, transfer=trans * L,
        tactical_diameter=td * L,
        tactical_diameter_at_180_raw=td * L,
        steady_diameter=2.0 * R_total * L,
        steady_diameter_surge_only=2.0 * R_surge * L,
        steady_radius_over_L=R_total,
        steady_rate_deg_s=(float(math.degrees(r_end * U / L))
                           if r_end == r_end else float("nan")),
        u_end=u_end, v_end_p=v_end, r_end_p=r_end,
        drift_angle_deg=float(math.degrees(math.atan2(-v_end, u_end))),
        tau_end=float((len(h) - 1) * step),
        psi_end_deg=float(deg[-1]))


def zigzag_indices(prm: MMGParams, n_p: float, delta_deg: float = 20.0,
                   psi_deg: float = 10.0, step: float = STEP,
                   tau_max: float = 600.0, U: float = 1.0) -> dict:
    """Standard 10/10 (or delta/psi) zigzag with a 2.5 deg/s rudder rate.

    Phase 0: rudder to -delta until heading reaches -psi (first execute).
    Phase 1: rudder reversed to +delta, heading keeps growing (overshoot #1).
    Phase 2: at heading +psi rudder reversed again -> overshoot #2.
    Overshoot angle = (peak heading) - (execute heading), per ITTC wording.
    """
    d_cmd = math.radians(delta_deg)
    psi_stop = math.radians(psi_deg)
    rate = math.radians(2.5) * (prm.L / U)      # rad per unit non-dim time
    s = np.array([1.0, 0.0, 0.0, 0.0, 0.0, 0.0])
    delta = 0.0
    sign = -1.0
    phase, extr = 0, 0.0
    os1 = os2 = float("nan")
    hist = [s.copy()]
    for _ in range(int(tau_max / step)):
        target = sign * d_cmd
        dd = target - delta
        maxd = rate * step
        delta += max(-maxd, min(maxd, dd))
        k1 = prm.kinematics(s, delta, n_p)
        k2 = prm.kinematics(s + 0.5 * step * k1, delta, n_p)
        k3 = prm.kinematics(s + 0.5 * step * k2, delta, n_p)
        k4 = prm.kinematics(s + step * k3, delta, n_p)
        s = s + (step / 6.0) * (k1 + 2 * k2 + 2 * k3 + k4)
        hist.append(s.copy())
        psi = float(s[5])
        if phase == 0:
            if psi <= -psi_stop:                 # first execute reached
                phase, sign, extr = 1, +1.0, psi
        elif phase == 1:
            extr = max(extr, psi)
            if psi >= psi_stop:                  # heading swung back through +psi
                os1 = math.degrees(extr - psi_stop)
                phase, sign, extr = 2, -1.0, psi
        else:
            extr = min(extr, psi)
            if psi <= -psi_stop:                 # second overshoot complete
                os2 = math.degrees(-psi_stop - extr)
                break
    return dict(first_overshoot_deg=float(os1),
                second_overshoot_deg=float(os2),
                psi_end_deg=float(math.degrees(s[5])),
                delta_end_deg=float(math.degrees(delta)),
                n_tau=float(len(hist) * step), hist=np.array(hist))


def steady_turn_residual(prm: MMGParams, n_p: float, delta_deg: float,
                         tau_max: float = 4000.0) -> float:
    """Relative residual of the steady-turn equilibrium (A-class self-check)."""
    run = manoeuvre(prm, n_p, delta_deg, tau_max=tau_max, heading_limit_deg=1e9)
    h = run["hist"]
    u, v, r = float(h[-1, 0]), float(h[-1, 1]), float(h[-1, 2])
    X, Y, N = prm.forces(u, v, r, math.radians(delta_deg), n_p)
    dv = (Y - (prm.m_p + prm.c["m_x"]) * u * r) / (prm.m_p + prm.c["m_y"])
    dr = N / (prm.Izz_p + prm.c["J_zz"])
    return float(abs(dv) / max(abs(u * r), 1e-9) + abs(dr) / max(abs(r), 1e-9))
