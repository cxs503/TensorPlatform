# Benchmark dataset audit — benchmark_reference.json (v1.0)

Constants: g=9.81 m/s^2, rho=1025.0 kg/m^3, nu=1.139e-06 m^2/s

| case | ship | Fr filed | Fr derived | dFr % | Re | Cf(ITTC-57) | Ct/Cf | Rt filed | Rt derived | dRt % | Cb filed | Cb derived | status |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| SUBOFF-01 | SUBOFF | -- | 0.4589 | 0.0 | 1.147e+07 | 0.00293 | 1.297 | 12.5 | 95.0 | 86.84 | -- | -- | inconsistent_unresolved |
| SUBOFF-02 | SUBOFF | -- | 0.6119 | 0.0 | 1.530e+07 | 0.00279 | 1.434 | 21.0 | 177.78 | 88.19 | -- | -- | inconsistent_unresolved |
| SUBOFF-03 | SUBOFF | -- | 0.7649 | 0.0 | 1.912e+07 | 0.002689 | 1.562 | 31.5 | 291.66 | 89.2 | -- | -- | inconsistent_unresolved |
| SUBOFF-04 | SUBOFF | -- | 0.9179 | 0.0 | 2.295e+07 | 0.00261 | 1.686 | 45.0 | 440.0 | 89.77 | -- | -- | inconsistent_unresolved |
| DTMB5415-01 | DTMB-5415 | 0.061 | 0.3434 | 82.23 | 1.292e+07 | 0.002871 | 1.567 | -- | -- | -- | 0.508 | 0.426 | inconsistent_unresolved |
| DTMB5415-02 | DTMB-5415 | 0.122 | 0.6867 | 82.23 | 2.583e+07 | 0.00256 | 1.484 | -- | -- | -- | 0.508 | 0.426 | inconsistent_unresolved |
| DTMB5415-03 | DTMB-5415 | 0.244 | 1.3735 | 82.24 | 5.167e+07 | 0.002298 | 1.523 | -- | -- | -- | 0.508 | 0.426 | inconsistent_unresolved |
| DTMB5415-04 | DTMB-5415 | 0.366 | 2.0602 | 82.24 | 7.750e+07 | 0.002162 | 1.757 | -- | -- | -- | 0.508 | 0.426 | inconsistent_unresolved |
| KVLCC2-01 | KVLCC2 | -- | 0.1285 | 0.0 | 2.023e+09 | 0.001405 | 1.993 | -- | -- | -- | 0.8098 | 0.7758 | inconsistent_unresolved |
| KVLCC2-02 | KVLCC2 | -- | 0.1377 | 0.0 | 2.168e+09 | 0.001394 | 2.153 | -- | -- | -- | 0.8098 | 0.7758 | inconsistent_unresolved |
| KVLCC2-03 | KVLCC2 | -- | 0.1423 | 0.0 | 2.240e+09 | 0.001388 | 2.233 | -- | -- | -- | 0.8098 | 0.7758 | inconsistent_unresolved |
| KVLCC2-04 | KVLCC2 | -- | 0.1469 | 0.0 | 2.312e+09 | 0.001383 | 2.314 | -- | -- | -- | 0.8098 | 0.7758 | inconsistent_unresolved |

Cases: 12 total, 12 with ERROR-level contradictions; status counts: {'verified': 0, 'out_of_validity_range': 0, 'inconsistent_unresolved': 12}

Findings: {"cb_closure:ERROR": 8, "froude_identity:ERROR": 4, "froude_range:WARN": 6, "rt_closure:ERROR": 4}
