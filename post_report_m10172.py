#!/usr/bin/env python3
"""Post the Bayesian updater report to the tensor platform."""
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
with open("/tmp/report.json") as f:
    report = json.load(f)

# 3. Post report
report_data = json.dumps(report).encode()
req = urllib.request.Request(f"{api_base}/tensor/reports", data=report_data,
                             headers={"Content-Type": "application/json",
                                      "Authorization": f"Bearer {token}"})
try:
    resp = urllib.request.urlopen(req, timeout=30)
    result = json.loads(resp.read())
    print(f"Report posted: {json.dumps(result, indent=2, ensure_ascii=False)[:500]}")
except Exception as e:
    print(f"Report failed: {e}", file=sys.stderr)
    sys.exit(1)
