#!/usr/bin/env python3
"""交付语义分类器·漂移语料harvest（p372w2-n06 / E2）。

口径（预注册于 ``quality/delivery_semantics/evidence/drift/preregistration.json``，
本文件是它的机器实现，改口径必须先改预注册）：

1. **总体**：``campaign_nodes`` 中 ``plan_id > 372``、``status='done'``、``mission_id`` 非空，
   且其 mission ``created_at`` 晚于 plan 372 的创建时刻（=「p372 波后」新单）。
2. **抽样**：分层确定性抽样。分层代理 = ``proxy_complete``（**只用于分层，绝不充当标签**）：
   ``checks`` 命中运行结果形状（``\\d+ (passed|failed|通过|失败)`` / ``PASS`` / ``FAIL`` /
   ``exit code`` / ``sha256``）且实现清单非空。排序键 = ``sha256("p372w2-n06:<mission_id>")``
   升序（种子写死在排序键里，无 RNG → 同输入必同样本）。
3. **规模**：每层 15 单，合计 30 单（验收下界 20 单）。

输出（全部內容派生的 sha256，零手抄）：

* ``drift/candidates.json`` —— 总体计数 + 每单最小字段 + 抽样键（审计抽样规则是否被遵守）；
* ``drift/corpus_raw.json`` —— 被抽中 30 单的**原始 mission result 载荷**（分类器直接消费，
  不做任何字段重建，避免「重构偏差」）。

用法::

    python3 tools/delivery_semantics_drift/harvest.py            # 生成两个工件
    python3 tools/delivery_semantics_drift/harvest.py --dry-run  # 只打印计数
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shlex
import subprocess
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

SEED_TAG = "p372w2-n06"
PLAN_THRESHOLD = 372
PER_STRATUM = 15
ROOT = Path(__file__).resolve().parents[2]
EVIDENCE = ROOT / "quality" / "delivery_semantics" / "evidence" / "drift"

#: 分层代理（与分类器的 MACHINE_CHECKABLE_PATTERNS 不同，仅用于分层）
_PROXY_RE = re.compile(
    r"(?:\d+\s*(?:passed|failed|通过|失败)|\bPASS(?:ED)?\b|\bFAIL(?:ED)?\b"
    r"|exit\s*(?:code|status)|\bsha256\b)",
    re.IGNORECASE,
)

CANDIDATE_SQL = """
SELECT coalesce(json_agg(row_to_json(t)), '[]'::json) FROM (
  SELECT n.id                    AS node_id,
         n.plan_id               AS plan_id,
         n.mission_id            AS mission_id,
         n.metadata->>'ext_id'   AS ext_id,
         n.status                AS node_status,
         m.status                AS mission_status,
         m.created_at            AS mission_created,
         m.finished_at           AS mission_finished,
         left(coalesce(m.result->>'checks', ''), 400)  AS checks_head,
         length(coalesce(m.result->>'checks', ''))     AS checks_len,
         coalesce(jsonb_array_length(
             CASE WHEN jsonb_typeof(m.result->'changed_files') = 'array'
                  THEN m.result->'changed_files' ELSE '[]'::jsonb END), 0) AS n_changed_files,
         coalesce(jsonb_array_length(
             CASE WHEN jsonb_typeof(m.result->'on_disk_changes') = 'array'
                  THEN m.result->'on_disk_changes' ELSE '[]'::jsonb END), 0) AS n_on_disk,
         (m.result ? 'headline')                       AS has_headline,
         coalesce(m.result->>'fail_stage', '')         AS fail_stage
  FROM campaign_nodes n
  JOIN tensor_dev_missions m ON m.id = n.mission_id
  WHERE n.plan_id > {plan}
    AND n.status = 'done'
    AND n.mission_id IS NOT NULL
    AND m.created_at > (SELECT created_at FROM campaign_plans WHERE id = {plan})
  ORDER BY n.id
) t;
"""

PAYLOAD_SQL = """
SELECT coalesce(json_agg(row_to_json(t)), '[]'::json) FROM (
  SELECT m.id AS mission_id, m.result AS result, m.status AS mission_status,
         m.created_at AS mission_created, n.metadata->>'ext_id' AS ext_id,
         n.plan_id AS plan_id, n.status AS node_status
  FROM campaign_nodes n
  JOIN tensor_dev_missions m ON m.id = n.mission_id
  WHERE m.id IN ({ids})
  ORDER BY m.id
) t;
"""


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def sample_key(mission_id: int) -> str:
    """确定性抽样键：种子写进哈希输入，无 RNG。"""
    return sha256_text(f"{SEED_TAG}:{int(mission_id)}")


def proxy_complete(rec: Dict[str, Any]) -> bool:
    """分层代理：运行结果形状 + 实现清单非空（不是标签，只用于分层）。"""
    checks = rec.get("checks_head") or ""
    impl = (rec.get("n_changed_files") or 0) + (rec.get("n_on_disk") or 0)
    return bool(_PROXY_RE.search(checks)) and impl > 0


def stratified_sample(candidates: Sequence[Dict[str, Any]],
                      per_stratum: int = PER_STRATUM) -> List[Dict[str, Any]]:
    """分层确定性抽样：每层按 sha256 排序键升序取前 N。"""
    pos = [c for c in candidates if c["proxy_complete"]]
    neg = [c for c in candidates if not c["proxy_complete"]]
    out: List[Dict[str, Any]] = []
    for stratum in (pos, neg):
        ranked = sorted(stratum, key=lambda c: c["sample_key"])
        out.extend(ranked[:per_stratum])
    return sorted(out, key=lambda c: c["mission_id"])


def run_sql(sql: str, timeout: int = 300) -> Any:
    """只读查询：TCP+密码优先，否则 postgres peer（与运维工具同法）。"""
    db = os.environ.get("TP_PG_DB", "tensorplatform")
    pwd = os.environ.get("TP_PG_PASSWORD", "")
    if pwd:
        proc = subprocess.run(
            ["psql", "-h", os.environ.get("TP_PG_HOST", "127.0.0.1"),
             "-U", os.environ.get("TP_PG_USER", "foundation"), "-d", db, "-tA", "-c", sql],
            capture_output=True, text=True, timeout=timeout,
            env=dict(os.environ, PGPASSWORD=pwd))
    else:
        flat = " ".join(sql.split())          # 单行化：避免 \n 被 psql 当元命令
        proc = subprocess.run(["su", "postgres", "-c",
                               f"psql -d {db} -tA -c {shlex.quote(flat)}"],
                              capture_output=True, text=True, timeout=timeout)
    if proc.returncode != 0:
        raise RuntimeError(f"psql failed: {proc.stderr[:400]}")
    return json.loads(proc.stdout.strip() or "[]")


def harvest(out_dir: Path = EVIDENCE, per_stratum: int = PER_STRATUM,
            dry_run: bool = False) -> Dict[str, Any]:
    sql1 = CANDIDATE_SQL.format(plan=PLAN_THRESHOLD)
    rows = run_sql(sql1)
    for r in rows:
        r["proxy_complete"] = proxy_complete(r)
        r["sample_key"] = sample_key(r["mission_id"])
    sample = stratified_sample(rows, per_stratum)

    summary = {
        "population": len(rows),
        "stratum_proxy_yes": sum(1 for r in rows if r["proxy_complete"]),
        "stratum_proxy_no": sum(1 for r in rows if not r["proxy_complete"]),
        "sampled": len(sample),
        "sql_sha256": sha256_text(sql1),
    }
    if dry_run:
        return {"summary": summary, "sample_ids": [s["mission_id"] for s in sample]}

    payload_rows = run_sql(PAYLOAD_SQL.format(
        ids=",".join(str(s["mission_id"]) for s in sample))) if sample else []
    payload_by_id = {int(p["mission_id"]): p for p in payload_rows}

    corpus_units: List[Dict[str, Any]] = []
    for s in sample:
        p = payload_by_id.get(int(s["mission_id"]), {})
        corpus_units.append({
            "mission_id": int(s["mission_id"]),
            "plan_id": s["plan_id"],
            "ext_id": s.get("ext_id") or "",
            "mission_status": p.get("mission_status") or s.get("mission_status"),
            "mission_created": str(p.get("mission_created") or s.get("mission_created")),
            "stratum": "proxy_yes" if s["proxy_complete"] else "proxy_no",
            "sample_key": s["sample_key"],
            "result": p.get("result") or {},
        })

    corpus_sha = sha256_text(json.dumps(corpus_units, ensure_ascii=False,
                                        sort_keys=True, separators=(",", ":")))
    # 人群清单：只留可复算抽样所需的最小字段（checks 正文只留 sha256 与前 80 字，
    # 避免把 1111 单正文塞进仓库；被抽中单的完整载荷在 corpus_raw.json）
    candidates = {
        "schema": "delivery-drift-candidates/1.0",
        "seed": SEED_TAG,
        "population_sql": sql1,
        "population_sql_sha256": sha256_text(sql1),
        "sampling_rule": {
            "population": "campaign_nodes.plan_id > 372 AND status='done' AND mission_id NOT NULL "
                          "AND mission.created_at > campaign_plans[372].created_at",
            "stratify_proxy": "PROXY_RE(checks) AND (changed_files + on_disk_changes) > 0",
            "rank_key": "sha256('p372w2-n06:<mission_id>') asc",
            "per_stratum": per_stratum,
        },
        "sample_summary": summary,
        "sampled_mission_ids": [u["mission_id"] for u in corpus_units],
        "candidates": [
            {"plan_id": r["plan_id"], "mission_id": r["mission_id"], "ext_id": r["ext_id"],
             "node_status": r["node_status"], "mission_status": r["mission_status"],
             "checks_len": r["checks_len"],
             "checks_sha256": sha256_text(r.get("checks_head") or ""),
             "checks_head": (r.get("checks_head") or "")[:80],
             "n_changed_files": r["n_changed_files"], "n_on_disk": r["n_on_disk"],
             "has_headline": r["has_headline"], "fail_stage": r["fail_stage"],
             "proxy_complete": r["proxy_complete"], "sample_key": r["sample_key"],
             "sampled": any(u["mission_id"] == r["mission_id"] for u in corpus_units)}
            for r in rows
        ],
    }
    corpus = {
        "schema": "delivery-drift-corpus/1.0",
        "seed": SEED_TAG,
        "population_sql_sha256": sha256_text(sql1),
        "sample_summary": summary,
        "corpus_sha256": corpus_sha,
        "units": corpus_units,
    }
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "candidates.json").write_text(
        json.dumps(candidates, ensure_ascii=False, indent=1, sort_keys=True) + "\n",
        encoding="utf-8")
    (out_dir / "corpus_raw.json").write_text(
        json.dumps(corpus, ensure_ascii=False, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    return corpus


def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="漂移语料 harvest")
    ap.add_argument("--out-dir", default=str(EVIDENCE))
    ap.add_argument("--per-stratum", type=int, default=PER_STRATUM)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)
    out = harvest(Path(args.out_dir), args.per_stratum, args.dry_run)
    if args.dry_run:
        print(json.dumps(out, ensure_ascii=False, indent=1))
    else:
        print(json.dumps({"summary": out["sample_summary"],
                          "corpus_sha256": out["corpus_sha256"]}, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())