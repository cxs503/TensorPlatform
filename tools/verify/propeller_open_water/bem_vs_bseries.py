"""Verification-debt closure for the in-repo open-water (BEM) propeller model.

Debt being closed
-----------------
Mission 1393 replaced the legacy hard-coded ``KT = max(0.05, 0.5 - 0.3 J)``
surrogate in ``backend/services/propeller_open_water.py`` with a first-principles
blade-element / momentum (BEM) model and certified it with **analytic bounds
only** (``eta_0 < eta_ideal``, Froude-scaling invariance, zero-thrust root).  Its
own docstring records the gap verbatim: *"No literature point data was reachable
offline for mission 1393, so the published verification is analytic-bound +
internal-consistency only."*

That leaves the quantitative question unanswered: does the BEM model actually
reproduce measured propeller thrust and torque within the platform's 15 %
acceptance bar?  This script answers it with two independent literature
benchmarks and writes a machine-decisionable error budget.

Reference 1 — regression (open-water charts)
    Oosterveld & van Oossanen (1975) Wageningen B-series polynomials (39-term
    ``KT``, 47-term ``KQ``), evaluated through the in-repo clean-room
    transcription :mod:`tools.verify.propeller_open_water.reference_bseries`
    (provenance and term-by-term cross-check documented there and in
    ``tests/verify/test_propeller_open_water_audit.py``).  Regression accuracy
    against the B-series model tests is ~1-2 % of point value, i.e. far inside
    the 15 % bar, so it is a legitimate reference.

Reference 2 — experiment (model test, independent of the regression)
    KP505 model propeller open water: Z = 5, Ae/A0 = 0.800, P/D = 0.950,
    Iftikhar & Takinaci (2023) Table 2, transcribed once in
    ``tools/verify/propeller_open_water/audit_open_water.py`` (m1403).

Outputs (every number programmatically injected, nothing hand-copied):
    artifacts/m1638/bem_vs_bseries/audit.json
    artifacts/m1638/bem_vs_bseries/fig1_b4_70_curves.png
    artifacts/m1638/bem_vs_bseries/fig2_error_by_geometry.png
    artifacts/m1638/bem_vs_bseries/fig3_kp505_experiment.png

Run:  python3 -m tools.verify.propeller_open_water.bem_vs_bseries
"""
from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Dict, List, Sequence, Tuple

from backend.services.propeller_open_water import PropellerGeometry, open_water_point
from tools.verify.propeller_open_water import reference_bseries as ref
from tools.verify.propeller_open_water.audit_open_water import KP505_EXPERIMENT

OUT_DIR = Path("artifacts/m1638/bem_vs_bseries")
ACCEPT_BAR_PCT = 15.0          # platform rule: >15 % error vs reference = unacceptable
WORKING_RANGE = (0.30, 0.75)   # J/J0 band used by docs/verifier/PROPELLER_OPEN_WATER_VERIFICATION.md
DESIGN_RANGE = (0.40, 0.70)    # narrower design band, where a propeller actually operates
KT_FLOOR = 0.05                # relative errors on values below this floor are not meaningful

# B-series geometries bracketing the validity box (Z=2..7, P/D=0.5..1.4,
# Ae/A0=0.2..1.05).  Same five families as the m1403 audit so the two reports
# are directly comparable.
GEOMETRIES: List[Dict[str, float]] = [
    {"name": "B4-70", "P_D": 1.00, "Ae_Ao": 0.70, "Z": 4},
    {"name": "B4-55", "P_D": 0.80, "Ae_Ao": 0.55, "Z": 4},
    {"name": "B4-40", "P_D": 1.00, "Ae_Ao": 0.40, "Z": 4},
    {"name": "B5-80", "P_D": 0.95, "Ae_Ao": 0.80, "Z": 5},
    {"name": "B3-50", "P_D": 1.20, "Ae_Ao": 0.50, "Z": 3},
]


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
def rel_err_pct(value: float, reference: float) -> float:
    if abs(reference) < 1e-12:
        return float("nan")
    return (value - reference) / abs(reference) * 100.0


def make_geometry(spec: Dict[str, float]) -> PropellerGeometry:
    return PropellerGeometry(
        diameter_m=5.0,
        pitch_ratio=float(spec["P_D"]),
        blade_area_ratio=float(spec["Ae_Ao"]),
        blade_count=int(spec["Z"]),
    )


def j_grid(j0_ref: float, n: int = 21) -> List[float]:
    """Points spaced over the physical branch, up to 0.95 J0 (KT still > 0)."""
    return [round(0.95 * j0_ref * k / (n - 1), 6) for k in range(n)]


# ---------------------------------------------------------------------------
# comparison 1: BEM vs B-series regression
# ---------------------------------------------------------------------------
def compare_geometry(spec: Dict[str, float]) -> Dict[str, object]:
    geom = make_geometry(spec)
    j0_ref = ref.zero_thrust_J(spec["P_D"], spec["Ae_Ao"], int(spec["Z"]))
    j0_bem = _bem_zero_thrust(geom)
    rows: List[Dict[str, float]] = []
    for J in j_grid(j0_ref):
        bem = open_water_point(geom, J)
        r = ref.open_water_coefficients(J, spec["P_D"], spec["Ae_Ao"], int(spec["Z"]))
        rows.append({
            "J": J,
            "J_over_J0": J / j0_ref if j0_ref > 0 else float("nan"),
            "KT_bem": bem["KT"], "KT_ref": r["KT"],
            "KQ_bem": bem["KQ"], "KQ_ref": r["KQ"],
            "eta_bem": bem["eta0"], "eta_ref": r["eta_0"],
            "e_KT_pct": rel_err_pct(bem["KT"], r["KT"]),
            "e_KQ_pct": rel_err_pct(bem["KQ"], r["KQ"]),
            "e_eta_pct": rel_err_pct(bem["eta0"], r["eta_0"])
            if (r["eta_0"] > 1e-6 and bem["eta0"] > 1e-9) else float("nan"),
            "bem_converged": bool(bem["induction_converged"]),
        })

    def band(key: str, rng: Tuple[float, float] = WORKING_RANGE) -> List[float]:
        lo, hi = rng
        out = [abs(row[key]) for row in rows
               if lo <= row["J_over_J0"] <= hi and not math.isnan(row[key])]
        return out

    def signed(key: str, rng: Tuple[float, float]) -> float:
        lo, hi = rng
        return _signed_mean([row[key] for row in rows
                             if lo <= row["J_over_J0"] <= hi and not math.isnan(row[key])])

    summary = {}
    for key in ("e_KT_pct", "e_KQ_pct", "e_eta_pct"):
        vals = band(key)
        dvals = band(key, DESIGN_RANGE)
        all_vals = [abs(row[key]) for row in rows if not math.isnan(row[key])]
        summary[key] = {
            "working_mean_abs": sum(vals) / len(vals) if vals else float("nan"),
            "working_max_abs": max(vals) if vals else float("nan"),
            "working_signed_mean": signed(key, WORKING_RANGE),
            "design_mean_abs": sum(dvals) / len(dvals) if dvals else float("nan"),
            "design_max_abs": max(dvals) if dvals else float("nan"),
            "design_signed_mean": signed(key, DESIGN_RANGE),
            "all_max_abs": max(all_vals) if all_vals else float("nan"),
            "n_working": len(vals),
            "n_design": len(dvals),
        }

    # systematic bias (BEM value / reference value) in the design band: this is a
    # *characterisation* of the model's bias, not a calibration to be applied blindly
    def bias(kb: str, kr: str) -> float:
        lo, hi = DESIGN_RANGE
        vals = [row[kb] / row[kr] for row in rows
                if lo <= row["J_over_J0"] <= hi and abs(row[kr]) > 1e-9]
        return _signed_mean(vals) if vals else float("nan")
    bias_block = {"KT_bem_over_ref": bias("KT_bem", "KT_ref"),
                  "KQ_bem_over_ref": bias("KQ_bem", "KQ_ref"),
                  "eta_bem_over_ref": bias("eta_bem", "eta_ref")}
    peak_ref = ref.peak_efficiency(spec["P_D"], spec["Ae_Ao"], int(spec["Z"]))
    peak_bem = _peak_efficiency_bem(geom, j0_ref)
    return {
        "geometry": spec,
        "J0_ref": j0_ref,
        "J0_bem": j0_bem,
        "J0_rel_err_pct": rel_err_pct(j0_bem, j0_ref),
        "peak_eta_ref": peak_ref,
        "peak_eta_bem": peak_bem,
        "peak_eta_rel_err_pct": rel_err_pct(peak_bem["eta"], peak_ref["eta_0"]),
        "rows": rows,
        "summary": summary,
        "bias_design_band": bias_block,
        "n_nonconverged_strips": sum(0 if r["bem_converged"] else 1 for r in rows),
    }


def _signed_mean(vals: Sequence[float]) -> float:
    return sum(vals) / len(vals) if vals else float("nan")


def _bem_zero_thrust(geom: PropellerGeometry, hi: float = 3.0, tol: float = 1e-4) -> float:
    lo, f_lo = 0.0, open_water_point(geom, 0.0)["KT"]
    f_hi = open_water_point(geom, hi)["KT"]
    if f_lo <= 0.0:
        return float("nan")
    for _ in range(200):
        mid = 0.5 * (lo + hi)
        f_mid = open_water_point(geom, mid)["KT"]
        if f_mid > 0.0:
            lo, f_lo = mid, f_mid
        else:
            hi = mid
        if hi - lo < tol:
            break
    return 0.5 * (lo + hi)


def _peak_efficiency_bem(geom: PropellerGeometry, j0: float) -> Dict[str, float]:
    best = {"J": float("nan"), "eta": -1.0}
    n = 60
    for k in range(n + 1):
        J = j0 * k / n
        p = open_water_point(geom, J)
        if p["KT"] <= 0.0 or p["KQ"] <= 1e-12:
            continue
        if p["eta0"] > best["eta"]:
            best = {"J": J, "eta": p["eta0"]}
    return best


# ---------------------------------------------------------------------------
# comparison 2: BEM / regression vs KP505 model-test experiment
# ---------------------------------------------------------------------------
def compare_kp505() -> Dict[str, object]:
    spec = {"name": "KP505", "P_D": 0.95, "Ae_Ao": 0.80, "Z": 5}
    geom = make_geometry(spec)
    rows = []
    for J, kt_exp, kq_exp in KP505_EXPERIMENT:
        bem = open_water_point(geom, J)
        r = ref.open_water_coefficients(J, spec["P_D"], spec["Ae_Ao"], spec["Z"])
        eta_exp = (J / (2.0 * math.pi)) * (kt_exp / kq_exp) if kq_exp > 1e-12 else float("nan")
        rows.append({
            "J": J,
            "KT_exp": kt_exp, "KQ_exp": kq_exp, "eta_exp": eta_exp,
            "KT_bem": bem["KT"], "KQ_bem": bem["KQ"], "eta_bem": bem["eta0"],
            "KT_ref": r["KT"], "KQ_ref": r["KQ"], "eta_ref": r["eta_0"],
            "e_KT_bem_pct": rel_err_pct(bem["KT"], kt_exp),
            "e_KQ_bem_pct": rel_err_pct(bem["KQ"], kq_exp),
            "e_eta_bem_pct": rel_err_pct(bem["eta0"], eta_exp),
            "e_KT_ref_pct": rel_err_pct(r["KT"], kt_exp),
            "e_KQ_ref_pct": rel_err_pct(r["KQ"], kq_exp),
            "e_eta_ref_pct": rel_err_pct(r["eta_0"], eta_exp),
        })

    def stats(key: str, floor_key: str | None = None) -> Dict[str, float]:
        """``floor_key`` restricts to points where the measured quantity is large
        enough for a relative error to mean anything (KT >= KT_FLOOR)."""
        sel = rows if floor_key is None else [r for r in rows if r[floor_key] >= KT_FLOOR]
        vals = [abs(r[key]) for r in sel]
        return {"mean_abs": sum(vals) / len(vals), "max_abs": max(vals), "n": len(vals)}

    return {
        "geometry": spec,
        "source": "Iftikhar & Takinaci (2023) Table 2, KP505 model propeller",
        "rows": rows,
        "stats": {k: stats(k) for k in (
            "e_KT_bem_pct", "e_KQ_bem_pct", "e_eta_bem_pct",
            "e_KT_ref_pct", "e_KQ_ref_pct", "e_eta_ref_pct")},
        "stats_above_kt_floor": {k: stats(k, "KT_exp") for k in (
            "e_KT_bem_pct", "e_KQ_bem_pct", "e_eta_bem_pct",
            "e_KT_ref_pct", "e_KQ_ref_pct", "e_eta_ref_pct")},
        "kt_floor": KT_FLOOR,
    }


# ---------------------------------------------------------------------------
# sensitivity: which modelled constant drives the residual bias?
# ---------------------------------------------------------------------------
def sensitivity_b4_70(spec: Dict[str, float] | None = None) -> Dict[str, object]:
    """Perturb the two documented closure constants of the BEM section model
    (``CD0`` profile drag, ``CL_SLOPE`` lift slope) and report how the KT bias
    responds -- a physics check on whether the BEM error is a tuning artefact or
    structural (loading distribution / lost camber)."""
    import backend.services.propeller_open_water as bem_mod

    spec = spec or GEOMETRIES[0]
    geom = make_geometry(spec)
    j0_ref = ref.zero_thrust_J(spec["P_D"], spec["Ae_Ao"], int(spec["Z"]))
    probes = [0.4 * j0_ref, 0.6 * j0_ref]
    base_cd0, base_slope = bem_mod.CD0, bem_mod.CL_SLOPE

    def run(cd0: float, slope: float) -> List[Dict[str, float]]:
        bem_mod.CD0, bem_mod.CL_SLOPE = cd0, slope
        try:
            out = []
            for J in probes:
                bem = open_water_point(geom, J)
                r = ref.open_water_coefficients(J, spec["P_D"], spec["Ae_Ao"], int(spec["Z"]))
                out.append({
                    "J": J,
                    "e_KT_pct": rel_err_pct(bem["KT"], r["KT"]),
                    "e_KQ_pct": rel_err_pct(bem["KQ"], r["KQ"]),
                    "e_eta_pct": rel_err_pct(bem["eta0"], r["eta_0"]),
                })
            return out
        finally:
            bem_mod.CD0, bem_mod.CL_SLOPE = base_cd0, base_slope

    variants = {
        "baseline (CD0=0.012, CL_slope=2pi)": run(base_cd0, base_slope),
        "CD0 x2/3 (0.008)": run(0.008, base_slope),
        "CD0 x 5/3 (0.020)": run(0.020, base_slope),
        "CL_slope -10% (1.8pi)": run(base_cd0, 0.9 * base_slope),
        "CL_slope +10% (2.2pi)": run(base_cd0, 1.1 * base_slope),
    }
    return {
        "geometry": spec,
        "constants": {"CD0": base_cd0, "CL_SLOPE": base_slope},
        "probe_J": probes,
        "variants": variants,
    }


# ---------------------------------------------------------------------------
# verdict
# ---------------------------------------------------------------------------
def decide(comparisons: List[Dict[str, object]], kp505: Dict[str, object]) -> Dict[str, object]:
    """Machine verdict against the platform 15 % bar (nothing hand-written).

    Three independent families of judgement are reported, because they disagree
    and the disagreement *is* the finding:

    * ``per_geometry``: are the absolute KT, KQ, eta_0 curves inside 15 % over the
      working band for this propeller family?
    * ``integrated``: are the *integrated* quantities (peak efficiency, zero-thrust
      advance ratio) inside 15 %?  These are what a design study actually quotes.
    * ``experiment``: the KP505 model-test anchor (restricted to points where the
      measured KT is above ``KT_FLOOR`` so that relative errors stay meaningful).
    """
    worst = {"e_KT_pct": 0.0, "e_KQ_pct": 0.0, "e_eta_pct": 0.0}
    for c in comparisons:
        for key in worst:
            worst[key] = max(worst[key], c["summary"][key]["working_max_abs"])
    failing = [k for k, v in worst.items() if v > ACCEPT_BAR_PCT]

    per_geometry = {}
    for c in comparisons:
        row = {k: c["summary"][k]["working_max_abs"] for k in worst}
        row["pass"] = all(v <= ACCEPT_BAR_PCT for v in row.values())
        row["fail_reasons"] = [k for k in worst if row[k] > ACCEPT_BAR_PCT]
        per_geometry[c["geometry"]["name"]] = row

    peak_err = max(abs(c["peak_eta_rel_err_pct"]) for c in comparisons)
    j0_err = max(abs(c["J0_rel_err_pct"]) for c in comparisons)
    integrated = {
        "peak_eta_max_abs_pct": peak_err,
        "J0_max_abs_pct": j0_err,
        "peak_eta_pass": peak_err <= ACCEPT_BAR_PCT,
        "J0_pass": j0_err <= ACCEPT_BAR_PCT,
    }
    kp_bem = {k: v["max_abs"] for k, v in kp505["stats_above_kt_floor"].items()
              if k.endswith("bem_pct")}
    kp_worst = max(kp_bem.values())
    quantitative = (not failing) and kp_worst <= ACCEPT_BAR_PCT
    return {
        "per_geometry_pass": per_geometry,
        "n_geometry_pass_all_metrics": sum(1 for r in per_geometry.values() if r["pass"]),
        "n_geometry": len(per_geometry),
        "integrated_checks": integrated,
        "accept_bar_pct": ACCEPT_BAR_PCT,
        "working_range_J_over_J0": list(WORKING_RANGE),
        "worst_working_max_abs_pct_vs_regression": worst,
        "kp505_worst_abs_pct_bem": kp_worst,
        "kp505_errors_restricted_to_KT_exp_ge": KT_FLOOR,
        "quantitative_vs_literature": quantitative,
        "failing_quantities": failing,
        "grade": "REAL (quantitative, within 15 %)" if quantitative
                 else "REAL-trend only (exceeds 15 % quantitative bar)",
        "recommendation": (
            "Use the B-series regression (reference_bseries) for numeric KT/KQ/eta "
            "queries inside its validity box; use the BEM model only for "
            "physics-consistent trends / geometries outside the B-series box "
            "(off-design pitch, blade count, ducted variants), and never quote its "
            "absolute KT, KQ or eta_0 as a REAL-grade number without a calibration "
            "factor disclosed next to it." if not quantitative else
            "BEM model may be quoted as a REAL quantitative open-water predictor "
            "within the audited geometry box."
        ),
    }


# ---------------------------------------------------------------------------
# figures
# ---------------------------------------------------------------------------
def make_figures(comparisons: List[Dict[str, object]], kp505: Dict[str, object]) -> List[Path]:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    paths: List[Path] = []
    c70 = next(c for c in comparisons if c["geometry"]["name"] == "B4-70")
    rows = c70["rows"]

    # --- fig1: B4-70 curves, BEM vs regression, +-15 % rails ---
    fig, axes = plt.subplots(1, 3, figsize=(15, 4.4))
    Js = [r["J"] for r in rows]
    panels = (("KT", "KT_bem", "KT_ref", "KT [-]"),
              ("KQ", "KQ_bem", "KQ_ref", "KQ [-]"),
              ("eta", "eta_bem", "eta_ref", r"$\eta_0$ [-]"))
    for ax, (label, kb, kr, ylab) in zip(axes, panels):
        ax.plot(Js, [r[kr] for r in rows], "k-", lw=2.0,
                label="Wageningen B-series regression (Oosterveld & van Oossanen 1975)")
        ax.plot(Js, [r[kb] for r in rows], "o--", color="tab:red", ms=4.5,
                label="in-repo BEM (blade-element + momentum)")
        ax.set_xlabel("advance coefficient  J = Va/(nD)")
        ax.set_ylabel(ylab)
        ax.grid(alpha=0.3)
        ax.set_title({"KT": "Thrust", "KQ": "Torque", "eta": "Efficiency"}[label])
    axes[2].axhspan(0, 1, color="none")
    axes[0].set_title("Thrust  KT — B4-70 (P/D=1.0, Ae/A0=0.70)")
    axes[1].set_title("Torque  KQ — B4-70")
    axes[2].set_title(r"Efficiency  $\eta_0$ — B4-70")
    axes[0].legend(fontsize=7.5, loc="upper right")
    fig.suptitle("fig1  in-repo BEM open-water model vs published B-series regression "
                 f"(B4-70; working band J/J0 = {WORKING_RANGE[0]:.2f}-{WORKING_RANGE[1]:.2f})",
                 fontsize=10)
    fig.tight_layout()
    p = OUT_DIR / "fig1_b4_70_curves.png"
    fig.savefig(p, dpi=150)
    plt.close(fig)
    paths.append(p)

    # --- fig2: error budget per geometry with the 15 % bar ---
    names = [c["geometry"]["name"] for c in comparisons]
    metrics = (("e_KT_pct", "|KT| error", "tab:blue"),
               ("e_KQ_pct", "|KQ| error", "tab:green"),
               ("e_eta_pct", r"|$\eta_0$| error", "tab:orange"))
    fig, ax = plt.subplots(figsize=(9.5, 4.6))
    width = 0.26
    xs = range(len(names))
    for i, (key, lab, color) in enumerate(metrics):
        vals = [c["summary"][key]["working_max_abs"] for c in comparisons]
        ax.bar([x + (i - 1) * width for x in xs], vals, width=width, label=lab, color=color)
        for x, v in zip(xs, vals):
            ax.text(x + (i - 1) * width, v + 0.4, f"{v:.1f}", ha="center", fontsize=7.5)
    ax.axhline(ACCEPT_BAR_PCT, color="k", ls="--", lw=1.4,
               label=f"{ACCEPT_BAR_PCT:.0f} % acceptance bar")
    ax.set_xticks(list(xs))
    ax.set_xticklabels(names)
    ax.set_ylabel("max |relative error| in working band [%]")
    ax.set_title("fig2  BEM vs B-series error budget (max over J/J0 = 0.30-0.75)")
    ax.legend(fontsize=8)
    ax.grid(axis="y", alpha=0.3)
    fig.tight_layout()
    p = OUT_DIR / "fig2_error_by_geometry.png"
    fig.savefig(p, dpi=150)
    plt.close(fig)
    paths.append(p)

    # --- fig3: KP505 model test vs BEM and vs regression ---
    rows = kp505["rows"]
    fig, axes = plt.subplots(1, 3, figsize=(15, 4.4))
    for ax, (kb, kr, ke, ylab) in zip(axes, (
            ("KT_bem", "KT_ref", "KT_exp", "KT [-]"),
            ("KQ_bem", "KQ_ref", "KQ_exp", "KQ [-]"),
            ("eta_bem", "eta_ref", "eta_exp", r"$\eta_0$ [-]"))):
        Js = [r["J"] for r in rows]
        ax.plot(Js, [r[ke] for r in rows], "ks-", ms=6, lw=1.6,
                label="KP505 model test (Iftikhar & Takinaci 2023)")
        ax.plot(Js, [r[kr] for r in rows], "^--", color="tab:green", ms=5,
                label="B-series regression (same P/D, Ae/A0, Z)")
        ax.plot(Js, [r[kb] for r in rows], "o--", color="tab:red", ms=4.5,
                label="in-repo BEM")
        ax.set_xlabel("J")
        ax.set_ylabel(ylab)
        ax.grid(alpha=0.3)
    axes[0].legend(fontsize=7.5)
    axes[0].set_title("Thrust KT")
    axes[1].set_title("Torque KQ")
    axes[2].set_title(r"Efficiency $\eta_0$")
    fig.suptitle("fig3  independent experimental anchor: KP505 open-water test "
                 "(Z=5, Ae/A0=0.800, P/D=0.950)", fontsize=10)
    fig.tight_layout()
    p = OUT_DIR / "fig3_kp505_experiment.png"
    fig.savefig(p, dpi=150)
    plt.close(fig)
    paths.append(p)
    return paths


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------
def build_audit() -> Dict[str, object]:
    comparisons = [compare_geometry(spec) for spec in GEOMETRIES]
    kp505 = compare_kp505()
    verdict = decide(comparisons, kp505)
    sens = sensitivity_b4_70()
    return {
        "task": "m1638 verification debt: in-repo BEM open-water model vs literature",
        "model": "backend/services/propeller_open_water.py",
        "references": {
            "regression": "Oosterveld & van Oossanen (1975) Wageningen B-series "
                          "via tools/verify/propeller_open_water/reference_bseries.py",
            "experiment": kp505["source"],
        },
        "geometries": GEOMETRIES,
        "comparisons": comparisons,
        "kp505": kp505,
        "sensitivity_b4_70": sens,
        "verdict": verdict,
    }


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    audit = build_audit()
    figs = make_figures(audit["comparisons"], audit["kp505"])
    audit["figures"] = [str(p) for p in figs]
    out = OUT_DIR / "audit.json"
    out.write_text(json.dumps(audit, indent=2, ensure_ascii=False))
    v = audit["verdict"]
    print(json.dumps({
        "artifact": str(out),
        "worst_working_max_abs_pct": v["worst_working_max_abs_pct_vs_regression"],
        "kp505_worst_abs_pct_bem": round(v["kp505_worst_abs_pct_bem"], 2),
        "quantitative_vs_literature": v["quantitative_vs_literature"],
        "grade": v["grade"],
        "failing": v["failing_quantities"],
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())