"""依赖图构建：台账（节点 + 证据等级）× 配置依赖边（结构）× 证据文本推断（补充边）。

零领域语义：能力 id / 域标签 / 依赖边 / 权重 / 推断阈值全部经配置注入。

三类边来源（每条边的 ``sources`` 记录出处，可审计）
--------------------------------------------------
``declared``
    既有台账配置（``sources.semantics_config``）里的 ``depends_on`` 声明边。
``ledger_evidence_text``
    证据文本推断边：下游能力的证据记录（``evidence[*].note`` / ``quote_match`` /
    ``action``）里多次提到上游能力的 id。只做**保守**推断（≥ min_mentions 条独立
    证据记录提到才成立），权重取 ``edges.inferred_weight``（弱于声明边）。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from .model import (
    ConfigError,
    Edge,
    LevelError,
    Node,
    Snapshot,
    confidence_of,
    dedupe_edges,
    normalise_level,
)

__all__ = [
    "GraphConfig",
    "load_json",
    "load_levels",
    "load_declared_edges",
    "infer_edges_from_evidence",
    "build_snapshot",
]


def load_json(path: Path) -> Any:
    if not path.exists():
        raise FileNotFoundError(f"missing source: {path}")
    with path.open("r", encoding="utf-8") as fh:
        return json.load(fh)


@dataclass
class GraphConfig:
    """配置 + 源文件解析结果（路径全部相对 ``repo_root``）。"""

    raw: Dict[str, Any]
    repo_root: Path
    sources: Dict[str, Path]

    @classmethod
    def from_file(cls, config_path: Path, repo_root: Path) -> "GraphConfig":
        raw = load_json(config_path)
        src_cfg = raw.get("sources") or {}
        missing = [k for k in ("semantics_config", "ledger") if not src_cfg.get(k)]
        if missing:
            raise ConfigError(f"config.sources 缺字段: {missing}")
        sources = {k: (repo_root / str(v)) for k, v in src_cfg.items()}
        cfg = cls(raw=raw, repo_root=repo_root, sources=sources)
        cfg.validate()
        return cfg

    # ── 参数访问 ─────────────────────────────────────────────
    def validate(self) -> None:
        for key in ("level_rank", "level_confidence", "confidence_bands", "edges", "cascade", "alerts"):
            if key not in self.raw:
                raise ConfigError(f"config 缺字段: {key}")
        lr = self.raw["level_rank"]
        lc = self.raw["level_confidence"]
        diff = set(lc) - set(lr)
        if diff:
            raise ConfigError(f"level_confidence 含未知等级: {sorted(diff)}")
        bands = {str(b["level"]) for b in self.raw["confidence_bands"]}
        if not bands <= set(lr):
            raise ConfigError(f"confidence_bands 含未知等级: {sorted(bands - set(lr))}")

    @property
    def level_rank(self) -> Dict[str, int]:
        return {str(k): int(v) for k, v in self.raw["level_rank"].items()}

    @property
    def level_confidence(self) -> Dict[str, float]:
        return {str(k): float(v) for k, v in self.raw["level_confidence"].items()}

    @property
    def bands(self) -> List[Mapping[str, Any]]:
        return list(self.raw["confidence_bands"])

    @property
    def default_weight(self) -> float:
        return float(self.raw["edges"].get("default_weight", 1.0))

    @property
    def inferred_weight(self) -> float:
        return float(self.raw["edges"].get("inferred_weight", self.default_weight))

    def weight_override(self, src: str, dst: str) -> Optional[float]:
        key = f"{src}->{dst}"
        kw = (self.raw["edges"].get("overrides") or {}).get(key)
        return None if kw is None else float(kw)

    @property
    def acceptance(self) -> Dict[str, Any]:
        return dict(self.raw.get("acceptance") or {})

    @property
    def performance(self) -> Dict[str, Any]:
        return dict(self.raw.get("performance") or {})


def load_levels(ledger_path: Path, cfg: GraphConfig) -> Tuple[Dict[str, str], List[Dict[str, str]]]:
    """从能力台账取 (id → 声明等级)。无法解析的等级不猜，记入 unparsable。"""
    data = load_json(ledger_path)
    if isinstance(data, dict):
        entries = data.get("capabilities") or data.get("entries") or []
    elif isinstance(data, list):
        entries = data
    else:  # pragma: no cover - 结构错误直接报
        raise ConfigError(f"ledger 结构不支持: {type(data)!r}")
    levels: Dict[str, str] = {}
    unparsable: List[Dict[str, str]] = []
    for entry in entries:
        if not isinstance(entry, Mapping):
            continue
        node_id = entry.get("id") or entry.get("capability_id")
        if not node_id:
            continue
        raw_level = (
            entry.get("claimed_level")
            or entry.get("initial_level")
            or entry.get("level")
            or entry.get("evidence_level")
        )
        try:
            levels[str(node_id)] = normalise_level(raw_level, cfg.level_rank)
        except LevelError as exc:
            unparsable.append({"id": str(node_id), "raw_level": repr(raw_level), "reason": str(exc)})
    return levels, unparsable


def load_declared_edges(semantics_cfg_path: Path, cfg: GraphConfig) -> List[Edge]:
    """从台账配置读 ``depends_on`` 声明边。缺目标节点/自环显式返回，不静默丢弃。"""
    data = load_json(semantics_cfg_path)
    entries = data.get("capabilities") or []
    edges: List[Edge] = []
    for entry in entries:
        dst = str(entry.get("id") or "")
        if not dst:
            continue
        for dep in entry.get("depends_on") or []:
            src = str(dep)
            if src == dst:
                continue
            weight = cfg.weight_override(src, dst)
            edges.append(
                Edge(
                    src=src,
                    dst=dst,
                    weight=cfg.default_weight if weight is None else weight,
                    sources=("declared",),
                )
            )
    return edges


def infer_edges_from_evidence(
    ledger_path: Path, cfg: GraphConfig, node_ids: Iterable[str]
) -> List[Edge]:
    """保守推断边：下游能力的证据记录里 ≥min_mentions 次提到上游能力 id。"""
    spec = (cfg.raw["edges"].get("inference") or {})
    if not spec.get("enabled", False):
        return []
    min_mentions = int(spec.get("min_mentions", 2))
    data = load_json(ledger_path)
    entries = data.get("capabilities") if isinstance(data, dict) else data
    ids = sorted({str(i) for i in node_ids})
    inferred: List[Edge] = []
    for entry in entries or []:
        if not isinstance(entry, Mapping):
            continue
        dst = str(entry.get("id") or "")
        if not dst:
            continue
        records = entry.get("evidence") or []
        for src in ids:
            if src == dst:
                continue
            mentions = 0
            for rec in records:
                if not isinstance(rec, Mapping):
                    continue
                blob = " ".join(
                    str(rec.get(k) or "") for k in ("note", "quote_match", "action", "title")
                )
                if src in blob:
                    mentions += 1
            if mentions >= min_mentions:
                weight = cfg.weight_override(src, dst)
                inferred.append(
                    Edge(
                        src=src,
                        dst=dst,
                        weight=cfg.inferred_weight if weight is None else weight,
                        sources=("ledger_evidence_text",),
                        corroborations=mentions,
                    )
                )
    return inferred


def build_snapshot(
    cfg: GraphConfig,
    *,
    extra_edges: Sequence[Edge] = (),
    extra_levels: Optional[Mapping[str, str]] = None,
) -> Snapshot:
    """构建依赖图谱快照。

    节点 = 台账条目 ∪ 依赖边端点（边的端点若不是台账条目，登记为 ``role="dangling"``
    并以 ``edges`` 里的保守等级 ``dangling_level`` 占位，绝不当成 REAL）。
    """
    import datetime as _dt

    levels, unparsable = load_levels(cfg.sources["ledger"], cfg)
    if extra_levels:
        for k, v in extra_levels.items():
            levels[str(k)] = normalise_level(v, cfg.level_rank)
    sem_cfg = load_json(cfg.sources["semantics_config"])
    declared = load_declared_edges(cfg.sources["semantics_config"], cfg)
    labels = {
        str(c.get("id")): str(c.get("label") or "")
        for c in (sem_cfg.get("capabilities") or [])
        if c.get("id")
    }
    roles = {
        str(c.get("id")): str(c.get("graph_role") or "domain")
        for c in (sem_cfg.get("capabilities") or [])
        if c.get("id")
    }
    edges_all = list(declared) + list(extra_edges)
    node_ids = set(levels) | {e.src for e in edges_all} | {e.dst for e in edges_all}
    inferred = infer_edges_from_evidence(cfg.sources["ledger"], cfg, node_ids)
    edges = dedupe_edges(edges_all + inferred)

    dangling_level = str((cfg.raw["edges"].get("dangling_level") or "NOT_SUPPORTED"))
    normalise_level(dangling_level, cfg.level_rank)  # 校验配置合法
    nodes: Dict[str, Node] = {}
    for node_id in sorted(node_ids):
        level = levels.get(node_id)
        source = "ledger"
        if level is None:
            level, source = dangling_level, "dangling"
        nodes[node_id] = Node(
            node_id=node_id,
            own_level=level,
            own_confidence=confidence_of(level, cfg.level_confidence),
            role=roles.get(node_id, "domain" if source == "ledger" else "dangling"),
            label=labels.get(node_id, ""),
            level_source=source,
        )
    meta = {
        "generated_at": _dt.datetime.now(_dt.timezone.utc).isoformat(),
        "sources": {k: str(v) for k, v in cfg.sources.items()},
        "unparsable_levels": unparsable,
        "declared_edge_count": len(declared),
        "inferred_edge_count": len(inferred),
        "default_weight": cfg.default_weight,
        "inferred_weight": cfg.inferred_weight,
    }
    return Snapshot(nodes=nodes, edges=edges, meta=meta)