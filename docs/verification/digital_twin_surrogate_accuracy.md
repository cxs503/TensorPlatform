# Digital-twin surrogate accuracy audit (mission m1647)

**Scope** — the five PCE surrogates of `backend/services/digital_twin/surrogate_registry.py` that serve `services/digital_twin/online_predictor.py` (resistance, seakeeping, manoeuvring, stability, fatigue), judged against their reference physics models on an **independent uniform hold-out** in linear output space.

**Hull** — L=180.0 m, B=32.0 m, T=11.5 m, Cb=0.8, KG=10.0 m (geometry closure `{'volume_numeric_m3': 52991.673641972855, 'volume_analytic_m3': 52992.0, 'volume_rel_error': 6.158628229639072e-06, 'awp_numeric_m2': 4607.971621041117, 'awp_analytic_m2': 4608.0, 'awp_rel_error': 6.158628229810701e-06, 'IT_rel_error': 2.575164152390132e-09, 'IL_numeric_m4': 9554765.45835143, 'IL_rel_error': 2.4054411757256184e-05, 'Cb_input': 0.8, 'Cb_numeric': 0.7999950730974162, 'exponent_p': 0.45315637712832313}`).

**Design** — hold-out points per surrogate: `{'resistance': 192, 'seakeeping': 64, 'manoeuvring': 24, 'stability': 48, 'fatigue': 192}`, seed 20261101, thresholds J1 p95<=15%, J2 median<=5%, J3 R²>=0.95, J6 max |error| <=5% of range, J5 surrogate latency<=2.0 ms.

**Verdict** — **FAIL (honest)**: 1/5 surrogates pass every judge; failing judge instances: ['seakeeping.heave_rms_m.J1', 'seakeeping.heave_rms_m.J2', 'seakeeping.heave_rms_m.J6', 'seakeeping.pitch_rms_deg.J1', 'seakeeping.pitch_rms_deg.J2', 'seakeeping.pitch_rms_deg.J6', 'manoeuvring.speed_loss_pct.J6', 'manoeuvring.steady_yaw_rate_deg_s.J1', 'manoeuvring.steady_yaw_rate_deg_s.J6', 'stability.GZ_m.J1', 'stability.KN_m.J1', 'fatigue.damage_per_day.J1', 'fatigue.damage_per_day.J2', 'fatigue.damage_per_day.J3', 'fatigue.damage_per_day.J6'].

## Reproduction

```bash
python3 tools/verify_digital_twin_surrogates.py
python3 -m pytest tests/test_digital_twin_surrogate_accuracy.py -q
```

Ledger: `artifacts/m1647/digital_twin_surrogate_audit.json`.

## Per-surrogate results

| surrogate | degree | basis | n_train | p95 worst output | median worst output | speedup | J4 guard | verdict |
|---|---|---|---|---|---|---|---|---|
| `resistance` | 5 | 56 | 512 | 2.56% (`P_D_kW`) | 0.19% (`R_calm_kN`) | 0.0x | ok | PASS |
| `seakeeping` | 4 | 70 | 320 | 390.92% (`heave_rms_m`) | 17.76% (`pitch_rms_deg`) | 2.0x | ok | FAIL |
| `manoeuvring` | 2 | 6 | 48 | 20.84% (`steady_yaw_rate_deg_s`) | 2.60% (`steady_yaw_rate_deg_s`) | 534.3x | ok | FAIL |
| `stability` | 2 | 6 | 48 | 58.77% (`GZ_m`) | 2.25% (`GZ_m`) | 81.9x | ok | FAIL |
| `fatigue` | 3 | 35 | 160 | 127.58% (`damage_per_day`) | 31.35% (`damage_per_day`) | 0.0x | ok | FAIL |

### `resistance` — reference `holtrop_mennen_1982 + lindqvist_1989`

Latency: surrogate 1.108 ms vs reference 0.04 ms (0.0x). Hold-out: 192 points.

| output | n | R² (lin) | median | p95 | max | max abs | max abs / range | J1 | J2 | J3 | J6 |
|---|---|---|---|---|---|---|---|---|---|---|---|
| `R_calm_kN` | 192 | 1.00000 | 0.19% | 1.26% | 12.6% | 5.324 | 0.31% | P | P | P | P |
| `R_ice_kN` | 189 | 1.00000 | 0.02% | 1.06% | 42.3% | 1.996 | 0.08% | P | P | P | P |
| `R_total_kN` | 192 | 1.00000 | 0.06% | 0.30% | 1.0% | 5.144 | 0.14% | P | P | P | P |
| `P_D_kW` | 192 | 1.00000 | 0.11% | 2.56% | 6.8% | 35.24 | 0.08% | P | P | P | P |

Worst p95 point (`P_D_kW`): inputs `{'V': 1.164469, 'ice_concentration': 0.752444, 'ice_thickness': 0.162649}`, reference 389.46, surrogate 362.97.

### `seakeeping` — reference `2dof_modal_strip + gerritsma_added_resistance`

Latency: surrogate 1.581 ms vs reference 3.18 ms (2.0x). Hold-out: 64 points.

| output | n | R² (lin) | median | p95 | max | max abs | max abs / range | J1 | J2 | J3 | J6 |
|---|---|---|---|---|---|---|---|---|---|---|---|
| `heave_rms_m` | 63 | 0.99170 | 8.00% | 390.92% | 3057.2% | 0.2443 | 7.81% | F | F | P | F |
| `pitch_rms_deg` | 64 | 0.95305 | 17.76% | 378.17% | 1049.3% | 2.216 | 26.20% | F | F | P | F |
| `R_aw_kN` | 62 | 0.99999 | 0.17% | 6.82% | 17.7% | 0.7093 | 0.46% | P | P | P | P |

Worst p95 point (`heave_rms_m`): inputs `{'Hs': 1.435422, 'Tp': 3.040626, 'heading_deg': 172.206407, 'V': 2.931122}`, reference 0.0056484, surrogate 0.17833.

### `manoeuvring` — reference `mmg_3dof_crossflow_lift`

Latency: surrogate 0.344 ms vs reference 183.91 ms (534.3x). Hold-out: 24 points.

| output | n | R² (lin) | median | p95 | max | max abs | max abs / range | J1 | J2 | J3 | J6 |
|---|---|---|---|---|---|---|---|---|---|---|---|
| `turning_diameter_L` | 24 | 0.99261 | 0.96% | 2.36% | 3.1% | 0.1551 | 4.65% | P | P | P | P |
| `advance_L` | 24 | 0.99389 | 0.85% | 1.61% | 2.1% | 0.1028 | 4.77% | P | P | P | P |
| `speed_loss_pct` | 24 | 0.97011 | 0.92% | 2.90% | 3.4% | 2.132 | 11.47% | P | P | P | F |
| `steady_yaw_rate_deg_s` | 24 | 0.98729 | 2.60% | 20.84% | 29.0% | 0.03106 | 7.48% | F | P | P | F |

Worst p95 point (`steady_yaw_rate_deg_s`): inputs `{'V': 2.387578, 'rudder_deg': 21.369836}`, reference 0.10695, surrogate 0.138.

### `stability` — reference `numerical_kn_gz_hydrostatics`

Latency: surrogate 0.312 ms vs reference 25.53 ms (81.9x). Hold-out: 48 points.

| output | n | R² (lin) | median | p95 | max | max abs | max abs / range | J1 | J2 | J3 | J6 |
|---|---|---|---|---|---|---|---|---|---|---|---|
| `GZ_m` | 48 | 0.99865 | 2.25% | 58.77% | 365.0% | 0.08181 | 3.18% | F | P | P | P |
| `KN_m` | 48 | 0.99965 | 0.27% | 19.96% | 149.9% | 0.1058 | 2.21% | F | P | P | P |
| `GM_m` | 48 | 1.00000 | 0.00% | 0.00% | 0.0% | 3.997e-15 | 0.00% | P | P | P | P |

Worst p95 point (`GZ_m`): inputs `{'heel_deg': 0.35467, 'KG': 7.776543}`, reference 0.022416, surrogate -0.059391.

### `fatigue` — reference `spectral_miner_dnv_c203_curveF`

Latency: surrogate 0.704 ms vs reference 0.03 ms (0.0x). Hold-out: 192 points.

| output | n | R² (lin) | median | p95 | max | max abs | max abs / range | J1 | J2 | J3 | J6 |
|---|---|---|---|---|---|---|---|---|---|---|---|
| `damage_per_day` | 122 | -9.60924 | 31.35% | 127.58% | 664.5% | 0.9125 | 664.47% | F | F | F | F |
| `sigma_rms_MPa` | 190 | 1.00000 | 0.00% | 0.00% | 0.0% | 4.619e-14 | 0.00% | P | P | P | P |

Worst p95 point (`damage_per_day`): inputs `{'Hs': 7.94517, 'Tp': 4.340134, 'ice_concentration': 0.123058, 'ice_thickness': 1.030259}`, reference 0.13732, surrogate 1.0498.

## Figures

![parity](figs/digital_twin_surrogate_parity.png)

![error bars](figs/digital_twin_surrogate_error_bars.png)

## Notes / limitations

* The reference model *is* the ground truth here: this audit measures surrogate-vs-model fidelity, **not** physical accuracy against model tests (that remains WITHHELD — no tank data is bundled).
* The reference models are the platform's own `holtrop_mennen_1982 + lindqvist_1989`, `2dof_modal_strip + gerritsma`, `mmg_3dof_crossflow_lift`, `numerical_kn_gz_hydrostatics` and `spectral_miner_dnv_c203_curveF` implementations, so a shared modelling error would be invisible.
* Relative error is undefined where the reference crosses zero (`GZ_m` near 0 deg heel); those points are excluded from J1/J2 and the range-normalised absolute error J6 is used instead.
* The hold-out is uniform over the declared input box; it therefore weights rare combinations (e.g. 8 m seas with 3 s peak period) equally with realistic ones.  Error at the box corners is the price of a domain-wide guarantee; a sea-state-weighted study is left open.

