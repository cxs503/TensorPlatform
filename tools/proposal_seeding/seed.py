"""聚合项 → 战役 DAG 播种（campaign_plans / milestones / nodes）。

播种的五个决定（每个都能在报告里被复核）
----------------------------------------
1. **容量与预留**：``plan.node_count_max`` 是硬上界；``reserve_platform_slot`` 时预留 1 席
   给自回归增长产生的平台自优化节点，因此提案容量 = ``node_count_max - 1``。
   超容量项**不丢弃**，进 ``overflow``（反哺下一波，见 :mod:`autoregress`）。
2. **选择策略**：聚合项已按「成熟化单优先 → 仓名 → 内容摘要」排序，取前 capacity 个；
   完全确定性，同一输入必得同一集合。
3. **ext_id**：``{wave}-n{序号}-{slug}``。slug 取标题里的拉丁词（内容定址的降级用摘要前缀），
   序号来自排序后的位置 → 同一输入两次运行 ext_id 序列逐字节相同（幂等）。
4. **里程碑分配**：按节点顺序切成 ``milestone_count`` 个连续块（每块 ≥1 节点），平台节点
   落最后一个里程碑（它消费前面所有里程碑的遥测）。
5. **DAG 依赖**：里程碑内按执行顺序串联（``chain_within_milestone``）+ 提案自带的依赖提示
   消解到**更早的节点**上。因为只允许指向更早节点，构造性无环；机器门仍独立跑一次 Kahn
   拓扑排序复核（防止手工改 plan 引入环）。
"""

from __future__ import annotations

import re
from typing import Any, Mapping, Sequence

from .config import SeedingConfig
from .model import AggregatedItem, PlanNode, ascii_tokens, digest, sections_text

SCHEMA = "proposal-seeding-plan/1.0"


# ---------------------------------------------------------------------------
# ext_id
# ---------------------------------------------------------------------------
_MULTI_SEP = re.compile(r"[-_]{2,}")


def slugify(title: str, cfg: SeedingConfig) -> str:
    """标题 → ext_id slug（拉丁词；无拉丁词时退回内容摘要前缀）。"""
    tokens = [t for t in ascii_tokens(title)
              if len(t) >= cfg.ext_id.min_slug_token_length]
    if not tokens:
        return f"{cfg.ext_id.fallback_prefix}-{digest({'title': title})[:8]}"
    slug = "-".join(tokens[: cfg.ext_id.max_slug_tokens])
    return _MULTI_SEP.sub("-", slug).strip("-")


def build_ext_id(wave: str, index: int, title: str, cfg: SeedingConfig) -> str:
    return f"{wave}-n{index:0{cfg.ext_id.index_width}d}-{slugify(title, cfg)}"


# ---------------------------------------------------------------------------
# item → node
# ---------------------------------------------------------------------------
def render_goal(item: AggregatedItem) -> str:
    """分节渲染（节名原样保留，不解释语义）+ 判据清单。"""
    parts: list[str] = []
    for name in item.sections:
        parts.append(f"【{name}】")
        parts.extend(item.sections[name])
    present = {line for lines in item.sections.values() for line in lines}
    extra = [c for c in item.criteria if c not in present]
    if extra:
        parts.append("【判据汇总】")
        parts.extend(extra)
    return "\n".join(parts)


def _hint_tokens(hint: str, cfg: SeedingConfig) -> list[str]:
    return [t for t in ascii_tokens(hint) if len(t) >= cfg.ext_id.min_slug_token_length + 1]


def _resolve_hints(node: PlanNode, hint: str, earlier: Sequence[PlanNode],
                   cfg: SeedingConfig) -> list[int]:
    tokens = _hint_tokens(hint, cfg)
    if not tokens:
        return []
    hits: list[int] = []
    for cand in earlier:
        haystack = set(ascii_tokens(cand.name)) | set(ascii_tokens(cand.ext_id))
        if haystack & set(tokens):
            hits.append(cand.index)
    return hits if len(hits) == 1 else []


def _milestone_blocks(count: int, milestones: int) -> list[tuple[int, int]]:
    """把 ``count`` 个节点切成 ``milestones`` 个连续块（长度差 ≤1，每块 ≥1）。"""
    milestones = max(1, min(milestones, count)) if count else 0
    if not milestones:
        return []
    base, rem = divmod(count, milestones)
    blocks: list[tuple[int, int]] = []
    start = 0
    for i in range(milestones):
        size = base + (1 if i < rem else 0)
        blocks.append((start, start + size))
        start += size
    return blocks


def _item_from_dict(raw: Mapping[str, Any]) -> AggregatedItem:
    return AggregatedItem(
        item_id=str(raw["item_id"]),
        title=str(raw["title"]),
        repo=str(raw["repo"]),
        repos=tuple(raw.get("repos", [raw["repo"]])),
        is_maturity=bool(raw.get("is_maturity", False)),
        sections={str(k): list(v) for k, v in (raw.get("sections") or {}).items()},
        criteria=tuple(raw.get("criteria", [])),
        deps=tuple(raw.get("deps", [])),
        quotes=(),
        absorbed=tuple(raw.get("absorbed", [])),
        repo_conflict=bool(raw.get("repo_conflict", False)),
        max_similarity=float(raw.get("max_similarity", 0.0)),
        members=tuple(raw.get("members", [])),
    )


# ---------------------------------------------------------------------------
# seeding
# ---------------------------------------------------------------------------
def seed_plan(agg: Mapping[str, Any], cfg: SeedingConfig, *, wave: str,
              plan_id: str, name: str | None = None,
              parent_plan: str | None = None) -> dict[str, Any]:
    """聚合结果 → 计划（节点 DAG + 里程碑 + 溢出队列）。"""
    raw_items = list(agg.get("items", []))
    items = [_item_from_dict(raw) for raw in raw_items]
    if not items:
        raise ValueError("聚合结果为空，无法播种")

    capacity = cfg.capacity
    selected = items[:capacity]
    overflow = items[capacity:]
    overflow_raw = raw_items[capacity:]

    blocks = _milestone_blocks(len(selected), cfg.plan.milestone_count)
    milestone_of: dict[int, int] = {}
    for m_idx, (start, end) in enumerate(blocks):
        for pos in range(start, end):
            milestone_of[pos] = m_idx

    nodes: list[PlanNode] = []
    for pos, item in enumerate(selected):
        index = pos + 1
        ext_id = build_ext_id(wave, index, item.title, cfg)
        nodes.append(PlanNode(
            index=index,
            ext_id=ext_id,
            name=item.title,
            repo=item.repo,
            milestone=milestone_of[pos],
            goal=render_goal(item),
            dependencies=[],
            is_maturity=item.is_maturity,
            metadata={
                "item_id": item.item_id,
                "sources": [q.source for q in item.quotes] or list(item.sources or []),
                "quotes": [q.as_dict() for q in item.quotes],
                "absorbed": list(item.absorbed),
                "repo_conflict": item.repo_conflict,
                "max_similarity": item.max_similarity,
                "members": list(item.members),
                "criteria": list(item.criteria),
                "physics_domain": "",
            },
        ))

    # --- dependencies: chain within milestone + resolved hints -----------
    by_milestone: dict[int, list[PlanNode]] = {}
    for node in nodes:
        by_milestone.setdefault(node.milestone, []).append(node)
    unresolved: list[dict[str, Any]] = []
    for node, item in zip(nodes, selected):
        deps: list[int] = []
        if cfg.plan.chain_within_milestone:
            mates = by_milestone[node.milestone]
            pos = [m.index for m in mates].index(node.index)
            if pos > 0:
                deps.append(mates[pos - 1].index)
        earlier = [n for n in nodes if n.index < node.index]
        for hint in item.deps:
            hits = _resolve_hints(node, hint, earlier, cfg)
            if not hits:
                unresolved.append({"node": node.index, "hint": hint})
                continue
            if hits[0] not in deps:
                deps.append(hits[0])
        node.dependencies = sorted(deps)

    milestones = [{
        "index": m_idx,
        "name": f"{wave}-M{m_idx + 1}",
        "node_indices": [n.index for n in by_milestone.get(m_idx, [])],
    } for m_idx in range(len(blocks))]

    plan = {
        "schema": SCHEMA,
        "plan_id": str(plan_id),
        "wave": wave,
        "name": name or f"{wave}-seeded",
        "parent_plan": parent_plan,
        "milestones": milestones,
        "nodes": [n.as_dict() for n in nodes],
        "overflow": [{
            "item_id": it.item_id,
            "title": it.title,
            "repo": it.repo,
            "is_maturity": it.is_maturity,
            "sources": [q["source"] for q in (raw.get("quotes") or [])],
            "reason": "capacity",
            "item": dict(raw, quotes=list(raw.get("quotes") or [])),
        } for it, raw in zip(overflow, overflow_raw)],
        "meta": {
            "aggregate_counts": agg.get("counts", {}),
            "capacity": capacity,
            "reserved_platform_slot": cfg.plan.reserve_platform_slot,
            "selection_policy": "maturity_first_then_repo_then_digest",
            "items_total": len(items),
            "selected": len(selected),
            "overflow": len(overflow),
            "dep_policy": ("chain_within_milestone" if cfg.plan.chain_within_milestone
                           else "hints_only"),
            "unresolved_hints": unresolved,
            "config": cfg.as_dict(),
        },
    }
    return plan


def plan_from_dict(raw: Mapping[str, Any]) -> dict[str, Any]:
    """规范化外部传入的 plan（保留未知字段，节点强制重排 index）。"""
    plan = dict(raw)
    plan["nodes"] = [PlanNode.from_dict(n) for n in raw.get("nodes", [])]
    plan.setdefault("milestones", [])
    plan.setdefault("overflow", [])
    plan.setdefault("meta", {})
    return plan


def plan_to_json(plan: Mapping[str, Any]) -> dict[str, Any]:
    out = dict(plan)
    out["nodes"] = [n.as_dict() if isinstance(n, PlanNode) else dict(n)
                    for n in plan.get("nodes", [])]
    return out