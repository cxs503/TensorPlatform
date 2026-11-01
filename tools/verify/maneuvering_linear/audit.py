"""Analytic-anchor audit of the manoeuvring chain (mission m1657, models seat).

Read-only with respect to the shipped physics; it only *imports* the 3-DOF MMG core
and the new closed-form linear path, and writes every number programmatically:

    artifacts/m1657/maneuvering_linear/audit.json
    artifacts/m1657/maneuvering_linear/fig1_linear_vs_sim.png
    artifacts/m1657/maneuvering_linear/fig2_steady_gain.png
    artifacts/m1657/maneuvering_linear/fig3_zigzag_overshoot.png

Run:  python3 -m tools.verify.maneuvering_linear.audit
"""

from __future__ import annotations

import json
import math
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
OUT = ROOT / "artifacts/m1657/maneuvering_linear"
sys.path.insert(0, str(ROOT / "backend"))

import matplotlib                                   # noqa: E402
matplotlib.use("Agg")
import matplotlib.pyplot as plt                     # noqa: E402

from physics.ship_env_maneuvering import data as D          # noqa: E402
from physics.ship_env_maneuvering import linear_analysis as LA  # noqa: E402
from physics.ship_env_maneuvering import mmg as M           # noqa: E402

V, COEF, U = D.VESSEL_VLCC, M.KVLCC2_REFERENCE, 7.717


def anchor_derivatives():
    d = LA.linear_derivatives(V, COEF, U)
    table = {"Y_v_nd": COEF.Y_v, "Y_r_nd": COEF.Y_r, "N_v_nd": COEF.N_v,
             "N_r_nd": COEF.N_r}
    rows = [{"name": k, "finite_difference": getattr(d, k),
             "coefficient_table": table[k],
             "rel_diff_pct": abs(getattr(d, k) - table[k]) / abs(table[k]) * 100.0}
            for k in table]
    return {"derivatives_dimensional": d.as_dict(), "table_comparison": rows,
            "max_rel_diff_pct": max(r["rel_diff_pct"] for r in rows)}


def anchor_linear_vs_sim(rudder_deg: float = 0.5, dt: float = 0.25, t_max: float = 400.0):
    """Exact two-mode linear solution vs the nonlinear RK4 solver (small rudder)."""
    sim = M.simulate_turning_circle(V, COEF, U, rudder_deg=rudder_deg,
                                    dt=dt, t_max=t_max)
    t_sim = sim.history["t"]
    r_sim = sim.history["r"]
    lin = LA.linear_response(V, COEF, U, rudder_deg=rudder_deg, times=t_sim)
    r_lin = lin["r"]
    scale = max(abs(x) for x in r_lin)
    errs = [abs(a - b) / scale * 100.0 for a, b in zip(r_lin, r_sim)]
    return {
        "rudder_deg": rudder_deg, "dt": dt, "t_max": t_max,
        "n_samples": len(t_sim),
        "r_linear_end_rad_s": r_lin[-1],
        "r_simulated_end_rad_s": r_sim[-1],
        "max_abs_err_pct_of_linear_scale": max(errs),
        "max_abs_err_pct_after_50s": max(e for t, e in zip(t_sim, errs) if t >= 50.0),
        "series": {"t": t_sim, "r_sim": r_sim, "r_linear": r_lin},
    }


def anchor_steady_gain(rudders=(0.5, 2.0, 5.0, 10.0, 20.0, 35.0)):
    idx = LA.linearised_indices(V, COEF, U)
    rows = []
    for dgr in rudders:
        sim = M.simulate_turning_circle(V, COEF, U, rudder_deg=dgr,
                                        dt=0.5, t_max=1500.0)
        m = sim.metrics
        r_sim = m["steady_yaw_rate_degs"]
        r_lin = math.degrees(abs(idx.K)) * dgr
        rows.append({
            "rudder_deg": dgr,
            "r_steady_sim_degs": r_sim,
            "r_linear_abs_degs": r_lin,
            "deviation_pct": abs(r_sim - r_lin) / r_lin * 100.0,
            "steady_turning_diameter_over_L": m["steady_turning_diameter_over_L"],
            "advance_over_L": m["advance_over_L"],
            "steady_speed_ratio": m["steady_speed_ratio"],
        })
    return {"rows": rows, "linear_K_nd": idx.K_nd}


def anchor_zigzag(rudder_deg: float = 10.0, heading_deg: float = 10.0):
    idx = LA.linearised_indices(V, COEF, U)
    sim = M.simulate_zigzag(V, COEF, U, rudder_deg=rudder_deg,
                            heading_deg=heading_deg, dt=0.25, t_max=1500.0)
    m = sim.metrics
    t1 = m["time_to_first_heading"]
    r_at_rev = next(r for t, r in zip(sim.history["t"], sim.history["r"]) if t >= t1)
    delta = math.radians(rudder_deg)
    # classical Nomoto identification from the execute phase: T from the 63.2 %
    # crossing of the yaw rate, K from the value at the reversal.
    t63 = next(t for t, r in zip(sim.history["t"], sim.history["r"])
               if r >= 0.632 * r_at_rev)
    k_op = r_at_rev / (delta * (1.0 - math.exp(-t1 / t63)))
    analytic = LA.nomoto_zigzag_overshoot(k_op, t63, rudder_deg, heading_deg)
    return {
        "sim_metrics": m,
        "identified_K": k_op, "identified_T_s": t63,
        "identified_K_nd": k_op * V.L / U, "identified_T_nd": t63 * U / V.L,
        "analytic_first_order_overshoot_deg": analytic["first_overshoot_deg"],
        "sim_overshoot_deg": m["first_overshoot_deg"],
        "first_order_underprediction_pct": (
            1.0 - analytic["first_overshoot_deg"] / m["first_overshoot_deg"]) * 100.0,
        "linear_index_K_nd": idx.K_nd,
        "history": {"t": sim.history["t"], "psi": sim.history["psi_deg"],
                    "r": sim.history["r"]},
        "analytic_t_first_reach_s": analytic["t_first_reach_s"],
    }


def anchor_jz_sensitivity():
    rows = []
    for jz in (0.0, 0.1, 0.2, 0.3):
        idx = LA.linearised_indices(V, COEF, U, jz_ratio=jz)
        rows.append({"jz_ratio": jz, "K_nd": idx.K_nd, "T_s": idx.T, "T_nd": idx.T_nd,
                     "lambda_slow": float(getattr(idx.lam1, "real", idx.lam1))})
    k_spread = max(r["K_nd"] for r in rows) - min(r["K_nd"] for r in rows)
    t_spread = (max(r["T_nd"] for r in rows) - min(r["T_nd"] for r in rows))
    t_ref = abs(rows[0]["T_nd"])
    return {"rows": rows, "K_nd_spread": k_spread,
            "T_nd_spread_pct_of_baseline": t_spread / t_ref * 100.0}


def figures(lin, gain, zz):
    OUT.mkdir(parents=True, exist_ok=True)
    fig, ax = plt.subplots(figsize=(7.2, 4.4), dpi=140)
    s = lin["series"]
    ax.plot(s["t"], [x * 1e3 for x in s["r_sim"]], lw=2.0, label="3-DOF RK4 (mmg)")
    ax.plot(s["t"], [x * 1e3 for x in s["r_linear"]], "--", lw=1.6,
            label="exact 2-mode linear solution")
    ax.set_xlabel("t [s]"); ax.set_ylabel("yaw rate r [10$^{-3}$ rad/s]")
    ax.set_title(f"rudder step {lin['rudder_deg']} deg: time-domain vs closed form")
    ax.legend(); ax.grid(alpha=0.3)
    fig.tight_layout(); fig.savefig(OUT / "fig1_linear_vs_sim.png"); plt.close(fig)

    fig, ax = plt.subplots(figsize=(7.2, 4.4), dpi=140)
    d = [r["rudder_deg"] for r in gain["rows"]]
    ax.plot(d, [r["r_steady_sim_degs"] for r in gain["rows"]], "o-", label="simulated steady yaw rate")
    ax.plot(d, [r["r_linear_abs_degs"] for r in gain["rows"]], "s--",
            label="linear theory |K|$\\delta$ (K' = %.2f)" % gain["linear_K_nd"])
    ax.set_xscale("log"); ax.set_yscale("log")
    ax.set_xlabel("rudder angle [deg]"); ax.set_ylabel("$r_{ss}$ [deg/s]")
    ax.set_title("steady gain: nonlinear simulation vs linearised theory")
    ax.legend(); ax.grid(alpha=0.3, which="both")
    fig.tight_layout(); fig.savefig(OUT / "fig2_steady_gain.png"); plt.close(fig)

    fig, ax = plt.subplots(figsize=(7.2, 4.4), dpi=140)
    h = zz["history"]
    ax.plot(h["t"], h["psi"], lw=1.8, label="3-DOF zigzag heading")
    ax.axhline(10.0, color="grey", ls=":", lw=1.0)
    ax.axhline(zz["sim_overshoot_deg"], color="C3", ls="--", lw=1.0,
               label="simulated 1st overshoot %.2f$^\\circ$" % zz["sim_overshoot_deg"])
    ax.axhline(zz["analytic_first_order_overshoot_deg"] + 10.0, color="C2", ls="-.",
               lw=1.2, label="1st-order Nomoto prediction %.2f$^\\circ$"
               % zz["analytic_first_order_overshoot_deg"])
    ax.set_xlabel("t [s]"); ax.set_ylabel("heading $\\psi$ [deg]")
    ax.set_title("10/10 zigzag at $\\delta$=10$^\\circ$: overshoot")
    ax.legend(fontsize=8); ax.grid(alpha=0.3)
    fig.tight_layout(); fig.savefig(OUT / "fig3_zigzag_overshoot.png"); plt.close(fig)


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    idx = LA.linearised_indices(V, COEF, U)
    res = {
        "mission": 1657,
        "vessel": V.particulars(),
        "coefficient_set": COEF.name,
        "speed_ms": U,
        "linear_indices": {k: (float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else v)
                           for k, v in idx.__dict__.items()},
        "anchor_derivatives": anchor_derivatives(),
        "anchor_linear_vs_sim": anchor_linear_vs_sim(),
        "anchor_steady_gain": anchor_steady_gain(),
        "anchor_zigzag": anchor_zigzag(),
        "anchor_jz_sensitivity": anchor_jz_sensitivity(),
    }
    figures(res["anchor_linear_vs_sim"], res["anchor_steady_gain"], res["anchor_zigzag"])
    (OUT / "audit.json").write_text(json.dumps(res, indent=2, default=str))

    print("K'      =", round(idx.K_nd, 3))
    print("T'      =", round(idx.T_nd, 3), " T1'=", round(idx.T1_nd, 4),
          " T2'=", round(idx.T2_nd, 3))
    print("C_nd    =", idx.stability_C_nd, " Routh-Hurwitz stable:", idx.routh_hurwitz_stable)
    print("lin-vs-sim max err [% of linear scale]:",
          round(res["anchor_linear_vs_sim"]["max_abs_err_pct_of_linear_scale"], 3))
    for r in res["anchor_steady_gain"]["rows"]:
        print(f"  delta={r['rudder_deg']:5.1f}  r_sim={r['r_steady_sim_degs']:.4f} "
              f"r_lin={r['r_linear_abs_degs']:.4f} dev={r['deviation_pct']:.1f}%  "
              f"D/L={r['steady_turning_diameter_over_L']:.2f}")
    z = res["anchor_zigzag"]
    print("zigzag 10/10: sim", round(z["sim_overshoot_deg"], 3),
          "analytic 1st-order", round(z["analytic_first_order_overshoot_deg"], 3),
          "underprediction", round(z["first_order_underprediction_pct"], 1), "%")
    print("Jz sensitivity T' spread [%]:",
          round(res["anchor_jz_sensitivity"]["T_nd_spread_pct_of_baseline"], 2))


if __name__ == "__main__":
    main()