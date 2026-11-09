"""Explicit clock semantics for multi-solver coupling (domain-neutral).

Coupled solvers rarely share a time step.  Instead of leaving that implicit in
ad-hoc call ordering, this module makes the *contract* an object:

:class:`ClockContract`
    master step ``dt_master``, sub step ``dt_sub`` and the requirement that
    ``dt_master = k · dt_sub`` with integer ``k ≥ 1``.  A non-integer ratio, a
    negative time or a sub step larger than the master step is rejected at
    construction (fail-closed at the boundary of the contract).

:class:`ClockMode`
    the four legal exchange semantics:

    ``SYNC``                    exchange only at times where both solvers have a sample
    ``LINEAR_INTERPOLATION``    the coarser side linearly interpolates between two stored samples
    ``ONE_STEP_EXTRAPOLATION``  at most ``max_extrapolation_steps`` steps beyond the last sample
    ``EVENT_TRIGGERED``         the finer side publishes at events; between events the value is
                                *stale* and only valid within ``max_staleness_steps``

:class:`TimeSeriesExchange`
    stores aligned samples on the sub grid and answers ``request(t)`` according to
    the mode.  Every legitimate answer is returned with the provenance that makes
    it legitimate (interpolated / extrapolated / stale), and every request outside
    the contract raises :class:`ClockContractViolation` — an out-of-contract
    exchange can never be answered silently with a stale or invented value.

All times are recorded in a hash-chained :class:`~tools.coupling.provenance.ExchangeLedger`.
"""

from __future__ import annotations

from dataclasses import dataclass, field as _dc_field
from enum import Enum
from typing import Any, Dict, List, Optional, Sequence

import numpy as np

from .provenance import ExchangeLedger, canonical_digest

__all__ = [
    "ClockContractViolation",
    "ClockMode",
    "ClockContract",
    "ExchangeSample",
    "TimeSeriesExchange",
]


class ClockContractViolation(RuntimeError):
    """Raised whenever an exchange falls outside the declared clock contract."""


class ClockMode(str, Enum):
    SYNC = "sync"
    LINEAR_INTERPOLATION = "linear_interpolation"
    ONE_STEP_EXTRAPOLATION = "one_step_extrapolation"
    EVENT_TRIGGERED = "event_triggered"


@dataclass(frozen=True)
class ClockContract:
    """Declared master/sub step relationship and exchange semantics."""

    dt_master: float
    dt_sub: float
    mode: ClockMode = ClockMode.SYNC
    tolerance: float = 1e-12
    max_extrapolation_steps: int = 1
    max_staleness_steps: int = 1

    def __post_init__(self) -> None:
        try:
            object.__setattr__(self, "mode", ClockMode(self.mode))
        except ValueError as exc:
            raise ClockContractViolation(f"unknown clock mode: {self.mode!r}") from exc
        for name in ("dt_master", "dt_sub"):
            value = getattr(self, name)
            if not np.isfinite(value) or value <= 0.0:
                raise ClockContractViolation(f"{name} must be finite and positive, got {value!r}")
        if self.dt_sub > self.dt_master * (1.0 + self.tolerance):
            raise ClockContractViolation(
                f"sub step {self.dt_sub!r} exceeds master step {self.dt_master!r}: subcycling requires "
                "dt_sub <= dt_master"
            )
        ratio = self.dt_master / self.dt_sub
        if abs(ratio - round(ratio)) > 1e-9 * max(1.0, abs(ratio)):
            raise ClockContractViolation(
                f"dt_master/dt_sub = {ratio!r} is not an integer: the subcycling contract "
                "dt_master = k * dt_sub cannot be satisfied"
            )
        if int(round(ratio)) < 1:
            raise ClockContractViolation("the subcycling ratio must be >= 1")
        if self.max_extrapolation_steps < 0:
            raise ClockContractViolation("max_extrapolation_steps must be >= 0")
        if self.max_staleness_steps < 0:
            raise ClockContractViolation("max_staleness_steps must be >= 0")

    # ------------------------------------------------------------------ ratios
    @property
    def ratio(self) -> int:
        """``k`` in ``dt_master = k · dt_sub``."""
        return int(round(self.dt_master / self.dt_sub))

    def atol(self, t: float = 0.0) -> float:
        """Absolute time tolerance (never below a few ULPs of the largest time seen)."""
        return max(self.tolerance * max(abs(t), self.dt_sub), 64.0 * np.finfo(float).eps * max(abs(t), 1.0))

    def substep_times(self, master_step: int) -> np.ndarray:
        """Sub-step times inside ``master_step`` (excluding the master tick itself)."""
        if master_step < 0:
            raise ClockContractViolation("master_step must be >= 0")
        k = self.ratio
        return np.array(
            [(master_step * k + j) * self.dt_sub for j in range(k)], dtype=float
        )

    def master_time(self, master_step: int) -> float:
        if master_step < 0:
            raise ClockContractViolation("master_step must be >= 0")
        return float(master_step * self.ratio * self.dt_sub)

    def on_sub_grid(self, t: float) -> bool:
        """``True`` if ``t`` is a sub-step time within tolerance."""
        steps = t / self.dt_sub
        return abs(steps - round(steps)) * self.dt_sub <= self.atol(t)

    def digest(self) -> str:
        return canonical_digest(self)


@dataclass
class ExchangeSample:
    """Value returned by a request, plus how it was obtained."""

    t: float
    values: np.ndarray
    mode: str
    interpolated: bool = False
    extrapolated: bool = False
    stale: bool = False
    source_times: Sequence[float] = ()

    def to_dict(self) -> Dict[str, Any]:
        return {
            "t": self.t,
            "mode": self.mode,
            "interpolated": self.interpolated,
            "extrapolated": self.extrapolated,
            "stale": self.stale,
            "source_times": list(self.source_times),
            "values_digest": canonical_digest(self.values),
        }


class TimeSeriesExchange:
    """Store sub-step samples and answer contract-checked requests."""

    def __init__(self, contract: ClockContract, name: str = "exchange"):
        self.contract = contract
        self.name = name
        self._times: List[float] = []
        self._values: List[np.ndarray] = []
        self._events: List[tuple] = []          # (t, values) for EVENT_TRIGGERED
        self.ledger = ExchangeLedger(name=f"{name}-ledger")
        self.ledger.append(action="open", contract=contract.digest(), mode=contract.mode.value)

    # ------------------------------------------------------------------ writes
    def store(self, t: float, values) -> None:
        """Record a sample at a sub-grid time ``t`` (strictly increasing)."""
        t = float(t)
        if not np.isfinite(t) or t < 0.0:
            raise ClockContractViolation(f"sample time must be finite and >= 0, got {t!r}")
        if not self.contract.on_sub_grid(t):
            raise ClockContractViolation(
                f"sample time {t!r} is not a multiple of dt_sub={self.contract.dt_sub!r}"
            )
        arr = np.array(values, dtype=float, copy=True)
        if not np.all(np.isfinite(arr)):
            raise ClockContractViolation("sample values contain non-finite entries")
        if self._times and t <= self._times[-1] + self.contract.atol(t):
            raise ClockContractViolation(
                f"sample times must increase strictly: got {t!r} after {self._times[-1]!r}"
            )
        self._times.append(t)
        self._values.append(arr)
        self.ledger.append(action="store", t=t, values=arr)

    def publish_event(self, t: float, values) -> None:
        """Record an event-triggered publication (``EVENT_TRIGGERED`` mode only)."""
        if self.contract.mode is not ClockMode.EVENT_TRIGGERED:
            raise ClockContractViolation(
                f"publish_event is only legal in {ClockMode.EVENT_TRIGGERED.value!r} mode"
            )
        t = float(t)
        arr = np.array(values, dtype=float, copy=True)
        if self._events and t <= self._events[-1][0] + self.contract.atol(t):
            raise ClockContractViolation("event times must increase strictly")
        self._events.append((t, arr))
        self.ledger.append(action="event", t=t, values=arr)

    # ------------------------------------------------------------------ reads
    @property
    def times(self) -> np.ndarray:
        return np.asarray(self._times, dtype=float)

    def request(self, t: float) -> ExchangeSample:
        """Answer for time ``t`` under the declared mode, or raise."""
        t = float(t)
        if not np.isfinite(t) or t < 0.0:
            raise ClockContractViolation(f"request time must be finite and >= 0, got {t!r}")
        mode = self.contract.mode
        if mode is ClockMode.EVENT_TRIGGERED:
            sample = self._request_event(t)
        elif not self._times:
            raise ClockContractViolation("no samples stored: an exchange cannot be answered")
        elif mode is ClockMode.SYNC:
            sample = self._request_sync(t)
        elif mode is ClockMode.LINEAR_INTERPOLATION:
            sample = self._request_linear(t)
        elif mode is ClockMode.ONE_STEP_EXTRAPOLATION:
            sample = self._request_extrapolate(t)
        else:  # pragma: no cover - guarded by ClockMode
            raise ClockContractViolation(f"unknown mode: {mode!r}")
        record = {k: v for k, v in sample.to_dict().items() if k != "t"}
        self.ledger.append(action="request", t=t, **record)
        return sample

    # -------------------------------------------------------------- internals
    def _exact(self, t: float) -> Optional[int]:
        atol = self.contract.atol(t)
        for i, ts in enumerate(self._times):
            if abs(ts - t) <= atol:
                return i
        return None

    def _request_sync(self, t: float) -> ExchangeSample:
        idx = self._exact(t)
        if idx is None:
            raise ClockContractViolation(
                f"SYNC request at t={t!r} has no matching stored sample "
                f"(stored {self.times.tolist()[:4]}...{self.times.tolist()[-2:]}, n={len(self._times)})"
            )
        return ExchangeSample(t=t, values=self._values[idx].copy(), mode="sync",
                              source_times=(self._times[idx],))

    def _bracket(self, t: float) -> Optional[tuple]:
        times = self.times
        atol = self.contract.atol(t)
        if t <= times[0] + atol or t >= times[-1] - atol:
            idx = self._exact(t)
            return None if idx is None else (idx, idx)
        hi = int(np.searchsorted(times, t))
        return (hi - 1, hi)

    def _request_linear(self, t: float) -> ExchangeSample:
        pair = self._bracket(t)
        if pair is None:
            raise ClockContractViolation(
                f"LINEAR_INTERPOLATION request at t={t!r} outside the sampled window "
                f"[{self.times[0]!r}, {self.times[-1]!r}]: interpolation may not extrapolate"
            )
        i, j = pair
        if i == j:
            return ExchangeSample(t=t, values=self._values[i].copy(), mode="linear_interpolation",
                                  source_times=(self._times[i],))
        t0, t1 = self._times[i], self._times[j]
        w = (t - t0) / (t1 - t0)
        values = (1.0 - w) * self._values[i] + w * self._values[j]
        return ExchangeSample(t=t, values=values, mode="linear_interpolation",
                              interpolated=True, source_times=(t0, t1))

    def _request_extrapolate(self, t: float) -> ExchangeSample:
        idx = self._exact(t)
        if idx is not None:
            return ExchangeSample(t=t, values=self._values[idx].copy(), mode="one_step_extrapolation",
                                  source_times=(self._times[idx],))
        times = self.times
        if len(times) < 2:
            raise ClockContractViolation(
                "one-step extrapolation needs at least two stored samples"
            )
        horizon = self.contract.max_extrapolation_steps * self.contract.dt_sub
        over = t - times[-1]
        if over <= 0.0 or over > horizon + self.contract.atol(t):
            raise ClockContractViolation(
                f"request t={t!r} is {over!r} beyond the last sample {times[-1]!r}; the contract "
                f"allows at most {self.contract.max_extrapolation_steps} step(s) ({horizon!r})"
            )
        t0, t1 = times[-2], times[-1]
        slope = (self._values[-1] - self._values[-2]) / (t1 - t0)
        values = self._values[-1] + slope * (t - t1)
        return ExchangeSample(t=t, values=values, mode="one_step_extrapolation",
                              extrapolated=True, source_times=(t0, t1))

    def _request_event(self, t: float) -> ExchangeSample:
        if not self._events:
            raise ClockContractViolation("no events published: event-triggered exchange cannot answer")
        t_event, values = self._events[-1]
        if t < t_event - self.contract.atol(t):
            # asked for a time before the only available event -> find the bracketing past event
            past = [e for e in self._events if e[0] <= t + self.contract.atol(t)]
            if not past:
                raise ClockContractViolation(
                    f"event-triggered request at t={t!r} precedes the first event {self._events[0][0]!r}"
                )
            t_event, values = past[-1]
        staleness = t - t_event
        horizon = self.contract.max_staleness_steps * self.contract.dt_sub
        if staleness > horizon + self.contract.atol(t):
            raise ClockContractViolation(
                f"event-triggered value is stale by {staleness!r} > allowed {horizon!r} "
                f"(last event at {t_event!r})"
            )
        return ExchangeSample(t=t, values=values.copy(), mode="event_triggered",
                              stale=bool(staleness > self.contract.atol(t)), source_times=(t_event,))

    # ------------------------------------------------------------------ audit
    def to_dict(self) -> Dict[str, Any]:
        return {
            "contract": {
                "dt_master": self.contract.dt_master,
                "dt_sub": self.contract.dt_sub,
                "ratio": self.contract.ratio,
                "mode": self.contract.mode.value,
                "max_extrapolation_steps": self.contract.max_extrapolation_steps,
                "max_staleness_steps": self.contract.max_staleness_steps,
            },
            "n_samples": len(self._times),
            "n_events": len(self._events),
            "ledger": self.ledger.to_dict(),
        }