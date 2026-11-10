"""Data model for cross-solver regression (re-exports from benchmark_runner.model)."""
from __future__ import annotations

import math
from typing import Any, Optional


def is_finite(x: Any) -> bool:
    """Strict finite number check: bool/None/str/NaN/inf all return False."""
    if isinstance(x, bool) or x is None:
        return False
    if not isinstance(x, (int, float)):
        return False
    return math.isfinite(float(x))


def rel_error(actual: Any, reference: Any, eps: float = 1e-300) -> Optional[float]:
    """Relative error |a-r| / max(|r|, eps). Non-finite → None."""
    try:
        a, r = float(actual), float(reference)
    except (TypeError, ValueError):
        return None
    if not (math.isfinite(a) and math.isfinite(r)):
        return None
    denom = max(abs(r), eps)
    return abs(a - r) / denom
