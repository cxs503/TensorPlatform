"""Pure-Python Ed25519 (RFC 8032) — no third-party crypto dependency.

The audit tool must be re-runnable on the cluster (loong64) and inside the
platform CI image, neither of which guarantees ``cryptography`` or
``pynacl``.  This module is a compact, literal transcription of the RFC 8032
reference algorithm (extended twisted-Edwards coordinates, SHA-512).

Only two primitives are exported:

    generate_seed()            -> 32-byte seed (private key material)
    public_key(seed)           -> 32-byte public key
    sign(seed, message)        -> 64-byte signature
    verify(public_key, message, signature) -> bool

Correctness is pinned to the RFC 8032 section 7.1 test vectors in
``tests/test_capability_audit.py``.
"""
from __future__ import annotations

import hashlib
import os

P = 2**255 - 19
L = 2**252 + 27742317777372353535851937790883648493


def _sha512(data: bytes = b"") -> bytes:
    return hashlib.sha512(data).digest()


def _inv(x: int) -> int:
    return pow(x, P - 2, P)


_D = -121665 * _inv(121666) % P
_SQRT_M1 = pow(2, (P - 1) // 4, P)


def _add(p1, p2):
    """Extended-coordinate point addition (Hisil et al. 2008)."""
    x1, y1, z1, t1 = p1
    x2, y2, z2, t2 = p2
    a = (y1 - x1) * (y2 - x2) % P
    b = (y1 + x1) * (y2 + x2) % P
    c = t1 * 2 * _D * t2 % P
    d = z1 * 2 * z2 % P
    e, f, g, h = b - a, d - c, d + c, b + a
    return (e * f % P, g * h % P, f * g % P, e * h % P)


def _mul(scalar: int, point):
    result = (0, 1, 1, 0)
    addend = point
    while scalar > 0:
        if scalar & 1:
            result = _add(result, addend)
        addend = _add(addend, addend)
        scalar >>= 1
    return result


def _recover_x(y: int, sign: int) -> int:
    if y >= P:
        raise ValueError("y out of field")
    x2 = (y * y - 1) * _inv(_D * y * y + 1) % P
    if x2 == 0:
        if sign:
            raise ValueError("invalid point sign")
        return 0
    x = pow(x2, (P + 3) // 8, P)
    if (x * x - x2) % P != 0:
        x = x * _SQRT_M1 % P
    if (x * x - x2) % P != 0:
        raise ValueError("not a square")
    if (x & 1) != sign:
        x = P - x
    return x


_BY = 4 * _inv(5) % P
_BX = _recover_x(_BY, 0)
_B = (_BX, _BY, 1, _BX * _BY % P)


def _compress(point) -> bytes:
    x, y, z, _t = point
    zinv = _inv(z)
    x = x * zinv % P
    y = y * zinv % P
    return int.to_bytes(y | ((x & 1) << 255), 32, "little")


def _decompress(data: bytes):
    if len(data) != 32:
        raise ValueError("point encoding must be 32 bytes")
    y = int.from_bytes(data, "little")
    sign = y >> 255
    y &= (1 << 255) - 1
    x = _recover_x(y, sign)
    return (x, y, 1, x * y % P)


def _expand(seed: bytes):
    if len(seed) != 32:
        raise ValueError("seed must be 32 bytes")
    h = _sha512(seed)
    a = int.from_bytes(h[:32], "little")
    # RFC 8032 clamping: clear the 3 low bits and bit 255, set bit 254.
    a &= (1 << 254) - 8
    a |= 1 << 254
    return a, h[32:]


def _hash_mod_l(data: bytes) -> int:
    return int.from_bytes(_sha512(data), "little") % L


def generate_seed() -> bytes:
    return os.urandom(32)


def public_key(seed: bytes) -> bytes:
    a, _prefix = _expand(seed)
    return _compress(_mul(a, _B))


def sign(seed: bytes, message: bytes) -> bytes:
    a, prefix = _expand(seed)
    pub = _compress(_mul(a, _B))
    r = _hash_mod_l(prefix + message)
    r_enc = _compress(_mul(r, _B))
    k = _hash_mod_l(r_enc + pub + message)
    s = (r + k * a) % L
    return r_enc + int.to_bytes(s, 32, "little")


def verify(pub: bytes, message: bytes, signature: bytes) -> bool:
    if len(pub) != 32 or len(signature) != 64:
        return False
    try:
        a_point = _decompress(pub)
        r_point = _decompress(signature[:32])
    except (ValueError, IndexError):
        return False
    s = int.from_bytes(signature[32:], "little")
    if s >= L:
        return False
    k = _hash_mod_l(signature[:32] + pub + message)
    lhs = _mul(s, _B)
    rhs = _add(r_point, _mul(k, a_point))
    return _compress(lhs) == _compress(rhs)


def fingerprint(pub: bytes) -> str:
    """Short human-readable key fingerprint (SHA-256 of the public key)."""
    return hashlib.sha256(pub).hexdigest()[:16]