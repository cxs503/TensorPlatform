"""v3 spec 装载与校验：21 项能力全覆盖探针规范。"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional
from dataclasses import dataclass


@dataclass
class ProbeSpec:
    """单个探针规范。"""
    probe_id: str
    capability: str
    probe_type: str  # IDENTITY | SENSITIVITY | DETERMINISM | BOUNDARY | EXISTENCE
    endpoint: str
    method: str  # GET | POST
    input_base: Dict[str, Any]
    input_variants: List[Dict[str, Any]]
    check_expr: str
    tolerance: float
    description: str


@dataclass
class SpecV3:
    """v3 审计规范。"""
    schema_version: str
    title: str
    capabilities: Dict[str, Dict[str, Any]]
    thresholds: Dict[str, float]
    probes: List[ProbeSpec]


def load_spec_v3(path: str | Path) -> SpecV3:
    """装载 v3 spec。"""
    p = Path(path)
    if not p.is_file():
        raise ValueError(f"Spec not found: {p}")
    raw = json.loads(p.read_text(encoding="utf-8"))
    
    probes = []
    for cap_id, cap_spec in raw.get("capabilities", {}).items():
        for probe in cap_spec.get("probes", []):
            probes.append(ProbeSpec(
                probe_id=probe["id"],
                capability=cap_id,
                probe_type=probe["type"],
                endpoint=probe["endpoint"],
                method=probe.get("method", "POST"),
                input_base=probe.get("input_base", {}),
                input_variants=probe.get("input_variants", []),
                check_expr=probe.get("check_expr", ""),
                tolerance=probe.get("tolerance", 0.01),
                description=probe.get("description", ""),
            ))

    return SpecV3(
        schema_version=raw.get("schema_version", "3.0"),
        title=raw.get("title", ""),
        capabilities=raw.get("capabilities", {}),
        thresholds=raw.get("thresholds", {}),
        probes=probes,
    )
