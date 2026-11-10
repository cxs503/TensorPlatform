#!/usr/bin/env python3
"""等待图死锁检测：Tarjan 强连通分量 + 打破策略（TTL 最早到期者）。

纯函数，无 IO，可用合成图直接验证（D2 的 15 例即建立在这套函数上）。
"""
from __future__ import annotations

from . import model


def tarjan_sccs(graph: dict[str, list[str]]) -> list[list[str]]:
    """Tarjan 强连通分量（迭代版，避免深递归爆栈）。

    ``graph`` 缺键按无边处理；边指向的节点会被自动补成节点。
    """
    nodes: list[str] = []
    seen: set[str] = set()
    for u in graph:
        if u not in seen:
            seen.add(u)
            nodes.append(u)
        for v in graph[u]:
            if v not in seen:
                seen.add(v)
                nodes.append(v)
    index: dict[str, int] = {}
    low: dict[str, int] = {}
    on_stack: set[str] = set()
    stack: list[str] = []
    sccs: list[list[str]] = []
    counter = 0
    for root in nodes:
        if root in index:
            continue
        work: list[tuple[str, int]] = [(root, 0)]
        while work:
            node, pi = work[-1]
            if pi == 0:
                index[node] = low[node] = counter
                counter += 1
                stack.append(node)
                on_stack.add(node)
            neighbours = graph.get(node, [])
            if pi < len(neighbours):
                work[-1] = (node, pi + 1)
                nxt = neighbours[pi]
                if nxt not in index:
                    work.append((nxt, 0))
                elif nxt in on_stack:
                    low[node] = min(low[node], index[nxt])
            else:
                work.pop()
                if work:
                    parent = work[-1][0]
                    low[parent] = min(low[parent], low[node])
                if low[node] == index[node]:
                    comp = []
                    while True:
                        w = stack.pop()
                        on_stack.discard(w)
                        comp.append(w)
                        if w == node:
                            break
                    sccs.append(comp)
    return sccs


def find_cycles(graph: dict[str, list[str]]) -> list[list[str]]:
    """返回所有「真环」：SCC 大小 > 1，或大小 1 但带自环。"""
    cycles = []
    for comp in tarjan_sccs(graph):
        if len(comp) > 1:
            cycles.append(sorted(comp))
        elif comp[0] in graph.get(comp[0], []):
            cycles.append([comp[0]])
    return sorted(cycles)


def cycle_containing(graph: dict[str, list[str]], node: str) -> list[str] | None:
    for comp in tarjan_sccs(graph):
        if node in comp and (len(comp) > 1 or node in graph.get(node, [])):
            return sorted(comp)
    return None


def choose_victim(cycle: list[str], expiry_of) -> dict:
    """打破策略：TTL 最早到期者优先；同时刻按 lease_id 升序（确定性）。"""
    ranked = sorted(cycle, key=lambda lid: (float(expiry_of(lid)), lid))
    return {
        "victim": ranked[0],
        "rule": "earliest_ttl_expiry",
        "ranked": ranked,
        "expiry": {lid: float(expiry_of(lid)) for lid in ranked},
    }


def is_self_cycle(cycle: list[str]) -> bool:
    return len(cycle) == 1


def classify(cycle: list[str]) -> str:
    """环长度分类（2 环 / 4 环 / 自环 / 其他），报告用合成标签。"""
    if len(cycle) == 1:
        return "self"
    return f"{len(cycle)}-cycle"


def detect(graph: dict[str, list[str]], waiter: str | None = None, expiry_of=None) -> dict | None:
    """检出死锁。

    * ``waiter`` 给定 → 只判断该节点是否在环里（等锁者的本地判定）；
    * 否则返回图中任一环（全局巡检）；
    * ``expiry_of`` 给定 → 同时给出打破方案（victim）。
    """
    cycles = find_cycles(graph)
    if not cycles:
        return None
    target = None
    if waiter is not None:
        target = cycle_containing(graph, waiter)
        if target is None:
            return None
    else:
        target = cycles[0]
    out = {
        "schema": model.SCHEMA + ".deadlock",
        "cycle": target,
        "kind": classify(target),
        "graph": graph,
        "cycles_total": len(cycles),
    }
    if expiry_of is not None:
        out["resolution"] = (
            {"victim": target[0], "rule": "reentrant_grant", "ranked": target,
             "expiry": {lid: float(expiry_of(lid)) for lid in target}}
            if is_self_cycle(target)
            else choose_victim(target, expiry_of)
        )
    out["digest"] = model.digest({k: v for k, v in out.items() if k != "digest"})
    return out