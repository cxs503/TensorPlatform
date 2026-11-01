#!/usr/bin/env python3
"""m1654 — ITTC-1978 model->full-scale extrapolation verification runner.

Produces every number quoted in ``docs/audits/full_scale_extrapolation_ittc1978_m1654.md``
and in the published report, so that no figure in the write-up is hand-copied:

  * ``docs/reports/m1654/results.json``  — full numeric record (tables + identities)
  * ``docs/reports/m1654/fig1_friction_lines.png``
  * ``docs/reports/m1654/fig2_correlation_allowance.png``
  * ``docs/reports/m1654/fig3_extrapolation_cascade.png``

Usage (from the repo root)::

    python3 tools/verif/verify_full_scale_m1654.py

The SUBOFF datum is read from the in-tree canon
``backend/data/benchmark_reference.json`` (v3.0, Roddy 1990 DTRC-90/012) — nothing is
typed in by hand except the geometric scale ratios, which are *parametric* (no
full-scale EFD datum for SUBOFF exists in the tree, see the audit note).
"""
from __future__ import annotations

import json
import math
import os
import sys

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "backend"))

from services.full_scale_extrapolation import (  # noqa: E402
    DEFAULT_KS_M,
    SEA_WATER_15C,
    correlation_allowance,
    correlation_allowance_zero_length,
    decompose,
    extrapolate,
    ittc1957_cf,
)
from services.ship_resistance import schoenherr_cf  # noqa: E402

OUT = os.path.join(ROOT, "docs", "reports", "m1654")
os.makedirs(OUT, exist_ok=True)

CANON = os.path.join(ROOT, "backend", "data", "benchmark_reference.json")


# ---------------------------------------------------------------------------
# 1. inputs
# ---------------------------------------------------------------------------
def load_suboff_cases():
    with open(CANON, encoding="utf-8") as fh:
        canon = json.load(fh)
    cases = {}
    for case in canon["cases"]:
        if case.get("ship_type") != "SUBOFF":
            continue
        cases[case["case_id"]] = {
            "length_m": case["geometry"]["length_m"],
            "diameter_m": case["geometry"]["diameter_m"],
            "wetted_surface_m2": case["geometry"]["wetted_surface_m2"],
            "speed_ms": case["speed_ms"],
            "rt_n": case.get("experimental_rt_n"),
            "ct": case.get("experimental_ct"),
            "froude": case.get("froude_number"),
            "rho": case["reference_density_kg_m3"],
            "nu": case["reference_kinematic_viscosity_m2_s"],
            "source": case["source_citation"],
        }
    return canon, cases


# ---------------------------------------------------------------------------
# 2. tables
# ---------------------------------------------------------------------------
def friction_line_table():
    rows = []
    for re in (1e5, 1e6, 1e7, 1e8, 1e9, 3e9, 1e10):
        cf_i = ittc1957_cf(re)
        cf_s = schoenherr_cf(re)
        rows.append(
            {
                "re": re,
                "cf_ittc1957": cf_i,
                "cf_schoenherr1932": cf_s,
                "spread_pct": 100.0 * (cf_i - cf_s) / cf_s,
            }
        )
    return rows


def allowance_table():
    rows = []
    for length in (50.0, 100.0, 150.0, 200.0, 320.0, 400.0, 662.398338317871):
        rows.append(
            {
                "length_wl_m": length,
                "dcf_ks150um": correlation_allowance(length, 150e-6),
                "dcf_ks300um": correlation_allowance(length, 300e-6),
            }
        )
    return rows


def extrapolation_table(cases, lambdas=(10.0, 24.0, 40.0), form_factor=1.0):
    rows = []
    for cid, c in cases.items():
        if c["rt_n"] is None:
            continue
        for lam in lambdas:
            res = extrapolate(
                model_length_m=c["length_m"],
                model_speed_ms=c["speed_ms"],
                model_wetted_surface_m2=c["wetted_surface_m2"],
                model_resistance_n=c["rt_n"],
                ship_length_m=c["length_m"] * lam,
                form_factor=form_factor,
                ks=DEFAULT_KS_M,
                rho_model=c["rho"],
                nu_model=c["nu"],
                rho_ship=SEA_WATER_15C[0],
                nu_ship=SEA_WATER_15C[1],
            )
            split = decompose(res)
            rows.append(
                {
                    "case_id": cid,
                    "lambda": lam,
                    "froude": res.froude_number_model,
                    "ship_length_m": res.length_ship_m,
                    "speed_ship_ms": res.speed_ship_ms,
                    "re_model": res.reynolds_model,
                    "re_ship": res.reynolds_ship,
                    "cf_model": res.cf_model,
                    "cf_ship": res.cf_ship,
                    "ct_model": res.ct_model,
                    "cw": res.cw,
                    "delta_cf": res.delta_cf,
                    "ct_ship": res.ct_ship,
                    "delta_ct_total": res.delta_ct_total,
                    "delta_ct_viscous": split["delta_ct_viscous"],
                    "delta_ct_correlation": split["delta_ct_correlation"],
                    "closure_residual": split["closure_residual"],
                    "rt_model_n": res.rt_model_n,
                    "rt_ship_n": res.rt_ship_n,
                    "effective_power_kw": res.effective_power_kw,
                    "warnings": res.warnings,
                }
            )
    return rows


def identity_audit(cases):
    """Machine-precision audit of the two identities the procedure relies on."""
    c = cases["SUBOFF-02"]
    worst = {"wave_invariance": 0.0, "decomposition_closure": 0.0,
             "froude_similarity": 0.0, "lambda3_limit": 0.0}
    for lam in (5.0, 10.0, 24.0, 40.0, 60.0):
        for k in (1.0, 1.05, 1.15, 1.3):
            res = extrapolate(
                model_length_m=c["length_m"], model_speed_ms=c["speed_ms"],
                model_wetted_surface_m2=c["wetted_surface_m2"], model_resistance_n=c["rt_n"],
                ship_length_m=c["length_m"] * lam, form_factor=k,
                rho_model=c["rho"], nu_model=c["nu"],
            )
            split = decompose(res)
            worst["wave_invariance"] = max(
                worst["wave_invariance"],
                abs((res.ct_ship - k * res.cf_ship - res.delta_cf) - res.cw) / res.cw,
            )
            worst["decomposition_closure"] = max(
                worst["decomposition_closure"], abs(split["closure_residual"])
            )
            worst["froude_similarity"] = max(
                worst["froude_similarity"],
                abs(res.froude_number_ship - res.froude_number_model) / res.froude_number_model,
            )
            limit = extrapolate(
                model_length_m=c["length_m"], model_speed_ms=c["speed_ms"],
                model_wetted_surface_m2=c["wetted_surface_m2"], model_resistance_n=c["rt_n"],
                ship_length_m=c["length_m"] * lam, ks=None,
                rho_model=c["rho"], nu_model=c["nu"],
                rho_ship=c["rho"], nu_ship=c["nu"] * lam ** 1.5,
            )
            worst["lambda3_limit"] = max(
                worst["lambda3_limit"], abs(limit.rt_ship_n / limit.rt_model_n - lam ** 3) / lam ** 3
            )
    return worst


def sensitivity_audit(cases):
    """Update coefficients of C_Ts with respect to the two free modelling knobs."""
    c = cases["SUBOFF-02"]
    base = extrapolate(
        model_length_m=c["length_m"], model_speed_ms=c["speed_ms"],
        model_wetted_surface_m2=c["wetted_surface_m2"], model_resistance_n=c["rt_n"],
        ship_length_m=c["length_m"] * 24.0, form_factor=1.0,
        rho_model=c["rho"], nu_model=c["nu"],
    )
    k_only = extrapolate(
        model_length_m=c["length_m"], model_speed_ms=c["speed_ms"],
        model_wetted_surface_m2=c["wetted_surface_m2"], model_resistance_n=c["rt_n"],
        ship_length_m=c["length_m"] * 24.0, form_factor=1.1,
        rho_model=c["rho"], nu_model=c["nu"],
    )
    rough = extrapolate(
        model_length_m=c["length_m"], model_speed_ms=c["speed_ms"],
        model_wetted_surface_m2=c["wetted_surface_m2"], model_resistance_n=c["rt_n"],
        ship_length_m=c["length_m"] * 24.0, ks=300e-6,
        rho_model=c["rho"], nu_model=c["nu"],
    )
    return {
        "base_ct_ship": base.ct_ship,
        "dct_d_one_plus_k": (k_only.ct_ship - base.ct_ship) / 0.1,
        "analytic_cf_ship_minus_cf_model": k_only.cf_ship - k_only.cf_model,
        "dcf_per_100um": rough.ct_ship - base.ct_ship,
        "rt_ship_change_pct_per_100um": 100.0 * (rough.rt_ship_n - base.rt_ship_n) / base.rt_ship_n,
    }


# ---------------------------------------------------------------------------
# 3. figures
# ---------------------------------------------------------------------------
def fig_friction_lines(cases):
    c = cases["SUBOFF-02"]
    re_m = c["speed_ms"] * c["length_m"] / c["nu"]
    re_s = c["speed_ms"] * math.sqrt(24.0) * c["length_m"] * 24.0 / SEA_WATER_15C[1]
    re = [10 ** (5 + 0.05 * i) for i in range(101)]
    plt.figure(figsize=(7.2, 4.4))
    plt.semilogx(re, [ittc1957_cf(r) for r in re], label="ITTC-1957 (platform line)")
    plt.semilogx(re, [schoenherr_cf(r) for r in re], "--", label="Schoenherr 1932 (independent)")
    plt.axvline(re_m, color="C2", lw=1, ls=":")
    plt.axvline(re_s, color="C3", lw=1, ls=":")
    plt.text(re_m, 0.0052, " Re model", color="C2", fontsize=8)
    plt.text(re_s, 0.0052, " Re ship (lambda=24)", color="C3", fontsize=8)
    plt.xscale("log")
    plt.xlabel("Re")
    plt.ylabel("$C_F$")
    plt.title("Friction lines: ITTC-1957 vs Schoenherr 1932 (model/ship Re of SUBOFF-02)")
    plt.grid(alpha=0.3, which="both")
    plt.legend(fontsize=8)
    plt.tight_layout()
    path = os.path.join(OUT, "fig1_friction_lines.png")
    plt.savefig(path, dpi=150)
    plt.close()
    return path


def fig_allowance():
    lengths = [20.0 + 2.0 * i for i in range(400)]
    plt.figure(figsize=(7.2, 4.4))
    for ks, style in ((150e-6, "-"), (300e-6, "--")):
        plt.plot(lengths, [1e3 * correlation_allowance(x, ks) for x in lengths], style,
                 label=f"$k_s$ = {ks*1e6:.0f} um")
    l0 = correlation_allowance_zero_length()
    plt.axhline(0.0, color="k", lw=0.8)
    plt.axvline(150.0, color="C0", lw=1, ls=":")
    plt.text(155, 0.62, "150 m ship: 0.410e-3", fontsize=8, color="C0")
    plt.axvline(l0, color="C7", lw=1, ls=":")
    plt.text(l0 + 8, -0.18, f"zero crossing {l0:.1f} m", fontsize=8, color="C7")
    plt.xlabel("$L_{wl}$ (m)")
    plt.ylabel("$\\Delta C_F$ (x1e-3)")
    plt.title("ITTC-1978 correlation allowance")
    plt.grid(alpha=0.3)
    plt.legend(fontsize=8)
    plt.tight_layout()
    path = os.path.join(OUT, "fig2_correlation_allowance.png")
    plt.savefig(path, dpi=150)
    plt.close()
    return path


def fig_cascade(cases, rows):
    c = cases["SUBOFF-02"]
    demo = [r for r in rows if r["case_id"] == "SUBOFF-02" and r["lambda"] == 24.0][0]
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(11.0, 4.3))

    labels = ["$C_{Tm}$\n(model)", "$C_w$", "$(1+k)C_{Fs}$", "$\\Delta C_F$", "$C_{Ts}$\n(ship)"]
    values = [demo["ct_model"], demo["cw"], demo["cf_ship"], demo["delta_cf"], demo["ct_ship"]]
    colors = ["C0", "C1", "C2", "C3", "C4"]
    bars = ax1.bar(labels, [1e3 * v for v in values], color=colors)
    for bar, v in zip(bars, values):
        ax1.text(bar.get_x() + bar.get_width() / 2, 1e3 * v, f"{1e3*v:.3f}",
                 ha="center", va="bottom", fontsize=8)
    ax1.set_ylabel("$C_T$ / $C_F$ (x1e-3)")
    ax1.set_title("SUBOFF-02 model -> ship, lambda=24\n(ITTC-1978 budget, Fn=%.3f)"
                  % demo["froude"])
    ax1.grid(alpha=0.3, axis="y")

    lam = [r["lambda"] for r in rows if r["case_id"] == "SUBOFF-02"]
    rt = [r["rt_ship_n"] / 1e3 for r in rows if r["case_id"] == "SUBOFF-02"]
    pe = [r["effective_power_kw"] / 1e3 for r in rows if r["case_id"] == "SUBOFF-02"]
    ax2.plot(lam, rt, "o-", color="C0", label="$R_{Ts}$ (kN)")
    ax2.set_xlabel("geometric scale lambda")
    ax2.set_ylabel("$R_{Ts}$ (kN)", color="C0")
    ax2.grid(alpha=0.3)
    ax2b = ax2.twinx()
    ax2b.plot(lam, pe, "s--", color="C3", label="$P_E$ (MW)")
    ax2b.set_ylabel("$P_E$ (MW)", color="C3")
    ax2.set_title("extrapolated full-scale resistance vs scale ratio")
    fig.tight_layout()
    path = os.path.join(OUT, "fig3_extrapolation_cascade.png")
    plt.savefig(path, dpi=150)
    plt.close()
    return path


# ---------------------------------------------------------------------------
# 4. main
# ---------------------------------------------------------------------------
def main():
    canon, cases = load_suboff_cases()
    lambda_rows = extrapolation_table(cases)
    payload = {
        "mission": 1654,
        "module": "services.full_scale_extrapolation",
        "canon_version": canon.get("version"),
        "canon_file": "backend/data/benchmark_reference.json",
        "suboff_cases": cases,
        "tables": {
            "friction_line": friction_line_table(),
            "correlation_allowance": allowance_table(),
            "extrapolation": lambda_rows,
        },
        "identity_audit_relative": identity_audit(cases),
        "sensitivity_audit": sensitivity_audit(cases),
        "full_scale_efd_available": False,
        "full_scale_efd_note": (
            "No reviewable full-scale R_T datum exists in the tree: the KVLCC2 full-scale "
            "rows of benchmark_reference.json are adjudicated WITHHELD (filed R_T/C_T pair "
            "fails the S/(L*B) closure) and the DTRC SUBOFF provenance is model-scale only."
        ),
        "figures": [],
    }
    payload["figures"] = [
        os.path.relpath(p, ROOT) for p in (fig_friction_lines(cases), fig_allowance(), fig_cascade(cases, lambda_rows))
    ]
    out = os.path.join(OUT, "results.json")
    with open(out, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=1)
    print(json.dumps(
        {
            "results": os.path.relpath(out, ROOT),
            "figures": payload["figures"],
            "identity_audit": payload["identity_audit_relative"],
            "sensitivity": payload["sensitivity_audit"],
            "n_extrapolation_rows": len(lambda_rows),
        },
        ensure_ascii=False, indent=1,
    ))


if __name__ == "__main__":
    main()