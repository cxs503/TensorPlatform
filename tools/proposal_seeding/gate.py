"""机器门：计划 DAG 的硬校验（全部由配置驱动，一项不过即 ``all_passed=False``）。

七项硬校验
----------
1. ``node_count_range``   —— 节点数落在 [node_count_min, node_count_max]；
2. ``ext_id_unique_intra``—— 计划内 ext_id 唯一；
3. ``ext_id_unique_global``—— 与「全库既有 ext_id 宇宙」无碰撞（可排除本计划自身，
   用于「原地重播种」场景）；
4. ``repo_whitelist``     —— 每个节点仓都在白名单内；
5. ``dag_acyclic``        —— Kahn 拓扑排序可消费全部节点；
6. ``deps_within_plan``   —— 依赖只引用本计划节点，且不允许自依赖；
7. ``milestone_coverage`` —— 每个里程碑 ≥1 节点，且节点所属里程碑存在；
8. ``maturity_count``     —— 成熟化单数 ≥ ``min_maturity_nodes``（标记词来自配置）。

门是**独立复核**，不是播种器的自我声明：播种器构造性无环，门仍然真跑一次拓扑排序，
因此手工编辑 plan（注入环 / 越界依赖 / 幻觉 ext_id）一定会被抓住（见 pytest 用例）。
"""

from __future__ import annotations

from typing import Any, Iterable, Mapping, Sequence

from .config import SeedingConfig
from .model import PlanNode

SCHEMA = "proposal-seeding-gate/1.0"


def _nodes(plan: Mapping[str, Any]) -> list[PlanNode]:
    out: list[PlanNode] = []
    for raw in plan.get("nodes", []):
        out.append(raw if isinstance(raw, PlanNode) else PlanNode.from_dict(raw))
    return out


def topological_order(nodes: Sequence[PlanNode]) -> tuple[list[int], list[int]]:
    """Kahn：返回 ``(order, cycle_nodes)``；``cycle_nodes`` 非空即存在环。"""
    index_set = {n.index for n in nodes}
    indeg = {n.index: 0 for n in nodes}
    succ: dict[int, list[int]] = {n.index: [] for n in nodes}
    for n in nodes:
        for dep in n.dependencies:
            if dep not in index_set:
                continue  # 由 deps_within_plan 单独判 fail
            succ[dep].append(n.index)
            indeg[n.index] += 1
    ready = sorted(i for i, d in indeg.items() if d == 0)
    order: list[int] = []
    while ready:
        cur = ready.pop(0)
        order.append(cur)
        for nxt in sorted(succ[cur]):
            indeg[nxt] -= 1
            if indeg[nxt] == 0:
                ready.append(nxt)
        ready.sort()
    cycle = sorted(i for i, d in indeg.items() if d > 0)
    return order, cycle


def check_plan(plan: Mapping[str, Any], cfg: SeedingConfig,
               *, global_ext_ids: Iterable[str] = (),
               exclude_ext_ids: Iterable[str] = ()) -> dict[str, Any]:
    nodes = _nodes(plan)
    checks: list[dict[str, Any]] = []

    def add(name: str, ok: bool, detail: str, **extra: Any) -> None:
        checks.append({"name": name, "ok": bool(ok), "detail": detail, **extra})

    # 1 node count -------------------------------------------------------
    n = len(nodes)
    add("node_count_range", cfg.plan.node_count_min <= n <= cfg.plan.node_count_max,
        f"节点数 {n} 需落在 [{cfg.plan.node_count_min}, {cfg.plan.node_count_max}]",
        value=n, expected=[cfg.plan.node_count_min, cfg.plan.node_count_max])

    # 2 ext_id intra -----------------------------------------------------
    ext_ids = [x.ext_id for x in nodes]
    dup = sorted({x for x in ext_ids if ext_ids.count(x) > 1})
    add("ext_id_unique_intra", not dup, f"计划内重复 ext_id: {dup or '无'}",
        duplicates=dup, value=len(set(ext_ids)))

    # 3 ext_id global ----------------------------------------------------
    exclude = set(exclude_ext_ids)
    universe = {str(x) for x in global_ext_ids if x} - exclude
    clashes = sorted(set(ext_ids) & universe)
    add("ext_id_unique_global", not clashes,
        f"与全库既有 ext_id 碰撞: {clashes or '无'}（比较宇宙 {len(universe)} 条，"
        f"已排除自身 {len(exclude)} 条）",
        clashes=clashes, universe_size=len(universe))

    # 4 repo whitelist ---------------------------------------------------
    bad_repo = sorted({x.repo for x in nodes if x.repo not in cfg.repo_whitelist})
    add("repo_whitelist", not bad_repo, f"白名单外仓: {bad_repo or '无'}",
        offending=bad_repo)

    # 5 dag acyclic ------------------------------------------------------
    order, cycle = topological_order(nodes)
    add("dag_acyclic", not cycle,
        f"拓扑序 {len(order)}/{n}；成环节点 {cycle or '无'}",
        order=order, cycle=cycle)

    # 6 deps within plan -------------------------------------------------
    index_set = {x.index for x in nodes}
    outside: list[dict[str, Any]] = []
    self_dep: list[int] = []
    for x in nodes:
        for dep in x.dependencies:
            if dep == x.index:
                self_dep.append(x.index)
            elif dep not in index_set:
                outside.append({"node": x.index, "dep": dep})
    add("deps_within_plan", not outside and not self_dep,
        f"越界依赖 {outside or '无'}；自依赖 {self_dep or '无'}",
        outside=outside, self_dep=self_dep)

    # 7 milestone coverage ----------------------------------------------
    declared = [int(m.get("index", i)) for i, m in enumerate(plan.get("milestones", []))]
    counts = {m: 0 for m in declared}
    for x in nodes:
        if x.milestone in counts:
            counts[x.milestone] += 1
    empty = sorted(m for m in declared if counts[m] == 0)
    phantom = sorted({x.milestone for x in nodes if x.milestone not in counts})
    # 声明侧一致性：milestones[].node_indices 与实际节点归属必须对得上
    inconsistent: list[dict[str, Any]] = []
    for i, m in enumerate(plan.get("milestones", [])):
        declared_idx = {int(m0) for m0 in m.get("node_indices", [])}
        actual_idx = {x.index for x in nodes if x.milestone == int(m.get("index", i))}
        if declared_idx != actual_idx:
            inconsistent.append({"milestone": int(m.get("index", i)),
                                 "declared": sorted(declared_idx),
                                 "actual": sorted(actual_idx)})
    add("milestone_coverage", bool(declared) and not empty and not phantom and not inconsistent,
        f"里程碑 {len(declared)} 个，空里程碑 {empty or '无'}，幻影里程碑 {phantom or '无'}，"
        f"声明不一致 {len(inconsistent)} 处",
        declared=declared, empty=empty, phantom=phantom, inconsistent=inconsistent)

    # 8 maturity count ---------------------------------------------------
    marker = cfg.maturity_marker
    maturity_nodes = [x.index for x in nodes
                      if marker and (marker in x.name or marker in x.goal or x.is_maturity)]
    add("maturity_count", len(maturity_nodes) >= cfg.plan.min_maturity_nodes,
        f"成熟化单 {len(maturity_nodes)} 条 ≥ {cfg.plan.min_maturity_nodes}"
        f"（节点 {maturity_nodes}）",
        value=len(maturity_nodes), nodes=maturity_nodes,
        expected_min=cfg.plan.min_maturity_nodes)

    failed = [c["name"] for c in checks if not c["ok"]]
    return {
        "schema": SCHEMA,
        "plan_id": str(plan.get("plan_id")),
        "wave": plan.get("wave"),
        "node_count": n,
        "checks": checks,
        "failed": failed,
        "all_passed": not failed,
        "topological_order": order,
        "maturity_nodes": maturity_nodes,
    }