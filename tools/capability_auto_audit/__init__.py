"""Capability Auto-Audit System (IN63-N09).

Automated evidence-level classification, SQL-based provenance tracing,
capability ledger auto-update, audit logging with rollback, and
paper-quality report generation for the ship performance platform's
21 capabilities.

Modules
-------
provenance_db     SQLite provenance store for accuracy numbers (zero-hardcode)
evidence_classifier  Auto-classify evidence levels (REAL/MOCK/STUB/WITHHELD/NOT_SUPPORTED)
ledger_updater    Auto-update capability ledger after verification runs
audit_logger      Audit logging with before/after tracking and rollback
report_generator  Paper-quality six-element report auto-generation
cli               Command-line interface
"""
from .provenance_db import ProvenanceDB
from .evidence_classifier import EvidenceClassifier
from .ledger_updater import LedgerUpdater
from .audit_logger import AuditLogger
from .report_generator import ReportGenerator

__all__ = [
    "ProvenanceDB",
    "EvidenceClassifier",
    "LedgerUpdater",
    "AuditLogger",
    "ReportGenerator",
]
