#!/usr/bin/env python3
"""SPEC 落实军（IN19 波）验收门的**证据采集器**。

职责：把验收门要审的证据从"上游各 mission 的分支工作树 + 平台库只读事实"里
**原样**搬进本仓的 evidence bundle，并为每份证据记下来源（仓/分支/commit/路径）
与 sha256。之后验收门（`spec_impl_acceptance_gate.py`）只读 bundle，成为纯函数：
同一 bundle → 同一判定，可离线复算、可被 pytest 篡改负控。

采集两类证据：
1. `upstream/`：上游节点交付的证据文件（stats/快照/账本/矩阵），逐文件 sha256；
2. `db_facts.json`：对平台库（tensor_dev_missions / outcome_evaluations）执行
   **只读** SELECT 的事实快照，连 SQL 文本一起存（便于别人拿同一条 SQL 复核）。

用法：
    python3 tools/ops/wave_evidence_collect.py \\
        --work-root /root/tp-agent/work \\
        --out-root artifacts/in19_g20/evidence \\
        --psql "psql -h /var/run/postgresql -U postgres -d tensorplatform"

纪律：本脚本**只读**平台库，不做任何写入/迁移；不做网络访问。
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Sequence, Tuple

REPO_ROOT = Path(__file__).resolve().parents[2]

# ── 上游节点 → 证据文件（相对各自工作树）────────────────────────────────────
# node: 战役 DAG 节点标签；checks: 该证据服务于验收门哪几项。
UPSTREAM_EVIDENCE: Tuple[Dict[str, Any], ...] = (
    {
        "mission": 5018,
        "node": "IN19-Q1",
        "worktree": "m5018",
        "branch": "agent/m5018",
        "checks": ["E1", "E5"],
        "paths": [
            "artifacts/m5018_outcome_evaluations/stats_snapshot.json",
            "artifacts/m5018_outcome_evaluations/stats_pinned.sql",
            "artifacts/m5018_outcome_evaluations/recompute_psql.txt",
            "artifacts/m5018_outcome_evaluations/backfill_report.json",
            "artifacts/m5018_outcome_evaluations/api_smoke_pg.json",
        ],
    },
    {
        "mission": 5019,
        "node": "IN19-Q2",
        "worktree": "m5019",
        "branch": "agent/m5019",
        "checks": ["E3"],
        "paths": [
            "artifacts/review_budget_ledger.jsonl",
            "artifacts/m5019/review_budget_backfill.json",
            "docs/governance/review_budget_weekly_2026-W44.md",
            "docs/governance/review_budget_weekly_2026-W45.md",
        ],
    },
    {
        "mission": 5023,
        "node": "IN19-Q8",
        "worktree": "m5023",
        "branch": "agent/m5023",
        "checks": ["E5"],
        "paths": [
            "artifacts/m5023/center_payloads_snapshot.json",
            "artifacts/m5023/center_sql_crosscheck.json",
        ],
    },
    {
        "mission": 5024,
        "node": "IN19-Q9",
        "worktree": "m5024",
        "branch": "agent/m5024",
        "checks": ["E2"],
        "paths": ["artifacts/m5024/credit_tier_criteria.json"],
    },
    {
        "mission": 5025,
        "node": "IN19-Q3",
        "worktree": "m5025",
        "branch": "agent/m5025",
        "checks": ["E1", "E4"],
        "paths": [
            "artifacts/m5025/outcome_review_gate_audit.json",
            "artifacts/m5025/review_tickets_snapshot.json",
        ],
    },
    {
        "mission": 5026,
        "node": "IN19-Q6",
        "worktree": "m5026",
        "branch": "agent/m5026",
        "checks": ["E2"],
        "paths": [
            "artifacts/mission_gradient/dispatch_matrix.json",
            "artifacts/mission_gradient/q1_selfcheck.json",
            "artifacts/mission_gradient/pg_verify.json",
        ],
    },
)

# ── 只读事实快照（SQL 与结果一起落盘，便于独立复核）────────────────────────
DB_FACTS: Tuple[Dict[str, str], ...] = (
    {
        "key": "wave_missions",
        "note": "本波 9 个节点对应的平台任务（状态/完工时间）",
        "sql": (
            "SELECT id, status, repo, finished_at::text AS finished_at "
            "FROM tensor_dev_missions WHERE id BETWEEN 5018 AND 5026 ORDER BY id"
        ),
    },
    {
        "key": "outcome_evaluations",
        "note": "评价流全部记录（精简列；免费存证体积，逐行一条）",
        "sql": (
            "SELECT evaluation_id::text AS evaluation_id, mission_id, contributor, "
            "coalesce(grade,'') AS grade, coalesce(e_level,'') AS e_level, status, "
            "coalesce(reason_source,'') AS reason_source, "
            "coalesce(reviewer_kind,'') AS reviewer_kind, "
            "CASE WHEN evidence_refs IS NULL OR btrim(evidence_refs) IN ('','[]') THEN 0 "
            "ELSE json_array_length(evidence_refs::json) END::int AS evidence_refs_n, "
            "created_at::text AS created_at, window_finished_at::text AS window_finished_at "
            "FROM outcome_evaluations ORDER BY mission_id"
        ),
    },
    {
        "key": "outcome_evaluations_detail",
        "note": "非 confirmed 记录的三类审查字段（pending/candidate/rejected 才需要）",
        "sql": (
            "SELECT mission_id, status, coalesce(grade,'') AS grade, "
            "coalesce(baseline_or_scope,'') AS baseline_or_scope, "
            "coalesce(reviewer,'') AS reviewer, coalesce(reviewer_kind,'') AS reviewer_kind, "
            "CASE WHEN evidence_refs IS NULL OR btrim(evidence_refs) IN ('','[]') THEN 0 "
            "ELSE json_array_length(evidence_refs::json) END::int AS evidence_refs_n "
            "FROM outcome_evaluations WHERE status <> 'confirmed' ORDER BY mission_id"
        ),
    },
    {
        "key": "mission_status_counts",
        "note": "任务状态总账（口径背景）",
        "sql": (
            "SELECT status, count(*) AS n FROM tensor_dev_missions "
            "GROUP BY status ORDER BY n DESC"
        ),
    },
)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def git(args: Sequence[str], cwd: Path) -> str:
    out = subprocess.run(
        ["git", *args], cwd=str(cwd), capture_output=True, text=True, check=True
    )
    return out.stdout.strip()


def psql_rows(psql_cmd: Sequence[str], sql: str) -> List[Dict[str, str]]:
    """以 COPY ... TO STDOUT WITH CSV HEADER 取结构化结果（只读）。"""
    wrapped = f"COPY ({sql}) TO STDOUT WITH CSV HEADER"
    proc = subprocess.run(
        [*psql_cmd, "-v", "ON_ERROR_STOP=1", "-c", wrapped],
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"psql 失败: {proc.stderr.strip()[:400]}")
    reader = csv.DictReader(io.StringIO(proc.stdout))
    return [dict(row) for row in reader]


def collect_upstream(work_root: Path, out_root: Path) -> List[Dict[str, Any]]:
    records: List[Dict[str, Any]] = []
    for spec in UPSTREAM_EVIDENCE:
        worktree = work_root / spec["worktree"]
        if not worktree.is_dir():
            records.append(
                {
                    "mission": spec["mission"],
                    "node": spec["node"],
                    "status": "missing_worktree",
                    "worktree": str(worktree),
                }
            )
            continue
        commit = git(["rev-parse", "HEAD"], worktree)
        branch = git(["rev-parse", "--abbrev-ref", "HEAD"], worktree)
        files: List[Dict[str, Any]] = []
        for rel in spec["paths"]:
            src = worktree / rel
            if not src.is_file():
                files.append({"src": rel, "status": "absent"})
                continue
            dest = out_root / "upstream" / f"m{spec['mission']}" / Path(rel).name
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(src.read_bytes())
            files.append(
                {
                    "src": rel,
                    "dest": str(dest.relative_to(out_root.parent.parent)),
                    "sha256": sha256_file(dest),
                    "bytes": dest.stat().st_size,
                    "status": "copied",
                }
            )
        records.append(
            {
                "mission": spec["mission"],
                "node": spec["node"],
                "branch": spec["branch"],
                "head_branch": branch,
                "commit": commit,
                "checks": spec["checks"],
                "files": files,
            }
        )
    return records


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="IN19 验收门证据采集（只读）")
    ap.add_argument("--work-root", default="/root/tp-agent/work", help="上游工作树根目录")
    ap.add_argument(
        "--out-root", default=str(REPO_ROOT / "artifacts/in19_g20/evidence"), help="证据 bundle 目录"
    )
    ap.add_argument(
        "--psql",
        default="psql -h /var/run/postgresql -U postgres -d tensorplatform",
        help="psql 命令前缀（只读账号；默认走本地 socket）",
    )
    ap.add_argument("--skip-db", action="store_true", help="跳过库事实采集（离线环境）")
    args = ap.parse_args(argv)

    out_root = Path(args.out_root)
    out_root.mkdir(parents=True, exist_ok=True)
    psql_cmd = args.psql.split()

    upstream = collect_upstream(Path(args.work_root), out_root)

    db_block: Dict[str, Any] = {"collected_at": None, "psql": args.psql, "facts": {}}
    if not args.skip_db:
        facts: Dict[str, Any] = {}
        for spec in DB_FACTS:
            rows = psql_rows(psql_cmd, spec["sql"])
            facts[spec["key"]] = {
                "note": spec["note"],
                "sql": spec["sql"],
                "n_rows": len(rows),
                "rows": rows,
            }
        db_block = {
            "collected_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "psql": args.psql,
            "read_only": True,
            "facts": facts,
        }
    (out_root / "db_facts.json").write_text(
        json.dumps(db_block, ensure_ascii=False, indent=1) + "\n", encoding="utf-8"
    )

    provenance = {
        "schema": "in19_g20.evidence_bundle/1",
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "collector": "tools/ops/wave_evidence_collect.py",
        "repo": "TensorPlatform",
        "gate_mission": 5176,
        "upstream": upstream,
        "db_facts": "db_facts.json",
        "notes": [
            "全部上游文件按字节原样复制，sha256 见 files[].sha256。",
            "平台库只读 SELECT；SQL 文本随 db_facts.json 一并保存，可独立重放。",
            "证据 bundle 是验收门的唯一输入：判定可离线复算。",
        ],
    }
    (out_root / "provenance.json").write_text(
        json.dumps(provenance, ensure_ascii=False, indent=1) + "\n", encoding="utf-8"
    )

    copied = sum(
        1
        for rec in upstream
        for f in rec.get("files", [])
        if f.get("status") == "copied"
    )
    absent = sum(
        1
        for rec in upstream
        for f in rec.get("files", [])
        if f.get("status") == "absent"
    )
    print(
        json.dumps(
            {
                "out_root": str(out_root),
                "upstream_nodes": len(upstream),
                "files_copied": copied,
                "files_absent": absent,
                "db_facts": sorted(db_block.get("facts", {})),
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())