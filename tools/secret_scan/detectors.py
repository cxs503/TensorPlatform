"""The three detection routes: patterns, Shannon entropy, known values.

Route separation is the point. A single regex library catches *shaped* secrets
(``AKIA…``, PEM headers, ``postgres://user:pw@…``) but misses a bare random key
pasted with no prefix; an entropy route catches exactly those bare keys but
floods on hashes; a known-value route catches the case that matters most
(a credential we already know has leaked) regardless of shape. Each route has
its own false-positive surface, so each is filterable on its own.

Every threshold and every pattern arrives from policy (``data/secret_scan/
policy.json``); this module contains no secret literals and no domain vocabulary.
"""

from __future__ import annotations

import base64
import binascii
import math
import re
from bisect import bisect_right
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple


def shannon_entropy(text: str) -> float:
    """Bits per character. Hex sha256 ≈ 3.7, random base64 ≈ 5.2."""
    if not text:
        return 0.0
    counts: Dict[str, int] = {}
    for ch in text:
        counts[ch] = counts.get(ch, 0) + 1
    total = float(len(text))
    return -sum((n / total) * math.log2(n / total) for n in counts.values())


class LineIndex:
    """Offset → 1-based line number (used by every detector for locatability)."""

    def __init__(self, text: str):
        self.starts = [0]
        for match in re.finditer("\n", text):
            self.starts.append(match.end())

    def line_of(self, offset: int) -> int:
        return bisect_right(self.starts, offset)


@dataclass(frozen=True)
class RawHit:
    """Internal hit: carries the value only inside the scanner process."""

    start: int
    end: int
    type: str
    detector: str
    value: str
    entropy: Optional[float] = None


@dataclass(frozen=True)
class PatternRule:
    id: str
    regex: "re.Pattern[str]"
    value_group: Optional[str]
    context_before: Optional["re.Pattern[str]"]
    needs_entropy: bool = False


def compile_rules(rules: Sequence[Dict[str, Any]]) -> List[PatternRule]:
    compiled: List[PatternRule] = []
    for rule in rules:
        try:
            regex = re.compile(str(rule["regex"]))
        except re.error as exc:  # pragma: no cover - policy validation path
            raise ValueError("bad_pattern:{}:{}".format(rule.get("id"), exc)) from exc
        value_group = "value" if "value" in regex.groupindex else None
        context = rule.get("context_before")
        compiled.append(
            PatternRule(
                id=str(rule["id"]),
                regex=regex,
                value_group=value_group,
                context_before=re.compile(str(context)) if context else None,
                needs_entropy=bool(rule.get("needs_entropy", False)),
            )
        )
    return compiled


def scan_patterns(text: str, rules: Iterable[PatternRule],
                  entropy_cfg: Optional[Dict[str, Any]] = None) -> List[RawHit]:
    """Shaped-secret route.

    A rule may declare ``needs_entropy``: the shape alone (e.g. ``NAME=value``)
    then only fires when the value also survives the entropy gate, which is what
    keeps context-anchored rules from flagging ordinary config paths.
    """
    hits: List[RawHit] = []
    for rule in rules:
        for match in rule.regex.finditer(text):
            if rule.value_group:
                start, end = match.span(rule.value_group)
                if start < 0 or end <= start:
                    continue
            else:
                start, end = match.span()
            if rule.context_before is not None:
                window = text[max(0, start - 400):start]
                if not rule.context_before.search(window):
                    continue
            value = text[start:end]
            if not value.strip():
                continue
            if rule.needs_entropy:
                ok, entropy = _entropy_ok(value, entropy_cfg or {})
                if not ok:
                    continue
                hits.append(RawHit(start=start, end=end, type=rule.id, detector="pattern",
                                   value=value, entropy=entropy))
                continue
            hits.append(RawHit(start=start, end=end, type=rule.id, detector="pattern", value=value))
    return hits


def _entropy_ok(token: str, cfg: Dict[str, Any]) -> Tuple[bool, float]:
    value = shannon_entropy(token)
    if value < float(cfg.get("min_bits_per_char", 4.0)):
        return False, value
    if cfg.get("require_mixed_case", True):
        if not (any(ch.islower() for ch in token) and any(ch.isupper() for ch in token)):
            return False, value
    if cfg.get("require_digit", True) and not any(ch.isdigit() for ch in token):
        return False, value
    digits = sum(1 for ch in token if ch.isdigit())
    ratio = digits / float(len(token))
    if ratio < float(cfg.get("min_digit_ratio", 0.0)) or ratio > float(cfg.get("max_digit_ratio", 1.0)):
        return False, value
    return True, value


def _letter_run_ratio(token: str) -> float:
    """Longest run of consecutive letters, divided by length.

    A random key is a soup of cases/digits; an identifier is mostly one long
    letter run (``artifactProvenanceChainBuilder…``). That asymmetry, not the
    entropy value, is what separates a bare key from a long identifier.
    """
    longest = 0
    current = 0
    for ch in token:
        if ch.isalpha():
            current += 1
            longest = max(longest, current)
        else:
            current = 0
    return longest / float(len(token)) if token else 0.0


def _decodes_to_prose(token: str, cfg: Dict[str, Any]) -> bool:
    """True when the token base64-decodes to readable text (encoded *content*).

    Prose base64 has high entropy and long letter runs, so it survives both other
    filters. But it *decodes* to language with spaces — that is transported
    content, not a credential: the decode layer scans inside it for real secrets,
    so the entropy route can safely stand down here.
    """
    if not cfg.get("exempt_decodable_prose", False):
        return False
    if not all(ch.isalnum() or ch in "+/=_-" for ch in token):
        return False
    padded = token + "=" * (-len(token) % 4)
    try:
        raw = base64.urlsafe_b64decode(padded.encode("ascii"))
    except (binascii.Error, ValueError, UnicodeError):
        return False
    if not raw:
        return False
    try:
        decoded = raw.decode("utf-8")
    except UnicodeDecodeError:
        return False
    if not decoded:
        return False
    printable = sum(1 for ch in decoded if ch.isprintable() or ch in "\r\n\t") / len(decoded)
    if printable < float(cfg.get("prose_min_printable_ratio", 0.95)):
        return False
    space_ratio = decoded.count(" ") / float(len(decoded))
    return space_ratio >= float(cfg.get("prose_min_space_ratio", 0.01))


def scan_entropy(text: str, cfg: Dict[str, Any]) -> List[RawHit]:
    if not cfg.get("enabled", True):
        return []
    max_run_ratio = float(cfg.get("max_letter_run_ratio", 1.0))
    alphabet = str(cfg.get("alphabet", "A-Za-z0-9+/=_-"))
    min_len = int(cfg.get("min_length", 20))
    max_len = int(cfg.get("max_length", 256))
    token_re = re.compile("[{}]{{{},}}".format(alphabet, min_len))
    hits: List[RawHit] = []
    for match in token_re.finditer(text):
        token = match.group(0)
        if len(token) > max_len:
            # Long opaque blobs (encoded figures, data dumps) are handled by the
            # pattern/decoder routes instead; the entropy route would flood here.
            continue
        if max_run_ratio < 1.0 and _letter_run_ratio(token) > max_run_ratio:
            continue
        if _decodes_to_prose(token, cfg):
            continue
        ok, value = _entropy_ok(token, cfg)
        if not ok:
            continue
        hits.append(
            RawHit(start=match.start(), end=match.end(), type="high_entropy_string",
                   detector="entropy", value=token, entropy=value)
        )
    return hits


def scan_known(text: str, values: Sequence[str]) -> List[RawHit]:
    """Exact match of in-memory known secret values (never persisted)."""
    hits: List[RawHit] = []
    for idx, value in enumerate(values):
        if not value:
            continue
        start = 0
        while True:
            found = text.find(value, start)
            if found < 0:
                break
            hits.append(
                RawHit(start=found, end=found + len(value), type="known_secret_value[{}]".format(idx),
                       detector="known_value", value=value)
            )
            start = found + len(value)
    return hits