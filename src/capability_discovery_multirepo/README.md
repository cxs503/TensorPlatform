# Multi-Repo Capability Auto-Discovery

Automated capability discovery and registration across multiple solver repositories.

## Overview

This module implements a closed-loop system for discovering solver capabilities by scanning test suites, extracting capability features, and auto-registering them to the central capability ledger.

### Architecture

```
┌─────────────────┐     ┌──────────────────┐     ┌─────────────────┐     ┌──────────────────┐
│  MultiScanner   │────▶│ FeatureExtractor │────▶│ AutoRegistrar   │────▶│ DiffReportGen    │
│                 │     │                  │     │                 │     │                  │
│ • Marker scan   │     │ • Group by cap   │     │ • Add/Update    │     │ • Markdown       │
│ • Pattern scan  │     │ • Reconcile lvl  │     │ • Retract       │     │ • JSON           │
│ • Multi-repo    │     │ • Confidence     │     │ • Hash chain    │     │ • Coverage       │
└─────────────────┘     └──────────────────┘     └─────────────────┘     └──────────────────┘
```

### Components

1. **MultiScanner**: Scans multiple repositories for `@pytest.mark.capability()` markers and pattern-based evidence.

2. **FeatureExtractor**: Aggregates raw discovery records into capability features with reconciled evidence levels and confidence scores.

3. **AutoRegistrar**: Registers features to the capability ledger with full audit trail (hash chain). Supports rollback via retract/restore.

4. **DiffReportGenerator**: Produces human-readable reports of new/updated/missing capabilities.

## Usage

### CLI

```bash
# Scan multiple repos
python -m capability_discovery_multirepo \
    --repos TensorLBM=/path/to/lbm,TensorFEM=/path/to/fem \
    --ledger artifacts/ledger.json \
    --output artifacts/reports/

# Using config file
python -m capability_discovery_multirepo --config config/discovery.json

# Verify ledger chain
python -m capability_discovery_multirepo --verify-chain --ledger artifacts/ledger.json
```

### Python API

```python
from capability_discovery_multirepo import (
    DiscoveryConfig, RepoDescriptor, MultiScanner,
    FeatureExtractor, AutoRegistrar, DiffReportGenerator,
)
from capability_discovery_multirepo.pipeline import run_pipeline

# Configure repos
config = DiscoveryConfig(
    repos=[
        RepoDescriptor(name="TensorLBM", root="/path/to/lbm"),
        RepoDescriptor(name="TensorFEM", root="/path/to/fem"),
    ],
    ledger_path="artifacts/ledger.json",
)

# Run full pipeline
result = run_pipeline(config, workspace_root=".")

# Access results
print(f"Discovered: {len(result.features)}")
print(f"Added: {result.report.summary['added']}")
print(f"Coverage: {result.coverage.get('coverage_ratio', 0):.1%}")
```

## Capability Marker Convention

Solvers annotate capabilities using pytest markers:

```python
import pytest

@pytest.mark.capability(
    "multiphase",                    # Capability ID
    evidence_level="REAL",           # REAL/MOCK/STUB/WITHHELD
    density_ratio=100,               # Parameters
    anchors=["Laplace", "Rayleigh"], # Verification anchors
    tags=["validation"],             # Tags
)
def test_multiphase_laplace():
    ...
```

## Evidence Levels

| Level | Meaning |
|-------|---------|
| REAL | Verified against analytical/reference solution |
| WITHHELD | Insufficient evidence to classify |
| MOCK | Hard-coded or random values |
| STUB | Placeholder implementation |
| NOT_SUPPORTED | Capability not implemented |

## Audit Trail

All ledger mutations are recorded in a hash chain for tamper detection:

```json
{
  "chain": [
    {
      "seq": 1,
      "action": "added",
      "capability_id": "multiphase",
      "entry_hash": "abc123...",
      "prev_hash": "",
      "hash": "def456..."
    }
  ]
}
```

## Acceptance Criteria

| Criterion | Target | Status |
|-----------|--------|--------|
| C1: Scan coverage | ≥90% | ✅ |
| C2: Extraction accuracy | ≥95% | ✅ |
| C3: Ledger consistency | ≤5% diff | ✅ |
| C4: Report completeness | All sections | ✅ |
| C5: Test coverage | ≥12 cases | ✅ (33 tests) |

## Zero Domain Semantics

Following TensorPlatform iron rule: no domain-specific terms are hardcoded. All capability names, patterns, and evidence types come from configuration/registries.
