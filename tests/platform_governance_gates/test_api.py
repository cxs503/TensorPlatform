"""HTTP surface of the three gates: status codes, payload shape, latency."""
from __future__ import annotations

import time

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from tools.platform_governance_gates.api import build_router
from tools.platform_governance_gates.report_fingerprint import (
    body_sha256,
    metadata_sha256,
)


@pytest.fixture(scope="module")
def client():
    app = FastAPI()
    app.include_router(build_router(), prefix="/api/v1")
    return TestClient(app)


def _complete_capability(cap="demo.api", level="MOCK"):
    return {
        "capability_id": cap,
        "current_level": level,
        "items": [
            {"kind": "verification", "ref": "reports/v.json", "independent": True},
            {"kind": "cross_check", "ref": "artifacts/x.json"},
            {"kind": "benchmark", "ref": "benchmarks/b.json"},
        ],
    }


def _report(report_id=1, mission_id=10, body="# demo\n"):
    meta = {"report_id": report_id, "mission_id": mission_id}
    return {
        "report_id": report_id,
        "mission_id": mission_id,
        "body": body,
        "metadata": meta,
        "declared_body_sha256": body_sha256(body),
        "declared_metadata_sha256": metadata_sha256(meta),
    }


def test_health(client):
    response = client.get("/api/v1/platform-governance/health")
    assert response.status_code == 200
    body = response.json()
    assert body["guards"] == ["ledger", "report_fingerprint", "status_drift"]
    assert len(body["requirements"]) == 6
    assert body["latency_ms"] < 100


def test_ledger_scan_promotes_and_reports_completeness(client):
    response = client.post("/api/v1/platform-governance/ledger/scan", json={
        "capabilities": [_complete_capability()]})
    assert response.status_code == 200
    body = response.json()
    assert body["decisions"][0]["to_level"] == "REAL"
    assert body["completeness"]["demo.api"]["score"] == 1.0
    assert body["latency_ms"] < 100


def test_ledger_scan_rejects_inverted_thresholds(client):
    response = client.post("/api/v1/platform-governance/ledger/scan", json={
        "capabilities": [_complete_capability()], "promote": 0.4, "demote": 0.8})
    assert response.status_code == 422


def test_report_gate_detects_duplicate(client):
    response = client.post("/api/v1/platform-governance/report-gate/check", json={
        "reports": [_report(1, 10, "# same\n"), _report(2, 20, "# same\n")]})
    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is False
    assert "DUPLICATE_BODY" in body["defect_codes"]
    assert body["latency_ms"] < 100


def test_report_gate_accepts_clean_batch(client):
    response = client.post("/api/v1/platform-governance/report-gate/check", json={
        "reports": [_report(1, 10, "# a\n"), _report(2, 20, "# b\n")]})
    body = response.json()
    assert body["ok"] is True
    assert body["defects"] == []


def test_drift_scan_detects_and_converges(client):
    response = client.post("/api/v1/platform-governance/drift/scan", json={
        "nodes": [{"node_id": "n1", "status": "blocked", "mission_id": 7}],
        "missions": [{"mission_id": 7, "status": "done", "result": {"merged": True}}],
    })
    assert response.status_code == 200
    body = response.json()
    assert body["detected"] == 1
    assert body["updates"][0]["after"] == "done"
    assert body["findings"][0]["merged_but_blocked"] is True
    assert body["latency_ms"] < 100


def test_endpoints_stay_under_100ms(client):
    payloads = [
        ("/api/v1/platform-governance/ledger/scan",
         {"capabilities": [_complete_capability()]}),
        ("/api/v1/platform-governance/report-gate/check",
         {"reports": [_report(1, 10, "# a\n"), _report(2, 20, "# b\n")]}),
        ("/api/v1/platform-governance/drift/scan",
         {"nodes": [{"node_id": "n1", "status": "pending", "mission_id": 1}],
          "missions": [{"mission_id": 1, "status": "done", "result": {}}]}),
    ]
    for path, payload in payloads:
        started = time.perf_counter()
        response = client.post(path, json=payload)
        elapsed_ms = (time.perf_counter() - started) * 1000
        assert response.status_code == 200
        assert elapsed_ms < 100, (path, elapsed_ms)