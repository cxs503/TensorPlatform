"""API surface: three verification endpoints, spec route, and strict 422 validation."""

from __future__ import annotations

import pytest

fastapi = pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

from tools.metrics_verify.api import SPEC, app  # noqa: E402


@pytest.fixture(scope="module")
def client() -> TestClient:
    return TestClient(app)


def _verify_routes() -> list:
    return sorted(
        route.path for route in app.routes
        if getattr(route, "path", "").startswith("/v1/metrics_verify")
        and "POST" in getattr(route, "methods", set())
    )


def test_exactly_three_verification_endpoints_exist():
    assert _verify_routes() == [
        "/v1/metrics_verify/chronicle",
        "/v1/metrics_verify/portrait",
        "/v1/metrics_verify/pulse",
    ]


def test_spec_endpoint_exposes_the_thresholds(client):
    body = client.get("/v1/metrics_verify/spec").json()
    assert body["spec"] == SPEC
    assert body["spec"]["pulse_latency_p99_max_s"] == 5.0
    assert body["spec"]["portrait_error_max"] == 0.05


def test_pulse_endpoint_returns_truth_and_recovered_values(client):
    response = client.post("/v1/metrics_verify/pulse",
                           json={"n_agents": 10, "n_heartbeats": 100, "seed": 3})
    assert response.status_code == 200
    body = response.json()
    assert body["ceiling"]["mean_pass"] is True
    assert body["latency"]["measured"]["n"] > 0
    assert body["core"]["pass"] is True
    assert "loss" in body and "sensitivity_control" in body


def test_chronicle_endpoint_reports_integrity(client):
    response = client.post("/v1/metrics_verify/chronicle",
                           json={"n_events": 2000, "seed": 3, "disorder_rate": 0.01})
    assert response.status_code == 200
    body = response.json()
    assert body["honest"]["completeness"] == 1.0
    assert body["disorder"]["detection_rate"] > 0.99
    assert body["criteria"]["honest_stream_integrity"] is True


def test_portrait_endpoint_reports_consistency_and_drift(client):
    response = client.post("/v1/metrics_verify/portrait",
                           json={"n_agents": 4, "missions_per_agent": 60, "seed": 3})
    assert response.status_code == 200
    body = response.json()
    assert body["consistency"]["pass"] is True
    assert body["criteria"]["corruption_detected"] is True
    assert body["drift"]["latency_max"] < 10


@pytest.mark.parametrize("path,payload", [
    ("/v1/metrics_verify/pulse", {"n_agents": 0}),
    ("/v1/metrics_verify/pulse", {"n_heartbeats": 1}),
    ("/v1/metrics_verify/pulse", {"mean_max_s": 10.0, "p99_max_s": 5.0}),
    ("/v1/metrics_verify/chronicle", {"n_events": -5}),
    ("/v1/metrics_verify/chronicle", {"disorder_rate": 2.0}),
    ("/v1/metrics_verify/portrait", {"n_agents": 2}),
    ("/v1/metrics_verify/portrait", {"missions_per_agent": "not-a-number"}),
    ("/v1/metrics_verify/portrait", {"tolerance": 0.0}),
])
def test_invalid_input_is_rejected_with_422(client, path, payload):
    assert client.post(path, json=payload).status_code == 422


def test_health_endpoint(client):
    body = client.get("/health").json()
    assert body["status"] == "ok" and body["endpoints"] == 3