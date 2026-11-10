"""I/O benchmark harness for checkpoint save/load throughput.

The acceptance criterion AC7 asks for a 100 GB state snapshot to be saved and
loaded in under 60 s with parallel I/O.  A single sandbox cannot honestly write
100 GB inside a 32 GB-free filesystem, so this harness does the only defensible
thing:

* it measures **real** round-trips at sizes that fit (default 0.25 / 0.5 / 1 / 2
  GiB), including full digest verification on load, and
* it reports the measured throughput together with the **explicit
  extrapolation** to 100 GiB, so the reader can see exactly how far the
  measured rate is from the 1.79 GiB/s (100 GiB / 60 s) requirement.

The report never presents the extrapolation as a measurement.

Payload data are standard-normal float64 (incompressible on purpose): a zeros
payload would flatter the compressor by orders of magnitude.
"""

from __future__ import annotations

import os
import shutil
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence

import numpy as np

from .protocol import SolverSnapshot, StateBundle
from .store import CheckpointStore
from .sync import CouplingTimeGrid, SyncPolicy

GIB = 1024 ** 3
AC7_PAYLOAD_GIB = 100.0
AC7_TARGET_SECONDS = 60.0


@dataclass
class BenchResult:
    label: str
    payload_bytes: int
    stored_bytes: int
    n_solvers: int
    codec: str
    level: Optional[int]
    max_workers: int
    save_seconds: float
    load_seconds: float
    verify_seconds: float
    lossless: bool
    state_digest: str

    @property
    def payload_gib(self) -> float:
        return self.payload_bytes / GIB

    @property
    def save_gibs(self) -> float:
        return (self.payload_bytes / GIB) / self.save_seconds if self.save_seconds > 0 else float("inf")

    @property
    def load_gibs(self) -> float:
        return (self.payload_bytes / GIB) / self.load_seconds if self.load_seconds > 0 else float("inf")

    @property
    def compression_ratio(self) -> float:
        return self.stored_bytes / self.payload_bytes if self.payload_bytes else 1.0

    def extrapolation(self) -> Dict[str, Any]:
        need = AC7_PAYLOAD_GIB / AC7_TARGET_SECONDS
        save_s = AC7_PAYLOAD_GIB / self.save_gibs if self.save_gibs else float("inf")
        load_s = AC7_PAYLOAD_GIB / self.load_gibs if self.load_gibs else float("inf")
        return {
            "payload_gib": AC7_PAYLOAD_GIB,
            "target_seconds": AC7_TARGET_SECONDS,
            "required_gibs": need,
            "save_seconds_extrapolated": save_s,
            "load_seconds_extrapolated": load_s,
            "save_meets_target": bool(save_s < AC7_TARGET_SECONDS),
            "load_meets_target": bool(load_s < AC7_TARGET_SECONDS),
            "speedup_needed_save": max(1.0, save_s / AC7_TARGET_SECONDS),
            "speedup_needed_load": max(1.0, load_s / AC7_TARGET_SECONDS),
        }

    def to_dict(self) -> Dict[str, Any]:
        return {
            "label": self.label,
            "payload_bytes": int(self.payload_bytes),
            "payload_gib": self.payload_gib,
            "stored_bytes": int(self.stored_bytes),
            "compression_ratio": self.compression_ratio,
            "n_solvers": int(self.n_solvers),
            "codec": self.codec,
            "level": self.level,
            "max_workers": int(self.max_workers),
            "save_seconds": float(self.save_seconds),
            "load_seconds": float(self.load_seconds),
            "save_gibs": self.save_gibs,
            "load_gibs": self.load_gibs,
            "measured_total_gibs": 2.0 * self.payload_gib / max(self.save_seconds + self.load_seconds, 1e-12),
            "lossless": bool(self.lossless),
            "state_digest": self.state_digest,
            "extrapolation": self.extrapolation(),
        }


def make_payload(total_bytes: int, *, n_solvers: int = 3, seed: int = 5,
                 step: int = 0) -> StateBundle:
    """Incompressible-but-deterministic payload split across ``n_solvers``."""

    total = int(total_bytes)
    if total < 8 * n_solvers:
        raise ValueError("payload too small for the requested number of solvers")
    per = total // n_solvers
    per -= per % 8
    snaps: Dict[str, SolverSnapshot] = {}
    rng = np.random.default_rng(int(seed))
    for i in range(int(n_solvers)):
        n = per // 8
        half = n // 2
        a = rng.standard_normal(half).astype(np.float64)
        b = rng.standard_normal(n - half).astype(np.float64)
        snaps[f"solver{i}"] = SolverSnapshot(
            name=f"solver{i}", arrays={"a": a, "b": b},
            scalars={"bytes": int(per), "index": i}, phase=0, time=0.0,
        )
    return StateBundle(step=int(step), solvers=snaps)


def check_headroom(root: str | os.PathLike[str], payload_bytes: int, *, factor: float = 1.5) -> Dict[str, Any]:
    path = Path(root)
    path.mkdir(parents=True, exist_ok=True)
    usage = shutil.disk_usage(path)
    need = int(payload_bytes * factor) + 256 * 1024 ** 2
    info = {
        "free_bytes": int(usage.free), "need_bytes": need,
        "payload_bytes": int(payload_bytes), "factor": float(factor),
    }
    if usage.free < need:
        raise ValueError(
            f"insufficient space under {path}: free {usage.free / GIB:.2f} GiB < "
            f"required {need / GIB:.2f} GiB (payload {payload_bytes / GIB:.2f} GiB × {factor})"
        )
    return info


def _drop_cache(path: Path) -> None:
    """Best-effort page-cache eviction so load timing is not pure memcpy."""

    try:
        fd = os.open(path, os.O_RDONLY)
    except OSError:  # pragma: no cover - platform dependent
        return
    try:
        os.posix_fadvise(fd, 0, 0, os.POSIX_FADV_DONTNEED)
    except (AttributeError, OSError):  # pragma: no cover
        pass
    finally:
        os.close(fd)


def run_bench(root: str | os.PathLike[str], payload_bytes: int, *, label: str = "",
              n_solvers: int = 3, codec: str = "zstd", level: int = 3, threads: int = 4,
              max_workers: int = 4, keep: bool = False, drop_cache: bool = True) -> BenchResult:
    root = Path(root)
    info = check_headroom(root, payload_bytes)
    bundle = make_payload(payload_bytes, n_solvers=n_solvers)
    store = CheckpointStore(
        root, codec=codec, level=level, threads=threads,
        grid=CouplingTimeGrid(dt_master=1.0, n_sub=1), sync_policy=SyncPolicy.STRICT,
        max_workers=max_workers,
    )
    t0 = time.perf_counter()
    saved = store.save(0, bundle, label=label or f"bench-{payload_bytes}")
    save_seconds = time.perf_counter() - t0

    if drop_cache:
        for child in store.dir_of(0).iterdir():
            if child.is_file():
                _drop_cache(child)

    t0 = time.perf_counter()
    loaded = store.load(0)
    load_seconds = time.perf_counter() - t0
    lossless = loaded.digest == bundle.digest
    result = BenchResult(
        label=label or f"{payload_bytes / GIB:.2f}GiB",
        payload_bytes=len(bundle.arrays()) and saved.raw_bytes,
        stored_bytes=saved.stored_bytes, n_solvers=n_solvers, codec=codec,
        level=None if codec == "raw" else level, max_workers=max_workers,
        save_seconds=save_seconds, load_seconds=load_seconds,
        verify_seconds=loaded.verify_seconds, lossless=lossless,
        state_digest=saved.state_digest,
    )
    result.headroom = info  # type: ignore[attr-defined]
    if keep:
        result.store_path = str(root)  # type: ignore[attr-defined]
    else:
        shutil.rmtree(root, ignore_errors=True)
    return result


def component_profile(payload_bytes: int, *, workdir: str | os.PathLike[str] | None = None,
                      codec: str = "zstd", level: int = 3, threads: int = 4) -> Dict[str, Any]:
    """Split the save cost into its three components (hash / compress / write).

    Without this decomposition "the checkpoint is slow" is an opinion; with it
    the bottleneck is attributable to a specific stage and a specific piece of
    hardware.
    """

    import tempfile

    from .codec import get_codec
    from .protocol import sha256_parallel

    tmp = Path(workdir or tempfile.mkdtemp(prefix="ckpt-comp-"))
    tmp.mkdir(parents=True, exist_ok=True)
    buf = np.random.default_rng(7).standard_normal(int(payload_bytes) // 8).astype(np.float64).tobytes()
    out: Dict[str, Any] = {"payload_bytes": len(buf), "codec": codec, "level": level, "threads": threads}
    t0 = time.perf_counter()
    sha256_parallel(buf)
    out["hash_seconds"] = time.perf_counter() - t0
    c = get_codec(codec, level=level, threads=threads)
    t0 = time.perf_counter()
    stored = c.compress(buf)
    out["compress_seconds"] = time.perf_counter() - t0
    out["compression_ratio"] = len(stored) / max(len(buf), 1)
    path = tmp / "payload.bin"
    t0 = time.perf_counter()
    with open(path, "wb") as fh:
        fh.write(stored)
        fh.flush()
        os.fsync(fh.fileno())
    out["write_seconds"] = time.perf_counter() - t0
    t0 = time.perf_counter()
    blob = path.read_bytes()
    out["read_seconds"] = time.perf_counter() - t0
    t0 = time.perf_counter()
    restored = c.decompress(blob)
    out["decompress_seconds"] = time.perf_counter() - t0
    out["lossless"] = restored == buf
    for key, seconds in (("hash", out["hash_seconds"]), ("compress", out["compress_seconds"]),
                         ("write", out["write_seconds"]), ("read", out["read_seconds"]),
                         ("decompress", out["decompress_seconds"])):
        out[f"{key}_mbps"] = (len(buf) / 1e6) / max(seconds, 1e-12)
    shutil.rmtree(tmp, ignore_errors=True)
    return out


def run_size_sweep(base_root: str | os.PathLike[str], sizes_bytes: Iterable[int], *,
                   n_solvers: int = 3, codec: str = "zstd", level: int = 3,
                   max_workers: int = 4, threads: int = 4) -> List[Dict[str, Any]]:
    base = Path(base_root)
    out: List[Dict[str, Any]] = []
    for size in sizes_bytes:
        sub = base / f"bench-{int(size)}"
        res = run_bench(sub, int(size), n_solvers=n_solvers, codec=codec, level=level,
                        max_workers=max_workers, threads=threads, keep=False)
        payload = res.to_dict()
        payload["headroom"] = getattr(res, "headroom", None)
        out.append(payload)
    return out


def run_worker_sweep(base_root: str | os.PathLike[str], payload_bytes: int, *,
                     workers: Sequence[int] = (1, 2, 4), n_solvers: int = 6,
                     codec: str = "zstd", level: int = 3) -> List[Dict[str, Any]]:
    """Same payload, different numbers of payload-writing threads."""

    out = []
    for w in workers:
        sub = Path(base_root) / f"workers-{int(w)}"
        res = run_bench(sub, payload_bytes, n_solvers=n_solvers, codec=codec, level=level,
                        max_workers=int(w), label=f"workers={w}")
        data = res.to_dict()
        data["workers"] = int(w)
        out.append(data)
    return out