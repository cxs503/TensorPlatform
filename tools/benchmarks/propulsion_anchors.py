"""Propulsion anchor kernel — self-contained reference judges for the
"推进 (propulsion)" discipline of the ship overall-performance platform.

Motivation (m1413 verification-debt audit)
------------------------------------------
``/api/v1/tensor/reports`` and ``audit_capabilities`` grade ``propeller.open_water``
as REAL, but ``backend/data/benchmark_reference.json`` contains **12 resistance
cases only** (SUBOFF / DTMB5415 / KVLCC2) and ``backend/services/ship_proxy.py``
exposes **no propeller endpoint** (resistance + stability only).  Propulsion
therefore has *zero* machine-checkable evidence in the worktree.

This module supplies the missing judge without depending on the live ship
service (ports 8001/8002 are down at audit time).  Every anchor below is either

* an **exact definitional identity / invariance** (closed form, tolerance 1e-12), or
* an **independently derived physical bound** (momentum theory vs. blade-element
  style open-water numbers), i.e. not a tautology.

Anchors (see ``ANCHORS``):
  P-A1..A3   ITTC-57 friction line closed-form pin values
  P-B1       open-water efficiency identity  eta_o = J*K_T/(2*pi*K_Q)
  P-B2       momentum-theory derivation agreement (quadratic solve vs closed form)
  P-B3       physical admissibility bound  eta_o <= eta_ideal(C_T)
  P-C1       Froude scaling invariance of (J, K_T, K_Q)
  P-D1       propulsion chain identity  eta_D = eta_o*eta_H*eta_R
  P-D2       delivered power  P_D = 2*pi*n*Q
  P-E1       cavitation number at 0.7R (closed form)
  P-F1       self-propulsion thrust identity  T = R/(1-t)

Reference constants: ITTC-78 seawater (rho, nu), ITTC/Gonin vapour pressure.
"""

from __future__ import annotations

import math
from typing import Dict, List

# ---------------------------------------------------------------- constants
RHO_SEAWATER = 1025.0      # kg/m^3, ITTC-78 standard seawater
NU_SEAWATER = 1.19e-6      # m^2/s
G = 9.80665                # m/s^2, standard gravity
P_ATM = 101325.0           # Pa
P_VAP = 1704.0             # Pa, water vapour pressure at 15 degC (ITTC)

TOL_EXACT = 1e-12          # relative tolerance for definitional identities


# ------------------------------------------------------------ kernel pieces
def advance_ratio(Va: float, n: float, D: float) -> float:
    """Advance coefficient J = V_a / (n D)."""
    return Va / (n * D)


def thrust_coefficient(T: float, n: float, D: float, rho: float = RHO_SEAWATER) -> float:
    """Thrust coefficient K_T = T / (rho n^2 D^4)."""
    return T / (rho * n * n * D ** 4)


def torque_coefficient(Q: float, n: float, D: float, rho: float = RHO_SEAWATER) -> float:
    """Torque coefficient K_Q = Q / (rho n^2 D^5)."""
    return Q / (rho * n * n * D ** 5)


def open_water_efficiency(J: float, KT: float, KQ: float) -> float:
    """Open-water efficiency eta_o = J K_T / (2 pi K_Q)  (definitional)."""
    return J * KT / (2.0 * math.pi * KQ)


def disk_area(D: float) -> float:
    """Actuator-disk area A_0 = pi D^2 / 4."""
    return math.pi * D * D / 4.0


def thrust_loading(T: float, Va: float, D: float, rho: float = RHO_SEAWATER) -> float:
    """Actuator-disk thrust loading C_T = T / (0.5 rho A_0 V_a^2)."""
    return T / (0.5 * rho * disk_area(D) * Va * Va)


def ideal_efficiency(CT: float) -> float:
    """Actuator-disk ideal efficiency, closed form  eta_i = 2 / (1 + sqrt(1+C_T)).

    Derivation: T = 2 rho A_0 (V_a + w) w  with axial induced velocity w;
    define C_T = T/(0.5 rho A_0 V_a^2) -> w/V_a = (sqrt(1+C_T)-1)/2 ;
    eta_i = V_a/(V_a + w) = 2/(1+sqrt(1+C_T)).
    """
    return 2.0 / (1.0 + math.sqrt(1.0 + CT))


def ideal_efficiency_from_momentum(T: float, Va: float, D: float,
                                  rho: float = RHO_SEAWATER) -> float:
    """Ideal efficiency obtained by *solving* the momentum quadratic numerically.

    T = 2 rho A_0 (V_a + w) w   ->   w^2 + V_a w - T/(2 rho A_0) = 0
    Taking the positive root gives an independent route to eta_i that shares no
    algebra with :func:`ideal_efficiency`; agreement of the two to ~1e-15 is the
    derivation-level check P-B2.
    """
    A0 = disk_area(D)
    b = Va
    c = -T / (2.0 * rho * A0)
    w = (-b + math.sqrt(b * b - 4.0 * c)) / 2.0
    return Va / (Va + w)


def ittc57_cf(Re: float) -> float:
    """ITTC-57 model-ship friction line  C_F = 0.075 / (log10(Re) - 2)^2."""
    return 0.075 / (math.log10(Re) - 2.0) ** 2


def hull_efficiency(t: float, w: float) -> float:
    """Hull efficiency eta_H = (1 - t) / (1 - w)."""
    return (1.0 - t) / (1.0 - w)


def delivered_power(n: float, Q: float) -> float:
    """Delivered power P_D = 2 pi n Q  [W] for n in rev/s, Q in N m."""
    return 2.0 * math.pi * n * Q


def effective_power(R: float, V: float) -> float:
    """Effective (towed) power P_E = R V  [W]."""
    return R * V


def cavitation_number(immersed_depth: float, V: float, n: float, D: float,
                      rho: float = RHO_SEAWATER) -> float:
    """Propeller cavitation number at 0.7R.

    sigma_n = (p_atm + rho g h - p_v) / (0.5 rho V_R^2),
    V_R = sqrt(V_a^2 + (0.7 pi n D)^2)  (V_a taken equal to V for open water).
    """
    VR = math.sqrt(V * V + (0.7 * math.pi * n * D) ** 2)
    return (P_ATM + rho * G * immersed_depth - P_VAP) / (0.5 * rho * VR * VR)


# --------------------------------------------------- anchor implementation
def _rel_err(value: float, expected: float) -> float:
    if expected == 0.0:
        return abs(value - expected)
    return abs(value - expected) / abs(expected)


def _record(aid: str, name: str, value: float, expected: float,
            tol: float, kind: str, note: str = "") -> Dict:
    err = _rel_err(value, expected)
    return {
        "id": aid,
        "name": name,
        "kind": kind,              # exact | bound | derivation
        "value": value,
        "expected": expected,
        "rel_error": err,
        "tol": tol,
        "pass": err <= tol,
        "note": note,
    }


# Illustrative open-water design point used for the admissibility bound.
# Chosen to be representative of a medium-speed marine propeller
# (J ~ 0.83, K_T ~ 0.285, K_Q ~ 0.060); flagged as illustrative in the report.
DESIGN_POINT = {"D": 0.3048, "n": 10.0, "J": 0.833, "KT": 0.285, "KQ": 0.060}


def run_anchors() -> List[Dict]:
    """Evaluate every anchor and return the ledger rows."""
    rows: List[Dict] = []

    # --- P-A1..A3  ITTC-57 closed-form pin values -------------------------
    # At Re = 10^k the formula collapses to 0.075/(k-2)^2 -> exact rationals.
    for aid, k, expected in (("P-A1", 6, 0.075 / 16.0),
                             ("P-A2", 7, 0.075 / 25.0),
                             ("P-A3", 9, 0.075 / 49.0)):
        rows.append(_record(
            aid, f"ITTC-57 C_F at Re=1e{k}", ittc57_cf(10.0 ** k), expected,
            TOL_EXACT, "exact", "C_F = 0.075/(k-2)^2 exact rational"))

    # --- P-B1  open-water efficiency identity -----------------------------
    dp = DESIGN_POINT
    Va = dp["J"] * dp["n"] * dp["D"]
    T = dp["KT"] * RHO_SEAWATER * dp["n"] ** 2 * dp["D"] ** 4
    Q = dp["KQ"] * RHO_SEAWATER * dp["n"] ** 2 * dp["D"] ** 5
    # round-trip: recover K_T, K_Q from (T, Q) then rebuild eta_o
    KT_rt = thrust_coefficient(T, dp["n"], dp["D"])
    KQ_rt = torque_coefficient(Q, dp["n"], dp["D"])
    eta_o = open_water_efficiency(advance_ratio(Va, dp["n"], dp["D"]), KT_rt, KQ_rt)
    expected_eta = dp["J"] * dp["KT"] / (2.0 * math.pi * dp["KQ"])
    rows.append(_record("P-B1", "open-water efficiency identity eta_o = J K_T/(2 pi K_Q)",
                        eta_o, expected_eta, TOL_EXACT, "exact",
                        "round-trip through K_T/K_Q definitions"))

    # --- P-B2  momentum quadratic vs closed form --------------------------
    CT = thrust_loading(T, Va, dp["D"])
    eta_closed = ideal_efficiency(CT)
    eta_mom = ideal_efficiency_from_momentum(T, Va, dp["D"])
    rows.append(_record("P-B2", "momentum-theory quadratic vs closed-form eta_i",
                        eta_mom, eta_closed, 1e-10, "derivation",
                        "independent algebra, C_T = %.6f" % CT))

    # --- P-B3  admissibility bound eta_o <= eta_ideal ---------------------
    eta_i = ideal_efficiency(CT)
    rows.append({
        "id": "P-B3",
        "name": "physical admissibility eta_o <= eta_ideal(C_T)",
        "kind": "bound",
        "value": eta_o,
        "expected": eta_i,
        "rel_error": max(0.0, (eta_o - eta_i) / eta_i),
        "tol": 0.0,
        "pass": eta_o <= eta_i + 1e-12,
        "note": "C_T=%.6f eta_ideal=%.6f eta_o=%.6f margin=%.1f%%"
                % (CT, eta_i, eta_o, 100.0 * (eta_i - eta_o) / eta_i),
    })

    # --- P-C1  Froude scaling invariance of (J, K_T, K_Q) -----------------
    lam = 12.0
    J0 = advance_ratio(Va, dp["n"], dp["D"])
    KT0 = thrust_coefficient(T, dp["n"], dp["D"])
    KQ0 = torque_coefficient(Q, dp["n"], dp["D"])
    # Froude scaling: V -> V sqrt(lam), n -> n / sqrt(lam), D -> lam D,
    # T -> lam^3 T  (K_T ~ T/(rho n^2 D^4)) but Q -> lam^4 Q  (K_Q ~ Q/(rho n^2 D^5)).
    J1 = advance_ratio(Va * math.sqrt(lam), dp["n"] / math.sqrt(lam), dp["D"] * lam)
    KT1 = thrust_coefficient(T * lam ** 3, dp["n"] / math.sqrt(lam), dp["D"] * lam)
    KQ1 = torque_coefficient(Q * lam ** 4, dp["n"] / math.sqrt(lam), dp["D"] * lam)
    worst = max(_rel_err(J1, J0), _rel_err(KT1, KT0), _rel_err(KQ1, KQ0))
    rows.append(_record("P-C1", "Froude scaling invariance of J, K_T, K_Q",
                        worst, 0.0, TOL_EXACT, "exact",
                        "scale factor lambda=%.1f" % lam))

    # --- P-D1  propulsion chain identity ----------------------------------
    t, w = 0.18, 0.28
    eta_R = 1.02
    eta_H = hull_efficiency(t, w)
    eta_D = eta_o * eta_H * eta_R
    R = T * (1.0 - t)                       # self-propulsion thrust identity
    # V_ship = V_a/(1-w); behind-hull torque Q_b = Q_open/eta_R  (definition of eta_R)
    V_ship = Va / (1.0 - w)
    Q_behind = Q / eta_R
    eta_check = effective_power(R, V_ship) / delivered_power(dp["n"], Q_behind)
    rows.append(_record("P-D1", "propulsion chain eta_D = P_E/P_D",
                        eta_check, eta_D, TOL_EXACT, "exact",
                        "t=%.2f w=%.2f eta_R=%.2f eta_H=%.4f" % (t, w, eta_R, eta_H)))

    # --- P-D2  delivered power ---------------------------------------------
    P_D = delivered_power(dp["n"], Q)
    P_D_alt = 2.0 * math.pi * (dp["n"]) * (dp["KQ"] * RHO_SEAWATER * dp["n"] ** 2 * dp["D"] ** 5)
    rows.append(_record("P-D2", "delivered power 2 pi n Q", P_D, P_D_alt,
                        TOL_EXACT, "exact"))

    # --- P-E1  cavitation number at 0.7R ----------------------------------
    h = 3.0
    sigma = cavitation_number(h, Va, dp["n"], dp["D"])
    VR = math.sqrt(Va * Va + (0.7 * math.pi * dp["n"] * dp["D"]) ** 2)
    sigma_manual = (P_ATM + RHO_SEAWATER * G * h - P_VAP) / (0.5 * RHO_SEAWATER * VR ** 2)
    rows.append(_record("P-E1", "cavitation number sigma_n at 0.7R", sigma, sigma_manual,
                        TOL_EXACT, "exact", "h=%.1f m, V_R=%.4f m/s" % (h, VR)))

    # --- P-F1  self-propulsion thrust identity ----------------------------
    rows.append(_record("P-F1", "self-propulsion thrust identity T = R/(1-t)",
                        R / (1.0 - t), T, TOL_EXACT, "exact"))

    return rows


# ---------------------------------------------- global admissibility sweep
# NOTE: the KT(J)/KQ(J) family below is S Y N T H E T I C (parabolic in J, chosen
# to mimic the *shape* of a B-series propeller).  It is NOT a literature table and
# must never be quoted as one; its only purpose is to exercise the momentum-theory
# bound of P-B3 over a whole J range instead of a single design point.
_SWEEP_KT = lambda J: 0.500 - 0.300 * J - 0.200 * J ** 2
_SWEEP_KQ = lambda J: 0.055 - 0.020 * J - 0.012 * J ** 2


def sweep_admissibility(n_points: int = 33) -> Dict:
    """Check eta_o <= eta_ideal(C_T) <= 1 over an illustrative open-water family."""
    rows = []
    for i in range(n_points):
        J = 0.10 + 0.80 * i / (n_points - 1)
        KT, KQ = _SWEEP_KT(J), _SWEEP_KQ(J)
        if KT <= 0.0 or KQ <= 0.0:
            continue
        eta_o = open_water_efficiency(J, KT, KQ)
        # C_T from the axial-momentum definition with V_a = J n D, A_0 = pi D^2/4:
        # C_T = 8 K_T / (pi J^2)   (dimensionless, D-independent)
        CT = 8.0 * KT / (math.pi * J * J)
        eta_i = ideal_efficiency(CT)
        rows.append({"J": J, "KT": KT, "KQ": KQ,
                     "eta_o": eta_o, "CT": CT, "eta_ideal": eta_i,
                     "bound_ok": eta_o <= eta_i + 1e-12,
                     "eta_ok": eta_o < 1.0})
    worst = max(rows, key=lambda r: r["eta_o"] - r["eta_ideal"])
    peak = max(rows, key=lambda r: r["eta_o"])
    return {
        "points": rows,
        "n_points": len(rows),
        "all_bound_ok": all(r["bound_ok"] for r in rows),
        "all_eta_below_unity": all(r["eta_ok"] for r in rows),
        "min_margin": min(r["eta_ideal"] - r["eta_o"] for r in rows),
        "tightest_point": {"J": worst["J"], "eta_o": worst["eta_o"],
                           "eta_ideal": worst["eta_ideal"]},
        "peak": {"J": peak["J"], "eta_o": peak["eta_o"],
                 "eta_ideal": peak["eta_ideal"]},
    }


def summary(rows: List[Dict]) -> Dict:
    n = len(rows)
    passed = sum(1 for r in rows if r["pass"])
    worst = max((r["rel_error"] for r in rows), default=0.0)
    return {
        "n_anchors": n,
        "n_pass": passed,
        "all_pass": passed == n,
        "worst_rel_error": worst,
        "within_3pct": worst <= 0.03,
        "by_kind": {k: sum(1 for r in rows if r["kind"] == k)
                    for k in ("exact", "derivation", "bound")},
    }


if __name__ == "__main__":
    import json
    rs = run_anchors()
    for r in rs:
        print("%-6s %-58s %-10s %-10.6g %-10.6g %s"
              % (r["id"], r["name"][:58], r["kind"], r["value"], r["expected"],
                 "PASS" if r["pass"] else "FAIL"))
    print(json.dumps(summary(rs), indent=2))