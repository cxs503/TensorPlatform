"""参数来源：``config/proposal_seeding.json``（唯一真相，代码内零字面量阈值）。

为什么阈值必须外置
------------------
聚合阈值、节点数上下界、里程碑数、成熟化单下限、仓白名单都会随战役规模漂移；
把它们写进代码会让「同一份输入在不同战役里得到不同结论」无法审计。本模块把它们
收进一个带 ``schema_version`` 的 JSON，并用 :func:`load_config` 做 fail-closed 校验。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

CONFIG_SCHEMA = "proposal-seeding/1.0"
DEFAULT_CONFIG_PATH = Path(__file__).resolve().parents[2] / "config" / "proposal_seeding.json"


class ConfigError(ValueError):
    """配置缺项 / 版本不符 / 值域非法。"""


@dataclass(frozen=True)
class SimilarityConfig:
    shingle_size: int = 3
    merge_threshold: float = 0.55
    containment_weight: float = 0.5
    min_length_ratio: float = 0.7
    merge_rule: str = "jaccard_only"


@dataclass(frozen=True)
class PlanConfig:
    node_count_min: int = 5
    node_count_max: int = 12
    milestone_count: int = 3
    min_maturity_nodes: int = 2
    reserve_platform_slot: bool = True
    chain_within_milestone: bool = True
    platform_deps: str = "previous_milestone_tails"


@dataclass(frozen=True)
class ExtIdConfig:
    index_width: int = 2
    max_slug_tokens: int = 3
    min_slug_token_length: int = 3
    fallback_prefix: str = "cap"


@dataclass(frozen=True)
class SeedingConfig:
    """聚合/播种/门的全部参数。构造后不可变，便于在报告中引用确切取值。"""

    schema_version: str = CONFIG_SCHEMA
    repo_whitelist: tuple[str, ...] = ()
    similarity: SimilarityConfig = field(default_factory=SimilarityConfig)
    plan: PlanConfig = field(default_factory=PlanConfig)
    maturity_marker: str = ""
    platform_repo: str = ""
    ext_id: ExtIdConfig = field(default_factory=ExtIdConfig)
    criteria_section_markers: tuple[str, ...] = ()
    repro_match_threshold: float = 0.30
    required_fields: tuple[str, ...] = ("title", "repo", "goal")
    source_path: str | None = None

    # -- derived -----------------------------------------------------------
    @property
    def capacity(self) -> int:
        """本波可容纳的提案节点数（预留平台自优化节点 1 席）。"""
        reserved = 1 if self.plan.reserve_platform_slot else 0
        return max(0, self.plan.node_count_max - reserved)

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "repo_whitelist": list(self.repo_whitelist),
            "similarity": vars(self.similarity),
            "plan": vars(self.plan),
            "maturity_marker": self.maturity_marker,
            "platform_repo": self.platform_repo,
            "ext_id": vars(self.ext_id),
            "criteria_section_markers": list(self.criteria_section_markers),
            "repro_match_threshold": self.repro_match_threshold,
            "required_fields": list(self.required_fields),
            "source_path": self.source_path,
            "capacity": self.capacity,
        }


def _require(raw: dict[str, Any], keys: Iterable[str], where: str) -> None:
    missing = [k for k in keys if k not in raw]
    if missing:
        raise ConfigError(f"{where} 缺少字段: {', '.join(missing)}")


def load_config(path: str | Path | None = None) -> SeedingConfig:
    """读取并校验配置（fail-closed）。"""
    p = Path(path) if path is not None else DEFAULT_CONFIG_PATH
    if not p.is_file():
        raise ConfigError(f"配置文件不存在: {p}")
    raw = json.loads(p.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ConfigError("配置文件顶层必须是对象")
    _require(raw, ("schema_version", "repo_whitelist", "similarity", "plan", "ext_id"),
             "config")
    if raw["schema_version"] != CONFIG_SCHEMA:
        raise ConfigError(f"schema_version 期望 {CONFIG_SCHEMA}，实际 {raw['schema_version']}")
    whitelist = raw["repo_whitelist"]
    if not isinstance(whitelist, list) or not whitelist:
        raise ConfigError("repo_whitelist 必须是非空列表")

    sim = dict(raw["similarity"])
    plan = dict(raw["plan"])
    ext = dict(raw["ext_id"])
    cfg = SeedingConfig(
        schema_version=raw["schema_version"],
        repo_whitelist=tuple(str(x) for x in whitelist),
        similarity=SimilarityConfig(
            shingle_size=int(sim.get("shingle_size", 3)),
            merge_threshold=float(sim.get("merge_threshold", 0.55)),
            containment_weight=float(sim.get("containment_weight", 0.5)),
            min_length_ratio=float(sim.get("min_length_ratio", 0.7)),
            merge_rule=str(sim.get("merge_rule", "jaccard_only")),
        ),
        plan=PlanConfig(
            node_count_min=int(plan.get("node_count_min", 5)),
            node_count_max=int(plan.get("node_count_max", 12)),
            milestone_count=int(plan.get("milestone_count", 3)),
            min_maturity_nodes=int(plan.get("min_maturity_nodes", 2)),
            reserve_platform_slot=bool(plan.get("reserve_platform_slot", True)),
            chain_within_milestone=bool(plan.get("chain_within_milestone", True)),
            platform_deps=str(plan.get("platform_deps", "previous_milestone_tails")),
        ),
        maturity_marker=str(raw.get("maturity_marker", "")),
        platform_repo=str(raw.get("platform_repo", "")),
        ext_id=ExtIdConfig(
            index_width=int(ext.get("index_width", 2)),
            max_slug_tokens=int(ext.get("max_slug_tokens", 3)),
            min_slug_token_length=int(ext.get("min_slug_token_length", 3)),
            fallback_prefix=str(ext.get("fallback_prefix", "cap")),
        ),
        criteria_section_markers=tuple(str(x) for x in raw.get("criteria_section_markers", [])),
        repro_match_threshold=float(raw.get("repro", {}).get("match_threshold", 0.30)),
        required_fields=tuple(raw.get("input_sources", {}).get(
            "reject_missing_fields", ["title", "repo", "goal"])),
        source_path=str(p),
    )
    _validate(cfg)
    return cfg


def _validate(cfg: SeedingConfig) -> None:
    if not 0.0 < cfg.similarity.merge_threshold <= 1.0:
        raise ConfigError("similarity.merge_threshold 必须落在 (0, 1]")
    if cfg.similarity.shingle_size < 1:
        raise ConfigError("similarity.shingle_size 必须 ≥ 1")
    if cfg.plan.node_count_min < 1:
        raise ConfigError("plan.node_count_min 必须 ≥ 1")
    if cfg.plan.node_count_max < cfg.plan.node_count_min:
        raise ConfigError("plan.node_count_max 必须 ≥ node_count_min")
    if cfg.plan.milestone_count < 1:
        raise ConfigError("plan.milestone_count 必须 ≥ 1")
    if cfg.plan.milestone_count > cfg.plan.node_count_min:
        # 否则「每里程碑 ≥1 节点」与「节点数下限」不可同时满足
        raise ConfigError("plan.milestone_count 不得大于 node_count_min")
    if not cfg.maturity_marker:
        raise ConfigError("maturity_marker 不得为空（成熟化单判定依赖它）")
    if cfg.plan.reserve_platform_slot and cfg.platform_repo not in cfg.repo_whitelist:
        raise ConfigError("platform_repo 必须在 repo_whitelist 内")