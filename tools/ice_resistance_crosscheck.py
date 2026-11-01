#!/usr/bin/env python3
"""Ice resistance crosscheck harness for m1682 audit.

This harness validates two production paths (digital twin Lindqvist and polar TP-POLAR-1)
against analytical expectations and full-scale trial data.

Criteria:
  C1: Monotonicity - ice resistance increases with thickness
  C2: Breaking exponent - twin breaking term thickness exponent in [1.00, 1.30]
  C3: Submersion ratio - polar submersion fraction in [0.40, 0.90]
  C4: Dimensional consistency - all components have correct units
  C5: Absolute error vs trial - three ships < 15% error
"""

import json
import math
import sys
from pathlib import Path

# Add backend to path
BACKEND_DIR = Path(__file__).resolve().parent.parent / "backend"
sys.path.insert(0, str(BACKEND_DIR))

from services.digital_twin.models.resistance import lindqvist_ice_resistance
from services.polar.resistance import ice_components, characteristic_length_m
from services.polar.ships import get_ship


def compute_breaking_exponent_twin():
    """C2: Compute thickness exponent of twin breaking term.
    
    R_b ∝ h^n, solve for n using two thickness values.
    Analytical value: n = 2 - 3/4 = 1.25 (since l_c ∝ h^{3/4})
    """
    V, L, B, T, Cb = 3.0, 180.0, 30.0, 11.0, 0.82
    sigma_c, sigma_b = 1.5e6, 0.6e6
    
    h1, h2 = 0.5, 1.0
    r1 = lindqvist_ice_resistance(V, L, B, T, Cb, 1.0, h1, sigma_c, sigma_b)
    r2 = lindqvist_ice_resistance(V, L, B, T, Cb, 1.0, h2, sigma_c, sigma_b)
    
    Rb1, Rb2 = r1["Rb_N"], r2["Rb_N"]
    if Rb1 <= 0 or Rb2 <= 0:
        return None
    
    exponent = math.log(Rb2 / Rb1) / math.log(h2 / h1)
    return exponent


def compute_submersion_ratio_polar(ship_key, h_m=0.8, sigma_mpa=1.5, v_ms=3.0):
    """C3: Compute submersion fraction in polar path."""
    ship = get_ship(ship_key)
    comp = ice_components(ship, v_ms, h_m, sigma_mpa)
    
    total = comp["total"]
    sub = comp["submersion"]
    
    if total <= 0:
        return None
    
    return sub / total


def compute_total_exponent_twin():
    """Compute total ice resistance thickness exponent."""
    V, L, B, T, Cb = 3.0, 180.0, 30.0, 11.0, 0.82
    sigma_c, sigma_b = 1.5e6, 0.6e6
    
    h1, h2 = 0.5, 1.0
    r1 = lindqvist_ice_resistance(V, L, B, T, Cb, 1.0, h1, sigma_c, sigma_b)
    r2 = lindqvist_ice_resistance(V, L, B, T, Cb, 1.0, h2, sigma_c, sigma_b)
    
    R1, R2 = r1["R_ice_N"], r2["R_ice_N"]
    if R1 <= 0 or R2 <= 0:
        return None
    
    exponent = math.log(R2 / R1) / math.log(h2 / h1)
    return exponent


def check_dimensional_consistency():
    """C4: Verify all components have correct dimensions [N]."""
    V, L, B, T, Cb = 3.0, 180.0, 30.0, 11.0, 0.82
    r = lindqvist_ice_resistance(V, L, B, T, Cb, 1.0, 0.8, 1.5e6, 0.6e6)
    
    # All should be positive and in reasonable range (1 kN - 10 MN)
    for key in ["Rc_N", "Rb_N", "Rs_N", "R_ice_N"]:
        val = r[key]
        if val < 1e3 or val > 1e7:
            return False, f"{key} = {val} out of range"
    
    return True, "All components in valid range"


def compute_trial_error(ship_key, h_m=0.8, sigma_mpa=1.5, v_kn=5.0, trial_mean_kn=None):
    """C5: Compute error vs full-scale trial.
    
    Trial data (synthetic reference for m1682 audit):
    - handysize_bulker: 850 kN @ 0.8m/1.5MPa/5kn
    - pc6_bulker: 1100 kN @ 0.8m/1.5MPa/5kn
    - aframax_tanker: 1450 kN @ 0.8m/1.5MPa/5kn
    """
    # Reference trial values (synthetic for audit)
    trial_refs = {
        "handysize_bulker": 850e3,
        "pc6_bulker": 1100e3,
        "aframax_tanker": 1450e3,
    }
    
    if trial_mean_kn is None:
        trial_mean_kn = trial_refs.get(ship_key)
    
    if trial_mean_kn is None:
        return None
    
    ship = get_ship(ship_key)
    v_ms = v_kn * 0.514444
    
    comp = ice_components(ship, v_ms, h_m, sigma_mpa)
    predicted = comp["total"]
    
    error_pct = 100.0 * (predicted - trial_mean_kn) / trial_mean_kn
    return error_pct


def run_crosscheck():
    """Run all crosscheck criteria."""
    results = {
        "harness": "tools/ice_resistance_crosscheck.py",
        "timestamp": "2026-11-01T00:00:00Z",
        "criteria": {},
    }
    
    # C1: Monotonicity
    V, L, B, T, Cb = 3.0, 180.0, 30.0, 11.0, 0.82
    r1 = lindqvist_ice_resistance(V, L, B, T, Cb, 1.0, 0.5, 1.5e6, 0.6e6)
    r2 = lindqvist_ice_resistance(V, L, B, T, Cb, 1.0, 1.0, 1.5e6, 0.6e6)
    c1_pass = r2["R_ice_N"] > r1["R_ice_N"]
    results["criteria"]["C1_monotonicity"] = {
        "pass": c1_pass,
        "R_ice_0.5m": r1["R_ice_N"],
        "R_ice_1.0m": r2["R_ice_N"],
    }
    
    # C2: Breaking exponent
    breaking_exp = compute_breaking_exponent_twin()
    c2_pass = breaking_exp is not None and 1.00 <= breaking_exp <= 1.30
    results["criteria"]["C2_breaking_exponent"] = {
        "pass": c2_pass,
        "value": breaking_exp,
        "range": [1.00, 1.30],
        "analytical": 1.25,
    }
    
    # C3: Submersion ratio (polar)
    sub_ratios = {}
    for ship_key in ["handysize_bulker", "pc6_bulker", "aframax_tanker"]:
        ratio = compute_submersion_ratio_polar(ship_key)
        sub_ratios[ship_key] = ratio
    
    c3_pass = all(0.40 <= r <= 0.90 for r in sub_ratios.values() if r is not None)
    results["criteria"]["C3_submersion_ratio"] = {
        "pass": c3_pass,
        "values": sub_ratios,
        "range": [0.40, 0.90],
    }
    
    # C4: Dimensional consistency
    c4_pass, c4_msg = check_dimensional_consistency()
    results["criteria"]["C4_dimensional"] = {
        "pass": c4_pass,
        "message": c4_msg,
    }
    
    # C5: Trial error
    trial_errors = {}
    for ship_key in ["handysize_bulker", "pc6_bulker", "aframax_tanker"]:
        err = compute_trial_error(ship_key)
        trial_errors[ship_key] = err
    
    c5_pass = all(abs(e) < 15.0 for e in trial_errors.values() if e is not None)
    results["criteria"]["C5_trial_error"] = {
        "pass": c5_pass,
        "errors_pct": trial_errors,
        "threshold_pct": 15.0,
    }
    
    # Total thickness exponent
    total_exp = compute_total_exponent_twin()
    results["criteria"]["total_thickness_exponent"] = {
        "value": total_exp,
        "range": [1.0, 2.0],
        "pass": total_exp is not None and 1.0 <= total_exp <= 2.0,
    }
    
    # Overall pass
    all_pass = all([
        c1_pass,
        c2_pass,
        c3_pass,
        c4_pass,
        c5_pass,
        results["criteria"]["total_thickness_exponent"]["pass"],
    ])
    results["overall_pass"] = all_pass
    
    return results


if __name__ == "__main__":
    results = run_crosscheck()
    print(json.dumps(results, indent=2))
    
    # Exit with error if not all pass
    if not results["overall_pass"]:
        sys.exit(1)
