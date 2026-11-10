"""Bit-level checkpoint store for coupled multi-solver state.

Guarantees (each one is covered by a test in ``tests/test_multisolver_ckpt_*.py``):

* **bit level** -- every array is digested as ``sha256(dtype|shape|bytes)``; the
  restart proof is *digest equality*, never a tolerance.
* **chunk localisation** -- each array's bytes are additionally digested in
  fixed-size chunks, so a corrupted payload is reported as
  ``array 'f' chunk 3 of 12`` instead of just "file bad".
* **compressed but lossless** -- zstd (or ``raw`` for the control arm); the
  codec identity and level travel inside the manifest, and the decompressed
  bytes are re-digested, so compression can never mask a change.
* **atomic** -- payloads and manifest are written inside ``.tmp-<step>-<uuid>``,
  ``fsync``-ed, then the directory is ``os.replace``-d into place.  A crash
  mid-write leaves a temporary directory, never a half checkpoint under the
  canonical name.
* **chained** -- every manifest carries ``prev_digest``/``prev_step``, so an
  edited, reordered or deleted checkpoint in the middle of the chain is
  detectable (append-only audit trail).
* **fail loud** -- any inconsistency raises
  :class:`~tools.multisolver_ckpt.errors.CheckpointCorruption` /
  :class:`CheckpointUnreadable`.  A silent fall-back to a stale state would
  corrupt the resume guarantee, which is worse than a crash.

Layout::

    <root>/ckpt-000000500/manifest.json
    <root>/ckpt-000000500/<solver>.bin.zst
    <root>/restart_audit.jsonl          (written by rollback.RestartSupervisor)

Domain neutrality: the store moves ``StateBundle`` objects; it never inspects
what an array means.
"""

from __future__ import annotations

import json
import os
import shutil
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

import numpy as np

from .codec import Codec, codec_from_description, get_codec
from .energy import EnergyLedger, EnergyReading, GateReport
from .errors import (
    CheckpointCorruption,
    CheckpointLayoutError,
    CheckpointUnreadable,
    CodecError,
    SchemaMismatch,
)
from .protocol import (
    DIGEST_CHUNK_BYTES,
    SCHEMA,
    SolverSnapshot,
    StateBundle,
    array_chunk_digests,
    canonical_json,
    canonical_scalars,
    compose_state_digest,
    sha256_bytes,
    sha256_parallel,
)
from .sync import CouplingTimeGrid, SyncGuard, SyncPolicy, SyncReport, sync_manifest_section

MANIFEST_NAME = "manifest.json"
CKPT_PREFIX = "ckpt-"
DEFAULT_CHUNK_BYTES = DIGEST_CHUNK_BYTES


@dataclass
class LoadedCheckpoint:
    """A verified checkpoint: verified bytes + metadata + reconstruction cost."""

    step: int
    bundle: StateBundle
    manifest: Dict[str, Any]
    load_seconds: float = 0.0
    decompress_seconds: float = 0.0
    verify_seconds: float = 0.0
    energy: Optional[EnergyReading] = None

    @property
    def digest(self) -> str:
        return self.bundle.digest

    @property
    def sync(self) -> Dict[str, Any]:
        return dict(self.manifest.get("sync") or {})


@dataclass
class SaveResult:
    step: int
    manifest: Dict[str, Any]
    state_digest: str
    manifest_digest: str
    raw_bytes: int
    stored_bytes: int
    save_seconds: float
    energy: Optional[EnergyReading] = None

    @property
    def compression_ratio(self) -> float:
        return (self.stored_bytes / self.raw_bytes) if self.raw_bytes else 1.0


class CheckpointStore:
    """Append-only, bit-verified, zstd-compressed checkpoint store."""

    def __init__(self, root: str | os.PathLike[str], *, codec: str = "zstd", level: int = 3,
                 threads: int = 4, chunk_bytes: int = DEFAULT_CHUNK_BYTES,
                 grid: CouplingTimeGrid | None = None,
                 sync_policy: SyncPolicy | str = SyncPolicy.STRICT,
                 ledger: EnergyLedger | None = None,
                 max_workers: int = 4, fsync: bool = True) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.codec = get_codec(codec, level=level, threads=threads)
        self.chunk_bytes = int(chunk_bytes)
        if self.chunk_bytes < 1:
            raise CheckpointLayoutError("chunk_bytes must be >= 1")
        self.grid = grid or CouplingTimeGrid()
        self.guard = SyncGuard(self.grid, sync_policy)
        self.ledger = ledger
        self.max_workers = max(1, int(max_workers))
        self.fsync = bool(fsync)
        self.last_save: Optional[SaveResult] = None
        self.last_load: Optional[LoadedCheckpoint] = None

    # ── paths ──────────────────────────────────────────────────────────────
    def dir_of(self, step: int) -> Path:
        return self.root / f"{CKPT_PREFIX}{int(step):012d}"

    def steps(self) -> List[int]:
        out: List[int] = []
        for child in self.root.iterdir():
            if not child.is_dir() or not child.name.startswith(CKPT_PREFIX):
                continue
            try:
                out.append(int(child.name[len(CKPT_PREFIX):]))
            except ValueError:
                continue
        return sorted(out)

    def latest_step(self) -> Optional[int]:
        steps = self.steps()
        return steps[-1] if steps else None

    def has(self, step: int) -> bool:
        return (self.dir_of(step) / MANIFEST_NAME).is_file()

    # ── save ───────────────────────────────────────────────────────────────
    def save(self, step: int, bundle: StateBundle, *, label: str = "",
             extra: Optional[Mapping[str, Any]] = None,
             allow_overwrite: bool = True) -> SaveResult:
        step = int(step)
        if step < 0:
            raise CheckpointLayoutError(f"step must be >= 0, got {step}")
        if int(bundle.step) != step:
            raise CheckpointLayoutError(
                f"bundle.step={bundle.step} does not match the requested checkpoint step={step}"
            )
        started = time.perf_counter()

        sync_report: SyncReport = self.guard.check_save(bundle)
        energy = self.ledger.measure(bundle) if self.ledger is not None else None

        final_dir = self.dir_of(step)
        if final_dir.exists() and not allow_overwrite:
            raise CheckpointLayoutError(f"checkpoint {step} already exists")
        tmp_dir = self.root / f".tmp-{step:012d}-{os.getpid()}-{uuid.uuid4().hex[:8]}"
        tmp_dir.mkdir(parents=True, exist_ok=False)
        try:
            metas = self._write_payloads(tmp_dir, bundle)
            raw_bytes = sum(int(m["payload"]["raw_bytes"]) for m in metas.values())
            stored_bytes = sum(int(m["payload"]["stored_bytes"]) for m in metas.values())
            prev_step, prev_digest = self._previous(step)
            manifest: Dict[str, Any] = {
                "schema": SCHEMA,
                "step": step,
                "label": str(label),
                "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "codec": self.codec.describe(),
                "chunk_bytes": self.chunk_bytes,
                "grid": {"dt_master_hex": self.grid.dt_master.hex(), "n_sub": self.grid.n_sub,
                         "tolerance": self.grid.tolerance, "digest": self.grid.digest()},
                "sync": sync_manifest_section(sync_report, self.grid),
                "energy": energy.to_manifest() if energy is not None else None,
                "solvers": metas,
                "array_order": {name: sorted(meta["arrays"]) for name, meta in metas.items()},
                "state_digest": bundle.digest,
                "raw_bytes": raw_bytes,
                "stored_bytes": stored_bytes,
                "compression_ratio": (stored_bytes / raw_bytes) if raw_bytes else 1.0,
                "prev_step": prev_step,
                "prev_digest": prev_digest,
                "extra": dict(extra or {}),
            }
            manifest["digest"] = self._manifest_digest(manifest)
            self._write_json(tmp_dir / MANIFEST_NAME, manifest)
            self._fsync_dir(tmp_dir)
            if final_dir.exists():
                shutil.rmtree(final_dir)
            os.replace(tmp_dir, final_dir)
            self._fsync_dir(self.root)
        except Exception:
            shutil.rmtree(tmp_dir, ignore_errors=True)
            raise

        elapsed = time.perf_counter() - started
        result = SaveResult(
            step=step, manifest=manifest, state_digest=manifest["state_digest"],
            manifest_digest=manifest["digest"], raw_bytes=raw_bytes,
            stored_bytes=stored_bytes, save_seconds=elapsed, energy=energy,
        )
        self.last_save = result
        return result

    def _previous(self, step: int) -> Tuple[Optional[int], Optional[str]]:
        prior = [s for s in self.steps() if s < step]
        if not prior:
            return None, None
        prev_step = prior[-1]
        try:
            prev_digest = self.manifest_of(prev_step).get("digest")
        except CheckpointUnreadable:
            prev_digest = None
        return prev_step, prev_digest

    def _write_payloads(self, tmp_dir: Path, bundle: StateBundle) -> Dict[str, Dict[str, Any]]:
        def _one(name: str) -> Tuple[str, Dict[str, Any]]:
            return name, self._write_payload(tmp_dir, bundle.solvers[name])

        names = list(bundle.names())
        with ThreadPoolExecutor(max_workers=min(self.max_workers, len(names))) as ex:
            return dict(ex.map(_one, names))

    def _write_payload(self, tmp_dir: Path, snap: SolverSnapshot) -> Dict[str, Any]:
        arrays_meta: Dict[str, Any] = {}
        array_digests: Dict[str, str] = {}
        offset = 0
        total = sum(int(snap.arrays[k].nbytes) for k in snap.arrays)
        raw = bytearray(total)
        for name in sorted(snap.arrays):
            arr = np.ascontiguousarray(snap.arrays[name])
            nbytes = int(arr.nbytes)
            raw[offset:offset + nbytes] = memoryview(arr).cast("B")
            # one hashing pass yields both the bit-level array digest and the
            # per-chunk digests used to localise a corruption later.
            digest, chunks = array_chunk_digests(arr, chunk_bytes=self.chunk_bytes)
            array_digests[name] = digest
            arrays_meta[name] = {
                "dtype": arr.dtype.str,
                "shape": list(arr.shape),
                "nbytes": nbytes,
                "offset": int(offset),
                "sha256": digest,
                "chunks": chunks,
            }
            offset += nbytes
        raw_bytes = bytes(raw)
        stored = self.codec.compress(raw_bytes)
        fname = f"{snap.name}.bin{self.codec.suffix}"
        path = tmp_dir / fname
        with open(path, "wb") as fh:
            fh.write(stored)
            fh.flush()
            if self.fsync:
                os.fsync(fh.fileno())
        solver_digest = compose_state_digest(
            array_digests, snap.scalars, extra={"phase": snap.phase, "time": float(snap.time).hex()}
        )
        snap.cache_digest(solver_digest)
        return {
            "payload": {
                "file": fname,
                "codec": self.codec.describe(),
                "raw_bytes": len(raw_bytes),
                "stored_bytes": len(stored),
                "sha256": sha256_parallel(stored, chunk_bytes=self.chunk_bytes, header=b"payload"),
            },
            "arrays": arrays_meta,
            "scalars": canonical_scalars(snap.scalars),
            "phase": int(snap.phase),
            "time_hex": float(snap.time).hex(),
            "digest": solver_digest,
        }

    # ── read ───────────────────────────────────────────────────────────────
    def manifest_of(self, step: int) -> Dict[str, Any]:
        path = self.dir_of(int(step)) / MANIFEST_NAME
        try:
            manifest = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError as exc:
            raise CheckpointUnreadable(f"manifest missing for step {step}") from exc
        except (OSError, json.JSONDecodeError) as exc:
            raise CheckpointUnreadable(f"manifest unreadable for step {step}: {exc}") from exc
        if manifest.get("schema") != SCHEMA:
            raise SchemaMismatch(
                f"checkpoint {step} schema {manifest.get('schema')!r} != {SCHEMA!r}"
            )
        stored = manifest.get("digest")
        recomputed = self._manifest_digest(manifest)
        if stored != recomputed:
            raise CheckpointCorruption(
                f"manifest digest mismatch at step {step}: stored={stored} recomputed={recomputed}"
            )
        return manifest

    @staticmethod
    def _manifest_digest(manifest: Mapping[str, Any]) -> str:
        body = {k: v for k, v in manifest.items() if k != "digest"}
        return sha256_bytes(canonical_json(body).encode("ascii"))

    def load(self, step: Optional[int] = None, *, verify: bool = True,
             verify_sync: bool = True, verify_energy: bool = True) -> LoadedCheckpoint:
        started = time.perf_counter()
        if step is None:
            step = self.latest_step()
            if step is None:
                raise CheckpointUnreadable(f"no checkpoint available under {self.root}")
        step = int(step)
        manifest = self.manifest_of(step)
        if verify_sync:
            self.guard.check_load(manifest.get("sync") or {})

        base = self.dir_of(step)
        names = sorted(manifest["solvers"])
        decompress_seconds = 0.0

        def _one(name: str) -> Tuple[str, SolverSnapshot, float]:
            return (name, *self._read_payload(
                base, name, manifest["solvers"][name], verify=verify, step=step))

        results: List[Tuple[str, SolverSnapshot, float]] = []
        with ThreadPoolExecutor(max_workers=min(self.max_workers, len(names))) as ex:
            results = list(ex.map(_one, names))
        snaps = {name: snap for name, snap, _ in results}
        decompress_seconds = sum(dt for _, _, dt in results)

        bundle = StateBundle(step=step, solvers=snaps)
        if verify and bundle.digest != manifest.get("state_digest"):
            raise CheckpointCorruption(
                f"group state digest mismatch at step {step}: "
                f"manifest={manifest.get('state_digest')} reconstructed={bundle.digest}"
            )
        energy: Optional[EnergyReading] = None
        if self.ledger is not None and verify_energy and manifest.get("energy"):
            recorded = EnergyReading.from_manifest(manifest["energy"])
            current = self.ledger.measure(bundle)
            gate: GateReport = self.ledger.check_boundary(recorded, current)
            energy = current
            if not gate.ok:
                raise CheckpointCorruption(
                    f"energy ledger disagrees after restore at step {step}: {gate.detail}"
                )
        elif self.ledger is not None:
            energy = self.ledger.measure(bundle)

        loaded = LoadedCheckpoint(
            step=step, bundle=bundle, manifest=manifest,
            load_seconds=time.perf_counter() - started,
            decompress_seconds=decompress_seconds,
            verify_seconds=0.0,
            energy=energy,
        )
        self.last_load = loaded
        return loaded

    def _read_payload(self, base: Path, name: str, meta: Mapping[str, Any], *, verify: bool,
                      step: Optional[int] = None) -> Tuple[SolverSnapshot, float]:
        payload = dict(meta["payload"])
        path = base / str(payload["file"])
        try:
            stored = path.read_bytes()
        except FileNotFoundError as exc:
            raise CheckpointUnreadable(f"payload '{path.name}' missing for solver {name!r}") from exc
        except OSError as exc:
            raise CheckpointUnreadable(f"payload '{path.name}' unreadable: {exc}") from exc
        problems: List[str] = []
        if verify:
            actual = sha256_parallel(stored, chunk_bytes=self.chunk_bytes, header=b"payload")
            if actual != payload["sha256"]:
                # keep going: array/chunk verification below localises the damage
                problems.append(
                    f"payload digest mismatch for solver {name!r} at step {step}: "
                    f"stored={payload['sha256']} actual={actual}"
                )
        try:
            codec: Codec = codec_from_description(payload.get("codec") or {})
            t0 = time.perf_counter()
            raw = codec.decompress(stored)
            decompress_seconds = time.perf_counter() - t0
        except CodecError as exc:
            raise CheckpointUnreadable(f"payload '{path.name}' not decodable: {exc}") from exc
        if len(raw) != int(payload["raw_bytes"]):
            raise CheckpointCorruption(
                f"payload size mismatch for solver {name!r}: expected {payload['raw_bytes']} "
                f"bytes, got {len(raw)}"
            )


        arrays: Dict[str, np.ndarray] = {}
        recomputed_digests: Dict[str, str] = {}
        for array_name in sorted(meta["arrays"]):
            a_meta = meta["arrays"][array_name]
            dtype = np.dtype(a_meta["dtype"])
            shape = tuple(int(x) for x in a_meta["shape"])
            nbytes = int(a_meta["nbytes"])
            start = int(a_meta["offset"])
            buf = raw[start:start + nbytes]
            if len(buf) != nbytes:
                raise CheckpointCorruption(
                    f"array {name}/{array_name} truncated: need {nbytes} bytes at offset {start}, "
                    f"got {len(buf)}"
                )
            # read-only view; SolverSnapshot takes the single owning copy
            arr = np.frombuffer(buf, dtype=dtype).reshape(shape)
            digest, chunks = array_chunk_digests(arr, chunk_bytes=self.chunk_bytes)
            recomputed_digests[array_name] = digest
            if verify and digest != a_meta["sha256"]:
                bad = self._bad_chunk(a_meta, chunks)
                problems.append(
                    f"array digest mismatch for {name}/{array_name}: expected "
                    f"{a_meta['sha256']} got {digest}" + (f"; {bad}" if bad else "")
                )
            arrays[array_name] = arr

        if problems:
            raise CheckpointCorruption(
                "checkpoint payload is corrupt: " + " | ".join(problems)
                + " (a one-bit flip in the stored bytes is the usual cause)"
            )
        extra = sorted(set(arrays) - set(meta["arrays"]))
        if extra:  # pragma: no cover - defensive
            raise CheckpointCorruption(f"unexpected arrays {extra} for solver {name!r}")
        scalars = _scalars_from_canonical(meta.get("scalars") or {})
        snap = SolverSnapshot(
            name=name, arrays=arrays, scalars=scalars,
            phase=int(meta.get("phase", 0)),
            time=float.fromhex(meta["time_hex"]) if meta.get("time_hex") else 0.0,
        )
        # reuse the digests from the verification pass instead of hashing twice
        snap.cache_digest(
            compose_state_digest(recomputed_digests, scalars,
                                 extra={"phase": snap.phase, "time": float(snap.time).hex()})
        )
        if verify and snap.digest != meta.get("digest"):
            raise CheckpointCorruption(
                f"solver digest mismatch for {name!r}: expected {meta.get('digest')} got {snap.digest}"
            )
        return snap, decompress_seconds

    @staticmethod
    def _bad_chunk(a_meta: Mapping[str, Any], chunks: Sequence[Mapping[str, Any]]) -> str:
        """Localise a corruption to an array chunk (audit-friendly)."""

        recorded = {int(c["index"]): c for c in (a_meta.get("chunks") or [])}
        for chunk in chunks:
            ref = recorded.get(int(chunk["index"]))
            if ref is None or ref.get("sha256") != chunk["sha256"]:
                return (
                    f"array chunk {chunk['index']} (offset {chunk['offset']}, "
                    f"{chunk['nbytes']} bytes) corrupt"
                )
        return ""

    # ── maintenance ────────────────────────────────────────────────────────
    def verify_all(self) -> List[Dict[str, Any]]:
        report: List[Dict[str, Any]] = []
        for step in self.steps():
            try:
                ck = self.load(step)
                report.append({"step": step, "ok": True, "digest": ck.digest})
            except Exception as exc:  # noqa: BLE001 - the point is to classify any failure
                report.append({"step": step, "ok": False, "error": type(exc).__name__,
                               "message": str(exc)})
        return report

    def prune(self, keep: int = 4) -> List[int]:
        keep = max(1, int(keep))
        steps = self.steps()
        removed = steps[: max(0, len(steps) - keep)]
        for step in removed:
            shutil.rmtree(self.dir_of(step), ignore_errors=True)
        return removed

    def quarantine(self, step: int, reason: str = "") -> Path:
        """Move a failed checkpoint aside so it is never silently retried."""

        src = self.dir_of(step)
        target = self.root / f"{src.name}.corrupt-{time.strftime('%Y%m%dT%H%M%S')}-{uuid.uuid4().hex[:6]}"
        os.replace(src, target)
        (target / "QUARANTINE.txt").write_text(
            f"step={step}\nreason={reason}\nquarantined_at={time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}\n",
            encoding="utf-8",
        )
        return target

    # ── helpers ────────────────────────────────────────────────────────────
    @staticmethod
    def _write_json(path: Path, payload: Mapping[str, Any]) -> None:
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, indent=2, sort_keys=True)
            fh.flush()
            os.fsync(fh.fileno())

    @staticmethod
    def _fsync_dir(path: Path) -> None:
        try:
            fd = os.open(path, os.O_RDONLY)
        except OSError:  # pragma: no cover - platform dependent
            return
        try:
            os.fsync(fd)
        finally:
            os.close(fd)

    def describe(self) -> Dict[str, Any]:
        return {
            "root": str(self.root),
            "schema": SCHEMA,
            "codec": self.codec.describe(),
            "chunk_bytes": self.chunk_bytes,
            "grid": {"dt_master": self.grid.dt_master, "n_sub": self.grid.n_sub,
                     "digest": self.grid.digest()},
            "sync_policy": self.guard.policy.value,
            "ledger": list(self.ledger.names) if self.ledger is not None else None,
            "steps": self.steps(),
        }


def _scalars_from_canonical(payload: Mapping[str, Any]) -> Dict[str, Any]:
    """Inverse of :func:`canonical_scalars` for the scalar types we emit."""

    out: Dict[str, Any] = {}
    for key, value in payload.items():
        if isinstance(value, dict) and set(value) == {"f"}:
            out[key] = float.fromhex(value["f"])
        elif isinstance(value, dict) and set(value) == {"special"}:
            out[key] = float(value["special"])
        elif isinstance(value, dict) and {"dtype", "shape", "sha256"} <= set(value):
            out[key] = {"dtype": value["dtype"], "shape": list(value["shape"]),
                        "sha256": value["sha256"]}
        else:
            out[key] = value
    return out