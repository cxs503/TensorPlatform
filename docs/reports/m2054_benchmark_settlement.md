# m2054 · 对标决算复算：KPI#9/#10 从手写字面量降级为机器可复算（data 席）

**结论先行（honest）**：现存唯一计算侧决算工件（M398 `artifacts/benchmark_validation.json`）
在正典 policy 明令禁止的 8 个 `reference_status=WITHHELD` 算例上照出了 pass/fail 判决；
真正可判决的 4 例（SUBOFF）复算误差 66.73%~68.89%，全部超平台 15% 不可接受线。
因此 KPI#9「对标算例 1 完成」与 KPI#10「阻力精度」在可判决口径下都是 **0**，
字面量与证据不符——本次把两项从字面量降级为 registry 机器复算，并把判决链写成可测代码。

## ① 案例介绍（对象与参数）

对标链条有三段：**实验/文献参考值**（正典）→ **平台计算值**（决算工件）→ **KPI 折算**。

| 环节 | 工件 | 内容 |
| --- | --- | --- |
| 参考侧 | `backend/data/benchmark_reference.json` v3.0 | 12 例：SUBOFF-01..04（Roddy 1990 DTRC-90/012）、DTMB5415-01..04（ITTC V&V 2010）、KVLCC2-01..04（SIMMAN 2008），含 R1~R6 解析恒等式、逐例 `reference_status` 与 `rt_basis` |
| 计算侧 | `artifacts/benchmark_validation.json`（M398） | 同 12 例的 `calc_ct` / `ref_ct` / `error_pct` / `pass`，方法 ITTC-57 + Holtrop |
| KPI 侧 | `docs/kpi/literal_ledger.json`（m1596 建） | KPI#9「对标算例 ≥10」字面量=1；KPI#10「静水阻力 ≤5%」=未实测 |

正典 policy 原文：**「仅 reference_status=DERIVED 的行可参与 15% 精度判决；WITHHELD 行如实标注、不出判决」**。
本次审计的分歧点即由此产生：正典里只有 SUBOFF 4 例是 `DERIVED`，DTMB5415 与 KVLCC2 共 8 例是
`WITHHELD`（`audit_flags` 含 `displacement_closure_fail` / `resistance_not_reviewable`），
而 M398 对这 8 例全部出了 pass/fail。

## ② 怎么做的（方法/流程/工具链）

新增 `tools/benchmark_settlement.py`（纯函数层 + CLI，零外部依赖），五道机器判据：

1. **误差复算**：`err = (calc_ct − ref_ct_canon)/ref_ct_canon`，一律从原始数字重算；
   与工件手写 `error_pct` 对账，偏差 >0.05pp → `typed_field_trusted=false`。
2. **符号完整性**：手写值把有向误差写成绝对值（负误差丢符号）→ `sign_lost`。
3. **正典 R1 自检**：`Rt = Ct·½ρV²S` 逐例复算，与正典 `experimental_rt_n` 比对（tol 0.5%）。
   *正典自身也是被验证对象，不是神谕*。
4. **policy 判决**：`reference_status=WITHHELD` 的行一律 `verdict=WITHHELD`；源工件若已出判决 →
   计 `policy_violation`。
5. **三档判据**（闭区间）：3% 论文级 / 5% 指南 KPI#10 / 15% 平台不可接受线。

随后接线 `tools/guide_kpi_ledger.py`：新增 `kind="registry"`，KPI#9/#10 改为读
`artifacts/benchmark_settlement/audit.json`；**工件缺失 → `None`（未实测）+ `stale=True`，
绝不回落手写字面量**（宁可缺行，不填 0 说谎）。`literal_ledger.json` 对应条目保留作历史字面量与对账参照，
标 `superseded_by`。

复算命令：`python3 tools/benchmark_settlement.py --write`（轻量，纯算术，无重算力）。

## ③ 计算结果（全部由程序从工件读出，零手抄）

12 行决算摘要（`artifacts/benchmark_settlement/audit.json`）：

| 指标 | 值 |
| --- | --- |
| 决算行数 | 12 |
| 可判决（DERIVED） | 4 |
| 不可判决（WITHHELD） | 8 |
| 达标 ≤3% | 0 |
| 达标 ≤5%（KPI#10） | 0 |
| 合格 ≤15%（KPI#9 严格口径） | 0 |
| 最大绝对误差 | 68.8947%（SUBOFF-01） |
| policy 越权判决 | 8 |
| 手写误差字段不可信 / 丢符号 | 4 / 12 · 4 |
| 源工件 pass 判决数 / 与复算冲突 | 5 / 0（可判决行全 fail，方向一致） |
| 正典 R1 自检失败 | 0 / 4 |
| Rt 层决算数 | 0（计算侧从未产出 Rt） |

逐例（可判决 4 例，正典 DERIVED）：

| case | calc_ct | ref_ct（正典） | 复算 err% | 手写 err% |
| --- | --- | --- | --- | --- |
| SUBOFF-01 | 0.006418 | 0.0038 | +68.8947 | 68.89 |
| SUBOFF-02 | 0.006724 | 0.0040 | +68.1000 | 68.10 |
| SUBOFF-03 | 0.007030 | 0.0042 | +67.3810 | 67.38 |
| SUBOFF-04 | 0.007336 | 0.0044 | +66.7273 | 66.72 |

不可判决 8 例（正典 WITHHELD，源工件仍出判决 → 越权）：DTMB5415-01..04 复算
−3.49 / +23.34 / +53.54 / +59.47%，KVLCC2-01..04 复算 +3.07 / −2.90 / −5.58 / −8.09%。
其中 4 例手写 `error_pct` 把负误差写成正值（KVLCC2-02/03/04、DTMB5415-01），误差方向信息丢失。

KPI 折算（`kpi` 块，被台账直接消费）：

| KPI | 字面量（旧） | registry 复算（新） | 目标 |
| --- | --- | --- | --- |
| KPI#9 对标算例数 | 1 | **0** | 10 |
| KPI#10 阻力精度达标率 | 未实测 | **0.0**（0/4） | 1.0 |

![fig1](fig1_per_case_error.png)
![fig2](fig2_kpi_literal_vs_registry.png)

（图 1：逐例 |相对误差|，灰柱 = WITHHELD（policy 禁止判决）；图 2：字面量 vs registry 复算。）

## ④ 注意点（坑 / 局限 / 适用范围）

- **坑 1：正典纪律不是自动执行的**。`reference_status=WITHHELD` 只是数据字段，任何下游消费者都能无视它。
  本次把 policy 写成可执行判据（`is_admissible`）+ 测试，才真正拦得住。
- **坑 2：手写误差字段不可信**。12 例里 4 例手写值丢了符号（把「平台算小了」写成「算大了」），
  另有 8 例只四舍五入到 2 位小数。结论：任何误差必须由原始 `calc/ref` 复算，不许读 `error_pct`。
- **局限 1**：决算只到 **Ct 层**，正典 R1（Rt = Ct·½ρV²S）要求的 Rt 层从未决算（`rt_level_settled=0`）。
  也就是说即便 4 例 SUBOFF 都在 Ct 层失败，Rt 层的对标仍属"未决算"，不是"已达标"。
- **局限 2**：KPI#9 的严格口径（可判决 且 |err|≤15%）是本工具的判定选择，比字面量的"真算=1"更严；
  若 owner 想要宽松口径（有决算即计），改 `summarize()` 一处即可，但必须显式声明并留痕。
- **局限 3**：DTMB5415/KVLCC2 在正典里 WITHHELD 的根因（排水量闭合失败、Froude 溯源不可审）属船舶域问题，
  需在 ship-performance-platform 仓修数据/算法，本单按纪律不动。

## ⑤ 误差判定（逐项，显式回答是否 <3%）

| 关键量 | 基准 | 平台复算 | 相对误差 | 是否 <3% |
| --- | --- | --- | --- | --- |
| SUBOFF-01 Ct | 0.0038（正典，Roddy 1990） | 0.006418 | **+68.89%** | **否** |
| SUBOFF-02 Ct | 0.0040 | 0.006724 | **+68.10%** | **否** |
| SUBOFF-03 Ct | 0.0042 | 0.007030 | **+67.38%** | **否** |
| SUBOFF-04 Ct | 0.0044 | 0.007336 | **+66.73%** | **否** |
| 正典 R1 恒等式（SUBOFF-01..04） | `Ct·½ρV²S` | 正典 `experimental_rt_n` | 最大残差 <0.5%（4/4 PASS） | 是 |
| DTMB5415/KVLCC2（8 例） | 正典 WITHHELD | — | 不出判决 | 不适用 |

**判定：可判决的阻力对标 0/4 达标，最大误差 68.89%，远超 15% 不可接受线与 3% 论文级要求 → 本单为
honest-FAIL（对标精度未达标），不予粉饰。** 本单自身交付物（KPI 口径改造）的正确性由 48 项测试证明
（`tests/test_benchmark_settlement.py` 20 项 + `tests/test_guide_kpi_ledger.py` 28 项全绿）。

## ⑥ 复现

```bash
python3 tools/benchmark_settlement.py --write         # 复算 + 落工件（trivial CPU）
python3 -m pytest tests/test_benchmark_settlement.py tests/test_guide_kpi_ledger.py -q
```

产物：`artifacts/benchmark_settlement/{audit.json,audit.md,fig1_per_case_error.png,fig2_kpi_literal_vs_registry.png}`。