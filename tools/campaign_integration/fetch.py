"""战役注册表快照抓取（``/api/v1/tensor/campaigns/plans/{plan_id}/nodes`` → 落盘 JSON）。

为什么落快照而不是每次直连：**审计链需要可复算**。快照 sha256 进 provenance，
报告与判定体引用同一份快照，事后任何人拿同一份快照重跑应得同样结论；
API 漂移只会让新快照摘要变化，不会让历史结论无声改写。

凭据只从环境读（``TENSOR_API_BASE`` / ``TENSOR_AGENT_USER`` / ``TENSOR_AGENT_PASS``），
不落盘、不打印。
"""

from __future__ import annotations

import os
import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

SNAPSHOT_SCHEMA = "campaign-snapshot/1.0"

#: 报告正文里**显式**声明的 mission 归属（只认 "Mission m12345" 这种强声明，避免正文里
#: 顺带提到别的 mission 造成误报）。
_MISSION_CLAIM = re.compile(r"Mission\s+m(\d{4,6})")
_MAX_REPORT_DETAILS = 10


def _login(base: str, user: str, password: str, timeout: float):
    import requests
    resp = requests.post(f"{base}/auth/login",
                         json={"username": user, "password": password}, timeout=timeout)
    resp.raise_for_status()
    return resp.json()["access_token"]


def fetch_snapshot(plan_id: int, base_url: Optional[str] = None,
                   user: Optional[str] = None, password: Optional[str] = None,
                   timeout: float = 30.0) -> Dict[str, Any]:
    """抓一份战役快照（计划头 + 全部节点 + 每 mission 的报告条目）。"""
    import requests

    base = (base_url or os.environ.get("TENSOR_API_BASE")
            or "http://127.0.0.1:8010/api/v1").rstrip("/")
    user = user or os.environ.get("TENSOR_AGENT_USER", "")
    password = password or os.environ.get("TENSOR_AGENT_PASS", "")
    token = _login(base, user, password, timeout)
    headers = {"Authorization": f"Bearer {token}"}

    plans = requests.get(f"{base}/tensor/campaigns/plans", headers=headers,
                         timeout=timeout).json()
    items = plans.get("items") if isinstance(plans, dict) else plans
    plan = next((p for p in (items or []) if int(p.get("id", -1)) == int(plan_id)), None)
    if plan is None:
        raise ValueError(f"plan {plan_id} 不在 {base}/tensor/campaigns/plans 列表中")

    raw_nodes = requests.get(f"{base}/tensor/campaigns/plans/{plan_id}/nodes",
                             headers=headers, timeout=timeout).json()
    raw_nodes = raw_nodes.get("items") if isinstance(raw_nodes, dict) else raw_nodes

    nodes: List[Dict[str, Any]] = []
    reports: Dict[str, List[Any]] = {}
    for raw in raw_nodes or []:
        meta = raw.get("metadata") or {}
        key = str(meta.get("ext_id") or raw.get("name") or raw.get("id"))
        nodes.append({
            "node_key": key,
            "campaign_node_id": raw.get("id"),
            "mission_id": raw.get("mission_id"),
            "title": raw.get("name"),
            "repo": meta.get("repo"),
            "status": raw.get("status"),
            "block_reason": raw.get("block_reason"),
            "consecutive_fails": raw.get("consecutive_fails"),
            "actual_end": raw.get("actual_end"),
            "metadata": {
                "acceptance_criteria": list(meta.get("acceptance_criteria") or []),
                "goal": meta.get("goal"),
                "layer": meta.get("layer"),
                "risk": meta.get("risk"),
            },
        })
        mid = raw.get("mission_id")
        if mid is not None:
            rep = requests.get(f"{base}/tensor/reports", headers=headers,
                               params={"mission_id": mid, "limit": 20},
                               timeout=timeout).json()
            entries = []
            for item in (rep.get("items") or [])[:_MAX_REPORT_DETAILS]:
                rid = item.get("id")
                claimed: List[int] = []
                try:
                    detail = requests.get(f"{base}/tensor/reports/{rid}",
                                          headers=headers, timeout=timeout).json()
                    claimed = sorted({int(x) for x in
                                      _MISSION_CLAIM.findall(detail.get("markdown") or "")})
                except Exception:  # 详情取不到不影响索引信号，但会显形为 claimed 空
                    claimed = []
                entries.append({
                    "id": rid,
                    "title": item.get("title"),
                    "claimed_missions": claimed,
                    "mission_link_mismatch": bool(claimed) and int(mid) not in claimed,
                })
            reports[str(mid)] = entries

    nodes.sort(key=lambda n: str(n["node_key"]))
    return {
        "schema": SNAPSHOT_SCHEMA,
        "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "api_base": base,
        "spec_id": f"campaign-{plan.get('name') or plan_id}",
        "plan": {
            "plan_id": plan.get("id"),
            "name": plan.get("name"),
            "description": plan.get("description"),
            "status": plan.get("status"),
            "plan_version": plan.get("plan_version"),
            "metadata": plan.get("metadata") or {},
        },
        "nodes": nodes,
        "reports": reports,
    }


__all__ = ["SNAPSHOT_SCHEMA", "fetch_snapshot"]