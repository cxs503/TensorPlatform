"""Machine-checkable verification harness for the hull-girder strength module.

Every benchmark below compares the implementation against a reference that is
*derived independently* - a closed-form identity, a textbook formula, a rule
formula property, or a physical bound - so a PASS means the code reproduces
mathematics/physics and not its own output.

Acceptance rules (platform standard, `docs/verification/HULL_GIRDER_STRENGTH.md`):

  * numerical-vs-closed-form:               relative error < 3 %
  * exact-algorithm cross-check (float):    relative error < 1e-9
  * bounds / monotonicity / continuity:     boolean criteria, must hold everywhere

Usage::

    python3 -m tools.verify.hull_girder.benchmarks            # print table
    python3 -m tools.verify.hull_girder.benchmarks --write    # + artifacts/figures
"""

from __future__ import annotations

import argparse
import json
import math
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Dict, List

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

from . import loads, section, smith  # noqa: E402

REL_TOL_NUMERIC = 0.03          # 3 %
REL_TOL_EXACT = 1e-9            # exact algorithm cross-check
SIGMA_Y = 235e6                 # mild steel, S11 k = 1
E_STEEL = 206e9
NU = 0.3

ARTIFACT_DIR = Path("artifacts/hull_girder")
FIG_DIR = ARTIFACT_DIR / "figures"


def _rel(a: float, b: float) -> float:
    if b == 0:
        return abs(a)
    return abs(a - b) / abs(b)


def _rec(bid: str, name: str, reference: str, value, ref_value, rel_err,
         threshold: float, passed: bool, note: str = "", kind: str = "numeric") -> Dict:
    return {
        "id": bid, "name": name, "kind": kind, "reference": reference,
        "value": value, "reference_value": ref_value,
        "rel_error": rel_err, "threshold": threshold,
        "passed": bool(passed), "note": note,
    }


# --------------------------------------------------------------------- B1 / B2
def bench_section_properties() -> List[Dict]:
    """B1: three independent section-property algorithms on the same box."""
    B, D, t = 20.0, 12.0, 0.020
    sec = section.make_box_section(B=B, D=D, t=t)
    a_rect, z_rect, i_rect = sec.rect_moments()
    a_poly, z_poly, i_poly = sec.polygon_moments()
    a_ref, z_ref, i_ref = section.box_closed_form(B, D, t)

    out = [
        _rec("B1a", "rectangle integration vs closed-form area",
             "A = B D - b h  (b=B-2t, h=D-2t)", a_rect, a_ref, _rel(a_rect, a_ref),
             REL_TOL_NUMERIC, _rel(a_rect, a_ref) < REL_TOL_NUMERIC,
             f"box B={B} D={D} t={t*1e3:.0f} mm"),
        _rec("B1b", "rectangle integration vs closed-form neutral axis",
             "z_c = D/2", z_rect, z_ref, _rel(z_rect, z_ref),
             REL_TOL_NUMERIC, _rel(z_rect, z_ref) < REL_TOL_NUMERIC),
        _rec("B1c", "rectangle integration vs closed-form inertia",
             "I = (B D^3 - b h^3)/12", i_rect, i_ref, _rel(i_rect, i_ref),
             REL_TOL_NUMERIC, _rel(i_rect, i_ref) < REL_TOL_NUMERIC),
        _rec("B1d", "Green-theorem polygon vs rectangle integration (area)",
             "independent algorithms must agree to float precision", a_poly, a_rect,
             _rel(a_poly, a_rect), REL_TOL_EXACT, _rel(a_poly, a_rect) < REL_TOL_EXACT,
             "algorithm cross-check", kind="exact"),
        _rec("B1e", "Green-theorem polygon vs rectangle integration (inertia)",
             "independent algorithms must agree to float precision", i_poly, i_rect,
             _rel(i_poly, i_rect), REL_TOL_EXACT, _rel(i_poly, i_rect) < REL_TOL_EXACT,
             "algorithm cross-check", kind="exact"),
    ]
    return out


def bench_discretisation() -> List[Dict]:
    """B2: strip-model discretisation error on the *realistic* double-hull section."""
    sec = section.make_midship_section()
    a_ref, z_ref, i_ref = sec.rect_moments()
    out = []
    for n in (10, 40, 100):
        a, z, i = sec.strip_props(n_strips=n)
        out.append(_rec(f"B2-{n}", f"strip model vs exact, n_strips={n}",
                        "exact rectangle integration of the same section", i, i_ref,
                        _rel(i, i_ref), REL_TOL_NUMERIC, _rel(i, i_ref) < REL_TOL_NUMERIC,
                        f"n_strips={n}; area err {_rel(a, a_ref):.2e}, z_c err {_rel(z, z_ref):.2e}"))
    return out


# -------------------------------------------------------------------------- B3
def bench_smith_vs_rigid_plastic() -> List[Dict]:
    """B3: with stocky plating (sigma_cr >= sigma_y) Smith must reproduce M_p.

    The element law is then elastic - perfectly plastic, so the hand-derived
    rigid-plastic solution ``M_p = sigma_y Z_p`` is the exact limit of the
    incremental solver.  Plating is sized so that ``sigma_cr`` exceeds ``sigma_y``
    with a comfortable margin - the check is that the *solver* is right, so the
    regime has to be the one the identity holds in.
    """
    out = []
    cases = [
        ("box-B20", section.make_box_section(B=20.0, D=12.0, t=0.030, panel_breadth=0.30), SIGMA_Y),
        ("box-B30", section.make_box_section(B=30.0, D=18.0, t=0.040, panel_breadth=0.35), SIGMA_Y),
        ("double-hull", section.make_midship_section(panel_breadth=0.35), SIGMA_Y),
        # a lower strength steel makes the same geometry stocky as well
        ("double-hull-S235-panel065", section.make_midship_section(panel_breadth=0.65), 120e6),
    ]
    for name, sec, sy in cases:
        res = smith.collapse_analysis(sec, sigma_y=sy, E=E_STEEL, nu=NU, mode="euler", n_strips=40)
        zp, z_eq = smith.plastic_modulus(sec.parts)
        m_p = sy * zp
        err = _rel(res["M_u"], m_p)
        scr_min = min(e.sigma_cr for e in res["elements"])
        out.append(_rec(f"B3-{name}", f"Smith ultimate moment vs rigid-plastic M_p ({name})",
                        "M_p = sigma_y Z_p (exact rigid-plastic limit, LEPP element law)",
                        res["M_u"], m_p, err, REL_TOL_NUMERIC,
                        err < REL_TOL_NUMERIC and scr_min >= sy,
                        f"sigma_y={sy/1e6:.0f} MPa, min sigma_cr={scr_min/1e6:.0f} MPa "
                        f"(>= sigma_y: stocky), Z_p={zp:.4f} m^3, z_eq={z_eq:.3f} m, "
                        f"M_u/M_p={res['M_u']/m_p:.4f}"))
    return out


# -------------------------------------------------------------------------- B4
def bench_bounds_and_two_flange() -> List[Dict]:
    """B4: rigorous bounds + the buckling-regime analytic closed form.

    Two corrections against naive intuition, both *physics*:

    * ``M_u <= sigma_y Z_p`` is a theorem (any stress state with ``|sigma| <=
      sigma_y`` gives a moment bounded by ``sigma_y Z_p`` about the equal-area
      axis).  It is checked at every slenderness level.
    * ``M_u >= M_y`` (first yield of the *full* section) is **not** a theorem when
      the compression flange buckles below yield: a slender flange carries far
      less than ``sigma_y`` and the collapse moment can sit below the full-section
      first-yield moment.  An earlier revision of this harness asserted the
      opposite and was falsified (-91 % at 3.0 m panel breadth); the regime is
      therefore judged against the two-flange closed form instead, which is the
      correct reference in the buckling regime.
    """
    sec0 = section.make_midship_section()
    panel = [0.6, 0.8, 1.0, 1.2, 1.5, 2.0, 2.5, 3.0]
    mus, mys, mps = [], [], []
    for pb in panel:
        sec = section.make_midship_section(panel_breadth=pb)
        r = smith.collapse_analysis(sec, sigma_y=SIGMA_Y, E=E_STEEL, nu=NU, mode="euler")
        mus.append(r["M_u"]); mys.append(r["M_y"]); mps.append(r["M_p"])
    above_zero = all(m > 0.0 for m in mus)
    below_plastic = all(m <= hi * (1 + 1e-9) for m, hi in zip(mus, mps))
    monotone = all(mus[i] >= mus[i + 1] - 1e-9 for i in range(len(mus) - 1))

    out = [
        _rec("B4a", "upper bound M_u <= M_p = sigma_y Z_p over 8 slenderness levels",
             "theorem: |sigma| <= sigma_y bounds the moment by sigma_y Z_p",
             f"{max(mus)/1e9:.3f} GNm (max)", f"{min(mps)/1e9:.3f} GNm (min M_p)",
             0.0, 0.0, above_zero and below_plastic,
             f"panel breadth {panel[0]}..{panel[-1]} m; M_u/M_p {mus[0]/mps[0]:.3f} -> "
             f"{mus[-1]/mps[-1]:.3f}; M_u/M_y {mus[0]/mys[0]:.3f} -> {mus[-1]/mys[-1]:.3f}",
             kind="boolean"),
        _rec("B4b", "M_u monotone decreasing in plate slenderness (b/t)",
             "buckling stress ~ (t/b)^2 -> thinner panels cannot raise M_u",
             f"M_u/M_p {mus[0]/mps[0]:.3f} -> {mus[-1]/mps[-1]:.3f}", "monotone sequence",
             0.0, 0.0, monotone, f"levels {panel}", kind="boolean"),
    ]
    out += bench_two_flange()
    return out


def bench_two_flange() -> List[Dict]:
    """B4c: buckling-regime closed form for a two-flange girder, both directions."""
    out = []
    cases = [
        ("tdeck20-tbot30-b1200", dict(t_deck=0.020, t_bottom=0.030, panel_breadth=1.2)),
        ("tdeck25-tbot40-b1500", dict(t_deck=0.025, t_bottom=0.040, panel_breadth=1.5)),
    ]
    for name, kw in cases:
        sec = section.make_two_flange_section(B=20.0, D=12.0, **kw)
        a_deck = 20.0 * kw["t_deck"]
        a_bot = 20.0 * kw["t_bottom"]
        scr_deck = section.plate_buckling_stress(kw["t_deck"], kw["panel_breadth"], E=E_STEEL, nu=NU)
        scr_bot = section.plate_buckling_stress(kw["t_bottom"], kw["panel_breadth"], E=E_STEEL, nu=NU)
        # direction -1 (sagging): deck is the compression flange
        # direction +1 (hogging): bottom is the compression flange
        for direction, area_comp, area_ten, scr, tag in (
                (-1, a_deck, a_bot, scr_deck, "sagging"),
                (+1, a_bot, a_deck, scr_bot, "hogging")):
            su = min(SIGMA_Y, scr)
            res = smith.collapse_analysis(sec, sigma_y=SIGMA_Y, E=E_STEEL, nu=NU,
                                          mode="euler", n_strips=40, direction=direction)
            ref = smith.two_flange_closed_form(area_comp, area_ten, 12.0, SIGMA_Y, su)
            err = _rel(abs(res["M_u"]), ref["M"])
            out.append(_rec(
                f"B4c-{name}-{tag}",
                f"Smith vs hand-derived two-flange collapse moment ({tag})",
                "M = min(sigma_u A_comp, sigma_y A_ten) * D  (flange-force couple)",
                abs(res["M_u"]), ref["M"], err, REL_TOL_NUMERIC,
                err < REL_TOL_NUMERIC and res["na_failures"] == 0,
                f"{ref['regime']}; sigma_u(comp)={su/1e6:.1f} MPa; F={ref['F']/1e6:.2f} MN; "
                f"solver z_na={res['z_na_u']:.4f} m, na_failures={res['na_failures']}"))
    return out


# -------------------------------------------------------------------------- B5
def bench_s11_coefficient() -> List[Dict]:
    """B5: S11 wave coefficient - continuity, flat plateau, endpoint value."""
    out = []
    c300m = loads.s11_C(300.0 - 1e-9)
    c300p = loads.s11_C(300.0 + 1e-9)
    c350m = loads.s11_C(350.0 - 1e-9)
    c350p = loads.s11_C(350.0 + 1e-9)
    e300 = abs(c300p - c300m) / 10.75
    e350 = abs(c350p - c350m) / 10.75
    out.append(_rec("B5a", "C(L) continuity at L = 300 m", "both branches -> 10.75",
                    c300p, c300m, e300, REL_TOL_NUMERIC, e300 < REL_TOL_NUMERIC,
                    f"C(300-)={c300m:.6f}, C(300+)={c300p:.6f}"))
    out.append(_rec("B5b", "C(L) continuity at L = 350 m", "both branches -> 10.75",
                    c350p, c350m, e350, REL_TOL_NUMERIC, e350 < REL_TOL_NUMERIC,
                    f"C(350-)={c350m:.6f}, C(350+)={c350p:.6f}"))
    plateau = [loads.s11_C(x) for x in (305.0, 320.0, 340.0, 350.0)]
    flat = max(plateau) - min(plateau) < 1e-12
    out.append(_rec("B5c", "flat plateau 10.75 on 300 < L <= 350", "C = 10.75 (S11 table)",
                    f"{min(plateau):.6f}..{max(plateau):.6f}", 10.75,
                    abs(plateau[1] - 10.75) / 10.75, REL_TOL_NUMERIC,
                    flat and abs(plateau[1] - 10.75) < REL_TOL_NUMERIC, kind="boolean"))
    up = all(loads.s11_C(x) <= loads.s11_C(x + 0.5) + 1e-12 for x in
             [90 + 0.5 * i for i in range(int((350 - 90) / 0.5))])
    down = all(loads.s11_C(x) >= loads.s11_C(x + 0.5) - 1e-12 for x in
               [350 + 0.5 * i for i in range(int((500 - 350) / 0.5))])
    out.append(_rec("B5d", "C(L) rises on 90..350 m and falls on 350..500 m",
                    "closed-form trend of the two branches (not globally monotone)",
                    f"up={up}, down={down}", "up and down", 0.0, 0.0, up and down,
                    "C(90)=%.3f, C(350)=%.3f, C(500)=%.3f" % (
                        loads.s11_C(90.0), loads.s11_C(350.0), loads.s11_C(500.0)),
                    kind="boolean"))
    m = loads.s11_wave_bending_moments(320.0, 58.0, 0.8098)
    ratio_err = _rel(m["hogging_over_sagging"], loads.HOGGING_RATIO)
    out.append(_rec("B5e", "hogging/sagging moment ratio", "0.19/0.11 = 1.72727 (S11)",
                    m["hogging_over_sagging"], loads.HOGGING_RATIO, ratio_err,
                    REL_TOL_EXACT, ratio_err < REL_TOL_EXACT, kind="exact"))
    return out


# -------------------------------------------------------------------------- B6
def bench_vlcc_stress_check() -> List[Dict]:
    """B6: S11 wave moment applied to the idealised VLCC section -> girder stress."""
    sec = section.make_midship_section(B=58.0, D=30.5, b_inner=46.0, h_inner_bottom=3.0,
                                       h_inner_top=27.0, t_deck=0.035, t_bottom=0.038,
                                       t_side_outer=0.025, t_inner_bottom=0.022,
                                       t_inner_deck=0.022, t_bulkhead=0.020)
    z_bot, z_deck = sec.section_moduli()
    m = loads.s11_wave_bending_moments(L=320.0, B=58.0, Cb=0.8098)
    sig_hog_deck = loads.bending_stress(m["M_wh"], z_deck)      # hogging: deck tension
    sig_hog_bot = loads.bending_stress(m["M_wh"], z_bot)
    sig_sag_deck = loads.bending_stress(m["M_ws"], z_deck)      # sagging: deck compression
    allowable = loads.rule_allowable_stress(320.0)
    worst = max(sig_hog_deck, sig_hog_bot, abs(sig_sag_deck))
    out = [
        _rec("B6a", "wave-only girder stress vs S11 allowable 175/k MPa (necessary condition)",
             "sigma = M_w / Z <= 175 MPa (still-water moment NOT included)",
             worst / 1e6, allowable / 1e6, 0.0, 0.0, worst <= allowable,
             f"Z_bottom={z_bot:.2f} m^3, Z_deck={z_deck:.2f} m^3; "
             f"sigma_hog(deck)={sig_hog_deck/1e6:.1f} MPa, sigma_hog(bottom)={sig_hog_bot/1e6:.1f} MPa, "
             f"sigma_sag(deck)={abs(sig_sag_deck)/1e6:.1f} MPa",
             kind="boolean"),
    ]
    # independent recomputation of the same stress by hand-carried formula
    c = 10.75
    m_wh_hand = 0.19 * c * 320.0 ** 2 * 58.0 * 0.8098 * 1e3
    out.append(_rec("B6b", "S11 hogging moment vs hand-carried formula",
                    "M_wh = 0.19 C L^2 B Cb [kN m] with C = 10.75",
                    m["M_wh"], m_wh_hand, _rel(m["M_wh"], m_wh_hand), REL_TOL_EXACT,
                    _rel(m["M_wh"], m_wh_hand) < REL_TOL_EXACT,
                    f"L=320 m, B=58 m, Cb=0.8098 -> {m['M_wh']/1e9:.3f} GNm", kind="exact"))
    return out


# -------------------------------------------------------------------------- B7
def bench_platform_capability(probe: bool = True) -> List[Dict]:
    """B7: platform-side evidence grade for the structural discipline (404 probe)."""
    paths = ["calculations/structure", "calculations/strength",
             "calculations/hull_girder", "structure/strength",
             "calculations/section_modulus"]
    codes = {}
    if probe:
        for p in paths:
            body = json.dumps({"L": 320, "B": 58, "Cb": 0.81, "D": 30.5}).encode()
            req = urllib.request.Request(f"http://127.0.0.1:8001/api/v1/{p}", data=body,
                                         headers={"Content-Type": "application/json"})
            try:
                with urllib.request.urlopen(req, timeout=5) as r:
                    codes[p] = r.status
            except urllib.error.HTTPError as e:
                codes[p] = e.code
            except Exception as e:  # host/port down: cannot judge
                codes[p] = f"ERR:{type(e).__name__}"
    n404 = sum(1 for v in codes.values() if v == 404)
    judged = len(codes)
    supported = judged > 0 and n404 < judged
    grade = "NOT_SUPPORTED" if (judged and n404 == judged) else (
        "UNKNOWN (probe unavailable)" if not judged else "ENDPOINT PRESENT")
    return [_rec("B7", "platform exposure of structural-strength capability",
                 "HTTP probe of 5 candidate /api/v1 paths on :8001",
                 grade, "all 404 => NOT_SUPPORTED", 0.0, 0.0, not supported,
                 f"codes={codes}", kind="boolean")]


# ---------------------------------------------------------------------- figures
def make_figures(mode_results: Dict[str, dict]) -> List[Path]:
    FIG_DIR.mkdir(parents=True, exist_ok=True)
    figs: List[Path] = []

    # fig1: stress distribution across the section at the collapse state
    sec = mode_results["section"]
    res = mode_results["collapse"]
    prof = res["sigma_profile"]
    fig, ax = plt.subplots(figsize=(7.0, 4.6))
    zs = [z for _, z, _ in prof]
    ss = [s / 1e6 for _, _, s in prof]
    sc = ax.scatter(ss, zs, c=ss, cmap="coolwarm", s=8)
    ax.axvline(0, color="k", lw=0.8)
    ax.axhline(res["z_na_u"], color="b", ls="--", lw=1.0,
               label=f"collapsed NA z={res['z_na_u']:.2f} m")
    ax.set_xlabel("element stress [MPa] (tension +)")
    ax.set_ylabel("z above baseline [m]")
    ax.set_title(f"Stress state at ultimate curvature: {sec.name}")
    fig.colorbar(sc, ax=ax, label="MPa")
    ax.legend(loc="upper right", fontsize=8)
    fig.tight_layout()
    p = FIG_DIR / "fig1_stress_profile_ultimate.png"
    fig.savefig(p, dpi=130)
    plt.close(fig)
    figs.append(p)

    # fig2: moment-curvature for several panel breadths + bounds
    fig, ax = plt.subplots(figsize=(7.0, 4.6))
    for pb, r in mode_results["mc"].items():
        ax.plot(r["kappa"], [m / 1e9 for m in r["M"]], lw=1.4, label=f"panel {pb} m")
    r0 = mode_results["mc"][0.6]
    ax.axhline(r0["M_y"] / 1e9, color="gray", ls=":", lw=1.0, label="M_y (first yield)")
    ax.axhline(r0["M_p"] / 1e9, color="k", ls="--", lw=1.0, label="M_p (rigid-plastic)")
    ax.set_xscale("log")
    ax.set_xlabel("curvature kappa [1/m]")
    ax.set_ylabel("bending moment [GNm]")
    ax.set_title("Smith moment-curvature, idealised double-hull midship section")
    ax.legend(fontsize=8)
    fig.tight_layout()
    p = FIG_DIR / "fig2_moment_curvature.png"
    fig.savefig(p, dpi=130)
    plt.close(fig)
    figs.append(p)

    # fig3: M_u/M_p vs plate slenderness for both load-shortening modes
    fig, ax = plt.subplots(figsize=(7.0, 4.6))
    for mode, style in (("euler", "-o"), ("von_karman", "-s")):
        panel = [0.6, 0.8, 1.0, 1.2, 1.5, 2.0, 2.5, 3.0]
        ratio = []
        for pb in panel:
            s = section.make_midship_section(panel_breadth=pb)
            r = smith.collapse_analysis(s, sigma_y=SIGMA_Y, E=E_STEEL, nu=NU, mode=mode)
            ratio.append(r["M_u"] / r["M_p"])
        ax.plot(panel, ratio, style, ms=4, lw=1.3, label=mode)
    ax.axhline(1.0, color="k", ls="--", lw=0.8, label="M_p limit")
    ax.set_xlabel("stiffener spacing / plate panel breadth b [m]  (slenderness up ->)")
    ax.set_ylabel("M_u / M_p")
    ax.set_title("Ultimate strength loss with plate slenderness")
    ax.legend(fontsize=8)
    fig.tight_layout()
    p = FIG_DIR / "fig3_slenderness.png"
    fig.savefig(p, dpi=130)
    plt.close(fig)
    figs.append(p)

    # fig4: S11 C(L) and wave bending moments vs L
    Ls = [90 + 2 * i for i in range(int((500 - 90) / 2) + 1)]
    cs = [loads.s11_C(x) for x in Ls]
    mh = [loads.s11_wave_bending_moments(x, 0.16 * x, 0.82)["M_wh_kNm"] / 1e9 for x in Ls]
    ms = [loads.s11_wave_bending_moments(x, 0.16 * x, 0.82)["M_ws_kNm"] / 1e9 for x in Ls]
    fig, (a1, a2) = plt.subplots(1, 2, figsize=(10.0, 4.0))
    a1.plot(Ls, cs)
    a1.axvline(300, color="gray", ls=":", lw=0.8)
    a1.axvline(350, color="gray", ls=":", lw=0.8)
    a1.set_xlabel("rule length L [m]")
    a1.set_ylabel("S11 coefficient C")
    a1.set_title("C(L): flat 10.75 on 300-350 m")
    a2.plot(Ls, mh, label="M_wh hogging")
    a2.plot(Ls, ms, label="M_ws sagging")
    a2.set_xlabel("rule length L [m]")
    a2.set_ylabel("wave bending moment [GNm]")
    a2.set_title("S11 wave moments (B = 0.16 L, Cb = 0.82)")
    a2.legend(fontsize=8)
    fig.tight_layout()
    p = FIG_DIR / "fig4_s11_loads.png"
    fig.savefig(p, dpi=130)
    plt.close(fig)
    figs.append(p)

    # fig5: section outline / part map
    fig, ax = plt.subplots(figsize=(6.4, 4.4))
    s = mode_results["section"]
    for part in s.parts:
        ax.add_patch(plt.Rectangle((part.y0, part.z0), part.y1 - part.y0, part.z1 - part.z0,
                                   facecolor="tab:blue", alpha=0.55, edgecolor="k", lw=0.4))
    ax.set_aspect("equal")
    ax.set_xlabel("y [m]")
    ax.set_ylabel("z [m]")
    ax.set_title(f"{s.name}: A={s.area():.2f} m^2, "
                 f"I={s.inertia():.1f} m^4, Z_deck={s.section_moduli()[1]:.1f} m^3")
    fig.tight_layout()
    p = FIG_DIR / "fig5_section_model.png"
    fig.savefig(p, dpi=130)
    plt.close(fig)
    figs.append(p)
    return figs


# ------------------------------------------------------------------------- main
def run_all(probe_platform: bool = True) -> Dict:
    results: List[Dict] = []
    results += bench_section_properties()
    results += bench_discretisation()
    results += bench_smith_vs_rigid_plastic()
    results += bench_bounds_and_two_flange()
    results += bench_s11_coefficient()
    results += bench_vlcc_stress_check()
    capability = bench_platform_capability(probe_platform)

    sec = section.make_midship_section()
    collapse = smith.collapse_analysis(sec, sigma_y=SIGMA_Y, E=E_STEEL, nu=NU, mode="euler")
    sagging = smith.collapse_analysis(sec, sigma_y=SIGMA_Y, E=E_STEEL, nu=NU, mode="euler",
                                      direction=-1)
    mc = {}
    for pb in (0.6, 1.0, 2.0):
        s = section.make_midship_section(panel_breadth=pb)
        mc[pb] = smith.collapse_analysis(s, sigma_y=SIGMA_Y, E=E_STEEL, nu=NU, mode="euler")
    mode_results = {"section": sec, "collapse": collapse, "mc": mc, "sagging": sagging,
                    "von_karman": smith.collapse_analysis(sec, sigma_y=SIGMA_Y, E=E_STEEL,
                                                          nu=NU, mode="von_karman")}

    payload = {
        "module": "hull_girder",
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "discipline": "结构 / hull girder longitudinal strength",
        "sigma_y_Pa": SIGMA_Y, "E_Pa": E_STEEL, "nu": NU,
        "tolerances": {"numeric_rel": REL_TOL_NUMERIC, "exact_rel": REL_TOL_EXACT},
        "benchmarks": results,
        "platform_capability_probe": capability,
        "summary": {
            "n": len(results),
            "n_pass": sum(1 for r in results if r["passed"]),
            "n_fail": sum(1 for r in results if not r["passed"]),
            "all_pass": all(r["passed"] for r in results),
        },
        "section_summary": {
            "name": sec.name, "A_m2": sec.area(), "z_na_m": sec.neutral_axis(),
            "I_m4": sec.inertia(),
            "Z_bottom_m3": sec.section_moduli()[0], "Z_deck_m3": sec.section_moduli()[1],
            "Z_p_m3": collapse["Z_p"], "M_u_GNm": collapse["M_u"] / 1e9,
            "M_p_GNm": collapse["M_p"] / 1e9, "M_y_GNm": collapse["M_y"] / 1e9,
            "M_u_over_M_p": collapse["M_u"] / collapse["M_p"],
            "M_u_hogging_GNm": collapse["M_u"] / 1e9,
            "M_u_sagging_GNm": abs(sagging["M_u"]) / 1e9,
            "kappa_u_sagging_1_per_m": abs(sagging["kappa_u"]),
            "z_na_u_sagging_m": sagging["z_na_u"],
            "M_u_von_karman_GNm": mode_results["von_karman"]["M_u"] / 1e9,
            "na_failures": collapse["na_failures"] + sagging["na_failures"],
            "kappa_u_1_per_m": collapse["kappa_u"],
        },
    }
    return {"payload": payload, "mode_results": mode_results}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="hull-girder strength verification harness")
    ap.add_argument("--write", action="store_true", help="write artifacts and figures")
    ap.add_argument("--no-probe", action="store_true", help="skip the :8001 capability probe")
    ap.add_argument("--json", default="", help="path to write the JSON payload")
    args = ap.parse_args(argv)

    out = run_all(probe_platform=not args.no_probe)
    payload = out["payload"]

    print(f"{'id':8s} {'pass':5s} {'rel_err':>10s}  name")
    for r in payload["benchmarks"]:
        rel = r["rel_error"]
        rel_s = f"{rel:.3e}" if isinstance(rel, float) else str(rel)
        print(f"{r['id']:8s} {str(r['passed']):5s} {rel_s:>10s}  {r['name']}")
    s = payload["summary"]
    print(f"\n{ s['n_pass'] }/{s['n']} PASS  (failures: {s['n_fail']})")

    if args.write or args.json:
        ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)
        figs = make_figures(out["mode_results"])
        payload["figures"] = [str(p) for p in figs]
        target = Path(args.json) if args.json else (ARTIFACT_DIR / "benchmark_results.json")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(payload, indent=2, ensure_ascii=False))
        print(f"wrote {target} and {len(figs)} figures")
    return 0 if payload["summary"]["all_pass"] else 1


if __name__ == "__main__":
    sys.exit(main())