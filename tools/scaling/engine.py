"""Persistent fork pool with barrier-bounded timing regions.

Why a persistent pool and not ``multiprocessing.Pool.map``: strong-scaling
efficiency is defined on the *steady-state* parallel region.  Fork/exit costs and
import time must be excluded, otherwise a 16-core point would be dominated by
process start-up.  Workers are forked once per ``n_cores`` in ``setup()``, then
each ``run()`` starts with a barrier release and ends when every rank reaches the
closing barrier, so ``elapsed`` measures exactly the parallel region plus rank
arrival skew.

Deadlock safety: every ``Barrier.wait`` carries a timeout; a rank that raises
still reaches the closing barrier and reports the exception through the queue, so
the coordinator never blocks forever.
"""
from __future__ import annotations

import multiprocessing as mp
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List

__all__ = ["WorkerPool", "ParallelResult", "WorkerError"]

Body = Callable[[int, int], Dict[str, Any]]


class WorkerError(RuntimeError):
    """Raised in the coordinator when a worker (or the pool) failed."""


@dataclass
class ParallelResult:
    elapsed_s: float
    rank_times: List[float] = field(default_factory=list)
    payloads: List[Dict[str, Any]] = field(default_factory=list)

    @property
    def slowest_rank_s(self) -> float:
        return max(self.rank_times) if self.rank_times else self.elapsed_s


def _worker_main(rank, n_cores, body, start, end, stop, queue, timeout):
    try:
        while True:
            try:
                start.wait(timeout=timeout)
            except Exception:
                return
            if stop.is_set():
                try:
                    end.wait(timeout=timeout)
                except Exception:
                    pass
                return
            t0 = time.perf_counter()
            err = None
            payload: Dict[str, Any] = {}
            try:
                payload = body(rank, n_cores) or {}
            except BaseException as exc:  # noqa: BLE001 - reported to coordinator
                err = f"{type(exc).__name__}: {exc}"
            t1 = time.perf_counter()
            try:
                queue.put((rank, t1 - t0, payload, err))
            except Exception:
                pass
            try:
                end.wait(timeout=timeout)
            except Exception:
                return
    finally:
        return


class WorkerPool:
    """Fork ``n_cores`` workers that execute ``body(rank, n_cores)`` on demand."""

    def __init__(self, n_cores: int, body: Body, *, timeout: float = 900.0):
        if n_cores < 1:
            raise ValueError("n_cores must be >= 1")
        self.n_cores = int(n_cores)
        self.body = body
        self.timeout = float(timeout)
        ctx = mp.get_context("fork")
        self._ctx = ctx
        # Parties = workers + coordinator, so the coordinator itself releases the
        # start barrier and observes the end barrier (that IS the timed region).
        self._start = ctx.Barrier(self.n_cores + 1)
        self._end = ctx.Barrier(self.n_cores + 1)
        self._stop = ctx.Event()
        self._queue = ctx.Queue()
        self._procs: List[Any] = []
        self._closed = False
        for rank in range(self.n_cores):
            p = ctx.Process(
                target=_worker_main,
                args=(
                    rank,
                    self.n_cores,
                    body,
                    self._start,
                    self._end,
                    self._stop,
                    self._queue,
                    self.timeout,
                ),
                daemon=True,
            )
            p.start()
            self._procs.append(p)

    # -- measurement -----------------------------------------------------
    def run(self) -> ParallelResult:
        if self._closed:
            raise WorkerError("pool is closed")
        try:
            self._start.wait(timeout=self.timeout)
        except Exception as exc:  # pragma: no cover - hang protection
            self._abort()
            raise WorkerError(f"start barrier timeout: {exc}") from exc
        t0 = time.perf_counter()
        try:
            self._end.wait(timeout=self.timeout)
        except Exception as exc:  # pragma: no cover - hang protection
            self._abort()
            raise WorkerError(f"end barrier timeout: {exc}") from exc
        elapsed = time.perf_counter() - t0
        results = []
        for _ in range(self.n_cores):
            results.append(self._queue.get(timeout=self.timeout))
        results.sort(key=lambda r: r[0])
        errors = [r[3] for r in results if r[3]]
        if errors:
            raise WorkerError("worker failure: " + "; ".join(sorted(set(errors))))
        return ParallelResult(
            elapsed_s=elapsed,
            rank_times=[r[1] for r in results],
            payloads=[r[2] for r in results],
        )

    # -- lifecycle -------------------------------------------------------
    def _abort(self) -> None:
        self._stop.set()
        for p in self._procs:
            if p.is_alive():
                p.terminate()
        for p in self._procs:
            p.join(timeout=5.0)
        self._closed = True

    def close(self) -> None:
        if self._closed:
            return
        self._stop.set()
        try:
            self._start.wait(timeout=min(self.timeout, 30.0))
        except Exception:  # pragma: no cover
            self._abort()
            return
        try:
            self._end.wait(timeout=min(self.timeout, 30.0))
        except Exception:  # pragma: no cover
            pass
        for p in self._procs:
            p.join(timeout=10.0)
        self._closed = True

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        self.close()
        return False