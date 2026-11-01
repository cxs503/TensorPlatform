#!/usr/bin/env python3
"""
Verification debt #1 · wall-sided GZ formula vs an independent exact reference
─────────────────────────────────────────────────────────────────────────────
Target   : POST /api/v1/calculations/stability  (capability ``calc.stability``, audited REAL)
Question : is the implemented wall-sided arm
               GZ_ws(phi) = sin(phi) * (GM + 0.5 * BM * tan^2(phi))
           actually REAL, and over what heel range?
Method   : compare against an exact 2-D hydrostatic solution of a rectangular
           section (volume-balanced waterline by bisection + polygon-clipped
           centroid, see tools/validation/wall_sided_gz_reference.py). The
           reference shares no algebra with the formula under test.
           The comparison is made twice: against the pure formula and against
           the live FastAPI response, so the endpoint wiring is covered too.

Outputs  : artifacts/validation/wall_sided_gz/{summary.json,sweep.csv,*png}
Exit code: 0 when every acceptance check passes.
"""
from __future__ import annotations

import csv
import importlib.util
import json
import math
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))
sys.path.insert(0, str(ROOT / "tools" / "validation"))

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

from wall_sided_gz_reference import BoxCase, exact_gz, vanishing_angle_deg, wall_sided_gz  # noqa: E402

OUT = ROOT / "artifacts" / "validation" / "wall_sided_gz"
OUT.mkdir(parents=True, exist_ok=True)

# acceptance thresholds
DOMAIN_TOL_REL = 1e-6   # closed form must equal the exact solution inside the domain
DOMAIN_TOL_ABS = 1e-9


def load_api_module():
    """Import backend/api/calculations.py without triggering the api package __init__."""
    spec = importlib.util.spec_from_file_location(
        "calculations_under_test", ROOT / "backend" / "api" / "calculations.py"
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules["calculations_under_test"] = mod
    spec.loader.exec_module(mod)
    return mod


def make_client(api):
    """Minimal FastAPI app exposing only the calculations router (avoids api/__init__ deps)."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    app = FastAPI()
    app.include_router(api.router, prefix="/calc")
    return TestClient(app)


CASES = [
    # name, B, T, D, KG   (typical values; case 1 = reference barge, case 2 = fuller/shallower, case 3 = fine/deep)
    ("barge_ref", 20.0, 8.0, 12.0, 6.0),
    ("shallow_full", 16.0, 5.0, 6.0, 3.4),
    ("deep_fine", 11.0, 6.5, 9.5, 4.6),
]

ANGLES = [0.0, 2.0, 5.0, 8.0, 10.0, 12.0, 15.0, 18.0, 20.0, 22.0, 23.0, 25.0,
          28.0, 30.0, 35.0, 40.0, 45.0, 50.0, 55.0, 60.0, 65.0, 70.0, 75.0, 80.0]


def main() -> int:
    api = load_api_module()
    rows, per_case, checks = [], {}, []
    client = make_client(api)

    for name, B, T, D, KG in CASES:
        case = BoxCase(beam_m=B, draft_m=T, depth_m=D, kg_m=KG)
        phi_de = case.deck_edge_immersion_deg
        curve = []
        for phi in ANGLES:
            ex = exact_gz(case, phi)
            gz_ws = wall_sided_gz(case.gm_m, case.bm_m, phi)
            gz_api, api_flags = math.nan, {}
            try:
                resp = client.post(
                    "/calc/calculations/stability",
                    json={
                        "beam_m": B, "draft_m": T, "depth_m": D,
                        "gm_m": case.gm_m, "bm_m": case.bm_m, "kb_m": case.kb_m,
                        "displacement_t": 1000.0, "heel_angle_deg": phi,
                    },
                )
                payload = resp.json()
                gz_api = payload["righting_arm_m"]
                api_flags = {
                    "wall_sided_valid": payload.get("wall_sided_valid"),
                    "deck_edge_immersion_deg": payload.get("deck_edge_immersion_deg"),
                }
            except Exception as exc:  # pragma: no cover - endpoint unavailable
                api_flags = {"error": repr(exc)}
            err_abs = gz_ws - ex["gz_m"]
            err_rel = err_abs / ex["gz_m"] if abs(ex["gz_m"]) > 1e-12 else 0.0
            curve.append({"heel_deg": phi, "gz_exact": ex["gz_m"], "gz_wall_sided": gz_ws,
                          "gz_api": gz_api, "err_abs": err_abs, "err_rel": err_rel,
                          "delta_area_rel": ex["delta_area_rel"],
                          "api_wall_sided_valid": api_flags.get("wall_sided_valid"),
                          "inside_domain": phi <= phi_de + 1e-9})
            rows.append(dict(case=name, beam_m=B, draft_m=T, depth_m=D, kg_m=KG,
                             gm_m=case.gm_m, bm_m=case.bm_m, phi_de_deg=phi_de,
                             heel_deg=phi, gz_exact=ex["gz_m"], gz_wall_sided=gz_ws,
                             gz_api=gz_api, err_abs=err_abs, err_rel=err_rel,
                             inside_domain=phi <= phi_de + 1e-9,
                             api_wall_sided_valid=api_flags.get("wall_sided_valid")))
        inside = [c for c in curve if c["inside_domain"]]
        outside = [c for c in curve if not c["inside_domain"]]
        max_rel_in = max(abs(c["err_rel"]) for c in inside)
        max_abs_in = max(abs(c["err_abs"]) for c in inside)
        api_in_max = max(abs(c["gz_api"] - c["gz_exact"]) for c in inside)
        per_case[name] = {
            "beam_m": B, "draft_m": T, "depth_m": D, "kg_m": KG,
            "kb_m": case.kb_m, "bm_m": case.bm_m, "gm_m": case.gm_m,
            "phi_de_deg": phi_de,
            "vanishing_angle_deg": vanishing_angle_deg(case),
            "max_abs_err_inside_domain_m": max_abs_in,
            "max_rel_err_inside_domain": max_rel_in,
            "max_abs_err_api_vs_exact_inside_domain_m": api_in_max,
            "err_at_30deg_rel": next(c["err_rel"] for c in curve if abs(c["heel_deg"] - 30.0) < 1e-9),
            "err_at_45deg_rel": next(c["err_rel"] for c in curve if abs(c["heel_deg"] - 45.0) < 1e-9),
            "err_at_60deg_rel": next(c["err_rel"] for c in curve if abs(c["heel_deg"] - 60.0) < 1e-9),
            "gz_exact_peak_m": max(c["gz_exact"] for c in curve),
            "gz_wall_sided_at_80deg_m": next(c["gz_wall_sided"] for c in curve if abs(c["heel_deg"] - 80.0) < 1e-9),
            "max_delta_area_rel": max(c["delta_area_rel"] for c in curve),
            "curve": curve,
        }
        checks.append({
            "name": f"{name}: wall-sided == exact inside domain",
            "value": max(max_rel_in, max_abs_in), "threshold": DOMAIN_TOL_REL,
            "pass": max_rel_in <= DOMAIN_TOL_REL and max_abs_in <= DOMAIN_TOL_ABS,
        })
        checks.append({
            "name": f"{name}: endpoint == exact inside domain",
            "value": api_in_max, "threshold": 1e-5,
            "pass": api_in_max <= 1e-5,
        })
        checks.append({
            "name": f"{name}: endpoint flags out-of-domain heels",
            "value": sum(1 for c in outside if c["api_wall_sided_valid"] is False),
            "threshold": len(outside),
            "pass": all(c["api_wall_sided_valid"] is False for c in outside),
        })

    with open(OUT / "sweep.csv", "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)

    summary = {
        "capability": "calc.stability",
        "endpoint": "POST /api/v1/calculations/stability",
        "formula_under_test": "GZ = sin(phi) * (GM + 0.5*BM*tan^2(phi))",
        "reference": "exact 2-D hydrostatics: volume-balanced waterline (bisection) + "
                     "polygon-clipped centroid; no shared algebra with the closed form",
        "reference_self_check_max_delta_area_rel": max(v["max_delta_area_rel"] for v in per_case.values()),
        "cases": per_case,
        "checks": checks,
        "verdict": "REAL inside the wall-sided domain (phi <= atan(2(D-T)/B)); "
                   "NOT_SUPPORTED beyond deck-edge immersion (unbounded over-prediction)",
    }
    (OUT / "summary.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False))
    make_figures(per_case)
    report(summary)
    return 0 if all(c["pass"] for c in checks) else 1


def make_figures(per_case: dict) -> None:
    fig, axes = plt.subplots(1, 2, figsize=(12.5, 4.8))
    for name, v in per_case.items():
        ph = [c["heel_deg"] for c in v["curve"]]
        axes[0].plot(ph, [c["gz_exact"] for c in v["curve"]], "-", lw=2,
                     label=f"exact 2-D ({name})")
        axes[0].plot(ph, [c["gz_wall_sided"] for c in v["curve"]], "--", lw=1.6,
                     label=f"wall-sided formula ({name})")
        axes[0].axvline(v["phi_de_deg"], color="0.55", ls=":", lw=1.0)
        axes[1].semilogy(ph, [max(abs(c["err_rel"]), 1e-16) for c in v["curve"]], "-o", ms=3, label=name)
    axes[0].set_xlabel("heel angle φ [deg]")
    axes[0].set_ylabel("righting arm GZ [m]")
    axes[0].set_title("GZ: exact 2-D hydrostatics vs wall-sided formula")
    axes[0].axvline(0, color="0.55", ls=":", lw=1.0)
    axes[0].annotate("deck-edge immersion φ_de\n(dotted verticals)", xy=(22, 0.05), fontsize=8, color="0.35")
    axes[0].legend(fontsize=7, loc="upper left")
    axes[0].grid(alpha=0.3)
    axes[1].set_xlabel("heel angle φ [deg]")
    axes[1].set_ylabel("relative error |GZ_ws - GZ_exact| / GZ_exact")
    axes[1].set_title("Closed-form error: machine precision inside domain, unbounded beyond")
    axes[1].axhline(DOMAIN_TOL_REL, color="tab:red", ls="--", lw=1.0, label="1e-6 acceptance")
    axes[1].legend(fontsize=7)
    axes[1].grid(alpha=0.3, which="both")
    fig.tight_layout()
    fig.savefig(OUT / "gz_curve_and_error.png", dpi=170)
    plt.close(fig)

    # bar chart of the quantified error at typical criteria heels
    import numpy as np

    fig, ax = plt.subplots(figsize=(7.2, 4.2))
    names = list(per_case)
    heels = ["err_at_30deg_rel", "err_at_45deg_rel", "err_at_60deg_rel"]
    labels = ["30°", "45°", "60°"]
    x = np.arange(len(names))
    width = 0.26
    for i, (h, lb) in enumerate(zip(heels, labels)):
        ax.bar(x + (i - 1) * width, [per_case[n][h] * 100 for n in names], width, label=f"φ={lb}")
    ax.set_xticks(x)
    ax.set_xticklabels(names)
    ax.set_ylabel("over-prediction of GZ [%]")
    ax.set_title("Wall-sided formula error beyond the validity domain (GZ over-predicted)")
    ax.axhline(15, color="tab:red", ls="--", lw=1.0)
    ax.text(-0.45, 17, "15% unacceptable-error line", color="tab:red", fontsize=8)
    ax.legend(fontsize=8)
    ax.grid(alpha=0.3, axis="y")
    fig.tight_layout()
    fig.savefig(OUT / "error_beyond_domain.png", dpi=170)
    plt.close(fig)


def report(summary: dict) -> None:
    print(f"reference self-check |ΔA|/A max = {summary['reference_self_check_max_delta_area_rel']:.2e}")
    for name, v in summary["cases"].items():
        print(f"\n[{name}] B={v['beam_m']} T={v['draft_m']} D={v['depth_m']} KG={v['kg_m']} "
              f"KB={v['kb_m']:.3f} BM={v['bm_m']:.3f} GM={v['gm_m']:.3f} φ_de={v['phi_de_deg']:.2f}°")
        print(f"  max|err| inside domain: abs {v['max_abs_err_inside_domain_m']:.2e} m, "
              f"rel {v['max_rel_err_inside_domain']:.2e}")
        print(f"  endpoint vs exact inside domain: max|Δ| = {v['max_abs_err_api_vs_exact_inside_domain_m']:.2e} m")
        print(f"  error beyond domain: 30° {v['err_at_30deg_rel']*100:+.1f}%, "
              f"45° {v['err_at_45deg_rel']*100:+.1f}%, 60° {v['err_at_60deg_rel']*100:+.1f}%")
        print(f"  exact GZ peak {v['gz_exact_peak_m']:.3f} m; formula at 80° = {v['gz_wall_sided_at_80deg_m']:.3f} m")
    print("\nchecks:")
    for c in summary["checks"]:
        print(f"  [{'PASS' if c['pass'] else 'FAIL'}] {c['name']}  (value={c['value']}, thr={c['threshold']})")
    print(f"\nfigures -> {OUT}")


if __name__ == "__main__":
    raise SystemExit(main())