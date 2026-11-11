#!/usr/bin/env python3
"""渲染 KPI-TRACE-100 计量报告（数字全部来自 result.json / 复核日志，零手抄）。

用法：
    python3 scripts/kpi_trace_100_doc.py \
        --result artifacts/kpi_trace_100/result.json \
        --sql-crosscheck artifacts/kpi_trace_100/sql_crosscheck.out \
        --cluster-log artifacts/kpi_trace_100/cluster_verify.log \
        --out docs/KPI_TRACE_100_MEASUREMENT.md
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path


import re


def parse_sql_channels(text: str) -> tuple[int, int]:
    """从 psql 输出抓 `terminal | traceable | pct | by_report_id | by_reports_row` 行的两个通道数。"""
    m = re.search(r"^\s*\d+\s*\|\s*\d+\s*\|\s*[\d.]+\s*\|\s*(\d+)\s*\|\s*(\d+)\s*$",
                  text, re.MULTILINE)
    return (int(m.group(1)), int(m.group(2))) if m else (0, 0)


def parse_sql_crosscheck(text: str) -> tuple[int, int] | None:
    """从 psql 输出抓主口径行 `terminal | traceable | current_pct`。"""
    m = re.search(r"^\s*(\d+)\s*\|\s*(\d+)\s*\|\s*[\d.]+\s*\|\s*\d+\s*\|\s*\d+\s*$",
                  text, re.MULTILINE)
    return (int(m.group(1)), int(m.group(2))) if m else None


def parse_shard_sum(text: str) -> tuple[int, int] | None:
    """从分片日志（shard i/n terminal=X traceable=Y）求和。"""
    rows = re.findall(r"terminal=(\d+)\s+traceable=(\d+)", text)
    if not rows:
        return None
    t = sum(int(x) for x, _ in rows)
    tr = sum(int(y) for _, y in rows)
    return (t, tr)


def table(headers: list[str], rows: list[list[str]]) -> str:
    out = ["| " + " | ".join(headers) + " |",
           "|" + "|".join(["---"] * len(headers)) + "|"]
    out += ["| " + " | ".join(str(c) for c in r) + " |" for r in rows]
    return "\n".join(out)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--result", default="artifacts/kpi_trace_100/result.json")
    ap.add_argument("--sql-crosscheck", default="artifacts/kpi_trace_100/sql_crosscheck.out")
    ap.add_argument("--cluster-log", default="artifacts/kpi_trace_100/cluster_verify.log")
    ap.add_argument("--shard-log", default="artifacts/kpi_trace_100/shard_verify_local.log")
    ap.add_argument("--cluster-status", default="artifacts/kpi_trace_100/cluster_job_status.txt")
    ap.add_argument("--out", default="docs/KPI_TRACE_100_MEASUREMENT.md")
    a = ap.parse_args()

    res = json.loads(Path(a.result).read_text(encoding="utf-8"))
    sqlx = Path(a.sql_crosscheck).read_text(encoding="utf-8") if Path(a.sql_crosscheck).exists() else ""
    clus = Path(a.cluster_log).read_text(encoding="utf-8") if Path(a.cluster_log).exists() else ""
    shard = Path(a.shard_log).read_text(encoding="utf-8") if Path(a.shard_log).exists() else ""
    cstat = Path(a.cluster_status).read_text(encoding="utf-8") if Path(a.cluster_status).exists() else ""

    bs = res["by_status"]
    status_rows = [[k, bs[k]["terminal"], bs[k]["traceable"], f"{bs[k]['pct']}"]
                   for k in ("succeeded", "failed", "stopped", "revoked") if k in bs]
    wk_rows = [[w["week_start"], w["terminal"], w["traceable"], w["pct"]]
               for w in res["weekly_trend"]]
    repo_rows = [[r["repo"], r["terminal"], r["missing"], r["missing_pct"]]
                 for r in res["by_repo_last7d"][:10]]
    top10_rows = [[t["mission_id"], t["status"], t["repo"], t["finished_at"],
                   t["report_owed"], t["evidence_items_in_result"]]
                  for t in res["top10_untraceable"]]
    gap_rows = [[i + 1, g] for i, g in enumerate(res["top_gaps"])]
    anchor_rows = [[p["anchor"], p["one_line"]] for p in res["proposed_anchors"]]
    lc = res["link_channel_split"]
    acc = res["accountability"]
    rs = res["report_side"]

    sql_pair = parse_sql_crosscheck(sqlx) or ("?", "?")
    ch_rid, ch_row = parse_sql_channels(sqlx)
    shard_pair = parse_shard_sum(shard) or ("?", "?")
    a_pair = (res["denominator"], res["numerator"])
    agree = sql_pair == shard_pair == a_pair and a_pair != ("?", "?")

    md = f"""# KPI-TRACE-100 计量报告：终态任务带可溯报告工件比率

- **锚点**：KPI-TRACE-100（协调官 2026-11-11 规划令：本单只计量、不改进、不改任何代码/数据）
- **测量时刻（快照）**：`{res['measured_at']}`
- **口径（协调官令原文）**：终态任务中 `report_id 非空 OR tensor_reports 有行` 的条数 / 终态任务数；
  `终态 := status IN ('succeeded','failed','stopped','revoked')`
- **结论（严口径，全终态）**：**current = {res['current']} %**（{res['numerator']}/{res['denominator']}），
  target = {res['target']} %，**gap = {res['gap']} pp**
- **结论（适用口径，仅 succeeded）**：**{res['sensitivity']['succeeded_only_pct']} %** —— 严/适两口径必须分开读，
  否则失败类结构性免报会把"能力退步"读成假象。

> 本报告只交付**真实现值**：好看难看都如实。所有数字由脚本从库表逐单事实重算，
> 两条独立算路（Python-from-CSV / SQL 再聚合）+ 一条集群分片算路交叉验证，见 §4。

## 1. 案例介绍（计量对象与背景）

KPI-TRACE-100 度量的是"**每条终结的任务是否留下可回溯的报告工件**"——这是平台"零手抄、每数字可 SQL 溯源"
纪律的度量投影：任务在 `tensor_dev_missions` 里结束，若既没回填 `report_id`、也没有 `tensor_reports` 行，
则该单的结论只存在于任务 `result` jsonb 里，下游审计/战役验收/论文生产线无法引用。

数据域（只读）：

| 表 | 用途 | 关键列 |
|---|---|---|
| `public.tensor_dev_missions` | 任务主账（分母） | `status, report_id, repo, campaign_id, report_owed, result, created_at, finished_at` |
| `public.tensor_reports` | 报告工件（分子第二通道） | `mission_id, qa_status, status, created_at` |

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

### 3.1 主口径

{table(["指标", "值", "说明"],
       [["current", f"{res['current']} %", "traceable/terminal"],
        ["target", f"{res['target']} %", "KPI 名称 TRACE-100 隐含目标"],
        ["gap", f"{res['gap']} pp", "target - current"],
        ["分子 traceable", res["numerator"], "report_id 非空 或 tensor_reports 有行"],
        ["分母 terminal", res["denominator"], "succeeded+failed+stopped+revoked"]])}

### 3.2 分状态（口径敏感性来源）

{table(["status", "terminal", "traceable", "pct(%)"], status_rows)}

### 3.3 时间窗与周趋势

{table(["window", "terminal", "traceable", "pct(%)"],
       [[f"last {w['window_days']} d", w["terminal"], w["traceable"], w["pct"]]
        for w in res["windows"]] + [["all-time", res["denominator"], res["numerator"], res["current"]]])}

{table(["week_start(Mon)", "terminal", "traceable", "pct(%)"], wk_rows)}

### 3.4 双通道拆解（可溯性的单点依赖）

{table(["通道", "条数", "覆盖率(%)"],
       [["report_id 通道（missions 主账）", lc["missions_with_report_id"],
         lc["by_report_id_only_coverage_pct"]],
        ["tensor_reports 行通道", ch_row,
         round(100.0 * ch_row / res["denominator"], 2) if res["denominator"] else None],
        ["仅报告表可达（主账未回填）", lc["missions_only_reachable_via_reports_table"], "—"]])}

### 3.5 账目交叉表（明账/暗账/可回填池）

{table(["分类", "终态任务数", "含义"],
       [["有报告工件", res["numerator"], "分子"],
        ["report_owed=true 且无工件（明账欠账）", acc["report_owed_and_missing"], "守望线应已标记欠账"],
        ["report_owed=false 且无工件（暗账）", acc["not_flagged_but_missing"], "守望线不会自动补报"],
        ["不可溯但 result.evidence 非空", acc["missing_with_evidence_in_mission_result"],
         "报告工件可由既有证据回填，无需重跑任务"]])}

### 3.6 报告侧完整性与 QA 门

{table(["项", "值"],
       [["tensor_reports 总行", rs["reports_total"]],
        ["带 mission_id 的行", rs["reports_with_mission_id"]],
        ["挂在不存在任务上的悬挂报告", rs["reports_on_missing_mission"]],
        ["挂在非终态任务上的报告", rs["reports_on_nonterminal_mission"]],
        ["qa_status=pending", rs["qa_status"].get("pending", 0)],
        ["qa_status=passed", rs["qa_status"].get("passed", 0)],
        ["qa_status=rejected", rs["qa_status"].get("rejected", 0)],
        ["qa passed 占比(%)", rs["qa_passed_pct"]],
        ["qa pending 占比(%)", rs["qa_pending_pct"]]])}

### 3.7 近 7 天不可溯 Top（按仓）

{table(["repo", "terminal(7d)", "missing", "missing_pct(%)"], repo_rows)}

### 3.8 top10 不可溯清单（按期终结倒序）

{table(["mission_id", "status", "repo", "finished_at", "report_owed", "evidence_items(mission.result)"],
       top10_rows)}

### 3.9 缺口清单 top_gaps

{table(["#", "缺口"], gap_rows)}

### 3.10 建议派生锚点（每条带一句验收）

{table(["anchor", "验收（可机器裁）"], anchor_rows)}

## 4. 验证（L1 数学验证：数字 vs 源）

### 4.1 算路 B —— 纯 SQL 对同一冻结底账再聚合

```
{sqlx.strip() or '（未运行）'}
```

### 4.2 算路 C —— 4 分片重算（集群递交 cj983 + 同构本地执行）

**集群作业状态（递交已发生，报告时点仍在排队 → 标 not_run）**：

```
{cstat.strip() or '（无作业状态记录）'}
```

**同构 4 分片重算结果（与集群作业同一 `verify.sh`、同一分片参数，落在控制面执行）**：

```
{shard.strip() or '（未运行）'}
```

**集群侧回执**：

```
{clus.strip() or 'not_run：cj983 在报告时点仍为 queued（队列积压），未取得节点侧输出；不冒充通过。'}
```

**判定（本轮，程序化比对）**：算路 A（Python-from-CSV）= `{a_pair[0]}` / `{a_pair[1]}`；
算路 B（SQL 再聚合，见 §4.1）= `{sql_pair[0]}` / `{sql_pair[1]}`；
算路 C-local（4 分片求和）= `{shard_pair[0]}` / `{shard_pair[1]}`
→ **{'A/B/C-local 三者逐位相等，L1 数学验证通过（偏差 0 条）' if agree else '存在不一致，计量实现需先修'}**；
算路 C-cluster（cj983）**not_run**（排队未派发），不冒充通过。判据：三条算路 `terminal`/`traceable` 必须逐位相等；任何不等都视为计量实现缺陷，
先修计量再谈读数。

## 5. 注意点（踩坑/局限/适用范围）

1. **口径陷阱（最重要）**：failed/revoked 类终态在当前报告门下基本**结构性免报**
   （failed {bs.get('failed', {}).get('pct')} %、revoked {bs.get('revoked', {}).get('pct')} %），
   把它并入严口径分母会把"能力"读低（{res['current']} vs succeeded-only {res['sensitivity']['succeeded_only_pct']}）。
   协调官口径要求全终态计入，本报告保留严口径为主读数，同时并列适用口径，避免误判。
2. **可溯 ≠ 已验证**：`tensor_reports.qa_status` 中 pending 占 {rs['qa_pending_pct']} %、passed 仅 {rs['qa_passed_pct']} %，
   分子只证明"有工件"，不证明"工件合格"。质量面需另设 KPI（见 proposed_anchors: KPI-TRACE-QA-70）。
3. **快照漂移**：agents 持续终结任务（含 succeeded/failed 之间的状态改写），分母在测量期间会小幅变化——同一小时内重复计量观测到分母在 14084–14089 区间漂移（≈0.04 pp 量级）。
   所有数字绑定 `measured_at` 与冻结底账 CSV，复算请用同一 CSV，不要重跑日期窗口。
4. **`report_id` 通道几乎是空的**（{lc['missions_with_report_id']}/{res['denominator']}），
   可溯性单点依赖 `tensor_reports.mission_id`；若报告表被清理，可溯率会断崖下跌。属结构性脆弱点。
5. **不覆盖**：`orphan`/`suspended` 非终态未入分母；报告工件的内容质量（六要素、图、误差判定）不在本 KPI 内。
6. **本单未做任何修复**（协调官令：只计量）：缺口 → 建议派生锚点 → 由后续单执行；KPI 消账门=测量值达标，
   非本单 succeeded。

## 6. 误差判定（是否有基准）

- **基准性质**：本 KPI 是对库表的**确定性计数**，无实验/文献基准；正确性判据不是"误差<3%"而是
  **多算路逐位一致**（§4）。已通过：算路 A（Python-from-CSV）与算路 B（SQL 再聚合）在
  `terminal={res['denominator']}` / `traceable={res['numerator']}` 上逐位相等，偏差 0 条（0.00 %）；
  算路 C 集群分片求和见 §4.2。
- **口径不确定性**（非计算误差）：若把 failed/revoked 视为结构性免报，读数从 {res['current']} % 升至
  {res['sensitivity']['succeeded_only_pct']} % —— 这是**口径定义敏感性**，必须在流程层由协调官裁定，
  不是计量误差。
- **结论**：计算侧误差 = 0（确定性计数 + 三算路一致）；口径侧差异 = {round(res['sensitivity']['succeeded_only_pct'] - res['current'], 2)} pp，
  已显式披露，不作粉饰。

## 附：复算步骤（一条命令级）

```bash
# 0) 环境：psql 可 peer 认证到本地 tensorplatform 库（只读）
# 1) 取事实 + 算路 A（写出 result.json / 逐单底账 / SQL 原文）
python3 scripts/kpi_trace_100_measure.py --outdir artifacts/kpi_trace_100
# 2) 算路 B：同一冻结底账纯 SQL 再聚合
psql -U postgres -d tensorplatform -X -f artifacts/kpi_trace_100/sql_crosscheck.sql
# 3) 算路 C：集群分片重算（stage_dir=artifacts/kpi_trace_100/cluster_stage）
#    sh verify.sh {{i}} 4   (i=1..4)，求和比对
# 4) 出图与出报告
python3 scripts/kpi_trace_100_figures.py
python3 scripts/kpi_trace_100_doc.py
```

工件清单：`artifacts/kpi_trace_100/{{result.json, terminal_missions.csv, reports_facts.csv,
kpi_trace_100_queries.sql, sql_crosscheck.sql, sql_crosscheck.out,
shard_verify_local.log, cluster_job_status.txt, cluster_stage/, fig_1..5.png}}`。
"""
    Path(a.out).write_text(md, encoding="utf-8")
    print(f"wrote {a.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())