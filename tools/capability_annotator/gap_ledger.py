"""Gap ledger: compare scan results vs expected levels and generate priority-sorted gap list.

The gap ledger identifies:
- Missing capabilities: expected but not found
- Degraded capabilities: expected REAL but found MOCK/STUB/WITHHELD
- Unexpected capabilities: found but not expected

Priority sorting is based on:
1. Severity (REAL→MOCK is higher than REAL→WITHHELD)
2. Domain importance (safety-critical domains first)
3. Impact on platform self-diagnosis capability
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from tools.capability_annotator.registry import VALID_LEVELS
from tools.capability_annotator.scanner import ScanReport, ScanResult

# Level ordering for severity calculation
LEVEL_RANK = {"NOT_SUPPORTED": 0, "STUB": 1, "MOCK": 2, "WITHHELD": 3, "REAL": 4}

# Domain priority (safety-critical first)
DOMAIN_PRIORITY = {
    "stability": 1,
    "resistance": 2,
    "propulsion": 3,
    "structure": 4,
    "ice_navigation": 5,
    "seakeeping": 6,
    "manoeuvring": 7,
    "vibration_noise": 8,
    "green_navigation": 9,
}


@dataclass
class Gap:
    """Single gap entry."""
    capability_id: str
    domain: str
    expected_level: str
    detected_level: str
    gap_type: str  # "missing", "degraded", "unexpected"
    severity: int  # 1-5, higher is more severe
    priority: int  # 1-N, lower is higher priority
    description: str
    recommendation: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "capability_id": self.capability_id,
            "domain": self.domain,
            "expected_level": self.expected_level,
            "detected_level": self.detected_level,
            "gap_type": self.gap_type,
            "severity": self.severity,
            "priority": self.priority,
            "description": self.description,
            "recommendation": self.recommendation,
        }


@dataclass
class GapLedgerReport:
    """Complete gap ledger report."""
    schema_version: str = "2.0"
    generated_at: str = ""
    total_capabilities: int = 0
    gaps_found: int = 0
    gaps: list[Gap] = field(default_factory=list)
    summary: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "generated_at": self.generated_at,
            "total_capabilities": self.total_capabilities,
            "gaps_found": self.gaps_found,
            "gaps": [g.to_dict() for g in self.gaps],
            "summary": self.summary,
        }


class GapLedger:
    """Generate and maintain gap ledger from scan results."""

    def __init__(self, expected_levels: dict[str, str] | None = None):
        """Initialize with expected levels for each capability.

        Args:
            expected_levels: dict mapping capability_id to expected level.
                           If None, all capabilities expected to be REAL.
        """
        self.expected_levels = expected_levels or {}

    def analyze(self, scan_report: ScanReport, domain_map: dict[str, str] | None = None) -> GapLedgerReport:
        """Analyze scan results and generate gap ledger.

        Args:
            scan_report: Results from CapabilityScanner.scan_all()
            domain_map: Optional mapping of capability_id to domain

        Returns:
            GapLedgerReport with priority-sorted gaps
        """
        t0 = time.perf_counter()
        gaps = []
        domain_map = domain_map or {}

        # Build result lookup
        results_by_id = {r.capability_id: r for r in scan_report.results}

        # Check each capability
        for result in scan_report.results:
            cap_id = result.capability_id
            domain = domain_map.get(cap_id, "unknown")
            expected = self.expected_levels.get(cap_id, "REAL")  # Default expectation
            detected = result.level

            # Determine if there's a gap
            gap = self._identify_gap(cap_id, domain, expected, detected, result)
            if gap:
                gaps.append(gap)

        # Sort by priority
        gaps = self._sort_by_priority(gaps)

        # Assign priority numbers
        for i, gap in enumerate(gaps, 1):
            gap.priority = i

        # Build summary
        gap_type_tally = {}
        severity_tally = {}
        domain_tally = {}
        for g in gaps:
            gap_type_tally[g.gap_type] = gap_type_tally.get(g.gap_type, 0) + 1
            severity_tally[g.severity] = severity_tally.get(g.severity, 0) + 1
            domain_tally[g.domain] = domain_tally.get(g.domain, 0) + 1

        report = GapLedgerReport(
            generated_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            total_capabilities=scan_report.capability_count,
            gaps_found=len(gaps),
            gaps=gaps,
            summary={
                "gap_type_tally": gap_type_tally,
                "severity_tally": severity_tally,
                "domain_tally": domain_tally,
                "gap_rate": round(len(gaps) / scan_report.capability_count, 4) if scan_report.capability_count > 0 else 0.0,
                "critical_gaps": sum(1 for g in gaps if g.severity >= 4),
                "analysis_time_sec": round(time.perf_counter() - t0, 3),
            },
        )
        return report

    def _identify_gap(self, cap_id: str, domain: str, expected: str,
                      detected: str, result: ScanResult) -> Gap | None:
        """Identify if there's a gap between expected and detected levels."""
        expected_rank = LEVEL_RANK.get(expected, 3)
        detected_rank = LEVEL_RANK.get(detected, 0)

        # No gap if detected >= expected
        if detected_rank >= expected_rank:
            return None

        # Calculate severity based on gap size
        gap_size = expected_rank - detected_rank
        severity = min(5, max(1, gap_size + 2))

        # Determine gap type
        if detected == "NOT_SUPPORTED":
            gap_type = "missing"
            description = f"Capability {cap_id} is not implemented"
            recommendation = f"Implement {cap_id} with real algorithm and tests"
        elif detected in ("MOCK", "STUB"):
            gap_type = "degraded"
            description = f"Capability {cap_id} is {detected} (expected {expected})"
            recommendation = f"Replace {detected.lower()} implementation with real algorithm"
        else:  # WITHHELD
            gap_type = "degraded"
            description = f"Capability {cap_id} has insufficient evidence (expected {expected})"
            recommendation = f"Add tests with numeric assertions and verification artifacts"

        # Adjust severity by domain priority
        domain_priority = DOMAIN_PRIORITY.get(domain, 5)
        if domain_priority <= 3:  # Safety-critical domains
            severity = min(5, severity + 1)

        return Gap(
            capability_id=cap_id,
            domain=domain,
            expected_level=expected,
            detected_level=detected,
            gap_type=gap_type,
            severity=severity,
            priority=0,  # Will be assigned later
            description=description,
            recommendation=recommendation,
        )

    def _sort_by_priority(self, gaps: list[Gap]) -> list[Gap]:
        """Sort gaps by priority (severity desc, domain priority asc, gap_type)."""
        def sort_key(gap: Gap):
            domain_pri = DOMAIN_PRIORITY.get(gap.domain, 5)
            type_order = {"missing": 0, "degraded": 1, "unexpected": 2}
            return (-gap.severity, domain_pri, type_order.get(gap.gap_type, 3))

        return sorted(gaps, key=sort_key)

    def save(self, report: GapLedgerReport, path: str | Path):
        """Save gap ledger to JSON file."""
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w") as f:
            json.dump(report.to_dict(), f, ensure_ascii=False, indent=2)

    def diff(self, old_report: GapLedgerReport, new_report: GapLedgerReport) -> dict[str, Any]:
        """Generate diff between two gap ledger reports.

        Returns dict with:
        - new_gaps: gaps in new but not in old
        - resolved_gaps: gaps in old but not in new
        - changed_gaps: gaps with changed severity/level
        """
        old_gaps = {g.capability_id: g for g in old_report.gaps}
        new_gaps = {g.capability_id: g for g in new_report.gaps}

        new_gap_list = []
        resolved_gap_list = []
        changed_gap_list = []

        # Find new gaps
        for cap_id, gap in new_gaps.items():
            if cap_id not in old_gaps:
                new_gap_list.append(gap.to_dict())

        # Find resolved gaps
        for cap_id, gap in old_gaps.items():
            if cap_id not in new_gaps:
                resolved_gap_list.append(gap.to_dict())

        # Find changed gaps
        for cap_id in set(old_gaps.keys()) & set(new_gaps.keys()):
            old_gap = old_gaps[cap_id]
            new_gap = new_gaps[cap_id]
            if (old_gap.detected_level != new_gap.detected_level or
                old_gap.severity != new_gap.severity):
                changed_gap_list.append({
                    "capability_id": cap_id,
                    "old": old_gap.to_dict(),
                    "new": new_gap.to_dict(),
                })

        return {
            "new_gaps": new_gap_list,
            "resolved_gaps": resolved_gap_list,
            "changed_gaps": changed_gap_list,
            "summary": {
                "new_count": len(new_gap_list),
                "resolved_count": len(resolved_gap_list),
                "changed_count": len(changed_gap_list),
            },
        }
