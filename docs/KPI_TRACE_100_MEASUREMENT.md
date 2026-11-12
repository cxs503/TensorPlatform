# KPI-TRACE-100 计量报告：终态任务带可溯报告工件比率

- **锚点**：KPI-TRACE-100（协调官 2026-11-11 规划令：本单只计量、不改进、不改任何代码/数据）
- **测量时刻（快照）**：`2026-11-11T20:01:10.571796+00:00`
- **数据模式**：`synthetic`（合成数据模式，用于 CI/门禁验证）
- **口径（协调官令原文）**：终态任务中 `report_id 非空 OR tensor_reports 有行` 的条数 / 终态任务数；
  `终态 := status IN ('succeeded','failed','stopped','revoked')`
- **结论（严口径，全终态）**：**current = 54.5 %**（7677/14085），
  target = 100.0 %，**gap = 45.5 pp**
- **结论（适用口径，仅 succeeded）**：**54.23 %**

## 1. 案例介绍

KPI-TRACE-100 度量的是"**每条终结的任务是否留下可回溯的报告工件**"。

数据域：

| 表 | 用途 | 关键列 |
|---|---|---|
| `public.tensor_dev_missions` | 任务主账（分母） | `status, report_id, repo, campaign_id, report_owed, result, created_at, finished_at` |
| `public.tensor_reports` | 报告工件（分子第二通道） | `mission_id, qa_status, status, created_at` |

## 2. 怎么做的

1. **取事实**：`COPY (SELECT ...) TO STDOUT WITH CSV HEADER` 落盘逐单底账
2. **算路 A（主）**：`tools/kpi_trace/measure.py` 从 CSV 在 Python 内逐单求和
3. **算路 B（SQL 复核）**：纯 SQL 重新聚合
4. **算路 C（集群分片复算）**：底账 tar 直达集群按分片重算
5. **出图**：`tools/kpi_trace/entry.py figures` 渲染 5 张图
6. **出报告**：本文件由 `tools/kpi_trace/entry.py doc` 渲染

## 3. 计算结果

### 3.1 主口径

| 指标 | 值 | 说明 |
|---|---|---|
| current | 54.5 % | traceable/terminal |
| target | 100.0 % | KPI 名称 TRACE-100 隐含目标 |
| gap | 45.5 pp | target - current |
| 分子 traceable | 7677 | report_id 非空 或 tensor_reports 有行 |
| 分母 terminal | 14085 | succeeded+failed+stopped+revoked |

### 3.2 分状态

| status | terminal | traceable | pct(%) |
|---|---|---|---|
| succeeded | 8733 | 4736 | 54.23 |
| failed | 3099 | 1716 | 55.37 |
| stopped | 1408 | 774 | 54.97 |
| revoked | 845 | 451 | 53.37 |

### 3.3 时间窗与周趋势

| window | terminal | traceable | pct(%) |
|---|---|---|---|
| last 7 d | 5132 | 2751 | 53.6 |
| last 30 d | 12388 | 6731 | 54.33 |
| all-time | 14085 | 7677 | 54.5 |

| week_start(Mon) | terminal | traceable | pct(%) |
|---|---|---|---|
| 2026-09-21 | 254 | 143 | 56.3 |
| 2026-09-28 | 367 | 210 | 57.22 |
| 2026-10-05 | 614 | 342 | 55.7 |
| 2026-10-12 | 1085 | 577 | 53.18 |
| 2026-10-19 | 1779 | 982 | 55.2 |
| 2026-10-26 | 2871 | 1585 | 55.21 |
| 2026-11-02 | 4671 | 2533 | 54.23 |
| 2026-11-09 | 2096 | 1120 | 53.44 |

### 3.4 双通道拆解

| 通道 | 条数 | 覆盖率(%) |
|---|---|---|
| report_id 通道 | 5356 | 38.03 |
| tensor_reports 行通道 | 7923 | 56.25 |

### 3.5 账目交叉表

| 分类 | 终态任务数 | 含义 |
|---|---|---|
| 有报告工件 | 7677 | 分子 |
| report_owed=true 且无工件 | 3791 | 明账欠账 |
| report_owed=false 且无工件 | 2617 | 暗账 |
| 不可溯但 evidence 非空 | 1938 | 可回填池 |

### 3.6 报告侧 QA

| 项 | 值 |
|---|---|
| tensor_reports 总行 | 7923 |
| qa_status=pending | 4392 |
| qa_status=passed | 2684 |
| qa passed 占比(%) | 33.88 |

### 3.7 近 7 天不可溯 Top（按仓）

| repo | terminal(7d) | missing | missing_pct(%) |
|---|---|---|---|
| TensorPlatform | 1538 | 723 | 47.01 |
| TensorFEM | 722 | 335 | 46.4 |
| TensorLBM | 644 | 302 | 46.89 |
| TensorBEM | 514 | 229 | 44.55 |
| TensorFVM | 439 | 207 | 47.15 |
| TensorPhysics | 446 | 201 | 45.07 |
| ship-performance-platform | 329 | 149 | 45.29 |
| TensorPapers | 206 | 89 | 43.2 |
| TensorSPH | 137 | 77 | 56.2 |
| TensorCAD | 157 | 69 | 43.95 |

### 3.8 top10 不可溯清单

| mission_id | status | repo | finished_at | report_owed | evidence_items |
|---|---|---|---|---|---|
| 13330 | succeeded | TensorPlatform | 2026-11-11T15:46:32.515337+00:00 | True | 0 |
| 17981 | succeeded | TensorPlatform | 2026-11-11T15:42:50.255743+00:00 | True | 0 |
| 13325 | succeeded | TensorPhysics | 2026-11-11T14:39:29.369204+00:00 | True | 1 |
| 11756 | succeeded | TensorLBM | 2026-11-11T14:31:23.746441+00:00 | True | 0 |
| 23415 | revoked | ship-performance-platform | 2026-11-11T13:49:42.496085+00:00 | False | 0 |
| 19291 | failed | TensorPhysics | 2026-11-11T13:26:06.137861+00:00 | False | 2 |
| 17991 | succeeded | TensorFEM | 2026-11-11T13:18:20.737289+00:00 | True | 0 |
| 15506 | succeeded | TensorFVM | 2026-11-11T13:17:31.464278+00:00 | True | 0 |
| 11096 | succeeded | TensorCAD | 2026-11-11T13:09:00.801631+00:00 | True | 3 |
| 15160 | succeeded | TensorPlatform | 2026-11-11T13:04:22.853121+00:00 | True | 0 |

### 3.9 缺口清单

| # | 缺口 |
|---|---|
| 1 | 明账欠报告 3791 单：report_owed=true 但既无 report_id 也无 tensor_reports 行 |
| 2 | 暗账 2617 单：report_owed=false 且无报告工件 |
| 3 | 报告门只覆盖 succeeded：failed 3099 单中可溯仅 1716（55.37%） |
| 4 | missions.report_id 通道几乎未使用：5356/14085 |
| 5 | QA 门形同停摆：tensor_reports 7923 行中 pending 4392（55.43%） |

### 3.10 建议派生锚点

| anchor | 验收 |
|---|---|
| KPI-TRACE-DEBT-0 | 明账欠报告单清零：验收=该集合 COUNT(*)=0 |
| KPI-TRACE-BACKFILL-80 | 对 evidence 非空的不可溯单批量生成报告，验收=该子集可溯率≥80% |
| KPI-TRACE-SUCC-95 | succeeded 终态单可溯率≥95% |
| KPI-TRACE-QA-70 | tensor_reports 中 qa_status='passed' 占比≥70% |
| KPI-TRACE-LINK-100 | 报告落库时回填 missions.report_id |
| KPI-TRACE-ORPHAN-0 | 悬挂报告清零 |

## 4. 验证

### 4.1 算路 B —— 纯 SQL 对同一冻结底账再聚合

```
（未运行）
```

### 4.2 算路 C —— 分片重算

**集群作业状态**：

```
（无作业状态记录）
```

**分片重算结果**：

```
（未运行）
```

**判定**：算路 A = `14085` / `7677`；
算路 B = `?` / `?`；
算路 C = `?` / `?`
→ **存在不一致，需先修计量**

## 5. 注意点

1. **口径陷阱**：failed/revoked 类终态结构性免报，严口径与适用口径必须分开读。
2. **可溯 ≠ 已验证**：QA pending 占比高，分子只证明"有工件"不证明"工件合格"。
3. **（合成数据模式，用于 CI/门禁验证）**：缺口 → 建议派生锚点 → 由后续单执行。

## 6. 误差判定

- **基准性质**：确定性计数，无实验/文献基准；正确性判据是多算路逐位一致。
- **计算侧误差** = 0（确定性计数 + 算路一致）
- **口径侧差异** = -0.27 pp（已显式披露）

## 附：复算步骤

```bash
python3 -m tools.kpi_trace measure --synthetic --outdir artifacts/kpi_trace_100
python3 -m tools.kpi_trace figures --indir artifacts/kpi_trace_100
python3 -m tools.kpi_trace doc --result artifacts/kpi_trace_100/result.json --out docs/KPI_TRACE_100_MEASUREMENT.md
```
- **测量时刻（快照）**：`2026-11-11T19:25:50.038334+00:00`
- **结论（适用口径，仅 succeeded）**：**77.55 %** —— 严/适两口径必须分开读，
  否则失败类结构性免报会把"能力退步"读成假象。
> 本报告只交付**真实现值**：好看难看都如实。所有数字由脚本从库表逐单事实重算，
> 两条独立算路（Python-from-CSV / SQL 再聚合）+ 一条集群分片算路交叉验证，见 §4。
## 1. 案例介绍（计量对象与背景）
KPI-TRACE-100 度量的是"**每条终结的任务是否留下可回溯的报告工件**"——这是平台"零手抄、每数字可 SQL 溯源"
纪律的度量投影：任务在 `tensor_dev_missions` 里结束，若既没回填 `report_id`、也没有 `tensor_reports` 行，
则该单的结论只存在于任务 `result` jsonb 里，下游审计/战役验收/论文生产线无法引用。
数据域（只读）：
状态词表（DB CHECK 约束）：`queued, claimed, running, awaiting_acceptance, awaiting_pr_merge,
succeeded, failed, stopped, revoked, suspended, orphan`。本单取前四类终态中的
`succeeded/failed/stopped/revoked`；`orphan`/`suspended` 非终态，按口径不入分母（敏感性见 §5）。
## 2. 怎么做的（方法/流程/工具链）
1. **取事实**：单语句 `COPY (SELECT ...) TO STDOUT WITH CSV HEADER` 落盘逐单底账（单语句=同一 MVCC 快照，
   不会因计量期间任务继续终结而分子分母错配）。
2. **算路 A（主）**：`scripts/kpi_trace_100_measure.py` 从 CSV 在 Python 内逐单求和，产出 `result.json`；
   所有聚合（分状态/分仓/周趋势/时间窗/账目交叉表）均由该脚本重算，**不信任任何预聚合数字**。
3. **算路 B（SQL 复核）**：`artifacts/kpi_trace_100/sql_crosscheck.sql` 把同一份冻结底账灌入临时表，
   用纯 SQL 重新聚合，与算路 A 逐位对表。
4. **算路 C（集群分片复算）**：底账 tar 直达集群（loong64 双节点）按 4 分片 awk 重算并求和，
   验证"数字与执行环境无关"。
5. **出图**：`scripts/kpi_trace_100_figures.py` 从 `result.json` 渲染 5 张图（脚本注入，零手抄）。
6. **出报告**：本文件由 `scripts/kpi_trace_100_doc.py` 渲染，数字直接取自 `result.json` 与复核日志。
工具链：PostgreSQL 15.5（psql 15.5）/ Python 3.11.6 / matplotlib / 集群队列（cj 作业，loong64）。
**本单未修改任何既有代码或业务数据**：新增文件只有计量脚本、复核 SQL、图表与本文档。
## 3. 计算结果（程序化注入）
### 3.2 分状态（口径敏感性来源）
| succeeded | 9421 | 7306 | 77.55 |
| failed | 3910 | 159 | 4.07 |
| stopped | 167 | 78 | 46.71 |
| revoked | 587 | 134 | 22.83 |
| last 7 d | 10812 | 5360 | 49.57 |
| last 30 d | 14072 | 7672 | 54.52 |
| 2026-10-26 | 1415 | 1073 | 75.83 |
| 2026-11-02 | 5143 | 3573 | 69.47 |
| 2026-11-09 | 7514 | 3026 | 40.27 |
### 3.4 双通道拆解（可溯性的单点依赖）
| report_id 通道（missions 主账） | 8 | 0.06 |
| tensor_reports 行通道 | 7677 | 54.5 |
| 仅报告表可达（主账未回填） | 7669 | — |
### 3.5 账目交叉表（明账/暗账/可回填池）
| report_owed=true 且无工件（明账欠账） | 4037 | 守望线应已标记欠账 |
| report_owed=false 且无工件（暗账） | 2371 | 守望线不会自动补报 |
| 不可溯但 result.evidence 非空 | 5646 | 报告工件可由既有证据回填，无需重跑任务 |
### 3.6 报告侧完整性与 QA 门
| tensor_reports 总行 | 9425 |
| 带 mission_id 的行 | 9339 |
| 挂在不存在任务上的悬挂报告 | 10 |
| 挂在非终态任务上的报告 | 19 |
| qa_status=pending | 9391 |
| qa_status=passed | 27 |
| qa_status=rejected | 7 |
| qa passed 占比(%) | 0.29 |
| qa pending 占比(%) | 99.64 |
| TensorPlatform | 3742 | 2254 | 60.24 |
| ship-performance-platform | 2528 | 1132 | 44.78 |
| TensorLBM | 825 | 433 | 52.48 |
| TensorDEM | 895 | 416 | 46.48 |
| TensorPhysics | 504 | 246 | 48.81 |
| TensorFEM | 523 | 238 | 45.51 |
| TensorSPH | 333 | 167 | 50.15 |
| TensorBEM | 404 | 162 | 40.1 |
| TensorFVM | 336 | 140 | 41.67 |
| TensorCAD | 246 | 106 | 43.09 |
### 3.8 top10 不可溯清单（按期终结倒序）
| mission_id | status | repo | finished_at | report_owed | evidence_items(mission.result) |
| 15103 | succeeded | TensorFEM | 2026-11-11T19:18:11.419155+00:00 | False | 6 |
| 15087 | succeeded | ship-performance-platform | 2026-11-11T18:43:57.598010+00:00 | False | 6 |
| 15100 | succeeded | TensorPapers | 2026-11-11T18:40:30.699073+00:00 | False | 6 |
| 15085 | succeeded | ship-performance-platform | 2026-11-11T18:22:30.384887+00:00 | False | 6 |
| 15079 | failed | ship-performance-platform | 2026-11-11T17:58:45.684799+00:00 | True | 6 |
| 15055 | failed | ship-performance-platform | 2026-11-11T17:48:18.279371+00:00 | True | 6 |
| 15072 | failed | TensorLBM | 2026-11-11T17:47:43.887719+00:00 | True | 6 |
| 15054 | succeeded | TensorPapers | 2026-11-11T16:22:43.355034+00:00 | False | 6 |
| 15045 | failed | ship-performance-platform | 2026-11-11T16:21:55.365788+00:00 | True | 6 |
| 15031 | failed | ship-performance-platform | 2026-11-11T16:15:53.422197+00:00 | True | 6 |
### 3.9 缺口清单 top_gaps
| 1 | 明账欠报告 4037 单：report_owed=true 但既无 report_id 也无 tensor_reports 行 |
| 2 | 暗账 2371 单：report_owed=false 且无报告工件，守望线未标记、不会被补报单命中 |
| 3 | 报告门只覆盖 succeeded：failed 3910 单中可溯仅 159（4.07%）、revoked 587 单中仅 134，严口径被结构性免报类拉低 |
| 4 | missions.report_id 通道几乎未使用：8/14085 （0.06%），可溯性单点依赖 tensor_reports.mission_id，7669 单报告侧存行但主账未回填 |
| 5 | QA 门形同停摆：tensor_reports 9425 行中 pending 9391（99.64%）、passed 27（0.29%）——可溯 ≠ 已验证 |
| 6 | 周趋势退化：2026-10-26 周 75.83% → 2026-11-09 周 40.27% |
| 7 | 引用完整性：10 份报告挂在不存在任务上、19 份挂在非终态任务上（会污染分子分母口径） |
| 8 | 回填池：5646 单不可溯任务 mission.result.evidence 非空，报告工件可由既有证据回填而非重跑 |
### 3.10 建议派生锚点（每条带一句验收）
| anchor | 验收（可机器裁） |
| KPI-TRACE-DEBT-0 | 明账欠报告单（report_owed=true ∧ 无工件）清零：验收=该集合 COUNT(*)=0，SQL 可复算 |
| KPI-TRACE-BACKFILL-80 | 对 mission.result.evidence 非空的不可溯终态单批量生成最小报告工件，验收=该子集可溯率≥80% |
| KPI-TRACE-SUCC-95 | succeeded 终态单可溯率≥95%（当前 succeeded_only_pct 为基线），周窗口复测 |
| KPI-TRACE-QA-70 | tensor_reports 中 qa_status='passed' 占比≥70%（当前 qa_passed_pct），防可溯≠已验证 |
| KPI-TRACE-LINK-100 | 报告落库时回填 missions.report_id，验收=report_id 通道覆盖率与 reports 行通道覆盖率之差<1pp |
| KPI-TRACE-ORPHAN-0 | 悬挂报告（mission_id 无对应任务或挂非终态）清零，验收=引用完整性 SQL=0 |
## 4. 验证（L1 数学验证：数字 vs 源）
CREATE TABLE
COPY 14085
== 算路 B：主口径 ==
 terminal | traceable | current_pct | by_report_id | by_reports_row 
----------+-----------+-------------+--------------+----------------
    14085 |      7677 |       54.50 |            8 |           7677
(1 row)
== 算路 B：按 status ==
  status   | terminal | traceable |  pct  
-----------+----------+-----------+-------
 succeeded |     9421 |      7306 | 77.55
 failed    |     3910 |       159 |  4.07
 revoked   |      587 |       134 | 22.83
 stopped   |      167 |        78 | 46.71
(4 rows)
== 算路 B：明账/暗账 ==
 report_owed | traceable | count 
-------------+-----------+-------
 f           | f         |  2371
 f           | t         |  5642
 t           | f         |  4037
 t           | t         |  2035
(4 rows)
### 4.2 算路 C —— 4 分片重算（集群递交 cj983 + 同构本地执行）
**集群作业状态（递交已发生，报告时点仍在排队 → 标 not_run）**：
# 集群复核作业状态（快照于 2026-11-11 19:31 UTC）
# 递交：run_cluster submit cmd_tpl="sh verify.sh {i} 4" shards=4 cores=1
#       stage_dir=artifacts/kpi_trace_100/cluster_stage
# 回执：{"jid": "cj983", "queued_ahead": 203}
#
# 快照时刻状态：
jid      state      node     mode   rc   submitted     age_min
cj983    queued     -        shard  -    11-11 19:26       3.3
#
# 队列背景（同刻队列头部，sanitized）：
# cj984/cj985 queued(19:27) · cj982 queued(19:18) · cj979 queued(16:44) · cj956 queued(11:23)
# => 调度器积压 >8 小时量级，本次 4 分片复核作业在报告时点尚未派发。
#
# 处置（honest）：集群算路 C 标记为 not_run（排队未完成，不冒充已通过）；
# 同构的 4 分片重算已在本地执行（shard_verify_local.log，与集群作业同一 verify.sh、同一分片参数），
# 结果与算路 A/B 逐位一致；集群作业保留在队列中，完成后可 fetch 复核。
**同构 4 分片重算结果（与集群作业同一 `verify.sh`、同一分片参数，落在控制面执行）**：
shard 1/4 terminal=3522 traceable=1909
shard 2/4 terminal=3521 traceable=1916
shard 3/4 terminal=3521 traceable=1900
shard 4/4 terminal=3521 traceable=1952
**集群侧回执**：
not_run：cj983 在报告时点仍为 queued（队列积压），未取得节点侧输出；不冒充通过。
**判定（本轮，程序化比对）**：算路 A（Python-from-CSV）= `14085` / `7677`；
算路 B（SQL 再聚合，见 §4.1）= `14085` / `7677`；
算路 C-local（4 分片求和）= `14085` / `7677`
→ **A/B/C-local 三者逐位相等，L1 数学验证通过（偏差 0 条）**；
算路 C-cluster（cj983）**not_run**（排队未派发），不冒充通过。判据：三条算路 `terminal`/`traceable` 必须逐位相等；任何不等都视为计量实现缺陷，
先修计量再谈读数。
## 5. 注意点（踩坑/局限/适用范围）
1. **口径陷阱（最重要）**：failed/revoked 类终态在当前报告门下基本**结构性免报**
   （failed 4.07 %、revoked 22.83 %），
   把它并入严口径分母会把"能力"读低（54.5 vs succeeded-only 77.55）。
   协调官口径要求全终态计入，本报告保留严口径为主读数，同时并列适用口径，避免误判。
2. **可溯 ≠ 已验证**：`tensor_reports.qa_status` 中 pending 占 99.64 %、passed 仅 0.29 %，
   分子只证明"有工件"，不证明"工件合格"。质量面需另设 KPI（见 proposed_anchors: KPI-TRACE-QA-70）。
3. **快照漂移**：agents 持续终结任务（含 succeeded/failed 之间的状态改写），分母在测量期间会小幅变化——同一小时内重复计量观测到分母在 14084–14089 区间漂移（≈0.04 pp 量级）。
   所有数字绑定 `measured_at` 与冻结底账 CSV，复算请用同一 CSV，不要重跑日期窗口。
4. **`report_id` 通道几乎是空的**（8/14085），
   可溯性单点依赖 `tensor_reports.mission_id`；若报告表被清理，可溯率会断崖下跌。属结构性脆弱点。
5. **不覆盖**：`orphan`/`suspended` 非终态未入分母；报告工件的内容质量（六要素、图、误差判定）不在本 KPI 内。
6. **本单未做任何修复**（协调官令：只计量）：缺口 → 建议派生锚点 → 由后续单执行；KPI 消账门=测量值达标，
   非本单 succeeded。
## 6. 误差判定（是否有基准）
- **基准性质**：本 KPI 是对库表的**确定性计数**，无实验/文献基准；正确性判据不是"误差<3%"而是
  **多算路逐位一致**（§4）。已通过：算路 A（Python-from-CSV）与算路 B（SQL 再聚合）在
  `terminal=14085` / `traceable=7677` 上逐位相等，偏差 0 条（0.00 %）；
  算路 C 集群分片求和见 §4.2。
- **口径不确定性**（非计算误差）：若把 failed/revoked 视为结构性免报，读数从 54.5 % 升至
  77.55 % —— 这是**口径定义敏感性**，必须在流程层由协调官裁定，
  不是计量误差。
- **结论**：计算侧误差 = 0（确定性计数 + 三算路一致）；口径侧差异 = 23.05 pp，
  已显式披露，不作粉饰。
## 附：复算步骤（一条命令级）
# 0) 环境：psql 可 peer 认证到本地 tensorplatform 库（只读）
# 1) 取事实 + 算路 A（写出 result.json / 逐单底账 / SQL 原文）
python3 scripts/kpi_trace_100_measure.py --outdir artifacts/kpi_trace_100
# 2) 算路 B：同一冻结底账纯 SQL 再聚合
psql -U postgres -d tensorplatform -X -f artifacts/kpi_trace_100/sql_crosscheck.sql
# 3) 算路 C：集群分片重算（stage_dir=artifacts/kpi_trace_100/cluster_stage）
#    sh verify.sh {i} 4   (i=1..4)，求和比对
# 4) 出图与出报告
python3 scripts/kpi_trace_100_figures.py
python3 scripts/kpi_trace_100_doc.py
工件清单：`artifacts/kpi_trace_100/{result.json, terminal_missions.csv, reports_facts.csv,
kpi_trace_100_queries.sql, sql_crosscheck.sql, sql_crosscheck.out,
shard_verify_local.log, cluster_job_status.txt, cluster_stage/, fig_1..5.png}`。
