"""Synthetic kernel families for the scalability ledger.

Two genuinely different bottleneck families plus two controls:

``stencil5p``   compute + memory-bandwidth bound 5-point Jacobi stencil on an
                int64 grid.  Shared-memory domain decomposition (row blocks); the
                ghost rows of the padded array *are* the neighbours' data rows, so
                a per-iteration barrier is the only communication.  Integer
                arithmetic keeps the fingerprint bitwise partition-invariant.
``halo_ring``   communication bound ring exchange over ``multiprocessing.Pipe``.
                Each iteration ships one 30 KB slab to the next rank and receives
                one from the previous rank.  Payload < the 64 KB pipe buffer, which
                is what makes the ring deadlock-free (no blocking send before the
                matching receive).
``serial_frac`` injected-serial-fraction family: a perfectly parallel phase plus a
                serial phase executed by rank 0 alone while the others wait at a
                barrier.  The serial fraction ``s`` is *calibrated on the measured
                machine* (not assumed), so the Amdahl fit can be scored against a
                known truth.
``pseudo_par``  negative control: accepts ``n_cores``, forks ``n_cores`` workers,
                but every unit of work is done by rank 0; the others only hit the
                barrier.  A ledger that cannot flag this as ``s -> 1`` is broken.

All kernels are domain-neutral by construction: no physics, just memory/CPU/IPC.
"""
from __future__ import annotations

import multiprocessing as mp
import time
from typing import Dict, List, Optional, Tuple

import numpy as np

from tools.scaling.engine import WorkerPool
from tools.scaling.protocol import SharedArray, combine_mod2_64, split_ranges

__all__ = [
    "StencilKernel",
    "HaloRingKernel",
    "SerialFractionKernel",
    "PseudoParallelKernel",
    "KERNELS",
]

# Size of the L2-resident working buffer used by the CPU-bound families.
_BLOCK_ELEMS = 32768

# Phase selector for SerialFractionKernel (shared-memory int codes).
_PHASES = {"both": 0, "par": 1, "ser": 2}
_PHASE_NAMES = {v: k for k, v in _PHASES.items()}


def _stencil_init(shape: Tuple[int, int]) -> np.ndarray:
    ny, nx = shape
    ii = np.arange(ny, dtype=np.int64)[:, None]
    jj = np.arange(nx, dtype=np.int64)[None, :]
    return (ii * 31 + jj * 17 + 1) % 1024


class _ParallelKernel:
    """Common lifecycle: one persistent pool per ``n_cores``."""

    name = "kernel"
    mode = "strong"  # "strong" | "weak"

    def __init__(self, *, timeout: float = 1800.0):
        self._pool: Optional[WorkerPool] = None
        self._n = 0
        self._timeout = timeout
        self._iter_barrier = None
        self.last_elapsed_s = 0.0

    # -- to override -----------------------------------------------------
    def _allocate(self, n_cores: int) -> None:  # pragma: no cover - abstract
        raise NotImplementedError

    def _body(self, rank: int, n_cores: int) -> Dict[str, object]:  # pragma: no cover
        raise NotImplementedError

    def _collect_checksum(self) -> int:  # pragma: no cover - abstract
        raise NotImplementedError

    def _release(self) -> None:
        pass

    # -- protocol --------------------------------------------------------
    def setup(self, n_cores: int) -> None:
        self.teardown()
        self._n = int(n_cores)
        self._allocate(self._n)
        self._iter_barrier = mp.get_context("fork").Barrier(self._n)
        self._pool = WorkerPool(self._n, self._body, timeout=self._timeout)

    def run(self, n_cores: int) -> float:
        if self._pool is None or n_cores != self._n:
            self.setup(n_cores)
        result = self._pool.run()
        self.last_elapsed_s = float(result.elapsed_s)
        return self.last_elapsed_s

    def checksum(self) -> int:
        return self._collect_checksum()

    def teardown(self) -> None:
        if self._pool is not None:
            self._pool.close()
            self._pool = None
        self._release()
        self._iter_barrier = None

    # -- helpers ---------------------------------------------------------
    def _set_phase(self, phase: str) -> None:
        self._phase = phase


class StencilKernel(_ParallelKernel):
    """5-point Jacobi stencil, int64, row-block decomposition."""

    name = "stencil5p"
    mode = "strong"

    def __init__(
        self,
        *,
        nx: int = 4096,
        ny: int = 4096,
        n_iter: int = 40,
        rows_per_rank: int = 512,
        mode: str = "strong",
        timeout: float = 1800.0,
    ):
        super().__init__(timeout=timeout)
        self.nx = int(nx)
        self.ny_config = int(ny)
        self.n_iter = int(n_iter)
        self.rows_per_rank = int(rows_per_rank)
        self.mode = mode
        self._a = self._b = None
        self._ranges: List[Tuple[int, int]] = []
        self._ny = 0
        self._tmp: Optional[np.ndarray] = None
        self._local_checksum = 0

    def _allocate(self, n_cores: int) -> None:
        self._ny = self.rows_per_rank * n_cores if self.mode == "weak" else self.ny_config
        self._ny = max(self._ny, n_cores)
        shape = (self._ny + 2, self.nx)
        init = _stencil_init(shape)
        self._a = SharedArray(shape, dtype=np.int64, fill=init)
        self._b = SharedArray(shape, dtype=np.int64, fill=init)
        self._ranges = split_ranges(self._ny, n_cores)
        self._tmp = None
        self._local_checksum = 0

    def _release(self) -> None:
        for arr in (self._a, self._b):
            if arr is not None:
                arr.close_unlink()
        self._a = self._b = None
        self._tmp = None

    def _body(self, rank: int, n_cores: int) -> Dict[str, object]:
        a, b = self._a.arr, self._b.arr
        r0, r1 = self._ranges[rank]
        rows = r1 - r0
        if self._tmp is None or self._tmp.shape[0] < max(rows, 1):
            self._tmp = np.empty((max(rows, 1), self.nx - 2), dtype=np.int64)
        tmp = self._tmp
        barrier = self._iter_barrier
        for _ in range(self.n_iter):
            if rows:
                out = tmp[:rows]
                np.add(a[r0:r1, 1:-1], a[r0 + 2 : r1 + 2, 1:-1], out=out)
                np.add(out, a[r0 + 1 : r1 + 1, 0:-2], out=out)
                np.add(out, a[r0 + 1 : r1 + 1, 2:], out=out)
                np.add(out, a[r0 + 1 : r1 + 1, 1:-1] * 3, out=out)
                b[r0 + 1 : r1 + 1, 1:-1] = out
            barrier.wait()  # ghost rows are the neighbours' data rows
            a, b = b, a  # double buffering
        cur = self._a.arr if self.n_iter % 2 == 0 else self._b.arr
        local = int(np.sum(cur[r0 + 1 : r1 + 1, :], dtype=np.int64)) if rows else 0
        self._local_checksum = local
        return {"rank": rank, "rows": rows, "checksum": local}

    def _collect_checksum(self) -> int:
        # Recompute in the coordinator (children sums are not visible to it):
        # the coordinator sees the same shared segment, so reading is enough.
        cur = self._a.arr if self.n_iter % 2 == 0 else self._b.arr
        parts = [
            int(np.sum(cur[r0 + 1 : r1 + 1, :], dtype=np.int64)) for r0, r1 in self._ranges
        ]
        return combine_mod2_64(parts)

    def describe(self) -> Dict[str, object]:
        return {
            "kernel": self.name,
            "mode": self.mode,
            "nx": self.nx,
            "ny": self._ny,
            "n_iter": self.n_iter,
            "grid_cells": self._ny * self.nx,
            "bytes_per_array": int(self.nx * (self._ny + 2) * 8),
            "decomposition": "contiguous row blocks; ghost rows shared; barrier per iteration",
        }


class HaloRingKernel(_ParallelKernel):
    """Communication-bound ring halo exchange over pipes (30 KB slabs)."""

    name = "halo_ring"
    mode = "strong"

    def __init__(
        self,
        *,
        total_elems: int = 4096,
        per_rank_elems: int = 4096,
        n_iter: int = 2000,
        mode: str = "strong",
        timeout: float = 1800.0,
    ):
        super().__init__(timeout=timeout)
        self.total_elems = int(total_elems)
        self.per_rank_elems = int(per_rank_elems)
        self.n_iter = int(n_iter)
        self.mode = mode
        self._pipes: List[Tuple[object, object]] = []
        self._payload: Optional[np.ndarray] = None
        self._ranges: List[Tuple[int, int]] = []

    def _allocate(self, n_cores: int) -> None:
        total = self.per_rank_elems * n_cores if self.mode == "weak" else self.total_elems
        self._ranges = split_ranges(total, n_cores)
        ctx = mp.get_context("fork")
        self._pipes = [ctx.Pipe(duplex=False) for _ in range(n_cores)]
        self._payload = None

    def _release(self) -> None:
        self._pipes = []
        self._payload = None

    def _body(self, rank: int, n_cores: int) -> Dict[str, object]:
        lo, hi = self._ranges[rank]
        n = hi - lo
        if self._payload is None or self._payload.size != max(n, 1):
            idx = np.arange(lo, hi, dtype=np.int64)
            self._payload = (idx * 7 + 3) % 1009
        send = self._pipes[rank][1]
        recv = self._pipes[(rank - 1) % n_cores][0]
        payload = self._payload
        bufout = payload.tobytes() if n else b""
        barrier = self._iter_barrier
        acc = 0
        for _ in range(self.n_iter):
            if n:
                send.send_bytes(bufout)
                acc += int(np.frombuffer(recv.recv_bytes(), dtype=np.int64).sum(dtype=np.int64))
            barrier.wait()
        return {"rank": rank, "elems": n, "acc": acc % (1 << 64)}

    def _collect_checksum(self) -> int:
        # Partition-invariant by construction: every global element is received
        # exactly once per iteration, so the total is independent of n_cores.
        total = 0
        for lo, hi in self._ranges:
            if hi > lo:
                idx = np.arange(lo, hi, dtype=np.int64)
                total += int(((idx * 7 + 3) % 1009).sum(dtype=np.int64))
        return (total * self.n_iter) % (1 << 64)

    def describe(self) -> Dict[str, object]:
        total = self.per_rank_elems * max(self._n, 1) if self.mode == "weak" else self.total_elems
        return {
            "kernel": self.name,
            "mode": self.mode,
            "n_iter": self.n_iter,
            "total_elems": total,
            "bytes_per_message": int(total / max(self._n, 1)) * 8,
            "note": "payload <= 64 KB pipe buffer -> ring is deadlock-free",
        }


def _spin_blocks(buf: np.ndarray, n_blocks: int) -> int:
    """CPU-bound (L2-resident) integer work: ``n_blocks`` sweeps of buf*3+7."""
    acc = 0
    for _ in range(n_blocks):
        np.add(buf * 3, 7, out=buf)
        acc ^= int(buf[0])
    return acc


def _time_blocks(n_blocks: int, probe_blocks: int = 200) -> float:
    """Seconds for ``n_blocks`` sweeps, measured on a private L2-resident buffer."""
    buf = np.arange(_BLOCK_ELEMS, dtype=np.int64) % 1009
    _spin_blocks(buf, probe_blocks)
    t0 = time.perf_counter()
    _spin_blocks(buf, n_blocks)
    return time.perf_counter() - t0


class SerialFractionKernel(_ParallelKernel):
    """Perfectly parallel phase + rank-0-only serial phase (injected ``s``)."""

    name = "serial_frac"
    mode = "strong"

    def __init__(
        self,
        *,
        target_s: float = 0.05,
        target_total_s: float = 1.2,
        parallel_blocks: int = 2000,
        serial_blocks: int = 100,
        total_elems: int = 1 << 20,
        mode: str = "strong",
        timeout: float = 1800.0,
    ):
        super().__init__(timeout=timeout)
        self.target_s = float(target_s)
        self.target_total_s = float(target_total_s)
        self.parallel_blocks = int(parallel_blocks)
        self.serial_blocks = int(serial_blocks)
        self.total_elems = int(total_elems)
        self.mode = mode
        self._phase = "both"
        self._phase_shared = None
        self._vec: Optional[SharedArray] = None
        self._ranges: List[Tuple[int, int]] = []
        self._buf: Optional[np.ndarray] = None

    def _allocate(self, n_cores: int) -> None:
        self._vec = SharedArray((self.total_elems,), dtype=np.int64, fill=1)
        self._ranges = split_ranges(self.total_elems, n_cores)
        # A forked child gets a *copy* of self, so the phase selector has to live
        # in shared memory to be visible to the workers.
        self._phase_shared = mp.get_context("fork").Value("i", _PHASES["both"])
        self._buf = None

    def _release(self) -> None:
        if self._vec is not None:
            self._vec.close_unlink()
        self._vec = None
        self._buf = None

    def _body(self, rank: int, n_cores: int) -> Dict[str, object]:
        if self._buf is None:
            self._buf = np.arange(_BLOCK_ELEMS, dtype=np.int64) % 1009
        buf = self._buf
        barrier = self._iter_barrier
        phase = _PHASE_NAMES[self._phase_shared.value]
        if phase in ("both", "par"):
            mine = self.parallel_blocks // n_cores + (1 if rank < self.parallel_blocks % n_cores else 0)
            _spin_blocks(buf, mine)
            barrier.wait()
        if phase in ("both", "ser"):
            if rank == 0:
                _spin_blocks(buf, self.serial_blocks)
            barrier.wait()
        lo, hi = self._ranges[rank]
        if hi > lo:
            self._vec.arr[lo:hi] = self._vec.arr[lo:hi] * 3 + 7
        return {"rank": rank, "blocks_par": self.parallel_blocks}

    def _collect_checksum(self) -> int:
        return int(np.sum(self._vec.arr, dtype=np.int64)) % (1 << 64)

    def _set_phase(self, phase: str) -> None:
        self._phase = phase
        if self._phase_shared is not None:
            self._phase_shared.value = _PHASES[phase]

    def calibrate(self) -> Dict[str, float]:
        """Size both phases from measured machine speed, then verify the real s.

        The serial fraction is not assumed: a feedback loop compares the measured
        phase ratio with the target and rescales the serial block count until they
        agree.  A zero-block probe measures the fixed per-run cost (barrier +
        worker wake-up), which would otherwise masquerade as serial work.

        All corrections are rate-based and clamped, so a noisy machine (or a
        co-tenant spike) cannot drive the iteration counts to absurd values --
        a failure mode that would otherwise hang the measurement instead of
        reporting a bad number.
        """
        per_block = _time_blocks(50, probe_blocks=50) / 50.0
        s = self.target_s
        total = self.target_total_s
        init_par = max(1, int(round(total * (1.0 - s) / per_block)))
        init_ser = max(1, int(round(total * s / per_block)))
        self.parallel_blocks, self.serial_blocks = init_par, init_ser
        cap_par, cap_ser = 50 * init_par, 50 * init_ser

        saved = self.serial_blocks
        self.setup(1)
        self._set_phase("ser")
        self.serial_blocks = 0
        eps = float(np.median([self._pool.run().elapsed_s for _ in range(5)]))
        self.serial_blocks = saved
        self.teardown()

        history = []
        for _ in range(4):
            par, ser = self.measure_phases(reps=5)
            history.append(
                {
                    "t_parallel_s": par,
                    "t_serial_s": ser,
                    "s": ser / max(par + ser, 1e-12),
                    "parallel_blocks": self.parallel_blocks,
                    "serial_blocks": self.serial_blocks,
                }
            )
            # Overhead-free times, floored at 20 % so timing noise cannot invert
            # the correction; both counts are clamped to 50x their first guess.
            par_eff = max(par - eps, 0.2 * par)
            ser_eff = max(ser - eps, 0.2 * ser)
            wanted_ser = par_eff * s / (1.0 - s)
            new_ser = int(round(self.serial_blocks * wanted_ser / max(ser_eff, 1e-12)))
            new_par = int(round(self.parallel_blocks * total / max(par + ser, 1e-12)))
            new_par = min(max(new_par, 1), cap_par)
            new_ser = min(max(new_ser, 1), cap_ser)
            converged = abs(new_par - self.parallel_blocks) <= max(
                1, int(0.03 * self.parallel_blocks)
            ) and abs(new_ser - self.serial_blocks) <= max(1, int(0.03 * self.serial_blocks))
            self.parallel_blocks, self.serial_blocks = new_par, new_ser
            if converged:
                break

        par, ser = self.measure_phases(reps=5)
        return {
            "target_s": s,
            "per_block_s": per_block,
            "fixed_overhead_s": eps,
            "parallel_blocks": self.parallel_blocks,
            "serial_blocks": self.serial_blocks,
            "t_parallel_s": par,
            "t_serial_s": ser,
            "s_measured": ser / max(par + ser, 1e-12),
            "convergence": history,
        }

    def measure_phases(self, reps: int = 5) -> Tuple[float, float]:
        """Median seconds for the parallel-only and serial-only phases at n=1."""
        self.setup(1)
        par, ser = [], []
        for _ in range(reps):
            self._set_phase("par")
            par.append(self._pool.run().elapsed_s)
            self._set_phase("ser")
            ser.append(self._pool.run().elapsed_s)
        self._set_phase("both")
        self.teardown()
        return float(np.median(par)), float(np.median(ser))

    def describe(self) -> Dict[str, object]:
        return {
            "kernel": self.name,
            "mode": self.mode,
            "target_s": self.target_s,
            "parallel_blocks": self.parallel_blocks,
            "serial_blocks": self.serial_blocks,
        }


class PseudoParallelKernel(_ParallelKernel):
    """Negative control: all work on rank 0, other ranks idle at the barrier."""

    name = "pseudo_par"
    mode = "strong"

    def __init__(
        self,
        *,
        total_blocks: int = 2000,
        total_elems: int = 1 << 20,
        mode: str = "strong",
        timeout: float = 1800.0,
    ):
        super().__init__(timeout=timeout)
        self.total_blocks = int(total_blocks)
        self.total_elems = int(total_elems)
        self.mode = mode
        self._vec: Optional[SharedArray] = None
        self._buf: Optional[np.ndarray] = None

    def _allocate(self, n_cores: int) -> None:
        self._vec = SharedArray((self.total_elems,), dtype=np.int64, fill=1)
        self._buf = None

    def _release(self) -> None:
        if self._vec is not None:
            self._vec.close_unlink()
        self._vec = None
        self._buf = None

    def _body(self, rank: int, n_cores: int) -> Dict[str, object]:
        if self._buf is None:
            self._buf = np.arange(_BLOCK_ELEMS, dtype=np.int64) % 1009
        if rank == 0:
            _spin_blocks(self._buf, self.total_blocks)
            self._vec.arr[...] = self._vec.arr * 3 + 7
        self._iter_barrier.wait()
        return {"rank": rank, "did_work": rank == 0}

    def _collect_checksum(self) -> int:
        return int(np.sum(self._vec.arr, dtype=np.int64)) % (1 << 64)

    def calibrate(self) -> Dict[str, float]:
        per_block = _time_blocks(50, probe_blocks=50) / 50.0
        self.total_blocks = max(1, int(round(1.0 / per_block)))
        return {"per_block_s": per_block, "total_blocks": self.total_blocks}

    def describe(self) -> Dict[str, object]:
        return {
            "kernel": self.name,
            "mode": self.mode,
            "total_blocks": self.total_blocks,
            "note": "declares n_cores but serialises all work on rank 0",
        }


KERNELS: Dict[str, type] = {
    "stencil5p": StencilKernel,
    "halo_ring": HaloRingKernel,
    "serial_frac": SerialFractionKernel,
    "pseudo_par": PseudoParallelKernel,
}