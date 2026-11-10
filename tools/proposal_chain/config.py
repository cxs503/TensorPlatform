"""配置装载：``config/proposal_chain.json`` 是唯一参数来源（代码内无字面量阈值/无域词）。

设计要点
--------
* 配置缺失/损坏 → :class:`ConfigError`（**显式失败**，不做静默默认值）；
  但**数据**缺失（空池、节点无 metadata）→ 恒等降级不崩（见 ``dedup`` / ``node_schema``）。
* :func:`load_config` 支持 ``overrides`` 深合并（测试注入阈值，避免字面量散落在测试里）。
* 所有路径相对**仓库根**解析（``REPO_ROOT`` = 本文件上溯三级），便于在任意 cwd 跑。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG_PATH = REPO_ROOT / "config" / "proposal_chain.json"


class ConfigError(RuntimeError):
    """配置缺失/结构不合法（fail-fast；与数据缺口的恒等降级区分开）。"""


def deep_merge(base: Mapping[str, Any], override: Mapping[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = dict(base)
    for key, value in override.items():
        if isinstance(value, Mapping) and isinstance(out.get(key), Mapping):
            out[key] = deep_merge(out[key], value)  # type: ignore[arg-type]
        else:
            out[key] = value
    return out


@dataclass(frozen=True)
class ChainConfig:
    raw: dict[str, Any] = field(default_factory=dict)
    path: Path = DEFAULT_CONFIG_PATH

    # ---- 分段访问（每段缺失即 ConfigError，避免下游 KeyError 变噪音）----
    def section(self, name: str) -> dict[str, Any]:
        value = self.raw.get(name)
        if not isinstance(value, dict):
            raise ConfigError(f"config section '{name}' missing or not an object")
        return dict(value)

    @property
    def repo_whitelist(self) -> list[str]:
        value = self.raw.get("repo_whitelist")
        if not isinstance(value, list) or not value:
            raise ConfigError("config 'repo_whitelist' missing or empty")
        return [str(x) for x in value]

    def path_of(self, key: str, *, base: Path | None = None) -> Path:
        paths = self.section("paths")
        rel = paths.get(key)
        if not rel:
            raise ConfigError(f"config paths.{key} missing")
        p = Path(str(rel))
        return p if p.is_absolute() else (base or REPO_ROOT) / p

    def get(self, dotted: str, default: Any = None) -> Any:
        node: Any = self.raw
        for part in dotted.split("."):
            if not isinstance(node, Mapping) or part not in node:
                return default
            node = node[part]
        return node


def load_config(
    path: str | Path | None = None, overrides: Mapping[str, Any] | None = None
) -> ChainConfig:
    cfg_path = Path(path) if path else DEFAULT_CONFIG_PATH
    if not cfg_path.exists():
        raise ConfigError(f"config not found: {cfg_path}")
    try:
        raw = json.loads(cfg_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:  # pragma: no cover - 配置损坏
        raise ConfigError(f"config not valid json: {cfg_path}: {exc}") from exc
    if not isinstance(raw, dict):
        raise ConfigError(f"config root must be object: {cfg_path}")
    if overrides:
        raw = deep_merge(raw, overrides)
    return ChainConfig(raw=raw, path=cfg_path)