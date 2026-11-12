"""kb_lifecycle —— knowledge_base 层：use_count / last_used 生命周期与遗忘日志。

规则（预声明）
- 引用即 +1：被干货包选中 → ``injected_count += 1, last_hit_at = now``；
  被交付文本命中 → ``cited_count += 1, last_cited_at = now``（ledger.attribute 调用）。
- 分档：hot（近 ``hot_days`` 有引用）> warm（有引用但过窗）>
  stale（零引用且龄 > ``stale_days``）> archive（零引用且龄 > ``archive_days``）。
- 降权：写回 ``decay_weight``（1.0/0.6/0.3/0.0），assembler 用 relevance×weight 排序。
- 归档：**默认 dry-run**；``--apply`` 才改 status，且先落快照文件（可回滚），
  每个动作一行遗忘日志（``runs/memory_compress/forgetting_log.jsonl``）。
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from ..context_package.loader import ROOT, sha16

DEFAULT_POLICY = {
    "hot_days": 30,
    "stale_days": 90,
    "archive_days": 180,
    "weights": {"hot": 1.0, "warm": 0.6, "stale": 0.3, "archive": 0.0},
    "log_dir": "runs/memory_compress",
}


def _now() -> datetime:
    return datetime.now(timezone.utc).astimezone()


def _parse(ts: Any) -> datetime | None:
    if not ts:
        return None
    try:
        d = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
    except Exception:
        return None
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)   # naive 按 UTC 归一，防跨时区相减崩


def decay_tiers(rows: list[Mapping[str, Any]], policy: Mapping[str, Any] | None = None,
                now: datetime | None = None) -> list[dict]:
    """给每条 KB 打档位与衰减权重（纯函数，不写库）。"""
    pol = {**DEFAULT_POLICY, **(policy or {})}
    now = now or _now()
    out = []
    for r in rows:
        lasts = [d for d in (_parse(r.get("last_cited_at")), _parse(r.get("last_hit_at"))) if d]
        cited = int(r.get("cited_count") or 0)
        injected = int(r.get("injected_count") or 0)
        last = max(lasts) if lasts else _parse(r.get("created_at"))
        age_days = (now - last).days if last else 10_000
        if cited and age_days <= pol["hot_days"]:
            tier = "hot"
        elif (cited or injected) and age_days <= pol["stale_days"]:
            tier = "warm"
        elif age_days <= pol["archive_days"]:
            tier = "stale"
        else:
            tier = "archive"
        out.append({
            "kb_id": r.get("id"), "kb_key": r.get("kb_key"), "ktype": r.get("ktype"),
            "domain": r.get("domain"), "cited_count": cited, "injected_count": injected,
            "last_used": last.isoformat(timespec="seconds") if last else None,
            "age_days": age_days, "tier": tier, "decay_weight": pol["weights"][tier],
            "action": "archive" if tier == "archive" else ("downweight" if tier == "stale" else "keep"),
        })
    return sorted(out, key=lambda x: (x["tier"] != "archive", -x["age_days"]))


def write_forgetting_log(tiers: list[Mapping[str, Any]], action: str = "dry_run",
                         log_dir: Path | str | None = None) -> dict:
    """写遗忘日志（每行一条决策，带档位/年龄/权重/动作/是否已执行）。"""
    d = Path(log_dir) if log_dir else ROOT / DEFAULT_POLICY["log_dir"]
    d.mkdir(parents=True, exist_ok=True)
    ts = _now()
    p = d / "forgetting_log.jsonl"
    n = 0
    with open(p, "a", encoding="utf-8") as fh:
        for t in tiers:
            if t.get("tier") not in ("stale", "archive"):
                continue
            row = {"ts": ts.isoformat(timespec="seconds"), "mode": action,
                   "action": t.get("action"), "kb_id": t.get("kb_id"), "kb_key": t.get("kb_key"),
                   "tier": t.get("tier"), "age_days": t.get("age_days"),
                   "decay_weight": t.get("decay_weight"),
                   "reason": f"zero_or_old_reference(tier={t.get('tier')},age={t.get('age_days')}d)"}
            fh.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
            n += 1
    return {"log": str(p), "rows": n, "mode": action,
            "snapshot_sha": sha16(json.dumps([t.get("kb_id") for t in tiers]))}


def touch(kb_id: int, kind: str = "inject", conn=None) -> dict:
    """引用即 +1：kind ∈ {inject, cite}；无连接则返回计划动作（不写库）。"""
    if conn is None:
        return {"kb_id": kb_id, "kind": kind, "applied": False, "reason": "NO_CONN"}
    col, ts_col = ("injected_count", "last_hit_at") if kind == "inject" else ("cited_count", "last_cited_at")
    try:
        with conn.cursor() as cur:
            cur.execute(f"UPDATE knowledge_base SET {col} = COALESCE({col},0)+1, "
                        f"{ts_col} = %(ts)s WHERE id = %(id)s", {"ts": _now().isoformat(), "id": kb_id})
        conn.commit()
        return {"kb_id": kb_id, "kind": kind, "applied": True}
    except Exception as exc:  # noqa: BLE001
        return {"kb_id": kb_id, "kind": kind, "applied": False, "reason": str(exc)[:120]}


def snapshot(rows: list[Mapping[str, Any]], out_dir: Path | str | None = None) -> str:
    """归档前快照（回滚用）：整表 id/status/injected/cited 落盘。"""
    d = Path(out_dir) if out_dir else ROOT / DEFAULT_POLICY["log_dir"]
    d.mkdir(parents=True, exist_ok=True)
    p = d / f"kb_snapshot_{_now().strftime('%Y%m%dT%H%M%S')}.json"
    with open(p, "w", encoding="utf-8") as fh:
        json.dump([{k: r.get(k) for k in ("id", "kb_key", "status", "injected_count", "cited_count")}
                   for r in rows], fh, ensure_ascii=False, indent=2)
    return str(p)
