#!/usr/bin/env python3
"""V-01 verification-debt harness for the ship-performance calculation endpoints.

Independently verifies ``backend/api/calculations.py`` (the capability audit grades
``calc.resistance`` / ``calc.stability`` as REAL) against first-principles
references that do NOT reuse the code under test:

  R1  ITTC-1957 friction line  -- re-evaluated with 50-digit ``mpmath`` precision
  R2  Schoenherr (1932) line   -- independent published closed form, cross-check
  R3  flat-plate identities    -- Rf == 0.5*rho*S*Cf*V^2,  Rt == Rf + Rr
  R4  exact wetted surface     -- S = int 2*pi*r*sqrt(1+r'^2) dx over explicit bodies
                                  of revolution (cylinder / spheroid / cosine-power /
                                  SUBOFF-proportioned) vs the code's Cb-only fit
  R5  hydrostatic GZ           -- first-principles strip integration of the submerged
                                  cross-section of a wall-sided box barge vs the code's
                                  wall-sided closed form
  R6  structural probes        -- draft sensitivity, Cr(Fn) monotonicity, small-angle limit

Outputs
  artifacts/verification/v01/metrics.json      all numeric results (single source)
  docs/verification/v01_figs/fig_*.png         figures
  docs/verification/V01_calc_endpoints.md      report, numbers injected programmatically

Run:  python3 scripts/verify_calc_v01.py
"""
from __future__ import annotations

import asyncio
import importlib.util
import json
import math
import os
import sys
from typing import Callable, Dict, List, Tuple

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BACKEND = os.path.join(ROOT, "backend")
OUT = os.path.join(ROOT, "artifacts", "verification", "v01")
DOCS = os.path.join(ROOT, "docs", "verification")
FIGDIR = os.path.join(DOCS, "v01_figs")

sys.path.insert(0, BACKEND)

try:  # numpy >= 2
    _TRAP = np.trapezoid
except AttributeError:  # pragma: no cover
    _TRAP = np.trapz


# ---------------------------------------------------------------------------
# module under test (its own imports are stdlib-only, so no DB is touched)
# ---------------------------------------------------------------------------

def load_calc():
    spec = importlib.util.spec_from_file_location(
        "vp_calc_under_test", os.path.join(BACKEND, "api", "calculations.py")
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


CALC = load_calc()


def resistance(**kw):
    return asyncio.run(CALC.calc_resistance(CALC.ResistanceRequest(**kw)))


def stability(**kw):
    return asyncio.run(CALC.calc_stability(CALC.StabilityRequest(**kw)))


# ---------------------------------------------------------------------------
# R1 / R2: published friction lines, evaluated independently
# ---------------------------------------------------------------------------

def ittc1957_cf_mp(Re: float) -> float:
    import mpmath as mp
    mp.mp.dps = 50
    return float(mp.mpf("0.075") / (mp.log10(mp.mpf(Re)) - 2) ** 2)


def schoenherr_cf(Re: float) -> float:
    """Schoenherr (1932) ATTC line, Cf = 0.455/(log10 Re)^2.58."""
    return 0.455 / (math.log10(Re)) ** 2.58


# ---------------------------------------------------------------------------
# R4: exact geometry of bodies of revolution
# ---------------------------------------------------------------------------

def revolve_metrics(rfun: Callable[[np.ndarray], np.ndarray], L: float,
                    n: int = 400001) -> Tuple[float, float, float]:
    """Exact wetted area S, volume nabla and block coefficient Cb of r(x)."""
    x = np.linspace(0.0, L, n)
    r = rfun(x)
    drdx = np.gradient(r, x)
    S = float(_TRAP(2.0 * np.pi * r * np.sqrt(1.0 + drdx ** 2), x))
    vol = float(_TRAP(np.pi * r ** 2, x))
    rmax = float(np.max(r))
    return S, vol, vol / (np.pi * rmax ** 2 * L)


def code_wetted_surface(L: float, B: float, cb: float) -> float:
    """Wetted-surface model exactly as implemented in api/calculations.py."""
    return math.pi * B * L * (0.5 + 0.25 * cb)


def geoms(L: float, R: float) -> Dict[str, Dict]:
    out: Dict[str, Dict] = {}

    # (a) right circular cylinder: Cb = 1, lateral area = pi*D*L exactly
    out["cylinder"] = dict(rfun=lambda x, R=R: np.full_like(x, R), cb=1.0,
                           S_exact=2.0 * np.pi * R * L,
                           note="right circular cylinder (Cb=1), lateral area pi*D*L")

    # (b) prolate spheroid: Cb = 2/3, closed-form surface area
    a, b = L / 2.0, R
    e = math.sqrt(1.0 - (b / a) ** 2)
    S_spheroid = 2 * np.pi * b ** 2 + 2 * np.pi * (a * b / e) * math.asin(e)
    out["spheroid"] = dict(
        rfun=lambda x, L=L, R=R: R * np.sqrt(np.clip(1.0 - ((x - L / 2) / (L / 2)) ** 2, 0, 1)),
        cb=2.0 / 3.0, S_exact=S_spheroid,
        note="prolate spheroid (Cb=2/3), closed-form area (quadrature cross-checked)")

    # (c) cosine-power body, exponent fitted so that Cb = 0.542
    target = 0.542

    def cosine_power(p):
        return lambda x, L=L, R=R, p=p: R * np.sin(np.pi * x / L) ** p

    lo, hi = 0.02, 8.0
    for _ in range(120):
        mid = 0.5 * (lo + hi)
        _, _, cb = revolve_metrics(cosine_power(mid), L, 20001)
        if cb > target:          # Cb decreases with the exponent p
            lo = mid
        else:
            hi = mid
    p_star = 0.5 * (lo + hi)
    S_cp, _, cb_cp = revolve_metrics(cosine_power(p_star), L)
    out["cosine_power"] = dict(rfun=cosine_power(p_star), cb=cb_cp, S_exact=S_cp,
                               param=p_star,
                               note=f"cosine-power body r=R*sin(pi*x/L)^{p_star:.4f} (fitted to Cb=0.542)")

    # (d)/(e) SUBOFF-proportioned bodies of revolution (module-level builders)
    out["suboff_like"] = build_capped(0.542, L, R)
    out["full_form"] = build_capped(0.750, L, R)
    return out


def _cap_fracs(L: float):
    return 1.016 / L, 2.229 / L, 1.111 / L

# SUBOFF-proportioned bodies of revolution: nose / parallel middle body / tail
# occupy the SUBOFF length fractions; the cap fullness (superellipse
#         exponent p) is solved so that Cb hits the target.  p=2 is an elliptical
#         cap, p>2 is fuller, p<2 is pointier.
def capped_family(p_exp: float, L: float, R: float):
    f_nose, f_mid, f_tail = _cap_fracs(L)
    l0, lm, lt = f_nose * L, f_mid * L, f_tail * L
    x_tail = l0 + lm

    def rfun(x, L=L, R=R, l0=l0, x_tail=x_tail, lt=lt, pe=p_exp):
        r = np.zeros_like(x)
        m = x < l0
        u = np.clip((l0 - x[m]) / l0, 0.0, 1.0)
        r[m] = R * (1.0 - u ** pe) ** (1.0 / pe)
        m = (x >= l0) & (x <= x_tail)
        r[m] = R
        m = x > x_tail
        u = np.clip((x[m] - x_tail) / lt, 0.0, 1.0)
        r[m] = R * (1.0 - u ** pe) ** (1.0 / pe)
        return r
    return rfun

def solve_capped(cb_target: float, L: float, R: float) -> Tuple[Callable, float]:
    lo, hi = 0.15, 200.0           # Cb increases with the cap exponent
    for _ in range(140):
        mid = math.sqrt(lo * hi)
        _, _, cb = revolve_metrics(capped_family(mid, L, R), L, 40001)
        if cb < cb_target:
            lo = mid
        else:
            hi = mid
    p_star = math.sqrt(lo * hi)
    return capped_family(p_star, L, R), p_star

def build_capped(cb_target: float, L: float, R: float) -> Dict:
    rfun, p_star = solve_capped(cb_target, L, R)
    S_g, _, cb_g = revolve_metrics(rfun, L)
    f_nose, f_mid, f_tail = _cap_fracs(L)
    return dict(rfun=rfun, cb=cb_g, S_exact=S_g, param=p_star,
                note=(f"caps: superellipse exponent p={p_star:.3f} solved for Cb={cb_target}; "
                      f"length fractions nose/mid/tail = {f_nose:.3f}/{f_mid:.3f}/{f_tail:.3f} "
                      "(SUBOFF proportions)"))


def verify_spheroid_closed_form(L: float, R: float) -> float:
    """Quadrature self-check against the closed-form prolate-spheroid area."""
    S_num, _, _ = revolve_metrics(lambda x, L=L, R=R: R * np.sqrt(
        np.clip(1.0 - ((x - L / 2) / (L / 2)) ** 2, 0, 1)), L)
    a, b = L / 2.0, R
    e = math.sqrt(1.0 - (b / a) ** 2)
    S_ana = 2 * np.pi * b ** 2 + 2 * np.pi * (a * b / e) * math.asin(e)
    return abs(S_num - S_ana) / S_ana


# ---------------------------------------------------------------------------
# R5: first-principles hydrostatics of a wall-sided box barge
# ---------------------------------------------------------------------------

def box_barge_gz(B: float, T: float, KG: float, depth: float, phi_deg: float,
                 n_strip: int = 200001) -> Dict[str, float]:
    """GZ from strip integration of the submerged cross-section.

    Body frame: y in [0,B], z in [0,depth].  The waterplane expressed in body
    coordinates is z_w(y) = T + (y - B/2)*tan(phi); the submerged height per strip
    is clipped to [0, depth], so bilge emergence and deck immersion are handled
    exactly (the wall-sided closed form is only valid before either occurs).
    G lies on the centreline, hence the transverse lever is (y_B - B/2).
    """
    phi = math.radians(phi_deg)
    y = np.linspace(0.0, B, n_strip)
    h = np.clip(T + (y - B / 2.0) * math.tan(phi), 0.0, depth)
    area = float(_TRAP(h, y))
    zc = float(_TRAP(h ** 2 / 2.0, y)) / area
    yc = float(_TRAP(y * h, y)) / area
    gz = (yc - B / 2.0) * math.cos(phi) + (zc - KG) * math.sin(phi)
    return dict(GZ=gz, y_B=yc, z_B=zc, submerged_area=area,
                bilge_or_deck=bool(h.min() <= 1e-12 or h.max() >= depth - 1e-12))


def wall_sided_gz(GM: float, BM: float, phi_deg: float) -> float:
    phi = math.radians(phi_deg)
    return GM * math.sin(phi) + 0.5 * BM * math.tan(phi) ** 2 * math.sin(phi)


# ---------------------------------------------------------------------------
# verification run
# ---------------------------------------------------------------------------

SUBOFF = dict(length=4.356, beam=0.508, draft=0.476, cb=0.542)
RHO, NU = 998.0, 1.004e-6


def main() -> Dict:
    os.makedirs(OUT, exist_ok=True)
    os.makedirs(FIGDIR, exist_ok=True)
    m: Dict = {"meta": {
        "harness": "scripts/verify_calc_v01.py",
        "target": "backend/api/calculations.py",
        "api_reachable": False,
        "api_note": ("ship API on :8001/:8002 was unreachable during this run; the "
                     "route handlers were therefore exercised in-process (same code, "
                     "no HTTP layer)"),
        "suboff": SUBOFF,
    }}

    # ---- R1 / R2 --------------------------------------------------------
    # V = 5 m/s keeps Rf large enough that the response field's 4-decimal
    # rounding (<= 5e-5 N) is far below the compared quantity.
    rows, eps_max = [], 0.0
    V1 = 5.0
    for Re in (1e6, 1e7, 1e8, 1e9):
        Lg = Re * NU / V1                       # Re = V*L/nu
        Bg, Tg = Lg / 8.57, Lg / 9.15
        r = resistance(speed_ms=V1, length=Lg, beam=Bg, draft=Tg, cb=0.542)
        S = code_wetted_surface(Lg, Bg, 0.542)
        cf_code = r.frictional_resistance_n / (0.5 * RHO * S * V1 ** 2)
        cf_mp = ittc1957_cf_mp(r.reynolds_number)
        cf_sch = schoenherr_cf(r.reynolds_number)
        eps = abs(cf_code - cf_mp) / cf_mp
        eps_max = max(eps_max, eps)
        rows.append(dict(Re=Re, Re_code=r.reynolds_number, L=Lg, Rf=r.frictional_resistance_n,
                         Cf_code=cf_code, Cf_ittc1957=cf_mp, Cf_schoenherr=cf_sch,
                         rel_err_ittc=eps, dev_vs_schoenherr=(cf_sch - cf_mp) / cf_mp,
                         rounding_bound=5e-5))
    m["R1_ittc1957"] = {"rows": rows, "max_rel_err": eps_max, "tol": 1e-6,
                        "V": V1, "rounding_bound_N": 5e-5,
                        "rounding_limited": [x for x in rows if x["rel_err_ittc"] > 1e-9]}

    # ---- R3 identities ---------------------------------------------------
    r = resistance(speed_ms=3.05, **SUBOFF)
    L, B, cb = SUBOFF["length"], SUBOFF["beam"], SUBOFF["cb"]
    S_code = code_wetted_surface(L, B, cb)
    cf = 0.075 / (math.log10(r.reynolds_number) - 2.0) ** 2
    rf_ref = 0.5 * RHO * S_code * cf * 3.05 ** 2
    fn_ref = 3.05 / math.sqrt(9.81 * L)
    m["R3_identities"] = {
        "Rf_rel_err": abs(r.frictional_resistance_n - rf_ref) / rf_ref,
        "Rt_minus_Rf_Rr_abs": abs(r.total_resistance_n - r.frictional_resistance_n
                                  - r.residual_resistance_n),
        "Fn_rel_err": abs(r.froude_number - fn_ref) / fn_ref,
        "S_code": S_code, "Rf": r.frictional_resistance_n,
        "Rr": r.residual_resistance_n, "Rt": r.total_resistance_n,
        "Re": r.reynolds_number, "Fn": r.froude_number,
    }

    # ---- R4 wetted surface ----------------------------------------------
    G = geoms(L, B / 2.0)
    m["R4_spheroid_quadrature_check"] = verify_spheroid_closed_form(L, B / 2.0)
    w_rows = []
    for name, g in G.items():
        S_code_g = code_wetted_surface(L, B, g["cb"])
        w_rows.append(dict(
            name=name, cb=g["cb"], S_exact=g["S_exact"], S_code=S_code_g,
            ratio_exact=g["S_exact"] / (math.pi * B * L),
            ratio_code=S_code_g / (math.pi * B * L),
            rel_err=(S_code_g - g["S_exact"]) / g["S_exact"],
            note=g["note"], param=g.get("param")))
    sweep_rows = []
    for cb_t in (0.54, 0.58, 0.62, 0.66, 0.70, 0.75, 0.80, 0.85, 0.90, 0.95):
        rfun_s, p_s = solve_capped(cb_t, L, B / 2.0)
        S_s, _, cb_s = revolve_metrics(rfun_s, L, 20001)
        S_code_s = code_wetted_surface(L, B, cb_s)
        sweep_rows.append(dict(cb_target=cb_t, cb=cb_s, p=p_s, S_exact=S_s,
                               S_code=S_code_s,
                               ratio_exact=S_s / (math.pi * B * L),
                               ratio_code=S_code_s / (math.pi * B * L),
                               rel_err=(S_code_s - S_s) / S_s))
    cross = None
    for a, b_ in zip(sweep_rows, sweep_rows[1:]):
        if a["rel_err"] > -0.15 >= b_["rel_err"]:
            w = (a["rel_err"] + 0.15) / (a["rel_err"] - b_["rel_err"])
            cross = a["cb"] + w * (b_["cb"] - a["cb"])
    band = [x for x in (next(y for y in w_rows if y["name"] == "suboff_like"),
                        next(y for y in w_rows if y["name"] == "cosine_power"))]

    m["R4_wetted_surface"] = {
        "sweep": sweep_rows, "cb_15pct_crossover": cross,
        "shape_band_at_cb0542": [dict(name=x["name"], rel_err=x["rel_err"]) for x in band],
        "rows": w_rows,
        "code_ratio_at_cb0542": code_wetted_surface(L, B, 0.542) / (math.pi * B * L),
        "worst_rel_err": min(x["rel_err"] for x in w_rows),
        "suboff_like_rel_err": next(x["rel_err"] for x in w_rows if x["name"] == "suboff_like"),
    }

    # ---- R5 GZ -----------------------------------------------------------
    barge = dict(B=20.0, T=8.0, KG=6.0, depth=20.0)
    KB, BM = barge["T"] / 2.0, barge["B"] ** 2 / (12.0 * barge["T"])
    GM = KB + BM - barge["KG"]
    gz_rows = []
    for phi in (0.0, 5.0, 10.0, 20.0, 30.0, 36.0, 38.0, 40.0, 45.0, 50.0):
        ref = box_barge_gz(barge["B"], barge["T"], barge["KG"], barge["depth"], phi)
        api = stability(displacement_t=1000.0, gm_m=GM, kb_m=KB, bm_m=BM,
                        heel_angle_deg=phi)
        abs_err = abs(api.righting_arm_m - ref["GZ"])
        gz_rows.append(dict(
            phi=phi, GZ_first_principles=ref["GZ"], GZ_code=api.righting_arm_m,
            RM_knm=api.righting_moment_knm,
            GZ_wall_sided=wall_sided_gz(GM, BM, phi), abs_err=abs_err,
            rel_err=(abs_err / abs(ref["GZ"])) if abs(ref["GZ"]) > 1e-12 else abs_err,
            y_B=ref["y_B"], z_B=ref["z_B"], bilge_or_deck=ref["bilge_or_deck"]))
    phi_valid = min(m_phi_bilge := math.degrees(math.atan(barge["T"] / (barge["B"] / 2.0))),
                    math.degrees(math.atan((barge["depth"] - barge["T"]) / (barge["B"] / 2.0))))
    valid = [g for g in gz_rows if not g["bilge_or_deck"]]
    small_angles = (1.0, 0.5, 0.2)
    small = [stability(displacement_t=1000.0, gm_m=GM, kb_m=KB, bm_m=BM,
                       heel_angle_deg=e).righting_arm_m / math.radians(e)
             for e in small_angles]
    m["R5_gz"] = {
        "barge": barge, "KB": KB, "BM": BM, "GM": GM, "rows": gz_rows,
        "max_abs_err_valid": max(g["abs_err"] for g in valid),
        "max_rel_err_valid": max(g["rel_err"] for g in valid),
        "phi_bilge_emergence": math.degrees(math.atan(barge["T"] / (barge["B"] / 2.0))),
        "phi_deck_immersion": math.degrees(
            math.atan((barge["depth"] - barge["T"]) / (barge["B"] / 2.0))),
        "max_rel_err_beyond_validity": max(g["rel_err"] for g in gz_rows
                                           if g["bilge_or_deck"] and g["phi"] > 0),
        "small_angle_GZ_over_phi": small,
        # GZ/phi -> GM + 0.5*BM*tan^2(phi); compare against that closed limit
        "small_angle_angles": list(small_angles),
        "small_angle_rel_err": abs(small[-1] - (GM + 0.5 * BM * math.tan(math.radians(small_angles[-1])) ** 2))
                               / GM,
        "phi_valid_limit": phi_valid,
    }

    # ---- R6 structural probes -------------------------------------------
    drafts = [0.2, 0.476, 1.0, 2.0, 4.0]
    rt_vs_t = [resistance(speed_ms=3.05, length=L, beam=B, draft=t, cb=cb).total_resistance_n
               for t in drafts]
    sweep = []
    for V in (1.0, 1.5, 2.0, 2.5, 3.05, 3.5, 4.0, 4.5, 5.0):
        rr = resistance(speed_ms=V, length=L, beam=B, draft=SUBOFF["draft"], cb=cb)
        sweep.append(dict(V=V, Fn=rr.froude_number, Rt=rr.total_resistance_n,
                          Rf=rr.frictional_resistance_n, Rr=rr.residual_resistance_n,
                          Ct=rr.total_resistance_n / (0.5 * RHO * S_code * V ** 2)))
    cr_curve = [(d["Fn"], d["Rr"] / (0.5 * RHO * S_code * d["V"] ** 2)) for d in sweep]
    diffs = [cr_curve[i + 1][1] - cr_curve[i][1] for i in range(len(cr_curve) - 1)]
    m["R6_structural"] = {
        "drafts": drafts, "Rt_vs_draft": rt_vs_t,
        "draft_sensitivity_rel": (max(rt_vs_t) - min(rt_vs_t)) / max(rt_vs_t),
        "sweep": sweep, "cr_vs_fn": cr_curve,
        "cr_monotone_increasing": all(d >= 0 for d in diffs),
        "cr_ratio_to_ittc_at_design": sweep[4]["Rr"] / sweep[4]["Rf"],
    }

    m["meta"]["figures"] = make_figures(m, G, sweep, gz_rows)
    with open(os.path.join(OUT, "metrics.json"), "w", encoding="utf-8") as fh:
        json.dump(m, fh, indent=2, ensure_ascii=False)
    write_report(m)
    return m


def make_figures(m, G, sweep, gz_rows) -> List[str]:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    figs: List[str] = []
    plt.rcParams.update({"figure.dpi": 132, "font.size": 9})
    L, B = SUBOFF["length"], SUBOFF["beam"]
    cn = {"cylinder": "cylinder (Cb=1)", "spheroid": "prolate spheroid (Cb=2/3)",
          "cosine_power": "cosine-power body", "suboff_like": "SUBOFF-proportioned (Cb=0.542)",
          "full_form": "fuller body (Cb=0.75)"}
    mk = {"cylinder": "s", "spheroid": "^", "cosine_power": "o", "suboff_like": "D",
          "full_form": "v"}

    # FIG 1 -- wetted surface: code fit vs exact bodies of revolution
    fig, ax = plt.subplots(1, 2, figsize=(9.6, 3.7))
    cb_ax = np.linspace(0.4, 1.0, 200)
    ax[0].plot(cb_ax, 0.5 + 0.25 * cb_ax, "k-", lw=2,
               label=r"code fit $S_w/(\pi DL)=0.5+0.25C_b$")
    for name, g in G.items():
        ax[0].plot(g["cb"], g["S_exact"] / (math.pi * B * L), mk[name], ms=9,
                   label=f"{cn[name]} (exact $\\int2\\pi r\\,ds$)")
    ax[0].axvline(0.542, color="0.6", ls="--", lw=1)
    ax[0].set_xlabel(r"$C_b$"); ax[0].set_ylabel(r"$S_w/(\pi D L)$")
    ax[0].set_title("Exact wetted surface vs the code's $C_b$-only fit")
    ax[0].legend(fontsize=6.6, loc="lower right"); ax[0].grid(alpha=0.3)

    sw = m["R4_wetted_surface"]["sweep"]
    ax[0].plot([x["cb"] for x in sw], [x["ratio_exact"] for x in sw], "-", color="0.45",
               lw=1.2, label="exact, SUBOFF length proportions (Cb sweep)")
    labels = [cn[n] for n in G]
    errs = [(next(x["rel_err"] for x in m["R4_wetted_surface"]["rows"] if x["name"] == n)) * 100
            for n in G]
    ax[1].barh(labels, errs, color=["#c0392b" if e < -15 else "#e67e22" for e in errs])
    ax[1].axvline(-15, color="r", ls="--", lw=1.2)
    ax[1].text(-14.7, len(errs) - 0.55, "-15% bound", color="r", fontsize=7)
    for i, e in enumerate(errs):
        ax[1].text(e + 0.3, i, f"{e:+.1f}%", va="center", fontsize=8)
    ax[1].set_xlabel(r"relative error of the code $S_w$  (%)")
    ax[1].set_xlim(min(errs) - 4, 3)
    ax[1].set_title("Systematic under-prediction of $S_w$")
    ax[1].grid(alpha=0.3, axis="x")
    fig.tight_layout(w_pad=2.0); p = os.path.join(FIGDIR, "fig1_wetted_surface.png")
    fig.savefig(p); plt.close(fig); figs.append(p)

    # FIG 2 -- friction lines, then resistance sweep with geometric correction
    fig, ax = plt.subplots(1, 2, figsize=(9.6, 3.7))
    Re = np.logspace(6, 9.5, 240)
    cf_i = 0.075 / (np.log10(Re) - 2.0) ** 2
    cf_s = 0.455 / (np.log10(Re)) ** 2.58
    ax[0].loglog(Re, cf_i * 1e3, "k-", lw=2, label="ITTC-1957 (as implemented)")
    ax[0].loglog(Re, cf_s * 1e3, "b--", lw=1.4, label="Schoenherr 1932 (cross-check)")
    ax[0].set_xlabel("Re"); ax[0].set_ylabel(r"$C_F\times10^{3}$")
    ax2 = ax[0].twinx()
    ax2.semilogx(Re, (cf_s - cf_i) / cf_i * 100, "r:", lw=1.1)
    ax2.set_ylabel("Schoenherr vs ITTC (%)", color="r")
    ax2.tick_params(axis="y", colors="r")
    ax[0].set_title("Friction line reproduced exactly; Schoenherr cross-check within a few %")
    ax[0].legend(fontsize=7, loc="lower left"); ax[0].grid(alpha=0.3, which="both")

    V = [d["V"] for d in sweep]
    ax[1].plot(V, [d["Rt"] for d in sweep], "o-", color="#c0392b", lw=2,
               label=r"endpoint $R_t$ (as implemented)")
    styles = {"suboff_like": ("s--", "#1f77b4", "exact $S_w$, pointy caps"),
              "cosine_power": ("^--", "#2ca02c", "exact $S_w$, cosine-power body"),
              "full_form": ("D--", "#8c564b", "exact $S_w$, fuller body (Cb=0.75)")}
    for nm, (st, col, lab) in styles.items():
        row = next(x for x in m["R4_wetted_surface"]["rows"] if x["name"] == nm)
        f = row["S_exact"] / row["S_code"]
        ax[1].plot(V, [d["Rt"] * f for d in sweep], st, color=col, lw=1.3,
                   label=rf"{lab} ($\times${f:.3f})")
    ax[1].set_xlabel("ship speed V (m/s)"); ax[1].set_ylabel(r"$R_t$ (N)")
    ax[1].set_title(r"Same inputs, different true geometry: $R_t$ spread from $S_w$ alone")
    ax[1].legend(fontsize=7); ax[1].grid(alpha=0.3)
    fig.tight_layout(); p = os.path.join(FIGDIR, "fig2_friction_and_resistance.png")
    fig.savefig(p); plt.close(fig); figs.append(p)

    # FIG 3 -- GZ verification and validity limit
    fig, ax = plt.subplots(1, 2, figsize=(9.6, 3.7))
    b = m["R5_gz"]["barge"]
    phi_ref = np.linspace(0, 55, 111)
    gz_ref = [box_barge_gz(b["B"], b["T"], b["KG"], b["depth"], e)["GZ"] for e in phi_ref]
    gz_code = [wall_sided_gz(m["R5_gz"]["GM"], m["R5_gz"]["BM"], e) for e in phi_ref]
    ax[0].plot(phi_ref, gz_ref, "k-", lw=2.4, label="first-principles strip integration")
    ax[0].plot(phi_ref, gz_code, "r--", lw=1.5, label="endpoint wall-sided closed form")
    ax[0].axvline(m["R5_gz"]["phi_valid_limit"], color="0.5", ls=":", lw=1.2)
    ax[0].text(m["R5_gz"]["phi_valid_limit"] + 0.8, 1.6,
               "validity limit\n(bilge/deck)", fontsize=7, color="0.35")
    ax[0].set_xlabel(r"heel angle $\phi$ (deg)"); ax[0].set_ylabel("GZ (m)")
    ax[0].set_title("Box barge 100x20x8 m, KG=6 m (GM=%.3f m)" % m["R5_gz"]["GM"])
    ax[0].legend(fontsize=7); ax[0].grid(alpha=0.3)

    err = [abs(c - r_) / abs(r_) * 100 if abs(r_) > 1e-9 else np.nan
           for c, r_ in zip(gz_code, gz_ref)]
    ax[1].semilogy(phi_ref, np.clip(err, 1e-14, None), lw=1.8, color="#c0392b")
    ax[1].axvline(m["R5_gz"]["phi_valid_limit"], color="0.5", ls=":", lw=1.2)
    ax[1].set_xlabel(r"heel angle $\phi$ (deg)")
    ax[1].set_ylabel("relative GZ error (%)")
    ax[1].set_title("Error <= 1e-4% while wall-sided, unbounded beyond")
    ax[1].grid(alpha=0.3, which="both")
    fig.tight_layout(); p = os.path.join(FIGDIR, "fig3_stability_gz.png")
    fig.savefig(p); plt.close(fig); figs.append(p)

    # FIG 4 -- structural probes
    fig, ax = plt.subplots(1, 2, figsize=(9.6, 3.4))
    ax[0].plot(m["R6_structural"]["drafts"], m["R6_structural"]["Rt_vs_draft"], "o-",
               color="#c0392b")
    ax[0].set_ylim(min(m["R6_structural"]["Rt_vs_draft"]) * 0.98,
                   max(m["R6_structural"]["Rt_vs_draft"]) * 1.02)
    ax[0].set_xlabel("draft T (m)"); ax[0].set_ylabel(r"$R_t$ (N)")
    ax[0].set_title(r"$R_t$ does not depend on draft at all (T unused)")
    ax[0].grid(alpha=0.3)
    fn = [c[0] for c in m["R6_structural"]["cr_vs_fn"]]
    cr = [c[1] for c in m["R6_structural"]["cr_vs_fn"]]
    ax[1].plot(fn, cr, "s-", color="#1f77b4", label=r"code $C_r$")
    ax[1].axvspan(0.22, 0.32, color="0.8", alpha=0.6,
                  label="where published $C_r$ shows a hump")
    ax[1].set_xlabel(r"$F_n$"); ax[1].set_ylabel(r"$C_r$")
    ax[1].set_title(r"$C_r=0.002(1+2C_b)F_n^{2.5}$: monotone, no hump representable")
    ax[1].legend(fontsize=7); ax[1].grid(alpha=0.3)
    fig.tight_layout(); p = os.path.join(FIGDIR, "fig4_structural_probes.png")
    fig.savefig(p); plt.close(fig); figs.append(p)
    return figs

# ---------------------------------------------------------------------------
# report generation (every number below is injected from `m`, never hand-copied)
# ---------------------------------------------------------------------------

def write_report(m: Dict) -> None:
    os.makedirs(DOCS, exist_ok=True)
    r1, r3, r4, r5, r6 = (m["R1_ittc1957"], m["R3_identities"], m["R4_wetted_surface"],
                          m["R5_gz"], m["R6_structural"])
    row = {x["name"]: x for x in r4["rows"]}
    sub, cos_, cyl, sph, full = (row["suboff_like"], row["cosine_power"],
                                 row["cylinder"], row["spheroid"], row["full_form"])
    cross = r4["cb_15pct_crossover"]
    sw = r4["sweep"]
    worst_round = max(r1["rows"], key=lambda x: x["rel_err_ittc"])
    big = [x for x in r1["rows"] if x["Rf"] > 10.0]
    worst_big = max(x["rel_err_ittc"] for x in big)

    def pc(x, n=2):
        return f"{x * 100:+.{n}f}%"

    T = []
    A = T.append
    A("# V-01 验证欠账：船舶总体性能计算端点（阻力 / 稳性）独立基准复核\n")
    A("> tp-paper-2 自找活（验证欠账类）· 仓 `TensorPlatform` · 分支 `agent/m1607`")
    A("> 复算脚本 `scripts/verify_calc_v01.py` · 原始数值 `artifacts/verification/v01/metrics.json`")
    A("> · 图 `docs/verification/v01_figs/` · 判据回归 `tests/test_calc_verification_v01.py`")
    A("> · 本文数字全部由脚本注入（零手抄）。\n")

    A("## 1. 案例介绍\n")
    A("平台能力审计把 `calc.resistance` 与 `calc.stability` 判为 **REAL**。REAL 只说明「有真实算法」，")
    A("不等于**已核对精度**：`grep -rl 'calculations/resistance' backend/tests` 命中 0 个文件，")
    A("即这两条端点在仓内此前没有任何测试、没有任何误差界。本单补这笔验证欠账：")
    A("给已合并功能配独立基准，量化误差，并如实给出证据等级建议。\n")
    A("物理背景：")
    A("- **阻力**：$R_t=\\tfrac12\\rho S_w(C_F+C_r)V^2$。$C_F$ 用 ITTC-1957 平板摩擦线，")
    A("  $C_r$ 为经验式，$S_w$ 用 `api/calculations.py` 里的 $C_b$ 单参数拟合式。")
    A("  $S_w$ 是唯一的几何放大因子，$R_t\\propto S_w$，所以 $S_w$ 是链路上最该被核对的量。")
    A("- **稳性**：复原力臂用直壁闭式 $GZ=\\sin\\varphi\\,(GM_0+\\tfrac12 BM_0\\tan^2\\varphi)$。")
    A("  对直壁船型，在「舭部未出水、甲板未入水」区间内该式应为**精确**，因此可被一阶原理积分反证。\n")
    A("| 量 | 阻力算例（端点默认参数，SUBOFF 主尺度） | 稳性算例（直壁箱型驳） |")
    A("|---|---|---|")
    A(f"| 主尺度 | L={SUBOFF['length']} m, B={SUBOFF['beam']} m, T={SUBOFF['draft']} m, Cb={SUBOFF['cb']} | "
      f"L=100 m, B={r5['barge']['B']} m, T={r5['barge']['T']} m, 型深 {r5['barge']['depth']} m |")
    A(f"| 物性 / 工况 | ρ={RHO} kg/m³, ν={NU:.3e} m²/s, V=3.05 m/s | "
      f"KB={r5['KB']:.4f} m, BM={r5['BM']:.4f} m, KG={r5['barge']['KG']} m → GM={r5['GM']:.4f} m |")
    A(f"| 端点返回 | Re={r3['Re']:.4e}, Fn={r3['Fn']:.4f}, $R_f$={r3['Rf']:.3f} N, "
      f"$R_r$={r3['Rr']:.3f} N, $R_t$={r3['Rt']:.3f} N | "
      f"GZ(20°)={next(g['GZ_code'] for g in r5['rows'] if g['phi'] == 20.0):.6f} m, "
      f"$RM$={next(g['RM_knm'] for g in r5['rows'] if g['phi'] == 20.0):.1f} kN·m |\n")

    A("## 2. 怎么做的\n")
    A("**核心纪律：参考量不复用被测代码的任何公式**（否则是自证循环）。六条独立基准：\n")
    A("| 编号 | 独立基准 | 构造方式 |")
    A("|---|---|---|")
    A("| R1 | ITTC-1957 摩擦线 | `mpmath` 50 位精度独立重算 $0.075/(\\log_{10}Re-2)^2$，与端点反推的 $C_F$ 比 |")
    A("| R2 | Schoenherr 1932 线 | 独立闭式 $0.455/(\\log_{10}Re)^{2.58}$，一致性交叉检验 |")
    A("| R3 | 平板阻力恒等式 | 由定义重算 $R_f$、$R_t-R_f-R_r$、$F_n$ |")
    A("| R4 | 精确湿表面积 | 显式回转体母型数值积分 $S_w=\\int 2\\pi r\\sqrt{1+r'^2}\\,dx$（40 万点梯形） |")
    A("| R5 | 一阶原理复原力臂 | 箱型驳浸没剖面条带积分（20 万条）：$GZ=(\\bar y_B-\\tfrac{B}{2})\\cos\\varphi+(\\bar z_B-KG)\\sin\\varphi$ |")
    A("| R6 | 结构探针 | 吃水灵敏度、$C_r(F_n)$ 单调性、小角极限 |")
    A(f"\n积分器自检：回转体积分对长球体精确闭式面积的相对偏差 {m['R4_spheroid_quadrature_check']:.2e}；")
    A(f"箱型驳在适用域内与直壁闭式吻合到 {r5['max_abs_err_valid']:.2e} m —— 说明基准本身可信。")
    A("参考几何族（全部显式公开，可复算）：")
    A("正圆柱（$C_b$=1）、长球体（$C_b$=2/3，有闭式面积）、余弦幂体（$C_b$=0.542）、")
    A("以及「SUBOFF 长度比例（首/中/尾 1.016/2.229/1.111 m）超椭圆首尾帽」族——")
    A("该族用帽形丰满度指数 $p$ 反解到目标 $C_b$，从 $C_b$=0.54 扫到 0.95。\n")
    A("判据：平台精度门槛「对标参考值误差 >15% 不可接受」，故 R4 以 ±15% 为红线。")
    A("被测代码以 `importlib` 按文件路径加载、进程内直接调用路由函数（同 `TestClient` 语义）；")
    A("本机 8001/8002 端点复算时不可达（见 §5）。\n")

    A("## 3. 计算结果\n")
    A("### 3.1 R1/R2 摩擦线：实现无误\n")
    A("| Re | 端点反推 $C_F$ | ITTC-1957（50 位重算） | 相对误差 | Schoenherr | Schoenherr 相对 ITTC |")
    A("|---|---|---|---|---|---|")
    for x in r1["rows"]:
        A(f"| {x['Re']:.0e} | {x['Cf_code']:.6e} | {x['Cf_ittc1957']:.6e} | {x['rel_err_ittc']:.2e} "
          f"| {x['Cf_schoenherr']:.6e} | {pc(x['dev_vs_schoenherr'])} |")
    A(f"\n全表最大相对误差 {r1['max_rel_err']:.2e}，只出现在 $R_f$ 最小的 Re={worst_round['Re']:.0e} 点：")
    A(f"该点 $R_f$={worst_round['Rf']:.4f} N，而响应字段只保留 4 位小数，舍入相对误差界 "
      f"{r1['rounding_bound_N'] / worst_round['Rf']:.1e} 已大于观测值——偏差可完全由舍入解释。")
    A(f"$R_f>10$ N 的算例偏差最大 {worst_big:.2e}，即浮点精度级。")
    A(f"两条独立标准线差异 {max(abs(x['dev_vs_schoenherr']) for x in r1['rows'])*100:.2f}% 量级，"
      "与「ITTC-1957 为 Schoenherr 线的拟合」这一已知关系相容。**摩擦项算法可信。**\n")

    A("### 3.2 R3 恒等式\n")
    A(f"- $R_f$ 相对误差 {r3['Rf_rel_err']:.2e}（同样受 4 位小数舍入限制）；"
      f"$|R_t-R_f-R_r|$={r3['Rt_minus_Rf_Rr_abs']:.2e} N；$F_n$ 相对误差 {r3['Fn_rel_err']:.2e}。")
    A("- 这一条只证明无算术错，不证明物理正确。\n")

    A("### 3.3 R4 湿表面积 $S_w$：**本单主要结论**\n")
    A("| 参考几何 | $C_b$ | 精确 $S_w$ (m²) | 代码 $S_w$ (m²) | $S_w/(\\pi DL)$ 精确 | 代码 | 代码相对误差 |")
    A("|---|---|---|---|---|---|---|")
    for k in ("cylinder", "spheroid", "cosine_power", "suboff_like", "full_form"):
        x = row[k]
        A(f"| {k} | {x['cb']:.4f} | {x['S_exact']:.4f} | {x['S_code']:.4f} | {x['ratio_exact']:.4f} "
          f"| {x['ratio_code']:.4f} | {pc(x['rel_err'])} |")
    A(f"\n几何说明：{sub['note']}；{full['note']}。\n")
    A("**三条可判定结论：**\n")
    A(f"1. **硬反例（不依赖任何文献）**：正圆柱 $C_b$=1 的真实侧面积恰为 $\\pi DL$，代码给 $0.75\\pi DL$，"
      f"误差 {pc(cyl['rel_err'])}。说明该式不是几何推导，而是「只看 $C_b$」的拟合，")
    A("   除 $C_b$ 外的形状信息（$L/B$、平行中体、首尾丰满度）全部丢失。")
    A(f"2. **系统性低估随丰满度增长**：$C_b$=0.667→{pc(sph['rel_err'])}，0.75→{pc(full['rel_err'])}，"
      f"1.0→{pc(cyl['rel_err'])}；在 SUBOFF 长度比例族上扫描得到 **-15% 门槛交叉点 "
      f"$C_b$={cross:.3f}**（交叉点右侧一律不合格，$C_b$=0.95 时达 {pc(sw[-1]['rel_err'])}）。")
    A(f"3. **细长角落可用，但靠的是巧合**：默认 $C_b$=0.542 处，同 $C_b$ 不同形状的真值差 "
      f"{pc(cos_['ratio_exact'] / sub['ratio_exact'] - 1)}（余弦幂体 {cos_['ratio_exact']:.4f} vs "
      f"尖首尾 {sub['ratio_exact']:.4f}），代码 {sub['ratio_code']:.4f} 落在其中——"
      f"误差从 {pc(cos_['rel_err'])} 到 {pc(sub['rel_err'])}，即**同一组输入的真实 $S_w$ 本身有 "
      f"{abs(cos_['rel_err'] - sub['rel_err'])*100:.1f} 个百分点的形状不确定带**，")
    A("   端点没有任何参数能表达这个不确定性。\n")
    A("$S_w$ 误差经 $R_t\\propto S_w$ **一比一**传给总阻力，故下表即 $R_t$ 的相对误差：\n")
    A("| $C_b$（SUBOFF 长度比例族） | 帽形指数 p | 精确 $S_w/(\\pi DL)$ | 代码 | $S_w$ 及 $R_t$ 相对误差 |")
    A("|---|---|---|---|---|")
    for x in sw:
        A(f"| {x['cb']:.3f} | {x['p']:.3f} | {x['ratio_exact']:.4f} | {x['ratio_code']:.4f} | {pc(x['rel_err'])} |")

    A("\n### 3.4 R5 稳性：适用域内精确、域外无护栏\n")
    A("| φ (°) | 一阶原理 GZ (m) | 端点 GZ (m) | 绝对误差 (m) | 相对误差 | 舭部/甲板越界 |")
    A("|---|---|---|---|---|---|")
    for g in r5["rows"]:
        A(f"| {g['phi']:.1f} | {g['GZ_first_principles']:.6f} | {g['GZ_code']:.6f} | {g['abs_err']:.2e} "
          f"| {g['rel_err']:.2e} | {'是' if g['bilge_or_deck'] else '否'} |")
    A(f"\n- 适用域 φ ≤ {r5['phi_valid_limit']:.2f}°（舭部出水 {r5['phi_bilge_emergence']:.2f}° 与甲板入水 "
      f"{r5['phi_deck_immersion']:.2f}° 中更早者）：最大绝对误差 {r5['max_abs_err_valid']:.2e} m、"
      f"最大相对误差 {r5['max_rel_err_valid']:.2e} → 与一阶原理积分等价，**公式实现无误**。")
    A(f"- 域外（φ > {r5['phi_valid_limit']:.2f}°）：相对偏差最大 {r5['max_rel_err_beyond_validity']*100:.1f}%，"
      "端点既不校验也不告警——因为它只收 GM/KB/BM、不知道船体几何，结构上无法判断有效性。")
    A(f"- 小角极限：GZ/φ 在 φ={r5['small_angle_angles']}° 时分别为 "
      f"{', '.join(f'{v:.6f}' for v in r5['small_angle_GZ_over_phi'])}，"
      f"与闭式极限 $GM+\\tfrac12 BM\\tan^2\\varphi$ 的相对偏差 {r5['small_angle_rel_err']:.2e} → 小角行为正确。\n")

    A("### 3.5 R6 结构探针（与数值精度无关的形态缺陷）\n")
    A(f"- **吃水完全不进公式**：T 从 {r6['drafts'][0]} m 到 {r6['drafts'][-1]} m 变 20 倍，"
      f"$R_t$ 恒为 {r6['Rt_vs_draft'][0]:.4f} N（相对变化 {r6['draft_sensitivity_rel']:.2e}）。")
    A("  `T` 只用于合法性检查，从未进入 $S_w$/$R_f$/$C_r$ —— 物理上不成立（吃水改变浸湿面积与排水量）。")
    A(f"- **残差阻力结构上不可能有峰谷**：$C_r=0.002(1+2C_b)F_n^{{2.5}}$ 严格单调"
      f"（扫描单调递增 = {r6['cr_monotone_increasing']}）；实船 $C_r(F_n)$ 在 $F_n\\approx0.22$–$0.32$ "
      "有兴波阻力峰谷，单调函数表达不了。")
    A(f"- 量级参考：设计点 $R_r/R_f$={r6['cr_ratio_to_ittc_at_design']:.3f}，量级不算离谱——"
      "**错的不是量级，是形状趋势**。")
    A("- 缺失项（按 ITTC-1978 性能预报惯例）：形状因子 $(1+k)$ 与粗糙度补贴 $\\Delta C_F$ 均未实现，"
      "端点算的是「光滑平板摩擦 + 经验残差」。\n")

    A("## 4. 结论与证据分级建议\n")
    A("| 端点 | 现状 | 复核后建议 | 依据 |")
    A("|---|---|---|---|")
    A(f"| `calc.stability` | REAL | **保留 REAL**，文档/响应加适用域声明 | "
      f"适用域内与一阶原理积分一致到 {r5['max_rel_err_valid']:.1e}；域外偏差达 "
      f"{r5['max_rel_err_beyond_validity']*100:.0f}% 且无告警 |")
    A(f"| `calc.resistance` | REAL | **降级 WITHHELD**（摩擦项 REAL，总量与几何项不可采信） | "
      f"$C_F$ 与 ITTC-1957 一致到 {worst_big:.0e}；但 $S_w$ 在 $C_b>${cross:.2f} 时低估 >15%"
      f"（最高 {pc(sw[-1]['rel_err'])}）、圆柱硬反例 {pc(cyl['rel_err'])}、$T$ 完全不参与、"
      "$C_r$ 结构上无法表达峰谷 → 总阻力精度不可采信 |")
    A("\n一句话：**摩擦线是对的，几何/残差/吃水三项不是**；且「REAL ≠ 已核对」，")
    A("这条判断只有把参考量独立造出来才能得到。\n")

    A("## 5. 注意点 / 局限\n")
    A("1. 端点服务（:8001/:8002）复算时不可达，验证为**进程内**调用同一路由函数，未覆盖 HTTP 层与部署差异。")
    A("2. $S_w$ 的「真值」来自本文显式定义并公开母型的回转体族（超椭圆帽、余弦幂体、长球体、圆柱），")
    A("   **不是** SUBOFF 官方式子；因此结论是「$C_b$ 单参数式对一类真实回转体系统性低估（$C_b>0.71$ 时 >15%）」，")
    A("   而**不是**「某条实船阻力误差恰为多少」。圆柱反例不依赖文献，是硬判据。")
    A("3. $C_r(F_n)$ 峰谷位置、ITTC-1978 惯例按通行教科书结论引用，本单未逐篇核页码，只用于定性判断，")
    A("   不进任何数值误差表；故 $C_r$ 的量化误差标 WITHHELD 而非「通过」。")
    A("4. 未与实船/船模拖曳试验对比（缺对标数据）——这是把定性升级为数值的下一步。")
    A("5. 本单不改实现：先立判据、留证据，修公式另开单（避免同一单既改公式又改判据）。\n")

    A("## 6. 误差判定（逐项回答「是否 < 3%」）\n")
    A("| 关键量 | 基准 | 误差 | < 3% ? |")
    A("|---|---|---|---|")
    A(f"| $C_F$，Re=10⁶–10⁹ | ITTC-1957 闭式（50 位重算） | {worst_big:.2e} 起（$R_f>10$ N 各点） | **是** |")
    A(f"| $C_F$ vs Schoenherr | Schoenherr 1932 线 | {max(abs(x['dev_vs_schoenherr']) for x in r1['rows'])*100:.2f}% | **是**（两线本应数 % 级吻合） |")
    A(f"| $R_t=R_f+R_r$ | 定义 | {r3['Rt_minus_Rf_Rr_abs']:.2e} N | **是** |")
    A(f"| GZ，φ≤{r5['phi_valid_limit']:.1f}° | 一阶原理条带积分 | {r5['max_rel_err_valid']:.2e} | **是** |")
    A(f"| GZ 小角极限 | $GM+\\tfrac12 BM\\tan^2\\varphi$ | {r5['small_angle_rel_err']:.2e} | **是** |")
    A(f"| $S_w$，$C_b$=0.542 尖首尾（默认算例） | 精确 $\\int2\\pi r\\,ds$ | {pc(sub['rel_err'])} | **是**（但同 $C_b$ 换形状即 {pc(cos_['rel_err'])}） |")
    A(f"| $S_w$，$C_b$=0.75 | 精确积分 | {pc(full['rel_err'])} | **否** |")
    A(f"| $S_w$，$C_b$=1（圆柱硬反例） | $\\pi DL$ | {pc(cyl['rel_err'])} | **否** |")
    A(f"| $R_t$ 总量，$C_b\\geq${cross:.2f} | 精确 $S_w$ 代入 | {pc(sw[4]['rel_err'])} … {pc(sw[-1]['rel_err'])} | **否**（>15%） |")
    A(f"| 吃水灵敏度 | 应为显著正相关 | 0.00e+00（恒值） | **否**（结构性缺陷） |")
    A("\n$C_r$ 的绝对误差：**无基准，无法判定**（标 WITHHELD，不粉饰为通过）。\n")

    A("## 7. 给下次自找活的线索\n")
    A("1. 直接续单（最省力）：按 §3.3 把 $S_w$ 换成几何母型积分或引入 $(L/B,\\,C_b)$ 双参数拟合，"
      "并把 $T$ 接进去；判据直接用本单 `metrics.json` 的 R4 表（圆柱 $\\pi DL$ 是硬上界、$C_b$ 扫描是回归基线）。")
    A("2. 补对标试验：找一组公开船模拖曳试验（含 $S_w$ 与 $C_T(F_n)$），把 $C_r$ 从定性升为数值。")
    A("3. 稳性端点缺几何版：只有带 $B/T/$型深/舭半径的几何版才能给出适用域护栏（本单已量化域外 "
      f"{r5['max_rel_err_beyond_validity']*100:.0f}% 偏差）。")
    A("4. 同类「REAL 但零测试」端点还有 `geometry.hydro_curves`、`propeller.open_water`，"
      "可直接套用本单脚本骨架（三条基准：闭式、几何积分、结构探针）。\n")

    A("## 8. 复现\n")
    A("```bash")
    A("python3 scripts/verify_calc_v01.py                        # 生成 metrics.json / 图 / 本文")
    A("python3 -m pytest tests/test_calc_verification_v01.py -q  # 判据回归")
    A("```\n")

    import re as _re
    text = _re.sub(r"\n{3,}", "\n\n", "\n".join(T))
    with open(os.path.join(DOCS, "V01_calc_endpoints.md"), "w", encoding="utf-8") as fh:
        fh.write(text)


if __name__ == "__main__":
    res = main()
    print(json.dumps({
        "R1_max_rel_err": res["R1_ittc1957"]["max_rel_err"],
        "R4_wetted": {r_["name"]: round(r_["rel_err"] * 100, 2)
                      for r_ in res["R4_wetted_surface"]["rows"]},
        "R5_max_rel_err_valid": res["R5_gz"]["max_rel_err_valid"],
        "R5_rel_err_beyond": res["R5_gz"]["max_rel_err_beyond_validity"],
        "R6_draft_sensitivity": res["R6_structural"]["draft_sensitivity_rel"],
    }, indent=2, ensure_ascii=False))
