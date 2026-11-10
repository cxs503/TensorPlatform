"""Evidence Chain Integrity Auditor (P190-N07).

Automatically validates consistency between:
  - Test results (pytest outcomes per capability)
  - Capability declarations (ledger claimed_level)
  - Ledger entries (capability_ledger.json)

Detects breaks and generates machine-executable fix suggestions.

Finding types:
  - missing_test: capability has no mapped test module
  - overclaim: ledger claims REAL but tests indicate MOCK/STUB
  - stale_status: ledger status doesn't match test outcomes
  - insufficient_coverage: fewer than 3 test cases for a capability

Design constraints:
  - Zero domain semantics: auditor doesn't depend on any domain-specific knowledge
  - Fail-soft: missing mapping → WITHHELD, not crash
  - Idempotent: same state → same output across repeated scans
"""
from .auditor import EvidenceChainAuditor, AuditFinding, AuditReport
from .mapping_loader import MappingLoader, CapabilityTestMapping
from .report_generator import AuditReportGenerator

__all__ = [
    "EvidenceChainAuditor",
    "AuditFinding",
    "AuditReport",
    "MappingLoader",
    "CapabilityTestMapping",
    "AuditReportGenerator",
]
