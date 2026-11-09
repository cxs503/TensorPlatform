#!/usr/bin/env python3
"""导出审计输入快照（P151W1-N10 / ③）：终态 mission + 一个已收官 DAG 的节点。

只读 SQL，写 artifacts/ 快照；后续审计与 pytest 都吃快照（脱库可复现，E2 前提）。

快照字段（全部来自库里，不含手抄）：
  mission: id / repo / campaign / is_maturity / status / goal
  dag    : plan_id / ext_id / name / status / mission_id / goal

用法：
    python3 tools/judgeability/export_missions.py --limit 300 --dag-plan 22
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

REPO_ROOT = Path(__file__).resolve().parents[2]
OUT_DIR = REPO_ROOT / "artifacts" / "quality" / "judgeability"
PSQL = ["runuser", "-u", "postgres", "--", "psql", "-d", os.getenv("TP_DB", "tensorplatform"),
        "-t", "-A", "-c"]
TERMINAL = ("succeeded", "failed", "stopped", "revoked", "suspended", "orphan")

_CAMPAIGN_RE = re.compile(r"(?:plan_id\s*=\s*|【战役\s*DAG\s*节点\|)([A-Za-z0-9]+?)[-W|】]")


def q_json(sql: str) -> List[Dict[str, Any]]:
    out = subprocess.run(PSQL + [sql], capture_output=True, text=True)
    if out.returncode != 0:
        raise SystemExit(f"psql failed: {out.stderr[:400]}")
    raw = out.stdout.strip()
    if not raw or raw == "null":
        return []
    return json.loads(raw)


def campaign_of(goal: str, campaign_id: Any) -> str:
    """战役切片：优先库字段 campaign_id；否则从任务书抬头 ``【战役 DAG 节点|<EXT>|`` 解析，
    再退 ``plan_id=N``；都没有 → ``unattributed``。解析规则纯形状，无领域语义。"""
    if campaign_id not in (None, "", "-"):
        return str(campaign_id)
    m = _CAMPAIGN_RE.search(goal or "")
    return m.group(1) if m else "unattributed"


def is_maturity_of(goal: str) -> bool:
    return "成熟化" in (goal or "")


def export_missions(limit: int = 300) -> Dict[str, Any]:
    term = ",".join(f"'{s}'" for s in TERMINAL)
    sql = (
        "SELECT json_agg(row_to_json(t)) FROM ("
        "SELECT m.id, coalesce(m.repo,'unknown') AS repo, "
        "coalesce(m.campaign_id::text,'') AS campaign_id, m.status, "
        "coalesce(m.goal,'') AS goal "
        f"FROM tensor_dev_missions m WHERE m.status IN ({term}) "
        f"ORDER BY m.id DESC LIMIT {int(limit)}) t")
    rows = q_json(sql)
    recs = []
    for r in rows:
        goal = (r.get("goal") or "").replace("\r\n", "\n")
        recs.append({"id": int(r["id"]), "repo": r.get("repo") or "unknown",
                     "campaign": campaign_of(goal, r.get("campaign_id")),
                     "is_maturity": is_maturity_of(goal), "status": r.get("status"),
                     "goal": goal})
    return {"schema": "tp.quality.judgeability.mission_snapshot/v1",
            "source": {"table": "tensor_dev_missions", "statuses": list(TERMINAL),
                       "order": "id DESC", "limit": int(limit)},
            "n": len(recs), "missions": recs}


def export_dag(plan_id: int) -> Dict[str, Any]:
    sql = (
        "SELECT json_agg(row_to_json(t)) FROM ("
        "SELECT n.id, coalesce(n.metadata->>'ext_id','?') AS ext_id, n.name, n.status, "
        "coalesce(n.mission_id::text,'-') AS mission_id, "
        "coalesce(n.metadata->>'repo','') AS repo, coalesce(m.goal,'') AS goal "
        "FROM campaign_nodes n LEFT JOIN tensor_dev_missions m ON m.id=n.mission_id "
        f"WHERE n.plan_id={int(plan_id)} ORDER BY n.id) t")
    rows = q_json(sql) or []
    return {"schema": "tp.quality.judgeability.dag_snapshot/v1",
            "plan_id": int(plan_id), "n": len(rows),
            "nodes": [{"id": r["id"], "ext_id": r["ext_id"], "name": r["name"],
                       "status": r["status"], "mission_id": r["mission_id"],
                       "repo": r.get("repo") or "unknown",
                       "goal": (r.get("goal") or "").replace("\r\n", "\n")} for r in rows]}


def main(argv: Optional[List[str]] = None) -> int:
    p = argparse.ArgumentParser(description="导出判据可裁决性审计输入快照")
    p.add_argument("--limit", type=int, default=300)
    p.add_argument("--dag-plan", type=int, default=22)
    p.add_argument("--out-dir", default=str(OUT_DIR))
    a = p.parse_args(argv)
    out = Path(a.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    snap = export_missions(a.limit)
    (out / "missions_snapshot.json").write_text(
        json.dumps(snap, ensure_ascii=False, indent=1), encoding="utf-8")
    dag = export_dag(a.dag_plan)
    (out / f"dag_plan{a.dag_plan}.json").write_text(
        json.dumps(dag, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"missions={snap['n']} → {out/'missions_snapshot.json'}")
    print(f"dag nodes={dag['n']} (plan {a.dag_plan}) → {out/f'dag_plan{a.dag_plan}.json'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())