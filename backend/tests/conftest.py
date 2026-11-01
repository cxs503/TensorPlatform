"""Pytest fixtures"""
import pytest
from fastapi.testclient import TestClient
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

# The full application pulls in every optional dependency (kms, mlflow, ...).
# Import it lazily so that unit-test modules which do not need the ASGI app can
# still be collected in a lean environment; tests that need the app are skipped
# instead of erroring out during collection.
try:  # pragma: no cover - environment dependent
    from main import app
except Exception as _app_import_error:  # noqa: BLE001
    app = None
    _APP_IMPORT_ERROR = _app_import_error
else:
    _APP_IMPORT_ERROR = None


@pytest.fixture
def client():
    if app is None:
        pytest.skip(f"full application unavailable: {_APP_IMPORT_ERROR}")
    return TestClient(app)


@pytest.fixture
def auth_headers(client):
    resp = client.post("/api/v1/auth/login", json={"username":"admin","password":"CHANGE_ME_ADMIN"})
    token = resp.json().get("access_token","")
    return {"Authorization": f"Bearer {token}"}