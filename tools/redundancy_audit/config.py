"""配置装载（fail-closed）。

纪律
----
* 代码里不出现阈值/权重/词表字面量：一律 ``cfg.get("a.b")``。
* 配置文件缺失或不合法 → 抛 :class:`RedundancyConfigError`，**不**静默回落到内置默认
  （那是把常量搬回代码，等于把可调性做假）。
* 测试用 :meth:`RedundancyConfig.from_dict` 显式构造合成配置（合成域名 foo/bar/widget）。

与 :mod:`tools.proposal_gate` 的关系
------------------------------------
相似度口径（NFKC 归一化 + 字符 n-gram + Jaccard/受控 containment）直接复用
:mod:`tools.proposal_gate.textsim`；本模块只负责把本审计的参数视图映射成
``GateConfig`` 所需的 ``dedup.*`` 键，避免第二套相似度实现。
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

CONFIG_ENV = "REDUNDANCY_AUDIT_CONFIG"

_REQUIRED = (
    "similarity.shingle_size",
    "similarity.containment_weight",
    "similarity.min_length_ratio",
    "similarity.min_chars",
    "ann.min_shared_shingles",
    "ann.posting_cap",
    "ann.max_candidates_per_node",
    "thresholds.strong",
    "thresholds.weak",
    "thresholds.sensitivity",
    "thresholds.sweep_grid",
    "ledger.status_evidence_priority",
    "ledger.archive_statuses",
    "ledger.cost_fields",
    "policy.keeper_rule",
    "policy.non_keeper_rule",
    "labels.pairs",
    "labels.true_group_count",
    "labels.hard_negative_ratio",
    "labels.random_negative_ratio",
    "labels.f1_min_accept",
    "labels.f1_min_viable",
    "labels.hard_negative_fp_max",
    "labels.true_group_recall_min",
    "paths.snapshot",
    "paths.labels",
    "paths.prereg",
    "paths.report",
    "paths.figure_dir",
    "audit.idempotency_repeats",
)


class RedundancyConfigError(RuntimeError):
    """配置缺失/不合法（fail-closed）。"""


def repo_root() -> Path:
    """仓库根（本文件位于 <root>/tools/redundancy_audit/config.py）。"""
    return Path(__file__).resolve().parents[2]


def default_config_path() -> Path:
    env = os.environ.get(CONFIG_ENV)
    if env:
        return Path(env)
    return repo_root() / "config" / "redundancy_audit.json"


@dataclass(frozen=True)
class RedundancyConfig:
    """只读参数包：一层 dict + 点号取值。"""

    data: Mapping[str, Any]
    path: Path | None = None

    # ── 构造 ────────────────────────────────────────────────────────────
    @classmethod
    def from_dict(cls, data: Mapping[str, Any], path: Path | None = None) -> "RedundancyConfig":
        if not isinstance(data, Mapping):
            raise RedundancyConfigError("config 必须是对象")
        cfg = cls(data=dict(data), path=path)
        for key in _REQUIRED:
            cfg.get(key)  # 缺失即抛
        strong = float(cfg.get("thresholds.strong"))
        weak = float(cfg.get("thresholds.weak"))
        if not 0.0 < weak <= strong <= 1.0:
            raise RedundancyConfigError(
                f"阈值必须满足 0 < weak({weak}) <= strong({strong}) <= 1"
            )
        if int(cfg.get("similarity.shingle_size")) < 1:
            raise RedundancyConfigError("similarity.shingle_size 必须 >= 1")
        return cfg

    @classmethod
    def load(cls, path: Path | str | None = None) -> "RedundancyConfig":
        p = Path(path) if path else default_config_path()
        if not p.exists():
            raise RedundancyConfigError(f"配置不存在: {p}")
        with open(p, encoding="utf-8") as fh:
            doc = json.load(fh)
        return cls.from_dict(doc, path=p)

    # ── 取值 ────────────────────────────────────────────────────────────
    def get(self, dotted: str, default: Any = _REQUIRED) -> Any:
        node: Any = self.data
        for part in dotted.split("."):
            if not isinstance(node, Mapping) or part not in node:
                if default is _REQUIRED:
                    raise RedundancyConfigError(f"配置缺少必填键 {dotted!r}（来源 {self.path}）")
                return default
            node = node[part]
        return node

    def path_of(self, key: str) -> Path:
        """``paths.*`` 项解析为仓库内绝对路径。"""
        return repo_root() / str(self.get(key))

    # ── 与 proposal_gate 的桥接 ─────────────────────────────────────────
    def gate_config(self):
        """把相似度参数映射成 :class:`tools.proposal_gate.config.GateConfig`。

        只填 ``textsim`` 会读到的键；其余键给最小合法占位（本审计不使用提案门的
        契约/台账能力）。映射是单向、显式的，因此相似度语义唯一。
        """
        from tools.proposal_gate.config import GateConfig

        return GateConfig.from_dict(
            {
                "repo_whitelist": ["repo"],
                "dedup": {
                    "shingle_size": int(self.get("similarity.shingle_size")),
                    "dup_threshold": float(self.get("thresholds.strong")),
                    "twin_dependency_overlap": 1.0,
                    "containment_enabled": True,
                    "containment_weight": float(self.get("similarity.containment_weight")),
                    "min_length_ratio": float(self.get("similarity.min_length_ratio")),
                },
                "contract": {"min_criteria": 1, "numeric_pattern": "[0-9]+"},
                "ledger": {"table": "redundancy_audit"},
                "states": ["new", "accepted", "rejected"],
            }
        )