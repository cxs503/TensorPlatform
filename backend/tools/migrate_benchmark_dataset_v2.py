"""Migrate ``data/benchmark_reference.json`` v1.0 -> v2.0 (provenance of the fix).

Every corrected field in v2.0 is **computed** here from the row's own columns, never
hand-typed, so the correction is reproducible:

    python3 backend/tools/migrate_benchmark_dataset_v2.py [--in ...] [--out ...]

Corrections applied (justification in docs/ship-benchmark-reference-audit.md):

1. ``froude_number`` -> V / sqrt(g*L) using the row's own ``speed_ms`` and
   ``geometry.length_m``.  The filed DTMB-5415 values are 5.6x too small.
   Original kept as ``froude_number_as_filed``.
2. ``rt_n`` for rows that carry a wetted surface -> Ct * 0.5 * rho * V^2 * S.
   The filed ``experimental_rt_n`` for SUBOFF contradicts the filed Ct by 87-90 %.
   The Ct column is kept untouched (it is the quantity the platform validates
   against); only the derived towing force is recomputed, and the original is
   kept as ``rt_n_as_filed``.
3. ``geometry.cb_from_displacement`` / ``geometry.cb_implied_density_kg_m3`` added
   for every row that has displacement and L/B/T.  ``cb`` itself is NOT
   overwritten: resolving which of {cb, displacement} is wrong needs an external
   source (out of scope of an offline audit), so the row is marked
   ``inconsistent_unresolved`` instead of being silently "fixed".
4. ``reference_status`` assigned per row: ``verified`` / ``out_of_validity_range``
   (Fr > 0.5) / ``inconsistent_unresolved`` (ERROR-level contradiction left open).
"""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from typing import Any, Dict, List

G = 9.81
RHO_SEA = 1025.0
FR_MAX_PLAUSIBLE = 0.50
CB_REL_TOL = 0.03
RT_REL_TOL = 0.05

HERE = Path(__file__).resolve().parent.parent
DEFAULT_IN = HERE / "data" / "benchmark_reference.json"
DEFAULT_OUT = HERE / "data" / "benchmark_reference.json"


def audit_row(case: Dict[str, Any]) -> Dict[str, Any]:
    """Compute the corrected/derived fields and the row verdict for one case."""
    geom = dict(case.get("geometry", {}))
    speed = float(case["speed_ms"])
    length = float(geom["length_m"])
    ct = float(case["experimental_ct"])
    flags: List[str] = []

    fr = speed / math.sqrt(G * length)
    fr_filed = case.get("froude_number")
    if fr_filed is not None and abs(float(fr_filed) - fr) / fr > 0.01:
        flags.append(
            f"froude_number_as_filed({fr_filed}) contradicts V/L identity -> corrected to {round(fr, 4)}"
        )

    # towing force closure
    rt_derived = None
    surface = geom.get("wetted_surface_m2")
    rt_filed = case.get("experimental_rt_n")
    if surface:
        rt_derived = ct * 0.5 * RHO_SEA * speed ** 2 * float(surface)
        if rt_filed is not None and abs(float(rt_filed) - rt_derived) / rt_derived > RT_REL_TOL:
            flags.append(
                f"experimental_rt_n({rt_filed} N) contradicts Ct*0.5*rho*V^2*S "
                f"({round(rt_derived, 2)} N)"
            )
    elif rt_filed is not None:
        flags.append("experimental_rt_n cannot be closed without geometry.wetted_surface_m2")

    # block coefficient closure
    unresolved = False
    beam, draft = geom.get("beam_m"), geom.get("draft_m")
    mass = None
    if geom.get("displacement_t") is not None:
        mass = float(geom["displacement_t"]) * 1000.0
    elif geom.get("displacement_kg") is not None:
        mass = float(geom["displacement_kg"])
    if beam and draft and mass:
        cb_calc = mass / (RHO_SEA * length * float(beam) * float(draft))
        geom["cb_from_displacement"] = round(cb_calc, 4)
        if geom.get("cb"):
            err = abs(float(geom["cb"]) - cb_calc) / cb_calc
            geom["cb_implied_density_kg_m3"] = round(
                mass / (float(geom["cb"]) * length * float(beam) * float(draft)), 1)
            geom["displacement_implied_by_filed_cb_t"] = round(
                float(geom["cb"]) * RHO_SEA * length * float(beam) * float(draft) / 1000.0, 1)
            if err > CB_REL_TOL:
                unresolved = True
                flags.append(
                    f"cb({geom['cb']}) contradicts displacement*L*B*T (cb={round(cb_calc, 4)}, "
                    f"implied rho={geom['cb_implied_density_kg_m3']} kg/m3) - needs external source"
                )

    if fr > FR_MAX_PLAUSIBLE:
        flags.append(f"Fr={round(fr, 4)} > {FR_MAX_PLAUSIBLE}: outside towing-tank validity range")

    if unresolved:
        status = "inconsistent_unresolved"
    elif fr > FR_MAX_PLAUSIBLE:
        status = "out_of_validity_range"
    else:
        status = "verified"

    return {
        "fr": round(fr, 4),
        "fr_filed": fr_filed,
        "rt_derived": None if rt_derived is None else round(rt_derived, 2),
        "rt_filed": rt_filed,
        "geometry": geom,
        "flags": flags,
        "status": status,
    }


def migrate(data: Dict[str, Any]) -> Dict[str, Any]:
    out_cases: List[Dict[str, Any]] = []
    corrections: List[str] = []
    for case in data["cases"]:
        a = audit_row(case)
        new = dict(case)
        new["froude_number"] = a["fr"]
        new["froude_number_as_filed"] = a["fr_filed"]
        new["froude_number_source"] = "derived: V/sqrt(g*L)"
        new["experimental_ct_status"] = "externally_unverified_in_offline_audit"
        if a["rt_derived"] is not None:
            new.pop("experimental_rt_n", None)
            new["rt_n"] = a["rt_derived"]
            new["rt_n_source"] = "derived: Ct*0.5*rho*V^2*S (rho=1025 kg/m3)"
            new["rt_n_as_filed"] = a["rt_filed"]
        else:
            new["rt_n"] = a["rt_filed"]
            new["rt_n_source"] = "filed, not closable offline (no wetted_surface_m2)"
        new["geometry"] = a["geometry"]
        new["reference_status"] = a["status"]
        new["audit_flags"] = a["flags"]
        if a["flags"]:
            corrections.append(f"{case['case_id']}: " + " | ".join(a["flags"]))
        out_cases.append(new)

    return {
        "version": "2.0",
        "supersedes": data.get("version"),
        "description": data.get("description", ""),
        "audit": {
            "tool": "backend/tools/benchmark_dataset_audit.py",
            "migration": "backend/tools/migrate_benchmark_dataset_v2.py",
            "doc": "docs/ship-benchmark-reference-audit.md",
            "constants": {"g": G, "rho_sea": RHO_SEA, "nu": 1.139e-6},
            "tolerances": {"fr_rel_pct": 1.0, "rt_rel_pct": RT_REL_TOL * 100,
                           "cb_rel_pct": CB_REL_TOL * 100, "fr_max_plausible": FR_MAX_PLAUSIBLE},
            "corrections": corrections,
            "policy": (
                "reference_status=verified rows may decide PASS/FAIL; out_of_validity_range rows are "
                "kept for traceability but excluded from statistics in strict mode; "
                "inconsistent_unresolved rows hold a contradiction that cannot be resolved without an "
                "external source (cb vs displacement) and must not be used as a yardstick."
            ),
        },
        "cases": out_cases,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--in", dest="inp", default=str(DEFAULT_IN))
    ap.add_argument("--out", dest="out", default=str(DEFAULT_OUT))
    args = ap.parse_args()
    data = json.loads(Path(args.inp).read_text())
    migrated = migrate(data)
    Path(args.out).write_text(json.dumps(migrated, indent=2) + "\n")
    print(f"[migrate] {len(migrated['cases'])} cases -> {args.out} (v{migrated['version']})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())