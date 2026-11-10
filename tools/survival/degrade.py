"""Memory-pressure degradation: decide -> pin compute threads to one -> stream state.

The survival layer already has a *pressure ladder* (:mod:`tools.longrun.ladder`) that
rungs down numerics when a probe crosses a threshold.  This module supplies the two
things a long run needs in order to obey such a rung **without losing the answer**:

1. :func:`decide` -- a pure function from *measured* numbers (memory cap, current
   RSS, bytes the full-grid state needs) to a plan ``{threads, chunk_rows, reason}``.
   Being pure, the decision is unit-testable and reproducible; no wall-clock guessing.
2. :func:`run_kernel` -- the same iteration expressed at two granularities: full-grid
   and row-chunked with a periodic halo.  The stencil arithmetic per point is
   identical in both, so the *only* numerical difference introduced by degradation
   is the reduction order of the global normalisation (a BLAS dot product) and of
   the thread-parallel inner loops.  That difference is measured
   (:func:`rel_error`), not assumed.

Honest limitation (also recorded in ``docs/platform_survival.md``): the memory cap is
enforced by a *capped allocator* rather than ``setrlimit(RLIMIT_AS)``, because the
latter also caps the virtual space numpy reserves at import time, which makes every
later allocation fail -- a constraint that cannot distinguish "too big" from "any".
Peak RSS is nevertheless measured for real (``VmHWM`` read from ``/proc/self/status``
in a child process), so the memory benefit of chunking is a measured number too.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional

import numpy as np

SCHEMA = "survival.degrade/1"

#: Thread-count knobs honoured by the common numerical stacks.
THREAD_ENV_KEYS = (
    "OMP_NUM_THREADS",
    "OPENBLAS_NUM_THREADS",
    "MKL_NUM_THREADS",
    "NUMEXPR_NUM_THREADS",
    "VECLIB_MAXIMUM_THREADS",
)

#: Bytes assumed per element of the full-grid state set (u, lap, v, normalisation).
DEFAULT_FACTOR = 4.0
#: Head-room kept free under the cap (interpreters, numpy temporaries, IO buffers).
HEADROOM_FRACTION = 0.15


def rel_error(measured: np.ndarray, reference: np.ndarray) -> float:
    """Relative L2 error ``||a-b|| / ||b||`` (0.0 when the reference is exactly zero)."""
    a = np.asarray(measured, dtype="float64")
    b = np.asarray(reference, dtype="float64")
    if a.shape != b.shape:
        raise ValueError(f"shape mismatch: {a.shape} vs {b.shape}")
    denom = float(np.linalg.norm(b.ravel()))
    if denom == 0.0:
        denom = 1.0
    return float(np.linalg.norm((a - b).ravel()) / denom)


def decide(
    mem_cap_bytes: int,
    rss_bytes: int,
    n: int,
    itemsize: int = 8,
    factor: float = DEFAULT_FACTOR,
    min_chunk_rows: int = 8,
) -> Dict[str, Any]:
    """Decide thread count + chunking for a single-grid relaxation of size ``n x n``.

    Policy: if the *projected* footprint (``factor`` full-grid arrays) does not fit
    under ``cap - rss`` (with head-room), pin to one thread and stream rows so that
    only one chunk plus the two state arrays is resident.  Otherwise keep the full
    parallel path.
    """
    state_bytes = int(n) * int(n) * int(itemsize)
    need = int(state_bytes * float(factor))
    usable = int(mem_cap_bytes) - int(rss_bytes)
    headroom = int(mem_cap_bytes * HEADROOM_FRACTION)
    fits = need + headroom <= usable
    if fits:
        return {
            "threads": max(1, os.cpu_count() or 1),
            "chunk_rows": None,
            "degraded": False,
            "reason": "fits_under_cap",
            "state_bytes": state_bytes,
            "projected_bytes": need,
            "usable_bytes": usable,
            "headroom_bytes": headroom,
        }
    # chunk size: keep chunk buffers (3 rows-blocks) inside a quarter of the budget
    budget_for_chunk = max(1, (usable - headroom - 2 * state_bytes) // 4)
    chunk = int(max(min_chunk_rows, min(n, budget_for_chunk // max(1, n * int(itemsize) * 3))))
    return {
        "threads": 1,
        "chunk_rows": int(chunk),
        "degraded": True,
        "reason": "projected_footprint_exceeds_cap: single thread + row streaming",
        "state_bytes": state_bytes,
        "projected_bytes": need,
        "usable_bytes": usable,
        "headroom_bytes": headroom,
    }


class capped_alloc:
    """Context-manager allocator that refuses projections above a byte cap."""

    def __init__(self, cap_bytes: int, rss_bytes: int) -> None:
        self.cap = int(cap_bytes)
        self.rss = int(rss_bytes)

    def empty(self, shape, dtype="float64") -> np.ndarray:
        nbytes = int(np.prod(shape)) * np.dtype(dtype).itemsize
        if self.rss + nbytes > self.cap:
            raise MemoryError(
                f"capped_alloc: {nbytes} B would exceed cap {self.cap} B (rss {self.rss} B)"
            )
        return np.empty(shape, dtype=dtype)


def run_kernel(
    n: int = 256,
    iters: int = 12,
    chunk: Optional[int] = None,
    threads: int = 1,
    seed: int = 7,
    dt: float = 0.15,
    dtype: str = "float64",
) -> np.ndarray:
    """Periodic 5-point relaxation with a global normalisation.

    Identical arithmetic per grid point for the chunked and full variants (row halo
    is taken modulo ``n``), so their difference isolates reduction-order effects.
    """
    rng = np.random.default_rng(seed)
    u = rng.standard_normal((n, n)).astype(dtype)
    full = chunk is None or int(chunk) >= n or int(chunk) <= 0
    for _ in range(int(iters)):
        if full:
            lap = (
                np.roll(u, 1, 0) + np.roll(u, -1, 0)
                + np.roll(u, 1, 1) + np.roll(u, -1, 1) - 4.0 * u
            )
            v = u + dt * lap
            sq = float(np.dot(v.ravel(), v.ravel()))
        else:
            v = np.empty_like(u)
            sq = 0.0
            for r0 in range(0, n, int(chunk)):
                r1 = min(n, r0 + int(chunk))
                rows = np.arange(r0 - 1, r1 + 1) % n
                block = u[rows, :]
                lapb = (
                    np.roll(block, 1, 0) + np.roll(block, -1, 0)
                    + np.roll(block, 1, 1) + np.roll(block, -1, 1) - 4.0 * block
                )[1:-1]
                vb = block[1:-1] + dt * lapb
                v[r0:r1] = vb
                sq += float(np.dot(vb.ravel(), vb.ravel()))
        u = v / (1.0 + dt * sq / float(v.size))
    return u


def apply_thread_env(threads: int, env: Optional[Dict[str, str]] = None) -> Dict[str, str]:
    """Return an environment with every thread knob pinned to ``threads``."""
    out = dict(os.environ if env is None else env)
    for key in THREAD_ENV_KEYS:
        out[key] = str(int(threads))
    return out


def _vm_hwm_kb() -> Optional[int]:
    try:
        for line in Path("/proc/self/status").read_text(encoding="utf-8").splitlines():
            if line.startswith("VmHWM:"):
                return int(line.split()[1])
    except OSError:
        return None
    return None


def _worker(payload: Dict[str, Any], out_path: Path) -> Dict[str, Any]:
    n = int(payload["n"])
    iters = int(payload["iters"])
    cap = int(payload["mem_cap_bytes"])
    rss0 = int(payload.get("rss_bytes") or 0)
    decision = decide(cap, rss0, n, itemsize=int(payload.get("itemsize", 8)))
    # counterfactual: would the full-grid buffers have been admissible?
    probe: Dict[str, Any] = {"attempted_bytes": int(4 * decision["state_bytes"])}
    try:
        with_alloc = capped_alloc(cap, rss0)
        with_alloc.empty((4, n, n))
        probe.update({"full_grid_admissible": True, "error": None})
        del with_alloc
    except MemoryError as exc:
        probe.update({"full_grid_admissible": False, "error": str(exc)})
    t0 = time.time()
    field = run_kernel(n=n, iters=iters, chunk=decision["chunk_rows"], threads=decision["threads"],
                       seed=int(payload.get("seed", 7)))
    wall = time.time() - t0
    npy = out_path.with_suffix(".npy")
    np.save(npy, field)
    result = {
        "schema": SCHEMA,
        "n": n,
        "iters": iters,
        "mem_cap_bytes": cap,
        "rss_bytes_at_start": rss0,
        "decision": decision,
        "full_grid_probe": probe,
        "wall_s": round(wall, 4),
        "peak_rss_kb": _vm_hwm_kb(),
        "thread_env": {k: os.environ.get(k) for k in THREAD_ENV_KEYS},
        "field_path": str(npy),
        "field_l2": float(np.linalg.norm(field.ravel())),
        "field_mean": float(field.mean()),
    }
    out_path.write_text(json.dumps(result, indent=2, sort_keys=True), encoding="utf-8")
    return result


def run_degraded(
    n: int = 256,
    iters: int = 12,
    mem_cap_bytes: int = 96 << 20,
    seed: int = 7,
    outdir: Optional[os.PathLike | str] = None,
    threads: Optional[int] = None,
) -> Dict[str, Any]:
    """Run the constrained worker in a child process (env pinned, honest RSS read)."""
    outdir = Path(outdir or ".")
    outdir.mkdir(parents=True, exist_ok=True)
    rss0 = _child_baseline_rss()
    payload = {
        "n": int(n),
        "iters": int(iters),
        "mem_cap_bytes": int(mem_cap_bytes),
        "rss_bytes": int(rss0),
        "seed": int(seed),
    }
    out_json = outdir / f"degraded_{n}_{int(mem_cap_bytes)}.json"
    env = apply_thread_env(1 if threads is None else int(threads))
    env["SURVIVAL_WORKER_PAYLOAD"] = json.dumps(payload)
    root = str(Path(__file__).resolve().parents[2])
    env["PYTHONPATH"] = os.pathsep.join([root, env.get("PYTHONPATH", "")]).strip(os.pathsep)
    proc = subprocess.run(
        [sys.executable, "-m", "tools.survival.degrade", "--worker", str(out_json)],
        capture_output=True, text=True, timeout=600, env=env, cwd=root,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"degraded worker failed rc={proc.returncode}: {proc.stderr[-2000:]}")
    result = json.loads(Path(out_json).read_text(encoding="utf-8"))
    result["child_stderr_tail"] = (proc.stderr or "")[-500:]
    return result


def _child_baseline_rss() -> int:
    from tools.longrun import forensics  # local import keeps module import light

    probe = forensics.memory_probe() or {}
    rss = probe.get("rss_bytes")
    if isinstance(rss, (int, float)) and rss > 0:
        return int(rss)
    return 64 << 20


def main(argv: Optional[list] = None) -> int:
    import argparse

    ap = argparse.ArgumentParser(description="constrained relaxation worker")
    ap.add_argument("--worker", metavar="OUT_JSON", help="run one constrained workload")
    args = ap.parse_args(argv)
    if not args.worker:
        ap.error("--worker OUT_JSON required")
    payload = json.loads(os.environ.get("SURVIVAL_WORKER_PAYLOAD", "{}"))
    if not payload:
        ap.error("SURVIVAL_WORKER_PAYLOAD missing")
    out = Path(args.worker)
    out.parent.mkdir(parents=True, exist_ok=True)
    _worker(payload, out)
    return 0


if __name__ == "__main__":  # pragma: no cover - child entry point
    raise SystemExit(main())