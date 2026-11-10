"""Exception taxonomy for the multi-solver checkpoint layer.

The taxonomy is deliberately *flat and closed*: every failure mode of a
checkpoint (bad bytes, missing file, foreign schema, inconsistent coupling
time, codec missing, energy gate violated) maps onto exactly one class, so a
restart supervisor can decide "retry / roll back / abort" without parsing
message strings.

Domain neutrality: nothing in here knows what the serialised arrays *mean*.
A checkpoint is bytes + metadata; the classes below describe how those can be
wrong.
"""

from __future__ import annotations


class CheckpointError(Exception):
    """Base class for every checkpoint problem."""


class CheckpointCorruption(CheckpointError):
    """Bytes on disk are not the bytes that were written (digest mismatch)."""


class CheckpointUnreadable(CheckpointError):
    """The checkpoint cannot be opened at all (missing, truncated, bad codec)."""


class CheckpointLayoutError(CheckpointError):
    """The store cannot accept this request (step clash, unsafe path, ...)."""


class NonSyncPointError(CheckpointError):
    """A checkpoint was taken or loaded at a coupling instant that is not a
    synchronisation point, so the per-solver states belong to different
    logical times and cannot be resumed consistently as a coupled state."""


class SchemaMismatch(CheckpointError):
    """Manifest schema of the checkpoint is unknown to this code."""


class CodecError(RuntimeError):
    """Compression/decompression failed or produced a non-round-trip result."""


class CodecUnavailable(CodecError):
    """The requested codec is not installed in this environment (fail closed)."""