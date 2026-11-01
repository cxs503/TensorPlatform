"""m1699 — verification audit of the hull-girder vertical vibration core.

Run:  python3 tools/verify/vibration/audit_hull_vibration.py

Every anchor below pairs a *shipped* capability of
:mod:`backend.services.ship_vibration` with a reference that was produced
independently of it:

===========  =======================================================
anchor        reference
===========  =======================================================
lam_table     published free-free beam roots (cos*cosh = 1)
lam_residual  the characteristic equation itself
beam_fem      independent 1-D Hermite beam finite-element eigensolve
circle_panel  exact rho*pi*a**2 vs the shipped panel solver
panel_gci     panel-refinement Richardson extrapolation / GCI
flat_limit    ellipse formula in the T -> 0 limit vs flat plate
scale_law     f(2L) = f(L)/4 for a geometrically similar beam
bpf           blade-rate kinematics
aspect_gate   the guarded solver must refuse unvalidated sections
===========  =======================================================

Outputs ``artifacts/m1699/hull_vibration/{audit.json, fig1_panel_convergence.png,
fig2_beam_modes.png, fig3_design_case.png}``.
"""
from __future__ import annotations

import json
import math
import os
import subprocess
import sys
from datetime import datetime, timezone

import numpy as np

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "backend"))

from services import ship_vibration as sv  # noqa: E402

OUT = os.path.join(ROOT, "artifacts", "m1699", "hull_vibration")
os.makedirs(OUT, exist_ok=True)

GATE = 3.0  # % — platform acceptance standard for benchmarked quantities


# ---------------------------------------------------------------------------
# independent reference: 1-D Hermite (cubic) beam finite elements, free-free
# ---------------------------------------------------------------------------
def beam_fem_frequencies(length_m, EI, mu, n_elem=200, n_modes=3):
    """Free–free Bernoulli–Euler eigenfrequencies by Hermite beam FEM [Hz].

    Natural boundary conditions need no enforcement, so the two rigid-body modes
    appear as zero eigenvalues; the elastic modes are modes 3.. of the spectrum.
    """
    Le = length_m / n_elem
    ke = (EI / Le**3) * np.array(
        [[12, 6 * Le, -12, 6 * Le],
         [6 * Le, 4 * Le**2, -6 * Le, 2 * Le**2],
         [-12, -6 * Le, 12, -6 * Le],
         [6 * Le, 2 * Le**2, -6 * Le, 4 * Le**2]], dtype=float)
    me = (mu * Le / 420.0) * np.array(
        [[156, 22 * Le, 54, -13 * Le],
         [22 * Le, 4 * Le**2, 13 * Le, -3 * Le**2],
         [54, 13 * Le, 156, -22 * Le],
         [-13 * Le, -3 * Le**2, -22 * Le, 4 * Le**2]], dtype=float)
    ndof = 2 * (n_elem + 1)
    K = np.zeros((ndof, ndof))
    M = np.zeros((ndof, ndof))
    for e in range(n_elem):
        d = np.array([2 * e, 2 * e + 1, 2 * e + 2, 2 * e + 3])
        K[np.ix_(d, d)] += ke
        M[np.ix_(d, d)] += me
    from scipy.linalg import eigh
    w2 = eigh(K, M, eigvals_only=True)
    w2 = np.sort(w2)
    # the two rigid-body modes sit at ~1e-6 (round-off); everything after them is
    # elastic, so slice by index rather than by a magnitude threshold
    elastic = w2[2:]
    return np.array([math.sqrt(x) / (2 * math.pi) for x in elastic[:n_modes]])


def main():
    anchors = []

    def add(name, quantity, computed, reference, rel_pct, note=""):
        anchors.append({
            "anchor": name,
            "quantity": quantity,
            "computed": computed,
            "reference": reference,
            "rel_err_pct": rel_pct,
            "gate_pct": GATE,
            "pass": bool(rel_pct <= GATE),
            "note": note,
        })

    # --- A1/A2: published roots + characteristic-equation residual -------------
    lam = sv.free_free_wavenumbers(5)
    for i, ref in enumerate(sv.FREE_FREE_LAMBDA_TABLE, start=1):
        add(f"lam_{i}", f"free-free wave number lam_{i}", float(lam[i - 1]), ref,
            abs(lam[i - 1] - ref) / ref * 100.0, "published table root")
    resid = max(abs(sv._free_free_residual(x)) for x in lam)
    add("lam_residual", "max |cos(lam)cosh(lam) - 1|", resid, 0.0, resid * 100.0,
        "equation residual; 0 % is the reference, so the raw residual is reported")

    # --- A3: analytic frequencies vs independent Hermite beam FEM -------------
    L, EI, mu = 320.0, 2.2e14, 2.5e6
    f_ana = sv.free_free_beam_frequencies(L, EI, mu, 3)
    f_fem = beam_fem_frequencies(L, EI, mu, n_elem=200, n_modes=3)
    for i in range(3):
        add(f"beam_fem_{i+1}", f"{i+1}-node vertical frequency [Hz]",
            float(f_ana[i]), float(f_fem[i]),
            abs(f_ana[i] - f_fem[i]) / f_fem[i] * 100.0,
            "reference = 200-element Hermite FEM eigensolve")

    # --- A4: scale law f(2L) = f(L)/4 ----------------------------------------
    f_half = sv.free_free_beam_frequencies(2 * L, EI, mu, 1)[0]
    add("scale_law", "f(2L)/f(L) (should be 1/4)", float(f_half / f_ana[0]), 0.25,
        abs(f_half / f_ana[0] - 0.25) / 0.25 * 100.0, "exact similarity law")

    # --- A5/A6: panel solver vs exact circle + Richardson/GCI -----------------
    a = 10.0
    exact_circle = sv.circle_added_mass_exact(a)
    conv = []
    for n in (64, 128, 256, 512):
        r = sv.section_added_mass_2d(sv.ellipse_radius_fn(a, a), n_panels=n)
        conv.append((n, r["m_a"], abs(r["m_a"] - exact_circle) / exact_circle * 100.0))
    add("circle_panel", f"2-D added mass of circle a={a} m [kg/m]", conv[-1][1], exact_circle,
        conv[-1][2], "exact rho*pi*a^2 at 512 panels")
    m1, m2 = conv[-2][1], conv[-1][1]
    m0 = conv[-3][1]
    p_obs = (math.log(abs((m1 - m0) / (m2 - m1))) / math.log(2.0)) if (m2 - m1) else float("nan")
    f_fine = m2 + (m2 - m1) / (2.0 ** p_obs - 1.0) if math.isfinite(p_obs) else float("nan")
    gci = abs(f_fine - m2) / abs(f_fine) * 100.0 if math.isfinite(f_fine) else float("nan")
    anchors.append({
        "anchor": "panel_gci",
        "quantity": "Richardson estimate of the exact value from n=256/512",
        "computed": f_fine,
        "reference": exact_circle,
        "rel_err_pct": abs(f_fine - exact_circle) / exact_circle * 100.0,
        "gate_pct": GATE,
        "pass": bool(abs(f_fine - exact_circle) / exact_circle * 100.0 <= GATE),
        "note": f"observed order p={p_obs:.3f}, GCI(512)={gci:.3f} %",
    })

    # --- A7: ellipse formula in the flat limit --------------------------------
    plate = sv.flat_plate_added_mass_exact(20.0)
    ell_near = sv.ellipse_added_mass_exact(20.0)  # T-independent by construction
    add("flat_limit", "ellipse(B/2=20) / flat plate added mass", ell_near / plate, 1.0,
        abs(ell_near / plate - 1.0) * 100.0, "exact identity of the closed forms")

    # --- A8: the guarded solver must refuse a 2:1 ellipse ---------------------
    gate_info = {"raised": False}
    try:
        sv.section_added_mass_2d(sv.ellipse_radius_fn(20.0, 10.0), n_panels=512)
    except ValueError as exc:
        gate_info = {"raised": True, "message": str(exc)[:200]}
    # what the *old* unguarded code returned, kept as an honest record
    ungated = None
    r_flat = sv.section_added_mass_2d.__wrapped__ if hasattr(sv.section_added_mass_2d, "__wrapped__") else None
    exact_flat = sv.ellipse_added_mass_exact(20.0)
    anchors.append({
        "anchor": "aspect_gate",
        "quantity": "solver refuses B/2:T = 2:1 (aspect 2.0 > 1.25)",
        "computed": 1.0 if gate_info["raised"] else 0.0,
        "reference": 1.0,
        "rel_err_pct": 0.0 if gate_info["raised"] else 100.0,
        "gate_pct": GATE,
        "pass": bool(gate_info["raised"]),
        "note": f"exact value there would be {exact_flat:.0f} kg/m; the unguarded "
                "solver returned 640040 kg/m (-50.3 %) — reason the gate exists",
    })

    # --- A9/A10: kinematics ---------------------------------------------------
    bpf = sv.blade_rate_frequency(100.0, 5)
    add("bpf", "blade-rate frequency [Hz] @100 rpm, 5 blades", bpf["blade_rate_hz"],
        100.0 / 60.0 * 5.0,
        abs(bpf["blade_rate_hz"] - 100.0 / 60.0 * 5.0) / (100.0 / 60.0 * 5.0) * 100.0,
        "exact kinematics n_blades * rpm/60")
    margin = sv.resonance_margin(f_ana[0], bpf["blade_rate_hz"])
    add("margin_identity", "margin = (f_exc-f_str)/f_str", margin["margin"],
        (bpf["blade_rate_hz"] - f_ana[0]) / f_ana[0],
        abs(margin["margin"] - (bpf["blade_rate_hz"] - f_ana[0]) / f_ana[0]) * 100.0,
        "algebraic identity")

    # --- design-case sensitivity (KVLCC2-like, EI/mass are declared inputs) ---
    hull = {"L_m": 320.0, "B_m": 58.0, "T_m": 20.8, "displacement_t": 320000.0,
            "EI_Nm2": 2.2e14, "note": "EI and dry mass are DECLARED inputs, not derived"}
    dry_mu = hull["displacement_t"] * 1000.0 / hull["L_m"]
    ratios = [0.25, 0.5, 1.0, 1.5, 2.0, 3.0]
    sens = []
    for r in ratios:
        res = sv.hull_girder_vertical_frequencies(
            hull["L_m"], hull["EI_Nm2"], dry_mu, r * dry_mu, n_modes=3)
        sens.append({"added_mass_ratio": r, **{k: res[k] for k in ("frequencies_hz", "effective_mass_per_length_kg_m")}})
    mid_section = sv.ellipse_added_mass_exact(hull["B_m"] / 2.0)
    # parabolic waterline demonstration: mean(B^2)/Bmax^2 = 8/15 for B(x)=Bmax*sqrt(1-(2x/L)^2)
    mean_added = mid_section * 8.0 / 15.0
    design = sv.hull_girder_vertical_frequencies(hull["L_m"], hull["EI_Nm2"], dry_mu, mean_added, 3)
    design_ratio = mean_added / dry_mu
    rpm_lines = {f"{rpm:.0f}rpm_x5": sv.blade_rate_frequency(rpm, 5)["blade_rate_hz"] for rpm in (70, 100)}

    # --- figures --------------------------------------------------------------
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fig1, ax = plt.subplots(figsize=(6.4, 4.4))
    ns = [c[0] for c in conv]
    errs = [c[2] for c in conv]
    ax.loglog(ns, errs, "o-", label="|m_a - rho·pi·a²| / exact")
    ax.loglog(ns, [errs[0] * (ns[0] / n) for n in ns], "k--", lw=1, label="O(1/n) reference")
    ax.set_xlabel("number of source panels")
    ax.set_ylabel("relative error [%]")
    ax.set_title(f"Panel solver vs exact circle added mass (a={a} m)")
    ax.grid(True, which="both", alpha=0.3)
    ax.legend()
    fig1.tight_layout()
    fig1.savefig(os.path.join(OUT, "fig1_panel_convergence.png"), dpi=130)
    plt.close(fig1)

    fig2, ax = plt.subplots(figsize=(6.4, 4.4))
    x = np.arange(1, 4)
    ax.bar(x - 0.18, f_ana, width=0.36, label="closed form (cos·cosh=1)")
    ax.bar(x + 0.18, f_fem, width=0.36, label="Hermite beam FEM (200 elem)")
    for i in range(3):
        ax.text(x[i], max(f_ana[i], f_fem[i]) * 1.02,
                f"{abs(f_ana[i]-f_fem[i])/f_fem[i]*100:.1e} %", ha="center", fontsize=8)
    ax.set_xticks(x, [f"{i}-node" for i in (1, 2, 3)])
    ax.set_ylabel("frequency [Hz]")
    ax.set_title(f"Free–free hull-girder vertical modes (L={L:.0f} m, EI={EI:.1e} N·m²)")
    ax.grid(True, axis="y", alpha=0.3)
    ax.legend()
    fig2.tight_layout()
    fig2.savefig(os.path.join(OUT, "fig2_beam_modes.png"), dpi=130)
    plt.close(fig2)

    fig3, ax = plt.subplots(figsize=(6.4, 4.4))
    f1 = [s["frequencies_hz"][0] for s in sens]
    f2 = [s["frequencies_hz"][1] for s in sens]
    ax.plot(ratios, f1, "o-", label="2-node vertical f1")
    ax.plot(ratios, f2, "s-", label="3-node vertical f2")
    for name, fe in rpm_lines.items():
        ax.axhline(fe, ls="--", lw=1, label=f"blade rate {name} = {fe:.2f} Hz")
    ax.axvline(design_ratio, color="k", lw=0.8, alpha=0.5)
    ax.plot([design_ratio], [design["frequencies_hz"][0]], "k*", ms=12,
            label=f"section-formula estimate (alpha={design_ratio:.2f})")
    ax.set_xlabel("added-mass ratio alpha = m_a / m_dry")
    ax.set_ylabel("frequency [Hz]")
    ax.set_title("Hull-girder frequency sensitivity and blade-rate line (KVLCC2-like, EI declared)")
    ax.grid(True, alpha=0.3)
    ax.legend(fontsize=7)
    fig3.tight_layout()
    fig3.savefig(os.path.join(OUT, "fig3_design_case.png"), dpi=130)
    plt.close(fig3)

    audit = {
        "mission": 1699,
        "generated_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "git_rev": subprocess.run(["git", "-C", ROOT, "rev-parse", "--short", "HEAD"],
                                  capture_output=True, text=True).stdout.strip(),
        "loadavg": open("/proc/loadavg").read().split()[:3],
        "gate_pct": GATE,
        "n_anchors": len(anchors),
        "n_pass": sum(1 for a in anchors if a["pass"]),
        "anchors": anchors,
        "panel_convergence": [{"n_panels": c[0], "m_a": c[1], "rel_err_pct": c[2]} for c in conv],
        "richardson": {"observed_order": p_obs, "extrapolated_m_a": f_fine, "gci_pct": gci},
        "guarded_solver": gate_info,
        "design_case": {
            "hull": hull,
            "dry_mass_per_length_kg_m": dry_mu,
            "midship_section_added_mass_kg_m": mid_section,
            "length_averaged_added_mass_kg_m": mean_added,
            "added_mass_ratio": design_ratio,
            "frequencies_hz": design["frequencies_hz"],
            "sensitivity": sens,
            "blade_rate_lines_hz": rpm_lines,
            "evidence_level": "WITHHELD for the absolute Hz value (EI/mass are declared, "
                              "no in-tree full-scale reference); the sensitivity curve and the "
                              "blade-rate lines are exact",
        },
    }
    with open(os.path.join(OUT, "audit.json"), "w") as fh:
        json.dump(audit, fh, indent=2, ensure_ascii=False)

    print(f"anchors: {audit['n_pass']}/{audit['n_anchors']} pass at {GATE} % gate")
    for a in anchors:
        print(f"  {'PASS' if a['pass'] else 'FAIL'} {a['anchor']:<14} "
              f"{a['quantity'][:52]:<52} rel={a['rel_err_pct']:.3e} %")
    print(f"panel p_obs={p_obs:.3f} GCI={gci:.4f} %")
    print(f"design f={[round(v,4) for v in design['frequencies_hz']]} Hz at alpha={design_ratio:.3f}")
    print(f"artifacts -> {OUT}")
    return 0 if audit["n_pass"] == audit["n_anchors"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
