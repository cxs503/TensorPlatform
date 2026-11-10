# 终态↔报告对账门成熟化（IN50-W4-N10）：72h 补报 SLA · 反向核对 · 只读独占路径

> **节点**：IN50-W4-N10｜终态-报告对账门成熟化
> **仓**：TensorPlatform（平台共性机制，零领域语义）
> **分支**：`agent/m10378`　**数据截面**：2026-11-10T17:54Z（prod `tensorplatform` 只读导出）
> **工件根**：`artifacts/reconciliation/`（4 份自哈希对账工件 + 3 图 +
> `summary_numbers.json` 数字单一来源）
> **复核命令**：`python -m pytest tests/test_report_reconciliation.py -q`（18 passed）

---

## 0. 一句话结论

把「终态节点必须有一条可审计报告」这条规矩做成了**机器可跑、可入 CI、可审计三个层面**的东西：
四类账本对账（0.403 s @ 2748 终态节点 × 7914 报告）＋ **每张补报工单自带 72 h 截止时刻**（逾期按桶计龄、
一轮一升级、重放零改动）＋ **结构性只读守卫**（越过白名单的写语句在到达数据库前抛异常）＋
**自哈希工件**（sha256 内嵌，改一个数字即验签失败）＋ **双口径覆盖率 KPI**（落库 prod row id=125）。
本轮实测：覆盖率 **76.19%**（终态口径）／**82.45%**（done 口径）／**28.39%**（blocked 口径），
欠账 **652** 张（done 425 + blocked 227），幻影报告 **10** 条。

---

## 1. 案例介绍（问题与数据面）

### 1.1 问题陈述

战役 DAG 有终态（`done` / `blocked`），科研报告有 `mission_id` 外键，二者是**两张互不引用的表**。
缺了对账，就同时存在四个方向的漏洞：

| 方向 | 漏洞 | 后果 |
|---|---|---|
| 正向 | 节点 done/blocked 但无报告 | 终态无证据，KPI 虚高；honest-FAIL 也可悄悄消失 |
| 反向 | 报告引用不存在的 `mission_id` | 串号 / 凭空报告无人拦 |
| 时间 | 欠账没有截止时刻 | 「按 SLA 派单」= 靠人记性，重启即失忆 |
| 读写 | 对账器顺手写了业务表 | 对账动作污染被审计对象，审计链自毁 |

本节点只解决**平台共性**部分：谁欠账、欠多久、怎么升级、KPI 怎么算、对账器凭什么只读。
域语义（哪些物理量、什么算验收）一概不进本仓（见 §5.6 的零领域语义自检）。

### 1.2 数据面（只读导出的生产横截面）

| 量 | 值 | 说明 |
|---|---|---|
| `campaign_nodes` 总数 | 3927 | done 2423 + blocked 325 + in-flight 694 + pending 485 |
| **终态节点** | **2748** | 对账扫描域（`status ∈ {done, blocked}`） |
| 终态节点中**无 `mission_id`** | **10** | 验收门类节点，无法经 mission 关联报告 → 单列不可对账桶 |
| `tensor_reports` 总数 | 7977 | 其中带 `mission_id` 的 **7914**（63 条无 mission，不在对账域） |
| `tensor_dev_missions` | 10455 | 反向核对用来区分「幻影」与「非战役报告」 |
| `campaign_plans` | 305 | 节点租户归属（`organization_id`）来源 |

> 导出与重建：`artifacts/reconciliation/snapshot/manifest.json` 记录导出 SQL、CSV 与快照库的 sha256
> （快照库 8.35 MB 未入库，按 manifest 一条命令即可重建）。沙箱无 `psycopg2`，故用
> `tools/reconciliation/pg_snapshot.py` 把只读 CSV 装进 SQLite 后跑全量对账——**这是环境限制，不是设计**（§5.4）。

---

## 2. 怎么做的（方法 / 流程 / 关键设置）

### 2.1 对账四桶 + 两个"不误报"分桶

```
campaign_nodes(2748 终态)                tensor_reports(7914, mission_id 非空)
        │                                             │
        ├── 正向：mission_id → reports ──→ done_with_report / done_no_report
        │                                  blocked_with_report / blocked_no_report
        │
        └── 反向：reports.mission_id → nodes ──→ report_no_node（幻影，GATE）
                                               report_orphan_mission（真实非战役 mission，INFO 不 gate）
                                               report_hash_mismatch（租户/哈希不符，GATE）
```

关键设计（避免把审计变成噪音）：反向命中分两级——`mission_id` **在任何 mission 行里都不存在**才算
幻影（伪造/串号，进 RED）；mission 存在但没挂战役节点是**普通非战役任务报告**，只记 info。
若把后者也算红，全仓 5032 条正常报告会立刻把门染红，门一红就等于没有门。

### 2.2 补报 SLA = 行属性，不是调度器记忆

`migrations/113_report_supplement_sla.sql`（只增列，不改语义、不删行）：

| 列 | 含义 | 为什么落行 |
|---|---|---|
| `sla_hours` | 工单约定时限，默认 72 | 不同批次可给不同时限，口径留在行上 |
| `due_at` | `created_at + sla_hours` | 重启/换派单进程后逾期仍是可 SQL 审计的属性 |
| `escalation_level` | 已升级次数 | 防同一张单反复升级 |
| `escalated_at` | 最近升级时刻 | 幂等闸门（`IS NULL` 才升） |

配套函数（`backend/audit/report_reconcile.py`）：

* `sla_report()` —— 待办/逾期/12 h 内到期/按龄分桶（≤24 / 24–48 / 48–72 / >72 h）/ 按时完成率 /
  逾期工单明细；**112 时代没有 `due_at` 的存量行不获豁免**，按 `created_at + sla_hours` 补齐计算（不回写）。
* `escalate_overdue()` —— 逾期且未升级过的单 → `priority='urgent'`、`escalation_level+1`；
  第二轮 0 改动（同一逾期窗口不做二次升级、不刷事件）。
* `dispatch_missing_reports(..., sla_hours=72)` —— 一次调用完成「派单 + 消账 + 计龄 + 升级」，返回 SLA 汇总。

### 2.3 结构性只读（"不写业务表"不是纪律，是代码）

`tools/reconciliation/report_gate.py::guarded_session` 在引擎上挂 `before_cursor_execute` 钩子，
**语句到达数据库之前**检查：写语句（INSERT/UPDATE/DELETE/CREATE/ALTER/DROP/…）只允许命中白名单——
补报外箱 `tensor_report_supplement_queue`（需 `--dispatch`）与 KPI 表 `tensor_guide_tracker`（需
`--snapshot-kpi`），其余一律 `ReadOnlyViolation`（退出码 1）。
`campaign_nodes` / `campaign_plans` / `tensor_reports` / `tensor_dev_missions` 四张表在**任何**模式下
都只走 SELECT。

### 2.4 自哈希工件（报告里的数字零手抄）

每次运行落一份工件：`scope` / `ledger` / `kpi` / `gate` / `sla` / `dispatch` / `kpi_snapshot`
＋ `provenance`（工具与引擎源码 sha256、git commit、DB scheme 与 URL 哈希——**不落凭据**）
＋ `integrity.payload_sha256`（对剔除 `integrity` 后的规范化 JSON 求 sha256）。
`verify_artifact()` 可随时重算；改一个数字即验签失败（T10）。
`summary_numbers.json` 与所有图都**只从工件读**，因此报告数字与工件不可能漂移。

### 2.5 事件面：可达的事件信箱（诚实版"事件总线"）

目标里的「通过事件总线触发派单」在本平台只有一个半成品面：`campaign_events` 由战役逻辑写，
且**没有通用事件摄取端点**；而对账器又必须对业务表只读。折中做法是把门的事件发到
**只增 JSONL 事件信箱** `artifacts/reconciliation/events/report_gate_events.jsonl`
（`emit_event()`，一行一个事件，`--event-log` 可改路径、`--no-event-log` 可关）：

| 字段 | 用途 |
|---|---|
| `event_type` | `report_gate.reconciliation` / `report_gate.supplement_dispatch`（派单进程只需过滤后者） |
| `gate_status` / `debt_nodes` / `forged_reports` / `coverage_rate` | 消费方免解析工件即可分级 |
| `sla_overdue` / `dispatched` / `escalated` | 逾期与派单动作量 |
| `artifact_sha256` | 事件 ↔ 工件绑定，事件不可能与自己的账本不一致 |

派单进程/守望线按行尾 tail 即可触发补报任务；`tour` 清单位 = `clickable`（工件内）。
**未完成**：把它接到真正的 HTTP 事件总线需要平台侧提供通用事件摄取端点（本轮没有，故不假装有）。

### 2.6 工具链与命令

```bash
# 1) 只读对账（不写任何业务表，CI 可用；RED → 退出码 2）
python -m tools.reconciliation.report_gate \
    --db-url sqlite:///snapshot/prod_snapshot.db --org 1 \
    --sqlite-ram-journal --out artifacts/reconciliation/report_gate_prod_readonly.json

# 2) 派单（写补报外箱，72h SLA；重放幂等）
python -m tools.reconciliation.report_gate --db-url "$DATABASE_URL" --dispatch --sla-hours 72

# 3) 生产 DB 上写 KPI 快照（只写 tensor_guide_tracker）
python -m tools.reconciliation.report_gate --db-url "$DATABASE_URL" --snapshot-kpi

# 4) 图（只读工件）与测试
python -m tools.reconciliation.gen_report_gate_figures
python -m pytest tests/test_report_reconciliation.py -q
```

KPI 口径修正两处（本轮）：

* **节点比**：`coverage = done_with_report / (done_with_report + done_no_report)`。旧式分子是
  「有报告的**任务数**」、分母是「**节点数**」，单位不一致；同一 mission 挂多个终态节点时会低报。
  现按节点计，并同时输出 `report_coverage_rate_done` / `_blocked` 与 `nodes_without_mission`，
  分母不再被"无 mission 节点"悄悄缩小。
* **单位归一**：历史 KPI 行混编（早期波次写 0.730101 / target 1，本模块写百分数 / target 100），
  趋势读取时按「值与本行 target 同时 ≤1 判为比例码」归一到百分数，`raw_value`/`scale` 保留可追溯；
  写入端统一百分数并在 notes 里打 `unit=percent;basis=node`。

---

## 3. 计算结果（全部反查自工件，零手抄）

数字源：`artifacts/reconciliation/summary_numbers.json`（由 4 份工件程序化汇总）。

### 3.1 四类账本（表 1）

| 桶 | 计数 | 性质 |
|---|---|---|
| `done_with_report` | 1996 | 正常 |
| `done_no_report` | **425** | 欠账（GATE） |
| `blocked_with_report` | 90 | honest-FAIL 有报告 |
| `blocked_no_report` | **227** | 欠账（GATE，honest-FAIL 也必须有报告） |
| `report_no_node`（幻影） | **10** | 伪造/串号 mission_id（GATE） |
| `report_hash_mismatch` | 0 | 租户/哈希不符 |
| `report_orphan_mission` | 5032 | 非战役任务报告（INFO，不 gate） |
| `nodes_without_mission` | 10 | 不可对账桶（单列，不进分母） |

恒等式（程序断言）：`1996+425=2421`（done 有 mission 总数）、`90+227=317`（blocked 有 mission 总数）、
`2421+317+10=2748`。

### 3.2 覆盖率三口径（表 2）

| 口径 | 公式 | 值 | 工件字段 |
|---|---|---|---|
| 终态（门指标） | `(done_w + blk_w)/(done_total + blk_total)` | **76.19 %** | `kpi.report_coverage_rate` |
| done | `done_w/(done_w + done_no)` | **82.45 %** | `kpi.report_coverage_rate_done` |
| blocked | `blk_w/(blk_w + blk_no)` | **28.39 %** | `kpi.report_coverage_rate_blocked` |

blocked 口径 28.39% 是全账最薄的一环：**honest-FAIL 的留痕率不到三成**——失败节点的报告最容易欠，
与「honest FAIL 不可覆盖」的纪律正好相反，这是本轮最值得派单追的账。

### 3.3 补报派单与 SLA（表 3）

| 场景 | 候选 | 新派单 | 已在队 | 逾期 | 本轮升级 | 用时 |
|---|---|---|---|---|---|---|
| 首轮派单（全新建票） | 652 | **652** | 0 | 0 | 0 | 0.271 s |
| 计龄后派单（时间前移模拟） | 652 | 0 | 652 | **130** | **130** | 0.403 s |
| 原样重放 | 652 | **0** | 652 | 130 | **0** | — |

分桶（时间前移模拟：20% 单前移 100 h、另 15% 前移 55 h、另 20% 前移 30 h）：

| 龄桶 | ≤24 h | 24–48 h | 48–72 h | >72 h | 合计 |
|---|---|---|---|---|---|
| 工单数 | 294 | 130 | 98 | 130 | **652**（= pending，恒等） |

最大龄 100.0 h，逾期 130 张全部升级为 `urgent`。

### 3.4 KPI 落库

`tensor_guide_tracker` 新增一行（prod 实写）：`id=125`、`metric_key=report_coverage_rate`、
`metric_value=76.19`、`target_value=100.0`、`notes` 内含 `unit=percent;basis=node;source=…artifact sha256=942672…0555`。
历史两行（`id=45/46`，0.730101 / target 1，比例码）保持不动，读取侧归一后与百分数同尺度（§2.5）。

---

## 4. 结果图（全部由工件数据绘制，`tools/reconciliation/gen_report_gate_figures.py`）

| 图 | 文件 | 内容 |
|---|---|---|
| 图 1 | `artifacts/reconciliation/figures/fig1_terminal_ledger.png` | 左：done/blocked × 有/无报告堆叠柱（1996/425、90/227）＋覆盖率标注；右：四桶账本（symlog，含 orphan） |
| 图 2 | `artifacts/reconciliation/figures/fig2_sla_aging.png` | 左：652 张工单按龄分桶（294/130/98/130）；右：pending / overdue / 12h 内到期 / 本轮升级 |
| 图 3 | `artifacts/reconciliation/figures/fig3_dispatch_idempotency.png` | 首轮 652 派单 vs 重放 0；升级 130 vs 0（重放零改动） |

（图内标签为英文，避免沙箱缺中文字体导致豆腐块；数据点与 §3 表格一一对应。）

---

## 5. 注意点（踩过的坑 / 局限 / 适用范围）

### 5.1 本节点与前序节点高度重叠——诚实声明

`backend/audit/report_reconcile.py`（四桶对账、幂等补报外箱、KPI 快照）来自前序节点 p254w1-n01，
**不是本节点新建**。本节点可验证的增量只有六项：

1. **72 h SLA 落到工单行**（`sla_hours/due_at` + `migrations/113`）——此前工单没有截止时刻，"按 SLA 派单"无法机器裁定；
2. **逾期台账与幂等升级**（`sla_report` / `escalate_overdue`，含 112 时代存量行不获豁免）；
3. **CLI + 自哈希工件**（`tools/reconciliation/report_gate.py`，退出码可入 CI，provenance 链）；
4. **结构性只读守卫**（`guarded_session`，把"只读"从纪律变成会抛异常的事实）；
5. **覆盖率口径统一**（节点比 + done/blocked 分口径 + `nodes_without_mission` 显式计数）；
6. **KPI 单位归一**（混编历史可同尺度读；写入端打 `unit=percent`）。

按 S 分级：**capability_extension**（有对照基线的成熟化），**不是** new_capability。

### 5.2 生产库**没有**派单——未完成项（不得粉饰）

652 张欠账单只写进了**快照库**，prod 的 `tensor_report_supplement_queue` 一张也没开。原因：
线上 :8010 跑的是**旧构建**（`/tensor/reconcile` 的 detail 文案与仓内代码不一致，且表里没有 SLA 列），
此时派单会产出 652 张**没有 `due_at`** 的票，正好把本轮要消灭的问题重新种一遍。
**正确顺序**：先上迁移 113 → 部署新构建 → 再 `--dispatch` 首轮开票。本节点不代替 owner 做部署决定。

### 5.3 线上端点与仓内口径的构建漂移

同一时刻：线上 `GET /tensor/reconcile` 报 `blocked_no_report=204`、`report_no_node=5038`；
仓内新构建在同一数据上给 `227` 与 `10`（其余 5028 条归为 `report_orphan_mission` 的 info）。
两者差异是**构建漂移**（旧构建未做幻影/普通两级分离）。本轮只记录、不追平——追平需要一个部署窗口做同数据对拍。

### 5.4 沙箱无 Postgres 驱动

`psycopg2` 在本环境是 stub（`no real driver in loong64 sandbox`），因此走
「psql 只读 `\copy` 导出 CSV → SQLite 快照 → 全量对账」。对账逻辑本身与引擎无关（纯 SQLAlchemy 文本 SQL），
但**结论的边界**要说清楚：这是**生产数据的离线截面**（17:54Z），不是线上实时；快照库未入库（8.35 MB），
由 `snapshot/manifest.json` 的 sha256 + 导出 SQL 重建。

### 5.5 两类结构性排除（会计口径，不是精度）

* 10 个终态节点**无 `mission_id`**（多为验收门节点）：无法关联报告，单列 `nodes_without_mission`，
  **不进覆盖率分母**（否则门永远不可能 100%）。若要覆盖它们，需要另立「门节点证据」语义——不在本节点范围。
* 63 条报告 `mission_id IS NULL`：不在对账域（无法判归属）。
* 时间前移的计龄场景是**模拟**：把 20%/15%/20% 的工单 `created_at` 往前挪 100/55/30 h，
  只为在"没有真实逾期"的干净账上验证分桶与升级逻辑；这些工单的预期应答是它自己的 `due_at`，
  与物理时间无关。**模拟不等于生产已逾期**（生产逾期数仍为 0）。

### 5.6 零领域语义自检

`tests/test_report_reconciliation.py::test_no_domain_semantics_in_implementation` 对
`report_gate.py` / `pg_snapshot.py` / `report_reconcile.py` / `113_*.sql` 做词表扫描
（resistance/propulsion/seakeeping/manoeuvring/stability/cavitation/wigley/hull 边界匹配），全绿。
测试夹具用 `node-alpha`、合成 mission id，不含域词条；对账只读 id/status/metadata。

### 5.7 「事件总线」只做到可达信箱（honest）

平台没有通用事件摄取端点（`campaign_events` 只由战役逻辑写），本工具又在业务表上只读，
故派单触发走**只增 JSONL 事件信箱**（§2.5），由派单进程/守望线 tail 消费。
**这不等价**于一条正式的事件总线：没有投递保证、没有消费位点、没有重放协议。
接 HTTP 事件总线需要一个平台侧摄取端点，属后续集成项（本轮未做，也不假装已做）。

### 5.8 适用范围

适用于任何「终态必须留痕」的 DAG 账本（战役节点、实验run、评测单）；不适用于需要判**报告内容质量**的场景
（内容 QA 另有门）。工件里 `clickable` 列表可直接当运维工作清单，但**单文件体积随欠账线性增长**
（本仓 4 份工件共 ~1.6 MB，因为保留了全量 clickable）；生产若要长期归档建议只留 `ledger/sla/kpi` 摘要。

---

## 6. 误差判定（关键量 vs 基准，逐项）

本节点是**平台机制**，没有物理基准量；因此基准取三类可机器复算的量：**独立 SQL 复算**、
**恒等式**、**幂等/负对照**。逐项如下（"是否 <3%"按要求显式回答）：

| # | 关键量 | 基准（独立来源） | 本工具值 | 偏差 | 是否 <3% |
|---|---|---|---|---|---|
| 1 | 覆盖率（终态口径） | 手写 SQL 直算 `(1996+90)/(2421+317)` = 76.19 % | 76.19 % | **0.0000 %** | ✅ 是（0%） |
| 2 | 覆盖率（done 口径） | SQL `1996/2421` = 82.45 % | 82.45 % | **0.0000 %** | ✅ 是 |
| 3 | 覆盖率（blocked 口径） | SQL `90/317` = 28.39 % | 28.39 % | **0.0000 %** | ✅ 是 |
| 4 | 龄桶合计 = pending | 恒等式 | 294+130+98+130 = 652 = pending | 0 | ✅ 精确 |
| 5 | done 分解 | 恒等式 `w + no = 2421` | 1996+425 | 0 | ✅ 精确 |
| 6 | blocked 分解 | 恒等式 `w + no = 317` | 90+227 | 0 | ✅ 精确 |
| 7 | 派单幂等 | 重放期望 0 新增 / 0 二次升级 | 0 / 0 | 0 | ✅ 精确 |
| 8 | 全量对账耗时 | 门限 30 s（前序节点判据） | 0.268–0.403 s | 约为门限的 1.3 % | ✅ 是 |
| 9 | 负对照（无欠账） | 全报告场景应 GREEN | GREEN（T12） | — | ✅ 通过 |
| 10 | 工件完整性 | 重算 sha256 应等于内嵌值 | 4/4 一致；改 1 个数字即 False（T10） | — | ✅ 通过 |

**结论**：1–7 项为**精确恒等**（偏差 0，远优于 3% 线）；第 8 项耗时余量 74×。
**无物理量基准，因此不涉及"物理误差是否 <3%"的判断**——本节点不产生物理量。honest 说明：若有人把
"覆盖率 76.19%"当成"科研完成度 76.19%"来读，那是误读；它只度量"终态留痕率"，与科学结论正确性无关。

---

## 7. 复现清单（一屏可跑）

```bash
# 单测（18 项，含只读守卫 / 幂等 / 负对照 / 工件篡改 / 事件信箱 / 零领域语义）
python -m pytest tests/test_report_reconciliation.py -q
# 既有对账门回归（21 项，确认未破坏前序节点行为）
cd backend && python -m pytest tests/test_report_reconcile.py -q
# 图（只读工件）
python -m tools.reconciliation.gen_report_gate_figures
# 数字单一来源
cat artifacts/reconciliation/summary_numbers.json
```

| 工件 | sha256（payload） |
|---|---|
| `report_gate_prod_readonly.json` | `a3b24b81ac15914e8144657e9296322999bdc907a4122f910e344e3f1c26a0ab` |
| `report_gate_prod_dispatch.json` | `be7dc0b9d91d6418845398e3503fabff0ba851a1559b682caf66374f1119903f` |
| `report_gate_prod_dispatch_aged.json` | `6b9e9497274a3b6c62f0a1381c6b0978f33a6203b930b71455193312cb3d118e` |
| `report_gate_prod_dispatch_replay.json` | `7fe630adc3574a4247897b13c13302bb3df1a83c99e60d03078761446ae3cf84` |

事件信箱 `artifacts/reconciliation/events/report_gate_events.jsonl`（4 行，由上述 4 份工件
`emit_event()` 回填，事件↔工件哈希一一对应；新运行的 CLI 默认继续追加）。

**工件瘦身（`compact_artifact` / `--compact`）**：`gate.clickable` 与 `dispatch.tickets` 是体量主体，
入库版本各截断到前 20 条并写入 `compaction{*_total,*_dropped}` 记录，随后**重算** `integrity.payload_sha256`——
计数（ledger/kpi/sla/dispatch 的统计量）一个都没动，只是把可重算的运维清单挪出仓库
（对同一 DB 重跑 CLI 即得全量）。工件总量从 1.8 MB 降到 456 KB。

---

## 附录 A：改动清单

| 文件 | 类型 | 内容 |
|---|---|---|
| `backend/audit/report_reconcile.py` | 改 | SLA 列/`sla_report`/`escalate_overdue`/派单带截止时刻；节点比覆盖率 + done/blocked 分口径 + `nodes_without_mission`；KPI 单位归一 + 趋势同秒定序；INSERT 真错误不再被静默吞掉 |
| `backend/migrations/113_report_supplement_sla.sql` | 新 | 只增 4 列 + 逾期扫描索引（112 保持原样，112+113 ≡ 代码内 DDL） |
| `tools/reconciliation/report_gate.py` | 新 | CLI：只读守卫 + 自哈希工件 + 退出码 0/2/1 |
| `tools/reconciliation/pg_snapshot.py` | 新 | 只读 CSV → SQLite 快照（离线规模化复算） |
| `tools/reconciliation/gen_report_gate_figures.py` | 新 | 三图，仅从工件读数 |
| `artifacts/reconciliation/events/report_gate_events.jsonl` | 新 | 只增事件信箱（4 条，事件↔工件哈希绑定） |
| `tests/test_report_reconciliation.py` | 新 | 18 项（T1–T16） |
| `docs/REPORT_RECONCILIATION.md` | 新 | 本文 |
| `artifacts/reconciliation/**` | 新 | 4 工件（已 `--compact` 瘦身，计数未变）+ 3 图 + 事件信箱 + `summary_numbers.json` + `snapshot/manifest.json` |

## 附录 B：判据逐条

| 判据 | 结果 | 证据 |
|---|---|---|
| 四类账本产出并落工件 | ✅ | `ledger` 桶 8 项 + `integrity.payload_sha256` |
| done_no_report 自动进补报队列 | ✅（快照内验证，prod 待部署） | 首轮 652 张，重放 0 张 |
| SLA = 72 h | ✅ | `sla_hours=72`、`due_at−created_at = 72 h ± 2 s`（T1）、逾期桶 T2 |
| 逾期按 SLA 派单/升级 | ✅ | 130 逾期 → 130 升级；重放 0（T3、表 3） |
| 反向核对 mission_id 有效性 | ✅ | 10 条幻影入 `report_no_node`；5032 条普通非战役报告**不误报**（T11） |
| `report_coverage_rate` 写 `tensor_guide_tracker` | ✅ | prod `id=125`；快照路径 T15 |
| 对账器只读业务表 | ✅ | `guarded_session` + T7/T8（越界写抛异常且未落库） |
| 派单触发面可达 | ✅（只增事件信箱，非正式总线） | `events/report_gate_events.jsonl` + T16：事件带 `event_type` 与工件 sha256 |
| 零领域语义 | ✅ | T14 词表扫描 |
| 规模性能 ≤30 s | ✅ | 0.268 / 0.271 / 0.403 s @ 2748×7914 |
| 未完成项如实申报 | ✅ | §5.2（prod 未派单）、§5.3（构建漂移未追平） |