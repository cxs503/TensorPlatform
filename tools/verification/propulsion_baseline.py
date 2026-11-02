#!/usr/bin/env python3
"""Reproducible propulsion (推进) baseline runner: open-water audit v1.

Generates, from code only (no hand-copied numbers):

* ``artifacts/propulsion/results.json`` -- gate verdicts for the model under test
  (serving ``wageningen_b``), for the declared reference dataset, and for the
  RBC-1 reference curve; plus the pointwise residual table and the analytic
  anchors (actuator-disk bound, zero-thrust advance ratios);
* ``artifacts/propulsion/fig_open_water.png`` -- KT / KQ / eta_0 vs J with the
  declared dataset, the serving model, the RBC-1 curve and the actuator-disk
  ideal-efficiency bound;
* ``artifacts/propulsion/fig_deviation.png`` -- pointwise relative deviation of
  the serving model from its own cited dataset, against the 3 % / 15 % lines.

Usage:  MPLBACKEND=Agg python3 tools/verification/propulsion_baseline.py
"""
from __future__ import annotations

import json
import math
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[2] / "backend"
sys.path.insert(0, str(BACKEND))

import matplotlib                                   # noqa: E402
matplotlib.use("Agg")
import matplotlib.pyplot as plt                     # noqa: E402

from methods.propulsion import (                    # noqa: E402
    SERVING_PROVENANCE, audit_open_water_curve, fit_reference_model,
    ideal_efficiency_from_KT_J, ideal_wake_fraction, load_reference_dataset,
    serving_wageningen_b, thrust_loading_coefficient,
)

OUT = Path(__file__).resolve().parents[2] / "artifacts" / "propulsion"
TOL_OK, TOL_BAD = 0.03, 0.15


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    ds = load_reference_dataset()
    dp = ds["data_points"]
    J = dp["J"]
    KT, KQ, ETA = dp["KT"], dp["KQ"], dp["eta_0"]

    ref = fit_reference_model(J, KT, KQ)

    # ---- model under test: serving wageningen_b at the dataset's own P/D, Ae/Ao, Z
    part = ds["principal_particulars"]
    P_D, Ae_Ao, Z = part["pitch_ratio_P_D"], part["area_ratio_Ae_A0"], part["blade_count"]
    rows = []
    for j, kt_ref, kq_ref, eta_ref in zip(J, KT, KQ, ETA):
        out = serving_wageningen_b(j, P_D=P_D, Ae_Ao=Ae_Ao, Z=Z)
        rows.append({
            "J": j,
            "KT_ref": kt_ref, "KT_serving": out["KT"],
            "KT_dev": (out["KT"] - kt_ref) / kt_ref if kt_ref > 0 else None,
            "KQ_ref": kq_ref, "KQ_serving": out["KQ"],
            "KQ_dev": (out["KQ"] - kq_ref) / kq_ref if kq_ref > 0 else None,
            "eta_ref": eta_ref, "eta_serving": out["eta_0"],
            "eta_dev": (out["eta_0"] - eta_ref) / eta_ref if eta_ref > 0 else None,
            "eta_identity_dev": abs(j * kt_ref / (2 * math.pi * kq_ref) - eta_ref) if kq_ref > 0 else None,
        })

    # ---- gates
    j_fine = [i * 0.01 for i in range(0, 121)]
    serv_curve = {"J": [], "KT": [], "KQ": [], "eta_0": []}
    for j in j_fine:
        o = serving_wageningen_b(j, P_D=P_D, Ae_Ao=Ae_Ao, Z=Z)
        for k in serv_curve:
            serv_curve[k].append(o[k])
    ref_curve = ref.curve(j_fine)

    audits = {
        "serving_wageningen_b": audit_open_water_curve(
            serv_curve["J"], serv_curve["KT"], serv_curve["KQ"], serv_curve["eta_0"]),
        "declared_dataset": audit_open_water_curve(J, KT, KQ, ETA),
        "reference_RBC1": audit_open_water_curve(
            ref_curve["J"], ref_curve["KT"], ref_curve["KQ"], ref_curve["eta_0"]),
    }

    # ---- analytic anchors
    anchors = {}
    for j, kt in ((0.4, 0.28), (0.6, 0.18), (0.8, 0.10)):
        ct = thrust_loading_coefficient(j, kt)
        anchors[f"J={j}"] = {
            "CT": ct,
            "eta_ideal": 2.0 / (1.0 + math.sqrt(1.0 + ct)),
            "ideal_wake_fraction": ideal_wake_fraction(ct),
            "dataset_eta0": float(j * kt / (2 * math.pi * dict(zip(J, KQ))[j])),
        }
    for name, a in anchors.items():
        a["utilisation"] = a["dataset_eta0"] / a["eta_ideal"]

    max_dev = {
        "KT": max(abs(r["KT_dev"]) for r in rows if r["KT_dev"] is not None),
        "KQ": max(abs(r["KQ_dev"]) for r in rows if r["KQ_dev"] is not None),
        "eta_0": max(abs(r["eta_dev"]) for r in rows if r["eta_dev"] is not None),
    }

    results = {
        "mission": "m1595",
        "domain": "propulsion / propeller open water",
        "provenance": SERVING_PROVENANCE,
        "dataset": {"source": ds.get("source"), "notes": dp.get("notes"),
                    "j": J, "kt": KT, "kq": KQ, "eta_0": ETA},
        "reference_model_RBC1": {
            "form": "monotone cubic Hermite interpolation (Fritsch-Carlson) of the declared dataset",
            "interpolation_residual": ref.mu,
            "kt_zero_J": ref.kt_zero_J,
            "kq_zero_J": ref.kq_zero_J,
        },
        "residual_table": rows,
        "max_abs_deviation_serving_vs_dataset": max_dev,
        "tolerance": {"ok": TOL_OK, "unacceptable": TOL_BAD},
        "audits": audits,
        "analytic_anchors": anchors,
        "verdicts": {},
    }
    v = results["verdicts"]
    v["serving_metadata_claims_verified"] = False
    v["reason"] = ("serving metadata declares validation_status='verified' against Wageningen/Troost "
                   "open-water data, but its regression deviates from its own cited dataset by "
                   f"{max_dev['KT']*100:.1f}% (KT) / {max_dev['KQ']*100:.1f}% (KQ), far beyond the 15% "
                   "acceptance threshold, and has no zero-thrust point inside its declared J range")
    v["dataset_eta_identity_violation_at"] = 0.8
    v["absolute_accuracy"] = "WITHHELD: B-series coefficient table not traceable offline; see docs/verification/PROPULSION_OPENWATER_V1.md"

    (OUT / "results.json").write_text(json.dumps(results, indent=2, ensure_ascii=False))

    # ---------------- figure 1: curves
    fig, axes = plt.subplots(1, 3, figsize=(15, 4.2))
    jj = serv_curve["J"]
    for ax, key, ylab in ((axes[0], "KT", "$K_T$ [-]"), (axes[1], "KQ", "$K_Q$ [-]"),
                          (axes[2], "eta_0", r"$\eta_0$ [-]")):
        ax.plot(jj, serv_curve[key], "-", lw=2, label="serving wageningen_b (P/D=1.0)")
        ax.plot(jj, ref_curve[key], "--", lw=2, label="RBC-1 reference (declared dataset)")
        ax.plot(J, {"KT": KT, "KQ": KQ, "eta_0": ETA}[key], "o", ms=6,
                label="declared dataset points")
        if key == "eta_0":
            ideal = [ideal_efficiency_from_KT_J(j, kt) for j, kt in zip(jj, serv_curve["KT"]) if j > 0]
            ax.plot(jj[1:], ideal, ":", lw=1.6, label=r"actuator-disk bound $\eta_i$ (serving $K_T$)")
            ax.set_ylim(0, 1.05)
        ax.set_xlabel("advance ratio $J$"); ax.set_ylabel(ylab)
        ax.grid(alpha=0.3); ax.legend(fontsize=7)
    fig.suptitle("Propeller open water: declared dataset vs serving model vs reference (m1595)")
    fig.tight_layout()
    fig.savefig(OUT / "fig_open_water.png", dpi=130)
    plt.close(fig)

    # ---------------- figure 2: deviations
    js = [r["J"] for r in rows if r["KT_dev"] is not None]
    fig, ax = plt.subplots(figsize=(7.6, 4.5))
    for key, marker in (("KT_dev", "o"), ("KQ_dev", "s"), ("eta_dev", "^")):
        xs = [r["J"] for r in rows if r[key] is not None]
        ys = [abs(r[key]) * 100 for r in rows if r[key] is not None]
        ax.plot(xs, ys, marker + "-", label=key.replace("_dev", ""))
    ax.axhline(TOL_OK * 100, color="green", ls="--", lw=1.2, label="3% target")
    ax.axhline(TOL_BAD * 100, color="red", ls="--", lw=1.2, label="15% acceptance limit")
    ax.set_yscale("log"); ax.set_xlabel("advance ratio $J$")
    ax.set_ylabel("|relative deviation| [%] (serving vs its cited dataset)")
    ax.grid(alpha=0.3, which="both"); ax.legend(fontsize=8)
    ax.set_title("Audit: serving wageningen_b vs its own reference dataset")
    fig.tight_layout(); fig.savefig(OUT / "fig_deviation.png", dpi=130); plt.close(fig)

    print(json.dumps({"max_dev": max_dev,
                      "serving_gates": {k: (None if v_["pass"] is None else v_["pass"])
                                        for k, v_ in audits["serving_wageningen_b"]["gates"].items()},
                      "dataset_gates": {k: (None if v_["pass"] is None else v_["pass"])
                                        for k, v_ in audits["declared_dataset"]["gates"].items()},
                      "ref_gates": {k: (None if v_["pass"] is None else v_["pass"])
                                    for k, v_ in audits["reference_RBC1"]["gates"].items()}},
                     indent=2))
    print("artifacts ->", OUT)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())