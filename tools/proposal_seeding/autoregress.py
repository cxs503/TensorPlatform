"""战役 DAG 自回归增长：管线用自身遥测合成下一波，并把溢出项反哺回去。

为什么需要「自回归」
--------------------
一次性播种的战役是开环的：播种完就没有来源了，下一波要么靠人写、要么靠随机。
本模块把管线的**自身观测**变成提案来源，形成闭环：

``proposal → aggregate → seed → gate → telemetry → platform_proposal → 下一波``

遥测口径（全部可从 :mod:`aggregate` / :mod:`gate` / :mod:`seed` 的产物直接读出）
-------------------------------------------------------------------------------
* **聚合段**：``merged_semantic`` / ``cross_source_merges`` / ``max_jaccard``
  —— 两位提案者的提案到底有没有语义重叠？没有重叠时阈值是否失效？
* **播种段**：``overflow`` / ``capacity`` / ``selected``
  —— 容量饱和了吗？溢出项必须进下一波，否则能力被静默丢弃。
* **门段**：``failed`` —— 哪一项硬校验没过？这一项就是下一波的自优化靶子。
* **契约段**：无判据节点占比 —— 判据是机器可算性的来源，缺判据的节点无法被裁决。

合成的平台自优化提案是**确定性**的（摘要由遥测内容定址），因此重复运行不会产生
重复提案；注入也幂等：计划里已有平台节点就不再注入。
"""

from __future__ import annotations

from typing import Any, Mapping, Sequence

from .config import SeedingConfig
from .gate import check_plan
from .model import PlanNode, digest
from .seed import _item_from_dict, build_ext_id, render_goal, seed_plan

SCHEMA = "proposal-seeding-autoregress/1.0"

STAGE_ORDER = ("aggregation", "arbitration", "seeding", "gate", "contract")


# ---------------------------------------------------------------------------
# telemetry
# ---------------------------------------------------------------------------
def pipeline_telemetry(agg: Mapping[str, Any], plan: Mapping[str, Any] | None = None,
                       gate: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """从聚合 / 计划 / 门产物读出管线自身观测与缺口。"""
    counts = dict(agg.get("counts", {}))
    sim = dict(agg.get("similarity", {}))
    nodes = [n.as_dict() if isinstance(n, PlanNode) else dict(n)
             for n in (plan.get("nodes", []) if plan else [])]
    no_criteria = [n for n in nodes if not (n.get("metadata") or {}).get("criteria")]
    overflow = list(plan.get("overflow", [])) if plan else []

    deficits: list[dict[str, Any]] = []

    def deficit(stage: str, signal: str, detail: str, severity: str = "medium") -> None:
        deficits.append({"stage": stage, "signal": signal, "detail": detail,
                         "severity": severity,
                         "digest": digest({"stage": stage, "signal": signal})[:12]})

    if counts.get("proposals_in", 0) == 0:
        deficit("aggregation", "empty_input", "输入提案为空，聚合段无内容可仲裁", "high")
    if counts.get("proposals_in", 0) > 1 and counts.get("merged_semantic", 0) == 0:
        deficit("aggregation", "no_semantic_overlap",
                f"{counts.get('proposals_in')} 条提案的最大 3-gram Jaccard 仅 "
                f"{sim.get('max_jaccard')}（阈值 {sim.get('threshold')}）："
                f"要么提案者之间确实互补，要么阈值/文本口径失配", "medium")
    if counts.get("proposals_in", 0) > 1 and counts.get("cross_source_merges", 0) == 0:
        deficit("arbitration", "siloed_proposers",
                "跨提案者合并数为 0：各提案者的提案彼此不相交，仲裁只能并列不能吸收", "low")
    if overflow:
        deficit("seeding", "capacity_saturation",
                f"{len(overflow)} 条聚合项超出容量，已进溢出队列待下一波", "high")
    if gate is not None:
        for name in gate.get("failed", []):
            deficit("gate", f"gate_fail:{name}", f"硬校验 {name} 未通过", "high")
    if nodes and no_criteria:
        deficit("contract", "missing_criteria",
                f"{len(no_criteria)}/{len(nodes)} 个节点无显式判据，机器裁决链缺口",
                "medium")

    severity_rank = {"high": 0, "medium": 1, "low": 2}
    deficits.sort(key=lambda d: (severity_rank.get(d["severity"], 9),
                                 STAGE_ORDER.index(d["stage"]) if d["stage"] in STAGE_ORDER else 9,
                                 d["signal"]))
    return {
        "schema": SCHEMA + "/telemetry",
        "counts": counts,
        "similarity": {k: v for k, v in sim.items() if k != "matrix"},
        "plan": {
            "plan_id": str(plan.get("plan_id")) if plan else None,
            "wave": plan.get("wave") if plan else None,
            "node_count": len(nodes),
            "overflow": len(overflow),
            "capacity": (plan.get("meta", {}) or {}).get("capacity") if plan else None,
            "nodes_without_criteria": len(no_criteria),
        },
        "gate": {"all_passed": gate.get("all_passed") if gate else None,
                 "failed": list(gate.get("failed", [])) if gate else []},
        "deficits": deficits,
        "primary_deficit": deficits[0] if deficits else None,
    }


# ---------------------------------------------------------------------------
# platform self-optimisation proposal
# ---------------------------------------------------------------------------
def platform_proposal(telemetry: Mapping[str, Any], cfg: SeedingConfig,
                      *, wave: str) -> dict[str, Any]:
    """把遥测缺口合成一条平台自优化提案（成熟化单，幂等摘要）。"""
    primary = telemetry.get("primary_deficit") or {
        "stage": "aggregation", "signal": "no_deficit", "detail": "管线无观测缺口"}
    stage = primary["stage"]
    title = (f"提案聚合播种管线{cfg.maturity_marker}：{stage} 段"
             f"{primary['signal']} 的自动化闭环")
    deficits = telemetry.get("deficits", [])
    goal = {
        "objective": (
            f"把仲裁播种管线 {stage} 段的观测缺口（{primary['signal']}）转成机器可执行的门，"
            f"使「提案→仲裁→播种→激活」在无人值守下自持运行。"),
        "approach": [
            f"1) 复现缺口：{primary['detail']}",
            "2) 为缺口建立机器判据（数字或布尔断言），纳入 gate 硬校验",
            "3) 用本波遥测做回归基线，下一波同口径复测，未改善即 honest FAIL",
        ],
        "acceptance": [
            f"1) 缺口 {primary['signal']} 在下一波遥测中消失或给出显式豁免理由",
            "2) 管线回归测试全绿（同输入两次运行 ext_id 序列一致）",
        ],
    }
    return {
        "title": title,
        "repo": cfg.platform_repo,
        "goal": goal,
        "criteria": goal["acceptance"],
        "dependencies_hint": [],
        "is_maturity": True,
        "origin": "autoregress",
        "source_telemetry": {
            "primary": primary,
            "deficit_count": len(deficits),
            "deficit_signals": [d["signal"] for d in deficits],
        },
    }


# ---------------------------------------------------------------------------
# injection
# ---------------------------------------------------------------------------
def _platform_nodes(plan: Mapping[str, Any], cfg: SeedingConfig) -> list[dict[str, Any]]:
    marker = cfg.maturity_marker
    return [n for n in plan.get("nodes", [])
            if n.get("repo") == cfg.platform_repo
            and (marker in str(n.get("name", "")) or marker in str(n.get("goal", "")))]


def _tail_nodes(plan: Mapping[str, Any]) -> list[int]:
    """每个（除最后一个）里程碑的末节点：平台节点要消费它们的遥测。"""
    nodes = [n if isinstance(n, PlanNode) else PlanNode.from_dict(n)
             for n in plan.get("nodes", [])]
    milestones = list(plan.get("milestones", []))
    tails: list[int] = []
    for m in milestones[:-1]:
        idxs = [i for i in m.get("node_indices", []) if any(n.index == i for n in nodes)]
        if idxs:
            tails.append(max(idxs))
    return tails


def ensure_platform_node(plan: Mapping[str, Any], cfg: SeedingConfig,
                         *, telemetry: Mapping[str, Any] | None = None,
                         proposal: Mapping[str, Any] | None = None
                         ) -> tuple[dict[str, Any], dict[str, Any]]:
    """保证计划含 ≥1 条平台自优化节点（幂等）。返回 ``(plan, injection_report)``。"""
    plan = dict(plan)
    nodes = [n if isinstance(n, PlanNode) else PlanNode.from_dict(n)
             for n in plan.get("nodes", [])]
    plan["nodes"] = [n.as_dict() for n in nodes]
    existing = _platform_nodes({"nodes": [n.as_dict() for n in nodes]}, cfg)
    report: dict[str, Any] = {
        "schema": SCHEMA + "/injection",
        "injected": False,
        "reason": "",
        "existing": [n["ext_id"] for n in existing],
    }
    if existing:
        report["reason"] = "计划已含平台自优化节点，幂等跳过"
        return plan, report

    telemetry = telemetry or {}
    prop = dict(proposal or platform_proposal(telemetry, cfg, wave=str(plan.get("wave") or "")))
    item_like = _item_from_dict({
        "item_id": digest({"title": prop["title"], "goal": prop["goal"]})[:16],
        "title": prop["title"],
        "repo": prop["repo"],
        "repos": [prop["repo"]],
        "is_maturity": True,
        "sections": {k: (v if isinstance(v, list) else [str(v)])
                     for k, v in (prop["goal"] or {}).items()},
        "criteria": list(prop.get("criteria", [])),
        "deps": [],
        "quotes": [],
        "absorbed": [],
    })
    index = max((n.index for n in nodes), default=0) + 1
    milestones = list(plan.get("milestones", []))
    if not milestones:
        milestones = [{"index": 0, "name": f"{plan.get('wave')}-M1", "node_indices": []}]
    last_milestone = int(milestones[-1].get("index", len(milestones) - 1))
    deps = _tail_nodes({"nodes": nodes, "milestones": milestones}) \
        if cfg.plan.platform_deps == "previous_milestone_tails" else []
    node = PlanNode(
        index=index,
        ext_id=build_ext_id(str(plan.get("wave") or "wave"), index, item_like.title, cfg),
        name=item_like.title,
        repo=item_like.repo,
        milestone=last_milestone,
        goal=render_goal(item_like),
        dependencies=sorted(d for d in deps if d < index),
        is_maturity=True,
        metadata={
            "item_id": item_like.item_id,
            "origin": "autoregress",
            "sources": ["autoregress"],
            "criteria": list(item_like.criteria),
            "telemetry_primary": (telemetry.get("primary_deficit") or {}).get("signal"),
            "absorbed": [],
            "quotes": [],
            "repo_conflict": False,
            "max_similarity": 0.0,
            "members": [],
        },
    )
    nodes.append(node)
    milestones[-1]["node_indices"] = list(milestones[-1].get("node_indices", [])) + [index]
    plan["nodes"] = [n.as_dict() for n in nodes]
    plan["milestones"] = milestones
    report.update({
        "injected": True,
        "reason": "计划缺平台自优化节点，按遥测缺口合成并注入",
        "node": node.as_dict(),
        "platform_proposal": prop,
    })
    return plan, report


# ---------------------------------------------------------------------------
# next wave
# ---------------------------------------------------------------------------
def advance_wave(plan: Mapping[str, Any], cfg: SeedingConfig, *, wave_next: str,
                 plan_id_next: str | None = None,
                 telemetry: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """溢出项 + 平台自优化提案 → 下一波计划（闭环的第二圈）。"""
    overflow = list(plan.get("overflow", []))
    items = [dict(entry["item"]) for entry in overflow if entry.get("item")]
    telemetry = telemetry or pipeline_telemetry({"counts": {}, "similarity": {}}, plan)
    prop = platform_proposal(telemetry, cfg, wave=wave_next)
    seed_input = {
        "counts": {"proposals_in": len(items), "carried_overflow": len(items)},
        "items": sorted(items, key=lambda it: (not it.get("is_maturity"),
                                               it.get("repo", ""), it.get("item_id", ""))),
    }
    next_plan = seed_plan(seed_input, cfg, wave=wave_next,
                          plan_id=plan_id_next or plan_id_next, name=f"{wave_next}-autoregress",
                          parent_plan=str(plan.get("plan_id")))
    next_plan["meta"]["origin"] = "autoregress.advance_wave"
    next_plan["meta"]["carried_overflow"] = len(items)
    next_plan["meta"]["platform_proposal_seed"] = prop["title"]
    # 每一波都必须含平台自优化节点：下一波同样注入（幂等；缺则补）
    next_plan, injection = ensure_platform_node(next_plan, cfg, telemetry=telemetry,
                                                proposal=prop)
    mins = cfg.plan.node_count_min
    backpressure = {
        "node_count": len(next_plan["nodes"]),
        "min_required": mins,
        "demand_new_proposals": len(next_plan["nodes"]) < mins,
        "demanded": max(0, mins - len(next_plan["nodes"])),
        "note": ("背压：下一波节点数不足下限 → 需要新一轮提案者入场"
                 if len(next_plan["nodes"]) < mins else "下一波节点数满足下限"),
    }
    return {"plan": next_plan, "backpressure": backpressure, "telemetry": telemetry,
            "platform_proposal": prop, "injection": injection}