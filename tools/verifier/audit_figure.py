#!/usr/bin/env python3
"""
audit_figure — figures for the question-bank audit report.

Panel (a): form factor (1+k) vs block coefficient — stored reference values
           against the literature Holtrop & Mennen (1982) curve and its
           sensitivity band over the inputs the questions omit.
Panel (b): flat-plate friction Cf vs Re — stored reference values against the
           independent reference implementation (the 12 questions that pass).
Panel (c): per-question relative deviation of stored reference vs independent
           recompute, with the platform 3% precision line.

Usage:  python -m tools.verifier.audit_figure --out artifacts/qb_audit.png
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Dict, List

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402

try:
    from tools.verifier import reference_impl as ref
except ImportError:  # direct execution
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
    from tools.verifier import reference_impl as ref

PLATFORM_PRECISION = 0.03


def _holtrop_cases(result: Dict) -> List[Dict]:
    out = []
    for q in result["questions"]:
        if q.get("formula") == "holtrop_mennen_form_factor":
            spec = q["specification"]
            out.append(
                {
                    "Cb": spec["Cb"],
                    "L": spec["L"],
                    "B": spec["B"],
                    "T": spec["T"],
                    "stored": q["stored"]["1+k"],
                    "independent": q["independent"]["1+k"],
                    "band": q["sensitivity_band"],
                }
            )
    return sorted(out, key=lambda d: d["Cb"])


def _flat_plate_cases(result: Dict) -> List[Dict]:
    out = []
    for q in result["questions"]:
        spec = q["specification"]
        if q.get("formula") in ("blasius_laminar", "prandtl_schlichting", "ittc_1957"):
            re_l = spec.get("Re_L") or spec.get("Re")
            out.append(
                {
                    "formula": q["formula"],
                    "Re": re_l,
                    "stored": q["stored"]["Cf"],
                    "independent": q["independent"]["Cf"],
                }
            )
    return sorted(out, key=lambda d: (d["formula"], d["Re"]))


def plot(result: Dict, out_path: str) -> str:
    fig, axes = plt.subplots(1, 3, figsize=(17.5, 5.2))

    # ---- (a) form factor: stored vs literature + band ---------------------
    ax = axes[0]
    cases = _holtrop_cases(result)
    if cases:
        cbs_lit = np.linspace(0.55, 0.85, 61)
        lit = [ref.holtrop_mennen_form_factor(cb, 150.0, 22.0, 8.5)["1+k"] for cb in cbs_lit]
        ax.plot(cbs_lit, lit, "-", color="#1f4e79", lw=2,
                label="Holtrop & Mennen (1982) Eq.(12)\n(L=150,B=22,T=8.5 reference hull)")
        band_lo, band_hi = [], []
        for cb in cbs_lit:
            b = ref.holtrop_mennen_form_factor_band(cb, 150.0, 22.0, 8.5, n=3)
            band_lo.append(b["min"])
            band_hi.append(b["max"])
        ax.fill_between(cbs_lit, band_lo, band_hi, color="#1f4e79", alpha=0.15,
                        label="admissible band (Cm 0.95–0.99, LCB ±2%L, Cstern ±10)")
        ax.plot([c["Cb"] for c in cases], [c["stored"] for c in cases], "s--",
                color="#c00000", ms=9, lw=1.8, label="stored reference in questions.db")
        for c in cases:
            ax.errorbar(c["Cb"], c["independent"], fmt="o", color="#2e7d32", ms=7)
            ax.annotate(f"lit {c['independent']:.3f}", (c["Cb"], c["independent"]),
                        textcoords="offset points", xytext=(6, 4), fontsize=8, color="#2e7d32")
            ax.annotate(f"{c['stored']:.3f}", (c["Cb"], c["stored"]),
                        textcoords="offset points", xytext=(6, -12), fontsize=8, color="#c00000")
        ax.plot([], [], "o", color="#2e7d32", label="independent recompute (this audit)")
        ax.set_xlabel("block coefficient $C_B$ [-]")
        ax.set_ylabel("form factor $1+k$ [-]")
        ax.set_title("(a) form factor: stored bank vs literature\n"
                     "stored values fall below the admissible band AND invert the trend")
        ax.grid(alpha=0.3)
        ax.legend(fontsize=7.5, loc="lower left")

    # ---- (b) flat plate: stored vs independent ---------------------------
    ax = axes[1]
    fp = _flat_plate_cases(result)
    style = {"blasius_laminar": ("o", "#1f4e79", "Blasius laminar"),
             "prandtl_schlichting": ("s", "#b26a00", "Prandtl–Schlichting turbulent"),
             "ittc_1957": ("^", "#2e7d32", "ITTC-1957 line")}
    for fam, (marker, color, label) in style.items():
        sel = [q for q in fp if q["formula"] == fam]
        if not sel:
            continue
        ax.plot([q["Re"] for q in sel], [q["stored"] for q in sel], marker, color=color,
                ms=9, label=f"{label} — stored")
        ax.plot([q["Re"] for q in sel], [q["independent"] for q in sel], "-", color=color, lw=1.0)
    ax.plot([], [], "k-", lw=1.0, label="independent recompute (overlaps stored)")
    ax.set_xscale("log")
    ax.set_yscale("log")
    ax.set_xlabel("Reynolds number $Re_L$ [-]")
    ax.set_ylabel("$C_F$ [-]")
    ax.set_title("(b) friction lines: 12/15 references confirmed\n"
                 "relative deviation $\\leq$ 1e-9 (round-off only)")
    ax.grid(alpha=0.3, which="both")
    ax.legend(fontsize=8)

    # ---- (c) per-question deviation --------------------------------------
    ax = axes[2]
    labels, devs, colors = [], [], []
    for q in sorted(result["questions"], key=lambda x: -max(x.get("deviations", {}).values() or [0])):
        dev = max(q.get("deviations", {}).values() or [0])
        labels.append(q["title"].replace("Holtrop-Mennen form factor: ", "1+k ")
                      .replace("Laminar flat plate Cf at ", "laminar ")
                      .replace("Turbulent flat plate Cf at ", "turb. ")
                      .replace("ITTC-1957 Cf at ", "ITTC "))
        devs.append(max(dev, 1e-17))
        colors.append("#c00000" if (q.get("verdict") != "CONFIRMED") else "#2e7d32")
    y = np.arange(len(labels))
    ax.barh(y, devs, color=colors, height=0.7)
    ax.set_yticks(y)
    ax.set_yticklabels(labels, fontsize=7)
    ax.set_xscale("log")
    ax.axvline(PLATFORM_PRECISION, color="k", ls="--", lw=1.2, label="platform 3% standard")
    ax.set_xlabel("relative deviation |stored − independent| / |independent|")
    ax.set_title("(c) per-question deviation\nred = rejected (circular reference), green = confirmed")
    ax.set_xlim(1e-17, 1.0)
    ax.grid(alpha=0.3, axis="x")
    ax.legend(fontsize=8, loc="lower right")

    fig.suptitle("Independent audit of the verifier question bank ground truth "
                 "(tools/verifier/question_bank/questions.db)", fontsize=12.5)
    fig.tight_layout(rect=(0, 0, 1, 0.95))
    os.makedirs(os.path.dirname(os.path.abspath(out_path)) or ".", exist_ok=True)
    fig.savefig(out_path, dpi=150)
    plt.close(fig)
    return out_path


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--audit", default="artifacts/question_bank_audit.json")
    ap.add_argument("--out", default="artifacts/question_bank_audit.png")
    args = ap.parse_args(argv)
    with open(args.audit, "r", encoding="utf-8") as fh:
        result = json.load(fh)
    path = plot(result, args.out)
    print(f"figure written: {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())