# 耐波（seakeeping）端点独立审计 — m1809

端点：`/calculations/seakeeping`  ·  总证据等级：**MOCK**

硬 FAIL 判据：H-A33, H-FREQ, H-HEADING, H-LONGWAVE, H-ROLL-SYM

## 逐案例判据

| 案例 | 判据 | 判定 | 实测 |
|------|------|------|------|
| M100 | G-CB | PASS | 0.012 |
| M100 | H-LONGWAVE | FAIL | {"period_s": 30.0, "lambda_over_L": 14.05, "api_rao": 1.3, "kernel_rao": 1.0289653039439979, "exact_limit": 1.0, "error": 0.2634} |
| M100 | H-FREQ | FAIL | {"max": 1.3, "min": 1.3, "ratio": 1.0, "periods": [4.0, 6.0, 8.0, 10.0, 12.0, 14.0, 16.0, 20.0, 24.0, 30.0]} |
| M100 | H-HEADING | FAIL | {"180.0": [1.3, 1.3, 1.3, 1.3, 1.3, 1.3, 1.3, 1.3, 1.3, 1.3], "135.0": [1.3, 1.3, 1.3, 1.3, 1.3, 1.3, 1.3, 1.3, 1.3, 1.3], "90.0": [1.3, 1.3, 1.3, 1.3, 1.3, ... |
| M100 | H-ROLL-SYM | FAIL | {"api_roll_rao_max_head_seas": 2.0, "kernel_fk_roll_moment_180": 4.565739494416596e-11, "kernel_fk_roll_moment_90": 298464.5164655956} |
| M100 | H-LINEAR | PASS | {"wave_heights": [1.0, 2.0, 4.0], "rao": [1.3, 1.3, 1.3], "spread": 0.0} |
| M100 | H-A33 | FAIL | {"kernel_kg": 6141533.390345142, "api_kg": 3920625.0, "error": 0.3616, "kernel_ratio_to_displacement": 1.3305} |
| KVLCC2-like | G-CB | PASS | 0.225 |
| KVLCC2-like | H-LONGWAVE | PASS | {"period_s": 30.0, "lambda_over_L": 4.39, "api_rao": 1.27, "kernel_rao": 1.1598443257160362, "exact_limit": 1.0, "error": 0.095} |
| KVLCC2-like | H-FREQ | FAIL | {"max": 1.27, "min": 1.27, "ratio": 1.0, "periods": [4.0, 6.0, 8.0, 10.0, 12.0, 14.0, 16.0, 20.0, 24.0, 30.0]} |
| KVLCC2-like | H-HEADING | FAIL | {"180.0": [1.27, 1.27, 1.27, 1.27, 1.27, 1.27, 1.27, 1.27, 1.27, 1.27], "135.0": [1.27, 1.27, 1.27, 1.27, 1.27, 1.27, 1.27, 1.27, 1.27, 1.27], "90.0": [1.27,... |
| KVLCC2-like | H-ROLL-SYM | FAIL | {"api_roll_rao_max_head_seas": 2.0, "kernel_fk_roll_moment_180": 1.3032939979476991e-09, "kernel_fk_roll_moment_90": 4817480.521408907} |
| KVLCC2-like | H-LINEAR | PASS | {"wave_heights": [1.0, 2.0, 4.0], "rao": [1.27, 1.27, 1.27], "spread": 0.0} |
| KVLCC2-like | H-A33 | FAIL | {"kernel_kg": 464432582.38265294, "api_kg": 272371630.0, "error": 0.4135, "kernel_ratio_to_displacement": 1.4514} |
| S30 | G-CB | PASS | 0.162 |
| S30 | H-LONGWAVE | FAIL | {"period_s": 30.0, "lambda_over_L": 46.82, "api_rao": 1.33, "kernel_rao": 1.0140611015621515, "exact_limit": 1.0, "error": 0.3116} |
| S30 | H-FREQ | FAIL | {"max": 1.33, "min": 1.33, "ratio": 1.0, "periods": [4.0, 6.0, 8.0, 10.0, 12.0, 14.0, 16.0, 20.0, 24.0, 30.0]} |
| S30 | H-HEADING | FAIL | {"180.0": [1.33, 1.33, 1.33, 1.33, 1.33, 1.33, 1.33, 1.33, 1.33, 1.33], "135.0": [1.33, 1.33, 1.33, 1.33, 1.33, 1.33, 1.33, 1.33, 1.33, 1.33], "90.0": [1.33,... |
| S30 | H-ROLL-SYM | FAIL | {"api_roll_rao_max_head_seas": 2.0, "kernel_fk_roll_moment_180": 1.3135070091248927e-11, "kernel_fk_roll_moment_90": 58186.78024084636} |
| S30 | H-LINEAR | PASS | {"wave_heights": [1.0, 2.0, 4.0], "rao": [1.33, 1.33, 1.33], "spread": 0.0} |
| S30 | H-A33 | FAIL | {"kernel_kg": 450182.1193554481, "api_kg": 287512.0, "error": 0.3613, "kernel_ratio_to_displacement": 1.3319} |

## 参考核（本仓面元法）

- **M100**：A33 = 6.1415e+06 kg，升沉固有周期 T_n = 6.85 s，C = 9.0546e+06 N/m
- **KVLCC2-like**：A33 = 4.6443e+08 kg，升沉固有周期 T_n = 14.31 s，C = 1.5121e+08 N/m
- **S30**：A33 = 4.5018e+05 kg，升沉固有周期 T_n = 4.84 s，C = 1.3280e+06 N/m

## 判据说明（可第三方复算）

- `panel_laplace_neumann` → REAL
- `added_mass_submerged_ellipse` → REAL
- `added_mass_fs_heave_semicircle` → REAL
- `added_mass_fs_heave_general_section` → REAL
- `radiation_damping` → WITHHELD
- `heave_rao_peak_amplitude` → WITHHELD
- `heave_long_wave_limit` → REAL
- `heave_natural_period` → REAL
- `roll_fk_excitation_head_seas` → REAL
