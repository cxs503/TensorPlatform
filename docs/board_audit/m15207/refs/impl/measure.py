#!/usr/bin/env python3
"""KPI-TRACE-100 计量器（工具模块版）。

支持两种模式：
1. 数据库模式：连接 PostgreSQL 取真实数据（需 psql 可 peer 认证）
2. 合成模式：使用合成底账（--synthetic），用于 CI/离线验证

口径（协调官 2026-11-11 令，锚点 KPI-TRACE-100）：
    traceable = (report_id IS NOT NULL) OR (tensor_reports 存在 mission_id 行)
    denominator = 终态任务数（status IN ('succeeded','failed','stopped','revoked')）
    current = traceable / terminal * 100   [unit = %]
    target  = 100 %
"""
from __future__ import annotations

import csv
import json
import subprocess
import sys
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

# 从 scripts 导入核心逻辑（避免重复）
_SCRIPT_DIR = Path(__file__).resolve().parents[2] / "scripts"
sys.path.insert(0, str(_SCRIPT_DIR))

# 导入核心函数（如果 scripts 可用）
try:
    from kpi_trace_100_measure import (
        TERMINAL_STATUSES,
        truthy, tf, pct, traceable_flag, core_metrics, parse_ts,
    )
except ImportError:
    # 如果 scripts 不可用，内联定义
    TERMINAL_STATUSES = ("succeeded", "failed", "stopped", "revoked")

    def tf(v: str) -> bool:
        return v.strip().lower() == "t"

    def pct(num: int, den: int) -> float | None:
        return round(100.0 * num / den, 2) if den else None

    def traceable_flag(row: dict) -> bool:
        return tf(row.get("link_report_id", "")) or tf(row.get("link_report_row", ""))

    def core_metrics(rows: list[dict]) -> dict:
        terminal = len(rows)
        traceable = sum(1 for r in rows if r.get("traceable"))
        return {"terminal": terminal, "traceable": traceable, "pct": pct(traceable, terminal)}

    def parse_ts(v: str) -> datetime | None:
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


def read_csv(path: Path) -> list[dict]:
    with path.open(encoding="utf-8", newline="") as fh:
        return list(csv.DictReader(fh))


def compute_kpi(
    terminal_csv: Path,
    reports_csv: Path,
    now: Optional[datetime] = None,
) -> dict:
    """从 CSV 底账计算 KPI（纯函数，可单测）。"""
    if now is None:
        now = datetime.now(timezone.utc)

    rows = read_csv(terminal_csv)
    reps = read_csv(reports_csv) if reports_csv.exists() else []

    # 标记可溯性
    for r in rows:
        r["traceable"] = traceable_flag(r)
        r["fin"] = parse_ts(r["finished_at"])

    core = core_metrics(rows)
    terminal_n, traceable_n, current = core["terminal"], core["traceable"], core["pct"]

    # 双通道拆分
    by_report_id = sum(1 for r in rows if tf(r["link_report_id"]))
    by_row = sum(1 for r in rows if tf(r["link_report_row"]))
    both_only_row = by_row - by_report_id

    def split(pred) -> dict:
        sub = [r for r in rows if pred(r)]
        t = sum(1 for r in sub if r["traceable"])
        return {"terminal": len(sub), "traceable": t, "pct": pct(t, len(sub))}

    by_status = {s: split(lambda r, s=s: r["status"] == s) for s in TERMINAL_STATUSES}
    succeeded_only = by_status["succeeded"]["pct"]

    # 时间窗
    def window(days: int) -> dict:
        lo = now - timedelta(days=days)
        sub = [r for r in rows if r["fin"] and r["fin"] >= lo]
        t = sum(1 for r in sub if r["traceable"])
        return {"window_days": days, "terminal": len(sub), "traceable": t,
                "pct": pct(t, len(sub))}

    w7, w30 = window(7), window(30)

    # 周趋势
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

    # 账目交叉表
    debt = Counter()
    for r in rows:
        debt[(tf(r["report_owed"]), bool(r["traceable"]))] += 1
    owed_missing = debt[(True, False)]
    silently_missing = debt[(False, False)]
    has_evidence_no_report = sum(
        1 for r in rows if not r["traceable"] and int(r["evidence_items"] or 0) > 0)

    # 报告侧
    qa = Counter((r["qa_status"] or "") for r in reps)
    rep_total = len(reps)
    dangling = sum(1 for r in reps if tf(r["mission_missing"]))
    no_mid = sum(1 for r in reps if tf(r["no_mission_id"]))
    reports_on_nonterminal = sum(
        1 for r in reps
        if r["mission_status"] and r["mission_status"] not in TERMINAL_STATUSES)

    # top10 不可溯
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
            f"CSV 底账: {terminal_csv}",
            f"报告侧底账: {reports_csv}",
            "脚本: tools/kpi_trace/measure.py",
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
                     "严口径(全终态)与适用口径(succeeded)必须分开读数。"),
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
            "note": ("report_owed=true 且无工件 = 明账欠报告；report_owed=false 且无工件 = 暗账。"),
        },
        "top10_untraceable": top10,
        "top_gaps": [
            f"明账欠报告 {owed_missing} 单：report_owed=true 但既无 report_id 也无 tensor_reports 行",
            f"暗账 {silently_missing} 单：report_owed=false 且无报告工件",
            f"报告门只覆盖 succeeded：failed {by_status['failed']['terminal']} 单中可溯仅 "
            f"{by_status['failed']['traceable']}（{by_status['failed']['pct']}%）",
            f"missions.report_id 通道几乎未使用：{by_report_id}/{terminal_n}",
            f"QA 门形同停摆：tensor_reports {rep_total} 行中 pending "
            f"{qa.get('pending', 0)}（{pct(qa.get('pending', 0), rep_total)}%）",
        ],
        "proposed_anchors": [
            {"anchor": "KPI-TRACE-DEBT-0",
             "one_line": "明账欠报告单清零：验收=该集合 COUNT(*)=0"},
            {"anchor": "KPI-TRACE-BACKFILL-80",
             "one_line": "对 evidence 非空的不可溯单批量生成报告，验收=该子集可溯率≥80%"},
            {"anchor": "KPI-TRACE-SUCC-95",
             "one_line": "succeeded 终态单可溯率≥95%"},
            {"anchor": "KPI-TRACE-QA-70",
             "one_line": "tensor_reports 中 qa_status='passed' 占比≥70%"},
            {"anchor": "KPI-TRACE-LINK-100",
             "one_line": "报告落库时回填 missions.report_id"},
            {"anchor": "KPI-TRACE-ORPHAN-0",
             "one_line": "悬挂报告清零"},
        ],
    }

    return result


def run_measure(
    outdir: Path,
    synthetic: bool = False,
    dsn: Optional[str] = None,
    seed: int = 42,
    now: Optional[datetime] = None,
) -> dict:
    """执行计量，返回 result dict。

    Args:
        outdir: 输出目录
        synthetic: 是否使用合成数据
        dsn: 数据库连接串（非合成模式）
        seed: 合成数据随机种子
        now: 测量基准时刻
    """
    outdir.mkdir(parents=True, exist_ok=True)

    if synthetic:
        # 使用合成数据
        from .synthetic import generate_all
        gen_result = generate_all(outdir, seed=seed, now=now)
        term_csv = outdir / "terminal_missions.csv"
        rep_csv = outdir / "reports_facts.csv"
        data_mode = "synthetic"
    else:
        # 使用数据库
        if dsn is None:
            dsn = "postgresql://postgres@/tensorplatform?host=/var/run/postgresql"

        SQL_TERMINAL = """
        COPY (
          SELECT m.id, m.status, m.repo, m.campaign_id, m.report_owed,
                 (m.report_id IS NOT NULL) AS link_report_id,
                 EXISTS (SELECT 1 FROM tensor_reports r WHERE r.mission_id = m.id) AS link_report_row,
                 m.created_at, m.finished_at,
                 CASE jsonb_typeof(m.result -> 'evidence')
                      WHEN 'array'  THEN jsonb_array_length(m.result -> 'evidence')
                      WHEN 'object' THEN (SELECT count(*) FROM jsonb_object_keys(m.result -> 'evidence'))
                      ELSE 0 END AS evidence_items
          FROM tensor_dev_missions m
          WHERE m.status IN ('succeeded','failed','stopped','revoked')
        ) TO STDOUT WITH CSV HEADER
        """.strip()

        SQL_REPORTS = """
        COPY (
          SELECT r.id, r.mission_id, r.qa_status, r.status,
                 (r.mission_id IS NULL) AS no_mission_id,
                 (r.mission_id IS NOT NULL AND NOT EXISTS (SELECT 1 FROM tensor_dev_missions m WHERE m.id = r.mission_id)) AS mission_missing,
                 (SELECT m.status FROM tensor_dev_missions m WHERE m.id = r.mission_id) AS mission_status,
                 r.created_at
          FROM tensor_reports r
        ) TO STDOUT WITH CSV HEADER
        """.strip()

        term_csv = outdir / "terminal_missions.csv"
        rep_csv = outdir / "reports_facts.csv"

        proc = subprocess.run(
            ["psql", "-X", "-q", "-d", dsn, "-c", SQL_TERMINAL],
            capture_output=True, text=True, timeout=600,
        )
        if proc.returncode != 0:
            raise RuntimeError(f"psql failed: {proc.stderr.strip()[:800]}")
        term_csv.write_text(proc.stdout, encoding="utf-8")

        proc = subprocess.run(
            ["psql", "-X", "-q", "-d", dsn, "-c", SQL_REPORTS],
            capture_output=True, text=True, timeout=600,
        )
        if proc.returncode != 0:
            raise RuntimeError(f"psql failed: {proc.stderr.strip()[:800]}")
        rep_csv.write_text(proc.stdout, encoding="utf-8")

        data_mode = "database"

    # 计算 KPI
    result = compute_kpi(term_csv, rep_csv, now=now)
    result["data_mode"] = data_mode

    # 写 result.json
    (outdir / "result.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")

    # 写 SQL 原文（合成模式下为说明）
    if synthetic:
        sql_text = "-- Synthetic mode: no SQL executed\n"
        sql_text += f"-- Seed: {seed}\n"
        sql_text += f"-- Generated terminal missions: {result['denominator']}\n"
        sql_text += f"-- Generated traceable: {result['numerator']}\n"
    else:
        sql_text = "-- KPI-TRACE-100 SQL\n" + SQL_TERMINAL + "\n\n" + SQL_REPORTS + "\n"
    (outdir / "kpi_trace_100_queries.sql").write_text(sql_text, encoding="utf-8")

    print(json.dumps({k: result[k] for k in
                      ("kpi", "current", "unit", "target", "gap", "numerator", "denominator")},
                     ensure_ascii=False))
    print(f"data_mode={data_mode}")
    print(f"wrote {outdir / 'result.json'}")
    return result


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--outdir", default="artifacts/kpi_trace_100")
    ap.add_argument("--synthetic", action="store_true", help="Use synthetic data")
    ap.add_argument("--dsn", default=None)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--now", default=None)
    args = ap.parse_args()

    now = parse_ts(args.now) if args.now else datetime.now(timezone.utc)
    result = run_measure(
        Path(args.outdir),
        synthetic=args.synthetic,
        dsn=args.dsn,
        seed=args.seed,
        now=now,
    )
