"""Numerical primitives of the cross-solver consistency gate.

Everything here is deliberately physical-domain free: the module knows about
numbers, pairs of arms, curves on a common abscissa and digests -- never about a
particular quantity, geometry or unit.  Which quantity is compared, what its
reference value is, and how wide a tolerance is defensible all arrive through
``config/consistency/*.json`` at run time.

Design notes (they are part of the contract, so they are stated once here and
never re-invented by a caller):

``rel_diff``
    Symmetric and bounded: ``|a-b| / max(|a|,|b|)``.  A one-sided definition such
    as ``|a-b|/|a|`` makes the verdict depend on which arm happens to be listed
    first, which is exactly the kind of hidden freedom a consistency gate must
    not have.  Two zeros are consistent by definition (0.0); one zero against a
    non-zero value is total disagreement (1.0).

``pair_stats``
    Mean / population standard deviation / max over the pairwise differences of
    an arm set.  ``max_dev`` drives the verdict (a single rogue arm must not be
    averaged away), the mean and the spread describe the picture, and ``n_pairs``
    is reported so a "2 arms, 1 pair" verdict can never be mistaken for a
    well-sampled one.

``profile_l2_rel``
    Relative L2 distance of two curves defined on (possibly different)
    abscissae: both are resampled onto the union grid inside the shared interval
    and normalised by the RMS of the two norms,
    ``||a-b|| / sqrt((||a||^2 + ||b||^2)/2)``.  Comparing raw ``||a-b||`` would
    make the verdict scale with the number of sample points; normalising by a
    single arm's norm would again break symmetry.
"""
from __future__ import annotations

import hashlib
import json
import math
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

EPS = 1e-12


# ---------------------------------------------------------------------------
# canonicalisation / provenance
# ---------------------------------------------------------------------------
def canonical_json(obj: Any) -> str:
    """Deterministic serialisation used for every digest in this package."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
                      allow_nan=False)


def sha256_of(obj: Any) -> str:
    """sha256 of the canonical JSON form of ``obj``."""
    if isinstance(obj, (bytes, bytearray)):
        return hashlib.sha256(bytes(obj)).hexdigest()
    return hashlib.sha256(canonical_json(obj).encode("utf-8")).hexdigest()


def sha256_of_file(path: Any) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


# ---------------------------------------------------------------------------
# scalar comparison
# ---------------------------------------------------------------------------
def rel_diff(a: Optional[float], b: Optional[float]) -> Optional[float]:
    """Symmetric relative difference of two scalars (see module docstring)."""
    if a is None or b is None:
        return None
    a = float(a)
    b = float(b)
    if not (math.isfinite(a) and math.isfinite(b)):
        return None
    scale = max(abs(a), abs(b))
    if scale <= EPS:
        return 0.0
    return abs(a - b) / scale


def pair_stats(diffs: Sequence[Optional[float]]) -> Dict[str, Any]:
    """Mean / population std / max of a set of pairwise differences."""
    vals = [float(d) for d in diffs if d is not None and math.isfinite(float(d))]
    if not vals:
        return {"n_pairs": 0, "mean_dev": None, "std_dev": None, "max_dev": None,
                "min_dev": None}
    n = len(vals)
    mean = sum(vals) / n
    var = sum((v - mean) ** 2 for v in vals) / n
    return {
        "n_pairs": n,
        "mean_dev": mean,
        "std_dev": math.sqrt(var),
        "max_dev": max(vals),
        "min_dev": min(vals),
    }


def pairwise(items: Sequence[Tuple[str, Optional[float]]]) -> Dict[str, Any]:
    """All unordered pairs of ``(arm_id, value)`` with their relative difference.

    Returns ``{"pairs": [...], "matrix": {arm: {arm: diff}}, **pair_stats}``.
    Pairs where either side is missing are reported with ``rel_diff=None`` so a
    WITHHELD quantity is visible instead of silently shrinking the sample.
    """
    pairs: List[Dict[str, Any]] = []
    matrix: Dict[str, Dict[str, Optional[float]]] = {aid: {} for aid, _ in items}
    for i, (ai, vi) in enumerate(items):
        matrix[ai][ai] = 0.0
        for j in range(i + 1, len(items)):
            bj, vj = items[j]
            d = rel_diff(vi, vj)
            pairs.append({"a": ai, "b": bj, "value_a": vi, "value_b": vj, "rel_diff": d})
            matrix[ai][bj] = d
            matrix[bj][ai] = d
    stats = pair_stats([p["rel_diff"] for p in pairs])
    return {"pairs": pairs, "matrix": matrix, **stats}


def verdict_of(max_dev: Optional[float], rule: Dict[str, float],
               declared: bool = True) -> str:
    """Map a worst-case deviation onto the declared verdict vocabulary.

    ``consistent``          max_dev < rule["consistent"]
    ``acceptable``          rule["consistent"] <= max_dev < rule["acceptable"]
                            and the deviation is declared by the arms
    ``acceptable_undeclared`` same band, but no arm supplied a declaration (the
                            band is only defensible when someone says why)
    ``inconsistent``        max_dev >= rule["acceptable"]
    ``withheld``            no comparable pair at all -- absence of evidence is
                            never reported as agreement
    """
    if max_dev is None:
        return "withheld"
    lo = float(rule["consistent"])
    hi = float(rule["acceptable"])
    if max_dev < lo:
        return "consistent"
    if max_dev < hi:
        return "acceptable" if declared else "acceptable_undeclared"
    return "inconsistent"


# ---------------------------------------------------------------------------
# profile (curve) comparison
# ---------------------------------------------------------------------------
def _as_curve(profile: Any) -> Tuple[List[float], List[float]]:
    """Accept ``{"coordinate": [...], "value": [...]}`` or ``{"x": [], "y": []}``."""
    if not isinstance(profile, dict):
        raise TypeError("profile must be a mapping with coordinate/value arrays")
    x = profile.get("coordinate", profile.get("x"))
    y = profile.get("value", profile.get("y"))
    if x is None or y is None:
        raise ValueError("profile needs 'coordinate' and 'value' (or 'x'/'y')")
    xs = [float(v) for v in x]
    ys = [float(v) for v in y]
    if len(xs) != len(ys):
        raise ValueError("profile coordinate/value length mismatch")
    if len(xs) < 2:
        raise ValueError("profile needs at least two sample points")
    order = sorted(range(len(xs)), key=lambda k: xs[k])
    return [xs[k] for k in order], [ys[k] for k in order]


def _interp(xs: Sequence[float], ys: Sequence[float], q: Sequence[float]) -> List[float]:
    """Linear interpolation on a sorted abscissa, clamped at the ends."""
    out: List[float] = []
    n = len(xs)
    j = 0
    for x in q:
        if x <= xs[0]:
            out.append(ys[0])
            continue
        if x >= xs[-1]:
            out.append(ys[-1])
            continue
        while j < n - 2 and xs[j + 1] < x:
            j += 1
        x0, x1 = xs[j], xs[j + 1]
        y0, y1 = ys[j], ys[j + 1]
        if abs(x1 - x0) <= EPS:
            out.append(0.5 * (y0 + y1))
        else:
            out.append(y0 + (y1 - y0) * (x - x0) / (x1 - x0))
    return out


def profile_l2_rel(a: Any, b: Any, n_grid: int = 401) -> Dict[str, Any]:
    """Relative L2 distance of two curves sampled on different abscissae."""
    xa, ya = _as_curve(a)
    xb, yb = _as_curve(b)
    lo = max(min(xa), min(xb))
    hi = min(max(xa), max(xb))
    if not (hi > lo):
        raise ValueError("profiles do not overlap on the abscissa")
    n = max(3, int(n_grid))
    grid = [lo + (hi - lo) * k / (n - 1) for k in range(n)]
    ga = _interp(xa, ya, grid)
    gb = _interp(xb, yb, grid)
    num = math.sqrt(sum((p - q) ** 2 for p, q in zip(ga, gb)))
    na = sum(p * p for p in ga)
    nb = sum(q * q for q in gb)
    den = math.sqrt(0.5 * (na + nb))
    if den <= EPS:
        return {"l2_rel": 0.0 if num <= EPS else 1.0, "n_grid": n,
                "overlap": [lo, hi]}
    return {"l2_rel": num / den, "n_grid": n, "overlap": [lo, hi],
            "abs_l2": num, "ref_norm": den}