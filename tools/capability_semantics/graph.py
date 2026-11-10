"""能力依赖图：依赖边（配置注入）→ 循环依赖检测、孤立节点、拓扑序、可视化导出。

依赖语义：``A depends_on B`` 读作「A 的可用性以 B 为前提」（B → A 有向边）。
图把三层结构串起来：依赖边的两端都是 capability 层节点，交付/证据仍挂在各自
能力下——依赖图只回答「谁能先算」，不重复回答「算得准不准」。

判据 (d) 口径：**循环依赖 = 0**；**孤立节点比例 < 5%**（默认在 ``graph_role=domain``
子图上计算，跨领域工具类节点单列披露，避免用工具节点稀释分母）。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping, Sequence

from .sources import SemanticsConfig


@dataclass
class DependencyGraph:
    nodes: list[str] = field(default_factory=list)
    edges: list[tuple[str, str]] = field(default_factory=list)  # (src=dep, dst=dependent)
    roles: dict[str, str] = field(default_factory=dict)
    missing_targets: list[dict[str, str]] = field(default_factory=list)
    self_loops: list[str] = field(default_factory=list)

    # ── 图算法 ──────────────────────────────────────────────
    def adjacency(self) -> dict[str, list[str]]:
        adj: dict[str, list[str]] = {n: [] for n in self.nodes}
        for src, dst in self.edges:
            adj.setdefault(src, []).append(dst)
        return {k: sorted(v) for k, v in adj.items()}

    def find_cycles(self) -> list[list[str]]:
        """DFS 三色法找环；返回每个环的节点序列（确定性顺序，去重）。"""
        adj = self.adjacency()
        WHITE, GREY, BLACK = 0, 1, 2
        colour: dict[str, int] = {n: WHITE for n in self.nodes}
        stack: list[str] = []
        cycles: list[list[str]] = []
        seen: set[tuple[str, ...]] = set()

        def dfs(node: str) -> None:
            colour[node] = GREY
            stack.append(node)
            for nxt in adj.get(node, []):
                if colour.get(nxt, WHITE) == WHITE:
                    dfs(nxt)
                elif colour.get(nxt) == GREY:
                    idx = stack.index(nxt)
                    cyc = stack[idx:]
                    key = tuple(sorted(cyc))
                    if key not in seen:
                        seen.add(key)
                        cycles.append(list(cyc))
            stack.pop()
            colour[node] = BLACK

        for n in sorted(self.nodes):
            if colour[n] == WHITE:
                dfs(n)
        return sorted(cycles, key=lambda c: (len(c), c))

    def degrees(self) -> dict[str, tuple[int, int]]:
        out_deg: dict[str, int] = {n: 0 for n in self.nodes}
        in_deg: dict[str, int] = {n: 0 for n in self.nodes}
        for src, dst in self.edges:
            if src not in in_deg or dst not in out_deg:
                continue
            in_deg[dst] += 1
            out_deg[src] += 1
        return {n: (in_deg[n], out_deg[n]) for n in self.nodes}

    def orphans(self, roles: Sequence[str] = ("domain",)) -> list[str]:
        """入度=出度=0 且在指定角色范围内的节点。"""
        deg = self.degrees()
        return sorted(n for n, (i, o) in deg.items()
                      if i == 0 and o == 0 and self.roles.get(n, "domain") in roles)

    def topo_order(self) -> list[str]:
        """Kahn 拓扑序（有环时返回已排出的前缀 + 剩余节点按名字排序）。"""
        deg = self.degrees()
        ready = sorted(n for n, (i, _o) in deg.items() if i == 0)
        adj = self.adjacency()
        order: list[str] = []
        while ready:
            n = ready.pop(0)
            order.append(n)
            for nxt in adj.get(n, []):
                if nxt not in deg:
                    continue
                i, o = deg[nxt]
                deg[nxt] = (i - 1, o)
                if i - 1 == 0:
                    ready.append(nxt)
            ready.sort()
        rest = sorted(set(self.nodes) - set(order))
        return order + rest

    def summary(self, orphan_ratio_max: float = 0.05) -> dict[str, Any]:
        cycles = self.find_cycles()
        domain_nodes = [n for n in self.nodes if self.roles.get(n, "domain") == "domain"]
        tooling_nodes = [n for n in self.nodes if self.roles.get(n, "domain") == "tooling"]
        orphans_domain = self.orphans(("domain",))
        orphans_all = self.orphans(("domain", "tooling"))
        ratio_domain = (len(orphans_domain) / len(domain_nodes)) if domain_nodes else 0.0
        ratio_all = (len(orphans_all) / len(self.nodes)) if self.nodes else 0.0
        return {
            "node_count": len(self.nodes),
            "edge_count": len(self.edges),
            "domain_node_count": len(domain_nodes),
            "tooling_node_count": len(tooling_nodes),
            "cycle_count": len(cycles),
            "cycles": cycles,
            "self_loops": list(self.self_loops),
            "missing_targets": list(self.missing_targets),
            "orphan_domain": orphans_domain,
            "orphan_all": orphans_all,
            "orphan_ratio_domain": round(ratio_domain, 4),
            "orphan_ratio_all_disclosed": round(ratio_all, 4),
            "orphan_ratio_max": orphan_ratio_max,
            "criterion_no_cycle_ok": len(cycles) == 0 and not self.self_loops,
            "criterion_orphan_ok": ratio_domain < orphan_ratio_max,
            "topological_order": self.topo_order(),
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            "nodes": list(self.nodes),
            "edges": [{"dep": s, "dependent": d} for s, d in self.edges],
            "roles": dict(self.roles),
        }

    def to_dot(self) -> str:
        lines = ["digraph capability_deps {", '  rankdir="LR";']
        for n in self.nodes:
            shape = "box" if self.roles.get(n) == "domain" else "ellipse"
            lines.append(f'  "{n}" [shape={shape}];')
        for src, dst in self.edges:
            lines.append(f'  "{src}" -> "{dst}";')
        lines.append("}")
        return "\n".join(lines) + "\n"

    def to_mermaid(self) -> str:
        lines = ["flowchart LR"]
        for n in self.nodes:
            lines.append(f'  n_{abs(hash(n)) % (10 ** 9)}["{n}"]')
        for src, dst in self.edges:
            lines.append(f'  n_{abs(hash(src)) % (10 ** 9)} --> n_{abs(hash(dst)) % (10 ** 9)}')
        return "\n".join(lines) + "\n"


def build_graph(cfg: SemanticsConfig, extra_nodes: Iterable[str] = ()) -> DependencyGraph:
    """从配置依赖边构图；缺目标/自环显式登记，不静默丢弃。"""
    nodes = [str(c.get("id")) for c in cfg.capabilities if c.get("id")]
    node_set = set(nodes) | {str(n) for n in extra_nodes}
    edges: list[tuple[str, str]] = []
    missing: list[dict[str, str]] = []
    self_loops: list[str] = []
    for c in cfg.capabilities:
        dst = str(c.get("id") or "")
        if not dst:
            continue
        for dep in c.get("depends_on") or []:
            src = str(dep)
            if src == dst:
                self_loops.append(dst)
                continue
            if src not in node_set:
                missing.append({"node": dst, "missing_dependency": src})
                node_set.add(src)
                continue
            edges.append((src, dst))
    roles = {str(c.get("id")): str(c.get("graph_role") or "domain")
             for c in cfg.capabilities if c.get("id")}
    for n in node_set - set(roles):
        roles[n] = "domain"
    return DependencyGraph(
        nodes=sorted(node_set),
        edges=sorted(set(edges)),
        roles=roles,
        missing_targets=sorted(missing, key=lambda m: (m["node"], m["missing_dependency"])),
        self_loops=sorted(set(self_loops)),
    )


__all__ = ["DependencyGraph", "build_graph"]