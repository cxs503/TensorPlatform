# KCS Bow Form Optimizer

Closed-loop optimization system for KCS hull bow form optimization using CMA-ES.

## Overview

This optimizer implements a complete optimization loop for ship hull form design:
- **Parameterization**: 8-parameter KCS bow deformation model (m546 interface)
- **Evaluation**: Holtrop-Mennen resistance calculation (m547 interface)
- **Optimizer**: CMA-ES (Covariance Matrix Adaptation Evolution Strategy)
- **Constraints**: Geometric validity (watertight, displacement) + Holtrop domain

## Features

✓ Fixed seed reproducibility (bit-identical results)
✓ Convergence detection (stagnation-based)
✓ Geometric constraint enforcement
✓ Top-5 candidate extraction with validation
✓ Convergence curve visualization
✓ Comprehensive reporting

## Usage

### Basic Run
```bash
python optimizer/run_optimization.py --seed 42 --max-evals 300 --output-dir ./optimizer/runs/run_001
```

### Reproducibility Verification
```bash
python optimizer/run_optimization.py --seed 42 --max-evals 300 --output-dir ./optimizer/runs/repro_test
```
This runs the optimization twice and verifies bit-identical results.

### Options
- `--seed SEED`: Random seed for reproducibility (default: 42)
- `--max-evals N`: Maximum evaluations (200-500, default: 300)
- `--output-dir DIR`: Output directory (default: ./optimizer/runs/opt_run)
- `--no-verify`: Skip reproducibility verification
- `--no-plots`: Skip visualization generation

## Output Files

- `optimization_result.json`: Full optimization result
- `convergence_curve.json`: Convergence history data
- `top5_candidates.json`: Top-5 validated candidates
- `summary.txt`: Human-readable summary
- `convergence_curve.png`: Convergence plot
- `step_size_evolution.png`: CMA-ES step size evolution
- `top5_comparison.png`: Top-5 candidates comparison

## Interface Contracts

### m546 (Geometry Generation)
- Input: 8-dim parameter vector in [-1, 1]
- Output: Geometry dict with validity flags
- Constraints: Watertight, displacement bounds (±5%)

### m547 (Resistance Evaluation)
- Input: Geometry dict + design Fn
- Output: Resistance components and Ct
- Constraints: Holtrop calibration domain

## Parameters

1. `bow_entrance_angle`: Bow entrance angle modification (±5 deg)
2. `forward_shoulder_curvature`: Forward shoulder curvature (±15%)
3. `waterline_flare_fp`: Waterline flare at FP (±10%)
4. `deck_camber_bow`: Deck camber near bow (±5%)
5. `bulb_volume_scale`: Bulbous bow volume scaling (0.7-1.3x)
6. `bulb_vertical_pos`: Bulb vertical position (±8% T)
7. `stem_rake_angle`: Stem rake angle (±8 deg)
8. `forward_body_length_frac`: Forward body length distribution (0.35-0.65)

## Convergence Criteria

- **Patience**: 20 generations without improvement
- **Tolerance**: Relative improvement < 1e-5
- **Budget**: 200-500 evaluations (configurable)

## Validation

All top-5 candidates must pass:
1. Geometric constraints (watertight, displacement)
2. Holtrop calibration domain check
3. Valid resistance evaluation

## Reproducibility

Fixed seed ensures bit-identical results across runs:
- Same random number generator state
- Deterministic geometry generation
- Deterministic resistance calculation
- No floating-point non-determinism

## Dependencies

- numpy
- matplotlib (for visualizations)

## Notes

- Mock interfaces (m546/m547) will be replaced with real implementations
- Resistance model includes parameter sensitivity for optimization
- Convergence is typically achieved within 200-300 evaluations
