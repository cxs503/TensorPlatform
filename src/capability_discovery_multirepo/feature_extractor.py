"""Feature extractor: aggregates discovery records into capability features.

Takes raw DiscoveryRecords from the MultiScanner and produces
CapabilityFeature objects with aggregated metadata, suitable for
ledger registration.

The extractor performs:
1. Grouping by capability_id
2. Evidence level reconciliation (conservative: lowest wins)
3. Parameter merging (union of all params)
4. Source deduplication
5. Outcome aggregation
"""
from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from .multi_scanner import DiscoveryRecord


# Evidence level hierarchy (lower index = weaker evidence)
LEVEL_HIERARCHY = ["NOT_SUPPORTED", "STUB", "MOCK", "WITHHELD", "REAL"]
LEVEL_RANK = {lv: i for i, lv in enumerate(LEVEL_HIERARCHY)}


def reconcile_level(levels: List[str]) -> str:
    """Reconcile multiple evidence levels using conservative rule.

    The weakest evidence wins: if any test claims MOCK, the capability
    is MOCK. If all claim REAL, it's REAL. Empty/missing levels
    default to WITHHELD.

    Parameters
    ----------
    levels : list[str]
        List of evidence level strings.

    Returns
    -------
    str
        Reconciled evidence level.
    """
    valid = [lv.upper() for lv in levels if lv and lv.upper() in LEVEL_RANK]
    if not valid:
        return "WITHHELD"
    # Conservative: minimum rank wins
    return min(valid, key=lambda lv: LEVEL_RANK.get(lv, 3))


def aggregate_outcomes(outcomes: List[str]) -> str:
    """Aggregate test outcomes into a single state.

    Priority: failed > error > skipped > passed > not_run

    Parameters
    ----------
    outcomes : list[str]
        List of test outcome strings.

    Returns
    -------
    str
        Aggregated outcome state.
    """
    present = {o for o in outcomes if o}
    if not present:
        return "not_run"
    priority = ["failed", "error", "skipped", "xfailed", "xpassed", "passed"]
    for state in priority:
        if state in present:
            return state
    return "not_run"


@dataclass
class CapabilityFeature:
    """Aggregated capability feature from multiple discovery records.

    Attributes
    ----------
    capability_id : str
        Unique capability identifier.
    label : str
        Human-readable label.
    evidence_level : str
        Reconciled evidence level.
    repos : list[str]
        Repositories where this capability was found.
    params : dict
        Merged parameters from all records.
    anchors : list[str]
        Union of all verification anchors.
    tags : list[str]
        Union of all tags.
    sources : list[dict]
        Source files with SHA-256 hashes.
    tests : list[dict]
        Test nodes with outcomes.
    discovery_types : list[str]
        Types of discovery (marker, pattern, etc.).
    outcomes : dict
        Outcome counts by state.
    confidence : float
        Confidence score (0.0-1.0) based on evidence breadth.
    """
    capability_id: str
    label: str = ""
    evidence_level: str = "WITHHELD"
    repos: List[str] = field(default_factory=list)
    params: Dict[str, Any] = field(default_factory=dict)
    anchors: List[str] = field(default_factory=list)
    tags: List[str] = field(default_factory=list)
    sources: List[Dict[str, str]] = field(default_factory=list)
    tests: List[Dict[str, Any]] = field(default_factory=list)
    discovery_types: List[str] = field(default_factory=list)
    outcomes: Dict[str, int] = field(default_factory=dict)
    confidence: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "capability_id": self.capability_id,
            "label": self.label,
            "evidence_level": self.evidence_level,
            "repos": list(self.repos),
            "params": dict(self.params),
            "anchors": list(self.anchors),
            "tags": list(self.tags),
            "sources": list(self.sources),
            "tests": list(self.tests),
            "discovery_types": list(self.discovery_types),
            "outcomes": dict(self.outcomes),
            "confidence": round(self.confidence, 4),
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "CapabilityFeature":
        return cls(
            capability_id=d["capability_id"],
            label=d.get("label", ""),
            evidence_level=d.get("evidence_level", "WITHHELD"),
            repos=list(d.get("repos") or []),
            params=dict(d.get("params") or {}),
            anchors=list(d.get("anchors") or []),
            tags=list(d.get("tags") or []),
            sources=list(d.get("sources") or []),
            tests=list(d.get("tests") or []),
            discovery_types=list(d.get("discovery_types") or []),
            outcomes=dict(d.get("outcomes") or {}),
            confidence=float(d.get("confidence", 0.0)),
        )

    def evidence_fingerprint(self) -> str:
        """Compute a fingerprint of the evidence (for change detection)."""
        body = {
            "capability_id": self.capability_id,
            "evidence_level": self.evidence_level,
            "anchors": sorted(self.anchors),
            "params": self.params,
            "sources": sorted(s.get("sha256", "") for s in self.sources),
            "tests": sorted(t.get("nodeid", "") for t in self.tests),
        }
        return hashlib.sha256(
            json.dumps(body, sort_keys=True).encode("utf-8")
        ).hexdigest()


class FeatureExtractor:
    """Extracts capability features from discovery records.

    Parameters
    ----------
    capability_labels : dict[str, str] | None
        Optional mapping of capability_id -> human-readable label.
    """

    def __init__(self, capability_labels: Optional[Dict[str, str]] = None):
        self.capability_labels = capability_labels or {}

    def extract(
        self, records: List[DiscoveryRecord]
    ) -> List[CapabilityFeature]:
        """Extract features from discovery records.

        Parameters
        ----------
        records : list[DiscoveryRecord]
            Raw discovery records from MultiScanner.

        Returns
        -------
        features : list[CapabilityFeature]
            Aggregated capability features.
        """
        # Group by capability_id
        groups: Dict[str, List[DiscoveryRecord]] = defaultdict(list)
        for rec in records:
            groups[rec.capability_id].append(rec)

        features: List[CapabilityFeature] = []
        for cap_id, recs in sorted(groups.items()):
            feature = self._extract_single(cap_id, recs)
            features.append(feature)

        return features

    def _extract_single(
        self, cap_id: str, records: List[DiscoveryRecord]
    ) -> CapabilityFeature:
        """Extract a single capability feature from its records."""
        # Collect unique repos
        repos = sorted(set(r.repo_name for r in records if r.repo_name))

        # Reconcile evidence level
        levels = [r.evidence_level for r in records if r.evidence_level]
        evidence_level = reconcile_level(levels)

        # Merge parameters (union; conflicts take first seen)
        params: Dict[str, Any] = {}
        for rec in records:
            for key, value in rec.params.items():
                if key not in params:
                    params[key] = value

        # Union of anchors and tags
        anchors = sorted(set(a for r in records for a in r.anchors if a))
        tags = sorted(set(t for r in records for t in r.tags if t))

        # Deduplicate sources
        sources_map: Dict[str, str] = {}
        for rec in records:
            key = f"{rec.repo_name}:{rec.source_file}"
            if key not in sources_map and rec.source_sha256:
                sources_map[key] = rec.source_sha256
        sources = [
            {"repo": k.split(":")[0], "path": k.split(":", 1)[1], "sha256": h}
            for k, h in sorted(sources_map.items())
        ]

        # Collect test outcomes
        tests: List[Dict[str, Any]] = []
        outcomes: Dict[str, int] = {}
        seen_nodes: set = set()
        for rec in records:
            nodeid = f"{rec.repo_name}:{rec.source_file}:{rec.lineno}"
            if nodeid in seen_nodes:
                continue
            seen_nodes.add(nodeid)
            outcome = rec.test_outcome or "not_run"
            outcomes[outcome] = outcomes.get(outcome, 0) + 1
            tests.append({
                "nodeid": nodeid,
                "repo": rec.repo_name,
                "source_file": rec.source_file,
                "lineno": rec.lineno,
                "outcome": outcome,
                "discovery_type": rec.discovery_type,
            })

        # Discovery types
        discovery_types = sorted(set(r.discovery_type for r in records if r.discovery_type))

        # Compute confidence
        confidence = self._compute_confidence(records, evidence_level, anchors)

        # Label
        label = self.capability_labels.get(cap_id, cap_id)

        return CapabilityFeature(
            capability_id=cap_id,
            label=label,
            evidence_level=evidence_level,
            repos=repos,
            params=params,
            anchors=anchors,
            tags=tags,
            sources=sources,
            tests=tests,
            discovery_types=discovery_types,
            outcomes=outcomes,
            confidence=confidence,
        )

    def _compute_confidence(
        self,
        records: List[DiscoveryRecord],
        evidence_level: str,
        anchors: List[str],
    ) -> float:
        """Compute confidence score based on evidence breadth.

        Factors:
        - Number of independent sources (repos)
        - Evidence level
        - Presence of verification anchors
        - Test outcomes (passed tests boost confidence)
        """
        score = 0.0

        # Base score from evidence level
        level_scores = {
            "REAL": 0.4, "WITHHELD": 0.2, "MOCK": 0.1,
            "STUB": 0.05, "NOT_SUPPORTED": 0.0,
        }
        score += level_scores.get(evidence_level, 0.1)

        # Multi-repo bonus (max 0.2)
        repos = set(r.repo_name for r in records if r.repo_name)
        score += min(0.2, len(repos) * 0.05)

        # Anchor bonus (max 0.2)
        if anchors:
            score += min(0.2, len(anchors) * 0.1)

        # Test outcome bonus (max 0.2)
        passed = sum(1 for r in records if r.test_outcome == "passed")
        total = sum(1 for r in records if r.test_outcome and r.test_outcome != "not_run")
        if total > 0:
            score += 0.2 * (passed / total)

        return min(1.0, score)

    def extract_coverage_stats(
        self,
        features: List[CapabilityFeature],
        known_capabilities: List[str],
    ) -> Dict[str, Any]:
        """Compute coverage statistics.

        Parameters
        ----------
        features : list[CapabilityFeature]
            Extracted features.
        known_capabilities : list[str]
            List of known capability IDs (from registry).

        Returns
        -------
        dict
            Coverage statistics including coverage ratio.
        """
        discovered_ids = {f.capability_id for f in features}
        known_set = set(known_capabilities)

        found = discovered_ids & known_set
        missing = known_set - discovered_ids
        extra = discovered_ids - known_set

        coverage = len(found) / len(known_set) if known_set else 0.0

        return {
            "known_count": len(known_set),
            "discovered_count": len(discovered_ids),
            "found_count": len(found),
            "missing_count": len(missing),
            "extra_count": len(extra),
            "coverage_ratio": round(coverage, 4),
            "found": sorted(found),
            "missing": sorted(missing),
            "extra": sorted(extra),
        }
