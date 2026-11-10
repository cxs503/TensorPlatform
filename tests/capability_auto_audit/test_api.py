"""HTTP API tests for the auto-audit router (p262w1-N07).

The router is mounted by ``backend/main.py`` at ``/api/v1``; here it is exercised
against a throw-away FastAPI app so the test does not need the whole backend.
"""
from __future__ import annotations

import pytest

fastapi = pytest.importorskip("fastapi")
from fastapi import Depends, FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from tools.capability_auto_audit.api import build_router, mount  # noqa: E402
from tools.capability_auto_audit.auto_audit_engine import AutoAuditEngine  # noqa: E402
from tools.capability_auto_audit.evidence_chain import ChainSpec  # noqa: E402


@pytest.fixture
def client(tmp_path, make_case):
    engine = AutoAuditEngine(verified_dir=make_case.root,
                             state_path=tmp_path / "state" / "ledger_state.json",
                             log_path=tmp_path / "state" / "audit_log.jsonl",
                             spec=ChainSpec())
    app = FastAPI()
    mount(app, engine, prefix="/api/v1")
    return TestClient(app), engine


def test_auto_audit_endpoint_promotes_and_logs(client, make_case):
    http, engine = client
    make_case("syn.api")

    response = http.post("/api/v1/capabilities/syn.api/auto-audit")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    outcome = body["outcome"]
    assert outcome["from_level"] == "WITHHELD"
    assert outcome["to_level"] == "REAL"
    assert outcome["action"] == "UPGRADE"
    assert outcome["changed"] is True
    assert outcome["latency_s"] < 5.0
    assert len(outcome["chain_digest"]) == 64
    assert engine.level_of("syn.api") == "REAL"


def test_level_and_levels_endpoints(client, make_case):
    http, _ = client
    make_case("syn.api2")
    http.post("/api/v1/capabilities/syn.api2/auto-audit")

    level = http.get("/api/v1/capabilities/syn.api2/level")
    assert level.status_code == 200
    assert level.json()["level"] == "REAL"

    levels = http.get("/api/v1/capabilities/levels").json()
    assert levels["levels"]["syn.api2"] == "REAL"
    assert len(levels["spec_digest"]) == 64

    assert http.get("/api/v1/capabilities/syn.nothing/level").status_code == 404


def test_audit_log_endpoints_expose_a_verified_chain(client, make_case):
    http, _ = client
    make_case("syn.api3")
    for _ in range(3):
        http.post("/api/v1/capabilities/syn.api3/auto-audit")

    verify = http.get("/api/v1/capabilities/audit-log/verify").json()
    assert verify["ok"] is True and verify["entries"] == 3

    tail = http.get("/api/v1/capabilities/audit-log?limit=2").json()
    assert tail["count"] == 2
    assert tail["head_sha256"] == verify["head_sha256"]
    assert [e["seq"] for e in tail["entries"]] == [2, 3]


def test_auth_dependency_is_enforced_when_supplied(tmp_path, make_case):
    calls = []

    def fake_auth():
        calls.append(1)
        return {"user": "tester"}

    engine = AutoAuditEngine(verified_dir=make_case.root,
                             state_path=tmp_path / "s.json",
                             log_path=tmp_path / "l.jsonl", spec=ChainSpec())
    app = FastAPI()
    app.include_router(build_router(engine, fake_auth), prefix="/api/v1")
    http = TestClient(app)
    make_case("syn.auth")
    assert http.post("/api/v1/capabilities/syn.auth/auto-audit").status_code == 200
    assert calls, "auth dependency was not invoked"
    assert Depends is not None


def test_main_application_mounts_the_router():
    """Declarative check of backend/main.py wiring (no app import: heavy deps)."""
    from pathlib import Path

    source = Path("backend/main.py").read_text(encoding="utf-8")
    assert "tools.capability_auto_audit.api import build_router" in source
    assert '/api/v1/capabilities/{id}/auto-audit' in source  # log line documents the route
    assert "capability_chain_spec.json" in source