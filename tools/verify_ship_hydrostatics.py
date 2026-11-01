#!/usr/bin/env python3
"""Verification driver for the hull-form hydrostatics module.

Recomputes every analytic anchor used in
``backend/tests/test_ship_hydrostatics.py`` and dumps a machine-readable
record (``artifacts/m1411/hydrostatics_verification.json``) plus three figures.

No numbers in the report are transcribed by hand: the report builder reads the
JSON produced here.

Run:  python3 tools/verify_ship_hydrostatics.py
"""
from __future__ import annotations

import json
import math
import os
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent / "backend"
sys.path.insert(0, str(BACKEND))

from services.ship_hydrostatics import (  # noqa: E402
    GeometryError,
    HullOffsets,
    barge_offsets,
    compute_hydrostatics,
    wigley_offsets,
)

OUT = Path(__file__).resolve().parent.parent / "artifacts" / "m1411"
OUT.mkdir(parents=True, exist_ok=True)


def wigley_exact(L, B, T):
    return {
        "volume_m3": 4.0 * B * L * T / 9.0,
        "cb": 4.0 / 9.0,
        "cm": 2.0 / 3.0,
        "cw": 2.0 / 3.0,
        "waterplane_area_m2": 2.0 * B * L / 3.0,
        "lcb_m": 0.0,
        "lcf_m": 0.0,
        "kb_m": 5.0 * T / 8.0,
        "bm_t_m": 3.0 * B ** 2 / (35.0 * T),
        "bm_l_m": 3.0 * L ** 2 / (40.0 * T),
    }


def barge_exact(L, B, T):
    return {
        "volume_m3": L * B * T,
        "cb": 1.0,
        "cm": 1.0,
        "cw": 1.0,
        "waterplane_area_m2": L * B,
        "lcb_m": 0.0,
        "lcf_m": 0.0,
        "kb_m": 0.5 * T,
        "bm_t_m": B ** 2 / (12.0 * T),
        "bm_l_m": L ** 2 / (12.0 * T),
        "wetted_surface_m2": L * (B + 2.0 * T),
    }


def rel(computed, exact):
    return abs(computed - exact) / abs(exact) if abs(exact) > 0 else abs(computed)


def main() -> int:
    record = {"anchors": {}, "convergence": [], "hydrostatic_curves": [], "gates": []}

    # --- anchor case 1: Wigley verification hull ---------------------------
    L, B, T = 6.0, 0.6, 0.375
    wig = compute_hydrostatics(wigley_offsets(L, B, T, 81, 41), kg=0.5, rho=1025.0)
    wex = wigley_exact(L, B, T)
    rows = []
    for key, exact in wex.items():
        computed = getattr(wig, key)
        rows.append({"quantity": key, "exact": exact, "computed": computed,
                     "rel_err": rel(computed, exact)})
    record["anchors"]["wigley"] = {
        "geometry": {"L": L, "B": B, "T": T, "stations": 81, "waterlines": 41},
        "quantities": rows,
        "derived": {"km_t_m": wig.km_t_m, "gm_m": wig.gm_m, "cp": wig.cp,
                    "wetted_surface_m2": wig.wetted_surface_m2,
                    "integration": wig.integration},
        "max_rel_err": max(r["rel_err"] for r in rows),
    }

    # --- anchor case 2: rectangular barge ---------------------------------
    Lb, Bb, Tb = 100.0, 20.0, 8.0
    bar = compute_hydrostatics(barge_offsets(Lb, Bb, Tb, 41, 21), kg=4.0, rho=1000.0)
    bex = barge_exact(Lb, Bb, Tb)
    rows = []
    for key, exact in bex.items():
        computed = getattr(bar, key)
        rows.append({"quantity": key, "exact": exact, "computed": computed,
                     "rel_err": rel(computed, exact)})
    rows.append({"quantity": "displacement_t", "exact": Lb * Bb * Tb,
                 "computed": bar.displacement_t,
                 "rel_err": rel(bar.displacement_t, Lb * Bb * Tb)})
    record["anchors"]["barge"] = {
        "geometry": {"L": Lb, "B": Bb, "T": Tb, "stations": 41, "waterlines": 21},
        "quantities": rows,
        "derived": {"km_t_m": bar.km_t_m, "gm_m": bar.gm_m, "cp": bar.cp},
        "max_rel_err": max(r["rel_err"] for r in rows),
    }

    # --- quadrature convergence -------------------------------------------
    for n in (11, 21, 41, 81, 161):
        res = compute_hydrostatics(wigley_offsets(L, B, T, n, 41))
        record["convergence"].append({
            "n_stations": n,
            "h": L / (n - 1),
            "cb_rel_err": rel(res.cb, 4.0 / 9.0),
            "bm_t_rel_err": rel(res.bm_t_m, 3.0 * B ** 2 / (35.0 * T)),
            "bm_l_rel_err": rel(res.bm_l_m, 3.0 * L ** 2 / (40.0 * T)),
        })

    # --- hydrostatic curves for a 60 m hull (design-space use) ------------
    Lh, Bh = 60.0, 10.0
    for draft in (2.0, 2.5, 3.0, 3.5, 4.0, 4.5, 5.0):
        res = compute_hydrostatics(wigley_offsets(Lh, Bh, draft, 81, 41), kg=draft * 0.6)
        ex = wigley_exact(Lh, Bh, draft)
        record["hydrostatic_curves"].append({
            "draft_m": draft,
            "volume_m3": res.volume_m3,
            "displacement_t": res.displacement_t,
            "aw_m2": res.waterplane_area_m2,
            "kb_m": res.kb_m,
            "bm_t_m": res.bm_t_m,
            "km_t_m": res.km_t_m,
            "gm_m": res.gm_m,
            "volume_rel_err": rel(res.volume_m3, ex["volume_m3"]),
            "km_rel_err": rel(res.km_t_m, ex["kb_m"] + ex["bm_t_m"]),
        })

    # --- geometry gate behaviour ------------------------------------------
    gate_cases = []

    def try_gate(name, fn):
        try:
            fn()
            gate_cases.append({"case": name, "raised": False, "message": None})
        except GeometryError as exc:
            gate_cases.append({"case": name, "raised": True, "message": str(exc)})

    try_gate("cb_reference=4/9 (match)", lambda: compute_hydrostatics(
        wigley_offsets(L, B, T, 41, 21), cb_reference=4.0 / 9.0))
    try_gate("cb_reference=0.60 (mismatch)", lambda: compute_hydrostatics(
        wigley_offsets(L, B, T, 41, 21), cb_reference=0.60))
    try_gate("negative half-breadth", lambda: _bad_table())
    try_gate("non-monotone stations", lambda: _monotone_table())
    record["gates"] = gate_cases

    # --- figures -----------------------------------------------------------
    _figures(record)

    path = OUT / "hydrostatics_verification.json"
    path.write_text(json.dumps(record, indent=2, ensure_ascii=False))
    print(f"[ok] wrote {path}")
    print(f"[ok] wigley max rel err = {record['anchors']['wigley']['max_rel_err']:.3e}")
    print(f"[ok] barge  max rel err = {record['anchors']['barge']['max_rel_err']:.3e}")
    return 0


def _bad_table():
    off = wigley_offsets(6.0, 0.6, 0.375, 11, 11)
    rows = [list(r) for r in off.half_breadths]
    rows[2][2] = -0.01
    return compute_hydrostatics(
        HullOffsets(off.length, off.stations, off.waterlines, rows))


def _monotone_table():
    off = wigley_offsets(6.0, 0.6, 0.375, 11, 11)
    stations = list(off.stations)
    stations[3], stations[4] = stations[4], stations[3]
    return compute_hydrostatics(
        HullOffsets(off.length, stations, off.waterlines, off.half_breadths))


def _figures(record) -> None:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    # fig 1: hull lines + quadrature convergence
    off = wigley_offsets(6.0, 0.6, 0.375, 41, 21)
    fig, ax = plt.subplots(1, 2, figsize=(11, 4.2))
    for j, z in enumerate(off.waterlines[::4]):
        ax[0].plot(off.stations, [row[j * 4] for row in off.half_breadths],
                   label=f"WL z={z:.3f} m")
    ax[0].set_xlabel("x from AP [m]")
    ax[0].set_ylabel("half-breadth y(x,z) [m]")
    ax[0].set_title("(a) Wigley verification hull: waterline offsets")
    ax[0].legend(fontsize=7)
    ax[0].grid(alpha=0.3)

    conv = record["convergence"]
    hs = [c["h"] for c in conv]
    for key, style in (("cb_rel_err", "o-"), ("bm_t_rel_err", "s-"), ("bm_l_rel_err", "^-")):
        ys = [max(c[key], 1e-17) for c in conv]
        ax[1].loglog(hs, ys, style, label=key.replace("_rel_err", ""))
    h4 = [hs[0] * (hs[-1] / hs[0]) ** (i / 20) for i in range(21)]
    ax[1].loglog(h4, [1e-4 * (h / h4[0]) ** 4 for h in h4], "k--", lw=1, label="slope h^4")
    ax[1].set_xlabel("station spacing h [m]")
    ax[1].set_ylabel("relative error vs closed form")
    ax[1].set_title("(b) Quadrature convergence (Simpson)")
    ax[1].legend(fontsize=7)
    ax[1].grid(alpha=0.3, which="both")
    fig.tight_layout()
    fig.savefig(OUT / "fig1_hull_and_convergence.png", dpi=130)
    plt.close(fig)

    # fig 2: computed vs closed form, both hulls
    fig, ax = plt.subplots(1, 2, figsize=(11, 4.4))
    for panel, key in ((0, "wigley"), (1, "barge")):
        rows = record["anchors"][key]["quantities"]
        labels = [r["quantity"] for r in rows]
        computed = [r["computed"] for r in rows]
        exact = [r["exact"] for r in rows]
        idx = range(len(rows))
        ax[panel].bar([i - 0.2 for i in idx], exact, width=0.4, label="closed form")
        ax[panel].bar([i + 0.2 for i in idx], computed, width=0.4, label="integrated")
        ax[panel].set_xticks(list(idx))
        ax[panel].set_xticklabels(labels, rotation=70, fontsize=7)
        ax[panel].set_yscale("log")
        ax[panel].set_ylabel("value [m, m², m³, -]")
        ax[panel].set_title(f"({'ab'[panel]}) {key}: integrated vs closed form")
        ax[panel].legend(fontsize=7)
        ax[panel].grid(alpha=0.3, axis="y")
        for i, r in enumerate(rows):
            ax[panel].annotate(f"{r['rel_err']:.0e}", (i, max(r["exact"], r["computed"]) * 2),
                               ha="center", fontsize=6, rotation=90)
    fig.tight_layout()
    fig.savefig(OUT / "fig2_anchor_comparison.png", dpi=130)
    plt.close(fig)

    # fig 3: hydrostatic curves of a 60 m hull, with error panel
    curves = record["hydrostatic_curves"]
    drafts = [c["draft_m"] for c in curves]
    fig, ax = plt.subplots(1, 2, figsize=(11, 4.2))
    ax[0].plot(drafts, [c["displacement_t"] for c in curves], "o-", label="displacement [t]")
    ax[0].plot(drafts, [c["aw_m2"] for c in curves], "s-", label="waterplane area [m²]")
    ax[0].set_xlabel("draft T [m]")
    ax[0].set_title("(a) Hydrostatic curves, Wigley 60 m x 10 m")
    ax[0].legend(fontsize=8)
    ax[0].grid(alpha=0.3)
    ax[1].plot(drafts, [c["kb_m"] for c in curves], "o-", label="KB")
    ax[1].plot(drafts, [c["bm_t_m"] for c in curves], "s-", label="BM_T")
    ax[1].plot(drafts, [c["km_t_m"] for c in curves], "^-", label="KM_T")
    ax[1].plot(drafts, [c["gm_m"] for c in curves], "d--", label="GM (KG = 0.6 T)")
    ax[1].set_xlabel("draft T [m]")
    ax[1].set_ylabel("height above keel [m]")
    ax[1].set_title("(b) Stability lever arms from geometry")
    ax[1].legend(fontsize=8)
    ax[1].grid(alpha=0.3)
    fig.tight_layout()
    fig.savefig(OUT / "fig3_hydrostatic_curves.png", dpi=130)
    plt.close(fig)


if __name__ == "__main__":
    raise SystemExit(main())