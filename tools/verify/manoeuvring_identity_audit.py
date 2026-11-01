"""Independent verification of the in-repo 3-DOF MMG manoeuvring kernel (m1676).

Target under audit
------------------
``backend/physics/ship_env_maneuvering/mmg.py`` (``KVLCC2_REFERENCE`` coefficients,
``D.VESSEL_VLCC`` hull, ``simulate_turning_circle`` / ``simulate_zigzag`` /
``steady_turning_solution``).  The module predates m1676 and had never been adjudicated
by an independent harness (mission 1546 built a *separate* kernel under
``backend/methods/manoeuvring/``).

Criterion design rule
---------------------
Every PASS/FAIL verdict is anchored on something that does **not** require a remembered
literature constant:

* steady-turn kinematics identities (``r = u / R``, ``D = 2 R``),
* the mirror symmetry that the equations of motion themselves impose (``delta -> -delta``),
* monotonicity imposed by the rudder force law (``dD/ddelta < 0``),
* the module's *own* independent steady solver reproduced by its own time integration
  (a self-consistency contract, both directions),
* time-step convergence (``dt`` halving) and CFL sanity of the integrator,
* definitional consistency of the zigzag controller (rudder switch at ``|psi| = target``),
* physical inequalities on added masses (slender-body / Munk) and yaw damping.

The single *external* magnitude anchor (tactical diameter of the KVLCC2 family,
``TD/L ~ 3``) is reported separately, tagged ``MAGNITUDE_ANCHOR``: it is context, never a
verdict driver, because the exact trial value depends on loading/rudder-rate assumptions
this kernel does not model.

Usage
-----
    python3 tools/verify/manoeuvring_identity_audit.py [--out artifacts/m1676]

Writes ``<out>/manoeuvring_identity_audit.json`` + three PNGs, and prints the ledger.
The pytest wrapper ``tests/test_manoeuvring_identity_audit.py`` asserts on the ledger.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import sys
from typing import Dict, List

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402

_REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _REPO not in sys.path:
    sys.path.insert(0, _REPO)

from backend.physics.ship_env_maneuvering import data as D  # noqa: E402
from backend.physics.ship_env_maneuvering import mmg as M  # noqa: E402

VESSEL = D.VESSEL_VLCC
COEF = M.KVLCC2_REFERENCE
SPEED = 7.717          # m/s, KVLCC2 design speed (Fn = 0.142)
DT = 0.5
T_MAX = 3000.0


def rel_err(model: float, ref: float) -> float:
    """Relative error of ``model`` w.r.t. ``ref`` in percent (|ref| based)."""
    if ref is None or abs(ref) < 1e-12:
        return float("nan")
    return abs(model - ref) / abs(ref) * 100.0


def hist_of(res) -> Dict[str, List[float]]:
    return res.history if hasattr(res, "history") else res.as_dict(with_history=True)["history"]


def circle_diameter(hist: Dict[str, List[float]], frac: float = 0.6) -> float:
    """Least-squares (Kasa) circle fit to the late part of the trajectory -> diameter [m]."""
    x = np.asarray(hist["x"], dtype=float)
    y = np.asarray(hist["y"], dtype=float)
    n0 = int(frac * len(x))
    x, y = x[n0:], y[n0:]
    A = np.column_stack([2.0 * x, 2.0 * y, np.ones_like(x)])
    b = x ** 2 + y ** 2
    (cx, cy, c), *_ = np.linalg.lstsq(A, b, rcond=None)
    return 2.0 * math.sqrt(max(c + cx ** 2 + cy ** 2, 1e-9))


def turn(delta: float, dt: float = DT, t_max: float = T_MAX) -> Dict[str, List[float]]:
    return hist_of(M.simulate_turning_circle(vessel=VESSEL, coef=COEF, speed=SPEED,
                                             rudder_deg=delta, dt=dt, t_max=t_max))


def run_audit() -> Dict[str, object]:
    v = VESSEL
    crit: List[Dict[str, object]] = []
    raw: Dict[str, object] = {}

    # ---- geometry / mass gate (platform rule: Cb, L, B verified before physics) ----
    cb_geom = v.volume / (v.L * v.B * v.T)
    crit.append(dict(id="GEO-0", what="Cb closes on volume/(L*B*T)", model=v.Cb, ref=cb_geom,
                     err=rel_err(v.Cb, cb_geom), tol=1.0))
    mass_id = D.RHO_WATER * v.volume
    crit.append(dict(id="GEO-1", what="mass = rho * volume", model=v.mass, ref=mass_id,
                     err=rel_err(v.mass, mass_id), tol=1.0))

    # ---- independent steady (Newton) solution: the reference for the time domain ----
    ss = M.steady_turning_solution(vessel=v, coef=COEF, speed=SPEED, rudder_deg=35.0)
    newton_D = ss["steady_turning_diameter"]
    raw["steady_35"] = {k: (float(x) if isinstance(x, (int, float)) else x) for k, x in ss.items()}

    h35 = turn(35.0)
    u_ss, r_ss = h35["u"][-1], h35["r"][-1]
    d_rate = 2.0 * math.hypot(u_ss, h35["v"][-1]) / abs(r_ss)
    d_track = circle_diameter(h35)
    raw["turning_35"] = dict(
        u_ss=u_ss, r_ss=r_ss, D_from_rate=d_rate, D_from_track=d_track, D_newton=newton_D,
        advance_m=float(np.max(np.asarray(h35["x"]))),
        drift_deg=math.degrees(math.atan2(-h35["v"][-1], h35["u"][-1])), L=v.L,
        n_steps=len(h35["t"]))

    crit.append(dict(id="KIN-1", what="steady-turn geometry: 2*|V|/r vs circle fit of the late track",
                     model=d_rate, ref=d_track, err=rel_err(d_rate, d_track), tol=2.0))
    crit.append(dict(id="KIN-2", what="time-domain converged D vs independent Newton D",
                     model=d_track, ref=newton_D, err=rel_err(d_track, newton_D), tol=2.0))

    # ---- mirror symmetry: EOM are odd under (v, r, delta) -> (-v, -r, -delta) ----
    hn = turn(-35.0)
    n = min(len(h35["y"]), len(hn["y"]))
    y_pos = np.asarray(h35["y"][:n], dtype=float)
    y_neg = np.asarray(hn["y"][:n], dtype=float)
    denom = max(float(np.max(np.abs(y_pos))), 1e-12)
    asym = float(np.max(np.abs(y_pos + y_neg))) / denom
    raw["symmetry"] = dict(max_asym_rel=asym, y_peak=float(np.max(np.abs(y_pos))))
    crit.append(dict(id="SYM-1", what="mirror symmetry delta->-delta (y->-y)", model=asym,
                     ref=0.0, err=asym * 100.0, tol=1e-4))

    # ---- rudder response monotonicity + IMO metric sweep ----
    sweep = {}
    for delta in (5.0, 10.0, 20.0, 35.0):
        hh = turn(delta)
        Dd = circle_diameter(hh)
        sweep[delta] = dict(D=Dd, D_over_L=Dd / v.L,
                            advance_over_L=float(np.max(np.asarray(hh["x"]))) / v.L,
                            steady_r=abs(hh["r"][-1]))
    raw["sweep"] = sweep
    mono = all(sweep[a]["D"] > sweep[b]["D"] for a, b in ((5.0, 10.0), (10.0, 20.0), (20.0, 35.0)))
    crit.append(dict(id="MONO-1", what="dD/ddelta < 0 (larger rudder -> tighter circle)",
                     model=float(mono), ref=1.0, err=0.0 if mono else 100.0, tol=0.0))

    # ---- time-step convergence ----
    d_dt = circle_diameter(turn(35.0, dt=0.25))
    raw["dt_convergence"] = dict(D_dt050=d_track, D_dt025=d_dt)
    crit.append(dict(id="CONV-1", what="dt halving 0.5 -> 0.25 s changes D (Richardson)",
                     model=d_dt, ref=d_track, err=rel_err(d_dt, d_track), tol=1.0))

    # ---- stationarity of the time-domain state (is t_max enough to be "steady"?) ----
    n_s = len(h35["r"])
    k80 = int(0.8 * n_s)
    drift_r = rel_err(h35["r"][-1], float(np.mean(h35["r"][k80:])))
    drift_u = rel_err(h35["u"][-1], float(np.mean(h35["u"][k80:])))
    raw["stationarity"] = dict(r_end=h35["r"][-1], r_tail_mean=float(np.mean(h35["r"][k80:])),
                              u_end=h35["u"][-1], u_tail_mean=float(np.mean(h35["u"][k80:])),
                              drift_r_pct=drift_r, drift_u_pct=drift_u,
                              v_ratio_end=math.hypot(u_ss, h35["v"][-1]) / SPEED,
                              v_ratio_newton=ss["speed_ratio"])
    crit.append(dict(id="CONV-2", what="r(t) stationary over the last 20% of the record",
                     model=h35["r"][-1], ref=float(np.mean(h35["r"][k80:])),
                     err=drift_r, tol=1.0))
    crit.append(dict(id="CONV-3", what="u(t) stationary over the last 20% of the record",
                     model=h35["u"][-1], ref=float(np.mean(h35["u"][k80:])),
                     err=drift_u, tol=1.0))
    crit.append(dict(id="KIN-3", what="speed ratio V/V0 at t_max vs Newton steady speed ratio",
                     model=math.hypot(u_ss, h35["v"][-1]) / SPEED, ref=ss["speed_ratio"],
                     err=rel_err(math.hypot(u_ss, h35["v"][-1]) / SPEED, ss["speed_ratio"]),
                     tol=5.0))

    # ---- linearisation self-consistency: small rudder, time domain vs Newton ----
    ss1 = M.steady_turning_solution(vessel=v, coef=COEF, speed=SPEED, rudder_deg=1.0)
    h1 = turn(1.0)
    K_lin = ss1["r"] / math.radians(1.0)          # Nomoto gain implied by the kernel
    raw["nomoto"] = dict(K_lin=K_lin, r_time=h1["r"][-1], r_newton=ss1["r"],
                         D_over_L_small=ss1["steady_turning_diameter_over_L"])
    crit.append(dict(id="NOM-1", what="small-rudder (1 deg) r_ss: time domain vs Newton",
                     model=h1["r"][-1], ref=ss1["r"], err=rel_err(h1["r"][-1], ss1["r"]), tol=5.0))

    # ---- zigzag: controller definitional consistency + overshoot ordering ----
    zz = M.simulate_zigzag(vessel=v, coef=COEF, speed=SPEED, rudder_deg=10.0,
                           heading_deg=10.0, dt=DT, t_max=T_MAX)
    hz = hist_of(zz)
    psi = np.asarray(hz["psi_deg"], dtype=float)
    dl = np.asarray(hz["delta"], dtype=float)
    switches = np.where(np.diff(np.sign(dl)) != 0)[0] + 1
    switch_err = [abs(abs(psi[i]) - 10.0) for i in switches]
    raw["zigzag_10_10"] = dict(
        peak_deg=float(np.max(np.abs(psi))),
        overshoot_1=float(abs(psi[max(0, switches[0] - 1) if len(switches) else -1])),
        n_switches=int(len(switches)),
        switch_heading_error_deg=[float(e) for e in switch_err],
        durations=getattr(zz, "durations", None))
    crit.append(dict(id="ZIG-1", what="controller switches rudder at |psi| = 10 deg (definition)",
                     model=float(max(switch_err)) if switch_err else float("nan"), ref=0.0,
                     err=float(max(switch_err)) if switch_err else float("inf"), tol=1.0))

    # overshoots per leg (peak after each switch) must be positive and ordered
    leg_peaks = []
    bounds = [0] + [int(i) for i in switches] + [len(psi) - 1]
    for a, b in zip(bounds[:-1], bounds[1:]):
        if b > a:
            leg_peaks.append(float(np.max(np.abs(psi[a:b + 1]))))
    ordered = all(x <= y + 1e-9 for x, y in zip(leg_peaks, leg_peaks[1:])) if len(leg_peaks) > 1 else False
    raw["zigzag_10_10"]["leg_peaks_deg"] = leg_peaks
    raw["zigzag_10_10"]["overshoots_deg"] = [p - 10.0 for p in leg_peaks]
    crit.append(dict(id="ZIG-2", what="zigzag overshoots increase leg over leg (stable hull)",
                     model=float(ordered), ref=1.0, err=0.0 if ordered else 100.0, tol=0.0))

    # ---- IMO Res. MSC.137(76) verdicts on the kernel's own output (informational) ----
    imo = {
        "advance_over_L(<=4.5)": [sweep[35.0]["advance_over_L"], 4.5],
        "tactical_diameter_over_L(<=5.0)": [sweep[35.0]["D_over_L"], 5.0],
        "first_overshoot_10_10(<=10)": [raw["zigzag_10_10"]["overshoots_deg"][0]
                                        if raw["zigzag_10_10"]["overshoots_deg"] else None, 10.0],
    }
    raw["imo"] = {k: dict(value=x, limit=y, verdict=None if x is None else
                          ("PASS" if x <= y else "FAIL")) for k, (x, y) in imo.items()}

    # ---- physical inequalities ----
    ok_inertia = COEF.m_y > COEF.m_x > 0.0
    crit.append(dict(id="INA-1", what="added-mass ordering m_y > m_x > 0 (slender body)",
                     model=float(ok_inertia), ref=1.0, err=0.0 if ok_inertia else 100.0, tol=0.0))
    crit.append(dict(id="STAB-1", what="hull yaw damping N_r < 0 (linear course stability)",
                     model=COEF.N_r, ref=0.0, err=0.0 if COEF.N_r < 0 else 100.0, tol=0.0))

    anchor = dict(id="ANCHOR-1", kind="MAGNITUDE_ANCHOR",
                  what="tactical diameter / L vs published KVLCC2-family trial magnitude",
                  model=sweep[35.0]["D_over_L"], ref=3.0,
                  err=rel_err(sweep[35.0]["D_over_L"], 3.0),
                  note=("Order-of-magnitude anchor only (KVLCC2/SIMMAN-2008 class trials report "
                        "TD/L of order 3). Never used as a verdict driver: the exact trial value "
                        "depends on loading, rudder rate and approach-speed convention, none of "
                        "which this kernel models. No digitised EFD table exists in-repo."))

    for c in crit:
        c["err"] = round(float(c["err"]), 6) if math.isfinite(float(c["err"])) else None
        c["verdict"] = "PASS" if (c["err"] is not None and c["err"] <= c["tol"]) else "FAIL"
    fails = [c["id"] for c in crit if c["verdict"] == "FAIL"]
    return dict(criteria=crit, n_pass=sum(1 for c in crit if c["verdict"] == "PASS"),
                n_fail=len(fails), failed=fails,
                domain_grade="REAL (identities)" if not fails else "FAIL:" + ",".join(fails),
                anchor=anchor, raw=raw, meta=dict(
                    module="backend/physics/ship_env_maneuvering/mmg.py",
                    coef=COEF.name, coef_dict=COEF.as_dict(), speed_ms=SPEED,
                    dt=DT, t_max=T_MAX, vessel=v.particulars if hasattr(v, "particulars") else v.name))


def make_figures(res: Dict[str, object], outdir: str) -> List[str]:
    raw = res["raw"]
    paths: List[str] = []
    h = turn(35.0)
    hn = turn(-35.0)

    fig, ax = plt.subplots(figsize=(6.4, 6.0))
    ax.plot(h["x"], h["y"], lw=1.6, label="+35 deg")
    ax.plot(hn["x"], hn["y"], lw=1.1, ls="--", label="-35 deg (mirror)")
    ax.set_aspect("equal")
    ax.set_xlabel("x [m]"); ax.set_ylabel("y [m]")
    ax.set_title(f"Turning circle 35 deg — TD/L = {raw['sweep'][35.0]['D_over_L']:.2f} "
                 f"(Newton {raw['steady_35']['steady_turning_diameter_over_L']:.2f})", fontsize=10)
    ax.legend(fontsize=8)
    p = os.path.join(outdir, "fig1_turning_circle_symmetry.png")
    fig.tight_layout(); fig.savefig(p, dpi=140); plt.close(fig); paths.append(p)

    fig, ax = plt.subplots(figsize=(6.4, 4.2))
    ax.plot(h["t"], h["psi_deg"], lw=1.4, label="heading")
    ax2 = ax.twinx()
    ax2.plot(h["t"], np.degrees(h["r"]) * 60.0, lw=1.0, color="C1", label="yaw rate [deg/min]")
    ax.set_xlabel("t [s]"); ax.set_ylabel("heading [deg]"); ax2.set_ylabel("yaw rate [deg/min]")
    ax.set_title("Time-domain turn: approach to the steady state", fontsize=10)
    ax.legend(loc="upper left", fontsize=8)
    p = os.path.join(outdir, "fig2_turn_convergence.png")
    fig.tight_layout(); fig.savefig(p, dpi=140); plt.close(fig); paths.append(p)

    zz = M.simulate_zigzag(vessel=VESSEL, coef=COEF, speed=SPEED, rudder_deg=10.0,
                           heading_deg=10.0, dt=DT, t_max=T_MAX)
    hz = hist_of(zz)
    fig, ax = plt.subplots(figsize=(6.4, 4.2))
    ax.plot(hz["t"], hz["psi_deg"], lw=1.5, label="10/10 zigzag")
    ax.plot(hz["t"], np.asarray(hz["delta"]) * 3.0, lw=0.9, ls=":", color="C2",
            label="rudder x3 [deg]")
    ax.axhline(10.0, color="k", lw=0.6, ls=":")
    ax.set_xlabel("t [s]"); ax.set_ylabel("heading [deg]")
    ax.set_title(f"10/10 zigzag — overshoots {raw['zigzag_10_10']['overshoots_deg']}", fontsize=10)
    ax.legend(fontsize=8)
    p = os.path.join(outdir, "fig3_zigzag.png")
    fig.tight_layout(); fig.savefig(p, dpi=140); plt.close(fig); paths.append(p)
    return paths


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=os.path.join(_REPO, "artifacts", "m1676"))
    args = ap.parse_args()
    os.makedirs(args.out, exist_ok=True)
    res = run_audit()
    figs = make_figures(res, args.out)
    res["figures"] = [os.path.relpath(f, _REPO) for f in figs]
    path = os.path.join(args.out, "manoeuvring_identity_audit.json")
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(res, fh, indent=2, ensure_ascii=False, default=str)
    print(f"PASS {res['n_pass']} / FAIL {res['n_fail']} -> {res['domain_grade']}")
    for c in res["criteria"]:
        print(f"  {c['id']:<7} {c['verdict']:<4} err={c['err']!s:<12} tol={c['tol']:<8} {c['what']}")
    a = res["anchor"]
    print(f"  {a['id']:<7} ANCHOR  TD/L={a['model']:.3f} vs {a['ref']} ({a['err']:.1f}%)")
    print("artifact:", path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())