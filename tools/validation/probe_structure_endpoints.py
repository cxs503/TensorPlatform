#!/usr/bin/env python3
"""Probe the `/structure/plate` and `/structure/analyze` endpoints.

Records the response surface so the audit can distinguish "real formula" from
"hard-coded constant" by varying one input at a time (OAT design).

Usage: python3 tools/validation/probe_structure_endpoints.py <out.json>
"""
from __future__ import annotations

import json
import sys
import time
import urllib.request

BASE = "http://127.0.0.1:8001"
SHIP = {"L": 180.0, "B": 30.0, "T": 10.5, "D": 16.0, "displacement": 45000.0,
        "Cb": 0.82, "frame_spacing": 0.6, "plate_thickness": 0.015,
        "yield_stress": 235.0}


def post(path: str, body: dict, timeout: float = 60.0) -> dict:
    req = urllib.request.Request(BASE + path, data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"},
                                 method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.load(r)


def main(out: str) -> None:
    rec = {"probe_started": time.strftime("%Y-%m-%dT%H:%M:%S"),
           "base": SHIP, "plate": [], "analyze": [], "errors": []}

    def flush():
        with open(out, "w") as f:
            json.dump(rec, f, ensure_ascii=False, indent=1)

    # OAT: plate_thickness, frame_spacing, yield_stress
    cases = []
    for t in (0.010, 0.015, 0.020, 0.030):
        cases.append({"var": "plate_thickness", "value": t, "plate_thickness": t})
    for fs in (0.6, 0.9, 1.2, 2.4):
        cases.append({"var": "frame_spacing", "value": fs, "frame_spacing": fs})
    for sy in (235.0, 315.0, 355.0):
        cases.append({"var": "yield_stress", "value": sy, "yield_stress": sy})
    for c in cases:
        body = dict(SHIP)
        body[c["var"]] = c["value"]
        row = {"input": body, "var": c["var"], "value": c["value"]}
        try:
            row["resp"] = post("/api/v1/structure/plate", body)
        except Exception as e:
            row["error"] = repr(e)
            rec["errors"].append(f"structure/plate {c}: {e!r}")
        rec["plate"].append(row)
        flush()

    # OAT: hull girder stress vs L, B, T, D, displacement
    acases = []
    for L in (90.0, 120.0, 180.0, 240.0, 360.0):
        acases.append({"var": "L", "value": L, "L": L})
    for Bm in (24.0, 30.0, 40.0, 50.0):
        acases.append({"var": "B", "value": Bm, "B": Bm})
    for T in (8.0, 10.5, 13.0, 16.0):
        acases.append({"var": "T", "value": T, "T": T})
    for D in (12.0, 16.0, 20.0, 24.0):
        acases.append({"var": "D", "value": D, "D": D})
    for disp in (20000.0, 45000.0, 90000.0):
        acases.append({"var": "displacement", "value": disp, "displacement": disp})
    for c in acases:
        body = dict(SHIP)
        body[c["var"]] = c["value"]
        row = {"input": body, "var": c["var"], "value": c["value"]}
        t0 = time.time()
        try:
            row["resp"] = post("/api/v1/structure/analyze", body, timeout=90.0)
            row["elapsed_s"] = round(time.time() - t0, 3)
        except Exception as e:
            row["error"] = repr(e)
            row["elapsed_s"] = round(time.time() - t0, 3)
            rec["errors"].append(f"structure/analyze {c}: {e!r}")
        rec["analyze"].append(row)
        flush()

    rec["probe_finished"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    flush()
    print(f"done: plate={len(rec['plate'])} analyze={len(rec['analyze'])} "
          f"errors={len(rec['errors'])} -> {out}")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "artifacts/probe_structure_endpoints.json")