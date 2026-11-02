#!/usr/bin/env python3
"""Independent verification of ship hydrostatics against closed-form analytical solutions.

Mission m1883 — paper席 self-start · TensorPlatform · agent/m1883

Verifies:
  1. Box barge (rectangular barge): exact textbook formulas
  2. Wigley III parabolic hull: exact analytical integrals
  3. Wetted surface: barge exact S = L*(B + 2T)
  4. Grid convergence: hydrostatics → exact as stations/WLs → ∞

Closed-form references (all exact, no empirical data needed):

Box barge (L, B, T):
  ∇ = B·L·T          Cb = 1
  Aw = B·L            KB = T/2
  BM_T = B²/(12T)     BM_L = L²/(12T)
  S  = L·(B + 2T)

Wigley III: y(x,z) = (B/2)·(1 - (2x/L)²)·(1 - (1 - z/T)²)
  ∇ = 4·B·L·T/9      Cb = 4/9 ≈ 0.444444
  Aw = 2·B·L/3       KB = 5T/8
  BM_T = 3·B²/(35·T)  BM_L = 3·L²/(40·T)
  Cm = 2/3            Cp = Cb/Cm = 2/3

Run:
  python3 tools/verify/verify_hydrostatics_m1883.py
"""
from __future__ import annotations

import json
import math
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

from services.ship_hydrostatics import (
    HullOffsets,
    HydrostaticResult,
    compute_hydrostatics,
    wigley_offsets,
    barge_offsets,
)

# ---------------------------------------------------------------------------
# Closed-form reference values
# ---------------------------------------------------------------------------

def box_barge_exact(L, B, T, KG=None, rho=1025.0):
    """Exact textbook hydrostatics for a rectangular barge."""
    vol = B * L * T
    aw = B * L
    kb = T / 2.0
    bm_t = B * B / (12.0 * T)
    bm_l = L * L / (12.0 * T)
    km_t = kb + bm_t
    gm = km_t - KG if KG is not None else None
    wetted = L * (B + 2.0 * T)
    return {
        "volume_m3": vol,
        "cb": 1.0,
        "cm": 1.0,
        "cp": 1.0,
        "cw": 1.0,
        "waterplane_area_m2": aw,
        "kb_m": kb,
        "bm_t_m": bm_t,
        "bm_l_m": bm_l,
        "km_t_m": km_t,
        "gm_m": gm,
        "wetted_surface_m2": wetted,
        "lcb_m": 0.0,
        "lcf_m": 0.0,
    }


def wigley_exact(L, B, T, KG=None, rho=1025.0):
    """Exact analytical hydrostatics for Wigley III parabolic hull."""
    vol = 4.0 * B * L * T / 9.0
    aw = 2.0 * B * L / 3.0
    kb = 5.0 * T / 8.0
    bm_t = 3.0 * B * B / (35.0 * T)
    bm_l = 3.0 * L * L / (40.0 * T)
    km_t = kb + bm_t
    gm = km_t - KG if KG is not None else None
    cm = 2.0 / 3.0
    cp = 2.0 / 3.0
    cw = 2.0 / 3.0
    return {
        "volume_m3": vol,
        "cb": 4.0 / 9.0,
        "cm": cm,
        "cp": cp,
        "cw": cw,
        "waterplane_area_m2": aw,
        "kb_m": kb,
        "bm_t_m": bm_t,
        "bm_l_m": bm_l,
        "km_t_m": km_t,
        "gm_m": gm,
        "wetted_surface_m2": None,  # no simple closed form for Wigley wetted surface
        "lcb_m": 0.0,
        "lcf_m": 0.0,
    }


# ---------------------------------------------------------------------------
# Verification engine
# ---------------------------------------------------------------------------

TOL_REL = 1e-3  # 0.1% relative tolerance for numerical integration
TOL_ABS = 1e-9

def verify_case(name, result: HydrostaticResult, exact: dict):
    """Compare computed vs exact, return list of verdicts."""
    verdicts = []
    fields = ["volume_m3", "cb", "cm", "cp", "cw", "waterplane_area_m2",
              "kb_m", "bm_t_m", "bm_l_m", "km_t_m", "lcb_m", "lcf_m"]
    if exact.get("gm_m") is not None:
        fields.append("gm_m")

    for f in fields:
        computed = getattr(result, f)
        ref = exact[f]
        if ref is None:
            continue
        if abs(ref) > 1e-12:
            rel_err = abs(computed - ref) / abs(ref)
        else:
            rel_err = abs(computed - ref)
        passed = rel_err < TOL_REL or abs(computed - ref) < TOL_ABS
        verdicts.append({
            "field": f,
            "computed": computed,
            "reference": ref,
            "rel_error": rel_err,
            "passed": passed,
        })
    return verdicts


def main():
    results = {"barge": {}, "wigley": {}, "convergence": {}}
    all_pass = True

    # ------------------------------------------------------------------
    # 1. Box barge verification
    # ------------------------------------------------------------------
    L, B, T = 100.0, 20.0, 8.0
    KG = 6.0
    exact_barge = box_barge_exact(L, B, T, KG=KG)

    hull = barge_offsets(length=L, beam=B, draft=T, n_stations=21, n_waterlines=11)
    res = compute_hydrostatics(hull, kg=KG)

    verdicts_barge = verify_case("barge", res, exact_barge)
    results["barge"] = {
        "geometry": {"L": L, "B": B, "T": T, "KG": KG},
        "verdicts": verdicts_barge,
        "n_pass": sum(1 for v in verdicts_barge if v["passed"]),
        "n_total": len(verdicts_barge),
    }

    # Also check wetted surface
    ws_err = abs(res.wetted_surface_m2 - exact_barge["wetted_surface_m2"]) / exact_barge["wetted_surface_m2"]
    ws_pass = ws_err < 0.01  # 1% tolerance for wetted surface (trapezoidal approx)
    results["barge"]["wetted_surface"] = {
        "computed": res.wetted_surface_m2,
        "reference": exact_barge["wetted_surface_m2"],
        "rel_error": ws_err,
        "passed": ws_pass,
    }

    # ------------------------------------------------------------------
    # 2. Wigley III verification
    # ------------------------------------------------------------------
    L, B, T = 100.0, 15.0, 6.0
    KG = 4.0
    exact_wigley = wigley_exact(L, B, T, KG=KG)

    hull = wigley_offsets(length=L, beam=B, draft=T, n_stations=41, n_waterlines=21)
    res = compute_hydrostatics(hull, kg=KG)

    verdicts_wigley = verify_case("wigley", res, exact_wigley)
    results["wigley"] = {
        "geometry": {"L": L, "B": B, "T": T, "KG": KG},
        "verdicts": verdicts_wigley,
        "n_pass": sum(1 for v in verdicts_wigley if v["passed"]),
        "n_total": len(verdicts_wigley),
    }

    # ------------------------------------------------------------------
    # 3. Grid convergence (Wigley)
    # ------------------------------------------------------------------
    conv_data = []
    for ns in [11, 21, 41, 81, 161]:
        nw = ns // 2 + 1
        hull = wigley_offsets(length=L, beam=B, draft=T, n_stations=ns, n_waterlines=nw)
        res = compute_hydrostatics(hull, kg=KG)
        vol_err = abs(res.volume_m3 - exact_wigley["volume_m3"]) / exact_wigley["volume_m3"]
        kb_err = abs(res.kb_m - exact_wigley["kb_m"]) / exact_wigley["kb_m"]
        bm_err = abs(res.bm_t_m - exact_wigley["bm_t_m"]) / exact_wigley["bm_t_m"]
        conv_data.append({
            "n_stations": ns,
            "n_waterlines": nw,
            "vol_rel_error": vol_err,
            "kb_rel_error": kb_err,
            "bm_rel_error": bm_err,
        })
    results["convergence"] = conv_data

    # ------------------------------------------------------------------
    # Summary
    # ------------------------------------------------------------------
    n_pass_barge = results["barge"]["n_pass"] + (1 if results["barge"]["wetted_surface"]["passed"] else 0)
    n_total_barge = results["barge"]["n_total"] + 1
    n_pass_wigley = results["wigley"]["n_pass"]

    all_pass = (n_pass_barge == n_total_barge) and (n_pass_wigley == results["wigley"]["n_total"])

    summary = {
        "barge_pass": f"{n_pass_barge}/{n_total_barge}",
        "wigley_pass": f"{n_pass_wigley}/{results['wigley']['n_total']}",
        "convergence": "monotonic" if all(
            conv_data[i]["vol_rel_error"] >= conv_data[i+1]["vol_rel_error"]
            for i in range(len(conv_data)-1)
        ) else "non-monotonic",
        "overall": "PASS" if all_pass else "PARTIAL",
    }
    results["summary"] = summary

    # Write JSON
    out_dir = ROOT / "artifacts" / "m1883"
    out_dir.mkdir(parents=True, exist_ok=True)
    with open(out_dir / "hydrostatics_verification.json", "w") as f:
        json.dump(results, f, indent=2, default=str)

    # Print summary
    print(f"=== Hydrostatics Verification m1883 ===")
    print(f"Box barge: {summary['barge_pass']}")
    print(f"Wigley III: {summary['wigley_pass']}")
    print(f"Convergence: {summary['convergence']}")
    print(f"Overall: {summary['overall']}")

    if not all_pass:
        print("\nFailed verdicts:")
        for v in verdicts_barge + verdicts_wigley:
            if not v["passed"]:
                print(f"  {v['field']}: computed={v['computed']}, ref={v['reference']}, err={v['rel_error']:.2e}")

    return 0 if all_pass else 1


if __name__ == "__main__":
    sys.exit(main())
