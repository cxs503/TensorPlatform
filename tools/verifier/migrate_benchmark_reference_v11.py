#!/usr/bin/env python3
"""Migrate backend/data/benchmark_reference.json v1.0 -> v1.1.

Pays the verification debt found by tools/verifier/audit_benchmark_reference.py:
the v1.0 dataset carried a ``froude_number`` column that was arithmetically
inconsistent with its own geometry for the DTMB-5415 cases, an
``experimental_rt_n`` column that violated the identity
``Rt = 0.5 * rho * V^2 * S * Ct`` by factors of 4-10, and no declaration of the
water density used.

What this migration does (nothing is copied from an external source; every new
number is *derived* from fields already in v1.0, and every derivation is
recorded in the case itself):

1. declares ``reference_density_kg_m3`` and
   ``reference_kinematic_viscosity_m2_s`` per ship type (model tests in fresh
   water: 1000 kg/m^3, 1.00e-6 m^2/s; KVLCC2 at ship scale: 1025 kg/m^3,
   1.19e-6 m^2/s) - documented approximations that make the Ct<->Rt identity and
   the ITTC-57 friction floor reproducible;
2. recomputes ``froude_number`` = V / sqrt(g * L) - exact identity;
3. rebuilds ``experimental_rt_n`` from Ct where the wetted surface is known,
   otherwise sets it to ``null`` with ``rt_status = "unavailable..."`` instead of
   publishing a fabricated number;
4. tags cases whose own geometry contradicts their speed with
   ``data_status = "withheld"`` (+ human-readable reason) so the accuracy gate
   cannot issue a 15%-error verdict on them;
5. keeps the v1.0 numbers under ``legacy_v1_0_*`` for audit traceability.

Usage::

    python tools/verifier/migrate_benchmark_reference_v11.py \\
        --input artifacts/verifier/benchmark_reference_v1.0.json \\
        --output backend/data/benchmark_reference.json
"""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from typing import Any, Dict

G = 9.81
FN_PHYS_LIMIT = 1.0
DENSITY_BY_TYPE = {
    "SUBOFF": 1000.0,      # model test, fresh water (documented approximation)
    "DTMB-5415": 1000.0,   # model test, fresh water
    "KVLCC2": 1025.0,      # ship scale, sea water
}
# Kinematic viscosity used to make the ITTC-57 friction floor reproducible
NU_BY_TYPE = {
    "SUBOFF": 1.00e-6,     # fresh water, 20 degC
    "DTMB-5415": 1.00e-6,
    "KVLCC2": 1.19e-6,     # sea water, 15 degC
}


def migrate(data: Dict[str, Any]) -> Dict[str, Any]:
    cases = []
    for case in data["cases"]:
        c = dict(case)
        geo = case.get("geometry") or {}
        ship_type = case["ship_type"]
        rho = DENSITY_BY_TYPE.get(ship_type, 1025.0)
        v = float(case["speed_ms"])
        L = geo.get("length_m")
        ct = case.get("experimental_ct")
        s = geo.get("wetted_surface_m2")

        c["reference_density_kg_m3"] = rho
        c["reference_kinematic_viscosity_m2_s"] = NU_BY_TYPE.get(ship_type, 1.19e-6)
        c["legacy_v1_0_froude_number"] = case.get("froude_number")
        c["legacy_v1_0_experimental_rt_n"] = case.get("experimental_rt_n")

        # 2. Froude number, exact identity
        fn = v / math.sqrt(G * float(L)) if L else None
        c["froude_number"] = round(fn, 4) if fn is not None else None
        c["froude_derivation"] = f"V/sqrt(g*L) with V={v} m/s, L={L} m, g={G}"

        # 4. declared usability of the *speed* column: withhold when the speed
        #    cannot be reconciled with the stated geometry, either because the
        #    published Froude number disagrees with V/sqrt(gL) or because the
        #    resulting Froude number is out of the physical range.
        fn_published = case.get("froude_number")
        fn_mismatch = (
            fn is not None and fn_published not in (None, 0)
            and abs(fn - fn_published) / fn_published > 0.005
        )
        if (fn is not None and fn > FN_PHYS_LIMIT) or fn_mismatch:
            c["data_status"] = "withheld"
            c["data_status_reason"] = (
                f"declared speed {v} m/s gives Fn={fn:.3f} on the stated length L={L} m, "
                f"but v1.0 published Fn={fn_published} which implies L="
                f"{((v / fn_published) ** 2 / G):.1f} m instead (sanity bound Fn<={FN_PHYS_LIMIT})"
                " - the speed column and the geometry appear to come from different scales, "
                "case cannot be scored against the model geometry"
            )
        else:
            c["data_status"] = "ok"
            c["data_status_reason"] = ""

        # 3. total resistance: derive when possible, otherwise withhold honestly
        if s and ct and v:
            rt = 0.5 * rho * v ** 2 * float(s) * float(ct)
            c["experimental_rt_n"] = round(rt, 3)
            c["rt_status"] = "derived_from_ct"
            c["rt_derivation"] = (
                f"Rt = 0.5*rho*V^2*S*Ct with rho={rho:.0f} kg/m^3, V={v} m/s, "
                f"S={s} m^2, Ct={ct}"
            )
        else:
            c["experimental_rt_n"] = None
            c["rt_status"] = "unavailable_no_wetted_surface"
            c["rt_derivation"] = (
                "v1.0 published an Rt that violated Rt=0.5*rho*V^2*S*Ct by a factor of "
                "4-10; the wetted surface is not part of this case, so Rt cannot be "
                "reconstructed without inventing data - withheld instead"
            )
        cases.append(c)

    out = {
        "description": data.get("description", "")
        + " | v1.1: froude_number recomputed from geometry, experimental_rt_n derived or "
          "withheld, reference density declared, unusable cases tagged data_status=withheld "
          "(audit: tools/verifier/audit_benchmark_reference.py)",
        "version": "1.1",
        "audit": {
            "tool": "tools/verifier/audit_benchmark_reference.py",
            "migration": "tools/verifier/migrate_benchmark_reference_v11.py",
            "pre_fix_snapshot": "artifacts/verifier/benchmark_reference_v1.0.json",
            "pre_fix_audit": "artifacts/verifier/benchmark_audit_prefix.json",
            "post_fix_audit": "artifacts/verifier/benchmark_audit_postfix.json",
            "findings": [
                "v1.0 experimental_rt_n violated Rt=0.5*rho*V^2*S*Ct for 12/12 cases "
                "(implied wetted surface 0.10-0.30 of the geometry-implied value)",
                "v1.0 froude_number for DTMB-5415 was consistent with L~181 m, not the "
                "stated L=5.72 m - speed column is full-scale while geometry is model-scale",
                "v1.0 did not declare the water density, so Ct<->Rt conversion was ambiguous",
                "the accuracy gate compared Ct only, so the broken Rt column never surfaced "
                "in the 15% verdict but was published verbatim by /calculations/benchmark_cases",
            ],
        },
        "cases": cases,
    }
    return out


def main() -> int:
    repo_root = Path(__file__).resolve().parents[2]
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--input", default=str(repo_root / "artifacts/verifier/benchmark_reference_v1.0.json"))
    ap.add_argument("--output", default=str(repo_root / "backend/data/benchmark_reference.json"))
    args = ap.parse_args()
    data = json.loads(Path(args.input).read_text())
    out = migrate(data)
    Path(args.output).write_text(json.dumps(out, indent=2, ensure_ascii=False) + "\n")
    print(f"wrote {args.output}: {len(out['cases'])} cases, version {out['version']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())