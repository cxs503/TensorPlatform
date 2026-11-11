# m15120（KPI-TRACE-100）交差审计核验报告

- **审计对象**：m15120（KPI-TRACE-100·谱系追溯 100%·TensorPlatform，已 succeeded）
- **审计任务**：m15149（交差核验单——非重做）
- **审计人**：board-audit（独立席位）
- **审计时刻**：2026-11-11 19:47 UTC

## 1. 案例介绍（审计对象与背景）

m15120 是 KPI-TRACE-100 锚点的**计量单**（协调官 2026-11-11 规划令：本单只计量、不改进、不改任何代码/数据）。
它度量「每条终结的任务是否留下可回溯的报告工件」——即 `report_id 非空 OR tensor_reports 有行` 的条数 / 终态任务数。

数据域（只读）：

| 表 | 用途 | 关键列 |
|---|---|---|
| `tensor_dev_missions` | 任务主账（分母） | `status, report_id, repo, campaign_id, report_owed, result` |
| `tensor_reports` | 报告工件（分子第二通道） | `mission_id, qa_status, status` |

## 2. 怎么做的（审计方法/流程）

本审计按任务榜三件套逐项核验：

1. **run_artifacts 核验**：检查 `artifacts/kpi_trace_100/` 下所有工件是否真实存在（result.json、CSV 底账、SQL 复核、分片日志、5 张图）。
2. **benchmark ref vs got 核验**：对照 `tensor_benchmark_cases` 表（`WHERE anchor_slug='KPI-TRACE-100'`）逐项核 got 数值是否在容差内。
3. **repro_cmd 核验**：实际执行 `python3 scripts/kpi_trace_100_measure.py --dsn ... --outdir ...`，验证可复现。
4. **报告六段核验**：检查 `docs/KPI_TRACE_100_MEASUREMENT.md` 是否六要素齐全。
5. **判级**：按 L0-L5 功能实质分级。

## 3. 计算结果（审计发现）

### 3.1 run_artifacts —— 全部真实存在 ✅

| 工件 | 路径 | 状态 |
|---|---|---|
| 主结果 | `artifacts/kpi_trace_100/result.json` | ✅ 存在（327 行） |
| 逐单底账 | `artifacts/kpi_trace_100/terminal_missions.csv` | ✅ 存在（14086 行） |
| 报告侧事实 | `artifacts/kpi_trace_100/reports_facts.csv` | ✅ 存在（9426 行） |
| SQL 复核 | `artifacts/kpi_trace_100/sql_crosscheck.sql` / `.out` | ✅ 存在 |
| 分片日志 | `artifacts/kpi_trace_100/shard_verify_local.log` | ✅ 存在（4 分片求和=14085/7677） |
| 集群状态 | `artifacts/kpi_trace_100/cluster_job_status.txt` | ✅ 存在（cj983 排队 not_run，honest） |
| 图 | `fig_1_gap.png` ~ `fig_5_debt.png` | ✅ 存在（5 张） |

### 3.2 benchmark ref vs got —— ⚠️ 无基准用例可对照

`tensor_benchmark_cases` 表对 `anchor_slug='KPI-TRACE-100'` **返回 0 行**：

```
SELECT case_name, metric, tolerance FROM tensor_benchmark_cases WHERE anchor_slug='KPI-TRACE-100';
-- 0 rows
```

即：**该锚点未在基准表登记任何参考用例**。m15120 的 got 值 = 54.50%（7677/14085），
无 ref 可对照。m15120 自身报告明确声明「本 KPI 是对库表的确定性计数，无实验/文献基准；
正确性判据不是『误差<3%』而是多算路逐位一致」。

**审计判定**：基准裁判无法应用（无基准用例），但 m15120 的**内部一致性**已通过——
三算路（Python-from-CSV / SQL 再聚合 / 4 分片求和）在 `terminal=14085 / traceable=7677` 上逐位相等，偏差 0 条。

### 3.3 repro_cmd —— ✅ 可执行

实际执行 `python3 scripts/kpi_trace_100_measure.py --dsn "postgresql://postgres@/tensorplatform?host=/var/run/postgresql" --outdir /tmp/kpi_trace_100_repro`：

- 成功产出 `result.json`（current=54.52%，numerator=7693，denominator=14110）
- 数值与冻结快照略有漂移（54.52% vs 54.50%）——因 DB 在测量期间持续有任务终结，属**快照漂移**（报告 §5.3 已显式披露），非计量错误。

### 3.4 报告六段 —— ✅ 齐全

`docs/KPI_TRACE_100_MEASUREMENT.md`（290 行）六要素齐全：
① 案例介绍 ✅ ② 怎么做的 ✅ ③ 计算结果（数字表）✅ ④ 结果图（5 张）✅ ⑤ 注意点（6 条）✅ ⑥ 误差判定（显式声明无基准+多算路一致）✅

### 3.5 判级 —— L2（独立功能实现）

| 级别 | 判据 | 判定 |
|---|---|---|
| L0 文档成果 | 仅文档 | ❌ 有真实代码 |
| L1 数学/单元验证 | 单元测试 | ✅ 有 5 个单元测试，但不止于此 |
| **L2 独立功能实现** | **真代码：输入→计算→输出** | **✅ 计量脚本真实可跑，输入(DB)→计算(聚合)→输出(result.json)** |
| L3 系统集成 | 接入主求解器/平台 | ❌ 独立计量工具，未接入平台主求解器 |
| L4 基准验证 | 完整算例+误差分析+可复现 | ❌ 无基准用例（tensor_benchmark_cases 0 行），且本单为计量非基准验证 |
| L5 科研工程成果 | 科研工程 | ❌ 计量工具，非科研工程 |

**结论：L2**。附百页报告不改变级别；级别=功能实质。

## 4. 验证（审计证据）

- **repro_cmd 实测**：`python3 scripts/kpi_trace_100_measure.py --dsn ... --outdir ...` → 成功产出 result.json（见 §3.3）
- **三算路一致**：A(Python)=14085/7677，B(SQL)=14085/7677，C-local(4分片)=14085/7677，逐位相等
- **报告落库**：`tensor_reports` 有行（id=14248, mission_id=15120, qa_status=pending, status=published）
- **verdict 落表**：`tensor_maturity_verdicts`（id=232, level=2, verdict_by='board-audit'）

## 5. 注意点（审计局限）

1. **基准表缺口**：`tensor_benchmark_cases` 对 KPI-TRACE-100 无基准用例——这是**平台基准基础设施缺口**，非 m15120 自身缺陷。建议后续为 KPI-TRACE-100 登记基准用例（如「多算路一致=0 偏差」）。
2. **快照漂移**：复算时 DB 已变化（54.52% vs 54.50%），所有数字须绑定 `measured_at` 与冻结底账 CSV。
3. **L4 不可判**：因无基准用例，L4（基准验证）无法裁定；m15120 的 L4 判据在 `tensorbench_results` 无实证行 → `CLAIMED_NOT_PROVEN`（与 L2 判定一致，不冲突）。
4. **本审计只核验不重做**：m15120 已 succeeded，本单只做交差核验。

## 6. 误差判定

- **审计对象**：m15120 的计量结果（54.50%）
- **基准**：无（tensor_benchmark_cases 对 KPI-TRACE-100 无行）
- **内部一致性**：三算路逐位一致，偏差 0 条（0.00%）——通过
- **复现性**：repro_cmd 可执行，复现结果与冻结快照一致（漂移 <0.05pp，属快照漂移非误差）
- **结论**：m15120 交付真实、可复现、内部一致；级别=L2；**无虚标**。
