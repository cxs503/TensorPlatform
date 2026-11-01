"""冰区航行域验证图件：从 evidence.json 读数字（零手抄），输出 3 张 png。"""
from __future__ import annotations

import json
import os

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

OUT = os.path.dirname(os.path.abspath(__file__))
EV = json.load(open(os.path.join(OUT, "evidence.json")))
P = EV["probes"]


def fig_thickness():
    h = P["P3_thickness_sweep"]["input"]["ice_thickness_m"]
    rep = P["P3_thickness_sweep"]["R_ice"]
    orc = P["P3_thickness_sweep"]["oracle_R_ice_kN"]
    fig, ax = plt.subplots(figsize=(7.2, 4.6))
    ax.loglog(h, rep, "o-", lw=2, label="platform /api/v1/extended-calculations/ice")
    ax.loglog(h, orc, "s--", lw=2, label="in-repo Lindqvist (methods/ice/lindqvist.py)")
    ax.set_xlabel("ice thickness h [m]")
    ax.set_ylabel("ice resistance R_ice [kN]")
    ax.set_title("Ice resistance vs thickness (L=150 m, B=23 m, T=8 m, 5 kn)")
    for x, y in zip(h, rep):
        ax.annotate(f"{y:.0f}", (x, y), textcoords="offset points", xytext=(4, 6), fontsize=7)
    ax.grid(True, which="both", alpha=.3)
    ax.legend(fontsize=8)
    fig.tight_layout()
    fig.savefig(os.path.join(OUT, "fig_R_vs_thickness.png"), dpi=130)
    plt.close(fig)


def fig_speed():
    v = P["P2_speed_sweep"]["input"]["speed_kn"]
    rep = np.array(P["P2_speed_sweep"]["R_ice"], dtype=float)
    # Lindqvist 速度结构（同一破冰项乘上 (1+1.4 v/sqrt(g h))）——只展示速度依赖性缺失
    g, h = 9.81, 1.0
    vms = np.array(v) * 0.5144
    lind = rep[0] * (1 + 1.4 * vms / np.sqrt(g * h))
    fig, ax = plt.subplots(figsize=(7.2, 4.4))
    ax.plot(v, rep, "o-", lw=2, label="platform (speed-independent)")
    ax.plot(v, lind, "s--", lw=2, label=r"Lindqvist speed term $(1+1.4v/\sqrt{gh})$")
    ax.set_xlabel("speed [kn]")
    ax.set_ylabel("R_ice [kN]")
    ax.set_title("Speed dependence: platform R_ice is flat, Lindqvist is not")
    ax.grid(alpha=.3)
    ax.legend(fontsize=8)
    fig.tight_layout()
    fig.savefig(os.path.join(OUT, "fig_R_vs_speed.png"), dpi=130)
    plt.close(fig)


def fig_structure():
    cls = P["P7_ice_class_sweep"]["input"]["ice_class"]
    sp = P["P7_ice_class_sweep"]["shell_plating"]
    fs = P["P7_ice_class_sweep"]["frame_spacing"]
    h = P["P3_thickness_sweep"]["input"]["ice_thickness_m"]
    fsh = P["P3_thickness_sweep"]["frame_spacing"]
    fig, (a1, a2) = plt.subplots(1, 2, figsize=(10, 4.2))
    a1.bar(cls, sp, color="#4c78a8")
    a1.set_ylim(0, max(sp) * 1.35)
    a1.set_title("shell plating vs Polar Class (h=1.0 m)")
    a1.set_ylabel("shell plating [mm]")
    for i, y in enumerate(sp):
        a1.annotate(f"{y}", (i, y), ha="center", va="bottom", fontsize=8)
    a1.grid(axis="y", alpha=.3)
    a2.plot(h, fsh, "o-", lw=2, color="#e45756")
    a2.axhline(0, color="k", lw=1)
    a2.set_xlabel("ice thickness h [m]")
    a2.set_ylabel("frame spacing [mm]")
    a2.set_title(r"frame spacing $400-50h$: unbounded, negative at h>8 m")
    a2.grid(alpha=.3)
    for x, y in zip(h, fsh):
        a2.annotate(f"{y:.0f}", (x, y), textcoords="offset points", xytext=(4, 6), fontsize=7)
    fig.tight_layout()
    fig.savefig(os.path.join(OUT, "fig_structure_requirements.png"), dpi=130)
    plt.close(fig)


def fig_error():
    d = EV["derived"]
    labels = ["R_ice @1m ice\n[endpoint / in-repo Lindqvist]",
              "effective ice strength\n[endpoint, MPa vs 0.5-5 MPa band]",
              "R(15kn)/R(1kn)\n[expected >=1.5]"]
    vals = [d["ratio_endpoint_over_oracle"], d["sigma_eff_MPa"], d["speed_ratio_15_over_1"]]
    fig, ax = plt.subplots(figsize=(8.2, 4.3))
    ax.bar(labels, vals, color=["#e45756", "#f58518", "#54a24b"])
    ax.set_yscale("log")
    ax.set_ylabel("value (log scale)")
    ax.set_title("Criteria deviations at baseline point")
    for i, y in enumerate(vals):
        ax.annotate(f"{y:.4g}", (i, y), ha="center", va="bottom", fontsize=9)
    ax.grid(axis="y", alpha=.3)
    fig.tight_layout()
    fig.savefig(os.path.join(OUT, "fig_deviation.png"), dpi=130)
    plt.close(fig)


if __name__ == "__main__":
    fig_thickness()
    fig_speed()
    fig_structure()
    fig_error()
    print("figures written:", sorted(f for f in os.listdir(OUT) if f.endswith(".png")))