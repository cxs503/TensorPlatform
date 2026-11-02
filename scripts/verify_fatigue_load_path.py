#!/usr/bin/env python3
"""Independent verification of the fatigue *load path* of the digital twin.

Verified object (already merged, in service):
    backend/services/digital_twin/models/fatigue.py
      - wave_bending_moment()  (IACS UR S11 vertical wave bending moment)
      - spectral_fatigue()     (stress amplitude transfer + Miner damage)

The fatigue chain's *counting* side (rain-flow / wide-band factor) was verified
against ASTM E1049-85 by mission m1653.  What was never checked against an
external baseline is the LOAD side:

  (V1) the IACS UR S11 wave parameter C(L) over its full validity range
       (including the L <= 90 m and L > 350 m branches), and the 0.19 / 0.11
       wave bending moment coefficients;
  (V2) the transfer function from design sea state to an arbitrary sea state,
       i.e. how the stress amplitude -- and therefore the damage -- scales with
       significant wave height Hs.

External baselines used (no self-reference to the module):
  * IACS UR S11 (Rev.7) 3.2.1 wave parameter and 3.2 wave bending moments;
  * linear wave theory: for a linear (RAO-based) load model the wave-induced
    stress amplitude is proportional to the wave amplitude, hence
    sigma_a(Hs) = sigma_design * (Hs / Hs_ref).  The same platform's own
    seakeeping model is linear (RAO independent of wave amplitude), so the
    heave rms it returns scales exactly linearly with Hs -- that internal
    linearity is measured here as a cross-check of the anchor.

Outputs
    artifacts/m1692/fatigue_load_path.json   (all numbers, zero hand-copying)
    artifacts/m1692/fat_*.png                (figures for the report)

Run:  python3 scripts/verify_fatigue_load_path.py [--tag before|after]
"""
from __future__ import annotations

import argparse
import json
import math
import os
import sys

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from backend.services.digital_twin.models import fatigue as F            # noqa: E402
from backend.services.digital_twin.models.geometry import HullForm       # noqa: E402
from backend.services.digital_twin.models import seakeeping as SK        # noqa: E402

OUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "artifacts", "m1692")


# ---------------------------------------------------------------------------
# independent (reference) implementation of IACS UR S11
# ---------------------------------------------------------------------------
def ref_wave_parameter_C(L: float) -> float:
    """IACS UR S11 Rev.7 3.2.1 wave parameter C(L)."""
    if L <= 90.0:
        return 0.0412 * L + 4.0
    if L <= 300.0:
        return 10.75 - ((300.0 - L) / 100.0) ** 1.5
    return 10.75                      # 300 < L, incl. L > 350


def ref_wave_bending_moment(L: float, B: float, Cb: float):
    C = ref_wave_parameter_C(L)
    base = C * L ** 2 * B * Cb
    return 0.19 * base, 0.11 * base, C        # hogging, sagging [kN m]


def rel(a: float, b: float) -> float:
    return abs(a - b) / abs(b) * 100.0 if b else float("nan")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tag", default="after", help="before|after, only for file naming")
    args = ap.parse_args()
    os.makedirs(OUT_DIR, exist_ok=True)

    res: dict = {"tag": args.tag, "module": "backend/services/digital_twin/models/fatigue.py"}

    # ---------------------------------------------------------------- V1 ----
    # UR S11 wave parameter + moment coefficients over the full L range
    Ls = [50.0, 60.0, 70.0, 80.0, 90.0, 100.0, 120.0, 150.0, 180.0,
          230.0, 265.0, 300.0, 320.0, 350.0, 360.0, 400.0]
    B, Cb = 30.0, 0.82
    rows = []
    for L in Ls:
        m = F.wave_bending_moment(L, B, Cb)
        C_ref = ref_wave_parameter_C(L)
        hog_ref, sag_ref, _ = ref_wave_bending_moment(L, B, Cb)
        rows.append({
            "L_m": L,
            "C_rule": C_ref,
            "C_module": m["C_wave"],
            "C_err_pct": rel(m["C_wave"], C_ref),
            "Mhog_rule_kNm": hog_ref,
            "Mhog_module_kNm": m["M_hog_kNm"],
            "Mhog_err_pct": rel(m["M_hog_kNm"], hog_ref),
            "Msag_err_pct": rel(m["M_sag_kNm"], sag_ref),
            "sag_hog_ratio": m["M_sag_kNm"] / m["M_hog_kNm"],
        })
    res["V1_wave_parameter"] = {
        "rows": rows,
        "max_C_err_pct": max(r["C_err_pct"] for r in rows),
        "max_C_err_pct_in_rule_range_90_350": max(
            r["C_err_pct"] for r in rows if 90.0 < r["L_m"] <= 350.0),
        "worst_L_m": max(rows, key=lambda r: r["C_err_pct"])["L_m"],
        "sag_hog_ratio_nominal": 0.11 / 0.19,
    }

    # validity of the 0.19 / 0.11 coefficients: deterministic transcription check
    L0 = 180.0
    hog_ref, sag_ref, _ = ref_wave_bending_moment(L0, 45.0, 0.7)
    m0 = F.wave_bending_moment(L0, 45.0, 0.7)
    res["V1_coefficients"] = {
        "L_m": L0, "B_m": 45.0, "Cb": 0.7,
        "Mhog_ref_kNm": hog_ref, "Mhog_module_kNm": m0["M_hog_kNm"],
        "Msag_ref_kNm": sag_ref, "Msag_module_kNm": m0["M_sag_kNm"],
        "err_pct": rel(m0["M_hog_kNm"], hog_ref),
    }

    # ---------------------------------------------------------------- V2 ----
    # Hs transfer function: module vs linear wave theory
    hull = HullForm(180.0, 30.0, 18.0, 0.82)
    Tp = 9.0
    hs_grid = [2.0, 4.0, 5.0, 6.0, 8.0, 10.0, 11.0, 12.0]
    design = F.spectral_fatigue(hull, F.HS_IACS, Tp)
    sig_design = design["sigma_amp_MPa"]
    truth = []
    for Hs in hs_grid:
        out = F.spectral_fatigue(hull, Hs, Tp)
        sig_lin = sig_design * Hs / F.HS_IACS          # linear wave theory
        truth.append({
            "Hs_m": Hs,
            "sigma_amp_module_MPa": out["sigma_amp_MPa"],
            "sigma_amp_linear_MPa": sig_lin,
            "sigma_err_pct": rel(out["sigma_amp_MPa"], sig_lin),
            "damage_per_day_module": out["damage_per_day"],
            "life_years_module": out["life_years"],
            "nu0_Hz": out["nu0_Hz"],
        })
    # linear reference damage: only the stress level may change with Hs
    for row in truth:
        # NB: the kwarg is historically named sigma_rms_mpa but is consumed as a
        # stress *amplitude* (see the docstring of spectral_fatigue).
        out_lin = F.spectral_fatigue(hull, row["Hs_m"], Tp,
                                     sigma_rms_mpa=row["sigma_amp_linear_MPa"])
        row["damage_per_day_linear"] = out_lin["damage_per_day"]
        row["life_years_linear"] = out_lin["life_years"]
        row["damage_ratio_module_over_linear"] = (
            row["damage_per_day_module"] / out_lin["damage_per_day"]
            if out_lin["damage_per_day"] > 0 else float("nan"))
        row["life_ratio_module_over_linear"] = (
            row["life_years_module"] / out_lin["life_years"]
            if out_lin["life_years"] > 0 else float("nan"))
    res["V2_hs_transfer"] = {
        "hull": {"L": hull.L, "B": hull.B, "T": hull.T, "Cb": hull.Cb},
        "Tp_s": Tp, "Hs_ref_m": F.HS_IACS,
        "sigma_design_MPa": sig_design,
        "rows": truth,
        "max_sigma_err_pct": max(r["sigma_err_pct"] for r in truth),
        "max_sigma_err_pct_excl_ref": max(
            r["sigma_err_pct"] for r in truth if abs(r["Hs_m"] - F.HS_IACS) > 1e-9),
        "life_ratio_at_Hs5": [r["life_ratio_module_over_linear"]
                              for r in truth if r["Hs_m"] == 5.0][0],
    }

    # linearity self-consistency after the fix: sigma(2*Hs) == 2*sigma(Hs)
    s4 = F.spectral_fatigue(hull, 4.0, Tp)["sigma_amp_MPa"]
    s8 = F.spectral_fatigue(hull, 8.0, Tp)["sigma_amp_MPa"]
    res["V2_self_similarity"] = {
        "sigma_Hs4_MPa": s4, "sigma_Hs8_MPa": s8,
        "ratio_sigma_8_over_4": s8 / s4, "ratio_expected": 2.0,
        "err_pct": rel(s8 / s4, 2.0),
    }

    # same-platform linearity cross-check: heave rms from the linear strip model
    kk = []
    for Hs in [2.0, 4.0, 6.0, 8.0, 11.0]:
        ir = SK.irregular_seastate(Hs, Tp, 180.0, hull, 8.5, 12.0, n_freq=25, n_sections=41)
        kk.append({"Hs_m": Hs, "heave_rms_m": ir["heave_rms_m"],
                   "heave_rms_over_Hs": ir["heave_rms_m"] / Hs})
    res["V2_platform_linearity"] = {
        "rows": kk,
        "spread_heave_rms_over_Hs_pct": rel(max(r["heave_rms_over_Hs"] for r in kk),
                                            min(r["heave_rms_over_Hs"] for r in kk)),
    }

    # ---------------------------------------------------------------- V3 ----
    # independent time-domain closure at the linear stress level
    rec_rms = 8.0
    rec = F.synthesise_stress(n=120_000, dt=0.2, sigma_rms_MPa=rec_rms, seed=11)
    rf = F.rainflow_damage(rec)
    st = F.record_spectral_stats(rec, 0.2)
    mom = F.rayleigh_bilinear_moment(st["sigma_rms_MPa"])
    d_nb = st["nu0_Hz"] * 120_000 * 0.2 * (mom["inv_life_m3"] + mom["inv_life_m5"])
    res["V3_time_domain_closure"] = {
        "sigma_rms_MPa": st["sigma_rms_MPa"],
        "damage_rainflow": rf["damage"], "damage_narrowband": d_nb,
        "ratio": d_nb / rf["damage"], "err_pct": rel(d_nb, rf["damage"]),
    }

    res["summary"] = {
        "V1_max_C_err_pct": res["V1_wave_parameter"]["max_C_err_pct"],
        "V1_max_C_err_pct_in_90_350": res["V1_wave_parameter"]["max_C_err_pct_in_rule_range_90_350"],
        "V2_max_sigma_err_pct_excl_ref": res["V2_hs_transfer"]["max_sigma_err_pct_excl_ref"],
        "V2_life_ratio_at_Hs5": res["V2_hs_transfer"]["life_ratio_at_Hs5"],
        "V2_self_similarity_err_pct": res["V2_self_similarity"]["err_pct"],
        "V3_err_pct": res["V3_time_domain_closure"]["err_pct"],
    }

    path = os.path.join(OUT_DIR, "fatigue_load_path_%s.json" % args.tag)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(res, fh, indent=2, ensure_ascii=False)
    print(json.dumps(res["summary"], indent=2))
    print("written:", os.path.normpath(path))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())