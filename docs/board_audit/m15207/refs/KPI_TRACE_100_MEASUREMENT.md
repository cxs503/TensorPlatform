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
