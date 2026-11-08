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
- No automatic parameter adjustment for retry nodes (manual or external orchestration required)

## References

- Mission m6722 task specification
- Campaign plans API: `backend/api/campaign_plans.py`
- Migration: `backend/migrations/108_campaign_edge_conditions.sql`
- Tests: `backend/tests/test_campaign_routing.py`
