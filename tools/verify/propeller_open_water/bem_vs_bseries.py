"""BEM vs. Wageningen B-series benchmark: absolute-level verification of
``propeller.open_water`` (closes the ``WITHHELD`` in
``docs/verifier/PROPELLER_OPEN_WATER_VERIFICATION.md`` §5.1/§6).

Reference
---------
``tools/verify/propeller_open_water/reference_bseries.py`` — clean-room
transcription of the Oosterveld & van Oossanen (1975) 39-term ``KT`` / 47-term
``KQ`` regression (the industry-standard representation of the B-series
open-water charts; validity box Z=2..7, P/D=0.5..1.4, Ae/A0=0.2..1.05).

Solver under test
-----------------
``backend/services/propeller_open_water.py`` (certified REAL on structural /
analytic criteria, mission 1393) in two configurations:

* ``certified``  — the default symmetric thin-airfoil section;
* ``calibrated`` — ``backend/services/propeller_bseries_calibration.py``
  (camber + cascade lift-slope deficit, 3 parameters fitted on the training
  grid only).

Grids (disjoint, so the hold-out number is honest)
--------------------------------------------------
train     : Z=4,  P/D in {0.8, 1.0, 1.2},  Ae/A0 in {0.40, 0.55, 0.70}
hold-out  : Z in {5, 6}, P/D in {0.9, 1.1}, Ae/A0 in {0.475, 0.625}

Usage
-----
    python3 tools/verify/propeller_open_water/bem_vs_bseries.py          # benchmark only
    python3 tools/verify/propeller_open_water/bem_vs_bseries.py --fit    # re-fit constants

Artifacts (``outputs/propeller_open_water/``):
    bem_vs_bseries.json       every grid point, both configurations
    bem_vs_bseries.csv        flat table (also feeds the report tables)
    calibration_fit.json      fitted constants + objective trace
    fig_kt_kq_eta_vs_J.png    open-water diagram, reference vs both solvers
    fig_error_map.png         KT/KQ relative error vs (P/D, Ae/A0), certified vs calibrated
    fig_zero_thrust_J0.png    J0 / J_opt comparison
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import os
import sys
from typing import Dict, Iterable, List, Tuple

import numpy as np

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from backend.services import propeller_bseries_calibration as cal  # noqa: E402
from backend.services.propeller_open_water import (  # noqa: E402
    PropellerGeometry,
    open_water_point,
    solve_zero_thrust_advance_ratio,
)
from tools.verify.propeller_open_water import reference_bseries as ref  # noqa: E402

OUT_DIR = os.path.join(REPO_ROOT, "outputs", "propeller_open_water")

DIAMETER_M = 5.0
J_POINTS: Tuple[float, ...] = tuple(round(0.1 * k, 6) for k in range(1, 11))

TRAIN_GRID: List[Tuple[int, float, float]] = [
    (4, p_d, ae)
    for p_d in (0.8, 1.0, 1.2)
    for ae in (0.40, 0.55, 0.70)
]
HOLDOUT_GRID: List[Tuple[int, float, float]] = [
    (z, p_d, ae)
    for z in (5, 6)
    for p_d in (0.9, 1.1)
    for ae in (0.475, 0.625)
]

KT_FLOOR = 0.02     # relative-error floor: below this the thrust is ~0 and the
KQ_FLOOR = 0.005    # ratio is meaningless (both references say so explicitly)


# --------------------------------------------------------------------------- #
# evaluation
# --------------------------------------------------------------------------- #
FIT_SECTIONS = 16          # cheaper discretisation, fit only (validated at 40 after)
FIT_J: Tuple[float, ...] = (0.2, 0.4, 0.6, 0.8)


def geometry(z: int, p_d: float, ae: float, sections: int = 40) -> PropellerGeometry:
    return PropellerGeometry(
        diameter_m=DIAMETER_M, pitch_ratio=p_d, blade_area_ratio=ae, blade_count=z,
        hub_ratio=0.18, sections=sections,
    )


def evaluate_point(z: int, p_d: float, ae: float, J: float, mode: str) -> Dict[str, float]:
    g = geometry(z, p_d, ae)
    if mode == "reference":
        c = ref.open_water_coefficients(J, p_d, ae, z)
        return {"KT": c["KT"], "KQ": c["KQ"], "eta0": c["eta_0"]}
    if mode == "certified":
        out = open_water_point(g, J)
    elif mode == "calibrated":
        out = cal.open_water_point_calibrated(g, J)
    else:  # pragma: no cover - guard
        raise ValueError(mode)
    return {"KT": out["KT"], "KQ": out["KQ"], "eta0": out["eta0"]}


def rel(num: float, den: float, floor: float) -> float:
    return (num - den) / (den if abs(den) > floor else math.copysign(floor, den if den else 1.0))


def grid_rows(grid: Iterable[Tuple[int, float, float]], mode: str) -> List[Dict[str, float]]:
    rows = []
    for z, p_d, ae in grid:
        for J in J_POINTS:
            r = evaluate_point(z, p_d, ae, J, "reference")
            m = evaluate_point(z, p_d, ae, J, mode)
            rows.append(
                {
                    "Z": z,
                    "P_D": p_d,
                    "Ae_A0": ae,
                    "J": J,
                    "KT_ref": r["KT"],
                    "KQ_ref": r["KQ"],
                    "eta_ref": r["eta0"],
                    "KT_solver": m["KT"],
                    "KQ_solver": m["KQ"],
                    "eta_solver": m["eta0"],
                    "dKT_pct": 100.0 * rel(m["KT"], r["KT"], KT_FLOOR) if r["KT"] > KT_FLOOR else float("nan"),
                    "dKQ_pct": 100.0 * rel(m["KQ"], r["KQ"], KQ_FLOOR) if r["KT"] > KT_FLOOR else float("nan"),
                    "deta_pct": 100.0 * rel(m["eta0"], r["eta0"], 0.05) if r["KT"] > KT_FLOOR else float("nan"),
                }
            )
    return rows


def endpoint_metrics(z: int, p_d: float, ae: float, mode: str) -> Dict[str, float]:
    """Zero-thrust advance ratio and peak-efficiency point."""
    g = geometry(z, p_d, ae)
    if mode == "reference":
        J0 = ref.zero_thrust_J(p_d, ae, z)
        peak = ref.peak_efficiency(p_d, ae, z)
        return {"J0": J0, "J_opt": peak["J"], "eta_max": peak["eta_0"]}
    if mode == "certified":
        J0 = solve_zero_thrust_advance_ratio(g)
        Js = [0.02 * k for k in range(1, 101)]
        pts = [open_water_point(g, J) for J in Js]
    elif mode == "calibrated":
        J0 = cal.zero_thrust_advance_ratio_calibrated(g)
        Js = [0.02 * k for k in range(1, 101)]
        pts = [cal.open_water_point_calibrated(g, J) for J in Js]
    else:  # pragma: no cover
        raise ValueError(mode)
    best = max(pts, key=lambda p: p["eta0"] if p["KT"] > 0.0 else -1.0)
    pos = [p for p in pts if p["KT"] > 0.0]
    best = max(pos, key=lambda p: p["eta0"])
    return {"J0": J0, "J_opt": best["J"], "eta_max": best["eta0"]}


def summarize(rows: List[Dict[str, float]]) -> Dict[str, float]:
    def stats(key: str) -> Dict[str, float]:
        vals = np.array([abs(r[key]) for r in rows if r[key] == r[key]])  # drop NaN
        return {
            "n": int(vals.size),
            "mean_abs_pct": float(vals.mean()) if vals.size else float("nan"),
            "max_abs_pct": float(vals.max()) if vals.size else float("nan"),
            "within_15pct": float((vals <= 15.0).mean()) if vals.size else float("nan"),
            "within_3pct": float((vals <= 3.0).mean()) if vals.size else float("nan"),
        }

    return {"KT": stats("dKT_pct"), "KQ": stats("dKQ_pct"), "eta0": stats("deta_pct")}


def endpoint_table(grid: Iterable[Tuple[int, float, float]]) -> List[Dict[str, float]]:
    out = []
    for z, p_d, ae in grid:
        r = endpoint_metrics(z, p_d, ae, "reference")
        c = endpoint_metrics(z, p_d, ae, "certified")
        k = endpoint_metrics(z, p_d, ae, "calibrated")
        out.append(
            {
                "Z": z,
                "P_D": p_d,
                "Ae_A0": ae,
                "J0_ref": r["J0"],
                "J0_certified": c["J0"],
                "J0_calibrated": k["J0"],
                "J0_certified_pct": 100.0 * rel(c["J0"], r["J0"], 0.05),
                "J0_calibrated_pct": 100.0 * rel(k["J0"], r["J0"], 0.05),
                "Jopt_ref": r["J_opt"],
                "Jopt_certified": c["J_opt"],
                "Jopt_calibrated": k["J_opt"],
                "eta_max_ref": r["eta_max"],
                "eta_max_certified": c["eta_max"],
                "eta_max_calibrated": k["eta_max"],
                "eta_max_certified_pct": 100.0 * rel(c["eta_max"], r["eta_max"], 0.05),
                "eta_max_calibrated_pct": 100.0 * rel(k["eta_max"], r["eta_max"], 0.05),
            }
        )
    return out


# --------------------------------------------------------------------------- #
# calibration fit (training grid only)
# --------------------------------------------------------------------------- #
def _objective(params: np.ndarray) -> float:
    a0, k_s, ds = (float(p) for p in params)
    if not (0.0 <= a0 <= 0.20 and 0.0 <= k_s <= 6.0 and 0.3 <= ds <= 3.0):
        return 1e6 + 1e6 * (abs(a0) + abs(k_s) + abs(ds))
    total, n = 0.0, 0
    for z, p_d, ae in TRAIN_GRID:
        g = geometry(z, p_d, ae, sections=FIT_SECTIONS)
        sec = cal.calibrated_section(g, zero_lift_rad=a0, k_solidity=k_s, drag_scale=ds)
        for J in FIT_J:
            r = ref.open_water_coefficients(J, p_d, ae, z)
            if r["KT"] <= KT_FLOOR:
                continue
            m = open_water_point(g, J, section=sec)
            total += rel(m["KT"], r["KT"], KT_FLOOR) ** 2
            total += rel(m["KQ"], r["KQ"], KQ_FLOOR) ** 2
            n += 2
    return total / max(n, 1)


def fit_constants() -> Dict[str, object]:
    """Deterministic coarse grid + coordinate refinement on the training grid.

    A simplex search was rejected on purpose: this fit has to be reproducible
    bit-for-bit on any machine (no optimiser version dependence) and one
    objective evaluation costs ~0.8 s, so a structured search is both cheaper
    and auditable.
    """
    a0s = [0.015, 0.0225, 0.030, 0.0375, 0.045]
    kss = [0.4, 0.7, 1.0, 1.3, 1.6]
    dss = [1.0, 1.25, 1.5, 1.75]

    evals: List[Dict[str, object]] = []

    def evaluate(a0, k_s, ds):
        obj = float(_objective(np.array([a0, k_s, ds], dtype=float)))
        evals.append({"params": [float(a0), float(k_s), float(ds)], "objective": obj})
        return obj

    best = None
    for a0 in a0s:
        for k_s in kss:
            for ds in dss:
                obj = evaluate(a0, k_s, ds)
                if best is None or obj < best[0]:
                    best = (obj, a0, k_s, ds)
                print(f"grid a0={a0:.4f} k={k_s:.2f} ds={ds:.2f} -> {obj:.6f}", flush=True)

    _, a0, k_s, ds = best
    for rnd in range(4):
        step = 0.5 ** rnd
        for axis in range(3):
            span = (0.008, 0.25, 0.25)[axis] * step
            for delta in (-span, span, -span / 2.0, span / 2.0):
                trial = [a0, k_s, ds]
                trial[axis] = trial[axis] + delta
                trial[axis] = max(0.0, trial[axis])
                if axis == 2:
                    trial[2] = max(0.3, trial[2])
                obj = evaluate(*trial)
                if obj < best[0]:
                    best = (obj, *trial)
        _, a0, k_s, ds = best
        print(f"round {rnd}: a0={a0:.5f} k_s={k_s:.4f} ds={ds:.4f} obj={best[0]:.6f}", flush=True)

    return {
        "zero_lift_rad": float(a0),
        "k_solidity": float(k_s),
        "drag_scale": float(ds),
        "objective_train": float(best[0]),
        "nfev": len(evals),
        "train_grid": [{"Z": z, "P_D": p, "Ae_A0": a} for z, p, a in TRAIN_GRID],
        "holdout_grid": [{"Z": z, "P_D": p, "Ae_A0": a} for z, p, a in HOLDOUT_GRID],
        "fit_sections": FIT_SECTIONS,
        "fit_J": list(FIT_J),
        "trace": evals[-6:],
        "reference": "Oosterveld & van Oossanen (1975) 39/47-term regression",
    }


# --------------------------------------------------------------------------- #
# figures
# --------------------------------------------------------------------------- #
def figures(rows_by_mode, endpoints, fit_info):
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    os.makedirs(OUT_DIR, exist_ok=True)
    colors = {"reference": "k", "certified": "#d62728", "calibrated": "#1f77b4"}
    labels = {
        "reference": "Wageningen B-series polynomial (ref.)",
        "certified": "BEM, certified section",
        "calibrated": "BEM, calibrated section",
    }

    # (1) open-water diagram: KT, 10KQ, eta0 vs J for a representative geometry
    panels = [(4, 1.0, 0.55), (4, 1.2, 0.70), (5, 0.9, 0.475)]
    fig, axes = plt.subplots(3, 3, figsize=(13.5, 10.5), sharex=True)
    for col, (z, p_d, ae) in enumerate(panels):
        for mode in ("reference", "certified", "calibrated"):
            Js = [0.04 * k for k in range(1, 26)]
            kt, kq, eta = [], [], []
            for J in Js:
                v = evaluate_point(z, p_d, ae, J, mode)
                kt.append(v["KT"]); kq.append(10.0 * v["KQ"]); eta.append(v["eta0"])
            ls = "--" if mode != "reference" else "-"
            axes[0][col].plot(Js, kt, ls, color=colors[mode], label=labels[mode])
            axes[1][col].plot(Js, kq, ls, color=colors[mode])
            axes[2][col].plot(Js, eta, ls, color=colors[mode])
        axes[0][col].set_title(f"Z={z}, P/D={p_d}, Ae/A0={ae}")
        axes[0][col].axhline(0.0, color="0.6", lw=0.6)
        axes[2][col].set_xlabel("J = Va/(nD)")
        axes[2][col].set_ylim(0.0, 0.85)
    axes[0][0].set_ylabel("$K_T$"); axes[1][0].set_ylabel("$10K_Q$"); axes[2][0].set_ylabel(r"$\eta_0$")
    axes[0][0].legend(fontsize=8, loc="upper right")
    fig.suptitle("Open-water diagram: Wageningen B-series reference vs BEM solvers")
    fig.tight_layout()
    fig.savefig(os.path.join(OUT_DIR, "fig_kt_kq_eta_vs_J.png"), dpi=150)
    plt.close(fig)

    # (2) error maps over (P/D, Ae/A0) at fixed Z=4 (certified vs calibrated)
    def err_map(rows, key):
        pd_vals = sorted({r["P_D"] for r in rows})
        ae_vals = sorted({r["Ae_A0"] for r in rows})
        m = np.full((len(ae_vals), len(pd_vals)), np.nan)
        for i, ae in enumerate(ae_vals):
            for j, p_d in enumerate(pd_vals):
                vals = [abs(r[key]) for r in rows if r["P_D"] == p_d and r["Ae_A0"] == ae and r[key] == r[key]]
                if vals:
                    m[i, j] = float(np.mean(vals))
        return m, ae_vals, pd_vals

    fig, axes = plt.subplots(2, 2, figsize=(11.0, 7.6))
    for row_i, key, name in ((0, "dKT_pct", "$K_T$"), (1, "dKQ_pct", "$K_Q$")):
        for col_i, mode in ((0, "certified"), (1, "calibrated")):
            m, ae_vals, pd_vals = err_map(rows_by_mode[mode], key)
            im = axes[row_i][col_i].imshow(m, origin="lower", cmap="viridis", vmin=0, vmax=40,
                                           aspect="auto")
            axes[row_i][col_i].set_xticks(range(len(pd_vals)), [f"{v:g}" for v in pd_vals])
            axes[row_i][col_i].set_yticks(range(len(ae_vals)), [f"{v:g}" for v in ae_vals])
            axes[row_i][col_i].set_xlabel("P/D"); axes[row_i][col_i].set_ylabel("Ae/A0")
            axes[row_i][col_i].set_title(f"{name} mean |rel. err| [%] — {mode}")
            for i in range(len(ae_vals)):
                for j in range(len(pd_vals)):
                    if m[i, j] == m[i, j]:
                        axes[row_i][col_i].text(j, i, f"{m[i, j]:.1f}", ha="center", va="center",
                                                color="w", fontsize=9)
            fig.colorbar(im, ax=axes[row_i][col_i])
    fig.suptitle("Relative error vs B-series reference, Z=4 training grid")
    fig.tight_layout()
    fig.savefig(os.path.join(OUT_DIR, "fig_error_map.png"), dpi=150)
    plt.close(fig)

    # (3) zero-thrust / peak-efficiency advance ratio
    fig, axes = plt.subplots(1, 2, figsize=(11.0, 4.2))
    pds = sorted({e["P_D"] for e in endpoints})
    for z in sorted({e["Z"] for e in endpoints}):
        sub = [e for e in endpoints if e["Z"] == z]
        xs = [e["P_D"] for e in sub]
        axes[0].plot(xs, [e["J0_ref"] for e in sub], "o-", color="k", label=f"ref Z={z}")
        axes[0].plot(xs, [e["J0_certified"] for e in sub], "s--", color="#d62728", label=f"certified Z={z}")
        axes[0].plot(xs, [e["J0_calibrated"] for e in sub], "^-.", color="#1f77b4", label=f"calibrated Z={z}")
        axes[1].plot(xs, [e["eta_max_ref"] for e in sub], "o-", color="k", label=f"ref Z={z}")
        axes[1].plot(xs, [e["eta_max_certified"] for e in sub], "s--", color="#d62728")
        axes[1].plot(xs, [e["eta_max_calibrated"] for e in sub], "^-.", color="#1f77b4")
    axes[0].set_xlabel("P/D"); axes[0].set_ylabel("$J_0$ (zero thrust)")
    axes[1].set_xlabel("P/D"); axes[1].set_ylabel(r"$\eta_{0,max}$")
    axes[0].legend(fontsize=7)
    axes[0].set_title("Zero-thrust advance ratio")
    axes[1].set_title("Peak open-water efficiency")
    fig.tight_layout()
    fig.savefig(os.path.join(OUT_DIR, "fig_zero_thrust_J0.png"), dpi=150)
    plt.close(fig)


# --------------------------------------------------------------------------- #
# main
# --------------------------------------------------------------------------- #
def run(fit: bool = False) -> Dict[str, object]:
    os.makedirs(OUT_DIR, exist_ok=True)
    result: Dict[str, object] = {"calibration": dict(cal.CALIBRATION), "grids": {
        "J": list(J_POINTS),
        "train": [{"Z": z, "P_D": p, "Ae_A0": a} for z, p, a in TRAIN_GRID],
        "holdout": [{"Z": z, "P_D": p, "Ae_A0": a} for z, p, a in HOLDOUT_GRID],
    }}

    if fit:
        fit_info = fit_constants()
        with open(os.path.join(OUT_DIR, "calibration_fit_raw.json"), "w") as fh:
            json.dump(fit_info, fh, indent=2)
        cal.CALIBRATION.update(
            {
                "zero_lift_rad": fit_info["zero_lift_rad"],
                "k_solidity": fit_info["k_solidity"],
                "drag_scale": fit_info["drag_scale"],
            }
        )
        print("fitted:", json.dumps({k: fit_info[k] for k in ("zero_lift_rad", "k_solidity", "drag_scale", "objective_train", "nfev")}, indent=2))
        result["calibration"] = dict(cal.CALIBRATION)
        result["fit"] = {k: fit_info[k] for k in ("zero_lift_rad", "k_solidity", "drag_scale", "objective_train", "nfev", "trace")}

    rows_by_mode = {mode: grid_rows(TRAIN_GRID + HOLDOUT_GRID, mode) for mode in ("certified", "calibrated")}
    train_rows = {mode: grid_rows(TRAIN_GRID, mode) for mode in ("certified", "calibrated")}
    hold_rows = {mode: grid_rows(HOLDOUT_GRID, mode) for mode in ("certified", "calibrated")}

    result["summary"] = {
        "train": {mode: summarize(train_rows[mode]) for mode in train_rows},
        "holdout": {mode: summarize(hold_rows[mode]) for mode in hold_rows},
    }
    result["endpoints"] = endpoint_table(TRAIN_GRID + HOLDOUT_GRID)
    result["solidity"] = {
        f"Z{z}_PD{p}_AE{a}": cal.solidity_07(geometry(z, p, a))
        for z, p, a in TRAIN_GRID + HOLDOUT_GRID
    }

    with open(os.path.join(OUT_DIR, "bem_vs_bseries.json"), "w") as fh:
        json.dump({**result, "points": {m: rows_by_mode[m] for m in rows_by_mode}}, fh, indent=2)

    fieldnames = list(rows_by_mode["certified"][0].keys()) + [
        "cal_KT", "cal_KQ", "cal_eta", "cal_dKT_pct", "cal_dKQ_pct", "cal_deta_pct"]
    with open(os.path.join(OUT_DIR, "bem_vs_bseries.csv"), "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=fieldnames)
        w.writeheader()
        for a, b in zip(rows_by_mode["certified"], rows_by_mode["calibrated"]):
            w.writerow({**a, "cal_KT": b["KT_solver"], "cal_KQ": b["KQ_solver"], "cal_eta": b["eta_solver"],
                        "cal_dKT_pct": b["dKT_pct"], "cal_dKQ_pct": b["dKQ_pct"], "cal_deta_pct": b["deta_pct"]})

    if not fit:
        with open(os.path.join(OUT_DIR, "calibration_fit.json"), "w") as fh:
            json.dump({"calibration": dict(cal.CALIBRATION),
                       "summary": result["summary"]}, fh, indent=2)

    figures(rows_by_mode, result["endpoints"], result.get("fit"))
    return result


def _print_summary(result) -> None:
    for split in ("train", "holdout"):
        for mode in ("certified", "calibrated"):
            s = result["summary"][split][mode]
            line = f"{split:8s} {mode:10s}"
            for key in ("KT", "KQ", "eta0"):
                line += f" | {key}: mean {s[key]['mean_abs_pct']:6.2f}% max {s[key]['max_abs_pct']:6.2f}% <15% {100*s[key]['within_15pct']:5.1f}%"
            print(line)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--fit", action="store_true", help="re-fit the calibration constants")
    args = ap.parse_args()
    res = run(fit=args.fit)
    _print_summary(res)