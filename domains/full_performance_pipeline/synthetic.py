"""合成船型/环境输入（合成域名，零真实船名；所有系数集中在 config/*.json）。

命名纪律：船型只叫 SYNTH-A/B/C，环境只叫 SEA-1/2/3 —— 便于在任何仓库/测试里
引用而不引入真实船型语义；也便于审计“这些数字不是从某份实船资料抄来的”。
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field as dc_field
from typing import Any, Dict, List

HERE = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(HERE, "config", "synthetic_pipeline_config.json")
MS_PER_KNOT = 0.5144444444444445


def load_config(path: str = CONFIG_PATH) -> Dict[str, Any]:
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


@dataclass
class Scenario:
    """一个端到端算例：船型 + 环境 + 航线 + 拖曳/积水工况。"""

    key: str
    vessel: Dict[str, Any]
    environment: Dict[str, Any]
    route: Dict[str, Any]
    deck: Dict[str, Any]
    towing: Dict[str, Any]
    extra: Dict[str, Any] = dc_field(default_factory=dict)

    def config_flat(self) -> Dict[str, Any]:
        """把所有配置注入项摊平成一个扁平字典（供契约“配置注入”判定用）。"""
        flat: Dict[str, Any] = {}
        for src in (self.vessel, self.environment, self.route, self.deck, self.towing,
                    self.extra):
            flat.update(src)
        return flat


def _vessel(cfg: Dict[str, Any], name: str) -> Dict[str, Any]:
    v = dict(cfg["vessels"][name])
    v["speed_reference_ms"] = v.pop("speed_reference_kn") * MS_PER_KNOT
    return v


def _environment(cfg: Dict[str, Any], name: str) -> Dict[str, Any]:
    return dict(cfg["environments"][name])


def build_scenarios(cfg: Dict[str, Any] | None = None) -> List[Scenario]:
    cfg = cfg or load_config()
    out: List[Scenario] = []
    for spec in cfg["scenarios"]:
        out.append(Scenario(
            key=spec["key"],
            vessel=_vessel(cfg, spec["vessel"]),
            environment=_environment(cfg, spec["environment"]),
            route=dict(spec["route"]),
            deck={**cfg.get("deck", {}), **spec.get("deck", {})},
            towing={**cfg.get("towing", {}), **spec.get("towing", {})},
            extra=dict(cfg.get("wind_coefficients", {})),
        ))
    return out


def scenario(key: str, cfg: Dict[str, Any] | None = None) -> Scenario:
    for s in build_scenarios(cfg):
        if s.key == key:
            return s
    raise KeyError(key)