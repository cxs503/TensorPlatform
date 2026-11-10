# Evidence Chain Integrity Auditor (P190-N07)

Automated evidence chain integrity auditing for the TensorPlatform capability ledger.

## Purpose

The platform capability ledger (21 capabilities) is maintained manually, leading to consistency issues:
- Tests pass but ledger not updated
- Ledger claims REAL but no corresponding tests exist (OVERCLAIM)
- Tests fail but ledger still shows REAL
- Insufficient test coverage (< 3 test cases)

This auditor automatically detects these breaks and generates machine-executable fix suggestions.

## Architecture

```
capability_test_mapping.json  ──┐
                                ├──> EvidenceChainAuditor ──> AuditReport
capability_ledger.json ─────────┘                                    │
                                                                     ├── JSON report
test files (via AST inspection) ─────────────────────────────────────├── Markdown summary
                                                                     └── Audit events (JSONL)
```

### Components

| Module | Purpose |
|--------|---------|
| `mapping_loader.py` | Loads capability→test mapping with fail-soft semantics |
| `auditor.py` | Core scanning engine: detects breaks, classifies capabilities |
| `report_generator.py` | Generates JSON/Markdown/JSONL reports |
| `cli.py` | Command-line interface |

### Finding Types

| Type | Severity | Description |
|------|----------|-------------|
| `missing_test` | critical/warning | No test mapping or no test files found |
| `overclaim` | critical | Ledger claims higher level than test evidence supports |
| `stale_status` | critical/info | Ledger status doesn't match test outcomes |
| `insufficient_coverage` | warning | Fewer than minimum required test cases |

## Usage

### Python API

```python
from tools.evidence_chain_auditor import EvidenceChainAuditor, AuditReportGenerator

auditor = EvidenceChainAuditor(worktree=Path("."))
report = auditor.run_audit(scan_id="my-scan")

generator = AuditReportGenerator(output_dir=Path("artifacts/evidence_chain_auditor"))
paths = generator.generate_all(report)
```

### CLI

```bash
# Basic audit
python -m tools.evidence_chain_auditor.cli

# With options
python -m tools.evidence_chain_auditor.cli \
    --worktree /path/to/worktree \
    --scan-id my-scan \
    --run-pytest \
    --output-dir artifacts/my_audit
```

### Configuration

The capability-test mapping (`capability_test_mapping.json`) maps each capability to its test modules:

```json
{
  "mappings": [
    {
      "capability_id": "calc.resistance",
      "test_modules": [
        "tests/test_resistance_m1775_verification.py",
        "tests/test_resistance_identity_audit_m1817.py"
      ],
      "min_test_cases": 3,
      "domain": "阻力"
    }
  ]
}
```

## Design Principles

1. **Zero domain semantics**: The auditor doesn't depend on any domain-specific knowledge. It works with arbitrary capability names and test structures.

2. **Fail-soft**: Missing mapping file → WITHHELD (not crash). Malformed JSON → empty mapping + warning.

3. **Idempotent**: Same state → same output across repeated scans (except scan_id and timestamp).

4. **Deterministic**: Results are sorted by capability_id for reproducibility.

## Acceptance Criteria (P190-N07)

| Criterion | Status | Evidence |
|-----------|--------|----------|
| (a) OVERCLAIM detection | ✅ | `test_overclaim_detected_for_no_tests`, `test_overclaim_detected_for_insufficient_coverage` |
| (b) 3 synthetic capabilities | ✅ | `test_has_tests_classified_as_consistent`, `test_no_tests_classified_as_inconsistent`, `test_few_tests_classified_as_inconsistent` |
| (c) Fail-soft | ✅ | `test_missing_mapping_file_no_crash`, `test_unmapped_capability_gets_withheld` |
| (d) JSON report structure | ✅ | `test_report_json_structure`, `test_finding_json_structure` |
| (e) Zero domain semantics | ✅ | `test_no_domain_strings_in_auditor_logic`, `test_auditor_works_with_arbitrary_capability_names` |
| (f) Idempotent | ✅ | `test_three_scans_produce_same_output`, `test_findings_order_is_deterministic` |

## Test Results

```
26 passed, 0 failed
```

Run tests:
```bash
python -m pytest tests/evidence_chain_auditor/test_auditor.py -v
```

## Output Formats

### JSON Report (`audit_report_{scan_id}.json`)

Full machine-readable report with all findings and per-capability details.

### Markdown Summary (`audit_summary_{scan_id}.md`)

Human-readable summary with tables and per-capability status.

### Audit Events (`audit_events_{scan_id}.jsonl`)

One JSON object per line, suitable for ingestion into `audit_events` database table.

## Limitations

- **Static inspection mode** (default): Counts test functions via AST but can't detect actual test failures. Use `--run-pytest` for accurate failure detection.
- **No test execution context**: Doesn't know if tests are skipped, xfailed, or parametrized.
- **Mapping maintenance**: The capability-test mapping must be manually maintained (though it's machine-readable).

## Future Work

- Integration with CI/CD pipeline (auto-run on PR)
- Auto-fix suggestions that can be applied via API
- Historical trend tracking (is the ledger getting better or worse?)
- Integration with `audit_events` database table for persistent storage
