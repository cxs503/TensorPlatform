#!/usr/bin/env python3
"""Check API endpoints and methods."""
import urllib.request
import json

base_url = "http://127.0.0.1:8004/api/v1"

# Try to get OpenAPI spec
endpoints = [
    "/openapi.json",
    "/docs",
    "/redoc",
    "/api",
    "/"
]

for endpoint in endpoints:
    url = f"{base_url}{endpoint}" if not endpoint.startswith("/") else f"{base_url}{endpoint}"
    try:
        req = urllib.request.Request(url, method='GET')
        with urllib.request.urlopen(req, timeout=5) as response:
            print(f"✓ GET {endpoint}: {response.status}")
            if endpoint == "/openapi.json":
                data = json.loads(response.read().decode('utf-8'))
                print(f"  Title: {data.get('info', {}).get('title')}")
                print(f"  Paths: {len(data.get('paths', {}))}")
                # Find login-related paths
                for path in data.get('paths', {}).keys():
                    if 'login' in path.lower() or 'auth' in path.lower():
                        print(f"  Found: {path}")
            break
    except urllib.error.HTTPError as e:
        print(f"✗ GET {endpoint}: HTTP {e.code}")
    except Exception as e:
        print(f"✗ GET {endpoint}: {e}")

# Try OPTIONS on login endpoint
print("\nTrying OPTIONS on /login:")
try:
    req = urllib.request.Request(f"{base_url}/login", method='OPTIONS')
    with urllib.request.urlopen(req, timeout=5) as response:
        print(f"✓ OPTIONS /login: {response.status}")
        print(f"  Headers: {dict(response.headers)}")
except Exception as e:
    print(f"✗ OPTIONS /login: {e}")
