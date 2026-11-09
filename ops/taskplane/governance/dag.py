#!/usr/bin/env python3
"""战役 DAG 校验（campaign DAG acyclicity）——依赖归一 + 只引用本 plan + 拓扑排序 + 环路径。

**要防的东西**：仲裁者播种的节点依赖数组可能（a）引用别的 plan 的节点 id（跨计划
幽灵依赖，会让派发器按别人的状态解锁）；（b）形成环（互相等待，战役永不收敛）；
（c）自指或重复 id。这些在 SQL 里没有约束，只能在播种时机器校验。

本模块把三件事做成**纯函数**（不碰 DB，节点表由调用方转成 dict 列表）：

1. ``normalize_nodes``：节点 id/依赖/里程碑/元数据归一（依赖支持 list 或 PG 的 ``{1,2}``
   文本两种形态），重复 id 显式报出；
2. ``check_dag``：Kahn 拓扑排序（就绪队列按 id 升序 → 输出确定），残留节点 = 环集合，
   再用 DFS 把每个环**展开成可读路径**（如 ``3→5→3``）进报告，派发器可直接抄进拒收理由；
   同时报「依赖引用本 plan 外节点」与自指；
3. ``topological_order``：合规 DAG 的执行序（前沿节点按 id 升序，确定性），供派发器/仪表用。

确定性：全部循环只用排序后的序列，同一输入连跑两次逐字节相同。

零领域语义：只有节点 id / 依赖 / 标签。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Mapping, Optional, Sequence, Set, Tuple

VERSION = "in99-n10-v1"


def _as_dep_list(raw: object) -> List[int]:
    """依赖归一：None / list / tuple / "{1,2}" / "1,2" / 单值 都转成 int 列表。"""
    if raw is None:
        return []
    if isinstance(raw, bool):
        raise ValueError(f"依赖不能是 bool：{raw!r}")
    if isinstance(raw, int):
        return [raw]
    if isinstance(raw, str):
        text = raw.strip()
        if not text or text in ("{}", "[]", "None", "null"):
            return []
        if text.startswith("{") and text.endswith("}"):
            text = text[1:-1]
        elif text.startswith("[") and text.endswith("]"):
            text = text[1:-1]
        parts = [p.strip().strip("'\"") for p in text.split(",")]
        return [int(p) for p in parts if p not in ("", "None")]
    if isinstance(raw, Iterable):
        out: List[int] = []
        for item in raw:                      # 元素可能是 "{2}" / "2" / 2，递归归一
            out.extend(_as_dep_list(item))
        return out
    raise ValueError(f"无法解析依赖：{raw!r}")


def _node_id(node: Mapping[str, object]) -> int:
    for key in ("node_id", "id"):
        if key in node and node[key] is not None:
            return int(node[key])  # type: ignore[arg-type]
    raise ValueError("节点缺 node_id/id")


@dataclass
class CampaignNode:
    node_id: int
    dependencies: List[int] = field(default_factory=list)
    milestone_id: Optional[str] = None
    metadata: Dict[str, object] = field(default_factory=dict)

    @property
    def ext_id(self) -> str:
        return str(self.metadata.get("ext_id") or "")

    @property
    def repo(self) -> str:
        return str(self.metadata.get("repo") or "")

    @property
    def goal(self) -> str:
        return str(self.metadata.get("goal") or "")


def normalize_nodes(nodes: Sequence[Mapping[str, object]]) -> Tuple[List[CampaignNode], List[str]]:
    """归一节点；返回 (节点列表, 结构性问题列表)。结构性问题=重复 id / 缺 id / 依赖不可解析。"""
    out: List[CampaignNode] = []
    problems: List[str] = []
    seen: Set[int] = set()
    for node in nodes:
        try:
            nid = _node_id(node)
        except (ValueError, TypeError) as exc:
            problems.append(f"节点 id 不可解析: {exc}")
            continue
        if nid in seen:
            problems.append(f"节点 id 重复: {nid}")
            continue
        seen.add(nid)
        raw_deps = node.get("dependencies", node.get("deps", []))
        try:
            deps = _as_dep_list(raw_deps)
        except (ValueError, TypeError) as exc:
            problems.append(f"节点 {nid} 依赖不可解析({raw_deps!r}): {exc}")
            deps = []
        io = node.get("metadata") or {}
        if not isinstance(io, Mapping):
            problems.append(f"节点 {nid} metadata 非映射")
            io = {}
        ms = node.get("milestone_id", node.get("milestone"))
        out.append(CampaignNode(nid, deps, None if ms is None else str(ms), dict(io)))
    out.sort(key=lambda n: n.node_id)
    return out, problems


def adjacency(nodes: Sequence[CampaignNode]) -> Dict[int, List[int]]:
    """邻接表：只保留指向本 plan 内节点的依赖（外引用单独报）。"""
    ids = {n.node_id for n in nodes}
    return {n.node_id: sorted({d for d in n.dependencies if d in ids}) for n in nodes}


def out_of_plan_refs(nodes: Sequence[CampaignNode]) -> Dict[int, List[int]]:
    ids = {n.node_id for n in nodes}
    return {n.node_id: sorted({d for d in n.dependencies if d not in ids})
            for n in nodes if any(d not in ids for d in n.dependencies)}


def self_loops(nodes: Sequence[CampaignNode]) -> List[int]:
    return sorted(n.node_id for n in nodes if n.node_id in n.dependencies)


def kahn(nodes: Sequence[CampaignNode]) -> Tuple[List[int], List[int]]:
    """Kahn 拓扑排序。返回 (拓扑序, 残留节点=环上节点)。入队按 id 升序以保证确定性。"""
    adj = adjacency(nodes)
    indeg = {nid: len(deps) for nid, deps in adj.items()}
    ready = sorted([nid for nid, d in indeg.items() if d == 0])
    order: List[int] = []
    # 反向表：谁依赖了 cur（cur 出队后其下游入度 -1）
    rev: Dict[int, List[int]] = {nid: [] for nid in adj}
    for nid, deps in adj.items():
        for d in deps:
            rev[d].append(nid)
    while ready:
        cur = ready.pop(0)
        order.append(cur)
        for nxt in sorted(rev.get(cur, [])):
            indeg[nxt] -= 1
            if indeg[nxt] == 0:
                ready.append(nxt)
        ready.sort()
    residual = sorted(nid for nid, d in indeg.items() if d > 0)
    return order, residual


def find_cycles(nodes: Sequence[CampaignNode], residual: Optional[Sequence[int]] = None) -> List[List[int]]:
    """在残留节点（无入度 0 的核）上 DFS，展开每条显式环路径（起点最小的规范化）。"""
    adj = adjacency(nodes)
    pool = sorted(set(residual) if residual is not None else {n.node_id for n in nodes})
    index = {nid: i for i, nid in enumerate(pool)}
    color: Dict[int, int] = {nid: 0 for nid in pool}
    cycles: List[List[int]] = []
    seen_key: Set[Tuple[int, ...]] = set()

    def dfs(start: int, cur: int, stack: List[int]) -> None:
        color[cur] = 1
        stack.append(cur)
        for nxt in adj.get(cur, []):
            if nxt not in index:
                continue
            if color.get(nxt, 0) == 1:                     # 回边 → 找到环
                if nxt in stack:
                    cyc = stack[stack.index(nxt):] + [nxt]
                    # 规范化：以环内最小 id 起旋转（保持方向），去掉重复的尾节点
                    body = cyc[:-1]
                    k = body.index(min(body))
                    rot_body = body[k:] + body[:k]
                    rot = rot_body + [rot_body[0]]        # 闭合成环，起点=环内最小 id
                    key = tuple(rot)
                    if key not in seen_key:
                        seen_key.add(key)
                        cycles.append(rot)
            elif color.get(nxt, 0) == 0:
                dfs(start, nxt, stack)
        stack.pop()
        color[cur] = 2

    for nid in pool:
        if color[nid] == 0:
            dfs(nid, nid, [])
    cycles.sort(key=lambda c: (len(c), c))
    return cycles


@dataclass
class DagReport:
    ok: bool
    order: List[int] = field(default_factory=list)
    cycles: List[List[int]] = field(default_factory=list)
    out_of_plan: Dict[int, List[int]] = field(default_factory=dict)
    self_loops: List[int] = field(default_factory=list)
    problems: List[str] = field(default_factory=list)
    n_nodes: int = 0

    def reasons(self) -> List[str]:
        out = list(self.problems)
        if self.self_loops:
            out.append(f"自指依赖: {self.self_loops}")
        if self.out_of_plan:
            out.append("dependencies 引用 plan 外节点: " +
                       "; ".join(f"{k}->{v}" for k, v in sorted(self.out_of_plan.items())))
        if self.cycles:
            out.append("DAG 有环: " + "; ".join("→".join(str(x) for x in c) for c in self.cycles))
        return out

    def to_dict(self) -> dict:
        return {
            "ok": self.ok,
            "n_nodes": self.n_nodes,
            "order": list(self.order),
            "cycles": [list(c) for c in self.cycles],
            "cycle_paths": ["→".join(str(x) for x in c) for c in self.cycles],
            "out_of_plan": {str(k): v for k, v in sorted(self.out_of_plan.items())},
            "self_loops": list(self.self_loops),
            "problems": list(self.problems),
            "reasons": self.reasons(),
        }


def check_dag(nodes: Sequence[Mapping[str, object]]) -> DagReport:
    """完整 DAG 校验：结构问题 + 外引用 + 自指 + 环。"""
    norm, problems = normalize_nodes(nodes)
    order, residual = kahn(norm)
    cycles = find_cycles(norm, residual) if residual else []
    out_of = out_of_plan_refs(norm)
    loops = self_loops(norm)
    ok = not (problems or out_of or loops or cycles)
    return DagReport(ok=ok, order=order if ok else [], cycles=cycles, out_of_plan=out_of,
                     self_loops=loops, problems=problems, n_nodes=len(norm))


def topological_order(nodes: Sequence[Mapping[str, object]]) -> List[int]:
    """合规 DAG 的确定性拓扑序；有环抛 ValueError（带环路径）。"""
    report = check_dag(nodes)
    if not report.ok:
        raise ValueError("DAG 不合规: " + "; ".join(report.reasons()))
    return report.order


def frontier(nodes: Sequence[CampaignNode], done: Set[int]) -> List[int]:
    """当前可派发前沿：依赖全 done 且自身未 done（确定性升序）。"""
    adj = adjacency(nodes)
    return sorted(n.node_id for n in nodes
                  if n.node_id not in done and all(d in done for d in adj.get(n.node_id, [])))