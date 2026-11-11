"""Conservative boundary exchange on the global sync grid.

Interface contract
------------------
Two solvers exchange *conserved quantities* across a shared boundary.  What
crosses a frame (one consumer step) is a **quantity**, never a point value: the
consumer needs ``Q_k = int_{frame k} q dt``.  Each channel therefore has an
analytic rate ``q(t)``, so the exact frame amount is known independently of any
discretisation (synthetic manufactured solution: nothing is compared against
itself).

Two protocols are audited — the two a coupling library can actually offer:

``exchange_synced``  (barrier + nested sub-sampling)
    The producer publishes the rate at every node of the global grid; the
    consumer integrates the nested sub-samples inside each of its own frames.
    Because the consumer's nodes are a subset of the producer's, the frame
    amounts agree with the exact integral to the composite trapezoid error on
    the *global* step.

``exchange_free_running``  (no shared clock)
    The producer publishes only at its own step times; the consumer steps at its
    own step times and can only use what already exists (causality), i.e. the
    last published sample, booked over its whole frame (zero-order hold) or
    linearly extrapolated from the two last samples (optimistic consumer).

Two different statements are reported, and they are *not* interchangeable:

``net_defect``  ``|sum_k booked_k - sum_k required_k| / sum_k |required_k|``
    The familiar mass/momentum/energy balance over the whole run.  It is
    structurally small for *any* consistent sampling scheme, because the
    left-endpoint Riemann error telescopes to the two horizon ends
    (``h/2 * (q(T) - q(0))``).  A net-only gate therefore cannot see a missing
    clock: it passes a protocol that is off by a fraction of a step in every
    single frame.

``frame_l1_defect``  ``sum_k |booked_k - required_k| / sum_k |required_k|``
    Total variation of the per-frame error: how much of the interface traffic was
    booked with the wrong amount.  This is the metric that discriminates the two
    protocols, and the one the acceptance gate uses.

Both are reported for every channel; the report states which one the criterion
is attached to and why.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence

import numpy as np

from .clock_manager import (
    ClockContractViolation,
    GlobalClockManager,
    SolverClock,
    SyncGrid,
)

TWO_PI = 2.0 * np.pi


@dataclass(frozen=True)
class Channel:
    """One exchanged conserved quantity, given by an analytic rate ``q(t)``.

    ``q(t) = base + amp*sin(2*pi*freq*t + phase) + amp2*sin(4*pi*freq*t + phase2)``
    """

    name: str
    base: float = 1.0
    amp: float = 0.5
    freq: float = 8.0
    phase: float = 0.0
    amp2: float = 0.0
    phase2: float = 0.0
    tag: str = ""

    def rate(self, t):
        t = np.asarray(t, dtype=float)
        value = self.base + self.amp * np.sin(TWO_PI * self.freq * t + self.phase)
        if self.amp2:
            value = value + self.amp2 * np.sin(2 * TWO_PI * self.freq * t + self.phase2)
        return value if value.ndim else float(value)

    def integral(self, a: float, b: float) -> float:
        """Exact ``int_a^b q dt``."""
        w = TWO_PI * self.freq
        total = self.base * (b - a)
        total += self.amp * (np.cos(w * a + self.phase) - np.cos(w * b + self.phase)) / w
        if self.amp2:
            total += (self.amp2 * (np.cos(2 * w * a + self.phase2)
                                   - np.cos(2 * w * b + self.phase2)) / (2 * w))
        return float(total)

    @property
    def scale(self) -> float:
        return abs(self.base) + abs(self.amp) + abs(self.amp2)

    def to_dict(self) -> Dict[str, object]:
        return {
            "name": self.name,
            "tag": self.tag,
            "base": self.base,
            "amp": self.amp,
            "freq": self.freq,
            "phase": self.phase,
            "amp2": self.amp2,
            "phase2": self.phase2,
            "integral_one_period": self.integral(0.0, 1.0 / self.freq),
        }


@dataclass
class ChannelLedger:
    """Producer account, consumer account, exact amounts and per-frame error."""

    channel: str
    protocol: str
    frame_edges: np.ndarray
    required: np.ndarray        # exact frame amounts Q_k = int_frame q dt
    booked: np.ndarray          # what the consumer booked
    produced: float             # the producer's own (fine) account over the run
    exact: float                # exact integral over the run

    @property
    def consumed(self) -> float:
        return float(np.sum(self.booked))

    @property
    def net_defect(self) -> float:
        """Run-level imbalance between the producer's and consumer's accounts."""
        return abs(self.produced - self.consumed) / abs(self.exact)

    @property
    def frame_l1_defect(self) -> float:
        """Total variation of the per-frame booking error (the discriminating metric)."""
        denom = float(np.sum(np.abs(self.required)))
        if denom == 0.0:
            return 0.0
        return float(np.sum(np.abs(self.booked - self.required)) / denom)

    @property
    def frame_max_defect(self) -> float:
        denom = np.maximum(np.abs(self.required), 1e-300)
        return float(np.max(np.abs(self.booked - self.required) / denom))

    @property
    def consumed_error(self) -> float:
        return abs(self.consumed - self.exact) / abs(self.exact)

    @property
    def produced_error(self) -> float:
        return abs(self.produced - self.exact) / abs(self.exact)

    @property
    def n_frames(self) -> int:
        return int(len(self.booked))

    def to_dict(self) -> Dict[str, object]:
        return {
            "channel": self.channel,
            "protocol": self.protocol,
            "produced": self.produced,
            "consumed": self.consumed,
            "exact": self.exact,
            "net_defect": self.net_defect,
            "frame_l1_defect": self.frame_l1_defect,
            "frame_max_defect": self.frame_max_defect,
            "produced_error_rel": self.produced_error,
            "consumed_error_rel": self.consumed_error,
            "n_frames": self.n_frames,
        }


def _trapz(y: np.ndarray, t: np.ndarray) -> float:
    return float(np.sum(0.5 * (y[1:] + y[:-1]) * np.diff(t)))


def _lattice(dt: float, horizon: float) -> np.ndarray:
    n = int(round(horizon / dt))
    return np.arange(n + 1) * dt


def exchange_synced(
    manager: GlobalClockManager,
    grid: SyncGrid,
    channels: Sequence[Channel],
    producer: str,
    consumer: str,
) -> Dict[str, ChannelLedger]:
    """Barrier protocol: the consumer's frames are grid nodes (nested lattices).

    The producer publishes at its own step lattice; each consumer frame contains
    an integer number of those samples (this is what the global grid buys), so
    the frame total is a composite trapezoid on the *producer's* lattice and its
    error is set by the fast clock, not by the slow consumer step.
    """
    dt_p = manager.clocks[producer].dt_float
    horizon = float(grid.horizon)
    t_p = _lattice(dt_p, horizon)
    frames = _lattice(manager.clocks[consumer].dt_float, horizon)
    # contract check: every consumer frame edge must be a producer publication node
    if not grid.nesting_ok(producer, consumer):
        raise ClockContractViolation(
            f"consumer {consumer} frame edges are not on the producer {producer} "
            f"publication lattice: the global grid must nest them")
    idx_edges = np.rint(frames / dt_p).astype(int)
    ledger: Dict[str, ChannelLedger] = {}
    for channel in channels:
        rates = np.asarray(channel.rate(t_p), dtype=float)
        produced = _trapz(rates, t_p)
        booked = np.array([
            _trapz(rates[i0:i1 + 1], t_p[i0:i1 + 1])
            for i0, i1 in zip(idx_edges[:-1], idx_edges[1:])
        ])
        required = np.array([
            channel.integral(float(a), float(b)) for a, b in zip(frames[:-1], frames[1:])
        ])
        ledger[channel.name] = ChannelLedger(
            channel=channel.name,
            protocol="synced",
            frame_edges=frames,
            required=required,
            booked=booked,
            produced=produced,
            exact=channel.integral(0.0, horizon),
        )
    return ledger


def exchange_free_running(
    clocks: Dict[str, SolverClock],
    channels: Sequence[Channel],
    producer: str,
    consumer: str,
    horizon: float,
    mode: str = "hold",
    consumer_offset: float = 0.0,
) -> Dict[str, ChannelLedger]:
    """No global clock: each solver steps on its own grid, exchange is causal.

    ``mode='hold'``         zero-order hold of the last published sample
    ``mode='extrapolate'``  linear extrapolation from the two last samples
    ``consumer_offset``     phase of the consumer's frame lattice relative to the
                            producer's publications.  With no global clock there is
                            nothing that pins it to zero, so the audit sweeps it.
    """
    if mode not in ("hold", "extrapolate"):
        raise ValueError(f"unknown free-running mode {mode!r}")
    dt_p = clocks[producer].dt_float
    dt_c = clocks[consumer].dt_float
    horizon = float(horizon)
    t_p = _lattice(dt_p, horizon)
    n_c = int(round(horizon / dt_c))
    t_c = consumer_offset * dt_c + np.arange(n_c + 1) * dt_c
    ledger: Dict[str, ChannelLedger] = {}
    for channel in channels:
        q_p = np.asarray(channel.rate(t_p), dtype=float)
        in_window = (t_p >= t_c[0] - 1e-15) & (t_p <= t_c[-1] + 1e-15)
        produced = _trapz(q_p[in_window], t_p[in_window])
        booked = np.zeros(n_c)
        for k in range(n_c):
            start, end = t_c[k], t_c[k + 1]
            idx = int(np.searchsorted(t_p, start, side="right")) - 1   # causal
            if idx < 0:
                value = 0.0
            elif mode == "hold" or idx == 0:
                value = float(q_p[idx])
            else:
                slope = (q_p[idx] - q_p[idx - 1]) / (t_p[idx] - t_p[idx - 1])
                value = float(q_p[idx] + slope * 0.5 * (end - start))
            booked[k] = value * (end - start)
        required = np.array([
            channel.integral(float(a), float(b)) for a, b in zip(t_c[:-1], t_c[1:])
        ])
        ledger[channel.name] = ChannelLedger(
            channel=channel.name,
            protocol=f"free:{mode}",
            frame_edges=t_c,
            required=required,
            booked=booked,
            produced=produced,
            exact=channel.integral(float(t_c[0]), float(t_c[-1])),
        )
    return ledger


def summarise(ledger: Dict[str, ChannelLedger]) -> Dict[str, float]:
    """Worst-case aggregation over channels for gate decisions."""
    return {
        "max_net_defect": max(l.net_defect for l in ledger.values()),
        "max_frame_l1_defect": max(l.frame_l1_defect for l in ledger.values()),
        "max_frame_max_defect": max(l.frame_max_defect for l in ledger.values()),
        "max_consumed_error": max(l.consumed_error for l in ledger.values()),
        "max_produced_error": max(l.produced_error for l in ledger.values()),
        "n_frames": min(l.n_frames for l in ledger.values()),
    }


def interpolation_audit(
    channel: Channel,
    dt_source: float,
    n_query: int = 997,
    horizon: Optional[float] = None,
    modes: Sequence[str] = ("linear", "nearest", "hermite"),
    seed: int = 20260101,
) -> Dict[str, Dict[str, float]]:
    """Error at instants that are *not* on the source grid (non-synchronous exchange).

    The source publishes at ``k*dt_source``; the consumer asks at random offsets
    inside a source cell.  Error is normalised by ``max|q|`` over the horizon, so
    the number is a relative exchange error.
    """
    horizon = float(horizon if horizon is not None else 20 * dt_source)
    n_src = int(np.ceil(horizon / dt_source))
    t_src = np.arange(n_src + 1) * dt_source
    q_src = np.asarray(channel.rate(t_src), dtype=float)
    reference = float(np.max(np.abs(q_src)))
    rng = np.random.default_rng(seed)
    cells = rng.integers(0, n_src, size=n_query)
    frac = rng.uniform(0.0, 1.0, size=n_query)
    t_q = t_src[cells] + frac * dt_source
    truth = np.asarray(channel.rate(t_q), dtype=float)
    out: Dict[str, Dict[str, float]] = {}
    for mode in modes:
        if mode == "nearest":
            idx = np.rint(t_q / dt_source).astype(int).clip(0, n_src)
            approx = q_src[idx]
        elif mode == "linear":
            idx = cells
            frac_local = (t_q - t_src[idx]) / dt_source
            approx = q_src[idx] + frac_local * (q_src[np.minimum(idx + 1, n_src)] - q_src[idx])
        elif mode == "hermite":
            slopes = np.gradient(q_src, dt_source)
            idx = cells
            s = (t_q - t_src[idx]) / dt_source
            h00 = 2 * s ** 3 - 3 * s ** 2 + 1
            h10 = s ** 3 - 2 * s ** 2 + s
            h01 = -2 * s ** 3 + 3 * s ** 2
            h11 = s ** 3 - s ** 2
            j = np.minimum(idx + 1, n_src)
            approx = (h00 * q_src[idx] + h10 * dt_source * slopes[idx]
                      + h01 * q_src[j] + h11 * dt_source * slopes[j])
        else:
            raise ValueError(f"unknown interpolation mode {mode!r}")
        err = np.abs(approx - truth) / reference
        out[mode] = {
            "max_rel_error": float(np.max(err)),
            "rms_rel_error": float(np.sqrt(np.mean(err ** 2))),
            "p95_rel_error": float(np.percentile(err, 95)),
            "n_query": int(n_query),
            "dt_source": float(dt_source),
        }
    return out