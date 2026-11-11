#!/usr/bin/env python3
"""Publish the metrics-verify paper report to the platform reports API."""
import json
import os

import httpx

base = os.environ["TENSOR_API_BASE"].rstrip("/")
user = os.environ["TENSOR_AGENT_USER"]
password = os.environ["TENSOR_AGENT_PASS"]

payload = json.load(open("/tmp/report.json"))

with httpx.Client(timeout=180.0) as client:
    login = client.post(f"{base}/auth/login",
                        json={"username": user, "password": password})
    login.raise_for_status()
    body = login.json()
    token = body.get("access_token") or body.get("token")
    if not token:
        token = (body.get("data") or {}).get("access_token")
    if not token:
        raise SystemExit(f"no token in login response keys: {list(body)}")
    resp = client.post(f"{base}/reports", json=payload,
                       headers={"Authorization": f"Bearer {token}"})
    print(resp.status_code, resp.text[:800])
    resp.raise_for_status()