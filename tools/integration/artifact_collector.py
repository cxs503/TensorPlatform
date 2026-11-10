"""Artifact Collector — Campaign P180W1 Integration Verification.

Collects artifacts from N01-N10 campaign nodes, validates completeness
(each node must have >=1 JSON + >=1 PNG), and produces completeness.json.

C1: 10 nodes artifacts/ complete rate 100% (each >=1 json + >=1 png)
C5-negative: missing node artifacts → integration failure with explicit annotation
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class NodeArtifactReport:
    """Report for a single campaign node."""
    node_id: str
    node_name: str
    directory: str
    json_files: list[str] = field(default_factory=list)
    png_files: list[str] = field(default_factory=list)
    other_files: list[str] = field(default_factory=list)
    results_data: dict[str, Any] = field(default_factory=dict)
    validation_data: dict[str, Any] = field(default_factory=dict)
    is_complete: bool = False
    errors: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "node_id": self.node_id,
            "node_name": self.node_name,
            "directory": self.directory,
            "json_files": self.json_files,
            "png_files": self.png_files,
            "other_files": self.other_files,
            "has_results_json": "results.json" in self.json_files,
            "has_validation_json": "validation.json" in self.json_files,
            "json_count": len(self.json_files),
            "png_count": len(self.png_files),
            "is_complete": self.is_complete,
            "errors": self.errors,
        }


# Expected nodes for campaign P180W1
EXPECTED_NODES = {
    "N01": ("N01_reference_corpus", "Reference Corpus - Ice Resistance Anchors"),
    "N02": ("N02_ice_resistance", "Ice Resistance Full Spectrum"),
    "N03": ("N03_ice_sheet_fem", "Ice Sheet Bending FEM - Winkler"),
    "N04": ("N04_ice_belt_fem", "Ice Belt Local Structure FEM"),
    "N05": ("N05_broken_ice_dem", "Broken Ice Field DEM"),
    "N06": ("N06_frazil_lbm", "Frazil Ice Rheology LBM"),
    "N07": ("N07_ice_module_tests", "Ice Module Test Surface"),
    "N08": ("N08_calibration_backflow", "Calibration Backflow"),
    "N09": ("N09_acceptance_gate", "Acceptance Gate Cross-Validation"),
    "N10": ("N10_integration", "Integration & BP Alignment"),
}


def collect_node_artifacts(base_dir: str | Path, node_id: str,
                           subdir: str, node_name: str) -> NodeArtifactReport:
    """Collect artifacts for a single node."""
    report = NodeArtifactReport(
        node_id=node_id,
        node_name=node_name,
        directory=str(Path(base_dir) / subdir),
    )

    node_path = Path(base_dir) / subdir
    if not node_path.exists():
        report.errors.append(f"Directory not found: {node_path}")
        report.is_complete = False
        return report

    # Scan files
    for f in sorted(node_path.iterdir()):
        if f.is_file():
            if f.suffix == '.json':
                report.json_files.append(f.name)
            elif f.suffix == '.png':
                report.png_files.append(f.name)
            else:
                report.other_files.append(f.name)

    # Validate completeness: >=1 json + >=1 png
    if len(report.json_files) < 1:
        report.errors.append("Missing JSON files (need >=1)")
    if len(report.png_files) < 1:
        report.errors.append("Missing PNG files (need >=1)")

    # Load results.json if present
    results_path = node_path / "results.json"
    if results_path.exists():
        try:
            with open(results_path) as f:
                report.results_data = json.load(f)
        except json.JSONDecodeError as e:
            report.errors.append(f"Invalid JSON in results.json: {e}")

    # Load validation.json if present
    val_path = node_path / "validation.json"
    if val_path.exists():
        try:
            with open(val_path) as f:
                report.validation_data = json.load(f)
        except json.JSONDecodeError as e:
            report.errors.append(f"Invalid JSON in validation.json: {e}")

    report.is_complete = (
        len(report.json_files) >= 1
        and len(report.png_files) >= 1
        and len(report.errors) == 0
    )
    return report


def collect_all(base_dir: str | Path) -> dict[str, Any]:
    """Collect artifacts for all expected nodes.

    Returns completeness report dict.
    """
    base_dir = Path(base_dir)
    reports: list[NodeArtifactReport] = []
    missing_nodes: list[str] = []

    for node_id, (subdir, name) in EXPECTED_NODES.items():
        report = collect_node_artifacts(base_dir, node_id, subdir, name)
        reports.append(report)
        if not report.is_complete and not report.errors:
            missing_nodes.append(node_id)

    total = len(EXPECTED_NODES)
    complete = sum(1 for r in reports if r.is_complete)
    incomplete = [r.node_id for r in reports if not r.is_complete]

    result = {
        "campaign": "P180W1",
        "campaign_name": "Ice Navigation Full-Process Demonstration",
        "total_nodes_expected": total,
        "nodes_complete": complete,
        "nodes_incomplete": incomplete,
        "completeness_rate": complete / total if total > 0 else 0.0,
        "c1_pass": complete == total,
        "node_reports": [r.to_dict() for r in reports],
        "summary": {
            "total_json_files": sum(len(r.json_files) for r in reports),
            "total_png_files": sum(len(r.png_files) for r in reports),
            "all_validations_pass": all(
                r.validation_data.get("overall_pass", False)
                for r in reports
                if r.validation_data
            ),
        }
    }
    return result


def main(output_path: str | Path | None = None,
         base_dir: str | Path | None = None) -> dict:
    """Run artifact collection and optionally save to file."""
    if base_dir is None:
        base_dir = Path(__file__).parent.parent.parent / "artifacts" / "campaign_p180w1"
    result = collect_all(base_dir)

    if output_path is None:
        output_path = Path(__file__).parent.parent.parent / "artifacts" / "integration" / "completeness.json"
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    with open(output_path, 'w') as f:
        json.dump(result, f, indent=2, ensure_ascii=False)

    return result


if __name__ == "__main__":
    result = main()
    print(f"Completeness: {result['completeness_rate']:.1%}")
    print(f"C1 pass: {result['c1_pass']}")
    if result['nodes_incomplete']:
        print(f"Incomplete nodes: {result['nodes_incomplete']}")
