"""生成复核报告图（matplotlib Agg，输出 outputs/*.png）。"""
from __future__ import annotations

import json
import os
import sys

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "backend"))

from services import resistance_holtrop as engine           # noqa: E402
from tools.validate import legacy_resistance as legacy      # noqa: E402
from tools.validate import reference_corpus as corpus_mod   # noqa: E402

OUT = os.path.join(ROOT, "outputs")
os.makedirs(OUT, exist_ok=True)

KVL = dict(L=320.0, B=58.0, T=20.8, cb=0.8098, disp_t=310000.0,
           i_e_deg=25.0, lcb_pct=1.5)
# 部署态 8001 实测点（curl 回归，2026-11-01）
LIVE = {13.0: 3992.51, 15.0: 5512.42}


def fig1_rt_vs_speed():
    kn = np.arange(11.0, 17.01, 0.5)
    rt_new, rt_lo, rt_hi, pe_new = [], [], [], []
    for v in kn:
        inp = engine.ResistanceInput(length=KVL["L"], beam=KVL["B"], draft=KVL["T"],
                                     cb=KVL["cb"], speed_ms=v * 0.5144,
                                     displacement_t=KVL["disp_t"],
                                     i_e_deg=KVL["i_e_deg"], lcb_pct=KVL["lcb_pct"])
        r = engine.compute_resistance(inp)
        b = engine.sensitivity_band(inp)
        rt_new.append(r["rt"] / 1e3)
        rt_lo.append(b["rt_min"] / 1e3)
        rt_hi.append(b["rt_max"] / 1e3)
        pe_new.append(r["pe"] / 1e6)
    rt_old = [legacy.legacy_deployed(KVL["L"], KVL["B"], KVL["T"], KVL["cb"], v)["rt"] / 1e3
              for v in kn]

    fig, ax = plt.subplots(1, 2, figsize=(11, 4.2))
    ax[0].fill_between(kn, rt_lo, rt_hi, color="tab:blue", alpha=0.18,
                       label="Holtrop-1984 band (i_E 10–40°, lcb ±2%L, k_s 75–300µm)")
    ax[0].plot(kn, rt_new, "b-o", ms=3, label="fixed engine (this commit)")
    ax[0].plot(kn, rt_old, "r--s", ms=4, label="legacy deployed (fudge Cr)")
    lk = sorted(LIVE)
    ax[0].plot(lk, [LIVE[k] for k in lk], "kx", ms=9, mew=2,
               label="live :8001 regression (13/15 kn)")
    ax[0].set_xlabel("ship speed V [kn]"); ax[0].set_ylabel("R_T [kN]")
    ax[0].set_title("KVLCC2 resistance: fixed engine vs legacy deployed")
    ax[0].grid(alpha=.3); ax[0].legend(fontsize=7)

    ax[1].plot(kn, pe_new, "b-o", ms=3, label="fixed engine P_E")
    ax[1].plot(kn, [legacy.legacy_deployed(KVL["L"], KVL["B"], KVL["T"], KVL["cb"], v)["pe"] / 1e6
                     for v in kn], "r--s", ms=4, label="legacy deployed P_E")
    ax[1].axhspan(20, 30, color="gray", alpha=0.25,
                  label="VLCC-class 2-stroke MCR order (20-30 MW, order-of-magnitude check)")
    ax[1].set_xlabel("ship speed V [kn]"); ax[1].set_ylabel("effective power P_E [MW]")
    ax[1].set_title("Effective power vs engineer-order MCR envelope")
    ax[1].grid(alpha=.3); ax[1].legend(fontsize=7)
    fig.tight_layout()
    p = os.path.join(OUT, "refval_fig1_rt_vs_speed.png")
    fig.savefig(p, dpi=140); plt.close(fig)
    return p


def fig2_deviation():
    verdict = corpus_mod.verify(persist=True)
    hulls = [r for r in verdict["results"] if r["class"] == "cross_method"]
    labels = [r["case_id"].replace("_", "\n") for r in hulls]
    x = np.arange(len(hulls))
    fig, ax = plt.subplots(1, 2, figsize=(11.5, 4.3))
    ax[0].bar(x - 0.22, [r["legacy_deployed_err_pct"] for r in hulls], 0.42,
              color="tab:red", label="legacy deployed (Cr fudge)")
    ax[0].bar(x + 0.22, [r["err_pct"] for r in hulls], 0.42,
              color="tab:blue", label="fixed engine vs corpus")
    ax[0].axhline(15, ls="--", c="k", lw=1, label="15% acceptance limit")
    ax[0].set_xticks(x); ax[0].set_xticklabels(labels, fontsize=7)
    ax[0].set_ylabel("R_T deviation from reference [%]")
    ax[0].set_title("Per-case deviation (log scale)"); ax[0].set_yscale("log")
    ax[0].legend(fontsize=7); ax[0].grid(alpha=.3, axis="y")

    ax[1].bar(x - 0.22, [r["legacy_deployed_rt_n"] / 1e3 for r in hulls], 0.42,
              color="tab:red", label="legacy deployed")
    ax[1].bar(x + 0.22, [r["reference_rt_n"] / 1e3 for r in hulls], 0.42,
              color="tab:blue", label="Holtrop-1984 reference")
    ax[1].set_xticks(x); ax[1].set_xticklabels(labels, fontsize=7)
    ax[1].set_ylabel("R_T [kN]"); ax[1].set_title("Absolute R_T: legacy vs reference")
    ax[1].legend(fontsize=7); ax[1].grid(alpha=.3, axis="y")
    fig.tight_layout()
    p = os.path.join(OUT, "refval_fig2_deviation.png")
    fig.savefig(p, dpi=140); plt.close(fig)

    with open(os.path.join(OUT, "figures_index.json"), "w") as f:
        json.dump({"verdict_summary": verdict["summary"]}, f, indent=2)
    return p, verdict


def fig3_components():
    cases = corpus_mod.CASE_HULLS
    names, rf, rw, ra, cr_old, cr_new = [], [], [], [], [], []
    for s in cases:
        inp = engine.ResistanceInput(length=s["L"], beam=s["B"], draft=s["T"],
                                     cb=s["cb"], speed_ms=s["Vs_ms"],
                                     displacement_t=s["disp_t"],
                                     i_e_deg=s["i_e_deg"], lcb_pct=s["lcb_pct"])
        r = engine.compute_resistance(inp)
        ld = legacy.legacy_deployed(s["L"], s["B"], s["T"], s["cb"], s["Vs_ms"] / 0.5144)
        names.append(s["case_id"]); rf.append(r["rf"] / 1e3); rw.append(r["rr"] / 1e3)
        ra.append(r["ra"] / 1e3); cr_old.append(ld["cr"]); cr_new.append(r["cr"])
    x = np.arange(len(names))
    fig, ax = plt.subplots(1, 2, figsize=(11.5, 4.3))
    ax[0].bar(x, rf, 0.6, label="R_F(1+k1) viscous+form")
    ax[0].bar(x, rw, 0.6, bottom=rf, label="R_W wave")
    ax[0].bar(x, ra, 0.6, bottom=np.array(rf) + np.array(rw), label="R_A correlation")
    ax[0].set_xticks(x); ax[0].set_xticklabels([n.replace("_", "\n") for n in names], fontsize=7)
    ax[0].set_ylabel("resistance [kN]"); ax[0].set_title("Fixed engine: resistance breakdown")
    ax[0].legend(fontsize=7); ax[0].grid(alpha=.3, axis="y")
    ax[1].bar(x - 0.22, cr_old, 0.42, color="tab:red", label="legacy deployed Cr")
    ax[1].bar(x + 0.22, cr_new, 0.42, color="tab:blue", label="fixed engine Cr (equiv.)")
    ax[1].set_xticks(x); ax[1].set_xticklabels([n.replace("_", "\n") for n in names], fontsize=7)
    ax[1].set_ylabel("residual coefficient C_r [-]")
    ax[1].set_title("Residual coefficient: fudge vs Holtrop")
    ax[1].legend(fontsize=7); ax[1].grid(alpha=.3, axis="y")
    fig.tight_layout()
    p = os.path.join(OUT, "refval_fig3_components.png")
    fig.savefig(p, dpi=140); plt.close(fig)
    return p


if __name__ == "__main__":
    p1 = fig1_rt_vs_speed()
    p2, v = fig2_deviation()
    p3 = fig3_components()
    print(json.dumps({"figures": [p1, p2, p3], "verdict": v["summary"]}, ensure_ascii=False))