"""Cross-check tests for ice resistance (m1682 audit remediation).

These tests verify that the two defects identified in the m1682 audit have been fixed:
1. Twin breaking term uses ice characteristic length l_c (not ship length L)
2. Polar submersion term uses Lindqvist form (not B*T*Cb)

The tests also verify that the calibrated coefficients produce results within
15% of reference trial values.
"""

from __future__ import annotations

import math
import sys
from pathlib import Path

import pytest

BACKEND_DIR = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(BACKEND_DIR))

from services.digital_twin.models.resistance import lindqvist_ice_resistance
from services.polar.resistance import ice_components, characteristic_length_m
from services.polar.ships import get_ship


# ---------------------------------------------------------------------------
# Twin breaking exponent (was pinned at 2.000 by defect test)
# ---------------------------------------------------------------------------

def test_twin_breaking_exponent_is_analytical():
    """After fix: twin breaking term thickness exponent should be 1.25 (analytical).
    
    R_b = (27/64) * sigma_b * h^2 * B / l_c
    l_c ∝ h^{3/4}, so R_b ∝ h^{2 - 3/4} = h^{1.25}
    
    This test replaces the old defect-pin test that asserted exponent == 2.000.
    """
    V, L, B, T, Cb = 3.0, 180.0, 30.0, 11.0, 0.82
    sigma_c, sigma_b = 1.5e6, 0.6e6
    
    h1, h2 = 0.5, 1.0
    r1 = lindqvist_ice_resistance(V, L, B, T, Cb, 1.0, h1, sigma_c, sigma_b)
    r2 = lindqvist_ice_resistance(V, L, B, T, Cb, 1.0, h2, sigma_c, sigma_b)
    
    Rb1, Rb2 = r1["Rb_N"], r2["Rb_N"]
    assert Rb1 > 0 and Rb2 > 0, "Breaking resistance should be positive"
    
    exponent = math.log(Rb2 / Rb1) / math.log(h2 / h1)
    
    # Analytical value is 1.25, allow small numerical tolerance
    assert abs(exponent - 1.25) < 0.01, f"Breaking exponent {exponent:.3f} should be 1.25"
    
    # Also verify it's in the acceptance range [1.00, 1.30]
    assert 1.00 <= exponent <= 1.30, f"Breaking exponent {exponent:.3f} out of range [1.00, 1.30]"


def test_twin_breaking_uses_characteristic_length():
    """Verify that twin breaking term uses l_c, not L."""
    V, L, B, T, Cb = 3.0, 180.0, 30.0, 11.0, 0.82
    h = 0.8
    r = lindqvist_ice_resistance(V, L, B, T, Cb, 1.0, h, 1.5e6, 0.6e6)
    
    # Check that l_c is returned as diagnostic
    assert "l_c_m" in r, "Should return ice characteristic length"
    l_c = r["l_c_m"]
    
    # l_c should be much smaller than L for typical ice thickness
    assert l_c < L, f"l_c ({l_c:.2f} m) should be < L ({L:.1f} m)"
    assert l_c > 0, "l_c should be positive"
    
    # Verify l_c formula: l_c = (E h^3 / (12 rho g (1-nu^2)))^{1/4}
    E_ice = 5.0e9
    nu_ice = 0.3
    rho = 1025.0
    g = 9.80665
    l_c_expected = (E_ice * h**3 / (12.0 * rho * g * (1.0 - nu_ice**2)))**0.25
    
    assert abs(l_c - l_c_expected) < 0.01, f"l_c {l_c:.3f} should match formula {l_c_expected:.3f}"


# ---------------------------------------------------------------------------
# Polar submersion ratio (was 5-7%, should be 52-85%)
# ---------------------------------------------------------------------------

def test_polar_submersion_ratio_in_literature_range():
    """After fix: polar submersion should be 40-90% of total (literature: 52-85%).
    
    This test replaces the old behavior where submersion was only 5-7% of total
    due to using B*T*Cb instead of Lindqvist form B*T*(B+T)/(B+2T).
    """
    for ship_key in ["handysize_bulker", "pc6_bulker", "aframax_tanker"]:
        ship = get_ship(ship_key)
        v_ms = 3.0  # m/s
        h_m = 0.8
        sigma_mpa = 1.5
        
        comp = ice_components(ship, v_ms, h_m, sigma_mpa)
        
        total = comp["total"]
        sub = comp["submersion"]
        
        assert total > 0, f"Total resistance should be positive for {ship_key}"
        
        ratio = sub / total
        assert 0.40 <= ratio <= 0.90, (
            f"{ship_key}: submersion ratio {ratio:.3f} out of range [0.40, 0.90]"
        )


def test_polar_submersion_uses_lindqvist_form():
    """Verify that polar submersion uses Lindqvist form, not B*T*Cb."""
    ship = get_ship("handysize_bulker")
    v_ms = 3.0
    h_m = 0.8
    sigma_mpa = 1.5
    
    comp = ice_components(ship, v_ms, h_m, sigma_mpa)
    sub = comp["submersion"]
    
    # Compute expected Lindqvist form
    from services.polar.constants import RHO_WATER, G
    from services.polar.resistance import C_SUB, C_VEL
    
    B = ship.beam_m
    T = ship.draft_m
    dyn = 1.0 + C_VEL * v_ms / math.sqrt(G * h_m)
    sub_expected = C_SUB * RHO_WATER * G * h_m * B * T * (B + T) / (B + 2.0 * T) * dyn
    
    assert abs(sub - sub_expected) < 1.0, (
        f"Submersion {sub:.1f} should match Lindqvist form {sub_expected:.1f}"
    )


# ---------------------------------------------------------------------------
# Total thickness exponent (should be in [1.0, 2.0])
# ---------------------------------------------------------------------------

def test_twin_total_thickness_exponent_in_range():
    """Total ice resistance thickness exponent should be in [1.0, 2.0]."""
    V, L, B, T, Cb = 3.0, 180.0, 30.0, 11.0, 0.82
    sigma_c, sigma_b = 1.5e6, 0.6e6
    
    h1, h2 = 0.5, 1.0
    r1 = lindqvist_ice_resistance(V, L, B, T, Cb, 1.0, h1, sigma_c, sigma_b)
    r2 = lindqvist_ice_resistance(V, L, B, T, Cb, 1.0, h2, sigma_c, sigma_b)
    
    R1, R2 = r1["R_ice_N"], r2["R_ice_N"]
    assert R1 > 0 and R2 > 0
    
    exponent = math.log(R2 / R1) / math.log(h2 / h1)
    
    assert 1.0 <= exponent <= 2.0, f"Total exponent {exponent:.3f} out of range [1.0, 2.0]"


# ---------------------------------------------------------------------------
# Trial error (should be <15% for three ships)
# ---------------------------------------------------------------------------

def test_polar_trial_error_within_threshold():
    """Polar predictions should be within 15% of reference trial values."""
    # Reference trial values (synthetic for audit)
    trial_refs = {
        "handysize_bulker": 850e3,
        "pc6_bulker": 1100e3,
        "aframax_tanker": 1450e3,
    }
    
    for ship_key, trial_n in trial_refs.items():
        ship = get_ship(ship_key)
        v_ms = 5.0 * 0.514444  # 5 kn in m/s
        h_m = 0.8
        sigma_mpa = 1.5
        
        comp = ice_components(ship, v_ms, h_m, sigma_mpa)
        predicted = comp["total"]
        
        error_pct = 100.0 * (predicted - trial_n) / trial_n
        
        assert abs(error_pct) < 15.0, (
            f"{ship_key}: error {error_pct:.1f}% exceeds 15% threshold"
        )


# ---------------------------------------------------------------------------
# Monotonicity and dimensional consistency
# ---------------------------------------------------------------------------

def test_ice_resistance_monotonic_in_thickness():
    """Ice resistance should increase with thickness."""
    V, L, B, T, Cb = 3.0, 180.0, 30.0, 11.0, 0.82
    
    thicknesses = [0.3, 0.5, 0.8, 1.0, 1.5]
    resistances = []
    
    for h in thicknesses:
        r = lindqvist_ice_resistance(V, L, B, T, Cb, 1.0, h, 1.5e6, 0.6e6)
        resistances.append(r["R_ice_N"])
    
    # Check monotonicity
    for i in range(len(resistances) - 1):
        assert resistances[i+1] > resistances[i], (
            f"Resistance should increase with thickness: h={thicknesses[i]}->{thicknesses[i+1]}"
        )


def test_ice_components_dimensional_consistency():
    """All ice components should have correct dimensions [N] and reasonable magnitudes."""
    ship = get_ship("handysize_bulker")
    v_ms = 3.0
    h_m = 0.8
    sigma_mpa = 1.5
    
    comp = ice_components(ship, v_ms, h_m, sigma_mpa)
    
    # All components should be positive
    for key in ["crushing", "breaking", "submersion", "total"]:
        assert comp[key] > 0, f"{key} should be positive"
    
    # All components should be in reasonable range (1 kN - 10 MN)
    for key in ["crushing", "breaking", "submersion", "total"]:
        val = comp[key]
        assert 1e3 < val < 1e7, f"{key} = {val} out of reasonable range"
    
    # Total should equal sum of components
    total_sum = comp["crushing"] + comp["breaking"] + comp["submersion"]
    assert abs(comp["total"] - total_sum) < 1.0, "Total should equal sum of components"
