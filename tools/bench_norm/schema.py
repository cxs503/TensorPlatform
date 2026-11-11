"""Observation schema for cross-architecture benchmark normalisation.

A single timing observation is one (host, workload, repetition) triple.
Everything downstream (alpha estimation, mixed-effects fit, criteria) is a
pure function of a list of these records, so the measurement layer and the
inference layer are decoupled: records can come from a live host, from a
cluster node, or from a declared injection model (simulation layer).

Records are plain dicts (JSONL / JSON friendly) with a strict validator;
no physical/domain semantics live here -- only generic timing quantities.
"""
from __future__ import annotations

import json
import math
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Dict, Iterable, List, Sequence

SCHEMA_VERSION = "bench_norm.observation.v1"

REQUIRED_FIELDS = (
    "arm",            # architecture / host arm identifier, e.g. "loongarch64@3C6000:99"
    "isa",            # "loongarch64" | "x86_64" | ...
    "host",           # measurement host identifier
    "workload",       # workload name from config (data, not code)
    "features",       # numeric feature dict, e.g. {"nx":64,"ny":64,"steps":2000}
    "repetition",     # 0-based repetition index
    "t_seconds",      # measured wall time of the timed region (seconds)
    "source",         # "measured" | "injected"
)


@dataclass
class Observation:
    arm: str
    isa: str
    host: str
    workload: str
    features: Dict[str, float]
    repetition: int
    t_seconds: float
    source: str = "measured"
    elapsed_seconds: float | None = None      # process wall clock, if known
    checksum: str | None = None
    meta: Dict[str, Any] = field(default_factory=dict)
    schema: str = SCHEMA_VERSION

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def _check(obs: Dict[str, Any], idx: int) -> None:
    for k in REQUIRED_FIELDS:
        if k not in obs:
            raise ValueError(f"observation[{idx}] missing field {k!r}")
    if obs["source"] not in ("measured", "injected"):
        raise ValueError(f"observation[{idx}] bad source {obs['source']!r}")
    t = obs["t_seconds"]
    if not isinstance(t, (int, float)) or not math.isfinite(t) or t <= 0:
        raise ValueError(f"observation[{idx}] t_seconds must be finite and >0, got {t!r}")
    if not isinstance(obs["features"], dict) or not obs["features"]:
        raise ValueError(f"observation[{idx}] features must be a non-empty dict")
    for k, v in obs["features"].items():
        if not isinstance(v, (int, float)) or not math.isfinite(float(v)):
            raise ValueError(f"observation[{idx}] feature {k!r} non numeric: {v!r}")


def validate(records: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    if not records:
        raise ValueError("empty observation set")
    for i, r in enumerate(records):
        _check(r, i)
    arms = {r["arm"] for r in records}
    if len(arms) < 2:
        raise ValueError(f"need at least 2 arms for normalisation, got {sorted(arms)}")
    return list(records)


def write_jsonl(records: Iterable[Dict[str, Any]], path: str | Path) -> Path:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("w", encoding="utf-8") as fh:
        for r in records:
            fh.write(json.dumps(r, sort_keys=True, ensure_ascii=False) + "\n")
    return p


def read_jsonl(path: str | Path) -> List[Dict[str, Any]]:
    out = []
    with Path(path).open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                out.append(json.loads(line))
    return out


def write_json(obj: Any, path: str | Path) -> Path:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(obj, indent=2, sort_keys=True, ensure_ascii=False), encoding="utf-8")
    return p


def read_json(path: str | Path) -> Any:
    return json.loads(Path(path).read_text(encoding="utf-8"))