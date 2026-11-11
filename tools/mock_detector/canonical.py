"""Canonicalisation, hashing and numeric comparison helpers.

Everything the detector decides is derived from machine data only: responses are
flattened into ``{path: float}`` vectors, hashed canonically (SHA-256) and
compared with explicit tolerances.  No verdict may be produced from anything
that is not recorded in the probe report.
"""
from __future__ import annotations

import hashlib
import json
import math
from typing import Any, Dict, Iterable, List, Optional, Tuple

#: digits kept when serialising floats — long enough to keep double precision
#: meaning, short enough that last-bit noise from a different summation order
#: does not masquerade as non-determinism.
SIG_DIGITS = 12

EPS = 1e-300


def flatten_numeric(obj: Any, prefix: str = "") -> Dict[str, float]:
    """Flatten a JSON document into ``{dotted.path: float}`` for numerics only.

    Booleans are ignored on purpose: ``{"ok": true}`` is metadata, not a
    computed quantity, and counting it as an output would let a status flag
    stand in for a result.
    """
    out: Dict[str, float] = {}
    if isinstance(obj, dict):
        for key, val in obj.items():
            out.update(flatten_numeric(val, f"{prefix}{key}."))
    elif isinstance(obj, (list, tuple)):
        base = prefix.rstrip(".")
        for idx, val in enumerate(obj):
            out.update(flatten_numeric(val, f"{base}[{idx}]."))
    elif isinstance(obj, bool):
        return out
    elif isinstance(obj, (int, float)):
        fval = float(obj)
        if math.isnan(fval) or math.isinf(fval):
            return out
        out[prefix.rstrip(".")] = fval
    return out


def _norm_float(x: float) -> float:
    return float(f"{x:.{SIG_DIGITS}g}")


def canonical_json(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def payload_sha256(obj: Any) -> str:
    """SHA-256 of the *whole* response document (canonical JSON)."""
    return sha256_text(canonical_json(obj))


def numeric_sha256(vec: Dict[str, float]) -> str:
    """SHA-256 of the numeric output vector (sorted, normalised floats).

    This is the fingerprint the determinism probe compares: a response whose
    only moving part is a request id / timestamp keeps the same numeric hash,
    so volatile metadata cannot be mistaken for a random algorithm.
    """
    norm = {k: _norm_float(v) for k, v in sorted(vec.items())}
    return sha256_text(canonical_json(norm))


def rel_diff(a: float, b: float) -> float:
    return abs(a - b) / max(abs(a), abs(b), EPS)


def compare_vectors(base: Dict[str, float], other: Dict[str, float]
                    ) -> Tuple[float, Optional[str], Optional[Tuple[float, float]], int]:
    """Worst relative difference over shared numeric paths.

    Returns ``(worst_rel, path, (base_value, other_value), n_compared)``.
    ``n_compared == 0`` means the comparison proved nothing and the caller must
    not treat it as equality (an empty comparison was an early false-pass path).
    """
    paths = sorted(set(base) & set(other))
    worst, worst_path, pair = 0.0, None, None
    for path in paths:
        d = rel_diff(base[path], other[path])
        if d > worst:
            worst, worst_path, pair = d, path, (base[path], other[path])
    return worst, worst_path, pair, len(paths)


def vector_span(values: Iterable[float]) -> float:
    vals = list(values)
    if not vals:
        return 0.0
    lo, hi = min(vals), max(vals)
    return (hi - lo) / max(abs(hi), abs(lo), EPS)


def distinct_count(hashes: Iterable[str]) -> int:
    return len(set(hashes))


def shape_of_series(values: List[float], tol: float = 1e-9) -> str:
    """Classify one output series along one input axis (sign-agnostic).

    ``constant``   — no movement beyond ``tol`` (relative span).
    ``monotone``   — successive differences never change sign (zeros allowed).
    ``non_monotone`` — directions alternate; still a legitimate physical
                       response (e.g. a hump or a dip), recorded, not fatal.
    ``undefined``  — fewer than two points.
    """
    if len(values) < 2:
        return "undefined"
    if vector_span(values) <= tol:
        return "constant"
    diffs = [b - a for a, b in zip(values, values[1:])]
    signs = {0 if abs(d) <= tol * max(abs(a), abs(b), EPS) else (1 if d > 0 else -1)
             for d, a, b in zip(diffs, values, values[1:])}
    signs.discard(0)
    if len(signs) <= 1:
        return "monotone"
    return "non_monotone"


def jump_ratio(values: List[float]) -> float:
    """Largest adjacent |Δ| divided by the mean |Δ| — a cheap smoothness index.

    A published lookup table with a few discrete steps shows a large ratio; a
    continuous formula shows a ratio near 1.  Informational only: real physics
    may legitimately step (piecewise fits), so this never decides a level.
    """
    if len(values) < 3:
        return 1.0
    diffs = [abs(b - a) for a, b in zip(values, values[1:])]
    mean = sum(diffs) / len(diffs)
    if mean <= EPS:
        return 1.0
    return max(diffs) / mean