# 真值可供给性重判（Truth-Supply Recovery Ledger）— m1641

> 单号：m1641（platform 席自找活） · 分支 `agent/m1641` · 仓 TensorPlatform

> 模块 `backend/services/reference_recovery.py` · 账本 `artifacts/reference_recovery/ledger.json` · 测试 `tests/test_reference_recovery.py`（19 项）


## ① 案例介绍：被审的对象

平台对标算例的唯一正典 `backend/data/benchmark_reference.json`（v3.0）供给 `POST /calculations/benchmark_validate`，用来给阻力计算结果打 pass/fail，并喂给指南 KPI「对标算例 ≥10」。共 12 行、3 个船型：

| 船型 | 尺度 | 行数 | 归档介质 | 正典判定 |
|---|---|---|---|---|
| DTMB-5415 | — | 4 | ρ=998.2 kg/m³ | WITHHELD |
| KVLCC2 | — | 4 | ρ=1025.0 kg/m³ | WITHHELD |
| SUBOFF | — | 4 | ρ=998.2 kg/m³ | DERIVED |

**问题**：正典 v3.0（#272）判定 8 行 `WITHHELD`（Ct 与 Rt 不能同时成立）。但「`WITHHELD`」本身不回答下一步该干什么 —— 是补一个湿面积就能救回来，还是必须回到原始拖曳试验表重供 Rt？没有这个答案，欠账就永远派不出去。


## ② 怎么做的（判据 / 工具链）

- **R1 反解**（精确、无近似）：`S_implied = R_t / (C_t·½ρV²)`；带自检：由已知 S 正算 Rt 再反解必须回到原值（测试 `test_implied_surface_round_trip`，误差 <1e-12）。
- **船型物理带**：`S_hull·(1 ± 15%)`。`S_hull` 由 Holtrop–Mennen (1982) 湿表面积回归给出（`services/holtrop_mennen.py`，缺省 `Cm/Cwp` 由其内建回归派生并在账本 `derived` 留痕）；迴转体（SUBOFF）不用排水型船回归，改用归档实测 S —— 溯源逐行写在 `s_hull_basis`。**本模块不引入任何未溯源的文献数字。**
- **供给判定**：`S_implied ∈ 物理带` 且正典已认可 → `USABLE`；在带内但正典判 `WITHHELD` → `RECOVERABLE_BY_SURFACE`（补 S 即可救）；在带外 → `IRRECOVERABLE_FILED_RT`（补 S 无用）。**绝不改写正典判定**。
- **速度歧义鲁棒性**：DTMB-5415 的航速是「按 Fr 反算的条件值」，记录里另有归档实测航速；两个读法必须同结论（`speed_robust`）。
- **恒定因子检验**：`filed_Rt / R1_required_Rt` 逐行算，`max/min ≤ 1.15` 则「单一恒定因子可解释」（→ 系统性标度误用，只要认出约定即可救）；否则「逐行不一致」（→ Ct 与 Rt 混档，必须重取原始表）。
- **六种候选误用模式扫描**：航速 kn↔m/s、模型尺度湿面积、淡水/海水密度、湿面积折半、只记摩擦分量、Ct 实为剩余系数。每模式给 (预测 Rt, 隐含航速)；三态判定 `ACCEPTED`（Rt 复现**且**隐含航速在记录内）/ `RT_ONLY`（Rt 复现但航速是记录外的第三个值 → 巧合候选）/ `REJECTED`。
- **供数容差预算**：15 % 闸门里真值最多占 1/3 → `required_uncertainty_pct = 5.0 %`（理由写进每个 `datum_request`）。
- 工具链：`backend/services/reference_recovery.py`（判据） + `tools/audit/reference_recovery_ledger.py`（账本 JSON） + `tools/audit/render_reference_recovery.py`（本页图/表） + `pytest`（19 项）。纯 CPU 毫秒级，无算力负载。


## ③ 计算结果（全部程序化注入）

判定分布：`IRRECOVERABLE_FILED_RT`=8，`USABLE`=4；**可参与 15 % 判决的算例 4/12 行**，KPI 目标 10 → 赤字 **6 例**（`kpi_met=False`）。

| case | 正典 | 判定 | S_implied (m²) | S_hull (m²) | 带 (m²) | 比值 | Rt 归档 (N) | Rt 所需 (N) | 因子 |
|---|---|---|---|---|---|---|---|---|---|
| DTMB5415-01 | WITHHELD | IRRECOVERABLE_FILED_RT | 17.49 | 4.512 | 3.835–5.189 | 3.876 | 8.2 | 1.8–2.43 | 3.876 |
| DTMB5415-02 | WITHHELD | IRRECOVERABLE_FILED_RT | 17.99 | 4.512 | 3.835–5.189 | 3.987 | 28.5 | 6.08–8.22 | 3.987 |
| DTMB5415-03 | WITHHELD | IRRECOVERABLE_FILED_RT | 16.28 | 4.512 | 3.835–5.189 | 3.608 | 95 | 22.38–30.28 | 3.608 |
| DTMB5415-04 | WITHHELD | IRRECOVERABLE_FILED_RT | 14.73 | 4.512 | 3.835–5.189 | 3.264 | 210 | 54.68–73.98 | 3.264 |
| KVLCC2-01 | WITHHELD | IRRECOVERABLE_FILED_RT | 7,389 | 2.672e+04 | 2.271e+04–3.072e+04 | 0.2766 | 5.5e+05 | 1.69e+06–2.287e+06 | 0.2766 |
| KVLCC2-02 | WITHHELD | IRRECOVERABLE_FILED_RT | 7,099 | 2.672e+04 | 2.271e+04–3.072e+04 | 0.2657 | 6.5e+05 | 2.079e+06–2.813e+06 | 0.2657 |
| KVLCC2-03 | WITHHELD | IRRECOVERABLE_FILED_RT | 6,929 | 2.672e+04 | 2.271e+04–3.072e+04 | 0.2594 | 7e+05 | 2.294e+06–3.104e+06 | 0.2594 |
| KVLCC2-04 | WITHHELD | IRRECOVERABLE_FILED_RT | 6,840 | 2.672e+04 | 2.271e+04–3.072e+04 | 0.256 | 7.6e+05 | 2.523e+06–3.414e+06 | 0.256 |
| SUBOFF-01 | DERIVED | USABLE | 5.42 | 5.42 | 4.607–6.233 | 1 | 92.52 | 78.64–106.4 | 1 |
| SUBOFF-02 | DERIVED | USABLE | 5.42 | 5.42 | 4.607–6.233 | 1 | 173.1 | 147.2–199.1 | 1 |
| SUBOFF-03 | DERIVED | USABLE | 5.42 | 5.42 | 4.607–6.233 | 1 | 284 | 241.4–326.6 | 1 |
| SUBOFF-04 | DERIVED | USABLE | 5.42 | 5.42 | 4.607–6.233 | 1 | 428.5 | 364.2–492.8 | 1 |

### 恒定因子检验（逐船型）

| 船型 | 行数 | 因子 min | 因子 max | 极差比 | 单一恒定因子可解释 | 判读 |
|---|---|---|---|---|---|---|
| DTMB-5415 | 4 | 3.2645 | 3.9873 | 1.2214 | False | 逐行因子不一致 → Ct 与 Rt 疑似混档，无恒定因子可解释 |
| KVLCC2 | 4 | 0.256 | 0.2766 | 1.0805 | True | 单一恒定因子可解释全部行 → 系统性标度/单位误用 |
| SUBOFF | 4 | 1.0 | 1.0001 | 1.0001 | True | R1 闭合（因子 ≈ 1） |

### 线图

![reference_recovery_surface](figures/reference_recovery_surface.png)

![reference_recovery_factor](figures/reference_recovery_factor.png)


## ④ 三个可执行结论（本单的实质增量）

1. **SUBOFF（4 行，唯一可用真值）**：R1 闭合，逐行因子 1.0–1.0001，解析恒等误差 ≤0.01 %。它是当前平台唯一能撑起阻力精度判决的真值，`dev.gate_5415` 之类的「对标闸门」实际无真值可对（见结论 3）。
2. **KVLCC2（4 行）是系统性标度误用，不是随机混档**：逐行因子 [0.2766, 0.2657, 0.2594, 0.256]，极差仅 1.0805（<1.15）→ **一个恒定因子（≈0.26，即所需 Rt 是归档值的 ≈3.85 倍）能同时复现 4 行**。六种候选误用模式无一 ACCEPTED，故因子来源仍未识别：欠账的正确形态是「认出这 4 行共用哪个标度/单位约定」，而不是「逐行找不同来源」。
3. **DTMB-5415（4 行）无恒定因子可解释**：因子 [3.8759, 3.9873, 3.6076, 3.2645]，极差 1.2214（>1.15），且两种航速读法下（条件值 / 归档实测值）都远离物理带 → Ct 与 Rt 来自不同来源，必须成对重取原始表。这条对 `dev.gate_5415` 是直接裁决：**该闸门当前不具备可对的真值**。

## ⑤ 注意点 / 局限 / 适用范围

- **带宽 15 % 是声明值不是真值**：Holtrop–Mennen 湿面积回归在 Cb>0.8 的肥大型船体上残差较大，收紧带宽会把「本来可救」的行误判为不可救。账本把它写成常数 `SURFACE_BAND` 并有反向守卫测试（`test_band_widening_would_promote_rows_that_must_stay_withheld`）证明放宽到 0.80 会翻转结论。
- **`Irrecoverable` ≠ 该行数据一定是假的**：它只说明「在船型物理带内不存在任何 S 能让 (Ct, Rt) 同时成立」。若原始试验确实用了非物理的参考面积（例如按桨盘面积或单面湿面积记账），结论会变 —— 这正是 `datum_request` 要问的问题。
- **恒定因子 0.26 的成因本单未识别**：六种候选模式全被否决，本单不下结论、也不据此改数（honest）。
- **不改正典、不改任何归档值**：本模块只读。正典判定权仍在 `benchmark_canon`（#272）。
- **不构成本单的精度声明**：平台对 8 行 WITHHELD 数据**仍然无法**给出阻力精度 KPI；本单只把这份欠账变成可派单的清单。

## ⑥ 误差判定（逐项，含「是否 <3%」的直接回答）

| 项目 | 基准 | 本单复算 | 误差 | 是否 <3 % |
|---|---|---|---|---|
| R1 反解往返闭合（3 组样本） | 解析恒等 | 同一 S | <1e-12 | 是 |
| SUBOFF 4 行 R1 闭合 | 归档 (Ct, Rt, V, S=5.42 m²) | 因子 1.0–1.0001 | ≤0.01% | 是 |
| KVLCC2/DTMB-5415 8 行 R1 闭合 | R1 要求因子=1 | 299%（最坏行） | 299% | **否**（>15 % 闸门，故正典判 WITHHELD，本单确认其不可救回）|
| KPI「对标算例 ≥10」 | 指南 KPI | 4 | 赤字 6 例 | **否** |

结论：**关键量（SUBOFF 真值的 R1 闭合）满足 <3 %；8 行 WITHHELD 数据不满足，且本单用反解证明了它们不是「缺一个湿面积」的问题 —— 任何 <3 % 的精度声明在这些行上都无从谈起。**


## ⑦ 给下一次自找活的线索

1. **KVLCC2 标度约定溯源**（一单可做完）：围绕恒定因子 0.26，检验「归档值是否为某口径下的分解分量/某排水量口径下的单位换算」；只要认出约定，4 行即从 `WITHHELD` 变 `RECOVERABLE`。
2. **DTMB-5415 成对重取**：需要原始拖曳试验表 (Ct, Rt, V) 三元组；本单的 `datum_request` 已给出所需不确定度 5 %。
3. **把 `truth-supply` 机制推广**：同一套「反解 → 物理带 → 恒定因子」判据可用于任何「两个归档量不能同时成立」的行（如稳性 GM 与 GZ 表、推进 KT/KQ 与功率）。
