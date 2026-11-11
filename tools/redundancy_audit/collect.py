"""数据采集：平台只读 API → 冗余审计快照（``snapshot.json``）。

为什么用快照而不是每次直连 DB
------------------------------
1. **可复算**：报告只能由快照派生，快照 sha256 一钉，报告就与「采集时刻的库状态」脱钩，
   幂等性（重跑报告逐字节一致）才可证。
2. **只读纪律**：本审计对平台只做 GET，不写库；采集与裁决分离。
3. **库可见性**：``campaign_nodes`` 无 ``budget_actual`` 列（审计发现 F1），plan 级
   ``budget_actual`` 实测恒零（F2），故成本证据取自节点挂靠的 mission（wall-clock 实耗 +
   mission.budget 预算上限）。采集层把这些证据一并冻进快照，账本层不再回查网络。

端点与覆盖偏差（诚实披露）
--------------------------
``GET /tensor/campaigns/plans`` 无 offset 参数、单次上限 100 条，因此**无法拿到全库计划**；
本采集器按 ``status`` 分档扫（active/completed/draft/…），取并集。结果是「最新 100 条活动
计划 + 最新 100 条已完成计划 + 全部草稿」，属**有偏切片**；覆盖偏差写进快照的
``coverage`` 字段，并进入报告局限性一节。节点文本 = 节点名 + metadata.goal。

零领域语义
----------
本模块不认识任何域词：``repo``/``ext_id``/``metric`` 等字段只是原样搬运的字符串。
"""

from __future__ import annotations

import hashlib
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Iterable

from .config import RedundancyConfig, repo_root

USER_AGENT = "redundancy-audit/1.0"

STATUS_SWEEP: tuple[str, ...] = (
    "active",
    "completed",
    "draft",
    "paused",
    "cancelled",
    "failed",
    "archived",
)
PAGE_LIMIT = 100  # 计划端点硬上限
MISSION_PAGE = 200  # mission 端点硬上限


# ── HTTP（只读） ─────────────────────────────────────────────────────────
class ApiClient:
    def __init__(self, base: str | None = None, user: str | None = None, password: str | None = None,
                 timeout: int = 60):
        self.base = (base or os.environ.get("TENSOR_API_BASE", "http://127.0.0.1:8010/api/v1")).rstrip("/")
        self.user = user or os.environ.get("TENSOR_AGENT_USER")
        self.password = password or os.environ.get("TENSOR_AGENT_PASS")
        self.timeout = timeout
        self._token: str | None = None
        self.calls = 0

    def login(self) -> str:
        if self._token:
            return self._token
        if not self.user or not self.password:
            raise RuntimeError("缺少 TENSOR_AGENT_USER / TENSOR_AGENT_PASS 环境变量（只读采集需要登录）")
        body = json.dumps({"username": self.user, "password": self.password}).encode()
        req = urllib.request.Request(
            f"{self.base}/auth/login", data=body,
            headers={"Content-Type": "application/json", "User-Agent": USER_AGENT},
        )
        with urllib.request.urlopen(req, timeout=self.timeout) as resp:
            self.calls += 1
            self._token = json.loads(resp.read())["access_token"]
        return self._token

    def get(self, path: str, **params: Any) -> Any:
        token = self.login()
        url = f"{self.base}{path}"
        clean = {k: v for k, v in params.items() if v is not None}
        if clean:
            url += "?" + urllib.parse.urlencode(clean)
        req = urllib.request.Request(
            url, headers={"Authorization": f"Bearer {token}", "User-Agent": USER_AGENT}
        )
        for attempt in range(3):
            try:
                with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                    self.calls += 1
                    return json.loads(resp.read())
            except urllib.error.HTTPError:
                raise
            except (urllib.error.URLError, TimeoutError):
                if attempt == 2:
                    raise
                time.sleep(1.5 * (attempt + 1))
        raise RuntimeError("unreachable")


# ── 采集 ────────────────────────────────────────────────────────────────
def fetch_plans(client: ApiClient) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """按 status 分档扫计划端点，返回去重后的计划表 + 覆盖统计。"""
    by_id: dict[int, dict[str, Any]] = {}
    per_status: dict[str, int] = {}
    for status in STATUS_SWEEP:
        doc = client.get("/tensor/campaigns/plans", limit=PAGE_LIMIT, status=status)
        items = doc.get("items") or []
        per_status[status] = len(items)
        for pl in items:
            by_id[int(pl["id"])] = {
                "plan_id": int(pl["id"]),
                "name": pl.get("name") or "",
                "status": pl.get("status"),
                "budget_planned": pl.get("budget_planned") or {},
                "budget_actual": pl.get("budget_actual") or {},
                "created_at": pl.get("created_at"),
            }
    plans = [by_id[k] for k in sorted(by_id)]
    coverage = {
        "plans_per_status_query": per_status,
        "plans_unique": len(plans),
        "plan_endpoint_paging": "无 offset 参数、单次上限 100 条 → 按 status 分档取并集（有偏切片，见报告局限性）",
    }
    return plans, coverage


def _criteria_names(metadata: dict[str, Any]) -> list[str]:
    """从 ``metadata.acceptance_criteria`` 抽出度量名集合（只做结构解析，不解释语义）。"""
    raw = metadata.get("acceptance_criteria")
    items: Any = raw
    if isinstance(raw, str):
        try:
            items = json.loads(raw)
        except (json.JSONDecodeError, ValueError):
            items = []
    if not isinstance(items, list):
        return []
    names = []
    for it in items:
        if isinstance(it, dict):
            name = it.get("metric") or it.get("name")
            if isinstance(name, str) and name:
                names.append(name)
    return sorted(set(names))


def _deliverable_paths(goal: str) -> list[str]:
    """从目标正文里抽取形如 ``dir/file.py`` 的交付物路径（纯形态匹配，词表无关）。"""
    import re

    found = set()
    for m in re.finditer(r"[A-Za-z0-9_\-./]+\.(?:py|json|md|cpp|c|h|hpp|f90|jl|ts|yaml|yml)", goal or ""):
        token = m.group(0).strip("./")
        if "/" in token and len(token) >= 6:
            found.add(token)
    return sorted(found)


def fetch_nodes(client: ApiClient, plans: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    nodes: list[dict[str, Any]] = []
    seen: set[int] = set()
    for pl in plans:
        doc = client.get(f"/tensor/campaigns/plans/{pl['plan_id']}/nodes")
        for n in doc.get("items") or []:
            nid = int(n["id"])
            if nid in seen:
                continue
            seen.add(nid)
            meta = n.get("metadata") or {}
            if isinstance(meta, str):
                try:
                    meta = json.loads(meta)
                except (json.JSONDecodeError, ValueError):
                    meta = {}
            goal = meta.get("goal") if isinstance(meta.get("goal"), str) else ""
            name = str(n.get("name") or "")
            nodes.append(
                {
                    "node_id": nid,
                    "plan_id": int(n["plan_id"]),
                    "mission_id": n.get("mission_id"),
                    "name": name,
                    "goal": goal,
                    "repo": meta.get("repo"),
                    "ext_id": meta.get("ext_id"),
                    "status": n.get("status"),
                    "created_at": n.get("created_at"),
                    "actual_start": n.get("actual_start"),
                    "actual_end": n.get("actual_end"),
                }
            )
    nodes.sort(key=lambda x: x["node_id"])
    return nodes


def fetch_missions(client: ApiClient) -> dict[str, Any]:
    """全量分页拉 mission 的**紧凑成本证据**（预算上限 + 起止时间 + 状态）。"""
    out: dict[str, dict[str, Any]] = {}
    offset = 0
    total = None
    while True:
        doc = client.get("/tensor/missions", limit=MISSION_PAGE, offset=offset)
        items = doc.get("items") or []
        total = doc.get("total", total)
        for m in items:
            mid = int(m["id"])
            budget = m.get("budget") or {}
            out[str(mid)] = {
                "mission_id": mid,
                "status": m.get("status"),
                "budget": {
                    "max_wall_seconds": budget.get("max_wall_seconds"),
                    "max_tokens": budget.get("max_tokens"),
                    "max_tool_calls": budget.get("max_tool_calls"),
                },
                "created_at": m.get("created_at"),
                "finished_at": m.get("finished_at"),
                "heartbeat_at": m.get("heartbeat_at"),
                "campaign_id": m.get("campaign_id"),
                "campaign_phase": m.get("campaign_phase"),
            }
        offset += MISSION_PAGE
        if not items or (total is not None and offset >= int(total)):
            break
    return {"total": total, "items": {k: out[k] for k in sorted(out, key=lambda s: int(s))}}


def build_snapshot(cfg: RedundancyConfig, client: ApiClient | None = None) -> dict[str, Any]:
    client = client or ApiClient()
    plans, plan_coverage = fetch_plans(client)
    nodes = fetch_nodes(client, plans)
    missions = fetch_missions(client)
    snapshot = {
        "schema": "redundancy-audit-snapshot/1",
        "generator": {"module": "tools.redundancy_audit.collect", "api_base": client.base},
        "plans": plans,
        "nodes": nodes,
        "missions": missions["items"],
        "coverage": {
            **plan_coverage,
            "nodes_indexed": len(nodes),
            "nodes_with_goal": sum(1 for n in nodes if n["goal"]),
            "nodes_with_mission": sum(1 for n in nodes if n.get("mission_id")),
            "missions_indexed": len(missions["items"]),
            "missions_pruned_to_referenced": True,
            "missions_total_reported": missions["total"],
            "api_calls": client.calls,
        },
    }
    return snapshot


NODE_DERIVED_FIELDS = ("text", "metric_names", "deliverables")


def expand_nodes(snapshot: Mapping[str, Any]) -> dict[str, Any]:
    """把节点派生字段（text / metric_names / deliverables）补出来。

    快照只存原始字段（name/goal/…），派生量在读取时重算：既让落盘体积降到 1/3，又保证
    「派生逻辑改了 → 报告会跟着变」，不留下与代码口径不一致的陈旧派生列。
    """
    out = dict(snapshot)
    nodes = []
    for n in snapshot["nodes"]:
        m = dict(n)
        m["text"] = f"{m.get('name') or ''}\n{m.get('goal') or ''}".strip()
        m["metric_names"] = _criteria_names({"acceptance_criteria": (m.get("acceptance_criteria")
                                                                     or n.get("acceptance_criteria"))})
        m["deliverables"] = _deliverable_paths(m.get("goal") or "")
        nodes.append(m)
    out["nodes"] = nodes
    return out


def load_snapshot(cfg: "RedundancyConfig") -> dict[str, Any]:
    """读快照并补齐派生字段（报告的唯一入口）。"""
    return expand_nodes(read_json(snapshot_path(cfg)))


def prune_snapshot(snapshot: dict[str, Any]) -> dict[str, Any]:
    """只保留被节点引用的 mission 成本证据（其余 mission 与账本无关，落盘会白占十几 MB）。"""
    referenced = {str(n["mission_id"]) for n in snapshot["nodes"] if n.get("mission_id") is not None}
    missions = snapshot.get("missions") or {}
    kept = {k: v for k, v in missions.items() if k in referenced}
    snapshot = dict(snapshot)
    snapshot["missions"] = kept
    snapshot["nodes"] = [{k: v for k, v in n.items() if k not in NODE_DERIVED_FIELDS}
                         for n in snapshot["nodes"]]
    coverage = dict(snapshot.get("coverage") or {})
    coverage["missions_indexed"] = len(kept)
    coverage["missions_dropped_unreferenced"] = len(missions) - len(kept)
    coverage["missions_total_reported"] = coverage.get("missions_total_reported")
    snapshot["coverage"] = coverage
    return snapshot


# ── 落盘 / 读回（确定性序列化） ─────────────────────────────────────────
def dumps_deterministic(doc: Any) -> str:
    """报告/快照的唯一序列化口径：键排序 + 紧凑分隔符 + 保留中文。"""
    return json.dumps(doc, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def sha256_of(doc: Any) -> str:
    return hashlib.sha256(dumps_deterministic(doc).encode("utf-8")).hexdigest()


def write_json(path, doc: Any) -> str:
    from pathlib import Path

    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(doc, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    p.write_text(text, encoding="utf-8")
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def read_json(path) -> Any:
    from pathlib import Path

    return json.loads(Path(path).read_text(encoding="utf-8"))


def snapshot_path(cfg: RedundancyConfig):
    return repo_root() / str(cfg.get("paths.snapshot"))


def main(argv: list[str] | None = None) -> int:
    import argparse

    ap = argparse.ArgumentParser(description="采集冗余审计快照（只读 GET）")
    ap.add_argument("--config", default=None)
    ap.add_argument("--out", default=None)
    args = ap.parse_args(argv)
    cfg = RedundancyConfig.load(args.config)
    snap = prune_snapshot(build_snapshot(cfg))
    out = args.out or snapshot_path(cfg)
    digest = write_json(out, snap)
    print(json.dumps(
        {
            "snapshot": str(out),
            "sha256": digest,
            "plans": len(snap["plans"]),
            "nodes": len(snap["nodes"]),
            "missions": len(snap["missions"]),
            "api_calls": snap["coverage"]["api_calls"],
        },
        ensure_ascii=False,
    ))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())