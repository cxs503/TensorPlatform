"""Redaction: the automatic rewrite makes the gate *repair* what it caught.

The redactor is the mechanism half of the node: detection alone leaves the
operator to hand-edit (and hand-editing secrets is how they end up in a shell
history). The rewrite is defined so that repeated application is a no-op:
existing ``<REDACTED:type:len>`` placeholders are masked out of the scan, so
``redact(redact(x)) == redact(x)`` byte for byte.
"""

from __future__ import annotations

from typing import Iterable, List, Sequence, Tuple

from .detectors import RawHit
from .model import Hit


def _priority(hit: Hit) -> int:
    """Known values outrank shaped patterns, which outrank raw entropy."""
    if hit.detector == "known_value":
        return 0
    if hit.detector == "pattern":
        return 1
    if hit.detector == "decoded":
        return 2
    return 3


def select_spans(hits: Sequence[Hit]) -> List[Hit]:
    """Greedy non-overlapping span selection (deterministic)."""
    ordered = sorted(hits, key=lambda h: (_priority(h), -(h.end - h.start), h.start, h.type))
    chosen: List[Hit] = []
    for hit in ordered:
        if any(not (hit.end <= other.start or hit.start >= other.end) for other in chosen):
            continue
        chosen.append(hit)
    chosen.sort(key=lambda h: h.start)
    return chosen


def placeholder_for(hit_type: str, length: int, template: str, max_type_chars: int) -> str:
    safe_type = "".join(ch if ch.isalnum() or ch in "._-" else "_" for ch in hit_type)[:max_type_chars]
    return template.format(type=safe_type or "secret", len=length)


def redact_text(
    text: str,
    hits: Iterable[Hit],
    *,
    template: str = "<REDACTED:{type}:{len}>",
    max_type_chars: int = 32,
) -> Tuple[str, List[Tuple[int, int, str]]]:
    """Return ``(redacted_text, applied_spans)``; idempotent by construction."""
    spans = select_spans(list(hits))
    if not spans:
        return text, []
    out: List[str] = []
    cursor = 0
    applied: List[Tuple[int, int, str]] = []
    for hit in spans:
        out.append(text[cursor:hit.start])
        placeholder = placeholder_for(hit.type, hit.end - hit.start, template, max_type_chars)
        out.append(placeholder)
        applied.append((hit.start, hit.end, placeholder))
        cursor = hit.end
    out.append(text[cursor:])
    return "".join(out), applied


def redact_raw(text: str, raw_hits: Sequence[RawHit], **kwargs) -> Tuple[str, int]:
    """Convenience wrapper for the detector-level (pre-whitelist) path."""
    pseudo = [
        Hit(type=raw.type, detector=raw.detector, path="", line=0, start=raw.start, end=raw.end,
            length=raw.end - raw.start, preview="", fingerprint="", entropy=raw.entropy)
        for raw in raw_hits
    ]
    redacted, applied = redact_text(text, pseudo, **kwargs)
    return redacted, len(applied)