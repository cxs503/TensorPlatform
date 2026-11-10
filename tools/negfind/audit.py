"""CLI: audit negative findings from mission results.

Usage:
    python -m tools.negfind.audit --input missions.json [--output findings.json]

Input format (JSON array of missions):
    [
        {"id": 1, "node_id": "N01", "result": {"negative_findings": [...]}},
        ...
    ]

Output: extracted negative findings + duplicate check results.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List

# Allow running from repo root
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tools.negfind.extractor import NegativeFindingsExtractor, NegativeFinding
from tools.negfind.fingerprint import FingerprintGate, SimHash


def load_missions(input_path: str) -> List[Dict[str, Any]]:
    """Load missions from JSON file."""
    with open(input_path) as f:
        data = json.load(f)
    if isinstance(data, list):
        return data
    return [data]


def extract_findings(
    missions: List[Dict[str, Any]],
    repo: str = "unknown",
) -> List[NegativeFinding]:
    """Extract negative findings from missions."""
    extractor = NegativeFindingsExtractor()
    return extractor.extract_batch(missions, repo=repo)


def check_duplicates(
    findings: List[NegativeFinding],
    known_negated: List[Dict[str, Any]] | None = None,
) -> Dict[str, Any]:
    """Check findings for duplicates against known negated hypotheses.

    Args:
        findings: extracted negative findings
        known_negated: list of {"id": ..., "text": ...} for known negated hypotheses

    Returns:
        {
            "findings": [...],
            "duplicates": [...],
            "summary": {...}
        }
    """
    gate = FingerprintGate()

    # Register known negated hypotheses
    if known_negated:
        for item in known_negated:
            gate.register(item["id"], item["text"], item)

    # Check each finding
    results = []
    duplicates = []

    for finding in findings:
        # Build text from finding
        text = f"{finding.title} {finding.hypothesis}"
        passed, action, hits = gate.check(text)

        result = finding.as_dict()
        result["gate_action"] = action
        result["gate_hits"] = [{"id": h[0], "similarity": round(h[1], 4)} for h in hits]
        results.append(result)

        if action == "reject":
            duplicates.append(result)

    return {
        "findings": results,
        "duplicates": duplicates,
        "summary": {
            "total_findings": len(findings),
            "duplicates_found": len(duplicates),
            "unique_findings": len(findings) - len(duplicates),
        },
    }


def audit(
    missions: List[Dict[str, Any]],
    known_negated: List[Dict[str, Any]] | None = None,
    repo: str = "unknown",
) -> Dict[str, Any]:
    """Full audit pipeline: extract + duplicate check."""
    findings = extract_findings(missions, repo)
    return check_duplicates(findings, known_negated)


def main():
    parser = argparse.ArgumentParser(
        description="Audit negative findings from mission results"
    )
    parser.add_argument(
        "--input", "-i",
        required=True,
        help="Input JSON file with mission results",
    )
    parser.add_argument(
        "--known-negated", "-k",
        default=None,
        help="JSON file with known negated hypotheses",
    )
    parser.add_argument(
        "--output", "-o",
        default=None,
        help="Output JSON file (default: stdout)",
    )
    parser.add_argument(
        "--repo", "-r",
        default="unknown",
        help="Repository name for findings",
    )

    args = parser.parse_args()

    # Load missions
    missions = load_missions(args.input)

    # Load known negated hypotheses
    known_negated = None
    if args.known_negated:
        with open(args.known_negated) as f:
            known_negated = json.load(f)

    # Run audit
    result = audit(missions, known_negated, args.repo)

    # Output
    output_json = json.dumps(result, indent=2, ensure_ascii=False, default=str)
    if args.output:
        with open(args.output, "w") as f:
            f.write(output_json)
        print(f"Output written to {args.output}", file=sys.stderr)
    else:
        print(output_json)

    # Summary to stderr
    summary = result["summary"]
    print(
        f"\nSummary: {summary['total_findings']} findings, "
        f"{summary['duplicates_found']} duplicates, "
        f"{summary['unique_findings']} unique",
        file=sys.stderr,
    )


if __name__ == "__main__":
    main()
