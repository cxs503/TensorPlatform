# SWE-bench Style Fleet Capability Baseline

**Generated:** 2026-10-30T19:43:24.562507+00:00

**Mission ID:** 538

## Summary

Evaluation set of 20 tasks extracted from historical missions m186-m325, covering 5 repos (TensorPlatform, TensorLBM, TensorPhysics, TensorDEM, TensorBEM). Baseline constructed from historical outcomes with Wilson score confidence intervals.

## Evaluation Set

- Total tasks: 20
- Source succeeded: 10
- Source failed: 10
- Repos: TensorPlatform, TensorLBM, TensorPhysics, TensorDEM, TensorBEM, TensorCAD, TensorSPH
- Difficulty: easy=5, medium=9, hard=6

## Baseline Table

| Model | Difficulty | Pass | Total | Rate | Wall(h) | 95% CI |
|-------|-----------|------|-------|------|---------|--------|
| qwen3.7-plus | easy | 1 | 1 | 100.0% | 1.0 | [20.7%, 100.0%] |
| qwen3.7-plus | medium | 1 | 4 | 25.0% | 5.8 | [4.6%, 69.9%] |
| qwen3.7-plus | hard | 0 | 1 | 0.0% | 0.7 | [0.0%, 79.3%] |
| qwen3.7-plus | overall | 2 | 6 | 33.3% | 7.5 | [9.7%, 70.0%] |
| glm-5.2 | easy | 1 | 3 | 33.3% | 2.0 | [6.1%, 79.2%] * |
| glm-5.2 | medium | 1 | 1 | 100.0% | 1.5 | [20.7%, 100.0%] |
| glm-5.2 | hard | 1 | 3 | 33.3% | 6.0 | [6.1%, 79.2%] * |
| glm-5.2 | overall | 3 | 7 | 42.9% | 9.5 | [15.8%, 75.0%] |
| deepseek-v4-flash | easy | 1 | 3 | 33.3% | 1.6 | [6.1%, 79.2%] |
| deepseek-v4-flash | medium | 2 | 3 | 66.7% | 4.2 | [20.8%, 93.9%] |
| deepseek-v4-flash | hard | 2 | 5 | 40.0% | 5.8 | [11.8%, 76.9%] |
| deepseek-v4-flash | overall | 5 | 11 | 45.5% | 11.6 | [21.3%, 72.0%] |
| qwen3.6-plus | easy | 1 | 3 | 33.3% | 2.0 | [6.1%, 79.2%] * |
| qwen3.6-plus | medium | 1 | 1 | 100.0% | 1.3 | [20.7%, 100.0%] |
| qwen3.6-plus | hard | 1 | 3 | 33.3% | 4.3 | [6.1%, 79.2%] |
| qwen3.6-plus | overall | 3 | 7 | 42.9% | 7.7 | [15.8%, 75.0%] |
| glm-5.1 | easy | 0 | 1 | 0.0% | 0.7 | [0.0%, 79.3%] |
| glm-5.1 | medium | 2 | 3 | 66.7% | 4.0 | [20.8%, 93.9%] * |
| glm-5.1 | hard | 1 | 3 | 33.3% | 6.0 | [6.1%, 79.2%] * |
| glm-5.1 | overall | 3 | 7 | 42.9% | 10.7 | [15.8%, 75.0%] |

\* = estimated from fleet-wide rate

## Methodology

### Task Extraction
20 tasks from historical missions m186-m325, balanced succeeded/failed

### Acceptance Criteria
Independent verification (V1-V8), not reusing original author self-tests

### Baseline Construction
Historical outcomes mapped to eval tasks; Wilson score CI for small samples

### Limitations
- Baseline uses historical outcomes, not fresh model runs
- Many historical failures were infra-class (gateway_unavailable, agent_lost)
- Models with sparse data have estimated rates from fleet-wide averages
- Small sample sizes (n≤20 per model) yield wide confidence intervals

## Guide Tracker Recommendations

- Add eval_bench campaign type to dispatch_helper.py
- Track eval task results in tensor_mission_events with kind='eval_bench'
- Add eval-specific acceptance verifier to quality gates
- Integrate baseline comparison into mission completion workflow
