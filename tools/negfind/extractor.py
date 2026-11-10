"""Extract negative findings from terminal mission results.

Supports two extraction modes:
  1. Structured: mission result has explicit `negative_findings` field
  2. Regex fallback: parse report判据 tables for FAIL/REJECTED entries

Zero domain semantics: field names and patterns are configurable.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Sequence


@dataclass
class NegativeFinding:
    """A single negative finding extracted from a mission."""
    repo: str
    claim_fingerprint: str  # hash of the claim text
    evidence_ref: str  # reference to evidence artifact
    verdict: str  # "falsified", "failed", "blocked", "infeasible"
    title: str = ""
    hypothesis: str = ""
    source_mission_id: int | None = None
    source_node_id: str | None = None
    extracted_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    metadata: Dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict:
        return {
            "repo": self.repo,
            "claim_fingerprint": self.claim_fingerprint,
            "evidence_ref": self.evidence_ref,
            "verdict": self.verdict,
            "title": self.title,
            "hypothesis": self.hypothesis,
            "source_mission_id": self.source_mission_id,
            "source_node_id": self.source_node_id,
            "extracted_at": self.extracted_at.isoformat(),
            "metadata": self.metadata,
        }


# Default regex patterns for fallback extraction (configurable)
_DEFAULT_FAIL_PATTERNS = [
    r"(?i)\bFAIL(?:ED)?\b",
    r"(?i)\bREJECT(?:ED)?\b",
    r"(?i)\bFALSIFIED\b",
    r"(?i)\bNOT_SUPPORTED\b",
    r"(?i)\bSTUB\b",
    r"(?i)\bMOCK\b",
]

_DEFAULT_VERDICT_MAP = {
    "fail": "failed",
    "failed": "failed",
    "reject": "falsified",
    "rejected": "falsified",
    "falsified": "falsified",
    "not_supported": "infeasible",
    "stub": "failed",
    "mock": "failed",
}


@dataclass
class NegativeFindingsExtractor:
    """Extract negative findings from mission results.

    Configuration:
        structured_field: field name in mission result for structured findings
        fail_patterns: regex patterns for fallback extraction
        verdict_map: mapping from pattern match to verdict
        required_fields: fields required in structured findings
    """

    structured_field: str = "negative_findings"
    fail_patterns: List[str] = field(default_factory=lambda: list(_DEFAULT_FAIL_PATTERNS))
    verdict_map: Dict[str, str] = field(default_factory=lambda: dict(_DEFAULT_VERDICT_MAP))
    required_fields: List[str] = field(default_factory=lambda: ["claim_fingerprint", "evidence_ref", "verdict"])
    _compiled_patterns: List[re.Pattern] = field(default_factory=list, repr=False)

    def __post_init__(self):
        self._compiled_patterns = [re.compile(p) for p in self.fail_patterns]

    def extract_from_mission(
        self,
        mission_result: Dict[str, Any],
        *,
        mission_id: int | None = None,
        node_id: str | None = None,
        repo: str = "unknown",
    ) -> List[NegativeFinding]:
        """Extract negative findings from a mission result dict.

        Tries structured extraction first, falls back to regex.
        """
        findings = []

        # 1. Try structured extraction
        structured = mission_result.get(self.structured_field)
        if structured and isinstance(structured, list):
            for item in structured:
                finding = self._parse_structured(item, repo, mission_id, node_id)
                if finding:
                    findings.append(finding)
            if findings:
                return findings

        # 2. Fallback: regex scan of result text
        text_findings = self._extract_by_regex(mission_result, repo, mission_id, node_id)
        findings.extend(text_findings)

        return findings

    def _parse_structured(
        self,
        item: Dict[str, Any],
        repo: str,
        mission_id: int | None,
        node_id: str | None,
    ) -> Optional[NegativeFinding]:
        """Parse a structured negative finding entry."""
        if not isinstance(item, dict):
            return None

        # Check required fields
        for field_name in self.required_fields:
            if field_name not in item:
                return None

        return NegativeFinding(
            repo=item.get("repo", repo),
            claim_fingerprint=str(item["claim_fingerprint"]),
            evidence_ref=str(item["evidence_ref"]),
            verdict=str(item["verdict"]),
            title=str(item.get("title", "")),
            hypothesis=str(item.get("hypothesis", "")),
            source_mission_id=mission_id,
            source_node_id=node_id,
            metadata={k: v for k, v in item.items()
                      if k not in {"repo", "claim_fingerprint", "evidence_ref", "verdict",
                                   "title", "hypothesis"}},
        )

    def _extract_by_regex(
        self,
        mission_result: Dict[str, Any],
        repo: str,
        mission_id: int | None,
        node_id: str | None,
    ) -> List[NegativeFinding]:
        """Extract negative findings by regex scanning result text."""
        findings = []

        # Convert result to text for scanning
        text = json.dumps(mission_result, ensure_ascii=False, default=str)

        for pattern in self._compiled_patterns:
            for match in pattern.finditer(text):
                matched_text = match.group(0).lower()
                verdict = self.verdict_map.get(matched_text, "failed")

                # Extract context around match (±100 chars)
                start = max(0, match.start() - 100)
                end = min(len(text), match.end() + 100)
                context = text[start:end]

                # Create fingerprint from context
                import hashlib
                fingerprint = hashlib.sha256(context.encode()).hexdigest()[:16]

                findings.append(NegativeFinding(
                    repo=repo,
                    claim_fingerprint=fingerprint,
                    evidence_ref=f"mission:{mission_id}/regex_scan",
                    verdict=verdict,
                    title=f"Regex-detected: {matched_text}",
                    hypothesis=context[:200],
                    source_mission_id=mission_id,
                    source_node_id=node_id,
                    metadata={"extraction_method": "regex", "pattern": pattern.pattern},
                ))

        return findings

    def extract_batch(
        self,
        missions: Sequence[Dict[str, Any]],
        *,
        repo: str = "unknown",
    ) -> List[NegativeFinding]:
        """Extract negative findings from a batch of mission results."""
        all_findings = []
        for mission in missions:
            mission_id = mission.get("id") or mission.get("mission_id")
            node_id = mission.get("node_id")
            result = mission.get("result", mission)

            findings = self.extract_from_mission(
                result,
                mission_id=mission_id,
                node_id=node_id,
                repo=repo,
            )
            all_findings.extend(findings)

        return all_findings
