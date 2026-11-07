# P05 Skills Evaluation Scores

## 1. Submission P05-1
- **Goal and constraints**: 2/3. Correct scope, identifies trade-offs (adaptive TTL vs freshness).
- **Method discipline**: 2/3. Follows procedure, deterministic simulation, 5 reps.
- **Evidence quality**: 2/3. Traceable (JSON, figures), but limited repetitions.
- **Alternatives/uncertainty**: 2/3. Compares strategies, explains mechanism of failure for adaptive TTL.
- **Output usefulness**: 2/3. Actionable recommendation, clear limitations.
- **Safety and scope**: 2/3. Compliant, no external dependencies.
- **Total**: 12/18 (Competent)
- **Key Defect**: Insufficient repetitions (5) for statistical claims; no baseline (no-cache) comparison.

## 2. Submission P05-2
- **Goal and constraints**: 3/3. Captures full scope, includes baseline, clear thresholds.
- **Method discipline**: 3/3. Justified adaptation (power analysis), follows procedure strictly.
- **Evidence quality**: 3/3. Independent and reproducible (30 reps, 4 figures, JSON).
- **Alternatives/uncertainty**: 3/3. Seeks disconfirmation (H3 supported), calibrates with t-tests.
- **Output usefulness**: 3/3. Strong handoff, decision matrix, comprehensive figures.
- **Safety and scope**: 2/3. Compliant, synthetic data.
- **Total**: 14/18 (Competent/Strong)
- **Key Defect**: Degenerate t-tests for hit rate/stale ratio due to fixed trace (std=0); should note this limitation explicitly.

## 3. Verdict
- **Winner**: P05-2
- **Gap**: +2 points (Stronger method discipline and evidence quality).
- **Reason**: P05-2 includes a baseline, justifies sample size, and provides more robust statistical analysis. P05-1 is adequate but lighter on rigor.

## 4. Facts/Inferences/Unknowns
- **Facts**: P05-2 LRU hit rate 0.8371, LFU 0.8642. P05-2 LRU stale 2.7%, LFU 46.3%.
- **Inferences**: LFU retains stale hot items; LRU is more robust for freshness-sensitive workloads.
- **Unknowns**: Performance under write-heavy workloads; impact of cache size scaling.

## 5. Handoff
- Another agent can reproduce P05-2 using `cache_experiment.py` and `generate_figures.py`.
