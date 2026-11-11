"""Platform Capability Annotator — 21-capability evidence-level auto-scan, gap ledger & audit pipeline.

This module integrates the existing capability audit infrastructure into a unified
self-diagnosis system for the ship performance platform. It provides:

1. **Registry**: 21 capabilities (9 domains × 3 aspects + extras) with structured metadata
2. **Auto-scan engine**: Traverses worktree, uses AST + keyword analysis to determine evidence levels
3. **Gap ledger**: Compares scan results vs expected levels, generates priority-sorted gap list
4. **Audit pipeline**: CI-ready integration with diff reports
5. **Visualization**: Capability matrix heatmap, gap trend, scan accuracy figures

Evidence levels: REAL / MOCK / STUB / WITHHELD / NOT_SUPPORTED
"""
from tools.capability_annotator.registry import CapabilityRegistry, CAPABILITY_DOMAINS
from tools.capability_annotator.scanner import CapabilityScanner
from tools.capability_annotator.gap_ledger import GapLedger
from tools.capability_annotator.audit_pipeline import AuditPipeline

__all__ = [
    "CapabilityRegistry",
    "CAPABILITY_DOMAINS",
    "CapabilityScanner",
    "GapLedger",
    "AuditPipeline",
]
