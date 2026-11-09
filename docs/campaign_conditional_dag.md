# Campaign Conditional DAG — Design & Implementation

## Overview

This document describes the conditional branching and adaptive research loop capabilities added to the TensorPlatform campaign DAG system (mission m6722).

**Physical goal**: Enable campaign DAGs to dynamically route execution based on verification results, supporting self-adaptive research cycles (verify → judge → adjust → re-verify) without human intervention.

## Architecture

### 1. Condition Edge Model

Two mechanisms for specifying conditions:

#### Inline Conditions (on campaign_nodes)
```json
{
  "condition": {
    "type": "gate",
    "metric": "max_rel_err",
    "operator": "<",
    "threshold": 0.05,
    "on_true": 42,   // node ID to activate if condition met
    "on_false": 43   // node ID to activate if condition not met
  }
}
```

#### Edge Conditions Table (campaign_edge_conditions)
For complex DAGs with multiple conditions per node, explicit edge records:
```sql
CREATE TABLE campaign_edge_conditions (
    id              BIGSERIAL PRIMARY KEY,
    plan_id         BIGINT NOT NULL,
    source_node_id  BIGINT NOT NULL,
    condition_expr  JSONB NOT NULL,
    priority        INT NOT NULL DEFAULT 0,
    enabled         BOOLEAN NOT NULL DEFAULT TRUE,
    ...
);
```

### 2. Gate Router

When a node completes (status → done/failed), the routing evaluator:

1. Checks inline condition on the node
2. If no inline condition, checks `campaign_edge_conditions` table
3. Evaluates the condition against the provided metric value
4. Activates the appropriate target node (on_true or on_false)
5. Logs the evaluation to `campaign_events` for audit

### 3. Adaptive Loop

Supports self-referential conditions where `on_false` points back to the source node:

```
N1 (verify) → err < 0.01? → Yes: N2 (success)
                          → No:  N1 (retry with refined parameters)
```

Each retry increments `iteration_count` on the node. The loop continues until:
- Convergence: metric value meets target threshold
- Exhaustion: `iteration_count >= max_iterations`

### 4. Convergence Detection

Configuration on nodes:
```json
{
  "convergence_check": {
    "metric": "max_rel_err",
    "target": 0.01,
    "max_iterations": 3,
    "action_on_exhaust": "escalate"  // or "fail" or "warn"
  }
}
```

When `max_iterations` is exceeded:
- `escalate`: Node marked as `blocked` with escalation reason
- `fail`: Node marked as `failed`
- `warn`: Node continues but warning logged

### 5. API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| POST | `/tensor/campaigns/edge-conditions` | Create conditional edge (idempotent) |
| GET | `/tensor/campaigns/edge-conditions` | List edge conditions |
| GET | `/tensor/campaigns/edge-conditions/{id}` | Get specific edge |
| DELETE | `/tensor/campaigns/edge-conditions/{id}` | Delete edge |
| PUT | `/tensor/campaigns/nodes/{id}/condition` | Update node condition |
| POST | `/tensor/campaigns/routing/evaluate` | Evaluate routing for completed node |
| POST | `/tensor/campaigns/nodes/{id}/create-retry` | Create retry node for adaptive loop |
| GET | `/tensor/campaigns/plans/{id}/routing-status` | Get routing status for plan |
| GET | `/tensor/campaigns/plans/{id}/validate-dag` | Validate DAG structure |

## Concurrency Safety

Per-plan locking prevents race conditions when multiple evaluations occur simultaneously:

```python
_routing_locks: dict[int, threading.Lock] = {}

def _get_plan_lock(plan_id: int) -> threading.Lock:
    with _routing_locks_guard:
        if plan_id not in _routing_locks:
            _routing_locks[plan_id] = threading.Lock()
        return _routing_locks[plan_id]
```

## Backward Compatibility

Plans without conditions work identically to before:
- `condition = null` → routing evaluation returns `action: "no_condition"`
- No changes to existing `campaign_nodes` schema required
- Old DAGs continue to function via standard dependency resolution

## Negative Controls

Three edge cases handled gracefully:

1. **condition = null**: No conditional routing, returns `no_condition` action
2. **on_true == on_false**: Equivalent to unconditional activation
3. **source_node doesn't exist**: Returns 404 (graceful degradation)

## Audit Trail

Every routing evaluation is logged to `campaign_events`:
```json
{
  "event_type": "routing_evaluated",
  "detail": {
    "node_id": 42,
    "condition_met": true,
    "activated_node_id": 43,
    "action": "activated",
    "metric_value": 0.03,
    "metric_name": "max_rel_err"
  }
}
```

## Testing

18+ test cases covering:
- Condition routing correctness (pass/fail paths)
- Adaptive loop (retry creation, convergence)
- Convergence detection (max_iterations, escalation)
- Concurrent safety (parallel evaluation)
- Backward compatibility (old DAGs)
- Negative controls (3 edge cases)
- API idempotency (duplicate POST)
- Audit trail (event logging)
- DAG validation (cycle detection, invalid references)

## Migration

Migration `108_campaign_edge_conditions.sql` adds:
- `condition` JSONB column to `campaign_nodes`
- `convergence_check` JSONB column to `campaign_nodes`
- `iteration_count` INT column to `campaign_nodes`
- `parent_node_id` BIGINT column to `campaign_nodes`
- `campaign_edge_conditions` table

## Usage Example

```python
# 1. Create nodes
n1 = create_node(plan_id, "verification_task")
n2 = create_node(plan_id, "success_path")
n3 = create_node(plan_id, "retry_path")

# 2. Set condition on N1
update_node_condition(n1, {
    "condition": {
        "type": "gate",
        "metric": "max_rel_err",
        "operator": "<",
        "threshold": 0.05,
        "on_true": n2,
        "on_false": n3,
    },
    "convergence_check": {
        "metric": "max_rel_err",
        "target": 0.01,
        "max_iterations": 3,
        "action_on_exhaust": "escalate",
    }
})

# 3. When N1 completes, evaluate routing
result = evaluate_routing(n1, metric_value=0.03, metric_name="max_rel_err")
# → result["activated_node_id"] == n2 (pass path)

result = evaluate_routing(n1, metric_value=0.08, metric_name="max_rel_err")
# → result["activated_node_id"] == n3 (fail/retry path)
```

## Limitations & Future Work

- Currently supports single metric evaluation; multi-metric conditions not yet implemented
- No visualization of conditional DAG in frontend (future work)
- Convergence detection is per-node; cross-node convergence not supported
- ~~No automatic parameter adjustment for retry nodes~~ → implemented in m7918, see
  "Maturity update" below (retry nodes carry a 2x grid refinement by default)

## References

- Mission m6722 (initial capability) / m7918 (maturity: contract endpoints, migration,
  iteration accounting, audit view)
- Campaign plans API: `backend/api/campaign_plans.py`, routing API: `backend/api/campaign_routing.py`
- Migrations: `backend/alembic/versions/109_campaign_conditional_dag.py` (canonical),
  `backend/migrations/108_campaign_edge_conditions.sql` (pre-alembic raw SQL)
- Tests: `backend/tests/test_campaign_routing.py` (API-level, 29 cases),
  `backend/tests/test_campaign_routing_migration.py`,
  `backend/tests/test_campaign_routing_standalone.py`
- Evidence: `artifacts/campaign_routing/study.json` + `fig1..fig3` (generated by
  `scripts/campaign_routing_study.py`)

## Maturity update — mission m7918 (2026-11-09)

The capability described above was mature enough to be *usable*, not yet
*machine-verifiable*. m7918 closed the gaps on four fronts: a contractual
endpoint surface, a real alembic migration, correct iteration accounting, and a
single-run evidence bundle. Nothing in this section is hand-typed: the numbers
come from `artifacts/campaign_routing/study.json`, written by
`scripts/campaign_routing_study.py` in the same run that rendered the figures.

### Contract endpoints (spec (e))

All three paths live under the router prefix `/api/v1/tensor/campaigns`:

| Method & path | Purpose | Notes |
|---|---|---|
| `POST /plans/{plan_id}/edges` | create a conditional edge | body `{source_node_id, condition, priority, enabled}`; idempotency key `(plan_id, source_node_id, priority)` → repeat returns `status="updated"` with the same id |
| `GET /plans/{plan_id}/routing` | routing view of the plan | node conditions, edge conditions, plus `events[]` = routing audit tail from `campaign_events` |
| `POST /nodes/{node_id}/evaluate` | evaluate one node's gate | body `{metric_value, metric_name}`; identical semantics to `POST /routing/evaluate` |

Legacy paths (`/edge-conditions`, `/routing/evaluate`, `/plans/{id}/routing-status`,
`/plans/{id}/validate-dag`, `/nodes/{id}/condition`, `/nodes/{id}/create-retry`)
are unchanged; the new paths are thin adapters over the same functions, so the
routing semantics exist in exactly one place.

### Semantics pinned down in this mission

1. **Gate**: strict comparison on one metric; a metric the condition does not
   name counts as *not met* (routes to `on_false`). `ON_TRUE == ON_FALSE` is
   equivalent to unconditional routing.
2. **Iteration accounting**: `iteration_count` = number of failed rounds already
   consumed by the verification chain, incremented **once** per failed round at
   evaluation time. The retry node inherits the parent's count verbatim
   (previously it added `+1` on top, so the counter advanced 2 per round and
   `max_iterations=3` escalated a round early — fixed, regression-tested).
3. **Convergence**: when `|metric| <= target` the loop **stops** (`action="converged"`,
   node marked `done`, nothing activated). Previously a converged loop still
   re-activated `on_false`, i.e. it would keep looping if the dispatcher obeyed
   the activation. A typical configuration is a tight gate
   (`threshold=0.005`) plus a looser convergence target (`target=0.01`): the
   refinement saturates before the gate is met and the loop terminates honestly.
4. **Exhaustion**: failing at `iteration_count == max_iterations` (the 4th
   attempt when `max_iterations=3`) sets `action="escalated"`, node status
   `blocked`, `block_reason="convergence exhausted after N iterations"`.
5. **Adaptive retry parameters**: `POST /nodes/{id}/create-retry` accepts
   `{parameter_adjust, grid_refine_factor}` (default 2.0) and writes
   `metadata = {is_retry, parent_node_id, grid_multiplier, grid_refine_factor, params}`;
   `nx/ny/nz/n_grid/grid_size/grid_points` are scaled by the factor, so chained
   retries give 2x → 4x → 8x refinement.

### Defect fixed: wrong primary key on insert

`SELECT last_insert_rowid()` is connection-scoped; with a pooled session the
newly created edge could report an id belonging to another row (observed: the
edge inserted as row 3 was reported as id 50, and the follow-up idempotent POST
then "updated" a different row). Replaced with `INSERT ... RETURNING id`
(PostgreSQL) / cursor `lastrowid` (SQLite) in `_insert_returning_id()`; used by
both edge creation and retry-node creation. Regression tests:
`test_created_edge_reports_its_own_row_id`,
`test_created_retry_reports_its_own_row_id`.

### Migration

`backend/alembic/versions/109_campaign_conditional_dag.py`
(`revision="109_campaign_cond_dag"`, down_revision `ca5e2b2ff9e3`) adds the four
`campaign_nodes` columns and the `campaign_edge_conditions` table with
`UNIQUE(plan_id, source_node_id, priority)`; JSON is `JSONB` on PostgreSQL and
`TEXT` elsewhere; upgrade **and** downgrade are exercised by
`backend/tests/test_campaign_routing_migration.py` against a scratch SQLite DB
seeded in the pre-109 shape.

### Acceptance evidence (generated, single run)

| # | criterion | observed | acceptance line | metric | result |
|---|---|---|---|---|---|
| 1 | conditional routing correctness | err 0.03 < 0.05 → node 1 (on_true); err 0.08 > 0.05 → node 2 (on_false) | both cases route to the specified branch | 9/9 sweep points | PASS |
| 2 | adaptive research loop | actions ['loop_retry', 'loop_retry', 'converged'], grid multipliers [2.0, 4.0], iterations used 3 | fail → retry (2x grid) → converge within 3 iterations | 3 ≤ 3 | PASS |
| 3 | convergence guard | escalated at attempt 4, status 'blocked', block_reason 'convergence exhausted after 3 iterations' | 4th attempt blocked and marked escalated | blocked at attempt 4 | PASS |
| 4 | concurrency safety | 2 parallel evaluations → 2 audit events (1.0 per node), errors [] | two conditional edges evaluated concurrently, no race | 2 events / 2 evaluations | PASS |
| 5 | backward compatibility | 5 legacy plans × 5 nodes → all 'no_condition', statuses untouched | old plans without conditions behave as before | 5/5 plans unchanged | PASS |
| 6 | negative controls (3) | condition=NULL → no_condition; on_true==on_false → same target both ways; source_node 999999 → HTTP 404, 0 edges created | 3 controls behave as specified | 3/3 | PASS |
| 7 | API idempotency | repeat POST → id 3 status 'updated', rows in table 2 | repeat POST does not create a duplicate record | 2 rows | PASS |
| 8 | audit trail | events by type {'retry_node_created': 1, 'routing_evaluated': 1, 'edge_condition_created': 1} | every routing evaluation written to campaign_events | 3 events | PASS |

Summary: 8/8 criteria passed,
all_passed = `true`, 9 gate sweep
points, 3 negative controls, 5 legacy
plans regressed.

Figures (see `artifacts/campaign_routing/`):

- `fig1_gate_routing_sweep.png` — branch selected vs error over 0.01…0.10
  (step at the 0.05 threshold, strict `<`).
- `fig2_adaptive_loop_convergence.png` — error decay per refinement round with
  the gate threshold / convergence target lines and the 2x, 4x grid multipliers.
- `fig3_audit_and_iteration_guard.png` — audit events by type and the
  `max_iterations` guard (attempt 4 escalated).

### Residual risks / not covered

- The concurrency guard is a **per-process** `threading.Lock`; with several
  uvicorn workers the guarantee degrades to "each worker serialises its own
  routing". A DB advisory lock (`pg_advisory_xact_lock(plan_id)`) is the correct
  production fix and is **not** implemented here.
- Tests and the study run on SQLite; PostgreSQL JSONB behaviour is only covered
  by the migration DDL path, not by a live PG instance in this environment.
- Multi-metric conditions, cross-node convergence and frontend DAG visualisation
  remain unimplemented (unchanged from the original scope).
- No end-to-end dispatcher integration test: the router decides and activates,
  but whether the campaign dispatcher honours `action="loop_retry"` /
  `"escalated"` is outside this node's acceptance line.
