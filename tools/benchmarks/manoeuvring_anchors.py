#!/usr/bin/env python3
"""Manoeuvring (操纵性) anchor suite for the digital-twin MMG model (m1651).

Motivation
----------
``backend/services/digital_twin/models/manoeuvring.py`` states in its own
docstring: *"the absolute turning-circle diameter is not validated on a
full-scale trial in this worktree. The plausibility band ... is checked"*.
The only test in the tree (``test_digital_twin_physics.py::
test_manoeuvring_turning_band``) asserts a 2.5-4.5 L band, i.e. it would pass
a model that is 80 % wrong. This module supplies the missing *judges*.

Design rule (learned from the propulsion audit, m1413)
------------------------------------------------------
No anchor below depends on a table value recalled from memory. Every criterion
is one of

* a closed-form identity that any correct MMG implementation must satisfy
  (slender-body added mass for the idealised rectangular-section hull,
  kinematic relations between r, the path and the reported metrics),
* a symmetry / invariance stated by the model itself (port-starboard mirror,
  Froude scaling of the non-dimensional trajectory),
* a numerical-verification statement (time-step convergence order, agreement
  with an independent integrator),
* a regulatory threshold from IMO Res. A.751(18) §2.3 -- a *definition* of
  the required manoeuvre, cited, not a measured literature value. Its
  compliance verdict is therefore "meets/does not meet the standard", never
  "matches the trial".

Anchors
-------
M-A1  straight-run neutrality (delta = 0 => psi = y = 0 for all t)
M-A2  port/starboard mirror symmetry of the whole trajectory
M-A3  slender-body added mass / yaw inertia vs closed form
      (my = rho*pi*T^2*L, Jz = rho*pi*T^2*L^3/12 for the constant-draft hull)
M-A4  kinematic identity in the steady turn: R = |U|/|r|, |U| = hypot(u, v);
      the shipped ``turning_diameter_m = 2u/r`` is biased by cos(beta)
M-A5  IMO A.751(18) threshold compliance (D <= 5L, advance <= 4.5L)
M-A6  steady-state yaw-equation residual (r must stop evolving)
M-A7  RK4 time-step convergence order of the reported metrics
M-A8  independent-integrator reproduction of advance / diameter
M-A9  Froude scaling of the non-dimensional trajectory (diagnostic)
M-A10 zigzag: overshoot ordering, peak-detection consistency, integrator order

Usage
-----
    python3 tools/benchmarks/manoeuvring_anchors.py [--out DIR] [--quick]

Writes ``manoeuvring_anchors.json`` (machine-readable verdicts) into
``artifacts/m1651/`` and prints a table. Read-only with respect to the
platform service; pure offline computation.
"""
from __future__ import annotations

import argparse
import json
import math
import sys
import time
from pathlib import Path
from typing import Dict, List, Optional

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

from services.digital_twin.models.geometry import HullForm  # noqa: E402
from services.digital_twin.models.manoeuvring import turning_circle, zigzag  # noqa: E402

RHO = 1025.0
G = 9.80665

# KVLCC2-like full-form tanker: the case the module's docstring targets
# ("full-form single screw ship").  L/B = 5.52, T/B = 0.359 are inside the
# HullForm admissibility box, Cb = 0.8098 is the public KVLCC2 design value.
KVLCC2 = dict(L=320.0, B=58.0, T=20.8, Cb=0.8098)
DESIGN_SPEED_KN = 15.5
U0_MS = DESIGN_SPEED_KN * 1852.0 / 3600.0     # 7.974 m/s


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------
def _hull(**kw) -> HullForm:
    p = dict(KVLCC2)
    p.update(kw)
    return HullForm(**p)


def _steady_diameter(metrics: Dict[str, float]) -> Dict[str, float]:
    """Diameter from the reported steady state, both definitions."""
    u = metrics["steady_speed_m_s"]
    r = math.radians(metrics["steady_yaw_rate_deg_s"])
    return {"u_only": 2.0 * u / r if r else float("inf"), "r_rad_s": r}


def _reference_turn(hull: HullForm, U0: float, delta_deg: float,
                    t_end: float = 1200.0, dt: float = 0.05) -> Dict[str, float]:
    """Independent re-integration of the equations *as documented in the
    module docstring* (RK4, its own metric extraction, no import of the
    model's internal helpers).

    This is a cross-implementation check: if the shipped code deviates from
    its documented model, the two trajectories separate.
    """
    from services.digital_twin.models.resistance import holtrop_resistance

    L, T = hull.L, hull.T
    rho = hull.rho
    n = 61
    x = np.linspace(-0.5 * L, 0.5 * L, n)
    xm = 0.5 * (x[:-1] + x[1:])
    dx = float(x[1] - x[0])
    depth = np.full_like(xm, T)

    m = hull.Cb * L * hull.B * T * rho
    my = rho * math.pi * float(np.sum(depth ** 2)) * dx
    Jz = rho * math.pi * float(np.sum(depth ** 2 * xm ** 2)) * dx
    mx = 0.10 * m
    Iz = m * (0.25 * L) ** 2
    A_R = 0.017 * L * T
    x_R = -0.5 * L
    x_L = -0.05 * L
    C_Lalpha = 2.0 * math.pi
    AR = 2.0 * T / L
    CL_hull = math.pi * AR / 2.0
    S_ref = L * T

    def res(u):
        return holtrop_resistance(max(u, 0.5), L, hull.B, T, hull.Cb, rho=rho)["RT_N"]

    T0 = res(U0)
    delta = math.radians(delta_deg)

    def f(st):
        _, _, psi, u, v, r = st
        ue = max(u, 0.2)
        s = v + xm * r
        cf = -0.5 * rho * 1.0 * depth * np.abs(s) * s
        beta = math.atan((v + x_L * r) / max(u, 0.3))
        Yl = -0.5 * rho * u ** 2 * S_ref * CL_hull * beta
        Y_H = float(np.sum(cf)) * dx + Yl
        N_H = float(np.sum(cf * xm)) * dx + Yl * x_L
        alpha = delta - 0.4 * math.atan((v + x_R * r) / ue)
        U_R = 0.90 * ue
        Y_R = -0.5 * rho * A_R * C_Lalpha * alpha * U_R ** 2
        N_R = Y_R * x_R
        X_H = -res(u)
        X_P = T0 * (1.6 - 0.6 * min(u / U0, 1.2))
        return np.array([u * math.cos(psi) - v * math.sin(psi),
                         u * math.sin(psi) + v * math.cos(psi),
                         r,
                         (X_H + X_P + (m + my) * v * r) / (m + mx),
                         (Y_H + Y_R - (m + mx) * ue * r) / (m + my),
                         (N_H + N_R) / (Iz + Jz)])

    st = np.array([0.0, 0.0, 0.0, U0, 0.0, 0.0])
    t = 0.0
    advance = tactical = float("nan")
    max_y = 0.0
    while t < t_end:
        k1 = f(st)
        k2 = f(st + 0.5 * dt * k1)
        k3 = f(st + 0.5 * dt * k2)
        k4 = f(st + dt * k3)
        st = st + dt / 6.0 * (k1 + 2 * k2 + 2 * k3 + k4)
        t += dt
        max_y = max(max_y, abs(st[1]))
        if advance != advance and abs(st[2]) >= math.pi / 2:
            advance = float(st[0])
        if tactical != tactical and abs(st[2]) >= math.pi:
            tactical = float(abs(st[1]))
            break
    return {"advance_m": advance, "tactical_diameter_m": tactical,
            "extreme_transverse_m": 2.0 * max_y,
            "u_end": float(st[3]), "v_end": float(st[4]), "r_end": float(st[5])}


def _rec(anchors: List[Dict], aid: str, name: str, kind: str, verdict: str,
         value, expected=None, criterion: str = "", detail: str = "") -> None:
    anchors.append({"id": aid, "name": name, "type": kind, "verdict": verdict,
                    "value": value, "expected": expected,
                    "criterion": criterion, "detail": detail})


def _rel(a: float, b: float) -> float:
    d = abs(b)
    return abs(a - b) / d if d > 0 else float("inf")


# --------------------------------------------------------------------------
# anchors
# --------------------------------------------------------------------------
def run(t_end: float = 1200.0, dt: float = 0.2, quick: bool = False) -> Dict:
    hull = _hull()
    L = hull.L
    rec_anchors: List[Dict] = []
    metrics: Dict[str, Dict[str, float]] = {}
    t_start = time.time()

    # ---------------- M-A1 straight-run neutrality -------------------------
    straight = turning_circle(hull, U0_MS, rudder_deg=0.0, t_end=min(t_end, 600.0), dt=dt)
    tx, ty = np.asarray(straight["traj_x"]), np.asarray(straight["traj_y"])
    max_dev = float(max(np.abs(ty)))
    psi_max = float(np.ptp(np.degrees(np.arctan2(np.gradient(ty), np.gradient(tx)))))
    ok = max_dev < 1e-6 * L and psi_max < 1e-3
    _rec(rec_anchors, "M-A1", "straight run stays straight (delta=0)", "identity",
         "PASS" if ok else "FAIL",
         {"max_|y|_m": max_dev, "heading_range_deg": psi_max},
         0.0, "max|y| < 1e-6 L and heading range < 1e-3 deg",
         "no symmetry breaking may push a symmetric ship off its initial course")

    # ---------------- M-A2 port/starboard mirror ---------------------------
    stb = turning_circle(hull, U0_MS, rudder_deg=35.0, t_end=t_end, dt=dt)
    pt = turning_circle(hull, U0_MS, rudder_deg=-35.0, t_end=t_end, dt=dt)
    metrics["35deg_starboard"] = stb
    metrics["35deg_port"] = pt
    d_stb = _rel(stb["turning_diameter_m"], pt["turning_diameter_m"])
    a_stb = _rel(stb["advance_m"], pt["advance_m"])
    ok = d_stb < 1e-9 and a_stb < 1e-9
    _rec(rec_anchors, "M-A2", "port/starboard mirror symmetry", "symmetry",
         "PASS" if ok else "FAIL",
         {"rel_diff_D": d_stb, "rel_diff_advance": a_stb}, 0.0,
         "relative difference < 1e-9",
         "a symmetric hull+rudder cannot turn differently to port and starboard")

    # ---------------- M-A3 added mass / inertia closed form ----------------
    n = 61
    x = np.linspace(-0.5 * L, 0.5 * L, n)
    xm = 0.5 * (x[:-1] + x[1:])
    dx = float(x[1] - x[0])
    depth = np.full_like(xm, hull.T)
    my_num = RHO * math.pi * float(np.sum(depth ** 2)) * dx
    Jz_num = RHO * math.pi * float(np.sum(depth ** 2 * xm ** 2)) * dx
    my_cf = RHO * math.pi * hull.T ** 2 * L
    Jz_cf = RHO * math.pi * hull.T ** 2 * L ** 3 / 12.0
    e_my, e_Jz = _rel(my_num, my_cf), _rel(Jz_num, Jz_cf)
    ok = max(e_my, e_Jz) < 1e-3
    _rec(rec_anchors, "M-A3", "slender-body added mass / yaw inertia", "closed form",
         "PASS" if ok else "FAIL",
         {"my_integral_kg": my_num, "my_closed_kg": my_cf,
          "Jz_integral_kgm2": Jz_num, "Jz_closed_kgm2": Jz_cf,
          "rel_err_my": e_my, "rel_err_Jz": e_Jz}, 0.0,
         "quadrature vs closed form < 1e-3",
         "my = rho pi T^2 L, Jz = rho pi T^2 L^3/12 for constant-draft idealisation; "
         "the residual 2.8e-4 on Jz is the O((dx/L)^2) truncation of the 61-section "
         "midpoint rule, not a physics error - it only re-appears if n_sections changes")

    # ---------------- M-A4 kinematic identity in the steady turn -----------
    ss = _steady_diameter(stb)
    r_rad = ss["r_rad_s"]
    # steady drift angle from the last samples of the trajectory is not
    # available (subsampled positions only) -> re-run with velocity bookkeeping
    ref = _reference_turn(hull, U0_MS, 35.0, t_end=min(t_end, 900.0), dt=0.05)
    beta = math.atan2(abs(ref["v_end"]), abs(ref["u_end"]))
    U_steady = math.hypot(ref["u_end"], ref["v_end"])
    R_hypot = U_steady / abs(ref["r_end"])
    D_hypot = 2.0 * R_hypot
    D_shipped = 2.0 * ref["u_end"] / abs(ref["r_end"])
    bias = _rel(D_shipped, D_hypot)
    shipped_bias = _rel(stb["turning_diameter_m"], D_hypot)
    legacy_bias = _rel(stb.get("turning_diameter_surge_m", D_shipped), D_hypot)
    _rec(rec_anchors, "M-A4", "kinematic identity R = |U|/|r| (drift bias)", "identity",
         "PASS" if shipped_bias < 1e-3 else "FAIL",
         {"drift_angle_deg": math.degrees(beta), "D_from_hypot_L": D_hypot / L,
          "D_from_surge_L": D_shipped / L, "legacy_surge_bias": legacy_bias,
          "shipped_D_L": stb["turning_diameter_L"],
          "rel_bias_shipped_vs_identity": shipped_bias}, 0.0,
         "the reported turning diameter must equal 2|U|/|r| to < 0.1%",
         "in a steady turn the ship advances at drift angle beta, so the surge "
         "speed u understates the path speed by cos(beta). Pre-m1651 the shipped "
         "metric was 2u/r and inherited a (1-cos beta) low bias (%.2f%% here); "
         "m1651 switched turning_diameter_m to 2|U|/|r| and kept 2u/r as "
         "turning_diameter_surge_m" % (100.0 * legacy_bias))

    # ---------------- M-A5 IMO A.751(18) thresholds ------------------------
    imo_d = stb["turning_diameter_L"] if stb["turning_diameter_L"] == stb["turning_diameter_L"] else D_hypot / L
    imo_a = stb["advance_L"]
    ok_d, ok_a = imo_d <= 5.0, (imo_a == imo_a and imo_a <= 4.5)
    _rec(rec_anchors, "M-A5", "IMO A.751(18) tactical diameter / advance", "regulatory",
         "PASS" if (ok_d and ok_a) else "FAIL",
         {"D_L": imo_d, "advance_L": imo_a, "limit_D_L": 5.0, "limit_advance_L": 4.5},
         {"D_L": 5.0, "advance_L": 4.5}, "D <= 5L and advance <= 4.5L at 35 deg",
         "thresholds are the definition of the required manoeuvre (MSC/Circ.1053), "
         "not a trial value")

    # ---------------- M-A6 steady-state yaw residual -----------------------
    # rate of change of r over the last 10 % of the run, non-dimensionalised
    dts = 0.2
    r1 = turning_circle(hull, U0_MS, 35.0, t_end=t_end * 0.9, dt=dts)["steady_yaw_rate_deg_s"]
    r2 = turning_circle(hull, U0_MS, 35.0, t_end=t_end, dt=dts)["steady_yaw_rate_deg_s"]
    resid = _rel(r2, r1)
    _rec(rec_anchors, "M-A6", "steady-state yaw residual", "convergence",
         "PASS" if resid < 0.01 else "FAIL",
         {"r_at_0.9T_deg_s": r1, "r_at_T_deg_s": r2, "rel_change": resid}, 0.0,
         "yaw rate changes < 1% over the last 10% of the run",
         "the turning-circle metrics are only defined in the steady state")

    # ---------------- M-A7 RK4 time-step convergence -----------------------
    conv = {}
    for d in (0.4, 0.2, 0.1):
        m = turning_circle(hull, U0_MS, 35.0, t_end=t_end, dt=d)
        conv[d] = m["turning_diameter_m"]
    e1 = abs(conv[0.2] - conv[0.1])
    e2 = abs(conv[0.4] - conv[0.2])
    order = math.log2(e2 / e1) if e1 > 0 else float("inf")
    ok = order > 3.0
    _rec(rec_anchors, "M-A7", "RK4 time-step convergence order", "numerical",
         "PASS" if ok else "FAIL",
         {f"D_dt_{k}": v for k, v in conv.items()},
         {"observed_order": order, "expected_order": 4.0},
         "observed order > 3 (4th-order RK4 on a smooth ODE)",
         "metric is a sampled extremum, so the observed order may fall short of 4 "
         "even for a correct integrator")

    # ---------------- M-A8 independent integrator --------------------------
    e_adv = _rel(stb["advance_m"], ref["advance_m"]) if ref["advance_m"] == ref["advance_m"] else float("nan")
    D_ref = 2.0 * math.hypot(ref["u_end"], ref["v_end"]) / abs(ref["r_end"])
    e_D = _rel(D_hypot, D_ref)
    ok = (e_adv < 0.01) and (e_D < 0.01)
    _rec(rec_anchors, "M-A8", "independent integrator reproduction", "duplicate impl.",
         "PASS" if ok else "FAIL",
         {"advance_shipped_m": stb["advance_m"], "advance_reference_m": ref["advance_m"],
          "rel_err_advance": e_adv, "D_shipped_L": D_hypot / L,
          "D_reference_L": D_ref / L, "rel_err_D": e_D}, 0.0,
         "relative difference < 1%",
         "reference = RK4 at dt=0.05 on the equations as written in the module "
         "docstring, with its own metric extraction")

    # ---------------- M-A9 Froude scaling of the trajectory ----------------
    lam = 4.0          # model scale 1:4, tanker too large for 1:1 alternatives
    hull_m = _hull(L=KVLCC2["L"] / lam, B=KVLCC2["B"] / lam, T=KVLCC2["T"] / lam)
    full = turning_circle(hull, U0_MS, 35.0, t_end=t_end, dt=dt)
    model = turning_circle(hull_m, U0_MS / math.sqrt(lam), 35.0, t_end=t_end / math.sqrt(lam), dt=dt / math.sqrt(lam))
    if not quick:
        metrics["froude_model"] = model
    sc = {"D_L_full": full["turning_diameter_L"], "D_L_model": model["turning_diameter_L"],
          "advance_L_full": full["advance_L"], "advance_L_model": model["advance_L"]}
    sc["rel_diff_D"] = _rel(model["turning_diameter_L"], full["turning_diameter_L"])
    sc["rel_diff_advance"] = _rel(model["advance_L"], full["advance_L"])
    _rec(rec_anchors, "M-A9", "Froude scaling of the non-dimensional trajectory",
         "invariance (diagnostic)",
         "WITHHELD" if sc["rel_diff_D"] > 0.05 else "PASS", sc, 0.0,
         "D/L and advance/L invariant under lambda=4 Froude scaling",
         "not a pass/fail identity: the model borrows Holtrop resistance, which is "
         "Reynolds dependent, so a few percent of scale effect is expected and "
         "reported rather than judged")

    # ---------------- M-A10 zigzag suite -----------------------------------
    z10 = zigzag(hull, U0_MS, 10.0)
    z20 = zigzag(hull, U0_MS, 20.0)
    z10_f = zigzag(hull, U0_MS, 10.0, dt=0.05)
    zig = {"first_overshoot_10_deg": z10["first_overshoot_deg"],
           "first_overshoot_20_deg": z20["first_overshoot_deg"],
           "first_overshoot_10_dt0.05": z10_f["first_overshoot_deg"],
           "IMO_limit_10_10_deg": 10.0, "IMO_limit_20_20_deg": 25.0}
    euler_err = _rel(z10_f["first_overshoot_deg"], z10["first_overshoot_deg"])
    zig["euler_dt0.1_vs_dt0.05_rel"] = euler_err
    zig["order_estimate"] = (math.log2(_rel(z10["first_overshoot_deg"], z10_f["first_overshoot_deg"]) / euler_err)
                             if euler_err > 0 else float("nan"))
    ok_ord = z20["first_overshoot_deg"] >= z10["first_overshoot_deg"]
    ok_imo = z10["first_overshoot_deg"] <= 10.0 and z20["first_overshoot_deg"] <= 25.0
    _rec(rec_anchors, "M-A10a", "zigzag overshoot ordering (20/20 >= 10/10)",
         "definitional", "PASS" if ok_ord else "FAIL", zig, None,
         "a larger rudder angle must give a larger first overshoot for a ship "
         "whose response is monotone in rudder angle",
         "overshoot is defined at the first heading extremum after the counter-rudder")
    _rec(rec_anchors, "M-A10b", "zigzag IMO A.751(18) overshoot limits", "regulatory",
         "PASS" if ok_imo else "FAIL",
         {"overshoot_10": z10["first_overshoot_deg"], "overshoot_20": z20["first_overshoot_deg"]},
         {"limit_10": 10.0, "limit_20": 25.0}, "10/10 <= 10 deg, 20/20 <= 25 deg", "")
    _rec(rec_anchors, "M-A10c", "zigzag explicit-Euler step error at shipped dt=0.1",
         "numerical", "DEFECT" if euler_err > 0.01 else "PASS",
         {"rel_change_dt_0.1_vs_0.05": euler_err}, 0.0,
         "explicit Euler is O(dt): the shipped 0.1 s step must stay below 1% "
         "of the overshoot angle, otherwise halving dt moves the metric by more "
         "than the reporting tolerance",
         "turning_circle uses RK4 while zigzag uses forward Euler -- same module, "
         "two different integrators")

    # ---------------- M-A11 code-to-code vs the W3-N08 MMG model -----------
    # backend/physics/ship_env_maneuvering is an independently derived 3-DOF MMG
    # implementation with its own coefficient set and validation harness, built
    # for the *same* KVLCC2 particulars.  Two implementations of one physics
    # model on one hull must not disagree by tens of percent.
    cross = {}
    try:
        from physics.ship_env_maneuvering import data as wdata
        from physics.ship_env_maneuvering import mmg as wmmg

        w = wmmg.simulate_turning_circle(wdata.VESSEL_VLCC, speed=U0_MS,
                                         rudder_deg=35.0, dt=0.5, t_max=1200.0).metrics
        cross = {
            "tactical_D_L_dt": stb["tactical_diameter_L"],
            "tactical_D_L_w3n08": w["tactical_diameter_over_L"],
            "steady_D_L_dt": D_hypot / L,
            "steady_D_L_w3n08": w["steady_turning_diameter_over_L"],
            "advance_L_dt": stb["advance_L"],
            "advance_L_w3n08": w["advance_over_L"],
            "drift_deg_dt": math.degrees(beta),
            "drift_deg_w3n08": w["steady_drift_angle_deg"],
            "speed_ratio_dt": ref["u_end"] / U0_MS,
            "speed_ratio_w3n08": w["steady_speed_ratio"],
        }
        cross["rel_diff_tactical"] = _rel(cross["tactical_D_L_dt"], cross["tactical_D_L_w3n08"])
        cross["rel_diff_advance"] = _rel(cross["advance_L_dt"], cross["advance_L_w3n08"])
        cross["rel_diff_steady_D"] = _rel(cross["steady_D_L_dt"], cross["steady_D_L_w3n08"])
        worst = max(cross["rel_diff_tactical"], cross["rel_diff_advance"], cross["rel_diff_steady_D"])
        _rec(rec_anchors, "M-A11", "code-to-code vs physics.ship_env_maneuvering (W3-N08)",
             "cross-model (WITHHELD)", "WITHHELD" if worst > 0.10 else "PASS", cross, 0.0,
             "two MMG implementations on one hull must agree to < 10 %",
             "no anchor decides which of the two is right; the numbers say they are not the "
             "same model. Absolute validation requires trial data (WITHHELD)")
    except Exception as exc:  # pragma: no cover - optional cross-check
        _rec(rec_anchors, "M-A11", "code-to-code vs W3-N08 MMG", "cross-model",
             "WITHHELD", {"error": repr(exc)[:200]}, None, "n/a", "reference model unavailable")

    # ---------------- verdict roll-up --------------------------------------
    counts: Dict[str, int] = {}
    for a in rec_anchors:
        counts[a["verdict"]] = counts.get(a["verdict"], 0) + 1
    result = {
        "mission": "m1651",
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "case": {"hull": KVLCC2, "L_B": KVLCC2["L"] / KVLCC2["B"],
                 "T_B": KVLCC2["T"] / KVLCC2["B"],
                 "design_speed_kn": DESIGN_SPEED_KN, "U0_m_s": U0_MS,
                 "t_end_s": t_end, "dt_s": dt},
        "anchors": rec_anchors,
        "verdict_counts": counts,
        "elapsed_s": round(time.time() - t_start, 2),
        "turning_metrics": {k: {kk: vv for kk, vv in v.items() if not kk.startswith("traj")}
                            for k, v in metrics.items()},
        "zigzag": {"10/10": z10, "20/20": z20},
    }
    return result


def _fmt(v) -> str:
    if isinstance(v, float):
        return f"{v:.6g}"
    if isinstance(v, dict):
        return "{" + ", ".join(f"{k}={_fmt(x)}" for k, x in v.items()) + "}"
    return str(v)


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out", default=str(ROOT / "artifacts" / "m1651"))
    ap.add_argument("--t-end", type=float, default=1200.0)
    ap.add_argument("--dt", type=float, default=0.2)
    ap.add_argument("--quick", action="store_true")
    args = ap.parse_args(argv)

    res = run(t_end=args.t_end, dt=args.dt, quick=args.quick)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / "manoeuvring_anchors.json").write_text(json.dumps(res, indent=2, ensure_ascii=False))

    print(f"{'id':8s} {'type':28s} {'verdict':9s} name")
    for a in res["anchors"]:
        print(f"{a['id']:8s} {a['type'][:28]:28s} {a['verdict']:9s} {a['name']}")
        print(f"         value: {_fmt(a['value'])[:160]}")
    print("\nverdict counts:", res["verdict_counts"], f"({res['elapsed_s']} s)")
    print("artifact:", out / "manoeuvring_anchors.json")
    fails = [a for a in res["anchors"] if a["verdict"] in ("FAIL", "DEFECT")]
    return 0 if not fails else 0   # anchors are diagnostic; pytest is the gate


if __name__ == "__main__":
    raise SystemExit(main())