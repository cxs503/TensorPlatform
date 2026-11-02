"""m1809 审计图件：耐波参考核 + 端点伪表对账（matplotlib，无交互后端）。"""

from __future__ import annotations

import json
import math
import os
from typing import Dict, List, Sequence

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

from ship_perf import seakeeping as sk  # noqa: E402
from ship_perf.constants import RHO_SEA  # noqa: E402

PERIODS = (4.0, 6.0, 8.0, 10.0, 12.0, 14.0, 16.0)


def _case(rep: Dict, name: str) -> Dict:
    for c in rep["cases"]:
        if c["case"]["name"] == name:
            return c
    return rep["cases"][0]


def fig_rao(rep: Dict, out: str) -> str:
    case = _case(rep, "M100")
    hull = sk.Hull(case["case"]["L"], case["case"]["B"], case["case"]["T"], case["case"]["Cb"])
    mass = case["case"]["displacement_t"] * 1000.0
    a33 = case["kernel"]["a33_kg"]
    grid = [2.0 + 0.25 * i for i in range(113)]
    fig, ax = plt.subplots(figsize=(8.0, 5.0))
    for zeta, style in ((0.05, "-"), (0.15, "--"), (0.30, ":")):
        rows = sk.heave_rao(hull, grid, mass, damping_ratio=zeta, a33=a33)
        ax.plot([r["period"] for r in rows], [r["rao_heave"] for r in rows],
                style, lw=1.6, label=f"kernel strip model (damping ratio ζ={zeta:g}, parameterised)")
    ax.axhline(1.0, color="k", lw=1.2, ls="-.", label="exact long-wave limit RAO=1 (ω→0)")
    head = [r for r in case["sweep"] if r["heading"] == 180.0]
    ax.plot([r["period"] for r in head], [r["heave_rao_max"] for r in head],
            "o-", color="crimson", lw=2.2, ms=7, label="platform endpoint heave_rao_max (period-independent)")
    ax.axvline(case["kernel"]["natural_period_s"], color="gray", lw=0.8, ls="--")
    ax.annotate(f"T_n={case['kernel']['natural_period_s']:.2f}s",
                (case["kernel"]["natural_period_s"], 2.4), fontsize=8, color="gray")
    ax.set_xlabel("wave period T (s)")
    ax.set_ylabel("heave RAO (m/m)")
    ax.set_title("M100 (L=100 m, B=15 m, T=5 m, Cb=0.60): heave RAO, kernel vs endpoint")
    ax.set_xlim(2, 30)
    ax.set_ylim(0, 3.0)
    ax.grid(alpha=0.3)
    ax.legend(fontsize=8)
    p = os.path.join(out, "fig1_heave_rao.png")
    fig.tight_layout()
    fig.savefig(p, dpi=150)
    plt.close(fig)
    return p


def fig_convergence(out: str) -> str:
    ns = [48, 96, 192, 384]
    circ, semi, ell = [], [], []
    for n in ns:
        r = sk.panel_added_mass(sk.ellipse_polygon(1.0, 1.0, n), RHO_SEA)
        circ.append(abs(r["heave"] / (RHO_SEA * math.pi) - 1.0) * 100)
        e = sk.panel_added_mass(sk.ellipse_polygon(2.0, 1.0, n), RHO_SEA)
        ell.append(abs(e["heave"] / (RHO_SEA * math.pi * 4.0) - 1.0) * 100)
    for n in (40, 80, 160, 320):
        a = sk.section_heave_added_mass(sk.semicircle_polygon(1.0, n), RHO_SEA, True)
        semi.append((n, abs(a / (RHO_SEA * math.pi / 2.0) - 1.0) * 100))
    fig, ax = plt.subplots(figsize=(7.2, 4.6))
    ax.loglog(ns, circ, "o-", label="submerged circle vs rho*pi*a^2")
    ax.loglog(ns, ell, "s-", label="submerged ellipse(a=2,b=1) vs rho*pi*a^2")
    ax.loglog([s[0] for s in semi], [s[1] for s in semi], "^-", label="free-surface semicircle vs rho*pi*a^2/2")
    ax.axhline(3.0, color="crimson", ls="--", lw=1.0, label="3% target")
    ax.set_xlabel("number of panels n")
    ax.set_ylabel("error vs analytic solution (%)")
    ax.set_title("panel kernel convergence against 3 exact analytic anchors")
    ax.grid(alpha=0.3, which="both")
    ax.legend(fontsize=8)
    p = os.path.join(out, "fig2_panel_convergence.png")
    fig.tight_layout()
    fig.savefig(p, dpi=150)
    plt.close(fig)
    return p


def fig_roll_sym(rep: Dict, out: str) -> str:
    heads = [0, 30, 60, 90, 120, 150, 180]
    fig, ax = plt.subplots(figsize=(7.2, 4.6))
    for case in rep["cases"]:
        hull = sk.Hull(case["case"]["L"], case["case"]["B"], case["case"]["T"], case["case"]["Cb"])
        vals = [max(sk.fk_roll_moment_free_surface(hull, 1.0, t, h) for t in (6.0, 8.0, 10.0, 12.0))
                for h in heads]
        vmax = max(vals) or 1.0
        ax.plot(heads, [v / vmax for v in vals], "o-", label=f"kernel FK roll moment ({case['case']['name']})")
    ax.axhline(2.0, color="crimson", lw=2.0, ls="-",
               label="endpoint roll_rao_max=2.0 rad/rad (heading-independent)")
    ax.axvline(180, color="k", ls=":", lw=1.0)
    ax.annotate("psi=180 deg: symmetry => M4=0 exactly", (178, 1.55), ha="right", fontsize=8)
    ax.set_xlabel("heading psi (deg), 180 = head seas")
    ax.set_ylabel("normalised roll excitation / roll RAO")
    ax.set_title("roll excitation heading symmetry: kernel (normalised) vs endpoint constant 2.0")
    ax.grid(alpha=0.3)
    ax.legend(fontsize=8, loc="upper center")
    p = os.path.join(out, "fig3_roll_symmetry.png")
    fig.tight_layout()
    fig.savefig(p, dpi=150)
    plt.close(fig)
    return p


def fig_strip(rep: Dict, out: str) -> str:
    fig, ax = plt.subplots(figsize=(7.2, 4.4))
    for case in rep["cases"]:
        hull = sk.Hull(case["case"]["L"], case["case"]["B"], case["case"]["T"], case["case"]["Cb"])
        xs = [-0.5 * hull.length + hull.length * (i + 0.5) / 41 for i in range(41)]
        ms = []
        for x in xs:
            b = hull.beam_at(x)
            ms.append(0.0 if b < 1e-3 else sk.section_heave_added_mass(hull.section_at(x, 10), RHO_SEA, True))
        ax.plot([2 * x / hull.length for x in xs], ms, label=f"{case['case']['name']}")
    ax.set_xlabel("x/L (-0.5 stern -> +0.5 bow)")
    ax.set_ylabel("sectional heave added mass m33 (kg/m)")
    ax.set_title("2D heave added mass along a wall-sided ideal hull (free-surface double-body kernel)")
    ax.grid(alpha=0.3)
    ax.legend(fontsize=8)
    p = os.path.join(out, "fig4_strip_m33.png")
    fig.tight_layout()
    fig.savefig(p, dpi=150)
    plt.close(fig)
    return p


def fig_api_flat(rep: Dict, out: str) -> str:
    fig, ax = plt.subplots(figsize=(7.2, 4.6))
    case = _case(rep, "M100")
    for heading in rep["headings"]:
        rows = [r for r in case["sweep"] if r["heading"] == heading]
        ax.plot([r["period"] for r in rows], [r["heave_rao_max"] for r in rows],
                "o-", label=f"psi={int(heading)} deg  heave_rao_max")
        ax.plot([r["period"] for r in rows], [r["roll_rao_max"] for r in rows],
                "s--", alpha=0.6, label=f"psi={int(heading)} deg  roll_rao_max")
    ax.set_xlabel("wave period T (s)")
    ax.set_ylabel("RAO returned by the endpoint")
    ax.set_title("endpoint response (M100): identical over 10 periods x 3 headings => fake table")
    ax.grid(alpha=0.3)
    ax.legend(fontsize=7, ncol=2)
    ax.set_ylim(0, 4.2)
    p = os.path.join(out, "fig5_api_flat.png")
    fig.tight_layout()
    fig.savefig(p, dpi=150)
    plt.close(fig)
    return p


def make_all(rep: Dict, out: str) -> List[str]:
    os.makedirs(out, exist_ok=True)
    paths = [fig_rao(rep, out), fig_convergence(out), fig_roll_sym(rep, out),
             fig_strip(rep, out), fig_api_flat(rep, out)]
    with open(os.path.join(out, "figures_index.json"), "w") as fh:
        json.dump([os.path.basename(p) for p in paths], fh, indent=2)
    return paths