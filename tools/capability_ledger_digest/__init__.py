"""Capability-ledger auto audit with a digest-bound SQL evidence chain.

Public surface::

    from capability_ledger_digest import (
        AuditSpec, EvidenceRecord, Thresholds,
        classify, build_chain, SqlLedger, run_audit, verify_audit,
    )

The mechanism is domain-neutral: capability ids, metric values, thresholds and
expected labels are injected through a spec document.
"""
from __future__ import annotations

from .audit import (
    REPORT_SCHEMA_VERSION,
    load_report,
    report_markdown,
    run_audit,
    verify_audit,
    write_artifacts,
)
from .classifier import (
    RULES,
    RULE_IDS,
    Classification,
    classify,
    classify_all,
    known_label_score,
    level_counts,
    results_digest,
)
from .evidence_chain import (
    ChainVerdict,
    GateVerdict,
    build_chain,
    build_chains,
    chain_table,
)
from .spec import (
    GATES,
    LEVEL_RANK,
    LEVELS,
    REVIEW_VERDICTS,
    SCHEMA_VERSION,
    AuditSpec,
    EvidenceRecord,
    SpecError,
    Thresholds,
    canonical_json,
    fmt_number,
    sha256_json,
    sha256_text,
)
from .sql_ledger import (
    CAPABILITY_COLUMNS,
    GATE_COLUMNS,
    LEDGER_SCHEMA_VERSION,
    SCHEMA_SQL,
    SqlLedger,
)

__all__ = [
    "AuditSpec",
    "CAPABILITY_COLUMNS",
    "ChainVerdict",
    "Classification",
    "EvidenceRecord",
    "GATES",
    "GATE_COLUMNS",
    "GateVerdict",
    "LEDGER_SCHEMA_VERSION",
    "LEVELS",
    "LEVEL_RANK",
    "REPORT_SCHEMA_VERSION",
    "REVIEW_VERDICTS",
    "RULES",
    "RULE_IDS",
    "SCHEMA_SQL",
    "SCHEMA_VERSION",
    "SpecError",
    "SqlLedger",
    "Thresholds",
    "build_chain",
    "build_chains",
    "canonical_json",
    "chain_table",
    "classify",
    "classify_all",
    "fmt_number",
    "known_label_score",
    "level_counts",
    "load_report",
    "report_markdown",
    "results_digest",
    "run_audit",
    "sha256_json",
    "sha256_text",
    "verify_audit",
    "write_artifacts",
]