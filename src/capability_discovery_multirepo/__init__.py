"""Multi-repo capability auto-discovery and registration.

This package orchestrates capability discovery across multiple solver
repositories (TensorLBM, TensorFEM, TensorDEM, TensorFVM, etc.),
extracts capability features, auto-registers them to the central
capability ledger, and generates diff reports for human review.

Architecture
------------
1. **MultiScanner**: Scans multiple repo roots for capability markers
   and evidence patterns.
2. **FeatureExtractor**: Aggregates raw discovery records into
   capability features with standardized metadata.
3. **AutoRegistrar**: Compares discovered features against the existing
   ledger and performs add/update operations with audit trail.
4. **DiffReportGenerator**: Produces human-readable reports of
   new/updated/missing capabilities.

Zero domain semantics: all capability names, patterns, and evidence
types come from configuration/registries, never hardcoded.
"""
from __future__ import annotations

__version__ = "1.0.0"
__all__ = [
    "MultiScanner",
    "FeatureExtractor",
    "AutoRegistrar",
    "DiffReportGenerator",
    "DiscoveryConfig",
    "RepoDescriptor",
]

from .config import DiscoveryConfig, RepoDescriptor
from .multi_scanner import MultiScanner
from .feature_extractor import FeatureExtractor
from .auto_registrar import AutoRegistrar
from .diff_report import DiffReportGenerator
