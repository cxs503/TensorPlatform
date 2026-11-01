## 表1 参考数据集一致性审计（程序生成）

| case | ship | V (m/s) | Ct_ref | 状态 | 失败/未跑检查 |
|---|---|---|---|---|---|
| DTMB5415-01 | DTMB-5415 | 0.4569 | 0.0045 | PARTIAL | resistance_closure |
| DTMB5415-02 | DTMB-5415 | 0.9139 | 0.0038 | PARTIAL | resistance_closure |
| DTMB5415-03 | DTMB-5415 | 1.8278 | 0.0035 | PARTIAL | resistance_closure |
| DTMB5415-04 | DTMB-5415 | 2.7417 | 0.0038 | PARTIAL | resistance_closure |
| KVLCC2-01 | KVLCC2 | 7.202 | 0.0028 | PARTIAL | resistance_closure |
| KVLCC2-02 | KVLCC2 | 7.717 | 0.003 | PARTIAL | resistance_closure |
| KVLCC2-03 | KVLCC2 | 7.974 | 0.0031 | PARTIAL | resistance_closure |
| KVLCC2-04 | KVLCC2 | 8.231 | 0.0032 | PARTIAL | resistance_closure |
| SUBOFF-01 | SUBOFF | 3.0 | 0.0038 | CONSISTENT | - |
| SUBOFF-02 | SUBOFF | 4.0 | 0.004 | CONSISTENT | - |
| SUBOFF-03 | SUBOFF | 5.0 | 0.0042 | CONSISTENT | - |
| SUBOFF-04 | SUBOFF | 6.0 | 0.0044 | CONSISTENT | - |

## 表2 实链路对标结果（进程内物理，无 mock）

| ship | cases | verified | calc Ct (min–max) | ref Ct (min–max) | 误差幅度 | pass |
|---|---|---|---|---|---|---|
| SUBOFF | 4 | 4 | 0.00346–0.00592 | 0.00380–0.00440 | 1.2%–34.6% | 3 |
| DTMB-5415 | 4 | 0 | 0.00310–0.00385 | 0.00350–0.00450 | 10.7%–18.2% | 0 |
| KVLCC2 | 4 | 0 | 0.00141–0.00142 | 0.00280–0.00320 | 49.5%–56.1% | 0 |

## 表3 摩擦线交叉校核（ITTC-1957 vs Schoenherr）

| Re | Cf ITTC-57 | Cf Schoenherr | 相对差 |
|---|---|---|---|
| 1e+06 | 4.687500e-03 | 4.409433e-03 | 6.31% |
| 1e+07 | 3.000000e-03 | 2.934279e-03 | 2.24% |
| 1e+08 | 2.083333e-03 | 2.072030e-03 | 0.55% |
| 1e+09 | 1.530612e-03 | 1.530937e-03 | 0.02% |
