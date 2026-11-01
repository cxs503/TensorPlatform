#!/usr/bin/env python3
"""Raw probe of the ship platform structure / plate-stability / fatigue endpoints.

Purpose: capture the *observed* response surface (not self-reported method text)
so that every number in the validation report can be re-derived from an artifact.

Usage:  python3 tools/validation/probe_struct_fatigue.py <out.json>
No heavy computation: pure analytic endpoints, single-threaded HTTP.
"""
from __future__ import annotations

import json
import sys
import time
import urllib.error
import urllib.request

BASE = "http://127.0.0.1:8001"


def post(path: str, body: dict, timeout: float = 20.0) -> dict:
    req = urllib.request.Request(
        BASE + path,
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.load(r)


def get(path: str, timeout: float = 20.0) -> dict:
    with urllib.request.urlopen(BASE + path, timeout=timeout) as r:
        return json.load(r)


PLATE_KEYS = ("critical_stress_a", "critical_stress_b", "critical_shear",
              "buckling_coefficient", "ultimate_strength", "safety_factor_a",
              "safety_factor_b", "safety_factor_shear", "buckling_mode", "method")


def main(out: str) -> None:
    rec: dict = {"probe_started": time.strftime("%Y-%m-%dT%H:%M:%S"),
                 "plate": [], "fatigue": [], "fatigue_curves": None, "errors": []}

    def flush():
        with open(out, "w") as f:
            json.dump(rec, f, ensure_ascii=False, indent=1)

    # ---- published S-N class table as self-reported by the platform ----
    try:
        rec["fatigue_curves"] = get("/api/v1/fatigue/curves")
    except Exception as e:  # pragma: no cover - recorded, not raised
        rec["errors"].append(f"fatigue/curves: {e!r}")
    flush()

    # ---- plate buckling: aspect ratio x edge condition ----
    for a in (1.2, 2.4, 4.8, 7.2):
        for ec in ("SSSS", "CCCC", "clamped", "simply_supported"):
            body = {"a": a, "b": 2.4, "t": 0.015, "E": 206e9, "nu": 0.3,
                    "sigma_y": 235e6, "edge_condition": ec,
                    "axial_load_a": 0.0, "axial_load_b": 0.0,
                    "shear_load": 0.0, "lateral_pressure": 0.0}
            row = {"input": body, "edge_condition": ec, "a_over_b": round(a / 2.4, 4)}
            try:
                r = post("/api/v1/plate-stability/plate", body)
                for k in PLATE_KEYS:
                    row[k] = r.get(k)
                row["raw"] = r
            except Exception as e:
                row["error"] = repr(e)
                rec["errors"].append(f"plate a={a} {ec}: {e!r}")
            rec["plate"].append(row)
            flush()

    # ---- plate buckling: thickness sweep at fixed panel (post-buckling reserve) ----
    for t_mm in (10, 12, 15, 18, 20, 25, 30):
        body = {"a": 2.4, "b": 2.4, "t": t_mm / 1000.0, "E": 206e9, "nu": 0.3,
                "sigma_y": 235e6, "edge_condition": "SSSS",
                "axial_load_a": 0.0, "axial_load_b": 0.0,
                "shear_load": 0.0, "lateral_pressure": 0.0}
        row = {"input": body, "t_mm": t_mm}
        try:
            r = post("/api/v1/plate-stability/plate", body)
            for k in PLATE_KEYS:
                row[k] = r.get(k)
        except Exception as e:
            row["error"] = repr(e)
            rec["errors"].append(f"plate t={t_mm}: {e!r}")
        rec["plate"].append(row)
        flush()

    # ---- plate buckling: applied-load convention (is axial_load a stress?) ----
    for load in (0.0, 1.0, 5.0, 10.0, 29.09, 40.0):
        body = {"a": 2.4, "b": 2.4, "t": 0.015, "E": 206e9, "nu": 0.3,
                "sigma_y": 235e6, "edge_condition": "SSSS",
                "axial_load_a": load, "axial_load_b": 0.0,
                "shear_load": 0.0, "lateral_pressure": 0.0}
        row = {"input": body, "axial_load_a": load, "tag": "load_convention"}
        try:
            r = post("/api/v1/plate-stability/plate", body)
            for k in PLATE_KEYS:
                row[k] = r.get(k)
            row["raw"] = r
        except Exception as e:
            row["error"] = repr(e)
            rec["errors"].append(f"plate load={load}: {e!r}")
        rec["plate"].append(row)
        flush()

    # ---- fatigue: extract the effective S-N curve (m from log-log slope) ----
    for curve in ("BS-D", "BS-C", "BS-B", "BS-F"):
        for S in (10, 15, 20, 25, 30, 35, 40, 45, 50, 55, 60, 70, 80, 100, 120, 150):
            body = {"thickness": 15.0, "material": "steel", "stress_ranges": [S],
                    "cycles": [1000], "corrosion": False, "temperature": 20.0,
                    "sn_curve": curve}
            row = {"input": body, "sn_curve": curve, "S": S}
            try:
                r = post("/api/v1/fatigue/analyze", body)
                d = (r.get("damage_distribution") or [{}])[0]
                row["N"] = d.get("fatigue_life")
                row["damage"] = d.get("damage")
                row["raw"] = r
            except Exception as e:
                row["error"] = repr(e)
                rec["errors"].append(f"fatigue {curve} S={S}: {e!r}")
            rec["fatigue"].append(row)
            flush()

    # ---- fatigue: thickness correction + corrosion flags ----
    for t, corr in ((15.0, False), (22.0, False), (25.0, False), (40.0, False),
                    (15.0, True), (40.0, True)):
        body = {"thickness": t, "material": "steel", "stress_ranges": [80.0],
                "cycles": [1000], "corrosion": corr, "temperature": 20.0,
                "sn_curve": "BS-D"}
        row = {"input": body, "tag": "thickness_corrosion", "t": t, "corrosion": corr}
        try:
            r = post("/api/v1/fatigue/analyze", body)
            d = (r.get("damage_distribution") or [{}])[0]
            row["N"] = d.get("fatigue_life")
            row["raw"] = r
        except Exception as e:
            row["error"] = repr(e)
            rec["errors"].append(f"fatigue t={t} corr={corr}: {e!r}")
        rec["fatigue"].append(row)
        flush()

    rec["probe_finished"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    flush()
    n_pl = len(rec["plate"])
    n_fa = len(rec["fatigue"])
    print(f"done: plate={n_pl} fatigue={n_fa} errors={len(rec['errors'])} -> {out}")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "artifacts/probe_struct_fatigue.json")