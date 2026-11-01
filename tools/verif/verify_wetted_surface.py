"""Verification harness: wetted-surface models behind /calculations/resistance.

Mission m1686 (selfstart tp-impl-8).  Read-only against the platform modules;
writes JSON + PNG into ``outputs/wetted_surface/``.

What is checked (each with an independent reference, not self-consistency):

R1  Holtrop & Mennen (1982) wetted-surface regression
    (``services.holtrop_mennen.wetted_surface``, reachable through
    ``POST /calculations/resistance`` with ``hull_form="ship"``)
    vs the published KVLCC2 value S = 27 194 m^2 (SIMMAN 2008 hull table).

R2  Legacy estimator ``S = pi*B*L*(0.5+0.25*Cb)`` vs the *exact* wetted surface
    of a prolate spheroid (closed form), i.e. the shape family it was fitted to.
    Then the same estimator vs R1 on a KVLCC2-class hull: the documented
    "axisymmetric fit applied to a ship" bias.

R3  Platform geometry integration ``services.ship_hydrostatics`` (transverse
    girth x station spacing) vs a high-resolution numerical integral of the
    exact Wigley surface parametrisation (quasi-analytic reference, with its own
    convergence study).

R4  Consequence for resistance: total resistance / C_T vs Froude number for
    KVLCC2 with the legacy S and with the Holtrop S (same ITTC-1957 friction
    line, same residual fit), so the S bias is translated into the quantity the
    platform actually quotes.

Usage:  python3 tools/verif/verify_wetted_surface.py [--no-figures]
"""
from __future__ import annotations

import argparse
import asyncio
import importlib.util
import json
import math
import sys
from pathlib import Path
from typing import Dict, List, Tuple

REPO = Path(__file__).resolve().parents[2]
BACKEND = REPO / "backend"
OUTDIR = REPO / "outputs" / "wetted_surface"
sys.path.insert(0, str(BACKEND))

RHO = 998.0
NU = 1.004e-6

# --- published reference hull (SIMMAN 2008 / Tokyo 2015 KVLCC2) --------------
KVLCC2 = dict(
    length=320.0, beam=58.0, draft=20.8, cb=0.8098, cm=0.998, cwp=0.8748,
    volume_m3=312622.0, wetted_surface_m2=27194.0,
)

# --- exact closed-form references -------------------------------------------
def spheroid_surface(length: float, diameter: float) -> float:
    """Exact wetted surface of a prolate spheroid (semi-axes a, b=b)."""
    a = 0.5 * length
    b = 0.5 * diameter
    # ellipsoid with a > b = c:  S = 2*pi*b^2 * (1 + a/(b*e) * asin(e)) for a>b
    e = math.sqrt(1.0 - (b * b) / (a * a))
    return 2.0 * math.pi * b * b * (1.0 + (a / (b * e)) * math.asin(e))


def barge_surface(length: float, beam: float, draft: float) -> float:
    """Exact wetted surface of a rectangular barge (bottom + 2 sides)."""
    return length * beam + 2.0 * length * draft


def wigley_surface_reference(length: float, beam: float, draft: float,
                             n: int = 400) -> float:
    """Quasi-analytic wetted surface of the Wigley parabolic hull.

    y(x, z) = (B/2) (1 - (2x/L)^2) (1 - (z/T)^2),  x in [-L/2, L/2], z in [0, T]
    S = 2 * int int sqrt(1 + y_x^2 + y_z^2) dz dx, evaluated with composite
    Simpson on an n x n grid (n must be even).
    """
    dx = length / n
    dz = draft / n

    def integrand(x: float, z: float) -> float:
        sx = 1.0 - (2.0 * x / length) ** 2
        sz = 1.0 - (z / draft) ** 2
        yx = (beam / 2.0) * (-8.0 * x / (length * length)) * sz
        yz = (beam / 2.0) * sx * (-2.0 * z / (draft * draft))
        return math.sqrt(1.0 + yx * yx + yz * yz)

    def simpson_weights(npts: int) -> List[float]:
        w = [2.0] * (npts + 1)
        w[0] = w[-1] = 1.0
        for i in range(1, npts, 2):
            w[i] = 4.0
        return w

    wx = simpson_weights(n)
    wz = simpson_weights(n)
    total = 0.0
    for i in range(n + 1):
        x = -0.5 * length + i * dx
        row = 0.0
        for j in range(n + 1):
            z = j * dz
            row += wz[j] * integrand(x, z)
        total += wx[i] * row
    total *= (dx / 3.0) * (dz / 3.0)
    return 2.0 * total


def wigley_convergence(length: float, beam: float, draft: float,
                       levels: Tuple[int, ...] = (40, 80, 160, 320, 640)) -> List[Dict[str, float]]:
    rows: List[Dict[str, float]] = []
    prev = None
    for n in levels:
        s = wigley_surface_reference(length, beam, draft, n=n)
        rows.append({
            "n": n,
            "surface_m2": s,
            "richardson_delta_pct": None if prev is None else abs(s - prev) / prev * 100.0,
        })
        prev = s
    return rows


# --- module loading (no DB / no FastAPI app startup) ------------------------
def load_calculations():
    spec = importlib.util.spec_from_file_location(
        "tp_ws_verif_calc", BACKEND / "api" / "calculations.py"
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules["tp_ws_verif_calc"] = mod
    spec.loader.exec_module(mod)
    return mod


def pct(a: float, ref: float) -> float:
    return (a - ref) / ref * 100.0


def run_resistance(calc, **kwargs):
    req = calc.ResistanceRequest(**kwargs)
    return asyncio.run(calc.calc_resistance(req))


def holtrop_resistance_via_api(calc, hull: Dict[str, float], speed_kn: float):
    return run_resistance(
        calc,
        ship_type="KVLCC2",
        speed_ms=speed_kn * 0.514444,
        length=hull["length"],
        beam=hull["beam"],
        draft=hull["draft"],
        cb=hull["cb"],
        hull_form="ship",
        midship_coefficient=hull["cm"],
        waterplane_coefficient=hull["cwp"],
        water_density=RHO,
        kinematic_viscosity=NU,
    )


def legacy_resistance_via_api(calc, hull: Dict[str, float], speed_kn: float):
    return run_resistance(
        calc,
        ship_type="KVLCC2",
        speed_ms=speed_kn * 0.514444,
        length=hull["length"],
        beam=hull["beam"],
        draft=hull["draft"],
        cb=hull["cb"],
        hull_form="axisymmetric",
        water_density=RHO,
        kinematic_viscosity=NU,
    )


def build_report(calc) -> Dict[str, object]:
    from services.ship_hydrostatics import compute_hydrostatics, wigley_offsets

    result: Dict[str, object] = {"mission": "m1686", "references": {}}

    # ---------------- R1: Holtrop vs published KVLCC2 -----------------------
    s_holtrop = calc._holtrop_wetted_surface(
        KVLCC2["length"], KVLCC2["beam"], KVLCC2["draft"], KVLCC2["cb"],
        KVLCC2["cm"], KVLCC2["cwp"],
    )[0]
    s_holtrop_est = calc._holtrop_wetted_surface(
        KVLCC2["length"], KVLCC2["beam"], KVLCC2["draft"], KVLCC2["cb"],
    )[0]
    result["references"]["R1_kvlcc2"] = {
        "published_surface_m2": KVLCC2["wetted_surface_m2"],
        "holtrop_with_caller_coefficients_m2": round(s_holtrop, 2),
        "error_pct": round(pct(s_holtrop, KVLCC2["wetted_surface_m2"]), 3),
        "holtrop_with_estimated_coefficients_m2": round(s_holtrop_est, 2),
        "error_estimated_pct": round(pct(s_holtrop_est, KVLCC2["wetted_surface_m2"]), 3),
        "pass_3pct": abs(pct(s_holtrop, KVLCC2["wetted_surface_m2"])) < 3.0,
    }

    # ---------------- R2: legacy estimator on its own shape family ----------
    # prolate spheroid a/b = 4 (L/D = 8); Cb of that body of revolution on L*B*T
    # with B = D, T = D equals volume/(L*D*D) of the spheroid.
    L, D = 4.0, 0.5
    s_exact = spheroid_surface(L, D)
    cb_spheroid = (4.0 / 3.0) * math.pi * (D / 2) ** 3 / (L * D * D)
    s_legacy = calc._empirical_wetted_surface(L, D, cb_spheroid)
    result["references"]["R2_spheroid"] = {
        "length_m": L,
        "diameter_m": D,
        "cb_equivalent": round(cb_spheroid, 6),
        "exact_surface_m2": round(s_exact, 6),
        "legacy_surface_m2": round(s_legacy, 6),
        "legacy_error_pct": round(pct(s_legacy, s_exact), 3),
    }
    s_legacy_kvlcc2 = calc._empirical_wetted_surface(
        KVLCC2["length"], KVLCC2["beam"], KVLCC2["cb"]
    )
    result["references"]["R2_legacy_on_kvlcc2"] = {
        "legacy_surface_m2": round(s_legacy_kvlcc2, 2),
        "reference_surface_m2": KVLCC2["wetted_surface_m2"],
        "legacy_error_pct": round(pct(s_legacy_kvlcc2, KVLCC2["wetted_surface_m2"]), 3),
        "holtrop_error_pct": round(pct(s_holtrop, KVLCC2["wetted_surface_m2"]), 3),
    }

    # ---------------- R3: platform girth integration vs Wigley --------------
    WL, WB, WT = 10.0, 1.0, 0.625
    conv = wigley_convergence(WL, WB, WT)
    s_ref = conv[-1]["surface_m2"]
    grid_rows = []
    for n in (21, 41, 81, 161):
        offs = wigley_offsets(length=WL, beam=WB, draft=WT,
                              n_stations=n, n_waterlines=max(9, n // 2))
        hydro = compute_hydrostatics(offs)
        grid_rows.append({
            "n_stations": hydro.n_stations,
            "n_waterlines": hydro.n_waterlines,
            "cb_integrated": round(hydro.cb, 6),
            "cm_integrated": round(hydro.cm, 6),
            "cw_integrated": round(hydro.cw, 6),
            "surface_m2": round(hydro.wetted_surface_m2, 4),
            "error_vs_reference_pct": round(
                pct(hydro.wetted_surface_m2, s_ref), 3),
        })
    # Holtrop regression evaluated on the Wigley coefficients (out-of-domain probe)
    s_holtrop_wigley = calc._holtrop_wetted_surface(
        WL, WB, WT, grid_rows[-1]["cb_integrated"],
        grid_rows[-1]["cm_integrated"], grid_rows[-1]["cw_integrated"],
    )[0]
    result["references"]["R3_wigley"] = {
        "length_m": WL, "beam_m": WB, "draft_m": WT,
        "reference_convergence": conv,
        "reference_surface_m2": round(s_ref, 5),
        "grid_study": grid_rows,
        "holtrop_regression_surface_m2": round(s_holtrop_wigley, 4),
        "holtrop_regression_error_pct": round(pct(s_holtrop_wigley, s_ref), 3),
        "note": ("Holtrop is a ship-hull regression; the Wigley probe is an "
                 "out-of-domain extrapolation check, not an accuracy claim."),
    }

    # ---------------- R4: consequence for resistance ------------------------
    speeds = [7.0, 10.0, 13.0, 15.0, 15.5, 17.0]
    rows = []
    for kn in speeds:
        rh = holtrop_resistance_via_api(calc, KVLCC2, kn)
        rl = legacy_resistance_via_api(calc, KVLCC2, kn)
        rows.append({
            "speed_kn": kn,
            "froude_number": round(rh.froude_number, 5),
            "s_holtrop_m2": round(rh.wetted_surface_m2, 1),
            "s_legacy_m2": round(rl.wetted_surface_m2, 1),
            "rt_holtrop_n": round(rh.total_resistance_n, 1),
            "rt_legacy_n": round(rl.total_resistance_n, 1),
            "ct_holtrop": rh.total_resistance_coefficient,
            "ct_legacy": rl.total_resistance_coefficient,
            "ct_error_legacy_pct": round(
                pct(rl.total_resistance_coefficient,
                    rh.total_resistance_coefficient), 3),
        })
    result["references"]["R4_resistance"] = {
        "hull": "KVLCC2 (L=320 m, B=58 m, T=20.8 m, Cb=0.8098)",
        "rows": rows,
        "legacy_rt_overestimate_pct_range": [
            min(pct(r["rt_legacy_n"], r["rt_holtrop_n"]) for r in rows),
            max(pct(r["rt_legacy_n"], r["rt_holtrop_n"]) for r in rows),
        ],
        "ct_difference_pct": rows[0]["ct_error_legacy_pct"],
        "note": ("Every term of the simplified endpoint (Rf, Rform, Rr) is "
                 "proportional to S, so a wrong S cancels out of C_T and only "
                 "moves R_T (and hence EHP/power). C_T is therefore NOT a check "
                 "on the wetted surface."),
    }

    # ---------------- R5: cross-method: simplified vs full Holtrop ----------
    from services.holtrop_mennen import holtrop_resistance

    r5_rows = []
    for kn in (13.0, 15.5):
        v = kn * 0.514444
        api = holtrop_resistance_via_api(calc, KVLCC2, kn)
        full = holtrop_resistance(
            length=KVLCC2["length"], beam=KVLCC2["beam"], draft=KVLCC2["draft"],
            cb=KVLCC2["cb"], speed_ms=v, cm=KVLCC2["cm"], cwp=KVLCC2["cwp"],
            rho=RHO, nu=NU,
        )
        r5_rows.append({
            "speed_kn": kn,
            "simplified_endpoint_rt_n": round(api.total_resistance_n, 1),
            "full_holtrop_rt_n": round(full.rt_n, 1),
            "simplified_vs_full_pct": round(
                pct(api.total_resistance_n, full.rt_n), 3),
            "full_holtrop_ct": round(full.ct, 8),
            "simplified_ct": api.total_resistance_coefficient,
            "full_holtrop_wetted_surface_m2": round(full.wetted_surface_m2, 2),
            "form_factor_1k": round(full.form_factor, 4),
        })
    result["references"]["R5_cross_method"] = {
        "hull": "KVLCC2",
        "rows": r5_rows,
        "note": ("Same hull + same S, different residual/viscous treatment: the "
                 "simplified endpoint omits the published wave/residual "
                 "regression and the form factor, so it is a lower bound."),
    }
    return result


# --- figures ---------------------------------------------------------------
def make_figures(result: Dict[str, object]) -> List[str]:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import numpy as np

    OUTDIR.mkdir(parents=True, exist_ok=True)
    made: List[str] = []

    # fig 1: S model comparison vs published KVLCC2
    r1 = result["references"]["R1_kvlcc2"]
    r2l = result["references"]["R2_legacy_on_kvlcc2"]
    fig, ax = plt.subplots(figsize=(7.2, 4.2))
    labels = ["published\n(SIMMAN 2008)", "Holtrop 1982\n(caller coefficients)",
              "Holtrop 1982\n(Cm,Cwp estimated)", "legacy\npi*B*L*(0.5+0.25Cb)"]
    vals = [r1["published_surface_m2"], r1["holtrop_with_caller_coefficients_m2"],
            r1["holtrop_with_estimated_coefficients_m2"], r2l["legacy_surface_m2"]]
    errs = [0.0, r1["error_pct"], r1["error_estimated_pct"], r2l["legacy_error_pct"]]
    colors = ["#444", "#1f77b4", "#6baed6", "#d62728"]
    bars = ax.bar(labels, vals, color=colors)
    for b, v, e in zip(bars, vals, errs):
        ax.text(b.get_x() + b.get_width() / 2, v + 300,
                f"{v:,.0f}\n({e:+.1f}%)", ha="center", fontsize=8)
    ax.axhline(r1["published_surface_m2"], color="#444", ls="--", lw=1)
    ax.set_ylabel("wetted surface S [m²]")
    ax.set_title("KVLCC2 wetted surface: wetted-surface models vs published value")
    ax.set_ylim(0, max(vals) * 1.22)
    ax.grid(axis="y", alpha=0.3)
    fig.tight_layout()
    p = OUTDIR / "fig1_kvlcc2_surface_comparison.png"
    fig.savefig(p, dpi=150)
    plt.close(fig)
    made.append(str(p.relative_to(REPO)))

    # fig 2: legacy vs Holtrop error over a Cb sweep (same hull family)
    L, B, T = KVLCC2["length"], KVLCC2["beam"], KVLCC2["draft"]
    cbs = np.linspace(0.50, 0.86, 19)
    legacy, holt_est = [], []
    calc = load_calculations()
    for cb in cbs:
        s_l = calc._empirical_wetted_surface(L, B, float(cb))
        s_h = calc._holtrop_wetted_surface(L, B, T, float(cb))[0]
        # reference: Holtrop with caller-grade coefficients (Cm=0.9+0.1Cb is a
        # documented estimate; use fixed moulded Cm=0.998 to isolate the S model)
        s_ref = calc._holtrop_wetted_surface(L, B, T, float(cb), 0.998)[0]
        legacy.append(pct(s_l, s_ref))
        holt_est.append(pct(s_h, s_ref))
    fig, ax = plt.subplots(figsize=(7.2, 4.2))
    ax.plot(cbs, legacy, "o-", color="#d62728", label="legacy axisymmetric fit")
    ax.plot(cbs, holt_est, "s-", color="#1f77b4", label="Holtrop 1982 (Cm,Cwp estimated)")
    ax.axhspan(-3, 3, color="#2ca02c", alpha=0.15, label="±3 % band")
    ax.axhline(0, color="k", lw=0.8)
    ax.set_xlabel("block coefficient Cb")
    ax.set_ylabel("error in S vs Holtrop(Cm=0.998) [%]")
    ax.set_title(f"Wetted-surface error over a Cb sweep (L={L:.0f} m, B={B:.0f} m, T={T:.1f} m)")
    ax.legend(fontsize=8)
    ax.grid(alpha=0.3)
    fig.tight_layout()
    p = OUTDIR / "fig2_surface_error_vs_cb.png"
    fig.savefig(p, dpi=150)
    plt.close(fig)
    made.append(str(p.relative_to(REPO)))

    # fig 3: resistance consequence
    rows = result["references"]["R4_resistance"]["rows"]
    fn = [r["froude_number"] for r in rows]
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(11, 4.2))
    ax1.plot(fn, [r["rt_holtrop_n"] / 1e6 for r in rows], "s-", color="#1f77b4",
             label="S: Holtrop 1982")
    ax1.plot(fn, [r["rt_legacy_n"] / 1e6 for r in rows], "o--", color="#d62728",
             label="S: legacy axisymmetric fit")
    ax1.set_xlabel("Froude number Fn")
    ax1.set_ylabel("total resistance R_T [MN]")
    ax1.set_title("KVLCC2 R_T (ITTC-1957 + residual fit, same Cr)")
    ax1.legend(fontsize=8)
    ax1.grid(alpha=0.3)
    rt_err = [(r["rt_legacy_n"] / r["rt_holtrop_n"] - 1.0) * 100.0 for r in rows]
    ax2.plot(fn, rt_err, "o-", color="#d62728", label="R_T error (legacy S)")
    ax2.plot(fn, [r["ct_error_legacy_pct"] for r in rows], "s--", color="#1f77b4",
             label="C_T error (legacy S)")
    ax2.axhspan(-3, 3, color="#2ca02c", alpha=0.12, label="±3 % band")
    ax2.set_ylim(-10, max(rt_err) * 1.25)
    ax2.set_xlabel("Froude number Fn")
    ax2.set_ylabel("legacy-S vs Holtrop-S error [%]")
    ax2.set_title("Same wrong S: C_T cancels (all terms ∝ S), R_T does not")
    ax2.legend(fontsize=8)
    ax2.grid(alpha=0.3)
    fig.tight_layout()
    p = OUTDIR / "fig3_resistance_consequence.png"
    fig.savefig(p, dpi=150)
    plt.close(fig)
    made.append(str(p.relative_to(REPO)))

    # fig 4: Wigley geometry-integration convergence vs quasi-analytic reference
    r3 = result["references"]["R3_wigley"]
    grid = r3["grid_study"]
    fig, ax = plt.subplots(figsize=(7.2, 4.2))
    ax.semilogx([g["n_stations"] for g in grid],
                [g["error_vs_reference_pct"] for g in grid], "o-",
                label="platform girth integration")
    holt_err = r3["holtrop_regression_error_pct"]
    ax.axhline(holt_err, color="#1f77b4", ls="--",
               label=f"Holtrop regression ({holt_err:+.1f} %)")
    ax.axhspan(-3, 3, color="#2ca02c", alpha=0.12, label="±3 % band")
    ax.axhline(0, color="k", lw=0.8)
    ax.set_xlabel("n_stations (grid refinement)")
    ax.set_ylabel("error in S vs quasi-analytic Wigley [%]")
    ax.set_title("Wigley hull: girth integration convergence and Holtrop out-of-domain probe")
    ax.legend(fontsize=8)
    ax.grid(alpha=0.3)
    fig.tight_layout()
    p = OUTDIR / "fig4_wigley_convergence.png"
    fig.savefig(p, dpi=150)
    plt.close(fig)
    made.append(str(p.relative_to(REPO)))
    return made


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-figures", action="store_true")
    args = ap.parse_args()

    calc = load_calculations()
    result = build_report(calc)
    if not args.no_figures:
        result["figures"] = make_figures(result)
    OUTDIR.mkdir(parents=True, exist_ok=True)
    out = OUTDIR / "wetted_surface_verification.json"
    out.write_text(json.dumps(result, indent=2, ensure_ascii=False))
    print(json.dumps(result, indent=2, ensure_ascii=False))
    print(f"\nwritten: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())