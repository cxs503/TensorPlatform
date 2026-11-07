"""Benchmark cross-check against ``backend/data/benchmark_reference.json``.

The workspace already ships an adjudicated reference corpus (DTMB 5415, KVLCC2,
SUBOFF) whose own integrity gate states the rule the audit must obey:

    仅 reference_status=DERIVED 的行可参与 15% 精度判决；
    WITHHELD 行如实标注、不出判决。

So this module *reuses* the corpus instead of re-deriving references: it builds
an API payload from each case's recorded geometry, marks the adjudicable rows,
and leaves the withdrawn rows explicitly ``WITHHELD`` with the corpus' own
reason.  Nothing is invented for a case the corpus itself refuses to adjudicate.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CORPUS = REPO_ROOT / "backend" / "data" / "benchmark_reference.json"

#: The platform's own submerged-form volume for the SUBOFF lines is not stored in the
#: corpus (the record carries only L/D/S).  For L = 4.356 m the endpoint's geometry
#: closure reports and accepts ∇ = 0.699195 m³; any other value is rejected with
#: ``潜体几何不自洽``.  Scaling is affine in L^3, which is how the anchor is reused.
SUBOFF_NABLA_ANCHOR = {"length_m": 4.356, "nabla_m3": 0.699195}


def load_corpus(path: Optional[Path] = None) -> Dict[str, Any]:
    p = Path(path) if path else DEFAULT_CORPUS
    return json.loads(p.read_text(encoding="utf-8"))


def _payload_for(case: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    geo = case.get("geometry") or {}
    rho = case.get("reference_density_kg_m3", 1025.0)
    nu = case.get("reference_kinematic_viscosity_m2_s", 1.19e-6)
    if case.get("form_type") == "body_of_revolution":
        length = geo.get("length_m")
        dia = geo.get("diameter_m")
        speed = case.get("speed_ms")
        if not (length and dia and speed):
            return None
        nabla = SUBOFF_NABLA_ANCHOR["nabla_m3"] * (length / SUBOFF_NABLA_ANCHOR["length_m"]) ** 3
        return {
            "length": length, "beam": dia, "draft": dia / 2.0, "displacement": nabla,
            "speed": speed, "speed_unit": "m/s", "cb": 0.7854, "body_type": "submerged",
            "water_density": rho, "water_viscosity": nu,
        }
    length, beam, draft = geo.get("length_m"), geo.get("beam_m"), geo.get("draft_m")
    cb, speed_kn = geo.get("cb"), case.get("speed_kn")
    if not (length and beam and draft and cb and speed_kn):
        return None
    return {
        "length": length, "beam": beam, "draft": draft,
        "displacement": cb * length * beam * draft,
        "speed": speed_kn, "speed_unit": "kn", "cb": cb, "body_type": "ship",
        "water_density": rho, "water_viscosity": nu,
    }


def load_cases(path: Optional[Path] = None,
               ship_types: Optional[List[str]] = None) -> List[Dict[str, Any]]:
    """Return one entry per corpus case, flagged as adjudicable or withheld."""
    corpus = load_corpus(path)
    policy = (corpus.get("integrity_gate") or {}).get(
        "policy", "only reference_status=DERIVED rows are adjudicable")
    cases: List[Dict[str, Any]] = []
    for case in corpus.get("cases", []):
        if ship_types and case.get("ship_type") not in ship_types:
            continue
        status = case.get("reference_status", "WITHHELD")
        case_id = case["case_id"]
        if status != "DERIVED":
            cases.append({
                "case_id": case_id, "ship_type": case.get("ship_type"),
                "reference_status": status, "adjudicable": False,
                "reason": f"corpus policy: {policy}; adjudication: "
                          f"{(case.get('adjudication_reason') or '')[:180]}",
                "payload": None,
                "reference_ct": case.get("experimental_ct"),
                "reference_rt_n": case.get("experimental_rt_n"),
                "froude_number": case.get("froude_number"),
                "source": case.get("source_citation", "")[:90],
            })
            continue
        payload = _payload_for(case)
        cases.append({
            "case_id": case_id, "ship_type": case.get("ship_type"),
            "reference_status": status, "adjudicable": payload is not None,
            "reason": "" if payload else "recorded geometry insufficient to build an API payload",
            "payload": payload,
            "reference_ct": case.get("experimental_ct"),
            "reference_rt_n": case.get("experimental_rt_n"),
            "reference_surface_m2": (case.get("geometry") or {}).get("wetted_surface_m2"),
            "froude_number": case.get("froude_number"),
            "source": case.get("source_citation", "")[:90],
        })
    return cases