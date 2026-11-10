"""纯函数 DAG 层 + 配置装载（无 IO、无领域语义、可独立单测）。

节点代价单位统一为秒；一个节点 = 一段必须在某个 worker 上串行执行的工作。
图算法全部基于 ``dependencies``（前驱）与由此导出的 children 邻接表。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG_PATH = REPO_ROOT / "config" / "campaign_optimizer.v1.json"


# --------------------------------------------------------------------------- #
# 配置
# --------------------------------------------------------------------------- #
class ConfigError(ValueError):
    """配置缺字段 / 取值非法。"""


def load_config(path: Optional[str | Path] = None) -> Dict[str, Any]:
    p = Path(path) if path else DEFAULT_CONFIG_PATH
    if not p.exists():
        raise ConfigError(f"config not found: {p}")
    data = json.loads(p.read_text(encoding="utf-8"))
    required = ("status_vocab", "detect", "rootcause", "schedule", "backtest")
    missing = [k for k in required if k not in data]
    if missing:
        raise ConfigError(f"config missing sections: {missing}")
    for key in ("satisfied", "blocking", "active"):
        if key not in data["status_vocab"]:
            raise ConfigError(f"status_vocab missing {key}")
    return data


# --------------------------------------------------------------------------- #
# 节点 / 图
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class Node:
    node_id: int
    plan_id: int
    status: str
    node_type: str = "task"
    mission_id: Optional[int] = None
    dependencies: Tuple[int, ...] = ()
    consecutive_fails: int = 0
    duration_s: float = 0.0
    # 结构化任务元数据（状态机字段，非领域语义）
    mission_status: str = ""
    mission_has_result: bool = False
    updated_at: str = ""
    # 证据字段（只写产物，不参与判定）
    name: str = ""
    reason_text: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "node_id": self.node_id,
            "plan_id": self.plan_id,
            "status": self.status,
            "node_type": self.node_type,
            "mission_id": self.mission_id,
            "dependencies": list(self.dependencies),
            "consecutive_fails": self.consecutive_fails,
            "duration_s": round(self.duration_s, 3),
            "mission_status": self.mission_status,
        }


@dataclass
class Graph:
    """单个 plan 的 DAG。"""

    plan_id: int
    nodes: Dict[int, Node] = field(default_factory=dict)
    satisfied_statuses: frozenset[str] = frozenset({"done"})
    blocking_statuses: frozenset[str] = frozenset({"blocked", "failed"})
    dangling_deps: Dict[int, Tuple[int, ...]] = field(default_factory=dict)

    # -- 构造 ------------------------------------------------------------- #
    @classmethod
    def build(
        cls,
        nodes: Iterable[Node],
        satisfied_statuses: Iterable[str],
        blocking_statuses: Iterable[str],
        dangling_policy: str = "drop",
    ) -> "Graph":
        node_map: Dict[int, Node] = {}
        plan_ids = set()
        for n in nodes:
            node_map[n.node_id] = n
            plan_ids.add(n.plan_id)
        if len(plan_ids) > 1:
            raise ValueError(f"graph mixes plan ids: {sorted(plan_ids)}")
        g = cls(
            plan_id=next(iter(plan_ids)) if plan_ids else 0,
            nodes=node_map,
            satisfied_statuses=frozenset(satisfied_statuses),
            blocking_statuses=frozenset(blocking_statuses),
        )
        missing: Dict[int, Tuple[int, ...]] = {}
        for nid, n in sorted(node_map.items()):
            bad = tuple(sorted(dep for dep in n.dependencies if dep not in node_map))
            if bad:
                missing[nid] = bad
        if missing:
            if dangling_policy == "raise":
                raise ValueError(f"dangling dependencies in plan {g.plan_id}: {missing}")
            if dangling_policy != "drop":
                raise ValueError(f"unknown dangling_policy: {dangling_policy!r}")
            # 跨 plan / 已删除节点的外引：从图内视角丢弃前驱边，但把事实记进审计字段。
            g.dangling_deps = missing
            g.nodes = {
                nid: replace(n, dependencies=tuple(d for d in n.dependencies if d in node_map))
                for nid, n in node_map.items()
            }
        return g

    # -- 状态谓词 ---------------------------------------------------------- #
    def is_satisfied(self, node_id: int) -> bool:
        return self.nodes[node_id].status in self.satisfied_statuses

    def is_blocking(self, node_id: int) -> bool:
        return self.nodes[node_id].status in self.blocking_statuses

    def remaining(self) -> List[int]:
        return [nid for nid in self.sorted_ids() if not self.is_satisfied(nid)]

    def sorted_ids(self) -> List[int]:
        return sorted(self.nodes)

    # -- 拓扑 -------------------------------------------------------------- #
    def children_map(self) -> Dict[int, List[int]]:
        ch: Dict[int, List[int]] = {nid: [] for nid in self.nodes}
        for nid, node in self.nodes.items():
            for dep in node.dependencies:
                ch[dep].append(nid)
        for v in ch.values():
            v.sort()
        return ch

    def topo_order(self) -> List[int]:
        indeg = {nid: len(set(n.dependencies)) for nid, n in self.nodes.items()}
        ch = self.children_map()
        ready = sorted([nid for nid, d in indeg.items() if d == 0])
        out: List[int] = []
        while ready:
            nid = ready.pop(0)
            out.append(nid)
            for c in ch[nid]:
                indeg[c] -= 1
                if indeg[c] == 0:
                    ready.append(c)
            ready.sort()
        if len(out) != len(self.nodes):
            raise ValueError(f"cycle detected in plan {self.plan_id}")
        return out

    def downstream_closure(self, node_id: int) -> List[int]:
        """全部传递后继（不含自身），升序。"""
        ch = self.children_map()
        seen: set[int] = set()
        stack = list(ch[node_id])
        while stack:
            cur = stack.pop()
            if cur in seen:
                continue
            seen.add(cur)
            stack.extend(ch[cur])
        return sorted(seen)

    def upstream_closure(self, node_id: int) -> List[int]:
        seen: set[int] = set()
        stack = list(self.nodes[node_id].dependencies)
        while stack:
            cur = stack.pop()
            if cur in seen:
                continue
            seen.add(cur)
            stack.extend(self.nodes[cur].dependencies)
        return sorted(seen)

    # -- 关键路径 ---------------------------------------------------------- #
    def longest_paths(self, weights: Mapping[int, float]) -> Tuple[float, Dict[int, float]]:
        """返回 (最长路径长度, 每个节点为终点的最长路径长度 es)。

        权重为节点自身工期（秒）；节点串行执行语义下 makespan 下界 = 最长链权重和。
        """
        es: Dict[int, float] = {}
        for nid in self.topo_order():
            node = self.nodes[nid]
            w = float(weights.get(nid, 0.0))
            es[nid] = w + max((es[d] for d in node.dependencies), default=0.0)
        return (max(es.values()) if es else 0.0), es

    def critical_path(self, weights: Mapping[int, float]) -> List[int]:
        """一条最长链（按 es 回溯，确定性 tie-break：取 node_id 最小者）。"""
        length, es = self.longest_paths(weights)
        if not self.nodes:
            return []
        end = min(nid for nid, v in es.items() if abs(v - length) <= 1e-9)
        path = [end]
        cur = end
        while self.nodes[cur].dependencies:
            target = es[cur] - float(weights.get(cur, 0.0))
            cands = [d for d in self.nodes[cur].dependencies if abs(es[d] - target) <= 1e-9]
            if not cands:
                break
            cur = min(cands)
            path.append(cur)
        return list(reversed(path))

    def total_float(self, weights: Mapping[int, float]) -> Dict[int, float]:
        """总浮动 = 最长链长度 − 经过该节点的最长链长度。0 表示在关键路径上。"""
        length, es = self.longest_paths(weights)
        ch = self.children_map()
        # 反向最长路径：从节点到汇点的最长权重和（含自身）
        order = list(reversed(self.topo_order()))
        tail: Dict[int, float] = {}
        for nid in order:
            w = float(weights.get(nid, 0.0))
            tail[nid] = w + max((tail[c] for c in ch[nid]), default=0.0)
        out: Dict[int, float] = {}
        for nid in self.nodes:
            through = es[nid] + tail[nid] - float(weights.get(nid, 0.0))
            out[nid] = max(0.0, length - through)
        return out

    def downstream_weight(self, node_id: int, weights: Mapping[int, float]) -> float:
        return float(sum(weights.get(d, 0.0) for d in self.downstream_closure(node_id)))

    def gates_plan_completion(self, node_id: int) -> bool:
        """节点未满足即 plan 无法完成（plan 完成 = 全部节点 satisfied）。"""
        return not self.is_satisfied(node_id)

    def summary(self) -> Dict[str, Any]:
        counts: Dict[str, int] = {}
        for n in self.nodes.values():
            counts[n.status] = counts.get(n.status, 0) + 1
        return {
            "plan_id": self.plan_id,
            "n_nodes": len(self.nodes),
            "n_edges": sum(len(n.dependencies) for n in self.nodes.values()),
            "status_counts": dict(sorted(counts.items())),
            "n_blocking": sum(1 for nid in self.nodes if self.is_blocking(nid)),
            "n_dangling_dep_nodes": len(self.dangling_deps),
        }


def graphs_from_nodes(
    nodes: Sequence[Node], satisfied_statuses: Iterable[str], blocking_statuses: Iterable[str]
) -> Dict[int, Graph]:
    by_plan: Dict[int, List[Node]] = {}
    for n in nodes:
        by_plan.setdefault(n.plan_id, []).append(n)
    return {
        pid: Graph.build(ns, satisfied_statuses, blocking_statuses)
        for pid, ns in sorted(by_plan.items())
    }