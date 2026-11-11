"""Configuration model for the cross-repository capability evidence chain.

Everything that carries meaning — repository roots, artefact globs, metric
name patterns, equivalence margins, alpha, seeds, historical sources — enters
through JSON.  This keeps the package free of domain semantics: the same code
audits a CFD solver, a structural solver or a synthetic fixture repository.

Config shape (see ``configs/ecosystem_solvers.json``)::

    {
      "schema_version": "1.0",
      "root_env": "TP_ECOSYSTEM_ROOT",
      "repos": [
        {"name": "solver_a", "path": "solver_a",
         "include": ["**/artifacts/**/*.json"],
         "exclude": ["**/.git/**"], "max_files": 200}
      ],
      "capabilities": [
        {"id": "solver_a.case.accuracy", "scenario": "mean",
         "margin": 0.15, "alpha": 0.05, "theta": 1.5, "power_target": 0.80,
         "groups": {"a": {"repo": "solver_a", "include": ["**/*.json"],
                          "metric": "relative_error|rel_error",
                          "value_range": [0.0, 10.0], "max_samples": 200}},
         "unit": "relative", "note": "..."}
      ],
      "reclassification": {
        "sources": ["docs/**/acceptance*.json"],
        "status_keys": ["status", "verdict", "result"],
        "value_keys": ["value", "observed", "estimate", "measured"],
        "threshold_keys": ["threshold", "tolerance", "margin", "limit"],
        "margin_default": 0.15, "alpha": 0.05, "power_target": 0.80
      }
    }
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

__all__ = [
    "ConfigError",
    "GroupSpec",
    "CapabilitySpec",
    "RepoSpec",
    "ReclassSpec",
    "EvidenceChainConfig",
    "load_config",
]


class ConfigError(ValueError):
    """Raised for malformed configuration; never for missing data."""


def _require(obj: Dict[str, Any], key: str, where: str) -> Any:
    if key not in obj:
        raise ConfigError(f"missing key '{key}' in {where}")
    return obj[key]


@dataclass(frozen=True)
class GroupSpec:
    repo: str
    include: Sequence[str]
    metric: str
    value_range: Optional[Sequence[float]] = None
    max_samples: int = 200

    @property
    def value_bounds(self):
        if not self.value_range:
            return None
        lo, hi = float(self.value_range[0]), float(self.value_range[1])
        return lo, hi


@dataclass(frozen=True)
class CapabilitySpec:
    id: str
    scenario: str
    margin: float
    alpha: float = 0.05
    theta: float = 1.5
    power_target: float = 0.80
    groups: Dict[str, GroupSpec] = field(default_factory=dict)
    unit: str = ""
    note: str = ""

    def group(self, key: str) -> GroupSpec:
        if key not in self.groups:
            raise ConfigError(f"capability '{self.id}' has no group '{key}'")
        return self.groups[key]


@dataclass(frozen=True)
class RepoSpec:
    name: str
    path: str
    include: Sequence[str]
    exclude: Sequence[str] = ()
    max_files: int = 300


@dataclass(frozen=True)
class ReclassSpec:
    sources: Sequence[str]
    exclude: Sequence[str] = ()
    base: str = ""          # directory the sources are relative to; "" -> config root
    status_keys: Sequence[str] = ("status", "verdict", "result")
    value_keys: Sequence[str] = ("value", "observed", "estimate", "measured")
    threshold_keys: Sequence[str] = ("threshold", "tolerance", "margin", "limit")
    # (value key, threshold key) pairs whose units must agree; the first pair
    # present in a record wins, so a physical quantity stored under "value"
    # is never compared against a percentage tolerance
    value_threshold_pairs: Sequence[Sequence[str]] = (
        ("value", "threshold"),
        ("observed", "tolerance"),
        ("estimate", "margin"),
        ("deviation_pct", "tolerance_pct"),
        ("rel_err_pct", "tolerance_pct"),
    )
    n_keys: Sequence[str] = ("n", "n_samples", "repeats", "sample_size")
    spread_keys: Sequence[str] = (
        "sd", "std", "stdev", "sigma", "uncertainty", "ci_halfwidth",
    )
    margin_default: float = 0.15
    alpha: float = 0.05
    power_target: float = 0.80
    pass_tokens: Sequence[str] = ("PASS", "PASSED", "OK", "TRUE")
    fail_tokens: Sequence[str] = ("FAIL", "FAILED", "FALSE")
    min_computable_n: int = 2
    mde_fraction: float = 0.80


@dataclass
class EvidenceChainConfig:
    schema_version: str
    root: Path
    repos: List[RepoSpec]
    capabilities: List[CapabilitySpec]
    reclassification: Optional[ReclassSpec]
    raw: Dict[str, Any] = field(default_factory=dict)

    def repo(self, name: str) -> RepoSpec:
        for r in self.repos:
            if r.name == name:
                return r
        raise ConfigError(f"unknown repo '{name}'")

    @property
    def repo_names(self) -> List[str]:
        return [r.name for r in self.repos]


def _group_from(obj: Dict[str, Any], where: str) -> GroupSpec:
    return GroupSpec(
        repo=str(_require(obj, "repo", where)),
        include=list(_require(obj, "include", where)),
        metric=str(_require(obj, "metric", where)),
        value_range=obj.get("value_range"),
        max_samples=int(obj.get("max_samples", 200)),
    )


def load_config(path: str | os.PathLike, root: Optional[str | os.PathLike] = None) -> EvidenceChainConfig:
    """Load and validate a config file; ``root`` overrides ``root_env``."""
    p = Path(path)
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ConfigError(f"config not found: {p}") from exc
    except json.JSONDecodeError as exc:
        raise ConfigError(f"config is not valid JSON: {exc}") from exc

    root_env = raw.get("root_env")
    root_dir = Path(root) if root is not None else Path(
        os.environ.get(root_env, ".") if root_env else "."
    )
    repos = [
        RepoSpec(
            name=str(_require(r, "name", "repos[]")),
            path=str(r.get("path", r["name"])),
            include=list(_require(r, "include", "repos[]")),
            exclude=list(r.get("exclude", ["**/.git/**", "**/node_modules/**"])),
            max_files=int(r.get("max_files", 300)),
        )
        for r in raw.get("repos", [])
    ]
    caps: List[CapabilitySpec] = []
    for c in raw.get("capabilities", []):
        where = f"capabilities[{c.get('id', '?')}]"
        groups = {k: _group_from(v, f"{where}.groups.{k}")
                  for k, v in _require(c, "groups", where).items()}
        scenario = str(_require(c, "scenario", where))
        if scenario not in ("mean", "variance", "distribution"):
            raise ConfigError(f"{where}: unknown scenario '{scenario}'")
        if scenario != "mean" and "b" not in groups:
            raise ConfigError(f"{where}: scenario '{scenario}' needs groups.a and groups.b")
        if "a" not in groups:
            raise ConfigError(f"{where}: group 'a' is required")
        caps.append(
            CapabilitySpec(
                id=str(_require(c, "id", where)),
                scenario=scenario,
                margin=float(_require(c, "margin", where)),
                alpha=float(c.get("alpha", 0.05)),
                theta=float(c.get("theta", 1.5)),
                power_target=float(c.get("power_target", 0.80)),
                groups=groups,
                unit=str(c.get("unit", "")),
                note=str(c.get("note", "")),
            )
        )
    reclass = None
    if "reclassification" in raw:
        r = raw["reclassification"]
        reclass = ReclassSpec(
            sources=list(_require(r, "sources", "reclassification")),
            exclude=list(r.get("exclude", [])),
            base=str(r.get("base", "")),
            status_keys=tuple(r.get("status_keys", ReclassSpec.status_keys)),
            value_keys=tuple(r.get("value_keys", ReclassSpec.value_keys)),
            threshold_keys=tuple(r.get("threshold_keys", ReclassSpec.threshold_keys)),
            value_threshold_pairs=tuple(
                tuple(p) for p in r.get("value_threshold_pairs",
                                        ReclassSpec.value_threshold_pairs)
            ),
            n_keys=tuple(r.get("n_keys", ReclassSpec.n_keys)),
            spread_keys=tuple(r.get("spread_keys", ReclassSpec.spread_keys)),
            margin_default=float(r.get("margin_default", 0.15)),
            alpha=float(r.get("alpha", 0.05)),
            power_target=float(r.get("power_target", 0.80)),
            pass_tokens=tuple(r.get("pass_tokens", ReclassSpec.pass_tokens)),
            fail_tokens=tuple(r.get("fail_tokens", ReclassSpec.fail_tokens)),
            min_computable_n=int(r.get("min_computable_n", 2)),
            mde_fraction=float(r.get("mde_fraction", 0.80)),
        )
    return EvidenceChainConfig(
        schema_version=str(raw.get("schema_version", "1.0")),
        root=root_dir,
        repos=repos,
        capabilities=caps,
        reclassification=reclass,
        raw=raw,
    )