# Benchmark dataset audit — benchmark_reference.json (v2.0)

Constants: g=9.81 m/s^2, rho=1025.0 kg/m^3, nu=1.139e-06 m^2/s

| case | ship | Fr filed | Fr derived | dFr % | Re | Cf(ITTC-57) | Ct/Cf | Rt filed | Rt derived | dRt % | Cb filed | Cb derived | status |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| SUBOFF-01 | SUBOFF | -- | 0.4589 | 0.0 | 1.147e+07 | 0.00293 | 1.297 | -- | 95.0 | -- | -- | -- | verified |
| SUBOFF-02 | SUBOFF | -- | 0.6119 | 0.0 | 1.530e+07 | 0.00279 | 1.434 | -- | 177.78 | -- | -- | -- | out_of_validity_range |
| SUBOFF-03 | SUBOFF | -- | 0.7649 | 0.0 | 1.912e+07 | 0.002689 | 1.562 | -- | 291.66 | -- | -- | -- | out_of_validity_range |
| SUBOFF-04 | SUBOFF | -- | 0.9179 | 0.0 | 2.295e+07 | 0.00261 | 1.686 | -- | 440.0 | -- | -- | -- | out_of_validity_range |
| DTMB5415-01 | DTMB-5415 | -- | 0.3434 | 0.0 | 1.292e+07 | 0.002871 | 1.567 | -- | -- | -- | 0.508 | 0.426 | inconsistent_unresolved |
| DTMB5415-02 | DTMB-5415 | -- | 0.6867 | 0.0 | 2.583e+07 | 0.00256 | 1.484 | -- | -- | -- | 0.508 | 0.426 | inconsistent_unresolved |
| DTMB5415-03 | DTMB-5415 | -- | 1.3735 | 0.0 | 5.167e+07 | 0.002298 | 1.523 | -- | -- | -- | 0.508 | 0.426 | inconsistent_unresolved |
| DTMB5415-04 | DTMB-5415 | -- | 2.0602 | 0.0 | 7.750e+07 | 0.002162 | 1.757 | -- | -- | -- | 0.508 | 0.426 | inconsistent_unresolved |
| KVLCC2-01 | KVLCC2 | -- | 0.1285 | 0.0 | 2.023e+09 | 0.001405 | 1.993 | -- | -- | -- | 0.8098 | 0.7758 | inconsistent_unresolved |
| KVLCC2-02 | KVLCC2 | -- | 0.1377 | 0.0 | 2.168e+09 | 0.001394 | 2.153 | -- | -- | -- | 0.8098 | 0.7758 | inconsistent_unresolved |
| KVLCC2-03 | KVLCC2 | -- | 0.1423 | 0.0 | 2.240e+09 | 0.001388 | 2.233 | -- | -- | -- | 0.8098 | 0.7758 | inconsistent_unresolved |
| KVLCC2-04 | KVLCC2 | -- | 0.1469 | 0.0 | 2.312e+09 | 0.001383 | 2.314 | -- | -- | -- | 0.8098 | 0.7758 | inconsistent_unresolved |

Cases: 12 total, 8 with ERROR-level contradictions; status counts: {'verified': 1, 'out_of_validity_range': 3, 'inconsistent_unresolved': 8}

Findings: {"cb_closure:ERROR": 8, "froude_range:WARN": 6}
