#!/usr/bin/env python3
"""m1684 verification harness — hull-girder vibration kernel vs exact beam theory.

Runs the five independent judges on :mod:`backend.services.ship_vibration` and
writes a machine-readable verdict + figures:

J1  exact characteristic roots        cos x cosh x = 1  (x1 = 4.7300407448627...)
J2  mesh convergence of the FEM free-free beam vs the closed form
    f_n = (b_n L)^2/(2 pi L^2) sqrt(EI/m), with observed order and Richardson /
    GCI estimate
J3  mode-shape geometric check        nodal lines of mode 1 at 0.22416 L / 0.77584 L
J4  monotonicity bracket              a non-uniform EI(x) field must land inside
    [f_uniform(EI_min), f_uniform(EI_max)]  (rigorous, reference-free)
J5  similitude invariance             geometric scaling by lambda must scale the
    frequency by exactly 1/lambda

Usage::

    python3 examples/verify_ship_vibration.py [--out docs/reports/m1684_vibration]
"""
from __future__ import annotations

import argparse
import json
import math
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend.services.ship_vibration import (  # noqa: E402
    analytic_bracket,
    assess_hull_vibration,
    free_free_beam_eigenvalue,
    hull_girder_modal,
    uniform_beam_frequency,
    uniform_beam_mode_shape,
)

E_STEEL = 2.06e11
# Reference ship for the application case: a Panamax-type bulk carrier. The
# section distribution is synthetic-but-plausible (midship peak, ends at 35 % of
# midship); no experimental hull-girder frequency for this hull exists in-repo,
# so the absolute magnitude is graded WITHHELD (see the report).
CASE = {
    "name": "Panamax bulk carrier (synthetic section distribution)",
    "L": 225.0,
    "displacement_t": 80000.0,
    "I_midship": 260.0,
    "n_stations": 46,
    "design_rpm": 90.0,
    "n_blades": 4,
}


def judge_roots() -> dict:
    """J1 — exact free-free characteristic roots."""
    rows = []
    for mode in (1, 2, 3, 4, 5):
        bL = free_free_beam_eigenvalue(mode)
        residual = abs(math.cos(bL) * math.cosh(bL) - 1.0)
        rows.append({"mode": mode, "bL": bL, "residual": residual,
                     "reference_bL": {1: 4.730040744862704, 2: 7.853204624095838,
                                      3: 10.995607838001671, 4: 14.137165491257464,
                                      5: 17.278759657399481}[mode]})
    worst = max(abs(r["bL"] - r["reference_bL"]) for r in rows)
    return {"judge": "J1 exact characteristic roots", "rows": rows,
            "max_abs_dev": worst, "pass": worst < 1e-9}


def judge_convergence() -> dict:
    """J2 — FEM vs closed form, convergence order + GCI."""
    L, I0, m0 = 200.0, 8.0, 3.0e4
    f_exact = uniform_beam_frequency(E_STEEL, I0, m0, L, 1)
    rows = []
    for n_el in (5, 10, 20, 40, 80, 160):
        modal = hull_girder_modal(E_STEEL, np.full(n_el + 1, I0), np.full(n_el + 1, m0), L, 2)
        f = float(modal["frequencies"][0])
        rows.append({"n_elements": n_el, "f1_hz": f, "rel_error": abs(f - f_exact) / f_exact,
                     "rigid_body_max_hz": float(np.max(np.abs(modal["rigid_body_frequencies"])))})
    # observed order from the two finest healthy meshes (round-off starts to
    # pollute beyond ~160 elements at these magnitudes)
    rates = []
    for a, b in zip(rows, rows[1:]):
        if a["rel_error"] > 0 and b["rel_error"] > 0 and b["rel_error"] < a["rel_error"]:
            rates.append(math.log(a["rel_error"] / b["rel_error"], 2))
    p_obs = rates[-1] if rates else float("nan")
    f_fine = rows[-1]["f1_hz"]
    f_coarse = rows[-2]["f1_hz"]
    gci = abs(f_fine - f_coarse) / max(abs(f_fine), 1e-30) / (2 ** p_obs - 1) * 100.0
    return {"judge": "J2 mesh convergence vs closed form", "f_exact_hz": f_exact,
            "f2_exact_hz": uniform_beam_frequency(E_STEEL, I0, m0, L, 2),
            "rows": rows, "observed_order": p_obs, "gci_pct_finest_pair": gci,
            "pass": rows[-1]["rel_error"] < 1e-3 and p_obs > 3.0}


def judge_mode_shape() -> dict:
    """J3 — nodal lines of mode 1 against the exact 0.22416 L / 0.77584 L."""
    L = 200.0
    n_el = 80
    modal = hull_girder_modal(E_STEEL, np.full(n_el + 1, 8.0), np.full(n_el + 1, 3.0e4), L, 1)
    from backend.services.ship_vibration import nodal_points
    nodes = nodal_points(modal["stations"], modal["mode_shapes"][0])
    exact = [0.224159, 0.775841]  # exact zeros of the free-free mode 1, in x/L
    num = [n / L for n in nodes]
    dev = [abs(a - b) for a, b in zip(num, exact)] if len(num) == len(exact) else [1.0]
    # shape agreement at the stations, against the exact analytic shape
    phi_exact = uniform_beam_mode_shape(modal["stations"], L, 1)
    phi_num = modal["mode_shapes"][0]
    # point-wise comparison is sign- and scale-arbitrary: compare after matching
    if float(phi_num[np.argmax(np.abs(phi_num))] * phi_exact[np.argmax(np.abs(phi_exact))]) < 0:
        phi_num = -phi_num
    shape_rms = float(np.sqrt(np.mean((phi_num / np.max(np.abs(phi_num))
                                       - phi_exact / np.max(np.abs(phi_exact))) ** 2)))
    return {"judge": "J3 mode-1 nodal lines", "nodal_x_over_L": num, "exact_x_over_L": exact,
            "max_dev_x_over_L": max(dev), "shape_rms_diff": shape_rms,
            "pass": len(num) == 2 and max(dev) < 2e-3}


def judge_bracket() -> dict:
    """J4 — non-uniform EI(x) inside the rigorous uniform bracket."""
    L = 200.0
    x = np.linspace(0.0, 1.0, 41)
    I = 120.0 * (0.4 + 0.6 * np.exp(-((x - 0.5) / 0.25) ** 2))
    m = np.full(x.size, 4.0e5)
    lo, hi = analytic_bracket(E_STEEL, I, m, L, 1)
    modal = hull_girder_modal(E_STEEL, I, m, L, 3)
    f1 = float(modal["frequencies"][0])
    inside = lo < f1 < hi
    # additionally: uniform input must reproduce the closed form exactly
    f_uni = float(hull_girder_modal(E_STEEL, np.full(41, 8.0), np.full(41, m[0]), L, 1)["frequencies"][0])
    f_closed = uniform_beam_frequency(E_STEEL, 8.0, m[0], L, 1)
    return {"judge": "J4 monotonicity bracket", "bracket_lo_hz": lo, "bracket_hi_hz": hi,
            "fd_f1_hz": f1, "inside_bracket": inside,
            "uniform_fd_hz": f_uni, "uniform_closed_hz": f_closed,
            "uniform_rel_error": abs(f_uni - f_closed) / f_closed,
            "pass": inside and abs(f_uni - f_closed) / f_closed < 1e-6}


def judge_similitude() -> dict:
    """J5 — geometric scaling by lambda => frequency scales by 1/lambda."""
    L = 200.0
    x = np.linspace(0.0, 1.0, 41)
    I0 = 120.0 * (0.4 + 0.6 * np.exp(-((x - 0.5) / 0.25) ** 2))
    m0 = np.full(x.size, 4.0e5)
    f_ref = float(hull_girder_modal(E_STEEL, I0, m0, L, 1)["frequencies"][0])
    rows = []
    for lam in (0.5, 2.0, 3.0):
        f = float(hull_girder_modal(E_STEEL, I0 * lam ** 4, m0 * lam ** 2, L * lam, 1)["frequencies"][0])
        rows.append({"lambda": lam, "f_ratio": f / f_ref, "expected": 1.0 / lam,
                     "rel_error": abs(f / f_ref - 1.0 / lam) * lam})
    worst = max(r["rel_error"] for r in rows)
    return {"judge": "J5 similitude invariance", "rows": rows, "max_rel_error": worst,
            "pass": worst < 1e-6}


def application_case(outdir: str) -> dict:
    """Real-ship screening (synthetic section distribution, absolute grade WITHHELD)."""
    L = CASE["L"]
    x = np.linspace(0.0, 1.0, CASE["n_stations"])
    I = CASE["I_midship"] * (0.35 + 0.65 * np.exp(-((x - 0.5) / 0.22) ** 2))
    res = assess_hull_vibration(E_STEEL, I, CASE["displacement_t"], L,
                                CASE["design_rpm"], CASE["n_blades"])
    lo, hi = analytic_bracket(E_STEEL, I,
                              np.full(I.size, CASE["displacement_t"] * 1000.0 / L), L, 1)
    res["bracket_2node_hz"] = [lo, hi]
    res["case"] = CASE
    return res


def figures(out: dict, outdir: str) -> list:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    os.makedirs(outdir, exist_ok=True)
    paths = []

    # F1 — convergence
    rows = out["J2_convergence"]["rows"]
    n = np.array([r["n_elements"] for r in rows], float)
    err = np.array([r["rel_error"] for r in rows], float)
    fig, ax = plt.subplots(figsize=(6.4, 4.4))
    ax.loglog(n, err, "o-", label="FEM relative error (mode 1)")
    ref = err[0] * (n / n[0]) ** -4
    ax.loglog(n, ref, "k--", lw=1, label=r"$O(h^4)$ reference slope")
    ax.set_xlabel("elements along L")
    ax.set_ylabel(r"$|f_{1}^{FEM}-f_{1}^{exact}|/f_{1}^{exact}$")
    ax.set_title("Free-free beam: FEM vs closed form")
    ax.grid(True, which="both", alpha=0.3)
    ax.legend()
    p = os.path.join(outdir, "fig1_convergence.png")
    fig.tight_layout(); fig.savefig(p, dpi=140); plt.close(fig); paths.append(p)

    # F2 — mode shapes vs exact
    L = 200.0
    modal = hull_girder_modal(E_STEEL, np.full(81, 8.0), np.full(81, 3.0e4), L, 2)
    xs = modal["stations"]
    fig, ax = plt.subplots(figsize=(6.8, 4.4))
    for m_i in range(2):
        ax.plot(xs / L, modal["mode_shapes"][m_i], "-", lw=1.6,
                label=f"FEM mode {m_i+1}: {modal['frequencies'][m_i]:.3f} Hz")
        ax.plot(xs / L, uniform_beam_mode_shape(xs, L, m_i + 1), "--", lw=1.0,
                label=f"exact mode {m_i+1}")
    for xv in (0.224159, 0.775841):
        ax.axvline(xv, color="grey", ls=":", lw=0.8)
    ax.set_xlabel("x / L")
    ax.set_ylabel(r"$\phi$ (unit peak)")
    ax.set_title("Mode shapes: FEM vs exact free-free beam (nodal lines dotted)")
    ax.grid(alpha=0.3); ax.legend(fontsize=8)
    p = os.path.join(outdir, "fig2_modeshapes.png")
    fig.tight_layout(); fig.savefig(p, dpi=140); plt.close(fig); paths.append(p)

    # F3 — application screening: modes vs blade-rate harmonics
    app = out["application"]
    freqs = app["frequencies"]
    bpf = app["f_blade_rate"]
    fig, ax = plt.subplots(figsize=(7.0, 4.4))
    for k in (1, 2, 3, 4):
        ax.axvline(k * bpf, color="crimson", ls="--", lw=1.0)
        ax.text(k * bpf, max(freqs) * 1.02, f"{k}x BPF", color="crimson",
                ha="center", fontsize=8)
    ax.plot(freqs, np.ones(len(freqs)), "s", ms=8, color="navy")
    for i, f in enumerate(freqs):
        ax.annotate(f"mode {i+1}\n{f:.2f} Hz", (f, 1.0), textcoords="offset points",
                    xytext=(0, 12), ha="center", fontsize=7)
    ax.set_ylim(0.6, 1.5); ax.set_yticks([])
    ax.set_xlabel("frequency [Hz]")
    ax.set_title(f"{CASE['name']}: hull-girder modes vs blade-rate family "
                 f"({CASE['design_rpm']:.0f} rpm, Z={CASE['n_blades']})")
    ax.grid(axis="x", alpha=0.3)
    p = os.path.join(outdir, "fig3_bladerate.png")
    fig.tight_layout(); fig.savefig(p, dpi=140); plt.close(fig); paths.append(p)
    return paths


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="docs/reports/m1684_vibration")
    ap.add_argument("--no-figures", action="store_true")
    args = ap.parse_args()
    os.makedirs(args.out, exist_ok=True)

    out = {
        "mission": "m1684",
        "kernel": "backend/services/ship_vibration.py",
        "J1_roots": judge_roots(),
        "J2_convergence": judge_convergence(),
        "J3_mode_shape": judge_mode_shape(),
        "J4_bracket": judge_bracket(),
        "J5_similitude": judge_similitude(),
    }
    out["application"] = application_case(args.out)
    judges = ["J1_roots", "J2_convergence", "J3_mode_shape", "J4_bracket", "J5_similitude"]
    out["summary"] = {
        "n_pass": sum(1 for k in judges if out[k]["pass"]),
        "n_total": len(judges),
        "verdict": "PASS" if all(out[k]["pass"] for k in judges) else "FAIL",
    }
    if not args.no_figures:
        out["figures"] = [os.path.relpath(p) for p in figures(out, args.out)]

    path = os.path.join(args.out, "verification.json")

    def _default(o):
        if isinstance(o, (np.floating, np.integer)):
            return o.item()
        if isinstance(o, np.ndarray):
            return o.tolist()
        raise TypeError(type(o))

    with open(path, "w") as fh:
        json.dump(out, fh, indent=2, ensure_ascii=False, default=_default)
    print(json.dumps(out["summary"], ensure_ascii=False))
    for k in judges:
        print(f"  {k}: pass={out[k]['pass']}")
    print("application f1 = %.4f Hz, BPF = %.3f Hz, coincidences = %d"
          % (out["application"]["f_2node_vertical"], out["application"]["f_blade_rate"],
             out["application"]["n_coincident"]))
    print("written:", path)
    return 0 if out["summary"]["verdict"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())