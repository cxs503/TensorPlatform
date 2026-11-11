#!/usr/bin/env python3
"""KPI-TRACE-100 计量器：终态任务带可溯报告工件比率。

口径（协调官 2026-11-11 令，锚点 KPI-TRACE-100）：
    traceable = (report_id IS NOT NULL) OR (tensor_reports 存在 mission_id 行)
    denominator = 终态任务数（status IN ('succeeded','failed','stopped','revoked')）
    current = traceable / terminal * 100   [unit = %]
    target  = 100 %

本工具只读：仅执行 SELECT / COPY ... TO STDOUT，不写任何业务表。
数字可复算：所有聚合都在本脚本内从逐单事实表（CSV）重算，SQL 只负责取事实，
便于与 psql 侧聚合（L1 对表）及集群分片重算（独立算路）三方交叉验证。

用法：
    python3 scripts/kpi_trace_100_measure.py \
        --dsn "postgresql://postgres@/tensorplatform?host=/var/run/postgresql" \
        --outdir artifacts/kpi_trace_100

产物：
    artifacts/kpi_trace_100/terminal_missions.csv   逐单事实（可复算底账）
    artifacts/kpi_trace_100/reports_facts.csv        报告侧事实（供 QA 缺口核）
    artifacts/kpi_trace_100/kpi_trace_100_queries.sql 本次所跑 SQL 原文
    artifacts/kpi_trace_100/result.json              KPI 现值 + 缺口 + 建议派生锚点
"""

from __future__ import annotations

import argparse
import csv
import json
import subprocess
import sys
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

TERMINAL_STATUSES = ("succeeded", "failed", "stopped", "revoked")

# ---------------------------------------------------------------- SQL 取数
SQL_TERMINAL = """
COPY (
  SELECT m.id,
         m.status,
         m.repo,
         m.campaign_id,
         m.report_owed,
         (m.report_id IS NOT NULL)                                        AS link_report_id,
         EXISTS (SELECT 1 FROM tensor_reports r
                  WHERE r.mission_id = m.id)                             AS link_report_row,
         m.created_at,
         m.finished_at,
         CASE jsonb_typeof(m.result -> 'evidence')
              WHEN 'array'  THEN jsonb_array_length(m.result -> 'evidence')
              WHEN 'object' THEN (SELECT count(*) FROM jsonb_object_keys(m.result -> 'evidence'))
              ELSE 0 END                                                 AS evidence_items
  FROM tensor_dev_missions m
  WHERE m.status IN ('succeeded','failed','stopped','revoked')
) TO STDOUT WITH CSV HEADER
""".strip()

SQL_REPORTS = """
COPY (
  SELECT r.id,
         r.mission_id,
         r.qa_status,
         r.status,
         (r.mission_id IS NULL)                                          AS no_mission_id,
         (r.mission_id IS NOT NULL
          AND NOT EXISTS (SELECT 1 FROM tensor_dev_missions m
                          WHERE m.id = r.mission_id))                    AS mission_missing,
         (SELECT m.status FROM tensor_dev_missions m
           WHERE m.id = r.mission_id)                                    AS mission_status,
         r.created_at
  FROM tensor_reports r
) TO STDOUT WITH CSV HEADER
""".strip()

SQL_ALL = "\n\n".join([
    "-- KPI-TRACE-100 口径（协调官 2026-11-11）：终态任务带可溯报告工件比率",
    "-- terminal  := status IN ('succeeded','failed','stopped','revoked')",
    "-- traceable := report_id IS NOT NULL OR EXISTS(tensor_reports.mission_id = mission.id)",
    "-- current   := traceable / terminal * 100  [%] , target = 100",
    SQL_TERMINAL,
    "-- 报告侧事实：QA 状态 + 引用完整性（悬挂报告）",
    SQL_REPORTS,
])


def run_copy(dsn: str, sql: str, out_path: Path) -> int:
    """执行 COPY ... TO STDOUT 并落盘为 CSV，返回行数。"""
    proc = subprocess.run(
        ["psql", "-X", "-q", "-d", dsn, "-c", sql],
        capture_output=True, text=True, timeout=600,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"psql failed: {proc.stderr.strip()[:800]}")
    out_path.write_text(proc.stdout, encoding="utf-8")
    return max(len(proc.stdout.splitlines()) - 1, 0)


def read_csv(path: Path) -> list[dict]:
    with path.open(encoding="utf-8", newline="") as fh:
        return list(csv.DictReader(fh))


def truthy(v: str) -> bool:
    return v.strip().lower() in ("t", "true", "1", "yes")


def tf(v: str) -> bool:
    """Postgres COPY 布尔三态：t / f / 空(NULL)。"""
    return v.strip().lower() == "t"


def pct(num: int, den: int) -> float | None:
    return round(100.0 * num / den, 2) if den else None


def traceable_flag(row: dict) -> bool:
    """可溯判定：missions.report_id 非空 或 tensor_reports 存在 mission_id 行（任一通道即可）。"""
    return tf(row.get("link_report_id", "")) or tf(row.get("link_report_row", ""))


def core_metrics(rows: list[dict]) -> dict:
    """主口径计数（纯函数，便于单测；输入 rows 已含 traceable 布尔）。

    口径：traceable = link_report_id OR link_report_row，terminal = len(rows)。
    """
    terminal = len(rows)
    traceable = sum(1 for r in rows if r.get("traceable"))
    return {"terminal": terminal, "traceable": traceable, "pct": pct(traceable, terminal)}


def parse_ts(v: str) -> datetime | None:
    """解析 psql COPY 时间戳：'2026-10-31 00:34:05.83694+00'（微秒位数不定，
    低版本 fromisoformat 不接受，故先用正则规整）。"""
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


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dsn", default="postgresql://postgres@/tensorplatform?host=/var/run/postgresql")
    ap.add_argument("--outdir", default="artifacts/kpi_trace_100")
    ap.add_argument("--now", default=None, help="测量基准时刻(ISO)，默认当前 UTC")
    args = ap.parse_args()

    outdir = Path(args.outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    now = parse_ts(args.now) if args.now else datetime.now(timezone.utc)

    term_csv = outdir / "terminal_missions.csv"
    rep_csv = outdir / "reports_facts.csv"
    n_term = run_copy(args.dsn, SQL_TERMINAL, term_csv)
    n_rep = run_copy(args.dsn, SQL_REPORTS, rep_csv)
    (outdir / "kpi_trace_100_queries.sql").write_text(SQL_ALL + "\n", encoding="utf-8")

    rows = read_csv(term_csv)
    if len(rows) != n_term:
        print(f"WARN: csv rows {len(rows)} != psql count {n_term}", file=sys.stderr)

    # ---- 逐单事实（唯一算路：Python 从 CSV 求和，不信任任何预聚合）
    for r in rows:
        r["traceable"] = traceable_flag(r)
        r["fin"] = parse_ts(r["finished_at"])

    core = core_metrics(rows)
    terminal_n, traceable_n, current = core["terminal"], core["traceable"], core["pct"]

    # 双通道口径拆分（口径敏感性，不改变主口径）
    by_report_id = sum(1 for r in rows if tf(r["link_report_id"]))
    by_row = sum(1 for r in rows if tf(r["link_report_row"]))
    both_only_row = by_row - by_report_id  # 仅靠报告表可溯（missions 侧 report_id 未回填）

    def split(pred) -> dict:
        sub = [r for r in rows if pred(r)]
        t = sum(1 for r in sub if r["traceable"])
        return {"terminal": len(sub), "traceable": t, "pct": pct(t, len(sub))}

    by_status = {s: split(lambda r, s=s: r["status"] == s) for s in TERMINAL_STATUSES}
    succeeded_only = by_status["succeeded"]["pct"]

    # 时间窗（以测量时刻为基准，不用 now()，保证离线可复算）
    def window(days: int) -> dict:
        lo = now - timedelta(days=days)
        sub = [r for r in rows if r["fin"] and r["fin"] >= lo]
        t = sum(1 for r in sub if r["traceable"])
        return {"window_days": days, "terminal": len(sub), "traceable": t,
                "pct": pct(t, len(sub))}

    w7, w30 = window(7), window(30)

    # 周趋势（ISO 周，按 finished_at 归周）
    wk = defaultdict(lambda: [0, 0])
    for r in rows:
        if not r["fin"]:
            continue
        monday = (r["fin"] - timedelta(days=r["fin"].weekday())).date().isoformat()
        wk[monday][0] += 1
        wk[monday][1] += 1 if r["traceable"] else 0
    weekly = [{"week_start": k, "terminal": v[0], "traceable": v[1], "pct": pct(v[1], v[0])}
              for k, v in sorted(wk.items())][-8:]

    # 仓分布（近 7 天）
    lo7 = now - timedelta(days=7)
    repo_tab = defaultdict(lambda: [0, 0])
    for r in rows:
        if r["fin"] and r["fin"] >= lo7:
            repo_tab[r["repo"]][0] += 1
            repo_tab[r["repo"]][1] += 1 if r["traceable"] else 0
    by_repo_7d = sorted(
        ({"repo": k, "terminal": v[0], "missing": v[0] - v[1],
          "missing_pct": pct(v[0] - v[1], v[0])} for k, v in repo_tab.items()),
        key=lambda d: -d["missing"])

    # 欠报告账（系统自报 vs 实际缺失 的交叉表）
    debt = Counter()
    for r in rows:
        debt[(tf(r["report_owed"]), bool(r["traceable"]))] += 1
    owed_missing = debt[(True, False)]
    silently_missing = debt[(False, False)]
    has_evidence_no_report = sum(
        1 for r in rows if not r["traceable"] and int(r["evidence_items"] or 0) > 0)

    # 报告侧：QA 缺口 + 悬挂引用
    reps = read_csv(rep_csv)
    qa = Counter((r["qa_status"] or "") for r in reps)
    rep_total = len(reps)
    dangling = sum(1 for r in reps if tf(r["mission_missing"]))
    no_mid = sum(1 for r in reps if tf(r["no_mission_id"]))
    reports_on_nonterminal = sum(
        1 for r in reps
        if r["mission_status"] and r["mission_status"] not in TERMINAL_STATUSES)

    # top10 不可溯清单（最近终态、无任何报告工件）
    missing = [r for r in rows if not r["traceable"] and r["fin"]]
    missing.sort(key=lambda r: r["fin"], reverse=True)
    top10 = [{
        "mission_id": int(r["id"]),
        "status": r["status"],
        "repo": r["repo"],
        "campaign_id": int(r["campaign_id"]) if r["campaign_id"] else None,
        "finished_at": r["fin"].isoformat(),
        "report_owed": tf(r["report_owed"]),
        "evidence_items_in_result": int(r["evidence_items"] or 0),
    } for r in missing[:10]]

    result = {
        "kpi": "KPI-TRACE-100",
        "definition": ("终态任务带可溯报告工件比率 = 终态任务中 "
                       "(report_id IS NOT NULL OR tensor_reports 有 mission_id 行) 的条数 / 终态任务数"),
        "measured_at": now.isoformat(),
        "current": current,
        "unit": "%",
        "target": 100.0,
        "gap": round(100.0 - current, 2) if current is not None else None,
        "numerator": traceable_n,
        "denominator": terminal_n,
        "data_sources": [
            "postgres:///tensorplatform 表 public.tensor_dev_missions "
            "(status, report_id, repo, campaign_id, report_owed, result, created_at, finished_at)",
            "postgres:///tensorplatform 表 public.tensor_reports "
            "(mission_id, qa_status, status, created_at)",
            "SQL 原文: artifacts/kpi_trace_100/kpi_trace_100_queries.sql",
            "逐单底账: artifacts/kpi_trace_100/terminal_missions.csv",
            "报告侧底账: artifacts/kpi_trace_100/reports_facts.csv",
            "脚本: scripts/kpi_trace_100_measure.py",
        ],
        "link_channel_split": {
            "by_report_id_only_coverage_pct": pct(by_report_id, terminal_n),
            "by_reports_row_pct": pct(by_row, terminal_n),
            "missions_with_report_id": by_report_id,
            "missions_only_reachable_via_reports_table": both_only_row,
        },
        "sensitivity": {
            "all_terminal_pct": current,
            "succeeded_only_pct": succeeded_only,
            "last7d_all_terminal_pct": w7["pct"],
            "last7d_succeeded_only_pct": None,
            "note": ("failed/revoked 类终态任务在现行报告门里基本不要求报告工件，"
                     "严口径(全终态)与适用口径(succeeded)必须分开读数，"
                     "否则‘比率低’会被误判成能力退步。"),
        },
        "by_status": by_status,
        "windows": [w7, w30],
        "weekly_trend": weekly,
        "by_repo_last7d": by_repo_7d,
        "report_side": {
            "reports_total": rep_total,
            "reports_with_mission_id": rep_total - no_mid,
            "reports_on_missing_mission": dangling,
            "reports_on_nonterminal_mission": reports_on_nonterminal,
            "qa_status": dict(qa),
            "qa_passed_pct": pct(qa.get("passed", 0), rep_total),
            "qa_pending_pct": pct(qa.get("pending", 0), rep_total),
        },
        "accountability": {
            "report_owed_and_missing": owed_missing,
            "not_flagged_but_missing": silently_missing,
            "missing_with_evidence_in_mission_result": has_evidence_no_report,
            "note": ("report_owed=true 且无工件 = 明账欠报告；report_owed=false 且无工件 = 暗账。"
                     "第三项说明可回填池规模（mission.result.evidence 已在库里，缺的只是报告工件）。"),
        },
        "top10_untraceable": top10,
        "top_gaps": [
            f"明账欠报告 {owed_missing} 单：report_owed=true 但既无 report_id 也无 tensor_reports 行",
            f"暗账 {silently_missing} 单：report_owed=false 且无报告工件，守望线未标记、不会被补报单命中",
            f"报告门只覆盖 succeeded：failed {by_status['failed']['terminal']} 单中可溯仅 "
            f"{by_status['failed']['traceable']}（{by_status['failed']['pct']}%）、"
            f"revoked {by_status['revoked']['terminal']} 单中仅 {by_status['revoked']['traceable']}，"
            "严口径被结构性免报类拉低",
            f"missions.report_id 通道几乎未使用：{by_report_id}/{terminal_n} "
            f"（{pct(by_report_id, terminal_n)}%），可溯性单点依赖 tensor_reports.mission_id，"
            f"{both_only_row} 单报告侧存行但主账未回填",
            f"QA 门形同停摆：tensor_reports {rep_total} 行中 pending "
            f"{qa.get('pending', 0)}（{pct(qa.get('pending', 0), rep_total)}%）、passed "
            f"{qa.get('passed', 0)}（{pct(qa.get('passed', 0), rep_total)}%）——可溯 ≠ 已验证",
            f"周趋势退化：{weekly[0]['week_start']} 周 {weekly[0]['pct']}% → "
            f"{weekly[-1]['week_start']} 周 {weekly[-1]['pct']}%"
            if len(weekly) >= 2 else "周趋势样本不足",
            f"引用完整性：{dangling} 份报告挂在不存在任务上、"
            f"{reports_on_nonterminal} 份挂在非终态任务上（会污染分子分母口径）",
            f"回填池：{has_evidence_no_report} 单不可溯任务 mission.result.evidence 非空，"
            "报告工件可由既有证据回填而非重跑",
        ],
        "proposed_anchors": [
            {"anchor": "KPI-TRACE-DEBT-0",
             "one_line": "明账欠报告单（report_owed=true ∧ 无工件）清零：验收=该集合 COUNT(*)=0，SQL 可复算"},
            {"anchor": "KPI-TRACE-BACKFILL-80",
             "one_line": "对 mission.result.evidence 非空的不可溯终态单批量生成最小报告工件，验收=该子集可溯率≥80%"},
            {"anchor": "KPI-TRACE-SUCC-95",
             "one_line": "succeeded 终态单可溯率≥95%（当前 succeeded_only_pct 为基线），周窗口复测"},
            {"anchor": "KPI-TRACE-QA-70",
             "one_line": "tensor_reports 中 qa_status='passed' 占比≥70%（当前 qa_passed_pct），防可溯≠已验证"},
            {"anchor": "KPI-TRACE-LINK-100",
             "one_line": "报告落库时回填 missions.report_id，验收=report_id 通道覆盖率与 reports 行通道覆盖率之差<1pp"},
            {"anchor": "KPI-TRACE-ORPHAN-0",
             "one_line": "悬挂报告（mission_id 无对应任务或挂非终态）清零，验收=引用完整性 SQL=0"},
        ],
    }

    (outdir / "result.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")

    print(json.dumps({k: result[k] for k in
                      ("kpi", "current", "unit", "target", "gap", "numerator", "denominator")},
                     ensure_ascii=False))
    print(f"by_status={json.dumps(by_status, ensure_ascii=False)}")
    print(f"windows={json.dumps([w7, w30], ensure_ascii=False)}")
    print(f"weekly={json.dumps(weekly, ensure_ascii=False)}")
    print(f"accountability={json.dumps(result['accountability'], ensure_ascii=False)}")
    print(f"report_side={json.dumps(result['report_side'], ensure_ascii=False)}")
    print(f"wrote {outdir/'result.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())