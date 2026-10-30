"""
Ship performance calculation timing collector.

Records wall-clock and CPU time for each calculation call,
supports percentile baseline (p50/p90/p99) per endpoint.
"""
from __future__ import annotations

import functools
import os
import time
import threading
from typing import Any, Dict, List, Optional

# ---------------------------------------------------------------------------
# TimingCollector – thread-safe in-memory store
# ---------------------------------------------------------------------------

class TimingCollector:
    """Collects per-call timing records and computes percentile baselines."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._records: List[Dict[str, Any]] = []

    # -- public API ----------------------------------------------------------

    def record(
        self,
        endpoint: str,
        ship_type: str = "unknown",
        wall_clock_ms: float = 0.0,
        cpu_ms: float = 0.0,
        timestamp: Optional[float] = None,
    ) -> None:
        """Append one timing record."""
        if timestamp is None:
            timestamp = time.time()
        entry = {
            "endpoint": endpoint,
            "ship_type": ship_type,
            "wall_clock_ms": round(wall_clock_ms, 3),
            "cpu_ms": round(cpu_ms, 3),
            "timestamp": round(timestamp, 3),
        }
        with self._lock:
            self._records.append(entry)

    def get_records(self) -> List[Dict[str, Any]]:
        """Return a copy of all records."""
        with self._lock:
            return list(self._records)

    def get_baseline(self) -> Dict[str, Any]:
        """Return p50/p90/p99 wall-clock latency per endpoint + sample count."""
        with self._lock:
            records = list(self._records)

        # Group by endpoint
        by_endpoint: Dict[str, List[float]] = {}
        for r in records:
            ep = r["endpoint"]
            by_endpoint.setdefault(ep, []).append(r["wall_clock_ms"])

        result: Dict[str, Any] = {}
        for ep, values in sorted(by_endpoint.items()):
            values_sorted = sorted(values)
            n = len(values_sorted)
            result[ep] = {
                "count": n,
                "p50": _percentile(values_sorted, 50),
                "p90": _percentile(values_sorted, 90),
                "p99": _percentile(values_sorted, 99),
            }
        return result

    def clear(self) -> None:
        """Remove all records."""
        with self._lock:
            self._records.clear()


# ---------------------------------------------------------------------------
# Percentile helper
# ---------------------------------------------------------------------------

def _percentile(sorted_values: List[float], pct: float) -> float:
    """Compute percentile from a *sorted* list using linear interpolation."""
    if not sorted_values:
        return 0.0
    n = len(sorted_values)
    if n == 1:
        return sorted_values[0]
    # Use the "exclusive" method (same as numpy default)
    rank = (pct / 100.0) * (n - 1)
    lo = int(rank)
    hi = lo + 1
    frac = rank - lo
    if hi >= n:
        return sorted_values[-1]
    return round(sorted_values[lo] + frac * (sorted_values[hi] - sorted_values[lo]), 3)


# ---------------------------------------------------------------------------
# Decorator – @timed_calc
# ---------------------------------------------------------------------------

# Global singleton (shared across the process)
_collector: Optional[TimingCollector] = None
_collector_lock = threading.Lock()

# Sampling: record 1 out of every N calls to limit overhead
_SAMPLING_INTERVAL = int(os.environ.get("TIMING_SAMPLE_INTERVAL", "1"))  # default=1 (every call)
_call_counters: Dict[str, int] = {}
_counter_lock = threading.Lock()


def get_collector() -> TimingCollector:
    global _collector
    if _collector is None:
        with _collector_lock:
            if _collector is None:
                _collector = TimingCollector()
    return _collector


def _extract_ship_type(args, kwargs, default: str) -> str:
    """Try to extract ship_type from positional args or keyword argument values."""
    # Check kwargs first – FastAPI passes request models as keyword args
    for key, value in kwargs.items():
        if hasattr(value, "ship_type"):
            return str(value.ship_type)
        if isinstance(value, dict) and "ship_type" in value:
            return str(value["ship_type"])
    # Check positional args
    for obj in args:
        if hasattr(obj, "ship_type"):
            return str(obj.ship_type)
        if isinstance(obj, dict) and "ship_type" in obj:
            return str(obj["ship_type"])
    return default


def timed_calc(endpoint: str, ship_type: str = "unknown", sample_interval: int = 0):
    """
    Decorator that records wall-clock and CPU time for a calculation endpoint.

    Parameters
    ----------
    endpoint : str
        Logical name of the calculation (e.g. 'resistance', 'stability').
    ship_type : str
        Default ship type label; overridden by request.ship_type if present.
    sample_interval : int, optional
        If > 0, only record 1 out of every *sample_interval* calls.
        If 0 (default), uses the global TIMING_SAMPLE_INTERVAL env var.
    """
    interval = sample_interval if sample_interval > 0 else _SAMPLING_INTERVAL

    def decorator(func):
        @functools.wraps(func)
        async def async_wrapper(*args, **kwargs):
            should_record = _should_record(endpoint, interval)
            t0_wall = time.perf_counter()
            t0_cpu = time.process_time()
            try:
                result = await func(*args, **kwargs)
                return result
            finally:
                if should_record:
                    wall_ms = (time.perf_counter() - t0_wall) * 1000
                    cpu_ms = (time.process_time() - t0_cpu) * 1000
                    actual_ship_type = _extract_ship_type(args, kwargs, ship_type)
                    get_collector().record(
                        endpoint=endpoint,
                        ship_type=actual_ship_type,
                        wall_clock_ms=wall_ms,
                        cpu_ms=cpu_ms,
                    )

        @functools.wraps(func)
        def sync_wrapper(*args, **kwargs):
            should_record = _should_record(endpoint, interval)
            t0_wall = time.perf_counter()
            t0_cpu = time.process_time()
            try:
                result = func(*args, **kwargs)
                return result
            finally:
                if should_record:
                    wall_ms = (time.perf_counter() - t0_wall) * 1000
                    cpu_ms = (time.process_time() - t0_cpu) * 1000
                    actual_ship_type = _extract_ship_type(args, kwargs, ship_type)
                    get_collector().record(
                        endpoint=endpoint,
                        ship_type=actual_ship_type,
                        wall_clock_ms=wall_ms,
                        cpu_ms=cpu_ms,
                    )

        # Detect async vs sync
        import asyncio
        if asyncio.iscoroutinefunction(func):
            return async_wrapper
        return sync_wrapper

    return decorator


def _should_record(endpoint: str, interval: int) -> bool:
    """Return True if this call should be recorded (sampling logic)."""
    if interval <= 1:
        return True
    with _counter_lock:
        _call_counters.setdefault(endpoint, 0)
        _call_counters[endpoint] += 1
        return _call_counters[endpoint] % interval == 0


def reset_counters() -> None:
    """Reset sampling counters (useful for tests)."""
    with _counter_lock:
        _call_counters.clear()
