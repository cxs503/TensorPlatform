"""Bit-reproducible checkpointing for coupled solver states (domain-agnostic).

Scope (deliberately zero domain vocabulary): this module only knows about

* **solver states** = a set of named fixed-dtype numeric arrays ("fields") plus a
  step counter, a simulation time and an optional RNG state,
* **an exchange bus** used to couple several solvers (operator splitting with a
  synchronisation period),
* **an energy ledger** = a linear functional of the state fields.

Nothing here knows what a field *means*; solver-family semantics (which arrays a
given solver contributes, which ledger terms exist) are injected by the caller
through the public constructors.  That keeps the platform layer free of domain
semantics while giving every coupled run the same reproducibility machinery.

Capabilities
------------
1. ``SolverState``          canonical in-memory state (little-endian, C-contiguous,
                            hash-stable) with ``digest()``.
2. ``CheckpointStore``      deterministic on-disk format: magic + canonical JSON
                            header (self-checksummed) + payload; optional
                            whole-payload compression (zstd when the ``zstandard``
                            module is importable, otherwise stdlib ``lzma``); every
                            entry carries a SHA-256 and block-level SHA-256 page
                            checksums so a single flipped bit is both *detected*
                            and *localised*.
3. ``ExchangeBus`` / ``CoupledRunner``
                            coupling loop with explicit synchronisation points;
                            the runner can emit/consume checkpoints so a restarted
                            run is bit-identical to a continuous one (including
                            the RNG stream position).
4. ``EnergyLedger``         energy bookkeeping: checkpoint residual, per-segment
                            drift.
5. ``TimeSynchronizer``     decides whether two checkpoints are *time-synchronised*
                            (same step, same time, both on synchronisation points,
                            identical digest).
6. ``CheckpointManager``    atomic writes, retention pruning, verification on read
                            and automatic rollback to the newest **valid**
                            checkpoint when the newest file is corrupt.
7. ``measure_io_throughput`` throughput probe with a 100 GB extrapolation helper.

File layout (all integers little-endian)::

    b"TPCKPT\\x00\\x01"                 9-byte magic/version
    <u64 header_len>
    header_json                       canonical JSON (sorted keys, no spaces)
    sha256(header_json)               32 bytes, self-checksum of the header
    payload                           concatenated entry bytes (optionally
                                      compressed as a whole with `codec`)

The header lists, per entry, ``name``/``dtype``/``shape``/``nbytes``/``offset``/
``sha256``/``blocks``; ``digest`` is the SHA-256 of the canonical description
(version + meta + entry names/dtypes/shapes/hashes), hence two states are
bit-identical iff their digests are equal.
"""

from __future__ import annotations

import hashlib
import json
import lzma
import os
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field as dc_field
from pathlib import Path
from types import MappingProxyType
from typing import Any, Callable, Mapping, Sequence

import numpy as np

# --------------------------------------------------------------------------- #
# constants
# --------------------------------------------------------------------------- #
MAGIC = b"TPCKPT\x00\x01"
FORMAT_VERSION = 1
DEFAULT_BLOCK_SIZE = 64 * 1024
_HASH_LEN = 32

#: default block size for the page checksums (localises a flipped bit)
BLOCK_SIZE = DEFAULT_BLOCK_SIZE

CODEC_NONE = "none"
CODEC_ZSTD = "zstd"
CODEC_LZMA = "lzma"


def _zstd_module():
    for mod_name in ("zstandard", "zstd"):
        try:  # pragma: no cover - depends on deployment
            module = __import__(mod_name)
        except Exception:  # noqa: BLE001
            continue
        return module
    return None


def available_codecs() -> tuple[str, ...]:
    """Compression codecs usable on this interpreter (always includes ``none``)."""
    codecs = [CODEC_NONE, CODEC_LZMA]
    if _zstd_module() is not None:
        codecs.insert(1, CODEC_ZSTD)
    return tuple(codecs)


def resolve_codec(codec: str | None) -> str:
    """Map ``auto`` to the best available codec; validate explicit choices."""
    if codec in (None, "auto"):
        return CODEC_ZSTD if _zstd_module() is not None else CODEC_LZMA
    if codec not in available_codecs():
        raise ValueError(
            f"codec {codec!r} unavailable; choose from {available_codecs()!r}"
        )
    return codec


def _compress(raw: bytes, codec: str) -> bytes:
    if codec == CODEC_NONE:
        return raw
    if codec == CODEC_ZSTD:
        module = _zstd_module()
        if module is None:  # pragma: no cover - guarded by resolve_codec
            raise RuntimeError("zstd requested but module unavailable")
        if hasattr(module, "ZstdCompressor"):
            return module.ZstdCompressor(level=3).compress(raw)
        return module.compress(raw, 3)
    if codec == CODEC_LZMA:
        return lzma.compress(raw, format=lzma.FORMAT_XZ, preset=1)
    raise ValueError(f"unknown codec {codec!r}")


def _decompress(blob: bytes, codec: str, expected_len: int) -> bytes:
    if codec == CODEC_NONE:
        raw = blob
    elif codec == CODEC_ZSTD:
        module = _zstd_module()
        if module is None:  # pragma: no cover
            raise RuntimeError("checkpoint needs zstd but module unavailable")
        if hasattr(module, "ZstdDecompressor"):
            raw = module.ZstdDecompressor().decompress(blob, max_output_size=expected_len)
        else:
            raw = module.decompress(blob, max_output_size=expected_len)
    elif codec == CODEC_LZMA:
        raw = lzma.decompress(blob)
    else:
        raise ValueError(f"unknown codec {codec!r}")
    if expected_len is not None and len(raw) != expected_len:
        raise CheckpointCorruption(
            "payload length mismatch after decompression",
            detail={"expected_bytes": expected_len, "actual_bytes": len(raw)},
        )
    return raw


# --------------------------------------------------------------------------- #
# errors
# --------------------------------------------------------------------------- #
class CheckpointError(Exception):
    """Base class for checkpoint failures."""


class CheckpointCorruption(CheckpointError):
    """Raised when a checkpoint fails self-verification."""

    def __init__(self, message: str, *, entry: str | None = None,
                 offset: int | None = None,
                 detail: Mapping[str, Any] | None = None):
        super().__init__(message)
        self.message = message
        self.entry = entry
        self.offset = offset
        self.detail = dict(detail or {})

    def as_dict(self) -> dict[str, Any]:
        return {
            "error": type(self).__name__,
            "message": self.message,
            "entry": self.entry,
            "offset": self.offset,
            "detail": self.detail,
        }


class NoValidCheckpoint(CheckpointError):
    """Raised when every retained checkpoint failed verification."""


# --------------------------------------------------------------------------- #
# canonical JSON / hashing helpers
# --------------------------------------------------------------------------- #
def _canonical_json(obj: Any) -> bytes:
    return json.dumps(
        obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _copy_json(obj: Any) -> Any:
    return None if obj is None else json.loads(json.dumps(obj))


def _jsonable_rng_state(state: Any) -> Any:
    """Make a numpy bit-generator state JSON-safe (ints stay exact)."""
    if isinstance(state, dict):
        return {str(k): _jsonable_rng_state(v) for k, v in state.items()}
    if isinstance(state, (list, tuple)):
        return [_jsonable_rng_state(v) for v in state]
    if isinstance(state, np.integer):
        return int(state)
    if isinstance(state, np.floating):
        return float(state)
    return state


def _dtype_to_json(dtype: np.dtype) -> dict[str, Any]:
    dtype = np.dtype(dtype)
    if dtype.fields:  # structured / record dtype
        names, formats, offsets = [], [], []
        for name in dtype.names:
            sub, offset = dtype.fields[name][:2]
            names.append(name)
            formats.append(_dtype_to_json(sub))      # recursive: nested struct ok
            offsets.append(int(offset))
        return {"kind": "struct", "names": names, "formats": formats,
                "offsets": offsets, "itemsize": dtype.itemsize}
    if dtype.kind == "O":
        raise TypeError("object dtype arrays cannot be checkpointed bit-exactly")
    return {"kind": "plain", "str": dtype.str, "itemsize": dtype.itemsize}


def _dtype_from_json(spec: Mapping[str, Any]) -> np.dtype:
    if spec["kind"] == "struct":
        return np.dtype({
            "names": list(spec["names"]),
            "formats": [_dtype_from_json(fmt) for fmt in spec["formats"]],
            "offsets": list(spec["offsets"]),
            "itemsize": int(spec["itemsize"]),
        })
    return np.dtype(spec["str"])


def _default_workers() -> int:
    return max(1, min(8, os.cpu_count() or 1))


def buffer_view(array: np.ndarray) -> memoryview:
    """Raw byte view of a C-contiguous array (zero copy, zero-length safe)."""
    if array.size == 0:
        return memoryview(b"")
    view = memoryview(array)
    if view.format != "B" or view.ndim != 1:
        view = view.cast("B")
    return view


def merkle_digest(buffer, workers: int | None = None) -> tuple[str, list[str]]:
    """``(entry_digest, [page_digest_hex, ...])`` over a buffer.

    ``entry_digest`` is the SHA-256 of the concatenated ``BLOCK_SIZE`` page
    digests (2-level Merkle root).  Page digests are hashed in a thread pool
    because ``hashlib`` releases the GIL: on the acceptance host 8 threads take a
    0.5 GiB payload from 161 MB/s (sequential) to ~1 GB/s.
    """
    size = len(buffer)
    block_size = BLOCK_SIZE
    spans = [(i, min(i + block_size, size)) for i in range(0, size, block_size)]
    if not spans:
        spans = [(0, 0)]
    workers = _default_workers() if workers is None else max(1, int(workers))
    view = memoryview(buffer)

    def hash_range(start: int, stop: int) -> list[bytes]:
        return [hashlib.sha256(view[i:min(i + block_size, stop)]).digest()
                for i in range(start, stop, block_size)]

    # group pages into ~1 MiB tasks: one thread-pool submission per page would
    # dominate for small pages (64 KiB) while localisation stays page-granular.
    task_size = max(block_size, 1024 * 1024)
    tasks = [(i, min(i + task_size, size)) for i in range(0, size, task_size)] or [(0, 0)]
    if workers > 1 and len(tasks) > 1:
        with ThreadPoolExecutor(max_workers=workers) as pool:
            groups = list(pool.map(lambda t: hash_range(t[0], t[1]), tasks))
    else:
        groups = [hash_range(t[0], t[1]) for t in tasks]
    raw = [digest for group in groups for digest in group]
    return _sha256(b"".join(raw)), [digest.hex() for digest in raw]


def state_digest(step: int, time: float, meta: Mapping[str, Any],
                 rng_state: Any,
                 entries: Sequence[Mapping[str, Any]]) -> str:
    """Canonical state digest from an entry table (``digest`` = per-entry root).

    Re-uses the per-entry Merkle roots, so a save/load cycle hashes the payload
    exactly once instead of once per layer.
    """
    descriptor = {
        "version": FORMAT_VERSION,
        "step": int(step),
        "time": float(time),
        "meta": dict(meta),
        "rng_state": rng_state,
        "entries": [
            {key: entry[key] for key in ("name", "dtype", "shape", "nbytes", "digest")}
            for entry in entries
        ],
    }
    return _sha256(_canonical_json(descriptor))


def _canonical_array(arr: np.ndarray, name: str) -> np.ndarray:
    """Little-endian, C-contiguous, fixed-dtype copy of ``arr``."""
    arr = np.asarray(arr)
    if arr.dtype.kind == "O":
        raise TypeError(f"field {name!r}: object dtype is not bit-reproducible")
    dtype = np.dtype(arr.dtype)
    if dtype.byteorder not in ("<", "|", "=") and dtype.itemsize > 1:
        dtype = dtype.newbyteorder("<")
    # NB: ``np.ascontiguousarray`` promotes 0-d arrays to shape (1,), which would
    # silently change the field shape; ``np.array(..., order="C")`` keeps it.
    return np.array(arr, dtype=dtype, order="C", copy=True)


# --------------------------------------------------------------------------- #
# state
# --------------------------------------------------------------------------- #
@dataclass
class SolverState:
    """Canonical, hash-stable snapshot of one (coupled) solver state."""

    fields: dict[str, np.ndarray] = dc_field(default_factory=dict)
    step: int = 0
    time: float = 0.0
    meta: dict[str, Any] = dc_field(default_factory=dict)
    rng_state: dict[str, Any] | None = None

    def __post_init__(self) -> None:
        self.step = int(self.step)
        self.time = float(self.time)
        self.meta = dict(self.meta)
        self.fields = {
            str(name): _canonical_array(value, str(name))
            for name, value in self.fields.items()
        }

    @property
    def field_names(self) -> tuple[str, ...]:
        return tuple(sorted(self.fields))

    def arrays(self) -> dict[str, np.ndarray]:
        """Writable copies (safe for the caller to mutate)."""
        return {name: arr.copy() for name, arr in self.fields.items()}

    def copy(self) -> "SolverState":
        return SolverState(fields=self.arrays(), step=self.step, time=self.time,
                           meta=_copy_json(self.meta),
                           rng_state=_copy_json(self.rng_state))

    def array_digest(self, name: str, *, workers: int | None = None) -> str:
        return merkle_digest(buffer_view(self.fields[name]), workers)[0]

    def entry_descriptors(self) -> list[dict[str, Any]]:
        out = []
        for name in self.field_names:
            arr = self.fields[name]
            out.append({
                "name": name,
                "dtype": _dtype_to_json(arr.dtype),
                "shape": list(arr.shape),
                "nbytes": int(arr.nbytes),
                "digest": self.array_digest(name),
            })
        return out

    def descriptor(self) -> dict[str, Any]:
        """Full canonical description used for the state digest."""
        return {
            "version": FORMAT_VERSION,
            "step": self.step,
            "time": self.time,
            "meta": self.meta,
            "rng_state": self.rng_state,
            "entries": self.entry_descriptors(),
        }

    def digest(self) -> str:
        """SHA-256 over the canonical descriptor -> bit-level identity."""
        return state_digest(self.step, self.time, self.meta, self.rng_state,
                            self.entry_descriptors())

    def nbytes(self) -> int:
        return int(sum(arr.nbytes for arr in self.fields.values()))

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, SolverState):
            return NotImplemented
        return self.digest() == other.digest()# --------------------------------------------------------------------------- #
# store: serialize / deserialize / verify
# --------------------------------------------------------------------------- #
@dataclass
class VerifyReport:
    ok: bool
    path: str
    codec: str
    step: int | None
    time: float | None
    digest: str | None
    n_entries: int
    uncompressed_bytes: int
    on_disk_bytes: int
    compression_ratio: float
    entries: list[dict[str, Any]] = dc_field(default_factory=list)
    error: dict[str, Any] | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok, "path": self.path, "codec": self.codec,
            "step": self.step, "time": self.time, "digest": self.digest,
            "n_entries": self.n_entries,
            "uncompressed_bytes": self.uncompressed_bytes,
            "on_disk_bytes": self.on_disk_bytes,
            "compression_ratio": self.compression_ratio,
            "entries": self.entries, "error": self.error,
        }


class CheckpointStore:
    """Deterministic single-file checkpoint codec (no domain semantics).

    Integrity scheme (one pass over the payload, Merkle-style)::

        block_digests  = [sha256(block_i) for each BLOCK_SIZE page]
        entry_digest   = sha256(concat(block_digests))       # 2-level Merkle root
        state_digest   = sha256(canonical JSON descriptor)   # step/time/meta/entries

    Block digests are computed in a thread pool (``hashlib`` releases the GIL), so
    the cost of verification is dominated by memory/device bandwidth instead of
    one long sequential SHA-256 stream.  Because the entry digest is derived from
    the page digests, a single flipped bit is both detected and localised to one
    ``BLOCK_SIZE`` page with a single pass.
    """

    def __init__(self, block_size: int = BLOCK_SIZE, workers: int | None = None):
        # The page size is part of the identity scheme, hence module-wide fixed:
        # a per-store value would make the header digests incomparable.
        if int(block_size) != BLOCK_SIZE:
            raise ValueError(f"block_size is fixed at {BLOCK_SIZE} bytes")
        self.block_size = BLOCK_SIZE
        self.workers = _default_workers() if workers is None else max(1, int(workers))

    # -- integrity primitives ----------------------------------------------- #
    def _digest_buffer(self, buffer) -> tuple[str, list[str]]:
        """Return ``(entry_digest, [page_digest_hex, ...])`` for a buffer."""
        return merkle_digest(buffer, self.workers)

    _view = staticmethod(buffer_view)

    def _encode(self, state: SolverState, codec: str,
                extra_meta: Mapping[str, Any] | None = None
                ) -> tuple[list[memoryview], dict[str, Any]]:
        chunks: list[memoryview] = []
        entries: list[dict[str, Any]] = []
        offset = 0
        for name in state.field_names:
            view = self._view(state.fields[name])
            digest, blocks = self._digest_buffer(view)
            entries.append({
                "name": name,
                "dtype": _dtype_to_json(state.fields[name].dtype),
                "shape": list(state.fields[name].shape),
                "nbytes": len(view),
                "offset": offset,
                "digest": digest,
                "blocks": blocks,
            })
            chunks.append(view)
            offset += len(view)
        total = offset
        header: dict[str, Any] = {
            "format": FORMAT_VERSION,
            "codec": codec,
            "digest_scheme": "sha256-merkle-v1",
            "block_size": self.block_size,
            "step": state.step,
            "time": state.time,
            "meta": state.meta,
            "rng_state": state.rng_state,
            "state_digest": state_digest(state.step, state.time, state.meta,
                                         state.rng_state, entries),
            "uncompressed_bytes": total,
            "entries": entries,
        }
        header["digest"] = self._digest_of(header)
        if extra_meta:
            header["extra"] = dict(extra_meta)
        return chunks, header

    # -- public API --------------------------------------------------------- #
    def save(self, state: SolverState, path: str | os.PathLike[str], *,
             codec: str = "auto",
             extra_meta: Mapping[str, Any] | None = None) -> dict[str, Any]:
        """Serialize ``state`` to ``path`` atomically (tmp file + rename)."""
        chosen = resolve_codec(codec)
        chunks, header = self._encode(state, chosen, extra_meta)
        payload_bytes = sum(len(chunk) for chunk in chunks)
        header_bytes = _canonical_json(header)
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + ".tmp")
        with open(tmp, "wb") as handle:
            handle.write(MAGIC)
            handle.write(len(header_bytes).to_bytes(8, "little"))
            handle.write(header_bytes)
            handle.write(hashlib.sha256(header_bytes).digest())
            if chosen == CODEC_NONE:
                # zero copy: write the array buffers straight to the file
                for chunk in chunks:
                    handle.write(chunk)
            else:
                handle.write(_compress(b"".join(bytes(chunk) for chunk in chunks), chosen))
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
        on_disk = path.stat().st_size
        return {
            "path": str(path),
            "codec": chosen,
            "digest": header["state_digest"],
            "file_digest": header["digest"],
            "step": state.step,
            "time": state.time,
            "uncompressed_bytes": payload_bytes,
            "on_disk_bytes": on_disk,
            "compression_ratio": (payload_bytes / on_disk) if on_disk else 0.0,
            "n_entries": len(header["entries"]),
        }

    def _read(self, path: str | os.PathLike[str]) -> tuple[dict[str, Any], memoryview, int]:
        """Open ``path`` with mmap; return (header, payload view, file size)."""
        path = Path(path)
        if not path.exists():
            raise CheckpointCorruption("checkpoint file is missing",
                                       detail={"path": str(path)})
        size = path.stat().st_size
        with open(path, "rb") as handle:
            head = handle.read(len(MAGIC) + 8)
            if len(head) < len(MAGIC) + 8 or not head.startswith(MAGIC):
                raise CheckpointCorruption(
                    "bad magic / truncated checkpoint header",
                    detail={"path": str(path), "size": size},
                )
            header_len = int.from_bytes(head[len(MAGIC):len(MAGIC) + 8], "little")
            if header_len <= 0 or header_len > size:
                raise CheckpointCorruption(
                    "implausible header length", detail={"header_len": header_len}
                )
            header_bytes = handle.read(header_len)
            stored_hash = handle.read(_HASH_LEN)
            if hashlib.sha256(header_bytes).digest() != stored_hash:
                raise CheckpointCorruption(
                    "header checksum mismatch (metadata corrupted)",
                    detail={"path": str(path)},
                )
            try:
                header = json.loads(header_bytes)
            except Exception as exc:  # noqa: BLE001
                raise CheckpointCorruption(
                    "header is not valid JSON",
                    detail={"path": str(path), "exc": str(exc)},
                ) from exc
        payload_start = len(MAGIC) + 8 + header_len + _HASH_LEN
        return header, payload_start, size

    def verify(self, path: str | os.PathLike[str]) -> VerifyReport:
        """Full self-check; corruption is reported, never raised."""
        path = Path(path)
        on_disk = path.stat().st_size if path.exists() else 0
        try:
            header, payload_start, size = self._read(path)
            expected_total = int(header["uncompressed_bytes"])
            if header["codec"] == CODEC_NONE:
                payload = self._payload_view(path, payload_start, expected_total)
            else:
                with open(path, "rb") as handle:
                    handle.seek(payload_start)
                    body = handle.read()
                payload = _decompress(body, header["codec"], expected_total)
            entries: list[dict[str, Any]] = []
            for entry in header["entries"]:
                start = entry["offset"]
                view = memoryview(payload)[start:start + entry["nbytes"]]
                digest, blocks = self._digest_buffer(view)
                bad_blocks = [index for index, expected in enumerate(entry["blocks"])
                              if index >= len(blocks) or blocks[index] != expected]
                entries.append({
                    "name": entry["name"],
                    "ok": digest == entry["digest"],
                    "expected_digest": entry["digest"],
                    "actual_digest": digest,
                    "bad_blocks": bad_blocks,
                    "first_bad_offset": (
                        entry["offset"] + bad_blocks[0] * self.block_size
                        if bad_blocks else None
                    ),
                })
            digest = self._digest_of(header)
            ok = all(item["ok"] for item in entries) and digest == header["digest"]
            report = VerifyReport(
                ok=ok, path=str(path), codec=header["codec"],
                step=header.get("step"), time=header.get("time"),
                digest=digest, n_entries=len(entries),
                uncompressed_bytes=expected_total,
                on_disk_bytes=on_disk,
                compression_ratio=(expected_total / on_disk) if on_disk else 0.0,
                entries=entries,
            )
            if not ok:
                bad = next((e for e in entries if not e["ok"]), None)
                report.error = {
                    "error": "CheckpointCorruption",
                    "message": "entry digest mismatch",
                    "entry": bad["name"] if bad else None,
                    "offset": bad["first_bad_offset"] if bad else None,
                }
            return report
        except CheckpointCorruption as exc:
            return VerifyReport(ok=False, path=str(path), codec="unknown", step=None,
                                time=None, digest=None, n_entries=0,
                                uncompressed_bytes=0, on_disk_bytes=on_disk,
                                compression_ratio=0.0, error=exc.as_dict())
        except Exception as exc:  # noqa: BLE001  (IO errors -> report)
            return VerifyReport(ok=False, path=str(path), codec="unknown", step=None,
                                time=None, digest=None, n_entries=0,
                                uncompressed_bytes=0, on_disk_bytes=on_disk,
                                compression_ratio=0.0,
                                error={"error": type(exc).__name__, "message": str(exc)})

    @staticmethod
    def _payload_view(path: Path, payload_start: int, expected_total: int) -> memoryview:
        size = path.stat().st_size
        if expected_total < 0 or payload_start + expected_total > size:
            raise CheckpointCorruption(
                "payload truncated",
                detail={"expected_bytes": expected_total,
                        "available_bytes": max(0, size - payload_start)},
            )
        with open(path, "rb") as handle:
            handle.seek(payload_start)
            data = handle.read(expected_total)
        return memoryview(data)

    @staticmethod
    def _digest_of(header: Mapping[str, Any]) -> str:
        descriptor = {
            "version": header["format"],
            "step": header["step"],
            "time": header["time"],
            "meta": header["meta"],
            "rng_state": header.get("rng_state"),
            "entries": [
                {key: entry[key] for key in ("name", "dtype", "shape", "nbytes", "digest")}
                for entry in header["entries"]
            ],
        }
        return _sha256(_canonical_json(descriptor))

    def load(self, path: str | os.PathLike[str], *, verify: bool = True) -> SolverState:
        """Deserialize; raises ``CheckpointCorruption`` on any mismatch."""
        path = Path(path)
        header, payload_start, _size = self._read(path)
        expected_total = int(header["uncompressed_bytes"])
        if header["codec"] == CODEC_NONE:
            payload = self._payload_view(path, payload_start, expected_total)
        else:
            with open(path, "rb") as handle:
                handle.seek(payload_start)
                body = handle.read()
            try:
                payload = _decompress(body, header["codec"], expected_total)
            except CheckpointCorruption:
                raise
            except Exception as exc:  # noqa: BLE001
                raise CheckpointCorruption(
                    "payload decompression failed",
                    detail={"path": str(path), "exc": str(exc)},
                ) from exc
        fields: dict[str, np.ndarray] = {}
        for entry in header["entries"]:
            start = entry["offset"]
            view = memoryview(payload)[start:start + entry["nbytes"]]
            if verify:
                digest, blocks = self._digest_buffer(view)
                if digest != entry["digest"]:
                    bad_offset = None
                    for index, expected in enumerate(entry["blocks"]):
                        actual = blocks[index] if index < len(blocks) else None
                        if actual != expected:
                            bad_offset = start + index * self.block_size
                            break
                    raise CheckpointCorruption(
                        "entry digest mismatch (bit flip) on reload",
                        entry=entry["name"], offset=bad_offset,
                        detail={"expected_digest": entry["digest"],
                                "actual_digest": digest},
                    )
            dtype = _dtype_from_json(entry["dtype"])
            array = np.frombuffer(view, dtype=dtype).reshape(tuple(entry["shape"]))
            fields[entry["name"]] = array.copy()
        state = SolverState(fields=fields, step=header["step"], time=header["time"],
                            meta=header.get("meta") or {},
                            rng_state=header.get("rng_state"))
        if verify:
            actual_digest = state_digest(header["step"], header["time"],
                                         header.get("meta") or {},
                                         header.get("rng_state"),
                                         header["entries"])
            expected_state = header.get("state_digest", header["digest"])
            if actual_digest != expected_state:
                raise CheckpointCorruption(
                    "state digest mismatch after deserialization",
                    detail={"expected_digest": expected_state,
                            "actual_digest": actual_digest},
                )
        return state

    def digest_of(self, path: str | os.PathLike[str]) -> str:
        """In-memory canonical state digest stored in the header (cheap)."""
        header, _start, _size = self._read(path)
        return header.get("state_digest", self._digest_of(header))

    def file_digest_of(self, path: str | os.PathLike[str]) -> str:
        """Digest over the header entry table (detects metadata corruption)."""
        header, _start, _size = self._read(path)
        return self._digest_of(header)

    def header(self, path: str | os.PathLike[str]) -> dict[str, Any]:
        header, _start, _size = self._read(path)
        return header

def flip_bit_in_file(path: str | os.PathLike[str], byte_offset: int,
                     bit: int = 0) -> dict[str, Any]:
    """Flip a single bit in ``path`` (fault injection for corruption tests).

    Returns the affected byte offset and the original/patched byte values.
    """
    path = Path(path)
    data = bytearray(path.read_bytes())
    if not 0 <= bit <= 7:
        raise ValueError("bit must be in 0..7")
    if not 0 <= byte_offset < len(data):
        raise ValueError(f"byte_offset {byte_offset} outside file of {len(data)} bytes")
    before = data[byte_offset]
    data[byte_offset] ^= (1 << bit)
    path.write_bytes(bytes(data))
    return {"path": str(path), "byte_offset": int(byte_offset), "bit": int(bit),
            "byte_before": int(before), "byte_after": int(data[byte_offset])}


# --------------------------------------------------------------------------- #
# exchange bus + coupled runner
# --------------------------------------------------------------------------- #
StepFn = Callable[[Mapping[str, np.ndarray], float, "ExchangeBus"], None]


class ExchangeBus:
    """Coupling channel with explicit commit (time-synchronisation) points.

    During a segment a solver publishes into a *pending* dict; readers keep
    seeing the last *committed* value until :meth:`commit` is called, which is the
    synchronisation point of the coupling loop.  Both dicts are part of the
    checkpoint, so resuming mid-segment is bit-identical.
    """

    def __init__(self, initial: Mapping[str, Any] | None = None):
        self.committed: dict[str, np.float64] = {
            str(k): np.float64(v) for k, v in (initial or {}).items()
        }
        self.pending: dict[str, np.float64] = {}
        self.commits: int = 0

    # -- reader side -------------------------------------------------------- #
    def read(self, key: str, default: float = 0.0) -> np.float64:
        key = str(key)
        if key in self.committed:
            return self.committed[key]
        return np.float64(default)

    def view(self) -> Mapping[str, np.float64]:
        return MappingProxyType(self.committed)

    # -- writer side -------------------------------------------------------- #
    def publish(self, key: str, value: Any) -> None:
        self.pending[str(key)] = np.float64(value)

    def commit(self) -> dict[str, np.float64]:
        """Promote pending values to committed ones (a synchronisation point)."""
        promoted = dict(self.pending)
        self.committed.update(promoted)
        self.pending.clear()
        self.commits += 1
        return promoted

    # -- serialisation ------------------------------------------------------ #
    def to_fields(self) -> dict[str, np.ndarray]:
        out: dict[str, np.ndarray] = {
            f"bus/committed/{k}": np.float64(v) for k, v in self.committed.items()
        }
        out.update({f"bus/pending/{k}": np.float64(v) for k, v in self.pending.items()})
        out["bus/commits"] = np.float64(self.commits)
        return out

    def load_fields(self, fields: Mapping[str, np.ndarray]) -> None:
        self.committed = {
            key.split("/", 2)[2]: np.float64(val)
            for key, val in fields.items()
            if key.startswith("bus/committed/")
        }
        self.pending = {
            key.split("/", 2)[2]: np.float64(val)
            for key, val in fields.items()
            if key.startswith("bus/pending/")
        }
        commits = fields.get("bus/commits")
        self.commits = int(commits) if commits is not None else 0


@dataclass
class CoupledSolver:
    """One participant of a coupled run (initial state + one step kernel)."""

    name: str
    step_fn: StepFn
    fields: Mapping[str, np.ndarray]


class CoupledRunner:
    """Deterministic operator-splitting loop over several solvers.

    Each iteration calls the solvers in their declared order; the exchange bus is
    committed every ``sync_interval`` steps (synchronisation points).  All numeric
    state -- solver arrays, committed + pending bus values, commit counter, step,
    time and the RNG stream position -- is captured by :meth:`state`, so a
    checkpoint/restore cycle reproduces the continuous run bit for bit.
    """

    def __init__(self, solvers: Sequence[CoupledSolver], *, dt: float = 1.0,
                 sync_interval: int = 10, bus: ExchangeBus | None = None,
                 ledger: "EnergyLedger | None" = None,
                 rng: np.random.Generator | None = None,
                 meta: Mapping[str, Any] | None = None):
        if not solvers:
            raise ValueError("at least one solver is required")
        if sync_interval < 1:
            raise ValueError("sync_interval must be >= 1")
        names = [s.name for s in solvers]
        if len(set(names)) != len(names):
            raise ValueError(f"duplicate solver names: {names}")
        self.solvers = list(solvers)
        self.dt = float(dt)
        self.sync_interval = int(sync_interval)
        self.bus = bus if bus is not None else ExchangeBus()
        self.ledger = ledger
        self.rng = rng
        self.meta = dict(meta or {})
        self.step = 0
        self.sync_log: list[int] = []
        self._state: dict[str, dict[str, np.ndarray]] = {
            solver.name: {
                str(key): _canonical_array(val, f"{solver.name}/{key}")
                for key, val in solver.fields.items()
            }
            for solver in self.solvers
        }

    # -- introspection ------------------------------------------------------ #
    @property
    def time(self) -> float:
        return self.step * self.dt

    def is_sync_point(self, step: int | None = None) -> bool:
        step = self.step if step is None else int(step)
        return step % self.sync_interval == 0

    def next_sync_step(self) -> int:
        return (self.step // self.sync_interval + 1) * self.sync_interval

    def fields(self, solver_name: str) -> Mapping[str, np.ndarray]:
        if solver_name not in self._state:
            raise KeyError(solver_name)
        return MappingProxyType(self._state[solver_name])

    # -- state I/O ---------------------------------------------------------- #
    def state(self) -> SolverState:
        fields: dict[str, np.ndarray] = {}
        for name, arrays in self._state.items():
            for key, val in arrays.items():
                fields[f"{name}/{key}"] = val.copy()
        fields.update(self.bus.to_fields())
        rng_state = None
        if self.rng is not None:
            rng_state = _jsonable_rng_state(self.rng.bit_generator.state)
        meta = dict(self.meta)
        meta.setdefault("sync_interval", self.sync_interval)
        meta["sync_points"] = len(self.sync_log)
        return SolverState(fields=fields, step=self.step, time=self.time,
                           meta=meta, rng_state=rng_state)

    def load_state(self, state: SolverState) -> None:
        prefix_map: dict[str, dict[str, np.ndarray]] = {name: {} for name in self._state}
        for key, value in state.fields.items():
            if key.startswith("bus/"):
                continue
            name, _, rest = key.partition("/")
            if name not in prefix_map:
                raise KeyError(f"unknown solver {name!r} in checkpoint")
            prefix_map[name][rest] = value.copy()
        for name, arrays in prefix_map.items():
            expected = set(self._state[name])
            if set(arrays) != expected:
                raise KeyError(
                    f"solver {name!r} field set mismatch: "
                    f"missing={sorted(expected - set(arrays))}, "
                    f"extra={sorted(set(arrays) - expected)}"
                )
            self._state[name] = arrays
        self.bus.load_fields(state.fields)
        self.step = int(state.step)
        if self.rng is not None and state.rng_state is not None:
            self.rng.bit_generator.state = state.rng_state
        sync_points = state.meta.get("sync_points")
        if sync_points is not None:
            count = int(sync_points)
            self.sync_log = [self.sync_interval * (i + 1) for i in range(count)]

    def restore(self, path: str | os.PathLike[str],
                store: CheckpointStore | None = None) -> SolverState:
        store = store or CheckpointStore()
        state = store.load(path)
        self.load_state(state)
        return state

    def checkpoint(self, path: str | os.PathLike[str],
                   store: CheckpointStore | None = None, *,
                   codec: str = "auto") -> dict[str, Any]:
        store = store or CheckpointStore()
        return store.save(self.state(), path, codec=codec)

    def ledger_totals(self) -> dict[str, float]:
        if self.ledger is None:
            return {}
        return self.ledger.breakdown(self.state())

    # -- stepping ----------------------------------------------------------- #
    def step_once(self) -> int:
        for solver in self.solvers:
            solver.step_fn(MappingProxyType(self._state[solver.name]), self.dt, self.bus)
        self.step += 1
        if self.is_sync_point(self.step):
            self.bus.commit()
            self.sync_log.append(self.step)
        return self.step

    def advance(self, n_steps: int, *,
                on_step: Callable[["CoupledRunner"], None] | None = None) -> list[int]:
        """Advance ``n_steps`` steps; returns the synchronisation points hit."""
        if n_steps < 0:
            raise ValueError("n_steps must be >= 0")
        before = len(self.sync_log)
        for _ in range(int(n_steps)):
            self.step_once()
            if on_step is not None:
                on_step(self)
        return self.sync_log[before:]


def run_restart_equivalence(runner_factory: Callable[[], CoupledRunner],
                            *, n_steps: int = 1000, restart_at: int = 500,
                            store: CheckpointStore | None = None,
                            tmpdir: str | os.PathLike[str] | None = None,
                            codec: str = "auto") -> dict[str, Any]:
    """Continuous run vs. checkpoint/restart run -> bit-level equivalence verdict.

    ``runner_factory`` must return a *fresh* runner (nothing shared with earlier
    calls, including any RNG generator it owns).  The returned dict contains both
    digests, the verdict ``bit_identical``, per-field equality and the checkpoint
    IO metadata, i.e. everything the acceptance criterion needs without a single
    hand-copied number.
    """
    import shutil
    import tempfile

    if restart_at <= 0 or restart_at >= n_steps:
        raise ValueError("restart_at must be in (0, n_steps)")
    store = store or CheckpointStore()
    own_tmp = tmpdir is None
    tmpdir = Path(tempfile.mkdtemp(prefix="ckpt_restart_")) if own_tmp else Path(tmpdir)
    try:
        runner_a = runner_factory()
        runner_a.advance(n_steps)
        state_a = runner_a.state()

        runner_b = runner_factory()
        runner_b.advance(restart_at)
        ckpt = tmpdir / "restart.ckpt"
        io_meta = runner_b.checkpoint(ckpt, store, codec=codec)

        runner_c = runner_factory()     # fresh object: nothing shared but the file
        runner_c.restore(ckpt, store)
        runner_c.advance(n_steps - restart_at)
        state_c = runner_c.state()

        digest_a, digest_c = state_a.digest(), state_c.digest()
        return {
            "n_steps": int(n_steps),
            "restart_at": int(restart_at),
            "sync_interval": runner_a.sync_interval,
            "dt": runner_a.dt,
            "restart_at_sync_point": bool(restart_at % runner_a.sync_interval == 0),
            "digest_continuous": digest_a,
            "digest_restarted": digest_c,
            "bit_identical": digest_a == digest_c,
            "restored_step": runner_c.step,
            "restored_time": runner_c.time,
            "checkpoint": io_meta,
            "field_equal": {
                name: bool(np.array_equal(state_a.fields[name], state_c.fields[name]))
                for name in state_a.field_names
            },
        }
    finally:
        if own_tmp:
            shutil.rmtree(tmpdir, ignore_errors=True)


# --------------------------------------------------------------------------- #
# energy ledger
# --------------------------------------------------------------------------- #
class EnergyLedger:
    """Linear functional over state fields used as an energy bookkeeping gate.

    A term is ``sum``/``sumsq``/``abs_sum`` of one field times a coefficient.
    Terms are registered by the caller (domain semantics stay outside).
    """

    def __init__(self, tolerance: float = 1e-10):
        self.tolerance = float(tolerance)
        self.terms: list[dict[str, Any]] = []

    def register(self, term: str, field: str, *, coefficient: float = 1.0,
                 reduce: str = "sumsq") -> "EnergyLedger":
        if reduce not in ("sum", "sumsq", "abs_sum"):
            raise ValueError(f"unknown reduce {reduce!r}")
        self.terms.append({"term": str(term), "field": str(field),
                           "coefficient": float(coefficient), "reduce": reduce})
        return self

    def breakdown(self, state: SolverState) -> dict[str, float]:
        out: dict[str, float] = {}
        for spec in self.terms:
            if spec["field"] not in state.fields:
                raise KeyError(f"ledger field {spec['field']!r} missing from state")
            values = np.asarray(state.fields[spec["field"]], dtype=np.float64)
            if spec["reduce"] == "sum":
                raw = float(np.sum(values))
            elif spec["reduce"] == "abs_sum":
                raw = float(np.sum(np.abs(values)))
            else:
                raw = float(np.sum(values * values))
            out[spec["term"]] = spec["coefficient"] * raw
        out["E_total"] = float(sum(out.values()))
        return out

    def total(self, state: SolverState) -> float:
        return self.breakdown(state)["E_total"]

    def residual(self, a: SolverState, b: SolverState,
                 tolerance: float | None = None) -> dict[str, Any]:
        """Absolute / relative difference of ``E_total`` between two states."""
        tol = self.tolerance if tolerance is None else float(tolerance)
        ea, eb = self.total(a), self.total(b)
        abs_diff = abs(ea - eb)
        scale = max(abs(ea), abs(eb), 1e-300)
        rel = abs_diff / scale
        return {"E_before": ea, "E_after": eb, "abs_residual": abs_diff,
                "rel_residual": rel, "tolerance": tol,
                "ok": bool(abs_diff <= tol)}

    def drift(self, reference_total: float, state: SolverState,
              tolerance: float | None = None) -> dict[str, Any]:
        """Relative drift of ``E_total`` w.r.t. a reference value."""
        tol = 1e-3 if tolerance is None else float(tolerance)
        current = self.total(state)
        scale = max(abs(reference_total), 1e-300)
        rel = abs(current - reference_total) / scale
        return {"E_reference": float(reference_total), "E_current": current,
                "abs_drift": abs(current - reference_total), "rel_drift": rel,
                "tolerance": tol, "ok": bool(rel <= tol)}


# --------------------------------------------------------------------------- #
# time synchronisation
# --------------------------------------------------------------------------- #
class TimeSynchronizer:
    """Synchronisation-point bookkeeping for coupled replicas."""

    def __init__(self, sync_interval: int, time_atol: float = 1e-12):
        if sync_interval < 1:
            raise ValueError("sync_interval must be >= 1")
        self.sync_interval = int(sync_interval)
        self.time_atol = float(time_atol)

    def is_sync_point(self, step: int) -> bool:
        return int(step) % self.sync_interval == 0

    def next_sync_step(self, step: int) -> int:
        return (int(step) // self.sync_interval + 1) * self.sync_interval

    def compare(self, a: SolverState, b: SolverState) -> dict[str, Any]:
        """Are two checkpoints *time-synchronised* replicas of the same run?"""
        time_diff = abs(a.time - b.time)
        a_sync = self.is_sync_point(a.step)
        b_sync = self.is_sync_point(b.step)
        digest_equal = a.digest() == b.digest()
        step_equal = a.step == b.step
        synchronized = bool(a_sync and b_sync and step_equal and digest_equal)
        if synchronized:
            reason = "sync_point_match"
        elif not (a_sync and b_sync):
            reason = "not_sync_point"
        elif not step_equal:
            reason = "step_mismatch"
        elif not digest_equal:
            reason = "digest_mismatch"
        else:  # pragma: no cover - defensive
            reason = "unknown"
        return {
            "step_a": a.step, "step_b": b.step,
            "time_a": a.time, "time_b": b.time, "time_diff": time_diff,
            "time_tolerance": self.time_atol,
            "step_equal": step_equal,
            "a_is_sync_point": a_sync, "b_is_sync_point": b_sync,
            "digest_equal": digest_equal,
            "digest_a": a.digest(), "digest_b": b.digest(),
            "synchronized": synchronized, "reason": reason,
        }


# --------------------------------------------------------------------------- #
# checkpoint manager: retention + rollback
# --------------------------------------------------------------------------- #
class CheckpointManager:
    """Directory of retained checkpoints with verification and rollback."""

    SUFFIX = ".ckpt"

    def __init__(self, root: str | os.PathLike[str], *, keep: int = 3,
                 codec: str = "auto", store: CheckpointStore | None = None):
        if keep < 1:
            raise ValueError("keep must be >= 1")
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.keep = int(keep)
        self.codec = codec
        self.store = store or CheckpointStore()
        self.rollback_history: list[dict[str, Any]] = []

    # -- paths -------------------------------------------------------------- #
    def paths(self) -> list[Path]:
        return sorted(self.root.glob(f"*{self.SUFFIX}"),
                      key=lambda p: (p.stat().st_mtime_ns, p.name))

    def _name_for(self, state: SolverState) -> Path:
        candidate = self.root / f"ckpt_{state.step:012d}{self.SUFFIX}"
        if not candidate.exists():
            return candidate
        index = 1
        while True:
            candidate = self.root / f"ckpt_{state.step:012d}_{index:03d}{self.SUFFIX}"
            if not candidate.exists():
                return candidate
            index += 1

    # -- save / prune ------------------------------------------------------- #
    def save(self, state: SolverState, *, codec: str | None = None) -> dict[str, Any]:
        path = self._name_for(state)
        meta = self.store.save(state, path, codec=codec or self.codec)
        self.prune()
        return meta

    def prune(self) -> list[str]:
        removed: list[str] = []
        paths = self.paths()
        if len(paths) > self.keep:
            for path in paths[:-self.keep]:
                path.unlink(missing_ok=True)
                removed.append(str(path))
        return removed

    # -- read / rollback ---------------------------------------------------- #
    def latest(self) -> Path:
        paths = self.paths()
        if not paths:
            raise NoValidCheckpoint(f"no checkpoint in {self.root}")
        return paths[-1]

    def load_latest(self, *, max_rollback: int | None = None
                    ) -> tuple[SolverState, dict[str, Any]]:
        """Newest *valid* checkpoint, rolling back past corrupt files."""
        paths = list(reversed(self.paths()))
        limit = len(paths) if max_rollback is None else max_rollback + 1
        skipped: list[dict[str, Any]] = []
        for path in paths[:limit]:
            report = self.store.verify(path)
            if report.ok:
                state = self.store.load(path, verify=False)
                info = {
                    "path": str(path),
                    "digest": report.digest,
                    "step": state.step,
                    "time": state.time,
                    "rolled_back": bool(skipped),
                    "skipped": skipped,
                    "attempts": len(skipped) + 1,
                }
                if skipped:
                    self.rollback_history.append(info)
                return state, info
            skipped.append({"path": str(path), "error": report.error})
        raise NoValidCheckpoint(
            f"all {len(skipped)} retained checkpoints failed verification: {skipped}"
        )


# --------------------------------------------------------------------------- #
# IO throughput probe
# --------------------------------------------------------------------------- #
def measure_io_throughput(state: SolverState, directory: str | os.PathLike[str], *,
                          codec: str = "none", repeats: int = 1,
                          store: CheckpointStore | None = None,
                          keep_file: bool = False) -> dict[str, Any]:
    """Write+read one checkpoint and report throughput of the whole operations.

    ``write_s`` / ``read_s`` are wall-clock times for serialisation + fsync +
    rename, respectively read + verification + deserialisation.
    """
    store = store or CheckpointStore()
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "io_probe.ckpt"
    reference_digest = state.digest()
    write_times: list[float] = []
    read_times: list[float] = []
    meta_last: dict[str, Any] = {}
    restored_digest = None
    try:
        for _ in range(max(1, int(repeats))):
            start = time.perf_counter()
            meta_last = store.save(state, path, codec=codec)
            write_times.append(time.perf_counter() - start)
            start = time.perf_counter()
            restored = store.load(path)
            read_times.append(time.perf_counter() - start)
            restored_digest = restored.digest()
        best_write = min(write_times)
        best_read = min(read_times)
        mib = 1024.0 * 1024.0
        return {
            "path": str(path),
            "codec": meta_last.get("codec"),
            "uncompressed_bytes": int(state.nbytes()),
            "on_disk_bytes": int(meta_last.get("on_disk_bytes", 0)),
            "compression_ratio": float(meta_last.get("compression_ratio", 0.0)),
            "write_s": best_write,
            "read_s": best_read,
            "write_MBps": (state.nbytes() / mib) / best_write if best_write > 0 else None,
            "read_MBps": (state.nbytes() / mib) / best_read if best_read > 0 else None,
            "write_MBps_on_disk": ((meta_last.get("on_disk_bytes", 0) / mib) / best_write
                                   if best_write > 0 else None),
            "digest_before": reference_digest,
            "digest_after": restored_digest,
            "lossless": restored_digest == reference_digest,
            "repeats": int(repeats),
            "write_times_s": write_times,
            "read_times_s": read_times,
        }
    finally:
        if not keep_file:
            path.unlink(missing_ok=True)


def extrapolate_to(state_nbytes: int, write_s: float, read_s: float,
                   target_gb: float = 100.0) -> dict[str, Any]:
    """Linear extrapolation of the measured IO rate to a target snapshot size."""
    gb = 1024.0 ** 3
    target_bytes = target_gb * gb
    write_rate = state_nbytes / write_s if write_s > 0 else None
    read_rate = state_nbytes / read_s if read_s > 0 else None
    projected_write = (target_bytes / write_rate) if write_rate else None
    projected_read = (target_bytes / read_rate) if read_rate else None
    return {
        "target_gb": target_gb,
        "target_bytes": int(target_bytes),
        "measured_bytes": int(state_nbytes),
        "write_rate_GBps": (write_rate / gb) if write_rate else None,
        "read_rate_GBps": (read_rate / gb) if read_rate else None,
        "projected_write_s": projected_write,
        "projected_read_s": projected_read,
        "projected_under_60s": bool(
            projected_write is not None and projected_read is not None
            and max(projected_write, projected_read) < 60.0
        ),
    }


__all__ = [
    "MAGIC", "FORMAT_VERSION", "BLOCK_SIZE", "CODEC_NONE", "CODEC_ZSTD", "CODEC_LZMA",
    "available_codecs", "resolve_codec",
    "CheckpointError", "CheckpointCorruption", "NoValidCheckpoint",
    "SolverState", "VerifyReport", "CheckpointStore", "flip_bit_in_file",
    "ExchangeBus", "CoupledSolver", "CoupledRunner", "run_restart_equivalence",
    "EnergyLedger", "TimeSynchronizer", "CheckpointManager",
    "measure_io_throughput", "extrapolate_to",
]