"""m1678 - absolute-magnitude verification of the blade-element/momentum (BEM)
open-water model against literature and regression anchors.

Why this exists
---------------
``backend/services/propeller_open_water.py`` replaced the legacy MOCK surrogate
and is the model behind the platform capability ``propeller.open_water``, which
``audit_capabilities`` grades **REAL**.  Its own module docstring states the
proof obligation it could not discharge when it was merged:

    "No literature point data was reachable offline for mission 1393, so the
     published verification is analytic-bound + internal-consistency only"

Every check so far was a *bound* (``0 < eta_0 < eta_ideal``) or an *identity*
(``eta_0 = J KT / (2 pi KQ)``, Froude invariance).  Bounds and identities are
necessary but cannot detect a model that is internally perfect and externally
wrong by 30 percent.  Two independent anchors already in the tree close that gap:

  A  KP505 open-water experiment (Iftikhar & Takinaci 2023, Table 2) - the
     literature anchor, carried in
     ``backend/data/propulsion_benchmark_reference.json``.
  B  Wageningen B-series 39/47-term polynomials (Oosterveld & van Oossanen
     1975) via the clean-room reference
     ``tools/verify/propeller_open_water/reference_bseries.py`` - used to sweep
     the (P/D, Ae/A0, Z) box, which no experimental map covers.

Both anchors are external to the BEM code path; neither is fitted to it.

Verdicts follow the owner standard: >15 percent error against an
experimental/reference value is unacceptable (platform gate); 3 percent is the
paper-grade bar.  The harness never upgrades a failing comparison to PASS.

Run
---
    python3 tools/verify/propeller_open_water/bem_benchmark.py            # full
    python3 tools/verify/propeller_open_water/bem_benchmark.py --quick    # small sweep

Outputs (every number is written programmatically, nothing hand-copied):
    artifacts/m1678/propeller_bem_benchmark/benchmark.json
    artifacts/m1678/propeller_bem_benchmark/fig1_kp505_curves.png
    artifacts/m1678/propeller_bem_benchmark/fig2_error_map.png
    artifacts/m1678/propeller_bem_benchmark/fig3_anchoring_decomposition.png
    artifacts/m1678/propeller_bem_benchmark/fig4_diagnosis.png
    docs/verification/propeller_bem_benchmark.md
"""

from __future__ import annotations

import json
import math
import sys
from pathlib import Path
from typing import Dict, Iterable, List, Sequence

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

REPO = Path(__file__).resolve().parents[3]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from backend.services.propeller_open_water import (  # noqa: E402
    PropellerGeometry,
    open_water_point,
    solve_zero_thrust_advance_ratio,
)
from tools.verify.propeller_open_water import reference_bseries as ref  # noqa: E402

REFERENCE_JSON = REPO / "backend" / "data" / "propulsion_benchmark_reference.json"
OUT_DIR = REPO / "artifacts" / "m1678" / "propeller_bem_benchmark"
DOC_PATH = REPO / "docs" / "verification" / "propeller_bem_benchmark.md"

GATE_PLATFORM_PCT = 15.0
GATE_PAPER_PCT = 3.0
# Headline statistics band: below J ~ 1.0 a real propeller carries useful thrust,
# and the KP505 measurement at J=1.0 sits at KT=0.029 where a 1e-3 absolute
# wobble is a 3 percent relative error.  Band and full range are both reported so
# the interval choice cannot flatter the verdict.
OPERATING_BAND = (0.1, 0.7)

SWEEP_P_D = (0.7, 1.0, 1.3)
SWEEP_AE_AO = (0.35, 0.55, 0.85)
SWEEP_Z = (3, 4, 5)
SWEEP_J = tuple(round(0.1 * i, 1) for i in range(1, 13))  # 0.1 .. 1.2


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
def _as_display_path(path: Path) -> str:
    """Repo-relative when possible (keeps the JSON reviewable), else absolute."""
    try:
        return str(path.relative_to(REPO))
    except ValueError:
        return str(path)


def load_reference() -> Dict[str, object]:
    with open(REFERENCE_JSON, encoding="utf-8") as fh:
        return json.load(fh)


def rel_err_pct(model: float, anchor: float) -> float:
    """Signed relative error in percent; NaN when the anchor is (near) zero."""
    if anchor == 0.0 or abs(anchor) < 1e-9:
        return float("nan")
    return 100.0 * (model - anchor) / anchor


def stats(values: Iterable[float]) -> Dict[str, float]:
    vals = [v for v in values if v == v]
    if not vals:
        return {"n": 0, "mean_abs": float("nan"), "max_abs": float("nan"), "rms": float("nan")}
    absv = [abs(v) for v in vals]
    return {
        "n": len(vals),
        "mean_abs": sum(absv) / len(absv),
        "max_abs": max(absv),
        "rms": math.sqrt(sum(v * v for v in vals) / len(vals)),
    }


def classify(error_pct: float) -> str:
    a = abs(error_pct)
    if a <= GATE_PAPER_PCT:
        return "PASS_3PCT"
    if a <= GATE_PLATFORM_PCT:
        return "PASS_15PCT"
    return "FAIL"


def bem_geometry(P_D: float, Ae_Ao: float, Z: int, diameter_m: float = 5.0) -> PropellerGeometry:
    return PropellerGeometry(
        diameter_m=diameter_m, pitch_ratio=P_D, blade_area_ratio=Ae_Ao, blade_count=Z
    )


# ---------------------------------------------------------------------------
# case A: KP505 experiment (literature, independent of both models)
# ---------------------------------------------------------------------------
def case_kp505(anchor: Dict[str, object]) -> Dict[str, object]:
    gm = anchor["geometry"]
    P_D, Ae_Ao, Z = float(gm["P_D"]), float(gm["Ae_Ao"]), int(gm["Z"])
    geom = bem_geometry(P_D, Ae_Ao, Z)

    rows: List[Dict[str, float]] = []
    for pt in anchor["points"]:
        J = float(pt["J"])
        bem = open_water_point(geom, J)
        poly = ref.open_water_coefficients(J, P_D, Ae_Ao, Z)
        kt_e, kq_e = float(pt["KT"]), float(pt["KQ"])
        eta_e = J * kt_e / (2.0 * math.pi * kq_e)
        rows.append(
            {
                "J": J,
                "KT_exp": kt_e,
                "KT_bem": bem["KT"],
                "KT_bseries": poly["KT"],
                "e_KT_bem": rel_err_pct(bem["KT"], kt_e),
                "e_KT_bseries": rel_err_pct(poly["KT"], kt_e),
                "KQ_exp": kq_e,
                "KQ_bem": bem["KQ"],
                "KQ_bseries": poly["KQ"],
                "e_KQ_bem": rel_err_pct(bem["KQ"], kq_e),
                "e_KQ_bseries": rel_err_pct(poly["KQ"], kq_e),
                "eta_exp": eta_e,
                "eta_bem": bem["eta0"],
                "eta_bseries": poly["eta_0"],
                "e_eta_bem": rel_err_pct(bem["eta0"], eta_e),
                "e_eta_bseries": rel_err_pct(poly["eta_0"], eta_e),
                "e_KT_bem_vs_bseries": rel_err_pct(bem["KT"], poly["KT"]),
                "e_KQ_bem_vs_bseries": rel_err_pct(bem["KQ"], poly["KQ"]),
                "eta_ideal_bem": bem["eta_ideal"],
            }
        )

    band = [r for r in rows if OPERATING_BAND[0] <= r["J"] <= OPERATING_BAND[1]]

    return {
        "geometry": {"P_D": P_D, "Ae_Ao": Ae_Ao, "Z": Z, "D_m": geom.diameter_m},
        "source": anchor["source"],
        "band_J": list(OPERATING_BAND),
        "bem_vs_experiment_band": {
            q: stats([r[f"e_{q}_bem"] for r in band]) for q in ("KT", "KQ", "eta")
        },
        "bem_vs_experiment_full": {
            q: stats([r[f"e_{q}_bem"] for r in rows]) for q in ("KT", "KQ", "eta")
        },
        "bseries_vs_experiment_band": {
            q: stats([r[f"e_{q}_bseries"] for r in band]) for q in ("KT", "KQ", "eta")
        },
        "bem_vs_bseries_band": {
            "KT": stats([r["e_KT_bem_vs_bseries"] for r in band]),
            "KQ": stats([r["e_KQ_bem_vs_bseries"] for r in band]),
        },
        "rows": rows,
    }


# ---------------------------------------------------------------------------
# case B: B-series sweep over the (P/D, Ae/A0, Z) validity box
# ---------------------------------------------------------------------------
def case_bseries_sweep(quick: bool = False) -> Dict[str, object]:
    p_ds = SWEEP_P_D if not quick else (0.8, 1.2)
    ae_aos = SWEEP_AE_AO if not quick else (0.55,)
    zs = SWEEP_Z if not quick else (4,)
    js = SWEEP_J if not quick else (0.1, 0.4, 0.7, 1.0)

    rows: List[Dict[str, float]] = []
    for P_D in p_ds:
        for Ae_Ao in ae_aos:
            for Z in zs:
                geom = bem_geometry(P_D, Ae_Ao, Z)
                for J in js:
                    bem = open_water_point(geom, J)
                    poly = ref.open_water_coefficients(J, P_D, Ae_Ao, Z)
                    # eta_0 diverges where the anchor's KQ crosses zero; only
                    # compare efficiency where the anchor itself is physical.
                    eta_ok = poly["KQ"] > 0.002 and poly["KT"] > 0.0
                    rows.append(
                        {
                            "P_D": P_D,
                            "Ae_Ao": Ae_Ao,
                            "Z": Z,
                            "J": J,
                            "KT_bem": bem["KT"],
                            "KT_ref": poly["KT"],
                            "e_KT": rel_err_pct(bem["KT"], poly["KT"]),
                            "KQ_bem": bem["KQ"],
                            "KQ_ref": poly["KQ"],
                            "e_KQ": rel_err_pct(bem["KQ"], poly["KQ"]),
                            "e_eta": (
                                rel_err_pct(bem["eta0"], poly["eta_0"]) if eta_ok else float("nan")
                            ),
                            "eta_bem": bem["eta0"],
                            "eta_ref": poly["eta_0"],
                            "eta_ideal_bem": bem["eta_ideal"],
                        }
                    )

    # Relative error is meaningless where the anchor itself vanishes (KT -> 0 at
    # the zero-thrust advance ratio): a 0.004 absolute difference on KT_ref=1e-4
    # reads as 4000 percent and says nothing about model quality.  Every
    # relative-error statistic below therefore uses the "comparable" mask
    # (anchor carries thrust and torque); the absolute KT / KQ error is reported
    # over the whole grid so nothing is hidden by the mask.
    COMPARABLE_KT = 0.05
    COMPARABLE_KQ = 0.005

    def comparable(rs: List[Dict[str, float]]) -> List[Dict[str, float]]:
        return [r for r in rs if r["KT_ref"] >= COMPARABLE_KT and abs(r["KQ_ref"]) >= COMPARABLE_KQ]

    comp = comparable(rows)

    def grouped(key: str) -> Dict[str, Dict[str, float]]:
        buckets: Dict[str, List[Dict[str, float]]] = {}
        for r in comp:
            buckets.setdefault(str(r[key]), []).append(r)
        return {k: stats([r["e_KT"] for r in v]) for k, v in sorted(buckets.items())}

    def abs_stats(key: str) -> Dict[str, float]:
        vals = [abs(r[key]) for r in rows]
        return {"n": len(vals), "mean_abs": sum(vals) / len(vals), "max_abs": max(vals),
                "rms": math.sqrt(sum(v * v for v in vals) / len(vals))}

    return {
        "grid": {"P_D": list(p_ds), "Ae_Ao": list(ae_aos), "Z": list(zs), "J": list(js)},
        "n_total": len(rows),
        "n_comparable": len(comp),
        "comparable_mask": {"KT_ref_min": COMPARABLE_KT, "KQ_ref_min": COMPARABLE_KQ},
        "all": stats([r["e_KT"] for r in comp]),
        "KQ_all": stats([r["e_KQ"] for r in comp]),
        "eta_all": stats([r["e_eta"] for r in comp]),
        "unmasked_KT": stats([r["e_KT"] for r in rows]),
        "abs_KT": abs_stats("e_KT"),
        "by_P_D": grouped("P_D"),
        "by_Ae_Ao": grouped("Ae_Ao"),
        "by_Z": grouped("Z"),
        "rows": rows,
    }


# ---------------------------------------------------------------------------
# diagnostics: where does the bias come from?  (no fitting, no model edits)
# ---------------------------------------------------------------------------
def effective_pitch_ratio(P_D: float, Ae_Ao: float, Z: int, J: float = 0.4) -> Dict[str, float]:
    """P/D the BEM model would need to reproduce the reference KT at ``J``.

    Single-parameter *bias indicator*, not a calibration: if the ratio is
    systematically below 1, the BEM chain makes more thrust per unit geometric
    pitch than the B-series family does.
    """
    target = ref.open_water_coefficients(J, P_D, Ae_Ao, Z)["KT"]

    def f(pd: float) -> float:
        return open_water_point(bem_geometry(pd, Ae_Ao, Z), J)["KT"] - target

    lo, hi = 0.20, 2.00
    f_lo, f_hi = f(lo), f(hi)
    if f_lo * f_hi > 0.0:
        return {"P_D": P_D, "Ae_Ao": Ae_Ao, "Z": Z, "J": J, "P_D_eff": float("nan"),
                "ratio": float("nan"), "KT_ref": target, "note": "no bracket"}
    for _ in range(60):
        mid = 0.5 * (lo + hi)
        f_mid = f(mid)
        if f_lo * f_mid <= 0.0:
            hi = mid
        else:
            lo, f_lo = mid, f_mid
        if hi - lo < 1e-3:
            break
    p_eff = 0.5 * (lo + hi)
    return {"P_D": P_D, "Ae_Ao": Ae_Ao, "Z": Z, "J": J, "P_D_eff": p_eff,
            "ratio": p_eff / P_D, "KT_ref": target, "note": ""}


def diagnostics(kp505: Dict[str, object]) -> Dict[str, object]:
    gm = kp505["geometry"]
    P_D, Ae_Ao, Z = gm["P_D"], gm["Ae_Ao"], gm["Z"]
    geom = bem_geometry(P_D, Ae_Ao, Z)

    j0 = {
        "J0_bem": solve_zero_thrust_advance_ratio(geom),
        "J0_bseries": ref.zero_thrust_J(P_D, Ae_Ao, Z),
        "J0_experiment": None,
        "note": "experimental J0 not reported: the KP505 table ends at KT=0.029 (J=1.0), "
                "so an experimental J0 would be an extrapolation, not a measurement.",
    }
    j0["e_J0_pct"] = rel_err_pct(j0["J0_bem"], j0["J0_bseries"])

    induction = []
    for J in (0.4, 0.7):
        with_mom = open_water_point(geom, J, axial_induction=True)
        be_only = open_water_point(geom, J, axial_induction=False)
        ref_c = ref.open_water_coefficients(J, P_D, Ae_Ao, Z)
        induction.append(
            {
                "J": J,
                "KT_momentum": with_mom["KT"],
                "KT_blade_element_only": be_only["KT"],
                "KT_ref": ref_c["KT"],
                "e_KT_momentum": rel_err_pct(with_mom["KT"], ref_c["KT"]),
                "e_KT_blade_element_only": rel_err_pct(be_only["KT"], ref_c["KT"]),
                "induction_share_pct": 100.0 * (with_mom["KT"] - be_only["KT"]) / with_mom["KT"],
            }
        )

    eff = [
        effective_pitch_ratio(0.70, 0.55, 4),
        effective_pitch_ratio(1.00, 0.55, 4),
        effective_pitch_ratio(1.30, 0.55, 4),
        effective_pitch_ratio(P_D, Ae_Ao, Z),
    ]

    return {"J0": j0, "induction": induction, "effective_pitch": eff}

# ---------------------------------------------------------------------------
# figures
# ---------------------------------------------------------------------------
def _nanmax(grid: Sequence[Sequence[float]]) -> float:
    vals = [v for row in grid for v in row if v == v]
    return max(vals) if vals else 1.0


def fig_kp505(kp505: Dict[str, object], path: Path) -> None:
    rows = kp505["rows"]
    Js = [r["J"] for r in rows]
    fig, axes = plt.subplots(1, 3, figsize=(15, 4.4))
    panels = [
        ("KT_exp", "KT_bem", "KT_bseries", "thrust coefficient $K_T$"),
        ("KQ_exp", "KQ_bem", "KQ_bseries", "torque coefficient $K_Q$"),
        ("eta_exp", "eta_bem", "eta_bseries", "open-water efficiency $\\eta_0$"),
    ]
    for ax, (ke, kb, kp, title) in zip(axes, panels):
        ax.plot(Js, [r[ke] for r in rows], "ko", ms=6, label="KP505 experiment (Iftikhar 2023)")
        ax.plot(Js, [r[kb] for r in rows], "s-", color="#c0392b", ms=5,
                label="platform BEM (m1678 audit)")
        ax.plot(Js, [r[kp] for r in rows], "^--", color="#2980b9", ms=5,
                label="Wageningen B-series reference")
        ax.set_xlabel("advance ratio $J$")
        ax.set_ylabel(title)
        ax.grid(alpha=0.3)
        ax.legend(fontsize=7.5)
    axes[2].axhline(0, color="grey", lw=0.6)
    fig.suptitle("Fig. 1  KP505 open-water curves: platform BEM vs literature experiment vs B-series",
                 fontsize=11)
    fig.tight_layout()
    fig.savefig(path, dpi=150)
    plt.close(fig)


def fig_error_map(sweep: Dict[str, object], path: Path) -> None:
    rows = sweep["rows"]
    z_sel = 4 if 4 in sweep["grid"]["Z"] else sweep["grid"]["Z"][0]
    pds = sweep["grid"]["P_D"]
    aes = sweep["grid"]["Ae_Ao"]
    fig, axes = plt.subplots(1, 2, figsize=(12.5, 4.4))

    grid = [[float("nan") for _ in aes] for _ in pds]
    for i, P_D in enumerate(pds):
        for j, Ae_Ao in enumerate(aes):
            errs = [r["e_KT"] for r in rows
                    if r["P_D"] == P_D and r["Ae_Ao"] == Ae_Ao and r["Z"] == z_sel
                    and r["J"] <= OPERATING_BAND[1]]
            if errs:
                grid[i][j] = sum(abs(e) for e in errs) / len(errs)
    im = axes[0].imshow(grid, cmap="RdYlGn_r", vmin=0, vmax=max(40.0, _nanmax(grid)))
    axes[0].set_xticks(range(len(aes)))
    axes[0].set_xticklabels([f"{a:.2f}" for a in aes])
    axes[0].set_yticks(range(len(pds)))
    axes[0].set_yticklabels([f"{p:.2f}" for p in pds])
    axes[0].set_xlabel("$A_E/A_0$")
    axes[0].set_ylabel("$P/D$")
    axes[0].set_title(
        "mean $|e(K_T)|$ [pct] vs B-series, $Z=%d$, $J\\leq %.1f$" % (z_sel, OPERATING_BAND[1]),
        fontsize=9)
    for i in range(len(pds)):
        for j in range(len(aes)):
            if grid[i][j] == grid[i][j]:
                axes[0].text(j, i, f"{grid[i][j]:.0f}", ha="center", va="center", fontsize=9)
    fig.colorbar(im, ax=axes[0], fraction=0.046)

    for Z in sweep["grid"]["Z"]:
        Js = sorted({r["J"] for r in rows if r["Z"] == Z})
        vals = []
        for J in Js:
            e = [r["e_KT"] for r in rows if r["Z"] == Z and r["J"] == J]
            vals.append(sum(e) / len(e) if e else float("nan"))
        axes[1].plot(Js, vals, "o-", ms=4, label="$Z=%d$" % Z)
    axes[1].axhline(GATE_PLATFORM_PCT, color="#e67e22", ls="--", lw=1.2, label="15% platform gate")
    axes[1].axhline(-GATE_PLATFORM_PCT, color="#e67e22", ls="--", lw=1.2)
    axes[1].axhline(0, color="grey", lw=0.6)
    axes[1].set_xlabel("advance ratio $J$")
    axes[1].set_ylabel("signed mean $e(K_T)$ [%]")
    axes[1].set_title("bias vs $J$ (averaged over swept $P/D$, $A_E/A_0$)", fontsize=9)
    axes[1].grid(alpha=0.3)
    axes[1].legend(fontsize=8)
    fig.suptitle("Fig. 2  Where the BEM-vs-B-series discrepancy lives", fontsize=11)
    fig.tight_layout()
    fig.savefig(path, dpi=150)
    plt.close(fig)


def fig_decomposition(kp505: Dict[str, object], path: Path) -> None:
    rows = kp505["rows"]
    Js = [r["J"] for r in rows]
    band = [r for r in rows if OPERATING_BAND[0] <= r["J"] <= OPERATING_BAND[1]]
    fig, axes = plt.subplots(1, 2, figsize=(12.5, 4.4))
    axes[0].plot(Js, [abs(r["e_KT_bem"]) for r in rows], "s-", color="#c0392b", ms=5,
                 label="BEM vs KP505 experiment")
    axes[0].plot(Js, [abs(r["e_KT_bseries"]) for r in rows], "^-", color="#2980b9", ms=5,
                 label="B-series regression vs same experiment")
    axes[0].plot(Js, [abs(r["e_KT_bem_vs_bseries"]) for r in rows], "o--", color="#7f8c8d", ms=4,
                 label="BEM vs B-series regression")
    axes[0].axhline(GATE_PLATFORM_PCT, color="#e67e22", ls="--", lw=1.2, label="15% platform gate")
    axes[0].axhline(GATE_PAPER_PCT, color="#27ae60", ls=":", lw=1.2, label="3% paper gate")
    axes[0].set_xlabel("advance ratio $J$")
    axes[0].set_ylabel("$|e(K_T)|$ [%]")
    axes[0].set_title("three-way anchoring of the same $K_T$", fontsize=9)
    axes[0].grid(alpha=0.3)
    axes[0].legend(fontsize=8)

    labels = ["BEM vs\nexperiment", "B-series vs\nexperiment", "BEM vs\nB-series"]
    means = [
        sum(abs(r["e_KT_bem"]) for r in band) / len(band),
        sum(abs(r["e_KT_bseries"]) for r in band) / len(band),
        sum(abs(r["e_KT_bem_vs_bseries"]) for r in band) / len(band),
    ]
    bars = axes[1].bar(labels, means, color=["#c0392b", "#2980b9", "#7f8c8d"])
    axes[1].axhline(GATE_PLATFORM_PCT, color="#e67e22", ls="--", lw=1.2, label="15% platform gate")
    axes[1].axhline(GATE_PAPER_PCT, color="#27ae60", ls=":", lw=1.2, label="3% paper gate")
    for b, m in zip(bars, means):
        axes[1].text(b.get_x() + b.get_width() / 2, m, f"{m:.1f}%", ha="center", va="bottom",
                     fontsize=9)
    axes[1].set_ylabel("mean $|e(K_T)|$ [pct], $J\\in[%.1f,%.1f]$" % OPERATING_BAND)
    axes[1].set_title("error decomposition", fontsize=9)
    axes[1].grid(alpha=0.3, axis="y")
    axes[1].legend(fontsize=8)
    fig.suptitle("Fig. 3  How much of the discrepancy is intrinsic (KP505 is not a B-series propeller)",
                 fontsize=11)
    fig.tight_layout()
    fig.savefig(path, dpi=150)
    plt.close(fig)


def fig_diagnosis(diag: Dict[str, object], path: Path) -> None:
    fig, axes = plt.subplots(1, 2, figsize=(12.5, 4.4))
    eff = diag["effective_pitch"]
    labels = ["$P/D$=%.2f\n$A_E/A_0$=%.2f, $Z$=%d" % (e["P_D"], e["Ae_Ao"], e["Z"]) for e in eff]
    ratios = [e["ratio"] for e in eff]
    bars = axes[0].bar(labels, ratios, color="#8e44ad")
    axes[0].axhline(1.0, color="k", ls="--", lw=1.2, label="ratio = 1 (no pitch bias)")
    for b, r, e in zip(bars, ratios, eff):
        if r == r:
            axes[0].text(b.get_x() + b.get_width() / 2, r,
                         "%.3f\n$P/D_{eff}$=%.3f" % (r, e["P_D_eff"]), ha="center", va="bottom",
                         fontsize=8)
    axes[0].set_ylabel("$P/D_{eff} / (P/D)_{geometric}$  ($K_T$-matched at $J=0.4$)")
    axes[0].set_title("diagnosis 1: geometric-pitch bias indicator", fontsize=9)
    axes[0].set_ylim(0, max(1.25, max([r for r in ratios if r == r], default=1.0) * 1.3))
    axes[0].legend(fontsize=8)
    axes[0].tick_params(axis="x", labelsize=7)

    ind = diag["induction"]
    width = 0.35
    idx = list(range(len(ind)))
    axes[1].bar([i - width / 2 for i in idx], [i["KT_momentum"] for i in ind], width,
                color="#c0392b", label="momentum-coupled (shipped)")
    axes[1].bar([i + width / 2 for i in idx], [i["KT_blade_element_only"] for i in ind], width,
                color="#e59866", label="blade element only")
    axes[1].plot(idx, [i["KT_ref"] for i in ind], "k^", ms=8, label="B-series reference")
    for i, item in zip(idx, ind):
        axes[1].text(i, max(item["KT_momentum"], item["KT_blade_element_only"]) + 0.01,
                     "e=%+.1f%% / %+.1f%%" % (item["e_KT_momentum"],
                                              item["e_KT_blade_element_only"]),
                     ha="center", fontsize=8)
    axes[1].set_xticks(idx)
    axes[1].set_xticklabels(["$J$=%.1f" % i["J"] for i in ind])
    axes[1].set_ylabel("$K_T$")
    axes[1].set_title("diagnosis 2: induction share vs section model", fontsize=9)
    axes[1].grid(alpha=0.3, axis="y")
    axes[1].legend(fontsize=8)
    fig.suptitle("Fig. 4  Root-cause diagnostics for the $K_T$ over-prediction", fontsize=11)
    fig.tight_layout()
    fig.savefig(path, dpi=150)
    plt.close(fig)


# ---------------------------------------------------------------------------
# markdown report (all numbers injected, none hand-typed)
# ---------------------------------------------------------------------------
def fmt(x: float, nd: int = 2) -> str:
    if x != x:
        return "n/a"
    return f"{x:.{nd}f}"


def build_markdown(b: Dict[str, object]) -> str:
    kp = b["kp505"]
    sw = b["bseries_sweep"]
    diag = b["diagnostics"]
    v = b["verdicts"]
    acc = b["acceptance"]
    band = kp["bem_vs_experiment_band"]
    full = kp["bem_vs_experiment_full"]
    bs_band = kp["bseries_vs_experiment_band"]
    lo, hi = OPERATING_BAND
    L = []
    A = L.append

    A("# 推进·敞水 BEM 模型的绝对量级复核（m1678）")
    A("")
    A("- 复核对象：`backend/services/propeller_open_water.py`（叶元-动量 BEM 敞水模型）——能力 "
      "`propeller.open_water`（`audit_capabilities` 分级 REAL）的物理内核")
    A("- 复核基准：① KP505 敞水试验（Iftikhar & Takinaci 2023, Table 2，文献，独立）；"
      "② Wageningen B 系列 39/47 项回归（Oosterveld & van Oossanen 1975，"
      "`tools/verify/propeller_open_water/reference_bseries.py` 净室实现，独立于被复现代码）")
    A("- 生成脚本：`tools/verify/propeller_open_water/bem_benchmark.py`（**本报告全部数字由脚本注入**）")
    A("- 工件：`artifacts/m1678/propeller_bem_benchmark/benchmark.json` + fig1–fig4")
    A("- 机器判决（headline）：**%s**" % v["headline"])
    A("")
    A("## 1. 案例介绍")
    A("")
    A("**物理背景。** 敞水试验把桨从船后分离，在均匀来流中测 `KT(J)`、`KQ(J)`，是推进学科的基准试验。"
      "平台此前用硬编码线性代理（`KT = max(0.05, 0.5-0.3J)`、`KQ` 随 `J` 递增、`η0` clamp 在 0.85）"
      "充当该能力（m1413 判 MOCK），后由 BEM 模型替换。BEM 模型合并时自陈的证明义务是：")
    A("")
    A("> \"No literature point data was reachable offline for mission 1393, so the published "
      "verification is analytic-bound + internal-consistency only\"")
    A("")
    A("即已发布的验证只有**上界**（`0 < η0 < η_ideal`）与**恒等式**（`η0 = J·KT/(2πKQ)`、Froude "
      "不变性）。上界与恒等式是必要条件，却无法发现「内部自洽、外部偏差 30%」的模型。"
      "本单用树内两个独立基准补这一环。")
    A("")
    A("**几何与参数表（复核算例）。**")
    A("")
    A("| 参数 | 符号 | 值 | 来源 |")
    A("| --- | --- | --- | --- |")
    g = kp["geometry"]
    A("| 桨直径（仅用于量纲化） | D | %.2f m | BEM 输出为无量纲量，D 不影响 KT/KQ |" % g["D_m"])
    A("| 螺距比 | P/D | %.2f | KP505 试验几何 |" % g["P_D"])
    A("| 盘面比 | A_E/A_0 | %.2f | KP505 试验几何 |" % g["Ae_Ao"])
    A("| 叶数 | Z | %d | KP505 试验几何 |" % g["Z"])
    A("| 水密度 | ρ | 1025 kg/m³ | 平台默认（对无量纲量无影响） |")
    A("| 试验点数 | — | %d | J = 0.1 … 1.0，步长 0.1 |" % len(kp["rows"]))
    A("| B 系列扫描 | — | %d 点 | P/D∈%s, A_E/A_0∈%s, Z∈%s, J=%.1f…%.1f |"
      % (sw["all"]["n"], sw["grid"]["P_D"], sw["grid"]["Ae_Ao"], sw["grid"]["Z"],
         sw["grid"]["J"][0], sw["grid"]["J"][-1]))
    A("")
    A("## 2. 方法与工具链")
    A("")
    A("1. **锚点 A（文献试验）**：KP505 表值只读地取自 `backend/data/propulsion_benchmark_reference.json`"
      "（本单新增，同时闭合 `docs/audit/m1413_propulsion_verification_debt.md` §6 第 5 项"
      "「B 系列/试验表值入库」欠账），并由测试逐点比对 `audit_open_water.py` 中的原始常数，"
      "防止两处漂移。")
    A("2. **锚点 B（回归）**：`reference_bseries.py` 的 39/47 项多项式，用于扫描（P/D, A_E/A_0, Z）"
      "参数面——试验数据不覆盖该面。")
    A("3. **误差定义**：`e = 100·(模型 − 基准)/基准`；统计量取平均绝对误差与最大绝对误差。"
      "主机统计带 `J∈[%.1f, %.1f]`（有效推力段），同时给出全量程 `J∈[0.1, 1.0]`，"
      "避免用区间选择美化判决。" % (lo, hi))
    A("4. **判据**：平台标准 %.0f%%（超过即不可接受）、论文级 %.0f%%，对每个对比量逐项判定。"
      % (acc["platform_standard_percent"], acc["paper_grade_percent"]))
    A("5. **诊断（不拟合）**：① 匹配同一 `KT` 所需等效螺距比（几何螺距偏置指标）；"
      "② 动量耦合项 vs 纯叶元项的推力占比；③ 零推力进速比 `J0` 对比。"
      "诊断量只用于归因，**不回写模型**——把拟合试验当验证是本仓明令禁止的伪证据。")
    A("")
    A("## 3. 计算结果")
    A("")
    A("### 3.1 BEM vs KP505 试验（逐点）")
    A("")
    A("| J | KT_exp | KT_BEM | e(KT) % | KQ_exp | KQ_BEM | e(KQ) % | η0_exp | η0_BEM | e(η0) % |")
    A("| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |")
    for r in kp["rows"]:
        A("| %.1f | %.4f | %.4f | %s | %.4f | %.5f | %s | %.3f | %.3f | %s |"
          % (r["J"], r["KT_exp"], r["KT_bem"], fmt(r["e_KT_bem"], 1), r["KQ_exp"], r["KQ_bem"],
             fmt(r["e_KQ_bem"], 1), r["eta_exp"], r["eta_bem"], fmt(r["e_eta_bem"], 1)))
    A("")
    A("### 3.2 误差统计（BEM vs 试验）")
    A("")
    A("| 量 | 区间 | 平均绝对误差 % | 最大绝对误差 % | 是否 <15% |")
    A("| --- | --- | --- | --- | --- |")
    for q, label in (("KT", "$K_T$"), ("KQ", "$K_Q$"), ("eta", r"$\eta_0$")):
        A("| %s | $J\\in[%.1f,%.1f]$ | %s | %s | %s |"
          % (label, lo, hi, fmt(band[q]["mean_abs"]), fmt(band[q]["max_abs"]),
             "是" if band[q]["max_abs"] <= GATE_PLATFORM_PCT else "**否**"))
    for q, label in (("KT", "$K_T$"), ("KQ", "$K_Q$"), ("eta", r"$\eta_0$")):
        A("| %s | $J\\in[0.1,1.0]$ 全量程 | %s | %s | %s |"
          % (label, fmt(full[q]["mean_abs"]), fmt(full[q]["max_abs"]),
             "是" if full[q]["max_abs"] <= GATE_PLATFORM_PCT else "**否**"))
    A("")
    A("### 3.3 误差分解：一个 KT，三个参考链")
    A("")
    A("| 对比 | 平均绝对误差 %% ($J\\in[%.1f,%.1f]$) | 说明 |" % (lo, hi))
    A("| --- | --- | --- |")
    A("| BEM vs 试验 | %s | 被复核模型对独立试验的偏差 |" % fmt(band["KT"]["mean_abs"]))
    A("| B 系列回归 vs 同一试验 | %s | 基准自身的模型形式偏移（KP505 非标准 B 系列） |"
      % fmt(bs_band["KT"]["mean_abs"]))
    A("| BEM vs B 系列回归 | %s | 两条模型链之间的偏差 |"
      % fmt(kp["bem_vs_bseries_band"]["KT"]["mean_abs"]))
    A("")
    A("### 3.4 B 系列有效性箱扫描（BEM vs 回归）")
    A("")
    A("扫描 %d 点，其中 %d 点落在「基准仍出力」的可比区间"
      "（$K_T^{ref}\\geq %.2f$ 且 $|K_Q^{ref}|\\geq %.3f$）——"
      "在 `J→J0` 处基准的 `KT` 本身趋于零，相对误差在那里没有物理含义，"
      "故相对误差统计一律带此掩码，而绝对误差对全部 %d 点报出，掩码不隐藏任何东西。"
      % (sw["n_total"], sw["n_comparable"], sw["comparable_mask"]["KT_ref_min"],
         sw["comparable_mask"]["KQ_ref_min"], sw["n_total"]))
    A("")
    A("| 量 | 平均绝对误差 % | 最大绝对误差 % | 样本数 |")
    A("| --- | --- | --- | --- |")
    A("| $K_T$ | %s | %s | %d |"
      % (fmt(sw["all"]["mean_abs"]), fmt(sw["all"]["max_abs"]), sw["all"]["n"]))
    A("| $K_Q$ | %s | %s | %d |"
      % (fmt(sw["KQ_all"]["mean_abs"]), fmt(sw["KQ_all"]["max_abs"]), sw["KQ_all"]["n"]))
    A("| $\\eta_0$ | %s | %s | %d |"
      % (fmt(sw["eta_all"]["mean_abs"]), fmt(sw["eta_all"]["max_abs"]), sw["eta_all"]["n"]))
    A("")
    A("绝对误差（全 %d 点，$K_T$ 与 $K_Q$ 的绝对偏差，无量纲）：平均 `|ΔK_T| = %s`、"
      "`max|ΔK_T| = %s`。未加掩码的原始相对误差统计为平均 %s%% / 最大 %s%%——"
      "该极端值只来自 `J` 越过基准 `J0` 后的近零点，列此以说明掩码的必要性，"
      "不作为精度指标。"
      % (sw["n_total"], fmt(sw["abs_KT"]["mean_abs"], 4), fmt(sw["abs_KT"]["max_abs"], 4),
         fmt(sw["unmasked_KT"]["mean_abs"], 1), fmt(sw["unmasked_KT"]["max_abs"], 1)))
    A("")
    A("按维度分组的 $|e(K_T)|$（平均 %）：")
    A("")
    A("| 维度 | 取值: 平均 $|e(K_T)|$ % |")
    A("| --- | --- |")
    for key, label in (("by_P_D", "P/D"), ("by_Ae_Ao", "A_E/A_0"), ("by_Z", "Z")):
        A("| %s | %s |" % (label, ", ".join("%s: %s" % (k, fmt(val["mean_abs"]))
                                            for k, val in sw[key].items())))
    A("")
    A("### 3.5 诊断")
    A("")
    j0 = diag["J0"]
    A("- **零推力进速比**：BEM `J0 = %.4f`，B 系列回归 `J0 = %.4f`，偏差 `%s %%`。"
      "试验 `J0` 不予报出——KP505 表在 J=1.0 处 `KT` 仍有 0.029，外推得到的 `J0` 不是测量值。"
      % (j0["J0_bem"], j0["J0_bseries"], fmt(j0["e_J0_pct"], 1)))
    A("")
    A("| 几何 (P/D, A_E/A_0, Z) | P/D_eff（KT 匹配 @J=0.4） | 比值 | 解读 |")
    A("| --- | --- | --- | --- |")
    for e in diag["effective_pitch"]:
        A("| (%.2f, %.2f, %d) | %s | %s | %s |"
          % (e["P_D"], e["Ae_Ao"], e["Z"], fmt(e["P_D_eff"], 4), fmt(e["ratio"], 4),
             "BEM 单位几何螺距产推力偏大" if e["ratio"] < 1 else "BEM 单位几何螺距产推力偏小"))
    A("")
    A("| J | KT 动量耦合 | KT 纯叶元 | KT 基准 | e 动量 % | e 纯叶元 % | 动量项占比 % |")
    A("| --- | --- | --- | --- | --- | --- | --- |")
    for i in diag["induction"]:
        A("| %.1f | %.4f | %.4f | %.4f | %s | %s | %s |"
          % (i["J"], i["KT_momentum"], i["KT_blade_element_only"], i["KT_ref"],
             fmt(i["e_KT_momentum"], 1), fmt(i["e_KT_blade_element_only"], 1),
             fmt(i["induction_share_pct"], 1)))
    A("")
    ib = b["ideal_bound"]
    A("- **物理上界（已有的自洽判据）仍然成立**：在 %d 个出力点（`KT_BEM > 0`）上 "
      "`0 < η0 < η_ideal` 无违反；超出模型自身 `J0` 的点（`J = %s`）不参与该判据，"
      "因为那里 `η0 ≡ 0`、上界无约束力。这说明本单发现的是**量级偏差**，不是能量守恒破坏——"
      "正是「上界类判据无法发现量级偏差」的实例。"
      % (ib["n_thrusting_points"], ib["degenerate_no_thrust_J"] or "无"))
    A("")
    A("![fig1](../../artifacts/m1678/propeller_bem_benchmark/fig1_kp505_curves.png)")
    A("")
    A("**图 1**　KP505 敞水曲线三路对照：平台 BEM（红方）、文献试验（黑圆）、B 系列回归（蓝三角）。"
      "BEM 在中段 `J` 抬高 `KT`/`KQ`，且 `KT` 过零点提前。")
    A("")
    A("![fig2](../../artifacts/m1678/propeller_bem_benchmark/fig2_error_map.png)")
    A("")
    A("**图 2**　有效性箱扫描误差分布：(左) `(P/D, A_E/A_0)` 面上的平均 `|e(KT)|`；"
      "(右) 各叶数下随 `J` 的平均偏差——偏差在中 `J` 段最大，说明不是常数标定误差。")
    A("")
    A("![fig3](../../artifacts/m1678/propeller_bem_benchmark/fig3_anchoring_decomposition.png)")
    A("")
    A("**图 3**　误差分解：B 系列回归对同一试验的平均偏差（%s%%）远小于 BEM 的偏差（%s%%），"
      "因此「KP505 不是标准 B 系列」只能解释小部分，主因在 BEM 自身。"
      % (fmt(bs_band["KT"]["mean_abs"]), fmt(band["KT"]["mean_abs"])))
    A("")
    A("![fig4](../../artifacts/m1678/propeller_bem_benchmark/fig4_diagnosis.png)")
    A("")
    A("**图 4**　根因诊断：(左) 等效螺距比一致低于几何螺距；(右) 动量耦合与纯叶元两种方案的 `KT` 对比，"
      "用于分离「诱导速度」与「剖面模型」的贡献。")
    A("")
    A("## 4. 注意点（坑与局限）")
    A("")
    A("1. **KP505 不是标准 B 系列桨。** 其剖面为弯度型、叶宽/螺距分布不同于 B 系列族，故"
      "「B 系列回归 vs KP505 试验」自带模型形式偏移；本单用 3.3 三路分解把这一份额量化，"
      "而不是拿它当挡箭牌。")
    A("2. **近零推力段相对误差失真。** `J→J0` 时 `KT→0`，相对误差分母趋零；故主机统计带取 "
      "`J∈[%.1f, %.1f]`，全量程另列作参考。" % (lo, hi))
    A("3. **一级叶元/动量模型的上限。** BEM 未含剖面库（用 `Cl = 2π sinα`、`Cd = 0.012 + α²`）、"
      "未含梢涡卷起与桨毂涡损失，对照试验的偏差不可能为零；本单量化的是**偏差有多大**，"
      "不是声称 BEM 应当精确。")
    A("4. **未覆盖**：空泡、非均匀来流、桨-船干扰（伴流/推力减额）、尺度效应（Rn 修正）。"
      "`propeller.open_water` 若被用于自航预报，还需 `w`、`t`、`η_R` 的独立验证。")
    A("5. **本单不改模型。** 只做复核与归因；修复方案见 §6，需另立单并各自带判据。")
    A("")
    A("## 5. 六个要素自查")
    A("")
    A("| 要素 | 落点 |")
    A("| --- | --- |")
    A("| ① 案例介绍 | §1：KP505 几何 + 参数表 + 基准来源 |")
    A("| ② 方法与工具链 | §2：两个独立锚点、误差定义、判据、诊断 |")
    A("| ③ 计算结果 | §3.1–3.5 数字表（脚本注入） |")
    A("| ④ 结果图 | 图 1–4（matplotlib 生成，PNG 入库） |")
    A("| ⑤ 注意点 | §4 局限与适用范围 |")
    A("| ⑥ 误差判定 | §6 逐项 + 显式「是否 <3%」 |")
    A("")
    A("## 6. 误差判定（逐项，含「是否 <3%」显式回答）")
    A("")
    A("| 关键量 | 基准 | 平均绝对误差 % | 最大绝对误差 % | <15%? | <3%? |")
    A("| --- | --- | --- | --- | --- | --- |")
    rows_spec = [
        ("$K_T$（$J\\in[%.1f,%.1f]$）" % (lo, hi), "KP505 试验", band["KT"]),
        ("$K_Q$（$J\\in[%.1f,%.1f]$）" % (lo, hi), "KP505 试验", band["KQ"]),
        ("$\\eta_0$（$J\\in[%.1f,%.1f]$）" % (lo, hi), "KP505 试验", band["eta"]),
        ("$K_T$（扫描 $J\\in[0.1,1.2]$）", "B 系列回归", sw["all"]),
        ("$K_Q$（扫描 $J\\in[0.1,1.2]$）", "B 系列回归", sw["KQ_all"]),
        ("$\\eta_0$（扫描 $J\\in[0.1,1.2]$）", "B 系列回归", sw["eta_all"]),
    ]
    for label, base, st in rows_spec:
        A("| %s | %s | %s | %s | %s | %s |"
          % (label, base, fmt(st["mean_abs"]), fmt(st["max_abs"]),
             "是" if st["max_abs"] <= GATE_PLATFORM_PCT else "**否**",
             "是" if st["max_abs"] <= GATE_PAPER_PCT else "**否**"))
    A("")
    A("**显式回答「是否 <3%%」：否。** 有效推力段内 `K_T`、`K_Q`、`η_0` 对试验基准的最大绝对误差"
      "分别为 %s%%、%s%%、%s%%，均远超 15%% 平台门槛。"
      % (fmt(band["KT"]["max_abs"]), fmt(band["KQ"]["max_abs"]), fmt(band["eta"]["max_abs"])))
    A("")
    A("## 7. 结论与建议（按优先级）")
    A("")
    A("1. **降级**：把 `propeller.open_water` 的证据等级从 REAL 降为 MOCK，"
      "直到 `e(K_T)` 在有效推力段落到 15%% 以内。本单的机器判决为 %s。" % v["headline"])
    A("2. **修剖面模型**（根因主项）：用弯度型剖面（NACA a=0.8）的零升角与升力线斜率替换 "
      "`Cl = 2π sinα`，即在 `α` 中扣除理想迎角；当前实现把弯度剖面的升力完全算成对称剖面，"
      "等效螺距比因此系统性偏低（见 3.5 表）。")
    A("3. **修叶宽/螺距分布**：`chord_shape = sin(πt)^0.55` 与 B 系列真实叶宽分布差异较大，"
      "建议改为 B 系列可实现叶宽分布，并以 `A_E/A_0` 归一。")
    A("4. **加回归保护**：把本单 3.1 表作为回归基准（允许的偏差带由修复目标反推），"
      "CI 中运行 `pytest tests/verify/test_propeller_bem_benchmark.py`。")
    A("5. **不要用拟合消差**：任何把 `KT` 曲线拉向试验的参数整定都必须同时给出独立验证"
      "（另一桨型或另一工况带），否则只是把 MOCK 换成 calibrated MOCK。")
    A("")
    A("*本报告由 `tools/verify/propeller_open_water/bem_benchmark.py` 生成；"
      "所有数字来自 `benchmark.json`，无手抄。*")
    return "\n".join(L) + "\n"


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------
def run(quick: bool = False) -> Dict[str, object]:
    reference = load_reference()
    cases = {c["id"]: c for c in reference["cases"]}
    kp505_case = case_kp505(cases["KP505_EXPERIMENT"])

    # Independent sanity rail: every BEM point must stay below the actuator-disc
    # bound.  A violation would mean the model is unphysical, not merely biased.
    # Only points that actually carry thrust can be tested against the
    # actuator-disc bound; beyond the model's own zero-thrust J0 the efficiency
    # is identically 0 and the rail is vacuous (recorded as `degenerate`).
    thrusting = [r for r in kp505_case["rows"] if r["KT_bem"] > 0.0]
    bound_violations = [r["J"] for r in thrusting
                        if not (0.0 < r["eta_bem"] < r["eta_ideal_bem"])]
    degenerate = [r["J"] for r in kp505_case["rows"] if r["KT_bem"] <= 0.0]

    sweep = case_bseries_sweep(quick=quick)
    diag = diagnostics(kp505_case)

    band = kp505_case["bem_vs_experiment_band"]
    worst = max(abs(band[q]["max_abs"]) for q in ("KT", "KQ", "eta"))
    headline = (
        "FAIL（BEM vs KP505 试验：有效推力段最大绝对误差 %.1f%%，平台门槛 %.0f%%）" % (
            worst, GATE_PLATFORM_PCT)
        if worst > GATE_PLATFORM_PCT else
        "PASS（BEM vs KP505 试验：有效推力段最大绝对误差 %.1f%%）" % worst
    )

    bundle = {
        "mission": 1678,
        "generated_by": "tools/verify/propeller_open_water/bem_benchmark.py",
        "subject": "backend/services/propeller_open_water.py",
        "acceptance": {
            "platform_standard_percent": reference["acceptance"]["platform_standard_percent"],
            "paper_grade_percent": reference["acceptance"]["paper_grade_percent"],
            "band_J": list(OPERATING_BAND),
        },
        "kp505": kp505_case,
        "bseries_sweep": sweep,
        "diagnostics": diag,
        "ideal_bound": {
            "n_thrusting_points": len(thrusting),
            "violations_J": bound_violations,
            "degenerate_no_thrust_J": degenerate,
            "rule": "0 < eta_0 < eta_ideal must hold wherever KT_bem > 0",
        },
        "out_dir": _as_display_path(OUT_DIR),
        "doc_path": _as_display_path(DOC_PATH),
        "verdicts": {
            "headline": headline,
            "KT_band": classify(band["KT"]["max_abs"]),
            "KQ_band": classify(band["KQ"]["max_abs"]),
            "eta_band": classify(band["eta"]["max_abs"]),
            "KT_sweep": classify(sweep["all"]["max_abs"]),
            "KQ_sweep": classify(sweep["KQ_all"]["max_abs"]),
        },
    }

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    fig_kp505(kp505_case, OUT_DIR / "fig1_kp505_curves.png")
    fig_error_map(sweep, OUT_DIR / "fig2_error_map.png")
    fig_decomposition(kp505_case, OUT_DIR / "fig3_anchoring_decomposition.png")
    fig_diagnosis(diag, OUT_DIR / "fig4_diagnosis.png")

    with open(OUT_DIR / "benchmark.json", "w", encoding="utf-8") as fh:
        json.dump(bundle, fh, ensure_ascii=False, indent=2)
        fh.write("\n")

    DOC_PATH.parent.mkdir(parents=True, exist_ok=True)
    DOC_PATH.write_text(build_markdown(bundle), encoding="utf-8")
    return bundle


def main(argv: List[str]) -> int:
    quick = "--quick" in argv
    bundle = run(quick=quick)
    v = bundle["verdicts"]
    band = bundle["kp505"]["bem_vs_experiment_band"]
    print("BEM vs KP505 experiment, J in [%.1f, %.1f]:" % OPERATING_BAND)
    for q in ("KT", "KQ", "eta"):
        print("  %-4s mean|e|=%6.2f%%  max|e|=%6.2f%%" % (q, band[q]["mean_abs"],
                                                           band[q]["max_abs"]))
    print("B-series sweep (n=%d): KT mean|e|=%.2f%% max=%.2f%%"
          % (bundle["bseries_sweep"]["all"]["n"],
             bundle["bseries_sweep"]["all"]["mean_abs"],
             bundle["bseries_sweep"]["all"]["max_abs"]))
    ib = bundle["ideal_bound"]
    print("ideal-bound: %d thrusting points, violations=%s, no-thrust points J=%s"
          % (ib["n_thrusting_points"], ib["violations_J"] or "none",
             ib["degenerate_no_thrust_J"] or "none"))
    print("verdicts:", v)
    print("artifacts:", OUT_DIR)
    print("doc:", DOC_PATH)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
