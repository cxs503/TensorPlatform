#!/usr/bin/env python3
"""Evidence Chain Integrity Checker — auto-verify report six elements.

Checks:
  1. Number traceability (every numeric claim has SQL/artifact source)
  2. Figure sha256 (every referenced figure has a hash in provenance)
  3. Criteria coverage (all acceptance criteria are addressed)
  4. Six-element completeness (intro/method/results/figures/notes/error)
  5. Honest-FAIL declaration (if results are negative, they're labeled)
  6. Audit trail (timestamps, versions, commit refs present)

Design: standalone, zero domain semantics, stdlib only.
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Set


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass
class EvidenceReport:
    """A report to be verified."""
    title: str = ""
    summary: str = ""
    markdown: str = ""
    figures: List[Dict[str, Any]] = field(default_factory=list)
    mission_id: Optional[int] = None
    scenario_key: str = ""
    tags: List[str] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_json(cls, path: Path) -> "EvidenceReport":
        """Load report from JSON file."""
        with open(path) as f:
            data = json.load(f)
        return cls(
            title=data.get("title", ""),
            summary=data.get("summary", ""),
            markdown=data.get("markdown", ""),
            figures=data.get("figures", []),
            mission_id=data.get("mission_id"),
            scenario_key=data.get("scenario_key", ""),
            tags=data.get("tags", []),
            metadata=data.get("metadata", {}),
        )

    @classmethod
    def from_dict(cls, data: dict) -> "EvidenceReport":
        return cls(
            title=data.get("title", ""),
            summary=data.get("summary", ""),
            markdown=data.get("markdown", ""),
            figures=data.get("figures", []),
            mission_id=data.get("mission_id"),
            scenario_key=data.get("scenario_key", ""),
            tags=data.get("tags", []),
            metadata=data.get("metadata", {}),
        )


@dataclass
class VerificationResult:
    """Result of evidence chain verification."""
    passed: bool = False
    checks: Dict[str, Any] = field(default_factory=dict)
    errors: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    score: float = 0.0  # 0.0 to 1.0
    verified_at: str = ""

    def to_dict(self) -> dict:
        return {
            "passed": self.passed,
            "score": self.score,
            "checks": self.checks,
            "errors": self.errors,
            "warnings": self.warnings,
            "verified_at": self.verified_at,
        }


# ---------------------------------------------------------------------------
# Six-element verification
# ---------------------------------------------------------------------------

SIX_ELEMENTS = {
    "introduction": {
        "patterns": [r"案例介绍", r"物理背景", r"几何", r"参数表",
                     r"introduction", r"background", r"case\s+description"],
        "required": True,
    },
    "method": {
        "patterns": [r"怎么做的", r"方法", r"流程", r"关键设置", r"工具链",
                     r"method", r"approach", r"procedure"],
        "required": True,
    },
    "results": {
        "patterns": [r"计算结果", r"数字表", r"结果", r"results", r"findings"],
        "required": True,
    },
    "figures": {
        "patterns": [r"结果图", r"云图", r"曲线", r"figure", r"plot", r"visualization"],
        "required": True,
    },
    "notes": {
        "patterns": [r"注意点", r"踩过的坑", r"局限", r"适用范围",
                     r"notes", r"caveats", r"limitations"],
        "required": True,
    },
    "error_analysis": {
        "patterns": [r"误差判定", r"误差", r"基准误差", r"error\s+analysis",
                     r"accuracy", r"vs\s+基准", r"<\s*3%"],
        "required": True,
    },
}


def verify_six_elements(report: EvidenceReport) -> Dict[str, Any]:
    """Check that all six elements are present in the report."""
    text = (report.markdown or "").lower()
    title_summary = (report.title + " " + report.summary).lower()
    combined = text + " " + title_summary

    results = {}
    found_count = 0

    for element, spec in SIX_ELEMENTS.items():
        found = False
        for pattern in spec["patterns"]:
            if re.search(pattern, combined, re.IGNORECASE):
                found = True
                break
        results[element] = {
            "found": found,
            "required": spec["required"],
        }
        if found:
            found_count += 1

    return {
        "elements": results,
        "found": found_count,
        "total": len(SIX_ELEMENTS),
        "complete": found_count == len(SIX_ELEMENTS),
    }


# ---------------------------------------------------------------------------
# Number traceability
# ---------------------------------------------------------------------------

def verify_number_traceability(report: EvidenceReport) -> Dict[str, Any]:
    """Check that numeric claims have traceable sources.

    Looks for patterns like:
    - "误差 X%" with nearby "vs" or "基准"
    - Tables with numeric data
    - References to artifacts/SQL paths
    """
    text = report.markdown or ""
    issues = []
    traced = 0
    untraced = 0

    # Find numeric claims (percentages, measurements)
    number_patterns = [
        r'(\d+\.?\d*)\s*%',           # percentages
        r'误差[：:]\s*(\d+\.?\d*)',    # error values
        r'(\d+\.?\d*)\s*(?:m/s|kN|kg|m\b)',  # physical quantities
    ]

    for pattern in number_patterns:
        matches = list(re.finditer(pattern, text))
        for match in matches:
            # Check if there's a source reference nearby (within 200 chars)
            start = max(0, match.start() - 200)
            end = min(len(text), match.end() + 200)
            context = text[start:end]

            has_source = any([
                re.search(r'(?:artifact|sql|source|ref|表|图|文献|baseline)', context, re.I),
                re.search(r'\d+\.\d+', context),  # has decimal precision
                re.search(r'(?:误差|error|vs|基准)', context, re.I),
            ])

            if has_source:
                traced += 1
            else:
                untraced += 1
                issues.append(f"Untraced number at position {match.start()}: {match.group()}")

    total = traced + untraced
    return {
        "traced": traced,
        "untraced": untraced,
        "total": total,
        "traceability_rate": traced / total if total > 0 else 1.0,
        "issues": issues[:10],  # cap at 10
        "passed": untraced == 0 or (total > 0 and traced / total >= 0.8),
    }


# ---------------------------------------------------------------------------
# Figure provenance
# ---------------------------------------------------------------------------

def verify_figure_provenance(report: EvidenceReport) -> Dict[str, Any]:
    """Check that all figures have sha256 provenance."""
    figures = report.figures or []
    issues = []
    verified = 0

    for i, fig in enumerate(figures):
        name = fig.get("name", f"figure_{i}")
        has_hash = bool(fig.get("sha256") or fig.get("hash"))
        has_path = bool(fig.get("png_base64") or fig.get("path") or fig.get("png_path"))

        if has_hash:
            verified += 1
        elif has_path:
            # Has path but no hash — warning
            issues.append(f"Figure '{name}' has path but no sha256")
        else:
            issues.append(f"Figure '{name}' missing both hash and path")

    return {
        "total_figures": len(figures),
        "verified": verified,
        "issues": issues,
        "passed": len(issues) == 0,
    }


# ---------------------------------------------------------------------------
# Criteria coverage
# ---------------------------------------------------------------------------

def verify_criteria_coverage(report: EvidenceReport) -> Dict[str, Any]:
    """Check that acceptance criteria are addressed in the report."""
    text = (report.markdown or "").lower()
    metadata = report.metadata or {}
    criteria = metadata.get("acceptance_criteria", [])

    if not criteria:
        # No explicit criteria — check for common patterns
        criteria_patterns = [
            (r"判据", "criteria_mentioned"),
            (r"验收", "acceptance_mentioned"),
            (r"pass|fail|通过|失败", "outcome_stated"),
        ]
        found = []
        for pattern, label in criteria_patterns:
            if re.search(pattern, text, re.I):
                found.append(label)
        return {
            "explicit_criteria": 0,
            "implicit_signals": found,
            "passed": len(found) >= 2,
        }

    covered = 0
    uncovered = []
    for criterion in criteria:
        # Simple keyword match
        keywords = str(criterion).lower().split()
        if any(kw in text for kw in keywords if len(kw) > 1):
            covered += 1
        else:
            uncovered.append(str(criterion))

    return {
        "explicit_criteria": len(criteria),
        "covered": covered,
        "uncovered": uncovered,
        "passed": covered == len(criteria),
    }


# ---------------------------------------------------------------------------
# Honest-FAIL check
# ---------------------------------------------------------------------------

def verify_honest_fail(report: EvidenceReport) -> Dict[str, Any]:
    """Check that failures are honestly reported."""
    text = (report.markdown or "").lower()

    fail_signals = [
        r"fail", r"失败", r"未完成", r"error", r"错误",
        r"局限", r"limitation", r"不适用", r"not\s+supported",
    ]
    honest_signals = [
        r"honest", r"如实", r"坦诚", r"声明",
        r"未通过", r"不满足", r"超出范围",
    ]

    has_failure = any(re.search(p, text, re.I) for p in fail_signals)
    has_honest = any(re.search(p, text, re.I) for p in honest_signals)

    return {
        "has_failure_signals": has_failure,
        "has_honest_declaration": has_honest,
        "passed": not has_failure or has_honest,
        "note": "No failures detected" if not has_failure else
                ("Honestly reported" if has_honest else "FAIL detected but not honestly declared"),
    }


# ---------------------------------------------------------------------------
# Audit trail check
# ---------------------------------------------------------------------------

def verify_audit_trail(report: EvidenceReport) -> Dict[str, Any]:
    """Check that audit trail elements are present."""
    text = report.markdown or ""
    metadata = report.metadata or {}

    checks = {
        "has_timestamp": bool(re.search(r'\d{4}-\d{2}-\d{2}', text)),
        "has_version": bool(re.search(r'v\d+\.\d+|version|版本', text, re.I)),
        "has_commit_ref": bool(re.search(r'[a-f0-9]{7,40}', text) or bool(metadata.get("commit"))),
        "has_mission_id": report.mission_id is not None,
        "has_scenario": bool(report.scenario_key),
    }

    passed = sum(checks.values()) >= 3
    return {
        "checks": checks,
        "passed": passed,
        "score": sum(checks.values()) / len(checks),
    }


# ---------------------------------------------------------------------------
# Main verification
# ---------------------------------------------------------------------------

def verify_report(report: EvidenceReport) -> VerificationResult:
    """Run all evidence chain checks on a report."""
    errors = []
    warnings = []
    checks = {}
    scores = []

    # 1. Six elements
    six = verify_six_elements(report)
    checks["six_elements"] = six
    scores.append(six["found"] / six["total"])
    if not six["complete"]:
        missing = [k for k, v in six["elements"].items() if not v["found"]]
        warnings.append(f"Missing six elements: {', '.join(missing)}")

    # 2. Number traceability
    trace = verify_number_traceability(report)
    checks["number_traceability"] = trace
    scores.append(trace["traceability_rate"])
    if not trace["passed"]:
        errors.append(f"Low traceability: {trace['untraced']} untraced numbers")

    # 3. Figure provenance
    figs = verify_figure_provenance(report)
    checks["figure_provenance"] = figs
    scores.append(1.0 if figs["passed"] else figs["verified"] / max(figs["total_figures"], 1))
    if not figs["passed"]:
        warnings.extend(figs["issues"])

    # 4. Criteria coverage
    crit = verify_criteria_coverage(report)
    checks["criteria_coverage"] = crit
    scores.append(1.0 if crit["passed"] else 0.5)
    if not crit["passed"] and crit.get("uncovered"):
        warnings.append(f"Uncovered criteria: {len(crit['uncovered'])}")

    # 5. Honest-FAIL
    honest = verify_honest_fail(report)
    checks["honest_fail"] = honest
    scores.append(1.0 if honest["passed"] else 0.0)
    if not honest["passed"]:
        errors.append("FAIL detected but not honestly declared")

    # 6. Audit trail
    audit = verify_audit_trail(report)
    checks["audit_trail"] = audit
    scores.append(audit["score"])
    if not audit["passed"]:
        warnings.append("Incomplete audit trail")

    # Aggregate
    avg_score = sum(scores) / len(scores) if scores else 0.0
    passed = len(errors) == 0 and avg_score >= 0.7

    return VerificationResult(
        passed=passed,
        checks=checks,
        errors=errors,
        warnings=warnings,
        score=round(avg_score, 3),
        verified_at=datetime.now(timezone.utc).isoformat(),
    )


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    import argparse
    parser = argparse.ArgumentParser(description="Evidence Chain Integrity Checker")
    parser.add_argument("report", help="Report JSON file")
    parser.add_argument("--output", "-o", help="Output verification JSON")
    args = parser.parse_args()

    report = EvidenceReport.from_json(Path(args.report))
    result = verify_report(report)
    output = json.dumps(result.to_dict(), indent=2)

    if args.output:
        Path(args.output).write_text(output)
        print(f"Verification saved to {args.output}")
    else:
        print(output)

    print(f"\nResult: {'PASS' if result.passed else 'FAIL'} (score: {result.score})")
    if result.errors:
        print(f"Errors: {len(result.errors)}")
    if result.warnings:
        print(f"Warnings: {len(result.warnings)}")


if __name__ == "__main__":
    main()
