# Benchmark baseline integrity audit

- dataset: `backend/data/benchmark_reference.json`
- policy: max tolerance 15.0% (platform精度标准), identity 2.0%, Froude 1.0%
- **verdict: PASS** — cases 12, fail 0, partial 12, pass 2
- failing checks: none

## Per-case identity checks

| case | C1 Ct↔Rt | C1 ratio | C2 Fr | Fr implied L (m) | C3 units | C4 physicality | verdict |
|---|---|---|---|---|---|---|---|
| DTMB5415-01 | WITHHELD | — | PASS | 5.72 | WARN | PASS | PARTIAL |
| DTMB5415-02 | WITHHELD | — | PASS | 5.72 | WARN | PASS | PARTIAL |
| DTMB5415-03 | WITHHELD | — | PASS | 5.72 | WARN | PASS | PARTIAL |
| DTMB5415-04 | WITHHELD | — | PASS | 5.72 | WARN | PASS | PARTIAL |
| KVLCC2-01 | WITHHELD | — | PASS | 320.21 | PASS | PASS | PARTIAL |
| KVLCC2-02 | WITHHELD | — | PASS | 320.15 | PASS | PASS | PARTIAL |
| KVLCC2-03 | WITHHELD | — | PASS | 320.09 | PASS | PASS | PARTIAL |
| KVLCC2-04 | WITHHELD | — | PASS | 320.03 | PASS | PASS | PARTIAL |
| SUBOFF-01 | PASS | 0.998 | PASS | 4.36 | WITHHELD | PASS | PARTIAL |
| SUBOFF-02 | PASS | 0.998 | PASS | 4.36 | WITHHELD | PASS | PARTIAL |
| SUBOFF-03 | PASS | 0.998 | PASS | 4.36 | WITHHELD | PASS | PARTIAL |
| SUBOFF-04 | PASS | 0.998 | PASS | 4.36 | WITHHELD | PASS | PARTIAL |

`*` wetted surface from Holtrop estimate (indicative).

## Cross-store agreement (C5)

| case | dataset case | gate R_T (N) | Ct-implied R_T (N) | dev % | status |
|---|---|---|---|---|---|
| kvlcc2_full | — | — | — | — | WITHHELD |
| suboff_model | — | — | — | — | WITHHELD |

## Gate policy (C6)

| case | tolerance % | gate enabled | status |
|---|---|---|---|
| resistance/kvlcc2_full | 15.0 | True | PASS |
| resistance/suboff_model | 300.0 | False | WARN |
| stability/generic_barge | 15.0 | True | PASS |
