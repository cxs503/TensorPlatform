"""Pipeline orchestrator: ties scanner → extractor → registrar → reporter.

This is the main entry point for running a complete multi-repo
capability discovery run.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .auto_registrar import AutoRegistrar, CapabilityLedger, RegistrationResult
from .config import DiscoveryConfig
from .diff_report import DiffReport, DiffReportGenerator
from .feature_extractor import CapabilityFeature, FeatureExtractor
from .multi_scanner import DiscoveryRecord, MultiScanner, ScanDiagnostics


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


@dataclass
class PipelineResult:
    """Result of a complete pipeline run.

    Attributes
    ----------
    run_id : str
        Unique run identifier.
    records : list[DiscoveryRecord]
        Raw discovery records.
    features : list[CapabilityFeature]
        Extracted capability features.
    results : list[RegistrationResult]
        Registration results.
    missing : list[dict]
        Missing capabilities.
    report : DiffReport
        Generated diff report.
    diagnostics : ScanDiagnostics
        Scan diagnostics.
    coverage : dict
        Coverage statistics.
    """
    run_id: str
    records: List[DiscoveryRecord]
    features: List[CapabilityFeature]
    results: List[RegistrationResult]
    missing: List[Dict[str, Any]]
    report: DiffReport
    diagnostics: ScanDiagnostics
    coverage: Dict[str, Any]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "run_id": self.run_id,
            "records_count": len(self.records),
            "features_count": len(self.features),
            "report": self.report.to_dict(),
            "coverage": self.coverage,
            "diagnostics": self.diagnostics.to_dict(),
        }


def run_pipeline(
    config: DiscoveryConfig,
    workspace_root: Path | str = ".",
    known_capabilities: Optional[List[str]] = None,
    capability_labels: Optional[Dict[str, str]] = None,
    run_id: Optional[str] = None,
) -> PipelineResult:
    """Run the complete capability discovery pipeline.

    Steps:
    1. Scan all repos for capability markers and patterns
    2. Extract features from raw records
    3. Register features to ledger (add/update)
    4. Generate diff report

    Parameters
    ----------
    config : DiscoveryConfig
        Discovery configuration.
    workspace_root : Path | str
        Workspace root for resolving paths.
    known_capabilities : list[str] | None
        Known capability IDs for coverage calculation.
    capability_labels : dict[str, str] | None
        Human-readable labels for capabilities.
    run_id : str | None
        Custom run ID (default: timestamp).

    Returns
    -------
    PipelineResult
        Complete pipeline result.
    """
    rid = run_id or now_iso()
    workspace = Path(workspace_root).resolve()

    # Step 1: Scan
    scanner = MultiScanner(config, workspace)
    records, diagnostics = scanner.scan_all()

    # Step 2: Extract features
    extractor = FeatureExtractor(capability_labels)
    features = extractor.extract(records)

    # Step 3: Register to ledger
    ledger_path = None
    if config.ledger_path:
        ledger_path = workspace / config.ledger_path
    ledger = CapabilityLedger(ledger_path)
    registrar = AutoRegistrar(ledger, min_confidence=config.min_confidence)
    results, missing = registrar.register_all(features)

    # Step 4: Verify chain
    chain_ok, chain_msg = ledger.verify_chain()

    # Step 5: Coverage
    coverage = {}
    if known_capabilities:
        coverage = extractor.extract_coverage_stats(features, known_capabilities)

    # Step 6: Generate report
    report_gen = DiffReportGenerator(run_id=rid)
    report = report_gen.generate(
        results=results,
        features=features,
        missing=missing,
        diagnostics=diagnostics,
        coverage=coverage,
        chain_ok=chain_ok,
        chain_message=chain_msg,
    )

    # Save ledger if path configured
    if ledger_path:
        ledger.save()

    # Save report if output dir configured
    if config.output_dir:
        output_dir = workspace / config.output_dir
        report_gen.save_report(report, output_dir)

    return PipelineResult(
        run_id=rid,
        records=records,
        features=features,
        results=results,
        missing=missing,
        report=report,
        diagnostics=diagnostics,
        coverage=coverage,
    )
