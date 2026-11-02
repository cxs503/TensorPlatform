#!/usr/bin/env python3
"""Independent re-check of TensorLBM benchmark cases (cross-family verification).

Why this tool exists
--------------------
The platform's most recurrent verification debt is *self-judging*: a benchmark case
prints its own PASS/FAIL using the same script that produced the number.  Reviewers
who read a "verified" badge are therefore trusting one code path twice.  This tool is a
second, different code path: it re-reads the raw artifacts (velocity fields, front-
position histories, result rows) that a case left on disk, re-derives the measured
quantity with its own post-processing, and re-judges it against reference values pinned
in ``tools/benchmarks/lbm_reference_registry.py``.

Read-only by construction: nothing under the LBM tree is written; all output goes to
``artifacts/independent_recheck/``.

Usage
-----
    python3 tools/benchmarks/independent_recheck.py \
        --lbm-root /root/tensor-ecosystem/TensorLBM \
        --out-artifacts artifacts/independent_recheck

Checks (each declares its independence tier)
--------------------------------------------
1. ``cavity_3d_full``  field-recompute : u_min on the vertical centreline from the saved
   3D velocity field, vs Ku/Hirsh/Taylor 1987 (3D Re=1000, u_min=-0.2751) and the 2D
   Ghia (1982) table for the 3D-correction sanity check.
2. ``dam_break_sc``    field-recompute : front position X(T) from the history CSV vs the
   Martin & Moyce 1952 digitisation; additionally quantifies the sqrt(2) time-convention
   error by re-plotting the same history on the wrong axis.
3. ``forchheimer``     error-recompute + baseline-audit : recomputes the drag errors of
   every reported row and re-checks the Stokes intercept against Sangani & Acrivos
   f(0.40)=217.89.
4. ``poiseuille_2d``   baseline-audit : re-derives u_max/u_mean from (dp, H_eff, nu) and
   checks the case's analytic baseline plus the half-way bounce-back H_eff bookkeeping.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import os
import sys
from typing import Any

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from tools.benchmarks import lbm_reference_registry as reg  # noqa: E402


# --------------------------------------------------------------------------------------
# 1. 3D lid-driven cavity — recompute u_min from the raw field
# --------------------------------------------------------------------------------------
def check_cavity_3d(npz_path: str, result_json: str | None = None) -> dict[str, Any]:
    """Re-extract the centreline velocity minimum from a stored 3D cavity field."""
    import numpy as np

    z = np.load(npz_path)
    ux = np.asarray(z["ux"], dtype=float)
    if ux.ndim != 3:
        raise ValueError(f"expected a 3D velocity field, got shape {ux.shape}")
    u_lid = float(z["u_lid"]) if "u_lid" in z else 1.0
    nx, ny, nz = ux.shape
    # Stored fields come in two flavours: dimensional (u_lid in lattice units, e.g. 0.06)
    # and already normalised (max|u| ~ 1).  Detect instead of assuming.
    normalised_field = float(np.max(np.abs(ux))) > 5.0 * abs(u_lid)
    scale = 1.0 if normalised_field else u_lid

    # Centreline candidates: hold two indices at the mid-plane, sweep the third.
    lines = {}
    for axis in range(3):
        sl = [slice(None)] * 3
        for other in range(3):
            if other != axis:
                sl[other] = ux.shape[other] // 2
        line = ux[tuple(sl)]
        j_min = int(np.argmin(line))
        lines["xyz"[axis]] = {
            "n": int(line.size),
            "u_min_normalised": float(line[j_min] / scale),
            "argmin_fraction": float(j_min / max(line.size - 1, 1)),
        }

    # The driven-lid problem has its interior recirculation minimum on the vertical
    # (wall-normal) centreline; pick the most negative interior minimum among the three.
    interior = {k: v for k, v in lines.items()
                if 0.02 < v["argmin_fraction"] < 0.98}
    chosen = min(interior or lines, key=lambda k: (interior or lines)[k]["u_min_normalised"])
    u_min_sim = lines[chosen]["u_min_normalised"]

    err_ku = reg.rel_error(u_min_sim, reg.KU_1987_3D["u_min"])
    u_min_2d = min(u for _, u in reg.GHIA_U_CENTERLINE[1000])
    correction = (u_min_sim - u_min_2d) / abs(u_min_2d)

    # Spanwise symmetry diagnostic (axis-convention agnostic): for a square cavity the
    # spanwise axis is the one that mirrors ux (and anti-mirrors uz) exactly.  Test all
    # three axes and keep the best one -- never assume the storage order.
    uz = np.asarray(z["uz"], dtype=float) if "uz" in z else None
    mirror: dict[str, dict[str, float]] = {}
    for axis in range(3):
        def flip(arr):
            if axis == 0:
                return arr[::-1, :, :]
            if axis == 1:
                return arr[:, ::-1, :]
            return arr[:, :, ::-1]

        mirror["xyz"[axis]] = {
            "ux_mirror_residual": float(np.max(np.abs(ux - flip(ux))) / max(abs(scale), 1e-30)),
            "uz_antimirror_residual": (float(np.max(np.abs(uz + flip(uz))) / max(abs(scale), 1e-30))
                                       if uz is not None else None),
        }
    span_axis = min(mirror, key=lambda k: mirror[k]["ux_mirror_residual"])

    return {
        "case": "cavity_3d_full",
        "artifact": npz_path,
        "independence": reg.TIER_FIELD,
        "measured": {"u_min_normalised": u_min_sim, "centreline_axis": chosen,
                     "u_lid_stored": u_lid, "field_already_normalised": normalised_field,
                     "grid": [int(nx), int(ny), int(nz)]},
        "reference": [
            {"name": "Ku/Hirsh/Taylor 1987 (3D Re=1000)",
             "value": reg.KU_1987_3D["u_min"], "rel_err": err_ku,
             "verdict": reg.classify(err_ku)},
            {"name": "Ghia 1982 2D Re=1000 (u_min, for 3D-correction sanity)",
             "value": u_min_2d, "sim_3d_correction": correction,
             "verdict": "REFERENCE-ONLY"},
        ],
        "quality": {"centreline_candidates": lines,
                    "symmetry": mirror, "spanwise_axis": span_axis,
                    "spanwise_ux_mirror_residual": mirror[span_axis]["ux_mirror_residual"]},
        "verdict": reg.classify(err_ku),
        "provenance": reg.KU_1987_3D["provenance"],
    }


# --------------------------------------------------------------------------------------
# 2. Dam break — recompute X(T) from the history CSV
# --------------------------------------------------------------------------------------
def _read_hist(path: str) -> dict[str, list[float]]:
    cols: dict[str, list[float]] = {}
    with open(path, newline="", encoding="utf-8") as fh:
        rdr = csv.DictReader(fh)
        for name in rdr.fieldnames or []:
            cols[name.strip()] = []
        for row in rdr:
            for name in cols:
                raw = row.get(name)
                if raw in (None, ""):
                    continue
                cols[name].append(float(raw))
    return cols


def check_dam_break(hist_csv: str, label: str = "") -> dict[str, Any]:
    """Re-judge the dam-break front position against Martin & Moyce (1952)."""
    cols = _read_hist(hist_csv)
    t_mm = cols.get("T_MM") or cols.get("T")
    x = cols.get("X_toe") or cols.get("X_glob") or cols.get("X")
    if t_mm is None or x is None:
        raise ValueError(f"{hist_csv}: need T_MM and X_* columns, got {list(cols)}")

    ref_t = reg.MARTIN_MOYCE_1952["T"]
    ref_z = reg.MARTIN_MOYCE_1952["Z"]
    ok = reg.curve_errors(t_mm, x, ref_t, ref_z)

    # Convention audit: the same history plotted on the legacy axis T_code=t*sqrt(g/a)
    # lands sqrt(2) to the left, i.e. it reads as a systematically *faster* collapse.
    t_code = cols.get("T_code")
    by_wrong_axis = None
    if t_code:
        by_wrong_axis = reg.curve_errors(t_code, x, ref_t, ref_z)
        t_shift = [t * reg.DAM_BREAK_TIME_FACTOR for t in t_code]
        max_dev = max(abs(a - b) for a, b in zip(t_shift, t_mm)) if t_mm else None
    else:
        t_shift, max_dev = None, None

    return {
        "case": f"dam_break_sc{(':' + label) if label else ''}",
        "artifact": hist_csv,
        "independence": reg.TIER_FIELD,
        "measured": {"n_samples": len(t_mm), "T_range": [min(t_mm), max(t_mm)],
                     "X_range": [min(x), max(x)]},
        "reference": [{"name": "Martin & Moyce 1952 (digitised)",
                       "rel_err_l_inf": ok["l_inf"], "rms": ok["rms"],
                       "n_points": ok["n_points"], "verdict": ok["verdict"]}],
        "quality": {
            "convention_correct_axis": ok,
            "convention_legacy_axis": by_wrong_axis,
            "sqrt2_consistency_max_abs": max_dev,
        },
        "verdict": ok["verdict"],
        "provenance": reg.MARTIN_MOYCE_1952["provenance"],
    }


# --------------------------------------------------------------------------------------
# 3. Forchheimer square array — recompute drag errors and audit the Stokes intercept
# --------------------------------------------------------------------------------------
def check_forchheimer(result_json: str) -> dict[str, Any]:
    with open(result_json, encoding="utf-8") as fh:
        data = json.load(fh)
    rows = data.get("rows") or []
    if not rows:
        raise ValueError(f"{result_json}: no rows")

    recomputed = []
    for r in rows:
        r_sim, r_ref = r.get("R_sim"), r.get("R_ref_A")
        if r_sim is None or r_ref is None:
            continue
        err = reg.rel_error(r_sim, r_ref)
        recomputed.append({
            "re_target": r.get("re_target"), "d": r.get("d"), "re_ach": r.get("re_ach"),
            "R_sim": r_sim, "R_ref_A": r_ref, "rel_err_recomputed": err,
            "rel_err_reported": r.get("err_A"),
            "verdict": reg.classify(err),
            "steady": r.get("steady"), "mass_drift": r.get("mass_drift"),
            "y0_f_form": r.get("y0_f_form"),
        })

    errs = [c["rel_err_recomputed"] for c in recomputed]
    worst = max(errs) if errs else float("nan")
    # Baseline audit: Stokes-limit intercept against Sangani & Acrivos f(phi_s=0.40).
    f_ref = reg.SANGANI_ACRIVOS_F40["f_stokes"]
    y0s = [c["y0_f_form"] for c in recomputed if c["y0_f_form"] is not None]
    y0_err = [reg.rel_error(y, f_ref) for y in y0s] if y0s else []
    reported = [c["rel_err_reported"] for c in recomputed if c["rel_err_reported"] is not None]
    max_arith_gap = (max(abs(a - b) for a, b in zip(errs, reported)) if reported else None)

    return {
        "case": "forchheimer",
        "artifact": result_json,
        "independence": f"{reg.TIER_ERROR}+{reg.TIER_BASELINE}",
        "measured": {"n_rows": len(recomputed), "worst_rel_err": worst,
                     "median_rel_err": sorted(errs)[len(errs) // 2] if errs else None},
        "reference": [
            {"name": "Koch & Ladd 1997 (via Forslund 2023 Fig.6), R_ref_A",
             "rel_err_l_inf": worst, "verdict": reg.classify(worst)},
            {"name": "Sangani & Acrivos f(phi_s=0.40)=217.89 (Stokes intercept)",
             "rel_err_max": max(y0_err) if y0_err else None,
             "verdict": reg.classify(max(y0_err)) if y0_err else "INDETERMINATE"},
        ],
        "quality": {"rows": recomputed,
                    "max_arith_gap_vs_reported": max_arith_gap,
                    "all_steady": all(c["steady"] for c in recomputed) if recomputed else None},
        "verdict": reg.classify(worst),
        "provenance": data.get("reference", {}),
    }


# --------------------------------------------------------------------------------------
# 4. Plane Poiseuille — audit the analytic baseline itself
# --------------------------------------------------------------------------------------
def check_poiseuille(result_json: str) -> dict[str, Any]:
    with open(result_json, encoding="utf-8") as fh:
        d = json.load(fh)
    cs2 = 1.0 / 3.0
    delta_rho = d.get("delta_rho")
    L = d.get("nx")
    H_eff = d.get("H_eff")
    nu = d.get("nu_lb")
    if None in (delta_rho, L, H_eff, nu):
        raise ValueError(f"{result_json}: need delta_rho, nx, H_eff, nu_lb")

    grad_p = delta_rho * cs2 / L
    ana = reg.poiseuille_planar(grad_p, H_eff, nu)
    err_max = reg.rel_error(ana["u_max"], d["u_max_ana"])
    err_mean = reg.rel_error(ana["u_mean"], d["u_mean_ana"])
    # half-way bounce-back bookkeeping: H_eff must be ny - 2 (two half lattice layers).
    h_be = (d.get("ny") - 2) if d.get("ny") is not None else None
    return {
        "case": "poiseuille_2d",
        "artifact": result_json,
        "independence": reg.TIER_BASELINE,
        "measured": {"grad_p_recomputed": grad_p, "u_max_recomputed": ana["u_max"],
                     "u_mean_recomputed": ana["u_mean"],
                     "u_max_reported_ana": d["u_max_ana"],
                     "u_max_reported_num": d.get("u_max_num")},
        "reference": [
            {"name": "plane Poiseuille u_max=G H^2/(8nu)",
             "rel_err": err_max, "verdict": reg.classify(err_max)},
            {"name": "plane Poiseuille u_mean=2/3 u_max",
             "rel_err": err_mean, "verdict": reg.classify(err_mean)},
            {"name": "H_eff = ny-2 (half-way bounce-back)",
             "H_eff_reported": H_eff, "H_eff_expected": h_be,
             "verdict": "PASS" if h_be == H_eff else "FAIL"},
        ],
        "quality": {"num_vs_analytic_rel_err": reg.rel_error(d["u_max_num"], ana["u_max"]),
                    "case_self_reported_err_pct": d.get("u_max_err_pct")},
        "verdict": reg.classify(max(err_max, err_mean)),
        "provenance": "analytic (no empirical constant)",
    }


# --------------------------------------------------------------------------------------
# Orchestration
# --------------------------------------------------------------------------------------
def _candidate(*paths: str) -> str | None:
    for p in paths:
        if p and os.path.exists(p):
            return p
    return None


def run(lbm_root: str) -> dict[str, Any]:
    bench = os.path.join(lbm_root, "benchmarks")
    out: dict[str, Any] = {"lbm_root": lbm_root, "checks": [], "skipped": []}

    def add(name, fn, *args):
        try:
            out["checks"].append(fn(*args))
        except Exception as exc:  # noqa: BLE001 - report, never crash the audit
            out["skipped"].append({"case": name, "reason": f"{type(exc).__name__}: {exc}"})

    cav = _candidate(os.path.join(bench, "verified/cavity_3d_full/case_963.npz"),
                     os.path.join(bench, "verified/cavity_3d_full/case_643.npz"))
    if cav:
        add("cavity_3d_full", check_cavity_3d, cav)
    else:
        out["skipped"].append({"case": "cavity_3d_full", "reason": "no field npz found"})

    h40 = _candidate(os.path.join(bench, "pending/dam_break_sc/hist_a40.csv"))
    h80 = _candidate(os.path.join(bench, "pending/dam_break_sc/hist_a80.csv"))
    if h40:
        add("dam_break_sc:a40", check_dam_break, h40, "a40")
    if h80:
        add("dam_break_sc:a80", check_dam_break, h80, "a80")
    if not (h40 or h80):
        out["skipped"].append({"case": "dam_break_sc", "reason": "no history csv"})

    p_for = _candidate(os.path.join(bench, "pending/forchheimer/result.json"),
                       os.path.join(bench, "verified/forchheimer/result.json"))
    if p_for:
        add("forchheimer", check_forchheimer, p_for)
    else:
        out["skipped"].append({"case": "forchheimer", "reason": "no result.json"})

    p_poi = _candidate(os.path.join(bench, "verified/poiseuille_2d/result.json"))
    if p_poi:
        add("poiseuille_2d", check_poiseuille, p_poi)
    else:
        out["skipped"].append({"case": "poiseuille_2d", "reason": "no result.json"})

    verdicts = [c["verdict"] for c in out["checks"]]
    out["summary"] = {
        "n_checks": len(out["checks"]),
        "n_skipped": len(out["skipped"]),
        "verdicts": {v: verdicts.count(v) for v in sorted(set(verdicts))},
        "worst": "FAIL" if "FAIL" in verdicts else ("MARGINAL" if "MARGINAL" in verdicts
                                                   else ("PASS" if verdicts else "INDETERMINATE")),
        "tol_target": reg.TOL_TARGET, "tol_acceptable": reg.TOL_ACCEPTABLE,
    }
    return out


def to_markdown(report: dict[str, Any]) -> str:
    lines = ["# Independent re-check of LBM benchmark cases", "",
             f"- LBM root: `{report['lbm_root']}`",
             f"- checks: {report['summary']['n_checks']} · skipped: {report['summary']['n_skipped']}",
             f"- verdicts: {report['summary']['verdicts']}",
             f"- standards: PASS ≤ {reg.TOL_TARGET:.0%} · MARGINAL ≤ "
             f"{reg.TOL_ACCEPTABLE:.0%} · otherwise FAIL", "",
             "| case | independence | key quantity | worst rel. err | verdict |",
             "|---|---|---|---|---|"]
    for c in report["checks"]:
        if c["case"].startswith("cavity"):
            q = f"u_min={c['measured']['u_min_normalised']:.5f}"
            err = c["reference"][0]["rel_err"]
        elif c["case"].startswith("dam_break"):
            q = f"X(T) n={c['reference'][0]['n_points']}"
            err = c["reference"][0]["rel_err_l_inf"]
        elif c["case"] == "forchheimer":
            q = f"worst R err over {c['measured']['n_rows']} rows"
            err = c["reference"][0]["rel_err_l_inf"]
        else:
            q = "u_max analytic baseline"
            err = c["reference"][0]["rel_err"]
        lines.append(f"| {c['case']} | {c['independence']} | {q} | {err:.4%} | {c['verdict']} |")
    if report["skipped"]:
        lines += ["", "## Skipped", ""] + [f"- `{s['case']}`: {s['reason']}"
                                           for s in report["skipped"]]
    lines += ["", "> 判定口径：PASS ≤3%（平台目标），MARGINAL ≤15%，>15% = FAIL（不可接受）。"]
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--lbm-root", default="/root/tensor-ecosystem/TensorLBM")
    ap.add_argument("--out-artifacts", default="artifacts/independent_recheck")
    args = ap.parse_args(argv)

    report = run(args.lbm_root)
    os.makedirs(args.out_artifacts, exist_ok=True)
    jpath = os.path.join(args.out_artifacts, "recheck.json")
    mpath = os.path.join(args.out_artifacts, "recheck.md")
    with open(jpath, "w", encoding="utf-8") as fh:
        json.dump(report, fh, indent=1, ensure_ascii=False)
    with open(mpath, "w", encoding="utf-8") as fh:
        fh.write(to_markdown(report))
    print(to_markdown(report))
    print(f"[written] {jpath}\n[written] {mpath}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())