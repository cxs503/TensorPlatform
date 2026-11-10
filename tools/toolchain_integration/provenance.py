"""Provenance: canonical, reproducible digests for values crossing a pipeline edge.

Three rules make the digest usable as evidence:

1. **Number formatting is fixed** (``repr`` of the float after a round-trip through
   ``float``), so ``0.1 + 0.2`` and a serialised JSON copy hash identically.
2. **Arrays hash by their exact bytes plus shape/dtype**, so a silently reshaped
   array is a different digest even if the values are equal.
3. **Timing is excluded** (``wall_s``/``elapsed_s``/timestamps): a run whose numbers
   are identical must hash identically, otherwise reproducibility is untestable.
4. **Callables cannot be hashed**, so they hash by qualified name *and* the source
   text of their code object.  Two different callables that share a name therefore
   collide only if their bytecode does — which the tests falsify explicitly.

A node record chains its input digest with its output digest and the previous
node's chain digest, giving a single hash per run that changes if any stage's
inputs, outputs or ordering change.
"""

from __future__ import annotations

import hashlib
import inspect
import json
import math
from typing import Any, Dict, Iterable, List

import numpy as np

EMPTY_DIGEST = hashlib.sha256(b"tp.toolchain/empty").hexdigest()


#: Keys excluded from digests: timing is *cost*, not *evidence*, and would make
#: every run digest differ even when the numbers are bit-identical.
TIMING_KEYS = frozenset({"wall_s", "elapsed_s", "duration_s", "timestamp", "runtime_s"})


_MAX_DEPTH = 8


def _canon(obj: Any, _depth: int = 0) -> Any:
    if _depth > _MAX_DEPTH:  # pragma: no cover - guards pathological self-reference
        return "__too_deep__"
    if obj is None or isinstance(obj, (bool, str)):
        return obj
    if isinstance(obj, (int,)):
        return int(obj)
    if isinstance(obj, float):
        if math.isnan(obj):
            return "nan"
        if math.isinf(obj):
            return "inf" if obj > 0 else "-inf"
        return repr(float(obj))
    if isinstance(obj, np.generic):
        return _canon(obj.item())
    if isinstance(obj, np.ndarray):
        arr = np.ascontiguousarray(obj)
        return {
            "__ndarray__": hashlib.sha256(arr.tobytes()).hexdigest(),
            "shape": list(arr.shape),
            "dtype": str(arr.dtype),
        }
    if callable(obj):
        payload: Dict[str, Any] = {"__callable__": getattr(obj, "__qualname__", repr(obj))}
        code = getattr(obj, "__code__", None)
        if code is not None:
            # bytecode + constants + referenced names identify the implementation.
            # Closure *values* are deliberately excluded (they may be arrays and are
            # carried by the spec digest); ``repr(obj)`` is never used because it
            # embeds an address that changes every run.
            payload["code_hash"] = hashlib.sha256(
                code.co_code
                + repr(code.co_consts).encode("utf-8", "replace")
                + repr(code.co_names).encode("utf-8", "replace")
            ).hexdigest()
        else:  # builtins / numpy ufuncs have no __code__
            payload["code_hash"] = hashlib.sha256(
                f"{getattr(obj, '__module__', '')}:{getattr(obj, '__qualname__', '')}".encode()
            ).hexdigest()
        # captured parameters are part of what the callable *is*: two closures over
        # the same code with different constants must not hash alike.
        closure = getattr(obj, "__closure__", None) or ()
        freevars = getattr(code, "co_freevars", ()) if code is not None else ()
        captured: Dict[str, Any] = {}
        for name, cell in zip(freevars, closure):
            try:
                captured[str(name)] = _canon(cell.cell_contents, _depth + 1)
            except ValueError:  # empty cell
                captured[str(name)] = "__empty__"
        payload["closure"] = captured
        return payload
    if isinstance(obj, dict):
        return {
            str(k): _canon(v)
            for k, v in sorted(obj.items(), key=lambda kv: str(kv[0]))
            if str(k) not in TIMING_KEYS
        }
    if isinstance(obj, (list, tuple)):
        return [_canon(v, _depth + 1) for v in obj]
    if hasattr(obj, "to_dict"):
        return _canon(obj.to_dict(), _depth + 1)
    if isinstance(obj, (set, frozenset)):
        return sorted(_canon(v, _depth + 1) for v in obj)  # type: ignore[type-var]
    return repr(obj)


def canonical_json(obj: Any) -> str:
    return json.dumps(_canon(obj), sort_keys=True, separators=(",", ":"))


def digest(obj: Any) -> str:
    """sha256 over the canonical form of ``obj``."""
    return hashlib.sha256(canonical_json(obj).encode("utf-8")).hexdigest()


def chain(previous: str, node_id: str, input_digest: str, output_digest: str) -> str:
    """Hash-chain link: order-sensitive, so node reordering changes the run hash."""
    payload = f"{previous}|{node_id}|{input_digest}|{output_digest}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def source_digest(*targets: Any) -> str:
    """Digest of the *source text* of the given callables/modules (implementation id)."""
    parts: List[str] = []
    for t in targets:
        try:
            parts.append(inspect.getsource(t))
        except (OSError, TypeError):  # pragma: no cover - C extensions
            parts.append(repr(t))
    return digest(parts)


def summarize(values: Iterable[float]) -> Dict[str, float]:
    """Compact numeric summary used in reports (never a substitute for the data)."""
    arr = np.asarray(list(values), dtype=float).reshape(-1)
    if arr.size == 0:
        return {"count": 0.0}
    return {
        "count": float(arr.size),
        "min": float(arr.min()),
        "max": float(arr.max()),
        "mean": float(arr.mean()),
        "std": float(arr.std(ddof=0)),
    }