"""阈值聚类 + 每个簇的治理建议（keep / merge / archive）。

聚类口径
--------
* **强冗余边**：score ≥ ``thresholds.strong``（默认 0.85）。
* **弱冗余边**：``thresholds.weak`` ≤ score < strong（默认 0.70-0.85）——不并簇，单列成
  「待复核对」，因为弱带里混着「同主题不同交付」的真难题（见报告劫难例）。
* 强边上的**连通分量**即一个冗余簇：冗余是传递关系（A≈B、B≈C 时三个一起治理），
  但簇内每条边的最弱分数记为 ``min_edge_score``，供人工按「链式传递风险」复核。

确定性
------
节点的遍历顺序来自快照（node_id 排序），连通分量用最小 node_id 代表；所有输出列表
按 (min_score desc, 代表 id) 排序，保证报告逐字节可复现。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Sequence

from tools.proposal_gate.textsim import SimResult

from .config import RedundancyConfig


@dataclass(frozen=True)
class Edge:
    a: str
    b: str
    score: float
    jaccard: float
    containment: float

    @classmethod
    def of(cls, key_a: str, key_b: str, res: SimResult) -> "Edge":
        a, b = (key_a, key_b) if key_a <= key_b else (key_b, key_a)
        return cls(a, b, round(res.score, 6), round(res.jaccard, 6), round(res.containment, 6))

    def as_dict(self) -> dict:
        return {"a": self.a, "b": self.b, "score": self.score,
                "jaccard": self.jaccard, "containment": self.containment}


class _UnionFind:
    def __init__(self) -> None:
        self.parent: dict[str, str] = {}

    def find(self, x: str) -> str:
        self.parent.setdefault(x, x)
        root = x
        while self.parent[root] != root:
            root = self.parent[root]
        while self.parent[x] != root:  # 路径压缩
            self.parent[x], x = root, self.parent[x]
        return root

    def union(self, a: str, b: str) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            # 以字典序小的为根（确定性）
            lo, hi = (ra, rb) if ra <= rb else (rb, ra)
            self.parent[hi] = lo


def split_edges(
    edges: Sequence[Edge], cfg: RedundancyConfig
) -> tuple[list[Edge], list[Edge]]:
    """强边 / 弱边（弱边 = 落在 [weak, strong) 的待复核对）。"""
    strong_t = float(cfg.get("thresholds.strong"))
    weak_t = float(cfg.get("thresholds.weak"))
    strong = [e for e in edges if e.score >= strong_t]
    weak = [e for e in edges if weak_t <= e.score < strong_t]
    strong.sort(key=lambda e: (-e.score, e.a, e.b))
    weak.sort(key=lambda e: (-e.score, e.a, e.b))
    return strong, weak


def components(strong_edges: Sequence[Edge], keys: Iterable[str]) -> list[list[str]]:
    uf = _UnionFind()
    for k in keys:
        uf.parent.setdefault(str(k), str(k))
    for e in strong_edges:
        uf.union(e.a, e.b)
    buckets: dict[str, list[str]] = {}
    for k in sorted(uf.parent):
        buckets.setdefault(uf.find(k), []).append(k)
    groups = [sorted(v) for v in buckets.values() if len(v) >= 2]
    groups.sort(key=lambda g: (-len(g), g[0]))
    return groups


def _status_rank(status: str | None, cfg: RedundancyConfig) -> int:
    order = list(cfg.get("ledger.status_evidence_priority"))
    norm = str(status or "").strip().lower()
    return order.index(norm) if norm in order else len(order)


def select_keeper(members: Sequence[dict], cfg: RedundancyConfig) -> str:
    """keeper = 证据优先级最高 → created_at 最早 → node_id 最小（确定性）。"""
    def key(m: dict):
        return (
            _status_rank(m.get("status"), cfg),
            str(m.get("created_at") or ""),
            str(m.get("node_id")),
        )

    return str(min(members, key=key)["node_id"])


def member_action(member: dict, keeper: str, cfg: RedundancyConfig, cost: dict) -> str:
    """非 keeper 的动作：零成本且未开工 → archive；否则 merge（证据并入 keeper，禁止重跑）。"""
    if str(member["node_id"]) == keeper:
        return "keep"
    archive_statuses = [str(s).strip().lower() for s in cfg.get("ledger.archive_statuses")]
    status = str(member.get("status") or "").strip().lower()
    spent = float(cost.get("wall_seconds_actual") or 0.0)
    if status in archive_statuses and spent == 0.0:
        return "archive"
    return "merge"


def build_clusters(
    strong_edges: Sequence[Edge],
    node_index: dict[str, dict],
    cfg: RedundancyConfig,
    costs: dict[str, dict],
) -> list[dict]:
    """簇结构 + 每簇成员成本/动作（成本由 ledger 层提供，保持一致口径）。"""
    groups = components(strong_edges, node_index.keys())
    edge_by_pair = {(e.a, e.b): e for e in strong_edges}
    out: list[dict] = []
    for idx, g in enumerate(groups, start=1):
        members = [node_index[k] for k in g]
        keeper = select_keeper(members, cfg)
        min_edge = None
        for i, a in enumerate(g):
            for b in g[i + 1:]:
                e = edge_by_pair.get((a, b))
                if e and (min_edge is None or e.score < min_edge.score):
                    min_edge = e
        rows = []
        for m in members:
            k = str(m["node_id"])
            c = costs.get(k, {})
            rows.append(
                {
                    "node_id": k,
                    "name": m.get("name"),
                    "plan_id": m.get("plan_id"),
                    "mission_id": m.get("mission_id"),
                    "ext_id": m.get("ext_id"),
                    "status": m.get("status"),
                    "action": member_action(m, keeper, cfg, c),
                    "cost": c,
                }
            )
        rows.sort(key=lambda r: (r["node_id"] != keeper, r["node_id"]))
        out.append(
            {
                "cluster_id": f"C{idx:03d}",
                "size": len(g),
                "keeper": keeper,
                "min_edge_score": round(min_edge.score, 6) if min_edge else None,
                "members": rows,
            }
        )
    out.sort(key=lambda c: (-(c["min_edge_score"] or 0.0), c["keeper"]))
    for i, c in enumerate(out, start=1):
        c["cluster_id"] = f"C{i:03d}"
    return out