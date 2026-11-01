#!/usr/bin/env python3
"""Repair the resistance benchmark reference dataset (v1.0 -> v1.1).

Every edit is *derived*, never guessed: each changed field is recomputed from
quantities already present in the row (speed, length, block coefficient,
density) plus one documented constant.  The original v1.0 values are preserved
in ``artifacts/benchmark_dataset_audit/benchmark_reference.orig.json`` and a
machine-readable before/after change log is embedded in the new dataset, so the
repair is auditable and reversible.

Repairs
-------
DTMB-5415 speed frame
    The v1.0 rows pair a *model-scale* hull (L = 5.72 m) with *full-scale*
    ship speeds (2.572 ... 15.433 m/s = 5 ... 30 kn), giving Froude numbers up
    to 2.06 - impossible for a displacement hull.  The model length is exactly
    the full-scale Lpp divided by 24.8252 (142.0 m / 5.72 m), so the working
    speed is recovered by Froude scaling V_m = V_s / sqrt(lambda).
``froude_number``
    Recomputed as Fr = V / sqrt(g L) for every row (v1.0 values for the
    DTMB-5415 block were off by 82 %; SUBOFF and KVLCC2 were already correct).
Hydrostatic closure
    ``displacement_kg`` (DTMB-5415) and ``displacement_t`` (KVLCC2) are set to
    the value implied by Cb * L * B * T * rho.
``experimental_rt_n``
    Recomputed from 0.5 rho S V^2 Ct where the wetted surface is stored
    (SUBOFF).  For DTCB-5415 / KVLCC2 no wetted surface is stored and the v1.0
    forces were inconsistent with the stored Ct by factors 3.7-10.0, so the
    field is *withheld* (null) rather than replaced by an unverifiable number.
"""
from __future__ import annotations

import json
import shutil
from datetime import date
from pathlib import Path
from typing import Any, Dict, List

ROOT = Path(__file__).resolve().parent.parent
DATASET = ROOT / "backend" / "data" / "benchmark_reference.json"
AUDITDIR = ROOT / "artifacts" / "benchmark_dataset_audit"
BACKUP = AUDITDIR / "benchmark_reference.orig.json"

G = 9.80665
KNOT = 0.514444
L_FULL_5415 = 142.0          # DTMB-5415 full-scale Lpp [m] (published)
RHO_FRESH = 998.2            # kg/m3, towing-tank fresh water at ~20 C
RHO_SEA = 1025.0             # kg/m3, ITTC standard seawater
NU_FRESH = 1.004e-6
NU_SEA = 1.19e-6


def fr(v: float, L: float) -> float:
    return v / (G * L) ** 0.5


def main() -> int:
    AUDITDIR.mkdir(parents=True, exist_ok=True)
    if not BACKUP.exists():
        shutil.copy2(DATASET, BACKUP)
    data = json.loads(DATASET.read_text())
    log: List[Dict[str, Any]] = []
    withheld: List[Dict[str, Any]] = []

    for case in data["cases"]:
        geo = case["geometry"]
        st = case["ship_type"]
        L = geo["length_m"]
        old = dict(case)

        # --- density / viscosity: frame-specific reference values ---------
        if st == "KVLCC2":
            case["frame"] = "full_scale"
            case["water_density_kg_m3"] = RHO_SEA
            case["kinematic_viscosity_m2_s"] = NU_SEA
        else:
            case["frame"] = "model"
            case["water_density_kg_m3"] = RHO_FRESH
            case["kinematic_viscosity_m2_s"] = NU_FRESH

        # --- DTMB-5415: restore the model-scale speed frame ----------------
        if st == "DTMB-5415":
            lam = L_FULL_5415 / L
            v_full = case["speed_ms"]
            v_model = v_full / lam ** 0.5
            case["speed_ms_full_scale"] = round(v_full, 4)
            case["scale_factor"] = round(lam, 4)
            case["speed_ms"] = round(v_model, 4)
            log.append({
                "case_id": case["case_id"], "field": "speed_ms",
                "before": v_full, "after": case["speed_ms"],
                "reason": "Fr-scaling to model frame: V_m = V_s/sqrt(L_full/L_model), "
                          f"lambda={lam:.4f}",
            })

        # --- Froude number: single definition ------------------------------
        fr_new = round(fr(case["speed_ms"], L), 4)
        if old.get("froude_number") != fr_new:
            log.append({
                "case_id": case["case_id"], "field": "froude_number",
                "before": old.get("froude_number"), "after": fr_new,
                "reason": "Fr = V/sqrt(g*L) with the working-frame speed",
            })
        case["froude_number"] = fr_new

        # --- hydrostatic closure -------------------------------------------
        rho = case["water_density_kg_m3"]
        if "displacement_t" in geo:
            m_new = round(L * geo["beam_m"] * geo["draft_m"] * geo["cb"] * rho / 1000.0, 1)
            if geo["displacement_t"] != m_new:
                log.append({
                    "case_id": case["case_id"], "field": "geometry.displacement_t",
                    "before": geo["displacement_t"], "after": m_new,
                    "reason": "Cb*L*B*T*rho closure (implied Cb was "
                              f"{geo['displacement_t']*1000/rho/(L*geo['beam_m']*geo['draft_m']):.4f} "
                              f"vs stored {geo['cb']})",
                })
            geo["displacement_t"] = m_new
        if "displacement_kg" in geo:
            m_new = round(L * geo["beam_m"] * geo["draft_m"] * geo["cb"] * rho, 1)
            if geo["displacement_kg"] != m_new:
                log.append({
                    "case_id": case["case_id"], "field": "geometry.displacement_kg",
                    "before": geo["displacement_kg"], "after": m_new,
                    "reason": "Cb*L*B*T*rho closure (implied Cb was "
                              f"{geo['displacement_kg']/rho/(L*geo['beam_m']*geo['draft_m']):.4f} "
                              f"vs stored {geo['cb']})",
                })
            geo["displacement_kg"] = m_new

        # --- total resistance: recompute or withhold ------------------------
        V = case["speed_ms"]
        ct = case["experimental_ct"]
        S = geo.get("wetted_surface_m2")
        if S:
            rt_new = round(0.5 * rho * S * V * V * ct, 2)
            log.append({
                "case_id": case["case_id"], "field": "experimental_rt_n",
                "before": case.get("experimental_rt_n"), "after": rt_new,
                "reason": "0.5*rho*S*V^2*Ct with stored wetted surface",
            })
            case["experimental_rt_n"] = rt_new
            case["experimental_rt_source"] = "derived: 0.5*rho*S*V^2*Ct"
        else:
            if case.get("experimental_rt_n") is not None:
                implied_s = case["experimental_rt_n"] / (0.5 * rho * V * V * ct)
                withheld.append({
                    "case_id": case["case_id"], "field": "experimental_rt_n",
                    "removed_value": case["experimental_rt_n"],
                    "implied_wetted_surface_m2": round(implied_s, 1),
                    "reason": "no wetted surface stored; removed value implies an "
                              "unphysical wetted area - closure unverifiable",
                })
            case["experimental_rt_n"] = None
            case["experimental_rt_source"] = (
                "withheld: wetted surface absent from geometry, stored v1.0 force "
                "inconsistent with experimental_ct - see integrity.withheld"
            )

        case["provenance"] = {
            "experimental_ct": "as-published (see source_citation)",
            "froude_number": "derived: V/sqrt(g*L)",
            "speed_ms": ("derived: Fr-scaled from full-scale knots"
                         if st == "DTMB-5415" else "as-published"),
            "displacement": "derived: Cb*L*B*T*rho" if st != "SUBOFF" else "as-published",
            "experimental_rt_n": case["experimental_rt_source"],
        }
        case["usable_for_validation"] = True

    data["version"] = "1.1"
    data["integrity"] = {
        "audited_at": str(date.today()),
        "auditor": "tp-arch-1 / agent-m1509",
        "method": "scripts/audit_benchmark_dataset.py",
        "reference_constants": {
            "g": G, "knot_ms": KNOT, "dtmb5415_full_scale_Lpp_m": L_FULL_5415,
            "rho_fresh": RHO_FRESH, "rho_sea": RHO_SEA,
        },
        "v1_0_defects": [
            "DTMB-5415 rows stored full-scale ship speeds against model-scale geometry "
            "(Fr up to 2.06, physically impossible)",
            "stored froude_number fields of the DTMB-5415 block were 82 % below "
            "V/sqrt(gL)",
            "stored displacement of DTMB-5415 (466.3 kg) and KVLCC2 (307000 t) did not "
            "close with Cb*L*B*T*rho",
            "stored experimental_rt_n values were inconsistent with the stored "
            "experimental_ct by factors 3.7-10.0 (derived quantity hand-stored)",
        ],
        "withheld": withheld,
        "change_log": log,
    }
    DATASET.write_text(json.dumps(data, indent=2) + "\n")
    print(f"repaired {len(data['cases'])} cases, {len(log)} field edits, "
          f"{len(withheld)} withheld")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())