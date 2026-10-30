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


def compute_holtrop_form_factor(Cb: float, L: float, B: float, T: float) -> float:
    """
    Compute Holtrop-Mennen form factor (1+k).

    Simplified formula:
      1+k ≈ 0.93 + 0.487118 * Cb^(-0.9249) * (L/B)^0.15 * (B/T)^0.2 * ...

    This is a simplified version for pilot verification.
    """
    if not (0.3 < Cb < 0.95):
        raise ValueError(f"Block coefficient out of range: {Cb}")
    if L <= 0 or B <= 0 or T <= 0:
        raise ValueError("Dimensions must be positive")

    # Simplified Holtrop form factor
    term1 = 0.93
    term2 = 0.487118 * (Cb ** (-0.9249))
    term3 = (L / B) ** 0.15
    term4 = (B / T) ** 0.2

    return term1 + term2 * term3 * term4 * 0.1  # Scaled for pilot


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
        form_factor = compute_holtrop_form_factor(Cb, L, B, T)
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
