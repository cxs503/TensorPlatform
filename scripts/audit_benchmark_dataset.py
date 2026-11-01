#!/usr/bin/env python3
"""Benchmark reference dataset integrity audit (verification-debt payment).

The resistance benchmark harness (``backend/api/domain/ship_benchmark.py``)
compares computed ``Ct`` against ``experimental_ct`` stored in
``backend/data/benchmark_reference.json``.  Those reference rows are the
*ground truth* of the platform: if they are internally inconsistent, every
"pass" verdict downstream is meaningless.  This tool re-derives every
kinematic / hydrostatic / hydrodynamic quantity that can be recomputed from
the stored parameters alone (no network, no external file) and reports the
closure error of each.

Checks (all closed-form, analytic):

K1 FROUDE      Fr == V / sqrt(g L)                       (pure kinematics)
K2 KNOT_MS     speed_ms == speed_kn * 0.514444           (unit closure)
K3 MASS_CB     Cb * L * B * T == m / rho                 (hydrostatic closure)
K4 FRICTION    Ct / Cf(ITTC-57, Re = V L / nu) >= 1      (hard physical bound:
               total resistance cannot be below flat-plate friction)
K5 RT_CT       Rt == 0.5 rho S V^2 Ct                     (only where S is stored)
K6 PROVENANCE  every case carries a non-empty source citation

Outputs ``artifacts/benchmark_dataset_audit/audit.json`` plus three figures.
Read-only with respect to the dataset: run with ``--fix-baseline`` to emit the
self-consistent replacement values for review.
"""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from typing import Any, Dict, List

ROOT = Path(__file__).resolve().parent.parent
DATASET = ROOT / "backend" / "data" / "benchmark_reference.json"
OUTDIR = ROOT / "artifacts" / "benchmark_dataset_audit"

G = 9.80665
KNOT = 0.514444  # m/s per knot

TOL_FR = 0.005        # relative
TOL_KNOT = 0.005      # relative
TOL_MASS = 0.03       # relative (3 %)
TOL_RT = 0.02         # relative (2 %)
CF_FLOOR = 1.02       # Ct/Cf hard bound (2 % measurement-noise tolerance)
CF_CEIL_ADVISORY = 2.5

DEFAULT_NU = {"fresh": 1.004e-6, "sea": 1.19e-6}


def ittc57_cf(re: float) -> float:
    """ITTC-57 flat-plate friction coefficient."""
    return 0.075 / (math.log10(re) - 2.0) ** 2


def rel_err(a: float, b: float) -> float:
    if b == 0:
        return float("inf")
    return abs(a - b) / abs(b)


def audit_case(case: Dict[str, Any], data_integrity: Dict[str, Any] | None = None) -> Dict[str, Any]:
    data_integrity = data_integrity or {}
    geo = case.get("geometry", {})
    rho = case.get("water_density_kg_m3")
    nu = case.get("kinematic_viscosity_m2_s", DEFAULT_NU["fresh"])
    L = geo.get("length_m")
    V = case.get("speed_ms")
    issues: List[str] = []
    findings: Dict[str, Any] = {}

    # --- K1 Froude -------------------------------------------------------
    if L and V:
        fr_calc = V / math.sqrt(G * L)
        fr_store = case.get("froude_number")
        err = rel_err(fr_store, fr_calc) if fr_store else float("inf")
        ok = fr_store is not None and err <= TOL_FR
        findings["K1_froude"] = {
            "stored": fr_store, "recomputed": round(fr_calc, 4),
            "rel_err": round(err, 4), "tol": TOL_FR, "pass": ok,
        }
        if not ok:
            issues.append(
                f"K1 FROUDE: stored Fr={fr_store} vs V/sqrt(gL)={fr_calc:.4f} "
                f"({err*100:.1f}% off, tol {TOL_FR*100:.1f}%)"
            )

    # --- K2 knot -> m/s (frame aware) ------------------------------------
    lam = case.get("scale_factor")
    if case.get("speed_kn") is not None and V is not None:
        # full-scale knots -> working-frame speed (Froude scaling when the row
        # carries model geometry)
        v_calc = case["speed_kn"] * KNOT / (math.sqrt(lam) if lam else 1.0)
        err = rel_err(V, v_calc)
        ok = err <= TOL_KNOT
        findings["K2_knot_ms"] = {
            "stored_ms": V, "recomputed_ms": round(v_calc, 4),
            "rel_err": round(err, 4), "tol": TOL_KNOT, "pass": ok,
        }
        if not ok:
            issues.append(
                f"K2 KNOT_MS: speed_ms={V} vs {case['speed_kn']} kn="
                f"{v_calc:.4f} m/s ({err*100:.1f}% off)"
            )

    # --- K3 hydrostatic closure -----------------------------------------
    B, T, cb = geo.get("beam_m"), geo.get("draft_m"), geo.get("cb")
    if geo.get("displacement_t") is not None:
        mass = geo["displacement_t"] * 1000.0
    elif geo.get("displacement_kg") is not None:
        mass = geo["displacement_kg"]
    else:
        mass = None
    if L and B and T and cb and mass and rho:
        vol_geo = L * B * T * cb
        vol_mass = mass / rho
        err = rel_err(vol_mass, vol_geo)
        ok = err <= TOL_MASS
        findings["K3_mass_cb"] = {
            "mass_kg": mass, "rho": rho,
            "vol_from_mass_m3": round(vol_mass, 3),
            "vol_from_LBTCb_m3": round(vol_geo, 3),
            "cb_implied": round(vol_mass / (L * B * T), 4),
            "rel_err": round(err, 4), "tol": TOL_MASS, "pass": ok,
        }
        if not ok:
            issues.append(
                f"K3 MASS_CB: m/rho={vol_mass:.2f} m3 vs Cb*L*B*T={vol_geo:.2f} m3 "
                f"({err*100:.1f}% off; Cb_implied={vol_mass/(L*B*T):.4f} vs stored {cb})"
            )

    # --- K4 friction bound ----------------------------------------------
    ct = case.get("experimental_ct")
    if L and V and ct:
        re = V * L / nu
        cf = ittc57_cf(re)
        ratio = ct / cf
        ok = ratio >= CF_FLOOR
        findings["K4_friction_bound"] = {
            "Re": float(f"{re:.4e}"), "Cf_ittc57": round(cf, 6),
            "Ct_over_Cf": round(ratio, 3), "floor": CF_FLOOR,
            "pass": ok, "advisory_high": ratio > CF_CEIL_ADVISORY,
        }
        if not ok:
            issues.append(
                f"K4 FRICTION: Ct/Cf={ratio:.3f} < {CF_FLOOR} "
                f"(Re={re:.2e}) - total resistance below flat-plate friction"
            )
    # --- K5 Rt closure (only when wetted surface is stored) --------------
    if geo.get("wetted_surface_m2") and V and ct and rho:
        rt_calc = 0.5 * rho * geo["wetted_surface_m2"] * V * V * ct
        rt_store = case.get("experimental_rt_n")
        if rt_store is None:
            findings["K5_rt_ct"] = {"stored": None, "recomputed": round(rt_calc, 2),
                                    "pass": True, "skipped": "no stored Rt"}
        else:
            err = rel_err(rt_store, rt_calc)
            ok = err <= TOL_RT
            findings["K5_rt_ct"] = {
                "stored": rt_store, "recomputed": round(rt_calc, 2),
                "rel_err": round(err, 4), "tol": TOL_RT, "pass": ok,
            }
            if not ok:
                issues.append(
                    f"K5 RT_CT: stored Rt={rt_store} N vs 0.5*rho*S*V^2*Ct="
                    f"{rt_calc:.1f} N ({err*100:.1f}% off, tol {TOL_RT*100:.0f}%)"
                )
    else:
        # no wetted surface -> Rt is a stored derivative that cannot be closed
        rt_store = case.get("experimental_rt_n")
        withheld_ids = {w["case_id"] for w in data_integrity.get("withheld", [])}
        findings["K7_rt_withheld"] = {
            "stored": rt_store, "pass": rt_store is None,
            "documented": case["case_id"] in withheld_ids,
        }
        if rt_store is not None:
            implied_s = (0.5 * rho * V * V * ct) if rho and V and ct else None
            findings["K5_rt_ct"] = {
                "stored": rt_store, "pass": False, "skipped": "no wetted_surface_m2",
                "implied_S_m2": round(rt_store / implied_s, 4) if implied_s else None,
            }
            issues.append(
                "K5 RT_CT: Rt stored but geometry lacks wetted_surface_m2 - "
                f"closure not verifiable (stored Rt implies S="
                f"{rt_store/implied_s:.3f} m2)" if implied_s else
                "K5 RT_CT: Rt stored without wetted surface / speed"
            )

    # --- K6 provenance ---------------------------------------------------
    cit = (case.get("source_citation") or "").strip()
    findings["K6_provenance"] = {"len": len(cit), "pass": len(cit) >= 20}
    if len(cit) < 20:
        issues.append("K6 PROVENANCE: missing/too-short source_citation")

    return {
        "case_id": case["case_id"],
        "ship_type": case.get("ship_type"),
        "status": "PASS" if not issues else "FAIL",
        "issues": issues,
        "checks": findings,
    }


def build_report(data: Dict[str, Any]) -> Dict[str, Any]:
    rows = [audit_case(c, data.get("integrity", {})) for c in data.get("cases", [])]
    failed = [r for r in rows if r["status"] == "FAIL"]
    checks: Dict[str, Dict[str, int]] = {}
    for r in rows:
        for k, v in r["checks"].items():
            slot = checks.setdefault(k, {"pass": 0, "fail": 0, "skipped": 0})
            if v.get("skipped"):
                slot["skipped"] += 1
            elif v.get("pass"):
                slot["pass"] += 1
            else:
                slot["fail"] += 1
    return {
        "dataset": str(DATASET.relative_to(ROOT)),
        "dataset_version": data.get("version"),
        "n_cases": len(rows),
        "n_fail": len(failed),
        "checks": checks,
        "tolerances": {
            "K1_froude": TOL_FR, "K2_knot_ms": TOL_KNOT, "K3_mass_cb": TOL_MASS,
            "K4_friction_floor": CF_FLOOR, "K5_rt_ct": TOL_RT,
        },
        "cases": rows,
    }


def figures(report: Dict[str, Any], data: Dict[str, Any]) -> List[str]:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    OUTDIR.mkdir(parents=True, exist_ok=True)
    by_id = {c["case_id"]: c for c in data["cases"]}
    paths: List[str] = []

    # Fig 1: stored vs recomputed Froude number
    fig, ax = plt.subplots(figsize=(6.0, 5.2))
    colors = {"SUBOFF": "#1f77b4", "DTMB-5415": "#d62728", "KVLCC2": "#2ca02c"}
    for r in report["cases"]:
        k1 = r["checks"].get("K1_froude")
        if not k1:
            continue
        st = by_id[r["case_id"]].get("ship_type")
        ax.scatter(k1["recomputed"], k1["stored"], s=70,
                   color=colors.get(st, "k"), label=st, zorder=3)
    lim = [0.0, 2.3]
    ax.plot(lim, lim, "k--", lw=1, label="identity")
    ax.set_xlabel("Froude number recomputed from $V/\\sqrt{gL}$")
    ax.set_ylabel("Froude number stored in dataset")
    ax.set_title("K1: Froude closure of benchmark reference rows")
    ax.set_xlim(*lim); ax.set_ylim(*lim)
    ax.grid(alpha=0.3)
    handles, labels = ax.get_legend_handles_labels()
    uniq = dict(zip(labels, handles))
    ax.legend(uniq.values(), uniq.keys(), fontsize=8)
    p = OUTDIR / "fig1_froude_closure.png"
    fig.tight_layout(); fig.savefig(p, dpi=150); plt.close(fig)
    paths.append(str(p.relative_to(ROOT)))

    # Fig 2: Ct vs Re with ITTC-57 friction line (K4 bound visualised)
    fig, ax = plt.subplots(figsize=(6.4, 5.0))
    res = []
    for c in data["cases"]:
        L = c["geometry"].get("length_m"); V = c.get("speed_ms")
        nu = c.get("kinematic_viscosity_m2_s", DEFAULT_NU["fresh"])
        if not (L and V and c.get("experimental_ct")):
            continue
        re = V * L / nu
        ax.scatter(re, c["experimental_ct"], s=60,
                   color=colors.get(c["ship_type"], "k"), zorder=3)
        res.append((re, c["experimental_ct"], c["case_id"], c["ship_type"]))
    res.sort()
    re_line = [10 ** e for e in [x * 0.05 for x in range(120, 200)]]
    ax.plot(re_line, [ittc57_cf(r) for r in re_line], "k-", lw=1.2,
            label="ITTC-57 $C_F$ (lower bound)")
    ax.plot(re_line, [1.4 * ittc57_cf(r) for r in re_line], "k:", lw=1.0,
            label=r"$1.4\,C_F$ reference band")
    ax.set_xscale("log"); ax.set_yscale("log")
    ax.set_xlabel("Reynolds number $Re = VL/\\nu$")
    ax.set_ylabel("Experimental $C_T$")
    ax.set_title("K4: experimental $C_T$ vs ITTC-57 friction bound")
    ax.grid(alpha=0.3, which="both")
    ax.legend(fontsize=8)
    p = OUTDIR / "fig2_ct_vs_friction.png"
    fig.tight_layout(); fig.savefig(p, dpi=150); plt.close(fig)
    paths.append(str(p.relative_to(ROOT)))

    # Fig 3: stored Rt vs Ct-closure Rt (log-log) for rows with wetted surface
    fig, ax = plt.subplots(figsize=(6.0, 5.0))
    xs, ys, ids = [], [], []
    for r in report["cases"]:
        k5 = r["checks"].get("K5_rt_ct")
        if k5 and k5.get("stored") and k5.get("recomputed"):
            xs.append(k5["recomputed"]); ys.append(k5["stored"]); ids.append(r["case_id"])
    if xs:
        ax.scatter(xs, ys, s=70, color="#1f77b4", zorder=3)
        for x, y, i in zip(xs, ys, ids):
            ax.annotate(i, (x, y), fontsize=7, xytext=(4, 4),
                        textcoords="offset points")
        lo, hi = min(xs + ys) * 0.7, max(xs + ys) * 1.4
        ax.plot([lo, hi], [lo, hi], "k--", lw=1, label="identity")
        ax.set_xscale("log"); ax.set_yscale("log")
        ax.set_xlim(lo, hi); ax.set_ylim(lo, hi)
    ax.set_xlabel("$R_T$ recomputed from $\\frac{1}{2}\\rho S V^2 C_T$  [N]")
    ax.set_ylabel("$R_T$ stored in dataset [N]")
    ax.set_title("K5: force-coefficient closure of stored $R_T$")
    ax.grid(alpha=0.3, which="both"); ax.legend(fontsize=8)
    p = OUTDIR / "fig3_rt_closure.png"
    fig.tight_layout(); fig.savefig(p, dpi=150); plt.close(fig)
    paths.append(str(p.relative_to(ROOT)))
    return paths


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--json-out", default=str(OUTDIR / "audit.json"))
    args = ap.parse_args()

    data = json.loads(DATASET.read_text())
    report = build_report(data)
    OUTDIR.mkdir(parents=True, exist_ok=True)
    Path(args.json_out).write_text(json.dumps(report, indent=2))
    figs = figures(report, data)
    report["figures"] = figs
    Path(args.json_out).write_text(json.dumps(report, indent=2))

    print(f"cases={report['n_cases']} fail={report['n_fail']}")
    for k, v in report["checks"].items():
        print(f"  {k:22s} pass={v['pass']:2d} fail={v['fail']:2d} skipped={v['skipped']:2d}")
    for r in report["cases"]:
        for msg in r["issues"]:
            print(f"  [{r['case_id']}] {msg}")
    print("figures:", ", ".join(figs))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())