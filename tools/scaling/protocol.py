"""Kernel protocol, deterministic partitioning and shared-memory helpers.

The kernel contract is deliberately tiny so that any workload (solver kernel,
data pipeline, synthetic micro-benchmark) can be measured by the same harness:

    setup(n_cores)   allocate/prepare state for n_cores ranks (may fork workers)
    run(n_cores)     execute exactly one timed unit of work
    checksum()       return a deterministic integer fingerprint of the result

``checksum`` must be *partition invariant*: identical bit pattern for any
``n_cores``.  Callers that need cross-partition equality should accumulate in
integer arithmetic (exact, associative) or reduce modulo 2**64 (the int64
wraparound ring), never in floating point.
"""
from __future__ import annotations

from typing import Any, Iterable, List, Protocol, Tuple, runtime_checkable

import numpy as np

__all__ = ["Kernel", "split_ranges", "SharedArray", "combine_mod2_64"]


@runtime_checkable
class Kernel(Protocol):
    """Minimal injectable workload interface."""

    name: str
    mode: str  # "strong" | "weak"

    def setup(self, n_cores: int) -> None:  # pragma: no cover - protocol
        ...

    def run(self, n_cores: int) -> None:  # pragma: no cover - protocol
        ...

    def checksum(self) -> int:  # pragma: no cover - protocol
        ...

    def teardown(self) -> None:  # pragma: no cover - protocol
        ...


def split_ranges(total: int, n_parts: int) -> List[Tuple[int, int]]:
    """Split ``range(total)`` into ``n_parts`` contiguous, balanced half-open ranges.

    Deterministic and independent of scheduling: the first ``total % n_parts``
    parts get one extra element.  Used for row/column decomposition so that the
    same ``n_parts`` always yields the same partition (reproducibility).
    """
    if n_parts <= 0:
        raise ValueError("n_parts must be positive")
    if total < 0:
        raise ValueError("total must be non-negative")
    base, rem = divmod(int(total), int(n_parts))
    out: List[Tuple[int, int]] = []
    start = 0
    for i in range(n_parts):
        size = base + (1 if i < rem else 0)
        out.append((start, start + size))
        start += size
    return out


def combine_mod2_64(parts: Iterable[int]) -> int:
    """Combine integer partial sums exactly modulo 2**64.

    Integer addition modulo 2**64 is associative, so the reduction is invariant
    under any regrouping of the partition -> bitwise identical checksums for any
    core count, which is what acceptance criterion C1 requires.
    """
    total = 0
    for p in parts:
        total += int(p)
    return total % (1 << 64)


class SharedArray:
    """A named shared-memory ndarray, inherited by forked children.

    Children access ``.arr`` directly (no re-attach needed under ``fork``); the
    creating process owns the segment and must call :meth:`close_unlink`.
    """

    def __init__(self, shape, dtype=np.int64, fill: Any = None):
        from multiprocessing import shared_memory

        self.shape = tuple(int(s) for s in shape)
        self.dtype = np.dtype(dtype)
        nbytes = max(1, int(np.prod(self.shape)) * self.dtype.itemsize)
        self._shm = shared_memory.SharedMemory(create=True, size=nbytes)
        self.arr = np.ndarray(self.shape, dtype=self.dtype, buffer=self._shm.buf)
        if fill is None:
            self.arr.fill(0)
        else:
            self.arr[...] = fill

    @property
    def nbytes(self) -> int:
        return int(self.arr.nbytes)

    def close_unlink(self) -> None:
        # Drop every local view before unlinking so that no mapping survives in
        # the creating process.  Children keep their inherited mapping until they
        # exit (documented behaviour of multiprocessing.shared_memory).
        self.arr = np.empty((0,), dtype=self.dtype)
        try:
            self._shm.unlink()
        finally:
            self._shm.close()

    def __del__(self):  # pragma: no cover - best effort cleanup
        try:
            self._shm.close()
        except Exception:
            pass