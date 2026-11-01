#!/usr/bin/env python3
"""Repair the ship-resistance reference dataset (backend/data/benchmark_reference.json).

Background (mission 1452): the resistance benchmark reference file is the ground
truth behind POST /calculations/benchmark_validate.  A physical-consistency audit
(`tools/audit_resistance_validation.py`) showed all 12 cases violate at least one
invariant: Froude numbers that contradict V/sqrt(gL), total-resistance columns
that contradict their own dimensionless coefficient by factors 3.7-8.5, and a
displacement inconsistent with the tabulated block coefficient.

Repair policy — nothing is invented:
  * the dimensionless column `experimental_ct` is retained (it passes the
    ITTC-57 plausibility band and is the quantity the validator actually uses);
  * Froude numbers are recomputed from the tabulated speed and length;
  * Rt is *re-derived* from Ct where a documented wetted surface exists
    (SUBOFF: 5.42 m^2 from Roddy 1990; KVLCC2: 27,194 m^2 from SIMMAN 2008) and
    set to null where it does not (DTMB-5415), instead of keeping a number that
    is off by ~7x;
  * displacement is re-derived from Cb*L*B*T*rho where it contradicted the
    tabulated Cb (DTMB-5415);
  * cases whose speed falls outside the model-scale test envelope are flagged
    `validation_eligible: false` with an explicit reason rather than deleted;
  * every change is appended to a per-case `dataset_fixes` audit trail and a
    top-level `corrections` block, so the repair is reproducible and reversible.

Idempotent: running it twice produces the same file.
"""
from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Any, Dict, List

G = 9.81
RHO_FRESH = 998.0
RHO_SEA = 1025.0

DATASET = Path("backend/data/benchmark_reference.json")

# Documented wetted surface areas (m^2) with their literature sources.
DOCUMENTED_S = {
    "SUBOFF": 5.42,    # Roddy 1990, DTRC-90/012 bare hull
    "KVLCC2": 27194.0,  # SIMMAN 2008 KVLCC2 bare-hull geometry
}

# Model-scale Froude envelope used for validation eligibility.
FN_ENVELOPE = {"SUBOFF": 0.95, "DTMB-5415": 0.45, "KVLCC2": 0.25}


def _fix(case: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Repair one case in place, returning the list of applied corrections."""
    applied: List[Dict[str, Any]] = []
    geom = case.setdefault("geometry", {})
    ship = case["ship_type"]
    L = geom["length_m"]
    V = case["speed_ms"]
    ct = case["experimental_ct"]
    rho = RHO_SEA if L > 50 else RHO_FRESH

    # --- Froude number -----------------------------------------------------
    fn = round(V / (G * L) ** 0.5, 4)
    if case.get("froude_number") is None or abs(case["froude_number"] - fn) > 0.01:
        applied.append({
            "field": "froude_number",
            "reported": case.get("froude_number"),
            "corrected": fn,
            "reason": "Fn = V/sqrt(g*L) recomputed from tabulated speed and length",
        })
        case["froude_number"] = fn

    # --- Wetted surface ----------------------------------------------------
    s = geom.get("wetted_surface_m2") or DOCUMENTED_S.get(ship)
    if s:
        geom["wetted_surface_m2"] = s
        geom["wetted_surface_provenance"] = (
            "Roddy 1990 (SUBOFF bare hull)" if ship == "SUBOFF"
            else "SIMMAN 2008 (KVLCC2 bare hull)"
        )
    else:
        geom["wetted_surface_provenance"] = "not documented for this hull"

    # --- Displacement vs block coefficient --------------------------------
    if geom.get("cb") and geom.get("beam_m") and geom.get("draft_m"):
        cb, B, T = geom["cb"], geom["beam_m"], geom["draft_m"]
        mass_kg = cb * L * B * T * rho
        reported = geom.get("displacement_kg")
        if reported is None and geom.get("displacement_t"):
            reported = geom["displacement_t"] * 1000.0
        if reported is not None and abs(reported - mass_kg) / mass_kg > 0.02:
            applied.append({
                "field": "geometry.displacement_kg",
                "reported": reported,
                "corrected": round(mass_kg, 1),
                "reason": f"Cb*L*B*T*rho = {cb}*{L}*{B}*{T}*{rho:.0f}; "
                          f"reported value implies Cb={(reported/rho)/(L*B*T):.4f}",
            })
            geom["displacement_kg"] = round(mass_kg, 1)
            geom.pop("displacement_t", None)

    # --- Total resistance --------------------------------------------------
    if s:
        rt = round(0.5 * rho * s * V ** 2 * ct, 2)
        if case.get("experimental_rt_n") is None or \
                abs(case["experimental_rt_n"] - rt) / rt > 0.02:
            applied.append({
                "field": "experimental_rt_n",
                "reported": case.get("experimental_rt_n"),
                "corrected": rt,
                "reason": "re-derived as 0.5*rho*S*V^2*Ct from the retained Ct and "
                          f"documented S={s} m^2 (reported value rejected as "
                          "physically inconsistent with its own Ct)",
            })
            case["experimental_rt_n"] = rt
        case["rt_n_provenance"] = "derived_from_ct"
    else:
        if case.get("experimental_rt_n") is not None:
            applied.append({
                "field": "experimental_rt_n",
                "reported": case["experimental_rt_n"],
                "corrected": None,
                "reason": "no documented wetted surface for this hull; a resistance "
                          "value cannot be reconstructed without inventing one",
            })
        case["experimental_rt_n"] = None
        case["rt_n_provenance"] = "unavailable_no_wetted_surface"

    # --- Validation eligibility -------------------------------------------
    limit = FN_ENVELOPE.get(ship, 1.0)
    if case["froude_number"] > limit:
        case["validation_eligible"] = False
        case["exclusion_reason"] = (
            f"Fn={case['froude_number']} exceeds the model-scale test envelope "
            f"(Fn<={limit}) for a {L} m {ship} model; the record appears to "
            "combine full-scale speed in knots with model-scale geometry, so it "
            "cannot serve as a resistance validation point"
        )
    else:
        case["validation_eligible"] = True
        case.pop("exclusion_reason", None)

    if applied:
        case.setdefault("dataset_fixes", [])
        keys = {(f["field"], f["corrected"]) for f in case["dataset_fixes"]}
        case["dataset_fixes"].extend(f for f in applied if (f["field"], f["corrected"]) not in keys)
    return applied


def main() -> int:
    data = json.loads(DATASET.read_text())
    total = 0
    for case in data["cases"]:
        total += len(_fix(case))
    cumulative = sum(len(c.get("dataset_fixes", [])) for c in data["cases"])
    data["corrections"] = {
        "tool": "tools/fix_benchmark_reference_dataset.py",
        "audit": "tools/audit_resistance_validation.py",
        "date": date.today().isoformat(),
        "mission": 1452,
        "policy": (
            "Dimensionless Ct columns retained as cited; Froude numbers recomputed; "
            "Rt re-derived only where a documented wetted surface exists, otherwise "
            "nulled; out-of-envelope cases flagged instead of deleted. "
            "Original values recorded per case under dataset_fixes."
        ),
        "n_fixes_applied": cumulative,
    }
    DATASET.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n")
    print(f"applied {total} corrections to {len(data['cases'])} cases -> {DATASET}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())