#!/usr/bin/env python3
"""
literature_calculator — a *literature-faithful* candidate, used as the audit's
control implementation.

Purpose
-------
``pilots/resistance_calculator.py`` implements the same "simplified" Holtrop
form factor that generated the question bank's reference values, so running it
against the bank yields 15/15 pass with zero deviation — a tautology.  This
control candidate instead calls :mod:`tools.verifier.reference_impl`, i.e. the
Holtrop & Mennen (1982) Eq.(12) form factor and the textbook flat-plate lines.

Running it against the bank is the decisive experiment:

  * 12/15 questions (flat plate, ITTC-1957) pass — those references are sound;
  * 3/15 form-factor questions FAIL — the bank *rejects a correct
    implementation*, which proves the stored references, not the candidate, are
    wrong.

Subprocess protocol (identical to the pilot): stdin ``{"specification": {...}}``,
stdout ``{"result": {...}}``.
"""

from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))))

from tools.verifier import reference_impl as ref  # noqa: E402


def run_candidate(spec: dict) -> dict:
    formula = spec.get("formula") or spec.get("method")

    if formula == "blasius_laminar":
        return {"Cf": ref.blasius_laminar_cf(float(spec["Re_L"]))}
    if formula == "prandtl_schlichting":
        return {"Cf": ref.prandtl_schlichting_cf(float(spec["Re_L"]))}
    if formula in ("ittc_1957", "ittc57"):
        return {"Cf": ref.ittc_1957_cf(float(spec.get("Re_L") or spec["Re"]))}
    if formula == "holtrop_mennen_form_factor":
        return {
            "1+k": ref.holtrop_mennen_form_factor(
                float(spec["Cb"]), float(spec["L"]), float(spec["B"]), float(spec["T"])
            )["1+k"]
        }
    raise ValueError(f"Unknown formula: {formula}")


def main() -> None:
    raw = sys.stdin.read().strip()
    if not raw:
        print(json.dumps({"error": "No input"}))
        sys.exit(1)
    try:
        data = json.loads(raw)
        print(json.dumps({"result": run_candidate(data.get("specification", data))}))
    except Exception as exc:  # noqa: BLE001 - protocol requires an error payload
        print(json.dumps({"error": str(exc)}))
        sys.exit(1)


if __name__ == "__main__":
    main()