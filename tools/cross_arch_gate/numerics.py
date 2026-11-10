"""Architecture-independent numeric plumbing for the cross-architecture gate.

Three things live here, all stdlib-only so the module runs unchanged on any
node of the fleet:

1. **Bit-level identity plumbing** — ``float.hex()`` round-trip and payload-aware
   NaN comparison (so "same NaN" and "different NaN payload" are distinguishable
   facts, not a single opaque ``nan != nan``).
2. **ULP distance** — the number of representable doubles between two values on
   the monotone integer key line.  Cross-checked against
   ``tools.repro_digest.ulp.ulp_distance`` in the test suite (that module is the
   older in-repo implementation used for the same-ISA reproducibility protocol;
   agreement is asserted rather than assumed).
3. **A portable IEEE-754 binary64 oracle** — every elementary operation
   (``+ - * / sqrt`` and fused multiply-add) re-implemented on exact rationals and
   rounded once, half-to-even.  An operation emulated this way is bit-identical
   to what any IEEE-754 conforming binary64 implementation must return, so the
   oracle is a *hardware-free reference arm*: it makes the arithmetic layer of
   the gate verifiable without a second physical machine, and it is the only
   honest way to state "what an x86-64 SSE2 unit would produce here" when no
   x86 host is reachable from the build node.
"""

from __future__ import annotations

import hashlib
import math
import struct
from fractions import Fraction
from typing import Any, Iterable, Optional

MASK63 = (1 << 63) - 1
SIGN_BIT = 1 << 63
ULP_UNBOUNDED = (1 << 64) - 1


# --------------------------------------------------------------------------- #
# bit identity
# --------------------------------------------------------------------------- #
def bits(value: float) -> int:
    """IEEE-754 binary64 bit pattern as an unsigned 64-bit integer."""
    return struct.unpack("<Q", struct.pack("<d", float(value)))[0]


def from_bits(word: int) -> float:
    return struct.unpack("<d", struct.pack("<Q", int(word) & 0xFFFFFFFFFFFFFFFF))[0]


def hex_token(value: float) -> str:
    """``%a``-style exact hexadecimal token (CPython's ``float.hex``)."""
    return float(value).hex()


def decode_hex(token: str) -> float:
    """Inverse of :func:`hex_token`; accepts the C99 ``%a`` spelling too."""
    return float.fromhex(str(token).strip())


def sha256_of_tokens(tokens: Iterable[str]) -> str:
    h = hashlib.sha256()
    for token in tokens:
        h.update(str(token).encode("utf-8"))
        h.update(b"\x00")
    return h.hexdigest()


def is_nan(value: Any) -> bool:
    return isinstance(value, float) and math.isnan(value)


def nan_payload(value: float) -> Optional[int]:
    """Mantissa payload of a NaN, or ``None`` for non-NaN values."""
    if not is_nan(value):
        return None
    return bits(value) & 0x000FFFFFFFFFFFFF


# --------------------------------------------------------------------------- #
# ULP distance
# --------------------------------------------------------------------------- #
def _key(value: float) -> int:
    """Monotone integer key: ``key(a) < key(b) <=> a < b`` for finite floats.

    Negative values are mirrored onto ``-(magnitude word)`` so that the key line
    is monotone through zero and ``+0.0``/``-0.0`` share the key ``0`` (they are
    equal values; their bit patterns are compared separately, see
    :func:`.classify.classify_field`).  This is the same convention as
    ``tools.repro_digest.ulp``; the test suite asserts agreement.
    """
    word = bits(value)
    if word & SIGN_BIT:
        return -(word & MASK63)
    return word


def ulp_distance(a: float, b: float) -> int:
    """Number of representable doubles between ``a`` and ``b`` (0 = same bits).

    Returns :data:`ULP_UNBOUNDED` when either side is not finite, because the
    integer key line is only meaningful for finite values.
    """
    if not (math.isfinite(a) and math.isfinite(b)):
        return 0 if bits(a) == bits(b) else ULP_UNBOUNDED
    return abs(_key(a) - _key(b))


def rel_error(a: float, b: float) -> float:
    """Symmetric relative distance, with an exact-zero convention.

    ``rel_error(0, 0) == 0`` (identical zeros are not an error), and a
    denominator floored at the smaller of the two magnitudes so that a
    cancel-to-zero on one side against a non-zero on the other reads back as 1.0
    (i.e. 100 %), not as infinity.
    """
    if a == b:
        return 0.0
    if not (math.isfinite(a) and math.isfinite(b)):
        return math.inf
    denom = max(abs(a), abs(b))
    return abs(a - b) / denom if denom else 0.0


# --------------------------------------------------------------------------- #
# portable IEEE-754 binary64 oracle
# --------------------------------------------------------------------------- #
def _pow2(exp: int) -> Fraction:
    return Fraction(1 << exp, 1) if exp >= 0 else Fraction(1, 1 << -exp)


def to_binary64(value: Fraction) -> float:
    """Correctly rounded (half-to-even) conversion of an exact rational.

    Normal, subnormal and overflow ranges are all handled; the result is the
    unique binary64 nearest to ``value`` with ties resolved to an even mantissa,
    i.e. exactly the IEEE-754 default rounding of an infinitely precise
    intermediate.
    """
    if value == 0:
        return 0.0
    sign = -1.0 if value < 0 else 1.0
    a = -value if value < 0 else value
    exp = a.numerator.bit_length() - a.denominator.bit_length()
    while _pow2(exp) > a:
        exp -= 1
    while _pow2(exp + 1) <= a:
        exp += 1
    if exp > 1023:
        return sign * math.inf
    if exp < -1022:                      # subnormal target lattice
        shift, target = 1074, -1074
    else:
        shift, target = 52 - exp, exp - 52
    num, den = a.numerator, a.denominator
    if shift >= 0:
        num <<= shift
    else:
        den <<= -shift
    quot, rem = divmod(num, den)
    if rem:
        twice = 2 * rem
        if twice > den or (twice == den and (quot & 1)):
            quot += 1
    return sign * math.ldexp(float(quot), target)


def _frac(x: float) -> Fraction:
    return Fraction(x)          # exact: every finite double is a dyadic rational


def exact_add(a: float, b: float) -> float:
    return to_binary64(_frac(a) + _frac(b))


def exact_sub(a: float, b: float) -> float:
    return to_binary64(_frac(a) - _frac(b))


def exact_mul(a: float, b: float) -> float:
    return to_binary64(_frac(a) * _frac(b))


def exact_div(a: float, b: float) -> float:
    if b == 0.0:
        return math.inf if a else math.nan
    return to_binary64(_frac(a) / _frac(b))


def exact_fma(a: float, b: float, c: float) -> float:
    """Fused multiply-add: one rounding for ``a*b + c`` (x86-64 FMA semantics)."""
    return to_binary64(_frac(a) * _frac(b) + _frac(c))


def exact_sqrt(a: float) -> float:
    """Correctly rounded square root.

    The real square root of a binary64 is either exactly representable or
    irrational, so no midpoint ties can occur and 160 guard bits of integer
    square root suffice for a bit-exact answer.
    """
    if a < 0 or math.isnan(a):
        return math.nan
    if a == 0 or not math.isfinite(a):
        return a
    frac = _frac(a)
    root = math.isqrt(frac.numerator * frac.denominator)   # exact iff perfect square
    if root * root == frac.numerator * frac.denominator and frac.denominator == 1:
        return to_binary64(Fraction(root))
    guard = 160
    scaled = (frac.numerator << (2 * guard)) // frac.denominator
    approx = math.isqrt(scaled)
    return to_binary64(Fraction(approx, 1 << guard))


# --------------------------------------------------------------------------- #
# deterministic integer source (identical on every architecture by construction)
# --------------------------------------------------------------------------- #
class Lcg:
    """Numerical Recipes 64-bit LCG; pure integer arithmetic, hence arch-exact."""

    MASK = (1 << 64) - 1

    def __init__(self, seed: int = 20261110) -> None:
        self.state = seed & self.MASK

    def next_int(self) -> int:
        self.state = (6364136223846793005 * self.state + 1442695040888963407) & self.MASK
        return self.state

    def next_unit(self) -> float:
        """Uniform double in [0,1): ``m / 2**53`` is exact, so no rounding enters."""
        return (self.next_int() >> 11) / float(1 << 53)