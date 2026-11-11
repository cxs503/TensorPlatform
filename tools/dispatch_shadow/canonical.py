"""Canonical serialisation, content addressing, bitwise float comparison and a
counter-based deterministic RNG.

Everything in the dispatch decision log is keyed by a hash of *canonical bytes*,
so two processes that disagree about dict ordering, float formatting or RNG
streams must produce different hashes rather than silently different logs.

Rules (schema v1):

* canonical JSON = ``json.dumps(obj, sort_keys=True, separators=(",", ":"),
  ensure_ascii=False, allow_nan=False)`` encoded as UTF-8.  Python's float
  repr is the shortest round-tripping decimal, so ``float -> str -> float`` is
  an identity and the digest is stable across platforms.
* ``NaN``/``Infinity`` are **not** representable: a log that cannot be
  canonicalised cannot be replayed, so it is rejected at write time.
* floats are compared *bit for bit* via ``struct.pack(">d", x)`` -- ``0.0 ==
  -0.0`` and ``x == x + 1ulp is False`` are both things a naive ``==`` gets
  wrong in opposite directions.
"""

from __future__ import annotations

import hashlib
import json
import math
import struct
from typing import Any, Iterable, Mapping

__all__ = [
    "canonical_json",
    "canonical_bytes",
    "sha256_hex",
    "content_digest",
    "float_bits",
    "bit_equal",
    "ulp_distance",
    "HashRNG",
    "fsum",
]


def canonical_json(obj: Any) -> str:
    """Serialise ``obj`` to the canonical JSON form used for all digests."""
    return json.dumps(
        obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    )


def canonical_bytes(obj: Any) -> bytes:
    return canonical_json(obj).encode("utf-8")


def sha256_hex(data: bytes | str) -> str:
    if isinstance(data, str):
        data = data.encode("utf-8")
    return hashlib.sha256(data).hexdigest()


def content_digest(obj: Any) -> str:
    """Content address of a JSON-able object."""
    return sha256_hex(canonical_bytes(obj))


def float_bits(x: float) -> str:
    """IEEE-754 big-endian hex of a float -- the *bitwise* identity of a value."""
    return struct.pack(">d", float(x)).hex()


def bit_equal(a: float, b: float) -> bool:
    """True iff ``a`` and ``b`` have the same IEEE-754 bit pattern.

    Distinguishes ``0.0`` from ``-0.0`` (different bits, ``==`` true) and any
    1-ulp difference (different bits, ``==`` false; both handled explicitly).
    NaN never compares equal to itself, but two NaNs with the same payload do
    have the same bits -- replay treats that as a match, since NaNs in a score
    column are a policy bug caught by schema validation, not by replay.
    """
    return float_bits(a) == float_bits(b)


def ulp_distance(a: float, b: float) -> int:
    """Number of representable floats between ``a`` and ``b`` (order-preserving ints)."""
    ia = struct.unpack(">q", struct.pack(">d", float(a)))[0]
    ib = struct.unpack(">q", struct.pack(">d", float(b)))[0]
    if ia < 0:
        ia = -(1 << 63) - ia
    if ib < 0:
        ib = -(1 << 63) - ib
    return abs(ia - ib)


def fsum(values: Iterable[float]) -> float:
    """Exactly-rounded sum -- the mandated reducer for replayable scores.

    ``sum()`` is order dependent for floats, so a policy that reduces a slate
    with ``sum`` can replay differently if the candidate order changes; that is
    precisely the class of non-determinism this module must exclude.
    """
    return math.fsum(float(v) for v in values)


class HashRNG:
    """Counter-mode SHA-256 RNG: same seed -> same stream on any platform.

    ``random.Random``/``numpy.random`` are stable in practice but not by
    contract, and their streams are not auditable from the log.  Here every
    draw is ``int(sha256(seed || counter)) / 2**256`` -- the seed is recorded in
    the decision record, so a replayed bootstrap is reproducible from the log
    alone.
    """

    __slots__ = ("_seed", "_counter", "_buf", "_buf_pos")

    def __init__(self, seed: str | bytes):
        self._seed = seed.encode("utf-8") if isinstance(seed, str) else bytes(seed)
        self._counter = 0
        self._buf = b""
        self._buf_pos = 0

    # -- primitives -----------------------------------------------------
    def _next_u64(self) -> int:
        if self._buf_pos + 8 > len(self._buf):
            self._buf = hashlib.sha256(
                self._seed + b"|" + str(self._counter).encode("ascii")
            ).digest()
            self._counter += 1
            self._buf_pos = 0
        chunk = self._buf[self._buf_pos : self._buf_pos + 8]
        self._buf_pos += 8
        return int.from_bytes(chunk, "big")

    def random(self) -> float:
        """Uniform in [0, 1) with 53 bits of precision."""
        return (self._next_u64() >> 11) * (1.0 / (1 << 53))

    def uniform(self, lo: float, hi: float) -> float:
        return lo + (hi - lo) * self.random()

    def randint(self, n: int) -> int:
        if n <= 0:
            raise ValueError("randint requires n > 0")
        return self._next_u64() % n

    def choice(self, probs: Iterable[float]) -> int:
        """Draw an index from an unnormalised probability vector (cumulative walk)."""
        p = [float(x) for x in probs]
        total = fsum(p)
        if total <= 0:
            raise ValueError("choice requires positive total mass")
        u = self.random() * total
        acc = 0.0
        for i, pi in enumerate(p):
            acc += pi
            if u < acc:
                return i
        return len(p) - 1

    def sample_indices(self, n: int, k: int) -> list[int]:
        """``k`` indices drawn with replacement from ``range(n)``."""
        return [self.randint(n) for _ in range(k)]


def digest_with_prefix(prefix: str, obj: Any) -> str:
    """Domain-separated digest (prevents cross-purpose hash reuse)."""
    return sha256_hex(prefix + "|" + canonical_json(obj))


def stable_mapping(items: Mapping[str, Any], names: Iterable[str]) -> dict[str, Any]:
    """Project ``items`` onto ``names`` in the given (config) order."""
    return {name: items[name] for name in names}