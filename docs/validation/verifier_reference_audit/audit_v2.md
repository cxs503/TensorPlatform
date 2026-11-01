# Verifier question-bank reference audit

- bank: `tools/verifier/question_bank/questions.db`
- questions: **15**
- verdicts: PASS=8 WARN=7 FAIL=0
- grades: {'EMPIRICAL': 7, 'REAL': 8}

| question | family | grade | stored vs independent | worst invariant failure | verdict |
|---|---|---|---|---|---|
| Turbulent flat plate Cf at Re_L=1e+09 | prandtl_schlichting | EMPIRICAL | Cf: 0.0000% | — | **WARN** |
| Turbulent flat plate Cf at Re_L=1e+06 | prandtl_schlichting | EMPIRICAL | Cf: 0.0000% | — | **WARN** |
| Holtrop-1982 form factor: Bulk carrier Cb=0.7 | holtrop_mennen_form_factor | EMPIRICAL | 1+k: 0.0000% | — | **WARN** |
| Laminar flat plate Cf at Re_L=5e+05 | blasius_laminar | REAL | Cf: 0.0000% | — | **PASS** |
| Laminar flat plate Cf at Re_L=1e+05 | blasius_laminar | REAL | Cf: 0.0000% | — | **PASS** |
| Turbulent flat plate Cf at Re_L=1e+08 | prandtl_schlichting | EMPIRICAL | Cf: 0.0000% | — | **WARN** |
| ITTC-1957 Cf at Re=5e6 (medium model) | ittc_1957 | REAL | Cf: 0.0000% | — | **PASS** |
| ITTC-1957 Cf at Re=1e6 (low speed model) | ittc_1957 | REAL | Cf: 0.0000% | — | **PASS** |
| ITTC-1957 Cf at Re=1e9 (large ship) | ittc_1957 | REAL | Cf: 0.0000% | — | **PASS** |
| Turbulent flat plate Cf at Re_L=1e+07 | prandtl_schlichting | EMPIRICAL | Cf: 0.0000% | — | **WARN** |
| ITTC-1957 Cf at Re=1e8 (full ship) | ittc_1957 | REAL | Cf: 0.0000% | — | **PASS** |
| Laminar flat plate Cf at Re_L=1e+06 | blasius_laminar | REAL | Cf: 0.0000% | — | **PASS** |
| ITTC-1957 Cf at Re=1e7 (typical model) | ittc_1957 | REAL | Cf: 0.0000% | — | **PASS** |
| Holtrop-1982 form factor: Tanker Cb=0.8 | holtrop_mennen_form_factor | EMPIRICAL | 1+k: 0.0000% | — | **WARN** |
| Holtrop-1982 form factor: Series 60 Cb=0.6 | holtrop_mennen_form_factor | EMPIRICAL | 1+k: 0.0000% | — | **WARN** |

Mutation test (x1.2 reference corruption): all caught
