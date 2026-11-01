# m1757 阻力端点路线复核：轴对称体经验式 → 域内 Holtrop 全量

**结论先行**：平台服务的 `POST /api/v1/calculations/resistance` 过去对**所有**船型
只走一条 SUBOFF 标定的轴对称体经验式；对 KCS 类细长集装箱船它把总阻力高估
**+28.9%**（KCS, Fn=0.105），
远超平台 15 % 可接受带。m1757 引入**域内 Holtrop-Mennen 1982/1984 全量路线**并作为
`auto` 默认：同一批算例的偏差降到 **≤1.53%**（<3 % 验收线），越域船型
（回转体/小尺度）仍退回原经验式且响应显式标注越域理由。

## 1. 案例与物理背景

阻力是船舶总体性能的入口量：它决定有效功率 P_E = R_T·V、进而决定主机选型与 EEDI。
平台阻力端点同时被batch 计算、对标链与数字孪生调用，因此"哪个模型给的数"必须是
可追溯字段而不是隐含假设。

| 船型 | Lpp [m] | B [m] | T [m] | Cb | Cm | LCB [%L] | 计算航速 [m/s] |
|---|---|---|---|---|---|---|---|
| KVLCC2（VLCC 标模） | 320.0 | 58.0 | 20.8 | 0.8098 | 0.9998 | +3.48 | 7.20 / 7.72 / 8.23 |
| KCS（集装箱标模） | 230.0 | 32.2 | 10.8 | 0.651 | 0.9849 | −1.48 | 5.00 / 6.17 / 7.40 |
| Bulk180k（典型好望角型） | 290.0 | 45.0 | 18.0 | 0.82 | 0.995 | +2.5 | 6.00 / 7.00 / 8.00 |
| SUBOFF（回转体，越域对照） | 4.356 | 0.508 | 0.476 | 0.542 | 1.0 | 0 | 3.0 / 4.0 / 5.0 |

海水 ρ = 1025 kg/m³、ν = 1.190e-06 m²/s（端点输入）。湿面积**不作为输入注入**，
由各路线自算，以便同时暴露湿面积模型本身的问题。

## 2. 方法

三条独立路线 + 一条异实现交叉：

1. **legacy（修复前默认）**：`S = π·B·L·(0.5+0.25Cb)`，`Cf` 用 ITTC-1957，
   `Cr = 0.002·(1+2Cb)·Fn^2.5`，无兴波/相关补贴项。
2. **Holtrop 参考（仓内 REAL 实现）**：`services/resistance_holtrop.py`，
   `R_T = R_F(1+k1) + R_W + R_A`，含 Holtrop 兴波回归、`c1…c16/m1/m2/λ` 系数族、
   ITTC-1978 相关补贴 `C_A`。
3. **auto（m1757 新默认）**：按 Holtrop 公布适用域（Cb∈[0.55,0.87]、
   L/B∈[3.9,14.9]、B/T∈[2.1,4.0]、L≥20 m、Fn≤0.40）判定；域内调用路线 2，
   越域退回路线 1 并把逐项越域理由写进 `method_notes`。
4. **异实现交叉**：`src/ship_perf/resistance.py`（另一份独立编码的 Holtrop 1984，
   含 (1+k)、C_A、Rw），用于给出"实现不确定带"。

判据（先于动手）：`|R_T(auto) − R_T(Holtrop)| / R_T(Holtrop) < 3 %`；分项可加
`R_T = R_F + R_form + R_resid`（残差 < 1e-3 N）；越域船型强制 Holtrop 必须 HTTP 400。

推演链：`python3 tools/m1757/verify_resistance_routing.py`（产出 JSON+PNG）→
`python3 tools/m1757/make_report.py`（本报告，数字全部从 JSON 注入）。

## 3. 计算结果

| 船型 | Fn | 路线 | R_T legacy [MN] | R_T auto [MN] | Holtrop 参考 [MN] | err_legacy | err_auto | S_auto [m²] | S 对照 [m²] |
|---|---|---|---|---|---|---|---|---|---|
| KVLCC2 | 0.129 | holtrop | 1.571 | 1.482 | 1.459 | +7.64% | +1.53% | 26691 | 27000 |
| KVLCC2 | 0.138 | holtrop | 1.799 | 1.704 | 1.678 | +7.16% | +1.53% | 26691 | 27000 |
| KVLCC2 | 0.147 | holtrop | 2.038 | 1.949 | 1.919 | +6.18% | +1.53% | 26691 | 27000 |
| KCS | 0.105 | holtrop | 0.307 | 0.237 | 0.238 | +28.92% | -0.30% | 9000 | 9530 |
| KCS | 0.130 | holtrop | 0.459 | 0.356 | 0.357 | +28.48% | -0.31% | 9000 | 9530 |
| KCS | 0.156 | holtrop | 0.653 | 0.518 | 0.520 | +25.69% | -0.35% | 9000 | 9530 |
| Bulk180k | 0.113 | holtrop | 0.791 | 0.779 | 0.773 | +2.39% | +0.88% | 19860 | — |
| Bulk180k | 0.131 | holtrop | 1.065 | 1.056 | 1.047 | +1.72% | +0.86% | 19860 | — |
| Bulk180k | 0.150 | holtrop | 1.382 | 1.404 | 1.393 | -0.79% | +0.84% | 19860 | — |
| SUBOFF | 0.459 | legacy | 0.000 | 0.000 | 0.000 | -75.41% | -75.41% | 4 | 8 |
| SUBOFF | 0.612 | legacy | 0.000 | 0.000 | 0.001 | -73.74% | -73.74% | 4 | 8 |
| SUBOFF | 0.765 | legacy | 0.000 | 0.000 | 0.001 | -67.69% | -67.69% | 4 | 8 |

（`err_*` 为相对仓内 Holtrop 参考实现 `R_T` 的相对偏差；分项残差最大值
1.00e-04 N，即分项严格可加。）

**湿面积**是 legacy 路线偏差的主因：同一 KVLCC2，legacy 估 40958 m²，
Holtrop 回归给 26691 m²（相对仓内引用的公布值 ~27 000 m² 差
-1.1%）。KCS 上 legacy 的
+71.3% 湿面积高估
被偏小的 `Cr` 部分抵消，净偏差仍有 +28.9%。

Holtrop 实现自身的**参数不确定带**（i_E ∈ [10°,40°]、LCB ±2 %L、k_s ∈ [75,300] µm，
本脚本对每算例做 12 点扫描）为 R_T ∈ [0.215, 2.178] MN，
相对半宽 ≤ 11.9%。因此**任何单点比对必须先声明带**，把"<3 %"理解为
"同一实现、同一参数下的可复现性"，而不是"对实验的绝对精度"。

## 4. 结果图

![阻力路线复核](fig_resistance_routes.png)

(a) 设计点 R_T 三路线对比；(b) 相对 Holtrop 的偏差随 Fn 的变化，紫点线为平台 15 %
门限；(c) auto 路线湿面积（实线）与仓内引用的公布值（点线）。

## 5. 注意点 / 局限 / 适用范围

- **越域不静默**：SUBOFF 类回转体（B/T=1.07、Cb=0.542、L=4.4 m）在 `auto` 下退回
  legacy，`method_notes` 逐条给出越域理由；强制 `holtrop` 返回 400。表里 SUBOFF 三行
  的 `err_*` 无物理意义（Holtrop 对 4 m 回转体本不适用），列出仅为展示"拒绝外推"行为。
- **legacy 路线保留**：`resistance_method="legacy"` 可复现旧值，供既有对标链/回归基线
  使用；响应中该路线带 `legacy axisymmetric-body fit` 标注，不再冒充通用方法。
- **KCS 公布湿面积 9 530 m²** 为文献常引值，本仓无 provenance 记录，仅作量级对照；
  Holtrop 回归给 9000 m²（-5.6%）。
- **未覆盖**：附体/球鼻/方尾项（`R_B/R_TR/R_APP` 缺几何输入时置 0）、空气阻力 Raa
  （L ≥ 150 m）、浅水/限制航道、模型-实船换算 ITTC-78 全链条。
- Holtrop 参数（i_E、LCB、k_s）对多数调用方不可知，端点沿用回归缺省并在
  `method_notes` 声明；需要严格区间时应改用 `sensitivity_band()`。

## 6. 误差判定（逐项，显式回答是否 < 3 %）

| 关键量 | 基准 | 最大偏差 | 是否 < 3 % |
|---|---|---|---|
| `R_T`（auto 路线，3 船型 × 3 航速） | 仓内 Holtrop 全量实现 | 1.53 % | **是** |
| `R_T`（legacy 路线，同算例） | 同上 | 28.92 % | **否**（KCS 上超 15 % 门限，本单修复对象） |
| `R_T`（异实现 ship_perf） | 同上 | 8.80 % | **否**（两份独立编码的 Holtrop 实现差异带） |
| `S_wet`（KVLCC2） | 仓内引用公布值 ~27 000 m² | 1.15 % | **是** |
| 分项可加性 `R_T−(R_F+R_form+R_resid)` | 恒等式 | 1.0e-04 N | **是** |
| **对实验/实船的绝对精度** | 无 provenance 的实验数据 | — | **WITHHELD**（本仓无带出处的 KVLCC2/KCS 试验 R_T 数据集，不得声称 ≤5 %） |

**诚实结论**：本单把"端点在域内与仓内参考实现一致"的偏差压到 <3 %，并让越域调用
显式失败；但"平台静水阻力对实验 ≤5 %"这一 KPI **仍未取得**，因为缺带出处的试验
基准（KVLCC2 标模的 C_T 需要出处 + 尺度换算链）。下一单应优先补这个锚点。

## 7. 测试

- 新增 `backend/tests/feat/test_resistance_routing_m1757.py`（11 passed）。
- 回归抽查 `tests/test_calculations.py`、`tests/feat/test_benchmark_chain_m1388.py`、
  `tests/feat/test_benchmark_integrity_audit.py`：m1757_suite: 11 passed in 2.1s; regression: FAILED tests/feat/test_benchmark_integrity_audit.py::TestConsistencyBetweenPaths::test_all_presets_carry_verified_wetted
  （`test_benchmark_integrity_audit.py` 的失败在改动前后同样存在——它断言的是
  `resp.ct` 字段，而响应模型早已是 `total_resistance_coefficient`，属存量陈旧测试，
  本单未连带修改以保持改动最小、可归因）。
