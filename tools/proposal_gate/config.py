"""配置装载：参数与词表只在 ``config/proposal_gate.json``（+ 其引用的域词黑名单）。

纪律
----
* 代码里不允许出现阈值/白名单/词表字面量 —— 一律 ``cfg.get(path)``。
* 找不到配置文件时**不静默降级为内置默认值**（那是把常量搬回代码），而是抛
  :class:`ConfigError`，让调用方显式决定（fail-closed）。测试用 ``GateConfig.from_dict``
  显式构造合成配置（夹具用合成域名 foo/bar/widget）。
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

CONFIG_ENV = "PROPOSAL_GATE_CONFIG"


class ConfigError(RuntimeError):
    """配置缺失/不合法（fail-closed，不回落内置默认）。"""


def repo_root() -> Path:
    """仓库根（本文件位于 <root>/tools/proposal_gate/config.py）。"""
    return Path(__file__).resolve().parents[2]


def default_config_path() -> Path:
    env = os.environ.get(CONFIG_ENV)
    if env:
        return Path(env)
    return repo_root() / "config" / "proposal_gate.json"


@dataclass(frozen=True)
class GateConfig:
    """只读参数包：一层 dict 视图 + 点号取值。"""

    data: Mapping[str, Any]
    path: Path | None = None

    # ── 构造 ────────────────────────────────────────────────────────────
    @classmethod
    def from_dict(cls, data: Mapping[str, Any], path: Path | None = None) -> "GateConfig":
        if not isinstance(data, Mapping):
            raise ConfigError("config 必须是对象")
        cfg = cls(data=dict(data), path=path)
        cfg._require(
            "repo_whitelist",
            "dedup.dup_threshold",
            "dedup.twin_dependency_overlap",
            "dedup.shingle_size",
            "contract.min_criteria",
            "contract.numeric_pattern",
            "ledger.table",
            "states",
        )
        return cfg

    @classmethod
    def load(cls, path: str | Path | None = None) -> "GateConfig":
        p = Path(path) if path is not None else default_config_path()
        if not p.exists():
            raise ConfigError(f"配置不存在：{p}（禁止回落内置默认值）")
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:  # pragma: no cover - 坏配置文件
            raise ConfigError(f"配置不是合法 JSON：{p}: {exc}") from exc
        return cls.from_dict(data, path=p)

    # ── 取值 ────────────────────────────────────────────────────────────
    def get(self, dotted: str, default: Any = None) -> Any:
        cur: Any = self.data
        for part in dotted.split("."):
            if not isinstance(cur, Mapping) or part not in cur:
                return default
            cur = cur[part]
        return cur

    def need(self, dotted: str) -> Any:
        sentinel = object()
        value = self.get(dotted, sentinel)
        if value is sentinel:
            raise ConfigError(f"缺配置项：{dotted}")
        return value

    def _require(self, *keys: str) -> None:
        for k in keys:
            self.need(k)

    # ── 便捷属性（全部来自配置，无内置字面量）──────────────────────────
    @property
    def repo_whitelist(self) -> tuple[str, ...]:
        return tuple(self.need("repo_whitelist"))

    @property
    def table_name(self) -> str:
        return str(self.need("ledger.table"))

    @property
    def state_map(self) -> dict[str, str]:
        return dict(self.need("states"))

    def state_for(self, verdict: str) -> str:
        mapping = self.state_map
        if verdict not in mapping:
            raise ConfigError(f"verdict 未在 states 中登记：{verdict}")
        return mapping[verdict]

    def resolve_path(self, rel: str) -> Path:
        p = Path(rel)
        return p if p.is_absolute() else repo_root() / p

    def domain_vocab(self) -> tuple[list[str], list[str]]:
        """域词黑名单（从配置注入；文件缺失 → 空表，不崩）。"""
        rel = self.get("domain_vocab_file")
        if not rel:
            return [], []
        path = self.resolve_path(rel)
        if not path.exists():
            return [], []
        data = json.loads(path.read_text(encoding="utf-8"))
        ascii_words = [w for w in data.get("ascii_words", []) if isinstance(w, str)]
        cjk_words = [w for w in data.get("cjk_words", []) if isinstance(w, str)]
        return ascii_words, cjk_words


def load_config(path: str | Path | None = None) -> GateConfig:
    return GateConfig.load(path)


@dataclass
class SyntheticConfigBuilder:  # pragma: no cover - 仅测试辅助门面
    """测试夹具：以合成域名（foo/bar/widget）构造配置，避免依赖真仓库清单。"""

    extra: dict[str, Any] = field(default_factory=dict)

    def build(self, base: GateConfig) -> GateConfig:
        data = dict(base.data)
        data.update(self.extra)
        return GateConfig.from_dict(data)