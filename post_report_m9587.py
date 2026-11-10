#!/usr/bin/env python3
"""Post the BEM+LBM cross-validation report to the tensor platform."""
import json, os, sys, urllib.request

api_base = os.environ.get("TENSOR_API_BASE", "http://127.0.0.1:8010/api/v1")
user = os.environ.get("TENSOR_AGENT_USER", "eco_admin")
passwd = os.environ.get("TENSOR_AGENT_PASS", "")

# 1. Login
login_data = json.dumps({"username": user, "password": passwd}).encode()
req = urllib.request.Request(f"{api_base}/auth/login", data=login_data,
                             headers={"Content-Type": "application/json"})
try:
    resp = urllib.request.urlopen(req, timeout=10)
    token = json.loads(resp.read())["access_token"]
except Exception as e:
    print(f"Login failed: {e}", file=sys.stderr)
    sys.exit(1)

# 2. Read report
report_path = os.path.join(os.path.dirname(__file__), "artifacts", "cross_validation", "report.json")
with open(report_path) as f:
    report = json.load(f)

# 3. Post report
body = json.dumps(report).encode()
req2 = urllib.request.Request(f"{api_base}/tensor/reports", data=body,
                              headers={"Content-Type": "application/json",
                                       "Authorization": f"Bearer {token}"})
try:
    resp2 = urllib.request.urlopen(req2, timeout=15)
    result = json.loads(resp2.read())
    print(f"Report posted: {result}")
except Exception as e:
    print(f"Post failed: {e}", file=sys.stderr)
    if hasattr(e, 'read'):
        print(e.read().decode(), file=sys.stderr)
    sys.exit(1)
