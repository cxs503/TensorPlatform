"""Parallel scalability ledger harness (domain-neutral).

Public API
----------
Kernel protocol (:mod:`tools.scaling.protocol`)
    ``setup(n_cores) -> None`` / ``run(n_cores) -> None`` / ``checksum() -> int``.
Engine (:mod:`tools.scaling.engine`)
    Persistent fork pool with barrier-bounded timing regions.
Kernels (:mod:`tools.scaling.kernels`)
    Synthetic compute/bandwidth-bound stencil, communication-bound halo exchange,
    injected-serial-fraction families and a pseudo-parallel negative control.
Runner (:mod:`tools.scaling.runner`)
    Measurement protocol (reps, randomized core order, bitwise checksum) plus the
    C1..C6 acceptance verdicts and a core-quota recommendation.
Fitting (:mod:`tools.scaling.fitting`)
    Amdahl ``A(n)=1/(s+(1-s)/n)`` and Gustafson fits with bootstrap CIs.
"""

from tools.scaling.protocol import Kernel, split_ranges, SharedArray  # noqa: F401
from tools.scaling.engine import WorkerPool, ParallelResult  # noqa: F401

__all__ = [
    "Kernel",
    "split_ranges",
    "SharedArray",
    "WorkerPool",
    "ParallelResult",
]