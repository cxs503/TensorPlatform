#!/usr/bin/env python3
"""Verification-case registry fed **only** by configuration (no domain code).

Design contract
---------------
* The registry knows six things about a case: ``case_id``, two dotted callable
  paths (``implementation`` = code under test, ``reference`` = independent
  authority), an opaque ``domain`` label, an opaque ``inputs`` payload and a
  quantity/tolerance table.  It never branches on any of those values.
* Any physical domain joins by dropping a JSON file (or by calling
  :meth:`CaseRegistry.register` from its own package) — the harness ships no
  per-domain branch, no per-domain list and no physical constant.
* Validation is fail-closed: unknown keys, missing keys, bad tolerances and
  duplicate ids are rejected with the full reason list instead of being
  silently ignored.

Case file format (``*.json``)::

    {
      "schema_version": "verification-harness/1.0",
      "cases": [
        {
          "case_id": "synth-linear-slope",
          "domain": "synthetic-quadrature",
          "implementation": "some.pkg.module:numeric_impl",
          "reference": "some.pkg.module:closed_form",
          "reference_kind": "analytic",
          "inputs": {"n": 64},
          "quantities": {"slope": {"rtol": 1e-4, "atol": 0.0}},
          "description": "free text",
          "tags": ["quadrature"]
        }
      ]
    }

A file may also contain a single case object (no ``cases`` wrapper) and a
directory may be scanned recursively with :meth:`CaseRegistry.discover`.
"""
from __future__ import annotations

import importlib
import json
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, Iterator, List, Mapping, Optional, Sequence, Tuple

from .model import SCHEMA_VERSION, CaseSpec, ReferenceKind, Tolerance

# Keys the registry understands at case level.  Anything else is a typo or an
# attempt to smuggle domain logic into the harness -> reject.
ALLOWED_CASE_KEYS = frozenset({
    "case_id", "domain", "implementation", "reference", "reference_kind",
    "inputs", "quantities", "description", "tags",
})
REQUIRED_CASE_KEYS = frozenset({"case_id", "implementation", "reference", "quantities"})
# "case_count" / "case_domains" are informational (emitted by
# CaseRegistry.to_dict so a bundle snapshot is self-describing); both are
# ignored on the way back in.
ALLOWED_FILE_KEYS = frozenset({"schema_version", "cases", "description",
                                  "case_count", "case_domains"})
ALLOWED_QUANTITY_KEYS = frozenset({"rtol", "atol"})


class RegistryError(ValueError):
    """Raised when a case declaration cannot be accepted (fail-closed)."""

    def __init__(self, reasons: Sequence[str]) -> None:
        self.reasons = list(reasons)
        super().__init__("; ".join(self.reasons))


class CallableResolutionError(RegistryError):
    """Raised when a dotted callable path cannot be resolved to a callable."""


def resolve_callable(dotted: str) -> Callable[..., Any]:
    """Resolve ``"package.module:attr"`` (or ``"package.module.attr"``) to a callable.

    Import happens at *run* time, not at registration time, so registering a
    case never drags a solver (or its heavy dependencies) into the process.
    """
    if not isinstance(dotted, str) or not dotted.strip():
        raise CallableResolutionError([f"callable path must be a non-empty string, got {dotted!r}"])
    spec = dotted.strip()
    module_path, _, attr_path = spec.partition(":")
    if not attr_path:
        module_path, _, attr_path = spec.rpartition(".")
    if not module_path or not attr_path:
        raise CallableResolutionError([
            f"callable path {spec!r} must look like 'package.module:attr' or 'package.module.attr'"
        ])
    try:
        target: Any = importlib.import_module(module_path)
    except Exception as exc:  # pragma: no cover - environment dependent
        raise CallableResolutionError([f"cannot import {module_path!r} for {spec!r}: {exc}"]) from exc
    for part in attr_path.split("."):
        try:
            target = getattr(target, part)
        except AttributeError as exc:
            raise CallableResolutionError([f"{spec!r}: {module_path!r} has no attribute {part!r}"]) from exc
    if not callable(target):
        raise CallableResolutionError([f"{spec!r} resolved to non-callable {type(target).__name__}"])
    return target


def _parse_tolerance(case_id: str, name: str, raw: Any) -> Tolerance:
    if isinstance(raw, (int, float)):
        return Tolerance(rtol=float(raw))
    if not isinstance(raw, Mapping):
        raise RegistryError([f"case {case_id}: tolerance for {name!r} must be a mapping or a number, got {type(raw).__name__}"])
    unknown = sorted(set(raw) - ALLOWED_QUANTITY_KEYS)
    if unknown:
        raise RegistryError([f"case {case_id}: unknown tolerance keys for {name!r}: {unknown}"])
    try:
        return Tolerance(rtol=float(raw.get("rtol", 0.0)), atol=float(raw.get("atol", 0.0)))
    except (TypeError, ValueError) as exc:
        raise RegistryError([f"case {case_id}: bad tolerance for {name!r}: {exc}"]) from exc


def case_spec_from_mapping(raw: Mapping[str, Any]) -> CaseSpec:
    """Validate one declarative case mapping and turn it into a :class:`CaseSpec`."""
    if not isinstance(raw, Mapping):
        raise RegistryError([f"case declaration must be a mapping, got {type(raw).__name__}"])
    reasons: List[str] = []
    missing = sorted(REQUIRED_CASE_KEYS - set(raw))
    if missing:
        reasons.append(f"missing required keys {missing} in {sorted(raw)!r}")
    unknown = sorted(set(raw) - ALLOWED_CASE_KEYS)
    if unknown:
        reasons.append(f"unknown keys {unknown} in case {raw.get('case_id')!r} (fail-closed)")
    if reasons:
        raise RegistryError(reasons)

    case_id = str(raw["case_id"])
    raw_quantities = raw["quantities"]
    if not isinstance(raw_quantities, Mapping) or not raw_quantities:
        raise RegistryError([f"case {case_id}: 'quantities' must be a non-empty mapping"])

    quantities: List[Tuple[str, Tolerance]] = []
    for name in raw_quantities:
        quantities.append((str(name), _parse_tolerance(case_id, str(name), raw_quantities[name])))

    raw_inputs = raw.get("inputs") or {}
    if not isinstance(raw_inputs, Mapping):
        raise RegistryError([f"case {case_id}: 'inputs' must be a mapping"])

    kind_raw = str(raw.get("reference_kind") or ReferenceKind.ANALYTIC.value).strip().lower()
    try:
        reference_kind = ReferenceKind(kind_raw)
    except ValueError as exc:
        allowed = [k.value for k in ReferenceKind]
        raise RegistryError([f"case {case_id}: reference_kind {kind_raw!r} not in {allowed}"]) from exc

    tags = raw.get("tags") or ()
    if isinstance(tags, str) or not isinstance(tags, Iterable):
        raise RegistryError([f"case {case_id}: 'tags' must be a list of strings"])

    try:
        return CaseSpec(
            case_id=case_id,
            implementation=str(raw["implementation"]),
            reference=str(raw["reference"]),
            quantities=tuple(quantities),
            domain=str(raw.get("domain") or ""),
            inputs=dict(raw_inputs),
            reference_kind=reference_kind,
            description=str(raw.get("description") or ""),
            tags=tuple(str(t) for t in tags),
        )
    except ValueError as exc:
        raise RegistryError([f"case {case_id}: {exc}"]) from exc


class CaseRegistry:
    """Ordered, duplicate-free collection of :class:`CaseSpec`."""

    def __init__(self) -> None:
        self._cases: Dict[str, CaseSpec] = {}

    # -- registration -----------------------------------------------------
    def register(self, case: Any) -> CaseSpec:
        """Register a mapping (configuration) or an already-built spec."""
        spec = case if isinstance(case, CaseSpec) else case_spec_from_mapping(case)
        if spec.case_id in self._cases:
            raise RegistryError([f"duplicate case_id {spec.case_id!r} (registry is fail-closed)"])
        self._cases[spec.case_id] = spec
        return spec

    def register_file(self, path: Path | str) -> List[CaseSpec]:
        """Register every case declared by one JSON file."""
        path = Path(path)
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise RegistryError([f"{path}: invalid JSON ({exc})"]) from exc
        return self.register_payload(payload, origin=str(path))

    def register_payload(self, payload: Any, origin: str = "<memory>") -> List[CaseSpec]:
        if isinstance(payload, Mapping):
            unknown = sorted(set(payload) - ALLOWED_FILE_KEYS - ALLOWED_CASE_KEYS)
            if unknown:
                raise RegistryError([f"{origin}: unknown top-level keys {unknown}"])
            version = payload.get("schema_version")
            if version is not None and str(version) != SCHEMA_VERSION:
                raise RegistryError([f"{origin}: schema_version {version!r} != {SCHEMA_VERSION!r}"])
            raw_cases = payload.get("cases")
            if raw_cases is None:
                if not REQUIRED_CASE_KEYS & set(payload):
                    raise RegistryError([f"{origin}: neither a 'cases' list nor a case object"])
                raw_cases = [payload]
        elif isinstance(payload, (list, tuple)):
            raw_cases = list(payload)
        else:
            raise RegistryError([f"{origin}: payload must be an object, got {type(payload).__name__}"])
        if not isinstance(raw_cases, (list, tuple)):
            raise RegistryError([f"{origin}: 'cases' must be a list"])
        registered = []
        for raw in raw_cases:
            try:
                registered.append(self.register(raw))
            except RegistryError as exc:
                raise RegistryError([f"{origin}: {reason}" for reason in exc.reasons]) from exc
        return registered

    def discover(self, directory: Path | str, pattern: str = "*.json") -> List[CaseSpec]:
        """Register every ``pattern`` file under ``directory`` (recursive, sorted)."""
        directory = Path(directory)
        if not directory.is_dir():
            raise RegistryError([f"case directory {directory} does not exist"])
        registered: List[CaseSpec] = []
        files = sorted(directory.rglob(pattern))
        if not files:
            raise RegistryError([f"no case files matching {pattern!r} under {directory}"])
        for path in files:
            registered.extend(self.register_file(path))
        return registered

    # -- access -----------------------------------------------------------
    def __len__(self) -> int:
        return len(self._cases)

    def __contains__(self, case_id: object) -> bool:
        return case_id in self._cases

    def __iter__(self) -> Iterator[CaseSpec]:
        for case_id in sorted(self._cases):
            yield self._cases[case_id]

    @property
    def case_ids(self) -> Tuple[str, ...]:
        return tuple(sorted(self._cases))

    def get(self, case_id: str) -> CaseSpec:
        try:
            return self._cases[case_id]
        except KeyError as exc:
            raise KeyError(f"case {case_id!r} is not registered ({len(self._cases)} cases known)") from exc

    def domains(self) -> Dict[str, int]:
        """Opaque domain labels -> case count (reporting only)."""
        counts: Dict[str, int] = {}
        for spec in self._cases.values():
            counts[spec.domain] = counts.get(spec.domain, 0) + 1
        return {name: counts[name] for name in sorted(counts)}

    def to_dict(self) -> Dict[str, Any]:
        return {
            "schema_version": SCHEMA_VERSION,
            "case_count": len(self._cases),
            "case_domains": {cid: self._cases[cid].domain for cid in sorted(self._cases)},
            "cases": [spec.to_dict() for spec in self],
        }