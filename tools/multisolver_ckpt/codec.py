"""Lossless codec layer for multi-solver checkpoints (zstd + verified fallbacks).

Design rules
------------
* **explicit identity** -- codec name, level, backend and zstd frame format are
  recorded in every checkpoint manifest (``describe()``), so a checkpoint can
  always be decoded by the code it was written with, and a future codec change
  cannot silently reinterpret old bytes.
* **lossless or nothing** -- a codec either round-trips the exact byte string
  or raises :class:`~tools.multisolver_ckpt.errors.CodecError`.  There is no
  "best effort" path: a lossy step here would destroy the bit-level
  reproducibility guarantee the whole store is built around.
* **fail closed** -- requesting ``zstd`` when neither the ``zstandard`` python
  binding nor the ``zstd`` CLI is present raises
  :class:`~tools.multisolver_ckpt.errors.CodecUnavailable` at *construction*
  time.  A silent downgrade to ``raw`` would show up much later as a size or
  performance surprise, so it is forbidden.

The ``raw`` codec exists on purpose: it is the control arm that makes the
"1-bit flip is detected by sha256, not merely by the compressor" claim testable.
"""

from __future__ import annotations

import shutil
import subprocess
from typing import Dict, Tuple

from .errors import CodecError, CodecUnavailable

ZSTD_MAGIC = b"\x28\xb5\x2f\xfd"
ZSTD_LEVEL_RANGE = (1, 19)


def _zstd_cli() -> str | None:
    return shutil.which("zstd")


def _python_zstd():
    try:  # pragma: no cover - depends on the environment
        import zstandard  # type: ignore

        return zstandard
    except Exception:  # pragma: no cover
        return None


def available_codecs() -> Tuple[str, ...]:
    """Codecs usable in this interpreter (``raw`` is always available)."""

    names = ["raw"]
    if _python_zstd() is not None or _zstd_cli() is not None:
        names.append("zstd")
    return tuple(names)


def zstd_available() -> bool:
    return "zstd" in available_codecs()


class Codec:
    """Byte-string <-> byte-string transform with a recorded identity."""

    name = "abstract"
    suffix = ".bin"

    def compress(self, data: bytes) -> bytes:  # pragma: no cover - interface
        raise NotImplementedError

    def decompress(self, data: bytes) -> bytes:  # pragma: no cover - interface
        raise NotImplementedError

    def describe(self) -> Dict[str, object]:  # pragma: no cover - interface
        raise NotImplementedError

    # -- helpers ---------------------------------------------------------- #
    def roundtrip_ok(self, probe: bytes = b"multisolver-ckpt-probe") -> bool:
        try:
            return self.decompress(self.compress(probe)) == probe
        except CodecError:
            return False

    def __repr__(self) -> str:  # pragma: no cover - cosmetic
        return f"<{type(self).__name__} {self.name} {self.describe()}>"


class RawCodec(Codec):
    """Identity transform: the control arm for corruption-detection tests."""

    name = "raw"
    suffix = ".bin"

    def compress(self, data: bytes) -> bytes:
        return bytes(data)

    def decompress(self, data: bytes) -> bytes:
        return bytes(data)

    def describe(self) -> Dict[str, object]:
        return {"codec": self.name, "level": None, "backend": "identity", "lossless": True}


class ZstdCodec(Codec):
    """zstd through the ``zstandard`` binding when present, else the CLI."""

    name = "zstd"
    suffix = ".zst"

    def __init__(self, level: int = 3, threads: int = 0) -> None:
        lo, hi = ZSTD_LEVEL_RANGE
        if not (lo <= int(level) <= hi):
            raise CodecError(f"zstd level must be in [{lo}, {hi}], got {level!r}")
        if int(threads) < 0:
            raise CodecError(f"zstd threads must be >= 0, got {threads!r}")
        self.level = int(level)
        self.threads = int(threads)
        mod = _python_zstd()
        if mod is not None:
            self.backend = "python-zstandard"
            self._impl = mod
            self._compressor = mod.ZstdCompressor(
                level=self.level, threads=self.threads or 0
            )
            self._decompressor = mod.ZstdDecompressor()
        elif _zstd_cli() is not None:
            self.backend = "zstd-cli"
            self._impl = None
            self._cli = _zstd_cli()
        else:  # pragma: no cover - guarded by available_codecs
            raise CodecUnavailable(
                "zstd requested but neither the 'zstandard' python binding nor the 'zstd' CLI is installed"
            )
        if not self.roundtrip_ok():  # pragma: no cover - environment guard
            raise CodecUnavailable(f"zstd backend {self.backend} failed its own round-trip self test")

    # -- compression ------------------------------------------------------ #
    def compress(self, data: bytes) -> bytes:
        payload = bytes(data)
        if not payload:
            return b""
        if self._impl is not None:
            try:
                out = self._compressor.compress(payload)
            except Exception as exc:  # pragma: no cover - binding level errors
                raise CodecError(f"python-zstandard compress failed: {exc}") from exc
        else:
            cmd = [self._cli, "-q", "-c", f"-{self.level}", f"-T{self.threads}"]
            proc = subprocess.run(cmd, input=payload, capture_output=True)
            if proc.returncode != 0:
                raise CodecError(
                    f"zstd CLI compress failed (rc={proc.returncode}): "
                    f"{proc.stderr.decode('utf-8', 'replace')[:400]}"
                )
            out = proc.stdout
        if not out.startswith(ZSTD_MAGIC):
            raise CodecError("compressed output lacks the zstd frame magic; refusing to write it")
        return out

    def decompress(self, data: bytes) -> bytes:
        payload = bytes(data)
        if not payload:
            return b""
        if not payload.startswith(ZSTD_MAGIC):
            raise CodecError("payload is not a zstd frame (magic missing): checkpoint is not decodable")
        if self._impl is not None:
            try:
                return self._decompressor.decompress(payload)
            except Exception as exc:
                raise CodecError(f"python-zstandard decompress failed: {exc}") from exc
        cmd = [self._cli, "-d", "-q", "-c"]
        proc = subprocess.run(cmd, input=payload, capture_output=True)
        if proc.returncode != 0:
            raise CodecError(
                f"zstd CLI decompress failed (rc={proc.returncode}): "
                f"{proc.stderr.decode('utf-8', 'replace')[:400]}"
            )
        return proc.stdout

    def describe(self) -> Dict[str, object]:
        impl = self._impl
        version = getattr(impl, "__version__", None) if impl is not None else None
        return {
            "codec": self.name,
            "level": self.level,
            "threads": self.threads,
            "backend": self.backend,
            "backend_version": version,
            "frame_magic": ZSTD_MAGIC.hex(),
            "lossless": True,
        }


def get_codec(name: str = "zstd", level: int = 3, threads: int = 0) -> Codec:
    """Factory honouring the manifest-recorded codec identity."""

    key = str(name).lower()
    if key in ("raw", "none", "identity"):
        return RawCodec()
    if key == "zstd":
        return ZstdCodec(level=level, threads=threads)
    raise CodecUnavailable(f"unknown codec {name!r}; available: {available_codecs()}")


def codec_from_description(desc: Dict[str, object]) -> Codec:
    """Rebuild the codec recorded inside a checkpoint manifest."""

    if not isinstance(desc, dict) or "codec" not in desc:
        raise CodecUnavailable(f"manifest carries no codec description: {desc!r}")
    return get_codec(
        str(desc["codec"]),
        level=int(desc.get("level") or 3),
        threads=int(desc.get("threads") or 0),
    )