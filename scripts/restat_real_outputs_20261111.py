#!/usr/bin/env python3
"""AUDIT-RESTAT: 真实成果重新统计（owner 2026-11-11 第四项令：1 万任务≠1 万成果）。

零手抄纪律：本脚本所有数字均由 SQL 从 PostgreSQL(tensorplatform) 现查现算，
每条口径在 SQL_REGISTRY 中留原文，产物写入 runs/restat_20261111/，报告由产物渲染。

数据源
  - tensor_maturity_verdicts  审计波判定（scope 级 L0-L5 + 机器门 gate_level）
  - tensor_dev_missions       任务库（13,742 条，含 status/result.changed_files/pr_gate）
  - tensor_reports            报告库
  - knowledge_base / kb_samples / knowledge_rules / negative_knowledge_ledger  知识资产

L0-L5 六类口径（与审计波 gate_note 的判据键一致，见 docs/RESTAT_L0_L5_20261111.md §1）
  L0 文档成果      : 仅文档/图表/清单，无可执行实现
  L1 数学验证成果  : 仅测试/验证脚本（test_/verify_/audit_），无独立功能实现
  L2 独立功能成果  : 有实现文件 + 工程入口 + 可运行工件（PR 合并）
  L3 集成成果      : L2 + 被集成面调用（跨仓 co_repos 或 api/backend/cli 集成面）
  L4 基准验证成果  : L2/L3 + 与外部基准/解析解/文献对标且留 run 工件
  L5 创造性成果    : 无先例的新方法（不可由文件信号机器判定，只由人工/审计判定给出）

用法：python3 scripts/restat_real_outputs_20261111.py [--out runs/restat_20261111]
退出码：0 成功
"""
from __future__ import annotations

import argparse
import csv
import io
import json
import os
import re
import subprocess
from collections import Counter, defaultdict
from datetime import datetime, timezone

DB = os.environ.get("TP_PG_DB", "tensorplatform")
PSQL = ["psql", "-U", "postgres", "-d", DB, "--csv", "-t", "-q"]

CATEGORY = {
    0: "L0 文档成果",
    1: "L1 数学验证成果",
    2: "L2 独立功能成果",
    3: "L3 集成成果",
    4: "L4 基准验证成果",
    5: "L5 创造性成果",
}

# ---------------------------------------------------------------- SQL registry
SQL_REGISTRY = {
    "missions": """
        SELECT m.id, m.repo, m.status, m.origin, m.campaign_id,
               coalesce(array_to_string(m.co_repos, ';'), '') AS co_repos,
               to_char(m.created_at, 'YYYY-MM-DD') AS day,
               coalesce(m.result->'pr_gate'->>'state', '') AS pr_state,
               coalesce(m.result->>'accepted', '') AS accepted,
               coalesce(jsonb_array_length(m.result->'changed_files'), 0) AS n_files,
               replace(coalesce(m.result->>'changed_files', '[]'), chr(10), ' ') AS files_json,
               (m.result->'capability_delta' IS NOT NULL
                AND m.result->'capability_delta'::text <> 'null') AS has_cap_delta,
               coalesce(m.result->'capability_delta'->>'category', '') AS cap_category,
               replace(coalesce(m.goal, ''), chr(10), ' ') AS goal
          FROM tensor_dev_missions m
    """,
    "verdicts": """
        SELECT v.id, v.repo, v.scope, v.level, coalesce(v.gate_level, -1) AS gate_level,
               coalesce(v.gate_state, '') AS gate_state, coalesce(v.verdict_by, '') AS verdict_by,
               coalesce(v.evidence->>'gap', '') AS gap,
               coalesce(v.evidence->>'executed', '') AS executed,
               coalesce((regexp_match(v.scope, '\\(m([0-9]+)\\)'))[1], '') AS scope_mid,
               coalesce(v.mission_id, 0) AS mission_id,
               v.evidence::text AS evidence
          FROM tensor_maturity_verdicts v
    """,
    "reports": "SELECT count(*) FROM tensor_reports",
    "reports_with_mission": "SELECT count(*) FROM tensor_reports WHERE mission_id IS NOT NULL",
    "reports_distinct_mission": "SELECT count(DISTINCT mission_id) FROM tensor_reports WHERE mission_id IS NOT NULL",
    "knowledge": """
        SELECT 'knowledge_base' AS tbl, count(*) AS n FROM knowledge_base
        UNION ALL SELECT 'kb_samples', count(*) FROM kb_samples
        UNION ALL SELECT 'knowledge_rules', count(*) FROM knowledge_rules
        UNION ALL SELECT 'negative_knowledge_ledger', count(*) FROM negative_knowledge_ledger
        UNION ALL SELECT 'knowledge_injections', count(*) FROM knowledge_injections
    """,
    "status_split": "SELECT status, count(*) FROM tensor_dev_missions GROUP BY 1 ORDER BY 2 DESC",
    "pr_coverage_by_day": """
        SELECT created_at::date::text AS d, count(*) AS succ,
               count(*) FILTER (WHERE result ? 'pr_gate') AS with_pr,
               count(*) FILTER (WHERE result->'pr_gate'->>'state' = 'merged') AS merged
          FROM tensor_dev_missions WHERE status = 'succeeded' GROUP BY 1 ORDER BY 1
    """,
    "tutorial_reports": """
        SELECT count(*) FROM tensor_reports
         WHERE title ILIKE '%教程%' OR title ILIKE '%tutorial%' OR tags::text ILIKE '%tutorial%'
    """,
    "gate_artifact_gap": """
        SELECT count(*) FILTER (WHERE gate_note->>'why' LIKE '%工件缺失%') AS missing_artifact,
               count(*) AS total
          FROM tensor_maturity_verdicts
    """,
}

CODE_EXT = {".py", ".c", ".cc", ".cpp", ".h", ".hpp", ".f", ".f90", ".f95", ".cu",
            ".rs", ".ts", ".js", ".vue", ".go", ".jl", ".m", ".java", ".sql"}
DOC_EXT = {".md", ".rst", ".txt", ".tex", ".pdf"}
ASSET_EXT = {".png", ".jpg", ".jpeg", ".svg", ".json", ".csv", ".yaml", ".yml", ".html"}
BENCH_WORDS = ("benchmark", "基准", "对标", "解析解", "analytic", "reference value",
               "文献", "vs 实验", "实验值", "误差<", "误差 <", "validation against")
INTEG_MARKERS = ("api/", "backend/", "tools/", "cli", "routes", "server.", "__init__.py",
                 "main.py", "harness", "daemon", "worker")


def psql(sql: str) -> list[list[str]]:
    """执行 SQL，返回 CSV 行（csv 模块负责引号/转义）。"""
    out = subprocess.run(PSQL + ["-c", sql], capture_output=True, text=True, timeout=600)
    if out.returncode != 0:
        raise RuntimeError(f"psql failed: {out.stderr[:400]}")
    return [r for r in csv.reader(io.StringIO(out.stdout)) if r]


def q1(sql: str) -> str:
    rows = psql(sql)
    return rows[0][0] if rows else ""


def strip_prefix(path: str) -> str:
    """把 /root/tp-agent/work/m1234/a/b.py 归一为 a/b.py。"""
    m = re.search(r"/work/m\d+/(.+)$", path)
    return m.group(1) if m else path


def classify(files, pr_state: str, co_repos: str, goal: str):
    """代理分档（Tier-B）。

    返回 (level_strict, level_loose, reason, n_run_artifacts)。
      level_strict : L2+ 必须有 PR 合并证据（owner 口径：未入库不算成果）
      level_loose   : 只要有实现文件即计入 L2+（用于量化"宽松口径"膨胀）
      level = -1    : 无工件证据，不可判
    """
    paths = [strip_prefix(p.strip()) for p in files if p.strip()]
    if not paths:
        return -1, -1, "no_changed_files", 0
    impl, tests, runs = [], [], 0
    for p in paths:
        low = p.lower()
        ext = os.path.splitext(low)[1]
        base = os.path.basename(low)
        if ext in DOC_EXT:
            pass
        elif ext in ASSET_EXT:
            if low.startswith(("runs/", "artifacts/", "figures/", "fig/")) or base.startswith("fig"):
                runs += 1
        elif ext in CODE_EXT:
            if base.startswith("test_") or low.startswith("tests/") or base.endswith("_test.py") \
               or re.search(r"(verify|validate|audit|check|probe)", base):
                tests.append(low)
            else:
                impl.append(low)
    if not impl and not tests:
        return 0, 0, "docs_only", runs
    if not impl:
        return 1, 1, "tests_or_verify_only", runs
    integrated = (len([r for r in co_repos.split(";") if r.strip()]) >= 2) or \
                 any(mk in p for p in impl for mk in INTEG_MARKERS)
    bench = any(w in goal.lower() for w in BENCH_WORDS) and runs > 0
    merged = pr_state == "merged"

    if integrated and bench:
        loose = 4
    elif integrated:
        loose = 3
    elif bench:
        loose = 4
    else:
        loose = 2
    if not merged:
        # 严格口径：实现存在但无合并证据 -> 只能算 L1（代码级工件，未入库）
        return 1, loose, ("impl_without_merge_evidence+" + str(loose)), runs
    return loose, loose, ("merged_" + ("integ" if integrated else "impl") +
                          ("+bench" if bench else "")), runs


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="runs/restat_20261111")
    args = ap.parse_args()
    outdir = args.out
    os.makedirs(outdir, exist_ok=True)

    missions = psql(SQL_REGISTRY["missions"])
    verdicts = psql(SQL_REGISTRY["verdicts"])
    status_split = psql(SQL_REGISTRY["status_split"])
    knowledge = psql(SQL_REGISTRY["knowledge"])
    n_reports = int(q1(SQL_REGISTRY["reports"]))
    n_reports_mid = int(q1(SQL_REGISTRY["reports_with_mission"]))
    n_reports_dm = int(q1(SQL_REGISTRY["reports_distinct_mission"]))

    # ---------------- Tier-B: 全库任务代理分档
    rows = []
    for r in missions:
        (mid, repo, status, origin, campaign, co_repos, day, pr_state,
         accepted, n_files, files_json, has_cap_delta, cap_category, goal) = r[:14]
        try:
            files = json.loads(files_json) if files_json.strip().startswith("[") else []
        except Exception:
            files = []
        lvl, lvl_loose, why, run_art = classify(files, pr_state, co_repos, goal)
        rows.append(dict(
            mission_id=int(mid), repo=repo, status=status, origin=origin,
            campaign=("" if campaign in ("", None) else int(float(campaign))),
            co_repos=co_repos, day=day, pr_state=pr_state, accepted=accepted,
            n_files=int(n_files or 0), level=lvl, level_loose=lvl_loose, why=why,
            has_cap_delta=(has_cap_delta == "t"), cap_category=cap_category,
            run_artifacts=int(run_art),
        ))

    by_status = Counter(r["status"] for r in rows)
    succ = [r for r in rows if r["status"] == "succeeded"]
    lvl_count = Counter(r["level"] for r in succ)
    lvl_count_loose = Counter(r["level_loose"] for r in succ)
    total_succ = len(succ)

    # 分月趋势（成功单的档位分布）
    trend = defaultdict(Counter)
    for r in succ:
        trend[r["day"]][r["level"]] += 1
    trend_all = defaultdict(Counter)   # 全库（含失败）按日提交量
    for r in rows:
        trend_all[r["day"]][r["level"]] += 1
    # 分仓分布
    per_repo = defaultdict(Counter)
    for r in succ:
        per_repo[r["repo"]][r["level"]] += 1

    # ---------------- Tier-A: 184 条审计判定（scope 级，claim vs 机器门实测）
    claim = Counter(int(v[3]) for v in verdicts)
    gate = Counter(int(v[4]) for v in verdicts)
    gate_state = Counter(re.sub(r"\(.*", "", v[5]) for v in verdicts)
    crosstab = Counter((int(v[3]), int(v[4])) for v in verdicts)
    per_repo_verdict = defaultdict(Counter)
    for v in verdicts:
        per_repo_verdict[v[1]][int(v[3])] += 1
    l0_l1_items = [dict(
        verdict_id=int(v[0]), repo=v[1], scope=v[2], claimed_level=int(v[3]),
        gate_level=int(v[4]), gate_state=v[5], verdict_by=v[6], gap=v[7][:300],
        executed=v[8], scope_mid=(int(v[9]) if v[9] else None),
        mission_id=int(v[10]), evidence_excerpt=v[11][:180],
    ) for v in verdicts if int(v[3]) <= 1]

    # 代理分档 vs 审计实测（验证集：18 条 scope 带原任务号）
    val_pairs = []
    by_mid = {r["mission_id"]: r for r in rows}
    for v in verdicts:
        if not v[9]:
            continue
        smid = int(v[9])
        vr = by_mid.get(smid)
        if vr is None:
            val_pairs.append(dict(scope_mid=smid, scope=v[2], claimed=int(v[3]),
                                  gate=int(v[4]), proxy=None))
            continue
        val_pairs.append(dict(scope_mid=smid, repo=vr["repo"], scope=v[2],
                              claimed=int(v[3]), gate=int(v[4]), proxy=vr["level"],
                              why=vr["why"]))
    val_known = [p for p in val_pairs if p.get("proxy") is not None]
    val_agree = [p for p in val_known if p["proxy"] == p["gate"]]
    val_within1 = [p for p in val_known if abs(p["proxy"] - p["gate"]) <= 1]

    # ---------------- ③ 成果膨胀量化
    pr_merged = sum(1 for r in rows if r["pr_state"] == "merged")
    pr_left = Counter(r["pr_state"] for r in rows if r["status"] == "succeeded")
    cap_declared_missions = sum(1 for r in rows if r["has_cap_delta"])
    cap_declared_succ = sum(1 for r in succ if r["has_cap_delta"])
    cap_categories = Counter(r["cap_category"] for r in rows if r["has_cap_delta"])
    verdict_l2plus = sum(1 for v in verdicts if int(v[4]) >= 2)
    proxy_l2plus = sum(1 for r in succ if r["level"] >= 2)
    verdict_executed = sum(1 for v in verdicts if v[8] == "true")
    verdict_with_run = sum(1 for v in verdicts if '"run"' in v[11] or '"artifacts"' in v[11])
    succ_with_run = sum(1 for r in succ if r["run_artifacts"] > 0)
    gap_rows = psql(SQL_REGISTRY["gate_artifact_gap"])
    gate_missing_artifact, gate_total = int(gap_rows[0][0]), int(gap_rows[0][1])
    tutorial_reports = int(q1(SQL_REGISTRY["tutorial_reports"]))
    pr_cov = {r[0]: dict(succ=int(r[1]), with_pr=int(r[2]), merged=int(r[3]))
              for r in psql(SQL_REGISTRY["pr_coverage_by_day"])}

    inflation = {
        "declared_capability_delta_records": cap_declared_missions,
        "declared_capability_delta_on_succeeded": cap_declared_succ,
        "declared_capability_categories": dict(cap_categories),
        "declared_implementation_missions_proxy": proxy_l2plus,
        "verified_functions_audit_gate_L2plus": verdict_l2plus,
        "verified_audit_gate_L1plus": int(gate.get(1, 0)),
        "ratio_declared_over_verified": (round(cap_declared_missions / verdict_l2plus, 2)
                                         if verdict_l2plus else None),
        "proxy_L2plus_succeeded_missions": proxy_l2plus,
        "succ_with_run_artifacts": succ_with_run,
        "gate_missing_artifact_scopes": gate_missing_artifact,
        "gate_total_scopes": gate_total,
        "ratio_reports_over_succ_with_run_artifacts": (round(n_reports / succ_with_run, 2)
                                                      if succ_with_run else None),
        "reports_total": n_reports,
        "reports_linked_to_mission": n_reports_mid,
        "reports_distinct_missions": n_reports_dm,
        "verdicts_with_executed_true": verdict_executed,
        "verdicts_with_run_or_artifact_pointer": verdict_with_run,
        "ratio_reports_over_runnable": (round(n_reports / verdict_executed, 2)
                                        if verdict_executed else None),
        "pr_merged_missions": pr_merged,
        "pr_merged_over_gate_L2plus": (round(pr_merged / verdict_l2plus, 2)
                                       if verdict_l2plus else None),
        "pr_state_on_succeeded": dict(pr_left),
    }

    # ---------------- ④ 知识资产（单列，不计入功能账）
    knowledge_rows = [dict(table=t, rows=int(n)) for t, n in knowledge]
    knowledge_total = sum(k["rows"] for k in knowledge_rows)
    tk_missions = sum(1 for r in rows if r["repo"] == "TensorKnowledge")
    tk_verdicts = sum(1 for v in verdicts if v[1] == "TensorKnowledge")

    # ---------------- 写产物
    with open(f"{outdir}/missions_classified.csv", "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)

    l0l1_payload = dict(
        generated_at=datetime.now(timezone.utc).isoformat(),
        rule="曾标 succeeded/claim 但审计实测 gate_level<=1（只列名单，不改任务状态）",
        count=len(l0_l1_items),
        items=l0_l1_items,
    )
    with open(f"{outdir}/pending_acceptance_L0L1.json", "w") as fh:
        json.dump(l0l1_payload, fh, ensure_ascii=False, indent=1)

    summary = dict(
        generated_at=datetime.now(timezone.utc).isoformat(),
        db=DB,
        missions_total=len(rows),
        status_split=dict((s, int(n)) for s, n in status_split),
        succeeded_total=total_succ,
        tierB_level_counts={str(k): v for k, v in sorted(lvl_count.items())},
        tierB_level_counts_loose={str(k): v for k, v in sorted(lvl_count_loose.items())},
        tierB_why_counts=dict(Counter(r["why"] for r in succ)),
        tierA_claimed_level_counts={str(k): v for k, v in sorted(claim.items())},
        tierA_gate_level_counts={str(k): v for k, v in sorted(gate.items())},
        tierA_gate_state=dict(gate_state),
        tierA_crosstab=[dict(claimed=k[0], gate=k[1], n=v) for k, v in sorted(crosstab.items())],
        tierA_by_repo={k: dict((str(a), b) for a, b in sorted(c.items()))
                       for k, c in sorted(per_repo_verdict.items())},
        trend_by_day={k: dict((str(a), b) for a, b in sorted(c.items()))
                      for k, c in sorted(trend.items())},
        trend_by_day_all={k: sum(c.values()) for k, c in sorted(trend_all.items())},
        by_repo_succeeded={k: dict((str(a), b) for a, b in sorted(c.items()))
                           for k, c in sorted(per_repo.items())},
        inflation=inflation,
        pr_coverage_by_day=pr_cov,
        knowledge=dict(tables=knowledge_rows, total_rows=knowledge_total,
                       tk_missions=tk_missions, tk_verdict_scopes=tk_verdicts,
                       tutorial_reports=tutorial_reports),
        proxy_validation=dict(pairs=len(val_pairs), matched=int(len(val_known)),
                              exact_agree=len(val_agree), within1=len(val_within1),
                              detail=val_pairs),
    )
    with open(f"{outdir}/restat_summary.json", "w") as fh:
        json.dump(summary, fh, ensure_ascii=False, indent=1)

    with open(f"{outdir}/sql_registry.sql", "w") as fh:
        fh.write("-- AUDIT-RESTAT SQL registry (口径原文，可直接 psql 复跑)\n")
        for name, sql in SQL_REGISTRY.items():
            fh.write(f"\n-- [{name}]\n{sql.strip()};\n")

    print(json.dumps(dict(total=len(rows), succeeded=total_succ,
                          tierB={str(k): v for k, v in sorted(lvl_count.items())},
                          verdict_claim={str(k): v for k, v in sorted(claim.items())},
                          verdict_gate={str(k): v for k, v in sorted(gate.items())},
                          pending_l0l1=len(l0_l1_items), inflation=inflation,
                          validation=dict(matched=len(val_known), agree=len(val_agree),
                                          within1=len(val_within1))),
                     ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())