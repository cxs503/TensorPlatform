"""否定性知识台账配置。

所有阈值/表名/字段映射从配置文件或环境变量装载，本模块不含字面量阈值。
配置文件路径：``config/negative_knowledge.json``（缺省用内置默认值）。
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

_BACKEND_DIR = Path(__file__).resolve().parents[2]
_DEFAULT_CONFIG_PATH = _BACKEND_DIR / "config" / "negative_knowledge.json"

# 默认值（全部可被配置文件覆盖）
_DEFAULTS: dict[str, Any] = {
    "table_name": "negative_knowledge_ledger",
    "search": {
        "shingle_size": 4,
        "min_score_threshold": 0.25,
        "gate_reject_threshold": 0.6,
        "gate_review_threshold": 0.4,
        "containment_enabled": True,
        "containment_weight": 0.8,
        "min_length_ratio": 0.3,
        "top_k": 5,
    },
    "failure_kinds": ("hypothesis_falsified", "method_failed", "blocked", "infeasible", "regression"),
    "schema_version": "1.0",
}


@dataclass
class NegativeKnowledgeConfig:
    """否定性知识台账配置（不可变快照）。"""

    table_name: str
    search_shingle_size: int
    search_min_score: float
    gate_reject_threshold: float
    gate_review_threshold: float
    search_containment_enabled: bool
    search_containment_weight: float
    search_min_length_ratio: float
    search_top_k: int
    failure_kinds: tuple[str, ...]
    schema_version: str
    path: Path | None = None
    _raw: dict[str, Any] = field(default_factory=dict, repr=False)

    def get(self, dotted_key: str, default: Any = None) -> Any:
        """点分路径取值（兼容旧调用风格）。"""
        node: Any = self._raw
        for part in dotted_key.split("."):
            if isinstance(node, dict) and part in node:
                node = node[part]
            else:
                return default
        return node

    def need(self, dotted_key: str) -> Any:
        val = self.get(dotted_key)
        if val is None:
            raise KeyError(f"配置缺失：{dotted_key}")
        return val


def load_config(path: str | Path | None = None) -> NegativeKnowledgeConfig:
    """装载配置（文件缺失时降级到内置默认值，不抛异常）。"""
    config_path = Path(path) if path else _DEFAULT_CONFIG_PATH
    raw: dict[str, Any] = {}
    if config_path.is_file():
        try:
            raw = json.loads(config_path.read_text(encoding="utf-8"))
        except Exception:
            raw = {}

    search = {**_DEFAULTS["search"], **raw.get("search", {})}

    return NegativeKnowledgeConfig(
        table_name=raw.get("table_name", _DEFAULTS["table_name"]),
        search_shingle_size=int(search["shingle_size"]),
        search_min_score=float(search["min_score_threshold"]),
        gate_reject_threshold=float(search["gate_reject_threshold"]),
        gate_review_threshold=float(search["gate_review_threshold"]),
        search_containment_enabled=bool(search.get("containment_enabled", True)),
        search_containment_weight=float(search.get("containment_weight", 0.8)),
        search_min_length_ratio=float(search.get("min_length_ratio", 0.3)),
        search_top_k=int(search.get("top_k", 5)),
        failure_kinds=tuple(raw.get("failure_kinds", _DEFAULTS["failure_kinds"])),
        schema_version=raw.get("schema_version", _DEFAULTS["schema_version"]),
        path=config_path if config_path.is_file() else None,
        _raw=raw,
    )
