"""The scan engine: one pass over text, three detection routes, one decode layer.

Order matters, and the order is a security property:

1. **Placeholder masking** — already-redacted regions are removed from
   consideration first, which is what makes redaction idempotent and stops the
   gate from re-reporting its own `<REDACTED:…>` markers.
2. **Known values** — cheap, exact, and never silenced by a whitelist class: if we
   already know a credential leaked, its shape is irrelevant.
3. **Shaped patterns** — vendor/PEM/JWT/DB-URL/Bearer/kv shapes from policy.
4. **Entropy** — bare keys with no shape, gated on mixed case + digit to keep
   digests and identifiers out.
5. **Decode layer** — base64/hex blobs are decoded (bounded depth) and re-scanned,
   because a payload can *transport* a secret rather than type one.
"""

from __future__ import annotations

from bisect import bisect_right
from typing import Any, Dict, Iterable, List, Optional, Sequence

from . import decoders
from .detectors import LineIndex, RawHit, compile_rules, scan_entropy, scan_known, scan_patterns
from .model import GateError, Hit, ScanResult, mask, value_fingerprint
from .policy import Policy
from .whitelist import Whitelist


class Scanner:
    def __init__(self, policy: Policy):
        self.policy = policy
        self.rules = compile_rules(policy.patterns)
        self.whitelist = Whitelist(policy.whitelist)
        self._placeholder_re = None
        pattern = policy.whitelist.get("placeholder_regex")
        if pattern:
            import re

            self._placeholder_re = re.compile(str(pattern))

    # --- public --------------------------------------------------------
    def scan_text(
        self,
        text: str,
        *,
        path: str = "",
        decode: Optional[bool] = None,
        apply_location_whitelist: bool = True,
    ) -> ScanResult:
        if not isinstance(text, str):
            raise GateError("scan_target_not_text")
        result = ScanResult(target=path or "<text>", scanned_chars=len(text))
        result.hits = self._scan(text, path=path, decode=decode,
                                apply_location_whitelist=apply_location_whitelist)
        return result

    def scan_value(self, obj: Any, *, path: str = "payload", decode: Optional[bool] = None,
                   apply_location_whitelist: bool = False) -> ScanResult:
        """Recursive scan of a JSON-ish payload; string leaves are scanned."""
        result = ScanResult(target=path)
        self._walk(obj, path, result, decode=decode,
                   apply_location_whitelist=apply_location_whitelist)
        return result

    def redact(self, text: str, *, path: str = "") -> tuple:
        """Return ``(redacted_text, ScanResult)`` with no hit left behind."""
        from .redact import redact_text

        scan = self.scan_text(text, path=path)
        redacted, applied = redact_text(
            text,
            scan.hits,
            template=str(self.policy.redaction.get("placeholder", "<REDACTED:{type}:{len}>")),
            max_type_chars=int(self.policy.redaction.get("max_type_chars", 32)),
        )
        return redacted, scan

    # --- internals -----------------------------------------------------
    def _walk(self, obj: Any, path: str, result: ScanResult, *, decode: Optional[bool],
              apply_location_whitelist: bool) -> None:
        if isinstance(obj, str):
            sub = self.scan_text(obj, path=path, decode=decode,
                                 apply_location_whitelist=apply_location_whitelist)
            result.hits.extend(sub.hits)
            result.scanned_chars += sub.scanned_chars
            result.decoded_blobs += sub.decoded_blobs
        elif isinstance(obj, dict):
            for key, value in obj.items():
                if isinstance(key, str):
                    sub = self.scan_text(key, path="{}.<key>".format(path), decode=decode,
                                         apply_location_whitelist=apply_location_whitelist)
                    result.hits.extend(sub.hits)
                self._walk(value, "{}.{}".format(path, key), result, decode=decode,
                           apply_location_whitelist=apply_location_whitelist)
        elif isinstance(obj, (list, tuple, set)):
            for idx, value in enumerate(obj):
                self._walk(value, "{}[{}]".format(path, idx), result, decode=decode,
                           apply_location_whitelist=apply_location_whitelist)
        elif isinstance(obj, (bytes, bytearray)):
            result.advisories.append({"code": "binary_leaf_skipped", "path": path})

    def _placeholder_spans(self, text: str) -> List[tuple]:
        if self._placeholder_re is None:
            return []
        return [match.span() for match in self._placeholder_re.finditer(text)]

    def _scan(self, text: str, *, path: str, decode: Optional[bool],
              apply_location_whitelist: bool, depth: int = 0) -> List[Hit]:
        line_index = LineIndex(text)
        keep = int(self.policy.output.get("preview_prefix_chars", 2))
        max_prev = int(self.policy.output.get("max_preview_chars", 12))
        max_hits = int(self.policy.output.get("max_hits_per_target", 200))

        blocked = self._placeholder_spans(text)
        raw: List[RawHit] = []
        raw.extend(scan_known(text, self.policy.known_values))
        raw.extend(scan_patterns(text, self.rules, self.policy.entropy))
        known_spans = [(h.start, h.end) for h in raw if h.detector == "known_value"]

        entropy_hits = scan_entropy(text, self.policy.entropy)
        for hit in entropy_hits:
            blocked_by = _overlaps(hit, known_spans) or _overlaps(hit, blocked)
            if blocked_by:
                continue
            if self.whitelist.value_class(hit.value):
                continue
            raw.append(hit)

        lines = text.splitlines()
        hits: List[Hit] = []
        for hit in raw:
            if _overlaps(hit, blocked):
                continue
            if hit.detector != "known_value" and _inside_value_class(self.whitelist, hit):
                continue
            line_no = line_index.line_of(hit.start)
            line_text = lines[line_no - 1] if 0 <= line_no - 1 < len(lines) else ""
            if apply_location_whitelist and hit.detector != "known_value":
                if self.whitelist.match_class(path, line_text):
                    continue
            hits.append(
                Hit(
                    type=hit.type,
                    detector=hit.detector,
                    path=path,
                    line=line_no,
                    start=hit.start,
                    end=hit.end,
                    length=hit.end - hit.start,
                    preview=mask(hit.value, prefix_chars=keep, max_chars=max_prev),
                    fingerprint=value_fingerprint(hit.value),
                    entropy=hit.entropy,
                    via="raw",
                )
            )

        want_decode = self.policy.decoders.get("enabled", True) if decode is None else decode
        if want_decode and depth < int(self.policy.decoders.get("max_depth", 1)):
            hits.extend(
                self._scan_decoded(
                    text,
                    path=path,
                    line_index=line_index,
                    depth=depth,
                    keep=keep,
                    max_prev=max_prev,
                    apply_location_whitelist=apply_location_whitelist,
                )
            )
        return hits[:max_hits]

    def _scan_decoded(self, text: str, *, path: str, line_index: LineIndex, depth: int,
                      keep: int, max_prev: int, apply_location_whitelist: bool) -> List[Hit]:
        hits: List[Hit] = []
        for start, end, via, decoded in decoders.iter_decoded(text, self.policy.decoders, depth=depth + 1):
            inner = self._scan(decoded, path=path, decode=False,
                               apply_location_whitelist=apply_location_whitelist, depth=depth + 1)
            for hit in inner:
                hits.append(
                    Hit(
                        type="decoded:{}".format(hit.type),
                        detector="decoded",
                        path=path,
                        line=line_index.line_of(start),
                        start=start,
                        end=end,
                        length=end - start,
                        preview=hit.preview,
                        fingerprint=hit.fingerprint,
                        entropy=hit.entropy,
                        via=via,
                    )
                )
        return hits


def _overlaps(hit: RawHit, spans: Sequence[tuple]) -> bool:
    for start, end in spans:
        if hit.start < end and hit.end > start:
            return True
    return False


def _inside_value_class(whitelist: Whitelist, hit: RawHit) -> bool:
    """Only the entropy route may be silenced by a value class."""
    if hit.detector != "entropy":
        return False
    return whitelist.value_class(hit.value) is not None