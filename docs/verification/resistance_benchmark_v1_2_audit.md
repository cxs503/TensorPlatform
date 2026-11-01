# 阻力基准数据集与验证链独立复核（v1.0 → v1.2，验证欠账清偿）

> 复核对象：`backend/data/benchmark_reference.json` + `backend/api/domain/ship_benchmark.py`
> + `backend/api/calculations.py` 阻力链。
> 判据工具：`tools/benchmark_audit/audit_resistance_benchmark.py`（J1~J7，全闭式、可离线、进 CI）。
> 结论口径遵循平台证据分级：REAL / MOCK / WITHHELD / NOT_SUPPORTED。

## 1. 案例介绍

「阻力基准验证」是平台对外宣称精度能力的入口：给定船型与航速，把平台算得的
总阻力系数 Ct 与数据集里的「实验」Ct 比对，误差 ≤15% 判 pass。复核发现**该入口
在修复前 100% 失效且结论方向错误**——不是算法不行，而是三处工程链路缺陷：

1. **请求字段失配**：payload 发 `speed`，服务端模型只认 `speed_ms`，多余键被
   Pydantic 静默忽略 → 求解器恒按默认 1.0 m/s 计算；
2. **响应字段不存在**：`benchmark_validate` 读 `calc_data["ct"]`，而
   `/calculations/resistance` 的响应模型从未定义 `ct` → `calc_ct ≡ 0.0`、
   `error_pct ≡ 100%` → 12/12 案例判 FAIL，并自动写下「建议改用 CFD」的错误结论；
3. **数据集自身不自洽**：模型尺度几何配实船尺度航速（Fr 最高 2.06）、声明 Fr 与
   V/√(gL) 不符、Rt 与 Ct 互斥 3~9 倍、排水体积 ≠ Cb·L·B·T、出处无表号页码。

几何参数（v1.2，全部由公式派生，见各 case 的 `derivation` 字段）：

| 船型 | 尺度 | L (m) | B/D (m) | T (m) | Cb | ∇ (m³) | S (m²) |
|---|---|---|---|---|---|---|---|
| SUBOFF | model | 4.356 | 0.508 | 0.508 | 0.508 | 0.4485 | 5.42（继承） |
| DTMB-5415 | model | 5.720 | 0.7677 | 0.2477 | 0.507 | 0.5513 | 4.84（Holtrop） |
| KVLCC2 | full | 320.0 | 58.0 | 20.8 | 0.8098 | 312,470.8 | 26,631.8（Holtrop） |

## 2. 怎么做的

1. **先判据后动手**：把「数据集该满足什么」写成 7 条闭式判据（不引用任何外部数值，
   只用 V/√(gL)、Cb·L·B·T、0.5ρSCtV²、ITTC-57 这些恒等式），先在 v1.0 上跑出
   失效清单（证据指针 `docs/verification/audit_before.json`）；
2. **重建而非补丁**：`tools/benchmark_audit/build_dataset_v1_2.py` 从 v1.0 读入
   继承值，按公称主尺度与公式重算派生量，写 v1.2；继承的实验 Ct 一律
   `reference_verified=false`，无法复原速度配对的（DTMB-5415 四例）降级 `retired`；
3. **修 API 契约**：`ResistanceResponse` 补 `ct / cf_ittc57 / s_wetted_m2 /
   surface_method`；`benchmark_validate` 的字段构造、Ct 提取、判决三态重构；
4. **顺带修湿表面积**：旧式 `S = πBL(0.5+0.25Cb)` 用于排水型船时对 KVLCC2 高估
   +50.6%，改为 Holtrop–Mennen (1982) 后 -2.07%；回转体仍走代理式；
5. **回归门禁**：18 条 pytest（契约、Ct 提取、判决语义、判据、CF 闭式核对），
   其中契约测试用「payload 字段 ⊆ 请求模型字段」的黑名单判据，能拦住同类静默丢字段。

## 3. 计算结果

判据门禁（12 案例 × 7 判据 = 84 判定）：

| 判据 | v1.0 FAIL | v1.2 FAIL | v1.2 WITHHELD |
|---|---|---|---|
| J1_FROUDE_CONSISTENT | 4 | 0 | 0 |
| J2_VOLUME_CONSISTENT | 12 | 0 | 0 |
| J3_RT_CT_CONSISTENT | 12 | 0 | 4 |
| J4_FRICTION_FLOOR | 0 | 0 | 4 |
| J5_FR_BAND | 6 | 0 | 0 |
| J6_PROVENANCE | 12 | 0 | 0 |
| J7_SCALE_DECLARED | 12 | 0 | 0 |

湿表面积（外部锚点 = KVLCC2 公称 27,194 m²）：

| 模型 | S (m²) | 相对公称误差 |
|---|---|---|
| 旧代理式 πBL(0.5+0.25Cb) | 40,958.4 | +50.62% |
| Holtrop–Mennen 1982（缺省 Cm/Cwp） | 26,631.8 | -2.07% |

平台阻力链逐案例状态（Fr/Re/Cf/继承 Ct 与证据状态）：

| case | 船型 | 尺度 | 有效性 | Fr | Re | Cf_ITTC57 | Ct / 参考值状态 |
|---|---|---|---|---|---|---|---|
| SUBOFF-01 | SUBOFF | model | in_range | 0.459 | 1.30e+07 | 0.002867 | 0.003800 / withheld |
| SUBOFF-02 | SUBOFF | model | out_of_range | 0.612 | 1.74e+07 | 0.002732 | 0.004000 / withheld |
| SUBOFF-03 | SUBOFF | model | out_of_range | 0.765 | 2.17e+07 | 0.002634 | 0.004200 / withheld |
| SUBOFF-04 | SUBOFF | model | out_of_range | 0.918 | 2.60e+07 | 0.002557 | 0.004400 / withheld |
| DTMB5415-01 | DTMB-5415 | model | in_range | 0.069 | 2.94e+06 | 0.003756 | —  / retired |
| DTMB5415-02 | DTMB-5415 | model | in_range | 0.138 | 5.88e+06 | 0.003297 | —  / retired |
| DTMB5415-03 | DTMB-5415 | model | in_range | 0.276 | 1.18e+07 | 0.002917 | —  / retired |
| DTMB5415-04 | DTMB-5415 | model | in_range | 0.413 | 1.76e+07 | 0.002725 | —  / retired |
| KVLCC2-01 | KVLCC2 | full | in_range | 0.129 | 1.94e+09 | 0.001412 | 0.002800 / withheld |
| KVLCC2-02 | KVLCC2 | full | in_range | 0.138 | 2.08e+09 | 0.001401 | 0.003000 / withheld |
| KVLCC2-03 | KVLCC2 | full | in_range | 0.142 | 2.15e+09 | 0.001395 | 0.003100 / withheld |
| KVLCC2-04 | KVLCC2 | full | in_range | 0.147 | 2.22e+09 | 0.001390 | 0.003200 / withheld |

算法级核对（KVLCC2 最高速点，8.231 m/s，Fr=0.1469）：平台 Ct = Cf + Cr =
0.001390 + 0.000043 = **0.001433**，继承参考 Ct = 0.003200，
相对偏差 **55.2%**（该参考值未复核 → 按证据分级记 WITHHELD，不作为精度判决）。

## 4. 结果图

* `figures/fig1_froude_scale_mix.png`：v1.0 由速度/几何复算的实际 Fr（最高 2.06）与
  修复后声明 Fr 对照，直观暴露尺度混用；
* `figures/fig2_judge_gate.png`：J1~J7 判据 FAIL 数由 58 降为 0，
  同时显式标出 WITHHELD 数（不把「不知道」伪装成「通过」）；
* `figures/fig3_wetted_surface.png`：湿表面积两套模型对照 + 公称锚点，
  KVLCC2 由 +50.6% 修到 -2.07%；
* `figures/fig4_ct_friction_floor.png`：继承 Ct 与 ITTC-57 物理带 [Cf, 3Cf]，
  说明「量级可信、出处不可信」这一分级判断有据。

## 5. 注意点 / 局限

* **没有变出真实验值**：所有继承 Ct 仍 `reference_verified=false`。补值必须给出
  原始试验表的表号/页码（ITTC 2010 Gothenburg、SIMMAN 2008、DTRC-90/012），
  在此之前任何「pass 率」都是 0 样本，`accuracy_claim = withheld`；
* **Holtrop 湿表面积**在 Cb<0.55 的细长体（5415）属外推，式中 Cm/Cwp 用工程通式
  缺省，误差量级 ±5%（KVLCC2 上实测 -2.07%，因为该 Cb 在该式命名域内）；
* **剩余阻力项 Cr = 0.002(1+2Cb)Fn^2.5 无文献标定**（原文自述 curve-fitting），
  本次未改物理模型、只修链路与几何：它决定 KVLCC2 点 Ct 的 55.2% 偏差，
  要消掉必须换 Holtrop-Mennen 1982 全套或 CFD，建议单独立项；
* 回转体（SUBOFF）湿表面积仍是继承值、未标定，`hull_form=body_of_revolution`
  路径会绕过 Holtrop——这是显式选择的代理式，不是通用解；
* 阻力服务（:8002）在本次复核环境**未监听**，端到端核对走的是同一函数的进程内直调
  （`asyncio.run(calc_resistance(...))`），HTTP 层只做契约静态校验。

## 6. 误差判定

| 关键量 | 基准 | 误差 | 是否 <3% |
|---|---|---|---|
| 平台 Cf(Re=kvlcc2 最高速) | ITTC-57 闭式 0.075/(logRe−2)² | <1e-6 %（逐位一致，pytest 断言 rel<1e-6） | **是** |
| Holtrop 湿表面积 KVLCC2 | 公称 27,194 m² | -2.07% | **是** |
| 旧代理式湿表面积 KVLCC2 | 公称 27,194 m² | +50.62% | 否（已废弃该路径） |
| Fr 自洽（12 例） | V/√(gL) 闭式 | ≤1%（数据舍入） | **是** |
| 排水体积自洽（12 例） | Cb·L·B·T | ≤1% | **是** |
| 平台 Ct vs 继承参考 Ct（KVLCC2） | 无来源的继承值（WITHHELD） | 55.2% | **不判定**——无可用基准，原因：参考值缺表号/页码且量级与实船 Re 不符 |

**诚实结论**：本次清偿的是「验证链本身不可信」这个欠账（3 处静默缺陷 + 数据集
12/12 不自洽 → 0/12），并把唯一可精确核验的量（ITTC-57 摩擦项、Holtrop 湿表面积、
几何闭合）压到 <3%；**阻力总值的绝对精度仍是 WITHHELD**，因为数据集里没有一条
可追溯的实验 Ct。后续自找活的最高价值线索就是「补 3~5 条带表号页码的试验 Ct」，
一旦补齐，判决自动从 WITHHELD 翻成 PASS/FAIL。
