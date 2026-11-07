# Mission Failure Root Cause Classification & Recovery Analysis

**Analysis Date**: 2026-11-07T04:22:53.426842+00:00
**Total Terminal Missions**: 4698
**Failed Missions**: 587
**Recoverable Rate**: 91.1%

## 1. Overview

### Terminal State Distribution

| Status | Count | Percentage |
|--------|-------|------------|
| succeeded | 3908 | 83.2% |
| failed | 587 | 12.5% |
| revoked | 174 | 3.7% |
| stopped | 29 | 0.6% |

## 2. Failure Root Cause Classification

### Classification Rules (v1)

| Category | Trigger Patterns | Recoverable |
|----------|-----------------|-------------|
| budget_exhausted | budget_exhausted, tool_calls | Yes |
| wall_exhausted | wall_exhausted, wall_seconds | Yes |
| no_progress | no_progress | Yes |
| agent_lost | agent_lost | Yes |
| worktree_empty | 无任何改动, worktree_empty | Yes |
| llm_error | llm_error | Yes |
| runtime_error | error, exception, 错误 | No |
| timeout | timeout, 超时 | Yes |
| revoked | revoked, 撤销 | No |
| stopped | stopped, 停止 | No |

### Failure Category Distribution

| Category | Count | Percentage | Recoverable | Recovery Rate |
|----------|-------|------------|-------------|---------------|
| budget_exhausted | 471 | 80.2% | 471 | 100.0% |
| agent_lost | 60 | 10.2% | 60 | 100.0% |
| runtime_error | 52 | 8.9% | 0 | 0.0% |
| timeout | 4 | 0.7% | 4 | 100.0% |

## 3. Failure by Repository

| Repository | Failed Count |
|------------|-------------|
| TensorLBM | 156 |
| TensorDEM | 61 |
| TensorPlatform | 52 |
| TensorDG | 50 |
| TensorFVM | 46 |
| TensorFEM | 43 |
| TensorSPH | 39 |
| TensorBEM | 31 |
| TensorCAD | 30 |
| ship-performance-platform | 24 |

## 4. Failure by Model

| Model | Failed Count |
|-------|-------------|
| (none) | 329 |
| deepseek-v4-flash | 69 |
| qwen3.6-plus | 53 |
| DeepSeek-V4-Flash-0731 | 33 |
| MiniMax-M2.5-AWQ | 29 |
| Qwen3.6-35B-A3B | 23 |
| Qwen3.8-27B | 18 |
| qwen3.7-plus | 16 |
| glm-5.1 | 10 |
| glm-5.2 | 7 |

## 5. Recovery Analysis

**Total Recoverable**: 535 (91.1%)

| Category | Count | Recoverable | Recovery Rate | Suggestion |
|----------|-------|-------------|---------------|------------|
| budget_exhausted | 471 | 471 | 100.0% | Increase budget caps (max_tool_calls / max_wall_seconds) or split into sub-missions |
| agent_lost | 60 | 60 | 100.0% | Reduce mission complexity; add checkpoint/heartbeat; use more stable model |
| runtime_error | 52 | 0 | 0.0% | Fix code bugs; add error handling; validate inputs before execution |
| timeout | 4 | 4 | 100.0% | Increase timeout; optimize computation; use cluster for heavy tasks |

## 6. Re-dispatch Strategy Recommendations

1. Overall recoverable rate: 91.1% (535/587)
2. Top failure category: budget_exhausted (471 cases, 80.2%)
3. Budget-related failures: 471 (80.2%) — consider dynamic budget allocation based on mission complexity
4. Agent lost: 60 — implement heartbeat monitoring and automatic requeue
5. High failure repo: TensorLBM (156 failures) — review repo-specific issues
6. High failure repo: TensorDEM (61 failures) — review repo-specific issues
7. High failure repo: TensorPlatform (52 failures) — review repo-specific issues
8. High failure model: (none) (329 failures) — consider model-specific tuning
9. High failure model: deepseek-v4-flash (69 failures) — consider model-specific tuning
10. High failure model: qwen3.6-plus (53 failures) — consider model-specific tuning

## 7. Sample Failed Missions

| ID | Repo | Model | Category | Stage | Recoverable |
|------|------|-------|----------|-------|-------------|
| 5094 | ship-performance-platform | None | budget_exhausted | no_progress+forced_finish | Yes |
| 5080 | TensorDEM | None | budget_exhausted | no_progress+forced_finish | Yes |
| 5040 | TensorPlatform | None | budget_exhausted | budget_exhausted+forced_finish | Yes |
| 5017 | TensorLBM | None | budget_exhausted | no_progress | Yes |
| 5001 | TensorLBM | None | budget_exhausted | wall_exhausted | Yes |
| 4954 | TensorLBM | None | budget_exhausted | no_progress | Yes |
| 4952 | TensorFEM | None | budget_exhausted | no_progress | Yes |
| 4905 | TensorPlatform | qwen3.7-plus | agent_lost |  | Yes |
| 4898 | TensorLBM | None | budget_exhausted |  | Yes |
| 4884 | TensorSPH | None | agent_lost |  | Yes |

## 8. Limitations

- Classification is based on text pattern matching in result JSON
- Manual review may be needed for edge cases
- Recovery suggestions are heuristic and may not apply to all cases
- Analysis is based on historical data; real-time conditions may differ
