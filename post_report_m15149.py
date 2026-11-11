#!/usr/bin/env python3
"""Post m15149 audit report to tensor platform."""
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
with open("/root/tp-agent/work/m15149/docs/m15149_audit_report.md") as f:
    md = f.read()

# 3. Post report
report = {
    "title": "m15120（KPI-TRACE-100）交差审计核验：L2 判定，三算路一致，无虚标",
    "summary": "审计 m15120（KPI-TRACE-100 计量单）：run_artifacts 全部真实存在；repro_cmd 可执行（复现 result.json）；三算路（Python/SQL/分片）逐位一致=14085/7677；报告六段齐全；tensor_benchmark_cases 对 KPI-TRACE-100 无基准用例（0 行）→ 基准裁判不可用但内部一致性通过；判级 L2（独立功能实现）；verdict 已落表（id=232）。",
    "scenario_key": "board-audit",
    "mission_id": 15149,
    "tags": ["board-audit", "kpi-trace-100", "m15120", "maturity-verdict", "L2"],
    "markdown": md
}

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
