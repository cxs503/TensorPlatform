#!/usr/bin/env python3
"""m1643 — independent verification of the CII reference lines (IMO G1).

Why this script exists
----------------------
``backend/services/polar/cii.py: verify_cii_ship_types`` was the only "CII vs
guideline" harness in the repo.  It re-checks the reduction-factor schedule Z and
the rating-boundary multipliers d1..d4, and it cross-checks the mass path against
the energy path -- but both of those are *self-consistency* checks.  The single
most consequential table of the whole regulation, the **G1 reference line
coefficients (a, c)** in ``constants.CII_REFERENCE_LINES``, was never compared to
an external authority: its only guard was that the same numbers are reproduced by
the same numbers.

This script supplies the missing external anchor: the G1 table transcribed from

    IMO Resolution MEPC.353(78) (2022 Guidelines on the reference lines for use
    with the operational carbon intensity rating of ships, G1), Annex, Table 1:
        CII_ref = a * capacity^(-c)

plus three coefficient-free invariants that hold for *any* correct rendition of
that table (magnitude band, monotone decrease in capacity, cross-type ordering).
The invariants alone are enough to reject the pre-fix value of ``general_cargo``;
the external table supplies the replacement value.

Outputs (all numbers are injected programmatically, never hand-copied):
    artifacts/cii_m1643/cii_reference_verification.json
    artifacts/cii_m1643/fig1_cii_reference_lines.png
    artifacts/cii_m1643/fig2_relative_error.png
    artifacts/cii_m1643/fig3_rating_general_cargo.png
"""
from __future__ import annotations

import json
import math
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "backend"))

from services.polar import cii as C  # noqa: E402
from services.polar import constants as K  # noqa: E402

OUT = REPO / "artifacts" / "cii_m1643"
OUT.mkdir(parents=True, exist_ok=True)

# ---------------------------------------------------------------------------
# 1. External reference: MEPC.353(78) G1, Annex Table 1 (a, c) -- c stored with
#    the sign of  CII_ref = a * capacity^(-c), i.e. the guideline's positive c.
# ---------------------------------------------------------------------------
PUBLISHED_G1 = {
    #  category              a            c      capacity basis
    "bulk_carrier":        (4745.0,      0.622, "DWT"),
    "gas_carrier_large":   (14405.0e7,   2.071, "DWT"),   # guideline prints 14405E7
    "gas_carrier_small":   (8104.0,      0.639, "DWT"),
    "tanker":              (5247.0,      0.610, "DWT"),
    "container_ship":      (1984.0,      0.489, "DWT"),
    "general_cargo":       (31948.0,     0.792, "DWT"),
    "refrigerated_cargo":  (4600.0,      0.557, "DWT"),
    "combination_carrier": (5119.0,      0.622, "DWT"),
}

#: G1 categories the platform does not implement (never guessed -- see report §5).
PUBLISHED_G1_UNIMPLEMENTED = {
    "lng_carrier",
    "ro_ro_cargo_vehicle_carrier",
    "ro_ro_cargo",
    "ro_ro_passenger",
    "cruise_passenger",
}

#: Pre-fix value that this mission rejects (kept only to quantify the defect).
PRE_FIX_GENERAL_CARGO_C = -1.430

ANCHOR_DWT = {
    "bulk_carrier": (20000.0, 50000.0, 200000.0),
    "gas_carrier_large": (70000.0, 100000.0, 200000.0),
    "gas_carrier_small": (10000.0, 30000.0, 60000.0),
    "tanker": (20000.0, 100000.0, 300000.0),
    "container_ship": (20000.0, 50000.0, 200000.0),
    "general_cargo": (5000.0, 20000.0, 50000.0),
    "refrigerated_cargo": (5000.0, 10000.0, 20000.0),
    "combination_carrier": (20000.0, 50000.0, 150000.0),
}

#: Physical band for a CII reference line [g CO2 / (capacity * nm)].  Published
#: fleet attained-CII values sit roughly in 2..60 for the ship types in G1; a
#: reference line outside this band cannot be the guideline's.
CII_REF_BAND = (1.0, 100.0)

#: Empirical cross-type ordering at equal capacity: a general cargo ship and a
#: refrigerated cargo carrier are *less* efficient (higher CII_ref) than a bulk
#: carrier of the same size.  Rejecting a table that violates this needs no
#: external data at all.
ORDERING_REF = "bulk_carrier"
ORDERING_HIGHER = ("general_cargo", "refrigerated_cargo")

#: Representative general cargo voyage, used to show the consequence of the defect
#: (inputs only -- SFOC/power/speed are the same magnitudes as ``polar.ships``).
GC_VOYAGE = dict(
    dwt=20000.0,
    brake_power_kw=4200.0,
    sfoc_g_kwh=195.0,
    design_speed_kn=14.0,
    distance_nm=3000.0,
    me_load=0.85,
    aux_load=0.05,
    fuel_type="heavy_fuel_oil",
    year=2026,
)


def _rel(a: float, b: float) -> float:
    return abs(a - b) / abs(b) * 100.0


# ---------------------------------------------------------------------------
# 2. Table comparison
# ---------------------------------------------------------------------------
def compare_tables() -> dict:
    rows = []
    for cat, (a_ref, c_ref, basis) in PUBLISHED_G1.items():
        got = K.CII_REFERENCE_LINES.get(cat)
        row = {
            "category": cat,
            "capacity_basis": basis,
            "a_published": a_ref,
            "c_published": c_ref,
            "present": got is not None,
        }
        if got is not None:
            row.update(
                a_platform=got["a"],
                c_platform=-got["c"],          # platform stores the signed exponent
                a_rel_err_pct=round(_rel(got["a"], a_ref), 9),
                c_rel_err_pct=round(_rel(-got["c"], c_ref), 9),
            )
        rows.append(row)

    anchors = []
    for cat, dwts in ANCHOR_DWT.items():
        a_ref, c_ref, _ = PUBLISHED_G1[cat]
        for dwt in dwts:
            ref = a_ref * dwt ** (-c_ref)
            plat = C.cii_reference_value(cat, dwt)
            anchors.append(
                {
                    "category": cat,
                    "capacity_dwt": dwt,
                    "cii_ref_published": round(ref, 6),
                    "cii_ref_platform": round(plat, 6),
                    "rel_err_pct": round(_rel(plat, ref), 9),
                    "in_band": CII_REF_BAND[0] <= plat <= CII_REF_BAND[1],
                }
            )

    extra = sorted(set(K.CII_REFERENCE_LINES) - set(PUBLISHED_G1))
    missing = sorted(set(PUBLISHED_G1) - set(K.CII_REFERENCE_LINES))
    return {
        "source": "IMO MEPC.353(78) G1 Annex Table 1, CII_ref = a * capacity^(-c)",
        "rows": rows,
        "max_a_rel_err_pct": max((r.get("a_rel_err_pct", 1e9) for r in rows)),
        "max_c_rel_err_pct": max((r.get("c_rel_err_pct", 1e9) for r in rows)),
        "max_total_rel_err_pct": max(a["rel_err_pct"] for a in anchors),
        "anchors": anchors,
        "platform_only_categories": extra,
        "published_only_categories": missing,
        "guideline_categories_not_implemented": sorted(PUBLISHED_G1_UNIMPLEMENTED),
    }


def pre_fix_comparison() -> dict:
    """Quantify the defect: general cargo with the shipped exponent -1.430."""
    a = K.CII_REFERENCE_LINES["general_cargo"]["a"]
    out = []
    for dwt in ANCHOR_DWT["general_cargo"]:
        a_ref, c_ref, _ = PUBLISHED_G1["general_cargo"]
        ref = a_ref * dwt ** (-c_ref)
        bad = a * dwt ** PRE_FIX_GENERAL_CARGO_C
        out.append(
            {
                "capacity_dwt": dwt,
                "cii_ref_published": round(ref, 6),
                "cii_ref_pre_fix": bad,
                "rel_err_pct": round(_rel(bad, ref), 6),
                "factor_low": round(ref / bad, 3),
            }
        )
    return {
        "shipped_exponent_c": PRE_FIX_GENERAL_CARGO_C,
        "published_exponent_c": PUBLISHED_G1["general_cargo"][1],
        "rows": out,
    }


# ---------------------------------------------------------------------------
# 3. Coefficient-free invariants (would have caught the defect with no document)
# ---------------------------------------------------------------------------
def invariants() -> dict:
    checks = []

    # (i) magnitude band
    for cat in PUBLISHED_G1:
        for dwt in ANCHOR_DWT[cat]:
            val = C.cii_reference_value(cat, dwt)
            lo, hi = CII_REF_BAND
            checks.append(
                {
                    "check": f"band[{cat}@{dwt:g}]",
                    "value": round(val, 6),
                    "criterion": f"{lo} <= CII_ref <= {hi}",
                    "pass": lo <= val <= hi,
                }
            )

    # (ii) monotone decreasing in capacity (exponent must be positive)
    for cat in PUBLISHED_G1:
        dwts = sorted(ANCHOR_DWT[cat])
        vals = [C.cii_reference_value(cat, d) for d in dwts]
        checks.append(
            {
                "check": f"monotone[{cat}]",
                "value": [round(v, 6) for v in vals],
                "criterion": "strictly decreasing in capacity",
                "pass": all(x > y for x, y in zip(vals, vals[1:])),
            }
        )

    # (iii) cross-type ordering at equal capacity
    for cat in ORDERING_HIGHER:
        for dwt in (20000.0, 50000.0):
            hi_val = C.cii_reference_value(cat, dwt)
            ref_val = C.cii_reference_value(ORDERING_REF, dwt)
            checks.append(
                {
                    "check": f"order[{cat}>bulk_carrier@{dwt:g}]",
                    "value": {"category": round(hi_val, 6), "bulk": round(ref_val, 6)},
                    "criterion": "CII_ref(general/reefer) > CII_ref(bulk) at equal DWT",
                    "pass": hi_val > ref_val,
                }
            )

    failed = [c for c in checks if not c["pass"]]
    return {
        "n_checks": len(checks),
        "n_failed": len(failed),
        "failed": failed,
        "pass": not failed,
        "checks": checks,
    }


# ---------------------------------------------------------------------------
# 4. Consequence on the rating of a representative general cargo voyage
# ---------------------------------------------------------------------------
def rating_consequence() -> dict:
    v = GC_VOYAGE
    hours = v["distance_nm"] / v["design_speed_kn"]
    p_me = v["me_load"] * v["brake_power_kw"]
    p_ae = v["aux_load"] * v["brake_power_kw"]
    fuel_t = (p_me + p_ae) * v["sfoc_g_kwh"] * hours / 1.0e6
    energy_mj = (p_me + p_ae) * hours * C.MJ_PER_KWH
    voy = C.VoyageEfficiency(
        fuel_t=fuel_t,
        fuel_type=v["fuel_type"],
        distance_nm=v["distance_nm"],
        capacity=v["dwt"],
        category="general_cargo",
        brake_energy_mj=energy_mj,
        sfoc_g_kwh=v["sfoc_g_kwh"],
    )
    now = voy.as_dict(year=v["year"])

    a_ref, c_ref, _ = PUBLISHED_G1["general_cargo"]
    cii_ref_pub = a_ref * v["dwt"] ** (-c_ref)
    z = C.CII_REDUCTION_FACTOR[v["year"]]
    required_pub = cii_ref_pub * (1.0 - z / 100.0)
    rating_pub = C.cii_rating(now["attained_cii"], required_pub)

    a_bad = K.CII_REFERENCE_LINES["general_cargo"]["a"]
    cii_ref_bad = a_bad * v["dwt"] ** PRE_FIX_GENERAL_CARGO_C
    required_bad = cii_ref_bad * (1.0 - z / 100.0)
    rating_bad = C.cii_rating(now["attained_cii"], required_bad)

    return {
        "voyage_inputs": v,
        "fuel_t": round(fuel_t, 6),
        "attained_cii": round(now["attained_cii"], 6),
        "attained_cii_energy_path": round(now.get("attained_cii_energy", float("nan")), 6),
        "two_path_rel_diff_pct": now.get("path_rel_diff_pct", float("nan")),
        "cii_ref_published": round(cii_ref_pub, 6),
        "required_cii_published": round(required_pub, 6),
        "rating_after_fix": rating_pub,
        "cii_ref_pre_fix": cii_ref_bad,
        "required_cii_pre_fix": required_bad,
        "rating_pre_fix": rating_bad,
    }


# ---------------------------------------------------------------------------
# 5. Figures
# ---------------------------------------------------------------------------
def figures(res: dict, pre: dict, rating: dict) -> list:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    made = []
    cats = list(PUBLISHED_G1)
    colours = plt.cm.tab10(range(len(cats)))

    # fig 1 -- published vs platform CII_ref over DWT (log-log)
    fig, ax = plt.subplots(figsize=(9, 5.6))
    dwts = [1e4, 2e4, 3e4, 5e4, 7e4, 1e5, 1.5e5, 2e5, 3e5]
    for cat, col in zip(cats, colours):
        a, c, _ = PUBLISHED_G1[cat]
        ax.plot(dwts, [a * d ** (-c) for d in dwts], "-", color=col, lw=1.8,
                label=f"{cat} (published)")
        pts = [C.cii_reference_value(cat, d) for d in dwts]
        ax.plot(dwts, pts, "o", color=col, ms=4.5, mfc="none")
    # the rejected pre-fix general cargo line
    a_bad = K.CII_REFERENCE_LINES["general_cargo"]["a"]
    ax.plot(dwts, [a_bad * d ** PRE_FIX_GENERAL_CARGO_C for d in dwts], "--",
            color="crimson", lw=2.2, label="general_cargo pre-fix c=-1.430 (rejected)")
    ax.set_xscale("log")
    ax.set_yscale("log")
    ax.set_xlabel("capacity (DWT)")
    ax.set_ylabel("CII_ref  [g CO2 / (capacity · nm)]")
    ax.set_title("G1 reference lines: published table (lines) vs platform (circles)")
    ax.grid(True, which="both", alpha=0.3)
    ax.legend(fontsize=7.5, ncol=2)
    p = OUT / "fig1_cii_reference_lines.png"
    fig.tight_layout()
    fig.savefig(p, dpi=150)
    plt.close(fig)
    made.append(p)

    # fig 2 -- per-type relative error of the platform constants, pre vs post fix
    fig, ax = plt.subplots(figsize=(9, 4.6))
    labels = [r["category"] for r in res["rows"]]
    post = [r.get("c_rel_err_pct", float("nan")) for r in res["rows"]]
    pre_err = []
    pre_rows = {r["capacity_dwt"]: r["rel_err_pct"] for r in pre["rows"]}
    for cat in labels:
        pre_err.append(pre_rows[20000.0] if cat == "general_cargo" else 0.0)
    x = range(len(labels))
    ax.bar([i - 0.2 for i in x], pre_err, width=0.4, label="pre-fix", color="crimson")
    ax.bar([i + 0.2 for i in x], post, width=0.4, label="post-fix",
           color="seagreen")
    ax.set_xticks(list(x))
    ax.set_xticklabels(labels, rotation=30, ha="right", fontsize=8)
    ax.set_ylabel("relative error in exponent c vs G1 [%]")
    ax.set_yscale("symlog", linthresh=1e-6)
    ax.set_title("CII reference-line exponent vs MEPC.353(78) G1 (DWT = 20 000)")
    ax.grid(True, axis="y", alpha=0.3)
    ax.legend()
    p = OUT / "fig2_relative_error.png"
    fig.tight_layout()
    fig.savefig(p, dpi=150)
    plt.close(fig)
    made.append(p)

    # fig 3 -- rating bands + where the voyage lands, before/after
    fig, ax = plt.subplots(figsize=(9, 4.4))
    b = C.CII_RATING_BOUNDARIES
    bands = [(0.0, b["d1"], "A", "#2e7d32"), (b["d1"], b["d2"], "B", "#7cb342"),
             (b["d2"], b["d3"], "C", "#fdd835"), (b["d3"], b["d4"], "D", "#fb8c00"),
             (b["d4"], 4.0, "E", "#c62828")]
    for lo, hi, g, col in bands:
        ax.axvspan(lo, hi, color=col, alpha=0.35)
        ax.text((lo + min(hi, 3.9)) / 2, 0.62, g, ha="center", fontsize=12, weight="bold")
    r_bad = rating["rating_pre_fix"]["attained_over_required"]
    r_new = rating["rating_after_fix"]["attained_over_required"]
    ax.axvline(min(r_bad, 3.99), color="crimson", lw=2.4,
               label=f"pre-fix ratio = {r_bad:.1f} -> {rating['rating_pre_fix']['rating']}")
    ax.axvline(r_new, color="navy", lw=2.4,
               label=f"post-fix ratio = {r_new:.3f} -> {rating['rating_after_fix']['rating']}")
    ax.set_xlim(0, 4.0)
    ax.set_ylim(0, 1.0)
    ax.set_yticks([])
    ax.set_xlabel("attained CII / required CII")
    ax.set_title("20 000 DWT general cargo voyage (3000 nm @ 14 kn, 85 % MCR): "
                 "rating position before/after the fix")
    ax.legend(loc="upper right", fontsize=8.5)
    p = OUT / "fig3_rating_general_cargo.png"
    fig.tight_layout()
    fig.savefig(p, dpi=150)
    plt.close(fig)
    made.append(p)
    return made


def main() -> int:
    res = compare_tables()
    pre = pre_fix_comparison()
    inv = invariants()
    rating = rating_consequence()
    figs = figures(res, pre, rating)

    verdict = {
        "mission": "m1643",
        "artifact": "CII reference-line independent verification",
        "source": res["source"],
        "table_comparison": res,
        "pre_fix_defect": pre,
        "invariants": inv,
        "rating_consequence": rating,
        "figures": [str(f.relative_to(REPO)) for f in figs],
        "verdict": {
            "max_rel_err_vs_published_pct": res["max_total_rel_err_pct"],
            "invariants_pass": inv["pass"],
            "n_invariants": inv["n_checks"],
            "threshold_pct": 3.0,
            "pass": res["max_total_rel_err_pct"] < 3.0 and inv["pass"],
        },
    }
    out = OUT / "cii_reference_verification.json"
    out.write_text(json.dumps(verdict, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(verdict["verdict"], indent=2))
    print("max a err %%: %.3e   max c err %%: %.3e"
          % (res["max_a_rel_err_pct"], res["max_c_rel_err_pct"]))
    print("pre-fix general cargo rel err @20k DWT: %.3f %% (factor %.0fx low)"
          % (pre["rows"][1]["rel_err_pct"], pre["rows"][1]["factor_low"]))
    print("rating pre-fix %s (ratio %.1f) -> post-fix %s (ratio %.3f)"
          % (rating["rating_pre_fix"]["rating"],
             rating["rating_pre_fix"]["attained_over_required"],
             rating["rating_after_fix"]["rating"],
             rating["rating_after_fix"]["attained_over_required"]))
    print("artifact:", out.relative_to(REPO))
    return 0 if verdict["verdict"]["pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())