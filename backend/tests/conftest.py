"""Pytest fixtures"""
import pytest
from fastapi.testclient import TestClient
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
try:                                    # optional runtime deps (cryptography, ...)
    from main import app
except Exception as exc:                # noqa: BLE001
    _APP_IMPORT_ERROR = exc
    app = None
else:
    _APP_IMPORT_ERROR = None

@pytest.fixture
def client():
    if app is None:                     # pure-module test suites must still collect
        pytest.skip(f"FastAPI app unavailable: {_APP_IMPORT_ERROR}")
    return TestClient(app)

@pytest.fixture
def auth_headers(client):
    resp = client.post("/api/v1/auth/login", json={"username":"admin","password":"CHANGE_ME_ADMIN"})
    token = resp.json().get("access_token","")
    return {"Authorization": f"Bearer {token}"}
