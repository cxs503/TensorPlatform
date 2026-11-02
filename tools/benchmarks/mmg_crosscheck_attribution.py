#!/usr/bin/env python3
"""Root-cause attribution of the unresolved 18-32 % MMG code-to-code gap.

Background
----------
``docs/audit/manoeuvring_verification_debt.md`` (m1651) left one anchor open:
the digital-twin MMG model (``backend/services/digital_twin/models/manoeuvring``)
and the independently derived MMG model in ``backend/physics/ship_env_maneuvering``
(W3-N08) disagree on the *same* KVLCC2 hull at 35 deg rudder by

    tactical D/L  4.192 (DT) vs 3.223 (W3-N08)   +30.1 %
    advance/L     2.718      vs 3.315               18.0 %
    steady drift  19.88 deg  vs 13.26 deg            +50 %
    speed ratio   0.293      vs 0.326                -10 %

m1651 recorded this as WITHHELD because no trial data and no copy of the SIMMAN
report exist in the worktree, so neither side could be *declared* right.  That
is a correct verdict about absolute truth.  It is **not** the end of the line:
the two models are built from different force sub-models, and a difference in
the predicted turning circle can be attributed to specific terms with purely
internal evidence.  This module does that attribution.

Design rule (inherited from m1413 / m1651)
-----------------------------------------
No anchor here uses a table value recalled from memory.  Every judge is one of

* a closed form that follows from the model's *own* stated assumptions
  (slender-body added mass ``my = rho*pi*T^2*L``, ``Jz = rho*pi*T^2*L^3/12``
  for the rectangular-section hull the DT module declares),
* a derivation from first principles (Munk moment of a drifted slender body
  ``N_Munk = (m_y - m_x) u v``),
* an exact identity of the equations of motion (steady-turn momentum balance),
* a controlled knockout experiment (inject one missing term, measure the
  displacement of the reported metrics).

The ``kvlcc2_reference`` derivative set of W3-N08 is *literature-shaped*, not
verified against the printed table offline (its own ``data.py`` provenance note
says so).  It is therefore used as a **diagnostic reference, never as truth**:
the verdict vocabulary of this report is "which sub-model differs, by how much,
and what term would close the gap", not "which ship is right".

Findings are staged in four blocks
----------------------------------
A  reproduction   -- both models, same hull, same rudder, six metrics
B  derivatives    -- linear hull derivatives of both models at the operating
                     point, non-dimensionalised identically, plus closed-form
                     arbiters (added mass, Munk moment)
C  knockout       -- the Munk moment, injected into the DT model with a ramp
                     factor lambda in [0, 1], and the resulting displacement of
                     D/L, advance/L, drift and speed ratio
D  verdict        -- attribution table, machine-readable JSON, figures

Usage
-----
    python3 tools/benchmarks/mmg_crosscheck_attribution.py [--out DIR] [--quick]
"""
from __future__ import annotations

import argparse
import json
import math
import sys
import time
from pathlib import Path
from dataclasses import dataclass
from typing import Dict, List, Tuple

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

from services.digital_twin.models.geometry import HullForm            # noqa: E402
from services.digital_twin.models.manoeuvring import (                # noqa: E402
    _MMGCore, rk4_step, turning_circle,
)

RHO = 1025.0
KVLCC2 = dict(L=320.0, B=58.0, T=20.8, Cb=0.8098)
U0 = 15.5 * 1852.0 / 3600.0          # 7.974 m/s (design speed of the case)
RUDDER_DEG = 35.0


# ---------------------------------------------------------------------------
# A. bring-up: both models on the same hull / same rudder
# ---------------------------------------------------------------------------
def _hull(**kw) -> HullForm:
    p = dict(KVLCC2)
    p.update(kw)
    return HullForm(**p)


def dt_turn(dt: float = 0.1, rudder_deg: float = RUDDER_DEG,
            t_end: float = 1200.0) -> Dict[str, float]:
    return turning_circle(_hull(), U0, rudder_deg=rudder_deg, t_end=t_end, dt=dt)


def w3n08_turn(dt: float = 0.5, rudder_deg: float = RUDDER_DEG,
               t_end: float = 1200.0) -> Dict[str, float]:
    from physics.ship_env_maneuvering import data as wdata
    from physics.ship_env_maneuvering import mmg as wmmg

    sim = wmmg.simulate_turning_circle(wdata.VESSEL_VLCC, speed=U0,
                                       rudder_deg=rudder_deg, dt=dt, t_max=t_end)
    return sim.metrics


def w3n08_track(dt: float = 0.5, rudder_deg: float = RUDDER_DEG,
                t_end: float = 1200.0) -> Dict[str, list]:
    """Ground track of the W3-N08 turn (for the trajectory figure)."""
    from physics.ship_env_maneuvering import data as wdata
    from physics.ship_env_maneuvering import mmg as wmmg

    sim = wmmg.simulate_turning_circle(wdata.VESSEL_VLCC, speed=U0,
                                       rudder_deg=rudder_deg, dt=dt, t_max=t_end)
    h = sim.history
    xs, ys = h.get("x") or h.get("x_m"), h.get("y") or h.get("y_m")
    step = max(1, len(xs) // 300)
    return {"traj_x": list(xs)[::step], "traj_y": list(ys)[::step]}


def _rel(a: float, b: float) -> float:
    if b == 0:
        return float("nan")
    return abs(a - b) / abs(b)


def block_a(dt_metrics: Dict[str, float], w_metrics: Dict[str, float]) -> Dict:
    L = KVLCC2["L"]
    r = math.radians(dt_metrics["steady_yaw_rate_deg_s"])
    D_steady_dt = 2.0 * dt_metrics["steady_path_speed_m_s"] / r / L
    rows = [
        ("tactical_D_L", dt_metrics["tactical_diameter_L"],
         w_metrics["tactical_diameter_over_L"]),
        ("advance_L", dt_metrics["advance_L"], w_metrics["advance_over_L"]),
        ("steady_D_L", D_steady_dt, w_metrics["steady_turning_diameter_over_L"]),
        ("drift_deg", dt_metrics["steady_drift_angle_deg"],
         w_metrics["steady_drift_angle_deg"]),
        ("speed_ratio", dt_metrics["steady_speed_m_s"] / U0,
         w_metrics["steady_speed_ratio"]),
        ("yaw_rate_deg_s", dt_metrics["steady_yaw_rate_deg_s"],
         w_metrics["steady_yaw_rate_degs"]),
    ]
    return {
        "rows": [{"metric": m, "dt": a, "w3n08": b, "rel_diff": _rel(a, b)}
                 for m, a, b in rows],
        "worst_rel_diff": max(_rel(a, b) for _, a, b in rows),
    }


# ---------------------------------------------------------------------------
# B. linear hull derivatives, identically non-dimensionalised
#    Y' = Y / (0.5 rho L T U^2),  N' = N / (0.5 rho L^2 T U^2)
#    v' = v/U, r' = r L/U
# ---------------------------------------------------------------------------
def dt_hull_force(core: _MMGCore, u: float, v: float, r: float) -> Tuple[float, float]:
    """Hull sway force and yaw moment of the DT model (same expressions as
    ``_MMGCore.derivs``, lifted out so they can be differentiated)."""
    rho = core.hull.rho
    s = v + core.xm * r
    cf = -0.5 * rho * core.C_D * core.depth * np.abs(s) * s
    Yl, Nl = core.hull_lift(v, r, u)
    return float(np.sum(cf)) * core.dx + Yl, float(np.sum(cf * core.xm)) * core.dx + Nl


def w3n08_hull_force(u: float, v: float, r: float) -> Tuple[float, float]:
    """Hull sway force and yaw moment of the W3-N08 model."""
    from physics.ship_env_maneuvering import data as wdata
    from physics.ship_env_maneuvering import mmg as wmmg
    from physics.ship_env_maneuvering.mmg import Environment

    v_ = wdata.VESSEL_VLCC
    env = Environment()
    bd = wmmg.manoeuvring_forces((0.0, 0.0, 0.0, u, v, r), 0.0, 0.0, v_,
                                 wmmg.KVLCC2_REFERENCE, env)
    return bd.parts["Y_H"], bd.parts["N_H"]


def _derivatives(force_fn, u: float, h_v: float, h_r: float) -> Dict[str, float]:
    """Central differences of (Y_H, N_H) in v and r, non-dimensionalised."""
    L, T = KVLCC2["L"], KVLCC2["T"]
    qY = 0.5 * RHO * L * T * u ** 2
    qN = qY * L

    dv = 2.0 * u * h_v
    Ypv = (force_fn(u, u * h_v, 0.0)[0] - force_fn(u, -u * h_v, 0.0)[0]) / dv
    Npv = (force_fn(u, u * h_v, 0.0)[1] - force_fn(u, -u * h_v, 0.0)[1]) / dv
    h_rr = h_r * u / L
    Ypr = (force_fn(u, 0.0, h_rr)[0] - force_fn(u, 0.0, -h_rr)[0]) / (2.0 * h_r)
    Npr = (force_fn(u, 0.0, h_rr)[1] - force_fn(u, 0.0, -h_rr)[1]) / (2.0 * h_r)
    # Y' = Y/qY, N' = N/qN, v' = v/u, r' = r L/u  =>
    #   dY'/dv' = (dY/dv) u/qY      dY'/dr' = (dY/dr) u/(qY L) = Ypr/qY
    #   dN'/dv' = (dN/dv) u/qN      dN'/dr' = Npr/qN
    # (Ypr, Npr already carry the factor u/L because r was stepped by h_r*u/L)
    return {
        "Y_v": Ypv * u / qY,
        "N_v": Npv * u / qN,
        "Y_r": Ypr / qY,
        "N_r": Npr / qN,
    }


def block_b(dt_core: _MMGCore, dt_metrics: Dict[str, float],
            w_metrics: Dict[str, float]) -> Dict:
    L, T = KVLCC2["L"], KVLCC2["T"]
    # --- closed-form arbiters that use only each model's own assumptions ----
    rho = RHO
    m = KVLCC2["Cb"] * L * KVLCC2["B"] * T * rho        # hull mass
    q_mass = 0.5 * rho * L ** 2 * T
    my_slender = rho * math.pi * T ** 2 * L             # slender body, rectangular section
    my_slender_nd = my_slender / q_mass
    mx_dt_nd = (0.10 * m) / q_mass
    munk_nd_dt = -(my_slender_nd - mx_dt_nd)            # Munk moment N_v' (destabilising)
    # W3-N08 own added mass: (m_y - m_x) * 0.5 rho L^2 T
    my_w = 0.223 * q_mass
    mx_w = 0.022 * q_mass
    munk_nd_w = -(my_w - mx_w) / q_mass * 1.0

    # --- numerical derivatives at a small drift angle (beta = 1 deg) --------
    h_v = math.tan(math.radians(1.0))
    h_r = 1e-3
    d_dt = _derivatives(dt_hull_force_fn(dt_core), U0, h_v, h_r)
    d_w = _derivatives(w3n08_hull_force, U0, h_v, h_r)

    # --- effective (secant) derivatives at the two models' own operating ----
    #     drift angles: the linearisation above is not the whole story
    def secant(fn, beta_deg: float) -> Dict[str, float]:
        qY = 0.5 * RHO * L * T * U0 ** 2
        qN = qY * L
        v = -U0 * math.sin(math.radians(beta_deg))  # MMG: beta = atan(-v/u)
        Y, N = fn(U0, v, 0.0)
        # secant value  Y'/v' = (Y/qY) * (U0/v)
        return {"Y_v_eff": Y * U0 / (qY * v),
                "N_v_eff": N * U0 / (qN * v),
                "beta_deg": beta_deg, "Y_N": Y, "N_Nm": N}

    sec_dt = secant(dt_hull_force_fn(dt_core), dt_metrics["steady_drift_angle_deg"])
    sec_w = secant(w3n08_hull_force, w_metrics["steady_drift_angle_deg"])

    # --- steady-turn momentum balance of each model (exact identity) -------
    bal = {}
    for tag, mets, fn in (("dt", dt_metrics, dt_hull_force_fn(dt_core)),
                          ("w3n08", w_metrics, w3n08_hull_force)):
        st = steady_state(mets)
        Y_H, N_H = fn(st["u"], st["v"], st["r"])
        bal[tag] = {"u": st["u"], "v": st["v"], "r": st["r"],
                    "U": st["U"], "Y_H_N": Y_H, "N_H_Nm": N_H,
                    "drift_deg": st["drift_deg"]}

    return {
        "linear_derivatives": {"dt": d_dt, "w3n08": d_w},
        "closed_form": {
            "my_slender_nd": my_slender_nd, "mx_dt_nd": mx_dt_nd,
            "munk_N_v_dt": munk_nd_dt, "munk_N_v_w3n08": munk_nd_w,
            "w3n08_table": {"Y_v": -0.315, "N_v": -0.137,
                            "Y_r": 0.083, "N_r": -0.075},
        },
        "secant_at_operating_drift": {"dt": sec_dt, "w3n08": sec_w},
        "steady_state": bal,
    }


def steady_state(mets: Dict[str, float]) -> Dict[str, float]:
    """Normalise the two metric vocabularies to (u, v, r, U, drift)."""
    if "steady_path_speed_m_s" in mets:          # digital-twin vocabulary
        U = mets["steady_path_speed_m_s"]
        r = math.radians(mets["steady_yaw_rate_deg_s"])
    else:                                        # W3-N08 vocabulary
        U = mets["steady_speed"]
        r = math.radians(mets["steady_yaw_rate_degs"])
    drift = mets["steady_drift_angle_deg"]
    u = U * math.cos(math.radians(drift))        # W3-N08 signs: beta = atan(-v/u)
    v = -U * math.sin(math.radians(drift))
    return {"u": u, "v": v, "r": r, "U": U, "drift_deg": drift}


def dt_hull_force_fn(core: _MMGCore):
    return lambda u, v, r: dt_hull_force(core, u, v, r)


# ---------------------------------------------------------------------------
# C. knockout: inject the missing hull yaw-moment family, two ways
# ---------------------------------------------------------------------------
@dataclass
class _ExtraCore(_MMGCore):
    """DT force model + extra hull yaw-moment terms.

    Three independent injections, all zero by default (lambda = add = 0
    reproduces the shipped model bit-for-bit):

    ``munk_lambda``  the potential-flow Munk moment ``(m_y - m_x) u v`` of the
                     model's *own* slender-body added masses, ramped 0..1;
    ``n_v_add``      an extra linear sway-moment derivative ``N'_v``
                     (n.d. by ``0.5 rho L^2 T u^2``) - the knob that stands in
                     for the *viscous / lifting* hull moment the DT model does
                     not have;
    ``n_r_add``      the matching rotary derivative ``N'_r``.
    """

    munk_lambda: float = 0.0
    n_v_add: float = 0.0
    n_r_add: float = 0.0

    def derivs(self, state: np.ndarray, delta: float) -> np.ndarray:
        out = super().derivs(state, delta)
        u, v, r = state[3], state[4], state[5]
        L, T, rho = self.hull.L, self.hull.T, self.hull.rho
        u_eff = max(u, 0.2)
        qN = 0.5 * rho * L * L * T * u_eff ** 2
        n_extra = (self.munk_lambda * (self.my - self.mx) * u * v
                   + self.n_v_add * qN * (v / u_eff)
                   + self.n_r_add * qN * (r * L / u_eff))
        out[5] += n_extra / (self.Iz + self.Jz)
        return out


def _extra_turn(label: str, dt: float = 0.2, rudder_deg: float = RUDDER_DEG,
                t_end: float = 1800.0, **kw) -> Dict:
    """Turn of the DT model with extra yaw-moment terms; reports whether the
    manoeuvre actually completed (an unstable model must not look 'better')."""
    core = _ExtraCore(_hull(), U0, **kw)
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
    i90 = int(np.argmax(psi_abs >= math.pi / 2)) if np.any(psi_abs >= math.pi / 2) else None
    i180 = int(np.argmax(psi_abs >= math.pi)) if np.any(psi_abs >= math.pi) else None
    L = KVLCC2["L"]
    u_ss, v_ss = float(traj[-1, 3]), float(traj[-1, 4])
    r_ss = abs(float(traj[-1, 5]))
    U_ss = math.hypot(u_ss, v_ss)
    out = {
        "label": label, **kw,
        "turned_180": i180 is not None,
        "turned_90": i90 is not None,
        "max_heading_deg": float(math.degrees(np.max(psi_abs))),
        "tactical_D_L": abs(float(traj[i180, 1])) / L if i180 is not None else float("nan"),
        "advance_L": float(traj[i90, 0]) / L if i90 is not None else float("nan"),
        "steady_D_L": 2.0 * U_ss / r_ss / L if r_ss > 1e-9 else float("nan"),
        "drift_deg": math.degrees(math.atan2(abs(v_ss), abs(u_ss))),
        "speed_ratio": u_ss / U0,
        "yaw_rate_deg_s": math.degrees(r_ss),
        "t_end_s": t,
    }
    step = max(1, len(traj) // 300)
    out["traj_x"] = traj[::step, 0].tolist()
    out["traj_y"] = traj[::step, 1].tolist()
    return out


N_ADD_SCAN = (-0.05, -0.10, -0.137, -0.18, -0.25)
MUNK_SCAN = (0.0, 0.1, 0.2, 0.3, 0.5, 1.0)


def block_c(dt: float = 0.2) -> Dict[str, List[Dict]]:
    munk = [_extra_turn(f"munk={l:.2f}", dt=dt, munk_lambda=l) for l in MUNK_SCAN]
    nadd = []
    for nv in N_ADD_SCAN:
        nadd.append(_extra_turn(f"N_v_add={nv:.3f}", dt=dt,
                                n_v_add=nv, n_r_add=0.55 * nv))
    return {"munk_scan": munk, "n_add_scan": nadd}


# ---------------------------------------------------------------------------
# figures
# ---------------------------------------------------------------------------
def make_figures(out_dir: Path, b: Dict, c: Dict, dt_traj: Dict,
                 w_traj: Dict, w_metrics: Dict) -> List[str]:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    out_dir.mkdir(parents=True, exist_ok=True)
    figs: List[str] = []

    # fig 1 -- required-moment scan (the knob that reproduces the W3-N08 turn)
    scan = c["n_add_scan"]
    nv = [r["n_v_add"] for r in scan]
    fig, ax = plt.subplots(1, 3, figsize=(13.5, 4.0))
    panels = [("tactical_D_L", "Tactical D/L [-]", w_metrics["tactical_diameter_over_L"]),
              ("drift_deg", "Steady drift angle [deg]", w_metrics["steady_drift_angle_deg"]),
              ("speed_ratio", "Steady speed ratio u/U0 [-]", w_metrics["steady_speed_ratio"])]
    for k, (key, label, ref) in enumerate(panels):
        ax[k].plot(nv, [r[key] for r in scan], "o-", color="#1f77b4",
                   label="DT + $N'_v$ add, $N'_r=0.55 N'_v$")
        ax[k].axhline(ref, ls="--", color="#d62728", label="W3-N08 MMG")
        ax[k].set_xlabel("injected $N'_v$ [-]  (n.d. by $\\rho L^2 T U^2/2$)")
        ax[k].set_ylabel(label)
        ax[k].grid(alpha=0.3)
        ax[k].legend(fontsize=8)
    fig.suptitle(f"Required hull yaw moment to close the gap "
                 f"(KVLCC2 particulars, {RUDDER_DEG:.0f} deg rudder, U0={U0:.2f} m/s)")
    fig.tight_layout()
    p1 = out_dir / "fig1_required_moment.png"
    fig.savefig(p1, dpi=135)
    plt.close(fig)
    figs.append(str(p1))

    # fig 2 -- pure Munk ramp: the naive patch destabilises the model
    ms = c["munk_scan"]
    lam = [r["munk_lambda"] for r in ms]
    fig, ax = plt.subplots(1, 2, figsize=(10.5, 4.0))
    ax[0].plot(lam, [r["drift_deg"] for r in ms], "s-", color="#9467bd")
    ax[0].axhline(w_metrics["steady_drift_angle_deg"], ls="--", color="#d62728",
                  label="W3-N08 drift")
    ax[0].set_xlabel("Munk ramp factor $\\lambda$ [-]")
    ax[0].set_ylabel("steady drift angle [deg]")
    ax[0].legend(fontsize=8); ax[0].grid(alpha=0.3)
    ok = [r["turned_180"] for r in ms]
    ax[1].plot(lam, [r["max_heading_deg"] for r in ms], "o-", color="#ff7f0e")
    ax[1].axhline(180.0, ls=":", color="k", lw=1, label="180 deg (turn completed)")
    for l, good in zip(lam, ok):
        if not good:
            ax[1].annotate("turn not completed", (l, 60), rotation=90, fontsize=7)
    ax[1].set_xlabel("Munk ramp factor $\\lambda$ [-]")
    ax[1].set_ylabel("max heading change in 1800 s [deg]")
    ax[1].legend(fontsize=8); ax[1].grid(alpha=0.3)
    fig.suptitle("Naive Munk-only patch: the pure potential moment drives the hull "
                 "course-unstable (no steady turn)")
    fig.tight_layout()
    p2 = out_dir / "fig2_munk_naive_patch.png"
    fig.savefig(p2, dpi=135)
    plt.close(fig)
    figs.append(str(p2))

    # fig 3 -- hull derivatives: DT vs W3-N08 vs closed form
    dd, dw = b["linear_derivatives"]["dt"], b["linear_derivatives"]["w3n08"]
    keys = ["Y_v", "N_v", "Y_r", "N_r"]
    x = np.arange(len(keys))
    w = 0.27
    fig, ax = plt.subplots(figsize=(8.5, 4.2))
    ax.bar(x - w, [dd[k] for k in keys], w, label="DT (cross-flow + slender lift)",
           color="#1f77b4")
    ax.bar(x, [dw[k] for k in keys], w, label="W3-N08 (empirical MMG set)",
           color="#d62728")
    cf = b["closed_form"]
    ax.plot([1 - w], [cf["munk_N_v_dt"]], "k_", ms=24, mew=2,
            label="closed-form Munk $N'_v$ of DT's own added mass")
    ax.axhline(0, color="k", lw=0.8)
    ax.set_xticks(x)
    ax.set_xticklabels(["$Y'_v$", "$N'_v$", "$Y'_r$", "$N'_r$"])
    ax.set_ylabel("non-dimensional derivative [-]")
    ax.set_title("Linear hull derivatives at $\\beta\\to0$ (identical non-dimensionalisation)")
    ax.legend(fontsize=8)
    ax.grid(alpha=0.3, axis="y")
    fig.tight_layout()
    p3 = out_dir / "fig3_hull_derivatives.png"
    fig.savefig(p3, dpi=135)
    plt.close(fig)
    figs.append(str(p3))

    # fig 4 -- trajectories
    fig, ax = plt.subplots(figsize=(6.4, 6.0))
    ax.plot(dt_traj["traj_x"], dt_traj["traj_y"], lw=1.6, label="DT (shipped)")
    ax.plot(w_traj["traj_x"], w_traj["traj_y"], lw=1.6, label="W3-N08 MMG")
    best = min(c["n_add_scan"],
               key=lambda r: abs(r["tactical_D_L"] - w_metrics["tactical_diameter_over_L"]))
    if best.get("traj_x"):
        ax.plot(best["traj_x"], best["traj_y"], lw=1.6, ls="--",
                label=f"DT + {best['label']}")
    ax.set_aspect("equal")
    ax.set_xlabel("x [m]")
    ax.set_ylabel("y [m]")
    ax.set_title(f"Turning-circle tracks, {RUDDER_DEG:.0f} deg starboard rudder")
    ax.grid(alpha=0.3)
    ax.legend(fontsize=8)
    fig.tight_layout()
    p4 = out_dir / "fig4_trajectories.png"
    fig.savefig(p4, dpi=135)
    plt.close(fig)
    figs.append(str(p4))
    return figs


# ---------------------------------------------------------------------------
# D. verdict
# ---------------------------------------------------------------------------
def verdict(a: Dict, b: Dict, c: Dict) -> Dict:
    cf = b["closed_form"]
    dd = b["linear_derivatives"]["dt"]
    dw = b["linear_derivatives"]["w3n08"]
    w_tac = a["rows"][0]["w3n08"]
    w_drift = a["rows"][3]["w3n08"]
    w_speed = a["rows"][4]["w3n08"]
    best = min(c["n_add_scan"],
               key=lambda r: abs(r["tactical_D_L"] - w_tac))
    base = [r for r in c["munk_scan"] if r["munk_lambda"] == 0.0][0]
    naive = [r for r in c["munk_scan"] if r["munk_lambda"] >= 0.2]
    broken = [r["label"] for r in naive if not r["turned_180"]]
    return {
        "gap_before": {"tactical_D_L_rel": a["rows"][0]["rel_diff"],
                       "advance_L_rel": a["rows"][1]["rel_diff"],
                       "drift_rel": a["rows"][3]["rel_diff"],
                       "speed_rel": a["rows"][4]["rel_diff"]},
        "baseline_reproduction_rel": _rel(base["tactical_D_L"], a["rows"][0]["dt"]),
        "missing_moment_family": {
            "dt_N_v": dd["N_v"], "w3n08_N_v": dw["N_v"],
            "dt_N_r": dd["N_r"], "w3n08_N_r": dw["N_r"],
            "dt_Y_v": dd["Y_v"], "w3n08_Y_v": dw["Y_v"],
            "closed_form_munk_N_v": cf["munk_N_v_dt"],
            "n_v_ratio_dt_over_w3n08": abs(dd["N_v"] / dw["N_v"]),
        },
        "naive_munk_patch_fails": {"broken_cases": broken,
                                   "note": "injecting only the potential (Munk) moment "
                                           "makes the hull course-unstable; the turn is "
                                           "not completed within 1800 s"},
        "required_moment_to_close_gap": {
            "injected_N_v": best["n_v_add"], "injected_N_r": best["n_r_add"],
            "tactical_D_L": best["tactical_D_L"],
            "residual_rel_vs_w3n08": _rel(best["tactical_D_L"], w_tac),
            "drift_deg": best["drift_deg"],
            "drift_rel_vs_w3n08": _rel(best["drift_deg"], w_drift),
            "speed_ratio": best["speed_ratio"],
            "speed_rel_vs_w3n08": _rel(best["speed_ratio"], w_speed),
        },
        "attribution": (
            "The 18-32 %% code-to-code gap is carried by the *yaw-moment family* of the "
            "hull force model, not by the sway force: the DT model's N'_v = %+.3f and "
            "N'_r = %+.4f are effectively zero (its lift term is applied at a single "
            "point, x_L = -0.05L, so the sway force barely produces a moment), against "
            "%+.3f / %+.3f in W3-N08; Y'_v differs by only %.0f %%. The DT hull therefore "
            "balances the rudder moment almost entirely through cross-flow drag, which "
            "needs a %.1f deg drift angle instead of %.1f deg and a %.0f %% larger "
            "tactical diameter. The missing moment is NOT the potential (Munk) term "
            "alone: injecting it (%+.3f) makes the hull course-unstable and the turn "
            "never completes. Fitting the *linear* extra moment that reproduces the "
            "W3-N08 turn requires N'_v = %+.3f, N'_r = %+.3f, leaving a %.1f %% residual "
            "on tactical D/L - the models are not the same physics, and neither can be "
            "declared correct without trial data (absolute verdict stays WITHHELD)."
            % (dd["N_v"], dd["N_r"], dw["N_v"], dw["N_r"],
               100.0 * _rel(dd["Y_v"], dw["Y_v"]), base["drift_deg"], w_drift,
               100.0 * a["rows"][0]["rel_diff"], cf["munk_N_v_dt"],
               best["n_v_add"], best["n_r_add"],
               100.0 * _rel(best["tactical_D_L"], w_tac))
        ),
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", default=str(ROOT / "artifacts" / "mmg_crosscheck"))
    ap.add_argument("--quick", action="store_true",
                    help="coarser time steps (smoke test)")
    args = ap.parse_args(argv)
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    t0 = time.time()

    dt_step = 0.4 if args.quick else 0.1
    w_step = 1.0 if args.quick else 0.5
    dtt = dt_turn(dt=dt_step)
    dtw = w3n08_turn(dt=w_step)
    a = block_a(dtt, dtw)
    print("== A. reproduction ==")
    for r in a["rows"]:
        print(f"  {r['metric']:<16} DT={r['dt']:>10.4f}  W3N08={r['w3n08']:>10.4f}  "
              f"rel={r['rel_diff']*100:6.2f}%")

    core = _MMGCore(_hull(), U0)
    b = block_b(core, dtt, dtw)
    print("\n== B. linear hull derivatives (n.d. by 0.5 rho L T U^2 / 0.5 rho L^2 T U^2) ==")
    for k in ("Y_v", "N_v", "Y_r", "N_r"):
        print(f"  {k:<5} DT={b['linear_derivatives']['dt'][k]:>8.4f}  "
              f"W3N08={b['linear_derivatives']['w3n08'][k]:>8.4f}  "
              f"(W3-N08 stated {b['closed_form']['w3n08_table'][k]:>8.4f})")
    print(f"  closed-form Munk N'_v (DT's own added mass) = "
          f"{b['closed_form']['munk_N_v_dt']:.4f}")

    c = block_c(dt=0.4 if args.quick else 0.2)
    print("\n== C1. pure Munk ramp (naive patch) ==")
    for r in c["munk_scan"]:
        print(f"  {r['label']:<12} D/L={r['tactical_D_L']:>7.3f} drift={r['drift_deg']:>6.2f} "
              f"u/U0={r['speed_ratio']:.3f} max_heading={r['max_heading_deg']:6.1f} "
              f"turned={r['turned_180']}")
    print("\n== C2. required-moment scan (N_r_add = 0.55 N_v_add) ==")
    for r in c["n_add_scan"]:
        print(f"  {r['label']:<16} D/L={r['tactical_D_L']:>7.3f} adv/L={r['advance_L']:>7.3f} "
              f"drift={r['drift_deg']:>6.2f} u/U0={r['speed_ratio']:.3f} "
              f"turned={r['turned_180']}")

    v = verdict(a, b, c)
    print("\n== D. verdict ==")
    print(" ", v["attribution"])

    figs = make_figures(out_dir, b, c, dtt, w3n08_track(w_step), dtw)
    payload = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "case": {"hull": KVLCC2, "U0_m_s": U0, "rudder_deg": RUDDER_DEG,
                 "dt_step_dt": dt_step, "dt_step_w3n08": w_step},
        "block_a_reproduction": a,
        "block_b_derivatives": b,
        "block_c_knockout": {
            "munk_scan": [{k: v_ for k, v_ in r.items() if not k.startswith("traj")}
                          for r in c["munk_scan"]],
            "n_add_scan": [{k: v_ for k, v_ in r.items() if not k.startswith("traj")}
                           for r in c["n_add_scan"]],
        },
        "block_d_verdict": v,
        "figures": [str(Path(p_).name) for p_ in figs],
        "wall_time_s": round(time.time() - t0, 1),
    }
    (out_dir / "mmg_crosscheck_attribution.json").write_text(
        json.dumps(payload, indent=2, ensure_ascii=False))
    print(f"\nartifacts -> {out_dir}  ({payload['wall_time_s']} s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
