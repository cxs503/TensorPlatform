# M1384 — 阻力/稳性计算器验证欠账核查与修复（平台席自找活）

**任务**：tp-impl-3 self-start（mission 1384）· 仓库 TensorPlatform · 分支 `agent/m1384`
**类型**：验证欠账（把已上线的 REAL 能力对闭式解析基准复核）+ 由此暴露的数值缺陷最小修复
**容量**：本机 load15 ≥ 100（nproc=128），全程仅纯 Python 解析/数值复核，未起任何重算力进程。

## 1 案例介绍（物理背景与对象）

审计对象是平台 `calc.resistance` / `calc.stability` 两个证据等级被标为 **REAL** 的能力，实现位于
`backend/api/calculations.py`：

* **阻力**：`Rf = 1/2 ρ S V² Cf`，`Cf` 取 ITTC-1957 平板摩擦线 `0.075/(log10 Re − 2)²`；
  湿表面积用经验式 `S = π B L (0.5 + 0.25 Cb)`；剩余阻力系数 `Cr = 0.002(1+2Cb)Fn^2.5`（Fn>0.05）。
* **稳性**：舷侧直立（wall-sided）公式 `GZ = GM·sinφ + ½·BM·tan²φ·sinφ`。

计算基线参数（与平台默认 SUBOFF 工况一致，ρ = 998.0 kg/m³，ν = 1.004e-06 m²/s）：

| 参数 | 值 |
|---|---|
| 船长 L | 4.356 m |
| 最大直径/型宽 B | 0.508 m |
| 吃水 T | 0.476 m |
| 方形系数 Cb | 0.542 |
| 参考速度 | 3.0 m/s（Re = 1.30e7，Fn = 0.459） |
| 稳性基线 | 排水量 1000 t，GM = 1.0 m，BM = 3.0 m |

验证基准分两类：**(a) 闭式解析真值**（圆柱/椭球湿表面积、ITTC-1957 闭式、wall-sided 闭式）；
**(b) 平台自带参考数据集** `backend/data/benchmark_reference.json`（Roddy 1990 / ITTC V&V 2010 / SIMMAN 2008）。
所有数字由 `tools/verif/verify_ship_calcs.py` 程序化产出（`artifacts/verif/ship_calcs_verification.json`），本文表格由
`tools/verif/render_m1384_report.py` 从该 JSON 注入，无手抄。

## 2 怎么做的（方法与流程）

1. 读实现 → 定位两处「可判定量」：湿表面积 `S`（有闭式真值）、摩擦系数 `Cf`（有闭式真值）、`GZ`（有闭式真值 + 有效域约束）。
2. 写独立复核器 `tools/verif/verify_ship_calcs.py`：用 `importlib` 以文件路径加载 `backend/api/calculations.py`
   （绕开 `api/__init__` 的数据库依赖），直接调用真实端点协程，**不复制公式**，因此测的是线上实现本身。
3. 对每个可判定量构造闭式真值：
   * 圆柱侧面积 `S = πDL`（Cb = π/4）；
   * 长椭球 `S = 2πb² + 2πab·asin(e)/e`（Cb = π/6）；
   * ITTC-1957 闭式 `0.075/(log10 Re − 2)²`（Re ∈ [1e5, 1e10]）；
   * wall-sided 闭式同式（φ ≤ 25° 有效域内）。
4. 数据集自洽性审计：任何阻力基准行必须满足 `R = ½ ρ S V² Ct`，偏差 > 5% 即判定该行不能当判据。
5. 修复（最小、向后兼容）：见 §5。
6. 回归：`python3 -m pytest tools/verif/test_ship_calcs_verification.py -q` → **17 passed**。

## 3 计算结果

### 3.1 湿表面积：经验式 vs 闭式真值

| shape | L [m] | B [m] | Cb | closed-form S [m2] | empirical S [m2] | error [%] |
|---|---|---|---|---|---|---|
| cylinder | 4.356 | 0.508 | 0.7854 | 6.9519 | 4.8409 | -30.36 |
| prolate_spheroid_b/a=0.25 | 4.356 | 0.508 | 0.5236 | 5.4938 | 4.3859 | -20.17 |
| prolate_spheroid_b/a=0.15 | 4.356 | 0.3048 | 0.5236 | 3.2836 | 2.6316 | -19.86 |

经验式对回转体系统偏小，且偏差随胖瘦变化（圆柱 −30.4%，椭球 −20%）——**不是常数偏置，无法用一个系数校正**。

### 3.2 ITTC-1957 摩擦线：与闭式完全一致

| Re | Cf ITTC-1957 (closed form) | Cf API | rel. error [%] |
|---|---|---|---|
| 1e+05 | 8.333333e-03 | 8.333333e-03 | -4.00e-07 |
| 1e+06 | 4.687500e-03 | 4.687500e-03 | 0.00e+00 |
| 1e+07 | 3.000000e-03 | 3.000000e-03 | 0.00e+00 |
| 1e+08 | 2.083333e-03 | 2.083333e-03 | -1.60e-06 |
| 1e+09 | 1.530612e-03 | 1.530612e-03 | -2.93e-06 |
| 1e+10 | 1.171875e-03 | 1.171875e-03 | 0.00e+00 |

`Cf` 最大相对偏差 2.93e-06%（仅来自响应序列化 round(…, 10)），且随 Re 单调递减：
`monotone_decreasing = True`。**该分支无欠账**。

### 3.3 参考数据集自洽性审计（关键发现）

| case | type | V [m/s] | S [m2] | Ct ref | Rt ref [N] | Rt implied by Ct [N] | inconsistency [%] |
|---|---|---|---|---|---|---|---|
| SUBOFF-01 | SUBOFF | 3.0 | 5.42 | 0.0038 | 12.5 | 92.497 | -86.5 |
| SUBOFF-02 | SUBOFF | 4.0 | 5.42 | 0.004 | 21.0 | 173.093 | -87.9 |
| SUBOFF-03 | SUBOFF | 5.0 | 5.42 | 0.0042 | 31.5 | 283.981 | -88.9 |
| SUBOFF-04 | SUBOFF | 6.0 | 5.42 | 0.0044 | 45.0 | 428.405 | -89.5 |

SUBOFF 四行自相矛盾：其 `experimental_rt_n`（12.5–45 N）比自身 `Ct × ½ρSV²`（92–428 N）低 86–90%，
物理上不可能同时成立。**这四行不能作为通过判据**（此前 `backend/artifacts/benchmark_validation.json`
以此数据集判定 67% 误差，本质上是在与不可信参照比较）。

### 3.4 模型精度：与参考 Ct 的同口径比较（湿表面积取数据集几何值）

| case | Fn | Ct ref | Ct API (geometry S) | err [%] | verdict | Ct API (legacy S) | err [%] |
|---|---|---|---|---|---|---|---|
| SUBOFF-01 | 0.459 | 0.0038 | 0.003462 | -8.90 | <15% | 0.002822 | -25.74 |
| SUBOFF-02 | 0.612 | 0.0040 | 0.003953 | -1.18 | <3% | 0.003222 | -19.45 |
| SUBOFF-03 | 0.765 | 0.0042 | 0.004766 | +13.48 | <15% | 0.003885 | -7.50 |
| SUBOFF-04 | 0.918 | 0.0044 | 0.005921 | +34.58 | **>15% FAIL** | 0.004827 | +9.69 |

**是否 <3%：否。** 4 例中仅 1 例 <3%（SUBOFF-02，−1.18%）；3/4 例 <15%；
SUBOFF-04（Fn = 0.918）达 **+34.58%，超过 15% 不可接受线**，说明 `Cr` 经验式在高 Fn 段发散——其本身即
无文献出处的曲线拟合（模块 docstring 已如实标注）。

### 3.5 稳性：wall-sided 有效域外曾输出非物理值

修复前实测：`GZ(60°) = 4.76314 m`、`GZ(89°) = 4923.5 m`、`GZ(90°) = 4.001e+32 m`
（GM = 1.0 m 的千吨级艇，物理上 GZ 不可能超过约 B/2）。修复后（有效域内保持 wall-sided 不变）：

| heel [deg] | GZ [m] | gz_basis | formula_valid |
|---|---|---|---|
| 0 | 0 | wall_sided | True |
| 5 | 0.088156 | wall_sided | True |
| 10 | 0.181747 | wall_sided | True |
| 15 | 0.286693 | wall_sided | True |
| 20 | 0.409983 | wall_sided | True |
| 25 | 0.560461 | wall_sided | True |
| 30 | 0.5 | linear_fallback | False |
| 40 | 0.642788 | linear_fallback | False |
| 50 | 0.766044 | linear_fallback | False |
| 60 | 0.866025 | linear_fallback | False |
| 75 | 0.965926 | linear_fallback | False |
| 85 | 0.996195 | linear_fallback | False |
| 89 | 0.999848 | linear_fallback | False |

`heel_90_rejected = True`，`all_finite = True`，
`all_bounded_by_gm = True`。

## 4 结果图

1. `artifacts/verif/m1384_gz_vs_heel.png` — GZ 随横倾角（对数纵轴）：有效域外单元项使 GZ 冲出物理包络（虚线 B/2 = 0.254 m），修复后线性回退把值约束在 GM 以内。
2. `artifacts/verif/m1384_wetted_surface_bias.png` — 三种闭式形状的湿表面积偏差（全部远超 ±3% 线）。
3. `artifacts/verif/m1384_cf_vs_re.png` — API 反算 Cf 与 ITTC-1957 闭式完全重合（差 < 3e-6%）。
4. `artifacts/verif/m1384_ct_vs_fn.png` — SUBOFF 四工况 Ct vs Fn：参考值、几何 S 路径、遗留估计式路径与 ±15% 带；标注逐点误差。

## 5 注意点（修复内容、局限、适用范围）

**已修复（`backend/api/calculations.py`，向后兼容，默认参数下数值不变）**

1. **稳性数值缺陷**：`heel_angle_deg` 限定 [0, 90]；90° 的 tan 奇点显式返回 HTTP 400；
   超过 `wall_sided_limit_deg`（默认 25°，甲板边缘入水角）时不再输出无界 cubic 项，改为
   `GZ = GM·sinφ` 的有界回退，并以 `formula_valid=False` + `gz_basis="linear_fallback"` + `validity_note`
   显式标注。回退是**保守估计，不能替代静水力 GZ 曲线**。
2. **几何优先**：新增可选 `wetted_surface_m2`（几何已校验的湿表面积覆盖经验式）与
   `wetted_surface_source` 字段，落实「几何必须先验证」的规则；默认路径仍在响应里暴露
   `wetted_surface_m2`，让估计式偏差可被下游审计。
3. **ITTC-1957 (1+k)**：新增 `form_factor` 与 `form_resistance_n`，默认 1.0，保持既有数值。
4. **可审计性**：响应新增 `friction_coefficient_cf`，使 Cf 可被外部直接复核（此前只能从 Rf 反算并受 round 影响）。

**未修复 / 局限（如实声明）**

* `Cr = 0.002(1+2Cb)Fn^2.5` 仍为无文献出处的拟合，Fn≥0.9 时误差 34.6%；本次**未**替换为 Holtrop-1984 完整式
  （需要 LCB、Cp、Cm、球鼻、舵等几何量，平台当前缺少经校验的几何输入，硬替换等于用未验证的假设换掉未验证的假设）。
  建议列入后续单：先打通「几何→Cb/Cp/LCB」校验链，再引入 Holtrop-1984 并全 Fn 段复核。
* SUBOFF 参考行不可信（Rt 与 Ct 自相矛盾），且 DTMB-5415 四行缺少湿表面积字段，故 3.4 节仅覆盖
  SUBOFF 四例；本报告**未**宣称 calc.resistance 达到 <3% 精度。**该能力应维持 REAL 但精度标签标注
  「Fn<0.8 且湿表面积由几何给定：−8.9%…+13.5%；Fn>0.9：>15% 不可接受」**。
* 稳性侧仅校验 wall-sided 公式本身与其有效域，未涉及真实 GZ 曲线的静水力积分（属另一能力）。

## 6 误差判定（逐项，显式回答「是否 <3%」）

| 关键量 | 基准 | 误差 | 是否 <3% |
|---|---|---|---|
| Cf（Re = 1e5…1e10） | ITTC-1957 闭式 | ≤ 2.9e-06% | **是**（仅序列化舍入） |
| 湿表面积（圆柱 Cb=π/4） | πDL | −30.36% | 否 |
| 湿表面积（椭球 b/a=0.25） | 2πb²+2πab·asin(e)/e | −20.17% | 否 |
| 湿表面积（椭球 b/a=0.15） | 同上 | −19.86% | 否 |
| Ct（SUBOFF-01，Fn 0.459，几何 S） | 数据集 Ct = 0.0038 | −8.90% | 否 |
| Ct（SUBOFF-02，Fn 0.612，几何 S） | 数据集 Ct = 0.0040 | −1.18% | **是** |
| Ct（SUBOFF-03，Fn 0.765，几何 S） | 数据集 Ct = 0.0042 | +13.48% | 否 |
| Ct（SUBOFF-04，Fn 0.918，几何 S） | 数据集 Ct = 0.0044 | +34.58% | 否（>15% 不可接受） |
| GZ（φ ≤ 25°，wall-sided 闭式） | 同式闭式 | ≤ 1e-6 m 绝对 | **是** |
| GZ 有界性（φ ∈ [0, 90)） | GM 上界 | 修复前 4.0e32 m → 修复后 ≤ GM | **是**（修复后） |

**总结论**：摩擦线与稳性闭式实现达标；湿表面积经验式与高 Fn 剩余阻力**不达标**，且平台参考数据集
SUBOFF 四行不可用作判据。已修复稳性数值缺陷与几何输入通道，其余如实记为欠账。

## 7 复现

```bash
python3 tools/verif/verify_ship_calcs.py                       # 复核 + 出图 + JSON
python3 tools/verif/render_m1384_report.py                     # 本文 + API payload
python3 -m pytest tools/verif/test_ship_calcs_verification.py -q   # 17 passed
```

平台报告 id：**981**（POST /api/v1/tensor/reports，mission_id=1384）。

产物：`artifacts/verif/ship_calcs_verification.json`、`artifacts/verif/*.png`、
`tools/verif/` 下 verify_ship_calcs.py / render_m1384_report.py / test_ship_calcs_verification.py。

> 环境说明：`backend/tests/` 在本沙箱**不可运行**（`tests/conftest.py` 导入完整 FastAPI app，缺
> `cryptography`/`psycopg2` 且无外网），故回归测试放在不依赖该 conftest 的 `tools/verif/`，真实执行并全绿。
