"""Probe registry: load, validate and execute capability probes.

The registry is *data* (``config/evidence_regression/probes.json``). Validation enforces the
minimum anti-degradation strength that the drill later demonstrates:

* every capability id is unique (one entry per capability, all probes inside it);
* every target resolves to a declared call chain;
* every entry carries **at least one variation-sensitive probe** (``nontriviality`` or
  ``scale_law``) — without it a constant MOCK satisfies the entry;
* every entry carries a **determinism** probe — without it a random stub passes;
* every entry carries an **expectation-bearing** probe (``anchor``/``bounded``) — without it
  a *uniform drift* passes (drift preserves spreads and ratios, so only an absolute
  expectation notices it). The drill measures all of this empirically.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence

from .model import (EXPECTATION_KINDS, REQUIRED_KINDS, SCHEMA_REGISTRY, VARIATION_SENSITIVE, Entry,
                    ProbeReport, sha256_obj)
from .probes import run_probe
from .targets import FieldView, ResolvedTarget, TargetError

DEFAULT_WORKSPACE = Path(__file__).resolve().parents[2]


class RegistryError(ValueError):
    """Registry file is unusable (structural or anti-degradation invariant violated)."""


@dataclass
class ProbeRegistry:
    entries: Dict[str, Entry]
    workspace_root: Path = DEFAULT_WORKSPACE
    schema_version: str = SCHEMA_REGISTRY
    levels_source: Dict[str, object] = field(default_factory=dict)
    meta: Dict[str, object] = field(default_factory=dict)

    # -- construction -----------------------------------------------------
    @staticmethod
    def from_dict(raw: Dict[str, object], workspace_root: Path | str = DEFAULT_WORKSPACE) -> "ProbeRegistry":
        if not isinstance(raw, dict):
            raise RegistryError("registry must be an object")
        schema = str(raw.get("schema_version") or "")
        if schema and schema != SCHEMA_REGISTRY:
            raise RegistryError(f"unsupported schema_version {schema!r}")
        raw_entries = raw.get("entries")
        if not isinstance(raw_entries, list) or not raw_entries:
            raise RegistryError("registry.entries must be a non-empty list")
        entries: Dict[str, Entry] = {}
        for item in raw_entries:
            entry = Entry.from_dict(item)
            if entry.capability_id in entries:
                raise RegistryError(f"duplicate capability_id {entry.capability_id!r}")
            entries[entry.capability_id] = entry
        registry = ProbeRegistry(
            entries=entries,
            workspace_root=Path(workspace_root),
            schema_version=schema or SCHEMA_REGISTRY,
            levels_source=dict(raw.get("levels_source") or {}),
            meta={k: raw[k] for k in ("title", "description") if k in raw},
        )
        registry.validate()
        return registry

    @staticmethod
    def load(path: str | Path, workspace_root: Path | str = DEFAULT_WORKSPACE) -> "ProbeRegistry":
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
        return ProbeRegistry.from_dict(raw, workspace_root=workspace_root)

    # -- invariants -------------------------------------------------------
    def validate(self) -> Dict[str, object]:
        problems: List[str] = []
        for cid, entry in self.entries.items():
            if not entry.probes:
                problems.append(f"{cid}: no probes")
            if not entry.variation_sensitive:
                problems.append(f"{cid}: needs >=1 variation-sensitive probe {VARIATION_SENSITIVE}")
            for required in REQUIRED_KINDS:
                if required not in entry.kinds:
                    problems.append(f"{cid}: missing mandatory probe kind {required!r}")
            if not any(kind in EXPECTATION_KINDS for kind in entry.kinds):
                problems.append(f"{cid}: needs an expectation-bearing probe {EXPECTATION_KINDS} (kills uniform drift)")
            for i, step in enumerate(entry.target.steps):
                ref = step["ref"]
                if ref.split(":")[0].split(".")[0] in {"os", "sys", "subprocess", "importlib", "builtins"}:
                    problems.append(f"{cid}: step {i} points at a denied module ({ref})")
        if problems:
            raise RegistryError("; ".join(problems))
        return self.summary()

    def summary(self) -> Dict[str, object]:
        kinds: Dict[str, int] = {}
        for entry in self.entries.values():
            for probe in entry.probes:
                kinds[probe.kind] = kinds.get(probe.kind, 0) + 1
        return {
            "capability_entries": len(self.entries),
            "probe_specs": sum(len(e.probes) for e in self.entries.values()),
            "probe_kinds": dict(sorted(kinds.items())),
            "entries_without_variation_probe": [c for c, e in self.entries.items() if not e.variation_sensitive],
            "digest": sha256_obj([e.to_dict() for e in sorted(self.entries.values(), key=lambda e: e.capability_id)]),
        }

    # -- execution --------------------------------------------------------
    def target_for(self, capability_id: str,
                   overrides: Optional[Dict[str, ResolvedTarget]] = None) -> ResolvedTarget:
        if capability_id not in self.entries:
            raise KeyError(capability_id)
        if overrides and capability_id in overrides:
            return overrides[capability_id]
        entry = self.entries[capability_id]
        return ResolvedTarget(entry.target, entry.sys_path, self.workspace_root)

    def run(self, capability_id: str, target: Optional[object] = None) -> ProbeReport:
        entry = self.entries[capability_id]
        resolved = target if target is not None else self.target_for(capability_id)
        report = ProbeReport(capability_id=capability_id)
        started = time.perf_counter()
        for spec in entry.probes:
            # per-probe field override: several probes of one capability may observe
            # different scalars of the same call chain (e.g. a dimensionless number and a
            # force) without duplicating the chain.
            field = spec.params.get("field")
            view = FieldView(resolved, field) if isinstance(field, str) else resolved
            report.checks.append(run_probe(spec, view, capability_id))  # type: ignore[arg-type]
        report.elapsed_ms = (time.perf_counter() - started) * 1000.0
        return report

    def run_all(self, capability_ids: Optional[Iterable[str]] = None) -> List[ProbeReport]:
        ids = list(capability_ids) if capability_ids is not None else list(self.entries)
        return [self.run(cid) for cid in ids]

    def registered_capabilities(self) -> Sequence[str]:
        return tuple(sorted(self.entries))


def load_registry(path: str | Path, workspace_root: Path | str = DEFAULT_WORKSPACE) -> ProbeRegistry:
    return ProbeRegistry.load(path, workspace_root=workspace_root)


def load_levels(path: str | Path, field_name: str = "claimed_level") -> Dict[str, str]:
    """Read a ``{capability_id: level}`` map from a capability ledger artifact.

    Accepts either the ledger shape (``{"capabilities": [{id, <field>, ...}]}``) or a flat
    object. Levels are upper-cased; missing/blank levels are skipped (never guessed).
    """
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    caps = data.get("capabilities") if isinstance(data, dict) else data
    if not isinstance(caps, list):
        raise RegistryError(f"{path}: cannot find a capability list")
    out: Dict[str, str] = {}
    for cap in caps:
        if not isinstance(cap, dict):
            continue
        cid = cap.get("id") or cap.get("capability_id")
        level = cap.get(field_name)
        if cid and level:
            out[str(cid)] = str(level).upper()
    return out


__all__ = ["ProbeRegistry", "RegistryError", "load_registry", "load_levels", "TargetError", "DEFAULT_WORKSPACE"]