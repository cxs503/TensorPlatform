#!/usr/bin/env python3
"""Submit report to Tensor API."""

import json
import os
import sys
import requests
from pathlib import Path

def main():
    api_base = os.environ.get("TENSOR_API_BASE", "http://127.0.0.1:8010/api/v1")
    username = os.environ.get("TENSOR_AGENT_USER", "eco_admin")
    password = os.environ.get("TENSOR_AGENT_PASS", "")

    if not password:
        print("ERROR: TENSOR_AGENT_PASS not set")
        return 1

    # Login
    print(f"Logging in as {username}...")
    try:
        resp = requests.post(
            f"{api_base}/auth/login",
            json={"username": username, "password": password},
            timeout=10,
        )
        if resp.status_code != 200:
            print(f"Login failed: {resp.status_code} {resp.text}")
            return 1
        token = resp.json().get("access_token")
        if not token:
            print("No access_token in response")
            return 1
        print(f"Login successful, token: {token[:20]}...")
    except Exception as e:
        print(f"Login error: {e}")
        return 1

    # Load report
    report_path = Path("/tmp/report.json")
    if not report_path.exists():
        print(f"Report not found: {report_path}")
        return 1

    report = json.loads(report_path.read_text())
    print(f"Report loaded: {len(report.get('markdown', ''))} chars, {len(report.get('figures', []))} figures")

    # Submit report
    print("Submitting report...")
    try:
        resp = requests.post(
            f"{api_base}/tensor/reports",
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
            },
            json=report,
            timeout=30,
        )
        if resp.status_code in (200, 201):
            print(f"Report submitted successfully: {resp.status_code}")
            result = resp.json()
            print(f"Report ID: {result.get('id', 'N/A')}")
            return 0
        else:
            print(f"Report submission failed: {resp.status_code}")
            print(resp.text[:500])
            return 1
    except Exception as e:
        print(f"Report submission error: {e}")
        return 1

if __name__ == "__main__":
    sys.exit(main())
