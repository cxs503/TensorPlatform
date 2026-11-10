"""Benchmark configuration for the evidence pipeline (domain-neutral).

A benchmark configuration is the *only* input the runner needs.  It declares

* which generator script produces the numbers (``generator``),
* the parameters handed to that generator (``params``),
* which gates the emitted metrics/fields must satisfy (``gates``), and
* how the run is compared against a committed baseline (``regression``).

Everything is validated eagerly: an unknown gate type, a threshold without a
bound, a missing generator file or a negative tolerance raises
:class:`~tools.evidence_pipeline.PipelineError` at load time.  A configuration
that cannot be judged must never reach the executor.

The canonical digest (:meth:`BenchmarkConfig.digest`) uses
:func:`tools.coupling.provenance.canonical_digest`, so floats are compared in
their exact hexadecimal form and two textually different but numerically equal
JSON files digest identically.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field as _dc_field
from pathlib import Path
from typing import Any, Dict, Mapping, Sequence, Tuple

from tools.coupling.provenance import canonical_digest

from . import PipelineError

ROOT = Path(__file__).resolve().parents[2]

GATE_TYPES: Tuple[str, ...] = ("numeric", "conservation")
CONSERVATION_METRICS: Tuple[str, ...] = (
    "sum_rel_residual",
    "moment_rel_residual",
    "nonneg_violation",
    "overshoot_ratio",
)


def _as_float(value: Any, what: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise PipelineError(f"{what} must be a number, got {value!r}")
    return float(value)


@dataclass(frozen=True)
class GateSpec:
    """One gate: an id, a type, and the thresholds that type consumes."""

    id: str
    type: str
    thresholds: Dict[str, Any] = _dc_field(default_factory=dict)
    fields: Dict[str, str] = _dc_field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "type": self.type,
            "thresholds": dict(self.thresholds),
            "fields": dict(self.fields),
        }

    @classmethod
    def parse(cls, data: Mapping[str, Any], index: int) -> "GateSpec":
        if not isinstance(data, Mapping):
            raise PipelineError(f"gates[{index}] must be an object")
        gate_id = data.get("id")
        if not isinstance(gate_id, str) or not gate_id:
            raise PipelineError(f"gates[{index}].id must be a non-empty string")
        gate_type = data.get("type")
        if gate_type not in GATE_TYPES:
            raise PipelineError(
                f"gate {gate_id!r}: unknown type {gate_type!r}; allowed {GATE_TYPES}"
            )
        raw_thresholds = data.get("thresholds", {})
        if not isinstance(raw_thresholds, Mapping) or not raw_thresholds:
            raise PipelineError(f"gate {gate_id!r}: thresholds must be a non-empty object")

        if gate_type == "numeric":
            thresholds: Dict[str, Any] = {}
            for metric, bound in raw_thresholds.items():
                if not isinstance(bound, Mapping):
                    raise PipelineError(
                        f"gate {gate_id!r}: numeric bound for {metric!r} must be an object"
                    )
                unknown = set(bound) - {"min", "max"}
                if unknown:
                    raise PipelineError(
                        f"gate {gate_id!r}: unsupported bound keys {sorted(unknown)} for {metric!r}"
                    )
                if not bound:
                    raise PipelineError(
                        f"gate {gate_id!r}: metric {metric!r} has neither min nor max"
                    )
                parsed_bound = {}
                for key, value in bound.items():
                    parsed_bound[key] = _as_float(value, f"{gate_id}.{metric}.{key}")
                if "min" in parsed_bound and "max" in parsed_bound:
                    if parsed_bound["min"] > parsed_bound["max"]:
                        raise PipelineError(
                            f"gate {gate_id!r}: metric {metric!r} has min > max"
                        )
                thresholds[str(metric)] = parsed_bound
            return cls(id=gate_id, type=gate_type, thresholds=thresholds, fields={})

        # conservation gate
        thresholds = {}
        for metric, value in raw_thresholds.items():
            if metric not in CONSERVATION_METRICS:
                raise PipelineError(
                    f"gate {gate_id!r}: unknown conservation threshold {metric!r}; "
                    f"allowed {CONSERVATION_METRICS}"
                )
            thresholds[str(metric)] = _as_float(value, f"{gate_id}.{metric}")
            if thresholds[str(metric)] < 0.0:
                raise PipelineError(f"gate {gate_id!r}: threshold {metric!r} must be >= 0")
        missing = [m for m in CONSERVATION_METRICS if m not in thresholds]
        if missing:
            raise PipelineError(
                f"gate {gate_id!r}: conservation gate must declare every metric; "
                f"missing {missing}"
            )
        if thresholds.get("overshoot_ratio", 0.0) < 1.0:
            raise PipelineError(
                f"gate {gate_id!r}: overshoot_ratio is a bound on the ratio and must be >= 1.0 "
                f"(got {thresholds['overshoot_ratio']})"
            )
        raw_fields = data.get("fields", {"src": "src", "dst": "dst"})
        if not isinstance(raw_fields, Mapping):
            raise PipelineError(f"gate {gate_id!r}: fields must be an object")
        fields = {}
        for role in ("src", "dst"):
            key = raw_fields.get(role, role)
            if not isinstance(key, str) or not key:
                raise PipelineError(f"gate {gate_id!r}: fields.{role} must be a string")
            fields[role] = key
        unknown_roles = set(raw_fields) - {"src", "dst"}
        if unknown_roles:
            raise PipelineError(
                f"gate {gate_id!r}: unsupported field roles {sorted(unknown_roles)}"
            )
        return cls(id=gate_id, type=gate_type, thresholds=thresholds, fields=fields)


@dataclass(frozen=True)
class RegressionSpec:
    """Baseline comparison policy."""

    enabled: bool = True
    baseline: str = ""          # path relative to ROOT, or absolute
    tol: float = 1e-12          # relative drift tolerance per metric
    reruns: int = 2             # re-runs triggered when drift is detected
    floors: Dict[str, float] = _dc_field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "enabled": self.enabled,
            "baseline": self.baseline,
            "tol": self.tol,
            "reruns": self.reruns,
            "floors": dict(self.floors),
        }

    @classmethod
    def parse(cls, data: Mapping[str, Any] | None) -> "RegressionSpec":
        if data is None:
            return cls()
        if not isinstance(data, Mapping):
            raise PipelineError("regression must be an object")
        enabled = bool(data.get("enabled", True))
        baseline = data.get("baseline", "")
        if not isinstance(baseline, str):
            raise PipelineError("regression.baseline must be a string path")
        tol = _as_float(data.get("tol", 1e-12), "regression.tol")
        if tol < 0.0:
            raise PipelineError("regression.tol must be >= 0")
        reruns = data.get("reruns", 2)
        if isinstance(reruns, bool) or not isinstance(reruns, int) or reruns < 0:
            raise PipelineError("regression.reruns must be a non-negative integer")
        raw_floors = data.get("floors", {})
        if not isinstance(raw_floors, Mapping):
            raise PipelineError("regression.floors must be an object")
        floors = {str(k): _as_float(v, f"regression.floors.{k}") for k, v in raw_floors.items()}
        return cls(enabled=enabled, baseline=baseline, tol=tol, reruns=reruns, floors=floors)


@dataclass(frozen=True)
class BenchmarkConfig:
    """A fully validated benchmark declaration."""

    node: str
    generator: str                       # path relative to ROOT (or absolute)
    params: Dict[str, Any]
    gates: Tuple[GateSpec, ...]
    regression: RegressionSpec = _dc_field(default_factory=RegressionSpec)
    timeout_s: int = 120
    source: str = "<memory>"
    raw: Dict[str, Any] = _dc_field(default_factory=dict, repr=False, compare=False)

    # ------------------------------------------------------------------ paths
    @property
    def generator_path(self) -> Path:
        p = Path(self.generator)
        return p if p.is_absolute() else (ROOT / p)

    @property
    def baseline_path(self) -> Path:
        p = Path(self.regression.baseline) if self.regression.baseline else Path()
        if str(p) in ("", "."):
            return Path()
        return p if p.is_absolute() else (ROOT / p)

    # ---------------------------------------------------------------- digests
    def digest(self) -> str:
        return canonical_digest(
            {
                "node": self.node,
                "generator": self.generator,
                "params": self.params,
                "gates": [g.to_dict() for g in self.gates],
                "regression": self.regression.to_dict(),
                "timeout_s": self.timeout_s,
            }
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "node": self.node,
            "generator": self.generator,
            "params": dict(self.params),
            "gates": [g.to_dict() for g in self.gates],
            "regression": self.regression.to_dict(),
            "timeout_s": self.timeout_s,
        }

    # ------------------------------------------------------------------ parse
    @classmethod
    def parse(cls, data: Mapping[str, Any], source: str = "<memory>") -> "BenchmarkConfig":
        if not isinstance(data, Mapping):
            raise PipelineError(f"{source}: benchmark config must be a JSON object")
        node = data.get("node")
        if not isinstance(node, str) or not node.strip():
            raise PipelineError(f"{source}: 'node' must be a non-empty string")
        generator = data.get("generator")
        if not isinstance(generator, str) or not generator:
            raise PipelineError(f"{source}: 'generator' must be a non-empty path string")
        params = data.get("params", {})
        if not isinstance(params, Mapping):
            raise PipelineError(f"{source}: 'params' must be an object")
        raw_gates = data.get("gates")
        if not isinstance(raw_gates, Sequence) or isinstance(raw_gates, (str, bytes)) or not raw_gates:
            raise PipelineError(f"{source}: 'gates' must be a non-empty array")
        gates = tuple(GateSpec.parse(g, i) for i, g in enumerate(raw_gates))
        ids = [g.id for g in gates]
        duplicates = sorted({i for i in ids if ids.count(i) > 1})
        if duplicates:
            raise PipelineError(f"{source}: duplicate gate ids {duplicates}")
        regression = RegressionSpec.parse(data.get("regression"))
        timeout_s = data.get("timeout_s", 120)
        if isinstance(timeout_s, bool) or not isinstance(timeout_s, (int, float)) or timeout_s <= 0:
            raise PipelineError(f"{source}: timeout_s must be a positive number")

        cfg = cls(
            node=node,
            generator=generator,
            params=dict(params),
            gates=gates,
            regression=regression,
            timeout_s=int(timeout_s),
            source=source,
            raw=dict(data),
        )
        if not cfg.generator_path.is_file():
            raise PipelineError(
                f"{source}: generator not found: {cfg.generator_path} (fail-closed)"
            )
        return cfg


def load_config(path: str | Path) -> BenchmarkConfig:
    """Load + validate a benchmark configuration from disk."""
    p = Path(path)
    if not p.is_absolute():
        p = ROOT / p
    if not p.is_file():
        raise PipelineError(f"benchmark config not found: {p}")
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise PipelineError(f"{p}: invalid JSON ({exc})") from exc
    return BenchmarkConfig.parse(data, source=str(p))


def config_digest_from_file(path: str | Path) -> str:
    """Canonical digest of a configuration *file* (independent of formatting)."""
    return load_config(path).digest()