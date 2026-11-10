"""Unified, domain-neutral state protocol shared by every coupled solver.

What a "solver" has to expose to be checkpointable is deliberately small:

``name``        stable identifier, used as the payload file stem
``arrays``      ``{array_name: np.ndarray}`` -- the numeric state
``scalars``     JSON-serialisable auxiliary state (counters, flags, ...)
``phase``       integer sub-step index inside the current coupling interval
``time``        logical time of the state (``float``)

Nothing in this protocol knows whether an array holds a discrete distribution,
a nodal field or a particle table; the three archetype solvers in
:mod:`tools.multisolver_ckpt.models` are just members of the same shape.

Bit-level identity
------------------
Every digest here is computed over ``dtype.str | shape | raw C-order bytes`` --
the IEEE-754 bit pattern, not a tolerance-based comparison.  Floats in the
``scalars`` mapping are canonicalised as ``float.hex()`` so that the digest is
exact (``repr`` of a float is round-trip exact but ``hex`` makes the intent
unambiguous) and portable across JSON writers.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Dict, Iterable, List, Mapping, Protocol, Sequence, Tuple, runtime_checkable

import numpy as np

if TYPE_CHECKING:  # pragma: no cover
    from concurrent.futures import ThreadPoolExecutor

SCHEMA = "multisolver.ckpt/2"


# ───────────────────────────────────────────────────────────────────────────
# Bit-level digests
# ───────────────────────────────────────────────────────────────────────────
def canonical_json(obj: Any) -> str:
    """Deterministic JSON: sorted keys, no whitespace, exact floats."""

    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=True, default=_json_default)


def _json_default(obj: Any) -> Any:
    if isinstance(obj, np.generic):
        return obj.item()
    if isinstance(obj, np.ndarray):
        return obj.tolist()
    raise TypeError(f"not JSON serialisable: {type(obj).__name__}")


def canonical_scalars(scalars: Mapping[str, Any] | None) -> Dict[str, Any]:
    """Canonical form of a scalar mapping (floats as hex, arrays as lists)."""

    out: Dict[str, Any] = {}
    for key in sorted(scalars or {}):
        value = scalars[key]
        if isinstance(value, (bool, np.bool_)):
            out[key] = bool(value)
        elif isinstance(value, (int, np.integer)):
            out[key] = int(value)
        elif isinstance(value, (float, np.floating)):
            fv = float(value)
            # tagged so the inverse mapping is unambiguous even when a solver
            # stores a genuine string scalar that looks like a float literal.
            out[key] = {"f": fv.hex()} if math.isfinite(fv) else {"special": str(fv)}
        elif isinstance(value, np.ndarray):
            out[key] = {
                "dtype": value.dtype.str,
                "shape": list(value.shape),
                "sha256": array_digest(value),
            }
        elif isinstance(value, (list, tuple)):
            out[key] = list(value)
        elif isinstance(value, dict):
            out[key] = canonical_scalars(value)
        else:
            out[key] = value if isinstance(value, str) else repr(value)
    return out


# Hashing strategy
# ----------------
# A *single* SHA-256 stream runs at ~127 MB/s on the loongarch64 nodes of this
# fleet (no SHA extensions), which would make a multi-GiB checkpoint verification
# CPU-bound long before the disk is.  ``hashlib`` releases the GIL for large
# buffers, so the bytes are digested in fixed 8 MiB chunks whose digests are
# combined into a Merkle root:
#
#     digest = sha256(header || chunk_count || sha256(chunk_0) || ... )
#
# This is the standard tree-hash construction (same collision resistance
# reasoning as a plain concatenation), it is bit-exactly reproducible on any
# machine *provided the chunk size is part of the header*, and it scales with
# cores.  Measured on this host: 127 MB/s single-thread -> ~760 MB/s with 8
# workers.
DIGEST_CHUNK_BYTES = 8 * 1024 * 1024
_HASH_WORKERS = min(8, os.cpu_count() or 1)
_HASH_POOL: "ThreadPoolExecutor | None" = None


def set_hash_workers(workers: int) -> None:
    """Adjust the digest worker pool (1 disables parallelism)."""

    global _HASH_WORKERS, _HASH_POOL
    _HASH_WORKERS = max(1, int(workers))
    _HASH_POOL = None


def get_hash_workers() -> int:
    return _HASH_WORKERS


def _pool() -> "ThreadPoolExecutor":
    global _HASH_POOL
    if _HASH_POOL is None:
        from concurrent.futures import ThreadPoolExecutor

        _HASH_POOL = ThreadPoolExecutor(max_workers=_HASH_WORKERS, thread_name_prefix="ckpt-hash")
    return _HASH_POOL


def sha256_bytes(data: bytes) -> str:
    """Plain (serial) SHA-256 hex digest."""

    return hashlib.sha256(data).hexdigest()


def _sha256_hex(buf) -> str:
    return hashlib.sha256(buf).hexdigest()


def sha256_parallel(buf, *, chunk_bytes: int = DIGEST_CHUNK_BYTES, header: bytes = b"") -> str:
    """Chunked SHA-256 (Merkle root), parallel across chunks."""

    view = memoryview(buf)
    nbytes = len(view)
    head = bytes(header) + canonical_json(
        {"chunk_bytes": int(chunk_bytes), "nbytes": int(nbytes), "order": "C"}
    ).encode("ascii") + b"\x00"
    if nbytes <= chunk_bytes or _HASH_WORKERS == 1:
        h = hashlib.sha256()
        h.update(head)
        h.update(view)
        return h.hexdigest()
    spans = [(i, min(i + chunk_bytes, nbytes)) for i in range(0, nbytes, chunk_bytes)]
    digests = list(_pool().map(lambda sp: hashlib.sha256(view[sp[0]:sp[1]]).digest(), spans))
    h = hashlib.sha256()
    h.update(head)
    for d in digests:
        h.update(d)
    return h.hexdigest()


def array_chunk_digests(arr: np.ndarray, *, chunk_bytes: int = DIGEST_CHUNK_BYTES
                        ) -> Tuple[str, List[Dict[str, Any]]]:
    """Return ``(array_digest, chunk_metadata)`` from a single hashing pass."""

    a = np.ascontiguousarray(arr)
    view = memoryview(a).cast("B")
    nbytes = int(a.nbytes)
    header = canonical_json({"dtype": a.dtype.str, "shape": list(a.shape), "order": "C"})
    spans = [(i, min(i + chunk_bytes, nbytes)) for i in range(0, nbytes, chunk_bytes)] or [(0, 0)]
    if _HASH_WORKERS == 1 or len(spans) == 1 or nbytes <= chunk_bytes:
        chunk_hex = [hashlib.sha256(view[s:e]).hexdigest() for s, e in spans]
    else:
        chunk_hex = list(_pool().map(lambda sp: hashlib.sha256(view[sp[0]:sp[1]]).hexdigest(), spans))
    h = hashlib.sha256()
    h.update(header.encode("ascii"))
    h.update(b"\x00")
    h.update(canonical_json({"chunk_bytes": int(chunk_bytes), "chunks": len(spans)}).encode("ascii"))
    h.update(b"\x00")
    for c in chunk_hex:
        h.update(bytes.fromhex(c))
    chunks = [
        {"index": i, "offset": int(s), "nbytes": int(e - s), "sha256": chunk_hex[i]}
        for i, (s, e) in enumerate(spans)
    ]
    return h.hexdigest(), chunks


def array_digest(arr: np.ndarray, *, chunk_bytes: int = DIGEST_CHUNK_BYTES) -> str:
    """Bit-level digest: dtype + shape + C-order bytes (chunked, parallel)."""

    return array_chunk_digests(arr, chunk_bytes=chunk_bytes)[0]


def compose_state_digest(array_digests: Mapping[str, str], scalars: Mapping[str, Any] | None = None,
                         extra: Mapping[str, Any] | None = None) -> str:
    """Digest from already-computed per-array digests (avoids a second hash pass)."""

    payload = {
        "arrays": {name: array_digests[name] for name in sorted(array_digests)},
        "scalars": canonical_scalars(scalars),
    }
    if extra:
        payload["extra"] = canonical_scalars(extra)
    return sha256_bytes(canonical_json(payload).encode("ascii"))


def state_digest(arrays: Mapping[str, np.ndarray], scalars: Mapping[str, Any] | None = None,
                 extra: Mapping[str, Any] | None = None) -> str:
    return compose_state_digest(
        {name: array_digest(arrays[name]) for name in sorted(arrays)}, scalars, extra
    )


# ───────────────────────────────────────────────────────────────────────────
# Snapshots / bundles
# ───────────────────────────────────────────────────────────────────────────
def _copy_arrays(arrays: Mapping[str, np.ndarray]) -> Dict[str, np.ndarray]:
    out: Dict[str, np.ndarray] = {}
    for name in sorted(arrays):
        arr = np.array(arrays[name], copy=True, order="C")
        if arr.dtype.kind not in "fiub":
            raise TypeError(f"array {name!r} has unsupported dtype {arr.dtype!r}")
        out[name] = arr
    return out


@dataclass
class SolverSnapshot:
    """Frozen state of one solver at one instant (arrays are private copies)."""

    name: str
    arrays: Dict[str, np.ndarray]
    scalars: Dict[str, Any] = field(default_factory=dict)
    phase: int = 0
    time: float = 0.0
    _digest_cache: str | None = field(default=None, repr=False, compare=False)

    def __post_init__(self) -> None:
        self.name = str(self.name)
        self.arrays = _copy_arrays(self.arrays)
        self.scalars = dict(self.scalars or {})
        self.phase = int(self.phase)
        self.time = float(self.time)
        if not math.isfinite(self.time):
            raise ValueError(f"solver {self.name!r} has non-finite time {self.time!r}")

    @property
    def digest(self) -> str:
        if self._digest_cache is None:
            self._digest_cache = self.compute_digest()
        return self._digest_cache

    def compute_digest(self, array_digests: Mapping[str, str] | None = None) -> str:
        """Bit-level identity; pass ``array_digests`` to reuse an existing hash pass."""

        digests = (
            {name: array_digest(self.arrays[name]) for name in sorted(self.arrays)}
            if array_digests is None else dict(array_digests)
        )
        return compose_state_digest(
            digests, self.scalars, extra={"phase": self.phase, "time": float(self.time).hex()}
        )

    def cache_digest(self, digest: str) -> None:
        """Record a digest computed elsewhere so later reads do not re-hash."""

        self._digest_cache = str(digest)

    def meta(self) -> Dict[str, Any]:
        return {
            "arrays": {
                name: {
                    "dtype": self.arrays[name].dtype.str,
                    "shape": list(self.arrays[name].shape),
                    "nbytes": int(self.arrays[name].nbytes),
                    "sha256": array_digest(self.arrays[name]),
                }
                for name in sorted(self.arrays)
            },
            "scalars": canonical_scalars(self.scalars),
            "phase": self.phase,
            "time_hex": float(self.time).hex(),
            "digest": self.digest,
        }

    def array(self, name: str) -> np.ndarray:
        try:
            return self.arrays[name]
        except KeyError as exc:
            raise KeyError(f"solver {self.name!r} has no array {name!r}") from exc


@dataclass
class StateBundle:
    """Coupled state: one snapshot per solver, plus the master step index."""

    step: int
    solvers: Dict[str, SolverSnapshot]

    def __post_init__(self) -> None:
        self.step = int(self.step)
        self.solvers = {str(k): v for k, v in self.solvers.items()}
        for key, snap in self.solvers.items():
            if key != snap.name:
                raise ValueError(f"bundle key {key!r} does not match snapshot name {snap.name!r}")

    # -- views ------------------------------------------------------------ #
    def names(self) -> Sequence[str]:
        return tuple(sorted(self.solvers))

    def snapshot(self, name: str) -> SolverSnapshot:
        try:
            return self.solvers[name]
        except KeyError as exc:
            raise KeyError(f"bundle has no solver {name!r} (has {list(self.names())})") from exc

    def arrays(self) -> Dict[str, np.ndarray]:
        """Flat ``"solver/array"`` view used by energy functionals."""

        return {f"{s}/{a}": self.solvers[s].arrays[a] for s in self.names() for a in sorted(self.solvers[s].arrays)}

    def phases(self) -> Dict[str, int]:
        return {s: self.solvers[s].phase for s in self.names()}

    def times(self) -> Dict[str, float]:
        return {s: self.solvers[s].time for s in self.names()}

    # -- identity --------------------------------------------------------- #
    @property
    def digest(self) -> str:
        payload = {
            "schema": SCHEMA,
            "step": self.step,
            "solvers": {s: {"digest": self.solvers[s].digest} for s in self.names()},
        }
        return sha256_bytes(canonical_json(payload).encode("ascii"))

    def describe(self) -> Dict[str, Any]:
        return {
            "step": self.step,
            "digest": self.digest,
            "solvers": {s: self.solvers[s].meta() for s in self.names()},
        }


# ───────────────────────────────────────────────────────────────────────────
# Solver protocol + generic adapter base
# ───────────────────────────────────────────────────────────────────────────
@runtime_checkable
class StatefulSolver(Protocol):
    """Minimum surface a solver must implement to be checkpointed."""

    name: str

    @property
    def steps_per_master(self) -> int:  # pragma: no cover - protocol
        ...

    def snapshot(self) -> SolverSnapshot:  # pragma: no cover - protocol
        ...

    def restore(self, snap: SolverSnapshot) -> None:  # pragma: no cover - protocol
        ...


class ArraySolverBase:
    """Generic adapter: state = arrays + scalars, dynamics = in-place update.

    Subclasses implement :meth:`advance` (one sub-step of duration ``dt``) and
    the hook :meth:`after_restore`.  Everything else -- snapshotting, digesting,
    phase bookkeeping, strict restore validation -- is handled once, here, so
    the three archetypes in :mod:`tools.multisolver_ckpt.models` share one
    checkpoint protocol.
    """

    def __init__(self, name: str, state: Mapping[str, np.ndarray], scalars: Mapping[str, Any] | None = None,
                 *, steps_per_master: int = 1, dt: float = 1.0, time: float = 0.0) -> None:
        self.name = str(name)
        self.arrays: Dict[str, np.ndarray] = {
            k: np.array(v, copy=True, order="C") for k, v in sorted(state.items())
        }
        for k, arr in self.arrays.items():
            if not np.issubdtype(arr.dtype, np.number):
                raise TypeError(f"array {k!r} of solver {self.name!r} is not numeric: {arr.dtype}")
        self.scalars: Dict[str, Any] = dict(scalars or {})
        self.steps_per_master = int(steps_per_master)
        if self.steps_per_master < 1:
            raise ValueError("steps_per_master must be >= 1")
        self.dt = float(dt)
        self.time = float(time)
        self.phase = 0  # sub-steps completed inside the current master interval

    # -- protocol --------------------------------------------------------- #
    def snapshot(self) -> SolverSnapshot:
        return SolverSnapshot(
            name=self.name,
            arrays=self.arrays,
            scalars=self.scalars,
            phase=self.phase,
            time=self.time,
        )

    def restore(self, snap: SolverSnapshot) -> None:
        """Strict restore: names/dtypes/shapes must match exactly.

        A silently broadcast or truncated restore is the classic way to lose
        bit-level reproducibility, so mismatches raise instead of coercing.
        """

        if not isinstance(snap, SolverSnapshot):
            raise TypeError(f"expected SolverSnapshot, got {type(snap).__name__}")
        if snap.name != self.name:
            raise ValueError(f"snapshot for {snap.name!r} cannot restore solver {self.name!r}")
        if set(snap.arrays) != set(self.arrays):
            raise ValueError(
                f"array set mismatch for solver {self.name!r}: snapshot={sorted(snap.arrays)} "
                f"solver={sorted(self.arrays)}"
            )
        for key in sorted(self.arrays):
            incoming = snap.arrays[key]
            current = self.arrays[key]
            if incoming.dtype != current.dtype:
                raise ValueError(
                    f"dtype mismatch for {self.name}/{key}: {incoming.dtype} != {current.dtype}"
                )
            if incoming.shape != current.shape:
                raise ValueError(
                    f"shape mismatch for {self.name}/{key}: {incoming.shape} != {current.shape}"
                )
            self.arrays[key][...] = incoming
        self.scalars = dict(snap.scalars)
        self.phase = int(snap.phase)
        self.time = float(snap.time)
        self.after_restore()

    # -- dynamics hooks --------------------------------------------------- #
    def advance(self, dt: float) -> None:  # pragma: no cover - subclass hook
        raise NotImplementedError

    def after_restore(self) -> None:  # pragma: no cover - optional hook
        """Recreate derived caches after a restore (default: nothing to do)."""

    # -- convenience ------------------------------------------------------ #
    def __repr__(self) -> str:  # pragma: no cover - cosmetic
        sizes = ", ".join(f"{k}{self.arrays[k].shape}" for k in sorted(self.arrays))
        return f"<{type(self).__name__} {self.name} [{sizes}] t={self.time:g} phase={self.phase}>"


def bundle_from_solvers(step: int, solvers: Iterable[StatefulSolver]) -> StateBundle:
    snaps = {}
    for solver in solvers:
        snap = solver.snapshot()
        if snap.name in snaps:
            raise ValueError(f"duplicate solver name {snap.name!r} in bundle")
        snaps[snap.name] = snap
    if not snaps:
        raise ValueError("cannot build a bundle from zero solvers")
    return StateBundle(step=int(step), solvers=snaps)