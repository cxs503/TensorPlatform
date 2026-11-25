# Guide KPI Refresh & Gap Re-seeding（m27641）：12 项硬考核指标现场复算 vs 2026-10-31 陈旧快照

**结论速览**：本单以 **psql 直读 tensorplatform 事实库（零手抄）** 重算 guide_dashboard 全部 12 项 KPI，
对比 **2026-10-31 03:55 陈旧快照**（m1596 留档），输出 **6 条缺口**（每条含目标仓）与 **2 条可执行重播种建议**
（P0=SUBOFF 阻力精度重播种进 TensorBEM；P1=工具第 5 类优化库落地）。刷新表/缺口/建议全部**程序化注入**
（`artifacts/m27641_guide_kpi/kpi_refresh.json`），无任何手抄数字。

## ① 案例介绍（数据背景 / 对象 / 参数表）

考核对象不是船体，而是**平台自身的考核口径**——owner 2026-10-31 定下的 12 项硬考核指标
（工具接入 ≥5 类、无人值守 ≥1000 工况、成功率 ≥90%、谱系追溯 100%、复现 ≥95%、门禁覆盖 100%、
知识库 ≥1 万条、对标算例 ≥10、静水阻力 ≤5%、周期 −50%、IP ≥3 标准/5 专利/5 论文/5 软著、已合并功能复核通过率）。

本单的「几何量」= 规格表规模与事实库规模（参数表）：

| 参数 | 值 | 说明 |
|---|---|---|
| KPI 规格数 | 12 | DB 类 6 + 台账类 4 + registry 类 2 |
| 数值型 / 定性 | 10 / 2 | 定性（gate_coverage/cycle_reduction）不入库、不塞 0 |
| 本次可落库行 | 10 | `tensor_guide_tracker` 2026-11-25T10:00 小时桶 |
| 事实库规模（现场 SQL） | 终态单 22079；成功 15301；有报告任务 12779；`ship_samples` 23176；`tensor_repro_verdicts` 1 | 全部 psql 直读 |
| 正典对标 | 12 例（SUBOFF/DTMB-5415/KVLCC2）；DERIVED 4 / WITHHELD 8 | `backend/data/benchmark_reference.json` v3.0 |

## ② 怎么做的（方法 / 流程 / 关键设置 / 工具链）

1. **registry 先决**：`python3 tools/benchmark_settlement.py --write` 把 KPI#9/#10 从「手写字面量」降级为
   **机器可复算**（正典 policy：仅 `reference_status=DERIVED` 可参与 15% 判决；WITHHELD 行不出判决，
   源工件 M398 在 8 例 WITHHELD 上照出 pass = 8 例 `policy_violation`）→ 落盘 `artifacts/benchmark_settlement/audit.json`。
2. **KPI 现场复算**：`python3 tools/guide_kpi_ledger.py --persist --json artifacts/m27641_guide_kpi/kpi_snapshot.json`
   —— 6 项 DB 类走 `psql -tAF` 只读通道（每项一条 SQL，`merge_rows` 按 `how=ratio|identity` 折算）；
   4 项台账类读 `docs/kpi/literal_ledger.json`（超 14 天自动标 `[STALE]`）；2 项 registry 类读决算工件。
   数值型幂等 upsert 进 `tensor_guide_tracker`（小时桶去重，本单 10 行）。
3. **刷新表构建**：`/tmp/build_refresh.py` 读旧快照（m1596 §⑥ 留档的 2026-10-31 cur 字符串）+
   新快照（kpi_snapshot.json）→ 逐项算 `delta`（绝对差 + 相对差）→ 写 `kpi_refresh.json`（程序化注入）。
4. **缺口/重播种**：按「缺口=目标−当前」且「每条含目标仓 + 机器可算验收判据」枚举；重播种按
   「算法级硬缺口优先」（SUBOFF 68% 误差是唯一算法级缺口）。
5. **验证**：`pytest tests/test_guide_kpi_ledger.py` → **27 passed, 1 skipped**（口径/单位/幂等/转义/台账完整性全覆盖）。

工具链：Python 3.11 + psql（只读事实库）+ pytest + matplotlib（Agg，仅出对比图）。

## ③ 计算结果（12 项 KPI 刷新表，程序化注入零手抄）

| # | KPI key | 2026-10-31 旧值 | 2026-11-25 新值 | Δ（绝对） | Δ（相对） | 目标 | 完成度(新) | 口径 |
|---|---|---|---|---|---|---|---|---|
| 1 | `qr_pass_rate` | 1（4/4） | 1（5/5） | +0 | 0.0% | 1.0 | 100% | DB |
| 2 | `tool_categories` | 4 | 4 | +0 | 0.0% | 5.0 | 80% | 台账 [STALE] |
| 3 | `unattended_cases` | 65 | 65 | +0 | 0.0% | 1000 | 6.5% | DB |
| 4 | `success_rate` | 0.717（71.7%） | 0.693（69.3%） | −0.024 | −3.4% | 0.9 | 77% | DB |
| 5 | `lineage_coverage` | 0.9098（90.98%） | 0.5788（57.88%） | −0.331 | −36.4% | 1.0 | 57.9% | DB |
| 6 | `repro_rate` | —（机制就绪） | 1（1/1） | — | — | 0.95 | 105% | DB |
| 7 | `gate_coverage` | —（新线全接） | —（定性） | — | — | — | — | 台账 [STALE] |
| 8 | `kb_samples` | 0 | 23176 | +23176 | — | 10000 | 231.8% | DB |
| 9 | `benchmark_cases` | 1（字面量） | **0**（registry） | −1 | −100% | 10 | 0% | registry |
| 10 | `resistance_accuracy` | —（未实测） | **0**（0/4） | — | — | 1.0 | 0% | registry |
| 11 | `cycle_reduction` | —（无基线） | —（定性） | — | — | — | — | 台账 [STALE] |
| 12 | `ip_assets` | 1 | 1 | +0 | 0.0% | 5 | 20% | 台账 [STALE] |

落库实况（`tensor_guide_tracker`，organization_id=1，snapshot_at=2026-11-25T10:00:00+00，10 行）：
`guide.qr_pass_rate=1 / tool_categories=4 / unattended_cases=65 / success_rate=0.693 / lineage_coverage=0.5788 /
repro_rate=1 / kb_samples=23176 / benchmark_cases=0 / resistance_accuracy=0 / ip_assets=1`。

**关键解读**：
- **kb_samples 已达标**（23176 > 10000，231.8%）——G-SAMPLE 回填管线已生效，从「样本 0」到「超 2 倍」。
- **benchmark_cases 从字面量 1 回落到 registry 0**——这是**口径升级**（m2054 已把该 KPI 降级为机器复算），
  不是能力倒退：旧「1 完成」所依据的 KVLCC2 行在正典里是 WITHHELD，按 policy 不得出判决；新口径下
  4 例 SUBOFF（DERIVED）全部 |err|≈66~69% > 15% → 合格 0。
- **success_rate / lineage_coverage 双双回落**——终态单从 499 涨到 22079（+44 倍），成功/报告增速跟不上
  任务总量增速（大量失败单/未接报告门禁单涌入），是**规模扩张期的真实回落**，不是算错。

## ④ 结果图

- `artifacts/m27641_guide_kpi/kpi_refresh_delta.png`：9 项数值 KPI 旧快照 vs 现场复算的对比柱状图
  （红=2026-10-31 快照，蓝=2026-11-25 现场复算，标注 Δ）。

**无图理由（纯文档/后端任务）**：本单是**数据口径治理**（KPI 刷新/缺口枚举），不涉及船体场量
（无速度场/压力场/阻力曲线等物理量），唯一可画的「比较曲线」就是 KPI 新旧值对比柱状图（已出）。
按 owner 报告纪律，后端/数据任务无场量云图时须显式写明理由——此处即：**无物理场量可画，表格+对比柱状图即全部证据**。

## ⑤ 注意点（踩过的坑 / 局限 / 适用范围）

1. **口径升级 ≠ 能力倒退**：`benchmark_cases` 从 1→0 是 m2054 把「手写字面量」降级为「registry 机器复算」的
   必然结果——旧字面量 1 依据的 KVLCC2 行是 WITHHELD（按 policy 不得出判决），新口径下只有 DERIVED 行可判。
   读刷新表时**必须看口径列**，否则会误读成「能力回退」。
2. **台账 [STALE] 是真实信号**：`tool_categories/gate_coverage/cycle_reduction/ip_assets` 的 as_of=2026-11-01
   已超 14 天未复核 → 这 4 项**无法由数据复核**，是「人工留痕」缺口，不是「实测」缺口。
3. **`metric_value` 是 double NOT NULL**：定性 KPI（gate_coverage/cycle_reduction）不能塞 0（会被下游读成
   「实测为零」），本单选择**不入库**，只在 artifacts json 里标 `numeric=false`——这是诚实的口径缺口。
4. **代理口径未消解**：`unattended_cases` 用「案例级」近似工况级（65 案例 ≈ 65–195 工况，目标 1000 是工况级）；
   `lineage_coverage` 用「有报告任务/终态单」近似。两者在样本库精确口径上线后须换。
5. **SUBOFF 68% 误差是算法级硬缺口**：ITTC-57+Holtrop 算出的 calc_ct（0.0064~0.0073）比正典 ref_ct
   （0.0038~0.0044）高 ~1.77 倍770——这不是「缺数据」，是**阻力算法本身**在 SUBOFF 几何上失准，
   必须进 TensorBEM 修（见 RSEED-1）。
6. **本单为数据/口径治理**，不涉及船体物理量；不做计算机时受限（仅轻量 SQL/pytest/matplotlib）。

## ⑥ 误差判定（关键量 vs 基准，逐项显式回答是否 <3%）

基准 = 2026-10-31 陈旧快照（guide_dashboard.json 的 cur 字符串，m1596 留档）。逐项：

| KPI key | 旧快照值 | 现场复算 | 相对漂移 | 是否 <3% | 判定 |
|---|---|---|---|---|---|
| `qr_pass_rate` | 1 | 1 | 0.0% | **是** | 一致 |
| `tool_categories` | 4 | 4 | 0.0% | **是** | 一致 |
| `unattended_cases` | 65 | 65 | 0.0% | **是** | 一致 |
| `success_rate` | 0.717 | 0.693 | 3.4% | **否** | 回落（终态单 499→22079，+44 倍） |
| `lineage_coverage` | 0.9098 | 0.5788 | 36.4% | **否** | 回落（报告增速 < 任务总量增速） |
| `repro_rate` | — | 1 | — | 无基准 | 旧「机制就绪」不可比；新 1/1 实测 |
| `gate_coverage` | — | — | — | 无基准 | 定性，不入库 |
| `kb_samples` | 0 | 23176 | — | 无基准 | 旧「样本 0」→ 新 23176（达标） |
| `benchmark_cases` | 1 | 0 | 100% | **否** | 口径升级（字面量→registry），非能力回退 |
| `resistance_accuracy` | — | 0 | — | 无基准 | 旧「未实测」→ 新 0/4（registry 实测） |
| `cycle_reduction` | — | — | — | 无基准 | 定性，无计时基线 |
| `ip_assets` | 1 | 1 | 0.0% | **是** | 一致 |

**诚实结论**：本单复算与 SQL 事实库是同一数据源（无独立误差），真正的「误差」是**快照呈现值 vs 现场值**——
3 项超 3%（success_rate 3.4%、lineage_coverage 36.4%、benchmark_cases 100%）。前两项是**规模扩张期真实回落**
（终态单 44 倍增长，成功/报告未同步）；`benchmark_cases` 是**口径升级**（m2054 机器复算取代手写字面量），
不是算术错误。修法已内建：每小时 `--persist` 落库后，看板与台账同源同时刻，该误差项归零。

---
**工件与代码**：`artifacts/m27641_guide_kpi/{kpi_snapshot.json,kpi_refresh.json,kpi_refresh_delta.png}`、
`artifacts/benchmark_settlement/{audit.json,audit.md}`、`tools/guide_kpi_ledger.py`、`tools/benchmark_settlement.py`、
`docs/kpi/literal_ledger.json`、`tests/test_guide_kpi_ledger.py`。
**复现**：`python3 tools/benchmark_settlement.py --write && python3 tools/guide_kpi_ledger.py --persist --json artifacts/m27641_guide_kpi/kpi_snapshot.json`。
