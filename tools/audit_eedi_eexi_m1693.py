#!/usr/bin/env python3
"""m1693 audit driver: EEDI / EEXI (green shipping) verification-debt evidence.

Produces ``artifacts/m1693/eedi_eexi_audit.json`` (all numbers used in the report
are read back from this file, nothing hand-copied) and three PNG figures.

Lightweight by design: closed-form arithmetic only, no solver, no campaign.
"""

from __future__ import annotations

import json
import math
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "backend"))

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

from services.polar import cii as cii_mod  # noqa: E402
from services.propulsion import attained_eedi  # noqa: E402

OUT = os.path.join(ROOT, "artifacts", "m1693")
os.makedirs(OUT, exist_ok=True)

CF_HFO = 3.114          # MEPC.364(79), value used by services.polar.cii
CF_LEGACY = 3.1144      # legacy HFO value still passed by some callers

CASE = dict(capacity_t=80_000.0, v_ref_kn=14.5, p_me_kw=9_000.0,
            sfc_me_g_kwh=170.0, p_ae_kw=600.0, sfc_ae_g_kwh=190.0)


# --- pre-m1693 formula (kept here only to quantify the defect) --------------
def old_eedi(f_i=1.0, f_c=1.0, f_l=1.0, f_w=None, p_eff_kw=0.0,
             sfc_eff_g_kwh=170.0, f_eff=1.0, **kw):
    cf = kw.pop("cf_me", CF_HFO)
    f_w = f_i if f_w is None else f_w          # old alias
    me = kw["p_me_kw"] * kw["sfc_me_g_kwh"] * cf
    ae = kw["p_ae_kw"] * kw["sfc_ae_g_kwh"] * cf
    eff = f_eff * p_eff_kw * sfc_eff_g_kwh * cf
    return (f_i * (me + ae) - eff) / (f_i * f_c * f_l * kw["capacity_t"] * f_w
                                      * kw["v_ref_kn"])


def rel_err(a, b):
    return 100.0 * (a - b) / b


def main():
    ev = {"meta": {"module": "m1693", "cf_hfo": CF_HFO, "cf_legacy": CF_LEGACY,
                   "case": CASE}}

    # -- 1. baseline case, post-fix -----------------------------------------
    fixed = attained_eedi(**CASE, cf_me=CF_HFO)
    ev["baseline"] = {
        "attained_eedi_g_per_t_nm": fixed.eedi_g_co2_per_t_nm,
        "attained_eedi_legacy_cf": attained_eedi(**CASE, cf_me=CF_LEGACY).eedi_g_co2_per_t_nm,
        "numerator_g_per_h": fixed.numerator_g_per_h,
        "denominator_t_nm_per_h": fixed.denominator_t_nm_per_h,
    }

    # -- 2. defect materiality: two independent exposures -------------------
    cap, v = CASE["capacity_t"], CASE["v_ref_kn"]
    p_me, sfc = CASE["p_me_kw"], CASE["sfc_me_g_kwh"]
    me = p_me * sfc * CF_HFO

    rows = []
    for f_i in (1.0, 1.1, 1.2, 1.3, 1.4, 1.5):
        # (a) explicit weather factor, no innovative power
        new_a = attained_eedi(**CASE, cf_me=CF_HFO, f_i=f_i, f_w=1.10).eedi_g_co2_per_t_nm
        old_a = old_eedi(**CASE, cf_me=CF_HFO, f_i=f_i, f_w=1.10)
        # (b) default weather factor + 20 % innovative-power credit
        p_eff = 0.20 * p_me                   # 20 % of main-engine CO2 / h
        new_b = attained_eedi(**CASE, cf_me=CF_HFO, f_i=f_i, p_eff_kw=p_eff,
                               sfc_eff_g_kwh=sfc).eedi_g_co2_per_t_nm
        old_b = old_eedi(**CASE, cf_me=CF_HFO, f_i=f_i, p_eff_kw=p_eff,
                         sfc_eff_g_kwh=sfc)
        rows.append({
            "f_i": f_i,
            "exposure_a_explicit_f_w": {"new": new_a, "old": old_a,
                                        "err_pct": rel_err(old_a, new_a)},
            "exposure_b_default_f_w_eff20": {"new": new_b, "old": old_b,
                                             "err_pct": rel_err(old_b, new_b)},
        })
    ev["defect_materiality"] = rows

    # -- 3. cross-implementation spread -------------------------------------
    cii_val = cii_mod.attained_eedi(p_me, sfc, "heavy_fuel_oil", cap, v,
                                    p_ae_kw=CASE["p_ae_kw"],
                                    sfoc_ae_g_kwh=CASE["sfc_ae_g_kwh"])
    ev["cross_impl"] = {
        "propulsion_fixed_cf_hfo": fixed.eedi_g_co2_per_t_nm,
        "polar_cii_path": cii_val,
        "rel_diff_pct_cf_hfo": rel_err(fixed.eedi_g_co2_per_t_nm, cii_val),
        "rel_diff_pct_legacy_cf": rel_err(
            attained_eedi(**CASE, cf_me=CF_LEGACY).eedi_g_co2_per_t_nm, cii_val),
        "old_form_legacy_cf": old_eedi(**CASE, cf_me=CF_LEGACY),
    }

    # -- 4. reference line / phase table ------------------------------------
    types = {"bulk_carrier": [35_000, 80_000, 180_000],
             "tanker": [50_000, 150_000],
             "container_ship": [120_000, 200_000],
             "general_cargo": [10_000, 25_000]}
    ref = {}
    for cat, dwts in types.items():
        ref[cat] = {str(d): {
            "eedi_reference": cii_mod.eedi_reference_value(cat, float(d)),
            "required_phase1": cii_mod.required_eedi(cat, float(d), 1)["required_eedi"],
            "required_phase2": cii_mod.required_eedi(cat, float(d), 2)["required_eedi"],
            "required_phase3": cii_mod.required_eedi(cat, float(d), 3)["required_eedi"],
        } for d in dwts}
    ev["eedi_reference_lines"] = ref

    # -- 5. EEXI attained / required / compliance ---------------------------
    eexi_att = cii_mod.attained_eexi(p_me, sfc, "heavy_fuel_oil", cap, v,
                                     p_ae_kw=CASE["p_ae_kw"],
                                     sfoc_ae_g_kwh=CASE["sfc_ae_g_kwh"])
    eexi_rows = []
    for r in (0.0, 10.0, 15.0, 20.0, 25.0, 30.0, 35.0, 40.0):
        st = cii_mod.eexi_status(p_me, sfc, "heavy_fuel_oil", cap, v, r,
                                 category="bulk_carrier",
                                 p_ae_kw=CASE["p_ae_kw"],
                                 sfoc_ae_g_kwh=CASE["sfc_ae_g_kwh"])
        eexi_rows.append({"reduction_pct": r, "required_eexi": st["required_eexi"],
                          "margin_pct": st["margin_pct"],
                          "compliant": st["compliant"]})
    ev["eexi"] = {"attained": eexi_att,
                  "attained_over_eedi_me_only": cii_mod.attained_eexi(
                      p_me, sfc, "heavy_fuel_oil", cap, v)
                  / cii_mod.attained_eedi(p_me, sfc, "heavy_fuel_oil", cap, v),
                  "rows": eexi_rows}

    # ---------------------------------------------------------------- figures
    # fig1: defect materiality vs f_i
    fig, ax = plt.subplots(figsize=(7.0, 4.4))
    fi = [r["f_i"] for r in rows]
    ax.plot(fi, [r["exposure_a_explicit_f_w"]["err_pct"] for r in rows], "o-",
            label="(a) explicit $f_w$=1.10, no E$_\\mathrm{eff}$")
    ax.plot(fi, [r["exposure_b_default_f_w_eff20"]["err_pct"] for r in rows], "s-",
            label="(b) default $f_w$ + 20 % innovative credit")
    ax.axhline(3.0, color="crimson", ls="--", lw=1.0, label="3 % acceptance line")
    ax.axhline(-3.0, color="crimson", ls="--", lw=1.0)
    ax.set_xlabel("ice-class capacity correction $f_i$ [-]")
    ax.set_ylabel("pre-fix EEDI error [%]")
    ax.set_title("EEDI defect materiality (m1693): old vs guideline formula")
    ax.grid(alpha=0.3)
    ax.legend(fontsize=8)
    fig.tight_layout()
    fig.savefig(os.path.join(OUT, "fig1_defect_materiality.png"), dpi=160)
    plt.close(fig)

    # fig2: reference line + phase requirements (bulk carriers) + case ship
    dwt = [x for x in range(20_000, 220_001, 2_000)]
    refv = [cii_mod.eedi_reference_value("bulk_carrier", float(d)) for d in dwt]
    fig, ax = plt.subplots(figsize=(7.0, 4.4))
    ax.plot(dwt, refv, "k-", label="EEDI reference line (MEPC.203(62))")
    for phase, style in ((1, ":"), (2, "--"), (3, "-.")):
        ax.plot(dwt, [cii_mod.required_eedi("bulk_carrier", float(d), phase)["required_eedi"]
                      for d in dwt], style,
                label=f"required EEDI, phase {phase}")
    ax.plot([cap], [fixed.eedi_g_co2_per_t_nm], "r*", ms=14,
            label=f"case ship attained = {fixed.eedi_g_co2_per_t_nm:.3f}")
    ax.set_xlabel("capacity DWT [t]")
    ax.set_ylabel("g CO$_2$ / (t$\\cdot$nm)")
    ax.set_title("EEDI reference line, phase requirements, case ship (m1693)")
    ax.grid(alpha=0.3)
    ax.legend(fontsize=8)
    fig.tight_layout()
    fig.savefig(os.path.join(OUT, "fig2_eedi_reference_lines.png"), dpi=160)
    plt.close(fig)

    # fig3: EEXI compliance boundary
    red = [r["reduction_pct"] for r in eexi_rows]
    fig, ax = plt.subplots(figsize=(7.0, 4.4))
    ax.plot(red, [r["required_eexi"] for r in eexi_rows], "o-",
            label="required EEXI vs reduction factor")
    ax.axhline(eexi_att, color="tab:red", ls="--",
               label=f"attained EEXI = {eexi_att:.3f}")
    ax.fill_between(red, [r["required_eexi"] for r in eexi_rows], eexi_att,
                    where=[r["required_eexi"] >= eexi_att for r in eexi_rows],
                    alpha=0.15, color="tab:green", label="compliant region")
    ax.set_xlabel("ship-type reduction factor [%]  (MEPC.328(76) table input)")
    ax.set_ylabel("g CO$_2$ / (t$\\cdot$nm)")
    ax.set_title("EEXI attained vs required (bulk carrier 80 000 DWT, m1693)")
    ax.grid(alpha=0.3)
    ax.legend(fontsize=8)
    fig.tight_layout()
    fig.savefig(os.path.join(OUT, "fig3_eexi_compliance.png"), dpi=160)
    plt.close(fig)

    path = os.path.join(OUT, "eedi_eexi_audit.json")
    with open(path, "w") as fh:
        json.dump(ev, fh, indent=2, ensure_ascii=False)
    print(json.dumps({"out": path,
                      "figures": sorted(x for x in os.listdir(OUT) if x.endswith(".png")),
                      "attained_eedi": fixed.eedi_g_co2_per_t_nm,
                      "attained_eexi": eexi_att}, indent=2))


if __name__ == "__main__":
    main()