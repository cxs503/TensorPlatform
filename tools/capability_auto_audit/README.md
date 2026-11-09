# Capability Auto-Audit System (IN63-N09)

Automated evidence-level classification, SQL-based provenance tracing,
capability ledger auto-update, audit logging with rollback, and
paper-quality report generation for the ship performance platform's
21 capabilities.

## Overview

This system implements the IN63-N09 milestone: **成熟化：验证面证据链与精度台账自动审计**.

### Components

| Module | Purpose |
|--------|---------|
| `provenance_db.py` | SQLite provenance store for accuracy numbers (zero-hardcode) |
| `evidence_classifier.py` | Auto-classify evidence levels (REAL/MOCK/STUB/WITHHELD/NOT_SUPPORTED) |
| `ledger_updater.py` | Auto-update capability ledger after verification runs |
| `audit_logger.py` | Audit logging with before/after tracking and rollback |
| `report_generator.py` | Paper-quality six-element report auto-generation |
| `cli.py` | Command-line interface |

### Evidence Levels

| Level | Meaning |
|-------|---------|
| REAL | Real algorithm with verified accuracy (error ≤ 3%) |
| MOCK | Hardcoded/random values pretending to be physics |
| STUB | Empty shell (NotImplementedError, placeholder) |
| WITHHELD | Insufficient evidence to classify |
| NOT_SUPPORTED | No implementation exists |

## Usage

### Full Audit Run

```bash
python3 -m tools.capability_auto_audit.cli run --run-id my_audit_run
```

This will:
1. Classify all 21 capabilities
2. Snapshot the current ledger
3. Update the ledger with new classifications
4. Log all changes to the audit trail
5. Generate a paper-quality audit report

### Individual Commands

```bash
# Classify a single capability
python3 -m tools.capability_auto_audit.cli classify calc.resistance

# Query accuracy data (SQL-sourced)
python3 -m tools.capability_auto_audit.cli query calc.resistance

# Validate classifier against known labels
python3 -m tools.capability_auto_audit.cli validate

# Rollback ledger to a previous run
python3 -m tools.capability_auto_audit.cli rollback <run_id>

# Show system summary
python3 -m tools.capability_auto_audit.cli summary

# Negative control test
python3 -m tools.capability_auto_audit.cli negative-control
```

## Acceptance Criteria

All criteria from the IN63-N09 task specification are met:

### (a) Evidence Level Classification
- ✅ Known MOCK modules correctly identified as MOCK
- ✅ Known REAL modules correctly identified as REAL
- ✅ Multi-signal approach: code/test/verify/mock/stub patterns

### (b) SQL Provenance Tracing
- ✅ All accuracy numbers stored in SQLite with full provenance chain
- ✅ Query interface enforces SQL-only access (zero-hardcode)
- ✅ Each record has: source_file, line_no, content_hash, context

### (c) Ledger Auto-Update
- ✅ Updates complete within 5 seconds
- ✅ Automatic backup before each update
- ✅ Tracks: evidence_level, accuracy_pct, last_verified_utc

### (d) Audit Logging
- ✅ Complete before/after records for every change
- ✅ Snapshot support for rollback to any historical version
- ✅ Integrity verification (sha256 hashes)

### (e) Negative Control
- ✅ Empty/non-existent modules return NOT_SUPPORTED
- ✅ No crashes on missing data

### (f) Report Generation
- ✅ Paper-quality six-element format (案例介绍/方法/结果/图/注意点/误差判定)
- ✅ All accuracy numbers SQL-sourced
- ✅ Report payload formatted for API submission

## Testing

```bash
# Run all tests
python3 -m pytest tests/test_capability_auto_audit.py -v

# Run specific test class
python3 -m pytest tests/test_capability_auto_audit.py::TestProvenanceDB -v
```

24 tests covering all acceptance criteria.

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                    Capability Auto-Audit                     │
├─────────────────────────────────────────────────────────────┤
│                                                              │
│  ┌──────────────┐    ┌──────────────┐    ┌──────────────┐  │
│  │  Evidence    │    │  Provenance  │    │   Ledger     │  │
│  │  Classifier  │───▶│     DB       │───▶│   Updater    │  │
│  └──────────────┘    └──────────────┘    └──────────────┘  │
│         │                    │                    │          │
│         ▼                    ▼                    ▼          │
│  ┌──────────────┐    ┌──────────────┐    ┌──────────────┐  │
│  │   Report     │    │    Audit     │    │   Rollback   │  │
│  │  Generator   │    │   Logger     │    │   Support    │  │
│  └──────────────┘    └──────────────┘    └──────────────┘  │
│                                                              │
└─────────────────────────────────────────────────────────────┘
```

## Data Flow

1. **Classification**: Source code scanned → signals extracted → level assigned
2. **Provenance**: Accuracy numbers extracted → stored with source chain → SQL-queryable
3. **Ledger**: Classification results + accuracy data → ledger updated → backup created
4. **Audit**: Every change logged → before/after recorded → snapshot saved
5. **Report**: All data aggregated → six-element report generated → API-ready payload

## Zero-Hardcode Policy

All accuracy numbers MUST come from SQL queries against the provenance database.
Direct Python access to accuracy values is prohibited. This ensures:
- Full traceability to source files
- Immutable audit trail
- Reproducible results

## Files Created

```
tools/capability_auto_audit/
├── __init__.py
├── provenance_db.py       # SQL provenance store
├── evidence_classifier.py # Auto-classification engine
├── ledger_updater.py      # Ledger auto-update
├── audit_logger.py        # Audit logging + rollback
├── report_generator.py    # Paper-quality reports
└── cli.py                 # Command-line interface

tests/
└── test_capability_auto_audit.py  # 24 comprehensive tests

artifacts/
├── provenance/
│   ├── provenance.db      # Accuracy provenance database
│   └── audit_log.db       # Audit log database
├── capability_ledger/
│   ├── capability_ledger.json  # Auto-updated ledger
│   └── backups/           # Historical snapshots
└── capability_reports/
    └── audit_report_*.json     # Generated reports
```

## Integration with Existing Infrastructure

This system integrates with:
- `tools/audit/capability_audit_scan.py` — scanning kernel
- `tools/audit/capability_ledger.py` — ledger reconciliation
- `artifacts/capability_ledger/` — existing ledger directory

The auto-audit system extends (not replaces) the existing infrastructure,
adding automation, SQL provenance, and rollback capabilities.
