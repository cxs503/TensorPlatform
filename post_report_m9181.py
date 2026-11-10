#!/usr/bin/env python3
"""Post the coupling orchestrator report to the tensor platform."""
import json, os, sys, urllib.request, base64

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
    print(f"Login successful, token: {token[:20]}...")
except Exception as e:
    print(f"Login failed: {e}", file=sys.stderr)
    sys.exit(1)

# 2. Read report markdown
with open("/root/tp-agent/work/m9181/docs/coupling/README.md") as f:
    md = f.read()

# 3. Read figures
figs = []
for name in ['aitken_convergence', 'interpolation_accuracy', 'dimension_check']:
    try:
        with open(f'/root/tp-agent/work/m9181/artifacts/m9181/{name}.png', 'rb') as f:
            b64 = base64.b64encode(f.read()).decode()
        figs.append({
            'name': name,
            'caption': name.replace('_', ' ').title(),
            'png_base64': b64
        })
    except Exception as e:
        print(f"Warning: Could not read figure {name}: {e}")

# 4. Post report
report = {
    "title": "TensorPlatform Multi-Solver Coupling Orchestrator: LBM-FEM-DEM Data Exchange with Conservation, Time Coordination & Contract Validation",
    "summary": "Built and verified a domain-agnostic multi-solver coupling framework with 5 modules (field exchange, conservation checking, time stepping coordination, contract validation, Aitken relaxation). All 7 acceptance criteria verified by 31 pytest tests. Zero domain semantics enforced.",
    "scenario_key": "p246w2-n07",
    "mission_id": 9181,
    "tags": ["coupling", "multi-solver", "conservation", "aitken", "time-stepping", "contract-validation", "TensorPlatform", "LBM", "FEM", "DEM"],
    "markdown": md,
    "figures": figs
}

body = json.dumps(report).encode()
req2 = urllib.request.Request(f"{api_base}/tensor/reports", data=body,
                              headers={"Content-Type": "application/json",
                                       "Authorization": f"Bearer {token}"})
try:
    resp2 = urllib.request.urlopen(req2, timeout=30)
    result = json.loads(resp2.read())
    print(f"Report posted successfully: {result}")
except Exception as e:
    print(f"Post failed: {e}", file=sys.stderr)
    if hasattr(e, 'read'):
        print(e.read().decode(), file=sys.stderr)
    sys.exit(1)
