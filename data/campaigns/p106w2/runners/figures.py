"""Campaign-owned figure runner for the p106w2 integration gate.

Domain narrative (which curve means what) lives here, in the data package -- the
platform CLI only knows ``draw(verdict, data_dir, out_dir) -> [{name, caption}]``.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Mapping

PASS, FAIL, WITHHELD = "PASS", "FAIL", "WITHHELD"


def draw(verdict, data_dir, out_dir):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import numpy as np

    out_dir.mkdir(parents=True, exist_ok=True)
    made: List[Dict[str, str]] = []
    checks = {c["id"]: c for item in verdict["items"] for c in item["checks"]}

    # fig1: slip drag-reduction vs DNS band
    derived = json.loads((data_dir / "raw/n02/derived.json").read_text(encoding="utf-8"))
    fig, ax = plt.subplots(figsize=(6.4, 4.0))
    bp = np.array(derived["b_plus"], dtype=float)
    dcf = np.array(derived["dcf_rel_pct"], dtype=float)
    ax.axhspan(derived["dns_band_lo_pct"], derived["dns_band_hi_pct"], color="#cfe3ff",
               label="DNS literature band (5-15 %)")
    ax.plot(bp, dcf, "o-", color="#b2382e", label="n02 slip model (extracted)")
    for x, y in zip(bp, dcf):
        ax.annotate(f"{y:.3f}%", (x, y), textcoords="offset points", xytext=(4, 6), fontsize=7)
    detail = checks["a.slip_drag_reduction_vs_dns_band"]["detail"]
    ax.set_xlabel("b+ (slip length in wall units)")
    ax.set_ylabel("drag reduction  dCf/Cf0  (%)")
    ax.set_title(f"(a) slip vs bubble mechanism: n02 is {detail['points'][0]['factor_below_lo']:.1f}x below the DNS band")
    ax.legend(fontsize=7, loc="upper left")
    ax.grid(alpha=0.3)
    path = out_dir / "fig1_slip_vs_dns_band.png"
    fig.tight_layout(); fig.savefig(path, dpi=130); plt.close(fig)
    made.append({"name": path.name, "caption": "Fig 1  (a) mechanism cross-check: extracted n02 slip drag reduction vs the DNS band it cites"})

    # fig2: geometry precondition
    fig, ax = plt.subplots(figsize=(6.4, 3.6))
    labels, n05_vals, n06_vals, tols = [], [], [], []
    for cid, label in (("b.geometry_block_coefficient", "block coefficient Cb"),
                       ("b.geometry_length", "length L (m)")):
        entries = checks[cid]["detail"]["entries"]
        labels.append(label)
        n05_vals.append(entries[0]["value"])
        n06_vals.append(entries[1]["value"])
        tols.append(checks[cid]["detail"]["tolerance"])
    x = np.arange(len(labels))
    ax.bar(x - 0.18, n05_vals, 0.36, label="n05 (BEM-Cummins)")
    ax.bar(x + 0.18, n06_vals, 0.36, label="n06 (FVM tank)")
    for i, (a, b) in enumerate(zip(n05_vals, n06_vals)):
        rel = abs(a - b) / max(abs(a), abs(b))
        ax.text(i, max(a, b) * 1.03, f"rel diff {rel*100:.1f}%  (tol {tols[i]*100:.1f}%)",
                ha="center", fontsize=7)
    ax.set_xticks(x); ax.set_xticklabels(labels)
    ax.set_title("(b) RAO inter-comparison precondition: the two arms must describe the same hull")
    ax.legend(fontsize=7); ax.grid(axis="y", alpha=0.3)
    path = out_dir / "fig2_geometry_precondition.png"
    fig.tight_layout(); fig.savefig(path, dpi=130); plt.close(fig)
    made.append({"name": path.name, "caption": "Fig 2  (b) shared-parameter check: the two RAO arms were solved on different geometries"})

    # fig3: rpm excursion band vs barred band
    d = checks["d.rpm_excursion_vs_barred_band"]["detail"]
    fig, ax = plt.subplots(figsize=(6.4, 2.6))
    ax.axhspan(d["band_a"][0], d["band_a"][1], color="#f5d0c5", label="n09 emergence rpm excursion")
    ax.axhspan(d["band_b"][0], d["band_b"][1], color="#c8e6c9", label="n07 5P barred band")
    n09_rpm = checks["d.rated_speed"]["detail"]["entries"][0]["value"]
    n07_rpm = checks["d.rated_speed"]["detail"]["entries"][1]["value"]
    ax.axhline(n09_rpm, color="#b2382e", ls="--", lw=1.2, label=f"n09 rated {n09_rpm:.0f} rpm")
    ax.axhline(n07_rpm, color="#1f4e79", ls=":", lw=1.2, label=f"n07 rated {n07_rpm:.0f} rpm")
    ax.set_ylim(0, 200); ax.set_xlim(0, 1); ax.set_xticks([])
    ax.set_ylabel("shaft speed (rpm)")
    ax.set_title(f"(d) coupling: excursion band covers {d['overlap_fraction']*100:.0f}% of the torsional barred band")
    ax.legend(fontsize=7, loc="upper right"); ax.grid(axis="y", alpha=0.3)
    path = out_dir / "fig3_rpm_excursion_vs_barred.png"
    fig.tight_layout(); fig.savefig(path, dpi=130); plt.close(fig)
    made.append({"name": path.name, "caption": "Fig 3  (d) propeller-emergence rpm excursion vs the 5P barred band of the shafting"})

    # fig4: verdict matrix
    colors = {PASS: "#2e7d32", FAIL: "#b2382e", WITHHELD: "#9e9e9e"}
    fig, ax = plt.subplots(figsize=(7.6, 4.4))
    rows, labels = [], []
    for item in verdict["items"]:
        rows.append([{"PASS": 1, "FAIL": 2, "WITHHELD": 0}[c["status"]] for c in item["checks"]])
        labels.append(item["id"])
    width = max(len(r) for r in rows)
    grid = np.full((len(rows), width), np.nan)
    for i, row in enumerate(rows):
        for j, value in enumerate(row):
            ax.add_patch(plt.Rectangle((j, len(rows) - 1 - i), 0.95, 0.95,
                                       color={1: colors[PASS], 2: colors[FAIL], 0: colors[WITHHELD]}[value]))
    for i, item in enumerate(verdict["items"]):
        for j, check in enumerate(item["checks"]):
            ax.text(j + 0.47, len(rows) - 1 - i + 0.47, check["id"].split(".", 1)[-1][:18],
                    ha="center", va="center", fontsize=5.5, color="white", rotation=0)
    ax.set_xlim(0, width); ax.set_ylim(0, len(rows))
    ax.set_xticks([]); ax.set_yticks(np.arange(len(rows)) + 0.47)
    ax.set_yticklabels(list(reversed(labels)), fontsize=7)
    ax.set_title(f"gate {verdict['gate_status']} - check matrix (green PASS / red FAIL / grey WITHHELD)")
    path = out_dir / "fig4_verdict_matrix.png"
    fig.tight_layout(); fig.savefig(path, dpi=130); plt.close(fig)
    made.append({"name": path.name, "caption": "Fig 4  check-level verdict matrix of the integration gate"})

    # fig5: shared parameter deviations vs tolerance
    fig, ax = plt.subplots(figsize=(6.4, 3.4))
    names, devs, tols = [], [], []
    for cid in ("b.geometry_block_coefficient", "b.geometry_length", "d.rated_speed", "d.shaft_inertia"):
        detail = checks[cid]["detail"]
        names.append(cid.split(".", 1)[-1])
        devs.append(detail["max_rel_diff"] * 100)
        tols.append(detail["tolerance"] * 100)
    y = np.arange(len(names))
    ax.barh(y, devs, color=["#b2382e" if d > t else "#2e7d32" for d, t in zip(devs, tols)])
    for i, (dval, tval) in enumerate(zip(devs, tols)):
        ax.text(dval + 0.4, i, f"{dval:.1f}% > tol {tval:.1f}%", va="center", fontsize=7)
    ax.set_yticks(y); ax.set_yticklabels(names, fontsize=7)
    ax.set_xlabel("cross-node relative deviation (%)")
    ax.set_title("shared-parameter arbitration: declared tolerance vs measured deviation")
    ax.grid(axis="x", alpha=0.3)
    path = out_dir / "fig5_shared_parameter_deviation.png"
    fig.tight_layout(); fig.savefig(path, dpi=130); plt.close(fig)
    made.append({"name": path.name, "caption": "Fig 5  shared-parameter checks (Cb, L, rated rpm, shaft inertia) against their tolerances"})
    return made


