"""数据源适配：succeeded 报告与 shadow 处置日志。

两种入口，测试只走文件入口（离线可复算），生产走 psql 只读导出：

1. ``load_reports`` / ``write_reports_snapshot`` —— tensor_reports ⨝ tensor_dev_missions
   (status='succeeded') 的只读快照，JSONL，一行一条；
2. ``load_shadow_dispositions`` —— shadow 处置日志，两种形态都吃：
   - ``shadow_merge.json``（PR 级「可合/不可合」判决 + reasons + outcome）
   - ``coordinator_shadow`` 表导出（JSONL：verdict.findings / urgent）
"""

from __future__ import annotations

import json
import os
import subprocess
from typing import Iterable, Iterator

DEFAULT_PSQL = ["runuser", "-u", "postgres", "--", "psql", "-d", "tensorplatform", "-t", "-A", "-F", "\x01"]

# 只读导出 SQL（无 DDL/DML；limit 由参数给定以保证快照可复现）
# markdown 用 base64 单行传输（正文含换行，直接用 psql 行输出会被拆断——实测只回 2 行）
EXPORT_SQL = """
SELECT m.id, r.id, coalesce(r.scenario_key,''),
       regexp_replace(r.title, '[\r\n]+', ' ', 'g'),
       regexp_replace(coalesce(r.summary,''), '[\r\n]+', ' ', 'g'),
       replace(encode(convert_to(substr(r.markdown, 1, {md_cap}), 'UTF8'), 'base64'), chr(10), ''),
       coalesce(m.repo,''), coalesce(m.status,''), coalesce(m.finished_at::text,'')
FROM tensor_reports r
JOIN tensor_dev_missions m ON m.id = r.mission_id
WHERE m.status = 'succeeded'
ORDER BY r.created_at DESC, r.id DESC
LIMIT {limit}
"""


def _iter_json_records(path: str) -> Iterator[dict]:
    if not os.path.exists(path):
        return
    with open(path, "r", encoding="utf-8") as f:
        first = f.read(1)
        f.seek(0)
        if first == "[" or first == "{":
            try:
                data = json.load(f)
                if isinstance(data, dict):
                    data = data.get("reports") or data.get("entries") or [data]
                for item in data:
                    if isinstance(item, dict):
                        yield item
                return
            except json.JSONDecodeError:
                f.seek(0)
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(rec, dict):
                yield rec


def load_reports(path: str) -> list[dict]:
    """读报告快照（JSONL 或 JSON 数组）。字段缺失即跳过，不猜测补全。"""
    out: list[dict] = []
    for rec in _iter_json_records(path):
        if not rec.get("title"):
            continue
        out.append(rec)
    return out


def _b64decode(raw: str) -> str:
    """psql 以 base64 单行回传 markdown；空值/坏值降级为空串（不猜内容）。"""
    raw = (raw or "").strip()
    if not raw:
        return ""
    import base64

    try:
        return base64.b64decode(raw).decode("utf-8", "replace")
    except Exception:
        return ""


def export_reports_from_pg(limit: int = 400, md_cap: int = 4000) -> list[dict]:
    """从平台库只读导出 succeeded 报告快照（生产路径；测试不调用）。"""
    sql = EXPORT_SQL.format(md_cap=int(md_cap), limit=int(limit))
    proc = subprocess.run(
        DEFAULT_PSQL + ["-q", "-c", sql], capture_output=True, text=True, timeout=180
    )
    if proc.returncode != 0:
        raise RuntimeError(f"psql export failed: {proc.stderr[:300]}")
    rows: list[dict] = []
    for line in proc.stdout.splitlines():
        parts = line.split("\x01")
        if len(parts) < 9:
            continue
        rows.append(
            {
                "mission_id": int(parts[0]),
                "report_id": int(parts[1]) if parts[1] else None,
                "scenario_key": parts[2],
                "title": parts[3],
                "summary": parts[4],
                "markdown": _b64decode(parts[5]),
                "repo": parts[6],
                "status": parts[7],
                "finished_at": parts[8],
            }
        )
    return rows


def write_reports_snapshot(rows: Iterable[dict], path: str) -> str:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
    return path


def load_shadow_dispositions(path: str) -> list[dict]:
    """读 shadow 处置日志，统一成 {repo, pr, verdict, reasons, outcome} 的扁平形态。"""
    out: list[dict] = []
    data: object
    if not os.path.exists(path):
        return out
    with open(path, "r", encoding="utf-8") as f:
        text = f.read().strip()
    if not text:
        return out
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return [rec for rec in _iter_json_records(path)]
    items: Iterable[dict]
    if isinstance(data, dict) and "entries" in data:
        items = list(data["entries"].values())
    elif isinstance(data, dict) and "reports" in data:
        items = data["reports"]
    elif isinstance(data, list):
        items = data
    else:
        items = [data]
    for ent in items:
        if not isinstance(ent, dict):
            continue
        verdict = ent.get("verdict")
        if isinstance(verdict, dict):
            verdict_text = verdict.get("verdict") or verdict.get("level") or "diagnose"
            reasons = verdict.get("reasons") or []
        else:
            verdict_text = str(verdict or "")
            reasons = ent.get("reasons") or []
        out.append(
            {
                "repo": ent.get("repo") or "?",
                "pr": ent.get("pr") or ent.get("id") or "?",
                "verdict_text": verdict_text,
                "reasons": reasons,
                "outcome": ent.get("outcome") or "",
            }
        )
    return out