"""Policies for campaign DAG self-healing.

All knobs are plain numbers so that they can live in
``campaign_plans.metadata["selfheal"]`` and be tuned per campaign without a
schema change.  Defaults are the acceptance thresholds of node P162W1-N08.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class RetryPolicy:
    """Exponential backoff retry policy.

    ``delay(n) = min(base_delay_s * factor ** n, max_delay_s)`` where ``n`` is
    the *consecutive* failure count (1 for the first failure).  With the
    defaults the waits are 4 s, 8 s and 16 s — exactly the node spec's
    ``delay = base * 2 ** fail_count`` for ``base = 2 s``.

    ``max_retries`` counts retries *after* the first attempt, so a node is
    attempted at most ``max_retries + 1`` times.
    """

    max_retries: int = 3
    base_delay_s: float = 2.0
    factor: float = 2.0
    max_delay_s: float = 300.0
    #: Deterministic by default: audit chains must be reproducible.
    jitter_ratio: float = 0.0

    def __post_init__(self) -> None:
        if self.max_retries < 0:
            raise ValueError("max_retries must be >= 0")
        if self.base_delay_s <= 0:
            raise ValueError("base_delay_s must be > 0")
        if self.factor < 1.0:
            raise ValueError("factor must be >= 1.0")
        if self.max_delay_s <= 0:
            raise ValueError("max_delay_s must be > 0")
        if not 0.0 <= self.jitter_ratio <= 1.0:
            raise ValueError("jitter_ratio must be within [0, 1]")

    def delay_for(self, fail_count: int) -> float:
        """Backoff delay in seconds for the ``fail_count``-th consecutive fail."""
        n = max(int(fail_count), 1)
        raw = self.base_delay_s * (self.factor ** n)
        return float(min(raw, self.max_delay_s))

    def to_dict(self) -> dict[str, Any]:
        return {
            "max_retries": self.max_retries,
            "base_delay_s": self.base_delay_s,
            "factor": self.factor,
            "max_delay_s": self.max_delay_s,
            "jitter_ratio": self.jitter_ratio,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> "RetryPolicy":
        data = dict(data or {})
        kwargs = {k: data[k] for k in cls.__dataclass_fields__ if k in data}
        return cls(**kwargs)


@dataclass(frozen=True)
class ResourcePolicy:
    """Cluster load awareness for elastic scheduling.

    ``saturation_threshold`` is the load ratio above which the scheduler only
    dispatches critical-path work.  Deferred work never waits longer than
    ``max_defer_s`` (acceptance C5: scheduling latency < 30 s under load).
    """

    saturation_threshold: float = 0.80
    max_defer_s: float = 30.0
    #: Queue length that maps to a load ratio of 1.0.
    queue_capacity: int = 64

    def __post_init__(self) -> None:
        if not 0.0 < self.saturation_threshold < 1.0:
            raise ValueError("saturation_threshold must be within (0, 1)")
        if self.max_defer_s <= 0:
            raise ValueError("max_defer_s must be > 0")
        if self.queue_capacity <= 0:
            raise ValueError("queue_capacity must be > 0")

    def to_dict(self) -> dict[str, Any]:
        return {
            "saturation_threshold": self.saturation_threshold,
            "max_defer_s": self.max_defer_s,
            "queue_capacity": self.queue_capacity,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> "ResourcePolicy":
        data = dict(data or {})
        kwargs = {k: data[k] for k in cls.__dataclass_fields__ if k in data}
        return cls(**kwargs)


@dataclass(frozen=True)
class LoadSample:
    """A single point-in-time cluster load observation.

    Percentages are 0-100 to match what monitoring systems emit; they are
    normalised to ratios internally.
    """

    cpu_pct: float = 0.0
    mem_pct: float = 0.0
    queue_len: int = 0
    queue_capacity: int = 64

    def ratios(self) -> dict[str, float]:
        cap = max(int(self.queue_capacity), 1)
        return {
            "cpu": max(0.0, min(float(self.cpu_pct) / 100.0, 1.0)),
            "mem": max(0.0, min(float(self.mem_pct) / 100.0, 1.0)),
            "queue": max(0.0, min(float(self.queue_len) / cap, 1.0)),
        }

    def saturation(self) -> float:
        """Worst resource ratio — the quantity compared to the threshold."""
        return max(self.ratios().values())

    def is_saturated(self, threshold: float = 0.80) -> bool:
        return self.saturation() > threshold

    def to_dict(self) -> dict[str, Any]:
        return {
            "cpu_pct": self.cpu_pct,
            "mem_pct": self.mem_pct,
            "queue_len": self.queue_len,
            "queue_capacity": self.queue_capacity,
            "ratios": self.ratios(),
            "saturation": self.saturation(),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> "LoadSample":
        data = dict(data or {})
        return cls(
            cpu_pct=float(data.get("cpu_pct", 0.0)),
            mem_pct=float(data.get("mem_pct", 0.0)),
            queue_len=int(data.get("queue_len", 0)),
            queue_capacity=int(data.get("queue_capacity", 64)),
        )


@dataclass(frozen=True)
class DegradationPolicy:
    """Degraded-execution guard.

    A degraded result is only accepted when its relative deviation from the
    last known normal-precision result stays below ``max_deviation`` (C3).
    """

    enabled: bool = True
    max_deviation: float = 0.15

    def __post_init__(self) -> None:
        if self.max_deviation <= 0:
            raise ValueError("max_deviation must be > 0")

    def to_dict(self) -> dict[str, Any]:
        return {"enabled": self.enabled, "max_deviation": self.max_deviation}

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> "DegradationPolicy":
        data = dict(data or {})
        kwargs = {k: data[k] for k in cls.__dataclass_fields__ if k in data}
        return cls(**kwargs)


@dataclass(frozen=True)
class SelfHealPolicy:
    """Bundle persisted under ``campaign_plans.metadata["selfheal"]``."""

    retry: RetryPolicy = field(default_factory=RetryPolicy)
    resources: ResourcePolicy = field(default_factory=ResourcePolicy)
    degradation: DegradationPolicy = field(default_factory=DegradationPolicy)
    #: When retries and degradation are both exhausted, skip the node so its
    #: dependants still run (C2) instead of failing the whole branch (False).
    bypass_on_exhaust: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "retry": self.retry.to_dict(),
            "resources": self.resources.to_dict(),
            "degradation": self.degradation.to_dict(),
            "bypass_on_exhaust": self.bypass_on_exhaust,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> "SelfHealPolicy":
        data = dict(data or {})
        return cls(
            retry=RetryPolicy.from_dict(data.get("retry")),
            resources=ResourcePolicy.from_dict(data.get("resources")),
            degradation=DegradationPolicy.from_dict(data.get("degradation")),
            bypass_on_exhaust=bool(data.get("bypass_on_exhaust", True)),
        )