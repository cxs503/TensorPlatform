#!/usr/bin/env python3
"""Normalise backend/data/benchmark_reference.json to a self-consistent baseline.

Every derived column is *recomputed* (zero hand-copied numbers) and the as-listed
value is preserved for traceability:

  experimental_rt_n      <- Ct identity: Rt = Ct * 0.5 * rho * S * V^2
  froude_number          <- V / sqrt(g * L)   (recorded L, not an implied hull)
  wetted_surface_m2      <- measured value, else Holtrop estimate (flagged)
  fluid_density_kg_m3    <- 1000 (fresh water, model basins) / 1025 (sea water)

Run:  python3 tools/audit/normalize_benchmark_reference.py [--check]
      --check  only report what would change, do not write.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from baseline_integrity import (  # noqa: E402
    DATASET, froude, holtrop_wetted_surface, rho_for,
)

STAMP = "20261031"


def normalise(dataset: dict) -> tuple[dict, list[str]]:
    log = []
    for case in dataset["cases"]:
        geom = case.setdefault("geometry", {})
        st = case["ship_type"]
        rho = case.setdefault("fluid_density_kg_m3", rho_for(st))
        v = float(case["speed_ms"])

        if not geom.get("wetted_surface_m2"):
            s = holtrop_wetted_surface(geom["length_m"], geom["beam_m"],
                                       geom["draft_m"], geom["cb"])
            geom["wetted_surface_m2"] = round(s, 2)
            geom["wetted_surface_provenance"] = "holtrop_1982_estimate"
        s = float(geom["wetted_surface_m2"])

        old_rt = case.get("experimental_rt_n")
        new_rt = round(case["experimental_ct"] * 0.5 * rho * s * v * v, 2)
        if old_rt is None or abs(new_rt - float(old_rt)) / new_rt > 0.02:
            case.setdefault(f"experimental_rt_n_as_listed_{STAMP}", old_rt)
            case["experimental_rt_n"] = new_rt
            case["rt_provenance"] = (f"derived: Ct * 0.5 * {rho} * {s} m2 * ({v} m/s)^2")
            log.append(f"{case['case_id']}: Rt {old_rt} -> {new_rt} N "
                       f"(ratio {float(old_rt)/new_rt:.3f})" if old_rt else
                       f"{case['case_id']}: Rt set to {new_rt} N")

        old_fr = case.get("froude_number")
        new_fr = round(froude(v, float(geom["length_m"])), 3)
        if old_fr is None or abs(new_fr - float(old_fr)) / new_fr > 0.01:
            case.setdefault(f"froude_number_as_listed_{STAMP}", old_fr)
            case["froude_number"] = new_fr
            implied = (v / float(old_fr)) ** 2 / 9.81 if old_fr else None
            case["froude_provenance"] = (
                f"recomputed with recorded L={geom['length_m']} m; "
                f"as-listed value implied L={implied:.1f} m" if implied else
                f"recomputed with recorded L={geom['length_m']} m")
            log.append(f"{case['case_id']}: Fr {old_fr} -> {new_fr} "
                       f"(as-listed implied L={implied:.1f} m)" if implied else
                       f"{case['case_id']}: Fr set to {new_fr}")

    dataset.setdefault("audit", {})
    dataset["audit"] = {
        "tool": "tools/audit/baseline_integrity.py",
        "normalised_by": "tools/audit/normalize_benchmark_reference.py",
        "normalised_on": STAMP,
        "policy_max_tolerance_pct": 15.0,
        "notes": ("Rt / Fr columns recomputed from Ct identity and recorded geometry; "
                  "as-listed values preserved under *_as_listed_%s keys." % STAMP),
    }
    dataset["version"] = "1.1"
    return dataset, log


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true")
    args = ap.parse_args()
    path = Path(DATASET)
    data = json.loads(path.read_text())
    data, log = normalise(data)
    print(f"{len(log)} corrections:")
    for line in log:
        print("  " + line)
    if not args.check:
        path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n")
        print(f"written: {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
