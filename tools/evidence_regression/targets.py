"""Target resolution and degradation (MOCK) wrappers.

A target is resolved lazily from an import reference (``module:attribute``). Resolution is
restricted: no private/dunder attributes, no modules from a deny list (``os``, ``sys``,
``subprocess``, ``importlib``…), so a registry file cannot smuggle code execution in.

Degradation wrappers (used by the drill and by the negative-control tests) replace the
observable of a target with a MOCK family *without touching files on disk*: this is the
same failure mode as "the implementation was silently replaced by a hardcoded / random /
expectation-echoing stub", but it is reproducible and side-effect free.
"""
from __future__ import annotations

import copy
import importlib
import random
import sys
from pathlib import Path
from typing import Any, Dict, Optional, Sequence

from .model import TargetSpec

#: Modules a registry file may never point at.
DENY_MODULES = {
    "os", "sys", "subprocess", "shutil", "importlib", "socket", "ctypes", "multiprocessing",
    "builtins", "__builtin__", "pathlib", "pickle", "marshal", "eval", "exec",
}

#: MOCK families used for degradation injection. ``expected_echo`` is the nastiest one:
#: it returns the *expected* values of the anchors, i.e. a stub written to make the
#: anchor probes pass — a registry with only anchor probes would miss it.
MOCK_FAMILIES = ("constant_freeze", "unseeded_random", "drift_scale", "zero_fill", "expected_echo")


class TargetError(RuntimeError):
    """Raised when a target cannot be resolved or invoked."""


class ResolvedTarget:
    """Callable view over a :class:`TargetSpec`.

    ``__call__(overrides)`` merges ``overrides`` into the first step's kwargs, runs the
    call chain and returns the selected field (or the raw final result).
    """

    def __init__(self, spec: TargetSpec, sys_paths: Sequence[str] = (), workspace_root: Optional[Path] = None):
        self.spec = spec
        self._sys_paths = tuple(sys_paths)
        self._root = Path(workspace_root) if workspace_root is not None else None
        self._callables: Dict[str, Any] = {}
        self.calls = 0

    # -- resolution -------------------------------------------------------
    def _import(self, ref: str):
        module_name, _, attr = ref.partition(":")
        if module_name.split(".")[0] in DENY_MODULES:
            raise TargetError(f"refusing to resolve denied module {module_name!r}")
        if attr.startswith("_") or "." in attr:
            raise TargetError(f"refusing to resolve private/qualified attribute {attr!r}")
        added = []
        for rel in self._sys_paths:
            path = str((self._root / rel) if (self._root is not None and not Path(rel).is_absolute()) else Path(rel))
            if path not in sys.path:
                sys.path.insert(0, path)
                added.append(path)
        try:
            module = importlib.import_module(module_name)
        except Exception as exc:  # noqa: BLE001 - surfaced as TargetError, not swallowed
            raise TargetError(f"cannot import {module_name!r}: {type(exc).__name__}: {exc}") from exc
        target = getattr(module, attr, None)
        if target is None or not callable(target):
            raise TargetError(f"{ref!r} does not resolve to a callable")
        return target

    def callable_for(self, ref: str):
        if ref not in self._callables:
            self._callables[ref] = self._import(ref)
        return self._callables[ref]

    # -- invocation -------------------------------------------------------
    def __call__(self, overrides: Optional[Dict[str, Any]] = None) -> Any:
        """Selected observable (``spec.field`` applied to :meth:`raw`)."""
        return select_field(self.raw(overrides), self.spec.field)

    def raw(self, overrides: Optional[Dict[str, Any]] = None) -> Any:
        """Final result of the call chain, before field selection."""
        overrides = dict(overrides or {})
        result: Any = None
        for index, step in enumerate(self.spec.steps):
            fn = self.callable_for(step["ref"])
            kwargs = dict(step["kwargs"])
            if index == 0:
                kwargs.update(overrides)
                args: tuple = ()
            else:
                args = (result,)
            try:
                result = fn(*args, **kwargs)
            except Exception as exc:  # noqa: BLE001 - a call error is evidence, not a crash
                raise TargetError(f"step {index} ({step['ref']}) failed: {type(exc).__name__}: {exc}") from exc
            self.calls += 1
        return result

    def describe(self) -> Dict[str, Any]:
        return self.spec.to_dict()


def select_field(value: Any, path: Optional[str]) -> Any:
    """Dotted-path selection over dicts, attributes and sequences (numeric = index)."""
    if not path:
        return value
    current = value
    for segment in path.split("."):
        if segment == "":
            continue
        if isinstance(current, dict):
            if segment not in current:
                raise TargetError(f"field segment {segment!r} missing from dict keys {sorted(current)[:8]}")
            current = current[segment]
        elif isinstance(current, (list, tuple)):
            try:
                current = current[int(segment)]
            except (ValueError, IndexError) as exc:
                raise TargetError(f"field segment {segment!r} is not a valid index") from exc
        else:
            if not hasattr(current, segment):
                raise TargetError(f"field segment {segment!r} missing from {type(current).__name__}")
            current = getattr(current, segment)
    return current


# ---------------------------------------------------------------------------
# degradation wrappers
# ---------------------------------------------------------------------------
def _numeric_leaves(value: Any):
    """Yield (getter-ish path, number) pairs: simplified as recursive rewrite below."""
    if isinstance(value, bool):
        return
    if isinstance(value, (int, float)):
        yield value
    elif isinstance(value, dict):
        for item in value.values():
            yield from _numeric_leaves(item)
    elif isinstance(value, (list, tuple)):
        for item in value:
            yield from _numeric_leaves(item)
    else:
        for name in getattr(value, "__dataclass_fields__", {}) or {}:
            yield from _numeric_leaves(getattr(value, name))


def _map_numbers(value: Any, fn) -> Any:
    """Deep-copy with every numeric leaf replaced by ``fn(leaf)`` (shape preserved)."""
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return fn(value)
    if isinstance(value, dict):
        return {k: _map_numbers(v, fn) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return type(value)(_map_numbers(v, fn) for v in value)
    if hasattr(value, "__dataclass_fields__"):
        clone = copy.copy(value)
        for name in value.__dataclass_fields__:
            setattr(clone, name, _map_numbers(getattr(value, name), fn))
        return clone
    return value


class DegradedTarget:
    """A target whose observable has been replaced by one of :data:`MOCK_FAMILIES`."""

    def __init__(self, base: ResolvedTarget, family: str, expectations: Sequence[Any] = (), drift: float = 1.7):
        if family not in MOCK_FAMILIES:
            raise ValueError(f"unknown MOCK family {family!r}")
        self.base = base
        self.family = family
        self.expectations = list(expectations)
        self.drift = float(drift)
        self._frozen: Any = None
        self._echo_index = 0

    def __call__(self, overrides: Optional[Dict[str, Any]] = None) -> Any:
        return select_field(self.raw(overrides), self.base.spec.field)

    def raw(self, overrides: Optional[Dict[str, Any]] = None) -> Any:
        raw = self.base.raw(overrides)
        if self.family == "constant_freeze":
            if self._frozen is None:
                self._frozen = copy.deepcopy(raw)
            return copy.deepcopy(self._frozen)
        if self.family == "unseeded_random":
            return _map_numbers(raw, lambda _x: random.uniform(1.0, 10.0))
        if self.family == "drift_scale":
            return _map_numbers(raw, lambda x: x * self.drift)
        if self.family == "zero_fill":
            return _map_numbers(raw, lambda _x: 0.0)
        # expected_echo: return the anchor expectations in order (hardcoded-to-expectations stub)
        if not self.expectations:
            return _map_numbers(raw, lambda _x: 0.0)
        value = self.expectations[self._echo_index % len(self.expectations)]
        self._echo_index += 1
        return value

class FieldView:
    """Read an alternative field of the *same* target (per-probe field override)."""

    def __init__(self, base: Any, field: str):
        self.base = base
        self.field = field

    def raw(self, overrides: Optional[Dict[str, Any]] = None) -> Any:
        raw = self.base.raw(overrides) if hasattr(self.base, "raw") else self.base(overrides)
        return raw

    def __call__(self, overrides: Optional[Dict[str, Any]] = None) -> Any:
        return select_field(self.raw(overrides), self.field)
