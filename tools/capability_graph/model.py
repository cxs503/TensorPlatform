"""能力依赖图谱的数据模型：节点 / 边 / 快照 / 置信度映射。

零领域语义：本模块不含任何域词条、不含任何具体能力 id。节点 id、等级名、
置信度映射、置信带全部由 ``config/capability_graph.json`` 注入。

核心概念
--------
``own_confidence``
    节点自身证据强度（来自能力台账的声明等级经 ``level_confidence`` 映射）。
``edge weight``
    依赖强度 w∈(0,1]：上游能力退化时向下游传递的衰减比例。
``degradation``
    节点当次事件后承接到的**相对退化度** deg∈[0,1]（见 cascade.py）。
``effective_confidence``
    ``own_confidence * (1 - degradation)``，再经置信带映射回等级。

等级与置信度是两套坐标：等级是台账的离散裁决（可审计、可对账），置信度是
级联传播需要的连续量（可微、可比较）。两者之间的映射是显式配置，不是隐含约定。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

__all__ = [
    "LevelError",
    "ConfigError",
    "normalise_level",
    "rank_of",
    "confidence_of",
    "level_from_confidence",
    "Edge",
    "Node",
    "Snapshot",
    "edge_key",
]


class LevelError(ValueError):
    """等级值不在配置的 level_rank 里 —— 不猜，直接报。"""


class ConfigError(ValueError):
    """配置缺字段/结构不符。"""


def normalise_level(raw: Any, level_rank: Mapping[str, int]) -> str:
    """把外部等级字符串归一到大写；不认识就抛 LevelError（绝不猜成 WITHHELD）。"""
    if raw is None:
        raise LevelError("level is None")
    text = str(raw).strip().upper()
    if text not in level_rank:
        raise LevelError(f"unknown level: {raw!r}")
    return text


def rank_of(level: str, level_rank: Mapping[str, int]) -> int:
    try:
        return int(level_rank[level])
    except KeyError as exc:  # pragma: no cover - normalise_level 已挡
        raise LevelError(f"unknown level: {level!r}") from exc


def confidence_of(level: str, level_confidence: Mapping[str, float]) -> float:
    try:
        return float(level_confidence[level])
    except KeyError as exc:
        raise LevelError(f"no confidence mapping for level: {level!r}") from exc


def level_from_confidence(
    confidence: float,
    bands: Sequence[Mapping[str, Any]],
    *,
    own_level: Optional[str] = None,
    level_rank: Optional[Mapping[str, int]] = None,
    cap_by_own_level: bool = True,
) -> str:
    """置信度 → 等级（置信带按 min_confidence 降序匹配）。

    ``cap_by_own_level``：级联只能**降级**不能升级 —— 一个自身证据只有 MOCK 的
    能力，即使依赖全是 REAL，也不得因传播被抬到 REAL。这条是语义护栏，不是优化。
    """
    if not bands:
        raise ConfigError("confidence_bands 为空")
    ordered = sorted(bands, key=lambda b: float(b["min_confidence"]), reverse=True)
    inferred = str(ordered[-1]["level"])
    for band in ordered:
        if confidence >= float(band["min_confidence"]) - 1e-12:
            inferred = str(band["level"])
            break
    if not cap_by_own_level or own_level is None or level_rank is None:
        return inferred
    if rank_of(inferred, level_rank) > rank_of(own_level, level_rank):
        return own_level
    return inferred


def edge_key(src: str, dst: str) -> str:
    return f"{src}->{dst}"


@dataclass(frozen=True)
class Edge:
    """依赖边：``src`` 是上游（被依赖），``dst`` 是下游（依赖者）。"""

    src: str
    dst: str
    weight: float = 1.0
    sources: Tuple[str, ...] = ()
    corroborations: int = 0

    @property
    def key(self) -> str:
        return edge_key(self.src, self.dst)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "src": self.src,
            "dst": self.dst,
            "key": self.key,
            "weight": round(self.weight, 6),
            "sources": list(self.sources),
            "corroborations": self.corroborations,
        }


@dataclass
class Node:
    """能力节点：id 不透明，等级/置信度来自台账 + 配置映射。"""

    node_id: str
    own_level: str
    own_confidence: float
    role: str = "domain"
    label: str = ""
    level_source: str = "ledger"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.node_id,
            "role": self.role,
            "label": self.label,
            "own_level": self.own_level,
            "own_confidence": round(self.own_confidence, 6),
            "level_source": self.level_source,
        }


@dataclass
class Snapshot:
    """某一时刻的依赖图谱快照（不可变语义：改动请造新快照）。"""

    nodes: Dict[str, Node]
    edges: List[Edge]
    meta: Dict[str, Any] = field(default_factory=dict)

    # ── 图结构 ────────────────────────────────────────────────
    def adjacency(self) -> Dict[str, List[str]]:
        """上游 → 下游邻接表（确定性排序）。"""
        adj: Dict[str, List[str]] = {n: [] for n in self.nodes}
        for e in self.edges:
            adj.setdefault(e.src, [])
            if e.dst in self.nodes:
                adj.setdefault(e.dst, [])
            adj[e.src].append(e.dst)
        return {k: sorted(set(v)) for k, v in adj.items()}

    def reverse_adjacency(self) -> Dict[str, List[str]]:
        rev: Dict[str, List[str]] = {n: [] for n in self.nodes}
        for e in self.edges:
            rev.setdefault(e.dst, []).append(e.src)
        return {k: sorted(set(v)) for k, v in rev.items()}

    def dependencies(self, node_id: str) -> List[str]:
        """``node_id`` 直接依赖的上游列表。"""
        return sorted({e.src for e in self.edges if e.dst == node_id})

    def edge_map(self) -> Dict[str, Edge]:
        return {e.key: e for e in self.edges}

    def weight(self, src: str, dst: str, default: float = 1.0) -> float:
        for e in self.edges:
            if e.src == src and e.dst == dst:
                return e.weight
        return default

    def levels(self) -> Dict[str, str]:
        return {n: node.own_level for n, node in self.nodes.items()}

    def confidences(self) -> Dict[str, float]:
        return {n: node.own_confidence for n, node in self.nodes.items()}

    def with_levels(self, levels: Mapping[str, str], level_confidence: Mapping[str, float]) -> "Snapshot":
        """返回替换了部分节点自身等级的**新**快照（用于事件模拟，不改原快照）。"""
        nodes = dict(self.nodes)
        for node_id, level in levels.items():
            if node_id not in nodes:
                raise KeyError(f"unknown node: {node_id}")
            old = nodes[node_id]
            nodes[node_id] = Node(
                node_id=old.node_id,
                own_level=level,
                own_confidence=confidence_of(level, level_confidence),
                role=old.role,
                label=old.label,
                level_source="event",
            )
        meta = dict(self.meta)
        meta["derived_from"] = self.meta.get("generated_at")
        return Snapshot(nodes=nodes, edges=list(self.edges), meta=meta)

    # ── 报告 ──────────────────────────────────────────────────
    def summary(self) -> Dict[str, Any]:
        indeg: Dict[str, int] = {n: 0 for n in self.nodes}
        outdeg: Dict[str, int] = {n: 0 for n in self.nodes}
        for e in self.edges:
            if e.dst in indeg:
                indeg[e.dst] += 1
            if e.src in outdeg:
                outdeg[e.src] += 1
        by_level: Dict[str, int] = {}
        for node in self.nodes.values():
            by_level[node.own_level] = by_level.get(node.own_level, 0) + 1
        edge_sources: Dict[str, int] = {}
        for e in self.edges:
            for s in e.sources:
                edge_sources[s] = edge_sources.get(s, 0) + 1
        return {
            "node_count": len(self.nodes),
            "edge_count": len(self.edges),
            "edge_source_counts": dict(sorted(edge_sources.items())),
            "level_histogram": dict(sorted(by_level.items())),
            "roots": sorted(n for n, d in indeg.items() if d == 0),
            "leaves": sorted(n for n, d in outdeg.items() if d == 0),
            "isolated": sorted(n for n in self.nodes if indeg[n] == 0 and outdeg[n] == 0),
        }

    def to_dict(self) -> Dict[str, Any]:
        return {
            "generated_at": self.meta.get("generated_at"),
            "sources": self.meta.get("sources", {}),
            "summary": self.summary(),
            "nodes": [self.nodes[n].to_dict() for n in sorted(self.nodes)],
            "edges": [e.to_dict() for e in sorted(self.edges, key=lambda e: e.key)],
        }


def dedupe_edges(candidates: Iterable[Edge]) -> List[Edge]:
    """按 (src,dst) 合并边：权重取最大，来源求并集，佐证数累加。"""
    merged: Dict[str, Edge] = {}
    for e in candidates:
        if e.src == e.dst:  # 自环不参与级联（自身的退化由 own_confidence 承担）
            continue
        prev = merged.get(e.key)
        if prev is None:
            merged[e.key] = Edge(
                src=e.src, dst=e.dst, weight=e.weight,
                sources=tuple(sorted(set(e.sources))), corroborations=e.corroborations,
            )
            continue
        merged[e.key] = Edge(
            src=e.src,
            dst=e.dst,
            weight=max(prev.weight, e.weight),
            sources=tuple(sorted(set(prev.sources) | set(e.sources))),
            corroborations=prev.corroborations + e.corroborations,
        )
    return [merged[k] for k in sorted(merged)]