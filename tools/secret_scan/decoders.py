"""Payload decoders: the report gate must see through one level of encoding.

A leak does not have to be typed as a leak. Report payloads routinely carry
base64 (figures, embedded blobs) and hex digests, so a secret can be *transported*
encoded and only become a secret after a decode step. This module turns a text
into ``(encoded_span, decoded_text, codec)`` candidates, bounded in depth and
size so that decoding can never become a denial-of-service surface.
"""

from __future__ import annotations

import base64
import binascii
import re
from typing import Dict, Iterator, List, Tuple

_B64_CHARS = re.compile(r"[A-Za-z0-9+/=]{4,}")
_B64URL_CHARS = re.compile(r"[A-Za-z0-9_-]{4,}")
_HEX_CHARS = re.compile(r"[0-9a-fA-F]{16,}")


def _printable_ratio(text: str) -> float:
    if not text:
        return 0.0
    good = sum(1 for ch in text if ch.isprintable() or ch in "\r\n\t")
    return good / len(text)


def _decode_base64(blob: str) -> str:
    padded = blob + "=" * (-len(blob) % 4)
    data = base64.b64decode(padded.encode("ascii"), validate=False)
    # strict decode: a "replace"-decoded binary blob would look printable
    # (U+FFFD is printable) and would swallow the span before the codec that
    # actually produced text gets a turn.
    return data.decode("utf-8")


def _decode_base64url(blob: str) -> str:
    padded = blob + "=" * (-len(blob) % 4)
    data = base64.urlsafe_b64decode(padded.encode("ascii"))
    return data.decode("utf-8")


def _decode_hex(blob: str) -> str:
    if len(blob) % 2:
        return ""
    data = binascii.unhexlify(blob.encode("ascii"))
    return data.decode("utf-8")


_CODECS = {
    "base64": (_B64_CHARS, _decode_base64),
    "base64url": (_B64URL_CHARS, _decode_base64url),
    "hex": (_HEX_CHARS, _decode_hex),
}


def candidate_blobs(text: str, cfg: Dict[str, object]) -> List[Tuple[int, int, str, str]]:
    """Find encoded blobs worth decoding: ``(start, end, codec, blob)``."""
    min_len = int(cfg.get("min_blob_length", 24))
    max_len = int(cfg.get("max_blob_length", 65536))
    max_blobs = int(cfg.get("max_blobs_per_target", 256))
    out: List[Tuple[int, int, str, str]] = []
    seen = set()
    for codec in cfg.get("codecs", ["base64"]):
        spec = _CODECS.get(str(codec))
        if spec is None:
            continue
        pattern, _ = spec
        for match in pattern.finditer(text):
            blob = match.group(0)
            if len(blob) < min_len or len(blob) > max_len:
                continue
            key = (match.start(), match.end(), str(codec))
            if key in seen:
                continue
            seen.add(key)
            out.append((match.start(), match.end(), str(codec), blob))
            if len(out) >= max_blobs * len(cfg.get("codecs", ["base64"])):
                break
    out.sort()
    return out[:max_blobs]


def decode_blob(codec: str, blob: str, cfg: Dict[str, object]) -> str:
    spec = _CODECS.get(codec)
    if spec is None:
        return ""
    min_ratio = float(cfg.get("min_printable_ratio", 0.9))
    try:
        decoded = spec[1](blob)
    except (binascii.Error, ValueError, UnicodeError):
        return ""
    if not decoded or "\ufffd" in decoded:
        return ""
    if _printable_ratio(decoded) < min_ratio:
        return ""
    return decoded


def iter_decoded(text: str, cfg: Dict[str, object], depth: int = 1) -> Iterator[Tuple[int, int, str, str]]:
    """Yield ``(start, end, via, decoded_text)`` for depth-1 decodes of ``text``."""
    if not cfg.get("enabled", True):
        return
    max_depth = int(cfg.get("max_depth", 1))
    if depth > max_depth:
        return
    done: List[Tuple[int, int]] = []
    for start, end, codec, blob in candidate_blobs(text, cfg):
        # One successful decode per region: a pure-hex span is also valid base64
        # alphabet, and base64 vs base64url match slightly different spans, so
        # try each declared codec until one yields text and then lock the region.
        if any(start < done_end and end > done_start for done_start, done_end in done):
            continue
        decoded = decode_blob(codec, blob, cfg)
        if not decoded:
            continue
        done.append((start, end))
        yield start, end, "{}:{}".format(codec, depth), decoded