"""Exception types for the reference-value corpus.

The corpus is a *serving* layer over literature/standard tables: every failure
mode must be distinguishable by the caller (and by the acceptance harness), so
each error carries machine-readable context.
"""
from __future__ import annotations

from typing import Optional


class ReferenceCorpusError(Exception):
    """Base class for all reference-corpus failures."""


class SchemaViolation(ReferenceCorpusError):
    """A corpus entry does not satisfy ``refs.schema.json``."""

    def __init__(self, entry_path: str, errors: list) -> None:
        self.entry_path = entry_path
        self.errors = list(errors)
        super().__init__(f"{entry_path}: {len(self.errors)} schema error(s): {self.errors[:3]}")


class IntegrityError(ReferenceCorpusError):
    """sha256 anchor of an archived source artefact does not match the entry.

    This is a *refusal to serve*: the corpus never returns numbers whose
    provenance chain is broken.
    """

    def __init__(self, entry_id: str, path: str, expected: str, actual: str) -> None:
        self.entry_id = entry_id
        self.path = path
        self.expected = expected
        self.actual = actual
        super().__init__(
            f"sha256 mismatch for entry {entry_id!r} ({path}): "
            f"expected {expected[:12]}..., got {actual[:12]}..."
        )


class OutOfRangeError(ReferenceCorpusError):
    """Requested abscissa lies outside the entry's digitised validity range."""

    def __init__(
        self,
        entry_id: str,
        x_quantity: str,
        x: float,
        x_min: float,
        x_max: float,
        note: Optional[str] = None,
    ) -> None:
        self.entry_id = entry_id
        self.x_quantity = x_quantity
        self.x = float(x)
        self.x_min = float(x_min)
        self.x_max = float(x_max)
        self.note = note
        super().__init__(
            f"OUT_OF_RANGE: {entry_id}: {x_quantity}={x!r} outside [{x_min!r}, {x_max!r}]"
            + (f" ({note})" if note else "")
        )


class EntryNotFound(ReferenceCorpusError):
    """Unknown reference id."""

    def __init__(self, entry_id: str, known: list) -> None:
        self.entry_id = entry_id
        self.known = list(known)
        super().__init__(f"unknown reference id {entry_id!r}; known={self.known[:8]}")


class DualPassViolation(ReferenceCorpusError):
    """Two independent digitisation passes disagree beyond the declared gate."""

    def __init__(self, entry_id: str, max_rel_diff: float, gate: float) -> None:
        self.entry_id = entry_id
        self.max_rel_diff = max_rel_diff
        self.gate = gate
        super().__init__(
            f"dual-pass check failed for {entry_id!r}: max_rel_diff={max_rel_diff:.3e} > gate={gate:.3e}"
        )