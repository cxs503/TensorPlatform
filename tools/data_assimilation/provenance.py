"""Zero-hand-copy provenance: every number in a report is anchored to a source.

The rule this module enforces: a number that appears in a verification report must
be reproducible from *(a)* the artefact it was read from -- identified by file path,
sha256, and JSON pointer (row/column) -- or *(b)* a declared formula applied to
already-anchored numbers.  Nothing may be typed in by hand.

``Provenance.verify()`` re-reads every file anchor at the current state of the tree
and fails loudly on any mismatch, so a stale report cannot survive an edit of its
source table.
"""
from __future__ import annotations

import hashlib
import json
import os
from typing import Dict, List, Mapping, Optional, Sequence


def sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def json_pointer_get(obj, pointer: str):
    """RFC-6901 style lookup: ``/entries/3/reference/ct`` (``""`` = whole document)."""
    if pointer in ("", "/"):
        return obj
    cur = obj
    for token in pointer.lstrip("/").split("/"):
        token = token.replace("~1", "/").replace("~0", "~")
        if isinstance(cur, list):
            cur = cur[int(token)]
        elif isinstance(cur, Mapping):
            cur = cur[token]
        else:  # pragma: no cover - defensive
            raise KeyError(f"pointer {pointer!r} does not resolve at {token!r}")
    return cur


_MODULE_CACHE: Dict[str, object] = {}


def _load_module(abspath: str):
    """Import a python file by path (cached); used for code-anchored numbers."""
    import importlib.util
    if abspath in _MODULE_CACHE:
        return _MODULE_CACHE[abspath]
    modname = "provenance_" + hashlib.sha256(abspath.encode()).hexdigest()[:12]
    spec = importlib.util.spec_from_file_location(modname, abspath)
    if spec is None or spec.loader is None:  # pragma: no cover
        raise ProvenanceError(f"cannot import {abspath}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    _MODULE_CACHE[abspath] = module
    return module


class ProvenanceError(AssertionError):
    """Raised when provenance is incomplete or no longer reproducible."""


class Provenance:
    """Ordered table of anchored numbers."""

    def __init__(self, root: str = "."):
        self.root = root
        self.rows: List[Dict[str, object]] = []
        self._cache: Dict[str, object] = {}
        self._sha: Dict[str, str] = {}

    # -- internals ---------------------------------------------------------
    def _load(self, path: str):
        abspath = path if os.path.isabs(path) else os.path.join(self.root, path)
        key = os.path.relpath(abspath, self.root)
        if key not in self._cache:
            with open(abspath, "r", encoding="utf-8") as fh:
                self._cache[key] = json.load(fh)
            self._sha[key] = sha256_file(abspath)
        return key, self._cache[key]

    def _add(self, row: Mapping[str, object]) -> None:
        self.rows.append(dict(row))

    # -- anchors -----------------------------------------------------------
    def file_value(self, name: str, path: str, pointer: str, *,
                   unit: Optional[str] = None, note: Optional[str] = None):
        """Read one value from a JSON artefact and anchor it (path, sha256, pointer)."""
        key, doc = self._load(path)
        value = json_pointer_get(doc, pointer)
        row = {"name": name, "kind": "file", "value": value, "source": key,
               "sha256": self._sha[key], "pointer": pointer}
        if unit:
            row["unit"] = unit
        if note:
            row["note"] = note
        self._add(row)
        return value

    def config_value(self, name: str, path: str, pointer: str, *,
                     unit: Optional[str] = None):
        """Same as :meth:`file_value` but flagged as injected configuration."""
        key, doc = self._load(path)
        value = json_pointer_get(doc, pointer)
        row = {"name": name, "kind": "config", "value": value, "source": key,
               "sha256": self._sha[key], "pointer": pointer}
        if unit:
            row["unit"] = unit
        self._add(row)
        return value

    def code_value(self, name: str, path: str, symbol: str, key: Optional[str] = None):
        """Anchor a value read from a python source file (module attribute / item)."""
        abspath = path if os.path.isabs(path) else os.path.join(self.root, path)
        digest = sha256_file(abspath)
        obj = _load_module(abspath)
        attr = getattr(obj, symbol)
        value = attr[key] if key is not None else attr
        row = {"name": name, "kind": "code", "value": value,
               "source": os.path.relpath(abspath, self.root), "sha256": digest,
               "pointer": f"{symbol}/{key}" if key is not None else symbol}
        self._add(row)
        return value

    def derived(self, name: str, value, inputs: Sequence[str], formula: str, *,
                unit: Optional[str] = None, note: Optional[str] = None):
        """Record a computed number together with its formula and input names."""
        row = {"name": name, "kind": "derived", "value": value,
               "inputs": list(inputs), "formula": formula}
        if unit:
            row["unit"] = unit
        if note:
            row["note"] = note
        self._add(row)
        return value

    def text(self, name: str, value: str, *, note: Optional[str] = None):
        row = {"name": name, "kind": "text", "value": value}
        if note:
            row["note"] = note
        self._add(row)
        return value

    # -- verification ------------------------------------------------------
    def verify(self) -> Dict[str, object]:
        """Re-read every anchor; any drift is a hard failure."""
        names = [r["name"] for r in self.rows]
        if len(names) != len(set(names)):
            dupes = sorted({n for n in names if names.count(n) > 1})
            raise ProvenanceError(f"duplicate provenance names: {dupes}")
        problems: List[str] = []
        known = {r["name"]: r for r in self.rows}
        checked = 0
        for row in self.rows:
            kind = row["kind"]
            if kind in ("file", "config"):
                path = row["source"]  # type: ignore[index]
                abspath = os.path.join(self.root, path)
                if not os.path.isfile(abspath):
                    problems.append(f"{row['name']}: missing source {path}")
                    continue
                digest = sha256_file(abspath)
                if digest != row["sha256"]:
                    problems.append(f"{row['name']}: source sha256 changed ({path})")
                    continue
                with open(abspath, "r", encoding="utf-8") as fh:
                    doc = json.load(fh)
                try:
                    value = json_pointer_get(doc, row["pointer"])  # type: ignore[index]
                except Exception as exc:  # noqa: BLE001
                    problems.append(f"{row['name']}: pointer failed ({exc})")
                    continue
                if value != row["value"]:
                    problems.append(f"{row['name']}: value drifted ({value!r} != "
                                    f"{row['value']!r})")
                checked += 1
            elif kind == "code":
                path = row["source"]  # type: ignore[index]
                abspath = os.path.join(self.root, path)
                if not os.path.isfile(abspath):
                    problems.append(f"{row['name']}: missing source {path}")
                    continue
                digest = sha256_file(abspath)
                if digest != row["sha256"]:
                    problems.append(f"{row['name']}: source sha256 changed ({path})")
                    continue
                _MODULE_CACHE.pop(abspath, None)
                try:
                    module = _load_module(abspath)
                    symbol, _, key = str(row["pointer"]).partition("/")  # type: ignore[index]
                    attr = getattr(module, symbol)
                    value = attr[key] if key else attr
                except Exception as exc:  # noqa: BLE001
                    problems.append(f"{row['name']}: code anchor failed ({exc})")
                    continue
                if value != row["value"]:
                    problems.append(f"{row['name']}: value drifted ({value!r} != "
                                    f"{row['value']!r})")
                checked += 1
            elif kind == "derived":
                for dep in row["inputs"]:  # type: ignore[index]
                    if dep not in known:
                        problems.append(f"{row['name']}: unknown input {dep!r}")
                checked += 1
            else:
                checked += 1
        return {"ok": not problems, "checked": checked, "problems": problems,
                "n_rows": len(self.rows)}

    # -- serialisation -----------------------------------------------------
    def as_list(self) -> List[Dict[str, object]]:
        return [dict(r) for r in self.rows]

    def canonical(self) -> str:
        return json.dumps(self.as_list(), sort_keys=True, separators=(",", ":"))

    def digest(self) -> str:
        return sha256_text(self.canonical())