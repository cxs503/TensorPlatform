#!/usr/bin/env python3
"""真库快照采集器：从平台只读 API 取三张表的真数据，落成可复算快照（JSON + SQLite）。

用法::

    python3 tools/proposal_gate/dedup_evidence_collect.py \
        --out artifacts/m9953/snapshot --max-plans 120 --max-missions 400

产物：
* ``snapshot.json`` —— ``{"tables": {name: [{id, text}]}, "provenance": {...}}``
  （provenance 含端点、取数时间、逐表行数/内容 sha256），供 :class:`JsonSnapshotSource`；
* ``snapshot.sqlite`` —— 同三张真表落成 ``(id, text)`` 两列，供 :class:`DbapiSource`
  做**只读**复算与「前后行哈希对照」证明；
* ``collect_manifest.json`` —— 列表 + 各表指纹 + API 端点清单。

纪律：只读 GET；不写平台库；凭证只从环境变量取，不落盘。
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Mapping, Sequence

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tools.proposal_gate.dedup_evidence import (  # noqa: E402
    EvidenceConfig,
    MemorySource,
    canonical_bytes,
    sha256_hex,
    snapshot_fingerprint,
    table_fingerprint,
)


class Client:
    """平台只读 API 客户端（Bearer token；GET only）。"""

    def __init__(self, base: str, username: str, password: str, *, timeout: float = 60.0) -> None:
        self.base = base.rstrip("/")
        self.timeout = timeout
        self.token = self._login(username, password)

    def _login(self, username: str, password: str) -> str:
        payload = json.dumps({"username": username, "password": password}).encode("utf-8")
        req = urllib.request.Request(
            f"{self.base}/auth/login", data=payload, headers={"Content-Type": "application/json"}
        )
        with urllib.request.urlopen(req, timeout=self.timeout) as resp:
            data = json.loads(resp.read())
        token = data.get("access_token")
        if not token:
            raise RuntimeError("登录未返回 access_token")
        return str(token)

    def get(self, path: str, **params: Any) -> Any:
        query = urllib.parse.urlencode({k: v for k, v in params.items() if v is not None})
        url = f"{self.base}{path}" + (f"?{query}" if query else "")
        req = urllib.request.Request(url, headers={"Authorization": f"Bearer {self.token}"})
        with urllib.request.urlopen(req, timeout=self.timeout) as resp:
            return json.loads(resp.read())


def _dig(record: Mapping[str, Any], path: str) -> str:
    cur: Any = record
    for part in path.split("."):
        if not isinstance(cur, Mapping):
            return ""
        cur = cur.get(part)
    return cur if isinstance(cur, str) else ""


def _row_text(record: Mapping[str, Any], text_fields: Sequence[str]) -> str:
    parts = [p for p in (_dig(record, f) for f in text_fields) if p]
    return "\n".join(parts).strip()


def collect(client: Client, cfg: EvidenceConfig, *, max_plans: int, max_missions: int, page: int = 50) -> dict[str, Any]:
    """抓取三表真数据（plan 分页 → plan nodes；missions 分页）。"""
    by_name = {t.name: t for t in cfg.tables}
    nodes: list[dict[str, Any]] = []
    plans: list[dict[str, Any]] = []
    proposals: list[dict[str, Any]] = []
    endpoints: list[str] = []

    offset = 0
    seen_plans = 0
    while seen_plans < max_plans:
        listing = client.get("/tensor/campaigns/plans", limit=page, offset=offset)
        items = listing.get("items") or []
        if not items:
            break
        endpoints.append(f"GET /tensor/campaigns/plans?limit={page}&offset={offset}")
        for plan in items:
            plans.append(
                {
                    "id": plan.get("id"),
                    "text": _row_text(plan, by_name["campaign_plans"].text_fields),
                }
            )
            node_listing = client.get(f"/tensor/campaigns/plans/{plan.get('id')}/nodes")
            endpoints.append(f"GET /tensor/campaigns/plans/{plan.get('id')}/nodes")
            for node in node_listing.get("items") or []:
                nodes.append(
                    {
                        "id": node.get("id"),
                        "text": _row_text(node, by_name["campaign_nodes"].text_fields),
                    }
                )
        seen_plans += len(items)
        offset += len(items)

    offset = 0
    while len(proposals) < max_missions:
        listing = client.get("/tensor/missions", limit=min(page, 200), offset=offset)
        items = listing.get("items") or []
        if not items:
            break
        endpoints.append(f"GET /tensor/missions?limit={min(page, 200)}&offset={offset}")
        for mission in items:
            proposals.append(
                {
                    "id": mission.get("id"),
                    "text": _row_text(mission, by_name["proposals"].text_fields),
                }
            )
            if len(proposals) >= max_missions:
                break
        offset += len(items)

    tables = {
        "campaign_nodes": [r for r in nodes if r["text"]],
        "campaign_plans": [r for r in plans if r["text"]],
        "proposals": [r for r in proposals if r["text"]],
    }
    dropped: dict[str, int] = {}
    for name, rows in tables.items():
        seen: set[str] = set()
        kept: list[dict[str, Any]] = []
        for row in rows:
            key = str(row["id"])
            if row["id"] is None or key in seen:
                continue
            seen.add(key)
            kept.append(row)
        dropped[name] = len(rows) - len(kept)
        kept.sort(key=lambda r: (len(str(r["id"])), str(r["id"])))
        tables[name] = kept
    return {"tables": tables, "endpoints": sorted(set(endpoints)), "dropped_duplicate_ids": dropped}


def write_snapshot(payload: Mapping[str, Any], cfg: EvidenceConfig, out_dir: Path) -> dict[str, Any]:
    """落盘 JSON 快照 + SQLite 只读副本 + manifest（含逐表指纹）。"""
    out_dir.mkdir(parents=True, exist_ok=True)
    tables = payload["tables"]
    source = MemorySource(tables)
    snap = snapshot_fingerprint(source, cfg)
    per_table = {fp.table: fp.to_dict() for fp in snap.tables}

    snapshot_doc = {
        "schema_version": "dedup-evidence-snapshot/1.0",
        "provenance": {
            "source": "platform read-only API",
            "endpoints": payload["endpoints"],
            "collected_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "tables": per_table,
            "snapshot_sha256": snap.sha256,
        },
        "tables": {name: rows for name, rows in tables.items()},
    }
    json_path = out_dir / "snapshot.json"
    json_path.write_text(json.dumps(snapshot_doc, ensure_ascii=False, sort_keys=True), encoding="utf-8")

    db_path = out_dir / "snapshot.sqlite"
    if db_path.exists():
        db_path.unlink()
    conn = sqlite3.connect(db_path)
    try:
        for name, rows in tables.items():
            conn.execute(f"CREATE TABLE {name} (id TEXT PRIMARY KEY, text TEXT NOT NULL)")
            conn.executemany(
                f"INSERT INTO {name} (id, text) VALUES (?, ?)",
                [(str(r["id"]), r["text"]) for r in rows],
            )
        conn.commit()
    finally:
        conn.close()

    manifest = {
        "schema_version": "dedup-evidence-collect/1.0",
        "snapshot_json": str(json_path),
        "snapshot_json_sha256": sha256_hex(json.loads(json_path.read_text(encoding="utf-8"))),
        "snapshot_sqlite": str(db_path),
        "snapshot_sqlite_sha256": sha256_hex_bytes(db_path.read_bytes()),
        "tables": per_table,
        "snapshot_sha256": snap.sha256,
        "total_rows": sum(fp["row_count"] for fp in per_table.values()),
        "endpoints": payload["endpoints"],
        "dropped_duplicate_ids": payload.get("dropped_duplicate_ids", {}),
        "collected_at": snapshot_doc["provenance"]["collected_at"],
    }
    (out_dir / "collect_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2), encoding="utf-8"
    )
    return manifest


def sha256_hex_bytes(data: bytes) -> str:
    import hashlib

    return hashlib.sha256(data).hexdigest()


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--config", default="config/dedup_evidence.json")
    ap.add_argument("--out", default="artifacts/m9953/snapshot")
    ap.add_argument("--max-plans", type=int, default=120)
    ap.add_argument("--max-missions", type=int, default=400)
    args = ap.parse_args(argv)

    cfg = EvidenceConfig.load(args.config)
    base = os.environ.get("TENSOR_API_BASE")
    user = os.environ.get("TENSOR_AGENT_USER")
    password = os.environ.get("TENSOR_AGENT_PASS")
    if not (base and user and password):
        print("缺少 TENSOR_API_BASE/TENSOR_AGENT_USER/TENSOR_AGENT_PASS", file=sys.stderr)
        return 2
    client = Client(base, user, password)
    payload = collect(client, cfg, max_plans=args.max_plans, max_missions=args.max_missions)
    manifest = write_snapshot(payload, cfg, Path(args.out))
    print(json.dumps(manifest, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())