# P350W1-N07: Cross-domain Integration & Regression Test Report

**Mission**: m7924 | **Node**: P350W1-N07  
**Date**: 2026-11-09  
**Status**: ALL PASS  

## Summary

| Metric | Value |
|--------|-------|
| Nodes tested | 6 |
| Nodes passed | 6 |
| Integration tests | 22/22 PASS |
| Core pytest | 194/194 PASS |
| Capability audit | 5 REAL, 4 WITHHELD, 3 NOT_SUPPORTED |

## Node Results

### N01: Propulsion Self-Propulsion Point
- **Module**: `backend/services/propulsion.py`
- **Input**: R=500 kN, V=5 m/s, D=4m, w=0.2, t=0.15
- **Output**: η_O=0.559, η_D=0.582, n=2.55 rps, J=0.391
- **Criteria**: η_O ∈ [0.3, 0.8] ✓, force balance < 1e-3 N ✓

### N02: Ice Integration (Contact + Sloshing)
- **Module**: `backend/services/ice_integration/reference_nodes.py`
- **Input**: 1m ice, 2 MPa, 1.5 m/s, 5 m² contact
- **Output**: F_contact=10 MN, melt film=4.4 mm, T_interface=273.0 K
- **Criteria**: Force ∈ [5, 20] MN ✓, film ∈ [1e-6, 0.1] m ✓

### N03: Structural Vibration (Hull Girder)
- **Module**: `backend/services/digital_twin/models/vibration.py`
- **Input**: L=150m tanker, B=24m, T=8m, Cb=0.7
- **Output**: f_2node=0.97 Hz, f_3node=2.68 Hz, ratio=2.76
- **Criteria**: f_2node ∈ [0.3, 5] Hz ✓, ratio ∈ [2, 4] ✓

### N04: Acoustic Radiation / Cabin Noise
- **Module**: `backend/services/digital_twin/models/vibration.py`
- **Input**: Lw=95 dB, 25 kg/m² panel, 250 Hz
- **Output**: TL=28.5 dB, SPL=60.8 dB, T60=1.65 s
- **Criteria**: TL ∈ [25, 35] dB ✓, mass law 6 dB/oct ✓

### N05: Evidence Grading Engine
- **Module**: `tools/outcome_evaluations.py`
- **Input**: 30 synthetic fixtures (S1-S5 × E0-E4)
- **Output**: 100% reproducible, correct rejection rules
- **Criteria**: Deterministic ✓, all fixtures processed ✓

### N06: SIMP Topology Optimization
- **Module**: `backend/services/simp_topology.py` (NEW)
- **Input**: 20×10 mesh, volfrac=0.5, penalty=3
- **Output**: C: 26.7 → 5.9 (69% reduction), V=0.500, converged
- **Criteria**: Compliance decreases ✓, volume constraint ✓, converged ✓

## Capability Ledger

| Capability | Evidence | Status |
|-----------|----------|--------|
| fem.simp.min_compliance | REAL | p=1 1D matches analytic |
| ice.maneuver.energy_ledger | REAL | Energy residual <10% |
| platform.evidence_grade.engine | REAL | 30/30 fixtures match |
| structure.ice.plate_beam_elastic | REAL | <5% vs analytic |
| structure.ice.plate_beam_ultimate | REAL | <5% vs yield line |
| fem.simp.density_filter | WITHHELD | No filter test yet |
| fem.simp.sensitivity_consistency | WITHHELD | No FD check yet |
| ice.maneuver.turning_radius | WITHHELD | No empirical data |
| ice.resistance.dem_straight_ahead | WITHHELD | DEM not integrated |

## Files Changed

- `backend/services/simp_topology.py` (NEW): SIMP 2D topology optimization
- `tests/integration/test_cross_domain_n07.py` (NEW): 22 integration tests
- `artifacts/m7924/integration_report.json`: Machine-readable report
- `docs/m7924/INTEGRATION_REPORT.md`: This report

## Limitations

1. SIMP MBB beam BC needs more work (symmetry handling)
2. Evidence grading chain links not populated (by design - requires real evidence)
3. 25 test files have collection errors (pre-existing, missing optional deps)
