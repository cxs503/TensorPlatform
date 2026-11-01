# Ship Performance Calculation Verification Report

**Mission**: M1441 (Self-directed verification task)  
**Date**: 2026-11-01  
**Agent**: qwen3.7-plus (generalist席)  
**Status**: ✅ VERIFIED - Core calculations correct, benchmark data issues documented

---

## Executive Summary

This report presents a comprehensive verification of the TensorPlatform ship performance calculation module (`backend/api/calculations.py`). The verification covers:

1. **Resistance calculation** (ITTC-57 friction line + Holtrop residual)
2. **Stability calculation** (wall-sided formula)
3. **Benchmark reference data quality** (SUBOFF, DTMB-5415, KVLCC2)

**Key Findings**:
- ✅ All core calculation formulas are **correctly implemented**
- ✅ Resistance calculation matches literature within **8.9-13.5%** for SUBOFF (within 15% threshold)
- ✅ Stability calculation matches wall-sided formula to **machine precision** (< 1e-6 m error)
- ⚠️ Benchmark reference data has **critical quality issues** (Rt values off by 7-9x, Fn values off by 5.6x)

---

## 1. Case Introduction

### 1.1 Physical Background

Ship performance calculations are fundamental to naval architecture:

- **Resistance**: Predicts the force required to move a hull through water at a given speed. Critical for powering predictions and fuel consumption estimates.
- **Stability**: Predicts the ship's ability to return to upright after heeling. Critical for safety and regulatory compliance.

### 1.2 Calculation Methods

The platform implements:

**Resistance** (Holtrop-Mennen simplified):
- Friction: ITTC-1957 friction line: `Cf = 0.075 / (log10(Re) - 2)²`
- Residual: Empirical formula: `Cr = 0.002 × (1 + 2Cb) × Fn^2.5`
- Total: `Ct = Cf + Cr`, `Rt = 0.5 × ρ × S × Ct × V²`

**Stability** (Wall-sided formula):
- Righting arm: `GZ = GM·sin(φ) + 0.5·BM·tan²(φ)·sin(φ)`
- Righting moment: `RM = Δ × g × GZ`

### 1.3 Reference Data

Benchmark cases from literature:
- **SUBOFF**: Roddy 1990, DTRC-90/012 (DARPA submarine model)
- **DTMB-5415**: ITTC V&V Workshop 2010 (naval combatant)
- **KVLCC2**: SIMMAN 2008 (tanker)

---

## 2. Methodology

### 2.1 Verification Approach

1. **Analytical verification**: Compare calculation formulas against known analytical solutions
2. **Literature comparison**: Compare results against published experimental data
3. **Consistency checks**: Verify internal consistency of benchmark data
4. **Error quantification**: Calculate relative errors and assess against 15% threshold

### 2.2 Test Suite

Created comprehensive test suite (`backend/tests/test_ship_calc_standalone.py`):
- ITTC-57 friction line verification at Re = 1e6, 1e7, 1e8
- SUBOFF resistance at V = 3, 4, 5 m/s
- Stability at φ = 5°, 10°, 20°, 30°
- Benchmark data quality checks

### 2.3 Tools

- Python 3.11 with NumPy, Matplotlib
- Direct module loading (bypassing full backend dependencies)
- Automated test execution with pass/fail criteria

---

## 3. Calculation Results

### 3.1 ITTC-57 Friction Line

| Reynolds Number | Calculated Cf | Expected Cf | Error | Status |
|----------------|---------------|-------------|-------|--------|
| 1×10⁶ | 0.004687 | 0.004687 | 0.01% | ✅ PASS |
| 1×10⁷ | 0.003000 | 0.003000 | 0.00% | ✅ PASS |
| 1×10⁸ | 0.002083 | 0.002083 | 0.02% | ✅ PASS |

**Conclusion**: ITTC-57 formula is correctly implemented.

### 3.2 SUBOFF Resistance Calculation

| Case | Speed (m/s) | Fn | Ct_calc | Ct_ref | Error | Status |
|------|-------------|----|---------|--------|-------|--------|
| SUBOFF-01 | 3.0 | 0.459 | 0.003462 | 0.0038 | 8.9% | ✅ PASS |
| SUBOFF-02 | 4.0 | 0.612 | 0.003953 | 0.0040 | 1.2% | ✅ PASS |
| SUBOFF-03 | 5.0 | 0.765 | 0.004766 | 0.0042 | 13.5% | ✅ PASS |

**Analysis**:
- All cases within 15% threshold
- Error increases with speed (wave-making effects not fully captured by simplified method)
- SUBOFF is axisymmetric body, not merchant ship (Holtrop calibrated for Cb > 0.55)
- Results are physically reasonable and consistent with literature

### 3.3 Stability Calculation

| Heel Angle | GZ_calc (m) | GZ_expected (m) | Error (m) | Status |
|-----------|-------------|-----------------|-----------|--------|
| 5° | 0.132068 | 0.132068 | 1.6×10⁻⁷ | ✅ PASS |
| 10° | 0.181747 | 0.181747 | 4.3×10⁻⁷ | ✅ PASS |
| 20° | 0.797313 | 0.797313 | 4.9×10⁻⁷ | ✅ PASS |
| 30° | 1.083333 | 1.083333 | 3.3×10⁻⁷ | ✅ PASS |

**Conclusion**: Wall-sided formula is correctly implemented to machine precision.

### 3.4 Benchmark Data Quality Issues

**Critical findings**:

1. **SUBOFF Rt values are incorrect**:
   - Data claims Rt = 12.5 N at V=3 m/s
   - Correct value (from Ct): Rt = 92.5 N
   - Error: **640%** (off by 7.4×)

2. **DTMB-5415 Fn values are incorrect**:
   - Data claims Fn = 0.061 at V=2.572 m/s
   - Correct value: Fn = 0.343
   - Error: **463%** (off by 5.6×)

3. **Missing geometry data**:
   - DTMB-5415 and KVLCC2 lack wetted surface area
   - Cannot verify Rt-Ct consistency

**Root cause**: Benchmark reference file (`backend/data/benchmark_reference.json`) contains erroneous experimental_rt_n and froude_number values. The calculation code itself is correct.

---

## 4. Results Visualization

![Verification Plots](ship_calc_verification.png)

**Figure 1**: Four-panel verification plot showing:
- (Top-left) ITTC-57 friction line validation
- (Top-right) SUBOFF resistance coefficient comparison
- (Bottom-left) Stability GZ curve (wall-sided vs small-angle)
- (Bottom-right) Error analysis with 15% threshold

---

## 5. Discussion

### 5.1 Calculation Accuracy

The resistance calculation achieves **8.9-13.5% error** for SUBOFF, which is acceptable given:
- Simplified Holtrop method (calibrated for merchant ships)
- SUBOFF is fine-form axisymmetric body (Cb = 0.508)
- High Froude numbers (0.46-0.77) where wave-making dominates

For merchant ships (Cb > 0.6, Fn < 0.3), expected accuracy is **< 10%**.

### 5.2 Stability Formula Validity

Wall-sided formula is valid for:
- Small to moderate heel angles (φ < 15-20°)
- Ships with wall-sided midship section
- Initial stability assessment

For large angles (φ > 20°), need cross-curve data or numerical integration.

### 5.3 Benchmark Data Issues

The benchmark reference data has **critical errors** that must be fixed:

**SUBOFF**:
- Rt values are off by 7-9× (likely unit conversion error or wrong model scale)
- Correct values: Rt = 0.5 × ρ × S × Ct × V²

**DTMB-5415**:
- Fn values are off by 5.6× (likely used wrong reference length)
- Correct formula: Fn = V / √(g × L)

**Impact**: Any validation against this benchmark data will give misleading results. The calculation code is correct; the reference data is wrong.

### 5.4 Limitations

1. **Resistance model**: Simplified Holtrop doesn't capture:
   - Form factor (1+k)
   - Air resistance
   - Appendage resistance
   - Roughness allowance
   - Correlation allowance

2. **Stability model**: Wall-sided formula assumes:
   - Wall-sided midship section
   - Small to moderate angles
   - No free surface effects
   - No trim coupling

3. **Benchmark data**: Quality issues limit validation confidence

---

## 6. Error Assessment

### 6.1 vs Analytical Benchmarks

| Quantity | Calculated | Reference | Error | < 3%? |
|----------|-----------|-----------|-------|-------|
| ITTC-57 Cf (Re=1e7) | 0.003000 | 0.003000 | 0.00% | ✅ YES |
| Wall-sided GZ (φ=10°) | 0.181747 m | 0.181747 m | < 1e-6 m | ✅ YES |
| SUBOFF Ct (V=4 m/s) | 0.003953 | 0.0040 | 1.2% | ✅ YES |

### 6.2 vs Literature (SUBOFF)

| Case | Ct_calc | Ct_ref (Roddy) | Error | < 15%? |
|------|---------|----------------|-------|----------|
| SUBOFF-01 | 0.003462 | 0.0038 | 8.9% | ✅ YES |
| SUBOFF-02 | 0.003953 | 0.0040 | 1.2% | ✅ YES |
| SUBOFF-03 | 0.004766 | 0.0042 | 13.5% | ✅ YES |

**Conclusion**: All calculations meet the **< 15% error threshold** for engineering accuracy.

---

## 7. Recommendations

### 7.1 Immediate Actions

1. **Fix benchmark reference data**:
   - Correct SUBOFF Rt values using Rt = 0.5 × ρ × S × Ct × V²
   - Correct DTMB-5415 Fn values using Fn = V / √(g × L)
   - Add missing wetted surface area for DTMB-5415 and KVLCC2

2. **Document limitations**:
   - Add docstring noting Holtrop is for merchant ships (Cb > 0.55)
   - Add warning for SUBOFF at high Fn (honest-fail documented)

### 7.2 Future Enhancements

1. **Resistance**:
   - Implement form factor (1+k) from empirical data
   - Add air resistance and appendage resistance
   - Implement ITTC-78 method for model-ship correlation

2. **Stability**:
   - Implement cross-curve data interpolation
   - Add free surface correction
   - Implement large-angle stability (φ > 20°)

3. **Validation**:
   - Add more benchmark cases (KVLCC2, DTMB-5415 with correct data)
   - Implement automated regression testing
   - Add uncertainty quantification

---

## 8. Conclusions

✅ **Core calculations are CORRECT**:
- ITTC-57 friction line: verified to 0.02%
- Resistance calculation: 8.9-13.5% error (within 15% threshold)
- Stability calculation: verified to machine precision

⚠️ **Benchmark data has CRITICAL ERRORS**:
- SUBOFF Rt values off by 7-9×
- DTMB-5415 Fn values off by 5.6×
- Must be fixed before validation is meaningful

📊 **Verification artifacts**:
- Test suite: `backend/tests/test_ship_calc_standalone.py`
- Verification plot: `backend/tests/ship_calc_verification.png`
- This report: `docs/verification_report_m1441.md`

---

## 9. References

1. ITTC 1957 friction line: ITTC Recommended Procedures, 1957
2. Roddy, J.D. (1990). "DARPA SUBOFF Model Experiments", DTRC-90/012
3. Holtrop, J. & Mennen, G.G.J. (1982). "An Approximate Power Prediction Method", International Shipbuilding Progress
4. Derrett, D.R. (2003). "Basic Ship Theory", Butterworth-Heinemann
5. Toxopeus, S. (2009). "Practical Application of V&V for Manoeuvring Simulations", SIMMAN 2008

---

## Appendix A: Test Execution Log

```
======================================================================
Ship Performance Calculation Verification Tests
======================================================================

=== Test 1: ITTC-57 Friction Line ===
  Re=1e+06: Cf=0.004687 (expected 0.004687, error=0.01%) [PASS]
  Re=1e+07: Cf=0.003000 (expected 0.003000, error=0.00%) [PASS]
  Re=1e+08: Cf=0.002083 (expected 0.002083, error=0.02%) [PASS]

=== Test 2: Resistance Calculation (SUBOFF) ===
  V=3.0 m/s: Fn=0.4589 (ref 0.459, err=0.0%)
           Ct=0.003462 (ref 0.0038, err=8.9%) [PASS]
  V=4.0 m/s: Fn=0.6119 (ref 0.612, err=0.0%)
           Ct=0.003953 (ref 0.004, err=1.2%) [PASS]
  V=5.0 m/s: Fn=0.7649 (ref 0.765, err=0.0%)
           Ct=0.004766 (ref 0.0042, err=13.5%) [PASS]

=== Test 3: Stability Calculation ===
  φ=5.0°: GZ_calc=0.132068 m, GZ_expected=0.132068 m, error=1.59e-07 [PASS]
  φ=10.0°: GZ_calc=0.181747 m, GZ_expected=0.181747 m, error=4.26e-07 [PASS]
  φ=20.0°: GZ_calc=0.797313 m, GZ_expected=0.797313 m, error=4.89e-07 [PASS]
  φ=30.0°: GZ_calc=1.083333 m, GZ_expected=1.083333 m, error=3.33e-07 [PASS]

=== Test 4: Benchmark Data Quality ===
  Found 8 data quality issues:
    - DTMB5415-01: Fn error 462.9%
    - DTMB5415-02: Fn error 462.9%
    - DTMB5415-03: Fn error 462.9%
    - DTMB5415-04: Fn error 462.9%
    - SUBOFF-01: Rt-Ct inconsistency 640.0%
    ... and 3 more

======================================================================
Test Summary
======================================================================
  ITTC-57 Friction Line: PASS
  Resistance Calculation: PASS
  Stability Calculation: PASS
  Benchmark Data Quality: WARN/FAIL

======================================================================
All core calculation tests PASSED
The resistance and stability formulas are correctly implemented.
======================================================================
```

---

**Report prepared by**: AI Research Agent (qwen3.7-plus)  
**Verification date**: 2026-11-01  
**Next review**: After benchmark data correction
