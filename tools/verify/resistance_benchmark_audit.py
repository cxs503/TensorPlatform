#!/usr/bin/env python3
"""Audit the resistance benchmark dataset + cross-check Holtrop-Mennen 1982.

Purpose (verification debt)
---------------------------
``backend/data/benchmark_reference.json`` is the *only* external reference used
by ``POST /calculations/benchmark_validate`` to decide pass/fail on resistance
accuracy.  Before any accuracy statement is made against it, the dataset must
satisfy the two physics identities that define its own fields:

    (I1)  Fn      = V / sqrt(g L)
    (I2)  Rt      = 0.5 rho V^2 S Ct
    (I3)  Delta   = rho Cb L B T          (where displacement is stated)

An entry that violates its own identities cannot act as a baseline: any "error"
computed against it measures the dataset's bookkeeping error, not the solver's.

The script then predicts the same operating points with the published
Holtrop-Mennen 1982 regression (``backend/services/holtrop_mennen.py``) and
reports the deviation, together with the method-validity domain flags.

Outputs (all under artifacts/resistance_benchmark_audit/):
  audit.json      machine-readable per-case verdicts
  fig_identity.png        listed Ct vs identity-consistent Ct / Fn check
  fig_kvlcc2.png          KVLCC2: Holtrop curve vs dataset points vs Rt-derived Ct
  fig_components.png      Holtrop resistance breakdown for KVLCC2
  table.md        markdown table for the report

Usage: python3 tools/verify/resistance_benchmark_audit.py
"""
from __future__ import annotations

import json
import math
import os
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "backend"))

from services.holtrop_mennen import (  # noqa: E402
    NU_FRESHWATER, NU_SEAWATER, RHO_FRESHWATER, RHO_SEAWATER,
    holtrop_resistance, in_domain,
)

G = 9.81
BENCH = REPO / "backend" / "data" / "benchmark_reference.json"
OUT = REPO / "artifacts" / "resistance_benchmark_audit"

FN_TOL = 0.02      # 2 %  -- Fn is a definition, not a measurement
CT_TOL = 0.02      # 2 %  -- Ct <-> Rt is a definition
DISP_TOL = 0.05    # 5 %  -- displacement excludes appendages/allowances


def rel_err(calc: float, ref: float) -> float:
    return (calc - ref) / ref if ref else float("nan")


def audit_case(case: dict) -> dict:
    geo = case["geometry"]
    V = case["speed_ms"]
    L = geo["length_m"]
    B = geo.get("beam_m") or geo.get("diameter_m")
    T = geo.get("draft_m") or geo.get("diameter_m")
    cb = geo.get("cb")
    s_stated = geo.get("wetted_surface_m2")
    # model tests are run in fresh water, full-scale in sea water
    model_scale = L < 50.0
    rho = RHO_FRESHWATER if model_scale else RHO_SEAWATER
    nu = NU_FRESHWATER if model_scale else NU_SEAWATER

    out = {
        "case_id": case["case_id"],
        "ship_type": case["ship_type"],
        "speed_ms": V,
        "scale": "model" if model_scale else "full",
        "rho_used": rho,
        "identities": {},
    }

    # (I1) Froude number
    fn_calc = V / math.sqrt(G * L)
    fn_case = case.get("froude_number")
    out["fn_calc"] = fn_calc
    out["fn_listed"] = fn_case
    e_fn = rel_err(fn_calc, fn_case) if fn_case else float("nan")
    out["identities"]["I1_froude"] = {
        "listed": fn_case, "recomputed": fn_calc, "rel_err": e_fn,
        "pass": bool(abs(e_fn) <= FN_TOL),
    }

    # Holtrop prediction (primary particulars only; Cm/Cwp derived by the module)
    hol = None
    if B and T and cb:
        hol = holtrop_resistance(L, B, T, cb, V, rho=rho, nu=nu)
        out["holtrop"] = hol.as_dict()
        out["holtrop_domain_ok"] = in_domain(L, B, T, cb, fn_calc)
        out["wetted_surface_holtrop_m2"] = hol.wetted_surface_m2
        # independent wetted-surface regression (Denny-Mumford: S = 1.7 L T + nabla/T)
        nabla = cb * L * B * T
        s_dm = 1.7 * L * T + nabla / T
        out["wetted_surface_denny_mumford_m2"] = s_dm
        out["wetted_surface_cross_check"] = {
            "holtrop_m2": hol.wetted_surface_m2, "denny_mumford_m2": s_dm,
            "rel_diff": (hol.wetted_surface_m2 - s_dm) / s_dm,
            "pass_3pct": bool(abs((hol.wetted_surface_m2 - s_dm) / s_dm) <= 0.03),
        }
    s_use = s_stated or (hol.wetted_surface_m2 if hol else None)

    # (I2) Ct <-> Rt
    ct_case = case.get("experimental_ct")
    rt_case = case.get("experimental_rt_n")
    out["ct_listed"] = ct_case
    out["rt_listed"] = rt_case
    if ct_case and rt_case and s_use:
        ct_from_rt = rt_case / (0.5 * rho * V ** 2 * s_use)
        rt_from_ct = 0.5 * rho * V ** 2 * s_use * ct_case
        e_ct = rel_err(ct_from_rt, ct_case)
        out["identities"]["I2_ct_rt"] = {
            "s_used_m2": s_use,
            "s_source": "listed" if s_stated else "holtrop",
            "ct_from_rt": ct_from_rt, "rt_from_ct_n": rt_from_ct,
            "rel_err": e_ct, "pass": bool(abs(e_ct) <= CT_TOL),
        }
    else:
        out["identities"]["I2_ct_rt"] = {
            "pass": None,
            "reason": "Ct or Rt or S missing -> identity not testable",
            "s_used_m2": s_use,
        }
        if ct_case and rt_case and s_use:
            pass

    # (I3) displacement <-> Cb L B T
    disp = geo.get("displacement_t")
    if disp is None and geo.get("displacement_kg") is not None:
        disp = geo["displacement_kg"] / 1000.0
    if disp and cb and B and T:
        nabla = cb * L * B * T
        disp_calc = rho * nabla / 1000.0
        e_d = rel_err(disp_calc, disp)
        out["identities"]["I3_displacement"] = {
            "listed_t": disp, "recomputed_t": disp_calc,
            "rel_err": e_d, "pass": bool(abs(e_d) <= DISP_TOL),
        }
    else:
        out["identities"]["I3_displacement"] = {
            "pass": None, "reason": "displacement/Cb/B/T incomplete",
        }

    # Holtrop vs listed Ct (only meaningful where the listed Ct survives I2)
    if hol and ct_case:
        out["holtrop_vs_listed_ct"] = {
            "ct_holtrop": hol.ct,
            "rel_err": rel_err(hol.ct, ct_case),
            "method_domain_ok": out["holtrop_domain_ok"],
        }
    # Rt that the listed Ct implies (the physically consistent magnitude)
    if hol and ct_case:
        out["rt_implied_by_listed_ct_n"] = 0.5 * rho * V ** 2 * hol.wetted_surface_m2 * ct_case
        out["rt_listed_over_implied"] = (
            rt_case / out["rt_implied_by_listed_ct_n"] if rt_case else None)

    fails = [k for k, v in out["identities"].items() if v.get("pass") is False]
    untested = [k for k, v in out["identities"].items() if v.get("pass") is None]
    out["failed_identities"] = fails
    out["untested_identities"] = untested
    out["verdict"] = "REJECT_AS_BASELINE" if fails else (
        "USABLE_WITH_CAVEAT" if untested else "USABLE")
    return out


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    data = json.loads(BENCH.read_text())
    cases = [audit_case(c) for c in data["cases"]]

    rejected = [c["case_id"] for c in cases if c["verdict"] == "REJECT_AS_BASELINE"]
    summary = {
        "source_file": str(BENCH.relative_to(REPO)),
        "n_cases": len(cases),
        "n_rejected": len(rejected),
        "rejected_cases": rejected,
        "tolerances": {"fn": FN_TOL, "ct_rt": CT_TOL, "displacement": DISP_TOL},
        "verdict": ("DATASET_REJECTED_AS_EXTERNAL_BASELINE"
                    if rejected else "DATASET_CONSISTENT"),
        "cases": cases,
    }
    (OUT / "audit.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False))

    # ---- markdown table ---------------------------------------------------
    lines = ["| case | Fn listed | Fn recomp | I1 | Ct listed | Ct from Rt | I2 | "
             "Holtrop Ct | Holtrop vs listed | domain |",
             "|---|---|---|---|---|---|---|---|---|---|"]
    for c in cases:
        i1 = c["identities"]["I1_froude"]
        i2 = c["identities"]["I2_ct_rt"]
        ct_rt = i2.get("ct_from_rt")
        hv = c.get("holtrop_vs_listed_ct", {})
        lines.append(
            f"| {c['case_id']} | {c['fn_listed']:.3f} | {c['fn_calc']:.3f} | "
            f"{'PASS' if i1['pass'] else 'FAIL'} | "
            f"{(c.get('ct_listed') or 0):.5f} | "
            f"{('%.5f' % ct_rt) if ct_rt else 'n/a'} | "
            f"{'PASS' if i2.get('pass') else ('untested' if i2.get('pass') is None else 'FAIL')} | "
            f"{('%.5f' % hv['ct_holtrop']) if hv else 'n/a'} | "
            f"{('%+.1f%%' % (100 * hv['rel_err'])) if hv else 'n/a'} | "
            f"{'ok' if c.get('holtrop_domain_ok') else 'OUT'} |")
    (OUT / "table.md").write_text("\n".join(lines) + "\n")

    summary["wetted_surface_cross_checks"] = [
        {"case_id": c["case_id"], **c["wetted_surface_cross_check"]}
        for c in cases if c.get("wetted_surface_cross_check")]
    (OUT / "audit.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False))
    print(json.dumps({k: summary[k] for k in
                      ("n_cases", "n_rejected", "rejected_cases", "verdict")},
                     indent=2, ensure_ascii=False))
    for c in summary["wetted_surface_cross_checks"]:
        print("S cross-check %s: Holtrop=%.1f m2  Denny-Mumford=%.1f m2  diff=%+.2f%%  <3%%: %s"
              % (c["case_id"], c["holtrop_m2"], c["denny_mumford_m2"],
                 100 * c["rel_diff"], c["pass_3pct"]))
    print("\n".join(lines))
    _figures(cases)
    return 0


def _figures(cases: list) -> None:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    # --- figure 1: identity audit -----------------------------------------
    subs = [c for c in cases if c.get("ct_listed") and
            c["identities"]["I2_ct_rt"].get("ct_from_rt")]
    fig, axes = plt.subplots(1, 2, figsize=(11, 4.2))
    names = [c["case_id"] for c in subs]
    x = range(len(subs))
    axes[0].bar([i - 0.2 for i in x], [c["ct_listed"] for c in subs], 0.4,
                label="listed $C_T$")
    axes[0].bar([i + 0.2 for i in x], [c["identities"]["I2_ct_rt"]["ct_from_rt"]
                                       for c in subs], 0.4,
                label="$C_T$ from listed $R_T$")
    axes[0].set_xticks(list(x))
    axes[0].set_xticklabels(names, rotation=45, ha="right", fontsize=8)
    axes[0].set_ylabel("$C_T$ [-]")
    axes[0].set_title("(I2) $C_T\\leftrightarrow R_T$ identity")
    axes[0].legend()
    axes[0].grid(alpha=.3)

    fnerr = [100 * c["identities"]["I1_froude"]["rel_err"] for c in cases]
    axes[1].bar(range(len(cases)), fnerr, color="tab:red")
    axes[1].axhline(0, color="k", lw=.8)
    axes[1].axhline(2, color="gray", ls="--", lw=.8)
    axes[1].axhline(-2, color="gray", ls="--", lw=.8)
    axes[1].set_xticks(range(len(cases)))
    axes[1].set_xticklabels([c["case_id"] for c in cases], rotation=60,
                            ha="right", fontsize=7)
    axes[1].set_ylabel("$F_n$ listed vs $V/\\sqrt{gL}$  [%]")
    axes[1].set_title("(I1) Froude-number identity (tolerance $\\pm$2%)")
    axes[1].grid(alpha=.3)
    fig.tight_layout()
    fig.savefig(OUT / "fig_identity.png", dpi=140)
    plt.close(fig)

    # --- figure 2: KVLCC2 Ct(V) -------------------------------------------
    kv = [c for c in cases if c["ship_type"] == "KVLCC2" and c.get("holtrop")]
    if kv:
        fig, ax = plt.subplots(figsize=(7.2, 4.6))
        vs = [c["speed_ms"] for c in kv]
        ax.plot(vs, [c["ct_listed"] for c in kv], "o-",
                label="dataset 'experimental' $C_T$")
        ax.plot(vs, [c["rt_listed"] / (0.5 * c["rho_used"] * c["speed_ms"] ** 2
                                       * c["wetted_surface_holtrop_m2"]) for c in kv],
                "s--", label="$C_T$ implied by listed $R_T$ (identity-consistent)")
        vgrid = [min(vs) + (max(vs) - min(vs)) * i / 20 for i in range(21)]
        L, B, T, cb = 320.0, 58.0, 20.8, 0.8098
        ax.plot(vgrid, [holtrop_resistance(L, B, T, cb, v).ct for v in vgrid],
                "-", lw=2, label="Holtrop–Mennen 1982 (full scale, seawater)")
        ax.set_xlabel("ship speed $V$ [m/s]")
        ax.set_ylabel("$C_T$ [-]")
        ax.set_title("KVLCC2: dataset vs identity-consistent vs Holtrop–Mennen")
        ax.legend(fontsize=8)
        ax.grid(alpha=.3)
        fig.tight_layout()
        fig.savefig(OUT / "fig_kvlcc2.png", dpi=140)
        plt.close(fig)

        # --- figure 3: component breakdown --------------------------------
        rows = []
        for v in vgrid:
            r = holtrop_resistance(L, B, T, cb, v)
            rows.append((v, r.rf_n, r.rw_n, r.ra_n, r.rt_n))
        fig, ax = plt.subplots(figsize=(7.2, 4.6))
        vv = [r[0] for r in rows]
        ax.stackplot(vv,
                     [r[1] / 1e6 for r in rows],
                     [r[2] / 1e6 for r in rows],
                     [r[3] / 1e6 for r in rows],
                     labels=["$R_F(1+k)$ ITTC-57 + form factor",
                             "$R_W$ wave (1982 regression)",
                             "$R_A$ correlation allowance"])
        ax.plot(vv, [r[4] / 1e6 for r in rows], "k--", lw=1.2, label="$R_T$ total")
        ax.set_xlabel("ship speed $V$ [m/s]")
        ax.set_ylabel("resistance [MN]")
        ax.set_title("KVLCC2 full-scale resistance breakdown (Holtrop–Mennen)")
        ax.legend(fontsize=8, loc="upper left")
        ax.grid(alpha=.3)
        fig.tight_layout()
        fig.savefig(OUT / "fig_components.png", dpi=140)
        plt.close(fig)

    # --- figure 4: dataset consistency ratio ------------------------------
    ratio = [(c["case_id"], c["rt_listed_over_implied"])
             for c in cases if c.get("rt_listed_over_implied")]
    if ratio:
        fig, ax = plt.subplots(figsize=(7.4, 4.2))
        ax.bar([r[0] for r in ratio], [r[1] for r in ratio], color="tab:orange")
        ax.axhline(1.0, color="k", ls="--", lw=1)
        ax.set_ylabel("listed $R_T$ / $R_T$ implied by listed $C_T$")
        ax.set_title("Dataset bookkeeping inconsistency factor")
        ax.set_xticks(range(len(ratio)))
        ax.set_xticklabels([r[0] for r in ratio], rotation=60, ha="right", fontsize=7)
        ax.grid(alpha=.3)
        fig.tight_layout()
        fig.savefig(OUT / "fig_inconsistency.png", dpi=140)
        plt.close(fig)


if __name__ == "__main__":
    sys.exit(main())