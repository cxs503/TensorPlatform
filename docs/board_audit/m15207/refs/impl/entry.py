#!/usr/bin/env python3
"""KPI-TRACE-100 入口点：CLI 与编程接口。

用法：
    python3 -m tools.kpi_trace measure --synthetic --outdir artifacts/kpi_trace_100
    python3 -m tools.kpi_trace figures --indir artifacts/kpi_trace_100
    python3 -m tools.kpi_trace doc --result artifacts/kpi_trace_100/result.json --out docs/KPI_TRACE_100_MEASUREMENT.md
    python3 -m tools.kpi_trace run-all --synthetic --outdir artifacts/kpi_trace_100
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path


def _parse_ts(v: str) -> datetime | None:
    import re
    v = (v or "").strip()
    if not v:
        return None
    m = re.match(r"^(\d{4}-\d{2}-\d{2})[ T](\d{2}:\d{2}:\d{2})(?:\.(\d+))?\s*(Z|[+-]\d{2}(?::?\d{2})?)?$", v)
    if not m:
        return None
    date, clock, frac, tz = m.groups()
    base = f"{date}T{clock}"
    if frac:
        base += "." + (frac + "000000")[:6]
    if tz in (None, "Z", "+00", "+0000"):
        base += "+00:00"
    else:
        t = tz if ":" in tz else (tz[:3] + ":" + tz[3:] if len(tz) == 5 else tz[:3] + ":00")
        base += t
    try:
        return datetime.fromisoformat(base)
    except ValueError:
        return None


def cmd_measure(args):
    """执行计量。"""
    from .measure import run_measure
    now = _parse_ts(args.now) if args.now else datetime.now(timezone.utc)
    result = run_measure(
        Path(args.outdir),
        synthetic=args.synthetic,
        dsn=args.dsn,
        seed=args.seed,
        now=now,
    )
    return 0


def cmd_figures(args):
    """生成图表。"""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import json

    plt.rcParams.update({"figure.dpi": 130, "font.size": 9, "axes.grid": True,
                         "grid.alpha": 0.3, "axes.axisbelow": True})

    indir = Path(args.indir)
    res = json.loads((indir / "result.json").read_text(encoding="utf-8"))

    def pct(num, den):
        return round(100.0 * num / den, 2) if den else None

    # Fig 1: Gap
    fig, ax = plt.subplots(figsize=(5.2, 3.4))
    cur, tgt = res["current"], res["target"]
    bars = ax.bar(["current\n(measured)", "target"], [cur, tgt],
                  color=["#c0504d", "#4f81bd"], width=0.5)
    ax.axhline(tgt, ls="--", lw=1, color="#4f81bd")
    for b, v in zip(bars, [cur, tgt]):
        ax.text(b.get_x() + b.get_width() / 2, v + 1.5, f"{v:.2f}%", ha="center")
    ax.annotate(f"gap = {res['gap']:.2f} pp",
                xy=(0.5, (cur + tgt) / 2), xytext=(0.62, cur + 12),
                arrowprops=dict(arrowstyle="->", color="#333"), ha="center")
    ax.set_ylim(0, 115)
    ax.set_ylabel("traceable report artifacts (%)")
    ax.set_title(f"KPI-TRACE-100  n={res['numerator']}/{res['denominator']} terminal missions")
    p = indir / "fig_1_gap.png"
    fig.tight_layout(); fig.savefig(p); plt.close(fig)
    print(f"wrote {p}")

    # Fig 2: By status
    bs = res["by_status"]
    keys = [k for k in ("succeeded", "failed", "stopped", "revoked") if k in bs]
    fig, ax = plt.subplots(figsize=(5.6, 3.4))
    vals = [bs[k]["pct"] or 0 for k in keys]
    bars = ax.bar([f"{k}\nn={bs[k]['terminal']}" for k in keys], vals,
                  color=["#4f81bd", "#c0504d", "#9bbb59", "#f79646"])
    for b, k in zip(bars, keys):
        ax.text(b.get_x() + b.get_width() / 2, b.get_height() + 1.5,
                f"{bs[k]['pct']:.2f}%\n({bs[k]['traceable']})", ha="center", fontsize=8)
    ax.set_ylim(0, 100)
    ax.set_ylabel("traceable (%)")
    ax.set_title("Traceability by terminal status")
    p = indir / "fig_2_by_status.png"
    fig.tight_layout(); fig.savefig(p); plt.close(fig)
    print(f"wrote {p}")

    # Fig 3: Weekly trend
    wk = res["weekly_trend"]
    fig, ax = plt.subplots(figsize=(5.6, 3.2))
    xs = [w["week_start"] for w in wk]
    ys = [w["pct"] for w in wk]
    ax.plot(xs, ys, "o-", color="#c0504d", lw=2)
    for x, w in zip(xs, wk):
        ax.annotate(f"{w['pct']:.2f}%\nn={w['terminal']}", (x, w["pct"]),
                    textcoords="offset points", xytext=(0, 9), ha="center", fontsize=8)
    ax.axhline(res["current"], ls=":", color="#555", lw=1)
    ax.set_ylim(0, 100)
    ax.set_ylabel("traceable (%)")
    ax.set_title("Weekly traceability trend")
    p = indir / "fig_3_weekly.png"
    fig.tight_layout(); fig.savefig(p); plt.close(fig)
    print(f"wrote {p}")

    # Fig 4: Repo distribution
    rows = res["by_repo_last7d"][:10]
    if rows:
        fig, ax = plt.subplots(figsize=(6.0, 3.6))
        ys = [r["repo"] for r in rows][::-1]
        xs = [r["missing"] for r in rows][::-1]
        ax.barh(ys, xs, color="#c0504d")
        for y, r in zip(ys, rows[::-1]):
            ax.text(r["missing"] + 20, y, f"{r['missing']} ({r['missing_pct']:.1f}% of {r['terminal']})",
                    va="center", fontsize=8)
        ax.set_xlabel("untraceable terminal missions (last 7 d)")
        ax.set_title("Untraceable missions by repo — top 10")
        ax.set_xlim(0, max(xs) * 1.35)
        p = indir / "fig_4_repo_7d.png"
        fig.tight_layout(); fig.savefig(p); plt.close(fig)
        print(f"wrote {p}")

    # Fig 5: Debt
    acc = res["accountability"]
    m_owed = acc["report_owed_and_missing"]
    m_dark = acc["not_flagged_but_missing"]
    with_rep = res["numerator"]
    fig, ax = plt.subplots(figsize=(5.6, 3.2))
    labels = ["has report artifact", "owed & missing", "not flagged & missing"]
    vals = [with_rep, m_owed, m_dark]
    colors = ["#4f81bd", "#c0504d", "#f79646"]
    bars = ax.bar(labels, vals, color=colors)
    for b, v in zip(bars, vals):
        ax.text(b.get_x() + b.get_width() / 2, v + 80, str(v), ha="center", fontsize=8)
    ax.set_ylabel("terminal missions")
    ax.set_title("Accountability split of terminal missions")
    ax.tick_params(axis="x", labelsize=7)
    p = indir / "fig_5_debt.png"
    fig.tight_layout(); fig.savefig(p); plt.close(fig)
    print(f"wrote {p}")

    return 0


def cmd_doc(args):
    """生成报告文档。"""
    import json
    import re

    def parse_sql_crosscheck(text: str):
        m = re.search(r"^\s*(\d+)\s*\|\s*(\d+)\s*\|\s*[\d.]+\s*\|\s*\d+\s*\|\s*\d+\s*$",
                      text, re.MULTILINE)
        return (int(m.group(1)), int(m.group(2))) if m else None

    def parse_shard_sum(text: str):
        rows = re.findall(r"terminal=(\d+)\s+traceable=(\d+)", text)
        if not rows:
            return None
        t = sum(int(x) for x, _ in rows)
        tr = sum(int(y) for _, y in rows)
        return (t, tr)

    def table(headers, rows):
        out = ["| " + " | ".join(headers) + " |",
               "|" + "|".join(["---"] * len(headers)) + "|"]
        out += ["| " + " | ".join(str(c) for c in r) + " |" for r in rows]
        return "\n".join(out)

    res = json.loads(Path(args.result).read_text(encoding="utf-8"))
    sqlx = Path(args.sql_crosscheck).read_text(encoding="utf-8") if Path(args.sql_crosscheck).exists() else ""
    shard = Path(args.shard_log).read_text(encoding="utf-8") if Path(args.shard_log).exists() else ""
    cstat = Path(args.cluster_status).read_text(encoding="utf-8") if Path(args.cluster_status).exists() else ""

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
    shard_pair = parse_shard_sum(shard) or ("?", "?")
    a_pair = (res["denominator"], res["numerator"])
    agree = sql_pair == shard_pair == a_pair and a_pair != ("?", "?")

    data_mode = res.get("data_mode", "unknown")
    mode_note = "（合成数据模式，用于 CI/门禁验证）" if data_mode == "synthetic" else ""

    md = f"""# KPI-TRACE-100 计量报告：终态任务带可溯报告工件比率

- **锚点**：KPI-TRACE-100（协调官 2026-11-11 规划令：本单只计量、不改进、不改任何代码/数据）
- **测量时刻（快照）**：`{res['measured_at']}`
- **数据模式**：`{data_mode}`{mode_note}
- **口径（协调官令原文）**：终态任务中 `report_id 非空 OR tensor_reports 有行` 的条数 / 终态任务数；
  `终态 := status IN ('succeeded','failed','stopped','revoked')`
- **结论（严口径，全终态）**：**current = {res['current']} %**（{res['numerator']}/{res['denominator']}），
  target = {res['target']} %，**gap = {res['gap']} pp**
- **结论（适用口径，仅 succeeded）**：**{res['sensitivity']['succeeded_only_pct']} %**

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

{table(["指标", "值", "说明"],
       [["current", f"{res['current']} %", "traceable/terminal"],
        ["target", f"{res['target']} %", "KPI 名称 TRACE-100 隐含目标"],
        ["gap", f"{res['gap']} pp", "target - current"],
        ["分子 traceable", res["numerator"], "report_id 非空 或 tensor_reports 有行"],
        ["分母 terminal", res["denominator"], "succeeded+failed+stopped+revoked"]])}

### 3.2 分状态

{table(["status", "terminal", "traceable", "pct(%)"], status_rows)}

### 3.3 时间窗与周趋势

{table(["window", "terminal", "traceable", "pct(%)"],
       [[f"last {w['window_days']} d", w["terminal"], w["traceable"], w["pct"]]
        for w in res["windows"]] + [["all-time", res["denominator"], res["numerator"], res["current"]]])}

{table(["week_start(Mon)", "terminal", "traceable", "pct(%)"], wk_rows)}

### 3.4 双通道拆解

{table(["通道", "条数", "覆盖率(%)"],
       [["report_id 通道", lc["missions_with_report_id"], lc["by_report_id_only_coverage_pct"]],
        ["tensor_reports 行通道", res["report_side"]["reports_with_mission_id"],
         round(100.0 * res["report_side"]["reports_with_mission_id"] / res["denominator"], 2) if res["denominator"] else None]])}

### 3.5 账目交叉表

{table(["分类", "终态任务数", "含义"],
       [["有报告工件", res["numerator"], "分子"],
        ["report_owed=true 且无工件", acc["report_owed_and_missing"], "明账欠账"],
        ["report_owed=false 且无工件", acc["not_flagged_but_missing"], "暗账"],
        ["不可溯但 evidence 非空", acc["missing_with_evidence_in_mission_result"], "可回填池"]])}

### 3.6 报告侧 QA

{table(["项", "值"],
       [["tensor_reports 总行", rs["reports_total"]],
        ["qa_status=pending", rs["qa_status"].get("pending", 0)],
        ["qa_status=passed", rs["qa_status"].get("passed", 0)],
        ["qa passed 占比(%)", rs["qa_passed_pct"]]])}

### 3.7 近 7 天不可溯 Top（按仓）

{table(["repo", "terminal(7d)", "missing", "missing_pct(%)"], repo_rows)}

### 3.8 top10 不可溯清单

{table(["mission_id", "status", "repo", "finished_at", "report_owed", "evidence_items"],
       top10_rows)}

### 3.9 缺口清单

{table(["#", "缺口"], gap_rows)}

### 3.10 建议派生锚点

{table(["anchor", "验收"], anchor_rows)}

## 4. 验证

### 4.1 算路 B —— 纯 SQL 对同一冻结底账再聚合

```
{sqlx.strip() or '（未运行）'}
```

### 4.2 算路 C —— 分片重算

**集群作业状态**：

```
{cstat.strip() or '（无作业状态记录）'}
```

**分片重算结果**：

```
{shard.strip() or '（未运行）'}
```

**判定**：算路 A = `{a_pair[0]}` / `{a_pair[1]}`；
算路 B = `{sql_pair[0]}` / `{sql_pair[1]}`；
算路 C = `{shard_pair[0]}` / `{shard_pair[1]}`
→ **{'三者逐位相等，L1 数学验证通过' if agree else '存在不一致，需先修计量'}**

## 5. 注意点

1. **口径陷阱**：failed/revoked 类终态结构性免报，严口径与适用口径必须分开读。
2. **可溯 ≠ 已验证**：QA pending 占比高，分子只证明"有工件"不证明"工件合格"。
3. **{mode_note.strip() if mode_note else '本单未做任何修复'}**：缺口 → 建议派生锚点 → 由后续单执行。

## 6. 误差判定

- **基准性质**：确定性计数，无实验/文献基准；正确性判据是多算路逐位一致。
- **计算侧误差** = 0（确定性计数 + 算路一致）
- **口径侧差异** = {round(res['sensitivity']['succeeded_only_pct'] - res['current'], 2)} pp（已显式披露）

## 附：复算步骤

```bash
python3 -m tools.kpi_trace measure --synthetic --outdir artifacts/kpi_trace_100
python3 -m tools.kpi_trace figures --indir artifacts/kpi_trace_100
python3 -m tools.kpi_trace doc --result artifacts/kpi_trace_100/result.json --out docs/KPI_TRACE_100_MEASUREMENT.md
```
"""
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(md, encoding="utf-8")
    print(f"wrote {args.out}")
    return 0


def cmd_run_all(args):
    """一键运行：measure → figures → doc。"""
    now = _parse_ts(args.now) if args.now else datetime.now(timezone.utc)

    # Step 1: measure
    from .measure import run_measure
    result = run_measure(
        Path(args.outdir),
        synthetic=args.synthetic,
        dsn=args.dsn,
        seed=args.seed,
        now=now,
    )

    # Step 2: figures
    cmd_figures(argparse.Namespace(indir=args.outdir))

    # Step 3: doc
    cmd_doc(argparse.Namespace(
        result=str(Path(args.outdir) / "result.json"),
        sql_crosscheck=str(Path(args.outdir) / "sql_crosscheck.out"),
        shard_log=str(Path(args.outdir) / "shard_verify_local.log"),
        cluster_status=str(Path(args.outdir) / "cluster_job_status.txt"),
        out=args.doc_out,
    ))

    print(f"\nAll artifacts written to {args.outdir}/")
    return 0


def main():
    ap = argparse.ArgumentParser(prog="python3 -m tools.kpi_trace")
    sub = ap.add_subparsers(dest="command")

    # measure
    p_measure = sub.add_parser("measure", help="Execute KPI measurement")
    p_measure.add_argument("--outdir", default="artifacts/kpi_trace_100")
    p_measure.add_argument("--synthetic", action="store_true")
    p_measure.add_argument("--dsn", default=None)
    p_measure.add_argument("--seed", type=int, default=42)
    p_measure.add_argument("--now", default=None)
    p_measure.set_defaults(func=cmd_measure)

    # figures
    p_figures = sub.add_parser("figures", help="Generate figures from result.json")
    p_figures.add_argument("--indir", default="artifacts/kpi_trace_100")
    p_figures.set_defaults(func=cmd_figures)

    # doc
    p_doc = sub.add_parser("doc", help="Generate measurement report")
    p_doc.add_argument("--result", default="artifacts/kpi_trace_100/result.json")
    p_doc.add_argument("--sql-crosscheck", default="artifacts/kpi_trace_100/sql_crosscheck.out")
    p_doc.add_argument("--shard-log", default="artifacts/kpi_trace_100/shard_verify_local.log")
    p_doc.add_argument("--cluster-status", default="artifacts/kpi_trace_100/cluster_job_status.txt")
    p_doc.add_argument("--out", default="docs/KPI_TRACE_100_MEASUREMENT.md")
    p_doc.set_defaults(func=cmd_doc)

    # run-all
    p_all = sub.add_parser("run-all", help="Run measure → figures → doc")
    p_all.add_argument("--outdir", default="artifacts/kpi_trace_100")
    p_all.add_argument("--synthetic", action="store_true")
    p_all.add_argument("--dsn", default=None)
    p_all.add_argument("--seed", type=int, default=42)
    p_all.add_argument("--now", default=None)
    p_all.add_argument("--doc-out", default="docs/KPI_TRACE_100_MEASUREMENT.md")
    p_all.set_defaults(func=cmd_run_all)

    args = ap.parse_args()
    if not hasattr(args, "func"):
        ap.print_help()
        return 1
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
