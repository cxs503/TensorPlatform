#!/usr/bin/env python3
"""mission 1758 —— /advanced/bem 审计图（全部数字从 artifacts/m1758/bem_surface/audit.json 读取）。"""
from __future__ import annotations

import json
import os
import sys

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
AUD = os.path.join(ROOT, "artifacts", "m1758", "bem_surface", "audit.json")
FIGDIR = os.path.join(ROOT, "artifacts", "m1758", "figures")
sys.path.insert(0, os.path.join(ROOT, "backend", "api"))

RHO, G = 1025.0, 9.81


def load():
    with open(AUD, encoding="utf-8") as f:
        return json.load(f)


def fig_identity(a, path):
    d = a["derived"]
    names = [x["id"][5:] for x in d]
    x = np.arange(len(d))
    w = 0.27
    fig, ax = plt.subplots(figsize=(8, 4.6))
    ax.bar(x - w, [v["implied_Awp"] for v in d], w, label=r"implied $K_{33}/(\rho g)$", color="#c0392b")
    ax.bar(x, [v["LB"] for v in d], w, label=r"$L\cdot B$ (rectangular waterplane)", color="#7f8c8d")
    ax.bar(x + w, [v["Awp_est"] for v in d], w,
           label=r"$C_w LB,\ C_w=(1+2C_b)/3$ (independent est.)", color="#2980b9")
    for xi, v in zip(x, d):
        ax.annotate(f"{v['rel_Awp_vs_est']:+.1%}", (xi + w, v["Awp_est"]),
                    textcoords="offset points", xytext=(0, 4), ha="center", fontsize=8)
    ax.set_xticks(x)
    ax.set_xticklabels(names)
    ax.set_ylabel(r"waterplane area $A_{wp}$ [m$^2$]")
    ax.set_title("J4/J5: implied $A_{wp}$ from $K_{33}$ equals $L\\cdot B$ exactly (Cb ignored)")
    ax.legend(fontsize=8)
    ax.grid(axis="y", alpha=0.3)
    fig.tight_layout()
    fig.savefig(path, dpi=150)
    plt.close(fig)


def _stf_curve(hull_payload, freqs):
    import seakeeping_physics as stf
    L, B, T, cb = (hull_payload["length"], hull_payload["beam"],
                   hull_payload["draft"], hull_payload["cb"])
    ship = dict(L=L, B=B, T=T, Cb=cb,
                displacement=RHO * cb * L * B * T / 1000.0, GM=2.0, KG=0.55 * T)
    h, p = [], []
    for w in freqs:
        r = stf.solve_rao(ship, float(w), 180.0, 0.0)
        h.append(r["heave_rao"])
        p.append(r["pitch_rao"])
    return h, p


def fig_rao(a, path):
    hulls = {r["id"][5:]: r for r in a["probes"] if r.get("_ok") and r["_group"] == "hull"}
    kcs = hulls["KCS"]
    fr = kcs["rao"]["frequencies"]
    grid = np.linspace(0.1, 2.0, 40)
    h_stf, p_stf = _stf_curve(kcs["_payload"], grid)
    fig, axes = plt.subplots(1, 2, figsize=(10, 4.2))
    for ax, key, stf_v, lab in ((axes[0], "heave", h_stf, "heave RAO [m/m]"),
                                (axes[1], "pitch", p_stf, "pitch RAO [rad/(ka)]")):
        for name, rec in hulls.items():
            ax.plot(rec["rao"]["frequencies"], rec["rao"][key], "o-", ms=4,
                    label=f"platform /advanced/bem - {name}")
        ax.plot(grid, stf_v, "k--", lw=1.6, label="STF strip theory (verified kernel, m1547)")
        ax.set_xlabel(r"$\omega$ [rad/s]")
        ax.set_ylabel(lab)
        ax.grid(alpha=0.3)
    axes[0].set_title("J1/J2/J3: platform RAO is independent of omega and hull (4 hulls collapse)")
    axes[0].legend(fontsize=6.5)
    fig.tight_layout()
    fig.savefig(path, dpi=150)
    plt.close(fig)


def fig_sensitivity(a, path):
    groups = ["freq", "speed", "dir", "cb", "len", "beam", "draft"]
    outs = [("added_mass", "surge"), ("added_mass", "heave"), ("added_mass", "pitch"),
            ("restoring", "heave"), ("restoring", "roll"), ("restoring", "pitch"),
            ("wave_forces", "heave")]
    labels = [f"A11", "A33", "A55", "K33", "K44", "K55", "F3"]
    M = np.zeros((len(groups), len(outs)))
    for i, g in enumerate(groups):
        rs = [r for r in a["probes"] if r.get("_ok") and r["_group"] == g]
        for j, (k1, k2) in enumerate(outs):
            vals = [float(r[k1].get(k2, 0.0)) for r in rs]
            if not vals:
                continue
            scale = max(max(abs(v) for v in vals), 1e-12)
            M[i, j] = (max(vals) - min(vals)) / scale
    fig, ax = plt.subplots(figsize=(7.6, 4.2))
    im = ax.imshow(np.log10(M + 1e-12), cmap="viridis", aspect="auto", vmin=-12, vmax=0)
    ax.set_xticks(range(len(labels)))
    ax.set_xticklabels(labels)
    ax.set_yticks(range(len(groups)))
    ax.set_yticklabels(["wave_frequency", "speed", "wave_direction", "cb",
                        "length", "beam", "draft"])
    for i in range(len(groups)):
        for j in range(len(outs)):
            ax.text(j, i, "0" if M[i, j] < 1e-9 else f"{M[i,j]:.2f}",
                    ha="center", va="center", color="w" if M[i, j] < 1e-2 else "k", fontsize=7)
    fig.colorbar(im, label=r"$\log_{10}$ relative spread")
    ax.set_title("Input sensitivity matrix: 0 = input fully ignored")
    fig.tight_layout()
    fig.savefig(path, dpi=150)
    plt.close(fig)


def fig_cross(a, path):
    rows = a["cross_solver"]
    fig, ax = plt.subplots(figsize=(6.4, 4.6))
    c = {"KCS": "#2980b9", "KVLCC2": "#c0392b", "DTMB5415": "#27ae60", "WIGLEY": "#8e44ad"}
    for r in rows:
        ax.scatter(r["heave_stf"], r["heave_platform"], s=26, color=c.get(r["hull"], "k"),
                   label=r["hull"])
    lim = [0, max(max(r["heave_stf"] for r in rows), max(r["heave_platform"] for r in rows)) * 1.1]
    ax.plot(lim, lim, "k--", lw=1.0, label="1:1")
    ax.set_xlabel("STF strip theory heave RAO [m/m]")
    ax.set_ylabel("platform /advanced/bem heave RAO [m/m]")
    ax.set_title("J12 cross-solver: 24 hull-frequency points, deviation up to 99.7%")
    ax.legend(fontsize=7)
    ax.grid(alpha=0.3)
    fig.tight_layout()
    fig.savefig(path, dpi=150)
    plt.close(fig)


def main():
    os.makedirs(FIGDIR, exist_ok=True)
    a = load()
    outs = [("fig1_restoring_identity.png", fig_identity),
            ("fig2_rao_frequency_invariance.png", fig_rao),
            ("fig3_input_sensitivity.png", fig_sensitivity),
            ("fig4_cross_solver_rao.png", fig_cross)]
    for name, fn in outs:
        p = os.path.join(FIGDIR, name)
        fn(a, p)
        print("wrote", p)
    with open(os.path.join(FIGDIR, "index.json"), "w", encoding="utf-8") as f:
        json.dump(dict(mission=1758, figures=[n for n, _ in outs]), f, ensure_ascii=False, indent=1)


if __name__ == "__main__":
    main()