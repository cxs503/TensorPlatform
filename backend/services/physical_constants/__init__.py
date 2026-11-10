"""Physical constants SSOT (Single Source of Truth) registry.

Loads the canonical constants from ``config/physical_constants_registry.json``
and provides query/access APIs. Domain-free by design: physical content
arrives through the JSON registry, never through code here.
"""
from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

_REGISTRY_PATH = Path(__file__).resolve().parents[3] / "config" / "physical_constants_registry.json"


@dataclass(frozen=True)
class PhysicalConstant:
    """One entry in the SSOT registry."""
    key: str
    symbol: str
    value: float
    unit: str
    uncertainty: float
    uncertainty_type: str
    source: str
    scope: str
    aliases: tuple
    sha256: str
    notes: str

    def as_dict(self) -> Dict[str, Any]:
        return {
            "key": self.key,
            "symbol": self.symbol,
            "value": self.value,
            "unit": self.unit,
            "uncertainty": self.uncertainty,
            "uncertainty_type": self.uncertainty_type,
            "source": self.source,
            "scope": self.scope,
            "aliases": list(self.aliases),
            "sha256": self.sha256,
            "notes": self.notes,
        }


def _compute_sha256(entry: Dict[str, Any]) -> str:
    """Deterministic sha256 over the canonical fields of a constant entry."""
    canonical = json.dumps(
        {k: entry[k] for k in ("symbol", "value", "unit", "uncertainty", "source", "scope")},
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode()).hexdigest()


class PhysicalConstantsRegistry:
    """In-memory view of the SSOT physical constants registry.

    Parameters
    ----------
    path : Path or None
        Override the default registry location (useful for tests).
    """

    def __init__(self, path: Optional[Path] = None) -> None:
        self._path = Path(path) if path else _REGISTRY_PATH
        self._raw: Dict[str, Any] = {}
        self._constants: Dict[str, PhysicalConstant] = {}
        self._alias_map: Dict[str, str] = {}
        self._load()

    # -- loading -------------------------------------------------------------
    def _load(self) -> None:
        with open(self._path, "r", encoding="utf-8") as fh:
            self._raw = json.load(fh)
        for key, entry in self._raw.get("constants", {}).items():
            sha = _compute_sha256(entry)
            const = PhysicalConstant(
                key=key,
                symbol=entry["symbol"],
                value=float(entry["value"]),
                unit=entry["unit"],
                uncertainty=float(entry.get("uncertainty", 0.0)),
                uncertainty_type=entry.get("uncertainty_type", "unknown"),
                source=entry.get("source", ""),
                scope=entry.get("scope", "global"),
                aliases=tuple(entry.get("aliases", [])),
                sha256=sha,
                notes=entry.get("notes", ""),
            )
            self._constants[key] = const
            for alias in const.aliases:
                self._alias_map[alias] = key

    # -- query ---------------------------------------------------------------
    @property
    def version(self) -> str:
        return str(self._raw.get("version", "0.0.0"))

    @property
    def keys(self) -> List[str]:
        return sorted(self._constants.keys())

    def get(self, key_or_alias: str) -> PhysicalConstant:
        """Look up by canonical key or any registered alias."""
        if key_or_alias in self._constants:
            return self._constants[key_or_alias]
        canonical = self._alias_map.get(key_or_alias)
        if canonical is None:
            raise KeyError(f"unknown constant {key_or_alias!r}; known: {self.keys}")
        return self._constants[canonical]

    def value(self, key_or_alias: str) -> float:
        return self.get(key_or_alias).value

    def all_constants(self) -> List[PhysicalConstant]:
        return [self._constants[k] for k in sorted(self._constants)]

    def as_dict(self) -> Dict[str, Any]:
        return {
            "version": self.version,
            "constants": {k: c.as_dict() for k, c in sorted(self._constants.items())},
        }

    # -- drift detection helpers ---------------------------------------------
    def tolerance_band(self, key_or_alias: str, relative: float = 1e-3) -> tuple:
        """Return (lo, hi) tolerance band for a constant."""
        c = self.get(key_or_alias)
        if c.uncertainty_type == "exact":
            return (c.value, c.value)
        half = max(abs(c.value) * relative, c.uncertainty)
        return (c.value - half, c.value + half)


# Module-level singleton (lazy)
_default: Optional[PhysicalConstantsRegistry] = None


def get_registry(path: Optional[Path] = None) -> PhysicalConstantsRegistry:
    """Return the default registry singleton (or a fresh one if *path* given)."""
    global _default
    if path is not None:
        return PhysicalConstantsRegistry(path=path)
    if _default is None:
        _default = PhysicalConstantsRegistry()
    return _default


__all__ = [
    "PhysicalConstant",
    "PhysicalConstantsRegistry",
    "get_registry",
]
