"""Canonical conservation-ledger document + seal (ingest format of the gate).

A ledger is the *raw* evidence of one cross-library transfer: for every
conserved quantity it carries the value on the provider side and the value on
the consumer side, never a pre-computed "residual".  Residuals are the gate's
business (:mod:`src.coupling.conservation_gate.gate`), so a producer cannot
make a transfer pass by writing a small number next to a broken balance.

Kinds (shape of the two sides):

===========  ==========================================================
``scalar``   ``float`` — mass, energy, flux, rate, ...
``vector``   length-3 — linear momentum, ...
``tensor``   3x3 — inertia tensor, ...
``flag``     ``bool`` — "this invariant held during the run" (monotone
             clock, no deadlock, ...).  Only ``True`` is admissible.
===========  ==========================================================

Every document is sealed with a sha256 over a canonical JSON encoding of its
payload.  A ledger whose numbers were edited after sealing is rejected with
``SEAL_MISMATCH`` — this is what makes the gate an *ingest* gate rather than a
formatter.

No domain semantics: quantities are identified by the caller's names only.
"""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

SCHEMA = "coupling.conservation_ledger/1"
KINDS: Tuple[str, ...] = ("scalar", "vector", "tensor", "flag")


# --------------------------------------------------------------------------- #
# numeric helpers (shared with the gate so there is only one definition)
# --------------------------------------------------------------------------- #
def as_numbers(kind: str, value: Any) -> List[float]:
    """Flatten a ledger value to a list of floats; raise on a shape mismatch."""
    if kind == "flag":
        if not isinstance(value, bool):
            raise ValueError("flag entry must be a bool")
        return [1.0 if value else 0.0]
    if kind == "scalar":
        v = float(value)
        return [v]
    if kind == "vector":
        arr = [float(x) for x in value]
        if len(arr) != 3:
            raise ValueError(f"vector entry must have 3 components, got {len(arr)}")
        return arr
    if kind == "tensor":
        rows = [[float(x) for x in row] for row in value]
        if len(rows) != 3 or any(len(r) != 3 for r in rows):
            raise ValueError("tensor entry must be 3x3")
        return [x for row in rows for x in row]
    raise ValueError(f"unknown kind {kind!r}")


def norm2(values: Sequence[float]) -> float:
    return math.sqrt(sum(v * v for v in values))


def rel_residual(kind: str, source: Any, target: Any) -> float:
    """Relative mismatch of the two sides, computed from raw values only.

    ``|s - t| / max(½(|s| + |t|), tiny)`` with the 2-norm for vectors/tensors.
    Both sides exactly zero gives ``0.0`` (a genuinely balanced zero).
    """
    s = as_numbers(kind, source)
    t = as_numbers(kind, target)
    if len(s) != len(t):
        raise ValueError("source and target shapes differ")
    if kind == "flag":
        return 0.0 if s == t else 1.0
    num = norm2([a - b for a, b in zip(s, t)])
    den = 0.5 * (norm2(s) + norm2(t))
    if den == 0.0:
        return 0.0 if num == 0.0 else float("inf")
    return num / den


# --------------------------------------------------------------------------- #
# entries and documents
# --------------------------------------------------------------------------- #
@dataclass
class LedgerEntry:
    """One conserved quantity carried across one interface."""

    name: str
    kind: str
    source: Any
    target: Any
    tolerance: float = 0.0
    note: str = ""

    def __post_init__(self) -> None:
        if self.kind not in KINDS:
            raise ValueError(f"unknown kind {self.kind!r}")
        self.tolerance = float(self.tolerance)

    def canonical(self) -> dict:
        return {
            "name": self.name,
            "kind": self.kind,
            "source": _canon(self.source),
            "target": _canon(self.target),
            "tolerance": self.tolerance,
            "note": self.note,
        }

    def residual(self) -> float:
        return rel_residual(self.kind, self.source, self.target)


def _canon(value: Any) -> Any:
    # numpy scalars/arrays are not JSON types; normalise without importing numpy
    if hasattr(value, "tolist"):
        value = value.tolist()
    if hasattr(value, "item"):
        value = value.item()
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, dict):
        return {k: _canon(value[k]) for k in sorted(value)}
    if isinstance(value, (list, tuple)):
        return [_canon(v) for v in value]
    return value


def canonical_json(payload: Any) -> str:
    return json.dumps(_canon(payload), sort_keys=True, separators=(",", ":"))


def sha256_of(payload: Any) -> str:
    return hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()


@dataclass
class ConservationLedger:
    """Raw two-sided evidence for one cross-library interface."""

    face: str
    entries: List[LedgerEntry]
    producer: Dict[str, Any] = field(default_factory=dict)
    inputs: List[str] = field(default_factory=list)
    meta: Dict[str, Any] = field(default_factory=dict)
    seal: str = ""

    # -- payload / sealing ------------------------------------------------ #
    def payload(self) -> dict:
        return {
            "schema": SCHEMA,
            "face": self.face,
            "entries": [e.canonical() for e in self.entries],
            "producer": _canon(self.producer),
            "inputs": list(self.inputs),
            "meta": _canon(self.meta),
        }

    def seal_ledger(self) -> str:
        self.seal = sha256_of(self.payload())
        return self.seal

    def verify_seal(self) -> bool:
        return bool(self.seal) and self.seal == sha256_of(self.payload())

    # -- access ----------------------------------------------------------- #
    def entry(self, name: str) -> Optional[LedgerEntry]:
        for e in self.entries:
            if e.name == name:
                return e
        return None

    def names(self) -> List[str]:
        return [e.name for e in self.entries]

    def duplicate_names(self) -> List[str]:
        seen, dup = set(), []
        for n in self.names():
            if n in seen:
                dup.append(n)
            seen.add(n)
        return dup

    def residuals(self) -> Dict[str, float]:
        out: Dict[str, float] = {}
        for e in self.entries:
            try:
                out[e.name] = e.residual()
            except ValueError:
                out[e.name] = float("nan")
        return out

    def as_dict(self) -> dict:
        d = self.payload()
        d["seal"] = self.seal
        return d

    @classmethod
    def from_dict(cls, data: dict) -> "ConservationLedger":
        entries = [LedgerEntry(**e) for e in data.get("entries", [])]
        return cls(
            face=data["face"],
            entries=entries,
            producer=dict(data.get("producer", {})),
            inputs=list(data.get("inputs", [])),
            meta=dict(data.get("meta", {})),
            seal=str(data.get("seal", "")),
        )


def seal_all(ledgers: Iterable[ConservationLedger]) -> List[str]:
    return [lg.seal_ledger() for lg in ledgers]


def bundle_digest(ledgers: Sequence[ConservationLedger]) -> str:
    """sha256 over the sealed documents, in face-name order (stable)."""
    ordered = sorted(ledgers, key=lambda lg: lg.face)
    return sha256_of([lg.payload() for lg in ordered])


__all__ = [
    "SCHEMA",
    "KINDS",
    "LedgerEntry",
    "ConservationLedger",
    "as_numbers",
    "bundle_digest",
    "canonical_json",
    "norm2",
    "rel_residual",
    "seal_all",
    "sha256_of",
]