"""Data types shared by the scanner, the three exits and the acceptance runner.

A ``Hit`` is deliberately lossy: it can never carry the matched value, so a
verdict/artifact/ledger entry is safe to archive. That is what makes the scanner
itself non-leaky (invariant C6).
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


class GateError(RuntimeError):
    """Raised when a gate cannot decide (fail-closed: caller must BLOCK)."""


def value_fingerprint(value: str) -> str:
    """sha256 of a matched value — lets operators correlate hits without seeing them."""
    return hashlib.sha256(value.encode("utf-8", "surrogatepass")).hexdigest()


def mask(value: str, *, prefix_chars: int = 2, max_chars: int = 12) -> str:
    """Emit ``ab***…(len=42)``: prefix + fixed mask + length, never the value."""
    head = value[: max(0, prefix_chars)]
    text = "{}{}…(len={})".format(head, "*" * min(6, max(1, len(value) - len(head))), len(value))
    return text[:max_chars] if max_chars else text


@dataclass(frozen=True)
class Hit:
    type: str
    detector: str
    path: str
    line: int
    start: int
    end: int
    length: int
    preview: str
    fingerprint: str
    entropy: Optional[float] = None
    via: str = "raw"

    @property
    def confidence(self) -> str:
        """``high`` = shaped/known/decoded secret; ``low`` = bare entropy candidate.

        Character statistics alone cannot tell a bare random key from a long
        path or slug, so raw entropy hits are reported as candidates rather than
        as established exposures. They are still detected (that is what the
        injection calibration measures), but the exposure list separates them.
        """
        return "low" if self.detector == "entropy" else "high"

    def to_dict(self) -> Dict[str, Any]:
        out: Dict[str, Any] = {
            "type": self.type,
            "confidence": self.confidence,
            "detector": self.detector,
            "path": self.path,
            "line": self.line,
            "length": self.length,
            "preview": self.preview,
            "value_sha256": self.fingerprint,
            "via": self.via,
        }
        if self.entropy is not None:
            out["entropy"] = round(self.entropy, 4)
        return out


@dataclass
class ScanResult:
    """Outcome of scanning one target (text, file, diff or payload)."""

    target: str = ""
    hits: List[Hit] = field(default_factory=list)
    scanned_chars: int = 0
    decoded_blobs: int = 0
    files: List[Dict[str, Any]] = field(default_factory=list)
    advisories: List[Dict[str, Any]] = field(default_factory=list)

    def extend(self, other: "ScanResult") -> "ScanResult":
        self.hits.extend(other.hits)
        self.scanned_chars += other.scanned_chars
        self.decoded_blobs += other.decoded_blobs
        self.advisories.extend(other.advisories)
        return self

    @property
    def clean(self) -> bool:
        return not self.hits

    def by_confidence(self) -> Dict[str, int]:
        counts: Dict[str, int] = {"high": 0, "low": 0}
        for hit in self.hits:
            counts[hit.confidence] = counts.get(hit.confidence, 0) + 1
        return counts

    def by_type(self) -> Dict[str, int]:
        counts: Dict[str, int] = {}
        for hit in self.hits:
            counts[hit.type] = counts.get(hit.type, 0) + 1
        return counts

    def to_dict(self, *, max_hits: int = 200) -> Dict[str, Any]:
        shown = self.hits[:max_hits]
        return {
            "target": self.target,
            "clean": self.clean,
            "hit_count": len(self.hits),
            "hits_truncated": len(self.hits) > len(shown),
            "by_type": self.by_type(),
            "by_confidence": self.by_confidence(),
            "scanned_chars": self.scanned_chars,
            "decoded_blobs": self.decoded_blobs,
            "files": self.files,
            "advisories": self.advisories,
            "hits": [h.to_dict() for h in shown],
        }


@dataclass
class Verdict:
    """Gate decision. ``decision`` is ``allow`` / ``block`` / ``unknown``."""

    exit_name: str
    decision: str
    scan: ScanResult
    policy_sha256: str = ""
    redacted: Optional[str] = None
    extra: Dict[str, Any] = field(default_factory=dict)

    @property
    def blocked(self) -> bool:
        return self.decision != "allow"

    def to_dict(self, *, max_hits: int = 200) -> Dict[str, Any]:
        body: Dict[str, Any] = {
            "schema": "tp.secret_scan.verdict/v1",
            "exit": self.exit_name,
            "decision": self.decision,
            "verdict": "FAIL" if self.blocked else "PASS",
            "policy_sha256": self.policy_sha256,
            "scan": self.scan.to_dict(max_hits=max_hits),
        }
        body.update(self.extra)
        body["digest"] = verdict_digest(body)
        return body


def verdict_digest(body: Dict[str, Any]) -> str:
    """Content-addressed digest (no wall clock) so a verdict is recomputable."""
    import json

    payload = {k: v for k, v in body.items() if k != "digest"}
    blob = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()