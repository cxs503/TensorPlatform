#!/usr/bin/env python3
"""
Pilot: ITTC-1957 Friction Coefficient Calculator

This is the CANDIDATE implementation that the verifier will test.
It implements the ITTC-1957 model-ship correlation line:
    Cf = 0.075 / (log10(Re) - 2)^2

Protocol (subprocess mode):
  stdin:  {"specification": {"formula": "ittc_1957", "Re": 1e8}}
  stdout: {"result": {"Cf": 0.00217...}}

This file is the "worker" side — it does NOT have access to the question bank.
The verifier independently tests this implementation against held-out questions.
"""
from __future__ import annotations

import json
import math
import sys


def compute_ittc57_cf(Re: float) -> float:
    """
    Compute the ITTC-1957 friction coefficient.

    Cf = 0.075 / (log10(Re) - 2)^2

    Args:
        Re: Reynolds number (must be > 100 to avoid log issues)

    Returns:
        Friction coefficient Cf (dimensionless)
    """
    if Re <= 100:
        raise ValueError(f"Reynolds number too low: {Re}")
    log_Re = math.log10(Re)
    denominator = (log_Re - 2) ** 2
    if denominator == 0:
        raise ValueError(f"Singular denominator at Re={Re} (log10(Re)=2)")
    return 0.075 / denominator


def compute_blasius_cf(Re_L: float) -> float:
    """
    Compute laminar flat plate friction coefficient (Blasius).

    Cf = 1.328 / sqrt(Re_L)
    """
    if Re_L <= 0:
        raise ValueError(f"Reynolds number must be positive: {Re_L}")
    return 1.328 / math.sqrt(Re_L)


def compute_prandtl_schlichting_cf(Re_L: float) -> float:
    """
    Compute turbulent flat plate friction coefficient (Prandtl-Schlichting).

    Cf = 0.455 / (log10(Re_L))^2.58
    """
    if Re_L <= 1:
        raise ValueError(f"Reynolds number must be > 1: {Re_L}")
    log_Re = math.log10(Re_L)
    return 0.455 / (log_Re ** 2.58)


def compute_holtrop_form_factor(
    Cb: float,
    L: float,
    B: float,
    T: float,
    lcb: float = 0.0,
    Cstern: float = 0.0,
    Cm: float | None = None,
) -> float:
    """Holtrop-Mennen (1982) form factor 1+k1, full 1982 structure.

    The earlier pilot used ``0.93 + 0.487118*Cb^-0.9249*(L/B)^0.15*(B/T)^0.2*0.1``
    ("Scaled for pilot"), which mirrored the question generator's own bug and
    therefore passed the verifier 100% while being physically inadmissible
    (1+k < 1 for Cb=0.8, decreasing with Cb).
    """
    if not (0.3 < Cb < 0.95):
        raise ValueError(f"Block coefficient out of range: {Cb}")
    if L <= 0 or B <= 0 or T <= 0:
        raise ValueError("Dimensions must be positive")
    if Cm is None:
        Cm = 1.0 - 0.05 * (1.0 - Cb)
    Cp = Cb / Cm
    t_over_l = T / L
    if t_over_l > 0.05:
        c12 = t_over_l ** 0.2228446
    elif t_over_l > 0.02:
        c12 = 48.20 * (t_over_l - 0.02) ** 2.078 + 0.479948
    else:
        raise ValueError(f"T/L={t_over_l:.4f} outside Holtrop-1982 validity range")
    c13 = 1.0 + 0.93 * Cstern
    Lr = L * (1.0 - Cp + 0.0225 * lcb)
    if Lr <= 0:
        raise ValueError(f"length of run Lr={Lr:.3f} <= 0")
    return c13 * (
        0.93
        + c12 * (B / Lr) ** 0.92497 * (0.95 - Cp) ** (-0.521448)
        * (1.0 - Cp + 0.0225 * lcb) ** 0.6906
    )


def run_candidate(spec: dict) -> dict:
    """
    Run the candidate implementation with the given specification.

    Args:
        spec: Specification dict with 'formula' key and parameters

    Returns:
        Result dict with computed values
    """
    formula = spec.get("formula", spec.get("method", ""))

    if formula == "ittc_1957":
        Re = spec["Re"]
        Cf = compute_ittc57_cf(Re)
        return {"Cf": Cf}

    elif formula == "blasius_laminar":
        Re_L = spec["Re_L"]
        Cf = compute_blasius_cf(Re_L)
        return {"Cf": Cf}

    elif formula == "prandtl_schlichting":
        Re_L = spec["Re_L"]
        Cf = compute_prandtl_schlichting_cf(Re_L)
        return {"Cf": Cf}

    elif formula == "holtrop_mennen_form_factor":
        Cb = spec["Cb"]
        L = spec["L"]
        B = spec["B"]
        T = spec["T"]
        form_factor = compute_holtrop_form_factor(
            Cb, L, B, T,
            lcb=float(spec.get("lcb", 0.0)),
            Cstern=float(spec.get("Cstern", 0.0)),
            Cm=spec.get("Cm"),
        )
        return {"1+k": form_factor}

    else:
        raise ValueError(f"Unknown formula: {formula}")


def main():
    """Subprocess entry point: read spec from stdin, write result to stdout."""
    input_data = sys.stdin.read().strip()
    if not input_data:
        print(json.dumps({"error": "No input"}))
        sys.exit(1)

    try:
        data = json.loads(input_data)
        spec = data.get("specification", data)
        result = run_candidate(spec)
        print(json.dumps({"result": result}))
    except Exception as e:
        print(json.dumps({"error": str(e)}))
        sys.exit(1)


if __name__ == "__main__":
    main()
