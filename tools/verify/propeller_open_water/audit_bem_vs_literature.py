"""Audit harness: cross-check the first-principles blade-element/momentum model
``backend/services/propeller_open_water.py`` (mission 1393) against literature
baselines that are available offline in this repository.

Why this harness exists (verification debt)
-------------------------------------------
Mission 1393 replaced the hard-coded MOCK open-water surrogate with a
blade-element + momentum (BEM) model and certified it with **analytic bounds
only** (``0 < eta_0 < eta_ideal``, Froude scaling invariance, blade-area
conservation).  Those criteria cannot detect a *systematic* ``K_T`` bias: a model
that over-predicts thrust by 40 % still satisfies ``eta_0 < eta_ideal`` (the
bound moves with ``K_T``).  Mission 1403 audited the three *polynomial*
"Wageningen B-series" paths but did not touch the BEM model.  This harness closes
that gap with two independent literature anchors:

  * **Canonical regression** ``tools/verify/propeller_open_water/reference_bseries.py``
    — Oosterveld & van Oossanen (1975) 39-term ``K_T`` / 47-term ``K_Q`` tables,
    transcription cross-checked term-by-term against
    ``backend/data/official/Wageningen_Bseries_coeffs.dat`` (mission 1403).
  * **Experimental anchor** KP505 model-propeller open-water test, Table 2 of
    Iftikhar & Takinaci (2023) — ``Z=5, A_e/A_0=0.800, P/D=0.950``; imported
    from ``audit_open_water.py`` so the table is transcribed exactly once.

Outputs (every number written programmatically, nothing hand-copied):
    artifacts/m1697/bem_literature_audit/audit.json
    artifacts/m1697/bem_literature_audit/fig1_open_water_curves.png
    artifacts/m1697/bem_literature_audit/fig2_error_vs_J.png
    artifacts/m1697/bem_literature_audit/fig3_kp505_experiment.png
    artifacts/m1697/bem_literature_audit/fig4_error_map_advance_area.png
    artifacts/m1697/bem_literature_audit/fig5_bias_vs_solidity.png

Run:  python3 -m tools.verify.propeller_open_water.audit_bem_vs_literature
"""

from __future__ import annotations

import json
import math
import sys
from pathlib import Path
from typing import Dict, List, Sequence, Tuple

REPO = Path(__file__).resolve().parents[3]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

import matplotlib  # noqa: E402

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

from backend.services.propeller_open_water import (  # noqa: E402
    PropellerGeometry,
    open_water_point,
    solve_zero_thrust_advance_ratio,
)
from tools.verify.propeller_open_water import reference_bseries as ref  # noqa: E402
from tools.verify.propeller_open_water.audit_open_water import (  # noqa: E402
    KP505_EXPERIMENT,
)

OUT_DIR = REPO / "artifacts" / "m1697" / "bem_literature_audit"
DIAMETER = 5.0
RHO = 1025.0
J_GRID: Tuple[float, ...] = tuple(round(0.05 * k, 4) for k in range(1, 18))  # 0.05..0.85

# Wageningen B-series geometries inside the published validity box
# (Z = 2..7, P/D = 0.5..1.4, Ae/A0 = 0.2..1.05, J = 0..1.5).
CASES: List[Dict[str, object]] = [
    {"name": "B4-70", "P_D": 1.00, "Ae_Ao": 0.70, "Z": 4},
    {"name": "B4-55", "P_D": 0.80, "Ae_Ao": 0.55, "Z": 4},
    {"name": "B5-80", "P_D": 0.95, "Ae_Ao": 0.80, "Z": 5},
    {"name": "B3-50", "P_D": 1.20, "Ae_Ao": 0.50, "Z": 3},
]

# Acceptance band of the platform's accuracy standard (docs/QUALITY_BAR.md):
# a delivered quantity must agree with its reference within 15 %; 3 % is the
# "publication-grade" band.
BAND_ACCEPT = 0.15
BAND_TIGHT = 0.03
# Relative error is undefined where the reference crosses zero (both K_T and K_Q
# vanish near J0), so relative-error statistics are restricted to points where the
# reference is at least KT_FLOOR / KQ_FLOOR (a few percent of a typical light
# running point).  Absolute deviations and sign disagreements are reported for the
# excluded points instead of being silently dropped.
KT_FLOOR = 0.05
KQ_FLOOR = 0.005


def _rel(a: float, b: float) -> float:
    return (a - b) / b if abs(b) > 1e-12 else float("nan")


def _bem(case: Dict[str, object], J: float) -> Dict[str, float]:
    geom = PropellerGeometry(
        diameter_m=DIAMETER,
        pitch_ratio=float(case["P_D"]),
        blade_area_ratio=float(case["Ae_Ao"]),
        blade_count=int(case["Z"]),
    )
    return open_water_point(geom, J, rho=RHO)


def _bseries(case: Dict[str, object], J: float) -> Dict[str, float]:
    return ref.open_water_coefficients(
        J, float(case["P_D"]), float(case["Ae_Ao"]), int(case["Z"])
    )


# ---------------------------------------------------------------------------
# 1. BEM vs canonical B-series regression
# ---------------------------------------------------------------------------
def audit_vs_bseries() -> Dict[str, object]:
    per_case: List[Dict[str, object]] = []
    for case in CASES:
        rows: List[Dict[str, float]] = []
        for J in J_GRID:
            b = _bem(case, J)
            r = _bseries(case, J)
            rows.append(
                {
                    "J": J,
                    "KT_bem": b["KT"],
                    "KT_ref": r["KT"],
                    "KT_rel": _rel(b["KT"], r["KT"]),
                    "KQ_bem": b["KQ"],
                    "KQ_ref": r["KQ"],
                    "KQ_rel": _rel(b["KQ"], r["KQ"]),
                    "eta_bem": b["eta0"],
                    "eta_ref": r["eta_0"],
                    "eta_rel": _rel(b["eta0"], r["eta_0"]),
                    "eta_ideal": b["eta_ideal"],
                    "induction_converged": bool(b["induction_converged"]),
                    "dKT_abs": b["KT"] - r["KT"],
                    "dKQ_abs": b["KQ"] - r["KQ"],
                    "KT_valid": abs(r["KT"]) >= KT_FLOOR,
                    "KQ_valid": abs(r["KQ"]) >= KQ_FLOOR,
                }
            )
        # zero-thrust advance ratio: BEM bisection vs canonical B-series root
        geom = PropellerGeometry(
            diameter_m=DIAMETER,
            pitch_ratio=float(case["P_D"]),
            blade_area_ratio=float(case["Ae_Ao"]),
            blade_count=int(case["Z"]),
        )
        J0_bem = solve_zero_thrust_advance_ratio(geom, rho=RHO)
        J0_ref = ref.zero_thrust_J(float(case["P_D"]), float(case["Ae_Ao"]), int(case["Z"]))
        ktv = [r for r in rows if r["KT_valid"]]
        kqv = [r for r in rows if r["KQ_valid"]]
        per_case.append(
            {
                "name": case["name"],
                "P_D": case["P_D"],
                "Ae_Ao": case["Ae_Ao"],
                "Z": case["Z"],
                "points": rows,
                "J0_bem": J0_bem,
                "J0_ref": J0_ref,
                "J0_rel": _rel(J0_bem, J0_ref),
                "n_KT_valid": len(ktv),
                "max_abs_KT_rel": max(abs(r["KT_rel"]) for r in ktv),
                "max_abs_KQ_rel": max(abs(r["KQ_rel"]) for r in kqv),
                "max_abs_eta_rel": max(abs(r["eta_rel"]) for r in ktv),
                "mean_KT_rel": sum(r["KT_rel"] for r in ktv) / len(ktv),
                "mean_KQ_rel": sum(r["KQ_rel"] for r in kqv) / len(kqv),
                "max_abs_dKT": max(abs(r["dKT_abs"]) for r in rows),
                "sign_disagreements": sum(
                    1 for r in rows if r["KT_bem"] * r["KT_ref"] < 0.0),
                "eta_bound_violations": sum(1 for r in rows if r["eta_bem"] > r["eta_ideal"]),
            }
        )
    allrows = [r for c in per_case for r in c["points"]]
    ktv = [r for r in allrows if r["KT_valid"]]
    kqv = [r for r in allrows if r["KQ_valid"]]
    summary = {
        "n_points": len(allrows),
        "n_KT_valid": len(ktv),
        "n_KQ_valid": len(kqv),
        "KT_floor": KT_FLOOR,
        "KQ_floor": KQ_FLOOR,
        "max_abs_KT_rel": max(abs(r["KT_rel"]) for r in ktv),
        "max_abs_KQ_rel": max(abs(r["KQ_rel"]) for r in kqv),
        "max_abs_eta_rel": max(abs(r["eta_rel"]) for r in ktv),
        "mean_KT_rel": sum(r["KT_rel"] for r in ktv) / len(ktv),
        "max_abs_dKT": max(abs(r["dKT_abs"]) for r in allrows),
        "frac_KT_within_3pct": sum(1 for r in ktv if abs(r["KT_rel"]) <= BAND_TIGHT) / len(ktv),
        "frac_KT_within_15pct": sum(1 for r in ktv if abs(r["KT_rel"]) <= BAND_ACCEPT) / len(ktv),
        "frac_KQ_within_15pct": sum(1 for r in kqv if abs(r["KQ_rel"]) <= BAND_ACCEPT) / len(kqv),
        "frac_eta_within_15pct": sum(1 for r in ktv if abs(r["eta_rel"]) <= BAND_ACCEPT) / len(ktv),
        "max_abs_J0_rel": max(abs(c["J0_rel"]) for c in per_case),
        "sign_disagreements": sum(c["sign_disagreements"] for c in per_case),
        "eta_bound_violations": sum(c["eta_bound_violations"] for c in per_case),
    }
    return {"cases": per_case, "summary": summary}


# ---------------------------------------------------------------------------
# 2. BEM vs KP505 open-water experiment (independent, measured baseline)
# ---------------------------------------------------------------------------
def audit_vs_kp505() -> Dict[str, object]:
    case = {"name": "KP505", "P_D": 0.950, "Ae_Ao": 0.800, "Z": 5}
    geom = PropellerGeometry(
        diameter_m=DIAMETER,
        pitch_ratio=float(case["P_D"]),
        blade_area_ratio=float(case["Ae_Ao"]),
        blade_count=int(case["Z"]),
    )
    rows: List[Dict[str, float]] = []
    for J, kt_exp, kq_exp in KP505_EXPERIMENT:
        b = open_water_point(geom, J, rho=RHO)
        r = _bseries(case, J)
        eta_exp = J * kt_exp / (2.0 * math.pi * kq_exp) if kq_exp > 0 else float("nan")
        rows.append(
            {
                "J": J,
                "KT_exp": kt_exp,
                "KQ_exp": kq_exp,
                "eta_exp": eta_exp,
                "KT_bem": b["KT"],
                "KQ_bem": b["KQ"],
                "eta_bem": b["eta0"],
                "KT_bseries": r["KT"],
                "KQ_bseries": r["KQ"],
                "eta_bseries": r["eta_0"],
                "KT_bem_rel": _rel(b["KT"], kt_exp),
                "KQ_bem_rel": _rel(b["KQ"], kq_exp),
                "eta_bem_rel": _rel(b["eta0"], eta_exp),
                "KT_bseries_rel": _rel(r["KT"], kt_exp),
                "KQ_bseries_rel": _rel(r["KQ"], kq_exp),
                "eta_bseries_rel": _rel(r["eta_0"], eta_exp),
                "dKT_abs_bem": b["KT"] - kt_exp,
                "dKQ_abs_bem": b["KQ"] - kq_exp,
                "KT_valid": abs(kt_exp) >= KT_FLOOR,
                "KQ_valid": abs(kq_exp) >= KQ_FLOOR,
            }
        )
    ktv = [r for r in rows if r["KT_valid"]]
    kqv = [r for r in rows if r["KQ_valid"]]
    summary = {
        "n_points": len(rows),
        "n_KT_valid": len(ktv),
        "n_KQ_valid": len(kqv),
        "max_abs_KT_bem_rel": max(abs(r["KT_bem_rel"]) for r in ktv),
        "max_abs_KQ_bem_rel": max(abs(r["KQ_bem_rel"]) for r in kqv),
        "max_abs_eta_bem_rel": max(abs(r["eta_bem_rel"]) for r in ktv),
        "mean_KT_bem_rel": sum(r["KT_bem_rel"] for r in ktv) / len(ktv),
        "max_abs_dKT_bem": max(abs(r["dKT_abs_bem"]) for r in rows),
        "max_abs_KT_bseries_rel": max(abs(r["KT_bseries_rel"]) for r in ktv),
        "max_abs_KQ_bseries_rel": max(abs(r["KQ_bseries_rel"]) for r in kqv),
        "max_abs_eta_bseries_rel": max(abs(r["eta_bseries_rel"]) for r in ktv),
        "mean_KT_bseries_rel": sum(r["KT_bseries_rel"] for r in ktv) / len(ktv),
    }
    return {"case": case, "points": rows, "summary": summary}


# ---------------------------------------------------------------------------
# 3. Bias against blade-area ratio / blade count (root-cause diagnostic)
# ---------------------------------------------------------------------------
def audit_solidity_scaling() -> Dict[str, object]:
    J_DIAG = 0.40
    ae_list: Sequence[float] = (0.30, 0.40, 0.50, 0.60, 0.70, 0.80, 0.90, 1.00)
    z_list: Sequence[int] = (3, 4, 5, 6)
    by_ae: List[Dict[str, float]] = []
    for ae in ae_list:
        case = {"P_D": 1.0, "Ae_Ao": ae, "Z": 4}
        b = _bem(case, J_DIAG)
        r = _bseries(case, J_DIAG)
        by_ae.append(
            {"Ae_Ao": ae, "KT_bem": b["KT"], "KT_ref": r["KT"], "KT_rel": _rel(b["KT"], r["KT"]),
             "KQ_rel": _rel(b["KQ"], r["KQ"])}
        )
    by_z: List[Dict[str, float]] = []
    for z in z_list:
        case = {"P_D": 1.0, "Ae_Ao": 0.70, "Z": z}
        b = _bem(case, J_DIAG)
        r = _bseries(case, J_DIAG)
        by_z.append(
            {"Z": z, "KT_bem": b["KT"], "KT_ref": r["KT"], "KT_rel": _rel(b["KT"], r["KT"]),
             "KQ_rel": _rel(b["KQ"], r["KQ"])}
        )
    return {"J": J_DIAG, "by_Ae_Ao": by_ae, "by_Z": by_z}


# ---------------------------------------------------------------------------
# 4. Batch wrapper wiring (regression found and fixed in mission 1697)
# ---------------------------------------------------------------------------
MOCK_SURROGATE_MARKERS = ("0.5 - 0.3", "0.05 + 0.02", "min(0.85, max(0.3")


def audit_batch_wrapper() -> Dict[str, object]:
    """Check that the batch-scan engine serves the BEM model, not the MOCK surrogate.

    Mission 1393 claimed to have replaced ``ShipCalculationEngine.calc_propeller``,
    but in the merged tree the hard-coded linear surrogate was still in place (its
    wiring test was red on main).  Mission 1697 wired the wrapper to the BEM model;
    this check keeps the finding and the fix auditable.
    """
    import importlib.util

    path = REPO / "tools" / "batch_scan" / "batch_executor.py"
    src = path.read_text(encoding="utf-8")
    # strip comments and string literals: the removed formula is quoted in the new
    # docstring on purpose, so only executable code may be searched for MOCK markers
    import io
    import tokenize

    code_tokens = []
    for tok in tokenize.generate_tokens(io.StringIO(src).readline):
        if tok.type in (tokenize.COMMENT, tokenize.STRING):
            continue
        code_tokens.append(tok.string)
    code_only = " ".join(code_tokens)
    spec = importlib.util.spec_from_file_location("_batch_executor_audit", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module          # dataclasses need the module registered
    assert spec.loader is not None
    spec.loader.exec_module(module)
    engine = module.ShipCalculationEngine

    rows: List[Dict[str, float]] = []
    for kn in (2.0, 6.0, 10.0, 14.0):
        params = {
            "diameter_m": 5.0,
            "pitch_ratio": 1.0,
            "blade_area_ratio": 0.70,
            "blade_count": 4,
            "rpm": 120.0,
            "advance_speed_kn": kn,
        }
        out = engine.calc_propeller(params)
        n = params["rpm"] / 60.0
        J = params["advance_speed_kn"] * 0.5144 / (n * params["diameter_m"])
        direct = open_water_point(
            PropellerGeometry(diameter_m=5.0, pitch_ratio=1.0, blade_area_ratio=0.70, blade_count=4),
            J,
            rho=RHO,
            n_rps=n,
        )
        rows.append(
            {
                "advance_speed_kn": kn,
                "J": J,
                "KT_wrapper": out["thrust_coefficient"],
                "KT_direct": direct["KT"],
                "KQ_wrapper": out["torque_coefficient"],
                "KQ_direct": direct["KQ"],
                "dKT_abs": out["thrust_coefficient"] - direct["KT"],
                "dKQ_abs": out["torque_coefficient"] - direct["KQ"],
                "model": out.get("model", "MISSING"),
            }
        )
    return {
        "wrapper_path": "tools/batch_scan/batch_executor.py",
        "mock_markers_present": [m for m in MOCK_SURROGATE_MARKERS if m in code_only],
        "rows": rows,
        "max_abs_dKT_vs_service": max(abs(r["dKT_abs"]) for r in rows),
        "max_abs_dKQ_vs_service": max(abs(r["dKQ_abs"]) for r in rows),
        "model_field": sorted({r["model"] for r in rows}),
        "kq_decreasing_with_J": all(
            b["KQ_wrapper"] < a["KQ_wrapper"] for a, b in zip(rows, rows[1:])
        ),
    }


# ---------------------------------------------------------------------------
# Figures
# ---------------------------------------------------------------------------
def _fig_curves(vs_bs: Dict[str, object]) -> None:
    fig, axes = plt.subplots(4, 3, figsize=(15.5, 14.0))
    qty = (("KT", "K_T"), ("KQ", "K_Q"), ("eta", r"$\eta_0$"))
    for i, case in enumerate(vs_bs["cases"]):
        rows = case["points"]
        Js = [r["J"] for r in rows]
        for j, (key, label) in enumerate(qty):
            ax = axes[i][j]
            bem = [r[f"{key}_bem"] for r in rows]
            rf = [r[f"{key}_ref"] for r in rows]
            ax.plot(Js, rf, "o", ms=4.5, mfc="none", color="#1f77b4", label="B-series regression (ref)")
            ax.plot(Js, bem, "-", lw=1.8, color="#d62728", label="BEM (model 1393)")
            if key == "eta":
                ax.plot(Js, [r["eta_ideal"] for r in rows], ":", lw=1.2, color="grey",
                        label=r"actuator-disc bound $\eta_{ideal}$")
            ax.set_title(f"{case['name']}  P/D={case['P_D']}, Ae/A0={case['Ae_Ao']}, Z={case['Z']}  — {label}",
                         fontsize=9)
            ax.set_xlabel("J = V_a/(nD)")
            ax.set_ylabel(label)
            ax.grid(alpha=0.3)
            if i == 0 and j == 0:
                ax.legend(fontsize=7, loc="best")
    fig.suptitle("Open-water performance: first-principles BEM vs canonical Wageningen B-series", fontsize=12)
    fig.tight_layout(rect=(0, 0, 1, 0.975))
    fig.savefig(OUT_DIR / "fig1_open_water_curves.png", dpi=130)
    plt.close(fig)


def _fig_error_vs_J(vs_bs: Dict[str, object]) -> None:
    fig, axes = plt.subplots(3, 1, figsize=(9.5, 10.5), sharex=True)
    for case in vs_bs["cases"]:
        rows = case["points"]
        Js = [r["J"] for r in rows]
        for ax, (key, label) in zip(
            axes, (("KT", r"$K_T$"), ("KQ", r"$K_Q$"), ("eta", r"$\eta_0$"))
        ):
            vals = [100 * r[f"{key}_rel"] if r["KT_valid"] else float("nan") for r in rows]
            ax.plot(Js, vals, "-o", ms=3.5, lw=1.5, label=f"{case['name']}")
    for ax, label in zip(axes, (r"$K_T$ relative error / %", r"$K_Q$ relative error / %",
                                r"$\eta_0$ relative error / %")):
        ax.axhspan(-3, 3, color="green", alpha=0.12, label="±3 % band")
        ax.axhspan(-15, 15, color="orange", alpha=0.10, label="±15 % acceptance")
        ax.axhline(0, color="k", lw=0.6)
        ax.set_ylabel(label)
        ax.grid(alpha=0.3)
    axes[0].legend(fontsize=8, ncol=2)
    axes[-1].set_xlabel("J = V_a/(nD)")
    fig.suptitle("BEM deviation from the canonical B-series regression (positive = BEM over-predicts)", fontsize=11)
    fig.tight_layout(rect=(0, 0, 1, 0.965))
    fig.savefig(OUT_DIR / "fig2_error_vs_J.png", dpi=130)
    plt.close(fig)


def _fig_kp505(kp: Dict[str, object]) -> None:
    rows = kp["points"]
    Js = [r["J"] for r in rows]
    fig, axes = plt.subplots(1, 3, figsize=(14.5, 4.4))
    spec = (("KT", r"$K_T$"), ("KQ", r"$K_Q$"), ("eta", r"$\eta_0$"))
    for ax, (key, label) in zip(axes, spec):
        ax.plot(Js, [r[f"{key}_exp"] for r in rows], "ks", ms=6, label="KP505 experiment (Iftikhar 2023)")
        ax.plot(Js, [r[f"{key}_bseries"] for r in rows], "-", lw=1.3, color="#1f77b4",
                label="B-series regression")
        ax.plot(Js, [r[f"{key}_bem"] for r in rows], "--", lw=1.8, color="#d62728",
                label="BEM (model 1393)")
        ax.set_xlabel("J")
        ax.set_ylabel(label)
        ax.grid(alpha=0.3)
        ax.set_title(label)
    axes[0].legend(fontsize=7.5)
    fig.suptitle("KP505 measured open-water test (Z=5, Ae/A0=0.800, P/D=0.950) vs model and regression", fontsize=11)
    fig.tight_layout(rect=(0, 0, 1, 0.93))
    fig.savefig(OUT_DIR / "fig3_kp505_experiment.png", dpi=130)
    plt.close(fig)


def _fig_error_map() -> Dict[str, object]:
    Js = [round(0.05 * k, 4) for k in range(1, 19)]
    aes = [round(0.30 + 0.10 * k, 3) for k in range(8)]
    grid = []
    masked = []
    for ae in aes:
        row = []
        for J in Js:
            case = {"P_D": 1.0, "Ae_Ao": ae, "Z": 4}
            kt_ref = _bseries(case, J)["KT"]
            if abs(kt_ref) < KT_FLOOR:          # relative error undefined near J0
                row.append(float("nan"))
                masked.append((ae, J))
            else:
                row.append(100 * _rel(_bem(case, J)["KT"], kt_ref))
        grid.append(row)
    fig, ax = plt.subplots(figsize=(10.0, 5.2))
    cs = ax.contourf(Js, aes, grid, levels=20, cmap="RdBu_r", vmin=-50, vmax=50)
    fig.colorbar(cs, ax=ax, label=r"$K_T$ relative error / %")
    ax.set_xlabel("J")
    ax.set_ylabel(r"$A_e/A_0$")
    ax.set_title(r"$K_T$ bias of the BEM model over the $(J, A_e/A_0)$ plane (P/D=1.0, Z=4); "
                 r"blank = reference $|K_T|<0.05$")
    fig.tight_layout()
    fig.savefig(OUT_DIR / "fig4_error_map_advance_area.png", dpi=130)
    plt.close(fig)
    flat = [v for row in grid for v in row if v == v]
    return {
        "J": Js,
        "Ae_Ao": aes,
        "KT_rel_pct": grid,
        "masked_points": [[ae, J] for ae, J in masked],
        "max_abs": max(abs(v) for v in flat),
        "min": min(flat),
        "max": max(flat),
    }


def _fig_solidity(sol: Dict[str, object]) -> None:
    fig, axes = plt.subplots(1, 2, figsize=(12.0, 4.4))
    by_ae = sol["by_Ae_Ao"]
    axes[0].plot([r["Ae_Ao"] for r in by_ae], [100 * r["KT_rel"] for r in by_ae], "o-", color="#d62728",
                 label=r"$\Delta K_T$")
    axes[0].plot([r["Ae_Ao"] for r in by_ae], [100 * r["KQ_rel"] for r in by_ae], "s--", color="#2ca02c",
                 label=r"$\Delta K_Q$")
    axes[0].set_xlabel(r"$A_e/A_0$")
    axes[0].set_ylabel("BEM − regression / %")
    axes[0].set_title(f"bias vs blade-area ratio (P/D=1.0, Z=4, J={sol['J']})")
    by_z = sol["by_Z"]
    axes[1].plot([r["Z"] for r in by_z], [100 * r["KT_rel"] for r in by_z], "o-", color="#d62728",
                 label=r"$\Delta K_T$")
    axes[1].plot([r["Z"] for r in by_z], [100 * r["KQ_rel"] for r in by_z], "s--", color="#2ca02c",
                 label=r"$\Delta K_Q$")
    axes[1].set_xlabel("Z (blade count)")
    axes[1].set_ylabel("BEM − regression / %")
    axes[1].set_title(f"bias vs blade count (P/D=1.0, Ae/A0=0.70, J={sol['J']})")
    for ax in axes:
        ax.axhline(0, color="k", lw=0.6)
        ax.grid(alpha=0.3)
        ax.legend(fontsize=8)
    fig.suptitle("Root-cause diagnostic: bias grows with blade-area ratio (solidity), not with blade count", fontsize=11)
    fig.tight_layout(rect=(0, 0, 1, 0.93))
    fig.savefig(OUT_DIR / "fig5_bias_vs_solidity.png", dpi=130)
    plt.close(fig)


def main() -> Dict[str, object]:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    vs_bs = audit_vs_bseries()
    kp = audit_vs_kp505()
    sol = audit_solidity_scaling()
    wire = audit_batch_wrapper()
    _fig_curves(vs_bs)
    _fig_error_vs_J(vs_bs)
    _fig_kp505(kp)
    emap = _fig_error_map()
    _fig_solidity(sol)

    payload = {
        "harness": "tools/verify/propeller_open_water/audit_bem_vs_literature.py",
        "model_under_test": "backend/services/propeller_open_water.py",
        "references": {
            "regression": "Oosterveld & van Oossanen (1975) via tools/verify/propeller_open_water/reference_bseries.py",
            "experiment": "KP505 open-water test, Iftikhar & Takinaci (2023) Table 2",
        },
        "band_accept_pct": 100 * BAND_ACCEPT,
        "band_tight_pct": 100 * BAND_TIGHT,
        "vs_bseries": vs_bs,
        "vs_kp505_experiment": kp,
        "solidity_scaling": sol,
        "batch_wrapper": wire,
        "error_map": emap,
    }
    (OUT_DIR / "audit.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return payload


if __name__ == "__main__":
    p = main()
    s, k = p["vs_bseries"]["summary"], p["vs_kp505_experiment"]["summary"]
    print("== BEM vs B-series regression ==")
    print(f"  points={s['n_points']}  max|dKT|={100*s['max_abs_KT_rel']:.1f}%  "
          f"max|dKQ|={100*s['max_abs_KQ_rel']:.1f}%  max|deta|={100*s['max_abs_eta_rel']:.1f}%")
    print(f"  KT within 3%: {100*s['frac_KT_within_3pct']:.0f}%   within 15%: {100*s['frac_KT_within_15pct']:.0f}%")
    print(f"  mean dKT={100*s['mean_KT_rel']:+.1f}%  max|dKT_abs|={s['max_abs_dKT']:.4f}")
    print(f"  max|dJ0|={100*s['max_abs_J0_rel']:.1f}%  sign disagreements={s['sign_disagreements']}"
          f"  eta-bound violations={s['eta_bound_violations']}")
    print("== BEM vs KP505 experiment ==")
    print(f"  KT: mean={100*k['mean_KT_bem_rel']:+.1f}%  max|.|={100*k['max_abs_KT_bem_rel']:.1f}%")
    print(f"  KQ:            max|.|={100*k['max_abs_KQ_bem_rel']:.1f}%  "
          f"max|dKT_abs|={k['max_abs_dKT_bem']:.4f}")
    print(f"  B-series vs experiment: mean KT={100*k['mean_KT_bseries_rel']:+.1f}%  "
          f"max|KT|={100*k['max_abs_KT_bseries_rel']:.1f}%")
    w = p["batch_wrapper"]
    print("== batch-scan wrapper wiring ==")
    print(f"  model={w['model_field']}  MOCK markers left={w['mock_markers_present']}  "
          f"KQ decreasing={w['kq_decreasing_with_J']}")
    print(f"  max|wrapper - service|: dKT={w['max_abs_dKT_vs_service']:.2e} "
          f"dKQ={w['max_abs_dKQ_vs_service']:.2e}")
    print(f"artifacts -> {OUT_DIR}")