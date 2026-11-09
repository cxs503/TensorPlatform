#!/usr/bin/env python3
"""机器门自动裁定（machine gate）——播种的 campaign seed 结构/治理合规性硬校验。

**要防的东西**：仲裁者（上层智能体）播种的 draft 战役是自由文本生成的 JSON/动作序列，
没有数据库约束：可能只播 1-2 个节点就当战役、可能复用别人的 ``ext_id``（台账撞车、
证据归错节点）、可能把节点派给白名单外的仓、可能播一条「一句话任务书」或一条环，
也可能整场战役没有一条「成熟化」单（=只堆新功能，旧能力永不复核）。

本模块把这些判据做成**纯函数 + 可注入配置 + 具体违规项**：

| code | 判据 |
|---|---|
| PLAN_STATUS / PLAN_MISSING | plan 存在且状态 ∈ {draft, active} |
| NODE_COUNT | 节点数 ∈ [min_nodes, max_nodes]（默认 [5,12]） |
| EXT_ID_MISSING / EXT_ID_DUPLICATE / EXT_ID_COLLISION | ext_id 齐备、seed 内唯一、全库唯一（注册表注入） |
| REPO_NOT_ALLOWED | repo 在白名单内（别名先归一） |
| GOAL_TOO_THIN | goal 字数 ≥ min_goal_chars（默认 80） |
| CRITERIA_MISSING | 每节点 ≥1 条机器可算验收判据（字符串或 {"id","metric"} 均可） |
| MILESTONE_MISSING / MILESTONE_EMPTY / MILESTONE_FEW | 节点挂在已定义里程碑上；每里程碑 ≥1 节点；里程碑数 ≥ min_milestones |
| MATURATION_FEW | goal 含成熟化标记的节点数 ≥ min_maturation（默认 2） |
| DAG_* | 复用 ``dag.check_dag``：外引用、自指、环（带环路径） |

每条违规是一个 ``Violation(code, node_id, detail)``，按 (code, node_id) 升序输出 → 报告可
逐条复算、可与判决对账。``GateReport`` 同时给出 ``n_checked`` 与 ``checks``（每条判据的
通过/失败计数），便于度量「机器门对不合规 seed 的拒收率」。

**零领域语义**：白名单、节点数区间、成熟化标记、goal 字数下限全部由配置注入；
默认值只是平台治理参数（不是学科判据）。

外部依赖注入：``ext_id_registry``（既用 ext_id 集合，通常来自 DB 的
``SELECT metadata->>'ext_id' FROM campaign_nodes``）——模块本身不连库。
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Iterable, List, Mapping, Optional, Sequence, Set

from . import dag as dag_mod

VERSION = "in99-n10-v1"

DEFAULT_REPO_WHITELIST: tuple[str, ...] = (
    "TensorPlatform", "TensorPhysics", "TensorLBM", "TensorFVM", "TensorFEM",
    "TensorDEM", "TensorBEM", "TensorSPH", "TensorDG", "TensorCAD", "TensorPapers",
)
REPO_ALIASES: Dict[str, str] = {
    "platform": "TensorPlatform", "tensorplatform": "TensorPlatform",
    "physics": "TensorPhysics", "tensorphysics": "TensorPhysics",
}


@dataclass(frozen=True)
class GateConfig:
    min_nodes: int = 5
    max_nodes: int = 12
    min_goal_chars: int = 80
    min_milestones: int = 2
    min_maturation: int = 2
    maturation_marker: str = "成熟化"
    require_criteria: bool = True
    repo_whitelist: tuple[str, ...] = DEFAULT_REPO_WHITELIST
    repo_aliases: Mapping[str, str] = field(default_factory=lambda: dict(REPO_ALIASES))
    allowed_statuses: tuple[str, ...] = ("draft", "active")

    def to_dict(self) -> dict:
        return {
            "min_nodes": self.min_nodes, "max_nodes": self.max_nodes,
            "min_goal_chars": self.min_goal_chars, "min_milestones": self.min_milestones,
            "min_maturation": self.min_maturation, "maturation_marker": self.maturation_marker,
            "require_criteria": self.require_criteria,
            "repo_whitelist": list(self.repo_whitelist),
            "allowed_statuses": list(self.allowed_statuses),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, object]) -> "GateConfig":
        known = set(cls.__dataclass_fields__)
        unknown = set(data) - known
        if unknown:
            raise ValueError(f"未知配置项: {sorted(unknown)}")
        d = dict(data)
        if "repo_whitelist" in d:
            d["repo_whitelist"] = tuple(d["repo_whitelist"])  # type: ignore[arg-type]
        if "allowed_statuses" in d:
            d["allowed_statuses"] = tuple(d["allowed_statuses"])  # type: ignore[arg-type]
        return cls(**d)  # type: ignore[arg-type]

    @classmethod
    def load(cls, path: str | Path | None) -> "GateConfig":
        if path is None:
            return cls()
        return cls.from_dict(json.loads(Path(path).read_text(encoding="utf-8")))


@dataclass(frozen=True)
class Violation:
    code: str
    detail: str
    node_id: Optional[int] = None

    def to_dict(self) -> dict:
        return {"code": self.code, "node_id": self.node_id, "detail": self.detail}

    def __str__(self) -> str:
        where = "" if self.node_id is None else f"[node {self.node_id}] "
        return f"{self.code}: {where}{self.detail}"


@dataclass
class GateReport:
    ok: bool
    violations: List[Violation] = field(default_factory=list)
    n_checked: int = 0
    checks: Dict[str, int] = field(default_factory=dict)
    dag: Optional[dag_mod.DagReport] = None
    stats: Dict[str, object] = field(default_factory=dict)

    def codes(self) -> List[str]:
        return sorted({v.code for v in self.violations})

    def to_dict(self) -> dict:
        return {
            "ok": self.ok,
            "n_checked": self.n_checked,
            "violations": [v.to_dict() for v in self.violations],
            "violation_codes": self.codes(),
            "reasons": [str(v) for v in self.violations],
            "checks": dict(sorted(self.checks.items())),
            "stats": dict(sorted(self.stats.items())),
            "dag": self.dag.to_dict() if self.dag else None,
        }


def _criteria_count(node: dag_mod.CampaignNode) -> int:
    raw = node.metadata.get("acceptance_criteria")
    if raw is None:
        return 0
    if isinstance(raw, str):
        return 1 if raw.strip() else 0
    if isinstance(raw, Mapping):
        return len(raw)
    try:
        return len([c for c in raw if str(c).strip()])  # type: ignore[union-attr]
    except TypeError:
        return 1


def evaluate_seed(seed: Mapping[str, object],
                  config: Optional[GateConfig] = None,
                  ext_id_registry: Iterable[str] = ()) -> GateReport:
    """对一份 campaign seed 做机器门裁定。

    seed = {"plan": {"status": "draft", ...}, "milestones": [{"id": "M1"}...],
            "nodes": [{"node_id", "milestone_id", "dependencies", "metadata": {...}}...]}
    ext_id_registry: 已占用 ext_id 集合（全库范围，由调用方注入；DB 只读导出）
    """
    cfg = config or GateConfig()
    violations: List[Violation] = []
    checks: Dict[str, int] = {}

    def bump(code: str, ok: bool) -> None:
        checks[code] = checks.get(code, 0) + (1 if ok else 0)

    plan = seed.get("plan") or {}
    if not isinstance(plan, Mapping) or not plan:
        violations.append(Violation("PLAN_MISSING", "seed 缺 plan 段"))
        return GateReport(False, violations, 0, {"PLAN_MISSING": 0},
                          stats={"n_nodes": 0})
    status = str(plan.get("status", "draft"))
    ok_status = status in cfg.allowed_statuses
    bump("PLAN_STATUS", ok_status)
    if not ok_status:
        violations.append(Violation("PLAN_STATUS",
                                    f"status={status} 不在 {list(cfg.allowed_statuses)}"))

    nodes_raw = seed.get("nodes") or []
    if not isinstance(nodes_raw, Sequence) or isinstance(nodes_raw, (str, bytes)):
        violations.append(Violation("NODES_MALFORMED", "nodes 不是数组"))
        return GateReport(False, violations, 0, checks, stats={"n_nodes": 0})
    nodes, struct_problems = dag_mod.normalize_nodes(nodes_raw)  # type: ignore[arg-type]
    for p in struct_problems:
        violations.append(Violation("NODE_STRUCTURE", p))

    n = len(nodes)
    ok_count = cfg.min_nodes <= n <= cfg.max_nodes
    bump("NODE_COUNT", ok_count)
    if not ok_count:
        violations.append(Violation("NODE_COUNT",
                                    f"节点数 {n} 不在 [{cfg.min_nodes},{cfg.max_nodes}]"))

    # ── ext_id：齐备 / seed 内唯一 / 全库唯一 ──
    seen_ext: Dict[str, int] = {}
    registry = {str(e).strip() for e in ext_id_registry if str(e).strip()}
    for node in nodes:
        ext = node.ext_id.strip()
        if not ext:
            violations.append(Violation("EXT_ID_MISSING", "缺 ext_id", node.node_id))
            bump("EXT_ID_PRESENT", False)
            continue
        bump("EXT_ID_PRESENT", True)
        if ext in seen_ext:
            violations.append(Violation("EXT_ID_DUPLICATE",
                                        f"ext_id={ext} 与节点 {seen_ext[ext]} 撞车", node.node_id))
            bump("EXT_ID_UNIQUE_IN_SEED", False)
        else:
            seen_ext[ext] = node.node_id
            bump("EXT_ID_UNIQUE_IN_SEED", True)
        if ext in registry:
            violations.append(Violation("EXT_ID_COLLISION",
                                        f"ext_id={ext} 已被库内其他节点占用", node.node_id))
            bump("EXT_ID_GLOBAL_UNIQUE", False)
        else:
            bump("EXT_ID_GLOBAL_UNIQUE", True)

    # ── repo 白名单 / goal 厚度 / 判据 ──
    for node in nodes:
        repo = cfg.repo_aliases.get(node.repo.strip().lower(), node.repo.strip())
        ok_repo = repo in cfg.repo_whitelist
        bump("REPO_ALLOWED", ok_repo)
        if not ok_repo:
            violations.append(Violation("REPO_NOT_ALLOWED",
                                        f"repo={node.repo or '<空>'} 不在白名单", node.node_id))
        goal = node.goal.strip()
        ok_goal = len(goal) >= cfg.min_goal_chars
        bump("GOAL_THICK", ok_goal)
        if not ok_goal:
            violations.append(Violation("GOAL_TOO_THIN",
                                        f"goal {len(goal)} 字 < 下限 {cfg.min_goal_chars}",
                                        node.node_id))
        if cfg.require_criteria:
            nc = _criteria_count(node)
            bump("CRITERIA_PRESENT", nc >= 1)
            if nc < 1:
                violations.append(Violation("CRITERIA_MISSING", "缺机器可算验收判据", node.node_id))

    # ── 里程碑 ──
    ms_raw = seed.get("milestones") or []
    ms_ids: Set[str] = set()
    for m in ms_raw if isinstance(ms_raw, Sequence) else []:
        if isinstance(m, Mapping):
            mid = m.get("id", m.get("milestone_id", m.get("name")))
            if mid is not None:
                ms_ids.add(str(mid))
        else:
            ms_ids.add(str(m))
    ok_ms_count = len(ms_ids) >= cfg.min_milestones
    bump("MILESTONE_COUNT", ok_ms_count)
    if not ok_ms_count:
        violations.append(Violation("MILESTONE_FEW",
                                    f"里程碑 {len(ms_ids)} < 下限 {cfg.min_milestones}"))
    per_ms: Dict[str, int] = {}
    for node in nodes:
        mid = node.milestone_id
        if not mid or mid in ("None", "", "null"):
            violations.append(Violation("MILESTONE_MISSING", "节点未挂里程碑", node.node_id))
            bump("MILESTONE_ATTACHED", False)
            continue
        if ms_ids and mid not in ms_ids:
            violations.append(Violation("MILESTONE_MISSING",
                                        f"里程碑 {mid} 未在 seed.milestones 定义", node.node_id))
            bump("MILESTONE_ATTACHED", False)
            continue
        bump("MILESTONE_ATTACHED", True)
        per_ms[mid] = per_ms.get(mid, 0) + 1
    empty_ms = sorted(set(ms_ids) - set(per_ms))
    bump("MILESTONE_NONEMPTY", not empty_ms)
    if empty_ms:
        violations.append(Violation("MILESTONE_EMPTY", f"里程碑无节点: {empty_ms}"))

    # ── 成熟化单 ──
    mat = sum(1 for node in nodes if cfg.maturation_marker in node.goal)
    ok_mat = mat >= cfg.min_maturation
    bump("MATURATION_ENOUGH", ok_mat)
    if not ok_mat:
        violations.append(Violation("MATURATION_FEW",
                                    f"成熟化单 {mat} < 下限 {cfg.min_maturation}"))

    # ── DAG：外引用 / 自指 / 环 ──
    dag_report = dag_mod.check_dag([{"node_id": nd.node_id, "dependencies": nd.dependencies,
                                     "milestone_id": nd.milestone_id, "metadata": nd.metadata}
                                    for nd in nodes])
    for code, present in (("DAG_OUT_OF_PLAN", bool(dag_report.out_of_plan)),
                          ("DAG_SELF_LOOP", bool(dag_report.self_loops)),
                          ("DAG_CYCLE", bool(dag_report.cycles))):
        checks[code] = 0 if present else 1          # 通过=1 / 不通过=0（与 bump 同口径）
        if present:
            detail = {k: v for k, v in dag_report.to_dict().items() if k in
                      ("cycle_paths", "out_of_plan", "self_loops")}
            violations.append(Violation(code, json.dumps(detail, ensure_ascii=False)))
    checks["DAG_ACYCLIC"] = 1 if not dag_report.cycles else 0
    checks["DAG_REFS_IN_PLAN"] = 1 if not dag_report.out_of_plan else 0

    violations.sort(key=lambda v: (v.code, -1 if v.node_id is None else v.node_id, v.detail))
    stats = {"n_nodes": n, "n_milestones": len(ms_ids), "n_maturation": mat,
             "nodes_per_milestone": dict(sorted(per_ms.items()))}
    return GateReport(ok=not violations, violations=violations, n_checked=1,
                      checks=checks, dag=dag_report, stats=stats)


def gate_seed_file(path: str | Path, config: Optional[GateConfig] = None,
                   ext_id_registry: Iterable[str] = ()) -> GateReport:
    seed = json.loads(Path(path).read_text(encoding="utf-8"))
    return evaluate_seed(seed, config, ext_id_registry)