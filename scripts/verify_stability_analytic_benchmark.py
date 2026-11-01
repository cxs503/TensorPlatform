#!/usr/bin/env python3
"""Analytic verification of the platform stability capability (GZ / IMO areas).

Benchmark: rectangular wall-sided barge (L=100, B=20, T=6, D=10, KG=5 m), for
which every hydrostatic quantity has a closed form:

    KB = T/2,  BM = B^2/(12 T),  GM = KB + BM - KG
    GZ(phi) exact by exact integration of the heeled section (see
    services/stability_reference.py), valid at all heel angles.

Two arms are audited:

  A. the closed-form wall-sided GZ used by the platform API
     (``api.calculations.calc_stability``), against the exact curve;
  B. the live stability endpoint ``POST /api/v1/calculations/stability``
     (kb/bm/gm, gz_max, vanishing angle, IMO areas), against exact values.

Outputs (relative to the repository root):
  artifacts/stability_benchmark/benchmark.json
  artifacts/stability_benchmark/gz_verification.png
  artifacts/stability_benchmark/error_envelope.png

Usage:  python scripts/verify_stability_analytic_benchmark.py [--api URL]
"""

from __future__ import annotations

import argparse
import json
import math
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from services.stability_reference import (  # noqa: E402
    BargeGeometry,
    box_hydrostatics,
    exact_gz,
    exact_gz_curve,
    imo_areas,
    validity_angles_deg,
    wall_sided_gz,
)

GEOM = BargeGeometry(length=100.0, beam=20.0, draft=6.0, depth=10.0, kg=5.0, rho=1.025)
OUT = ROOT / "artifacts" / "stability_benchmark"


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def rel_err(got: float, ref: float) -> float:
    return (got - ref) / ref * 100.0 if abs(ref) > 1e-12 else float("nan")


def post_stability(api_base: str, body: Dict[str, Any], timeout: float = 45.0,
                   retries: int = 2) -> Dict[str, Any]:
    """POST to the live stability endpoint, tolerating a loaded host."""
    url = api_base.rstrip("/") + "/calculations/stability"
    last: Dict[str, Any] = {"error": "not attempted"}
    for attempt in range(retries + 1):
        t0 = time.time()
        try:
            req = urllib.request.Request(
                url, data=json.dumps(body).encode(),
                headers={"Content-Type": "application/json"}, method="POST")
            payload = json.loads(urllib.request.urlopen(req, timeout=timeout).read())
            payload["_wall_ms"] = int((time.time() - t0) * 1000)
            payload["_attempt"] = attempt + 1
            return payload
        except Exception as exc:  # noqa: BLE001 - recorded, not raised
            last = {"error": f"{type(exc).__name__}: {exc}",
                    "_wall_ms": int((time.time() - t0) * 1000),
                    "_attempt": attempt + 1}
            time.sleep(2.0)
    return last


def wall_sided_envelope(gm: float, bm: float) -> Dict[str, Any]:
    """Where does the closed-form wall-sided GZ stay inside 3 % / 15 %?"""
    rows: List[Dict[str, float]] = []
    limits = {"3pct": None, "15pct": None}
    for i in range(0, 901):
        phi = i * 0.1
        e = exact_gz(GEOM, phi)["gz"]
        ws = wall_sided_gz(gm, bm, phi)
        if abs(e) < 1e-6:
            continue
        err = rel_err(ws, e)
        if i % 10 == 0:  # store every 1 deg
            rows.append({"phi_deg": round(phi, 3), "gz_exact": e, "gz_wall_sided": ws,
                         "rel_err_pct": err})
        for key, tol in (("3pct", 3.0), ("15pct", 15.0)):
            if limits[key] is None and abs(err) > tol:
                limits[key] = round(phi, 2)
    for label, phi in (("deck_immersion", validity_angles_deg(GEOM)["deck_immersion_deg"]),
                       ("25deg", 25.0), ("30deg", 30.0), ("40deg", 40.0), ("50deg", 50.0)):
        if phi is None:
            continue
        e = exact_gz(GEOM, phi)["gz"]
        rows.append({"phi_deg": round(phi, 3), "gz_exact": e,
                     "gz_wall_sided": wall_sided_gz(gm, bm, phi),
                     "rel_err_pct": rel_err(wall_sided_gz(gm, bm, phi), e),
                     "label": label})
    return {"first_angle_beyond_3pct_deg": limits["3pct"],
            "first_angle_beyond_15pct_deg": limits["15pct"],
            "rows": sorted(rows, key=lambda r: r["phi_deg"])}


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------

def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--api", default="http://127.0.0.1:8001/api/v1")
    ap.add_argument("--skip-live", action="store_true")
    args = ap.parse_args()

    OUT.mkdir(parents=True, exist_ok=True)
    hs = box_hydrostatics(GEOM)
    va = validity_angles_deg(GEOM)
    angles = [float(a) for a in range(0, 91, 1)]
    curve = exact_gz_curve(GEOM, angles)
    gz_pairs = [(p["phi_deg"], p["gz"]) for p in curve]
    peak = max(curve, key=lambda p: p["gz"])
    exact_areas = imo_areas(gz_pairs)
    ws_areas = imo_areas([(a, wall_sided_gz(hs["gm"], hs["bm"], a))
                          for a in [float(x) for x in range(0, 91)]])

    result: Dict[str, Any] = {
        "benchmark": "wall-sided barge stability (analytic)",
        "geometry": {"L_m": GEOM.length, "B_m": GEOM.beam, "T_m": GEOM.draft,
                     "D_m": GEOM.depth, "KG_m": GEOM.kg, "rho_t_m3": GEOM.rho,
                     "cb": 1.0, "displacement_t": GEOM.displacement_t,
                     "volume_m3": GEOM.volume, "freeboard_m": GEOM.freeboard},
        "exact_hydrostatics": hs,
        "exact_gz_max": {"gz_m": peak["gz"], "angle_deg": peak["phi_deg"]},
        "exact_vanishing_angle_deg": 90.0,
        "exact_imo_areas": exact_areas,
        "wall_sided_imo_areas": ws_areas,
        "wall_sided_imo_area_error_pct": {k: rel_err(ws_areas[k], exact_areas[k])
                                          for k in exact_areas},
        "validity_envelope": va,
        "wall_sided_envelope": wall_sided_envelope(hs["gm"], hs["bm"]),
        "live_platform": {"attempted": False},
    }

    # ---- arm B: live platform endpoint ------------------------------------
    if not args.skip_live:
        body = {"ship_type": "barge", "length": GEOM.length, "beam": GEOM.beam,
                "depth": GEOM.depth, "draft": GEOM.draft, "cb": 1.0,
                "displacement": GEOM.displacement_t, "kg": GEOM.kg,
                "heel_angle_deg": 0.0}
        live = post_stability(args.api, body)
        result["live_platform"] = {"attempted": True, "request": body, "response": live}
        if "error" not in live:
            result["live_platform"]["comparison"] = {
                "kb": {"platform": live.get("kb"), "exact": hs["kb"],
                       "err_pct": rel_err(live.get("kb", 0.0), hs["kb"])},
                "bm": {"platform": live.get("bm"), "exact": hs["bm"],
                       "err_pct": rel_err(live.get("bm", 0.0), hs["bm"])},
                "gm": {"platform": live.get("gm"), "exact": hs["gm"],
                       "err_pct": rel_err(live.get("gm", 0.0), hs["gm"])},
                "gz_max": {"platform": live.get("gz_max"), "exact": peak["gz"],
                           "err_pct": rel_err(live.get("gz_max", 0.0), peak["gz"])},
                "gz_max_angle": {"platform": live.get("gz_max_angle"),
                                 "exact": peak["phi_deg"]},
                "vanishing_angle": {"platform": live.get("vanishing_angle"),
                                    "exact": 90.0},
            }
            for key in ("area_0_30", "area_30_40", "area_0_40"):
                if key in live:
                    result["live_platform"]["comparison"][key] = {
                        "platform": live[key], "exact": exact_areas[key],
                        "err_pct": rel_err(live[key], exact_areas[key])}

    (OUT / "benchmark.json").write_text(json.dumps(result, indent=2, ensure_ascii=False))

    # ---- figures ----------------------------------------------------------
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt

        fig, ax = plt.subplots(figsize=(7.2, 4.6))
        ax.plot([p["phi_deg"] for p in curve], [p["gz"] for p in curve],
                lw=2.0, label="exact (analytic, this work)")
        ax.plot(angles, [wall_sided_gz(hs["gm"], hs["bm"], a) for a in angles],
                "--", lw=1.6, label="wall-sided formula (platform calc.stability)")
        ax.axvspan(0, va["exact_limit_deg"], color="tab:green", alpha=0.10)
        ax.axvline(va["deck_immersion_deg"], color="tab:green", lw=0.9, ls=":")
        ax.annotate(f"deck-edge immersion {va['deck_immersion_deg']:.1f}°",
                    (va["deck_immersion_deg"], 0.15), rotation=90, fontsize=7,
                    color="tab:green", ha="right", va="bottom")
        comp = result["live_platform"].get("comparison") if result["live_platform"].get("attempted") else None
        if comp and comp.get("gz_max", {}).get("platform") is not None:
            ax.plot([comp["gz_max_angle"]["platform"]], [comp["gz_max"]["platform"]],
                    "r*", ms=14, label="platform endpoint gz_max")
        ax.set_xlabel("heel angle φ [deg]"); ax.set_ylabel("righting arm GZ [m]")
        ax.set_title("Wall-sided barge L=100 B=20 T=6 D=10 KG=5 m: GZ verification")
        ax.grid(alpha=0.3); ax.legend(fontsize=8); ax.set_xlim(0, 90)
        fig.tight_layout(); fig.savefig(OUT / "gz_verification.png", dpi=150)
        plt.close(fig)

        fig, ax = plt.subplots(figsize=(7.2, 4.2))
        phis = [r["phi_deg"] for r in result["wall_sided_envelope"]["rows"]]
        errs = [abs(r["rel_err_pct"]) for r in result["wall_sided_envelope"]["rows"]]
        ax.semilogy(phis, errs, ".", ms=4, label="|error| wall-sided vs exact")
        ax.axhline(3.0, color="tab:orange", ls="--", lw=1, label="3 % acceptance")
        ax.axhline(15.0, color="tab:red", ls="--", lw=1, label="15 % acceptance")
        ax.axvline(va["exact_limit_deg"], color="tab:green", lw=1, ls=":")
        ax.set_xlabel("heel angle φ [deg]"); ax.set_ylabel("relative error [%]")
        ax.set_title("Error envelope of the closed-form wall-sided GZ")
        ax.grid(alpha=0.3, which="both"); ax.legend(fontsize=8)
        fig.tight_layout(); fig.savefig(OUT / "error_envelope.png", dpi=150)
        plt.close(fig)
        result["figures"] = ["gz_verification.png", "error_envelope.png"]
        (OUT / "benchmark.json").write_text(json.dumps(result, indent=2, ensure_ascii=False))
    except Exception as exc:  # noqa: BLE001
        result["figure_error"] = str(exc)

    print(json.dumps({k: result[k] for k in
                      ("exact_hydrostatics", "exact_gz_max", "wall_sided_imo_area_error_pct",
                       "validity_envelope")}, indent=2, default=str))
    if "comparison" in result["live_platform"]:
        print(json.dumps(result["live_platform"]["comparison"], indent=2))
    else:
        print("live platform:", result["live_platform"].get("response", "skipped"))
    print(f"\nartifacts -> {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())