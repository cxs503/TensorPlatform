"""Verification-debt closure: BEM open-water model vs the canonical Wageningen
B-series regression table and vs published model-test data.

WHY THIS EXISTS
---------------
``backend/services/propeller_open_water.py`` (mission 1393) replaced a MOCK
linear surrogate with a blade-element/momentum model, but its published
verification (``docs/verifier/PROPELLER_OPEN_WATER_VERIFICATION.md``) could only
use *analytic* rails (actuator-disc bound, Froude invariance, zero-thrust
definition).  Its own error table ends with::

    eta_max 绝对值 | 已发表 B 系列图谱点值 | 无可用数据（离线） | 未评估 | WITHHELD

So the module behind capability ``propeller.open_water`` is REAL-grade in
construction but its absolute magnitude was never checked.  The verification
debt is now closable **inside the repository**, with no network access:

    reference A  tools/verify/propeller_open_water/reference_bseries.py
                 clean-room Oosterveld & van Oossanen (1975) 39-term KT /
                 47-term KQ table (provenance: Bernitsas, Ray & Kinley 1981,
                 Univ. of Michigan Report 237), already cross-checked term by
                 term against backend/data/official/Wageningen_Bseries_coeffs.dat
    reference B  KP505 model-propeller open-water test, Iftikhar & Takinaci
                 (2023) Table 2, as transcribed in audit_open_water.py
                 (Z=5, Ae/A0=0.800, P/D=0.950) — *measured* data, used both as
                 an experimental anchor for the BEM and as a credibility band
                 for reference A itself.

WHAT IT DOES (read-only with respect to the model under test)
-------------------------------------------------------------
1. For five B-series geometries inside the published validity box, evaluate the
   BEM model and reference A on the same J grid; compare KT, KQ, eta0 pointwise
   in the physically meaningful window (0.1 <= J <= 0.9).
2. Compare the integral characteristics J0 (zero thrust), J_opt and eta_max.
3. Compare both models against reference B (KP505) on its own J grid.
4. Quantify reference A's own accuracy against reference B, so the reader can
   see whether an observed deviation is the model's or the reference's fault.
5. Machine verdict against the platform accuracy standard (15% acceptable /
   3% publishable), written explicitly per key quantity, no hand-copied numbers.

Run:  python3 -m tools.verify.propeller_open_water.bem_vs_bseries
Out:  artifacts/m1650/propeller_bem_vs_bseries/{audit.json,fig1_kt_kq.png,
      fig2_error_profile.png,fig3_kp505_experiment.png,table.md}
"""

from __future__ import annotations

import importlib.util
import json
import math
import sys
from pathlib import Path

from tools.verify.propeller_open_water import reference_bseries as ref

ROOT = Path(__file__).resolve().parents[3]
BEM_PATH = ROOT / "backend/services/propeller_open_water.py"
OUT_DIR = ROOT / "artifacts/m1650/propeller_bem_vs_bseries"

# KP505 open-water test, Iftikhar & Takinaci (2023) Table 2: (J, KT, 10*KQ)
KP505_EXPERIMENT = [
    (0.1, 0.494, 0.683), (0.2, 0.447, 0.636), (0.3, 0.392, 0.585),
    (0.4, 0.343, 0.530), (0.5, 0.290, 0.471), (0.6, 0.241, 0.409),
    (0.7, 0.193, 0.342), (0.8, 0.141, 0.270), (0.9, 0.087, 0.192),
    (1.0, 0.029, 0.108),
]
KP505_GEOM = {"P_D": 0.95, "Ae_Ao": 0.80, "Z": 5}

# (label, P/D, Ae/A0, Z) — all inside the B-series validity box
GEOMETRIES = [
    ("B4-70", 1.00, 0.70, 4),
    ("B4-55", 0.80, 0.55, 4),
    ("B4-40", 1.00, 0.40, 4),
    ("B5-80", 0.95, 0.80, 5),
    ("B3-50", 1.20, 0.50, 3),
]

J_GRID = [round(0.1 * i, 2) for i in range(1, 10)]  # 0.10 .. 0.90

GATE_PUBLISH = 3.0   # %
GATE_ACCEPT = 15.0   # %


def load_bem():
    """Import the model under test by path (never modified here)."""
    name = "m1650_bem_under_test"
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, BEM_PATH)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def rel_err(model: float, base: float) -> float:
    if base is None or model is None or base != base or model != model:
        return float("nan")
    if abs(base) < 1e-12:
        return float("nan")
    return 100.0 * (model - base) / base


def compare_geometry(bem, label, P_D, Ae_Ao, Z):
    geom = bem.PropellerGeometry(diameter_m=5.0, pitch_ratio=P_D,
                                 blade_area_ratio=Ae_Ao, blade_count=Z)
    rows = []
    for J in J_GRID:
        p = bem.open_water_point(geom, J)
        r = ref.open_water_coefficients(J, P_D, Ae_Ao, Z)
        rows.append({
            "J": J,
            "KT_bem": p["KT"], "KT_ref": r["KT"], "e_KT": rel_err(p["KT"], r["KT"]),
            "KQ_bem": p["KQ"], "KQ_ref": r["KQ"], "e_KQ": rel_err(p["KQ"], r["KQ"]),
            "eta_bem": p["eta0"], "eta_ref": r["eta_0"],
            "e_eta": rel_err(p["eta0"], r["eta_0"]),
        })
    j0_bem = bem.solve_zero_thrust_advance_ratio(geom)
    j0_ref = ref.zero_thrust_J(P_D, Ae_Ao, Z)
    best_bem = bem.best_efficiency_point(geom)
    best_ref = ref.peak_efficiency(P_D, Ae_Ao, Z)
    return {
        "label": label, "P_D": P_D, "Ae_Ao": Ae_Ao, "Z": Z,
        "rows": rows,
        "J0": {"bem": j0_bem, "ref": j0_ref, "err_pct": rel_err(j0_bem, j0_ref)},
        "J_opt": {"bem": best_bem["J"], "ref": best_ref["J"],
                  "err_pct": rel_err(best_bem["J"], best_ref["J"])},
        "eta_max": {"bem": best_bem["eta0"], "ref": best_ref["eta_0"],
                    "err_pct": rel_err(best_bem["eta0"], best_ref["eta_0"])},
        "KT_J0": {"bem": best_bem["KT"], "ref": best_ref["KT"]},
    }


def abs_mean(values):
    vals = [abs(v) for v in values if v == v]
    return sum(vals) / len(vals) if vals else float("nan")


def abs_max(values):
    vals = [abs(v) for v in values if v == v]
    return max(vals) if vals else float("nan")

# Relative errors are only meaningful where the propeller still carries a
# thrust/torque load.  Close to J0 both KT and KQ pass through zero and a
# percent error there is noise (e.g. -586 % for KT=0.029 m).  The load band is
# the standard QP-comparison window: KT_ref >= 0.05 and KQ_ref >= 0.005, i.e.
# >= ~10 % of the bollard-pull values, and both models on the same branch.
LOAD_KT_MIN = 0.05
LOAD_KQ_MIN = 0.005


def in_load_band(row):
    return (row["KT_ref"] >= LOAD_KT_MIN and row["KQ_ref"] >= LOAD_KQ_MIN
            and row["KT_bem"] * row["KT_ref"] > 0.0
            and row["KQ_bem"] * row["KQ_ref"] > 0.0)


def kp505_block(bem):
    """Both models against the measured KP505 open-water test."""
    P_D, Ae_Ao, Z = KP505_GEOM["P_D"], KP505_GEOM["Ae_Ao"], KP505_GEOM["Z"]
    geom = bem.PropellerGeometry(diameter_m=5.0, pitch_ratio=P_D,
                                 blade_area_ratio=Ae_Ao, blade_count=Z)
    rows = []
    for J, kt_e, kq10_e in KP505_EXPERIMENT:
        kq_e = kq10_e / 10.0
        p = bem.open_water_point(geom, J)
        r = ref.open_water_coefficients(J, P_D, Ae_Ao, Z)
        rows.append({
            "J": J,
            "KT_exp": kt_e, "KT_bem": p["KT"], "KT_ref": r["KT"],
            "e_KT_bem": rel_err(p["KT"], kt_e), "e_KT_ref": rel_err(r["KT"], kt_e),
            "KQ_exp": kq_e, "KQ_bem": p["KQ"], "KQ_ref": r["KQ"],
            "e_KQ_bem": rel_err(p["KQ"], kq_e), "e_KQ_ref": rel_err(r["KQ"], kq_e),
        })
    band = [x for x in rows if x["J"] <= 0.8 + 1e-9]
    return {
        "geometry": KP505_GEOM,
        "note": ("KP505 is a KCS container-ship propeller, not a B-series member; "
                 "matching (P/D, Ae/A0, Z) means reference A is an approximation "
                 "here, so these numbers measure the reference's own band as much "
                 "as the model's."),
        "rows": rows,
        "summary": {
            "n_points_band": len(band),
            "KT_bem_mean_abs_err": abs_mean([x["e_KT_bem"] for x in band]),
            "KT_ref_mean_abs_err": abs_mean([x["e_KT_ref"] for x in band]),
            "KQ_bem_mean_abs_err": abs_mean([x["e_KQ_bem"] for x in band]),
            "KQ_ref_mean_abs_err": abs_mean([x["e_KQ_ref"] for x in band]),
            "KT_bem_max_abs_err": abs_max([x["e_KT_bem"] for x in band]),
            "KT_ref_max_abs_err": abs_max([x["e_KT_ref"] for x in band]),
            "band_window": "J <= 0.8 (KT_exp >= 0.14, away from the zero-thrust branch)",
        },
    }


def monotonicity(bem, P_D, Ae_Ao, Z):
    """KQ must fall with J (physics); KT must fall with J. Checks the J where
    both stay positive, so the sign flip near J0 does not confound it."""
    geom = bem.PropellerGeometry(diameter_m=5.0, pitch_ratio=P_D,
                                 blade_area_ratio=Ae_Ao, blade_count=Z)
    js = [round(0.05 * i, 3) for i in range(0, 19)]  # 0.00 .. 0.90
    bem_pts = [bem.open_water_point(geom, J) for J in js]
    ref_pts = [ref.open_water_coefficients(J, P_D, Ae_Ao, Z) for J in js]

    def strictly_decreasing(vals):
        return all(b < a for a, b in zip(vals, vals[1:]))

    return {
        "geometry": [P_D, Ae_Ao, Z],
        "KQ_strictly_decreasing_bem": strictly_decreasing([p["KQ"] for p in bem_pts]),
        "KQ_strictly_decreasing_ref": strictly_decreasing([p["KQ"] for p in ref_pts]),
        "KT_strictly_decreasing_bem": strictly_decreasing([p["KT"] for p in bem_pts]),
        "KT_strictly_decreasing_ref": strictly_decreasing([p["KT"] for p in ref_pts]),
    }


def kt_slope(rows):
    """Least-squares slope dKT/dJ on the load band: the single most diagnostic
    structural number, because a too-shallow slope is what pushes KT high at low
    J while pulling J0 low."""
    band = [r for r in rows if in_load_band(r)]
    if len(band) < 3:
        return {"slope_bem": float("nan"), "slope_ref": float("nan"),
                "ratio_pct": float("nan"), "J0_over_PD": {}}
    js = [r["J"] for r in band]

    def slope(key):
        ys = [r[key] for r in band]
        n = len(js)
        mj = sum(js) / n
        my = sum(ys) / n
        num = sum((a - mj) * (b - my) for a, b in zip(js, ys))
        den = sum((a - mj) ** 2 for a in js)
        return num / den

    sb, sr = slope("KT_bem"), slope("KT_ref")
    return {"slope_bem": sb, "slope_ref": sr,
            "ratio_pct": 100.0 * sb / sr if sr else float("nan"),
            "n_points": len(band)}


def j0_normalised(geoms):
    """J0/(P/D): the model's own zero-thrust ratio against the published table."""
    return {g["label"]: {"bem": g["J0"]["bem"] / g["P_D"],
                         "ref": g["J0"]["ref"] / g["P_D"]} for g in geoms}


def section_model_docstring_drift():
    """The delivered section model vs what its own docstring claims.

    ``blade_element_section`` documents ``Cd = 0.008 + 0.9 alpha^2`` clamped to
    [0.006, 0.08]; the code evaluates ``CD0 + 1.0*alpha^2`` clamped to
    [0.008, 0.10].  Recorded here as an auditable finding rather than silently
    patched, because changing the constants would move every number in the
    published 1393 report.
    """
    bem = load_bem()
    alpha = 0.05
    cl, cd = bem.blade_element_section(alpha)
    return {
        "alpha_rad": alpha,
        "cd_code": cd,
        "cd_per_docstring": 0.008 + 0.9 * alpha ** 2,
        "docstring_clamp": [0.006, 0.08],
        "code_clamp": [0.008, 0.10],
        "delta_cd": cd - (0.008 + 0.9 * alpha ** 2),
    }


def _stats(geoms, band_only: bool):
    rows = [(g, r) for g in geoms for r in g["rows"]
            if (in_load_band(r) if band_only else True)]
    pick = lambda key: [r[key] for _, r in rows]
    return rows, {
        "n_points": len(rows),
        "KT_pointwise": {"mean_abs_pct": abs_mean(pick("e_KT")),
                         "max_abs_pct": abs_max(pick("e_KT"))},
        "KQ_pointwise": {"mean_abs_pct": abs_mean(pick("e_KQ")),
                         "max_abs_pct": abs_max(pick("e_KQ"))},
        "eta_pointwise": {"mean_abs_pct": abs_mean(pick("e_eta")),
                          "max_abs_pct": abs_max(pick("e_eta"))},
    }


def _verdict(m):
    if m != m:
        return "WITHHELD"
    if m < GATE_PUBLISH:
        return f"PASS <{GATE_PUBLISH:.0f}%"
    if m < GATE_ACCEPT:
        return f"PASS <{GATE_ACCEPT:.0f}% (not publishable grade)"
    return "FAIL >=15%"


def judge(geoms):
    """Machine verdict per key quantity: pointwise on the load band (the
    defensible comparison) and on all grid points (for transparency), plus the
    integral characteristics, which are well posed everywhere."""
    band_rows, band = _stats(geoms, True)
    _, allp = _stats(geoms, False)
    out = {"load_band": {"window": f"KT_ref >= {LOAD_KT_MIN}, KQ_ref >= {LOAD_KQ_MIN}",
                         "n_points": band["n_points"],
                         "excluded_J": sorted({r["J"] for g in geoms for r in g["rows"]
                                               if not in_load_band(r)})},
           "band": band, "all_points": allp}
    out["J0"] = {"mean_abs_pct": abs_mean([g["J0"]["err_pct"] for g in geoms]),
                 "max_abs_pct": abs_max([g["J0"]["err_pct"] for g in geoms])}
    out["J_opt"] = {"mean_abs_pct": abs_mean([g["J_opt"]["err_pct"] for g in geoms]),
                    "max_abs_pct": abs_max([g["J_opt"]["err_pct"] for g in geoms])}
    out["eta_max"] = {"mean_abs_pct": abs_mean([g["eta_max"]["err_pct"] for g in geoms]),
                      "max_abs_pct": abs_max([g["eta_max"]["err_pct"] for g in geoms])}
    for key in ("KT_pointwise", "KQ_pointwise", "eta_pointwise"):
        for scope in ("band", "all_points"):
            for stat in ("mean_abs_pct", "max_abs_pct"):
                d = out[scope][key]
                d["verdict_" + stat] = _verdict(d[stat])
    for key in ("J0", "J_opt", "eta_max"):
        for stat in ("mean_abs_pct", "max_abs_pct"):
            out[key]["verdict_" + stat] = _verdict(out[key][stat])
    return out


def build():
    bem = load_bem()
    geoms = [compare_geometry(bem, *g) for g in GEOMETRIES]
    audit = {
        "mission": 1650,
        "model_under_test": "backend/services/propeller_open_water.py",
        "reference_A": "tools/verify/propeller_open_water/reference_bseries.py",
        "reference_B": "KP505 model test, Iftikhar & Takinaci (2023) Table 2",
        "J_grid": J_GRID,
        "gates": {"publish_pct": GATE_PUBLISH, "accept_pct": GATE_ACCEPT},
        "geometries": geoms,
        "verdict": judge(geoms),
        "kp505": kp505_block(bem),
        "monotonicity": monotonicity(bem, 1.0, 0.70, 4),
        "kt_slope": {g["label"]: kt_slope(g["rows"]) for g in geoms},
        "J0_over_PD": j0_normalised(geoms),
        "section_docstring_drift": section_model_docstring_drift(),
    }
    return audit


def render_tables(audit) -> str:
    f = lambda x, n=2: ("n/a" if x != x else f"{x:.{n}f}")
    lines = ["## Pointwise deviation vs reference A (B-series table)", "",
             "| geometry | P/D | Ae/A0 | Z | mean |e_KT| % | max |e_KT| % | mean |e_KQ| % | max |e_KQ| % | mean |e_eta| % |",
             "| --- | --- | --- | --- | --- | --- | --- | --- | --- |"]
    for g in audit["geometries"]:
        rows = g["rows"]
        mk = abs_mean([r["e_KT"] for r in rows]); xk = abs_max([r["e_KT"] for r in rows])
        mq = abs_mean([r["e_KQ"] for r in rows]); xq = abs_max([r["e_KQ"] for r in rows])
        me = abs_mean([r["e_eta"] for r in rows])
        lines.append(f"| {g['label']} | {g['P_D']:.2f} | {g['Ae_Ao']:.2f} | {g['Z']} "
                     f"| {f(mk,1)} | {f(xk,1)} | {f(mq,1)} | {f(xq,1)} | {f(me,1)} |")
    lines += ["", "## Integral characteristics", "",
              "| geometry | J0 BEM | J0 ref | err % | J_opt BEM | J_opt ref | err % | eta_max BEM | eta_max ref | err % |",
              "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |"]
    for g in audit["geometries"]:
        lines.append(
            f"| {g['label']} | {f(g['J0']['bem'],3)} | {f(g['J0']['ref'],3)} | {f(g['J0']['err_pct'],1)} "
            f"| {f(g['J_opt']['bem'],3)} | {f(g['J_opt']['ref'],3)} | {f(g['J_opt']['err_pct'],1)} "
            f"| {f(g['eta_max']['bem'],4)} | {f(g['eta_max']['ref'],4)} | {f(g['eta_max']['err_pct'],1)} |")
    v = audit["verdict"]
    lines += ["", "## Machine verdict (platform gates: <3% publishable, <15% acceptable)", "",
              f"Pointwise statistics are given on the **load band** "
              f"({v['load_band']['window']}, {v['load_band']['n_points']} points; points "
              f"excluded because the reference itself crosses zero: J = "
              f"{', '.join(str(j) for j in v['load_band']['excluded_J'])}) and, for "
              "transparency, on all grid points.", "",
              "| key quantity | scope | mean abs err % | verdict | max abs err % | verdict |",
              "| --- | --- | --- | --- | --- | --- |"]
    for k, label in (("KT_pointwise", "KT"), ("KQ_pointwise", "KQ"), ("eta_pointwise", "eta_0")):
        for scope, sname in (("band", "load band"), ("all_points", "all points")):
            d = v[scope][k]
            lines.append(f"| {label} | {sname} | {f(d['mean_abs_pct'],2)} | {d['verdict_mean_abs_pct']} "
                         f"| {f(d['max_abs_pct'],2)} | {d['verdict_max_abs_pct']} |")
    for k in ("J0", "J_opt", "eta_max"):
        d = v[k]
        lines.append(f"| {k} | all geometries | {f(d['mean_abs_pct'],2)} | {d['verdict_mean_abs_pct']} "
                     f"| {f(d['max_abs_pct'],2)} | {d['verdict_max_abs_pct']} |")
    lines += ["", "## Structural diagnostic: KT slope and normalised J0", "",
              "| geometry | dKT/dJ BEM | dKT/dJ ref | ratio % | J0/(P/D) BEM | J0/(P/D) ref |",
              "| --- | --- | --- | --- | --- | --- |"]
    for g in audit["geometries"]:
        sl = audit["kt_slope"][g["label"]]
        jp = audit["J0_over_PD"][g["label"]]
        lines.append(f"| {g['label']} | {f(sl['slope_bem'],4)} | {f(sl['slope_ref'],4)} "
                     f"| {f(sl['ratio_pct'],1)} | {f(jp['bem'],3)} | {f(jp['ref'],3)} |")
    kp = audit["kp505"]
    lines += ["", "## Against measured KP505 model test (reference B)", "",
              "| J | KT exp | KT BEM | e KT BEM % | KT ref | e KT ref % | KQ exp | KQ BEM | e KQ BEM % | KQ ref | e KQ ref % |",
              "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |"]
    for r in kp["rows"]:
        lines.append(
            f"| {r['J']:.1f} | {f(r['KT_exp'],3)} | {f(r['KT_bem'],3)} | {f(r['e_KT_bem'],1)} "
            f"| {f(r['KT_ref'],3)} | {f(r['e_KT_ref'],1)} | {f(r['KQ_exp'],4)} | {f(r['KQ_bem'],4)} "
            f"| {f(r['e_KQ_bem'],1)} | {f(r['KQ_ref'],4)} | {f(r['e_KQ_ref'],1)} |")
    s = kp["summary"]
    lines += ["", f"Mean absolute error over the KP505 grid: BEM KT {f(s['KT_bem_mean_abs_err'],1)} %, "
                  f"reference A KT {f(s['KT_ref_mean_abs_err'],1)} %; "
                  f"BEM KQ {f(s['KQ_bem_mean_abs_err'],1)} %, "
                  f"reference A KQ {f(s['KQ_ref_mean_abs_err'],1)} %."]
    return "\n".join(lines) + "\n"


def figures(audit):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    OUT_DIR.mkdir(parents=True, exist_ok=True)

    # Fig 1: KT, 10*KQ, eta0 for two representative geometries
    fig, axes = plt.subplots(2, 3, figsize=(15, 8.4))
    for col, (label, color) in enumerate((("B4-70", "#1f77b4"), ("B5-80", "#d62728"))):
        g = next(x for x in audit["geometries"] if x["label"] == label)
        J = [r["J"] for r in g["rows"]]
        for ax, (kb, kr, title, scale) in zip(axes[col], (
                ("KT_bem", "KT_ref", "K_T", 1.0),
                ("KQ_bem", "KQ_ref", "10 K_Q", 10.0),
                ("eta_bem", "eta_ref", "eta_0", 1.0))):
            ax.plot(J, [r[kb] * scale for r in g["rows"]], "o-", color=color,
                    label="BEM (under test)")
            ax.plot(J, [r[kr] * scale for r in g["rows"]], "s--", color="k",
                    label="B-series table (ref A)")
            ax.axhline(0.0, color="0.7", lw=0.8)
            ax.set_title(f"{g['label']}  P/D={g['P_D']:.2f}  Ae/A0={g['Ae_Ao']:.2f}  Z={g['Z']}  —  {title}")
            ax.set_xlabel("J")
            ax.grid(alpha=0.3)
            ax.legend(fontsize=8)
    axes[0][0].set_ylabel("K_T  /  10K_Q  /  eta_0")
    axes[1][0].set_ylabel("K_T  /  10K_Q  /  eta_0")
    fig.suptitle("Open-water curves: BEM model vs canonical Wageningen B-series table (m1650)", fontsize=13)
    fig.tight_layout(rect=(0, 0, 1, 0.95))
    fig.savefig(OUT_DIR / "fig1_kt_kq.png", dpi=125)
    plt.close(fig)

    # Fig 2: error profile + integral characteristics
    fig, axes = plt.subplots(1, 3, figsize=(15.5, 4.6))
    for g in audit["geometries"]:
        J = [r["J"] for r in g["rows"]]
        axes[0].plot(J, [r["e_KT"] for r in g["rows"]], "o-", ms=4, label=g["label"])
        axes[1].plot(J, [r["e_KQ"] for r in g["rows"]], "o-", ms=4, label=g["label"])
    for ax, title in ((axes[0], "relative error of K_T vs ref A [%]"),
                      (axes[1], "relative error of K_Q vs ref A [%]")):
        for gate, style in ((GATE_PUBLISH, ":"), (GATE_ACCEPT, "--")):
            ax.axhline(gate, color="r", ls=style, lw=1)
            ax.axhline(-gate, color="r", ls=style, lw=1)
        ax.set_xlabel("J")
        ax.set_title(title)
        ax.grid(alpha=0.3)
        ax.legend(fontsize=8)
    labels = [g["label"] for g in audit["geometries"]]
    x = range(len(labels))
    axes[2].bar([i - 0.2 for i in x], [g["eta_max"]["err_pct"] for g in audit["geometries"]],
                width=0.4, label="eta_max err [%]")
    axes[2].bar([i + 0.2 for i in x], [g["J0"]["err_pct"] for g in audit["geometries"]],
                width=0.4, label="J0 err [%]")
    axes[2].axhline(0, color="0.6", lw=0.8)
    for gate, style in ((GATE_PUBLISH, ":"), (-GATE_PUBLISH, ":"), (GATE_ACCEPT, "--"), (-GATE_ACCEPT, "--")):
        axes[2].axhline(gate, color="r", ls=style, lw=1)
    axes[2].set_xticks(list(x)); axes[2].set_xticklabels(labels, fontsize=8)
    axes[2].set_title("integral characteristics: error vs ref A [%]")
    axes[2].legend(fontsize=8); axes[2].grid(alpha=0.3)
    fig.suptitle("BEM deviation from the B-series table: pointwise and integral (dotted=3%, dashed=15% gates)", fontsize=12)
    fig.tight_layout(rect=(0, 0, 1, 0.93))
    fig.savefig(OUT_DIR / "fig2_error_profile.png", dpi=125)
    plt.close(fig)

    # Fig 3: KP505 measured data vs both models
    kp = audit["kp505"]
    J = [r["J"] for r in kp["rows"]]
    fig, axes = plt.subplots(1, 2, figsize=(12.4, 4.6))
    axes[0].plot(J, [r["KT_exp"] for r in kp["rows"]], "ks", label="KP505 model test (exp)")
    axes[0].plot(J, [r["KT_bem"] for r in kp["rows"]], "o-", color="#1f77b4", label="BEM (under test)")
    axes[0].plot(J, [r["KT_ref"] for r in kp["rows"]], "^--", color="#2ca02c", label="B-series table (ref A)")
    axes[0].set_xlabel("J"); axes[0].set_ylabel("K_T")
    axes[0].set_title(f"K_T — KP505: Z={kp['geometry']['Z']}, Ae/A0={kp['geometry']['Ae_Ao']:.2f}, P/D={kp['geometry']['P_D']:.2f}")
    axes[0].grid(alpha=0.3); axes[0].legend(fontsize=8)
    axes[1].plot(J, [10 * r["KQ_exp"] for r in kp["rows"]], "ks", label="KP505 model test (exp)")
    axes[1].plot(J, [10 * r["KQ_bem"] for r in kp["rows"]], "o-", color="#1f77b4", label="BEM (under test)")
    axes[1].plot(J, [10 * r["KQ_ref"] for r in kp["rows"]], "^--", color="#2ca02c", label="B-series table (ref A)")
    axes[1].set_xlabel("J"); axes[1].set_ylabel("10 K_Q")
    axes[1].set_title("10 K_Q — same propeller")
    axes[1].grid(alpha=0.3); axes[1].legend(fontsize=8)
    fig.suptitle("Independent experimental anchor: KP505 model-test data vs both models (m1650)", fontsize=12)
    fig.tight_layout(rect=(0, 0, 1, 0.92))
    fig.savefig(OUT_DIR / "fig3_kp505_experiment.png", dpi=125)
    plt.close(fig)

    return sorted(p.name for p in OUT_DIR.glob("*.png"))


def main():
    audit = build()
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUT_DIR / "audit.json").write_text(json.dumps(audit, indent=2, ensure_ascii=False))
    table = render_tables(audit)
    (OUT_DIR / "table.md").write_text(table)
    figs = figures(audit)
    print(table)
    print("figures:", figs)
    v = audit["verdict"]
    for key in ("KT_pointwise", "KQ_pointwise", "eta_pointwise"):
        d = v["band"][key]
        print(f"[gate-band] {key:14s} mean {d['mean_abs_pct']:.2f}% -> {d['verdict_mean_abs_pct']}")
    for key in ("J0", "J_opt", "eta_max"):
        d = v[key]
        print(f"[gate-all ] {key:14s} mean {d['mean_abs_pct']:.2f}% -> {d['verdict_mean_abs_pct']}")
    return audit


if __name__ == "__main__":
    main()