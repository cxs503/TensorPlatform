"""Resource degradation ladder: memory pressure -> cheaper numerics -> graceful exit.

Rungs are *declared*, ordered, and irreversible (a job never silently climbs back
up, which would make results non-reproducible).  Each rung is a callable that the
job applies to itself and reports back; the ladder only decides *when*.

Default ladder (thresholds are cgroup/meminfo utilization ratios):

===== ==================== ====================================================
order rung                 action handed to the job
===== ==================== ====================================================
1     ``reduce_precision``  float64 -> float32 state (halves the dominant term)
2     ``reduce_grid``       halve every spatial extent (quarter the state)
3     ``reduce_timestep``   double dt / stride the iteration (fewer steps ahead)
4     ``graceful_exit``     flush a final checkpoint and exit with a known code
===== ==================== ====================================================

The ladder is a *policy* object: it never touches the job's arrays.  This keeps the
package domain-free and makes each rung unit-testable with a scripted probe.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Sequence

from . import forensics

#: Exit code used when a job stops because of the ladder (sysexits.h EX_TEMPFAIL).
GRACEFUL_EXIT_CODE = 75
#: Exit code used when the ladder cannot shed any more resources.
HARD_EXIT_CODE = 76

DEFAULT_RUNGS: Sequence[tuple[str, float]] = (
    ("reduce_precision", 0.80),
    ("reduce_grid", 0.88),
    ("reduce_timestep", 0.94),
    ("graceful_exit", 0.98),
)


@dataclass
class Rung:
    name: str
    threshold: float
    applied: bool = False
    applied_at_unix: Optional[float] = None
    utilization_at_apply: Optional[float] = None

    def as_dict(self) -> Dict[str, Any]:
        return {
            "rung": self.name,
            "threshold": self.threshold,
            "applied": self.applied,
            "applied_at_unix": self.applied_at_unix,
            "utilization_at_apply": self.utilization_at_apply,
        }


class ResourceLadder:
    """Ordered degradation policy driven by an injectable pressure probe."""

    def __init__(
        self,
        probe: Optional[Callable[[], Dict[str, Any]]] = None,
        rungs: Sequence[tuple[str, float]] = DEFAULT_RUNGS,
        min_interval_s: float = 0.5,
        hysteresis: float = 0.0,
    ) -> None:
        self.probe = probe or forensics.memory_probe
        self.rungs: List[Rung] = [Rung(name, float(thr)) for name, thr in rungs]
        self.min_interval_s = float(min_interval_s)
        self.hysteresis = float(hysteresis)
        self.history: List[Dict[str, Any]] = []
        self.samples: List[Dict[str, Any]] = []
        self._last_poll = 0.0
        self.exhausted = False

    # -- policy ------------------------------------------------------------ #
    def probe_now(self) -> Dict[str, Any]:
        sample = self.probe()
        sample.setdefault("timestamp_unix", time.time())
        self.samples.append(sample)
        return sample

    def next_rung(self, utilization: Optional[float]) -> Optional[Rung]:
        if utilization is None:
            return None
        for rung in self.rungs:
            if not rung.applied and utilization >= (rung.threshold - self.hysteresis):
                return rung
        return None

    def poll(self, force: bool = False) -> Optional[Dict[str, Any]]:
        """Sample pressure; return the rung that just triggered (or ``None``).

        Throttled to ``min_interval_s`` so that a fast inner loop does not turn
        ``/proc`` reads into measurable overhead (see the ≤2 % negative control).
        If pressure jumped over several thresholds at once, *all* skipped rungs are
        applied in order so the state stays consistent with the ladder contract.
        """
        now = time.monotonic()
        if not force and (now - self._last_poll) < self.min_interval_s:
            return None
        self._last_poll = now
        sample = self.probe_now()
        utilization = sample.get("utilization")
        triggered: List[Rung] = []
        rung = self.next_rung(utilization)
        while rung is not None:
            rung.applied = True
            rung.applied_at_unix = time.time()
            rung.utilization_at_apply = utilization
            triggered.append(rung)
            record = {"rung": rung.name, "threshold": rung.threshold, "utilization": utilization,
                      "source": sample.get("source"), "timestamp_unix": rung.applied_at_unix}
            self.history.append(record)
            if rung.name == "graceful_exit":
                self.exhausted = True
                break
            rung = self.next_rung(utilization)
        if not triggered:
            return None
        last = triggered[-1]
        return {
            "rung": last.name,
            "kind": last.name,
            "rungs_applied": [r.name for r in triggered],
            "utilization": utilization,
            "threshold": last.threshold,
            "source": sample.get("source"),
        }

    # -- introspection ----------------------------------------------------- #
    def applied(self) -> List[str]:
        return [r.name for r in self.rungs if r.applied]

    def report(self) -> Dict[str, Any]:
        return {
            "rungs": [r.as_dict() for r in self.rungs],
            "applied": self.applied(),
            "history": self.history,
            "samples": len(self.samples),
            "exhausted": self.exhausted,
            "min_interval_s": self.min_interval_s,
        }


class ScriptedProbe:
    """Deterministic pressure probe for tests (returns a scripted utilization ramp)."""

    def __init__(self, values: Sequence[float], limit_bytes: int = 1 << 30) -> None:
        self.values = [float(v) for v in values]
        self.i = 0
        self.limit_bytes = limit_bytes

    def __call__(self) -> Dict[str, Any]:
        value = self.values[min(self.i, len(self.values) - 1)]
        self.i += 1
        return {
            "source": "scripted",
            "used_bytes": int(value * self.limit_bytes),
            "limit_bytes": self.limit_bytes,
            "utilization": value,
            "oom_kill_total": 0,
            "timestamp_unix": time.time(),
        }