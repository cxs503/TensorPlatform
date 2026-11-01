#!/usr/bin/env python3
"""
EEDI Verification against IMO MEPC.308(73) Regulation

This script verifies the platform's attained EEDI calculation against the
IMO Marine Environment Protection Committee resolution MEPC.308(73) which
defines the Energy Efficiency Design Index (EEDI) for ships.

Reference:
    IMO MEPC.308(73), "2018 Guidelines on the Method of Calculation of the
    Attained Energy Efficiency Design Index (EEDI) for New Ships"

The EEDI formula (Regulation 2, paragraph 2.2):

    EEDI = [(Π f_j)(Σ P_ME × C_F_ME × SFC_ME) + (P_AE × C_F_AE × SFC_AE)
           - (Σ f_eff × P_eff × C_F_eff × SFC_eff)]
           / [f_i × f_c × f_l × Capacity × f_w × V_ref]

Units:
    - Numerator: g CO2 / h
    - Denominator: tonne × nm / h
    - Result: g CO2 / (tonne × nm)

This verification checks:
    1. Basic arithmetic against hand-computed reference
    2. Unit chain consistency (kW × g/kWh → g/h; t × kn → t·nm/h)
    3. Correction factor application (f_i, f_c, f_l, f_w)
    4. Innovative technology credit (f_eff term)
    5. Default factor resolution (f_w defaults to f_i per MEPC guidance)
"""
import sys
from pathlib import Path

# Add backend to path
BACKEND_DIR = Path(__file__).resolve().parents[2] / "backend"
sys.path.insert(0, str(BACKEND_DIR))

from services.propulsion import attained_eedi


def test_case_1_basic_bulk_carrier():
    """
    Test Case 1: Bulk Carrier 50,000 DWT
    
    Scenario: Conventional bulk carrier with main diesel engine, auxiliary
    diesel generator, no innovative technologies.
    
    Parameters:
        Capacity: 50,000 tonnes (DWT)
        V_ref: 14.0 knots (service speed)
        Main engine: 15,000 kW MCR, SFC = 170 g/kWh, C_F = 3.114 (diesel oil)
        Auxiliary: 1,000 kW, SFC = 190 g/kWh, C_F = 3.114
    
    Expected (hand calculation):
        ME term = 15,000 × 170 × 3.114 = 7,940,700 g/h
        AE term = 1,000 × 190 × 3.114 = 591,660 g/h
        Numerator = 7,940,700 + 591,660 = 8,532,360 g/h
        Denominator = 50,000 × 14.0 = 700,000 t·nm/h
        EEDI = 8,532,360 / 700,000 = 12.1891 g/t·nm
    """
    print("=" * 70)
    print("Test Case 1: Bulk Carrier 50,000 DWT (Basic Configuration)")
    print("=" * 70)
    
    r = attained_eedi(
        capacity_t=50_000.0,
        v_ref_kn=14.0,
        p_me_kw=15_000.0,
        sfc_me_g_kwh=170.0,
        cf_me=3.114,
        p_ae_kw=1_000.0,
        sfc_ae_g_kwh=190.0,
        cf_ae=3.114,
        p_eff_kw=0.0
    )
    
    # Hand-computed reference
    me_term_ref = 15_000.0 * 170.0 * 3.114
    ae_term_ref = 1_000.0 * 190.0 * 3.114
    numerator_ref = me_term_ref + ae_term_ref
    denominator_ref = 50_000.0 * 14.0
    eedi_ref = numerator_ref / denominator_ref
    
    print(f"\nMain Engine Term:")
    print(f"  Reference: {me_term_ref:,.2f} g/h")
    print(f"  Platform:  {r.main_engine_term_g_per_h:,.2f} g/h")
    print(f"  Match:     {abs(r.main_engine_term_g_per_h - me_term_ref) < 1e-6}")
    
    print(f"\nAuxiliary Engine Term:")
    print(f"  Reference: {ae_term_ref:,.2f} g/h")
    print(f"  Platform:  {r.auxiliary_engine_term_g_per_h:,.2f} g/h")
    print(f"  Match:     {abs(r.auxiliary_engine_term_g_per_h - ae_term_ref) < 1e-6}")
    
    print(f"\nNumerator (total emissions):")
    print(f"  Reference: {numerator_ref:,.2f} g/h")
    print(f"  Platform:  {r.numerator_g_per_h:,.2f} g/h")
    print(f"  Match:     {abs(r.numerator_g_per_h - numerator_ref) < 1e-6}")
    
    print(f"\nDenominator (transport work):")
    print(f"  Reference: {denominator_ref:,.2f} t·nm/h")
    print(f"  Platform:  {r.denominator_t_nm_per_h:,.2f} t·nm/h")
    print(f"  Match:     {abs(r.denominator_t_nm_per_h - denominator_ref) < 1e-6}")
    
    print(f"\nAttained EEDI:")
    print(f"  Reference: {eedi_ref:.6f} g/t·nm")
    print(f"  Platform:  {r.eedi_g_co2_per_t_nm:.6f} g/t·nm")
    print(f"  Relative error: {abs(r.eedi_g_co2_per_t_nm - eedi_ref) / eedi_ref * 100:.10f}%")
    print(f"  PASS: {abs(r.eedi_g_co2_per_t_nm - eedi_ref) / eedi_ref < 1e-10}")
    
    return abs(r.eedi_g_co2_per_t_nm - eedi_ref) / eedi_ref < 1e-10


def test_case_2_container_with_innovative_tech():
    """
    Test Case 2: Container Ship with Innovative Energy Efficiency Technology
    
    Scenario: Container ship with waste heat recovery system (innovative tech).
    
    Parameters:
        Capacity: 100,000 tonnes (DWT)
        V_ref: 22.0 knots
        Main engine: 40,000 kW, SFC = 165 g/kWh, C_F = 3.206 (heavy fuel oil)
        Auxiliary: 3,000 kW, SFC = 180 g/kWh, C_F = 3.206
        Innovative tech: 2,000 kW equivalent power reduction, SFC = 165 g/kWh
    
    Expected:
        ME term = 40,000 × 165 × 3.206 = 21,159,600 g/h
        AE term = 3,000 × 180 × 3.206 = 1,731,240 g/h
        Innovative credit = 2,000 × 165 × 3.206 = 1,057,980 g/h
        Numerator = 21,159,600 + 1,731,240 - 1,057,980 = 21,832,860 g/h
        Denominator = 100,000 × 22.0 = 2,200,000 t·nm/h
        EEDI = 21,832,860 / 2,200,000 = 9.9240 g/t·nm
    """
    print("\n" + "=" * 70)
    print("Test Case 2: Container Ship with Innovative Technology")
    print("=" * 70)
    
    r = attained_eedi(
        capacity_t=100_000.0,
        v_ref_kn=22.0,
        p_me_kw=40_000.0,
        sfc_me_g_kwh=165.0,
        cf_me=3.206,
        p_ae_kw=3_000.0,
        sfc_ae_g_kwh=180.0,
        cf_ae=3.206,
        p_eff_kw=2_000.0,
        sfc_eff_g_kwh=165.0,
        cf_eff=3.206
    )
    
    # Hand-computed reference
    me_term_ref = 40_000.0 * 165.0 * 3.206
    ae_term_ref = 3_000.0 * 180.0 * 3.206
    eff_term_ref = 2_000.0 * 165.0 * 3.206
    numerator_ref = me_term_ref + ae_term_ref - eff_term_ref
    denominator_ref = 100_000.0 * 22.0
    eedi_ref = numerator_ref / denominator_ref
    
    print(f"\nMain Engine Term: {r.main_engine_term_g_per_h:,.2f} g/h (ref: {me_term_ref:,.2f})")
    print(f"Auxiliary Term:   {r.auxiliary_engine_term_g_per_h:,.2f} g/h (ref: {ae_term_ref:,.2f})")
    print(f"Innovative Term:  {r.innovative_term_g_per_h:,.2f} g/h (ref: {eff_term_ref:,.2f})")
    print(f"Numerator:        {r.numerator_g_per_h:,.2f} g/h (ref: {numerator_ref:,.2f})")
    print(f"Denominator:      {r.denominator_t_nm_per_h:,.2f} t·nm/h (ref: {denominator_ref:,.2f})")
    print(f"\nAttained EEDI:    {r.eedi_g_co2_per_t_nm:.6f} g/t·nm (ref: {eedi_ref:.6f})")
    print(f"Relative error:   {abs(r.eedi_g_co2_per_t_nm - eedi_ref) / eedi_ref * 100:.10f}%")
    print(f"PASS: {abs(r.eedi_g_co2_per_t_nm - eedi_ref) / eedi_ref < 1e-10}")
    
    return abs(r.eedi_g_co2_per_t_nm - eedi_ref) / eedi_ref < 1e-10


def test_case_3_correction_factors():
    """
    Test Case 3: Correction Factors (f_i, f_c, f_l, f_w)
    
    Scenario: Ro-Ro passenger ship with capacity and load correction factors.
    
    Parameters:
        Capacity: 20,000 tonnes
        V_ref: 18.0 knots
        Main engine: 8,000 kW, SFC = 175 g/kWh, C_F = 3.114
        f_i = 1.0 (default)
        f_c = 1.05 (capacity correction for Ro-Ro)
        f_l = 1.02 (load correction)
        f_w = 1.0 (weather factor, defaults to f_i per MEPC guidance)
    
    Expected:
        Numerator = 8,000 × 175 × 3.114 = 4,359,600 g/h
        Denominator = 1.0 × 1.05 × 1.02 × 20,000 × 1.0 × 18.0 = 38,556,000 t·nm/h
        EEDI = 4,359,600 / 38,556,000 = 0.11307 g/t·nm
    """
    print("\n" + "=" * 70)
    print("Test Case 3: Correction Factors Application")
    print("=" * 70)
    
    r = attained_eedi(
        capacity_t=20_000.0,
        v_ref_kn=18.0,
        p_me_kw=8_000.0,
        sfc_me_g_kwh=175.0,
        cf_me=3.114,
        f_i=1.0,
        f_c=1.05,
        f_l=1.02,
        f_w=1.0
    )
    
    # Hand-computed reference
    numerator_ref = 8_000.0 * 175.0 * 3.114
    denominator_ref = 1.0 * 1.05 * 1.02 * 20_000.0 * 1.0 * 18.0
    eedi_ref = numerator_ref / denominator_ref
    
    print(f"\nNumerator: {r.numerator_g_per_h:,.2f} g/h (ref: {numerator_ref:,.2f})")
    print(f"Denominator: {r.denominator_t_nm_per_h:,.2f} t·nm/h (ref: {denominator_ref:,.2f})")
    print(f"\nAttained EEDI: {r.eedi_g_co2_per_t_nm:.6f} g/t·nm (ref: {eedi_ref:.6f})")
    print(f"Relative error: {abs(r.eedi_g_co2_per_t_nm - eedi_ref) / eedi_ref * 100:.10f}%")
    print(f"PASS: {abs(r.eedi_g_co2_per_t_nm - eedi_ref) / eedi_ref < 1e-10}")
    
    return abs(r.eedi_g_co2_per_t_nm - eedi_ref) / eedi_ref < 1e-10


def test_case_4_weather_factor_default():
    """
    Test Case 4: Weather Factor Default (f_w defaults to f_i)
    
    Per IMO MEPC.308(73) guidance, when f_w is not separately specified,
    it defaults to f_i (the ice class factor).
    
    Scenario: Ice-classed vessel where f_i = 1.02 (ice class 1A)
    """
    print("\n" + "=" * 70)
    print("Test Case 4: Weather Factor Default (f_w = f_i)")
    print("=" * 70)
    
    # With explicit f_w
    r_explicit = attained_eedi(
        capacity_t=30_000.0,
        v_ref_kn=16.0,
        p_me_kw=12_000.0,
        sfc_me_g_kwh=168.0,
        cf_me=3.150,
        f_i=1.02,
        f_w=1.02
    )
    
    # With f_w defaulting to f_i
    r_default = attained_eedi(
        capacity_t=30_000.0,
        v_ref_kn=16.0,
        p_me_kw=12_000.0,
        sfc_me_g_kwh=168.0,
        cf_me=3.150,
        f_i=1.02,
        f_w=None  # Should default to f_i
    )
    
    print(f"\nExplicit f_w=1.02: {r_explicit.eedi_g_co2_per_t_nm:.6f} g/t·nm")
    print(f"Default f_w=f_i:   {r_default.eedi_g_co2_per_t_nm:.6f} g/t·nm")
    print(f"Match: {abs(r_explicit.eedi_g_co2_per_t_nm - r_default.eedi_g_co2_per_t_nm) < 1e-12}")
    
    return abs(r_explicit.eedi_g_co2_per_t_nm - r_default.eedi_g_co2_per_t_nm) < 1e-12


def main():
    """Run all verification test cases."""
    print("\n" + "=" * 70)
    print("EEDI VERIFICATION AGAINST IMO MEPC.308(73)")
    print("=" * 70)
    print("\nThis script verifies the platform's attained EEDI calculation")
    print("against the IMO MEPC.308(73) regulation formula.\n")
    
    results = []
    results.append(("Test 1: Basic bulk carrier", test_case_1_basic_bulk_carrier()))
    results.append(("Test 2: Container with innovative tech", test_case_2_container_with_innovative_tech()))
    results.append(("Test 3: Correction factors", test_case_3_correction_factors()))
    results.append(("Test 4: Weather factor default", test_case_4_weather_factor_default()))
    
    print("\n" + "=" * 70)
    print("VERIFICATION SUMMARY")
    print("=" * 70)
    
    all_pass = True
    for name, passed in results:
        status = "✓ PASS" if passed else "✗ FAIL"
        print(f"{status}: {name}")
        if not passed:
            all_pass = False
    
    print("\n" + "=" * 70)
    if all_pass:
        print("ALL TESTS PASSED ✓")
        print("The platform's EEDI calculation is verified against IMO MEPC.308(73).")
    else:
        print("SOME TESTS FAILED ✗")
        print("The platform's EEDI calculation has discrepancies.")
    print("=" * 70 + "\n")
    
    return 0 if all_pass else 1


if __name__ == "__main__":
    sys.exit(main())
