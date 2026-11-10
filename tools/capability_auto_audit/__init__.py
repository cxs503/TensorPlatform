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

# ── p262w1-N07: 证据链驱动的自动升账/降级（append-only sha256 审计）────────────
# 新增子模块与上面的 IN63-N09 分类器互补：这里只做「证据链是否完整」的机器裁决
# 与等级状态机，全部判据由 ChainSpec 注入（零领域语义）。
from .evidence_chain import (  # noqa: E402
    ChainSpec,
    ChainVerdict,
    CheckResult,
    EvidenceChainValidator,
    ErrorGate,
    load_evidence,
    parse_junit_xml,
    parse_pytest_json,
)
from .level_machine import (  # noqa: E402
    DOWNGRADE,
    HOLD,
    UPGRADE,
    Transition,
    decide,
    transition_table,
)
from .append_only_log import AppendOnlyAuditLog, AuditEntry, AuditLogCorrupted  # noqa: E402
from .auto_audit_engine import AuditOutcome, AutoAuditEngine  # noqa: E402

__all__ += [
    "ChainSpec",
    "ChainVerdict",
    "CheckResult",
    "EvidenceChainValidator",
    "ErrorGate",
    "load_evidence",
    "parse_junit_xml",
    "parse_pytest_json",
    "Transition",
    "decide",
    "transition_table",
    "UPGRADE",
    "DOWNGRADE",
    "HOLD",
    "AppendOnlyAuditLog",
    "AuditEntry",
    "AuditLogCorrupted",
    "AuditOutcome",
    "AutoAuditEngine",
]
