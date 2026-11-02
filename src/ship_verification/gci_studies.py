"""Applied grid-convergence studies for the TensorPlatform verifier references.

Two references quoted by the ship-performance verification suite integrate a
continuous model with a discretisation parameter and, until now, published a
single number with no discretisation-error budget:

=================================  ==========================================
reference                          discretisation
=================================  ==========================================
``tools/verifier/mmg_reference.py``  classical RK4, fixed ``dtau``
                                     (docstring *claims* "time-step
                                     convergence ... A-class" -> never shown)
``docs/verification_debt/wigley_michell/michell_wigley.py``
                                     Gauss-Legendre in ``theta``,
                                     ``michell_rw_converged`` applies a
                                     Richardson factor with an *assumed*
                                     ``p = 4``
=================================  ==========================================

This module closes both debts:

* ``mmg_timestep_study``  - refinement sequence ``dtau`` = 0.16 ... 0.01 for a
  35 deg KVLCC2 turning circle; observed order of accuracy on the position at a
  fixed dimensionless time ``tau = 40`` plus the tactical diameter, with GCI.
* ``michell_quadrature_study`` - refinement of the theta quadrature; measures the
  empirical error decay and checks whether the assumed ``p = 4`` of
  ``michell_rw_converged`` is conservative.

Run as a script::

    python3 -m src.ship_verification.gci_studies [--outdir DIR]

It writes ``gci_study.json`` (all numbers, machine generated) and
``gci_study.png`` (diagnostic figure) and prints a summary table.  No number in
the companion report is typed by hand: the report generator reads the JSON.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import sys
from typing import Dict, List

import numpy as np

from .gci import gci_study, observed_order

# ---------------------------------------------------------------------------
# repository layout
# ---------------------------------------------------------------------------
_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO = os.path.dirname(os.path.dirname(_HERE))
_MICHELL_DIR = os.path.join(_REPO, "docs", "verification_debt", "wigley_michell")

#: dimensionless time at which the RK4 trajectory is sampled (reached before the
#: 540 deg break criterion for every step used here, so the sample is comparable)
TAU_SAMPLE = 40.0

#: 35 deg rudder, self-propulsion point - the SIMMAN/ITTC turning-circle standard
RUDDER_DEG = 35.0


def _import_mmg():
    if _REPO not in sys.path:
        sys.path.insert(0, _REPO)
    from tools.verifier.mmg_reference import KVLCC2, MMGParams, manoeuvre, self_propulsion_n

    return KVLCC2, MMGParams, manoeuvre, self_propulsion_n


def _import_michell():
    if _MICHELL_DIR not in sys.path:
        sys.path.insert(0, _MICHELL_DIR)
    import importlib

    mod = importlib.import_module("michell_wigley")
    return mod


# ---------------------------------------------------------------------------
# study 1: RK4 time-step convergence of the MMG reference
# ---------------------------------------------------------------------------
def mmg_timestep_study(steps=(0.16, 0.08, 0.04, 0.02, 0.01), tol_pct: float = 5.0) -> Dict:
    KVLCC2, MMGParams, manoeuvre, self_propulsion_n = _import_mmg()
    prm = MMGParams(KVLCC2)
    n_p = self_propulsion_n(prm)
    L = prm.L

    rows: List[Dict] = []
    for st in steps:
        run = manoeuvre(prm, n_p, RUDDER_DEG, step=st)
        hist = run["hist"]
        i = int(round(TAU_SAMPLE / st))
        if i >= len(hist):
            raise RuntimeError(
                "step %.4f stops at tau = %.3f before the %.1f sampling time"
                % (st, len(hist) * st, TAU_SAMPLE)
            )
        s = hist[i]
        psi_deg = np.degrees(hist[:, 5])
        y = hist[:, 4]
        i180 = int(np.argmax(psi_deg >= 180.0))
        if i180 > 0:
            p0, p1 = psi_deg[i180 - 1], psi_deg[i180]
            w = (180.0 - p0) / (p1 - p0)
            y_180 = y[i180 - 1] + w * (y[i180] - y[i180 - 1])
            td_m = 2.0 * abs(float(y_180)) * L
        else:
            td_m = float("nan")
        rows.append(
            {
                "dtau": float(st),
                "n_steps": int(len(hist)),
                "x_at_tau40_m": float(s[3] * L),
                "y_at_tau40_m": float(s[4] * L),
                "psi_at_tau40_deg": float(psi_deg[i]),
                "tactical_diameter_m": td_m,
            }
        )

    observables = {
        "x_at_tau40_m": [r["x_at_tau40_m"] for r in rows],
        "y_at_tau40_m": [r["y_at_tau40_m"] for r in rows],
        "psi_at_tau40_deg": [r["psi_at_tau40_deg"] for r in rows],
        "tactical_diameter_m": [r["tactical_diameter_m"] for r in rows],
    }
    hs = [r["dtau"] for r in rows]

    studies: Dict[str, Dict] = {}
    for name, vals in observables.items():
        if any(not np.isfinite(v) for v in vals):
            continue
        # GCI at the finest triplet; plus the order from every consecutive triplet
        fine = gci_study(hs[:3], vals[:3], tolerance_pct=tol_pct).to_dict()
        triplets = []
        for i in range(len(hs) - 2):
            p, regime, msgs = observed_order(
                vals[i], vals[i + 1], vals[i + 2], hs[i + 1] / hs[i], hs[i + 2] / hs[i + 1]
            )
            triplets.append(
                {
                    "dtau_triplet": [hs[i], hs[i + 1], hs[i + 2]],
                    "observed_order": p,
                    "regime": regime,
                    "messages": msgs,
                }
            )
        # production-level check: the shipped step vs the next finer one
        prod = gci_study([hs[-2], hs[-1]], [vals[-2], vals[-1]], assumed_order=4.0,
                         tolerance_pct=tol_pct).to_dict()
        studies[name] = {"finest_triplet": fine, "per_triplet": triplets,
                         "production_step": prod}

    return {
        "case": "KVLCC2 35 deg turning circle at self-propulsion, RK4 fixed dtau",
        "L_m": L, "rudder_deg": RUDDER_DEG, "self_propulsion_n": n_p,
        "tau_sample": TAU_SAMPLE,
        "production_step": 0.01,
        "levels": rows,
        "studies": studies,
    }


# ---------------------------------------------------------------------------
# study 2: theta-quadrature refinement of the Michell reference solution
# ---------------------------------------------------------------------------
def michell_quadrature_study(fn: float = 0.30, n_sweep=(4, 8, 16, 32, 64, 100, 200, 400),
                             n_reference: int = 800, rho_note: str = "") -> Dict:
    mod = _import_michell()
    hull = mod.Wigley()
    U = fn * math.sqrt(hull.g * hull.L)
    ref = mod.rw_theta_integral(hull, U, n_theta=n_reference)
    rows = []
    for n in n_sweep:
        v = mod.rw_theta_integral(hull, U, n_theta=n)
        rows.append({"n_theta": int(n), "Rw_N": float(v),
                     "rel_err_vs_dense": float(abs(v - ref) / ref)})
    decay = []
    for a, b in zip(rows, rows[1:]):
        if b["rel_err_vs_dense"] > 0:
            decay.append(
                {
                    "from_n": a["n_theta"], "to_n": b["n_theta"],
                    "error_ratio": a["rel_err_vs_dense"] / b["rel_err_vs_dense"],
                }
            )
    # the shipped Richardson helper assumes p = 4 -- quantify how conservative
    # that is against the measured increments at production resolution
    rw_a = mod.rw_theta_integral(hull, U, n_theta=400)
    rw_b = mod.rw_theta_integral(hull, U, n_theta=800)
    rw_converged, increment = mod.michell_rw_converged(hull, U, n_theta=400)
    # round-off guard on the production triplet
    ro = gci_study([1 / 100.0, 1 / 200.0, 1 / 400.0], [rw_a, rw_b,
                    mod.rw_theta_integral(hull, U, n_theta=100)]).to_dict()
    # effective order implied by the two finest levels below the round-off floor
    useful = [r for r in rows if r["rel_err_vs_dense"] > 1e-12]
    a, b = useful[-2], useful[-1]
    p_eff = (math.log(a["rel_err_vs_dense"] / b["rel_err_vs_dense"])
             / math.log(b["n_theta"] / a["n_theta"]))
    p_eff_pair = [a["n_theta"], b["n_theta"]]
    return {
        "case": "Michell thin-ship Rw for the Wigley hull, theta Gauss-Legendre refinement",
        "Fn": fn, "U_ms": U, "reference_n_theta": n_reference, "Rw_reference_N": float(ref),
        "rows": rows, "decay_factors": decay,
        "effective_order_pair": p_eff_pair,
        "effective_order": float(p_eff),
        "assumed_order_in_repo": 4.0,
        "assumed_p_is_conservative": bool(p_eff > 4.0),
        "richardson_check": {
            "Rw_n400_N": float(rw_a), "Rw_n800_N": float(rw_b),
            "increment_rel": float(increment),
            "Rw_richardson_p4_N": float(rw_converged),
            "correction_rel": float(abs(rw_converged - rw_b) / rw_b),
        },
        "production_triplet_gci": ro,
        "note": rho_note,
    }


# ---------------------------------------------------------------------------
# figure + CLI
# ---------------------------------------------------------------------------
def make_figure(mmg: Dict, michell: Dict, path: str) -> None:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fig, axes = plt.subplots(1, 3, figsize=(15.0, 4.4))

    # (a) MMG: error vs dtau, log-log, reference slopes p = 1 and p = 4
    ax = axes[0]
    hs = [r["dtau"] for r in mmg["levels"]]
    ref_x = mmg["levels"][-1]["x_at_tau40_m"]
    errs = [abs(r["x_at_tau40_m"] - ref_x) for r in mmg["levels"]]
    ax.loglog(hs, errs, "o-", label=r"$|x_{h}-x_{h=0.01}|$ [m]")
    hh = np.array(hs)
    e0 = errs[0]
    for p, ls in ((1, ":"), (4, "--")):
        ax.loglog(hh, e0 * (hh / hh[0]) ** p, ls, color="grey",
                  label="slope $h^{%d}$" % p)
    ax.set_xlabel(r"RK4 step $\Delta\tau$ [-]")
    ax.set_ylabel("discretisation error in x(tau=40) [m]")
    ax.set_title("(a) MMG RK4 time step: order recovery")
    ax.grid(True, which="both", alpha=0.3)
    ax.legend(fontsize=8)

    # (b) tactical diameter vs dtau with GCI band
    ax = axes[1]
    td = [r["tactical_diameter_m"] for r in mmg["levels"]]
    ax.semilogx(hs, td, "s-", color="tab:blue")
    fine = mmg["studies"]["tactical_diameter_m"]["production_step"]
    if fine.get("extrapolated") and fine.get("gci_fine_pct") is not None:
        fe = fine["extrapolated"]
        g = fine["gci_fine_pct"] / 100.0 * abs(fe)
        ax.axhline(fe, color="tab:red", lw=1, label="Richardson extrapolated")
        ax.fill_between([min(hs), max(hs)], fe - g, fe + g, color="tab:red", alpha=0.15,
                        label="production GCI = %.3g %%" % fine["gci_fine_pct"])
    ax.set_xlabel(r"RK4 step $\Delta\tau$ [-]")
    ax.set_ylabel("tactical diameter [m]")
    ax.set_title("(b) tactical diameter convergence + GCI")
    ax.grid(True, which="both", alpha=0.3)
    ax.legend(fontsize=8)

    # (c) Michell theta quadrature: spectral decay + round-off floor
    ax = axes[2]
    ns = [r["n_theta"] for r in michell["rows"]]
    ee = [max(r["rel_err_vs_dense"], 1e-17) for r in michell["rows"]]
    ax.loglog(ns, ee, "o-")
    ax.axhline(2.2e-16, color="grey", ls=":", label="double-precision floor")
    ax.set_xlabel(r"$n_\theta$ (Gauss-Legendre nodes)")
    ax.set_ylabel(r"$|R_w(n)-R_w(1600)|/R_w(1600)$")
    ax.set_title("(c) Michell reference: spectral convergence")
    ax.grid(True, which="both", alpha=0.3)
    ax.legend(fontsize=8)

    fig.tight_layout()
    fig.savefig(path, dpi=150)
    plt.close(fig)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--outdir", default=os.path.join(_REPO, "docs", "verification_debt", "gci",
                                                     "artifacts"))
    args = ap.parse_args(argv)
    os.makedirs(args.outdir, exist_ok=True)

    mmg = mmg_timestep_study()
    michell = michell_quadrature_study()
    payload = {"mmg_timestep": mmg, "michell_quadrature": michell}
    json_path = os.path.join(args.outdir, "gci_study.json")
    with open(json_path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=1)
    png_path = os.path.join(args.outdir, "gci_study.png")
    make_figure(mmg, michell, png_path)

    print("== MMG RK4 time-step convergence (KVLCC2, 35 deg) ==")
    for name, s in mmg["studies"].items():
        ft = s["finest_triplet"]
        trips = ", ".join(
            "%.2f" % t["observed_order"] if t["observed_order"] else "n/a"
            for t in s["per_triplet"]
        )
        print("  %-22s p(0.16/0.08/0.04)=%s  GCI_fine=%.3g%%  asymptotic=%.3f  converged=%s"
              % (name, ("%.3f" % ft["observed_order"]) if ft["observed_order"] else "n/a",
                 ft["gci_fine_pct"] if ft["gci_fine_pct"] is not None else float("nan"),
                 ft["asymptotic_ratio"] if ft["asymptotic_ratio"] is not None else float("nan"),
                 ft["converged"]))
        print("      per-triplet orders:", trips)
    print("== Michell theta quadrature (Fn=%.2f) ==" % michell["Fn"])
    print("  Rw(1600) = %.10e N" % michell["Rw_reference_N"])
    print("  effective order 32->64: %.2f (repo assumes 4)" % michell["effective_order_32_64"])
    print("  production triplet GCI: %s" % michell["production_triplet_gci"]["regime"])
    print("wrote", json_path)
    print("wrote", png_path)
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())