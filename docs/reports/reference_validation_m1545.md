# 已合并功能复核：静水阻力 `calculations/resistance` 对文献/独立方法基准（m1545）

**结论一句话**：平台 `calc.resistance` 评 REAL，但其剩余阻力 `Cr` 是手调量级而非 Holtrop 公式；
KVLCC2 设计点 `R_T` 相对 Holtrop-1984 独立实现偏高 **+224.5%**、有效功率 `P_E = 42.5 MW`
超出该船型工程装机量级（20–30 MW），按平台「>15% 不可接受」判据 **验收不通过**。
本单提交「参考值语料库 + 双实现交叉判据 + 修正引擎」，把该项从 *无判据* 变为 *机器可裁*。

- 分支：`agent/m1545`（仓：TensorPlatform）
- 语料：`tests/data/reference_corpus/resistance_reference.json`（12 条，sha256 锚定，逐条带容差）
- 判定工件：`outputs/reference_corpus_verdict.json`
- 修正引擎：`backend/services/resistance_holtrop.py`（Holtrop-Mennen 1982/1984）
- 测试：`tests/test_reference_corpus.py`（26 passed）
- 图：`outputs/refval_fig1_rt_vs_speed.png`、`refval_fig2_deviation.png`、`refval_fig3_components.png`

---

## 1. 案例介绍（物理背景 / 几何 / 参数）

对象是平台对外的静水阻力端点 `POST /api/v1/calculations/resistance`：
输入主尺度 `L/B/T`、`Cb`、`Vs`、`ρ`、`ν`，输出 `R_T`、摩擦分量、剩余分量、`Re`、`Fn`。

复核船型集（主尺度均为公开基准船型；`Δ` 与 `Cb·L·B·T` 的一致性在引擎内做 5% 先验检查）：

| 案例 | 船型 | L [m] | B [m] | T [m] | Cb | Δ [t] | Vs | Fn |
|---|---|---|---|---|---|---|---|---|
| kvlcc2_design_15kn | KVLCC2 (MOERI VLCC) | 320.0 | 58.0 | 20.8 | 0.8098 | 310000 | 15.0 kn | 0.1377 |
| kvlcc2_slow_13kn | KVLCC2 | 320.0 | 58.0 | 20.8 | 0.8098 | 310000 | 13.0 kn | 0.1194 |
| kcs_design_24kn | KCS (KRISO 集装箱) | 230.0 | 32.2 | 10.8 | 0.651 | 52030 | 24.0 kn | 0.2599 |
| dtmb5415_15kn | DTMB 5415 (军船型) | 142.0 | 19.0 | 6.15 | 0.506 | 8630 | 15.0 kn | 0.2067 |
| panamax_tanker_14kn | Panamax 油船 | 230.0 | 32.2 | 12.5 | 0.82 | 77000 | 14.0 kn | 0.1534 |

## 2. 怎么做的（方法 / 流程 / 工具链）

1. **读码定性**：部署态（:8001）输出含 `cr`/`ct`/`pe` 字段，溯源到 `work/m6 backend/api/calculations.py`；
   其 `Cr = 0.005·C4·C1·C2·C3`（`C4 = 0.5+2.5Fn` 分段、`C2/C3` 为 B/T、Cb 修正）——**不是 Holtrop 公式**，
   而是量级手调式；本工作树旧实现同样是手调式 `Cr = 0.002(1+2Cb)Fn^2.5`。
2. **建基准（先造判据，再动手）**：
   - A 类 `analytic`（7 条）：ITTC-1957 摩擦线 `Cf=0.075/(log10Re-2)^2` 与 ITTC-1978 相关补贴
     `C_A=(105(k_s/L)^{1/3}-0.64)×10^-3`，参考值用 **mpmath dps=40** 独立求值，容差 0.05%。
   - B 类 `cross_method`（5 条）：Holtrop-Mennen (1982/1984) 公式集，由
     `tools/validate/holtrop_ref_independent.py`（numpy 写法、**零代码复用、不共享常量**）独立实现，
     平台引擎与它的偏差容差 0.5%（捕捉笔误/量纲错）。单点绝对值另附
     `sensitivity_band`（i_E 10–40°、lcb ±2%L、k_s 75–300 µm，45 点）以声明不确定性，不做「逐位」宣称。
   - C 类 `experimental`（文献/试验拖曳）：**离线环境无原文可查，语料库显式标 `withheld` 并写明缺什么，
     不手抄任何试验数值**。
3. **修正引擎**：`R_T = R_F(1+k1) + R_W + R_A`（Holtrop 形状因子回归 + 兴波阻力回归 + ITTC-78 相关补贴），
   缺省船型参数（`Cm`、`Cwp`、`i_E`、`lcb`、`C_stern`、`k_s`）全部写入 `assumptions` 随结果返回，
   `Cb·L·B·T` 与输入 `Δ` 偏差 >5% 时告警。
4. **端点接线**（最小改动，字段契约不变）：`backend/api/calculations.py:calc_resistance` 改为调用引擎，
   新增几何先验 `3 ≤ L/B ≤ 12` 显式 400（禁止静默外推），`frictional_resistance_n` 解释为 `R_F(1+k1)`、
   `residual_resistance_n = R_W + R_A`。
5. **判据有牙齿**：保留历史实现快照 `tools/validate/legacy_resistance.py`，测试断言
   同一判据必须把历史实现判为不通过（否则说明基准被调软）。

## 3. 计算结果（数字均由 `outputs/reference_corpus_verdict.json` 程序化读出）

### 3.1 逐案 R_T 与判定

| 案例 | 参考 R_T [kN] (Holtrop 独立实现) | 修正引擎 R_T [kN] | 引擎偏差 | 旧工作树偏差 | 部署态偏差 | 部署态 P_E [MW] | 判定 |
|---|---|---|---|---|---|---|---|
| kvlcc2_design_15kn | 1698.6 | 1698.6 | 0.000% | 5.8% | **+224.5%** | 42.5 | 引擎 PASS / 部署 FAIL |
| kvlcc2_slow_13kn | 1280.1 | 1280.1 | 0.000% | 6.4% | **+211.9%** | 26.7 | 引擎 PASS / 部署 FAIL |
| kcs_design_24kn | 2350.5 | 2350.5 | 0.000% | 21.3% | **+214.7%** | 91.3 | 引擎 PASS / 部署 FAIL |
| dtmb5415_15kn | 208.9 | 208.9 | 0.000% | 26.0% | **+233.5%** | 5.4 | 引擎 PASS / 部署 FAIL |
| panamax_tanker_14kn | 669.9 | 669.9 | 0.000% | 1.3% | **+214.2%** | 15.2 | 引擎 PASS / 部署 FAIL |

> 「引擎偏差 0.000%」是**同公式集双实现交叉**的结果（判据要求 <0.5%），不代表与试验一致；
> 与试验/文献的一致性属 C 类，本单 **WITHHELD**（见 §5）。

### 3.2 分解与系数

| 案例 | (1+k1) | R_F(1+k1) [kN] | R_W [kN] | R_A [kN] | 参考 C_r [-] | 部署态 C_r [-] | C_r 放大倍数 |
|---|---|---|---|---|---|---|---|
| kvlcc2_design_15kn | 1.340 | 1529.0 | 26.6 | 143.0 | ≈1.0e-3 | 4.744e-3 | ~4.7× |
| kcs_design_24kn | 1.167 | 1130.8 | 1029.5 | 190.2 | ≈2.0e-3 | 8.034e-3 | ~4.0× |
| panamax_tanker_14kn | 1.274 | 553.4 | 36.6 | 80.0 | ≈1.0e-3 | 5.4e-3 | ~5× |

部署态 `C_r` 随 Fn 单调放大到 8.0e-3（Fn=0.26），完全由 `C4` 分段线性驱动，与船型无关——这是 fudge 的指纹。

### 3.3 敏感性带（声明不确定性，不做逐位宣称）

| 案例 | 参考单点 R_T [kN] | 带 [min, max] [kN] | 部署态 R_T [kN] |
|---|---|---|---|
| kvlcc2_design_15kn | 1698.6 | [1552, 1908] | 5512.4 |
| kcs_design_24kn | 2350.5 | [1875, 2811] | 7397.0 |
| dtmb5415_15kn | 208.9 | [181, 238] | 696.6 |
| panamax_tanker_14kn | 669.9 | [607, 768] | 2105.6 |

即使取带上界，部署态仍高出 **1.9–3.5 倍**——结论对 `i_E/lcb/k_s` 的取值不敏感。

### 3.4 图

- `refval_fig1_rt_vs_speed.png`：KVLCC2 的 R_T(V) 曲线（修正引擎 + 敏感性带 + 部署态 + 实测回归点 13/15 kn）
  与 P_E(V) 对装机量级带。
- `refval_fig2_deviation.png`：逐案偏差（对数轴，含 15% 判据线）与逐案绝对 R_T 对比。
- `refval_fig3_components.png`：修正引擎的阻力分解堆叠图 + C_r 新旧对比。

## 4. 注意点（坑 / 局限 / 适用范围）

1. **自洽 ≠ 验证**：旧工作树实现把湿表面积抬高 **+48%**（`πBL(0.5+0.25Cb)` 冒充湿表面积）同时把 `Cr`
   压低一个量级，两者在总阻力上**碰巧抵消**（KVLCC2 仅差 5.8%，Panamax 仅差 1.3%）。
   若只测总阻力就会误判「合格」。本单因此把分量缺陷单独钉成测试
   （`test_legacy_worktree_geometry_defect_is_real_even_when_total_cancels`）。
2. **Holtrop 是回归方法**，对常规单体排水型船（0.55≤Cb≤0.85、Fn≤0.4）自述精度 ±5–10%；
   对军船（DTMB 5415，Cb=0.506）属外推，带更宽（[181,238] kN）。
3. `R_B/R_TR/R_APP` 因缺球鼻艏/方尾/附体参数置 0，对带大球鼻艏船会低估 `R_W`（本单已在 `assumptions` 显式记录）。
4. 端点保留了 `frictional_resistance_n / residual_resistance_n` 字段名，但语义已改为
   `R_F(1+k1)` 与 `R_W + R_A`——下游若做「加形状因子」的二次修正会重复计算，需同步。
5. 部署态（`work/m6`）代码不在本工作树内，本单只做**证据与修正引擎**，未修改部署仓；
   落地补丁建议见 §6。

## 5. 误差判定（关键量 vs 基准，逐项回答「是否 <3%」）

| 关键量 | 基准 | 修正引擎误差 | 部署态误差 | <3%？ |
|---|---|---|---|---|
| ITTC-57 `Cf`（5 个 Re） | mpmath dps=40 解析值 | ≤1e-13% | 同式实现（该分量无错） | **是** |
| ITTC-78 `C_A` (L=100/320) | mpmath dps=40 解析值 | ≤1e-13% | 同式实现 | **是** |
| KVLCC2@15kn `R_T` | Holtrop-1984 独立实现 1698.6 kN | 0.000%（双实现交叉 <0.5%） | +224.5% | **是（引擎）/ 否（部署态）** |
| KCS@24kn `R_T` | 2350.5 kN | 0.000% | +214.7% | **是 / 否** |
| `C_r` 量级 | 1.0–2.0e-3 | 同基准 | 4.7–8.0e-3（4–5×） | **否（部署态）** |
| `R_T` vs **试验/文献拖曳** | — | — | — | **WITHHELD**：离线无原文，不手抄；须引 `measured C_T`/试航 R_T 并附 sha256 原文锚 |

**判定**：解析分量 <3% 达标；引擎与独立方法交叉 <0.5% 达标；**与试验基准的一致性无法在本单闭环（WITHHELD）**，
因此按平台证据分级，`calc.resistance` 应由 REAL 降为 **WITHHELD**，直至 C 类实验基准入库；
而部署态实现（+214% ~ +233%）按「>15% 不可接受」直接判 **不通过**。

## 6. 落地建议 / 拆分（超出单干范围的派单逻辑）

1. **P0（部署仓 work/m6 补丁）**：把 `calculate_holtrop_cr` 换成本引擎的
   `R_F(1+k1)+R_W+R_A` 结构，或最小改动为「Cr 由本引擎计算后再组合」；
   风险点：`Cr` 字段语义、`ΔCf=4e-4` 与 `R_A` 重复计入（当前部署态先算 `ΔCf` 又叠 `Cr` fudge，属重复补贴）。
2. **P1（实验基准入库，需能读文献的席位）**：KVLCC2/KCS/DTMB5415 的 `measured C_T` 表与试航 R_T，
   `reference_corpus.py build` 增加 `experimental` 类条目并附原文 sha256；之后再跑一次 `verify` 即可把 WITHHELD 转 REAL/FAIL。
3. **P2（几何先验）**：端点请求体只收 `L/B/T/Cb` 与可选 `Δ`，无型值表 → 无法做 Cb/L/B 的真实几何验证；
   建议接 `geometry.verify_official` 的型线路径，把 `Cb` 从「用户声称」变为「几何实测」。
4. **P3（性能无关）**：本单不涉及重计算（纯解析 + 回归），load15=122 下单席可完成，未起 CFD 进程。