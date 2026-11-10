"""A deliberately domain-free long-running job used to exercise the survival layer.

The job is a linear iterative relaxation on a periodic 2-D grid::

    u <- u + dt * ( laplacian(u) + c )

with ``u`` the state and ``c`` a fixed coefficient field drawn from a seeded PRNG.
It is chosen because it has the three properties the survival layer must survive:

* it is **bit-reproducible** (pure IEEE-754 element-wise numpy ops, no reductions),
  so "resume == uninterrupted run" can be asserted at bit level;
* its **cost is tunable** (grid size, precision) so the degradation ladder has
  something real to trade away;
* it has **no domain vocabulary** (TensorPlatform rule: zero domain semantics).

Crash injection modes exist only to produce honest autopsy evidence; they are
never enabled by default.
"""

from __future__ import annotations

import ctypes
import hashlib
import json
import os
import signal
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

import numpy as np

from . import checkpoint as ckpt_mod

CRASH_MODES = ("segv", "abort", "exception", "memory_error", "sigkill")


@dataclass
class SyntheticIterativeJob:
    grid: int = 64
    precision: str = "float64"
    dt: float = 0.25
    seed: int = 7
    crash_at: Optional[int] = None
    crash_mode: str = "segv"
    step_index: int = 0
    degradations: List[Dict[str, Any]] = field(default_factory=list)

    def __post_init__(self) -> None:
        if self.crash_mode not in CRASH_MODES:
            raise ValueError(f"unknown crash mode {self.crash_mode!r}")
        self._build(self.grid, self.precision)

    # -- construction ------------------------------------------------------ #
    def _build(self, grid: int, precision: str) -> None:
        rng = np.random.default_rng(self.seed)
        self.u = rng.standard_normal((grid, grid)).astype(precision)
        self.c = (rng.standard_normal((grid, grid)) * 0.1).astype(precision)
        self.grid = int(grid)
        self.precision = precision

    # -- job protocol ------------------------------------------------------ #
    def params(self) -> Dict[str, Any]:
        return {"grid": self.grid, "precision": self.precision, "dt": self.dt, "seed": self.seed}

    def input_digest(self) -> str:
        rng = np.random.default_rng(self.seed)
        u = rng.standard_normal((self.grid, self.grid)).astype(self.precision)
        c = (rng.standard_normal((self.grid, self.grid)) * 0.1).astype(self.precision)
        return ckpt_mod.state_digest({"u0": u, "c": c})

    def step(self, i: int) -> None:
        u = self.u
        lap = (
            np.roll(u, 1, axis=0)
            + np.roll(u, -1, axis=0)
            + np.roll(u, 1, axis=1)
            + np.roll(u, -1, axis=1)
            - 4.0 * u
        )
        self.u = (u + self.dt * (lap + self.c)).astype(self.precision, copy=False)
        self.step_index = int(i)

    # -- crash injection (evidence generation only) ------------------------ #
    def maybe_crash(self, i: int) -> None:
        if self.crash_at is None or int(i) != int(self.crash_at):
            return
        mode = self.crash_mode
        if mode == "segv":
            ctypes.string_at(0)  # real SIGSEGV inside a C frame
        elif mode == "abort":
            os.abort()  # real SIGABRT
        elif mode == "sigkill":
            os.kill(os.getpid(), signal.SIGKILL)
        elif mode == "exception":
            raise RuntimeError("injected python-level failure (evidence generation)")
        elif mode == "memory_error":
            raise MemoryError("injected allocation refusal (evidence generation)")
        raise AssertionError(f"unreachable crash mode {mode}")

    # -- state ------------------------------------------------------------- #
    def state_arrays(self) -> Dict[str, np.ndarray]:
        return {"u": self.u, "c": self.c}

    def state_scalars(self) -> Dict[str, Any]:
        return {
            "grid": self.grid,
            "precision": self.precision,
            "dt": float(self.dt),
            "seed": int(self.seed),
            "step_index": int(self.step_index),
        }

    def load_state(self, arrays: Dict[str, np.ndarray], scalars: Dict[str, Any]) -> None:
        for name, arr in arrays.items():
            setattr(self, name, np.array(arr, copy=True))
        self.grid = int(scalars.get("grid", self.grid))
        self.precision = str(scalars.get("precision", self.precision))
        self.dt = float(scalars.get("dt", self.dt))
        self.step_index = int(scalars.get("step_index", self.step_index))
        if "u" not in arrays or "c" not in arrays:
            raise ckpt_mod.CheckpointCorruption(f"state arrays missing: {sorted(arrays)}")

    def result_digest(self) -> str:
        return ckpt_mod.state_digest(self.state_arrays(), self.state_scalars())

    # -- degradation ladder rungs ------------------------------------------ #
    def apply_degradation(self, kind: str) -> Dict[str, Any]:
        before = {"grid": self.grid, "precision": self.precision, "dt": self.dt,
                  "nbytes": int(self.u.nbytes + self.c.nbytes)}
        if kind == "reduce_precision":
            if self.precision != "float32":
                self.u = self.u.astype("float32")
                self.c = self.c.astype("float32")
                self.precision = "float32"
        elif kind == "reduce_grid":
            if self.grid > 8:
                self.u = np.ascontiguousarray(self.u[::2, ::2])
                self.c = np.ascontiguousarray(self.c[::2, ::2])
                self.grid = int(self.u.shape[0])
        elif kind == "reduce_timestep":
            self.dt = float(self.dt) * 2.0
        elif kind == "graceful_exit":
            pass  # policy only; the runner flushes a checkpoint and exits
        else:  # pragma: no cover - policy guard
            raise ValueError(f"unknown degradation {kind!r}")
        after = {"grid": self.grid, "precision": self.precision, "dt": self.dt,
                 "nbytes": int(self.u.nbytes + self.c.nbytes)}
        record = {"rung": kind, "before": before, "after": after}
        self.degradations.append(record)
        return record