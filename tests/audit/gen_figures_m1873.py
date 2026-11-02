"""m1873 figures: duplicate-kernel divergence audit (matplotlib -> outputs/).

Three panels, all numbers read from
``outputs/m1873_dup_kernel/divergence.json`` (never hand-typed):

1. ``fig_wetted_surface_error.png`` — wetted-surface estimators vs the published
   table value for KVLCC2 / DTMB-5415 (3 % criterion band drawn).
2. ``fig_box_gz.png`` — righting arm of a rectangular section: exact analytic
   section integral, the platform wall-sided closed form, the platform polygon
   solver, and the linearised GM*sin(phi); wall-sided validity limit marked.
3. ``fig_resistance_divergence.png`` — total resistance from the endpoint core
   vs the service core, before and after pinning the wetted surface.
"""

from __future__ import annotations

import json
import os

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
OUT = os.path.join(ROOT, "outputs", "m1873_dup_kernel")

LABEL = {
    "ref_axisym(pi*B*L*(0.5+0.25Cb))": "axisym  (legacy / core default)",
    "api_core default S (axisym)": "api_core default (axisym)",
    "svc.legacy_wetted_surface": "svc.legacy (= axisym)",
    "CLAIM(axisym vs published)": "documented claim (axisym)",
    "ref_holtrop1982(Cwp=published)": "Holtrop, published Cwp",
    "api.holtrop_wetted_surface(published Cm,Cwp)": "api.holtrop (published Cm,Cwp)",
    "ref_holtrop1982(Cwp=(1+2Cb)/3)": "Holtrop, Cwp=(1+2Cb)/3",
    "svc.holtrop_wetted_surface(default Cwp)": "svc.holtrop (default Cwp)",
    "svc.wetted_surface('ship')": "svc.wetted_surface('ship')",
    "ref_denny_mumford": "Denny-Mumford",
    "svc.denny_mumford": "svc.denny_mumford",
}


def fig_wetted(d):
    rows = d["A_wetted_surface"]["rows"]
    hulls = ["KVLCC2", "DTMB-5415"]
    keep = [k for k in LABEL if any(r["estimator"] == k for r in rows)]
    fig, ax = plt.subplots(figsize=(9.2, 4.6))
    x = np.arange(len(keep))
    w = 0.38
    for i, h in enumerate(hulls):
        vals = [next((r["err_vs_published_pct"] for r in rows
                      if r["hull"] == h and r["estimator"] == k), np.nan) for k in keep]
        ax.bar(x + (i - 0.5) * w, vals, w, label=h)
    ax.axhspan(-3, 3, color="tab:green", alpha=0.12, label="±3 % criterion")
    ax.axhspan(-15, 15, color="tab:orange", alpha=0.06, label="±15 % platform gate")
    ax.axhline(0, color="k", lw=0.8)
    ax.set_xticks(x)
    ax.set_xticklabels([LABEL[k] for k in keep], rotation=28, ha="right", fontsize=8)
    ax.set_ylabel("error vs published S  [%]")
    ax.set_title("Wetted-surface estimators vs published table (m1873)")
    ax.legend(fontsize=8)
    ax.grid(axis="y", ls=":", alpha=0.4)
    fig.tight_layout()
    p = os.path.join(OUT, "fig_wetted_surface_error.png")
    fig.savefig(p, dpi=150)
    plt.close(fig)
    return p


def fig_gz(d):
    rows = d["D_box_hydrostatics"]["rows"]
    lim = d["D_box_hydrostatics"]["wall_sided_limit_deg"]
    phi = [r["phi_deg"] for r in rows]
    fig, (ax, ax2) = plt.subplots(1, 2, figsize=(11.0, 4.3))
    ax.plot(phi, [r["gz_exact_m"] for r in rows], "o-", label="exact section integral")
    ax.plot(phi, [r["gz_wallsided_m"] for r in rows], "s--", label="wall-sided closed form")
    ax.plot(phi, [r["gz_numeric_m"] for r in rows], "^:", label="polygon solver (platform)")
    ax.plot(phi, [r["gz_linear_GMsin_m"] for r in rows], "-.", label="GM*sin(phi) linearised")
    ax.axvline(lim, color="tab:red", ls="--", lw=1, label=f"wall-sided limit {lim:.1f}°")
    ax.set_xlabel("heel angle [deg]")
    ax.set_ylabel("GZ [m]")
    ax.set_title("Box section B=10 T=4 depth=8 KG=3 (GM=+1.083 m)")
    ax.legend(fontsize=8)
    ax.grid(ls=":", alpha=0.4)
    ax2.plot(phi, [r["err_wallsided_pct"] for r in rows], "s--", label="wall-sided error")
    ax2.plot(phi, [r["err_numeric_pct"] for r in rows], "^:", label="polygon solver error")
    ax2.axhline(3, color="tab:green", ls=":", lw=1)
    ax2.axhline(-3, color="tab:green", ls=":", lw=1, label="±3 %")
    ax2.axvline(lim, color="tab:red", ls="--", lw=1)
    ax2.set_xlabel("heel angle [deg]")
    ax2.set_ylabel("error vs exact [%]")
    ax2.set_title("Error growth past the wall-sided limit")
    ax2.legend(fontsize=8)
    ax2.grid(ls=":", alpha=0.4)
    fig.tight_layout()
    p = os.path.join(OUT, "fig_box_gz.png")
    fig.savefig(p, dpi=150)
    plt.close(fig)
    return p


def fig_resistance(d):
    rows = d["E_resistance_end_to_end"]["rows"]
    fig, ax = plt.subplots(figsize=(8.6, 4.4))
    for hull, mk in (("KVLCC2", "o"), ("DTMB-5415", "s")):
        hs = [r for r in rows if r["hull"] == hull]
        fn = [r["fn"] for r in hs]
        ax.plot(fn, [r["rt_api_kN"] for r in hs], mk + "-", label=f"{hull}: endpoint core")
        ax.plot(fn, [r["rt_svc_kN"] for r in hs], mk + "--",
                label=f"{hull}: service core (axisym S)")
        ax.plot(fn, [r["rt_svc_kN"] for r in hs], "x", ms=9,
                label=f"{hull}: service core, S pinned")
    ax.set_yscale("log")
    ax.set_xlabel("Froude number Fn")
    ax.set_ylabel("total resistance Rt [kN]  (log)")
    ax.set_title("Two resistance cores on identical inputs (m1873)")
    ax.legend(fontsize=8)
    ax.grid(ls=":", alpha=0.4)
    fig.tight_layout()
    p = os.path.join(OUT, "fig_resistance_divergence.png")
    fig.savefig(p, dpi=150)
    plt.close(fig)
    return p


def main():
    d = json.load(open(os.path.join(OUT, "divergence.json"), encoding="utf-8"))
    os.makedirs(OUT, exist_ok=True)
    for fn in (fig_wetted, fig_gz, fig_resistance):
        print("wrote", fn(d))


if __name__ == "__main__":
    main()