# Resistance Calculation Benchmark Verification Report

**Mission**: m1414 (selfstart tp-qa-1 verifier)
**Date**: 2026-11-01
**Report ID**: 1012

## Executive Summary

Comprehensive verification of the platform's resistance calculation (`backend/api/calculations.py`) against experimental benchmark data for three hull types: SUBOFF (axisymmetric body), DTMB-5415 (surface combatant), and KVLCC2 (VLCC tanker).

**Key Finding**: The wetted surface formula `S = π·B·L·(0.5 + 0.25·Cb)` is calibrated for **axisymmetric bodies** (submarines, torpedoes) and produces systematic errors of 50-600% when applied to ship hulls.

## Results Summary

| Hull Type | Cases | Pass (≤15%) | Fail | Avg Error | Status |
|-----------|-------|-------------|------|-----------|--------|
| SUBOFF | 4 | 3 | 1 | 13.9% | Partially valid (Fr < 0.92) |
| DTMB-5415 | 4 | 1 | 3 | 215.1% | Invalid (wrong S formula) |
| KVLCC2 | 4 | 0 | 4 | 53.3% | Invalid (wrong S formula) |
| **Total** | **12** | **4** | **8** | **94.1%** | **Documented limitation** |

## Root Cause

The axisymmetric wetted surface formula overestimates S for ship hulls:
- KVLCC2: Formula gives S ≈ 38,800 m²; actual S ≈ 27,000 m² (43% overestimate)
- This directly causes Ct underestimation by ~50%

## Deliverables

1. **Test file**: `backend/tests/test_resistance_benchmark_verification.py` (22 tests, all passing)
2. **Figure generator**: `generate_verification_figures.py`
3. **Figures**: `outputs/ct_vs_fn_comparison.png`, `outputs/error_vs_fn.png`, `outputs/pass_fail_summary.png`
4. **Platform report**: ID 1012

## Recommendation

Replace the axisymmetric wetted surface formula with hull-type-appropriate formulas:
- For merchant ships: Holtrop's empirical S formula
- For fine-form hulls: Appropriate ship hull S estimation
- Or: Use actual geometry from offsets/CFD mesh

## Tests Run

```
pytest backend/tests/test_resistance_benchmark_verification.py -v --noconftest
22 passed in 0.28s
```
