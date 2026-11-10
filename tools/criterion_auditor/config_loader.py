"""配置装载：参数与词典只在 ``config/criterion_auditor.json``。

纪律：代码里不允许出现阈值/词典字面量——一律 ``cfg.get(path)``。
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

CONFIG_ENV = "CRITERION_AUDITOR_CONFIG"


class ConfigError(RuntimeError):
    """配置缺失/不合法（fail-closed）。"""


def repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def default_config_path() -> Path:
    env = os.environ.get(CONFIG_ENV)
    if env:
        return Path(env)
    return repo_root() / "config" / "criterion_auditor.json"


@dataclass(frozen=True)
class AuditorConfig:
    """只读参数包。"""

    data: Mapping[str, Any]
    path: Path | None = None

    @classmethod
    def from_dict(cls, data: Mapping[str, Any], path: Path | None = None) -> "AuditorConfig":
        if not isinstance(data, Mapping):
            raise ConfigError("config 必须是对象")
        return cls(data=dict(data), path=path)

    @classmethod
    def load(cls, path: str | Path | None = None) -> "AuditorConfig":
        p = Path(path) if path is not None else default_config_path()
        if not p.exists():
            raise ConfigError(f"配置不存在：{p}")
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise ConfigError(f"配置不是合法 JSON：{p}: {exc}") from exc
        return cls.from_dict(data, path=p)

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

    def resolve_path(self, rel: str) -> Path:
        p = Path(rel)
        return p if p.is_absolute() else repo_root() / p

    # ── 便捷访问 ──────────────────────────────────────────────────────
    def quantity_patterns(self) -> list[dict]:
        return list(self.get("extraction.quantity_patterns.entries", []))

    def criterion_form_patterns(self) -> list[dict]:
        return list(self.get("extraction.criterion_form_patterns.entries", []))

    def domain_patterns(self) -> list[dict]:
        return list(self.get("extraction.domain_patterns.entries", []))

    def slot_weights(self) -> dict[str, float]:
        return dict(self.get("alignment.slot_weights", {"quantity": 0.45, "criterion_form": 0.30, "domain": 0.25}))

    def tautology_patterns(self) -> list[dict]:
        return list(self.get("strength_audit.tautology_patterns", []))

    def adjudication_weights(self) -> dict[str, float]:
        return dict(self.get("adjudication", {}))


def load_config(path: str | Path | None = None) -> AuditorConfig:
    return AuditorConfig.load(path)
