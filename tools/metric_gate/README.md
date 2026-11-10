# Platform Metric Gate & Negative Findings System

## Overview

This module implements platform-level integrity gates for metric writes and negative findings management. It provides:

1. **Write-time Recompute Gate**: Validates metric values against registered expressions
2. **Silent Regression Sentinel**: Detects suspicious metric changes without evidence growth
3. **Negative Findings Extraction**: Extracts falsified hypotheses from mission results
4. **Duplicate Detection**: SimHash-based fingerprinting to prevent duplicate proposals

## Zero Domain Semantics

All metric names, expressions, and field names are configurable. No domain-specific terms are hardcoded. Tests use synthetic names like `repro_rate`, `accuracy`, etc.

## Components

### 1. Expression Registry (`expr_registry.py`)

Safe expression evaluator with whitelist-based parsing:
- **No eval()** or **exec()** — recursive descent parser
- Supports: `+`, `-`, `*`, `/`, `**`, `%`
- Functions: `abs`, `sqrt`, `log`, `exp`, `min`, `max`, `pow`, etc.
- Variables: references to metric row fields

```python
from tools.metric_gate.expr_registry import ExprRegistry

reg = ExprRegistry()
reg.register("repro_rate", "reproduced / total")
value = reg.evaluate("repro_rate", {"reproduced": 19, "total": 20})  # 0.95
```

### 2. Metric Gate (`gate.py`)

Write-time validation decorator:

```python
from tools.metric_gate.gate import MetricGate

gate = MetricGate(reg, default_tol=1e-6)
result = gate.check("repro_rate", 0.95, {"reproduced": 19, "total": 20})

if not result.accepted:
    raise GateRejection(...)  # 422 response
```

### 3. Regression Sentinel (`sentinel.py`)

Detects silent regressions (value changes without evidence growth):

```python
from tools.metric_gate.sentinel import RegressionSentinel, MetricSnapshot

sentinel = RegressionSentinel(base_tol=0.01, regression_tol=5.0)
prev = MetricSnapshot("repro_rate", 0.95, 20)
curr = MetricSnapshot("repro_rate", 0.50, 20)  # same evidence, big drop

alert = sentinel.check_transition("repro_rate", prev, curr)
# Returns SuspectAlert if suspicious
```

### 4. Snapshot Store (`models.py`)

SQLite-backed persistence for metric snapshots:

```python
from tools.metric_gate.models import MetricSnapshotStore

store = MetricSnapshotStore.from_url("sqlite:///metrics.db")
store.insert("repro_rate", 0.95, evidence_count=20)
recent = store.get_recent("repro_rate", limit=10)
```

### 5. Replay CLI (`replay.py`)

Replay metric sequences through gate + sentinel:

```bash
python -m tools.metric_gate.replay \
  --input snapshots.json \
  --config config_example.json \
  --output results.json
```

### 6. Negative Findings Extractor (`tools/negfind/extractor.py`)

Extract falsified hypotheses from mission results:

```python
from tools.negfind.extractor import NegativeFindingsExtractor

extractor = NegativeFindingsExtractor()
findings = extractor.extract_from_mission(mission_result)
# Returns list of NegativeFinding objects
```

### 7. SimHash Fingerprinting (`tools/negfind/fingerprint.py`)

Near-duplicate detection for proposals/nodes:

```python
from tools.negfind.fingerprint import FingerprintGate

gate = FingerprintGate(similarity_threshold=0.85)
gate.register("neg1", "hypothesis text...")
passed, action, hits = gate.check("new proposal text...")
```

### 8. Audit CLI (`tools/negfind/audit.py`)

Audit negative findings from mission results:

```bash
python -m tools.negfind.audit \
  --input missions.json \
  --known-negated negated.json \
  --output findings.json
```

## Acceptance Criteria

| ID | Criterion | Status |
|----|-----------|--------|
| F1 | Replay repro_rate sequence, precision=recall=1.0 (4 zero-value snapshots) | ✅ PASS |
| F2 | 20 legal improvement writes → 0 false positives | ✅ PASS |
| F3 | 20 inconsistent writes → all 422 | ✅ PASS |
| F4 | Negative findings extraction recall/precision ≥ 0.8 (≥20 labeled) | ✅ PASS |
| F5 | p5222/p5326 duplicate pairs must hit | ✅ PASS |
| F6 | Zero domain semantics: new code domain terms = 0 | ✅ PASS |
| F7 | pytest ≥ 15 cases all green | ✅ PASS (41 tests) |

## Testing

Run the test suite:

```bash
python -m pytest tools/metric_gate/tests/test_metric_gate.py -v
```

Expected output: **41 passed**

## Configuration

See `config_example.json` for a complete configuration example.

## Architecture

```
tools/
├── metric_gate/
│   ├── __init__.py
│   ├── expr_registry.py    # Safe expression evaluator
│   ├── gate.py             # Write-time recompute gate
│   ├── sentinel.py         # Silent regression detector
│   ├── models.py           # SQLite persistence
│   ├── replay.py           # CLI: replay sequences
│   ├── config_example.json
│   └── tests/
│       └── test_metric_gate.py  # 41 tests
└── negfind/
    ├── __init__.py
    ├── extractor.py        # Negative findings extraction
    ├── fingerprint.py      # SimHash duplicate detection
    └── audit.py            # CLI: audit findings
```

## Security

- **No eval/exec**: Expression parser uses recursive descent with whitelist
- **Token validation**: Only whitelisted operators, functions, and identifiers allowed
- **Injection prevention**: Unknown functions rejected at registration time
- **Division by zero**: Caught and reported as SafeExprError

## License

Part of TensorPlatform — see repository LICENSE file.
