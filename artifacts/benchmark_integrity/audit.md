# 基准算例真值完整性审计（benchmark_reference.json）

- 算例数：12
- 判定分布：{'REAL': 4, 'DERIVED': 0, 'WITHHELD': 8}
- 容差：{"froude_rel": 0.02, "wetted_surface_rel": 0.25, "ct_over_cf_band": [0.9, 3.5], "ct_trend_rel": 0.05}

| case | 船型 | 判定 | Fr(报表/几何复算) | Rt(报表/由Ct推导) N | S(引用/反推) m² | 反推/引用 | Ct/Cf |
|---|---|---|---|---|---|---|---|
| DTMB5415-01 | DTMB-5415 | **WITHHELD** | 0.061 / 0.06099 | None / 2.08 | 4.435 / None | None | 1.141 |
| DTMB5415-02 | DTMB-5415 | **WITHHELD** | 0.122 / 0.122 | None / 7.02 | 4.435 / None | None | 1.101 |
| DTMB5415-03 | DTMB-5415 | **WITHHELD** | 0.244 / 0.244 | None / 25.88 | 4.435 / None | None | 1.149 |
| DTMB5415-04 | DTMB-5415 | **WITHHELD** | 0.366 / 0.36601 | None / 63.22 | 4.435 / None | None | 1.338 |
| KVLCC2-01 | KVLCC2 | **WITHHELD** | 0.1285 / 0.12854 | None / 1973298.15 | 26511.517 / None | None | 1.982 |
| KVLCC2-02 | KVLCC2 | **WITHHELD** | 0.1377 / 0.13773 | None / 2427429.9 | 26511.517 / None | None | 2.142 |
| KVLCC2-03 | KVLCC2 | **WITHHELD** | 0.1423 / 0.14232 | None / 2678197.49 | 26511.517 / None | None | 2.222 |
| KVLCC2-04 | KVLCC2 | **WITHHELD** | 0.1469 / 0.14691 | None / 2945666.83 | 26511.517 / None | None | 2.302 |
| SUBOFF-01 | SUBOFF | **REAL** | 0.4589 / 0.45893 | 92.52 / 92.52 | 5.42 / 5.42 | 1.0001 | 1.297 |
| SUBOFF-02 | SUBOFF | **REAL** | 0.6119 / 0.6119 | 173.13 / 173.13 | 5.42 / 5.42 | 1.0 | 1.434 |
| SUBOFF-03 | SUBOFF | **REAL** | 0.7649 / 0.76488 | 284.04 / 284.04 | 5.42 / 5.42 | 1.0 | 1.562 |
| SUBOFF-04 | SUBOFF | **REAL** | 0.9179 / 0.91785 | 428.49 / 428.49 | 5.42 / 5.42 | 1.0 | 1.686 |

## C4 单调性告警

- DTMB-5415: DTMB5415-01 -> DTMB5415-02 Ct 下降 15.56%
- DTMB-5415: DTMB5415-02 -> DTMB5415-03 Ct 下降 7.89%
