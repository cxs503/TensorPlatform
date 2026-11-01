# calc.resistance 独立复核（m1536，验证欠账线）

- 复核对象：船舶总体性能平台 `POST /api/v1/calculations/resistance`（自述方法 `ITTC-57摩擦阻力 + Holtrop剩余阻力`）
- 基准：ITTC-1957 解析线 + Holtrop-Mennen(1984) 忠实实现 + 公开基准湿表面积 + IMO EEDI 参考线（MEPC.203(62)）
- 判据：|相对误差| > 15% 记 FAIL（平台精度标准）

## 1. 复核矩阵（逐工况）

| 船型 | V[kn] | Fn | 参考 Ct | 平台 Ct | 参考 RT[kN] | 平台 RT[kN] | RT 误差 | 参考 PE[MW] | 平台 PE[MW] | PE 误差 | 判决 |
|---|---|---|---|---|---|---|---|---|---|---|---|
| KVLCC2 | 12.0 | 0.1102 | 0.00215 | 0.00620 | 1113 | 3340 | 200% | 6.87 | 20.62 | 200% | FAIL |
| KVLCC2 | 13.5 | 0.1239 | 0.00213 | 0.00637 | 1396 | 4345 | 211.2% | 9.70 | 30.17 | 211.2% | FAIL |
| KVLCC2 | 15.0 | 0.1377 | 0.00213 | 0.00654 | 1719 | 5512 | 220.6% | 13.27 | 42.53 | 220.6% | FAIL |
| KVLCC2 | 16.5 | 0.1515 | 0.00215 | 0.00672 | 2098 | 6851 | 226.5% | 17.81 | 58.15 | 226.5% | FAIL |
| KCS | 18.0 | 0.1949 | 0.00225 | 0.00679 | 876 | 2877 | 228.4% | 8.11 | 26.64 | 228.4% | FAIL |
| KCS | 21.0 | 0.2274 | 0.00252 | 0.00820 | 1332 | 4732 | 255.4% | 14.38 | 51.12 | 255.4% | FAIL |
| KCS | 24.0 | 0.2599 | 0.00291 | 0.00981 | 2007 | 7394 | 268.4% | 24.78 | 91.29 | 268.4% | FAIL |
| AFRAMAX-110k | 13.0 | 0.1367 | 0.00230 | 0.00670 | 775 | 2339 | 201.7% | 5.18 | 15.64 | 201.7% | FAIL |
| AFRAMAX-110k | 15.0 | 0.1577 | 0.00235 | 0.00698 | 1054 | 3242 | 207.6% | 8.13 | 25.02 | 207.6% | FAIL |

## 2. 分项误差（KVLCC2 @ 15 kn 代表工况）

| 量 | 平台 | 参考 | 单位 | 误差 | 参考依据 | 判决 |
|---|---|---|---|---|---|---|
| geometry Cb=vol/(LBT) [KVLCC2] | 0.8098 | 0.8098 | - | -0% | hull particulars self-consistency | PASS |
| wetted surface S | 2.76e+04 | 2.649e+04 | m2 | 4.202% | Holtrop S correlation with published Cm/Cwp | PASS |
| wetted surface S | 2.76e+04 | 2.635e+04 | m2 | 4.776% | Denny-Mumford correlation (independent estimator) | PASS |
| Reynolds number Re | 2.075e+09 | 2.075e+09 | - | 0% | V L / nu (nu=1.19e-6 m^2/s) | PASS |
| Froude number Fn | 0.1377 | 0.1377 | - | 0% | V / sqrt(g L) | PASS |
| friction coefficient Cf | 0.001401 | 0.001401 | - | 0.01% | ITTC-1957 line (exact analytic) | PASS |
| residuary+form coefficient (Cr+0.4e-3 vs kCf+Cw+Ca) | 0.005144 | 0.0007264 | - | 608.1% | Holtrop-1984: k Cf + Cw + Ca | FAIL |
| platform Cr vs Holtrop Cw (wave-only, informational) | 0.004744 | 2.782e-05 | - | 1.696e+04% | Holtrop-1984 Cw = Rw/(0.5 rho V^2 S); platform Cr is its whole residual coefficient, so this row is informational, not a like-for-like gate | FAIL |
| total resistance coefficient Ct | 0.006545 | 0.002127 | - | 207.7% | Holtrop-1984 (1+k)Cf + Cw + Ca | FAIL |
| frictional resistance RF | 1517 | 1132 | kN | 33.96% | 0.5 rho V^2 S Cf (bare hull) | FAIL |
| residuary resistance RR | 3996 | 587.2 | kN | 580.5% | Holtrop-1984 k Rf + Rw + Ra | FAIL |
| total resistance RT | 5512 | 1719 | kN | 220.6% | Holtrop-1984 total | FAIL |
| effective power PE | 42.53 | 13.27 | MW | 220.6% | RT * V | FAIL |

## 3. 几何与输入校验

- 位移一致性探针（KVLCC2 @ 15.0 kn）：RT = 5512.42 kN （正常）vs 5512.42 kN（位移×10）→ **FAIL: endpoint ignores displacement (no Cb/vol cross-validation)**

- Cb 自洽性：Cb=0.8098 vs vol/(LBT)=0.8098 （-0%）


## 4. 物理/规范可行性交叉核对（KVLCC2 15 kn）

- IMO EEDI 参考线（tanker, DWT=312622 t）= 2.537 g CO2/(t·nm)；对应所需 PME = 21833 kW（V=15.0 kn）
- Holtrop-1984 → PE = 13.27 MW，折算 EEDI = 2.141（0.84× 参考线）
- 平台 → PE = 42.53 MW，折算 EEDI = 6.865（2.71× 参考线）→ platform result implies an attained EEDI far above the IMO reference line (physically implausible for a modern VLCC)


## 5. 参考数据集自洽性（backend/data/benchmark_reference.json）

- 4/12 cases have experimental_ct and experimental_rt_n that are mutually inconsistent (>10%): the two columns cannot both be experimental data

| case | V[m/s] | S[m²] | Ct | Rt[N] | Rt implied by Ct [N] | 误差 | 判决 |
|---|---|---|---|---|---|---|---|
| SUBOFF-01 | 3.0 | 5.42 | 0.0038 | 12.5 | 92.497 | -86.49% | FAIL |
| SUBOFF-02 | 4.0 | 5.42 | 0.004 | 21.0 | 173.093 | -87.87% | FAIL |
| SUBOFF-03 | 5.0 | 5.42 | 0.0042 | 31.5 | 283.981 | -88.91% | FAIL |
| SUBOFF-04 | 6.0 | 5.42 | 0.0044 | 45.0 | 428.405 | -89.5% | FAIL |
| DTMB5415-01 | - | - | - | - | - | - | NOT_CHECKABLE |
| DTMB5415-02 | - | - | - | - | - | - | NOT_CHECKABLE |
| DTMB5415-03 | - | - | - | - | - | - | NOT_CHECKABLE |
| DTMB5415-04 | - | - | - | - | - | - | NOT_CHECKABLE |
| KVLCC2-01 | - | - | - | - | - | - | NOT_CHECKABLE |
| KVLCC2-02 | - | - | - | - | - | - | NOT_CHECKABLE |
| KVLCC2-03 | - | - | - | - | - | - | NOT_CHECKABLE |
| KVLCC2-04 | - | - | - | - | - | - | NOT_CHECKABLE |

## 6. 结论

复核判决：**不通过（FAIL）**。平台 `calc.resistance` 自述 `ITTC-57摩擦阻力 + Holtrop剩余阻力`，但 (1) 阻力系数的结构与其声称的 Holtrop 方法不符——残余阻力用分段拟合式 Cr_base = 0.005·C4，量级比 Holtrop-1984 的 Cw+kCf+Ca 高出数倍；(2) 全尺度 RT 相对误差落在 199.96% … 268.44% 区间，远超 15% 可接受门；(3) 端点接收 displacement 却完全不使用，几何 Cb/∇ 未经交叉校验，违反平台「几何必须先验证 Cb/L/B」的精度前置条件。建议：以 src/ship_verification/holtrop1984.py 作为参考实现替换端点阻力内核，并补 Cb ↔ vol/(LBT) 一致性门。（附加）平台自带基准数据集 backend/data/benchmark_reference.json 的 4/12 个算例中 experimental_ct 与 experimental_rt_n 互相矛盾（同一几何同一速度下 Rt ≠ 0.5ρV²SCt，偏差 7-10 倍），任何以 Rt 列做判据的门禁都不可信。
