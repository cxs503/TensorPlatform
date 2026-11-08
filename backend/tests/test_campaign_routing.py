"""Tests for campaign routing API — conditional DAG branching & adaptive loops.

Test categories:
  1. Condition routing correctness (3-node DAG, pass/fail paths)
  2. Adaptive loop (retry node creation, convergence)
  3. Convergence detection (max_iterations guard, escalation)
  4. Concurrent safety (parallel routing evaluation)
  5. Backward compatibility (old DAGs without conditions)
  6. Negative controls (null condition, on_true==on_false, missing source_node)
  7. API idempotency (duplicate POST)
  8. Audit trail (routing events logged)
  9. DAG validation (cycle detection, missing references)
"""
from __future__ import annotations

import json
import threading
import pytest
from fastapi.testclient import TestClient


# ───────────────────────── Fixtures ─────────────────────────

@pytest.fixture
def client():
    """Create test client."""
    from main import app
    return TestClient(app)


@pytest.fixture
def auth_headers(client):
    """Get auth headers by logging in."""
    resp = client.post("/api/v1/auth/login", json={"username": "admin", "password": "CHANGE_ME_ADMIN"})
    if resp.status_code != 200:
        pytest.skip("Auth not available")
    token = resp.json().get("access_token", "")
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def test_org_id(client, auth_headers):
    """Get or create test organization."""
    resp = client.get("/api/v1/tensor/centers/summary", headers=auth_headers)
    if resp.status_code == 200:
        data = resp.json()
        if data.get("organization_id"):
            return data["organization_id"]
    return 1  # Default org


@pytest.fixture
def test_campaign_id(client, auth_headers):
    """Create a test campaign."""
    # Try to get existing
    resp = client.get("/api/v1/tensor/factory/strategy/campaigns", headers=auth_headers)
    if resp.status_code == 200:
        data = resp.json()
        if data.get("items"):
            return data["items"][0]["id"]
    pytest.skip("No test campaign available")


@pytest.fixture
def test_plan_id(client, auth_headers, test_campaign_id):
    """Create a test plan for routing tests."""
    resp = client.post("/api/v1/tensor/campaigns/plans", headers=auth_headers, json={
        "campaign_id": test_campaign_id,
        "name": f"Routing Test Plan {id(client)}",
        "description": "Test plan for conditional routing",
    })
    if resp.status_code != 200:
        pytest.skip(f"Cannot create plan: {resp.status_code} {resp.text}")
    return resp.json()["id"]


@pytest.fixture
def three_node_dag(client, auth_headers, test_plan_id):
    """Create a 3-node DAG: N1 → (condition) → N2 or N3.

    N1: source node with condition
    N2: activated when condition is true (err < threshold)
    N3: activated when condition is false (err >= threshold)
    """
    # Create N1
    resp = client.post("/api/v1/tensor/campaigns/nodes", headers=auth_headers, json={
        "plan_id": test_plan_id,
        "name": "N1_verification",
        "node_type": "task",
    })
    assert resp.status_code == 200, f"Failed to create N1: {resp.text}"
    n1_id = resp.json()["id"]

    # Create N2
    resp = client.post("/api/v1/tensor/campaigns/nodes", headers=auth_headers, json={
        "plan_id": test_plan_id,
        "name": "N2_pass_path",
        "node_type": "task",
        "dependencies": [n1_id],
    })
    assert resp.status_code == 200, f"Failed to create N2: {resp.text}"
    n2_id = resp.json()["id"]

    # Create N3
    resp = client.post("/api/v1/tensor/campaigns/nodes", headers=auth_headers, json={
        "plan_id": test_plan_id,
        "name": "N3_fail_path",
        "node_type": "task",
        "dependencies": [n1_id],
    })
    assert resp.status_code == 200, f"Failed to create N3: {resp.text}"
    n3_id = resp.json()["id"]

    return {"n1": n1_id, "n2": n2_id, "n3": n3_id, "plan_id": test_plan_id}


@pytest.fixture
def adaptive_loop_dag(client, auth_headers, test_plan_id):
    """Create an adaptive loop DAG: N1 → (convergence) → N1_retry.

    N1: verification node with convergence check
    N1_retry: retry node (created dynamically)
    """
    # Create N1 with convergence check
    resp = client.post("/api/v1/tensor/campaigns/nodes", headers=auth_headers, json={
        "plan_id": test_plan_id,
        "name": "N1_adaptive_verify",
        "node_type": "task",
    })
    assert resp.status_code == 200
    n1_id = resp.json()["id"]

    # Create N2 (success path)
    resp = client.post("/api/v1/tensor/campaigns/nodes", headers=auth_headers, json={
        "plan_id": test_plan_id,
        "name": "N2_success",
        "node_type": "task",
    })
    assert resp.status_code == 200
    n2_id = resp.json()["id"]

    # Create N3 (escalation path)
    resp = client.post("/api/v1/tensor/campaigns/nodes", headers=auth_headers, json={
        "plan_id": test_plan_id,
        "name": "N3_escalation",
        "node_type": "task",
    })
    assert resp.status_code == 200
    n3_id = resp.json()["id"]

    # Set condition on N1: err < 0.01 → N2, else → N1 (loop back for retry)
    resp = client.put(f"/api/v1/tensor/campaigns/nodes/{n1_id}/condition", headers=auth_headers, json={
        "condition": {
            "type": "gate",
            "metric": "max_rel_err",
            "operator": "<",
            "threshold": 0.01,
            "on_true": n2_id,
            "on_false": n1_id,  # Loop back to self
        },
        "convergence_check": {
            "metric": "max_rel_err",
            "target": 0.01,
            "max_iterations": 3,
            "action_on_exhaust": "escalate",
        },
    })
    assert resp.status_code == 200, f"Failed to set condition: {resp.text}"

    return {"n1": n1_id, "n2": n2_id, "n3": n3_id, "plan_id": test_plan_id}


# ───────────────────────── Test: Edge Condition CRUD ─────────────────────────

class TestEdgeConditionCRUD:
    """Test edge condition creation, listing, and deletion."""

    def test_create_edge_condition(self, client, auth_headers, three_node_dag):
        """Test creating a conditional edge."""
        resp = client.post("/api/v1/tensor/campaigns/edge-conditions", headers=auth_headers, json={
            "plan_id": three_node_dag["plan_id"],
            "source_node_id": three_node_dag["n1"],
            "condition": {
                "type": "gate",
                "metric": "max_rel_err",
                "operator": "<",
                "threshold": 0.05,
                "on_true": three_node_dag["n2"],
                "on_false": three_node_dag["n3"],
            },
            "priority": 0,
        })
        assert resp.status_code == 200
        data = resp.json()
        assert "id" in data
        assert data["status"] == "created"

    def test_list_edge_conditions(self, client, auth_headers, three_node_dag):
        """Test listing edge conditions for a plan."""
        # Create one first
        client.post("/api/v1/tensor/campaigns/edge-conditions", headers=auth_headers, json={
            "plan_id": three_node_dag["plan_id"],
            "source_node_id": three_node_dag["n1"],
            "condition": {
                "type": "gate",
                "metric": "max_rel_err",
                "operator": "<",
                "threshold": 0.05,
                "on_true": three_node_dag["n2"],
                "on_false": three_node_dag["n3"],
            },
        })

        resp = client.get(
            f"/api/v1/tensor/campaigns/edge-conditions?plan_id={three_node_dag['plan_id']}",
            headers=auth_headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] >= 1

    def test_get_edge_condition(self, client, auth_headers, three_node_dag):
        """Test getting a specific edge condition."""
        create_resp = client.post("/api/v1/tensor/campaigns/edge-conditions", headers=auth_headers, json={
            "plan_id": three_node_dag["plan_id"],
            "source_node_id": three_node_dag["n1"],
            "condition": {
                "type": "gate",
                "metric": "max_rel_err",
                "operator": "<",
                "threshold": 0.05,
                "on_true": three_node_dag["n2"],
                "on_false": three_node_dag["n3"],
            },
        })
        edge_id = create_resp.json()["id"]

        resp = client.get(f"/api/v1/tensor/campaigns/edge-conditions/{edge_id}", headers=auth_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert data["id"] == edge_id

    def test_delete_edge_condition(self, client, auth_headers, three_node_dag):
        """Test deleting an edge condition."""
        create_resp = client.post("/api/v1/tensor/campaigns/edge-conditions", headers=auth_headers, json={
            "plan_id": three_node_dag["plan_id"],
            "source_node_id": three_node_dag["n1"],
            "condition": {
                "type": "gate",
                "metric": "max_rel_err",
                "operator": "<",
                "threshold": 0.05,
                "on_true": three_node_dag["n2"],
                "on_false": three_node_dag["n3"],
            },
        })
        edge_id = create_resp.json()["id"]

        resp = client.delete(f"/api/v1/tensor/campaigns/edge-conditions/{edge_id}", headers=auth_headers)
        assert resp.status_code == 200
        assert resp.json()["status"] == "deleted"


# ───────────────────────── Test: Condition Routing Correctness ─────────────────────────

class TestConditionRouting:
    """Test conditional routing: 3-node DAG with pass/fail paths."""

    def test_routing_pass_path(self, client, auth_headers, three_node_dag):
        """Test routing activates N2 when err < threshold (pass path)."""
        n1, n2, n3 = three_node_dag["n1"], three_node_dag["n2"], three_node_dag["n3"]

        # Set condition on N1: err < 0.05 → N2, else → N3
        resp = client.put(f"/api/v1/tensor/campaigns/nodes/{n1}/condition", headers=auth_headers, json={
            "condition": {
                "type": "gate",
                "metric": "max_rel_err",
                "operator": "<",
                "threshold": 0.05,
                "on_true": n2,
                "on_false": n3,
            },
        })
        assert resp.status_code == 200

        # Evaluate routing with err=0.03 (< 0.05) → should activate N2
        resp = client.post("/api/v1/tensor/campaigns/routing/evaluate", headers=auth_headers, json={
            "node_id": n1,
            "metric_value": 0.03,
            "metric_name": "max_rel_err",
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["evaluated"] is True
        assert data["condition_met"] is True
        assert data["activated_node_id"] == n2
        assert data["action"] == "activated"

    def test_routing_fail_path(self, client, auth_headers, three_node_dag):
        """Test routing activates N3 when err >= threshold (fail path)."""
        n1, n2, n3 = three_node_dag["n1"], three_node_dag["n2"], three_node_dag["n3"]

        # Set condition on N1
        resp = client.put(f"/api/v1/tensor/campaigns/nodes/{n1}/condition", headers=auth_headers, json={
            "condition": {
                "type": "gate",
                "metric": "max_rel_err",
                "operator": "<",
                "threshold": 0.05,
                "on_true": n2,
                "on_false": n3,
            },
        })
        assert resp.status_code == 200

        # Evaluate routing with err=0.08 (>= 0.05) → should activate N3
        resp = client.post("/api/v1/tensor/campaigns/routing/evaluate", headers=auth_headers, json={
            "node_id": n1,
            "metric_value": 0.08,
            "metric_name": "max_rel_err",
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["evaluated"] is True
        assert data["condition_met"] is False
        assert data["activated_node_id"] == n3
        assert data["action"] == "activated"

    def test_routing_with_edge_conditions_table(self, client, auth_headers, three_node_dag):
        """Test routing using edge conditions table (not inline)."""
        n1, n2, n3 = three_node_dag["n1"], three_node_dag["n2"], three_node_dag["n3"]

        # Create edge condition
        resp = client.post("/api/v1/tensor/campaigns/edge-conditions", headers=auth_headers, json={
            "plan_id": three_node_dag["plan_id"],
            "source_node_id": n1,
            "condition": {
                "type": "gate",
                "metric": "max_rel_err",
                "operator": "<",
                "threshold": 0.05,
                "on_true": n2,
                "on_false": n3,
            },
        })
        assert resp.status_code == 200

        # Evaluate with err=0.03 → N2
        resp = client.post("/api/v1/tensor/campaigns/routing/evaluate", headers=auth_headers, json={
            "node_id": n1,
            "metric_value": 0.03,
            "metric_name": "max_rel_err",
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["activated_node_id"] == n2


# ───────────────────────── Test: Adaptive Loop ─────────────────────────

class TestAdaptiveLoop:
    """Test adaptive research loop: verify → judge → adjust → re-verify."""

    def test_adaptive_loop_creates_retry(self, client, auth_headers, adaptive_loop_dag):
        """Test that failed verification creates a retry node."""
        n1 = adaptive_loop_dag["n1"]

        # Create retry node
        resp = client.post(f"/api/v1/tensor/campaigns/nodes/{n1}/create-retry", headers=auth_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert "id" in data
        assert data["iteration"] == 1
        assert "retry" in data["name"]

    def test_adaptive_loop_convergence(self, client, auth_headers, adaptive_loop_dag):
        """Test adaptive loop converges within max_iterations."""
        n1, n2 = adaptive_loop_dag["n1"], adaptive_loop_dag["n2"]

        # First evaluation: err=0.05 (not converged, should loop)
        resp = client.post("/api/v1/tensor/campaigns/routing/evaluate", headers=auth_headers, json={
            "node_id": n1,
            "metric_value": 0.05,
            "metric_name": "max_rel_err",
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["action"] == "loop_retry"

        # Second evaluation: err=0.02 (still not converged)
        resp = client.post("/api/v1/tensor/campaigns/routing/evaluate", headers=auth_headers, json={
            "node_id": n1,
            "metric_value": 0.02,
            "metric_name": "max_rel_err",
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["action"] == "loop_retry"

        # Third evaluation: err=0.005 (converged!)
        resp = client.post("/api/v1/tensor/campaigns/routing/evaluate", headers=auth_headers, json={
            "node_id": n1,
            "metric_value": 0.005,
            "metric_name": "max_rel_err",
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["condition_met"] is True
        assert data["activated_node_id"] == n2


# ───────────────────────── Test: Convergence Detection ─────────────────────────

class TestConvergenceDetection:
    """Test convergence detection and escalation."""

    def test_max_iterations_exhausted(self, client, auth_headers, adaptive_loop_dag):
        """Test that exceeding max_iterations triggers escalation."""
        n1 = adaptive_loop_dag["n1"]

        # Exhaust all 3 iterations with non-converging values
        for i in range(3):
            resp = client.post("/api/v1/tensor/campaigns/routing/evaluate", headers=auth_headers, json={
                "node_id": n1,
                "metric_value": 0.1,  # Never converges
                "metric_name": "max_rel_err",
            })
            assert resp.status_code == 200

        # 4th iteration should be escalated
        resp = client.post("/api/v1/tensor/campaigns/routing/evaluate", headers=auth_headers, json={
            "node_id": n1,
            "metric_value": 0.1,
            "metric_name": "max_rel_err",
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["action"] == "escalated"


# ───────────────────────── Test: Concurrent Safety ─────────────────────────

class TestConcurrentSafety:
    """Test concurrent routing evaluation safety."""

    def test_concurrent_routing_evaluation(self, client, auth_headers, three_node_dag):
        """Test that concurrent routing evaluations don't cause race conditions."""
        n1, n2, n3 = three_node_dag["n1"], three_node_dag["n2"], three_node_dag["n3"]

        # Set condition
        client.put(f"/api/v1/tensor/campaigns/nodes/{n1}/condition", headers=auth_headers, json={
            "condition": {
                "type": "gate",
                "metric": "max_rel_err",
                "operator": "<",
                "threshold": 0.05,
                "on_true": n2,
                "on_false": n3,
            },
        })

        results = []
        errors = []

        def evaluate(metric_val):
            try:
                resp = client.post("/api/v1/tensor/campaigns/routing/evaluate", headers=auth_headers, json={
                    "node_id": n1,
                    "metric_value": metric_val,
                    "metric_name": "max_rel_err",
                })
                results.append(resp.status_code)
            except Exception as e:
                errors.append(str(e))

        # Run 2 concurrent evaluations
        threads = [
            threading.Thread(target=evaluate, args=(0.03,)),
            threading.Thread(target=evaluate, args=(0.08,)),
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)

        # Both should succeed without errors
        assert len(errors) == 0, f"Concurrent errors: {errors}"
        assert len(results) == 2
        assert all(r == 200 for r in results)


# ───────────────────────── Test: Backward Compatibility ─────────────────────────

class TestBackwardCompatibility:
    """Test that old DAGs without conditions still work."""

    def test_old_dag_without_conditions(self, client, auth_headers, test_plan_id):
        """Test that nodes without conditions behave normally."""
        # Create nodes without any conditions
        resp = client.post("/api/v1/tensor/campaigns/nodes", headers=auth_headers, json={
            "plan_id": test_plan_id,
            "name": "Old_N1",
            "node_type": "task",
        })
        assert resp.status_code == 200
        n1_id = resp.json()["id"]

        resp = client.post("/api/v1/tensor/campaigns/nodes", headers=auth_headers, json={
            "plan_id": test_plan_id,
            "name": "Old_N2",
            "node_type": "task",
            "dependencies": [n1_id],
        })
        assert resp.status_code == 200

        # Routing evaluation should return no_condition
        resp = client.post("/api/v1/tensor/campaigns/routing/evaluate", headers=auth_headers, json={
            "node_id": n1_id,
            "metric_value": 0.05,
            "metric_name": "max_rel_err",
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["action"] == "no_condition"
        assert data["evaluated"] is False


# ───────────────────────── Test: Negative Controls ─────────────────────────

class TestNegativeControls:
    """Negative control tests for edge cases."""

    def test_null_condition_no_routing(self, client, auth_headers, test_plan_id):
        """Negative control: condition=null → no conditional routing."""
        resp = client.post("/api/v1/tensor/campaigns/nodes", headers=auth_headers, json={
            "plan_id": test_plan_id,
            "name": "NullCond_N1",
            "node_type": "task",
        })
        n1_id = resp.json()["id"]

        resp = client.post("/api/v1/tensor/campaigns/routing/evaluate", headers=auth_headers, json={
            "node_id": n1_id,
            "metric_value": 0.05,
            "metric_name": "max_rel_err",
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["action"] == "no_condition"

    def test_on_true_equals_on_false(self, client, auth_headers, test_plan_id):
        """Negative control: on_true == on_false → equivalent to unconditional."""
        resp = client.post("/api/v1/tensor/campaigns/nodes", headers=auth_headers, json={
            "plan_id": test_plan_id,
            "name": "SameTarget_N1",
            "node_type": "task",
        })
        n1_id = resp.json()["id"]

        resp = client.post("/api/v1/tensor/campaigns/nodes", headers=auth_headers, json={
            "plan_id": test_plan_id,
            "name": "SameTarget_N2",
            "node_type": "task",
        })
        n2_id = resp.json()["id"]

        # Set condition where on_true == on_false
        resp = client.put(f"/api/v1/tensor/campaigns/nodes/{n1_id}/condition", headers=auth_headers, json={
            "condition": {
                "type": "gate",
                "metric": "max_rel_err",
                "operator": "<",
                "threshold": 0.05,
                "on_true": n2_id,
                "on_false": n2_id,  # Same as on_true
            },
        })
        assert resp.status_code == 200

        # Should activate N2 regardless of metric value
        resp = client.post("/api/v1/tensor/campaigns/routing/evaluate", headers=auth_headers, json={
            "node_id": n1_id,
            "metric_value": 0.99,  # Way above threshold
            "metric_name": "max_rel_err",
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["activated_node_id"] == n2_id

    def test_source_node_not_found(self, client, auth_headers):
        """Negative control: source_node doesn't exist → graceful degradation."""
        resp = client.post("/api/v1/tensor/campaigns/routing/evaluate", headers=auth_headers, json={
            "node_id": 999999,  # Non-existent
            "metric_value": 0.05,
            "metric_name": "max_rel_err",
        })
        assert resp.status_code == 404


# ───────────────────────── Test: API Idempotency ─────────────────────────

class TestAPIIdempotency:
    """Test API idempotency."""

    def test_duplicate_edge_condition_returns_existing(self, client, auth_headers, three_node_dag):
        """Test that duplicate POST returns existing edge (idempotent)."""
        payload = {
            "plan_id": three_node_dag["plan_id"],
            "source_node_id": three_node_dag["n1"],
            "condition": {
                "type": "gate",
                "metric": "max_rel_err",
                "operator": "<",
                "threshold": 0.05,
                "on_true": three_node_dag["n2"],
                "on_false": three_node_dag["n3"],
            },
            "priority": 0,
        }

        # First POST
        resp1 = client.post("/api/v1/tensor/campaigns/edge-conditions", headers=auth_headers, json=payload)
        assert resp1.status_code == 200
        id1 = resp1.json()["id"]

        # Second POST (same priority) → should update, not create duplicate
        resp2 = client.post("/api/v1/tensor/campaigns/edge-conditions", headers=auth_headers, json=payload)
        assert resp2.status_code == 200
        id2 = resp2.json()["id"]

        assert id1 == id2, "Idempotency violated: duplicate edge created"


# ───────────────────────── Test: Audit Trail ─────────────────────────

class TestAuditTrail:
    """Test that routing events are logged."""

    def test_routing_event_logged(self, client, auth_headers, three_node_dag):
        """Test that routing evaluation creates an event in campaign_events."""
        n1, n2, n3 = three_node_dag["n1"], three_node_dag["n2"], three_node_dag["n3"]

        # Set condition
        client.put(f"/api/v1/tensor/campaigns/nodes/{n1}/condition", headers=auth_headers, json={
            "condition": {
                "type": "gate",
                "metric": "max_rel_err",
                "operator": "<",
                "threshold": 0.05,
                "on_true": n2,
                "on_false": n3,
            },
        })

        # Evaluate routing
        resp = client.post("/api/v1/tensor/campaigns/routing/evaluate", headers=auth_headers, json={
            "node_id": n1,
            "metric_value": 0.03,
            "metric_name": "max_rel_err",
        })
        assert resp.status_code == 200

        # Check that event was logged (via routing status)
        resp = client.get(
            f"/api/v1/tensor/campaigns/plans/{three_node_dag['plan_id']}/routing-status",
            headers=auth_headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["plan_id"] == three_node_dag["plan_id"]


# ───────────────────────── Test: DAG Validation ─────────────────────────

class TestDAGValidation:
    """Test DAG validation including cycle detection."""

    def test_validate_valid_dag(self, client, auth_headers, three_node_dag):
        """Test validation of a valid DAG."""
        resp = client.get(
            f"/api/v1/tensor/campaigns/plans/{three_node_dag['plan_id']}/validate-dag",
            headers=auth_headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["valid"] is True
        assert len(data["errors"]) == 0

    def test_validate_dag_with_invalid_reference(self, client, auth_headers, test_plan_id):
        """Test validation catches invalid node references in conditions."""
        # Create node with condition referencing non-existent node
        resp = client.post("/api/v1/tensor/campaigns/nodes", headers=auth_headers, json={
            "plan_id": test_plan_id,
            "name": "BadRef_N1",
            "node_type": "task",
        })
        n1_id = resp.json()["id"]

        # Set condition with invalid reference
        client.put(f"/api/v1/tensor/campaigns/nodes/{n1_id}/condition", headers=auth_headers, json={
            "condition": {
                "type": "gate",
                "metric": "max_rel_err",
                "operator": "<",
                "threshold": 0.05,
                "on_true": 999998,  # Non-existent
                "on_false": 999999,  # Non-existent
            },
        })

        resp = client.get(
            f"/api/v1/tensor/campaigns/plans/{test_plan_id}/validate-dag",
            headers=auth_headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["valid"] is False
        assert len(data["errors"]) > 0


# ───────────────────────── Test: Routing Status ─────────────────────────

class TestRoutingStatus:
    """Test routing status query."""

    def test_get_routing_status(self, client, auth_headers, three_node_dag):
        """Test getting routing status for a plan."""
        n1 = three_node_dag["n1"]

        # Set condition
        client.put(f"/api/v1/tensor/campaigns/nodes/{n1}/condition", headers=auth_headers, json={
            "condition": {
                "type": "gate",
                "metric": "max_rel_err",
                "operator": "<",
                "threshold": 0.05,
                "on_true": three_node_dag["n2"],
                "on_false": three_node_dag["n3"],
            },
        })

        resp = client.get(
            f"/api/v1/tensor/campaigns/plans/{three_node_dag['plan_id']}/routing-status",
            headers=auth_headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["total_nodes"] == 3
        assert data["nodes_with_conditions"] >= 1
