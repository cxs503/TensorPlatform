# Platform Governance Maturity Module

> M3 Self-Sustaining Level — ≥30 days autonomous operation without human input

## Overview

This module implements four governance maturity capabilities for the TensorPlatform, enabling autonomous operation at M3 level:

| Capability | Description | Key Class |
|---|---|---|
| **(a) Report Gate** | 6-element completeness check with auto-degradation | `check_report_completeness()` |
| **(b) Evidence Chain** | SHA256 provenance tracking for all artifacts | `ProvenanceChain` |
| **(c) Capability Ledger** | Auto-updating capability registry with evidence levels | `CapabilityLedger` |
| **(d) Self-Healing** | Auto-degrade after N consecutive failures | `SelfHealingManager` |

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                    GovernanceManager                         │
│  (integrated entry point for all governance operations)      │
├─────────────┬──────────────┬──────────────┬─────────────────┤
│ Report Gate │  Provenance  │  Capability  │  Self-Healing   │
│  (6-elem)   │    Chain     │    Ledger    │    Manager      │
│             │  (SHA256)    │  (REAL/MOCK) │  (N-fail→degrade)│
└─────────────┴──────────────┴──────────────┴─────────────────┘
```

## (a) Report Gate Enhancement

### Six Required Elements

Every paper-quality report must contain:

| Element | Key | Detection Pattern |
|---|---|---|
| Case Introduction | `case_introduction` | 案例介绍/物理背景/geometry/parameter |
| Method | `method` | 方法/method/求解器/solver/工具链 |
| Results | `results` | 结果/result/数值/table/收敛 |
| Figures | `figures` | 图/figure/云图/curve OR non-empty figures list |
| Notes | `notes` | 注意/局限/适用/踩坑/展望 |
| Error Judgment | `error_judgment` | 误差/error/偏差/基准/<3% |

### Degradation Levels

| Level | Elements Present | Action |
|---|---|---|
| 0 | 6/6 | ✅ PASS |
| 1 | 5/6 | ⚠️ Warning — minor gap |
| 2 | 3-4/6 | 🔶 Major — requires revision |
| 3 | <3/6 | ❌ Critical — auto-reject |

### Usage

```python
from api.governance_maturity import check_report_completeness

result = check_report_completeness(markdown, figures=[{"name": "fig1.png"}])
print(result.passed)              # True/False
print(result.degradation_level)   # 0-3
print(result.elements_missing)    # list of missing element keys
```

## (b) Evidence Chain Auto-Audit

Every node output is registered with a SHA256 hash. Dependencies on upstream artifacts form an auditable chain.

### Provenance Record

```python
from api.governance_maturity import ProvenanceChain

chain = ProvenanceChain()
record = chain.register_artifact(
    artifact_id="result-001",
    artifact_path="/data/result.json",
    data=computed_data,
    node_id="resistance-calc",
    mission_id=1234,
    upstream_deps=["mesh-001", "boundary-001"],
)

# Verify integrity
assert chain.verify_artifact("result-001", computed_data)  # True
assert chain.verify_artifact("result-001", "tampered")     # False

# Check chain integrity
audit = chain.check_chain_integrity("result-001")
# {"valid": True, "chain_length": 3, "broken_links": []}
```

### Chain Integrity Check

- Traverses full dependency graph upstream
- Detects missing/broken links
- Returns root SHA256 for external verification

## (c) Capability Ledger Auto-Update

When a node passes verification, its capabilities are automatically registered.

### Evidence Levels

| Level | Meaning |
|---|---|
| `REAL` | Verified real algorithm with evidence |
| `MOCK` | Hardcoded/random values |
| `STUB` | Empty shell, not implemented |
| `WITHHELD` | Insufficient evidence |
| `NOT_SUPPORTED` | Not available |

### Usage

```python
from api.governance_maturity import CapabilityLedger, CapabilityLevel

ledger = CapabilityLedger()
ledger.register_capability(
    capability_id="wave_resistance",
    name="Wave Resistance (Rankine Source)",
    level=CapabilityLevel.REAL,
    domain="hydrodynamics",
    coverage=0.85,
    evidence_path="/evidence/m1234/result.json",
    mission_id=1234,
)

summary = ledger.get_coverage_summary()
# {"total_capabilities": 1, "by_level": {"REAL": 1, ...}, ...}
```

### Update Audit Log

Every create/update is logged with timestamp, mission_id, and new level.

## (d) Self-Healing Mechanism

Nodes that fail consecutively (default threshold=3) trigger automatic degradation.

### Degradation Strategies (escalation order)

| Strategy | Description |
|---|---|
| `reduce_precision` | Lower accuracy requirements |
| `switch_solver` | Try alternative solver |
| `reduce_scope` | Narrow parameter range |
| `skip_validation` | Skip expensive validation steps |

### Usage

```python
from api.governance_maturity import SelfHealingManager, DegradationStrategy

healing = SelfHealingManager(threshold=3)

# Record failures
for i in range(3):
    healing.record_failure("node-A", 1234, "timeout", f"fail {i}")

# Check if degradation needed
if healing.should_degrade("node-A"):
    strategy = healing.get_degradation_strategy("node-A")
    # → DegradationStrategy.REDUCE_PRECISION
    healing.apply_degradation("node-A", 1234, strategy)

# On success, counter resets
healing.record_success("node-A")
status = healing.get_node_status("node-A")
# {"recovered": True, "consecutive_failures": 0, ...}
```

## Integrated Governance Manager

```python
from api.governance_maturity import GovernanceManager

gov = GovernanceManager()

# Process node output (provenance + capabilities + healing reset)
result = gov.process_node_output(
    node_id="calc-node",
    mission_id=1234,
    artifact_id="result-001",
    artifact_path="/data/result.json",
    data="computed data",
    upstream_deps=["input-001"],
    capabilities=[{"capability_id": "cap1", "name": "Cap1", "level": "REAL", ...}],
)

# Handle node failure (with auto-degradation)
result = gov.handle_node_failure(
    node_id="calc-node",
    mission_id=1234,
    error_type="convergence_error",
    error_message="Failed to converge",
)

# Full governance audit
audit = gov.full_audit()
```

## API Endpoints (FastAPI)

When FastAPI is available, the module exposes:

| Endpoint | Method | Description |
|---|---|---|
| `/governance/audit/report` | POST | Audit report for 6-element completeness |
| `/governance/provenance/register` | POST | Register artifact in provenance chain |
| `/governance/healing/failure` | POST | Report node failure for self-healing |
| `/governance/ledger/capabilities` | GET | List all capabilities |
| `/governance/ledger/summary` | GET | Capability coverage summary |
| `/governance/audit/full` | GET | Full governance audit |

## Design Principles

1. **Zero domain semantics**: No domain-specific terms hardcoded; all domain data injected via API/config
2. **Honest FAIL**: Failed states are preserved, never overwritten
3. **SHA256 everything**: Every artifact gets a hash for provenance
4. **Graceful degradation**: Missing capabilities degrade gracefully, don't crash
5. **Audit trail**: Every state change is logged with timestamp and actor

## Testing

21 tests covering all 4 capabilities + 3 negative controls:

```bash
cd backend && python -m pytest tests/test_governance_maturity.py -v
```

| Category | Tests | Negative Controls |
|---|---|---|
| Report Gate | 5 | NC1: empty report → level 3 |
| Evidence Chain | 4 | NC2: forged SHA256 → verify fails |
| Capability Ledger | 4 | NC3: invalid coverage → ValueError |
| Self-Healing | 5 | — |
| Integration | 3 | — |
| **Total** | **21** | **3** |

## Limitations

- In-memory storage (no persistence); production deployment needs DB backend
- Pattern matching for report elements is regex-based; may have false positives/negatives
- Self-healing strategies are predefined; custom strategies require code changes
- No distributed consensus for provenance chain (single-writer assumption)
