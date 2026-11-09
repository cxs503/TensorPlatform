# Failure Minimizer

Delta debugging tool for automatic failure reproduction and minimization in TensorPlatform missions.

## Overview

The Failure Minimizer automatically reduces failed mission snapshots to 1-minimal executable reproduction scripts with cryptographic signatures for auditability. It implements the ddmin (delta debugging) algorithm to find the smallest subset of elements that still reproduces the failure.

## Key Features

- **Automatic Corpus Export**: Extract frozen snapshots from failed/blocked missions
- **Delta Debugging**: Find 1-minimal reproduction sets using ddmin algorithm
- **Cryptographic Signatures**: SHA256-based failure signatures for oracle comparison
- **Audit Reports**: Generate machine-readable reports with reduction logs
- **Zero Domain Semantics**: All domain data injected via API/config/artifacts
- **Integrity Verification**: SHA256 checksums for snapshot integrity

## Acceptance Criteria

All acceptance criteria are validated by automated tests:

- **C1 Reproduction Rate**: ≥80% on ≥8 frozen failures ✓
- **C2 Reduction Rate**: Median ≥50%, Max ≥80% ✓
- **C3 1-Minimality**: 100% pass rate ✓
- **C4 Deterministic**: 3 consecutive runs produce identical results ✓
- **C5 False Positive Gate**: ≤10% on succeeded missions ✓
- **C6 Zero Domain Semantics**: Keyword scan hits = 0 ✓
- **C7 Resource Constraint**: ≤min(10min, 3×original duration) ✓
- **C8 Auditability**: repro_min directory re-runnable by independent process ✓

## Installation

The tool is part of TensorPlatform and requires Python 3.11+.

```bash
# No additional installation needed - included in TensorPlatform
```

## Usage

### Command-Line Interface

```bash
# Export a mission to corpus
python -m tools.failure_minimizer export --mission-id 123 --mission-file mission.json

# Minimize a single mission
python -m tools.failure_minimizer minimize --mission-id 123

# Batch minimize all failed missions
python -m tools.failure_minimizer batch

# Generate audit report
python -m tools.failure_minimizer audit
```

### Python API

```python
from tools.failure_minimizer.corpus import CorpusExporter
from tools.failure_minimizer.ddmin import DeltaDebugger, SandboxExecutor
from tools.failure_minimizer.report import ReportGenerator

# Export mission to corpus
exporter = CorpusExporter("corpus")
exporter.export_from_dict(mission_data)

# Load and minimize
snapshot = exporter.load_mission(mission_id)
executor = SandboxExecutor(timeout_seconds=600)
debugger = DeltaDebugger(executor=executor)
result = debugger.minimize(snapshot)

# Generate report
generator = ReportGenerator("repro_min")
report_dir = generator.generate(snapshot, result, is_1minimal=True)
```

## Architecture

### Components

1. **corpus.py**: Mission snapshot export and management
   - `MissionSnapshot`: Frozen snapshot dataclass
   - `CorpusExporter`: Export/load/verify snapshots
   - SHA256 integrity verification

2. **signature.py**: Failure signature computation
   - `FailureSignature`: Immutable signature dataclass
   - Normalized signature computation (exit_code, fail_stage, exception_hash, missing_artifacts)
   - Signature comparison (oracle)

3. **ddmin.py**: Delta debugging algorithm
   - `SandboxExecutor`: Isolated execution environment
   - `DeltaDebugger`: ddmin algorithm implementation
   - 1-minimality verification

4. **report.py**: Audit report generation
   - `ReportGenerator`: Generate repro_min reports
   - `CorpusAuditReport`: Aggregate statistics
   - Executable reproduction scripts

5. **cli.py**: Command-line interface
   - Export, minimize, batch, audit commands
   - Argument parsing and validation

### Data Flow

```
Mission Data → Corpus Export → Snapshot Loading → Delta Debugging → Report Generation
     ↓              ↓                ↓                  ↓                  ↓
  JSON/API    manifest.json    MissionSnapshot    ReductionResult    repro_min/
              sha256.txt                          (minimal set)      manifest.json
                                                                     reduce.log
                                                                     signature.txt
                                                                     repro.sh
```

## Failure Signature

A failure signature is a normalized tuple:

```python
FailureSignature(
    exit_code: int,              # Process exit code
    fail_stage: str,             # Pipeline stage (check/command/file/unknown)
    first_exception_hash: str,   # SHA256 hash of first exception text
    missing_artifacts: tuple,    # Sorted list of missing artifact paths
)
```

The signature serves as the oracle for delta debugging: two executions match if their signatures are identical.

## Delta Debugging Algorithm

The ddmin algorithm finds a 1-minimal subset:

1. Start with full element set
2. Try removing each element one at a time
3. If removal still reproduces failure, keep the reduced set
4. Repeat until no further reduction possible
5. Verify 1-minimality: removing any single element flips the oracle

**Candidate Elements**:
- `file:<path>`: Changed files
- `check:<command>`: Validation checks
- `cmd:<command>`: Execution commands
- `env:<key>`: Environment variables

## Output Structure

```
repro_min/
├── {mission_id}/
│   ├── manifest.json      # Complete report with metadata
│   ├── reduce.log         # Human-readable reduction log
│   ├── signature.txt      # Failure signature (JSON)
│   └── repro.sh           # Executable reproduction script
└── audit_summary.json     # Aggregate statistics
```

### manifest.json

```json
{
  "mission_id": 123,
  "timestamp": "2026-11-08T12:00:00Z",
  "original_element_count": 10,
  "minimal_element_count": 3,
  "reduction_rate": 0.7,
  "iterations": 15,
  "duration_seconds": 2.5,
  "signature_match": true,
  "is_1minimal": true,
  "original_elements": [...],
  "minimal_elements": [...],
  "original_signature": {...},
  "final_signature": {...},
  "reduction_log": [...]
}
```

## Testing

Run the comprehensive test suite:

```bash
pytest tools/failure_minimizer/tests/test_failure_minimizer.py -v
```

The test suite validates all 8 acceptance criteria using synthetic mission data with zero domain semantics.

## Zero Domain Semantics

The tool contains **no domain-specific logic**. All domain data (ship resistance, propulsion, seakeeping, etc.) is injected via:

- Mission result dictionaries
- Configuration files
- API responses

This ensures the tool remains generic and reusable across all TensorPlatform domains.

## Limitations

1. **Sandbox Execution**: The default `SandboxExecutor` creates temporary directories but doesn't fully isolate execution. For production use, consider container-based isolation.

2. **Element Granularity**: Currently operates on file/check/command/env level. Finer granularity (e.g., line-level) would require AST parsing.

3. **Timeout Handling**: Long-running missions may timeout. Adjust `SandboxExecutor.timeout_seconds` as needed.

4. **Non-Deterministic Failures**: Flaky failures may not minimize correctly. Consider running multiple times and taking the intersection.

## Future Enhancements

- Container-based sandbox isolation (Docker/Podman)
- Parallel delta debugging for large element sets
- Machine learning-guided element ordering
- Integration with TensorPlatform mission API
- Web-based visualization of reduction trees

## License

Part of TensorPlatform - see main repository license.

## References

- Zeller, A., & Hildebrandt, R. (2002). Simplifying and isolating failure-inducing input. IEEE Transactions on Software Engineering, 28(2), 183-200.
- Delta Debugging: https://www.st.cs.uni-saarland.de/dd/
