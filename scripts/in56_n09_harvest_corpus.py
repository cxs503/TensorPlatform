"""IN56-N09 底座语料收割（一次性，产物冻结到 artifacts/IN56_N09/base_corpus.json）。

从平台库（tensor_dev_missions ⋈ tensor_reports）取 **40 条真实历史 mission** 的提交文本，
按预注册的**分层抽样**（每层 20 条，层在判级前定死）：

- ``report`` 层：有实质报告的提交（报告 markdown 400–6000 字）——「写得完整」的底座；
- ``summary`` 层：未交报告的提交（只有 result 里的 headline/checks 文本，≥200 字）
  ——「只交摘要」的底座。

为什么分层：判级器的 S2 判据是「基线数字片段 ≥2 段」，真实完整报告几乎人人满足（实测
40/40 底座落在 S2 → 天花板），只在 report 层上测「伪数字能否买等级」等于问一个它不可能
answer 的问题。两个层合起来才覆盖到 S1 地板区（见 docs/IN56-N09 的设计说明）。

取法是确定性的（``md5(id)`` 排序），不随机，重跑同一语料。

脱敏（只脱环境指纹，不动证据结构）：绝对路径 /root/** → ``<abs_path>``；URL/邮箱 →
``<url>``/``<email>``；≥12 位十六进制串 → ``<hash>``。数字、单位、基线词、命令词、
工件相对路径一律保留 —— 那些正是评分器读的东西。

用法: python3 scripts/in56_n09_harvest_corpus.py --dsn postgresql://... [--per-stratum 20]
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

_ABS = re.compile(r"/(?:root|home|tmp|opt|var|etc|usr)/[^\s`'\"()\[\]，。；、]*")
_URL = re.compile(r"https?://[^\s`'\")\]，。；]+")
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
_HEX = re.compile(r"\b[0-9a-f]{12,}\b")

MIN_MD, MAX_MD = 400, 6000
MIN_SUMMARY = 200


def sanitize(text: str) -> str:
    out = _URL.sub("<url>", text or "")
    out = _EMAIL.sub("<email>", out)
    out = _ABS.sub("<abs_path>", out)
    out = _HEX.sub("<hash>", out)
    return out


def _sanitize_obj(obj):
    if isinstance(obj, str):
        return sanitize(obj)
    if isinstance(obj, dict):
        return {k: _sanitize_obj(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_sanitize_obj(v) for v in obj]
    return obj


def _psql_json(dsn: str, sql: str):
    res = subprocess.run(["psql", dsn, "-Atc", sql], capture_output=True, text=True, timeout=300)
    if res.returncode != 0:
        raise RuntimeError(res.stderr[:2000])
    body = res.stdout.strip()
    return json.loads(body) if body else None


def _flatten_result(result):
    """把线上 result 形状摊平到 {summary, checks} 纯文本（判级器读的就是这两项）。"""
    if isinstance(result, str):
        try:
            result = json.loads(result)
        except Exception:
            return {"summary": result}
    if not isinstance(result, dict):
        return {}
    ev = result.get("evidence") if isinstance(result.get("evidence"), dict) else {}

    def _pick(container, key):
        v = container.get(key)
        if isinstance(v, dict):
            parts = [str(v[k]) for k in ("headline", "summary") if v.get(k)]
            if v.get("checks"):
                parts.append("checks: " + (json.dumps(v["checks"], ensure_ascii=False)
                                           if not isinstance(v["checks"], str) else v["checks"]))
            return "\n".join(parts) or json.dumps(v, ensure_ascii=False)
        if isinstance(v, (list, tuple)):
            return "\n".join(str(x) for x in v)
        return "" if v is None else str(v)

    out = {}
    summary = _pick(ev, "summary") or _pick(result, "summary")
    checks = _pick(ev, "checks") or _pick(result, "checks")
    if summary:
        out["summary"] = summary
    if checks:
        out["checks"] = checks
    return out


def harvest_layer(dsn: str, *, layer: str, n: int) -> list[dict]:
    if layer == "report":
        where = (f"m.status = 'succeeded' AND length(coalesce(r.markdown,'')) "
                 f"BETWEEN {MIN_MD} AND {MAX_MD} AND r.id IS NOT NULL")
        join = ("JOIN LATERAL (SELECT r2.id, r2.markdown FROM tensor_reports r2 "
                "WHERE r2.mission_id = m.id ORDER BY length(coalesce(r2.markdown,'')) DESC "
                "LIMIT 1) r ON true")
    else:
        where = (f"m.status = 'succeeded' AND (r.id IS NULL OR length(coalesce(r.markdown,'')) < 200)"
                 f" AND length(coalesce(m.result->'evidence'->>'summary','')) >= {MIN_SUMMARY}")
        join = ("LEFT JOIN LATERAL (SELECT r2.id, r2.markdown FROM tensor_reports r2 "
                "WHERE r2.mission_id = m.id ORDER BY length(coalesce(r2.markdown,'')) DESC "
                "LIMIT 1) r ON true")
    sql = f"""
    SELECT json_agg(x) FROM (
      SELECT m.id, m.repo, m.model, m.claimed_by, m.finished_at::text AS finished_at,
             m.goal, m.result, r.id AS report_id, r.markdown,
             (SELECT json_agg(json_build_object('category', d.category, 'artifacts', d.artifacts))
                FROM tensor_capability_deltas d WHERE d.mission_id = m.id) AS deltas
      FROM tensor_dev_missions m
      {join}
      WHERE {where} AND length(coalesce(m.goal,'')) >= 80
      ORDER BY md5(m.id::text)
      LIMIT {int(n)}
    ) x;
    """
    rows = _psql_json(dsn, sql) or []
    out = []
    for r in rows:
        deltas = r.pop("deltas") or []
        delta = None
        for d in deltas:
            arts = d.get("artifacts") or []
            if isinstance(arts, dict):
                arts = list(arts.values())
            if d.get("category") and str(d.get("category")) != "no_substantive_delta" and arts:
                delta = {"category": d["category"], "artifacts": [str(a) for a in arts]}
                break
        flat = _flatten_result(r.get("result"))
        out.append({
            "mission_id": int(r["id"]),
            "stratum": layer,
            "repo": r.get("repo") or "unknown",
            "model": r.get("model") or "",
            "claimed_by": r.get("claimed_by") or "",
            "finished_at": r.get("finished_at") or "",
            "goal": sanitize(r.get("goal") or ""),
            "result": _sanitize_obj(flat),
            "report": {"id": r.get("report_id"),
                       "markdown": sanitize(r.get("markdown") or "") if layer == "report" else ""},
            "delta": _sanitize_obj(delta),
        })
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dsn", default=os.environ.get("TENSOR_DSN", ""))
    ap.add_argument("--per-stratum", type=int, default=20)
    ap.add_argument("--out", default=str(ROOT / "artifacts/IN56_N09/base_corpus.json"))
    args = ap.parse_args(argv)
    if not args.dsn:
        print("need --dsn or TENSOR_DSN", file=sys.stderr)
        return 2
    bases = (harvest_layer(args.dsn, layer="report", n=args.per_stratum)
             + harvest_layer(args.dsn, layer="summary", n=args.per_stratum))
    bases.sort(key=lambda b: (b["stratum"], b["mission_id"]))
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(bases, ensure_ascii=False, indent=1), encoding="utf-8")
    counts = {}
    for b in bases:
        counts[b["stratum"]] = counts.get(b["stratum"], 0) + 1
    print(f"wrote {len(bases)} bases {counts} -> {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())