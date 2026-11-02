# SWE-bench Style Evaluation Set for TensorPlatform Fleet

## Overview

This evaluation set implements SWE-bench discipline for the TensorPlatform fleet:
- **External fixed test set** (tasks are fixed, models cannot see them during evaluation)
- **Publicly comparable** (baseline numbers are reproducible)
- **Stratified by difficulty** (easy/medium/hard)

## Contents

```
eval_set/
├── eval_set.json          # 20 tasks with descriptions, acceptance criteria, solution hints
├── README.md              # This file
├── scripts/
│   ├── run_eval.py        # Dispatch runner (live or dry-run)
│   ├── verify_eval.py     # Independent acceptance verifier
│   └── generate_baseline.py  # Baseline from historical data
└── results/
    ├── baseline_report.json  # Machine-readable baseline
    ├── baseline_report.md    # Human-readable baseline
    └── baseline_table.json   # Per-model per-difficulty stats
```

## Evaluation Set Design

### Task Selection
- **20 tasks** extracted from historical missions m186-m325 (2026-10-21 to 2026-10-25)
- **10 succeeded + 10 failed** source missions (balanced)
- **5+ repos** covered: TensorPlatform, TensorLBM, TensorPhysics, TensorDEM, TensorBEM, TensorCAD, TensorSPH
- **Difficulty stratification**: 5 easy, 9 medium, 6 hard

### Acceptance Criteria
Each task has 4-5 independent acceptance criteria that:
- Do NOT reuse original author's self-tests
- Are machine-verifiable (pass/fail)
- Cover: correctness, completeness, performance, documentation

### Conflict of Interest Check
No task was authored by any evaluated model's operator session. Tasks are extracted from missions where the model was the implementer, not the task designer.

## Running the Evaluation

### Dry Run (no API calls)
```bash
python3 scripts/run_eval.py --dry-run --models qwen3.7-plus,glm-5.2,deepseek-v4-flash
```

### Live Evaluation
```bash
export TENSOR_AGENT_USER=agent
export TENSOR_AGENT_PASS=<password>
python3 scripts/run_eval.py --models qwen3.7-plus,glm-5.2,deepseek-v4-flash,qwen3.6-plus,glm-5.1
```

### Verify Results
```bash
python3 scripts/verify_eval.py --results results/eval_results_*.json
```

## Baseline Construction

The baseline is constructed from historical mission outcomes:

1. **Map** each eval task to its source mission
2. **Extract** terminal status (succeeded/failed) and wall time
3. **Aggregate** per model × difficulty
4. **Compute** Wilson score 95% confidence intervals
5. **Estimate** missing cells from fleet-wide rates (marked with *)

### Key Findings

| Model | Overall Pass Rate | 95% CI | Notes |
|-------|------------------|--------|-------|
| deepseek-v4-flash | 45.5% | [21.3%, 72.0%] | Most tasks attempted (11) |
| glm-5.2 | 42.9% | [15.8%, 75.0%] | Strong on medium |
| qwen3.6-plus | 42.9% | [15.8%, 75.0%] | Strong on medium |
| glm-5.1 | 42.9% | [15.8%, 75.0%] | Estimated for most cells |
| qwen3.7-plus | 33.3% | [9.7%, 70.0%] | Limited data |

**Important**: Many historical failures were infra-class (gateway_unavailable, agent_lost) and do not reflect model capability on the task itself. True capability is likely higher.

## Guide Tracker Integration

Recommendations for integrating eval_bench into guide_tracker:

1. **Add campaign type**: `eval_bench` in dispatch_helper.py
2. **Track results**: `tensor_mission_events` with `kind='eval_bench'`
3. **Quality gate**: Independent verifier in acceptance workflow
4. **Baseline comparison**: Auto-compare new results against baseline
5. **Drift detection**: Alert when model performance drops >1σ from baseline

## Limitations

1. **Small sample sizes** (n≤20 per model) → wide confidence intervals
2. **Historical data** used instead of fresh runs → may not reflect current model versions
3. **Infra failures** contaminate pass rates → true capability likely higher
4. **Estimated cells** for models with sparse data → marked with *
5. **Single epoch** → no variance estimation across runs

## Reproducibility

All numbers are reproducible from:
- `eval_set.json` (task definitions)
- `scripts/generate_baseline.py` (baseline construction)
- Historical mission data in `operator_log/`

To regenerate:
```bash
python3 scripts/generate_baseline.py
```

## Version History

- v1.0.0 (2026-10-31): Initial evaluation set from m186-m325

## Baseline Integrity Audit (evidence grade)

The three artifacts under `results/` describe "the same" baseline and must agree
before any number from them is quoted as a capability claim. The audit is
machine-checked, read-only and deterministic:

```bash
# 1) recompute every claimed number from the artifact it claims to come from
python3 scripts/audit_baseline_integrity.py \
    --json results/baseline_integrity_audit.json \
    --md   ../docs/audit/eval_baseline_integrity_ledger.md
# 2) figures + paper-grade doc page (numbers injected from the ledger)
python3 scripts/plot_baseline_integrity.py
python3 scripts/gen_baseline_integrity_doc.py
# 3) regression tests (logic on synthetic artifacts + ledger reproduction)
python3 -m pytest ../tests/test_eval_baseline_integrity.py -q
```

Current verdict (`eval_set/results/baseline_integrity_audit.json`, 21 checks):
the baseline's evidence grade is **MOCK** — `baseline_report.json` pass rates are
derived from a hand-typed `HISTORICAL_MAPPING` (23 rows, 21 of the 5x20
(task, model) cells observed), the only executed artifact is a dry run in which
all 100 dispatched cells have `status='dry_run'` and all 100 verification scores
are the constant 0.25, and `baseline_table.json` reports `passed=0` for every
model while the report reports non-zero. Quote these numbers as historical
context only, never as an evaluation result. See
`docs/audit/eval_baseline_integrity.md` for the six-element write-up.

