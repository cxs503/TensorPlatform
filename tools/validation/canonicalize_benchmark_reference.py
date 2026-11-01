#!/usr/bin/env python3
"""Canonicalise the ship-resistance benchmark reference dataset (v1.0 -> v3.0).

Why
---
``backend/data/benchmark_reference.json`` v1.0 shipped reference values that its
own consumers use to judge "error <= 15%".  Two of those columns turned out to be
unusable:

* SUBOFF ``experimental_rt_n`` (12.5 / 21.0 / 31.5 / 45.0 N) is inconsistent with
  the same row's ``experimental_ct`` by a factor 7.4 (no water density makes the
  identity hold).  Cross-checking first principles: Ct = 0.0038 at Re = 1.30e7
  gives Cf_ITTC57 = 0.00287, so Ct = Cf + form + wave is plausible, while 12.5 N
  would require a wetted area of 0.73 m2 (the row declares 5.42 m2).  => the Rt
  column is the bad one; Ct survives an independent friction-line test.
* DTMB-5415 rows: the recorded ``froude_number`` column implies a waterline
  length of 181.2 m for all four rows, while ``geometry.length_m`` is 5.72 m; at
  the tabulated speeds the recomputed Froude number of rows 03/04 would be
  1.37 / 2.06 (physically impossible for a displacement hull).  The family
  cannot be used for precision claims until the workshop test matrix is sourced.

What this script does (never invents a number)
----------------------------------------------
* SUBOFF: Rt recomputed from the row's own Ct by the identity
  ``Rt = Ct * 0.5 * rho * S * V^2`` with rho = 998.2 (fresh water, DTRC tank).
  Status DERIVED, provenance written per row; the v1.0 value is kept under
  ``legacy_values`` (nothing is deleted, superseded values stay auditable).
* DTMB-5415 / KVLCC2: no validated wetted area in the file -> ``rt = null`` with
  ``rt_status = WITHHELD`` (rule R1b: no invented numbers).
* Every row gets ``medium``, ``rho_kg_m3``, ``rt_source``, ``froude_number_status``
  and ``froude_number_recomputed`` so the gate in
  ``backend/services/benchmark_integrity.py`` can be run mechanically.

Usage::

    python3 tools/validation/canonicalize_benchmark_reference.py --write
    python3 tools/validation/canonicalize_benchmark_reference.py --check
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path
from typing import Any, Dict

REPO = Path(__file__).resolve().parents[2]
DATA = REPO / "backend" / "data" / "benchmark_reference.json"

G = 9.81
RHO = {"fresh": 998.2, "sea": 1025.0}
MEDIUM_BY_SHIP = {"SUBOFF": "fresh", "DTMB-5415": "fresh", "KVLCC2": "sea"}

GOVERNANCE = {
    "arbiter": "mission 1561 (report 1144) canonical arbitration + mission 1577 runtime wiring",
    "rules": [
        "R1 Rt = Ct*0.5*rho*S*V^2 (1%)",
        "R1b rows without a validated wetted area carry rt=null and rt_status=WITHHELD",
        "R2 rho from the ITTC water table consistent with the declared medium, source_citation required",
        "R3 |Fn - V/sqrt(gL)| / Fn <= 2%",
        "R5 Ct >= Cf_ITTC57(Re)",
        "R6 rt_status enum consistent with the presence of experimental_rt_n",
    ],
    "gate_implementation": "backend/services/benchmark_integrity.py",
    "honest_limits": [
        "rho=998.2 for the DTRC tank rows is inferred (fresh water + ITTC table + platform "
        "density 998.0), not a direct citation of Roddy 1990; the original water temperature "
        "and the wetted-area definition of that report could not be sourced offline.",
        "DTMB-5415 and KVLCC2 Rt stay WITHHELD: the file carries no wetted area for them and "
        "deriving one from L/B/T would be invention.",
        "DTMB-5415 speed/Froude columns conflict with the tabulated model length "
        "(recorded Fr implies L=181.2 m); the workshop test matrix (Gothenburg 2010) is required.",
    ],
}


def _froude(v: float, L: float) -> float:
    return v / math.sqrt(G * L)


def canonicalise(src: Dict[str, Any]) -> Dict[str, Any]:
    cases = []
    for case in src.get("cases", []):
        c = dict(case)
        ship = c.get("ship_type")
        medium = MEDIUM_BY_SHIP.get(ship, "fresh")
        rho = RHO[medium]
        geom = c.get("geometry") or {}
        length = geom.get("length_m")
        surface = geom.get("wetted_surface_m2")
        v = c.get("speed_ms")
        ct = c.get("experimental_ct")

        legacy = dict(c.get("legacy_values") or {})
        old_rt = c.get("experimental_rt_n")
        old_fn = c.get("froude_number")

        # ---- Froude column provenance ---------------------------------
        if length and v:
            fn_calc = _froude(v, length)
            rel = abs(fn_calc - old_fn) / fn_calc if old_fn else 1.0
            if rel <= 0.02:
                c["froude_number"] = round(fn_calc, 4)
                c["froude_number_status"] = "CONSISTENT"
            else:
                c["froude_number_status"] = "CONFLICT"
                if old_fn is not None:
                    legacy.setdefault("froude_number_v1", old_fn)
                    legacy.setdefault(
                        "froude_implied_length_m_v1", round(v ** 2 / (G * old_fn ** 2), 2)
                    )
                # do not silently overwrite: keep the recorded column, expose the recomputed one
                c["froude_number_recomputed"] = round(fn_calc, 4)
            c["froude_number_recomputed"] = round(fn_calc, 4)
        else:
            c["froude_number_status"] = "WITHHELD"

        # ---- Rt column: derive only when geometry is validated ---------
        if surface and ct and v:
            rt = ct * 0.5 * rho * surface * v ** 2
            if old_rt is not None:
                legacy.setdefault("experimental_rt_n_v1", old_rt)
            c["experimental_rt_n"] = round(rt, 4)
            c["rt_status"] = "DERIVED"
            c["rt_source"] = (
                "identity Rt = Ct*0.5*rho*S*V^2 with Ct as tabulated, S as tabulated in "
                f"geometry, rho={rho} kg/m3 ({medium} water, ITTC table)"
            )
        else:
            if old_rt is not None:
                legacy.setdefault("experimental_rt_n_v1", old_rt)
            c["experimental_rt_n"] = None
            c["rt_status"] = "WITHHELD"
            c["rt_source"] = (
                "no validated wetted area in dataset -> Rt not derivable without invention "
                "(needs the original workshop wetted-area table)"
            )

        if legacy:
            c["legacy_values"] = legacy

        c["medium"] = medium
        c["rho_kg_m3"] = rho
        cases.append(c)

    out = dict(src)
    out["version"] = "3.0"
    out["description"] = (
        "Benchmark reference dataset for ship resistance validation. Sources: Roddy 1990 "
        "(SUBOFF), ITTC V&V Workshop Gothenburg 2010 (DTMB-5415), SIMMAN 2008 (KVLCC2). "
        "v3.0: every row carries rt_status / medium / rho provenance and passes the "
        "first-principles gate in backend/services/benchmark_integrity.py; values that "
        "could not be validated are null + WITHHELD instead of invented."
    )
    out["governance"] = GOVERNANCE
    out["cases"] = cases
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--write", action="store_true", help="rewrite the dataset file")
    ap.add_argument("--check", action="store_true", help="verify the file already equals the canonical form")
    ap.add_argument("--path", default=str(DATA))
    ap.add_argument("--json", action="store_true", help="print the canonical dataset")
    args = ap.parse_args(argv)

    path = Path(args.path)
    src = json.loads(path.read_text())
    canon = canonicalise(src)

    if args.json:
        print(json.dumps(canon, ensure_ascii=False, indent=2))
    if args.write:
        path.write_text(json.dumps(canon, ensure_ascii=False, indent=2) + "\n")
        print(f"wrote {path} (version {canon['version']}, {len(canon['cases'])} cases)")
    if args.check:
        same = json.loads(path.read_text()) == canon
        print("CHECK:", "OK - file equals canonical form" if same else "DRIFT - file differs from canonical form")
        return 0 if same else 1
    if not (args.write or args.check or args.json):
        print(__doc__)
    return 0


if __name__ == "__main__":
    sys.exit(main())