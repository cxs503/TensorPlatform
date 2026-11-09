"""Provenance helpers for coupling exchanges (canonical digests + hash-chained ledger).

Two audit properties are enforced here rather than trusted:

* **Idempotence** —— the same exchange (same operator spec, meshes, values)
  produces the same digest and bit-identical values.  :func:`canonical_digest`
  is the single canonicalisation used for that claim, so a re-run can be compared
  without storing full fields.
* **Traceability** —— :class:`ExchangeLedger` stores one entry per exchange,
  each entry carrying ``prev_digest``; tampering with history therefore changes
  :meth:`ExchangeLedger.digest`.

Everything is plain JSON-serialisable data: no solver objects, no timestamps,
no floating-point formatting surprises (floats go through ``float.hex`` so the
representation is exact and platform independent).
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
from dataclasses import dataclass, field as _dc_field
from typing import Any, Dict, Iterable, List, Mapping, Sequence

import numpy as np

__all__ = ["to_canonical", "canonical_digest", "ExchangeLedger"]


def _canonical_scalar(value: Any) -> Any:
    if isinstance(value, (bool, np.bool_)):
        return bool(value)
    if isinstance(value, (np.floating, float)):
        f = float(value)
        if f == 0.0:
            return "0x0.0p+0"          # collapse -0.0 / 0.0
        if not np.isfinite(f):
            return repr(f)             # 'nan' / 'inf' — keeps JSON strict-safe
        return f.hex()
    if isinstance(value, (np.integer, int)) and not isinstance(value, bool):
        return int(value)
    return value


def to_canonical(obj: Any) -> Any:
    """Recursively convert ``obj`` to a JSON-safe canonical structure."""
    if isinstance(obj, np.ndarray):
        arr = np.ascontiguousarray(obj, dtype=np.float64)
        return {
            "__ndarray__": {
                "shape": list(arr.shape),
                "float64_hex": arr.reshape(-1).astype(np.float64).tobytes().hex(),
            }
        }
    if dataclasses.is_dataclass(obj) and not isinstance(obj, type):
        return {
            f.name: to_canonical(getattr(obj, f.name))
            for f in sorted(dataclasses.fields(obj), key=lambda f: f.name)
        }
    if isinstance(obj, Mapping):
        return {str(k): to_canonical(v) for k, v in sorted(obj.items(), key=lambda kv: str(kv[0]))}
    if isinstance(obj, (list, tuple)):
        return [to_canonical(v) for v in obj]
    if isinstance(obj, (set, frozenset)):
        return [to_canonical(v) for v in sorted(obj, key=repr)]
    if hasattr(obj, "value") and isinstance(getattr(obj, "value"), str):  # Enum
        return str(obj.value)
    return _canonical_scalar(obj)


def canonical_digest(obj: Any) -> str:
    """SHA-256 over the canonical JSON encoding of ``obj``."""
    payload = json.dumps(to_canonical(obj), sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


@dataclass
class ExchangeLedger:
    """Append-only, hash-chained record of coupling exchanges."""

    name: str = "exchange"
    entries: List[Dict[str, Any]] = _dc_field(default_factory=list)

    def append(self, **record: Any) -> Dict[str, Any]:
        prev = self.entries[-1]["digest"] if self.entries else "genesis"
        entry: Dict[str, Any] = {"seq": len(self.entries), "prev_digest": prev}
        entry.update({k: to_canonical(v) for k, v in record.items() if k not in ("seq", "prev_digest", "digest")})
        entry["digest"] = canonical_digest({k: entry[k] for k in sorted(entry)})
        self.entries.append(entry)
        return entry

    @property
    def digest(self) -> str:
        """Digest over every entry body and link, so altering any entry changes it."""
        return canonical_digest({"name": self.name, "n": len(self.entries), "entries": self.entries})

    def verify_chain(self) -> bool:
        """Recompute the chain; ``False`` if any entry was altered or reordered."""
        prev = "genesis"
        for i, entry in enumerate(self.entries):
            if entry.get("seq") != i or entry.get("prev_digest") != prev:
                return False
            body = {k: entry[k] for k in sorted(entry) if k != "digest"}
            if canonical_digest(body) != entry.get("digest"):
                return False
            prev = entry["digest"]
        return True

    def to_dict(self) -> Dict[str, Any]:
        return {"name": self.name, "n_entries": len(self.entries),
                "chain_digest": self.digest, "chain_valid": self.verify_chain(),
                "entries": list(self.entries)}