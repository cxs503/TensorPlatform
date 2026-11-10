"""Evidence debt extractor: parse report text to find WITHHELD declarations.

Zero-domain-semantics: the extractor uses only structural markers
(evidence-level keywords, capability-id patterns) that are domain-agnostic.
Domain-specific terms arrive via injected ``markers`` configuration.
"""
from __future__ import annotations

import re
import uuid
from typing import Any, Dict, List, Optional, Set

from .model import ExtractedDebt


# Default structural markers — these are NOT domain words; they are
# evidence-level taxonomy tokens defined by the platform itself.
_DEFAULT_WITHHELD_MARKERS = [
    r"\bWITHHELD\b",
    r"\bwithheld\b",
    r"evidence\s+level[:\s]+WITHHELD",
    r"level[:\s]+WITHHELD",
    r"no\s+(?:sufficient\s+)?evidence",
    r"unverified",
    r"not\s+(?:yet\s+)?validated",
    r"lacks?\s+(?:validation|verification|evidence|benchmark)",
    r"pending\s+(?:verification|validation|evidence)",
]

# Pattern to extract capability_id from context
_CAPABILITY_ID_PATTERN = re.compile(
    r"(?:capability|cap|slot|node)[\s_:]+([a-zA-Z0-9][\w.\-]*(?:\.[a-zA-Z0-9][\w.\-]*)*)"
)


class EvidenceDebtExtractor:
    """Extract WITHHELD declarations from report text.

    Parameters
    ----------
    markers : list of str, optional
        Regex patterns that signal a WITHHELD declaration. Defaults to
        platform-level structural markers (no domain vocabulary).
    """

    def __init__(self, markers: Optional[List[str]] = None):
        self.markers = markers or _DEFAULT_WITHHELD_MARKERS
        self._compiled = [re.compile(m, re.IGNORECASE) for m in self.markers]

    def extract(
        self,
        report_text: str,
        report_id: str = "",
        default_capability_id: str = "",
    ) -> List[ExtractedDebt]:
        """Extract all WITHHELD declarations from ``report_text``.

        Returns a list of ExtractedDebt objects. Each declaration is
        deduplicated by content digest.
        """
        if not report_text or not report_text.strip():
            return []

        report_id = report_id or f"rpt-{uuid.uuid4().hex[:12]}"
        debts: List[ExtractedDebt] = []
        seen_digests: Set[str] = set()

        # Split into sentences/paragraphs for context
        segments = self._split_segments(report_text)

        for segment in segments:
            for pattern in self._compiled:
                match = pattern.search(segment)
                if not match:
                    continue

                # Try to extract capability_id from surrounding context
                cap_id = self._extract_capability_id(segment, default_capability_id)
                if not cap_id:
                    continue

                debt = ExtractedDebt(
                    debt_id=f"debt-{uuid.uuid4().hex[:12]}",
                    capability_id=cap_id,
                    source_report_id=report_id,
                    declaration_text=segment.strip(),
                    context={
                        "matched_pattern": pattern.pattern,
                        "match_span": [match.start(), match.end()],
                    },
                )

                # Deduplicate by content digest
                if debt.digest not in seen_digests:
                    seen_digests.add(debt.digest)
                    debts.append(debt)
                break  # one match per segment is enough

        return debts

    def extract_from_structured(
        self,
        report_data: Dict[str, Any],
        report_id: str = "",
    ) -> List[ExtractedDebt]:
        """Extract debts from a structured report (dict with text fields).

        Looks for WITHHELD markers in string values and capability_id in keys.
        """
        report_id = report_id or report_data.get("report_id", "")
        debts: List[ExtractedDebt] = []
        seen_digests: Set[str] = set()

        # Check top-level capability declarations
        capabilities = report_data.get("capabilities", {})
        if isinstance(capabilities, dict):
            for cap_id, cap_info in capabilities.items():
                if isinstance(cap_info, dict):
                    level = str(cap_info.get("level", "")).upper()
                    if level == "WITHHELD":
                        debt = ExtractedDebt(
                            debt_id=f"debt-{uuid.uuid4().hex[:12]}",
                            capability_id=cap_id,
                            source_report_id=report_id,
                            declaration_text=f"capability {cap_id} level=WITHHELD",
                            context={"source": "structured_capabilities"},
                        )
                        if debt.digest not in seen_digests:
                            seen_digests.add(debt.digest)
                            debts.append(debt)

        # Check text fields
        text = report_data.get("text", "") or report_data.get("markdown", "")
        if text:
            text_debts = self.extract(text, report_id)
            for d in text_debts:
                if d.digest not in seen_digests:
                    seen_digests.add(d.digest)
                    debts.append(d)

        return debts

    def _split_segments(self, text: str) -> List[str]:
        """Split text into sentence-like segments for matching."""
        # Split on sentence boundaries
        segments = re.split(r'(?<=[.!?])\s+|\n+', text)
        return [s.strip() for s in segments if s.strip()]

    def _extract_capability_id(
        self, segment: str, default: str
    ) -> str:
        """Try to extract a capability_id from the segment text."""
        match = _CAPABILITY_ID_PATTERN.search(segment)
        if match:
            return match.group(1)
        return default
