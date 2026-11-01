# 指南 KPI 台账化 v1：12 项硬考核指标从「代码里的字面量」迁到「可复算 + 可追溯序列」（m1596）

**结论速览**：指南 12 项 KPI 中 **6/12 已改为现场 SQL 复算**，另有 **6/12 为带留痕的字面量台账**
（3 项定性指标不入库、不填 0）。首次落库成功：`tensor_guide_tracker` 由 **0 行 → 8 行**
（org=1，小时桶 `2026-11-01T07:00:00+00:00`；重跑幂等，行数不变）。对账发现 **2 项看板数字与现场复算超容差**
（最差 `success_rate` 相对漂移 11.5%），
说明常设看板 json 快照已过期，**本单的数值以现场复算为准**。

## ① 案例介绍（数据背景 / 对象 / 参数表）

考核对象不是船体，而是**平台的考核口径本身**——owner 2026-10-31 定下的 12 项硬考核指标（工具接入 ≥5 类、
无人值守 ≥1000 工况、成功率 ≥90%、谱系追溯 100%、复现 ≥95%、门禁覆盖 100%、知识库 ≥1 万条、对标算例 ≥10、
静水阻力 ≤5%、周期 −50%、IP ≥3 标准/5 专利/5 论文/5 软著、已合并功能复核通过率）。

审计到的三个数据层面事实（只读扫描，2026-11-01T07:57:31Z）：

| 事实 | 证据 |
|---|---|
| `tensor_guide_tracker` 表 **0 行**（指南 KPI 从未入库） | `select count(*) → 0`；后端仅 `/tensor/repro/kpi` 端点写 4 个复现类 key（`backend/api/tensor_repro.py:602`） |
| 常设线 `guide_tracker.py` 只覆盖写单个 `guide_dashboard.json`（无历史、无趋势） | `guide_tracker.py:snapshot()`；json `ts` 停在 2026-10-31 03:55 |
| 12 项中 **6 项是代码里的硬编码字符串**（`"4/5"`/`"机制就绪"`/`"样本 0"`/`"未实测"`…） | `guide_tracker.py:kpis()` 返回字典内的字面量 |

参数表（本单的「几何量」= 规格表规模与数据规模）：

| 参数 | 值 | 说明 |
|---|---|---|
| KPI 规格数 | 12 | DB 类 6 + 台账类 6 |
| 数值型 / 定性 | 9 / 3 | 定性不进 `metric_value`（double NOT NULL 字段塞 0 = 说谎） |
| 本次可落库行 | 8 | = 9 − 1（`repro_rate` 无裁决 → None） |
| 事实库规模 | 终态单 1027；有报告任务 842；`ship_samples` 0；`tensor_repro_verdicts` 0 | 现场 SQL，报告级口径 |

## ② 怎么做的（方法 / 流程 / 工具链）

1. **规格化**：`tools/guide_kpi_ledger.py` 里 `KPI_SPECS` 逐项登记 `key / label / kind(db|ledger) / numeric / target /
   formula / sql / nums / ledger_field`——每一项都自带口径说明，杜绝「数字从哪来的说不清」。
2. **DB 类直取**：`fetch_metrics()` 走 `psql -tAF '\x1f'` 通道（环境无 psycopg2，与 `guide_tracker.py` 同款通道），
   每项一条只读 SQL；`merge_rows()` 按 `how=ratio|identity` 折算。
3. **台账类留痕**：`docs/kpi/literal_ledger.json` 为每条字面量记录 `owner / as_of / evidence / status_text`；
   `build_records()` 超过 14 天自动标 `[STALE]`（本单全部 age 0d）。
4. **落库幂等**：`snapshot_rows()` 只收数值型，`hour_bucket()` 把 `snapshot_at` 截到整点，
   `INSERT … ON CONFLICT (organization_id, metric_key, snapshot_at) DO UPDATE` —— 同小时重跑只更新不增行。
5. **对账**：`reconcile()` 解析 `guide_dashboard.json` 的 `cur` 字符串，**先折算到 KPI 单位再比**（看板把比率写成
   `4/4`、`454/499`，直接比 4 vs 1.000 会误报），容差 2%（相对）。
6. **工件**：`artifacts/m1596_guide_kpi/kpi_snapshot.json`（全量记录+漂移）、`kpi_progress.png`、`kpi_drift.png`；
   图与表由 `tools/gen_guide_kpi_figures.py` / `tools/gen_guide_kpi_report.py` 从 json 程序化生成（零手抄）。
7. **验证**：`pytest tests/test_guide_kpi_ledger.py` → **28 passed**（口径/单位/幂等/转义/台账完整性全覆盖）。

工具链：Python 3.11 + psql（只读事实库）+ pytest 9.2 + matplotlib（Agg）。

## ③ 计算结果（12 项 KPI 快照表）

| # | KPI key | 口径来源 | 实测值 | 目标 | 完成度 | 口径公式 | 留痕 |
|---|---|---|---|---|---|---|---|
| 1 | `qr_pass_rate` | **DB** | 1 | 1 | 100.0% | QR-P 前缀命中 succeeded / QR-P 终态单数 | live SQL（现场直取） |
| 2 | `tool_categories` | 台账 | 4 | 5 | 80.0% | 台账：已真实实现工具类计数（CFD/FEA/DEM/势流切片/经验公式/规范） | guide_tracker（源自 owner 2026-10-31 KPI 定义） @ 2026-11-01；age 0d |
| 3 | `unattended_cases` | **DB** | 65 | 1000 | 6.5% | 扫荡线成功案例去重数（案例级代理口径，每案例≈1-3 工况） | live SQL（现场直取） |
| 4 | `success_rate` | **DB** | 0.7994 | 0.9 | 88.8% | 全程 succeeded / 终态单（succeeded+failed） | live SQL（现场直取） |
| 5 | `lineage_coverage` | **DB** | 0.8199 | 1 | 82.0% | 有报告工件的 mission_id 去重数 / 终态单数（代理口径） | live SQL（现场直取） |
| 6 | `repro_rate` | **DB** | — | 0.95 | — | (pass+partial) / tensor_repro_verdicts 总数；0 裁决 → None（未实测） | live SQL（现场直取） |
| 7 | `gate_coverage` | 台账 | 定性：新线全接，存量单未回填 | — | — | 台账（定性）：新线是否全接机器判据门；存量单未回填 | guide_tracker（owner 2026-10-31 口径） @ 2026-11-01；age 0d |
| 8 | `kb_samples` | **DB** | 0 | 10000 | 0.0% | ship_samples 行数（报告 ≠ 样本，不可混算） | live SQL（现场直取） |
| 9 | `benchmark_cases` | 台账 | 1 | 10 | 10.0% | 台账：已完成的独立对标算例数（KVLCC2@15kn 真算=1） | guide_tracker / KVLCC2@15kn 网关真算 @ 2026-11-01；age 0d |
| 10 | `resistance_accuracy` | 台账 | 定性：未实测 | — | — | 台账（定性）：需 SUBOFF/DTMB 实测对标；当前未实测 | guide_tracker @ 2026-11-01；age 0d |
| 11 | `cycle_reduction` | 台账 | 定性：无基线 | — | — | 台账（定性）：无计时基线，指标不可算 | guide_tracker @ 2026-11-01；age 0d |
| 12 | `ip_assets` | 台账 | 1 | 5 | 20.0% | 台账：计数口径取「已立项/骨架」数（论文骨架 1） | guide_tracker / 5090 线论文骨架 @ 2026-11-01；age 0d |

落库实况（`tensor_guide_tracker`，organization_id=1，snapshot_at=2026-11-01T07:00:00+00:00）：

```
guide.qr_pass_rate         value=1.0        target=1.0
guide.tool_categories      value=4.0        target=5.0
guide.unattended_cases     value=65.0       target=1000.0
guide.success_rate         value=0.7994     target=0.9
guide.lineage_coverage     value=0.8199     target=1.0
guide.kb_samples           value=0.0        target=10000.0
guide.benchmark_cases      value=1.0        target=10.0
guide.ip_assets            value=1.0        target=5.0
```

## ④ 结果图

- `kpi_progress.png`：9 项数值 KPI 完成度横条（绿=DB 现场复算，红=字面量台账），黑色虚线=目标线。
- `kpi_drift.png`：看板快照 vs 现场复算的相对漂移，虚线=2% 容差。

图意：**红色越多，说明该 KPI 越依赖人工留痕而非数据**；漂移图直接给出「看板数字还能不能信」的量化答案。

## ⑤ 注意点（坑 / 局限 / 适用范围）

1. **单位错配坑（本单初版真 bug）**：看板把比率 KPI 写成 `4/4`、`454/499`，而复算是 `1.000`、`0.8199`；
   朴素的"取第一个数字"比较会给出 **3 个假漂移**（已修：`normalize_to_unit()` + 回归测试
   `test_normalize_to_unit_fixes_dash_vs_ratio_mismatch`）。**这是全平台对账类脚本的通用坑，建议其他席位复用该函数。**
2. **标签逐字对齐坑**：`静水阻力 ≤5% / 冰区 ≤10%`（带空格）对上 `静水阻力 ≤5%/冰区 ≤10%` 会变成 `unmapped`，
   假阳性 2 项。已加 `test_all_spec_labels_match_dashboard_names` 作为机器门。
3. **`metric_value` 是 `double precision NOT NULL`**：定性 KPI 不能塞 0（会被下游读成"实测为零"），本单选择**不入库**，
   只在 artifacts json 里标 `numeric=false` —— KPI 台账因此有 3 项"结构性缺口"，这是诚实的口径缺口，不是遗漏。
4. **代理口径未消解**：`unattended_cases` 用「案例级」近似工况级（65 案例 ≈ 65–195 工况，目标 1000 是工况级），
   `lineage_coverage` 用「有报告任务/终态单」近似；两者在样本库（G-SAMPLE）上线后须换精确口径。
5. **`repro_rate` 目前不可算**：`tensor_repro_verdicts` 0 行 → 返回 None（未实测），库表里就不该有这一行；
   这解释了看板写"机制就绪"的原因，但**"机制就绪"不能当分母**。
6. **常设线未接线**：`/srv/tensor/agent/guide_tracker.py` 在 worktree 之外（不可改），本单只交付可被它调用的 ETL；
   建议运维侧把 `snapshot()` 改为调用 `python3 tools/guide_kpi_ledger.py --persist`，即可获得每小时趋势序列。
7. 本单为**数据/口径审计**，不涉及船体物理量；不做计算机时受限（load15 ≥ 100 时仅跑轻量 SQL/pytest/matplotlib）。

## ⑥ 误差判定（关键量 vs 基准，逐项显式回答是否 <3%）

基准 = 常设看板 `guide_dashboard.json` 的 `cur` 字符串（这是平台当前对外呈现的数字）。逐项：

| KPI key | 看板 cur 字符串 | 看板折算值 | 现场复算 | 相对漂移 | 判定 |
|---|---|---|---|---|---|
| `qr_pass_rate` | 4/4（在飞 0） | 1 | 1 | 0.0% | ok |
| `tool_categories` | 4/5 | 4 | 4 | 0.0% | ok |
| `unattended_cases` | 65 案例级 | 65 | 65 | 0.0% | ok |
| `success_rate` | 71.7%（358/499；48h 356/496） | 0.717 | 0.7994 | 11.5% | drift |
| `lineage_coverage` | 454/499 有报告工件 | 0.9098 | 0.8199 | 9.9% | drift |
| `repro_rate` | 机制就绪 | — | — | — | skipped |
| `gate_coverage` | 新线全接 | — | — | — | skipped |
| `kb_samples` | 样本 0（报告 270 篇≠样本） | 0 | 0 | 0.0% | ok |
| `benchmark_cases` | 1 完成+2 在飞 | 1 | 1 | 0.0% | ok |
| `resistance_accuracy` | 未实测 | — | — | — | skipped |
| `cycle_reduction` | 无基线 | — | — | — | skipped |
| `ip_assets` | 论文骨架 1 | 1 | 1 | 0.0% | ok |

| 判定对象 | 误差 | 是否 <3% | 说明 |
|---|---|---|---|
| `success_rate` | 相对 11.5% | **否** | 看板 71.7%（358/499；48h 356/496）（10-31 快照）vs 现场 821/1027；看板数字过期而非算错 |
| `lineage_coverage` | 相对 9.9% | **否** | 看板 454/499 有报告工件 vs 现场 842/1027；报告数涨但终态单涨更快 |
| 其余 6 项 | 0.0% | **是** | 逐字对齐、折算后完全一致 |
| 定性/未实测 4 项 | — | 无基准 | `repro_rate`（0 裁决）、`gate_coverage`/`resistance_accuracy`/`cycle_reduction`（无机器可算口径） |

**诚实结论**：本单交付的 ETL 自身复算与 SQL 事实库是同一数据源（无独立误差可言），真正的"误差"是**看板呈现值 vs 现场值**——
2 项超 3%（11.5% / 9.9%），根因是看板快照滞后（json `ts`=2026-10-31 03:55，本次复算 2026-11-01T07:57:31），
不是算术错误。修法已内建：每小时 `--persist` 落库后，看板与台账同源同时刻，该误差项归零。

---
工件与代码：`tools/guide_kpi_ledger.py`、`tools/gen_guide_kpi_figures.py`、`tools/gen_guide_kpi_report.py`、
`docs/kpi/literal_ledger.json`、`tests/test_guide_kpi_ledger.py`、`artifacts/m1596_guide_kpi/`。
复现：`python3 tools/guide_kpi_ledger.py --persist --json artifacts/m1596_guide_kpi/kpi_snapshot.json`。
