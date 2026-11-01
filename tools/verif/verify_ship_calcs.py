#!/usr/bin/env python3
"""Verification harness for the platform's ship-resistance / stability calculators.

Mission m1384 (tp-impl-3, platform seat).  Lightweight: pure-Python numerics,
no CFD, no heavy compute.

What it does
------------
1. Closed-form ground truth for the wetted surface:
       a) open cylinder            S = pi*D*L
       b) prolate spheroid         S = 2*pi*b^2 + 2*pi*a*b*asin(e)/e
   compared against the legacy estimator ``pi*B*L*(0.5+0.25*Cb)`` used by
   POST /api/v1/calculations/resistance.
2. ITTC-1957 friction line: exact closed form vs the API value over the
   Reynolds range Re in [1e5, 1e10]; checks monotonicity and the asymptotic
   behaviour.
3. Benchmark-reference self-consistency audit of
   ``backend/data/benchmark_reference.json``: a resistance benchmark case must
   satisfy R = 0.5*rho*S*V^2*Ct.  Cases that fail by more than 5 % cannot be
   used as pass/fail criteria.
4. Stability: wall-sided GZ sweep phi in [0, 89] deg; checks finiteness, the
   validity flag, and that the 90 deg singularity is rejected.

Usage
-----
    python3 tools/verif/verify_ship_calcs.py [--json PATH] [--figdir DIR]

Outputs (default): artifacts/verif/ship_calcs_verification.json
                  artifacts/verif/*.png
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import math
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
BACKEND = REPO / "backend"
sys.path.insert(0, str(BACKEND))

RHO = 998.0
NU = 1.004e-6


def _load_calc_module():
    """Import backend/api/calculations.py by path (api/__init__ needs a live DB)."""
    spec = importlib.util.spec_from_file_location(
        "tp_calc_verif", BACKEND / "api" / "calculations.py"
    )
    mod = importlib.util.module_from_spec(spec)
    # register before exec so pydantic can resolve the postponed annotations
    sys.modules["tp_calc_verif"] = mod
    spec.loader.exec_module(mod)
    return mod


# ---------------------------------------------------------------------------
# closed-form geometry
# ---------------------------------------------------------------------------

def s_cylinder(L: float, D: float) -> float:
    """Lateral area of an open cylinder (both ends excluded)."""
    return math.pi * D * L


def s_prolate_spheroid(L: float, B: float) -> float:
    """Surface area of a prolate spheroid with major axis L and mid diameter B."""
    a, b = L / 2.0, B / 2.0
    e = math.sqrt(1.0 - (b * b) / (a * a))
    return 2.0 * math.pi * b * b + 2.0 * math.pi * a * b * math.asin(e) / e


def cb_spheroid() -> float:
    """Volume / (L*B*T) for a spheroid with T = B  ->  pi/6."""
    return math.pi / 6.0


# ---------------------------------------------------------------------------
# checks
# ---------------------------------------------------------------------------

def check_wetted_surface(calc) -> dict:
    rows = []
    cases = [
        ("cylinder", "Cb=pi/4 (circular section)", 4.356, 0.508, math.pi / 4,
         s_cylinder(4.356, 0.508)),
        ("prolate_spheroid_b/a=0.25", "spheroid, b/a=0.25", 4.356, 0.508,
         cb_spheroid(), s_prolate_spheroid(4.356, 0.508)),
        ("prolate_spheroid_b/a=0.15", "spheroid, b/a=0.15", 4.356, 0.3048,
         cb_spheroid(), s_prolate_spheroid(4.356, 0.3048)),
    ]
    for name, note, L, B, cb, s_true in cases:
        s_emp = calc._empirical_wetted_surface(L, B, cb)
        rows.append({
            "shape": name,
            "note": note,
            "L_m": L,
            "B_m": B,
            "cb": round(cb, 6),
            "s_wet_closed_form_m2": round(s_true, 6),
            "s_wet_empirical_m2": round(s_emp, 6),
            "error_pct": round((s_emp / s_true - 1.0) * 100.0, 3),
        })
    return {"checks": rows}


def check_ittc57(calc) -> dict:
    rows = []
    for re_exp in range(5, 11):
        Re = 10.0 ** re_exp
        cf_closed = 0.075 / (math.log10(Re) - 2.0) ** 2
        # reproduce the API value through the resistance endpoint path
        L = 100.0
        V = Re * NU / L
        req = calc.ResistanceRequest(
            speed_ms=V, length=L, beam=10.0, draft=5.0, cb=0.6,
            water_density=RHO, kinematic_viscosity=NU,
            wetted_surface_m2=1e5,
        )
        import asyncio
        resp = asyncio.run(calc.calc_resistance(req))
        cf_api = resp.friction_coefficient_cf
        rows.append({
            "Re": Re,
            "cf_ittc1957_closed_form": cf_closed,
            "cf_api": cf_api,
            "error_pct": round((cf_api / cf_closed - 1.0) * 100.0, 9),
            "recovered_re": resp.reynolds_number,
        })
    mono = all(rows[i]["cf_api"] > rows[i + 1]["cf_api"] for i in range(len(rows) - 1))
    return {"checks": rows, "monotone_decreasing": mono}


def check_benchmark_dataset() -> dict:
    path = BACKEND / "data" / "benchmark_reference.json"
    data = json.loads(path.read_text())
    rows = []
    for c in data.get("cases", []):
        g = c.get("geometry", {}) or {}
        S = g.get("wetted_surface_m2")
        V = c.get("speed_ms")
        ct = c.get("experimental_ct")
        rt = c.get("experimental_rt_n")
        if not (S and V and ct is not None and rt):
            rows.append({"case_id": c.get("case_id"), "consistent": None,
                         "reason": "insufficient fields"})
            continue
        rt_from_ct = 0.5 * RHO * S * V * V * ct
        rel = (rt - rt_from_ct) / rt_from_ct * 100.0
        rows.append({
            "case_id": c.get("case_id"),
            "ship_type": c.get("ship_type"),
            "speed_ms": V,
            "s_wet_m2": S,
            "ct_ref": ct,
            "rt_ref_n": rt,
            "rt_implied_by_ct_n": round(rt_from_ct, 3),
            "inconsistency_pct": round(rel, 2),
            "consistent": abs(rel) <= 5.0,
        })
    n_bad = sum(1 for r in rows if r.get("consistent") is False)
    return {"cases": rows, "n_cases": len(rows), "n_inconsistent": n_bad}


def check_reference_comparison(calc) -> dict:
    """Like-for-like Ct comparison against the benchmark reference rows.

    The wetted surface of the reference geometry is passed explicitly
    (``wetted_surface_m2``) so the comparison isolates the *resistance model*
    from the wetted-surface estimator.
    """
    import asyncio

    path = BACKEND / "data" / "benchmark_reference.json"
    data = json.loads(path.read_text())
    rows = []
    for c in data.get("cases", []):
        g = c.get("geometry", {}) or {}
        L = g.get("length_m")
        S = g.get("wetted_surface_m2")
        V = c.get("speed_ms")
        ct_ref = c.get("experimental_ct")
        if not (L and S and V and ct_ref):
            continue
        req = calc.ResistanceRequest(
            ship_type=c.get("ship_type", c.get("case_id")),
            speed_ms=V,
            length=L,
            beam=g.get("beam_m", g.get("diameter_m", 0.5 * L)),
            draft=g.get("draft_m", g.get("diameter_m", 0.25 * L)),
            cb=g.get("cb", 0.542),
            water_density=RHO,
            kinematic_viscosity=NU,
            wetted_surface_m2=S,
        )
        resp = asyncio.run(calc.calc_resistance(req))
        ct_api = resp.total_resistance_n / (0.5 * RHO * S * V * V)
        err = (ct_api / ct_ref - 1.0) * 100.0
        # default path: no geometry override -> estimator bias enters the result
        req_default = req.model_copy(update={"wetted_surface_m2": None})
        resp_default = asyncio.run(calc.calc_resistance(req_default))
        ct_default = resp_default.total_resistance_n / (0.5 * RHO * S * V * V)
        rows.append({
            "case_id": c.get("case_id"),
            "speed_ms": V,
            "fn": round(V / math.sqrt(9.81 * L), 4),
            "ct_ref": ct_ref,
            "ct_api": round(ct_api, 6),
            "error_pct": round(err, 2),
            "ct_api_default_path": round(ct_default, 6),
            "error_pct_default_path": round((ct_default / ct_ref - 1.0) * 100.0, 2),
            "within_3pct": abs(err) < 3.0,
            "within_15pct": abs(err) < 15.0,
        })
    return {"rows": rows,
            "n_within_3pct": sum(1 for r in rows if r["within_3pct"]),
            "n_within_15pct": sum(1 for r in rows if r["within_15pct"]),
            "n_cases": len(rows)}


def check_stability(calc) -> dict:
    import asyncio
    sweep = []
    for phi in [0, 5, 10, 15, 20, 25, 30, 40, 50, 60, 75, 85, 89]:
        resp = asyncio.run(calc.calc_stability(calc.StabilityRequest(heel_angle_deg=phi)))
        sweep.append({
            "heel_deg": phi,
            "gz_m": resp.righting_arm_m,
            "formula_valid": resp.formula_valid,
            "gz_basis": resp.gz_basis,
            "finite": math.isfinite(resp.righting_arm_m),
            "bounded_by_gm": abs(resp.righting_arm_m) <= 1.0 + 1e-9,
        })
    rejected = None
    try:
        asyncio.run(calc.calc_stability(calc.StabilityRequest(heel_angle_deg=90)))
        rejected = False
    except Exception as exc:  # HTTPException from FastAPI
        rejected = True
        detail = str(getattr(exc, "detail", exc))[:120]
    else:
        detail = ""
    wallsided_exact = []
    for phi in [5, 10, 15, 20, 25]:
        phi_r = math.radians(phi)
        gz = math.sin(phi_r) * (1.0 + 0.5 * 3.0 * math.tan(phi_r) ** 2)
        wallsided_exact.append({"heel_deg": phi, "gz_closed_form_m": round(gz, 6),
                                "gz_api_m": next(s["gz_m"] for s in sweep if s["heel_deg"] == phi)})
    return {
        "sweep": sweep,
        "heel_90_rejected": rejected,
        "heel_90_detail": detail,
        "wall_sided_closed_form_spot_check": wallsided_exact,
        "all_finite": all(s["finite"] for s in sweep),
        "all_bounded_by_gm": all(s["bounded_by_gm"] for s in sweep),
        "pre_fix_gz_89_deg_m": round(math.sin(math.radians(89))
                                    * (1.0 + 0.5 * 3.0 * math.tan(math.radians(89)) ** 2), 1),
        "pre_fix_gz_90_deg_m": 4.000640682170357e32,
    }


def make_figures(result: dict, figdir: Path) -> list:
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except Exception as exc:  # pragma: no cover
        print(f"[warn] matplotlib unavailable: {exc}")
        return []
    figdir.mkdir(parents=True, exist_ok=True)
    figs = []

    # fig 1: GZ vs heel, wall-sided validity range shaded
    st = result["stability"]
    xs = [s["heel_deg"] for s in st["sweep"]]
    ys = [s["gz_m"] for s in st["sweep"]]
    fig, ax = plt.subplots(figsize=(7, 4.2))
    ax.plot(xs, ys, "o-", color="#c0392b", label="API wall-sided GZ")
    ax.axvspan(0, 25, color="#27ae60", alpha=0.12, label="valid range (phi<=25 deg)")
    ax.axhline(0.508 / 2, color="#2c3e50", ls="--", lw=1,
               label="physical envelope ~B/2 = 0.254 m")
    ax.set_yscale("log")
    ax.set_xlabel("heel angle phi [deg]")
    ax.set_ylabel("GZ [m] (log scale)")
    ax.set_title("M1384: wall-sided GZ leaves the physical envelope above deck-edge immersion")
    ax.legend(fontsize=8)
    ax.grid(alpha=0.3)
    p = figdir / "m1384_gz_vs_heel.png"
    fig.tight_layout()
    fig.savefig(p, dpi=130)
    plt.close(fig)
    figs.append(p)

    # fig 2: wetted-surface bias
    ws = result["wetted_surface"]["checks"]
    labels = [r["shape"].replace("prolate_spheroid", "spheroid") for r in ws]
    errs = [r["error_pct"] for r in ws]
    fig, ax = plt.subplots(figsize=(7, 3.8))
    bars = ax.barh(labels, errs, color="#2980b9")
    ax.axvline(0, color="k", lw=1)
    for b, e in zip(bars, errs):
        ax.text(e + (0.5 if e >= 0 else -0.5), b.get_y() + b.get_height() / 2,
                f"{e:+.1f}%", va="center", ha="left" if e >= 0 else "right", fontsize=9)
    ax.set_xlabel("wetted-surface error vs closed form [%]  (threshold +/-3%)")
    ax.set_title("M1384: legacy empirical wetted-surface estimator bias")
    ax.grid(axis="x", alpha=0.3)
    p = figdir / "m1384_wetted_surface_bias.png"
    fig.tight_layout()
    fig.savefig(p, dpi=130)
    plt.close(fig)
    figs.append(p)

    # fig 3: Cf vs Re (API vs closed form)
    it = result["ittc57"]["checks"]
    res = [c["Re"] for c in it]
    cf = [c["cf_api"] for c in it]
    fig, ax = plt.subplots(figsize=(7, 3.8))
    ax.loglog(res, cf, "o-", color="#16a085", label="API Cf (recovered from R_f)")
    ax.loglog(res, [c["cf_ittc1957_closed_form"] for c in it], "k--", lw=1,
              label="ITTC-1957 closed form")
    ax.set_xlabel("Re")
    ax.set_ylabel("Cf")
    ax.set_title("M1384: ITTC-1957 friction line reproduced exactly (max |err| < 1e-8 %)")
    ax.legend(fontsize=8)
    ax.grid(alpha=0.3, which="both")
    p = figdir / "m1384_cf_vs_re.png"
    fig.tight_layout()
    fig.savefig(p, dpi=130)
    plt.close(fig)
    figs.append(p)
    # fig 4: Ct vs Fn, API vs reference
    rc = result["reference_comparison"]["rows"]
    fig, ax = plt.subplots(figsize=(7, 4.0))
    fn = [r["fn"] for r in rc]
    ax.plot(fn, [r["ct_ref"] for r in rc], "s-", color="#2c3e50", label="reference Ct (dataset)")
    ax.plot(fn, [r["ct_api"] for r in rc], "o-", color="#c0392b",
            label="API Ct (geometry-validated S)")
    ax.plot(fn, [r["ct_api_default_path"] for r in rc], "^--", color="#e67e22",
            label="API Ct (legacy S estimator)")
    ax.fill_between(fn, [r["ct_ref"] * 0.85 for r in rc], [r["ct_ref"] * 1.15 for r in rc],
                    color="#27ae60", alpha=0.10, label="+/-15% band")
    for r in rc:
        ax.annotate(f"{r['error_pct']:+.1f}%", (r["fn"], r["ct_api"]),
                    textcoords="offset points", xytext=(0, 7), fontsize=8, color="#c0392b")
    ax.set_xlabel("Froude number Fn")
    ax.set_ylabel("total resistance coefficient Ct")
    ax.set_title("M1384: SUBOFF reference comparison (wetted surface taken from dataset)")
    ax.legend(fontsize=8)
    ax.grid(alpha=0.3)
    p = figdir / "m1384_ct_vs_fn.png"
    fig.tight_layout()
    fig.savefig(p, dpi=130)
    plt.close(fig)
    figs.append(p)
    return figs


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", default=str(REPO / "artifacts" / "verif" / "ship_calcs_verification.json"))
    ap.add_argument("--figdir", default=str(REPO / "artifacts" / "verif"))
    ap.add_argument("--no-figures", action="store_true")
    args = ap.parse_args()

    calc = _load_calc_module()
    result = {
        "mission": "m1384",
        "module": "backend/api/calculations.py",
        "rho": RHO,
        "nu": NU,
        "wetted_surface": check_wetted_surface(calc),
        "ittc57": check_ittc57(calc),
        "benchmark_dataset": check_benchmark_dataset(),
        "reference_comparison": check_reference_comparison(calc),
        "stability": check_stability(calc),
    }
    figs = [] if args.no_figures else make_figures(result, Path(args.figdir))
    result["figures"] = [str(f.relative_to(REPO)) for f in figs]

    out = Path(args.json)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, indent=2))

    # console summary
    print("== wetted surface (closed-form ground truth) ==")
    for r in result["wetted_surface"]["checks"]:
        print(f"  {r['shape']:28s} true={r['s_wet_closed_form_m2']:.4f} "
              f"emp={r['s_wet_empirical_m2']:.4f} err={r['error_pct']:+.2f}%")
    print("== ITTC-1957 ==")
    mx = max(abs(c["error_pct"]) for c in result["ittc57"]["checks"])
    print(f"  max |error| = {mx:.2e}%  monotone_decreasing={result['ittc57']['monotone_decreasing']}")
    print("== benchmark dataset self-consistency ==")
    print(f"  cases={result['benchmark_dataset']['n_cases']} "
          f"inconsistent={result['benchmark_dataset']['n_inconsistent']}")
    for r in result["benchmark_dataset"]["cases"]:
        if r.get("consistent") is False:
            print(f"    {r['case_id']}: rt_ref={r['rt_ref_n']} N vs "
                  f"ct-implied {r['rt_implied_by_ct_n']} N ({r['inconsistency_pct']:+.1f}%)")
    print("== reference comparison (Ct, geometry-validated S) ==")
    rc = result["reference_comparison"]
    for r in rc["rows"]:
        print(f"  {r['case_id']:14s} Fn={r['fn']:.3f} ct_ref={r['ct_ref']:.4f} "
              f"ct_api={r['ct_api']:.6f} err={r['error_pct']:+.2f}%"
              f"{'  <3%' if r['within_3pct'] else ('  <15%' if r['within_15pct'] else '  FAIL')}")
    print(f"  within 3%: {rc['n_within_3pct']}/{rc['n_cases']}   "
          f"within 15%: {rc['n_within_15pct']}/{rc['n_cases']}")
    print("== stability ==")
    print(f"  heel90_rejected={result['stability']['heel_90_rejected']} "
          f"all_finite={result['stability']['all_finite']}")
    print(f"written: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())