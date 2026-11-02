# 舰队能力基线（m538）证据等级审计 · 机检账本

> 本页由 `eval_set/scripts/audit_baseline_integrity.py` 从 `eval_set/results/baseline_integrity_audit.json` 程序化渲染，数字零手抄。
> 审计对象：`eval_set/results/baseline_report.json`（m538 能力基线）、`baseline_table.json`、`eval_results_20261030T194201Z{,_verified}.json`。

## 判定摘要

| 项 | 值 |
|---|---|
| 检查项 | 21 |
| PASS / WARN / FAIL / STUB / MISSING / INFO | 7 / 3 / 7 / 1 / 0 / 3 |
| 基线证据等级 | **MOCK** |

pass rates are derived from a hand-typed HISTORICAL_MAPPING inside generate_baseline.py ('cannot run live evaluations in this environment'); the only executed artifact is a dry run with every cell status='dry_run', every verification failed and every weighted score a constant 0.25

## 逐模型：声称值 vs 由映射表复算值 vs 另两份工件

| 模型 | 报告 passed/total | 报告 rate [95%CI] | 映射表 passed/total | 映射表 rate [95%CI] | table passed | verified passed |
|---|---|---|---|---|---|---|
| qwen3.7-plus | 2/6 | 0.333 [0.097, 0.700] | 2/6 | 0.3333 [0.0968, 0.7000] | 0 | 0 |
| glm-5.2 | 3/7 | 0.429 [0.158, 0.750] | 1/1 | 1.0000 [0.2065, 1.0000] | 0 | 0 |
| deepseek-v4-flash | 5/11 | 0.455 [0.213, 0.720] | 5/11 | 0.4545 [0.2127, 0.7199] | 0 | 0 |
| qwen3.6-plus | 3/7 | 0.429 [0.158, 0.750] | 2/4 | 0.5000 [0.1500, 0.8500] | 0 | 0 |
| glm-5.1 | 3/7 | 0.429 [0.158, 0.750] | 0/1 | 0.0000 [0.0000, 0.7935] | 0 | 0 |

## 检查逐条

| id | 组 | 判定 | 证据 | 说明 |
|---|---|---|---|---|
| `A1_run_provenance` | provenance | **FAIL** | REAL | eval run artifact is a dry run: meta.dry_run=True, 100/100 dispatched cells have status='dry_run' -> the per-model pass rates in baseline_report.json are NOT produced by executing the eval set |
| `A2_mapping_closure` | provenance | **FAIL** | REAL | the report's denominators do not close on its own source: 15 of 38 per-model task slots are imputed (estimated_total=3 per empty difficulty bucket) rather than present in HISTORICAL_MAPPING |
| `A3_estimate_flag` | provenance | **FAIL** | REAL | generate_baseline.py sets baseline[model][diff]['estimated']=True for imputed buckets and the .md table renders it as '*', but baseline_report.json contains 0 'estimated' keys -> a JSON consumer cannot tell measured from imputed cells |
| `A4_ci_block` | provenance | **STUB** | REAL | baseline_report.json['confidence_intervals'] is an empty object while the .md/JSON present per-model ci_95 -> the declared CI container is a stub |
| `A5_repo_claim` | provenance | **WARN** | REAL | summary text claims 5 repos, report eval_set.repos lists 7, tasks actually cover 7 (TensorBEM, TensorCAD, TensorDEM, TensorLBM, TensorPhysics, TensorPlatform, TensorSPH) |
| `A6_report_vs_verified` | cross_artifact | **FAIL** | REAL | verified artifact reports 0/100 passed (pass_rate 0.0) while baseline_report reports 16/38 passed -> two 'same baseline' artifacts disagree on the headline |
| `A7_verified_score_floor` | cross_artifact | **FAIL** | MOCK | all 100 verification scores take the single value 0.25 -> the weighted score is a constant, i.e. no criterion moved it (dry-run floor, not a measurement) |
| `A8_table_vs_report` | cross_artifact | **FAIL** | REAL | baseline_table.json and baseline_report.json disagree for 5/5 models on (passed, total) |
| `B1_difficulty_closure` | arithmetic | **PASS** | REAL | per-model by_difficulty totals sum to total_tasks for 5/5 models |
| `B2_rate_arithmetic` | arithmetic | **PASS** | REAL | claimed pass_rate equals passed/total (tol 0.0005) in all 20 buckets |
| `B3_wilson_recompute` | arithmetic | **PASS** | REAL | per-model Wilson 95% CI recomputed independently (z=1.96); max deviation 0.00047, 0/5 models outside tolerance 0.002 |
| `B4_wall_hours` | arithmetic | **PASS** | REAL | wall_hours_total equals wall_seconds_total/3600 (tol 0.06) for 5/5 models |
| `B5_cell_coverage` | arithmetic | **INFO** | REAL | HISTORICAL_MAPPING observes 21 of 100 (task, model) cells = 21.0%; the rest of the matrix is imputed |
| `D1_difficulty_dist` | evalset | **PASS** | REAL | recomputed difficulty distribution {'easy': 5, 'medium': 9, 'hard': 6} over 20 tasks (meta declares the levels, not the counts) |
| `D2_source_balance` | evalset | **INFO** | REAL | source mission outcomes: {'succeeded': 10, 'failed': 10} (balance is a design choice, not a defect) |
| `D3_id_integrity` | evalset | **PASS** | REAL | 20 tasks, 20 unique ids, duplicates=none |
| `D4_criteria_verifiability` | evalset | **FAIL** | REAL | 100 acceptance criteria: 49 measured (numeric tolerance), 21 decidable (observable anchor: endpoint/status/return/test/file), 30 subjective (30.0%, needs a human judge -> not machine-decidable; FAIL >25%, WARN >10%). Classifier calibrated on a 20-criterion hand-labelled sample, agreement 100% |
| `D5_hint_leakage` | evalset | **WARN** | REAL | run_eval.py renders solution_hints into the dispatched goal, so the model sees the intended solution: 4 hints name a concrete file/test path, 0 carry the numeric acceptance magnitude |
| `D6_field_completeness` | evalset | **PASS** | REAL | all tasks carry non-empty repo/title/description (empty=0) |
| `E1_reproducibility_stamp` | reproducibility | **WARN** | REAL | baseline_report.json records no digest of HISTORICAL_MAPPING and no seed, so the reader cannot tell which mapping revision produced the numbers |
| `E2_timestamp_drift` | reproducibility | **INFO** | REAL | baseline generated_at=2026-10-30T19:43:24.562507+00:00, eval run_at=2026-10-30T19:42:01.711926+00:00 -- regenerating rewrites these fields, so byte-identical reproduction is impossible by construction |

## 原始数值

```json
{
  "qwen3.7-plus": {
    "claimed_total": 6,
    "claimed_passed": 2,
    "claimed_rate": 0.333,
    "claimed_ci95": [
      0.097,
      0.7
    ],
    "mapping_total": 6,
    "mapping_passed": 2,
    "mapping_rate": 0.3333,
    "mapping_ci95": [
      0.0968,
      0.7
    ],
    "verified_passed": 0,
    "table_passed": 0
  },
  "glm-5.2": {
    "claimed_total": 7,
    "claimed_passed": 3,
    "claimed_rate": 0.429,
    "claimed_ci95": [
      0.158,
      0.75
    ],
    "mapping_total": 1,
    "mapping_passed": 1,
    "mapping_rate": 1.0,
    "mapping_ci95": [
      0.2065,
      1.0
    ],
    "verified_passed": 0,
    "table_passed": 0
  },
  "deepseek-v4-flash": {
    "claimed_total": 11,
    "claimed_passed": 5,
    "claimed_rate": 0.455,
    "claimed_ci95": [
      0.213,
      0.72
    ],
    "mapping_total": 11,
    "mapping_passed": 5,
    "mapping_rate": 0.4545,
    "mapping_ci95": [
      0.2127,
      0.7199
    ],
    "verified_passed": 0,
    "table_passed": 0
  },
  "qwen3.6-plus": {
    "claimed_total": 7,
    "claimed_passed": 3,
    "claimed_rate": 0.429,
    "claimed_ci95": [
      0.158,
      0.75
    ],
    "mapping_total": 4,
    "mapping_passed": 2,
    "mapping_rate": 0.5,
    "mapping_ci95": [
      0.15,
      0.85
    ],
    "verified_passed": 0,
    "table_passed": 0
  },
  "glm-5.1": {
    "claimed_total": 7,
    "claimed_passed": 3,
    "claimed_rate": 0.429,
    "claimed_ci95": [
      0.158,
      0.75
    ],
    "mapping_total": 1,
    "mapping_passed": 0,
    "mapping_rate": 0.0,
    "mapping_ci95": [
      0.0,
      0.7935
    ],
    "verified_passed": 0,
    "table_passed": 0
  }
}
```
