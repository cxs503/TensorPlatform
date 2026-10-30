#!/usr/bin/env python3
"""Try different login endpoints."""
import urllib.request
import json
import os

base_url = "http://127.0.0.1:8004/api/v1"
login_data = json.dumps({
    "username": os.getenv("TENSOR_AGENT_USER", "eco_admin"),
    "password": os.getenv("TENSOR_AGENT_PASS", "EcoTest!2026pass")
}).encode('utf-8')

endpoints_to_try = [
    "/login",
    "/auth/login", 
    "/auth",
    "/token",
    "/auth/token"
]

for endpoint in endpoints_to_try:
    url = f"{base_url}{endpoint}"
    try:
        req = urllib.request.Request(url, data=login_data, method='POST')
        req.add_header('Content-Type', 'application/json')
        with urllib.request.urlopen(req, timeout=5) as response:
            result = json.loads(response.read().decode('utf-8'))
            print(f"✓ {endpoint}: SUCCESS")
            print(f"  Response: {json.dumps(result, indent=2)[:200]}")
            break
    except urllib.error.HTTPError as e:
        print(f"✗ {endpoint}: HTTP {e.code} {e.reason}")
    except Exception as e:
        print(f"✗ {endpoint}: {e}")
