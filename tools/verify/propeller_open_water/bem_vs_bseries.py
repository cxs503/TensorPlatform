"""Absolute-magnitude verification of the BEM open-water model against the
Wageningen B-series regression (verification debt m1413 §6.5).

Background
----------
``backend/services/propeller_open_water.py`` replaced a MOCK linear surrogate
(KT = max(0.05, 0.5-0.3J), KQ = max(0.01, 0.05+0.02J), eta clamped at 0.85) with a
blade-element / momentum model.  Its own verification (mission 1393) could only
use *self-referential* criteria -- the actuator-disc bound ``eta_ideal`` and
invariance identities -- because no literature table was available in the repo at
that time.  ``docs/audit/m1413_propulsion_verification_debt.md`` §6 item 5 states
the remaining debt explicitly:

    "B 系列 KT/KQ 表值 ... 入库 ... 届时本套件的 J1-J7 可直接叠加绝对量级判据"

This module closes that debt in its measurable part: it evaluates the B-series
regression polynomials (``tools/verify/propeller_open_water/reference_bseries.py``,
Oosterveld & van Oossanen 1975 coefficients) on the *same* geometry as the BEM
model and reports KT / KQ / eta_0 differences point by point.

Two distinct claims are separated, because they fail for different reasons:

* **Propeller regime** (``0 < J < 0.9 J0``): the BEM should reproduce the
  regression to within the modelling error of a lift-line/BEM model
  (finite-span lift slope, camber, section drag).  Deviations here are *bias*.
* **Brake / windmill regime** (``J > J0``): momentum theory still has a solution
  (a < 0), but the induced-velocity search must be allowed to go negative.  If
  the search band is one-sided the model silently degrades into uncoupled
  blade-element theory and over-predicts the braking thrust.  That is a *bug*,
  not a bias, and it is judged separately.

Usage
-----
    python3 tools/verify/propeller_open_water/bem_vs_bseries.py --tag baseline
    python3 tools/verify/propeller_open_water/bem_vs_bseries.py --tag after

Artifacts land in ``artifacts/<mission>/bem_vs_bseries_<tag>.json``.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import sys
from typing import Dict, List

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from backend.services.propeller_open_water import (  # noqa: E402
    PropellerGeometry,
    open_water_point,
    solve_zero_thrust_advance_ratio,
)
from tools.verify.propeller_open_water import reference_bseries as ref  # noqa: E402

TWO_PI = 2.0 * math.pi

# B4-70 family: the geometry class the platform advertises (see
# ``backend/services/propeller_open_water.py`` docstring, "Wageningen-style").
CASE_Z = 4
CASE_AE_A0 = 0.70
CASE_PD = (0.8, 1.0, 1.2)
SECTIONS = 40          # production radial resolution
SECTIONS_CHECK = 16    # coarse resolution, used for a discretisation check


def _rel(pred: float, base_: float) -> float:
    return 100.0 * (pred - base_) / base_ if abs(base_) > 1e-12 else float("nan")


def _eta(J: float, KT: float, KQ: float) -> float:
    return J * KT / (TWO_PI * KQ) if KQ > 1e-14 else 0.0


def reference_point(J: float, pd: float) -> Dict[str, float]:
    row = ref.open_water_coefficients(J, pd, CASE_AE_A0, CASE_Z)
    return {"J": J, "KT": row["KT"], "KQ": row["KQ"], "eta0": row["eta_0"]}


def sweep(pd: float, js: List[float], sections: int) -> List[Dict[str, float]]:
    geom = PropellerGeometry(
        pitch_ratio=pd, blade_area_ratio=CASE_AE_A0, blade_count=CASE_Z, sections=sections
    )
    rows = []
    for J in js:
        bem = open_water_point(geom, J)
        r = reference_point(J, pd)
        rows.append(
            {
                "J": J,
                "KT_bem": bem["KT"],
                "KT_ref": r["KT"],
                "err_KT_pct": _rel(bem["KT"], r["KT"]),
                "KQ_bem": bem["KQ"],
                "KQ_ref": r["KQ"],
                "err_KQ_pct": _rel(bem["KQ"], r["KQ"]),
                "eta0_bem": bem["eta0"],
                "eta0_ref": r["eta0"],
                "err_eta0_pct": _rel(bem["eta0"], r["eta0"]) if r["eta0"] > 1e-9 else float("nan"),
                "eta_ideal": bem["eta_ideal"],
                "eta_ratio": bem["eta0"] / bem["eta_ideal"] if bem["eta_ideal"] > 1e-12 else float("nan"),
                "induction_converged": bem["induction_converged"],
            }
        )
    return rows


def brake_evidence(pd: float) -> Dict[str, float]:
    """Zero-thrust advance ratio and post-J0 thrust magnitude."""
    geom = PropellerGeometry(
        pitch_ratio=pd, blade_area_ratio=CASE_AE_A0, blade_count=CASE_Z, sections=SECTIONS
    )
    j0_bem = solve_zero_thrust_advance_ratio(geom)
    j0_ref = ref.zero_thrust_J(pd, CASE_AE_A0, CASE_Z)
    peak_bem = max(
        (open_water_point(geom, j) for j in [0.1 * i for i in range(1, 14)]),
        key=lambda p: p["eta0"],
    )
    peak_ref = ref.peak_efficiency(pd, CASE_AE_A0, CASE_Z)
    # a propeller just past J0 brakes gently: |KT| must stay small.
    j_brake = 1.08 * j0_ref
    kt_bem_brake = open_water_point(geom, j_brake)["KT"]
    kt_ref_brake = reference_point(j_brake, pd)["KT"]
    return {
        "J0_bem": j0_bem,
        "J0_ref": j0_ref,
        "err_J0_pct": _rel(j0_bem, j0_ref),
        "peak_eta_bem": peak_bem["eta0"],
        "peak_eta_ref": peak_ref["eta_0"],
        "err_peak_eta_pct": _rel(peak_bem["eta0"], peak_ref["eta_0"]),
        "J_peak_bem": peak_bem["J"],
        "J_peak_ref": peak_ref["J"],
        "brake_J": j_brake,
        "KT_brake_bem": kt_bem_brake,
        "KT_brake_ref": kt_ref_brake,
        "KT_brake_ratio": kt_bem_brake / kt_ref_brake if abs(kt_ref_brake) > 1e-12 else float("nan"),
    }


def build(pd_list=CASE_PD, sections: int = SECTIONS) -> Dict[str, object]:
    cases = []
    for pd in pd_list:
        j0_ref = ref.zero_thrust_J(pd, CASE_AE_A0, CASE_Z)
        js = sorted({round(0.1 * i, 2) for i in range(0, 13)} | {round(0.85 * j0_ref, 3)})
        rows = sweep(pd, js, sections)
        prop = [r for r in rows if 0.2 <= r["J"] <= 0.85 * j0_ref]
        brake = [r for r in rows if r["J"] > j0_ref]

        def mean(vals):
            vals = [v for v in vals if v == v]  # drop NaN
            return sum(vals) / len(vals) if vals else float("nan")

        cases.append(
            {
                "P_D": pd,
                "Ae_A0": CASE_AE_A0,
                "Z": CASE_Z,
                "J0_ref": j0_ref,
                "sections": sections,
                "rows": rows,
                "propeller_regime": {
                    "n": len(prop),
                    "mean_abs_err_KT_pct": mean([abs(r["err_KT_pct"]) for r in prop]),
                    "max_abs_err_KT_pct": max(abs(r["err_KT_pct"]) for r in prop),
                    "mean_abs_err_KQ_pct": mean([abs(r["err_KQ_pct"]) for r in prop]),
                    "max_abs_err_KQ_pct": max(abs(r["err_KQ_pct"]) for r in prop),
                    "mean_abs_err_eta0_pct": mean([abs(r["err_eta0_pct"]) for r in prop]),
                    "max_abs_err_eta0_pct": max(abs(r["err_eta0_pct"]) for r in prop),
                },
                "brake_regime": {
                    "n": len(brake),
                    "worst_KT_abs": max(abs(r["KT_bem"]) for r in brake) if brake else 0.0,
                    "min_eta_ratio_ok": all(
                        (r["eta0_bem"] < r["eta_ideal"]) or r["KT_bem"] <= 0 for r in brake
                    ),
                },
                "brake": brake_evidence(pd),
            }
        )
    summary = {
        "cases": len(cases),
        "mean_abs_err_KT_pct": sum(c["propeller_regime"]["mean_abs_err_KT_pct"] for c in cases) / len(cases),
        "mean_abs_err_KQ_pct": sum(c["propeller_regime"]["mean_abs_err_KQ_pct"] for c in cases) / len(cases),
        "mean_abs_err_eta0_pct": sum(c["propeller_regime"]["mean_abs_err_eta0_pct"] for c in cases) / len(cases),
        "max_abs_err_KT_pct": max(c["propeller_regime"]["max_abs_err_KT_pct"] for c in cases),
        "max_abs_err_eta0_pct": max(c["propeller_regime"]["max_abs_err_eta0_pct"] for c in cases),
        "worst_J0_err_pct": max(abs(c["brake"]["err_J0_pct"]) for c in cases),
        "worst_peak_eta_err_pct": max(abs(c["brake"]["err_peak_eta_pct"]) for c in cases),
        "worst_brake_KT_ratio": max(abs(c["brake"]["KT_brake_ratio"]) for c in cases),
    }
    return {"summary": summary, "cases": cases}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tag", default="run")
    ap.add_argument("--mission", default="m1637")
    ap.add_argument("--sections", type=int, default=SECTIONS)
    ap.add_argument("--outdir", default=os.path.join(REPO_ROOT, "artifacts"))
    args = ap.parse_args(argv)

    payload = build(sections=args.sections)
    payload["meta"] = {
        "mission": args.mission,
        "model_path": "backend/services/propeller_open_water.py",
        "reference_path": "tools/verify/propeller_open_water/reference_bseries.py",
        "reference_provenance": "Oosterveld & van Oossanen (1975) B-series regression, "
        "as reproduced in Bernitsas/Ray/Kinley (1981) Report 237",
        "reference_validity": ref.VALIDITY,
        "regime_definition": "propeller regime 0.2 <= J <= 0.85*J0_ref; brake regime J > J0_ref",
        "note": "err_* = 100*(model-reference)/reference",
    }
    blob = json.dumps(payload, indent=2, sort_keys=True)
    outdir = os.path.join(args.outdir, args.mission)
    os.makedirs(outdir, exist_ok=True)
    path = os.path.join(outdir, f"bem_vs_bseries_{args.tag}.json")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(blob)
    payload["meta"]["artifact"] = os.path.relpath(path, REPO_ROOT)
    payload["meta"]["sha256"] = hashlib.sha256(blob.encode()).hexdigest()
    print(json.dumps(payload["summary"], indent=2, sort_keys=True))
    print("artifact:", payload["meta"]["artifact"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())