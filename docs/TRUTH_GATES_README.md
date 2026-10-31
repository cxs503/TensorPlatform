# Truth Gates — Independence-Convergence Verdict Mechanism

**Mission**: m613  
**Status**: ✅ Complete (28/28 tests passing)

## Overview

Truth Gates implements a three-pillar verification mechanism for autonomous ship performance platforms:

1. **2-of-3 Majority Voting** — Independent reproduction for cheat detection
2. **Independent Metering** — Platform-side usage tracking & reconciliation
3. **Sidecar Physical-Property Checkers** — Constraint validation on simulation outputs

## Design Principle

> "两个不知情的人很难撒一样的谎；正确答案唯一，独立结果必收敛"  
> *"Two people who don't know each other's answers can hardly tell the same lie; the correct answer is unique — independent results must converge."*

## Architecture

See `docs/m613_truth_gates_report.md` for full paper-quality report with figures.

## Quick Start

### Run Tests

```bash
cd /srv/tensor/agent/work/m613
python -m pytest tests/test_truth_gates.py -v
```

Expected output: **28 passed in ~0.2s**

### API Endpoints

```
POST /api/v1/truth-gates/votes              — Submit verdict vote
GET  /api/v1/truth-gates/vote-groups/{id}   — Get aggregated result
POST /api/v1/truth-gates/metered-usage      — Platform-only metering write
POST /api/v1/truth-gates/reconcile          — Usage reconciliation
POST /api/v1/truth-gates/sidecar-check      — Run sidecar check
GET  /api/v1/truth-gates/integrity-events   — List integrity events
GET  /api/v1/truth-gates/split-events       — List split verdict events
```

### Example: Submit Vote

```python
import requests

# Submit a cheat verdict
response = requests.post(
    "http://localhost:8001/api/v1/truth-gates/votes",
    json={
        "vote_group_id": "vg_001",
        "mission_id": 613,
        "voter_model": "gpt-4",
        "verdict": "cheat",
        "confidence": 0.92,
        "reasoning": "Agent claimed 1000 tool calls but only 87 were metered"
    },
    headers={"Authorization": "Bearer <token>"}
)

print(response.json())
# {
#   "vote": {...},
#   "aggregation": {
#     "total_voters": 1,
#     "tally": {"cheat": 1},
#     "is_confirmed_cheat": False,  # Need 2-of-3
#     "is_split": False
#   }
# }
```

### Example: Run Sidecar Check

```python
# Check Holtrop Cd-Re monotonicity
response = requests.post(
    "http://localhost:8001/api/v1/truth-gates/sidecar-check",
    json={
        "mission_id": 613,
        "checker_name": "holtrop_cd_re",
        "data": {
            "reynolds_numbers": [1e6, 5e6, 1e7, 5e7],
            "cd_values": [5e-3, 3.5e-3, 2.5e-3, 1.8e-3]
        }
    },
    headers={"Authorization": "Bearer <token>"}
)

result = response.json()
print(f"Passed: {result['result']['passed']}")
print(f"Red flag: {result['result']['red_flag']}")
```

## Red Team Evidence

All 4 red-team tests pass:

1. ✅ **Split verdict**: 1 cheat + 1 pass + 1 partial → no strike confirmed
2. ✅ **Usage reconciliation**: 1000 vs 87 tool_calls → 1049% deviation detected
3. ✅ **Sidecar check**: Non-monotonic Cd-Re → red flag raised
4. ✅ **Permission enforcement**: Agent account blocked from metered_usage writes (403)

## Database Schema

5 new tables (pure additions, no modifications to existing tables):

- `repro_verdict_votes` — Individual votes in 2-of-3 groups
- `split_verdict_events` — Split verdict records
- `metered_usage` — Platform-metered usage (ground truth)
- `integrity_events` — Reconciliation discrepancies
- `sidecar_check_results` — Physical-property check results

## Implementation Files

```
backend/
├── models/truth_gates.py              # ORM models
├── api/truth_gates.py                 # API router
├── services/truth_gates_aggregator.py # Pure aggregation functions
├── services/sidecar_checkers.py       # Physical-property checkers
└── middleware/truth_gates_meter.py    # Metering middleware

tests/
└── test_truth_gates.py                # 28 comprehensive tests

docs/
└── m613_truth_gates_report.md         # Paper-quality report

artifacts/m613_figures/
├── fig1_architecture.png              # System architecture
├── fig2_vote_scenarios.png            # Voting scenarios
├── fig3_reconciliation.png            # Usage reconciliation
├── fig4_sidecar_cd_re.png             # Cd-Re monotonicity
└── fig5_sidecar_wave_cw.png           # Wave Cw-Fn peak
```

## Theoretical Foundation

- **N-Version Programming** (Avizienis 1985): Independent implementations, disagreement = fault
- **Byzantine Fault Tolerance** (Lamport et al. 1982): 3f+1 nodes to tolerate f faults
- **Measurement Audit** (ISO/IEC 17025): Independent metrological traceability

## Constraints Compliance

✅ **Pure additions only** — No modifications to tensor_missions.py or m597 files  
✅ **SQL reproducible** — Pure-function aggregators, deterministic output  
✅ **Permission enforced** — Agent accounts blocked from metered_usage writes  
✅ **Physically grounded** — Sidecar checkers enforce hydrodynamic constraints  

## Future Work

1. Integrate with m591 reproduction system (adapter layer ready)
2. Expand sidecar checker library (maneuvering, seakeeping, structural)
3. Credit ledger integration (negative scoring for integrity events)
4. Guide tracker dashboard (visualize metering discrepancies)
5. Production deployment with real LLM token metering

## References

See `docs/m613_truth_gates_report.md` Section 7 for full bibliography.

---

**Contact**: TensorPlatform Team  
**License**: Same as parent repository
