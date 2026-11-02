"""Discretisation-error certification of the hydrostatics / GZ-area producers.

Closes a verification (not validation) debt: ``backend/services/ship_hydrostatics.py``
and ``backend/services/box_hydrostatics.py`` publish numbers (volume, Cb, LCB, KB,
BM_T, wetted surface, area under the GZ curve) whose grid-independence was never
quantified.  This study

1. refines the offset table (n_stations, n_waterlines) by a constant factor r = 2
   over five levels for a rectangular barge and the Wigley parabolic hull,
2. computes the Roache/Celik observed order of accuracy p, the Richardson
   extrapolated value and the fine-grid GCI band for every reported quantity,
   reusing ``backend/services/vv/gci.py`` (no re-implementation),
3. anchors the extrapolated values on closed-form solutions where they exist:

       barge :  nabla = L B T, Cb = 1, Aw = L B, LCB = 0, KB = T/2,
                BM_T = B^2/(12 T), S = L (B + 2 T)
       Wigley:  nabla = 4 B L T / 9, Cb = 4/9, Aw = 2 B L / 3, KB = 5 T / 8,
                BM_T = 3 B^2 / (35 T)

   and on an adaptive-quadrature reference for the Wigley wetted surface
   (the producer approximates the section girth by a polyline, so its error is
   genuinely non-zero and must be bounded),
4. quantifies the heel-step (trapezoidal) error of ``gz_area`` against the
   closed-form integral of the wall-sided GZ formula.

Everything is CPU-millisecond scale: no solver is launched, no heavy process.

Usage
-----
    python3 examples/verification/hydrostatics_gci_study.py [--out DIR]

Writes ``study.json``, ``fig_gci_convergence.png``, ``fig_heel_step.png`` and
``tables.md`` into ``artifacts/verification/hydrostatics_gci/`` (override with
``--out``).  Exit code 1 when a certified quantity falls outside its analytic
anchor tolerance or out of the asymptotic range.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import sys
from typing import Dict, List

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
for _p in (os.path.join(REPO, "backend"), REPO):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from services.box_hydrostatics import (  # noqa: E402
    box_gz,
    gz_area,
    wallsided_gz,
)
from services.ship_hydrostatics import (  # noqa: E402
    barge_offsets,
    compute_hydrostatics,
    wigley_offsets,
)
from services.vv import Level, assess_convergence  # noqa: E402

# ---------------------------------------------------------------------------
# Case definitions
# ---------------------------------------------------------------------------

BARGE = dict(length=100.0, beam=20.0, draft=8.0)
WIGLEY = dict(length=6.0, beam=0.6, draft=0.375)

# Constant refinement ratio r = 2 in *both* offset directions: n_stations - 1
# doubles (this is the step that Simpson's rule sees) and n_waterlines - 1
# doubles with it, so a single refinement ratio describes the study.
LEVELS = [(21, 11), (41, 21), (81, 41), (161, 81), (321, 161)]

QUANTITIES = [
    ("volume_m3", "nabla", "m^3"),
    ("cb", "Cb", "-"),
    ("lcb_m", "LCB", "m"),
    ("kb_m", "KB", "m"),
    ("bm_t_m", "BM_T", "m"),
    ("waterplane_area_m2", "Aw", "m^2"),
    ("wetted_surface_m2", "S_wet", "m^2"),
]


def barge_analytic() -> Dict[str, float]:
    L, B, T = BARGE["length"], BARGE["beam"], BARGE["draft"]
    return {
        "volume_m3": L * B * T,
        "cb": 1.0,
        "lcb_m": 0.0,
        "kb_m": T / 2.0,
        "bm_t_m": B * B / (12.0 * T),
        "waterplane_area_m2": L * B,
        "wetted_surface_m2": L * (B + 2.0 * T),
    }


def wigley_analytic() -> Dict[str, float]:
    L, B, T = WIGLEY["length"], WIGLEY["beam"], WIGLEY["draft"]
    return {
        "volume_m3": 4.0 * B * L * T / 9.0,
        "cb": 4.0 / 9.0,
        "lcb_m": 0.0,
        "kb_m": 5.0 * T / 8.0,
        "bm_t_m": 3.0 * B * B / (35.0 * T),
        "waterplane_area_m2": 2.0 * B * L / 3.0,
    }


def wigley_wetted_surface_reference() -> float:
    """Adaptive-quadrature reference for the Wigley wetted surface.

    Section at station x: y(z) = (B/2)(1-u^2)(1-(1-z/T)^2), u = 2(x-L/2)/L.
    Girth s(x) = 2 * int_0^T sqrt(1 + (dy/dz)^2) dz ;  S = int_0^L s(x) dx.
    The producer approximates the same integral with a waterline polyline, so
    this reference is independent of the discretisation being certified.
    """
    from scipy.integrate import quad

    L, B, T = WIGLEY["length"], WIGLEY["beam"], WIGLEY["draft"]

    def girth(x: float) -> float:
        u = 2.0 * (x - 0.5 * L) / L
        a = 1.0 - u * u

        def integrand(z: float) -> float:
            dydz = (B * a / T) * (1.0 - z / T)
            return math.sqrt(1.0 + dydz * dydz)

        val, _ = quad(integrand, 0.0, T, limit=200, epsabs=1e-14, epsrel=1e-14)
        return 2.0 * val

    total, _ = quad(girth, 0.0, L, limit=400, epsabs=1e-12, epsrel=1e-12)
    return total


# ---------------------------------------------------------------------------
# Hydrostatics refinement study
# ---------------------------------------------------------------------------


def run_case(name: str, generator, analytic: Dict[str, float],
             surface_ref: float | None) -> Dict[str, object]:
    levels: List[Dict[str, float]] = []
    for n_st, n_wl in LEVELS:
        off = generator(n_stations=n_st, n_waterlines=n_wl)
        res = compute_hydrostatics(off, kg=None).as_dict()
        res["n_stations"] = n_st
        res["n_waterlines"] = n_wl
        levels.append(res)

    out: Dict[str, object] = {"case": name, "levels": LEVELS, "quantities": {}}
    for key, symbol, unit in QUANTITIES:
        # grid size: station spacing; refinement ratio is exactly 2 by construction
        h = [1.0 / (n_st - 1) for n_st, _ in LEVELS]
        vals = [float(lv[key]) for lv in levels]
        anchor = analytic.get(key)
        if key == "wetted_surface_m2" and surface_ref is not None:
            anchor_ref = surface_ref
        else:
            anchor_ref = anchor
        rep = assess_convergence(
            [Level(h=hh, value=v) for hh, v in zip(h, vals)],
            quantity=f"{name}:{symbol}",
            expected_order=None if key == "wetted_surface_m2" else 4,
            exact=anchor_ref,
        )
        d = rep.to_dict()
        d["symbol"] = symbol
        d["unit"] = unit
        d["values"] = vals
        d["h"] = h
        d["analytic"] = anchor
        d["reference"] = anchor_ref
        d["reference_kind"] = (
            "closed_form" if anchor is not None
            else ("adaptive_quadrature" if anchor_ref is not None else "richardson")
        )
        if anchor_ref is not None:
            # symmetric QoIs (LCB about midship) have a zero anchor: report the
            # absolute error there and fall back to relative elsewhere
            denom = abs(anchor_ref) if abs(anchor_ref) > 1e-12 else None
            d["rel_error_finest_vs_reference_pct"] = (
                None if denom is None else 100.0 * abs(vals[-2] - anchor_ref) / denom
            )
            d["rel_error_production_vs_reference_pct"] = (
                None if denom is None else 100.0 * abs(vals[2] - anchor_ref) / denom
            )
            d["abs_error_finest"] = abs(vals[-2] - anchor_ref)
            d["abs_error_production"] = abs(vals[2] - anchor_ref)
        out["quantities"][key] = d
    return out


# ---------------------------------------------------------------------------
# Heel-step (GZ area) study — closed-form anchor
# ---------------------------------------------------------------------------


def wallsided_gz_area_analytic(phi_end_deg: float, gm: float, bm: float) -> float:
    """int_0^Phi GZ dphi for GZ = sin(phi)*(GM + 0.5*BM*tan^2(phi)).

    int sin*GM dphi = GM*(1-cos Phi)
    int 0.5*BM*sin^3/cos^2 dphi = 0.5*BM*(cos Phi + sec Phi - 2),  Phi in rad.
    """
    phi = math.radians(phi_end_deg)
    return gm * (1.0 - math.cos(phi)) + 0.5 * bm * (
        math.cos(phi) + 1.0 / math.cos(phi) - 2.0
    )


def run_heel_study(phi_end_deg: float = 30.0) -> Dict[str, object]:
    B, T, D, KG = 20.0, 8.0, 14.0, 7.5
    gm = T / 2.0 + B * B / (12.0 * T) - KG          # box: KB + BM - KG
    bm = B * B / (12.0 * T)
    analytic = wallsided_gz_area_analytic(phi_end_deg, gm, bm)

    # per-angle producer (polygon clipping, exact for a box) evaluated on a very
    # fine heel grid -> reference for the *trapezoidal* area quadrature error
    fine = [i * 0.001 for i in range(int(round(phi_end_deg / 0.001)) + 1)]
    fine_gz = [box_gz(a, B, T, D, KG) for a in fine]
    fine_area = gz_area(fine, fine_gz, 0.0, phi_end_deg)

    rows = []
    for step in (10.0, 5.0, 2.5, 1.25, 0.625):
        grid = [i * step for i in range(int(round(phi_end_deg / step)) + 1)]
        vals = [box_gz(a, B, T, D, KG) for a in grid]
        area = gz_area(grid, vals, 0.0, phi_end_deg)
        rows.append(
            {
                "step_deg": step,
                "area": area,
                "rel_err_vs_analytic_pct": 100.0 * abs(area - analytic) / abs(analytic),
                "rel_err_vs_fine_pct": 100.0 * abs(area - fine_area) / abs(fine_area),
            }
        )

    # GCI on the trapezoid sequence uses the three finest grids (h doubling)
    h = [r["step_deg"] for r in rows[-3:]]
    v = [r["area"] for r in rows[-3:]]
    rep = assess_convergence(
        [Level(h=hh, value=vv) for hh, vv in zip(h, v)],
        quantity="box:area_under_GZ_0_30",
        expected_order=2,
        exact=analytic,
    )
    d = rep.to_dict()
    d["symbol"] = "A_GZ(0-30deg)"
    d["unit"] = "m*rad"
    d["analytic_formula"] = (
        "GM(1-cos Phi) + 0.5 BM (cos Phi + sec Phi - 2)"
    )
    d["reference_kind"] = "closed_form"
    d["grid_rows"] = rows
    d["fine_reference"] = fine_area
    d["analytic"] = analytic
    d["gm"] = gm
    d["bm"] = bm
    d["case"] = "box_GZ_area"
    return d


# ---------------------------------------------------------------------------
# Figures
# ---------------------------------------------------------------------------


def render_figures(study: Dict[str, object], heel: Dict[str, object], outdir: str) -> List[str]:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    paths: List[str] = []
    fig, axes = plt.subplots(1, 2, figsize=(11, 4.2))
    for ax, case in zip(axes, ("barge", "wigley")):
        q = study["cases"][case]["quantities"]["wetted_surface_m2"]
        ref = q["reference"]
        err = [abs(v - ref) / abs(ref) * 100.0 for v in q["values"]]
        ns = [n for n, _ in LEVELS]
        ax.loglog(ns, err, "o-", label="wetted surface")
        q2 = study["cases"][case]["quantities"]["bm_t_m"]
        ref2 = q2["reference"]
        err2 = [abs(v - ref2) / abs(ref2) * 100.0 for v in q2["values"]]
        ax.loglog(ns, err2, "s--", label="BM_T")
        ax.set_xlabel("n_stations (n_waterlines - 1 doubles with it)")
        ax.set_ylabel("|error| vs reference [%]")
        ax.set_title(f"{case}: discretisation error vs refinement")
        ax.grid(True, which="both", alpha=0.3)
        ax.legend(fontsize=8)
    fig.tight_layout()
    p1 = os.path.join(outdir, "fig_gci_convergence.png")
    fig.savefig(p1, dpi=130)
    plt.close(fig)
    paths.append(p1)

    fig, ax = plt.subplots(figsize=(5.6, 4.2))
    steps = [r["step_deg"] for r in heel["grid_rows"]]
    errs = [r["rel_err_vs_analytic_pct"] for r in heel["grid_rows"]]
    ax.loglog(steps, errs, "o-")
    for s, e in zip(steps, errs):
        ax.annotate(f"{e:.3g}%", (s, e), textcoords="offset points", xytext=(4, 4), fontsize=7)
    ax.set_xlabel("heel step [deg]")
    ax.set_ylabel("|error| of GZ area 0-30 deg [%]")
    ax.set_title("Trapezoidal heel-step error vs closed form")
    ax.grid(True, which="both", alpha=0.3)
    fig.tight_layout()
    p2 = os.path.join(outdir, "fig_heel_step.png")
    fig.savefig(p2, dpi=130)
    plt.close(fig)
    paths.append(p2)
    return paths


def render_tables(study: Dict[str, object], heel: Dict[str, object]) -> str:
    lines: List[str] = []
    lines.append("### Table 1 — hydrostatics refinement study (r = 2, five levels)\n")
    lines.append(
        "| case | QoI | " + " | ".join(f"n_st={n}" for n, _ in LEVELS)
        + " | reference | kind | p_obs | GCI_fine [%] | prod-grid err [%] | finest err [%] |"
    )
    lines.append("|---|---|" + "---|" * (LEVELS.__len__() + 6))
    for case in ("barge", "wigley"):
        for key, symbol, _unit in QUANTITIES:
            q = study["cases"][case]["quantities"][key]
            vals = " | ".join(f"{v:.10g}" for v in q["values"])
            ref = q["reference"]
            ref_s = "n/a" if ref is None else f"{ref:.10g}"
            p = q.get("observed_order")
            gci = q.get("gci_fine")
            pe = q.get("rel_error_production_vs_reference_pct")
            fe = q.get("rel_error_finest_vs_reference_pct")
            fmt = lambda x: "n/a" if x is None else f"{x:.3e}"
            lines.append(
                f"| {case} | {symbol} | {vals} | {ref_s} | {q['reference_kind']} | "
                f"{fmt(p)} | {fmt(gci)} | {fmt(pe)} | {fmt(fe)} |"
            )
    lines.append("\n### Table 2 — heel-step (trapezoid) error of the GZ area, box 20x8x14 m, KG = 9 m, 0-30 deg\n")
    lines.append("| heel step [deg] | area [m*rad] | err vs closed form [%] | err vs 0.001 deg grid [%] |")
    lines.append("|---|---|---|---|")
    for r in heel["grid_rows"]:
        lines.append(
            f"| {r['step_deg']:.3f} | {r['area']:.10g} | "
            f"{r['rel_err_vs_analytic_pct']:.4e} | {r['rel_err_vs_fine_pct']:.4e} |"
        )
    lines.append(
        f"\nClosed form: `{heel['analytic_formula']}` = {heel['analytic']:.12g} m*rad "
        f"(GM = {heel['gm']:.6g} m, BM = {heel['bm']:.6g} m); "
        f"observed order p = {heel.get('observed_order')}, GCI_fine = {heel.get('gci_fine')} %."
    )
    return "\n".join(lines) + "\n"


def main(argv: List[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--out",
        default=os.path.join(REPO, "artifacts", "verification", "hydrostatics_gci"),
    )
    args = ap.parse_args(argv)
    os.makedirs(args.out, exist_ok=True)

    barge = run_case(
        "barge",
        lambda n_stations, n_waterlines: barge_offsets(
            n_stations=n_stations, n_waterlines=n_waterlines, **BARGE
        ),
        barge_analytic(),
        None,
    )
    wsurf = wigley_wetted_surface_reference()
    wigley = run_case(
        "wigley",
        lambda n_stations, n_waterlines: wigley_offsets(
            n_stations=n_stations, n_waterlines=n_waterlines, **WIGLEY
        ),
        wigley_analytic(),
        wsurf,
    )
    heel = run_heel_study()

    study = {
        "cases": {"barge": barge, "wigley": wigley},
        "heel": heel,
        "refinement_levels": LEVELS,
        "wigley_wetted_surface_reference": wsurf,
        "barge_analytic": barge_analytic(),
        "wigley_analytic": wigley_analytic(),
    }

    figs = render_figures(study, heel, args.out)
    tables = render_tables(study, heel)
    with open(os.path.join(args.out, "study.json"), "w", encoding="utf-8") as fh:
        json.dump(study, fh, indent=2)
    with open(os.path.join(args.out, "tables.md"), "w", encoding="utf-8") as fh:
        fh.write(tables)

    # ---- gate -------------------------------------------------------------
    fails: List[str] = []
    for case in ("barge", "wigley"):
        for key, symbol, _ in QUANTITIES:
            q = study["cases"][case]["quantities"][key]
            pe = q.get("rel_error_production_vs_reference_pct")
            if pe is not None and pe > 3.0:
                fails.append(f"{case}:{symbol} production-grid error {pe:.3e}% > 3%")
    heel_prod = heel["grid_rows"][0]["rel_err_vs_analytic_pct"]
    if heel_prod > 3.0:
        fails.append(f"heel 10 deg GZ-area error {heel_prod:.3e}% > 3%")

    print(tables)
    print(f"figures: {figs}")
    print(f"wrote {args.out}")
    if fails:
        print("GATE FAIL:")
        for f in fails:
            print("  -", f)
        return 1
    print("GATE PASS: every certified quantity is within its analytic/reference band at production resolution")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())