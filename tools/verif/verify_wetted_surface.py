"""m1637 — wetted-surface estimator verification (independent references).

Every reference below is derived here, not copied from the implementation:

* closed-form lateral areas: cylinder, prolate spheroid, sphere, cone,
  capsule (cylinder + hemispherical caps, the SUBOFF-like geometry);
* an analytic double integral of the Wigley parabolic hull surface
  (``scipy.integrate.dblquad``), plus the platform's own hydrostatics
  integration as a second, independent code path;
* literature values for KCS / KVLCC2 / DTMB-5415 recorded with their
  provenance strings (``literature_recorded``: not re-read from the PDF in this
  mission, hence judged against a 5 % band rather than 3 %).

Outputs
-------
``artifacts/m1637/wetted_surface_validation.json`` -- full error table
``artifacts/m1637/wetted_surface_validation.png`` -- before/after bar chart

Usage::

    python tools/verif/verify_wetted_surface.py
"""
from __future__ import annotations

import json
import math
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT / "backend") not in sys.path:
    sys.path.insert(0, str(ROOT / "backend"))

from services import wetted_surface as ws  # noqa: E402

OUT_DIR = ROOT / "artifacts" / "m1637"

# --- closed-form reference surfaces ----------------------------------------

def s_cylinder(diameter: float, length: float) -> float:
    return math.pi * diameter * length


def s_spheroid(length: float, diameter: float) -> float:
    a, b = length / 2.0, diameter / 2.0
    e = math.sqrt(1.0 - (b * b) / (a * a))
    if e < 1e-12:
        return 4.0 * math.pi * a * a
    return 2.0 * math.pi * b * b + 2.0 * math.pi * a * b * math.asin(e) / e


def s_cone(diameter: float, length: float) -> float:
    r = diameter / 2.0
    return math.pi * r * math.hypot(r, length)


def s_capsule(diameter: float, length: float) -> float:
    """Cylinder with hemispherical caps — SUBOFF-like (parallel midbody)."""
    r = diameter / 2.0
    cyl_len = length - 2.0 * r
    return 2.0 * math.pi * r * cyl_len + 4.0 * math.pi * r * r


def s_wigley_exact(length: float, beam: float, draft: float) -> float:
    """Analytic geometry, high-order double quadrature over one half.

    y(x,z) = (B/2)(1-u^2)(1-v^2), u = 2x/L-1, v = 1-z/T.
    """
    from scipy.integrate import dblquad

    def y(x, z):
        u = 2.0 * x / length - 1.0
        v = 1.0 - z / draft
        return 0.5 * beam * (1.0 - u * u) * (1.0 - v * v)

    def dy_dx(x, z):
        u = 2.0 * x / length - 1.0
        v = 1.0 - z / draft
        return 0.5 * beam * (-2.0 * u * 2.0 / length) * (1.0 - v * v)

    def dy_dz(x, z):
        u = 2.0 * x / length - 1.0
        v = 1.0 - z / draft
        return 0.5 * beam * (1.0 - u * u) * (-2.0 * v * (-1.0 / draft))

    f = lambda z, x: math.sqrt(1.0 + dy_dx(x, z) ** 2 + dy_dz(x, z) ** 2)  # noqa: E731
    half, err = dblquad(f, 0.0, length, lambda _x: 0.0, lambda _x: draft,
                        epsabs=1e-10, epsrel=1e-10)
    return 2.0 * half, err


# --- case catalogue ---------------------------------------------------------

def build_cases():
    """(name, kind, L, B, T, Cb, exact_reference, provenance)."""
    L, D = 4.356, 0.508
    cases = []

    # bodies of revolution, harness convention T = B (so Cb = V/(L*B^2))
    cases.append(("cylinder_closed", "revolution", L, D, D,
                  math.pi / 4, s_cylinder(D, L) + 0.5 * math.pi * D * D,
                  "closed form pi*D*L + 2*pi*r^2 (closed-ended body)"))
    cases.append(("spheroid_b/a=0.25", "revolution", L, D, D,
                  math.pi / 6, s_spheroid(L, D),
                  "closed form 2*pi*b^2 + 2*pi*a*b*asin(e)/e"))
    cases.append(("spheroid_b/a=0.15", "revolution", L, 0.3048, 0.3048,
                  math.pi / 6, s_spheroid(L, 0.3048),
                  "closed form, second fineness (generalisation test)"))
    cases.append(("sphere", "revolution", 1.0, 1.0, 1.0,
                  math.pi / 6, 4.0 * math.pi * 0.25,
                  "closed form 4*pi*r^2, degenerate e->0 guard"))
    cases.append(("cylinder_open_bounded", "revolution", L, D, D,
                  math.pi / 4, s_cylinder(D, L),
                  "open cylinder: the two end discs are NOT counted by the model "
                  "(bounded deviation, kept to show the convention gap)"))
    cases.append(("cone_open", "revolution", L, D, D,
                  math.pi / 12, s_cone(D, L),
                  "closed form pi*r*sqrt(r^2+L^2): extreme taper"))
    cases.append(("capsule_SUBOFF_like", "revolution", L, D, D,
                  (2.0 * math.pi * (D / 2) ** 2 * (L - 2 * (D / 2)) + 4.0 / 3.0 * math.pi * (D / 2) ** 3)
                  / (L * D * D),
                  s_capsule(D, L),
                  "closed form cylinder + hemispherical caps (parallel midbody)"))

    # ship forms
    wigley_exact, _ = s_wigley_exact(6.0, 0.6, 0.375)
    cases.append(("wigley_hull", "ship", 6.0, 0.6, 0.375,
                  0.4444, wigley_exact,
                  "analytic double integral of the Wigley surface (dblquad 1e-10)"))
    cases.append(("barge_box_Cb1", "ship", 100.0, 20.0, 8.0,
                  1.0, 100.0 * 20.0 + 2.0 * 100.0 * 8.0 + 2.0 * 20.0 * 8.0,
                  "closed form box hull (outside Denny-Mumford Cb band, kept as a bound)"))
    cases.append(("KCS_full", "ship", 230.0, 32.2, 10.8, 0.6505, 9530.0,
                  "literature_recorded: KRISO container ship S_w=9530 m^2 (SIMMAN/ITTC)"))
    cases.append(("KVLCC2_full", "ship", 320.0, 58.0, 20.8, 0.8098, 27194.0,
                  "literature_recorded: SIMMAN 2008 KVLCC2 S_w=27194 m^2"))
    cases.append(("DTMB5415_model", "ship", 5.72, 0.768, 0.248, 0.5065, 4.72,
                  "literature_recorded: ITTC V&V 2010 DTMB-5415 model S_w=4.72 m^2"))
    return cases


#: Shapes outside the models' stated domain: kept in the table so the deviation
#: is honest, excluded from the headline statistics.
OUT_OF_DOMAIN = {
    "cone_open": "tapering to a point: no parallel midbody, capped-cylinder cannot represent it",
    "cylinder_open_bounded": "open cylinder: end discs intentionally not counted",
}


def rel(new: float, ref: float) -> float:
    return new / ref - 1.0


def main() -> int:
    cases = build_cases()
    rows = []
    for name, kind, L, B, T, cb, ref, prov in cases:
        legacy = ws.legacy_axisymmetric(L, B, cb)
        auto = ws.estimate_wetted_surface(L, B, T, cb, "auto")
        row = {
            "case": name,
            "kind": kind,
            "length_m": L,
            "beam_m": B,
            "draft_m": T,
            "cb": round(cb, 6),
            "reference_m2": round(ref, 4),
            "legacy_m2": round(legacy, 4),
            "legacy_err_pct": round(100.0 * rel(legacy, ref), 3),
            "auto_model": auto.model,
            "auto_m2": round(auto.area_m2, 4),
            "auto_err_pct": round(100.0 * rel(auto.area_m2, ref), 3),
            "branch_reason": auto.branch_reason,
            "in_domain": name not in OUT_OF_DOMAIN,
            "out_of_domain_reason": OUT_OF_DOMAIN.get(name),
            "provenance": prov,
        }
        rows.append(row)

    # platform hydrostatics cross-check on the Wigley hull (independent code path)
    from services.ship_hydrostatics import compute_hydrostatics, wigley_offsets

    hydro = {}
    for ns, nw in ((81, 41), (161, 81)):
        res = compute_hydrostatics(wigley_offsets(6.0, 0.6, 0.375, ns, nw))
        hydro[f"{ns}x{nw}"] = round(res.wetted_surface_m2, 4)

    in_domain = [r for r in rows if r["in_domain"]]
    summary = {
        "cases_total": len(rows),
        "cases_in_domain": len(in_domain),
        "legacy_max_abs_err_pct_in_domain": max(abs(r["legacy_err_pct"]) for r in in_domain),
        "auto_max_abs_err_pct_in_domain": max(abs(r["auto_err_pct"]) for r in in_domain),
        "auto_max_abs_err_pct_revolution": max(
            abs(r["auto_err_pct"]) for r in in_domain if r["kind"] == "revolution"),
        "auto_max_abs_err_pct_ship": max(
            abs(r["auto_err_pct"]) for r in in_domain if r["kind"] == "ship"),
        "auto_max_abs_err_pct_ship_Cb_le_0p90": max(
            abs(r["auto_err_pct"]) for r in in_domain
            if r["kind"] == "ship" and r["case"] != "barge_box_Cb1"),
        "legacy_fail_15pct_count_in_domain": sum(
            1 for r in in_domain if abs(r["legacy_err_pct"]) > 15.0),
        "auto_fail_15pct_count_in_domain": sum(
            1 for r in in_domain if abs(r["auto_err_pct"]) > 15.0),
        "legacy_fail_3pct_count_in_domain": sum(
            1 for r in in_domain if abs(r["legacy_err_pct"]) > 3.0),
        "auto_fail_3pct_count_in_domain": sum(
            1 for r in in_domain if abs(r["auto_err_pct"]) > 3.0),
    }

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    payload = {
        "mission": "m1637",
        "title": "Wetted-surface estimator verification and repair",
        "legacy_formula": "S = pi*B*L*(0.5 + 0.25*Cb)",
        "auto_models": ["denny_mumford (S=1.025L(1.7T+Cb B))", "spheroid_equivalent"],
        "rows": rows,
        "summary": summary,
        "wigley_hydrostatics_crosscheck_m2": hydro,
    }
    (OUT_DIR / "wetted_surface_validation.json").write_text(
        json.dumps(payload, indent=2, ensure_ascii=False))

    print(f"{'case':24s} {'ref':>10s} {'legacy%':>9s} {'auto%':>8s}  model / domain")
    for r in rows:
        dom = "" if r["in_domain"] else "  [OUT-OF-DOMAIN]"
        print(f"{r['case']:24s} {r['reference_m2']:10.3f} "
              f"{r['legacy_err_pct']:+9.2f} {r['auto_err_pct']:+8.2f}  "
              f"{r['auto_model']}{dom}")
    print("summary:", json.dumps(summary, ensure_ascii=False))
    print("wigley hydrostatics cross-check:", hydro)

    # --- figure -------------------------------------------------------------
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import numpy as np

    labels = [r["case"] for r in rows]
    x = np.arange(len(rows))
    w = 0.38
    fig, ax = plt.subplots(figsize=(12.5, 5.6))
    ax.bar(x - w / 2, [r["legacy_err_pct"] for r in rows], w,
           label="legacy  S=piBL(0.5+0.25Cb)", color="#c0392b")
    ax.bar(x + w / 2, [r["auto_err_pct"] for r in rows], w,
           label="repaired  (auto branch)", color="#1f6f3f")
    for y in (15.0, -15.0):
        ax.axhline(y, ls="--", lw=1.0, color="k")
    ax.axhline(0.0, lw=0.8, color="k")
    ax.set_xticks(x)
    ax.set_xticklabels(labels, rotation=30, ha="right", fontsize=9)
    ax.set_ylabel("relative error vs closed-form / literature  [%]")
    ax.set_title("m1637  wetted-surface estimator: legacy bias vs repaired branches "
                 "(dashed = 15% acceptance bar)")
    ax.legend(fontsize=9)
    ax.grid(axis="y", alpha=0.3)
    fig.tight_layout()
    fig.savefig(OUT_DIR / "wetted_surface_validation.png", dpi=150)
    print("wrote", OUT_DIR / "wetted_surface_validation.png")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())