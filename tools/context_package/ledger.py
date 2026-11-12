"""ledger —— 注入审计账本 + 命中归因（每单记录"注入了什么 / 命中了什么"）。

两路落账
--------
1. **JSONL**（默认，永远可用）：``runs/context_injections/<yyyy-mm-dd>.jsonl``，每行一个
   包摘要 + 条目 id 列表 + package sha256，可复算、可 diff。
2. **总线表**（可选，``TENSOR_CONTEXT_DB_SINK=1``）：写 ``knowledge_injections``
   （m15722 KBM-TKC-INDEX-API 建的总线表，字段 ``mission_id/kb_id/kb_key/consumer/
   source/package_sha256/match_score/match_terms/injected_at``）——**复用既有总线，
   不另开通道**；DB 不可用静默降级为仅 JSONL。

命中归因：``attribute`` 拿任务收尾文本（finish 摘要 / 报告 markdown / 交付产物清单）
去比对包内条目，命中即 ``cited``；写 ``runs/context_injections/<date>.hits.jsonl``，
DB sink 打开时回写 ``cited_at/citation_count`` 与 ``knowledge_base.cited_count``。
"""

from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping

from .loader import ROOT, load_config, terms

DB_SINK_ENV = "TENSOR_CONTEXT_DB_SINK"


def _now() -> datetime:
    return datetime.now(timezone.utc).astimezone()


def _date_str(ts: datetime | None = None) -> str:
    return (ts or _now()).strftime("%Y-%m-%d")


def _package_sha(pkg: Mapping[str, Any]) -> str:
    payload = json.dumps({k: pkg.get(k) for k in ("package_id", "task_class", "as_of", "budget")},
                         ensure_ascii=False, sort_keys=True)
    for s in pkg.get("slots") or []:
        for it in s.get("items") or []:
            payload += f"|{it.get('id')}:{it.get('tokens')}:{it.get('decision')}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def record(pkg: Mapping[str, Any], rendered: str = "", mode: str = "report",
           ledger_dir: Path | str | None = None, consumer: str = "",
           manifest_text: str = "", db=None) -> dict:
    """把一次注入落账，返回审计行（也是 JSONL 的那一行）。"""
    cfg = load_config()
    ld = Path(ledger_dir) if ledger_dir else ROOT / ((cfg.get("ledger") or {}).get("jsonl_dir")
                                                     or "runs/context_injections")
    ts = _now()
    entry = {
        "ts": ts.isoformat(timespec="seconds"),
        "package_id": pkg.get("package_id"),
        "package_sha256": _package_sha(pkg),
        "mission_id": pkg.get("mission_id"),
        "repo": pkg.get("repo"),
        "consumer": consumer,
        "task_class": pkg.get("task_class"),
        "class_evidence": pkg.get("class_evidence"),
        "as_of": pkg.get("as_of"),
        "mode": mode,
        "budget": pkg.get("budget"),
        "audit": {k: (pkg.get("audit") or {}).get(k) for k in
                  ("candidate_pool", "injected_items", "decision_bearing", "density_yield",
                   "dropped_by_reason")},
        "items": [{"id": it.get("id"), "slot": s.get("slot"), "decision": it.get("decision"),
                   "tokens": it.get("tokens"), "relevance": it.get("relevance"),
                   "pointer": it.get("pointer")}
                  for s in (pkg.get("slots") or []) for it in (s.get("items") or [])],
        "rendered_tokens": int(pkg.get("budget", {}).get("used_tokens", 0)),
        "rendered_sha256": hashlib.sha256((rendered or "").encode("utf-8")).hexdigest()[:16],
        "density_gate_hits": [it["id"] for it in
                              [{"id": it.get("id")} for s in (pkg.get("slots") or [])
                               for it in (s.get("items") or [])]],
    }
    try:
        ld.mkdir(parents=True, exist_ok=True)
        with open(ld / f"{_date_str(ts)}.jsonl", "a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry, ensure_ascii=False, sort_keys=True) + "\n")
        entry["_ledger_path"] = str(ld / f"{_date_str(ts)}.jsonl")
    except Exception as exc:  # noqa: BLE001 —— 落账失败不阻断派单
        entry["_ledger_error"] = f"{type(exc).__name__}: {exc}"[:160]
    if os.environ.get(DB_SINK_ENV, "0") not in ("0", "", "false", "False"):
        entry["_db"] = _sink_db(entry, manifest_text)
    return entry


def _sink_db(entry: Mapping[str, Any], manifest_text: str) -> dict:
    """写总线表 ``knowledge_injections``；任何异常静默降级。"""
    try:
        from .sources import PgReader
        reader = PgReader()
        conn = reader._connect()
        n = 0
        with conn.cursor() as cur:
            for it in entry.get("items") or []:
                iid = str(it.get("id") or "")
                if not iid.startswith("kb:"):
                    continue          # 总线表语义：KB 条目注入（m15722）
                try:
                    kb_id = int(iid.split(":", 1)[1])
                except Exception:
                    continue
                cur.execute(
                    """INSERT INTO knowledge_injections
                       (injection_id, mission_id, kb_id, kb_key, kb_domain, consumer, source,
                        package_sha256, match_score, match_terms, injected_at, citation_count)
                       VALUES (%(iid)s, %(mid)s, %(kb)s, %(key)s, %(dom)s, %(cons)s, %(src)s,
                               %(sha)s, %(score)s, %(terms)s, %(ts)s, 0)""",
                    {"iid": f"ctxpkg:{entry.get('package_id')}:{kb_id}",
                     "mid": entry.get("mission_id"), "kb": kb_id, "key": "",
                     "dom": entry.get("repo"), "cons": entry.get("consumer") or "context_package",
                     "src": "context_package/v1", "sha": entry.get("package_sha256"),
                     "score": float(it.get("relevance") or 0.0),
                     "terms": (manifest_text or "")[:400], "ts": entry.get("ts")})
                n += 1
        conn.commit()
        reader.close()
        return {"inserted": n}
    except Exception as exc:  # noqa: BLE001
        return {"error": f"{type(exc).__name__}: {exc}"[:160]}


def attribute(entry: Mapping[str, Any], artifact_text: str, ledger_dir: Path | str | None = None,
              min_terms: int = 2) -> dict:
    """命中归因：交付文本里出现条目 id 或 ≥min_terms 个条目专有词元即算命中。"""
    cfg = load_config()
    ld = Path(ledger_dir) if ledger_dir else ROOT / ((cfg.get("ledger") or {}).get("jsonl_dir")
                                                     or "runs/context_injections")
    at = terms(artifact_text)
    hits, misses = [], []
    for it in entry.get("items") or []:
        iid = str(it.get("id") or "")
        probe = terms(" ".join(str(x) for x in (it.get("pointer"), it.get("id"))))
        direct = bool(iid) and (iid in str(artifact_text))
        shared = len(probe & at)
        if direct or shared >= min_terms:
            hits.append({"id": iid, "direct": direct, "shared_terms": shared,
                         "decision": it.get("decision")})
        else:
            misses.append({"id": iid, "decision": it.get("decision")})
    out = {"ts": _now().isoformat(timespec="seconds"), "package_id": entry.get("package_id"),
           "mission_id": entry.get("mission_id"), "injected": len(entry.get("items") or []),
           "hits": hits, "misses": misses,
           "hit_rate": round(len(hits) / max(1, len(entry.get("items") or [])), 4)}
    try:
        ld.mkdir(parents=True, exist_ok=True)
        with open(ld / f"{_date_str()}.hits.jsonl", "a", encoding="utf-8") as fh:
            fh.write(json.dumps(out, ensure_ascii=False, sort_keys=True) + "\n")
    except Exception as exc:  # noqa: BLE001
        out["_ledger_error"] = f"{type(exc).__name__}: {exc}"[:160]
    return out


def read_ledger(day: str | None = None, ledger_dir: Path | str | None = None) -> list[dict]:
    """回读当日账本（A/B 与体检用）。"""
    cfg = load_config()
    ld = Path(ledger_dir) if ledger_dir else ROOT / ((cfg.get("ledger") or {}).get("jsonl_dir")
                                                     or "runs/context_injections")
    p = ld / f"{day or _date_str()}.jsonl"
    rows: list[dict] = []
    try:
        with open(p, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line:
                    rows.append(json.loads(line))
    except Exception:
        pass
    return rows