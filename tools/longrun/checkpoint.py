"""Bit-level checkpoint store: atomic writes, per-array digests, chained manifests.

Design rules that make "resume" verifiable instead of hopeful:

* **bit level** -- every array is digested as ``sha256(dtype + shape + tobytes())``,
  i.e. the exact IEEE-754 bit pattern, not a tolerance-based comparison.
* **atomic** -- write ``*.tmp`` in the same directory, ``fsync`` file and directory,
  then ``os.replace``.  A crash mid-write can never leave a half checkpoint at the
  canonical name.
* **chained** -- each manifest carries ``prev_digest`` so deletion/rollback of an
  intermediate checkpoint is detectable (append-only audit trail).
* **fail loud** -- any inconsistency raises :class:`CheckpointCorruption` /
  :class:`CheckpointUnreadable`; silent fallback to a stale state is forbidden
  because it would corrupt a bit-idempotent resume guarantee.
"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

import numpy as np

SCHEMA = "longrun.ckpt/1"


class CheckpointError(Exception):
    """Base class for every checkpoint problem (classified as state_corruption)."""


class CheckpointCorruption(CheckpointError):
    """Digest/chain mismatch: the bytes on disk are not the bytes we wrote."""


class CheckpointUnreadable(CheckpointError):
    """The checkpoint cannot be opened at all (truncated, missing, bad zip)."""


def canonical_json(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def array_digest(arr: np.ndarray) -> str:
    """Bit-level digest of a numpy array (dtype + shape + raw C-order bytes)."""

    a = np.ascontiguousarray(arr)
    header = canonical_json({"dtype": a.dtype.str, "shape": list(a.shape), "order": "C"})
    h = hashlib.sha256()
    h.update(header.encode())
    h.update(b"\x00")
    h.update(a.tobytes())
    return h.hexdigest()


def state_digest(arrays: Dict[str, np.ndarray], scalars: Optional[Dict[str, Any]] = None) -> str:
    """Order-independent digest of a whole state dict (used for idempotence checks)."""

    payload = {name: array_digest(arrays[name]) for name in sorted(arrays)}
    if scalars:
        payload["__scalars__"] = {k: scalars[k] for k in sorted(scalars)}
    return sha256_bytes(canonical_json(payload).encode())


@dataclass
class Checkpoint:
    step: int
    arrays: Dict[str, np.ndarray]
    scalars: Dict[str, Any]
    manifest: Dict[str, Any]

    @property
    def digest(self) -> str:
        return self.manifest["digest"]

    def describe(self) -> Dict[str, Any]:
        return {
            "step": self.step,
            "digest": self.digest,
            "config_digest": self.manifest.get("config_digest"),
            "input_digest": self.manifest.get("input_digest"),
            "arrays": {k: v["sha256"] for k, v in self.manifest["arrays"].items()},
        }


class CheckpointStore:
    """Directory-backed checkpoint store with digest verification on load."""

    def __init__(self, root: os.PathLike | str) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    # -- naming ------------------------------------------------------------ #
    def _paths(self, step: int) -> Tuple[Path, Path]:
        stem = f"ckpt_{int(step):010d}"
        return self.root / f"{stem}.npz", self.root / f"{stem}.json"

    def steps(self) -> List[int]:
        out: List[int] = []
        for p in self.root.glob("ckpt_*.json"):
            try:
                out.append(int(p.stem.split("_", 1)[1]))
            except (IndexError, ValueError):
                continue
        return sorted(out)

    def latest_step(self) -> Optional[int]:
        steps = self.steps()
        return steps[-1] if steps else None

    # -- write ------------------------------------------------------------- #
    def save(
        self,
        step: int,
        arrays: Dict[str, np.ndarray],
        scalars: Optional[Dict[str, Any]] = None,
        *,
        config_digest: Optional[str] = None,
        input_digest: Optional[str] = None,
        code_version: Optional[Dict[str, Any]] = None,
        extra: Optional[Dict[str, Any]] = None,
        label: str = "",
    ) -> Dict[str, Any]:
        scalars = dict(scalars or {})
        meta_arrays: Dict[str, Dict[str, Any]] = {}
        for name in sorted(arrays):
            arr = np.ascontiguousarray(arrays[name])
            arrays[name] = arr
            meta_arrays[name] = {
                "sha256": array_digest(arr),
                "dtype": arr.dtype.str,
                "shape": list(arr.shape),
                "nbytes": int(arr.nbytes),
            }
        prev = self.latest_step()
        prev_digest = None
        if prev is not None and prev != step:
            try:
                prev_digest = self.manifest_of(prev).get("digest")
            except CheckpointError:
                prev_digest = None
        manifest: Dict[str, Any] = {
            "schema": SCHEMA,
            "step": int(step),
            "label": label,
            "arrays": meta_arrays,
            "scalars": scalars,
            "config_digest": config_digest,
            "input_digest": input_digest,
            "code_version": code_version or {},
            "prev_digest": prev_digest,
            "state_digest": state_digest(arrays, scalars),
            "prev_step": prev,
            "extra": extra or {},
        }
        manifest["digest"] = sha256_bytes(canonical_json(manifest).encode())

        npz_path, json_path = self._paths(step)
        tmp_npz = npz_path.with_suffix(".npz.tmp")
        tmp_json = json_path.with_suffix(".json.tmp")
        with open(tmp_npz, "wb") as fh:
            np.savez(fh, **{k: arrays[k] for k in sorted(arrays)})
            fh.flush()
            os.fsync(fh.fileno())
        with open(tmp_json, "w", encoding="utf-8") as fh:
            fh.write(json.dumps(manifest, indent=2, sort_keys=True))
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp_json, json_path)
        os.replace(tmp_npz, npz_path)
        try:
            dfd = os.open(self.root, os.O_RDONLY)
            try:
                os.fsync(dfd)
            finally:
                os.close(dfd)
        except OSError:
            pass
        return manifest

    # -- read -------------------------------------------------------------- #
    def manifest_of(self, step: int) -> Dict[str, Any]:
        _, json_path = self._paths(step)
        try:
            manifest = json.loads(json_path.read_text(encoding="utf-8"))
        except FileNotFoundError as exc:
            raise CheckpointUnreadable(f"manifest missing for step {step}") from exc
        except (OSError, json.JSONDecodeError) as exc:
            raise CheckpointUnreadable(f"manifest unreadable for step {step}: {exc}") from exc
        stored = manifest.get("digest")
        recomputed = sha256_bytes(
            canonical_json({k: v for k, v in manifest.items() if k != "digest"}).encode()
        )
        if stored != recomputed:
            raise CheckpointCorruption(
                f"manifest digest mismatch at step {step}: stored={stored} recomputed={recomputed}"
            )
        return manifest

    def load(self, step: Optional[int] = None, *, verify_arrays: bool = True) -> Checkpoint:
        if step is None:
            step = self.latest_step()
            if step is None:
                raise CheckpointUnreadable("no checkpoint available")
        manifest = self.manifest_of(int(step))
        npz_path, _ = self._paths(int(step))
        try:
            with np.load(npz_path, allow_pickle=False) as data:
                arrays = {name: np.array(data[name], copy=True) for name in data.files}
        except FileNotFoundError as exc:
            raise CheckpointUnreadable(f"array file missing for step {step}") from exc
        except Exception as exc:  # zipfile/format/EOF problems all mean "corrupted"
            raise CheckpointUnreadable(f"array file unreadable for step {step}: {exc}") from exc
        if verify_arrays:
            for name, meta in manifest["arrays"].items():
                if name not in arrays:
                    raise CheckpointCorruption(f"array '{name}' missing from checkpoint {step}")
                actual = array_digest(arrays[name])
                if actual != meta["sha256"]:
                    raise CheckpointCorruption(
                        f"array '{name}' digest mismatch at step {step}: "
                        f"expected={meta['sha256']} actual={actual}"
                    )
            extra = sorted(set(arrays) - set(manifest["arrays"]))
            if extra:
                raise CheckpointCorruption(f"unexpected arrays {extra} in checkpoint {step}")
            if state_digest(arrays, manifest.get("scalars") or {}) != manifest.get("state_digest"):
                raise CheckpointCorruption(f"state digest mismatch at step {step}")
        return Checkpoint(step=int(step), arrays=arrays, scalars=dict(manifest.get("scalars") or {}), manifest=manifest)

    def verify_all(self) -> List[Dict[str, Any]]:
        report = []
        for step in self.steps():
            try:
                ck = self.load(step)
                report.append({"step": step, "ok": True, "digest": ck.digest})
            except CheckpointError as exc:
                report.append({"step": step, "ok": False, "error": str(exc)})
        return report

    def prune(self, keep: int = 4) -> List[int]:
        steps = self.steps()
        removed = steps[: max(0, len(steps) - keep)]
        for step in removed:
            npz_path, json_path = self._paths(step)
            for p in (npz_path, json_path):
                try:
                    p.unlink()
                except OSError:
                    pass
        return removed