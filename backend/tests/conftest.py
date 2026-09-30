"""Pytest fixtures"""
import pytest
from fastapi.testclient import TestClient
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
from main import app

@pytest.fixture
def client():
    return TestClient(app)

@pytest.fixture
def auth_headers(client):
    resp = client.post("/api/v1/auth/login", json={"username":"admin","password":"CHANGE_ME_ADMIN"})
    token = resp.json().get("access_token","")
    return {"Authorization": f"Bearer {token}"}
