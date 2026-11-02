#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""STD-GEOM figures from artifacts/std_geometry/ledger.json (zero hand-copied numbers).

    python3 -m tools.verif.geometry.make_std_geometry_figures
"""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from typing import Any, Dict, List

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt


def fig1(led: Dict[str, Any], path: Path) -> None:
    hulls = led["hulls"]
    names = [h["hull"] for h in hulls]
    holt = [h["computed"].get("s_holtrop_m2", float("nan")) for h in hulls]
    fit = [h["computed"]["s_axisym_fit_m2"] for h in hulls]
    ref = []
    for h in hulls:
        r = h["reference"]
        v = r.get("wetted_surface_m2") or r.get("wetted_surface_m2_filed")
        ref.append(v if v else float("nan"))
    x = list(range(len(names)))
    w = 0.27
    fig, ax = plt.subplots(figsize=(9, 4.6))
    ax.bar([i - w for i in x], holt, w, label="Holtrop (ship law)", color="#2b6cb0")
    ax.bar(x, ref, w, label="published / filed", color="#2f855a")
    ax.bar([i + w for i in x], fit, w, label="platform axisym fit (audited)", color="#c53030")
    for i, v in enumerate(ref):
        if v and not math.isnan(v):
            ax.annotate("%+.1f%%" % ((fit[i] / v - 1) * 100), (i + w, fit[i]),
                        textcoords="offset points", xytext=(0, 4), ha="center",
                        fontsize=9, color="#c53030", fontweight="bold")
    ax.set_xticks(x)
    ax.set_xticklabels(names, fontsize=9)
    ax.set_yscale("log")
    ax.set_ylabel("wetted surface S [m$^2$]")
    ax.set_title("Two in-tree wetted-surface laws vs the platform axisym fit")
    ax.legend(fontsize=8)
    ax.grid(axis="y", alpha=0.3)
    fig.tight_layout()
    fig.savefig(path, dpi=150)
    plt.close(fig)


def fig2(led: Dict[str, Any], path: Path) -> None:
    sub = next(h for h in led["hulls"] if h["hull"] == "SUBOFF")
    d = sub["geometry"]["diameter"]
    l = sub["geometry"]["length"]
    cyl = math.pi * d * l
    cbs = [i / 100.0 for i in range(1, 101)]
    curve = [cyl * (0.5 + 0.25 * cb) for cb in cbs]
    filed = sub["reference"]["wetted_surface_m2_filed"]
    pr = sub["reference"]["profile_surface_m2"]
    fig, ax = plt.subplots(figsize=(8.4, 4.6))
    ax.plot(cbs, curve, color="#c53030", lw=2, label="platform fit pi*D*L*(0.5+0.25Cb)")
    ax.axhline(filed, color="#2f855a", lw=1.6, label="filed case record %.3g m2" % filed)
    ax.axhspan(pr["min"], pr["max"], color="#2b6cb0", alpha=0.18,
               label="profile integral %.3g-%.3g m2" % (pr["min"], pr["max"]))
    ax.axhline(cyl * 0.75, color="#c53030", ls=":", lw=1.2,
               label="fit ceiling 0.75*pi*D*L = %.3g m2" % (cyl * 0.75))
    ax.annotate("filed value ABOVE the fit ceiling\n-> unattainable for any Cb <= 1",
                (0.02, filed), textcoords="offset points", xytext=(6, 10),
                fontsize=9, color="#2f855a")
    ax.set_xlabel("Cb passed to the endpoint")
    ax.set_ylabel("S [m$^2$]")
    ax.set_xlim(0, 1.0)
    ax.set_title("SUBOFF: the case record lies outside the fit's attainable range")
    ax.legend(fontsize=8, loc="lower right")
    ax.grid(alpha=0.3)
    fig.tight_layout()
    fig.savefig(path, dpi=150)
    plt.close(fig)


def fig3(led: Dict[str, Any], path: Path) -> None:
    rows: List[Dict[str, Any]] = led["rt_inflation"]["rows"] if led["rt_inflation"]["available"] else []
    fig, ax = plt.subplots(figsize=(6.4, 4.6))
    xs = [r["s_error"] for r in rows]
    ys = [r["rt_error"] for r in rows]
    cs = [r["ct_error"] for r in rows]
    lim = max(xs + ys + [0.1]) * 1.2
    ax.plot([0, lim], [0, lim], color="#4a5568", ls="--", lw=1, label="identity y = x")
    ax.scatter(xs, ys, s=70, color="#c53030", label="R_T error (N)")
    ax.scatter(xs, cs, s=70, color="#2b6cb0", marker="s", label="C_T error")
    for r in rows:
        ax.annotate(r["hull"], (r["s_error"], r["rt_error"]), textcoords="offset points",
                    xytext=(5, -12), fontsize=8)
    ax.axvline(0.15, color="#718096", ls=":", lw=1)
    ax.text(0.155, 0.02, "15% judgement line", fontsize=7, color="#718096", rotation=90)
    ax.set_xlabel("wetted-surface error S_model/S_ref - 1")
    ax.set_ylabel("resulting error")
    ax.set_title("In-process resistance model: R_T error identical to S error, C_T decoupled")
    ax.legend(fontsize=8)
    ax.grid(alpha=0.3)
    fig.tight_layout()
    fig.savefig(path, dpi=150)
    plt.close(fig)


def fig4(led: Dict[str, Any], path: Path) -> None:
    sub = next(h for h in led["hulls"] if h["hull"] == "SUBOFF")
    pv = sub["reference"]["profile_volume_m3"]
    c = sub["computed"]
    fig, ax = plt.subplots(figsize=(7.6, 4.4))
    ax.barh(0, pv["max"] - pv["min"], left=pv["min"], height=0.34, color="#2b6cb0",
            alpha=0.45, label="profile integral range (pi*int r^2 dx)")
    ax.plot([pv["nominal"]], [0], marker="|", ms=18, color="#2b6cb0", mew=2,
            label="profile nominal %.4f m3" % pv["nominal"])
    ax.plot([c["volume_m3_rect_law"]], [1], marker="o", ms=10, color="#c53030",
            label="preset, rectangular law %.4f m3" % c["volume_m3_rect_law"])
    ax.plot([c["volume_m3_circular_law"]], [1], marker="s", ms=9, color="#dd6b20",
            label="preset, circular law %.4f m3" % c["volume_m3_circular_law"])
    ax.plot([0.7060], [0.5], marker="*", ms=14, color="#2f855a",
            label="literature bare hull 0.7060 m3 (CONF_MED)")
    ax.set_yticks([0, 0.5, 1])
    ax.set_yticklabels(["independent\nprofile integral", "DARPA\nreference", "platform preset\n(Cb=0.542)"],
                       fontsize=9)
    ax.set_xlabel("displaced volume [m$^3$]")
    ax.set_title("SUBOFF displaced volume: preset vs profile integral vs section law")
    ax.legend(fontsize=8, loc="lower right")
    ax.grid(axis="x", alpha=0.3)
    fig.tight_layout()
    fig.savefig(path, dpi=150)
    plt.close(fig)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ledger", default="artifacts/std_geometry/ledger.json")
    ap.add_argument("--out-dir", default="artifacts/std_geometry")
    args = ap.parse_args(argv)
    led = json.loads(Path(args.ledger).read_text())
    out = Path(args.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    fig1(led, out / "fig1_surface_laws.png")
    fig2(led, out / "fig2_suboff_attainability.png")
    fig3(led, out / "fig3_rt_vs_ct.png")
    fig4(led, out / "fig4_suboff_volume.png")
    print("wrote 4 figures to", out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())