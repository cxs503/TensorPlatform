# M1775 — SUBOFF 阻力计算全面验证（paper 席自找活）

**任务**：tp-paper-1 self-start（mission 1775）· 仓库 TensorPlatform · 分支 `agent/m1775`
**类型**：验证欠账（把已合并的 REAL 能力对文献/解析基准复核）
**容量**：本机 load15 ≈ 118（nproc=128），全程仅纯 Python 数值复核 + matplotlib 出图，未起任何重算力进程。

## ① 案例介绍（物理背景/几何/参数表）

审计对象是平台 `calc.resistance` 能力（证据等级 **REAL**），实现位于 `backend/api/calculations.py`。
本次聚焦 **DARPA SUBOFF** 轴对称回转体模型——这是 ITTC 推荐的 CFD/实验验证基准船型之一，
其阻力特性在 Roddy (1990) 中有完整的实验数据。

SUBOFF 模型几何参数（经 m1561 基准裁决，DERIVED 状态）：

| 参数 | 值 | 来源 |
|---|---|---|
| 船长 L | 4.356 m | Roddy 1990 |
| 最大直径/型宽 B | 0.508 m | Roddy 1990 |
| 吃水 T | 0.476 m | Roddy 1990 |
| 方形系数 Cb | 0.542 | 几何派生 |
| 湿表面积 S | 5.42 m² | Roddy 1990（几何校验值） |
| 流体密度 ρ | 998.0 kg/m³ | 淡水 20°C（ITTC 7.5-02-01-01） |
| 运动黏度 ν | 1.004e-6 m²/s | 淡水 20°C |

平台阻力计算方法：
- **摩擦阻力**：ITTC-1957 平板摩擦线 `Cf = 0.075/(log10(Re)−2)²`
- **剩余阻力**：经验拟合 `Cr = 0.002(1+2Cb)Fn^2.5`（Fn > 0.05）
- **总阻力**：`Rt = Rf + Rform + Rr`，`Ct = Rt/(½ρSV²)`

验证基准：Roddy (1990) "DARPA SUBOFF Model Experiments", DTRC-90/012，
经 m1561 基准裁决引擎 adjudication 后的 4 行 DERIVED 数据。

## ② 怎么做的（方法/流程/关键设置/工具链）

1. **读实现** → 定位 `backend/api/calculations.py` 中的阻力计算逻辑，确认公式与 m1384 报告一致。
2. **独立复核器** → 写 `tools/verif/verify_resistance_m1775.py`，**不复制公式**，而是从实现文件直接提取逻辑
   （通过 importlib 绕过数据库依赖），确保测的是线上实现本身。
3. **四层验证**：
   - L1: ITTC-1957 摩擦线闭式验证（Re = 1e5 … 1e10，6 点）
   - L2: SUBOFF 四工况 Ct/Rt 计算 vs 实验基准
   - L3: 湿表面积经验式偏差量化（圆柱/椭球闭式对比）
   - L4: Cr 经验式在不同 Fn 段的行为分析
4. **测试** → `tests/test_resistance_m1775_verification.py`（21 项，离线可跑，不依赖 API）。
5. **出图** → matplotlib 生成 4 张 PNG（Ct-Fn 对比、误差柱状图、湿表面积偏差、Cr-Fn 曲线）。
6. **所有数字程序化注入**，零手抄。

工具链：Python 3.11 + numpy + matplotlib + pytest。

## ③ 计算结果

### 3.1 ITTC-1957 摩擦线：与闭式完全一致

| Re | Cf 闭式 | Cf 计算 | 相对误差 | 是否 <3% |
|---|---|---|---|---|
| 1e+05 | 8.333330e-03 | 8.333333e-03 | 4.00e-05% | ✅ 是 |
| 1e+06 | 4.687500e-03 | 4.687500e-03 | 0.00e+00% | ✅ 是 |
| 1e+07 | 3.000000e-03 | 3.000000e-03 | 0.00e+00% | ✅ 是 |
| 1e+08 | 2.083330e-03 | 2.083333e-03 | 1.60e-04% | ✅ 是 |
| 1e+09 | 1.530610e-03 | 1.530612e-03 | 1.47e-04% | ✅ 是 |
| 1e+10 | 1.171880e-03 | 1.171875e-03 | 4.27e-04% | ✅ 是 |

**该分支无欠账**：最大相对偏差 4.27e-04%（仅来自响应序列化 round(…, 10)）。

### 3.2 SUBOFF 阻力：逐工况对比（湿表面积取几何校验值 S = 5.42 m²）

| Case | V [m/s] | Fn | Ct_exp | Ct_calc | 误差 | Rt_calc [N] | Rt_exp [N] | 是否 <3% | 是否 <15% |
|---|---|---|---|---|---|---|---|---|---|
| SUBOFF-01 | 3.0 | 0.459 | 0.0038 | 0.003462 | **−8.9%** | 84.27 | 92.52 | ❌ 否 | ✅ 是 |
| SUBOFF-02 | 4.0 | 0.612 | 0.0040 | 0.003953 | **−1.2%** | 171.05 | 173.13 | ✅ 是 | ✅ 是 |
| SUBOFF-03 | 5.0 | 0.765 | 0.0042 | 0.004766 | **+13.5%** | 322.27 | 284.04 | ❌ 否 | ✅ 是 |
| SUBOFF-04 | 6.0 | 0.918 | 0.0044 | 0.005921 | **+34.6%** | 576.53 | 428.49 | ❌ 否 | ❌ **>15%** |

**是否 <3%：否。** 4 例中仅 1 例 <3%（SUBOFF-02，−1.2%）；3/4 例 <15%；
SUBOFF-04（Fn = 0.918）达 **+34.6%，超过 15% 不可接受线**。

### 3.3 湿表面积经验式偏差

| 形状 | L [m] | B [m] | Cb | 闭式 S [m²] | 经验式 S [m²] | 误差 |
|---|---|---|---|---|---|---|
| 圆柱 (Cb=π/4) | 4.356 | 0.508 | 0.7854 | 6.9519 | 4.8409 | **−30.4%** |
| 长椭球 b/a=0.25 | 4.356 | 0.508 | 0.5236 | 5.4938 | 4.3859 | **−20.2%** |
| 长椭球 b/a=0.15 | 4.356 | 0.3048 | 0.5236 | 3.2836 | 2.6316 | **−19.9%** |

经验式对回转体**系统偏小**，且偏差随胖瘦变化（不是常数偏置，无法用单系数校正）。

### 3.4 Cr 经验式行为

`Cr = 0.002(1+2Cb)Fn^2.5` 在 Fn > 0.7 后快速增长：

| Fn | Cr | 贡献占比 (Rr/Rt) |
|---|---|---|
| 0.459 | 0.000316 | 9.1% |
| 0.612 | 0.000631 | 15.9% |
| 0.765 | 0.001116 | 23.4% |
| 0.918 | 0.001811 | 30.6% |

在高 Fn 段，Cr 主导总阻力，其无文献出处的拟合形式导致误差发散。

## ④ 结果图

### 图1：Ct vs Fn 对比

![Ct vs Fn](artifacts/verif/m1775/m1775_ct_vs_fn.png)

SUBOFF 四工况 Ct 随 Fn 变化：实验值（红圈）与平台计算值（蓝方块）对比，标注逐点误差。
Fn=0.612 处误差最小（−1.2%），Fn=0.918 处误差最大（+34.6%）。

### 图2：误差柱状图

![Error Bars](artifacts/verif/m1775/m1775_error_bars.png)

逐点绝对误差与 3%/15% 阈值对比。绿色 = <15%，红色 = >15%。

### 图3：湿表面积偏差

![Wetted Surface Bias](artifacts/verif/m1775/m1775_wetted_surface_bias.png)

经验式对三种闭式形状的偏差，全部远超 ±3% 线。

### 图4：Cr 经验式行为

![Cr vs Fn](artifacts/verif/m1775/m1775_cr_vs_fn.png)

Cr 经验式随 Fn 的变化曲线及 SUBOFF 工况点标注。Fn > 0.7 后 Cr 快速增长。

## ⑤ 注意点（踩过的坑/局限/适用范围）

### 已确认的达标项
- **ITTC-1957 摩擦线**：闭式实现完全正确，误差 < 5e-04%。
- **几何输入通道**：`wetted_surface_m2` 参数有效，使用几何校验值后 Ct 计算不受 S 偏差影响
  （因为 Ct = Cf(1+k) + Cr，与 S 无关）。

### 未修复 / 局限（如实声明）
1. **Cr 经验式无文献出处**：`Cr = 0.002(1+2Cb)Fn^2.5` 是曲线拟合，Fn ≥ 0.9 时误差 34.6%。
   本次**未**替换为 Holtrop-1984 完整式（需要 LCB、Cp、Cm、球鼻等几何量，平台当前缺少
   经校验的几何输入）。
2. **湿表面积经验式偏差**：对回转体系统偏小 −20% 至 −30%，且非常数偏置。
   平台已提供 `wetted_surface_m2` 输入通道，但 Ct 计算本身不受 S 影响（Ct 是归一化系数）。
3. **SUBOFF 适用范围**：SUBOFF 为轴对称回转体（Cb ≈ 0.54），Holtrop 方法原为商船
   （Cb > 0.55）设计。本验证仅记录误差，不宣称 calc.resistance 对 SUBOFF 达到 <3% 精度。
4. **DTMB-5415 / KVLCC2**：基准数据均为 WITHHELD 状态（数据自洽性不满足），
   本次未覆盖。

### 适用范围声明
`calc.resistance` 能力在以下条件下可用：
- **Fn < 0.8**：误差 −8.9% … +13.5%（<15% 可接受线）
- **Fn ≥ 0.9**：误差 >15%，**不可接受**
- **湿表面积**：应使用几何校验值传入 `wetted_surface_m2`，而非依赖经验式

## ⑥ 误差判定（逐项，显式回答「是否 <3%」）

| 关键量 | 基准 | 误差 | 是否 <3% |
|---|---|---|---|
| Cf（Re = 1e5…1e10） | ITTC-1957 闭式 | ≤ 4.27e-04% | **✅ 是** |
| 湿表面积（圆柱 Cb=π/4） | πDL | −30.4% | ❌ 否 |
| 湿表面积（椭球 b/a=0.25） | 闭式 | −20.2% | ❌ 否 |
| 湿表面积（椭球 b/a=0.15） | 闭式 | −19.9% | ❌ 否 |
| Ct（SUBOFF-01，Fn 0.459） | 实验 Ct = 0.0038 | −8.9% | ❌ 否 |
| Ct（SUBOFF-02，Fn 0.612） | 实验 Ct = 0.0040 | −1.2% | **✅ 是** |
| Ct（SUBOFF-03，Fn 0.765） | 实验 Ct = 0.0042 | +13.5% | ❌ 否 |
| Ct（SUBOFF-04，Fn 0.918） | 实验 Ct = 0.0044 | +34.6% | ❌ 否（>15% 不可接受） |

**总结论**：ITTC-1957 摩擦线实现达标；湿表面积经验式与高 Fn 剩余阻力**不达标**。
SUBOFF 四工况中 3/4 在 15% 线内，1/4 超出。该能力应维持 REAL 但精度标签标注
「Fn<0.8：−8.9%…+13.5%；Fn≥0.9：>15% 不可接受」。

## ⑦ 复现

```bash
# 验证脚本（出图 + JSON）
python3 tools/verif/verify_resistance_m1775.py

# 测试（21 项）
python3 -m pytest tests/test_resistance_m1775_verification.py -v

# 产物
artifacts/verif/m1775/m1775_verification_results.json
artifacts/verif/m1775/m1775_ct_vs_fn.png
artifacts/verif/m1775/m1775_error_bars.png
artifacts/verif/m1775/m1775_wetted_surface_bias.png
artifacts/verif/m1775/m1775_cr_vs_fn.png
```

## 参考文献

1. Roddy, J.D. (1990). "DARPA SUBOFF Model Experiments", David Taylor Research Center, DTRC-90/012.
2. ITTC (1957). "Report of the Resistance Committee", 9th ITTC, Paris.
3. Holtrop, J. & Mennen, G.G.J. (1982). "An Approximate Power Prediction Method", Int. Shipbuilding Progress, 29.
4. m1384 报告：阻力/稳性计算器验证欠账核查与修复（本仓库 `docs/reports/m1384_resistance_stability_verification.md`）
5. m1561 基准裁决：`docs/reports/m1561_benchmark_canon_adjudication.md`
