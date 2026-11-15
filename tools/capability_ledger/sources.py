"""
证据元数据采集（扫描源）——把平台报告索引落成本地可复算快照
=================================================================
两条通路，产物结构一致（`platform-reports-index/1.0`）：

- 在线：`sync()` 走 Tensor 报告 API（/tensor/reports 分页），
  只保留与注册表模式匹配（强指纹或模式弱匹配）的报告元数据。
- 离线：`snapshot_from_records()` 从任意记录列表生成快照（测试/回放用）。

快照不含任何手抄数字：字段来自 API 原样元数据，`as_of` 取记录里的最大
created_at（而非墙钟时间），因此同一快照两次复算必然逐字节一致。
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from . import engine

SNAPSHOT_SCHEMA = "platform-reports-index/1.0"
_FIELDS = ("id", "mission_id", "scenario_key", "title", "summary", "tags", "created_at", "status")


def _http_get_json(url: str, token: str | None, timeout: float = 60.0,
                   retries: int = 4, backoff: float = 2.0) -> dict:
    """带 429/5xx 退避重试的 GET（平台有速率限制）。"""
    import time as _time
    last: Exception | None = None
    for attempt in range(retries + 1):
        req = urllib.request.Request(url)
        if token:
            req.add_header("Authorization", f"Bearer {token}")
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            last = e
            if e.code in (429, 500, 502, 503, 504) and attempt < retries:
                _time.sleep(backoff * (2 ** attempt))
                continue
            raise
    raise last if last else RuntimeError("GET failed")


def login(base: str, user: str | None = None, password: str | None = None) -> str:
    user = user or os.environ.get("TENSOR_AGENT_USER", "")
    password = password or os.environ.get("TENSOR_AGENT_PASS", "")
    body = json.dumps({"username": user, "password": password}).encode()
    req = urllib.request.Request(base.rstrip("/") + "/auth/login", data=body, method="POST")
    req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req, timeout=60) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    token = data.get("access_token") or data.get("token") or (data.get("data") or {}).get("access_token")
    if not token:
        raise RuntimeError("登录成功但未拿到 access_token")
    return token


def fetch_report_records(base: str, token: str, api_path: str = "/tensor/reports",
                         page_size: int = 100, max_pages: int = 400) -> list[dict]:
    """分页拉全部报告元数据（不含 markdown，控制体积）。"""
    records: list[dict] = []
    seen: set[Any] = set()
    for page in range(max_pages):
        url = f"{base.rstrip('/')}{api_path}?limit={page_size}&offset={page * page_size}"
        try:
            data = _http_get_json(url, token)
        except (urllib.error.URLError, ValueError) as e:
            raise RuntimeError(f"取报告列表失败（page={page}）：{e}") from e
        items = data.get("items", [])
        if not items:
            break
        for it in items:
            rid = it.get("id")
            if rid in seen:
                continue
            seen.add(rid)
            rec = {k: it.get(k) for k in _FIELDS if k in it}
            rec["tags"] = list(it.get("tags") or [])
            records.append(rec)
        if len(items) < page_size:
            break
    return records


def fetch_by_mission_sweep(base: str, token: str, lo: int, hi: int,
                           api_path: str = "/tensor/reports", per_mission: int = 5,
                           throttle: float = 0.03, max_rounds: int = 4) -> dict:
    """按 mission_id 扫描窗口内报告元数据（列表 API 无 offset，只能按任务维度分页）。

    被限流/网络错误的任务记入 failed，下一轮重试，避免静默丢证据。
    返回 {"records": [...], "failed": [...], "rounds": n}。
    """
    import time as _time
    pending = list(range(int(lo), int(hi) + 1))
    records: list[dict] = []
    rounds = 0
    while pending and rounds < max_rounds:
        rounds += 1
        still: list[int] = []
        for mid in pending:
            url = f"{base.rstrip('/')}{api_path}?mission_id={mid}&limit={per_mission}"
            try:
                data = _http_get_json(url, token, timeout=30, retries=4, backoff=3.0)
            except (urllib.error.URLError, ValueError, OSError):
                still.append(mid)
                continue
            for it in data.get("items", []):
                rec = {k: it.get(k) for k in _FIELDS if k in it}
                rec["tags"] = list(it.get("tags") or [])
                records.append(rec)
            if throttle:
                _time.sleep(throttle)
        if still and still == pending:  # 整轮无进展：退避后再试
            _time.sleep(5.0 * rounds)
        pending = still
    return {"records": records, "failed": pending, "rounds": rounds}
def _trim(rec: dict, summary_chars: int) -> dict:
    out = dict(rec)
    s = str(out.get("summary") or "")
    if len(s) > summary_chars:
        out["summary"] = s[:summary_chars]
    out["tags"] = list(out.get("tags") or [])
    return out


def _matches_any(rec: dict, registry: dict, matching: dict) -> bool:
    for reg in registry.get("capabilities", []):
        if engine.match_records(reg, [rec], matching):
            return True
    return False


def snapshot_from_records(records: list[dict], cfg: dict, registry: dict | None = None,
                          source: str = "records", filter_to_registry: bool = True) -> dict:
    """筛出与注册表匹配的记录 → 快照对象（确定性排序）。"""
    summary_chars = int(cfg.get("snapshot_summary_chars", 900))
    matching = cfg.get("matching", {})
    kept: dict[Any, dict] = {}
    dropped = 0
    for rec in records:
        if filter_to_registry and registry is not None and not _matches_any(rec, registry, matching):
            dropped += 1
            continue
        kept.setdefault(rec.get("id"), _trim(rec, summary_chars))
    out_records = sorted(kept.values(), key=lambda r: (str(r.get("created_at", "")), str(r.get("id", ""))))
    as_of = max((str(r.get("created_at", "")) for r in out_records), default="")
    return {
        "schema_version": SNAPSHOT_SCHEMA,
        "source": source,
        "generated_at": as_of,
        "as_of": as_of,
        "record_count": len(out_records),
        "dropped_unmatched": dropped,
        "fields": list(_FIELDS),
        "records": out_records,
    }


def sync(cfg: dict, root: str | Path | None = None, base: str | None = None,
         token: str | None = None, write: bool = True, lo: int | None = None,
         hi: int | None = None, merge: bool = True) -> dict:
    """在线采集 + 落盘。支持分段扫描（--from/--to）与增量合并（merge）。

    任何失败只降级（返回 warning），不抛异常。
    """
    root_p = engine.repo_root(root)
    base = base or os.environ.get("TENSOR_API_BASE", "http://127.0.0.1:8010/api/v1")
    registry = engine.load_registry(cfg, root_p)
    sweep = cfg.get("mission_sweep", {})
    lo = sweep.get("from", 0) if lo is None else lo
    hi = sweep.get("to", 0) if hi is None else hi
    try:
        if not token:
            token = login(base)
        api_path = cfg.get("report_api_path", "/tensor/reports")
        records = fetch_report_records(base, token, api_path, int(cfg.get("report_page_size", 100)),
                                       max_pages=1)  # 最新一页（列表 API 无 offset）
        sweep_failed = []
        if sweep.get("enabled", True):
            sw = fetch_by_mission_sweep(base, token, lo, hi, api_path)
            records += sw["records"]
            sweep_failed = sw["failed"]
    except (RuntimeError, urllib.error.URLError, OSError, KeyError) as e:
        return {"status": "degraded", "written": False, "records": 0, "sweep_failed": [],
                "warnings": [f"在线采集失败（fail-soft，沿用既有快照）：{e}"]}

    existing: list[dict] = []
    p = Path(cfg.get("snapshot_path", ""))
    if not p.is_absolute():
        p = root_p / p
    if merge and p.exists():
        try:
            existing = json.loads(p.read_text(encoding="utf-8")).get("records", [])
        except (json.JSONDecodeError, OSError):
            existing = []
    by_id = {r.get("id"): r for r in existing}
    for r in records:
        by_id.setdefault(r.get("id"), r)
    snap = snapshot_from_records(list(by_id.values()), cfg, registry,
                                 source=f"api:{base}+missions[{lo},{hi}]")

    result = {"status": "ok", "sweep_failed": sweep_failed, "fetched": len(records),
              "merged_from_existing": len(existing),
              "kept": snap["record_count"], "dropped_unmatched": snap["dropped_unmatched"],
              "as_of": snap["as_of"], "warnings": [], "window": [lo, hi]}
    if sweep_failed:
        result["warnings"].append(f"有 {len(sweep_failed)} 个任务未取到（限流/网络），见 sweep_failed")
        result["status"] = "degraded"
    if write:
        w = engine.write_artifact(snap, p)
        result.update({"written": w["written"], "path": str(p)})
        result["warnings"] += w["warnings"]
        if not w["written"]:
            result["status"] = "degraded"
    return result
