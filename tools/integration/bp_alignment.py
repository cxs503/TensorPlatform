"""BP Alignment — Campaign P180W1 vs BP §8.2 Ice Navigation Full-Process Demo.

Computes coverage of BP (Business Requirements Specification)
section 8.2 by campaign node deliverables.

C3: Coverage computable (given BP clause list, output covered/uncovered list)
C5-negative: empty BP clause list → coverage = 0
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any


# BP §8.2 Ice Navigation Full-Process Demonstration POC — clause list
BP_SECTION_8_2_CLAUSES = [
    {
        "id": "BP-8.2-01",
        "description": "Ice resistance prediction using validated empirical models (Lindqvist/Keinonen/Riska)",
        "required_capabilities": ["ice_resistance_empirical", "multi_model_comparison"],
        "priority": "high",
        "evidence_nodes": ["N01", "N02"],
    },
    {
        "id": "BP-8.2-02",
        "description": "Full-scale experimental validation against MT Uikku trial data",
        "required_capabilities": ["experimental_validation", "full_scale_data"],
        "priority": "high",
        "evidence_nodes": ["N02", "N08"],
    },
    {
        "id": "BP-8.2-03",
        "description": "Ice sheet structural analysis using FEM (Winkler foundation verified)",
        "required_capabilities": ["ice_fem_winkler", "analytical_verification"],
        "priority": "high",
        "evidence_nodes": ["N03"],
    },
    {
        "id": "BP-8.2-04",
        "description": "Ice belt local structure design per IACS Polar Class rules",
        "required_capabilities": ["iacs_pc_compliance", "pressure_area_curve"],
        "priority": "high",
        "evidence_nodes": ["N04"],
    },
    {
        "id": "BP-8.2-05",
        "description": "Broken ice field navigation simulation using DEM",
        "required_capabilities": ["dem_broken_ice", "channel_clearing"],
        "priority": "medium",
        "evidence_nodes": ["N05"],
    },
    {
        "id": "BP-8.2-06",
        "description": "Frazil ice rheology and sea water intake risk assessment",
        "required_capabilities": ["frazil_rheology", "clogging_risk"],
        "priority": "medium",
        "evidence_nodes": ["N06"],
    },
    {
        "id": "BP-8.2-07",
        "description": "Ship platform ice module test infrastructure (>=5 modules with tests)",
        "required_capabilities": ["test_infrastructure", "coverage_expansion"],
        "priority": "medium",
        "evidence_nodes": ["N07"],
    },
    {
        "id": "BP-8.2-08",
        "description": "High-fidelity to empirical calibration backflow with traceability",
        "required_capabilities": ["calibration_backflow", "sha256_traceability"],
        "priority": "high",
        "evidence_nodes": ["N08"],
    },
    {
        "id": "BP-8.2-09",
        "description": "Cross-node physical quantity consistency validation (<10% spread)",
        "required_capabilities": ["cross_validation", "consistency_check"],
        "priority": "high",
        "evidence_nodes": ["N09"],
    },
    {
        "id": "BP-8.2-10",
        "description": "Integrated full-process demonstration from ice resistance to route optimization",
        "required_capabilities": ["full_process_integration", "end_to_end_demo"],
        "priority": "high",
        "evidence_nodes": ["N10"],
    },
    {
        "id": "BP-8.2-11",
        "description": "Ice-induced vibration assessment (lock-in frequency prediction)",
        "required_capabilities": ["ice_vibration", "lock_in_prediction"],
        "priority": "medium",
        "evidence_nodes": [],
    },
    {
        "id": "BP-8.2-12",
        "description": "Polar route optimization with ice forecast integration",
        "required_capabilities": ["route_optimization", "ice_forecast"],
        "priority": "medium",
        "evidence_nodes": [],
    },
    {
        "id": "BP-8.2-13",
        "description": "Real-time ice load monitoring digital twin",
        "required_capabilities": ["digital_twin", "real_time_monitoring"],
        "priority": "low",
        "evidence_nodes": [],
    },
    {
        "id": "BP-8.2-14",
        "description": "Multi-year ice ridge crossing analysis",
        "required_capabilities": ["ice_ridge", "multi_year_ice"],
        "priority": "low",
        "evidence_nodes": [],
    },
    {
        "id": "BP-8.2-15",
        "description": "Ice class notation assignment automation",
        "required_capabilities": ["ice_class_auto", "notation_assignment"],
        "priority": "low",
        "evidence_nodes": [],
    },
    {
        "id": "BP-8.2-16",
        "description": "Green navigation efficiency in ice (emission optimization)",
        "required_capabilities": ["green_navigation_ice", "emission_optimization"],
        "priority": "medium",
        "evidence_nodes": [],
    },
]

# Map node IDs to their artifact subdirectory names
_NODE_SUBDIR_MAP = {
    "N01": "N01_reference_corpus",
    "N02": "N02_ice_resistance",
    "N03": "N03_ice_sheet_fem",
    "N04": "N04_ice_belt_fem",
    "N05": "N05_broken_ice_dem",
    "N06": "N06_frazil_lbm",
    "N07": "N07_ice_module_tests",
    "N08": "N08_calibration_backflow",
    "N09": "N09_acceptance_gate",
    "N10": "N10_integration",
}


def _detect_complete_nodes_from_disk(artifacts_dir: Path) -> set[str]:
    """Detect which nodes have complete artifacts by scanning disk."""
    complete = set()
    for node_id, subdir in _NODE_SUBDIR_MAP.items():
        node_path = artifacts_dir / subdir
        if not node_path.exists():
            continue
        json_files = list(node_path.glob("*.json"))
        png_files = list(node_path.glob("*.png"))
        if len(json_files) >= 1 and len(png_files) >= 1:
            complete.add(node_id)
    return complete


def compute_bp_alignment(
    bp_clauses: list[dict] | None = None,
    completeness_data: dict | None = None,
    node_artifacts_dir: str | Path | None = None,
) -> dict[str, Any]:
    """Compute BP §8.2 alignment coverage.

    Args:
        bp_clauses: List of BP clause dicts. If None, uses default.
        completeness_data: Output from artifact_collector. If None, tries file then disk.
        node_artifacts_dir: Directory containing node artifacts.

    Returns:
        Alignment report dict.
    """
    # C5-negative: empty BP clause list → coverage = 0
    if bp_clauses is None:
        bp_clauses = BP_SECTION_8_2_CLAUSES

    if not bp_clauses:
        return {
            "bp_section": "8.2",
            "bp_title": "Ice Navigation Full-Process Demonstration POC",
            "total_clauses": 0,
            "covered_clauses": 0,
            "partial_clauses": 0,
            "uncovered_clauses": 0,
            "coverage_rate": 0.0,
            "c3_pass": False,
            "clause_details": [],
            "note": "Empty BP clause list — coverage = 0 (C5 negative control)",
        }

    # Determine complete nodes: from completeness_data, file, or disk scan
    complete_nodes: set[str] = set()

    if completeness_data:
        for nr in completeness_data.get("node_reports", []):
            if nr.get("is_complete"):
                complete_nodes.add(nr["node_id"])
    else:
        # Try loading from file
        completeness_path = Path(__file__).parent.parent.parent / "artifacts" / "integration" / "completeness.json"
        if completeness_path.exists():
            with open(completeness_path) as f:
                completeness_data = json.load(f)
            for nr in completeness_data.get("node_reports", []):
                if nr.get("is_complete"):
                    complete_nodes.add(nr["node_id"])
        else:
            # Fallback: scan disk directly
            if node_artifacts_dir is None:
                node_artifacts_dir = Path(__file__).parent.parent.parent / "artifacts" / "campaign_p180w1"
            complete_nodes = _detect_complete_nodes_from_disk(Path(node_artifacts_dir))

    # Evaluate each clause
    clause_details = []
    covered = 0
    partial = 0
    uncovered = 0

    for clause in bp_clauses:
        evidence_nodes = clause.get("evidence_nodes", [])
        if not evidence_nodes:
            status = "uncovered"
            uncovered += 1
            coverage_pct = 0.0
        else:
            nodes_complete = [n for n in evidence_nodes if n in complete_nodes]

            if len(nodes_complete) == len(evidence_nodes):
                status = "covered"
                covered += 1
                coverage_pct = 100.0
            elif len(nodes_complete) > 0:
                status = "partial"
                partial += 1
                coverage_pct = len(nodes_complete) / len(evidence_nodes) * 100
            else:
                status = "uncovered"
                uncovered += 1
                coverage_pct = 0.0

        clause_details.append({
            "clause_id": clause["id"],
            "description": clause["description"],
            "priority": clause["priority"],
            "required_capabilities": clause["required_capabilities"],
            "evidence_nodes": evidence_nodes,
            "status": status,
            "coverage_pct": coverage_pct,
            "nodes_with_artifacts": [n for n in evidence_nodes if n in complete_nodes],
            "nodes_missing": [n for n in evidence_nodes if n not in complete_nodes],
        })

    total = len(bp_clauses)
    coverage_rate = (covered + 0.5 * partial) / total if total > 0 else 0.0

    result = {
        "bp_section": "8.2",
        "bp_title": "Ice Navigation Full-Process Demonstration POC",
        "total_clauses": total,
        "covered_clauses": covered,
        "partial_clauses": partial,
        "uncovered_clauses": uncovered,
        "coverage_rate": round(coverage_rate, 4),
        "coverage_rate_pct": round(coverage_rate * 100, 1),
        "c3_pass": total > 0 and coverage_rate > 0,
        "clause_details": clause_details,
        "covered_list": [c["clause_id"] for c in clause_details if c["status"] == "covered"],
        "partial_list": [c["clause_id"] for c in clause_details if c["status"] == "partial"],
        "uncovered_list": [c["clause_id"] for c in clause_details if c["status"] == "uncovered"],
        "priority_breakdown": {
            "high": {
                "total": sum(1 for c in bp_clauses if c["priority"] == "high"),
                "covered": sum(1 for c in clause_details if c["priority"] == "high" and c["status"] == "covered"),
            },
            "medium": {
                "total": sum(1 for c in bp_clauses if c["priority"] == "medium"),
                "covered": sum(1 for c in clause_details if c["priority"] == "medium" and c["status"] == "covered"),
            },
            "low": {
                "total": sum(1 for c in bp_clauses if c["priority"] == "low"),
                "covered": sum(1 for c in clause_details if c["priority"] == "low" and c["status"] == "covered"),
            },
        },
    }
    return result


def main(output_path: str | Path | None = None) -> dict:
    """Run BP alignment computation and save to JSON."""
    if output_path is None:
        output_path = Path(__file__).parent.parent.parent / "artifacts" / "integration" / "bp_alignment.json"

    result = compute_bp_alignment()

    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, 'w') as f:
        json.dump(result, f, indent=2, ensure_ascii=False)

    return result


if __name__ == "__main__":
    result = main()
    print(f"BP §8.2 Coverage: {result['coverage_rate_pct']}%")
    print(f"Covered: {result['covered_clauses']}/{result['total_clauses']}")
    print(f"Partial: {result['partial_clauses']}")
    print(f"Uncovered: {result['uncovered_clauses']}")
    print(f"C3 pass: {result['c3_pass']}")
