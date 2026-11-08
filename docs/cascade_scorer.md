# Multi-Fidelity Cascade Scorer (IN59-N10)

## Overview

The Multi-Fidelity Cascade Scorer is a three-level filtering architecture
for design-space exploration that addresses the `wall_exhausted` failure
mode (plan 41 / IN25-T3) by eliminating ≥80% of candidates at low and
medium fidelity stages, conserving expensive high-fidelity evaluation
budget.

## Architecture

```
Candidates (N_total)
    │
    ▼
┌─────────────────────────┐
│  LOW FIDELITY           │  analytical / empirical formulas
│  cost ≈ 1 unit/cand     │  target elimination ≥ 50 %
│  physics gate: basic    │  (mass conservation only)
└───────────┬─────────────┘
            │ survivors
            ▼
┌─────────────────────────┐
│  MEDIUM FIDELITY        │  reduced-order / surrogate model
│  cost ≈ 10 unit/cand    │  target elimination ≥ 60 % of survivors
│  physics gate: full     │  (mass + momentum)
└───────────┬─────────────┘
            │ survivors
            ▼
┌─────────────────────────┐
│  HIGH FIDELITY          │  full-order CFD / experiment
│  cost ≈ 1000 unit/cand  │  budget-adaptive sample size
│  physics gate: strict   │  (mass + momentum + energy)
└───────────┬─────────────┘
            │ winners
            ▼
        ranked results
```

## Key Features

### 1. Physics Consistency Gate

Three strictness levels, each checking progressively more conservation laws:

| Strictness | Stage | Checks |
|-----------|-------|--------|
| `basic` | Low | Mass conservation |
| `full` | Medium | Mass + Momentum |
| `strict` | High | Mass + Momentum + Energy |

Residuals are relative errors `|computed - expected| / |expected|`.
Candidates exceeding the tolerance are eliminated.

### 2. Budget-Adaptive Sampling

Two mechanisms ensure total cost never exceeds the budget envelope:

- **Pass-rate adaptation**: When the nominal pass rate would produce more
  survivors than the budget can afford at the next stage, the rate is
  shrunk (down to `min_pass_rate_fraction` of nominal).
- **Evaluation capping**: When the budget cannot afford to evaluate all
  incoming candidates at the current stage, candidates are pre-ranked
  using the previous stage's score and only the top-K affordable are
  evaluated.

### 3. Configurable

All parameters are externalised via `CascadeConfig`:

| Parameter | Default | Description |
|-----------|---------|-------------|
| `low_cost` | 1.0 | Cost per candidate at low fidelity |
| `medium_cost` | 10.0 | Cost per candidate at medium fidelity |
| `high_cost` | 1000.0 | Cost per candidate at high fidelity |
| `low_pass_rate` | 0.50 | Fraction passing low stage |
| `medium_pass_rate` | 0.40 | Fraction passing medium stage |
| `mass_tol` | 1e-3 | Mass conservation tolerance |
| `momentum_tol` | 1e-2 | Momentum conservation tolerance |
| `energy_tol` | 1e-2 | Energy conservation tolerance |
| `budget_adaptive` | True | Enable budget-adaptive sampling |
| `min_survivors` | 1 | Minimum candidates per stage |
| `min_pass_rate_fraction` | 0.10 | Floor for shrunk pass rate |
| `total_budget` | 0.0 | Total budget (0 = unlimited) |

## API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/v1/cascade/scorer/info` | Capability information |
| GET | `/api/v1/cascade/scorer/config` | Default configuration |
| POST | `/api/v1/cascade/scorer/config` | Validate custom configuration |
| POST | `/api/v1/cascade/scorer/run` | Run cascade scoring session |

## Usage

### Python API

```python
from services.cascade_scorer import (
    CascadeConfig, CascadeScorer, Candidate,
    make_synthetic_scorer,
)

# Configure
config = CascadeConfig(
    low_cost=1.0, medium_cost=10.0, high_cost=1000.0,
    low_pass_rate=0.50, medium_pass_rate=0.40,
    total_budget=5000.0, budget_adaptive=True,
)

# Create candidates
candidates = [
    Candidate(id=f"c{i}", params={"resistance": float(i)})
    for i in range(100)
]

# Create scorer (inject domain-specific scoring functions)
scorer = CascadeScorer(
    config=config,
    low_scorer=make_synthetic_scorer("resistance", "min"),
    medium_scorer=make_synthetic_scorer("resistance", "min"),
    high_scorer=make_synthetic_scorer("resistance", "min"),
)

# Run cascade
result = scorer.run(candidates)
print(f"Elimination rate (L+M): {result.elimination_rate_low_medium:.1%}")
print(f"Total cost: {result.total_cost} / {config.total_budget}")
```

### REST API

```bash
curl -X POST http://localhost:8001/api/v1/cascade/scorer/run \
  -H "Content-Type: application/json" \
  -d '{
    "candidates": [
      {"id": "c0", "params": {"resistance": 0.5}, ...}
    ],
    "low_scorer": {"param_key": "resistance", "direction": "min"},
    "medium_scorer": {"param_key": "resistance", "direction": "min"},
    "high_scorer": {"param_key": "resistance", "direction": "min"},
    "config": {"total_budget": 5000.0}
  }'
```

## Domain Integration

The cascade scorer is **domain-agnostic** (zero domain semantics per
platform rule). Domain-specific scoring functions are injected as
callables:

- **Low fidelity**: Analytical/empirical formulas (e.g., Holtrop
  resistance, simple stability checks)
- **Medium fidelity**: Reduced-order/surrogate models (e.g., RSM, Kriging)
- **High fidelity**: Full-order CFD/experiment

Physics-consistency residuals are computed by the caller and passed to
the gate via the `residual_fn` parameter.

## wall_exhausted Mitigation

The cascade directly addresses the IN25-T3 `wall_exhausted` failure mode:

| Scenario | Naive Cost | Cascade Cost | Savings |
|----------|-----------|-------------|---------|
| 500 candidates, budget=5000 | 500,000 | ≤5,000 | ≥99% |
| 200 candidates, unlimited | 200,000 | 41,200 | 79.4% |
| 200 candidates, budget=3000 | 200,000 | ≤3,000 | ≥98.5% |

## Test Coverage

43 tests covering:
- Physics consistency gate (7 tests)
- Budget manager (7 tests)
- Cascade scorer core (17 tests)
- API endpoints (8 tests)
- wall_exhausted integration scenarios (4 tests)

## Files

| File | Description |
|------|-------------|
| `backend/services/cascade_scorer.py` | Core implementation |
| `backend/api/cascade_scorer.py` | API endpoints |
| `tests/test_cascade_scorer.py` | Test suite (43 tests) |
| `docs/cascade_scorer.md` | This documentation |
| `artifacts/cascade_scorer/` | Benchmark results and figures |
